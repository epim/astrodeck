"""Target-state contract for fail-closed private DACL enforcement on Windows."""

from __future__ import annotations

import ast
import ctypes
import inspect
import json
import os
import re
import subprocess
import textwrap
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

import pytest


CURRENT_SID_SAMPLE = "S-1-5-21-111-222-333-1001"
SYSTEM_SID = "S-1-5-18"
ADMINISTRATORS_SID = "S-1-5-32-544"
BROAD_SIDS = {
    "S-1-1-0",       # Everyone
    "S-1-5-11",      # Authenticated Users
    "S-1-5-32-545",  # BUILTIN\\Users
    "S-1-5-32-546",  # BUILTIN\\Guests
}
PACKAGED_ENTRY = Path(__file__).resolve().parents[1] / "packaging" / "entry.py"


def _windows_acl_module():
    try:
        from astrodeck import windows_acl
    except ImportError as exc:
        pytest.fail(
            "missing server/astrodeck/windows_acl.py; implement the Windows "
            f"DACL design first ({exc})"
        )
    return windows_acl


def _require_windows_live() -> None:
    if os.name != "nt":
        pytest.fail("this mandatory live DACL gate must run on Windows NTFS/ReFS")


def _make_directory_reparse(link: Path, target: Path) -> None:
    """Create a directory symlink, falling back to an unprivileged junction."""
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError as symlink_error:
        completed = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode:
            detail = (completed.stderr or completed.stdout).strip()
            pytest.skip(
                "directory reparse creation is unavailable: "
                f"symlink={symlink_error}; junction={detail}"
            )


def _aces(sddl: str) -> list[tuple[str, str, str, str, str, str]]:
    return [tuple(match) for match in re.findall(r"\(([^;]*);([^;]*);([^;]*);([^;]*);([^;]*);([^)]*)\)", sddl)]


def test_sddl_is_protected_and_has_only_three_full_control_trustees():
    acl = _windows_acl_module()
    build = getattr(acl, "build_private_sddl", None)
    assert callable(build), "windows_acl.build_private_sddl() is part of the contract"

    file_sddl = build(CURRENT_SID_SAMPLE, directory=False)
    directory_sddl = build(CURRENT_SID_SAMPLE, directory=True)

    assert file_sddl.startswith("D:P")
    assert directory_sddl.startswith("D:P")

    for sddl, directory in ((file_sddl, False), (directory_sddl, True)):
        aces = _aces(sddl)
        assert len(aces) == 3
        assert {ace[5] for ace in aces} == {
            CURRENT_SID_SAMPLE,
            "SY",
            "BA",
        }
        assert all(ace[0] == "A" and ace[2] == "FA" for ace in aces)
        assert not ({ace[5] for ace in aces} & {"WD", "AU", "BU", "BG"})
        if directory:
            assert all("OI" in ace[1] and "CI" in ace[1] for ace in aces)
        else:
            assert all(ace[1] == "" for ace in aces)


def test_windows_private_state_filesystem_allowlist_fails_closed():
    acl = _windows_acl_module()
    validate = getattr(acl, "validate_acl_filesystem_name", None)
    assert callable(validate)
    assert validate("NTFS") is None
    assert validate("ntfs") is None
    assert validate("ReFS") is None
    error_type = getattr(acl, "PrivateAclError", RuntimeError)
    for unsupported in ("", "FAT", "FAT32", "exFAT", "CDFS", "unknown"):
        with pytest.raises(error_type):
            validate(unsupported)


def test_acl_failure_is_not_swallowed_as_missing_or_corrupt_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    from astrodeck import persist

    path = tmp_path / "secret.json"
    path.write_text('{"secret": true}', encoding="utf-8")

    class DeliberateAclFailure(RuntimeError):
        pass

    def fail(_path: Path) -> None:
        raise DeliberateAclFailure("cannot apply protected DACL")

    monkeypatch.setattr(persist, "harden_private_file", fail)
    with pytest.raises(DeliberateAclFailure):
        persist.read_json_or(path, default={})


def test_persistence_exposes_a_strict_private_directory_seam():
    from astrodeck import persist

    helper = getattr(persist, "ensure_private_dir", None)
    assert callable(helper), (
        "sensitive state must harden its directory before creating temp, backup, "
        "or final files; generic ensure_dir() is not that contract"
    )
    assert callable(getattr(persist, "secure_private_tree", None))


def test_private_tree_guard_runs_before_every_startup_or_admin_read():
    from astrodeck import __main__ as cli
    from astrodeck.api import app as app_module

    def call_line(source: str, name: str) -> int:
        tree = ast.parse(textwrap.dedent(source))
        lines = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = ""
            if isinstance(node.func, ast.Name):
                called = node.func.id
            elif isinstance(node.func, ast.Attribute):
                called = node.func.attr
            if called == name:
                lines.append(node.lineno)
        assert lines, f"missing executable {name}() call"
        return min(lines)

    run_source = inspect.getsource(cli._cmd_run)
    assert call_line(run_source, "secure_private_tree") < call_line(
        run_source, "_security_banner"
    )

    admin_source = inspect.getsource(cli._cmd_create_admin)
    assert call_line(admin_source, "secure_private_tree") < call_line(
        admin_source, "create_admin"
    )

    lifespan_source = inspect.getsource(app_module._lifespan)
    lifespan_tree = ast.parse(textwrap.dedent(lifespan_source))
    first_try = min(node.lineno for node in ast.walk(lifespan_tree) if isinstance(node, ast.Try))
    assert call_line(lifespan_source, "secure_private_tree") < first_try

    packaged_source = PACKAGED_ENTRY.read_text(encoding="utf-8")
    packaged_tree = ast.parse(packaged_source)
    first_directory_loop = min(
        node.lineno for node in ast.walk(packaged_tree) if isinstance(node, ast.For)
    )
    assert call_line(packaged_source, "secure_private_tree") < first_directory_loop


# ---------------- independent Win32 verifier (does not trust implementation)

SE_FILE_OBJECT = 1
OWNER_SECURITY_INFORMATION = 0x00000001
DACL_SECURITY_INFORMATION = 0x00000004
SE_DACL_PROTECTED = 0x1000
ACL_SIZE_INFORMATION_CLASS = 2
ACCESS_ALLOWED_ACE_TYPE = 0x00
ACCESS_DENIED_ACE_TYPE = 0x01
INHERITED_ACE = 0x10
OBJECT_INHERIT_ACE = 0x01
CONTAINER_INHERIT_ACE = 0x02
FILE_ALL_ACCESS = 0x001F01FF
TOKEN_QUERY = 0x0008
TOKEN_USER_CLASS = 1
ERROR_INSUFFICIENT_BUFFER = 122
UNPROTECTED_DACL_SECURITY_INFORMATION = 0x20000000
SDDL_REVISION_1 = 1


class ACL_SIZE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("AceCount", wintypes.DWORD),
        ("AclBytesInUse", wintypes.DWORD),
        ("AclBytesFree", wintypes.DWORD),
    ]


class ACE_HEADER(ctypes.Structure):
    _fields_ = [
        ("AceType", ctypes.c_ubyte),
        ("AceFlags", ctypes.c_ubyte),
        ("AceSize", wintypes.WORD),
    ]


class ACCESS_ACE(ctypes.Structure):
    _fields_ = [
        ("Header", ACE_HEADER),
        ("Mask", wintypes.DWORD),
        ("SidStart", wintypes.DWORD),
    ]


class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class TOKEN_USER(ctypes.Structure):
    _fields_ = [("User", SID_AND_ATTRIBUTES)]


@dataclass(frozen=True)
class _Ace:
    kind: str
    flags: int
    mask: int
    sid: str


@dataclass(frozen=True)
class _Acl:
    owner_sid: str
    protected: bool
    aces: tuple[_Ace, ...]


def _sid_string(advapi32, kernel32, sid_pointer: int) -> str:
    out = wintypes.LPWSTR()
    if not advapi32.ConvertSidToStringSidW(
        ctypes.c_void_p(sid_pointer), ctypes.byref(out)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return out.value
    finally:
        kernel32.LocalFree(ctypes.cast(out, ctypes.c_void_p))


def _read_acl(path: Path) -> _Acl:
    if os.name != "nt":
        raise RuntimeError("Win32 verifier called off Windows")

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()

    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.GetSecurityDescriptorControl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetSecurityDescriptorControl.restype = wintypes.BOOL
    advapi32.GetAclInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    advapi32.GetAclInformation.restype = wintypes.BOOL
    advapi32.GetAce.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetAce.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    status = advapi32.GetNamedSecurityInfoW(
        str(path),
        SE_FILE_OBJECT,
        OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if status:
        raise ctypes.WinError(status)

    try:
        control = wintypes.WORD()
        revision = wintypes.DWORD()
        if not advapi32.GetSecurityDescriptorControl(
            descriptor, ctypes.byref(control), ctypes.byref(revision)
        ):
            raise ctypes.WinError(ctypes.get_last_error())

        info = ACL_SIZE_INFORMATION()
        if not advapi32.GetAclInformation(
            dacl,
            ctypes.byref(info),
            ctypes.sizeof(info),
            ACL_SIZE_INFORMATION_CLASS,
        ):
            raise ctypes.WinError(ctypes.get_last_error())

        aces: list[_Ace] = []
        for index in range(info.AceCount):
            ace_pointer = ctypes.c_void_p()
            if not advapi32.GetAce(dacl, index, ctypes.byref(ace_pointer)):
                raise ctypes.WinError(ctypes.get_last_error())
            raw = ACCESS_ACE.from_address(ace_pointer.value)
            kind = {
                ACCESS_ALLOWED_ACE_TYPE: "allow",
                ACCESS_DENIED_ACE_TYPE: "deny",
            }.get(raw.Header.AceType, f"other:{raw.Header.AceType}")
            sid_pointer = ace_pointer.value + ACCESS_ACE.SidStart.offset
            aces.append(
                _Ace(
                    kind=kind,
                    flags=int(raw.Header.AceFlags),
                    mask=int(raw.Mask),
                    sid=_sid_string(advapi32, kernel32, sid_pointer),
                )
            )
        return _Acl(
            owner_sid=_sid_string(advapi32, kernel32, owner.value),
            protected=bool(control.value & SE_DACL_PROTECTED),
            aces=tuple(aces),
        )
    finally:
        kernel32.LocalFree(descriptor)


def _current_process_sid() -> str:
    if os.name != "nt":
        raise RuntimeError("Win32 token verifier called off Windows")

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
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
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        needed = wintypes.DWORD()
        advapi32.GetTokenInformation(
            token, TOKEN_USER_CLASS, None, 0, ctypes.byref(needed)
        )
        error = ctypes.get_last_error()
        if error != ERROR_INSUFFICIENT_BUFFER or not needed.value:
            raise ctypes.WinError(error)
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi32.GetTokenInformation(
            token,
            TOKEN_USER_CLASS,
            buffer,
            needed.value,
            ctypes.byref(needed),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        token_user = ctypes.cast(buffer, ctypes.POINTER(TOKEN_USER)).contents
        return _sid_string(
            advapi32, kernel32, ctypes.cast(token_user.User.Sid, ctypes.c_void_p).value
        )
    finally:
        kernel32.CloseHandle(token)


def _set_test_sddl(path: Path, sddl: str, *, protected: bool) -> None:
    """Install a deliberately broad test DACL without using production code."""
    if os.name != "nt":
        raise RuntimeError("Win32 test DACL writer called off Windows")

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    descriptor = ctypes.c_void_p()
    descriptor_size = wintypes.DWORD()
    dacl = ctypes.c_void_p()
    present = wintypes.BOOL()
    defaulted = wintypes.BOOL()

    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi32.GetSecurityDescriptorDacl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.BOOL),
    ]
    advapi32.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    advapi32.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl,
        SDDL_REVISION_1,
        ctypes.byref(descriptor),
        ctypes.byref(descriptor_size),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not advapi32.GetSecurityDescriptorDacl(
            descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        assert present.value and dacl.value
        flags = DACL_SECURITY_INFORMATION
        flags |= (
            0x80000000
            if protected
            else UNPROTECTED_DACL_SECURITY_INFORMATION
        )
        status = advapi32.SetNamedSecurityInfoW(
            str(path), SE_FILE_OBJECT, flags, None, None, dacl, None
        )
        if status:
            raise ctypes.WinError(status)
    finally:
        kernel32.LocalFree(descriptor)


def _assert_private_acl(
    snapshot: _Acl, *, directory: bool, process_sid: str
) -> None:
    assert snapshot.protected, "DACL inheritance remains enabled"
    assert snapshot.owner_sid in {
        process_sid,
        SYSTEM_SID,
        ADMINISTRATORS_SID,
    }
    assert snapshot.aces
    assert all(not (ace.flags & INHERITED_ACE) for ace in snapshot.aces)
    assert all(ace.kind == "allow" for ace in snapshot.aces)
    assert {ace.sid for ace in snapshot.aces} == {
        process_sid,
        SYSTEM_SID,
        ADMINISTRATORS_SID,
    }
    assert not ({ace.sid for ace in snapshot.aces} & BROAD_SIDS)
    assert all(ace.mask == FILE_ALL_ACCESS for ace in snapshot.aces)
    if directory:
        assert all(
            ace.flags == (OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE)
            for ace in snapshot.aces
        )
    else:
        assert all(ace.flags == 0 for ace in snapshot.aces)


@pytest.mark.parametrize(
    ("writer_name", "payload", "kwargs"),
    [
        pytest.param("write_json_atomic", {"secret": True}, {"backup": False}, id="json"),
        pytest.param("write_private_text_atomic", "secret", {}, id="text"),
    ],
)
def test_parent_acl_failure_prevents_staging_file_creation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    writer_name: str,
    payload,
    kwargs: dict,
):
    from astrodeck import persist

    class DeliberateAclFailure(RuntimeError):
        pass

    created = False

    def fail_parent(_path: Path) -> None:
        raise DeliberateAclFailure("parent DACL failed")

    def forbidden_mkstemp(*_args, **_kwargs):
        nonlocal created
        created = True
        raise AssertionError("temp file created before parent was private")

    monkeypatch.setattr(persist, "ensure_private_dir", fail_parent)
    monkeypatch.setattr(persist.tempfile, "mkstemp", forbidden_mkstemp)
    with pytest.raises(DeliberateAclFailure):
        getattr(persist, writer_name)(tmp_path / "secret.json", payload, **kwargs)
    assert created is False


@pytest.mark.parametrize(
    ("writer_name", "payload", "kwargs"),
    [
        pytest.param("write_json_atomic", {"secret": True}, {"backup": False}, id="json"),
        pytest.param("write_private_text_atomic", "secret", {}, id="text"),
    ],
)
def test_staging_file_is_hardened_before_atomic_replace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    writer_name: str,
    payload,
    kwargs: dict,
):
    from astrodeck import persist

    hardened: list[Path] = []

    monkeypatch.setattr(persist, "ensure_private_dir", lambda _path: None)
    monkeypatch.setattr(
        persist, "harden_private_file", lambda path: hardened.append(Path(path))
    )

    def verify_replace(src: Path, _dst: Path) -> None:
        assert Path(src) in hardened, "staging file was replaced before its DACL"

    monkeypatch.setattr(persist, "_replace_with_retry", verify_replace)
    getattr(persist, writer_name)(tmp_path / "secret.json", payload, **kwargs)


def test_backup_acl_failure_is_propagated_and_broad_backup_is_removed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    from astrodeck import persist

    target = tmp_path / "secret.json"
    persist.write_json_atomic(target, {"version": 1}, backup=False)

    class DeliberateAclFailure(RuntimeError):
        pass

    original_harden = persist.harden_private_file

    def fail_backup(path: Path) -> None:
        if str(path).endswith(".bak"):
            raise DeliberateAclFailure("backup DACL failed")
        original_harden(path)

    monkeypatch.setattr(persist, "harden_private_file", fail_backup)
    with pytest.raises(DeliberateAclFailure):
        persist.write_json_atomic(target, {"version": 2})
    assert not target.with_suffix(".json.bak").exists()
    assert json.loads(target.read_text(encoding="utf-8")) == {"version": 1}


def test_session_secret_acl_failure_is_not_converted_to_the_public_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    from astrodeck import persist
    from astrodeck.auth import session

    secret = tmp_path / session.SECRET_FILE_NAME
    secret.write_text("private-test-secret", encoding="utf-8")
    monkeypatch.setattr(session, "_secret_dir", lambda: tmp_path)
    monkeypatch.delenv(session.SECRET_ENV_VAR, raising=False)

    class DeliberateAclFailure(RuntimeError):
        pass

    monkeypatch.setattr(
        persist,
        "harden_private_file",
        lambda _path: (_ for _ in ()).throw(DeliberateAclFailure("DACL failed")),
    )
    with pytest.raises(DeliberateAclFailure):
        session.session_secret()


@pytest.mark.security_live
def test_live_windows_directory_primary_and_backup_have_exact_private_dacls(
    tmp_path: Path,
):
    _require_windows_live()
    from astrodeck import persist
    acl = _windows_acl_module()

    private_dir = tmp_path / "config"
    require_filesystem = getattr(acl, "require_acl_capable_filesystem", None)
    assert callable(require_filesystem)
    assert require_filesystem(private_dir) is None
    persist.ensure_private_dir(private_dir)
    process_sid = _current_process_sid()
    assert acl.current_user_sid() == process_sid
    _assert_private_acl(_read_acl(private_dir), directory=True, process_sid=process_sid)

    target = private_dir / "配置-astrodeck.json"
    persist.write_json_atomic(target, {"secret": "first"})
    persist.write_json_atomic(target, {"secret": "second"})
    session_secret = private_dir / "session_secret"
    persist.write_private_text_atomic(session_secret, "acceptance-secret")

    _assert_private_acl(_read_acl(target), directory=False, process_sid=process_sid)
    _assert_private_acl(
        _read_acl(target.with_suffix(".json.bak")),
        directory=False,
        process_sid=process_sid,
    )
    _assert_private_acl(
        _read_acl(session_secret), directory=False, process_sid=process_sid
    )


@pytest.mark.security_live
def test_live_windows_read_repairs_an_inherited_everyone_dacl(tmp_path: Path):
    _require_windows_live()
    from astrodeck import persist

    target = tmp_path / "legacy.json"
    target.write_text('{"secret": true}', encoding="utf-8")
    _set_test_sddl(target, "D:(A;;FA;;;WD)", protected=False)
    before = _read_acl(target)
    assert before.protected is False
    assert any(ace.sid == "S-1-1-0" for ace in before.aces)

    assert persist.read_json(target) == {"secret": True}
    _assert_private_acl(
        _read_acl(target),
        directory=False,
        process_sid=_current_process_sid(),
    )


@pytest.mark.security_live
def test_live_windows_reparse_point_is_rejected_without_touching_target(
    tmp_path: Path,
):
    _require_windows_live()
    acl = _windows_acl_module()
    error_type = getattr(acl, "PrivateAclError", RuntimeError)
    harden = getattr(acl, "harden_private_path", None)
    assert callable(harden)

    victim = tmp_path / "victim.txt"
    victim.write_text("unchanged", encoding="utf-8")
    victim_acl = _read_acl(victim)
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(victim)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable for this Windows runner: {exc}")

    with pytest.raises(error_type):
        harden(link, directory=False)
    assert victim.read_text(encoding="utf-8") == "unchanged"
    assert _read_acl(victim) == victim_acl


@pytest.mark.security_live
def test_live_windows_intermediate_reparse_point_cannot_redirect_private_io(
    tmp_path: Path,
):
    """A normal leaf below a directory link must not bypass the no-follow guard."""
    _require_windows_live()
    from astrodeck import persist

    acl = _windows_acl_module()
    error_type = getattr(acl, "PrivateAclError", RuntimeError)

    victim_dir = tmp_path / "victim"
    nested = victim_dir / "nested"
    nested.mkdir(parents=True)
    victim = nested / "existing.json"
    victim.write_text('{"value": "unchanged"}', encoding="utf-8")
    victim_acl = _read_acl(victim)

    redirect = tmp_path / "redirect"
    _make_directory_reparse(redirect, victim_dir)

    redirected_victim = redirect / "nested" / victim.name
    with pytest.raises(error_type, match="reparse point"):
        acl.harden_private_path(redirected_victim, directory=False)

    # The persistence path first secures its parent.  That parent is a normal
    # directory reached through the intermediate link, so this independently
    # proves the entire prefix (rather than only the final object) is checked.
    redirected_new = redirect / "nested" / "new-secret.json"
    with pytest.raises(error_type, match="reparse point"):
        persist.write_json_atomic(
            redirected_new, {"secret": True}, backup=False
        )

    assert victim.read_text(encoding="utf-8") == '{"value": "unchanged"}'
    assert _read_acl(victim) == victim_acl
    assert not (nested / "new-secret.json").exists()
