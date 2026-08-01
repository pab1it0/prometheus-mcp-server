"""Tests for the ASGI-level strict ``Mcp-*`` header validation (F6).

Header validation moved to the ASGI layer because it is the only place that
sees the exact JSON-RPC body the client sent and the only place that still owns
the HTTP status code. Every test here therefore drives a real ASGI app rather
than a synthesized middleware context -- the class of test that was missing when
strict mode shipped able to reject every client that exists.
"""

import json

import httpx
import pytest

import prometheus_mcp_server.server as server
from prometheus_mcp_server.spec2026.asgi import (
    HEADER_MISMATCH_STATUS,
    MAX_BUFFERED_BODY_BYTES,
    StrictHeaderMiddleware,
    strict_header_middleware,
    tool_annotation_lookup,
)
from prometheus_mcp_server.server import mcp, tool_header_annotations

MCP_URL = "http://testserver/mcp"
CLIENT_HEADERS = {
    "accept": "application/json, text/event-stream",
    "content-type": "application/json",
}


def _rpc(method, params=None, request_id=1):
    """Build a JSON-RPC request body."""
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}


@pytest.fixture
def strict_app():
    """A real FastMCP HTTP app with strict header validation installed.

    ``json_response`` and ``stateless_http`` keep the transport from holding an
    SSE stream open, which is what a test client would otherwise block on.
    """
    return mcp.http_app(
        transport="http",
        json_response=True,
        stateless_http=True,
        middleware=[strict_header_middleware(annotation_lookup=tool_header_annotations)],
    )


class _Recorder:
    """Minimal downstream ASGI app recording what it was handed."""

    def __init__(self):
        """Start with nothing recorded."""
        self.body = None
        self.called = False

    async def __call__(self, scope, receive, send):
        """Drain the request body and record it."""
        self.called = True
        chunks = []
        while True:
            message = await receive()
            if message.get("type") != "http.request":
                break
            chunks.append(message.get("body", b"") or b"")
            if not message.get("more_body", False):
                break
        self.body = b"".join(chunks)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})


async def _drive(app, scope, messages):
    """Run an ASGI app over a scripted receive stream, collecting the response."""
    pending = list(messages)
    sent = []

    async def receive():
        """Yield the next scripted message."""
        if pending:
            return pending.pop(0)
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        """Record an outbound ASGI message."""
        sent.append(message)

    await app(scope, receive, send)
    return sent


def _scope(headers, method="POST"):
    """Build a minimal HTTP ASGI scope."""
    return {
        "type": "http",
        "method": method,
        "path": "/mcp",
        "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
    }


# ---------------------------------------------------------------------------
# Pass-through behaviour
# ---------------------------------------------------------------------------

class TestPassThrough:
    """Everything the middleware does not understand is forwarded untouched."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["GET", "DELETE", "OPTIONS"])
    async def test_non_post_requests_are_forwarded(self, method):
        recorder = _Recorder()
        await _drive(
            StrictHeaderMiddleware(recorder),
            _scope({"mcp-method": "nonsense"}, method=method),
            [{"type": "http.request", "body": b"", "more_body": False}],
        )
        assert recorder.called is True

    @pytest.mark.asyncio
    async def test_non_http_scopes_are_forwarded(self):
        recorder = _Recorder()
        await _drive(StrictHeaderMiddleware(recorder), {"type": "lifespan"}, [])
        assert recorder.called is True

    @pytest.mark.asyncio
    async def test_the_body_reaches_the_app_unchanged(self):
        """The middleware buffers the body, so it must replay it verbatim."""
        recorder = _Recorder()
        body = json.dumps(_rpc("tools/list")).encode()
        await _drive(
            StrictHeaderMiddleware(recorder),
            _scope(CLIENT_HEADERS),
            [
                {"type": "http.request", "body": body[:10], "more_body": True},
                {"type": "http.request", "body": body[10:], "more_body": False},
            ],
        )
        assert recorder.body == body

    @pytest.mark.asyncio
    @pytest.mark.parametrize("body", [b"not json", b"[]", b'{"result": {}}', b""])
    async def test_unparseable_or_non_request_bodies_fail_open(self, body):
        """A batch, a response or garbage is the transport's problem, not ours."""
        recorder = _Recorder()
        await _drive(
            StrictHeaderMiddleware(recorder),
            _scope({"mcp-method": "tools/call"}),
            [{"type": "http.request", "body": body, "more_body": False}],
        )
        assert recorder.called is True

    @pytest.mark.asyncio
    async def test_an_oversized_body_is_forwarded_unvalidated(self):
        """Buffering an unbounded body would be a memory-exhaustion vector."""
        recorder = _Recorder()
        body = b"x" * (MAX_BUFFERED_BODY_BYTES + 1)
        await _drive(
            StrictHeaderMiddleware(recorder),
            _scope({"mcp-method": "nonsense"}),
            [{"type": "http.request", "body": body, "more_body": False}],
        )
        assert recorder.called is True

    @pytest.mark.asyncio
    async def test_reads_past_the_buffer_reach_the_real_receive(self):
        """A streaming response polls receive() for http.disconnect.

        Synthesising a reply once the buffer drains would spin that poll into a
        busy loop and starve the event loop, so the original callable has to
        take over -- which is what actually lets an SSE response stream.
        """
        seen = []

        async def greedy(scope, receive, send):
            """Read one message past the end of the buffered body."""
            seen.append(await receive())
            seen.append(await receive())
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"", "more_body": False})

        await _drive(
            StrictHeaderMiddleware(greedy),
            _scope(CLIENT_HEADERS),
            [
                {"type": "http.request", "body": json.dumps(_rpc("tools/list")).encode(), "more_body": False},
                {"type": "http.disconnect"},
            ],
        )

        assert seen[1] == {"type": "http.disconnect"}

    @pytest.mark.asyncio
    async def test_a_disconnect_is_forwarded(self):
        recorder = _Recorder()
        await _drive(
            StrictHeaderMiddleware(recorder),
            _scope(CLIENT_HEADERS),
            [{"type": "http.disconnect"}],
        )
        assert recorder.called is True

    @pytest.mark.asyncio
    async def test_undecodable_header_bytes_are_skipped(self):
        """Defensive: a header name that will not decode must not raise."""
        recorder = _Recorder()
        scope = _scope(CLIENT_HEADERS)
        scope["headers"].append((object(), object()))
        await _drive(
            StrictHeaderMiddleware(recorder),
            scope,
            [{"type": "http.request", "body": json.dumps(_rpc("tools/list")).encode(), "more_body": False}],
        )
        assert recorder.called is True


# ---------------------------------------------------------------------------
# Rejection behaviour
# ---------------------------------------------------------------------------

class TestRejection:
    """A genuine header/body disagreement is rejected with HTTP 400."""

    @pytest.mark.asyncio
    async def test_a_mismatched_method_header_is_rejected_with_http_400(self):
        """Design F6: Mismatch -> -32020 HeaderMismatch + HTTP 400.

        Regression: the check used to run inside FastMCP middleware, so the
        error was serialized into an HTTP 200 body where proxies, gateways and
        metrics pipelines that key on status code saw a successful request.
        """
        recorder = _Recorder()
        sent = await _drive(
            StrictHeaderMiddleware(recorder),
            _scope({**CLIENT_HEADERS, "mcp-method": "resources/read"}),
            [{"type": "http.request", "body": json.dumps(_rpc("tools/list")).encode(), "more_body": False}],
        )

        assert recorder.called is False
        assert sent[0]["status"] == HEADER_MISMATCH_STATUS == 400
        payload = json.loads(sent[1]["body"])
        assert payload["id"] == 1
        assert payload["error"]["code"] == -32020
        assert payload["error"]["data"]["mismatches"][0]["header"] == "Mcp-Method"

    @pytest.mark.asyncio
    async def test_a_mismatched_param_header_is_rejected(self):
        async def lookup(name):
            """Resolve execute_query's real annotations."""
            return await tool_annotation_lookup(mcp, name)

        body = _rpc("tools/call", {"name": "execute_query", "arguments": {"query": "up", "org_id": "tenant-9"}})
        sent = await _drive(
            StrictHeaderMiddleware(_Recorder(), annotation_lookup=lookup),
            _scope({
                **CLIENT_HEADERS,
                "mcp-method": "tools/call",
                "mcp-name": "execute_query",
                "mcp-param-org-id": "tenant-OTHER",
            }),
            [{"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}],
        )

        payload = json.loads(sent[1]["body"])
        assert payload["error"]["data"]["mismatches"][0]["header"] == "Mcp-Param-Org-Id"

    @pytest.mark.asyncio
    async def test_a_failing_annotation_lookup_skips_the_param_headers(self):
        """A registry outage must not turn every tools/call into a 400."""
        async def explode(_name):
            raise RuntimeError("registry down")

        recorder = _Recorder()
        body = _rpc("tools/call", {"name": "execute_query", "arguments": {"query": "up"}})
        await _drive(
            StrictHeaderMiddleware(recorder, annotation_lookup=explode),
            _scope({**CLIENT_HEADERS, "mcp-method": "tools/call", "mcp-name": "execute_query"}),
            [{"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}],
        )
        assert recorder.called is True

    @pytest.mark.asyncio
    async def test_a_non_string_tool_name_skips_the_lookup(self):
        recorder = _Recorder()
        body = _rpc("tools/call", {"name": 42})
        await _drive(
            StrictHeaderMiddleware(recorder, annotation_lookup=tool_header_annotations),
            _scope(CLIENT_HEADERS),
            [{"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}],
        )
        assert recorder.called is True


# ---------------------------------------------------------------------------
# End to end against the real server over HTTP
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_stock_client_completes_the_handshake_under_strict_mode(strict_app):
    """Regression: strict mode rejected every existing client at initialize.

    This drives the exact header set the official SDK's streamable-HTTP client
    sends -- no Mcp-* hints, plus the transport-mandated MCP-Protocol-Version.
    """
    transport = httpx.ASGITransport(app=strict_app)
    async with strict_app.router.lifespan_context(strict_app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/mcp",
                headers=CLIENT_HEADERS,
                json=_rpc("initialize", {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "probe", "version": "1"},
                }),
            )

    assert response.status_code == 200
    assert "-32020" not in response.text


@pytest.mark.asyncio
async def test_a_cold_tools_call_with_correct_2026_headers_succeeds(strict_app, monkeypatch):
    """Regression: the first tools/call of a connection was rejected.

    FastMCP warms its tool cache by re-entering its own middleware chain with an
    internal tools/list, so validating FastMCP's per-hop method against the real
    HTTP headers reported Mcp-Method: tools/call as a mismatch -- but only until
    the cache was warm, making the failure depend on call ordering.
    """
    monkeypatch.setattr(server.config, "url", "")

    transport = httpx.ASGITransport(app=strict_app)
    async with strict_app.router.lifespan_context(strict_app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            session_headers = {**CLIENT_HEADERS, "mcp-protocol-version": "2025-11-25"}
            # No tools/list first: the tool cache is deliberately cold.
            response = await client.post(
                "/mcp",
                headers={**session_headers, "mcp-method": "tools/call", "mcp-name": "health_check"},
                json=_rpc("tools/call", {"name": "health_check", "arguments": {}}, request_id=2),
            )

    assert response.status_code == 200
    assert "-32020" not in response.text
    assert "HeaderMismatch" not in response.text
    assert "prometheus-mcp-server" in response.text


@pytest.mark.asyncio
async def test_a_genuinely_wrong_header_still_gets_http_400(strict_app):
    """The feature must still do its job end to end."""
    transport = httpx.ASGITransport(app=strict_app)
    async with strict_app.router.lifespan_context(strict_app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/mcp",
                headers={**CLIENT_HEADERS, "mcp-method": "resources/read"},
                json=_rpc("tools/list"),
            )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32020
