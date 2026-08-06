"""Dawn park — the net under a night that ended without a run (task #139).

Every park in this codebase lives inside ``engine._wind_down``, so a session
that never got a run going leaves the mount tracking through sunrise. These
tests drive ``DawnPark.tick`` directly with an injected clock, which is what
lets "correct year-round and at any latitude" be a test rather than a claim:
the trigger is the Sun's ALTITUDE, so the same code is exercised at a
mid-latitude dawn, through a polar day, and through a polar night.

Every altitude a decision depends on is asserted as a PRECONDITION first — a
"it did not park" that passes because the Sun was never up proves nothing.
"""
from __future__ import annotations

import asyncio

import pytest

from astrodeck import dawn_park as dawn_park_mod
from astrodeck.catalog.coords import sun_altaz
from astrodeck.config import AppConfig
from astrodeck.dawn_park import CIVIL_TWILIGHT_DEG, DawnPark, park_threshold_deg

# A site that is emphatically not the developer's. Mid-northern latitude, so it
# has ordinary dawns; the polar cases below use their own.
SITE_LAT, SITE_LON = 45.0, -110.0

# A fixed instant to scan from (2026-06-01T00:00:00Z). Every timestamp used in
# this file is DERIVED from a scan for a sky, never hand-written as a clock
# time — the module under test does not know what a clock is.
JUNE_TS = 1780272000.0
DEC_TS = 1797120000.0            # 2026-12-11T00:00:00Z


def _find_sky(lat: float, lon: float, pred, start: float,
              span_h: float = 48.0, step_s: float = 60.0) -> tuple[float, float]:
    """First instant in the window whose Sun altitude satisfies ``pred``."""
    t = start
    end = start + span_h * 3600.0
    while t < end:
        alt, _az = sun_altaz(lat, lon, t)
        if pred(alt):
            return t, alt
        t += step_s
    raise AssertionError("no such sky within the scan window")


def _alt_extremes(lat: float, lon: float, start: float,
                  span_h: float = 24.0, step_s: float = 600.0):
    alts = []
    t = start
    while t < start + span_h * 3600.0:
        alts.append(sun_altaz(lat, lon, t)[0])
        t += step_s
    return min(alts), max(alts)


class FakeTel:
    def __init__(self, *, parked: bool = False, hub=None) -> None:
        self.connected = True
        self.parked = parked
        self.park_calls = 0
        self.park_error: Exception | None = None
        self.query_error: Exception | None = None
        self.locked_during_park: list[bool] = []
        self._hub = hub

    async def is_parked(self) -> bool:
        if self.query_error is not None:
            raise self.query_error
        return self.parked

    async def park(self) -> None:
        self.park_calls += 1
        if self._hub is not None:
            self.locked_during_park.append(self._hub._motion_lock.locked())
        if self.park_error is not None:
            raise self.park_error
        self.parked = True


class FakeHub:
    def __init__(self, tel=None, *, is_default: bool = False,
                 lat: float = SITE_LAT, lon: float = SITE_LON) -> None:
        self.site = {"name": "Ridge Test Site", "latitude": lat,
                     "longitude": lon, "elevation_m": 0.0,
                     "is_default": is_default, "horizon_min_deg": 10.0}
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

    Deliberately not a stub with two attributes: the threshold is derived from
    ``safety.twilight_deg`` and the solar-session check from
    ``safety.solar_avoidance``, and a hand-rolled double would keep passing
    after either was renamed."""
    conf = AppConfig()
    monkeypatch.setattr(dawn_park_mod, "config_store",
                        type("_S", (), {"cfg": staticmethod(lambda: conf)})())
    return conf


def _said(lines, needle: str) -> bool:
    """Did the daemon say ``needle``? ``lines`` is the ``bus_lines`` fixture —
    never a slice of the shared 200-entry ring, which goes empty forever once
    some other suite has filled it (see conftest)."""
    return any(needle in message for _level, message, _source in lines)


def _daytime(lat: float = SITE_LAT, lon: float = SITE_LON,
             start: float = JUNE_TS, above: float = 2.0):
    """An instant with the Sun comfortably above the park threshold."""
    return _find_sky(lat, lon, lambda a: a > above, start)


def _night(lat: float = SITE_LAT, lon: float = SITE_LON,
           start: float = JUNE_TS, below: float = -15.0):
    return _find_sky(lat, lon, lambda a: a < below, start)


# ------------------------------------------------------------------ threshold

def test_threshold_is_the_later_of_the_operators_night_end_and_civil_twilight(cfg):
    cfg.safety.twilight_deg = -18.0          # astronomical dark
    assert park_threshold_deg(cfg) == CIVIL_TWILIGHT_DEG, (
        "a rig that observes to -18 must not be parked at -18 — that is the "
        "middle of somebody's last frame")
    cfg.safety.twilight_deg = -3.0           # this owner stops early
    assert park_threshold_deg(cfg) == -3.0, "their own answer wins when it is later"
    cfg.safety.twilight_deg = -12.0
    assert park_threshold_deg(cfg) == CIVIL_TWILIGHT_DEG


# ----------------------------------------------------------------- the action

async def test_parks_an_idle_mount_after_the_sun_comes_up(cfg, bus_lines):
    hub = FakeHub()
    tel = FakeTel(hub=hub)
    hub.devices["telescope"] = tel
    tel._hub = hub
    ts, alt = _daytime()
    assert alt > park_threshold_deg(cfg), "precondition: the Sun is really up"
    assert not tel.parked, "precondition: the mount is tracking, not parked"

    d = DawnPark(hub, FakeEngine(), clock=lambda: ts)
    await d.tick()

    assert tel.park_calls == 1
    assert tel.parked is True
    assert hub.epoch_bumps == 1, "the motion fence is bumped like every other park"
    assert tel.locked_during_park == [True], (
        "the park must run under the hub motion lock — it is the one thing "
        "keeping two motion paths off the wire at once")
    assert _said(bus_lines, "mount parked"), bus_lines
    assert _said(bus_lines, "tracking into the Sun"), (
        "the log must say WHY, or the morning question is still unanswerable")


async def test_does_nothing_while_it_is_still_night(cfg):
    hub = FakeHub()
    tel = FakeTel(hub=hub)
    hub.devices["telescope"] = tel
    ts, alt = _night()
    assert alt < park_threshold_deg(cfg), "precondition: it is genuinely dark"

    d = DawnPark(hub, FakeEngine(), clock=lambda: ts)
    await d.tick()
    assert tel.park_calls == 0
    assert tel.parked is False


async def test_an_already_parked_mount_is_not_commanded(cfg, bus_lines):
    hub = FakeHub()
    tel = FakeTel(parked=True, hub=hub)
    hub.devices["telescope"] = tel
    ts, alt = _daytime()
    assert alt > park_threshold_deg(cfg), "precondition"

    d = DawnPark(hub, FakeEngine(), clock=lambda: ts)
    await d.tick()
    assert tel.park_calls == 0
    assert _said(bus_lines, "already parked")


# ---------------------------------------------------------------- hands off

async def test_a_running_sequence_keeps_its_own_wind_down(cfg, bus_lines):
    hub = FakeHub()
    tel = FakeTel(hub=hub)
    hub.devices["telescope"] = tel
    ts, alt = _daytime()
    assert alt > park_threshold_deg(cfg), "precondition: the trigger IS armed"

    d = DawnPark(hub, FakeEngine(running=True), clock=lambda: ts)
    await d.tick()
    assert tel.park_calls == 0, "the engine owns the park while a run is alive"
    assert _said(bus_lines, "sequence run is in progress"), (
        "and it says so — silence here is indistinguishable from success")


@pytest.mark.parametrize("lane", ["goto", "polar", "dome"])
async def test_hands_off_while_the_rig_is_busy(cfg, lane):
    hub = FakeHub()
    tel = FakeTel(hub=hub)
    hub.devices["telescope"] = tel
    hub.lanes = [lane]
    ts, alt = _daytime()
    assert alt > park_threshold_deg(cfg), "precondition"

    d = DawnPark(hub, FakeEngine(), clock=lambda: ts)
    await d.tick()
    assert tel.park_calls == 0
    # ...and it is NOT latched, so the next tick acts once the lane clears.
    hub.lanes = []
    await d.tick()
    assert tel.park_calls == 1


async def test_a_slew_that_starts_during_the_park_query_is_not_fenced(cfg, bus_lines):
    """The gap between "is it parked?" and the park it commits to.

    ``_is_parked`` is allowed to take up to MOUNT_QUERY_TIMEOUT_S (30 s) on a
    wedged mount, and the very next thing the tick does is bump the motion
    fence, which ABANDONS whatever motion is in flight. A run or a manual slew
    that started inside that window was idle when the daemon looked and is not
    when it acts — so it would be fenced (and the mount parked out from under
    its owner) by a net whose entire premise is that the room is empty.

    Driven by making the QUERY itself start the slew, which is the only way to
    land inside the window deterministically."""
    hub = FakeHub()
    tel = FakeTel(hub=hub)
    hub.devices["telescope"] = tel
    ts, alt = _daytime()
    assert alt > park_threshold_deg(cfg), "precondition: the trigger IS armed"
    assert hub.busy_lanes() == [], (
        "precondition: nothing is running when the tick begins, so the daemon "
        "really does get past its first hands-off check")

    real_is_parked = tel.is_parked

    async def slew_starts_mid_query():
        hub.lanes = ["goto"]          # somebody pressed goto during the query
        return await real_is_parked()

    tel.is_parked = slew_starts_mid_query
    d = DawnPark(hub, FakeEngine(), clock=lambda: ts)
    await d.tick()

    assert tel.park_calls == 0, (
        "the daemon must not park a mount that acquired an owner while it was "
        "asking")
    assert hub.epoch_bumps == 0, (
        "and must not have fenced that owner's motion on the way out")
    assert _said(bus_lines, "hands on the mount"), "and it says why, once"

    # NOT latched: the moment the lane clears, the net acts.
    tel.is_parked = real_is_parked
    hub.lanes = []
    await d.tick()
    assert tel.park_calls == 1


async def test_a_deliberate_solar_session_is_left_alone(cfg, bus_lines):
    hub = FakeHub()
    tel = FakeTel(hub=hub)
    hub.devices["telescope"] = tel
    cfg.safety.solar_avoidance = False       # the solar-session opt-out
    ts, alt = _daytime()
    assert alt > park_threshold_deg(cfg), "precondition"

    d = DawnPark(hub, FakeEngine(), clock=lambda: ts)
    await d.tick()
    assert tel.park_calls == 0
    assert _said(bus_lines, "solar session")


async def test_an_unconfigured_site_is_inert(cfg, bus_lines):
    """The refusal has to be real, so the Sun is up at (0, 0) at this instant."""
    hub = FakeHub(is_default=True, lat=0.0, lon=0.0)
    tel = FakeTel(hub=hub)
    hub.devices["telescope"] = tel
    ts, alt = _find_sky(0.0, 0.0, lambda a: a > 5.0, JUNE_TS)
    assert alt > park_threshold_deg(cfg), (
        "precondition: with the placeholder site taken at face value the "
        "trigger WOULD fire — so refusing is a decision, not an accident")

    d = DawnPark(hub, FakeEngine(), clock=lambda: ts)
    await d.tick()
    assert tel.park_calls == 0
    assert _said(bus_lines, "never been set")


async def test_no_telescope_is_reported_not_swallowed(cfg, bus_lines):
    hub = FakeHub()                       # devices empty
    ts, alt = _daytime()
    assert alt > park_threshold_deg(cfg), "precondition"
    d = DawnPark(hub, FakeEngine(), clock=lambda: ts)
    await d.tick()
    assert _said(bus_lines, "no telescope is connected")


# ------------------------------------------------------------ idempotence

async def test_repeated_ticks_park_once(cfg):
    hub = FakeHub()
    tel = FakeTel(hub=hub)
    hub.devices["telescope"] = tel
    ts, alt = _daytime()
    assert alt > park_threshold_deg(cfg), "precondition"

    d = DawnPark(hub, FakeEngine(), clock=lambda: ts)
    for _ in range(5):
        await d.tick()
    assert tel.park_calls == 1


async def test_it_does_not_fight_an_operator_who_unparks(cfg):
    """Somebody unparking at 10 a.m. is working on the rig, not forgetting it."""
    hub = FakeHub()
    tel = FakeTel(hub=hub)
    hub.devices["telescope"] = tel
    ts, alt = _daytime()
    assert alt > park_threshold_deg(cfg), "precondition"

    d = DawnPark(hub, FakeEngine(), clock=lambda: ts)
    await d.tick()
    assert tel.park_calls == 1, "precondition: it acted once"

    tel.parked = False                    # the operator unparks, deliberately
    for _ in range(5):
        await d.tick()
    assert tel.park_calls == 1, "the net does not re-park a mount somebody freed"


async def test_it_re_arms_after_sunset_and_fires_the_next_morning(cfg):
    hub = FakeHub()
    tel = FakeTel(hub=hub)
    hub.devices["telescope"] = tel
    now = {"t": 0.0}
    d = DawnPark(hub, FakeEngine(), clock=lambda: now["t"])

    dawn1, alt1 = _daytime()
    assert alt1 > park_threshold_deg(cfg), "precondition: morning one"
    now["t"] = dawn1
    await d.tick()
    assert tel.park_calls == 1

    night, alt_n = _night(start=dawn1)
    assert alt_n < park_threshold_deg(cfg), "precondition: the Sun really set"
    now["t"] = night
    await d.tick()

    dawn2, alt2 = _daytime(start=night)
    assert alt2 > park_threshold_deg(cfg), "precondition: morning two"
    assert dawn2 > dawn1
    tel.parked = False                    # a night was observed in between
    now["t"] = dawn2
    await d.tick()
    assert tel.park_calls == 2, "a new night gets a new net"


# ------------------------------------------------------------- failure modes

async def test_a_failed_park_is_loud_and_retried(cfg, bus_lines):
    hub = FakeHub()
    tel = FakeTel(hub=hub)
    tel.park_error = RuntimeError("mount says no")
    hub.devices["telescope"] = tel
    ts, alt = _daytime()
    assert alt > park_threshold_deg(cfg), "precondition"

    d = DawnPark(hub, FakeEngine(), clock=lambda: ts)
    await d.tick()
    assert tel.park_calls == 1
    assert _said(bus_lines, "DAWN PARK FAILED")
    await d.tick()
    assert tel.park_calls == 2, "a park that did not happen must be retried"


async def test_an_unreadable_park_state_parks_anyway(cfg):
    """Idempotence is what makes the cheap answer safe: a redundant park is a
    no-op, a wrongly-skipped one is the whole hazard."""
    hub = FakeHub()
    tel = FakeTel(hub=hub)
    tel.query_error = RuntimeError("mount not answering")
    hub.devices["telescope"] = tel
    ts, alt = _daytime()
    assert alt > park_threshold_deg(cfg), "precondition"

    d = DawnPark(hub, FakeEngine(), clock=lambda: ts)
    await d.tick()
    assert tel.park_calls == 1


async def test_the_loop_outlives_a_tick_that_raises(cfg, bus_lines):
    """A bookkeeping task that dies takes the safety net with it."""
    hub = FakeHub()
    tel = FakeTel(hub=hub)
    hub.devices["telescope"] = tel
    ts, _alt = _daytime()
    d = DawnPark(hub, FakeEngine(), clock=lambda: ts, interval_s=0.01)
    calls = {"n": 0}

    async def _boom() -> None:
        calls["n"] += 1
        raise RuntimeError("something in the sky math")

    d.tick = _boom                          # type: ignore[assignment]
    d.start()
    try:
        for _ in range(200):
            if calls["n"] >= 3:
                break
            await asyncio.sleep(0.01)
        assert calls["n"] >= 3, "the loop kept ticking after the first failure"
        assert d._task is not None and not d._task.done()
        assert _said(bus_lines, "dawn-park tick failed")
    finally:
        await d.stop()


# ------------------------------------------------------- altitude, not clock

async def test_it_triggers_on_the_sun_not_the_hour(cfg):
    """The same instant, two sites half a world apart: one parks, one does not.

    A clock-driven trigger cannot tell these two apart, which is the entire
    reason this reads an altitude."""
    # Site A at local noon, so site B — twelve hours of longitude away — is at
    # local midnight. Sunrise would NOT do: a 180° offset lands the far site at
    # sunset, at the same altitude, which proves nothing.
    ts, alt_here = _find_sky(SITE_LAT, SITE_LON, lambda a: a > 40.0, JUNE_TS)
    far_lon = SITE_LON + 180.0              # -110 -> +70, inside [-180, 180]
    alt_there, _az = sun_altaz(SITE_LAT, far_lon, ts)
    assert alt_here > park_threshold_deg(cfg) > alt_there, (
        "precondition: the two skies straddle the threshold at one instant")

    hub_here = FakeHub()
    tel_here = FakeTel(hub=hub_here)
    hub_here.devices["telescope"] = tel_here
    hub_there = FakeHub(lon=far_lon)
    tel_there = FakeTel(hub=hub_there)
    hub_there.devices["telescope"] = tel_there

    await DawnPark(hub_here, FakeEngine(), clock=lambda: ts).tick()
    await DawnPark(hub_there, FakeEngine(), clock=lambda: ts).tick()
    assert tel_here.park_calls == 1
    assert tel_there.park_calls == 0, "still night there — nothing to park for"


async def test_polar_day_parks_once_and_then_leaves_the_rig_alone(cfg):
    """78°N in June: the Sun never sets, so a threshold that could only reset at
    night would be re-triggering forever — or, latched wrong, never again."""
    lat, lon = 78.0, 15.0
    lo, _hi = _alt_extremes(lat, lon, JUNE_TS)
    assert lo > park_threshold_deg(cfg), (
        "precondition: the Sun stays above the threshold for the whole 24 h")

    hub = FakeHub(lat=lat, lon=lon)
    tel = FakeTel(hub=hub)
    hub.devices["telescope"] = tel
    now = {"t": JUNE_TS}
    d = DawnPark(hub, FakeEngine(), clock=lambda: now["t"])
    for hour in range(0, 24, 3):
        now["t"] = JUNE_TS + hour * 3600.0
        await d.tick()
        tel.parked = False                 # an operator keeps freeing the mount
    assert tel.park_calls == 1, (
        "one park for one continuous day, not one per tick")


async def test_polar_night_never_fires(cfg):
    lat, lon = 78.0, 15.0
    _lo, hi = _alt_extremes(lat, lon, DEC_TS)
    assert hi < park_threshold_deg(cfg), (
        "precondition: the Sun never reaches the threshold all 'day'")

    hub = FakeHub(lat=lat, lon=lon)
    tel = FakeTel(hub=hub)
    hub.devices["telescope"] = tel
    now = {"t": DEC_TS}
    d = DawnPark(hub, FakeEngine(), clock=lambda: now["t"])
    for hour in range(0, 24, 3):
        now["t"] = DEC_TS + hour * 3600.0
        await d.tick()
    assert tel.park_calls == 0, "there is no dawn to park for"


# ------------------------------------------------------------------- wiring

def test_the_app_lifespan_owns_the_task(tmp_path, monkeypatch):
    """Wired the way the AlertDispatcher is: started on boot, cancelled on
    shutdown. A safety net nobody starts is a comment."""
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
        task = app_module.dawn_park._task
        assert task is not None and not task.done(), "the net is running"
    assert app_module.dawn_park._task is None, "and is cancelled on shutdown"


def test_the_off_switch_announces_itself(monkeypatch, bus_lines):
    monkeypatch.setenv(dawn_park_mod.NO_DAWN_PARK_ENV_VAR, "1")
    d = DawnPark(FakeHub(), FakeEngine())
    d.start()
    assert d._task is None, "disabled means no task at all"
    assert _said(bus_lines, "DISABLED"), (
        "a safety net that is off must say so; silence reads as working")
