#!/usr/bin/env python

"""Protocol constants for the MCP 2026-07-28 compatibility layer.

The installed ``mcp`` SDK advertises ``2025-11-25`` as its latest protocol
version, so every 2026-07-28 concept used by this server is defined here rather
than imported from the SDK. Keeping the literals in one module means the
envelope, discovery, negotiation, header and OpenTelemetry modules cannot drift
apart from one another.

This module is intentionally free of logic and of any non-stdlib import so that
importing it can never fail, regardless of which SDK version is installed.
"""

from typing import Final, FrozenSet, Tuple

# ---------------------------------------------------------------------------
# Protocol versions
# ---------------------------------------------------------------------------

#: The protocol revision this compatibility layer targets.
PROTOCOL_VERSION_2026: Final[str] = "2026-07-28"

#: The revision implemented natively by the installed ``mcp`` SDK.
PROTOCOL_VERSION_2025_11: Final[str] = "2025-11-25"

#: The previous revision, still spoken by older clients.
PROTOCOL_VERSION_2025_06: Final[str] = "2025-06-18"

#: Every revision this server accepts, newest first. A request that names a
#: version outside this tuple is rejected with ``UNSUPPORTED_PROTOCOL_VERSION``;
#: a request that names no version at all is treated as legacy and accepted.
SUPPORTED_PROTOCOL_VERSIONS: Final[Tuple[str, ...]] = (
    PROTOCOL_VERSION_2026,
    PROTOCOL_VERSION_2025_11,
    PROTOCOL_VERSION_2025_06,
)

# ---------------------------------------------------------------------------
# ``_meta`` key names
# ---------------------------------------------------------------------------

#: Reserved prefix for keys defined by the MCP specification itself.
META_NAMESPACE: Final[str] = "io.modelcontextprotocol/"

#: Protocol revision the client is speaking on this particular request.
META_PROTOCOL_VERSION: Final[str] = "io.modelcontextprotocol/protocolVersion"

#: Client capabilities, declared per-request (never inferred from history).
META_CLIENT_CAPABILITIES: Final[str] = "io.modelcontextprotocol/clientCapabilities"

#: Client implementation name/version, declared per-request.
META_CLIENT_INFO: Final[str] = "io.modelcontextprotocol/clientInfo"

#: Server implementation name/version, echoed back on every result.
META_SERVER_INFO: Final[str] = "io.modelcontextprotocol/serverInfo"

#: Minimum severity the client wants ``notifications/message`` for. When this
#: key is absent the server MUST NOT emit log notifications for the request.
META_LOG_LEVEL: Final[str] = "io.modelcontextprotocol/logLevel"

#: Identifier correlating a request with a client-side subscription.
META_SUBSCRIPTION_ID: Final[str] = "io.modelcontextprotocol/subscriptionId"

# W3C trace-context keys. These are unprefixed by design: the 2026-07-28 minor
# change #2 reuses the wire names from the W3C Trace Context recommendation.
META_TRACEPARENT: Final[str] = "traceparent"
META_TRACESTATE: Final[str] = "tracestate"
META_BAGGAGE: Final[str] = "baggage"

#: Trace-context keys that may be forwarded onto outbound HTTP requests.
OTEL_META_KEYS: Final[Tuple[str, ...]] = (
    META_TRACEPARENT,
    META_TRACESTATE,
    META_BAGGAGE,
)

# ---------------------------------------------------------------------------
# JSON-RPC error codes
# ---------------------------------------------------------------------------

#: An ``Mcp-*`` HTTP header disagrees with the JSON-RPC body it accompanies.
HEADER_MISMATCH: Final[int] = -32020

#: The request needs a client capability the client did not declare.
MISSING_REQUIRED_CLIENT_CAPABILITY: Final[int] = -32021

#: The request named a protocol version outside SUPPORTED_PROTOCOL_VERSIONS.
UNSUPPORTED_PROTOCOL_VERSION: Final[int] = -32022

#: Standard JSON-RPC invalid-params code. 2026-07-28 reuses this for
#: resource-not-found, which earlier revisions reported as ``-32002``.
INVALID_PARAMS: Final[int] = -32602

#: Superseded resource-not-found code, kept for documentation of the change.
LEGACY_RESOURCE_NOT_FOUND: Final[int] = -32002

# ---------------------------------------------------------------------------
# Result envelope
# ---------------------------------------------------------------------------

#: The result carries the full answer; no further interaction is required.
RESULT_TYPE_COMPLETE: Final[str] = "complete"

#: The server needs more input before it can complete the request.
RESULT_TYPE_INPUT_REQUIRED: Final[str] = "input_required"

#: Every legal value of the ``resultType`` envelope field.
RESULT_TYPES: Final[Tuple[str, ...]] = (
    RESULT_TYPE_COMPLETE,
    RESULT_TYPE_INPUT_REQUIRED,
)

#: Results identical for every caller may be cached in a shared cache.
CACHE_SCOPE_PUBLIC: Final[str] = "public"

#: Results vary per caller and may only be cached per-client.
CACHE_SCOPE_PRIVATE: Final[str] = "private"

#: Every legal value of the ``cacheScope`` envelope field.
CACHE_SCOPES: Final[Tuple[str, ...]] = (
    CACHE_SCOPE_PUBLIC,
    CACHE_SCOPE_PRIVATE,
)

#: Default lifetime advertised via ``ttlMs`` for cacheable results (5 minutes,
#: matching the server's existing in-process metrics cache).
DEFAULT_CACHE_TTL_MS: Final[int] = 300000

#: Default lifetime advertised for ``server/discover``, which changes only when
#: the server itself is redeployed (1 hour).
DISCOVER_CACHE_TTL_MS: Final[int] = 3600000

# ---------------------------------------------------------------------------
# Method names
# ---------------------------------------------------------------------------

METHOD_SERVER_DISCOVER: Final[str] = "server/discover"
METHOD_TOOLS_LIST: Final[str] = "tools/list"
METHOD_TOOLS_CALL: Final[str] = "tools/call"
METHOD_PROMPTS_LIST: Final[str] = "prompts/list"
METHOD_RESOURCES_LIST: Final[str] = "resources/list"
METHOD_RESOURCES_TEMPLATES_LIST: Final[str] = "resources/templates/list"
METHOD_RESOURCES_READ: Final[str] = "resources/read"

#: Methods whose results carry ``ttlMs`` and ``cacheScope``. Notably excludes
#: ``tools/call``, whose results are never cacheable.
CACHEABLE_METHODS: Final[FrozenSet[str]] = frozenset(
    {
        METHOD_SERVER_DISCOVER,
        METHOD_TOOLS_LIST,
        METHOD_PROMPTS_LIST,
        METHOD_RESOURCES_LIST,
        METHOD_RESOURCES_TEMPLATES_LIST,
        METHOD_RESOURCES_READ,
    }
)

__all__ = [
    "CACHEABLE_METHODS",
    "CACHE_SCOPES",
    "CACHE_SCOPE_PRIVATE",
    "CACHE_SCOPE_PUBLIC",
    "DEFAULT_CACHE_TTL_MS",
    "DISCOVER_CACHE_TTL_MS",
    "HEADER_MISMATCH",
    "INVALID_PARAMS",
    "LEGACY_RESOURCE_NOT_FOUND",
    "METHOD_PROMPTS_LIST",
    "METHOD_RESOURCES_LIST",
    "METHOD_RESOURCES_READ",
    "METHOD_RESOURCES_TEMPLATES_LIST",
    "METHOD_SERVER_DISCOVER",
    "METHOD_TOOLS_CALL",
    "METHOD_TOOLS_LIST",
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
]
