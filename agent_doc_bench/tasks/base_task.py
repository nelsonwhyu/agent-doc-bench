from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PatternSpec:
    regex: str
    label: str


@dataclass
class CodingTask:
    id: str
    instruction: str
    language: str                           # e.g. "python"
    expected_patterns: list[PatternSpec] = field(default_factory=list)
    anti_patterns: list[PatternSpec] = field(default_factory=list)
    llm_judge_rubric: str = ""
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "CodingTask":
        return cls(
            id=data["id"],
            instruction=data["instruction"],
            language=data.get("language", "python"),
            expected_patterns=[PatternSpec(**p) for p in data.get("expected_patterns", [])],
            anti_patterns=[PatternSpec(**p) for p in data.get("anti_patterns", [])],
            llm_judge_rubric=data.get("llm_judge_rubric", ""),
            tags=data.get("tags", []),
        )
