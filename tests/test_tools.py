"""Tests for the MCP tools functionality."""

import pytest
from unittest.mock import patch, MagicMock
from prometheus_mcp_server.server import execute_query, execute_range_query, list_metrics, get_metric_metadata, get_targets

@pytest.fixture
def mock_make_request():
    """Mock the make_prometheus_request function."""
    with patch("prometheus_mcp_server.server.make_prometheus_request") as mock:
        yield mock

@pytest.mark.asyncio
async def test_execute_query(mock_make_request):
    """Test the execute_query tool."""
    # Setup
    mock_make_request.return_value = {
        "resultType": "vector",
        "result": [{"metric": {"__name__": "up"}, "value": [1617898448.214, "1"]}]
    }

    # Execute
    result = await execute_query("up")

    # Verify
    mock_make_request.assert_called_once_with("query", params={"query": "up"})
    assert result["resultType"] == "vector"
    assert len(result["result"]) == 1

@pytest.mark.asyncio
async def test_execute_query_with_time(mock_make_request):
    """Test the execute_query tool with a specified time."""
    # Setup
    mock_make_request.return_value = {
        "resultType": "vector",
        "result": [{"metric": {"__name__": "up"}, "value": [1617898448.214, "1"]}]
    }

    # Execute
    result = await execute_query("up", time="2023-01-01T00:00:00Z")

    # Verify
    mock_make_request.assert_called_once_with("query", params={"query": "up", "time": "2023-01-01T00:00:00Z"})
    assert result["resultType"] == "vector"

@pytest.mark.asyncio
async def test_execute_range_query(mock_make_request):
    """Test the execute_range_query tool."""
    # Setup
    mock_make_request.return_value = {
        "resultType": "matrix",
        "result": [{
            "metric": {"__name__": "up"},
            "values": [
                [1617898400, "1"],
                [1617898415, "1"]
            ]
        }]
    }

    # Execute
    result = await execute_range_query(
        "up", 
        start="2023-01-01T00:00:00Z", 
        end="2023-01-01T01:00:00Z", 
        step="15s"
    )

    # Verify
    mock_make_request.assert_called_once_with("query_range", params={
        "query": "up",
        "start": "2023-01-01T00:00:00Z",
        "end": "2023-01-01T01:00:00Z",
        "step": "15s"
    })
    assert result["resultType"] == "matrix"
    assert len(result["result"]) == 1
    assert len(result["result"][0]["values"]) == 2

@pytest.mark.asyncio
async def test_list_metrics(mock_make_request):
    """Test the list_metrics tool."""
    # Setup
    mock_make_request.return_value = ["up", "go_goroutines", "http_requests_total"]

    # Execute
    result = await list_metrics()

    # Verify
    mock_make_request.assert_called_once_with("label/__name__/values")
    assert result == ["up", "go_goroutines", "http_requests_total"]

@pytest.mark.asyncio
async def test_get_metric_metadata(mock_make_request):
    """Test the get_metric_metadata tool."""
    # Setup
    mock_make_request.return_value = {"metadata": [
        {"metric": "up", "type": "gauge", "help": "Up indicates if the scrape was successful", "unit": ""}
    ]}

    # Execute
    result = await get_metric_metadata("up")

    # Verify
    mock_make_request.assert_called_once_with("metadata", params={"metric": "up"})
    assert len(result) == 1
    assert result[0]["metric"] == "up"
    assert result[0]["type"] == "gauge"

@pytest.mark.asyncio
async def test_get_targets(mock_make_request):
    """Test the get_targets tool."""
    # Setup
    mock_make_request.return_value = {
        "activeTargets": [
            {"discoveredLabels": {"__address__": "localhost:9090"}, "labels": {"job": "prometheus"}, "health": "up"}
        ],
        "droppedTargets": []
    }

    # Execute
    result = await get_targets()

    # Verify
    mock_make_request.assert_called_once_with("targets")
    assert len(result["activeTargets"]) == 1
    assert result["activeTargets"][0]["health"] == "up"
    assert len(result["droppedTargets"]) == 0
