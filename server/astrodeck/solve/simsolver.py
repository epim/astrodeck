"""Simulator solver: answers with the sim mount's true pointing.

This makes the full goto→solve→sync→re-slew centering loop work end to end
with the simulated rig, including the deliberate pointing error the sim
mount introduces.

SAFETY (review 5d): this solver is a *fallback* when ASTAP isn't installed.
On a real rig that would be a false-solve hazard — with a hint it would echo
the hint straight back as a "successful solve" and fake-center the mount on a
discovery miss. So it REFUSES (returns failure) whenever it is asked to solve
for a real (``mode == "nina"`` / ``"alpaca"``) rig: only an explicit sim rig
or an explicitly-sim mode may receive a synthetic solution.
"""
from __future__ import annotations

import asyncio
import math
from pathlib import Path

from astropy.io import fits

from .base import PlateSolver, SolveResult, WcsSolution

#: Real-hardware hub modes the sim solver must never fake-solve for (review 5d).
#: A discovery miss that falls back here on a live rig must FAIL loudly rather
#: than echo the pointing hint as a centered solve.
_REAL_MODES = ("nina", "alpaca")


def _sim_wcs(fits_path: Path, ra_hours: float, dec_deg: float,
             rot_deg: float, scale_arcsec: float) -> WcsSolution:
    """A valid TAN WcsSolution centered on the sim pointing (hips_local idiom:
    ctype RA---TAN/DEC--TAN, crval, crpix at image center, CD from scale+rot)."""
    try:
        with fits.open(fits_path) as hdul:
            ny, nx = hdul[0].data.shape[-2:]
    except Exception:
        nx = ny = 1000                       # tolerate a not-yet-written path
    scale = scale_arcsec / 3600.0
    rot = math.radians(rot_deg)
    return WcsSolution(
        crval1=ra_hours * 15.0, crval2=dec_deg,
        crpix1=(nx + 1) / 2.0, crpix2=(ny + 1) / 2.0,
        cd11=-scale * math.cos(rot), cd12=scale * math.sin(rot),
        cd21=scale * math.sin(rot), cd22=scale * math.cos(rot))


class SimSolver(PlateSolver):
    name = "Simulator"

    def __init__(self, sim_rig=None, mode: str | None = None,
                 real_motion: bool = False):
        self.sim_rig = sim_rig
        #: the hub mode this solver was built for, so it can refuse to invent a
        #: solution for a real rig (review 5d). None / "sim" / "none" are safe.
        self.mode = mode
        #: device-flag truth (driver-framework A-minor 2): True when the rig has
        #: ANY real motion device (``Device.hardware``) — covers native serial
        #: mounts whose session name the mode denylist doesn't know.
        self.real_motion = real_motion

    async def solve(self, fits_path: Path, *, ra_hint: float | None = None,
                    dec_hint: float | None = None,
                    fov_deg_hint: float | None = None) -> SolveResult:
        # Refuse on a real rig: a SimSolver only ever reaches a live rig as the
        # ASTAP-not-found fallback, and faking a solve there would silently
        # fake-center a real mount (review 5d). Fail loudly instead.
        if self.mode in _REAL_MODES or self.real_motion:
            what = self.mode if self.mode in _REAL_MODES else "real-motion"
            return SolveResult(
                False,
                message=("ASTAP not found — refusing to fake a plate solve on a "
                         f"real ({what}) rig. Install ASTAP or set ASTAP_PATH."))
        await asyncio.sleep(1.2)  # pretend to work
        if self.sim_rig is not None:
            # Physical truth: the camera's sky PA is the rotator's mechanical
            # angle plus how the camera is clocked on it. Both default 0.0, so
            # rigs/tests that never touch the rotator see rotation 0.0 exactly
            # as before.
            rot_pa = (getattr(self.sim_rig, "rotator_mech_deg", 0.0)
                      + getattr(self.sim_rig, "rotator_pa_offset_deg", 0.0)) % 360.0
            return SolveResult(True, ra_hours=self.sim_rig.ra_hours,
                               dec_deg=self.sim_rig.dec_deg,
                               rotation_deg=rot_pa, pixel_scale_arcsec=1.55,
                               wcs=_sim_wcs(fits_path, self.sim_rig.ra_hours,
                                            self.sim_rig.dec_deg, rot_pa, 1.55),
                               message="solved (simulator)")
        if ra_hint is not None and dec_hint is not None:
            return SolveResult(True, ra_hours=ra_hint, dec_deg=dec_hint,
                               rotation_deg=0.0, pixel_scale_arcsec=1.55,
                               wcs=_sim_wcs(fits_path, ra_hint, dec_hint, 0.0, 1.55),
                               message="solved (simulator, from hint)")
        return SolveResult(False, message="simulator solver needs a hint or sim rig")
