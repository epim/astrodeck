"""ASTAP CLI plate solver (the standard fast local solver).

Runs `astap -f image.fits -ra H -spd D+90 -r radius` and reads the .ini
result file ASTAP writes next to the image.
"""
from __future__ import annotations

import asyncio
import math
import os
from pathlib import Path

from .base import PlateSolver, SolveResult

# The command-line build is named `astap_cli` (ASTAP docs: it "can be renamed
# to astap"), so search both names on every OS. ASTAP_PATH overrides all of this.
_CANDIDATES = [
    # Windows
    r"C:\Program Files\astap\astap.exe",
    r"C:\Program Files\astap\astap_cli.exe",
    r"C:\Program Files (x86)\astap\astap.exe",
    r"C:\Program Files (x86)\astap\astap_cli.exe",
    # Linux
    "/usr/bin/astap",
    "/usr/bin/astap_cli",
    "/usr/local/bin/astap",
    "/usr/local/bin/astap_cli",
    "/opt/astap/astap",
    "/opt/astap/astap_cli",
    # macOS (has no standard bin path — ASTAP_PATH is the reliable route)
    "/Applications/ASTAP.app/Contents/MacOS/astap",
    "/opt/homebrew/bin/astap",
    "/opt/homebrew/bin/astap_cli",
]


# The release vendors astap_cli + its star DB next to the package (docs: bundle
# ASTAP + D05). One directory holds both the per-OS binary and the D05 `.290`
# database files.
_VENDOR_ASTAP = Path(__file__).resolve().parents[1] / "vendor" / "astap"


def _bundled_candidates() -> list[Path]:
    # Checked FIRST so a bundled binary wins over a system install; names cover
    # the CLI and the renamed-to-astap form, per OS (closes the macOS gap, which
    # has no standard install path).
    return [_VENDOR_ASTAP / n
            for n in ("astap.exe", "astap_cli.exe", "astap", "astap_cli")]


def _bundled_db_dir() -> Path | None:
    """The vendored star-database directory, colocated with the bundled binary.
    ASTAP needs ``-d <dir>`` to find a DB outside its own install tree; a system
    install locates its own. Returns the dir only when it actually holds star-DB
    files (``.290`` for D05/D20/D50, ``.1476`` for the larger format), so a
    binary-only partial bundle never points ``-d`` at an empty directory."""
    root = _VENDOR_ASTAP
    if root.is_dir() and (any(root.glob("*.290")) or any(root.glob("*.1476"))):
        return root
    return None


def _db_dir() -> Path | None:
    """Resolve the ``-d`` star-DB directory: the ``ASTAP_DATA`` override wins, else
    the bundled DB, else None (let ASTAP use its own default location)."""
    env = os.environ.get("ASTAP_DATA")
    if env and Path(env).is_dir():
        return Path(env)
    return _bundled_db_dir()


def _solve_args(exe: str, fits_path: Path, ra_hint: float | None,
                dec_hint: float | None, fov_deg_hint: float | None,
                db_dir: Path | None) -> list[str]:
    """Assemble the astap_cli argv (extracted so the flag wiring — including the
    bundled ``-d`` DB path — is unit-testable without launching a subprocess)."""
    args = [exe, "-f", str(fits_path), "-z", "0"]
    if ra_hint is not None and dec_hint is not None:
        args += ["-ra", f"{ra_hint:.4f}", "-spd", f"{dec_hint + 90:.4f}", "-r", "15"]
    else:
        args += ["-r", "180"]
    if fov_deg_hint:
        args += ["-fov", f"{fov_deg_hint:.2f}"]
    if db_dir is not None:
        args += ["-d", str(db_dir)]
    return args


def find_astap() -> str | None:
    env = os.environ.get("ASTAP_PATH")
    if env and Path(env).exists():
        return env
    for c in (*_bundled_candidates(), *_CANDIDATES):
        if Path(c).exists():
            return str(c)
    return None


class AstapSolver(PlateSolver):
    name = "ASTAP"

    def __init__(self, exe: str):
        self.exe = exe

    async def solve(self, fits_path: Path, *, ra_hint: float | None = None,
                    dec_hint: float | None = None,
                    fov_deg_hint: float | None = None) -> SolveResult:
        args = _solve_args(self.exe, fits_path, ra_hint, dec_hint,
                           fov_deg_hint, _db_dir())

        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=60)
        except asyncio.TimeoutError:
            proc.kill()
            return SolveResult(False, message="ASTAP timed out")

        ini = fits_path.with_suffix(".ini")
        if not ini.exists():
            return SolveResult(False, message="ASTAP produced no result file")
        kv = {}
        for line in ini.read_text().splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                kv[k.strip()] = v.strip()
        try:
            ini.unlink()
            fits_path.with_suffix(".wcs").unlink(missing_ok=True)
        except OSError:
            pass

        if kv.get("PLTSOLVD") != "T":
            return SolveResult(False, message=kv.get("ERROR", "no solution"))
        ra_deg = float(kv["CRVAL1"])
        dec_deg = float(kv["CRVAL2"])
        rot = float(kv.get("CROTA2", 0))
        scale = abs(float(kv.get("CDELT2", 0))) * 3600
        return SolveResult(True, ra_hours=ra_deg / 15.0, dec_deg=dec_deg,
                           rotation_deg=rot, pixel_scale_arcsec=scale,
                           message="solved by ASTAP")

    @staticmethod
    def _fov_from_scale(scale_arcsec: float, height_px: int) -> float:
        return scale_arcsec * height_px / 3600.0
