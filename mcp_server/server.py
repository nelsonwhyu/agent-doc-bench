from __future__ import annotations

import os
import tempfile
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from agent_doc_bench.doc_requirements import build_doc_requirements
from agent_doc_bench.docs_validator import check_draft_content
from mcp_server.github_client import DocsRepoClient, GitHubDocsClient, LocalDocsClient

mcp = FastMCP("agent-doc-bench")


def make_client() -> DocsRepoClient:
    """MCP_DRY_RUN=1 reads a local checkout instead of calling GitHub —
    the mechanism for testing tool logic without live credentials (see
    mcp_server/tests/test_server_dry_run.py). MCP_DRY_RUN_REPO_ROOT points
    it at a specific checkout; defaults to the current working directory.
    """
    if os.environ.get("MCP_DRY_RUN") == "1":
        return LocalDocsClient(os.environ.get("MCP_DRY_RUN_REPO_ROOT", "."))
    return GitHubDocsClient(
        token=os.environ["GITHUB_TOKEN"],
        owner=os.environ["GITHUB_OWNER"],
        repo=os.environ["GITHUB_REPO"],
    )


@mcp.tool()
def list_apis() -> list[str]:
    """List the APIs this benchmark covers (docs_library/ subfolders)."""
    return make_client().list_apis()


@mcp.tool()
def list_experiments() -> list[str]:
    """List available ablation experiments (experiments/*.yaml, by name)."""
    return make_client().list_experiments()


@mcp.tool()
def get_doc_requirements(api: str) -> str:
    """Plain-language reference for what tends to help an agent on <api>'s
    benchmark tasks. Advisory, not a spec — only the code a coding agent
    writes with a doc's help ever gets scored, never the doc's own wording.
    """
    client = make_client()
    with tempfile.TemporaryDirectory() as tmp:
        client.fetch_task_suite(api, Path(tmp))
        return build_doc_requirements(api, base_dir=tmp)


@mcp.tool()
def list_doc_variants(api: str) -> list[str]:
    """List existing named documentation variants for <api> (e.g. none, v1, v2)."""
    return make_client().list_doc_variants(api)


@mcp.tool()
def get_doc_variant(api: str, value: str) -> str:
    """Raw content of an existing documentation variant."""
    return make_client().get_doc_variant(api, value)


@mcp.tool()
def validate_doc_variant(api: str, content: str) -> list[str]:
    """Check a documentation draft for the same missing-guidance/stub issues
    validate-docs flags for named variants — a free, local pre-check before
    spending a real evaluation run. `api` is accepted for symmetry with
    evaluate_doc_draft's signature; the check itself is content-only.
    Returns a list of issue descriptions (empty list means no issues found).
    """
    del api
    return check_draft_content(content)


def main() -> None:
    mcp.run(transport="sse")


if __name__ == "__main__":
    main()
