FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY agent_doc_bench ./agent_doc_bench
COPY mcp_server ./mcp_server

# The mcp_server group is the only thing this image runs — the "live"/"dev"
# default groups pull in blpapi (Bloomberg's private package index, not
# reachable from a generic build) and pytest, neither needed to serve tools.
RUN uv sync --frozen --no-default-groups --group mcp_server

EXPOSE 8000

CMD ["/app/.venv/bin/python", "mcp_server/server.py"]
