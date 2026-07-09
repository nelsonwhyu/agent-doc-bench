# AGENTS.md

Guidance for AI coding agents working in this repository.

## What this project is

`agent-doc-bench` is an evaluation framework that measures whether API documentation actually helps AI coding agents write correct code. It runs ablation studies: hold everything constant (model, tools) except one variable (e.g. documentation version), and score how that variable affects generated code quality — correct API/product selection, correct auth method, correct design patterns, valid syntax.

The initial benchmark target is Bloomberg BLPAPI, but the framework is API-agnostic — see "Adding a new API" below.

Full design rationale, data model, and scorer pipeline reference: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).

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
│   │   ├── base.py                 # EvaluatorResult + run_scorer() — shared {key, score, comment} shape
│   │   ├── syntax_scorer.py        # ast.parse() — does the code parse?
│   │   ├── pattern_scorer.py       # Regex/AST checks for expected + anti-patterns
│   │   ├── llm_judge.py            # LLM-as-judge: product, auth, design pattern quality
│   │   ├── static_analysis_scorer.py  # ruff (pyflakes) + bandit (security)
│   │   └── execution_scorer.py     # Runs generated code against a scoped API mock in a subprocess
│   │
│   ├── sandbox/
│   │   ├── executor.py             # subprocess runner: executes Python, captures stdout/stderr/exit code
│   │   └── fixtures/
│   │       └── blpapi_mock.py      # Scoped mock of the `blpapi` module — see caveats below
│   │
│   └── reporting/
│       ├── langsmith_reporter.py   # evaluate() wrapper, tags experiments
│       └── metrics.py              # Tracked metrics (latency, tokens, turns) — always-on, not a grader
│
├── docs_library/<api>/*.md         # Documentation variants injected into the agent's system prompt
├── task_suites/<api>/*.yaml        # Coding task definitions (instruction, patterns, rubric)
└── experiments/*.yaml              # Ablation configs — exactly one swept `variable`, rest `fixed`
```

## Setup

```bash
poetry install
cp .env.example .env   # fill in ANTHROPIC_API_KEY, LANGSMITH_API_KEY
```

## Common commands

```bash
# Run an ablation experiment (pushes results to LangSmith)
poetry run agent-doc-bench run experiments/doc_ablation.yaml

# Summarize a past experiment
poetry run agent-doc-bench report --experiment doc_ablation
```

There is currently no automated test suite — verify changes by running the smoke-test experiment above and confirming a LangSmith run appears with expected scorer output (see "Verification" in IMPLEMENTATION_PLAN.md).

## Conventions to follow

- **Single-variable isolation**: every `experiments/*.yaml` config must have exactly one `variable` key; everything else goes under `fixed`. Don't add configs that sweep more than one dimension at once — it breaks the ablation methodology this framework exists for.
- **Scorers are independent and composable** (`syntax_scorer`, `pattern_scorer`, `llm_judge`, `static_analysis_scorer`, `execution_scorer`), toggled per-experiment via `scorers: [...]`. New scorers should return an object exposing `.score` and `.comment` (see `scorers/base.py`), take `(trace, task)` or a subset, and not depend on other scorers — `runner.py` wraps every scorer call in `run_scorer()` so one raising an exception can't abort the whole eval run.
- **Tracked metrics are not scorers.** `reporting/metrics.py` reports turn count, token usage, and latency unconditionally, regardless of `config.scorers` — they measure cost/speed, not correctness, so don't gate them behind the scorers list.
- **The `execution_scorer` mock is scope-limited.** `sandbox/fixtures/blpapi_mock.py` only covers the request/response shapes the current `task_suites/blpapi/*.yaml` tasks exercise. If you add a task that needs a BLPAPI call the mock doesn't support, extend the mock — don't work around it in the scorer. A failure whose stderr contains `"blpapi_mock:"` means the mock is missing coverage, not that the generated code is wrong; keep that distinction when adding new mocked calls (raise `NotImplementedError` with the same prefix rather than silently guessing behavior).
- **Tasks are data, not code.** New coding tasks belong in `task_suites/<api>/*.yaml`, not hardcoded in Python. Each task needs `expected_patterns`, `anti_patterns`, and an `llm_judge_rubric`.
- **Docs variants are plain Markdown** under `docs_library/<api>/`, including an empty `none.md` baseline for every API — it's the no-doc control group and ablations depend on it existing. Note `docs_library/blpapi/v2.md` is still a stub placeholder — replace it with real content before treating `doc_ablation`'s v1-vs-v2 comparison as meaningful.
- Keep `agent/base_agent.py`'s `CodingTrace` output contract (`generated_code`, `language`, `steps`, `token_usage`, `tool_calls`, `error`, `latency`) stable — scorers, `metrics.py`, and the LangSmith reporter all consume it directly.

## Adding a new API to benchmark

1. `docs_library/<api>/none.md` (empty) plus one or more versioned doc files.
2. `task_suites/<api>/*.yaml` with tasks covering auth, data access, and design patterns as relevant.
3. `experiments/<name>.yaml` pointing `task_suite: <api>` with one `variable` to sweep.
4. Run via `agent-doc-bench run experiments/<name>.yaml`.
