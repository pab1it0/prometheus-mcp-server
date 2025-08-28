"""Tests for the Prometheus MCP server functionality."""

import pytest
from unittest.mock import patch, MagicMock
from prometheus_mcp_server.server import make_prometheus_request, get_prometheus_auth, config

@pytest.fixture
def mock_response():
    """Create a mock response object for requests."""
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    mock.json.return_value = {
        "status": "success", 
        "data": {
            "resultType": "vector",
            "result": []
        }
    }
    return mock

@patch("prometheus_mcp_server.server.requests.get")
def test_make_prometheus_request_no_auth(mock_get, mock_response):
    """Test making a request to Prometheus with no authentication."""
    # Setup
    mock_get.return_value = mock_response
    config.url = "http://test:9090"
    config.username = ""
    config.password = ""
    config.token = ""

    # Execute
    result = make_prometheus_request("query", {"query": "up"})

    # Verify
    mock_get.assert_called_once()
    assert result == {"resultType": "vector", "result": []}

@patch("prometheus_mcp_server.server.requests.get")
def test_make_prometheus_request_with_basic_auth(mock_get, mock_response):
    """Test making a request to Prometheus with basic authentication."""
    # Setup
    mock_get.return_value = mock_response
    config.url = "http://test:9090"
    config.username = "user"
    config.password = "pass"
    config.token = ""

    # Execute
    result = make_prometheus_request("query", {"query": "up"})

    # Verify
    mock_get.assert_called_once()
    assert result == {"resultType": "vector", "result": []}

@patch("prometheus_mcp_server.server.requests.get")
def test_make_prometheus_request_with_token_auth(mock_get, mock_response):
    """Test making a request to Prometheus with token authentication."""
    # Setup
    mock_get.return_value = mock_response
    config.url = "http://test:9090"
    config.username = ""
    config.password = ""
    config.token = "token123"

    # Execute
    result = make_prometheus_request("query", {"query": "up"})

    # Verify
    mock_get.assert_called_once()
    assert result == {"resultType": "vector", "result": []}

@patch("prometheus_mcp_server.server.requests.get")
def test_make_prometheus_request_error(mock_get):
    """Test handling of an error response from Prometheus."""
    # Setup
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"status": "error", "error": "Test error"}
    mock_get.return_value = mock_response
    config.url = "http://test:9090"

    # Execute and verify
    with pytest.raises(ValueError, match="Prometheus API error: Test error"):
        make_prometheus_request("query", {"query": "up"})
