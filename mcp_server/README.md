# agent-doc-bench MCP server

Lets a PM read documentation requirements, browse existing doc variants, and
sanity-check a draft — from Claude or ChatGPT, without a local checkout.
See `IMPLEMENTATION_PLAN.md`'s "Part 2" for the full design and rationale.

**Status: all 9 tools implemented and hosted.** Reads, local validation, and
`evaluate_doc_draft`/`get_evaluation_status`/`get_evaluation_report` (which
dispatch and poll a real ablation run via GitHub Actions) all work. The
server is deployed on Fly.io (`Dockerfile` + `fly.toml` at the repo root),
reachable over HTTPS with OAuth in front of it, and connectable from Claude
Desktop and (in principle — untested so far) ChatGPT's custom connector UI.
See "Hosting (Fly.io)" and "Connecting a client" below.

One thing that's simpler than the original Layer 3 design called for: PMs
don't get their own Claude/ChatGPT-linked identity through a real OAuth
login. `mcp_server/dev_oauth_provider.py`'s `DevOAuthProvider` implements
just enough OAuth (dynamic client registration + a one-field passphrase
gate) to satisfy the connector handshake Claude/ChatGPT require, backed by a
single shared secret (`MCP_SHARED_SECRET`) rather than per-PM login. Good
enough for a small team sharing one link; not a real identity system — see
"Open risks" in `IMPLEMENTATION_PLAN.md`.

## Setup

```bash
uv sync --group mcp_server   # installs mcp + PyGithub; excluded from a plain `uv sync`
```

## Local / dry-run testing

Set `MCP_DRY_RUN=1` to read from a local checkout instead of calling the
GitHub API — no credentials needed. `MCP_DRY_RUN_REPO_ROOT` points it at a
specific checkout (defaults to the current working directory).

```bash
export MCP_DRY_RUN=1
uv run python mcp_server/server.py   # starts a streamable-http server on localhost:8000
```

Without `MCP_PUBLIC_HOSTNAME` set, this runs with no auth at all (fine for
local testing over plain HTTP — see `mcp_server/tests/test_server_dry_run.py`,
which calls the `@mcp.tool()` functions directly rather than going through a
client). Set `MCP_PUBLIC_HOSTNAME` (and optionally `MCP_SHARED_SECRET`) to
exercise the same OAuth path production uses — see "Hosting (Fly.io)" below.

Run the test suite the same way (this is exactly what
`mcp_server/tests/test_server_dry_run.py` does under the hood):

```bash
uv run pytest mcp_server/tests
```

## Real (GitHub-backed) credentials

Without `MCP_DRY_RUN=1`, the server talks to the real GitHub Contents API and
requires:

| Env var | Purpose |
|---|---|
| `GITHUB_TOKEN` | A fine-grained PAT (or GitHub App installation token) scoped to **this one repo only** — `Contents: Read`, `Actions: Read and write` (to dispatch/poll `evaluate_doc_draft` runs). No `Contents: Write` or `Pull requests` scope of any kind: this server has no code path that can commit to the repo. |
| `GITHUB_OWNER` | Repo owner, e.g. `nelsonwhyu` |
| `GITHUB_REPO` | Repo name, e.g. `agent-doc-bench` |

## Hosting (Fly.io)

The repo root's `Dockerfile` + `fly.toml` deploy this server as a small,
scale-to-zero Fly.io app (`app = "agent-doc-bench-mcp"` in `fly.toml` —
change that if you deploy your own copy). The image only contains
`agent_doc_bench/` + `mcp_server/` code — no `docs_library/`/`task_suites/`
data, since the real (non-dry-run) client reads those from the GitHub
Contents API at request time, not from local disk.

```bash
fly apps create agent-doc-bench-mcp   # one-time, if the app doesn't exist yet

fly secrets set --app agent-doc-bench-mcp \
  GITHUB_TOKEN=<fine-grained PAT, see table above> \
  GITHUB_OWNER=<owner> \
  GITHUB_REPO=<repo> \
  MCP_SHARED_SECRET=<generate one, e.g. `python3 -c "import secrets; print(secrets.token_urlsafe(24))"`>

fly deploy --app agent-doc-bench-mcp
```

`MCP_PUBLIC_HOSTNAME` (in `fly.toml`'s `[env]` block, not a secret — it's
just the app's own hostname) tells the server two things it can't infer on
its own: which `Host` header to accept past FastMCP's DNS-rebinding
protection, and what base URL to advertise in its OAuth metadata
(`issuer_url`/`resource_server_url`). Update it if you rename the app.

Note the `Dockerfile`'s `mcp = FastMCP("agent-doc-bench", host="0.0.0.0", ...)`
in `server.py` — binding only `127.0.0.1` (FastMCP's default) works fine for
local/tunnel testing but is unreachable from Fly's proxy, which connects
from outside the container's network namespace.

### Finding the hosted URL

```bash
fly status --app agent-doc-bench-mcp   # "Hostname" line, e.g. agent-doc-bench-mcp.fly.dev
```

The MCP endpoint a client connects to is that hostname plus `/mcp` (the
`streamable-http` transport's path) — e.g.
`https://agent-doc-bench-mcp.fly.dev/mcp`. A plain `GET /healthz` against
the same hostname (no auth required) confirms the app is up before
troubleshooting a client-side connection issue.

## Connecting a client

Both Claude Desktop and ChatGPT's connector UI require **OAuth dynamic
client registration** before they'll add a remote MCP server — plain
unauthenticated HTTP isn't accepted, and neither is `http://` (only
`https://`). This server satisfies that with `DevOAuthProvider`'s shared-secret
gate (see the note at the top of this file).

**Claude Desktop**: Settings → Connectors → Add custom connector.
- Name: anything
- Remote MCP server URL: `https://<hostname from fly status>/mcp`
- OAuth Client ID / Secret: leave both blank — the server registers a client
  dynamically per connection, so there's nothing to pre-provision
- Click **Add**, then **Connect** — this redirects to a one-field passphrase
  page (`/dev-login`); enter the `MCP_SHARED_SECRET` value

**ChatGPT**: same idea via its custom connector settings, pointed at the
same URL — not yet smoke-tested end to end (see "Not yet implemented"
below), but the OAuth handshake it needs is the same one Claude Desktop
already completes successfully against this server.

**Claude Code CLI**, if you'd rather skip the OAuth dance entirely for your
own local use:
```bash
claude mcp add --transport http agent-doc-bench http://127.0.0.1:8000/mcp
```
(only works against a server run locally with no `MCP_PUBLIC_HOSTNAME` set —
unlike Claude Desktop's/ChatGPT's connector UIs, `claude mcp add` doesn't
require completing an OAuth handshake up front, so it'll happily talk to a
plain unauthenticated local server.)

## Tools implemented

| Tool | Notes |
|---|---|
| `list_apis()` | `docs_library/` subfolders |
| `list_experiments()` | `experiments/*.yaml`, by name |
| `get_doc_requirements(api)` | Plain-language, advisory checklist generated from `task_suites/<api>/*.yaml` — not a spec |
| `list_doc_variants(api)` | Existing named doc variants |
| `get_doc_variant(api, value)` | Raw content of an existing variant |
| `validate_doc_variant(api, content)` | Local empty/stub check on a draft — free, no network write |
| `evaluate_doc_draft(api, value, content, experiment)` | Dispatches `.github/workflows/evaluate-doc-draft.yml` via `repository_dispatch`; returns a run id. Costs real Anthropic + LangSmith usage — call `validate_doc_variant` first. |
| `get_evaluation_status(run_id)` | Polls the run: `queued` / `in_progress` / `completed` |
| `get_evaluation_report(run_id)` | Once completed, returns the Markdown report the workflow rendered and uploaded — same formatter the CLI's `report --format markdown` uses |

In `MCP_DRY_RUN=1` mode, `evaluate_doc_draft` never calls GitHub at all — it
returns a synthetic `dry-run-<hex>` run id, and `get_evaluation_status`/
`get_evaluation_report` recognize that prefix and return canned responses.

## Not yet implemented

- **Per-PM identity.** `MCP_SHARED_SECRET` is one passphrase for everyone
  with the URL, not each PM authenticating as themselves — fine for a small
  trusted team, a gap if this ever needs real access control or per-user
  audit trail.
- **ChatGPT, smoke-tested.** The connector setup steps above should work
  (same OAuth handshake Claude Desktop completes), but no one has actually
  clicked through it in ChatGPT yet.
- **A real end-to-end smoke test against an actual GitHub Actions run.**
  Everything so far is unit-tested against fakes; the one full
  `evaluate_doc_draft` → `get_evaluation_report` round trip through a real
  client (Claude Desktop) was against an earlier `MCP_DRY_RUN=1` server
  behind a temporary tunnel, before Fly hosting existed. The Fly-hosted
  instance is configured with real GitHub credentials (no `MCP_DRY_RUN`),
  but no tool call — dry-run or real — has been made against it yet at all.
- **Always-on availability.** Fly's scale-to-zero means the first request
  after idle cold-starts the machine (a few seconds' delay) — fine for
  occasional PM use, not for anything latency-sensitive.
