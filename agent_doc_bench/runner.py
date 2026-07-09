from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_doc_bench.agent.base_agent import CodingTrace
from agent_doc_bench.agent.claude_agent import ClaudeAgent
from agent_doc_bench.config import ExperimentConfig
from agent_doc_bench.reporting import metrics
from agent_doc_bench.reporting.langsmith_reporter import LangSmithReporter
from agent_doc_bench.scorers import execution_scorer, llm_judge, pattern_scorer, static_analysis_scorer, syntax_scorer
from agent_doc_bench.scorers.base import run_scorer
from agent_doc_bench.tasks.base_task import CodingTask
from agent_doc_bench.tasks.task_registry import load_suite

# Maps a scorer name (as used in ExperimentConfig.scorers) to the LangSmith
# feedback key it reports under and the scoring function itself. Every
# scorer function takes (trace, task) and returns an object exposing
# .score and .comment, so they can be run uniformly below.
#
# These are correctness graders, toggled per-experiment via config.scorers.
# Tracked metrics (latency, token/turn counts) are separate: they're always
# reported regardless of config.scorers, via _make_metrics_eval_fn below.
SCORER_REGISTRY: dict[str, tuple[str, Any]] = {
    "syntax": ("syntax_score", lambda trace, task: syntax_scorer.score(trace)),
    "pattern": ("pattern_score", lambda trace, task: pattern_scorer.score(trace, task)),
    "llm_judge": ("llm_judge_score", lambda trace, task: llm_judge.score(trace, task)),
    "static_analysis": ("static_analysis_score", lambda trace, task: static_analysis_scorer.score(trace)),
    "execution": ("execution_score", lambda trace, task: execution_scorer.score(trace, task)),
}


def _load_doc(api: str, version: str, docs_base: Path) -> str:
    path = docs_base / api / f"{version}.md"
    if not path.exists():
        return ""
    return path.read_text()


def _score_and_comment(scorer_fn: Any, trace: CodingTrace, task: CodingTask) -> tuple[float | bool, str | None]:
    result = scorer_fn(trace, task)
    return result.score, result.comment


def _make_eval_fn(key: str, scorer_fn: Any, task_map: dict[str, CodingTask]):
    # LangSmith's evaluate() inspects the evaluator's signature and rejects
    # any positional parameter other than run/example/inputs/outputs/
    # reference_outputs, so key/scorer_fn/task_map must be bound via a
    # factory closure rather than default arguments on eval_fn itself.
    def eval_fn(run, example) -> dict:
        task = task_map[example.inputs["task_id"]]
        trace = CodingTrace(
            generated_code=run.outputs.get("generated_code", ""),
            language=example.inputs.get("language", "python"),
            steps=run.outputs.get("steps", 0),
            token_usage=run.outputs.get("token_usage", {}),
            error=run.outputs.get("error"),
        )
        result = run_scorer(key, lambda: _score_and_comment(scorer_fn, trace, task))
        return {"key": result.key, "score": result.score, "comment": result.comment}

    return eval_fn


def _make_metrics_eval_fn():
    # Tracked metrics aren't correctness graders, so they're unconditional —
    # not gated by config.scorers — mirroring the tracked_metrics/graders
    # split in Anthropic's evals guide.
    def eval_fn(run, example) -> list[dict]:
        try:
            return metrics.collect(run.outputs)
        except Exception as e:
            return [{"key": "metric_collection_error", "score": None, "comment": str(e)}]

    return eval_fn


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
                    "n_toolcalls": len(trace.tool_calls),
                    "latency": trace.latency,
                    "error": trace.error,
                }
            return target_fn

        def make_evaluators(task_map=task_by_id, enabled=config.scorers):
            evals = []

            for name in enabled:
                if name not in SCORER_REGISTRY:
                    continue
                key, scorer_fn = SCORER_REGISTRY[name]
                evals.append(_make_eval_fn(key, scorer_fn, task_map))

            evals.append(_make_metrics_eval_fn())

            return evals

        run_id = reporter.run_experiment(
            experiment_name=f"{config.name}__{config.variable.name}_{value}",
            dataset_name=dataset_name,
            target_fn=make_target(),
            evaluators=make_evaluators(),
            metadata={config.variable.name: value, **variable_fixed},
        )
        print(f"  [{config.variable.name}={value}] experiment: {run_id}")
