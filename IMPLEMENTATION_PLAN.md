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
├── pyproject.toml                  # Python 3.11+, Poetry
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
│   │   └── fixtures/
│   │       └── blpapi_mock.py      # Scoped mock of the `blpapi` module for execution_scorer
│   │
│   └── reporting/
│       ├── langsmith_reporter.py   # evaluate() wrapper, tags experiments
│       └── metrics.py              # Tracked metrics (latency, tokens, turns) — always-on, not a grader
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
| `ExecutionScorer` | `generated_code` + `sandbox/fixtures/blpapi_mock.py` | pass/fail + stdout/stderr tail | Runs the script against a scoped BLPAPI mock in a subprocess; catches behavioral bugs (e.g. an event loop that never breaks) that regex can't. Mock coverage is limited to the request/response shapes the current task suite exercises — calls outside that raise a distinctly `"blpapi_mock:"`-prefixed error so a mock gap is distinguishable from a real defect |

Toggled per-experiment via `scorers: [...]` in the experiment config — a task suite for a different
API would swap in its own mock/executor rather than reusing `blpapi_mock.py`.

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

# Print a summary table from the last LangSmith experiment
agent-doc-bench report --experiment doc_ablation

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

---

## Verification

```bash
cd agent-doc-bench
poetry install
cp .env.example .env  # add ANTHROPIC_API_KEY + LANGSMITH_API_KEY

# Smoke test: single task, no docs, mock mode
agent-doc-bench run experiments/doc_ablation.yaml

# Expected: LangSmith experiment appears with 3 rows (none/v1/v2)
# Each row shows syntax_score, pattern_score, llm_judge_score,
# static_analysis_score, execution_score, plus tracked metrics
# (metric_n_turns, metric_n_total_tokens, metric_time_to_first_token, ...)
# v1/v2 rows should outscore none on pattern + llm_judge (once v2.md has
# real content — see step 12 above)
```
