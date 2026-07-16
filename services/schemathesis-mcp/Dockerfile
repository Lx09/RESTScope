FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    SCHEMATHESIS_MCP_ALLOWED_PATHS=/workspace \
    SCHEMATHESIS_MCP_ARTIFACT_DIR=/data \
    SCHEMATHESIS_MCP_ARTIFACT_TTL=1h

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --frozen --no-dev

RUN groupadd --system mcp \
    && useradd --system --gid mcp --home-dir /home/mcp --create-home mcp \
    && mkdir -p /workspace /data \
    && chown -R mcp:mcp /app /workspace /data

USER mcp

ENTRYPOINT ["/app/.venv/bin/schemathesis-mcp"]
