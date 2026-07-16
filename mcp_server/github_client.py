from __future__ import annotations

import shutil
from pathlib import Path
from typing import Protocol

from agent_doc_bench import doc_source


class DocsRepoClient(Protocol):
    """Read-only view over the parts of the repo the MCP server's tools
    need: docs_library/, task_suites/, experiments/. Implemented once
    against the real GitHub API (GitHubDocsClient) and once against a local
    checkout (LocalDocsClient, for MCP_DRY_RUN and tests) so server.py can
    swap between them without its tool functions caring which is in use.
    """

    def list_apis(self) -> list[str]: ...
    def list_experiments(self) -> list[str]: ...
    def list_doc_variants(self, api: str) -> list[str]: ...
    def get_doc_variant(self, api: str, value: str) -> str: ...
    def fetch_task_suite(self, api: str, dest_dir: Path) -> None: ...


class GitHubDocsClient:
    """Read-only wrapper over the GitHub Contents API. No write scopes are
    ever used here — the MCP server has no path to commit to the repo (see
    IMPLEMENTATION_PLAN.md's Layer 2 design decisions).
    """

    def __init__(self, token: str, owner: str, repo: str) -> None:
        from github import Github

        self._repo = Github(token).get_repo(f"{owner}/{repo}")

    def list_apis(self) -> list[str]:
        return sorted(c.name for c in self._repo.get_contents("docs_library") if c.type == "dir")

    def list_experiments(self) -> list[str]:
        return sorted(c.name[: -len(".yaml")] for c in self._repo.get_contents("experiments") if c.name.endswith(".yaml"))

    def list_doc_variants(self, api: str) -> list[str]:
        try:
            contents = self._repo.get_contents(f"docs_library/{api}")
        except Exception:
            return []
        return sorted(c.name[: -len(".md")] for c in contents if c.name.endswith(".md"))

    def get_doc_variant(self, api: str, value: str) -> str:
        content_file = self._repo.get_contents(f"docs_library/{api}/{value}.md")
        return content_file.decoded_content.decode("utf-8")

    def fetch_task_suite(self, api: str, dest_dir: Path) -> None:
        """Download task_suites/<api>/*.yaml into dest_dir/<api>/ so
        doc_requirements.build_doc_requirements(api, base_dir=dest_dir) can
        read them the same way it reads a local checkout.
        """
        target = dest_dir / api
        target.mkdir(parents=True, exist_ok=True)
        for content_file in self._repo.get_contents(f"task_suites/{api}"):
            if content_file.name.endswith(".yaml"):
                (target / content_file.name).write_bytes(content_file.decoded_content)


class LocalDocsClient:
    """Same interface as GitHubDocsClient, reading straight off a local
    checkout instead of the GitHub API. Used for MCP_DRY_RUN=1 and for
    tests, so the read tools can be exercised without live GitHub
    credentials — see mcp_server/tests/test_server_dry_run.py.
    """

    def __init__(self, repo_root: str | Path = ".") -> None:
        self._root = Path(repo_root)

    def list_apis(self) -> list[str]:
        docs_dir = self._root / "docs_library"
        if not docs_dir.exists():
            return []
        return sorted(p.name for p in docs_dir.iterdir() if p.is_dir())

    def list_experiments(self) -> list[str]:
        exp_dir = self._root / "experiments"
        if not exp_dir.exists():
            return []
        return sorted(p.stem for p in exp_dir.glob("*.yaml"))

    def list_doc_variants(self, api: str) -> list[str]:
        return doc_source.list_variants(api, self._root / "docs_library")

    def get_doc_variant(self, api: str, value: str) -> str:
        return doc_source.get_variant(api, value, self._root / "docs_library")

    def fetch_task_suite(self, api: str, dest_dir: Path) -> None:
        src = self._root / "task_suites" / api
        target = dest_dir / api
        target.mkdir(parents=True, exist_ok=True)
        for yaml_file in src.glob("*.yaml"):
            shutil.copy(yaml_file, target / yaml_file.name)
