# agent-doc-bench MCP server

Lets a PM read documentation requirements, browse existing doc variants, and
sanity-check a draft — from Claude or ChatGPT, without a local checkout.
See `IMPLEMENTATION_PLAN.md`'s "Part 2" for the full design and rationale.

**Status: read-only tools only.** `evaluate_doc_draft` / `get_evaluation_status` /
`get_evaluation_report` (the tools that actually trigger and report back an
ablation run via GitHub Actions) aren't built yet — see Implementation steps
7-9 in the plan. Hosting and OAuth (Layer 3) also aren't set up yet; today
this only runs locally.

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
| `GITHUB_TOKEN` | A fine-grained PAT (or GitHub App installation token) scoped to **this one repo only** — `Contents: Read`. No write scope of any kind: this server has no code path that can commit to the repo. |
| `GITHUB_OWNER` | Repo owner, e.g. `nelsonwhyu` |
| `GITHUB_REPO` | Repo name, e.g. `agent-doc-bench` |

Once `evaluate_doc_draft` lands (implementation step 8), the token will also
need `Actions: Read and write` to dispatch and poll ablation runs — still no
`Contents: Write` or `Pull requests` scope, since evaluation runs never
commit anything (see the plan's Layer 2 design).

## Tools implemented so far

| Tool | Notes |
|---|---|
| `list_apis()` | `docs_library/` subfolders |
| `list_experiments()` | `experiments/*.yaml`, by name |
| `get_doc_requirements(api)` | Plain-language, advisory checklist generated from `task_suites/<api>/*.yaml` — not a spec |
| `list_doc_variants(api)` | Existing named doc variants |
| `get_doc_variant(api, value)` | Raw content of an existing variant |
| `validate_doc_variant(api, content)` | Local empty/stub check on a draft — free, no network write |

## Not yet implemented

- `evaluate_doc_draft`, `get_evaluation_status`, `get_evaluation_report` (need `.github/workflows/evaluate-doc-draft.yml` + `mcp_server/actions_client.py` first)
- Hosting the server over HTTP/SSE at a stable URL
- OAuth in front of the connector
- Registering the connector in Claude Desktop / ChatGPT
