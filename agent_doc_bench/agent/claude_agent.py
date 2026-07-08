from __future__ import annotations

import re

import anthropic

from agent_doc_bench.tasks.base_task import CodingTask

from .base_agent import BaseAgent, CodingTrace, ToolCall

_CODE_FENCE = re.compile(r"```(?:\w+)?\n(.*?)```", re.DOTALL)

SYSTEM_TEMPLATE = """\
You are an expert software engineer. Your task is to write code that satisfies the user's request.

Output ONLY a single fenced code block containing your complete implementation — no explanations outside the block.

{doc_section}
"""

DOC_SECTION = """\
<documentation>
{doc_context}
</documentation>

Use the documentation above to guide your implementation. Follow the recommended patterns, \
authentication methods, and API surfaces it describes.
"""


class ClaudeAgent(BaseAgent):
    def __init__(self, model: str = "claude-sonnet-4-6"):
        self.model = model
        self._client = anthropic.Anthropic()

    def run_task(
        self,
        task: CodingTask,
        doc_context: str,
        tools: list[str],
    ) -> CodingTrace:
        doc_section = DOC_SECTION.format(doc_context=doc_context) if doc_context.strip() else ""
        system = SYSTEM_TEMPLATE.format(doc_section=doc_section)

        messages: list[dict] = [{"role": "user", "content": task.instruction}]
        tool_calls: list[ToolCall] = []
        total_usage: dict = {"input_tokens": 0, "output_tokens": 0}
        steps = 0

        while True:
            steps += 1
            response = self._client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system,
                messages=messages,
            )

            total_usage["input_tokens"] += response.usage.input_tokens
            total_usage["output_tokens"] += response.usage.output_tokens

            text = "".join(
                block.text for block in response.content if hasattr(block, "text")
            )

            if response.stop_reason == "end_turn":
                code = _extract_code(text)
                return CodingTrace(
                    generated_code=code,
                    language=task.language,
                    steps=steps,
                    token_usage=total_usage,
                    tool_calls=tool_calls,
                    error=None if code else "No code block found in response",
                )

            messages.append({"role": "assistant", "content": text})

            if steps > 10:
                return CodingTrace(
                    generated_code=_extract_code(text),
                    language=task.language,
                    steps=steps,
                    token_usage=total_usage,
                    tool_calls=tool_calls,
                    error="max steps exceeded",
                )


def _extract_code(text: str) -> str:
    matches = _CODE_FENCE.findall(text)
    return matches[-1].strip() if matches else text.strip()
