"""Portable regression tests for Windows private-state ACL orchestration.

The independent live Win32 assertions live in
``security_acceptance/test_windows_acl_contract.py``.  These tests exercise the
fail-closed branches on every CI host, including hosts where Win32 is absent.
"""

from __future__ import annotations

import argparse
import ctypes
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace

import pytest

from astrodeck import persist, windows_acl


USER_SID = "S-1-5-21-111-222-333-1001"


@contextmanager
def _allow_path_components(_kernel32, _target):
    """Keep focused handle/DACL tests independent of component traversal."""
    yield [], []


def test_import_and_sddl_construction_do_not_load_win32(monkeypatch):
    """Pure helpers remain usable without importing a Win32 DLL eagerly."""
    called = False

    def forbidden_windll(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("WinDLL was loaded by a pure ACL helper")

    monkeypatch.setattr(ctypes, "WinDLL", forbidden_windll, raising=False)
    assert windows_acl.build_private_sddl(USER_SID, directory=False) == (
        f"D:P(A;;FA;;;{USER_SID})(A;;FA;;;SY)(A;;FA;;;BA)"
    )
    assert windows_acl.build_private_sddl(USER_SID, directory=True) == (
        f"D:P(A;OICI;FA;;;{USER_SID})(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
    )
    assert called is False


@pytest.mark.parametrize("name", ["", "FAT", "FAT32", "exFAT", "CDFS", "ext4"])
def test_filesystem_allowlist_rejects_unknown_or_acl_losing_filesystems(name):
    with pytest.raises(windows_acl.PrivateAclError):
        windows_acl.validate_acl_filesystem_name(name)


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        (
            PureWindowsPath("C:/private/config.json"),
            ["C:/", "C:/private", "C:/private/config.json"],
        ),
        (
            PureWindowsPath("//server/share/private/config.json"),
            [
                "//server/share/",
                "//server/share/private",
                "//server/share/private/config.json",
            ],
        ),
    ],
)
def test_lexical_component_walk_preserves_drive_and_unc_roots(target, expected):
    assert [
        path.as_posix() for path in windows_acl._lexical_components(target)
    ] == expected


def test_component_handle_is_nofollow_and_denies_delete_sharing(monkeypatch):
    opened: list[tuple] = []
    kernel32 = SimpleNamespace(
        CreateFileW=lambda *args: opened.append(args) or 321
    )

    assert windows_acl._open_component_handle(
        kernel32, Path("C:/private")
    ) == 321
    assert len(opened) == 1
    args = opened[0]
    assert args[1] == windows_acl._FILE_READ_ATTRIBUTES
    assert args[2] == (
        windows_acl._FILE_SHARE_READ | windows_acl._FILE_SHARE_WRITE
    )
    assert not args[2] & windows_acl._FILE_SHARE_DELETE
    assert args[5] & windows_acl._FILE_FLAG_OPEN_REPARSE_POINT
    assert args[5] & windows_acl._FILE_FLAG_BACKUP_SEMANTICS


def test_untrusted_owner_is_rejected_before_the_dacl_is_changed(monkeypatch):
    closed: list[int] = []
    kernel32 = SimpleNamespace(CloseHandle=lambda handle: closed.append(handle))
    advapi32 = SimpleNamespace()
    handle = 123

    monkeypatch.setattr(windows_acl, "_win32", lambda: (kernel32, advapi32))
    monkeypatch.setattr(
        windows_acl, "_validated_path_components", _allow_path_components
    )
    monkeypatch.setattr(windows_acl, "_open_existing_handle", lambda _path: handle)
    monkeypatch.setattr(
        windows_acl,
        "_attribute_info",
        lambda *_args: windows_acl._FILE_ATTRIBUTE_TAG_INFO(FileAttributes=0),
    )
    monkeypatch.setattr(windows_acl, "current_user_sid", lambda: USER_SID)
    monkeypatch.setattr(
        windows_acl, "_owner_sid", lambda *_args: "S-1-5-21-999-888-777-1002"
    )

    with pytest.raises(windows_acl.PrivateAclError, match="untrusted SID"):
        windows_acl.harden_private_path(Path("private.json"), directory=False)
    assert closed == [handle]


def test_set_security_info_return_code_is_propagated_and_resources_close(
    monkeypatch,
):
    freed: list[object] = []
    closed: list[int] = []
    kernel32 = SimpleNamespace(
        LocalFree=lambda descriptor: freed.append(descriptor),
        CloseHandle=lambda handle: closed.append(handle),
    )
    advapi32 = SimpleNamespace(SetSecurityInfo=lambda *_args: 5)
    handle = 456
    descriptor = ctypes.c_void_p(1)
    dacl = ctypes.c_void_p(2)

    monkeypatch.setattr(windows_acl, "_win32", lambda: (kernel32, advapi32))
    monkeypatch.setattr(
        windows_acl, "_validated_path_components", _allow_path_components
    )
    monkeypatch.setattr(windows_acl, "_open_existing_handle", lambda _path: handle)
    monkeypatch.setattr(
        windows_acl,
        "_attribute_info",
        lambda *_args: windows_acl._FILE_ATTRIBUTE_TAG_INFO(FileAttributes=0),
    )
    monkeypatch.setattr(windows_acl, "current_user_sid", lambda: USER_SID)
    monkeypatch.setattr(windows_acl, "_owner_sid", lambda *_args: USER_SID)
    monkeypatch.setattr(
        windows_acl, "_private_descriptor", lambda *_args: (descriptor, dacl)
    )

    with pytest.raises(windows_acl.PrivateAclError, match=r"SetSecurityInfo.*\(5\)"):
        windows_acl.harden_private_path(Path("private.json"), directory=False)
    assert freed == [descriptor]
    assert closed == [handle]


def test_intermediate_reparse_point_is_rejected_before_target_open(
    monkeypatch, tmp_path
):
    """Every lexical prefix is opened no-follow, not only the final object."""
    target = windows_acl._absolute(
        tmp_path / "trusted" / "redirect" / "secret.json"
    )
    components = windows_acl._lexical_components(target)
    redirect = components[-2]
    opened: list[Path] = []
    closed: list[int] = []
    handles: dict[int, Path] = {}
    kernel32 = SimpleNamespace(CloseHandle=lambda handle: closed.append(handle))

    def open_component(_kernel32, path):
        handle = len(handles) + 100
        opened.append(path)
        handles[handle] = path
        return handle

    def attributes(_kernel32, handle, _path):
        flags = windows_acl._FILE_ATTRIBUTE_DIRECTORY
        if handles[handle] == redirect:
            flags |= windows_acl._FILE_ATTRIBUTE_REPARSE_POINT
        return windows_acl._FILE_ATTRIBUTE_TAG_INFO(FileAttributes=flags)

    monkeypatch.setattr(
        windows_acl, "_win32", lambda: (kernel32, SimpleNamespace())
    )
    monkeypatch.setattr(windows_acl, "_open_component_handle", open_component)
    monkeypatch.setattr(windows_acl, "_attribute_info", attributes)
    monkeypatch.setattr(
        windows_acl,
        "_open_existing_handle",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("target was opened through a reparse ancestor")
        ),
    )

    with pytest.raises(windows_acl.PrivateAclError, match="reparse point"):
        windows_acl.harden_private_path(target, directory=False)

    assert opened == components[:-1]
    assert closed == list(reversed(sorted(handles)))


def test_component_guard_reports_missing_suffix_and_holds_existing_prefixes(
    monkeypatch, tmp_path
):
    target = windows_acl._absolute(tmp_path / "one" / "two" / "private")
    components = windows_acl._lexical_components(target)
    first_missing = len(components) - 2
    opened: list[Path] = []
    closed: list[int] = []
    kernel32 = SimpleNamespace(CloseHandle=lambda handle: closed.append(handle))

    def open_component(_kernel32, path):
        opened.append(path)
        index = components.index(path)
        return None if index >= first_missing else index + 1

    monkeypatch.setattr(windows_acl, "_open_component_handle", open_component)
    monkeypatch.setattr(
        windows_acl,
        "_attribute_info",
        lambda *_args: windows_acl._FILE_ATTRIBUTE_TAG_INFO(
            FileAttributes=windows_acl._FILE_ATTRIBUTE_DIRECTORY
        ),
    )

    with windows_acl._validated_path_components(kernel32, target) as (
        missing,
        held,
    ):
        assert missing == components[first_missing:]
        assert held == list(range(1, first_missing + 1))
        assert closed == []

    assert opened == components[: first_missing + 1]
    assert closed == list(reversed(range(1, first_missing + 1)))


def test_volume_failure_precedes_private_tree_enumeration(monkeypatch, tmp_path):
    class DeliberateAclFailure(RuntimeError):
        pass

    scanned = False

    def fail_volume(_path):
        raise DeliberateAclFailure("filesystem cannot persist ACLs")

    def forbidden_scandir(_path):
        nonlocal scanned
        scanned = True
        raise AssertionError("tree was traversed before its volume was trusted")

    monkeypatch.setattr(windows_acl, "require_acl_capable_filesystem", fail_volume)
    monkeypatch.setattr(
        persist, "os", SimpleNamespace(name="nt", scandir=forbidden_scandir)
    )
    with pytest.raises(DeliberateAclFailure):
        persist.secure_private_tree(tmp_path)
    assert scanned is False


def test_private_tree_failure_stops_cli_before_banner_or_listener(monkeypatch):
    """A DACL failure is terminal before app construction and Uvicorn startup."""
    from astrodeck import __main__ as cli
    from astrodeck import runtime_security

    class DeliberateAclFailure(RuntimeError):
        pass

    monkeypatch.setattr(
        runtime_security, "require_unprivileged_runtime", lambda _name: None
    )
    monkeypatch.setattr(
        runtime_security, "parse_forwarded_allow_ips", lambda _value: ""
    )
    monkeypatch.setattr(
        persist,
        "secure_private_tree",
        lambda _path: (_ for _ in ()).throw(
            DeliberateAclFailure("protected DACL unavailable")
        ),
    )
    monkeypatch.setattr(
        cli,
        "_security_banner",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("security banner ran after private-tree failure")
        ),
    )
    args = argparse.Namespace(
        host="127.0.0.1", port=8800, allow_insecure_open=False
    )
    assert cli._cmd_run(args) == 2


def test_acl_error_escapes_recoverable_read_fallback(monkeypatch, tmp_path):
    target = tmp_path / "secret.json"
    target.write_text('{"secret": true}', encoding="utf-8")

    class DeliberateAclFailure(RuntimeError):
        pass

    def fail_hardening(_path):
        raise DeliberateAclFailure("protected DACL unavailable")

    monkeypatch.setattr(persist, "harden_private_file", fail_hardening)
    with pytest.raises(DeliberateAclFailure):
        persist.read_json_or(target, default={})


def test_backup_cleanup_io_error_cannot_mask_acl_failure(monkeypatch, tmp_path):
    target = tmp_path / "secret.json"
    persist.write_json_atomic(target, {"version": 1}, backup=False)
    backup = target.with_suffix(".json.bak")

    class DeliberateAclFailure(RuntimeError):
        pass

    original_harden = persist.harden_private_file

    def fail_backup(path):
        if Path(path) == backup and backup.exists():
            raise DeliberateAclFailure("backup ACL failed")
        original_harden(path)

    original_unlink = Path.unlink

    def fail_backup_unlink(path, *args, **kwargs):
        if path == backup:
            raise PermissionError("backup is locked")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(persist, "harden_private_file", fail_backup)
    monkeypatch.setattr(Path, "unlink", fail_backup_unlink)
    with pytest.raises(persist.PrivatePermissionsError) as caught:
        persist.write_json_atomic(target, {"version": 2})
    assert isinstance(caught.value.__cause__, DeliberateAclFailure)
    assert target.read_text(encoding="utf-8").find('"version": 1') >= 0
