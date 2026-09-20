"""Strict Windows ACL support for AstroDeck's private state.

The functions in this module intentionally use only :mod:`ctypes` and Win32
handle APIs.  In particular, ACL changes are made through ``SetSecurityInfo``
on a handle opened with ``FILE_FLAG_OPEN_REPARSE_POINT``.  That keeps a
junction or symlink from redirecting a security operation to another object.

The module is safe to import on non-Windows hosts.  Win32 DLLs are loaded only
after a public operation that actually requires Windows is called.
"""
from __future__ import annotations

import ctypes
import os
import re
from contextlib import contextmanager

# ``ctypes.set_last_error`` / ``get_last_error`` exist only on Windows. The
# Win32 paths below are only ever *invoked* on Windows, but the portable ACL
# orchestration tests drive them on every platform with a fake kernel32 (that
# is the point of those tests: the DACL logic is graded on Linux CI too), and
# the first Linux run tripped on the bare attribute (2026-09-05). On Windows
# these are the real functions; elsewhere they are inert.
_set_last_error = getattr(ctypes, "set_last_error", lambda code: None)
_get_last_error = getattr(ctypes, "get_last_error", lambda: 0)
from ctypes import wintypes
from pathlib import Path
from typing import Iterator


class PrivateAclError(RuntimeError):
    """A private-state ACL cannot be established or verified safely."""


_SYSTEM_SID = "S-1-5-18"
_ADMINISTRATORS_SID = "S-1-5-32-544"
_SID_RE = re.compile(r"S-[0-9]+(?:-[0-9]+)+\Z", re.IGNORECASE)
_ACL_FILESYSTEMS = frozenset({"ntfs", "refs"})

_TOKEN_QUERY = 0x0008
_TOKEN_USER_CLASS = 1
_ERROR_INSUFFICIENT_BUFFER = 122
_ERROR_FILE_NOT_FOUND = 2
_ERROR_PATH_NOT_FOUND = 3
_ERROR_ALREADY_EXISTS = 183

_READ_CONTROL = 0x00020000
_WRITE_DAC = 0x00040000
_FILE_READ_ATTRIBUTES = 0x00000080
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_DELETE = 0x00000004
_OPEN_EXISTING = 3
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_ATTRIBUTE_TAG_INFO_CLASS = 9

_SE_FILE_OBJECT = 1
_OWNER_SECURITY_INFORMATION = 0x00000001
_DACL_SECURITY_INFORMATION = 0x00000004
_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
_SE_DACL_PROTECTED = 0x1000
_SDDL_REVISION_1 = 1

_ACL_SIZE_INFORMATION_CLASS = 2
_ACCESS_ALLOWED_ACE_TYPE = 0x00
_OBJECT_INHERIT_ACE = 0x01
_CONTAINER_INHERIT_ACE = 0x02
_FILE_ALL_ACCESS = 0x001F01FF


class _SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class _TOKEN_USER(ctypes.Structure):
    _fields_ = [("User", _SID_AND_ATTRIBUTES)]


class _SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    ]


class _FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
    _fields_ = [
        ("FileAttributes", wintypes.DWORD),
        ("ReparseTag", wintypes.DWORD),
    ]


class _ACL_SIZE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("AceCount", wintypes.DWORD),
        ("AclBytesInUse", wintypes.DWORD),
        ("AclBytesFree", wintypes.DWORD),
    ]


class _ACE_HEADER(ctypes.Structure):
    _fields_ = [
        ("AceType", ctypes.c_ubyte),
        ("AceFlags", ctypes.c_ubyte),
        ("AceSize", wintypes.WORD),
    ]


class _ACCESS_ALLOWED_ACE(ctypes.Structure):
    _fields_ = [
        ("Header", _ACE_HEADER),
        ("Mask", wintypes.DWORD),
        ("SidStart", wintypes.DWORD),
    ]


def build_private_sddl(user_sid: str, *, directory: bool) -> str:
    """Return the exact protected DACL for one runtime identity."""
    if not isinstance(user_sid, str) or not _SID_RE.fullmatch(user_sid):
        raise PrivateAclError(f"invalid Windows user SID: {user_sid!r}")
    flags = "OICI" if directory else ""
    return (
        f"D:P(A;{flags};FA;;;{user_sid})"
        f"(A;{flags};FA;;;SY)"
        f"(A;{flags};FA;;;BA)"
    )


def validate_acl_filesystem_name(name: str) -> None:
    """Reject every filesystem except NTFS and ReFS."""
    if not isinstance(name, str) or name.casefold() not in _ACL_FILESYSTEMS:
        shown = name if isinstance(name, str) and name else "<unknown>"
        raise PrivateAclError(
            f"private Windows state requires NTFS or ReFS; found {shown}"
        )


def _require_windows() -> None:
    if os.name != "nt":
        raise PrivateAclError("Windows ACL support was called on a non-Windows host")


def _format_error(code: int) -> str:
    try:
        return ctypes.FormatError(code).strip()
    except Exception:  # pragma: no cover - only a diagnostic fallback
        return f"Win32 error {code}"


def _last_error(operation: str, path: Path | str | None = None) -> PrivateAclError:
    code = int(_get_last_error())
    suffix = f" for {path}" if path is not None else ""
    return PrivateAclError(
        f"{operation} failed{suffix}: {_format_error(code)} ({code})"
    )


def _status_error(
    operation: str, status: int, path: Path | str | None = None
) -> PrivateAclError:
    suffix = f" for {path}" if path is not None else ""
    return PrivateAclError(
        f"{operation} failed{suffix}: {_format_error(int(status))} ({int(status)})"
    )


def _win32():
    """Load Win32 libraries lazily and declare all signatures used below."""
    _require_windows()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = (
        wintypes.BOOL
    )
    advapi32.GetSecurityDescriptorDacl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.BOOL),
    ]
    advapi32.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    advapi32.GetSecurityDescriptorControl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetSecurityDescriptorControl.restype = wintypes.BOOL
    advapi32.GetSecurityInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetSecurityInfo.restype = wintypes.DWORD
    advapi32.SetSecurityInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    advapi32.SetSecurityInfo.restype = wintypes.DWORD
    advapi32.GetAclInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_int,
    ]
    advapi32.GetAclInformation.restype = wintypes.BOOL
    advapi32.GetAce.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetAce.restype = wintypes.BOOL

    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel32.CreateDirectoryW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(_SECURITY_ATTRIBUTES),
    ]
    kernel32.CreateDirectoryW.restype = wintypes.BOOL
    kernel32.GetVolumePathNameW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    kernel32.GetVolumePathNameW.restype = wintypes.BOOL
    kernel32.GetVolumeInformationW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    kernel32.GetVolumeInformationW.restype = wintypes.BOOL
    return kernel32, advapi32


def _sid_string(kernel32, advapi32, sid_pointer: int) -> str:
    text = wintypes.LPWSTR()
    if not advapi32.ConvertSidToStringSidW(
        ctypes.c_void_p(sid_pointer), ctypes.byref(text)
    ):
        raise _last_error("ConvertSidToStringSidW")
    try:
        value = text.value
        if not value:
            raise PrivateAclError("ConvertSidToStringSidW returned an empty SID")
        return value
    finally:
        kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))


def current_user_sid() -> str:
    """Return the process token user SID, never an impersonated thread SID."""
    kernel32, advapi32 = _win32()
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)
    ):
        raise _last_error("OpenProcessToken")
    try:
        needed = wintypes.DWORD()
        _set_last_error(0)
        ok = advapi32.GetTokenInformation(
            token,
            _TOKEN_USER_CLASS,
            None,
            0,
            ctypes.byref(needed),
        )
        error = int(_get_last_error())
        if ok or error != _ERROR_INSUFFICIENT_BUFFER or not needed.value:
            raise PrivateAclError(
                "GetTokenInformation(size) failed: "
                f"{_format_error(error)} ({error})"
            )
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi32.GetTokenInformation(
            token,
            _TOKEN_USER_CLASS,
            buffer,
            needed,
            ctypes.byref(needed),
        ):
            raise _last_error("GetTokenInformation(TokenUser)")
        token_user = ctypes.cast(buffer, ctypes.POINTER(_TOKEN_USER)).contents
        return _sid_string(kernel32, advapi32, token_user.User.Sid)
    finally:
        if token.value:
            kernel32.CloseHandle(token)


def _absolute(path: Path | str) -> Path:
    try:
        return Path(os.path.abspath(os.fspath(path)))
    except (OSError, TypeError, ValueError) as exc:
        raise PrivateAclError(f"invalid private-state path {path!r}: {exc}") from exc


def require_acl_capable_filesystem(path: Path) -> None:
    """Require that ``path`` resides on NTFS or ReFS, even if it is absent."""
    kernel32, _ = _win32()
    candidate = _absolute(path)
    probe = candidate
    root_buffer = ctypes.create_unicode_buffer(32768)

    while True:
        if kernel32.GetVolumePathNameW(
            str(probe), root_buffer, len(root_buffer)
        ):
            break
        error = int(_get_last_error())
        parent = probe.parent
        if (
            error not in (_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND)
            or parent == probe
        ):
            raise PrivateAclError(
                f"cannot resolve the volume for {candidate}: "
                f"{_format_error(error)} ({error})"
            )
        probe = parent

    filesystem = ctypes.create_unicode_buffer(64)
    if not kernel32.GetVolumeInformationW(
        root_buffer.value,
        None,
        0,
        None,
        None,
        None,
        filesystem,
        len(filesystem),
    ):
        raise _last_error("GetVolumeInformationW", candidate)
    validate_acl_filesystem_name(filesystem.value)


def _open_existing_handle(path: Path):
    kernel32, _ = _win32()
    handle = kernel32.CreateFileW(
        str(path),
        _READ_CONTROL | _WRITE_DAC | _FILE_READ_ATTRIBUTES,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_OPEN_REPARSE_POINT | _FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle == invalid:
        raise _last_error("CreateFileW", path)
    return handle


def _lexical_components(path: Path) -> list[Path]:
    """Return absolute ``path`` from its root to its leaf, without resolving it.

    ``Path.resolve`` must not be used here: resolving would traverse precisely
    the junctions and symbolic links that private-state handling must reject.
    ``_absolute`` has already made the spelling absolute and normalized ``.`` /
    ``..`` without dereferencing filesystem objects.
    """
    if not path.is_absolute() or not path.anchor:
        raise PrivateAclError(f"private-state path is not absolute: {path}")

    parts = path.parts
    # Preserve the pathlib flavour.  Runtime calls use ``WindowsPath``; doing
    # this instead of constructing a generic ``Path`` also keeps UNC/drive-root
    # behavior testable on non-Windows CI hosts with ``PureWindowsPath``.
    current = type(path)(path.anchor)
    components = [current]
    # On Windows and POSIX the anchor is the first part.  Keeping the fallback
    # makes the helper fail safely for any unusual pathlib flavour.
    start = 1 if parts and parts[0] == path.anchor else 0
    for part in parts[start:]:
        current = current / part
        components.append(current)
    return components


def _open_component_handle(kernel32, path: Path):
    """Open one lexical component itself and prevent replacement while held.

    ``FILE_FLAG_OPEN_REPARSE_POINT`` makes the final component no-follow.  The
    caller opens components root-to-leaf and retains every handle without
    ``FILE_SHARE_DELETE``; therefore a validated ancestor cannot be replaced
    with a junction while a descendant is inspected or changed.

    ``None`` means this component (and necessarily its suffix) is absent.
    Every other open failure is security-significant and fails closed.
    """
    _set_last_error(0)
    handle = kernel32.CreateFileW(
        str(path),
        _FILE_READ_ATTRIBUTES,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_OPEN_REPARSE_POINT | _FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle != invalid:
        return handle

    error = int(_get_last_error())
    if error in (_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND):
        return None
    raise PrivateAclError(
        f"CreateFileW(component) failed for {path}: "
        f"{_format_error(error)} ({error})"
    )


def _hold_component(kernel32, path: Path, *, directory: bool):
    """Open, no-follow inspect, and retain a component handle."""
    handle = _open_component_handle(kernel32, path)
    if handle is None:
        return None
    try:
        info = _attribute_info(kernel32, handle, path)
        # Keep this explicit even though ``_attribute_info`` also enforces it:
        # component traversal is a separate security boundary from leaf ACL
        # hardening and should remain fail-closed if that helper is refactored.
        if info.FileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise PrivateAclError(f"refusing private-state reparse point: {path}")
        if directory and not info.FileAttributes & _FILE_ATTRIBUTE_DIRECTORY:
            raise PrivateAclError(
                f"private-state path component is not a directory: {path}"
            )
        return handle
    except BaseException:
        kernel32.CloseHandle(handle)
        raise


@contextmanager
def _validated_path_components(
    kernel32, target: Path
) -> Iterator[tuple[list[Path], list[object]]]:
    """Reject every existing reparse component and hold ancestors stable.

    The yielded first list is the missing suffix in creation order.  The
    second list is deliberately mutable so a secure directory creator can add
    handles for newly created components and keep them stable until the whole
    operation completes.
    """
    components = _lexical_components(target)
    held: list[object] = []
    missing: list[Path] = []
    try:
        for index, component in enumerate(components):
            handle = _hold_component(
                kernel32,
                component,
                directory=index < len(components) - 1,
            )
            if handle is None:
                missing = components[index:]
                break
            held.append(handle)
        yield missing, held
    finally:
        for handle in reversed(held):
            kernel32.CloseHandle(handle)


def _attribute_info(kernel32, handle, path: Path) -> _FILE_ATTRIBUTE_TAG_INFO:
    info = _FILE_ATTRIBUTE_TAG_INFO()
    if not kernel32.GetFileInformationByHandleEx(
        handle,
        _FILE_ATTRIBUTE_TAG_INFO_CLASS,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        raise _last_error("GetFileInformationByHandleEx", path)
    if info.FileAttributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        raise PrivateAclError(f"refusing private-state reparse point: {path}")
    return info


def _owner_sid(kernel32, advapi32, handle, path: Path) -> str:
    owner = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    status = advapi32.GetSecurityInfo(
        handle,
        _SE_FILE_OBJECT,
        _OWNER_SECURITY_INFORMATION,
        ctypes.byref(owner),
        None,
        None,
        None,
        ctypes.byref(descriptor),
    )
    if status:
        raise _status_error("GetSecurityInfo(owner)", status, path)
    try:
        if not owner.value:
            raise PrivateAclError(f"private-state object has no owner: {path}")
        return _sid_string(kernel32, advapi32, owner.value)
    finally:
        if descriptor.value:
            kernel32.LocalFree(descriptor)


def _private_descriptor(kernel32, advapi32, user_sid: str, directory: bool):
    descriptor = ctypes.c_void_p()
    size = wintypes.DWORD()
    sddl = build_private_sddl(user_sid, directory=directory)
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl,
        _SDDL_REVISION_1,
        ctypes.byref(descriptor),
        ctypes.byref(size),
    ):
        raise _last_error("ConvertStringSecurityDescriptorToSecurityDescriptorW")

    present = wintypes.BOOL()
    defaulted = wintypes.BOOL()
    dacl = ctypes.c_void_p()
    if not advapi32.GetSecurityDescriptorDacl(
        descriptor,
        ctypes.byref(present),
        ctypes.byref(dacl),
        ctypes.byref(defaulted),
    ):
        kernel32.LocalFree(descriptor)
        raise _last_error("GetSecurityDescriptorDacl")
    if not present.value or not dacl.value:
        kernel32.LocalFree(descriptor)
        raise PrivateAclError("refusing to apply an absent or null DACL")
    return descriptor, dacl


def _read_acl(kernel32, advapi32, handle, path: Path):
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    status = advapi32.GetSecurityInfo(
        handle,
        _SE_FILE_OBJECT,
        _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if status:
        raise _status_error("GetSecurityInfo(verify)", status, path)
    try:
        if not owner.value or not dacl.value:
            raise PrivateAclError(f"private-state object has a null owner/DACL: {path}")
        owner_text = _sid_string(kernel32, advapi32, owner.value)
        control = wintypes.WORD()
        revision = wintypes.DWORD()
        if not advapi32.GetSecurityDescriptorControl(
            descriptor, ctypes.byref(control), ctypes.byref(revision)
        ):
            raise _last_error("GetSecurityDescriptorControl", path)
        info = _ACL_SIZE_INFORMATION()
        if not advapi32.GetAclInformation(
            dacl,
            ctypes.byref(info),
            ctypes.sizeof(info),
            _ACL_SIZE_INFORMATION_CLASS,
        ):
            raise _last_error("GetAclInformation", path)
        aces: list[tuple[int, int, int, str]] = []
        for index in range(info.AceCount):
            ace_pointer = ctypes.c_void_p()
            if not advapi32.GetAce(dacl, index, ctypes.byref(ace_pointer)):
                raise _last_error("GetAce", path)
            ace = _ACCESS_ALLOWED_ACE.from_address(ace_pointer.value)
            sid_pointer = ace_pointer.value + _ACCESS_ALLOWED_ACE.SidStart.offset
            aces.append(
                (
                    int(ace.Header.AceType),
                    int(ace.Header.AceFlags),
                    int(ace.Mask),
                    _sid_string(kernel32, advapi32, sid_pointer),
                )
            )
        return owner_text, bool(control.value & _SE_DACL_PROTECTED), aces
    finally:
        if descriptor.value:
            kernel32.LocalFree(descriptor)


def _verify_private_handle(
    kernel32, advapi32, handle, path: Path, user_sid: str, directory: bool
) -> None:
    owner, protected, aces = _read_acl(kernel32, advapi32, handle, path)
    trusted_owners = {user_sid, _SYSTEM_SID, _ADMINISTRATORS_SID}
    if owner not in trusted_owners:
        raise PrivateAclError(f"untrusted owner {owner} on private-state path: {path}")
    expected_flags = _OBJECT_INHERIT_ACE | _CONTAINER_INHERIT_ACE if directory else 0
    if (
        not protected
        or len(aces) != 3
        or any(ace_type != _ACCESS_ALLOWED_ACE_TYPE for ace_type, _, _, _ in aces)
        or any(flags != expected_flags for _, flags, _, _ in aces)
        or any(mask != _FILE_ALL_ACCESS for _, _, mask, _ in aces)
        or {sid for _, _, _, sid in aces} != trusted_owners
    ):
        raise PrivateAclError(f"private DACL verification failed for {path}")


def harden_private_path(path: Path, *, directory: bool | None = None) -> None:
    """Apply and verify the exact private DACL on an existing path.

    The opened object is inspected before its owner or DACL is trusted.  Every
    reparse point is rejected without following it.

    A path that already carries the exact private DACL is left alone: the DACL
    is read and verified first, and only a failed verification falls through to
    the set-then-verify path.  See the comment on that check for why.
    """
    kernel32, advapi32 = _win32()
    target = _absolute(path)
    with _validated_path_components(kernel32, target) as (missing, _held):
        if missing:
            raise PrivateAclError(f"private-state path does not exist: {target}")

        handle = _open_existing_handle(target)
        try:
            info = _attribute_info(kernel32, handle, target)
            actual_directory = bool(
                info.FileAttributes & _FILE_ATTRIBUTE_DIRECTORY
            )
            if directory is not None and bool(directory) != actual_directory:
                expected = "directory" if directory else "file"
                raise PrivateAclError(f"expected a private {expected}: {target}")

            user_sid = current_user_sid()
            owner = _owner_sid(kernel32, advapi32, handle, target)
            if owner not in {user_sid, _SYSTEM_SID, _ADMINISTRATORS_SID}:
                raise PrivateAclError(
                    "refusing private-state path owned by untrusted SID "
                    f"{owner}: {target}"
                )

            # VERIFY BEFORE SET. SetSecurityInfo with
            # PROTECTED_DACL_SECURITY_INFORMATION makes Windows re-propagate
            # the inheritable ACEs to every descendant, so on a directory it
            # costs time proportional to the tree beneath it: measured on the
            # rig at 2 ms empty, 279 ms at 1000 files and 7.0 s on captures/
            # (about 7400 files). A bookkeeping write every 10 s therefore
            # stalled the event loop for seconds, and got worse as thumbnails
            # accumulated through a night. A path already in the correct state
            # now costs one non-recursive DACL read instead.
            #
            # RULING: the unconditional set also re-propagated ACEs onto
            # pre-existing children as a side effect. That is not what this
            # function documents and is not a designed repair mechanism.
            # Children created under this directory inherit correctly from the
            # parent's inheritable ACEs at creation time, so steady-state
            # security is unchanged. The deliberate descendant sweep already
            # exists and is named: persist.secure_private_tree, which hardens a
            # root and every existing descendant and runs once at startup as
            # the migration for state written by older releases. That is where
            # periodic child repair belongs if it is ever wanted. A status poll
            # is not.
            #
            # The postcondition is unchanged: on a normal return the path has
            # been verified to carry the exact private DACL.
            try:
                _verify_private_handle(
                    kernel32,
                    advapi32,
                    handle,
                    target,
                    user_sid,
                    actual_directory,
                )
            except PrivateAclError:
                pass                    # wrong or absent: fall through and set
            else:
                return

            descriptor, dacl = _private_descriptor(
                kernel32, advapi32, user_sid, actual_directory
            )
            try:
                status = advapi32.SetSecurityInfo(
                    handle,
                    _SE_FILE_OBJECT,
                    _DACL_SECURITY_INFORMATION
                    | _PROTECTED_DACL_SECURITY_INFORMATION,
                    None,
                    None,
                    dacl,
                    None,
                )
                if status:
                    raise _status_error("SetSecurityInfo", status, target)
            finally:
                kernel32.LocalFree(descriptor)

            _verify_private_handle(
                kernel32,
                advapi32,
                handle,
                target,
                user_sid,
                actual_directory,
            )
        finally:
            kernel32.CloseHandle(handle)


def _create_private_directory(kernel32, advapi32, path: Path, user_sid: str) -> None:
    descriptor, _ = _private_descriptor(kernel32, advapi32, user_sid, True)
    attributes = _SECURITY_ATTRIBUTES(
        ctypes.sizeof(_SECURITY_ATTRIBUTES), descriptor, False
    )
    try:
        if not kernel32.CreateDirectoryW(str(path), ctypes.byref(attributes)):
            error = int(_get_last_error())
            if error != _ERROR_ALREADY_EXISTS:
                raise PrivateAclError(
                    f"CreateDirectoryW failed for {path}: "
                    f"{_format_error(error)} ({error})"
                )
    finally:
        kernel32.LocalFree(descriptor)


def ensure_private_directory(path: Path) -> None:
    """Create a Windows directory securely and apply/verify its private DACL."""
    kernel32, advapi32 = _win32()
    target = _absolute(path)
    require_acl_capable_filesystem(target)
    with _validated_path_components(kernel32, target) as (missing, held):
        user_sid = current_user_sid()
        for directory_path in missing:
            _create_private_directory(
                kernel32, advapi32, directory_path, user_sid
            )
            # Verify the object created under the trusted parent is a real
            # directory, then retain a no-delete-share handle before creating
            # the next suffix component.
            handle = _hold_component(
                kernel32, directory_path, directory=True
            )
            if handle is None:  # pragma: no cover - impossible without a race
                raise PrivateAclError(
                    f"created private directory disappeared: {directory_path}"
                )
            held.append(handle)
            harden_private_path(directory_path, directory=True)

        # Existing directories still need migration/repair.  Newly created
        # directories are deliberately verified a second time at the leaf.
        harden_private_path(target, directory=True)


__all__ = [
    "PrivateAclError",
    "build_private_sddl",
    "current_user_sid",
    "ensure_private_directory",
    "harden_private_path",
    "require_acl_capable_filesystem",
    "validate_acl_filesystem_name",
]
