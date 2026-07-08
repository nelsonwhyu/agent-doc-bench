from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_doc_bench.agent.claude_agent import ClaudeAgent
from agent_doc_bench.config import ExperimentConfig
from agent_doc_bench.reporting.langsmith_reporter import LangSmithReporter
from agent_doc_bench.scorers import llm_judge, pattern_scorer, syntax_scorer
from agent_doc_bench.tasks.task_registry import load_suite


def _load_doc(api: str, version: str, docs_base: Path) -> str:
    path = docs_base / api / f"{version}.md"
    if not path.exists():
        return ""
    return path.read_text()


def run_experiment(config: ExperimentConfig, docs_base: Path = Path("docs_library")) -> None:
    tasks = load_suite(config.task_suite)
    reporter = LangSmithReporter(project=config.langsmith_project)

    dataset_name = f"{config.name}_tasks"
    examples = [
        {
            "inputs": {
                "task_id": t.id,
                "instruction": t.instruction,
                "language": t.language,
            },
            "outputs": {
                "expected_patterns": [p.label for p in t.expected_patterns],
            },
        }
        for t in tasks
    ]
    reporter.upsert_dataset(dataset_name, examples)

    task_by_id = {t.id: t for t in tasks}

    for value in config.variable.values:
        variable_fixed: dict[str, Any] = {**config.fixed, config.variable.name: value}

        model = variable_fixed.get("model", "claude-sonnet-4-6")
        doc_version = variable_fixed.get("documentation", "none")
        tool_list: list[str] = variable_fixed.get("tools", []) or []

        if isinstance(tool_list, str):
            tool_list = [] if tool_list == "none" else [tool_list]

        agent = ClaudeAgent(model=model)
        doc_context = _load_doc(config.task_suite, doc_version, docs_base)

        def make_target(ag=agent, ctx=doc_context, tools=tool_list):
            def target_fn(inputs: dict) -> dict:
                task = task_by_id[inputs["task_id"]]
                trace = ag.run_task(task, ctx, tools)
                return {
                    "generated_code": trace.generated_code,
                    "steps": trace.steps,
                    "token_usage": trace.token_usage,
                    "error": trace.error,
                }
            return target_fn

        def make_evaluators(task_map=task_by_id, enabled=config.scorers):
            evals = []

            if "syntax" in enabled:
                def eval_syntax(run, example):
                    from agent_doc_bench.agent.base_agent import CodingTrace
                    trace = CodingTrace(
                        generated_code=run.outputs.get("generated_code", ""),
                        language=example.inputs.get("language", "python"),
                        steps=run.outputs.get("steps", 0),
                        token_usage=run.outputs.get("token_usage", {}),
                        error=run.outputs.get("error"),
                    )
                    result = syntax_scorer.score(trace)
                    return {"key": "syntax_score", "score": result.score}
                evals.append(eval_syntax)

            if "pattern" in enabled:
                def eval_pattern(run, example):
                    from agent_doc_bench.agent.base_agent import CodingTrace
                    task = task_map[example.inputs["task_id"]]
                    trace = CodingTrace(
                        generated_code=run.outputs.get("generated_code", ""),
                        language=example.inputs.get("language", "python"),
                        steps=run.outputs.get("steps", 0),
                        token_usage=run.outputs.get("token_usage", {}),
                        error=run.outputs.get("error"),
                    )
                    result = pattern_scorer.score(trace, task)
                    return {"key": "pattern_score", "score": result.score}
                evals.append(eval_pattern)

            if "llm_judge" in enabled:
                def eval_llm(run, example):
                    from agent_doc_bench.agent.base_agent import CodingTrace
                    task = task_map[example.inputs["task_id"]]
                    trace = CodingTrace(
                        generated_code=run.outputs.get("generated_code", ""),
                        language=example.inputs.get("language", "python"),
                        steps=run.outputs.get("steps", 0),
                        token_usage=run.outputs.get("token_usage", {}),
                        error=run.outputs.get("error"),
                    )
                    result = llm_judge.score(trace, task)
                    return {"key": "llm_judge_score", "score": result.score}
                evals.append(eval_llm)

            return evals

        run_id = reporter.run_experiment(
            experiment_name=f"{config.name}__{config.variable.name}_{value}",
            dataset_name=dataset_name,
            target_fn=make_target(),
            evaluators=make_evaluators(),
            metadata={config.variable.name: value, **variable_fixed},
        )
        print(f"  [{config.variable.name}={value}] experiment: {run_id}")
