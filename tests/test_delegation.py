"""Synthetic scope enrollment and executions. No real consent, account, or LINE."""

import asyncio
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_service import FakeLine

from line_agent import delegation, native_approval
from line_agent.server import create_server
from line_agent.service import ACTION_MODELS, AgentService
from line_agent.store import Store


def enroll(service, monkeypatch, **changes):
    service.store.put("setting", "identity", {"mid": service.line.ids[-1]})
    data = dict(targets=[service.line.ids[0]], kinds=list(ACTION_MODELS), hours=24,
                max_daily=20, max_total=50, max_groups=2, allow_created_groups=True)
    data.update(changes)
    monkeypatch.setattr(native_approval, "show_dialog", lambda *a, **k:
                        native_approval.DialogResult("accepted", 1001, 0))
    assert delegation.enroll_native(service, data)["enrolled"]
    # Nothing in normal execution may open any approval UI.
    def no_dialog(*a, **k):
        raise AssertionError("Per-operation dialog is forbidden")
    monkeypatch.setattr(native_approval, "show_dialog", no_dialog)
    return delegation.local_principal(service)


def prepared(service, text="架空の操作"):
    return service.prepare("send_text", {"to": service.line.ids[0], "text": text})


def test_normal_operations_have_no_dialog_and_background_does_not_inherit(service, monkeypatch):
    principal = enroll(service, monkeypatch)
    saved = prepared(service)
    with pytest.raises(ValueError):
        service.execute(saved["id"])
    assert not service.line.calls
    result = service.execute(saved["id"], delegation_principal=principal)
    assert result["state"] == "sent" and result["authority"] == "owner_client_delegation"
    assert result["delegation_id"] == delegation.active(service)["id"]
    with pytest.raises(delegation.DelegationDenied):
        service.execute(saved["id"], delegation_principal=principal)
    assert len(service.line.calls) == 1


@pytest.mark.parametrize("change,reason", [
    ("revoke", "delegation_revoked"), ("expiry", "delegation_expired"),
    ("identity", "delegation_account_or_connection_changed"),
    ("target", "delegation_target_out_of_scope"), ("kind", "delegation_kind_out_of_scope"),
    ("payload", "action_or_conversation_changed"), ("conversation", "action_or_conversation_changed"),
])
def test_scope_and_context_checked_at_write_boundary(service, monkeypatch, change, reason):
    principal = enroll(service, monkeypatch)
    saved = prepared(service)
    original = service.line.execute
    def before_write(kind, payload, *, preflight):
        grant = delegation.active(service)
        row = service.action(saved["id"])
        if change == "revoke":
            delegation.revoke(service)
        elif change == "expiry":
            grant["expires_at"] = time.time() - 1
        elif change == "identity":
            service.store.put("setting", "identity", {"mid": service.line.ids[2]})
        elif change == "target":
            grant["scope"]["targets"] = [service.line.ids[1]]
        elif change == "kind":
            grant["scope"]["kinds"] = ["create_group"]
        elif change == "payload":
            row["payload"]["text"] = "改変"
            service.store.put("action", saved["id"], row)
        else:
            service.line.messages = [{"id": "new", "text": "新着"}]
        if change in ("expiry", "target", "kind"):
            service.store.put("delegation", grant["id"], grant)
        return original(kind, payload, preflight=preflight)
    monkeypatch.setattr(service.line, "execute", before_write)
    with pytest.raises(delegation.DelegationDenied, match=reason):
        service.execute(saved["id"], delegation_principal=principal)
    assert not service.line.calls and delegation.active(service)["used_total"] == 0


def test_quota_and_claim_are_atomic_and_unknown_consumes_quota(service, monkeypatch):
    principal = enroll(service, monkeypatch, max_daily=2, max_total=2)
    actions = [prepared(service, str(i)) for i in range(5)]
    def execute(saved):
        try:
            return service.execute(saved["id"], delegation_principal=principal)["state"]
        except ValueError:
            return "stopped"
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(execute, actions))
    assert results.count("sent") == len(service.line.calls) == 2
    assert delegation.active(service)["used_total"] == 2
    assert results.count("stopped") == 3


def test_unknown_is_not_replayed_or_refunded(service, monkeypatch):
    principal = enroll(service, monkeypatch, max_daily=1, max_total=1)
    service.line.fail_send = True
    saved = prepared(service)
    assert service.execute(saved["id"], delegation_principal=principal)["state"] == "unknown"
    with pytest.raises(delegation.DelegationDenied):
        service.execute(saved["id"], delegation_principal=principal)
    with pytest.raises(delegation.DelegationDenied, match="limit_reached"):
        service.execute(prepared(service)["id"], delegation_principal=principal)
    assert len(service.line.calls) == delegation.active(service)["used_total"] == 1


def test_created_group_scope_never_spreads_to_other_people(service, monkeypatch):
    principal = enroll(service, monkeypatch)
    owner, peer = service.line.ids[-1], service.line.ids[0]
    group = "c" + "a" * 32
    original = service.line.execute
    def create(kind, payload, *, preflight):
        result = original(kind, payload, preflight=preflight)
        return {"chat": {"chatMid": group}} if kind == "create_group" else result
    monkeypatch.setattr(service.line, "execute", create)
    saved = service.prepare("create_group", {"name": "架空グループ", "members": [peer]})
    assert service.execute(saved["id"], delegation_principal=principal)["state"] == "sent"
    assert service.store.get("delegation_group", group)["grant_id"] == delegation.active(service)["id"]
    monkeypatch.setattr(service.line, "group", lambda mid: {"chatMid": mid, "members": [owner, peer], "invitees": []})
    saved = service.prepare("send_text", {"to": group, "text": "架空のグループ送信"})
    assert service.execute(saved["id"], delegation_principal=principal)["state"] == "sent"
    monkeypatch.setattr(service.line, "group", lambda mid: {"chatMid": mid, "members": [owner, peer, service.line.ids[2]], "invitees": []})
    saved = service.prepare("send_text", {"to": group, "text": "止まる本文"})
    with pytest.raises(delegation.DelegationDenied, match="group_members_changed"):
        service.execute(saved["id"], delegation_principal=principal)
    assert len(service.line.calls) == 2


def test_group_waiting_for_invitee_does_not_start_write(service, monkeypatch):
    group = "c" + "b" * 32
    principal = enroll(service, monkeypatch, targets=[service.line.ids[0], group])
    owner, peer = service.line.ids[-1], service.line.ids[0]
    monkeypatch.setattr(service.line, "group", lambda mid: {"chatMid":mid,"members":[owner],"invitees":[peer]})
    saved = service.prepare("send_text", {"to":group,"text":"参加を待つ架空本文"})
    with pytest.raises(delegation.DelegationDenied, match="group_waiting_for_members"):
        service.execute(saved["id"], delegation_principal=principal)
    assert not service.line.calls and delegation.active(service)["used_total"] == 0
    assert service.action(saved["id"])["state"] == "prepared"


def test_undelegated_targets_and_members_stop(service, monkeypatch):
    principal = enroll(service, monkeypatch)
    saved = service.prepare("create_group", {"name": "範囲外", "members": [service.line.ids[1]]})
    with pytest.raises(delegation.DelegationDenied, match="members_out_of_scope"):
        service.execute(saved["id"], delegation_principal=principal)
    assert not service.line.calls


def test_room_cannot_skip_participant_checks(service, monkeypatch):
    principal = enroll(service, monkeypatch)
    room = "r" + "c" * 32
    with pytest.raises(ValueError):
        delegation.candidate(service, dict(targets=[room], kinds=["send_text"], hours=1,
            max_daily=1, max_total=1, max_groups=0))
    grant = delegation.active(service)
    grant["scope"]["targets"] = [room]
    service.store.put("delegation", grant["id"], grant)
    saved = service.prepare("send_text", {"to": room, "text": "止まる架空データ"})
    with pytest.raises(delegation.DelegationDenied, match="target_type_unsupported"):
        service.execute(saved["id"], delegation_principal=principal)
    assert not service.line.calls


def test_cancelled_enrollment_creates_no_grant(service, monkeypatch):
    service.store.put("setting", "identity", {"mid": service.line.ids[-1]})
    monkeypatch.setattr(native_approval, "show_dialog", lambda *a, **k: native_approval.DialogResult("cancelled", 1000, 0))
    result = delegation.enroll_native(service, dict(targets=[service.line.ids[0]], kinds=["send_text"],
        hours=1, max_daily=1, max_total=1, max_groups=0))
    assert result["ok"] is False and delegation.active(service) is None and not service.line.calls


def test_real_mcp_session_uses_saved_delegation_without_any_elicitation(tmp_path, monkeypatch):
    key = Fernet.generate_key()
    service = AgentService(Store(tmp_path / "synthetic", key), FakeLine(5))
    enroll(service, monkeypatch)
    async def scenario():
        params = StdioServerParameters(command=sys.executable,
            args=[str(Path(__file__).with_name("fixture_server.py"))],
            env={**os.environ, "TEST_KEY": key.decode(), "TEST_STORE": str(service.store.path),
                 "TEST_CONFIRMATION": "owner-delegation", "TEST_CONTACT_COUNT": "5"})
        # No callback is installed that could produce synthetic host acceptance.
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                names = {t.name for t in (await client.list_tools()).tools}
                assert "delegation_status" in names and "delegation_revoke" in names
                assert not any("enroll" in name or "approve" in name for name in names)
                row = (await client.call_tool("action_prepare", {"kind": "send_text",
                    "payload": {"to": service.line.ids[0], "text": "架空の通常操作"}})).structuredContent
                result = (await client.call_tool("action_execute", {"action_id": row["id"]})).structuredContent
                assert result["state"] == "sent" and result["authority"] == "owner_client_delegation"
                await client.call_tool("delegation_revoke")
                row = (await client.call_tool("action_prepare", {"kind": "send_text",
                    "payload": {"to": service.line.ids[0], "text": "失効後"}})).structuredContent
                result = (await client.call_tool("action_execute", {"action_id": row["id"]})).structuredContent
                assert result["error"] == "delegation_revoked" and result["executed"] is False
    asyncio.run(scenario())


def test_remote_connection_cannot_use_local_grant(service, monkeypatch):
    enroll(service, monkeypatch)
    async def scenario():
        server = create_server(service, public_url="https://example.com", confirmation="owner-delegation")
        saved = prepared(service)
        result = await server.call_tool("action_execute", {"action_id": saved["id"]})
        assert result[1]["error"] == "delegation_connection_unsupported"
    asyncio.run(scenario())
    assert not service.line.calls


def test_internal_denial_survives_real_adapter_boundary(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from line_agent.line import LineAdapter
    store = Store(tmp_path / "synthetic-adapter", Fernet.generate_key())
    owner = "u" + "a" * 32
    store.put("setting", "identity", {"mid": owner})
    store.put("credential", "session", {"mid": owner})
    adapter = LineAdapter(store)
    monkeypatch.setattr(adapter, "_client", lambda s: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(adapter, "_save", lambda api: None)
    def deny(api):
        raise delegation.DelegationDenied("delegation_revoked")
    with pytest.raises(delegation.DelegationDenied, match="delegation_revoked"):
        adapter.call(deny)
    assert delegation.DelegationDenied("secret error or arbitrary upstream text").reason == "delegation_invalid_state"


def test_request_key_reconnect_replay_and_conflict_use_one_action(service, monkeypatch):
    principal = enroll(service, monkeypatch)
    request_id = str(uuid.uuid4())
    payload = {"to": service.line.ids[0], "text": "架空"}
    with ThreadPoolExecutor(max_workers=4) as pool:
        actions = list(pool.map(lambda _: service.prepare_once(request_id, "send_text", payload), range(4)))
    assert len({a["id"] for a in actions}) == 1
    assert service.execute(actions[0]["id"], delegation_principal=principal)["state"] == "sent"
    assert service.prepare_once(request_id, "send_text", payload)["state"] == "sent"
    with pytest.raises(ValueError):
        service.prepare_once(request_id, "send_text", {**payload, "text": "別内容"})
    assert len(service.line.calls) == 1


def test_operation_run_returns_same_receipt_after_mcp_reconnect(tmp_path, monkeypatch):
    key = Fernet.generate_key()
    service = AgentService(Store(tmp_path / "synthetic-operation", key), FakeLine(5))
    enroll(service, monkeypatch)
    request_id = str(uuid.uuid4())
    args = {"request_id": request_id, "kind": "send_text",
            "payload": {"to": service.line.ids[0], "text": "架空の一度だけの依頼"}}
    async def roundtrip(replayed):
        params = StdioServerParameters(command=sys.executable,
            args=[str(Path(__file__).with_name("fixture_server.py"))],
            env={**os.environ, "TEST_KEY": key.decode(), "TEST_STORE": str(service.store.path),
                 "TEST_CONFIRMATION": "owner-delegation", "TEST_CONTACT_COUNT": "5"})
        async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
            await client.initialize()
            result = (await client.call_tool("operation_run", args)).structuredContent
            assert result["state"] == "sent" and result["already_executed"] is replayed
            assert result["external_id"] == "external-message-1"
            status = (await client.call_tool("operation_status", {"request_id":request_id})).structuredContent
            assert status["action_id"] == result["action_id"]
            return result["action_id"]
    first = asyncio.run(roundtrip(False))
    assert asyncio.run(roundtrip(True)) == first
    assert delegation.active(service)["used_total"] == 1


def test_internal_client_preserves_request_key_on_start_failure(monkeypatch, tmp_path):
    from line_agent import owner_client
    def failed(*args):
        raise OSError("private diagnostic that must not be returned")
    monkeypatch.setattr(owner_client, "stdio_client", failed)
    request_id = str(uuid.uuid4())
    result = asyncio.run(owner_client.request(tmp_path, "synthetic", request_id, "send_text", {}))
    assert result["request_id"] == request_id and result["executed"] is None
    assert result["stage"] == "mcp_start" and result["next_action"] == "operation_status"
    assert "private" not in str(result)


def test_first_unknown_receipt_is_explicit_not_success(service, monkeypatch):
    enroll(service, monkeypatch)
    service.line.fail_send = True
    request_id = str(uuid.uuid4())
    async def scenario():
        server = create_server(service, confirmation="owner-delegation")
        args = {"request_id": request_id, "kind": "send_text",
                "payload": {"to": service.line.ids[0], "text": "架空"}}
        first = (await server.call_tool("operation_run", args))[1]
        again = (await server.call_tool("operation_run", args))[1]
        assert first["state"] == again["state"] == "unknown"
        assert first["ok"] is again["ok"] is False
        assert first["error"] == again["error"] == "operation_result_unknown"
        assert first["executed"] is again["executed"] is None
    asyncio.run(scenario())
    assert len(service.line.calls) == 1


def test_unknown_retains_closed_failure_phase_and_code(service, monkeypatch):
    from line_agent.diagnostics import DiagnosticError
    principal = enroll(service, monkeypatch)
    original = service.line.execute
    def fail(kind, payload, *, preflight):
        original(kind, payload, preflight=preflight)
        raise DiagnosticError("group_key_register", "line_api_rejected", http_status=400, line_code=99)
    monkeypatch.setattr(service.line, "execute", fail)
    saved = prepared(service)
    result = service.execute(saved["id"], delegation_principal=principal)
    assert result["state"] == "unknown"
    assert result["diagnostic"] == {"stage":"group_key_register", "reason":"line_api_rejected",
                                     "http_status":400, "line_code":99}


def test_live_contact_identity_must_match_before_cache_update(service, monkeypatch):
    mid = service.line.ids[0]
    async def scenario():
        server = create_server(service, confirmation="owner-delegation")
        row = {"mid":mid,"displayName":"架空の最新名","displayNameOverridden":"架空の呼び名"}
        monkeypatch.setattr(service.line, "contacts", lambda ids: [row])
        assert (await server.call_tool("contact_get", {"mid":mid}))[1] == row
        assert service.store.get("contact", mid) == row
        monkeypatch.setattr(service.line, "contacts", lambda ids: [{"mid":service.line.ids[1]}])
        assert (await server.call_tool("contact_get", {"mid":mid}))[1]["ok"] is False
        assert service.store.get("contact", mid) == row
    asyncio.run(scenario())
