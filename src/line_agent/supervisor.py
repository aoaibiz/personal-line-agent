"""Per-user receiver supervision. No message sending, credential reset or shell execution."""

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
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from .platform_security import hidden_subprocess_kwargs, private_file, release_python
from .runtime import release_path, safe_directory, selected, write_json
from .store import account_path, open_store

_WINDOWS_JOB_HOLDER = None


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


def windows_task_name(root, account):
    """A deterministic, account-scoped task name; never derived from display names."""
    return "\\PersonalLineAgent\\Receiver-" + hashlib.sha256(
        str(account_path(Path(root).absolute(), account)).encode()
    ).hexdigest()[:16]


def _windows_user_sid():
    """Return the current interactive user's SID without accepting account credentials."""
    if sys.platform != "win32":
        raise ValueError("windows_required")
    import win32api
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
    return win32security.ConvertSidToStringSid(
        win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    )


def _windows_arguments(home, root, account, node):
    # list2cmdline is Windows' CommandLineToArgvW-compatible quoting, including spaces in paths.
    return subprocess.list2cmdline(
        [
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
        ]
    )


def windows_definition(home, root, account, python, node, *, sid=None):
    """Create a least-privilege Task Scheduler XML definition for this Windows user.

    The task has an InteractiveToken principal, so it neither stores nor prompts for a
    password and cannot run before the account has logged on.
    """
    if sys.platform != "win32":
        raise ValueError("windows_required")
    home = safe_directory(home)
    release_path(home, selected(home)["current"])
    python, node = Path(python).absolute(), Path(node).absolute()
    if not python.is_file() or not node.is_file():
        raise ValueError("executable_missing")
    pythonw = python.with_name("pythonw.exe")
    if not pythonw.is_file():
        raise ValueError("windowless_executable_missing")
    sid = sid or _windows_user_sid()
    if not isinstance(sid, str) or not sid.startswith("S-"):
        raise ValueError("interactive_user_unavailable")
    task = ET.Element("Task", {"version": "1.4", "xmlns": "http://schemas.microsoft.com/windows/2004/02/mit/task"})
    registration = ET.SubElement(task, "RegistrationInfo")
    ET.SubElement(registration, "Description").text = "Personal LINE Agent receiver (per-user)"
    principals = ET.SubElement(task, "Principals")
    principal = ET.SubElement(principals, "Principal", {"id": "Owner"})
    ET.SubElement(principal, "UserId").text = sid
    ET.SubElement(principal, "LogonType").text = "InteractiveToken"
    ET.SubElement(principal, "RunLevel").text = "LeastPrivilege"
    triggers = ET.SubElement(task, "Triggers")
    logon = ET.SubElement(triggers, "LogonTrigger")
    ET.SubElement(logon, "UserId").text = sid
    ET.SubElement(logon, "Enabled").text = "true"
    # RestartOnFailure is not reliable for manually-demanded InteractiveToken
    # tasks on this host. A singleton periodic trigger is the OS recovery path.
    timer = ET.SubElement(triggers, "TimeTrigger")
    ET.SubElement(timer, "StartBoundary").text = datetime.now().astimezone().isoformat(timespec="seconds")
    ET.SubElement(timer, "Enabled").text = "true"
    repetition = ET.SubElement(timer, "Repetition")
    ET.SubElement(repetition, "Interval").text = "PT1M"
    ET.SubElement(repetition, "StopAtDurationEnd").text = "false"
    settings = ET.SubElement(task, "Settings")
    for name, value in (
        ("MultipleInstancesPolicy", "IgnoreNew"),
        ("DisallowStartIfOnBatteries", "false"),
        ("StopIfGoingOnBatteries", "false"),
        ("AllowHardTerminate", "true"),
        ("StartWhenAvailable", "true"),
        ("RunOnlyIfNetworkAvailable", "false"),
        ("AllowStartOnDemand", "true"),
        ("Enabled", "true"),
        ("Hidden", "true"),
        ("ExecutionTimeLimit", "PT0S"),
    ):
        ET.SubElement(settings, name).text = value
    restart = ET.SubElement(settings, "RestartOnFailure")
    ET.SubElement(restart, "Interval").text = "PT1M"
    ET.SubElement(restart, "Count").text = "3"
    actions = ET.SubElement(task, "Actions", {"Context": "Owner"})
    execute = ET.SubElement(actions, "Exec")
    # Task Scheduler's Hidden flag hides the task entry, not a console made by python.exe.
    ET.SubElement(execute, "Command").text = str(pythonw)
    ET.SubElement(execute, "Arguments").text = _windows_arguments(home, root, account, node)
    ET.SubElement(execute, "WorkingDirectory").text = str(home)
    return ET.tostring(task, encoding="utf-16", xml_declaration=True)


def _schtasks(args, *, input=None):
    """Run the built-in scheduler without exposing command output to callers."""
    return subprocess.run(
        ["schtasks.exe", *args],
        input=input,
        capture_output=True,
        timeout=30,
        check=False,
        **hidden_subprocess_kwargs(),
    )


def windows_install(home, root, account, python, node):
    data = windows_definition(home, root, account, python, node)
    name = windows_task_name(root, account)
    # schtasks only accepts XML by file. The private, short-lived file is removed even if
    # task registration fails; an existing task is never overwritten with /F.
    home = safe_directory(home)
    fd, temporary = tempfile.mkstemp(prefix=".receiver-task-", suffix=".xml", dir=home)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        private_file(Path(temporary))
        result = _schtasks(["/Create", "/TN", name, "/XML", temporary])
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    if result.returncode:
        # Do not distinguish a conflicting existing task from other raw scheduler output.
        raise ValueError("service_registration_failed")
    return {"label": name, "installed": True}


def _windows_task_state(name):
    """Map Task Scheduler state integers without relying on localized schtasks output."""
    import win32com.client

    service = win32com.client.Dispatch("Schedule.Service")
    service.Connect()
    task = service.GetFolder("\\").GetTask(name)
    return {0: "unknown", 1: "disabled", 2: "queued", 3: "ready", 4: "running"}.get(
        task.State, "unknown"
    )


def _windows_task_instances_running(name):
    """Read actual executions separately: a disabled task can still have one running."""
    import win32com.client

    service = win32com.client.Dispatch("Schedule.Service")
    service.Connect()
    task = service.GetFolder("\\").GetTask(name)
    return task.GetInstances(0).Count > 0


def _windows_worker_job():
    """Create a kill-on-close Job Object; processes are assigned before spawning workers."""
    if sys.platform != "win32":
        return None
    try:
        import win32job

        job = win32job.CreateJobObject(None, "")
        info = win32job.QueryInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation
        )
        info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
        return job
    except Exception as error:
        raise RuntimeError("windows_job_unavailable") from error


def _assign_pid_to_windows_job(job, pid):
    if job is None:
        return
    try:
        import win32api
        import win32con
        import win32job

        process = win32api.OpenProcess(
            win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, pid
        )
        try:
            win32job.AssignProcessToJobObject(job, process)
        finally:
            process.Close()
    except Exception as error:
        raise RuntimeError("windows_job_assignment_failed") from error


def _windows_supervisor_job():
    """Put the supervisor in its own Job Object before it can spawn a worker.

    A process spawned after this assignment inherits the job, including a Node
    child started immediately by the worker. This avoids the attach-after-spawn race.
    """
    global _WINDOWS_JOB_HOLDER
    if _WINDOWS_JOB_HOLDER is not None:
        return _WINDOWS_JOB_HOLDER
    job = _windows_worker_job()
    if job is not None:
        _assign_pid_to_windows_job(job, os.getpid())
        # Keep this handle for the process lifetime. Closing it deliberately would
        # kill the supervisor itself before main can return a successful stop exit.
        _WINDOWS_JOB_HOLDER = job
    return job


def _stop_requested(store):
    requested = store.get("setting", "supervisor_stop") or {}
    return requested.get("requested_at", 0) > requested.get("cleared_at", 0)


def _set_windows_stop_request(root, account, *, requested):
    try:
        store = open_store(root, account)
        current = store.get("setting", "supervisor_stop") or {}
        store.put(
            "setting",
            "supervisor_stop",
            {
                **current,
                ("requested_at" if requested else "cleared_at"): time.time(),
            },
        )
        return True
    except Exception:
        # A forced scheduler stop is the safe fallback when the encrypted store cannot open.
        return False


def _windows_receiver_locks_free(root, account):
    """Prove the account's real supervisor and worker have both exited."""
    try:
        store = open_store(root, account)
        with store.exclusive("supervisor", blocking=False):
            with store.exclusive("worker", blocking=False):
                return True
    except BlockingIOError:
        return False
    except Exception as error:
        raise RuntimeError("service_lock_query_failed") from error


def _wait_for_windows_stop(name, root, account, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            running = _windows_task_instances_running(name)
        except Exception:
            raise RuntimeError("service_instance_query_failed") from None
        if not running and _windows_receiver_locks_free(root, account):
            return True
        time.sleep(0.25)
    return False


def windows_task(verb, root, account):
    if sys.platform != "win32":
        raise ValueError("windows_required")
    if verb not in {"start", "stop", "status"}:
        raise ValueError("invalid_service_operation")
    name = windows_task_name(root, account)
    if verb == "start":
        try:
            idle = _windows_receiver_locks_free(root, account)
        except RuntimeError:
            return {"operation": verb, "label": name, "ok": False, "error": "service_stop_unverified"}
        if not idle:
            return {"operation": verb, "label": name, "ok": False, "error": "service_stop_pending"}
        if not _set_windows_stop_request(root, account, requested=False):
            return {"operation": verb, "label": name, "ok": False, "error": "stop_marker_clear_failed"}
        enabled = _schtasks(["/Change", "/TN", name, "/Enable"])
        if enabled.returncode:
            return {
                "operation": verb,
                "label": name,
                "ok": False,
                "error": "service_enable_failed",
                "exit_code": enabled.returncode,
            }
        result = _schtasks(["/Run", "/TN", name])
    elif verb == "stop":
        marker_written = _set_windows_stop_request(root, account, requested=True)
        disabled = _schtasks(["/Change", "/TN", name, "/Disable"])
        if disabled.returncode:
            return {
                "operation": verb,
                "label": name,
                "ok": False,
                "error": "service_disable_failed",
                "exit_code": disabled.returncode,
            }
        if not marker_written:
            # Without a durable marker, a surviving supervisor cannot be proven to
            # stay stopped. End the dedicated task, then fail closed as unmeasured.
            _schtasks(["/End", "/TN", name])
            return {"operation": verb, "label": name, "ok": False, "error": "service_stop_unverified"}
        try:
            if _wait_for_windows_stop(name, root, account, 70):
                return {"operation": verb, "label": name, "ok": True, "exit_code": 0, "state": "disabled"}
        except RuntimeError:
            # Disable prevents a future timer/logon revival; still ask Scheduler to end
            # the current instance before reporting that its final state is unmeasured.
            pass
        result = _schtasks(["/End", "/TN", name])
    else:
        result = _schtasks(["/Query", "/TN", name])
    value = {"operation": verb, "label": name, "ok": result.returncode == 0, "exit_code": result.returncode}
    if verb == "stop" and result.returncode == 0:
        try:
            stopped = _wait_for_windows_stop(name, root, account, 95)
        except RuntimeError:
            return {"operation": verb, "label": name, "ok": False, "error": "service_stop_unverified"}
        if not stopped:
            return {
                "operation": verb,
                "label": name,
                "ok": False,
                "error": "service_stop_timeout",
                "exit_code": result.returncode,
            }
    if result.returncode == 0:
        try:
            value["state"] = _windows_task_state(name)
        except Exception:
            value["state"] = "unknown"
    return value


def _stop_child(child):
    """Stop only the worker we created, then remove its Windows descendant tree if needed."""
    if child.poll() is not None:
        return
    if sys.platform == "win32":
        # Do this before TerminateProcess/terminate().  Once the root exits,
        # taskkill can no longer discover a Node grandchild through /T.
        try:
            subprocess.run(
                ["taskkill.exe", "/PID", str(child.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
                check=False,
                **hidden_subprocess_kwargs(),
            )
        except (OSError, subprocess.TimeoutExpired):
            # The supervisor's kill-on-close Job Object still owns the inherited tree.
            pass
        try:
            child.wait(timeout=20)
            return
        except subprocess.TimeoutExpired:
            # Do not target an unverified descendant: the parent PID remains the only scope.
            child.kill()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            return
    child.terminate()
    try:
        child.wait(timeout=65)
        return
    except subprocess.TimeoutExpired:
        pass
    child.kill()
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        # The OS scheduler owns the parent supervisor lifetime; never target a guessed PID.
        pass


def install(home, root, account, python, node, *, directory=None):
    value = definition(home, root, account, python, node)
    directory = safe_directory(directory or Path.home() / "Library/LaunchAgents")
    path = directory / (value["Label"] + ".plist")
    data = plistlib.dumps(value)
    if path.exists():
        if path.is_symlink() or path.read_bytes() != data:
            raise ValueError("existing_service_differs")
    else:
        fd = os.open(
            path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    return {"label": value["Label"], "path": str(path), "installed": True}


def launchctl(verb, root, account):
    label = service_label(root, account)
    path = Path.home() / "Library/LaunchAgents" / (label + ".plist")
    # getuid is absent on Windows, where this function is used only by the
    # cross-platform macOS-definition regression tests.
    domain = f"gui/{getattr(os, 'getuid', lambda: 0)()}"
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
    injected_runner = runner is not None
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

    # runner injection is test-only; never attach the pytest process to a kill-on-close job.
    if not injected_runner:
        _windows_supervisor_job()
    with store.exclusive("supervisor", blocking=False):
        try:
            while not stop.is_set():
                if _stop_requested(store):
                    state(state="stopping", child_pid=None)
                    break
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
                            str(release_python(target)),
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
                        **hidden_subprocess_kwargs(),
                    )
                    state(state="running", child_pid=child.pid, release=release)
                    deadline = time.monotonic() + 120
                    timed_out = False
                    while child.poll() is None and not stop.wait(0.25):
                        if time.monotonic() >= deadline:
                            timed_out = True
                            break
                    if (stop.is_set() or timed_out) and child.poll() is None:
                        _stop_child(child)
                    elif child.poll() is None:
                        _stop_child(child)
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
                _stop_child(child)
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
            result = (
                windows_install(args.home, args.root, args.account, sys.executable, args.node)
                if sys.platform == "win32"
                else install(args.home, args.root, args.account, sys.executable, args.node)
            )
        elif args.command == "retry":
            import uuid

            open_store(args.root, args.account).put("setting", "supervisor_retry", uuid.uuid4().hex)
            result = {"retry_requested": True}
        elif args.command in {"start", "stop", "status"}:
            result = (
                windows_task(args.command, args.root, args.account)
                if sys.platform == "win32"
                else launchctl(args.command, args.root, args.account)
            )
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
