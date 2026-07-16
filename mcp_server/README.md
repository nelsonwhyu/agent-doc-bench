# agent-doc-bench MCP server

Lets a PM read documentation requirements, browse existing doc variants, and
sanity-check a draft — from Claude or ChatGPT, without a local checkout.
See `IMPLEMENTATION_PLAN.md`'s "Part 2" for the full design and rationale.

**Status: all 9 tools implemented, nothing hosted yet.** Reads, local
validation, and now `evaluate_doc_draft`/`get_evaluation_status`/
`get_evaluation_report` (which dispatch and poll a real ablation run via
GitHub Actions) all work. What's missing is Layer 3: hosting the server over
HTTP/SSE at a stable URL, OAuth in front of it, and registering it as a
connector in Claude Desktop / ChatGPT — today this only runs locally.

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
uv run python mcp_server/server.py   # starts the SSE server on localhost
```

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

- Hosting the server over HTTP/SSE at a stable URL
- OAuth in front of the connector
- Registering the connector in Claude Desktop / ChatGPT
- A real end-to-end smoke test against an actual GitHub Actions run (everything so far is unit-tested against fakes; nothing has dispatched a real workflow yet)
