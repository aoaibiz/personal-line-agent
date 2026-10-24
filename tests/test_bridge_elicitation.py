import asyncio
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp import StdioServerParameters, types
from mcp.server.lowlevel.server import request_ctx
from mcp.shared.exceptions import McpError

from line_agent import bridge, runtime


def form():
    return types.ElicitRequestFormParams(
        mode="form", message="synthetic", requestedSchema={"type": "object", "properties": {}}
    )


def test_relay_timeout_is_error_not_fabricated_owner_decision(monkeypatch):
    monkeypatch.setattr(bridge, "ELICITATION_RELAY_TIMEOUT_SECONDS", 0.02)

    async def scenario():
        cancelled = asyncio.Event()

        async def never_responds(*args):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with pytest.raises(McpError) as error:
            await bridge.relay_elicitation(SimpleNamespace(elicit_form=never_responds), form())
        assert error.value.error.message == "authentication_ui_timeout"
        assert cancelled.is_set()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["accept", "decline", "cancel"])
def test_relay_preserves_actual_owner_response(action):
    async def scenario():
        response = types.ElicitResult(action=action, content={"confirm": True})

        async def respond(*args):
            return response

        assert await bridge.relay_elicitation(SimpleNamespace(elicit_form=respond), form()) is response

    asyncio.run(scenario())


def test_relay_cancellation_propagates():
    async def scenario():
        async def cancelled(*args):
            raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await bridge.relay_elicitation(SimpleNamespace(elicit_form=cancelled), form())

    asyncio.run(scenario())


def test_real_backend_timeout_returns_while_original_host_never_responds(monkeypatch):
    # Actual subprocess/stdio backend and production bridge; only the host UI
    # and short test deadlines are synthetic. No account/credentials are read.
    monkeypatch.setattr(bridge, "ELICITATION_RELAY_TIMEOUT_SECONDS", 0.15)
    monkeypatch.setattr(bridge, "selected", lambda _: {"current": "a" * 64})
    monkeypatch.setattr(runtime, "release_path", lambda *_: Path("/unused"))
    monkeypatch.setattr(runtime, "StdioServerParameters", lambda **_: StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).with_name("fixture_auth_roundtrip.py")), "backend-timeout"],
        env=dict(os.environ),
    ))

    async def scenario():
        calls = []

        async def never_responds(*args):
            calls.append(("owner_start", time.monotonic()))
            await asyncio.Event().wait()

        cap = types.ElicitationCapability(
            form=types.FormElicitationCapability(), url=types.UrlElicitationCapability()
        )
        session = SimpleNamespace(
            client_params=SimpleNamespace(capabilities=SimpleNamespace(elicitation=cap)),
            elicit_form=never_responds,
        )
        server = bridge.create_bridge(Path("/unused"), Path("/unused"), "test")
        token = request_ctx.set(SimpleNamespace(session=session))
        try:
            request = types.CallToolRequest(params=types.CallToolRequestParams(
                name="authentication_repair",
                arguments={"request_id": "22222222-2222-4222-8222-222222222222"},
            ))
            response = await asyncio.wait_for(server.request_handlers[types.CallToolRequest](request), 5)
            data = response.root.structuredContent
            assert data["error"] == "authentication_ui_timeout"
            assert data["login_invoked"] is False
            assert data["host_action"] is None
            assert [call[0] for call in calls] == ["owner_start"]
            # Process startup is outside the owner UI timeout; after the UI prompt
            # reaches the host, the relay deadline itself must remain bounded.
            assert time.monotonic() - calls[0][1] < 2
        finally:
            request_ctx.reset(token)

    asyncio.run(scenario())
