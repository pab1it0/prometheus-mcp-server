#!/usr/bin/env python

"""MCP 2026-07-28 server-side compatibility layer.

The installed ``mcp`` SDK implements protocol ``2025-11-25`` and knows nothing
about the 2026-07-28 revision, so every 2026-07-28 concept this server speaks is
built here on top of the SDK rather than imported from it.

The layer is split into six modules, each of which is importable on its own and
none of which raises at import time even if the SDK's internals change:

``constants``
    Protocol versions, ``_meta`` key names, JSON-RPC error codes and envelope
    literals. Pure data, no logic, stdlib-only.
``envelope``
    F2/F3. Wraps the low-level ``request_handlers`` so every result carries
    ``resultType``, ``_meta`` serverInfo and (for cacheable methods)
    ``ttlMs``/``cacheScope``, and so listable collections are returned sorted.
``discovery``
    F4/F8. Registers ``server/discover`` as a real JSON-RPC method, as a plain
    ``GET /server/discover`` HTTP route, and as an MCP tool, all fed by one
    payload factory.
``negotiation``
    F5. Parses per-request ``_meta`` (protocol version, client capabilities,
    client info, log level) and publishes it on a ``ContextVar`` for the
    duration of the request.
``headers``
    F6. Base64 sentinel codec, ``Mcp-*`` header/body validation, and
    ``x-mcp-header`` input-schema annotation support.
``otel``
    F7. Extracts W3C trace context from a request's ``_meta`` and exposes it as
    outbound HTTP headers.

Everything is additive: a 2025-11-25 client sees the same server it always did.
The whole layer is gated behind ``PROMETHEUS_MCP_SPEC_2026`` (default on).
"""

from prometheus_mcp_server.spec2026.constants import (
    CACHE_SCOPE_PRIVATE,
    CACHE_SCOPE_PUBLIC,
    CACHE_SCOPES,
    CACHEABLE_METHODS,
    DEFAULT_CACHE_TTL_MS,
    DISCOVER_CACHE_TTL_MS,
    HEADER_MISMATCH,
    INVALID_PARAMS,
    META_BAGGAGE,
    META_CLIENT_CAPABILITIES,
    META_CLIENT_INFO,
    META_LOG_LEVEL,
    META_NAMESPACE,
    META_PROTOCOL_VERSION,
    META_SERVER_INFO,
    META_SUBSCRIPTION_ID,
    META_TRACEPARENT,
    META_TRACESTATE,
    METHOD_SERVER_DISCOVER,
    MISSING_REQUIRED_CLIENT_CAPABILITY,
    OTEL_META_KEYS,
    PROTOCOL_VERSION_2025_06,
    PROTOCOL_VERSION_2025_11,
    PROTOCOL_VERSION_2026,
    RESULT_TYPE_COMPLETE,
    RESULT_TYPE_INPUT_REQUIRED,
    RESULT_TYPES,
    SUPPORTED_PROTOCOL_VERSIONS,
    UNSUPPORTED_PROTOCOL_VERSION,
)
from prometheus_mcp_server.spec2026.asgi import (
    StrictHeaderMiddleware,
    strict_header_middleware,
    tool_annotation_lookup,
)
from prometheus_mcp_server.spec2026.discovery import (
    DISCOVER_METHOD,
    DISCOVER_ROUTE_PATH,
    DISCOVER_TOOL_BASE_NAME,
    build_discover_payload,
    install_discovery,
)
from prometheus_mcp_server.spec2026.envelope import (
    enrich_result,
    install_envelope,
    install_initialize_envelope,
    uninstall_envelope,
    uninstall_initialize_envelope,
)
from prometheus_mcp_server.spec2026.headers import (
    HeaderAnnotation,
    is_safe_plain_ascii,
    HeaderValidationError,
    SchemaAnnotationError,
    collect_header_annotations,
    decode_header_value,
    encode_header_value,
    extract_param_headers,
    header_mismatch_error,
    validate_header_annotations,
    validate_request_headers,
)
from prometheus_mcp_server.spec2026.negotiation import (
    NegotiationError,
    RequestNegotiation,
    UnsupportedProtocolVersionError,
    clear_current_negotiation,
    current_log_level,
    extract_request_meta,
    get_current_negotiation,
    may_emit_log_notifications,
    negotiate_request,
    negotiation_scope,
)
from prometheus_mcp_server.spec2026.otel import (
    clear_trace_headers,
    extract_trace_headers,
    get_trace_headers,
    reset_trace_headers,
    set_trace_headers,
    trace_context,
)

__all__ = [
    # constants
    "CACHEABLE_METHODS",
    "CACHE_SCOPES",
    "CACHE_SCOPE_PRIVATE",
    "CACHE_SCOPE_PUBLIC",
    "DEFAULT_CACHE_TTL_MS",
    "DISCOVER_CACHE_TTL_MS",
    "HEADER_MISMATCH",
    "INVALID_PARAMS",
    "METHOD_SERVER_DISCOVER",
    "META_BAGGAGE",
    "META_CLIENT_CAPABILITIES",
    "META_CLIENT_INFO",
    "META_LOG_LEVEL",
    "META_NAMESPACE",
    "META_PROTOCOL_VERSION",
    "META_SERVER_INFO",
    "META_SUBSCRIPTION_ID",
    "META_TRACEPARENT",
    "META_TRACESTATE",
    "MISSING_REQUIRED_CLIENT_CAPABILITY",
    "OTEL_META_KEYS",
    "PROTOCOL_VERSION_2025_06",
    "PROTOCOL_VERSION_2025_11",
    "PROTOCOL_VERSION_2026",
    "RESULT_TYPES",
    "RESULT_TYPE_COMPLETE",
    "RESULT_TYPE_INPUT_REQUIRED",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "UNSUPPORTED_PROTOCOL_VERSION",
    # discovery (F4/F8)
    "DISCOVER_METHOD",
    "DISCOVER_ROUTE_PATH",
    "DISCOVER_TOOL_BASE_NAME",
    "build_discover_payload",
    "install_discovery",
    # envelope (F2/F3)
    "enrich_result",
    "install_envelope",
    "install_initialize_envelope",
    "uninstall_envelope",
    "uninstall_initialize_envelope",
    # headers (F6)
    "HeaderAnnotation",
    "StrictHeaderMiddleware",
    "is_safe_plain_ascii",
    "strict_header_middleware",
    "tool_annotation_lookup",
    "HeaderValidationError",
    "SchemaAnnotationError",
    "collect_header_annotations",
    "decode_header_value",
    "encode_header_value",
    "extract_param_headers",
    "header_mismatch_error",
    "validate_header_annotations",
    "validate_request_headers",
    # negotiation (F5)
    "NegotiationError",
    "RequestNegotiation",
    "UnsupportedProtocolVersionError",
    "clear_current_negotiation",
    "current_log_level",
    "extract_request_meta",
    "get_current_negotiation",
    "may_emit_log_notifications",
    "negotiate_request",
    "negotiation_scope",
    # otel (F7)
    "clear_trace_headers",
    "extract_trace_headers",
    "get_trace_headers",
    "reset_trace_headers",
    "set_trace_headers",
    "trace_context",
]
