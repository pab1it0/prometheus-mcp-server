#!/usr/bin/env python

"""Tests for the MCP 2026-07-28 result envelope layer (F2 + F3)."""

from unittest.mock import MagicMock, patch

import mcp.types as types
import pytest
from fastmcp import Client

from prometheus_mcp_server.server import clear_metrics_cache, mcp
from prometheus_mcp_server.spec2026.envelope import (
    CACHEABLE_METHODS,
    DEFAULT_CACHE_SCOPE,
    DEFAULT_CACHE_TTL_MS,
    _WRAPPER_MARKER,
    enrich_result,
    install_envelope,
    uninstall_envelope,
)

SERVER_NAME = "Prometheus MCP"
SERVER_VERSION = "1.6.1"
SERVER_INFO_KEY = "io.modelcontextprotocol/serverInfo"


@pytest.fixture(autouse=True)
def restore_server_state():
    """Keep the shared FastMCP singleton and metrics cache clean between tests.

    These tests install and uninstall the envelope on the module-level FastMCP
    singleton that server.py already wrapped at import. The teardown therefore
    *restores* the handler dict to whatever it was on entry rather than calling
    uninstall_envelope, which would strip the production wrappers and leave the
    shared server in a non-production state for the rest of the pytest process.
    """
    clear_metrics_cache()
    handlers = mcp._mcp_server.request_handlers
    saved = dict(handlers)
    yield
    handlers.clear()
    handlers.update(saved)
    clear_metrics_cache()


@pytest.fixture
def installed():
    """Install the envelope on the real server with known settings."""
    assert install_envelope(
        mcp,
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        ttl_ms=300000,
        cache_scope="public",
    )
    return mcp


def make_stub_mcp(handlers):
    """Build a minimal object shaped like a FastMCP server."""
    low_level = MagicMock()
    low_level.request_handlers = handlers
    stub = MagicMock()
    stub._mcp_server = low_level
    return stub


# ---------------------------------------------------------------------------
# End-to-end through a real in-memory MCP client
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tools_list_carries_full_envelope(installed):
    """tools/list gains resultType, ttlMs, cacheScope, serverInfo and sorted tools."""
    async with Client(mcp) as client:
        result = await client.session.list_tools()

    payload = result.model_dump(by_alias=True, mode="json", exclude_none=True)
    assert payload["resultType"] == "complete"
    assert payload["ttlMs"] == 300000
    assert payload["cacheScope"] == "public"
    assert payload["_meta"][SERVER_INFO_KEY] == {
        "name": SERVER_NAME,
        "version": SERVER_VERSION,
    }

    names = [tool.name for tool in result.tools]
    assert names == sorted(names)
    assert len(names) > 1, "need several tools for the ordering assertion to mean anything"


@pytest.mark.asyncio
async def test_tools_call_has_no_cache_fields(installed):
    """tools/call is not cacheable: resultType and serverInfo only."""
    with patch("prometheus_mcp_server.server.make_prometheus_request") as mock_request:
        mock_request.return_value = {"resultType": "vector", "result": []}
        async with Client(mcp) as client:
            result = await client.session.call_tool("execute_query", {"query": "up"})

    payload = result.model_dump(by_alias=True, mode="json", exclude_none=True)
    assert payload["resultType"] == "complete"
    assert payload["_meta"][SERVER_INFO_KEY]["name"] == SERVER_NAME
    assert "ttlMs" not in payload
    assert "cacheScope" not in payload


@pytest.mark.asyncio
async def test_ping_carries_result_type(installed):
    """Every result gets resultType, including the empty ping result."""
    async with Client(mcp) as client:
        await client.ping()
        result = await client.session.send_request(
            types.ClientRequest(types.PingRequest()), types.EmptyResult
        )

    payload = result.model_dump(by_alias=True, mode="json", exclude_none=True)
    assert payload["resultType"] == "complete"
    assert "ttlMs" not in payload


@pytest.mark.asyncio
async def test_uninstall_restores_plain_results():
    """Removing the layer leaves the SDK's untouched results behind."""
    install_envelope(mcp, server_name=SERVER_NAME, server_version=SERVER_VERSION)
    assert uninstall_envelope(mcp) is True

    async with Client(mcp) as client:
        result = await client.session.list_tools()

    payload = result.model_dump(by_alias=True, mode="json", exclude_none=True)
    assert "resultType" not in payload
    assert "ttlMs" not in payload
    assert "_meta" not in payload


# ---------------------------------------------------------------------------
# Installation behaviour
# ---------------------------------------------------------------------------


def test_install_is_idempotent(installed):
    """A second install replaces the wrappers instead of nesting them."""
    handlers = mcp._mcp_server.request_handlers
    first = dict(handlers)

    assert install_envelope(
        mcp, server_name=SERVER_NAME, server_version=SERVER_VERSION
    )

    assert set(handlers) == set(first)
    for request_type, handler in handlers.items():
        inner = getattr(handler, _WRAPPER_MARKER, None)
        assert inner is not None, f"{request_type.__name__} lost its wrapper"
        assert not hasattr(inner, _WRAPPER_MARKER), "wrappers were nested"
        assert inner is getattr(first[request_type], _WRAPPER_MARKER)


@pytest.mark.asyncio
async def test_reinstall_updates_settings(installed):
    """Re-installing with new settings takes effect rather than being ignored."""
    install_envelope(
        mcp,
        server_name="Other Server",
        server_version="9.9.9",
        ttl_ms=1000,
        cache_scope="private",
    )

    async with Client(mcp) as client:
        result = await client.session.list_tools()

    payload = result.model_dump(by_alias=True, mode="json", exclude_none=True)
    assert payload["ttlMs"] == 1000
    assert payload["cacheScope"] == "private"
    assert payload["_meta"][SERVER_INFO_KEY] == {"name": "Other Server", "version": "9.9.9"}


def test_uninstall_without_install_is_a_noop():
    """Uninstalling a server that was never wrapped reports no work done."""
    stub = make_stub_mcp({types.ListToolsRequest: lambda req: None})

    assert uninstall_envelope(stub) is False
    # The untouched handler is left exactly as it was found.
    assert not hasattr(stub._mcp_server.request_handlers[types.ListToolsRequest], _WRAPPER_MARKER)


def test_uninstall_is_idempotent(installed):
    """The second uninstall of a real server finds nothing left to restore."""
    assert uninstall_envelope(mcp) is True
    assert uninstall_envelope(mcp) is False


# ---------------------------------------------------------------------------
# Defensive degradation
# ---------------------------------------------------------------------------


def test_install_without_low_level_server_degrades():
    """A FastMCP without _mcp_server logs a warning and returns False."""
    stub = object()
    with patch("prometheus_mcp_server.spec2026.envelope.logger") as mock_logger:
        assert (
            install_envelope(stub, server_name=SERVER_NAME, server_version=SERVER_VERSION)
            is False
        )
    mock_logger.warning.assert_called_once()


def test_install_without_handler_dict_degrades():
    """request_handlers that is not a dict logs a warning and returns False."""
    stub = make_stub_mcp(["not", "a", "dict"])
    with patch("prometheus_mcp_server.spec2026.envelope.logger") as mock_logger:
        assert (
            install_envelope(stub, server_name=SERVER_NAME, server_version=SERVER_VERSION)
            is False
        )
    mock_logger.warning.assert_called_once()


def test_uninstall_without_low_level_server_degrades():
    """uninstall_envelope tolerates an unexpected server shape."""
    assert uninstall_envelope(object()) is False


def test_install_skips_non_callable_handlers():
    """A junk handler is skipped without aborting the whole install."""
    handlers = {types.ListToolsRequest: "not callable"}
    stub = make_stub_mcp(handlers)
    with patch("prometheus_mcp_server.spec2026.envelope.logger") as mock_logger:
        assert (
            install_envelope(stub, server_name=SERVER_NAME, server_version=SERVER_VERSION)
            is False
        )
    assert mock_logger.warning.call_count == 2  # skipped handler + layer inactive
    assert handlers[types.ListToolsRequest] == "not callable"


@pytest.mark.asyncio
async def test_install_wraps_unknown_request_types():
    """A request type with no method Literal still gets resultType, uncached."""

    class MysteryRequest:
        pass

    async def handler(req):
        return types.ServerResult(types.EmptyResult())

    handlers = {MysteryRequest: handler}
    stub = make_stub_mcp(handlers)
    assert install_envelope(
        stub, server_name=SERVER_NAME, server_version=SERVER_VERSION
    )

    result = await handlers[MysteryRequest](None)
    assert result.root.resultType == "complete"
    assert not hasattr(result.root, "ttlMs")


@pytest.mark.asyncio
async def test_enrichment_failure_still_returns_the_result():
    """If envelope assignment blows up, the client still gets its response."""

    class Hostile:
        root = None

        def __setattr__(self, name, value):
            raise RuntimeError("no mutation allowed")

    sentinel = Hostile()

    async def handler(req):
        return sentinel

    handlers = {types.ListToolsRequest: handler}
    stub = make_stub_mcp(handlers)
    assert install_envelope(
        stub, server_name=SERVER_NAME, server_version=SERVER_VERSION
    )

    with patch("prometheus_mcp_server.spec2026.envelope.logger") as mock_logger:
        result = await handlers[types.ListToolsRequest](None)

    assert result is sentinel
    mock_logger.warning.assert_called_once()


def test_install_survives_a_failing_handler_swap():
    """A handler dict that rejects writes degrades instead of raising."""

    class HostileDict(dict):
        def __setitem__(self, key, value):
            raise RuntimeError("read-only handler table")

    async def handler(req):
        return types.ServerResult(types.EmptyResult())

    handlers = HostileDict({types.ListToolsRequest: handler})
    stub = make_stub_mcp(handlers)
    with patch("prometheus_mcp_server.spec2026.envelope.logger") as mock_logger:
        assert (
            install_envelope(stub, server_name=SERVER_NAME, server_version=SERVER_VERSION)
            is False
        )
    assert mock_logger.warning.call_count == 2  # per-handler failure + layer inactive


def test_install_never_raises_on_unexpected_failure():
    """The outermost guard converts any surprise into a logged warning."""
    with patch(
        "prometheus_mcp_server.spec2026.envelope._get_request_handlers",
        side_effect=RuntimeError("boom"),
    ):
        with patch("prometheus_mcp_server.spec2026.envelope.logger") as mock_logger:
            assert (
                install_envelope(
                    mcp, server_name=SERVER_NAME, server_version=SERVER_VERSION
                )
                is False
            )
    mock_logger.warning.assert_called_once()


def test_uninstall_never_raises_on_unexpected_failure():
    """uninstall_envelope has the same never-raise guarantee."""
    with patch(
        "prometheus_mcp_server.spec2026.envelope._get_request_handlers",
        side_effect=RuntimeError("boom"),
    ):
        with patch("prometheus_mcp_server.spec2026.envelope.logger") as mock_logger:
            assert uninstall_envelope(mcp) is False
    mock_logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_install_supports_sync_handlers():
    """A non-coroutine handler is enriched rather than awaited blindly."""

    def handler(req):
        return types.ServerResult(types.ListToolsResult(tools=[]))

    handlers = {types.ListToolsRequest: handler}
    stub = make_stub_mcp(handlers)
    assert install_envelope(
        stub, server_name=SERVER_NAME, server_version=SERVER_VERSION
    )

    result = await handlers[types.ListToolsRequest](None)
    assert result.root.resultType == "complete"
    assert result.root.ttlMs == DEFAULT_CACHE_TTL_MS


# ---------------------------------------------------------------------------
# Setting validation
# ---------------------------------------------------------------------------


async def install_and_list(stub_handlers, **kwargs):
    """Install on a stub server and return the enriched tools/list result."""
    stub = make_stub_mcp(stub_handlers)
    assert install_envelope(
        stub, server_name=SERVER_NAME, server_version=SERVER_VERSION, **kwargs
    )
    result = await stub_handlers[types.ListToolsRequest](None)
    return result.root


def make_list_tools_handler():
    """Build a stub tools/list handler returning a fresh empty result."""

    async def handler(req):
        return types.ServerResult(types.ListToolsResult(tools=[]))

    return {types.ListToolsRequest: handler}


@pytest.mark.parametrize("bad_ttl", [-1, "300000", 1.5, True, None])
@pytest.mark.asyncio
async def test_invalid_ttl_falls_back_to_default(bad_ttl):
    """Only a non-negative int is accepted as ttlMs."""
    with patch("prometheus_mcp_server.spec2026.envelope.logger") as mock_logger:
        result = await install_and_list(make_list_tools_handler(), ttl_ms=bad_ttl)

    assert result.ttlMs == DEFAULT_CACHE_TTL_MS
    mock_logger.warning.assert_called_once()


@pytest.mark.parametrize("bad_scope", ["shared", "", None, 1])
@pytest.mark.asyncio
async def test_invalid_cache_scope_falls_back_to_default(bad_scope):
    """Only "public" or "private" is accepted as cacheScope."""
    with patch("prometheus_mcp_server.spec2026.envelope.logger") as mock_logger:
        result = await install_and_list(make_list_tools_handler(), cache_scope=bad_scope)

    assert result.cacheScope == DEFAULT_CACHE_SCOPE
    mock_logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_private_cache_scope_is_accepted():
    """"private" is a legal scope and is not coerced away."""
    with patch("prometheus_mcp_server.spec2026.envelope.logger") as mock_logger:
        result = await install_and_list(make_list_tools_handler(), cache_scope="private")

    assert result.cacheScope == "private"
    mock_logger.warning.assert_not_called()


def test_zero_ttl_is_valid():
    """ttlMs of 0 is a legal "do not cache" hint, not a falsy fallback."""
    result = types.ListToolsResult(tools=[])
    enrich_result(
        result,
        method="tools/list",
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        ttl_ms=0,
    )
    assert result.ttlMs == 0


# ---------------------------------------------------------------------------
# enrich_result unit behaviour
# ---------------------------------------------------------------------------


def test_existing_meta_is_merged_not_clobbered():
    """Pre-existing _meta keys survive the serverInfo merge."""
    result = types.ListToolsResult(tools=[], _meta={"traceparent": "abc", "custom": {"a": 1}})
    enrich_result(
        result,
        method="tools/list",
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
    )
    assert result.meta["traceparent"] == "abc"
    assert result.meta["custom"] == {"a": 1}
    assert result.meta[SERVER_INFO_KEY] == {"name": SERVER_NAME, "version": SERVER_VERSION}


def test_existing_envelope_fields_are_preserved():
    """A handler that built its own envelope stays authoritative."""
    result = types.ListToolsResult(tools=[], _meta={SERVER_INFO_KEY: {"name": "mine"}})
    result.resultType = "input_required"
    result.ttlMs = 42
    result.cacheScope = "private"

    enrich_result(
        result,
        method="tools/list",
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        ttl_ms=300000,
        cache_scope="public",
    )

    assert result.resultType == "input_required"
    assert result.ttlMs == 42
    assert result.cacheScope == "private"
    assert result.meta[SERVER_INFO_KEY] == {"name": "mine"}


def test_enrich_result_is_idempotent():
    """Applying the envelope twice changes nothing the second time."""
    result = types.ListToolsResult(tools=[])
    kwargs = {
        "method": "tools/list",
        "server_name": SERVER_NAME,
        "server_version": SERVER_VERSION,
    }
    enrich_result(result, **kwargs)
    first = result.model_dump(by_alias=True, mode="json", exclude_none=True)
    enrich_result(result, **kwargs)
    assert result.model_dump(by_alias=True, mode="json", exclude_none=True) == first


def test_enrich_result_unwraps_server_result():
    """Passing the ServerResult wrapper enriches the concrete result inside it."""
    inner = types.ListToolsResult(tools=[])
    wrapper = types.ServerResult(inner)
    returned = enrich_result(
        wrapper,
        method="tools/list",
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
    )
    assert returned is wrapper
    assert wrapper.root.resultType == "complete"
    dumped = wrapper.model_dump(by_alias=True, mode="json", exclude_none=True)
    assert dumped["resultType"] == "complete"
    assert dumped["ttlMs"] == DEFAULT_CACHE_TTL_MS


def test_non_mapping_meta_is_left_alone():
    """An unexpected _meta type is reported, not overwritten."""
    result = types.ListToolsResult(tools=[])
    object.__setattr__(result, "__dict__", {**result.__dict__, "meta": "not-a-dict"})

    with patch("prometheus_mcp_server.spec2026.envelope.logger") as mock_logger:
        enrich_result(
            result,
            method="tools/list",
            server_name=SERVER_NAME,
            server_version=SERVER_VERSION,
        )

    assert result.meta == "not-a-dict"
    mock_logger.warning.assert_called_once()


@pytest.mark.parametrize("method", sorted(CACHEABLE_METHODS))
def test_cacheable_methods_get_cache_fields(method):
    """Every method in the cacheable set carries ttlMs and cacheScope."""
    result = types.EmptyResult()
    enrich_result(
        result,
        method=method,
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
    )
    assert result.ttlMs == DEFAULT_CACHE_TTL_MS
    assert result.cacheScope == DEFAULT_CACHE_SCOPE


@pytest.mark.parametrize("method", ["tools/call", "prompts/get", "ping", "logging/setLevel", None])
def test_non_cacheable_methods_have_no_cache_fields(method):
    """tools/call and friends must not advertise a cache lifetime."""
    result = types.EmptyResult()
    enrich_result(
        result,
        method=method,
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
    )
    assert result.resultType == "complete"
    assert not hasattr(result, "ttlMs")
    assert not hasattr(result, "cacheScope")


# ---------------------------------------------------------------------------
# Deterministic ordering (F3)
# ---------------------------------------------------------------------------


def test_prompts_are_sorted_by_name():
    """prompts/list is ordered deterministically."""
    result = types.ListPromptsResult(
        prompts=[types.Prompt(name=name) for name in ["zeta", "alpha", "mid"]]
    )
    enrich_result(
        result,
        method="prompts/list",
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
    )
    assert [p.name for p in result.prompts] == ["alpha", "mid", "zeta"]


def test_resources_are_sorted_by_name_then_uri():
    """Same-named resources fall back to URI ordering for stability."""
    result = types.ListResourcesResult(
        resources=[
            types.Resource(name="dup", uri="http://b/2"),
            types.Resource(name="aaa", uri="http://a/1"),
            types.Resource(name="dup", uri="http://a/1"),
        ]
    )
    enrich_result(
        result,
        method="resources/list",
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
    )
    assert [(r.name, str(r.uri)) for r in result.resources] == [
        ("aaa", "http://a/1"),
        ("dup", "http://a/1"),
        ("dup", "http://b/2"),
    ]


def test_resource_templates_are_sorted():
    """resources/templates/list is ordered deterministically."""
    result = types.ListResourceTemplatesResult(
        resourceTemplates=[
            types.ResourceTemplate(name="z", uriTemplate="http://z/{id}"),
            types.ResourceTemplate(name="a", uriTemplate="http://a/{id}"),
        ]
    )
    enrich_result(
        result,
        method="resources/templates/list",
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
    )
    assert [t.name for t in result.resourceTemplates] == ["a", "z"]


def test_read_resource_result_is_untouched_by_sorting():
    """resources/read has no listable collection but still gets cache fields."""
    result = types.ReadResourceResult(contents=[])
    enrich_result(
        result,
        method="resources/read",
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
    )
    assert result.contents == []
    assert result.ttlMs == DEFAULT_CACHE_TTL_MS
    assert result.cacheScope == DEFAULT_CACHE_SCOPE


# ---------------------------------------------------------------------------
# Regressions found by adversarial review
# ---------------------------------------------------------------------------

class TestEnrichResultValidatesItsEnvelopeFields:
    """enrich_result is public API and must not emit a spec-invalid envelope."""

    @pytest.mark.parametrize("ttl_ms", [-5000, 1.5, True, False, "300000", None])
    def test_an_invalid_ttl_falls_back_to_the_default(self, ttl_ms):
        """Regression: ttlMs was written through unchecked, so a negative,
        float or boolean value reached the wire despite the docstring
        promising an integer >= 0."""
        result = types.ListToolsResult(tools=[])

        enrich_result(
            result,
            method="tools/list",
            server_name=SERVER_NAME,
            server_version=SERVER_VERSION,
            ttl_ms=ttl_ms,
        )

        payload = result.model_dump(by_alias=True, exclude_none=True)
        assert payload["ttlMs"] == DEFAULT_CACHE_TTL_MS
        assert isinstance(payload["ttlMs"], int) and not isinstance(payload["ttlMs"], bool)

    @pytest.mark.parametrize("cache_scope", ["shared", "PUBLIC", None, 42])
    def test_an_invalid_cache_scope_falls_back_to_the_default(self, cache_scope):
        """Regression: an arbitrary cacheScope was emitted verbatim, and None
        produced a cacheable result carrying ttlMs but no cacheScope at all."""
        result = types.ListToolsResult(tools=[])

        enrich_result(
            result,
            method="tools/list",
            server_name=SERVER_NAME,
            server_version=SERVER_VERSION,
            cache_scope=cache_scope,
        )

        payload = result.model_dump(by_alias=True, exclude_none=True)
        assert payload["cacheScope"] == DEFAULT_CACHE_SCOPE
        assert payload["cacheScope"] in ("public", "private")

    def test_valid_values_are_still_honoured(self):
        """The guard must not flatten legitimate configuration."""
        result = types.ListToolsResult(tools=[])

        enrich_result(
            result,
            method="tools/list",
            server_name=SERVER_NAME,
            server_version=SERVER_VERSION,
            ttl_ms=0,
            cache_scope="private",
        )

        payload = result.model_dump(by_alias=True, exclude_none=True)
        assert payload["ttlMs"] == 0
        assert payload["cacheScope"] == "private"


class TestResourceNotFoundErrorCode:
    """F1: resource-not-found moved from -32002 to -32602."""

    @pytest.mark.asyncio
    async def test_a_missing_resource_reports_invalid_params(self):
        """Regression: INVALID_PARAMS was defined in constants but never wired,
        so the SDK's 2025-11-25 -32002 reached the client unchanged."""
        from mcp.shared.exceptions import McpError

        async with Client(mcp) as client:
            with pytest.raises(McpError) as excinfo:
                await client.session.send_request(
                    types.ClientRequest(
                        types.ReadResourceRequest(
                            params=types.ReadResourceRequestParams(uri="file:///nope")
                        )
                    ),
                    types.ReadResourceResult,
                )

        assert excinfo.value.error.code == -32602
        assert "nope" in excinfo.value.error.message

    @pytest.mark.asyncio
    async def test_other_methods_keep_their_error_codes(self):
        """Only resources/read changed; nothing else may be retyped."""
        from mcp.shared.exceptions import McpError
        from mcp.types import ErrorData

        from prometheus_mcp_server.spec2026.envelope import _retype_error

        error = McpError(ErrorData(code=-32002, message="Resource not found: x"))

        assert _retype_error(error, "tools/call") is error
        assert _retype_error(error, None) is error
        assert _retype_error(ValueError("boom"), "resources/read").__class__ is ValueError
        assert _retype_error(
            McpError(ErrorData(code=-32603, message="internal")), "resources/read"
        ).error.code == -32603


class TestInitializeEnvelope:
    """F2 requires the envelope on *all* results, including the handshake."""

    @pytest.mark.asyncio
    async def test_the_handshake_result_carries_the_envelope(self):
        """Regression: InitializeRequest is answered inside ServerSession and
        never reaches request_handlers, so a 2026-07-28 client was guaranteed
        at least one envelope-less result on every connection."""
        async with Client(mcp) as client:
            payload = client.initialize_result.model_dump(by_alias=True, exclude_none=True)

        assert payload["resultType"] == "complete"
        assert payload["_meta"][SERVER_INFO_KEY] == {
            "name": SERVER_NAME,
            "version": SERVER_VERSION,
        }
        # initialize is not cacheable.
        assert "ttlMs" not in payload
        assert "cacheScope" not in payload

    @pytest.mark.asyncio
    async def test_install_and_uninstall_are_idempotent(self):
        """The session class is process-global, so the patch must be reversible."""
        from prometheus_mcp_server.spec2026.envelope import (
            install_initialize_envelope,
            uninstall_initialize_envelope,
        )

        try:
            assert install_initialize_envelope(
                server_name=SERVER_NAME, server_version=SERVER_VERSION
            ) is True
            assert install_initialize_envelope(
                server_name=SERVER_NAME, server_version=SERVER_VERSION
            ) is True

            assert uninstall_initialize_envelope() is True
            assert uninstall_initialize_envelope() is False

            async with Client(mcp) as client:
                payload = client.initialize_result.model_dump(by_alias=True, exclude_none=True)
            assert "resultType" not in payload
        finally:
            install_initialize_envelope(
                server_name=SERVER_NAME, server_version=SERVER_VERSION
            )

    @pytest.mark.asyncio
    async def test_a_failing_enrichment_still_completes_the_handshake(self):
        """A broken envelope must never cost the client its connection."""
        from prometheus_mcp_server.spec2026 import envelope as envelope_module

        with patch.object(envelope_module, "enrich_result", side_effect=RuntimeError("boom")):
            async with Client(mcp) as client:
                assert client.initialize_result.serverInfo.name == SERVER_NAME

    def test_install_degrades_when_the_sdk_shape_changes(self):
        """An incompatible SDK is logged, not raised."""
        from prometheus_mcp_server.spec2026 import envelope as envelope_module

        with patch.dict("sys.modules", {"mcp.server.session": None}):
            assert envelope_module.install_initialize_envelope(
                server_name=SERVER_NAME, server_version=SERVER_VERSION
            ) is False


class TestRetypeErrorGuards:
    """Defensive branches of the resource-not-found error retyping."""

    def test_a_non_mcp_error_carrying_the_code_is_left_alone(self):
        """Only the SDK's own McpError may be rebuilt."""
        from prometheus_mcp_server.spec2026.envelope import _retype_error

        class Lookalike(Exception):
            """Not an McpError, but shaped like one."""

            error = MagicMock(code=-32002, message="Resource not found: x", data=None)

        error = Lookalike()
        assert _retype_error(error, "resources/read") is error

    @pytest.mark.asyncio
    async def test_an_unrelated_handler_failure_propagates_unchanged(self):
        """A non-retyped exception keeps its original traceback."""
        from prometheus_mcp_server.spec2026.envelope import _wrap_handler

        async def handler(_req):
            raise ValueError("boom")

        wrapped = _wrap_handler(
            handler,
            method="resources/read",
            server_name=SERVER_NAME,
            server_version=SERVER_VERSION,
            ttl_ms=DEFAULT_CACHE_TTL_MS,
            cache_scope=DEFAULT_CACHE_SCOPE,
        )

        with pytest.raises(ValueError, match="boom"):
            await wrapped(None)
