"""Tests for the MCP tools functionality."""

import pytest
import json
from unittest.mock import patch, MagicMock
from fastmcp import Client
from prometheus_mcp_server.server import mcp, execute_query, execute_range_query, list_metrics, get_metric_metadata, get_targets

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

    async with Client(mcp) as client:
        # Execute
        result = await client.call_tool("execute_query", {"query":"up"})

        # Verify
        mock_make_request.assert_called_once_with("query", params={"query": "up"})
        assert result.data["resultType"] == "vector"
        assert len(result.data["result"]) == 1
        # Verify resource links are included (MCP 2025 feature)
        assert "links" in result.data
        assert len(result.data["links"]) > 0
        assert result.data["links"][0]["rel"] == "prometheus-ui"

@pytest.mark.asyncio
async def test_execute_query_with_time(mock_make_request):
    """Test the execute_query tool with a specified time."""
    # Setup
    mock_make_request.return_value = {
        "resultType": "vector",
        "result": [{"metric": {"__name__": "up"}, "value": [1617898448.214, "1"]}]
    }

    async with Client(mcp) as client:
        # Execute
        result = await client.call_tool("execute_query", {"query":"up", "time":"2023-01-01T00:00:00Z"})
        
        # Verify
        mock_make_request.assert_called_once_with("query", params={"query": "up", "time": "2023-01-01T00:00:00Z"})
        assert result.data["resultType"] == "vector"

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

    async with Client(mcp) as client:
        # Execute
        result = await client.call_tool(
            "execute_range_query",{
            "query": "up",
            "start": "2023-01-01T00:00:00Z",
            "end": "2023-01-01T01:00:00Z",
            "step": "15s"
        })

        # Verify
        mock_make_request.assert_called_once_with("query_range", params={
            "query": "up",
            "start": "2023-01-01T00:00:00Z",
            "end": "2023-01-01T01:00:00Z",
            "step": "15s"
        })
        assert result.data["resultType"] == "matrix"
        assert len(result.data["result"]) == 1
        assert len(result.data["result"][0]["values"]) == 2
        # Verify resource links are included (MCP 2025 feature)
        assert "links" in result.data
        assert len(result.data["links"]) > 0
        assert result.data["links"][0]["rel"] == "prometheus-ui"

@pytest.mark.asyncio
async def test_list_metrics(mock_make_request):
    """Test the list_metrics tool."""
    # Setup
    mock_make_request.return_value = ["up", "go_goroutines", "http_requests_total"]

    async with Client(mcp) as client:
        # Execute - call without pagination
        result = await client.call_tool("list_metrics", {})

        # Verify
        mock_make_request.assert_called_once_with("label/__name__/values")
        # Now returns a dict with pagination info
        assert result.data["metrics"] == ["up", "go_goroutines", "http_requests_total"]
        assert result.data["total_count"] == 3
        assert result.data["returned_count"] == 3
        assert result.data["offset"] == 0
        assert result.data["has_more"] == False

@pytest.mark.asyncio
async def test_list_metrics_with_pagination(mock_make_request):
    """Test the list_metrics tool with pagination."""
    # Setup
    mock_make_request.return_value = ["metric1", "metric2", "metric3", "metric4", "metric5"]

    async with Client(mcp) as client:
        # Execute - call with limit and offset
        result = await client.call_tool("list_metrics", {"limit": 2, "offset": 1})

        # Verify
        mock_make_request.assert_called_once_with("label/__name__/values")
        assert result.data["metrics"] == ["metric2", "metric3"]
        assert result.data["total_count"] == 5
        assert result.data["returned_count"] == 2
        assert result.data["offset"] == 1
        assert result.data["has_more"] == True

@pytest.mark.asyncio
async def test_list_metrics_with_filter(mock_make_request):
    """Test the list_metrics tool with filter pattern."""
    # Setup
    mock_make_request.return_value = ["http_requests_total", "http_response_size", "go_goroutines", "up"]

    async with Client(mcp) as client:
        # Execute - call with filter
        result = await client.call_tool("list_metrics", {"filter_pattern": "http"})

        # Verify
        mock_make_request.assert_called_once_with("label/__name__/values")
        assert result.data["metrics"] == ["http_requests_total", "http_response_size"]
        assert result.data["total_count"] == 2
        assert result.data["returned_count"] == 2
        assert result.data["offset"] == 0
        assert result.data["has_more"] == False

@pytest.mark.asyncio
async def test_get_metric_metadata(mock_make_request):
    """Test the get_metric_metadata tool."""
    # Setup
    mock_make_request.return_value = {"data": [
        {"metric": "up", "type": "gauge", "help": "Up indicates if the scrape was successful", "unit": ""}
    ]}

    async with Client(mcp) as client:
        # Execute
        result = await client.call_tool("get_metric_metadata", {"metric":"up"})

        payload = result.content[0].text
        json_data = json.loads(payload)
        print(json_data)

        # Verify
        mock_make_request.assert_called_once_with("metadata?metric=up", params=None)
        assert len(json_data) == 1
        assert json_data[0]["metric"] == "up"
        assert json_data[0]["type"] == "gauge"

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

    async with Client(mcp) as client:
        # Execute
        result = await client.call_tool("get_targets",{})

        payload = result.content[0].text
        json_data = json.loads(payload)

        # Verify
        mock_make_request.assert_called_once_with("targets")
        assert len(json_data["activeTargets"]) == 1
        assert json_data["activeTargets"][0]["health"] == "up"
        assert len(json_data["droppedTargets"]) == 0
