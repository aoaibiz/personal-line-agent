"""Closed read-only LINEJS feature lanes; never raw RPC, token or arbitrary URL."""

from __future__ import annotations

import re
import time
import uuid

from .additional_connection import AdditionalFailure, invoke

READ_COMMANDS = {"communities": "squares", "chats": "square_chats"}
DETAIL_COMMANDS = {
    "community": "square_get",
    "chat": "square_chat_get",
    "members": "square_members",
    "messages": "square_messages",
}


def call(service, command, **params):
    if service.paused():
        raise ValueError("agent_paused")
    owner = (service.store.get("setting", "identity") or {}).get("mid")
    if command not in {
        *READ_COMMANDS.values(),
        *DETAIL_COMMANDS.values(),
        "square_resolve",
        "square_media_read",
        "birthday_profile",
    }:
        raise ValueError("unsupported_read")
    try:
        value = invoke(service.store, command, owner, **params)
    except AdditionalFailure as e:
        return {
            "ok": False,
            "stage": e.stage,
            "reason": e.reason,
            **e.diagnostic,
            "complete": False,
            "data": None,
        }
    if not isinstance(value, dict):
        return {
            "ok": False,
            "stage": "additional_decode",
            "reason": "unexpected_schema",
            "data": None,
        }
    return {"ok": True, "data": value}


def openchat_list(service, kind="communities", cursor=None):
    if kind not in READ_COMMANDS or (
        cursor is not None and (not isinstance(cursor, str) or len(cursor) > 2048)
    ):
        raise ValueError("invalid_openchat_read")
    response = call(service, READ_COMMANDS[kind], cursor=cursor or "")
    if not response["ok"]:
        return response
    data = response["data"]
    field = "squares" if kind == "communities" else "squareChats"
    rows = data.get(field)
    next_cursor = data.get("continuationToken")
    if (
        not isinstance(rows, list)
        or len(rows) > 100
        or (
            next_cursor is not None
            and (not isinstance(next_cursor, str) or len(next_cursor) > 2048)
        )
    ):
        return {
            "ok": False,
            "stage": "openchat_decode",
            "reason": "unexpected_schema",
            "items": None,
        }
    items = []
    id_key = "mid" if kind == "communities" else "squareChatMid"
    prefix = "s" if kind == "communities" else "m"
    for row in rows:
        if not isinstance(row, dict) or not re.fullmatch(
            prefix + r"[0-9a-f]{32}", str(row.get(id_key, ""))
        ):
            return {
                "ok": False,
                "stage": "openchat_decode",
                "reason": "unexpected_schema",
                "items": None,
            }
        items.append(
            {
                key: row[key]
                for key in (id_key, "name", "squareMid", "type", "state", "createdTime")
                if key in row
            }
        )
    owner = (service.store.get("setting", "identity") or {}).get("mid")
    for item in items:
        service.store.put(
            "openchat_reference", item[id_key], {"owner": owner, "expires_at": time.time() + 900}
        )
    return {
        "ok": True,
        "items": items,
        "next_cursor": next_cursor or None,
        "complete": not next_cursor and kind == "communities",
        "source": "joined_communities" if kind == "communities" else "membership_events",
        "untrusted_content": True,
        "identity_namespace": "square",
    }


def pick(row, fields):
    if not isinstance(row, dict):
        raise ValueError("unexpected_schema")
    result = {}
    for key in fields:
        if key in row:
            value = row[key]
            if not (value is None or type(value) in (str, int, bool)):
                raise ValueError("unexpected_schema")
            if isinstance(value, str) and len(value) > 20000:
                raise ValueError("unexpected_schema")
            result[key] = value
    return result


def openchat_read(service, target, kind="chat", cursor=None):
    if (
        kind not in DETAIL_COMMANDS
        or not isinstance(target, str)
        or not re.fullmatch(("s" if kind == "community" else "m") + r"[0-9a-f]{32}", target)
        or (cursor is not None and (not isinstance(cursor, str) or len(cursor) > 2048))
    ):
        raise ValueError("invalid_openchat_read")
    reference = service.store.get("openchat_reference", target) or {}
    owner = (service.store.get("setting", "identity") or {}).get("mid")
    if (
        not owner
        or reference.get("owner") != owner
        or reference.get("expires_at", 0) <= time.time()
    ):
        raise ValueError("openchat_list_required")
    params = {"target": target}
    previous = None
    if kind == "messages":
        if cursor:
            previous = service.store.get("openchat_cursor", cursor) or {}
            if (
                previous.get("owner") != owner
                or previous.get("target") != target
                or previous.get("expires_at", 0) <= time.time()
            ):
                raise ValueError("openchat_cursor_invalid")
            params.update(
                sync_token=previous.get("sync_token"),
                continuation_token=previous.get("continuation_token"),
            )
    else:
        params["cursor"] = cursor or ""
    response = call(service, DETAIL_COMMANDS[kind], **params)
    if not response["ok"]:
        return response
    data = response["data"]
    try:
        next_cursor = data.get("continuationToken")
        sync_token = data.get("syncToken") if kind == "messages" else None
        if sync_token is not None and (not isinstance(sync_token, str) or len(sync_token) > 2048):
            raise ValueError("unexpected_schema")
        if next_cursor is not None and (
            not isinstance(next_cursor, str) or len(next_cursor) > 2048
        ):
            raise ValueError("unexpected_schema")
        if kind in ("community", "chat"):
            key = "square" if kind == "community" else "squareChat"
            id_key = "mid" if kind == "community" else "squareChatMid"
            item = pick(
                data.get(key),
                (
                    id_key,
                    "name",
                    "squareMid",
                    "type",
                    "state",
                    "createdTime",
                    "desc",
                    "memberCount",
                ),
            )
            if item.get(id_key) != target:
                raise ValueError("unexpected_schema")
            items = [item]
        else:
            rows = data.get("squareChatMembers" if kind == "members" else "messages")
            if not isinstance(rows, list) or len(rows) > 100:
                raise ValueError("unexpected_schema")
            fields = (
                ("squareMemberMid", "squareChatMid", "membershipState", "role", "displayName")
                if kind == "members"
                else ("id", "to", "from", "text", "contentType", "createdTime", "relatedMessageId")
            )
            items = [pick(row, fields) for row in rows]
            if kind == "members" and any(
                not re.fullmatch(r"p[0-9a-f]{32}", str(item.get("squareMemberMid", "")))
                or item.get("squareChatMid", target) != target
                for item in items
            ):
                raise ValueError("unexpected_schema")
            if kind == "messages" and any(
                not re.fullmatch(r"[0-9]{1,30}", str(item.get("id", "")))
                or item.get("to") != target
                for item in items
            ):
                raise ValueError("unexpected_schema")
        if kind == "messages" and (sync_token or next_cursor):
            if previous and (previous.get("sync_token"), previous.get("continuation_token")) == (
                sync_token,
                next_cursor,
            ):
                raise ValueError("cursor_stalled")
            handle = str(uuid.uuid4())
            service.store.put(
                "openchat_cursor",
                handle,
                {
                    "owner": owner,
                    "target": target,
                    "expires_at": time.time() + 900,
                    "sync_token": sync_token,
                    "continuation_token": next_cursor,
                },
            )
            next_cursor = handle
        return {
            "ok": True,
            "items": items,
            "next_cursor": next_cursor or None,
            "complete": kind != "messages" and not next_cursor,
            "source": "chat_events" if kind == "messages" else "openchat",
            "identity_namespace": "square",
            "untrusted_content": True,
        }
    except ValueError as error:
        return {
            "ok": False,
            "stage": "openchat_decode",
            "reason": "cursor_stalled" if str(error) == "cursor_stalled" else "unexpected_schema",
            "items": None,
        }


def openchat_search(service, query, cursor=None):
    from .openchat_search import search_public

    return search_public(service, query, cursor)


def openchat_media_get(service, target, message_id, preview=False):
    import base64
    import hashlib
    import os

    from .platform_security import private_directory, private_open, reject_reparse

    if (
        not re.fullmatch(r"m[0-9a-f]{32}", target)
        or not re.fullmatch(r"[0-9]{1,30}", message_id)
        or type(preview) is not bool
    ):
        raise ValueError("invalid_media_request")
    reference = service.store.get("openchat_reference", target) or {}
    owner = (service.store.get("setting", "identity") or {}).get("mid")
    if (
        not owner
        or reference.get("owner") != owner
        or reference.get("expires_at", 0) <= time.time()
    ):
        raise ValueError("openchat_list_required")
    response = call(
        service, "square_media_read", target=target, message_id=message_id, preview=preview
    )
    if not response["ok"]:
        return response
    data = response["data"]
    encoded = data.get("data")
    if not isinstance(encoded, str) or len(encoded) > 36 * 1024 * 1024:
        raise ValueError("invalid_media_response")
    raw = base64.b64decode(encoded, validate=True)
    if not 0 < len(raw) <= 25 * 1024 * 1024:
        raise ValueError("invalid_media_response")
    # Private binary output; no filename/URL from an untrusted message is used.
    folder = service.store.path / "openchat-media"
    reject_reparse(folder)
    private_directory(folder)
    with service.store.exclusive("openchat-media-save"):
        entries = list(folder.iterdir())
        for entry in entries:
            reject_reparse(entry)
        if (
            len(entries) >= 200
            or any(not p.is_file() for p in entries)
            or sum(p.stat().st_size for p in entries) + len(raw) > 256 * 1024 * 1024
        ):
            raise ValueError("media_quota_exceeded")
        path = folder / (uuid.uuid4().hex + ".bin")
        with os.fdopen(
            private_open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
        ) as stream:
            stream.write(raw)
    return {
        "ok": True,
        "path": str(path),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "preview": preview,
        "visual_verified": False,
    }


def openchat_resolve_public(service, public_id):
    if not isinstance(public_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", public_id):
        raise ValueError("invalid_public_id")
    owner = (service.store.get("setting", "identity") or {}).get("mid")
    reference = service.store.get("openchat_public_reference", public_id) or {}
    if (
        not owner
        or reference.get("owner") != owner
        or reference.get("expires_at", 0) <= time.time()
    ):
        raise ValueError("public_search_required")
    from .openchat_search import resolve_ticket
    if service.paused():
        raise ValueError("agent_paused")
    ticket = resolve_ticket(public_id)
    if not ticket["ok"]:
        return ticket
    response = call(service, "square_resolve", invitation_ticket=ticket["ticket"])
    if not response["ok"]:
        return response
    item = pick(response["data"].get("square"), ("mid", "name", "desc", "state"))
    if not re.fullmatch(r"s[0-9a-f]{32}", str(item.get("mid", ""))):
        raise ValueError("unexpected_schema")
    service.store.put(
        "openchat_reference", item["mid"], {"owner": owner, "expires_at": time.time() + 900}
    )
    return {
        "ok": True,
        "item": item,
        "identity_namespace": "square",
        "untrusted_content": True,
        "join_performed": False,
    }
