#!/usr/bin/env python

import inspect
import os
import json
from typing import Annotated, Any, Dict, List, Optional, Union
from dataclasses import dataclass
import time
from datetime import datetime, timedelta
from enum import Enum

import dotenv
import requests
from fastmcp import FastMCP, Context
from pydantic import Field
from prometheus_mcp_server.logging_config import get_logger
from prometheus_mcp_server.spec2026.headers import is_safe_plain_ascii
from prometheus_mcp_server.spec2026.otel import get_trace_headers

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
    # MCP 2026-07-28 compatibility layer (see prometheus_mcp_server.spec2026)
    spec_2026_enabled: bool = True
    # Advertised cache lifetime in milliseconds for cacheable result envelopes.
    # Deliberately separate from _CACHE_TTL, which governs the in-process
    # metrics cache and is measured in seconds.
    cache_ttl_ms: int = 300000
    # Advertised cache scope for cacheable result envelopes: "public" or "private"
    cache_scope: str = "public"
    # Opt-in strict validation of the Mcp-* request headers against the body
    strict_headers: bool = False
    # Opt-in: let a per-call org_id override an operator-configured ORG_ID.
    # Off by default because X-Scope-OrgID is the only tenancy boundary in
    # Mimir/Cortex/Thanos, so honouring a client-supplied tenant when the
    # operator has pinned one is a cross-tenant data-access hole.
    allow_org_id_override: bool = False


def _env_int(name: str, default: int) -> int:
    """Read an integer environment variable without ever failing at import.

    A malformed value must not take the process down before any logging is
    configured, so it is reported and the documented default is used instead.

    Args:
        name: Environment variable to read.
        default: Value to fall back to when the variable is absent or unusable.

    Returns:
        The parsed integer, or ``default``.
    """
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid integer environment variable, using default",
            variable=name,
            value=raw,
            default=default,
        )
        return default


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
    spec_2026_enabled=os.environ.get("PROMETHEUS_MCP_SPEC_2026", "True").lower() in ("true", "1", "yes"),
    cache_ttl_ms=_env_int("PROMETHEUS_MCP_CACHE_TTL_MS", 300000),
    cache_scope=os.environ.get("PROMETHEUS_MCP_CACHE_SCOPE", "public").lower(),
    strict_headers=os.environ.get("PROMETHEUS_MCP_STRICT_HEADERS", "False").lower() in ("true", "1", "yes"),
    allow_org_id_override=os.environ.get("PROMETHEUS_MCP_ALLOW_ORG_ID_OVERRIDE", "False").lower() in ("true", "1", "yes"),
)

def get_prometheus_auth():
    """Get authentication for Prometheus based on provided credentials."""
    if config.token:
        return {"Authorization": f"Bearer {config.token}"}
    elif config.username and config.password:
        return requests.auth.HTTPBasicAuth(config.username, config.password)
    return None

def resolve_org_id(org_id: Optional[str]) -> Optional[str]:
    """Decide which tenant id, if any, belongs on the outbound request.

    ``X-Scope-OrgID`` is the *only* tenancy boundary in Mimir/Cortex/Thanos, and
    the caller here is typically an LLM acting on untrusted content, so a
    client-supplied tenant must never silently displace one the operator pinned
    via ``ORG_ID``. The precedence is therefore:

    1. An operator-configured ``ORG_ID`` wins, unless the operator additionally
       set ``PROMETHEUS_MCP_ALLOW_ORG_ID_OVERRIDE``.
    2. With no ``ORG_ID`` configured, a per-call value is used as-is.
    3. Either way the value must be safe plain ASCII, so a CR/LF or a non-ASCII
       smuggling attempt never reaches an outbound header.

    Args:
        org_id: Tenant id supplied with this call, or None.

    Returns:
        The tenant id to send, or None when no ``X-Scope-OrgID`` should be set.
    """
    configured = config.org_id or None
    if not org_id:
        return configured

    if not isinstance(org_id, str) or not is_safe_plain_ascii(org_id):
        logger.warning(
            "Rejecting unsafe per-call org_id; falling back to the configured tenant",
            org_id_type=type(org_id).__name__,
        )
        return configured

    if configured and not config.allow_org_id_override:
        logger.warning(
            "Ignoring per-call org_id because ORG_ID is configured",
            switch="PROMETHEUS_MCP_ALLOW_ORG_ID_OVERRIDE",
        )
        return configured

    logger.info("Using per-call tenant for this Prometheus request", org_id=org_id)
    return org_id


def make_prometheus_request(endpoint, params=None, org_id=None):
    """Make a request to the Prometheus API with proper authentication and headers.

    Args:
        endpoint: Prometheus API endpoint below /api/v1/ (e.g. 'query', 'targets')
        params: Optional query string parameters
        org_id: Optional per-call tenant id. Only honoured when the operator
            configured no ORG_ID, or explicitly opted into overrides via
            PROMETHEUS_MCP_ALLOW_ORG_ID_OVERRIDE. See :func:`resolve_org_id`.

    Returns:
        The 'data' member of the Prometheus API response
    """
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
    
    # Add OrgID header if specified. An operator-configured ORG_ID always wins
    # over a per-call value unless the operator opted into overrides.
    effective_org_id = resolve_org_id(org_id)
    if effective_org_id:
        headers["X-Scope-OrgID"] = effective_org_id

    # W3C trace context taken from the incoming request's _meta (2026-07-28
    # minor change #2). Merged *before* custom_headers so operator-configured
    # headers always win: a per-request value must never silently override
    # static deployment configuration. Empty for clients that send no trace
    # context, so this is a no-op on the 2025-11-25 path.
    headers.update(get_trace_headers())

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

def _org_id_json_schema(schema: Dict[str, Any]) -> None:
    """Attach the 2026-07-28 x-mcp-header annotation to the org_id property.

    Pydantic renders Optional[str] as an anyOf union, but the specification only
    permits the annotation on a primitive integer/string/boolean property, so
    the union is collapsed to a plain string first. Pydantic's ``default: null``
    is dropped along with it: a null default does not validate against the
    declared ``string`` type, and a client that checks its own arguments against
    the published inputSchema (or that materialises declared defaults into the
    argument object) would reject a call the server happily accepts.

    Args:
        schema: The generated JSON Schema fragment for the property, edited in place.

    Returns:
        None
    """
    schema.pop("anyOf", None)
    schema.pop("default", None)
    schema["type"] = "string"
    schema["x-mcp-header"] = "Org-Id"


# Type of the optional multi-tenant override. An MCP 2026-07-28 client may mirror
# the value in the Mcp-Param-Org-Id HTTP header; older clients simply pass it as a
# normal argument. Only honoured when the operator configured no ORG_ID, or opted
# into overrides -- see resolve_org_id.
#
# The parameter defaults to "" rather than None: FastMCP publishes the signature
# default in the inputSchema *after* json_schema_extra has run, so a None default
# would advertise `default: null` on a property this annotation forces to
# `type: "string"`. A client that validates its arguments against the advertised
# schema (or that materialises declared defaults) would then reject the very
# value the schema told it to send. "" validates, and reads as "no tenant".
OrgIdParam = Annotated[
    Optional[str],
    Field(
        description=(
            "Tenant id sent as X-Scope-OrgID. Ignored when the server has ORG_ID "
            "configured, unless the operator enabled PROMETHEUS_MCP_ALLOW_ORG_ID_OVERRIDE"
        ),
        json_schema_extra=_org_id_json_schema,
    ),
]


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
async def execute_query(query: str, time: Optional[str] = None, org_id: OrgIdParam = "") -> Dict[str, Any]:
    """Execute an instant query against Prometheus.

    Args:
        query: PromQL query string
        time: Optional RFC3339 or Unix timestamp (default: current time)
        org_id: Optional tenant id sent as X-Scope-OrgID for this call only.
            Ignored when the operator configured ORG_ID and did not enable
            PROMETHEUS_MCP_ALLOW_ORG_ID_OVERRIDE. Annotated with
            ``x-mcp-header: Org-Id`` so a 2026-07-28 client may also send it as
            the ``Mcp-Param-Org-Id`` HTTP header.

    Returns:
        Query result with type (vector, matrix, scalar, string) and values
    """
    params = {"query": query}
    if time:
        params["time"] = time

    logger.info("Executing instant query", query=query, time=time, org_id=org_id)

    # Forward the tenant override only when one was supplied, so the single-tenant
    # call is byte-for-byte what it has always been.
    tenant_kwargs = {"org_id": org_id} if org_id else {}
    data = make_prometheus_request("query", params=params, **tenant_kwargs)

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

# ---------------------------------------------------------------------------
# MCP 2026-07-28 compatibility layer
#
# The installed mcp SDK implements 2025-11-25, so every 2026-07-28 behaviour is
# added here on top of it (see prometheus_mcp_server.spec2026). Everything below
# is additive and switched off wholesale by PROMETHEUS_MCP_SPEC_2026=false.
# ---------------------------------------------------------------------------

#: Version advertised in the 2026-07-28 result envelope and discovery document.
#: Kept in step with pyproject.toml and server.json by sync-version.yml.
SERVER_VERSION = "1.6.1"

from prometheus_mcp_server.spec2026.asgi import (
    strict_header_middleware,
    tool_annotation_lookup,
)
from prometheus_mcp_server.spec2026.discovery import install_discovery
from prometheus_mcp_server.spec2026.envelope import (
    _WRAPPER_MARKER as _ENVELOPE_WRAPPER_MARKER,
    _get_request_handlers,
    install_envelope,
    install_initialize_envelope,
)
from prometheus_mcp_server.spec2026.negotiation import (
    NegotiationError,
    extract_request_meta,
    negotiation_from_meta,
    reset_current_negotiation,
    set_current_negotiation,
)
from prometheus_mcp_server.spec2026.otel import (
    extract_trace_headers,
    reset_trace_headers,
    set_trace_headers,
)

# The authoritative per-request ``_meta`` comes from the SDK's own request
# context variable rather than from anything FastMCP rebuilt for itself.
try:
    from mcp.server.lowlevel.server import request_ctx as _sdk_request_ctx
except Exception as e:  # pragma: no cover - requires an incompatible mcp SDK
    logger.warning(
        "MCP SDK request context unavailable; request _meta will not be read",
        error=str(e),
        error_type=type(e).__name__,
    )
    _sdk_request_ctx = None


def _current_request_meta() -> Any:
    """Read the raw ``_meta`` of the request currently being served.

    Returns:
        The SDK's ``RequestParams.Meta`` model, or None when no request is in
        scope (the initialize handshake, or an internal FastMCP call such as a
        tool-listing cache refresh).
    """
    if _sdk_request_ctx is None:
        return None
    try:
        return getattr(_sdk_request_ctx.get(), "meta", None)
    except LookupError:
        return None
    except Exception as e:
        logger.warning(
            "Could not read the SDK request context",
            error=str(e),
            error_type=type(e).__name__,
        )
        return None


_NEGOTIATION_WRAPPER_MARKER = "_spec2026_negotiation_inner"

#: Markers stamped on the low-level request handlers by this layer, innermost
#: last. Used to unwrap a handler back to the SDK's own callable so a repeated
#: install replaces rather than stacks.
_SPEC_2026_WRAPPER_MARKERS = (_ENVELOPE_WRAPPER_MARKER, _NEGOTIATION_WRAPPER_MARKER)


def _unwrap_spec_2026_handler(handler: Any) -> Any:
    """Strip every 2026-07-28 wrapper off a low-level request handler.

    Args:
        handler: The handler currently registered for a request type.

    Returns:
        The innermost handler, i.e. the one the SDK or FastMCP registered.
    """
    while True:
        for marker in _SPEC_2026_WRAPPER_MARKERS:
            inner = getattr(handler, marker, None)
            if inner is not None:
                handler = inner
                break
        else:
            return handler


def _wrap_negotiation(handler: Any) -> Any:
    """Build the per-request negotiation wrapper around a low-level handler.

    Negotiation deliberately lives *below* FastMCP rather than in a FastMCP
    middleware. FastMCP renders an exception raised during ``tools/call`` as a
    successful ``CallToolResult`` with ``isError: true``, which would strip the
    -32022 code and the ``data.supported`` list a client needs in order to
    renegotiate. Raising from the low-level request handler produces a real
    JSON-RPC error for every method alike.

    Args:
        handler: The original ``async (req) -> ServerResult`` handler.

    Returns:
        A coroutine function stamped with the negotiation wrapper marker.
    """

    async def negotiation_handler(req: Any = None) -> Any:
        """Negotiate the request, publish its state, then run the handler."""
        meta = extract_request_meta({"_meta": _current_request_meta()})

        try:
            negotiation = negotiation_from_meta(meta)
        except NegotiationError as e:
            logger.warning(
                "Rejecting request after failed protocol negotiation",
                code=e.code,
                error=e.message,
            )
            raise e.to_mcp_error() from e

        negotiation_token = set_current_negotiation(negotiation)
        trace_token = set_trace_headers(extract_trace_headers(meta))
        try:
            result = handler(req)
            if inspect.isawaitable(result):
                result = await result
            return result
        finally:
            # Capabilities are per-request and MUST NOT leak into the next one.
            reset_current_negotiation(negotiation_token)
            reset_trace_headers(trace_token)

    setattr(negotiation_handler, _NEGOTIATION_WRAPPER_MARKER, handler)
    return negotiation_handler


def install_negotiation(server: Any = None) -> bool:
    """Wrap a FastMCP server's request handlers with per-request negotiation.

    Under 2026-07-28 a client restates its protocol version, capabilities and
    trace context on every request inside ``params._meta`` rather than once at
    initialize time. Each wrapped handler parses that block, rejects a protocol
    version the server does not speak, and publishes the result on context
    variables that downstream code (notably ``make_prometheus_request``) reads
    without any threading of parameters. Both variables are reset in a
    ``finally`` block so nothing survives into the next request.

    Idempotent: an already-wrapped handler is unwrapped and re-wrapped rather
    than nested, so repeated calls never stack duplicate negotiation passes.
    Unwrapping strips the result envelope too, so this must run *before*
    ``install_envelope`` -- which is the order ``install_spec_2026`` uses, and
    which also puts negotiation innermost where the SDK's own request context is
    still in scope.

    Fully defensive: any unexpected SDK shape is logged and the server is left
    exactly as it was. This function never raises.

    Args:
        server: The FastMCP server instance. Defaults to this module's server.

    Returns:
        True if at least one handler was wrapped, False if the layer degraded.
    """
    try:
        handlers = _get_request_handlers(server if server is not None else mcp)
        if handlers is None:
            return False

        wrapped_count = 0
        for request_type in list(handlers.keys()):
            inner = _unwrap_spec_2026_handler(handlers[request_type])
            if not callable(inner):
                logger.warning(
                    "Skipping non-callable request handler",
                    request_type=getattr(request_type, "__name__", repr(request_type)),
                )
                continue
            handlers[request_type] = _wrap_negotiation(inner)
            wrapped_count += 1

        if wrapped_count == 0:
            logger.warning("Negotiation wrapped no request handlers; layer inactive")
            return False

        logger.info("MCP 2026-07-28 negotiation installed", handler_count=wrapped_count)
        return True

    except Exception as e:
        logger.warning(
            "Per-request negotiation unavailable; server continues on 2025-11-25",
            error=str(e),
            error_type=type(e).__name__,
        )
        return False


async def tool_header_annotations(tool_name: Any) -> List[Any]:
    """Collect the ``x-mcp-header`` annotations declared by a tool's input schema.

    Args:
        tool_name: Name of the tool being called.

    Returns:
        The tool's validated header annotations, empty when the tool cannot be
        resolved or declares none.
    """
    if not isinstance(tool_name, str):
        return []
    return await tool_annotation_lookup(mcp, tool_name)


def strict_header_asgi_middleware() -> Any:
    """Build the ASGI middleware enforcing the ``Mcp-*`` header rules (F6).

    Strict validation happens at the ASGI layer because that is the only place
    that still sees the exact JSON-RPC body the client sent -- FastMCP re-enters
    its own middleware chain with internal sub-requests, and the SDK normalises
    a ``params.uri`` before any FastMCP hook runs -- and the only place that can
    still choose the HTTP status the design requires (400).

    Returns:
        A Starlette ``Middleware`` entry for ``FastMCP.run(middleware=[...])``,
        or None when strict validation is switched off or unavailable.
    """
    if not config.strict_headers:
        return None
    try:
        return strict_header_middleware(annotation_lookup=tool_header_annotations)
    except Exception as e:
        logger.warning(
            "Strict header validation unavailable; requests will not be checked",
            error=str(e),
            error_type=type(e).__name__,
        )
        return None


def install_spec_2026() -> Dict[str, Any]:
    """Install the MCP 2026-07-28 compatibility layer onto this module's server.

    Each piece is installed independently and every failure mode degrades to a
    logged warning, so a future SDK release can disable the layer but can never
    stop the server from serving 2025-11-25 clients. Every installer is
    idempotent, so calling this twice (as the test-suite does) neither stacks
    wrappers nor registers anything twice.

    Returns:
        Mapping of layer name to whether that piece was installed.
    """
    status: Dict[str, Any] = {
        "negotiation": False,
        "discovery": {},
        "envelope": False,
        "initialize_envelope": False,
    }

    try:
        # Discovery first, so the negotiation and envelope wrappers installed
        # below also cover the server/discover handler it registers.
        status["discovery"] = install_discovery(
            mcp,
            mcp_name,
            SERVER_VERSION,
            tool_namer=_tool_name,
            cache_scope=config.cache_scope,
        )

        status["negotiation"] = install_negotiation(mcp)

        status["envelope"] = install_envelope(
            mcp,
            server_name=mcp_name,
            server_version=SERVER_VERSION,
            ttl_ms=config.cache_ttl_ms,
            cache_scope=config.cache_scope,
        )

        # The SDK answers initialize inside ServerSession, out of reach of the
        # request-handler wrappers, so it needs its own installer.
        status["initialize_envelope"] = install_initialize_envelope(
            server_name=mcp_name,
            server_version=SERVER_VERSION,
        )

        logger.info(
            "MCP 2026-07-28 compatibility layer installed",
            strict_headers=config.strict_headers,
            **status,
        )
    except Exception as e:
        logger.warning(
            "MCP 2026-07-28 compatibility layer unavailable; server continues on 2025-11-25",
            error=str(e),
            error_type=type(e).__name__,
        )

    return status


if config.spec_2026_enabled:
    SPEC_2026_STATUS = install_spec_2026()
else:
    SPEC_2026_STATUS = {}
    logger.info(
        "MCP 2026-07-28 compatibility layer disabled",
        switch="PROMETHEUS_MCP_SPEC_2026",
    )


if __name__ == "__main__":
    logger.info("Starting Prometheus MCP Server", mode="direct")
    mcp.run()
