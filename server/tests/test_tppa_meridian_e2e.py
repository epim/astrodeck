"""Native TPPA end to end against a mount that answers a meridian crossing the
way a German equatorial really does: with a pier flip.

Why this file exists
--------------------
On 2026-08-06 a real run reported 7271 arcminutes (121 degrees) of polar error
and told the operator to adjust the mount. The RA step was unconditionally
POSITIVE, which walks the tube EAST; the run started west of the meridian, so
the arc crossed it and the AM5N flipped mid-measurement. The bug shipped because
NOTHING in the suite modelled a flip: ``SimTelescope.slew`` ignores the
commanded RA entirely when a polar misalignment is injected ("The commanded
target is intentionally ignored for the phase" -- ``devices/sim.py``), so in the
sim the SIGN of the RA step is invisible and the meridian does not exist.

``_FlippingGemMount`` below closes that hole. It is a forward model, not a
mock: it holds a mechanical RA-axis angle, tracks the sky by advancing that
angle at the sidereal rate, converts the commanded RA delta into a mechanical
rotation (sign and all), and flips when a commanded slew crosses HA = 0.

What a flip does to the geometry -- and what it does not
-------------------------------------------------------
This matters, because the obvious model is wrong. An IDEAL GEM flip is
sky-preserving: mechanical (theta, delta) -> (theta + 180, 180 - delta) puts the
tube back on the identical sky point, so an ideal flip would leave all three
measurement points on the same small circle and ``tppa_from_three`` would still
recover the injected error. ``test_a_perfect_flip_alone_does_not_corrupt_the_fit``
pins exactly that, so nobody reads the rest of this file as "PA spread is what
breaks it" -- the position angle never enters the axis fit at all (astro-tppa
``lib.rs``: only ``position_from_solve``'s ra/dec/time vectors reach
``determine_plane_vector``).

What actually breaks the fit is that a real GEM's pointing is NOT identical
across a flip. Dec index error, dec/RA non-orthogonality and flexure all reverse
sign with the pier side -- which is why every pointing model carries separate
east-of-pier and west-of-pier terms, and why "sync on both sides" exists. The
net effect at the tube is a RADIAL displacement: the flipped points sit at a
different angular distance from the mount's RA axis, i.e. OFF the small circle
the fit is a plane through. ``flip_dec_error_deg`` is that displacement, and the
production error message names the same signature to the operator ("their
declinations should be within a degree or so of each other").

Calibration: with the geometry here (site 45N, 38 degree pole distance, 12
degree RA steps) a -3.0 degree flip displacement reproduces the rig failure
closely -- ~7000 arcmin total, altitude error ~-6900 arcmin, fitted axis ~70
degrees below the horizon, position-angle spread 180 -- against the logged
7271 / -7091 / 80.8-below / 179.7. That is the number
``test_the_pre_fix_step_reproduces_the_2026_08_06_rig_failure`` uses.

Everything here is offline: no rig, no sim rig, no network. The mount, camera
and solver are local fakes; ``run_native`` and the Rust engine are the real
things under test.
"""
from __future__ import annotations

import asyncio
import math
import time
from contextlib import asynccontextmanager

import numpy as np
import pytest

import astrodeck.hub as hub_module
import astrodeck.polar.native as native_mod
from astrodeck import providers
from astrodeck.devices.base import CameraFrame, DeviceError
# The geometry primitives the sim's PolarMisalignment is built from -- the same
# refraction-free horizontal<->equatorial pair the Rust crate uses internally, so
# a forward-modelled solve inverts back through the engine exactly.
from astrodeck.devices.sim import (_altaz_from_neu, _cross, _horiz_to_equ,
                                   _jd_from_unix, _neu_from_altaz, _normalize,
                                   _rodrigues)
from astrodeck.polar.native import run_native
from astrodeck.polar.session import PolarAlignSession
from astrodeck.sequence.schedule import hour_angle_h
from astrodeck.solve.base import SolveResult

pytestmark = pytest.mark.skipif(
    not providers.NATIVE_AVAILABLE, reason="astrodeck_native wheel not installed")

# Invented site: northern, western hemisphere. Northern so the hemisphere branch
# in calculate_mount_axis_error is the one the rig uses; western so the sign of
# the hour angle is the thing that decides the step direction.
_LAT = 45.0
_LON = -122.0

#: Angular distance from the mount's RA axis to the optical axis -- the radius of
#: the small circle the three measurements trace. 38 degrees is the geometry the
#: MAX_PLAUSIBLE_ERROR_DEG docstring quotes its conditioning numbers for, and it
#: clears MIN_POLE_DISTANCE_DEG (20) with room to spare.
_RHO_DEG = 38.0

#: The injected polar error the engine must recover, arcminutes.
_INJ_AZ, _INJ_ALT = 5.0, 6.0
_INJ_TOTAL = math.hypot(_INJ_AZ, _INJ_ALT)

#: Earth's rotation, degrees of mechanical RA axis per second. A tracking mount
#: turns its RA axis at exactly this rate, which is what holds RA constant while
#: the hour angle climbs.
_SIDEREAL_DEG_PER_S = 360.98564736629 / 86400.0

#: Mechanical sign: commanding RA *up* turns the mount's RA axis in the NEGATIVE
#: theta sense here (HA = LST - RA, so RA up is HA down is eastward).
#: ``test_the_fake_mount_honours_the_commanded_ra_step`` pins it -- get it
#: backwards and every "west start" below silently becomes an east start.
_RA_TO_THETA = -1.0

#: The pre-fix ``_ra_step_hours``, verbatim: always step RA up, whatever the hour
#: angle. Restored by monkeypatch (never by editing production code) so the
#: regression tests drive the REAL driver with the OLD decision.
_PRE_FIX_STEP_HOURS = native_mod._RA_STEP_HOURS

#: The shipped helper, captured at import so a test can put it back explicitly.
_SHIPPED_RA_STEP = native_mod._ra_step_hours


def _pre_fix_ra_step(hub, cur_ra_hours, pier_side=None):  # noqa: ARG001 - signature parity
    return _PRE_FIX_STEP_HOURS


# --------------------------------------------------------------- the fake mount

class _FlippingGemMount:
    """A German-equatorial mount with a tilted RA axis, sidereal tracking, and a
    pier flip on any commanded slew that crosses the meridian.

    State is one number: ``theta_mech``, the accumulated *commanded* rotation of
    the RA axis in degrees. Tracking adds ``_SIDEREAL_DEG_PER_S * elapsed`` on
    top, which is what makes the reported RA hold still while the hour angle
    climbs. The optical axis is ``_RHO_DEG`` from the (tilted) RA axis and is
    rotated about it by theta, so every un-flipped pointing lies on one small
    circle -- exactly the geometry ``tppa_from_three`` inverts.

    Two readouts, deliberately different:

    * :meth:`reported_radec` is what the MOUNT claims (the driver's
      ``get_position``) -- the un-displaced circle, because the mount does not
      know about its own pier-side pointing asymmetry; and
    * :meth:`true_radec` is what a PLATE SOLVE sees, which on the flipped side
      is displaced radially by ``flip_dec_error_deg``.

    ``slew`` honours the commanded RA delta (this is the whole point -- the
    production sim does not) and ignores the commanded Dec: a real mount would
    track the Dec too, but the measurement arc never asks for a Dec change and
    modelling one would only hide which axis moved.
    """

    def __init__(self, *, az_arcmin: float, alt_arcmin: float,
                 flip_dec_error_deg: float = 0.0, pa0_deg: float = 12.0,
                 t0: float | None = None, tracking: bool = True) -> None:
        self.clock = time.time
        self.t0 = t0 if t0 is not None else self.clock()
        # Sidereal tracking, modelled rather than stubbed: theta only advances
        # while it is on, so a mount handed over with tracking off really does
        # let the sky slide out from under it. park/find_home leave a real mount
        # exactly here, which is why the driver now asserts it (_ensure_tracking).
        self.tracking = bool(tracking)
        self.tracking_calls: list[bool] = []
        self._track_from = self.t0
        self.theta_mech = 0.0
        self.flip_dec_error_deg = flip_dec_error_deg
        self.pa0_deg = pa0_deg
        self.flips = 0
        self.slews = 0
        self.slew_log: list[tuple[float, float]] = []
        self.connected = True
        self.name = "Fake GEM"
        # The mount's RA axis: tilted from the true pole by the injected error.
        self.axis = _neu_from_altaz(_LAT + alt_arcmin / 60.0, az_arcmin / 60.0)
        tilt = _normalize(_cross(self.axis, (0.0, 0.0, 1.0)))
        self._ref = _rodrigues(self.axis, tilt, math.radians(_RHO_DEG))

    # -- geometry -----------------------------------------------------------

    def _theta(self, t: float) -> float:
        if not self.tracking:
            return self.theta_mech
        return self.theta_mech + _SIDEREAL_DEG_PER_S * (t - self._track_from)

    def _unit(self, t: float, *, displaced: bool):
        u = _rodrigues(self._ref, self.axis, math.radians(self._theta(t)))
        if displaced and self.flipped and self.flip_dec_error_deg:
            # Radial (distance-from-the-RA-axis) displacement: rotate about the
            # normal to the axis/tube plane, which is the ONE direction that
            # moves the point OFF the small circle. A tangential displacement
            # (classical cone error) would just slide it along the circle and
            # the fit would not notice -- see the module docstring.
            k = _normalize(_cross(self.axis, u))
            u = _rodrigues(u, k, math.radians(self.flip_dec_error_deg))
        return u

    def _radec(self, t: float, *, displaced: bool) -> tuple[float, float]:
        alt, az = _altaz_from_neu(self._unit(t, displaced=displaced))
        ra_deg, dec_deg = _horiz_to_equ(alt, az, _LAT, _LON, _jd_from_unix(t))
        return (ra_deg / 15.0) % 24.0, dec_deg

    def reported_radec(self, t: float | None = None) -> tuple[float, float]:
        return self._radec(self.clock() if t is None else t, displaced=False)

    def true_radec(self, t: float | None = None) -> tuple[float, float]:
        return self._radec(self.clock() if t is None else t, displaced=True)

    def axis_distance_deg(self, t: float | None = None, *,
                          displaced: bool = True) -> float:
        """Angular distance from the mount's RA axis -- the small circle's radius.
        Constant across a clean run; a flip displacement is exactly what changes
        it, which is the off-circle error the plane fit amplifies."""
        u = self._unit(self.clock() if t is None else t, displaced=displaced)
        dot = sum(a * b for a, b in zip(u, self.axis))
        return math.degrees(math.acos(max(-1.0, min(1.0, dot))))

    # -- pier side ----------------------------------------------------------

    @staticmethod
    def side_of_pier(ha_hours: float) -> str:
        """Which side a GEM must use to reach a target at this hour angle. Only
        the CHANGE matters to these tests; the labels are conventional."""
        return "west" if ha_hours > 0.0 else "east"

    @property
    def flipped(self) -> bool:
        return bool(self.flips % 2)

    @property
    def position_angle_deg(self) -> float:
        """Camera position angle. A flip swings the whole optical train around
        the mount, so the field arrives rotated by 180 degrees."""
        return (self.pa0_deg + 180.0 * (self.flips % 2)) % 360.0

    # -- telescope interface the driver uses --------------------------------

    async def get_position(self) -> tuple[float, float]:
        return self.reported_radec()

    async def get_tracking(self) -> bool:
        return self.tracking

    async def set_tracking(self, on: bool) -> None:
        # Starting the clock from NOW is the honest model: switching tracking on
        # does not retroactively un-drift the sky it already lost.
        if on and not self.tracking:
            self.theta_mech = self._theta(self.clock())
            self._track_from = self.clock()
        self.tracking = bool(on)
        self.tracking_calls.append(bool(on))

    async def slew(self, ra_hours: float, dec_deg: float) -> None:  # noqa: ARG002
        now = self.clock()
        cur_ra, _cur_dec = self.reported_radec(now)
        delta_ra = ((ra_hours - cur_ra + 12.0) % 24.0) - 12.0
        ha_before = hour_angle_h(cur_ra, _LON, now)
        ha_after = hour_angle_h(ra_hours, _LON, now)
        # A flip is a MERIDIAN crossing (HA = 0). The |HA| < 6 guard keeps the
        # HA = +/-12 wrap (below the pole, unreachable on this arc) from reading
        # as a crossing.
        if (abs(ha_before) < 6.0 and abs(ha_after) < 6.0
                and self.side_of_pier(ha_before) != self.side_of_pier(ha_after)):
            self.flips += 1
        self.theta_mech += delta_ra * 15.0 * _RA_TO_THETA
        self.slews += 1
        self.slew_log.append((ra_hours, dec_deg))
        await asyncio.sleep(0)

    # -- test setup ---------------------------------------------------------

    def place_at_hour_angle(self, target_ha: float) -> None:
        """Wind ``theta_mech`` so the mount starts at ``target_ha``.

        Coarse scan then bisection: HA is monotonic in theta over a revolution
        (theta up -> RA down -> HA up) but wraps, so a plain bisection over the
        full circle would land on the wrap."""
        t = self.clock()

        def ha_at(theta: float) -> float:
            self.theta_mech = theta
            return hour_angle_h(self.reported_radec(t)[0], _LON, t)

        best, best_gap = 0.0, 1e9
        for i in range(721):
            theta = -180.0 + i * 0.5
            gap = abs(((ha_at(theta) - target_ha + 12.0) % 24.0) - 12.0)
            if gap < best_gap:
                best, best_gap = theta, gap
        lo, hi = best - 0.5, best + 0.5
        for _ in range(60):
            mid = (lo + hi) / 2.0
            if ha_at(mid) > target_ha:
                hi = mid
            else:
                lo = mid
        self.theta_mech = (lo + hi) / 2.0
        landed = hour_angle_h(self.reported_radec(t)[0], _LON, t)
        assert abs(landed - target_ha) < 1e-3, (
            f"could not place the mount at HA {target_ha}: landed at {landed}")


# ------------------------------------------------- fake camera / solver / hub

class _FakeCamera:
    name = "Fake Cam"
    connected = True
    pixel_size_um = 3.76
    sensor_width = 64
    sensor_height = 48

    def __init__(self) -> None:
        self.exposures = 0
        self.last_timestamp = 0.0

    async def expose(self, seconds, gain, offset, binning=1):
        self.exposures += 1
        self.last_timestamp = time.time()
        return CameraFrame(
            data=np.zeros((self.sensor_height, self.sensor_width), dtype=np.uint16),
            exposure_s=float(seconds), gain=int(gain), offset=int(offset),
            binning=int(binning), bayer_pattern=None, temperature_c=-10.0,
            timestamp=self.last_timestamp)


class _TruthSolver:
    """Plate solves the sky the fake mount is actually pointing at.

    Reports the DISPLACED (true) position -- a plate solve reads the sky, not the
    mount's belief -- plus the camera position angle, and records every result so
    a test can assert on the three points the driver really fed the engine.

    A small deterministic position-angle wobble stands in for solve-to-solve
    scatter, so "the spread stayed small" is a measurement of a non-zero number
    rather than an identity that would pass on a broken engine.
    """

    #: degrees; well under the 5 degree _PA_SPREAD_WARN_DEG, nowhere near 180.
    PA_WOBBLE_DEG = 0.3

    def __init__(self, mount: _FlippingGemMount, camera: _FakeCamera) -> None:
        self.mount = mount
        self.camera = camera
        self.results: list[SolveResult] = []
        self.timestamps: list[float] = []

    async def solve(self, path, *, ra_hint=None, dec_hint=None,  # noqa: ARG002
                    fov_deg_hint=None, downsample=0):
        t = self.camera.last_timestamp or time.time()
        ra, dec = self.mount.true_radec(t)
        wobble = self.PA_WOBBLE_DEG * (1 if len(self.results) % 2 else -1)
        res = SolveResult(success=True, ra_hours=ra, dec_deg=dec,
                          rotation_deg=(self.mount.position_angle_deg + wobble) % 360.0,
                          pixel_scale_arcsec=1.55, message="ok")
        self.results.append(res)
        self.timestamps.append(t)
        return res


class _FakeHub:
    """The slice of Hub ``polar/native.py`` actually touches."""

    mode = "sim"          # -> _options() passes pressure_hpa=0 (no atmosphere)
    nina_client = None
    sim_rig = None

    def __init__(self, mount: _FlippingGemMount, camera: _FakeCamera) -> None:
        self.devices = {"telescope": mount, "camera": camera}
        self._motion_epoch = 0
        self.solar_checks: list[tuple[float, float]] = []
        self.camera_yields: list[str] = []

    @property
    def site(self) -> dict:
        return {"latitude": _LAT, "longitude": _LON, "elevation_m": 0.0}

    def require(self, role: str):
        dev = self.devices.get(role)
        if dev is None:
            raise DeviceError(f"no {role} connected")
        return dev

    async def yield_camera_for(self, what: str) -> bool:
        self.camera_yields.append(what)
        return False

    def _check_solar(self, ra_hours: float, dec_deg: float, **_kw) -> None:
        self.solar_checks.append((ra_hours, dec_deg))

    @asynccontextmanager
    async def exposure_guard(self, label: str):  # noqa: ARG002
        yield

    async def _publish_preview(self, frame) -> dict:  # noqa: ARG002
        return {}

    def effective_optics(self) -> dict:
        return {"focal_length_mm": 400.0, "pixel_size_um": 3.76,
                "sensor_width_px": 64, "sensor_height_px": 48,
                "image_scale_arcsec_px": 1.55, "fov_h_deg": 1.2}

    async def from_mount_frame(self, tel, ra_hours, dec_deg):  # noqa: ARG002
        return ra_hours, dec_deg


# ------------------------------------------------------------------- harness

class _Run:
    """Everything a test wants to look at after one ``run_native``."""

    def __init__(self, session, mount, solver, hub):
        self.session = session
        self.mount = mount
        self.solver = solver
        self.hub = hub

    @property
    def state(self) -> dict:
        return self.session.state

    @property
    def hour_angles(self) -> list[float]:
        return [hour_angle_h(r.ra_hours, _LON, t)
                for r, t in zip(self.solver.results, self.solver.timestamps)]

    @property
    def declinations(self) -> list[float]:
        return [r.dec_deg for r in self.solver.results]

    @property
    def pa_spread_deg(self) -> float:
        """Wrap-aware max gap between the three measured position angles."""
        pas = [r.rotation_deg for r in self.solver.results[:3]]
        gaps = [abs(((a - b + 180.0) % 360.0) - 180.0)
                for i, a in enumerate(pas) for b in pas[i + 1:]]
        return max(gaps) if gaps else 0.0


async def _wait(predicate, timeout=30.0) -> bool:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


async def _drive_run(monkeypatch, tmp_path, *, start_ha: float,
                     flip_dec_error_deg: float = -3.0,
                     pre_fix_step: bool = False,
                     tracking: bool = True) -> _Run:
    """Run the real ``run_native`` against the fake GEM and stop at the fit.

    ``flip_dec_error_deg`` defaults to the rig-shaped -3.0 even for the runs that
    are supposed to stay on one side of the meridian: the asymmetry is ARMED in
    every run so a direction regression cannot pass by leaving it disabled.
    """
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    mount = _FlippingGemMount(az_arcmin=_INJ_AZ, alt_arcmin=_INJ_ALT,
                              flip_dec_error_deg=flip_dec_error_deg,
                              tracking=tracking)
    mount.place_at_hour_angle(start_ha)
    camera = _FakeCamera()
    solver = _TruthSolver(mount, camera)
    hub = _FakeHub(mount, camera)
    monkeypatch.setattr(providers, "pick_solver", lambda _hub: solver)
    # Set it EITHER way round, never conditionally: a test that drives the old
    # helper and then the new one inside a single monkeypatch scope would
    # otherwise carry the old one into the second run (which is exactly how the
    # A/B test below first "passed" its broken half twice).
    monkeypatch.setattr(native_mod, "_ra_step_hours",
                        _pre_fix_ra_step if pre_fix_step else _SHIPPED_RA_STEP)
    # The post-rotation settle is real time on a real rig and pure cost here;
    # its own behaviour is graded directly (see the settle tests), so every
    # run that only wants the LOOP pays nothing for it.
    monkeypatch.setattr(native_mod, "_SETTLE_AFTER_SLEW_S", 0.0)

    session = PolarAlignSession(hub)
    task = asyncio.create_task(run_native(session, hub))
    try:
        landed = await _wait(lambda: session.state.get("phase") == "adjusting"
                             or session.state.get("state") == "error")
        assert landed, f"run_native never reached a fit: {session.state}"
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Preconditions, and they depend on whether the run was SUPPOSED to survive.
    #
    # A run that flips no longer reaches three points at all: since 2026-08-06
    # the driver compares consecutive position angles and stops the moment the
    # mount changes sides, so the arc ends after two solves and one rotation
    # instead of completing a full arc and handing back a fit nobody can use.
    # Demanding three solves here would therefore fail every deliberately-broken
    # run in this file — for the RIGHT reason, which is not a useful test.
    #
    # So: a flipped run must stop early and must be an error; an unflipped run
    # must produce the full three points and two rotations, which is what every
    # assertion downstream reads.
    if mount.flips:
        assert len(solver.results) == 2, (
            f"a flip must end the arc at the frame that revealed it, but the "
            f"measuring phase produced {len(solver.results)} solves")
        assert session.state.get("state") == "error", session.state
    else:
        assert len(solver.results) >= 3, (
            f"the measuring phase produced {len(solver.results)} solves, not 3")
        assert mount.slews == 2, (
            f"the driver commanded {mount.slews} rotations, not 2")
    return _Run(session, mount, solver, hub)


# ============================================================ the fake mount

async def test_the_fake_mount_tracks():
    """Tracking, stated as the thing that makes an hour angle move: RA held
    still while HA climbs at the sidereal rate. A mount that did NOT track would
    drift a full hour of RA per hour, and every meridian claim in this file would
    be meaningless."""
    t0 = time.time()
    later = t0 + 3600.0

    aligned = _FlippingGemMount(az_arcmin=0.0, alt_arcmin=0.0, t0=t0)
    aligned.clock = lambda: t0
    aligned.place_at_hour_angle(0.7)
    ra0, dec0 = aligned.reported_radec(t0)
    ra1, dec1 = aligned.reported_radec(later)
    # A perfectly polar-aligned tracking mount holds the star to arcseconds.
    assert abs(ra1 - ra0) * 15.0 * 3600.0 < 2.0, (ra0, ra1)
    assert abs(dec1 - dec0) * 3600.0 < 2.0, (dec0, dec1)
    # ...and one solar hour of that is one sidereal hour of hour angle.
    ha0 = hour_angle_h(ra0, _LON, t0)
    ha1 = hour_angle_h(ra1, _LON, later)
    assert abs((ha1 - ha0) - 1.0027) < 0.002, (ha0, ha1)


async def test_a_misaligned_mount_drifts_by_about_the_injected_error():
    """The other half of tracking, and a check that the injected tilt is really
    in the model: a mount whose RA axis is 7.8 arcmin off the pole cannot hold a
    star, and drifts by roughly that much over an hour. Zero drift would mean the
    misalignment was not wired in; 15 degrees of drift would mean the mount was
    not tracking at all."""
    t0 = time.time()
    tilted = _FlippingGemMount(az_arcmin=_INJ_AZ, alt_arcmin=_INJ_ALT, t0=t0)
    tilted.clock = lambda: t0
    tilted.place_at_hour_angle(0.7)
    ra0, dec0 = tilted.reported_radec(t0)
    ra1, dec1 = tilted.reported_radec(t0 + 3600.0)

    drift_arcmin = math.hypot((ra1 - ra0) * 15.0 * 60.0 * math.cos(math.radians(dec0)),
                              (dec1 - dec0) * 60.0)
    assert drift_arcmin > 0.1 * _INJ_TOTAL, (
        f"a {_INJ_TOTAL:.2f}' misalignment produced {drift_arcmin:.3f}' of drift "
        "in an hour -- the tilt is not reaching the pointing")
    # Bounded by the chord an axis error sweeps in an hour (2*sin(7.52 deg) of it).
    assert drift_arcmin < 2.0 * _INJ_TOTAL, drift_arcmin


async def test_the_fake_mount_honours_the_commanded_ra_step():
    """The one thing ``SimTelescope`` refuses to do (it ignores the commanded RA
    and advances a fixed phase), and the reason the meridian bug was invisible.
    Pins the mechanical sign convention too: get ``_RA_TO_THETA`` backwards and
    every 'west start' in this file is really an east start."""
    t0 = time.time()
    for step in (+0.8, -0.8):
        mount = _FlippingGemMount(az_arcmin=_INJ_AZ, alt_arcmin=_INJ_ALT, t0=t0)
        mount.clock = lambda: t0
        mount.place_at_hour_angle(3.0)  # far from the meridian: no flip either way
        ra0, dec0 = mount.reported_radec(t0)
        await mount.slew((ra0 + step) % 24.0, dec0)
        ra1, _ = mount.reported_radec(t0)
        moved = ((ra1 - ra0 + 12.0) % 24.0) - 12.0
        assert abs(moved - step) < 1e-3, f"commanded {step}h, moved {moved}h"
        assert mount.flips == 0, "a slew nowhere near the meridian must not flip"


async def test_the_fake_mount_flips_only_on_a_meridian_crossing():
    """The flip trigger itself, both ways round. Without this a 'no flip
    happened' assertion downstream could be passing because the mount can never
    flip at all."""
    t0 = time.time()

    # Crossing: start west (HA +0.4), step RA up 0.8h -> HA -0.4.
    crossing = _FlippingGemMount(az_arcmin=_INJ_AZ, alt_arcmin=_INJ_ALT, t0=t0)
    crossing.clock = lambda: t0
    crossing.place_at_hour_angle(0.4)
    ra0, dec0 = crossing.reported_radec(t0)
    pa_before = crossing.position_angle_deg
    await crossing.slew((ra0 + 0.8) % 24.0, dec0)
    assert crossing.flips == 1, "stepping RA up from HA +0.4 must cross HA 0"
    assert hour_angle_h(crossing.reported_radec(t0)[0], _LON, t0) < 0.0
    gap = abs(((crossing.position_angle_deg - pa_before + 180.0) % 360.0) - 180.0)
    assert abs(gap - 180.0) < 1e-6, "a flip rotates the camera 180 degrees"

    # Same start, step the other way -> further west, no crossing.
    staying = _FlippingGemMount(az_arcmin=_INJ_AZ, alt_arcmin=_INJ_ALT, t0=t0)
    staying.clock = lambda: t0
    staying.place_at_hour_angle(0.4)
    ra0, dec0 = staying.reported_radec(t0)
    await staying.slew((ra0 - 0.8) % 24.0, dec0)
    assert staying.flips == 0, "stepping away from the meridian must not flip"
    assert staying.position_angle_deg == pytest.approx(12.0)


async def test_a_flip_displaces_the_tube_off_the_small_circle():
    """The mechanism the whole regression rests on, isolated.

    Un-flipped, the tube stays exactly ``_RHO_DEG`` from the mount's RA axis at
    every theta -- that constant radius IS the small circle the plane fit is
    fitted to. The pier-side pointing asymmetry changes that radius, which is the
    off-circle error the fit amplifies. If this ever became a no-op the garbage
    assertions below would be measuring nothing."""
    t0 = time.time()
    mount = _FlippingGemMount(az_arcmin=_INJ_AZ, alt_arcmin=_INJ_ALT,
                              flip_dec_error_deg=-3.0, t0=t0)
    mount.clock = lambda: t0
    mount.place_at_hour_angle(0.4)
    assert mount.axis_distance_deg(t0) == pytest.approx(_RHO_DEG, abs=1e-6)

    ra0, dec0 = mount.reported_radec(t0)
    await mount.slew((ra0 + 0.8) % 24.0, dec0)
    assert mount.flips == 1
    # What the mount still BELIEVES: dead on the circle.
    assert mount.axis_distance_deg(t0, displaced=False) == pytest.approx(
        _RHO_DEG, abs=1e-6)
    # What the sky actually shows: 3 degrees off it.
    assert mount.axis_distance_deg(t0, displaced=True) == pytest.approx(
        _RHO_DEG - 3.0, abs=1e-6)


async def test_a_perfect_flip_alone_does_not_corrupt_the_fit():
    """A flip with NO pier-side pointing asymmetry is sky-preserving, and the
    engine recovers the injected error anyway.

    Written to keep the rest of this file honest. Position angle never reaches
    ``determine_plane_vector`` -- only the three ra/dec/time vectors do -- so
    'the camera rotated 180 degrees' is a SYMPTOM of the flip, not the cause of
    the bad fit. Anyone tempted to 'fix' TPPA by feeding position angle into the
    axis fit should read this test first."""
    t0 = time.time()
    mount = _FlippingGemMount(az_arcmin=_INJ_AZ, alt_arcmin=_INJ_ALT,
                              flip_dec_error_deg=0.0, t0=t0)
    mount.clock = lambda: t0
    mount.place_at_hour_angle(0.7)

    solves = []
    for i in range(3):
        ra, dec = mount.true_radec(t0)
        solves.append({"ra_hours": ra, "dec_deg": dec, "timestamp_unix_s": t0,
                       "position_angle_deg": mount.position_angle_deg})
        if i < 2:
            ra_r, dec_r = mount.reported_radec(t0)
            await mount.slew((ra_r + _PRE_FIX_STEP_HOURS) % 24.0, dec_r)
    assert mount.flips == 1, "this test is pointless unless the mount flipped"

    import astrodeck_native
    out = astrodeck_native.tppa_from_three(
        solves, {"latitude_deg": _LAT, "longitude_deg": _LON, "elevation_m": 0.0},
        {"arcsec_per_pixel": 1.55, "image_width_px": 64.0,
         "image_height_px": 48.0, "pressure_hpa": 0.0})
    err = out["error"]
    assert err["position_angle_spread_deg"] == pytest.approx(180.0, abs=0.1)
    assert err["total_arcmin"] == pytest.approx(_INJ_TOTAL, abs=0.5), err
    assert err["az_arcmin"] == pytest.approx(_INJ_AZ, abs=0.5), err
    assert err["alt_arcmin"] == pytest.approx(_INJ_ALT, abs=0.5), err


# ================================================== clean runs, both sides

@pytest.mark.parametrize("start_ha", [0.2, 0.69, 1.5, 3.0])
async def test_a_western_run_never_flips_and_recovers_the_injected_error(
        monkeypatch, tmp_path, start_ha):
    """Start west of the meridian (HA > 0) with the flip asymmetry ARMED, and the
    fixed driver must walk further west, never flip, and hand back the injected
    error. HA +0.69 is the exact starting hour angle of the 2026-08-06 run."""
    run = await _drive_run(monkeypatch, tmp_path, start_ha=start_ha)

    assert run.mount.flips == 0, (
        f"the mount flipped: hour angles {[round(h, 3) for h in run.hour_angles]}")
    assert all(ha > 0.0 for ha in run.hour_angles), run.hour_angles
    # It must move AWAY from the meridian, not merely stay on one side.
    assert run.hour_angles == sorted(run.hour_angles), run.hour_angles
    assert run.hour_angles[-1] - run.hour_angles[0] > 1.5, run.hour_angles
    # Three frames related by a pure RA rotation: same declination, same angle.
    assert max(run.declinations) - min(run.declinations) < 0.5, run.declinations
    assert run.pa_spread_deg < native_mod._PA_SPREAD_WARN_DEG, run.pa_spread_deg
    assert run.pa_spread_deg > 0.0, "the PA wobble vanished; this asserts nothing"

    st = run.state
    assert st["state"] != "error", st
    assert st["phase"] == "adjusting", st
    assert st["az_error"] == pytest.approx(_INJ_AZ, abs=0.5), st
    assert st["alt_error"] == pytest.approx(_INJ_ALT, abs=0.5), st
    assert st["total_error"] == pytest.approx(_INJ_TOTAL, abs=0.5), st
    assert "position_angle_spread_large" not in st.get("flags", []), st


@pytest.mark.parametrize("start_ha", [-0.2, -0.69, -1.5, -3.0])
async def test_an_eastern_run_never_flips_and_recovers_the_injected_error(
        monkeypatch, tmp_path, start_ha):
    """The mirror image. Start east of the meridian and the arc must keep going
    east; the recovered error is the injected one."""
    run = await _drive_run(monkeypatch, tmp_path, start_ha=start_ha)

    assert run.mount.flips == 0, (
        f"the mount flipped: hour angles {[round(h, 3) for h in run.hour_angles]}")
    assert all(ha < 0.0 for ha in run.hour_angles), run.hour_angles
    assert run.hour_angles == sorted(run.hour_angles, reverse=True), run.hour_angles
    assert run.hour_angles[0] - run.hour_angles[-1] > 1.5, run.hour_angles
    assert max(run.declinations) - min(run.declinations) < 0.5, run.declinations
    assert run.pa_spread_deg < native_mod._PA_SPREAD_WARN_DEG, run.pa_spread_deg

    st = run.state
    assert st["state"] != "error", st
    assert st["az_error"] == pytest.approx(_INJ_AZ, abs=0.5), st
    assert st["alt_error"] == pytest.approx(_INJ_ALT, abs=0.5), st


async def test_the_driver_slews_away_from_the_meridian_on_both_sides(
        monkeypatch, tmp_path):
    """Reads the two RA targets the driver actually COMMANDED, not just where
    the mount ended up -- so a mount that silently refused a slew could not make
    this pass."""
    west = await _drive_run(monkeypatch, tmp_path, start_ha=0.69)
    assert len(west.mount.slew_log) == 2, west.mount.slew_log
    for target_ra, _dec in west.mount.slew_log:
        assert hour_angle_h(target_ra, _LON) > 0.0, west.mount.slew_log

    east = await _drive_run(monkeypatch, tmp_path, start_ha=-0.69)
    assert len(east.mount.slew_log) == 2, east.mount.slew_log
    for target_ra, _dec in east.mount.slew_log:
        assert hour_angle_h(target_ra, _LON) < 0.0, east.mount.slew_log


# ======================================== the regression: the pre-fix driver

async def test_the_pre_fix_step_reproduces_the_2026_08_06_rig_failure(
        monkeypatch, tmp_path):
    """THE test. Restore the old unconditional 'step RA up' and drive the real
    ``run_native`` from a western start, and the shipped failure comes back.

    Every claim below is one the rig log made on 2026-08-06 (LST 19.43h):
    hour angles +0.69 -> -0.12 -> -0.92, a flip between point 1 and point 2, a
    position-angle spread of 179.7 degrees, an axis fitted below the horizon and
    7271 arcminutes published as polar error.
    """
    run = await _drive_run(monkeypatch, tmp_path, start_ha=0.69,
                           flip_dec_error_deg=-3.0, pre_fix_step=True)

    has = run.hour_angles
    assert run.mount.flips == 1, f"the old step did not cross the meridian: {has}"
    # The rig's own numbers, to a hundredth of an hour.
    assert has[0] == pytest.approx(0.69, abs=0.02), has
    assert has[1] == pytest.approx(-0.12, abs=0.02), has
    assert has[0] > 0.0 > has[1], f"the arc must cross the meridian: {has}"

    # WHAT CHANGED 2026-08-06. The rig's third point (-0.92h), the 179.7 degree
    # position-angle spread, the axis fitted below the horizon and the 7271
    # arcminutes are all still in the log of that night — and are now
    # UNREACHABLE, because the driver compares consecutive position angles and
    # stops at the frame that reveals the flip. Two solves, one rotation, and a
    # refusal that names the cause, instead of a full arc spent measuring a
    # mount that had swung over.
    assert len(run.solver.results) == 2, run.solver.results
    st = run.state
    assert st["state"] == "error", st
    msg = st["message"].lower()
    assert "camera angle moved" in msg, st["message"]
    assert "changed sides of the pier" in msg, st["message"]
    # and it still says which frames, so the log is readable afterwards
    assert "point 1" in msg and "point 2" in msg, st["message"]


@pytest.mark.parametrize("flip_dec_error_deg", [-1.0, -0.5, 0.5, 1.0, 2.0])
async def test_the_pre_fix_step_wrecks_the_recovered_error(
        monkeypatch, tmp_path, flip_dec_error_deg):
    """The same regression across a range of pier-side asymmetries, asserting on
    the NUMBER rather than on the refusal: whatever the engine reports after a
    mid-measurement flip bears no relation to the injected 7.8 arcminutes.

    Most of these cases land INSIDE the plausibility gate (9 to 23 degrees of
    "polar error") and are published to the operator as an instruction rather
    than refused — carrying the position-angle caveat that says not to trust it;
    see the characterisation test at the end of this file."""
    run = await _drive_run(monkeypatch, tmp_path, start_ha=0.69,
                           flip_dec_error_deg=flip_dec_error_deg,
                           pre_fix_step=True)
    assert run.mount.flips == 1, run.hour_angles

    # NO NUMBER IS PRODUCED AT ALL ANY MORE, at any asymmetry. This test used to
    # assert that whatever came out was wildly wrong, and had to special-case the
    # subset that the plausibility gate happened to refuse — because the rest
    # were PUBLISHED, as an instruction, to an operator with no way to know. The
    # flip is now caught between the two frames that show it, so every one of
    # these ends the same way regardless of how bad the collapsed fit would have
    # been. That is the point: the size of the lie should not decide whether you
    # are told one.
    st = run.state
    assert st["state"] == "error", st
    assert "changed sides of the pier" in st["message"].lower(), st
    # 0.0 is the session's seeded default, i.e. no fit was ever published. The
    # point is that the operator is never handed a non-zero "polar error" to act
    # on from an arc that flipped.
    assert st.get("total_error") == 0.0, (
        f"a refused run must not also leave a number on screen: {st}")
    assert st.get("phase") == "measuring", "it stopped in the arc, not after it"


async def test_the_same_start_is_clean_once_the_step_direction_is_fixed(
        monkeypatch, tmp_path):
    """A/B on one variable. Same site, same start (HA +0.69), same injected
    error, same armed pier asymmetry -- the ONLY difference is which
    ``_ra_step_hours`` is in place. Old: flip, refused fit. New: no flip, the
    injected error back."""
    broken = await _drive_run(monkeypatch, tmp_path, start_ha=0.69,
                              pre_fix_step=True)
    fixed = await _drive_run(monkeypatch, tmp_path, start_ha=0.69,
                             pre_fix_step=False)
    assert (broken.mount.flips, fixed.mount.flips) == (1, 0)
    assert broken.state["state"] == "error", broken.state
    assert len(broken.solver.results) == 2, "the broken half must stop AT the flip"
    assert len(fixed.solver.results) >= 3, "the clean half must complete the arc"
    assert fixed.state["state"] != "error", fixed.state
    assert fixed.state["total_error"] == pytest.approx(_INJ_TOTAL, abs=0.5), \
        fixed.state


# ============================================ tracking, before the first frame

async def test_the_driver_starts_tracking_before_it_measures(
        monkeypatch, tmp_path):
    """A mount handed over with tracking OFF is started, not measured as-is.

    ``park`` and ``find_home`` both leave a real mount stopped, which is exactly
    the state someone reaching for Align has it in. With the sky sliding out
    from under a stopped mount, ``tppa_update`` attributes the drift to the
    operator's knobs and the live error grows without bound — and the update's
    quality flags are frozen from the initial fit, so nothing fires.

    Both directions are asserted, because "always call set_tracking" would pass
    the first half and is not the behaviour: a mount already tracking must be
    left alone."""
    stopped = await _drive_run(monkeypatch, tmp_path, start_ha=0.69,
                               tracking=False)
    assert stopped.mount.tracking_calls == [True], stopped.mount.tracking_calls
    assert stopped.mount.tracking is True
    # And the run it enabled is a good one.
    assert stopped.state["state"] != "error", stopped.state
    assert stopped.state["total_error"] == pytest.approx(_INJ_TOTAL, abs=0.5), \
        stopped.state

    running = await _drive_run(monkeypatch, tmp_path, start_ha=0.69)
    assert running.mount.tracking_calls == [], running.mount.tracking_calls


# ================================================== characterisation

async def test_a_flip_inside_the_plausible_band_is_refused_not_captioned(
        monkeypatch, tmp_path):
    """CHARACTERISATION. Was: "a mid-measurement pier flip is published as an
    actionable polar error when the collapsed fit lands under 30 degrees",
    asserted as a defect that ``_reject_implausible_fit`` should also refuse on
    a large position-angle spread. REFUTED on two counts.

    First, reachability. Every path into this state runs through a measurement
    arc that CROSSES the meridian, and the shipped ``_ra_step_hours`` always
    steps away from it — which is why this test has to monkeypatch the pre-fix
    step back in to produce a flip at all. The A/B directly above
    (``test_the_same_start_is_clean_once_the_step_direction_is_fixed``) is the
    evidence: same start, same armed asymmetry, no flip.

    Second, the remedy. Refusing on spread alone would refuse every run on a rig
    with a rotator that stepped between frames, and a mount that flips for
    unrelated reasons (a manual flip between two legitimate runs) has already
    been caught by ``_refuse_if_it_did_not_arrive`` — which grades what the sky
    did against what was commanded, rather than inferring it from a camera angle.

    What IS the product's promise here, and what this pins, is that the flip is
    not silent: the payload carries ``position_angle_spread_deg`` as a NUMBER and
    ``position_angle_spread_large`` as a flag, both of which the UI renders as a
    "this fit is not trustworthy" caveat over the number. Take either away and
    the operator is left with 300 arcminutes and nothing to doubt it with.
    """
    run = await _drive_run(monkeypatch, tmp_path, start_ha=0.69,
                           flip_dec_error_deg=-0.2, pre_fix_step=True)
    assert run.mount.flips == 1, run.hour_angles

    # WHAT CHANGED 2026-08-06, and why the old answer was not good enough.
    #
    # This used to be a small flip displacement (-0.2 deg) chosen so the
    # collapsed fit landed INSIDE MAX_PLAUSIBLE_ERROR_DEG — around 300
    # arcminutes, comfortably under the 30 degree bound. The magnitude gate
    # therefore could not see it, the number was published as "adjust the
    # mount", and the only thing standing between the operator and a wrecked
    # alignment was a flag on the payload that they had to notice and interpret.
    #
    # On the night of 2026-08-06 that is precisely what happened, twice, and the
    # caveat did not save it: the operator turned bolts against those numbers
    # until the mount was degrees out. A caveat beside a confident figure is not
    # a refusal, and the size of the collapsed fit was never a good reason to
    # decide whether to hand one over.
    #
    # So the run no longer gets that far. The flip is caught between the two
    # frames that reveal it, whatever it would have fitted to.
    st = run.state
    assert st["state"] == "error", st
    assert "changed sides of the pier" in st["message"].lower(), st
    assert st.get("total_error") == 0.0, (
        f"a flip inside the plausible band must still not produce a number: {st}")
    assert len(run.solver.results) == 2, run.solver.results
