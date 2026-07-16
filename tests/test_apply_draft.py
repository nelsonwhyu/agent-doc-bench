from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

SCRIPT = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "scripts" / "apply_draft.py"


def _run(tmp_path: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=tmp_path,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
    )


def _seed_experiment(tmp_path: Path, name: str, values: list[str]) -> None:
    (tmp_path / "experiments").mkdir(exist_ok=True)
    (tmp_path / "experiments" / f"{name}.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "task_suite": "blpapi",
                "variable": {"name": "documentation", "values": values},
                "fixed": {},
                "scorers": ["syntax"],
            }
        )
    )


def test_writes_draft_and_appends_new_value(tmp_path: Path) -> None:
    _seed_experiment(tmp_path, "doc_ablation", ["none", "v1"])

    result = _run(
        tmp_path,
        {
            "DRAFT_API": "blpapi",
            "DRAFT_VALUE": "pm-draft-1",
            "DRAFT_CONTENT": "# hello\n\nreal content " + "x" * 200,
            "DRAFT_EXPERIMENT": "doc_ablation",
        },
    )

    assert result.returncode == 0, result.stderr
    doc_path = tmp_path / "docs_library" / "blpapi" / "pm-draft-1.md"
    assert doc_path.read_text().startswith("# hello")

    data = yaml.safe_load((tmp_path / "experiments" / "doc_ablation.yaml").read_text())
    assert "pm-draft-1" in data["variable"]["values"]


def test_does_not_duplicate_existing_value(tmp_path: Path) -> None:
    _seed_experiment(tmp_path, "doc_ablation", ["none", "v1"])

    result = _run(
        tmp_path,
        {
            "DRAFT_API": "blpapi",
            "DRAFT_VALUE": "v1",
            "DRAFT_CONTENT": "# real content\n\n" + "x" * 200,
            "DRAFT_EXPERIMENT": "doc_ablation",
        },
    )

    assert result.returncode == 0, result.stderr
    data = yaml.safe_load((tmp_path / "experiments" / "doc_ablation.yaml").read_text())
    assert data["variable"]["values"].count("v1") == 1


def test_rejects_unsafe_identifier(tmp_path: Path) -> None:
    _seed_experiment(tmp_path, "doc_ablation", ["none"])

    result = _run(
        tmp_path,
        {
            "DRAFT_API": "../../etc",
            "DRAFT_VALUE": "v1",
            "DRAFT_CONTENT": "# real content\n\n" + "x" * 200,
            "DRAFT_EXPERIMENT": "doc_ablation",
        },
    )

    assert result.returncode != 0
    assert "safe identifier" in result.stderr
    assert not (tmp_path / ".." / ".." / "etc").resolve().exists()


def test_rejects_experiment_without_documentation_variable(tmp_path: Path) -> None:
    (tmp_path / "experiments").mkdir()
    (tmp_path / "experiments" / "llm_ablation.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "llm_ablation",
                "task_suite": "blpapi",
                "variable": {"name": "model", "values": ["a", "b"]},
                "fixed": {},
                "scorers": ["syntax"],
            }
        )
    )

    result = _run(
        tmp_path,
        {
            "DRAFT_API": "blpapi",
            "DRAFT_VALUE": "v1",
            "DRAFT_CONTENT": "# real content\n\n" + "x" * 200,
            "DRAFT_EXPERIMENT": "llm_ablation",
        },
    )

    assert result.returncode != 0
    assert "documentation" in result.stderr


def test_rejects_empty_draft_without_touching_disk(tmp_path: Path) -> None:
    _seed_experiment(tmp_path, "doc_ablation", ["none", "v1"])

    result = _run(
        tmp_path,
        {
            "DRAFT_API": "blpapi",
            "DRAFT_VALUE": "pm-draft-empty",
            "DRAFT_CONTENT": "",
            "DRAFT_EXPERIMENT": "doc_ablation",
        },
    )

    assert result.returncode != 0
    assert "Draft rejected" in result.stderr
    assert not (tmp_path / "docs_library" / "blpapi" / "pm-draft-empty.md").exists()


def test_rejects_stub_draft_regardless_of_other_variants(tmp_path: Path) -> None:
    # v1.md itself contains a "> **Stub.**" marker in the real repo — this
    # draft-only gate must not care about that; it only judges the content
    # actually passed in for this call.
    _seed_experiment(tmp_path, "doc_ablation", ["none", "v1"])

    result = _run(
        tmp_path,
        {
            "DRAFT_API": "blpapi",
            "DRAFT_VALUE": "pm-draft-stub",
            "DRAFT_CONTENT": "> **Stub.** placeholder",
            "DRAFT_EXPERIMENT": "doc_ablation",
        },
    )

    assert result.returncode != 0
    assert "Draft rejected" in result.stderr


def test_rejects_missing_experiment(tmp_path: Path) -> None:
    (tmp_path / "experiments").mkdir()

    result = _run(
        tmp_path,
        {
            "DRAFT_API": "blpapi",
            "DRAFT_VALUE": "v1",
            "DRAFT_CONTENT": "# real content\n\n" + "x" * 200,
            "DRAFT_EXPERIMENT": "does_not_exist",
        },
    )

    assert result.returncode != 0
    assert "does not exist" in result.stderr
