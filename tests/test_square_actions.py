import uuid

import pytest

from line_agent.delegation import configure_owner, default_scope, local_principal

TARGET = "m" + "1" * 32


def ready(service):
    service.store.put("setting", "identity", {"mid": service.line.ids[0]})
    service.line.square_snapshot = lambda kind, payload: {
        "target": payload["target"],
        "member": "p" + "2" * 32,
        "revision": "1",
    }


def test_ordinary_all_targets_does_not_authorize_square(service):
    ready(service)
    scope = default_scope()
    configure_owner(service, scope)
    action = service.prepare("square_send_text", {"target": TARGET, "text": "synthetic"})
    with pytest.raises(ValueError, match="target_out_of_scope"):
        service.execute(action["id"], delegation_principal=local_principal(service))
    assert not service.line.calls


def test_square_specific_scope_and_unknown_not_replayed(service):
    ready(service)
    scope = default_scope()
    scope["square_targets"] = [TARGET]
    configure_owner(service, scope)
    request = str(uuid.uuid4())
    payload = {"target": TARGET, "text": "synthetic"}
    action = service.prepare_once(request, "square_send_text", payload)
    service.line.fail_send = True
    assert (
        service.execute(action["id"], delegation_principal=local_principal(service))["state"]
        == "unknown"
    )
    assert service.prepare_once(request, "square_send_text", payload)["id"] == action["id"]
    with pytest.raises(ValueError):
        service.execute(action["id"], delegation_principal=local_principal(service))
    assert len(service.line.calls) == 1


def test_square_target_namespace_and_explicit_block(service):
    ready(service)
    for target in [service.line.ids[0], "s" + "2" * 32, "c" + "3" * 32]:
        with pytest.raises(ValueError):
            service.prepare("square_send_text", {"target": target, "text": "x"})
    scope = default_scope()
    scope["square_targets"] = [TARGET]
    scope["blocked_square_targets"] = [TARGET]
    configure_owner(service, scope)
    action = service.prepare("square_leave_chat", {"target": TARGET})
    with pytest.raises(ValueError, match="target_blocked"):
        service.execute(action["id"], delegation_principal=local_principal(service))
    assert not service.line.calls


def test_create_only_scope_and_owner_binding(service):
    ready(service)
    owner = service.line.ids[0]
    service.line.public_profile = lambda: {"mid": owner}
    service.line.square_snapshot = lambda *a: {"owner": "synthetic"}
    scope = default_scope()
    scope.update(
        all_targets=False,
        targets=[],
        square_targets=[],
        allow_square_create=True,
        kinds=["square_create"],
    )
    configure_owner(service, scope)
    action = service.prepare(
        "square_create", {"mid": owner, "name": "synthetic", "display_name": "owner"}
    )
    assert (
        service.execute(action["id"], delegation_principal=local_principal(service))["state"]
        == "sent"
    )
    with pytest.raises(ValueError, match="owner_mismatch"):
        service.prepare(
            "square_create",
            {"mid": service.line.ids[1], "name": "synthetic", "display_name": "owner"},
        )


def test_community_invitee_scopes_are_separate_and_old_grants_deny(service):
    ready(service)
    community = "s" + "3" * 32
    invitee = "u" + "4" * 32
    scope = default_scope()
    scope["square_targets"] = [TARGET]
    configure_owner(service, scope)
    for kind, payload in [
        ("square_leave", {"target": community}),
        ("square_invite", {"target": TARGET, "invitees": [invitee]}),
    ]:
        action = service.prepare(kind, payload)
        with pytest.raises(ValueError, match="target_out_of_scope"):
            service.execute(action["id"], delegation_principal=local_principal(service))
    scope.update(square_community_targets=[community], square_invitee_targets=[invitee])
    configure_owner(service, scope)
    action = service.prepare("square_leave", {"target": community})
    assert (
        service.execute(action["id"], delegation_principal=local_principal(service))["state"]
        == "sent"
    )
    scope["blocked_square_invitee_targets"] = [invitee]
    configure_owner(service, scope)
    action = service.prepare("square_invite", {"target": TARGET, "invitees": [invitee]})
    with pytest.raises(ValueError, match="target_blocked"):
        service.execute(action["id"], delegation_principal=local_principal(service))


def test_real_adapter_staged_media_dispatch_preserves_payload(service, tmp_path, monkeypatch):
    import base64
    from types import SimpleNamespace

    from line_agent import additional_connection
    from line_agent.line import LineAdapter
    from line_agent.media import stage_file

    ready(service)
    path = tmp_path / "synthetic.bin"
    path.write_bytes(b"synthetic")
    media = stage_file(service.store, path)
    action = service.prepare(
        "square_send_media", {"target": TARGET, "media_id": media["id"], "media_type": "image"}
    )
    adapter = LineAdapter(service.store)
    api = SimpleNamespace(tokens=SimpleNamespace(mid=service.line.ids[0]))
    adapter.call = lambda callback: callback(api)
    adapter.square_snapshot = service.line.square_snapshot
    seen = []

    def invoke(store, command, owner, **args):
        assert command == "square_execute"
        assert base64.b64decode(args["payload"]["data"]) == b"synthetic"
        seen.append(args)
        return {"id": "123"}

    monkeypatch.setattr(additional_connection, "invoke", invoke)
    assert adapter.execute(action["kind"], action["payload"], preflight=lambda c: None) == {
        "id": "123"
    }
    assert len(seen) == 1
    assert "data" not in action["payload"]
