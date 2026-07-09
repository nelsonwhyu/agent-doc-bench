# Plan: agent-doc-bench

## Context

API product teams write documentation to guide developers on how to use their APIs. The question is: **does that documentation actually help AI coding agents produce correct code?** `agent-doc-bench` is a generic evaluation framework that tests this by having a coding agent complete programming tasks — with different documentation, models, or tooling — and scoring whether the generated code:

- Picks the right product / API surface
- Uses the correct authentication method
- Follows recommended design patterns
- Has valid syntax for the target language

The framework is designed for ablation studies: hold all factors constant, vary exactly one (model, doc version, tools available), and measure the impact. Initial use case is Bloomberg BLPAPI documentation, but the framework is API-agnostic.

**Confirmed choices:** LangSmith for experiment tracking, project name `agent-doc-bench`.

---

## Project Structure

```
agent-doc-bench/
├── pyproject.toml                  # Python 3.11+, uv (PEP 621 + hatchling)
├── .env.example                    # ANTHROPIC_API_KEY, LANGSMITH_API_KEY
│
├── agent_doc_bench/
│   ├── cli.py                      # Typer CLI: run / report
│   ├── config.py                   # ExperimentConfig dataclass + YAML loader
│   ├── runner.py                   # Orchestrates: config → agent → scorers → reporter
│   │
│   ├── agent/
│   │   ├── base_agent.py           # Abstract: run_task(task, docs) → CodingTrace
│   │   └── claude_agent.py         # Claude implementation (Anthropic SDK, streaming tool_use loop)
│   │
│   ├── tasks/
│   │   ├── base_task.py            # CodingTask dataclass
│   │   └── task_registry.py        # Load tasks from task_suites/ YAML files
│   │
│   ├── scorers/
│   │   ├── base.py                 # EvaluatorResult + run_scorer() — shared {key, score, comment} shape
│   │   ├── syntax_scorer.py        # ast.parse() — does the code parse?
│   │   ├── pattern_scorer.py       # Regex/AST checks for expected + anti-patterns
│   │   ├── llm_judge.py            # LLM-as-judge: product, auth, design pattern quality
│   │   ├── static_analysis_scorer.py  # ruff (pyflakes) + bandit (security) on generated code
│   │   └── execution_scorer.py     # Runs generated code against the blpapi mock in a subprocess
│   │
│   ├── sandbox/
│   │   ├── executor.py             # subprocess runner: executes Python, captures stdout/stderr/exit code
│   │   ├── live_runner.py          # Live-mode entrypoint: installs instrumentation, runs generated.py
│   │   └── fixtures/
│   │       ├── blpapi_mock.py      # Scoped mock of the `blpapi` module for execution_scorer (BLOOMBERG_MODE=mock)
│   │       └── blpapi_live_shim.py # Wraps real blpapi.Session to log event/timing metadata only (BLOOMBERG_MODE=live)
│   │
│   └── reporting/
│       ├── langsmith_reporter.py   # evaluate() wrapper, tags experiments
│       ├── metrics.py              # Tracked metrics (latency, tokens, turns) — always-on, not a grader
│       ├── results_fetcher.py      # Reads scores/comments/code back from LangSmith into plain dataclasses
│       └── report_formatters.py    # Renders results_fetcher output as a rich table, JSON, or Markdown
│
├── docs_library/                   # Documentation variants (Markdown)
│   └── blpapi/
│       ├── none.md                 # Empty — no-doc baseline
│       ├── v1.md                   # First version of BLPAPI agent guidance
│       └── v2.md                   # Iteration B for A/B comparison
│
├── task_suites/                    # Task definitions (YAML)
│   └── blpapi/
│       ├── auth_tasks.yaml         # How to authenticate / open a session
│       ├── data_tasks.yaml         # Fetch prices, history, bulk data
│       └── pattern_tasks.yaml      # Design patterns (sync vs async, error handling)
│
└── experiments/                    # Ablation configs (YAML)
    ├── doc_ablation.yaml           # Vary: docs | Fixed: model=sonnet, tools=none
    ├── llm_ablation.yaml           # Vary: model | Fixed: docs=v1, tools=none
    └── tools_ablation.yaml         # Vary: tools | Fixed: model=sonnet, docs=v1
```

---

## Core Concepts

### CodingTask
```yaml
# task_suites/blpapi/auth_tasks.yaml
- id: blpapi_open_session
  instruction: "Write Python code to connect to a Bloomberg Terminal using BLPAPI and open a session."
  language: python
  expected_patterns:
    - regex: "blpapi\\.Session\\("
      label: "uses Session class"
    - regex: "session\\.start\\("
      label: "calls session.start()"
  anti_patterns:
    - regex: "(username|password|api[_-]?key)"
      label: "wrong auth method"
  llm_judge_rubric: |
    Score each dimension 1–5:
    - product_selection: Used BLPAPI (not Bloomberg REST API or Data License)?
    - auth_method: Used local terminal auth (not credentials)?
    - design_pattern: Followed synchronous session pattern correctly?
    - syntax_quality: Is the code idiomatic, complete, and correct Python?
```

### ExperimentConfig
```yaml
# experiments/doc_ablation.yaml
name: doc_ablation
task_suite: blpapi
variable:
  name: documentation
  values: [none, v1, v2]          # maps to docs_library/blpapi/{value}.md
fixed:
  model: claude-sonnet-4-6
  tools: none
scorers: [syntax, pattern, llm_judge, static_analysis, execution]
langsmith_project: agent-doc-bench
```

Only one `variable` key per experiment — enforces single-variable isolation.

### CodingTrace (output of agent)
```python
@dataclass
class CodingTrace:
    generated_code: str
    language: str
    steps: int                  # turns used
    token_usage: dict
    tool_calls: list[ToolCall]  # if agent used tools (web search, etc.)
    error: str | None
    latency: dict                # time_to_first_token, time_to_last_token, output_tokens_per_sec
```

---

## Scorer Pipeline

Every scorer returns an object exposing `.score` and `.comment`; `runner.py` wraps each call in
`scorers/base.py`'s `run_scorer()` so one scorer raising can't abort the whole evaluation run, and
reports uniformly as LangSmith feedback `{key, score, comment}`.

| Scorer | Input | Output | Notes |
|---|---|---|---|
| `SyntaxScorer` | `generated_code` | pass/fail + error msg | `ast.parse()` for Python; language-specific |
| `PatternScorer` | `generated_code` + task patterns | score 0–1 per pattern group | Counts matched expected / anti-pattern hits |
| `LLMJudgeScorer` | `generated_code` + task rubric | structured grades (Pydantic) | Uses `claude-haiku-4-5` as judge for speed |
| `StaticAnalysisScorer` | `generated_code` | score 0–1 + issue list | `ruff` (pyflakes only) + `bandit` (security); no type-checker since BLPAPI has no stubs |
| `ExecutionScorer` | `generated_code` + `sandbox/fixtures/blpapi_mock.py` (or real `blpapi`, live mode) | pass/fail + comment | Runs the script in a subprocess. Catches behavioral bugs (e.g. an event loop that never breaks) that regex can't. See "Execution modes" below for mock vs. live |

Toggled per-experiment via `scorers: [...]` in the experiment config — a task suite for a different
API would swap in its own mock/executor rather than reusing `blpapi_mock.py`.

### Execution modes: mock vs. live

`ExecutionScorer` branches on the `BLOOMBERG_MODE` env var (`.env.example`):

- **`mock` (default).** `sandbox/fixtures/blpapi_mock.py` is written into the sandbox as `blpapi.py`,
  shadowing the real package, and the generated script runs against it directly. Coverage is limited
  to the request/response shapes the current task suite exercises — calls outside that raise a
  distinctly `"blpapi_mock:"`-prefixed error so a mock gap is distinguishable from a real defect. This
  is the CI/default path and needs no Bloomberg Terminal.
- **`live`.** Runs against a real Bloomberg Terminal via the real `blpapi` SDK (installed via the
  `live` extra, see Setup). Because the script's own stdout/stderr may contain real market data (e.g.
  a printed price), the scorer never reads them into `.comment` — nothing derived from a live run
  reaches LangSmith except the exit code and *structural* session metadata (event types, message
  counts, elapsed time). This is captured by `sandbox/fixtures/blpapi_live_shim.py`, which monkeypatches
  `blpapi.Session.start/stop/sendRequest/nextEvent` to log shape/timing only — never field values — to
  a JSON file the scorer reads. `sandbox/live_runner.py` is the actual subprocess entrypoint in live
  mode: it installs the shim, then runs `generated.py` via `runpy`, flushing metadata even on failure.
  Raw stdout/stderr are written only to a local, gitignored log file
  (`sandbox/.live_logs/<task_id>__<variable_name>-<variable_value>__<timestamp>.log`, with a header
  spelling out the task/variant/fixed config) referenced by *path only* in the comment, for local
  debugging — never their contents.

---

## Tracked Metrics

Separate from correctness scorers — these measure cost/speed, not correctness, and are always
reported regardless of which scorers are enabled (`reporting/metrics.py`, invoked unconditionally
in `runner.py`). Reported as LangSmith feedback so they're sortable/comparable across doc/model/tool
variants in the same way correctness scores are:

- `metric_n_turns`, `metric_n_toolcalls`, `metric_n_total_tokens` — transcript size
- `metric_time_to_first_token`, `metric_time_to_last_token`, `metric_output_tokens_per_sec` — latency, captured via the streaming Messages API

---

## Agent Layer

`ClaudeAgent.run_task(task: CodingTask, doc_context: str, tools: list) → CodingTrace`

- Injects `doc_context` into system prompt inside a `<documentation>` block
- Runs an Anthropic SDK **streaming** tool_use loop (`client.messages.stream(...)`) until the agent emits a code block and stops, timing the first streamed chunk to compute `time_to_first_token`
- If `tools` includes `web_search`, wires in a web search tool via MCP or function call
- `doc_context` is loaded from `docs_library/{api}/{doc_version}.md`; empty string for `none`

---

## LangSmith Integration

1. Create/update a LangSmith dataset with all task inputs + expected criteria
2. `target_fn(inputs)`: calls `agent.run_task()` with the experiment's fixed + variable config
3. One evaluator function per enabled scorer, plus one always-on metrics evaluator — each returns `{key, score, comment}` (or a list of these) per LangSmith's evaluator interface
4. `evaluate(target_fn, data=dataset, evaluators=[...], client=client, experiment_prefix=run_id)` — `evaluate` is a top-level `langsmith` function, not a `Client` method
5. Each run tagged with `variable=value` so LangSmith comparison view works automatically

---

## CLI Commands

```bash
# Run an ablation (records results in LangSmith)
agent-doc-bench run experiments/doc_ablation.yaml

# Print a scored summary + per-task detail table, pulled from LangSmith
agent-doc-bench report experiments/doc_ablation.yaml

# Machine-readable export (e.g. to paste into an LLM chat for interpretation)
agent-doc-bench report experiments/doc_ablation.yaml --format markdown
agent-doc-bench report experiments/doc_ablation.yaml --format json --output report.json

# (Future) Record live Bloomberg responses to fixtures for mock mode
agent-doc-bench record --task blpapi_open_session
```

---

## Implementation Steps

1. Scaffold `pyproject.toml` + package skeleton + `.env.example`  ✅
2. `config.py` — `ExperimentConfig` dataclass + YAML loader  ✅
3. `base_task.py` + `task_registry.py` — load tasks from YAML  ✅
4. `base_agent.py` + `claude_agent.py` — Anthropic SDK coding agent (streaming, with latency capture)  ✅
5. `syntax_scorer.py` — `ast.parse()` for Python; pluggable for other languages  ✅
6. `pattern_scorer.py` — regex + anti-pattern checking  ✅
7. `llm_judge.py` — LLM-as-judge with Pydantic structured grades  ✅
8. `langsmith_reporter.py` — `evaluate()` wrapper  ✅
9. `runner.py` — ties config → agent → scorers → reporter  ✅
10. `cli.py` — Typer `run` and `report` commands  ✅
11. Seed `task_suites/blpapi/auth_tasks.yaml`, `data_tasks.yaml`, `pattern_tasks.yaml` with 5 tasks total  ✅
12. Seed `docs_library/blpapi/none.md` and `docs_library/blpapi/v1.md`  ✅ (`v2.md` still a stub — needs real content before `doc_ablation` says anything about v1 vs v2)
13. Seed `experiments/doc_ablation.yaml`, `llm_ablation.yaml`, `tools_ablation.yaml`  ✅
14. `scorers/base.py` — shared `{key, score, comment}` evaluator result + `run_scorer()` failure isolation  ✅
15. `static_analysis_scorer.py` — ruff + bandit  ✅
16. `sandbox/fixtures/blpapi_mock.py` + `execution_scorer.py` — scoped BLPAPI mock and execution grader  ✅
17. `reporting/metrics.py` — tracked metrics (turns, tokens, latency), always-on  ✅
18. `tools_ablation.yaml` is seeded but untested end-to-end — no scorer currently grades `CodingTrace.tool_calls`, so a tools ablation run wouldn't yet tell you whether the agent actually used the tool it was given
19. Migrated `pyproject.toml` from Poetry to `uv` (PEP 621 + `hatchling`); `blpapi` sourced from
    Bloomberg's own package index via a uv dependency group (`[dependency-groups] live = ["blpapi"]`,
    marked default via `[tool.uv] default-groups`), since it isn't on PyPI — a plain `uv sync` installs
    it; opt out with `--no-default-groups` on a machine that can't reach Bloomberg's index  ✅
20. `sandbox/fixtures/blpapi_live_shim.py` + `sandbox/live_runner.py` — live-mode execution against a
    real Bloomberg Terminal, with metadata-only capture so no market data reaches LangSmith  ✅
21. `reporting/results_fetcher.py` + `reporting/report_formatters.py` — `report` now reads scores back
    from LangSmith (summary + per-task detail table, JSON/Markdown export) instead of only listing
    experiment names  ✅

---

## Verification

```bash
cd agent-doc-bench
uv sync                        # installs everything, including real blpapi (default group)
cp .env.example .env  # add ANTHROPIC_API_KEY + LANGSMITH_API_KEY

# Smoke test: single task, no docs, mock mode
uv run agent-doc-bench run experiments/doc_ablation.yaml

# Expected: LangSmith experiment appears with 3 rows (none/v1/v2)
# Each row shows syntax_score, pattern_score, llm_judge_score,
# static_analysis_score, execution_score, plus tracked metrics
# (metric_n_turns, metric_n_total_tokens, metric_time_to_first_token, ...)
# v1/v2 rows should outscore none on pattern + llm_judge (once v2.md has
# real content — see step 12 above)

# Verify locally without opening LangSmith's UI:
uv run agent-doc-bench report experiments/doc_ablation.yaml
```

---

## Next Generation: PM-Facing Doc-Authoring Claude Desktop Extension

**Status: design only, not yet implemented.** Recorded here so the approach is captured before building it.

### Context

Today only an engineer with Claude Code/shell access can realistically add or edit `docs_library/<api>/*.md` files, because:

1. **Nothing tells a non-engineer what a doc file needs to contain.** The real requirements live in `task_suites/<api>/*.yaml` (`expected_patterns`, `anti_patterns`, `llm_judge_rubric`) — technical YAML, not something a PM can read.
2. **There's no safe way for a PM to get content into the repo.** A PM would work from a separate machine, with no local checkout, collaborating via Claude Desktop (chat-only), not Claude Code.
3. **A silent footgun exists today**: `_load_doc()` in `agent_doc_bench/runner.py:33-37` returns `""` (not an error) when a doc filename doesn't exactly match an experiment's `variable.values` entry. A wrong filename would silently run a "no docs" condition with no warning.

The goal: let a PM author documentation variants conversationally in Claude Desktop, get plain-language guidance on what "good" means for each API's benchmark tasks, and safely propose changes — without touching git, YAML, or the engineering machine — while guaranteeing nothing lands on `main` without human review.

**Design decisions:**
- A full custom Claude Desktop Extension (locally-installed MCP server, `.mcpb` packaged) — not just docs/guardrails.
- Repo needs to be pushed to GitHub as a prerequisite (not yet done — this is a separate, explicit action to confirm before Layer 2/3 work starts).
- The extension talks to the GitHub API (not a local clone), so it works from a PM's separate machine.
- Writes always go through a PR — nothing ever lands on `main` directly.
- Includes a first-ever pytest suite for the new logic (none exists in the repo today).

### Layer 1 — Repo-side validator + CLI guardrail

New file `agent_doc_bench/docs_validator.py` — pure function, no CLI/MCP framework leakage, mirroring the shape of `agent_doc_bench/config.py`'s `ExperimentConfig.from_yaml()`:

```python
@dataclass
class DocIssue:
    experiment: str
    api: str        # task_suite, doubles as docs_library subfolder
    value: str       # the variable.values entry, e.g. "v1"
    kind: str        # "missing" | "empty_non_none" | "stub"
    path: Path
    detail: str

def validate_docs(experiments_dir=Path("experiments"), docs_base=Path("docs_library"),
                   stub_threshold_chars=200) -> list[DocIssue]:
    ...
```

For every `experiments/*.yaml` whose `variable.name == "documentation"`, for each `value` in `variable.values`, check `docs_base/<task_suite>/<value>.md`:
- **missing** — file doesn't exist (the exact `_load_doc()` footgun, turned into a hard failure).
- **empty_non_none** — file is empty/whitespace and `value != "none"` (empty `none.md` is required and correct — never flag it).
- **stub** — file is non-empty but trivially short / contains a `> **Stub.**` marker (heuristic; treat as a warning, not a hard failure — today's `docs_library/blpapi/v2.md` should trip this).

Never raises on one bad file — collects all issues, mirroring the failure-isolation pattern in `agent_doc_bench/scorers/base.py`'s `run_scorer()`.

CLI wiring in `agent_doc_bench/cli.py`: new `@app.command(name="validate-docs")`, following the existing thin-command style (`run`/`report`: parse args, defer heavy imports into the function body, print via the shared `rich.Console`). Non-strict: warns on `stub`, exits 1 only on `missing`/`empty_non_none`. `--strict` flag also fails on `stub`.

### Layer 2 — MCP server (GitHub-backed, PR-only writes)

New top-level directory `mcp_server/` (sibling to `agent_doc_bench/`, not nested in it — different dependency footprint (`mcp`, `PyGithub`) and deployment target (bundled into a Desktop Extension) than the core benchmark library). Depends on `agent_doc_bench` as a library (imports `docs_validator.validate_docs`, `doc_requirements.build_doc_requirements`, `tasks.task_registry.load_suite`) — never the reverse.

Add an optional Poetry dependency group so a plain `poetry install` for benchmark work doesn't pull these in:
```toml
[tool.poetry.group.mcp_server]
optional = true
[tool.poetry.group.mcp_server.dependencies]
mcp = "^1.0"
PyGithub = "^2.4"
```

New file `agent_doc_bench/doc_requirements.py` — `build_doc_requirements(api, base_dir=Path("task_suites")) -> str`. Generates a plain-language Markdown checklist **on the fly** from `task_registry.load_suite(api)` — no new YAML schema field. Rationale: `expected_patterns[].label` / `anti_patterns[].label` are already human-readable (e.g. `"instantiates blpapi.Session"`), and `llm_judge_rubric` is already prose — passing them through avoids a second source of truth that would drift from the real pattern/rubric definitions, and avoids touching `task_suites/*.yaml`'s existing 4-field convention (`AGENTS.md`: "Tasks are data, not code").

Tools exposed by `mcp_server/server.py` (using the official `mcp` Python SDK's `FastMCP`, stdio transport):

| Tool | Purpose |
|---|---|
| `list_apis()` | List `docs_library/` subfolders via GitHub Contents API |
| `list_experiments()` | List `experiments/*.yaml`, their swept variable + values |
| `get_doc_requirements(api)` | Plain-language checklist — fetches `task_suites/<api>/*.yaml` into a temp dir, calls `doc_requirements.build_doc_requirements` against it |
| `list_doc_variants(api)` | Existing `docs_library/<api>/*.md` filenames |
| `get_doc_variant(api, version)` | Raw content of an existing variant |
| `validate_doc_variant(api, version, content)` | Dry-run the validator against proposed content, no GitHub write — lets the PM self-correct before committing to a PR |
| `propose_doc_variant(api, version, content, summary)` | The one write path — see flow below |

`propose_doc_variant` flow (`mcp_server/github_client.py` wraps PyGithub for `fetch_file`/`list_dir`/branch/PR helpers):
1. Fetch current `docs_library/<api>/` + `experiments/*.yaml` via GitHub API; warn (don't block) if `version` isn't referenced by any experiment's `variable.values` — such a variant would never actually get used by `_load_doc()`.
2. Materialize a `tempfile.TemporaryDirectory()` shaped like `docs_base/<api>/`, overlay the proposed content.
3. Call `docs_validator.validate_docs(docs_base=<tmp>, strict=True)` — same function the CLI uses, no duplicated logic. Hard failures reject the call before anything touches GitHub.
4. Create branch `docs/<api>-<version>-<short suffix>` off the default branch HEAD.
5. Create/update `docs_library/<api>/<version>.md` on that branch.
6. Open a PR into the default branch (never merges) with a body including the PM's `summary` and which `get_doc_requirements` items were targeted. Returns the PR URL.

`MCP_DRY_RUN=1` env var: skips steps 4-6, returns a synthetic "would open PR titled X" response — the mechanism for testing the full tool logic without a real GitHub write.

PAT scope (documented in `mcp_server/README.md`): fine-grained PAT scoped to this one repo only — Contents: Read/write, Pull requests: Read/write. Nothing else.

### Layer 3 — Desktop Extension packaging

`mcp_server/manifest.json` (Desktop Extension / `.mcpb` spec): declares the 7 tools, and `user_config` for `repo_owner`, `repo_name`, and `github_pat` (marked `sensitive: true`, templated into `GITHUB_TOKEN`/`GITHUB_OWNER`/`GITHUB_REPO` env vars at process spawn). Verify exact manifest field names against the current published spec at build time.

Packaging: `@anthropic-ai/mcpb` CLI (`npx @anthropic-ai/mcpb pack mcp_server/ agent-doc-bench-docs.mcpb`), bundling a vendored `lib/` of Python deps (including `agent_doc_bench` itself) so the PM doesn't need Python/poetry installed locally — the PM persona is explicitly non-technical.

PM install steps (documented in `mcp_server/README.md`):
1. Receive the built `.mcpb` from an engineer.
2. Claude Desktop → Settings → Extensions → install from file.
3. Fill in `user_config`: repo owner/name, and a fine-grained PAT the PM creates themselves (one-time manual GitHub UI step — worth a short annotated doc since this is the one place the PM touches raw GitHub).
4. Chat naturally: "what does the blpapi docs need to cover?" → `get_doc_requirements`; "here's a draft, open a PR" → `propose_doc_variant`.

### Alternative: multi-client support (Claude Desktop + ChatGPT)

The 7-tool design and the GitHub-PR write flow are protocol-level (plain MCP) and portable — they aren't Claude-specific. The `.mcpb` **packaging** in Layer 3 is, though: it's a local stdio process Claude Desktop spawns and installs from a file, which ChatGPT has no equivalent for. OpenAI's MCP support in ChatGPT ("connectors," currently Team/Enterprise/Pro tiers) expects a **remote HTTP/SSE MCP server** reachable at a URL, typically with OAuth-style auth rather than the `sensitive: true` env-var PAT field the Desktop Extension manifest uses.

If PM usage ever needs to span both clients, the design should shift from "local process bundled as `.mcpb`" to "one hosted HTTP service, registered two ways":
- Same `mcp_server/server.py` tool logic, served over HTTP/SSE instead of stdio, deployed somewhere reachable (not spawned locally).
- Claude Desktop: still installable as a thin `.mcpb` extension, or added directly as a remote connector pointing at the hosted URL.
- ChatGPT: added as a custom connector pointing at the same hosted URL.
- Auth moves from a raw PAT typed into `user_config` to an OAuth flow in front of the GitHub PAT (or a scoped backend service account), since ChatGPT's connector model expects OAuth, not a pasted secret.

This is a materially bigger lift than the local-only design above (hosting, uptime, OAuth) — worth doing only once it's confirmed a PM will actually be on ChatGPT rather than speculatively.

### New/modified files (planned)

```
agent_doc_bench/
├── docs_validator.py          NEW
├── doc_requirements.py        NEW
└── cli.py                     MODIFIED — add `validate-docs` command

docs_library/
├── README.md                  NEW — format conventions, points at get_doc_requirements/validate-docs
└── TEMPLATE.md                NEW — skeleton mirroring v1.md's section shape

mcp_server/                    NEW top-level dir
├── server.py                  FastMCP tool definitions
├── github_client.py           PyGithub wrapper
├── manifest.json              Desktop Extension manifest
├── README.md                  PAT setup + install steps + dry-run testing
└── tests/
    └── test_server_dry_run.py

tests/
└── test_docs_validator.py     NEW — first test file in the repo

pyproject.toml                 MODIFIED — optional `mcp_server` group (mcp, PyGithub); pytest as dev dep
AGENTS.md                      MODIFIED — add "Doc authoring via Claude Desktop extension" pointer + validate-docs in Common commands
README.md                      MODIFIED — mention validate-docs, link mcp_server/README.md
```

### Implementation steps

1. Push repo to GitHub (prerequisite for Layers 2/3 — confirm timing separately)
2. `agent_doc_bench/docs_validator.py` — `validate_docs()` + `DocIssue`
3. `cli.py` — `validate-docs` command
4. `tests/test_docs_validator.py` — pytest added as dev dependency; covers `v2.md` stub flag, `none.md` exemption, filename-mismatch footgun
5. `agent_doc_bench/doc_requirements.py` — `build_doc_requirements()`, tested against real `task_suites/blpapi/*.yaml` labels
6. `mcp_server/` scaffold — `server.py`, `github_client.py`, optional Poetry dependency group
7. `propose_doc_variant` write flow (branch → validate → commit → PR), `MCP_DRY_RUN` support
8. `mcp_server/tests/test_server_dry_run.py` — all 7 tools exercised without real GitHub calls
9. `mcp_server/manifest.json` + packaging via `@anthropic-ai/mcpb`
10. `docs_library/README.md` + `TEMPLATE.md`; `AGENTS.md`/`README.md` pointers
11. One manual smoke test of the real write path against a throwaway/test repo once GitHub hosting exists

### Verification (once implemented)

1. `tests/test_docs_validator.py`: `validate_docs()` against the real `experiments/`/`docs_library/` flags `docs_library/blpapi/v2.md` as `kind="stub"`; `none.md` is not flagged despite being empty; a deliberately mismatched filename is flagged `kind="missing"`.
2. `doc_requirements.build_doc_requirements("blpapi")` test asserts output contains known pattern labels (e.g. `"instantiates blpapi.Session"`).
3. `mcp_server/tests/test_server_dry_run.py`: drive the server via the `mcp` SDK's client session over stdio (or the official MCP Inspector for manual poking), with `MCP_DRY_RUN=1` and `github_client` mocked/pointed at local disk for read tools.
4. `poetry run agent-doc-bench validate-docs` against the repo as-is prints the `v2.md` stub warning, exits 0; `--strict` exits 1.
5. One manual smoke test of the real write path (`MCP_DRY_RUN=0`) against a throwaway/test repo — confirm a real PR appears, nothing touches `main`, and the PAT can't do anything beyond Contents+PRs on that repo.

### Open risks

- **GitHub push is a prerequisite outside this design** — nothing in Layer 2/3 works until the repo has a remote.
- **PAT storage**: `sensitive: true` in the manifest keeps Claude Desktop from displaying/logging the token, but verify how Desktop actually stores `user_config` secrets before treating this as fully solved.
- **Stub-detection is a heuristic** (character count + marker match) — expect some false positives/negatives; keep it a warning, not a hard failure, outside `--strict`.
- **`llm_judge_rubric` passthrough** assumes existing rubric prose is PM-readable — confirmed for `auth_tasks.yaml`; worth a quick read-through of `data_tasks.yaml`/`pattern_tasks.yaml` rubrics before shipping to confirm they're equally plain-language.
