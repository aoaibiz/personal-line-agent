"""Bounded read-only image recovery through the existing authenticated adapter.

No caller URL/path, registration, receipt, or send. Media material stays inside SDK.
"""
import base64
import hashlib
import os
import re
import uuid
from urllib.parse import urlsplit

LIMIT = 32 * 1024 * 1024
TOKEN = re.compile(r"[A-Za-z0-9_-]{1,200}\Z")
READ_RPC = {"Talk.TalkService." + name for name in (
    "getRecentMessagesV2", "getE2EEPublicKey", "getE2EEGroupSharedKey",
    "getLastE2EEGroupSharedKey", "acquireEncryptedAccessToken")}


def guarded_transport(api):
    transport = api.transport
    original_call, original_send = transport.call, transport._send

    def call(endpoint, *args, **kwargs):
        if endpoint not in READ_RPC:
            raise ValueError("non_read_rpc_denied")
        return original_call(endpoint, *args, **kwargs)

    def send(method, url, **kwargs):
        u = urlsplit(url)
        if (u.scheme != "https" or u.hostname not in {
            "line-chrome-gw.line-apps.com", "obs.line-apps.com", "obs.line-scdn.net"
        } or u.port not in (None, 443) or u.username or u.password):
            raise ValueError("destination_denied")
        kwargs["allow_redirects"] = False
        obs = u.hostname != "line-chrome-gw.line-apps.com"
        if obs:
            if method != "GET" or u.query or not re.fullmatch(
                r"/r/talk/[A-Za-z0-9_-]{1,200}/[A-Za-z0-9_-]{1,200}", u.path
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


def fetch_images(service, chat, message_ids):
    from .identifiers import is_group
    from .service import validate_mid
    validate_mid(chat)
    if not is_group(chat):
        raise ValueError("group_required")
    if (not 1 <= len(message_ids) <= 5 or len(set(message_ids)) != len(message_ids)
            or any(not re.fullmatch(r"[0-9]{1,30}", x) for x in message_ids)):
        raise ValueError("invalid_image_ids")
    if service.paused():
        raise ValueError("agent_paused")

    def operation(api):
        stage = "history_lookup"
        try:
            guarded_transport(api)
            raw = api.get_recent_messages(chat, 100)
            if not isinstance(raw, list):
                raise ValueError("unexpected_history")
            results = []
            for message_id in message_ids:
                matches = [m for m in raw if str(m.get("id")) == message_id]
                if len(matches) != 1:
                    results.append({"message_id": message_id, "state": "not_in_recent_page"})
                    continue
                msg = matches[0]
                if msg.get("to") != chat or msg.get("contentType") != 1:
                    results.append({"message_id": message_id, "state": "scope_or_type_mismatch"})
                    continue
                stage = "image_decrypt"
                sealed = bool((msg.get("contentMetadata") or {}).get("e2eeVersion") or msg.get("chunks"))
                if msg.get("chunks"):
                    msg = api.e2ee.decrypt(msg)
                meta = msg.get("contentMetadata") or {}
                oid, sid = (meta.get("OID"), meta.get("SID", "m")) if sealed else (message_id, "m")
                if not all(isinstance(v, str) and TOKEN.fullmatch(v) for v in (oid, sid)):
                    raise ValueError("invalid_reference")
                stage = "image_download"
                data = api.obs.download_object("talk", sid, oid, message_id=message_id)
                if sealed:
                    stage = "image_decrypt"
                    encoded = meta.get("ENC_KM")
                    if not isinstance(encoded, str) or len(encoded) > 64:
                        raise ValueError("media_material_missing")
                    km = base64.b64decode(encoded + "=" * (-len(encoded) % 4), validate=True)
                    if len(km) != 32:
                        raise ValueError("media_material_invalid")
                    from okline.e2ee_crypto import decrypt_blob
                    data = decrypt_blob(km, data)
                stage = "image_save"
                ext = image_extension(data)
                if not 0 < len(data) <= LIMIT:
                    raise ValueError("image_size_invalid")
                folder = service.store.path / "received-images"
                if folder.is_symlink():
                    raise ValueError("unsafe_output")
                folder.mkdir(mode=0o700, exist_ok=True)
                os.chmod(folder, 0o700)
                entries = list(folder.iterdir())
                if len(entries) >= 200 or any(p.is_symlink() or not p.is_file() for p in entries):
                    raise ValueError("output_limit_or_unsafe_entry")
                if sum(p.stat().st_size for p in entries) + len(data) > 256 * 1024 * 1024:
                    raise ValueError("output_quota_exceeded")
                path = folder / (uuid.uuid4().hex + ext)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                results.append({"message_id": message_id, "state": "saved",
                                "path": str(path), "bytes": len(data),
                                "sha256": hashlib.sha256(data).hexdigest(),
                                "visual_verified": False})
            return {"items": results, "scope": "requested_images_in_recent_100"}
        except Exception as error:
            # Never expose raw upstream errors, object references, or key material.
            status = getattr(error, "status", None)
            if type(status) is not int:
                status = getattr(getattr(error, "response", None), "status_code", None)
            reason = str(error) if type(error) is ValueError and str(error) in {
                "non_read_rpc_denied", "destination_denied", "object_path_denied", "redirect_denied",
                "image_too_large", "unexpected_history", "invalid_reference", "media_material_missing",
                "media_material_invalid", "unsupported_image_format", "image_size_invalid", "unsafe_output",
                "output_limit_or_unsafe_entry", "output_quota_exceeded"
            } else "image_recovery_failed"
            return {"ok": False, "stage": stage, "reason": reason,
                    "http_status": status if type(status) is int and 100 <= status <= 599 else None,
                    "items": locals().get("results", [])}
    return service.line.call(operation)
