from __future__ import annotations

import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from agent_doc_bench.agent.base_agent import CodingTrace
from agent_doc_bench.tasks.base_task import CodingTask

_MOCK_SOURCE = Path(__file__).parent.parent / "sandbox" / "fixtures" / "blpapi_mock.py"
_TIMEOUT_SECONDS = 10


@dataclass
class ExecutionResult:
    passed: bool
    comment: str | None = None

    @property
    def score(self) -> float:
        return 1.0 if self.passed else 0.0


def score(trace: CodingTrace, task: CodingTask) -> ExecutionResult:
    """Actually run the generated script against a mocked `blpapi` module,
    rather than only regex-matching its source (see pattern_scorer). This
    catches bugs regex can't, e.g. a loop that never reaches its break
    condition, at the cost of only covering the request types the mock
    knows about (see blpapi_mock.py's docstring for scope).
    """
    if trace.language != "python" or not trace.generated_code:
        return ExecutionResult(passed=False, comment="no python code to execute")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "blpapi.py").write_text(_MOCK_SOURCE.read_text())
        script_path = tmp_path / "generated.py"
        script_path.write_text(trace.generated_code)

        try:
            proc = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECONDS,
                cwd=tmp_path,
            )
        except subprocess.TimeoutExpired:
            return ExecutionResult(
                passed=False,
                comment=f"execution timed out after {_TIMEOUT_SECONDS}s (possible infinite loop)",
            )

        if proc.returncode == 0:
            return ExecutionResult(passed=True)

        stderr_tail = "\n".join(proc.stderr.strip().splitlines()[-5:])
        is_mock_gap = "blpapi_mock:" in stderr_tail
        prefix = "mock coverage gap, not necessarily a code defect: " if is_mock_gap else "execution failed: "
        return ExecutionResult(passed=False, comment=prefix + stderr_tail)
