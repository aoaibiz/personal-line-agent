"""Read-only SSE processing. Cursor acknowledgement follows durable storage."""
from __future__ import annotations

import time
from uuid import uuid4

from .diagnostics import REASONS, DiagnosticError
from .identifiers import id_kind


class ReceiveFailure(DiagnosticError):
    def __init__(self, reason):
        super().__init__("event_receive", reason if reason in REASONS else "unexpected_error")


def revision(value):
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and value.isdecimal() and len(value) <= 20:
        return int(value)
    raise ReceiveFailure("invalid_revision")


def event_messages(service, event):
    """Called only after SDK strips credentials/chunks and decrypts message bodies."""
    name, data = event["event"], event.get("data")
    now = time.time()
    if name in {"opened", "ping", "connInfoRevision", "reconnect"}:
        service.store.put("setting", "receiver", {
            **(service.store.get("setting", "receiver") or {}),
            "state": "listening", "mode": "events", "checked_at": now,
            "last_signal": name,
        })
        return {"new": 0}
    if name in {"fullSync", "partialFullSync", "talkException"}:
        # A sync reset may skip operations. Never silently acknowledge its cursor.
        if name == "talkException":
            raise ReceiveFailure("server_event_exception")
        cursor = service.store.get("setting", "event_cursor")
        if not isinstance(data, dict) or "nextRevision" not in data or not cursor:
            raise ReceiveFailure("sync_cursor_unverified")
        if revision(data["nextRevision"]) > revision(cursor["revision"]):
            raise ReceiveFailure("resync_required")
        return {"new": 0}
    if name != "message":
        raise ReceiveFailure("unsupported_event")
    operations = data.get("operations") if isinstance(data, dict) else data
    if isinstance(data, dict) and "type" in data:
        operations = [data]
    if not isinstance(operations, list) or any(not isinstance(op, dict) for op in operations):
        raise ReceiveFailure("invalid_operation_batch")
    ordered = sorted(operations, key=lambda op: revision(op.get("revision")))
    revisions = [revision(op.get("revision")) for op in ordered]
    if len(set(revisions)) != len(revisions):
        raise ReceiveFailure("duplicate_operation_revision")
    identity = service.store.get("setting", "identity") or {}
    owner = identity.get("mid")
    if not owner:
        raise ReceiveFailure("missing_account_identity")
    received = 0
    with service.store.atomic() as tx:
        saved = tx.get("setting", "event_cursor")
        if not saved:
            raise ReceiveFailure("missing_resume_cursor")
        cursor = revision(saved["revision"])
        for op in ordered:
            rev = revision(op["revision"])
            if rev <= cursor:
                continue
            typ = revision(op.get("type"))
            if typ in {25, 26}:
                msg = op.get("message")
                if (not isinstance(msg, dict)
                    or type(msg.get("id")) not in (str, int)
                    or not str(msg["id"]).strip()
                    or (type(msg["id"]) is int and msg["id"] < 0)):
                    raise ReceiveFailure("invalid_message_event")
                sender, recipient = msg.get("from"), msg.get("to")
                try:
                    sender_kind = id_kind(sender)
                except ValueError:
                    raise ReceiveFailure("invalid_sender") from None
                if sender_kind != "u":
                    raise ReceiveFailure("invalid_sender")
                try:
                    target_kind = id_kind(recipient)
                except ValueError:
                    raise ReceiveFailure("invalid_recipient") from None
                if target_kind == "u" and sender != owner and recipient != owner:
                    raise ReceiveFailure("unexpected_direct_recipient")
                chat = recipient if target_kind in {"c", "r"} or sender == owner else sender
                message_id = str(msg["id"])
                if len(message_id) > 256:
                    raise ReceiveFailure("invalid_message_id")
                key = f"{chat}:{message_id}"
                tx.put("message", key, {**msg, "chat": chat}, scope=chat)
                incoming = typ == 26 and sender != owner
                if incoming and tx.get("delivery", key) is None:
                    tx.put("delivery", key, {"revision": rev, "received_at": now}, scope=chat)
                    old = tx.get("inbox", chat) or {"message_ids": []}
                    ids = list(dict.fromkeys(old["message_ids"] + [message_id]))[-2000:]
                    action_id = old.get("action_id")
                    action = tx.get("action", action_id) if action_id else None
                    if action and action.get("state") in {"sent", "stale", "cancelled"}:
                        action_id = None
                    tx.put("inbox", chat, {**old, "chat": chat, "message_ids": ids,
                        "latest_ids": [message_id], "latest_message_id": message_id,
                        "updated_at": now, "pending": True, "action_id": action_id})
                    tx.put("classification_job", chat, {"chat": chat, "state": "pending",
                        "latest_message_id": message_id, "updated_at": now}, scope=chat)
                    received += 1
            cursor = rev
        tx.put("setting", "event_cursor", {**saved, "revision": cursor, "saved_at": now})
        old_status = tx.get("setting", "receiver") or {}
        tx.put("setting", "receiver", {**old_status, "mode": "events", "state": "listening",
            "checked_at": now, "last_commit_at": now,
            "new_messages": old_status.get("new_messages", 0) + received})
    return {"new": received}


def run_events(service, duration=None):
    """Foreground SSE worker; never executes outgoing actions or templates."""
    if duration is not None and not 1 <= duration <= 86400:
        raise ValueError("durationは1〜86400秒です")
    started = time.monotonic()
    deadline = started + duration if duration is not None else None
    operation_id = uuid4().hex[:16]
    total, failures = 0, 0
    with service.store.exclusive("worker", blocking=False):
        service.store.put("setting", "receiver", {"state": "starting", "mode": "events",
            "operation_id": operation_id, "started_at": time.time(), "new_messages": 0})
        try:
            while not service.paused() and (deadline is None or time.monotonic() < deadline):
                remaining = min(30.0, deadline - time.monotonic()) if deadline else 20.0
                def accept(event):
                    nonlocal total
                    if service.paused():
                        raise ReceiveFailure("paused")
                    result = event_messages(service, event)
                    total += result["new"]
                try:
                    service.line.receive_events(accept, remaining)
                    failures = 0
                except DiagnosticError as error:
                    if error.reason == "paused":
                        break
                    status = service.store.get("setting", "receiver") or {}
                    service.store.put("setting", "receiver", {**status, "state": "error",
                        "reason": error.reason, "failure_stage": error.stage,
                        "failed_at": time.time()})
                    raise
                except Exception as error:
                    from okline.exceptions import LineTransportError
                    from requests.exceptions import RequestException

                    failures += 1
                    transient = isinstance(error, (LineTransportError, RequestException))
                    status = service.store.get("setting", "receiver") or {}
                    delay = min(2 ** min(failures, 6), 60)
                    if deadline:
                        delay = min(delay, max(0, deadline - time.monotonic()))
                    service.store.put("setting", "receiver", {**status, "state": "reconnecting",
                        "reason": "stream_transport_failed", "failures": failures,
                        "failed_at": time.time(), "retry_delay_seconds": delay,
                        "next_retry_at": time.time()+delay})
                    if failures >= 3 and (deadline or not transient):
                        service.store.put("setting", "receiver", {**status, "state": "error",
                            "reason": "stream_transport_failed", "failed_at": time.time()})
                        raise ReceiveFailure("stream_transport_failed") from None
                    remaining_delay = delay
                    while remaining_delay > 0 and not service.paused():
                        step = min(1, remaining_delay)
                        time.sleep(step)
                        remaining_delay -= step
        finally:
            status = service.store.get("setting", "receiver") or {}
            service.store.put("setting", "receiver", {**status,
                "state": status.get("state") if status.get("state") == "error" else "stopped",
                "stopped_at": time.time()})
    return {"state": "stopped", "mode": "events", "new": total,
        "operation_id": operation_id, "duration_seconds": time.monotonic() - started}
