# AGENTS.md

Guidance for AI coding agents working in this repository.

## What this project is

`agent-doc-bench` is an evaluation framework that measures whether API documentation actually helps AI coding agents write correct code. It runs ablation studies: hold everything constant (model, tools) except one variable (e.g. documentation version), and score how that variable affects generated code quality — correct API/product selection, correct auth method, correct design patterns, valid syntax.

The initial benchmark target is Bloomberg BLPAPI, but the framework is API-agnostic — see "Adding a new API" below.

Full design rationale, data model, and scorer pipeline reference: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).

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
│   │   ├── base.py                 # EvaluatorResult + run_scorer() — shared {key, score, comment} shape
│   │   ├── syntax_scorer.py        # ast.parse() — does the code parse?
│   │   ├── pattern_scorer.py       # Regex/AST checks for expected + anti-patterns
│   │   ├── llm_judge.py            # LLM-as-judge: product, auth, design pattern quality
│   │   ├── static_analysis_scorer.py  # ruff (pyflakes) + bandit (security)
│   │   └── execution_scorer.py     # Runs generated code against a scoped API mock in a subprocess
│   │
│   ├── sandbox/
│   │   ├── executor.py             # subprocess runner: executes Python, captures stdout/stderr/exit code
│   │   ├── live_runner.py          # Live-mode entrypoint (BLOOMBERG_MODE=live): installs instrumentation, runs generated.py
│   │   └── fixtures/
│   │       ├── blpapi_mock.py      # Scoped mock of the `blpapi` module — see caveats below
│   │       └── blpapi_live_shim.py # Wraps real blpapi.Session for metadata-only capture — see caveats below
│   │
│   └── reporting/
│       ├── langsmith_reporter.py   # evaluate() wrapper, tags experiments
│       ├── metrics.py              # Tracked metrics (latency, tokens, turns) — always-on, not a grader
│       ├── results_fetcher.py      # Reads scores/comments/code back from LangSmith into plain dataclasses
│       └── report_formatters.py    # Renders results_fetcher output as a rich table, JSON, or Markdown
│
├── docs_library/<api>/*.md         # Documentation variants injected into the agent's system prompt
├── task_suites/<api>/*.yaml        # Coding task definitions (instruction, patterns, rubric)
└── experiments/*.yaml              # Ablation configs — exactly one swept `variable`, rest `fixed`
```

## Setup

```bash
uv sync                        # installs everything, including real blpapi (default uv dependency group)
uv sync --no-default-groups    # skip blpapi — e.g. on a machine without Bloomberg index access
cp .env.example .env   # fill in ANTHROPIC_API_KEY, LANGSMITH_API_KEY
```

## Common commands

```bash
# Run an ablation experiment (pushes results to LangSmith)
uv run agent-doc-bench run experiments/doc_ablation.yaml

# Summarize a past experiment (scored table: summary + per-task detail)
uv run agent-doc-bench report experiments/doc_ablation.yaml

# Machine-readable export, e.g. for a PM to paste into an LLM chat for interpretation
uv run agent-doc-bench report experiments/doc_ablation.yaml --format markdown
uv run agent-doc-bench report experiments/doc_ablation.yaml --format json --output report.json

# Check that every experiment's documentation variants exist and aren't stub placeholders
uv run agent-doc-bench validate-docs
uv run agent-doc-bench validate-docs --strict   # also fail on stub-quality docs
```

```bash
uv run pytest tests/  # docs_validator and other non-LangSmith-dependent logic
```

For anything that touches the agent/LangSmith path, verify by running the smoke-test experiment above and confirming a LangSmith run appears with expected scorer output (see "Verification" in IMPLEMENTATION_PLAN.md).

## Conventions to follow

- **Single-variable isolation**: every `experiments/*.yaml` config must have exactly one `variable` key; everything else goes under `fixed`. Don't add configs that sweep more than one dimension at once — it breaks the ablation methodology this framework exists for.
- **Scorers are independent and composable** (`syntax_scorer`, `pattern_scorer`, `llm_judge`, `static_analysis_scorer`, `execution_scorer`), toggled per-experiment via `scorers: [...]`. New scorers should return an object exposing `.score` and `.comment` (see `scorers/base.py`), take `(trace, task)` or a subset, and not depend on other scorers — `runner.py` wraps every scorer call in `run_scorer()` so one raising an exception can't abort the whole eval run.
- **Tracked metrics are not scorers.** `reporting/metrics.py` reports turn count, token usage, and latency unconditionally, regardless of `config.scorers` — they measure cost/speed, not correctness, so don't gate them behind the scorers list.
- **The `execution_scorer` mock is scope-limited.** `sandbox/fixtures/blpapi_mock.py` only covers the request/response shapes the current `task_suites/blpapi/*.yaml` tasks exercise. If you add a task that needs a BLPAPI call the mock doesn't support, extend the mock — don't work around it in the scorer. A failure whose stderr contains `"blpapi_mock:"` means the mock is missing coverage, not that the generated code is wrong; keep that distinction when adding new mocked calls (raise `NotImplementedError` with the same prefix rather than silently guessing behavior).
- **Live-mode execution must never leak real data to LangSmith.** When `BLOOMBERG_MODE=live`, `execution_scorer.py` runs generated code against a real Bloomberg Terminal via `sandbox/live_runner.py` + `sandbox/fixtures/blpapi_live_shim.py`. The generated script's `stdout`/`stderr` may contain real market data and must never be read into `ExecutionResult.comment` (or any other LangSmith-visible field) — only exit code + the shim's structural metadata (event types, message counts, timing) may cross that boundary. Raw output goes only to the local, gitignored `sandbox/.live_logs/` directory. If you touch this path, preserve that boundary; don't add a "just print the stderr tail" fallback like the mock-mode scorer has.
- **Tasks are data, not code.** New coding tasks belong in `task_suites/<api>/*.yaml`, not hardcoded in Python. Each task needs `expected_patterns`, `anti_patterns`, and an `llm_judge_rubric`.
- **Docs variants are plain Markdown** under `docs_library/<api>/`, including an empty `none.md` baseline for every API — it's the no-doc control group and ablations depend on it existing. Note `docs_library/blpapi/v2.md` is still a stub placeholder — replace it with real content before treating `doc_ablation`'s v1-vs-v2 comparison as meaningful.
- Keep `agent/base_agent.py`'s `CodingTrace` output contract (`generated_code`, `language`, `steps`, `token_usage`, `tool_calls`, `error`, `latency`) stable — scorers, `metrics.py`, and the LangSmith reporter all consume it directly.

## Adding a new API to benchmark

1. `docs_library/<api>/none.md` (empty) plus one or more versioned doc files.
2. `task_suites/<api>/*.yaml` with tasks covering auth, data access, and design patterns as relevant.
3. `experiments/<name>.yaml` pointing `task_suite: <api>` with one `variable` to sweep.
4. Run via `agent-doc-bench run experiments/<name>.yaml`.
