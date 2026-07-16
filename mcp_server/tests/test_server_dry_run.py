from __future__ import annotations

import pytest

from mcp_server import server


@pytest.fixture(autouse=True)
def dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    # Points LocalDocsClient at the real repo checkout (tests run from repo
    # root), so these exercise the tools against real content without any
    # GitHub credentials or network calls.
    monkeypatch.setenv("MCP_DRY_RUN", "1")
    monkeypatch.setenv("MCP_DRY_RUN_REPO_ROOT", ".")


def test_list_apis() -> None:
    assert "blpapi" in server.list_apis()


def test_list_experiments() -> None:
    assert "doc_ablation" in server.list_experiments()


def test_list_doc_variants() -> None:
    assert {"none", "v1", "v2"} <= set(server.list_doc_variants("blpapi"))


def test_get_doc_variant() -> None:
    assert "BLPAPI" in server.get_doc_variant("blpapi", "v1")


def test_get_doc_requirements_reflects_real_task_suite() -> None:
    text = server.get_doc_requirements("blpapi")
    assert "blpapi_open_session" in text
    assert "instantiates blpapi.Session" in text


def test_validate_doc_variant_flags_empty_draft() -> None:
    issues = server.validate_doc_variant("blpapi", "")
    assert issues and "empty" in issues[0].lower()


def test_validate_doc_variant_accepts_real_content() -> None:
    issues = server.validate_doc_variant("blpapi", "# Real docs\n\n" + "x" * 300)
    assert issues == []


def test_make_client_requires_github_env_when_not_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_DRY_RUN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(KeyError):
        server.make_client()


def test_evaluate_doc_draft_dry_run_never_touches_github() -> None:
    result = server.evaluate_doc_draft(api="blpapi", value="pm-draft", content="# doc", experiment="doc_ablation")

    assert result["run_id"].startswith(server.DRY_RUN_PREFIX)
    assert "doc_ablation" in result["note"]


def test_get_evaluation_status_dry_run_reports_completed() -> None:
    dispatched = server.evaluate_doc_draft(api="blpapi", value="pm-draft", content="# doc", experiment="doc_ablation")

    status = server.get_evaluation_status(dispatched["run_id"])

    assert status["status"] == "completed"
    assert status["conclusion"] == "success"


def test_get_evaluation_report_dry_run_is_a_placeholder() -> None:
    dispatched = server.evaluate_doc_draft(api="blpapi", value="pm-draft", content="# doc", experiment="doc_ablation")

    report = server.get_evaluation_report(dispatched["run_id"])

    assert "MCP_DRY_RUN" in report
