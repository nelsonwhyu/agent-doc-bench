from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from agent_doc_bench.doc_requirements import build_doc_requirements
from agent_doc_bench.docs_validator import check_draft_content
from mcp_server.actions_client import ActionsClient
from mcp_server.dev_oauth_provider import DevOAuthProvider
from mcp_server.github_client import DocsRepoClient, GitHubDocsClient, LocalDocsClient


def _transport_security() -> TransportSecuritySettings | None:
    """FastMCP's default DNS-rebinding protection only allows Host headers
    of 127.0.0.1/localhost — anything fronting the server (e.g. a
    cloudflared tunnel) needs its hostname added explicitly, via
    MCP_PUBLIC_HOSTNAME, or every request gets a 421.
    """
    public_host = os.environ.get("MCP_PUBLIC_HOSTNAME")
    if not public_host:
        return None
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", public_host],
        allowed_origins=[
            "http://127.0.0.1:*",
            "http://localhost:*",
            "http://[::1]:*",
            f"https://{public_host}",
        ],
    )


def _auth_settings() -> AuthSettings | None:
    """Claude/ChatGPT's connector UI (unlike Claude Code's `claude mcp add`)
    requires a remote MCP server to support OAuth dynamic client
    registration before it'll add it — set MCP_PUBLIC_HOSTNAME to stand up
    DevOAuthProvider's OAuth server to satisfy that handshake.
    """
    public_host = os.environ.get("MCP_PUBLIC_HOSTNAME")
    if not public_host:
        return None
    base_url = f"https://{public_host}"
    return AuthSettings(
        issuer_url=base_url,
        resource_server_url=base_url,
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=["mcp"],
            default_scopes=["mcp"],
        ),
        revocation_options=RevocationOptions(enabled=True),
    )


_oauth_provider = (
    DevOAuthProvider(shared_secret=os.environ.get("MCP_SHARED_SECRET")) if os.environ.get("MCP_PUBLIC_HOSTNAME") else None
)

mcp = FastMCP(
    "agent-doc-bench",
    # Fly's proxy connects from outside the container's network namespace —
    # the FastMCP default of binding only 127.0.0.1 would be unreachable
    # from there, even though it's fine for local/tunnel testing.
    host="0.0.0.0",
    transport_security=_transport_security(),
    auth_server_provider=_oauth_provider,
    auth=_auth_settings(),
)

DRY_RUN_PREFIX = "dry-run-"


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request) -> Response:
    del request
    return PlainTextResponse("ok")


@mcp.custom_route("/dev-login", methods=["GET"])
async def dev_login_form(request: Request) -> Response:
    pending_id = request.query_params.get("pending", "")
    page = _oauth_provider.render_login_page(pending_id) if _oauth_provider else None
    if page is None:
        return PlainTextResponse("Unknown or expired login attempt", status_code=400)
    return HTMLResponse(page)


@mcp.custom_route("/dev-login", methods=["POST"])
async def dev_login_submit(request: Request) -> Response:
    form = await request.form()
    pending_id = str(form.get("pending", ""))
    secret = str(form.get("secret", ""))
    redirect_url = _oauth_provider.complete_login(pending_id, secret) if _oauth_provider else None
    if redirect_url is None:
        return PlainTextResponse("Incorrect passphrase, or the login attempt expired — go back and reconnect.", status_code=401)
    return RedirectResponse(url=redirect_url, status_code=302)


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


def make_actions_client() -> ActionsClient:
    return ActionsClient(
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


@mcp.tool()
def evaluate_doc_draft(api: str, value: str, content: str, experiment: str) -> dict:
    """Runs a real ablation with this draft substituted in for <value>, via
    a GitHub Actions workflow — nothing is ever committed to the repo.
    Takes minutes; this call returns as soon as the run has started, not
    when it finishes. Returns a run_id — pass it to get_evaluation_status,
    then get_evaluation_report once status is "completed". Costs real
    Anthropic + LangSmith usage — call validate_doc_variant first, it's
    free and local.
    """
    if os.environ.get("MCP_DRY_RUN") == "1":
        run_id = f"{DRY_RUN_PREFIX}{uuid.uuid4().hex[:8]}"
        return {
            "run_id": run_id,
            "note": f"MCP_DRY_RUN=1 — would dispatch evaluate-doc-draft for experiment={experiment!r}, "
            f"api={api!r}, value={value!r}. No real GitHub Actions run was started.",
        }

    run_id = str(make_actions_client().dispatch_evaluation(api, value, content, experiment))
    return {"run_id": run_id}


@mcp.tool()
def get_evaluation_status(run_id: str) -> dict:
    """Poll a run started by evaluate_doc_draft. status is one of
    queued/in_progress/completed."""
    if run_id.startswith(DRY_RUN_PREFIX):
        return {"run_id": run_id, "status": "completed", "conclusion": "success"}

    run = make_actions_client().get_status(int(run_id))
    return {"run_id": run.run_id, "status": run.status, "conclusion": run.conclusion}


@mcp.tool()
def get_evaluation_report(run_id: str) -> str:
    """Once get_evaluation_status shows status="completed", fetch the
    scored report as Markdown — the same summary + per-task detail table
    `agent-doc-bench report --format markdown` renders locally.
    """
    if run_id.startswith(DRY_RUN_PREFIX):
        return "MCP_DRY_RUN=1 — no real report; this is where the rendered results table would appear."

    return make_actions_client().get_report(int(run_id))


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
