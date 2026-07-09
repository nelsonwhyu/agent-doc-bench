from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from langsmith import Client

from agent_doc_bench.config import ExperimentConfig
from agent_doc_bench.reporting import metrics

# No rich/typer imports here on purpose — this module is the data layer for
# both the CLI's report_formatters.py today and, later, a web dashboard, so
# it stays presentation-agnostic (plain dataclasses only).


@dataclass
class TaskResult:
    task_id: str
    scores: dict[str, float | bool | None] = field(default_factory=dict)
    comments: dict[str, str | None] = field(default_factory=dict)
    metrics: dict[str, float | None] = field(default_factory=dict)
    generated_code: str = ""
    error: str | None = None


@dataclass
class VariantResult:
    variable_name: str
    variable_value: str
    project_name: str
    start_time: datetime
    task_results: list[TaskResult] = field(default_factory=list)

    def mean_score(self, key: str) -> float | None:
        vals = [float(t.scores[key]) for t in self.task_results if t.scores.get(key) is not None]
        return sum(vals) / len(vals) if vals else None

    def mean_metric(self, key: str) -> float | None:
        vals = [float(t.metrics[key]) for t in self.task_results if t.metrics.get(key) is not None]
        return sum(vals) / len(vals) if vals else None


@dataclass
class ExperimentResult:
    config_name: str
    variable_name: str
    scorer_keys: list[str] = field(default_factory=list)
    metric_keys: list[str] = field(default_factory=list)
    variants: list[VariantResult] = field(default_factory=list)
    missing_values: list[str] = field(default_factory=list)


def _resolve_variant_project(client: Client, config: ExperimentConfig, dataset_name: str, value: str):
    prefix = f"{config.name}__{config.variable.name}_{value}"

    # Prefer an exact metadata match (LangSmith stores the run_context dict
    # runner.py passes as `metadata=` to evaluate() on session.metadata) —
    # name_contains alone is ambiguous when variable_value itself contains a
    # hyphen (e.g. a model name), which can collide with LangSmith's own
    # "-<random suffix>" appended to the experiment_prefix.
    candidates = list(client.list_projects(name_contains=prefix, reference_dataset_name=dataset_name))
    exact = [p for p in candidates if str(p.metadata.get("variable_value")) == str(value)]
    pool = exact or candidates
    if not pool:
        return None
    return max(pool, key=lambda p: p.start_time)


def fetch_experiment_results(config: ExperimentConfig, client: Client | None = None) -> ExperimentResult:
    from agent_doc_bench.runner import SCORER_REGISTRY

    client = client or Client()
    dataset_name = f"{config.name}_tasks"

    examples = list(client.list_examples(dataset_name=dataset_name))
    example_id_to_task_id = {str(ex.id): ex.inputs["task_id"] for ex in examples}

    scorer_keys = [SCORER_REGISTRY[name][0] for name in config.scorers if name in SCORER_REGISTRY]
    metric_keys = list(metrics.KEYS)

    result = ExperimentResult(
        config_name=config.name,
        variable_name=config.variable.name,
        scorer_keys=scorer_keys,
        metric_keys=metric_keys,
    )

    for value in config.variable.values:
        project = _resolve_variant_project(client, config, dataset_name, value)
        if project is None:
            result.missing_values.append(str(value))
            continue

        runs = list(client.list_runs(project_id=project.id, run_type="chain"))
        run_ids = [r.id for r in runs]

        feedback_by_run: dict[str, list] = defaultdict(list)
        if run_ids:
            for fb in client.list_feedback(run_ids=run_ids):
                feedback_by_run[str(fb.run_id)].append(fb)

        task_results = []
        for run in runs:
            task_id = example_id_to_task_id.get(str(run.reference_example_id), "?")
            scores: dict[str, float | bool | None] = {}
            comments: dict[str, str | None] = {}
            metric_values: dict[str, float | None] = {}

            for fb in feedback_by_run.get(str(run.id), []):
                if fb.key in metric_keys:
                    metric_values[fb.key] = fb.score
                else:
                    scores[fb.key] = fb.score
                    comments[fb.key] = fb.comment

            outputs = run.outputs or {}
            task_results.append(
                TaskResult(
                    task_id=task_id,
                    scores=scores,
                    comments=comments,
                    metrics=metric_values,
                    generated_code=outputs.get("generated_code", ""),
                    error=outputs.get("error"),
                )
            )

        result.variants.append(
            VariantResult(
                variable_name=config.variable.name,
                variable_value=str(value),
                project_name=project.name or "",
                start_time=project.start_time,
                task_results=task_results,
            )
        )

    return result
