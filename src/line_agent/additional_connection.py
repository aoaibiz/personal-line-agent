"""Separate LINEJS connection. Only this module handles its private stdio state.

Existing credentials, receiver and send ledger are never replaced. No raw API tool.
"""

from __future__ import annotations

import json
import os
import re
import selectors
import subprocess
import sys
import time
from pathlib import Path

from .authentication import valid_line_url
from .native_approval import available
from .platform_security import find_executable, reject_reparse

SCRIPT = r"""
ObjC.import('AppKit'); ObjC.import('Foundation'); ObjC.import('CoreImage');
function run() {
 var data=$.NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile;
 var body=JSON.parse(ObjC.unwrap($.NSString.alloc.initWithDataEncoding(data,$.NSUTF8StringEncoding)));
 var app=$.NSApplication.sharedApplication; app.setActivationPolicy($.NSApplicationActivationPolicyAccessory);
 var alert=$.NSAlert.alloc.init; alert.messageText='Personal LINE Agent：追加接続';
 alert.addButtonWithTitle('キャンセル');
 if(body.kind==='qr') {
   alert.informativeText='スマホのLINEでQRコードを読み取ってください。今の自動受信用接続は置き換えません。LINE側の端末制限によって別のPC版LINEがログアウトする可能性があります。';
   var filter=$.CIFilter.filterWithName('CIQRCodeGenerator'); filter.setDefaults;
   filter.setValueForKey($(body.value).dataUsingEncoding($.NSUTF8StringEncoding),'inputMessage');
   filter.setValueForKey($('M'),'inputCorrectionLevel');
   var output=filter.outputImage.imageByApplyingTransform($.CGAffineTransformMakeScale(6,6));
   var rep=$.NSCIImageRep.imageRepWithCIImage(output);
   var image=$.NSImage.alloc.initWithSize(rep.size); image.addRepresentation(rep);
   var view=$.NSImageView.alloc.initWithFrame($.NSMakeRect(0,0,360,360)); view.image=image;
   view.imageScaling=$.NSImageScaleProportionallyUpOrDown; alert.accessoryView=view;
 } else if(body.kind==='pin') {
   alert.informativeText='スマホのLINEで確認番号を入力してください：'+body.value;
 } else throw Error('invalid');
 app.activateIgnoringOtherApps(true); alert.runModal;
}
"""


class AdditionalFailure(Exception):
    def __init__(self, stage, reason):
        self.stage, self.reason = stage, reason


def adapter_directory():
    # Source checkout / immutable runtime, not a caller-controlled path.
    root = Path(sys.prefix).parent
    if not (root / "adapters" / "linejs" / "worker.mjs").is_file():
        root = Path(__file__).resolve().parents[2]
    directory = root / "adapters" / "linejs"
    if not (directory / "worker.mjs").is_file():
        directory = Path(__file__).resolve().parent / "linejs"
    for path in (directory, directory / "worker.mjs", directory / "node_modules"):
        reject_reparse(path)
    if not (directory / "worker.mjs").is_file() or not (directory / "node_modules").is_dir():
        raise AdditionalFailure("bootstrap", "additional_adapter_not_installed")
    return directory


def show_auth(kind, value):
    if not available():
        raise AdditionalFailure("owner_ui", "native_ui_unavailable")
    if (
        (kind == "qr" and not valid_line_url(value))
        or (kind == "pin" and (not isinstance(value, str) or not re.fullmatch(r"\d{4,8}", value)))
        or kind not in ("qr", "pin")
    ):
        raise AdditionalFailure("owner_ui", "invalid_authentication_prompt")
    proc = subprocess.Popen(
        ["/usr/bin/osascript", "-l", "JavaScript", "-e", SCRIPT],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    proc.stdin.write(json.dumps({"kind": kind, "value": value}).encode())
    proc.stdin.close()
    return proc


def invoke(store, command, owner, *, display=None, **params):
    if command not in (
        "login",
        "check",
        "albums",
        "photos",
        "photo",
        "notes",
        "resolve_chat",
        "note_get",
        "note_create",
        "note_update",
        "note_delete",
    ):
        raise ValueError("unsupported_additional_command")
    if not isinstance(owner, str) or not re.fullmatch(
        r"(?:u[0-9a-f]{32}|U[A-Za-z0-9_-]{43})", owner
    ):
        raise AdditionalFailure("identity", "owner_mapping_unavailable")
    node = find_executable("node")
    if not node:
        raise AdditionalFailure("bootstrap", "node_runtime_missing")
    directory = adapter_directory()
    with store.exclusive("additional-linejs", blocking=False):
        state = store.get("additional_connection", "linejs")
        if command != "login" and (not state or state.get("owner") != owner):
            raise AdditionalFailure("authentication", "additional_connection_required")
        if command == "login" and display is None:
            raise AdditionalFailure("owner_ui", "native_ui_unavailable")
        # Do not inherit loader/debug flags or NODE_OPTIONS from the host.
        env = {
            k: v
            for k, v in os.environ.items()
            if k in ("PATH", "HOME", "LANG", "TMPDIR", "SystemRoot")
        }
        proc = subprocess.Popen(
            [node, str(directory / "worker.mjs")],
            cwd=directory,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        dialog = None
        selector = selectors.DefaultSelector()
        try:
            request = {
                "command": command,
                "owner": owner,
                "state": state if command != "login" else None,
                **params,
            }
            proc.stdin.write((json.dumps(request) + "\n").encode())
            proc.stdin.close()
            selector.register(proc.stdout, selectors.EVENT_READ)
            buffer = bytearray()
            deadline = time.monotonic() + (240 if command == "login" else 100)
            result = None
            saved = None
            failure = None
            phase = "adapter_start"
            while time.monotonic() < deadline:
                if dialog and dialog.poll() is not None:
                    raise AdditionalFailure("owner_ui", "owner_cancelled_or_ui_failed")
                events = selector.select(0.25)
                if not events:
                    if proc.poll() is not None:
                        break
                    continue
                chunk = os.read(proc.stdout.fileno(), 65536)
                if not chunk:
                    break
                buffer.extend(chunk)
                if len(buffer) > 48 * 1024 * 1024:
                    raise AdditionalFailure("adapter", "response_too_large")
                while b"\n" in buffer:
                    line, _, remaining = buffer.partition(b"\n")
                    buffer = bytearray(remaining)
                    event = json.loads(line)
                    kind = event.get("event")
                    if kind == "progress" and event.get("phase") in {
                        "phone_wait",
                        "qr_setup",
                        "channel_authorize",
                        "album_list",
                        "album_download",
                        "encrypted_talk",
                        "notes",
                        "talk",
                    }:
                        phase = event["phase"]
                        store.put(
                            "additional_diagnostic",
                            "last",
                            {"phase": phase, "at": time.time(), "state": "running"},
                        )
                        continue
                    if kind in ("qr", "pin") and command == "login":
                        if dialog:
                            dialog.terminate()
                            dialog.wait(timeout=5)
                        dialog = display(kind, event.get("value"))
                    elif kind == "error":
                        allowed = {
                            "owner_mismatch",
                            "group_mapping_unavailable",
                            "group_mapping_ambiguous",
                            "additional_connection_required",
                            "input_invalid",
                            "response_too_large",
                            "destination_denied",
                            "transport_timeout",
                            "transport_failed",
                            "upstream_http_error",
                            "upstream_failed",
                        }
                        reason = event.get("reason")
                        failure = AdditionalFailure(
                            "additional_" + event.get("stage", "adapter")
                            if event.get("stage")
                            in (
                                "input",
                                "authentication",
                                "identity",
                                "content",
                                "qr_session_or_code",
                                "qr_phone_wait",
                                "group_mapping",
                            )
                            else "adapter",
                            reason if reason in allowed else "upstream_failed",
                        )
                        if event.get("state") is None:
                            raise failure
                    if kind in ("result", "error"):
                        saved = event.get("state")
                        if (
                            not isinstance(saved, dict)
                            or saved.get("owner") != owner
                            or not isinstance(saved.get("token"), str)
                            or not isinstance(saved.get("storage"), dict)
                        ):
                            raise AdditionalFailure("identity", "invalid_adapter_result")
                        if result is not None:
                            raise AdditionalFailure("adapter", "duplicate_result")
                        result = event.get("result") if kind == "result" else {}
                    elif kind not in ("qr", "pin"):
                        raise AdditionalFailure("adapter", "invalid_adapter_event")
            if result is None:
                raise AdditionalFailure(phase, "timeout_or_incomplete_response")
            if dialog and dialog.poll() is not None:
                raise AdditionalFailure("owner_ui", "owner_cancelled_or_ui_failed")
            try:
                code = proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                raise AdditionalFailure("adapter", "incomplete_exit") from None
            if code != 0:
                raise AdditionalFailure("adapter", "incomplete_exit")
            store.put("additional_connection", "linejs", saved)
            store.put(
                "additional_diagnostic",
                "last",
                {"phase": phase, "at": time.time(), "state": "failed" if failure else "succeeded"},
            )
            if failure:
                raise failure
            return result
        except (ValueError, TypeError, KeyError):
            raise AdditionalFailure("adapter", "invalid_adapter_response") from None
        finally:
            selector.close()
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            if dialog and dialog.poll() is None:
                dialog.terminate()
                dialog.wait(timeout=5)


def connect(service):
    if service.paused():
        raise ValueError("agent_paused")
    if not available():
        return {"ok": False, "stage": "owner_ui", "reason": "native_ui_unavailable"}
    try:
        # Match independently obtained public keys, not display names or a guessed
        # conversion between Chrome's opaque ID and the desktop protocol's ID.
        import base64
        import hashlib

        def identity_proof(api):
            keys = api.get_e2ee_public_keys_ex(True)
            if not isinstance(keys, list) or not 1 <= len(keys) <= 100:
                raise AdditionalFailure("identity", "owner_proof_unavailable")
            proof = []
            for key in keys:
                if not isinstance(key, dict) or type(key.get("keyId")) is not int:
                    raise AdditionalFailure("identity", "owner_proof_unavailable")
                raw = key.get("keyData")
                if not isinstance(raw, str):
                    raise AdditionalFailure("identity", "owner_proof_unavailable")
                try:
                    public = base64.b64decode(raw, validate=True)
                except ValueError:
                    raise AdditionalFailure("identity", "owner_proof_unavailable") from None
                if len(public) != 32:
                    raise AdditionalFailure("identity", "owner_proof_unavailable")
                proof.append({"key_id": key["keyId"], "sha256": hashlib.sha256(public).hexdigest()})
            return api.tokens.mid, proof

        owner, proof = service.line.call(identity_proof)
        result = invoke(service.store, "login", owner, display=show_auth, public_keys=proof)
        return {
            "ok": result.get("connected") is True,
            "state": "connected",
            "existing_connection_replaced": False,
        }
    except AdditionalFailure as e:
        return {"ok": False, "stage": e.stage, "reason": e.reason}


def status(store):
    state = store.get("additional_connection", "linejs")
    return {
        "configured": bool(state),
        "device": "DESKTOPMAC",
        "live_verified": False,
        "scope": "album_and_note_reads",
        "existing_connection_replaced": False,
    }
