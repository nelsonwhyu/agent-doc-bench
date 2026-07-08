from __future__ import annotations

import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ExecutionResult:
    passed: bool
    stdout: str
    stderr: str
    returncode: int

    @property
    def score(self) -> float:
        return 1.0 if self.passed else 0.0


def run_python(code: str, timeout: int = 10) -> ExecutionResult:
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(code)
        tmp_path = Path(f.name)

    try:
        proc = subprocess.run(
            [sys.executable, str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return ExecutionResult(
            passed=proc.returncode == 0,
            stdout=proc.stdout,
            stderr=proc.stderr,
            returncode=proc.returncode,
        )
    except subprocess.TimeoutExpired:
        return ExecutionResult(passed=False, stdout="", stderr="timeout", returncode=-1)
    finally:
        tmp_path.unlink(missing_ok=True)
