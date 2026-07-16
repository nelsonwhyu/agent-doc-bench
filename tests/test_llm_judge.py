from __future__ import annotations

import pytest

from agent_doc_bench.agent.base_agent import CodingTrace
from agent_doc_bench.scorers import llm_judge
from agent_doc_bench.scorers.llm_judge import _extract_json_object
from agent_doc_bench.tasks.base_task import CodingTask


def test_extract_json_object_removes_json_fence() -> None:
    fenced = '```json\n{"a": 1}\n```'
    assert _extract_json_object(fenced) == '{"a": 1}'


def test_extract_json_object_removes_plain_fence() -> None:
    fenced = '```\n{"a": 1}\n```'
    assert _extract_json_object(fenced) == '{"a": 1}'


def test_extract_json_object_leaves_unfenced_json_untouched() -> None:
    plain = '{"a": 1}'
    assert _extract_json_object(plain) == plain


def test_extract_json_object_ignores_trailing_prose() -> None:
    # The real, observed shape: fenced JSON followed by explanatory prose,
    # despite the prompt asking for "no text before or after the JSON".
    real_shape = (
        '```json\n{"product_selection": 1, "auth_method": 1, "design_pattern": 1, '
        '"syntax_quality": 3}\n```\n\n'
        "**Justification:**\n- **product_selection (1)**: no context given."
    )
    assert _extract_json_object(real_shape) == (
        '{"product_selection": 1, "auth_method": 1, "design_pattern": 1, "syntax_quality": 3}'
    )


class _FakeTextBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeMessage:
    def __init__(self, text: str) -> None:
        self.content = [_FakeTextBlock(text)]


class _FakeMessages:
    def __init__(self, text: str) -> None:
        self._text = text

    def create(self, **kwargs):
        return _FakeMessage(self._text)


class _FakeAnthropic:
    def __init__(self, text: str) -> None:
        self.messages = _FakeMessages(text)


def _task(rubric: str = "Score 1-5: product_selection, auth_method, design_pattern, syntax_quality") -> CodingTask:
    return CodingTask(id="t1", instruction="do the thing", language="python", llm_judge_rubric=rubric)


def _trace() -> CodingTrace:
    return CodingTrace(generated_code="print('hi')", language="python", steps=1, token_usage={})


def test_score_parses_a_fenced_json_response(monkeypatch: pytest.MonkeyPatch) -> None:
    # This is the exact shape that was silently breaking every llm_judge
    # call in the real evaluate-doc-draft smoke test: Claude wraps its JSON
    # answer in a markdown fence despite being asked for "only the JSON".
    fenced = (
        "```json\n"
        '{"product_selection": 5, "auth_method": 4, "design_pattern": 5, '
        '"syntax_quality": 4, "reasoning": "Uses the `blpapi` library correctly."}\n'
        "```"
    )
    monkeypatch.setattr(llm_judge.anthropic, "Anthropic", lambda: _FakeAnthropic(fenced))

    result = llm_judge.score(_trace(), _task())

    assert result.error is None
    assert result.scores is not None
    assert result.scores.product_selection == 5
    assert result.score == pytest.approx((5 + 4 + 5 + 4) / 20)


def test_score_parses_the_real_observed_shape_fence_plus_trailing_prose_no_reasoning_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Exactly what a real API call returned during the evaluate-doc-draft
    # smoke test: fenced JSON, no "reasoning" key inside it, followed by a
    # "**Justification:**" section outside the JSON entirely.
    real_shape = (
        "```json\n"
        '{\n  "product_selection": 1,\n  "auth_method": 1,\n  "design_pattern": 1,\n  "syntax_quality": 3\n}\n'
        "```\n\n"
        "**Justification:**\n"
        "- **product_selection (1)**: Single import with no context or implementation."
    )
    monkeypatch.setattr(llm_judge.anthropic, "Anthropic", lambda: _FakeAnthropic(real_shape))

    result = llm_judge.score(_trace(), _task())

    assert result.error is None
    assert result.scores is not None
    assert result.scores.product_selection == 1
    assert result.scores.reasoning == ""
    assert result.score == pytest.approx((1 + 1 + 1 + 3) / 20)


def test_score_still_parses_plain_json_response(monkeypatch: pytest.MonkeyPatch) -> None:
    plain = '{"product_selection": 3, "auth_method": 3, "design_pattern": 3, "syntax_quality": 3, "reasoning": "ok"}'
    monkeypatch.setattr(llm_judge.anthropic, "Anthropic", lambda: _FakeAnthropic(plain))

    result = llm_judge.score(_trace(), _task())

    assert result.error is None
    assert result.scores.product_selection == 3


def test_score_surfaces_malformed_json_as_error_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_judge.anthropic, "Anthropic", lambda: _FakeAnthropic("not json at all"))

    result = llm_judge.score(_trace(), _task())

    assert result.scores is None
    assert result.error is not None
    assert result.score == 0.0


def test_score_returns_none_scores_when_task_has_no_rubric() -> None:
    result = llm_judge.score(_trace(), _task(rubric=""))

    assert result.scores is None
    assert result.error == "no rubric defined for this task"
