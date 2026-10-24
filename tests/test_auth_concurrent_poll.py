import asyncio
import threading
import uuid
from types import SimpleNamespace

import pytest
from mcp import types
from okline.exceptions import LineApiError
from okline.transport import Tokens

from line_agent.authentication import repair
from line_agent.line import LineAdapter


@pytest.mark.parametrize("deny_stage,cancel_before_issue", [(None, False), ("qr", False), ("pin", False), (None, True)])
def test_poll_starts_during_ui_but_token_issue_requires_both_accepts(service, monkeypatch, deny_stage, cancel_before_issue):
    from test_authentication import context

    owner = service.line.ids[-1]
    service.store.put("setting", "identity", {"mid": owner})
    polled = {kind: threading.Event() for kind in ("qr", "pin")}
    calls, saves = [], []

    def transport_call(endpoint, *args, **kwargs):
        calls.append(endpoint)
        if endpoint == "checkQrCodeVerified":
            polled["qr"].set()
        if endpoint == "checkPinCodeVerified":
            polled["pin"].set()
        return {}

    api = SimpleNamespace(
        transport=SimpleNamespace(call=transport_call),
        tokens=Tokens(mid=owner, access_token="SYNTHETIC"),
        e2ee=SimpleNamespace(is_ready=lambda: True),
        close=lambda: None,
        get_profile=lambda: {"mid": owner},
    )

    def qr_login(on_qr, on_pin, **kwargs):
        on_qr("https://line.me/R/au/lgn/sq/SYNTHETIC")
        api.transport.call("checkQrCodeVerified")
        api.transport.call("verifyCertificate")
        on_pin("123456")
        api.transport.call("checkPinCodeVerified")
        api.transport.call("qrCodeLoginV2")
        return SimpleNamespace(success=True)

    api.qr_login = qr_login
    adapter = LineAdapter(service.store)
    monkeypatch.setattr(adapter, "_client", lambda *_: api)
    monkeypatch.setattr(adapter, "_save", lambda *_: saves.append("save"))
    service.line = adapter
    ctx, _ = context()
    original_form = ctx.session.elicit_form
    pin_ui_waiting, release_pin_ui = asyncio.Event(), asyncio.Event()

    async def respond(kind):
        # This fails with the former blocking on_qr/on_pin: LINE polling could
        # not start before the UI returned, even though the owner saw the UI.
        assert await asyncio.to_thread(polled[kind].wait, 1)
        if kind == "pin" and cancel_before_issue:
            pin_ui_waiting.set()
            await release_pin_ui.wait()
        await asyncio.sleep(0.01)
        return types.ElicitResult(action="decline" if deny_stage == kind else "accept")

    async def link(*args):
        return await respond("qr")

    async def form(message, schema):
        if "confirm" in schema["properties"]:
            return await original_form(message, schema)
        return await respond("pin")

    ctx.session.elicit_url, ctx.session.elicit_form = link, form
    request = str(uuid.uuid4())

    async def scenario():
        task = asyncio.create_task(repair(service, request, ctx))
        if cancel_before_issue:
            await asyncio.wait_for(pin_ui_waiting.wait(), 2)
            task.cancel()
            await asyncio.sleep(0.02)
            release_pin_ui.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            return service.store.get("authentication_request", request)
        return await task

    result = asyncio.run(scenario())
    if cancel_before_issue:
        assert result["state"] == "cancelled"
        assert "qrCodeLoginV2" not in calls and not saves
        return
    if deny_stage:
        assert result["state"] == "declined"
        assert "qrCodeLoginV2" not in calls and not saves
    else:
        assert result["state"] == "complete"
        assert calls.count("qrCodeLoginV2") == 1 and saves
    assert "123456" not in str(result) and "SYNTHETIC" not in str(result)


def test_exhausted_scan_wait_is_not_reported_as_owner_rejection(service, monkeypatch):
    from line_agent.diagnostics import DiagnosticError

    adapter = LineAdapter(service.store)
    api = SimpleNamespace(transport=SimpleNamespace(call=lambda *a, **k: None), close=lambda: None)

    def qr_login(**kwargs):
        api.transport.call("checkQrCodeVerified")
        raise LineApiError("PRIVATE", status=410)

    api.qr_login = qr_login
    monkeypatch.setattr(adapter, "_client", lambda *_: api)
    with pytest.raises(DiagnosticError) as exc:
        adapter.login(lambda _: None, lambda _: None)
    assert exc.value.reason == "authentication_phone_confirmation_timeout"
    assert exc.value.http_status == 410 and "PRIVATE" not in str(exc.value)


def test_after_issuance_starts_host_cancellation_does_not_drop_checkpoint(service, monkeypatch):
    from test_login_diagnostics import SyntheticLogin

    from line_agent.diagnostics import DiagnosticError

    adapter = LineAdapter(service.store)
    api = SyntheticLogin()
    issued = False
    original_call = api.transport.call

    def issue(endpoint, *args, **kwargs):
        nonlocal issued
        if endpoint.endswith("qrCodeLoginV2"):
            issued = True
        return original_call(endpoint, *args, **kwargs)

    def gate(*, wait=False):
        if issued:
            raise DiagnosticError("authentication_ui", "authentication_cancelled")

    api.transport.call = issue
    monkeypatch.setattr(adapter, "_client", lambda *_: api)
    result = adapter.login(lambda _: None, lambda _: None, confirmation_gate=gate)
    assert result["connected"] and issued
    assert adapter.store.get("credential", "session")["accessToken"] == "synthetic-credential-never-from-user"


def test_ui_response_time_excludes_later_sdk_poll_delay(service):
    import time

    from test_authentication import context, setup

    setup(service)
    ctx, _ = context()

    async def immediate(*args):
        return types.ElicitResult(action="accept")

    def login(on_qr, **kwargs):
        on_qr("https://line.me/R/au/lgn/sq/SYNTHETIC")
        time.sleep(0.3)
        kwargs["confirmation_gate"](wait=True)
        return {"connected": True, "e2ee_ready": True}

    ctx.session.elicit_url = immediate
    service.line.login = login
    result = asyncio.run(repair(service, str(uuid.uuid4()), ctx))
    assert result["state"] == "complete"
    assert result["ui_response_ms"] < 150
