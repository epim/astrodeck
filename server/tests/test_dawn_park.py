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
        # Dropped-link recovery (#209): the hub keeps holding a device whose
        # transport died, so `connected` can go False on a live object.
        self.connect_calls = 0
        self.connect_error: Exception | None = None

    async def connect(self) -> None:
        self.connect_calls += 1
        if self.connect_error is not None:
            raise self.connect_error
        self.connected = True

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
        #: Every ``warm_camera`` call this hub was asked to make, and what it
        #: should answer with (or raise). The dawn tick releases the cooler
        #: once the night is over; see ``DawnPark._release_cooler``.
        self.warm_calls: list[str] = []
        self.warm_note: str | None = None
        self.warm_error: Exception | None = None

    def bump_motion_epoch(self) -> None:
        self.epoch_bumps += 1

    def busy_lanes(self) -> list[str]:
        return list(self.lanes)

    async def warm_camera(self, *, source: str = "user", ramp: bool = True):
        if self.warm_error is not None:
            raise self.warm_error
        self.warm_calls.append(source)
        return {"note": self.warm_note} if self.warm_note else {"active": True}


class FakeCam:
    """A camera that may or may not have a cooler to release."""

    def __init__(self, *, connected: bool = True, can_cool: bool = True) -> None:
        self.connected = connected
        self.can_cool = can_cool


class FakeEngine:
    def __init__(self, running: bool = False) -> None:
        self.running = running


@pytest.fixture(autouse=True)
def nothing_armed(monkeypatch):
    """Every test here runs with NO session armed unless it says otherwise.

    The real answer comes from the session store on disk, and the developer's
    own captures/sessions may well hold an armed dormant session -- which
    would make the warm tests pass on one machine and fail on another for a
    reason that has nothing to do with the code under test."""
    import astrodeck.sequence.session as sess
    monkeypatch.setattr(sess.session_store, "armed", lambda: None)


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


# ------------------------------------------- dropped link + log volume (#209/#210)
#
# THE OUTAGE. 2026-08-09, from captures/logs/2026-08-08.jsonl: the mount's
# serial link was abandoned at 02:38:26 and this net began failing at 05:50:48.
# It then ran 139 consecutive ticks — two hours and eighteen minutes, the Sun
# climbing from -5.9 deg to +20.1 deg — emitting the same THREE lines every
# minute and never once attempting the reconnect that fixed it in one call at
# 08:08. 417 lines, on a 200-entry ring: by the time anybody read the drawer,
# the line naming the root cause had been pushed out by the retries.

async def test_a_dropped_mount_link_is_reopened_rather_than_retried_forever(
        cfg, bus_lines):
    """#209. The action nothing took.

    A telescope OBJECT reporting not-connected is a link that died under us —
    the hub only holds devices it once opened. Reopening it is the single thing
    that fixes that state, and it must be tried BEFORE the net gives up for the
    tick, or the mount tracks into the daylight while the log fills up."""
    hub = FakeHub()
    tel = FakeTel(hub=hub)
    hub.devices["telescope"] = tel
    tel._hub = hub
    tel.connected = False                    # the 02:38:26 state
    ts, alt = _daytime()
    assert alt > park_threshold_deg(cfg), "precondition: the Sun is really up"
    assert not tel.parked, "precondition: the mount is tracking, not parked"

    await DawnPark(hub, FakeEngine(), clock=lambda: ts).tick()

    assert tel.connect_calls == 1, "the link must be reopened, not merely retried"
    assert tel.park_calls == 1 and tel.parked is True, (
        "and the park must then actually happen — a reconnect that does not "
        "lead to a park leaves the mount exactly where it was")
    assert _said(bus_lines, "reopened the mount's link"), bus_lines


async def test_a_mount_that_is_simply_switched_off_does_not_park_or_crash(
        cfg, bus_lines):
    """The other side of the same branch. A reopen that fails is an ordinary
    failed attempt: reported once, retried next tick, never fatal."""
    hub = FakeHub()
    tel = FakeTel(hub=hub)
    hub.devices["telescope"] = tel
    tel.connected = False
    tel.connect_error = OSError("cannot open COM3: the device is not present")
    ts, _alt = _daytime()

    await DawnPark(hub, FakeEngine(), clock=lambda: ts).tick()

    assert tel.park_calls == 0, "nothing to park through a link that will not open"
    assert _said(bus_lines, "DAWN PARK FAILED"), bus_lines


async def test_a_failing_net_stays_legible_instead_of_flooding_the_log(
        cfg, bus_lines):
    """#210. 139 ticks must not become 417 lines.

    The ring the operator actually reads holds 200 entries. A net that writes
    three lines a minute erases the evidence of its own root cause inside an
    hour — which is exactly what happened, and why the 02:38:26 link-abandon
    line was gone by morning. One line per fault, then a heartbeat."""
    hub = FakeHub()
    tel = FakeTel(hub=hub)
    hub.devices["telescope"] = tel
    tel._hub = hub
    tel.park_error = RuntimeError("Gps read failed: link not open")
    tel.query_error = RuntimeError("Gps read failed: link not open")
    ts, _alt = _daytime()

    d = DawnPark(hub, FakeEngine(), clock=lambda: ts)
    for _ in range(dawn_park_mod.FAIL_LOG_EVERY - 1):
        await d.tick()

    assert tel.park_calls == dawn_park_mod.FAIL_LOG_EVERY - 1, (
        "precondition: it really did keep TRYING — quiet must mean quiet "
        "logging, never a net that stopped working")
    assert len(bus_lines) <= 3, (
        f"29 failing ticks wrote {len(bus_lines)} lines; the old code wrote "
        f"87 of them: {bus_lines}")

    # ...and it is not silent forever: the heartbeat carries the count and the
    # elapsed time, so an operator reading at 08:00 learns it has been failing
    # since 05:50 rather than seeing one stale line.
    await d.tick()
    beats = [m for _l, m, _s in bus_lines if "still failing after" in m]
    assert len(beats) == 1, f"expected one heartbeat: {bus_lines}"
    assert f"attempt {dawn_park_mod.FAIL_LOG_EVERY}" in beats[0], beats


async def test_a_recovered_net_says_so_and_rearms_the_counter(cfg, bus_lines):
    """A streak that ends must be announced, or the log's last word on the
    subject is a failure that is no longer true."""
    hub = FakeHub()
    tel = FakeTel(hub=hub)
    hub.devices["telescope"] = tel
    tel._hub = hub
    tel.park_error = RuntimeError("Gps read failed: link not open")
    ts, _alt = _daytime()

    d = DawnPark(hub, FakeEngine(), clock=lambda: ts)
    await d.tick()
    await d.tick()
    assert d._fail_count == 2, "precondition: a streak is running"

    tel.park_error = None
    await d.tick()

    assert tel.parked is True
    assert _said(bus_lines, "recovered after 2 failed attempts"), bus_lines


# ------------------------------------------------- releasing the cooler
#
# The wind-down stopped warming the camera when another session is armed and
# tonight's window is still open (2026-09-08), so the resumed run does not
# wait for the TEC to walk back down. When that resume never happens, nothing
# else would ever release the cooler -- and a sensor held at -10 inside a
# warm enclosure on a 40 C day is a condensation risk, not just wasted power.
# The same tick that decides "the night is over and nobody is using this rig"
# is the net.


async def _idle_daytime_rig(*, parked: bool = False, cam=None):
    hub = FakeHub()
    tel = FakeTel(hub=hub)
    tel.parked = parked
    hub.devices["telescope"] = tel
    tel._hub = hub
    if cam is not None:
        hub.devices["camera"] = cam
    ts, _alt = _daytime()
    return hub, tel, ts


async def test_the_cooler_is_released_once_the_mount_is_parked(cfg, bus_lines):
    hub, tel, ts = await _idle_daytime_rig(cam=FakeCam())

    await DawnPark(hub, FakeEngine(), clock=lambda: ts).tick()

    assert tel.park_calls == 1, "precondition: this is the parking path"
    assert hub.warm_calls == ["dawn"], (
        "the night ended with nothing armed that will use this camera, so the "
        "cooler must not be left holding its setpoint through the day")
    assert _said(bus_lines, "warming it"), bus_lines


async def test_an_already_parked_rig_still_gets_its_cooler_released(cfg):
    """The mount being parked already says nothing about the cooler: the run
    that parked it may well be the one that deliberately skipped its warm."""
    hub, tel, ts = await _idle_daytime_rig(parked=True, cam=FakeCam())

    await DawnPark(hub, FakeEngine(), clock=lambda: ts).tick()

    assert tel.park_calls == 0, "precondition: nothing to park"
    assert hub.warm_calls == ["dawn"]


async def test_a_camera_with_no_cooler_is_not_asked(cfg):
    hub, _tel, ts = await _idle_daytime_rig(cam=FakeCam(can_cool=False))

    await DawnPark(hub, FakeEngine(), clock=lambda: ts).tick()

    assert hub.warm_calls == []


async def test_a_disconnected_camera_is_not_asked(cfg):
    hub, _tel, ts = await _idle_daytime_rig(cam=FakeCam(connected=False))

    await DawnPark(hub, FakeEngine(), clock=lambda: ts).tick()

    assert hub.warm_calls == []


async def test_no_camera_at_all_is_not_an_error(cfg, bus_lines):
    hub, tel, ts = await _idle_daytime_rig()

    await DawnPark(hub, FakeEngine(), clock=lambda: ts).tick()

    assert tel.parked is True, "the park must still have happened"
    assert hub.warm_calls == []


async def test_a_cooler_that_refuses_does_not_unpark_the_mount(cfg, bus_lines):
    """A watchdog that raises is worse than none. The park has already
    happened by this point and must stand, with the failure said out loud."""
    hub, tel, ts = await _idle_daytime_rig(cam=FakeCam())
    hub.warm_error = RuntimeError("cooler link down")

    await DawnPark(hub, FakeEngine(), clock=lambda: ts).tick()

    assert tel.parked is True
    assert _said(bus_lines, "could not release the cooler"), bus_lines
    assert _said(bus_lines, "still holding its setpoint"), (
        "the log has to say what state the rig was left in, not just that a "
        "call failed")


async def test_a_camera_already_at_ambient_says_so_rather_than_claiming_a_warm(cfg, bus_lines):
    hub, _tel, ts = await _idle_daytime_rig(cam=FakeCam())
    hub.warm_note = "already at ambient"

    await DawnPark(hub, FakeEngine(), clock=lambda: ts).tick()

    assert hub.warm_calls == ["dawn"]
    assert _said(bus_lines, "needed no action"), bus_lines
    assert not _said(bus_lines, "warming it"), (
        "a line claiming a warm that never started is the kind of log that "
        "makes the next morning undiagnosable")


async def test_a_running_sequence_keeps_its_own_cooler(cfg):
    """Hands off means hands off: a live run owns the camera, and its own
    wind-down is what decides whether that camera warms."""
    hub, _tel, ts = await _idle_daytime_rig(cam=FakeCam())

    await DawnPark(hub, FakeEngine(running=True), clock=lambda: ts).tick()

    assert hub.warm_calls == []


async def test_it_does_not_warm_while_it_is_still_night(cfg):
    hub = FakeHub()
    tel = FakeTel(hub=hub)
    hub.devices["telescope"] = tel
    hub.devices["camera"] = FakeCam()
    tel._hub = hub
    ts, _alt = _night()

    await DawnPark(hub, FakeEngine(), clock=lambda: ts).tick()

    assert hub.warm_calls == []


async def test_an_armed_session_keeps_its_cooler(cfg, bus_lines, monkeypatch):
    """MEASURED ON THE FIRST LIVE TICK, 2026-09-08 16:59: a deploy restarted the
    rig with the Sun up, the net warmed the camera "because no run is going to
    use it tonight", and NGC 604 was armed to resume at dusk. An armed session
    IS a run that is going to use this camera."""
    hub, tel, ts = await _idle_daytime_rig(cam=FakeCam())
    import astrodeck.sequence.session as sess
    armed = type("S", (), {"name": "NGC 604 - LRGB+SHO cycle"})()
    monkeypatch.setattr(sess.session_store, "armed", lambda: armed)

    await DawnPark(hub, FakeEngine(), clock=lambda: ts).tick()

    assert tel.parked is True, "the mount is still parked: only the cooler differs"
    assert hub.warm_calls == [], "an armed session's camera stays cold"
    assert _said(bus_lines, "NGC 604"), (
        "the line has to name the session the cooler is being kept for")
    assert not _said(bus_lines, "warming it")


async def test_a_session_store_that_raises_reads_as_nothing_armed(cfg, monkeypatch):
    """Bookkeeping must not decide a cooler's fate by crashing: an unreadable
    store means the pre-2026-09-08 behaviour, which warms."""
    import astrodeck.sequence.session as sess

    def boom():
        raise RuntimeError("store unreadable")
    monkeypatch.setattr(sess.session_store, "armed", boom)
    hub, _tel, ts = await _idle_daytime_rig(cam=FakeCam())

    await DawnPark(hub, FakeEngine(), clock=lambda: ts).tick()

    assert hub.warm_calls == ["dawn"]
