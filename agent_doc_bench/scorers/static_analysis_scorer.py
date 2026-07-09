from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from agent_doc_bench.agent.base_agent import CodingTrace

_TIMEOUT_SECONDS = 15


@dataclass
class StaticAnalysisResult:
    ruff_issues: list[str] = field(default_factory=list)
    bandit_issues: list[str] = field(default_factory=list)
    tool_errors: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        # Bandit findings are security issues and weighted heavier than
        # ruff's correctness/style findings (unused imports, undefined
        # names, ...).
        penalty = 0.1 * len(self.ruff_issues) + 0.25 * len(self.bandit_issues)
        return max(0.0, 1.0 - penalty)

    @property
    def comment(self) -> str | None:
        parts = []
        if self.bandit_issues:
            parts.append(f"security: {'; '.join(self.bandit_issues)}")
        if self.ruff_issues:
            parts.append(f"lint: {'; '.join(self.ruff_issues)}")
        if self.tool_errors:
            parts.append(f"tool errors: {'; '.join(self.tool_errors)}")
        return " | ".join(parts) if parts else None


def _run_ruff(path: Path) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    errors: list[str] = []
    try:
        # Restrict to pyflakes (F) — undefined names, unused imports/vars,
        # etc. Pure style rules (E/W) are excluded since LLM formatting
        # quirks would swamp the score without signaling real defects.
        # Missing third-party imports (blpapi isn't on PyPI) aren't flagged
        # by these rules, so no need to ignore them explicitly.
        proc = subprocess.run(
            ["ruff", "check", "--select", "F", "--output-format=json", str(path)],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
        data = json.loads(proc.stdout or "[]")
        for item in data:
            code = item.get("code", "?")
            msg = item.get("message", "")
            line = item.get("location", {}).get("row", "?")
            issues.append(f"{code} line {line}: {msg}")
    except Exception as e:
        errors.append(f"ruff: {e}")
    return issues, errors


def _run_bandit(path: Path) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    errors: list[str] = []
    try:
        proc = subprocess.run(
            ["bandit", "-f", "json", "-q", str(path)],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
        data = json.loads(proc.stdout or "{}")
        for item in data.get("results", []):
            issues.append(f"{item.get('test_id')} line {item.get('line_number')}: {item.get('issue_text')}")
    except Exception as e:
        errors.append(f"bandit: {e}")
    return issues, errors


def score(trace: CodingTrace) -> StaticAnalysisResult:
    if trace.language != "python" or not trace.generated_code:
        return StaticAnalysisResult()

    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(trace.generated_code)
        tmp_path = Path(f.name)

    try:
        ruff_issues, ruff_errors = _run_ruff(tmp_path)
        bandit_issues, bandit_errors = _run_bandit(tmp_path)
        return StaticAnalysisResult(
            ruff_issues=ruff_issues,
            bandit_issues=bandit_issues,
            tool_errors=ruff_errors + bandit_errors,
        )
    finally:
        tmp_path.unlink(missing_ok=True)
