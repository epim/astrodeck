"""The measuring arc has to be ONE ROTATION OF ONE AXIS, and until 2026-09-09
it was two gotos.

THE NIGHT THIS FILE IS WRITTEN AGAINST. Three consecutive real runs on a mount
about 33 arcmin out reported 504', 493' and 359' of polar error:

    20:49  NGC 6826  Dec 50.539 -> 50.701 -> 50.587   bend 16.5'   reported 504'
    21:54  M39       Dec 48.444 -> 48.639 -> 48.571   bend 15.8'   reported 493'
    22:12  M39       Dec 48.440 -> 48.587 -> 48.532   bend 15.8'   reported 359'

Every leg was a goto to ``(new_ra, arc_dec)``. A goto is a request in SKY
coordinates: the mount inverts its own model of itself to place BOTH axes and
lands with whatever that model gets wrong — measured on this rig the same night
at 10.2' and 4.9' on two centring attempts. Point 1 is clean because the
centring solve syncs the mount there; points 2 and 3 each inherit the error at
their own position, and two independent ~10' declination landings are exactly
the bend above. The fit through three points is exact, so no residual could
object.

Turning ONE MECHANICAL AXIS cannot do that. A rigid body rotated about a fixed
axis traces an exact cone whatever else is wrong with it — cone error, a
non-orthogonal declination axis, a mount whose model of itself is hours out —
because none of that moves relative to the body. Only moving the OTHER axis
bends the arc off the cone, and a single-axis rotation never commands it.

So this file grades the mechanism, not the arithmetic:

  * ``_turn_the_guard_should_expect`` — the sidereal reasoning, both cases.
  * the capability probe, and that the loop reads it rather than guessing.
  * the loop against fakes: which calls each kind of mount receives, what it is
    told when its mount cannot make a clean arc, and that a timed rotation's
    OWN report of what it turned is what the guards grade against.
  * the simulator end to end, with a life-sized goto pointing error injected:
    the goto mechanism is wrecked by it and the axis mechanism is not. That
    pairing is the test that would have caught 2026-09-09.

The engine's own arithmetic is covered by ``test_tppa_engine_geometry.py``, the
guards as pure helpers by ``test_polar_meridian_guard.py``, and the measuring
loop's other properties by ``test_tppa_procedure.py``; none of that is repeated.
"""
from __future__ import annotations

import asyncio
import math
import time

import pytest

import astrodeck.hub as hub_module
from astrodeck import providers
from astrodeck.config import ConfigStore, SafetyConfig, Site
from astrodeck.devices.alpaca import (
    AXIS_ROTATION_MAX_RATE_DEG_S,
    AXIS_ROTATION_OVERRUN_FACTOR,
    AXIS_ROTATION_TARGET_S,
    AlpacaTelescope,
)
from astrodeck.devices.base import DeviceError, Telescope
from astrodeck.devices.sim import SimRig, SimTelescope
from astrodeck.events import bus
from astrodeck.hub import Hub
from astrodeck.polar import native as nat
from astrodeck.polar.session import PolarAlignSession

_LAT = 45.0
_LON = -122.0

requires_engine = pytest.mark.skipif(
    not nat.NATIVE_AVAILABLE, reason="astrodeck_native wheel not installed")


# ============================================================ the sidereal case

def test_with_tracking_on_the_expected_turn_carries_no_clock():
    """A tracking mount's frames are separated by the COMMANDED rotation, and
    the wall clock does not enter it.

    The derivation is in ``_turn_the_guard_should_expect``: the mount's RA axis
    turns (commanded − ωΔt) while the equatorial frame itself turns +ωΔt about
    the pole, and the two sidereal terms cancel in the composition. Asserted
    across leg lengths a run could plausibly take, because "no clock" is the
    whole claim."""
    for leg_s in (0.0, 19.0, 45.0, 600.0):
        assert nat._turn_the_guard_should_expect(12.0, leg_s, True) == 12.0
    assert nat._turn_the_guard_should_expect(-12.0, 45.0, True) == -12.0


def test_with_tracking_off_the_skys_own_rotation_is_added():
    """Tracking off, the mount holds its ground-fixed pointing between commands
    and the sky keeps turning, so the frames show the commanded rotation PLUS
    the sidereal turn over the gap between the two exposures.

    ``_ensure_tracking`` is best-effort — a mount that will not set tracking is
    not a reason to refuse an alignment — so this case is reachable, and grading
    such a run against the commanded angle alone would accuse a mount that did
    exactly what it was told. Over the ~45 s legs this routine takes the term is
    0.19 degrees, a fifth of the whole disagreement budget."""
    got = nat._turn_the_guard_should_expect(12.0, 45.0, False)
    assert got == pytest.approx(12.0 + 45.0 * nat.SIDEREAL_DEG_S)
    assert got - 12.0 == pytest.approx(0.188, abs=0.002)
    # ...and it is a SKY term, so it is added in the same (RA-increasing) sense
    # whichever way the arc was commanded to go.
    west = nat._turn_the_guard_should_expect(-12.0, 45.0, False)
    assert west == pytest.approx(-12.0 + 45.0 * nat.SIDEREAL_DEG_S)


def test_the_sidereal_rate_is_a_sidereal_day_not_a_solar_one():
    """0.004178 deg/s. A solar day would be 0.27% low, which is invisible over
    one leg and is exactly the kind of constant nobody re-derives later."""
    assert nat.SIDEREAL_DEG_S == pytest.approx(15.041 / 3600.0, rel=1e-4)


def test_the_guard_uses_the_tracking_state_it_is_given():
    """End to end through the guard, not just the helper: a synthetic arc made
    by a NON-tracking mount is refused when graded as if it were tracking, and
    accepted when the guard is told the truth.

    Built as a rigid rotation about the celestial pole of (12° + the sidereal
    turn over a 600 s leg) — 2.5 degrees of extra arc, well past the 1.0 degree
    limit, so the two answers cannot be confused for noise."""
    leg_s = 600.0
    extra = nat.SIDEREAL_DEG_S * leg_s
    t0 = 1_760_000_000.0
    solves = [{"ra_hours": (5.0 + n * (12.0 + extra) / 15.0),
               "dec_deg": 40.0 + 0.01 * n,   # a hair off-pole so an axis exists
               "timestamp_unix_s": t0 + n * leg_s,
               "position_angle_deg": 0.0}
              for n in range(3)]
    commanded = 12.0 / 15.0
    with pytest.raises(DeviceError, match="did not turn by what the mount"):
        nat._refuse_if_the_fit_does_not_reproduce_the_rotation(
            solves, commanded, tracking_on=True)
    worst = nat._refuse_if_the_fit_does_not_reproduce_the_rotation(
        solves, commanded, tracking_on=False)
    assert worst is not None and worst < 0.2, worst


def test_the_guard_takes_one_commanded_rotation_per_leg():
    """A timed single-axis rotation delivers rate x the elapsed time it
    measured, so the two legs of one arc can differ. The guard has to be able to
    hear that, or a leg the event loop stretched reads as a mount fault."""
    t0 = 1_760_000_000.0
    legs = [12.0, 13.5]
    ra = 5.0
    solves = []
    for n in range(3):
        solves.append({"ra_hours": ra, "dec_deg": 40.0 + 0.01 * n,
                       "timestamp_unix_s": t0 + n * 45.0,
                       "position_angle_deg": 0.0})
        if n < 2:
            ra += legs[n] / 15.0
    # Graded against the nominal step, the second leg is 1.5 degrees out.
    with pytest.raises(DeviceError, match="did not turn by what the mount"):
        nat._refuse_if_the_fit_does_not_reproduce_the_rotation(
            solves, 12.0 / 15.0)
    # Graded against what each leg actually commanded, it is a clean run.
    worst = nat._refuse_if_the_fit_does_not_reproduce_the_rotation(
        solves, [legs[0] / 15.0, legs[1] / 15.0])
    assert worst is not None and worst < 0.05, worst
    # A per-leg list that does not line up with the legs is not evidence for
    # anything, so it refuses nothing rather than inventing a comparison.
    assert nat._refuse_if_the_fit_does_not_reproduce_the_rotation(
        solves, [legs[0] / 15.0]) is None


# =========================================================== capability probe

class _Bare(Telescope):
    """The base contract, unimplemented — enough to read the default off."""

    async def connect(self): ...
    async def disconnect(self): ...
    async def get_position(self): return (0.0, 0.0)
    async def slew(self, ra_hours, dec_deg): ...
    async def sync(self, ra_hours, dec_deg): ...
    async def set_tracking(self, on): ...
    async def get_tracking(self): return True
    async def park(self): ...
    async def unpark(self): ...
    async def is_parked(self): return False
    async def move_axis(self, axis, rate_deg_s): ...
    async def is_slewing(self): return False


async def test_a_mount_refuses_single_axis_rotation_by_default():
    """The default is False and the default method raises. A capability nobody
    has proved must not be assumed: the whole point of the flag is that the
    polar driver can tell a mount that will hold its declination axis from one
    that merely has not been asked yet."""
    tel = _Bare("Quiet Mount")
    assert tel.can_rotate_axis is False
    assert nat._can_rotate_one_axis(tel) is False
    with pytest.raises(DeviceError, match="cannot rotate one axis"):
        await tel.rotate_axis("ra", 12.0)


def test_the_probe_needs_the_flag_AND_the_method():
    """Half a capability is not a capability. A backend that sets the flag and
    forgets the method would send the polar driver down a path that ends in an
    AttributeError two legs into an irreversible arc."""
    tel = _Bare("Half Mount")
    tel.can_rotate_axis = True
    assert nat._can_rotate_one_axis(tel) is True
    tel.rotate_axis = None                      # type: ignore[assignment]
    assert nat._can_rotate_one_axis(tel) is False


def test_the_probe_is_read_off_the_device_not_a_list_of_mount_names():
    """Hardcoding "the AM5 cannot, the sim can" here would be a second, worse
    copy of a question the backends already answer — and wrong for the case that
    matters most, the same mount reached through two different drivers. So the
    check is a duck-type on the device, and an object that offers the capability
    gets it regardless of what it is."""

    class _Anything:
        can_rotate_axis = True

        async def rotate_axis(self, axis, degrees):
            return degrees

    assert nat._can_rotate_one_axis(_Anything()) is True


# =================================================== the loop, against fakes

class _Frame:
    def __init__(self, timestamp): self.timestamp = timestamp


class _Solve:
    success = True
    message = "solved"
    pixel_scale_arcsec = 1.55

    def __init__(self, ra_hours, dec_deg, rotation_deg=0.0):
        self.ra_hours = ra_hours
        self.dec_deg = dec_deg
        self.rotation_deg = rotation_deg


class _Cam:
    name = "cam"


class _Tel:
    """A mount whose rotation mechanism the test chooses.

    ``can_rotate_axis`` False makes every leg a goto (the pre-2026-09-09 shape);
    True routes them through ``rotate_axis``, which here turns the RA coordinate
    and — the whole point — cannot touch the declination even when asked."""

    name = "Fictional Mount"

    def __init__(self, hub, ra_hours, dec_deg, *, can_rotate_axis=False):
        self._hub = hub
        self.ra = ra_hours
        self.dec = dec_deg
        self.can_rotate_axis = can_rotate_axis
        self.tracking = True
        #: what ``rotate_axis`` really delivers, as a multiple of what it was
        #: asked for — a stalled event loop stretching a timed move.
        self.rotate_delivers = 1.0
        #: ...and whether it OWNS UP to it. Reporting the ask while delivering
        #: something else is the lie the return value exists to prevent.
        self.rotate_reports_honestly = True

    async def get_position(self):
        return self.ra, self.dec

    async def get_tracking(self):
        return self.tracking

    async def set_tracking(self, on):
        self.tracking = bool(on)

    async def slew(self, ra_hours, dec_deg):
        self._hub.slews.append((ra_hours, dec_deg))
        self.ra = ra_hours % 24.0
        self.dec = dec_deg

    async def rotate_axis(self, axis, degrees):
        self._hub.rotations.append((axis, degrees))
        done = degrees * self.rotate_delivers
        if axis == "ra":
            self.ra = (self.ra + done / 15.0) % 24.0
        else:
            self.dec += done
        return done if self.rotate_reports_honestly else degrees


class _Hub:
    mode = "sim"

    def __init__(self, ra_hours, dec_deg, **tel_kw):
        self._motion_epoch = 0
        self.slews: list[tuple[float, float]] = []
        self.rotations: list[tuple[str, float]] = []
        self.solar_checks: list[tuple[float, float]] = []
        self.site = {"latitude": _LAT, "longitude": _LON, "elevation_m": 0.0,
                     "is_default": False}
        self.tel = _Tel(self, ra_hours, dec_deg, **tel_kw)

    def require(self, role):
        return self.tel if role == "telescope" else _Cam()

    async def yield_camera_for(self, what):
        return True

    def _check_solar(self, ra_hours, dec_deg):
        self.solar_checks.append((ra_hours, dec_deg))

    def effective_optics(self):
        return {"fov_h_deg": 1.2, "image_scale_arcsec_px": 1.55}


class _Session(PolarAlignSession):
    def __init__(self, hub):
        super().__init__(hub)
        self.published: list[dict] = []

    def _publish(self, **kw):
        self.published.append(dict(kw))
        super()._publish(**kw)


_INITIAL_ERR = {"az_arcmin": 1.44, "alt_arcmin": 1.92, "total_arcmin": 2.4,
                "az_direction": "left_west", "alt_direction": "up",
                "flags": [], "position_angle_spread_deg": 0.0}
_CLEAN_ERR = {"az_arcmin": 0.24, "alt_arcmin": 0.32, "total_arcmin": 0.4,
              "az_direction": "left_west", "alt_direction": "up",
              "flags": [], "position_angle_spread_deg": 0.0}


class _FakeEngine:
    def __init__(self):
        self.from_three_calls: list[list[dict]] = []

    def tppa_from_three(self, solves, site, opts):
        self.from_three_calls.append([dict(s) for s in solves])
        return {"model": {"fake": True}, "error": dict(_INITIAL_ERR)}

    def tppa_update(self, model, solve):
        return dict(_CLEAN_ERR)


class _Rig:
    """A hub, a real session, and a capture stand-in that reports where the
    mount ACTUALLY is — so the arc the driver made is the arc the fit sees."""

    def __init__(self, hub):
        self.hub = hub
        self.session = _Session(hub)
        self.solved: list[tuple[float, float]] = []
        self._t0 = time.time()

    async def capture(self, hub, solver, session=None):
        n = len(self.solved) + 1
        ra, dec = await hub.tel.get_position()
        self.solved.append((ra, dec))
        return (_Frame(self._t0 + 45.0 * n), _Solve(ra, dec),
                (1.55, 1024.0, 768.0))

    async def run(self):
        await nat.run_native(self.session, self.hub)


@pytest.fixture
def make_rig(monkeypatch):
    monkeypatch.setattr(nat, "NATIVE_AVAILABLE", True)
    monkeypatch.setattr(nat, "_ADJUST_INTERVAL_S", 0.01)
    monkeypatch.setattr(nat, "_SETTLE_AFTER_SLEW_S", 0.0)
    import astrodeck.providers as _pv
    monkeypatch.setattr(_pv, "pick_solver", lambda hub: object())

    def _make(*, can_rotate_axis=False, start_ha=-2.0, dec=40.0):
        from astrodeck.catalog.coords import lst_hours
        ra = (lst_hours(_LON) - start_ha) % 24.0
        hub = _Hub(ra, dec, can_rotate_axis=can_rotate_axis)
        rig = _Rig(hub)
        monkeypatch.setattr(nat, "_capture_and_solve", rig.capture)
        monkeypatch.setattr(nat, "_native", _FakeEngine())
        return rig

    return _make


async def test_a_mount_that_can_turn_one_axis_is_never_asked_for_a_declination(
        make_rig):
    """THE FIX, at the level of which calls the mount receives.

    Not "the declination it is sent is the right one" — no declination is sent
    at all. That is the difference between an arc that is a cone by construction
    and one that is a cone if the mount's pointing model behaves, and the second
    is what produced 504' on the sky."""
    rig = make_rig(can_rotate_axis=True)
    await rig.run()

    assert rig.hub.slews == [], (
        f"a mount that can turn one axis was still sent gotos: {rig.hub.slews}")
    assert len(rig.hub.rotations) == 2, rig.hub.rotations
    axes = {axis for axis, _deg in rig.hub.rotations}
    assert axes == {"ra"}, f"the declination axis was commanded: {axes}"
    # Both legs the same size and the same direction — the arc must not double
    # back on itself, and the step is decided once (see _ra_step_hours).
    degs = [deg for _axis, deg in rig.hub.rotations]
    assert degs[0] == pytest.approx(degs[1])
    assert abs(degs[0]) == pytest.approx(nat._RA_STEP_HOURS * 15.0)
    # ...and the declination never moved, which is what the fit needs.
    decs = [dec for _ra, dec in rig.solved]
    assert max(decs) == min(decs), decs


async def test_a_mount_that_cannot_falls_back_to_gotos_and_says_so(
        make_rig, bus_lines):
    """A mount without the capability keeps the mechanism it always had — and
    is not left silent about it.

    An operator who is refused by the rotation-agreement guard needs the line
    that says WHY this mount is at risk of it, or the refusal reads as the guard
    being broken. Naming the limitation is the only honest thing available for a
    mount that cannot do better."""
    rig = make_rig(can_rotate_axis=False)
    await rig.run()

    assert rig.hub.rotations == [], rig.hub.rotations
    assert len(rig.hub.slews) == 2, rig.hub.slews
    warned = [m for lvl, m, _src in bus_lines
              if lvl == "warning" and "cannot turn its RA axis on its own" in m]
    assert warned, [m for _l, m, _s in bus_lines]
    assert "GOTO" in warned[0] and "BOTH axes" in warned[0], warned[0]


async def test_the_capable_mount_is_told_so_in_the_log_too(make_rig, bus_lines):
    """The other half of the same line. A run that used the good mechanism says
    so, so a log from a night that went wrong can be read for which mechanism it
    used without inferring it from the absence of a warning."""
    rig = make_rig(can_rotate_axis=True)
    await rig.run()
    assert any("can turn its RA axis on its own" in m
               for _l, m, _s in bus_lines), [m for _l, m, _s in bus_lines]


async def test_the_goto_leg_names_the_declination_motion_it_is_about_to_make(
        make_rig, bus_lines):
    """THE EVIDENCE THE NEXT NIGHT NEEDS, and the reason 2026-09-09 took a day.

    The goto path pins the arc's declination and commands it every leg, so it
    drives the declination axis by (pinned − whatever the mount claims now).
    That number separates the two mounts nobody could tell apart from the logs:
    one whose REPORT walks while the tube holds still, and one that MISSES its
    gotos and reports the miss. Neither was distinguishable on the night,
    because nothing wrote it down."""
    rig = make_rig(can_rotate_axis=False)
    rig.hub.tel.dec = 40.0
    await rig.run()
    lines = [m for _l, m, _s in bus_lines
             if "moves the declination axis" in m]
    assert len(lines) == 2, [m for _l, m, _s in bus_lines]
    assert "+0.0'" in lines[0], lines[0]


async def test_a_stretched_timed_rotation_is_graded_by_what_it_really_turned(
        make_rig):
    """A timed single-axis move delivers rate x the elapsed time it MEASURED,
    and a blocked event loop makes that longer than intended. The mount here
    turns 20% further than it was asked and says so; the run must complete,
    because the arc really was one rigid rotation — just a bigger one.

    Graded against the intention instead, this would be refused as a mount
    fault, which is the failure this return value exists to prevent."""
    rig = make_rig(can_rotate_axis=True)
    rig.hub.tel.rotate_delivers = 1.2
    await rig.run()
    states = [p.get("state") for p in rig.session.published]
    assert "error" not in states, [p.get("message")
                                   for p in rig.session.published]


async def test_a_rotation_that_lies_about_what_it_turned_is_refused(make_rig):
    """The same 20% overshoot from a mount that reports the ask instead.

    Now "commanded" is a fiction, the frames disagree with it by 2.4 degrees,
    and the run is refused rather than reported — which is right: nothing here
    can tell that overshoot from a tripod leg settling, and the number the fit
    produced is not something to turn a bolt by either way."""
    rig = make_rig(can_rotate_axis=True)
    rig.hub.tel.rotate_delivers = 1.2
    rig.hub.tel.rotate_reports_honestly = False
    await rig.run()
    errs = [p for p in rig.session.published if p.get("state") == "error"]
    assert errs, [p.get("message") for p in rig.session.published]
    assert "did not turn by what the mount was told to turn" in errs[-1]["message"]


async def test_the_sun_cone_is_checked_against_where_the_axis_move_will_land(
        make_rig):
    """Defence in depth survives the mechanism change. The axis path predicts
    the landing declination as the mount's CURRENT one — because that is what
    will not change — rather than the arc's pinned value, so the sun guard is
    told the truth even on a mount that has drifted off it."""
    rig = make_rig(can_rotate_axis=True)
    rig.hub.tel.dec = 40.0
    await rig.run()
    assert len(rig.hub.solar_checks) == 2, rig.hub.solar_checks
    assert all(dec == pytest.approx(40.0)
               for _ra, dec in rig.hub.solar_checks), rig.hub.solar_checks


# ============================================ the simulator, end to end

@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    """A connected sim rig on a northern site with solar avoidance off — the
    ``test_polar_native.py`` fixture, duplicated rather than shared because that
    module is skipped wholesale without the Rust wheel and half of this one is
    not."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(hub_module, "config_store", store)
    import astrodeck.config as config_mod
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(providers, "config_store", store)
    monkeypatch.setattr(nat, "_SETTLE_AFTER_SLEW_S", 0.0)
    store.set_site(Site(name="Test", latitude=_LAT, longitude=_LON,
                        is_default=False), expected_version=None)
    store.set_safety(SafetyConfig(solar_avoidance=False))
    h = Hub()
    await h.connect_sim()
    yield h
    try:
        await h.polar.stop()
    except Exception:
        pass
    await h.disconnect_all()


async def _first_fit(h, timeout=45.0):
    """Run one alignment and return the FIRST fit it publishes, or the terminal
    error event.

    Taken off the bus rather than off ``h.polar.state``, because the adjust
    phase keeps re-publishing and a test that reads the live state is reading
    whichever update won the race — which for a WRECKED arc is a different
    number every run."""
    q = bus.subscribe()
    try:
        await h.polar.start()
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if (h.polar.state.get("phase") == "adjusting"
                    or h.polar.state.get("state") == "error"):
                break
            await asyncio.sleep(0.02)
        while not q.empty():
            ev = q.get_nowait()
            if ev.type != "polar":
                continue
            if ev.data.get("state") == "error":
                return ev.data
            if ev.data.get("phase") == "adjusting" and "az_error" in ev.data:
                return ev.data
    finally:
        bus.unsubscribe(q)
    return dict(h.polar.state)


#: The pointing error injected into the simulator's gotos, in arcminutes. The
#: rig's own two centring attempts on 2026-09-09 measured 10.2' and 4.9'.
_GOTO_ERROR_ARCMIN = 10.0
#: The misalignment being measured, and it is deliberately SMALL — a rig someone
#: has already aligned, which is the one 2026-09-09 was run on.
_INJ_AZ, _INJ_ALT = 5.0, 6.0


@requires_engine
async def test_the_axis_rotation_recovers_the_error_through_a_pointing_error(
        sim_hub):
    """THE FIX, end to end, with the fault that broke the real thing present.

    The simulator's mount misses every goto by ~10 arcmin in a smooth,
    position-dependent way — a pointing-model residual, which is what a real
    mount's goto error is. The alignment still recovers the injected 7.8' to
    well under half an arcminute, because it never issues a goto: turning one
    mechanical axis traces a cone whatever the model gets wrong."""
    h = sim_hub
    h.sim_rig.set_polar_misalignment(
        _INJ_AZ, _INJ_ALT, lat_deg=_LAT, lon_deg=_LON,
        goto_pointing_error_arcmin=_GOTO_ERROR_ARCMIN)
    expected = h.sim_rig.polar_misalignment.expected_total_arcmin
    assert h.require("telescope").can_rotate_axis is True

    fit = await _first_fit(h)
    assert fit.get("state") != "error", fit
    total = math.hypot(fit["az_error"], fit["alt_error"])
    assert abs(total - expected) < 0.5, (
        f"a {_GOTO_ERROR_ARCMIN:.0f}' goto pointing error reached the fit "
        f"through a single-axis rotation: reported {total:.2f}' against "
        f"{expected:.2f}' injected")
    assert abs(fit["az_error"] - _INJ_AZ) < 0.5, fit
    assert abs(fit["alt_error"] - _INJ_ALT) < 0.5, fit


@requires_engine
async def test_the_goto_arc_reports_a_wildly_wrong_error_on_the_same_mount(
        sim_hub, monkeypatch):
    """THE TEST THAT WOULD HAVE CAUGHT 2026-09-09, and the reason the mechanism
    had to change rather than the thresholds.

    Same mount, same 10' goto pointing error, same 7.8' of real misalignment —
    only the mechanism differs. Every guard is opened out first, because the
    point of this test is what the arithmetic DOES with a bent arc, not that
    something eventually refuses it: three points determine the axis exactly, so
    a few arcminutes of declination landing error come out as hundreds of
    arcminutes of "polar error", stated with total confidence, next to an
    instruction to go and turn a bolt."""
    h = sim_hub
    # Open every gate: this test is about the NUMBER, and each guard's own
    # behaviour is graded elsewhere (the one below grades this same arc against
    # the shipped thresholds).
    monkeypatch.setattr(nat, "MAX_ROTATION_DISAGREEMENT_DEG", 1e6)
    monkeypatch.setattr(nat, "MAX_PLAUSIBLE_ERROR_DEG", 1e6)
    monkeypatch.setattr(nat, "_AXIS_MOVED_DEC_BEND_DEG", 1e6)
    tel = h.require("telescope")
    tel.can_rotate_axis = False      # the pre-2026-09-09 mechanism
    h.sim_rig.set_polar_misalignment(
        _INJ_AZ, _INJ_ALT, lat_deg=_LAT, lon_deg=_LON,
        goto_pointing_error_arcmin=_GOTO_ERROR_ARCMIN)
    expected = h.sim_rig.polar_misalignment.expected_total_arcmin

    fit = await _first_fit(h)
    assert fit.get("state") != "error", fit
    total = math.hypot(fit["az_error"], fit["alt_error"])
    assert total > 20.0 * expected, (
        f"the goto mechanism reported {total:.1f}' on a mount {expected:.1f}' "
        f"out — if this ever stops being wildly wrong, the simulator has "
        f"stopped modelling the fault that ruined three real runs")


@requires_engine
async def test_the_guard_refuses_the_goto_arc_it_cannot_measure(sim_hub):
    """The same wrecked arc against the SHIPPED thresholds: refused, not
    reported.

    The guard is not the fix — it stops the lie, it does not make the
    measurement work — and this is what "stops the lie" looks like from the
    panel. Paired deliberately with the test above, which shows what it is
    stopping."""
    h = sim_hub
    h.require("telescope").can_rotate_axis = False
    h.sim_rig.set_polar_misalignment(
        _INJ_AZ, _INJ_ALT, lat_deg=_LAT, lon_deg=_LON,
        goto_pointing_error_arcmin=_GOTO_ERROR_ARCMIN)

    fit = await _first_fit(h)
    assert fit.get("state") == "error", fit
    assert ("did not turn by what the mount was told to turn" in fit["message"]
            or "do not lie on one circle" in fit["message"]), fit["message"]


@requires_engine
async def test_half_that_pointing_error_slips_past_every_guard(sim_hub):
    """WHY THE MECHANISM HAD TO CHANGE AND NOT THE THRESHOLDS.

    At HALF the pointing error — 5 arcmin, which is a well-behaved mount — the
    goto arc reports about 155' on a rig 7.8' out and EVERY guard lets it
    through: the declination bend is 6.3' against a 45' threshold, the RA
    separations are inside the arrival band, the camera angle never moves, 2.6
    degrees is far inside the plausibility cap, and the fit reproduces its
    commanded rotation to 0.58 degrees against a 1.0 degree limit.

    That number would be published with "adjust the mount" beside it. The
    rotation-agreement guard is not the fix and was never claimed to be — it
    stops the lie it can see, and this is one it cannot. Tightening it to reach
    here would re-arm the false-positive history that
    ``_AXIS_MOVED_DEC_BEND_DEG`` already has: 0.58 degrees is only three times
    the 99.9th percentile of ordinary plate-solve noise.

    Kept as a test rather than a comment because it is the thing that stops
    someone "simplifying" the mechanism back to a goto on the grounds that the
    guard would catch it."""
    h = sim_hub
    h.require("telescope").can_rotate_axis = False
    h.sim_rig.set_polar_misalignment(
        _INJ_AZ, _INJ_ALT, lat_deg=_LAT, lon_deg=_LON,
        goto_pointing_error_arcmin=_GOTO_ERROR_ARCMIN / 2.0)
    expected = h.sim_rig.polar_misalignment.expected_total_arcmin

    fit = await _first_fit(h)
    assert fit.get("state") != "error", (
        "the guard now catches a 5 arcmin pointing error — good, but this test "
        "is the argument for the mechanism and needs rewriting rather than "
        "deleting", fit)
    total = math.hypot(fit["az_error"], fit["alt_error"])
    assert total > 10.0 * expected, (
        f"reported {total:.1f}' on a mount {expected:.1f}' out, with nothing "
        f"refusing it")


@requires_engine
async def test_the_axis_rotation_is_unmoved_by_that_smaller_error_too(sim_hub):
    """The pair to the one above, and the point of the whole change: the same
    mount, the same 5 arcmin of goto pointing error, and the answer is right —
    because the arc never asks the mount where the sky is."""
    h = sim_hub
    h.sim_rig.set_polar_misalignment(
        _INJ_AZ, _INJ_ALT, lat_deg=_LAT, lon_deg=_LON,
        goto_pointing_error_arcmin=_GOTO_ERROR_ARCMIN / 2.0)
    expected = h.sim_rig.polar_misalignment.expected_total_arcmin
    fit = await _first_fit(h)
    assert fit.get("state") != "error", fit
    assert abs(math.hypot(fit["az_error"], fit["alt_error"]) - expected) < 0.5


@requires_engine
async def test_a_clean_run_logs_the_agreement_it_achieved(sim_hub, bus_lines):
    """The commanded rotation handed to the guard is the quantity the guard's
    geometry expects — verified end to end rather than asserted.

    A clean simulator run turns its RA axis 12 degrees a leg while tracking, and
    the turn measured about the axis its own three frames fit comes back at the
    commanded angle to a small fraction of a degree. If the sidereal reasoning
    in ``_turn_the_guard_should_expect`` were wrong by the tracking term, this
    would read ~0.19 rather than ~0."""
    h = sim_hub
    h.sim_rig.set_polar_misalignment(_INJ_AZ, _INJ_ALT, lat_deg=_LAT,
                                     lon_deg=_LON)
    fit = await _first_fit(h)
    assert fit.get("state") != "error", fit
    lines = [m for _l, m, _s in bus_lines
             if "reproduces the commanded rotation" in m]
    assert lines, [m for _l, m, _s in bus_lines]
    worst = float(lines[0].rsplit("to ", 1)[1].rstrip("°"))
    assert worst < nat.MAX_ROTATION_DISAGREEMENT_DEG, lines[0]
    assert worst < 0.1, (
        f"a clean sim arc disagreed with its own command by {worst:.3f}° — "
        f"the tracking term is 0.19° at a 45 s leg, so anything near that is "
        f"the sidereal reasoning being wrong rather than solver noise")


# ================================================ the simulator's own mechanism

async def test_the_sim_rotation_moves_the_ra_axis_and_nothing_else():
    """Directly on the device, with no polar machinery in the way: the
    declination the mount reports is bit-identical across a rotation, and the
    right ascension moved by exactly what was asked."""
    rig = SimRig()
    tel = SimTelescope(rig)
    rig.ra_hours, rig.dec_deg = 5.0, 40.0
    got = await tel.rotate_axis("ra", 12.0)
    assert got == 12.0
    assert rig.dec_deg == 40.0
    assert rig.ra_hours == pytest.approx(5.0 + 12.0 / 15.0)


async def test_the_sim_rotation_under_a_misalignment_holds_the_cone():
    """With a misalignment injected, an RA rotation advances the traced circle
    and the declination axis stays where it is — so three rotations put three
    points on ONE cone, which is the property the fit needs and the property a
    goto cannot promise."""
    rig = SimRig()
    tel = SimTelescope(rig)
    rig.set_polar_misalignment(5.0, 6.0, lat_deg=_LAT, lon_deg=_LON,
                               goto_pointing_error_arcmin=10.0)
    for _ in range(2):
        await tel.rotate_axis("ra", 12.0)
    assert rig._polar_dec_axis_deg == 0.0, (
        "a single-axis rotation displaced the declination axis, so the "
        "simulator can no longer tell the two mechanisms apart")


async def test_the_sim_goto_lands_with_the_injected_pointing_error():
    """The other half: the fault injection has to actually fire, or the pair of
    end-to-end tests above proves nothing. A goto displaces the DECLINATION
    AXIS, which is the displacement a rotation cannot make and the fit cannot
    survive."""
    rig = SimRig()
    tel = SimTelescope(rig)
    rig.set_polar_misalignment(5.0, 6.0, lat_deg=_LAT, lon_deg=_LON,
                               goto_pointing_error_arcmin=10.0)
    await tel.slew((rig.ra_hours + 0.8) % 24.0, rig.dec_deg)
    landed = rig._polar_dec_axis_deg
    assert abs(landed) > 1e-4, "the goto landed perfectly; nothing is injected"
    assert abs(landed) <= 10.0 / 60.0 + 1e-9, landed


def test_the_pointing_error_is_a_repeatable_function_of_the_axis_angle():
    """A pointing-model residual, not a random miss: the same mechanical
    position gives the same error every time, and two positions a leg apart give
    genuinely different ones.

    Both halves matter. Repeatable is what makes it a MODEL error rather than
    noise the fit could average away; different across a leg is what makes it
    bend the arc rather than merely offset it — a common-mode error would slide
    all three points along one cone and be harmless."""
    rig = SimRig()
    rig.set_polar_misalignment(5.0, 6.0, lat_deg=_LAT, lon_deg=_LON,
                               goto_pointing_error_arcmin=10.0)
    m = rig.polar_misalignment
    assert m.goto_landing_error(-12.0) == m.goto_landing_error(-12.0)
    leg1 = m.goto_landing_error(-12.0)[1]
    leg2 = m.goto_landing_error(-24.0)[1]
    # The bend two legs produce: (e3 - e2) - (e2 - 0) = e3 - 2 e2, against the
    # 15.8-16.5 arcmin measured on the sky.
    bend_arcmin = abs(leg2 - 2.0 * leg1) * 60.0
    assert 10.0 < bend_arcmin < 20.0, bend_arcmin


def test_the_pointing_error_is_off_by_default():
    """Every pre-2026-09-09 caller of ``set_polar_misalignment`` gets a mount
    whose gotos land exactly where they were sent, byte for byte — the same
    opt-in discipline ``phase_step_deg`` and ``rotator_pa_offset_deg`` have."""
    rig = SimRig()
    rig.set_polar_misalignment(5.0, 6.0, lat_deg=_LAT, lon_deg=_LON)
    assert rig.polar_misalignment.goto_landing_error(5.0) == (0.0, 0.0)


# ================================================== the Alpaca mechanism

class _Conn:
    """Records every Alpaca verb; scripted GET responses. The ``test_alpaca.py``
    RecConn pattern — no network, no mock library."""

    def __init__(self, **responses):
        self.host, self.port = "10.0.0.9", 11111
        self.calls: list[tuple[str, str, dict]] = []
        self.put_raises: dict[str, Exception] = {}
        self.responses = {
            "rightascension": 5.0, "declination": 10.0, "slewing": False,
            "tracking": True, "atpark": False, "canpulseguide": False,
            "trackingrates": [0], "trackingrate": 0, "canfindhome": False,
            "canmoveaxis": True,
            "axisrates": [{"Minimum": 0.0, "Maximum": 4.0}],
        }
        self.responses.update(responses)

    async def get(self, dev_type, dev_num, method, **params):
        self.calls.append(("get", method, params))
        return self.responses.get(method)

    async def put(self, dev_type, dev_num, method, **params):
        self.calls.append(("put", method, params))
        exc = self.put_raises.get(method)
        if exc is not None:
            raise exc
        return None


async def test_alpaca_probes_the_capability_at_connect():
    """Probed ONCE at connect, like every other capability here, because the
    polar driver picks its mechanism before the first leg — a lazy first-call
    probe would leave every mount on the goto path for the run that mattered."""
    tel = AlpacaTelescope(_Conn(), 0, "Fictional Alpaca Mount")
    await tel.connect()
    assert tel.can_rotate_axis is True
    assert tel._axis_rate_range == (0.0, 4.0)
    assert ("get", "canmoveaxis", {"Axis": 0}) in tel.conn.calls
    assert ("get", "axisrates", {"Axis": 0}) in tel.conn.calls


async def test_alpaca_declines_when_the_driver_cannot_move_the_axis():
    tel = AlpacaTelescope(_Conn(canmoveaxis=False), 0, "No-MoveAxis Mount")
    await tel.connect()
    assert tel.can_rotate_axis is False
    assert ("get", "axisrates", {"Axis": 0}) not in tel.conn.calls


async def test_alpaca_declines_when_the_only_rates_offered_are_too_slow():
    """CanMoveAxis alone is not the capability. A driver offering only
    guide-speed rates would take four minutes a leg, the field would set while
    it ran, and the sidereal correction would stop being a rounding error — so
    it keeps the goto path, and says nothing it cannot do."""
    tel = AlpacaTelescope(
        _Conn(axisrates=[{"Minimum": 0.0, "Maximum": 0.004}]), 0, "Guide-Only")
    await tel.connect()
    assert tel.can_rotate_axis is False


async def test_alpaca_survives_a_driver_that_has_never_heard_of_axisrates():
    """Best-effort, like the pier/pulse/home probes beside it: a transport or
    driver failure leaves the flag False and the mount correctly on the goto
    path, rather than failing the whole connect."""
    conn = _Conn()
    original = conn.get

    async def boom(dev_type, dev_num, method, **params):
        if method == "axisrates":
            raise DeviceError("not implemented")
        return await original(dev_type, dev_num, method, **params)

    conn.get = boom
    tel = AlpacaTelescope(conn, 0, "Old Driver")
    await tel.connect()
    assert tel.connected is True
    assert tel.can_rotate_axis is False


async def test_alpaca_rotation_starts_the_axis_and_always_stops_it():
    """MoveAxis at a chosen rate, then MoveAxis 0 — and the pair is the whole
    move, because there is no "turn N degrees" call in ASCOM."""
    tel = AlpacaTelescope(_Conn(), 0, "Fictional Alpaca Mount")
    await tel.connect()
    tel.conn.calls.clear()
    got = await tel.rotate_axis("ra", 12.0)

    puts = [(m, p) for verb, m, p in tel.conn.calls if verb == "put"]
    assert [m for m, _p in puts] == ["moveaxis", "moveaxis"], puts
    assert puts[0][1]["Axis"] == 0 and puts[0][1]["Rate"] > 0
    assert puts[1][1] == {"Axis": 0, "Rate": 0.0}
    # Rate is chosen to take about AXIS_ROTATION_TARGET_S, clamped by the
    # safety cap — 12 degrees in 12 s is 1.0 deg/s, exactly at it.
    assert puts[0][1]["Rate"] == pytest.approx(
        min(12.0 / AXIS_ROTATION_TARGET_S, AXIS_ROTATION_MAX_RATE_DEG_S))
    # What comes back is rate x the MEASURED dwell, so it is close to the ask
    # but need not equal it.
    assert got == pytest.approx(12.0, rel=0.05)


async def test_alpaca_rotation_runs_the_axis_the_other_way_for_a_negative_turn():
    tel = AlpacaTelescope(_Conn(), 0, "Fictional Alpaca Mount")
    await tel.connect()
    tel.conn.calls.clear()
    got = await tel.rotate_axis("ra", -12.0)
    start = [p for verb, m, p in tel.conn.calls if verb == "put"][0]
    assert start["Rate"] < 0
    assert got < 0


async def test_alpaca_stops_the_axis_when_the_wait_is_cancelled():
    """Cancellation lands in the middle of a timed move more often than
    anywhere else — it is where the coroutine spends the whole call. The stop is
    in a ``finally`` precisely so a STOP leaves the mount stopped."""
    tel = AlpacaTelescope(_Conn(), 0, "Fictional Alpaca Mount")
    await tel.connect()
    tel.conn.calls.clear()
    task = asyncio.create_task(tel.rotate_axis("ra", 12.0))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    puts = [(m, p) for verb, m, p in tel.conn.calls if verb == "put"]
    assert puts[-1] == ("moveaxis", {"Axis": 0, "Rate": 0.0}), puts


async def test_alpaca_refuses_a_rotation_the_event_loop_stretched(monkeypatch):
    """GN-02, one mechanism further on. A blocked event loop turned guide pulses
    into +83, +128 and +35 arcsec of unwanted travel on 2026-09-06; at a slew
    rate the same stall is degrees. The stop still fires — it is in a
    ``finally`` — but the arc that resulted is not the arc anything downstream
    was told about, so the rotation is refused rather than reported."""
    tel = AlpacaTelescope(_Conn(), 0, "Fictional Alpaca Mount")
    await tel.connect()

    real_sleep = asyncio.sleep

    async def stalled(seconds):
        # Return "late" by more than the overrun factor allows.
        await real_sleep(0.01)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + seconds * AXIS_ROTATION_OVERRUN_FACTOR + 0.05
        while loop.time() < deadline:
            await real_sleep(0.01)

    monkeypatch.setattr("astrodeck.devices.alpaca.AXIS_ROTATION_TARGET_S", 0.1)
    monkeypatch.setattr(asyncio, "sleep", stalled)
    with pytest.raises(DeviceError, match="should have taken"):
        await tel.rotate_axis("ra", 0.5)
    monkeypatch.undo()
    puts = [(m, p) for verb, m, p in tel.conn.calls if verb == "put"]
    assert puts[-1] == ("moveaxis", {"Axis": 0, "Rate": 0.0}), puts


async def test_alpaca_shouts_when_it_cannot_stop_the_axis():
    """The one failure here that is worse than a bad measurement: the axis is
    still running. AbortSlew is tried as a second route through the driver, and
    when that fails too the caller is told in words that the mount may still be
    moving — not handed a number."""
    conn = _Conn()
    tel = AlpacaTelescope(conn, 0, "Fictional Alpaca Mount")
    await tel.connect()

    calls = {"n": 0}
    original = conn.put

    async def put(dev_type, dev_num, method, **params):
        if method == "moveaxis" and params.get("Rate") == 0.0:
            calls["n"] += 1
            raise DeviceError("link dropped")
        if method == "abortslew":
            raise DeviceError("link dropped")
        return await original(dev_type, dev_num, method, **params)

    conn.put = put
    with pytest.raises(DeviceError, match="may still be running"):
        await tel.rotate_axis("ra", 0.05)
    assert calls["n"] == 1


async def test_alpaca_recovers_a_failed_stop_with_abortslew():
    """AbortSlew stops MoveAxis too, and a moving axis is worth a second attempt
    down a different code path in the driver. When it works, the rotation is a
    success rather than an alarm."""
    conn = _Conn()
    tel = AlpacaTelescope(conn, 0, "Fictional Alpaca Mount")
    await tel.connect()
    original = conn.put

    async def put(dev_type, dev_num, method, **params):
        if method == "moveaxis" and params.get("Rate") == 0.0:
            conn.calls.append(("put", method, params))
            raise DeviceError("link hiccup")
        return await original(dev_type, dev_num, method, **params)

    conn.put = put
    got = await tel.rotate_axis("ra", 0.05)
    assert got > 0
    assert any(m == "abortslew" for verb, m, _p in conn.calls if verb == "put")
