import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.fernet import Fernet, InvalidToken

from line_agent.platform_security import private_open
from line_agent.store import Store


def test_private_records_survive_restart_without_plaintext(tmp_path):
    key = Fernet.generate_key()
    store = Store(tmp_path / "one", key)
    store.put("contact", "u-personal-id", {"name": "機密の友達", "text": "秘密の会話"})
    assert Store(tmp_path / "one", key).get("contact", "u-personal-id")["name"] == "機密の友達"
    for file in (tmp_path / "one").iterdir():
        if file.is_file():
            content = file.read_bytes()
            assert b"u-personal-id" not in content
            assert "秘密の会話".encode() not in content


def test_accounts_and_keys_are_isolated(tmp_path):
    one = Store(tmp_path / "one", Fernet.generate_key())
    two = Store(tmp_path / "two", Fernet.generate_key())
    one.put("memory", "same", {"private": True})
    assert two.get("memory", "same") is None
    wrong = Store(tmp_path / "one", Fernet.generate_key())
    with pytest.raises(InvalidToken):
        wrong.records("memory")


def test_atomic_claim_allows_only_one_sender(tmp_path):
    store = Store(tmp_path / "one", Fernet.generate_key())
    store.put("action", "id", {"state": "authorized"})

    def claim(_):
        return store.transition("action", "id", "authorized", "sending")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(claim, range(8)))
    assert sum(r is not None for r in results) == 1


def test_symlink_account_directory_is_rejected(tmp_path):
    (tmp_path / "target").mkdir()
    try:
        (tmp_path / "alias").symlink_to(tmp_path / "target", target_is_directory=True)
    except OSError as error:
        if os.name == "nt" and error.winerror == 1314:
            pytest.skip("Windows symlink privilege is unavailable")
        raise
    with pytest.raises(ValueError):
        Store(tmp_path / "alias", Fernet.generate_key())


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point contract")
def test_windows_junction_account_directory_is_rejected(tmp_path):
    target, alias = tmp_path / "target", tmp_path / "junction"
    target.mkdir()
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(alias), str(target)], capture_output=True, timeout=15
    )
    assert result.returncode == 0
    with pytest.raises(ValueError):
        Store(alias, Fernet.generate_key())


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL contract")
def test_windows_store_directory_has_no_world_acl(tmp_path):
    import win32security

    store = Store(tmp_path / "one", Fernet.generate_key())
    descriptor = win32security.GetFileSecurity(str(store.path), win32security.DACL_SECURITY_INFORMATION)
    dacl = descriptor.GetSecurityDescriptorDacl()
    world = win32security.CreateWellKnownSid(win32security.WinWorldSid, None)
    assert all(dacl.GetAce(index)[2] != world for index in range(dacl.GetAceCount()))


@pytest.mark.skipif(os.name != "nt", reason="Windows create ACL contract")
def test_windows_private_open_sets_acl_at_create_time(tmp_path):
    import win32security

    path = tmp_path / "new-private.json"
    fd = private_open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(fd)
    descriptor = win32security.GetFileSecurity(str(path), win32security.DACL_SECURITY_INFORMATION)
    dacl = descriptor.GetSecurityDescriptorDacl()
    world = win32security.CreateWellKnownSid(win32security.WinWorldSid, None)
    assert all(dacl.GetAce(index)[2] != world for index in range(dacl.GetAceCount()))


def test_exclusive_nonblocking_contention_and_release(tmp_path):
    store = Store(tmp_path / "one", Fernet.generate_key())
    with store.exclusive("sdk"):
        with pytest.raises(BlockingIOError):
            with store.exclusive("sdk", blocking=False):
                pass
    with store.exclusive("sdk", blocking=False):
        pass


@pytest.mark.skipif(os.name != "nt", reason="Windows first-lock creation contract")
def test_windows_concurrent_first_lock_creation_is_safe(tmp_path):
    store = Store(tmp_path / "one", Fernet.generate_key())
    started = threading.Event()

    def acquire(_):
        started.wait()
        with store.exclusive("worker"):
            return True

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(acquire, index) for index in range(8)]
        started.set()
        assert [future.result(timeout=10) for future in futures] == [True] * 8


@pytest.mark.skipif(os.name != "nt", reason="Windows lock contract")
def test_windows_exclusive_lock_rejects_another_process(tmp_path):
    key = Fernet.generate_key()
    store = Store(tmp_path / "one", key)
    code = """
from pathlib import Path
from cryptography.fernet import Fernet
from line_agent.store import Store
import sys
store = Store(Path(sys.argv[1]), bytes(sys.argv[2], 'ascii'))
try:
    with store.exclusive('sdk', blocking=False): pass
except BlockingIOError: raise SystemExit(0)
raise SystemExit(1)
"""
    with store.exclusive("sdk"):
        result = subprocess.run(
            [sys.executable, "-c", code, str(store.path), key.decode()],
            capture_output=True, text=True, timeout=15,
        )
    assert result.returncode == 0


def test_legacy_messages_gain_scope_without_losing_evidence(tmp_path):
    key = Fernet.generate_key()
    store = Store(tmp_path / "one", key)
    store.put("message", "u1:message", {"chat": "u1", "id": "message", "text": "legacy"})
    assert store.count("message", scope="u1") == 0
    reopened = Store(tmp_path / "one", key)
    assert reopened.count("message", scope="u1") == 1
    assert reopened.records("message", scope="u1")[0]["value"]["text"] == "legacy"
