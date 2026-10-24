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
import tempfile
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class RuntimeFailure(Exception):
    pass


def safe_directory(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in [path, *path.parents]):
        raise RuntimeFailure("runtime_path_symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o022:
        raise RuntimeFailure("runtime_path_not_private")
    return path


@contextmanager
def locked(home):
    import fcntl
    home = safe_directory(home)
    fd = os.open(home / "management.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield home
    finally:
        os.close(fd)


def read_json(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        if os.fstat(stream.fileno()).st_size > 65536:
            raise RuntimeFailure("runtime_metadata_oversize")
        return json.load(stream)


def write_json(path, data):
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, ensure_ascii=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def release_path(home, release):
    if not isinstance(release, str) or not re.fullmatch(r"[a-f0-9]{64}", release):
        raise RuntimeFailure("invalid_release_id")
    home = safe_directory(home)
    target = home / "releases" / release
    if not target.is_dir() or any(p.is_symlink() for p in [target, target.parent]):
        raise RuntimeFailure("release_missing")
    metadata = read_json(target / "release.json")
    if metadata.get("id") != release or metadata.get("state") != "ready":
        raise RuntimeFailure("release_not_ready")
    # uv uses a Python symlink inside its venv; that specific executable is expected.
    if not (target / ".venv/bin/python").is_file():
        raise RuntimeFailure("release_python_missing")
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
        if any(p.is_symlink() for p in [path, *path.parents]) or not path.is_file():
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
            target.mkdir(mode=0o700)
        for name, data in files.items():
            path = target / name
            safe_directory(path.parent)
            if path.exists():
                if path.is_symlink() or path.read_bytes() != data:
                    raise RuntimeFailure("candidate_source_changed")
            else:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
        uv = shutil.which("uv")
        if not uv:
            raise RuntimeFailure("uv_missing")
        # No shell; stdout/stderr can contain private local paths. Keep them out of chat.
        result = subprocess.run([uv, "sync", "--project", str(target), "--frozen", "--no-dev", "--no-editable"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=300, check=False)
        if result.returncode:
            write_json(target / "release.json", {"id": release, "state": "install_failed"})
            raise RuntimeFailure("release_install_failed")
        bootstrap = subprocess.run([str(target / ".venv/bin/python"), "-I", "-c",
            "import line_agent.bridge, line_agent.server, line_agent.lifecycle"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=30, check=False)
        if bootstrap.returncode:
            write_json(target / "release.json", {"id": release, "state": "bootstrap_failed"})
            raise RuntimeFailure("release_bootstrap_failed")
        write_json(target / "release.json", {"id": release, "state": "ready"})
        return {"release": release, "state": "ready", "reused": False}


async def invoke(home, release, root, account, method, *args):
    target = release_path(home, release)
    params = StdioServerParameters(command=str(target / ".venv/bin/python"),
        args=["-I", "-B", "-m", "line_agent", "--root", str(Path(root).absolute()), "--account", account,
              "mcp", "--confirmation", "owner-delegation"])
    with open(os.devnull, "w") as errors:
        async with stdio_client(params, errlog=errors) as (read, write), ClientSession(
            read, write, read_timeout_seconds=timedelta(seconds=180)
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
