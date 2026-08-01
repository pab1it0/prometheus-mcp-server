# Prometheus MCP Server

[![GitHub Container Registry](https://img.shields.io/badge/ghcr.io-pab1it0%2Fprometheus--mcp--server-blue?logo=docker)](https://github.com/users/pab1it0/packages/container/package/prometheus-mcp-server)
[![Helm Chart](https://img.shields.io/badge/helm%20chart-ghcr.io-blue?logo=helm)](https://github.com/pab1it0/prometheus-mcp-server/pkgs/container/charts%2Fprometheus-mcp-server)
[![GitHub Release](https://img.shields.io/github/v/release/pab1it0/prometheus-mcp-server)](https://github.com/pab1it0/prometheus-mcp-server/releases)
[![Codecov](https://codecov.io/gh/pab1it0/prometheus-mcp-server/branch/main/graph/badge.svg)](https://codecov.io/gh/pab1it0/prometheus-mcp-server)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
[![License](https://img.shields.io/github/license/pab1it0/prometheus-mcp-server)](https://github.com/pab1it0/prometheus-mcp-server/blob/main/LICENSE)

Give AI assistants the power to query your Prometheus metrics.

A [Model Context Protocol][mcp] (MCP) server that provides access to your Prometheus metrics and queries through standardized MCP interfaces, allowing AI assistants to execute PromQL queries and analyze your metrics data.

[mcp]: https://modelcontextprotocol.io

## Getting Started

### Prerequisites

- Prometheus server accessible from your environment
- MCP-compatible client (Claude Desktop, VS Code, Cursor, Windsurf, etc.)

### Installation Methods

<details>
<summary><b>Claude Desktop</b></summary>

Add to your Claude Desktop configuration:

```json
{
  "mcpServers": {
    "prometheus": {
      "command": "docker",
      "args": [
        "run",
        "-i",
        "--rm",
        "-e",
        "PROMETHEUS_URL",
        "ghcr.io/pab1it0/prometheus-mcp-server:latest"
      ],
      "env": {
        "PROMETHEUS_URL": "<your-prometheus-url>"
      }
    }
  }
}
```
</details>

<details>
<summary><b>Claude Code</b></summary>

Install via the Claude Code CLI:

```bash
claude mcp add prometheus --env PROMETHEUS_URL=http://your-prometheus:9090 -- docker run -i --rm -e PROMETHEUS_URL ghcr.io/pab1it0/prometheus-mcp-server:latest
```
</details>

<details>
<summary><b>VS Code / Cursor / Windsurf</b></summary>

Add to your MCP settings in the respective IDE:

```json
{
  "prometheus": {
    "command": "docker",
    "args": [
      "run",
      "-i",
      "--rm",
      "-e",
      "PROMETHEUS_URL",
      "ghcr.io/pab1it0/prometheus-mcp-server:latest"
    ],
    "env": {
      "PROMETHEUS_URL": "<your-prometheus-url>"
    }
  }
}
```
</details>

<details>
<summary><b>Docker Desktop</b></summary>

The easiest way to run the Prometheus MCP server is through Docker Desktop:

<a href="https://hub.docker.com/open-desktop?url=https://open.docker.com/dashboard/mcp/servers/id/prometheus/config?enable=true">
  <img src="https://img.shields.io/badge/+%20Add%20to-Docker%20Desktop-2496ED?style=for-the-badge&logo=docker&logoColor=white" alt="Add to Docker Desktop" />
</a>

1. **Via MCP Catalog**: Visit the [Prometheus MCP Server on Docker Hub](https://hub.docker.com/mcp/server/prometheus/overview) and click the button above

2. **Via MCP Toolkit**: Use Docker Desktop's MCP Toolkit extension to discover and install the server

3. Configure your connection using environment variables (see Configuration Options below)

</details>

<details>
<summary><b>Manual Docker Setup</b></summary>

Run directly with Docker:

```bash
# With environment variables
docker run -i --rm \
  -e PROMETHEUS_URL="http://your-prometheus:9090" \
  ghcr.io/pab1it0/prometheus-mcp-server:latest

# With authentication
docker run -i --rm \
  -e PROMETHEUS_URL="http://your-prometheus:9090" \
  -e PROMETHEUS_USERNAME="admin" \
  -e PROMETHEUS_PASSWORD="password" \
  ghcr.io/pab1it0/prometheus-mcp-server:latest
```
</details>

<details>
<summary><b>Helm Chart (Kubernetes)</b></summary>

Deploy to Kubernetes using the Helm chart from the OCI registry:

```bash
helm install prometheus-mcp-server \
  oci://ghcr.io/pab1it0/charts/prometheus-mcp-server \
  --version 1.0.0 \
  --set prometheus.url="http://prometheus:9090"
```

With authentication:

```bash
helm install prometheus-mcp-server \
  oci://ghcr.io/pab1it0/charts/prometheus-mcp-server \
  --version 1.0.0 \
  --set prometheus.url="http://prometheus:9090" \
  --set auth.username="admin" \
  --set auth.password="secret"
```

With a custom values file:

```bash
helm install prometheus-mcp-server \
  oci://ghcr.io/pab1it0/charts/prometheus-mcp-server \
  --version 1.0.0 \
  -f values.yaml
```

See the [chart values](charts/prometheus-mcp-server/values.yaml) for all available configuration options.
</details>

### Configuration Options

| Variable | Description | Required |
|----------|-------------|----------|
| `PROMETHEUS_URL` | URL of your Prometheus server | Yes |
| `PROMETHEUS_URL_SSL_VERIFY` | Set to False to disable SSL verification | No |
| `PROMETHEUS_DISABLE_LINKS` | Set to True to disable Prometheus UI links in query results (saves context tokens) | No |
| `PROMETHEUS_REQUEST_TIMEOUT` | Request timeout in seconds to prevent hanging requests (DDoS protection) | No (default: 30) |
| `PROMETHEUS_USERNAME` | Username for basic authentication | No |
| `PROMETHEUS_PASSWORD` | Password for basic authentication | No |
| `PROMETHEUS_TOKEN` | Bearer token for authentication | No |
| `PROMETHEUS_CLIENT_CERT` | Path to client certificate file for mutual TLS authentication | No |
| `PROMETHEUS_CLIENT_KEY` | Path to client private key file for mutual TLS authentication | No |
| `REQUESTS_CA_BUNDLE` | Path to CA bundle file for verifying the server's TLS certificate (standard `requests` library env var) | No |
| `ORG_ID` | Organization ID for multi-tenant setups, sent as `X-Scope-OrgID`. Always wins over a per-call `org_id` tool argument unless `PROMETHEUS_MCP_ALLOW_ORG_ID_OVERRIDE` is set | No |
| `PROMETHEUS_MCP_SERVER_TRANSPORT` | Transport mode (stdio, http, sse) | No (default: stdio) |
| `PROMETHEUS_MCP_BIND_HOST` | Host for HTTP transport | No (default: 127.0.0.1) |
| `PROMETHEUS_MCP_BIND_PORT` | Port for HTTP transport | No (default: 8080) |
| `PROMETHEUS_MCP_STATELESS_HTTP` | Enable stateless HTTP mode for multi-replica support | No (default: False) |
| `PROMETHEUS_CUSTOM_HEADERS` | Custom headers as JSON string | No |
| `TOOL_PREFIX` | Prefix for all tool names (e.g., `staging` results in `staging_execute_query`). Useful for running multiple instances targeting different environments in Cursor | No |
| `PROMETHEUS_MCP_SPEC_2026` | Master switch for the MCP 2026-07-28 protocol layer | No (default: True) |
| `PROMETHEUS_MCP_CACHE_TTL_MS` | Cache lifetime advertised as `ttlMs` on cacheable results | No (default: 300000) |
| `PROMETHEUS_MCP_CACHE_SCOPE` | Cache scope advertised on cacheable results (`public` or `private`) | No (default: public) |
| `PROMETHEUS_MCP_STRICT_HEADERS` | Reject an HTTP request whose `Mcp-*` headers contradict its JSON-RPC body (HTTP 400, code `-32020`) | No (default: False) |
| `PROMETHEUS_MCP_ALLOW_ORG_ID_OVERRIDE` | Allow a per-call `org_id` tool argument to override a configured `ORG_ID` | No (default: False) |

## Available Tools

| Tool | Category | Description |
| --- | --- | --- |
| `health_check` | System | Health check endpoint for container monitoring and status verification |
| `execute_query` | Query | Execute a PromQL instant query against Prometheus |
| `execute_range_query` | Query | Execute a PromQL range query with start time, end time, and step interval |
| `list_metrics` | Discovery | List all available metrics in Prometheus with pagination and filtering support |
| `get_metric_metadata` | Discovery | Get metadata for one metric or bulk metadata with optional filtering |
| `get_targets` | Discovery | Get information about all scrape targets |
| `server_discover` | System | Return the server's MCP 2026-07-28 discovery document |

The list of tools is configurable, so you can choose which tools you want to make available to the MCP client. This is useful if you don't use certain functionality or if you don't want to take up too much of the context window.

## MCP 2026-07-28 Support

The server accepts `2026-07-28`, `2025-11-25` and `2025-06-18`. Support for the newest
revision is a compatibility layer on top of the MCP SDK (which implements `2025-11-25`),
so it is entirely additive — clients on older revisions see the server unchanged.

- **`server/discover`** is reachable three ways: as a JSON-RPC method on every transport,
  as `GET /server/discover` (the only pre-handshake route, alongside `/health`), and as the
  `server_discover` tool.
- **Result envelope** — every result carries `resultType` and
  `_meta["io.modelcontextprotocol/serverInfo"]`. Cacheable results (`server/discover`,
  `tools/list`, `prompts/list`, `resources/list`, `resources/templates/list`,
  `resources/read`) also carry `ttlMs` and `cacheScope`; `tools/call` does not.
- **Deterministic ordering** — `tools/list` returns tools sorted by name, so clients get
  stable prompt-cache keys.
- **Per-request negotiation** — `params._meta` may carry
  `io.modelcontextprotocol/protocolVersion`, `clientCapabilities`, `clientInfo` and
  `logLevel`. An unrecognised protocol version is rejected with JSON-RPC code `-32022`;
  a request that declares none is treated as legacy and served normally.
- **Trace context** — `traceparent`, `tracestate` and `baggage` in `params._meta` are
  validated against W3C Trace Context and forwarded as headers on the outbound Prometheus
  request. Malformed values are dropped, and `PROMETHEUS_CUSTOM_HEADERS` always takes
  precedence over per-request values.
- **`x-mcp-header`** — `execute_query` annotates its optional `org_id` parameter with
  `x-mcp-header: Org-Id`, so the tenant may also be sent as the `Mcp-Param-Org-Id` header.
  The value is sent to Prometheus as `X-Scope-OrgID`. **A configured `ORG_ID` always
  wins**: `X-Scope-OrgID` is the only tenancy boundary in Mimir/Cortex/Thanos and the
  caller is typically an LLM acting on untrusted content, so a client-supplied tenant is
  only honoured when no `ORG_ID` is configured, or when the operator explicitly sets
  `PROMETHEUS_MCP_ALLOW_ORG_ID_OVERRIDE=true`. Values that are not safe plain ASCII (CR,
  LF, non-ASCII) are refused before they reach an outbound header.
- **Strict header validation** — set `PROMETHEUS_MCP_STRICT_HEADERS=true` to reject an
  HTTP request whose `Mcp-*` headers contradict its JSON-RPC body, with HTTP 400 and code
  `-32020`. The `Mcp-*` headers are optional hints, so omitting one is never an error and
  clients that send none are unaffected; only a header that actually disagrees with the
  body is rejected. `MCP-Protocol-Version` is a transport header rather than a body
  mirror and is only compared when the body declares a pre-2026 revision.

Set `PROMETHEUS_MCP_SPEC_2026=false` to turn the whole layer off.

## Features

- Execute PromQL queries against Prometheus
- Discover and explore metrics
  - List available metrics
  - Get metadata for specific metrics
  - Search metric metadata by name or description in a single call
  - View instant query results
  - View range query results with different step intervals
- Authentication support
  - Basic auth from environment variables
  - Bearer token auth from environment variables
- Docker containerization support
- Provide interactive tools for AI assistants

## Development

Contributions are welcome! Please see our [Contributing Guide](CONTRIBUTING.md) for detailed information on how to get started, coding standards, and the pull request process.

This project uses [`uv`](https://github.com/astral-sh/uv) to manage dependencies. Install `uv` following the instructions for your platform:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

You can then create a virtual environment and install the dependencies with:

```bash
uv venv
source .venv/bin/activate  # On Unix/macOS
.venv\Scripts\activate     # On Windows
uv pip install -e .
```

### Testing

The project includes a comprehensive test suite that ensures functionality and helps prevent regressions.

Run the tests with pytest:

```bash
# Install development dependencies
uv pip install -e ".[dev]"

# Run the tests
pytest

# Run with coverage report
pytest --cov=src --cov-report=term-missing
```

When adding new features, please also add corresponding tests.

## License

MIT

---
