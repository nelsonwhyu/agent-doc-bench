from __future__ import annotations

import re
from dataclasses import dataclass, field

from agent_doc_bench.agent.base_agent import CodingTrace
from agent_doc_bench.tasks.base_task import CodingTask, PatternSpec


@dataclass
class PatternResult:
    expected_hits: list[str] = field(default_factory=list)
    expected_misses: list[str] = field(default_factory=list)
    anti_pattern_hits: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        total_expected = len(self.expected_hits) + len(self.expected_misses)
        if total_expected == 0:
            pattern_score = 1.0
        else:
            pattern_score = len(self.expected_hits) / total_expected

        anti_penalty = 0.2 * len(self.anti_pattern_hits)
        return max(0.0, pattern_score - anti_penalty)


def score(trace: CodingTrace, task: CodingTask) -> PatternResult:
    code = trace.generated_code or ""
    result = PatternResult()

    for spec in task.expected_patterns:
        if re.search(spec.regex, code, re.IGNORECASE):
            result.expected_hits.append(spec.label)
        else:
            result.expected_misses.append(spec.label)

    for spec in task.anti_patterns:
        if re.search(spec.regex, code, re.IGNORECASE):
            result.anti_pattern_hits.append(spec.label)

    return result
