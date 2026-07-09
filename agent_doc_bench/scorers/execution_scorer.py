from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


def score(trace: CodingTrace, task: CodingTask, run_context: dict[str, Any] | None = None) -> ExecutionResult:
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

    `run_context` (e.g. {"variable_name": "documentation", "variable_value":
    "v1", "model": ..., "tools": ...}) identifies which experiment variant
    produced this run — it's config labels, not generated content, so it's
    safe to fold into the local log's filename/header even though it never
    reaches LangSmith itself.
    """
    if trace.language != "python" or not trace.generated_code:
        return ExecutionResult(passed=False, comment="no python code to execute")

    live_mode = _is_live_mode()
    metadata_path: Path | None = None

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
            assert metadata_path is not None
            return _score_live(proc, metadata_path, task, run_context)
        return _score_mock(proc)


def _score_mock(proc: subprocess.CompletedProcess) -> ExecutionResult:
    if proc.returncode == 0:
        return ExecutionResult(passed=True)

    stderr_tail = "\n".join(proc.stderr.strip().splitlines()[-5:])
    is_mock_gap = "blpapi_mock:" in stderr_tail
    prefix = "mock coverage gap, not necessarily a code defect: " if is_mock_gap else "execution failed: "
    return ExecutionResult(passed=False, comment=prefix + stderr_tail)


def _score_live(
    proc: subprocess.CompletedProcess,
    metadata_path: Path,
    task: CodingTask,
    run_context: dict[str, Any] | None,
) -> ExecutionResult:
    """Build a result from exit code + structural metadata only.

    proc.stdout/proc.stderr are deliberately never read into the comment —
    a generated script prints or raises on real values (prices, security
    data, ...) that must not leave the sandbox. Raw output is written only
    to a local, gitignored log file for the operator's own debugging.

    Exit code 0 alone isn't enough to call this "passed": a script can
    exit cleanly without ever reaching Bloomberg (e.g. it ignores
    session.start()'s return value) or without ever getting real data back
    (e.g. it breaks out on the first TIMEOUT). blpapi_live_shim.py computes
    session_started/received_response semantically, using the real
    blpapi.Event constants — this function just enforces them.
    """
    events: list[dict] = []
    session_started = False
    received_response = False
    if metadata_path.exists():
        try:
            data = json.loads(metadata_path.read_text())
            events = data.get("events", [])
            session_started = data.get("session_started", False)
            received_response = data.get("received_response", False)
        except (json.JSONDecodeError, OSError):
            pass

    event_summary = ", ".join(f"{e['kind']}" for e in events) if events else "no session activity observed"
    log_path = _write_local_log(proc, task, run_context)

    if proc.returncode != 0:
        return ExecutionResult(
            passed=False,
            comment=(
                f"live execution failed (exit {proc.returncode}, {len(events)} session events: "
                f"{event_summary}); raw output kept local-only, not sent to LangSmith: {log_path}"
            ),
        )

    if not session_started:
        return ExecutionResult(
            passed=False,
            comment=(
                "script exited 0 but the session never reported a successful start "
                f"({len(events)} session events: {event_summary}); raw output kept local-only, "
                f"not sent to LangSmith: {log_path}"
            ),
        )

    if not received_response:
        return ExecutionResult(
            passed=False,
            comment=(
                "script exited 0 and the session started, but no RESPONSE/PARTIAL_RESPONSE event "
                f"was ever observed ({len(events)} session events: {event_summary}); raw output "
                f"kept local-only, not sent to LangSmith: {log_path}"
            ),
        )

    return ExecutionResult(
        passed=True,
        comment=f"live execution succeeded ({len(events)} session events: {event_summary})",
    )


def _slugify(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value)).strip("-") or "unknown"


def _write_local_log(
    proc: subprocess.CompletedProcess, task: CodingTask, run_context: dict[str, Any] | None
) -> Path:
    run_context = run_context or {}
    variant_slug = _slugify(f"{run_context.get('variable_name', 'variant')}-{run_context.get('variable_value', 'unknown')}")

    _LOCAL_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = _LOCAL_LOG_DIR / f"{task.id}__{variant_slug}__{int(time.time())}.log"

    fixed_config = {k: v for k, v in run_context.items() if k not in ("variable_name", "variable_value")}
    header = (
        f"task_id={task.id}\n"
        f"variable={run_context.get('variable_name', '?')}={run_context.get('variable_value', '?')}\n"
        f"fixed_config={fixed_config}\n"
        f"exit_code={proc.returncode}\n"
    )
    log_path.write_text(f"{header}\n--- stdout ---\n{proc.stdout}\n\n--- stderr ---\n{proc.stderr}\n")
    return log_path
