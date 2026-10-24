import asyncio
import hashlib
import os
import sys
import time
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError
from test_service import FakeLine

from line_agent.server import create_server
from line_agent.service import AgentService
from line_agent.store import Store


def test_real_stdio_client_contact_job_and_approved_action(tmp_path):
    key = Fernet.generate_key()
    store = Store(tmp_path / "test-account", key)
    owner = AgentService(store, FakeLine(4000))

    async def scenario():
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(Path(__file__).with_name("fixture_server.py"))],
            env={**os.environ, "TEST_KEY": key.decode(), "TEST_STORE": str(store.path)},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                tools = {tool.name for tool in (await client.list_tools()).tools}
                assert "action_execute" in tools
                assert not any(
                    "approve" in tool or "login" in tool or "shell" in tool for tool in tools
                )
                with pytest.raises(McpError):
                    await client.read_resource("line://contacts/all.json")
                result = await client.call_tool("contacts_sync", {})
                assert not result.isError, result
                assert result.structuredContent is not None, result
                assert result.structuredContent["done"] == 4000
                page = await client.call_tool("contacts_list", {"offset": 3900, "limit": 100})
                assert len(page.structuredContent["items"]) == 100
                candidate = await client.call_tool(
                    "action_prepare",
                    {
                        "kind": "send_text",
                        "payload": {"to": owner.line.ids[0], "text": "架空の試験文"},
                    },
                )
                action_id = candidate.structuredContent["id"]
                refused = await client.call_tool("action_execute", {"action_id": action_id})
                assert refused.structuredContent["ok"] is False
                owner.approve_locally(action_id)
                sent = await client.call_tool("action_execute", {"action_id": action_id})
                assert sent.structuredContent["state"] == "sent"
                resources = await client.list_resources()
                assert str(resources.resources[0].uri) == "line://contacts/all.json"

    asyncio.run(scenario())


def test_http_requires_correct_unexpired_audience_and_rejects_foreign_origin(tmp_path):
    service = AgentService(Store(tmp_path / "test", Fernet.generate_key()), FakeLine())
    token = "test-only-not-a-real-credential"
    service.store.put(
        "setting",
        "remote_access",
        {
            "sha256": hashlib.sha256(token.encode()).hexdigest(),
            "expires_at": time.time() + 300,
            "resource": "https://agent.example/mcp",
        },
    )

    async def scenario():
        server = create_server(service, "https://agent.example")
        app = server.streamable_http_app()
        async with server.session_manager.run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://agent.example"
            ) as client:
                body = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "synthetic-test", "version": "1"},
                    },
                }
                headers = {"Accept": "application/json, text/event-stream"}
                assert (await client.post("/mcp", json=body, headers=headers)).status_code == 401
                headers["Authorization"] = f"Bearer {token}"
                assert (await client.post("/mcp", json=body, headers=headers)).status_code == 200
                headers["Origin"] = "https://hostile.example"
                assert (await client.post("/mcp", json=body, headers=headers)).status_code in (
                    403,
                    421,
                )
                headers.pop("Origin")
                service.store.put(
                    "setting",
                    "remote_access",
                    {
                        "sha256": hashlib.sha256(token.encode()).hexdigest(),
                        "expires_at": time.time() - 1,
                        "resource": "https://agent.example/mcp",
                    },
                )
                assert (await client.post("/mcp", json=body, headers=headers)).status_code == 401

    asyncio.run(scenario())
