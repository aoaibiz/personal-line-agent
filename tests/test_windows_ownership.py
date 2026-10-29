"""Real Windows ownership contracts, including elevated CI tokens."""
import os
import tempfile
from pathlib import Path

import pytest

from line_agent import platform_security as security
from line_agent import runtime

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows ownership contract")


def owner(path):
    import win32security
    return win32security.GetFileSecurity(
        str(path), win32security.OWNER_SECURITY_INFORMATION
    ).GetSecurityDescriptorOwner()


def test_new_private_objects_reopen_with_actual_user_owner(tmp_path):
    home = security.private_directory(tmp_path / "private")
    path = home / "new-file"
    fd = security.private_open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
    os.close(fd)
    user = security._windows_identity()[0]
    assert owner(home) == user
    assert owner(path) == user
    import win32security
    for target in (home, path):
        descriptor = win32security.GetFileSecurity(str(target), win32security.DACL_SECURITY_INFORMATION)
        assert descriptor.GetSecurityDescriptorControl()[0] & win32security.SE_DACL_PROTECTED
    security.private_directory(home, strict=True)
    security.private_file(path, strict=True)


def test_strict_rejects_admin_owner_before_explicit_initialization(tmp_path):
    import pywintypes
    import win32security

    path = tmp_path / "admin-owned"
    path.mkdir()
    descriptor = win32security.SECURITY_DESCRIPTOR()
    admins = win32security.CreateWellKnownSid(win32security.WinBuiltinAdministratorsSid, None)
    descriptor.SetSecurityDescriptorOwner(admins, False)
    try:
        win32security.SetFileSecurity(str(path), win32security.OWNER_SECURITY_INFORMATION, descriptor)
    except pywintypes.error as error:
        if error.winerror in (5, 1307, 1314):
            pytest.skip("Assigning the Administrators owner requires an elevated token")
        raise
    assert owner(path) == admins
    with pytest.raises(PermissionError):
        security.private_directory(path, strict=True)
    assert owner(path) == admins
    security.private_directory(path)
    assert owner(path) == security._windows_identity()[0]
    security.private_directory(path, strict=True)


def test_real_stage_reopens_uv_interpreter_under_current_token():
    source = Path(__file__).parents[1]
    # Avoid pytest's long test-name path exceeding Windows dependency limits.
    with tempfile.TemporaryDirectory(prefix="pla-") as temporary:
        home = Path(temporary)
        security.private_directory(home)
        result = runtime.stage(home, source)
        target = runtime.release_path(home, result["release"])
        assert owner(runtime.release_python(target)) == security._windows_identity()[0]
        reused = runtime.stage(home, source)
        assert reused["reused"] is True
        assert not (home / "active.json").exists()
