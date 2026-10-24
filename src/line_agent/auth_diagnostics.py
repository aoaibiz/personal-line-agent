"""Closed diagnostic projection; never return upstream prose or credential records."""

import math
import re
import uuid

from .diagnostics import KNOWN_LINE_CODES, REASONS, STAGES


def number(value, maximum):
    return value if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= maximum else None


def project(trace):
    events = trace.get("events")
    events = events if isinstance(events, list) else []
    result = {
        "stage": trace.get("stage") if trace.get("stage") in STAGES else "unknown",
        "reason": trace.get("reason") if trace.get("reason") in REASONS else "unknown",
        "started_at": number(trace.get("started_at"), 1e11),
        "finished_at": number(trace.get("finished_at"), 1e11),
        "event_count": len(events),
        "events_truncated": len(events) > 64,
        "events": [],
    }
    for event in events[:64]:
        if not isinstance(event, dict):
            continue
        http, code = event.get("http_status"), event.get("line_code")
        result["events"].append({
            "stage": event.get("stage") if event.get("stage") in STAGES else "unknown",
            "outcome": event.get("outcome") if event.get("outcome") in {"ok", "error"} else "unknown",
            "http_status": http if type(http) is int and 100 <= http <= 599 else None,
            "line_code": code if type(code) is int and code in KNOWN_LINE_CODES else None,
            "elapsed_seconds": number(event.get("elapsed_seconds"), 3600),
        })
    return result


def read(service, request_id):
    request_id = str(uuid.UUID(request_id))
    record = service.store.get("authentication_request", request_id)
    if not record:
        return {"request_id": request_id, "correlation": "request_not_found", "traces": []}
    trace_id = record.get("login_trace_id")
    if isinstance(trace_id, str) and re.fullmatch(r"[a-f0-9]{16}", trace_id):
        trace = service.store.get("login_trace", trace_id)
        return {"request_id": request_id, "correlation": "recorded_trace_id",
                "traces": [project(trace)] if isinstance(trace, dict) else []}
    # Legacy requests lack a durable trace ID. Offer time-nearby candidates,
    # explicitly not an authenticated binding to this request.
    finished = number(record.get("checked_at"), 1e11)
    if record.get("login_invoked") is not True or finished is None:
        return {"request_id": request_id, "correlation": "no_login_trace", "traces": []}
    matches = []
    count = service.store.count("login_trace")
    for row in service.store.records("login_trace", limit=1000):
        trace = row.get("value")
        if not isinstance(trace, dict) or trace.get("repair_e2ee") is not True:
            continue
        end = number(trace.get("finished_at"), 1e11)
        if end is not None and abs(end - finished) <= 2:
            matches.append(project(trace))
    return {"request_id": request_id, "correlation": "timestamp_candidates_unverified",
            "scan_complete": count <= 1000, "candidate_count": len(matches),
            "traces": matches[:3], "candidates_truncated": len(matches) > 3}
