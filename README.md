# Prometheus MCP Server

A [Model Context Protocol][mcp] (MCP) server for Prometheus.

![GitHub Container Registry](https://img.shields.io/badge/ghcr.io-pab1it0%2Fprometheus--mcp--server-blue?logo=docker)
![GitHub Release](https://img.shields.io/github/v/release/pab1it0/prometheus-mcp-server)
![Codecov](https://codecov.io/gh/pab1it0/prometheus-mcp-server/branch/main/graph/badge.svg)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/github/license/pab1it0/prometheus-mcp-server)

This provides access to your Prometheus metrics and queries through standardized MCP interfaces, allowing AI assistants to execute PromQL queries and analyze your metrics data.

<a href="https://glama.ai/mcp/servers/@pab1it0/prometheus-mcp-server">
  <img width="380" height="200" src="https://glama.ai/mcp/servers/@pab1it0/prometheus-mcp-server/badge" alt="Prometheus Server MCP server" />
</a>

[mcp]: https://modelcontextprotocol.io

## Features

- [x] Execute PromQL queries against Prometheus
- [x] Discover and explore metrics
  - [x] List available metrics
  - [x] Get metadata for specific metrics
  - [x] View instant query results
  - [x] View range query results with different step intervals
- [x] Authentication support
  - [x] Basic auth from environment variables
  - [x] Bearer token auth from environment variables
- [x] Docker containerization support

- [x] Provide interactive tools for AI assistants

The list of tools is configurable, so you can choose which tools you want to make available to the MCP client.
This is useful if you don't use certain functionality or if you don't want to take up too much of the context window.

## Getting Started

### Prerequisites

- Prometheus server accessible from your environment
- Docker Desktop (recommended) or Docker CLI
- MCP-compatible client (Claude Desktop, VS Code, Cursor, Windsurf, etc.)

### Configuration

Create a `.env` file or set environment variables for your Prometheus connection:

```env
# Required: Your Prometheus server URL
PROMETHEUS_URL=http://your-prometheus-server:9090

# Optional: Authentication (choose one method if needed)
# Basic auth:
PROMETHEUS_USERNAME=your_username
PROMETHEUS_PASSWORD=your_password

# Or Bearer token:
PROMETHEUS_TOKEN=your_token

# Optional: For multi-tenant setups (Cortex, Mimir, Thanos)
ORG_ID=your_organization_id
```

#### Common Configuration Examples

<details>
<summary><b>Local Prometheus (no auth)</b></summary>

```env
PROMETHEUS_URL=http://localhost:9090
```
</details>

<details>
<summary><b>Prometheus with Basic Auth</b></summary>

```env
PROMETHEUS_URL=https://prometheus.example.com
PROMETHEUS_USERNAME=admin
PROMETHEUS_PASSWORD=secretpassword
```
</details>

<details>
<summary><b>Grafana Cloud Prometheus</b></summary>

```env
PROMETHEUS_URL=https://prometheus-prod-us-central.grafana.net
PROMETHEUS_USERNAME=123456
PROMETHEUS_PASSWORD=glc_eyJvIjoiMTIzNDU2IiwibiI6InN0YWNrLTEyMzQ1Ni1obS1yZWFkLXRva2VuIiwiayI6IjEyMzQ1Njc4OTAifQ==
```
</details>

<details>
<summary><b>Cortex/Mimir with Org ID</b></summary>

```env
PROMETHEUS_URL=https://mimir.example.com/prometheus
PROMETHEUS_TOKEN=Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
ORG_ID=company-dev
```
</details>

### Installation Methods

<details>
<summary><b>Docker Desktop</b></summary>

The easiest way to run the Prometheus MCP server is through Docker Desktop:

1. **Via MCP Catalog**: Visit the [Prometheus MCP Server on Docker Hub](https://hub.docker.com/mcp/server/prometheus-mcp-server) and click "Add to Docker Desktop" (when available)
   
2. **Via MCP Toolkit**: Use Docker Desktop's MCP Toolkit extension to discover and install the server

3. Configure your connection using the environment variables from the Configuration section above

</details>

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

### Configuration Options

| Variable | Description | Required |
|----------|-------------|----------|
| `PROMETHEUS_URL` | URL of your Prometheus server | Yes |
| `PROMETHEUS_USERNAME` | Username for basic authentication | No |
| `PROMETHEUS_PASSWORD` | Password for basic authentication | No |
| `PROMETHEUS_TOKEN` | Bearer token for authentication | No |
| `ORG_ID` | Organization ID for multi-tenant setups | No |
| `PROMETHEUS_MCP_SERVER_TRANSPORT` | Transport mode (stdio, http, sse) | No (default: stdio) |
| `PROMETHEUS_MCP_BIND_HOST` | Host for HTTP transport | No (default: 127.0.0.1) |
| `PROMETHEUS_MCP_BIND_PORT` | Port for HTTP transport | No (default: 8080) |


## Development

Contributions are welcome! Please open an issue or submit a pull request if you have any suggestions or improvements.

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

### Tools

| Tool | Category | Description |
| --- | --- | --- |
| `execute_query` | Query | Execute a PromQL instant query against Prometheus |
| `execute_range_query` | Query | Execute a PromQL range query with start time, end time, and step interval |
| `list_metrics` | Discovery | List all available metrics in Prometheus |
| `get_metric_metadata` | Discovery | Get metadata for a specific metric |
| `get_targets` | Discovery | Get information about all scrape targets |

## License

MIT

---

[mcp]: https://modelcontextprotocol.io