"""Tests for the MCP 2026-07-28 compatibility layer as wired into server.py."""

import asyncio
import importlib.util
import json
import os
from unittest.mock import MagicMock, patch

import pytest
from fastmcp import Client
from mcp.shared.exceptions import McpError

import prometheus_mcp_server.server as server
from prometheus_mcp_server.server import (
    PrometheusConfig,
    SPEC_2026_STATUS,
    _current_request_meta,
    _env_int,
    _org_id_json_schema,
    _wrap_negotiation,
    clear_metrics_cache,
    config,
    execute_query,
    install_negotiation,
    install_spec_2026,
    make_prometheus_request,
    mcp,
    resolve_org_id,
    strict_header_asgi_middleware,
    tool_header_annotations,
)
from prometheus_mcp_server.spec2026.asgi import StrictHeaderMiddleware
from prometheus_mcp_server.spec2026.headers import validate_header_annotations
from prometheus_mcp_server.spec2026.negotiation import get_current_negotiation
from prometheus_mcp_server.spec2026.otel import get_trace_headers, set_trace_headers

VALID_TRACEPARENT = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"


@pytest.fixture(autouse=True)
def reset_shared_server_state():
    """Reset the metrics cache and re-arm the envelope on the shared server.

    Other modules exercise install/uninstall against the same FastMCP
    singleton, so this module re-installs the envelope with server.py's own
    settings rather than assuming import-time state survived, and stays correct
    no matter which order pytest happens to collect the files in.
    install_envelope replaces rather than nests, so this is safe to run before
    every test.
    """
    clear_metrics_cache()
    server.install_envelope(
        mcp,
        server_name=server.mcp_name,
        server_version=server.SERVER_VERSION,
        ttl_ms=config.cache_ttl_ms,
        cache_scope=config.cache_scope,
    )
    yield
    clear_metrics_cache()


@pytest.fixture
def restore_config():
    """Restore every config field this module mutates."""
    saved = {
        field: getattr(config, field)
        for field in (
            "url",
            "org_id",
            "custom_headers",
            "strict_headers",
            "cache_scope",
            "cache_ttl_ms",
            "allow_org_id_override",
        )
    }
    yield config
    for field, value in saved.items():
        setattr(config, field, value)


@pytest.fixture
def mock_response():
    """Build a successful Prometheus HTTP response."""
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        "status": "success",
        "data": {"resultType": "vector", "result": []},
    }
    return response


# ---------------------------------------------------------------------------
# F9 configuration surface
# ---------------------------------------------------------------------------

def test_config_exposes_the_spec_2026_fields_with_documented_defaults():
    """The four new env-backed fields default to the documented values."""
    defaults = PrometheusConfig(url="http://prometheus:9090")

    assert defaults.spec_2026_enabled is True
    assert defaults.cache_ttl_ms == 300000
    assert defaults.cache_scope == "public"
    assert defaults.strict_headers is False
    assert defaults.allow_org_id_override is False


@pytest.mark.parametrize("raw", ["300000.5", "5m", "abc", "", "300000ms"])
def test_a_malformed_cache_ttl_env_var_never_crashes_the_import(raw, monkeypatch):
    """Regression: `int(os.environ[...])` at module scope took the process down.

    A newly introduced, entirely optional env var must degrade to the documented
    default with a logged warning, never to an unhandled ValueError before any
    of the layer's defensive machinery has had a chance to run.
    """
    monkeypatch.setenv("PROMETHEUS_MCP_CACHE_TTL_MS", raw)
    assert _env_int("PROMETHEUS_MCP_CACHE_TTL_MS", 300000) == 300000


def test_a_valid_cache_ttl_env_var_is_still_honoured(monkeypatch):
    """The defensive parse must not swallow legitimate configuration."""
    monkeypatch.setenv("PROMETHEUS_MCP_CACHE_TTL_MS", "60000")
    assert _env_int("PROMETHEUS_MCP_CACHE_TTL_MS", 300000) == 60000


def test_a_malformed_cache_ttl_leaves_the_module_importable(monkeypatch):
    """End to end: importing server.py with a bad TTL must not raise."""
    with patch.dict(os.environ, {"PROMETHEUS_MCP_CACHE_TTL_MS": "5m"}):
        spec = importlib.util.spec_from_file_location(
            "prometheus_mcp_server._server_bad_ttl", server.__file__
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

    assert module.config.cache_ttl_ms == 300000


def test_live_config_carries_the_spec_2026_fields():
    """The module-level config was built with the new fields populated."""
    assert isinstance(config.spec_2026_enabled, bool)
    assert isinstance(config.cache_ttl_ms, int)
    assert config.cache_scope in ("public", "private")
    assert isinstance(config.strict_headers, bool)


def test_cache_ttl_ms_is_independent_of_the_metrics_cache_ttl():
    """The advertised ttlMs must not be conflated with the in-process cache TTL."""
    assert server._CACHE_TTL == 300
    assert config.cache_ttl_ms == 300000


# ---------------------------------------------------------------------------
# Layer installation
# ---------------------------------------------------------------------------

def test_the_layer_is_installed_on_the_module_server():
    """Import-time installation registered all three discovery paths."""
    assert SPEC_2026_STATUS["negotiation"] is True
    assert SPEC_2026_STATUS["envelope"] is True
    assert SPEC_2026_STATUS["initialize_envelope"] is True
    assert SPEC_2026_STATUS["discovery"] == {"jsonrpc": True, "http_route": True, "tool": True}


def test_install_spec_2026_reports_every_layer():
    """A clean install reports negotiation, discovery and both envelopes."""
    fake_server = MagicMock()
    with patch("prometheus_mcp_server.server.mcp", fake_server), \
         patch("prometheus_mcp_server.server.install_negotiation", return_value=True) as negotiation, \
         patch("prometheus_mcp_server.server.install_discovery", return_value={"jsonrpc": True}) as discovery, \
         patch("prometheus_mcp_server.server.install_envelope", return_value=True) as envelope, \
         patch("prometheus_mcp_server.server.install_initialize_envelope", return_value=True) as handshake:
        status = install_spec_2026()

    assert status == {
        "negotiation": True,
        "discovery": {"jsonrpc": True},
        "envelope": True,
        "initialize_envelope": True,
    }
    assert negotiation.call_count == 1
    assert discovery.call_count == 1
    assert envelope.call_count == 1
    assert handshake.call_count == 1


def test_install_spec_2026_reports_a_degraded_negotiation_layer():
    """An unusable SDK shape degrades to discovery and envelope only."""
    fake_server = MagicMock()
    with patch("prometheus_mcp_server.server.mcp", fake_server), \
         patch("prometheus_mcp_server.server.install_negotiation", return_value=False), \
         patch("prometheus_mcp_server.server.install_discovery", return_value={"jsonrpc": True}), \
         patch("prometheus_mcp_server.server.install_envelope", return_value=True), \
         patch("prometheus_mcp_server.server.install_initialize_envelope", return_value=True):
        status = install_spec_2026()

    assert status["negotiation"] is False
    assert status["envelope"] is True


def test_install_spec_2026_never_raises():
    """A failing sub-install is logged and the server keeps running."""
    fake_server = MagicMock()
    with patch("prometheus_mcp_server.server.mcp", fake_server), \
         patch("prometheus_mcp_server.server.install_discovery", side_effect=RuntimeError("boom")):
        status = install_spec_2026()

    assert status == {
        "negotiation": False,
        "discovery": {},
        "envelope": False,
        "initialize_envelope": False,
    }


def test_install_spec_2026_is_idempotent():
    """Regression: every call used to append another middleware instance.

    The layer is installed at import time and re-installed by several test
    modules against the same FastMCP singleton, so a non-idempotent installer
    multiplies the per-request work (and the log noise) without bound.
    """
    import mcp.types as mcp_types

    def wrapper_depth():
        """Count the spec2026 wrappers stacked on one request handler."""
        handler = mcp._mcp_server.request_handlers[mcp_types.ListToolsRequest]
        depth = 0
        while True:
            inner = getattr(handler, "_spec2026_envelope_inner", None) or getattr(
                handler, "_spec2026_negotiation_inner", None
            )
            if inner is None:
                return depth
            handler = inner
            depth += 1

    middleware_before = len(mcp.middleware)
    baseline = wrapper_depth()

    install_spec_2026()
    install_spec_2026()

    assert len(mcp.middleware) == middleware_before
    assert wrapper_depth() == baseline


# ---------------------------------------------------------------------------
# F7 - trace context on the outbound Prometheus request
# ---------------------------------------------------------------------------

@patch("prometheus_mcp_server.server.requests.get")
def test_trace_headers_are_forwarded_to_prometheus(mock_get, mock_response, restore_config):
    """Validated trace context from _meta rides along on the outbound request."""
    mock_get.return_value = mock_response
    restore_config.url = "http://test:9090"
    restore_config.custom_headers = None

    token = set_trace_headers({"traceparent": VALID_TRACEPARENT, "baggage": "tenant=acme"})
    try:
        make_prometheus_request("query", params={"query": "up"})
    finally:
        server.reset_trace_headers(token)

    headers = mock_get.call_args[1]["headers"]
    assert headers["traceparent"] == VALID_TRACEPARENT
    assert headers["baggage"] == "tenant=acme"


@patch("prometheus_mcp_server.server.requests.get")
def test_custom_headers_win_over_per_request_trace_headers(mock_get, mock_response, restore_config):
    """Operator configuration must never be silently overwritten by a request."""
    mock_get.return_value = mock_response
    restore_config.url = "http://test:9090"
    restore_config.custom_headers = {"traceparent": "operator-value"}

    token = set_trace_headers({"traceparent": VALID_TRACEPARENT})
    try:
        make_prometheus_request("query", params={"query": "up"})
    finally:
        server.reset_trace_headers(token)

    assert mock_get.call_args[1]["headers"]["traceparent"] == "operator-value"


@patch("prometheus_mcp_server.server.requests.get")
def test_no_trace_headers_without_request_context(mock_get, mock_response, restore_config):
    """A 2025-11-25 client that sends no trace context sees no extra headers."""
    mock_get.return_value = mock_response
    restore_config.url = "http://test:9090"
    restore_config.custom_headers = None

    make_prometheus_request("query", params={"query": "up"})

    headers = mock_get.call_args[1]["headers"]
    assert "traceparent" not in headers
    assert "baggage" not in headers


# ---------------------------------------------------------------------------
# F6 - the x-mcp-header annotated org_id parameter
# ---------------------------------------------------------------------------

def test_org_id_json_schema_collapses_the_optional_union():
    """The annotation needs a primitive type, so the anyOf union is flattened.

    The null default goes with it: `default: null` does not validate against
    the `type: "string"` the annotation forces onto the property.
    """
    schema = {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None}

    _org_id_json_schema(schema)

    assert schema == {"type": "string", "x-mcp-header": "Org-Id"}


@pytest.mark.asyncio
async def test_execute_query_declares_a_valid_header_annotation():
    """The published inputSchema passes the specification's annotation rules."""
    async with Client(mcp) as client:
        tools = await client.list_tools()

    tool = next(tool for tool in tools if tool.name == "execute_query")
    org_id = tool.inputSchema["properties"]["org_id"]

    assert org_id["x-mcp-header"] == "Org-Id"
    assert org_id["type"] == "string"
    assert validate_header_annotations(tool.inputSchema) == []


@pytest.mark.asyncio
async def test_the_published_input_schema_validates_its_own_default():
    """Regression: the advertised schema contradicted itself.

    ``default: null`` on a ``type: "string"`` property means a client that
    validates its arguments against the published inputSchema -- or that
    materialises declared defaults into the argument object, which several
    tool-calling backends do -- rejects a call the server would have accepted.
    """
    jsonschema = pytest.importorskip("jsonschema")

    async with Client(mcp) as client:
        tools = await client.list_tools()

    schema = next(tool for tool in tools if tool.name == "execute_query").inputSchema
    org_id = schema["properties"]["org_id"]

    validator = jsonschema.Draft202012Validator(schema)
    assert list(validator.iter_errors({"query": "up"})) == []
    assert list(validator.iter_errors({"query": "up", "org_id": "tenant-9"})) == []
    # Echoing the advertised default back must validate against the schema.
    assert list(validator.iter_errors({"query": "up", "org_id": org_id["default"]})) == []
    jsonschema.Draft202012Validator.check_schema(schema)


@pytest.mark.asyncio
@patch("prometheus_mcp_server.server.make_prometheus_request")
async def test_execute_query_forwards_the_org_id_argument(mock_make_request):
    """A supplied org_id is threaded through to the Prometheus request."""
    mock_make_request.return_value = {"resultType": "vector", "result": []}

    await execute_query(query="up", org_id="tenant-9")

    mock_make_request.assert_called_once_with("query", params={"query": "up"}, org_id="tenant-9")


@pytest.mark.asyncio
@pytest.mark.parametrize("org_id", ["", None])
@patch("prometheus_mcp_server.server.make_prometheus_request")
async def test_execute_query_without_org_id_keeps_the_legacy_call(mock_make_request, org_id):
    """Omitting the argument leaves the single-tenant call exactly as it was."""
    mock_make_request.return_value = {"resultType": "vector", "result": []}

    await execute_query(query="up", org_id=org_id)

    mock_make_request.assert_called_once_with("query", params={"query": "up"})


@patch("prometheus_mcp_server.server.requests.get")
def test_a_configured_org_id_always_beats_the_client_supplied_one(mock_get, mock_response, restore_config):
    """Regression: any caller could read another tenant's metrics.

    X-Scope-OrgID is the only tenancy boundary in Mimir/Cortex/Thanos, and the
    caller is typically an LLM acting on untrusted content, so an operator who
    pinned ORG_ID must not have it displaced by a tool argument.
    """
    mock_get.return_value = mock_response
    restore_config.url = "http://test:9090"
    restore_config.org_id = "tenant-a"
    restore_config.custom_headers = None
    restore_config.allow_org_id_override = False

    make_prometheus_request("query", params={"query": "up"}, org_id="tenant-b")

    assert mock_get.call_args[1]["headers"]["X-Scope-OrgID"] == "tenant-a"


@patch("prometheus_mcp_server.server.requests.get")
def test_the_override_is_honoured_when_the_operator_opts_in(mock_get, mock_response, restore_config):
    """Multi-tenant deployments can still opt into per-call tenants."""
    mock_get.return_value = mock_response
    restore_config.url = "http://test:9090"
    restore_config.org_id = "tenant-a"
    restore_config.custom_headers = None
    restore_config.allow_org_id_override = True

    make_prometheus_request("query", params={"query": "up"}, org_id="tenant-b")

    assert mock_get.call_args[1]["headers"]["X-Scope-OrgID"] == "tenant-b"


@patch("prometheus_mcp_server.server.requests.get")
def test_a_per_call_tenant_applies_when_none_is_configured(mock_get, mock_response, restore_config):
    """With no ORG_ID pinned there is no boundary to cross."""
    mock_get.return_value = mock_response
    restore_config.url = "http://test:9090"
    restore_config.org_id = ""
    restore_config.custom_headers = None
    restore_config.allow_org_id_override = False

    make_prometheus_request("query", params={"query": "up"}, org_id="tenant-b")

    assert mock_get.call_args[1]["headers"]["X-Scope-OrgID"] == "tenant-b"


@pytest.mark.parametrize("hostile", [
    "acme\r\nX-Injected: yes",
    "acme\nX-Injected: yes",
    "acme\x00",
    " acme",
    "acme ",
    "ünicode",
    42,
])
@patch("prometheus_mcp_server.server.requests.get")
def test_an_unsafe_org_id_never_reaches_an_outbound_header(mock_get, mock_response, restore_config, hostile):
    """Regression: raw client input was written into X-Scope-OrgID unchecked.

    requests' own header validation happens to block the CRLF case today, but
    that turns a hostile argument into a RequestException instead of a clean
    refusal, and any move off requests would make it a real injection.
    """
    mock_get.return_value = mock_response
    restore_config.url = "http://test:9090"
    restore_config.org_id = ""
    restore_config.custom_headers = None
    restore_config.allow_org_id_override = True

    make_prometheus_request("query", params={"query": "up"}, org_id=hostile)

    assert "X-Scope-OrgID" not in mock_get.call_args[1]["headers"]


def test_resolve_org_id_falls_back_to_the_configured_tenant(restore_config):
    """An unsafe per-call value must not disable the configured tenant either."""
    restore_config.org_id = "tenant-a"
    restore_config.allow_org_id_override = True

    assert resolve_org_id("acme\r\nX-Injected: yes") == "tenant-a"
    assert resolve_org_id("tenant-b") == "tenant-b"
    assert resolve_org_id(None) == "tenant-a"


# ---------------------------------------------------------------------------
# Request-context helpers
# ---------------------------------------------------------------------------

def test_current_request_meta_is_none_outside_a_request():
    """No request in scope must not raise; there is simply nothing to read."""
    assert _current_request_meta() is None


def test_current_request_meta_reads_the_sdk_context():
    """The authoritative _meta comes from the SDK's own request context."""
    request_context = MagicMock()
    request_context.meta = {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}

    token = server._sdk_request_ctx.set(request_context)
    try:
        assert _current_request_meta() == request_context.meta
    finally:
        server._sdk_request_ctx.reset(token)


def test_current_request_meta_degrades_when_the_context_misbehaves():
    """An unexpected SDK failure is logged, not propagated."""
    broken = MagicMock()
    broken.get.side_effect = RuntimeError("boom")

    with patch("prometheus_mcp_server.server._sdk_request_ctx", broken):
        assert _current_request_meta() is None


def test_current_request_meta_is_none_without_the_sdk_context():
    """An incompatible SDK leaves the negotiation layer inert, not broken."""
    with patch("prometheus_mcp_server.server._sdk_request_ctx", None):
        assert _current_request_meta() is None


@pytest.mark.asyncio
async def test_tool_header_annotations_reads_the_registered_tool():
    """execute_query's Org-Id annotation is discoverable from the registry."""
    annotations = await tool_header_annotations("execute_query")

    assert [annotation.header_name for annotation in annotations] == ["Mcp-Param-Org-Id"]
    assert annotations[0].path == ("org_id",)


@pytest.mark.asyncio
async def test_tool_header_annotations_handles_a_missing_tool():
    """An unresolvable tool yields no annotations instead of raising."""
    assert await tool_header_annotations("no-such-tool") == []


@pytest.mark.asyncio
async def test_tool_header_annotations_survives_a_registry_failure():
    """A registry that raises degrades to "no annotations", never to a 500."""
    async def explode(_name):
        raise RuntimeError("registry down")

    with patch.object(mcp, "get_tool", explode):
        assert await tool_header_annotations("execute_query") == []


@pytest.mark.asyncio
async def test_tool_header_annotations_ignores_a_non_string_name():
    """A malformed tool name is rejected before the registry is touched."""
    assert await tool_header_annotations(None) == []


# ---------------------------------------------------------------------------
# F6 - strict header validation is wired in at the ASGI layer
# ---------------------------------------------------------------------------

def test_strict_header_middleware_is_absent_unless_enabled(restore_config):
    """The switch is opt-in, so nothing is added by default."""
    restore_config.strict_headers = False
    assert strict_header_asgi_middleware() is None


def test_strict_header_middleware_is_built_when_enabled(restore_config):
    """Turning the switch on yields a Starlette middleware entry."""
    restore_config.strict_headers = True
    entry = strict_header_asgi_middleware()

    assert entry is not None
    assert entry.cls is StrictHeaderMiddleware


def test_strict_header_middleware_degrades_instead_of_raising(restore_config):
    """An unavailable Starlette must not stop the server from starting."""
    restore_config.strict_headers = True
    with patch("prometheus_mcp_server.server.strict_header_middleware", side_effect=RuntimeError("boom")):
        assert strict_header_asgi_middleware() is None


def test_run_server_passes_the_middleware_to_the_http_transport(restore_config):
    """main.py must actually hand the middleware to FastMCP, not just build it."""
    import prometheus_mcp_server.main as main_module

    restore_config.strict_headers = True
    sentinel = object()

    with patch.object(main_module, "setup_environment", return_value=True), \
         patch.object(main_module.config, "mcp_server_config",
                      server.MCPServerConfig(mcp_server_transport="http", mcp_bind_host="h", mcp_bind_port=1)), \
         patch.object(main_module, "strict_header_asgi_middleware", return_value=sentinel), \
         patch.object(main_module.mcp, "run") as run:
        main_module.run_server()

    assert run.call_args[1]["middleware"] == [sentinel]


def test_run_server_adds_no_middleware_when_strict_headers_are_off():
    """The default HTTP run path stays byte-for-byte what it was."""
    import prometheus_mcp_server.main as main_module

    with patch.object(main_module, "setup_environment", return_value=True), \
         patch.object(main_module.config, "mcp_server_config",
                      server.MCPServerConfig(mcp_server_transport="http", mcp_bind_host="h", mcp_bind_port=1)), \
         patch.object(main_module, "strict_header_asgi_middleware", return_value=None), \
         patch.object(main_module.mcp, "run") as run:
        main_module.run_server()

    assert "middleware" not in run.call_args[1]


# ---------------------------------------------------------------------------
# F5 - the negotiation wrapper
#
# Negotiation lives below FastMCP, on the low-level request handlers, because
# FastMCP renders an exception raised during tools/call as a successful result
# with isError=true -- which strips the -32022 code and the data.supported list
# a client needs in order to renegotiate.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_negotiation_publishes_and_resets_the_request_state():
    """Negotiation and trace state live only for the duration of the request."""
    observed = {}

    async def handler(_req):
        observed["negotiation"] = get_current_negotiation()
        observed["trace"] = get_trace_headers()
        return "done"

    meta = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientInfo": {"name": "probe"},
        "traceparent": VALID_TRACEPARENT,
    }

    with patch("prometheus_mcp_server.server._current_request_meta", return_value=meta):
        result = await _wrap_negotiation(handler)(None)

    assert result == "done"
    assert observed["negotiation"].protocol_version == "2026-07-28"
    assert observed["negotiation"].client_info == {"name": "probe"}
    assert observed["trace"] == {"traceparent": VALID_TRACEPARENT}
    # Nothing survives into the next request.
    assert get_current_negotiation() is None
    assert get_trace_headers() == {}


@pytest.mark.asyncio
async def test_negotiation_resets_state_even_when_the_handler_raises():
    """The reset lives in a finally block, so a failure cannot leak state."""

    async def handler(_req):
        raise ValueError("handler exploded")

    meta = {"io.modelcontextprotocol/protocolVersion": "2026-07-28", "traceparent": VALID_TRACEPARENT}

    with patch("prometheus_mcp_server.server._current_request_meta", return_value=meta):
        with pytest.raises(ValueError):
            await _wrap_negotiation(handler)(None)

    assert get_current_negotiation() is None
    assert get_trace_headers() == {}


@pytest.mark.asyncio
async def test_negotiation_accepts_a_legacy_request_without_a_protocol_version():
    """A 2025-11-25 client that declares nothing is served normally."""
    observed = {}

    async def handler(_req):
        observed["negotiation"] = get_current_negotiation()
        return "done"

    with patch("prometheus_mcp_server.server._current_request_meta", return_value=None):
        assert await _wrap_negotiation(handler)(None) == "done"

    assert observed["negotiation"].is_legacy is True
    assert observed["negotiation"].may_log is False


@pytest.mark.asyncio
async def test_negotiation_rejects_an_unsupported_protocol_version():
    """An unrecognised version fails with JSON-RPC code -32022."""

    async def handler(_req):  # pragma: no cover - must never be reached
        raise AssertionError("handler must not run")

    meta = {"io.modelcontextprotocol/protocolVersion": "1999-01-01"}

    with patch("prometheus_mcp_server.server._current_request_meta", return_value=meta):
        with pytest.raises(McpError) as excinfo:
            await _wrap_negotiation(handler)(None)

    assert excinfo.value.error.code == -32022
    assert excinfo.value.error.data["requested"] == "1999-01-01"
    assert "2026-07-28" in excinfo.value.error.data["supported"]


@pytest.mark.asyncio
async def test_negotiation_supports_a_synchronous_handler():
    """The SDK's handlers are async, but the wrapper must not assume it."""
    def handler(_req):
        return "sync"

    with patch("prometheus_mcp_server.server._current_request_meta", return_value=None):
        assert await _wrap_negotiation(handler)(None) == "sync"


def test_install_negotiation_degrades_without_request_handlers():
    """An incompatible SDK leaves the layer off rather than crashing."""
    assert install_negotiation(MagicMock(_mcp_server=MagicMock(request_handlers=None))) is False


def test_install_negotiation_reports_an_empty_handler_table():
    """Nothing to wrap means the layer is inactive, not installed."""
    assert install_negotiation(MagicMock(_mcp_server=MagicMock(request_handlers={}))) is False


def test_install_negotiation_skips_a_non_callable_handler():
    """A handler table holding junk is logged, not dereferenced."""
    handlers = {object(): "not callable"}
    assert install_negotiation(MagicMock(_mcp_server=MagicMock(request_handlers=handlers))) is False


def test_install_negotiation_never_raises():
    """Any unexpected failure degrades to False."""
    broken = MagicMock()
    type(broken)._mcp_server = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    assert install_negotiation(broken) is False


# ---------------------------------------------------------------------------
# End-to-end behaviour through the in-memory client
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tools_list_is_sorted_and_enveloped():
    """F2/F3: results carry the envelope and tools come back in name order."""
    import mcp.types as mcp_types

    async with Client(mcp) as client:
        result = await client.session.send_request(
            mcp_types.ClientRequest(mcp_types.ListToolsRequest()),
            mcp_types.ListToolsResult,
        )

    payload = result.model_dump(by_alias=True, exclude_none=True)
    names = [tool["name"] for tool in payload["tools"]]

    assert names == sorted(names)
    assert payload["resultType"] == "complete"
    assert payload["ttlMs"] == config.cache_ttl_ms
    assert payload["cacheScope"] == config.cache_scope
    assert payload["_meta"]["io.modelcontextprotocol/serverInfo"] == {
        "name": server.mcp_name,
        "version": server.SERVER_VERSION,
    }


@pytest.mark.asyncio
async def test_server_discover_is_dispatched_as_a_json_rpc_method():
    """F4: server/discover answers over the real JSON-RPC path."""
    import mcp.types as mcp_types
    from prometheus_mcp_server.spec2026.discovery import DiscoverRequest, DiscoverResult

    async with Client(mcp) as client:
        result = await client.session.send_request(
            mcp_types.ClientRequest(DiscoverRequest()),
            DiscoverResult,
        )

    payload = result.model_dump(by_alias=True, exclude_none=True)

    assert payload["resultType"] == "complete"
    assert payload["supportedVersions"][0] == "2026-07-28"
    assert payload["capabilities"]["extensions"] == {}
    assert payload["cacheScope"] == "public"
    assert payload["_meta"]["io.modelcontextprotocol/serverInfo"]["version"] == server.SERVER_VERSION


@pytest.mark.asyncio
async def test_server_discover_is_also_an_mcp_tool():
    """F4: the payload stays reachable as a tool on every transport."""
    async with Client(mcp) as client:
        result = await client.call_tool("server_discover", {})

    assert result.data["supportedVersions"][0] == "2026-07-28"


def test_server_discover_http_route_serves_the_payload():
    """F4: GET /server/discover is the pre-handshake answer for HTTP clients."""
    routes = {
        route.path: route
        for route in mcp.http_app().routes
        if hasattr(route, "path")
    }
    assert "/server/discover" in routes

    response = asyncio.run(routes["/server/discover"].endpoint(MagicMock()))
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload["supportedVersions"] == ["2026-07-28", "2025-11-25", "2025-06-18"]
    assert payload["ttlMs"] == 3600000


@pytest.mark.asyncio
async def test_tools_list_envelope_fields_satisfy_the_specification():
    """F2: the envelope fields are spec-valid in their own right, not just equal
    to config -- ttlMs is a non-negative integer and cacheScope is an enum member.
    """
    import mcp.types as mcp_types

    async with Client(mcp) as client:
        result = await client.session.send_request(
            mcp_types.ClientRequest(mcp_types.ListToolsRequest()),
            mcp_types.ListToolsResult,
        )

    payload = result.model_dump(by_alias=True, exclude_none=True)

    assert payload["resultType"] == "complete"
    # bool is a subclass of int, so an accidental True would satisfy a bare
    # isinstance check while serializing as `true` on the wire.
    assert isinstance(payload["ttlMs"], int) and not isinstance(payload["ttlMs"], bool)
    assert payload["ttlMs"] >= 0
    assert payload["cacheScope"] in ("public", "private")

    server_info = payload["_meta"]["io.modelcontextprotocol/serverInfo"]
    assert server_info["name"] and isinstance(server_info["name"], str)
    assert server_info["version"] and isinstance(server_info["version"], str)


@pytest.mark.asyncio
async def test_tools_list_order_is_the_sorted_registry():
    """F3: the published order is the registry sorted by name, deterministically."""
    async with Client(mcp) as client:
        first = await client.list_tools()
        second = await client.list_tools()

    # run_middleware=False reads the registry directly, so this is the raw
    # registration order the envelope is expected to sort.
    registered = sorted(tool.name for tool in await mcp.list_tools(run_middleware=False))
    names = [tool.name for tool in first]

    assert len(names) > 1, "need several tools for the ordering assertion to mean anything"
    assert names == registered
    assert names == [tool.name for tool in second], "ordering must be stable across calls"


@pytest.mark.asyncio
@patch("prometheus_mcp_server.server.requests.get")
async def test_tools_call_carries_result_type_but_no_cache_fields(mock_get, mock_response, restore_config):
    """F2: tools/call is not in the cacheable set, so it gets no ttlMs/cacheScope."""
    mock_get.return_value = mock_response
    restore_config.url = "http://test:9090"

    async with Client(mcp) as client:
        result = await client.session.call_tool("execute_query", {"query": "up"})

    payload = result.model_dump(by_alias=True, mode="json", exclude_none=True)

    assert result.isError is False
    assert payload["resultType"] == "complete"
    assert payload["_meta"]["io.modelcontextprotocol/serverInfo"] == {
        "name": server.mcp_name,
        "version": server.SERVER_VERSION,
    }
    assert "ttlMs" not in payload
    assert "cacheScope" not in payload


# ---------------------------------------------------------------------------
# Backward compatibility: PROMETHEUS_MCP_SPEC_2026=false
#
# The switch is read at import time, so the guarantee can only be tested by
# loading a second, independent copy of server.py with the variable unset. The
# copy is deliberately not registered in sys.modules: it must not shadow the
# real module for any other test.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def legacy_server():
    """Load a fresh copy of server.py with the 2026 layer switched off."""
    with patch.dict(os.environ, {"PROMETHEUS_MCP_SPEC_2026": "false"}):
        spec = importlib.util.spec_from_file_location(
            "prometheus_mcp_server._server_spec2026_disabled", server.__file__
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def test_disabled_layer_is_not_installed(legacy_server):
    """The master switch turns the whole layer off, not just parts of it."""
    assert legacy_server.config.spec_2026_enabled is False
    assert legacy_server.SPEC_2026_STATUS == {}


@pytest.mark.asyncio
async def test_disabled_layer_serves_plain_2025_results(legacy_server):
    """A 2025-11-25 client sees byte-for-byte the results it always saw."""
    import mcp.types as mcp_types

    async with Client(legacy_server.mcp) as client:
        result = await client.session.send_request(
            mcp_types.ClientRequest(mcp_types.ListToolsRequest()),
            mcp_types.ListToolsResult,
        )

    payload = result.model_dump(by_alias=True, exclude_none=True)

    assert list(payload.keys()) == ["tools"]
    assert "resultType" not in payload
    assert "ttlMs" not in payload
    assert "cacheScope" not in payload
    assert "_meta" not in payload
    # Untouched means unsorted: the order is the registration order FastMCP
    # itself reports, which is exactly what a 2025-11-25 client used to get.
    registration_order = [
        tool.name for tool in await legacy_server.mcp.list_tools(run_middleware=False)
    ]
    assert [tool.name for tool in result.tools] == registration_order
    assert registration_order != sorted(registration_order), (
        "the fixture is only meaningful while registration order differs from sorted order"
    )


@pytest.mark.asyncio
async def test_disabled_layer_registers_no_discovery_surfaces(legacy_server):
    """server/discover is part of the layer and must vanish with it."""
    async with Client(legacy_server.mcp) as client:
        tools = {tool.name for tool in await client.list_tools()}

    assert "server_discover" not in tools

    paths = {route.path for route in legacy_server.mcp.http_app().routes if hasattr(route, "path")}
    assert "/server/discover" not in paths
    assert "/health" in paths, "the pre-existing health route must survive"


@pytest.mark.asyncio
async def test_disabled_layer_still_queries_prometheus_end_to_end(legacy_server, mock_response):
    """The actual job of the server keeps working with the layer switched off."""
    legacy_server.config.url = "http://test:9090"
    mock_response.json.return_value = {
        "status": "success",
        "data": {"resultType": "vector", "result": [{"metric": {"job": "api"}, "value": [1, "1"]}]},
    }

    with patch.object(legacy_server.requests, "get", return_value=mock_response) as mock_get:
        async with Client(legacy_server.mcp) as client:
            # session.call_tool returns the raw protocol CallToolResult, which is
            # what the envelope would have decorated; the fastmcp-level wrapper
            # returned by client.call_tool drops any unknown top-level fields.
            result = await client.session.call_tool("execute_query", {"query": "up"})

    assert result.isError is False
    assert result.structuredContent["resultType"] == "vector"
    assert result.structuredContent["result"][0]["metric"] == {"job": "api"}
    assert mock_get.call_args[0][0] == "http://test:9090/api/v1/query"
    # No trace-context plumbing runs, so the outbound headers stay empty.
    assert mock_get.call_args[1]["headers"] == {}

    payload = result.model_dump(by_alias=True, mode="json", exclude_none=True)
    assert "resultType" not in payload, "the CallToolResult envelope must stay off"
    assert "_meta" not in payload


@pytest.mark.asyncio
async def test_disabled_layer_ignores_a_2026_protocol_version(legacy_server):
    """Without the negotiation middleware an unknown version is not rejected.

    The layer is off, so the server behaves exactly as 2025-11-25 does: _meta is
    passed through untouched and no -32022 is raised.
    """
    import mcp.types as mcp_types

    request = mcp_types.ListToolsRequest(
        params=mcp_types.PaginatedRequestParams(
            _meta=mcp_types.RequestParams.Meta.model_validate(
                {"io.modelcontextprotocol/protocolVersion": "1999-01-01"}
            )
        )
    )

    async with Client(legacy_server.mcp) as client:
        result = await client.session.send_request(
            mcp_types.ClientRequest(request), mcp_types.ListToolsResult
        )

    assert len(result.tools) > 0


@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore::UserWarning")
async def test_an_unknown_method_is_still_rejected():
    """Extending the request union must not make the server accept anything."""
    import mcp.types as mcp_types
    from pydantic import ConfigDict

    class BogusRequest(mcp_types.Request):
        model_config = ConfigDict(extra="allow")
        method: str = "bogus/method"
        params: None = None

    async with Client(mcp) as client:
        with pytest.raises(McpError) as excinfo:
            await client.session.send_request(
                mcp_types.ClientRequest.model_construct(root=BogusRequest()),
                mcp_types.EmptyResult,
            )

    assert excinfo.value.error.code == -32602


# ---------------------------------------------------------------------------
# F5 end to end: the JSON-RPC error code must survive tools/call
# ---------------------------------------------------------------------------

def _call_tool_request(name, arguments, protocol_version=None):
    """Build a CallToolRequest optionally declaring a protocol version."""
    import mcp.types as mcp_types

    meta = None
    if protocol_version is not None:
        meta = mcp_types.RequestParams.Meta.model_validate(
            {"io.modelcontextprotocol/protocolVersion": protocol_version}
        )
    return mcp_types.CallToolRequest(
        params=mcp_types.CallToolRequestParams(name=name, arguments=arguments, _meta=meta)
    )


@pytest.mark.asyncio
async def test_tools_call_reports_an_unsupported_version_as_a_json_rpc_error():
    """Regression: -32022 was downgraded to a resultType "complete" tool result.

    FastMCP renders an exception raised inside its middleware chain during
    tools/call as a successful CallToolResult with isError=true, so the client
    saw a well-formed *complete* result, could not read the code, and never
    received the data.supported list it needs in order to renegotiate.
    """
    import mcp.types as mcp_types

    async with Client(mcp) as client:
        with pytest.raises(McpError) as excinfo:
            await client.session.send_request(
                mcp_types.ClientRequest(
                    _call_tool_request("health_check", {}, protocol_version="1999-01-01")
                ),
                mcp_types.CallToolResult,
            )

    assert excinfo.value.error.code == -32022
    assert excinfo.value.error.data["requested"] == "1999-01-01"
    assert "2026-07-28" in excinfo.value.error.data["supported"]


@pytest.mark.asyncio
async def test_tools_list_reports_the_same_code_as_tools_call():
    """The two paths must not disagree about a protocol-level rejection."""
    import mcp.types as mcp_types

    request = mcp_types.ListToolsRequest(
        params=mcp_types.PaginatedRequestParams(
            _meta=mcp_types.RequestParams.Meta.model_validate(
                {"io.modelcontextprotocol/protocolVersion": "1999-01-01"}
            )
        )
    )

    async with Client(mcp) as client:
        with pytest.raises(McpError) as excinfo:
            await client.session.send_request(
                mcp_types.ClientRequest(request), mcp_types.ListToolsResult
            )

    assert excinfo.value.error.code == -32022


@pytest.mark.asyncio
@patch("prometheus_mcp_server.server.requests.get")
async def test_trace_context_still_reaches_prometheus_through_the_wrapper(
    mock_get, mock_response, restore_config
):
    """Negotiation moved below FastMCP; the contextvars must still be published."""
    import mcp.types as mcp_types

    mock_get.return_value = mock_response
    restore_config.url = "http://test:9090"
    restore_config.custom_headers = None

    request = mcp_types.CallToolRequest(
        params=mcp_types.CallToolRequestParams(
            name="execute_query",
            arguments={"query": "up"},
            _meta=mcp_types.RequestParams.Meta.model_validate(
                {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "traceparent": VALID_TRACEPARENT,
                }
            ),
        )
    )

    async with Client(mcp) as client:
        result = await client.session.send_request(
            mcp_types.ClientRequest(request), mcp_types.CallToolResult
        )

    assert result.isError is False
    assert mock_get.call_args[1]["headers"]["traceparent"] == VALID_TRACEPARENT
    # And it does not leak past the request.
    assert get_trace_headers() == {}
