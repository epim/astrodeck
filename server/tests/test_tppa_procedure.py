"""The native TPPA measurement PROCEDURE, driven against controllable fakes.

``polar/native.py`` is the one loop in the system that commits a telescope to an
irreversible 24 degree arc on the strength of three plate solves, and then hands
the operator a number to turn a bolt by. Two of its failures are already on the
record: a run at Dec +85 that reported 580 arcmin of error on a working
alignment (2026-08-03), and a run that walked across the meridian, pier-flipped,
and reported 7271 arcmin (2026-08-06). Both were REPORTED-CONFIDENTLY-WRONG, not
crashes, and neither was visible from inside the fit: three points determine a
plane exactly, so there are no residuals and nothing internal ever notices a bad
input.

Existing coverage is deliberately not repeated here:

  * ``test_polar_meridian_guard.py`` walks ``_ra_step_hours`` and
    ``_reject_implausible_fit`` as PURE HELPERS. This file drives ``_drive``, the
    loop that has to actually call them — a right answer wired into a function
    nobody calls is the #112 autofocus bug.
  * ``test_polar_pole_guard.py`` covers the MOUNT-CLAIMED pole refusal. The
    authoritative SOLVED-position refusal, one exposure later, is covered here.
  * ``test_polar_pause.py`` owns pause/resume; ``test_polar_native.py`` owns the
    end-to-end sim recovery (and needs the Rust wheel).

The stubs below are in the ``test_polar_pause.py`` house style — a ``_Hub`` /
``_Tel`` / ``_Cam`` triple plus a swappable engine, with the REAL
``PolarAlignSession`` so ``_publish`` is production code. What is new is that the
telescope can MISBEHAVE the way the AM5 has been observed to: refuse a goto with
``reply 'e3'``, or accept one and silently not arrive.
"""
from __future__ import annotations

import contextlib
import math
import time
import types

import numpy as np
import pytest

from astrodeck.catalog.coords import altaz, lst_hours
from astrodeck.devices.base import DeviceError
from astrodeck.polar import native as nat
from astrodeck.polar.session import PolarAlignSession
from astrodeck.sequence.schedule import hour_angle_h

# An invented northern site. The driver reads longitude to choose the RA step
# direction and latitude to sanity-check the fitted axis against the horizon, so
# both have to be real numbers well away from the equator.
_LAT = 45.0
_LON = -122.0
#: Declination of the pretend target: 50 degrees from the pole, so the pole guard
#: lets every run below through, AND high enough that the whole 24 degree arc
#: clears ``MIN_MEASUREMENT_ALT_DEG`` at every hour angle these tests start from
#: (the worst case is |HA| 5h stepping out to 6.6h, which lands at +21.7 deg).
#: The altitude refusal is a real guard on real geometry, so the fakes have to
#: model a mount pointing somewhere a real operator could have pointed it —
#: disabling the guard would stop grading the meridian property it protects.
_DEC = 40.0
#: A deliberately LOW target, used only by the test that needs the arc to head
#: for the ground: at lat 45, Dec +20, HA +6.5h the tube sits at +8.9 deg.
_DEC_LOW = 20.0

_REAL_ENGINE = nat._native
requires_engine = pytest.mark.skipif(
    not nat.NATIVE_AVAILABLE, reason="astrodeck_native wheel not installed")


def _ra_at_hour_angle(ha_hours: float) -> float:
    """The RA a target must have RIGHT NOW to sit at hour angle ``ha_hours``.

    Inverted against the live clock rather than hard-coded, so these tests assert
    on the geometry they mean instead of on whatever LST CI happens to run at."""
    return (lst_hours(_LON) - ha_hours) % 24.0


# --------------------------------------------------------------------- doubles

class _Frame:
    def __init__(self, timestamp: float):
        self.timestamp = timestamp


class _Solve:
    """A successful plate solve. ``rotation_deg`` may be None — that is the case
    ``native.py`` guards with ``result.rotation_deg or 0.0``."""
    success = True
    message = "solved"
    pixel_scale_arcsec = 1.55

    def __init__(self, ra_hours: float, dec_deg: float, rotation_deg):
        self.ra_hours = ra_hours
        self.dec_deg = dec_deg
        self.rotation_deg = rotation_deg


class _Cam:
    name = "cam"


class _Tel:
    """A mount that can misbehave the two ways the real AM5 has been seen to."""

    def __init__(self, hub, ra_hours: float, dec_deg: float):
        self._hub = hub
        self.ra = ra_hours
        self.dec = dec_deg
        #: 1-based slew ordinals that are REFUSED (``:MS#`` replied 'e3').
        self.reject_slew: set[int] = set()
        #: from this 1-based ordinal on, accept the goto and never arrive.
        self.deaf_from: int | None = None
        #: A real mount reports and controls sidereal tracking, and ``_drive``
        #: calls ``_ensure_tracking`` before the first frame. Defaulting to True
        #: means that call is a no-op here; the tracking-off path is graded in
        #: ``test_tppa_adjust_and_report.py``, which owns it. A fake WITHOUT
        #: these would send _ensure_tracking down its "mount that cannot say"
        #: exception path, so this loop would never touch the real one.
        self.tracking = True

    async def get_position(self):
        return self.ra, self.dec

    async def get_tracking(self):
        return self.tracking

    async def set_tracking(self, on):
        self.tracking = bool(on)

    async def slew(self, ra_hours, dec_deg):
        self._hub.events.append("slew")
        self._hub.slews.append((ra_hours, dec_deg))
        n = len(self._hub.slews)
        if n in self.reject_slew:
            raise DeviceError("mount: goto rejected (reply 'e3')")
        if self.deaf_from is not None and n >= self.deaf_from:
            return                      # accepted, settled instantly, never moved
        self.ra, self.dec = ra_hours % 24.0, dec_deg


class _Hub:
    mode = "sim"

    def __init__(self, ra_hours: float, dec_deg: float):
        self._motion_epoch = 0
        self.events: list[str] = []     # ordered: camera / capture / slew
        self.slews: list[tuple[float, float]] = []
        self.solar_checks: list[tuple[float, float]] = []
        self.solar_raises_on: int | None = None
        self.camera_yields: list[str] = []
        self.site = {"latitude": _LAT, "longitude": _LON, "elevation_m": 0.0,
                     "is_default": False}
        self.tel = _Tel(self, ra_hours, dec_deg)

    def require(self, role):
        return self.tel if role == "telescope" else _Cam()

    async def yield_camera_for(self, what):
        self.events.append("camera")
        self.camera_yields.append(what)
        return True

    def _check_solar(self, ra_hours, dec_deg):
        self.solar_checks.append((ra_hours, dec_deg))
        if self.solar_raises_on == len(self.solar_checks):
            raise DeviceError(
                "target is within 3 deg of the Sun (exclusion 30 deg)")

    def effective_optics(self):
        return {"fov_h_deg": 1.2, "image_scale_arcsec_px": 1.55}


class _Session(PolarAlignSession):
    """The REAL session, with the raw driver kwargs recorded on the side."""

    def __init__(self, hub):
        super().__init__(hub)
        self.published: list[dict] = []

    def _publish(self, **kw):
        self.published.append(dict(kw))
        super()._publish(**kw)


#: A small, entirely plausible fit: 0.4 arcmin total, so ``_reject_implausible_fit``
#: passes and the first live update crosses the 1.0 arcmin "aligned" gate — every
#: run that gets that far terminates on its own instead of spinning 240 times.
_CLEAN_ERR = {"az_arcmin": 0.24, "alt_arcmin": 0.32, "total_arcmin": 0.4,
              "az_direction": "left_west", "alt_direction": "up",
              "flags": [], "position_angle_spread_deg": 0.0}


class _FakeEngine:
    """Stands in for the Rust wheel, and RECORDS what the driver handed it — the
    solves are the only place the driver's ``or 0.0`` position-angle coercion is
    observable."""

    def __init__(self, error: dict | None = None):
        self.error = dict(error or _CLEAN_ERR)
        self.from_three_calls: list[tuple[list, dict, dict]] = []
        self.update_calls: list[dict] = []
        self.update_raises_first = 0

    def tppa_from_three(self, solves, site, opts):
        self.from_three_calls.append(([dict(s) for s in solves], dict(site),
                                      dict(opts)))
        return {"model": {"fake": True}, "error": dict(self.error)}

    def tppa_update(self, model, solve):
        self.update_calls.append(dict(solve))
        if len(self.update_calls) <= self.update_raises_first:
            raise ValueError("degenerate correction line")
        return dict(self.error)


class _Rig:
    """One stubbed native run: a hub, a real session, and a capture/solve stand-in
    that reports WHERE THE MOUNT ACTUALLY IS — so a mount that did not arrive
    produces a duplicate measurement point, exactly as a real solver would."""

    def __init__(self, hub: _Hub):
        self.hub = hub
        self.session = _Session(hub)
        self.solved: list[tuple[float, float]] = []
        self.fail_solve_on: set[int] = set()
        self.rotation: float | None = 0.0
        self.rotation_by_point: dict[int, float | None] = {}
        self.solved_dec: float | None = None      # override what the SKY says
        self.bump_epoch_after: int | None = None
        self.engine: _FakeEngine | None = None
        self._t0 = time.time()

    async def capture(self, hub, solver):
        n = len(self.solved) + 1
        hub.events.append("capture")
        ra, dec = await hub.tel.get_position()
        self.solved.append((ra, dec))
        if self.bump_epoch_after == n:
            hub._motion_epoch += 1
        if n in self.fail_solve_on:
            raise DeviceError("polar plate solve failed: not enough stars")
        rot = self.rotation_by_point.get(n, self.rotation)
        solved_dec = self.solved_dec if self.solved_dec is not None else dec
        return (_Frame(self._t0 + 10.0 * n), _Solve(ra, solved_dec, rot),
                (1.55, 1024.0, 768.0))

    async def run(self):
        await nat.run_native(self.session, self.hub)

    @property
    def messages(self) -> list[str]:
        return [p.get("message", "") for p in self.session.published]

    def measured_hour_angles(self) -> list[float]:
        return [hour_angle_h(ra, _LON) for ra, _dec in self.solved[:3]]


@pytest.fixture
def make_rig(monkeypatch):
    """Build the one rig this test drives. ``engine="real"`` uses the Rust wheel
    (mark the test ``@requires_engine``); the default fake keeps every test that
    does not depend on the fit's arithmetic runnable on a wheel-less runner."""
    monkeypatch.setattr(nat, "NATIVE_AVAILABLE", True)
    monkeypatch.setattr(nat, "_ADJUST_INTERVAL_S", 0.01)
    import astrodeck.providers as _pv
    monkeypatch.setattr(_pv, "pick_solver", lambda hub: object())

    def _make(start_ha: float = -2.0, dec: float = _DEC, engine: str = "fake"):
        hub = _Hub(_ra_at_hour_angle(start_ha), dec)
        rig = _Rig(hub)
        monkeypatch.setattr(nat, "_capture_and_solve", rig.capture)
        if engine == "real":
            monkeypatch.setattr(nat, "_native", _REAL_ENGINE)
        else:
            rig.engine = _FakeEngine()
            monkeypatch.setattr(nat, "_native", rig.engine)
        return rig

    return _make


# ------------------------------------------- the signed step, IN THE DRIVE LOOP

@pytest.mark.parametrize("start_ha", [0.5, 2.0, 5.0, -0.5, -2.0, -5.0])
async def test_the_measured_arc_never_crosses_the_meridian(make_rig, start_ha):
    """``_ra_step_hours`` gets the sign right in isolation (covered elsewhere).
    This asserts the LOOP actually uses it: run ``_drive`` and look at the hour
    angles of the three points it really measured.

    The 2026-08-06 run went HA +0.69h, -0.12h, -0.92h — the sign change between
    point 1 and point 2 IS the pier flip that produced 7271 arcmin."""
    rig = make_rig(start_ha=start_ha)
    await rig.run()

    assert len(rig.solved) >= 3, f"precondition: never took three points: {rig.solved}"
    assert len(rig.hub.slews) == 2, rig.hub.slews
    has = rig.measured_hour_angles()
    signs = {math.copysign(1.0, h) for h in has}
    assert len(signs) == 1, f"the arc crossed the meridian: {[round(h, 3) for h in has]}"
    # AWAY from the meridian, monotonically — a step of zero, or a step toward
    # the meridian that stopped just short, would satisfy the sign check alone.
    assert abs(has[1]) > abs(has[0]) and abs(has[2]) > abs(has[1]), has
    assert abs(has[2] - has[0]) == pytest.approx(2 * nat._RA_STEP_HOURS, abs=1e-6), has


async def test_the_rotation_keeps_the_declination_it_was_given(make_rig):
    """TPPA is a pure RA rotation. A slew that also moved in Dec would make the
    three frames trace something other than the small circle the fit assumes."""
    rig = make_rig(start_ha=-2.0)
    await rig.run()
    assert len(rig.hub.slews) == 2, rig.hub.slews
    assert [dec for _ra, dec in rig.hub.slews] == [_DEC, _DEC], rig.hub.slews


# ----------------------------------------------------------------- ALTITUDE

@pytest.mark.parametrize("start_ha", [1.0, 3.0, 5.0])
async def test_a_western_arc_always_walks_the_tube_downward(make_rig, start_ha):
    """Not a defect by itself — the MECHANISM behind the one below.

    Since the meridian fix, west of the meridian means "step further west", and
    west of the meridian every further step is a step toward the horizon. So a
    western run's altitude loss is not bad luck, it is guaranteed by the rule."""
    rig = make_rig(start_ha=start_ha)
    await rig.run()
    assert len(rig.solved) >= 3, rig.solved
    alts = [altaz(ra, dec, _LAT, _LON)[0] for ra, dec in rig.solved[:3]]
    assert alts[0] > alts[1] > alts[2], alts
    assert alts[0] - alts[2] > 3.0, alts


async def test_the_arc_must_not_walk_the_tube_below_the_horizon(make_rig):
    """A run that starts low in the WEST is walked underground by its own step
    rule, and nothing checks.

    Concretely, at latitude 45 with the tube at Dec +20 and hour angle +6.5h the
    altitude is +8.9 degrees — low, but a legal place to be pointing, and TPPA
    measures from wherever the mount already is. The two 12 degree steps take it
    to +1.1 and then to -6.1: the third measurement point is SIX DEGREES BELOW
    THE HORIZON and the driver commands the mount there.

    ``_rotate_in_ra`` gates on ``hub._check_solar`` and on the motion fence, but
    ``hub._check_horizon`` is never called from any polar path (it has exactly
    one caller in the tree, the user-initiated GOTO in api/app.py), and no code
    anywhere asks whether a MEASUREMENT point is high enough to mean anything."""
    rig = make_rig(start_ha=6.5, dec=_DEC_LOW)
    start_alt, _az = altaz(rig.hub.tel.ra, _DEC_LOW, _LAT, _LON)
    assert 5.0 < start_alt < 15.0, \
        f"precondition: a low but legal start; got alt {start_alt:.1f} deg"

    await rig.run()

    assert len(rig.solved) >= 1, "precondition: the loop never ran"
    commanded = [altaz(ra, dec, _LAT, _LON)[0] for ra, dec in rig.hub.slews]
    below = [round(a, 1) for a in commanded if a <= 0.0]
    assert not below, (
        f"started at alt {start_alt:.1f} deg west of the meridian and commanded "
        f"the mount to altitudes {[round(a, 1) for a in commanded]} — "
        f"{below} is underground. Nothing in the polar path calls _check_horizon.")
    # A driver that refused instead of slewing is the right answer, but it has to
    # SAY so — a silent stop after one frame is its own failure.
    if len(rig.hub.slews) < 2:
        st = rig.session.state
        assert st["state"] == "error", st
        assert "horizon" in st["message"].lower(), st


# ------------------------------------------------------------- the sun cone

async def test_the_sun_cone_is_checked_before_the_mount_moves(make_rig):
    rig = make_rig(start_ha=-2.0)
    rig.hub.solar_raises_on = 1
    await rig.run()
    assert rig.hub.solar_checks, "precondition: the cone was never consulted"
    assert rig.hub.slews == [], "slewed toward the Sun before checking the cone"
    st = rig.session.state
    assert st["state"] == "error", st
    assert "sun" in st["message"].lower(), st


async def test_the_sun_cone_is_checked_before_EVERY_rotation(make_rig):
    """Not just the first. The arc spans 24 degrees, so a start outside the cone
    says nothing about where point 3 lands — and the Sun is the one thing on this
    list that destroys the camera rather than the measurement."""
    rig = make_rig(start_ha=-2.0)
    rig.hub.solar_raises_on = 2
    await rig.run()
    assert len(rig.hub.solar_checks) == 2, \
        f"the cone was consulted {len(rig.hub.solar_checks)}x for 2 rotations"
    assert len(rig.hub.slews) == 1, rig.hub.slews
    st = rig.session.state
    assert st["state"] == "error", st
    assert "sun" in st["message"].lower(), st


async def test_the_cone_is_checked_against_the_target_not_the_current_pointing(make_rig):
    """Checking where the mount IS would pass a rotation that ends inside the
    cone, which is the only rotation that matters."""
    rig = make_rig(start_ha=-2.0)
    await rig.run()
    assert len(rig.hub.solar_checks) == 2, rig.hub.solar_checks
    assert len(rig.hub.slews) == 2, rig.hub.slews
    assert [ra for ra, _d in rig.hub.solar_checks] == \
        [ra for ra, _d in rig.hub.slews], (rig.hub.solar_checks, rig.hub.slews)


# ----------------------------------------------------- a mount that says no

@pytest.mark.parametrize("ordinal", [1, 2])
async def test_a_refused_goto_stops_the_run_instead_of_measuring_twice(make_rig, ordinal):
    """The real AM5 answers a goto it cannot honour with ``:MS#`` reply 'e3' and
    the driver raises. The loop must not shrug and take another frame at the
    point it already measured."""
    rig = make_rig(start_ha=-2.0)
    rig.hub.tel.reject_slew = {ordinal}
    await rig.run()

    assert len(rig.hub.slews) == ordinal, rig.hub.slews
    assert len(rig.solved) == ordinal, \
        f"measured {len(rig.solved)} points off {ordinal} successful rotations"
    st = rig.session.state
    assert st["state"] == "error", st
    assert "e3" in st["message"], st


@requires_engine
async def test_a_mount_that_never_moves_at_all_is_caught(make_rig):
    """The control for the test below: when NO rotation lands, all three solves
    coincide and the Rust engine refuses outright with "mount did not move
    between points". That refusal is the reason the next case is a surprise."""
    rig = make_rig(start_ha=-2.0, engine="real")
    rig.hub.tel.deaf_from = 1
    await rig.run()

    ras = [ra for ra, _d in rig.solved[:3]]
    assert len(set(ras)) == 1, f"precondition: the mount was supposed to be stuck: {ras}"
    st = rig.session.state
    assert st["state"] == "error", st
    assert "did not move" in st["message"].lower(), st


@requires_engine
async def test_a_mount_that_stops_arriving_halfway_is_not_reported_as_aligned(
        make_rig, bus_lines):
    """A goto the mount ACCEPTS and does not honour — no exception, ``slew``
    settles at once because the coordinate delta is zero — leaves points 2 and 3
    on top of each other.

    Three points where two coincide do not determine a plane. The engine refuses
    only the all-three-identical case (see the test above); this one it fits,
    returning ``total_arcmin`` 0.0 with no flags. ``_reject_implausible_fit``
    waves that through, because zero error is extremely plausible.

    Two things happen from there, and which one you get is a numerical coin toss
    on a fit whose axis is arbitrary — both observed on this exact input:

      * usually the first live update also returns ~0 and the run publishes
        ``state:"done"`` / "polar aligned"; or
      * the update raises "degenerate geometry" every time, so the loop burns all
        240 iterations — 240 shutter actuations, four minutes on a real rig — and
        then publishes ``state:"done"`` / "alignment session ended" anyway.

    Either way the operator is told the alignment finished. This is the
    2026-08-06 failure with the sign flipped: not a confident huge number but a
    confident PERFECT one, from a measurement that was never made. The driver is
    the only layer that CAN see it, because it is the only layer that knows it
    commanded a rotation between those two frames."""
    rig = make_rig(start_ha=-2.0, engine="real")
    rig.hub.tel.deaf_from = 2           # first rotation lands, second does not
    await rig.run()

    ra1, ra2, ra3 = [ra for ra, _d in rig.solved[:3]]
    assert ra1 != ra2, f"precondition: the first rotation had to land: {rig.solved}"
    assert ra2 == ra3, f"precondition: the second rotation had to be ignored: {rig.solved}"

    st = rig.session.state
    assert st["state"] == "error", (
        "the mount was commanded 12 deg and never arrived, so points 2 and 3 are "
        f"the same place ({ra2:.4f}h), and the run ended {st['state']!r} / "
        f"{st['message']!r} with total_error {st.get('total_error')} arcmin. "
        f"Nothing compared the solved positions against the rotation it had just "
        f"commanded. Log: {[m for _l, m, _s in bus_lines][-2:]}")


# --------------------------------------------------------- a solve that fails

@pytest.mark.parametrize("point", [1, 2, 3])
async def test_a_failed_solve_carries_the_point_it_reached(make_rig, point):
    """CHARACTERISATION. Claim: "a failed plate solve names neither the
    measurement point nor where it left the mount" — REFUTED, first premise. The
    ``polar`` event is a MERGED dict, not a fresh one, so the terminal error
    inherits ``phase``/``point_index``/``progress`` from the last measuring
    publish: the consumer is told exactly how far the run got. The message text
    is the solver's own, and does not need to repeat what the payload carries.

    Pinned here so a future refactor that starts publishing a fresh dict — or
    stops publishing ``point_index`` — is caught, since nothing else in the
    suite grades the merge."""
    rig = make_rig(start_ha=-2.0)
    rig.fail_solve_on = {point}
    await rig.run()

    assert len(rig.solved) == point, \
        f"precondition: the loop had to reach point {point}: {rig.solved}"
    st = rig.session.state
    assert st["state"] == "error", st
    assert st["source"] == "native", st
    assert st["message"] == "polar plate solve failed: not enough stars", st
    # The phase survives the merge, so a consumer can tell a measuring failure
    # from an adjusting one without parsing prose.
    assert st["phase"] == "measuring", st
    if point == 1:
        # Nothing was measured, so there is no point index to inherit — the
        # absence is itself the fact ("no point completed").
        assert "point_index" not in st, st
        assert st["progress"] == pytest.approx(0.0), st
    else:
        # 0-based index of the last point that DID land, i.e. point-1 of 3.
        assert st["point_index"] == point - 2, st
        assert st["progress"] == pytest.approx(0.1 + 0.15 * (point - 1)), st


async def test_a_failed_solve_leaves_the_mounts_position_in_the_log(make_rig, bus_lines):
    """CHARACTERISATION. Same claim, second premise — "it does not say where it
    left the mount" — REFUTED: ``_log_measurement`` writes every solved point
    (RA/Dec/HA) and ``_rotate_in_ra`` logs every commanded target, so the last
    "rotating RA to Xh" line IS where the tube was left, to 0.01h.

    The proposal to repeat it in the terminal message was rejected; this pins
    the channel that actually carries it, so deleting those log lines fails."""
    rig = make_rig(start_ha=-2.0)
    rig.fail_solve_on = {3}
    await rig.run()

    assert len(rig.hub.slews) == 2, \
        f"precondition: two rotations had to have happened: {rig.hub.slews}"
    msgs = [m for _lvl, m, _src in bus_lines]
    points = [m for m in msgs if "native TPPA point" in m]
    assert len(points) == 2, points
    for n, m in enumerate(points, start=1):
        assert f"point {n}/3" in m, m
        assert "RA " in m and "Dec " in m and "HA " in m, m
    rotations = [m for m in msgs if "rotating RA to" in m]
    assert len(rotations) == 2, rotations
    left_at, _dec = rig.hub.slews[-1]
    assert f"rotating RA to {left_at:.2f}h" in rotations[-1], (rotations, left_at)
    assert any("plate solve failed" in m for _l, m, _s in bus_lines
               if _l == "error"), msgs


# ------------------------------------------------------------- the pole guard

async def test_a_solved_position_near_the_pole_stops_the_run_after_one_frame(make_rig):
    """The mount's claim is checked first and cheaply; the SOLVED position is the
    authoritative one, because this mount has been observed 50 degrees out after
    a restart. Nothing else drives that second check through ``_drive``."""
    rig = make_rig(start_ha=-2.0)
    assert 90.0 - abs(_DEC) > nat.MIN_POLE_DISTANCE_DEG, \
        "precondition: the MOUNT's claim has to pass, so only the solve can refuse"
    rig.solved_dec = 86.0               # the sky says 4 degrees from the pole
    await rig.run()

    assert len(rig.solved) == 1, f"kept measuring past the refusal: {rig.solved}"
    assert rig.hub.slews == [], "rotated a mount it had already decided not to measure"
    st = rig.session.state
    assert st["state"] == "error", st
    assert "pole" in st["message"].lower(), st
    assert "plate solve puts you" in st["message"], \
        f"must say the SKY refused, not the mount: {st['message']!r}"


# ------------------------------------------------------------ the motion fence

async def test_the_motion_fence_is_re_read_before_the_second_rotation(make_rig):
    """A STOP / park / deadman / safety abort bumps ``_motion_epoch``. Reading it
    once at the top would let the loop commit one more 12 degree slew to a mount
    somebody had just halted."""
    rig = make_rig(start_ha=-2.0)
    rig.bump_epoch_after = 2            # the halt lands during the second solve
    await rig.run()

    assert len(rig.solved) == 2, rig.solved
    assert len(rig.hub.slews) == 1, \
        f"kept slewing a mount someone had just halted: {rig.hub.slews}"
    st = rig.session.state
    assert st["state"] == "error", st
    assert "fenced" in st["message"] or "motion" in st["message"], st


async def test_a_halt_during_the_first_frame_stops_before_the_second(make_rig):
    """The fence is a monotonic counter, not a boolean, so a session that starts
    at a non-zero epoch must still notice its own bump."""
    rig = make_rig(start_ha=-2.0)
    rig.hub._motion_epoch = 7           # ...an earlier abort already happened
    rig.bump_epoch_after = 1            # the halt lands during frame 1
    await rig.run()
    assert len(rig.solved) == 1, rig.solved
    assert rig.hub.slews == [], rig.hub.slews
    assert rig.session.state["state"] == "error", rig.session.state


# ------------------------------------------------------------ the camera lease

async def test_the_camera_is_taken_before_the_first_exposure_and_the_first_slew(make_rig):
    """TPPA is a camera-owning MOUNT-MOTION path: losing the camera at point 2
    does not merely fail, it abandons the tube 12 degrees from where the user
    pointed it. So the lease is taken up front, exactly once."""
    rig = make_rig(start_ha=-2.0)
    await rig.run()

    ev = rig.hub.events
    assert "capture" in ev and "slew" in ev, f"precondition: the run never ran: {ev}"
    assert ev.count("camera") == 1, ev
    assert ev[0] == "camera", ev
    assert ev.index("camera") < ev.index("capture") < ev.index("slew"), ev
    assert rig.hub.camera_yields == ["polar alignment"], rig.hub.camera_yields


async def test_a_run_the_pole_guard_refuses_never_takes_the_camera(make_rig):
    """Deliberate ordering: both the solver pick and the mount-side pole refusal
    can end the run before a single exposure, and amputating someone's Live View
    for a run that was about to be refused anyway is its own small betrayal."""
    rig = make_rig(start_ha=-2.0, dec=89.5)
    await rig.run()

    assert rig.hub.camera_yields == [], rig.hub.camera_yields
    assert rig.hub.events == [], rig.hub.events
    assert rig.solved == [], rig.solved
    st = rig.session.state
    assert st["state"] == "error", st
    assert "pole" in st["message"].lower(), st


# --------------------------------------------------- the exposure guard itself

class _GuardCam:
    name = "cam"

    def __init__(self, events):
        self.events = events
        self.exposures: list[tuple] = []

    async def expose(self, exposure_s, gain, offset, binning=1):
        self.events.append("expose")
        self.exposures.append((exposure_s, gain, offset, binning))
        return types.SimpleNamespace(
            data=np.zeros((768, 1024), dtype="uint16"),
            timestamp=1_700_000_000.0, exposure_s=exposure_s)


class _GuardHub:
    """Only what the REAL ``_capture_and_solve`` touches."""

    def __init__(self):
        self.events: list[str] = []
        self.cam = _GuardCam(self.events)
        self.tel = _Tel(self, _ra_at_hour_angle(-2.0), _DEC)
        self.slews: list = []
        self.enters = 0
        self.exits = 0

    def require(self, role):
        return self.tel if role == "telescope" else self.cam

    async def from_mount_frame(self, tel, ra_hours, dec_deg):
        return ra_hours, dec_deg

    async def _publish_preview(self, frame):
        self.events.append("preview")

    def effective_optics(self):
        return {"fov_h_deg": 1.2, "image_scale_arcsec_px": 1.55}

    @contextlib.asynccontextmanager
    async def exposure_guard(self, label):
        self.enters += 1
        self.events.append("enter")
        try:
            yield
        finally:
            self.exits += 1
            self.events.append("exit")


class _GuardSolver:
    def __init__(self, result):
        self.result = result
        self.calls: list[tuple] = []

    async def solve(self, path, *, ra_hint=None, dec_hint=None, fov_deg_hint=None):
        self.calls.append((path, ra_hint, dec_hint, fov_deg_hint))
        return self.result


@pytest.fixture
def guard_rig(monkeypatch, tmp_path):
    import astrodeck.hub as hub_mod
    import astrodeck.imaging as imaging_mod
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(imaging_mod, "save_fits", lambda frame, path, **kw: path)
    return _GuardHub()


async def test_the_exposure_guard_wraps_the_exposure_and_releases_it(guard_rig):
    solver = _GuardSolver(_Solve(5.0, _DEC, 12.0))
    _frame, result, geom = await nat._capture_and_solve(guard_rig, solver)
    assert guard_rig.enters == 1, "the exposure never went through exposure_guard"
    assert guard_rig.exits == 1, guard_rig.events
    assert guard_rig.events[:3] == ["enter", "expose", "exit"], guard_rig.events
    assert result.ra_hours == 5.0
    assert geom == (1.55, 1024.0, 768.0), geom
    assert guard_rig.cam.exposures == [(nat._SOLVE_EXPOSURE_S, 200, 30, 1)], \
        guard_rig.cam.exposures


async def test_the_exposure_guard_is_released_when_the_solve_fails(guard_rig):
    """The guard is non-blocking: whoever finds it held gets a DeviceError. A
    polar run that leaked it would lock the camera out for the rest of the night
    with nothing running."""
    bad = types.SimpleNamespace(success=False, message="not enough stars")
    with pytest.raises(DeviceError) as e:
        await nat._capture_and_solve(guard_rig, _GuardSolver(bad))
    assert "plate solve failed" in str(e.value)
    assert guard_rig.enters == 1, guard_rig.events
    assert guard_rig.exits == 1, "the exposure guard was not released on the failure"


async def test_the_exposure_guard_is_released_when_the_camera_raises(guard_rig, monkeypatch):
    async def _boom(*a, **kw):
        raise DeviceError("camera is busy (plate solve)")

    monkeypatch.setattr(guard_rig.cam, "expose", _boom)
    with pytest.raises(DeviceError):
        await nat._capture_and_solve(guard_rig, _GuardSolver(None))
    assert guard_rig.enters == 1 and guard_rig.exits == 1, guard_rig.events


# ------------------------------------------------- the position-angle channel

async def test_a_solver_with_no_rotation_is_recorded_as_zero_and_runs_on(
        make_rig, bus_lines):
    """CHARACTERISATION. Claim: "``result.rotation_deg or 0.0`` lets the
    pier-flip detector report a spread that never happened" — REFUTED. Position
    angle does not enter the axis fit at all (see the test below, which measures
    that), so an unknown coerced to 0.0 cannot move the number the operator turns
    a bolt by; and the spread is not the only flip signal — the hour-angle sign
    rule in ``_ra_step_hours`` now makes the crossing that causes a flip
    unreachable in the first place. Making the field nullable would only push a
    None into an engine that types it as a float.

    So: pin the coercion and pin that a solver with no rotation channel at all
    (plenty of them) still produces a complete, unflagged run."""
    rig = make_rig(start_ha=-2.0)
    rig.rotation = None
    await rig.run()

    assert rig.engine.from_three_calls, "precondition: the fit never ran"
    sent = [s["position_angle_deg"] for s in rig.engine.from_three_calls[0][0]]
    assert sent == [0.0, 0.0, 0.0], sent
    assert rig.session.state["state"] == "done", rig.session.state
    # And nothing accuses the run of a flip it did not make.
    blamed = [m for lvl, m, _src in bus_lines
              if lvl == "warning" and "meridian" in m]
    assert not blamed, blamed


@requires_engine
async def test_a_missing_rotation_cannot_move_the_number_you_turn_a_bolt_by(
        make_rig, bus_lines, monkeypatch):
    """CHARACTERISATION, and the measurement that refutes the claim above.

    Two runs over identical geometry: one where every frame solves at PA 175,
    one where frame 1 reports nothing and is coerced to 0.0. The coercion makes
    the engine's ``position_angle_spread_deg`` jump from 0 to 175 and fires
    ``_log_pa_spread`` — a cosmetic warning — but the AZ/ALT/total error, the
    only outputs anyone acts on, are bit-identical. Position angle is carried for
    diagnostics; it is not an input to the axis fit.

    That is why "make the missing angle a None so the detector can abstain" was
    rejected: the false positive costs a log line, and the proposed change would
    have handed the engine a null in a float field to buy it."""
    # The adjust phase re-solves the same stationary frame and the engine refuses
    # a zero-length correction leg, so cap it: this test is about the INITIAL
    # fit, which is the only place the three position angles are used at all.
    monkeypatch.setattr(nat, "_MAX_ADJUST_UPDATES", 1)
    both: dict[str, tuple] = {}
    for label, by_point in (("all_known", {}), ("one_missing", {1: None})):
        rig = make_rig(start_ha=-2.0, engine="real")
        rig.rotation = 175.0
        rig.rotation_by_point = dict(by_point)
        await rig.run()
        fit = [p for p in rig.session.published if p.get("progress") == 0.6]
        assert len(fit) == 1, rig.session.published
        both[label] = (fit[0]["az_error"], fit[0]["alt_error"],
                       fit[0].get("position_angle_spread_deg"),
                       tuple(fit[0].get("flags") or ()))

    known, missing = both["all_known"], both["one_missing"]
    assert known[:2] == missing[:2], (
        "coercing one unknown position angle to 0.0 changed the reported polar "
        f"error: {known[:2]} -> {missing[:2]}")
    assert known[2] == pytest.approx(0.0, abs=0.01), known
    assert missing[2] == pytest.approx(175.0, abs=0.5), missing
    # The spread flag is the ONLY thing that differs between the two runs.
    assert "position_angle_spread_large" not in known[3], known
    assert "position_angle_spread_large" in missing[3], missing
    # The cosmetic consequence, pinned so it is not mistaken for a fit change.
    blamed = [m for lvl, m, _src in bus_lines
              if lvl == "warning" and "camera/pier angle moved" in m]
    assert len(blamed) == 1, blamed


async def test_a_real_rotation_reaches_the_engine_unchanged(make_rig):
    """The control: when the solver DOES report an angle, it must arrive intact —
    otherwise the test above would pass for the wrong reason."""
    rig = make_rig(start_ha=-2.0)
    rig.rotation_by_point = {1: 10.0, 2: 11.5, 3: 189.7}
    await rig.run()
    assert rig.engine.from_three_calls, "precondition: the fit never ran"
    sent = [s["position_angle_deg"] for s in rig.engine.from_three_calls[0][0]]
    assert sent == [10.0, 11.5, 189.7], sent


# ------------------------------------------------------------ the adjust phase

async def test_a_degenerate_live_update_does_not_kill_the_session(make_rig, bus_lines):
    """A collapsed live correction must be survivable: the user is mid-adjustment
    with a hex key in the mount, and ending the session drops them back to a
    wizard start."""
    rig = make_rig(start_ha=-2.0)
    rig.engine.update_raises_first = 2
    await rig.run()

    assert len(rig.engine.update_calls) == 3, rig.engine.update_calls
    assert rig.session.state["state"] == "done", rig.session.state
    skipped = [m for lvl, m, _s in bus_lines
               if lvl == "warning" and "skipped" in m]
    assert len(skipped) == 2, skipped


async def test_an_adjust_phase_where_every_update_failed_does_not_report_done(
        make_rig, monkeypatch, bus_lines):
    """The safety cap publishes ``state:"done"`` unconditionally, even when NOT
    ONE live update succeeded.

    That is the shape of the run above: 240 exposures, 240 "update skipped"
    warnings the operator never sees on screen, and then a terminal "done" over
    the stale measuring-phase number — which the wizard renders as a finished
    alignment. "The session ran out of retries" and "the alignment is finished"
    are different facts and only one of them is true."""
    monkeypatch.setattr(nat, "_MAX_ADJUST_UPDATES", 4)
    rig = make_rig(start_ha=-2.0)
    rig.engine.update_raises_first = 99          # every update fails
    await rig.run()

    assert len(rig.engine.update_calls) == 4, \
        f"precondition: the cap has to have been reached: {len(rig.engine.update_calls)}"
    skipped = [m for lvl, m, _s in bus_lines if lvl == "warning" and "skipped" in m]
    assert len(skipped) == 4, skipped
    st = rig.session.state
    assert st["state"] != "done", (
        "every live update failed and the session still announced a finished "
        f"alignment: {st['state']!r} / {st['message']!r}")


async def test_the_adjust_phase_keeps_re_solving_the_live_position(make_rig):
    """The live update has to be fed a NEW solve each tick, not the last
    measuring frame — that is the whole point of the phase."""
    rig = make_rig(start_ha=-2.0)
    rig.engine.update_raises_first = 2
    await rig.run()
    assert len(rig.solved) == 3 + 3, rig.solved      # 3 measuring + 3 updates
    fed = [s["timestamp_unix_s"] for s in rig.engine.update_calls]
    assert len(set(fed)) == len(fed), fed
