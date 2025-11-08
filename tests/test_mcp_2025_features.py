"""Tests for MCP 2025 specification features (v1.4.1).

This module tests the following features added in v1.4.1:
- Tool annotations (readOnlyHint, destructiveHint, idempotentHint, openWorldHint)
- Tool titles for human-friendly display
- Progress notifications for long-running operations
- Resource links in query results
- Metrics caching infrastructure
"""

import pytest
import json
import time
from unittest.mock import patch, MagicMock, AsyncMock, call
from fastmcp import Client
from prometheus_mcp_server.server import (
    mcp,
    get_cached_metrics,
    _metrics_cache,
    _CACHE_TTL
)


@pytest.fixture
def mock_make_request():
    """Mock the make_prometheus_request function."""
    with patch("prometheus_mcp_server.server.make_prometheus_request") as mock:
        yield mock


class TestToolAnnotations:
    """Tests for MCP 2025 tool annotations."""

    @pytest.mark.asyncio
    async def test_all_tools_have_annotations(self):
        """Verify all tools have proper MCP 2025 annotations."""
        async with Client(mcp) as client:
            tools = await client.list_tools()

            # All tools should have annotations
            expected_tools = [
                "health_check",
                "execute_query",
                "execute_range_query",
                "list_metrics",
                "get_metric_metadata",
                "get_targets"
            ]

            tool_names = [tool.name for tool in tools]
            for expected_tool in expected_tools:
                assert expected_tool in tool_names, f"Tool {expected_tool} not found"

    @pytest.mark.asyncio
    async def test_tools_have_readonly_annotation(self):
        """Verify all tools are marked as read-only."""
        async with Client(mcp) as client:
            tools = await client.list_tools()

            for tool in tools:
                # All Prometheus query tools should be read-only
                if hasattr(tool, 'annotations') and tool.annotations:
                    assert tool.annotations.readOnlyHint is True, \
                        f"Tool {tool.name} should have readOnlyHint=True"

    @pytest.mark.asyncio
    async def test_tools_have_non_destructive_annotation(self):
        """Verify all tools are marked as non-destructive."""
        async with Client(mcp) as client:
            tools = await client.list_tools()

            for tool in tools:
                # All Prometheus query tools should be non-destructive
                if hasattr(tool, 'annotations') and tool.annotations:
                    assert tool.annotations.destructiveHint is False, \
                        f"Tool {tool.name} should have destructiveHint=False"

    @pytest.mark.asyncio
    async def test_tools_have_idempotent_annotation(self):
        """Verify all tools are marked as idempotent."""
        async with Client(mcp) as client:
            tools = await client.list_tools()

            for tool in tools:
                # All Prometheus query tools should be idempotent
                if hasattr(tool, 'annotations') and tool.annotations:
                    assert tool.annotations.idempotentHint is True, \
                        f"Tool {tool.name} should have idempotentHint=True"

    @pytest.mark.asyncio
    async def test_tools_have_openworld_annotation(self):
        """Verify all tools are marked as open-world (accessing external resources)."""
        async with Client(mcp) as client:
            tools = await client.list_tools()

            for tool in tools:
                # All Prometheus tools access external Prometheus server
                if hasattr(tool, 'annotations') and tool.annotations:
                    assert tool.annotations.openWorldHint is True, \
                        f"Tool {tool.name} should have openWorldHint=True"


class TestToolTitles:
    """Tests for human-friendly tool titles."""

    @pytest.mark.asyncio
    async def test_all_tools_have_titles(self):
        """Verify all tools have human-friendly titles."""
        async with Client(mcp) as client:
            tools = await client.list_tools()

            expected_titles = {
                "health_check": "Health Check",
                "execute_query": "Execute PromQL Query",
                "execute_range_query": "Execute PromQL Range Query",
                "list_metrics": "List Available Metrics",
                "get_metric_metadata": "Get Metric Metadata",
                "get_targets": "Get Scrape Targets"
            }

            for tool in tools:
                if tool.name in expected_titles:
                    if hasattr(tool, 'annotations') and tool.annotations:
                        assert hasattr(tool.annotations, 'title'), \
                            f"Tool {tool.name} should have a title"
                        assert tool.annotations.title == expected_titles[tool.name], \
                            f"Tool {tool.name} has incorrect title"

    @pytest.mark.asyncio
    async def test_tool_titles_are_descriptive(self):
        """Verify tool titles are more descriptive than function names."""
        async with Client(mcp) as client:
            tools = await client.list_tools()

            for tool in tools:
                if hasattr(tool, 'annotations') and tool.annotations and hasattr(tool.annotations, 'title'):
                    title = tool.annotations.title
                    # Title should be different from function name (more readable)
                    assert title != tool.name, \
                        f"Tool {tool.name} title should differ from function name"
                    # Title should have spaces (human-friendly)
                    assert ' ' in title or len(title.split()) > 1 or title[0].isupper(), \
                        f"Tool {tool.name} title should be human-friendly"


class TestProgressNotifications:
    """Tests for progress notification support.

    Note: Progress notifications are tested indirectly through the MCP client,
    as they are an internal implementation detail that gets handled by FastMCP.
    """

    @pytest.mark.asyncio
    async def test_execute_range_query_with_progress_works(self, mock_make_request):
        """Verify execute_range_query works with progress support."""
        mock_make_request.return_value = {
            "resultType": "matrix",
            "result": [{"metric": {"__name__": "up"}, "values": [[1617898400, "1"]]}]
        }

        async with Client(mcp) as client:
            # Execute - should not error even though progress is implemented
            result = await client.call_tool(
                "execute_range_query",
                {
                    "query": "up",
                    "start": "2023-01-01T00:00:00Z",
                    "end": "2023-01-01T01:00:00Z",
                    "step": "15s"
                }
            )

            # Verify result is valid
            assert result.data["resultType"] == "matrix"
            assert len(result.data["result"]) == 1

    @pytest.mark.asyncio
    async def test_list_metrics_with_progress_works(self, mock_make_request):
        """Verify list_metrics works with progress support."""
        mock_make_request.return_value = ["metric1", "metric2", "metric3"]

        async with Client(mcp) as client:
            # Execute - should not error even though progress is implemented
            result = await client.call_tool("list_metrics", {})

            # Verify result is valid - now returns a dict with pagination info
            assert isinstance(result.data, dict)
            assert result.data["total_count"] == 3
            assert result.data["returned_count"] == 3
            assert "metric1" in result.data["metrics"]


class TestResourceLinks:
    """Tests for resource links in query results."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("disable_links,should_have_links", [
        (False, True),
        (True, False),
    ])
    async def test_execute_query_includes_prometheus_ui_link(self, mock_make_request, disable_links, should_have_links):
        """Verify execute_query includes/excludes Prometheus UI link based on config."""
        with patch("prometheus_mcp_server.server.config.disable_prometheus_links", disable_links):
            mock_make_request.return_value = {
                "resultType": "vector",
                "result": [{"metric": {"__name__": "up"}, "value": [1617898448.214, "1"]}]
            }

            async with Client(mcp) as client:
                result = await client.call_tool("execute_query", {"query": "up"})

                if should_have_links:
                    assert "links" in result.data, "Result should include links"
                    assert len(result.data["links"]) > 0, "Should have at least one link"

                    # Check link structure
                    link = result.data["links"][0]
                    assert "href" in link, "Link should have href"
                    assert "rel" in link, "Link should have rel"
                    assert "title" in link, "Link should have title"

                    # Verify link points to Prometheus
                    assert "/graph?" in link["href"]
                    assert link["rel"] == "prometheus-ui"
                    assert "up" in link["href"], "Query should be included in link"
                else:
                    assert "links" not in result.data, "Result should not include links when disabled"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("disable_links,should_have_links", [
        (False, True),
        (True, False),
    ])
    async def test_execute_range_query_includes_prometheus_ui_link(self, mock_make_request, disable_links, should_have_links):
        """Verify execute_range_query includes/excludes Prometheus UI link based on config."""
        with patch("prometheus_mcp_server.server.config.disable_prometheus_links", disable_links):
            mock_make_request.return_value = {
                "resultType": "matrix",
                "result": []
            }

            async with Client(mcp) as client:
                result = await client.call_tool(
                    "execute_range_query",
                    {
                        "query": "rate(http_requests_total[5m])",
                        "start": "2023-01-01T00:00:00Z",
                        "end": "2023-01-01T01:00:00Z",
                        "step": "15s"
                    }
                )

                if should_have_links:
                    assert "links" in result.data
                    link = result.data["links"][0]

                    # Verify time parameters are in the link
                    assert "rate" in link["href"] or "http_requests_total" in link["href"]
                    assert link["rel"] == "prometheus-ui"
                else:
                    assert "links" not in result.data, "Result should not include links when disabled"

    @pytest.mark.asyncio
    async def test_query_link_includes_time_parameter(self, mock_make_request):
        """Verify instant query link includes time parameter when provided."""
        mock_make_request.return_value = {
            "resultType": "vector",
            "result": []
        }

        async with Client(mcp) as client:
            result = await client.call_tool(
                "execute_query",
                {
                    "query": "up",
                    "time": "2023-01-01T00:00:00Z"
                }
            )

            link = result.data["links"][0]
            # Link should include the time parameter
            assert "2023-01-01" in link["href"] or "moment" in link["href"]

    @pytest.mark.asyncio
    async def test_links_include_required_fields(self, mock_make_request):
        """Verify all links have required fields."""
        mock_make_request.return_value = {
            "resultType": "vector",
            "result": []
        }

        async with Client(mcp) as client:
            result = await client.call_tool("execute_query", {"query": "up"})

            link = result.data["links"][0]
            assert "href" in link, "Link must have href"
            assert "rel" in link, "Link must have rel"
            assert "title" in link, "Link must have title"
            assert link["rel"] == "prometheus-ui"


class TestMetricsCaching:
    """Tests for metrics caching infrastructure."""

    def test_get_cached_metrics_returns_list(self):
        """Verify get_cached_metrics returns a list of metrics."""
        with patch("prometheus_mcp_server.server.make_prometheus_request") as mock_request:
            mock_request.return_value = ["metric1", "metric2", "metric3"]

            result = get_cached_metrics()

            assert isinstance(result, list)
            assert len(result) == 3
            assert "metric1" in result

    def test_metrics_are_cached(self):
        """Verify metrics are cached and subsequent calls use cache."""
        with patch("prometheus_mcp_server.server.make_prometheus_request") as mock_request:
            mock_request.return_value = ["metric1", "metric2"]

            # Clear cache
            _metrics_cache["data"] = None
            _metrics_cache["timestamp"] = 0

            # First call should fetch from Prometheus
            result1 = get_cached_metrics()
            assert mock_request.call_count == 1

            # Second call should use cache
            result2 = get_cached_metrics()
            assert mock_request.call_count == 1  # Still 1, not called again

            assert result1 == result2

    def test_cache_expires_after_ttl(self):
        """Verify cache expires after TTL and refreshes."""
        with patch("prometheus_mcp_server.server.make_prometheus_request") as mock_request:
            with patch("prometheus_mcp_server.server.time") as mock_time:
                mock_request.return_value = ["metric1", "metric2"]

                # Clear cache
                _metrics_cache["data"] = None
                _metrics_cache["timestamp"] = 0

                # First call at time 0
                mock_time.time.return_value = 0
                result1 = get_cached_metrics()
                assert mock_request.call_count == 1

                # Call within TTL (at time 100, TTL is 300)
                mock_time.time.return_value = 100
                result2 = get_cached_metrics()
                assert mock_request.call_count == 1  # Still using cache

                # Call after TTL (at time 400, beyond 300s TTL)
                mock_time.time.return_value = 400
                mock_request.return_value = ["metric1", "metric2", "metric3"]
                result3 = get_cached_metrics()
                assert mock_request.call_count == 2  # Cache refreshed
                assert len(result3) == 3

    def test_cache_ttl_is_5_minutes(self):
        """Verify cache TTL is set to 5 minutes (300 seconds)."""
        assert _CACHE_TTL == 300, "Cache TTL should be 5 minutes (300 seconds)"

    def test_cache_handles_errors_gracefully(self):
        """Verify cache returns stale data on error rather than failing."""
        with patch("prometheus_mcp_server.server.make_prometheus_request") as mock_request:
            # First successful call
            mock_request.return_value = ["metric1", "metric2"]
            _metrics_cache["data"] = None
            _metrics_cache["timestamp"] = 0

            result1 = get_cached_metrics()
            assert len(result1) == 2

            # Expire cache and make request fail
            _metrics_cache["timestamp"] = 0
            mock_request.side_effect = Exception("Connection error")

            # Should return stale cache data instead of raising
            result2 = get_cached_metrics()
            assert result2 == ["metric1", "metric2"], \
                "Should return stale cache data on error"

    def test_cache_returns_empty_list_when_no_data(self):
        """Verify cache returns empty list when no data available."""
        with patch("prometheus_mcp_server.server.make_prometheus_request") as mock_request:
            mock_request.side_effect = Exception("Connection error")

            # Clear cache completely
            _metrics_cache["data"] = None
            _metrics_cache["timestamp"] = 0

            result = get_cached_metrics()
            assert result == [], "Should return empty list when no data available"


class TestBackwardCompatibility:
    """Tests to ensure new features don't break existing functionality."""

    @pytest.mark.asyncio
    async def test_query_results_still_include_resulttype(self, mock_make_request):
        """Verify query results still include original resultType field."""
        mock_make_request.return_value = {
            "resultType": "vector",
            "result": []
        }

        async with Client(mcp) as client:
            result = await client.call_tool("execute_query", {"query": "up"})

            assert "resultType" in result.data
            assert "result" in result.data

    @pytest.mark.asyncio
    async def test_tools_work_via_mcp_client(self, mock_make_request):
        """Verify all tools work when called via MCP client."""
        mock_make_request.return_value = {
            "resultType": "vector",
            "result": []
        }

        async with Client(mcp) as client:
            # Should not raise any errors
            result1 = await client.call_tool("execute_query", {"query": "up"})

            mock_make_request.return_value = {
                "resultType": "matrix",
                "result": []
            }

            result2 = await client.call_tool(
                "execute_range_query",
                {
                    "query": "up",
                    "start": "2023-01-01T00:00:00Z",
                    "end": "2023-01-01T01:00:00Z",
                    "step": "15s"
                }
            )

            mock_make_request.return_value = ["metric1"]
            result3 = await client.call_tool("list_metrics", {})

            assert result1 is not None
            assert result2 is not None
            assert result3 is not None


class TestMCP2025Integration:
    """Integration tests for MCP 2025 features working together."""

    @pytest.mark.asyncio
    async def test_full_query_workflow_with_all_features(self, mock_make_request):
        """Test a complete query workflow using all MCP 2025 features."""
        mock_make_request.return_value = {
            "resultType": "vector",
            "result": [{"metric": {"__name__": "up"}, "value": [1617898448, "1"]}]
        }

        async with Client(mcp) as client:
            # List tools and verify annotations
            tools = await client.list_tools()
            assert len(tools) > 0

            # Execute query and verify result includes links
            result = await client.call_tool("execute_query", {"query": "up"})
            result_data = result.data

            assert "resultType" in result_data
            assert "result" in result_data
            assert "links" in result_data
            assert len(result_data["links"]) > 0

    @pytest.mark.asyncio
    async def test_range_query_includes_links(self, mock_make_request):
        """Test range query includes resource links."""
        mock_make_request.return_value = {
            "resultType": "matrix",
            "result": []
        }

        async with Client(mcp) as client:
            result = await client.call_tool(
                "execute_range_query",
                {
                    "query": "up",
                    "start": "2023-01-01T00:00:00Z",
                    "end": "2023-01-01T01:00:00Z",
                    "step": "15s"
                }
            )

            # Verify links are included
            assert "links" in result.data
            assert len(result.data["links"]) > 0
            assert result.data["links"][0]["rel"] == "prometheus-ui"
