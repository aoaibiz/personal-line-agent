"""macOS receiver supervision. No message sending, credential reset or shell execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from .runtime import release_path, safe_directory, selected, write_json
from .store import account_path, open_store


def service_label(root, account):
    path = account_path(Path(root).absolute(), account)
    return "com.personal-line-agent.receiver." + hashlib.sha256(str(path).encode()).hexdigest()[:16]


def definition(home, root, account, python, node):
    if sys.platform != "darwin":
        raise ValueError("macos_required")
    home = safe_directory(home)
    release_path(home, selected(home)["current"])
    python, node = Path(python).absolute(), Path(node).absolute()
    if not python.is_file() or not node.is_file():
        raise ValueError("executable_missing")
    return {
        "Label": service_label(root, account),
        "ProgramArguments": [
            str(python),
            "-I",
            "-B",
            "-m",
            "line_agent.supervisor",
            "--home",
            str(home),
            "--root",
            str(Path(root).absolute()),
            "--account",
            account,
            "--node",
            str(node),
            "run",
        ],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "ExitTimeOut": 90,
        "ProcessType": "Background",
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
        "WorkingDirectory": str(home),
        "EnvironmentVariables": {"PATH": str(node.parent) + ":/usr/bin:/bin:/usr/sbin:/sbin"},
    }


def install(home, root, account, python, node, *, directory=None):
    value = definition(home, root, account, python, node)
    directory = safe_directory(directory or Path.home() / "Library/LaunchAgents")
    path = directory / (value["Label"] + ".plist")
    data = plistlib.dumps(value)
    if path.exists():
        if path.is_symlink() or path.read_bytes() != data:
            raise ValueError("existing_service_differs")
    else:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    return {"label": value["Label"], "path": str(path), "installed": True}


def launchctl(verb, root, account):
    label = service_label(root, account)
    path = Path.home() / "Library/LaunchAgents" / (label + ".plist")
    domain = f"gui/{os.getuid()}"
    if verb not in {"start", "stop", "status"}:
        raise ValueError("invalid_service_operation")
    if verb == "start":
        if path.is_symlink() or not path.is_file():
            raise ValueError("service_definition_missing")
        args = ["bootstrap", domain, str(path)]
    elif verb == "stop":
        args = ["bootout", f"{domain}/{label}"]
    else:
        args = ["print", f"{domain}/{label}"]
    result = subprocess.run(["/bin/launchctl", *args], capture_output=True, timeout=15)
    if verb == "stop" and result.returncode == 0:
        # bootout acknowledges before teardown finishes. Do not race the next bootstrap.
        deadline = time.monotonic() + 95
        while time.monotonic() < deadline:
            probe = subprocess.run(
                ["/bin/launchctl", "print", f"{domain}/{label}"], capture_output=True, timeout=15
            )
            if probe.returncode != 0:
                break
            time.sleep(0.25)
        else:
            return {
                "operation": verb,
                "label": label,
                "ok": False,
                "error": "service_stop_timeout",
                "exit_code": result.returncode,
            }
    # Never expose launchctl's environment/paths/raw output.
    return {
        "operation": verb,
        "label": label,
        "ok": result.returncode == 0,
        "exit_code": result.returncode,
    }


def supervise(home, root, account, node, *, stop=None, runner=None, store=None):
    stop = stop or threading.Event()
    store = store or open_store(root, account)
    runner = runner or subprocess.Popen
    generation, failures, child = 0, 0, None
    transient = {"stream_transport_failed", "line_transport_failed"}

    def signature(release):
        return {
            "release": release,
            "identity_verified_at": (store.get("setting", "identity") or {}).get("verified_at"),
            "retry": store.get("setting", "supervisor_retry"),
        }

    def state(**fields):
        store.put(
            "setting",
            "receiver_supervisor",
            {
                "pid": os.getpid(),
                "generation": generation,
                "failures": failures,
                "checked_at": time.time(),
                **fields,
            },
        )

    with store.exclusive("supervisor", blocking=False):
        try:
            while not stop.is_set():
                if (store.get("setting", "runtime") or {}).get("paused"):
                    state(state="paused", child_pid=None)
                    stop.wait(2)
                    continue
                try:
                    release = selected(home)["current"]
                    target = release_path(home, release)
                    block = store.get("setting", "supervisor_block")
                    stamp = signature(release)
                    if block and block.get("signature") == stamp:
                        state(
                            state="blocked", child_pid=None, release=release, reason=block["reason"]
                        )
                        stop.wait(2)
                        continue
                    try:
                        with store.exclusive("worker", blocking=False):
                            pass
                    except BlockingIOError:
                        state(
                            state="waiting_for_existing_receiver", child_pid=None, release=release
                        )
                        stop.wait(2)
                        continue
                    generation += 1
                    began = time.time()
                    child = runner(
                        [
                            str(target / ".venv/bin/python"),
                            "-I",
                            "-B",
                            "-m",
                            "line_agent",
                            "--root",
                            str(Path(root).absolute()),
                            "--account",
                            account,
                            "worker",
                            "--events",
                            "--duration",
                            "20",
                        ],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        text=True,
                        env={**os.environ, "LINE_NODE": str(node)},
                    )
                    state(state="running", child_pid=child.pid, release=release)
                    deadline = time.monotonic() + 120
                    timed_out = False
                    while child.poll() is None and not stop.wait(0.25):
                        if time.monotonic() >= deadline:
                            timed_out = True
                            break
                    if (stop.is_set() or timed_out) and child.poll() is None:
                        child.terminate()
                    try:
                        child.wait(timeout=65)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=10)
                    code = child.returncode
                    child = None
                    if stop.is_set():
                        break
                    if code == 0 and not timed_out:
                        failures = 0
                        state(state="restarting", child_pid=None, release=release, exit_code=0)
                        stop.wait(0.5)
                    else:
                        failures += 1
                        delay = min(2 ** min(failures, 6), 60)
                        received = store.get("setting", "receiver") or {}
                        reason = (
                            received.get("reason")
                            if received.get("started_at", 0) >= began
                            else None
                        )
                        reason = (
                            "receiver_timeout" if timed_out else reason or "receiver_process_failed"
                        )
                        if (reason not in transient and reason != "receiver_process_failed") or (
                            reason == "receiver_process_failed" and failures >= 3
                        ):
                            store.put(
                                "setting",
                                "supervisor_block",
                                {"signature": stamp, "reason": reason},
                            )
                            state(state="blocked", child_pid=None, release=release, reason=reason)
                        else:
                            state(
                                state="retry_wait",
                                child_pid=None,
                                release=release,
                                exit_code=code,
                                reason=reason,
                                retry_seconds=delay,
                            )
                            stop.wait(delay)
                except Exception:
                    failures += 1
                    state(
                        state="retry_wait",
                        child_pid=None,
                        reason="supervisor_cycle_failed",
                        retry_seconds=30,
                    )
                    stop.wait(30)
        finally:
            if child and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=65)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=10)
            state(state="stopped", child_pid=None)


def run_managed(home, root, account, node, stop):
    # Keychain/store bootstrap failures must not cause repeated OS prompts via KeepAlive.
    diagnostic = safe_directory(home) / (service_label(root, account) + "-diagnostic.json")
    write_json(diagnostic, {"state": "starting", "pid": os.getpid(), "checked_at": time.time()})
    try:
        supervise(home, root, account, node, stop=stop)
    except Exception:
        write_json(
            diagnostic,
            {
                "state": "blocked",
                "pid": os.getpid(),
                "reason": "supervisor_startup_or_store_failed",
                "checked_at": time.time(),
                "next_action": "owner_agent_inspect_store_then_restart_service",
            },
        )
        stop.wait()  # Owner agent restarts after resolving the cause; no terminal request.


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument(
        "--root", type=Path, default=Path.home() / ".local/share/personal-line-agent"
    )
    parser.add_argument("--account", default="personal")
    parser.add_argument("--node", default=shutil.which("node"))
    parser.add_argument("command", choices=["install", "start", "stop", "status", "retry", "run"])
    args = parser.parse_args()
    try:
        if args.command == "install":
            result = install(args.home, args.root, args.account, sys.executable, args.node)
        elif args.command == "retry":
            import uuid

            open_store(args.root, args.account).put("setting", "supervisor_retry", uuid.uuid4().hex)
            result = {"retry_requested": True}
        elif args.command in {"start", "stop", "status"}:
            result = launchctl(args.command, args.root, args.account)
        else:
            stop = threading.Event()
            for signum in (signal.SIGTERM, signal.SIGINT):
                signal.signal(signum, lambda *_: stop.set())
            run_managed(args.home, args.root, args.account, args.node, stop)
            result = {"state": "stopped"}
        print(json.dumps(result))
        if result.get("ok") is False:
            raise SystemExit(1)
    except Exception:
        print(json.dumps({"ok": False, "error": "receiver_supervision_failed"}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
