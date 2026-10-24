import pytest
from test_service import FakeLine

from line_agent.service import validate_mid
from line_agent.worker import receive_once, reply_with_owner_templates


@pytest.mark.parametrize(
    "value", ["u" + "a" * 32, "c" + "b" * 32, "U" + "A" * 43, "C" + "aB_9-" * 8 + "abC"]
)
def test_legacy_and_opaque_line_ids_are_preserved(value):
    assert validate_mid(value) == value


@pytest.mark.parametrize(
    "value",
    ["U" + "A" * 42, "C" + "A" * 44, "X" + "a" * 43, "U" + "/" * 43, "u" + "a" * 32 + ";echo x"],
)
def test_invalid_ids_remain_rejected(value):
    with pytest.raises(ValueError):
        validate_mid(value)


def test_opaque_ids_work_in_sync_groups_rename_and_reply_policy(service):
    user = "U" + "a" * 43
    group = "C" + "a" * 43
    service.line = FakeLine(1)
    service.line.ids = [user]
    assert service.sync_contacts()["done"] == 1
    assert service.contacts()["items"][0]["mid"] == user
    assert (
        service.prepare("rename_contact", {"mid": user, "name": "試験"})["payload"]["mid"] == user
    )
    assert (
        service.prepare("create_group", {"name": "試験", "members": [user]})["kind"]
        == "create_group"
    )
    assert service.prepare("invite", {"group": group, "members": [user]})["kind"] == "invite"
    action = service.prepare(
        "send_mention",
        {"to": group, "text": "@試験", "mentions": [{"mid": user, "start": 0, "end": 3}]},
    )
    assert action["kind"] == "send_mention"
    service.set_policy_locally(user, ["確認します"], max_daily=1, hours=1)
    service.line.messages = [{"id": "100", "from": user, "text": "試験の受信"}]
    service.line.unread = lambda *args: {
        "items": [{"id": user, "unreadCount": 1}],
        "next_cursor": None,
    }
    receive_once(service)
    reply_with_owner_templates(service)
    assert len(service.line.calls) == 1
