#!/usr/bin/env python

import os
import json
import re
from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass
import time
from datetime import datetime, timedelta

import dotenv
import requests
from mcp.server.fastmcp import FastMCP
from prometheus_mcp_server.logging_config import get_logger

dotenv.load_dotenv()
mcp = FastMCP("Prometheus MCP")

# Get logger instance
logger = get_logger()

@dataclass
class PrometheusConfig:
    url: str
    # Optional credentials
    username: Optional[str] = None
    password: Optional[str] = None
    token: Optional[str] = None
    # Optional Org ID for multi-tenant setups
    org_id: Optional[str] = None

config = PrometheusConfig(
    url=os.environ.get("PROMETHEUS_URL", ""),
    username=os.environ.get("PROMETHEUS_USERNAME", ""),
    password=os.environ.get("PROMETHEUS_PASSWORD", ""),
    token=os.environ.get("PROMETHEUS_TOKEN", ""),
    org_id=os.environ.get("ORG_ID", ""),
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

    url = f"{config.url.rstrip('/')}/api/v1/{endpoint}"
    auth = get_prometheus_auth()
    headers = {}

    if isinstance(auth, dict):  # Token auth is passed via headers
        headers.update(auth)
        auth = None  # Clear auth for requests.get if it's already in headers

    # Add OrgID header if specified
    if config.org_id:
        headers["X-Scope-OrgID"] = config.org_id

    try:
        logger.debug("Making Prometheus API request", endpoint=endpoint, url=url, params=params)

        # Make the request with appropriate headers and auth
        response = requests.get(url, params=params, auth=auth, headers=headers)

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

def apply_pagination(data: List[Any], limit: Optional[int] = None, offset: Optional[int] = None) -> Dict[str, Any]:
    """Apply pagination to a list of data.

    Args:
        data: List of data to paginate
        limit: Maximum number of items to return
        offset: Number of items to skip

    Returns:
        Dictionary with paginated data and metadata
    """
    total_count = len(data)
    start_index = offset or 0

    if limit is not None:
        end_index = start_index + limit
        paginated_data = data[start_index:end_index]
        has_more = end_index < total_count
    else:
        paginated_data = data[start_index:]
        has_more = False

    return {
        "data": paginated_data,
        "metadata": {
            "total": total_count,
            "offset": start_index,
            "limit": limit,
            "returned": len(paginated_data),
            "hasMore": has_more
        }
    }

def filter_metrics(metrics: List[str], filter_pattern: Optional[str] = None, prefix: Optional[str] = None) -> List[str]:
    """Filter metric names by pattern or prefix.

    Args:
        metrics: List of metric names
        filter_pattern: Regex pattern to match metric names
        prefix: Prefix to filter metric names

    Returns:
        Filtered list of metric names
    """
    filtered = metrics

    if prefix:
        filtered = [m for m in filtered if m.startswith(prefix)]

    if filter_pattern:
        try:
            pattern = re.compile(filter_pattern)
            filtered = [m for m in filtered if pattern.search(m)]
        except re.error as e:
            logger.warning("Invalid regex pattern", pattern=filter_pattern, error=str(e))
            # Continue with unfiltered results if regex is invalid

    return filtered

def create_compact_query_result(result_data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a compact version of query results to reduce token usage.

    Args:
        result_data: Original Prometheus query result

    Returns:
        Compacted result with essential information
    """
    if result_data["resultType"] != "vector":
        return result_data  # Only compact vector results for now

    compact_results = []
    for item in result_data["result"]:
        metric = item["metric"]
        value = item["value"]

        compact_item = {
            "name": metric.get("__name__", "unknown"),
            "value": value[1],  # The actual metric value
            "timestamp": value[0],  # The timestamp
            "labels": {k: v for k, v in metric.items() if k != "__name__"}
        }
        compact_results.append(compact_item)

    return {
        "resultType": "compact_vector",
        "result": compact_results
    }

@mcp.tool(description="Execute a PromQL instant query against Prometheus with optional pagination and compact mode")
async def execute_query(
    query: str, 
    time: Optional[str] = None, 
    limit: Optional[int] = None, 
    offset: Optional[int] = None, 
    compact: bool = False
) -> Dict[str, Any]:
    """Execute an instant query against Prometheus.

    Args:
        query: PromQL query string
        time: Optional RFC3339 or Unix timestamp (default: current time)
        limit: Maximum number of results to return (pagination)
        offset: Number of results to skip (pagination)
        compact: Return results in compact format to reduce token usage

    Returns:
        Query result with type (vector, matrix, scalar, string), values, and optional pagination metadata
    """
    params = {"query": query}
    if time:
        params["time"] = time

    logger.info("Executing instant query", query=query, time=time, limit=limit, offset=offset, compact=compact)
    data = make_prometheus_request("query", params=params)

    # Create the base result
    result_data = {
        "resultType": data["resultType"],
        "result": data["result"]
    }

    # Apply compact mode if requested
    if compact:
        result_data = create_compact_query_result(result_data)

    # Apply pagination if requested and result is a list
    if (limit is not None or offset is not None) and isinstance(data["result"], list):
        paginated = apply_pagination(data["result"], limit=limit, offset=offset)
        result = {
            "resultType": result_data["resultType"],
            "result": paginated["data"],
            "pagination": paginated["metadata"]
        }
        # Apply compact mode to paginated results if requested
        if compact:
            compact_paginated = create_compact_query_result({
                "resultType": data["resultType"],
                "result": paginated["data"]
            })
            result["resultType"] = compact_paginated["resultType"]
            result["result"] = compact_paginated["result"]
    else:
        result = result_data

    result_count = len(data["result"]) if isinstance(data["result"], list) else 1
    returned_count = len(result["result"]) if isinstance(result["result"], list) else 1

    logger.info("Instant query completed", 
                query=query, 
                result_type=data["resultType"], 
                total_results=result_count,
                returned_results=returned_count,
                compact=compact)

    return result

@mcp.tool(description="Execute a PromQL range query with start time, end time, and step interval")
async def execute_range_query(query: str, start: str, end: str, step: str) -> Dict[str, Any]:
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
    data = make_prometheus_request("query_range", params=params)

    result = {
        "resultType": data["resultType"],
        "result": data["result"]
    }

    logger.info("Range query completed", 
                query=query, 
                result_type=data["resultType"], 
                result_count=len(data["result"]) if isinstance(data["result"], list) else 1)

    return result

@mcp.tool(description="List available metrics in Prometheus with optional filtering and pagination")
async def list_metrics(
    limit: Optional[int] = None, 
    offset: Optional[int] = None, 
    filter_pattern: Optional[str] = None, 
    prefix: Optional[str] = None
) -> Dict[str, Any]:
    """Retrieve a list of metric names available in Prometheus.

    Args:
        limit: Maximum number of metrics to return (pagination)
        offset: Number of metrics to skip (pagination)
        filter_pattern: Regex pattern to filter metric names
        prefix: Prefix to filter metric names (e.g., 'storage_' for storage metrics)

    Returns:
        Dictionary with metric names and optional pagination metadata
    """
    logger.info("Listing available metrics", limit=limit, offset=offset, filter_pattern=filter_pattern, prefix=prefix)
    data = make_prometheus_request("label/__name__/values")

    # Apply filtering if requested
    filtered_metrics = filter_metrics(data, filter_pattern=filter_pattern, prefix=prefix)

    # Apply pagination if requested
    if limit is not None or offset is not None:
        paginated = apply_pagination(filtered_metrics, limit=limit, offset=offset)
        result = {
            "metrics": paginated["data"],
            "pagination": paginated["metadata"]
        }
    else:
        result = {
            "metrics": filtered_metrics,
            "total": len(filtered_metrics)
        }

    logger.info("Metrics list retrieved", 
                total_metrics=len(data), 
                filtered_metrics=len(filtered_metrics),
                returned_metrics=len(result["metrics"]))

    return result

@mcp.tool(description="Get metadata for a specific metric")
async def get_metric_metadata(metric: str) -> List[Dict[str, Any]]:
    """Get metadata about a specific metric.

    Args:
        metric: The name of the metric to retrieve metadata for

    Returns:
        List of metadata entries for the metric
    """
    logger.info("Retrieving metric metadata", metric=metric)
    params = {"metric": metric}
    data = make_prometheus_request("metadata", params=params)
    logger.info("Metric metadata retrieved", metric=metric, metadata_count=len(data["metadata"]))
    return data["metadata"]

@mcp.tool(description="Get information about scrape targets with optional pagination")
async def get_targets(
    limit: Optional[int] = None, 
    offset: Optional[int] = None, 
    active_only: bool = False
) -> Dict[str, Any]:
    """Get information about all Prometheus scrape targets.

    Args:
        limit: Maximum number of targets to return (applies to active targets)
        offset: Number of targets to skip (applies to active targets)
        active_only: Return only active targets (ignore dropped targets)

    Returns:
        Dictionary with targets information and optional pagination metadata
    """
    logger.info("Retrieving scrape targets information", limit=limit, offset=offset, active_only=active_only)
    data = make_prometheus_request("targets")

    active_targets = data["activeTargets"]
    dropped_targets = data["droppedTargets"]

    # Apply pagination to active targets if requested
    if limit is not None or offset is not None:
        paginated_active = apply_pagination(active_targets, limit=limit, offset=offset)
        result = {
            "activeTargets": paginated_active["data"],
            "activePagination": paginated_active["metadata"]
        }
        if not active_only:
            result["droppedTargets"] = dropped_targets
    else:
        result = {
            "activeTargets": active_targets
        }
        if not active_only:
            result["droppedTargets"] = dropped_targets

    logger.info("Scrape targets retrieved", 
                total_active_targets=len(active_targets),
                returned_active_targets=len(result["activeTargets"]), 
                dropped_targets=len(dropped_targets) if not active_only else 0)

    return result

if __name__ == "__main__":
    logger.info("Starting Prometheus MCP Server", mode="direct")
    mcp.run()
