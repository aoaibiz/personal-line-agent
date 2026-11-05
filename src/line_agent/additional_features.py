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
    if command not in {*READ_COMMANDS.values(), *DETAIL_COMMANDS.values()}:
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
