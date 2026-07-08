#!/usr/bin/env python

import os
import json
from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass
import time
from datetime import datetime, timedelta
from enum import Enum

import dotenv
import requests
from fastmcp import FastMCP, Context
from prometheus_mcp_server.logging_config import get_logger

dotenv.load_dotenv()

# Get tool prefix from environment (empty string for backward compatibility)
TOOL_PREFIX = os.environ.get("TOOL_PREFIX", "")

def _tool_name(name: str) -> str:
    """Build tool name with optional prefix."""
    return f"{TOOL_PREFIX}_{name}" if TOOL_PREFIX else name

# Include prefix in MCP server name if set
mcp_name = f"Prometheus MCP ({TOOL_PREFIX})" if TOOL_PREFIX else "Prometheus MCP"
mcp = FastMCP(mcp_name)

from starlette.requests import Request
from starlette.responses import JSONResponse

@mcp.custom_route("/health", methods=["GET"])
async def health_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})

# Cache for metrics list to improve completion performance
_metrics_cache = {"data": None, "timestamp": 0}
_CACHE_TTL = 300  # 5 minutes

def clear_metrics_cache():
    """Reset the metrics cache, forcing the next fetch to hit Prometheus."""
    _metrics_cache["data"] = None
    _metrics_cache["timestamp"] = 0

# Get logger instance
logger = get_logger()

# Health check tool for Docker containers and monitoring
@mcp.tool(
    name=_tool_name("health_check"),
    description="Health check endpoint for container monitoring and status verification",
    annotations={
        "title": "Health Check",
        "icon": "❤️",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def health_check() -> Dict[str, Any]:
    """Return health status of the MCP server and Prometheus connection.

    Returns:
        Health status including service information, configuration, and connectivity
    """
    try:
        health_status = {
            "status": "healthy",
            "service": "prometheus-mcp-server",
            "version": "1.6.1",
            "timestamp": datetime.utcnow().isoformat(),
            "transport": config.mcp_server_config.mcp_server_transport if config.mcp_server_config else "stdio",
            "configuration": {
                "prometheus_url_configured": bool(config.url),
                "authentication_configured": bool(config.username or config.token or config.client_cert),
                "org_id_configured": bool(config.org_id)
            }
        }
        
        # Test Prometheus connectivity if configured
        if config.url:
            try:
                # Quick connectivity test
                make_prometheus_request("query", params={"query": "up", "time": str(int(time.time()))})
                health_status["prometheus_connectivity"] = "healthy"
                health_status["prometheus_url"] = config.url
            except Exception as e:
                health_status["prometheus_connectivity"] = "unhealthy"
                health_status["prometheus_error"] = str(e)
                health_status["status"] = "degraded"
        else:
            health_status["status"] = "unhealthy"
            health_status["error"] = "PROMETHEUS_URL not configured"
        
        logger.info("Health check completed", status=health_status["status"])
        return health_status
        
    except Exception as e:
        logger.error("Health check failed", error=str(e))
        return {
            "status": "unhealthy",
            "service": "prometheus-mcp-server",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }


class TransportType(str, Enum):
    """Supported MCP server transport types."""

    STDIO = "stdio"
    HTTP = "http"
    SSE = "sse"

    @classmethod
    def values(cls) -> list[str]:
        """Get all valid transport values."""
        return [transport.value for transport in cls]

@dataclass
class MCPServerConfig:
    """Global Configuration for MCP."""
    mcp_server_transport: TransportType = None
    mcp_bind_host: str = None
    mcp_bind_port: int = None
    stateless_http: bool = False

    def __post_init__(self):
        """Validate mcp configuration."""
        if not self.mcp_server_transport:
            raise ValueError("MCP SERVER TRANSPORT is required")
        if not self.mcp_bind_host:
            raise ValueError(f"MCP BIND HOST is required")
        if not self.mcp_bind_port:
            raise ValueError(f"MCP BIND PORT is required")

@dataclass
class PrometheusConfig:
    url: str
    url_ssl_verify: bool = True
    disable_prometheus_links: bool = False
    # Optional credentials
    username: Optional[str] = None
    password: Optional[str] = None
    token: Optional[str] = None
    # Optional Org ID for multi-tenant setups
    org_id: Optional[str] = None
    # Optional client TLS certificate for mutual TLS authentication
    client_cert: Optional[str] = None
    client_key: Optional[str] = None
    # Optional Custom MCP Server Configuration
    mcp_server_config: Optional[MCPServerConfig] = None
    # Optional custom headers for Prometheus requests
    custom_headers: Optional[Dict[str, str]] = None
    # Request timeout in seconds to prevent hanging requests (DDoS protection)
    request_timeout: int = 30

config = PrometheusConfig(
    url=os.environ.get("PROMETHEUS_URL", ""),
    url_ssl_verify=os.environ.get("PROMETHEUS_URL_SSL_VERIFY", "True").lower() in ("true", "1", "yes"),
    disable_prometheus_links=os.environ.get("PROMETHEUS_DISABLE_LINKS", "False").lower() in ("true", "1", "yes"),
    username=os.environ.get("PROMETHEUS_USERNAME", ""),
    password=os.environ.get("PROMETHEUS_PASSWORD", ""),
    token=os.environ.get("PROMETHEUS_TOKEN", ""),
    org_id=os.environ.get("ORG_ID", ""),
    mcp_server_config=MCPServerConfig(
        mcp_server_transport=os.environ.get("PROMETHEUS_MCP_SERVER_TRANSPORT", "stdio").lower(),
        mcp_bind_host=os.environ.get("PROMETHEUS_MCP_BIND_HOST", "127.0.0.1"),
        mcp_bind_port=int(os.environ.get("PROMETHEUS_MCP_BIND_PORT", "8080")),
        stateless_http=os.environ.get("PROMETHEUS_MCP_STATELESS_HTTP", "False").lower() in ("true", "1", "yes"),
    ),
    client_cert=os.environ.get("PROMETHEUS_CLIENT_CERT", "") or None,
    client_key=os.environ.get("PROMETHEUS_CLIENT_KEY", "") or None,
    custom_headers=json.loads(os.environ.get("PROMETHEUS_CUSTOM_HEADERS")) if os.environ.get("PROMETHEUS_CUSTOM_HEADERS") else None,
    request_timeout=int(os.environ.get("PROMETHEUS_REQUEST_TIMEOUT", "30")),
)

def get_prometheus_auth():
    """Get authentication for Prometheus based on provided credentials."""
    if config.token:
        return {"Authorization": f"Bearer {config.token}"}
    elif config.username and config.password:
        return requests.auth.HTTPBasicAuth(config.username, config.password)
    return None

def make_prometheus_request(endpoint, params=None):
    """Make a request to the Prometheus API with proper authentication and headers."""
    if not config.url:
        logger.error("Prometheus configuration missing", error="PROMETHEUS_URL not set")
        raise ValueError("Prometheus configuration is missing. Please set PROMETHEUS_URL environment variable.")
    if not config.url_ssl_verify:
        logger.warning("SSL certificate verification is disabled. This is insecure and should not be used in production environments.", endpoint=endpoint)

    url = f"{config.url.rstrip('/')}/api/v1/{endpoint}"
    url_ssl_verify = config.url_ssl_verify
    auth = get_prometheus_auth()
    headers = {}

    if isinstance(auth, dict):  # Token auth is passed via headers
        headers.update(auth)
        auth = None  # Clear auth for requests.get if it's already in headers
    
    # Add OrgID header if specified
    if config.org_id:
        headers["X-Scope-OrgID"] = config.org_id

    if config.custom_headers:
        headers.update(config.custom_headers)

    # Build client certificate tuple for mutual TLS authentication
    client_cert = None
    if config.client_cert:
        if config.client_key:
            client_cert = (config.client_cert, config.client_key)
        else:
            client_cert = config.client_cert

    try:
        logger.debug("Making Prometheus API request", endpoint=endpoint, url=url, params=params, headers=headers, timeout=config.request_timeout)

        # Make the request with appropriate headers, auth, and timeout (DDoS protection)
        response = requests.get(url, params=params, auth=auth, headers=headers, verify=url_ssl_verify, cert=client_cert, timeout=config.request_timeout)

        response.raise_for_status()
        result = response.json()
        
        if result["status"] != "success":
            error_msg = result.get('error', 'Unknown error')
            logger.error("Prometheus API returned error", endpoint=endpoint, error=error_msg, status=result["status"])
            raise ValueError(f"Prometheus API error: {error_msg}")
        
        data_field = result.get("data", {})
        if isinstance(data_field, dict):
            result_type = data_field.get("resultType")
        else:
            result_type = "list"
        logger.debug("Prometheus API request successful", endpoint=endpoint, result_type=result_type)
        return result["data"]
    
    except requests.exceptions.RequestException as e:
        logger.error("HTTP request to Prometheus failed", endpoint=endpoint, url=url, error=str(e), error_type=type(e).__name__)
        raise
    except json.JSONDecodeError as e:
        logger.error("Failed to parse Prometheus response as JSON", endpoint=endpoint, url=url, error=str(e))
        raise ValueError(f"Invalid JSON response from Prometheus: {str(e)}")
    except Exception as e:
        logger.error("Unexpected error during Prometheus request", endpoint=endpoint, url=url, error=str(e), error_type=type(e).__name__)
        raise

def get_cached_metrics() -> List[str]:
    """Get metrics list with caching to improve completion performance.

    This helper function is available for future completion support when
    FastMCP implements the completion capability. For now, it can be used
    internally to optimize repeated metric list requests.
    """
    current_time = time.time()

    # snapshot for clarity
    cached_data = _metrics_cache["data"]
    cached_timestamp = _metrics_cache["timestamp"]

    if cached_data is not None and (current_time - cached_timestamp) < _CACHE_TTL:
        logger.debug("Using cached metrics list", cache_age=current_time - cached_timestamp)
        return cached_data

    # Fetch fresh metrics
    data = make_prometheus_request("label/__name__/values")
    _metrics_cache["data"] = data
    _metrics_cache["timestamp"] = current_time
    logger.debug("Refreshed metrics cache", metric_count=len(data))
    return data

# Note: Argument completions will be added when FastMCP supports the completion
# capability. The get_cached_metrics() function above is ready for that integration.

@mcp.tool(
    name=_tool_name("execute_query"),
    description="Execute a PromQL instant query against Prometheus",
    annotations={
        "title": "Execute PromQL Query",
        "icon": "📊",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def execute_query(query: str, time: Optional[str] = None) -> Dict[str, Any]:
    """Execute an instant query against Prometheus.

    Args:
        query: PromQL query string
        time: Optional RFC3339 or Unix timestamp (default: current time)

    Returns:
        Query result with type (vector, matrix, scalar, string) and values
    """
    params = {"query": query}
    if time:
        params["time"] = time
    
    logger.info("Executing instant query", query=query, time=time)
    data = make_prometheus_request("query", params=params)

    result = {
        "resultType": data["resultType"],
        "result": data["result"]
    }

    if not config.disable_prometheus_links:
        from urllib.parse import urlencode
        ui_params = {"g0.expr": query, "g0.tab": "0"}
        if time:
            ui_params["g0.moment_input"] = time
        prometheus_ui_link = f"{config.url.rstrip('/')}/graph?{urlencode(ui_params)}"
        result["links"] = [{
            "href": prometheus_ui_link,
            "rel": "prometheus-ui",
            "title": "View in Prometheus UI"
        }]

    logger.info("Instant query completed",
                query=query,
                result_type=data["resultType"],
                result_count=len(data["result"]) if isinstance(data["result"], list) else 1)

    return result

@mcp.tool(
    name=_tool_name("execute_range_query"),
    description="Execute a PromQL range query with start time, end time, and step interval",
    annotations={
        "title": "Execute PromQL Range Query",
        "icon": "📈",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def execute_range_query(query: str, start: str, end: str, step: str, ctx: Context | None = None) -> Dict[str, Any]:
    """Execute a range query against Prometheus.

    Args:
        query: PromQL query string
        start: Start time as RFC3339 or Unix timestamp
        end: End time as RFC3339 or Unix timestamp
        step: Query resolution step width (e.g., '15s', '1m', '1h')

    Returns:
        Range query result with type (usually matrix) and values over time
    """
    params = {
        "query": query,
        "start": start,
        "end": end,
        "step": step
    }

    logger.info("Executing range query", query=query, start=start, end=end, step=step)

    # Report progress if context available
    if ctx:
        await ctx.report_progress(progress=0, total=100, message="Initiating range query...")

    data = make_prometheus_request("query_range", params=params)

    # Report progress
    if ctx:
        await ctx.report_progress(progress=50, total=100, message="Processing query results...")

    result = {
        "resultType": data["resultType"],
        "result": data["result"]
    }

    if not config.disable_prometheus_links:
        from urllib.parse import urlencode
        ui_params = {
            "g0.expr": query,
            "g0.tab": "0",
            "g0.range_input": f"{start} to {end}",
            "g0.step_input": step
        }
        prometheus_ui_link = f"{config.url.rstrip('/')}/graph?{urlencode(ui_params)}"
        result["links"] = [{
            "href": prometheus_ui_link,
            "rel": "prometheus-ui",
            "title": "View in Prometheus UI"
        }]

    # Report completion
    if ctx:
        await ctx.report_progress(progress=100, total=100, message="Range query completed")

    logger.info("Range query completed",
                query=query,
                result_type=data["resultType"],
                result_count=len(data["result"]) if isinstance(data["result"], list) else 1)

    return result

@mcp.tool(
    name=_tool_name("list_metrics"),
    description="List all available metrics in Prometheus with optional pagination support",
    annotations={
        "title": "List Available Metrics",
        "icon": "📋",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def list_metrics(
    limit: Optional[int] = None,
    offset: int = 0,
    filter_pattern: Optional[str] = None,
    ctx: Context | None = None,
    refresh_cache: bool = False,
) -> Dict[str, Any]:
    """Retrieve a list of all metric names available in Prometheus.

    Args:
        limit: Maximum number of metrics to return (default: all metrics)
        offset: Number of metrics to skip for pagination (default: 0)
        filter_pattern: Optional substring to filter metric names (case-insensitive)
        refresh_cache: Force a cache refresh to pick up newly scraped metrics (default: False)

    Returns:
        Dictionary containing:
        - metrics: List of metric names
        - total_count: Total number of metrics (before pagination)
        - returned_count: Number of metrics returned
        - offset: Current offset
        - has_more: Whether more metrics are available
    """
    logger.info("Listing available metrics", limit=limit, offset=offset, filter_pattern=filter_pattern, refresh_cache=refresh_cache)

    # Report progress if context available
    if ctx:
        await ctx.report_progress(progress=0, total=100, message="Fetching metrics list...")

    if refresh_cache:
        clear_metrics_cache()

    data = get_cached_metrics()

    if ctx:
        await ctx.report_progress(progress=50, total=100, message=f"Processing {len(data)} metrics...")

    # Apply filter if provided
    if filter_pattern:
        filtered_data = [m for m in data if filter_pattern.lower() in m.lower()]
        logger.debug("Applied filter", original_count=len(data), filtered_count=len(filtered_data), pattern=filter_pattern)
        data = filtered_data

    total_count = len(data)

    # Apply pagination
    start_idx = offset
    end_idx = offset + limit if limit is not None else len(data)
    paginated_data = data[start_idx:end_idx]

    result = {
        "metrics": paginated_data,
        "total_count": total_count,
        "returned_count": len(paginated_data),
        "offset": offset,
        "has_more": end_idx < total_count
    }

    if ctx:
        await ctx.report_progress(progress=100, total=100, message=f"Retrieved {len(paginated_data)} of {total_count} metrics")

    logger.info("Metrics list retrieved",
                total_count=total_count,
                returned_count=len(paginated_data),
                offset=offset,
                has_more=result["has_more"])

    return result

def _coerce_metadata_entries(value: Any) -> List[Dict[str, Any]]:
    """Normalize metadata value into a list of metadata dictionaries."""
    if isinstance(value, list):
        return [entry for entry in value if isinstance(entry, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _normalize_metadata_map(raw_data: Any) -> Dict[str, List[Dict[str, Any]]]:
    """Normalize diverse metadata response shapes into {metric_name: [entries]}."""
    if isinstance(raw_data, dict):
        if "metadata" in raw_data:
            return _normalize_metadata_map(raw_data["metadata"])
        if "data" in raw_data:
            return _normalize_metadata_map(raw_data["data"])

        normalized: Dict[str, List[Dict[str, Any]]] = {}
        for metric_name, entries in raw_data.items():
            if not isinstance(metric_name, str):
                continue
            coerced_entries = _coerce_metadata_entries(entries)
            if coerced_entries:
                normalized[metric_name] = coerced_entries

        if normalized:
            return normalized

        metric_name = raw_data.get("metric")
        if isinstance(metric_name, str):
            return {metric_name: [raw_data]}

    if isinstance(raw_data, list):
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for entry in raw_data:
            if not isinstance(entry, dict):
                continue
            metric_name = entry.get("metric")
            if not isinstance(metric_name, str):
                continue
            grouped.setdefault(metric_name, []).append(entry)
        return grouped

    return {}


def _metadata_matches_pattern(metric_name: str, entries: List[Dict[str, Any]], pattern: str) -> bool:
    """Return True when pattern matches metric name or metadata text fields."""
    lowered_pattern = pattern.lower()
    if lowered_pattern in metric_name.lower():
        return True

    for entry in entries:
        for value in entry.values():
            if isinstance(value, str) and lowered_pattern in value.lower():
                return True

    return False


@mcp.tool(
    name=_tool_name("get_metric_metadata"),
    description=(
        "Get metadata (type, help, unit) for metrics. "
        "Returns all metric metadata when no metric name is provided. "
        "Use filter_pattern to search metric names and descriptions."
    ),
    annotations={
        "title": "Get Metric Metadata",
        "icon": "ℹ️",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def get_metric_metadata(
    metric: Optional[str] = None,
    filter_pattern: Optional[str] = None,
    limit: Optional[int] = None,
    offset: int = 0,
) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """Get metadata for one metric or bulk metadata for all metrics.

    Args:
        metric: Optional metric name. If provided, returns legacy list format.
        filter_pattern: Optional substring filter on metric name and descriptions.
        limit: Maximum number of metrics to return in bulk mode.
        offset: Number of metrics to skip in bulk mode.

    Returns:
        If metric is provided: list of metadata entries for that metric.
        If metric is not provided: dict with filtered metadata and pagination info.
    """
    logger.info("Retrieving metric metadata", metric=metric, filter_pattern=filter_pattern, limit=limit, offset=offset)

    params = {"metric": metric} if metric else None
    raw_data = make_prometheus_request("metadata", params=params)

    metadata_by_metric = _normalize_metadata_map(raw_data)

    # Fallback for atypical single-metric response formats.
    if metric and metric not in metadata_by_metric:
        fallback_entries = _coerce_metadata_entries(raw_data)
        if fallback_entries:
            metadata_by_metric[metric] = fallback_entries

    if filter_pattern:
        metadata_by_metric = {
            metric_name: entries
            for metric_name, entries in metadata_by_metric.items()
            if _metadata_matches_pattern(metric_name, entries, filter_pattern)
        }

    if metric:
        metric_entries = metadata_by_metric.get(metric, [])
        logger.info("Metric metadata retrieved", metric=metric, metadata_count=len(metric_entries))
        return metric_entries

    metric_names = list(metadata_by_metric.keys())
    total_count = len(metric_names)
    start_idx = offset
    end_idx = offset + limit if limit is not None else total_count
    selected_metric_names = metric_names[start_idx:end_idx]
    paginated_metadata = {name: metadata_by_metric[name] for name in selected_metric_names}

    result = {
        "metadata": paginated_metadata,
        "total_count": total_count,
        "returned_count": len(paginated_metadata),
        "offset": offset,
        "has_more": end_idx < total_count,
    }

    logger.info(
        "Bulk metric metadata retrieved",
        total_count=total_count,
        returned_count=result["returned_count"],
        offset=offset,
        has_more=result["has_more"],
    )

    return result

@mcp.tool(
    name=_tool_name("get_targets"),
    description="Get information about all scrape targets",
    annotations={
        "title": "Get Scrape Targets",
        "icon": "🎯",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def get_targets() -> Dict[str, List[Dict[str, Any]]]:
    """Get information about all Prometheus scrape targets.

    Returns:
        Dictionary with active and dropped targets information
    """
    logger.info("Retrieving scrape targets information")
    data = make_prometheus_request("targets")
    
    result = {
        "activeTargets": data["activeTargets"],
        "droppedTargets": data["droppedTargets"]
    }
    
    logger.info("Scrape targets retrieved", 
                active_targets=len(data["activeTargets"]), 
                dropped_targets=len(data["droppedTargets"]))
    
    return result

@mcp.tool(
    name=_tool_name("list_alerts"),
    description="Get all active alerts from Prometheus with their state, labels, and annotations",
    annotations={
        "title": "List Active Alerts",
        "icon": "🚨",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def list_alerts() -> Dict[str, Any]:
    """Get all currently active alerts from Prometheus.

    Returns:
        Dictionary containing:
        - alerts: List of active alerts with labels, annotations, state, and activeAt
        - alert_count: Number of active alerts
    """
    logger.info("Retrieving active alerts")
    data = make_prometheus_request("alerts", params=None)

    alerts = data.get("alerts", [])
    result = {
        "alerts": alerts,
        "alert_count": len(alerts)
    }

    logger.info("Active alerts retrieved", alert_count=len(alerts))
    return result

@mcp.tool(
    name=_tool_name("list_rules"),
    description="Get alerting and recording rules with their health, state, and evaluation info",
    annotations={
        "title": "List Alerting & Recording Rules",
        "icon": "📜",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def list_rules(
    type: Optional[str] = None,
    rule_name: Optional[List[str]] = None,
    rule_group: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Get alerting and recording rules currently loaded in Prometheus.

    Args:
        type: Optional rule type filter, either 'alert' or 'record'
        rule_name: Optional list of rule names to filter by (forwarded to the server
            on Prometheus >= 2.44 and re-applied client-side for older or
            Prometheus-compatible backends that ignore the parameter)
        rule_group: Optional list of rule group names to filter by (same fallback)

    Returns:
        Dictionary containing:
        - groups: List of rule groups with their rules
        - group_count: Number of rule groups returned
    """
    if type is not None and type not in ("alert", "record"):
        raise ValueError(f"Invalid rule type: '{type}'. Must be 'alert' or 'record'.")

    logger.info("Retrieving rules", type=type, rule_name=rule_name, rule_group=rule_group)

    params: Dict[str, Any] = {}
    if type:
        params["type"] = type
    if rule_name:
        params["rule_name[]"] = rule_name
    if rule_group:
        params["rule_group[]"] = rule_group

    data = make_prometheus_request("rules", params=params or None)

    groups = data.get("groups", [])
    # Prometheus < 2.44 and some compatible backends (Thanos, VictoriaMetrics) silently
    # ignore rule_name[]/rule_group[], so re-apply the filters client-side. This is a
    # no-op when the server already filtered.
    if rule_group:
        wanted_groups = set(rule_group)
        groups = [g for g in groups if g.get("name") in wanted_groups]
    if rule_name:
        wanted_rules = set(rule_name)
        groups = [
            {**g, "rules": [r for r in g.get("rules", []) if r.get("name") in wanted_rules]}
            for g in groups
        ]
        groups = [g for g in groups if g["rules"]]

    result = {
        "groups": groups,
        "group_count": len(groups)
    }

    logger.info("Rules retrieved", group_count=len(groups))
    return result

@mcp.tool(
    name=_tool_name("list_label_names"),
    description="List all label names, optionally restricted to series matching selectors and a time range",
    annotations={
        "title": "List Label Names",
        "icon": "🏷️",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def list_label_names(
    match: Optional[List[str]] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> Dict[str, Any]:
    """List label names known to Prometheus.

    Args:
        match: Optional list of series selectors (e.g. ['up', 'node_cpu_seconds_total{job="node"}'])
        start: Optional start time as RFC3339 or Unix timestamp
        end: Optional end time as RFC3339 or Unix timestamp

    Returns:
        Dictionary containing:
        - labels: List of label names
        - count: Number of label names returned
    """
    logger.info("Listing label names", match=match, start=start, end=end)

    params: Dict[str, Any] = {}
    if match:
        params["match[]"] = match
    if start:
        params["start"] = start
    if end:
        params["end"] = end

    data = make_prometheus_request("labels", params=params or None)

    result = {
        "labels": data,
        "count": len(data)
    }

    logger.info("Label names retrieved", count=len(data))
    return result

def _is_legacy_label_rune(ch: str, index: int) -> bool:
    """Check whether a character is valid in a classic Prometheus label name."""
    return (
        ch == "_"
        or "a" <= ch <= "z"
        or "A" <= ch <= "Z"
        or (index > 0 and "0" <= ch <= "9")
    )


def _escape_label_name(name: str) -> str:
    """Escape a label name for use in a URL path using Prometheus 'values' escaping.

    Names valid under the classic charset ([a-zA-Z_][a-zA-Z0-9_]*) pass through
    unchanged. UTF-8 names (legal since Prometheus 3.x) are escaped to the U__ form
    the API requires for path segments, since characters like '/' would otherwise
    change the request path.
    """
    if all(_is_legacy_label_rune(ch, i) for i, ch in enumerate(name)):
        return name

    escaped = ["U__"]
    for index, ch in enumerate(name):
        if ch == "_":
            escaped.append("__")
        elif _is_legacy_label_rune(ch, index):
            escaped.append(ch)
        else:
            escaped.append(f"_{ord(ch):x}_")
    return "".join(escaped)


@mcp.tool(
    name=_tool_name("list_label_values"),
    description="List all values for a label, optionally restricted to series matching selectors and a time range",
    annotations={
        "title": "List Label Values",
        "icon": "🔤",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def list_label_values(
    label_name: str,
    match: Optional[List[str]] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> Dict[str, Any]:
    """List values for a specific label name.

    Args:
        label_name: The label name to retrieve values for (e.g. 'job', 'instance')
        match: Optional list of series selectors to restrict the values
        start: Optional start time as RFC3339 or Unix timestamp
        end: Optional end time as RFC3339 or Unix timestamp

    Returns:
        Dictionary containing:
        - values: List of values for the label
        - count: Number of values returned
    """
    if not label_name:
        raise ValueError("label_name must not be empty")

    logger.info("Listing label values", label_name=label_name, match=match, start=start, end=end)

    params: Dict[str, Any] = {}
    if match:
        params["match[]"] = match
    if start:
        params["start"] = start
    if end:
        params["end"] = end

    data = make_prometheus_request(f"label/{_escape_label_name(label_name)}/values", params=params or None)

    result = {
        "values": data,
        "count": len(data)
    }

    logger.info("Label values retrieved", label_name=label_name, count=len(data))
    return result

@mcp.tool(
    name=_tool_name("find_series"),
    description="Find time series matching label selectors, with optional time range and result limit",
    annotations={
        "title": "Find Series",
        "icon": "🔍",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def find_series(
    match: List[str],
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Find time series by label matchers.

    Args:
        match: List of series selectors; at least one is required
            (e.g. ['up', 'process_start_time_seconds{job="prometheus"}'])
        start: Optional start time as RFC3339 or Unix timestamp
        end: Optional end time as RFC3339 or Unix timestamp
        limit: Maximum number of series to return; must be positive (default: all)

    Returns:
        Dictionary containing:
        - series: List of label sets identifying matching series
        - returned_count: Number of series returned
        - has_more: Whether more series matched than were returned
    """
    if not match:
        raise ValueError("find_series requires at least one series selector in 'match'")
    if limit is not None and limit < 1:
        raise ValueError("limit must be a positive number")

    logger.info("Finding series", match=match, start=start, end=end, limit=limit)

    params: Dict[str, Any] = {"match[]": match}
    if start:
        params["start"] = start
    if end:
        params["end"] = end
    if limit is not None:
        # Ask for one extra series so has_more can be derived from a bounded fetch.
        # Servers older than 2.53 ignore the param; the slice below still applies.
        params["limit"] = limit + 1

    data = make_prometheus_request("series", params=params)

    series = data[:limit] if limit is not None else data

    result = {
        "series": series,
        "returned_count": len(series),
        "has_more": len(series) < len(data)
    }

    logger.info("Series retrieved", returned_count=len(series), has_more=result["has_more"])
    return result

@mcp.tool(
    name=_tool_name("get_runtime_info"),
    description="Get Prometheus runtime information such as start time, config reload status, goroutine count, and storage retention",
    annotations={
        "title": "Get Runtime Info",
        "icon": "⚙️",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def get_runtime_info() -> Dict[str, Any]:
    """Get runtime information about the Prometheus server.

    Returns:
        Runtime properties including start time, working directory, config reload
        status, goroutine count, and storage retention
    """
    logger.info("Retrieving runtime info")
    data = make_prometheus_request("status/runtimeinfo")

    logger.info("Runtime info retrieved")
    return data

@mcp.tool(
    name=_tool_name("get_build_info"),
    description="Get Prometheus build information such as version, revision, and Go version",
    annotations={
        "title": "Get Build Info",
        "icon": "🏗️",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def get_build_info() -> Dict[str, Any]:
    """Get build information about the Prometheus server.

    Returns:
        Build properties including version, revision, branch, and Go version
    """
    logger.info("Retrieving build info")
    data = make_prometheus_request("status/buildinfo")

    logger.info("Build info retrieved", version=data.get("version"))
    return data

@mcp.tool(
    name=_tool_name("get_tsdb_stats"),
    description="Get TSDB cardinality statistics: head series counts and top metrics by series count",
    annotations={
        "title": "Get TSDB Stats",
        "icon": "💾",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
    }
)
async def get_tsdb_stats(limit: Optional[int] = None) -> Dict[str, Any]:
    """Get TSDB usage and cardinality statistics from Prometheus.

    Args:
        limit: Maximum number of items to return per stats list; must be positive (default: 10)

    Returns:
        TSDB statistics including head block stats and cardinality breakdowns
        (series count by metric name, label pairs, and memory usage)
    """
    if limit is not None and limit < 1:
        raise ValueError("limit must be a positive number")

    logger.info("Retrieving TSDB stats", limit=limit)

    params = {"limit": limit} if limit is not None else None
    data = make_prometheus_request("status/tsdb", params=params)

    logger.info("TSDB stats retrieved")
    return data

if __name__ == "__main__":
    logger.info("Starting Prometheus MCP Server", mode="direct")
    mcp.run()
