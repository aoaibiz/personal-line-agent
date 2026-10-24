"""Owner reauthentication through the trusted MCP host UI, never tool-result secrets."""

from __future__ import annotations

import asyncio
import re
import threading
import time
import uuid
from urllib.parse import urlsplit

from .diagnostics import DiagnosticError, diagnostic_error


def capabilities(ctx):
    params = ctx.session.client_params
    cap = params.capabilities.elicitation if params else None
    return {
        "form": bool(cap and cap.form is not None),
        "url": bool(cap and cap.url is not None),
        "presentation_verified": False,
    }


def valid_line_url(value):
    if not isinstance(value, str) or len(value) > 8192 or any(ord(c) < 32 for c in value):
        return False
    try:
        url = urlsplit(value)
        if url.username or url.password or url.fragment or url.port:
            return False
        return (
            (url.scheme == "line" and url.netloc == "au" and url.path.startswith("/q/"))
            or (
                url.scheme == "https"
                and url.netloc == "line.me"
                and (
                    url.path.startswith("/R/au/q/")
                    or re.fullmatch(r"/R/au/lgn/sq/[A-Za-z0-9_-]+", url.path) is not None
                )
            )
            or (
                url.scheme == "https"
                and url.netloc == "access.line.me"
                and url.path.startswith("/q/")
            )
        )
    except ValueError:
        return False


def status(service, request_id):
    request_id = str(uuid.UUID(request_id))
    record = service.store.get("authentication_request", request_id)
    return record or {"request_id": request_id, "state": "not_found"}


async def repair(service, request_id, ctx):
    request_id = str(uuid.UUID(request_id))
    supported = capabilities(ctx)
    if not supported["form"] or not supported["url"]:
        return {
            "ok": False,
            "error": "authentication_ui_unsupported",
            "started": False,
            "required": ["form_elicitation", "url_elicitation"],
        }
    if not service.store.get("setting", "identity"):
        return {"ok": False, "error": "initial_setup_required", "started": False}
    try:
        with service.store.exclusive("authentication", blocking=False):
            previous = service.store.get("authentication_request", request_id)
            if previous:
                return previous

            flow = {
                "login_invoked": False,
                "ui_stage": "owner_start",
                "host_action": None,
                "presentation_verified": False,
                "ui_response_ms": None,
            }

            def decision(answer, stage, elapsed):
                action = (
                    answer.action if answer.action in {"accept", "decline", "cancel"} else "invalid"
                )
                flow.update(
                    ui_stage=stage, host_action=action, ui_response_ms=round(elapsed * 1000)
                )

            def record(state, **fields):
                row = {
                    "request_id": request_id,
                    "state": state,
                    "checked_at": time.time(),
                    **flow,
                    **fields,
                }
                service.store.put("authentication_request", request_id, row)
                return row

            record("awaiting_owner")
            schema = {
                "type": "object",
                "properties": {
                    "confirm": {"type": "boolean", "title": "このLINEアカウントを再認証する"}
                },
                "required": ["confirm"],
            }
            requested_at = time.monotonic()
            try:
                answer = await asyncio.wait_for(
                    ctx.session.elicit_form(
                        "LINEの再認証を開始します。続いてスマホのLINEで本人確認します。送信や権限の変更は行いません。",
                        schema,
                    ),
                    90,
                )
            except TimeoutError:
                return record("error", ok=False, error="authentication_ui_timeout")
            except Exception:
                return record("error", ok=False, error="authentication_ui_unavailable")
            decision(answer, "owner_start", time.monotonic() - requested_at)
            if answer.action == "decline":
                return record("declined", ok=False, error="authentication_host_declined")
            if answer.action == "cancel":
                return record("cancelled", ok=False, error="authentication_cancelled")
            if (
                answer.action != "accept"
                or not answer.content
                or answer.content.get("confirm") is not True
            ):
                return record(
                    "not_confirmed", ok=False, error="authentication_confirmation_missing"
                )
            loop = asyncio.get_running_loop()
            cancelled = threading.Event()

            async def prompt(kind, value):
                if kind == "url":
                    if not valid_line_url(value):
                        raise DiagnosticError("authentication_ui", "authentication_url_invalid")
                    return await ctx.session.elicit_url(
                        "このリンクをスマホで開き、LINEの本人確認を完了してください。リンクは共有しないでください。",
                        value,
                        request_id,
                    )
                if not isinstance(value, str) or not re.fullmatch(r"\d{4,8}", value):
                    raise DiagnosticError("authentication_ui", "authentication_pin_invalid")
                return await ctx.session.elicit_form(
                    "スマホのLINEに表示の確認番号を入力してください：" + value,
                    {"type": "object", "properties": {}},
                )

            def display(kind, value):
                if cancelled.is_set():
                    raise DiagnosticError("authentication_ui", "authentication_cancelled")
                flow.update(
                    ui_stage="qr_link" if kind == "url" else "pin",
                    host_action=None,
                    ui_response_ms=None,
                )
                record("awaiting_host")
                requested_at = time.monotonic()
                future = asyncio.run_coroutine_threadsafe(prompt(kind, value), loop)
                try:
                    reply = future.result(timeout=90)
                except TimeoutError:
                    future.cancel()
                    raise DiagnosticError(
                        "authentication_ui", "authentication_ui_timeout"
                    ) from None
                except DiagnosticError:
                    raise
                except Exception:
                    raise DiagnosticError(
                        "authentication_ui", "authentication_ui_unavailable"
                    ) from None
                decision(
                    reply, "qr_link" if kind == "url" else "pin", time.monotonic() - requested_at
                )
                if reply.action == "decline":
                    raise DiagnosticError("authentication_ui", "authentication_host_declined")
                if reply.action != "accept":
                    raise DiagnosticError("authentication_ui", "authentication_cancelled")

            def login():
                try:
                    flow["login_invoked"] = True
                    record("running")
                    result = service.line.login(
                        on_qr=lambda value: display("url", value),
                        on_pin=lambda value: display("pin", str(value)),
                        repair_e2ee=True,
                    )
                    if result.get("connected") is not True or result.get("e2ee_ready") is not True:
                        raise DiagnosticError("authentication_ui", "e2ee_login_incomplete")
                    return record(
                        "complete",
                        ok=True,
                        connected=result.get("connected") is True,
                        e2ee_ready=result.get("e2ee_ready") is True,
                    )
                except Exception as error:
                    safe = diagnostic_error("authentication_ui", error)
                    return record(
                        (
                            "cancelled"
                            if safe.reason == "authentication_cancelled"
                            else "declined"
                            if safe.reason == "authentication_host_declined"
                            else "error"
                        ),
                        ok=False,
                        error=safe.reason,
                        stage=safe.stage,
                    )

            # Shield keeps the auth lock held until the SDK stops, even on host cancellation.
            task = asyncio.create_task(asyncio.to_thread(login))
            try:
                result = await asyncio.shield(task)
                if result.get("state") == "complete":
                    try:
                        await ctx.session.send_elicit_complete(request_id)
                    except Exception:
                        pass  # Login succeeded; never repeat authentication for a lost UI notification.
                return result
            except asyncio.CancelledError:
                cancelled.set()
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        continue
                raise
    except BlockingIOError:
        return {"ok": False, "error": "authentication_in_progress", "started": False}
