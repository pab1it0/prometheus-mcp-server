# Multi-stage build for optimal image size and security
# Using latest Python 3.12 slim with updated security patches
FROM python:3.12-slim-bookworm AS builder

# Copy uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Optimize Python bytecode compilation
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Copy dependency files first for better Docker layer caching
COPY pyproject.toml ./
COPY uv.lock ./

# Copy source code first for better error handling  
COPY src ./src/

# Install dependencies in virtual environment with security focus
RUN uv venv && \
    uv sync --frozen --no-dev && \
    uv pip install -e . --no-deps && \
    # Update pip and setuptools for security
    uv pip install --upgrade pip setuptools

# Production stage - using latest base image with security updates
FROM python:3.12-slim-bookworm

WORKDIR /app

# Security hardening: update all system packages and install minimal requirements
RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
        curl \
        procps \
        ca-certificates && \
    # Clean up package cache and temporary files for smaller image size
    rm -rf /var/lib/apt/lists/* && \
    apt-get clean && \
    apt-get autoremove -y

# Create non-root user with enhanced security settings
RUN groupadd -r -g 1000 app && \
    useradd -r -g app -u 1000 -d /app -s /bin/false app && \
    chown -R app:app /app && \
    # Additional security: remove unnecessary binaries and secure directories
    chmod 755 /app && \
    # Ensure secure permissions on sensitive directories
    chmod -R go-w /app

# Copy virtual environment and source from builder
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/src /app/src
COPY --chown=app:app pyproject.toml /app/

# Set environment variables for production
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="/app" \
    PYTHONFAULTHANDLER=1 \
    PROMETHEUS_MCP_BIND_HOST=0.0.0.0 \
    PROMETHEUS_MCP_BIND_PORT=8080

# Switch to non-root user
USER app

# Expose port for HTTP transport mode
EXPOSE 8080

# Add health check for container orchestration
# For HTTP transport mode, check if the server is listening
# For stdio mode, check if the process is running
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD if [ "$PROMETHEUS_MCP_SERVER_TRANSPORT" = "http" ] || [ "$PROMETHEUS_MCP_SERVER_TRANSPORT" = "sse" ]; then \
            curl -f http://localhost:${PROMETHEUS_MCP_BIND_PORT}/ >/dev/null 2>&1 || exit 1; \
        else \
            pgrep -f prometheus-mcp-server >/dev/null 2>&1 || exit 1; \
        fi

# Use exec form for better signal handling
CMD ["/app/.venv/bin/prometheus-mcp-server"]

# Enhanced OCI labels for Docker registry compliance
LABEL org.opencontainers.image.title="Prometheus MCP Server" \
      org.opencontainers.image.description="Model Context Protocol server for Prometheus integration, enabling AI assistants to query metrics and monitor system health" \
      org.opencontainers.image.version="1.2.5" \
      org.opencontainers.image.authors="Pavel Shklovsky <pavel@cloudefined.com>" \
      org.opencontainers.image.source="https://github.com/pab1it0/prometheus-mcp-server" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.url="https://github.com/pab1it0/prometheus-mcp-server" \
      org.opencontainers.image.documentation="https://github.com/pab1it0/prometheus-mcp-server/blob/main/docs/" \
      org.opencontainers.image.vendor="Pavel Shklovsky" \
      org.opencontainers.image.base.name="python:3.12-slim-bookworm" \
      org.opencontainers.image.created="" \
      org.opencontainers.image.revision="" \
      mcp.server.name="prometheus-mcp-server" \
      mcp.server.category="monitoring" \
      mcp.server.tags="prometheus,monitoring,metrics,observability" \
      mcp.server.transport.stdio="true" \
      mcp.server.transport.http="true" \
      mcp.server.transport.sse="true"
