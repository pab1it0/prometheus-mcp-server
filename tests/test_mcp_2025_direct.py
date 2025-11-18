"""Direct function tests for MCP 2025 features to improve diff coverage.

This module tests features by calling functions directly rather than through
the MCP client, allowing us to test code paths that require direct context
passing (like progress notifications with ctx parameter).
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime
from prometheus_mcp_server.server import (
    execute_query,
    execute_range_query,
    list_metrics,
    get_metric_metadata,
    get_targets,
    health_check,
    config
)


@pytest.fixture
def mock_make_request():
    """Mock the make_prometheus_request function."""
    with patch("prometheus_mcp_server.server.make_prometheus_request") as mock:
        yield mock


class TestDirectFunctionCalls:
    """Test functions called directly to cover context-dependent code paths."""

    @pytest.mark.asyncio
    async def test_execute_query_direct_call(self, mock_make_request):
        """Test execute_query by calling it directly."""
        mock_make_request.return_value = {
            "resultType": "vector",
            "result": [{"metric": {"__name__": "up"}, "value": [1617898448.214, "1"]}]
        }

        # Access the underlying function from FunctionTool
        result = await execute_query.fn(query="up", time="2023-01-01T00:00:00Z")

        assert "resultType" in result
        assert "result" in result
        assert "links" in result
        assert result["links"][0]["rel"] == "prometheus-ui"
        assert "up" in result["links"][0]["href"]

    @pytest.mark.asyncio
    async def test_execute_range_query_with_context(self, mock_make_request):
        """Test execute_range_query with context for progress reporting."""
        mock_make_request.return_value = {
            "resultType": "matrix",
            "result": [{"metric": {"__name__": "up"}, "values": [[1617898400, "1"]]}]
        }

        # Create mock context
        mock_ctx = AsyncMock()
        mock_ctx.report_progress = AsyncMock()

        result = await execute_range_query.fn(
            query="up",
            start="2023-01-01T00:00:00Z",
            end="2023-01-01T01:00:00Z",
            step="15s",
            ctx=mock_ctx
        )

        # Verify progress was reported
        assert mock_ctx.report_progress.call_count >= 3
        calls = mock_ctx.report_progress.call_args_list

        # Check initial progress
        assert calls[0].kwargs["progress"] == 0
        assert calls[0].kwargs["total"] == 100
        assert "Initiating" in calls[0].kwargs["message"]

        # Check completion progress
        assert calls[-1].kwargs["progress"] == 100
        assert calls[-1].kwargs["total"] == 100
        assert "completed" in calls[-1].kwargs["message"]

        # Verify result includes links
        assert "links" in result
        assert result["links"][0]["rel"] == "prometheus-ui"

    @pytest.mark.asyncio
    async def test_execute_range_query_without_context(self, mock_make_request):
        """Test execute_range_query without context (backward compatibility)."""
        mock_make_request.return_value = {
            "resultType": "matrix",
            "result": []
        }

        # Call without context - should not error
        result = await execute_range_query.fn(
            query="up",
            start="2023-01-01T00:00:00Z",
            end="2023-01-01T01:00:00Z",
            step="15s",
            ctx=None
        )

        assert "resultType" in result
        assert "links" in result

    @pytest.mark.asyncio
    async def test_list_metrics_with_context(self, mock_make_request):
        """Test list_metrics with context for progress reporting."""
        mock_make_request.return_value = ["metric1", "metric2", "metric3"]

        # Create mock context
        mock_ctx = AsyncMock()
        mock_ctx.report_progress = AsyncMock()

        result = await list_metrics.fn(ctx=mock_ctx)

        # Verify progress was reported (now expects 3 calls: start, processing, completion)
        assert mock_ctx.report_progress.call_count >= 2
        calls = mock_ctx.report_progress.call_args_list

        # Check initial progress
        assert calls[0].kwargs["progress"] == 0
        assert calls[0].kwargs["total"] == 100
        assert "Fetching" in calls[0].kwargs["message"]

        # Check completion progress with count
        assert calls[-1].kwargs["progress"] == 100
        assert calls[-1].kwargs["total"] == 100
        assert "3" in calls[-1].kwargs["message"]

        # Verify result - now returns a dict with pagination info
        assert isinstance(result, dict)
        assert result["total_count"] == 3
        assert result["returned_count"] == 3
        assert "metric1" in result["metrics"]

    @pytest.mark.asyncio
    async def test_list_metrics_without_context(self, mock_make_request):
        """Test list_metrics without context (backward compatibility)."""
        mock_make_request.return_value = ["metric1", "metric2"]

        result = await list_metrics.fn(ctx=None)

        # Now returns a dict with pagination info
        assert isinstance(result, dict)
        assert result["total_count"] == 2
        assert result["returned_count"] == 2
        assert "metric1" in result["metrics"]

    @pytest.mark.asyncio
    async def test_get_metric_metadata_direct_call(self, mock_make_request):
        """Test get_metric_metadata by calling it directly."""
        # Test when data is in "metadata" key
        mock_make_request.return_value = {
            "metadata": [
                {"metric": "up", "type": "gauge", "help": "Up status", "unit": ""}
            ]
        }

        result = await get_metric_metadata.fn(metric="up")

        assert len(result) == 1
        assert result[0]["metric"] == "up"
        assert result[0]["type"] == "gauge"

    @pytest.mark.asyncio
    async def test_get_metric_metadata_data_key(self, mock_make_request):
        """Test get_metric_metadata when data is in 'data' key instead of 'metadata'."""
        # Test when data is in "data" key (fallback path)
        mock_make_request.return_value = {
            "data": [
                {"metric": "http_requests", "type": "counter", "help": "HTTP requests", "unit": ""}
            ]
        }

        result = await get_metric_metadata.fn(metric="http_requests")

        assert len(result) == 1
        assert result[0]["metric"] == "http_requests"
        assert result[0]["type"] == "counter"

    @pytest.mark.asyncio
    async def test_get_metric_metadata_fallback_to_raw_data(self, mock_make_request):
        """Test get_metric_metadata when neither 'metadata' nor 'data' keys exist."""
        # Test when data is returned directly (neither "metadata" nor "data" keys exist)
        mock_make_request.return_value = [
            {"metric": "cpu_usage", "type": "gauge", "help": "CPU usage", "unit": "percent"}
        ]

        result = await get_metric_metadata.fn(metric="cpu_usage")

        assert len(result) == 1
        assert result[0]["metric"] == "cpu_usage"
        assert result[0]["type"] == "gauge"

    @pytest.mark.asyncio
    async def test_get_metric_metadata_dict_to_list_conversion(self, mock_make_request):
        """Test get_metric_metadata when metadata is a dict and needs conversion to list."""
        # Test when metadata is a single dict that needs to be converted to a list
        mock_make_request.return_value = {
            "metadata": {"metric": "memory_usage", "type": "gauge", "help": "Memory usage", "unit": "bytes"}
        }

        result = await get_metric_metadata.fn(metric="memory_usage")

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["metric"] == "memory_usage"
        assert result[0]["type"] == "gauge"

    @pytest.mark.asyncio
    async def test_get_metric_metadata_data_key_dict_to_list(self, mock_make_request):
        """Test get_metric_metadata when data is in 'data' key as a dict."""
        # Test when data is in "data" key as a dict that needs conversion
        mock_make_request.return_value = {
            "data": {"metric": "disk_usage", "type": "gauge", "help": "Disk usage", "unit": "bytes"}
        }

        result = await get_metric_metadata.fn(metric="disk_usage")

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["metric"] == "disk_usage"
        assert result[0]["type"] == "gauge"

    @pytest.mark.asyncio
    async def test_get_metric_metadata_raw_dict_to_list(self, mock_make_request):
        """Test get_metric_metadata when raw data is a dict (fallback path with dict)."""
        # Test when data is returned directly as a dict (neither "metadata" nor "data" keys)
        mock_make_request.return_value = {
            "metric": "network_bytes", "type": "counter", "help": "Network bytes", "unit": "bytes"
        }

        result = await get_metric_metadata.fn(metric="network_bytes")

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["metric"] == "network_bytes"
        assert result[0]["type"] == "counter"

    @pytest.mark.asyncio
    async def test_get_targets_direct_call(self, mock_make_request):
        """Test get_targets by calling it directly."""
        mock_make_request.return_value = {
            "activeTargets": [
                {
                    "discoveredLabels": {"__address__": "localhost:9090"},
                    "labels": {"job": "prometheus"},
                    "health": "up"
                }
            ],
            "droppedTargets": [
                {
                    "discoveredLabels": {"__address__": "localhost:9091"}
                }
            ]
        }

        result = await get_targets.fn()

        assert "activeTargets" in result
        assert "droppedTargets" in result
        assert len(result["activeTargets"]) == 1
        assert result["activeTargets"][0]["health"] == "up"
        assert len(result["droppedTargets"]) == 1


class TestHealthCheckFunction:
    """Test health_check function directly to improve coverage."""

    @pytest.mark.asyncio
    async def test_health_check_healthy_with_prometheus(self, mock_make_request):
        """Test health_check when Prometheus is accessible."""
        mock_make_request.return_value = {
            "resultType": "vector",
            "result": []
        }

        with patch("prometheus_mcp_server.server.config") as mock_config:
            mock_config.url = "http://prometheus:9090"
            mock_config.username = "admin"
            mock_config.password = "secret"
            mock_config.org_id = None
            mock_config.mcp_server_config = MagicMock()
            mock_config.mcp_server_config.mcp_server_transport = "stdio"

            result = await health_check.fn()

            assert result["status"] == "healthy"
            assert result["service"] == "prometheus-mcp-server"
            assert "version" in result  # Version exists but value is managed by maintainers
            assert "timestamp" in result
            assert result["prometheus_connectivity"] == "healthy"
            assert result["prometheus_url"] == "http://prometheus:9090"
            assert result["configuration"]["prometheus_url_configured"] is True
            assert result["configuration"]["authentication_configured"] is True

    @pytest.mark.asyncio
    async def test_health_check_degraded_prometheus_error(self, mock_make_request):
        """Test health_check when Prometheus is not accessible."""
        mock_make_request.side_effect = Exception("Connection refused")

        with patch("prometheus_mcp_server.server.config") as mock_config:
            mock_config.url = "http://prometheus:9090"
            mock_config.username = None
            mock_config.password = None
            mock_config.token = None
            mock_config.org_id = None
            mock_config.mcp_server_config = MagicMock()
            mock_config.mcp_server_config.mcp_server_transport = "http"

            result = await health_check.fn()

            assert result["status"] == "degraded"
            assert result["prometheus_connectivity"] == "unhealthy"
            assert "prometheus_error" in result
            assert "Connection refused" in result["prometheus_error"]

    @pytest.mark.asyncio
    async def test_health_check_unhealthy_no_url(self):
        """Test health_check when PROMETHEUS_URL is not configured."""
        with patch("prometheus_mcp_server.server.config") as mock_config:
            mock_config.url = ""
            mock_config.username = None
            mock_config.password = None
            mock_config.token = None
            mock_config.org_id = None
            mock_config.mcp_server_config = MagicMock()
            mock_config.mcp_server_config.mcp_server_transport = "stdio"

            result = await health_check.fn()

            assert result["status"] == "unhealthy"
            assert "error" in result
            assert "PROMETHEUS_URL not configured" in result["error"]
            assert result["configuration"]["prometheus_url_configured"] is False

    @pytest.mark.asyncio
    async def test_health_check_with_token_auth(self, mock_make_request):
        """Test health_check with token authentication."""
        mock_make_request.return_value = {
            "resultType": "vector",
            "result": []
        }

        with patch("prometheus_mcp_server.server.config") as mock_config:
            mock_config.url = "http://prometheus:9090"
            mock_config.username = None
            mock_config.password = None
            mock_config.token = "bearer-token-123"
            mock_config.org_id = "org-1"
            mock_config.mcp_server_config = MagicMock()
            mock_config.mcp_server_config.mcp_server_transport = "sse"

            result = await health_check.fn()

            assert result["status"] == "healthy"
            assert result["configuration"]["authentication_configured"] is True
            assert result["configuration"]["org_id_configured"] is True
            assert result["transport"] == "sse"

    @pytest.mark.asyncio
    async def test_health_check_exception_handling(self):
        """Test health_check handles unexpected exceptions."""
        with patch("prometheus_mcp_server.server.config") as mock_config:
            # Make accessing config.url raise an exception
            type(mock_config).url = property(lambda self: (_ for _ in ()).throw(RuntimeError("Unexpected error")))

            result = await health_check.fn()

            assert result["status"] == "unhealthy"
            assert "error" in result
            assert "Unexpected error" in result["error"]

    @pytest.mark.asyncio
    async def test_health_check_with_org_id(self, mock_make_request):
        """Test health_check includes org_id configuration."""
        mock_make_request.return_value = {
            "resultType": "vector",
            "result": []
        }

        with patch("prometheus_mcp_server.server.config") as mock_config:
            mock_config.url = "http://prometheus:9090"
            mock_config.username = None
            mock_config.password = None
            mock_config.token = None
            mock_config.org_id = "tenant-123"
            mock_config.mcp_server_config = MagicMock()
            mock_config.mcp_server_config.mcp_server_transport = "stdio"

            result = await health_check.fn()

            assert result["configuration"]["org_id_configured"] is True

    @pytest.mark.asyncio
    async def test_health_check_no_mcp_server_config(self, mock_make_request):
        """Test health_check when mcp_server_config is None."""
        mock_make_request.return_value = {
            "resultType": "vector",
            "result": []
        }

        with patch("prometheus_mcp_server.server.config") as mock_config:
            mock_config.url = "http://prometheus:9090"
            mock_config.username = None
            mock_config.password = None
            mock_config.token = None
            mock_config.org_id = None
            mock_config.mcp_server_config = None

            result = await health_check.fn()

            assert result["status"] == "healthy"
            assert result["transport"] == "stdio"


class TestProgressNotificationsPaths:
    """Test progress notification code paths for complete coverage."""

    @pytest.mark.asyncio
    async def test_range_query_progress_all_stages(self, mock_make_request):
        """Test all three progress stages in execute_range_query."""
        mock_make_request.return_value = {
            "resultType": "matrix",
            "result": []
        }

        mock_ctx = AsyncMock()
        mock_ctx.report_progress = AsyncMock()

        await execute_range_query.fn(
            query="up",
            start="2023-01-01T00:00:00Z",
            end="2023-01-01T01:00:00Z",
            step="15s",
            ctx=mock_ctx
        )

        # Verify all three stages
        calls = [call.kwargs for call in mock_ctx.report_progress.call_args_list]

        # Stage 1: Initiation (0%)
        assert any(c["progress"] == 0 and "Initiating" in c["message"] for c in calls)

        # Stage 2: Processing (50%)
        assert any(c["progress"] == 50 and "Processing" in c["message"] for c in calls)

        # Stage 3: Completion (100%)
        assert any(c["progress"] == 100 and "completed" in c["message"] for c in calls)

    @pytest.mark.asyncio
    async def test_list_metrics_progress_both_stages(self, mock_make_request):
        """Test both progress stages in list_metrics."""
        mock_make_request.return_value = ["m1", "m2", "m3", "m4", "m5"]

        mock_ctx = AsyncMock()
        mock_ctx.report_progress = AsyncMock()

        await list_metrics.fn(ctx=mock_ctx)

        calls = [call.kwargs for call in mock_ctx.report_progress.call_args_list]

        # Stage 1: Fetching (0%)
        assert any(c["progress"] == 0 and "Fetching" in c["message"] for c in calls)

        # Stage 2: Completion (100%) with count
        assert any(c["progress"] == 100 and "5" in c["message"] for c in calls)
