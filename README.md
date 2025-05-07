# Prometheus MCP Server

A [Model Context Protocol][mcp] (MCP) server for Prometheus.

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

## Usage

### Docker (Recommended)

The easiest way to run prometheus-mcp-server with [Claude Desktop](https://claude.ai/desktop) is using Docker. If you don't have Docker installed, you can get it from [Docker's official website](https://www.docker.com/get-started/).

Edit your Claude Desktop config file:
* Mac: `~/Library/Application Support/Claude/claude_desktop_config.json`
* Windows: `%APPDATA%/Claude/claude_desktop_config.json`
* Linux: `~/.config/Claude/claude_desktop_config.json`

Then add the following configuration:

```json
{
  "mcpServers": {
    "prometheus": {
      "command": "docker",
      "args": [
        "run",
        "--rm",
        "-i",
        "-e", "PROMETHEUS_URL",
        "-e", "PROMETHEUS_USERNAME",
        "-e", "PROMETHEUS_PASSWORD",
        "-e", "PROMETHEUS_TOKEN",
        "pab1it0/prometheus-mcp-server"
      ]
    }
  }
}
```

### Running with UV

Alternatively, you can run the server directly using UV. Edit your Claude Desktop config file (locations listed above) and add the server configuration:

```json
{
  "mcpServers": {
    "prometheus": {
      "command": "uv",
      "args": [
        "--directory",
        "<full path to prometheus-mcp-server directory>",
        "run",
        "src/prometheus_mcp_server/main.py"
      ]
    }
  }
}
```

> Note: if you see `Error: spawn uv ENOENT` in [Claude Desktop](https://claude.ai/desktop), you may need to specify the full path to `uv` or set the environment variable `NO_UV=1` in the configuration.

## Configuration

Ensure your Prometheus server is accessible from the environment where you'll run this MCP server.

Configure the environment variables for your Prometheus server, either through your MCP client configuration or system environment variables:

```env
# Required: Prometheus configuration
PROMETHEUS_URL=http://your-prometheus-server:9090

# Optional: Authentication credentials (if needed)
# Choose one of the following authentication methods if required:

# For basic auth
PROMETHEUS_USERNAME=your_username
PROMETHEUS_PASSWORD=your_password

# For bearer token auth
PROMETHEUS_TOKEN=your_token

# Optional: For multi-tenant setups like Cortex, Mimir or Thanos
ORG_ID=your_organization_id
```

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

The project includes a test suite that ensures functionality and helps prevent regressions.

Run the tests with pytest:

```bash
# Install development dependencies
uv pip install -e ".[dev]"

# Run the tests
pytest

# Run with coverage report
pytest --cov=src --cov-report=term-missing
```

## Available Tools

### Query
- `execute_query` - Execute a PromQL instant query against Prometheus
- `execute_range_query` - Execute a PromQL range query with start time, end time, and step interval

### Discovery
- `list_metrics` - List all available metrics in Prometheus
- `get_metric_metadata` - Get metadata for a specific metric
- `get_targets` - Get information about all scrape targets

## License

MIT

---

[mcp]: https://modelcontextprotocol.io