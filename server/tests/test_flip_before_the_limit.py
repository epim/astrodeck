"""THE MOUNT QUITS BEFORE THE MERIDIAN, SO THE FLIP HAS TO GO FIRST.

Four nights ended at the same place, and every previous attempt at the fix
assumed a German equatorial's meridian limit sits PAST the meridian, so a flip
triggered at the crossing arrives in time. Measured on this AM5 it does not:

    night of 2026-08-20/21  NGC 7129  refused 00:44 local  HA -7.6 min
    night of 2026-08-21/22  NGC 6946  refused 23:35 local  HA -4.7 min

(The nights are labelled by the night, not by the calendar date the clock
happened to read: the 6946 event is 2026-08-21 23:35 LOCAL, and the -4.7 min
hour angle does not reproduce from 2026-08-22 23:35.)

Both EAST of the meridian - i.e. with the countdown still running - and both
times the mount then answered ``:Te#`` with ``0``, which is a limit state and
not a transient. The engine waited for the crossing. The mount always wins.

`test_the_measured_failures_would_now_be_caught` is the regression, named after
the two nights it cost.
"""
from __future__ import annotations

import asyncio
import time

import pytest

import astrodeck.hub as hub_module
from astrodeck.devices.base import PierSide
from astrodeck.devices.sim import build_sim_rig
from astrodeck.focus.autofocus import TrackingLost, run_autofocus
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence import schedule
from astrodeck.sequence.engine import StopTarget
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence.schedule import MERIDIAN_FLIP_LEAD_MIN
from astrodeck.providers import NATIVE_AVAILABLE

#: The five autofocus-sweep tests below drive the sequence engine's REAL
#: autofocus path, which on this rig is the native Rust V-curve engine. With the
#: wheel absent the engine reports "autofocus unavailable: no backend autofocus
#: and the native engine is not installed" and returns False before any sweep
#: runs, so they cannot grade the tracking guard at all -- they ran red on the
#: wheel-less `server` CI job from the day they landed (2026-08-22) while
#: passing on every dev box that has the wheel. Skip them without it; the
#: `native` CI job builds the wheel and runs exactly these five.
native_only = pytest.mark.skipif(
    not NATIVE_AVAILABLE,
    reason="native wheel absent: the sequence autofocus sweep is the native engine",
)


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    monkeypatch.setenv("ASTRODECK_SIM_LEGACY_GUIDER", "1")
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


# --------------------------------------------------------------- flip harness

def _engine_at(sim_hub, monkeypatch, ttf_min: float, *, dec_deg: float = 66.11,
               pier: str = "west", lead_min: float | None = None,
               meridian_flip: bool = True):
    """An engine whose armed target is ``ttf_min`` minutes from transit.

    POSITIVE ``ttf_min`` means the target is still EAST of the meridian and the
    countdown is running - which is where BOTH measured refusals happened.

    The device countdown is pinned to ``None``, which is what the AM5 actually
    answers: an earlier attempt at this fix keyed off that value and was inert
    on the rig for exactly that reason.

    ``_wait_for_flip_point`` is stubbed to a recorder. The hold is a real-time
    sleep of up to one frame window, and its own behaviour is asserted
    separately; here what matters is only WHEN the engine decides to act.
    ``_autofocus`` likewise - the post-flip sweep is not this test's subject.
    """
    lon = sim_hub.site["longitude"]
    ra = (schedule.lst_hours(lon) + ttf_min / 60.0) % 24.0
    t = Target(name="T", ra_hours=ra, dec_deg=dec_deg, center=False,
               autofocus_first=False,
               steps=[ExposureStep(filter="L", exposure_s=0.05, count=1)])
    kw = {} if lead_min is None else {"meridian_flip_lead_min": lead_min}
    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan(targets=[t], meridian_flip=meridian_flip, **kw)
    e._flip_armed = True

    async def _pier():
        return {"east": PierSide.EAST, "west": PierSide.WEST}.get(
            pier, PierSide.UNKNOWN)

    async def _no_countdown():
        return None

    monkeypatch.setattr(sim_hub.devices["telescope"], "pier_side", _pier)
    monkeypatch.setattr(sim_hub.devices["telescope"], "time_to_meridian_flip",
                        _no_countdown)

    record: dict = {"flips": [], "holds": []}

    async def _flip(ra_h, dec_d):
        record["flips"].append((ra_h, dec_d))

    async def _hold(target, lead_s=0.0):
        record["holds"].append(lead_s)

    async def _af(label, **kw):
        return True

    monkeypatch.setattr(sim_hub, "meridian_flip", _flip)
    e._wait_for_flip_point = _hold
    e._autofocus = _af
    return e, t, record


# 1 --------------------------------------------------------------------------

async def test_the_flip_fires_before_the_mount_reaches_its_limit(
        sim_hub, monkeypatch):
    """Walk the countdown in from 30 minutes and find where the engine acts.

    A flip "acted on" is one where the engine either flipped or entered the
    hold that immediately precedes a flip - both mean the decision is made and
    the mount will be moved before the next frame opens.
    """
    acted: dict[float, bool] = {}
    for tenths in range(300, -1, -5):          # 30.0 min down to 0.0, 0.5 steps
        ttf = tenths / 10.0
        e, t, rec = _engine_at(sim_hub, monkeypatch, ttf)
        await e._maybe_meridian_flip(t, next_exposure_s=0.0)
        acted[ttf] = bool(rec["flips"] or rec["holds"])

    first = max((k for k, v in acted.items() if v), default=None)
    assert first is not None, "the engine never flipped anywhere inside 30 min"
    assert first >= MERIDIAN_FLIP_LEAD_MIN, (
        f"the flip is first acted on at {first:.1f} min out, but the lead is "
        f"{MERIDIAN_FLIP_LEAD_MIN:g} min - the mount stops before that")
    # ...and it is not simply "always flip": far out, the engine leaves it alone.
    assert not acted[30.0] and not acted[20.0] and not acted[15.0], (
        "the flip fired half an hour early; the lead is a lead, not a disabler")
    # The measurement everything rests on: both refusals are inside the window.
    for ttf in (7.6, 5.0, 4.7, 2.0, 0.5):
        near = min(acted, key=lambda k: abs(k - ttf))
        assert acted[near], (
            f"no flip at {near:.1f} min before transit - the AM5 refused to "
            f"track at 7.6 and 4.7 min before transit on two separate nights")


# 2 --------------------------------------------------------------------------

@pytest.mark.parametrize("night,name,ha_min", [
    # NIGHTS, NOT DATES. NGC 7129 refused at 00:44 local on 2026-08-21 (the
    # morning end of the night of 08-20/21); NGC 6946 refused at 23:35 local on
    # 2026-08-21, the evening that opened the next night. Recomputed
    # independently: 2026-08-21 23:35 gives HA -4.72 min, and 2026-08-22 23:35
    # gives -0.78 - so the -4.7 the lead is sized against only reproduces from
    # the earlier evening. Getting the label right matters because these two
    # numbers are the entire empirical basis for MERIDIAN_FLIP_LEAD_MIN.
    ("night of 2026-08-20/21", "NGC 7129", -7.6),
    ("night of 2026-08-21/22", "NGC 6946", -4.7),
])
async def test_the_measured_failures_would_now_be_caught(
        sim_hub, monkeypatch, night, name, ha_min):
    """THE REGRESSION, NAMED AFTER THE NIGHTS IT COST.

    ``ha_min`` is the hour angle at which the mount refused to track - negative,
    i.e. EAST of the meridian, i.e. with the countdown still positive. At that
    instant the flip must already have been taken.
    """
    ttf_min = -ha_min                       # countdown = -HA
    e, t, rec = _engine_at(sim_hub, monkeypatch, ttf_min)
    await e._maybe_meridian_flip(t, next_exposure_s=180.0)
    assert rec["flips"], (
        f"{night} {name}: the mount stopped tracking at HA {ha_min:g} min and "
        f"the engine still had not flipped")


# 3 --------------------------------------------------------------------------

async def test_a_zero_lead_restores_the_old_behaviour(sim_hub, monkeypatch):
    """The escape hatch. A mount with a genuinely permissive limit can ask for
    the old crossing-triggered flip DELIBERATELY rather than by accident."""
    e, t, rec = _engine_at(sim_hub, monkeypatch, 5.0, lead_min=0.0)
    await e._maybe_meridian_flip(t, next_exposure_s=0.0)
    assert not rec["flips"] and not rec["holds"], (
        "lead 0 still flipped five minutes early - the opt-out does nothing")

    # ...and with the lead at its default the same countdown DOES flip, so the
    # assertion above is about the setting and not about the geometry.
    e2, t2, rec2 = _engine_at(sim_hub, monkeypatch, 5.0)
    await e2._maybe_meridian_flip(t2, next_exposure_s=0.0)
    assert rec2["flips"], "control: the default lead must flip at 5 min out"

    # A zero lead still flips at the crossing itself.
    e3, t3, rec3 = _engine_at(sim_hub, monkeypatch, -0.2, lead_min=0.0)
    await e3._maybe_meridian_flip(t3, next_exposure_s=0.0)
    assert rec3["flips"], "lead 0 did not flip even past the meridian"


# 4 --------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [-0.1, -1.0, 60.1, 120.0])
def test_the_lead_is_bounded(bad):
    """A lead outside [0, 60] is a typo, and a typo here is a lost night in
    either direction: negative flips after the limit, huge flips at once."""
    with pytest.raises(ValueError):
        SequencePlan(meridian_flip_lead_min=bad)


@pytest.mark.parametrize("ok", [0.0, 10.0, 60.0])
def test_the_bounds_admit_the_useful_range(ok):
    assert SequencePlan(meridian_flip_lead_min=ok).meridian_flip_lead_min == ok


def test_the_default_lead_clears_the_worse_measurement():
    """7.6 minutes is the earliest refusal measured. A lead that does not clear
    it is not a fix."""
    assert MERIDIAN_FLIP_LEAD_MIN >= 7.6, (
        f"a {MERIDIAN_FLIP_LEAD_MIN:g} min lead does not clear the 7.6 min "
        "refusal measured on NGC 7129")
    assert SequencePlan().meridian_flip_lead_min == MERIDIAN_FLIP_LEAD_MIN


# 5, 6 -----------------------------------------------------------------------

async def _armed_after_setup(sim_hub, ttf_h: float) -> bool:
    """Run the REAL `_setup_target` and report the arming latch it left.

    Through the call site on purpose: the latch gates the decline log as well
    as the flip, so a trigger that moves without its arming produces a silent
    skip - which is exactly how two nights produced no flip log at all.
    """
    lon = sim_hub.site["longitude"]
    ra = (schedule.lst_hours(lon) + ttf_h) % 24.0
    t = Target(name="T", ra_hours=ra, dec_deg=66.11, center=False,
               autofocus_first=False,
               steps=[ExposureStep(filter="L", exposure_s=0.05, count=1)])
    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan(targets=[t], meridian_flip=True, guide=False)
    e._cfg = None                       # no safety monitor in this unit
    await e._setup_target(0, t)
    return e._flip_armed


async def test_a_target_starting_inside_the_lead_window_still_arms(sim_hub):
    """Acquired five minutes before transit - inside the lead - so the flip is
    owed at the very first frame and the latch has to say so."""
    assert await _armed_after_setup(sim_hub, 5.0 / 60.0) is True


async def test_a_target_already_past_the_meridian_does_not_arm(sim_hub):
    """Two hours west. The mount was slewed straight to the correct pier side
    and the countdown stays negative for the whole night; arming here would
    flip a target that needs nothing."""
    assert await _armed_after_setup(sim_hub, -2.0) is False


# 7 --------------------------------------------------------------------------

async def test_unknown_pier_side_now_flips(sim_hub, monkeypatch):
    """UNREADABLE IS NOT A VERDICT, AND THE TWO ERRORS ARE NOT SYMMETRIC.

    A fork mount and a GEM whose driver is quiet look identical from here, so
    this used to fall back to the tube geometry and decline. Treating a GEM as
    a fork has now cost four nights - 2026-08-19, 08-20, 08-21, 08-22. Treating
    a fork as a GEM costs one unnecessary re-slew per crossing, and a fork user
    who minds has `meridian_flip=False`.
    """
    monkeypatch.setattr(hub_module.config_store.cfg().site, "latitude", 37.35)
    # dec +80 from lat 37: lower culmination 27 deg up, so the TUBE never dips.
    # The old rule declined on exactly this.
    assert schedule.flip_unnecessary_over_pole(80.0, 37.35) is True
    e, t, rec = _engine_at(sim_hub, monkeypatch, 1.0, dec_deg=80.0,
                           pier="unknown")
    await e._maybe_meridian_flip(t, next_exposure_s=0.05)
    assert rec["flips"], (
        "an unreadable pier side declined the flip - the failure to MEASURE "
        "was read as a measurement of 'no pier'")


def test_the_predicate_itself_no_longer_excuses_an_unknown_mount():
    """The helper, directly, so the reversal cannot be undone underneath the
    call site above."""
    assert schedule.flip_can_be_skipped(80.0, 37.35, "unknown") is False
    assert schedule.flip_can_be_skipped(80.0, 37.35, None) is False
    assert schedule.flip_can_be_skipped(80.0, 37.35, "west") is False


# 8 --------------------------------------------------------------------------

async def test_a_fork_mount_can_still_opt_out(sim_hub, monkeypatch):
    """`meridian_flip=False` is the setting that already existed and it still
    wins - a fork owner is not forced into a re-slew every crossing."""
    monkeypatch.setattr(hub_module.config_store.cfg().site, "latitude", 37.35)
    e, t, rec = _engine_at(sim_hub, monkeypatch, 1.0, dec_deg=80.0,
                           pier="unknown", meridian_flip=False)
    await e._maybe_meridian_flip(t, next_exposure_s=0.05)
    assert not rec["flips"], "meridian_flip=False was overruled"

    e2, t2, rec2 = _engine_at(sim_hub, monkeypatch, 1.0, dec_deg=80.0,
                              pier="west", meridian_flip=False)
    await e2._maybe_meridian_flip(t2, next_exposure_s=0.05)
    assert not rec2["flips"], "meridian_flip=False was overruled on a GEM"


# ------------------------------------------------------- the refusal recovery

def _recovery_plan(count: int = 6, guide: bool = False) -> SequencePlan:
    return SequencePlan(
        name="recovery", guide=guide, dither_every=0, autofocus_every=0,
        # OFF so these tests are about the recovery and not about whatever hour
        # angle M42 happens to be at when the suite runs.
        meridian_flip=False,
        targets=[Target(name="M42", ra_hours=5.5881, dec_deg=-5.3911,
                        center=False, autofocus_first=False,
                        steps=[ExposureStep(filter="L", exposure_s=0.05,
                                            gain=100, count=count)])])


def _configured_dark_site(monkeypatch):
    """A site the rig can actually reason about, and a night.

    THE RECOVERY REFUSES ON A DEFAULT SITE, on purpose: it is the one gate in
    the engine that unparks a mount and slews it unattended, and
    `schedule.dark_enough` deliberately FAILS OPEN when the site is unset ("we
    cannot tell must not stand every automation down ... fail-open here,
    fail-closed in the gates that actually move hardware"). So a fresh install
    used to get the daylight check for free and inert. These tests are about
    what the recovery does on a configured rig, so give them one - and pin the
    darkness rather than letting the suite's answer depend on the time of day
    it runs (`test-count-is-not-test-cost` has the wall-clock version of this
    trap on record).
    """
    from astrodeck.sequence import engine as engine_module
    monkeypatch.setattr(hub_module.config_store.cfg().site, "is_default", False)
    monkeypatch.setattr(engine_module.schedule, "dark_enough",
                        lambda *a, **kw: True)


def _limit_mount(sim_hub, monkeypatch, *, park_clears: bool,
                 stop_after_reads: int = 3, configured_site: bool = True):
    """A telescope that hits its meridian limit part-way through the run.

    ``park_clears`` is the whole difference between the night that recovers and
    the night that does not: on the rig, park/unpark is what released the AM5.
    """
    if configured_site:
        _configured_dark_site(monkeypatch)
    tel = sim_hub.devices["telescope"]
    real_park, real_unpark = tel.park, tel.unpark
    st = {"tracking": True, "at_limit": False, "reads": 0, "events": []}

    async def get_tracking():
        st["reads"] += 1
        if st["reads"] == stop_after_reads:
            st["at_limit"] = True
            st["tracking"] = False
        return st["tracking"]

    async def set_tracking(on):
        if on and st["at_limit"]:
            st["events"].append("tracking_refused")
            raise RuntimeError("tracking on rejected (reply '0')")
        st["events"].append(f"set_tracking({bool(on)})")
        st["tracking"] = bool(on)

    async def park():
        st["events"].append("park")
        if park_clears:
            st["at_limit"] = False
        await real_park()

    async def unpark():
        st["events"].append("unpark")
        await real_unpark()

    monkeypatch.setattr(tel, "get_tracking", get_tracking)
    monkeypatch.setattr(tel, "set_tracking", set_tracking)
    monkeypatch.setattr(tel, "park", park)
    monkeypatch.setattr(tel, "unpark", unpark)

    real_goto = sim_hub.goto_and_center

    async def goto(*a, **kw):
        st["events"].append("goto_and_center")
        return await real_goto(*a, **kw)

    monkeypatch.setattr(sim_hub, "goto_and_center", goto)
    return st


async def _wait(eng, timeout=90.0):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        if not eng.running:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"run did not finish: {eng.state}")


# 9 --------------------------------------------------------------------------

async def test_a_refused_flip_parks_unparks_and_recovers(sim_hub, monkeypatch):
    """THE INTERVENTION A HUMAN PERFORMED TWICE, DONE BY THE ENGINE.

        park -> unpark -> tracking on -> re-slew -> solve and re-centre
             -> resume guiding -> continue

    That exact sequence recovered both nights by hand and put the run back
    0.5 arcmin from target.
    """
    st = _limit_mount(sim_hub, monkeypatch, park_clears=True)
    eng = SequenceEngine(sim_hub)
    eng.start(_recovery_plan(count=8))
    await _wait(eng)

    ev = st["events"]
    assert "tracking_refused" in ev, "premise: the mount must refuse first"
    assert "park" in ev, f"never parked, so it never left the limit: {ev}"
    i_park = ev.index("park")
    assert "unpark" in ev[i_park:], f"parked and stayed parked: {ev}"
    i_unpark = i_park + ev[i_park:].index("unpark")
    assert any(e.startswith("set_tracking(True)") for e in ev[i_unpark:]), (
        f"unparked but never re-asserted tracking: {ev}")
    assert "goto_and_center" in ev[i_unpark:], (
        f"recovered tracking but never re-slewed and re-centred: {ev}")
    done = (eng.state.get("progress") or {}).get("frames_done", 0)
    assert done == 8, (
        f"captured {done}/8 - the recovery is supposed to save the night, not "
        f"just log about it (state={eng.state.get('state')})")


async def test_the_recovery_puts_guiding_back(sim_hub, monkeypatch):
    """A recovered mount that is no longer guided shoots soft frames until
    dawn, which is a quieter version of the same lost night."""
    _limit_mount(sim_hub, monkeypatch, park_clears=True)
    guider = sim_hub.guider
    assert guider is not None, "premise: this fixture has a guider"
    starts = {"n": 0}
    real_start = guider.start_guiding

    async def start_guiding(*a, **kw):
        starts["n"] += 1
        return await real_start(*a, **kw)

    monkeypatch.setattr(guider, "start_guiding", start_guiding)
    eng = SequenceEngine(sim_hub)
    eng.start(_recovery_plan(count=6, guide=True))
    await _wait(eng)
    assert starts["n"] >= 2, (
        f"guiding started {starts['n']} time(s): once at target setup and once "
        f"after the recovery is the minimum")


# 10 -------------------------------------------------------------------------

async def test_the_recovery_runs_at_most_once_per_target(sim_hub, monkeypatch):
    """A RECOVERY LOOP AT 3 A.M. IS WORSE THAN THE BUG.

    A mount whose limit park does NOT clear gets exactly one attempt. The
    wind-down parks too, so the unpark is the countable half: nothing else in
    the run unparks a mount that started unparked.

    ...AND THE LATCH ITSELF IS ASSERTED, because counting unparks alone could
    not fail for the bound it is named after: with `park_clears=False` the
    first failed recovery raises StopTarget, the single-target run ends, and
    the wind-down never unparks - so "unpark == 1" held whether the latch
    existed, allowed two attempts, or was removed entirely. Both mutations were
    caught only by `test_the_budget_is_per_target_not_per_run`.
    """
    st = _limit_mount(sim_hub, monkeypatch, park_clears=False)
    eng = SequenceEngine(sim_hub)
    eng.start(_recovery_plan(count=8))
    await _wait(eng)
    n = st["events"].count("unpark")
    assert n == 1, f"the recovery ran {n} times: {st['events']}"
    assert len(eng._tracking_recovered) == 1, (
        f"the one-attempt latch holds {eng._tracking_recovered!r} - it is what "
        f"stops a 3 a.m. recovery loop, and nothing else records the attempt")
    # ...and a second refusal on the same target is refused by the latch rather
    # than by the run having already ended.
    target = eng.plan.targets[0]
    st["at_limit"] = True
    st["tracking"] = False
    eng._window_closed = False          # so the LATCH is what refuses, not this
    with pytest.raises(StopTarget):
        await eng._enforce_tracking(target.steps[0], target)
    assert st["events"].count("unpark") == 1, (
        f"a second attempt ran for the same target: {st['events']}")


async def test_the_budget_is_per_target_not_per_run(sim_hub, monkeypatch):
    """One attempt per TARGET. A second target that hits the limit later in the
    night is a separate crossing and gets its own single attempt; a second
    attempt on the SAME target does not."""
    st = _limit_mount(sim_hub, monkeypatch, park_clears=False,
                      stop_after_reads=1)
    a = Target(name="A", ra_hours=5.5881, dec_deg=-5.3911, center=False,
               autofocus_first=False,
               steps=[ExposureStep(filter="L", exposure_s=0.05, count=1)])
    b = Target(name="B", ra_hours=1.0, dec_deg=20.0, center=False,
               autofocus_first=False,
               steps=[ExposureStep(filter="L", exposure_s=0.05, count=1)])
    eng = SequenceEngine(sim_hub)
    eng.plan = SequencePlan(targets=[a, b], meridian_flip=False, guide=False)
    eng._cfg = None
    for target in (a, a, b):
        st["at_limit"] = True
        st["tracking"] = False
        with pytest.raises(StopTarget):
            await eng._enforce_tracking(target.steps[0], target)
    assert st["events"].count("unpark") == 2, (
        f"expected one attempt for A and one for B: {st['events']}")


async def test_a_wedged_device_still_tears_the_night_down(sim_hub, monkeypatch):
    """A `_bounded` timeout inside the recovery arrives as SafetyAbort, which is
    the shielded park/warm wind-down and NOT "the recovery did not help".
    Swallowing it would carry on with a mount left mid-slew."""
    from astrodeck.sequence.engine import SafetyAbort
    _limit_mount(sim_hub, monkeypatch, park_clears=True, stop_after_reads=1)

    async def wedged():
        raise SafetyAbort("park timed out after 240s")

    monkeypatch.setattr(sim_hub.devices["telescope"], "park", wedged)
    plan = _recovery_plan(count=2)
    eng = SequenceEngine(sim_hub)
    eng.plan = plan
    eng._cfg = None
    target = plan.targets[0]
    with pytest.raises(SafetyAbort):
        await eng._enforce_tracking(target.steps[0], target)


async def test_the_recovery_has_a_hard_outer_bound(sim_hub, monkeypatch):
    """Every step inside is bounded; so is the whole. A mount that answers every
    command slowly must not be able to spend the night being recovered."""
    from astrodeck.sequence import engine as engine_module
    st = _limit_mount(sim_hub, monkeypatch, park_clears=True,
                      stop_after_reads=1)
    monkeypatch.setattr(engine_module, "TRACKING_RECOVERY_TIMEOUT_S", 0.25)

    async def slow_park():
        st["events"].append("park")
        await asyncio.sleep(30.0)

    monkeypatch.setattr(sim_hub.devices["telescope"], "park", slow_park)
    plan = _recovery_plan(count=2)
    eng = SequenceEngine(sim_hub)
    eng.plan = plan
    eng._cfg = None
    target = plan.targets[0]
    t0 = asyncio.get_event_loop().time()
    with pytest.raises(StopTarget):
        await eng._enforce_tracking(target.steps[0], target)
    assert asyncio.get_event_loop().time() - t0 < 10.0, (
        "the recovery outlasted its own bound")


# 11 -------------------------------------------------------------------------

async def test_the_recovery_is_refused_after_the_window_closes(
        sim_hub, monkeypatch):
    """"Recover" after the window has shut means slewing a parked mount into
    daylight."""
    st = _limit_mount(sim_hub, monkeypatch, park_clears=True,
                      stop_after_reads=1)
    plan = _recovery_plan(count=2)
    eng = SequenceEngine(sim_hub)
    eng.plan = plan
    eng._cfg = None
    target = plan.targets[0]
    step = target.steps[0]
    eng._frozen[id(target)] = (time.time() - 7200.0, time.time() - 60.0)
    with pytest.raises(StopTarget):
        await eng._enforce_tracking(step, target)
    assert "park" not in st["events"], (
        f"slewed after the window closed: {st['events']}")

    # Control: the SAME refusal with the window still open does recover, so the
    # assertion above is about the window and not about a broken harness.
    st["at_limit"] = True
    st["tracking"] = False
    eng2 = SequenceEngine(sim_hub)
    eng2.plan = plan
    eng2._cfg = None
    eng2._frozen[id(target)] = (time.time() - 7200.0, time.time() + 7200.0)
    await eng2._enforce_tracking(step, target)
    assert "park" in st["events"], (
        f"control: an open window must still recover: {st['events']}")


async def test_the_recovery_is_refused_in_daylight(sim_hub, monkeypatch):
    """No slewing a parked mount into the sun."""
    st = _limit_mount(sim_hub, monkeypatch, park_clears=True,
                      stop_after_reads=1)
    from astrodeck.sequence import engine as engine_module
    monkeypatch.setattr(engine_module.schedule, "dark_enough",
                        lambda *a, **kw: False)
    plan = _recovery_plan(count=2)
    eng = SequenceEngine(sim_hub)
    eng.plan = plan
    eng._cfg = None
    target = plan.targets[0]
    with pytest.raises(StopTarget):
        await eng._enforce_tracking(target.steps[0], target)
    assert "park" not in st["events"], (
        f"recovered into daylight: {st['events']}")


# 12 -------------------------------------------------------------------------

async def test_the_recovery_restores_the_old_path_on_failure(
        sim_hub, monkeypatch):
    """When the recovery cannot help, the run ends exactly the way it did
    before this existed: the target is set aside and the night winds down. A
    recovery that swallows the failure is worse than none."""
    st = _limit_mount(sim_hub, monkeypatch, park_clears=False)
    eng = SequenceEngine(sim_hub)
    eng.start(_recovery_plan(count=12))
    await _wait(eng)
    done = (eng.state.get("progress") or {}).get("frames_done", 0)
    assert 0 < done < 12, (
        f"captured {done}/12: a mount that will not resume must still stop the "
        f"target, not keep shooting streaks")
    assert eng.state.get("end_reason") == "incomplete", (
        f"the set-aside path used to end the night 'incomplete'; it now ends "
        f"{eng.state.get('end_reason')!r}")
    assert st["events"].count("unpark") == 1, st["events"]


async def test_a_recovery_that_raises_is_not_fatal(sim_hub, monkeypatch):
    """A park that throws must degrade to the old set-aside path, not take the
    run down with an unhandled error."""
    tel = sim_hub.devices["telescope"]
    _limit_mount(sim_hub, monkeypatch, park_clears=False)

    async def boom():
        raise RuntimeError("park failed: serial timeout")

    monkeypatch.setattr(tel, "park", boom)
    eng = SequenceEngine(sim_hub)
    eng.start(_recovery_plan(count=12))
    await _wait(eng)
    done = (eng.state.get("progress") or {}).get("frames_done", 0)
    assert 0 < done < 12, f"captured {done}/12 (state={eng.state})"


# ------------------------------------------------------ a sweep needs tracking

class _Tracking:
    """A tracking probe with the same tri-state contract `_enforce_tracking`
    reads: True tracking, False refused, None unreadable."""

    def __init__(self, values):
        self.values = list(values)
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        i = min(self.calls - 1, len(self.values) - 1)
        return self.values[i]


async def _sim_focus_parts():
    parts = build_sim_rig()
    cam, foc = parts["camera"], parts["focuser"]
    await cam.connect()
    await foc.connect()
    return cam, foc


# 13 -------------------------------------------------------------------------

async def test_a_sweep_refuses_to_start_without_tracking():
    """Ten exposures over five minutes on a drifting mount measure the drift.
    On 2026-08-21 one ran to completion and returned a 'focus' position."""
    cam, foc = await _sim_focus_parts()
    start = await foc.get_position()
    probe = _Tracking([False])
    with pytest.raises(TrackingLost):
        await run_autofocus(cam, foc, exposure_s=0.05, gain=200, step=350,
                            steps_each_side=4, binning=2, tracking_check=probe)
    assert await foc.get_position() == start, "moved the focuser anyway"


async def test_an_unreadable_mount_does_not_stop_a_sweep():
    """Unknown is not False - the same tri-state `_enforce_tracking` keeps. A
    driver that cannot answer must not cost a focus run."""
    cam, foc = await _sim_focus_parts()
    probe = _Tracking([None])
    r = await run_autofocus(cam, foc, exposure_s=0.05, gain=200, step=350,
                            steps_each_side=4, binning=2, tracking_check=probe)
    assert r.success, r.message


async def test_a_sweep_with_no_probe_is_unchanged():
    """Every existing caller passes nothing and must behave byte-identically."""
    cam, foc = await _sim_focus_parts()
    r = await run_autofocus(cam, foc, exposure_s=0.05, gain=200, step=350,
                            steps_each_side=4, binning=2)
    assert r.success, r.message


# 14 -------------------------------------------------------------------------

async def test_a_sweep_that_loses_tracking_aborts_and_restores_the_focuser():
    """A sweep abandoned mid-way is how a night gets shot 2450 steps out of
    focus, so the abort has to put the focuser back where it started."""
    cam, foc = await _sim_focus_parts()
    start = await foc.get_position()
    probe = _Tracking([True, True, True, False])
    with pytest.raises(TrackingLost):
        await run_autofocus(cam, foc, exposure_s=0.05, gain=200, step=350,
                            steps_each_side=4, binning=2, tracking_check=probe)
    assert probe.calls >= 4, (
        f"only asked {probe.calls} times - a five-minute sweep has to re-ask "
        f"per point, not only at the start")
    assert await foc.get_position() == start, (
        f"left the focuser at {await foc.get_position()} instead of {start}")


# 15 -------------------------------------------------------------------------

async def test_a_sweep_does_not_apply_a_result_measured_while_stopped(
        sim_hub, monkeypatch):
    """THE 8.40 px CASE, ASSERTED AS A REFUSAL RATHER THAN A VALUE.

    00:48 on 2026-08-21 the sequence started an autofocus on a mount that had
    stopped four minutes earlier. It swept ten points through the drift, called
    it focus at HFR 8.40 px against 3.05-3.71 earlier the same night, and
    APPLIED it. Driven through the engine, because a rule wired to nothing is
    the shape this whole file is about.
    """
    tel = sim_hub.devices["telescope"]
    foc = sim_hub.devices["focuser"]
    start = await foc.get_position()

    async def not_tracking():
        return False

    monkeypatch.setattr(tel, "get_tracking", not_tracking)
    eng = SequenceEngine(sim_hub)
    eng.plan = SequencePlan(targets=[], meridian_flip=False)
    eng._cfg = None
    ok = await eng._autofocus("refocus")
    assert ok is False, "the engine accepted a focus measured on a dead mount"
    assert await foc.get_position() == start, (
        "applied a focus position measured while the mount was not tracking")


@native_only
async def test_the_engines_sweep_aborts_when_tracking_dies_mid_way(
        sim_hub, monkeypatch):
    """THE PATH THE RIG ACTUALLY RUNS, not the one that is easiest to test.

    `run_autofocus` routes through the provider layer, and on this rig that is
    the native Rust V-curve engine — a completely separate sweep loop. A guard
    wired only to the legacy numpy path would pass every test above and do
    nothing on sky, which is the shape half this file exists to prevent.
    """
    tel = sim_hub.devices["telescope"]
    foc = sim_hub.devices["focuser"]
    start = await foc.get_position()
    st = {"reads": 0}

    async def get_tracking():
        st["reads"] += 1
        return st["reads"] <= 3          # the limit bites part-way through

    monkeypatch.setattr(tel, "get_tracking", get_tracking)
    eng = SequenceEngine(sim_hub)
    eng.plan = SequencePlan(targets=[], meridian_flip=False)
    eng._cfg = None
    ok = await eng._autofocus("refocus")
    assert ok is False, (
        "the sweep ran on through a mount that stopped mid-way and applied "
        "whatever the drift measured")
    assert st["reads"] > 3, (
        f"only asked {st['reads']} times - a five-minute sweep has to re-ask "
        f"per point, not only at the start")
    assert await foc.get_position() == start, (
        f"left the focuser at {await foc.get_position()} instead of {start}")


@native_only
async def test_a_dark_frame_sweep_is_not_gated_on_tracking(sim_hub, monkeypatch):
    """Darks are shot parked on purpose - the same exemption a light-frame gate
    already makes - so a calibration step must not lose its refocus."""
    tel = sim_hub.devices["telescope"]

    async def not_tracking():
        return False

    monkeypatch.setattr(tel, "get_tracking", not_tracking)
    eng = SequenceEngine(sim_hub)
    eng.plan = SequencePlan(targets=[], meridian_flip=False)
    eng._cfg = None
    step = ExposureStep(filter="L", exposure_s=0.05, count=1, frame_type="Dark")
    ok = await eng._autofocus("refocus", step=step)
    assert ok is True, "gated a dark-frame refocus on a tracking mount"


# ============================================================================
# ROUND 2 - what three verifiers found after the first cut, reproduced first
# and then closed. Each of these failed, or could not fail, before the fix it
# names.
# ============================================================================

async def _noop_gate(context="", target=None):
    """A safety gate that lets everything through, for the tests whose subject
    is something else."""
    return None


# --------------------------------------------- one sample is not a verdict

@native_only
async def test_one_sampled_dropout_does_not_abandon_a_sweep(
        sim_hub, monkeypatch):
    """THE EAST GUIDE PULSE IS A TRACKING-OFF WINDOW ON THIS MOUNT.

    `zwo_am5.pulse_guide` implements an east pulse as a tracking SUSPEND -
    ``:Td#``, sleep, ``:Te#`` in a finally - so ``get_tracking()`` sampled
    inside it answers False on a mount that is working perfectly. The incident
    note lists it as the known benign false negative. The engine does not stop
    guiding for a sequence autofocus, so the sweep asks this question about
    eleven times with the guider actively pulsing; before the confirming
    re-probe, ONE unlucky sample threw the whole sweep away - and with
    ``escalation.af_failure_action = "abort"`` it would have ended the night.
    """
    tel = sim_hub.devices["telescope"]
    st = {"n": 0}

    async def get_tracking():
        st["n"] += 1
        return st["n"] != 4              # ONE transient dropout, mid-sweep

    monkeypatch.setattr(tel, "get_tracking", get_tracking)
    eng = SequenceEngine(sim_hub)
    eng.plan = SequencePlan(targets=[], meridian_flip=False)
    eng._cfg = None
    ok = await eng._autofocus("refocus")
    assert ok is True, (
        "a single sampled 'not tracking' - the guide pulse's own :Td# window - "
        "threw away a whole autofocus sweep")
    assert st["n"] > 4, "the False was believed without ever being re-asked"


@native_only
async def test_a_dropout_that_lasts_is_still_believed(sim_hub, monkeypatch):
    """...and the confirm must not become a way of never noticing. A mount at
    its meridian limit stays stopped for MINUTES; the confirm window is
    seconds. This is the control that stops the fix above from being a
    disabler."""
    from astrodeck.sequence import engine as engine_module
    tel = sim_hub.devices["telescope"]
    st = {"n": 0}

    async def get_tracking():
        st["n"] += 1
        return st["n"] < 3               # stops on the third read and stays

    monkeypatch.setattr(tel, "get_tracking", get_tracking)
    eng = SequenceEngine(sim_hub)
    eng.plan = SequencePlan(targets=[], meridian_flip=False)
    eng._cfg = None
    ok = await eng._autofocus("refocus")
    assert ok is False, "a mount that really stopped ran a whole sweep anyway"
    assert st["n"] >= 3 + engine_module.TRACKING_CONFIRM_PROBES, (
        f"only {st['n']} reads - the verdict was reached without confirming it")


def test_the_confirm_window_outlasts_a_guide_pulse():
    """The numbers, pinned. A guide pulse is capped near one second
    (`guide.assistant.MAX_PULSE_MS`); the confirm samples across several. A
    confirm window shorter than a pulse is not a confirm."""
    from astrodeck.guide import assistant as ga
    from astrodeck.sequence import engine as engine_module
    window_s = (engine_module.TRACKING_CONFIRM_PROBES
                * engine_module.TRACKING_CONFIRM_S)
    assert window_s >= 2.0 * (ga.MAX_PULSE_MS / 1000.0), (
        f"the confirm window is {window_s:g}s against a {ga.MAX_PULSE_MS} ms "
        f"guide pulse - one pulse can still cover every sample")


@native_only
async def test_a_calibration_target_sweep_is_not_gated_on_tracking(
        sim_hub, monkeypatch):
    """The dark-STEP exemption has a test; the calibration-TARGET one did not,
    and dropping it was a sabotage survivor."""
    tel = sim_hub.devices["telescope"]

    async def not_tracking():
        return False

    monkeypatch.setattr(tel, "get_tracking", not_tracking)
    eng = SequenceEngine(sim_hub)
    eng.plan = SequencePlan(targets=[], meridian_flip=False)
    eng._cfg = None
    t = Target(name="flats", ra_hours=1.0, dec_deg=20.0, center=False,
               autofocus_first=False, calibration=True,
               steps=[ExposureStep(filter="L", exposure_s=0.05, count=1)])
    ok = await eng._autofocus("refocus", target=t)
    assert ok is True, "gated a calibration target's refocus on a tracking mount"


async def test_a_stopped_mount_is_not_an_autofocus_failure(
        sim_hub, monkeypatch):
    """``escalation.af_failure_action = "abort"`` is a setting a careful
    operator turns on. Routed through it, a stopped mount became a night-ending
    SafetyAbort at the two sweeps that run BEFORE any tracking gate - the
    initial autofocus in `_setup_target` and the post-flip one - so the
    park/unpark recovery never got its turn. A stopped mount is a mount
    problem, and it has its own recovery."""
    from astrodeck.config import config_store
    from astrodeck.sequence.engine import SafetyAbort
    tel = sim_hub.devices["telescope"]

    async def not_tracking():
        return False

    monkeypatch.setattr(tel, "get_tracking", not_tracking)
    monkeypatch.setattr(config_store.cfg().escalation, "af_failure_action",
                        "abort")
    eng = SequenceEngine(sim_hub)
    eng.plan = SequencePlan(targets=[], meridian_flip=False)
    eng._cfg = config_store.cfg()
    try:
        ok = await eng._autofocus("initial autofocus")
    except SafetyAbort as e:                # pragma: no cover - the defect
        raise AssertionError(
            f"a stopped mount tore the night down through the autofocus "
            f"escalation: {e}")
    assert ok is False

    # ...and a REAL autofocus failure still escalates, so the exemption above
    # is about TrackingLost and not about quietly disarming the setting.
    async def tracking():
        return True

    async def boom(*a, **kw):
        raise RuntimeError("focuser jammed")

    monkeypatch.setattr(tel, "get_tracking", tracking)
    monkeypatch.setattr("astrodeck.sequence.engine.run_autofocus", boom)
    with pytest.raises(SafetyAbort):
        await eng._autofocus("initial autofocus")


# ------------------------------------------------------- the recovery's gates

async def test_the_recovery_takes_the_same_safety_gate_the_flip_does(
        sim_hub, monkeypatch):
    """THE RECOVERY IS A SLEW. The flip takes `_safety_gate(context="slew")`
    before it moves ("the flip is a slew - S1.9-B") and target setup takes it
    too; this one moved with only a darkness check, so the mount-altitude
    floor, the horizon profile, the no-go wedges, the ZENITH KEEP-OUT and the
    pier-collision guard were all skipped - on a slew that by definition
    happens at the meridian, which is exactly where the zenith keep-out
    lives."""
    st = _limit_mount(sim_hub, monkeypatch, park_clears=True,
                      stop_after_reads=1)
    gated: list[str] = []

    eng = SequenceEngine(sim_hub)
    eng.plan = _recovery_plan()
    eng._cfg = None

    async def _gate(context="", target=None):
        gated.append(context)

    eng._safety_gate = _gate
    target = eng.plan.targets[0]
    await eng._enforce_tracking(target.steps[0], target)
    assert "park" in st["events"], f"premise: it must recover: {st['events']}"
    assert "slew" in gated, (
        "the recovery unparked and slewed without the slew safety gate the "
        "flip takes")


async def test_a_refused_safety_gate_does_not_burn_the_one_attempt(
        sim_hub, monkeypatch):
    """The gate is taken BEFORE the latch. A recovery the safety system refuses
    costs nothing, so the one attempt is still there when the sky clears."""
    from astrodeck.sequence.engine import SafetyAbort
    _limit_mount(sim_hub, monkeypatch, park_clears=True, stop_after_reads=1)
    eng = SequenceEngine(sim_hub)
    eng.plan = _recovery_plan()
    eng._cfg = None

    async def _gate(context="", target=None):
        raise SafetyAbort("roof closed")

    eng._safety_gate = _gate
    target = eng.plan.targets[0]
    with pytest.raises(SafetyAbort):
        await eng._enforce_tracking(target.steps[0], target)
    assert eng._tracking_recovered == set(), (
        "a refused recovery spent the target's only attempt")


def test_the_recovery_bound_is_bigger_than_the_steps_it_wraps():
    """`TRACKING_RECOVERY_TIMEOUT_S` is documented as sitting "on top of the
    per-call bounds each step already carries". It only does if it is bigger
    than their sum - otherwise a park and an unpark that are merely SLOW eat
    the budget and the outer `wait_for` cancels a plate-solving re-slew
    MID-MOTION, which the generic handler then reports as "the recovery
    failed" instead of the SafetyAbort teardown a wedged device is promised.
    Recomputed from the constants so the two cannot drift apart again."""
    from astrodeck.sequence import engine as E
    inner = (E.GUIDE_OP_TIMEOUT_S            # stop guiding
             + E.PARK_TIMEOUT_S              # park
             + E.PARK_TIMEOUT_S              # unpark
             + E.MOUNT_QUERY_TIMEOUT_S       # set_tracking
             + E.MOUNT_QUERY_TIMEOUT_S       # readback
             + E.GOTO_TIMEOUT_S + 300.0      # re-centre (+ rotation)
             + E.GUIDE_OP_TIMEOUT_S          # guider calibration flip
             + E.GUIDE_START_TIMEOUT_S       # restart guiding
             + E.MOUNT_QUERY_TIMEOUT_S)      # final readback
    assert E.TRACKING_RECOVERY_TIMEOUT_S >= inner, (
        f"the outer bound ({E.TRACKING_RECOVERY_TIMEOUT_S:.0f}s) is smaller "
        f"than the steps it wraps ({inner:.0f}s), so it overrides them "
        f"instead of backing them up")


async def test_the_recovery_is_refused_on_a_rig_with_no_site(
        sim_hub, monkeypatch):
    """`dark_enough` FAILS OPEN on an unset site, deliberately: "we cannot
    tell" must not stand every automation down. Its own docstring finishes
    "fail-closed in the gates that actually move hardware", and this is the
    gate that unparks a mount and slews it unattended. On a fresh install the
    whole daylight refusal was inert."""
    st = _limit_mount(sim_hub, monkeypatch, park_clears=True,
                      stop_after_reads=1, configured_site=False)
    monkeypatch.setattr(hub_module.config_store.cfg().site, "is_default", True)
    eng = SequenceEngine(sim_hub)
    eng.plan = _recovery_plan()
    eng._cfg = None
    target = eng.plan.targets[0]
    with pytest.raises(StopTarget):
        await eng._enforce_tracking(target.steps[0], target)
    assert "park" not in st["events"], (
        f"unparked and slewed on a rig that cannot tell day from night: "
        f"{st['events']}")


async def test_the_recovery_is_refused_once_the_RUN_window_has_closed(
        sim_hub, monkeypatch):
    """The per-target window is tested; the run-wide `_window_closed` latch was
    not, and deleting it left the suite green. It is the one that says
    "something tonight has already run out of time"."""
    st = _limit_mount(sim_hub, monkeypatch, park_clears=True,
                      stop_after_reads=1)
    eng = SequenceEngine(sim_hub)
    eng.plan = _recovery_plan()
    eng._cfg = None
    eng._window_closed = True
    target = eng.plan.targets[0]
    with pytest.raises(StopTarget):
        await eng._enforce_tracking(target.steps[0], target)
    assert "park" not in st["events"], (
        f"unparked and slewed after the run's window closed: {st['events']}")


async def test_a_mount_that_ACCEPTS_tracking_but_does_not_track_sets_aside(
        sim_hub, monkeypatch):
    """The recovery's own readback branch - "the mount still will not track
    after a park/unpark cycle" - was unreachable from any test, because every
    one of them makes `set_tracking` RAISE. An AM5 at its limit answers `:Te#`
    with a bare `0`; a driver that maps that to "accepted" while `:GAT#` still
    reports 0 is the same lost night, and this branch is the only thing between
    it and shooting streaks until dawn."""
    _configured_dark_site(monkeypatch)
    tel = sim_hub.devices["telescope"]
    real_park, real_unpark = tel.park, tel.unpark
    st = {"events": []}

    async def get_tracking():
        return False                     # never tracks, never complains

    async def set_tracking(on):
        st["events"].append(f"set_tracking({bool(on)})")

    async def park():
        st["events"].append("park")
        await real_park()

    async def unpark():
        st["events"].append("unpark")
        await real_unpark()

    monkeypatch.setattr(tel, "get_tracking", get_tracking)
    monkeypatch.setattr(tel, "set_tracking", set_tracking)
    monkeypatch.setattr(tel, "park", park)
    monkeypatch.setattr(tel, "unpark", unpark)
    eng = SequenceEngine(sim_hub)
    eng.plan = _recovery_plan()
    eng._cfg = None
    target = eng.plan.targets[0]
    with pytest.raises(StopTarget):
        await eng._enforce_tracking(target.steps[0], target)
    assert "unpark" in st["events"], f"premise: it must try: {st['events']}"


async def test_the_one_recovery_budget_is_restored_for_the_NEXT_run(
        sim_hub, monkeypatch):
    """`engine = SequenceEngine(hub)` is a MODULE-LEVEL SINGLETON in
    api/app.py - one object for every night the process lives. A latch that is
    not cleared at run start spends a target's single recovery once and never
    gives it back, which is `astrodeck-resume-never-armed` exactly."""
    st = _limit_mount(sim_hub, monkeypatch, park_clears=True,
                      stop_after_reads=1)
    eng = SequenceEngine(sim_hub)
    eng.plan = _recovery_plan()
    eng._cfg = None
    target = eng.plan.targets[0]
    await eng._enforce_tracking(target.steps[0], target)
    assert st["events"].count("unpark") == 1, st["events"]
    assert eng._tracking_recovered, "premise: the latch must have been spent"
    eng._flip_no_op.add("stale")
    eng.start(_recovery_plan(count=1))
    await asyncio.sleep(0.05)
    await eng.abort()
    await _wait(eng)
    assert eng._tracking_recovered == set(), (
        "the recovery budget was not restored at run start: a target that used "
        "its one attempt last night gets none tonight")
    assert eng._flip_no_op == set(), (
        "the no-op-flip latch was not restored at run start either")


# ------------------------------------------------------------------ the hold

async def test_the_hold_waits_for_the_LEAD_point_not_the_meridian(sim_hub):
    """`_wait_for_flip_point` is stubbed out in every other test here, so the
    one piece of arithmetic that decides how long the mount idles before an
    early flip was unexercised - inverting its "already past" test so it
    returned immediately, always, left the whole suite green."""
    lon = sim_hub.site["longitude"]
    # 40 s to transit, lead 30 s -> the flip point is 10 s away.
    ra = (schedule.lst_hours(lon) + 40.0 / 3600.0) % 24.0
    t = Target(name="T", ra_hours=ra, dec_deg=66.11, center=False,
               autofocus_first=False,
               steps=[ExposureStep(filter="L", exposure_s=0.05, count=1)])
    eng = SequenceEngine(sim_hub)
    eng.plan = SequencePlan(targets=[t], meridian_flip=True)
    t0 = time.monotonic()
    await eng._wait_for_flip_point(t, 30.0)
    waited = time.monotonic() - t0
    assert 5.0 <= waited <= 25.0, (
        f"held {waited:.1f}s for a flip point 10s away - the hold is not "
        f"honouring the lead")
    # ...and it does not hold at all once the flip point is behind us.
    t1 = time.monotonic()
    await eng._wait_for_flip_point(t, 3600.0)
    assert time.monotonic() - t1 < 2.0, "held for a flip point already passed"


# -------------------------------------------------------- the arming boundary

def test_the_arming_boundary_follows_the_lead_not_a_flat_hour():
    """The latch has to arm at least as far past transit as the trigger fires
    before it, or a target acquired inside its own lead window never flips.
    It does NOT have to arm a whole hour past: the flip countdown is computed
    from the TARGET's catalogue RA and the site longitude, never from the
    mount's drifted readout, so the drift argument for a flat hour does not
    reach this decision - while the cost does. `_setup_target` re-runs on a
    safety-pause resume, a roof reopen and a cloud-hold release, and each one
    armed a latch that fired a full flip on a mount `goto_and_center` had just
    placed on the correct side."""
    lead = MERIDIAN_FLIP_LEAD_MIN
    margin = schedule.FLIP_ARM_MARGIN_MIN
    assert margin > 0, "the boundary must sit PAST transit, not on it"
    # inside the lead window: still arms, which is the case the design named
    assert schedule.flip_should_arm(5.0 / 60.0) is True
    assert schedule.flip_should_arm(0.0) is True
    # just past transit, still within lead+margin: arms
    assert schedule.flip_should_arm(-(lead + margin - 1.0) / 60.0) is True
    # ...and it stops there rather than an hour later
    assert schedule.flip_should_arm(-(lead + margin + 1.0) / 60.0) is False
    assert schedule.flip_should_arm(-1.0) is False, (
        "a target acquired an hour past transit still arms an immediate flip")
    assert schedule.flip_should_arm(-2.0) is False
    # the boundary MOVES with the plan's own lead
    assert schedule.flip_should_arm(-(30.0 + margin - 1.0) / 60.0,
                                    lead_min=30.0) is True
    assert schedule.flip_should_arm(-(margin + 1.0) / 60.0,
                                    lead_min=0.0) is False


async def test_a_target_acquired_half_an_hour_west_does_not_flip_on_frame_one(
        sim_hub):
    """The cost the flat hour was paying, asserted through the real
    `_setup_target`: a re-acquisition 30 minutes past transit is a mount that
    was just plate-solved onto the correct side."""
    assert await _armed_after_setup(sim_hub, -30.0 / 60.0) is False


def test_a_mount_with_no_pier_side_is_still_not_a_reported_gem():
    """`flip_can_be_skipped` returns False on both branches now, so nothing
    downstream can tell whether `mount_is_gem` still works - making it return
    True for everything left the suite green. It is the single place a future
    "the driver reports a mount TYPE" optimisation reconnects (its own
    docstring says so), and a predicate that answers True for everything would
    reconnect it to nothing."""
    assert schedule.mount_is_gem("east") is True
    assert schedule.mount_is_gem("west") is True
    assert schedule.mount_is_gem("WEST") is True
    assert schedule.mount_is_gem("unknown") is False
    assert schedule.mount_is_gem(None) is False
    assert schedule.mount_is_gem("") is False


# --------------------------------------------------- the flip's own refusal

async def test_a_flip_refused_by_a_stopped_mount_recovers_instead_of_dying(
        sim_hub, monkeypatch):
    """THIS IS THE LINE BOTH NIGHTS ACTUALLY DIED ON.

    `hub.goto_and_center` does an UNGUARDED `set_tracking(True)` before it
    slews, so a mount already at its limit answers `0` and the RuntimeError
    comes straight out of `hub.meridian_flip` and kills the run - "sequence
    crashes: tracking on rejected (reply '0')" is the incident note's own line.
    The park/unpark recovery was wired only to `_enforce_tracking`, which this
    path never reaches.
    """
    _configured_dark_site(monkeypatch)
    tel = sim_hub.devices["telescope"]
    real_park, real_unpark = tel.park, tel.unpark
    st = {"at_limit": True, "events": []}

    async def get_tracking():
        return not st["at_limit"]

    async def set_tracking(on):
        if on and st["at_limit"]:
            st["events"].append("tracking_refused")
            raise RuntimeError("tracking on rejected (reply '0')")
        st["events"].append("set_tracking")

    async def park():
        st["events"].append("park")
        st["at_limit"] = False           # park/unpark is what released the AM5
        await real_park()

    async def unpark():
        st["events"].append("unpark")
        await real_unpark()

    monkeypatch.setattr(tel, "get_tracking", get_tracking)
    monkeypatch.setattr(tel, "set_tracking", set_tracking)
    monkeypatch.setattr(tel, "park", park)
    monkeypatch.setattr(tel, "unpark", unpark)

    e, t, rec = _engine_at(sim_hub, monkeypatch, 9.0)
    e._safety_gate = _noop_gate
    # the REAL flip, so the real unguarded set_tracking inside it runs
    real_flip = type(sim_hub).meridian_flip

    async def flip(ra_h, dec_d):
        st["events"].append("flip")
        return await real_flip(sim_hub, ra_h, dec_d)

    monkeypatch.setattr(sim_hub, "meridian_flip", flip)
    await e._maybe_meridian_flip(t, next_exposure_s=180.0)
    assert "tracking_refused" in st["events"], (
        f"premise: the flip must be refused first: {st['events']}")
    assert "park" in st["events"] and "unpark" in st["events"], (
        f"the flip's own tracking refusal killed the run with no recovery "
        f"attempted: {st['events']}")


async def test_a_flip_that_does_not_change_the_pier_side_keeps_the_latch(
        sim_hub, monkeypatch):
    """NOTHING EVER CHECKED THAT THE FLIP FLIPPED.

    `hub.meridian_flip` is a bare `goto_and_center` whose docstring only
    ASSUMES "the mount chooses the far side of the pier", and the latch was
    spent whether or not anything moved. With a 10-minute lead the GoTo is
    issued while the target is still EAST of the meridian - and this repo's own
    recorded AM5N measurement says the reported side follows the hour angle, so
    that GoTo may well be answered by staying put. Then the engine believes it
    has flipped, the mount reaches its limit four minutes later, and the one
    flip of the crossing has been spent on nothing.
    """
    e, t, rec = _engine_at(sim_hub, monkeypatch, 9.0, pier="west")
    await e._maybe_meridian_flip(t, next_exposure_s=180.0)
    assert rec["flips"], "premise: it must flip"
    assert e._flip_armed is True, (
        "the flip re-slewed, the mount still reports the same pier side, and "
        "the crossing's only flip was spent on it anyway")
    # ...and the retry drops the lead, so it waits for the meridian itself
    # rather than firing again on the very next frame.
    assert e._flip_lead_s(t) == 0.0
    rec["flips"].clear()
    await e._maybe_meridian_flip(t, next_exposure_s=180.0)
    assert not rec["flips"], (
        "the retry fired on the very next frame instead of waiting for the "
        "meridian - that is a re-flip loop, not a retry")
    assert e._flip_armed is True
    # ONE retry, not a loop: once the flip point is reached the second attempt
    # disarms whatever the side says.
    await e._maybe_meridian_flip(t, next_exposure_s=100000.0)
    assert rec["flips"], "premise: a huge frame window must reach the flip"
    assert e._flip_armed is False, "the no-op flip retry is unbounded"


async def test_a_flip_that_DOES_change_the_pier_side_disarms(
        sim_hub, monkeypatch):
    """The control. A mount that really flipped must not be flipped again."""
    e, t, rec = _engine_at(sim_hub, monkeypatch, 9.0)

    async def _pier():
        return PierSide.WEST if not rec["flips"] else PierSide.EAST

    monkeypatch.setattr(sim_hub.devices["telescope"], "pier_side", _pier)
    await e._maybe_meridian_flip(t, next_exposure_s=180.0)
    assert rec["flips"], "premise: it must flip"
    assert e._flip_armed is False, "a real flip left the latch armed"


async def test_an_unreadable_pier_side_does_not_read_as_unchanged(
        sim_hub, monkeypatch):
    """Two failures to READ the side must never be compared and called
    "unchanged" - that is the same category error the whole change is about.
    An unreadable side leaves the old behaviour: one flip, latch spent."""
    e, t, rec = _engine_at(sim_hub, monkeypatch, 9.0)

    async def _blind():
        raise RuntimeError("pier side unreadable")

    monkeypatch.setattr(sim_hub.devices["telescope"], "pier_side", _blind)
    await e._maybe_meridian_flip(t, next_exposure_s=180.0)
    assert rec["flips"], "premise: an unknown side must still flip"
    assert e._flip_armed is False, (
        "two unreadable pier sides were compared and called unchanged")


# ------------------------------------------- the gap between two flip checks

async def test_the_flip_is_re_checked_after_the_events_in_a_frame():
    """THE SWEEP THAT SAT IN THE GAP.

    The gate at the top of the frame loop budgets one exposure plus the
    measured per-frame overhead for the trip back to it. A dither, a filter
    change and a five-minute autofocus sweep can put five minutes in between -
    and the whole margin between the flip point and this mount's limit is 2.4
    minutes on the worse of the two measured nights. On 2026-08-21 the sweep
    that ran at 00:48 sat exactly there, between the last flip check and a flip
    that arrived nine minutes after the mount had already stopped.

    Asserted structurally, on the source: the loop asks twice per frame with
    the events in between, so no event can sit between the last check and the
    shutter.
    """
    import inspect
    from astrodeck.sequence.engine import SequenceEngine as _E
    lines = inspect.getsource(_E._run_step).splitlines()
    calls = [i for i, line in enumerate(lines)
             if "await self._maybe_meridian_flip(" in line]
    assert len(calls) >= 2, (
        "the frame loop checks the meridian flip once, before the dither, the "
        "filter change and the autofocus sweep that can run after it")
    refocus = [i for i, line in enumerate(lines)
               if '_autofocus("refocus"' in line]
    assert refocus and calls[0] < refocus[0] < calls[-1], (
        "the second flip check does not sit AFTER the autofocus sweep, which "
        "is the event that swallowed the flip on 2026-08-21")


def test_the_frame_window_covers_the_measured_overhead():
    """...and the ordinary gap - download, quality, thumbnail, the guider-quiet
    gate - is budgeted from the engine's own measurement rather than assumed to
    fit inside the flat margin."""
    import inspect
    from astrodeck.sequence.engine import SequenceEngine as _E
    src = inspect.getsource(_E._maybe_meridian_flip)
    i = src.index("window_s =")
    assert "_overhead_ema" in src[i:i + 400], (
        "the frame-window gate budgets the exposure and a flat margin but not "
        "the measured per-frame overhead it takes to get back here")


@pytest.mark.parametrize("center", [True, False])
async def test_target_setup_recovers_a_mount_that_refuses_after_the_slew(
        sim_hub, monkeypatch, center):
    """THE SAME UNGUARDED `set_tracking(True)` SITS AT TARGET SETUP.

    The centered branch goes through `hub.goto_and_center`, which asserts
    tracking before it slews; the non-centered branch asserts it again AFTER
    the slew, and only the PRE-slew call was deliberately tolerant (the
    2026-08-19 fix: "the slew is the one action that moves it OFF the limit").
    So a mount still pinned at its limit from the previous target killed the
    run here, with nothing tried - the recovery was wired only to
    `_enforce_tracking`, which target setup never reaches.
    """
    _configured_dark_site(monkeypatch)
    tel = sim_hub.devices["telescope"]
    real_park, real_unpark = tel.park, tel.unpark
    st = {"at_limit": True, "events": []}

    async def get_tracking():
        return not st["at_limit"]

    async def set_tracking(on):
        if on and st["at_limit"]:
            st["events"].append("tracking_refused")
            raise RuntimeError("tracking on rejected (reply '0')")
        st["events"].append("set_tracking")

    async def park():
        st["events"].append("park")
        st["at_limit"] = False
        await real_park()

    async def unpark():
        st["events"].append("unpark")
        await real_unpark()

    monkeypatch.setattr(tel, "get_tracking", get_tracking)
    monkeypatch.setattr(tel, "set_tracking", set_tracking)
    monkeypatch.setattr(tel, "park", park)
    monkeypatch.setattr(tel, "unpark", unpark)

    t = Target(name="M42", ra_hours=5.5881, dec_deg=-5.3911, center=center,
               autofocus_first=False,
               steps=[ExposureStep(filter="L", exposure_s=0.05, count=1)])
    eng = SequenceEngine(sim_hub)
    eng.plan = SequencePlan(targets=[t], meridian_flip=False, guide=False)
    eng._cfg = None
    eng._safety_gate = _noop_gate
    await eng._setup_target(0, t)        # must not raise
    assert "tracking_refused" in st["events"], (
        f"premise: the mount must refuse first: {st['events']}")
    assert "park" in st["events"] and "unpark" in st["events"], (
        f"target setup died on the refusal with no recovery: {st['events']}")
    assert st["at_limit"] is False
