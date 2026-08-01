#!/usr/bin/env python

"""Result envelope enrichment for MCP 2026-07-28 (design features F2 and F3).

The installed ``mcp`` SDK implements protocol ``2025-11-25`` and knows nothing
about the 2026-07-28 result envelope. This module adds it server-side by
wrapping the low-level server's ``request_handlers`` -- the one place where
every request type is turned into a ``ServerResult``.

Two facts make this work, both verified against ``mcp==1.29.0``:

1. ``mcp.types.Result`` is declared with ``model_config = {"extra": "allow"}``,
   so assigning ``resultType`` / ``ttlMs`` / ``cacheScope`` onto a result
   instance lands in ``__pydantic_extra__`` and serializes through to the wire
   with no serializer warnings.
2. Handlers return a ``ServerResult``, which is a pydantic ``RootModel`` union
   wrapper. The concrete result (``ListToolsResult``, ``CallToolResult``, ...)
   lives on ``.root`` and is held **by reference**, so mutating it in place is
   visible in the response without rebuilding the wrapper.

Everything here is additive: 2025-11-25 clients ignore the unknown fields.
"""

import inspect
from typing import Any, Callable, Dict, Optional, Tuple

from prometheus_mcp_server.logging_config import get_logger

logger = get_logger()

try:
    from prometheus_mcp_server.spec2026.constants import (
        INVALID_PARAMS,
        LEGACY_RESOURCE_NOT_FOUND,
        META_SERVER_INFO,
        RESULT_TYPE_COMPLETE,
    )
except Exception:  # pragma: no cover - constants.py is present; this is a safety net
    # Values are fixed by the spec, so this module stays correct on its own even
    # if constants.py is ever renamed or refactored. Never crash at import time.
    INVALID_PARAMS = -32602
    LEGACY_RESOURCE_NOT_FOUND = -32002
    META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"
    RESULT_TYPE_COMPLETE = "complete"

# Defaults per the design doc. 300000 ms mirrors the existing 300 s
# ``_CACHE_TTL`` in server.py, but is deliberately a separate symbol so the two
# can diverge without breaking the tests that pin ``_CACHE_TTL == 300``.
DEFAULT_CACHE_TTL_MS = 300000
DEFAULT_CACHE_SCOPE = "public"

VALID_CACHE_SCOPES = ("public", "private")

# Methods whose results may carry ``ttlMs``/``cacheScope``. Notably absent:
# ``tools/call``, whose result depends on arguments and live Prometheus state
# and must never be cached by the client.
CACHEABLE_METHODS = frozenset(
    {
        "server/discover",
        "tools/list",
        "prompts/list",
        "resources/list",
        "resources/templates/list",
        "resources/read",
    }
)

# Result collections that must be returned in a deterministic order (F3).
SORTABLE_COLLECTIONS = ("tools", "prompts", "resources", "resourceTemplates")

# Methods whose "not found" failure moved from -32002 to -32602 in 2026-07-28
# (design feature F1). The SDK still raises the 2025-11-25 code.
RESOURCE_NOT_FOUND_METHODS = frozenset({"resources/read"})

# Attribute stamped on a wrapper holding the handler it wrapped. Presence marks
# a handler as already enriched, which makes install_envelope idempotent.
_WRAPPER_MARKER = "_spec2026_envelope_inner"


def _coerce_ttl_ms(value: Any) -> int:
    """Validate a ttlMs setting, falling back to the default when unusable.

    Args:
        value: Candidate time-to-live in milliseconds.

    Returns:
        A non-negative integer suitable for the ``ttlMs`` envelope field.
    """
    # bool is a subclass of int; True would otherwise sail through as ttlMs=1.
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        logger.warning(
            "Invalid cache TTL for result envelope, using default",
            ttl_ms=repr(value),
            default_ttl_ms=DEFAULT_CACHE_TTL_MS,
        )
        return DEFAULT_CACHE_TTL_MS
    return value


def _coerce_cache_scope(value: Any) -> str:
    """Validate a cacheScope setting, falling back to the default when unusable.

    Args:
        value: Candidate cache scope.

    Returns:
        Either ``"public"`` or ``"private"``.
    """
    if value in VALID_CACHE_SCOPES:
        return value
    logger.warning(
        "Invalid cache scope for result envelope, using default",
        cache_scope=repr(value),
        default_cache_scope=DEFAULT_CACHE_SCOPE,
    )
    return DEFAULT_CACHE_SCOPE


def _request_method(request_type: Any) -> Optional[str]:
    """Read the JSON-RPC method string off an SDK request model class.

    Every ``mcp.types`` request declares ``method: Literal["..."] = "..."``, so
    the default of that field is the method name. A future SDK could change
    that shape, hence the defensive read.

    Args:
        request_type: The request model class used as a request_handlers key.

    Returns:
        The method string, or None if it could not be determined.
    """
    try:
        field = request_type.model_fields["method"]
    except Exception:
        return None
    default = getattr(field, "default", None)
    return default if isinstance(default, str) else None


def _unwrap_result(result: Any) -> Any:
    """Reach through a ``ServerResult`` RootModel to the concrete result.

    Args:
        result: A ``ServerResult`` wrapper or an already-unwrapped result.

    Returns:
        The concrete result model that carries the ``meta`` field.
    """
    root = getattr(result, "root", None)
    return result if root is None else root


def _sort_key(item: Any) -> Tuple[str, str]:
    """Build a total, exception-free ordering key for a listable item.

    Name is the primary key (spec minor change #3); the URI (or URI template)
    breaks ties so ordering stays stable even for same-named entries.

    Args:
        item: A Tool, Prompt, Resource or ResourceTemplate model.

    Returns:
        A (name, uri) tuple of strings.
    """
    name = getattr(item, "name", None)
    uri = getattr(item, "uri", None)
    if uri is None:
        uri = getattr(item, "uriTemplate", None)
    return (str(name or ""), str(uri or ""))


def _sort_collections(result: Any) -> None:
    """Sort every known listable collection on a result in place.

    Args:
        result: The concrete result model to reorder.
    """
    for attribute in SORTABLE_COLLECTIONS:
        items = getattr(result, attribute, None)
        if isinstance(items, list) and len(items) > 1:
            setattr(result, attribute, sorted(items, key=_sort_key))


def _merge_server_info(result: Any, server_name: str, server_version: str) -> None:
    """Merge serverInfo into a result's ``_meta`` without clobbering it.

    Existing ``_meta`` keys are preserved, including an existing serverInfo
    entry -- a handler that deliberately set one (the discover payload, for
    instance) stays authoritative.

    Args:
        result: The concrete result model to enrich.
        server_name: Server name to advertise.
        server_version: Server version to advertise.
    """
    existing = getattr(result, "meta", None)
    if existing is None:
        meta: Dict[str, Any] = {}
    elif isinstance(existing, dict):
        meta = dict(existing)
    else:
        logger.warning(
            "Result _meta is not a mapping, skipping serverInfo merge",
            meta_type=type(existing).__name__,
        )
        return

    meta.setdefault(META_SERVER_INFO, {"name": server_name, "version": server_version})
    result.meta = meta


def _is_unset(result: Any, field: str) -> bool:
    """Return True when an envelope field is absent from a result.

    Args:
        result: The concrete result model.
        field: Envelope field name.

    Returns:
        True if the field is missing or None.
    """
    return getattr(result, field, None) is None


def enrich_result(
    result: Any,
    *,
    method: Optional[str],
    server_name: str,
    server_version: str,
    ttl_ms: int = DEFAULT_CACHE_TTL_MS,
    cache_scope: str = DEFAULT_CACHE_SCOPE,
) -> Any:
    """Apply the 2026-07-28 envelope to a result, in place.

    Adds ``resultType`` and ``_meta`` serverInfo to every result, plus
    ``ttlMs``/``cacheScope`` when the method is in ``CACHEABLE_METHODS``, and
    sorts any listable collection by name.

    Fields that are already set are left alone, so the function is safe to
    apply twice and a handler that produced its own envelope (for example a
    non-``complete`` ``resultType``) keeps its values.

    Args:
        result: A ``ServerResult`` wrapper or a concrete result model.
        method: The JSON-RPC method that produced the result, or None if
            unknown (unknown methods are treated as non-cacheable).
        server_name: Server name advertised in ``_meta`` serverInfo.
        server_version: Server version advertised in ``_meta`` serverInfo.
        ttl_ms: Cache lifetime in milliseconds for cacheable methods. Validated
            here rather than trusted, because this function is public API and a
            caller may pass an operator-supplied value straight through.
        cache_scope: ``"public"`` or ``"private"`` for cacheable methods. Also
            validated; anything else (including None) falls back to the default.

    Returns:
        The same object that was passed in, for convenience.
    """
    inner = _unwrap_result(result)

    if _is_unset(inner, "resultType"):
        inner.resultType = RESULT_TYPE_COMPLETE

    if method in CACHEABLE_METHODS:
        # Never emit a spec-invalid envelope: ttlMs MUST be an integer >= 0 and
        # cacheScope MUST be "public" or "private".
        if _is_unset(inner, "ttlMs"):
            inner.ttlMs = _coerce_ttl_ms(ttl_ms)
        if _is_unset(inner, "cacheScope"):
            inner.cacheScope = _coerce_cache_scope(cache_scope)

    _merge_server_info(inner, server_name, server_version)
    _sort_collections(inner)
    return result


def _retype_error(error: Exception, method: Optional[str]) -> Exception:
    """Rewrite a handler failure to the 2026-07-28 JSON-RPC error code (F1).

    Only one code changed: ``resources/read`` reports a missing resource with
    ``INVALID_PARAMS`` (-32602) instead of the 2025-11-25 ``-32002``. Anything
    else is returned untouched.

    Args:
        error: The exception the wrapped handler raised.
        method: The JSON-RPC method the handler serves.

    Returns:
        A replacement exception, or ``error`` itself when nothing changed.
    """
    if method not in RESOURCE_NOT_FOUND_METHODS:
        return error

    data = getattr(error, "error", None)
    if getattr(data, "code", None) != LEGACY_RESOURCE_NOT_FOUND:
        return error

    try:
        from mcp.shared.exceptions import McpError
        from mcp.types import ErrorData

        if not isinstance(error, McpError):
            return error
        return McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message=data.message,
                data=getattr(data, "data", None),
            )
        )
    except Exception as e:  # pragma: no cover - depends on SDK layout
        logger.warning(
            "Could not retype the resource-not-found error code",
            method=method,
            error=str(e),
            error_type=type(e).__name__,
        )
        return error


def _wrap_handler(
    handler: Callable[..., Any],
    *,
    method: Optional[str],
    server_name: str,
    server_version: str,
    ttl_ms: int,
    cache_scope: str,
) -> Callable[..., Any]:
    """Build an envelope-applying wrapper around a low-level request handler.

    Args:
        handler: The original ``async (req) -> ServerResult`` handler.
        method: The JSON-RPC method this handler serves, or None if unknown.
        server_name: Server name advertised in ``_meta`` serverInfo.
        server_version: Server version advertised in ``_meta`` serverInfo.
        ttl_ms: Cache lifetime in milliseconds for cacheable methods.
        cache_scope: ``"public"`` or ``"private"`` for cacheable methods.

    Returns:
        A coroutine function stamped with the wrapper marker.
    """

    async def envelope_handler(req: Any = None) -> Any:
        # The SDK calls the tools/list handler with req=None when refreshing its
        # internal tool cache, so req is never dereferenced here.
        try:
            result = handler(req)
            if inspect.isawaitable(result):
                result = await result
        except Exception as e:
            retyped = _retype_error(e, method)
            if retyped is e:
                raise
            raise retyped from e
        try:
            enrich_result(
                result,
                method=method,
                server_name=server_name,
                server_version=server_version,
                ttl_ms=ttl_ms,
                cache_scope=cache_scope,
            )
        except Exception as e:
            # A malformed envelope must never cost the client its response.
            logger.warning(
                "Failed to apply result envelope",
                method=method,
                error=str(e),
                error_type=type(e).__name__,
            )
        return result

    setattr(envelope_handler, _WRAPPER_MARKER, handler)
    return envelope_handler


def install_envelope(
    mcp: Any,
    *,
    server_name: str,
    server_version: str,
    ttl_ms: int = DEFAULT_CACHE_TTL_MS,
    cache_scope: str = DEFAULT_CACHE_SCOPE,
) -> bool:
    """Wrap a FastMCP server's request handlers with envelope enrichment.

    Idempotent: an already-wrapped handler is unwrapped and re-wrapped rather
    than nested, so repeated calls neither stack wrappers nor leave stale
    settings behind. Calling it again after new handlers are registered (the
    ``server/discover`` handler, for instance) picks those up too.

    Fully defensive: any unexpected SDK shape is logged and the server is left
    exactly as it was. This function never raises.

    Args:
        mcp: The FastMCP server instance.
        server_name: Server name advertised in ``_meta`` serverInfo.
        server_version: Server version advertised in ``_meta`` serverInfo.
        ttl_ms: Cache lifetime in milliseconds; must be an int >= 0.
        cache_scope: ``"public"`` or ``"private"``. Defaults to ``"public"``
            because every list result on this server is identical for all
            users -- tool, prompt and resource listings are static and carry no
            per-tenant data, so a shared cache is both safe and the more useful
            choice. Deployments that put per-tenant data behind auth should set
            ``"private"`` via configuration.

    Returns:
        True if at least one handler was wrapped, False if the layer degraded.
    """
    try:
        ttl_ms = _coerce_ttl_ms(ttl_ms)
        cache_scope = _coerce_cache_scope(cache_scope)

        handlers = _get_request_handlers(mcp)
        if handlers is None:
            return False

        wrapped_count = 0
        for request_type in list(handlers.keys()):
            try:
                handler = handlers[request_type]
                # Unwrap first so a re-install replaces rather than nests.
                inner = getattr(handler, _WRAPPER_MARKER, None)
                if inner is None:
                    inner = handler
                if not callable(inner):
                    logger.warning(
                        "Skipping non-callable request handler",
                        request_type=getattr(request_type, "__name__", repr(request_type)),
                    )
                    continue

                method = _request_method(request_type)
                if method is None:
                    logger.debug(
                        "Could not determine method for request handler; treating as non-cacheable",
                        request_type=getattr(request_type, "__name__", repr(request_type)),
                    )

                handlers[request_type] = _wrap_handler(
                    inner,
                    method=method,
                    server_name=server_name,
                    server_version=server_version,
                    ttl_ms=ttl_ms,
                    cache_scope=cache_scope,
                )
                wrapped_count += 1
            except Exception as e:
                logger.warning(
                    "Failed to wrap request handler for result envelope",
                    request_type=getattr(request_type, "__name__", repr(request_type)),
                    error=str(e),
                    error_type=type(e).__name__,
                )

        if wrapped_count == 0:
            logger.warning("Result envelope wrapped no request handlers; layer inactive")
            return False

        logger.info(
            "MCP 2026-07-28 result envelope installed",
            handler_count=wrapped_count,
            ttl_ms=ttl_ms,
            cache_scope=cache_scope,
            server_name=server_name,
            server_version=server_version,
        )
        return True

    except Exception as e:
        logger.warning(
            "Result envelope unavailable; server continues without it",
            error=str(e),
            error_type=type(e).__name__,
        )
        return False


def uninstall_envelope(mcp: Any) -> bool:
    """Restore the original request handlers, removing envelope enrichment.

    Primarily useful for tests, which share a module-level FastMCP singleton
    and must not leak the wrappers into unrelated test modules. Like
    ``install_envelope``, this never raises.

    Args:
        mcp: The FastMCP server instance.

    Returns:
        True if at least one handler was restored, False otherwise.
    """
    try:
        handlers = _get_request_handlers(mcp)
        if handlers is None:
            return False

        restored_count = 0
        for request_type in list(handlers.keys()):
            inner = getattr(handlers[request_type], _WRAPPER_MARKER, None)
            if inner is not None:
                handlers[request_type] = inner
                restored_count += 1

        if restored_count:
            logger.info("MCP 2026-07-28 result envelope removed", handler_count=restored_count)
        return restored_count > 0

    except Exception as e:
        logger.warning(
            "Failed to remove result envelope",
            error=str(e),
            error_type=type(e).__name__,
        )
        return False


# Attribute stamped on the patched ServerSession._received_request holding the
# method it replaced. Presence marks the patch as already applied.
_INIT_WRAPPER_MARKER = "_spec2026_initialize_envelope_inner"


def install_initialize_envelope(*, server_name: str, server_version: str) -> bool:
    """Extend the result envelope to cover the ``initialize`` handshake (F2).

    ``InitializeRequest`` never reaches ``request_handlers``: the SDK answers it
    inside ``ServerSession._received_request``, so :func:`install_envelope`
    cannot see it and a 2026-07-28 client would receive at least one
    envelope-less result on every connection. This wraps that one method and
    enriches the ``InitializeResult`` on its way out.

    Idempotent and fully defensive: a second call is a no-op and any unexpected
    SDK shape is logged, leaving the session class untouched.

    Note that ``ServerSession`` is a process-global class, so this patch covers
    every MCP server running in the process, not just one FastMCP instance. That
    is correct for this package (a single server per process) and is why
    :func:`uninstall_initialize_envelope` exists.

    Args:
        server_name: Server name advertised in ``_meta`` serverInfo.
        server_version: Server version advertised in ``_meta`` serverInfo.

    Returns:
        True when the handshake is now enveloped, False if the layer degraded.
    """
    try:
        import mcp.types as mcp_types
        from mcp.server.session import ServerSession

        original = ServerSession._received_request
        if getattr(original, _INIT_WRAPPER_MARKER, None) is not None:
            return True

        async def received_request(self: Any, responder: Any) -> Any:
            """Enrich the InitializeResult before the responder sends it."""
            root = getattr(getattr(responder, "request", None), "root", None)
            if isinstance(root, mcp_types.InitializeRequest):
                inner_respond = responder.respond

                async def respond(response: Any) -> Any:
                    """Apply the envelope, then hand the result to the SDK."""
                    try:
                        enrich_result(
                            response,
                            method="initialize",
                            server_name=server_name,
                            server_version=server_version,
                        )
                    except Exception as e:
                        logger.warning(
                            "Failed to apply result envelope to the initialize handshake",
                            error=str(e),
                            error_type=type(e).__name__,
                        )
                    return await inner_respond(response)

                responder.respond = respond
            return await original(self, responder)

        setattr(received_request, _INIT_WRAPPER_MARKER, original)
        ServerSession._received_request = received_request
        logger.info(
            "MCP 2026-07-28 initialize envelope installed",
            server_name=server_name,
            server_version=server_version,
        )
        return True

    except Exception as e:
        logger.warning(
            "Initialize envelope unavailable; the handshake result stays un-enveloped",
            error=str(e),
            error_type=type(e).__name__,
        )
        return False


def uninstall_initialize_envelope() -> bool:
    """Restore the SDK's own ``ServerSession._received_request``.

    Mainly for tests, which share a process with every other test module and
    must be able to observe the un-enveloped handshake. Never raises.

    Returns:
        True when a patch was removed, False when there was nothing to remove.
    """
    try:
        from mcp.server.session import ServerSession

        original = getattr(ServerSession._received_request, _INIT_WRAPPER_MARKER, None)
        if original is None:
            return False
        ServerSession._received_request = original
        logger.info("MCP 2026-07-28 initialize envelope removed")
        return True
    except Exception as e:  # pragma: no cover - requires an incompatible SDK
        logger.warning(
            "Failed to remove the initialize envelope",
            error=str(e),
            error_type=type(e).__name__,
        )
        return False


def _get_request_handlers(mcp: Any) -> Optional[Dict[Any, Callable[..., Any]]]:
    """Fetch the low-level server's request_handlers dict, defensively.

    Args:
        mcp: The FastMCP server instance.

    Returns:
        The mutable handler dict, or None if the SDK's shape was unexpected.
    """
    low_level = getattr(mcp, "_mcp_server", None)
    if low_level is None:
        logger.warning(
            "FastMCP instance exposes no _mcp_server; result envelope unavailable",
            mcp_type=type(mcp).__name__,
        )
        return None

    handlers = getattr(low_level, "request_handlers", None)
    if not isinstance(handlers, dict):
        logger.warning(
            "Low-level server exposes no request_handlers dict; result envelope unavailable",
            handlers_type=type(handlers).__name__,
        )
        return None

    return handlers
