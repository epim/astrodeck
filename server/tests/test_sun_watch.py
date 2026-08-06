"""Sun watch — the net under a tube the Sun is coming TO (task #150).

``Hub._check_solar`` refuses a SLEW whose destination is inside the cone. It has
never been able to notice a tube that is simply standing there while the Sun
arrives, which is the hazard the owner asked about on 2026-08-06 and could not
find anywhere in the code.

Everything here runs against an INJECTED clock and an INJECTED Sun, so the suite
decides the sky rather than the date it happens to run on. The Sun is pinned at
RA 0h / Dec 0°: over the 30-minute projection window the real Sun moves 0.02°
along the ecliptic, which is three orders of magnitude below the degrees-wide
cone the decision is made against, so holding it still costs the tests nothing
and buys exact arithmetic (at Dec 0, separation is just ΔRA × 15°/h).
``test_the_real_ephemeris_is_the_one_wired_in`` is the one test that does NOT
inject, so the injection cannot hide a module reading the wrong Sun.

Every separation a decision turns on is asserted as a PRECONDITION first: a
"it did not park" that passes because the tube was 90° away proves nothing.
"""
from __future__ import annotations

import asyncio

import pytest

from astrodeck import sun_watch as sun_watch_mod
from astrodeck.catalog.coords import angular_sep_deg, sun_radec
from astrodeck.config import AppConfig
from astrodeck.sun_watch import (LEAD_TIME_S, SIDEREAL_HOURS_PER_SOLAR_HOUR,
                                 SunWatch, closest_approach, project_pointing)

# A fixed instant, so nothing in this file depends on when it runs.
# 2026-06-01T00:00:00Z.
JUNE_TS = 1780272000.0

# The injected Sun. Dec 0 makes separation exactly ΔRA × 15.
SUN_RA_H, SUN_DEC = 0.0, 0.0

# How much RA a non-tracking mount gains over the whole lead window, in hours.
# The mount holds its hour angle, so its RA follows local sidereal time.
DRIFT_H = (LEAD_TIME_S / 3600.0) * SIDEREAL_HOURS_PER_SOLAR_HOUR


class FakeTel:
    def __init__(self, *, ra_hours: float = 12.0, dec_deg: float = 0.0,
                 tracking: bool = False, parked: bool = False, hub=None) -> None:
        self.connected = True
        self.ra_hours = ra_hours
        self.dec_deg = dec_deg
        self.tracking = tracking
        self.parked = parked
        self.park_calls = 0
        self.park_error: Exception | None = None
        self.position_error: Exception | None = None
        self.tracking_error: Exception | None = None
        self.locked_during_park: list[bool] = []
        self._hub = hub

    async def get_position(self) -> tuple[float, float]:
        if self.position_error is not None:
            raise self.position_error
        return self.ra_hours, self.dec_deg

    async def get_tracking(self) -> bool:
        if self.tracking_error is not None:
            raise self.tracking_error
        return self.tracking

    async def is_parked(self) -> bool:
        return self.parked

    async def park(self) -> None:
        self.park_calls += 1
        if self._hub is not None:
            self.locked_during_park.append(self._hub._motion_lock.locked())
        if self.park_error is not None:
            raise self.park_error
        self.parked = True
        self.tracking = False
        # A real park points the tube at the celestial pole, which is >= 66.5°
        # from the Sun at every site and every date. Modelling that is what lets
        # "the approach cleared after the park" be an assertion rather than a
        # claim.
        self.ra_hours, self.dec_deg = 0.0, 90.0


class FakeHub:
    def __init__(self, tel=None) -> None:
        self.devices = {"telescope": tel} if tel is not None else {}
        self._motion_lock = asyncio.Lock()
        self.epoch_bumps = 0
        self.lanes: list[str] = []

    def bump_motion_epoch(self) -> None:
        self.epoch_bumps += 1

    def busy_lanes(self) -> list[str]:
        return list(self.lanes)


class FakeEngine:
    def __init__(self, running: bool = False) -> None:
        self.running = running


@pytest.fixture
def cfg(monkeypatch):
    """The REAL AppConfig (defaults), served to the module under test.

    Deliberately not a stub with two attributes: the disarm is
    ``safety.solar_avoidance`` and the cone ``safety.solar_exclusion_deg``, and
    a hand-rolled double would keep passing after either was renamed."""
    conf = AppConfig()
    monkeypatch.setattr(sun_watch_mod, "config_store",
                        type("_S", (), {"cfg": staticmethod(lambda: conf)})())
    return conf


@pytest.fixture
def pinned_sun(monkeypatch):
    """The injected Sun: RA 0h, Dec 0°, motionless."""
    monkeypatch.setattr(sun_watch_mod, "sun_radec",
                        lambda ts=None: (SUN_RA_H, SUN_DEC))


def _rig(**tel_kw):
    hub = FakeHub()
    tel = FakeTel(hub=hub, **tel_kw)
    hub.devices["telescope"] = tel
    return hub, tel


def _watch(hub, engine=None, ts: float = JUNE_TS) -> SunWatch:
    return SunWatch(hub, engine or FakeEngine(), clock=lambda: ts)


def _said(lines, needle: str) -> bool:
    """Did the daemon say ``needle``? ``lines`` is the ``bus_lines`` fixture —
    never a slice of the shared 200-entry ring (see conftest)."""
    return any(needle in message for _level, message, _source in lines)


def _level_of(lines, needle: str) -> str | None:
    for level, message, _source in lines:
        if needle in message:
            return level
    return None


# ------------------------------------------------------------------ geometry

def test_a_tracking_tube_holds_its_place_and_a_stopped_one_does_not():
    """The whole predictive model in two lines, and the reason it is predictive.

    A tracking mount holds RA/Dec, so the only thing closing the gap is the
    Sun's ~1°/day of ecliptic motion. A STOPPED mount holds its hour angle, so
    the sky turns it toward the Sun at 15.04°/h — 360× faster, and the case a
    reactive check would only notice once the damage had started.
    """
    held = project_pointing(6.0, 20.0, tracking=True, dt_s=3600.0)
    assert held == (6.0, 20.0), "tracking means the pointing is a constant"

    ra, dec = project_pointing(6.0, 20.0, tracking=False, dt_s=3600.0)
    assert dec == 20.0, "hour-angle-holding moves RA only"
    assert ra == pytest.approx(6.0 + SIDEREAL_HOURS_PER_SOLAR_HOUR, abs=1e-9)
    assert (ra - 6.0) * 15.0 == pytest.approx(15.04, abs=0.01), (
        "15.04°/h — the number LEAD_TIME_S is justified against")

    # And it wraps rather than inventing an RA of 24.5 hours.
    wrapped, _ = project_pointing(23.9, 0.0, tracking=False, dt_s=3600.0)
    assert 0.0 <= wrapped < 1.0, wrapped


def test_closest_approach_includes_now_not_just_the_far_end(pinned_sun):
    """A Sun already in the cone must be reported, not projected past."""
    sep, lead = closest_approach(SUN_RA_H, SUN_DEC, tracking=True, now=JUNE_TS)
    assert sep == pytest.approx(0.0, abs=1e-6)
    assert lead == 0.0, "the worst moment is right now"


def test_closest_approach_finds_a_transit_inside_the_window(pinned_sun):
    """The reason the window is SAMPLED and not just checked at both ends.

    With a small cone a stopped mount can carry the Sun into it and back out
    again inside one window. A two-endpoint check sees a comfortable margin at
    both ends and reports safe, which is exactly wrong.
    """
    window = 3600.0
    drift_h = (window / 3600.0) * SIDEREAL_HOURS_PER_SOLAR_HOUR
    # Start half a window west of the Sun, so the Sun is dead centre at the
    # midpoint and well clear at both ends.
    start_ra = (SUN_RA_H - drift_h / 2.0) % 24.0
    end_ra, _ = project_pointing(start_ra, SUN_DEC, tracking=False,
                                 dt_s=window)
    sep_start = angular_sep_deg(start_ra, SUN_DEC, SUN_RA_H, SUN_DEC)
    sep_end = angular_sep_deg(end_ra, SUN_DEC, SUN_RA_H, SUN_DEC)
    assert sep_start > 7.0 and sep_end > 7.0, (
        f"precondition: both ENDPOINTS are clear of a 7° cone "
        f"({sep_start:.2f}°, {sep_end:.2f}°) — an endpoints-only check would "
        f"call this safe")

    sep, lead = closest_approach(start_ra, SUN_DEC, tracking=False,
                                 now=JUNE_TS, lead_s=window)
    assert sep < 1.0, f"the Sun passes right through the tube: {sep:.2f}°"
    assert lead == pytest.approx(window / 2.0, abs=1.0), (
        "and it happens in the middle of the window")


def test_the_real_ephemeris_is_the_one_wired_in():
    """No injection here. Every other test pins the Sun, so this is the only
    thing standing between "the projection works" and "the projection is done
    against something that is not the Sun"."""
    sun_ra, sun_dec = sun_radec(JUNE_TS)
    at_the_sun, lead = closest_approach(sun_ra, sun_dec, tracking=True,
                                        now=JUNE_TS)
    assert at_the_sun < 0.05, f"pointing at the Sun reads as {at_the_sun}°"
    assert lead == 0.0

    anti_ra, anti_dec = (sun_ra + 12.0) % 24.0, -sun_dec
    away, _ = closest_approach(anti_ra, anti_dec, tracking=True, now=JUNE_TS)
    assert away > 179.0, f"the antisolar point reads as {away}°"

    # June: the Sun is near the summer solstice, so its declination is close to
    # the obliquity. If a longitude/latitude mix-up ever crept into the helper
    # chain this is the number that moves.
    assert sun_dec == pytest.approx(22.0, abs=2.0), sun_dec


# ----------------------------------------------------------------- the action

async def test_it_parks_a_stopped_tube_before_the_sun_arrives(cfg, pinned_sun,
                                                              bus_lines):
    """The headline case: nothing is slewing, the tube is outside the cone RIGHT
    NOW, and the sky is carrying it in. Acting only once the Sun is inside would
    be acting after the damage started."""
    cone = cfg.safety.solar_exclusion_deg
    # 2.5 h west of the Sun = 37.5° now; the 0.501 h of sidereal drift over the
    # window brings that to 29.98°, i.e. just inside a 30° cone.
    start_ra = (SUN_RA_H - 2.5) % 24.0
    hub, tel = _rig(ra_hours=start_ra, dec_deg=SUN_DEC, tracking=False)

    now_sep, _ = closest_approach(start_ra, SUN_DEC, tracking=False,
                                  now=JUNE_TS, lead_s=0.0)
    assert now_sep > cone, (
        f"precondition: the Sun is OUTSIDE the cone at this instant "
        f"({now_sep:.1f}° vs {cone:.0f}°), so a reactive check would do nothing")

    await _watch(hub).tick()

    assert tel.park_calls == 1
    assert tel.parked is True
    assert hub.epoch_bumps == 1, "the motion fence is bumped like every other park"
    assert tel.locked_during_park == [True], (
        "the park must run under the hub motion lock — it is the one thing "
        "keeping two motion paths off the wire at once")
    assert _level_of(bus_lines, "SUN WATCH:") == "error", (
        "an approaching Sun is an error-level event; the AlertDispatcher only "
        "routes warning and error to the configured sinks")
    assert _said(bus_lines, "tracking is OFF"), (
        "the log must say WHY it is closing, or the morning question is still "
        "unanswerable")
    assert _said(bus_lines, "mount parked"), bus_lines


async def test_the_same_pointing_is_left_alone_when_the_mount_is_tracking(
        cfg, pinned_sun, bus_lines):
    """The pair to the test above, and the reason tracking is read at all.

    Identical pointing, identical sky, one bit different — and the answer flips.
    A watchdog that ignored tracking state would park this rig every night."""
    start_ra = (SUN_RA_H - 2.5) % 24.0
    hub, tel = _rig(ra_hours=start_ra, dec_deg=SUN_DEC, tracking=True)

    sep, _ = closest_approach(start_ra, SUN_DEC, tracking=True, now=JUNE_TS)
    assert sep > cfg.safety.solar_exclusion_deg, (
        f"precondition: a tracking tube stays {sep:.1f}° away all window")

    await _watch(hub).tick()
    assert tel.park_calls == 0
    assert bus_lines == [], (
        "and it is COMPLETELY silent — a net that narrates every quiet minute "
        "is a net whose alerts nobody reads")


async def test_a_sun_already_inside_the_cone_is_still_acted_on(cfg, pinned_sun):
    """Predictive must not mean "only in the future"."""
    ra = (SUN_RA_H - 10.0 / 15.0) % 24.0          # 10° away, tracking, holding
    hub, tel = _rig(ra_hours=ra, dec_deg=SUN_DEC, tracking=True)
    sep, _ = closest_approach(ra, SUN_DEC, tracking=True, now=JUNE_TS)
    assert sep < cfg.safety.solar_exclusion_deg, "precondition: already inside"

    await _watch(hub).tick()
    assert tel.park_calls == 1


async def test_an_unreadable_tracking_state_is_treated_as_stopped(
        cfg, pinned_sun):
    """The asymmetry: assuming "tracking" on a stopped mount projects a pointing
    that does not move and misses a 15°/h approach entirely, which is the whole
    hazard. Assuming "stopped" costs at worst one unnecessary park."""
    start_ra = (SUN_RA_H - 2.5) % 24.0
    hub, tel = _rig(ra_hours=start_ra, dec_deg=SUN_DEC, tracking=True)
    tel.tracking_error = RuntimeError("mount not answering")

    await _watch(hub).tick()
    assert tel.park_calls == 1, (
        "an unanswered question about tracking must not read as 'it is fine'")


# -------------------------------------------------------------- the disarms

async def test_a_deliberate_solar_session_disarms_it(cfg, pinned_sun, bus_lines):
    """``safety.solar_avoidance`` is the existing disarm for the pre-slew gate
    and the dawn-park daemon, and the owner said explicitly that solar astronomy
    is a legitimate use of this software. One switch, not three."""
    hub, tel = _rig(ra_hours=SUN_RA_H, dec_deg=SUN_DEC, tracking=True)
    sep, _ = closest_approach(SUN_RA_H, SUN_DEC, tracking=True, now=JUNE_TS)
    assert sep < cfg.safety.solar_exclusion_deg, (
        "precondition: the tube is pointing straight AT the Sun, so this rig "
        "would certainly be parked if the flag did not disarm it")

    cfg.safety.solar_avoidance = False
    await _watch(hub).tick()
    assert tel.park_calls == 0
    assert _said(bus_lines, "solar session")


async def test_a_zero_cone_disarms_it(cfg, pinned_sun, bus_lines):
    """``solar_exclusion_deg <= 0`` is already inert in ``_check_solar``; a
    second guard that ignored it would make the setting a half-truth."""
    hub, tel = _rig(ra_hours=SUN_RA_H, dec_deg=SUN_DEC, tracking=True)
    cfg.safety.solar_exclusion_deg = 0.0
    await _watch(hub).tick()
    assert tel.park_calls == 0
    assert _said(bus_lines, "0°")


async def test_the_off_switch_announces_itself(monkeypatch, bus_lines):
    monkeypatch.setenv(sun_watch_mod.NO_SUN_WATCH_ENV_VAR, "1")
    w = SunWatch(FakeHub(), FakeEngine())
    w.start()
    assert w._task is None, "disabled means no task at all"
    assert _said(bus_lines, "DISABLED"), (
        "a safety net that is off must say so; silence reads as working")


# ---------------------------------------------------------------- hands off

async def test_a_running_sequence_is_not_fought(cfg, pinned_sun, bus_lines):
    hub, tel = _rig(ra_hours=SUN_RA_H, dec_deg=SUN_DEC, tracking=True)
    sep, _ = closest_approach(SUN_RA_H, SUN_DEC, tracking=True, now=JUNE_TS)
    assert sep < cfg.safety.solar_exclusion_deg, "precondition: the net IS armed"

    await _watch(hub, FakeEngine(running=True)).tick()
    assert tel.park_calls == 0, "the engine owns its own aborts while a run lives"
    assert _level_of(bus_lines, "sequence run is in progress") == "warning", (
        "louder than dawn park's equivalent hold, because unlike a dawn the "
        "engine genuinely owns, NOTHING else is watching this")
    assert _said(bus_lines, "at closest"), (
        "and the hold carries the geometry, so the operator can judge it")


@pytest.mark.parametrize("lane", ["goto", "polar", "dome"])
async def test_hands_off_while_the_rig_is_busy(cfg, pinned_sun, lane):
    hub, tel = _rig(ra_hours=SUN_RA_H, dec_deg=SUN_DEC, tracking=True)
    hub.lanes = [lane]
    sep, _ = closest_approach(SUN_RA_H, SUN_DEC, tracking=True, now=JUNE_TS)
    assert sep < cfg.safety.solar_exclusion_deg, "precondition"

    w = _watch(hub)
    await w.tick()
    assert tel.park_calls == 0
    # ...and it is NOT latched, so the next tick acts once the lane clears.
    hub.lanes = []
    await w.tick()
    assert tel.park_calls == 1


# ------------------------------------------------------------ nothing to do

async def test_a_parked_mount_is_never_commanded(cfg, pinned_sun, bus_lines):
    """The ordinary parked case costs ZERO device commands and zero log lines:
    park points at the celestial pole, and the Sun's declination never leaves
    ±23.44°, so a parked tube is >= 66.5° clear at every site and every date."""
    hub, tel = _rig(ra_hours=0.0, dec_deg=90.0, tracking=False, parked=True)
    sep, _ = closest_approach(0.0, 90.0, tracking=False, now=JUNE_TS)
    assert sep > 66.0, f"precondition: the pole really is clear ({sep:.1f}°)"

    await _watch(hub).tick()
    assert tel.park_calls == 0
    assert bus_lines == []


async def test_a_parked_mount_inside_the_cone_is_reported_not_re_parked(
        cfg, pinned_sun, bus_lines):
    """A mount that says PARKED while pointing at the Sun has a wrong park
    position (or is lying). Sending it another park is a no-op that would hide
    the real problem."""
    hub, tel = _rig(ra_hours=SUN_RA_H, dec_deg=SUN_DEC, tracking=False,
                    parked=True)
    sep, _ = closest_approach(SUN_RA_H, SUN_DEC, tracking=False, now=JUNE_TS)
    assert sep < cfg.safety.solar_exclusion_deg, "precondition"

    await _watch(hub).tick()
    assert tel.park_calls == 0, "parking a parked mount cannot help"
    assert _level_of(bus_lines, "park position is not safe") == "error"


async def test_no_telescope_is_reported_not_swallowed(cfg, pinned_sun, bus_lines):
    hub = FakeHub()                       # devices empty
    await _watch(hub).tick()
    assert _said(bus_lines, "no telescope is connected")


async def test_a_disconnected_telescope_is_not_treated_as_safe(cfg, pinned_sun,
                                                               bus_lines):
    hub, tel = _rig(ra_hours=SUN_RA_H, dec_deg=SUN_DEC)
    tel.connected = False
    await _watch(hub).tick()
    assert tel.park_calls == 0
    assert _said(bus_lines, "no telescope is connected")


async def test_a_mount_that_will_not_report_its_position_does_not_park(
        cfg, pinned_sun, bus_lines):
    """Unknown is neither safe nor dangerous. Guessing would either park a
    working rig or bless a cooking one."""
    hub, tel = _rig(ra_hours=SUN_RA_H, dec_deg=SUN_DEC)
    tel.position_error = RuntimeError("mount not answering")
    await _watch(hub).tick()
    assert tel.park_calls == 0
    assert _said(bus_lines, "will not report its position")


async def test_a_nan_position_does_not_park(cfg, pinned_sun, bus_lines):
    """A half-initialised driver returning NaN makes every separation NaN, and
    ``nan >= cone`` is False — so an unguarded comparison reads NaN as "inside
    the cone" and parks the rig on a driver bug."""
    hub, tel = _rig(ra_hours=float("nan"), dec_deg=float("nan"))
    await _watch(hub).tick()
    assert tel.park_calls == 0
    assert _said(bus_lines, "will not report its position")


# ------------------------------------------------------------- idempotence

async def test_repeated_ticks_park_once(cfg, pinned_sun):
    start_ra = (SUN_RA_H - 2.5) % 24.0
    hub, tel = _rig(ra_hours=start_ra, dec_deg=SUN_DEC, tracking=False)
    w = _watch(hub)
    for _ in range(5):
        await w.tick()
    assert tel.park_calls == 1


async def test_a_park_the_mount_accepts_but_does_not_obey_is_called_out(
        cfg, pinned_sun, bus_lines):
    """Audit #15's shape: a driver that returns SUCCESS on absence of motion.

    The park is accepted, the tube has not moved, and the Sun is still coming.
    Commanding it again will not help — saying so, once, at error level, might.
    """
    hub, tel = _rig(ra_hours=SUN_RA_H, dec_deg=SUN_DEC, tracking=False)

    async def _lying_park() -> None:
        tel.park_calls += 1               # accepted...
        tel.locked_during_park.append(True)
        # ...and nothing moves: no pointing change, and it does not even claim
        # to be parked.

    tel.park = _lying_park
    w = _watch(hub)
    await w.tick()
    assert tel.park_calls == 1, "precondition: the first tick did command a park"

    await w.tick()
    await w.tick()
    assert tel.park_calls == 1, "re-commanding a mount that ignores parks is noise"
    assert _level_of(bus_lines, "HAS NOT MOVED") == "error"
    assert sum(1 for _l, m, _s in bus_lines if "HAS NOT MOVED" in m) == 1, (
        "said once, not once per minute")


async def test_a_failed_park_is_loud_and_retried(cfg, pinned_sun, bus_lines):
    hub, tel = _rig(ra_hours=SUN_RA_H, dec_deg=SUN_DEC, tracking=False)
    tel.park_error = RuntimeError("mount says no")

    w = _watch(hub)
    await w.tick()
    assert tel.park_calls == 1
    assert _level_of(bus_lines, "SUN WATCH PARK FAILED") == "error"
    await w.tick()
    assert tel.park_calls == 2, "a park that did not happen must be retried"


async def test_it_re_arms_once_the_sky_is_clear_again(cfg, pinned_sun, bus_lines):
    """After the park the pole is clear, so the net stands down — and if an
    operator unparks straight back into the Sun it fires again. The disarm for
    that is ``solar_avoidance``, not a latch: unlike dawn park (where unparking
    at 10 a.m. means somebody is working on the rig) pointing at the Sun is the
    hazard itself."""
    hub, tel = _rig(ra_hours=SUN_RA_H, dec_deg=SUN_DEC, tracking=False)
    w = _watch(hub)
    await w.tick()
    assert tel.park_calls == 1, "precondition: it acted once"
    assert (tel.ra_hours, tel.dec_deg) == (0.0, 90.0), (
        "precondition: the park really moved the tube to the pole")

    await w.tick()                        # parked at the pole: clear
    assert _said(bus_lines, "clear again")

    tel.parked = False                    # the operator unparks, back at the Sun
    tel.ra_hours, tel.dec_deg = SUN_RA_H, SUN_DEC
    await w.tick()
    assert tel.park_calls == 2


# ------------------------------------------------------------- failure modes

async def test_the_loop_outlives_a_tick_that_raises(cfg, bus_lines):
    """A bookkeeping task that dies takes the safety net with it, silently."""
    hub, _tel = _rig()
    w = SunWatch(hub, FakeEngine(), clock=lambda: JUNE_TS, interval_s=0.01)
    calls = {"n": 0}

    async def _boom() -> None:
        calls["n"] += 1
        raise RuntimeError("something in the sky math")

    w.tick = _boom                          # type: ignore[assignment]
    w.start()
    try:
        for _ in range(200):
            if calls["n"] >= 3:
                break
            await asyncio.sleep(0.01)
        assert calls["n"] >= 3, "the loop kept ticking after the first failure"
        assert w._task is not None and not w._task.done()
        assert _said(bus_lines, "sun-watch tick failed")
    finally:
        await w.stop()


# ------------------------------------------------------------------- wiring

def test_the_app_lifespan_owns_the_task(tmp_path, monkeypatch):
    """Wired the way dawn park is: started on boot, cancelled on shutdown. A
    safety net nobody starts is a comment."""
    from fastapi.testclient import TestClient
    import astrodeck.api.app as app_module
    from astrodeck.config import ConfigStore
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod

    monkeypatch.setenv(app_module.NO_AUTOCONNECT_ENV_VAR, "1")
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")

    app = app_module.create_app()
    with TestClient(app):
        task = app_module.sun_watch._task
        assert task is not None and not task.done(), "the net is running"
    assert app_module.sun_watch._task is None, "and is cancelled on shutdown"
