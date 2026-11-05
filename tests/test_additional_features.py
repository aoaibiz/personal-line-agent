import pytest

from line_agent import additional_features as f
from line_agent.additional_connection import AdditionalFailure


def test_joined_openchat_pages_and_namespace(service, monkeypatch):
    rows = {
        "squares": [
            {"mid": "s" + "1" * 32, "name": "synthetic", "accessToken": "synthetic-secret"}
        ],
        "continuationToken": "page2",
    }
    monkeypatch.setattr(f, "invoke", lambda *a, **k: rows)
    result = f.openchat_list(service)
    assert not result["complete"] and result["next_cursor"] == "page2"
    assert result["items"] == [{"mid": "s" + "1" * 32, "name": "synthetic"}]
    rows["squares"][0]["mid"] = "c" + "1" * 32
    assert not f.openchat_list(service)["ok"]


def test_failure_not_empty_and_unsupported_never_called(service, monkeypatch):
    calls = []

    def fail(*a, **k):
        calls.append(a)
        raise AdditionalFailure("additional_authentication", "upstream_failed", {"line_code": 8})

    monkeypatch.setattr(f, "invoke", fail)
    r = f.openchat_list(service)
    assert not r["ok"] and r["data"] is None and r["line_code"] == 8
    with pytest.raises(ValueError):
        f.call(service, "arbitrary_rpc")
    with pytest.raises(ValueError):
        f.openchat_list(service, "other")
    assert len(calls) == 1
    service.pause()
    with pytest.raises(ValueError):
        f.openchat_list(service)
    assert len(calls) == 1


def test_details_require_recent_owner_reference(service, monkeypatch):
    import time

    target = "m" + "1" * 32
    service.store.put("setting", "identity", {"mid": "u" + "1" * 32})
    with pytest.raises(ValueError, match="openchat_list_required"):
        f.openchat_read(service, target)
    service.store.put(
        "openchat_reference", target, {"owner": "u" + "1" * 32, "expires_at": time.time() + 60}
    )
    monkeypatch.setattr(
        f,
        "invoke",
        lambda *a, **k: {
            "squareChat": {"squareChatMid": target, "name": "synthetic", "token": "secret"}
        },
    )
    assert f.openchat_read(service, target)["items"] == [
        {"squareChatMid": target, "name": "synthetic"}
    ]
    service.store.put("setting", "identity", {"mid": "u" + "2" * 32})
    with pytest.raises(ValueError, match="openchat_list_required"):
        f.openchat_read(service, target)


def test_messages_project_only_matching_chat_and_never_complete(service, monkeypatch):
    import time

    target = "m" + "1" * 32
    owner = "u" + "1" * 32
    service.store.put("setting", "identity", {"mid": owner})
    service.store.put(
        "openchat_reference", target, {"owner": owner, "expires_at": time.time() + 60}
    )
    data = {
        "messages": [
            {"id": "1", "to": target, "text": "synthetic", "contentMetadata": {"token": "secret"}}
        ]
    }
    monkeypatch.setattr(f, "invoke", lambda *a, **k: data)
    result = f.openchat_read(service, target, "messages")
    assert result["ok"] and not result["complete"]
    assert "contentMetadata" not in result["items"][0]
    data["messages"][0]["to"] = "m" + "2" * 32
    assert not f.openchat_read(service, target, "messages")["ok"]


def test_event_pagination_keeps_both_tokens_and_binds_handle(service, monkeypatch):
    import time

    target = "m" + "1" * 32
    owner = "u" + "1" * 32
    service.store.put("setting", "identity", {"mid": owner})
    service.store.put(
        "openchat_reference", target, {"owner": owner, "expires_at": time.time() + 60}
    )
    calls = []
    rows = {"messages": [], "syncToken": "sync1", "continuationToken": "page2"}

    def invoke(*args, **kwargs):
        calls.append(kwargs)
        return rows

    monkeypatch.setattr(f, "invoke", invoke)
    first = f.openchat_read(service, target, "messages")
    assert first["ok"] and first["next_cursor"] not in {"sync1", "page2"}
    rows["syncToken"] = "sync2"
    rows.pop("continuationToken")
    second = f.openchat_read(service, target, "messages", first["next_cursor"])
    assert second["ok"] and not second["complete"]
    assert calls[-1]["sync_token"] == "sync1" and calls[-1]["continuation_token"] == "page2"
    stalled = f.openchat_read(service, target, "messages", second["next_cursor"])
    assert not stalled["ok"] and stalled["reason"] == "cursor_stalled"
    other = "m" + "2" * 32
    service.store.put("openchat_reference", other, {"owner": owner, "expires_at": time.time() + 60})
    with pytest.raises(ValueError, match="openchat_cursor_invalid"):
        f.openchat_read(service, other, "messages", first["next_cursor"])
