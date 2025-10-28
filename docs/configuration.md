# Configuration Guide

This guide details all available configuration options for the Prometheus MCP Server.

## Environment Variables

The server is configured primarily through environment variables. These can be set directly in your environment or through a `.env` file in the project root directory.

### Required Variables

| Variable | Description | Example |
|----------|-------------|--------|
| `PROMETHEUS_URL` | URL of your Prometheus server | `http://prometheus:9090` |

### Optional Variables for SSL

| Variable | Description | Example |
|----------|-------------|--------|
| `PROMETHEUS_URL_SSL_VERIFY` | Set to False to disable SSL verification | `True` |

### Authentication Variables

Prometheus MCP Server supports multiple authentication methods. Choose the appropriate one for your Prometheus setup:

#### Basic Authentication

| Variable | Description | Example |
|----------|-------------|--------|
| `PROMETHEUS_USERNAME` | Username for basic authentication | `admin` |
| `PROMETHEUS_PASSWORD` | Password for basic authentication | `secure_password` |

#### Token Authentication

| Variable | Description | Example |
|----------|-------------|--------|
| `PROMETHEUS_TOKEN` | Bearer token for authentication | `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...` |

## Authentication Priority

If multiple authentication methods are configured, the server will prioritize them in the following order:

1. Bearer token authentication (if `PROMETHEUS_TOKEN` is set)
2. Basic authentication (if both `PROMETHEUS_USERNAME` and `PROMETHEUS_PASSWORD` are set)
3. No authentication (if no credentials are provided)

#### MCP Server Configuration

| Variable | Description | Example |
|----------|-------------|--------|
| `PROMETHEUS_MCP_SERVER_TRANSPORT` | Choose between these transports: `http`, `stdio`, `sse`. If undefined,  `stdio` is set as the default transport. | `http` |
| `PROMETHEUS_MCP_BIND_HOST` | Define the host for your MCP server, if undefined, `127.0.0.1` is set by default. | `localhost` |
| `PROMETHEUS_MCP_BIND_PORT` | Define the port where your MCP server is exposed, if undefined, `8080` is set by default. | `8080` |
| `PROMETHEUS_DISABLE_LINKS` | Disable Prometheus UI links in query results, if undefined, `False` is set by default. | `True` |

## MCP Client Configuration

### Claude Desktop Configuration

To use the Prometheus MCP Server with Claude Desktop, you'll need to add configuration to the Claude Desktop settings:

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

### Docker Configuration with Claude Desktop

If you're using the Docker container with Claude Desktop:

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

## Network Connectivity

Ensure that the environment where the Prometheus MCP Server runs has network access to your Prometheus server. If running in Docker, you might need to adjust network settings or use host networking depending on your setup.

## Troubleshooting

### Connection Issues

If you encounter connection issues:

1. Verify that the `PROMETHEUS_URL` is correct and accessible from the environment where the MCP server runs
2. Check that authentication credentials are correct
3. Ensure no network firewalls are blocking access
4. Verify that your Prometheus server is running and healthy

### Authentication Issues

If you experience authentication problems:

1. Double-check your username and password or token
2. Ensure the authentication method matches what your Prometheus server expects
3. Check Prometheus server logs for authentication failures