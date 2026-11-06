"""Separate LINEJS connection. Only this module handles its private stdio state.

Existing credentials, receiver and send ledger are never replaced. No raw API tool.
"""

from __future__ import annotations

import json
import os
import queue
import re
import selectors
import subprocess
import sys
import threading
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
    def __init__(self, stage, reason, diagnostic=None):
        self.stage, self.reason = stage, reason
        self.diagnostic = diagnostic or {}


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


class PipeChunks:
    """Bounded pipe reader: Windows select() accepts sockets, not subprocess pipes."""

    def __init__(self, stream, *, threaded=None):
        self.stream = stream
        self.threaded = sys.platform == "win32" if threaded is None else threaded
        self.selector = None
        self.thread = None
        self.stop = threading.Event()
        self.queue = queue.Queue(maxsize=8)
        if self.threaded:
            self.thread = threading.Thread(target=self._pump, daemon=True)
            self.thread.start()
        else:
            self.selector = selectors.DefaultSelector()
            self.selector.register(stream, selectors.EVENT_READ)

    def _pump(self):
        while not self.stop.is_set():
            try:
                chunk = os.read(self.stream.fileno(), 65536)
            except OSError:
                chunk = False
            while not self.stop.is_set():
                try:
                    self.queue.put(chunk, timeout=0.1)
                    break
                except queue.Full:
                    continue
            if not chunk:
                return

    def read(self, timeout):
        if self.selector is not None:
            return os.read(self.stream.fileno(), 65536) if self.selector.select(timeout) else None
        try:
            chunk = self.queue.get(timeout=timeout)
        except queue.Empty:
            return None
        if chunk is False:
            raise AdditionalFailure("adapter", "pipe_read_failed")
        return chunk

    def close(self):
        self.stop.set()
        if self.selector is not None:
            self.selector.close()
        if self.thread is not None:
            self.thread.join(timeout=1)


def adapter_environment():
    # Windows os.environ keys are uppercase; keep OS bootstrap variables, not loaders.
    allowed = {"PATH", "HOME", "LANG", "TMPDIR", "SYSTEMROOT"}
    return {k: v for k, v in os.environ.items() if k.upper() in allowed}


def write_frame(stream, request, *, timeout=30, abort=None):
    data = memoryview((json.dumps(request) + "\n").encode())
    done = threading.Event()
    failed = []

    def write():
        offset = 0
        try:
            while offset < len(data):
                written = stream.write(data[offset:offset + 65536])
                if not isinstance(written, int) or written <= 0:
                    raise OSError("incomplete_write")
                offset += written
            stream.close()
        except (OSError, ValueError):
            failed.append(True)
        finally:
            done.set()

    writer = threading.Thread(target=write, daemon=True)
    writer.start()
    if not done.wait(timeout):
        if abort is not None:
            abort()
        writer.join(timeout=5)
        raise AdditionalFailure("adapter_input", "pipe_write_timeout")
    writer.join(timeout=1)
    if failed:
        raise AdditionalFailure("adapter_input", "pipe_write_failed")


def invoke(store, command, owner, *, display=None, **params):
    if command not in (
        "login",
        "check",
        "albums",
        "photos",
        "photo",
        "notes",
        "resolve_chat",
        "squares",
        "square_chats",
        "square_get",
        "square_chat_get",
        "square_members",
        "square_messages",
        "note_get",
        "note_create",
        "note_update",
        "note_delete",
        "note_like",
        "note_comment",
        "note_share",
        "call_links",
        "call_link_preflight",
        "call_link_execute",
        "liff_share",
        "square_preflight",
        "square_resolve",
        "square_media_read",
        "birthday_profile",
        "square_execute",
        "profile_image",
        "cover_preflight",
        "cover_set",
        "pin_message",
        "set_chat_bgm",
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
        env = adapter_environment()
        proc = subprocess.Popen(
            [node, str(directory / "worker.mjs")],
            cwd=directory,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        dialog = None
        reader = None
        try:
            request = {
                "command": command,
                "owner": owner,
                "state": state if command != "login" else None,
                **params,
            }
            write_frame(
                proc.stdin, request, abort=lambda: proc.kill() if proc.poll() is None else None
            )
            reader = PipeChunks(proc.stdout)
            buffer = bytearray()
            deadline = time.monotonic() + (240 if command == "login" else 100)
            result = None
            saved = None
            failure = None
            phase = "adapter_start"
            while time.monotonic() < deadline:
                if dialog and dialog.poll() is not None:
                    raise AdditionalFailure("owner_ui", "owner_cancelled_or_ui_failed")
                chunk = reader.read(0.25)
                if chunk is None:
                    continue
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
                            "cover_upstream_failed",
                            "cover_schema_unverified",
                            "cover_changed",
                            "cover_upload_unconfirmed",
                            "cover_apply_unconfirmed",
                            "owner_mismatch",
                            "group_mapping_unavailable",
                            "group_mapping_ambiguous",
                            "additional_connection_required",
                            "call_link_not_owned",
                            "call_link_changed",
                            "liff_consent_required",
                            "liff_connection_failed",
                            "square_not_member",
                            "square_join_state_conflict",
                            "square_role_required",
                            "square_message_not_found",
                            "square_own_message_required",
                            "square_context_changed",
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
                        diagnostic = event.get("diagnostic") or {}
                        failure.diagnostic = {
                            "error_kind": diagnostic.get("error_kind")
                            if diagnostic.get("error_kind")
                            in {
                                "RequestError",
                                "TalkException",
                                "NotAuthorized",
                                "TypeError",
                                "ReferenceError",
                                "TimeoutError",
                                "Other",
                            }
                            else "Other",
                            "line_code": diagnostic.get("line_code")
                            if type(diagnostic.get("line_code")) is int
                            else None,
                            "http_status": diagnostic.get("http_status")
                            if type(diagnostic.get("http_status")) is int
                            else None,
                        }
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
                if proc.poll() not in (None, 0):
                    raise AdditionalFailure("adapter_start", "adapter_process_failed")
                raise AdditionalFailure(phase, "timeout_or_incomplete_response")
            if dialog and dialog.poll() is not None:
                raise AdditionalFailure("owner_ui", "owner_cancelled_or_ui_failed")
            try:
                code = proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                raise AdditionalFailure("adapter", "incomplete_exit") from None
            if code != 0:
                raise AdditionalFailure("adapter", "incomplete_exit")
            if command == "login" and state:
                store.put("additional_connection", "linejs_previous", state)
            store.put("additional_connection", "linejs", saved)
            store.put(
                "additional_diagnostic",
                "last",
                {"phase": phase, "at": time.time(), "state": "failed" if failure else "succeeded"},
            )
            if failure:
                raise failure
            return result
        except AdditionalFailure as error:
            store.put(
                "additional_diagnostic",
                "last",
                {
                    "phase": error.stage,
                    "reason": error.reason,
                    "at": time.time(),
                    "state": "failed",
                    **error.diagnostic,
                },
            )
            raise
        except (ValueError, TypeError, KeyError):
            store.put(
                "additional_diagnostic",
                "last",
                {
                    "phase": "adapter",
                    "reason": "invalid_adapter_response",
                    "at": time.time(),
                    "state": "failed",
                },
            )
            raise AdditionalFailure("adapter", "invalid_adapter_response") from None
        finally:
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            if reader is not None:
                reader.close()
            if dialog and dialog.poll() is None:
                dialog.terminate()
                dialog.wait(timeout=5)


def connect(service, device=None):
    if device is None:
        device = (service.store.get("additional_connection", "linejs") or {}).get(
            "device", "DESKTOPMAC"
        )
    if device not in {"DESKTOPMAC", "IOSIPAD"}:
        raise ValueError("unsupported_additional_device")
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
        result = invoke(
            service.store, "login", owner, display=show_auth, public_keys=proof, device=device
        )
        if result.get("connected") is not True:
            raise AdditionalFailure("authentication", "authentication_not_completed")
        # A successful QR exchange is not yet proof of reusable saved state.
        # Verify a fresh worker using only the saved connection before reporting.
        restored = invoke(service.store, "check", owner)
        if restored.get("connected") is not True:
            raise AdditionalFailure("authentication", "saved_connection_unusable")
        return {
            "ok": True,
            "state": "connected",
            "reconnect_verified": True,
            "existing_connection_replaced": False,
        }
    except AdditionalFailure as e:
        return {"ok": False, "stage": e.stage, "reason": e.reason, **e.diagnostic}


def status(store):
    state = store.get("additional_connection", "linejs")
    return {
        "configured": bool(state),
        "device": (state or {}).get("device", "DESKTOPMAC"),
        "live_verified": False,
        "scope": "typed_additional_features",
        "last_check": store.get("additional_diagnostic", "last"),
        "existing_connection_replaced": False,
    }


def check(service):
    """Verify saved additional state without QR, grant changes or write retries."""
    owner = (service.store.get("setting", "identity") or {}).get("mid")
    try:
        result = invoke(service.store, "check", owner)
        if result.get("connected") is not True:
            raise AdditionalFailure("authentication", "saved_connection_unusable")
        return {"ok": True, "live_verified": True, "existing_connection_replaced": False}
    except AdditionalFailure as error:
        return {
            "ok": False,
            "live_verified": False,
            "stage": error.stage,
            "reason": error.reason,
            **error.diagnostic,
            "owner_authentication_required": error.stage
            in {"authentication", "additional_authentication"}
            and error.diagnostic.get("line_code") == 8,
            "existing_connection_replaced": False,
        }
