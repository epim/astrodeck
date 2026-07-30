"""Where a vendor SDK library lives, on whatever platform we are running.

Every native camera/accessory binding used to hard-code a Windows DLL basename
and look for it in one flat vendored directory. That was true of how the drivers
were developed and false of where they run: on Linux and macOS the file is not
called ``ASICamera2.dll``, and the failure is SILENT — the loader finds nothing,
the backend registers nothing, and the device simply is not offered, with no log
line saying why. A Raspberry Pi build would therefore run the mount and the
filter wheel and quietly have no cameras at all.

Layout::

    vendor/<vendor>/                        LICENSE, README
    vendor/<vendor>/<basename>.dll          the historical flat Windows files
    vendor/<vendor>/linux-arm64/lib*.so*    per-platform libraries
    vendor/<vendor>/linux-x86_64/lib*.so*
    vendor/<vendor>/macos/lib*.dylib

The flat Windows files stay exactly where they were. A deployed Windows rig
resolves them by the same path it always did, so this is additive: nothing that
works today can start failing because of it.
"""
from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

VENDOR_ROOT = Path(__file__).resolve().parent.parent / "vendor"


def platform_tag() -> str:
    """The vendored-library subdirectory for this machine.

    Architecture matters as much as OS: a Raspberry Pi and an Intel NUC both run
    Linux and cannot share a binary. ``aarch64`` and ``arm64`` are the same
    thing under two names (Linux and macOS report it differently), so both map
    to one tag.
    """
    machine = platform.machine().lower()
    if sys.platform == "darwin":
        # Universal/fat dylibs are the norm on macOS, so one directory serves
        # both Apple silicon and Intel.
        return "macos"
    if sys.platform.startswith("linux"):
        if machine in ("aarch64", "arm64"):
            return "linux-arm64"
        if machine in ("armv7l", "armv6l", "armhf"):
            return "linux-arm32"
        if machine in ("x86_64", "amd64"):
            return "linux-x86_64"
        return f"linux-{machine}"
    if sys.platform == "win32":
        return "win-x64" if machine in ("amd64", "x86_64", "arm64") else "win-x86"
    return sys.platform


def library_names(stem: str) -> list[str]:
    """Candidate filenames for a library, most specific first.

    ``stem`` is the SDK's base name without any prefix, decoration or extension
    — "ASICamera2", "PlayerOneCamera".

    The versioned Linux name comes first because that is the real file: vendors
    ship ``libFoo.so.3.10.0`` with ``libFoo.so`` as a symlink beside it, and a
    symlink does not survive a git checkout on Windows or a wheel build. So the
    versioned file is what gets vendored, and the unversioned name is kept as a
    fallback for a system install where the symlink does exist.
    """
    if sys.platform == "win32":
        return [f"{stem}.dll"]
    if sys.platform == "darwin":
        return [f"lib{stem}.dylib", f"{stem}.dylib"]
    return [f"lib{stem}.so", f"{stem}.so"]


def candidates(vendor: str, stem: str, *, env_var: str | None = None,
               extra: list[str] | None = None) -> list[Path]:
    """Ordered paths to try for one SDK library. Existence is NOT checked here —
    the caller must also confirm the file loads and exports what it needs, since
    a file of the right name can still be the wrong build.

    Order:
      1. an explicit ``env_var`` directory — the escape hatch for a user who has
         a newer SDK than the one we vendored;
      2. the per-platform vendored directory;
      3. the flat vendored directory (the historical Windows layout);
      4. ``extra`` — known vendor install locations.
    """
    out: list[Path] = []
    names = library_names(stem)
    env = os.environ.get(env_var) if env_var else None
    if env:
        base = Path(env)
        out.extend(base / n for n in names)
        # A user pointing at an unpacked vendor SDK may well point at its root.
        out.extend(base / platform_tag() / n for n in names)

    vdir = VENDOR_ROOT / vendor
    plat = vdir / platform_tag()
    if plat.is_dir():
        # Versioned Linux libraries sort ambiguously by name (`.so.3.10` vs
        # `.so.3.9`), so take whatever real files are there, longest name first:
        # the fully-versioned file is the actual library, the shorter names are
        # symlinks to it when they exist at all.
        matched = sorted(
            (p for p in plat.iterdir()
             if p.is_file() and stem.lower() in p.name.lower()),
            key=lambda p: len(p.name), reverse=True)
        out.extend(matched)
    out.extend(vdir / n for n in names)
    out.extend(Path(p) for p in (extra or []))
    return out
