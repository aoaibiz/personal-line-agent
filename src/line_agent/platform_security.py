"""Small, fail-closed filesystem primitives for private agent state."""
from __future__ import annotations

import errno
import os
import stat
import time
from contextlib import contextmanager
from pathlib import Path


def _reparse(path: Path) -> bool:
    try:
        return bool(os.lstat(path).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except FileNotFoundError:
        return False
    except AttributeError:  # POSIX
        return path.is_symlink()


def reject_reparse(path: Path) -> None:
    path = Path(path).absolute()
    # Checking each component prevents a junction from redirecting private data.
    for item in (path, *path.parents):
        if _reparse(item):
            raise ValueError("private_path_reparse")


def _windows_identity():
    import win32api
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
    return (
        win32security.GetTokenInformation(token, win32security.TokenUser)[0],
        win32security.CreateWellKnownSid(win32security.WinLocalSystemSid, None),
        win32security.CreateWellKnownSid(win32security.WinBuiltinAdministratorsSid, None),
    )


def current_os_principal() -> str | int:
    """Stable local OS principal without exposing account names."""
    if os.name != "nt":
        return os.geteuid()
    import win32security
    return win32security.ConvertSidToStringSid(_windows_identity()[0])


def _windows_descriptor():
    """Give the current user, SYSTEM and Administrators full control only.

    A protected DACL is required; chmod has no privacy meaning on Windows.
    """
    import ntsecuritycon
    import win32security

    user, system, admins = _windows_identity()
    acl = win32security.ACL()
    for sid in (user, system, admins):
        inherit = win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE
        acl.AddAccessAllowedAceEx(acl.GetAclRevision(), inherit, ntsecuritycon.FILE_ALL_ACCESS, sid)
    descriptor = win32security.SECURITY_DESCRIPTOR()
    # Elevated tokens can default new objects to the Administrators group.
    # Bind our new private objects to the actual user, as strict reads require.
    descriptor.SetSecurityDescriptorOwner(user, False)
    descriptor.SetSecurityDescriptorDacl(1, acl, 0)
    return descriptor


def _windows_acl(path: Path) -> None:
    import win32security
    descriptor = _windows_descriptor()
    existing = win32security.GetFileSecurity(str(path), win32security.OWNER_SECURITY_INFORMATION)
    flags = win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION
    if existing.GetSecurityDescriptorOwner() != descriptor.GetSecurityDescriptorOwner():
        # Only explicit initialization reaches this with a different owner:
        # strict callers validate the existing owner before calling us.
        flags |= win32security.OWNER_SECURITY_INFORMATION
    win32security.SetFileSecurity(str(path), flags, descriptor)


def _windows_acl_private(path: Path) -> bool:
    import ntsecuritycon
    import win32security

    user, system, admins = _windows_identity()
    allowed = (user, system, admins)
    descriptor = win32security.GetFileSecurity(
        str(path), win32security.DACL_SECURITY_INFORMATION | win32security.OWNER_SECURITY_INFORMATION
    )
    if descriptor.GetSecurityDescriptorOwner() != user:
        return False
    dacl = descriptor.GetSecurityDescriptorDacl()
    if dacl is None:
        return False
    dangerous = ntsecuritycon.FILE_GENERIC_WRITE | ntsecuritycon.WRITE_DAC | ntsecuritycon.WRITE_OWNER | ntsecuritycon.DELETE
    for index in range(dacl.GetAceCount()):
        ace = dacl.GetAce(index)
        if ace[0][0] == win32security.ACCESS_ALLOWED_ACE_TYPE and ace[1] & dangerous:
            if all(ace[2] != principal for principal in allowed):
                return False
    return True


def private_directory(path: Path, *, strict: bool = False) -> Path:
    path = Path(path).absolute()
    reject_reparse(path)
    existed = path.exists()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    reject_reparse(path)
    if os.name == "nt":
        try:
            if strict and existed and not _windows_acl_private(path):
                raise PermissionError("private_directory_not_private")
            _windows_acl(path)
        except Exception as error:
            raise PermissionError("private_acl_unavailable") from error
    else:
        info = path.stat()
        if strict and (info.st_uid != os.getuid() or info.st_mode & 0o022):
            raise PermissionError("private_directory_not_private")
        if strict:
            return path
        os.chmod(path, 0o700)
        info = path.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PermissionError("private_directory_not_private")
    return path


def private_open(path: Path, flags: int, mode: int = 0o600) -> int:
    """Open a regular non-reparse private file, preserving O_NOFOLLOW on POSIX."""
    path = Path(path)
    reject_reparse(path)
    if os.name != "nt":
        return os.open(path, flags | os.O_NOFOLLOW, mode)
    # Python's Windows os.open cannot request OPEN_REPARSE_POINT.  CreateFile can.
    import msvcrt

    import win32con
    import win32file

    create = flags & os.O_CREAT
    exclusive = flags & os.O_EXCL
    write = flags & (os.O_WRONLY | os.O_RDWR)
    access = win32con.GENERIC_READ | (win32con.GENERIC_WRITE if write else 0)
    disposition = win32con.CREATE_NEW if create and exclusive else (
        win32con.OPEN_ALWAYS if create else win32con.OPEN_EXISTING
    )
    try:
        handle = win32file.CreateFile(
            str(path), access, win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE,
            _windows_security_attributes() if create else None, disposition,
            win32con.FILE_ATTRIBUTE_NORMAL | 0x00200000, None,  # FILE_FLAG_OPEN_REPARSE_POINT
        )
    except Exception as error:
        code = getattr(error, "winerror", None)
        if code is None and getattr(error, "args", None):
            code = error.args[0]
        if exclusive and code == 80:
            raise FileExistsError(path) from error
        raise
    try:
        info = win32file.GetFileInformationByHandle(handle)
        if info[0] & win32con.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError("private_file_reparse")
        return msvcrt.open_osfhandle(int(handle.Detach()), flags | os.O_BINARY)
    except Exception:
        handle.Close()
        raise


def _windows_security_attributes():
    import pywintypes

    attributes = pywintypes.SECURITY_ATTRIBUTES()
    attributes.SECURITY_DESCRIPTOR = _windows_descriptor()
    return attributes


def private_file(path: Path, *, strict: bool = False) -> None:
    reject_reparse(path)
    if Path(path).exists() and not Path(path).is_file():
        raise ValueError("private_file_invalid")
    if os.name == "nt":
        try:
            if strict and not _windows_acl_private(Path(path)):
                raise PermissionError("private_file_not_private")
            _windows_acl(Path(path))
        except Exception as error:
            raise PermissionError("private_acl_unavailable") from error
    else:
        if strict:
            return
        os.chmod(path, 0o600)


@contextmanager
def exclusive_file(path: Path, blocking: bool = True):
    try:
        fd = private_open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        created = True
    except FileExistsError:
        fd = private_open(path, os.O_RDWR, 0o600)
        created = False
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            while True:
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    break
                except OSError as error:
                    if error.errno not in (errno.EACCES, errno.EDEADLK):
                        raise
                    if not blocking:
                        raise BlockingIOError("private_lock_busy") from error
                    time.sleep(0.05)
            try:
                if created:
                    # The lock covers byte zero before it is materialized.  Another
                    # first opener can therefore never hold the byte while this write runs.
                    os.write(fd, b"0")
                    os.lseek(fd, 0, os.SEEK_SET)
                yield
            finally:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def release_python(target: Path) -> Path:
    return Path(target) / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")


def hidden_subprocess_kwargs() -> dict:
    if os.name != "nt":
        return {}
    import subprocess
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    return {"startupinfo": startup, "creationflags": subprocess.CREATE_NO_WINDOW}
