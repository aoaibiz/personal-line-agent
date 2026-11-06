"""Bounded read-only image recovery through the existing authenticated adapter.

No caller URL/path, registration, receipt, or send. Media material stays inside SDK.
"""

import base64
import hashlib
import os
import re
import uuid
from urllib.parse import urlsplit

from .platform_security import private_directory, private_open, reject_reparse

LIMIT = 32 * 1024 * 1024
TOKEN = re.compile(r"[A-Za-z0-9_-]{1,200}\Z")
READ_RPC = {
    "Talk.TalkService." + name
    for name in (
        "getRecentMessagesV2",
        "getPreviousMessagesV2WithRequest",
        "getE2EEPublicKey",
        "getE2EEGroupSharedKey",
        "getLastE2EEGroupSharedKey",
        "acquireEncryptedAccessToken",
    )
}


def guarded_transport(api):
    transport = api.transport
    original_call, original_send = transport.call, transport._send

    def call(endpoint, *args, **kwargs):
        if endpoint not in READ_RPC:
            raise ValueError("non_read_rpc_denied")
        return original_call(endpoint, *args, **kwargs)

    def send(method, url, **kwargs):
        u = urlsplit(url)
        if (
            u.scheme != "https"
            or u.hostname
            not in {"line-chrome-gw.line-apps.com", "obs.line-apps.com", "obs.line-scdn.net"}
            or u.port not in (None, 443)
            or u.username
            or u.password
        ):
            raise ValueError("destination_denied")
        kwargs["allow_redirects"] = False
        obs = u.hostname != "line-chrome-gw.line-apps.com"
        if obs:
            if (
                method != "GET"
                or u.query
                or not re.fullmatch(r"/r/talk/[A-Za-z0-9_-]{1,200}/[A-Za-z0-9_-]{1,200}", u.path)
            ):
                raise ValueError("object_path_denied")
            kwargs["stream"] = True
        response = original_send(method, url, **kwargs)
        if 300 <= response.status_code < 400:
            response.close()
            raise ValueError("redirect_denied")
        if obs:
            chunks, total = [], 0
            try:
                for chunk in response.iter_content(65536):
                    total += len(chunk)
                    if total > LIMIT:
                        raise ValueError("image_too_large")
                    chunks.append(chunk)
                response._content = b"".join(chunks)
                response._content_consumed = True
            finally:
                response.close()
        return response

    transport.call, transport._send = call, send


def image_extension(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    raise ValueError("unsupported_image_format")


def media_extension(data, content_type):
    if content_type == 1:
        return image_extension(data)
    if content_type == 2:
        if data[4:8] == b"ftyp":
            return ".mp4"
        if data.startswith(b"\x1a\x45\xdf\xa3"):
            return ".webm"
        raise ValueError("unsupported_media_format")
    if content_type == 3:
        if data[4:8] == b"ftyp":
            return ".m4a"
        if data.startswith(b"OggS"):
            return ".ogg"
        if data.startswith(b"ID3") or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
            return ".mp3"
        if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
            return ".wav"
        raise ValueError("unsupported_media_format")
    if content_type == 14:
        # Never use an upstream filename/path. The caller can inspect the inert bytes.
        return ".bin"
    raise ValueError("unsupported_media_type")


def fetch_images(service, chat, message_ids):
    return fetch_media(service, chat, message_ids, allowed_types=(1,))


def fetch_media(
    service,
    chat,
    message_ids,
    *,
    allowed_types=(1, 2, 3, 14),
    before_message_id=None,
    before_delivered_time=None,
):
    from .identifiers import is_group
    from .service import validate_mid

    validate_mid(chat)
    if not is_group(chat):
        raise ValueError("group_required")
    if (
        not 1 <= len(message_ids) <= 5
        or len(set(message_ids)) != len(message_ids)
        or any(not re.fullmatch(r"[0-9]{1,30}", x) for x in message_ids)
    ):
        raise ValueError("invalid_image_ids")
    if (before_message_id is None) != (before_delivered_time is None):
        raise ValueError("incomplete_history_cursor")
    if before_message_id is not None and (
        not isinstance(before_message_id, str)
        or not re.fullmatch(r"[0-9]{1,30}", before_message_id)
        or type(before_delivered_time) is not int
        or not 0 <= before_delivered_time <= 2**63 - 1
    ):
        raise ValueError("invalid_history_cursor")
    if service.paused():
        raise ValueError("agent_paused")

    def operation(api):
        stage = "history_lookup"
        try:
            if service.paused():
                raise ValueError("agent_paused")
            guarded_transport(api)
            raw = (
                api.get_recent_messages(chat, 100)
                if before_message_id is None
                else api.get_previous_messages(chat, before_message_id, before_delivered_time, 100)
            )
            if (
                not isinstance(raw, list)
                or len(raw) > 100
                or any(not isinstance(m, dict) for m in raw)
            ):
                raise ValueError("unexpected_history")
            missing = [mid for mid in message_ids if not any(str(m.get("id")) == mid for m in raw)]
            lookup_failed = False
            lookup_diagnostic = None
            history_page = raw
            seen_cursors = set()
            next_cursor = None
            if missing:
                stage = "message_history_lookup"
                try:
                    for _ in range(10):
                        if not missing or not history_page:
                            next_cursor = None
                            break
                        if service.paused():
                            raise ValueError("agent_paused")
                        row = min(history_page, key=lambda m: int(m.get("id", 0)))
                        oldest = str(row.get("id", ""))
                        delivered = row.get("deliveredTime") or row.get("createdTime")
                        if isinstance(delivered, str) and re.fullmatch(r"[0-9]{1,19}", delivered):
                            delivered = int(delivered)
                        if (
                            not re.fullmatch(r"[0-9]{1,30}", oldest)
                            or type(delivered) is not int
                            or not 0 <= delivered <= 2**63 - 1
                        ):
                            raise ValueError("unexpected_history")
                        cursor = (oldest, delivered)
                        next_cursor = {"message_id": oldest, "delivered_time": delivered}
                        if cursor in seen_cursors:
                            raise ValueError("history_cursor_stalled")
                        seen_cursors.add(cursor)
                        extra = api.get_previous_messages(chat, oldest, delivered, 100)
                        if (
                            not isinstance(extra, list)
                            or len(extra) > 100
                            or any(not isinstance(m, dict) for m in extra)
                        ):
                            raise ValueError("unexpected_history")
                        known = {str(m.get("id")) for m in raw}
                        raw += [
                            m
                            for m in extra
                            if str(m.get("id")) in missing and str(m.get("id")) not in known
                        ]
                        missing = [
                            mid for mid in missing if not any(str(m.get("id")) == mid for m in raw)
                        ]
                        history_page = extra
                    if not missing or not history_page:
                        next_cursor = None
                except Exception as error:
                    lookup_failed = True
                    lookup_diagnostic = {
                        "http_status": getattr(error, "status", None)
                        if type(getattr(error, "status", None)) is int
                        else None,
                        "line_code": getattr(error, "code", None)
                        if type(getattr(error, "code", None)) is int
                        else None,
                        "category": type(error).__name__
                        if type(error).__name__
                        in {
                            "ValueError",
                            "LineApiError",
                            "LineTransportError",
                            "TimeoutError",
                            "KeyError",
                        }
                        else "other",
                    }
            results = []
            for message_id in message_ids:
                if service.paused():
                    raise ValueError("agent_paused")
                matches = [m for m in raw if str(m.get("id")) == message_id]
                if len(matches) != 1:
                    results.append(
                        {
                            "message_id": message_id,
                            "state": "message_lookup_failed"
                            if lookup_failed and message_id in missing
                            else "history_scan_limit"
                            if next_cursor
                            else "not_in_history",
                        }
                    )
                    continue
                msg = matches[0]
                if msg.get("to") != chat or msg.get("contentType") not in allowed_types:
                    results.append({"message_id": message_id, "state": "scope_or_type_mismatch"})
                    continue
                stage = "image_decrypt"
                sealed = bool(
                    (msg.get("contentMetadata") or {}).get("e2eeVersion") or msg.get("chunks")
                )
                if msg.get("chunks"):
                    msg = api.e2ee.decrypt(msg)
                meta = msg.get("contentMetadata") or {}
                oid, sid = (meta.get("OID"), meta.get("SID", "m")) if sealed else (message_id, "m")
                if not all(isinstance(v, str) and TOKEN.fullmatch(v) for v in (oid, sid)):
                    raise ValueError("invalid_reference")
                stage = "image_download"
                if service.paused():
                    raise ValueError("agent_paused")
                data = api.obs.download_object("talk", sid, oid, message_id=message_id)
                if sealed:
                    stage = "image_decrypt"
                    encoded = meta.get("ENC_KM")
                    if not isinstance(encoded, str) or len(encoded) > 64:
                        raise ValueError("media_material_missing")
                    km = base64.b64decode(encoded + "=" * (-len(encoded) % 4), validate=True)
                    if len(km) != 32:
                        raise ValueError("media_material_invalid")
                    from .media_crypto import decrypt_media_blob

                    data = decrypt_media_blob(km, data)
                stage = "image_save"
                ext = media_extension(data, msg.get("contentType"))
                if not 0 < len(data) <= LIMIT:
                    raise ValueError("image_size_invalid")
                folder = service.store.path / "received-images"
                if folder.is_symlink():
                    raise ValueError("unsafe_output")
                private_directory(folder)
                entries = list(folder.iterdir())
                for entry in entries:
                    reject_reparse(entry)
                if len(entries) >= 200 or any(p.is_symlink() or not p.is_file() for p in entries):
                    raise ValueError("output_limit_or_unsafe_entry")
                if sum(p.stat().st_size for p in entries) + len(data) > 256 * 1024 * 1024:
                    raise ValueError("output_quota_exceeded")
                path = folder / (uuid.uuid4().hex + ext)
                fd = private_open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                results.append(
                    {
                        "message_id": message_id,
                        "state": "saved",
                        "path": str(path),
                        "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "content_type": msg.get("contentType"),
                        "visual_verified": False,
                    }
                )
            return {
                "ok": not lookup_failed,
                "items": results,
                "scope": "requested_media_by_chat_and_id",
                "history_next_cursor": next_cursor,
                "complete": all(item["state"] == "saved" for item in results),
                **(
                    {
                        "stage": "message_history_lookup",
                        "reason": "message_lookup_failed",
                        "diagnostic": lookup_diagnostic,
                    }
                    if lookup_failed
                    else {}
                ),
            }
        except Exception as error:
            # Never expose raw upstream errors, object references, or key material.
            status = getattr(error, "status", None)
            if type(status) is not int:
                status = getattr(getattr(error, "response", None), "status_code", None)
            reason = (
                str(error)
                if type(error) is ValueError
                and str(error)
                in {
                    "non_read_rpc_denied",
                    "destination_denied",
                    "object_path_denied",
                    "redirect_denied",
                    "image_too_large",
                    "unexpected_history",
                    "invalid_reference",
                    "media_material_missing",
                    "media_material_invalid",
                    "unsupported_image_format",
                    "image_size_invalid",
                    "unsafe_output",
                    "output_limit_or_unsafe_entry",
                    "output_quota_exceeded",
                    "agent_paused",
                    "unsupported_media_format",
                    "unsupported_media_type",
                    "media_blob_size_invalid",
                    "media_authentication_failed",
                }
                else "image_recovery_failed"
            )
            return {
                "ok": False,
                "stage": stage,
                "reason": reason,
                "http_status": status if type(status) is int and 100 <= status <= 599 else None,
                "error_category": type(error).__name__
                if type(error).__name__
                in {
                    "ValueError",
                    "KeyError",
                    "RuntimeError",
                    "LineApiError",
                    "LineTransportError",
                    "TimeoutError",
                    "AttributeError",
                }
                else "other",
                "line_code": getattr(error, "code", None)
                if type(getattr(error, "code", None)) is int
                else None,
                "items": locals().get("results", []),
            }

    return service.line.call(operation)
