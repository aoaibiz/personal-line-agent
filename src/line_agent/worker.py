"""Read-only polling receiver. It never turns LINE messages into commands."""

from __future__ import annotations

import time

from .identifiers import is_user


def receive_once(service):
    if service.paused():
        return {"state": "paused", "new": 0}
    cursor, seen, total = None, set(), 0
    for _ in range(1000):
        page = service.line.unread(cursor, 100)
        for box in page["items"]:
            chat = box["id"]
            if chat in seen:
                continue
            seen.add(chat)
            messages = service.read_messages(chat, 100)["messages"]
            owner_mid = (service.store.get("setting", "identity") or {}).get("mid")
            messages = [m for m in messages if m.get("from") and m["from"] != owner_mid]
            # The inbox is durable; one row per chat, with no duplicate deliveries.
            old = service.store.get("inbox", chat) or {"message_ids": []}
            backfill = None
            if (box.get("unreadCount") or 0) > 100 or old.get("backfill_job_id"):
                backfill = service.sync_history(chat, old.get("backfill_job_id"), max_pages=10)
            known = set(old["message_ids"])
            new_ids = [str(m["id"]) for m in messages if str(m["id"]) not in known]
            total += len(new_ids)
            previous_action = old.get("action_id")
            if previous_action and new_ids and service.action(previous_action)["state"] == "sent":
                previous_action = None
            newest = (
                max(
                    messages,
                    key=lambda message: (
                        int(message.get("deliveredTime") or message.get("createdTime", 0)),
                        int(message["id"]) if str(message["id"]).isdigit() else 0,
                    ),
                )
                if messages
                else None
            )
            service.store.put(
                "inbox",
                chat,
                {
                    "chat": chat,
                    "message_ids": list(dict.fromkeys(old["message_ids"] + new_ids))[-2000:],
                    "latest_ids": new_ids,
                    "updated_at": time.time(),
                    "action_id": previous_action,
                    "latest_message_id": str(newest["id"])
                    if newest
                    else old.get("latest_message_id"),
                    "backfill_job_id": backfill["id"] if backfill else None,
                    "backfill_state": backfill["state"] if backfill else None,
                    "pending": old.get("pending", False) or bool(new_ids),
                },
            )
        next_cursor = page.get("next_cursor")
        if not next_cursor:
            break
        if next_cursor == cursor:
            raise ValueError("受信ページが進みません")
        cursor = next_cursor
    else:
        raise ValueError("受信ページ上限です。取得完了ではありません")
    result = {"state": "running", "new": total, "chats": len(seen), "checked_at": time.time()}
    service.store.put("setting", "receiver", result)
    return result


def run_worker(service, interval=15, auto_templates=False):
    """A foreground process: the caller decides when/how to keep it running."""
    with service.store.exclusive("worker", blocking=False):
        return _run_worker(service, interval, auto_templates)


def _run_worker(service, interval, auto_templates):
    if not 5 <= interval <= 3600:
        raise ValueError("受信間隔は5〜3600秒です")
    try:
        while True:
            if service.paused():
                return
            try:
                receive_once(service)
                if auto_templates:
                    reply_with_owner_templates(service)
            except Exception:
                service.store.put(
                    "setting",
                    "receiver",
                    {"state": "error", "error": "receive_failed", "checked_at": time.time()},
                )
            time.sleep(interval)
    finally:
        last = service.store.get("setting", "receiver") or {}
        service.store.put(
            "setting", "receiver", last | {"state": "stopped", "stopped_at": time.time()}
        )


def reply_with_owner_templates(service):
    """Only exact, locally approved text. No LLM or received-text instructions."""
    for row in service.store.records("inbox"):
        inbox = row["value"]
        if not inbox.get("pending") or not is_user(inbox["chat"]):
            continue
        if inbox.get("backfill_state") not in (None, "complete"):
            continue
        policy = service.store.get("policy", inbox["chat"])
        if not policy or policy.get("expires_at", 0) <= time.time():
            continue
        # Persist the action ID before executing. A restart never makes another action for it.
        action_id = inbox.get("action_id")
        if action_id:
            action = service.action(action_id)
            if action["state"] in ("stale", "cancelled") or (
                action["state"] in ("prepared", "authorized")
                and action["expires_at"] <= time.time()
            ):
                action = service.prepare(
                    "send_text",
                    {
                        "to": inbox["chat"],
                        "text": policy["texts"][0],
                        "in_reply_to": inbox["latest_message_id"],
                    },
                )
                inbox["action_id"] = action["id"]
                service.store.put("inbox", inbox["chat"], inbox)
        else:
            action = service.prepare(
                "send_text",
                {
                    "to": inbox["chat"],
                    "text": policy["texts"][0],
                    "in_reply_to": inbox["latest_message_id"],
                },
            )
            inbox["action_id"] = action["id"]
            service.store.put("inbox", inbox["chat"], inbox)
        if action["state"] in ("prepared", "authorized"):
            try:
                result = service.execute(action["id"])
            except ValueError:
                continue
            if result and result["state"] == "sent":
                inbox["pending"] = False
                service.store.put("inbox", inbox["chat"], inbox)
