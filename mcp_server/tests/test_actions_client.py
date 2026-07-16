from __future__ import annotations

import io
import zipfile

import pytest

from mcp_server.actions_client import ActionsClient


def _zip_bytes(filename: str, content: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(filename, content)
    return buf.getvalue()


class FakeResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        pass


class FakeArtifact:
    def __init__(self, name: str, content: bytes) -> None:
        self.name = name
        self.archive_download_url = "https://example.invalid/artifact.zip"
        self.content = content


class FakeWorkflowRun:
    def __init__(self, run_id: int, status: str = "completed", conclusion: str | None = "success", artifacts=None) -> None:
        self.id = run_id
        self.status = status
        self.conclusion = conclusion
        self._artifacts = artifacts or []

    def get_artifacts(self):
        return list(self._artifacts)


class FakeWorkflow:
    def __init__(self, runs: list[FakeWorkflowRun]) -> None:
        self._runs = runs

    def get_runs(self):
        return list(self._runs)


class FakeRepo:
    """Stands in for PyGithub's Repository — only the handful of methods
    ActionsClient actually calls. create_repository_dispatch() simulates
    GitHub starting a new run, the way the real API does asynchronously,
    so dispatch_evaluation's "poll for a run id not in before_ids" logic
    gets something new to find.
    """

    def __init__(self, workflow_runs: list[FakeWorkflowRun]) -> None:
        self._workflow = FakeWorkflow(list(workflow_runs))
        self._runs_by_id = {r.id: r for r in workflow_runs}
        self.dispatch_calls: list[tuple[str, dict]] = []

    def get_workflow(self, name: str) -> FakeWorkflow:
        return self._workflow

    def create_repository_dispatch(self, event_type: str, client_payload: dict) -> bool:
        self.dispatch_calls.append((event_type, client_payload))
        new_run = FakeWorkflowRun(run_id=999)
        self._workflow._runs.insert(0, new_run)
        self._runs_by_id[999] = new_run
        return True

    def get_workflow_run(self, run_id: int) -> FakeWorkflowRun:
        return self._runs_by_id[run_id]


def _make_client(repo: FakeRepo) -> ActionsClient:
    client = ActionsClient.__new__(ActionsClient)
    client._token = "test-token"
    client._repo = repo
    return client


def test_dispatch_evaluation_returns_the_new_runs_id() -> None:
    repo = FakeRepo([FakeWorkflowRun(1)])
    client = _make_client(repo)

    run_id = client.dispatch_evaluation(api="blpapi", value="pm-draft", content="# doc", experiment="doc_ablation")

    assert run_id == 999
    assert repo.dispatch_calls == [
        ("evaluate-doc-draft", {"api": "blpapi", "value": "pm-draft", "content": "# doc", "experiment": "doc_ablation"})
    ]


def test_get_status_reflects_run_state() -> None:
    repo = FakeRepo([FakeWorkflowRun(42, status="in_progress", conclusion=None)])
    client = _make_client(repo)

    status = client.get_status(42)

    assert status.run_id == 42
    assert status.status == "in_progress"
    assert status.conclusion is None


def test_get_report_downloads_and_returns_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    artifact = FakeArtifact("report", _zip_bytes("report.md", "# Results\n\nlooks good"))
    repo = FakeRepo([FakeWorkflowRun(7, status="completed", conclusion="success", artifacts=[artifact])])
    client = _make_client(repo)

    monkeypatch.setattr("mcp_server.actions_client.requests.get", lambda *a, **k: FakeResponse(artifact.content))

    report = client.get_report(7)

    assert "looks good" in report


def test_get_report_raises_if_run_not_completed() -> None:
    repo = FakeRepo([FakeWorkflowRun(7, status="in_progress", conclusion=None)])
    client = _make_client(repo)

    with pytest.raises(RuntimeError, match="still"):
        client.get_report(7)


def test_get_report_raises_if_no_artifact() -> None:
    repo = FakeRepo([FakeWorkflowRun(7, status="completed", conclusion="failure", artifacts=[])])
    client = _make_client(repo)

    with pytest.raises(RuntimeError, match="report"):
        client.get_report(7)
