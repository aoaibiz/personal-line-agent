"""Bounded album/Note API adapter, derived from pinned LINEJS MIT protocol contracts.

Source: evex-dev/linejs c0f4a21, base/service/moa and base/timeline.
No generic RPC/URL, device replacement, credential export, or automatic retry.
Live transport support is reported separately from implementation.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from urllib.parse import urlencode

from thrift.protocol.TCompactProtocol import TCompactProtocol
from thrift.Thrift import TMessageType, TType
from thrift.transport.TTransport import TMemoryBuffer

from .identifiers import is_group

HOST = "https://legy.line-apps.com"
DIGITS = re.compile(r"[0-9]{1,30}\Z")
OBJECT = re.compile(r"[A-Za-z0-9_-]{1,200}\Z")


class ContentFailure(Exception):
    def __init__(self, stage, reason, *, status=None, code=None):
        self.stage, self.reason = stage, reason
        self.status = status if type(status) is int and 100 <= status <= 599 else None
        self.code = code if type(code) is int and 0 <= code <= 999999 else None


def response_bytes(api, stage, method, path, headers, body=None, limit=2 * 1024 * 1024):
    """Only internal fixed paths reach here; caller strings are validated/encoded first."""
    try:
        response = api.transport._send(
            method,
            HOST + path,
            headers=headers,
            data=body,
            allow_redirects=False,
            stream=True,
            timeout=15,
        )
        try:
            if response.status_code != 200:
                raise ContentFailure(stage, "upstream_http_error", status=response.status_code)
            chunks, total = [], 0
            for chunk in response.iter_content(65536):
                total += len(chunk)
                if total > limit:
                    raise ContentFailure(stage, "response_too_large")
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            response.close()
    except ContentFailure:
        raise
    except Exception as error:
        cause = error.__cause__ or error
        import requests

        reason = (
            "transport_timeout"
            if isinstance(cause, requests.exceptions.Timeout)
            else "transport_tls_error"
            if isinstance(cause, requests.exceptions.SSLError)
            else "transport_failed"
        )
        raise ContentFailure(stage, reason) from None


def channel_request(channel):
    if channel not in ("1375220249", "1341209850"):
        raise ValueError("invalid_channel")
    buffer = TMemoryBuffer()
    p = TCompactProtocol(buffer)
    p.writeMessageBegin("approveChannelAndIssueChannelToken", TMessageType.CALL, 0)
    p.writeStructBegin("args")
    p.writeFieldBegin("channelId", TType.STRING, 1)
    p.writeString(channel)
    p.writeFieldEnd()
    p.writeFieldStop()
    p.writeStructEnd()
    p.writeMessageEnd()
    return buffer.getvalue()


def decode_channel(data):
    """Only consume the known scalar ChannelToken/ChannelException fields."""
    stage = "channel_decode"
    try:
        p = TCompactProtocol(TMemoryBuffer(data))
        name, typ, seq = p.readMessageBegin()
        if name != "approveChannelAndIssueChannelToken" or typ != TMessageType.REPLY or seq != 0:
            raise ContentFailure(stage, "unexpected_schema")
        token, code = None, None
        p.readStructBegin()
        for _ in range(8):
            _, field_type, fid = p.readFieldBegin()
            if field_type == TType.STOP:
                break
            if field_type != TType.STRUCT or fid not in (0, 1):
                raise ContentFailure(stage, "unexpected_schema")
            p.readStructBegin()
            for _ in range(16):
                _, t, f = p.readFieldBegin()
                if t == TType.STOP:
                    break
                if t == TType.STRING:
                    value = p.readString()
                    if len(value) > 16384:
                        raise ContentFailure(stage, "unexpected_schema")
                    if fid == 0 and f == 5:
                        token = value
                elif t == TType.I32:
                    value = p.readI32()
                    if fid == 1 and f == 1:
                        code = value
                elif t == TType.I64:
                    p.readI64()
                elif fid == 1 and f == 3 and t == TType.MAP:
                    kt, vt, n = p.readMapBegin()
                    if kt != TType.STRING or vt != TType.STRING or n > 32:
                        raise ContentFailure(stage, "unexpected_schema")
                    for _ in range(n):
                        p.readString()
                        p.readString()
                    p.readMapEnd()
                else:
                    raise ContentFailure(stage, "unexpected_schema")
                p.readFieldEnd()
            else:
                raise ContentFailure(stage, "unexpected_schema")
            p.readStructEnd()
            p.readFieldEnd()
        else:
            raise ContentFailure(stage, "unexpected_schema")
        if code is not None:
            raise ContentFailure("channel_authorize", "upstream_rejected", code=code)
        if not token:
            raise ContentFailure(stage, "channel_token_missing")
        return token
    except ContentFailure:
        raise
    except Exception:
        raise ContentFailure(stage, "unexpected_schema") from None


class ContentClient:
    def __init__(self, api):
        self.api = api
        self.tokens = {}

    def channel(self, channel):
        if channel in self.tokens:
            return self.tokens[channel]
        if channel == "1341209850":
            try:
                result = self.api.issue_channel_token(channel)
            except Exception as e:
                raise ContentFailure(
                    "note_channel",
                    "upstream_rejected",
                    status=getattr(e, "status", None),
                    code=getattr(e, "code", None),
                ) from None
            token = result.get("channelAccessToken") if isinstance(result, dict) else None
            if not token:
                raise ContentFailure("note_channel", "channel_token_missing")
        else:
            headers = self.api.transport.base_headers(base=HOST, application=True)
            headers.update(
                {"content-type": "application/x-thrift", "accept": "application/x-thrift"}
            )
            token = decode_channel(
                response_bytes(
                    self.api,
                    "album_channel",
                    "POST",
                    "/CH4",
                    headers,
                    channel_request(channel),
                    limit=65536,
                )
            )
        self.tokens[channel] = token
        return token

    def home(self, chat):
        self.channel("1341209850")
        try:
            home = self.api.obs.timeline_home_id(chat)
        except Exception:
            raise ContentFailure("home_resolution", "home_resolution_failed") from None
        if not isinstance(home, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", home):
            raise ContentFailure("home_resolution", "unexpected_schema")
        return home

    def download(self, home, album_id, ref):
        from .received_media import LIMIT

        headers = self.api.transport.base_headers(base=HOST, application=True)
        headers.update(
            {
                "X-Line-ChannelToken": self.channel("1375220249"),
                "X-Line-Album": album_id,
                "X-Line-Mid": home,
            }
        )
        return response_bytes(
            self.api,
            "album_download",
            "POST",
            "/oa/r/album/" + ref["sid"] + "/" + ref["oid"],
            headers,
            b"",
            LIMIT,
        )

    def json(self, path, params, *, channel, headers=None, method="GET"):
        h = self.api.transport.base_headers(base=HOST, application=True)
        h.update(
            {
                "accept": "application/json",
                "content-type": "application/json; charset=UTF-8",
                "X-Line-ChannelToken": self.channel(channel),
                **(headers or {}),
            }
        )
        raw = response_bytes(
            self.api,
            "content_list",
            method,
            path + "?" + urlencode(params),
            h,
            b"" if method == "POST" else None,
        )
        try:
            result = json.loads(raw)
        except (ValueError, RecursionError):
            raise ContentFailure("content_decode", "unexpected_schema") from None
        if not isinstance(result, dict) or type(result.get("code")) is not int:
            raise ContentFailure("content_decode", "unexpected_schema")
        if result["code"] != 0:
            raise ContentFailure("content_list", "upstream_rejected", code=result["code"])
        if not isinstance(result.get("result"), dict):
            raise ContentFailure("content_decode", "unexpected_schema")
        return result["result"]


class AdditionalContentClient(ContentClient):
    def __init__(self, api, store):
        super().__init__(api)
        self.store = store
        self.owner = api.tokens.mid

    def home(self, chat):
        if chat == self.api.tokens.mid:
            return self.store.get("additional_connection", "linejs")["canonicalOwner"]
        cached = self.store.get("additional_group_mapping", chat)
        if cached and cached.get("owner") == self.owner and time.time() - cached.get("at", 0) < 900:
            if not re.fullmatch(r"c[0-9a-f]{32}", str(cached.get("home", ""))):
                raise ContentFailure("group_mapping", "invalid_cached_mapping")
            return cached["home"]
        groups = self.api.get_chats([chat], with_members=False, with_invitees=False)
        rows = groups.get("chats") if isinstance(groups, dict) else None
        if not isinstance(rows, list) or len(rows) != 1 or rows[0].get("chatMid") != chat:
            raise ContentFailure("group_mapping", "group_mapping_unavailable")
        raw = self.api.get_recent_messages(chat, 20)
        if not isinstance(raw, list) or any(not isinstance(m, dict) for m in raw):
            raise ContentFailure("group_mapping", "group_mapping_unavailable")
        ids = list(
            dict.fromkeys(
                str(m.get("id"))
                for m in raw
                if m.get("to") == chat and DIGITS.fullmatch(str(m.get("id", "")))
            )
        )
        if len(ids) < 2:
            raise ContentFailure("group_mapping", "group_mapping_unavailable")
        mapped = self.invoke("resolve_chat", name=rows[0].get("chatName"), message_ids=ids[:20])
        if (
            not isinstance(mapped, dict)
            or not re.fullmatch(r"c[0-9a-f]{32}", str(mapped.get("home", "")))
            or mapped.get("matched_messages") != 2
        ):
            raise ContentFailure("group_mapping", "group_mapping_unavailable")
        self.store.put(
            "additional_group_mapping",
            chat,
            {"home": mapped["home"], "owner": self.owner, "at": time.time()},
        )
        return mapped["home"]

    def invoke(self, command, **params):
        from .additional_connection import AdditionalFailure, invoke

        try:
            return invoke(self.store, command, self.owner, **params)
        except AdditionalFailure as e:
            raise ContentFailure(
                e.stage,
                e.reason,
                status=e.diagnostic.get("http_status"),
                code=e.diagnostic.get("line_code"),
            ) from None

    def json(self, path, params, *, channel, headers=None, method="GET"):
        if path == "/ext/album/moa/v2/albums":
            raw = self.invoke("albums", cursor=params.get("cursor", ""))
        elif re.fullmatch(r"/ext/album/api/v6/albums/[0-9]{1,30}/photos", path):
            raw = self.invoke(
                "photos",
                home=headers["X-Line-Chat-Id"],
                album_id=path.split("/")[-2],
                cursor=params.get("cursor", ""),
            )
        elif path == "/ext/note/nt/api/v57/post/list.json":
            opts = {}
            if "postId" in params:
                opts["post_id"] = params["postId"]
            if "updatedTime" in params:
                opts["updated_time"] = params["updatedTime"]
            raw = self.invoke("notes", home=params["homeId"], **opts)
        else:
            raise ContentFailure("content_scope", "unsupported_operation")
        if not isinstance(raw, dict) or type(raw.get("code")) is not int:
            raise ContentFailure("content_decode", "unexpected_schema")
        if raw["code"] != 0:
            raise ContentFailure("content_list", "upstream_rejected", code=raw["code"])
        if not isinstance(raw.get("result"), dict):
            raise ContentFailure("content_decode", "unexpected_schema")
        return raw["result"]

    def download(self, home, album_id, ref):
        import base64

        result = self.invoke("photo", home=home, album_id=album_id, oid=ref["oid"], sid=ref["sid"])
        try:
            return base64.b64decode(result["bytes"], validate=True)
        except (ValueError, TypeError, KeyError):
            raise ContentFailure("album_download", "unexpected_schema") from None


def validate(service, chat, cursor=None):
    if not is_group(chat):
        raise ValueError("group_required")
    if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 2048):
        raise ValueError("invalid_cursor")
    if service.paused():
        raise ValueError("agent_paused")


def run(service, chat, operation):
    validate(service, chat)

    def perform(api):
        if service.paused():
            raise ValueError("agent_paused")
        try:
            result = operation(
                AdditionalContentClient(api, service.store)
                if service.store.get("additional_connection", "linejs")
                else ContentClient(api)
            )
            return {"ok": True, **result}
        except ContentFailure as e:
            return {
                "ok": False,
                "stage": e.stage,
                "reason": e.reason,
                "http_status": e.status,
                "line_code": e.code,
                "complete": False,
                "items": getattr(e, "items", None),
            }

    identity = service.store.get("setting", "identity") or {}
    mapping = service.store.get("additional_group_mapping", chat)
    extra = service.store.get("additional_connection", "linejs")
    if (
        extra
        and mapping
        and mapping.get("owner") == identity.get("mid") == extra.get("owner")
        and time.time() - mapping.get("at", 0) < 900
    ):
        from types import SimpleNamespace

        return perform(SimpleNamespace(tokens=SimpleNamespace(mid=identity["mid"])))
    return service.line.call(perform)


def notes_list(service, chat, updated_time=None, post_id=None):
    validate(service, chat)
    if post_id is not None and (not isinstance(post_id, str) or not DIGITS.fullmatch(post_id)):
        raise ValueError("invalid_post_id")
    if updated_time is not None and (
        type(updated_time) is not int or not 0 <= updated_time <= 2**53 - 1
    ):
        raise ValueError("invalid_note_cursor")

    def operation(client):
        home = client.home(chat)
        params = {"homeId": home, "sourceType": "TALKROOM", "likeLimit": 0, "commentLimit": 0}
        if post_id is not None:
            params["postId"] = post_id
        if updated_time is not None:
            params["updatedTime"] = updated_time
        result = client.json(
            "/ext/note/nt/api/v57/post/list.json",
            params,
            channel="1341209850",
            headers={"x-lhm": "GET"},
            method="POST",
        )
        home_info = result.get("homeInfo")
        if isinstance(home_info, dict) and home_info.get("homeId") != home:
            raise ContentFailure("note_scope", "home_changed")
        if (
            isinstance(home_info, dict)
            and "postCount" in home_info
            and (type(home_info["postCount"]) is not int or home_info["postCount"] < 0)
        ):
            raise ContentFailure("note_decode", "unexpected_schema")
        posts = result.get("posts")
        # The live Note API omits posts only when the returned home has zero posts.
        if (
            posts is None
            and isinstance(home_info, dict)
            and type(home_info.get("postCount")) is int
            and home_info["postCount"] == 0
        ):
            posts = []
        if not isinstance(posts, list) or len(posts) > 100:
            raise ContentFailure("note_decode", "unexpected_schema")
        items = []
        for post in posts:
            info = post.get("postInfo", {}) if isinstance(post, dict) else {}
            contents = post.get("contents", {}) if isinstance(post, dict) else {}
            if not isinstance(info, dict):
                raise ContentFailure("note_decode", "unexpected_schema")
            pid = str(info.get("postId", ""))
            if (
                not DIGITS.fullmatch(pid)
                or info.get("homeId", home) != home
                or not isinstance(contents, dict)
            ):
                raise ContentFailure("note_decode", "unexpected_schema")
            text = contents.get("text", "")
            if not isinstance(text, str) or len(text) > 100000:
                raise ContentFailure("note_decode", "unexpected_schema")
            service.store.put("note_reference", chat + ":" + pid, {"home": home, "at": time.time()})
            items.append(
                {"post_id": pid, "text": text, "source_chat": chat, "untrusted_content": True}
            )
        return {
            "items": items,
            "source": "note_api",
            "complete": isinstance(home_info, dict)
            and home_info.get("postCount") == 0
            and not posts,
            "total_count": home_info.get("postCount")
            if isinstance(home_info, dict) and type(home_info.get("postCount")) is int
            else None,
            "pagination": {
                "post_id": items[-1]["post_id"] if items else None,
                "updated_time": result.get("updatedTime")
                if type(result.get("updatedTime")) is int
                else None,
            },
            "limitation": "APIが返す1ページ。全件取得とは扱いません。",
        }

    return run(service, chat, operation)


def albums_list(service, chat, cursor=None):
    validate(service, chat, cursor)

    def operation(client):
        home, owner = client.home(chat), client.home(client.api.tokens.mid)
        result = client.json(
            "/ext/album/moa/v2/albums",
            {"cursor": cursor or "", "orderBy": "createTimeDesc", "include": ""},
            channel="1375220249",
            headers={"X-Line-Mid": owner},
            method="POST",
        )
        albums = result.get("albums")
        if not isinstance(albums, list) or len(albums) > 1000:
            raise ContentFailure("album_decode", "unexpected_schema")
        items = []
        for album in albums:
            if not isinstance(album, dict):
                raise ContentFailure("album_decode", "unexpected_schema")
            if album.get("chatId") != home:
                continue
            aid = str(album.get("albumId", ""))
            if not DIGITS.fullmatch(aid):
                raise ContentFailure("album_decode", "unexpected_schema")
            title = album.get("title", "")
            if not isinstance(title, str) or len(title) > 1000:
                raise ContentFailure("album_decode", "unexpected_schema")
            service.store.put(
                "album_reference",
                chat + ":" + aid,
                {"chat": chat, "home": home, "album_id": aid, "at": time.time()},
            )
            items.append(
                {
                    "album_id": aid,
                    "title": title,
                    "photo_count": album.get("photoCount")
                    if type(album.get("photoCount")) is int
                    else None,
                }
            )
        next_cursor = result.get("nextCursor") or result.get("cursor")
        if next_cursor is not None and (
            not isinstance(next_cursor, str) or len(next_cursor) > 2048
        ):
            raise ContentFailure("album_decode", "unexpected_schema")
        return {
            "items": items,
            "next_cursor": next_cursor,
            "complete": False,
            "source": "album_api_filtered_to_requested_chat",
            "page_end": result.get("hasMore") is False,
        }

    return run(service, chat, operation)


def album_photos(service, chat, album_id, cursor=None):
    validate(service, chat, cursor)
    if not isinstance(album_id, str) or not DIGITS.fullmatch(album_id):
        raise ValueError("invalid_album_id")
    reference = service.store.get("album_reference", chat + ":" + album_id)
    if not reference or time.time() - reference["at"] > 3600:
        raise ValueError("refresh_album_list_first")

    def operation(client):
        home = client.home(chat)
        if home != reference["home"]:
            raise ContentFailure("album_scope", "home_changed")
        result = client.json(
            "/ext/album/api/v6/albums/" + album_id + "/photos",
            {
                "cursor": cursor or "",
                "pageSize": 100,
                "orderBy": "createTimeDesc",
                "include": "all",
                "filterType": "",
            },
            channel="1375220249",
            headers={"X-Line-Mid": client.home(client.api.tokens.mid), "X-Line-Chat-Id": home},
            method="POST",
        )
        photos = result.get("photos")
        if not isinstance(photos, list) or len(photos) > 100:
            raise ContentFailure("photo_decode", "unexpected_schema")
        items = []
        for photo in photos:
            if not isinstance(photo, dict) or not isinstance(photo.get("obsResourceId", {}), dict):
                raise ContentFailure("photo_decode", "unexpected_schema")
            ref = photo.get("obsResourceId", {})
            oid = ref.get("oid") or photo.get("oid")
            sid = ref.get("sid", "a")
            if (
                not isinstance(oid, str)
                or not OBJECT.fullmatch(oid)
                or sid not in ("a", "o", "v")
                or ref.get("svc", "album") != "album"
            ):
                raise ContentFailure("photo_decode", "unexpected_schema")
            handle = hashlib.sha256((chat + "\0" + album_id + "\0" + oid).encode()).hexdigest()
            service.store.put(
                "album_photo_reference",
                handle,
                {
                    "chat": chat,
                    "home": home,
                    "album_id": album_id,
                    "oid": oid,
                    "sid": sid,
                    "at": time.time(),
                },
            )
            items.append({"photo_id": handle, "media_type": "video" if sid == "v" else "image"})
        nxt = result.get("nextCursor")
        if nxt is not None and (not isinstance(nxt, str) or len(nxt) > 2048):
            raise ContentFailure("photo_decode", "unexpected_schema")
        return {
            "items": items,
            "next_cursor": nxt,
            "page_end": result.get("hasMore") is False,
            "complete": False,
            "source": "album_api",
        }

    return run(service, chat, operation)


def album_media_get(service, chat, album_id, photo_ids):
    validate(service, chat)
    if not isinstance(album_id, str) or not DIGITS.fullmatch(album_id):
        raise ValueError("invalid_album_id")
    if (
        not isinstance(photo_ids, list)
        or not 1 <= len(photo_ids) <= 5
        or len(set(photo_ids)) != len(photo_ids)
        or any(not isinstance(p, str) or not re.fullmatch(r"[a-f0-9]{64}", p) for p in photo_ids)
    ):
        raise ValueError("invalid_photo_ids")
    refs = [service.store.get("album_photo_reference", p) for p in photo_ids]
    if any(
        not r or r["chat"] != chat or r["album_id"] != album_id or time.time() - r["at"] > 3600
        for r in refs
    ):
        raise ValueError("refresh_album_photos_first")

    def operation(client):
        import os
        import uuid

        from .platform_security import private_directory, private_open, reject_reparse
        from .received_media import media_extension

        home = client.home(chat)
        items = []
        try:
            for handle, ref in zip(photo_ids, refs, strict=True):
                if service.paused():
                    raise ContentFailure("album_download", "agent_paused")
                if home != ref["home"]:
                    raise ContentFailure("album_scope", "home_changed")
                raw = client.download(home, album_id, ref)
                try:
                    ext = media_extension(raw, 2 if ref["sid"] == "v" else 1)
                except ValueError:
                    raise ContentFailure("album_save", "unsupported_media_format") from None
                folder = service.store.path / "album-images"
                reject_reparse(folder)
                private_directory(folder)
                entries = list(folder.iterdir())
                for entry in entries:
                    reject_reparse(entry)
                if (
                    len(entries) >= 200
                    or any(not p.is_file() for p in entries)
                    or sum(p.stat().st_size for p in entries) + len(raw) > 256 * 1024 * 1024
                ):
                    raise ContentFailure("album_save", "quota_exceeded")
                path = folder / (uuid.uuid4().hex + ext)
                with os.fdopen(
                    private_open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
                ) as stream:
                    stream.write(raw)
                items.append(
                    {
                        "photo_id": handle,
                        "path": str(path),
                        "bytes": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "visual_verified": False,
                    }
                )
        except ContentFailure as error:
            error.items = items
            raise
        except Exception:
            error = ContentFailure("album_save", "local_save_failed")
            error.items = items
            raise error from None
        return {"items": items, "source": "album_original", "complete": True}

    return run(service, chat, operation)


def note_snapshot(api, store, chat, post_id):
    reference = store.get("note_reference", chat + ":" + post_id)
    if not reference or time.time() - reference["at"] > 3600:
        raise ValueError("refresh_note_list_first")
    client = AdditionalContentClient(api, store)
    home = client.home(chat)
    if reference["home"] != home:
        raise ContentFailure("note_scope", "home_changed")
    raw = client.invoke("note_get", home=home, post_id=post_id)
    if not isinstance(raw, dict) or type(raw.get("code")) is not int:
        raise ContentFailure("note_decode", "unexpected_schema")
    if raw["code"] != 0:
        raise ContentFailure("note_get", "upstream_rejected", code=raw["code"])
    result = raw.get("result")
    post = result.get("post", result) if isinstance(result, dict) else None
    info = post.get("postInfo") if isinstance(post, dict) else None
    contents = post.get("contents") if isinstance(post, dict) else None
    if (
        not isinstance(info, dict)
        or str(info.get("postId")) != post_id
        or info.get("homeId") != home
        or not isinstance(contents, dict)
    ):
        raise ContentFailure("note_decode", "unexpected_schema")
    text = contents.get("text", "")
    if not isinstance(text, str) or len(text) > 100000:
        raise ContentFailure("note_decode", "unexpected_schema")
    # Exclude volatile like/comment counts; bind editable content and version.
    return {
        "contents": contents,
        "home": home,
        "post_id": post_id,
        "updated_time": info.get("updatedTime"),
        "created_time": info.get("createdTime"),
    }


def note_get(service, chat, post_id):
    validate(service, chat)
    if not isinstance(post_id, str) or not DIGITS.fullmatch(post_id):
        raise ValueError("invalid_post_id")

    def operation(client):
        snapshot = note_snapshot(client.api, service.store, chat, post_id)
        return {
            "post_id": post_id,
            "text": snapshot["contents"].get("text", ""),
            "source_chat": chat,
            "untrusted_content": True,
            "complete": True,
        }

    return run(service, chat, operation)


def execute_note(api, store, kind, payload, snapshot=None):
    if kind not in {
        "note_create",
        "note_update",
        "note_delete",
        "note_like",
        "note_comment",
        "note_share",
    }:
        raise ValueError("unsupported_note_action")
    client = AdditionalContentClient(api, store)
    home = client.home(payload["group"])
    args = {"home": home}
    if kind != "note_create":
        ref = store.get("note_reference", payload["group"] + ":" + payload["post_id"])
        if not ref or ref.get("home") != home or time.time() - ref.get("at", 0) > 3600:
            raise ValueError("refresh_note_list_first")
        args["post_id"] = payload["post_id"]
        if not snapshot or snapshot["home"] != home or snapshot["post_id"] != payload["post_id"]:
            raise ValueError("note_snapshot_required")
        if kind == "note_update":
            args["contents"] = snapshot["contents"]
    if "text" in payload:
        args["text"] = payload["text"]
    if kind == "note_like":
        args["reaction"] = payload["reaction"]
    response = client.invoke(kind, **args)
    if not isinstance(response, dict) or type(response.get("code")) is not int:
        raise ContentFailure("note_write", "unexpected_schema")
    if response["code"] != 0:
        raise ContentFailure("note_write", "upstream_rejected", code=response["code"])
    result = response.get("result") or {}
    info = result.get("postInfo", {}) if isinstance(result, dict) else {}
    pid = str(info.get("postId", payload.get("post_id", ""))) if isinstance(info, dict) else ""
    if pid and not DIGITS.fullmatch(pid):
        raise ContentFailure("note_write", "unexpected_schema")
    return {"id": pid or None, "api_code": 0}
