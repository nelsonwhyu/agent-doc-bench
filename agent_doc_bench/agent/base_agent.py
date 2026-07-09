from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from agent_doc_bench.tasks.base_task import CodingTask


@dataclass
class ToolCall:
    name: str
    args: dict
    result: str


@dataclass
class CodingTrace:
    generated_code: str
    language: str
    steps: int
    token_usage: dict
    tool_calls: list[ToolCall] = field(default_factory=list)
    error: str | None = None
    # Latency metrics, in seconds / tokens-per-second. Populated by streaming
    # agents; left empty ({}) by agents that don't support timing.
    latency: dict = field(default_factory=dict)


class BaseAgent(ABC):
    @abstractmethod
    def run_task(
        self,
        task: CodingTask,
        doc_context: str,
        tools: list[str],
    ) -> CodingTrace:
        ...
