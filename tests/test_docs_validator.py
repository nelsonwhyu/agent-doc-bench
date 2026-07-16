from __future__ import annotations

from pathlib import Path

from agent_doc_bench.docs_validator import validate_docs


def _write_doc(base: Path, api: str, value: str, content: str) -> None:
    d = base / api
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{value}.md").write_text(content)


def _write_experiment(base: Path, name: str, api: str, values: list[str]) -> None:
    base.mkdir(parents=True, exist_ok=True)
    (base / f"{name}.yaml").write_text(
        f"name: {name}\n"
        f"task_suite: {api}\n"
        f"variable:\n  name: documentation\n  values: {values}\n"
        "fixed: {}\n"
        "scorers: [syntax]\n"
    )


def test_stub_flagged_as_warning(tmp_path: Path) -> None:
    docs = tmp_path / "docs_library"
    experiments = tmp_path / "experiments"

    _write_doc(docs, "blpapi", "none", "")
    _write_doc(docs, "blpapi", "v1", "# Real docs\n\n" + "x" * 300)
    _write_doc(docs, "blpapi", "v2", "> **Stub.** placeholder")
    _write_experiment(experiments, "doc_ablation", "blpapi", ["none", "v1", "v2"])

    issues = validate_docs(experiments_dir=experiments, docs_base=docs)

    stub_issues = [i for i in issues if i.kind == "stub"]
    assert len(stub_issues) == 1
    assert stub_issues[0].value == "v2"


def test_empty_none_is_not_flagged(tmp_path: Path) -> None:
    docs = tmp_path / "docs_library"
    experiments = tmp_path / "experiments"

    _write_doc(docs, "blpapi", "none", "")
    _write_doc(docs, "blpapi", "v1", "x" * 300)
    _write_experiment(experiments, "doc_ablation", "blpapi", ["none", "v1"])

    issues = validate_docs(experiments_dir=experiments, docs_base=docs)

    assert not any(i.value == "none" for i in issues)


def test_missing_file_is_hard_failure(tmp_path: Path) -> None:
    docs = tmp_path / "docs_library"
    experiments = tmp_path / "experiments"

    _write_doc(docs, "blpapi", "none", "")
    _write_experiment(experiments, "doc_ablation", "blpapi", ["none", "v1_typo"])

    issues = validate_docs(experiments_dir=experiments, docs_base=docs)

    missing = [i for i in issues if i.kind == "missing"]
    assert len(missing) == 1
    assert missing[0].value == "v1_typo"


def test_non_documentation_variable_is_ignored(tmp_path: Path) -> None:
    docs = tmp_path / "docs_library"
    experiments = tmp_path / "experiments"
    experiments.mkdir()

    (experiments / "llm_ablation.yaml").write_text(
        "name: llm_ablation\n"
        "task_suite: blpapi\n"
        "variable:\n  name: model\n  values: [a, b]\n"
        "fixed: {}\n"
        "scorers: [syntax]\n"
    )

    issues = validate_docs(experiments_dir=experiments, docs_base=docs)

    assert issues == []


def test_real_repo_flags_known_stub() -> None:
    # v1.md contains a "> **Stub.**" callout (it's placeholder/example content
    # by its own description), so it should trip the stub heuristic. v2.md
    # isn't referenced by any experiment's variable.values today, so it's
    # correctly out of scope for validate_docs (which only checks values a
    # real ablation run would actually load).
    issues = validate_docs()

    v1_issues = [i for i in issues if i.api == "blpapi" and i.value == "v1"]
    assert v1_issues and v1_issues[0].kind == "stub"
    assert not any(i.kind == "missing" for i in issues)
    assert not any(i.value == "none" for i in issues)
