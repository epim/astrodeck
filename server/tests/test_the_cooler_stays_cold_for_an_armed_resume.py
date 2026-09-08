"""Do not warm the camera when another session is armed to shoot tonight.

MEASURED 2026-09-08. The NGC 7331 run ended at 04:18 with
``warm_cooler_when_done`` set, so the wind-down started the warm ramp. The NGC
604 session was armed behind it; auto-resume picked it up minutes later, and
that run then sat waiting for the TEC to walk back from -7 to -10 before it
could take its first frame. The wind-down had undone, unasked, the one piece of
rig state the next run needed most.

The fix asks the SAME question the resume tick asks — is a session armed, and is
tonight's window still open (``resume_arm.window_open``) — because two copies of
"is the night still on" drift, and they drift silently. When the night really is
over, the warm runs exactly as it always did.

Park is not touched by any of this: the mount is stowed between runs regardless
of what happens next, and the test below says so.
"""
from __future__ import annotations

import types

import pytest

import astrodeck.hub as hub_module
from astrodeck.config import Site, config_store
from astrodeck.devices.base import Camera, CameraFrame, DeviceError
from astrodeck.hub import Hub
from astrodeck.sequence import schedule
from astrodeck.sequence import resume_arm as resume_arm_mod
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence.session import Session, session_store

# A real mid-latitude site with a proper June night — the same one
# test_resume_arm_daylight.py drives the window predicate against.
SITE = Site(name="test", latitude=40.0, longitude=-105.0, is_default=False)
JUNE_NOON = 1_781_524_800.0        # 2026-06-15 12:00 UTC (05:00 local)


class _Cam(Camera):
    def __init__(self):
        super().__init__("Fake Cooled Cam")
        self.connected = True
        self.can_cool = True
        self.calls: list[tuple[bool, float | None]] = []

    async def expose(self, seconds, gain, offset, binning=1, light=True,
                     save=False, target="") -> CameraFrame:   # pragma: no cover
        raise DeviceError("not used")

    async def abort_exposure(self) -> None:                   # pragma: no cover
        return None

    async def connect(self) -> None:                          # pragma: no cover
        self.connected = True

    async def disconnect(self) -> None:                       # pragma: no cover
        self.connected = False

    async def set_cooler(self, on: bool, target_c: float | None = None) -> None:
        self.calls.append((on, target_c))

    async def get_temperature(self) -> float | None:
        return -10.0


class _Mount:
    connected = True

    def __init__(self):
        self.parked = 0

    async def park(self) -> None:
        self.parked += 1


def _plan() -> SequencePlan:
    return SequencePlan(name="NGC 604", targets=[Target(
        name="NGC 604", ra_hours=1.5648, dec_deg=30.66,
        steps=[ExposureStep(filter="L", exposure_s=60, count=10)])])


def _night():
    twilight = config_store.cfg().safety.twilight_deg
    site = {"latitude": SITE.latitude, "longitude": SITE.longitude,
            "is_default": False}
    return schedule.observing_night(site, twilight, JUNE_NOON)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """A hub whose camera records its cooler commands, at a real site, with an
    isolated session store and a clock the test moves.

    The clock is patched at ``resume_arm.time`` rather than globally:
    ``resume_expected_tonight`` is the only thing in this test that asks what
    time it is, and a wind-down is full of other code that must keep the real
    one (asyncio timeouts, the report).
    """
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    cfg = config_store.cfg()
    before = cfg.site
    cfg.site = SITE
    clock = {"t": sum(_night()) / 2}          # the middle of the night
    monkeypatch.setattr(resume_arm_mod, "time",
                        types.SimpleNamespace(time=lambda: clock["t"]))
    h = Hub()
    cam = _Cam()
    h.devices["camera"] = cam
    warms: list[dict] = []

    async def warm_camera(**kw):
        warms.append(kw)
        return {"active": True, "ramped": True, "start_c": -10.0,
                "ambient_c": 15.0, "rate_c_per_min": 2.0}

    monkeypatch.setattr(h, "warm_camera", warm_camera)
    try:
        yield h, SequenceEngine(h), warms, clock
    finally:
        cfg.site = before


def _arm(name: str = "NGC 604") -> Session:
    s = Session(name=name, created_ts=1.0, updated_ts=1.0, status="dormant",
                plan=_plan(), auto_resume=True)
    session_store.save(s)
    return s


# --------------------------------------------------------------- the decision

async def test_it_warms_when_nothing_is_armed(rig):
    """The ordinary end of an ordinary night: nothing is coming, so the sensor
    comes back to ambient on a controlled ramp, as it always has."""
    _h, engine, warms, _clock = rig
    await engine._wind_down(park=False, warm=True)
    assert warms == [{"source": "wind-down"}]


async def test_it_does_not_warm_while_an_armed_session_still_has_tonight(
        rig, bus_lines):
    """The 04:18 case. The resume tick is about to start this session; warming
    now only makes it wait for the TEC on the way back."""
    _h, engine, warms, _clock = rig
    _arm()
    await engine._wind_down(park=False, warm=True)
    assert warms == [], "it warmed a camera that is about to shoot again"
    said = [m for lv, m, _s in bus_lines
            if lv == "info" and "cooler" in m and "setpoint" in m]
    assert len(said) == 1, said
    assert "NGC 604" in said[0], said[0]
    assert "window is still open" in said[0], said[0]


async def test_it_warms_when_the_night_is_over(rig):
    """A run ending BECAUSE the sky ran out. The armed session cannot resume
    until tomorrow, so holding a TEC at -10 all day would buy nothing."""
    _h, engine, warms, clock = rig
    _arm()
    _dusk, dawn = _night()
    clock["t"] = dawn + 90 * 60          # the hour the 2026-08-11 defect ran at
    await engine._wind_down(park=False, warm=True)
    assert warms == [{"source": "wind-down"}]


async def test_a_disarmed_session_does_not_hold_the_cooler(rig):
    """Dormant is half of what ``armed()`` asks for. A session the operator
    stopped by hand is not going to resume, and must not keep the TEC running.
    """
    _h, engine, warms, _clock = rig
    s = _arm()
    s.auto_resume = False
    session_store.save(s)
    await engine._wind_down(park=False, warm=True)
    assert warms == [{"source": "wind-down"}]


async def test_a_run_that_was_not_going_to_warm_still_does_not(rig, bus_lines):
    """``warm=False`` is a decision the caller already made; the skip must not
    announce itself over the top of it."""
    _h, engine, warms, _clock = rig
    _arm()
    await engine._wind_down(park=False, warm=False)
    assert warms == []
    assert not [m for _lv, m, _s in bus_lines if "setpoint" in m]


async def test_the_park_happens_either_way(rig):
    """Park is about the mount, not about what shoots next. Skipping the warm
    must not skip, delay or duplicate it."""
    h, engine, warms, _clock = rig
    tel = _Mount()
    h.devices["telescope"] = tel
    _arm()
    await engine._wind_down(park=True, warm=True)
    assert tel.parked == 1 and warms == []


# ------------------------------------------------------ the predicate itself

def test_an_unknown_site_warms_rather_than_holding_the_cooler_all_day(rig):
    """The one place this deliberately does NOT follow the resume tick.

    ``dark_enough`` fails OPEN for a default site so auto-resume is not stood
    down by "we cannot tell where you are". Here the two mistakes are not the
    same size: warming a camera that is about to shoot costs it a few minutes
    of cooling, while holding a TEC at -10 through a whole day because we could
    not tell whether it was night costs power, dew and an unattended cooler
    nobody asked to leave running.
    """
    h, _engine, _warms, _clock = rig
    _arm()
    assert resume_arm_mod.resume_expected_tonight(h) is not None
    cfg = config_store.cfg()
    cfg.site = Site()                     # is_default, lat/lon 0
    assert resume_arm_mod.resume_expected_tonight(h) is None


def test_the_predicate_names_the_session_it_found(rig):
    h, _engine, _warms, clock = rig
    assert resume_arm_mod.resume_expected_tonight(h) is None
    _arm("Sh2-155")
    found = resume_arm_mod.resume_expected_tonight(h)
    assert found is not None and found.name == "Sh2-155"
    # …and an explicit clock beats the module one, which is what lets the
    # wind-down and the tick be asked the same question about the same instant.
    _dusk, dawn = _night()
    assert resume_arm_mod.resume_expected_tonight(h, dawn + 5 * 3600) is None


def test_the_tick_and_the_wind_down_share_one_predicate(rig):
    """A second copy of "is the night still on" would drift, silently."""
    h, _engine, _warms, clock = rig
    s = _arm()
    twilight = config_store.cfg().safety.twilight_deg
    arm = resume_arm_mod.ResumeArm(engine=None, hub=h, clock=lambda: clock["t"])
    _dusk, dawn = _night()
    for t in (clock["t"], dawn + 90 * 60, dawn + 5 * 3600):
        assert arm._window_open(s, t) is resume_arm_mod.window_open(
            s, h.site, twilight, t)
