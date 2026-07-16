from __future__ import annotations

import io
import time
import zipfile
from dataclasses import dataclass
from typing import Any

import requests
from github import Github

WORKFLOW_FILE = "evaluate-doc-draft.yml"
EVENT_TYPE = "evaluate-doc-draft"
ARTIFACT_NAME = "report"


@dataclass
class EvaluationRun:
    run_id: int
    status: str  # "queued" | "in_progress" | "completed"
    conclusion: str | None  # "success" | "failure" | ... | None while not completed


class ActionsClient:
    """Wraps the GitHub Actions API to dispatch, poll, and fetch the result
    of an evaluate_doc_draft run. Needs Contents: Read and Actions: Read
    and write — never Contents: Write or Pull requests. This client has no
    code path that can commit to the repo (see IMPLEMENTATION_PLAN.md's
    Layer 2 design).
    """

    def __init__(self, token: str, owner: str, repo: str) -> None:
        self._token = token
        self._repo = Github(token).get_repo(f"{owner}/{repo}")

    def dispatch_evaluation(self, api: str, value: str, content: str, experiment: str) -> int:
        """Fires the evaluate-doc-draft repository_dispatch event, then
        polls for the run it triggers — dispatch doesn't return a run id
        directly — and returns that run's id.
        """
        before_ids = {run.id for run in self._recent_runs()}

        self._repo.create_repository_dispatch(
            event_type=EVENT_TYPE,
            client_payload={"api": api, "value": value, "content": content, "experiment": experiment},
        )

        return self._await_new_run(before_ids)

    def get_status(self, run_id: int) -> EvaluationRun:
        run = self._repo.get_workflow_run(run_id)
        return EvaluationRun(run_id=run.id, status=run.status, conclusion=run.conclusion)

    def get_report(self, run_id: int) -> str:
        """Returns the Markdown report the workflow rendered and uploaded.
        Raises RuntimeError if the run hasn't finished yet, or finished
        without producing a report (most likely the validate-docs --strict
        gate rejected the draft before the ablation ever ran).
        """
        run = self._repo.get_workflow_run(run_id)
        if run.status != "completed":
            raise RuntimeError(f"run {run_id} is still {run.status!r} — check get_evaluation_status first")

        for artifact in run.get_artifacts():
            if artifact.name == ARTIFACT_NAME:
                return self._download_text_artifact(artifact)

        raise RuntimeError(
            f"run {run_id} completed with conclusion={run.conclusion!r} but produced no {ARTIFACT_NAME!r} "
            "artifact — likely failed at the validate-docs --strict gate before running the ablation"
        )

    def _recent_runs(self, limit: int = 5) -> list[Any]:
        workflow = self._repo.get_workflow(WORKFLOW_FILE)
        runs = []
        for i, run in enumerate(workflow.get_runs()):
            if i >= limit:
                break
            runs.append(run)
        return runs

    def _await_new_run(self, before_ids: set[int], timeout_s: float = 30.0, poll_interval_s: float = 2.0) -> int:
        deadline = time.monotonic() + timeout_s
        while True:
            for run in self._recent_runs():
                if run.id not in before_ids:
                    return run.id
            if time.monotonic() >= deadline:
                raise RuntimeError("evaluate-doc-draft was dispatched, but no new workflow run appeared in time")
            time.sleep(poll_interval_s)

    def _download_text_artifact(self, artifact: Any) -> str:
        response = requests.get(
            artifact.archive_download_url,
            headers={"Authorization": f"Bearer {self._token}", "Accept": "application/vnd.github+json"},
        )
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            names = zf.namelist()
            if not names:
                raise RuntimeError(f"artifact {artifact.name!r} is empty")
            with zf.open(names[0]) as f:
                return f.read().decode("utf-8")
