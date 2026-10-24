"""Synthetic SSE events, encrypted persistence and failure/restart cases."""
import pytest
from cryptography.fernet import Fernet

from line_agent.realtime import ReceiveFailure, event_messages, revision, run_events
from line_agent.store import Store

OWNER = "u" + "f" * 32


def setup(service):
    service.store.put("setting", "identity", {"mid": OWNER})
    service.store.put("setting", "event_cursor", {"revision": 0})
    return service.line.ids[0]


def event(peer, rev=1, message_id="test-1", typ=26, sender=None, recipient=None):
    return {"event": "message", "data": {"operations": [{"revision": rev, "type": typ,
        "message": {"id": message_id, "from": sender or peer, "to": recipient or OWNER,
            "text": "synthetic message", "createdTime": 100, "decrypted": True}}]}}


def test_realtime_is_independent_of_unread_and_deduplicates_after_reopen(service):
    key = Fernet.generate_key()
    service.store = Store(service.store.path / "reopen", key)
    peer = setup(service)
    assert event_messages(service, event(peer))["new"] == 1
    reopened = Store(service.store.path, key)
    service.store = reopened
    assert event_messages(service, event(peer))["new"] == 0
    assert event_messages(service, event(peer, rev=2))["new"] == 0
    assert service.store.count("delivery") == 1
    assert service.store.get("inbox", peer)["pending"] is True
    assert service.store.get("classification_job", peer)["state"] == "pending"
    assert not service.line.calls


def test_cursor_and_all_side_effects_roll_back_on_save_failure(service, monkeypatch):
    peer = setup(service)
    encode = service.store._encode
    def fail(record_id, value):
        if record_id == "event_cursor":
            raise OSError("synthetic disk failure")
        return encode(record_id, value)
    monkeypatch.setattr(service.store, "_encode", fail)
    with pytest.raises(OSError):
        event_messages(service, event(peer))
    assert service.store.get("setting", "event_cursor")["revision"] == 0
    assert service.store.count("message") == service.store.count("delivery") == 0
    assert service.store.get("inbox", peer) is None
    monkeypatch.setattr(service.store, "_encode", encode)
    assert event_messages(service, event(peer))["new"] == 1


def test_bad_second_operation_does_not_commit_first(service):
    peer = setup(service)
    value = event(peer)
    value["data"]["operations"].append({"revision": 2, "type": 26, "message": {}})
    with pytest.raises(ReceiveFailure):
        event_messages(service, value)
    assert service.store.count("message") == 0
    assert service.store.get("setting", "event_cursor")["revision"] == 0


def test_unordered_wire_batch_covers_all_revisions(service):
    peer = setup(service)
    value = event(peer, 2, "second")
    value["data"]["operations"] += event(peer, 1, "first")["data"]["operations"]
    assert event_messages(service, value)["new"] == 2
    assert service.store.get("inbox", peer)["latest_message_id"] == "second"


def test_own_messages_are_context_only_and_never_loop(service):
    peer = setup(service)
    event_messages(service, event(peer, typ=25, sender=OWNER, recipient=peer))
    event_messages(service, event(peer, rev=2, message_id="self", sender=OWNER))
    assert service.store.count("message") == 2
    assert service.store.count("inbox") == 0
    assert not service.line.calls


def test_group_routing_uses_recipient_not_senders_personal_chat(service):
    peer = setup(service)
    group = "c" + "b" * 32
    event_messages(service, event(peer, recipient=group))
    assert service.store.get("inbox", group)
    assert not service.store.get("inbox", peer)


def test_other_accounts_direct_recipient_rejected(service):
    peer = setup(service)
    with pytest.raises(ReceiveFailure):
        event_messages(service, event(peer, recipient="u" + "d" * 32))
    assert service.store.count("message") == 0


def test_sync_reset_is_not_acknowledged_as_full_coverage(service):
    setup(service)
    with pytest.raises(ReceiveFailure):
        event_messages(service, {"event": "fullSync", "data": {"nextRevision": "10"}})
    assert service.store.get("setting", "event_cursor")["revision"] == 0


def test_wire_decimal_revision_supported_but_bool_and_negative_rejected():
    assert revision("123") == 123
    for value in [True, -1, None, {}, "bad"]:
        with pytest.raises(ReceiveFailure):
            revision(value)


def test_duplicate_revision_batch_fails_closed(service):
    peer = setup(service)
    value = event(peer)
    value["data"]["operations"] *= 2
    with pytest.raises(ReceiveFailure):
        event_messages(service, value)
    assert service.store.count("message") == 0


def test_incoming_preserves_unknown_outgoing_action(service):
    peer = setup(service)
    service.store.put("action", "old-action", {"state": "unknown"})
    service.store.put("inbox", peer, {"message_ids": [], "action_id": "old-action"})
    event_messages(service, event(peer))
    assert service.store.get("inbox", peer)["action_id"] == "old-action"
    assert not service.line.calls


def test_exhausted_network_retry_records_error_not_success(service, monkeypatch):
    setup(service)
    def fail(*args):
        raise RuntimeError("synthetic secret must not escape")
    service.line.receive_events = fail
    monkeypatch.setattr("line_agent.realtime.time.sleep", lambda seconds: None)
    with pytest.raises(ReceiveFailure) as failure:
        run_events(service, 5)
    assert failure.value.reason == "stream_transport_failed"
    state = service.store.get("setting", "receiver")
    assert state["state"] == "error"
    assert "synthetic secret" not in str(state)


def test_continuous_receiver_recovers_after_more_than_three_network_failures(service, monkeypatch):
    from okline.exceptions import LineTransportError
    setup(service)
    calls = []
    sleeps = []
    def receive(*args):
        calls.append(1)
        if len(calls) <= 4:
            raise LineTransportError('synthetic private connection details')
        service.pause()
    service.line.receive_events = receive
    monkeypatch.setattr('line_agent.realtime.time.sleep', sleeps.append)
    assert run_events(service)['state'] == 'stopped'
    assert len(calls) == 5 and max(sleeps) <= 1
    assert service.store.count('delivery') == 0 and service.store.count('action') == 0


def test_key_restore_failure_never_starts_a_new_login_or_transient_retry(service, monkeypatch):
    from line_agent.diagnostics import DiagnosticError
    setup(service)
    calls = []
    def receive(*args):
        calls.append(1)
        raise DiagnosticError('e2ee_restore', 'e2ee_restore_failed')
    service.line.receive_events = receive
    monkeypatch.setattr('line_agent.realtime.time.sleep', lambda seconds: pytest.fail('must stop'))
    with pytest.raises(DiagnosticError):
        run_events(service)
    assert len(calls) == 1
    state = service.store.get('setting', 'receiver')
    assert state['reason'] == 'e2ee_restore_failed' and state['failure_stage'] == 'e2ee_restore'


@pytest.mark.parametrize('field,reason', [('from', 'invalid_sender'), ('to', 'invalid_recipient')])
def test_malformed_wire_ids_have_schema_diagnosis(service, field, reason):
    peer = setup(service)
    value = event(peer)
    value['data']['operations'][0]['message'][field] = 'bad'
    with pytest.raises(ReceiveFailure) as error:
        event_messages(service, value)
    assert error.value.reason == reason
    assert service.store.count('message') == 0
