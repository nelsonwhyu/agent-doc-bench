from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class VariableSpec:
    name: str
    values: list[str]


@dataclass
class ExperimentConfig:
    name: str
    task_suite: str                 # e.g. "blpapi" → loads task_suites/blpapi/*.yaml
    variable: VariableSpec          # the single factor being varied
    fixed: dict[str, Any]           # all other factors, held constant
    scorers: list[str]              # e.g. ["syntax", "pattern", "llm_judge"]
    langsmith_project: str = "agent-doc-bench"

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ExperimentConfig":
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls(
            name=data["name"],
            task_suite=data["task_suite"],
            variable=VariableSpec(**data["variable"]),
            fixed=data.get("fixed", {}),
            scorers=data.get("scorers", ["syntax", "pattern", "llm_judge"]),
            langsmith_project=data.get("langsmith_project", "agent-doc-bench"),
        )
