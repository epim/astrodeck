# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A session whose flat will not meter is tried once a night, not once a tick
(#846; wave 18).

THE FINDING (the wave 17 integration verifier's, after #841). A calibration
step whose flat exposure will not meter now shoots none of its frames, so the
ledger still owes all of them and the session ends ``dormant`` and armed:

    status dormant, owed 6, auto_resume True, resume_across_nights True

``ResumeArm.armed()`` returns such a session. The recovery ladder's "nothing to
shoot tonight" refusal (``nothing_to_shoot_tonight``) is the guard against a
session the run would end at once and the next tick would start again, but it
asks only about LIGHT targets: "calibration-only work starts". A restart
meters the same lamp behind the same cover, fails the same way, and ends
dormant and armed again. PROVEN HERE, not argued: on the unfixed code a clocked
ResumeArm cycle restarts the session on every tick (4 of 4), each pass
closing the cover, lighting the lamp, moving the wheel and plate solving.

THE RULE, as this file holds it. A flat step whose opening solve did not
converge is set aside FOR THE NIGHT, the record every other "tried tonight,
do not try again tonight" is (``Session.set_aside``, spec 3.4):

* the run records it when it ends (``_finalize_report`` ->
  ``_record_flat_metering``), for each step the SESSION still owes that the
  run could not meter, kind ``"unmetered"``;
* ``nothing_to_shoot_tonight`` reads the record for calibration steps, so a
  session whose every owed calibration step is set aside tonight (and which
  has no light target the run can still shoot) holds in words
  (``NOTHING_TONIGHT``) on the ten-minute retry, said once a night;
* the NEXT night reads none of tonight's records and meters afresh, which is
  what ``resume_across_nights`` promises;
* a start BY HAND is not held by it: the person who presses CONTINUE has
  usually fixed the lamp, so ``_run_calibration`` meters again.

HARNESS: a real engine over the simulator, a real ``ResumeArm`` whose ``tick``
and ``_recover`` run unmodified, driven through the night by an injected clock
and a window the cases hold open (``_window_open``). ``flat_illumination`` is 0
until the sim panel is lit, so a step with no ``panel_brightness`` meters a
source that does not brighten with exposure ("too_dim_at_max"); a lit panel
converges, which is the control. THE NIGHT IS PINNED, not read: the engine's
``night_key`` is patched to a cell the case moves, and the arm's clock is the
evening of that night, so no case depends on the hour it runs at.

NAMED MUTANTS (each run from a byte backup, restored byte-identically with
the md5 compared); the observed failure is quoted in the case that catches it.
"""
from __future__ import annotations

import time

import astrodeck.sequence.engine as engine_module
from _simhub import sim_hub  # noqa: F401 (fixture import)

from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import (DuskFlatsPlan, ExposureStep,
                                       SequencePlan, Target)
from astrodeck.sequence.resume_arm import (NOTHING_TONIGHT, RETRY_INTERVAL_S,
                                           ResumeArm, nothing_to_shoot_tonight,
                                           spent_tonight)
from astrodeck.sequence.session import Session, SessionFrame, session_store

NIGHT_ONE = "2026-03-10"
NIGHT_TWO = "2026-03-11"

#: The gaps between the ticks of one night: two a minute apart (the cadence
#: the service really runs at, and inside the backoff a refusal sets), then two
#: past the ten-minute retry, so a refusal is asked again and not only waited
#: out.
TICK_GAPS_S = (60.0, 60.0, RETRY_INTERVAL_S + 1.0, RETRY_INTERVAL_S + 1.0)


def _evening_of(night: str) -> float:
    """22:00 local on the date ``night`` names: a clock reading whose
    ``events.night_key`` is ``night`` however the case is timed."""
    return time.mktime(time.strptime(f"{night} 22:00", "%Y-%m-%d %H:%M"))


class Night:
    """The pinned night: the engine's ``night_key`` answers ``night`` and
    ``clock()`` is an evening in it that ``advance`` moves forward."""

    def __init__(self, monkeypatch, night: str = NIGHT_ONE):
        self.night = night
        self.t = _evening_of(night)
        monkeypatch.setattr(engine_module, "night_key",
                            lambda ts=None: self.night)

    def clock(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds

    def next_night(self, night: str) -> None:
        self.night = night
        self.t = _evening_of(night)


def _plan(brightness: dict | None = None, filters=("L", "R"), adu=8000,
          count=3) -> SequencePlan:
    """A calibration-only plan of flats. ``brightness`` maps a filter to its
    panel brightness; a filter missing from it never lights the panel."""
    brightness = brightness or {}
    return SequencePlan(
        name="Flats", guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=False,
        targets=[Target(
            name="flats", ra_hours=0.0, dec_deg=0.0, calibration=True,
            center=False, autofocus_first=False,
            steps=[ExposureStep(filter=f, exposure_s=1.0, gain=100, offset=30,
                                count=count, frame_type="Flat", adu_target=adu,
                                panel_brightness=brightness.get(f))
                   for f in filters])])


class Tap:
    """Every trial (unsaved) capture the engine makes: the metering."""

    def __init__(self, hub, monkeypatch):
        self.trials: list[str] = []
        real = hub.capture

        async def capture(exposure_s, gain, offset, binning, **kw):
            if not kw.get("save", True):
                fw = hub.devices["filterwheel"]
                self.trials.append(fw.filter_names[await fw.get_position()])
            return await real(exposure_s, gain, offset, binning, **kw)
        monkeypatch.setattr(hub, "capture", capture)


async def _run_to_its_end(eng: SequenceEngine, plan: SequencePlan) -> Session:
    eng.start(plan)
    await eng._task
    assert eng._task.exception() is None, eng._task.exception()
    (s,) = session_store.load_all()
    return s


def _arm(eng, hub, monkeypatch, night: Night) -> ResumeArm:
    monkeypatch.setattr(ResumeArm, "_window_open", lambda self, s, t: True)
    return ResumeArm(eng, hub, clock=night.clock)


def _count_starts(eng, monkeypatch) -> list:
    starts: list = []
    real = eng.start

    def counting(*a, **k):
        starts.append(k.get("session"))
        return real(*a, **k)
    monkeypatch.setattr(eng, "start", counting)
    return starts


async def _tick_through(eng, arm: ResumeArm, night: Night, gaps=TICK_GAPS_S):
    for gap in gaps:
        night.advance(gap)
        await arm.tick()
        if eng._task is not None:       # a tick that started a run: let it end
            await eng._task


async def test_a_session_whose_flat_will_not_meter_is_not_restarted_each_tick(
        sim_hub, monkeypatch, bus_lines):
    """THE LOOP, graded across ticks. The first run ends with the state the
    issue measured (dormant, armed, all six frames owed, none shot), and the
    ticks that follow, a minute apart and then past the ten-minute retry,
    start NOTHING: the session holds in words, still armed for the next night.

    Red on the unfixed code, observed (4 ticks, 4 restarts, each one the full
    ladder and a run that closes the cover, lights the lamp and fails again):

        AssertionError: ResumeArm restarted a session whose flat cannot
        meter on 4 of 4 ticks
        assert 4 == 0

    MUTANT "the run records nothing" (``_finalize_report``: the
    ``if self._flat_metered:`` guard made ``if False:``; 6 failed, 9 passed)
    and MUTANT "the refusal ignores the record" (``nothing_to_shoot_tonight``:
    ``aside`` made an empty set; 4 failed, 11 passed) each turn this red the
    same way, observed:

        AssertionError: ResumeArm restarted a session whose flat cannot
        meter on 4 of 4 ticks
    """
    night = Night(monkeypatch)
    eng = SequenceEngine(sim_hub)
    s = await _run_to_its_end(eng, _plan())
    assert (s.status, s.auto_resume, s.owed(), len(s.frames)) == (
        "dormant", True, 6, 0), "premise: the state the issue measured"
    assert s.plan.resume_across_nights, "premise: a campaign, not a single night"
    arm = _arm(eng, sim_hub, monkeypatch, night)
    starts = _count_starts(eng, monkeypatch)

    await _tick_through(eng, arm, night)

    assert len(starts) == 0, (
        f"ResumeArm restarted a session whose flat cannot meter on "
        f"{len(starts)} of {len(TICK_GAPS_S)} ticks")
    held = arm.hold
    assert held is not None and held["reason"] == NOTHING_TONIGHT, held
    assert held["session_id"] == s.id and held["owed"] == 6, held
    again = session_store.load(s.id)
    assert (again.status, again.auto_resume) == ("dormant", True), (
        "the hold must keep the session armed: the next night tries it again")


async def test_the_next_night_meters_it_afresh(sim_hub, monkeypatch):
    """``resume_across_nights`` is a promise about LATER nights, and the
    record is the night's: the same session is started once on the next
    night, fails the same way, and is held again for that night.

    MUTANT "any night's record holds" (``nothing_to_shoot_tonight``: the
    records read from ``session.set_aside``, not ``set_aside_on(night)``;
    2 failed, 13 passed) turns this red, observed:

        AssertionError: the next night did not start the session once: 0
    """
    night = Night(monkeypatch)
    eng = SequenceEngine(sim_hub)
    s = await _run_to_its_end(eng, _plan())
    arm = _arm(eng, sim_hub, monkeypatch, night)
    starts = _count_starts(eng, monkeypatch)
    await _tick_through(eng, arm, night, gaps=(60.0, RETRY_INTERVAL_S + 1.0))
    assert len(starts) == 0, "premise: night one is held"

    night.next_night(NIGHT_TWO)
    await _tick_through(eng, arm, night)

    assert len(starts) == 1, (
        f"the next night did not start the session once: {len(starts)}")
    after = session_store.load(s.id)
    assert {r["night"] for r in after.set_aside} == {NIGHT_ONE, NIGHT_TWO}, (
        "each night leaves its own record")
    assert (after.status, after.auto_resume) == ("dormant", True)


async def test_the_run_records_only_the_steps_the_session_owes_unmetered(
        sim_hub, monkeypatch):
    """One filter meters and one does not. The metered filter's frames are
    shot and nothing is recorded for it; the unmetered filter is recorded
    once, for the session's own step, with the kind that says why.

    MUTANT "a metered step is recorded too" (``_record_flat_metering``: the
    ``why is not None and remaining.get(s.id, 0) > 0`` condition made
    ``True``; 4 failed, 11 passed) turns this red, observed:

        AssertionError: ['L', 'R'] != ['R']
    """
    Night(monkeypatch)
    eng = SequenceEngine(sim_hub)
    plan = _plan(brightness={"L": 100})
    s = await _run_to_its_end(eng, plan)

    by_step = {st.id: st.filter for st in s.plan.targets[0].steps}
    recorded = [by_step[r["step_id"]] for r in s.set_aside_on(NIGHT_ONE)]
    assert recorded == ["R"], f"{recorded} != ['R']"
    (rec,) = s.set_aside_on(NIGHT_ONE)
    assert rec["target_id"] == s.plan.targets[0].id
    assert rec["kind"] == "unmetered" and "too_dim_at_max" in rec["reason"], rec
    assert s.owed() == 3 and len(s.frames) == 3, (
        "premise: L was shot in full, R not at all")


async def test_a_flat_that_meters_leaves_no_record_and_the_session_completes(
        sim_hub, monkeypatch):
    """THE CONTROL. With the sim panel lit the solve converges, every frame
    is shot, the session completes (and is disarmed, #838), and nothing is
    set aside. If the harness could not reach a converged solve the red cases
    above would pass for nothing."""
    Night(monkeypatch)
    eng = SequenceEngine(sim_hub)
    s = await _run_to_its_end(eng, _plan(brightness={"L": 100, "R": 100}))
    assert (s.status, s.owed()) == ("complete", 0)
    assert s.set_aside == [], s.set_aside


async def test_the_dusk_flats_stage_records_nothing(sim_hub, monkeypatch):
    """The DUSK FLATS stage runs sets of its own through ``_run_calibration``;
    the plan owes none of them, so a set that would not meter there is not a
    step the session owes and leaves no record. The lights are the session's
    only debt (nothing shoots them here), and the stage's two unmetered sets
    are not among the records.

    MUTANT "every unmetered key is recorded" (``_record_flat_metering``
    walking ``self._flat_metered`` instead of the plan's steps; 4 failed, 11
    passed) turns this red, observed:

        AssertionError: [{'kind': 'unmetered', 'night': '2026-03-10',
        'reason': 'x', 'step_id': '68681b2f...', ...}, {...}]
    """
    from astrodeck.devices.sim import SimCoverCalibrator
    monkeypatch.setattr(SimCoverCalibrator, "ADU_PER_BRIGHTNESS", 1e-6)
    Night(monkeypatch)
    plan = SequencePlan(
        name="Flats night", guide=False, dither_every=0,
        targets=[Target(name="M31", ra_hours=0.7, dec_deg=41.0, center=False,
                        autofocus_first=False,
                        steps=[ExposureStep(filter=f, exposure_s=1.0, gain=100,
                                            offset=30, count=1)
                               for f in ("L", "R")])],
        dusk_flats=DuskFlatsPlan(method="panel", filters=["L", "R"],
                                 adu_target=8000, count=3))
    eng = SequenceEngine(sim_hub)

    async def no_lights(_plan):
        return None
    monkeypatch.setattr(eng, "_run_scheduled", no_lights)
    s = await _run_to_its_end(eng, plan)
    assert s.owed() == 2, "premise: the lights are owed and nothing shot them"
    assert sorted(v for v in eng._flat_metered.values() if v) == [
        "too_dim_at_max"] * 2, "premise: the stage met two sets it could not meter"
    assert s.set_aside == [], s.set_aside


async def test_a_start_by_hand_meters_again_tonight(sim_hub, monkeypatch):
    """The record holds the AUTOMATIC restart, not the person: CONTINUE on the
    same night, after the lamp was fixed, meters the flats again."""
    Night(monkeypatch)
    eng = SequenceEngine(sim_hub)
    s = await _run_to_its_end(eng, _plan(filters=("L",)))
    assert s.set_aside_on(NIGHT_ONE), "premise: tonight's record stands"
    tap = Tap(sim_hub, monkeypatch)

    eng.start(s.plan, session=session_store.load(s.id))
    await eng._task

    assert tap.trials, "a start by hand was held by tonight's record"


async def test_a_later_run_that_meters_a_step_clears_its_earlier_record(
        sim_hub, monkeypatch):
    """A run's answer replaces an earlier one's. Both filters fail to meter and
    are recorded; the lamp is then fixed for L, a start by hand shoots L in
    full, and R fails again. Tonight's standing records are R's alone: L's
    earlier failure no longer holds anything, and R is recorded once, not
    twice.

    MUTANT "an earlier record is kept" (``_record_flat_metering``: the
    ``session.note_set_aside_cleared`` call removed) turns this red, observed:

        AssertionError: ['L', 'R', 'R'] != ['R']
    """
    Night(monkeypatch)
    eng = SequenceEngine(sim_hub)
    s = await _run_to_its_end(eng, _plan())
    assert len(s.set_aside_on(NIGHT_ONE)) == 2, "premise: both recorded"

    again = session_store.load(s.id)
    again.plan.targets[0].steps[0].panel_brightness = 100     # L's lamp fixed
    eng.start(again.plan, session=again)
    await eng._task

    s = session_store.load(s.id)
    by_step = {st.id: st.filter for st in s.plan.targets[0].steps}
    standing = [by_step[r["step_id"]] for r in s.set_aside_on(NIGHT_ONE)]
    assert standing == ["R"], f"{standing} != ['R']"
    assert s.owed() == 3 and len(s.frames) == 3, "premise: L shot, R did not"


async def test_a_step_the_session_no_longer_owes_is_not_recorded(
        sim_hub, monkeypatch, bus_lines):
    """``_run_calibration`` meters a flat step before it looks at how many
    frames the step still owes, so a step the ledger holds in full is metered
    again on a later start. If that solve fails (here R's lamp is taken away
    on the second start) there is nothing to set aside: the session owes R
    nothing, and a record for it would hold a session that has other work.
    L, which is owed, is recorded.

    MUTANT "a step nothing is owed on is recorded too" (``_record_flat_
    metering``: the ``remaining.get(s.id, 0) > 0`` clause removed) turns this
    red, observed:

        AssertionError: ['L', 'R'] != ['L']
    """
    Night(monkeypatch)
    eng = SequenceEngine(sim_hub)
    s = await _run_to_its_end(eng, _plan(brightness={"R": 100}))
    assert s.owed() == 3 and len(s.frames) == 3, "premise: R shot, L not"

    again = session_store.load(s.id)
    again.plan.targets[0].steps[1].panel_brightness = None    # R's lamp gone
    bus_lines.clear()
    eng.start(again.plan, session=again)
    await eng._task
    assert [m for lvl, m, _s in bus_lines
            if "did not converge on R " in m], "premise: R was metered again"

    s = session_store.load(s.id)
    by_step = {st.id: st.filter for st in s.plan.targets[0].steps}
    standing = [by_step[r["step_id"]] for r in s.set_aside_on(NIGHT_ONE)]
    assert standing == ["L"], f"{standing} != ['L']"


def _calibration_session(*, light_done: bool = False,
                         aside: tuple[str, ...] = ("L", "R")) -> Session:
    """A session owing the flats of ``plan`` (and, with ``light_done`` False,
    a light target's frame), with the flats named in ``aside`` set aside for
    ``NIGHT_ONE``."""
    flats = _plan().targets[0]
    light = Target(name="M31", ra_hours=0.7, dec_deg=41.0, center=False,
                   autofocus_first=False,
                   steps=[ExposureStep(filter="L", exposure_s=60.0, count=1)])
    plan = SequencePlan(name="p", targets=[light, flats])
    s = Session(status="dormant", plan=plan, auto_resume=True)
    if light_done:
        s.frames.append(SessionFrame(target_id=light.id,
                                     step_id=light.steps[0].id, ts=1.0,
                                     night=NIGHT_ONE))
    for step in flats.steps:
        if step.filter in aside:
            s.note_set_aside(flats.id, "would not meter", night=NIGHT_ONE,
                             step_id=step.id, kind="unmetered")
    return s


class TestNothingToShootTonightReadsCalibrationRecords:
    """The refusal is a pure function of the session, the candidates and the
    night it is asked on."""

    def test_every_owed_calibration_step_set_aside_is_nothing_to_shoot(self):
        s = _calibration_session(light_done=True)
        assert nothing_to_shoot_tonight(s, [], night=NIGHT_ONE)

    def test_one_open_calibration_step_is_something_to_shoot(self):
        s = _calibration_session(light_done=True, aside=("L",))
        assert not nothing_to_shoot_tonight(s, [], night=NIGHT_ONE)

    def test_another_nights_record_holds_nothing(self):
        s = _calibration_session(light_done=True)
        assert not nothing_to_shoot_tonight(s, [], night=NIGHT_TWO)

    def test_asked_without_a_night_it_answers_as_it_did_before(self):
        s = _calibration_session(light_done=True)
        assert not nothing_to_shoot_tonight(s, [])

    def test_a_light_target_owed_beside_set_aside_flats_is_nothing_tonight(self):
        s = _calibration_session(light_done=False)
        assert nothing_to_shoot_tonight(s, [], night=NIGHT_ONE)

    def test_a_candidate_is_always_something_to_shoot(self):
        s = _calibration_session(light_done=False)
        light = s.plan.targets[0]
        assert not nothing_to_shoot_tonight(s, [light], night=NIGHT_ONE)

    def test_a_session_owing_nothing_is_not_refused(self):
        s = _calibration_session(light_done=True)
        for t in s.plan.targets:
            for st in t.steps:
                for _ in range(st.count):
                    s.frames.append(SessionFrame(target_id=t.id, step_id=st.id,
                                                 ts=1.0, night=NIGHT_ONE))
        assert s.owed() == 0
        assert not nothing_to_shoot_tonight(s, [], night=NIGHT_ONE)


def test_the_wind_down_counts_tonights_unmetered_flats_as_spent():
    """The wind-down's cooler question (``spent_tonight``, #887) asks the
    refusal the ladder asks, so it must read tonight's records too: a session
    whose every owed flat would not meter tonight is spent, and its cooler may
    warm, as the ladder will not restart it. Asked on the next night the same
    session is not spent. MUTANT "spent_tonight asks without the night" (the
    ``night`` argument dropped from its ``nothing_to_shoot_tonight`` call)
    turns the first assertion red."""
    s = _calibration_session(light_done=True)
    assert spent_tonight(s, None, -12.0, _evening_of(NIGHT_ONE))
    assert not spent_tonight(s, None, -12.0, _evening_of(NIGHT_TWO))
