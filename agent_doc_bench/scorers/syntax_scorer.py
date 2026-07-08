from __future__ import annotations

import ast
from dataclasses import dataclass

from agent_doc_bench.agent.base_agent import CodingTrace


@dataclass
class SyntaxResult:
    passed: bool
    error: str | None = None

    @property
    def score(self) -> float:
        return 1.0 if self.passed else 0.0

    @property
    def comment(self) -> str | None:
        return self.error


def score(trace: CodingTrace) -> SyntaxResult:
    if not trace.generated_code:
        return SyntaxResult(passed=False, error="empty output")

    if trace.language == "python":
        try:
            ast.parse(trace.generated_code)
            return SyntaxResult(passed=True)
        except SyntaxError as e:
            return SyntaxResult(passed=False, error=str(e))

    # For non-Python languages, default to pass (extend as needed)
    return SyntaxResult(passed=True)
