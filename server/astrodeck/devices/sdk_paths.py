# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
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
import threading
from pathlib import Path

VENDOR_ROOT = Path(__file__).resolve().parent.parent / "vendor"

#: The directory an operator points at a Player One SDK they fetched themselves.
#: Named ONCE, here, because it has two readers that must agree: the loader
#: (``cameras/player_one_sdk.py``) that opens the library, and the Credits
#: screen's "is the fetch already done?" probe (``licensing.py``). They used to
#: spell it differently (``ASTRODECK_PLAYERONE_SDK_DIR`` and
#: ``PLAYERONE_SDK_DIR``), so an operator who did what the loader documents got
#: a working camera and a screen saying the SDK was missing (#632).
PLAYERONE_SDK_ENV = "ASTRODECK_PLAYERONE_SDK_DIR"


#: ``(the platform.machine callable the answer came from, the answer)``, or
#: None before the first read. See `_machine`.
_machine_cache: tuple[object, str] | None = None
_MACHINE_LOCK = threading.Lock()


def _machine() -> str:
    """The machine type, lower-cased, without asking WMI on Windows and asking
    ``platform.machine()`` at most once per process everywhere else (#699).

    WINDOWS READS THE ENVIRONMENT. ``platform.machine()`` there costs about
    140 ms and two WMI queries on its first call, from CPython 3.12 on, and
    ``platform_tag`` is reached from ``find_astap`` on every solve resolve,
    from worker threads. Nothing serialises that first call: six racing
    threads issued twelve concurrent WMI queries, which were the "Windows
    fatal exception: code 0x8007000e" dumps in passing test runs and, once in
    a hundred under load, an ``OSError: [Errno 9]`` that failed a run whose
    tests had all passed. Measured under 40 CPU burners, eight threads racing
    that first call ended the process with a segmentation fault in 22 of 25
    runs; the same threads behind the lock below, 3 of 25; with the first
    call made on the main thread, 0 of 25. So the only safe number of WMI
    calls from a worker thread is none. ``PROCESSOR_ARCHITEW6432`` (the
    native architecture, set for a 32-bit process on 64-bit Windows) and
    ``PROCESSOR_ARCHITECTURE`` are what CPython itself falls back on when
    WMI fails (``platform._get_machine_win32``), so they name the same
    machine.

    EVERYWHERE ELSE, AND WINDOWS WITHOUT THOSE VARIABLES, takes the answer
    from ``platform.machine()`` once, holding a lock around the first call (a
    cache alone would not do: ``functools.lru_cache`` lets two threads compute
    one missing key at once).

    The cache is keyed on the callable the answer came from, not on nothing:
    the machine cannot change under a running process, but the callable can
    be replaced, and the tests that ask what the tag would be on another box
    (``test_sdk_paths.py``, ``test_astap_bundle.py``) do exactly that. A
    replaced ``platform.machine`` is a different question and gets its own
    answer, so they need no reset hook and cannot be given a stale one.
    """
    global _machine_cache
    if sys.platform == "win32":
        native = (os.environ.get("PROCESSOR_ARCHITEW6432")
                  or os.environ.get("PROCESSOR_ARCHITECTURE"))
        if native:
            return native.lower()
    probe = platform.machine
    cached = _machine_cache
    if cached is not None and cached[0] is probe:
        return cached[1]
    with _MACHINE_LOCK:
        cached = _machine_cache           # a thread that waited finds it here
        if cached is not None and cached[0] is probe:
            return cached[1]
        answer = probe().lower()
        _machine_cache = (probe, answer)
        return answer


def platform_tag() -> str:
    """The vendored-library subdirectory for this machine.

    Architecture matters as much as OS: a Raspberry Pi and an Intel NUC both run
    Linux and cannot share a binary. ``aarch64`` and ``arm64`` are the same
    thing under two names (Linux and macOS report it differently), so both map
    to one tag.
    """
    machine = _machine()
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


# A vendor does not always use the same basename on every platform, and the
# stems in this codebase were all taken from the Windows DLL. ZWO's focuser is
# `EAF_focuser.dll` on Windows and `libEAFFocuser.so` on Linux — no
# transformation of one yields the other, so the derived name would simply not
# exist and the focuser would vanish with no log line, which is the exact silent
# failure this module was written to prevent. Only names that genuinely differ
# belong here; a stem absent from the map derives normally.
_POSIX_STEM_ALIASES = {
    "EAF_focuser": "EAFFocuser",
}


def library_names(stem: str) -> list[str]:
    """Candidate filenames for a library, most specific first.

    ``stem`` is the SDK's base name without any prefix, decoration or extension
    — "ASICamera2", "PlayerOneCamera". Where a vendor's POSIX basename is not a
    transformation of its Windows one, ``_POSIX_STEM_ALIASES`` supplies the real
    name and the derived name is kept as a fallback.

    The versioned Linux name comes first because that is the real file: vendors
    ship ``libFoo.so.3.10.0`` with ``libFoo.so`` as a symlink beside it, and a
    symlink does not survive a git checkout on Windows or a wheel build. So the
    versioned file is what gets vendored, and the unversioned name is kept as a
    fallback for a system install where the symlink does exist.
    """
    if sys.platform == "win32":
        return [f"{stem}.dll"]
    alias = _POSIX_STEM_ALIASES.get(stem)
    stems = [alias, stem] if alias else [stem]
    if sys.platform == "darwin":
        return [n for s in stems for n in (f"lib{s}.dylib", f"{s}.dylib")]
    return [n for s in stems for n in (f"lib{s}.so", f"{s}.so")]


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
        # The substring sweep above keys on the RAW stem, so it cannot find a
        # library whose POSIX basename is not a transformation of its Windows
        # one -- "EAF_focuser" is not a substring of "libEAFFocuser.so". Without
        # this line the alias mechanism and per-platform vendoring were mutually
        # exclusive, and ZWO's focuser (the one library _POSIX_STEM_ALIASES
        # exists for) could never be found in vendor/zwo/linux-arm64/ at all.
        out.extend(plat / n for n in names)
    out.extend(vdir / n for n in names)
    out.extend(Path(p) for p in (extra or []))
    return out
