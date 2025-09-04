"""Tests for the main module."""

import os
import pytest
from unittest.mock import patch, MagicMock
from prometheus_mcp_server.server import MCPServerConfig
from prometheus_mcp_server.main import setup_environment, run_server

@patch("prometheus_mcp_server.main.config")
def test_setup_environment_success(mock_config):
    """Test successful environment setup."""
    # Setup
    mock_config.url = "http://test:9090"
    mock_config.username = None
    mock_config.password = None
    mock_config.token = None
    mock_config.org_id = None
    mock_config.mcp_server_config = None

    # Execute
    result = setup_environment()

    # Verify
    assert result is True

@patch("prometheus_mcp_server.main.config")
def test_setup_environment_missing_url(mock_config):
    """Test environment setup with missing URL."""
    # Setup - mock config with no URL
    mock_config.url = ""
    mock_config.username = None
    mock_config.password = None
    mock_config.token = None
    mock_config.org_id = None
    mock_config.mcp_server_config = None

    # Execute
    result = setup_environment()

    # Verify
    assert result is False

@patch("prometheus_mcp_server.main.config")
def test_setup_environment_with_auth(mock_config):
    """Test environment setup with authentication."""
    # Setup
    mock_config.url = "http://test:9090"
    mock_config.username = "user"
    mock_config.password = "pass"
    mock_config.token = None
    mock_config.org_id = None
    mock_config.mcp_server_config = None

    # Execute
    result = setup_environment()

    # Verify
    assert result is True

@patch("prometheus_mcp_server.main.config")
def test_setup_environment_with_custom_mcp_config(mock_config):
    """Test environment setup with custom mcp config."""
    # Setup
    mock_config.url = "http://test:9090"
    mock_config.username = "user"
    mock_config.password = "pass"
    mock_config.token = None
    mock_config.mcp_server_config = MCPServerConfig(
        mcp_server_transport="http",
        mcp_bind_host="localhost",
        mcp_bind_port=5000
    )

    # Execute
    result = setup_environment()

    # Verify
    assert result is True

@patch("prometheus_mcp_server.main.config")
def test_setup_environment_with_custom_mcp_config_caps(mock_config):
    """Test environment setup with custom mcp config."""
    # Setup
    mock_config.url = "http://test:9090"
    mock_config.username = "user"
    mock_config.password = "pass"
    mock_config.token = None
    mock_config.mcp_server_config = MCPServerConfig(
        mcp_server_transport="HTTP",
        mcp_bind_host="localhost",
        mcp_bind_port=5000
    )


    # Execute
    result = setup_environment()

    # Verify
    assert result is True

@patch("prometheus_mcp_server.main.config")
def test_setup_environment_with_undefined_mcp_server_transports(mock_config):
    """Test environment setup with undefined mcp_server_transport."""
    with pytest.raises(ValueError, match="MCP SERVER TRANSPORT is required"):
        mock_config.mcp_server_config = MCPServerConfig(
            mcp_server_transport=None,
            mcp_bind_host="localhost",
            mcp_bind_port=5000
        )

@patch("prometheus_mcp_server.main.config")
def test_setup_environment_with_undefined_mcp_bind_host(mock_config):
    """Test environment setup with undefined mcp_bind_host."""
    with pytest.raises(ValueError, match="MCP BIND HOST is required"):
        mock_config.mcp_server_config = MCPServerConfig(
            mcp_server_transport="http",
            mcp_bind_host=None,
            mcp_bind_port=5000
        )

@patch("prometheus_mcp_server.main.config")
def test_setup_environment_with_undefined_mcp_bind_port(mock_config):
    """Test environment setup with undefined mcp_bind_port."""
    with pytest.raises(ValueError, match="MCP BIND PORT is required"):
        mock_config.mcp_server_config = MCPServerConfig(
            mcp_server_transport="http",
            mcp_bind_host="localhost",
            mcp_bind_port=None
        )        

@patch("prometheus_mcp_server.main.config")
def test_setup_environment_with_bad_mcp_config_transport(mock_config):
    """Test environment setup with bad transport in mcp config."""
    # Setup
    mock_config.url = "http://test:9090"
    mock_config.username = "user"
    mock_config.password = "pass"
    mock_config.token = None
    mock_config.org_id = None
    mock_config.mcp_server_config = MCPServerConfig(
        mcp_server_transport="wrong_transport",
        mcp_bind_host="localhost",
        mcp_bind_port=5000
    )

    # Execute
    result = setup_environment()

    # Verify
    assert result is False

@patch("prometheus_mcp_server.main.config")
def test_setup_environment_with_bad_mcp_config_port(mock_config):
    """Test environment setup with bad port in mcp config."""
    # Setup
    mock_config.url = "http://test:9090"
    mock_config.username = "user"
    mock_config.password = "pass"
    mock_config.token = None
    mock_config.org_id = None
    mock_config.mcp_server_config = MCPServerConfig(
        mcp_server_transport="http",
        mcp_bind_host="localhost",
        mcp_bind_port="some_string"
    )

    # Execute
    result = setup_environment()

    # Verify
    assert result is False

@patch("prometheus_mcp_server.main.setup_environment")
@patch("prometheus_mcp_server.main.mcp.run")
@patch("prometheus_mcp_server.main.sys.exit")
def test_run_server_success(mock_exit, mock_run, mock_setup):
    """Test successful server run."""
    # Setup
    mock_setup.return_value = True

    # Execute
    run_server()

    # Verify
    mock_setup.assert_called_once()
    mock_exit.assert_not_called()

@patch("prometheus_mcp_server.main.setup_environment")
@patch("prometheus_mcp_server.main.mcp.run")
@patch("prometheus_mcp_server.main.sys.exit")
def test_run_server_setup_failure(mock_exit, mock_run, mock_setup):
    """Test server run with setup failure."""
    # Setup
    mock_setup.return_value = False
    # Make sys.exit actually stop execution
    mock_exit.side_effect = SystemExit(1)

    # Execute - should raise SystemExit
    with pytest.raises(SystemExit):
        run_server()

    # Verify
    mock_setup.assert_called_once()
    mock_run.assert_not_called()

@patch("prometheus_mcp_server.main.config")
@patch("prometheus_mcp_server.main.dotenv.load_dotenv")
def test_setup_environment_bearer_token_auth(mock_load_dotenv, mock_config):
    """Test environment setup with bearer token authentication."""
    # Setup
    mock_load_dotenv.return_value = False
    mock_config.url = "http://test:9090"
    mock_config.username = ""
    mock_config.password = ""
    mock_config.token = "bearer_token_123"
    mock_config.org_id = None
    mock_config.mcp_server_config = None

    # Execute
    result = setup_environment()

    # Verify
    assert result is True

@patch("prometheus_mcp_server.main.setup_environment")
@patch("prometheus_mcp_server.main.mcp.run")
@patch("prometheus_mcp_server.main.config")
def test_run_server_http_transport(mock_config, mock_run, mock_setup):
    """Test server run with HTTP transport."""
    # Setup
    mock_setup.return_value = True
    mock_config.mcp_server_config = MCPServerConfig(
        mcp_server_transport="http",
        mcp_bind_host="localhost", 
        mcp_bind_port=8080
    )

    # Execute
    run_server()

    # Verify
    mock_run.assert_called_once_with(transport="http", host="localhost", port=8080)

@patch("prometheus_mcp_server.main.setup_environment")
@patch("prometheus_mcp_server.main.mcp.run")
@patch("prometheus_mcp_server.main.config")
def test_run_server_sse_transport(mock_config, mock_run, mock_setup):
    """Test server run with SSE transport."""
    # Setup
    mock_setup.return_value = True
    mock_config.mcp_server_config = MCPServerConfig(
        mcp_server_transport="sse",
        mcp_bind_host="0.0.0.0",
        mcp_bind_port=9090
    )

    # Execute
    run_server()

    # Verify
    mock_run.assert_called_once_with(transport="sse", host="0.0.0.0", port=9090)
