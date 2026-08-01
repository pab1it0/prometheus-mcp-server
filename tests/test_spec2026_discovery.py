"""Tests for the MCP 2026-07-28 server/discover layer (F4 + F8)."""

import asyncio
import json
from typing import Literal
from unittest.mock import MagicMock

import mcp.types as mcp_types
import pytest
from fastmcp import Client, FastMCP
from pydantic import BaseModel, RootModel

from prometheus_mcp_server.spec2026 import discovery
from prometheus_mcp_server.spec2026.discovery import (
    DEFAULT_CACHE_SCOPE,
    DEFAULT_DISCOVER_TTL_MS,
    DISCOVER_METHOD,
    DISCOVER_REQUIRED_KEYS,
    build_discover_payload,
    default_instructions,
    install_discovery,
    make_discover_endpoint,
    make_discover_tool,
)


@pytest.fixture(scope="module")
def installed_server():
    """Install the discovery layer onto a dedicated FastMCP instance once."""
    server = FastMCP("Prometheus MCP (test)")
    status = install_discovery(
        server,
        "Prometheus MCP (test)",
        "1.6.1",
        tool_namer=lambda name: f"prom_{name}",
    )
    return server, status


# ----------------------------------------------------------------------------------
# Payload shape
# ----------------------------------------------------------------------------------

def test_payload_has_exactly_the_required_keys():
    """The discover payload carries every key the 2026-07-28 spec requires."""
    payload = build_discover_payload("Prometheus MCP", "1.6.1")

    assert set(payload.keys()) == set(DISCOVER_REQUIRED_KEYS)
    assert set(DISCOVER_REQUIRED_KEYS) == {
        "resultType",
        "supportedVersions",
        "capabilities",
        "instructions",
        "ttlMs",
        "cacheScope",
        "_meta",
    }


def test_payload_default_values():
    """Defaults match the design document's reference payload."""
    payload = build_discover_payload("Prometheus MCP", "1.6.1")

    assert payload["resultType"] == "complete"
    assert payload["supportedVersions"] == ["2026-07-28", "2025-11-25", "2025-06-18"]
    assert payload["ttlMs"] == DEFAULT_DISCOVER_TTL_MS
    assert payload["cacheScope"] == DEFAULT_CACHE_SCOPE == "public"
    assert payload["instructions"] == default_instructions("Prometheus MCP")
    assert payload["_meta"] == {
        "io.modelcontextprotocol/serverInfo": {"name": "Prometheus MCP", "version": "1.6.1"}
    }


def test_payload_is_json_serializable():
    """The payload survives a JSON round trip unchanged."""
    payload = build_discover_payload("Prometheus MCP", "1.6.1")

    assert json.loads(json.dumps(payload)) == payload


def test_payload_returns_a_fresh_object_each_call():
    """Mutating a returned payload does not leak into later calls."""
    first = build_discover_payload("A", "1")
    first["capabilities"]["tools"]["poisoned"] = True
    first["_meta"]["poisoned"] = True

    second = build_discover_payload("A", "1")

    assert second["capabilities"]["tools"] == {}
    assert "poisoned" not in second["_meta"]


def test_payload_overrides():
    """Explicit arguments override every default."""
    payload = build_discover_payload(
        "Custom",
        "9.9.9",
        supported_versions=["2026-07-28"],
        instructions="Do the thing.",
        ttl_ms=1000,
        cache_scope="private",
    )

    assert payload["supportedVersions"] == ["2026-07-28"]
    assert payload["instructions"] == "Do the thing."
    assert payload["ttlMs"] == 1000
    assert payload["cacheScope"] == "private"


@pytest.mark.parametrize(
    "ttl_ms,expected",
    [
        (0, 0),
        (42, 42),
        ("500", DEFAULT_DISCOVER_TTL_MS),
        (-1, DEFAULT_DISCOVER_TTL_MS),
        (None, DEFAULT_DISCOVER_TTL_MS),
        ("not-a-number", DEFAULT_DISCOVER_TTL_MS),
        # Regression: bool subclasses int, and int() silently truncates a float,
        # so a bare int() produced ttlMs=1 for True and 1 for 1.9 -- with no
        # warning -- while the result envelope rejected both. The two coercion
        # paths must agree, or the same misconfiguration is advertised two ways.
        (True, DEFAULT_DISCOVER_TTL_MS),
        (False, DEFAULT_DISCOVER_TTL_MS),
        (1.9, DEFAULT_DISCOVER_TTL_MS),
    ],
)
def test_ttl_ms_is_coerced_to_a_non_negative_integer(ttl_ms, expected):
    """ttlMs must always be an integer >= 0, never a coerced bool or float."""
    payload = build_discover_payload("A", "1", ttl_ms=ttl_ms)

    assert payload["ttlMs"] == expected
    assert isinstance(payload["ttlMs"], int)
    assert not isinstance(payload["ttlMs"], bool)


def test_discover_ttl_coercion_matches_the_envelope():
    """The discovery and envelope TTL guards must not disagree."""
    from prometheus_mcp_server.spec2026.envelope import _coerce_ttl_ms

    for value in (True, False, 1.9, -1, "500", None, 0, 42):
        envelope_default = _coerce_ttl_ms(value) != value
        discover_default = build_discover_payload("A", "1", ttl_ms=value)["ttlMs"] != value
        assert envelope_default == discover_default, f"disagreement on {value!r}"


@pytest.mark.parametrize(
    "cache_scope,expected",
    [("public", "public"), ("private", "private"), ("bogus", "public"), (None, "public")],
)
def test_cache_scope_falls_back_to_public(cache_scope, expected):
    """cacheScope is constrained to the two spec values."""
    payload = build_discover_payload("A", "1", cache_scope=cache_scope)

    assert payload["cacheScope"] == expected


# ----------------------------------------------------------------------------------
# F8 — extensions capability
# ----------------------------------------------------------------------------------

def test_extensions_capability_is_always_advertised():
    """F8: capabilities always carries an extensions object, defaulting to empty."""
    payload = build_discover_payload("A", "1")

    assert payload["capabilities"] == {"tools": {}, "extensions": {}}


def test_extensions_capability_can_be_populated():
    """A caller-supplied extensions mapping is advertised verbatim."""
    payload = build_discover_payload("A", "1", extensions={"io.example/thing": {"version": 1}})

    assert payload["capabilities"]["extensions"] == {"io.example/thing": {"version": 1}}
    assert payload["capabilities"]["tools"] == {}


def test_extensions_mapping_is_copied_not_aliased():
    """Mutating the caller's mapping afterwards does not change the payload."""
    extensions = {"io.example/thing": {}}
    payload = build_discover_payload("A", "1", extensions=extensions)
    extensions["io.example/other"] = {}

    assert payload["capabilities"]["extensions"] == {"io.example/thing": {}}


def test_extra_capabilities_merge_over_the_base():
    """Extra capabilities merge in while tools and extensions stay present."""
    payload = build_discover_payload(
        "A", "1", capabilities={"logging": {}, "extensions": {"io.example/from-caps": {}}}
    )

    assert payload["capabilities"]["tools"] == {}
    assert payload["capabilities"]["logging"] == {}
    assert payload["capabilities"]["extensions"] == {"io.example/from-caps": {}}


def test_explicit_extensions_wins_over_capabilities_extensions():
    """The dedicated extensions argument takes precedence."""
    payload = build_discover_payload(
        "A",
        "1",
        capabilities={"extensions": {"from": "caps"}},
        extensions={"from": "arg"},
    )

    assert payload["capabilities"]["extensions"] == {"from": "arg"}


# ----------------------------------------------------------------------------------
# HTTP route
# ----------------------------------------------------------------------------------

def test_http_endpoint_returns_200_and_valid_json():
    """GET /server/discover returns 200 with a valid, complete JSON payload."""
    endpoint = make_discover_endpoint(lambda: build_discover_payload("Prometheus MCP", "1.6.1"))

    response = asyncio.run(endpoint(MagicMock()))

    assert response.status_code == 200
    body = json.loads(response.body)
    assert set(body.keys()) == set(DISCOVER_REQUIRED_KEYS)
    assert body["resultType"] == "complete"
    assert body["capabilities"]["extensions"] == {}
    assert body["_meta"]["io.modelcontextprotocol/serverInfo"]["version"] == "1.6.1"


def test_http_route_is_registered_on_the_app(installed_server):
    """The route is mounted alongside the JSON-RPC endpoint."""
    server, _ = installed_server

    paths = {route.path: getattr(route, "methods", None) for route in server.http_app().routes}

    assert "/server/discover" in paths
    assert "GET" in paths["/server/discover"]
    assert "/mcp" in paths


# ----------------------------------------------------------------------------------
# MCP tool
# ----------------------------------------------------------------------------------

def test_tool_function_returns_the_payload():
    """The tool coroutine returns the shared payload."""
    tool_fn = make_discover_tool(lambda: build_discover_payload("A", "1"))

    result = asyncio.run(tool_fn())

    assert set(result.keys()) == set(DISCOVER_REQUIRED_KEYS)


@pytest.mark.asyncio
async def test_tool_is_registered_with_the_tool_prefix(installed_server):
    """The tool honors the caller-supplied namer (TOOL_PREFIX convention)."""
    server, _ = installed_server

    async with Client(server) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    assert "prom_server_discover" in tools
    assert "server_discover" not in tools


@pytest.mark.asyncio
async def test_tool_carries_a_complete_annotation_block(installed_server):
    """A partial annotation block breaks the existing suite, so require a full one."""
    server, _ = installed_server

    async with Client(server) as client:
        tool = {t.name: t for t in await client.list_tools()}["prom_server_discover"]

    assert tool.annotations is not None
    assert tool.annotations.title == "Server Discovery"
    assert tool.annotations.title != tool.name
    assert " " in tool.annotations.title
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.destructiveHint is False
    assert tool.annotations.idempotentHint is True
    assert tool.annotations.openWorldHint is True


@pytest.mark.asyncio
async def test_tool_call_returns_the_payload(installed_server):
    """Calling the tool over MCP yields the discovery document."""
    server, _ = installed_server

    async with Client(server) as client:
        result = await client.call_tool("prom_server_discover", {})

    assert set(result.data.keys()) == set(DISCOVER_REQUIRED_KEYS)
    assert result.data["capabilities"]["extensions"] == {}


# ----------------------------------------------------------------------------------
# JSON-RPC dispatch
# ----------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_server_discover_dispatches_as_a_real_jsonrpc_method(installed_server):
    """server/discover is answered by the low-level server on any transport."""
    server, status = installed_server
    assert status["jsonrpc"] is True

    async with Client(server) as client:
        result = await client.session.send_request(
            mcp_types.ClientRequest(discovery.DiscoverRequest()),
            discovery.DiscoverResult,
        )

    dumped = result.model_dump(by_alias=True, mode="json", exclude_none=True)
    assert set(dumped.keys()) == set(DISCOVER_REQUIRED_KEYS)
    assert dumped["resultType"] == "complete"
    assert dumped["capabilities"]["extensions"] == {}


def test_install_status_reports_all_three_paths(installed_server):
    """All three delivery paths install on a healthy SDK."""
    _, status = installed_server

    assert status == {"jsonrpc": True, "http_route": True, "tool": True}


def test_install_is_idempotent(installed_server):
    """Re-installing returns the cached status without registering duplicates."""
    server, status = installed_server
    routes_before = [route.path for route in server.http_app().routes]

    again = install_discovery(server, "Prometheus MCP (test)", "1.6.1")

    assert again == status
    assert [route.path for route in server.http_app().routes] == routes_before


def test_client_request_union_still_resolves_standard_methods(installed_server):
    """Extending the union must not perturb any existing method."""
    installed_server  # ensure the union has been extended

    assert isinstance(
        mcp_types.ClientRequest.model_validate({"method": "tools/list", "params": {}}).root,
        mcp_types.ListToolsRequest,
    )
    assert isinstance(
        mcp_types.ClientRequest.model_validate({"method": "ping", "params": {}}).root,
        mcp_types.PingRequest,
    )


def test_server_discover_method_now_parses(installed_server):
    """The extended union resolves the new method to our request model."""
    installed_server

    parsed = mcp_types.ClientRequest.model_validate({"method": DISCOVER_METHOD}).root

    assert isinstance(parsed, discovery.DiscoverRequest)
    assert parsed.params is None


def test_genuinely_unknown_methods_are_still_rejected(installed_server):
    """Only server/discover is added; other unknown methods still fail validation."""
    installed_server

    with pytest.raises(Exception):
        mcp_types.ClientRequest.model_validate({"method": "bogus/method", "params": {}})


def test_sdk_native_method_is_reused_instead_of_extending_again(installed_server):
    """Forward compat: when the union already knows the method, do not re-extend."""
    installed_server  # the union already contains a server/discover member

    before = (
        len(discovery._union_members(mcp_types.ClientRequest)),
        len(discovery._union_members(mcp_types.ServerResult)),
    )
    fresh = FastMCP("fresh")
    assert discovery._install_jsonrpc(fresh, lambda: build_discover_payload("A", "1")) is True
    after = (
        len(discovery._union_members(mcp_types.ClientRequest)),
        len(discovery._union_members(mcp_types.ServerResult)),
    )

    assert before == after
    found = discovery._find_member_by_method(mcp_types.ClientRequest, DISCOVER_METHOD)
    assert fresh._mcp_server.request_handlers[found] is not None


def test_find_member_by_method_matches_real_sdk_models():
    """The union scan works against the SDK's own request models."""
    found = discovery._find_member_by_method(mcp_types.ClientRequest, "tools/list")

    assert found is mcp_types.ListToolsRequest
    assert discovery._find_member_by_method(mcp_types.ClientRequest, "nope/nope") is None


# ----------------------------------------------------------------------------------
# Defensive degradation
# ----------------------------------------------------------------------------------

class _Stub:
    """Bare attribute container standing in for a FastMCP instance."""


def test_jsonrpc_install_degrades_without_a_low_level_server():
    """A FastMCP without _mcp_server logs and returns False rather than raising."""
    assert discovery._install_jsonrpc(_Stub(), lambda: {}) is False


def test_jsonrpc_install_degrades_when_request_handlers_is_not_a_dict():
    """An SDK whose dispatch table changed shape degrades cleanly."""
    stub = _Stub()
    stub._mcp_server = _Stub()
    stub._mcp_server.request_handlers = "not a dict"

    assert discovery._install_jsonrpc(stub, lambda: {}) is False


def test_http_route_install_degrades_when_custom_route_fails():
    """A custom_route failure is contained."""
    stub = _Stub()
    stub.custom_route = MagicMock(side_effect=RuntimeError("boom"))

    assert discovery._install_http_route(stub, lambda: {}, "/server/discover") is False


def test_tool_install_degrades_when_registration_fails():
    """A tool registration failure is contained."""
    stub = _Stub()
    stub.tool = MagicMock(side_effect=RuntimeError("boom"))

    assert discovery._install_tool(stub, lambda: {}, lambda name: name, "server_discover") is False


def test_install_never_raises_on_a_hostile_object():
    """install_discovery reports failure for every path instead of raising."""
    stub = _Stub()
    stub.custom_route = MagicMock(side_effect=RuntimeError("boom"))
    stub.tool = MagicMock(side_effect=RuntimeError("boom"))

    status = install_discovery(stub, "A", "1")

    assert status == {"jsonrpc": False, "http_route": False, "tool": False}


def test_jsonrpc_install_degrades_when_the_models_are_undefined(monkeypatch):
    """An SDK whose base models vanished leaves DiscoverRequest as None."""
    monkeypatch.setattr(discovery, "DiscoverRequest", None)

    assert discovery._install_jsonrpc(FastMCP("undefined"), lambda: {}) is False


def test_jsonrpc_install_degrades_when_union_surgery_raises(monkeypatch):
    """Any unexpected failure inside the installer is contained and logged."""
    monkeypatch.setattr(
        discovery,
        "_find_member_by_method",
        MagicMock(side_effect=RuntimeError("SDK changed shape")),
    )

    assert discovery._install_jsonrpc(FastMCP("raises"), lambda: {}) is False


def test_jsonrpc_install_degrades_when_the_root_field_is_absent(monkeypatch):
    """An SDK that stops modelling requests as a RootModel union degrades cleanly."""

    class _NoRoot(BaseModel):
        """Stands in for a restructured mcp.types.ClientRequest."""

    monkeypatch.setattr(mcp_types, "ClientRequest", _NoRoot)

    assert discovery._install_jsonrpc(FastMCP("no-root"), lambda: {}) is False


def test_union_scan_skips_non_model_members():
    """Union members that are not pydantic models are ignored, not crashed on."""

    class _Member(BaseModel):
        method: Literal["x/y"] = "x/y"

    class _Root(RootModel[int | _Member]):
        pass

    assert discovery._find_member_by_method(_Root, "x/y") is _Member
    assert discovery._find_member_by_method(_Root, "other/method") is None


def test_constants_module_is_optional(monkeypatch):
    """A missing spec2026.constants degrades to the design-doc literals."""
    monkeypatch.setattr(
        discovery.importlib, "import_module", MagicMock(side_effect=ImportError("gone"))
    )
    assert discovery._load_constants() is None

    monkeypatch.setattr(discovery, "_constants", None)
    assert discovery._const("PROTOCOL_VERSION_2026", "2026-07-28") == "2026-07-28"


def test_constants_are_sourced_from_the_shared_module():
    """When present, the shared constants module is the source of truth."""
    from prometheus_mcp_server.spec2026 import constants

    assert discovery.PROTOCOL_VERSION_2026 == constants.PROTOCOL_VERSION_2026
    assert discovery.SUPPORTED_PROTOCOL_VERSIONS == tuple(constants.SUPPORTED_PROTOCOL_VERSIONS)
    assert discovery.META_SERVER_INFO == constants.META_SERVER_INFO
    assert discovery.RESULT_TYPE_COMPLETE == constants.RESULT_TYPE_COMPLETE


def test_union_extension_rolls_back_when_rebuild_fails():
    """A failed model_rebuild restores the original annotation."""

    class _Member(BaseModel):
        pass

    class _Root(RootModel[int]):
        pass

    original = _Root.model_fields["root"].annotation
    calls = {"n": 0}

    def _exploding_rebuild(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("rebuild failed")
        return True

    _Root.model_rebuild = _exploding_rebuild

    with pytest.raises(RuntimeError):
        discovery._extend_union(_Root, _Member)

    assert _Root.model_fields["root"].annotation is original
    assert calls["n"] == 2  # failed extend, then successful restore


def test_custom_payload_factory_feeds_every_path():
    """The payload_factory escape hatch overrides the built-in builder."""
    server = FastMCP("factory")
    sentinel = {"resultType": "complete", "custom": True}

    status = install_discovery(server, "A", "1", payload_factory=lambda: dict(sentinel))
    endpoint_body = json.loads(asyncio.run(make_discover_endpoint(lambda: dict(sentinel))(MagicMock())).body)

    assert status["http_route"] is True
    assert endpoint_body == sentinel
