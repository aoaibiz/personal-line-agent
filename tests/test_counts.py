from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from line_agent.line import LineAdapter
from line_agent.store import Store


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    line = LineAdapter(Store(tmp_path / "synthetic", Fernet.generate_key()))
    line.api = SimpleNamespace()
    monkeypatch.setattr(line, "call", lambda fn: fn(line.api))
    return line


def user(n):
    return f"u{n:032x}"


def test_unread_filters_zero_but_advances_using_raw_page(adapter):
    adapter.api.get_message_boxes = lambda **kw: {
        "messageBoxes": [{"id": user(1), "unreadCount": "2"}, {"id": user(2), "unreadCount": "0"}],
        "hasNext": True,
    }
    result = adapter.unread()
    assert result["items"] == [{"id": user(1), "unreadCount": 2}]
    assert result["next_cursor"] == user(2)


def configure_counts(adapter):
    ids = [user(i) for i in range(5)]
    adapter.api.get_all_contact_ids = lambda: ids + [ids[0]]
    adapter.api.get_contacts = lambda batch: {
        "contacts": {
            mid: {
                "contact": {
                    "status": 1,
                    "settings": "6" if mid in ids[2:4] else "0",
                    "capableBuddy": mid in ids[3:],
                }
            }
            for mid in batch
        }
    }
    group = "c" + "a" * 32
    adapter.api.get_all_chat_mids = lambda: {
        "memberChatMids": [group, group, "r" + "b" * 32],
        "invitedChatMids": [],
    }
    calls = []

    def boxes(**kw):
        calls.append(kw)
        if kw["min_chat_id"] is None:
            return {
                "messageBoxes": [
                    {"id": user(1), "unreadCount": "2"},
                    {"id": user(2), "unreadCount": "0"},
                ],
                "hasNext": True,
            }
        return {
            "messageBoxes": [
                {"id": user(1), "unreadCount": "2"},
                {"id": user(3), "unreadCount": "1"},
            ],
            "hasNext": False,
        }

    adapter.api.get_message_boxes = boxes
    return calls


def test_live_counts_separate_visible_official_hidden_rooms_and_deduplicate(adapter):
    calls = configure_counts(adapter)
    result = adapter.account_counts()
    assert result["contacts"] == {
        "all_contacts": 5,
        "visible_friends": 2,
        "hidden_friends": 1,
        "official_accounts": 2,
        "hidden_official_accounts": 1,
        "other_status": 0,
    }
    assert result["groups"] == {"joined": 1, "invited": 0, "joined_rooms": 1}
    assert result["unread"]["complete"] is True
    assert result["unread"]["messages"] == 3
    assert result["unread"]["chats"] == 2
    assert len(calls) == 2


def test_page_cap_does_not_claim_partial_is_total(adapter):
    configure_counts(adapter)
    result = adapter.account_counts(max_unread_pages=1)
    assert result["unread"]["complete"] is False
    assert result["unread"]["messages"] is None
    assert result["unread"]["observed_messages"] == 2


@pytest.mark.parametrize("value", [None, "-1", True, "invalid", 1.5])
def test_bad_unread_counts_fail_instead_of_becoming_zero(adapter, value):
    adapter.api.get_message_boxes = lambda **kw: {
        "messageBoxes": [{"id": user(1), "unreadCount": value}],
        "hasNext": False,
    }
    with pytest.raises(ValueError):
        adapter.unread()


def test_missing_contact_metadata_does_not_guess_visible(adapter):
    configure_counts(adapter)
    adapter.api.get_contacts = lambda ids: {
        "contacts": {mid: {"contact": {"status": 1, "capableBuddy": False}} for mid in ids}
    }
    with pytest.raises(ValueError):
        adapter.account_counts()


def test_stalled_pagination_is_rejected(adapter):
    configure_counts(adapter)
    adapter.api.get_message_boxes = lambda **kw: {
        "messageBoxes": [{"id": user(1), "unreadCount": "1"}],
        "hasNext": True,
    }
    with pytest.raises(ValueError):
        adapter.account_counts()


def test_later_zero_count_replaces_earlier_unread_observation(adapter):
    configure_counts(adapter)

    def boxes(**kw):
        if kw["min_chat_id"] is None:
            return {"messageBoxes": [{"id": user(1), "unreadCount": "2"}], "hasNext": True}
        return {"messageBoxes": [{"id": user(1), "unreadCount": "0"}], "hasNext": False}

    adapter.api.get_message_boxes = boxes
    counts = adapter.account_counts()["unread"]
    assert counts["complete"] is True
    assert counts["messages"] == 0
    assert counts["chats"] == 0
