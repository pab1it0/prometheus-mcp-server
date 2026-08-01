"""Tests for MCP 2026-07-28 OpenTelemetry trace-context propagation (F7).

Covers:
- W3C traceparent validation, including the all-zero trace-id/parent-id and the
  reserved ``ff`` version that are syntactically valid but semantically invalid.
- Header-injection defence: no CR/LF (or any control character) may reach an
  outbound HTTP header.
- tracestate/baggage length and control-character limits.
- Extraction from a request ``_meta`` in each shape it can arrive in.
- The per-request contextvar and its reset path.
"""

import pytest

from prometheus_mcp_server.spec2026 import otel
from prometheus_mcp_server.spec2026.otel import (
    BAGGAGE_HEADER,
    MAX_BAGGAGE_LENGTH,
    MAX_TRACESTATE_LENGTH,
    TRACEPARENT_HEADER,
    TRACESTATE_HEADER,
    clear_trace_headers,
    extract_trace_headers,
    get_trace_headers,
    is_safe_header_value,
    meta_as_mapping,
    reset_trace_headers,
    set_trace_headers,
    trace_context,
    validate_baggage,
    validate_traceparent,
    validate_tracestate,
)

# Canonical example from the W3C Trace Context specification.
VALID_TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
VALID_TRACESTATE = "rojo=00f067aa0ba902b7,congo=t61rcWkgMzE"
VALID_BAGGAGE = "userId=alice,serverNode=DF%2028,isProduction=false"


@pytest.fixture(autouse=True)
def reset_trace_context():
    """Keep the trace-header contextvar clean between tests."""
    clear_trace_headers()
    yield
    clear_trace_headers()


class TestValidateTraceparent:
    """Tests for W3C traceparent validation."""

    def test_valid_traceparent_accepted(self):
        """A canonical W3C traceparent passes unchanged."""
        assert validate_traceparent(VALID_TRACEPARENT) == VALID_TRACEPARENT

    def test_valid_traceparent_unsampled_flags_accepted(self):
        """Flags of 00 (not sampled) are still a valid traceparent."""
        value = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-00"
        assert validate_traceparent(value) == value

    def test_valid_future_version_accepted(self):
        """Any two hex digits other than ff are an acceptable version."""
        value = "01-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        assert validate_traceparent(value) == value

    @pytest.mark.parametrize(
        "version",
        ["0", "000", "gg", "0g", "-0", "  ", "0x"],
        ids=["one-digit", "three-digit", "non-hex", "half-hex", "dash", "spaces", "prefixed"],
    )
    def test_malformed_version_rejected(self, version):
        """A version field that is not exactly two lowercase hex digits is dropped."""
        value = f"{version}-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        assert validate_traceparent(value) is None

    def test_reserved_ff_version_rejected(self):
        """Version ff is reserved and forbidden by W3C."""
        value = "ff-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        assert validate_traceparent(value) is None

    def test_uppercase_hex_rejected(self):
        """W3C mandates lowercase hex; uppercase is rejected, not folded."""
        assert validate_traceparent(VALID_TRACEPARENT.upper()) is None

    def test_all_zero_trace_id_rejected(self):
        """An all-zero trace-id is invalid per W3C."""
        value = f"00-{'0' * 32}-00f067aa0ba902b7-01"
        assert validate_traceparent(value) is None

    def test_all_zero_parent_id_rejected(self):
        """An all-zero parent-id is invalid per W3C."""
        value = f"00-4bf92f3577b34da6a3ce929d0e0e4736-{'0' * 16}-01"
        assert validate_traceparent(value) is None

    @pytest.mark.parametrize(
        "value",
        [
            "00-4bf92f3577b34da6a3ce929d0e0e473-00f067aa0ba902b7-01",
            "00-4bf92f3577b34da6a3ce929d0e0e47366-00f067aa0ba902b7-01",
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b-01",
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b77-01",
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7",
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01-extra",
            "004bf92f3577b34da6a3ce929d0e0e473600f067aa0ba902b701",
            "",
        ],
        ids=[
            "short-trace-id",
            "long-trace-id",
            "short-parent-id",
            "long-parent-id",
            "missing-flags",
            "extra-field",
            "no-separators",
            "empty",
        ],
    )
    def test_structurally_malformed_rejected(self, value):
        """Field-length and separator violations are dropped."""
        assert validate_traceparent(value) is None

    @pytest.mark.parametrize(
        "value",
        [None, 42, 1.5, True, ["a"], {"a": 1}, VALID_TRACEPARENT.encode()],
        ids=["none", "int", "float", "bool", "list", "dict", "bytes"],
    )
    def test_non_string_rejected(self, value):
        """Non-string values are never coerced into a header."""
        assert validate_traceparent(value) is None

    @pytest.mark.parametrize(
        "value",
        [f" {VALID_TRACEPARENT}", f"{VALID_TRACEPARENT} ", f"\t{VALID_TRACEPARENT}"],
        ids=["leading-space", "trailing-space", "leading-tab"],
    )
    def test_padded_traceparent_rejected(self, value):
        """The traceparent grammar has no optional whitespace, so padding is invalid."""
        assert validate_traceparent(value) is None

    @pytest.mark.parametrize(
        "suffix",
        ["\r\nX-Injected: 1", "\nX-Injected: 1", "\rX-Injected: 1", "\x00"],
        ids=["crlf", "lf", "cr", "nul"],
    )
    def test_crlf_injection_rejected(self, suffix):
        """A traceparent carrying CR/LF must never be forwarded."""
        assert validate_traceparent(VALID_TRACEPARENT + suffix) is None

    def test_crlf_injection_inside_value_rejected(self):
        """Injection in the middle of the value is rejected too."""
        value = "00-4bf92f3577b34da6a3ce929d0e0e4736\r\nEvil: 1-00f067aa0ba902b7-01"
        assert validate_traceparent(value) is None


class TestValidateTracestate:
    """Tests for tracestate length and character limits."""

    def test_valid_tracestate_accepted(self):
        """A well formed tracestate passes unchanged."""
        assert validate_tracestate(VALID_TRACESTATE) == VALID_TRACESTATE

    def test_surrounding_whitespace_trimmed(self):
        """Optional whitespace around the value is insignificant and trimmed."""
        assert validate_tracestate(f"  {VALID_TRACESTATE}  ") == VALID_TRACESTATE

    def test_max_length_accepted(self):
        """A value exactly at the limit is accepted."""
        value = "a" * MAX_TRACESTATE_LENGTH
        assert validate_tracestate(value) == value

    def test_over_max_length_rejected(self):
        """A value one character over the limit is dropped."""
        assert validate_tracestate("a" * (MAX_TRACESTATE_LENGTH + 1)) is None

    def test_length_measured_after_trimming(self):
        """Padding does not push an otherwise legal value over the limit."""
        value = "a" * MAX_TRACESTATE_LENGTH
        assert validate_tracestate(f"  {value}  ") == value

    @pytest.mark.parametrize(
        "value",
        [
            "rojo=1\r\nX-Injected: 1",
            "rojo=1\nX-Injected: 1",
            "rojo=1\rX-Injected: 1",
            "rojo=1\x00",
            "rojo=1\tcongo=2",
            "rojo=1\x7f",
            "rojo=café",
        ],
        ids=["crlf", "lf", "cr", "nul", "tab", "del", "non-ascii"],
    )
    def test_control_characters_rejected(self, value):
        """Any character outside printable ASCII is rejected."""
        assert validate_tracestate(value) is None

    @pytest.mark.parametrize(
        "value", ["", "   ", None, 42, ["rojo=1"]],
        ids=["empty", "whitespace-only", "none", "int", "list"],
    )
    def test_empty_and_non_string_rejected(self, value):
        """Empty, whitespace-only and non-string values are dropped."""
        assert validate_tracestate(value) is None


class TestValidateBaggage:
    """Tests for baggage length and character limits."""

    def test_valid_baggage_accepted(self):
        """A well formed baggage value passes unchanged."""
        assert validate_baggage(VALID_BAGGAGE) == VALID_BAGGAGE

    def test_max_length_accepted(self):
        """A value exactly at the limit is accepted."""
        value = "b" * MAX_BAGGAGE_LENGTH
        assert validate_baggage(value) == value

    def test_over_max_length_rejected(self):
        """A value one character over the limit is dropped."""
        assert validate_baggage("b" * (MAX_BAGGAGE_LENGTH + 1)) is None

    def test_baggage_limit_is_larger_than_tracestate_limit(self):
        """Baggage has its own, larger W3C cap."""
        assert MAX_BAGGAGE_LENGTH > MAX_TRACESTATE_LENGTH

    @pytest.mark.parametrize(
        "value",
        ["k=v\r\nX-Injected: 1", "k=v\n", "k=v\r", "k=v\x00", "k=v\tj=w", "k=vé"],
        ids=["crlf", "lf", "cr", "nul", "tab", "non-ascii"],
    )
    def test_control_characters_rejected(self, value):
        """Any character outside printable ASCII is rejected."""
        assert validate_baggage(value) is None

    @pytest.mark.parametrize(
        "value", ["", "   ", None, {"k": "v"}],
        ids=["empty", "whitespace-only", "none", "dict"],
    )
    def test_empty_and_non_string_rejected(self, value):
        """Empty, whitespace-only and non-string values are dropped."""
        assert validate_baggage(value) is None


class TestIsSafeHeaderValue:
    """Tests for the printable-ASCII header guard."""

    @pytest.mark.parametrize("value", ["", "abc", "a=1,b=2", "  padded  ", "~!@#$%^&*()"])
    def test_printable_ascii_is_safe(self, value):
        """Printable ASCII, including spaces, is safe."""
        assert is_safe_header_value(value) is True

    @pytest.mark.parametrize("value", ["a\r\nb", "a\nb", "a\rb", "a\tb", "a\x00b", "a\x7fb", "café"])
    def test_control_and_non_ascii_is_unsafe(self, value):
        """Control characters and non-ASCII are unsafe."""
        assert is_safe_header_value(value) is False


class TestMetaAsMapping:
    """Tests for coercing the various ``_meta`` shapes into a dictionary."""

    def test_none_returns_empty(self):
        """A missing _meta yields an empty mapping."""
        assert meta_as_mapping(None) == {}

    def test_plain_dict_passthrough(self):
        """A plain dict is copied, not aliased."""
        source = {"traceparent": VALID_TRACEPARENT}
        result = meta_as_mapping(source)
        assert result == source
        result["extra"] = "x"
        assert "extra" not in source

    def test_model_extra_is_read(self):
        """Pydantic extension keys live in model_extra and are picked up."""

        class FakeMeta:
            model_extra = {"traceparent": VALID_TRACEPARENT}

        assert meta_as_mapping(FakeMeta()) == {"traceparent": VALID_TRACEPARENT}

    def test_model_dump_is_read(self):
        """A model exposing only model_dump is still readable."""

        class FakeMeta:
            def model_dump(self):
                return {"traceparent": VALID_TRACEPARENT}

        assert meta_as_mapping(FakeMeta()) == {"traceparent": VALID_TRACEPARENT}

    def test_model_extra_wins_over_model_dump(self):
        """model_extra is authoritative for spec extension keys."""

        class FakeMeta:
            model_extra = {"traceparent": VALID_TRACEPARENT}

            def model_dump(self):
                return {"traceparent": "stale", "progressToken": 1}

        result = meta_as_mapping(FakeMeta())
        assert result["traceparent"] == VALID_TRACEPARENT
        assert result["progressToken"] == 1

    def test_unusable_object_returns_empty(self):
        """An object with neither accessor degrades to an empty mapping."""
        assert meta_as_mapping(object()) == {}

    def test_non_mapping_returns_from_accessors_ignored(self):
        """Accessors returning non-mappings are ignored rather than trusted."""

        class FakeMeta:
            model_extra = "not-a-mapping"

            def model_dump(self):
                return ["not-a-mapping"]

        assert meta_as_mapping(FakeMeta()) == {}

    def test_real_sdk_meta_model(self):
        """The real SDK RequestParams.Meta model round-trips extension keys."""
        from mcp.types import RequestParams

        meta = RequestParams.Meta(**{"traceparent": VALID_TRACEPARENT})
        assert meta_as_mapping(meta)["traceparent"] == VALID_TRACEPARENT


class TestExtractTraceHeaders:
    """Tests for building outbound headers from a request ``_meta``."""

    @pytest.mark.parametrize(
        "meta",
        [None, {}, {"progressToken": 1}, object(), []],
        ids=["none", "empty-dict", "unrelated-keys", "plain-object", "empty-list"],
    )
    def test_absent_meta_yields_empty_dict(self, meta):
        """No trace context present means no headers to forward."""
        assert extract_trace_headers(meta) == {}

    def test_full_valid_set_forwarded(self):
        """All three valid values are forwarded."""
        headers = extract_trace_headers(
            {
                TRACEPARENT_HEADER: VALID_TRACEPARENT,
                TRACESTATE_HEADER: VALID_TRACESTATE,
                BAGGAGE_HEADER: VALID_BAGGAGE,
            }
        )
        assert headers == {
            TRACEPARENT_HEADER: VALID_TRACEPARENT,
            TRACESTATE_HEADER: VALID_TRACESTATE,
            BAGGAGE_HEADER: VALID_BAGGAGE,
        }

    def test_traceparent_only(self):
        """A lone valid traceparent is forwarded."""
        headers = extract_trace_headers({TRACEPARENT_HEADER: VALID_TRACEPARENT})
        assert headers == {TRACEPARENT_HEADER: VALID_TRACEPARENT}

    def test_case_insensitive_meta_keys(self):
        """Meta keys are matched case-insensitively."""
        headers = extract_trace_headers({"TraceParent": VALID_TRACEPARENT})
        assert headers == {TRACEPARENT_HEADER: VALID_TRACEPARENT}

    def test_exact_key_preferred_over_case_variant(self):
        """An exact lowercase key wins over a differently cased duplicate."""
        headers = extract_trace_headers(
            {TRACEPARENT_HEADER: VALID_TRACEPARENT, "TRACEPARENT": "bogus"}
        )
        assert headers == {TRACEPARENT_HEADER: VALID_TRACEPARENT}

    def test_malformed_traceparent_drops_traceparent_and_tracestate(self):
        """tracestate must not be propagated without a valid traceparent."""
        headers = extract_trace_headers(
            {
                TRACEPARENT_HEADER: "not-a-traceparent",
                TRACESTATE_HEADER: VALID_TRACESTATE,
                BAGGAGE_HEADER: VALID_BAGGAGE,
            }
        )
        assert headers == {BAGGAGE_HEADER: VALID_BAGGAGE}

    def test_tracestate_without_traceparent_dropped(self):
        """A lone tracestate is meaningless and is dropped."""
        headers = extract_trace_headers({TRACESTATE_HEADER: VALID_TRACESTATE})
        assert headers == {}

    def test_baggage_without_traceparent_kept(self):
        """Baggage is an independent propagation format and stands alone."""
        headers = extract_trace_headers({BAGGAGE_HEADER: VALID_BAGGAGE})
        assert headers == {BAGGAGE_HEADER: VALID_BAGGAGE}

    def test_oversized_tracestate_dropped_but_traceparent_kept(self):
        """One bad value does not poison the others."""
        headers = extract_trace_headers(
            {
                TRACEPARENT_HEADER: VALID_TRACEPARENT,
                TRACESTATE_HEADER: "a" * (MAX_TRACESTATE_LENGTH + 1),
            }
        )
        assert headers == {TRACEPARENT_HEADER: VALID_TRACEPARENT}

    def test_crlf_injection_in_meta_yields_empty_dict(self):
        """A hostile _meta cannot inject headers into the Prometheus request."""
        headers = extract_trace_headers(
            {
                TRACEPARENT_HEADER: f"{VALID_TRACEPARENT}\r\nX-Injected: 1",
                TRACESTATE_HEADER: "rojo=1\r\nX-Injected: 1",
                BAGGAGE_HEADER: "k=v\r\nX-Injected: 1",
            }
        )
        assert headers == {}

    def test_no_forwarded_value_contains_crlf(self):
        """Nothing that survives extraction may contain CR or LF."""
        headers = extract_trace_headers(
            {
                TRACEPARENT_HEADER: VALID_TRACEPARENT,
                TRACESTATE_HEADER: VALID_TRACESTATE,
                BAGGAGE_HEADER: VALID_BAGGAGE,
            }
        )
        for value in headers.values():
            assert "\r" not in value and "\n" not in value

    def test_pydantic_style_meta(self):
        """A pydantic-style meta object is read via model_extra."""

        class FakeMeta:
            model_extra = {
                TRACEPARENT_HEADER: VALID_TRACEPARENT,
                BAGGAGE_HEADER: VALID_BAGGAGE,
            }

        headers = extract_trace_headers(FakeMeta())
        assert headers == {
            TRACEPARENT_HEADER: VALID_TRACEPARENT,
            BAGGAGE_HEADER: VALID_BAGGAGE,
        }

    def test_real_sdk_meta_model(self):
        """Extraction works against the real SDK RequestParams.Meta model."""
        from mcp.types import RequestParams

        meta = RequestParams.Meta(
            **{
                TRACEPARENT_HEADER: VALID_TRACEPARENT,
                TRACESTATE_HEADER: VALID_TRACESTATE,
            }
        )
        assert extract_trace_headers(meta) == {
            TRACEPARENT_HEADER: VALID_TRACEPARENT,
            TRACESTATE_HEADER: VALID_TRACESTATE,
        }

    def test_final_injection_guard_drops_poisoned_value(self, monkeypatch):
        """Defence in depth: a validator returning CR/LF is still caught."""
        monkeypatch.setattr(otel, "validate_baggage", lambda value: "evil\r\nX-Injected: 1")
        assert extract_trace_headers({BAGGAGE_HEADER: "k=v"}) == {}

    def test_exception_during_extraction_degrades_to_empty(self):
        """A hostile or unexpected meta object never propagates an exception."""

        class ExplodingMeta:
            @property
            def model_extra(self):
                raise RuntimeError("boom")

        assert extract_trace_headers(ExplodingMeta()) == {}


class TestTraceHeaderContext:
    """Tests for the per-request contextvar and its reset path."""

    def test_default_is_empty(self):
        """Nothing stashed means no headers."""
        assert get_trace_headers() == {}

    def test_set_and_get(self):
        """Stashed headers are readable."""
        set_trace_headers({TRACEPARENT_HEADER: VALID_TRACEPARENT})
        assert get_trace_headers() == {TRACEPARENT_HEADER: VALID_TRACEPARENT}

    def test_set_none_stores_empty(self):
        """Setting None stashes an empty mapping."""
        set_trace_headers(None)
        assert get_trace_headers() == {}

    def test_getter_returns_a_copy(self):
        """Callers cannot mutate the stored state through the getter."""
        set_trace_headers({TRACEPARENT_HEADER: VALID_TRACEPARENT})
        borrowed = get_trace_headers()
        borrowed["injected"] = "evil"
        assert get_trace_headers() == {TRACEPARENT_HEADER: VALID_TRACEPARENT}

    def test_setter_copies_input(self):
        """Later mutation of the caller's dict does not affect stored state."""
        source = {TRACEPARENT_HEADER: VALID_TRACEPARENT}
        set_trace_headers(source)
        source["injected"] = "evil"
        assert get_trace_headers() == {TRACEPARENT_HEADER: VALID_TRACEPARENT}

    def test_reset_restores_previous_value(self):
        """The token restores exactly the prior value."""
        set_trace_headers({TRACEPARENT_HEADER: VALID_TRACEPARENT})
        token = set_trace_headers({BAGGAGE_HEADER: VALID_BAGGAGE})
        assert get_trace_headers() == {BAGGAGE_HEADER: VALID_BAGGAGE}
        reset_trace_headers(token)
        assert get_trace_headers() == {TRACEPARENT_HEADER: VALID_TRACEPARENT}

    def test_reset_with_stale_token_is_logged_not_raised(self):
        """A double reset must not break request handling."""
        token = set_trace_headers({TRACEPARENT_HEADER: VALID_TRACEPARENT})
        reset_trace_headers(token)
        reset_trace_headers(token)
        assert get_trace_headers() == {}

    def test_clear(self):
        """Clearing drops whatever was stashed."""
        set_trace_headers({TRACEPARENT_HEADER: VALID_TRACEPARENT})
        clear_trace_headers()
        assert get_trace_headers() == {}

    def test_trace_context_sets_and_restores(self):
        """The context manager stashes on entry and restores on exit."""
        meta = {TRACEPARENT_HEADER: VALID_TRACEPARENT}
        with trace_context(meta) as headers:
            assert headers == {TRACEPARENT_HEADER: VALID_TRACEPARENT}
            assert get_trace_headers() == {TRACEPARENT_HEADER: VALID_TRACEPARENT}
        assert get_trace_headers() == {}

    def test_trace_context_restores_on_exception(self):
        """The previous value is restored even when the block raises."""
        set_trace_headers({BAGGAGE_HEADER: VALID_BAGGAGE})
        with pytest.raises(ValueError):
            with trace_context({TRACEPARENT_HEADER: VALID_TRACEPARENT}):
                raise ValueError("boom")
        assert get_trace_headers() == {BAGGAGE_HEADER: VALID_BAGGAGE}

    def test_trace_context_with_invalid_meta_yields_empty(self):
        """Invalid trace context stashes nothing."""
        with trace_context({TRACEPARENT_HEADER: "bogus"}) as headers:
            assert headers == {}
            assert get_trace_headers() == {}

    def test_headers_are_mergeable_into_outbound_request(self):
        """The stashed headers merge cleanly onto existing request headers."""
        set_trace_headers(extract_trace_headers({TRACEPARENT_HEADER: VALID_TRACEPARENT}))
        outbound = {"X-Scope-OrgID": "tenant-1"}
        outbound.update(get_trace_headers())
        assert outbound == {
            "X-Scope-OrgID": "tenant-1",
            TRACEPARENT_HEADER: VALID_TRACEPARENT,
        }
