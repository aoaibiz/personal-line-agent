"""Synthetic accounts only: real MCP host round trip plus refusal/race boundaries."""

import asyncio
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import ElicitationCapability, ElicitResult, FormElicitationCapability
from test_service import FakeLine

from line_agent.chat_approval import Confirmation, confirm_and_execute, preview, supported
from line_agent.service import AgentService
from line_agent.store import Store


def test_real_stdio_native_confirmation_then_one_execution(tmp_path):
    key = Fernet.generate_key()
    owner = AgentService(Store(tmp_path / "synthetic", key), FakeLine(4000))
    captured = []

    async def confirm(ctx, params):
        captured.append(params)
        assert "架空の試験本文" in params.message
        assert "友達" in params.message
        assert params.requestedSchema["required"] == ["confirm"]
        assert "default" not in params.requestedSchema["properties"]["confirm"]
        return ElicitResult(action="accept", content={"confirm": True})

    async def scenario():
        params = StdioServerParameters(command=sys.executable,
            args=[str(Path(__file__).with_name("fixture_server.py"))],
            env={**os.environ, "TEST_KEY": key.decode(), "TEST_STORE": str(owner.store.path)})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, elicitation_callback=confirm) as client:
                await client.initialize()
                status = (await client.call_tool("agent_status")).structuredContent
                assert status["approval"]["form_supported"] is True
                action = (await client.call_tool("action_prepare", {"kind": "send_text",
                    "payload": {"to": owner.line.ids[0], "text": "架空の試験本文"}})).structuredContent
                result = (await client.call_tool("action_execute", {"action_id": action["id"]})).structuredContent
                assert result["state"] == "sent"
                assert result["authority"] == "mcp_host_confirmation"
                again = (await client.call_tool("action_execute", {"action_id": action["id"]})).structuredContent
                assert again["ok"] is False
                assert len(captured) == 1
                assert owner.action(action["id"])["state"] == "sent"
    asyncio.run(scenario())


class Host:
    def __init__(self, response="accept", confirm=True, mutate=None, supported=True):
        self.session = SimpleNamespace(client_params=SimpleNamespace(
            capabilities=SimpleNamespace(elicitation=ElicitationCapability(
                form=FormElicitationCapability()) if supported else None),
            protocolVersion="2025-11-25"))
        self.response, self.confirm, self.mutate = response, confirm, mutate
        self.calls = 0

    async def elicit(self, message, schema):
        self.calls += 1
        if self.mutate:
            self.mutate()
        if self.response == "timeout":
            raise TimeoutError()
        if self.response == "disconnect":
            raise RuntimeError("sensitive synthetic exception never returned")
        return SimpleNamespace(action=self.response,
            data=Confirmation(confirm=self.confirm) if self.response == "accept" else None)


@pytest.mark.parametrize("kind,payload", [
    ("send_text", {"to": "u" + "0" * 32, "text": "架空"}),
    ("send_mention", {"to": "c" + "1" * 32, "text": "@架空", "mentions": [
        {"mid": "u" + "0" * 32, "start": 0, "end": 3}]}),
    ("rename_contact", {"mid": "u" + "0" * 32, "name": "架空さん"}),
    ("create_group", {"name": "架空グループ", "members": ["u" + "0" * 32]}),
    ("invite", {"group": "c" + "1" * 32, "members": ["u" + "0" * 32]}),
    ("rename_group", {"group": "c" + "1" * 32, "name": "架空変更後"}),
    ("add_friend", {"mid": "u" + "0" * 32}),
    ("send_media", {"to": "u" + "0" * 32, "media_id": "fixture", "media_type": "image"}),
])
def test_all_action_kinds_use_same_host_gate(service, kind, payload):
    service.store.put("media", "fixture", {"id": "fixture", "sha256": "synthetic"})
    action = service.prepare(kind, payload)
    host = Host()
    assert asyncio.run(confirm_and_execute(service, action["id"], host))["state"] == "sent"
    assert len(service.line.calls) == host.calls == 1


@pytest.mark.parametrize("host,code", [
    (Host(response="decline"), "confirmation_declined"),
    (Host(response="cancel"), "confirmation_cancelled"),
    (Host(confirm=False), "confirmation_declined"),
    (Host(response="timeout"), "confirmation_timeout"),
    (Host(response="disconnect"), "confirmation_transport_failed"),
    (Host(supported=False), "chat_confirmation_unavailable"),
])
def test_refusal_diagnostics_and_no_external_write(service, host, code):
    action = service.prepare("send_text", {"to": service.line.ids[0], "text": "秘密の架空本文"})
    result = asyncio.run(confirm_and_execute(service, action["id"], host))
    assert result["error"] == code
    assert not service.line.calls
    event = service.store.records("approval_event")[0]["value"]
    base = {"action_id", "stage", "reason", "at"}
    if host.response in ("accept", "decline", "cancel") and host.calls:
        base |= {"host_action", "decision_origin", "presentation_state"}
        assert event["decision_origin"] == event["presentation_state"] == "unknown"
        assert "判別できません" in result["message"]
    assert set(event) == base
    assert "秘密" not in str(result) + str(event)


@pytest.mark.parametrize("change", ["text", "to", "expiry", "pause", "identity", "new_message"])
def test_changes_during_confirmation_stop_execution(service, change):
    action = service.prepare("send_text", {"to": service.line.ids[0], "text": "架空"})
    def mutate():
        current = service.action(action["id"])
        if change in ("text", "to"):
            current["payload"][change] = "変わった本文" if change == "text" else service.line.ids[1]
        elif change == "expiry":
            current["expires_at"] = time.time() - 1
        elif change == "pause":
            service.pause()
        elif change == "identity":
            service.store.put("setting", "identity", {"mid": service.line.ids[2]})
        else:
            service.line.messages = [{"id": "new", "text": "架空の新着"}]
        service.store.put("action", action["id"], current)
    if change == "new_message":
        with pytest.raises(ValueError):
            asyncio.run(confirm_and_execute(service, action["id"], Host(mutate=mutate)))
    else:
        assert asyncio.run(confirm_and_execute(service, action["id"], Host(mutate=mutate)))["error"] == "confirmation_invalidated"
    assert not service.line.calls


def test_binding_is_rechecked_immediately_before_external_write(service):
    action = service.prepare("send_text", {"to": service.line.ids[0], "text": "架空"})
    binding = service.approval_binding(action)
    service.approve_from_client(action["id"], binding)
    current = service.action(action["id"])
    current["payload"]["text"] = "差し替え"
    service.store.put("action", action["id"], current)
    with pytest.raises(ValueError):
        service.execute(action["id"])
    assert not service.line.calls


def test_external_unknown_is_not_retried(service):
    action = service.prepare("send_text", {"to": service.line.ids[0], "text": "架空"})
    service.line.fail_send = True
    assert asyncio.run(confirm_and_execute(service, action["id"], Host()))["state"] == "unknown"
    with pytest.raises(ValueError):
        asyncio.run(confirm_and_execute(service, action["id"], Host()))
    assert len(service.line.calls) == 1


def test_preview_keeps_text_but_escapes_control_and_bidi(service):
    action = service.prepare("send_text", {"to": service.line.ids[0], "text": "架空\u202e\x1b[2J"})
    message = preview(service, action)
    assert "架空" in message and "\u202e" not in message and "\x1b" not in message


def test_url_only_is_not_form_support_and_legacy_is_explicit():
    from mcp.types import UrlElicitationCapability
    host = Host()
    host.session.client_params.capabilities.elicitation = ElicitationCapability(
        url=UrlElicitationCapability())
    assert supported(host) is False
    host.session.client_params.capabilities.elicitation = ElicitationCapability()
    assert supported(host) is False
    host.session.client_params.protocolVersion = "2025-06-18"
    assert supported(host) is True


def test_media_preview_discloses_sending_type(service):
    service.store.put("media", "fixture", {"id": "fixture", "name": "架空.jpg"})
    action = service.prepare("send_media", {"to": service.line.ids[0],
        "media_id": "fixture", "media_type": "file"})
    assert '"file"' in preview(service, action)
