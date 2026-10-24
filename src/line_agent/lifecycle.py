"""Safe connection probes. Never erase credentials or replay an external write."""
from __future__ import annotations

import time

from .diagnostics import diagnostic_error


def diagnose(service, *, probe=False):
    saved = service.status()
    with service.store.connect() as conn:
        integrity = conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    result = {
        "store_integrity": "ok" if integrity else "error",
        "identity_saved": saved["connected"],
        "connection_verified": None,
        "paused": saved["paused"],
        "uncertain_operations": len(saved["uncertain"]),
        "e2ee_state": (saved.get("e2ee") or {}).get("state", "unknown"),
        "receiver_state": (saved.get("receiver") or {}).get("state", "unknown"),
        "checked_at": time.time(),
    }
    if not integrity:
        return {**result, "ok": False, "reason": "storage_failed", "next_action": "preserve_store_and_investigate"}
    if not probe:
        return {**result, "ok": True, "next_action": "connection_check"}
    identity = service.store.get("setting", "identity") or {}
    if not identity.get("mid"):
        return {**result, "ok": False, "reason": "login_required", "next_action": "owner_authentication"}
    try:
        profile = service.line.profile()
        if not isinstance(profile, dict) or profile.get("mid") != identity["mid"]:
            return {**result, "ok": False, "connection_verified": False,
                    "reason": "identity_mismatch", "next_action": "preserve_store_and_investigate"}
        return {**result, "ok": True, "connection_verified": True,
                "next_action": "none"}
    except Exception as error:
        safe = diagnostic_error("profile_check", error)
        return {**result, "ok": False, "connection_verified": False,
                "reason": safe.reason, "stage": safe.stage,
                "http_status": safe.http_status, "line_code": safe.line_code,
                "next_action": "investigate_connection"}
