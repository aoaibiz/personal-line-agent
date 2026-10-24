"""All synthetic: broad capabilities are not unbounded background authority."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from line_agent import delegation
from line_agent.server import create_server
from line_agent.service import ACTION_MODELS


def configure(service, **changes):
    service.store.put("setting", "identity", {"mid": service.line.ids[-1]})
    return delegation.configure_owner(service, {**delegation.default_scope(), **changes})


def test_new_account_defaults_all_current_actions_and_new_targets(service, monkeypatch):
    service.store.put("setting", "identity", {"mid": service.line.ids[-1]})
    assert delegation.initialize_default(service)["initialized"]
    grant = delegation.active(service)
    assert grant["scope"]["all_targets"] and set(grant["scope"]["kinds"]) == set(ACTION_MODELS)
    assert grant["expires_at"] is None and grant["source"] == "authenticated_account_default"
    group = "c" + "c" * 32
    monkeypatch.setattr(service.line, "group", lambda mid: {"chatMid":mid,
        "members":[service.line.ids[-1], service.line.ids[1]],"invitees":[]})
    for target in (service.line.ids[1], "u" + "f" * 32, group):
        action = service.prepare("send_text", {"to":target,"text":"架空の通常操作"})
        assert service.execute(action["id"], delegation_principal=delegation.local_principal(service))["state"] == "sent"
    assert len(service.line.calls) == 3


def test_initialization_preserves_existing_restriction_revocation_and_missing_grant(service):
    configure(service, all_targets=False, targets=[service.line.ids[0]], kinds=["send_text"])
    pointer = service.store.get("setting", "owner_client_delegation")
    assert delegation.initialize_default(service)["initialized"] is False
    assert delegation.active(service)["scope"]["all_targets"] is False
    delegation.revoke(service)
    assert delegation.initialize_default(service)["initialized"] is False
    assert delegation.status(service)["state"] == "revoked"
    service.store.put("setting", "owner_client_delegation", {"id":"synthetic-missing"})
    assert delegation.initialize_default(service)["initialized"] is False
    assert service.store.get("setting", "owner_client_delegation")["id"] != pointer["id"]


def test_default_initializer_uses_atomic_install_without_requests_lock(service, monkeypatch):
    service.store.put("setting", "identity", {"mid": service.line.ids[-1]})
    original = service.store.exclusive
    @contextmanager
    def guarded(name="sdk", blocking=True):
        assert name != "requests", "SDK-held login must not invert requests->SDK lock order"
        with original(name, blocking):
            yield
    monkeypatch.setattr(service.store, "exclusive", guarded)
    with service.store.exclusive("sdk"):
        assert delegation.initialize_default(service)["initialized"]


def test_concurrent_default_and_owner_restriction_keep_owner_setting(service):
    service.store.put("setting", "identity", {"mid": service.line.ids[-1]})
    restricted = {**delegation.default_scope(), "all_targets":False,
                  "targets":[service.line.ids[0]], "kinds":["send_text"]}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(delegation.initialize_default, service) for _ in range(12)]
        futures.append(pool.submit(delegation.configure_owner, service, restricted))
        for future in futures:
            future.result(timeout=5)
    assert delegation.active(service)["scope"]["all_targets"] is False
    assert delegation.active(service)["source"] == "local_owner_configuration"


def test_group_only_restriction_never_grants_participant_direct_messages(service, monkeypatch):
    group = "c" + "d" * 32
    owner, peer = service.line.ids[-1], service.line.ids[1]
    configure(service, all_targets=False, targets=[group],
        group_rules={group:{"participants":[owner,peer],"kinds":["send_text"]}})
    monkeypatch.setattr(service.line, "group", lambda mid: {"chatMid":mid,"members":[owner,peer],"invitees":[]})
    principal = delegation.local_principal(service)
    action = service.prepare("send_text", {"to":group,"text":"グループ内の架空文"})
    assert service.execute(action["id"], delegation_principal=principal)["state"] == "sent"
    action = service.prepare("send_text", {"to":peer,"text":"個人へは不可"})
    with pytest.raises(delegation.DelegationDenied, match="target_out_of_scope"):
        service.execute(action["id"], delegation_principal=principal)
    action = service.prepare("rename_group", {"group":group,"name":"変更不可"})
    with pytest.raises(delegation.DelegationDenied, match="kind_out_of_scope"):
        service.execute(action["id"], delegation_principal=principal)
    assert len(service.line.calls) == 1


def test_explicit_blocks_override_all_targets_and_group_delivery(service, monkeypatch):
    group = "c" + "e" * 32
    owner, peer = service.line.ids[-1], service.line.ids[1]
    configure(service, blocked_targets=[peer], blocked_kinds=["rename_group"])
    principal = delegation.local_principal(service)
    monkeypatch.setattr(service.line, "group", lambda mid: {"chatMid":mid,"members":[owner,peer],"invitees":[]})
    for target in (peer, group):
        action = service.prepare("send_text", {"to":target,"text":"禁止を優先する架空文"})
        with pytest.raises(delegation.DelegationDenied, match="target_blocked"):
            service.execute(action["id"], delegation_principal=principal)
    action = service.prepare("rename_group", {"group":group,"name":"禁止"})
    with pytest.raises(delegation.DelegationDenied, match="kind_blocked"):
        service.execute(action["id"], delegation_principal=principal)
    assert not service.line.calls


def test_group_rule_can_invite_explicitly_allowed_participant_without_dm_access(service, monkeypatch):
    group = "c" + "b" * 32
    owner, current, invited = service.line.ids[-1], service.line.ids[0], service.line.ids[1]
    configure(service, all_targets=False, targets=[group],
              group_rules={group:{"participants":[owner,current,invited],"kinds":["invite"]}})
    monkeypatch.setattr(service.line,"group",lambda mid:{"chatMid":mid,"members":[owner,current],"invitees":[]})
    action = service.prepare("invite", {"group":group,"members":[invited]})
    assert service.execute(action["id"],delegation_principal=delegation.local_principal(service))["state"] == "sent"
    action = service.prepare("send_text", {"to":invited,"text":"個人へは不可"})
    with pytest.raises(delegation.DelegationDenied, match="target_out_of_scope"):
        service.execute(action["id"],delegation_principal=delegation.local_principal(service))


def test_unlimited_counters_do_not_bypass_pause_identity_or_background_boundary(service):
    configure(service)
    grant = delegation.active(service)
    grant["used_total"] = 100000
    service.store.put("delegation", grant["id"], grant)
    action = service.prepare("send_text", {"to":service.line.ids[1],"text":"架空"})
    with pytest.raises(ValueError):
        service.execute(action["id"])
    service.pause()
    with pytest.raises(delegation.DelegationDenied, match="agent_paused"):
        service.execute(action["id"], delegation_principal=delegation.local_principal(service))
    assert not service.line.calls


def test_broad_scope_still_unsupported_over_remote_and_no_mcp_configuration_tool(service):
    configure(service)
    async def scenario():
        server = create_server(service, public_url="https://example.com", confirmation="owner-delegation")
        names = {t.name for t in await server.list_tools()}
        assert not any("configure" in n or "enroll" in n for n in names)
        action = service.prepare("send_text", {"to":service.line.ids[1],"text":"架空"})
        row = (await server.call_tool("action_execute", {"action_id":action["id"]}))[1]
        assert row["error"] == "delegation_connection_unsupported"
    asyncio.run(scenario())
    assert not service.line.calls
