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
│   │   ├── base.py                 # Shared {key, score, comment} result shape + failure isolation
│   │   ├── syntax_scorer.py        # ast.parse() — does the code parse?
│   │   ├── pattern_scorer.py       # Regex/AST checks for expected + anti-patterns
│   │   ├── llm_judge.py            # LLM-as-judge: product, auth, design pattern quality
│   │   ├── static_analysis_scorer.py  # ruff (pyflakes) + bandit (security) on generated code
│   │   └── execution_scorer.py     # Runs generated code against a scoped API mock, in a subprocess
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
poetry install
```

### 2. Configure credentials

```bash
cp .env.example .env
```

Fill in `.env` with:
- `ANTHROPIC_API_KEY` — used to run the coding agent (and the LLM judge)
- `LANGSMITH_API_KEY` — used to record and compare experiment results
- `LANGSMITH_PROJECT` — defaults to `agent-doc-bench`
- `BLOOMBERG_MODE` — `mock` (default) or `live`, if you're running against real Bloomberg fixtures

### 3. Run an ablation experiment

```bash
poetry run agent-doc-bench run experiments/doc_ablation.yaml
```

This runs every task in the experiment's task suite once per value of the swept variable, scores each result, and pushes the run to LangSmith.

### 4. View results

```bash
poetry run agent-doc-bench report --experiment doc_ablation
```

Or open the LangSmith project directly to compare runs side by side.

## Adding a new API to benchmark

1. Add documentation variants under `docs_library/<api>/*.md` (include an empty `none.md` baseline).
2. Add task definitions under `task_suites/<api>/*.yaml` — each task needs an `instruction`, `expected_patterns`, `anti_patterns`, and an `llm_judge_rubric`.
3. Add an experiment config under `experiments/*.yaml` pointing at the new `task_suite`, with exactly one `variable` to sweep.
4. Run it with `agent-doc-bench run experiments/<your_experiment>.yaml`.

Note: `execution_scorer.py` currently runs against `sandbox/fixtures/blpapi_mock.py`, which is scoped
specifically to BLPAPI's request/response shapes. For a new API, either write an equivalent mock (if
the API can't be executed directly, e.g. it requires a live connection) or drop `execution` from that
experiment's `scorers` list until one exists.
