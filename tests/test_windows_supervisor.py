import os
import subprocess
import sys
import time
import types
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

from line_agent import supervisor


def _windows(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor.sys, "platform", "win32")
    monkeypatch.setattr(supervisor, "_windows_user_sid", lambda: "S-1-5-21-4242")
    monkeypatch.setattr(supervisor, "safe_directory", lambda value: Path(value))
    monkeypatch.setattr(supervisor, "selected", lambda _: {"current": "a" * 64})
    monkeypatch.setattr(supervisor, "release_path", lambda *_: tmp_path / "release")
    monkeypatch.setattr(supervisor, "_windows_receiver_locks_free", lambda *_a: True)
    python = tmp_path / "python.exe"
    node = tmp_path / "node.exe"
    pythonw = tmp_path / "pythonw.exe"
    python.write_text("fixture")
    pythonw.write_text("fixture")
    node.write_text("fixture")
    return python, node


def _values(xml):
    root = ET.fromstring(xml)
    return {node.tag.rsplit("}", 1)[-1]: node.text for node in root.iter()}


def test_windows_definition_uses_interactive_least_privilege_singleton_task(monkeypatch, tmp_path):
    python, node = _windows(monkeypatch, tmp_path)

    xml = supervisor.windows_definition(
        tmp_path / "managed", tmp_path / "data root", "personal", python, node, sid="S-1-5-21-1"
    )
    doc = _values(xml)
    task = ET.fromstring(xml)
    namespace = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}

    assert doc["LogonType"] == "InteractiveToken"
    assert xml.decode("utf-16").count("S-1-5-21-1") == 2
    assert task.findtext("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval", namespaces=namespace) == "PT1M"
    assert doc["RunLevel"] == "LeastPrivilege"
    assert doc["MultipleInstancesPolicy"] == "IgnoreNew"
    assert doc["Hidden"] == "true"
    assert doc["Count"] == "3" and doc["Interval"] == "PT1M"
    assert doc["Command"] == str(python.with_name("pythonw.exe"))
    assert "line_agent.supervisor" in doc["Arguments"]
    assert "data root" in doc["Arguments"]
    assert supervisor.windows_task_name(tmp_path, "one") != supervisor.windows_task_name(tmp_path, "two")


def test_windows_install_never_overwrites_existing_task_or_leaves_xml(monkeypatch, tmp_path):
    python, node = _windows(monkeypatch, tmp_path)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(supervisor, "_schtasks", run)
    result = supervisor.windows_install(tmp_path, tmp_path / "data", "test", python, node)

    assert result["installed"] and calls[0][0] == "/Create"
    assert "/F" not in calls[0]
    assert not list(tmp_path.glob(".receiver-task-*.xml"))

    monkeypatch.setattr(supervisor, "_schtasks", lambda *_a, **_k: SimpleNamespace(returncode=1))
    with pytest.raises(ValueError, match="service_registration_failed"):
        supervisor.windows_install(tmp_path, tmp_path / "data", "test", python, node)


def test_windows_task_reports_only_normalized_state(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor.sys, "platform", "win32")
    calls = []
    monkeypatch.setattr(
        supervisor,
        "_schtasks",
        lambda args: calls.append(args) or SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(supervisor, "_windows_task_state", lambda _: "running")

    result = supervisor.windows_task("status", tmp_path, "test")

    assert result["ok"] and result["state"] == "running"
    assert calls == [["/Query", "/TN", supervisor.windows_task_name(tmp_path, "test")]]


def test_windows_stop_requests_graceful_exit_before_scheduler_end(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor.sys, "platform", "win32")
    monkeypatch.setattr(supervisor, "_set_windows_stop_request", lambda *_a, **_k: True)
    monkeypatch.setattr(supervisor, "_wait_for_windows_stop", lambda *_a, **_k: True)
    calls = []
    monkeypatch.setattr(
        supervisor, "_schtasks", lambda args: calls.append(args) or SimpleNamespace(returncode=0)
    )

    assert supervisor.windows_task("stop", tmp_path, "test")["state"] == "disabled"
    assert calls == [["/Change", "/TN", supervisor.windows_task_name(tmp_path, "test"), "/Disable"]]


def test_windows_start_clears_marker_enables_then_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor.sys, "platform", "win32")
    monkeypatch.setattr(supervisor, "_windows_receiver_locks_free", lambda *_a: True)
    calls = []
    monkeypatch.setattr(supervisor, "_set_windows_stop_request", lambda *_a, **_k: True)
    monkeypatch.setattr(
        supervisor, "_schtasks", lambda args: calls.append(args) or SimpleNamespace(returncode=0)
    )

    assert supervisor.windows_task("start", tmp_path, "test")["ok"]
    assert calls == [
        ["/Change", "/TN", supervisor.windows_task_name(tmp_path, "test"), "/Enable"],
        ["/Run", "/TN", supervisor.windows_task_name(tmp_path, "test")],
    ]


def test_windows_refuses_start_but_stops_task_when_marker_write_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor.sys, "platform", "win32")
    monkeypatch.setattr(supervisor, "_windows_receiver_locks_free", lambda *_a: True)
    monkeypatch.setattr(supervisor, "_set_windows_stop_request", lambda *_a, **_k: False)
    calls = []
    monkeypatch.setattr(
        supervisor, "_schtasks", lambda args: calls.append(args) or SimpleNamespace(returncode=0)
    )
    monkeypatch.setattr(supervisor, "_wait_for_windows_stop", lambda *_a, **_k: True)

    assert supervisor.windows_task("start", tmp_path, "test")["error"] == "stop_marker_clear_failed"
    result = supervisor.windows_task("stop", tmp_path, "test")
    assert not result["ok"] and result["error"] == "service_stop_unverified"
    assert calls == [
        ["/Change", "/TN", supervisor.windows_task_name(tmp_path, "test"), "/Disable"],
        ["/End", "/TN", supervisor.windows_task_name(tmp_path, "test")],
    ]


def test_windows_start_refuses_to_clear_stop_marker_while_old_receiver_holds_lock(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor.sys, "platform", "win32")
    monkeypatch.setattr(supervisor, "_windows_receiver_locks_free", lambda *_a: False)
    monkeypatch.setattr(supervisor, "_set_windows_stop_request", lambda *_a, **_k: pytest.fail("must not clear"))

    assert supervisor.windows_task("start", tmp_path, "test")["error"] == "service_stop_pending"


def test_windows_stop_probe_uses_instances_not_disabled_task_state(monkeypatch):
    def running(count):
        task = SimpleNamespace(GetInstances=lambda _flags: SimpleNamespace(Count=count))
        service = SimpleNamespace(
            Connect=lambda: None,
            GetFolder=lambda _path: SimpleNamespace(GetTask=lambda _name: task),
        )
        client = types.ModuleType("win32com.client")
        client.Dispatch = lambda _name: service
        package = types.ModuleType("win32com")
        package.client = client
        monkeypatch.setitem(sys.modules, "win32com", package)
        monkeypatch.setitem(sys.modules, "win32com.client", client)
        return supervisor._windows_task_instances_running("\\PersonalLineAgent\\fixture")

    # A disabled task can still have an active supervisor instance; Count is authoritative.
    assert running(1) is True
    assert running(0) is False


def test_windows_wait_requires_free_supervisor_and_worker_locks(monkeypatch):
    monotonic = iter([0, 0, 1])
    monkeypatch.setattr(supervisor, "_windows_task_instances_running", lambda _name: False)
    monkeypatch.setattr(supervisor, "_windows_receiver_locks_free", lambda *_a: False)
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: next(monotonic))
    monkeypatch.setattr(supervisor.time, "sleep", lambda _seconds: None)

    assert not supervisor._wait_for_windows_stop("\\PersonalLineAgent\\fixture", "root", "test", 0.5)


def test_windows_stop_ends_task_after_initial_instance_query_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor.sys, "platform", "win32")
    monkeypatch.setattr(supervisor, "_set_windows_stop_request", lambda *_a, **_k: True)
    calls = []
    monkeypatch.setattr(
        supervisor, "_schtasks", lambda args: calls.append(args) or SimpleNamespace(returncode=0)
    )
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("fixture")

    monkeypatch.setattr(supervisor, "_wait_for_windows_stop", unavailable)

    result = supervisor.windows_task("stop", tmp_path, "test")

    assert not result["ok"] and result["error"] == "service_stop_unverified"
    assert calls == [
        ["/Change", "/TN", supervisor.windows_task_name(tmp_path, "test"), "/Disable"],
        ["/End", "/TN", supervisor.windows_task_name(tmp_path, "test")],
    ]


def test_windows_child_tree_is_killed_before_root_is_terminated(monkeypatch):
    monkeypatch.setattr(supervisor.sys, "platform", "win32")
    monkeypatch.setattr(supervisor, "windows_system_executable", lambda name: "/system/" + name)
    events = []

    class Child:
        pid = 8123
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            events.append("terminate")

        def wait(self, timeout):
            events.append(f"wait:{timeout}")
            self.returncode = -9

    def run(args, **kwargs):
        events.append(args)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(supervisor.subprocess, "run", run)
    supervisor._stop_child(Child())

    assert events[0] == ["/system/taskkill.exe", "/PID", "8123", "/T", "/F"]
    assert events[1] == "wait:20"
    assert "terminate" not in events


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Objects are native-only")
def test_windows_job_object_kills_harmless_descendant_on_close(tmp_path):
    """A real process-tree check: closing the supervisor-held job kills descendants."""
    marker = tmp_path / "descendant-survived"
    child = (
        "import pathlib,time; time.sleep(1.5); "
        f"pathlib.Path({str(marker)!r}).write_text('unexpected')"
    )
    parent = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import subprocess,sys,time; "
            f"subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(30)",
        ]
    )
    job = supervisor._windows_worker_job()
    try:
        supervisor._assign_pid_to_windows_job(job, parent.pid)
        job.Close()
        parent.wait(timeout=10)
        time.sleep(2)
        assert not marker.exists()
    finally:
        if parent.poll() is None:
            subprocess.run(
                [supervisor.windows_system_executable("taskkill.exe"), "/PID", str(parent.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows taskkill is native-only")
def test_windows_stop_child_kills_real_worker_descendant_while_supervisor_survives(tmp_path):
    """Regression for terminate-before-taskkill: the worker PID remains available to /T."""
    marker = tmp_path / "descendant-survived"
    descendant = (
        "import pathlib,time; time.sleep(1.5); "
        f"pathlib.Path({str(marker)!r}).write_text('unexpected')"
    )
    worker = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import subprocess,sys,time; "
            f"child=subprocess.Popen([sys.executable, '-c', {descendant!r}]); "
            "print(child.pid, flush=True); time.sleep(30)",
        ]
        ,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert worker.stdout is not None and worker.stdout.readline().strip().isdigit()
        supervisor._stop_child(worker)
        assert worker.poll() is not None
        time.sleep(2)
        assert not marker.exists()
    finally:
        if worker.poll() is None:
            subprocess.run(
                [supervisor.windows_system_executable("taskkill.exe"), "/PID", str(worker.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )


@pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("PERSONAL_LINE_RUN_SCHEDULER_PROBE") != "1",
    reason="explicit Windows Task Scheduler probe only",
)
def test_windows_scheduler_restarts_harmless_nonzero_manual_run(monkeypatch, tmp_path):
    """Exercise the generated recovery settings without touching an account task."""
    monkeypatch.setattr(supervisor.sys, "platform", "win32")
    monkeypatch.setattr(supervisor, "safe_directory", lambda value: Path(value))
    monkeypatch.setattr(supervisor, "selected", lambda _: {"current": "a" * 64})
    monkeypatch.setattr(supervisor, "release_path", lambda *_: tmp_path / "release")
    monkeypatch.setattr(supervisor, "_windows_receiver_locks_free", lambda *_a: True)
    name = supervisor.windows_task_name(tmp_path / "data", "probe" + uuid.uuid4().hex)
    marker = tmp_path / "restarted"
    count = tmp_path / "count"
    python = Path(sys.executable)
    xml = ET.fromstring(
        supervisor.windows_definition(tmp_path, tmp_path / "data", "probe", python, python)
    )
    namespace = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    code = (
        "import os; from pathlib import Path; "
        f"count=Path({str(count)!r}); marker=Path({str(marker)!r}); "
        "value=int(count.read_text())+1 if count.exists() else 1; count.write_text(str(value)); "
        "(marker.write_text(str(value)) if value > 1 else None); os._exit(0 if value > 1 else 1)"
    )
    xml.find("t:Actions/t:Exec/t:Arguments", namespace).text = subprocess.list2cmdline(
        ["-I", "-c", code]
    )
    definition = tmp_path / "restart-probe.xml"
    definition.write_bytes(ET.tostring(xml, encoding="utf-16", xml_declaration=True))
    registered = False
    try:
        assert supervisor._schtasks(["/Create", "/TN", name, "/XML", str(definition)]).returncode == 0
        registered = True
        assert supervisor._schtasks(["/Run", "/TN", name]).returncode == 0
        deadline = time.monotonic() + 80
        while time.monotonic() < deadline and not marker.exists():
            time.sleep(0.25)
        assert marker.exists(), "Task Scheduler did not restart the failed manual run"
        before_stop = int(count.read_text())
        assert supervisor._schtasks(["/Change", "/TN", name, "/Disable"]).returncode == 0
        assert supervisor._wait_for_windows_stop(name, tmp_path, "probe", 15)
        time.sleep(65)
        assert int(count.read_text()) == before_stop, "disabled task revived on its timer"
        assert supervisor._schtasks(["/Change", "/TN", name, "/Enable"]).returncode == 0
        assert supervisor._schtasks(["/Run", "/TN", name]).returncode == 0
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and int(count.read_text()) <= before_stop:
            time.sleep(0.25)
        assert int(count.read_text()) > before_stop, "enabled task did not resume"
    finally:
        if registered:
            supervisor._schtasks(["/End", "/TN", name])
            supervisor._wait_for_windows_stop(name, tmp_path, "probe", 15)
            supervisor._schtasks(["/Delete", "/TN", name, "/F"])
