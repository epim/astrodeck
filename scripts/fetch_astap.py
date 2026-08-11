#!/usr/bin/env python3
"""Fetch the ASTAP solver + a star database into ``server/astrodeck/vendor/astap``.

WHY A FETCH SCRIPT AND NOT COMMITTED BINARIES. The smallest usable database is
102 MB. Committing that puts a permanent 102 MB object in every clone of this
repository, forever, for a file that changes when its upstream does and that
nobody diffs. The goal was never "binaries in git" — it was "a user installs
nothing", and that is a property of the RELEASE. So the release build fetches
them, and ``build_release.py --strict`` refuses to ship a release that was asked
for ASTAP and did not get it.

LICENSING, verified 2026-07-30 — this is redistributable, with conditions:

  * ASTAP itself is **MPL-2.0** (sourceforge.net/projects/astap-program,
    github.com/han-k59/astap). File-level copyleft, and we invoke astap_cli as a
    SUBPROCESS over its CLI — we never link it — so it places no obligation on
    AstroDeck's own code. We must ship the licence text and point at the source.

  * The star databases are **Gaia-derived**: "The Gaia data are open and free to
    use, provided credit is given to 'ESA/Gaia/DPAC'." Attribution is the whole
    obligation, and it lives in THIRD-PARTY-NOTICES.md.

  * The catalogue CSVs that ship in ASTAP's own installer are **NOT** fetched.
    Steinicke's REV NGC&IC says "Any non-commercial use of my data is free! If a
    commercial use is planned, please contact me!", and HyperLEDA is "open-source
    for non-commercial purposes". We do not use those files — AstroDeck has its
    own catalogue — so bundling them would import a non-commercial restriction
    for nothing. This script fetches the SOLVER and the STAR DATABASE only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

SF = "https://sourceforge.net/projects/astap-program/files"

#: Star databases, smallest first.
#:
#: SHIPPING DEFAULT IS D05 (decided 2026-07-30). It is the practical floor for
#: a normal field, and it ships on EVERY artifact including the Pi image —
#: 102 MB on an SD card is cheaper than a user discovering at 2am that their
#: field is too narrow to solve.
#:
#: W08 is magnitude 8 only. Kept because 0.6 MB fits places 102 MB does not
#: (a CI smoke test that proves the bundle wiring without a 102 MB download),
#: NOT as a shipping option.
DATABASES = {
    "w08": (f"{SF}/star_databases/w08_star_database_mag08_astap.zip/download", 0.6),
    "d05": (f"{SF}/star_databases/d05_star_database.zip/download", 102.2),
    "g05": (f"{SF}/star_databases/g05_star_database.zip/download", 101.6),
    "d20": (f"{SF}/star_databases/d20_star_database.zip/download", 399.6),
    "d50": (f"{SF}/star_databases/d50_star_database.zip/download", 901.3),
}

#: DOWNLOAD SELECTOR key -> (download url, name of the executable inside the
#: archive). These keys are NOT the vendored-directory names — see ``_OUT_TAG``.
#: Filenames verified against the SourceForge listings 2026-07-30 — note the
#: HYPHEN in "command-line" and the space in the macOS folder name; both are
#: easy to guess wrong and both give a bare 404.
BINARIES = {
    "windows-x86_64": (
        f"{SF}/windows_installer/astap_command-line_version_win64.zip/download",
        "astap_cli.exe"),
    "windows-aarch64": (
        f"{SF}/windows_installer/astap_command-line_version_win11_aarch64.zip/download",
        "astap_cli.exe"),
    "linux-x86_64": (
        f"{SF}/linux_installer/astap_command-line_version_Linux_amd64.zip/download",
        "astap_cli"),
    "linux-aarch64": (
        f"{SF}/linux_installer/astap_command-line_version_Linux_aarch64.zip/download",
        "astap_cli"),
    "macos-x86_64": (
        f"{SF}/macOS%20installer/astap_command-line_version_macOS_x86_64.zip/download",
        "astap_cli"),
    "macos-aarch64": (
        f"{SF}/macOS%20installer/astap_command-line_version_macOS_M1.zip/download",
        "astap_cli"),
}

#: Download selector -> the vendored SUBDIRECTORY name, which MUST match
#: ``devices/sdk_paths.platform_tag()`` because that is the only path
#: ``solve/astap.py::_bundled_candidates`` looks in.
#:
#: These are two different namespaces and conflating them was a real bug. The
#: download key has to tell macOS x86_64 from macOS arm64 (separate archives);
#: platform_tag returns one ``macos`` for both (universal binaries). Of the six
#: selectors, exactly ONE — ``linux-x86_64`` — happened to spell the same as its
#: tag, and that is the platform CI and the dev box run, so the divergence was
#: invisible. On arm64 the binary landed in ``vendor/astap/linux-aarch64/``
#: while the loader read ``vendor/astap/linux-arm64/``, found nothing, fell
#: through to the flat path, found nothing, and silently used a system install
#: — which on an appliance image is no install at all. The star database, which
#: extracts flat, was found the whole time, so the bundle looked half-present.
_OUT_TAG = {
    "windows-x86_64": "win-x64",
    "windows-aarch64": "win-x64",   # platform_tag maps win/arm64 -> win-x64 too
    "linux-x86_64": "linux-x86_64",
    "linux-aarch64": "linux-arm64",
    "macos-x86_64": "macos",
    "macos-aarch64": "macos",
}

VENDOR = Path(__file__).resolve().parents[1] / "server" / "astrodeck" / "vendor" / "astap"


def _download(url: str, dest: Path) -> Path:
    print(f"  fetching {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "astrodeck-vendor/1.0"})
    with urllib.request.urlopen(req, timeout=300) as r, dest.open("wb") as f:  # noqa: S310
        shutil.copyfileobj(r, f)
    return dest


def _extract(archive: Path, into: Path) -> list[str]:
    into.mkdir(parents=True, exist_ok=True)
    names: list[str] = []
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            for m in z.namelist():
                # Never let an archive write outside the target (zip-slip).
                if m.startswith(("/", "..")) or ".." in Path(m).parts:
                    print(f"    SKIP unsafe entry {m!r}")
                    continue
                z.extract(m, into)
                names.append(m)
    elif tarfile.is_tarfile(archive):
        with tarfile.open(archive) as t:
            for m in t.getmembers():
                if m.name.startswith(("/", "..")) or ".." in Path(m.name).parts:
                    print(f"    SKIP unsafe entry {m.name!r}")
                    continue
                t.extract(m, into)
                names.append(m.name)
    else:
        raise SystemExit(f"{archive.name}: not a zip or tar archive")
    return names


def fetch(platforms: list[str], db: str, out: Path) -> int:
    if db not in DATABASES:
        raise SystemExit(f"unknown database {db!r}; pick one of {', '.join(DATABASES)}")
    missing = [p for p in BINARIES if p not in _OUT_TAG]
    if missing:                                  # a new selector with no tag
        raise SystemExit(f"_OUT_TAG has no entry for {', '.join(missing)}")
    # Two selectors can share a tag (both macOS builds -> "macos"), which is
    # correct for a universal binary and WRONG for two different ones: the
    # second extraction would overwrite the first and the manifest would record
    # a sha256 for a file that is no longer there. Refuse rather than clobber.
    tags: dict[str, str] = {}
    for p in platforms:
        t = _OUT_TAG.get(p)
        if t in tags:
            raise SystemExit(
                f"{p} and {tags[t]} both install to {t!r}; fetch them separately")
        if t:
            tags[t] = p
    out.mkdir(parents=True, exist_ok=True)
    manifest: dict = {"source": "https://www.hnsky.org/astap.htm",
                      "program_license": "MPL-2.0",
                      "database": db,
                      "database_attribution": "ESA/Gaia/DPAC",
                      "excluded": "ASTAP's deep-sky/variable-star CSV catalogues "
                                  "(Steinicke, HyperLEDA) — non-commercial terms, "
                                  "and unused by AstroDeck",
                      "platforms": {}}

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for plat in platforms:
            if plat not in BINARIES:
                raise SystemExit(f"unknown platform {plat!r}; "
                                 f"pick from {', '.join(BINARIES)}")
            url, exe = BINARIES[plat]
            print(f"{plat}:")
            arc = _download(url, tmp / f"{plat}.zip")
            # The directory is named for the LOADER's convention, not the
            # download selector's -- see _OUT_TAG.
            dest = out / _OUT_TAG[plat]
            _extract(arc, dest)
            found = next((p for p in dest.rglob(exe)), None)
            if found is None:
                raise SystemExit(f"{plat}: {exe} not found in the archive")
            if found.parent != dest:
                shutil.move(str(found), dest / exe)
            (dest / exe).chmod(0o755)
            manifest["platforms"][plat] = {
                "exe": exe,
                "sha256": hashlib.sha256((dest / exe).read_bytes()).hexdigest(),
            }
            print(f"  -> {dest / exe}")

        url, size_mb = DATABASES[db]
        print(f"{db} star database (~{size_mb:g} MB):")
        arc = _download(url, tmp / f"{db}.zip")
        _extract(arc, out)

    # Same three formats the solver recognises (solve/astap.py DB_EXTENSIONS).
    dbs = sorted(p.name for p in out.iterdir()
                 if p.suffix in (".290", ".1476", ".001"))
    if not dbs:
        raise SystemExit(
            f"{db}: no .290/.1476 files landed — solve/astap.py::_bundled_db_dir "
            "would not point -d at this directory, so the bundle would be "
            "binary-only and silently fall back to a system install")
    manifest["database_files"] = dbs
    (out / "vendor-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    total = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"\n{out}: {len(dbs)} database file(s), {total / 1e6:.1f} MB total")
    print("Attribution is REQUIRED and lives in THIRD-PARTY-NOTICES.md "
          "(ASTAP: MPL-2.0; star data: ESA/Gaia/DPAC).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--platform", action="append", dest="platforms",
                    help="repeatable; default is this machine's only")
    ap.add_argument("--all-platforms", action="store_true",
                    help="every platform (what a release build wants)")
    ap.add_argument("--db", default="d05",
                    help=f"star database ({', '.join(DATABASES)}); default d05, which is what ships — w08 is for CI smoke tests only")
    ap.add_argument("--out", default=str(VENDOR))
    args = ap.parse_args()

    if args.all_platforms:
        platforms = list(BINARIES)
    elif args.platforms:
        platforms = args.platforms
    else:
        # Keyed off the ARCHITECTURE as well as the OS. Keying off sys.platform
        # alone meant running this script ON an arm64 board downloaded the
        # x86_64 build and cheerfully announced it was fetching "linux-x86_64",
        # which is the one platform that board cannot execute. The
        # linux-aarch64 entry has been correct in BINARIES all along; nothing
        # ever selected it.
        machine = platform.machine().lower()
        arm = machine in ("aarch64", "arm64")
        if sys.platform == "win32":
            plat = "windows-x86_64"
        elif sys.platform == "darwin":
            plat = "macos-aarch64" if arm else "macos-x86_64"
        else:
            plat = "linux-aarch64" if arm else "linux-x86_64"
        if plat not in BINARIES:                    # e.g. macos-x86_64 absent
            raise SystemExit(
                f"no ASTAP build listed for this machine ({sys.platform}/"
                f"{machine}); pass --platform explicitly from: "
                f"{', '.join(BINARIES)}")
        platforms = [plat]
        print(f"(no --platform given; fetching {plat} only)")
    return fetch(platforms, args.db, Path(args.out))


if __name__ == "__main__":
    raise SystemExit(main())
