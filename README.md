[![MseeP.ai Security Assessment Badge](https://mseep.net/pr/pab1it0-prometheus-mcp-server-badge.png)](https://mseep.ai/app/pab1it0-prometheus-mcp-server)

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

1. Ensure your Prometheus server is accessible from the environment where you'll run this MCP server.

2. Configure the environment variables for your Prometheus server, either through a `.env` file or system environment variables:

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

3. Add the server configuration to your client configuration file. For example, for Claude Desktop:

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
      ],
      "env": {
        "PROMETHEUS_URL": "http://your-prometheus-server:9090",
        "PROMETHEUS_USERNAME": "your_username",
        "PROMETHEUS_PASSWORD": "your_password"
      }
    }
  }
}
```

> Note: if you see `Error: spawn uv ENOENT` in Claude Desktop, you may need to specify the full path to `uv` or set the environment variable `NO_UV=1` in the configuration.

## Docker Usage

This project includes Docker support for easy deployment and isolation.

### Building the Docker Image

Build the Docker image using:

```bash
docker build -t prometheus-mcp-server .
```

### Running with Docker

You can run the server using Docker in several ways:

#### Using docker run directly:

```bash
docker run -it --rm \
  -e PROMETHEUS_URL=http://your-prometheus-server:9090 \
  -e PROMETHEUS_USERNAME=your_username \
  -e PROMETHEUS_PASSWORD=your_password \
  prometheus-mcp-server
```

#### Using docker-compose:

Create a `.env` file with your Prometheus credentials and then run:

```bash
docker-compose up
```

### Running with Docker in Claude Desktop

To use the containerized server with Claude Desktop, update the configuration to use Docker with the environment variables:

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
        "prometheus-mcp-server"
      ],
      "env": {
        "PROMETHEUS_URL": "http://your-prometheus-server:9090",
        "PROMETHEUS_USERNAME": "your_username",
        "PROMETHEUS_PASSWORD": "your_password"
      }
    }
  }
}
```

This configuration passes the environment variables from Claude Desktop to the Docker container by using the `-e` flag with just the variable name, and providing the actual values in the `env` object.

> **Note about Docker implementation**: The Docker setup has been updated to match the structure of the chess-mcp project, which has been proven to work correctly with Claude. The new implementation uses a multi-stage build process and runs the entry point script directly without an intermediary shell script. This approach ensures proper handling of stdin/stdout for MCP communication.

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

## Project Structure

The project has been organized with a `src` directory structure:

```
prometheus-mcp-server/
├── src/
│   └── prometheus_mcp_server/
│       ├── __init__.py      # Package initialization
│       ├── server.py        # MCP server implementation
│       ├── main.py          # Main application logic
├── Dockerfile               # Docker configuration
├── docker-compose.yml       # Docker Compose configuration
├── .dockerignore            # Docker ignore file
├── pyproject.toml           # Project configuration
└── README.md                # This file
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
Tests are organized into:

- Configuration validation tests
- Server functionality tests
- Error handling tests
- Main application tests

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