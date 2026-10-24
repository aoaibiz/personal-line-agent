import asyncio
import uuid
from types import SimpleNamespace

import pytest
from mcp import types

from line_agent.authentication import repair, valid_line_url


def context(*, accept=True, url=True, cancel_url=False, cancel_pin=False):
    calls = []

    async def form(message, schema):
        calls.append("form")
        accepted = accept if "confirm" in schema["properties"] else not cancel_pin
        return types.ElicitResult(
            action="accept" if accepted else "cancel",
            content={"confirm": True} if accepted else None,
        )

    async def link(message, url, elicitation_id):
        calls.append("url")
        assert url.startswith("line://au/q/")
        return types.ElicitResult(action="cancel" if cancel_url else "accept")

    async def complete(request):
        calls.append("complete")

    cap = types.ElicitationCapability(
        form=types.FormElicitationCapability(),
        url=types.UrlElicitationCapability() if url else None,
    )
    session = SimpleNamespace(
        client_params=SimpleNamespace(capabilities=SimpleNamespace(elicitation=cap)),
        elicit_form=form,
        elicit_url=link,
        send_elicit_complete=complete,
    )
    return SimpleNamespace(session=session), calls


def setup(service):
    service.store.put("setting", "identity", {"mid": service.line.ids[-1]})
    calls = []

    def login(on_qr, on_pin, repair_e2ee=False, confirmation_gate=None):
        assert repair_e2ee
        calls.append("login")
        on_qr("line://au/q/synthetic?secret=NOT-A-REAL-SECRET")
        confirmation_gate(wait=True)
        on_pin("123456")
        confirmation_gate(wait=True)
        return {"connected": True, "e2ee_ready": True}

    service.line.login = login
    return calls


def test_no_host_capability_no_authentication(service):
    calls = setup(service)
    ctx, _ = context(url=False)
    result = asyncio.run(repair(service, str(uuid.uuid4()), ctx))
    assert result["error"] == "authentication_ui_unsupported" and not calls


def test_owner_decline_does_not_generate_qr(service):
    calls = setup(service)
    ctx, _ = context(accept=False)
    result = asyncio.run(repair(service, str(uuid.uuid4()), ctx))
    assert result["state"] == "cancelled" and not calls


@pytest.mark.parametrize("cancel_url,cancel_pin", [(True, False), (False, True)])
def test_cancelling_either_phone_step_stops_without_secret_result(service, cancel_url, cancel_pin):
    setup(service)
    ctx, _ = context(cancel_url=cancel_url, cancel_pin=cancel_pin)
    result = asyncio.run(repair(service, str(uuid.uuid4()), ctx))
    assert result["state"] == "cancelled"
    assert "SECRET" not in str(result) and "123456" not in str(result)


def test_success_never_returns_qr_pin_and_same_id_never_logs_in_twice(service):
    calls = setup(service)
    ctx, events = context()
    request = str(uuid.uuid4())
    result = asyncio.run(repair(service, request, ctx))
    replay = asyncio.run(repair(service, request, ctx))
    assert result == replay and result["state"] == "complete"
    assert events == ["form", "url", "form", "complete"] and calls == ["login"]
    assert "SECRET" not in str(result) and "123456" not in str(result)


def test_bad_url_does_not_open_arbitrary_page(service):
    setup(service)
    ctx, calls = context()

    def login(**kwargs):
        kwargs["on_qr"]("https://attacker.invalid/?private=synthetic")

    service.line.login = login
    result = asyncio.run(repair(service, str(uuid.uuid4()), ctx))
    assert result["error"] == "authentication_url_invalid" and calls == ["form"]
    assert result["ui_stage"] == "qr_link" and result["ui_response_ms"] is None
    assert "attacker" not in str(result)


def test_allowlist():
    for value in [
        "https://line.me.evil.invalid/R/au/q/a",
        "https://user:pass@line.me/R/au/q/a",
        "file:///tmp/a",
        "line://au/q/a\n",
        "https://line.me:443/R/au/q/a",
        "https://line.me/R/au/q/a#fragment",
    ]:
        assert not valid_line_url(value)
    assert valid_line_url("line://au/q/synthetic?secret=test")


def test_current_line_login_route_preserves_link_and_secret_only_in_host_ui(service):
    setup(service)
    ctx, events = context()
    value = "https://line.me/R/au/lgn/sq/synthetic_123-abc?secret=SYNTHETIC%2BKEY%3D&e2eeVersion=1"
    delivered = []

    def login(**kwargs):
        kwargs["on_qr"](value)
        return {"connected": True, "e2ee_ready": True}

    async def link(message, url, elicitation_id):
        delivered.append(url)
        return types.ElicitResult(action="accept")

    service.line.login = login
    ctx.session.elicit_url = link
    result = asyncio.run(repair(service, str(uuid.uuid4()), ctx))
    assert result["state"] == "complete" and delivered == [value]
    assert events == ["form", "complete"]
    assert "SYNTHETIC" not in str(result)
    assert value not in str(service.store.get("authentication_request", result["request_id"]))


@pytest.mark.parametrize(
    "value",
    [
        "http://line.me/R/au/lgn/sq/abc",
        "https://line.me.evil.invalid/R/au/lgn/sq/abc",
        "https://line.me@evil.invalid/R/au/lgn/sq/abc",
        "https://user@line.me/R/au/lgn/sq/abc",
        "https://line.me:443/R/au/lgn/sq/abc",
        "https://line.me/R/au/lgn/sq/abc#private",
        "https://line.me/R/au/lgn/sq/",
        "https://line.me/R/au/lgn/sq/../elsewhere",
        "https://line.me/R/au/lgn/sq/%2e%2e",
        "https://line.me/R/au/lgn/sq/abc/extra",
        "https://line.me/R/au/lgn/sq/abc\\evil",
        "https://line.me/R/au/lgn/sq/abc\n",
    ],
)
def test_current_line_login_route_rejects_other_destinations(value):
    assert not valid_line_url(value)


def test_repeated_cancellation_keeps_auth_lock_until_sdk_thread_exits(service):
    import threading

    started, release = threading.Event(), threading.Event()
    setup(service)

    def login(**kwargs):
        started.set()
        release.wait(5)
        return {"connected": True, "e2ee_ready": True}

    service.line.login = login
    ctx, _ = context()

    async def check():
        task = asyncio.create_task(repair(service, str(uuid.uuid4()), ctx))
        try:
            await asyncio.to_thread(started.wait, 2)
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0.02)
            with pytest.raises(BlockingIOError):
                with service.store.exclusive("authentication", blocking=False):
                    pass
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(check())


@pytest.mark.parametrize(
    "action,content,state,error",
    [
        ("decline", None, "declined", "authentication_host_declined"),
        ("cancel", None, "cancelled", "authentication_cancelled"),
        ("accept", {}, "not_confirmed", "authentication_confirmation_missing"),
    ],
)
def test_initial_host_decision_is_not_reported_as_user_action(
    service, action, content, state, error
):
    calls = setup(service)
    ctx, _ = context()

    async def respond(*args):
        return types.ElicitResult(
            action=action,
            content=content,
            _meta={"private": "DO-NOT-RECORD", "reason": "untrusted upstream prose"},
        )

    ctx.session.elicit_form = respond
    result = asyncio.run(repair(service, str(uuid.uuid4()), ctx))
    assert result["state"] == state and result["error"] == error
    assert result["host_action"] == action and result["ui_stage"] == "owner_start"
    assert result["presentation_verified"] is False and result["login_invoked"] is False
    assert result["ui_response_ms"] >= 0 and not calls
    assert "DO-NOT-RECORD" not in str(result) and "untrusted upstream prose" not in str(result)


def test_qr_host_decline_is_recorded_separately_from_cancel(service):
    setup(service)
    ctx, _ = context()

    async def refuse(*args):
        return types.ElicitResult(action="decline")

    ctx.session.elicit_url = refuse
    result = asyncio.run(repair(service, str(uuid.uuid4()), ctx))
    assert result["state"] == "declined" and result["error"] == "authentication_host_declined"
    assert result["ui_stage"] == "qr_link" and result["login_invoked"] is True
    assert "SECRET" not in str(result)
