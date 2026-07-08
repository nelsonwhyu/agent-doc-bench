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
│   │   └── claude_agent.py         # Claude implementation (Anthropic SDK, tool_use loop)
│   │
│   ├── tasks/
│   │   ├── base_task.py            # CodingTask dataclass
│   │   └── task_registry.py        # Load tasks from task_suites/ YAML files
│   │
│   ├── scorers/
│   │   ├── syntax_scorer.py        # ast.parse() — does the code parse?
│   │   ├── pattern_scorer.py       # Regex/AST checks for expected + anti-patterns
│   │   └── llm_judge.py            # LLM-as-judge: product, auth, design pattern quality
│   │
│   ├── sandbox/
│   │   └── executor.py             # Optional: run generated code in subprocess
│   │
│   └── reporting/
│       └── langsmith_reporter.py   # client.evaluate() wrapper, tags experiments
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
- **Scorers are independent and composable** (`syntax_scorer`, `pattern_scorer`, `llm_judge`, optional `execution` via `sandbox/executor.py`). New scorers should follow the same input/output shape: take `generated_code` (+ task metadata), return a score/grade, and not depend on other scorers.
- **Tasks are data, not code.** New coding tasks belong in `task_suites/<api>/*.yaml`, not hardcoded in Python. Each task needs `expected_patterns`, `anti_patterns`, and an `llm_judge_rubric`.
- **Docs variants are plain Markdown** under `docs_library/<api>/`, including an empty `none.md` baseline for every API — it's the no-doc control group and ablations depend on it existing.
- Keep `agent/base_agent.py`'s `CodingTrace` output contract (`generated_code`, `language`, `steps`, `token_usage`, `tool_calls`, `error`) stable — scorers and the LangSmith reporter consume it directly.

## Adding a new API to benchmark

1. `docs_library/<api>/none.md` (empty) plus one or more versioned doc files.
2. `task_suites/<api>/*.yaml` with tasks covering auth, data access, and design patterns as relevant.
3. `experiments/<name>.yaml` pointing `task_suite: <api>` with one `variable` to sweep.
4. Run via `agent-doc-bench run experiments/<name>.yaml`.
