import plistlib
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from line_agent import supervisor


class Stop:
    def __init__(self, limit=20):
        self.calls = 0
        self.limit = limit

    def is_set(self):
        return self.calls >= self.limit

    def wait(self, seconds):
        self.calls += 1
        return self.is_set()


def fixture_paths(tmp_path, monkeypatch):
    target = tmp_path / "release"
    (target / ".venv/bin").mkdir(parents=True)
    monkeypatch.setattr(supervisor, "selected", lambda _: {"current": "a" * 64})
    monkeypatch.setattr(supervisor, "release_path", lambda *_: target)
    return target


def test_crash_restarts_but_does_not_send_or_reset(service, tmp_path, monkeypatch):
    fixture_paths(tmp_path, monkeypatch)
    stop = Stop(5)
    calls = []
    service.store.put("setting", "event_cursor", {"revision": 42})

    def runner(args, **kwargs):
        calls.append(args)
        assert args[1:4] == ["-I", "-B", "-m"] and args[-4:] == [
            "worker",
            "--events",
            "--duration",
            "20",
        ]
        return SimpleNamespace(pid=123, returncode=-9, poll=lambda: -9, wait=lambda **_: None)

    supervisor.supervise(
        tmp_path, tmp_path, "test", "/fixture/node", stop=stop, runner=runner, store=service.store
    )
    assert len(calls) == 3  # Unknown repeated failures become blocked, rather than looping forever.
    assert service.store.get("setting", "event_cursor") == {"revision": 42}
    assert not service.line.calls


def test_pause_never_spawns_or_unpauses(service, tmp_path, monkeypatch):
    fixture_paths(tmp_path, monkeypatch)
    service.pause()

    def forbidden(*a, **k):
        raise AssertionError("must not spawn")

    supervisor.supervise(
        tmp_path,
        tmp_path,
        "test",
        "/fixture/node",
        stop=Stop(2),
        runner=forbidden,
        store=service.store,
    )
    assert service.paused()


def test_fatal_cursor_error_stays_blocked_even_on_supervisor_restart(
    service, tmp_path, monkeypatch
):
    fixture_paths(tmp_path, monkeypatch)
    calls = []

    def runner(*a, **k):
        import time

        calls.append(1)
        service.store.put(
            "setting",
            "receiver",
            {"started_at": time.time(), "reason": "resync_required", "state": "error"},
        )
        return SimpleNamespace(pid=123, returncode=1, poll=lambda: 1, wait=lambda **_: None)

    for _ in range(2):
        supervisor.supervise(
            tmp_path,
            tmp_path,
            "test",
            "/fixture/node",
            stop=Stop(3),
            runner=runner,
            store=service.store,
        )
    assert len(calls) == 1
    assert service.store.get("setting", "supervisor_block")["reason"] == "resync_required"


def test_launch_definition_is_scoped_and_does_not_overwrite(tmp_path, monkeypatch):
    fixture_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(supervisor.sys, "platform", "darwin")
    exe = tmp_path / "python"
    exe.write_text("fixture")
    node = tmp_path / "node"
    node.write_text("fixture")
    directory = tmp_path / "agents"
    result = supervisor.install(tmp_path, tmp_path / "data", "test", exe, node, directory=directory)
    doc = plistlib.loads(Path(result["path"]).read_bytes())
    assert doc["KeepAlive"] and doc["RunAtLoad"] and doc["ThrottleInterval"] == 10
    assert "-I" in doc["ProgramArguments"] and doc["StandardErrorPath"] == "/dev/null"
    assert service_label_distinct(tmp_path)
    Path(result["path"]).write_text("different")
    with pytest.raises(ValueError, match="existing_service_differs"):
        supervisor.install(tmp_path, tmp_path / "data", "test", exe, node, directory=directory)


def service_label_distinct(root):
    return supervisor.service_label(root, "one") != supervisor.service_label(root, "two")


def test_stop_terminates_only_owned_child(service, tmp_path, monkeypatch):
    fixture_paths(tmp_path, monkeypatch)
    stop = threading.Event()
    events = []

    class Child:
        pid = 123
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            events.append("term")
            self.returncode = 0

        def wait(self, **kwargs):
            return self.returncode

    def runner(*args, **kwargs):
        stop.set()
        return Child()

    supervisor.supervise(
        tmp_path, tmp_path, "test", "/fixture/node", stop=stop, runner=runner, store=service.store
    )
    assert events == ["term"]


def test_store_bootstrap_failure_blocks_without_repeated_keychain_attempts(tmp_path, monkeypatch):
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("fixture secret must not appear")

    class Stopped:
        def wait(self):
            calls.append("wait")

    monkeypatch.setattr(supervisor, "supervise", fail)
    supervisor.run_managed(tmp_path, tmp_path / "data", "test", "/fixture/node", Stopped())
    import json

    saved = json.loads(next(tmp_path.glob("*-diagnostic.json")).read_text())
    assert calls == [1, "wait"] and saved["state"] == "blocked"
    assert "fixture secret" not in str(saved)


def test_stop_waits_until_launchd_teardown_finishes(monkeypatch, tmp_path):
    calls = []

    def run(args, **kwargs):
        calls.append(args[1])
        return SimpleNamespace(returncode=113 if len(calls) == 3 else 0)

    monkeypatch.setattr(supervisor.subprocess, "run", run)
    monkeypatch.setattr(supervisor.time, "sleep", lambda _: None)
    result = supervisor.launchctl("stop", tmp_path, "test")
    assert result["ok"] and calls == ["bootout", "print", "print"]
