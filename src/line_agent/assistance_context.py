"""Read-only, exact-ID context assembly. A retrieved window is never full history."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from .identifiers import id_kind


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def source_current(source):
    wal = Path(source["corpus"] + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueError("context_uncheckpointed_wal")
    if file_sha(source["corpus"]) != source["corpus_sha256"]:
        raise ValueError("context_source_changed")
    if file_sha(source["snapshot"]) != source["snapshot_sha256"]:
        raise ValueError("context_source_changed")


def _binding(store, source, chat=None):
    """Corroborate backup's self rows with exact live owner message IDs AND text.

    This is data corroboration, not a cryptographic iPhone ownership attestation.
    A merely shared friend/group MID is insufficient.
    """
    owner = (store.get("setting", "identity") or {}).get("mid")
    if not owner:
        raise ValueError("context_identity_missing")
    proofs = []
    frozen = (source.get("binding") or {}).get("proofs") if chat is None else None
    uri = Path(source["corpus"]).as_uri() + "?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        for row in store.records("message", scope=chat):
            if frozen and row["id"] not in {p["live_ref"] for p in frozen}:
                continue
            m = row["value"]
            if m.get("from") != owner or not isinstance(m.get("text"), str) or not m["text"].strip():
                continue
            matches = db.execute("SELECT m.uid,m.text,m.sender_role FROM messages m JOIN entities e "
                "ON m.entity_key=e.entity_key WHERE m.native_message_id=? "
                "AND (e.current_mid=? OR e.native_mid=?)", (str(m["id"]), m["chat"], m["chat"])).fetchall()
            if len(matches) == 1 and matches[0][1] == m["text"] and matches[0][2] == "owner_candidate":
                proofs.append({"backup_ref": matches[0][0], "live_ref": row["id"],
                               "text_sha256": digest(m["text"])})
                if len(proofs) == 3:
                    break
    if len(proofs) < 3:
        raise ValueError("context_owner_not_corroborated")
    return {"identity_index": store._id("source_owner", owner),
            "proof_count": len(proofs), "proof_sha256": digest(proofs),
            "proofs": proofs,
            "method": "exact_live_self_message_id_and_text", "cryptographic_attestation": False}


def configure_source(store, corpus, snapshot):
    """Owner-only CLI path selection; never exposed as an arbitrary-file MCP tool."""
    corpus, snapshot = Path(corpus).resolve(strict=True), Path(snapshot).resolve(strict=True)
    hashes = {"corpus_sha256": file_sha(corpus), "snapshot_sha256": file_sha(snapshot)}
    source = {"corpus": str(corpus), "snapshot": str(snapshot), **hashes,
              "account_binding": "live_owner_messages_corroborated"}
    # Validate schema and snapshot before persisting any configuration.
    _load_source(source)
    source["binding"] = _binding(store, source)
    source_current(source)
    # Changing a source never inherits a previous external disclosure permission.
    store.put("setting", "assistance_consent", {"scope": "revoked"})
    store.put("setting", "assistance_source", source)
    return {"configured": True, **hashes}


def _load_source(source):
    source_current(source)
    raw = Path(source["snapshot"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != source["snapshot_sha256"]:
        raise ValueError("context_source_changed")
    rows = json.loads(raw)["rows"]
    by_key = {row["対象ID"]: row for row in rows}
    if len(by_key) != len(rows):
        raise ValueError("context_snapshot_duplicate")
    return by_key


def _timestamp(value):
    try:
        return float(value or 0) / 1000
    except (ValueError, TypeError, OverflowError):
        return 0


def assemble_context(store, chat):
    kind = "person" if id_kind(chat) == "u" else "group"
    owner = (store.get("setting", "identity") or {}).get("mid")
    if not owner:
        raise ValueError("context_identity_missing")
    contact = store.get("contact", chat) or {}
    name = contact.get("displayNameOverridden") or contact.get("displayName") or "名称未取得"
    messages, prior, source_meta = [], None, None
    withheld_backup_count, target_binding = 0, None
    source = store.get("setting", "assistance_source")
    if source:
        if source.get("binding") != _binding(store, source):
            raise ValueError("context_owner_binding_changed")
        snapshot = _load_source(source)
        uri = Path(source["corpus"]).as_uri() + "?mode=ro&immutable=1"
        with sqlite3.connect(uri, uri=True) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA trusted_schema=OFF")
            entities = db.execute("SELECT * FROM entities WHERE current_mid=? OR native_mid=?",
                                  (chat, chat)).fetchall()
            if len(entities) > 1:
                raise ValueError("context_identity_ambiguous")
            if entities:
                entity = entities[0]
                try:
                    target_binding = _binding(store, source, chat=chat)
                except ValueError as error:
                    if str(error) != "context_owner_not_corroborated":
                        raise
                    withheld_backup_count = entity["expected_records"]
                if target_binding:
                    if entity["kind"] != kind:
                        raise ValueError("context_kind_mismatch")
                    row = snapshot.get(entity["entity_key"])
                    if not row or row["分類"] not in {"仕事", "プライベート", "両方", "不明"}:
                        raise ValueError("context_prior_missing")
                    name = name if name != "名称未取得" else row["LINE名"]
                    records = db.execute("SELECT * FROM messages WHERE entity_key=? "
                                         "ORDER BY utc_ts IS NULL,utc_ts,source_pk",
                                         (entity["entity_key"],)).fetchall()
                    if len(records) != entity["expected_records"] or len(records) != row["履歴件数"]:
                        raise ValueError("context_source_count_mismatch")
                    for r in records:
                        messages.append({"ref": "backup:" + r["uid"], "native_id": r["native_message_id"],
                            "timestamp": r["utc_ts"] or 0, "date": r["timestamp_jst"],
                            "sender": r["sender_role"], "type": r["content_type"],
                            "text": r["text"], "text_state": r["text_state"], "source": "backup"})
                    prior = {"category": row["分類"], "reason": row["理由"],
                        "provenance": row["判定元"], "owner_confirmed": row["判定元"] == "owner",
                        "source_entity": entity["entity_key"], "evidence": row["根拠メッセージID"]}
        # Bind the exact bytes used; originals remain immutable/read-only here.
        source_current(source)
        source_meta = {k: source[k] for k in ("corpus_sha256", "snapshot_sha256", "account_binding")}
        source_meta.update(target_binding=({k: target_binding[k] for k in
            ("proof_count", "proof_sha256", "method", "cryptographic_attestation")}
            if target_binding else None), withheld_backup_count=withheld_backup_count)
    memory = store.get("memory", chat)
    native = {str(m["native_id"]): i for i, m in enumerate(messages) if m["native_id"]}
    live = [r["value"] for r in store.records("message", scope=chat)]
    live.sort(key=lambda m: (_timestamp(m.get("createdTime") or m.get("deliveredTime")), str(m["id"])))
    for m in live:
        item = {"ref": "live:" + str(m["id"]), "native_id": str(m["id"]),
            "timestamp": _timestamp(m.get("createdTime") or m.get("deliveredTime")),
            "date": None, "sender": "owner" if m.get("from") == owner else "participant",
            "type": m.get("contentType"), "text": m.get("text"),
            "text_state": "decryption_failed" if m.get("decrypted") is False else "available",
            "live_decryption_failed": m.get("decrypted") is False,
            "source": "live"}
        if str(m["id"]) in native:
            old = messages[native[str(m["id"])]]
            # Keep both source references. Prefer current exact owner identity for style.
            item["also_ref"] = old["ref"]
            if item["text_state"] == "decryption_failed":
                item.update(text=old["text"], text_state=old["text_state"])
            messages[native[str(m["id"])]] = item
        else:
            native[str(m["id"])] = len(messages)
            messages.append(item)
    messages.sort(key=lambda m: (m["timestamp"], m["ref"]))
    incoming = store.records("delivery", scope=chat)
    incoming.sort(key=lambda r: (r["value"]["revision"], r["value"]["received_at"]))
    if not incoming:
        raise ValueError("context_incoming_missing")
    last_id = incoming[-1]["id"].split(":", 1)[1]
    trigger = next((m for m in messages if str(m["native_id"]) == last_id), None)
    if not trigger:
        raise ValueError("context_incoming_missing")
    # Retrieve continuous tail + adjacent earlier exchanges relevant to the new text.
    text = str(trigger.get("text") or "")
    grams = {text[i:i+3] for i in range(max(0, len(text)-2)) if text[i:i+3].strip()}
    selected = set(range(max(0, len(messages)-60), len(messages)))
    ranked = sorted(((sum(g in str(m.get("text") or "") for g in grams), i)
                     for i, m in enumerate(messages[:-60])), reverse=True)
    for score, i in ranked[:4]:
        if score:
            selected.update(range(max(0, i-2), min(len(messages), i+3)))
    selected.update(i for i, m in enumerate(messages) if m["ref"] == trigger["ref"])
    chosen, chars = [], 0
    # Preserve each selected message intact; trim oldest windows with an explicit coverage flag.
    for i in sorted(selected, reverse=True):
        m = messages[i]
        size = len(str(m.get("text") or ""))
        if size > 24000:
            raise ValueError("context_message_too_large")
        if chars + size <= 48000 or m["ref"] == trigger["ref"]:
            chosen.append(m)
            chars += size
    chosen.reverse()
    timestamp_known = trigger["timestamp"] > 0
    owner_replied = timestamp_known and any(m["sender"] == "owner" and m["timestamp"] > trigger["timestamp"]
                        for m in messages)
    order_uncertain = timestamp_known and any(m["sender"] == "owner" and
        m["timestamp"] in (0, trigger["timestamp"]) for m in messages)
    document = {"kind": kind, "display_name": name, "prior": prior,
        "owner_memory": memory, "trigger_ref": trigger["ref"], "owner_replied": owner_replied,
        "trigger_timestamp_known": timestamp_known,
        "owner_response_order_uncertain": order_uncertain,
        "trigger_decryption_failed": trigger.get("live_decryption_failed", False),
        "messages": chosen, "available_count": len(messages), "input_count": len(chosen),
        "coverage": ("live_only_backup_withheld" if withheld_backup_count else
                     "all_acquired" if len(chosen) == len(messages) else "prior_plus_retrieved_windows"),
        "phone_history_complete": False, "media_originals_viewed": False,
        "source": source_meta, "businesses": store.get("setting", "assistance_businesses") or []}
    if len(json.dumps(document, ensure_ascii=False).encode()) > 250000:
        raise ValueError("context_budget_exceeded")
    return document
