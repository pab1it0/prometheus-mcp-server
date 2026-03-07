"""Tests for the MCP tools functionality."""

import pytest
import json
import time
from unittest.mock import patch, MagicMock
from fastmcp import Client
from prometheus_mcp_server.server import (
    mcp, execute_query, execute_range_query, list_metrics, get_metric_metadata, get_targets,
    _metrics_cache, clear_metrics_cache,
    _coerce_metadata_entries, _normalize_metadata_map, _metadata_matches_pattern,
)

@pytest.fixture(autouse=True)
def reset_metrics_cache():
    """Reset metrics cache before each test to prevent state leaking."""
    clear_metrics_cache()
    yield
    clear_metrics_cache()

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

@pytest.fixture
def mock_get_cached_metrics():
    """Mock the get_cached_metrics function."""
    with patch("prometheus_mcp_server.server.get_cached_metrics") as mock:
        yield mock

@pytest.mark.asyncio
async def test_list_metrics(mock_get_cached_metrics):
    """Test the list_metrics tool."""
    # Setup
    mock_get_cached_metrics.return_value = ["up", "go_goroutines", "http_requests_total"]

    async with Client(mcp) as client:
        # Execute - call without pagination
        result = await client.call_tool("list_metrics", {})

        # Verify
        mock_get_cached_metrics.assert_called_once()
        # Now returns a dict with pagination info
        assert result.data["metrics"] == ["up", "go_goroutines", "http_requests_total"]
        assert result.data["total_count"] == 3
        assert result.data["returned_count"] == 3
        assert result.data["offset"] == 0
        assert result.data["has_more"] == False

@pytest.mark.asyncio
async def test_list_metrics_with_pagination(mock_get_cached_metrics):
    """Test the list_metrics tool with pagination."""
    # Setup
    mock_get_cached_metrics.return_value = ["metric1", "metric2", "metric3", "metric4", "metric5"]

    async with Client(mcp) as client:
        # Execute - call with limit and offset
        result = await client.call_tool("list_metrics", {"limit": 2, "offset": 1})

        # Verify
        mock_get_cached_metrics.assert_called_once()
        assert result.data["metrics"] == ["metric2", "metric3"]
        assert result.data["total_count"] == 5
        assert result.data["returned_count"] == 2
        assert result.data["offset"] == 1
        assert result.data["has_more"] == True

@pytest.mark.asyncio
async def test_list_metrics_with_filter(mock_get_cached_metrics):
    """Test the list_metrics tool with filter pattern."""
    # Setup
    mock_get_cached_metrics.return_value = ["http_requests_total", "http_response_size", "go_goroutines", "up"]

    async with Client(mcp) as client:
        # Execute - call with filter
        result = await client.call_tool("list_metrics", {"filter_pattern": "http"})

        # Verify
        mock_get_cached_metrics.assert_called_once()
        assert result.data["metrics"] == ["http_requests_total", "http_response_size"]
        assert result.data["total_count"] == 2
        assert result.data["returned_count"] == 2
        assert result.data["offset"] == 0
        assert result.data["has_more"] == False

@pytest.mark.asyncio
async def test_list_metrics_refresh_cache(mock_get_cached_metrics):
    """Test that refresh_cache=True invalidates the cache before fetching."""
    # Pre-populate cache to simulate a warm cache
    _metrics_cache["data"] = ["old_metric"]
    _metrics_cache["timestamp"] = time.time()

    cache_state_at_call = {}
    def capture_cache_state():
        cache_state_at_call["data"] = _metrics_cache["data"]
        cache_state_at_call["timestamp"] = _metrics_cache["timestamp"]
        return ["new_metric"]

    mock_get_cached_metrics.side_effect = lambda: capture_cache_state()

    async with Client(mcp) as client:
        result = await client.call_tool("list_metrics", {"refresh_cache": True})

        mock_get_cached_metrics.assert_called_once()
        # Verify the cache was cleared before get_cached_metrics was called
        assert cache_state_at_call["data"] is None
        assert cache_state_at_call["timestamp"] == 0
        assert result.data["metrics"] == ["new_metric"]

@pytest.mark.asyncio
async def test_list_metrics_no_refresh_by_default(mock_get_cached_metrics):
    """Test that cache is not invalidated by default."""
    _metrics_cache["data"] = ["cached_metric"]
    original_timestamp = time.time()
    _metrics_cache["timestamp"] = original_timestamp

    mock_get_cached_metrics.return_value = ["cached_metric"]

    async with Client(mcp) as client:
        result = await client.call_tool("list_metrics", {})

        # Cache should not have been reset
        assert _metrics_cache["timestamp"] == original_timestamp
        assert _metrics_cache["data"] == ["cached_metric"]
        assert result.data["metrics"] == ["cached_metric"]

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

        # Verify
        mock_make_request.assert_called_once_with("metadata", params={"metric": "up"})
        assert len(json_data) == 1
        assert json_data[0]["metric"] == "up"
        assert json_data[0]["type"] == "gauge"

@pytest.mark.asyncio
async def test_get_metric_metadata_bulk(mock_make_request):
    """Test get_metric_metadata bulk mode without metric filter."""
    mock_make_request.return_value = {
        "up": [{"type": "gauge", "help": "Target availability", "unit": ""}],
        "tls_expiry_seconds": [{"type": "gauge", "help": "Seconds until certificate expiry", "unit": "seconds"}],
        "process_cpu_seconds_total": [{"type": "counter", "help": "Total CPU seconds", "unit": "seconds"}],
    }

    async with Client(mcp) as client:
        result = await client.call_tool("get_metric_metadata", {})

        payload = result.content[0].text
        json_data = json.loads(payload)

        mock_make_request.assert_called_once_with("metadata", params=None)
        assert json_data["total_count"] == 3
        assert json_data["returned_count"] == 3
        assert json_data["offset"] == 0
        assert json_data["has_more"] is False
        assert "tls_expiry_seconds" in json_data["metadata"]


@pytest.mark.asyncio
async def test_get_metric_metadata_filter_matches_description(mock_make_request):
    """Test get_metric_metadata filter_pattern on metadata descriptions."""
    mock_make_request.return_value = {
        "tls_expiry_seconds": [{"type": "gauge", "help": "Seconds until certificate expiry", "unit": "seconds"}],
        "process_cpu_seconds_total": [{"type": "counter", "help": "Total CPU seconds", "unit": "seconds"}],
    }

    async with Client(mcp) as client:
        result = await client.call_tool("get_metric_metadata", {"filter_pattern": "certificate"})

        payload = result.content[0].text
        json_data = json.loads(payload)

        mock_make_request.assert_called_once_with("metadata", params=None)
        assert json_data["total_count"] == 1
        assert "tls_expiry_seconds" in json_data["metadata"]
        assert "process_cpu_seconds_total" not in json_data["metadata"]


@pytest.mark.asyncio
async def test_get_metric_metadata_bulk_pagination(mock_make_request):
    """Test get_metric_metadata bulk pagination."""
    mock_make_request.return_value = {
        "metric_a": [{"type": "gauge", "help": "A", "unit": ""}],
        "metric_b": [{"type": "gauge", "help": "B", "unit": ""}],
        "metric_c": [{"type": "gauge", "help": "C", "unit": ""}],
    }

    async with Client(mcp) as client:
        result = await client.call_tool("get_metric_metadata", {"limit": 1, "offset": 1})

        payload = result.content[0].text
        json_data = json.loads(payload)

        mock_make_request.assert_called_once_with("metadata", params=None)
        assert json_data["total_count"] == 3
        assert json_data["returned_count"] == 1
        assert json_data["offset"] == 1
        assert json_data["has_more"] is True
        assert list(json_data["metadata"].keys()) == ["metric_b"]


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


# --- Helper function unit tests ---

class TestCoerceMetadataEntries:
    """Tests for _coerce_metadata_entries edge cases."""

    def test_with_dict_value(self):
        """A single dict should be wrapped in a list."""
        result = _coerce_metadata_entries({"type": "gauge", "help": "Up"})
        assert result == [{"type": "gauge", "help": "Up"}]

    def test_with_unsupported_type(self):
        """Non-dict/non-list values should return empty list."""
        assert _coerce_metadata_entries("string") == []
        assert _coerce_metadata_entries(42) == []
        assert _coerce_metadata_entries(None) == []


class TestNormalizeMetadataMap:
    """Tests for _normalize_metadata_map edge cases."""

    def test_skips_non_string_keys(self):
        """Non-string dict keys should be ignored."""
        data = {"up": [{"type": "gauge"}]}
        data[123] = [{"type": "counter"}]
        result = _normalize_metadata_map(data)
        assert list(result.keys()) == ["up"]

    def test_dict_no_normalizable_entries_no_metric_key(self):
        """Dict with no coercible entries and no 'metric' key returns empty."""
        result = _normalize_metadata_map({"foo": "bar", "baz": 42})
        assert result == {}

    def test_list_skips_non_dict_entries(self):
        """Non-dict items in a list should be skipped."""
        result = _normalize_metadata_map([
            "not_a_dict",
            {"metric": "up", "type": "gauge"},
        ])
        assert list(result.keys()) == ["up"]

    def test_list_skips_entries_without_metric_key(self):
        """Dict entries without a 'metric' string key should be skipped."""
        result = _normalize_metadata_map([
            {"type": "gauge"},
            {"metric": "up", "type": "gauge"},
        ])
        assert list(result.keys()) == ["up"]

    def test_unsupported_type_returns_empty(self):
        """Non-dict/non-list input should return empty dict."""
        assert _normalize_metadata_map("string") == {}
        assert _normalize_metadata_map(42) == {}
        assert _normalize_metadata_map(None) == {}


class TestMetadataMatchesPattern:
    """Tests for _metadata_matches_pattern edge cases."""

    def test_matches_metric_name(self):
        """Pattern matching on metric name should return True."""
        assert _metadata_matches_pattern(
            "http_requests_total", [{"type": "counter"}], "http"
        ) is True

    def test_no_match(self):
        """Non-matching pattern should return False."""
        assert _metadata_matches_pattern(
            "up", [{"type": "gauge", "help": "availability"}], "http"
        ) is False


# --- MCP tool integration tests for edge cases ---

@pytest.mark.asyncio
async def test_get_metric_metadata_dict_entries(mock_make_request):
    """Test bulk mode when metadata values are dicts instead of lists."""
    mock_make_request.return_value = {
        "up": {"type": "gauge", "help": "Target availability", "unit": ""},
    }

    async with Client(mcp) as client:
        result = await client.call_tool("get_metric_metadata", {})
        payload = result.content[0].text
        json_data = json.loads(payload)

        assert json_data["total_count"] == 1
        assert "up" in json_data["metadata"]
        assert json_data["metadata"]["up"] == [{"type": "gauge", "help": "Target availability", "unit": ""}]


@pytest.mark.asyncio
async def test_get_metric_metadata_filter_matches_name(mock_make_request):
    """Test filter_pattern matching on metric name (not description)."""
    mock_make_request.return_value = {
        "http_requests_total": [{"type": "counter", "help": "Total requests", "unit": ""}],
        "go_goroutines": [{"type": "gauge", "help": "Number of goroutines", "unit": ""}],
    }

    async with Client(mcp) as client:
        result = await client.call_tool("get_metric_metadata", {"filter_pattern": "http"})
        payload = result.content[0].text
        json_data = json.loads(payload)

        assert json_data["total_count"] == 1
        assert "http_requests_total" in json_data["metadata"]
        assert "go_goroutines" not in json_data["metadata"]


@pytest.mark.asyncio
async def test_get_metric_metadata_fallback_entries(mock_make_request):
    """Test fallback when metric is not found in normalized map."""
    mock_make_request.return_value = [
        {"type": "gauge", "help": "Up status", "unit": ""}
    ]

    async with Client(mcp) as client:
        result = await client.call_tool("get_metric_metadata", {"metric": "up"})
        payload = result.content[0].text
        json_data = json.loads(payload)

        assert len(json_data) == 1
        assert json_data[0]["type"] == "gauge"
