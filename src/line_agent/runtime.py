"""Owner-agent release selection, separate from account data and the MCP surface.

The source and candidate are executable code supplied by a trusted local installer.
No MCP/LINE input can select a path, download code, change the selector or authorize it.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .platform_security import (
    exclusive_file,
    hidden_subprocess_kwargs,
    private_directory,
    private_file,
    private_open,
    reject_reparse,
    release_python,
)


class RuntimeFailure(Exception):
    pass


def safe_directory(path):
    path = Path(path).absolute()
    try:
        reject_reparse(path)
    except ValueError:
        raise RuntimeFailure("runtime_path_symlink") from None
    try:
        private_directory(path, strict=True)
    except PermissionError:
        raise RuntimeFailure("runtime_path_not_private") from None
    return path


@contextmanager
def locked(home):
    home = safe_directory(home)
    try:
        with exclusive_file(home / "management.lock", blocking=False):
            yield home
    except BlockingIOError:
        raise RuntimeFailure("runtime_locked") from None


def read_json(path):
    try:
        private_file(path, strict=True)
    except (PermissionError, ValueError):
        raise RuntimeFailure("runtime_metadata_not_private") from None
    fd = private_open(path, os.O_RDONLY)
    with os.fdopen(fd, encoding="utf-8") as stream:
        if os.fstat(stream.fileno()).st_size > 65536:
            raise RuntimeFailure("runtime_metadata_oversize")
        return json.load(stream)


def write_json(path, data):
    path = Path(path)
    safe_directory(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, ensure_ascii=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        private_file(path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def release_path(home, release):
    if not isinstance(release, str) or not re.fullmatch(r"[a-f0-9]{64}", release):
        raise RuntimeFailure("invalid_release_id")
    home = safe_directory(home)
    target = home / "releases" / release
    if not target.is_dir():
        raise RuntimeFailure("release_missing")
    try:
        reject_reparse(target)
    except ValueError:
        raise RuntimeFailure("release_missing") from None
    metadata = read_json(target / "release.json")
    if metadata.get("id") != release or metadata.get("state") != "ready":
        raise RuntimeFailure("release_not_ready")
    # uv uses a Python symlink inside its venv; that specific executable is expected.
    interpreter = release_python(target)
    if not interpreter.is_file():
        raise RuntimeFailure("release_python_missing")
    if os.name == "nt":
        try:
            private_file(interpreter, strict=True)
        except (PermissionError, ValueError):
            raise RuntimeFailure("release_python_not_private") from None
    return target


def selected(home, *, require_ready=True):
    home = safe_directory(home)
    state = read_json(home / "active.json")
    if not isinstance(state, dict) or any(value is not None and
            (not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value))
            for value in (state.get("current"), state.get("previous"))):
        raise RuntimeFailure("invalid_release_selector")
    if not state.get("current"):
        raise RuntimeFailure("invalid_release_selector")
    if require_ready:
        release_path(home, state["current"])
    return state


def stage(home, source):
    """Copy only public source and locked metadata, then install a separate release."""
    source = Path(source).absolute()
    names = ["pyproject.toml", "uv.lock", "README.md", "LICENSE"]
    names += [str(p.relative_to(source)) for p in sorted((source / "src/line_agent").glob("*.py"))]
    if len(names) < 10:
        raise RuntimeFailure("source_incomplete")
    files = {}
    for name in names:
        path = source / name
        try:
            invalid = not path.is_file()
            reject_reparse(path)
        except ValueError:
            invalid = True
        if invalid:
            raise RuntimeFailure("source_file_invalid")
        data = path.read_bytes()
        if len(data) > 4 * 1024 * 1024:
            raise RuntimeFailure("source_file_oversize")
        files[name] = data
    digest = hashlib.sha256()
    for name, data in sorted(files.items()):
        digest.update(name.encode() + b"\0" + str(len(data)).encode() + b"\0" + data)
    release = digest.hexdigest()
    with locked(home) as home:
        releases = safe_directory(home / "releases")
        target = releases / release
        if target.exists():
            safe_directory(target)
            metadata = read_json(target / "release.json") if (target / "release.json").exists() else {}
            if metadata.get("state") == "ready":
                release_path(home, release)
                return {"release": release, "state": "ready", "reused": True}
            # Retry only this never-activated, content-addressed candidate. Never delete it.
            if (home / "active.json").exists() and release in selected(home, require_ready=False).values():
                raise RuntimeFailure("active_release_not_rebuildable")
        else:
            private_directory(target)
        for name, data in files.items():
            path = target / name
            safe_directory(path.parent)
            if path.exists():
                try:
                    reject_reparse(path)
                except ValueError:
                    raise RuntimeFailure("candidate_source_changed") from None
                if path.read_bytes() != data:
                    raise RuntimeFailure("candidate_source_changed")
            else:
                fd = private_open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                private_file(path)
        uv = shutil.which("uv")
        if not uv:
            raise RuntimeFailure("uv_missing")
        # No shell; stdout/stderr can contain private local paths. Keep them out of chat.
        result = subprocess.run([uv, "sync", "--project", str(target), "--python", sys.executable,
            "--frozen", "--no-dev", "--no-editable"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=300, check=False, **hidden_subprocess_kwargs())
        if result.returncode:
            write_json(target / "release.json", {"id": release, "state": "install_failed"})
            raise RuntimeFailure("release_install_failed")
        bootstrap = subprocess.run([str(release_python(target)), "-I", "-c",
            "import line_agent.bridge, line_agent.server, line_agent.lifecycle"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=30, check=False, **hidden_subprocess_kwargs())
        if bootstrap.returncode:
            write_json(target / "release.json", {"id": release, "state": "bootstrap_failed"})
            raise RuntimeFailure("release_bootstrap_failed")
        write_json(target / "release.json", {"id": release, "state": "ready"})
        return {"release": release, "state": "ready", "reused": False}


async def invoke(home, release, root, account, method, *args, elicitation_callback=None):
    target = release_path(home, release)
    params = StdioServerParameters(command=str(release_python(target)),
        args=["-I", "-B", "-m", "line_agent", "--root", str(Path(root).absolute()), "--account", account,
              "mcp", "--confirmation", "owner-delegation"])
    with open(os.devnull, "w") as errors:
        async with stdio_client(params, errlog=errors) as (read, write), ClientSession(
            read, write, read_timeout_seconds=timedelta(seconds=600 if method == "call_tool" and args and args[0] == "authentication_repair" else 180),
            elicitation_callback=elicitation_callback
        ) as session:
            await session.initialize()
            return await getattr(session, method)(*args)


async def activate(home, release, root, account):
    # Serialize stage/activation; calls in flight finish on their original release.
    with locked(home) as home:
        release_path(home, release)
        before = selected(home, require_ready=False) if (home / "active.json").exists() else {}
        result = await invoke(home, release, root, account, "call_tool", "connection_check", {})
        status = result.structuredContent or {}
        if result.isError or status.get("connection_verified") is not True or status.get("store_integrity") != "ok":
            raise RuntimeFailure("candidate_connection_unverified")
        previous = before.get("previous") if before.get("current") == release else before.get("current")
        write_json(home / "active.json", {"current": release, "previous": previous})
        return {"state": "activated", "release": release, "previous": previous,
                "connection_verified": True}


def main():
    parser = argparse.ArgumentParser(description="Owner-agent runtime management; never an MCP tool")
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.home() / ".local/share/personal-line-agent")
    parser.add_argument("--account", default="personal")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    add = commands.add_parser("stage")
    add.add_argument("--source", type=Path, required=True)
    enable = commands.add_parser("activate")
    enable.add_argument("--release", required=True)
    commands.add_parser("rollback")
    args = parser.parse_args()
    try:
        if args.command == "stage":
            result = stage(args.home, args.source)
        elif args.command == "status":
            result = selected(args.home)
        else:
            release = args.release if args.command == "activate" else selected(args.home, require_ready=False).get("previous")
            result = asyncio.run(activate(args.home, release, args.root, args.account))
        print(json.dumps(result))
    except Exception as error:
        reason = str(error) if isinstance(error, RuntimeFailure) else "runtime_management_failed"
        print(json.dumps({"ok": False, "reason": reason, "next_action": "runtime_status"}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
