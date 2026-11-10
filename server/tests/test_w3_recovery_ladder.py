# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 3.1: the recovery ladder for a mount that is lost or halted during a
run (#1109 with #1098, #857 and #880).

What is graded:

- ``RecoveryLadder`` (pure): trusted, suspect, lost and paused_by_operator,
  the one home leg per target per night, and a pause whose only exit is an
  operator resume.
- #1098: an off-field centring after a slew between targets is retried once
  in place, then goes HOME and then to the target, with no solve at home.
  Every drive here is the REAL ``Hub`` on the simulator, the REAL
  ``goto_and_center`` and the REAL ``SequenceEngine._setup_target``; only
  the solver (the field sits 2.5 degrees off until the mount has been home)
  is a stand-in.
- #857: homing does not reset the AM5 model, so a mount that will not take a
  sync gets no home leg, and a miss that survives the home leg says home did
  not repair it and what does.
- #880 and ruling D-A: a STOP and the manual-move deadman during a goto
  pause the run (``paused_by_operator``); nothing moves the mount until the
  operator resumes, and the resume continues the acquisition. The resume
  ladder (ResumeArm) never starts a run behind an operator's STOP.

Coordinates are fictional. Named mutants (each applied to a byte copy of
the production file and restored from it, sha256 checked):

- "skip the home step": `_home_for_recovery` returns without sending
  ``find_home`` -> test_an_off_field_goto_goes_home_then_to_the_target.
- "ResumeArm restarts after a STOP": the ``aborted`` arm of the re-centre
  in ``ResumeArm._recover`` removed -> test_a_stop_during_the_resume_ladders_
  recentre_starts_no_run.
- "no halt branch": see test_a_halt_during_the_goto_pauses_the_run_until_
  the_operator_resumes. "uncentred slew ignores a halt": the ``if
  self._motion_halted_since(epoch)`` arm at the uncentred slew made ``if
  False`` -> test_a_halted_slew_of_an_uncentred_target_pauses_too. "#806
  recorded as untouched": see the last test.
"""
from __future__ import annotations

import asyncio
import re
import time

import pytest

import astrodeck.flows.tonight as tonight_mod
import astrodeck.hub as hub_module
from _group_harness import (Night, group_hub, group_store,  # noqa: F401
                            single, wall_budget_s)
from _group_harness import T0 as GROUP_T0
from _humanizer_mirror import CLIP_AT, humanizer_rewrites
from _simhub import sim_hub  # noqa: F401 (fixture import)
from test_h4_sky_angle_after_untouched_slew import (  # noqa: F401
    _plan as _sky_plan, _run, _sky, mount_at_its_limit)
import astrodeck.api.app as app_module
import astrodeck.sequence.engine as engine_mod
import test_the_flip_stops_paying_for_itself as flp
from _group_harness import grid_plan
from test_1097_resume_from_park import T0, _arm, _Rig
from test_850_engine_sync_refused import _flip_with, flip_hub  # noqa: F401
from test_850_hub_sync_refused import DEC, RA, _failing_sync
from test_busy_lanes_routes import client  # noqa: F401 (fixture import)
from astrodeck.devices.base import DeviceError, GotoNotArrived
from astrodeck.hub import (HALT_DEADMAN, HALT_OPERATOR_STOP, Hub,
                           OPERATOR_HALT_CAUSES)
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target
from astrodeck.sequence import resume_arm as ra
from astrodeck.sequence.engine import (CENTRING_TOO_FAR, PositionUnknownStop,
                                       StopTarget)
from astrodeck.sequence.recovery import (HOME_LEGS_PER_TARGET, Action,
                                         LadderState, RecoveryLadder)
from astrodeck.sequence.session import Session, session_store
from astrodeck.solve.base import SolveResult

#: 2.5 degrees north of the target: far past the centring ceiling.
FAR_DEC = round(DEC + 2.5, 4)


# ======================================================== the pure ladder

def test_a_miss_earns_one_retry_then_one_home_leg_then_gives_up():
    ladder = RecoveryLadder()
    assert ladder.state is LadderState.TRUSTED
    assert ladder.missed("night:A") is Action.RETRY_IN_PLACE
    assert ladder.state is LadderState.SUSPECT
    assert ladder.missed("night:A") is Action.HOME_THEN_TARGET
    assert ladder.state is LadderState.LOST
    assert ladder.missed("night:A") is Action.GIVE_UP
    assert ladder.state is LadderState.LOST


def test_a_centred_field_is_trusted_again():
    ladder = RecoveryLadder()
    ladder.missed("k")
    assert ladder.centred() is Action.CONTINUE
    assert ladder.state is LadderState.TRUSTED


def test_the_home_leg_is_spent_per_target_for_the_night():
    """A target the mount cannot reach does not shuttle home and back: its
    second acquisition gets the retry and nothing after it."""
    ladder = RecoveryLadder()
    for _ in range(2):
        ladder.missed("night:A")
    assert HOME_LEGS_PER_TARGET == 1
    ladder.begin()
    assert ladder.missed("night:A") is Action.RETRY_IN_PLACE
    assert ladder.missed("night:A") is Action.GIVE_UP
    ladder.begin()
    assert ladder.missed("night:B") is Action.RETRY_IN_PLACE
    assert ladder.missed("night:B") is Action.HOME_THEN_TARGET


def test_a_sync_the_mount_will_not_take_gets_no_home_leg():
    """Home goes to the MODEL's home on the AM5 and does not repair the model
    (#857): the ladder gives up at once."""
    ladder = RecoveryLadder()
    assert ladder.sync_not_taken() is Action.GIVE_UP
    assert ladder.state is LadderState.LOST


def test_a_pause_has_one_exit_the_operators_resume():
    ladder = RecoveryLadder()
    assert ladder.halted() is Action.WAIT_FOR_OPERATOR
    assert ladder.paused
    ladder.begin()
    assert ladder.paused, "a new acquisition does not end a pause"
    assert ladder.missed("k") is Action.WAIT_FOR_OPERATOR
    assert ladder.centred() is Action.WAIT_FOR_OPERATOR
    assert ladder.sync_not_taken() is Action.WAIT_FOR_OPERATOR
    assert ladder.paused
    assert ladder.resumed() is True
    assert ladder.state is LadderState.TRUSTED
    assert ladder.resumed() is False, "nothing was paused"


# ================================================================ the rig

class _Skies:
    """The sky a plate solve sees. ``off`` is where the field sits until the
    mount has been home; after it the field is on the target. ``events`` is
    the order the rig was asked to do things in."""

    name = "Scripted"

    def __init__(self, events: list[str], *, on_target: bool = False):
        self.events = events
        self.centred = on_target

    async def solve(self, fits_path, *, ra_hint=None, dec_hint=None,
                    fov_deg_hint=None):
        self.events.append("solve")
        dec = DEC if self.centred else FAR_DEC
        return SolveResult(True, ra_hours=RA, dec_deg=dec,
                           pixel_scale_arcsec=1.55, message="scripted")


def _rig(sim_hub, monkeypatch, *, homing_fixes: bool = True,
         on_target: bool = False):
    """The real hub on the simulator with the solver and the recorders."""
    import astrodeck.providers as providers_module
    monkeypatch.setattr(Hub, "_check_solar",
                        lambda self, ra, dec, *, force=False: None)
    monkeypatch.setattr(tonight_mod, "target_own_window",
                        lambda *a, **kw: None)
    events: list[str] = []
    skies = _Skies(events, on_target=on_target)
    monkeypatch.setattr(providers_module, "pick_solver", lambda hub: skies)
    tel = sim_hub.devices["telescope"]

    real_goto = sim_hub.goto_and_center

    async def goto(*a, **kw):
        events.append("goto")
        return await real_goto(*a, **kw)
    monkeypatch.setattr(sim_hub, "goto_and_center", goto)

    real_home = tel.find_home

    async def find_home():
        events.append("home")
        await real_home()
        if homing_fixes:
            skies.centred = True
    monkeypatch.setattr(tel, "find_home", find_home)

    real_slew = tel.slew

    async def slew(ra_hours, dec_deg):
        events.append("slew")
        await real_slew(ra_hours, dec_deg)
    monkeypatch.setattr(tel, "slew", slew)
    return sim_hub, tel, skies, events


def _target(name="Fictional N", **kw) -> Target:
    base = dict(name=name, ra_hours=RA, dec_deg=DEC, center=True,
                autofocus_first=False,
                steps=[ExposureStep(filter="L", exposure_s=1.0, count=1)])
    base.update(kw)
    return Target(**base)


def _engine(hub, target) -> SequenceEngine:
    e = SequenceEngine(hub)
    e._cfg = None
    e.plan = SequencePlan(name="w31", guide=False, meridian_flip=False,
                          safety_check=False, targets=[target])
    return e


async def _until(predicate, what: str, timeout_s: float = 20.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not predicate():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        await asyncio.sleep(0.01)


def _lines(bus_lines, level="warning") -> list[str]:
    return [m for lvl, m, src in bus_lines
            if lvl == level and src == "sequence"]


def _the_motion(events: list[str]) -> list[str]:
    return [x for x in events if x in ("goto", "home")]


# ======================================= #1098: lost between two targets

async def test_an_off_field_goto_goes_home_then_to_the_target(
        sim_hub, monkeypatch, bus_lines):
    """The first goto lands 2.5 degrees off and so does the retry from
    where the mount stands. The ladder then slews HOME and then to the
    target, and the third goto is centred. There is no plate solve at home:
    the first thing asked after the home is the goto, and the solve comes
    after its slew.

    MUTANT "skip the home step" (`_home_for_recovery` returns True without
    sending ``find_home``): RED, the order is goto, goto, goto.
    """
    hub, tel, skies, events = _rig(sim_hub, monkeypatch)
    t = _target()
    e = _engine(hub, t)

    await e._setup_target(0, t)

    assert _the_motion(events) == ["goto", "goto", "home", "goto"], events
    home_at = events.index("home")
    next_goto = events.index("goto", home_at)
    assert "solve" not in events[home_at:next_goto], ("a solve at home",
                                                      events)
    assert e._tracked_target is t
    assert e._ladder.state is LadderState.TRUSTED
    said = [m for m in _lines(bus_lines) if "home" in m]
    assert len(said) == 1, said
    assert "does not correct" in said[0] and "model" in said[0], said


async def test_a_miss_that_survives_the_home_leg_stops_the_target_once(
        sim_hub, monkeypatch, bus_lines):
    """Home cannot repair a model that is wrong (#857): the target stops as
    it did, after exactly one home leg, and says what does repair it."""
    hub, tel, skies, events = _rig(sim_hub, monkeypatch, homing_fixes=False)
    t = _target()
    e = _engine(hub, t)

    with pytest.raises(StopTarget) as ei:
        await e._setup_target(0, t)

    assert str(ei.value).startswith("centring at acquisition: ")
    assert _the_motion(events) == ["goto", "goto", "home", "goto"], events
    advice = [m for m in _lines(bus_lines) if "Trust position" in m]
    assert len(advice) == 1, _lines(bus_lines)
    assert "home" in advice[0] and "model" in advice[0], advice


async def test_a_mount_with_no_home_gets_the_retry_and_nothing_more(
        sim_hub, monkeypatch, bus_lines):
    """No home position to slew to: the ladder says so and the target stops
    as it did, with no home sent."""
    hub, tel, skies, events = _rig(sim_hub, monkeypatch, homing_fixes=False)
    monkeypatch.setattr(tel, "can_find_home", False)
    t = _target()
    e = _engine(hub, t)

    with pytest.raises(StopTarget):
        await e._setup_target(0, t)

    assert _the_motion(events) == ["goto", "goto"], events
    assert any("no home position" in m for m in _lines(bus_lines)),         _lines(bus_lines)


async def test_a_home_leg_that_fails_stops_the_target_and_says_so(
        sim_hub, monkeypatch, bus_lines):
    """A device error from the home itself (not a halt): the ladder goes no
    further, names the type and never the text, and the target stops."""
    hub, tel, skies, events = _rig(sim_hub, monkeypatch, homing_fixes=False)

    async def find_home():
        events.append("home")
        raise DeviceError("the mount did not answer; secret detail 4711")
    monkeypatch.setattr(tel, "find_home", find_home)
    t = _target()
    e = _engine(hub, t)

    with pytest.raises(StopTarget):
        await e._setup_target(0, t)

    assert _the_motion(events) == ["goto", "goto", "home"], events
    said = [m for m in _lines(bus_lines) if "home leg failed" in m]
    assert len(said) == 1 and "DeviceError" in said[0], said
    assert "4711" not in said[0], said


async def test_the_home_leg_is_spent_for_the_night(
        sim_hub, monkeypatch, bus_lines):
    """A second acquisition of the same target the same night is retried
    in place and stops: the mount does not go home a second time."""
    hub, tel, skies, events = _rig(sim_hub, monkeypatch, homing_fixes=False)
    t = _target()
    e = _engine(hub, t)
    for _ in range(2):
        with pytest.raises(StopTarget):
            await e._setup_target(0, t)

    assert events.count("home") == 1, events
    assert events.count("goto") == 3 + 2, events


async def test_no_goto_and_no_home_while_the_position_is_unknown(
        sim_hub, monkeypatch, bus_lines):
    """The #850 class: a driver that stops vouching for its position ends
    the run without moving the mount. The ladder sends neither the retry nor
    the home, and gives no goto advice."""
    hub, tel, skies, events = _rig(sim_hub, monkeypatch)
    t = _target()
    e = _engine(hub, t)
    monkeypatch.setattr(e, "_position_unknown",
                        lambda: events.count("goto") >= 1)

    with pytest.raises(PositionUnknownStop):
        await e._setup_target(0, t)

    assert _the_motion(events) == ["goto"], events
    assert not any("slewing home" in m for m in _lines(bus_lines))


async def test_a_sync_the_mount_refuses_gets_no_home_leg_and_the_advice(
        sim_hub, monkeypatch, bus_lines):
    """#857: the model is off by more than a sync fixes and the mount says
    so. The target stops as it did; the line names the model error and the
    two things that repair it, and the ladder does not go home."""
    hub, tel, skies, events = _rig(sim_hub, monkeypatch)
    _failing_sync(tel, monkeypatch)
    t = _target()
    e = _engine(hub, t)

    with pytest.raises(StopTarget):
        await e._setup_target(0, t)

    assert "home" not in events, events
    advice = [m for m in _lines(bus_lines) if "Trust position" in m]
    assert len(advice) == 1, _lines(bus_lines)
    assert "model" in advice[0] and "home" in advice[0], advice


async def test_the_words_the_ladder_says_survive_the_ui(
        sim_hub, monkeypatch, bus_lines):
    """Each line the ladder logs fits what the UI keeps whole, is not
    rewritten by its humanizer and carries no figure."""
    hub, tel, skies, events = _rig(sim_hub, monkeypatch, homing_fixes=False)
    t = _target()
    e = _engine(hub, t)
    with pytest.raises(StopTarget):
        await e._setup_target(0, t)

    ladder_lines = [m for m in _lines(bus_lines)
                    if "home" in m or "Trust position" in m]
    assert len(ladder_lines) == 2, _lines(bus_lines)
    for m in ladder_lines:
        assert len(m) <= CLIP_AT, (len(m), m)
        assert not humanizer_rewrites(m), m
        assert not re.search(r"\d", m.replace(t.name, "")), m


# ============================ #880 and D-A: a halt pauses, a resume goes on

def _stop_lands_before_the_first_motion(hub, tel, monkeypatch,
                                        cause=HALT_OPERATOR_STOP) -> None:
    """What ``POST /api/mount/stop`` does to a goto: bump the fence with the
    operator's cause, then halt the mount. ``cause`` is the cause a safety or
    dawn park would bump it with instead, for the tests that say those do
    not pause. Fired once, inside the real ``goto_and_center``, before its
    first slew, so the real hub answers its aborted shape."""
    real = hub.yield_camera_for
    fired: list[int] = []

    async def yielding(*a, **kw):
        await real(*a, **kw)
        if not fired:
            fired.append(1)
            if cause is None:
                hub.bump_motion_epoch()
            else:
                hub.bump_motion_epoch(cause)
            await tel.stop()
    monkeypatch.setattr(hub, "yield_camera_for", yielding)


def _the_deadman_fires_during_the_goto(hub, monkeypatch) -> None:
    """A jog whose keepalive went stale, and the REAL watchdog task halting
    it, while the goto is still before its first slew."""
    monkeypatch.setattr(hub_module, "MOVE_DEADMAN_MS", 20)
    real = hub.yield_camera_for
    fired: list[int] = []

    async def yielding(*a, **kw):
        await real(*a, **kw)
        if not fired:
            fired.append(1)
            hub.note_move("ra", 0.3)
            await asyncio.sleep(0.8)
    monkeypatch.setattr(hub, "yield_camera_for", yielding)


def _arm_session(e: SequenceEngine) -> Session:
    s = Session(name="w31", status="active", plan=e.plan, auto_resume=True)
    session_store.save(s)
    e._session = s
    return s


@pytest.mark.parametrize("cause", ["stop", "deadman"])
async def test_a_halt_during_the_goto_pauses_the_run_until_the_operator_resumes(
        cause, sim_hub, monkeypatch, bus_lines):
    """D-A. The run is paused_by_operator; for as long as it is, nothing
    moves the mount (no re-centre, no next goto, no slew) and nothing arms
    a restart; the operator's resume continues the acquisition, which then
    centres. The pause is published as a paused run with the ladder state
    beside it, so every client that knows a pause shows its Resume control.

    MUTANT "no halt branch" (the ``while`` of `_centre_until_not_halted`
    made ``while False``): RED, the run does not pause: the setup goes on at
    once (``assert not task.done()``).
    """
    hub, tel, skies, events = _rig(sim_hub, monkeypatch, on_target=True)
    if cause == "stop":
        _stop_lands_before_the_first_motion(hub, tel, monkeypatch)
    else:
        _the_deadman_fires_during_the_goto(hub, monkeypatch)
    t = _target()
    e = _engine(hub, t)
    s = _arm_session(e)
    task = asyncio.create_task(e._setup_target(0, t))
    try:
        await _until(lambda: e.paused or task.done(), "the pause")
        assert not task.done(), task.exception() if task.done() else None
        assert e.paused
        assert e.state["state"] == "paused"
        assert e.state["recovery"] == "paused_by_operator"
        assert e._ladder.state is LadderState.PAUSED_BY_OPERATOR

        moved = list(events)
        await asyncio.sleep(0.4)
        assert events == moved, ("the run moved the mount while paused",
                                 events[len(moved):])
        assert not task.done()
        assert session_store.load(s.id).auto_resume is False, \
            "a restart would resume a run the operator halted"

        e.resume()
        assert e.state["state"] == "running"
        assert e.state.get("recovery") is None
        await asyncio.wait_for(task, 30)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    assert events.count("goto") == 2, events
    assert "home" not in events
    assert e._tracked_target is t
    assert e._ladder.state is LadderState.TRUSTED
    assert session_store.load(s.id).auto_resume is True


async def test_a_halted_slew_of_an_uncentred_target_pauses_too(
        sim_hub, monkeypatch, bus_lines):
    """Centring off: the driver raises GotoNotArrived for a stop sent during
    the goto, and the motion fence has moved. That is the operator's halt,
    not a skipped target (#880, before this the run slewed on to the next
    target). After the resume the slew is sent again."""
    hub, tel, skies, events = _rig(sim_hub, monkeypatch)
    real_slew = tel.slew
    sent: list[int] = []

    async def slew(ra_hours, dec_deg):
        sent.append(1)
        if len(sent) == 1:
            hub.bump_motion_epoch(HALT_OPERATOR_STOP)
            raise GotoNotArrived("the goto did not arrive",
                                 reason="a stop was sent during the goto",
                                 residual_deg=3.0)
        await real_slew(ra_hours, dec_deg)
    monkeypatch.setattr(tel, "slew", slew)
    t = _target(center=False)
    e = _engine(hub, t)
    task = asyncio.create_task(e._setup_target(0, t))
    try:
        await _until(lambda: e.paused or task.done(), "the pause")
        assert not task.done(), task.exception() if task.done() else None
        assert e.state["recovery"] == "paused_by_operator"
        await asyncio.sleep(0.2)
        assert len(sent) == 1, sent
        e.resume()
        await asyncio.wait_for(task, 30)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    assert len(sent) == 2, sent


async def test_control_a_stalled_goto_with_no_halt_still_skips_the_target(
        sim_hub, monkeypatch, bus_lines):
    """CONTROL: the same exception with the fence unmoved is a goto that did
    not arrive (#860), skipped as before."""
    hub, tel, skies, events = _rig(sim_hub, monkeypatch)

    async def slew(ra_hours, dec_deg):
        raise GotoNotArrived("the goto did not arrive",
                             reason="the mount stopped short of the target",
                             residual_deg=3.0)
    monkeypatch.setattr(tel, "slew", slew)
    t = _target(center=False)
    e = _engine(hub, t)

    with pytest.raises(StopTarget):
        await e._setup_target(0, t)
    assert not e.paused


async def test_an_operators_abort_ends_a_paused_run(
        sim_hub, monkeypatch, bus_lines):
    """A paused_by_operator run is not stuck: cancelling it (Abort) ends the
    wait."""
    hub, tel, skies, events = _rig(sim_hub, monkeypatch, on_target=True)
    _stop_lands_before_the_first_motion(hub, tel, monkeypatch)
    t = _target()
    e = _engine(hub, t)
    task = asyncio.create_task(e._setup_target(0, t))
    await _until(lambda: e.paused or task.done(), "the pause")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ===================== ResumeArm never restarts behind an operator's STOP

async def test_a_stop_during_the_resume_ladders_recentre_starts_no_run(
        sim_hub, monkeypatch, bus_lines):
    """The resume ladder re-centres the mount before it starts the run. An
    operator STOP during that goto comes back from ``goto_and_center`` as its
    aborted shape, and the ladder used to carry on and start the run, which
    slewed the mount the operator had just stopped. Now it holds in fixed
    words, disarms the session as an Abort does, and no later tick starts
    anything.

    MUTANT "ResumeArm restarts after a STOP" (the ``aborted`` arm in
    ``_recover`` removed): RED, the run is started.
    """
    s = _arm()
    rig = _Rig(sim_hub, monkeypatch, "solved", "solved")
    stops: list[int] = []

    async def goto(ra, dec, *a, **kw):
        rig.events.append("goto")
        sim_hub.bump_motion_epoch(HALT_OPERATOR_STOP)
        stops.append(1)
        return {"centered": False, "error_arcmin": None, "attempts": 0,
                "aborted": True, "rotation": None}
    monkeypatch.setattr(sim_hub, "goto_and_center", goto)

    await rig.tick_at(T0)

    assert rig.starts == [], "the ladder started a run behind a STOP"
    assert rig.arm.hold["reason"] == ra.HALTED_BY_OPERATOR_WORDS
    assert rig.arm._recentred is None
    assert session_store.load(s.id).auto_resume is False

    await rig.tick_at(T0 + ra.RETRY_INTERVAL_S + 1)
    await rig.tick_at(T0 + 2 * ra.RETRY_INTERVAL_S + 2)
    assert rig.starts == []
    assert rig.events.count("goto") == 1, ("the mount was moved again",
                                           rig.events)
    reason = ra.HALTED_BY_OPERATOR_WORDS
    assert not re.search(r"\d", reason), reason
    assert not humanizer_rewrites(reason), reason
    assert len(reason) <= CLIP_AT


# ====================================== #806: the row after a recovered track

@pytest.mark.parametrize("rotation, rows", [(30.0, []), (None, [("A", 92.7)])],
                         ids=["angle commanded", "no angle"])
async def test_a_recovered_refused_track_records_a_row_by_what_it_commanded(
        rotation, rows, group_hub, monkeypatch, mount_at_its_limit):
    """An uncentred target whose track was refused and recovered: the
    recovery's re-centre commands the target's angle (the hub turns the
    rotator for it), so its solve is not the train's angle and must not be
    recorded as one. With no angle commanded the rotator was not asked
    anything, and the row stays (the control).

    MUTANT "recorded as untouched" (setup's ``commanded=`` and ``result=``
    back to ``rotation if target.center else None`` and ``hop_centring``):
    RED, "angle commanded" -
        AssertionError: [('A', 92.7)] != []
    """
    st = mount_at_its_limit(group_hub, refuse_goto=True)
    target = single("A", count=1, rotation_deg=rotation)
    target.center = False
    night = Night(group_hub, monkeypatch, t0=GROUP_T0, sky=_sky({"A": 92.7}))

    got = await _run(night, _sky_plan(target))

    assert st["events"] == ["refused", "refused", "park"], (
        f"premise: the refused track was recovered: {st['events']}")
    assert [c["rotation_deg"] for c in night.goto_calls] == [rotation], (
        f"premise: the recovery's goto is the only one: {night.goto_calls}")
    assert [(r["target"], r["pa_deg"]) for r in got] == rows, got


# ======================== the halt's cause: only the operator's pauses the run

def test_only_an_operator_stop_or_the_deadman_is_an_operator_halt():
    """The hub remembers WHY the fence moved. An operator STOP and the
    manual-move deadman are the operator's; a park, a home, a roof close, a
    safety halt, a dawn park and a caller that names no cause (what the dawn
    park and the sun watch call today) are not."""
    hub = Hub()
    assert OPERATOR_HALT_CAUSES == {HALT_OPERATOR_STOP, HALT_DEADMAN}
    for cause in ("safety", "dawn", "operator_park", "operator_home",
                  "dome_close"):
        before = hub._motion_epoch
        hub.bump_motion_epoch(cause)
        assert not hub.operator_halted_since(before), cause
    before = hub._motion_epoch
    hub.bump_motion_epoch()
    assert not hub.operator_halted_since(before), "no cause named"
    for cause in (HALT_OPERATOR_STOP, HALT_DEADMAN):
        before = hub._motion_epoch
        hub.bump_motion_epoch(cause)
        assert hub.operator_halted_since(before), cause
        assert not hub.operator_halted_since(hub._motion_epoch), (
            "a halt before the fence was read is not this goto's")


def test_the_real_stop_route_is_an_operator_halt_and_a_park_is_not(client):
    """POST /api/mount/stop through the real app: the fence moves with the
    operator's cause. The park route moves it too, and is not the operator's
    halt of a goto (the run's own handling of it is unchanged).

    MUTANT "stop names no cause" (``hub.bump_motion_epoch(HALT_OPERATOR_STOP)``
    in ``mount_stop`` made ``hub.bump_motion_epoch()``): RED -
        assert False
         +  where False = operator_halted_since(<epoch>)
    """
    hub = app_module.hub
    assert client.post("/api/connect/sim").status_code == 200
    before = hub._motion_epoch
    assert client.post("/api/mount/stop").status_code == 200
    assert hub._motion_epoch > before
    assert hub.operator_halted_since(before)

    before = hub._motion_epoch
    assert client.post("/api/mount/park").status_code == 200
    assert hub._motion_epoch > before
    assert not hub.operator_halted_since(before)


@pytest.mark.parametrize("cause", ["safety", "dawn", None],
                         ids=["safety park", "dawn park", "no cause named"])
async def test_a_safety_or_dawn_halt_during_the_goto_neither_pauses_nor_disarms(
        cause, sim_hub, monkeypatch, bus_lines):
    """The fence moves for a safety park or a dawn park too, and a run that
    paused for it as the operator's would be disarmed for it: a lost night.
    The run keeps the handling it had: no pause, the session stays armed,
    and the acquisition goes on to centre.

    MUTANT "the engine ignores the cause" (`_operator_halted`'s
    ``self._operator_halt_since(self._centring_fence)`` made
    ``self._motion_epoch() != self._centring_fence``): RED, all three -
        AssertionError: a halt that was not the operator's paused the run
    """
    hub, tel, skies, events = _rig(sim_hub, monkeypatch, on_target=True)
    _stop_lands_before_the_first_motion(hub, tel, monkeypatch, cause=cause)
    t = _target()
    e = _engine(hub, t)
    s = _arm_session(e)
    task = asyncio.create_task(e._setup_target(0, t))
    try:
        await _until(lambda: e.paused or task.done(), "the setup to go on")
        assert not e.paused, "a halt that was not the operator's paused the run"
        await asyncio.wait_for(task, 30)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    assert e.state.get("recovery") is None
    assert not any(L_HALT in m for _l, m, _s in bus_lines)
    assert session_store.load(s.id).auto_resume is True


async def test_a_safety_halt_of_an_uncentred_slew_still_skips_the_target(
        sim_hub, monkeypatch, bus_lines):
    """The same cause rule at the uncentred slew: a goto that did not arrive
    with a safety halt behind it is the skipped target it always was."""
    hub, tel, skies, events = _rig(sim_hub, monkeypatch)

    async def slew(ra_hours, dec_deg):
        hub.bump_motion_epoch("safety")
        raise GotoNotArrived("the goto did not arrive",
                             reason="a stop was sent during the goto",
                             residual_deg=3.0)
    monkeypatch.setattr(tel, "slew", slew)
    t = _target(center=False)
    e = _engine(hub, t)

    with pytest.raises(StopTarget):
        await e._setup_target(0, t)
    assert not e.paused


async def test_a_safety_halt_under_the_resume_ladders_recentre_keeps_its_handling(
        sim_hub, monkeypatch, bus_lines):
    """ResumeArm asks the cause too: a safety halt under its re-centre does
    not disarm the session and does not hold in the operator's words."""
    s = _arm()
    rig = _Rig(sim_hub, monkeypatch, "solved", "solved")

    async def goto(ra, dec, *a, **kw):
        rig.events.append("goto")
        sim_hub.bump_motion_epoch("safety")
        return {"centered": False, "error_arcmin": None, "attempts": 0,
                "aborted": True, "rotation": None}
    monkeypatch.setattr(sim_hub, "goto_and_center", goto)

    await rig.tick_at(T0)

    assert (rig.arm.hold or {}).get("reason") != ra.HALTED_BY_OPERATOR_WORDS
    assert session_store.load(s.id).auto_resume is True


# ================ D-A on the paths that were not covered: flip, recovery, test

L_HALT = engine_mod.L_HALTED_BY_OPERATOR


async def test_a_halt_under_the_flip_pauses_and_the_flip_is_asked_again(
        flip_hub, monkeypatch, bus_lines):
    """The meridian flip's re-centre is halted by the operator: the run
    pauses with nothing sent to the mount, and after the resume the flip is
    asked for again from the top (the latch stays armed until a flip is
    taken), and flips.

    MUTANT "the flip ignores a halt" (the ``if self._operator_halted(
    flip_result)`` arm in `_maybe_meridian_flip` made ``if False``): RED -
        AssertionError: the run did not pause under the flip's halted goto
    """
    e, t, st = flp._flip_engine(flip_hub, monkeypatch, the_slew_flips=True)

    async def goto(ra_h, dec_d, **kw):
        st["gotos"].append((ra_h, dec_d))
        if len(st["gotos"]) == 1:
            flip_hub.bump_motion_epoch(HALT_OPERATOR_STOP)
            return {"centered": False, "error_arcmin": None, "attempts": 0,
                    "aborted": True, "rotation": None}
        st["side"] = "east" if st["side"] == "west" else "west"
        return {"centered": True, "error_arcmin": 0.2}
    monkeypatch.setattr(flip_hub, "goto_and_center", goto)

    task = asyncio.create_task(e._maybe_meridian_flip(t, next_exposure_s=180.0))
    try:
        await _until(lambda: e.paused or task.done(), "the pause")
        assert e.paused, "the run did not pause under the flip's halted goto"
        assert e.state["recovery"] == "paused_by_operator"
        await asyncio.sleep(0.3)
        assert len(st["gotos"]) == 1, st["gotos"]
        assert e._flip_armed is True
        e.resume()
        await asyncio.wait_for(task, 30)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    assert len(st["gotos"]) == 2, st["gotos"]
    assert e._flip_armed is False, "the flip was taken after the resume"


async def test_a_halt_under_the_tracking_recoverys_goto_pauses_without_using_its_bound(
        sim_hub, monkeypatch, bus_lines):
    """A refused track is recovered by park, unpark and a re-centre. An
    operator's halt under that re-centre pauses the run, and the pause does
    not use up the recovery's outer time bound (patched to a few seconds,
    the pause lasts longer than it): after the resume the re-centre is made
    again and the recovery finishes.

    THE BOUND AND THE PAUSE ARE REAL SECONDS, so both move with the load
    (#1119): the work the bound has to cover (the re-centre after the
    resume, 0.16 s on an idle box) takes longer on a shared one, and a bound
    of half a second that left it three times the margin idle was crossed by
    the work alone, with no pause involved. The bound is two seconds scaled
    by ``wall_budget_s``, twelve times the work, and the pause is half as
    long again as the bound, so a bound the pause were charged to is crossed
    in the pause on any box, and one the work alone could cross is not.

    MUTANT "the pause is charged to the bound" (the ``bound.reschedule(None)``
    in `SequenceEngine._hold_for_operator` removed): RED (observed), the
    recovery's bound running out inside the pause and the setup ending with
    the original refusal:
        assert not task.done()
        <Task finished name='Task-25' coro=<SequenceEngine._setup_target() ...
        exception=RuntimeError("tracking on rejected (reply '0')")>.done
    """
    import astrodeck.sequence.schedule as schedule_mod
    hub, tel, skies, events = _rig(sim_hub, monkeypatch, on_target=True)
    bound_s = wall_budget_s(2.0)            # takes the reading before timing
    monkeypatch.setattr(schedule_mod, "dark_enough", lambda site, *a, **k: True)
    monkeypatch.setattr(engine_mod, "TRACKING_CONFIRM_S", 0.0)
    monkeypatch.setattr(engine_mod, "TRACKING_RECOVERY_TIMEOUT_S", bound_s)
    limit = {"on": True}
    set_on, get_on, park = tel.set_tracking, tel.get_tracking, tel.park

    async def set_tracking(on):
        if on and limit["on"]:
            raise RuntimeError("tracking on rejected (reply '0')")
        await set_on(on)

    async def get_tracking():
        return bool(await get_on()) and not limit["on"]

    async def park_it():
        limit["on"] = False
        await park()
    monkeypatch.setattr(tel, "set_tracking", set_tracking)
    monkeypatch.setattr(tel, "get_tracking", get_tracking)
    monkeypatch.setattr(tel, "park", park_it)
    real_goto = hub.goto_and_center
    calls: list[int] = []

    async def goto(*a, **kw):
        calls.append(1)
        if len(calls) == 2:
            hub.bump_motion_epoch(HALT_OPERATOR_STOP)
            return {"centered": False, "error_arcmin": None, "attempts": 0,
                    "aborted": True, "rotation": None}
        return await real_goto(*a, **kw)
    monkeypatch.setattr(hub, "goto_and_center", goto)
    t = _target()
    e = _engine(hub, t)

    task = asyncio.create_task(e._setup_target(0, t))
    try:
        await _until(lambda: e.paused or task.done(), "the pause",
                     timeout_s=wall_budget_s(20.0))
        assert e.paused, task.exception() if task.done() else None
        assert e.state["recovery"] == "paused_by_operator"
        await asyncio.sleep(1.5 * bound_s)
        assert len(calls) == 2, "the run moved the mount while paused"
        assert not task.done()
        e.resume()
        await asyncio.wait_for(task, wall_budget_s(30.0))
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    assert len(calls) == 3, calls
    assert e._tracked_target is t


async def test_a_halt_under_the_rotator_self_tests_goto_pauses_and_skips_the_test(
        sim_hub, monkeypatch, bus_lines):
    """The first rotating panel's self-test places the mount before it asks
    the rotator anything. An operator halt under that goto pauses the run
    and the test does NOT go on to run on a field that is not the panel's;
    it stays unrun for a later hop.

    MUTANT "the self-test goes on" (the ``if self._operator_halted(placed)``
    arm in `_ensure_rotator_self_test` made ``if False``): RED -
        AssertionError: the run did not pause under the self-test's halted
        goto
    """
    monkeypatch.setattr(Hub, "_check_solar",
                        lambda self, ra, dec, *, force=False: None)
    plan = grid_plan(rows=1, cols=1,
                     group_kw={"rotate": True, "pa_deg": 30.0})
    t = plan.targets[0]
    e = SequenceEngine(sim_hub)
    e._cfg = None
    e.plan = plan
    ran: list[int] = []

    async def self_test():
        ran.append(1)
    monkeypatch.setattr(sim_hub, "rotator_self_test", self_test,
                        raising=False)
    monkeypatch.setattr(sim_hub, "_rotation_trusted", None, raising=False)
    gotos: list[int] = []

    async def goto(*a, **kw):
        gotos.append(1)
        sim_hub.bump_motion_epoch(HALT_OPERATOR_STOP)
        return {"centered": False, "error_arcmin": None, "attempts": 0,
                "aborted": True, "rotation": None}
    monkeypatch.setattr(sim_hub, "goto_and_center", goto)

    task = asyncio.create_task(e._ensure_rotator_self_test(t))
    try:
        await _until(lambda: e.paused or task.done(), "the pause")
        assert e.paused, "the run did not pause under the self-test's halted goto"
        assert e.state["recovery"] == "paused_by_operator"
        e.resume()
        await asyncio.wait_for(task, 30)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    assert ran == [], "the self-test ran on a field the halt had left"
    assert len(gotos) == 1
    assert e._rotator_tested_night is None
