"""Encrypted durable records; raw LINE IDs never become SQLite index keys."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet


def account_path(root: Path, account: str) -> Path:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}", account):
        raise ValueError("アカウント名は英数字・ハイフン・下線で1〜48文字です")
    return root / account


class Store:
    def __init__(self, path: Path, key: bytes):
        path = Path(path).absolute()
        if any(p.is_symlink() for p in [path, *path.parents]):
            raise ValueError("保存先にシンボリックリンクは使えません")
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)
        self.path = path
        self.cipher = Fernet(key)
        self.index_key = hmac.digest(base64.urlsafe_b64decode(key), b"record-index-v1", "sha256")
        self.db = path / "records.sqlite3"
        for filename in (self.db, path / "sdk.lock"):
            if filename.is_symlink():
                raise ValueError("保存ファイルにシンボリックリンクは使えません")
        fd = os.open(self.db, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        os.chmod(self.db, 0o600)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, data BLOB, "
                "PRIMARY KEY(kind,id))"
            )
            if "scope" not in {row[1] for row in conn.execute("PRAGMA table_info(records)")}:
                conn.execute("ALTER TABLE records ADD COLUMN scope TEXT NOT NULL DEFAULT ''")
            conn.execute("CREATE INDEX IF NOT EXISTS records_scope ON records(kind,scope)")
            # Preserve visibility of histories written before the per-chat index existed.
            while rows := conn.execute(
                "SELECT id,data FROM records WHERE kind='message' AND scope='' LIMIT 256"
            ).fetchall():
                updates = []
                for row_id, encrypted in rows:
                    value = self._decode(encrypted)["value"]
                    chat = value.get("chat")
                    if not isinstance(chat, str) or not chat:
                        raise ValueError("旧履歴の相手を確認できません。移行を中止しました")
                    updates.append((self._id("scope", chat), row_id))
                conn.executemany(
                    "UPDATE records SET scope=? WHERE kind='message' AND id=?", updates
                )

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db, timeout=30)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _id(self, kind, record_id):
        return hmac.new(self.index_key, f"{kind}\0{record_id}".encode(), hashlib.sha256).hexdigest()

    def _encode(self, record_id, value):
        raw = json.dumps({"id": record_id, "value": value}, ensure_ascii=False).encode()
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("1レコードの保存上限を超えました")
        return self.cipher.encrypt(raw)

    def _decode(self, value):
        return json.loads(self.cipher.decrypt(value))

    def put(self, kind, record_id, value, scope=None):
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO records (kind,id,data,scope) VALUES (?,?,?,?)",
                (
                    kind,
                    self._id(kind, record_id),
                    self._encode(record_id, value),
                    self._id("scope", scope) if scope else "",
                ),
            )

    @contextmanager
    def atomic(self):
        """Commit received messages, inbox and resume cursor together."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            yield _Transaction(self, conn)

    def get(self, kind, record_id):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT data FROM records WHERE kind=? AND id=?", (kind, self._id(kind, record_id))
            ).fetchone()
        return self._decode(row[0])["value"] if row else None

    def records(self, kind, scope=None, limit=None, offset=0):
        with self.connect() as conn:
            query, args = "SELECT data FROM records WHERE kind=?", [kind]
            if scope:
                query += " AND scope=?"
                args.append(self._id("scope", scope))
            query += " ORDER BY id LIMIT ? OFFSET ?"
            args.extend([limit if limit is not None else -1, offset])
            rows = conn.execute(query, args).fetchall()
        return [self._decode(row[0]) for row in rows]

    def count(self, kind, scope=None):
        with self.connect() as conn:
            query, args = "SELECT COUNT(*) FROM records WHERE kind=?", [kind]
            if scope:
                query += " AND scope=?"
                args.append(self._id("scope", scope))
            return conn.execute(query, args).fetchone()[0]

    def transition(self, kind, record_id, expected, new_state, **updates):
        """Compare-and-swap. The claim is committed before any external operation."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT data FROM records WHERE kind=? AND id=?", (kind, self._id(kind, record_id))
            ).fetchone()
            if not row:
                return None
            value = self._decode(row[0])["value"]
            if value.get("state") != expected:
                return None
            value.update(updates, state=new_state)
            conn.execute(
                "UPDATE records SET data=? WHERE kind=? AND id=?",
                (self._encode(record_id, value), kind, self._id(kind, record_id)),
            )
            return value

    @contextmanager
    def exclusive(self, name="sdk", blocking=True):
        """Serialize SDK operations/credential rotation across MCP and worker processes."""
        import fcntl

        if name not in ("sdk", "worker", "assistance", "native-confirmation", "requests", "supervisor", "authentication"):
            raise ValueError("不明なロックです")
        fd = os.open(self.path / f"{name}.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


class _Transaction:
    def __init__(self, store, conn):
        self.store, self.conn = store, conn

    def get(self, kind, record_id):
        row = self.conn.execute(
            "SELECT data FROM records WHERE kind=? AND id=?",
            (kind, self.store._id(kind, record_id)),
        ).fetchone()
        return self.store._decode(row[0])["value"] if row else None

    def put(self, kind, record_id, value, scope=None):
        self.conn.execute(
            "INSERT OR REPLACE INTO records (kind,id,data,scope) VALUES (?,?,?,?)",
            (kind, self.store._id(kind, record_id), self.store._encode(record_id, value),
             self.store._id("scope", scope) if scope else ""),
        )


def open_store(root: Path, account: str, *, create=False) -> Store:
    import keyring

    path = account_path(root, account)
    backend = keyring.get_keyring()
    # Reject plaintext/chainer/fail backends; never silently downgrade key storage.
    module = type(backend).__module__
    if not module.startswith(
        ("keyring.backends.macOS", "keyring.backends.SecretService", "keyring.backends.Windows")
    ):
        raise RuntimeError("OSの安全なキーチェーンが必要です")
    identity = hashlib.sha256(str(path.absolute()).encode()).hexdigest()
    key = keyring.get_password("personal-line-agent", identity)
    if key is None:
        if not create or (path / "records.sqlite3").exists():
            raise RuntimeError("暗号鍵がありません。初回は init を実行してください")
        key = Fernet.generate_key().decode()
        keyring.set_password("personal-line-agent", identity, key)
    return Store(path, key.encode())
