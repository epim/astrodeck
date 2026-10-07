# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Engine scheduler correctness (WP-140; #726, #728, #737, #738, #764).

Five defects the wave 15 coders found beside their own work packages, none of
them in the code they were allowed to touch:

* #726. `_wait_until` sleeps in ``SCHEDULE_WAIT_STEP_S`` steps for its safety
  gate and its idle clock and returns at its deadline, so the scheduler looked
  at its queues again only when the soonest waiter's wake arrived: 45 minutes
  for a panel set aside for now, longer for a window or a meridian crossing.
  WP-104 ended the wait early for a queued retry only. The wait now ends at the
  first step after ANYTHING is queued for the scheduler's next selection (a
  retry, or a skip aimed at a future target), through one shared check.
  THE WAIT STAYS ON THE ENGINE'S OWN TASK, a step at a time, and that is not
  incidental: the clocked harness (tests/_group_harness.py) parks only the
  engine's tasks on its fake clock, so a wait that raced a timer against an
  event on a task of its own would be real seconds in every night that idles.
  Every case here runs on that harness, so one that lost the harness's clock
  would fail on its real-time bound and not pass by waiting.
* #728. A retry asked between the scheduler returning and `_finalize_report`
  clearing the session (the idle-stop's wait, up to ``IDLE_STOP_FINISH_S``) is
  answered ``live`` and queued, and nothing drained it: the next `start`
  discarded it, and the operator had been told it was coming back.
  `_finalize_report` now hands what is still queued to the dormant session as
  the dormant route would (the record marked ``cleared``), says so, and a
  continue the same night takes the panel up.
* #737. After the guiding recoveries are spent under ``guiding_action =
  warn`` the stand-down warning was said at every frame boundary for the rest
  of the night. It is said once for a target in a run and again for a new
  spell, target or run, as the sky read beside it is (#704); the state's
  detail stays.
* #738. Target setup's uncentred branch recovered a refused track without
  taking the recovery's re-centre as its own, so the recovery recorded a
  sky-angle row and setup recorded the same solve again at its common call.
* #764. Since the #711 seed a cloud hold entered before the run's first frame
  has a step, so it could shoot hold darks before the camera had cooled. Before
  the first frame the hold now shoots a dark only on a camera not MEASURED away
  from its cooling target; nobody-can-say (no camera, no cooler, an unreadable
  sensor) is not a verdict, and after the first frame nothing changes.

Named mutants, each applied from a byte backup of ``sequence/engine.py``, run,
and restored byte-identically (sha256 compared, the mutant text grepped gone);
the observed failure is recorded in the docstring of the case it turned red.
"""
from __future__ import annotations

import time as _time

import astrodeck.sequence.engine as engine_mod
from _flow_night import DAY_S, FlowRig, flow_rig  # noqa: F401
from _group_harness import (GROUP_ID, GROUP_NAME, T0, Night, grid_plan,  # noqa: F401
                            group_hub, group_store, single)
from astrodeck.events import night_key
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.engine import SCHEDULE_WAIT_STEP_S
from astrodeck.sequence.instructions import FiredAction
from astrodeck.sequence.models import ExposureStep, Schedule, Target
from astrodeck.sequence.session import session_store

from test_h4_sky_angle_after_untouched_slew import (  # noqa: F401
    _run, _sky, mount_at_its_limit)
from test_w14_sky_before_recovery import (  # noqa: F401
    CLEAR, _rig, _target, _warnings, sim_hub)
from test_w15_guiding_recovery_stand_down import FRAMES, _a_frame_later

#: REAL seconds a night may take, and the spin watchdog's bound: generous
#: because these nights run beside other suites on a shared box (#669, #675).
WALL_S = 240.0
SPIN_S = 60.0
#: An hour on: still the night the first run was in (``night_key``).
LATER_TONIGHT = 3600.0 / DAY_S

MISSES = f"{GROUP_NAME} 2-2"
STAND_DOWN = "standing down from recovery"


def _hhmm(t: float) -> str:
    return _time.strftime("%H:%M", _time.localtime(t))


def _a_plan(*targets, **kw) -> SequencePlan:
    return SequencePlan(name="w16", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False,
                        park_when_done=False, warm_cooler_when_done=False,
                        recover_guiding=False, targets=list(targets), **kw)


# ------------------------------------------------------------ #726 the idle wait

async def test_a_skip_queued_while_the_scheduler_idles_is_taken_at_the_next_step(
        flow_rig):
    """The scheduler waits two hours for "Waiter"'s window to open. Ten
    minutes in, with every engine task parked on the harness's clock, a
    ``skip_target`` aimed at it is queued the way an instruction queues one
    for a target that is not the one in hand (`_dispatch_actions`). The skip
    is drained at the next selection, and the selection comes within one
    ``SCHEDULE_WAIT_STEP_S`` of the request and not at the window an hour
    and fifty minutes on: the target leaves the night at once, is never shot,
    and the run ends.

    TODAY'S ONLY PRODUCER of a queued skip is an instruction at a frame
    boundary, which is never during the scheduler's idle wait; the wait
    ending on one is the guard that rides the hazard's clock (the queue)
    instead of the loop top, so a producer that reaches the queue while the
    scheduler idles (the class #726 names) is not made to wait for the
    wait's end. The producer is therefore called directly, from the test.

    MUTANT "the idle wait ignores a queued skip" (`_queued_for_the_scheduler`
    reduced to ``return bool(self._pending_retries)``, so only a retry ends
    the wait): RED (observed):

        AssertionError: the skip asked at +600.0 s was taken at +7191.0 s: it
        waited for the 7200 s wait's own end
        assert (1788320880.0 - 1788314289.0) <= (5.0 + 0.5)
    MUTANT "the idle wait never ends early" (``and not
    self._queued_for_the_scheduler()`` removed from `_wait_until_or_queued`'s
    loop): RED, the same lines (observed), and so is WP-104's
    test_w15_retry_set_aside_night.py::
    test_a_live_retry_takes_a_set_aside_for_now_at_once_not_at_its_expiry
    (``2-2 was tried at [120.0, 180.0, 360.0, 3240.0, 3300.0, 3360.0], the
    retry at 1200.0 s was not taken up: it waited for its expiry``). WP-104's
    case is RED too under "the idle wait ignores a queued retry" (``return
    bool(self._pending_skips)``, observed), where this one stays GREEN: the
    two queues are each pinned by a case of their own.
    """
    rig: FlowRig = flow_rig
    night = await rig.night(spin_bound_s=SPIN_S)
    opens = night.t0 + 2 * 3600.0
    waiter = single("Waiter", count=1,
                    schedule=Schedule(start_mode="time",
                                      start_time=_hhmm(opens)))
    night.engine.start(_a_plan(waiter))
    assert await night.until(night.t0 + 600.0, wall_s=WALL_S), (
        "premise: the night is waiting at the hold")
    assert str(night.engine.state.get("detail", "")).startswith(
        "waiting for Waiter"), (
        f"premise: the scheduler is idling for the waiter: "
        f"{night.engine.state.get('detail')!r}")
    asked = night.clock.t
    assert night.shots() == [], "premise: nothing has been shot"

    other = Target(name="Other", ra_hours=1.0, dec_deg=10.0, center=False,
                   autofocus_first=False,
                   steps=[ExposureStep(filter="L", exposure_s=30.0, count=1)])
    await night.engine._dispatch_actions(
        [FiredAction("i1", "skip_target", "", "info", target_arg="Waiter")],
        other, None)
    assert night.engine._pending_skips == {"Waiter"}, (
        "premise: the skip is queued for the scheduler")

    night.release()
    await night.finish(wall_s=WALL_S)
    taken = [t for t, _lvl, m in night.lines
             if m == "Waiter: skipped by instruction"]
    assert len(taken) == 1, night.said("Waiter")
    assert taken[0] - asked <= SCHEDULE_WAIT_STEP_S + 0.5, (
        f"the skip asked at +{night.rel(asked)} s was taken at "
        f"+{night.rel(taken[0])} s: it waited for the "
        f"{opens - night.t0:g} s wait's own end")
    assert night.shots() == [], "the skipped waiter was shot"
    assert night.engine._pending_skips == set()


async def test_control_an_idle_wait_with_nothing_queued_runs_to_the_wake(
        flow_rig):
    """CONTROL. The same two-hour wait with nothing queued: the scheduler
    sleeps its steps through to the window and shoots the target when it
    opens, on the harness's clock, in the bounded real time the night
    allows. A wait that ended early on nothing, or spun, would shoot before
    the window or fail the spin watchdog.

    The window is ``start_time`` truncated to the minute, so it opens up to a
    minute before ``opens``; the first exposure is not before that and not
    long after the hop that follows it.
    """
    rig: FlowRig = flow_rig
    night = await rig.night(spin_bound_s=SPIN_S)
    opens = night.t0 + 2 * 3600.0
    waiter = single("Waiter", count=1,
                    schedule=Schedule(start_mode="time",
                                      start_time=_hhmm(opens)))
    night.engine.start(_a_plan(waiter))
    await night.until(night.t0 + 600.0, wall_s=WALL_S)
    night.release()
    await night.finish(wall_s=WALL_S)
    first = night.captures[0]["t"] if night.captures else None
    window = opens - (opens % 60.0)
    assert first is not None, f"Waiter was never shot: {night.lines[-4:]}"
    assert window <= first <= window + 300.0, (
        f"the first exposure was at +{night.rel(first)} s, the window opened "
        f"at +{night.rel(window)} s")


# ------------------------------------- #728 a retry asked after the scheduler

def _no_stars_while(bad: dict):
    return lambda who, _filt: 0 if who == MISSES and bad["on"] else 50


def _the_plan():
    return grid_plan(count_mode="accepted", min_stars=5,
                     max_consecutive_rejects=5,
                     max_consecutive_rejects_night=0,
                     panel_kw={"count": 6})


async def test_a_retry_asked_after_the_scheduler_returned_is_honoured_on_the_session(
        flow_rig, bus_lines):
    """The night ends with 2-2 set aside for good (its frames carry no stars
    and its third rejected visit sets it aside), the other three complete.
    The scheduler has returned and the run is in the idle-stop's wait, with
    the session still held: the operator presses RETRY and is told, live,
    that 2-2 is queued. Nothing drains the queue any more, so the run's end
    hands it to the stored session: the record is marked ``cleared``, the
    log says so once, the queue is empty, and a continue the same night
    shoots all twelve of 2-2's frames.

    The window is reached by wrapping ONE engine method, `_finish_idle_stop`,
    the first await after the scheduler returns: the wrapper asks
    ``retry_set_aside`` and then runs the real method. Nothing else of the
    engine is replaced.

    MUTANT "the hand-off removed" (the call to
    `_hand_pending_retries_to_the_session` in `_finalize_report` replaced by
    ``pass``): RED (observed):

        AssertionError: the queue was left holding the retry: ['p11']
        assert ['p11'] == []
    MUTANT "the hand-off clears nothing" (the targets handed to
    ``note_set_aside_cleared`` made ``[]``): RED (observed):

        AssertionError: the retry the operator was told was queued is not on
        the stored session: [{'target_id': 'p11', 'step_id': None, 'reason':
        '2-2 rejected every frame for 3 visits while the other panels were
        accepted', 'night': '2026-09-01', 'kind': 'rejects', 'ts':
        1788314229.0}]
    """
    rig: FlowRig = flow_rig
    bad = {"on": True}
    night = await rig.night(spin_bound_s=SPIN_S, stars=_no_stars_while(bad))
    eng = night.engine
    answers: list[dict] = []
    real = eng._finish_idle_stop

    async def finish(*a, **kw):
        # The scheduler has returned; the session has not been cleared.
        answers.append({"running": eng.running,
                        "held": eng._session is not None,
                        "answer": eng.retry_set_aside(GROUP_ID)})
        return await real(*a, **kw)

    rig.mp.setattr(eng, "_finish_idle_stop", finish)
    eng.start(_the_plan())
    sid = eng._session.id
    await night.finish(wall_s=WALL_S)
    assert answers and answers[0]["running"] and answers[0]["held"], (
        f"premise: the retry was asked with the run live and its session "
        f"held: {answers}")
    assert answers[0]["answer"] == {"live": True, "queued": ["2-2"]}, (
        f"premise: the engine told the operator it was queued: {answers}")
    assert eng._pending_retries == [], (
        f"the queue was left holding the retry: {eng._pending_retries}")

    stored = session_store.load(sid)
    assert stored.status == "dormant" and stored.owed() == 12, (
        stored.status, stored.owed())
    tonight = night_key(night.clock.t)
    assert stored.set_aside_on(tonight) == [], (
        f"the retry the operator was told was queued is not on the stored "
        f"session: {stored.set_aside_on(tonight)}")
    records = [x for x in stored.set_aside if x["target_id"] == "p11"]
    assert records and all(x.get("cleared") for x in records), records
    # `bus_lines`, not ``night.said``: the harness stops recording at the
    # run's first terminal publish, and this line is the finalize's, after it.
    said = [m for _lvl, m, _src in bus_lines
            if "set-aside panels retried by the operator" in m]
    assert len(said) == 1 and "2-2" in said[0], said

    bad["on"] = False
    again = await rig.night(spin_bound_s=SPIN_S, day=LATER_TONIGHT)
    again.engine.start(stored.plan, session=stored)
    await again.finish(wall_s=WALL_S)
    mine = [x for x in again.shots() if x[0] == MISSES]
    assert mine == [(MISSES, "L"), (MISSES, "R")] * 6, (
        f"2-2 shot nothing on the continue: {mine}")
    done = session_store.load(sid)
    assert done.status == "complete" and done.owed() == 0, (
        done.status, done.owed())


async def test_the_hand_off_empties_the_queue_and_never_raises(bus_lines):
    """`_finalize_report` is the one path that must always complete, so what
    it hands the session never ends it: a session that cannot mark its record
    (here, one whose ``note_set_aside_cleared`` raises) is a warning, the queue
    is emptied either way (it is the run's, and the run is over), and nothing
    reaches the caller. With nothing queued, or no session to hand it to, the
    call does nothing.

    MUTANT "the hand-off lets an error out" (``except Exception as e:`` in
    `_hand_pending_retries_to_the_session` made ``except KeyError as e:``):
    RED (observed):

        RuntimeError: the store is read-only
    """
    class _BrokenSession:
        def note_set_aside_cleared(self, ids, *, night):
            raise RuntimeError("the store is read-only")

    eng = SequenceEngine(_Hub())
    eng.plan = _the_plan()
    eng._session = _BrokenSession()
    eng._pending_retries = ["p11"]
    eng._hand_pending_retries_to_the_session()
    assert eng._pending_retries == [], eng._pending_retries
    said = [m for lvl, m, _s in bus_lines if "could not be recorded" in m]
    assert len(said) == 1 and "read-only" in said[0], said

    eng._pending_retries = ["p11"]
    eng._session = None
    eng._hand_pending_retries_to_the_session()
    assert eng._pending_retries == [], "no session: the queue was kept"
    eng._hand_pending_retries_to_the_session()           # nothing queued


# -------------------------------------------------- #737 the stand-down, once

async def _boundaries(eng, who, n=FRAMES) -> list:
    """``n`` frame boundaries of ``who``, each past the probe gap, with the
    frame loop's own detail written before each (it overwrites the
    stand-down's, which is why the stand-down writes it again). Returns the
    detail the state held after each."""
    details = []
    for _ in range(n):
        _a_frame_later(eng)
        eng._set_state(detail=f"{who.name}: Ha 180s [1/10]")
        await eng._maybe_recover_guiding(who)
        details.append(eng.state.get("detail"))
    return details


async def test_the_stand_down_is_said_once_for_a_target_in_a_run(
        sim_hub, monkeypatch, bus_lines):
    """Both attempts spent under ``guiding_action = warn``, the guider
    inactive, a clear sky, ten frame boundaries: the warning is said once, on
    arriving at the spent bound, and the state's detail still reads
    "guiding lost; recovery stood down" after every boundary, since the frame
    loop's own detail overwrites it between two.

    MUTANT "said at every boundary" (the guard on the stand-down's
    ``bus.log`` made ``if True:``): RED (observed):

        AssertionError: the stand-down was said 10 times in 10 frame
        boundaries
        assert 10 == 1
    (and the three cases below RED too, each on its own line.)
    MUTANT "the detail said once as well" (the ``_set_state`` made part of
    the guarded block): RED (observed):

        AssertionError: the detail did not stay: ['guiding lost; recovery
        stood down', 'NGC 7331: Ha 180s [1/10]', 'NGC 7331: Ha 180s [1/10]',
        ...]
    """
    eng, rig = _rig(sim_hub, monkeypatch, [CLEAR])
    t = eng.plan.targets[0]
    eng._guiding_recoveries = engine_mod._MAX_GUIDING_RECOVERIES
    details = await _boundaries(eng, t)
    said = _warnings(bus_lines, STAND_DOWN)
    assert len(said) >= 1, f"premise: the engine stood down: {rig.calls}"
    assert "start" not in rig.calls, f"premise: nothing was recovered: {rig.calls}"
    assert len(said) == 1, (
        f"the stand-down was said {len(said)} times in {FRAMES} frame "
        f"boundaries")
    assert details == ["guiding lost; recovery stood down"] * FRAMES, (
        f"the detail did not stay: {details}")


async def test_the_stand_down_is_said_again_for_the_next_target(
        sim_hub, monkeypatch, bus_lines):
    """The stand-down is for one target. The attempts carry to the next one
    when its own guider start did not work, and its arrival at the spent bound
    says it as the first did: two targets, two lines.

    MUTANT "the stand-down is for the night" (the stand-down's test made
    ``getattr(self, "_stand_down_said_at", None) is None or
    self._stand_down_said_at[0] != self._started_at``, so the mark is the
    run's and the target is not in it): RED (observed):

        AssertionError: the next target's stand-down was not said once, as
        the first's was: 1 line(s) for two targets
    """
    eng, _r = _rig(sim_hub, monkeypatch, [CLEAR])
    first = eng.plan.targets[0]
    second = _target(name="NGC 6946")
    eng._guiding_recoveries = engine_mod._MAX_GUIDING_RECOVERIES
    for who in (first, second):
        await _boundaries(eng, who, 4)
    said = _warnings(bus_lines, STAND_DOWN)
    assert len(said) == 2, (
        f"the next target's stand-down was not said once, as the first's was: "
        f"{len(said)} line(s) for two targets")


async def test_a_new_spell_of_attempts_says_the_stand_down_again(
        sim_hub, monkeypatch, bus_lines):
    """Guiding comes back and a banked, guided frame clears the attempts; the
    next loss is a new spell, and when its attempts are spent in turn the
    stand-down is said once more. The mark of the first spell is forgotten
    when the attempts are not spent.

    MUTANT "the mark is never forgotten" (``self._stand_down_said_at = None``
    on a non-spent boundary replaced by ``pass``): RED (observed):

        AssertionError: the second spell's stand-down was not said: 1
        line(s) over two spells
    """
    eng, _r = _rig(sim_hub, monkeypatch, [CLEAR])
    t = eng.plan.targets[0]
    eng._guiding_recoveries = engine_mod._MAX_GUIDING_RECOVERIES
    await _boundaries(eng, t, 3)
    assert len(_warnings(bus_lines, STAND_DOWN)) == 1, (
        "premise: the first spell said it once")
    eng._guiding_recoveries = 0               # a banked, guided frame
    _a_frame_later(eng)
    await eng._maybe_recover_guiding(t)       # a loss: attempt 1 of 2
    assert eng._guiding_recoveries == 1, "premise: the new spell recovers"
    eng._guiding_recoveries = engine_mod._MAX_GUIDING_RECOVERIES
    await _boundaries(eng, t, 3)
    said = _warnings(bus_lines, STAND_DOWN)
    assert len(said) == 2, (
        f"the second spell's stand-down was not said: {len(said)} line(s) "
        f"over two spells")


async def test_a_new_run_says_the_stand_down_again(sim_hub, monkeypatch,
                                                   bus_lines):
    """The mark is the run's: the attempts are not reset when a run starts, so
    a run on an engine that stood down in its last begins with the bound still
    spent, and an object's id is no proof of a different run. The key carries
    the run's start.

    MUTANT "the stand-down is for every run" (the stand-down's test made
    ``getattr(self, "_stand_down_said_at", None) is None or
    self._stand_down_said_at[1] != id(target)``, so the run is not in the
    mark): RED (observed):

        AssertionError: the new run's stand-down was not said: 1 line(s) over
        two runs
    """
    eng, _r = _rig(sim_hub, monkeypatch, [CLEAR])
    t = eng.plan.targets[0]
    eng._guiding_recoveries = engine_mod._MAX_GUIDING_RECOVERIES
    await _boundaries(eng, t, 3)
    assert len(_warnings(bus_lines, STAND_DOWN)) == 1, (
        "premise: the first run said it once")
    eng._started_at += 1000.0                 # a new run, the bound still spent
    await _boundaries(eng, t, 3)
    said = _warnings(bus_lines, STAND_DOWN)
    assert len(said) == 2, (
        f"the new run's stand-down was not said: {len(said)} line(s) over "
        f"two runs")


# --------------------------------------------- #738 one row per acquisition

async def test_a_setup_that_recovered_a_refused_track_on_an_uncentred_target_is_one_row(
        group_hub, monkeypatch, mount_at_its_limit):
    """Target setup, centring off: the slew lands, the mount refuses to track
    after it, and the park/unpark recovery re-centres the target. The solve
    that re-centre measured is the hop's, and setup records the hop's row
    from it once, at its common call. The recovery's own row, for a caller
    that takes no result, would be a second one for the same solve: the
    sky-angle rows are exactly one.

    MUTANT "the uncentred branch hands the recovery no centring"
    (`_setup_target`'s ``centring={}`` removed from the uncentred branch's
    `_recover_from_tracking_refusal` call): RED (observed):

        AssertionError: a recovered, uncentred setup is two rows for one
        solve: [('A', 92.7), ('A', 92.7)]
    """
    st = mount_at_its_limit(group_hub, refuse_goto=True)
    a = single("A", count=1)
    a.center = False
    night = Night(group_hub, monkeypatch, t0=T0, sky=_sky({"A": 92.7}))
    rows = await _run(night, _a_plan(a))
    assert "park" in st["events"] and "refused" in st["events"], (
        f"premise: the mount refused to track and the recovery parked: "
        f"{st['events']}")
    assert night.said("recovered in"), (
        f"premise: the recovery ran to its end: {night.said('A: ')}")
    assert len(night.gotos) == 1, (
        f"premise: the recovery's re-centre is the only goto: {night.gotos}")
    got = [(r["target"], r["pa_deg"]) for r in rows]
    assert got == [("A", 92.7)], (
        f"a recovered, uncentred setup is two rows for one solve: {got}")


# ------------------------------------- #764 hold darks before the first frame

HOLD_STEP = ExposureStep(exposure_s=180.0, gain=125, offset=30, binning=1,
                         count=30, filter="Ha")


class _Camera:
    """A cooled camera whose sensor reads ``temp`` (an exception is raised by
    the read, for a sensor nobody can read)."""
    connected = True
    can_cool = True

    def __init__(self, temp) -> None:
        self.temp = temp
        self.reads = 0

    async def get_temperature(self):
        self.reads += 1
        if isinstance(self.temp, BaseException):
            raise self.temp
        return self.temp


class _Hub:
    def __init__(self, camera=None) -> None:
        self.master_library = None
        self.devices = {} if camera is None else {"camera": camera}
        self.guider = None
        self.site = {}


def _hold_engine(camera, *, cool_to=-5.0, exposures=0):
    eng = SequenceEngine(_Hub(camera))
    eng.plan = SequencePlan(name="n", cloud_hold_darks=3, cool_to=cool_to)
    eng._hold_step = HOLD_STEP
    eng._hold_darks_want = None
    eng._hold_darks_taken = 0
    eng._exposures_taken = exposures
    eng._calls = []

    async def _fake_run_calibration(ti, target):
        eng._calls.append(target)

    eng._run_calibration = _fake_run_calibration
    eng._index_of_target = lambda t: 0
    eng._set_state = lambda **kw: None
    return eng


async def test_a_hold_before_the_first_frame_shoots_no_dark_on_a_warm_camera(
        monkeypatch, bus_lines):
    """The cloud hold is entered before the run's first frame (the sky is
    shut at dusk) with the step seeded from the plan, and the camera reads
    +18 C against a -5 C setpoint: a dark taken now is a warm one, cover for
    nothing. Ten passes of the hold loop shoot none, and the hold says why
    once, naming the reading and the setpoint.

    MUTANT "the guard removed" (``if warm is not None:`` in `_hold_darks`'
    entry made ``if False:``): RED (observed):

        AssertionError: a warm camera before the first frame shot 3 dark(s)
    """
    cam = _Camera(18.0)
    eng = _hold_engine(cam)
    fired = [await eng._hold_darks(None) for _ in range(10)]
    assert eng._calls == [], (
        f"a warm camera before the first frame shot {len(eng._calls)} "
        f"dark(s)")
    assert fired == [False] * 10
    said = [m for lvl, m, _s in bus_lines if "cloud hold" in m
            and "first frame" in m]
    assert len(said) == 1, said
    assert "18.0" in said[0] and "-5" in said[0], said[0]


async def test_a_hold_before_the_first_frame_shoots_its_dark_on_a_cooled_camera(
        monkeypatch):
    """The camera is at its setpoint, so the dark is a valid one whether or
    not a frame has been shot yet: the guard is about the camera, not the
    clock.

    MUTANT "the guard ignores the sensor" (`_camera_off_its_cooling_target`'s
    band test removed, ``if not math.isfinite(t):``, so every reading is a
    warm one): RED (observed):

        AssertionError: a cooled camera was refused its hold dark
    """
    eng = _hold_engine(_Camera(-5.4))
    assert await eng._hold_darks(None) is True, (
        "a cooled camera was refused its hold dark")
    assert len(eng._calls) == 1


async def test_a_sensor_nobody_can_read_is_not_a_warm_camera(monkeypatch):
    """Nobody-can-say is not a verdict: a sensor whose read raises, a camera
    that cannot cool, and a rig with no camera at all (the dark's own call
    fails, as it always did) are all let through, as before the guard. The
    tri-state is the point: only a MEASURED warm reading refuses.

    MUTANT "unreadable counts as warm" (the read's ``except`` answering
    ``99.0`` in place of ``None``): RED (observed):

        AssertionError: a sensor nobody could read was refused: a read that
        raised
    """
    class _NoCooler(_Camera):
        can_cool = False

    for what, cam in (("a read that raised", _Camera(RuntimeError("usb"))),
                      ("a camera that cannot cool", _NoCooler(40.0)),
                      ("no camera", None)):
        eng = _hold_engine(cam)
        assert await eng._hold_darks(None) is True, (
            f"a sensor nobody could read was refused: {what}")


async def test_the_guard_is_for_the_first_frame_and_for_a_plan_that_cools(
        monkeypatch):
    """After the run's first exposure nothing changes: a hold in the middle
    of the night shoots its darks as it always did, whatever the sensor reads
    (the cooler gate on the hold's release is what asks about the sensor
    then). So does a plan with no cooling intent, which has no setpoint to
    be away from.

    MUTANT "the guard never lifts" (``if self._exposures_taken <= 0 else
    None`` made ``if True else None``): RED (observed):

        AssertionError: a hold after the first frame was refused its dark on
        a warm camera
    """
    mid_night = _hold_engine(_Camera(18.0), exposures=1)
    assert await mid_night._hold_darks(None) is True, (
        "a hold after the first frame was refused its dark on a warm camera")
    uncooled = _hold_engine(_Camera(18.0), cool_to=None)
    assert await uncooled._hold_darks(None) is True, (
        "a plan with no setpoint was refused its dark on a warm camera")


async def test_each_hold_decides_for_itself(monkeypatch):
    """The refusal is the hold's, not the run's: the next hold (which resets
    ``_hold_darks_want`` as it opens) asks the sensor again, and a camera
    that has cooled since is let shoot.
    """
    cam = _Camera(18.0)
    eng = _hold_engine(cam)
    assert await eng._hold_darks(None) is False
    cam.temp = -5.0
    eng._hold_darks_want = None               # `_hold_for_clear` opens a hold
    assert await eng._hold_darks(None) is True
    assert len(eng._calls) == 1
