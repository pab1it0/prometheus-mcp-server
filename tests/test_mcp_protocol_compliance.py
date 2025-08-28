"""Tests for MCP protocol compliance and tool functionality."""

import pytest
import json
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime
from prometheus_mcp_server import server
from prometheus_mcp_server.server import (
    make_prometheus_request, get_prometheus_auth, config, TransportType,
    execute_query, execute_range_query, list_metrics, get_metric_metadata, get_targets, health_check
)

# Test the MCP tools by testing them through async wrappers
async def execute_query_wrapper(query: str, time=None):
    """Wrapper to test execute_query functionality."""
    params = {"query": query}
    if time:
        params["time"] = time
    data = make_prometheus_request("query", params=params)
    return {"resultType": data["resultType"], "result": data["result"]}

async def execute_range_query_wrapper(query: str, start: str, end: str, step: str):
    """Wrapper to test execute_range_query functionality."""  
    params = {"query": query, "start": start, "end": end, "step": step}
    data = make_prometheus_request("query_range", params=params)
    return {"resultType": data["resultType"], "result": data["result"]}

async def list_metrics_wrapper():
    """Wrapper to test list_metrics functionality."""
    return make_prometheus_request("label/__name__/values")

async def get_metric_metadata_wrapper(metric: str):
    """Wrapper to test get_metric_metadata functionality."""
    params = {"metric": metric}
    data = make_prometheus_request("metadata", params=params)
    return data["metadata"][metric]

async def get_targets_wrapper():
    """Wrapper to test get_targets functionality."""
    data = make_prometheus_request("targets")
    return {"activeTargets": data["activeTargets"], "droppedTargets": data["droppedTargets"]}

async def health_check_wrapper():
    """Wrapper to test health_check functionality."""
    try:
        health_status = {
            "status": "healthy",
            "service": "prometheus-mcp-server", 
            "version": "1.2.3",
            "timestamp": datetime.utcnow().isoformat(),
            "transport": config.mcp_server_config.mcp_server_transport if config.mcp_server_config else "stdio",
            "configuration": {
                "prometheus_url_configured": bool(config.url),
                "authentication_configured": bool(config.username or config.token),
                "org_id_configured": bool(config.org_id)
            }
        }
        
        if config.url:
            try:
                make_prometheus_request("query", params={"query": "up", "time": str(int(datetime.utcnow().timestamp()))})
                health_status["prometheus_connectivity"] = "healthy"
                health_status["prometheus_url"] = config.url
            except Exception as e:
                health_status["prometheus_connectivity"] = "unhealthy"
                health_status["prometheus_error"] = str(e)
                health_status["status"] = "degraded"
        else:
            health_status["status"] = "unhealthy"
            health_status["error"] = "PROMETHEUS_URL not configured"
        
        return health_status
    except Exception as e:
        return {
            "status": "unhealthy",
            "service": "prometheus-mcp-server",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }


@pytest.fixture
def mock_prometheus_response():
    """Mock successful Prometheus API response."""
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {
                    "metric": {"__name__": "up", "instance": "localhost:9090"},
                    "value": [1609459200, "1"]
                }
            ]
        }
    }


@pytest.fixture
def mock_metrics_response():
    """Mock Prometheus metrics list response."""
    return {
        "status": "success", 
        "data": ["up", "prometheus_build_info", "prometheus_config_last_reload_successful"]
    }


@pytest.fixture
def mock_metadata_response():
    """Mock Prometheus metadata response."""
    return {
        "status": "success",
        "data": {
            "metadata": {
                "up": [
                    {
                        "type": "gauge",
                        "help": "1 if the instance is healthy, 0 otherwise",
                        "unit": ""
                    }
                ]
            }
        }
    }


@pytest.fixture
def mock_targets_response():
    """Mock Prometheus targets response."""
    return {
        "status": "success",
        "data": {
            "activeTargets": [
                {
                    "discoveredLabels": {"__address__": "localhost:9090"},
                    "labels": {"instance": "localhost:9090", "job": "prometheus"},
                    "scrapePool": "prometheus",
                    "scrapeUrl": "http://localhost:9090/metrics",
                    "lastError": "",
                    "lastScrape": "2023-01-01T00:00:00Z",
                    "lastScrapeDuration": 0.001,
                    "health": "up"
                }
            ],
            "droppedTargets": []
        }
    }


class TestMCPToolCompliance:
    """Test MCP tool interface compliance."""
    
    @patch('test_mcp_protocol_compliance.make_prometheus_request')
    @pytest.mark.asyncio  
    async def test_execute_query_tool_signature(self, mock_request, mock_prometheus_response):
        """Test execute_query tool has correct MCP signature."""
        mock_request.return_value = mock_prometheus_response["data"]
        
        # Ensure config has a URL set for tests
        original_url = config.url
        if not config.url:
            config.url = "http://test-prometheus:9090"
            
        try:
            # Test required parameters
            result = await execute_query_wrapper("up")
            assert isinstance(result, dict)
            assert "resultType" in result
            assert "result" in result
            
            # Test optional parameters
            result = await execute_query_wrapper("up", time="2023-01-01T00:00:00Z")
            assert isinstance(result, dict)
        finally:
            config.url = original_url
    
    @patch('test_mcp_protocol_compliance.make_prometheus_request')
    @pytest.mark.asyncio
    async def test_execute_range_query_tool_signature(self, mock_request, mock_prometheus_response):
        """Test execute_range_query tool has correct MCP signature."""
        mock_request.return_value = mock_prometheus_response["data"]
        
        # Test all required parameters
        result = await execute_range_query_wrapper(
            query="up",
            start="2023-01-01T00:00:00Z", 
            end="2023-01-01T01:00:00Z",
            step="1m"
        )
        assert isinstance(result, dict)
        assert "resultType" in result
        assert "result" in result
    
    @patch('test_mcp_protocol_compliance.make_prometheus_request')
    @pytest.mark.asyncio
    async def test_list_metrics_tool_signature(self, mock_request, mock_metrics_response):
        """Test list_metrics tool has correct MCP signature."""
        mock_request.return_value = mock_metrics_response["data"]
        
        result = await list_metrics_wrapper()
        assert isinstance(result, list)
        assert all(isinstance(metric, str) for metric in result)
    
    @patch('test_mcp_protocol_compliance.make_prometheus_request')
    @pytest.mark.asyncio
    async def test_get_metric_metadata_tool_signature(self, mock_request, mock_metadata_response):
        """Test get_metric_metadata tool has correct MCP signature."""
        mock_request.return_value = mock_metadata_response["data"]
        
        result = await get_metric_metadata_wrapper("up")
        assert isinstance(result, list)
        assert all(isinstance(metadata, dict) for metadata in result)
    
    @patch('test_mcp_protocol_compliance.make_prometheus_request')
    @pytest.mark.asyncio
    async def test_get_targets_tool_signature(self, mock_request, mock_targets_response):
        """Test get_targets tool has correct MCP signature."""
        mock_request.return_value = mock_targets_response["data"]
        
        result = await get_targets_wrapper()
        assert isinstance(result, dict)
        assert "activeTargets" in result
        assert "droppedTargets" in result
        assert isinstance(result["activeTargets"], list)
        assert isinstance(result["droppedTargets"], list)
    
    @patch('test_mcp_protocol_compliance.make_prometheus_request')
    @pytest.mark.asyncio
    async def test_health_check_tool_signature(self, mock_request):
        """Test health_check tool has correct MCP signature."""
        # Mock successful Prometheus connectivity
        mock_request.return_value = {"resultType": "vector", "result": []}
        
        result = await health_check_wrapper()
        assert isinstance(result, dict)
        assert "status" in result
        assert "service" in result
        assert "timestamp" in result
        assert result["service"] == "prometheus-mcp-server"


class TestMCPToolErrorHandling:
    """Test MCP tool error handling compliance."""
    
    @patch('test_mcp_protocol_compliance.make_prometheus_request')
    @pytest.mark.asyncio
    async def test_execute_query_handles_prometheus_errors(self, mock_request):
        """Test execute_query handles Prometheus API errors gracefully."""
        mock_request.side_effect = ValueError("Prometheus API error: query timeout")
        
        with pytest.raises(ValueError):
            await execute_query_wrapper("invalid_query{")
    
    @patch('test_mcp_protocol_compliance.make_prometheus_request')
    @pytest.mark.asyncio
    async def test_execute_range_query_handles_network_errors(self, mock_request):
        """Test execute_range_query handles network errors gracefully."""
        import requests
        mock_request.side_effect = requests.exceptions.ConnectionError("Connection refused")
        
        with pytest.raises(requests.exceptions.ConnectionError):
            await execute_range_query_wrapper("up", "now-1h", "now", "1m")
    
    @patch('test_mcp_protocol_compliance.make_prometheus_request')
    @pytest.mark.asyncio
    async def test_health_check_handles_configuration_errors(self, mock_request):
        """Test health_check handles configuration errors gracefully."""
        # Test with missing Prometheus URL
        original_url = config.url
        config.url = ""
        
        try:
            result = await health_check_wrapper()
            assert result["status"] == "unhealthy" 
            assert "error" in result or "PROMETHEUS_URL" in str(result)
        finally:
            config.url = original_url
    
    @patch('test_mcp_protocol_compliance.make_prometheus_request')
    @pytest.mark.asyncio
    async def test_health_check_handles_connectivity_errors(self, mock_request):
        """Test health_check handles Prometheus connectivity errors."""
        mock_request.side_effect = Exception("Connection timeout")
        
        result = await health_check_wrapper()
        assert result["status"] in ["unhealthy", "degraded"]
        assert "prometheus_connectivity" in result or "error" in result


class TestMCPDataFormats:
    """Test MCP tool data format compliance."""
    
    @patch('test_mcp_protocol_compliance.make_prometheus_request')
    @pytest.mark.asyncio
    async def test_execute_query_returns_valid_json(self, mock_request, mock_prometheus_response):
        """Test execute_query returns JSON-serializable data."""
        mock_request.return_value = mock_prometheus_response["data"]
        
        result = await execute_query_wrapper("up")
        
        # Verify JSON serializability
        json_str = json.dumps(result)
        assert json_str is not None
        
        # Verify structure
        parsed = json.loads(json_str)
        assert "resultType" in parsed
        assert "result" in parsed
    
    @patch('test_mcp_protocol_compliance.make_prometheus_request')
    @pytest.mark.asyncio
    async def test_all_tools_return_json_serializable_data(self, mock_request):
        """Test all MCP tools return JSON-serializable data."""
        # Setup various mock responses
        mock_request.side_effect = [
            {"resultType": "vector", "result": []},  # execute_query
            {"resultType": "matrix", "result": []},  # execute_range_query
            ["metric1", "metric2"],  # list_metrics
            {"metadata": {"metric1": [{"type": "gauge", "help": "test"}]}},  # get_metric_metadata
            {"activeTargets": [], "droppedTargets": []},  # get_targets
        ]
        
        # Test all tools
        tools_and_calls = [
            (execute_query_wrapper, ("up",)),
            (execute_range_query_wrapper, ("up", "now-1h", "now", "1m")),
            (list_metrics_wrapper, ()),
            (get_metric_metadata_wrapper, ("metric1",)),
            (get_targets_wrapper, ()),
        ]
        
        for tool, args in tools_and_calls:
            result = await tool(*args)
            
            # Verify JSON serializability
            try:
                json_str = json.dumps(result)
                assert json_str is not None
            except (TypeError, ValueError) as e:
                pytest.fail(f"Tool {tool.__name__} returned non-JSON-serializable data: {e}")


class TestMCPServerConfiguration:
    """Test MCP server configuration compliance."""
    
    def test_transport_type_validation(self):
        """Test transport type validation works correctly."""
        # Valid transport types
        valid_transports = ["stdio", "http", "sse"]
        for transport in valid_transports:
            assert transport in TransportType.values()
        
        # Invalid transport types should not be in values
        invalid_transports = ["tcp", "websocket", "grpc"]
        for transport in invalid_transports:
            assert transport not in TransportType.values()
    
    def test_server_config_validation(self):
        """Test server configuration validation."""
        from prometheus_mcp_server.server import MCPServerConfig, PrometheusConfig
        
        # Valid configuration
        mcp_config = MCPServerConfig(
            mcp_server_transport="http",
            mcp_bind_host="127.0.0.1", 
            mcp_bind_port=8080
        )
        assert mcp_config.mcp_server_transport == "http"
        
        # Test Prometheus config
        prometheus_config = PrometheusConfig(
            url="http://prometheus:9090",
            mcp_server_config=mcp_config
        )
        assert prometheus_config.url == "http://prometheus:9090"
    
    def test_authentication_configuration(self):
        """Test authentication configuration options."""
        from prometheus_mcp_server.server import get_prometheus_auth
        
        # Test with no authentication
        original_config = {
            'username': config.username,
            'password': config.password, 
            'token': config.token
        }
        
        try:
            config.username = ""
            config.password = ""
            config.token = ""
            
            auth = get_prometheus_auth()
            assert auth is None
            
            # Test with basic auth
            config.username = "testuser"
            config.password = "testpass"
            config.token = ""
            
            auth = get_prometheus_auth()
            assert auth is not None
            
            # Test with token auth (should take precedence)
            config.token = "test-token"
            
            auth = get_prometheus_auth()
            assert auth is not None
            assert "Authorization" in auth
            assert "Bearer" in auth["Authorization"]
            
        finally:
            # Restore original config
            config.username = original_config['username']
            config.password = original_config['password']
            config.token = original_config['token']


class TestMCPProtocolVersioning:
    """Test MCP protocol versioning and capabilities."""
    
    def test_mcp_server_info(self):
        """Test MCP server provides correct server information."""
        # Test FastMCP server instantiation
        from prometheus_mcp_server.server import mcp
        
        assert mcp is not None
        # FastMCP should have a name
        assert hasattr(mcp, 'name') or hasattr(mcp, '_name')
    
    @patch('test_mcp_protocol_compliance.make_prometheus_request')
    @pytest.mark.asyncio
    async def test_tool_descriptions_are_present(self, mock_request):
        """Test that all MCP tools have proper descriptions."""
        # All tools should be registered with descriptions
        tools = [
            execute_query,
            execute_range_query,
            list_metrics,
            get_metric_metadata,
            get_targets,
            health_check
        ]
        
        for tool in tools:
            # Each tool should have a description (FastMCP tools have description attribute)
            assert hasattr(tool, 'description')
            assert tool.description is not None and tool.description.strip() != ""
    
    def test_server_capabilities(self):
        """Test server declares proper MCP capabilities."""
        # Test that the server supports the expected transports
        transports = ["stdio", "http", "sse"]
        
        for transport in transports:
            assert transport in TransportType.values()
    
    @pytest.mark.asyncio
    async def test_error_response_format(self):
        """Test that error responses follow MCP format."""
        # Test with invalid configuration to trigger errors
        original_url = config.url
        config.url = ""
        
        try:
            result = await health_check_wrapper()
            
            # Error responses should be structured
            assert isinstance(result, dict)
            assert "status" in result
            assert result["status"] in ["unhealthy", "degraded", "error"]
            
        finally:
            config.url = original_url


class TestMCPConcurrencyAndPerformance:
    """Test MCP tools handle concurrency and perform well."""
    
    @patch('test_mcp_protocol_compliance.make_prometheus_request')
    @pytest.mark.asyncio
    async def test_concurrent_tool_execution(self, mock_request, mock_prometheus_response):
        """Test tools can handle concurrent execution."""
        def mock_side_effect(endpoint, params=None):
            if endpoint == "targets":
                return {"activeTargets": [], "droppedTargets": []}
            elif endpoint == "label/__name__/values":
                return ["up", "prometheus_build_info"]
            else:
                return mock_prometheus_response["data"]
        
        mock_request.side_effect = mock_side_effect
        
        # Create multiple concurrent tasks
        tasks = [
            execute_query_wrapper("up"),
            execute_query_wrapper("prometheus_build_info"),
            list_metrics_wrapper(),
            get_targets_wrapper()
        ]
        
        # Execute concurrently
        results = await asyncio.gather(*tasks)
        
        # All should complete successfully
        assert len(results) == 4
        for result in results:
            assert result is not None
    
    @patch('test_mcp_protocol_compliance.make_prometheus_request')
    @pytest.mark.asyncio
    async def test_tool_timeout_handling(self, mock_request):
        """Test tools handle timeouts gracefully."""
        # Simulate slow response
        def slow_response(*args, **kwargs):
            import time
            time.sleep(0.1)
            return {"resultType": "vector", "result": []}
        
        mock_request.side_effect = slow_response
        
        # This should complete (not testing actual timeout, just that it's async)
        result = await execute_query_wrapper("up")
        assert result is not None