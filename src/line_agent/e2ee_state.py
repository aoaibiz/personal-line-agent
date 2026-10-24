"""Validate SDK key persistence without logging key material or upstream prose."""
from __future__ import annotations

import time

from .diagnostics import DiagnosticError


def login_poll_timeout(headers):
    """A server long-poll must outlive X-LST; never change write retry policy."""
    value = (headers or {}).get("X-LST")
    if value is None:
        return None
    try:
        seconds = int(value) / 1000
    except (ValueError, TypeError):
        return None
    if not 0 < seconds <= 180:
        return None
    return (10, max(30, seconds + 15))


def transport_kind(error):
    """Only exception classes, never str(error), URLs, headers or response bodies."""
    import socket

    from requests.exceptions import ConnectionError, ConnectTimeout, ReadTimeout, SSLError
    from urllib3.exceptions import ReadTimeoutError

    pending, seen, kinds = [error], set(), set()
    while pending and len(seen) < 16:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        for typ, kind in ((socket.gaierror, "dns"), (SSLError, "tls"),
            (ConnectTimeout, "connect_timeout"), ((ReadTimeout, ReadTimeoutError), "read_timeout"),
            (ConnectionError, "connection")):
            if isinstance(current, typ):
                kinds.add(kind)
        pending.extend(x for x in (current.__cause__, current.__context__, *current.args)
                       if isinstance(x, BaseException))
    return next((k for k in ("dns", "tls", "connect_timeout", "read_timeout", "connection")
                 if k in kinds), "unknown")


def health(store, state, *, count=0, reason=None):
    value = {"state": state, "ready": state == "ready", "key_count": count,
             "checked_at": time.time()}
    if reason:
        value["reason"] = reason
    store.put("setting", "e2ee_health", value)


def restore(api, data, store):
    material = data.get("e2ee") or {}
    keys = material.get("keys") or {}
    if not keys:
        health(store, "missing", reason="e2ee_keys_missing")
        return
    if material.get("mid") != data.get("mid"):
        health(store, "error", reason="e2ee_identity_mismatch")
        raise DiagnosticError("e2ee_restore", "e2ee_identity_mismatch")
    try:
        restored = api.e2ee.load_from_export(material)
        expected = {int(key) for key in keys}
        actual = set(api.e2ee.my_keys)
        if not restored or not api.e2ee.is_ready() or expected != actual:
            raise ValueError("incomplete restore")
    except Exception:
        health(store, "error", reason="e2ee_restore_failed")
        raise DiagnosticError("e2ee_restore", "e2ee_restore_failed") from None
    health(store, "ready", count=len(actual))


def session_for_save(api, previous):
    """Keep the previous opaque key blobs even when an SDK export fails.

    Tokens may have rotated during a read; return their checkpoint plus a safe
    failure instead of losing either those tokens or the previously saved keys.
    """
    from okline.session import Session

    session = Session.from_tokens(api.tokens)
    if previous and previous.get("mid") != session.mid:
        raise DiagnosticError("e2ee_save", "e2ee_identity_mismatch")
    old = (previous or {}).get("e2ee") or {}
    if old and old.get("mid") != session.mid:
        raise DiagnosticError("e2ee_save", "e2ee_identity_mismatch")
    session.e2ee = old or None
    failure = None
    if api.e2ee.is_ready():
        try:
            exported = api.e2ee.export_keys()
            expected = set(api.e2ee.my_keys)
            actual = {int(key) for key in exported.get("keys", {})}
            if (not actual or expected != actual or exported.get("mid") != session.mid
                or int(exported.get("latestKeyId")) not in actual):
                raise ValueError("incomplete export")
            # Retain older key IDs from the same owner for older messages.
            session.e2ee = {**exported, "keys": {**old.get("keys", {}), **exported["keys"]}}
        except Exception:
            failure = DiagnosticError("e2ee_save", "e2ee_export_failed")
    elif old.get("keys"):
        failure = DiagnosticError("e2ee_save", "e2ee_restore_failed")
    return session.to_dict(), failure


def decrypt_failure(error):
    if isinstance(error, DiagnosticError):
        return {"stage": error.stage, "reason": error.reason}
    text = str(error)
    if text in {"read_only_key_registration_blocked", "receive_only_key_registration_blocked"}:
        reason = "e2ee_group_registration_required"
    elif text in {"no local E2EE key to decrypt with", "no local E2EE key to unwrap the group key"}:
        reason = "e2ee_keys_missing"
    else:
        reason = "message_decryption_failed"
    return {"stage": "message_decrypt", "reason": reason}
