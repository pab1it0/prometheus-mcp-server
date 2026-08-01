#!/usr/bin/env python

"""Per-request protocol negotiation for the MCP 2026-07-28 compatibility layer.

Under 2026-07-28 the client restates who it is and what it can do on *every*
request, inside ``params._meta``, instead of once during ``initialize``. The
specification is explicit that a server MUST NOT infer a client's capabilities
from an earlier request, so this module deliberately offers no cache: state is
extracted per request, published on a :class:`contextvars.ContextVar` for the
duration of that request, and reset afterwards.

Backward compatibility is preserved by treating a missing
``io.modelcontextprotocol/protocolVersion`` as "legacy client" rather than as an
error. Only a version that is *present and unrecognised* is rejected, with
JSON-RPC code ``-32022``.

Nothing here introspects SDK internals, and every conversion of a foreign object
into a mapping is defensive, so a future ``mcp`` release cannot break import or
raise from the extraction path.
"""

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, Optional

from prometheus_mcp_server.logging_config import get_logger
from prometheus_mcp_server.spec2026.constants import (
    META_CLIENT_CAPABILITIES,
    META_CLIENT_INFO,
    META_LOG_LEVEL,
    META_PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    UNSUPPORTED_PROTOCOL_VERSION,
)

logger = get_logger()


class NegotiationError(Exception):
    """A request cannot be served because its ``_meta`` failed negotiation.

    Carries the JSON-RPC ``code``/``message``/``data`` triple so callers can
    turn it into an error response without re-deriving any of it.
    """

    def __init__(self, code: int, message: str, data: Optional[Dict[str, Any]] = None) -> None:
        """Initialise the error.

        Args:
            code: JSON-RPC error code to report.
            message: Concise single-sentence description of the failure.
            data: Optional structured payload for the ``error.data`` member.
        """
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def to_error_data(self) -> Dict[str, Any]:
        """Render the error as a JSON-RPC ``error`` object.

        Returns:
            Dictionary with ``code``, ``message`` and ``data`` members.
        """
        return {"code": self.code, "message": self.message, "data": self.data}

    def to_mcp_error(self) -> Exception:
        """Convert to the SDK's ``McpError`` so a handler can raise it directly.

        The low-level server turns a raised ``McpError`` into the exact
        JSON-RPC error carried here. The SDK import is done lazily and
        defensively so that an incompatible SDK degrades to returning ``self``
        rather than raising an unrelated exception.

        Returns:
            An ``mcp.shared.exceptions.McpError`` when the SDK exposes one,
            otherwise this exception itself.
        """
        try:
            from mcp.shared.exceptions import McpError
            from mcp.types import ErrorData

            return McpError(ErrorData(code=self.code, message=self.message, data=self.data))
        except Exception as e:  # pragma: no cover - depends on SDK layout
            logger.warning(
                "Could not build McpError from negotiation failure; returning raw error",
                error=str(e),
                error_type=type(e).__name__,
            )
            return self


class UnsupportedProtocolVersionError(NegotiationError):
    """The request named a protocol version this server does not speak."""

    def __init__(self, requested: Any) -> None:
        """Initialise the error for a rejected protocol version.

        Args:
            requested: The unusable value read from
                ``_meta["io.modelcontextprotocol/protocolVersion"]``. Non-string
                values are stringified so the payload stays JSON-serialisable.
        """
        readable = requested if isinstance(requested, str) else str(requested)
        super().__init__(
            code=UNSUPPORTED_PROTOCOL_VERSION,
            message=f"Unsupported protocol version: {readable}",
            data={
                "supported": list(SUPPORTED_PROTOCOL_VERSIONS),
                "requested": readable,
            },
        )
        self.requested = readable


@dataclass(frozen=True)
class RequestNegotiation:
    """Negotiation state extracted from a single request's ``_meta``.

    Attributes:
        protocol_version: Version the client declared, or ``None`` for a legacy
            client that declared none.
        client_capabilities: Capabilities declared on *this* request only.
        client_info: Client implementation name/version for this request.
        log_level: Requested minimum log severity, or ``None`` when the client
            did not opt into ``notifications/message`` for this request.
        meta: The raw ``_meta`` mapping, so downstream modules (trace-context
            propagation, header validation) can read keys this class does not
            model.
    """

    protocol_version: Optional[str] = None
    client_capabilities: Dict[str, Any] = field(default_factory=dict)
    client_info: Dict[str, Any] = field(default_factory=dict)
    log_level: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_legacy(self) -> bool:
        """Whether the client declared no protocol version on this request.

        Returns:
            True when no ``io.modelcontextprotocol/protocolVersion`` was sent.
        """
        return self.protocol_version is None

    @property
    def may_log(self) -> bool:
        """Whether ``notifications/message`` may be emitted for this request.

        Returns:
            True only when the request carried a log level.
        """
        return self.log_level is not None


#: Per-request negotiation state. Defaults to ``None`` so that code running
#: outside a negotiated request sees "nothing declared" rather than stale data
#: from a previous request.
_CURRENT_NEGOTIATION: contextvars.ContextVar[Optional[RequestNegotiation]] = contextvars.ContextVar(
    "prometheus_mcp_spec2026_negotiation",
    default=None,
)


def _get_field(source: Any, name: str) -> Any:
    """Read ``name`` from an object attribute or a mapping key, else ``None``."""
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)


def _coerce_mapping(value: Any) -> Optional[Dict[str, Any]]:
    """Best-effort conversion of an arbitrary value into a plain dict.

    Handles plain dicts, pydantic models (``model_dump`` then ``model_extra``)
    and anything else by returning ``None``.

    Args:
        value: Candidate mapping-like object.

    Returns:
        A new dict, or ``None`` when the value is not mapping-like.
    """
    if value is None:
        return None

    if isinstance(value, dict):
        return dict(value)

    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            dumped = dump(by_alias=True, exclude_none=True)
        except Exception as e:
            logger.warning(
                "Could not dump request metadata model",
                error=str(e),
                error_type=type(e).__name__,
                value_type=type(value).__name__,
            )
        else:
            if isinstance(dumped, dict):
                return dumped

    extra = getattr(value, "model_extra", None)
    if isinstance(extra, dict):
        return dict(extra)

    return None


def extract_request_meta(request: Any) -> Dict[str, Any]:
    """Pull the ``_meta`` mapping off an incoming request.

    Accepts a parsed MCP request model, a raw JSON-RPC request dict, a bare
    params object/dict, or anything exposing ``meta``/``_meta``. Every failure
    mode the specification permits -- ``_meta`` absent, explicitly ``None``, or
    present but not an object -- yields an empty dict rather than an exception.

    Args:
        request: The request, params, or metadata container to inspect.

    Returns:
        The ``_meta`` mapping with string keys, or an empty dict.
    """
    if request is None:
        return {}

    params = _get_field(request, "params")
    source = request if params is None else params

    raw_meta = _get_field(source, "meta")
    if raw_meta is None:
        raw_meta = _get_field(source, "_meta")

    if raw_meta is None:
        return {}

    coerced = _coerce_mapping(raw_meta)
    if coerced is None:
        logger.warning(
            "Ignoring malformed request _meta",
            meta_type=type(raw_meta).__name__,
        )
        return {}

    return {str(key): value for key, value in coerced.items()}


def validate_protocol_version(version: Any) -> Optional[str]:
    """Validate a declared protocol version.

    A missing version is legal: pre-2026 clients never send one and must keep
    working unchanged.

    Args:
        version: Raw value read from ``_meta``, or ``None`` when absent.

    Returns:
        The accepted version string, or ``None`` when none was declared.

    Raises:
        UnsupportedProtocolVersionError: The value was present but is not one of
            ``SUPPORTED_PROTOCOL_VERSIONS``. Carries JSON-RPC code ``-32022``
            and ``data={"supported": [...], "requested": "..."}``.
    """
    if version is None:
        return None

    if isinstance(version, str) and version in SUPPORTED_PROTOCOL_VERSIONS:
        return version

    logger.warning(
        "Rejecting unsupported protocol version",
        requested=version if isinstance(version, str) else str(version),
        supported=list(SUPPORTED_PROTOCOL_VERSIONS),
    )
    raise UnsupportedProtocolVersionError(version)


def _extract_log_level(meta: Dict[str, Any]) -> Optional[str]:
    """Read the requested log level, treating any non-string value as absent."""
    raw_level = meta.get(META_LOG_LEVEL)
    if raw_level is None:
        return None

    if isinstance(raw_level, str) and raw_level.strip():
        return raw_level

    logger.warning(
        "Ignoring malformed log level in request _meta; log notifications stay disabled",
        log_level_type=type(raw_level).__name__,
    )
    return None


def _extract_mapping(meta: Dict[str, Any], key: str) -> Dict[str, Any]:
    """Read a mapping-valued ``_meta`` key, defaulting to an empty dict."""
    raw_value = meta.get(key)
    if raw_value is None:
        return {}

    coerced = _coerce_mapping(raw_value)
    if coerced is None:
        logger.warning(
            "Ignoring malformed request _meta entry",
            meta_key=key,
            value_type=type(raw_value).__name__,
        )
        return {}

    return coerced


def negotiation_from_meta(meta: Any) -> RequestNegotiation:
    """Build negotiation state from an already-extracted ``_meta`` mapping.

    Args:
        meta: The ``_meta`` mapping. Anything that is not a dict (including
            ``None``) is treated as an empty mapping.

    Returns:
        The negotiation state for this request.

    Raises:
        UnsupportedProtocolVersionError: A protocol version was declared but is
            not supported.
    """
    if not isinstance(meta, dict):
        if meta is not None:
            logger.warning(
                "Ignoring malformed request _meta",
                meta_type=type(meta).__name__,
            )
        meta = {}

    protocol_version = validate_protocol_version(meta.get(META_PROTOCOL_VERSION))

    return RequestNegotiation(
        protocol_version=protocol_version,
        client_capabilities=_extract_mapping(meta, META_CLIENT_CAPABILITIES),
        client_info=_extract_mapping(meta, META_CLIENT_INFO),
        log_level=_extract_log_level(meta),
        meta=dict(meta),
    )


def negotiate_request(request: Any) -> RequestNegotiation:
    """Extract and validate the negotiation state of an incoming request.

    Args:
        request: The request, params, or metadata container to inspect.

    Returns:
        The negotiation state for this request.

    Raises:
        UnsupportedProtocolVersionError: A protocol version was declared but is
            not supported.
    """
    return negotiation_from_meta(extract_request_meta(request))


def get_current_negotiation() -> Optional[RequestNegotiation]:
    """Return the negotiation state of the request being served.

    Returns:
        The current state, or ``None`` when no request is in scope. ``None`` is
        never a stale value from an earlier request: capabilities are
        per-request and are always reset.
    """
    return _CURRENT_NEGOTIATION.get()


def set_current_negotiation(negotiation: Optional[RequestNegotiation]) -> contextvars.Token:
    """Publish negotiation state for the request being served.

    Args:
        negotiation: State to publish, or ``None`` to explicitly declare that
            nothing was negotiated.

    Returns:
        A token that must be handed to :func:`reset_current_negotiation` once
        the request completes.
    """
    return _CURRENT_NEGOTIATION.set(negotiation)


def reset_current_negotiation(token: Optional[contextvars.Token]) -> None:
    """Restore the negotiation state that preceded ``token``.

    Called from a ``finally`` block, this is what stops one request's declared
    capabilities from leaking into the next. A token created in a different
    context (which ``ContextVar.reset`` rejects) still results in the variable
    being cleared, so a leak is impossible even on the error path.

    Args:
        token: Token returned by :func:`set_current_negotiation`, or ``None``
            when no state was published.
    """
    if token is None:
        _CURRENT_NEGOTIATION.set(None)
        return

    try:
        _CURRENT_NEGOTIATION.reset(token)
    except (ValueError, RuntimeError) as e:
        logger.warning(
            "Could not reset negotiation context with token; clearing instead",
            error=str(e),
            error_type=type(e).__name__,
        )
        _CURRENT_NEGOTIATION.set(None)


def clear_current_negotiation() -> None:
    """Drop any published negotiation state.

    Use when no token is available, for instance when tearing down a context
    that was populated by code outside this module.
    """
    _CURRENT_NEGOTIATION.set(None)


@contextmanager
def negotiation_scope(negotiation: Optional[RequestNegotiation]) -> Iterator[Optional[RequestNegotiation]]:
    """Publish negotiation state for the duration of a ``with`` block.

    The state is always reset on exit, including when the body raises, which is
    the leak-proof way to satisfy the specification's requirement that
    capabilities never carry across requests.

    Args:
        negotiation: State to publish for the duration of the block.

    Yields:
        The same state that was published.
    """
    token = set_current_negotiation(negotiation)
    try:
        yield negotiation
    finally:
        reset_current_negotiation(token)


def may_emit_log_notifications(negotiation: Optional[RequestNegotiation] = None) -> bool:
    """Whether ``notifications/message`` may be emitted for a request.

    Under 2026-07-28 a server MUST NOT send log notifications unless the request
    carried ``io.modelcontextprotocol/logLevel``, so the answer defaults to
    ``False`` whenever nothing was negotiated.

    Args:
        negotiation: State to consult. Defaults to the state of the request
            currently in scope.

    Returns:
        True only when the request declared a log level.
    """
    state = negotiation if negotiation is not None else get_current_negotiation()
    if state is None:
        return False
    return state.log_level is not None


def current_log_level() -> Optional[str]:
    """Return the log level declared by the request being served.

    Returns:
        The declared level, or ``None`` when none was declared (in which case
        no log notifications may be emitted).
    """
    state = get_current_negotiation()
    if state is None:
        return None
    return state.log_level


__all__ = [
    "NegotiationError",
    "RequestNegotiation",
    "UnsupportedProtocolVersionError",
    "clear_current_negotiation",
    "current_log_level",
    "extract_request_meta",
    "get_current_negotiation",
    "may_emit_log_notifications",
    "negotiate_request",
    "negotiation_from_meta",
    "negotiation_scope",
    "reset_current_negotiation",
    "set_current_negotiation",
    "validate_protocol_version",
]
