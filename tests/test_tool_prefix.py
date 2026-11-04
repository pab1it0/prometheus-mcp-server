"""Tests for the TOOL_PREFIX environment variable.

TOOL_PREFIX renames every registered tool and the MCP server itself so that
multiple instances (e.g. staging and production) can coexist in one client.
The prefix is applied at import time, so each test loads its own private copy
of the server module instead of mutating the shared one.
"""

import importlib.util
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastmcp import Client

SERVER_PATH = Path(__file__).resolve().parents[1] / "src" / "prometheus_mcp_server" / "server.py"

EXPECTED_TOOLS = [
    "health_check",
    "execute_query",
    "execute_range_query",
    "list_metrics",
    "get_metric_metadata",
    "get_targets",
]


def load_server_module(prefix=None):
    """Import a fresh, isolated copy of server.py with TOOL_PREFIX applied."""
    env = {"TOOL_PREFIX": prefix} if prefix is not None else {}
    with patch.dict(os.environ, env, clear=False):
        if prefix is None:
            os.environ.pop("TOOL_PREFIX", None)
        spec = importlib.util.spec_from_file_location("prometheus_mcp_server_under_test", SERVER_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def test_tool_name_without_prefix():
    module = load_server_module()
    assert module._tool_name("execute_query") == "execute_query"


def test_tool_name_with_prefix():
    module = load_server_module("staging")
    assert module._tool_name("execute_query") == "staging_execute_query"


def test_empty_prefix_is_treated_as_unset():
    module = load_server_module("")
    assert module._tool_name("execute_query") == "execute_query"
    assert module.mcp.name == "Prometheus MCP"


def test_server_name_includes_prefix():
    assert load_server_module("staging").mcp.name == "Prometheus MCP (staging)"
    assert load_server_module().mcp.name == "Prometheus MCP"


@pytest.mark.asyncio
async def test_registered_tools_are_prefixed():
    module = load_server_module("staging")
    async with Client(module.mcp) as client:
        tool_names = {tool.name for tool in await client.list_tools()}

    assert tool_names == {f"staging_{name}" for name in EXPECTED_TOOLS}


@pytest.mark.asyncio
async def test_registered_tools_are_unprefixed_by_default():
    module = load_server_module()
    async with Client(module.mcp) as client:
        tool_names = {tool.name for tool in await client.list_tools()}

    assert tool_names == set(EXPECTED_TOOLS)


@pytest.mark.asyncio
async def test_prefixed_tool_is_callable():
    module = load_server_module("staging")
    with patch.object(module, "make_prometheus_request") as mock_request:
        mock_request.return_value = {"resultType": "vector", "result": []}
        async with Client(module.mcp) as client:
            result = await client.call_tool("staging_execute_query", {"query": "up"})

    assert result.data["resultType"] == "vector"
    mock_request.assert_called_once_with("query", params={"query": "up"})
