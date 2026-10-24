from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from line_agent.line import LineAdapter
from line_agent.store import Store


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    store = Store(tmp_path / "synthetic", Fernet.generate_key())
    line = LineAdapter(store)
    monkeypatch.setattr(line, "call", lambda fn: fn(line.api))
    line.api = SimpleNamespace()
    return line


def test_wire_contacts_and_group_shapes(adapter):
    mid = "u" + "a" * 32
    adapter.api.get_contacts = lambda ids: {
        "contacts": {
            mid: {
                "contact": {
                    "mid": mid,
                    "displayName": "本人の名前",
                    "displayNameOverridden": "自分の呼び名",
                }
            }
        }
    }
    assert adapter.contacts([mid])[0]["displayNameOverridden"] == "自分の呼び名"
    group = "c" + "b" * 32
    adapter.api.get_chats = lambda ids, **kwargs: {
        "chats": [
            {"chatMid": group, "chatName": "会議", "extra": {"groupExtra": {"memberMids": [mid]}}}
        ]
    }
    assert adapter.group(group)["members"] == [mid]


def test_unread_wire_cursor_and_minimal_projection(adapter):
    mid = "u" + "a" * 32
    adapter.api.get_message_boxes = lambda **kwargs: {
        "messageBoxes": [{"id": mid, "unreadCount": 2, "lastMessages": [{"chunks": ["private"]}]}],
        "hasNext": True,
    }
    result = adapter.unread()
    assert result == {"items": [{"id": mid, "unreadCount": 2}], "next_cursor": mid}


def test_failed_decryption_is_explicit_and_never_leaks_ciphertext(adapter):
    adapter.api.get_recent_messages = lambda *args: [
        {"id": "1", "chunks": ["secret"], "contentType": 0}
    ]

    def fail(msg):
        raise ValueError("cannot decrypt")

    adapter.api.decrypt_message = fail
    assert adapter.recent_messages("u" + "a" * 32) == [
        {"id": "1", "contentType": 0, "decrypted": False,
         "decryption_error": {"stage": "message_decrypt", "reason": "message_decryption_failed"}}
    ]


def test_contact_rename_uses_contact_override_and_not_owner_profile(adapter):
    calls = []
    adapter.api.update_contact_setting = lambda *args: calls.append(args)
    adapter.api.get_contacts = lambda ids: {
        "contacts": {mid: {"contact": {"displayNameOverridden": ""}}}
    }
    mid = "u" + "a" * 32
    adapter.execute(
        "rename_contact", {"mid": mid, "name": "新しい呼び名"}, preflight=lambda context: None
    )
    assert calls == [(mid, 2, "新しい呼び名")]


def test_no_raw_sdk_exception_leaves_adapter(tmp_path, monkeypatch):
    store = Store(tmp_path / "synthetic", Fernet.generate_key())
    mid = "u" + "a" * 32
    store.put("credential", "session", {"mid": mid})
    store.put("setting", "identity", {"mid": mid})
    adapter = LineAdapter(store)
    monkeypatch.setattr(adapter, "_client", lambda s: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(adapter, "_save", lambda api: None)

    def fail(api):
        raise RuntimeError("synthetic_secret_should_not_escape")

    from line_agent.diagnostics import DiagnosticError
    with pytest.raises(DiagnosticError) as exc:
        adapter.call(fail)
    assert "synthetic_secret" not in str(exc.value)


def test_pinned_sdk_constructs_without_network_and_disables_recording(tmp_path):
    store = Store(tmp_path / "synthetic", Fernet.generate_key())
    adapter = LineAdapter(store)
    with store.exclusive():
        api = adapter._client()
        try:
            assert api.recorder is None
            assert api.config.max_retries == 0
            assert api.next_req_seq() == 1
            assert api.next_req_seq() == 2
        finally:
            api.close()
    assert store.get("setting", "sequence")["next"] == 3


def test_history_decryption_cannot_register_group_key(adapter):
    registered = []
    def register(*args):
        registered.append(args)
    adapter.api.e2ee = SimpleNamespace(register_group_key=register)
    adapter.api.register_e2ee_group_key = register
    adapter.api.get_recent_messages = lambda *a: [{'id': 'synthetic', 'chunks': ['synthetic']}]
    adapter.api.decrypt_message = lambda msg: adapter.api.e2ee.register_group_key('c'+'a'*32)
    assert adapter.recent_messages('c'+'a'*32)[0]['decrypted'] is False
    assert registered == []
    assert adapter.api.e2ee.register_group_key is register
    assert adapter.api.register_e2ee_group_key is register


@pytest.mark.parametrize("kind", ["send_text", "send_mention"])
def test_group_text_negotiates_encryption_before_first_send(adapter, monkeypatch, kind):
    from line_agent import service
    monkeypatch.setattr(service, "action_context", lambda *a: {})
    calls = []
    adapter.api.send_message = lambda message, **kwargs: calls.append((message, kwargs)) or {"id":"synthetic"}
    group = "C" + "a" * 43
    payload = {"to":group,"text":"架空のグループ文","mention_metadata":{"MENTIONEES":[]}}
    adapter.execute(kind, payload, preflight=lambda context: None)
    assert len(calls) == 1 and calls[0][1] == {"encrypt":True}
    assert calls[0][0]["to"] == group
