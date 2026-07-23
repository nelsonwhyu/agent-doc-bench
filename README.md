# agent-doc-bench

**Does API documentation actually help AI coding agents write correct code?**

`agent-doc-bench` is a generic evaluation framework for running ablation studies on coding agents. It holds everything constant except one variable — documentation version, model, or available tools — and measures the effect on generated code quality: did the agent pick the right API surface, use the correct authentication method, follow recommended design patterns, and produce syntactically valid code?

The initial use case benchmarks Bloomberg BLPAPI documentation, but the framework itself is API-agnostic — plug in a new `docs_library/`, `task_suites/`, and experiment config to benchmark any API's docs.

## How it works

1. A **task suite** (YAML) defines coding tasks for an API, each with expected patterns, anti-patterns, and an LLM-judge rubric.
2. A **documentation variant** (Markdown) is injected into the agent's system prompt — or left empty as a no-doc baseline.
3. An **experiment config** (YAML) picks one variable to sweep (docs, model, or tools) and holds the rest fixed.
4. The **agent layer** drives an Anthropic SDK streaming tool-use loop to complete each task and emits a `CodingTrace` (generated code + token/latency metadata).
5. A **scorer pipeline** grades each trace: syntax validity, pattern matching, LLM-as-judge scoring, static analysis (ruff + bandit), and execution against a scoped API mock.
6. **Tracked metrics** (turns, tokens, latency) are captured alongside — separate from correctness scores — so you can compare doc variants on speed/cost, not just correctness.
7. Results are pushed to **LangSmith** for tracking and comparison across runs.

## Project structure

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
│   │   ├── base.py                 # Shared {key, score, comment} result shape + failure isolation
│   │   ├── syntax_scorer.py        # ast.parse() — does the code parse?
│   │   ├── pattern_scorer.py       # Regex/AST checks for expected + anti-patterns
│   │   ├── llm_judge.py            # LLM-as-judge: product, auth, design pattern quality
│   │   ├── static_analysis_scorer.py  # ruff (pyflakes) + bandit (security) on generated code
│   │   └── execution_scorer.py     # Runs generated code against a scoped API mock, in a subprocess
│   │
│   ├── sandbox/
│   │   ├── executor.py             # subprocess runner: executes Python, captures stdout/stderr/exit code
│   │   ├── live_runner.py          # Live-mode entrypoint: installs instrumentation, runs generated.py
│   │   └── fixtures/
│   │       ├── blpapi_mock.py      # Scoped mock of the `blpapi` module (BLOOMBERG_MODE=mock)
│   │       └── blpapi_live_shim.py # Metadata-only instrumentation for a real Terminal (BLOOMBERG_MODE=live)
│   │
│   └── reporting/
│       ├── langsmith_reporter.py   # evaluate() wrapper, tags experiments
│       ├── metrics.py              # Tracked metrics (latency, tokens, turns) — always-on, not a grader
│       ├── results_fetcher.py      # Reads scores/comments/code back from LangSmith into plain dataclasses
│       └── report_formatters.py    # Renders results_fetcher output as a rich table, JSON, or Markdown
│
├── docs_library/                   # Documentation variants (Markdown), one folder per API
│   └── blpapi/
│       ├── none.md                 # Empty — no-doc baseline
│       ├── v1.md                   # First version of BLPAPI agent guidance
│       └── v2.md                   # Iteration B for A/B comparison
│
├── task_suites/                    # Task definitions (YAML), one folder per API
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

See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the full design rationale, data model details (`CodingTask`, `ExperimentConfig`, `CodingTrace`), and the scorer pipeline reference.

## Getting started

### 1. Install dependencies

```bash
uv sync                          # installs everything, including real blpapi (default group)
uv sync --no-default-groups      # skip blpapi — e.g. on a machine without Bloomberg index access
```

`blpapi` isn't on PyPI — it's pulled from Bloomberg's own package index (configured in
`pyproject.toml`'s `[tool.uv.sources]`/`[[tool.uv.index]]`) and lives in the `live` uv dependency
group, marked default via `[tool.uv] default-groups = ["live"]` — so a plain `uv sync` always gives
you a working `BLOOMBERG_MODE=live` environment without remembering an extra flag. Opt out with
`--no-default-groups` on a machine that shouldn't (or can't) reach Bloomberg's index.

### 2. Configure credentials

```bash
cp .env.example .env
```

Fill in `.env` with:
- `ANTHROPIC_API_KEY` — used to run the coding agent (and the LLM judge)
- `LANGSMITH_API_KEY` — used to record and compare experiment results
- `LANGSMITH_PROJECT` — defaults to `agent-doc-bench`
- `BLOOMBERG_MODE` — `mock` (default, no Terminal needed) or `live`, to run generated code against a
  real Bloomberg Terminal. In live mode, the generated script's own stdout/stderr (which may contain
  real market data) never leaves the sandbox or reaches LangSmith — only pass/fail and structural
  session metadata (event types, message counts, timing) are reported; raw output is written to a
  local, gitignored log under `agent_doc_bench/sandbox/.live_logs/` for local debugging only.

### 3. Run an ablation experiment

```bash
uv run agent-doc-bench run experiments/doc_ablation.yaml
```

This runs every task in the experiment's task suite once per value of the swept variable, scores each result, and pushes the run to LangSmith.

### 4. View results

```bash
uv run agent-doc-bench report experiments/doc_ablation.yaml
```

Prints a scored summary table (mean per doc variant) plus a per-task detail table, pulled from
LangSmith's stored feedback. Add `--format json` or `--format markdown` (optionally with `--output
<path>`) for a machine-readable export — handy for pasting into a chat with another LLM to have it
interpret the results. Passing a bare experiment name instead of a YAML path falls back to a plain
list of matching LangSmith experiment names (the old behavior), with no score detail.

Or open the LangSmith project directly to compare runs side by side.

## Adding a new API to benchmark

1. Add documentation variants under `docs_library/<api>/*.md` (include an empty `none.md` baseline).
2. Add task definitions under `task_suites/<api>/*.yaml` — each task needs an `instruction`, `expected_patterns`, `anti_patterns`, and an `llm_judge_rubric`.
3. Add an experiment config under `experiments/*.yaml` pointing at the new `task_suite`, with exactly one `variable` to sweep.
4. Run it with `agent-doc-bench run experiments/<your_experiment>.yaml`.

Note: `execution_scorer.py` currently runs against `sandbox/fixtures/blpapi_mock.py`, which is scoped
specifically to BLPAPI's request/response shapes. For a new API, either write an equivalent mock (if
the API can't be executed directly, e.g. it requires a live connection) or drop `execution` from that
experiment's `scorers` list until one exists. If the new API does support a real, sandboxable live
connection, `blpapi_live_shim.py` is a template for metadata-only instrumentation — mirror its
pattern of monkeypatching only for event/timing metadata, never forwarding real response data into
the scorer's comment.

## PM-facing doc evaluation (MCP server)

A PM can read what a documentation draft needs to cover, sanity-check it, and get it scored — from
Claude or ChatGPT, no local checkout or git access needed — via the MCP server in `mcp_server/`,
hosted on Fly.io. See [mcp_server/README.md](mcp_server/README.md) for the connector setup steps and
[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)'s "Part 2" for the full design.
