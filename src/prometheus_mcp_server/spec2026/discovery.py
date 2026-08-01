#!/usr/bin/env python
"""``server/discover`` support for the MCP 2026-07-28 compatibility layer (F4 + F8).

The installed SDK (``mcp``) implements protocol ``2025-11-25`` and therefore has no
notion of the ``server/discover`` method: ``mcp.types.ClientRequest`` is a pydantic
``RootModel`` over a closed union of request models, and any method not present in that
union is rejected by ``BaseSession._receive_loop`` with ``-32602`` long before the
low-level server's dispatch table is consulted. There is no unknown-method hook.

Real JSON-RPC dispatch **is** nonetheless achievable without forking the SDK, and it
works on every transport (stdio, Streamable HTTP stateful/stateless, SSE, in-memory).
The union is a plain ``A | B | C`` ``types.UnionType`` rather than a discriminated
union, so appending a member in place and forcing a model rebuild makes pydantic's
smart-union resolve ``{"method": "server/discover"}`` to our request model, while every
existing method still resolves to exactly the class it did before. ``mcp.types.
ServerResult`` is extended the same way so the response serializes without pydantic
emitting ``PydanticSerializationUnexpectedValue`` warnings on every reply.

Three delivery paths are registered, all fed by a *single* payload factory so they can
never drift:

1. ``server/discover`` as a real JSON-RPC method (all transports).
2. ``GET /server/discover`` as a plain HTTP route (mirrors the existing ``/health``
   route), which is also the only pre-handshake answer for HTTP clients.
3. An MCP tool, so the payload stays reachable even if path 1 degrades.

Known limitation: JSON-RPC ``server/discover`` still requires the ``initialize``
handshake first, because ``ServerSession._received_request`` rejects any non-
``initialize``/non-``ping`` request before initialization. Removing that gate would
require a transport rewrite, which the design doc lists as a non-goal. Path 2 covers
the pre-initialization case for HTTP clients.

Forward compatibility: if a future SDK ships its own ``server/discover`` request model,
the union is left untouched and the handler is keyed on the SDK's own class instead.
Naively appending a second member with the same ``method`` literal would let smart-union
pick the SDK's class, so our handler key would never match and the method would silently
degrade to ``METHOD_NOT_FOUND``.

Every piece of SDK introspection here is wrapped: an incompatible SDK degrades to a
logged warning and a partially (or fully) unregistered layer, never an import-time crash.
"""

import copy
import importlib
from typing import (
    Any,
    Callable,
    Dict,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    get_args,
    get_origin,
)

from prometheus_mcp_server.logging_config import get_logger

logger = get_logger()

# JSON-RPC method name defined by the 2026-07-28 specification.
DISCOVER_METHOD = "server/discover"

# Base name of the MCP tool mirroring the payload. Run through the caller-supplied
# namer (see server.py's ``_tool_name``) so TOOL_PREFIX is honored.
DISCOVER_TOOL_BASE_NAME = "server_discover"

# Default HTTP route, alongside the existing ``/health`` route.
DISCOVER_ROUTE_PATH = "/server/discover"

# Attribute stamped on the FastMCP instance to make ``install_discovery`` idempotent.
_INSTALLED_ATTR = "_spec2026_discovery_status"

# Discovery results are long-lived; the design doc's reference payload uses one hour.
DEFAULT_DISCOVER_TTL_MS = 3600000

# The payload is identical for every caller, so "public" is the correct default. Callers
# that front the server with per-user auth can pass cache_scope="private" instead.
DEFAULT_CACHE_SCOPE = "public"
_VALID_CACHE_SCOPES = ("public", "private")


def _load_constants() -> Any:
    """Import the shared spec2026 constants module, tolerating its absence."""
    try:
        return importlib.import_module("prometheus_mcp_server.spec2026.constants")
    except Exception:
        return None


_constants = _load_constants()


def _const(name: str, default: Any) -> Any:
    """Read a spec2026 constant, falling back to a local literal from the design doc.

    Args:
        name: Attribute name on the constants module.
        default: Value to use when the constants module is missing the attribute.

    Returns:
        The shared constant when available, otherwise the supplied default.
    """
    if _constants is None:
        return default
    return getattr(_constants, name, default)


PROTOCOL_VERSION_2026: str = _const("PROTOCOL_VERSION_2026", "2026-07-28")
SUPPORTED_PROTOCOL_VERSIONS: Tuple[str, ...] = tuple(
    _const("SUPPORTED_PROTOCOL_VERSIONS", ("2026-07-28", "2025-11-25", "2025-06-18"))
)
META_SERVER_INFO: str = _const("META_SERVER_INFO", "io.modelcontextprotocol/serverInfo")
RESULT_TYPE_COMPLETE: str = _const("RESULT_TYPE_COMPLETE", "complete")

# Keys the 2026-07-28 discovery result is required to carry.
DISCOVER_REQUIRED_KEYS: Tuple[str, ...] = (
    "resultType",
    "supportedVersions",
    "capabilities",
    "instructions",
    "ttlMs",
    "cacheScope",
    "_meta",
)


# --------------------------------------------------------------------------------------
# F4 / F8 — the payload
# --------------------------------------------------------------------------------------

def default_instructions(server_name: str) -> str:
    """Build the default human-readable ``instructions`` string.

    Args:
        server_name: Display name of the MCP server.

    Returns:
        A sentence describing what the server offers.
    """
    return (
        f"{server_name} is a read-only Model Context Protocol server for Prometheus. "
        "Call tools/list to enumerate the available tools, then use them to run PromQL "
        "instant and range queries, list metrics and their metadata, inspect scrape "
        "targets, and check server health. All tools are read-only and idempotent."
    )


def build_discover_payload(
    server_name: str,
    server_version: str,
    *,
    supported_versions: Optional[Sequence[str]] = None,
    capabilities: Optional[Mapping[str, Any]] = None,
    extensions: Optional[Mapping[str, Any]] = None,
    instructions: Optional[str] = None,
    ttl_ms: int = DEFAULT_DISCOVER_TTL_MS,
    cache_scope: str = DEFAULT_CACHE_SCOPE,
) -> Dict[str, Any]:
    """Build the ``server/discover`` result payload.

    Pure and dependency-free: every input arrives as an argument, nothing is read from
    module or process globals, and a freshly owned dict is returned on every call so
    callers may mutate the result without affecting later ones.

    Args:
        server_name: Display name advertised in ``_meta`` serverInfo.
        server_version: Version string advertised in ``_meta`` serverInfo.
        supported_versions: Protocol versions to advertise, newest first. Defaults to
            SUPPORTED_PROTOCOL_VERSIONS.
        capabilities: Extra server capabilities merged over the ``{"tools": {}}`` base.
        extensions: Value for the ``extensions`` capability (F8). The key is always
            present in the output, defaulting to an empty object.
        instructions: Human-readable usage guidance. Defaults to default_instructions().
        ttl_ms: Cache lifetime in milliseconds. Must be a non-negative int;
            anything else (including a bool or a float) falls back to
            DEFAULT_DISCOVER_TTL_MS with a warning.
        cache_scope: Either "public" or "private". Anything else falls back to
            DEFAULT_CACHE_SCOPE.

    Returns:
        Dict carrying every key in DISCOVER_REQUIRED_KEYS.
    """
    resolved_capabilities: Dict[str, Any] = {"tools": {}}
    if capabilities:
        resolved_capabilities.update(copy.deepcopy(dict(capabilities)))
    if extensions is not None:
        resolved_capabilities["extensions"] = copy.deepcopy(dict(extensions))
    # F8: the extensions capability is advertised unconditionally.
    resolved_capabilities.setdefault("extensions", {})

    # Deliberately identical to envelope._coerce_ttl_ms: a bare int() would
    # truncate a float and let bool through as 0/1 (bool subclasses int), so the
    # same misconfiguration would produce a different ttlMs in the discovery
    # document than in the result envelope.
    if isinstance(ttl_ms, bool) or not isinstance(ttl_ms, int) or ttl_ms < 0:
        logger.warning(
            "Invalid discover ttlMs, falling back to default",
            ttl_ms=repr(ttl_ms),
            default_ttl_ms=DEFAULT_DISCOVER_TTL_MS,
        )
        resolved_ttl_ms = DEFAULT_DISCOVER_TTL_MS
    else:
        resolved_ttl_ms = ttl_ms

    resolved_cache_scope = cache_scope
    if resolved_cache_scope not in _VALID_CACHE_SCOPES:
        logger.warning(
            "Invalid discover cacheScope, falling back to default",
            cache_scope=repr(cache_scope),
            default_cache_scope=DEFAULT_CACHE_SCOPE,
        )
        resolved_cache_scope = DEFAULT_CACHE_SCOPE

    resolved_versions = list(
        supported_versions if supported_versions is not None else SUPPORTED_PROTOCOL_VERSIONS
    )

    return {
        "resultType": RESULT_TYPE_COMPLETE,
        "supportedVersions": resolved_versions,
        "capabilities": resolved_capabilities,
        "instructions": instructions if instructions is not None else default_instructions(server_name),
        "ttlMs": resolved_ttl_ms,
        "cacheScope": resolved_cache_scope,
        "_meta": {META_SERVER_INFO: {"name": server_name, "version": server_version}},
    }


# --------------------------------------------------------------------------------------
# SDK models — defined defensively so an incompatible SDK cannot break import
# --------------------------------------------------------------------------------------

def _define_models() -> Tuple[type, type]:
    """Define the request/result pydantic models mirroring the SDK's own shapes.

    Returns:
        Tuple of (DiscoverRequest, DiscoverResult) model classes.
    """
    from mcp.types import Request, RequestParams, Result
    from pydantic import ConfigDict

    class DiscoverRequestParams(RequestParams):
        """Parameters for ``server/discover``; the spec defines none, so all are extra."""

        model_config = ConfigDict(extra="allow")

    class DiscoverRequest(Request[Optional[DiscoverRequestParams], Literal["server/discover"]]):
        """``server/discover`` JSON-RPC request.

        ``params`` must exist (may be None): ``RequestResponder.__init__`` dereferences
        ``validated_request.root.params`` unconditionally.
        """

        method: Literal["server/discover"] = "server/discover"
        params: Optional[DiscoverRequestParams] = None

    class DiscoverResult(Result):
        """``server/discover`` JSON-RPC result; all spec fields ride as extras."""

        model_config = ConfigDict(extra="allow")

    return DiscoverRequest, DiscoverResult


DiscoverRequest: Optional[type] = None
DiscoverResult: Optional[type] = None

try:
    DiscoverRequest, DiscoverResult = _define_models()
except Exception as exc:  # pragma: no cover - requires an incompatible SDK
    logger.warning(
        "Unable to define server/discover models; JSON-RPC dispatch disabled",
        error=str(exc),
        error_type=type(exc).__name__,
    )


# --------------------------------------------------------------------------------------
# Union surgery helpers
# --------------------------------------------------------------------------------------

def _union_members(root_model: Any) -> Tuple[Any, ...]:
    """Return the current members of a pydantic RootModel's union annotation."""
    annotation = root_model.model_fields["root"].annotation
    return tuple(get_args(annotation))


def _find_member_by_method(root_model: Any, method: str) -> Optional[type]:
    """Find an existing union member already declaring the given JSON-RPC method.

    Args:
        root_model: A pydantic RootModel such as ``mcp.types.ClientRequest``.
        method: JSON-RPC method name to look for.

    Returns:
        The matching member class, or None when the SDK does not know the method.
    """
    for member in _union_members(root_model):
        model_fields = getattr(member, "model_fields", None)
        if not model_fields:
            continue
        annotation = getattr(model_fields.get("method"), "annotation", None)
        if get_origin(annotation) is Literal and method in get_args(annotation):
            return member
    return None


def _extend_union(root_model: Any, member: type) -> None:
    """Append a member to a RootModel's union annotation, rolling back on failure.

    Args:
        root_model: A pydantic RootModel such as ``mcp.types.ClientRequest``.
        member: Model class to add to the union.

    Returns:
        None.
    """
    field = root_model.model_fields["root"]
    original = field.annotation
    field.annotation = original | member
    try:
        root_model.model_rebuild(force=True)
    except Exception:
        # Never leave the SDK's types half-mutated.
        field.annotation = original
        try:
            root_model.model_rebuild(force=True)
        except Exception:  # pragma: no cover - rebuild of the pristine union cannot fail
            logger.error("Failed to restore original union annotation", model=getattr(root_model, "__name__", "?"))
        raise


# --------------------------------------------------------------------------------------
# Delivery paths
# --------------------------------------------------------------------------------------

def make_discover_endpoint(payload_factory: Callable[[], Dict[str, Any]]) -> Callable[..., Any]:
    """Build the ``GET /server/discover`` Starlette endpoint.

    Args:
        payload_factory: Zero-argument callable returning the discover payload.

    Returns:
        An async endpoint returning a JSONResponse, suitable for ``mcp.custom_route``.
    """
    from starlette.requests import Request as StarletteRequest
    from starlette.responses import JSONResponse

    async def discover_endpoint(request: StarletteRequest) -> JSONResponse:
        """Serve the server/discover payload over plain HTTP."""
        return JSONResponse(payload_factory())

    return discover_endpoint


def make_discover_tool(payload_factory: Callable[[], Dict[str, Any]]) -> Callable[..., Any]:
    """Build the MCP tool function exposing the discover payload.

    Args:
        payload_factory: Zero-argument callable returning the discover payload.

    Returns:
        An async, zero-argument coroutine function returning the payload dict.
    """

    async def server_discover() -> Dict[str, Any]:
        """Return this server's MCP 2026-07-28 discovery document.

        Returns:
            Supported protocol versions, capabilities, instructions and cache hints
        """
        return payload_factory()

    return server_discover


def _install_jsonrpc(mcp: Any, payload_factory: Callable[[], Dict[str, Any]]) -> bool:
    """Register ``server/discover`` as a real JSON-RPC method on the low-level server.

    Args:
        mcp: The FastMCP instance.
        payload_factory: Zero-argument callable returning the discover payload.

    Returns:
        True when the handler was registered, False when the SDK shape was unusable.
    """
    if DiscoverRequest is None or DiscoverResult is None:
        logger.warning("server/discover JSON-RPC dispatch unavailable; models undefined")
        return False

    try:
        import mcp.types as mcp_types

        low_level = getattr(mcp, "_mcp_server", None)
        handlers = getattr(low_level, "request_handlers", None)
        if not isinstance(handlers, dict):
            logger.warning(
                "server/discover JSON-RPC dispatch unavailable; request_handlers missing",
                low_level_type=type(low_level).__name__,
            )
            return False

        # Forward compatibility: if the SDK already knows the method, key the handler on
        # the SDK's own class instead of adding a competing union member.
        request_key = _find_member_by_method(mcp_types.ClientRequest, DISCOVER_METHOD)
        native = request_key is not None
        if request_key is None:
            _extend_union(mcp_types.ClientRequest, DiscoverRequest)
            request_key = DiscoverRequest

        if DiscoverResult not in _union_members(mcp_types.ServerResult):
            _extend_union(mcp_types.ServerResult, DiscoverResult)

        async def discover_handler(request: Any) -> Any:
            """Handle a ``server/discover`` JSON-RPC request."""
            return mcp_types.ServerResult(DiscoverResult.model_validate(payload_factory()))

        handlers[request_key] = discover_handler
        logger.info(
            "Registered server/discover JSON-RPC handler",
            handler_key=getattr(request_key, "__name__", str(request_key)),
            sdk_native=native,
        )
        return True

    except Exception as exc:
        logger.warning(
            "server/discover JSON-RPC dispatch unavailable; falling back to HTTP route and MCP tool",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return False


def _install_http_route(
    mcp: Any,
    payload_factory: Callable[[], Dict[str, Any]],
    route_path: str,
) -> bool:
    """Register the plain ``GET /server/discover`` HTTP route.

    Args:
        mcp: The FastMCP instance.
        payload_factory: Zero-argument callable returning the discover payload.
        route_path: Path to serve the payload from.

    Returns:
        True when the route was registered, False otherwise.
    """
    try:
        mcp.custom_route(route_path, methods=["GET"])(make_discover_endpoint(payload_factory))
        logger.info("Registered server/discover HTTP route", path=route_path)
        return True
    except Exception as exc:
        logger.warning(
            "server/discover HTTP route unavailable",
            path=route_path,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return False


def _install_tool(
    mcp: Any,
    payload_factory: Callable[[], Dict[str, Any]],
    tool_namer: Callable[[str], str],
    tool_base_name: str,
) -> bool:
    """Register the MCP tool mirroring the discover payload.

    Args:
        mcp: The FastMCP instance.
        payload_factory: Zero-argument callable returning the discover payload.
        tool_namer: Callable applying the server's TOOL_PREFIX convention to a base name.
        tool_base_name: Unprefixed tool name.

    Returns:
        True when the tool was registered, False otherwise.
    """
    tool_name = tool_base_name
    try:
        tool_name = tool_namer(tool_base_name)
        mcp.tool(
            name=tool_name,
            description=(
                "Return this server's MCP 2026-07-28 discovery document, listing the "
                "supported protocol versions, advertised capabilities, and cache hints"
            ),
            annotations={
                "title": "Server Discovery",
                "icon": "🧭",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
        )(make_discover_tool(payload_factory))
        logger.info("Registered server/discover MCP tool", tool_name=tool_name)
        return True
    except Exception as exc:
        logger.warning(
            "server/discover MCP tool unavailable",
            tool_name=tool_name,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return False


def install_discovery(
    mcp: Any,
    server_name: str,
    server_version: str,
    *,
    tool_namer: Optional[Callable[[str], str]] = None,
    supported_versions: Optional[Sequence[str]] = None,
    capabilities: Optional[Mapping[str, Any]] = None,
    extensions: Optional[Mapping[str, Any]] = None,
    instructions: Optional[str] = None,
    ttl_ms: int = DEFAULT_DISCOVER_TTL_MS,
    cache_scope: str = DEFAULT_CACHE_SCOPE,
    tool_base_name: str = DISCOVER_TOOL_BASE_NAME,
    route_path: str = DISCOVER_ROUTE_PATH,
    payload_factory: Optional[Callable[[], Dict[str, Any]]] = None,
) -> Dict[str, bool]:
    """Install every ``server/discover`` delivery path on a FastMCP instance.

    Idempotent: the resulting status is stamped on the FastMCP instance and returned
    unchanged on subsequent calls, so a double import cannot register duplicate routes
    or tools. Defensive: each path is installed independently inside its own guard, so
    an SDK change degrades to a logged warning and a partial install rather than an
    exception.

    Args:
        mcp: The FastMCP instance to install onto.
        server_name: Display name advertised in ``_meta`` serverInfo.
        server_version: Version string advertised in ``_meta`` serverInfo.
        tool_namer: Callable applying the server's TOOL_PREFIX convention (server.py's
            ``_tool_name``). Passed in rather than imported to avoid a circular import.
            Defaults to using the base name unchanged.
        supported_versions: Protocol versions to advertise. See build_discover_payload.
        capabilities: Extra capabilities merged over the ``{"tools": {}}`` base.
        extensions: Value for the ``extensions`` capability (F8).
        instructions: Human-readable usage guidance.
        ttl_ms: Cache lifetime in milliseconds for the discovery result.
        cache_scope: Either "public" or "private".
        tool_base_name: Unprefixed name for the MCP tool.
        route_path: Path for the plain HTTP route.
        payload_factory: Escape hatch overriding all payload arguments with a custom
            zero-argument builder. All three delivery paths share it so they cannot drift.

    Returns:
        Mapping of delivery path name ("jsonrpc", "http_route", "tool") to whether that
        path was successfully registered.
    """
    existing = getattr(mcp, _INSTALLED_ATTR, None)
    if isinstance(existing, dict):
        logger.debug("server/discover already installed; skipping", status=existing)
        return existing

    if payload_factory is None:

        def payload_factory() -> Dict[str, Any]:
            """Build a fresh discover payload from the configured arguments."""
            return build_discover_payload(
                server_name,
                server_version,
                supported_versions=supported_versions,
                capabilities=capabilities,
                extensions=extensions,
                instructions=instructions,
                ttl_ms=ttl_ms,
                cache_scope=cache_scope,
            )

    status = {
        "jsonrpc": _install_jsonrpc(mcp, payload_factory),
        "http_route": _install_http_route(mcp, payload_factory, route_path),
        "tool": _install_tool(mcp, payload_factory, tool_namer or (lambda name: name), tool_base_name),
    }

    try:
        setattr(mcp, _INSTALLED_ATTR, status)
    except Exception as exc:  # pragma: no cover - FastMCP instances accept attributes
        logger.warning(
            "Unable to record server/discover install status",
            error=str(exc),
            error_type=type(exc).__name__,
        )

    logger.info("server/discover installed", **status)
    return status
