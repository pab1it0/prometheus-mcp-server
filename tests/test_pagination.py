"""Tests for pagination and filtering functionality."""

import pytest
from unittest.mock import patch, MagicMock
from prometheus_mcp_server.server import (
    apply_pagination, 
    filter_metrics, 
    create_compact_query_result,
    execute_query,
    list_metrics,
    get_targets,
    config
)

class TestPaginationUtils:
    """Test pagination utility functions."""

    def test_apply_pagination_with_limit(self):
        """Test pagination with limit only."""
        data = list(range(100))
        result = apply_pagination(data, limit=10)

        assert len(result["data"]) == 10
        assert result["data"] == list(range(10))
        assert result["metadata"]["total"] == 100
        assert result["metadata"]["offset"] == 0
        assert result["metadata"]["limit"] == 10
        assert result["metadata"]["returned"] == 10
        assert result["metadata"]["hasMore"] is True

    def test_apply_pagination_with_offset(self):
        """Test pagination with offset only."""
        data = list(range(100))
        result = apply_pagination(data, offset=50)

        assert len(result["data"]) == 50
        assert result["data"] == list(range(50, 100))
        assert result["metadata"]["offset"] == 50
        assert result["metadata"]["hasMore"] is False

    def test_apply_pagination_with_limit_and_offset(self):
        """Test pagination with both limit and offset."""
        data = list(range(100))
        result = apply_pagination(data, limit=10, offset=20)

        assert len(result["data"]) == 10
        assert result["data"] == list(range(20, 30))
        assert result["metadata"]["offset"] == 20
        assert result["metadata"]["limit"] == 10
        assert result["metadata"]["hasMore"] is True

    def test_apply_pagination_no_params(self):
        """Test pagination with no parameters."""
        data = list(range(10))
        result = apply_pagination(data)

        assert result["data"] == data
        assert result["metadata"]["total"] == 10
        assert result["metadata"]["hasMore"] is False

class TestMetricFiltering:
    """Test metric filtering functionality."""

    def test_filter_metrics_by_prefix(self):
        """Test filtering metrics by prefix."""
        metrics = ["storage_total", "storage_used", "cpu_usage", "memory_total"]
        result = filter_metrics(metrics, prefix="storage_")

        assert result == ["storage_total", "storage_used"]

    def test_filter_metrics_by_pattern(self):
        """Test filtering metrics by regex pattern."""
        metrics = ["storage_total", "storage_used", "cpu_usage", "memory_total"]
        result = filter_metrics(metrics, filter_pattern=r".*_total$")

        assert result == ["storage_total", "memory_total"]

    def test_filter_metrics_by_prefix_and_pattern(self):
        """Test filtering metrics by both prefix and pattern."""
        metrics = ["storage_total", "storage_used", "storage_free", "cpu_usage"]
        result = filter_metrics(metrics, prefix="storage_", filter_pattern=r".*_(total|free)$")

        assert result == ["storage_total", "storage_free"]

    def test_filter_metrics_invalid_regex(self):
        """Test filtering with invalid regex pattern."""
        metrics = ["storage_total", "cpu_usage"]
        result = filter_metrics(metrics, filter_pattern="[invalid")

        # Should return original metrics when regex is invalid
        assert result == metrics

class TestCompactResults:
    """Test compact result formatting."""

    def test_create_compact_query_result_vector(self):
        """Test creating compact results for vector queries."""
        result_data = {
            "resultType": "vector",
            "result": [
                {
                    "metric": {"__name__": "up", "job": "prometheus", "instance": "localhost:9090"},
                    "value": [1234567890, "1"]
                },
                {
                    "metric": {"__name__": "up", "job": "node-exporter", "instance": "localhost:9100"},
                    "value": [1234567890, "0"]
                }
            ]
        }

        compact = create_compact_query_result(result_data)

        assert compact["resultType"] == "compact_vector"
        assert len(compact["result"]) == 2
        assert compact["result"][0]["name"] == "up"
        assert compact["result"][0]["value"] == "1"
        assert compact["result"][0]["labels"] == {"job": "prometheus", "instance": "localhost:9090"}

    def test_create_compact_query_result_non_vector(self):
        """Test compact results for non-vector queries (should return unchanged)."""
        result_data = {
            "resultType": "scalar",
            "result": [1234567890, "42"]
        }

        compact = create_compact_query_result(result_data)
        assert compact == result_data

class TestEnhancedTools:
    """Test enhanced MCP tools with pagination."""

    @pytest.mark.asyncio
    @patch("prometheus_mcp_server.server.make_prometheus_request")
    async def test_execute_query_with_pagination(self, mock_request):
        """Test execute_query with pagination parameters."""
        # Setup mock response
        mock_request.return_value = {
            "resultType": "vector",
            "result": [{"metric": {"__name__": f"metric_{i}"}, "value": [123, str(i)]} for i in range(20)]
        }
        config.url = "http://test:9090"

        # Test with pagination
        result = await execute_query("up", limit=5, offset=2)

        assert "pagination" in result
        assert len(result["result"]) == 5
        assert result["pagination"]["offset"] == 2
        assert result["pagination"]["limit"] == 5
        assert result["pagination"]["hasMore"] is True

    @pytest.mark.asyncio
    @patch("prometheus_mcp_server.server.make_prometheus_request")
    async def test_execute_query_with_compact_mode(self, mock_request):
        """Test execute_query with compact mode."""
        mock_request.return_value = {
            "resultType": "vector",
            "result": [
                {
                    "metric": {"__name__": "up", "job": "prometheus"},
                    "value": [1234567890, "1"]
                }
            ]
        }
        config.url = "http://test:9090"

        result = await execute_query("up", compact=True)

        assert result["resultType"] == "compact_vector"
        assert result["result"][0]["name"] == "up"
        assert result["result"][0]["labels"] == {"job": "prometheus"}

    @pytest.mark.asyncio
    @patch("prometheus_mcp_server.server.make_prometheus_request")
    async def test_list_metrics_with_filtering(self, mock_request):
        """Test list_metrics with filtering and pagination."""
        mock_request.return_value = [
            "storage_total", "storage_used", "cpu_usage", "memory_total", "network_bytes"
        ]
        config.url = "http://test:9090"

        # Test with prefix filtering
        result = await list_metrics(prefix="storage_", limit=2)

        assert "pagination" in result
        assert len(result["metrics"]) == 2
        assert all(m.startswith("storage_") for m in result["metrics"])
        assert result["pagination"]["total"] == 2  # Only 2 storage metrics

    @pytest.mark.asyncio
    @patch("prometheus_mcp_server.server.make_prometheus_request")
    async def test_get_targets_with_pagination(self, mock_request):
        """Test get_targets with pagination."""
        mock_request.return_value = {
            "activeTargets": [{"job": f"job_{i}"} for i in range(10)],
            "droppedTargets": [{"job": "dropped_job"}]
        }
        config.url = "http://test:9090"

        result = await get_targets(limit=3, offset=1)

        assert "activePagination" in result
        assert len(result["activeTargets"]) == 3
        assert result["activePagination"]["offset"] == 1
        assert "droppedTargets" in result  # Should still include dropped targets

    @pytest.mark.asyncio
    @patch("prometheus_mcp_server.server.make_prometheus_request")
    async def test_get_targets_active_only(self, mock_request):
        """Test get_targets with active_only flag."""
        mock_request.return_value = {
            "activeTargets": [{"job": "active_job"}],
            "droppedTargets": [{"job": "dropped_job"}]
        }
        config.url = "http://test:9090"

        result = await get_targets(active_only=True)

        assert "droppedTargets" not in result
        assert len(result["activeTargets"]) == 1
