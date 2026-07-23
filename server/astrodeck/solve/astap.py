"""ASTAP CLI plate solver (the standard fast local solver).

Runs `astap -f image.fits -ra H -spd D+90 -r radius` and reads the .ini
result file ASTAP writes next to the image.
"""
from __future__ import annotations

import asyncio
import math
import os
from pathlib import Path

from astropy.io import fits

from .base import PlateSolver, SolveResult, WcsSolution

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


def _read_ini(ini_path: Path) -> dict[str, str]:
    kv: dict[str, str] = {}
    for line in ini_path.read_text().splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            kv[k.strip()] = v.strip()
    return kv


def _wcs_from_astap(ini_path: Path, wcs_path: Path) -> "WcsSolution | None":
    """Build a WcsSolution from ASTAP's result files. Prefers the `.wcs`
    headerlet (a text FITS header) parsed with astropy; falls back to the `.ini`
    numerics. Returns None on ANY failure — a WCS-parse error must never fail the
    solve. Mirrors the `_solve_args` pattern: pure + subprocess-free, so it is
    unit-testable from fixture files."""
    try:
        if wcs_path.exists():
            h = fits.Header.fromtextfile(str(wcs_path))
            def _f(key):
                return float(h[key]) if key in h else None
            sol = WcsSolution(
                crval1=float(h["CRVAL1"]), crval2=float(h["CRVAL2"]),
                crpix1=float(h["CRPIX1"]), crpix2=float(h["CRPIX2"]),
                cd11=_f("CD1_1"), cd12=_f("CD1_2"),
                cd21=_f("CD2_1"), cd22=_f("CD2_2"),
                cdelt1=_f("CDELT1"), cdelt2=_f("CDELT2"), crota2=_f("CROTA2"),
                ctype1=str(h.get("CTYPE1", "RA---TAN")),
                ctype2=str(h.get("CTYPE2", "DEC--TAN")))
        else:
            kv = _read_ini(ini_path)
            if "CRVAL1" not in kv or "CRPIX1" not in kv:
                return None
            def _g(key):
                return float(kv[key]) if key in kv else None
            sol = WcsSolution(
                crval1=float(kv["CRVAL1"]), crval2=float(kv["CRVAL2"]),
                crpix1=float(kv["CRPIX1"]), crpix2=float(kv["CRPIX2"]),
                cd11=_g("CD1_1"), cd12=_g("CD1_2"),
                cd21=_g("CD2_1"), cd22=_g("CD2_2"),
                cdelt1=_g("CDELT1"), cdelt2=_g("CDELT2"), crota2=_g("CROTA2"))
        # A reference point with no scale (no CD*, no CDELT*) is a bogus WCS —
        # astropy reads it back as a silent 1 deg/pixel solution. Treat it as
        # unsolved rather than let a wrong astrometric scale reach a saved light
        # (spec §8: a wrong value is worse than an absent card).
        if sol.cd11 is None and sol.cdelt1 is None:
            return None
        return sol
    except Exception:
        return None


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
        wcs_path = fits_path.with_suffix(".wcs")
        if not ini.exists():
            return SolveResult(False, message="ASTAP produced no result file")
        kv = _read_ini(ini)
        # Capture the full WCS from the .wcs headerlet BEFORE deleting the files.
        wcs = _wcs_from_astap(ini, wcs_path)
        try:
            ini.unlink()
            wcs_path.unlink(missing_ok=True)
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
                           wcs=wcs, message="solved by ASTAP")

    @staticmethod
    def _fov_from_scale(scale_arcsec: float, height_px: int) -> float:
        return scale_arcsec * height_px / 3600.0
