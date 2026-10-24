"""Executable discovery must not let the working directory override installed tools."""
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from line_agent import line, supervisor
from line_agent.platform_security import find_executable, windows_system_executable


@pytest.mark.parametrize("name", ["uv", "node", "codex"])
def test_search_ignores_implicit_empty_and_relative_working_directories(tmp_path, monkeypatch, name):
    trusted = tmp_path / "installed"
    working = tmp_path / "downloads"
    trusted.mkdir()
    working.mkdir()
    relative = working / "relative"
    relative.mkdir()
    filename = name + (".exe" if os.name == "nt" else "")
    for folder in (trusted, working, relative):
        executable = folder / filename
        executable.write_text("synthetic, never executed")
        executable.chmod(0o700)
    monkeypatch.chdir(working)
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    monkeypatch.setenv("PATH", os.pathsep.join(["", ".", "relative", str(trusted)]))
    assert find_executable(name) == str(trusted / filename)
    assert find_executable(str(trusted / filename)) == str(trusted / filename)
    assert find_executable(str(Path("relative") / filename)) is None
    monkeypatch.setenv("PATH", os.pathsep.join(["", ".", "relative"]))
    assert find_executable(name) is None


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows executable lookup")
def test_schtasks_does_not_execute_planted_working_directory_binary(tmp_path, monkeypatch):
    import win32api

    system = Path(win32api.GetSystemDirectory())
    # If selected, this harmless copy of cmd.exe exits with the distinctive code 37.
    shutil.copyfile(system / "cmd.exe", tmp_path / "schtasks.exe")
    monkeypatch.chdir(tmp_path)
    assert windows_system_executable("schtasks.exe") == str(system / "schtasks.exe")
    assert windows_system_executable("taskkill.exe") == str(system / "taskkill.exe")
    result = supervisor._schtasks(["/d", "/c", "exit", "37"])
    assert result.returncode != 37
    assert result.returncode != 0  # The real scheduler rejects these invalid arguments.


def test_sdk_receives_resolved_node_and_fails_closed_if_missing(monkeypatch):
    from line_agent.diagnostics import DiagnosticError

    seen = []
    monkeypatch.setattr(line, "find_executable", lambda _: "/installed/node")
    monkeypatch.setattr("okline.OkLine", lambda **kw: seen.append(kw) or SimpleNamespace())
    monkeypatch.setattr("line_agent.group_encryption.install", lambda _: None)
    adapter = line.LineAdapter(None)
    adapter._client()
    assert seen[0]["config"].node_path == "/installed/node"
    monkeypatch.setattr(line, "find_executable", lambda _: None)
    with pytest.raises(DiagnosticError):
        adapter._client()
    assert len(seen) == 1
