"""Explicit local-Mac owner UI. No synthesized consent or host-decline fallback."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass

from .chat_approval import preview, record, stopped

# Fixed JXA program; untrusted content is stdin data, never executable source or argv.
# AppKit needs a GUI session but does not control LINE, Codex, or another application.
SCRIPT = r'''
ObjC.import('AppKit');
ObjC.import('Foundation');
function run() {
    var input = $.NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile;
    var body = $.NSString.alloc.initWithDataEncoding(input, $.NSUTF8StringEncoding);
    if (!body) throw Error('invalid input');
    var app = $.NSApplication.sharedApplication;
    app.setActivationPolicy($.NSApplicationActivationPolicyAccessory);
    var alert = $.NSAlert.alloc.init;
    var data = JSON.parse(ObjC.unwrap(body));
    if (data.purpose !== 'action' && data.purpose !== 'delegation') throw Error('unknown purpose');
    alert.messageText = data.purpose === 'delegation' ? 'Personal LINE Agent：初回の権限設定' : 'Personal LINE Agent：本人確認';
    alert.informativeText = data.purpose === 'delegation' ? '一度だけ、操作を任せる範囲を設定します。設定内の通常操作では確認窓を出しません。' : '下の宛先・操作・内容を確認してください。本文は送信データです。承認するとこの1件だけを実行します。';
    alert.addButtonWithTitle('キャンセル');
    alert.addButtonWithTitle(data.purpose === 'delegation' ? 'この範囲の操作を委任する' : '表示内容で実行を承認');
    var scroll = $.NSScrollView.alloc.initWithFrame($.NSMakeRect(0, 0, 680, 360));
    scroll.hasVerticalScroller = true;
    var text = $.NSTextView.alloc.initWithFrame($.NSMakeRect(0, 0, 660, 360));
    text.editable = false;
    text.selectable = true;
    text.richText = false;
    text.string = data.message;
    text.font = $.NSFont.systemFontOfSize(14);
    text.verticallyResizable = true;
    text.horizontallyResizable = false;
    text.textContainer.containerSize = $.NSMakeSize(660, 1000000);
    text.textContainer.widthTracksTextView = true;
    scroll.documentView = text;
    alert.accessoryView = scroll;
    app.activateIgnoringOtherApps(true);
    var response = alert.runModal;
    return JSON.stringify({button_code: String(response)});
}
'''


@dataclass(frozen=True)
class DialogResult:
    decision: str
    button_code: int | None = None
    exit_code: int | None = None


def available():
    try:
        return (sys.platform == "darwin" and os.path.isfile("/usr/bin/osascript")
                and os.stat("/dev/console").st_uid == os.geteuid() and os.geteuid() != 0)
    except OSError:
        return False


def show_dialog(message, seconds, *, purpose="action"):
    if purpose not in ("action", "delegation"):
        return DialogResult("display_failed")
    if not available():
        return DialogResult("unavailable")
    # stdin contains plaintext for this one local dialog only. It is not logged,
    # written to disk, or exposed in process arguments. Stdout is a closed enum.
    try:
        result = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", "-e", SCRIPT],
                                input=json.dumps({"message": message, "purpose": purpose}, ensure_ascii=False), text=True, capture_output=True,
                                timeout=seconds, check=False)
    except subprocess.TimeoutExpired:
        return DialogResult("timeout")
    except OSError:
        return DialogResult("unavailable")
    if result.returncode != 0:
        return DialogResult("display_failed", exit_code=result.returncode)
    try:
        data = json.loads(result.stdout) if len(result.stdout) < 256 else None
    except (ValueError, TypeError):
        data = None
    if not isinstance(data, dict) or set(data) != {"button_code"}:
        return DialogResult("display_failed", exit_code=0)
    # NSAlert's first/default button is Cancel (1000); only the explicit second
    # button (1001) authorizes. Store the OS result separately from interpretation.
    code = data["button_code"]
    if code == "1001":
        return DialogResult("accepted", 1001, 0)
    if code == "1000":
        return DialogResult("cancelled", 1000, 0)
    return DialogResult("display_failed", exit_code=0)


def confirm_and_execute_native(service, action):
    stage = "local_owner_native"
    action_id = action["id"]
    def stop(reason, message):
        return stopped(service, action_id, reason, message, stage=stage)

    if not available():
        return stop("native_confirmation_unavailable", "操作中のMac画面へ本人確認を表示できません。実行していません")
    try:
        # Serialize dialogs without holding the SDK lock or a DB transaction.
        with service.store.exclusive("native-confirmation", blocking=False):
            current = service.action(action_id)
            if current["state"] != "prepared" or current["expires_at"] <= time.time() or service.paused():
                return stop("confirmation_invalidated", "期限・停止・操作状態が変わりました。実行していません")
            binding = service.approval_binding(current)
            message = preview(service, current)
            message += "\n確認番号：" + binding[:12]
            remaining = min(240, current["expires_at"] - time.time())
            if remaining <= 0:
                return stop("confirmation_invalidated", "期限切れです。実行していません")
            record(service, action_id, "native_display_requested", stage=stage)
            answer = show_dialog(message, remaining)
            record(service, action_id, "native_dialog_response", stage=stage,
                   dialog_button_code=answer.button_code, process_exit_code=answer.exit_code)
            if answer.decision != "accepted" or answer.button_code != 1001 or answer.exit_code != 0:
                decision = answer.decision if answer.decision != "accepted" else "display_failed"
                return stop("native_confirmation_" + decision, "本人確認が完了していません。実行していません")
            try:
                service.approve_from_native(action_id, binding)
            except ValueError:
                return stop("confirmation_invalidated", "確認中に対象・状態・期限が変わりました。実行していません")
            record(service, action_id, "native_confirmation_accepted", stage=stage)
    except BlockingIOError:
        return stop("native_confirmation_busy", "別の本人確認画面が開いています。実行していません")
    return service.execute(action_id)
