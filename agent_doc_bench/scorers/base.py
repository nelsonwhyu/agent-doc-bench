from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class EvaluatorResult:
    key: str
    score: float | bool | None
    comment: str | None = None


def run_scorer(key: str, fn: Callable[[], tuple[float | bool, str | None]]) -> EvaluatorResult:
    """Run a scorer callable, catching any exception so one failing scorer
    can't abort the whole evaluation run — it just surfaces as a null score
    with the error in the comment instead.
    """
    try:
        score, comment = fn()
        return EvaluatorResult(key=key, score=score, comment=comment)
    except Exception as e:
        return EvaluatorResult(key=key, score=None, comment=f"scorer raised: {e}")
