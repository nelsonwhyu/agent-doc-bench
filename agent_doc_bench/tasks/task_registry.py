from __future__ import annotations

from pathlib import Path

import yaml

from .base_task import CodingTask


def load_suite(suite_name: str, base_dir: str | Path = "task_suites") -> list[CodingTask]:
    suite_path = Path(base_dir) / suite_name
    tasks: list[CodingTask] = []
    for yaml_file in sorted(suite_path.glob("*.yaml")):
        with open(yaml_file) as f:
            items = yaml.safe_load(f) or []
        for item in items:
            tasks.append(CodingTask.from_dict(item))
    return tasks
