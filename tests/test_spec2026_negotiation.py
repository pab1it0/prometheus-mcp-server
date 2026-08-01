"""Tests for MCP 2026-07-28 per-request negotiation (F5)."""

import pytest

from prometheus_mcp_server.spec2026.constants import (
    META_CLIENT_CAPABILITIES,
    META_CLIENT_INFO,
    META_LOG_LEVEL,
    META_PROTOCOL_VERSION,
    PROTOCOL_VERSION_2025_06,
    PROTOCOL_VERSION_2025_11,
    PROTOCOL_VERSION_2026,
    SUPPORTED_PROTOCOL_VERSIONS,
    UNSUPPORTED_PROTOCOL_VERSION,
)
from prometheus_mcp_server.spec2026.negotiation import (
    NegotiationError,
    _coerce_mapping,
    RequestNegotiation,
    UnsupportedProtocolVersionError,
    clear_current_negotiation,
    current_log_level,
    extract_request_meta,
    get_current_negotiation,
    may_emit_log_notifications,
    negotiate_request,
    negotiation_from_meta,
    negotiation_scope,
    reset_current_negotiation,
    set_current_negotiation,
    validate_protocol_version,
)


@pytest.fixture(autouse=True)
def reset_negotiation_context():
    """Guarantee a clean context var before and after every test."""
    clear_current_negotiation()
    yield
    clear_current_negotiation()


def make_request(meta):
    """Build a raw JSON-RPC style request dict carrying the given _meta."""
    return {"method": "tools/list", "params": {"_meta": meta}}


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

def test_error_code_and_supported_versions_are_as_specified():
    """The 2026-07-28 error code and version list match the specification."""
    assert UNSUPPORTED_PROTOCOL_VERSION == -32022
    assert SUPPORTED_PROTOCOL_VERSIONS == (
        "2026-07-28",
        "2025-11-25",
        "2025-06-18",
    )


# ---------------------------------------------------------------------------
# Absent _meta (legacy clients)
# ---------------------------------------------------------------------------

def test_extract_meta_from_request_without_params():
    """A request with no params yields an empty _meta."""
    assert extract_request_meta({"method": "tools/list"}) == {}


def test_extract_meta_from_params_without_meta():
    """Params without a _meta member yield an empty _meta."""
    assert extract_request_meta({"method": "tools/list", "params": {"name": "x"}}) == {}


def test_extract_meta_from_none_request():
    """A None request yields an empty _meta rather than raising."""
    assert extract_request_meta(None) == {}


def test_extract_meta_when_meta_is_explicitly_none():
    """An explicit _meta of None yields an empty _meta."""
    assert extract_request_meta(make_request(None)) == {}


def test_absent_meta_is_legacy_and_does_not_error():
    """A legacy client that sends no _meta negotiates successfully."""
    negotiation = negotiate_request({"method": "tools/list"})

    assert negotiation.protocol_version is None
    assert negotiation.is_legacy is True
    assert negotiation.client_capabilities == {}
    assert negotiation.client_info == {}
    assert negotiation.log_level is None
    assert negotiation.meta == {}


def test_absent_protocol_version_with_other_meta_present():
    """Other _meta keys are read even when no protocol version is declared."""
    negotiation = negotiate_request(make_request({"traceparent": "abc"}))

    assert negotiation.protocol_version is None
    assert negotiation.meta == {"traceparent": "abc"}


def test_validate_protocol_version_accepts_none():
    """Validation of an absent version returns None instead of raising."""
    assert validate_protocol_version(None) is None


# ---------------------------------------------------------------------------
# Malformed _meta
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_meta", ["not-a-dict", ["a", "b"], 42, True])
def test_extract_meta_ignores_non_mapping_meta(bad_meta):
    """A _meta that is not an object degrades to an empty mapping."""
    assert extract_request_meta(make_request(bad_meta)) == {}


@pytest.mark.parametrize("bad_meta", ["not-a-dict", ["a", "b"], 42, None])
def test_negotiation_from_malformed_meta_is_legacy(bad_meta):
    """Malformed _meta negotiates as a legacy request rather than erroring."""
    negotiation = negotiation_from_meta(bad_meta)

    assert negotiation.protocol_version is None
    assert negotiation.meta == {}


def test_malformed_client_capabilities_are_dropped():
    """A non-object clientCapabilities value degrades to an empty dict."""
    negotiation = negotiate_request(
        make_request(
            {
                META_PROTOCOL_VERSION: PROTOCOL_VERSION_2026,
                META_CLIENT_CAPABILITIES: "tools",
                META_CLIENT_INFO: ["nope"],
            }
        )
    )

    assert negotiation.client_capabilities == {}
    assert negotiation.client_info == {}
    assert negotiation.protocol_version == PROTOCOL_VERSION_2026


def test_non_string_meta_keys_are_stringified():
    """Non-string _meta keys do not break extraction."""
    assert extract_request_meta(make_request({1: "one"})) == {"1": "one"}


class _ExplodingModel:
    """Stand-in for an SDK model whose model_dump raises."""

    model_extra = {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}

    def model_dump(self, **kwargs):
        raise TypeError("unexpected keyword argument")


class _NonDictDumpModel:
    """Stand-in for an SDK model whose model_dump stops returning a dict."""

    model_extra = {"io.modelcontextprotocol/logLevel": "debug"}

    def model_dump(self, **kwargs):
        return ["not", "a", "dict"]


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        (_ExplodingModel(), {META_PROTOCOL_VERSION: PROTOCOL_VERSION_2026}),
        (_NonDictDumpModel(), {META_LOG_LEVEL: "debug"}),
    ],
)
def test_meta_model_falls_back_to_model_extra(model, expected):
    """A model_dump that raises or misbehaves degrades to model_extra."""
    assert extract_request_meta(make_request(model)) == expected


def test_coerce_mapping_returns_none_for_none():
    """The mapping coercion helper treats None as 'no mapping'."""
    assert _coerce_mapping(None) is None


def test_coerce_mapping_returns_none_for_opaque_object():
    """An object with neither model_dump nor model_extra is not a mapping."""
    assert _coerce_mapping(object()) is None


# ---------------------------------------------------------------------------
# Supported protocol versions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "version",
    [PROTOCOL_VERSION_2026, PROTOCOL_VERSION_2025_11, PROTOCOL_VERSION_2025_06],
)
def test_supported_protocol_versions_are_accepted(version):
    """Every advertised version negotiates successfully."""
    negotiation = negotiate_request(make_request({META_PROTOCOL_VERSION: version}))

    assert negotiation.protocol_version == version
    assert negotiation.is_legacy is False


def test_supported_version_captures_capabilities_and_client_info():
    """Client capabilities and info are captured verbatim for the request."""
    negotiation = negotiate_request(
        make_request(
            {
                META_PROTOCOL_VERSION: PROTOCOL_VERSION_2026,
                META_CLIENT_CAPABILITIES: {"tools": {"listChanged": True}},
                META_CLIENT_INFO: {"name": "probe", "version": "9.9.9"},
            }
        )
    )

    assert negotiation.client_capabilities == {"tools": {"listChanged": True}}
    assert negotiation.client_info == {"name": "probe", "version": "9.9.9"}


def test_capabilities_are_copied_not_aliased():
    """Mutating the caller's dict cannot alter captured capabilities."""
    capabilities = {"tools": {}}
    negotiation = negotiation_from_meta(
        {
            META_PROTOCOL_VERSION: PROTOCOL_VERSION_2026,
            META_CLIENT_CAPABILITIES: capabilities,
        }
    )

    capabilities["sampling"] = {}

    assert negotiation.client_capabilities == {"tools": {}}


def test_meta_is_preserved_for_downstream_modules():
    """Unmodelled _meta keys survive so trace context can be forwarded."""
    negotiation = negotiate_request(
        make_request(
            {
                META_PROTOCOL_VERSION: PROTOCOL_VERSION_2026,
                "traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01",
            }
        )
    )

    assert negotiation.meta["traceparent"] == "00-" + "a" * 32 + "-" + "b" * 16 + "-01"


def test_pydantic_meta_model_is_supported():
    """A pydantic RequestParams.Meta (extra=allow) is read like a dict."""
    types = pytest.importorskip("mcp.types")

    params = types.RequestParams(
        _meta=types.RequestParams.Meta.model_validate(
            {META_PROTOCOL_VERSION: PROTOCOL_VERSION_2026, META_LOG_LEVEL: "debug"}
        )
    )
    negotiation = negotiate_request(types.PingRequest(params=params))

    assert negotiation.protocol_version == PROTOCOL_VERSION_2026
    assert negotiation.log_level == "debug"


# ---------------------------------------------------------------------------
# Unsupported protocol version
# ---------------------------------------------------------------------------

def test_unsupported_protocol_version_raises_32022_with_data():
    """An unrecognised version raises -32022 carrying supported/requested."""
    with pytest.raises(UnsupportedProtocolVersionError) as excinfo:
        negotiate_request(make_request({META_PROTOCOL_VERSION: "1999-01-01"}))

    error = excinfo.value
    assert error.code == -32022
    assert error.code == UNSUPPORTED_PROTOCOL_VERSION
    assert error.data == {
        "supported": ["2026-07-28", "2025-11-25", "2025-06-18"],
        "requested": "1999-01-01",
    }
    assert error.requested == "1999-01-01"
    assert "1999-01-01" in error.message
    assert isinstance(error, NegotiationError)


def test_unsupported_protocol_version_error_data_round_trips():
    """to_error_data renders the full JSON-RPC error object."""
    with pytest.raises(UnsupportedProtocolVersionError) as excinfo:
        validate_protocol_version("2024-01-01")

    payload = excinfo.value.to_error_data()

    assert payload["code"] == -32022
    assert payload["data"]["requested"] == "2024-01-01"
    assert payload["data"]["supported"] == list(SUPPORTED_PROTOCOL_VERSIONS)
    assert isinstance(payload["message"], str)


def test_unsupported_protocol_version_converts_to_mcp_error():
    """The error converts to an SDK McpError carrying the same payload."""
    mcp_exceptions = pytest.importorskip("mcp.shared.exceptions")

    error = UnsupportedProtocolVersionError("1999-01-01").to_mcp_error()

    assert isinstance(error, mcp_exceptions.McpError)
    assert error.error.code == -32022
    assert error.error.data["requested"] == "1999-01-01"


@pytest.mark.parametrize("bad_version", [42, ["2026-07-28"], "", "   ", {"v": 1}])
def test_non_string_protocol_versions_are_rejected(bad_version):
    """A present-but-unusable version is rejected, never silently accepted."""
    with pytest.raises(UnsupportedProtocolVersionError) as excinfo:
        validate_protocol_version(bad_version)

    assert excinfo.value.code == -32022
    assert isinstance(excinfo.value.data["requested"], str)


def test_negotiation_error_carries_arbitrary_code():
    """The base error is reusable for the other 2026-07-28 codes."""
    error = NegotiationError(-32020, "HeaderMismatch", {"header": "Mcp-Method"})

    assert error.to_error_data() == {
        "code": -32020,
        "message": "HeaderMismatch",
        "data": {"header": "Mcp-Method"},
    }
    assert str(error) == "HeaderMismatch"


# ---------------------------------------------------------------------------
# ContextVar isolation between sequential requests
# ---------------------------------------------------------------------------

def test_context_var_defaults_to_none():
    """Outside a request there is no negotiation state."""
    assert get_current_negotiation() is None


def test_sequential_requests_do_not_leak_capabilities():
    """Request B never sees request A's declared capabilities."""
    first = negotiate_request(
        make_request(
            {
                META_PROTOCOL_VERSION: PROTOCOL_VERSION_2026,
                META_CLIENT_CAPABILITIES: {"sampling": {}},
                META_LOG_LEVEL: "info",
            }
        )
    )

    token = set_current_negotiation(first)
    assert get_current_negotiation().client_capabilities == {"sampling": {}}
    reset_current_negotiation(token)

    # Between requests nothing is in scope.
    assert get_current_negotiation() is None

    second = negotiate_request(make_request({META_PROTOCOL_VERSION: PROTOCOL_VERSION_2026}))
    token = set_current_negotiation(second)
    try:
        current = get_current_negotiation()
        assert current.client_capabilities == {}
        assert current.log_level is None
    finally:
        reset_current_negotiation(token)

    assert get_current_negotiation() is None


def test_negotiation_scope_resets_even_when_body_raises():
    """The scope helper cannot leak state when the handler fails."""
    negotiation = negotiation_from_meta({META_CLIENT_CAPABILITIES: {"tools": {}}})

    with pytest.raises(RuntimeError):
        with negotiation_scope(negotiation):
            assert get_current_negotiation() is negotiation
            raise RuntimeError("handler blew up")

    assert get_current_negotiation() is None


def test_nested_scopes_restore_the_outer_request():
    """Resetting an inner scope restores, not clears, the outer state."""
    outer = negotiation_from_meta({META_CLIENT_INFO: {"name": "outer"}})
    inner = negotiation_from_meta({META_CLIENT_INFO: {"name": "inner"}})

    with negotiation_scope(outer):
        with negotiation_scope(inner):
            assert get_current_negotiation().client_info == {"name": "inner"}
        assert get_current_negotiation().client_info == {"name": "outer"}

    assert get_current_negotiation() is None


def test_reset_with_none_token_clears_state():
    """Resetting without a token still leaves nothing behind."""
    set_current_negotiation(negotiation_from_meta({}))
    reset_current_negotiation(None)

    assert get_current_negotiation() is None


def test_reset_with_foreign_token_clears_state(monkeypatch):
    """A token that ContextVar.reset rejects still results in a clean context."""
    import contextvars

    from prometheus_mcp_server.spec2026 import negotiation as negotiation_module

    class BadVar:
        def set(self, value):
            self.value = value

        def get(self):
            return getattr(self, "value", None)

        def reset(self, token):
            raise ValueError("Token was created in a different Context")

    bad_var = BadVar()
    bad_var.set(negotiation_from_meta({META_CLIENT_INFO: {"name": "stale"}}))
    monkeypatch.setattr(negotiation_module, "_CURRENT_NEGOTIATION", bad_var)

    token = contextvars.ContextVar("throwaway", default=None).set(None)
    negotiation_module.reset_current_negotiation(token)

    assert bad_var.get() is None


@pytest.mark.asyncio
async def test_sequential_async_requests_are_isolated():
    """Two awaited handlers in one task do not share negotiation state."""

    async def handle(meta):
        negotiation = negotiation_from_meta(meta)
        with negotiation_scope(negotiation):
            return get_current_negotiation()

    first = await handle({META_LOG_LEVEL: "debug", META_CLIENT_INFO: {"name": "a"}})
    assert get_current_negotiation() is None

    second = await handle({})

    assert first.log_level == "debug"
    assert first.client_info == {"name": "a"}
    assert second.log_level is None
    assert second.client_info == {}
    assert get_current_negotiation() is None


# ---------------------------------------------------------------------------
# logLevel gate
# ---------------------------------------------------------------------------

def test_log_level_absent_forbids_notifications():
    """Without a declared log level the server must stay silent."""
    negotiation = negotiate_request(make_request({META_PROTOCOL_VERSION: PROTOCOL_VERSION_2026}))

    assert negotiation.log_level is None
    assert negotiation.may_log is False
    assert may_emit_log_notifications(negotiation) is False


def test_log_level_present_permits_notifications():
    """A declared log level opts the request into notifications/message."""
    negotiation = negotiate_request(
        make_request({META_PROTOCOL_VERSION: PROTOCOL_VERSION_2026, META_LOG_LEVEL: "warning"})
    )

    assert negotiation.log_level == "warning"
    assert negotiation.may_log is True
    assert may_emit_log_notifications(negotiation) is True


def test_log_level_gate_reads_the_current_request_by_default():
    """The gate consults the in-scope request when given no argument."""
    assert may_emit_log_notifications() is False
    assert current_log_level() is None

    with negotiation_scope(negotiation_from_meta({META_LOG_LEVEL: "error"})):
        assert may_emit_log_notifications() is True
        assert current_log_level() == "error"

    assert may_emit_log_notifications() is False
    assert current_log_level() is None


@pytest.mark.parametrize("bad_level", ["", "   ", 3, [], {"level": "info"}])
def test_malformed_log_level_keeps_notifications_disabled(bad_level):
    """An unusable log level fails closed instead of enabling notifications."""
    negotiation = negotiation_from_meta({META_LOG_LEVEL: bad_level})

    assert negotiation.log_level is None
    assert may_emit_log_notifications(negotiation) is False


def test_explicit_negotiation_argument_overrides_context():
    """An explicitly supplied state wins over whatever is in scope."""
    with negotiation_scope(negotiation_from_meta({META_LOG_LEVEL: "info"})):
        assert may_emit_log_notifications(RequestNegotiation()) is False
