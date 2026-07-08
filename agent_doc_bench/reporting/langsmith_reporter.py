from __future__ import annotations

from typing import Any, Callable

from langsmith import Client


class LangSmithReporter:
    def __init__(self, project: str = "agent-doc-bench"):
        self.project = project
        self._client = Client()

    def run_experiment(
        self,
        experiment_name: str,
        dataset_name: str,
        target_fn: Callable[[dict], dict],
        evaluators: list[Callable],
        metadata: dict[str, Any] | None = None,
    ) -> str:
        results = self._client.evaluate(
            target_fn,
            data=dataset_name,
            evaluators=evaluators,
            experiment_prefix=experiment_name,
            metadata=metadata or {},
            max_concurrency=2,
        )
        return results.experiment_name

    def upsert_dataset(self, name: str, examples: list[dict]) -> None:
        datasets = list(self._client.list_datasets(dataset_name=name))
        if not datasets:
            dataset = self._client.create_dataset(name)
        else:
            dataset = datasets[0]

        self._client.create_examples(
            inputs=[e["inputs"] for e in examples],
            outputs=[e.get("outputs") for e in examples],
            dataset_id=dataset.id,
        )
