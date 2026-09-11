"""A guide loop with nobody driving it is a motor turning on nobody's behalf.

2026-09-11, the four and a half hours this exists to prevent: the operator
paused the run at 00:40. A guider calibration that had already been started
completed at 00:50:37 and began guiding. From 04:56 to 05:29 the star washed
out in the brightening sky and the guider re-locked twelve times, each onto a
star 530 to 6141 arcsec away, walking the mount about 64 degrees, down to ten
degrees of altitude. Nothing stopped it, because every guard that could have --
the re-lock rate gate, the pointing checks, the tracking enforcement -- lives in
the sequence engine's per-frame loop, and a paused run takes no frames.

THE RULE THESE TESTS ENCODE: a safety check must be driven by the clock of the
HAZARD it guards, never by the progress of the work. So this one runs on the
wall clock, every minute, all night, and it does not care whether a run exists.

WHAT THESE TESTS MUST NOT DO is assert anything that only holds at dawn. The
check under test sits ABOVE the Sun-altitude gate on purpose; a test that only
exercised it at sunrise would pass just as happily if it were moved back below
that gate, which is exactly the defect being fixed. Every test here drives the
tick at a deep-night Sun altitude and asserts that as a precondition.
"""
from __future__ import annotations

import asyncio

import pytest

from astrodeck.catalog.coords import sun_altaz
from astrodeck.config import AppConfig
from astrodeck.dawn_park import DawnPark, park_threshold_deg

pytestmark = pytest.mark.asyncio

# Not the developer's site. Mid-northern, so it has ordinary nights.
SITE_LAT, SITE_LON = 45.0, -110.0
JUNE_TS = 1780272000.0          # 2026-06-01T00:00:00Z, the instant to scan from


def _deep_night_ts() -> tuple[float, float]:
    """An instant whose Sun is well below the dawn-park threshold, and its
    altitude -- returned so every test can assert it as a PRECONDITION."""
    cfg = AppConfig()
    threshold = park_threshold_deg(cfg)
    t = JUNE_TS
    for _ in range(48 * 60):
        alt, _az = sun_altaz(SITE_LAT, SITE_LON, t)
        if alt < threshold - 5.0:
            return t, alt
        t += 60.0
    raise AssertionError("no deep-night sky within the scan window")


class FakeGuider:
    def __init__(self, *, active: bool = True, connected: bool = True) -> None:
        self.connected = connected
        self._active = active
        self.stop_calls = 0
        self.stop_error: Exception | None = None
        self.active_error: Exception | None = None

    async def is_active(self) -> bool:
        if self.active_error is not None:
            raise self.active_error
        return self._active

    async def stop_guiding(self) -> None:
        self.stop_calls += 1
        if self.stop_error is not None:
            raise self.stop_error
        self._active = False


class FakeHub:
    def __init__(self, guider=None) -> None:
        self.site = {"name": "Ridge Test Site", "latitude": SITE_LAT,
                     "longitude": SITE_LON, "elevation_m": 0.0,
                     "is_default": False, "horizon_min_deg": 10.0}
        self.devices: dict = {}
        self.guider = guider
        self._motion_lock = asyncio.Lock()
        self.lanes: list[str] = []

    def bump_motion_epoch(self) -> None:
        pass

    def busy_lanes(self) -> list[str]:
        return list(self.lanes)


class FakeEngine:
    def __init__(self, running: bool = False, paused: bool = False) -> None:
        self.running = running
        self.paused = paused


class Clock:
    """A hand-wound clock. The check under test measures ten MINUTES; a test
    that reached them by sleeping would take ten minutes."""

    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance_min(self, minutes: float) -> None:
        self.t += minutes * 60.0


def _park(hub, engine, clock, cfg=None):
    return DawnPark(hub, engine, clock=clock)


async def _tick(net, monkeypatch, cfg):
    from astrodeck import dawn_park as mod
    monkeypatch.setattr(mod.config_store, "cfg", lambda: cfg)
    await net.tick()


@pytest.fixture(autouse=True)
def nothing_armed(monkeypatch):
    """No armed session, so the cooler-release path never reaches the store."""
    from astrodeck.sequence import session as session_mod
    monkeypatch.setattr(session_mod.session_store, "armed", lambda: None,
                        raising=False)


# ------------------------------------------------------- the night it happened


async def test_guiding_alone_all_night_is_stopped(monkeypatch):
    """THE NIGHT ITSELF. A paused run, a live guide loop, deep darkness, and
    nobody driving. Ten minutes later the loop stops."""
    ts, alt = _deep_night_ts()
    cfg = AppConfig()
    assert alt < park_threshold_deg(cfg), (
        f"precondition: the Sun must be DOWN ({alt:+.1f} deg) or this test "
        f"proves nothing about a check that has to run at midnight")

    g = FakeGuider(active=True)
    clock = Clock(ts)
    net = _park(FakeHub(g), FakeEngine(running=True, paused=True), clock)

    await _tick(net, monkeypatch, cfg)
    assert g.stop_calls == 0, "the first tick only starts the timer"

    clock.advance_min(5.0)
    await _tick(net, monkeypatch, cfg)
    assert g.stop_calls == 0, "five minutes is inside the ten-minute grace"

    clock.advance_min(6.0)
    await _tick(net, monkeypatch, cfg)
    assert g.stop_calls == 1, (
        "eleven minutes of guiding on a PAUSED run with nobody driving was not "
        "stopped -- this is the 2026-09-11 shape exactly")
    assert g._active is False


async def test_it_fires_with_no_run_at_all(monkeypatch):
    """The other half of the shape: an operator who calibrated from the Guide
    screen and went to bed. There is no run to be paused."""
    ts, _alt = _deep_night_ts()
    cfg = AppConfig()
    g = FakeGuider(active=True)
    clock = Clock(ts)
    net = _park(FakeHub(g), None, clock)

    await _tick(net, monkeypatch, cfg)
    clock.advance_min(11.0)
    await _tick(net, monkeypatch, cfg)
    assert g.stop_calls == 1, "a guide loop with no run behind it ran on"


# --------------------------------------------------- and it is not rude


async def test_a_running_sequence_keeps_its_guider(monkeypatch):
    """A run that is RUNNING is somebody driving. This must never take a guide
    loop away from a sequence that is using it -- that would break every night
    to fix one."""
    ts, _alt = _deep_night_ts()
    cfg = AppConfig()
    g = FakeGuider(active=True)
    clock = Clock(ts)
    net = _park(FakeHub(g), FakeEngine(running=True, paused=False), clock)

    for _ in range(30):                 # half an hour of ticks
        await _tick(net, monkeypatch, cfg)
        clock.advance_min(1.0)
    assert g.stop_calls == 0, "a running sequence had its guider stopped"


async def test_a_person_on_the_mount_keeps_its_guider(monkeypatch):
    """A polar alignment is a person at the tripod with a hand on a bolt."""
    ts, _alt = _deep_night_ts()
    cfg = AppConfig()
    g = FakeGuider(active=True)
    clock = Clock(ts)
    hub = FakeHub(g)
    hub.lanes = ["polar"]
    net = _park(hub, None, clock)

    await _tick(net, monkeypatch, cfg)
    clock.advance_min(30.0)
    await _tick(net, monkeypatch, cfg)
    assert g.stop_calls == 0, "a live polar alignment had its guider stopped"


async def test_the_timer_resets_when_a_run_takes_over(monkeypatch):
    """Nine minutes alone, then a run starts. The clock must start again, or a
    sequence that begins at minute nine loses its guider at minute ten."""
    ts, _alt = _deep_night_ts()
    cfg = AppConfig()
    g = FakeGuider(active=True)
    clock = Clock(ts)
    eng = FakeEngine(running=False)
    net = _park(FakeHub(g), eng, clock)

    await _tick(net, monkeypatch, cfg)
    clock.advance_min(9.0)
    await _tick(net, monkeypatch, cfg)
    assert g.stop_calls == 0

    eng.running = True                  # the run starts
    await _tick(net, monkeypatch, cfg)
    eng.running = False                 # ...and ends a minute later
    clock.advance_min(1.0)
    await _tick(net, monkeypatch, cfg)
    clock.advance_min(5.0)
    await _tick(net, monkeypatch, cfg)
    assert g.stop_calls == 0, (
        "the idle timer was not reset by the run, so a sequence that started "
        "at minute nine would have had its guider pulled at minute ten")


async def test_a_stopped_guider_is_not_hounded(monkeypatch):
    """No guide loop, nothing to stop, and no timer left running behind it."""
    ts, _alt = _deep_night_ts()
    cfg = AppConfig()
    g = FakeGuider(active=False)
    clock = Clock(ts)
    net = _park(FakeHub(g), None, clock)

    await _tick(net, monkeypatch, cfg)
    clock.advance_min(30.0)
    await _tick(net, monkeypatch, cfg)
    assert g.stop_calls == 0
    assert net._guiding_alone_since is None


async def test_an_unreadable_guider_is_left_alone(monkeypatch):
    """`is_active` raising means nobody can say whether it is guiding, and
    stopping something you cannot see is not a safety action."""
    ts, _alt = _deep_night_ts()
    cfg = AppConfig()
    g = FakeGuider(active=True)
    g.active_error = RuntimeError("serial link is wedged")
    clock = Clock(ts)
    net = _park(FakeHub(g), None, clock)

    await _tick(net, monkeypatch, cfg)
    clock.advance_min(30.0)
    await _tick(net, monkeypatch, cfg)
    assert g.stop_calls == 0


# --------------------------------------------------------------- the off switch


async def test_zero_disables_the_check(monkeypatch):
    """0 is off, as everywhere else in this config."""
    ts, _alt = _deep_night_ts()
    cfg = AppConfig()
    cfg.safety.unattended_guide_min = 0.0
    g = FakeGuider(active=True)
    clock = Clock(ts)
    net = _park(FakeHub(g), None, clock)

    await _tick(net, monkeypatch, cfg)
    clock.advance_min(120.0)
    await _tick(net, monkeypatch, cfg)
    assert g.stop_calls == 0, "unattended_guide_min = 0 did not disable it"


async def test_the_limit_is_read_from_config_not_hardcoded(monkeypatch):
    """A configured 45 minutes must not fire at 11. A test that only ever ran
    at the default would pass against a hardcoded 10."""
    ts, _alt = _deep_night_ts()
    cfg = AppConfig()
    cfg.safety.unattended_guide_min = 45.0
    g = FakeGuider(active=True)
    clock = Clock(ts)
    net = _park(FakeHub(g), None, clock)

    await _tick(net, monkeypatch, cfg)
    clock.advance_min(30.0)
    await _tick(net, monkeypatch, cfg)
    assert g.stop_calls == 0, "fired at 30 min against a 45 min limit"
    clock.advance_min(20.0)
    await _tick(net, monkeypatch, cfg)
    assert g.stop_calls == 1, "did not fire at 50 min against a 45 min limit"


# ------------------------------------------------------ a stop that will not


async def test_a_failed_stop_is_retried(monkeypatch):
    """A guider that refuses to stop is worse news than one left running, so
    the attempt has to come back rather than latch off."""
    ts, _alt = _deep_night_ts()
    cfg = AppConfig()
    g = FakeGuider(active=True)
    g.stop_error = RuntimeError("the guide loop will not stop")
    clock = Clock(ts)
    net = _park(FakeHub(g), None, clock)

    await _tick(net, monkeypatch, cfg)
    clock.advance_min(11.0)
    await _tick(net, monkeypatch, cfg)
    assert g.stop_calls == 1
    clock.advance_min(11.0)
    await _tick(net, monkeypatch, cfg)
    assert g.stop_calls == 2, "a failed stop was never retried"


# ---------------------------------------------- it does not ride the dawn gate


async def test_an_unset_site_does_not_disable_it(monkeypatch):
    """The dawn park is INERT without a site, because it cannot know when its
    own Sun rises. This check needs no site at all, and a rig that never
    configured one is exactly the rig most likely to be left guiding.

    This is the test that pins the check ABOVE the site gate; move it back
    below and this fails.
    """
    ts, _alt = _deep_night_ts()
    cfg = AppConfig()
    g = FakeGuider(active=True)
    clock = Clock(ts)
    hub = FakeHub(g)
    hub.site["is_default"] = True       # never configured
    net = _park(hub, None, clock)

    await _tick(net, monkeypatch, cfg)
    clock.advance_min(11.0)
    await _tick(net, monkeypatch, cfg)
    assert g.stop_calls == 1, (
        "the unattended-guiding check is sitting below the site gate, so a rig "
        "with no site configured gets no net at all")
