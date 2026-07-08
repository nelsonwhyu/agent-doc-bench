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
│   ├── cli.py                      # Typer CLI: run / record / report
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
│   │   └── executor.py             # Optional: run generated code in subprocess (for testable APIs)
│   │
│   └── reporting/
│       └── langsmith_reporter.py   # client.evaluate() wrapper, tags experiments
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
scorers: [syntax, pattern, llm_judge]
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
```

---

## Scorer Pipeline

| Scorer | Input | Output | Notes |
|---|---|---|---|
| `SyntaxScorer` | `generated_code` | pass/fail + error msg | `ast.parse()` for Python; language-specific |
| `PatternScorer` | `generated_code` + task patterns | score 0–1 per pattern group | Counts matched expected / anti-pattern hits |
| `LLMJudgeScorer` | `generated_code` + task rubric | structured grades (Pydantic) | Uses `claude-haiku-4-5` as judge for speed |
| `ExecutionScorer` | `generated_code` + test harness | pass/fail + stdout | Optional; only for APIs with sandboxable mocks |

---

## Agent Layer

`ClaudeAgent.run_task(task: CodingTask, doc_context: str, tools: list) → CodingTrace`

- Injects `doc_context` into system prompt inside a `<documentation>` block
- Runs an Anthropic SDK tool_use loop until the agent emits a code block and stops
- If `tools` includes `web_search`, wires in a web search tool via MCP or function call
- `doc_context` is loaded from `docs_library/{api}/{doc_version}.md`; empty string for `none`

---

## LangSmith Integration

1. Create/update a LangSmith dataset with all task inputs + expected criteria
2. `target_fn(inputs)`: calls `agent.run_task()` with the experiment's fixed + variable config
3. `evaluator_fn(outputs, reference_outputs)`: runs all scorers, returns `{scorer_name: score}`
4. `client.evaluate(target_fn, data=dataset, evaluators=[evaluator_fn], experiment_prefix=run_id)`
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
2. `config.py` — `ExperimentConfig` dataclass + YAML loader
3. `base_task.py` + `task_registry.py` — load tasks from YAML
4. `base_agent.py` + `claude_agent.py` — Anthropic SDK coding agent
5. `syntax_scorer.py` — `ast.parse()` for Python; pluggable for other languages
6. `pattern_scorer.py` — regex + anti-pattern checking
7. `llm_judge.py` — LLM-as-judge with Pydantic structured grades
8. `langsmith_reporter.py` — `client.evaluate()` wrapper
9. `runner.py` — ties config → agent → scorers → reporter
10. `cli.py` — Typer `run` and `report` commands
11. Seed `task_suites/blpapi/auth_tasks.yaml` with 3–5 tasks
12. Seed `docs_library/blpapi/none.md` and `docs_library/blpapi/v1.md`
13. Seed `experiments/doc_ablation.yaml` and `experiments/llm_ablation.yaml`

---

## Verification

```bash
cd agent-doc-bench
poetry install
cp .env.example .env  # add ANTHROPIC_API_KEY + LANGSMITH_API_KEY

# Smoke test: single task, no docs, mock mode
agent-doc-bench run experiments/doc_ablation.yaml

# Expected: LangSmith experiment appears with 3 rows (none/v1/v2)
# Each row shows syntax_score, pattern_score, llm_judge scores
# v1/v2 rows should outscore none on pattern + llm_judge
```
