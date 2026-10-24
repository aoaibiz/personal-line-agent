"""Owner-selected attachments, staged as encrypted chunks without MCP filesystem access."""

from __future__ import annotations

import base64
import hashlib
import os
import uuid
from pathlib import Path

MAX_MEDIA_BYTES = 50 * 1024 * 1024
CHUNK = 1024 * 1024


def stage_file(store, path: Path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("添付は通常ファイルを指定してください")
    lower = path.name.lower()
    if (
        lower.startswith((".env", "tokens", "session", "id_rsa", "id_ed25519"))
        or (lower.startswith("pairing-") and lower.endswith(".json"))
        or lower in ("auth.json", "config.toml")
        or path.suffix.lower() in (".pem", ".key")
    ):
        raise ValueError("認証・鍵・設定ファイルは添付できません")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    media_id, chunks, total = str(uuid.uuid4()), 0, 0
    checksum = hashlib.sha256()
    with os.fdopen(fd, "rb") as file:
        if os.fstat(file.fileno()).st_size > MAX_MEDIA_BYTES:
            raise ValueError("添付の上限は50MiBです")
        while data := file.read(CHUNK):
            total += len(data)
            if total > MAX_MEDIA_BYTES:
                raise ValueError("添付の上限は50MiBです")
            checksum.update(data)
            store.put(
                "media_chunk", f"{media_id}:{chunks}", {"data": base64.b64encode(data).decode()}
            )
            chunks += 1
    if total == 0:
        raise ValueError("空のファイルは添付できません")
    meta = {
        "id": media_id,
        "name": path.name[:200],
        "size": total,
        "sha256": checksum.hexdigest(),
        "chunks": chunks,
    }
    store.put("media", media_id, meta)
    return meta


def load_bytes(store, media_id):
    meta = store.get("media", media_id)
    if not meta or not 0 < meta["size"] <= MAX_MEDIA_BYTES:
        raise ValueError("添付がありません")
    data = bytearray()
    for index in range(meta["chunks"]):
        row = store.get("media_chunk", f"{media_id}:{index}")
        if not row:
            raise ValueError("添付データに欠損があります")
        data.extend(base64.b64decode(row["data"], validate=True))
        if len(data) > MAX_MEDIA_BYTES:
            raise ValueError("添付が上限を超えています")
    if len(data) != meta["size"] or hashlib.sha256(data).hexdigest() != meta["sha256"]:
        raise ValueError("添付の内容が一致しません")
    return bytes(data), meta
