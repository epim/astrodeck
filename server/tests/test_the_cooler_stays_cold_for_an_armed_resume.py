# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Do not warm the camera when another session is armed to shoot tonight.

MEASURED 2026-09-08. The NGC 7331 run ended with
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

#887: an armed session with nothing left to shoot tonight is not "about to
shoot again". The run that ended because its only target was set aside for the
night armed that very session, and its window was still open, so the wind-down
held the TEC at its setpoint until dawn for a run the resume tick refuses
(``NOTHING_TONIGHT``). The decision now also asks the tick's readiness check;
the cases at the end of this file grade it, a control beside each.
"""
from __future__ import annotations

import types

import pytest

import astrodeck.hub as hub_module
from astrodeck.config import Site, config_store
from astrodeck.devices.base import Camera, CameraFrame, DeviceError
from astrodeck.events import night_key
from astrodeck.hub import Hub
from astrodeck.sequence import schedule
from astrodeck.sequence import resume_arm as resume_arm_mod
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.group_rules import CENTRING
from astrodeck.sequence.models import (ExposureStep, Schedule, SequencePlan,
                                       Target, TargetGroup)
from astrodeck.sequence.session import Session, SessionFrame, session_store

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


# --------------------------------------- an armed session with nothing left

def _high(name: str = "Cepheus field", **kw) -> Target:
    """A target that stays above 30 degrees all night at SITE, so that nothing
    but a set-aside (or a start floor it cannot clear) keeps it from being
    shot."""
    return Target(name=name, ra_hours=12.0, dec_deg=80.0, steps=[
        ExposureStep(filter="L", exposure_s=60, count=2)], **kw)


def _arm_with(targets, *, at, groups=(), aside=(), banked=(), record_at=None,
              **record) -> Session:
    """An armed session of ``targets`` whose ``aside`` targets are set aside
    for the night ``record_at`` (default ``at``) falls in (``record``: the kind
    and clock time the engine writes with it) and whose ``banked`` targets are
    complete."""
    s = Session(name="Mosaic", created_ts=1.0, updated_ts=1.0,
                status="dormant", auto_resume=True,
                plan=SequencePlan(name="Mosaic", targets=list(targets),
                                  groups=list(groups)))
    for t in banked:
        for _ in range(2):
            s.frames.append(SessionFrame(ts=at - 3600.0, night="n1",
                                         target_id=t.id,
                                         step_id=t.steps[0].id))
    for t in aside:
        s.note_set_aside(t.id, "held for 6 passes in a row",
                         night=night_key(at if record_at is None else record_at),
                         **record)
    session_store.save(s)
    return s


def _held_back(bus_lines) -> list[str]:
    """The wind-down's "leaving the cooler at its setpoint" lines."""
    return [m for lv, m, _s in bus_lines
            if lv == "info" and "armed to resume" in m]


async def test_it_warms_when_the_armed_sessions_only_target_is_set_aside(
        rig, bus_lines):
    """#887, the 2026-10-09 shape: a run ended because its only target was set
    aside for the night, and the session it armed is the one the wind-down
    took for "about to resume". The tick refuses it until the next night, so
    holding the TEC at its setpoint buys no run. The sentence that claimed a
    resume must not be logged either (a claim nothing keeps)."""
    _h, engine, warms, clock = rig
    only = _high()
    _arm_with([only], at=clock["t"], aside=[only], kind="group", ts=clock["t"])
    await engine._wind_down(park=False, warm=True)
    assert warms == [{"source": "wind-down"}]
    assert _held_back(bus_lines) == []


async def test_it_holds_the_cooler_while_one_target_can_still_run(
        rig, bus_lines):
    """The control: the same set-aside beside a target that still owes light
    and is not set aside. The session can run tonight, so the cooler stays."""
    _h, engine, warms, clock = rig
    gone, left = _high("Set aside"), _high("Still owed")
    _arm_with([gone, left], at=clock["t"], aside=[gone], kind="group",
              ts=clock["t"])
    await engine._wind_down(park=False, warm=True)
    assert warms == []
    assert len(_held_back(bus_lines)) == 1


async def test_a_set_aside_from_another_night_does_not_warm(rig, bus_lines):
    """Set-aside is for the NIGHT it was made on (spec 3.4); yesterday's
    record is history, so tonight the target is owed and the cooler stays."""
    _h, engine, warms, clock = rig
    only = _high()
    _arm_with([only], at=clock["t"], aside=[only], kind="group",
              ts=clock["t"] - 86400.0, record_at=clock["t"] - 86400.0)
    await engine._wind_down(park=False, warm=True)
    assert warms == []
    assert len(_held_back(bus_lines)) == 1


def _mosaic(cols: int = 2):
    group = TargetGroup(id="g-mosaic", name="Mosaic",
                        geometry={"rows": 1, "cols": cols})
    panels = [Target(name=f"Panel 1-{c + 1}", ra_hours=12.0 + 0.05 * c,
                     dec_deg=80.0, mosaic_group=group.id, panel_row=0,
                     panel_col=c,
                     steps=[ExposureStep(filter="L", exposure_s=60, count=2)])
              for c in range(cols)]
    return group, panels


async def test_it_warms_when_every_panel_of_the_mosaic_is_set_aside(
        rig, bus_lines):
    """The mosaic the issue names: each panel carries its own whole-panel
    record, written by the group driver, and none is left to shoot."""
    _h, engine, warms, clock = rig
    group, panels = _mosaic()
    _arm_with(panels, at=clock["t"], groups=[group], aside=panels,
              kind="group", ts=clock["t"])
    await engine._wind_down(park=False, warm=True)
    assert warms == [{"source": "wind-down"}]
    assert _held_back(bus_lines) == []


async def test_it_holds_the_cooler_while_one_panel_of_the_mosaic_is_live(
        rig, bus_lines):
    """The control: one panel set aside, the other still owed."""
    _h, engine, warms, clock = rig
    group, panels = _mosaic()
    _arm_with(panels, at=clock["t"], groups=[group], aside=panels[:1],
              kind="group", ts=clock["t"])
    await engine._wind_down(park=False, warm=True)
    assert warms == []
    assert len(_held_back(bus_lines)) == 1


async def test_it_warms_when_every_owed_step_of_the_target_is_set_aside(
        rig, bus_lines):
    """A target whose owed steps each carry a step-level record (the reject
    guard's) has nothing left tonight; one owed step still without one does."""
    _h, engine, warms, clock = rig
    t = Target(name="Two filters", ra_hours=12.0, dec_deg=80.0, steps=[
        ExposureStep(filter="L", exposure_s=60, count=2),
        ExposureStep(filter="R", exposure_s=60, count=2)])
    s = _arm_with([t], at=clock["t"])
    night = night_key(clock["t"])
    s.note_set_aside(t.id, "rejects", night=night, step_id=t.steps[0].id)
    session_store.save(s)
    await engine._wind_down(park=False, warm=True)
    assert warms == [], "one step is still owed tonight"
    s.note_set_aside(t.id, "rejects", night=night, step_id=t.steps[1].id)
    session_store.save(s)
    await engine._wind_down(park=False, warm=True)
    assert warms == [{"source": "wind-down"}]
    assert len(_held_back(bus_lines)) == 1       # the first call's, only


async def test_it_warms_when_the_owing_target_never_clears_its_start_floor(
        rig, bus_lines):
    """A target past its floor is dropped by the run unshot, as set aside is.
    Beside a COMPLETE target whose window is open (so ``window_open`` is
    true), the session owes only what cannot be shot."""
    _h, engine, warms, clock = rig
    done = _high("Done")
    cannot = _high("Floor 80", schedule=Schedule(min_altitude_deg=80.0))
    _arm_with([done, cannot], at=clock["t"], banked=[done])
    await engine._wind_down(park=False, warm=True)
    assert warms == [{"source": "wind-down"}]
    assert _held_back(bus_lines) == []


async def test_it_holds_the_cooler_when_the_owing_target_clears_its_floor(
        rig, bus_lines):
    """The control: the same shape with a floor the target does clear."""
    _h, engine, warms, clock = rig
    done = _high("Done")
    can = _high("Floor 30", schedule=Schedule(min_altitude_deg=30.0))
    _arm_with([done, can], at=clock["t"], banked=[done])
    await engine._wind_down(park=False, warm=True)
    assert warms == []
    assert len(_held_back(bus_lines)) == 1


async def test_owed_calibration_keeps_the_cooler_for_the_tick_to_start(
        rig, bus_lines):
    """The tick does not refuse calibration-only work (it starts it), so a
    set-aside light target beside owed darks is a session about to resume."""
    _h, engine, warms, clock = rig
    only = _high()
    darks = Target(name="Darks", ra_hours=0.0, dec_deg=0.0, calibration=True,
                   steps=[ExposureStep(filter="Dark", exposure_s=60, count=5)])
    _arm_with([only, darks], at=clock["t"], aside=[only], kind="group",
              ts=clock["t"])
    await engine._wind_down(park=False, warm=True)
    assert warms == []
    assert len(_held_back(bus_lines)) == 1


async def test_a_centring_set_aside_that_expires_tonight_keeps_the_cooler(
        rig, bus_lines):
    """A panel set aside FOR NOW (centring, 45 minutes) is tried again tonight
    by the tick, so its session can still run and must not be warmed under.
    The control for the expiry cases below: the same session, the same record,
    with the sky open at the moment it expires."""
    _h, engine, warms, clock = rig
    only = _high()
    _arm_with([only], at=clock["t"], aside=[only], kind=CENTRING,
              ts=clock["t"] - 600.0)
    await engine._wind_down(park=False, warm=True)
    assert warms == []
    assert len(_held_back(bus_lines)) == 1


async def test_a_centring_set_aside_that_expires_after_dawn_warms(rig):
    """Expiring is only a reason to wait while the sky is still open when it
    does: ten minutes before dawn, a record that frees its panel 45 minutes
    later frees it into daylight."""
    _h, engine, warms, clock = rig
    _dusk, dawn = _night()
    clock["t"] = dawn - 600.0
    only = _high()
    _arm_with([only], at=clock["t"], aside=[only], kind=CENTRING,
              ts=clock["t"])
    await engine._wind_down(park=False, warm=True)
    assert warms == [{"source": "wind-down"}]


async def test_a_centring_set_aside_that_already_expired_once_warms(rig):
    """At most one expiry per panel per night (`set_aside_expiry`): the
    second centring set-aside stands for the rest of it, so nothing is
    waiting to free the panel."""
    _h, engine, warms, clock = rig
    only = _high()
    s = _arm_with([only], at=clock["t"], aside=[only], kind=CENTRING,
                  ts=clock["t"] - 4000.0)
    night = night_key(clock["t"])
    s.note_set_aside_expired(only.id, night=night)
    s.note_set_aside(only.id, "centring again", night=night, kind=CENTRING,
                     ts=clock["t"] - 600.0)
    session_store.save(s)
    await engine._wind_down(park=False, warm=True)
    assert warms == [{"source": "wind-down"}]


def test_the_wind_down_and_the_tick_ask_the_same_readiness_question(rig):
    """The tick refuses a session as ``NOTHING_TONIGHT`` by
    ``recentre_candidates`` and ``nothing_to_shoot_tonight`` (the pair
    ``ResumeArm._recover`` asks). Whichever way a session answers there, the
    wind-down's predicate answers the same: no session expected exactly when
    the tick refuses, none when it would start."""
    h, _engine, _warms, clock = rig
    twilight = config_store.cfg().safety.twilight_deg
    arm = resume_arm_mod.ResumeArm(engine=None, hub=h, clock=lambda: clock["t"])
    a, b = _high("A"), _high("B")
    for aside, refused in (([a, b], True), ([a], False), ([], False)):
        s = _arm_with([a, b], at=clock["t"], aside=aside, kind="group",
                      ts=clock["t"])
        t = clock["t"]
        asked = resume_arm_mod.nothing_to_shoot_tonight(
            s, resume_arm_mod.recentre_candidates(
                s, night_key(t), arm._walk(s, t), site=h.site,
                twilight_deg=twilight, now=t))
        assert asked is refused
        expected = resume_arm_mod.resume_expected_tonight(h)
        assert (expected is None) is refused
