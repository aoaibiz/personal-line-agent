"""Synthetic dialog responses only; never evidence of a real human/UI roundtrip."""

import asyncio
import subprocess
import time
from types import SimpleNamespace

import pytest

from line_agent import native_approval as native
from line_agent.chat_approval import confirm_and_execute
from line_agent.server import create_server


def action(service):
    return service.prepare("send_text", {"to": service.line.ids[0], "text": "架空の本文 $(touch BAD)"})


def test_explicit_native_route_uses_bound_human_result_once(service, monkeypatch):
    monkeypatch.setattr(native, "available", lambda: True)
    seen = []
    def dialog(message, seconds):
        seen.append(message)
        assert "架空の本文" in message and "確認番号：" in message
        assert 0 < seconds <= 240
        # The SDK lock is not held while a person decides.
        with service.store.exclusive("sdk", blocking=False):
            pass
        return native.DialogResult("accepted", 1001, 0)
    monkeypatch.setattr(native, "show_dialog", dialog)
    saved = action(service)
    result = asyncio.run(confirm_and_execute(service, saved["id"], None, "native-mac"))
    assert result["state"] == "sent" and result["authority"] == "local_owner_native"
    assert result["approval_binding"] == service.approval_binding(saved)
    with pytest.raises(ValueError):
        asyncio.run(confirm_and_execute(service, saved["id"], None, "native-mac"))
    assert len(service.line.calls) == len(seen) == 1


@pytest.mark.parametrize("answer", ["cancelled", "timeout", "display_failed", "unavailable"])
def test_native_display_failures_never_authorize(service, monkeypatch, answer):
    monkeypatch.setattr(native, "available", lambda: True)
    monkeypatch.setattr(native, "show_dialog", lambda *args: native.DialogResult(answer))
    saved = action(service)
    result = native.confirm_and_execute_native(service, saved)
    assert result["executed"] is False and result["stage"] == "local_owner_native"
    assert result["error"] == "native_confirmation_" + answer
    assert service.action(saved["id"])["state"] == "prepared"
    assert not service.line.calls


def test_native_unavailable_and_busy(service, monkeypatch):
    saved = action(service)
    monkeypatch.setattr(native, "available", lambda: False)
    assert native.confirm_and_execute_native(service, saved)["error"] == "native_confirmation_unavailable"
    monkeypatch.setattr(native, "available", lambda: True)
    with service.store.exclusive("native-confirmation", blocking=False):
        assert native.confirm_and_execute_native(service, saved)["error"] == "native_confirmation_busy"
    assert not service.line.calls


def test_accepted_word_without_matching_os_button_is_not_approval(service, monkeypatch):
    monkeypatch.setattr(native, "available", lambda: True)
    monkeypatch.setattr(native, "show_dialog", lambda *args: native.DialogResult("accepted", 1000, 0))
    result = native.confirm_and_execute_native(service, action(service))
    assert result["executed"] is False and not service.line.calls


@pytest.mark.parametrize("change", ["text", "identity", "pause", "expiry", "conversation"])
def test_native_changes_while_dialog_open_fail_closed(service, monkeypatch, change):
    saved = action(service)
    monkeypatch.setattr(native, "available", lambda: True)
    def dialog(*args):
        row = service.action(saved["id"])
        if change == "text":
            row["payload"]["text"] = "差し替え"
        elif change == "identity":
            service.store.put("setting", "identity", {"mid": service.line.ids[2]})
        elif change == "pause":
            service.pause()
        elif change == "expiry":
            row["expires_at"] = time.time() - 1
        else:
            service.line.messages = [{"id": "new", "text": "新着"}]
        service.store.put("action", saved["id"], row)
        return native.DialogResult("accepted", 1001, 0)
    monkeypatch.setattr(native, "show_dialog", dialog)
    if change == "conversation":
        with pytest.raises(ValueError):
            native.confirm_and_execute_native(service, saved)
    else:
        assert native.confirm_and_execute_native(service, saved)["error"] == "confirmation_invalidated"
    assert not service.line.calls


def test_native_binding_is_checked_again_immediately_before_send(service):
    saved = action(service)
    service.approve_from_native(saved["id"], service.approval_binding(saved))
    row = service.action(saved["id"])
    assert row["state"] == "native_authorized"
    row["payload"]["text"] = "差し替え"
    service.store.put("action", saved["id"], row)
    with pytest.raises(ValueError):
        service.execute(saved["id"])
    assert not service.line.calls


def test_native_unknown_is_not_retried(service, monkeypatch):
    monkeypatch.setattr(native, "available", lambda: True)
    monkeypatch.setattr(native, "show_dialog", lambda *args: native.DialogResult("accepted", 1001, 0))
    service.line.fail_send = True
    saved = action(service)
    assert native.confirm_and_execute_native(service, saved)["state"] == "unknown"
    with pytest.raises(ValueError):
        service.execute(saved["id"])
    assert len(service.line.calls) == 1


def test_native_is_explicit_and_forbidden_on_remote(service):
    with pytest.raises(ValueError):
        create_server(service, public_url="https://example.com", confirmation="native-mac")
    with pytest.raises(ValueError):
        create_server(service, confirmation="invalid")


def test_jxa_program_and_message_are_separate_and_errors_are_closed(monkeypatch):
    monkeypatch.setattr(native, "available", lambda: True)
    body = '架空 " ; doShellScript("BAD"); $(BAD)'
    def run(argv, **kwargs):
        assert argv == ["/usr/bin/osascript", "-l", "JavaScript", "-e", native.SCRIPT]
        import json
        assert body not in str(argv) and json.loads(kwargs["input"]) == {"message": body, "purpose": "action"}
        assert kwargs["capture_output"] and kwargs["timeout"] == 10
        return SimpleNamespace(returncode=0, stdout='{"button_code":"1001"}\n')
    monkeypatch.setattr(subprocess, "run", run)
    assert native.show_dialog(body, 10) == native.DialogResult("accepted", 1001, 0)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kw: SimpleNamespace(returncode=1, stdout="accepted"))
    assert native.show_dialog(body, 10) == native.DialogResult("display_failed", exit_code=1)


@pytest.mark.parametrize("stdout,decision,button", [
    ('{"button_code":"1000"}', "cancelled", 1000),
    ('{"button_code":"1001"}', "accepted", 1001),
    ('{"button_code":"1002"}', "display_failed", None),
    ('{"button_code":1001}', "display_failed", None),
    ('accepted', "display_failed", None),
    ('{"button_code":"1001","other":true}', "display_failed", None),
])
def test_actual_button_code_mapping_is_closed(monkeypatch, stdout, decision, button):
    monkeypatch.setattr(native, "available", lambda: True)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kw: SimpleNamespace(returncode=0, stdout=stdout))
    result = native.show_dialog("架空", 1)
    assert result.decision == decision and result.button_code == button
