"""Tests for the main module."""

import os
import pytest
from unittest.mock import patch, MagicMock
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

    # Execute
    result = setup_environment()

    # Verify
    assert result is True

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
    mock_run.assert_called_once_with(transport="stdio")
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
    mock_exit.assert_called_once_with(1)
