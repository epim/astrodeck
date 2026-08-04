"""Per-platform vendor SDK resolution.

The bug this exists to prevent is SILENT. Every native camera binding used to
look for a Windows DLL basename in one flat directory; on Linux or macOS it
found nothing, the backend registered nothing, and the device was simply not
offered — no error, no log line. A Raspberry Pi build ran the mount and the
filter wheel and had no cameras, and nothing on screen said why.

These tests fake the platform rather than skip, because the whole point is
behaviour on machines that are not the one running the suite.
"""
from __future__ import annotations

import pytest

from astrodeck.devices import sdk_paths
from astrodeck.devices.sdk_paths import candidates, library_names, platform_tag


@pytest.fixture
def fake_platform(monkeypatch):
    def _set(sys_platform: str, machine: str):
        monkeypatch.setattr(sdk_paths.sys, "platform", sys_platform)
        monkeypatch.setattr(sdk_paths.platform, "machine", lambda: machine)
    return _set


# ------------------------------------------------------------- platform_tag

@pytest.mark.parametrize("sys_platform,machine,expected", [
    ("linux", "aarch64", "linux-arm64"),      # Raspberry Pi OS 64-bit
    ("linux", "arm64", "linux-arm64"),        # same silicon, other spelling
    ("linux", "armv7l", "linux-arm32"),       # 32-bit Pi OS
    ("linux", "x86_64", "linux-x86_64"),      # NUC / mini-PC
    ("darwin", "arm64", "macos"),             # Apple silicon
    ("darwin", "x86_64", "macos"),            # Intel Mac — same fat dylib
    ("win32", "AMD64", "win-x64"),
])
def test_platform_tag(fake_platform, sys_platform, machine, expected):
    fake_platform(sys_platform, machine)
    assert platform_tag() == expected


def test_arm64_and_x86_64_never_collide(fake_platform):
    """A Pi and a NUC both report 'linux' and cannot share a binary. Conflating
    them would hand an aarch64 library to an x86 host, which fails at load with
    a message about the file format that names no device."""
    fake_platform("linux", "aarch64")
    pi = platform_tag()
    fake_platform("linux", "x86_64")
    assert pi != platform_tag()


# ------------------------------------------------------------ library_names

def test_library_names_follow_the_platform(fake_platform):
    fake_platform("win32", "AMD64")
    assert library_names("ASICamera2") == ["ASICamera2.dll"]
    fake_platform("darwin", "arm64")
    assert library_names("ASICamera2")[0] == "libASICamera2.dylib"
    fake_platform("linux", "aarch64")
    assert library_names("ASICamera2")[0] == "libASICamera2.so"


def test_the_zwo_focuser_resolves_to_its_real_posix_name(fake_platform):
    """The stems in this codebase were all taken from the Windows DLL, and ZWO's
    focuser is the one that does not survive the trip: `EAF_focuser.dll` on
    Windows, `libEAFFocuser.so` on Linux. No transformation of the first yields
    the second, so the derived name would simply not exist — and the failure is
    the silent one this module was written to prevent: no library, no backend,
    no device offered, no log line. On an arm64 appliance the mount and the
    filter wheel would work and the focuser would be missing with no stated
    cause."""
    fake_platform("linux", "aarch64")
    names = library_names("EAF_focuser")
    assert names[0] == "libEAFFocuser.so"
    # the derived name stays as a fallback for a system install that uses it
    assert "libEAF_focuser.so" in names
    fake_platform("darwin", "arm64")
    assert library_names("EAF_focuser")[0] == "libEAFFocuser.dylib"
    # Windows is untouched — a deployed rig must resolve exactly as before.
    fake_platform("win32", "AMD64")
    assert library_names("EAF_focuser") == ["EAF_focuser.dll"]


def test_stems_without_an_alias_are_unchanged(fake_platform):
    """The alias map is for names that genuinely differ, not a general hook."""
    fake_platform("linux", "aarch64")
    for stem in ("ASICamera2", "PlayerOneCamera", "CAARotator"):
        assert library_names(stem) == [f"lib{stem}.so", f"{stem}.so"]


# --------------------------------------------------------------- candidates

def test_the_vendored_linux_library_is_found_on_a_pi(fake_platform):
    """The end this whole module exists for: on aarch64 Linux, the Player One
    camera library resolves to a real vendored file."""
    fake_platform("linux", "aarch64")
    hits = [c for c in candidates("playerone", "PlayerOneCamera") if c.is_file()]
    assert hits, "no Player One library resolved for linux-arm64"
    assert hits[0].name.startswith("libPlayerOneCamera.so")
    assert "linux-arm64" in hits[0].parts


def test_the_vendored_macos_library_is_found(fake_platform):
    fake_platform("darwin", "arm64")
    for vendor, stem in (("playerone", "PlayerOneCamera"), ("zwo", "ASICamera2")):
        hits = [c for c in candidates(vendor, stem) if c.is_file()]
        assert hits, f"no {vendor} library resolved for macos"
        assert hits[0].suffix == ".dylib"


def test_windows_still_resolves_the_flat_vendored_dll(fake_platform):
    """The historical layout is untouched, so a deployed Windows rig keeps
    resolving exactly the file it always did. This change is additive or it is
    a regression."""
    fake_platform("win32", "AMD64")
    for vendor, stem in (("playerone", "PlayerOneCamera"),
                         ("zwo", "ASICamera2"),
                         ("zwo", "EAF_focuser")):
        hits = [c for c in candidates(vendor, stem) if c.is_file()]
        assert hits, f"{vendor}/{stem} no longer resolves on Windows"
        assert hits[0].name == f"{stem}.dll"


def test_a_versioned_linux_name_beats_a_bare_one(fake_platform):
    """Vendors ship libFoo.so as a SYMLINK to libFoo.so.3.10.0, and a symlink
    survives neither a Windows checkout nor a wheel build. The real, versioned
    file must therefore be preferred over a name that may not exist."""
    fake_platform("linux", "aarch64")
    hits = [c for c in candidates("playerone", "PlayerOneCamera") if c.is_file()]
    assert hits[0].name.count(".") >= 3, f"expected a versioned .so, got {hits[0].name}"


def test_env_override_is_tried_before_anything_vendored(fake_platform, monkeypatch, tmp_path):
    """The escape hatch for a user whose SDK is newer than the one we ship."""
    fake_platform("linux", "aarch64")
    monkeypatch.setenv("ASTRODECK_PLAYERONE_SDK_DIR", str(tmp_path))
    got = candidates("playerone", "PlayerOneCamera",
                     env_var="ASTRODECK_PLAYERONE_SDK_DIR")
    assert tmp_path in got[0].parents


def test_known_install_paths_come_last(fake_platform):
    """A vendored library must win over a system install, so a working rig is
    not silently re-pointed at whatever version the user happens to have."""
    fake_platform("win32", "AMD64")
    got = candidates("zwo", "ASICamera2", extra=[r"C:\Program Files\ZWO\ASICamera2.dll"])
    # Asserted on the STRING, not on Path.name. `fake_platform` can fake
    # sys.platform but it cannot change how pathlib parses separators: under
    # Linux CI these are PosixPaths, "\" is an ordinary character, and
    # Path(r"C:\Program Files\ZWO\ASICamera2.dll").name is the WHOLE string.
    # The old assertion therefore passed only on a Windows dev box and failed
    # the moment CI ran it — a platform assumption hidden inside a test ABOUT
    # platform handling.
    last = str(got[-1])
    assert last.endswith("ASICamera2.dll") and "Program Files" in last
    assert any("vendor" in str(p) for p in got[:-1])


def test_an_unknown_platform_degrades_instead_of_raising(fake_platform):
    """A future platform should offer no library, not crash the import that
    every device backend performs at startup."""
    fake_platform("freebsd13", "riscv64")
    assert platform_tag() == "freebsd13"
    candidates("playerone", "PlayerOneCamera")   # must not raise
