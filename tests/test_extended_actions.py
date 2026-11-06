import uuid

import pytest

from line_agent.delegation import configure_owner, default_scope, local_principal
from line_agent.service import MESSAGE_ACTIONS


def fixture(service, *, sender=None):
    owner = service.line.ids[0]
    peer = service.line.ids[1]
    service.store.put("setting", "identity", {"mid": owner})
    service.line.messages = [{"id": "123", "from": sender or owner, "to": peer, "text": "fixture"}]
    return owner, peer


@pytest.mark.parametrize("kind", sorted(MESSAGE_ACTIONS))
def test_message_actions_scope_and_unknown_not_replayed(service, kind):
    _, peer = fixture(service)
    configure_owner(service, default_scope())
    payload = {"to": peer, "message_id": "123"}
    if kind == "react_message":
        payload["reaction"] = 2
    request = str(uuid.uuid4())
    action = service.prepare_once(request, kind, payload)
    service.line.fail_send = True
    result = service.execute(action["id"], delegation_principal=local_principal(service))
    assert result["state"] == "unknown"
    again = service.prepare_once(request, kind, payload)
    assert again["id"] == action["id"]
    with pytest.raises(ValueError):
        service.execute(action["id"], delegation_principal=local_principal(service))
    assert len(service.line.calls) == 1


def test_unsend_other_person_and_wrong_chat_rejected(service):
    owner, peer = fixture(service, sender=service.line.ids[1])
    with pytest.raises(ValueError, match="only_owner"):
        service.prepare("unsend_message", {"to": peer, "message_id": "123"})
    with pytest.raises(ValueError, match="message_chat"):
        service.prepare("mark_read", {"to": service.line.ids[2], "message_id": "123"})
    with pytest.raises(ValueError, match="not_in_recent"):
        service.prepare("mark_read", {"to": peer, "message_id": "999"})


def test_existing_grant_does_not_gain_new_kinds(service):
    _, peer = fixture(service)
    scope = default_scope()
    scope["kinds"] = ["send_text"]
    configure_owner(service, scope)
    action = service.prepare("react_message", {"to": peer, "message_id": "123", "reaction": 2})
    with pytest.raises(ValueError, match="delegation_kind_out_of_scope"):
        service.execute(action["id"], delegation_principal=local_principal(service))
    assert not service.line.calls


def test_reaction_and_boolean_schema(service):
    _, peer = fixture(service)
    for reaction in [0, 1, 8, True, "2"]:
        with pytest.raises(ValueError):
            service.prepare(
                "react_message", {"to": peer, "message_id": "123", "reaction": reaction}
            )
    with pytest.raises(ValueError):
        service.prepare("favorite_contact", {"mid": peer, "enabled": "true"})


@pytest.mark.parametrize(
    "kind,payload",
    [
        ("send_sticker", {"package_id": "1", "sticker_id": "2", "version": 1}),
        ("send_location", {"latitude": 34.0, "longitude": 132.0, "title": "synthetic"}),
        ("send_contact", {"contact_mid": "u" + "4" * 32, "display_name": "synthetic"}),
    ],
)
def test_additional_send_schemas_and_no_grant_expansion(service, kind, payload):
    _, peer = fixture(service)
    scope = default_scope()
    scope["kinds"] = ["send_text"]
    configure_owner(service, scope)
    action = service.prepare(kind, {"to": peer, **payload})
    with pytest.raises(ValueError, match="kind_out_of_scope"):
        service.execute(action["id"], delegation_principal=local_principal(service))
    assert not service.line.calls


def test_bad_location_and_contact_card_denied(service):
    _, peer = fixture(service)
    for lat in [float("nan"), float("inf"), 91, -91, "34"]:
        with pytest.raises(ValueError):
            service.prepare("send_location", {"to": peer, "latitude": lat, "longitude": 132.0})
    with pytest.raises(ValueError):
        service.prepare("send_contact", {"to": peer, "contact_mid": "c" + "3" * 32})
    scope = default_scope()
    scope["blocked_targets"] = [service.line.ids[2]]
    configure_owner(service, scope)
    action = service.prepare("send_contact", {"to": peer, "contact_mid": service.line.ids[2]})
    with pytest.raises(ValueError, match="target_blocked"):
        service.execute(action["id"], delegation_principal=local_principal(service))


def test_profile_owner_binding(service):
    from line_agent.service import action_context

    owner, peer = fixture(service)
    service.line.public_profile = lambda: {"mid": owner, "displayName": "synthetic"}
    with pytest.raises(ValueError, match="profile_owner_mismatch"):
        service.prepare("set_profile_name", {"mid": peer, "name": "bad"})
    action = service.prepare("set_profile_name", {"mid": owner, "name": "synthetic updated"})
    assert action["context"]["profile"]
    with pytest.raises(ValueError, match="profile_owner_mismatch"):
        action_context(
            "set_profile_status", {"mid": owner}, None, None, None, lambda: {"mid": peer}
        )


def test_note_changes_are_part_of_fresh_preflight(service):
    from line_agent.service import action_context

    group = "c" + "2" * 32
    payload = {"group": group, "post_id": "123"}
    for kind in ("note_update", "note_delete"):
        original = action_context(
            kind,
            payload,
            None,
            None,
            service.line.group,
            note=lambda *a: {"contents": {"text": "original"}, "updated_time": 1},
        )
        changed = action_context(
            kind,
            payload,
            None,
            None,
            service.line.group,
            note=lambda *a: {"contents": {"text": "new"}, "updated_time": 2},
        )
        assert original != changed and original["group"] == changed["group"]
        with pytest.raises(ValueError, match="note_read_unavailable"):
            action_context(kind, payload, None, None, service.line.group)


def test_contact_card_allowed_group_participant(service):
    from line_agent.delegation import active, check

    owner, peer = fixture(service)
    group = "c" + "3" * 32
    service.line.group = lambda mid: {"chatMid": mid, "members": {owner: 1, peer: 1}}
    scope = default_scope()
    scope.update(
        all_targets=False,
        targets=[group],
        kinds=["send_contact"],
        group_rules={group: {"participants": [owner, peer], "kinds": ["send_contact"]}},
    )
    configure_owner(service, scope)
    action = service.prepare("send_contact", {"to": group, "contact_mid": peer})
    check(service, active(service), action, local_principal(service))


def test_new_send_profile_and_mute_dispatch(service):
    from types import SimpleNamespace

    from okline.enums import ContactSetting

    from line_agent.line import LineAdapter

    owner, peer = fixture(service)
    calls = []
    api = SimpleNamespace(
        get_recent_messages=lambda *a: [],
        get_profile=lambda: {"mid": owner, "displayName": "synthetic"},
        get_contacts=lambda ids: {"contacts": {ids[0]: {"displayNameOverridden": ""}}},
        send_sticker=lambda *a: calls.append(("sticker", a)),
        send_contact=lambda *a: calls.append(("card", a)),
        send_message=lambda msg, **kw: calls.append(("location", msg, kw)),
        set_display_name=lambda *a: calls.append(("name", a)),
        set_status_message=lambda *a: calls.append(("status", a)),
        update_contact_setting=lambda *a: calls.append(("mute", a)),
    )
    adapter = LineAdapter(service.store)
    adapter.call = lambda f: f(api)
    cases = [
        ("send_sticker", {"to": peer, "package_id": "1", "sticker_id": "2", "version": 1}),
        ("send_contact", {"to": peer, "contact_mid": owner, "display_name": "synthetic"}),
        (
            "send_location",
            {"to": peer, "latitude": 34.0, "longitude": 132.0, "title": "synthetic", "address": ""},
        ),
        ("set_profile_name", {"mid": owner, "name": "synthetic"}),
        ("set_profile_status", {"mid": owner, "text": ""}),
        ("mute_contact", {"mid": peer, "enabled": True}),
    ]
    for kind, payload in cases:
        seen = []
        adapter.execute(kind, payload, preflight=seen.append)
        assert len(seen) == 1
    assert calls[0] == ("sticker", (peer, "1", "2", 1))
    assert calls[1] == ("card", (peer, owner, "synthetic"))
    assert calls[2][2] == {"encrypt": False}
    assert calls[-1] == (
        "mute",
        (peer, int(ContactSetting.CONTACT_SETTING_NOTIFICATION_DISABLE), "true"),
    )
    before = len(calls)

    def denied(_):
        raise ValueError("synthetic stale context")

    with pytest.raises(ValueError):
        adapter.execute("set_profile_name", {"mid": owner, "name": "synthetic"}, preflight=denied)
    assert len(calls) == before


def test_pin_requires_exact_group_message_and_fresh_text(service):
    from line_agent.service import action_context

    group = "c" + "2" * 32
    payload = {"group": group, "message_id": "123"}
    rows = [{"id": "123", "to": group, "text": "original"}]

    def context():
        return action_context("pin_message", payload, lambda *a: rows, None, service.line.group)

    before = context()
    assert before["announcement_text"] == "original"
    rows[0]["text"] = "edited"
    assert context() != before
    rows[0]["to"] = "c" + "3" * 32
    with pytest.raises(ValueError, match="pin_requires_recent_group_text"):
        context()


@pytest.mark.parametrize("kind", ["note_like", "note_comment", "note_share"])
def test_note_interactions_require_fresh_snapshot(service, kind):
    from line_agent.service import action_context

    payload = {"group": "c" + "2" * 32, "post_id": "123"}
    with pytest.raises(ValueError, match="note_read_unavailable"):
        action_context(kind, payload, None, None, service.line.group)
    first = action_context(
        kind, payload, None, None, service.line.group, note=lambda *a: {"revision": 1}
    )
    changed = action_context(
        kind, payload, None, None, service.line.group, note=lambda *a: {"revision": 2}
    )
    assert first != changed


def test_bgm_and_note_schema_bounds():
    from line_agent.service import ACTION_MODELS

    group = "c" + "2" * 32
    with pytest.raises(ValueError):
        ACTION_MODELS["set_chat_bgm"].model_validate({"group": group, "bgm_info": "x" * 2001})
    with pytest.raises(ValueError):
        ACTION_MODELS["note_like"].model_validate(
            {"group": group, "post_id": "123", "reaction": "9999"}
        )
    with pytest.raises(ValueError):
        ACTION_MODELS["note_share"].model_validate(
            {"group": group, "post_id": "123", "to": "c" + "3" * 32}
        )


def test_profile_image_owner_staging_and_validation(service, tmp_path):
    from line_agent.media import profile_image_type, stage_file

    owner, peer = fixture(service)
    service.line.public_profile = lambda: {"mid": owner, "displayName": "synthetic"}
    path = tmp_path / "synthetic.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")
    media = stage_file(service.store, path)
    with pytest.raises(ValueError, match="profile_owner_mismatch"):
        service.prepare("set_profile_image", {"mid": peer, "media_id": media["id"]})
    action = service.prepare("set_profile_image", {"mid": owner, "media_id": media["id"]})
    assert action["payload"]["media"]["sha256"] == media["sha256"]
    for data in [b"", b"x" * 20, b"\x89PNG\r\n\x1a\n" + b"x" * (5 * 1024 * 1024)]:
        with pytest.raises(ValueError):
            profile_image_type(data)
