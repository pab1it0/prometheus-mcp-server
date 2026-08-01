#!/usr/bin/env python

"""OpenTelemetry trace-context propagation (MCP 2026-07-28, feature F7).

The 2026-07-28 specification allows a request's ``_meta`` object to carry W3C
Trace Context fields (``traceparent``, ``tracestate``) and a W3C Baggage field
(``baggage``). This module reads those values off an incoming request, validates
them, and exposes them as outbound HTTP headers so they can be merged into the
request the server makes against Prometheus.

Security posture: a value that fails validation is *dropped*, never forwarded.
Every candidate is checked against a printable-ASCII allowlist before anything
else, so a hostile ``_meta`` payload cannot smuggle CR/LF into the outbound
request and inject extra HTTP headers.

Standard library only - this module intentionally has no dependency on any
OpenTelemetry package, nor on the MCP SDK internals.
"""

import re
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Dict, Iterator, Optional

from prometheus_mcp_server.logging_config import get_logger

logger = get_logger()

# Header / _meta key names. The spec uses the lowercase W3C names verbatim.
TRACEPARENT_HEADER = "traceparent"
TRACESTATE_HEADER = "tracestate"
BAGGAGE_HEADER = "baggage"

TRACE_CONTEXT_KEYS = (TRACEPARENT_HEADER, TRACESTATE_HEADER, BAGGAGE_HEADER)

# W3C Trace Context recommends implementations cap ``tracestate`` at 512 chars.
MAX_TRACESTATE_LENGTH = 512
# W3C Baggage caps the whole header at 8192 bytes.
MAX_BAGGAGE_LENGTH = 8192

# Values that are syntactically well formed but invalid per W3C.
INVALID_VERSION = "ff"
INVALID_TRACE_ID = "0" * 32
INVALID_PARENT_ID = "0" * 16

# version(2 hex) "-" trace-id(32 hex) "-" parent-id(16 hex) "-" flags(2 hex).
# W3C mandates lowercase hex, so uppercase input is rejected rather than folded.
_TRACEPARENT_RE = re.compile(r"[0-9a-f]{2}-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}")

# Printable ASCII only. This excludes CR, LF, NUL, DEL, HTAB and every other
# control character, plus all non-ASCII. HTAB is technically legal as optional
# whitespace in the tracestate grammar, but it carries no meaning there and
# rejecting it keeps the header-injection guard trivially auditable.
_PRINTABLE_ASCII_RE = re.compile(r"[\x20-\x7e]*")

# Per-request storage so make_prometheus_request can pick up the headers without
# threading them through every tool signature.
_TRACE_HEADERS: ContextVar[Optional[Dict[str, str]]] = ContextVar(
    "prometheus_mcp_trace_headers", default=None
)


def is_safe_header_value(value: str) -> bool:
    """Check that a string is safe to place in an HTTP header value.

    Args:
        value: Candidate header value.

    Returns:
        True when the value is printable ASCII only, i.e. contains no CR, LF or
        any other control character. False otherwise.
    """
    return _PRINTABLE_ASCII_RE.fullmatch(value) is not None


def validate_traceparent(value: Any) -> Optional[str]:
    """Validate a W3C Trace Context ``traceparent`` value.

    Enforces the ``version-traceid-parentid-flags`` layout and rejects the
    reserved ``ff`` version, the all-zero trace-id and the all-zero parent-id,
    all of which are invalid per W3C Trace Context.

    Args:
        value: Candidate value taken from a request's ``_meta``. Any type is
            accepted; non-strings are rejected.

    Returns:
        The validated ``traceparent`` string, or None when the value is absent
        or malformed and therefore must not be forwarded.
    """
    if not isinstance(value, str) or not value:
        return None

    if not is_safe_header_value(value):
        logger.warning(
            "Rejected traceparent with unsafe characters",
            header=TRACEPARENT_HEADER,
            reason="control_characters",
        )
        return None

    if _TRACEPARENT_RE.fullmatch(value) is None:
        logger.warning(
            "Rejected malformed traceparent",
            header=TRACEPARENT_HEADER,
            reason="format",
        )
        return None

    version, trace_id, parent_id, _flags = value.split("-")

    if version == INVALID_VERSION:
        logger.warning(
            "Rejected traceparent with forbidden version",
            header=TRACEPARENT_HEADER,
            reason="forbidden_version",
        )
        return None

    if trace_id == INVALID_TRACE_ID:
        logger.warning(
            "Rejected traceparent with all-zero trace id",
            header=TRACEPARENT_HEADER,
            reason="all_zero_trace_id",
        )
        return None

    if parent_id == INVALID_PARENT_ID:
        logger.warning(
            "Rejected traceparent with all-zero parent id",
            header=TRACEPARENT_HEADER,
            reason="all_zero_parent_id",
        )
        return None

    return value


def _validate_list_header(value: Any, header: str, max_length: int) -> Optional[str]:
    """Validate a comma-separated list-style trace header value.

    Args:
        value: Candidate value taken from a request's ``_meta``.
        header: Header name, used for log context only.
        max_length: Maximum permitted length of the trimmed value.

    Returns:
        The trimmed value, or None when it is absent, empty, too long or
        contains characters that are unsafe in an HTTP header.
    """
    if not isinstance(value, str) or not value:
        return None

    if not is_safe_header_value(value):
        logger.warning(
            "Rejected trace header with unsafe characters",
            header=header,
            reason="control_characters",
        )
        return None

    # Only spaces can survive the printable-ASCII check, and leading/trailing
    # optional whitespace is insignificant in both grammars.
    trimmed = value.strip()

    if not trimmed:
        logger.warning("Rejected empty trace header", header=header, reason="empty")
        return None

    if len(trimmed) > max_length:
        logger.warning(
            "Rejected oversized trace header",
            header=header,
            reason="too_long",
            length=len(trimmed),
            max_length=max_length,
        )
        return None

    return trimmed


def validate_tracestate(value: Any) -> Optional[str]:
    """Validate a W3C Trace Context ``tracestate`` value.

    Args:
        value: Candidate value taken from a request's ``_meta``.

    Returns:
        The validated ``tracestate`` string, or None when it must be dropped.
    """
    return _validate_list_header(value, TRACESTATE_HEADER, MAX_TRACESTATE_LENGTH)


def validate_baggage(value: Any) -> Optional[str]:
    """Validate a W3C Baggage ``baggage`` value.

    Args:
        value: Candidate value taken from a request's ``_meta``.

    Returns:
        The validated ``baggage`` string, or None when it must be dropped.
    """
    return _validate_list_header(value, BAGGAGE_HEADER, MAX_BAGGAGE_LENGTH)


def meta_as_mapping(meta: Any) -> Dict[str, Any]:
    """Coerce a request ``_meta`` value into a plain dictionary.

    Handles the three shapes this can arrive in: None, a plain mapping, or the
    SDK's pydantic ``RequestParams.Meta`` model (which keeps spec extension keys
    such as ``traceparent`` in ``model_extra`` because it is ``extra="allow"``).

    Args:
        meta: The request's ``_meta`` value.

    Returns:
        Dictionary of meta keys to values. Empty when nothing usable is present.
    """
    if meta is None:
        return {}

    if isinstance(meta, Mapping):
        return dict(meta)

    collected: Dict[str, Any] = {}

    model_dump = getattr(meta, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, Mapping):
            collected.update(dumped)

    # ``model_extra`` is authoritative for spec extension keys, so it wins.
    model_extra = getattr(meta, "model_extra", None)
    if isinstance(model_extra, Mapping):
        collected.update(model_extra)

    return collected


def _lookup(data: Dict[str, Any], key: str) -> Any:
    """Look up a lowercase key in a meta mapping, case-insensitively."""
    if key in data:
        return data[key]

    for candidate_key, candidate_value in data.items():
        if isinstance(candidate_key, str) and candidate_key.lower() == key:
            return candidate_value

    return None


def extract_trace_headers(meta: Any) -> Dict[str, str]:
    """Build outbound HTTP trace-context headers from a request's ``_meta``.

    The returned mapping is intended to be merged into the headers of the
    outbound Prometheus request. Callers decide precedence; merging these last
    lets a caller-configured header win or lose as they prefer.

    Args:
        meta: The request's ``_meta`` value. May be None, a plain mapping, or
            the SDK's pydantic ``RequestParams.Meta`` model.

    Returns:
        Mapping of HTTP header name to validated value. Empty when ``_meta`` is
        absent or carries no forwardable trace context.
    """
    try:
        data = meta_as_mapping(meta)
        if not data:
            return {}

        headers: Dict[str, str] = {}

        traceparent = validate_traceparent(_lookup(data, TRACEPARENT_HEADER))
        if traceparent is not None:
            headers[TRACEPARENT_HEADER] = traceparent

            # W3C Trace Context: tracestate is meaningless without a valid
            # traceparent and MUST NOT be propagated on its own.
            tracestate = validate_tracestate(_lookup(data, TRACESTATE_HEADER))
            if tracestate is not None:
                headers[TRACESTATE_HEADER] = tracestate
        elif _lookup(data, TRACESTATE_HEADER) is not None:
            logger.warning(
                "Dropping tracestate without a valid traceparent",
                header=TRACESTATE_HEADER,
                reason="no_traceparent",
            )

        # W3C Baggage is a separate propagation format and stands alone.
        baggage = validate_baggage(_lookup(data, BAGGAGE_HEADER))
        if baggage is not None:
            headers[BAGGAGE_HEADER] = baggage

        # Defence in depth: nothing leaves this function without a final
        # header-injection check, even if a validator above is ever changed.
        safe_headers = {
            name: value
            for name, value in headers.items()
            if is_safe_header_value(value)
        }
        if len(safe_headers) != len(headers):
            logger.warning(
                "Dropped trace headers that failed the final injection guard",
                dropped=sorted(set(headers) - set(safe_headers)),
            )

        return safe_headers

    except Exception as e:
        logger.warning(
            "Failed to extract trace context from request _meta",
            error=str(e),
            error_type=type(e).__name__,
        )
        return {}


def set_trace_headers(headers: Optional[Mapping]) -> Token:
    """Stash trace-context headers for the current request context.

    Args:
        headers: Headers to stash, typically from ``extract_trace_headers``.
            None or empty stashes an empty mapping.

    Returns:
        Token that restores the previous value when passed to
        ``reset_trace_headers``.
    """
    snapshot: Dict[str, str] = dict(headers) if headers else {}
    return _TRACE_HEADERS.set(snapshot)


def get_trace_headers() -> Dict[str, str]:
    """Get the trace-context headers stashed for the current context.

    Returns:
        Copy of the stashed headers, so callers cannot mutate the stored state.
        Empty dictionary when nothing is stashed.
    """
    current = _TRACE_HEADERS.get()
    return dict(current) if current else {}


def reset_trace_headers(token: Token) -> None:
    """Restore the trace-header context to its value before ``set_trace_headers``.

    Args:
        token: Token returned by ``set_trace_headers``.

    Returns:
        None. A stale or foreign token is logged and ignored rather than raised,
        so a reset can never break request handling.
    """
    # CPython raises RuntimeError for an already-used token and ValueError for a
    # token created in a different Context, so catch broadly rather than guess.
    try:
        _TRACE_HEADERS.reset(token)
    except Exception as e:
        logger.warning(
            "Failed to reset trace header context",
            error=str(e),
            error_type=type(e).__name__,
        )


def clear_trace_headers() -> None:
    """Drop any trace-context headers stashed for the current context.

    Returns:
        None.
    """
    _TRACE_HEADERS.set(None)


@contextmanager
def trace_context(meta: Any) -> Iterator[Dict[str, str]]:
    """Extract trace headers from ``_meta`` and stash them for the block's scope.

    Args:
        meta: The request's ``_meta`` value.

    Returns:
        Context manager yielding the extracted headers. The previous context
        value is always restored on exit, including on exception.
    """
    headers = extract_trace_headers(meta)
    token = set_trace_headers(headers)
    try:
        yield headers
    finally:
        reset_trace_headers(token)
