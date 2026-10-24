import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp import types

from line_agent import runtime
from line_agent.bridge import create_bridge


@pytest.fixture(autouse=True)
def private_runtime_home(tmp_path):
    runtime.private_directory(tmp_path)


def release(home, marker):
    identity = marker * 64
    target = home / 'releases' / identity
    python = runtime.release_python(target)
    python.parent.mkdir(parents=True)
    python.write_text('fixture')
    runtime.private_file(python)
    (target / 'release.json').write_text(json.dumps({'id': identity, 'state': 'ready'}))
    runtime.private_file(target / 'release.json')
    return identity


def test_activation_rejects_unverified_and_preserves_selector(tmp_path, monkeypatch):
    a, b = release(tmp_path, 'a'), release(tmp_path, 'b')
    runtime.write_json(tmp_path / 'active.json', {'current': a})
    async def denied(*args):
        return SimpleNamespace(isError=False, structuredContent={'connection_verified': False})
    monkeypatch.setattr(runtime, 'invoke', denied)
    with pytest.raises(runtime.RuntimeFailure, match='candidate_connection_unverified'):
        asyncio.run(runtime.activate(tmp_path, b, tmp_path/'account', 'personal'))
    assert runtime.selected(tmp_path)['current'] == a


def test_switch_and_rollback_do_not_touch_account(tmp_path, monkeypatch):
    a, b = release(tmp_path, 'a'), release(tmp_path, 'b')
    data = tmp_path / 'records.sqlite3'
    data.write_bytes(b'untouched-fixture')
    async def healthy(*args):
        return SimpleNamespace(isError=False, structuredContent={
            'connection_verified': True, 'store_integrity': 'ok'})
    monkeypatch.setattr(runtime, 'invoke', healthy)
    asyncio.run(runtime.activate(tmp_path, a, tmp_path, 'personal'))
    asyncio.run(runtime.activate(tmp_path, b, tmp_path, 'personal'))
    assert runtime.selected(tmp_path) == {'current': b, 'previous': a}
    asyncio.run(runtime.activate(tmp_path, runtime.selected(tmp_path)['previous'], tmp_path, 'personal'))
    assert runtime.selected(tmp_path)['current'] == a
    assert data.read_bytes() == b'untouched-fixture'


def test_paths_fail_closed(tmp_path):
    with pytest.raises(runtime.RuntimeFailure):
        runtime.release_path(tmp_path, '../outside')
    home = tmp_path / 'home'
    try:
        home.symlink_to(tmp_path, target_is_directory=True)
    except OSError as error:
        if os.name == "nt" and error.winerror == 1314:
            pytest.skip("Windows symlink privilege is unavailable")
        raise
    with pytest.raises(runtime.RuntimeFailure, match='symlink'):
        runtime.safe_directory(home)
    (tmp_path / 'active.json').symlink_to(tmp_path / 'missing')
    with pytest.raises(runtime.RuntimeFailure, match="runtime_metadata_not_private"):
        runtime.selected(tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="Windows runtime ACL contract")
def test_runtime_metadata_with_protected_world_write_acl_is_rejected(tmp_path):
    import ntsecuritycon
    import win32security

    runtime.safe_directory(tmp_path)
    selector = tmp_path / "active.json"
    selector.write_text('{}')
    world = win32security.CreateWellKnownSid(win32security.WinWorldSid, None)
    dacl = win32security.ACL()
    dacl.AddAccessAllowedAce(dacl.GetAclRevision(), ntsecuritycon.FILE_ALL_ACCESS, world)
    descriptor = win32security.SECURITY_DESCRIPTOR()
    descriptor.SetSecurityDescriptorDacl(1, dacl, 0)
    win32security.SetFileSecurity(
        str(selector),
        win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        descriptor,
    )
    with pytest.raises(runtime.RuntimeFailure, match="runtime_metadata_not_private"):
        runtime.read_json(selector)


def test_stage_failure_does_not_select_candidate(tmp_path, monkeypatch):
    source = Path(__file__).parents[1]
    monkeypatch.setattr(runtime.shutil, 'which', lambda x: '/fixture/uv')
    calls = []
    def failed(*args, **kwargs):
        calls.append(args[0])
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(runtime.subprocess, 'run', failed)
    with pytest.raises(runtime.RuntimeFailure, match='release_install_failed'):
        runtime.stage(tmp_path, source)
    assert not (tmp_path / 'active.json').exists()
    assert calls[0][calls[0].index('--python') + 1] == sys.executable


def test_same_bridge_uses_new_backend_and_never_replays_failure(tmp_path, monkeypatch):
    a, b = release(tmp_path, 'a'), release(tmp_path, 'b')
    runtime.write_json(tmp_path / 'active.json', {'current': a})
    calls = []
    async def invoke(home, selected, root, account, method, *args):
        if method == 'list_tools':
            return types.ListToolsResult(tools=[types.Tool(name='operation_run', inputSchema={'type': 'object'})])
        calls.append(selected)
        if selected == b:
            raise RuntimeError('PRIVATE UPSTREAM ERROR')
        return types.CallToolResult(content=[], structuredContent={'version': selected})
    monkeypatch.setattr('line_agent.bridge.invoke', invoke)
    server = create_bridge(tmp_path, tmp_path, 'personal')
    request = types.CallToolRequest(params=types.CallToolRequestParams(name='operation_run', arguments={'request_id': 'test-uuid'}))
    async def scenario():
        first = (await server.request_handlers[types.CallToolRequest](request)).root
        runtime.write_json(tmp_path / 'active.json', {'current': b, 'previous': a})
        failed = (await server.request_handlers[types.CallToolRequest](request)).root
        assert first.structuredContent['version'] == a
        assert failed.isError and failed.structuredContent['executed'] is None
        assert failed.structuredContent['retry_safe'] is False
        assert failed.structuredContent['request_id'] == 'test-uuid'
        assert 'PRIVATE' not in str(failed)
        runtime.write_json(tmp_path / 'active.json', {'current': a, 'previous': b})
        again = (await server.request_handlers[types.CallToolRequest](request)).root
        assert again.structuredContent['version'] == a
    asyncio.run(scenario())
    assert calls == [a, b, a]


def test_failed_stage_can_retry_without_deleting_or_selecting(tmp_path, monkeypatch):
    source = Path(__file__).parents[1]
    monkeypatch.setattr(runtime.shutil, 'which', lambda x: '/fixture/uv')
    monkeypatch.setattr(runtime.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1))
    with pytest.raises(runtime.RuntimeFailure):
        runtime.stage(tmp_path, source)
    target = next((tmp_path / 'releases').iterdir())
    inode = target.stat().st_ino
    monkeypatch.setattr(runtime.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0))
    result = runtime.stage(tmp_path, source)
    assert result['state'] == 'ready'
    assert target.stat().st_ino == inode
    assert not (tmp_path / 'active.json').exists()


def test_broken_active_backend_can_be_replaced_without_manual_pointer_edit(tmp_path, monkeypatch):
    a, b = release(tmp_path, 'a'), release(tmp_path, 'b')
    runtime.write_json(tmp_path / 'active.json', {'current': a, 'previous': b})
    (tmp_path / 'releases' / a / 'release.json').write_text('{}')
    async def healthy(*args):
        return SimpleNamespace(isError=False, structuredContent={
            'connection_verified': True, 'store_integrity': 'ok'})
    monkeypatch.setattr(runtime, 'invoke', healthy)
    assert runtime.selected(tmp_path, require_ready=False)['previous'] == b
    asyncio.run(runtime.activate(tmp_path, b, tmp_path, 'personal'))
    assert runtime.selected(tmp_path)['current'] == b


def test_backend_isolated_from_hostile_cwd_and_pythonpath(tmp_path, monkeypatch):
    import subprocess
    import sys
    from contextlib import asynccontextmanager
    home = tmp_path / 'managed'
    a = release(home, 'a')
    captured = []
    @asynccontextmanager
    async def intercept(params, **kwargs):
        captured.append(params)
        raise RuntimeError('stop before any account access')
        yield  # pragma: no cover
    monkeypatch.setattr(runtime, 'stdio_client', intercept)
    with pytest.raises(RuntimeError):
        asyncio.run(runtime.invoke(home, a, tmp_path / 'no-account', 'test', 'list_tools'))
    args = captured[0].args
    assert args[:3] == ['-I', '-B', '-m']
    hostile = tmp_path / 'untrusted'
    module = hostile / 'line_agent'
    module.mkdir(parents=True)
    sentinel = tmp_path / 'unexpected-execution'
    (module / '__init__.py').write_text(f'from pathlib import Path\nPath({str(sentinel)!r}).touch()\n')
    result = subprocess.run([sys.executable, *args[:2], '-c', 'import line_agent'],
        cwd=hostile, env={'PYTHONPATH': str(hostile)}, capture_output=True, timeout=10)
    assert result.returncode == 0
    assert not sentinel.exists()
