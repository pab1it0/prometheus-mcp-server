#!/usr/bin/env python

"""ASGI-level strict ``Mcp-*`` header validation for MCP 2026-07-28 (F6).

Header validation belongs at the ASGI layer and nowhere else, for three reasons
that were each proven by a failing request first:

1. **The body must be the bytes the client sent.** FastMCP re-enters its own
   middleware chain with internal sub-requests (a cold ``tools/call`` triggers an
   internal ``tools/list`` to warm the tool cache). Validating FastMCP's
   per-hop view of "the request" against the real HTTP headers compares a
   ``tools/list`` body with a ``Mcp-Method: tools/call`` header and rejects a
   perfectly correct request -- but only on the first call, before the cache is
   warm. The raw ASGI body has no such ambiguity.
2. **A ``params.uri`` must be compared as sent.** By the time the SDK has parsed
   the request, a URI has been round-tripped through ``pydantic.AnyUrl`` and
   normalised, so it no longer matches the header the client mirrored it into.
3. **The design requires HTTP 400.** Only the ASGI layer still owns the status
   code; from inside the JSON-RPC dispatch the response is already committed to
   HTTP 200.

The middleware is **opt-in** (``PROMETHEUS_MCP_STRICT_HEADERS``) and inert for
everything it does not understand: non-POST requests, non-JSON bodies,
unparseable JSON and JSON-RPC batches are all forwarded untouched, so a
misconfiguration can only ever fail open.

Standard library plus Starlette (already a FastMCP dependency) only.
"""

import json
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from prometheus_mcp_server.logging_config import get_logger
from prometheus_mcp_server.spec2026.headers import (
    HeaderAnnotation,
    collect_header_annotations,
    header_mismatch_error,
    validate_request_headers,
)

logger = get_logger()

#: Largest request body this middleware will buffer before giving up and
#: forwarding unvalidated. A JSON-RPC request that big is not a routing hint
#: problem, and buffering it would be a memory-exhaustion vector.
MAX_BUFFERED_BODY_BYTES = 1024 * 1024

#: HTTP status for a header/body disagreement (design feature F6).
HEADER_MISMATCH_STATUS = 400


async def _read_body(
    receive: Callable[[], Awaitable[Dict[str, Any]]],
) -> "tuple[List[Dict[str, Any]], bool]":
    """Buffer the whole request body, returning the raw ASGI messages.

    Args:
        receive: The ASGI receive callable.

    Returns:
        Tuple of (every message consumed, whether the body was read in full).
        The messages are always returned so the caller can replay them
        downstream even when validation has to be skipped.
    """
    messages: List[Dict[str, Any]] = []
    size = 0
    while True:
        message = await receive()
        messages.append(message)
        if message.get("type") != "http.request":
            # http.disconnect: nothing to validate, let the app handle it.
            return messages, False
        size += len(message.get("body", b"") or b"")
        if size > MAX_BUFFERED_BODY_BYTES:
            logger.warning(
                "Request body too large for strict header validation; forwarding unvalidated",
                max_bytes=MAX_BUFFERED_BODY_BYTES,
            )
            return messages, False
        if not message.get("more_body", False):
            return messages, True


def _replay(
    messages: Sequence[Dict[str, Any]],
    original: Callable[[], Awaitable[Dict[str, Any]]],
) -> Callable[[], Awaitable[Dict[str, Any]]]:
    """Build a receive callable that replays already-consumed ASGI messages.

    Once the buffer is drained the original callable takes over again. That
    matters for more than tidiness: a streaming response polls ``receive`` to
    notice ``http.disconnect``, so synthesising a reply here would spin that
    poll into a busy loop and starve the event loop instead of streaming.

    Args:
        messages: Messages read off the original receive callable.
        original: The receive callable the messages were read from.

    Returns:
        A receive callable yielding the buffered messages, then delegating.
    """
    pending = list(messages)

    async def receive() -> Dict[str, Any]:
        """Yield the next buffered message, then defer to the real stream."""
        if pending:
            return pending.pop(0)
        return await original()

    return receive


def _headers_from_scope(scope: Dict[str, Any]) -> Dict[str, str]:
    """Decode the ASGI raw header list into a plain string mapping.

    Args:
        scope: The ASGI connection scope.

    Returns:
        Mapping of lowercased header name to value. Repeated headers keep the
        last value, matching the way the header hints are meant to be read.
    """
    headers: Dict[str, str] = {}
    for raw_name, raw_value in scope.get("headers") or ():
        try:
            headers[raw_name.decode("latin-1").lower()] = raw_value.decode("latin-1")
        except Exception:  # pragma: no cover - ASGI servers always hand over bytes
            continue
    return headers


class StrictHeaderMiddleware:
    """Reject an HTTP request whose ``Mcp-*`` headers contradict its body.

    Args:
        app: The next ASGI application in the chain.
        annotation_lookup: Optional async callable mapping a tool name to its
            validated ``x-mcp-header`` annotations, used to check the
            ``Mcp-Param-*`` headers of a ``tools/call``. Omitted or failing
            lookups simply skip the parameter headers.
    """

    def __init__(
        self,
        app: Any,
        annotation_lookup: Optional[Callable[[str], Awaitable[Sequence[HeaderAnnotation]]]] = None,
    ) -> None:
        """Store the downstream app and the tool-annotation resolver."""
        self.app = app
        self.annotation_lookup = annotation_lookup

    async def __call__(self, scope: Dict[str, Any], receive: Any, send: Any) -> None:
        """Validate the request, then run the rest of the ASGI chain.

        Args:
            scope: The ASGI connection scope.
            receive: The ASGI receive callable.
            send: The ASGI send callable.

        Returns:
            None
        """
        if scope.get("type") != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return

        messages, complete = await _read_body(receive)
        replay = _replay(messages, receive)
        if not complete:
            await self.app(scope, replay, send)
            return

        body = b"".join(message.get("body", b"") or b"" for message in messages)

        errors = await self._validate(scope, body)
        if not errors:
            await self.app(scope, replay, send)
            return

        await self._reject(body, errors, send)

    async def _validate(self, scope: Dict[str, Any], body: bytes) -> List[Any]:
        """Validate one buffered request body against its HTTP headers.

        Args:
            scope: The ASGI connection scope.
            body: The raw request body.

        Returns:
            The header validation findings; empty when there is nothing to
            reject, including every case this middleware does not understand.
        """
        try:
            payload = json.loads(body)
        except Exception:
            # Not JSON, or truncated. The transport will produce its own error.
            return []

        if not isinstance(payload, dict) or "method" not in payload:
            # A batch, a response, or a notification: nothing to validate.
            return []

        annotations: Sequence[HeaderAnnotation] = ()
        if payload.get("method") == "tools/call" and self.annotation_lookup is not None:
            params = payload.get("params")
            name = params.get("name") if isinstance(params, dict) else None
            if isinstance(name, str):
                try:
                    annotations = await self.annotation_lookup(name)
                except Exception as e:
                    logger.warning(
                        "Could not resolve tool header annotations; skipping Mcp-Param checks",
                        tool_name=name,
                        error=str(e),
                        error_type=type(e).__name__,
                    )

        return validate_request_headers(
            _headers_from_scope(scope),
            payload,
            strict=True,
            header_annotations=annotations,
        )

    async def _reject(self, body: bytes, errors: Sequence[Any], send: Any) -> None:
        """Send the HeaderMismatch response, ending the request.

        Args:
            body: The raw request body, used only to echo the JSON-RPC id.
            errors: Findings from :func:`validate_request_headers`.
            send: The ASGI send callable.

        Returns:
            None
        """
        request_id = None
        try:
            request_id = json.loads(body).get("id")
        except Exception:  # pragma: no cover - the body already parsed once
            pass

        error = header_mismatch_error(errors)
        payload = json.dumps({"jsonrpc": "2.0", "id": request_id, "error": error}).encode("utf-8")

        logger.warning(
            "Rejecting request with mismatched MCP headers",
            status=HEADER_MISMATCH_STATUS,
            mismatched_headers=[finding.header for finding in errors],
        )

        await send(
            {
                "type": "http.response.start",
                "status": HEADER_MISMATCH_STATUS,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload, "more_body": False})


def strict_header_middleware(
    annotation_lookup: Optional[Callable[[str], Awaitable[Sequence[HeaderAnnotation]]]] = None,
) -> Any:
    """Build the Starlette middleware entry FastMCP's HTTP app expects.

    Args:
        annotation_lookup: Async callable resolving a tool name to its validated
            ``x-mcp-header`` annotations.

    Returns:
        A ``starlette.middleware.Middleware`` wrapping
        :class:`StrictHeaderMiddleware`, ready to pass to ``FastMCP.run`` or
        ``FastMCP.http_app`` as ``middleware=[...]``.
    """
    from starlette.middleware import Middleware

    return Middleware(StrictHeaderMiddleware, annotation_lookup=annotation_lookup)


async def tool_annotation_lookup(mcp: Any, tool_name: str) -> List[HeaderAnnotation]:
    """Resolve a tool's ``x-mcp-header`` annotations from a FastMCP registry.

    Args:
        mcp: The FastMCP server instance.
        tool_name: Name of the tool being called.

    Returns:
        The tool's validated header annotations, empty when the tool cannot be
        resolved or declares none.
    """
    try:
        tool = await mcp.get_tool(tool_name)
        return collect_header_annotations(getattr(tool, "parameters", None))
    except Exception as e:
        logger.warning(
            "Could not resolve tool header annotations",
            tool_name=tool_name,
            error=str(e),
            error_type=type(e).__name__,
        )
        return []


__all__ = [
    "HEADER_MISMATCH_STATUS",
    "MAX_BUFFERED_BODY_BYTES",
    "StrictHeaderMiddleware",
    "strict_header_middleware",
    "tool_annotation_lookup",
]
