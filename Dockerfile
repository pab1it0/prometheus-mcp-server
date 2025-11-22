FROM python:3.14-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml ./
COPY uv.lock ./

COPY src ./src/

RUN uv venv && \
    uv sync --frozen --no-dev && \
    uv pip install -e . --no-deps && \
    uv pip install --upgrade pip setuptools

FROM python:3.14-slim-bookworm

WORKDIR /app

RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
        curl \
        procps \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/* && \
    apt-get clean && \
    apt-get autoremove -y

RUN groupadd -r -g 1000 app && \
    useradd -r -g app -u 1000 -d /app -s /bin/false app && \
    chown -R app:app /app && \
    chmod 755 /app && \
    chmod -R go-w /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/src /app/src
COPY --chown=app:app pyproject.toml /app/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="/app" \
    PYTHONFAULTHANDLER=1 \
    PROMETHEUS_MCP_BIND_HOST=0.0.0.0 \
    PROMETHEUS_MCP_BIND_PORT=8080

USER app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD if [ "$PROMETHEUS_MCP_SERVER_TRANSPORT" = "http" ] || [ "$PROMETHEUS_MCP_SERVER_TRANSPORT" = "sse" ]; then \
            curl -f http://localhost:${PROMETHEUS_MCP_BIND_PORT}/ >/dev/null 2>&1 || exit 1; \
        else \
            pgrep -f prometheus-mcp-server >/dev/null 2>&1 || exit 1; \
        fi

CMD ["/app/.venv/bin/prometheus-mcp-server"]

LABEL org.opencontainers.image.title="Prometheus MCP Server" \
      org.opencontainers.image.description="Model Context Protocol server for Prometheus integration, enabling AI assistants to query metrics and monitor system health" \
      org.opencontainers.image.version="1.5.1" \
      org.opencontainers.image.authors="Pavel Shklovsky <pavel@cloudefined.com>" \
      org.opencontainers.image.source="https://github.com/pab1it0/prometheus-mcp-server" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.url="https://github.com/pab1it0/prometheus-mcp-server" \
      org.opencontainers.image.documentation="https://github.com/pab1it0/prometheus-mcp-server/blob/main/docs/" \
      org.opencontainers.image.vendor="Pavel Shklovsky" \
      org.opencontainers.image.base.name="python:3.12-slim-bookworm" \
      org.opencontainers.image.created="" \
      org.opencontainers.image.revision="" \
      io.modelcontextprotocol.server.name="io.github.pab1it0/prometheus-mcp-server" \
      mcp.server.name="prometheus-mcp-server" \
      mcp.server.category="monitoring" \
      mcp.server.tags="prometheus,monitoring,metrics,observability" \
      mcp.server.transport.stdio="true" \
      mcp.server.transport.http="true" \
      mcp.server.transport.sse="true"