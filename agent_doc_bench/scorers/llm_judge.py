from __future__ import annotations

import re
from dataclasses import dataclass

import anthropic
from pydantic import BaseModel

from agent_doc_bench.agent.base_agent import CodingTrace
from agent_doc_bench.tasks.base_task import CodingTask

JUDGE_MODEL = "claude-haiku-4-5-20251001"

# In practice Claude doesn't reliably return *only* a JSON object: it often
# wraps it in a ```json fence and, despite being told to return only JSON,
# frequently appends explanatory prose afterward (e.g. "**Justification:**
# ..."). model_validate_json() needs an exact JSON document, so the object
# has to be located and extracted rather than assumed to be the whole
# string. The rubric fields here are always a flat, unnested object, so a
# regex for "a {...} with no nested braces" is enough — no real JSON parser
# needed to locate it.
_JSON_OBJECT_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


def _extract_json_object(text: str) -> str:
    match = _JSON_OBJECT_RE.search(text)
    return match.group(0) if match else text

JUDGE_PROMPT = """\
You are an expert code reviewer evaluating AI-generated code.

Task the agent was given:
{instruction}

Rubric:
{rubric}

Generated code:
```
{code}
```

Score each rubric dimension from 1 (very poor) to 5 (excellent). Respond with
nothing but a single JSON object with integer keys product_selection,
auth_method, design_pattern, syntax_quality, and a string key "reasoning"
holding a one-sentence justification. No text before or after the JSON.
"""


class JudgeScores(BaseModel):
    product_selection: int
    auth_method: int
    design_pattern: int
    syntax_quality: int
    # Claude sometimes omits this even when explicitly asked for it,
    # putting the explanation in prose outside the JSON instead — default
    # to empty rather than fail validation over a field that isn't scored.
    reasoning: str = ""


@dataclass
class LLMJudgeResult:
    scores: JudgeScores | None
    error: str | None = None

    @property
    def score(self) -> float:
        if self.scores is None:
            return 0.0
        values = [
            self.scores.product_selection,
            self.scores.auth_method,
            self.scores.design_pattern,
            self.scores.syntax_quality,
        ]
        return (sum(values) / (len(values) * 5))

    @property
    def comment(self) -> str | None:
        if self.scores is not None:
            return self.scores.reasoning
        return self.error


def score(trace: CodingTrace, task: CodingTask) -> LLMJudgeResult:
    if not task.llm_judge_rubric:
        return LLMJudgeResult(scores=None, error="no rubric defined for this task")

    client = anthropic.Anthropic()
    prompt = JUDGE_PROMPT.format(
        instruction=task.instruction,
        rubric=task.llm_judge_rubric,
        code=trace.generated_code or "(no code produced)",
    )

    try:
        response = client.messages.create(
            model=JUDGE_MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text
        scores = JudgeScores.model_validate_json(_extract_json_object(text))
        return LLMJudgeResult(scores=scores)
    except Exception as e:
        return LLMJudgeResult(scores=None, error=str(e))
