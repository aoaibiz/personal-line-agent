import asyncio
import uuid

from line_agent.auth_diagnostics import read
from line_agent.authentication import repair
from line_agent.diagnostics import DiagnosticError


def test_exact_trace_is_closed_and_other_records_are_not_exposed(service):
    request, trace_id = str(uuid.uuid4()), "a" * 16
    service.store.put("authentication_request", request, {"login_trace_id": trace_id})
    service.store.put("login_trace", trace_id, {
        "stage": "qr_scan_wait", "reason": "line_api_rejected", "private": "SECRET",
        "events": [{"stage": "qr_scan_wait", "outcome": "error", "http_status": 410,
                    "line_code": 99, "elapsed_seconds": 10, "url": "SECRET"}],
    })
    service.store.put("credential", "session", {"secret": "SECRET"})
    result = read(service, request)
    assert result["correlation"] == "recorded_trace_id"
    assert result["traces"][0]["events"][0]["http_status"] == 410
    assert "SECRET" not in str(result)


def test_legacy_candidates_are_never_claimed_exact(service):
    request = str(uuid.uuid4())
    service.store.put("authentication_request", request, {"login_invoked": True, "checked_at": 1000})
    for name, ended in [("a", 1000), ("b", 1001), ("outside", 900)]:
        service.store.put("login_trace", name, {"repair_e2ee": True, "finished_at": ended})
    result = read(service, request)
    assert result["candidate_count"] == 2 and result["scan_complete"]
    assert result["correlation"] == "timestamp_candidates_unverified"


def test_no_login_does_not_read_unrelated_traces(service):
    request = str(uuid.uuid4())
    assert read(service, request)["correlation"] == "request_not_found"
    service.store.put("authentication_request", request, {"login_invoked": False, "checked_at": 1})
    assert read(service, request)["traces"] == []


def test_unknown_codes_and_large_or_nonfinite_values_not_returned(service):
    request, trace_id = str(uuid.uuid4()), "b" * 16
    service.store.put("authentication_request", request, {"login_trace_id": trace_id})
    service.store.put("login_trace", trace_id, {"stage": "SECRET", "reason": "SECRET",
        "started_at": float("inf"), "events": [
            {"stage": "SECRET", "outcome": "SECRET", "http_status": True,
             "line_code": 123456789, "elapsed_seconds": float("nan")}] * 65})
    result = read(service, request)
    trace = result["traces"][0]
    assert trace["events_truncated"] and len(trace["events"]) == 64
    assert trace["started_at"] is None
    assert trace["events"][0]["line_code"] is None
    assert "SECRET" not in str(result)


def test_repair_preserves_safe_error_codes_and_trace(service):
    from test_authentication import context, setup

    setup(service)

    def login(**kwargs):
        error = DiagnosticError("qr_scan_wait", "line_api_rejected", http_status=410, line_code=99)
        error.login_trace_id = "c" * 16
        raise error

    service.line.login = login
    ctx, _ = context()
    result = asyncio.run(repair(service, str(uuid.uuid4()), ctx))
    assert result["http_status"] == 410 and result["line_code"] == 99
    assert result["login_trace_id"] == "c" * 16
