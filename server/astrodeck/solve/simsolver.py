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

One patch of sky can be told not to solve (``SOLVE_FAULT_ENV``), which is how
a probe stages a mosaic panel that never centres; it is asked only after the
refusal above, so it cannot reach a real rig either.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
from pathlib import Path

from astropy.io import fits

from ..devices.sim import _sim_delay
from .base import PlateSolver, SolveResult, WcsSolution

log = logging.getLogger(__name__)

#: Real-hardware hub modes the sim solver must never fake-solve for (review 5d).
#: A discovery miss that falls back here on a live rig must FAIL loudly rather
#: than echo the pointing hint as a centered solve.
_REAL_MODES = ("nina", "alpaca")

#: A PATCH OF SKY THAT DOES NOT SOLVE, on the simulator only (#189 S7 item 1:
#: "a forced solve failure on one panel"). ``"<ra_hours>,<dec_deg>,<radius_deg>"``:
#: every solve whose field centre lies within ``radius_deg`` of that point, on
#: the great circle, fails as a solve that finds no solution does, and every
#: other solve answers as before. It is how the UI probe stages one panel of a
#: running mosaic that never centres, end to end on a real server, with no
#: mock in the process: tools/ui_probe/server_ctl.py sets it when it starts
#: the server (``--sim-solve-fault``), for the one run that asks.
#:
#: SIM-ONLY BY CONSTRUCTION. It is read by this class alone, and only after
#: the real-rig refusal below, so on a real rig the answer is that refusal
#: whatever the variable says: a real rig never reaches a synthetic solution,
#: failed or not. Read at every solve (never cached at import), so a test sets
#: it with monkeypatch and a server started without it has none.
SOLVE_FAULT_ENV = "ASTRODECK_SIM_SOLVE_FAULT"

#: The words a faulted solve fails with. What a person reads in the engine's
#: set-aside reason and the run log, so it says the failure was staged.
SOLVE_FAULT_MESSAGE = (f"no solution: this field is inside the simulator's staged "
                       f"solve failure ({SOLVE_FAULT_ENV})")

#: Values already warned about as unreadable, so a bad value is said once per
#: process and not on every solve of a night.
_warned_fault_values: set[str] = set()


def solve_fault() -> tuple[float, float, float] | None:
    """``(ra_hours, dec_deg, radius_deg)`` from ``SOLVE_FAULT_ENV``, or None
    when it is unset, blank or unreadable. An unreadable value is no fault,
    said once as a warning: a knob that silently failed every solve would
    stage a different night from the one asked for, and one that raised
    would fail every solve the same way."""
    raw = os.environ.get(SOLVE_FAULT_ENV, "").strip()
    if not raw:
        return None
    try:
        ra_h, dec, radius = (float(p) for p in raw.split(","))
    except ValueError:
        ra_h = dec = radius = math.nan
    if not all(math.isfinite(v) for v in (ra_h, dec, radius)) or radius <= 0:
        if raw not in _warned_fault_values:
            _warned_fault_values.add(raw)
            log.warning("%s=%r is not '<ra_hours>,<dec_deg>,<radius_deg>' with a "
                        "positive radius, so no solve is failed", SOLVE_FAULT_ENV, raw)
        return None
    return ra_h, dec, radius


def _separation_deg(ra1_h: float, dec1: float, ra2_h: float, dec2: float) -> float:
    """Great-circle separation in degrees (haversine), so a fault centred at
    23h59m catches a field at 0h01m, as the sky does."""
    ra1, ra2 = math.radians(ra1_h * 15.0), math.radians(ra2_h * 15.0)
    d1, d2 = math.radians(dec1), math.radians(dec2)
    h = (math.sin((d2 - d1) / 2.0) ** 2
         + math.cos(d1) * math.cos(d2) * math.sin((ra2 - ra1) / 2.0) ** 2)
    return math.degrees(2.0 * math.asin(min(1.0, math.sqrt(h))))


def _inside_solve_fault(ra_h: float, dec: float) -> bool:
    """Whether a field centred at (``ra_h``, ``dec``) is inside the staged
    solve failure (``solve_fault``)."""
    fault = solve_fault()
    if fault is None:
        return False
    f_ra, f_dec, radius = fault
    return _separation_deg(f_ra, f_dec, ra_h, dec) <= radius


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
    # Canonical CD matrix from CDELT1=-scale (RA flip), CDELT2=+scale, CROTA2=rot
    # (FITS WCS Paper II eq. 189; verified against astropy's pixel_scale_matrix).
    # The off-diagonals carry the SAME sign as CD1_1's -scale, so a non-zero
    # rotation turns the sky in the standard (CROTA) sense; rot=0 is unaffected.
    return WcsSolution(
        crval1=ra_hours * 15.0, crval2=dec_deg,
        crpix1=(nx + 1) / 2.0, crpix2=(ny + 1) / 2.0,
        cd11=-scale * math.cos(rot), cd12=-scale * math.sin(rot),
        cd21=-scale * math.sin(rot), cd22=scale * math.cos(rot))


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
                    fov_deg_hint: float | None = None,
                    downsample: int = 0) -> SolveResult:
        # `downsample` is an ASTAP speed/precision knob; the synthetic solver
        # has no such trade-off, so it is accepted (ABC contract) and ignored.
        # Refuse on a real rig: a SimSolver only ever reaches a live rig as the
        # ASTAP-not-found fallback, and faking a solve there would silently
        # fake-center a real mount (review 5d). Fail loudly instead.
        if self.mode in _REAL_MODES or self.real_motion:
            what = self.mode if self.mode in _REAL_MODES else "real-motion"
            return SolveResult(
                False,
                message=("ASTAP not found — refusing to fake a plate solve on a "
                         f"real ({what}) rig. Install ASTAP or set ASTAP_PATH."))
        # "Pretend to work" pacing, routed through the SAME ``_sim_delay`` knob
        # every other simulator wait uses (devices/sim.py): under the test
        # fast-path (``ASTRODECK_FAST_TEST=1``, set suite-wide by conftest's
        # ``_fast_sim_delays``) this collapses to 0.0, so a centering/rotate
        # loop's dozen-plus solve attempts stop costing 1.2 s of wall clock
        # each. PACING ONLY: every value below is derived from the sim rig's
        # pointing/rotator state and the FITS geometry, never from elapsed
        # dwell, so a zero-wait solve is byte-identical to a paced one. In
        # production (flag unset) the pause is unchanged. Two tests
        # (test_goto_rotation / test_rotate_to_pa) opt back OUT via their
        # ``_real_solve_dwell`` fixture, keeping one realistically-paced goto
        # loop and one realistically-paced rotate loop as timing anchors.
        await asyncio.sleep(_sim_delay(1.2))
        # The staged solve failure (``SOLVE_FAULT_ENV``), here and only here:
        # after the real-rig refusal above, so a real rig never gets as far as
        # asking it, and before either synthetic answer below. The field is
        # where the frame was taken: the sim rig's true pointing, the pointing
        # error of an unsynced goto included (a sky patch does not solve
        # wherever the mount thought it was), else the hint.
        where = ((self.sim_rig.ra_hours, self.sim_rig.dec_deg)
                 if self.sim_rig is not None else (ra_hint, dec_hint))
        if where[0] is not None and where[1] is not None \
                and _inside_solve_fault(float(where[0]), float(where[1])):
            return SolveResult(False, message=SOLVE_FAULT_MESSAGE)
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
