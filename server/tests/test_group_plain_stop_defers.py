# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A plain StopTarget in a panel's visit defers the panel; it does not drop it
for the run (#316, S3 orchestrator ruling 5; spec 5.1 outcome table, 5.6 step
7, 6.4, 6.8).

THE DEFECT. A group member's visit runs the whole gate stack, and several of
those gates end a target with a plain ``StopTarget``: an autofocus that
failed under ``af_failure_action = skip``, a mount that will not track again
after its one recovery, a flip still owed at the end of its hold. For a
single target that drops it for the run, which is what the escalation asks.
`_visit_panel` let it through, and the scheduler dropped the PANEL for the
run (`_group_member_gone`): unrecorded, never retried, a hole in the mosaic
for the rest of the night over one failed sweep while its neighbours shot
on. The owner's ruling 5 ("no point in leaving a hole in the mosaic if we
don't have to") now covers every such stop: the panel is deferred to the
next pass with the stop's own sentence (`group_rules.TARGET_STOP`), counted
by `GroupRun.visit_outcome`, and set aside tonight, with the warning alert,
only after ``max_failed_visits`` (3) consecutive passes.

Two stops are not deferred. The frozen stop window (`WindowClosed`) is
shared by every panel, and the all-closed path ends them all; a panel below
its own floor (`FloorStop`) is set aside tonight at once. A single target's
stop still drops it.

Every run is the real `_run_scheduled`, `_setup_target`, `_run_steps` and
`_run_step` on the clocked simulator (tests/_group_harness.py). What each
case scripts is named in it: the autofocus result (the harness has no
focuser, and no sweep on a simulator can be made to fail on one panel), the
mount's tracking flag, and in one case the decision that a flip is owed.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim (pytest's own lines, long ones wrapped). Every mutant was
applied in a private scratch copy of server/ (scratchpad s3ea-mut), never in
the shared tree (#254).
"""
from __future__ import annotations

import time

import pytest

import astrodeck.sequence.engine as engine_mod
from _group_harness import (GROUP_NAME, T0, Night, grid_plan, group_hub,
                            group_store, single)
from astrodeck.config import EscalationConfig
from astrodeck.devices.base import DeviceError
from astrodeck.focus.autofocus import AutofocusResult
from astrodeck.sequence.models import SequencePlan
from astrodeck.sequence.session import session_store

FAILING = "2-2"


def _hhmm(t: float) -> str:
    """``"HH:MM"`` for ``schedule.resolve_window``'s ``stop_mode="time"``
    (used by the dawn-stop control below, re-pinned off ``max_run_min`` by
    backlog ruling for #164). Timezone-safe the way ``test_idle_park_hold``'s
    copy of this helper is: every zone's UTC offset is a whole number of
    minutes, so the local seconds-of-minute equal the epoch's."""
    return time.strftime("%H:%M", time.localtime(t))
OTHERS = ("1-1", "1-2", "2-1")
AF_ERROR = "autofocus failed: no stars"
TRACKING_ERROR = ("the mount is not tracking and will not resume — every "
                  "light frame from here would be a streak")


def _name(label: str) -> str:
    return f"{GROUP_NAME} {label}"


def _gotos(night, label: str) -> list[float]:
    return [night.rel(t) for t, who in night.gotos if who == _name(label)]


def _shot(night, label: str) -> list[str]:
    return [f for t, f in night.shots() if t == _name(label)]


async def _night(hub, monkeypatch, plan, *, wall_s: float = 60.0,
                 **kw) -> Night:
    night = Night(hub, monkeypatch, **kw)
    try:
        night.done = await night.run(plan, wall_s=wall_s)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


class _Focuser:
    """A focuser the engine can sweep with. The sweep itself is
    ``run_autofocus``, which each case scripts; this only answers the reads
    the engine and the hub make around it."""

    name = "scripted focuser"
    connected = True
    max_position = 60000

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def get_position(self) -> int:
        return 30000

    async def get_temperature(self):
        return None


def _scripted_autofocus(night, monkeypatch, fails_on: str) -> list[str]:
    """Give the rig a focuser and make every sweep on ``fails_on`` fail, the
    way a sweep with no stars in its frames does; every other sweep works.
    Returns the targets swept, in order. The engine's own `_autofocus` and
    its ``af_failure_action`` escalation run as they do on the rig."""
    swept: list[str] = []
    night.hub.devices["focuser"] = _Focuser()

    async def run_autofocus(cam, foc, **kw):
        who = str(night.engine.state.get("target", ""))
        swept.append(who)
        if who == fails_on:
            return AutofocusResult(False, 0, None, [], "no stars")
        return AutofocusResult(True, 30000, 2.0, [], "ok")

    monkeypatch.setattr(engine_mod, "run_autofocus", run_autofocus)
    return swept


# ---------------------------------------------------------- the four stops

def _af_at_the_hop(night, monkeypatch, plan, group_store):
    """The failing panel sweeps as its hop ends (``autofocus_first``), and
    the sweep fails: `_setup_target` -> `_autofocus` -> StopTarget."""
    group_store.set_escalation(EscalationConfig(af_failure_action="skip"))
    next(t for t in plan.targets
         if t.name == _name(FAILING)).autofocus_first = True
    swept = _scripted_autofocus(night, monkeypatch, _name(FAILING))

    def premise():
        assert swept == [_name(FAILING)] * 3, (
            f"premise: only 2-2 swept, at each of its hops: {swept}")
        assert len(night.said("initial autofocus failed: no stars")) == 3

    night.premise = premise
    return AF_ERROR


def _af_in_the_frame_loop(night, monkeypatch, plan, group_store):
    """The plan refocuses every frame (``autofocus_every`` 1), so the frame
    loop's `_refocus_due` sweeps at the failing panel's first frame boundary,
    in `_run_step` and not in the hop, and that sweep fails. Every other
    panel's sweeps work. It is not the visit's first thing: the hop and the
    gates before the refocus have run."""
    group_store.set_escalation(EscalationConfig(af_failure_action="skip"))
    plan.autofocus_every = 1
    swept = _scripted_autofocus(night, monkeypatch, _name(FAILING))

    def premise():
        assert swept.count(_name(FAILING)) == 3, swept
        assert {_name(label) for label in OTHERS} <= set(swept), (
            f"premise: the frame loop swept the other panels too: {swept}")
        assert len(night.said("refocus failed: no stars")) == 3
        assert not night.said("initial autofocus")

    night.premise = premise
    return AF_ERROR


def _tracking_the_recovery_could_not_clear(night, monkeypatch, plan,
                                           group_store):
    """Once the failing panel's goto has landed, the mount says it is not
    tracking and refuses to track again, until the next hop begins. The
    frame loop's `_enforce_tracking` asks it to resume, then runs the
    park/unpark recovery (`_recover_from_tracking_refusal`), whose own
    ``set_tracking(True)`` is refused too, and raises StopTarget; on later
    passes the recovery's one-attempt latch refuses at once."""
    tel = night.hub.devices["telescope"]
    inner_set, inner_get = tel.set_tracking, tel.get_tracking
    landed: dict[str, int] = {}

    def stuck() -> bool:
        who = str(night.engine.state.get("target", ""))
        return (who == _name(FAILING)
                and landed.get(who) == len(night.gotos))

    async def set_tracking(on):
        if on and stuck():
            raise DeviceError("tracking on rejected")
        await inner_set(on)

    async def get_tracking():
        if stuck():
            return False
        return await inner_get()

    def goto(who, n, result):
        if who == _name(FAILING):
            landed[who] = len(night.gotos)
        return result

    monkeypatch.setattr(tel, "set_tracking", set_tracking)
    monkeypatch.setattr(tel, "get_tracking", get_tracking)
    night.goto_script = goto

    def premise():
        name = _name(FAILING)
        assert len(night.said(f"{name}: the park/unpark recovery failed")) == 1
        assert len(night.said(f"{name}: the mount refused tracking again "
                              f"after a park/unpark recovery already ran")) == 2

    night.premise = premise
    return TRACKING_ERROR


def _a_flip_owed_past_its_hold(night, monkeypatch, plan, group_store):
    """A flip owed past its hold. The simulator cannot be made to refuse a
    flip, so the DECISION that one is owed is scripted: the frame loop's
    `_enforce_flip_owed`, for the failing panel only, finds the mount still
    on the side it reports now and hands it to the engine's own
    `_hold_for_owed_flip` with a two-minute hold. The hold, its re-armed
    flip gate, its side reads and the StopTarget it ends with are the
    engine's."""
    eng = night.engine
    real_hold = eng._hold_for_owed_flip
    sides: list = []

    async def enforce_flip_owed(target):
        if target.name != _name(FAILING):
            return
        side = await eng._pier_side_now()
        sides.append(side)
        await real_hold(target, side, 2.0)

    monkeypatch.setattr(eng, "_enforce_flip_owed", enforce_flip_owed)
    night.flip_sides = sides

    def premise():
        held = night.said(f"{_name(FAILING)}: a meridian flip is required and the "
                          f"mount is still on the")
        assert len(held) == 3, held

    night.premise = premise
    return None     # the sentence names the side the mount reported


STOPS = {
    "autofocus at the hop": _af_at_the_hop,
    "autofocus in the frame loop": _af_in_the_frame_loop,
    "tracking the recovery could not clear": (
        _tracking_the_recovery_could_not_clear),
    "a flip owed past its hold": _a_flip_owed_past_its_hold,
}


@pytest.mark.parametrize("stop", list(STOPS))
async def test_a_plain_stop_defers_the_panel_and_three_set_it_aside(
        group_hub, group_store, monkeypatch, stop):
    """A 2x2 of L and R, count 3, one pass a visit. On 2-2, and on no other
    panel, one of four gates ends the visit with a plain StopTarget before
    its first frame, on every visit. Each time the panel is DEFERRED, with
    the stop's own sentence as the last error: "the visit stopped on 2-2:
    <sentence>; retried on the next pass (1 of 3 consecutive)", then (2 of
    3). It is hopped to once a pass, never twice in one, and at its third
    consecutive failure it is set aside for tonight with ONE warning alert
    that names it, the cause and the sentence, recorded in
    ``Session.set_aside`` so a restart tonight does not retry it. The other
    three panels complete, and the run ends owing only 2-2's frames.

    MUTANT "let plain StopTarget through" (`_visit_panel`'s ``except
    StopTarget`` arm deleted, so the stop reaches the scheduler, which drops
    the panel for the run through `_group_member_gone`): RED on all four
    cases alike, one hop and never a retry (observed, each):
        AssertionError: 2-2 was hopped to [120.0]: a deferral is retried
        once a pass until its third failure
        assert 1 == 3
         +  where 1 = len([120.0])
    """
    plan = grid_plan()
    night = Night(group_hub, monkeypatch)
    expected = STOPS[stop](night, monkeypatch, plan, group_store)
    try:
        night.done = await night.run(plan, wall_s=60.0)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    assert night.done, night.lines[-4:]
    if expected is None:
        sides = getattr(night, "flip_sides", [])
        assert sides and all(s in ("east", "west") for s in sides), (
            f"premise: the mount reported a side at each hold: {sides}")
        expected = (f"{_name(FAILING)}: a meridian flip has been pending for 2 "
                    f"min and the mount is still on the {sides[0]} side; "
                    f"moving on rather than exposing across the pier")

    hops = _gotos(night, FAILING)
    assert len(hops) == 3, (
        f"{FAILING} was hopped to {hops}: a deferral is retried once a pass "
        f"until its third failure")
    # By position in the list of hops, not by time: a stop at the hop costs
    # no time on the fake clock, so the next panel's hop can share its
    # instant.
    order = [who for _t, who in night.gotos]
    at = [i for i, who in enumerate(order) if who == _name(FAILING)]
    between = [order[a + 1:b] for a, b in zip(at, at[1:])]
    assert all(between), f"{FAILING} was retried within one pass: {order}"
    assert _shot(night, FAILING) == [], night.shots()
    for label in OTHERS:
        assert _shot(night, label) == ["L", "R"] * 3, (label, night.shots())

    for n in (1, 2):
        retry = (f"M31: the visit stopped on {FAILING}: {expected}; retried "
                 f"on the next pass ({n} of 3 consecutive)")
        assert night.said(retry), (retry, night.lines[-6:])
    reason = (f"the visit stopped on {FAILING} on 3 consecutive visits: "
              f"{expected}")
    alerts = [(lvl, m) for _t, lvl, m in night.lines
              if "set aside for tonight" in m]
    assert alerts == [("warning", f"M31: {reason}; set aside for tonight: a "
                                  f"restart tonight does not retry it, the "
                                  f"next night does")], alerts
    assert [(r["target_id"], r["reason"]) for r in night.stored.set_aside] == [
        ("p11", reason)], night.stored.set_aside
    assert not night.said(f"{_name(FAILING)}: skipped —"), (
        "the scheduler dropped the panel for the run")
    last = night.states[-1]
    assert (last["state"], last.get("end_reason")) == (
        "complete", "incomplete"), last
    # Each of the three visits met the stop this case is about.
    night.premise()


# ---------------------------------------------------------------- controls

async def test_control_the_dawn_stop_still_ends_every_panel_at_once(
        group_hub, monkeypatch):
    """Every panel's frozen window closes at the SAME clock time, in the
    middle of 1-2's visit: 60 s frames, so 1-1 shoots 0 to 120 s and 1-2's L
    120 to 180 s, and the frame boundary at 180 s finds the window closed.
    That stop (`WindowClosed`) is NOT deferred: it goes on up, the scheduler
    skips 1-2 with the window's own words, and the all-closed path ends
    every other panel at that same boundary, as a dawn cutoff. Nothing is
    promised a retry, and nothing is set aside for the night: the window is
    tonight's, not the panel's.

    RE-PINNED OFF ``max_run_min`` FOR #164 (orchestrator ruling,
    2026-10-02, owner-approved plan 2026-09-30, backlog ruling D-nn): a
    ``max_run_min`` window is no longer shared by every panel -- each
    panel's own cap now counts from ITS OWN first attempt, not from a
    common anchor -- so it can no longer stand in for a genuinely SHARED
    "every panel closes at once" boundary, which is what this control is
    about. A ``stop_mode="time"`` boundary is untouched by that ruling (it
    was never anchored to any per-target attempt) and is what a real dawn
    stop actually is, so it replaces ``max_run_min`` here one-for-one: same
    shot pattern, same words, same all-closed sweep over panels 2-2 and 2-1
    that were never even visited.

    MUTANT "the window's close deferred like any stop" (`_visit_panel`'s
    ``except WindowClosed: raise`` arm deleted, so ``except StopTarget``
    defers it): RED (observed):
        AssertionError: a closed window promised a retry: ['M31: the visit
        stopped on 1-2: observing window closed (stop time / max run /
        dawn); retried on the next pass (1 of 3 consecutive)']
        assert not ['M31: the visit stopped on 1-2: observing window closed
        (stop time / max run / dawn); retried on the next pass (1 of 3
        consecutive)']
    """
    stop_time = _hhmm(T0 + 180.0)
    plan = grid_plan(panel_kw={"exposure_s": 60.0,
                               "schedule_kw": {"stop_mode": "time",
                                               "stop_time": stop_time}})
    night = await _night(group_hub, monkeypatch, plan)
    assert night.done, night.lines[-4:]
    assert night.shots() == [(_name("1-1"), "L"), (_name("1-1"), "R"),
                             (_name("1-2"), "L")], night.shots()
    promised = night.said("retried on the next pass")
    assert not promised, f"a closed window promised a retry: {promised}"
    assert night.said(f"{_name('1-2')}: skipped — observing window closed "
                      f"(stop time / max run / dawn)"), night.lines[-6:]
    for label in ("1-1", "2-2", "2-1"):
        assert night.said(f"{_name(label)}: window closed — skipping"), label
    last = night.states[-1]
    assert last.get("end_reason") == "dawn_cutoff", last
    assert night.stored.set_aside == [], night.stored.set_aside


async def test_control_a_single_targets_autofocus_failure_still_ends_it(
        group_hub, group_store, monkeypatch):
    """No group: "Solo" sweeps as its hop ends and the sweep fails, under
    ``af_failure_action = skip``. The escalation is a single target's, as it
    always was: Solo is skipped for the run with the stop's words after one
    hop and no frame, "Next" is shot, and the run ends owing Solo's frames.
    The deferral belongs to a panel's visit (`_visit_panel`) and nowhere
    else.

    MUTANT "the stop becomes a deferral where it is raised" (`_autofocus`
    raising ``PanelDeferred(..., kind="target_stop")`` in place of its
    ``StopTarget``, the shortcut that would reach every target): RED
    (observed):
        AssertionError: ('error', 'error', 'the visit stopped: autofocus
        failed: no stars')
        assert ('error', 'error') == ('complete', 'incomplete')
          At index 0 diff: 'error' != 'complete'
    """
    group_store.set_escalation(EscalationConfig(af_failure_action="skip"))
    solo = single("Solo")
    solo.autofocus_first = True
    plan = SequencePlan(name="solo", targets=[solo, single("Next", ha_h=-2.4)],
                        guide=False, dither_every=0, autofocus_every=0,
                        meridian_flip=False, park_when_done=False,
                        warm_cooler_when_done=False, recover_guiding=False)
    night = Night(group_hub, monkeypatch)
    _scripted_autofocus(night, monkeypatch, "Solo")
    try:
        night.done = await night.run(plan, wall_s=60.0)
    finally:
        await night.close()
    assert night.done, night.lines[-4:]
    last = night.states[-1]
    assert (last["state"], last.get("end_reason")) == (
        "complete", "incomplete"), (last["state"], last.get("end_reason"),
                                    last.get("detail"))
    assert [who for _t, who in night.gotos] == ["Solo", "Next"], night.gotos
    assert night.shots() == [("Next", "L")], night.shots()
    assert night.said(f"Solo: skipped — {AF_ERROR}"), night.lines[-6:]
    assert not night.said("retried on the next pass")
