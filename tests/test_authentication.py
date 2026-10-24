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

    def login(on_qr, on_pin, repair_e2ee=False):
        assert repair_e2ee
        calls.append("login")
        on_qr("line://au/q/synthetic?secret=NOT-A-REAL-SECRET")
        on_pin("123456")
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
