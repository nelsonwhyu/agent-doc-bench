from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from agent_doc_bench.agent.base_agent import CodingTrace
from agent_doc_bench.tasks.base_task import CodingTask

_FIXTURES_DIR = Path(__file__).parent.parent / "sandbox" / "fixtures"
_MOCK_SOURCE = _FIXTURES_DIR / "blpapi_mock.py"
_LIVE_SHIM_SOURCE = _FIXTURES_DIR / "blpapi_live_shim.py"
_LIVE_RUNNER_SOURCE = Path(__file__).parent.parent / "sandbox" / "live_runner.py"
_LOCAL_LOG_DIR = Path(__file__).parent.parent / "sandbox" / ".live_logs"
_TIMEOUT_SECONDS = 10


@dataclass
class ExecutionResult:
    passed: bool
    comment: str | None = None

    @property
    def score(self) -> float:
        return 1.0 if self.passed else 0.0


def _is_live_mode() -> bool:
    return os.environ.get("BLOOMBERG_MODE", "mock") == "live"


def score(trace: CodingTrace, task: CodingTask) -> ExecutionResult:
    """Run the generated script against either a mocked `blpapi` module
    (default) or a real Bloomberg Terminal (BLOOMBERG_MODE=live), rather
    than only regex-matching its source (see pattern_scorer). This catches
    bugs regex can't, e.g. a loop that never reaches its break condition.

    In mock mode, coverage is limited to the request types blpapi_mock.py
    knows about. In live mode, the script's real stdout/stderr may contain
    actual market data, so it is never inspected here or reported to
    LangSmith — only the sandboxed run's exit code and the structural
    metadata captured by blpapi_live_shim.py (event types, message counts,
    timing) are used.
    """
    if trace.language != "python" or not trace.generated_code:
        return ExecutionResult(passed=False, comment="no python code to execute")

    live_mode = _is_live_mode()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        script_path = tmp_path / "generated.py"
        script_path.write_text(trace.generated_code)

        if live_mode:
            (tmp_path / "blpapi_live_shim.py").write_text(_LIVE_SHIM_SOURCE.read_text())
            entry_path = tmp_path / "live_runner.py"
            entry_path.write_text(_LIVE_RUNNER_SOURCE.read_text())
            metadata_path = tmp_path / "_bench_metadata.json"
        else:
            (tmp_path / "blpapi.py").write_text(_MOCK_SOURCE.read_text())
            entry_path = script_path

        try:
            proc = subprocess.run(
                [sys.executable, str(entry_path)],
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

        if live_mode:
            return _score_live(proc, metadata_path, task)
        return _score_mock(proc)


def _score_mock(proc: subprocess.CompletedProcess) -> ExecutionResult:
    if proc.returncode == 0:
        return ExecutionResult(passed=True)

    stderr_tail = "\n".join(proc.stderr.strip().splitlines()[-5:])
    is_mock_gap = "blpapi_mock:" in stderr_tail
    prefix = "mock coverage gap, not necessarily a code defect: " if is_mock_gap else "execution failed: "
    return ExecutionResult(passed=False, comment=prefix + stderr_tail)


def _score_live(
    proc: subprocess.CompletedProcess, metadata_path: Path, task: CodingTask
) -> ExecutionResult:
    """Build a result from exit code + structural metadata only.

    proc.stdout/proc.stderr are deliberately never read into the comment —
    a generated script prints or raises on real values (prices, security
    data, ...) that must not leave the sandbox. Raw output is written only
    to a local, gitignored log file for the operator's own debugging.
    """
    events = []
    if metadata_path.exists():
        try:
            events = json.loads(metadata_path.read_text())
        except (json.JSONDecodeError, OSError):
            events = []

    event_summary = ", ".join(f"{e['kind']}" for e in events) if events else "no session activity observed"
    log_path = _write_local_log(proc, task)

    if proc.returncode == 0:
        return ExecutionResult(
            passed=True,
            comment=f"live execution succeeded ({len(events)} session events: {event_summary})",
        )

    return ExecutionResult(
        passed=False,
        comment=(
            f"live execution failed (exit {proc.returncode}, {len(events)} session events: "
            f"{event_summary}); raw output kept local-only, not sent to LangSmith: {log_path}"
        ),
    )


def _write_local_log(proc: subprocess.CompletedProcess, task: CodingTask) -> Path:
    _LOCAL_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = _LOCAL_LOG_DIR / f"{task.id}-{int(time.time())}.log"
    log_path.write_text(
        f"exit_code={proc.returncode}\n\n--- stdout ---\n{proc.stdout}\n\n--- stderr ---\n{proc.stderr}\n"
    )
    return log_path
