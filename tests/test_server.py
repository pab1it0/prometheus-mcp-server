"""Tests for the Prometheus MCP server functionality."""

import pytest
import requests
from unittest.mock import patch, MagicMock
import asyncio
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

@patch("prometheus_mcp_server.server.requests.get")
def test_make_prometheus_request_connection_error(mock_get):
    """Test handling of connection errors."""
    # Setup
    mock_get.side_effect = requests.ConnectionError("Connection failed")
    config.url = "http://test:9090"

    # Execute and verify
    with pytest.raises(requests.ConnectionError):
        make_prometheus_request("query", {"query": "up"})

@patch("prometheus_mcp_server.server.requests.get")
def test_make_prometheus_request_timeout(mock_get):
    """Test handling of timeout errors."""
    # Setup
    mock_get.side_effect = requests.Timeout("Request timeout")
    config.url = "http://test:9090"

    # Execute and verify
    with pytest.raises(requests.Timeout):
        make_prometheus_request("query", {"query": "up"})

@patch("prometheus_mcp_server.server.requests.get")
def test_make_prometheus_request_http_error(mock_get):
    """Test handling of HTTP errors."""
    # Setup
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = requests.HTTPError("HTTP 500 Error")
    mock_get.return_value = mock_response
    config.url = "http://test:9090"

    # Execute and verify
    with pytest.raises(requests.HTTPError):
        make_prometheus_request("query", {"query": "up"})

@patch("prometheus_mcp_server.server.requests.get")
def test_make_prometheus_request_json_error(mock_get):
    """Test handling of JSON decode errors."""
    # Setup
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.side_effect = requests.exceptions.JSONDecodeError("Invalid JSON", "", 0)
    mock_get.return_value = mock_response
    config.url = "http://test:9090"

    # Execute and verify
    with pytest.raises(requests.exceptions.JSONDecodeError):
        make_prometheus_request("query", {"query": "up"})

@patch("prometheus_mcp_server.server.requests.get")
def test_make_prometheus_request_pure_json_decode_error(mock_get):
    """Test handling of pure json.JSONDecodeError."""
    import json
    # Setup
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.side_effect = json.JSONDecodeError("Invalid JSON", "", 0)
    mock_get.return_value = mock_response
    config.url = "http://test:9090"

    # Execute and verify - should be converted to ValueError
    with pytest.raises(ValueError, match="Invalid JSON response from Prometheus"):
        make_prometheus_request("query", {"query": "up"})

@patch("prometheus_mcp_server.server.requests.get")
def test_make_prometheus_request_missing_url(mock_get):
    """Test make_prometheus_request with missing URL configuration."""
    # Setup
    original_url = config.url
    config.url = ""  # Simulate missing URL

    # Execute and verify
    with pytest.raises(ValueError, match="Prometheus configuration is missing"):
        make_prometheus_request("query", {"query": "up"})
    
    # Cleanup
    config.url = original_url

@patch("prometheus_mcp_server.server.requests.get")
def test_make_prometheus_request_with_org_id(mock_get, mock_response):
    """Test making a request with org_id header."""
    # Setup
    mock_get.return_value = mock_response
    config.url = "http://test:9090"
    original_org_id = config.org_id
    config.org_id = "test-org"

    # Execute
    result = make_prometheus_request("query", {"query": "up"})

    # Verify
    mock_get.assert_called_once()
    assert result == {"resultType": "vector", "result": []}
    
    # Check that org_id header was included
    call_args = mock_get.call_args
    headers = call_args[1]['headers']
    assert 'X-Scope-OrgID' in headers
    assert headers['X-Scope-OrgID'] == 'test-org'
    
    # Cleanup
    config.org_id = original_org_id

@patch("prometheus_mcp_server.server.requests.get")
def test_make_prometheus_request_request_exception(mock_get):
    """Test handling of generic request exceptions."""
    # Setup
    mock_get.side_effect = requests.exceptions.RequestException("Generic request error")
    config.url = "http://test:9090"

    # Execute and verify
    with pytest.raises(requests.exceptions.RequestException):
        make_prometheus_request("query", {"query": "up"})

@patch("prometheus_mcp_server.server.requests.get") 
def test_make_prometheus_request_response_error(mock_get):
    """Test handling of response errors from Prometheus."""
    # Setup - mock HTTP error response
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = requests.HTTPError("HTTP 500 Server Error")
    mock_response.status_code = 500
    mock_get.return_value = mock_response
    config.url = "http://test:9090"

    # Execute and verify
    with pytest.raises(requests.HTTPError):
        make_prometheus_request("query", {"query": "up"})

@patch("prometheus_mcp_server.server.requests.get")
def test_make_prometheus_request_generic_exception(mock_get):
    """Test handling of unexpected exceptions."""
    # Setup
    mock_get.side_effect = Exception("Unexpected error")
    config.url = "http://test:9090"

    # Execute and verify  
    with pytest.raises(Exception, match="Unexpected error"):
        make_prometheus_request("query", {"query": "up"})

@patch("prometheus_mcp_server.server.requests.get")
def test_make_prometheus_request_list_data_format(mock_get):
    """Test make_prometheus_request with list data format."""
    # Setup - mock response with list data format
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "status": "success", 
        "data": [{"metric": {}, "value": [1609459200, "1"]}]  # List format instead of dict
    }
    mock_get.return_value = mock_response
    config.url = "http://test:9090"

    # Execute
    result = make_prometheus_request("query", {"query": "up"})

    # Verify
    assert result == [{"metric": {}, "value": [1609459200, "1"]}]
