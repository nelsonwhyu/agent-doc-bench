from __future__ import annotations

from dataclasses import dataclass

import anthropic
from pydantic import BaseModel

from agent_doc_bench.agent.base_agent import CodingTrace
from agent_doc_bench.tasks.base_task import CodingTask

JUDGE_MODEL = "claude-haiku-4-5-20251001"

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

Score each rubric dimension from 1 (very poor) to 5 (excellent). Return only the JSON scores.
"""


class JudgeScores(BaseModel):
    product_selection: int
    auth_method: int
    design_pattern: int
    syntax_quality: int
    reasoning: str


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
        scores = JudgeScores.model_validate_json(text)
        return LLMJudgeResult(scores=scores)
    except Exception as e:
        return LLMJudgeResult(scores=None, error=str(e))
