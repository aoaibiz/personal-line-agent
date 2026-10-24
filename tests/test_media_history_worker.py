import pytest

from line_agent.media import load_bytes, stage_file
from line_agent.worker import receive_once, reply_with_owner_templates


def test_attachment_snapshot_is_encrypted_and_immutable(service, tmp_path):
    path = tmp_path / "chosen.txt"
    path.write_bytes(b"synthetic attachment data")
    meta = stage_file(service.store, path)
    path.write_bytes(b"changed on disk")
    assert load_bytes(service.store, meta["id"])[0] == b"synthetic attachment data"
    action = service.prepare(
        "send_media", {"to": service.line.ids[0], "media_id": meta["id"], "media_type": "file"}
    )
    assert action["payload"]["media"]["sha256"] == meta["sha256"]
    assert not service.line.calls
    service.store.put("media_chunk", f"{meta['id']}:0", {"data": "ZmFrZQ=="})
    with pytest.raises(ValueError):
        load_bytes(service.store, meta["id"])


def test_sensitive_named_files_are_not_staged(service, tmp_path):
    path = tmp_path / "auth.json"
    path.write_text("synthetic - not real credentials")
    with pytest.raises(ValueError):
        stage_file(service.store, path)


@pytest.mark.parametrize("name", ["pairing-01234567.json", "PAIRING-ABCDEF12.JSON"])
def test_pairing_credentials_cannot_be_staged(service, tmp_path, name):
    path = tmp_path / name
    path.write_text('{"authorization": "Bearer synthetic-only"}')
    with pytest.raises(ValueError):
        stage_file(service.store, path)
    assert service.store.count("media") == 0
    assert service.store.count("media_chunk") == 0


def test_history_resume_stops_on_accessible_boundary_without_claiming_all_history(service):
    mid = service.line.ids[0]
    service.line.messages = [{"id": "10", "createdTime": 100, "text": "first page"}]
    service.line.previous_messages = lambda *args: []
    first = service.sync_history(mid, max_pages=1)
    assert first["state"] == "running"
    assert first["done"] == 1
    second = service.sync_history(mid, first["id"], max_pages=1)
    assert second["state"] == "complete"
    assert second["accessible_history_only"] is True


def test_duplicate_history_page_is_limited_not_infinite(service):
    mid = service.line.ids[0]
    service.line.messages = [{"id": "10", "createdTime": 100, "text": "repeating page"}]
    service.line.previous_messages = lambda *args: service.line.messages
    job = service.sync_history(mid, max_pages=5)
    assert job["state"] == "limited"
    assert job["pages"] == 1


def test_empty_remote_page_does_not_certify_zero_phone_history(service):
    service.line.messages = []
    job = service.sync_history(service.line.ids[0])
    assert job["done"] == 0
    assert job["history_complete"] is False
    assert job["actual_total_message_count"] is None
    assert job["stop_reason"] == "empty_server_page"


def test_empty_remote_page_keeps_existing_saved_count(service):
    mid = service.line.ids[0]
    service.ingest(mid, [{"id": "old", "text": "previously saved"}])
    service.line.messages = []
    job = service.sync_history(mid)
    assert job["done"] == 1
    assert job["history_complete"] is False


def test_legacy_completed_history_job_is_not_presented_as_complete_history(service):
    mid = service.line.ids[0]
    legacy = {
        "id": "legacy-history",
        "kind": "history",
        "chat": mid,
        "state": "complete",
        "done": 0,
        "pages": 0,
        "cursor": None,
    }
    service.store.put("job", legacy["id"], legacy)
    for result in [service.sync_history(mid, legacy["id"]), service.job_status(legacy["id"])]:
        assert result["history_complete"] is False
        assert result["actual_total_message_count"] is None
        assert result["source"] == "line_chrome_gateway"


def test_receiver_deduplicates_and_pause_stops_it(service):
    mid = service.line.ids[0]
    service.line.messages = [{"id": "11", "from": mid, "createdTime": 100, "text": "new"}]
    service.line.unread = lambda *args: {
        "items": [{"id": mid, "unreadCount": 1}],
        "next_cursor": None,
    }
    assert receive_once(service)["new"] == 1
    assert receive_once(service)["new"] == 0
    service.pause()
    assert receive_once(service)["state"] == "paused"
    assert not service.line.calls


def test_rename_restore_preserves_later_owner_edit(service):
    mid = service.line.ids[0]
    action = service.prepare("rename_contact", {"mid": mid, "name": "仕事の友達"})
    service.approve_locally(action["id"])
    service.execute(action["id"])
    service.line.contacts = lambda ids: [{"mid": mid, "displayNameOverridden": "仕事の友達"}]
    undo = service.prepare_undo_rename(action["id"])
    assert undo["payload"]["name"] == ""
    service.line.contacts = lambda ids: [{"mid": mid, "displayNameOverridden": "本人が後で変更"}]
    with pytest.raises(ValueError):
        service.prepare_undo_rename(action["id"])


def test_auto_template_receiver_does_not_loop_on_own_message_or_retry_unknown(service):
    mid = service.line.ids[0]
    service.set_policy_locally(mid, ["確認します"], 5, 1)
    service.line.messages = [{"id": "11", "from": mid, "text": "連絡です"}]
    service.line.unread = lambda *args: {
        "items": [{"id": mid, "unreadCount": 1}],
        "next_cursor": None,
    }
    receive_once(service)
    service.line.fail_send = True
    reply_with_owner_templates(service)
    action_id = service.store.get("inbox", mid)["action_id"]
    assert service.action(action_id)["state"] == "unknown"
    receive_once(service)
    reply_with_owner_templates(service)
    assert service.store.get("inbox", mid)["action_id"] == action_id
    assert len(service.line.calls) == 1


def test_policy_revocation_between_authorization_and_send_is_checked(service):
    mid = service.line.ids[0]
    service.set_policy_locally(mid, ["確認します"], 1, 1)
    action = service.prepare("send_text", {"to": mid, "text": "確認します"})
    service._policy_authorize(action)
    service.store.put("policy", mid, {"expires_at": 0})
    with pytest.raises(ValueError):
        service.execute(action["id"])
    assert not service.line.calls
