"""Tests for MCP 2026-07-28 HTTP header validation and x-mcp-header support (F6).

Covers the three independent concerns of
``prometheus_mcp_server.spec2026.headers``:

- The base64 sentinel codec, including every example given in the spec.
- ``Mcp-Method`` / ``Mcp-Name`` / ``MCP-Protocol-Version`` / ``Mcp-Param-*``
  validation against the JSON-RPC body, including numeric comparison.
- ``x-mcp-header`` input-schema validation and value extraction.
"""

import pytest

from prometheus_mcp_server.spec2026.headers import (
    ANNOTATION_KEY,
    HEADER_MCP_METHOD,
    HEADER_MCP_NAME,
    HEADER_MCP_PROTOCOL_VERSION,
    HEADER_MISMATCH,
    META_PROTOCOL_VERSION,
    PARAM_HEADER_PREFIX,
    SENTINEL_PREFIX,
    SENTINEL_SUFFIX,
    HeaderAnnotation,
    collect_header_annotations,
    decode_header_value,
    encode_header_value,
    extract_param_headers,
    header_mismatch_error,
    is_safe_plain_ascii,
    looks_like_sentinel,
    strict_headers_enabled,
    validate_header_annotations,
    validate_request_headers,
)


def _codes(errors):
    """Collapse schema annotation errors to their machine-readable codes."""
    return [error.code for error in errors]


def _reasons(errors):
    """Collapse header validation errors to their machine-readable reasons."""
    return [error.reason for error in errors]


# ---------------------------------------------------------------------------
# (A) Base64 sentinel codec
# ---------------------------------------------------------------------------

class TestBase64SentinelCodec:
    """The =?base64?{b64}?= codec."""

    @pytest.mark.parametrize("value,expected", [
        ("us-west1", "us-west1"),
        ("Hello, 世界", "=?base64?SGVsbG8sIOS4lueVjA==?="),
        (" padded ", "=?base64?IHBhZGRlZCA=?="),
        ("line1\nline2", "=?base64?bGluZTEKbGluZTI=?="),
        ("=?base64?literal?=", "=?base64?PT9iYXNlNjQ/bGl0ZXJhbD89?="),
    ])
    def test_spec_examples_encode_verbatim(self, value, expected):
        """Every worked example from the specification encodes exactly."""
        assert encode_header_value(value) == expected

    @pytest.mark.parametrize("value", [
        "us-west1",
        "Hello, 世界",
        " padded ",
        "line1\nline2",
        "=?base64?literal?=",
    ])
    def test_spec_examples_round_trip(self, value):
        """Decoding the encoded form recovers the original value."""
        assert decode_header_value(encode_header_value(value)) == value

    def test_plain_ascii_is_not_encoded(self):
        """Safe plain ASCII travels verbatim, so no sentinel is added."""
        assert encode_header_value("us-west1") == "us-west1"
        assert not looks_like_sentinel(encode_header_value("us-west1"))

    @pytest.mark.parametrize("value", [
        "us-west1",
        "a b c",
        "tab\tseparated",
        "!#$%&'*+-.^_`|~",
        "",
        "~",
        "!",
    ])
    def test_safe_values(self, value):
        """Tab, space and printable ASCII 0x21-0x7E are safe."""
        assert is_safe_plain_ascii(value) is True

    @pytest.mark.parametrize("value,why", [
        ("Hello, 世界", "non-ascii"),
        ("café", "non-ascii"),
        ("line1\nline2", "control char LF"),
        ("a\rb", "control char CR"),
        ("a\x00b", "control char NUL"),
        ("a\x7fb", "DEL is not printable"),
        (" padded ", "leading and trailing whitespace"),
        (" leading", "leading whitespace"),
        ("trailing ", "trailing whitespace"),
        ("\tleading tab", "leading tab"),
        ("=?base64?literal?=", "collides with the sentinel"),
    ])
    def test_unsafe_values_are_encoded(self, value, why):
        """Anything not safe plain ASCII gets the sentinel."""
        assert is_safe_plain_ascii(value) is False, why
        encoded = encode_header_value(value)
        assert encoded.startswith(SENTINEL_PREFIX)
        assert encoded.endswith(SENTINEL_SUFFIX)
        assert decode_header_value(encoded) == value

    def test_sentinel_markers_are_case_sensitive(self):
        """Uppercase markers are not a sentinel and decode to themselves."""
        assert looks_like_sentinel("=?BASE64?dXMtd2VzdDE=?=") is False
        assert decode_header_value("=?BASE64?dXMtd2VzdDE=?=") == "=?BASE64?dXMtd2VzdDE=?="
        assert looks_like_sentinel("=?Base64?dXMtd2VzdDE=?=") is False

    def test_markers_may_not_overlap(self):
        """A string too short to hold both markers is not a sentinel."""
        assert looks_like_sentinel("=?base64?=") is False
        assert decode_header_value("=?base64?=") == "=?base64?="

    def test_partial_markers_are_not_sentinels(self):
        """Both markers must be present."""
        assert looks_like_sentinel("=?base64?dXMtd2VzdDE=") is False
        assert looks_like_sentinel("dXMtd2VzdDE=?=") is False

    def test_plain_value_decodes_to_itself(self):
        """Values without a sentinel pass through untouched."""
        assert decode_header_value("us-west1") == "us-west1"

    def test_empty_payload_decodes_to_empty_string(self):
        """An empty base64 payload is legal and yields the empty string."""
        assert decode_header_value("=?base64??=") == ""

    @pytest.mark.parametrize("malformed", [
        "=?base64?not valid base64!?=",
        "=?base64?A?=",
        "=?base64?////?=",
    ])
    def test_malformed_sentinel_degrades_to_raw_value(self, malformed):
        """A bad payload is returned verbatim rather than raising."""
        assert decode_header_value(malformed) == malformed

    def test_non_string_is_not_a_sentinel(self):
        """Defensive: non-string input never looks like a sentinel."""
        assert looks_like_sentinel(None) is False
        assert looks_like_sentinel(42) is False
        assert is_safe_plain_ascii(42) is False


# ---------------------------------------------------------------------------
# (B) Header / body validation
# ---------------------------------------------------------------------------

def _body(method="tools/call", name="execute_query", arguments=None, protocol_version=None):
    """Build a JSON-RPC request body for validation tests."""
    params = {}
    if name is not None:
        params["name"] = name
    if arguments is not None:
        params["arguments"] = arguments
    if protocol_version is not None:
        params["_meta"] = {META_PROTOCOL_VERSION: protocol_version}
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}


class TestStrictOptIn:
    """Strict validation is opt-in and off by default."""

    def test_default_is_off_and_is_a_no_op(self):
        """With no flag, even a blatant mismatch produces no errors."""
        errors = validate_request_headers(
            {"Mcp-Method": "resources/read"},
            _body(method="tools/call"),
        )
        assert errors == []

    def test_legacy_client_sending_no_headers_is_accepted(self):
        """A 2025-11-25 client sends no Mcp-* headers at all."""
        assert validate_request_headers({}, _body()) == []

    def test_strict_flag_enables_enforcement(self):
        """The same mismatch is reported once strict=True is passed."""
        errors = validate_request_headers(
            {"Mcp-Method": "resources/read", "Mcp-Name": "execute_query"},
            _body(method="tools/call"),
            strict=True,
        )
        assert _reasons(errors) == ["mismatch"]
        assert errors[0].header == HEADER_MCP_METHOD

    def test_env_helper_defaults_off(self, monkeypatch):
        """PROMETHEUS_MCP_STRICT_HEADERS defaults to off."""
        monkeypatch.delenv("PROMETHEUS_MCP_STRICT_HEADERS", raising=False)
        assert strict_headers_enabled() is False

    @pytest.mark.parametrize("raw,expected", [
        ("true", True),
        ("True", True),
        ("1", True),
        ("yes", True),
        ("false", False),
        ("no", False),
        ("", False),
    ])
    def test_env_helper_parses_switch(self, monkeypatch, raw, expected):
        """The env switch follows the repo's boolean parsing convention."""
        monkeypatch.setenv("PROMETHEUS_MCP_STRICT_HEADERS", raw)
        assert strict_headers_enabled() is expected

    def test_non_mapping_body_is_ignored(self):
        """Defensive: a body that is not a mapping yields no errors."""
        assert validate_request_headers({"Mcp-Method": "x"}, None, strict=True) == []

    @pytest.mark.parametrize("headers", [None, {}])
    def test_absent_headers_mapping_is_tolerated(self, headers):
        """No Mcp-* headers at all is the legacy case and must be accepted.

        Regression: treating an absent header as a mismatch made every Mcp-*
        header mandatory, which rejected every shipping MCP client -- including
        the initialize handshake -- the moment strict mode was switched on.
        """
        assert validate_request_headers(headers, _body(), strict=True) == []

    def test_initialize_without_mcp_headers_is_accepted(self):
        """Regression: strict mode must never lock out the handshake."""
        body = {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}},
        }
        headers = {
            "accept": "application/json, text/event-stream",
            "content-type": "application/json",
        }
        assert validate_request_headers(headers, body, strict=True) == []

    def test_transport_protocol_version_header_alone_is_accepted(self):
        """MCP-Protocol-Version is a mandated transport header, not a body mirror.

        Regression: a 2025-11-25 client sends it on every post-initialize
        request and puts nothing in _meta, which was reported as
        ``unexpected_header`` and rejected the request with -32020.
        """
        headers = {
            "mcp-protocol-version": "2025-11-25",
            "mcp-session-id": "abc",
            "content-type": "application/json",
        }
        body = {"method": "tools/list", "params": {}}
        assert validate_request_headers(headers, body, strict=True) == []

    def test_a_2026_client_may_declare_2026_while_the_transport_says_2025(self):
        """The SDK transport rejects MCP-Protocol-Version: 2026-07-28 outright.

        A 2026-07-28 client therefore has to send the transport-negotiated
        revision in the header while declaring 2026-07-28 in _meta. Comparing
        the two would make strict mode unsatisfiable for every client alive.
        """
        headers = {"mcp-protocol-version": "2025-11-25", "mcp-method": "tools/list"}
        body = {
            "method": "tools/list",
            "params": {"_meta": {META_PROTOCOL_VERSION: "2026-07-28"}},
        }
        assert validate_request_headers(headers, body, strict=True) == []


class TestNumericCoercionGuards:
    """Guards inside the numeric comparison helper."""

    def test_bool_is_never_treated_as_a_number(self):
        """bool subclasses int, so it must be rejected before float()."""
        from prometheus_mcp_server.spec2026.headers import _parse_number

        assert _parse_number(True) is None
        assert _parse_number(False) is None
        assert _parse_number(1) == 1.0

    def test_non_scalar_body_value_falls_back_to_string_compare(self):
        """A list argument cannot be parsed numerically and must not raise."""
        annotation = HeaderAnnotation(token="Limit", path=("limit",), type_name="integer")
        errors = validate_request_headers(
            {
                "Mcp-Method": "tools/call",
                "Mcp-Name": "execute_query",
                "Mcp-Param-Limit": "42",
            },
            _body(arguments={"limit": [42]}),
            strict=True,
            header_annotations=[annotation],
        )
        assert _reasons(errors) == ["mismatch"]


class TestMcpMethodHeader:
    """Mcp-Method vs the body method."""

    def test_matching_method_passes(self):
        errors = validate_request_headers(
            {"Mcp-Method": "tools/call", "Mcp-Name": "execute_query"},
            _body(),
            strict=True,
        )
        assert errors == []

    def test_mismatched_method_is_rejected(self):
        errors = validate_request_headers(
            {"Mcp-Method": "tools/list", "Mcp-Name": "execute_query"},
            _body(method="tools/call"),
            strict=True,
        )
        assert _reasons(errors) == ["mismatch"]
        assert errors[0].header_value == "tools/list"
        assert errors[0].body_value == "tools/call"

    def test_missing_header_when_value_in_body_is_accepted(self):
        """An omitted Mcp-Method is legal: the headers are optional hints.

        Regression: this used to be reported as ``missing_header``, which made
        Mcp-Method mandatory on every request (every JSON-RPC body has a
        method) and locked out every client that does not send it.
        """
        assert validate_request_headers({"Mcp-Name": "execute_query"}, _body(), strict=True) == []

    def test_encoded_header_value_is_decoded_before_comparison(self):
        """The server decodes the sentinel before comparing."""
        errors = validate_request_headers(
            {
                "Mcp-Method": encode_header_value("tools/call"),
                "Mcp-Name": encode_header_value("Hello, 世界"),
            },
            _body(name="Hello, 世界"),
            strict=True,
        )
        assert errors == []


class TestHeaderNameAndValueCasing:
    """Header names are case-insensitive; values are case-sensitive."""

    @pytest.mark.parametrize("header_name", [
        "Mcp-Method",
        "mcp-method",
        "MCP-METHOD",
        "mCp-MeThOd",
    ])
    def test_header_names_compare_case_insensitively(self, header_name):
        errors = validate_request_headers(
            {header_name: "tools/call", "mcp-name": "execute_query"},
            _body(),
            strict=True,
        )
        assert errors == []

    def test_header_values_compare_case_sensitively(self):
        """A value differing only in case is a mismatch."""
        errors = validate_request_headers(
            {"Mcp-Method": "Tools/Call", "Mcp-Name": "execute_query"},
            _body(method="tools/call"),
            strict=True,
        )
        assert _reasons(errors) == ["mismatch"]

    def test_name_value_case_matters(self):
        errors = validate_request_headers(
            {"Mcp-Method": "tools/call", "Mcp-Name": "Execute_Query"},
            _body(name="execute_query"),
            strict=True,
        )
        assert _reasons(errors) == ["mismatch"]
        assert errors[0].header == HEADER_MCP_NAME


class TestMcpNameHeader:
    """Mcp-Name vs params.name or params.uri."""

    def test_matches_params_name(self):
        errors = validate_request_headers(
            {"Mcp-Method": "tools/call", "Mcp-Name": "execute_query"},
            _body(),
            strict=True,
        )
        assert errors == []

    def test_matches_params_uri_when_name_absent(self):
        """resources/read carries a uri rather than a name."""
        body = {
            "method": "resources/read",
            "params": {"uri": "prometheus://metrics"},
        }
        errors = validate_request_headers(
            {"Mcp-Method": "resources/read", "Mcp-Name": "prometheus://metrics"},
            body,
            strict=True,
        )
        assert errors == []

    def test_uri_mismatch_is_rejected(self):
        body = {"method": "resources/read", "params": {"uri": "prometheus://metrics"}}
        errors = validate_request_headers(
            {"Mcp-Method": "resources/read", "Mcp-Name": "prometheus://targets"},
            body,
            strict=True,
        )
        assert _reasons(errors) == ["mismatch"]

    def test_absent_name_and_uri_requires_omitted_header(self):
        """tools/list has neither; omitting the header is not an error."""
        body = {"method": "tools/list", "params": {}}
        errors = validate_request_headers(
            {"Mcp-Method": "tools/list"},
            body,
            strict=True,
        )
        assert errors == []

    def test_header_sent_without_body_value_is_rejected(self):
        """The header must be omitted when the body carries no value."""
        body = {"method": "tools/list", "params": {}}
        errors = validate_request_headers(
            {"Mcp-Method": "tools/list", "Mcp-Name": "execute_query"},
            body,
            strict=True,
        )
        assert _reasons(errors) == ["unexpected_header"]
        assert errors[0].header == HEADER_MCP_NAME

    def test_null_name_falls_back_to_uri(self):
        body = {
            "method": "resources/read",
            "params": {"name": None, "uri": "prometheus://metrics"},
        }
        errors = validate_request_headers(
            {"Mcp-Method": "resources/read", "Mcp-Name": "prometheus://metrics"},
            body,
            strict=True,
        )
        assert errors == []

    def test_missing_params_entirely_is_tolerated(self):
        body = {"method": "ping"}
        errors = validate_request_headers({"Mcp-Method": "ping"}, body, strict=True)
        assert errors == []


class TestProtocolVersionHeader:
    """MCP-Protocol-Version vs the _meta protocol version."""

    def test_matching_version_passes(self):
        errors = validate_request_headers(
            {
                "Mcp-Method": "tools/call",
                "Mcp-Name": "execute_query",
                "MCP-Protocol-Version": "2026-07-28",
            },
            _body(protocol_version="2026-07-28"),
            strict=True,
        )
        assert errors == []

    def test_mismatched_version_is_rejected(self):
        """A declared revision the transport *can* carry is still compared."""
        errors = validate_request_headers(
            {
                "Mcp-Method": "tools/call",
                "Mcp-Name": "execute_query",
                "MCP-Protocol-Version": "2025-06-18",
            },
            _body(protocol_version="2025-11-25"),
            strict=True,
        )
        assert _reasons(errors) == ["mismatch"]
        assert errors[0].header == HEADER_MCP_PROTOCOL_VERSION

    def test_absent_version_in_body_needs_no_header(self):
        """Legacy requests carry no _meta protocolVersion; that is fine."""
        errors = validate_request_headers(
            {"Mcp-Method": "tools/call", "Mcp-Name": "execute_query"},
            _body(),
            strict=True,
        )
        assert errors == []

    def test_top_level_meta_is_also_accepted(self):
        """Defensive: _meta at the envelope root is read as a fallback."""
        body = {
            "method": "tools/list",
            "params": {},
            "_meta": {META_PROTOCOL_VERSION: "2026-07-28"},
        }
        errors = validate_request_headers(
            {"Mcp-Method": "tools/list", "MCP-Protocol-Version": "2026-07-28"},
            body,
            strict=True,
        )
        assert errors == []

    def test_missing_header_when_version_in_body_is_accepted(self):
        """Not sending the transport header is never this validator's business."""
        errors = validate_request_headers(
            {"Mcp-Method": "tools/call", "Mcp-Name": "execute_query"},
            _body(protocol_version="2025-11-25"),
            strict=True,
        )
        assert errors == []


class TestParamHeaderValidation:
    """Mcp-Param-{Name} vs the annotated argument value."""

    REGION = HeaderAnnotation(token="Region", path=("region",), type_name="string")
    LIMIT = HeaderAnnotation(token="Limit", path=("limit",), type_name="integer")
    VERBOSE = HeaderAnnotation(token="Verbose", path=("verbose",), type_name="boolean")

    def test_header_name_is_prefixed(self):
        assert self.REGION.header_name == f"{PARAM_HEADER_PREFIX}Region"
        assert self.REGION.header_name == "Mcp-Param-Region"

    def test_matching_string_param_passes(self):
        errors = validate_request_headers(
            {
                "Mcp-Method": "tools/call",
                "Mcp-Name": "execute_query",
                "Mcp-Param-Region": "us-west1",
            },
            _body(arguments={"region": "us-west1"}),
            strict=True,
            header_annotations=[self.REGION],
        )
        assert errors == []

    def test_mismatched_string_param_is_rejected(self):
        errors = validate_request_headers(
            {
                "Mcp-Method": "tools/call",
                "Mcp-Name": "execute_query",
                "Mcp-Param-Region": "eu-central1",
            },
            _body(arguments={"region": "us-west1"}),
            strict=True,
            header_annotations=[self.REGION],
        )
        assert _reasons(errors) == ["mismatch"]
        assert errors[0].header == "Mcp-Param-Region"

    @pytest.mark.parametrize("header_value,body_value", [
        ("42", 42),
        ("42.0", 42),
        ("42", 42.0),
        ("42.0", 42.0),
        ("42.00", 42),
        ("-7", -7),
        ("0", 0),
    ])
    def test_integer_params_compare_numerically(self, header_value, body_value):
        """42.0 == 42: integers compare as numbers, not as strings."""
        errors = validate_request_headers(
            {
                "Mcp-Method": "tools/call",
                "Mcp-Name": "execute_query",
                "Mcp-Param-Limit": header_value,
            },
            _body(arguments={"limit": body_value}),
            strict=True,
            header_annotations=[self.LIMIT],
        )
        assert errors == [], f"{header_value!r} should equal {body_value!r} numerically"

    @pytest.mark.parametrize("header_value,body_value", [
        ("43", 42),
        ("42.5", 42),
        ("4 2", 42),
        ("", 42),
        ("forty-two", 42),
    ])
    def test_numerically_different_values_are_rejected(self, header_value, body_value):
        errors = validate_request_headers(
            {
                "Mcp-Method": "tools/call",
                "Mcp-Name": "execute_query",
                "Mcp-Param-Limit": header_value,
            },
            _body(arguments={"limit": body_value}),
            strict=True,
            header_annotations=[self.LIMIT],
        )
        assert _reasons(errors) == ["mismatch"]

    @pytest.mark.parametrize("body_value,header_value", [
        (True, "true"),
        (False, "false"),
    ])
    def test_boolean_params_use_json_casing(self, body_value, header_value):
        errors = validate_request_headers(
            {
                "Mcp-Method": "tools/call",
                "Mcp-Name": "execute_query",
                "Mcp-Param-Verbose": header_value,
            },
            _body(arguments={"verbose": body_value}),
            strict=True,
            header_annotations=[self.VERBOSE],
        )
        assert errors == []

    def test_boolean_header_casing_is_significant(self):
        """Header values are case-sensitive, so 'True' does not match true."""
        errors = validate_request_headers(
            {
                "Mcp-Method": "tools/call",
                "Mcp-Name": "execute_query",
                "Mcp-Param-Verbose": "True",
            },
            _body(arguments={"verbose": True}),
            strict=True,
            header_annotations=[self.VERBOSE],
        )
        assert _reasons(errors) == ["mismatch"]

    def test_boolean_true_does_not_match_numeric_one(self):
        """bool must not be coerced into the numeric comparison path."""
        errors = validate_request_headers(
            {
                "Mcp-Method": "tools/call",
                "Mcp-Name": "execute_query",
                "Mcp-Param-Verbose": "1",
            },
            _body(arguments={"verbose": True}),
            strict=True,
            header_annotations=[self.VERBOSE],
        )
        assert _reasons(errors) == ["mismatch"]

    def test_missing_param_header_is_accepted_when_argument_present(self):
        """Regression: a client passing an annotated argument as a plain
        argument must not be forced to mirror it into a header as well."""
        errors = validate_request_headers(
            {"Mcp-Method": "tools/call", "Mcp-Name": "execute_query"},
            _body(arguments={"region": "us-west1"}),
            strict=True,
            header_annotations=[self.REGION],
        )
        assert errors == []

    @pytest.mark.parametrize("arguments", [
        {},
        {"region": None},
        {"other": "value"},
        None,
    ])
    def test_absent_or_null_argument_needs_no_header(self, arguments):
        """Absent/null in the body => header omitted, and that is NOT an error."""
        errors = validate_request_headers(
            {"Mcp-Method": "tools/call", "Mcp-Name": "execute_query"},
            _body(arguments=arguments),
            strict=True,
            header_annotations=[self.REGION],
        )
        assert errors == []

    def test_param_header_sent_without_argument_is_rejected(self):
        errors = validate_request_headers(
            {
                "Mcp-Method": "tools/call",
                "Mcp-Name": "execute_query",
                "Mcp-Param-Region": "us-west1",
            },
            _body(arguments={}),
            strict=True,
            header_annotations=[self.REGION],
        )
        assert _reasons(errors) == ["unexpected_header"]

    def test_nested_argument_path_is_honoured(self):
        nested = HeaderAnnotation(
            token="Region", path=("filters", "region"), type_name="string",
        )
        errors = validate_request_headers(
            {
                "Mcp-Method": "tools/call",
                "Mcp-Name": "execute_query",
                "Mcp-Param-Region": "us-west1",
            },
            _body(arguments={"filters": {"region": "us-west1"}}),
            strict=True,
            header_annotations=[nested],
        )
        assert errors == []

    def test_encoded_param_value_is_decoded(self):
        errors = validate_request_headers(
            {
                "Mcp-Method": "tools/call",
                "Mcp-Name": "execute_query",
                "Mcp-Param-Region": encode_header_value(" padded "),
            },
            _body(arguments={"region": " padded "}),
            strict=True,
            header_annotations=[self.REGION],
        )
        assert errors == []

    def test_multiple_mismatches_are_all_reported(self):
        errors = validate_request_headers(
            {
                "Mcp-Method": "tools/list",
                "Mcp-Name": "wrong_tool",
                "Mcp-Param-Region": "eu-central1",
            },
            _body(arguments={"region": "us-west1"}),
            strict=True,
            header_annotations=[self.REGION],
        )
        assert len(errors) == 3
        assert set(_reasons(errors)) == {"mismatch"}


class TestHeaderMismatchError:
    """The JSON-RPC error object rendered from findings."""

    def test_uses_code_minus_32020(self):
        errors = validate_request_headers(
            {"Mcp-Method": "tools/list", "Mcp-Name": "execute_query"},
            _body(method="tools/call"),
            strict=True,
        )
        payload = header_mismatch_error(errors)
        assert payload["code"] == -32020
        assert payload["code"] == HEADER_MISMATCH
        assert "HeaderMismatch" in payload["message"]

    def test_data_describes_each_mismatch(self):
        errors = validate_request_headers(
            {"Mcp-Method": "tools/list", "Mcp-Name": "execute_query"},
            _body(method="tools/call"),
            strict=True,
        )
        mismatches = header_mismatch_error(errors)["data"]["mismatches"]
        assert len(mismatches) == 1
        assert mismatches[0]["header"] == HEADER_MCP_METHOD
        assert mismatches[0]["headerValue"] == "tools/list"
        assert mismatches[0]["bodyValue"] == "tools/call"
        assert mismatches[0]["reason"] == "mismatch"

    def test_json_serialisable(self):
        import json

        errors = validate_request_headers(
            {"Mcp-Method": "tools/list"},
            _body(method="tools/call", name=None),
            strict=True,
        )
        assert json.loads(json.dumps(header_mismatch_error(errors)))["code"] == -32020


# ---------------------------------------------------------------------------
# (C) x-mcp-header inputSchema validation
# ---------------------------------------------------------------------------

def _schema(properties, **extra):
    """Build an object schema with the given properties."""
    schema = {"type": "object", "properties": properties}
    schema.update(extra)
    return schema


class TestValidAnnotations:
    """Schemas that satisfy every constraint."""

    def test_primitive_types_are_accepted(self):
        schema = _schema({
            "region": {"type": "string", ANNOTATION_KEY: "Region"},
            "limit": {"type": "integer", ANNOTATION_KEY: "Limit"},
            "verbose": {"type": "boolean", ANNOTATION_KEY: "Verbose"},
        })
        assert validate_header_annotations(schema) == []
        annotations = collect_header_annotations(schema)
        assert [a.token for a in annotations] == ["Region", "Limit", "Verbose"]
        assert [a.type_name for a in annotations] == ["string", "integer", "boolean"]

    def test_nested_properties_chain_is_reachable(self):
        schema = _schema({
            "filters": _schema({"region": {"type": "string", ANNOTATION_KEY: "Region"}}),
        })
        assert validate_header_annotations(schema) == []
        annotation = collect_header_annotations(schema)[0]
        assert annotation.path == ("filters", "region")
        assert annotation.header_name == "Mcp-Param-Region"

    def test_unannotated_schema_yields_nothing(self):
        schema = _schema({"query": {"type": "string"}})
        assert validate_header_annotations(schema) == []
        assert collect_header_annotations(schema) == []

    @pytest.mark.parametrize("token", [
        "Region", "region", "X-Trace", "a", "0", "abc123",
        "!#$%&'*+-.^_`|~",
    ])
    def test_tchar_tokens_are_accepted(self, token):
        schema = _schema({"p": {"type": "string", ANNOTATION_KEY: token}})
        assert validate_header_annotations(schema) == []

    def test_property_named_items_is_still_reachable(self):
        """A property literally named 'items' is not the 'items' keyword."""
        schema = _schema({"items": {"type": "string", ANNOTATION_KEY: "Items"}})
        assert validate_header_annotations(schema) == []
        assert collect_header_annotations(schema)[0].path == ("items",)

    @pytest.mark.parametrize("bad", [None, "not a schema", 42, []])
    def test_non_dict_schema_is_tolerated(self, bad):
        """Defensive: a malformed inputSchema must not raise."""
        assert validate_header_annotations(bad) == []
        assert collect_header_annotations(bad) == []


class TestAnnotationRejections:
    """At least one rejection case per documented constraint."""

    def test_empty_token_is_rejected(self):
        schema = _schema({"region": {"type": "string", ANNOTATION_KEY: ""}})
        assert _codes(validate_header_annotations(schema)) == ["empty"]
        assert collect_header_annotations(schema) == []

    def test_non_string_token_is_rejected(self):
        schema = _schema({"region": {"type": "string", ANNOTATION_KEY: 42}})
        assert _codes(validate_header_annotations(schema)) == ["not_a_string"]

    @pytest.mark.parametrize("token", ["Reg\rion", "Reg\nion", "Region\r\n"])
    def test_crlf_in_token_is_rejected(self, token):
        """Header injection via CR/LF must be refused."""
        schema = _schema({"region": {"type": "string", ANNOTATION_KEY: token}})
        assert _codes(validate_header_annotations(schema)) == ["crlf"]

    @pytest.mark.parametrize("token", [
        "has space",
        "colon:name",
        "quote\"name",
        "paren(name)",
        "comma,name",
        "slash/name",
        "at@name",
        "semi;name",
        "brace{name}",
        "back\\slash",
        "square[name]",
        "equals=name",
        "question?name",
        "less<name>",
        "Région",
        "\ttab",
    ])
    def test_non_tchar_token_is_rejected(self, token):
        """RFC 9110 tchar syntax excludes separators and non-ASCII."""
        schema = _schema({"region": {"type": "string", ANNOTATION_KEY: token}})
        assert _codes(validate_header_annotations(schema)) == ["invalid_token"]

    def test_case_insensitive_duplicates_are_rejected(self):
        schema = _schema({
            "region": {"type": "string", ANNOTATION_KEY: "Region"},
            "region2": {"type": "string", ANNOTATION_KEY: "region"},
        })
        errors = validate_header_annotations(schema)
        assert _codes(errors) == ["duplicate"]
        assert errors[0].path == ("region2",)
        # Only the first occurrence survives.
        assert [a.path for a in collect_header_annotations(schema)] == [("region",)]

    def test_exact_duplicates_are_rejected(self):
        schema = _schema({
            "a": {"type": "string", ANNOTATION_KEY: "Region"},
            "b": {"type": "string", ANNOTATION_KEY: "Region"},
        })
        assert _codes(validate_header_annotations(schema)) == ["duplicate"]

    def test_duplicate_across_nesting_levels_is_rejected(self):
        schema = _schema({
            "region": {"type": "string", ANNOTATION_KEY: "Region"},
            "filters": _schema({"region": {"type": "string", ANNOTATION_KEY: "REGION"}}),
        })
        assert _codes(validate_header_annotations(schema)) == ["duplicate"]

    def test_number_type_is_not_permitted(self):
        """'number' is explicitly excluded even though it is primitive."""
        schema = _schema({"ratio": {"type": "number", ANNOTATION_KEY: "Ratio"}})
        errors = validate_header_annotations(schema)
        assert _codes(errors) == ["invalid_type"]
        assert "number" in errors[0].message
        assert collect_header_annotations(schema) == []

    @pytest.mark.parametrize("type_value", [
        "array",
        "object",
        "null",
        None,
        ["string", "null"],
        "STRING",
    ])
    def test_non_primitive_types_are_rejected(self, type_value):
        prop = {ANNOTATION_KEY: "Region"}
        if type_value is not None:
            prop["type"] = type_value
        schema = _schema({"region": prop})
        assert _codes(validate_header_annotations(schema)) == ["invalid_type"]

    def test_annotation_inside_items_is_unreachable(self):
        schema = _schema({
            "regions": {
                "type": "array",
                "items": {"type": "string", ANNOTATION_KEY: "Region"},
            },
        })
        assert _codes(validate_header_annotations(schema)) == ["unreachable"]
        assert collect_header_annotations(schema) == []

    @pytest.mark.parametrize("keyword", ["oneOf", "anyOf", "allOf"])
    def test_annotation_inside_combinators_is_unreachable(self, keyword):
        schema = _schema({}, **{
            keyword: [_schema({"region": {"type": "string", ANNOTATION_KEY: "Region"}})],
        })
        assert "unreachable" in _codes(validate_header_annotations(schema))
        assert collect_header_annotations(schema) == []

    def test_annotation_inside_not_is_unreachable(self):
        schema = _schema({}, **{
            "not": _schema({"region": {"type": "string", ANNOTATION_KEY: "Region"}}),
        })
        assert "unreachable" in _codes(validate_header_annotations(schema))

    @pytest.mark.parametrize("keyword", ["if", "then", "else"])
    def test_annotation_inside_conditionals_is_unreachable(self, keyword):
        schema = _schema({}, **{
            keyword: _schema({"region": {"type": "string", ANNOTATION_KEY: "Region"}}),
        })
        assert "unreachable" in _codes(validate_header_annotations(schema))

    def test_annotation_behind_ref_is_unreachable(self):
        """$ref is never followed, so $defs content is not reachable."""
        schema = _schema(
            {"filters": {"$ref": "#/$defs/Filters"}},
            **{"$defs": {
                "Filters": _schema({"region": {"type": "string", ANNOTATION_KEY: "Region"}}),
            }},
        )
        assert "unreachable" in _codes(validate_header_annotations(schema))
        assert collect_header_annotations(schema) == []

    def test_annotation_on_schema_root_is_rejected(self):
        schema = _schema({"region": {"type": "string"}}, **{ANNOTATION_KEY: "Region"})
        errors = validate_header_annotations(schema)
        assert _codes(errors) == ["not_a_property"]
        assert errors[0].path == ()

    def test_error_is_serialisable(self):
        schema = _schema({"region": {"type": "number", ANNOTATION_KEY: "Region"}})
        payload = validate_header_annotations(schema)[0].to_dict()
        assert payload["code"] == "invalid_type"
        assert payload["path"] == ["region"]
        assert payload["token"] == "Region"

    def test_valid_and_invalid_annotations_coexist(self):
        """A broken annotation does not discard the good ones."""
        schema = _schema({
            "region": {"type": "string", ANNOTATION_KEY: "Region"},
            "ratio": {"type": "number", ANNOTATION_KEY: "Ratio"},
        })
        assert _codes(validate_header_annotations(schema)) == ["invalid_type"]
        assert [a.token for a in collect_header_annotations(schema)] == ["Region"]

    def test_self_referential_schema_terminates(self):
        """Defensive: a cyclic Python dict must not recurse forever."""
        schema = _schema({"region": {"type": "string", ANNOTATION_KEY: "Region"}})
        schema["properties"]["self"] = schema
        assert validate_header_annotations(schema) == []
        assert [a.token for a in collect_header_annotations(schema)] == ["Region"]

    def test_shared_list_node_is_visited_once(self):
        """Defensive: the same list object reached twice must not loop."""
        shared = [_schema({"region": {"type": "string", ANNOTATION_KEY: "Region"}})]
        schema = _schema({}, oneOf=shared, anyOf=shared)
        assert _codes(validate_header_annotations(schema)) == ["unreachable"]

    @pytest.mark.parametrize("properties", [
        {"region": "not a schema"},
        {"region": None},
        {"region": ["array"]},
        {1: {"type": "string", ANNOTATION_KEY: "Region"}},
    ])
    def test_malformed_property_entries_are_skipped(self, properties):
        """Defensive: junk inside 'properties' must not raise."""
        assert collect_header_annotations(_schema(properties)) == []


class TestExtractParamHeaders:
    """Reading annotated values out of tool arguments."""

    def test_reads_value_at_exact_path(self):
        schema = _schema({
            "filters": _schema({"region": {"type": "string", ANNOTATION_KEY: "Region"}}),
        })
        annotations = collect_header_annotations(schema)
        headers = extract_param_headers(annotations, {"filters": {"region": "us-west1"}})
        assert headers == {"Mcp-Param-Region": "us-west1"}

    def test_encodes_unsafe_values(self):
        schema = _schema({"region": {"type": "string", ANNOTATION_KEY: "Region"}})
        annotations = collect_header_annotations(schema)
        headers = extract_param_headers(annotations, {"region": "Hello, 世界"})
        assert headers == {"Mcp-Param-Region": "=?base64?SGVsbG8sIOS4lueVjA==?="}

    @pytest.mark.parametrize("arguments", [
        {},
        {"region": None},
        {"filters": {}},
        None,
        "not a mapping",
    ])
    def test_omits_header_when_no_value_present(self, arguments):
        schema = _schema({"region": {"type": "string", ANNOTATION_KEY: "Region"}})
        annotations = collect_header_annotations(schema)
        assert extract_param_headers(annotations, arguments) == {}

    def test_omits_header_when_nested_path_is_absent(self):
        schema = _schema({
            "filters": _schema({"region": {"type": "string", ANNOTATION_KEY: "Region"}}),
        })
        annotations = collect_header_annotations(schema)
        assert extract_param_headers(annotations, {"filters": {"other": 1}}) == {}
        assert extract_param_headers(annotations, {"filters": "scalar"}) == {}

    @pytest.mark.parametrize("value,expected", [
        (42, "42"),
        (42.0, "42"),
        (True, "true"),
        (False, "false"),
        (0, "0"),
    ])
    def test_formats_primitives_json_style(self, value, expected):
        annotation = HeaderAnnotation(token="P", path=("p",), type_name="integer")
        assert extract_param_headers([annotation], {"p": value}) == {"Mcp-Param-P": expected}

    def test_extracted_headers_validate_round_trip(self):
        """Headers built by the extractor pass strict validation."""
        schema = _schema({
            "region": {"type": "string", ANNOTATION_KEY: "Region"},
            "limit": {"type": "integer", ANNOTATION_KEY: "Limit"},
            "verbose": {"type": "boolean", ANNOTATION_KEY: "Verbose"},
        })
        annotations = collect_header_annotations(schema)
        arguments = {"region": " padded ", "limit": 42, "verbose": False}

        headers = {"Mcp-Method": "tools/call", "Mcp-Name": "execute_query"}
        headers.update(extract_param_headers(annotations, arguments))

        errors = validate_request_headers(
            headers,
            _body(arguments=arguments),
            strict=True,
            header_annotations=annotations,
        )
        assert errors == []

    def test_empty_annotation_list_yields_no_headers(self):
        assert extract_param_headers([], {"region": "us-west1"}) == {}


# ---------------------------------------------------------------------------
# Regressions found by adversarial review
# ---------------------------------------------------------------------------

class TestNumericComparisonIsJsonOnly:
    """Numeric header values must parse as JSON numbers, not as Python floats."""

    @pytest.mark.parametrize("header_value", [
        "4_2",       # PEP 515 underscore separator: float("4_2") == 42.0
        "４２",       # fullwidth digits: float() accepts Unicode decimals
        " 42",
        "42 ",
        "0x2a",
        "",
        "+42",
        "042",
        "inf",
        "nan",
    ])
    def test_non_json_numbers_never_match_an_integer_body_value(self, header_value):
        """Regression: float() accepted values no JSON client could have sent."""
        from prometheus_mcp_server.spec2026.headers import _values_match

        assert _values_match(header_value, 42, "integer") is False

    @pytest.mark.parametrize("header_value", ["42", "42.0", "4.2e1", "-42"])
    def test_legal_json_numbers_still_compare_numerically(self, header_value):
        """Integer params compare numerically, so 42.0 still equals 42."""
        from prometheus_mcp_server.spec2026.headers import _values_match

        expected = -42 if header_value.startswith("-") else 42
        assert _values_match(header_value, expected, "integer") is True

    def test_number_is_not_a_permitted_annotation_type(self):
        """`number` must not sneak into the numeric branch as a declared type."""
        from prometheus_mcp_server.spec2026.headers import ALLOWED_ANNOTATION_TYPES

        assert "number" not in ALLOWED_ANNOTATION_TYPES


class TestResourceUriComparison:
    """Mcp-Name for resources/read compares URIs, not raw strings."""

    @pytest.mark.parametrize("header_uri,body_uri", [
        ("http://example.com", "http://example.com/"),
        ("http://example.com/", "http://example.com"),
        ("HTTP://Example.COM/metrics", "http://example.com/metrics"),
        ("prometheus://metrics", "prometheus://metrics"),
    ])
    def test_equivalent_uris_are_not_a_mismatch(self, header_uri, body_uri):
        """Regression: a URI round-tripped through a parser gains a trailing
        slash and a lowercased host, which was reported as a HeaderMismatch."""
        errors = validate_request_headers(
            {"Mcp-Method": "resources/read", "Mcp-Name": header_uri},
            {"method": "resources/read", "params": {"uri": body_uri}},
            strict=True,
        )
        assert errors == []

    def test_a_genuinely_different_uri_is_still_rejected(self):
        errors = validate_request_headers(
            {"Mcp-Method": "resources/read", "Mcp-Name": "http://example.com/a"},
            {"method": "resources/read", "params": {"uri": "http://example.com/b"}},
            strict=True,
        )
        assert _reasons(errors) == ["mismatch"]


class TestSdkClientHeaderSets:
    """The exact header sets shipping MCP clients send must pass strict mode."""

    @pytest.mark.parametrize("method", ["initialize", "tools/list", "tools/call", "ping"])
    def test_official_sdk_streamable_http_headers_are_accepted(self, method):
        """Regression: strict mode rejected every request from every client."""
        headers = {
            "accept": "application/json, text/event-stream",
            "content-type": "application/json",
            "mcp-session-id": "3f9c1c0e",
            "mcp-protocol-version": "2025-11-25",
        }
        params = {"name": "health_check", "arguments": {}} if method == "tools/call" else {}
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}

        assert validate_request_headers(headers, body, strict=True) == []


class TestUriNormalisationGuards:
    """Defensive branches of the URI comparison helper."""

    @pytest.mark.parametrize("value", [None, 42, ["a"]])
    def test_non_strings_pass_through(self, value):
        from prometheus_mcp_server.spec2026.headers import _normalize_uri

        assert _normalize_uri(value) is value

    def test_a_relative_reference_is_left_alone(self):
        """No scheme means there is nothing to normalise."""
        from prometheus_mcp_server.spec2026.headers import _normalize_uri

        assert _normalize_uri("metrics/up") == "metrics/up"

    def test_an_unparseable_uri_is_left_alone(self):
        """urlsplit raises on a malformed IPv6 literal; that must not escape."""
        from prometheus_mcp_server.spec2026.headers import _normalize_uri

        assert _normalize_uri("http://[::1") == "http://[::1"
