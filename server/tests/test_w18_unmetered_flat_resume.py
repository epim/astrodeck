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
  (``NOTHING_TONIGHT_UNMETERED``, which names the flats and what to check,
  #911; ``NOTHING_TONIGHT`` for every other cause) on the ten-minute retry,
  said once a night;
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

import re
import time

import astrodeck.sequence.engine as engine_module
from _simhub import sim_hub  # noqa: F401 (fixture import)

from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import (DuskFlatsPlan, ExposureStep,
                                       SequencePlan, Target)
from astrodeck.sequence.resume_arm import (NOTHING_TONIGHT,
                                           NOTHING_TONIGHT_UNMETERED,
                                           RETRY_INTERVAL_S, ResumeArm,
                                           flats_would_not_meter,
                                           nothing_to_shoot_tonight,
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


class Lamp:
    """Every time the engine lights the flat panel: the brightness asked."""

    def __init__(self, hub, monkeypatch):
        self.lit: list[int] = []
        real = hub.calibrator_on

        async def calibrator_on(brightness):
            self.lit.append(brightness)
            await real(brightness)
        monkeypatch.setattr(hub, "calibrator_on", calibrator_on)


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
    assert held is not None and held["reason"] == NOTHING_TONIGHT_UNMETERED, held
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
    """A step the ledger holds in full is not metered on a later start (#910),
    so a lamp that has gone for it (here R's, taken away on the second start)
    says nothing and sets nothing aside: the session owes R nothing, and a
    warning or a record for it would be about a step that needed no flat. L,
    which is owed, is metered, fails, and is recorded.

    This case used to PIN THE DEFECT (#910): ``_run_calibration`` metered R
    before it looked at what R still owed, and the case asserted that
    "did not converge on R" was said. It now asserts the opposite. MUTANT "a
    finished step is metered again" (``_run_calibration``: the
    ``self._done.get(key, 0) >= step.count`` skip removed) turns it red,
    observed:

        AssertionError: R, which the ledger holds in full, was metered again
        (warnings: ['... flat exposure did not converge on R (too_dim_at_max)
        - none of its 3 flats are shot, and the next step goes on'])

    The ``remaining.get(s.id, 0) > 0`` clause of ``_record_flat_metering``
    is no longer what this case grades: the engine does not meter a step the
    session owes nothing on, so there is no outcome for the clause to drop.
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
    said = [m for lvl, m, _s in bus_lines if "did not converge on R " in m]
    assert not said, (f"R, which the ledger holds in full, was metered again "
                      f"(warnings: {said})")
    assert [m for lvl, m, _s in bus_lines
            if "did not converge on L " in m], (
        "premise: L, which is owed, was metered and did not converge")

    s = session_store.load(s.id)
    by_step = {st.id: st.filter for st in s.plan.targets[0].steps}
    standing = [by_step[r["step_id"]] for r in s.set_aside_on(NIGHT_ONE)]
    assert standing == ["L"], f"{standing} != ['L']"


async def test_a_step_the_ledger_holds_in_full_is_not_metered_again(
        sim_hub, monkeypatch, bus_lines):
    """#910. R is first in the plan and its lamp works; L, second, has no
    lamp. The first run shoots R in full and cannot meter L. The lamp is then
    fixed for L and the wheel is left on G (whichever filter is in the beam),
    and the session is started again: it owes L three frames and R none.

    The start meters L, through L, and shoots it. It does not close the
    cover, light the lamp or take a trial exposure for R, which the ledger
    holds in full: unfixed, R's metering ran first, with the wheel on G
    (the filter move is skipped for a step that owes nothing, the metering
    was not), so the trial exposures of the start included G's.

    MUTANT "a finished step is metered again" (``_run_calibration``: the
    ``self._done.get(key, 0) >= step.count`` skip removed) turns this red,
    observed:

        AssertionError: trial exposures through ['G', 'G', 'L', 'L']; a step
        the ledger holds in full was metered

    The same mutant turns the case above it red as well, on the warning.
    """
    Night(monkeypatch)
    eng = SequenceEngine(sim_hub)
    s = await _run_to_its_end(
        eng, _plan(brightness={"R": 100}, filters=("R", "L")))
    assert s.owed() == 3 and len(s.frames) == 3, "premise: R shot, L not"
    fw = sim_hub.devices["filterwheel"]
    await fw.set_position(fw.filter_names.index("G"))

    again = session_store.load(s.id)
    again.plan.targets[0].steps[1].panel_brightness = 100     # L's lamp fixed
    tap = Tap(sim_hub, monkeypatch)
    lamp = Lamp(sim_hub, monkeypatch)
    bus_lines.clear()
    eng.start(again.plan, session=again)
    await eng._task
    assert eng._task.exception() is None, eng._task.exception()

    assert tap.trials, "premise: L was metered"
    assert set(tap.trials) == {"L"}, (
        f"trial exposures through {tap.trials}; a step the ledger holds in "
        f"full was metered")
    assert lamp.lit == [100], (
        f"the lamp was lit {len(lamp.lit)} times for a start that owes one "
        f"step")
    warned = [m for lvl, m, _s in bus_lines
              if lvl == "warning" and "flat exposure did not converge" in m]
    assert warned == [], warned
    s = session_store.load(s.id)
    assert (s.status, s.owed(), len(s.frames)) == ("complete", 0, 6)


def _calibration_session(*, light_done: bool = False,
                         aside: tuple[str, ...] = ("L", "R"),
                         kind: str | None = "unmetered") -> Session:
    """A session owing the flats of ``plan`` (and, with ``light_done`` False,
    a light target's frame), with the flats named in ``aside`` set aside for
    ``NIGHT_ONE`` by a record of ``kind``."""
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
                             step_id=step.id, kind=kind)
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


async def test_the_hold_for_flats_that_will_not_meter_says_so(
        sim_hub, monkeypatch, bus_lines):
    """#911, graded across ticks like the loop above. The Monitor's hold and
    the one warning the night gets name the flats and what to check, not a
    window or a start altitude; the hold carries no site-derived sentence;
    and the refusal keeps the once-a-night latch and the ten-minute retry it
    shares with ``NOTHING_TONIGHT`` (four ticks, one warning, the retry from
    the last tick).

    MUTANT "the refusal ignores the cause" (``ResumeArm._recover``: the
    ``flats_would_not_meter`` choice made ``NOTHING_TONIGHT`` always; 4
    failed, 25 passed) turns this red, observed:

        AssertionError: {'owed': 6, 'reason': "none of this session's
        remaining frames can be captured tonight: the remaining targets are
        set aside for tonight, past their observing windows, or never above
        their minimum start altitude; waiting until the next night before
        slewing", ...}

    MUTANT "the kind is not read" (``flats_would_not_meter``: the
    ``r.get("kind") == "unmetered"`` clause removed; 2 failed, 27 passed)
    turns the two controls red instead: a record of another kind, and flats
    set aside for another reason, get the unmetered words.
    """
    night = Night(monkeypatch)
    eng = SequenceEngine(sim_hub)
    await _run_to_its_end(eng, _plan())
    arm = _arm(eng, sim_hub, monkeypatch, night)
    bus_lines.clear()

    await _tick_through(eng, arm, night)

    held = arm.hold
    assert held is not None and held["reason"] == NOTHING_TONIGHT_UNMETERED, held
    assert "site_detail" not in held, held
    warned = [m for lvl, m, _s in bus_lines
              if lvl == "warning" and m.startswith("auto-resume held:")]
    assert len(warned) == 1 and NOTHING_TONIGHT_UNMETERED in warned[0], warned
    assert "observing windows" not in warned[0], warned
    assert arm._retry_at == night.t + RETRY_INTERVAL_S, arm._retry_at


def test_the_words_for_flats_that_will_not_meter_name_the_cause_and_the_cure():
    """The sentence is words only (#233): the hold's ``reason`` reaches a
    viewer, so it carries no digit, and it names the flats, the three things
    to check and the way back in."""
    words = NOTHING_TONIGHT_UNMETERED
    assert not re.search(r"\d", words), words
    for part in ("flat exposures would not meter", "the lamp", "the cover",
                 "the sky", "next night", "CONTINUE"):
        assert part in words, (part, words)
    assert words != NOTHING_TONIGHT


class TestFlatsWouldNotMeter:
    """Which words the refusal is said in is a pure function of the session
    and the night it is asked on."""

    def test_an_owed_flat_set_aside_as_unmetered_tonight_is(self):
        s = _calibration_session(light_done=True)
        assert flats_would_not_meter(s, NIGHT_ONE)

    def test_another_nights_record_is_not(self):
        s = _calibration_session(light_done=True)
        assert not flats_would_not_meter(s, NIGHT_TWO)

    def test_a_record_of_another_kind_is_not(self):
        for kind in (None, "deferred", "centring"):
            s = _calibration_session(light_done=True, kind=kind)
            assert not flats_would_not_meter(s, NIGHT_ONE), kind

    def test_a_flat_the_session_no_longer_owes_is_not(self):
        s = _calibration_session(light_done=True)
        flats = s.plan.targets[1]
        for st in flats.steps:
            for _ in range(st.count):
                s.frames.append(SessionFrame(target_id=flats.id, step_id=st.id,
                                             ts=1.0, night=NIGHT_ONE))
        assert s.owed() == 0
        assert not flats_would_not_meter(s, NIGHT_ONE)

    def test_one_unmetered_flat_among_owed_steps_is(self):
        s = _calibration_session(light_done=True, aside=("L",))
        assert flats_would_not_meter(s, NIGHT_ONE)

    def test_a_light_target_out_of_tonight_does_not_hide_an_unmetered_flat(self):
        s = _calibration_session(light_done=False)
        light = s.plan.targets[0]
        s.note_set_aside(light.id, "window", night=NIGHT_ONE)
        assert nothing_to_shoot_tonight(s, [], night=NIGHT_ONE)
        assert flats_would_not_meter(s, NIGHT_ONE)


class TestTheLadderSaysWhichWayItRefused:
    """``ResumeArm._recover``, unmodified, over sessions that need no device:
    the refusal comes before the safety read, the focuser or the solve."""

    async def _refusal(self, sim_hub, monkeypatch, s: Session) -> str | None:
        night = Night(monkeypatch)
        arm = _arm(SequenceEngine(sim_hub), sim_hub, monkeypatch, night)
        return await arm._recover(s)

    async def test_unmetered_flats_are_said_as_such(self, sim_hub,
                                                    monkeypatch):
        s = _calibration_session(light_done=True)
        got = await self._refusal(sim_hub, monkeypatch, s)
        assert got == NOTHING_TONIGHT_UNMETERED, got

    async def test_flats_set_aside_for_another_reason_keep_the_generic_words(
            self, sim_hub, monkeypatch):
        s = _calibration_session(light_done=True, kind=None)
        got = await self._refusal(sim_hub, monkeypatch, s)
        assert got == NOTHING_TONIGHT, got

    async def test_a_light_target_out_of_tonight_alone_keeps_the_generic_words(
            self, sim_hub, monkeypatch):
        light = Target(name="M31", ra_hours=0.7, dec_deg=41.0, center=False,
                       autofocus_first=False,
                       steps=[ExposureStep(filter="L", exposure_s=60.0,
                                           count=1)])
        s = Session(status="dormant", auto_resume=True,
                    plan=SequencePlan(name="p", targets=[light]))
        s.note_set_aside(light.id, "window", night=NIGHT_ONE)
        got = await self._refusal(sim_hub, monkeypatch, s)
        assert got == NOTHING_TONIGHT, got

    async def test_a_light_target_out_of_tonight_beside_unmetered_flats_names_the_flats(
            self, sim_hub, monkeypatch):
        s = _calibration_session(light_done=False)
        s.note_set_aside(s.plan.targets[0].id, "window", night=NIGHT_ONE)
        got = await self._refusal(sim_hub, monkeypatch, s)
        assert got == NOTHING_TONIGHT_UNMETERED, got
