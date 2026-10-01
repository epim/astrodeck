"""The angle check on every hop of a mosaic (#189 U-04, task T15; spec 5.6
step 4, 6.12, Appendix A.2, D11).

A mosaic is laid out at one position angle, and a panel shot away from it
eats the corner overlap its neighbours need. So every hop of a member of a
group that carries ``pa_deg`` and ``angle_tolerance_deg`` compares the sky
angle its centring solve recorded (``hub.last_sky_angle``, exposed at or
after the hop started) with ``pa_deg``, mod 180, through
``angle_check.angle_verdict``, and does what ``group_rules.angle_decision``
says: shoot, shoot and log, warn, defer the panel, or set the whole group
aside.

Every case runs the REAL `_run_scheduled`, `_setup_target` and `_run_step`
on the clocked simulator (tests/_group_harness.py). The harness scripts the
sky angle each centring solve measured (``sky``), which is the one input the
check reads; the sim rotator is the real device object.

The fixture mosaic is a 2x2 of L and R, count 3, one pass a visit, laid out
at PA 30.0 with a 6.0 deg tolerance: the snake order is 1-1 1-2 2-2 2-1, and
a clean night is 12 hops. Its group is "M31".

Each case names the mutant it was shown RED under, with the failure
observed, verbatim (pytest's own lines, long ones wrapped). Every mutant was
applied in a private scratch copy of server/, never in the shared tree
(#254).
"""
from __future__ import annotations

import pytest

from _group_harness import (GROUP_NAME, LAT, LON, Night, grid_plan,
                            group_hub, group_store)
from astrodeck.sequence.session import session_store

PA, TOL = 30.0, 6.0
FIXED = {"pa_deg": PA, "angle_tolerance_deg": TOL}
ROTATING = {"pa_deg": PA, "angle_tolerance_deg": TOL, "rotate": True}
PANELS = ("1-1", "1-2", "2-2", "2-1")


def _name(label: str) -> str:
    return f"{GROUP_NAME} {label}"


async def _night(hub, monkeypatch, plan, **kw) -> Night:
    night = Night(hub, monkeypatch, **kw)
    try:
        night.done = await night.run(plan)
    finally:
        await night.close()
    night.stored = session_store.load(night.session_id)
    return night


def _shot(night, label: str) -> list[str]:
    return [f for t, f in night.shots() if t == _name(label)]


def _warnings(night, needle: str) -> list[str]:
    return [m for _t, lvl, m in night.lines
            if lvl == "warning" and needle in m]


# ------------------------------------------------------------ beyond tolerance

async def test_a_fixed_camera_off_its_angle_sets_the_whole_group_aside(
        group_hub, monkeypatch):
    """A fixed camera that reads PA 37.2 on a mosaic laid out at 30.0 cannot
    turn itself, so the first measurement sets the WHOLE group aside for
    tonight (spec 5.6 step 4): one hop, no frame, every panel recorded in
    ``Session.set_aside``, and one warning alert that names both numbers and
    the tolerance, and says what to do.

    MUTANT "fixed defers instead" (`group_rules.angle_decision` answers
    ``defer`` for a fixed camera's ``off``, as it does for a rotator's): the
    panels are deferred pass after pass and set aside one by one after three
    each. RED (observed):
        AssertionError: [(1788313689.0, 'M31 1-1'), (1788313689.0, 'M31
        1-2'), (1788313689.0, 'M31 2-2'), (1788313689.0, 'M31 2-1'),
        (1788313989.0, 'M31 1-1'), (1788313989.0, 'M31 1-2'), ...]
        assert 12 == 1
    """
    night = await _night(group_hub, monkeypatch, grid_plan(group_kw=FIXED),
                         sky=lambda who, n: 37.2)
    assert night.done, night.trace[-3:]
    assert len(night.gotos) == 1, night.gotos
    assert night.shots() == [], night.shots()
    alerts = _warnings(night, "set aside for tonight")
    assert len(alerts) == 1, night.lines
    alert = alerts[0]
    assert alert.startswith("M31: the camera reads PA 37.2 and this mosaic is "
                            "laid out at 30.0: 7.2 deg apart, beyond the 6.0 "
                            "deg tolerance"), alert
    assert "turn the camera or re-frame at the measured angle" in alert, alert
    stored = night.stored.set_aside
    assert sorted(r["target_id"] for r in stored) == ["p00", "p01", "p10",
                                                      "p11"], stored
    assert all("37.2" in r["reason"] and "30.0" in r["reason"]
               for r in stored), stored
    assert not night.said("deferred"), night.lines


async def test_a_half_turn_reading_is_the_same_footprint(group_hub,
                                                         monkeypatch):
    """PA 210.4 is PA 30.4 turned half a turn: a centred rectangle covers the
    same sky, so a fixed camera reading it on a mosaic laid out at 30.0 is
    within tolerance, and every panel is shot (spec 5.6 step 4, "mod 180").

    MUTANT "compare without mod 180" (`angle_check.angle_verdict` folds the
    difference mod 360, so 210.4 is 179.6 deg from 30.0): the first hop sets
    the group aside. RED (observed):
        AssertionError: ('1-1', [(1788313689.0, 'info', "sequence 'M31
        mosaic' started: 24 frames, 12 min integration"), (1788313689.0,
        'warni...t the measured angle; the mosaic is set aside for tonight:
        a restart tonight does not retry it, the next night does')])
        assert [] == ['L', 'R', 'L', 'R', 'L', 'R']
    """
    night = await _night(group_hub, monkeypatch, grid_plan(group_kw=FIXED),
                         sky=lambda who, n: 210.4)
    assert night.done, night.trace[-3:]
    for label in PANELS:
        assert _shot(night, label) == ["L", "R"] * 3, (label, night.lines[-4:])
    assert night.stored.set_aside == [], night.stored.set_aside
    assert night.stored.owed() == 0


async def test_a_rotating_panel_off_its_angle_is_deferred_to_the_next_pass(
        group_hub, monkeypatch):
    """A rotating mosaic: panel 1-2's first centring measures PA 40.0, 10 deg
    off. A rotator can put that right, so the panel is deferred, never shot
    off its angle, and retried on the next pass, where it measures 30.3 and
    is shot (spec 5.6 step 4). The others shoot on their first visits, and
    nothing is set aside.

    MUTANT "an off reading shoots" (`angle_decision` answers ``shoot`` for
    ``off``): 1-2 is shot on its first visit at PA 40.0. RED (observed):
        AssertionError: [('M31 1-1', ('L', 'R')), ('M31 1-2', ('L', 'R')),
        ('M31 2-2', ('L', 'R')), ('M31 2-1', ('L', 'R')), ('M31 1-1', ('L',
        'R'))]
        assert [('M31 1-1', ..., ('L', 'R'))] == [('M31 1-1', ..., ('L',
        'R'))]
          At index 1 diff: ('M31 1-2', ('L', 'R')) != ('M31 1-2', ())
    """
    def sky(who, n):
        return 40.0 if (who, n) == (_name("1-2"), 1) else 30.3

    night = await _night(group_hub, monkeypatch, grid_plan(group_kw=ROTATING),
                         sky=sky)
    assert night.done, night.trace[-3:]
    visits = night.visits()
    assert visits[:4] == [(_name("1-1"), ("L", "R")), (_name("1-2"), ()),
                          (_name("2-2"), ("L", "R")),
                          (_name("2-1"), ("L", "R"))], visits[:5]
    deferred = night.said("on 1-2")
    assert deferred and deferred[0].startswith(
        "M31: the rotator did not bring the camera to the mosaic's angle on "
        "1-2: the camera reads PA 40.0 and this mosaic is laid out at 30.0: "
        "10.0 deg apart, beyond the 6.0 deg tolerance"), deferred
    for label in PANELS:
        assert _shot(night, label) == ["L", "R"] * 3, label
    assert night.stored.set_aside == []


# ------------------------------------------------------ no fresh measurement

async def test_a_fixed_camera_measured_earlier_tonight_shoots_and_says_so(
        group_hub, monkeypatch):
    """Only the night's first centring reports an angle (30.4, within
    tolerance); every later hop's solve reports none, so the record the hub
    holds was exposed before that hop started. A fixed camera cannot turn
    between hops, and it was verified at 1-1, so each later hop shoots and
    logs that the angle was not re-measured, with the reading it stands on
    (spec 5.6 step 4).

    MUTANT "no_measurement read as off" (the verdict's ``no_measurement``
    passed to `angle_decision` as ``off``): the second hop sets the group
    aside. RED (observed):
        AssertionError: ('1-1', [(1788313689.0, 'info', 'target 1/4: M31
        1-1'), (1788313719.0, 'info', 'focus baseline HFR 2.00 px (relative
        w...t the measured angle; the mosaic is set aside for tonight: a
        restart tonight does not retry it, the next night does')])
        assert ['L', 'R'] == ['L', 'R', 'L', 'R', 'L', 'R']

    MUTANT "freshness from the run start" (`_setup_target` hands the check
    the run's start instead of the hop's): 1-1's record reads as every
    hop's own, and no hop says it was not re-measured. RED (observed):
        AssertionError: []
        assert 0 == 11
         +  where 0 = len([])
    """
    def sky(who, n):
        return 30.4 if (who, n) == (_name("1-1"), 1) else None

    night = await _night(group_hub, monkeypatch, grid_plan(group_kw=FIXED),
                         sky=sky)
    assert night.done, night.trace[-3:]
    for label in PANELS:
        assert _shot(night, label) == ["L", "R"] * 3, (label, night.lines[-4:])
    said = night.said("angle not re-measured on this hop")
    assert len(said) == 11, said
    assert said[0] == ("M31: 1-2: angle not re-measured on this hop; the "
                       "camera is fixed and was measured at the mosaic's "
                       "angle earlier tonight (it read PA 30.4 at 1-1)"), (
        said[0])
    assert night.stored.set_aside == []
    # A member never locks an angle of its own (ruling 9): the group's
    # layout angle is its angle.
    assert night.stored.locked_angles == {}, night.stored.locked_angles


async def test_a_fixed_camera_never_measured_tonight_defers_every_panel(
        group_hub, monkeypatch):
    """No centring reports an angle at all, and the camera is fixed. Tiles are
    never laid blind, so every hop defers its panel ("angle not measured"),
    and after three consecutive deferred passes each is set aside with a
    warning (spec 5.6 step 4, 6.4). No frame is shot.

    MUTANT "no_measurement read as ok" (the verdict's ``no_measurement``
    passed to `angle_decision` as ``ok``): every panel is shot. RED
    (observed):
        AssertionError: [('M31 1-1', 'L'), ('M31 1-1', 'R'), ('M31 1-2',
        'L'), ('M31 1-2', 'R'), ('M31 2-2', 'L'), ('M31 2-2', 'R'), ...]
        assert [('M31 1-1', ...2', 'R'), ...] == []
          Left contains 24 more items, first extra item: ('M31 1-1', 'L')

    MUTANT "set on any verdict" (``GroupRun.angle_verified`` set by any
    verdict, not only ``ok``, before the decision reads it): every hop, the
    first included, reads the camera as verified and shoots. RED (observed):
        AssertionError: [('M31 1-1', 'L'), ('M31 1-1', 'R'), ('M31 1-2',
        'L'), ('M31 1-2', 'R'), ('M31 2-2', 'L'), ('M31 2-2', 'R'), ...]
        assert [('M31 1-1', ...2', 'R'), ...] == []
          Left contains 24 more items, first extra item: ('M31 1-1', 'L')
    """
    night = await _night(group_hub, monkeypatch, grid_plan(group_kw=FIXED),
                         sky=lambda who, n: None)
    assert night.done, night.trace[-3:]
    assert night.shots() == [], night.shots()
    assert len(night.gotos) == 12, night.gotos
    first = night.said("on 1-1")[0]
    assert first.startswith("M31: angle not measured on 1-1: no sky angle has "
                            "been recorded"), first
    assert "tiles are never laid blind" in first, first
    alerts = _warnings(night, "set aside for tonight")
    assert len(alerts) == 4, alerts
    assert sorted(r["target_id"] for r in night.stored.set_aside) == [
        "p00", "p01", "p10", "p11"]


async def test_a_calibrated_rotator_at_its_angle_stands_in_for_the_solve(
        group_hub, monkeypatch):
    """A rotating mosaic whose centring solves report no angle, with the sim
    rotator calibrated (synced) and reading PA 30.0: the rotator's own read is
    the evidence, so each hop shoots with a warning that says so (spec 5.6
    step 4).

    MUTANT "no_measurement read as off" (as above): a rotator's ``off``
    defers, and nothing is shot. RED (observed):
        AssertionError: ('1-1', [(1788314289.0, 'info', 'target 4/4: M31
        2-2'), (1788314289.0, 'warning', 'M31: angle not measured on 2-2 on
        3... panel is retried on the next pass; set aside for tonight: a
        restart tonight does not retry it, the next night does')])
        assert [] == ['L', 'R', 'L', 'R', 'L', 'R']
    """
    rot = group_hub.devices["rotator"]
    await rot.sync(PA)
    night = await _night(group_hub, monkeypatch, grid_plan(group_kw=ROTATING),
                         sky=lambda who, n: None)
    assert night.done, night.trace[-3:]
    for label in PANELS:
        assert _shot(night, label) == ["L", "R"] * 3, (label, night.lines[-4:])
    warned = _warnings(night, "the calibrated rotator's own report")
    assert len(warned) == 12, warned
    assert warned[0].startswith(
        "M31: 1-1: angle not measured on this hop; shooting on the calibrated "
        "rotator's own report that it reached the mosaic's angle (it reads PA "
        "30.0)"), warned[0]
    assert night.stored.set_aside == []


@pytest.mark.parametrize("rotator", ["uncalibrated", "elsewhere", "moving"])
async def test_a_rotator_that_gives_no_evidence_defers(group_hub, monkeypatch,
                                                       rotator):
    """The same rotating mosaic with no angle from any solve, and a rotator
    that is not evidence: never calibrated (a fresh connection), calibrated
    and reading PA 50.0, or calibrated and reading PA 30.0 while it still
    reports itself moving, where the reading is a position it is passing
    through and not one it reached. Every hop defers its panel; nothing is
    shot (spec 5.6 step 4, "rotate mode otherwise").

    MUTANT "any connected rotator is evidence" (`_rotator_evidence` asks only
    that a rotator is connected): every panel is shot. RED on all three, the
    same line each time; on uncalibrated and elsewhere (observed):
        [uncalibrated]
        AssertionError: [('M31 1-1', 'L'), ('M31 1-1', 'R'), ('M31 1-2',
        'L'), ('M31 1-2', 'R'), ('M31 2-2', 'L'), ('M31 2-2', 'R'), ...]
        assert [('M31 1-1', ...2', 'R'), ...] == []
        [elsewhere]
        AssertionError: [('M31 1-1', 'L'), ('M31 1-1', 'R'), ('M31 1-2',
        'L'), ('M31 1-2', 'R'), ('M31 2-2', 'L'), ('M31 2-2', 'R'), ...]
        assert [('M31 1-1', ...2', 'R'), ...] == []

    MUTANT "evidence ignores motion" (`_rotator_evidence` never asks
    ``is_moving``): RED on moving (observed):
        AssertionError: [('M31 1-1', 'L'), ('M31 1-1', 'R'), ('M31 1-2',
        'L'), ('M31 1-2', 'R'), ('M31 2-2', 'L'), ('M31 2-2', 'R'), ...]
        assert [('M31 1-1', ...2', 'R'), ...] == []
    """
    rot = group_hub.devices["rotator"]
    if rotator == "elsewhere":
        await rot.sync(50.0)
    elif rotator == "moving":
        await rot.sync(PA)

        async def is_moving():
            return True

        monkeypatch.setattr(rot, "is_moving", is_moving)
    else:
        assert not rot.synced, "premise: a fresh sim rotator is uncalibrated"
    night = await _night(group_hub, monkeypatch, grid_plan(group_kw=ROTATING),
                         sky=lambda who, n: None)
    assert night.done, night.trace[-3:]
    assert night.shots() == [], night.shots()
    first = night.said("on 1-1")[0]
    assert first.startswith("M31: angle not measured on 1-1:"), first
    assert "the rotator gave no evidence" in first, first


@pytest.mark.parametrize("reading", [None, 37.2])
async def test_shoot_anyway_turns_every_refusal_into_a_warning(
        group_hub, monkeypatch, reading):
    """``require_centred`` off is the block's "Shoot anyway": a fixed camera
    that was never measured, or that reads 37.2, is shot on every panel with
    a warning each hop, and nothing is deferred or set aside (spec 5.6 step
    4).

    MUTANT "shoot anyway ignored" (`angle_decision` handed ``shoot_anyway =
    False``): the unmeasured camera defers, the one at 37.2 sets the group
    aside. RED on both (observed):
        [None]
        AssertionError: ('1-1', [(1788314289.0, 'info', 'target 4/4: M31
        2-2'), (1788314289.0, 'warning', 'M31: angle not measured on 2-2 on
        3...onight: tiles are never laid blind; set aside for tonight: a
        restart tonight does not retry it, the next night does')])
        assert [] == ['L', 'R', 'L', 'R', 'L', 'R']
        [37.2]
        AssertionError: ('1-1', [(1788313689.0, 'info', "sequence 'M31
        mosaic' started: 24 frames, 12 min integration"), (1788313689.0,
        'warni...t the measured angle; the mosaic is set aside for tonight:
        a restart tonight does not retry it, the next night does')])
        assert [] == ['L', 'R', 'L', 'R', 'L', 'R']
    """
    plan = grid_plan(group_kw={**FIXED, "require_centred": False})
    night = await _night(group_hub, monkeypatch, plan,
                         sky=lambda who, n: reading)
    assert night.done, night.trace[-3:]
    for label in PANELS:
        assert _shot(night, label) == ["L", "R"] * 3, (label, night.lines[-4:])
    warned = _warnings(night, "(shooting anyway, as this mosaic asks)")
    assert len(warned) == 12, warned
    assert night.stored.set_aside == []


@pytest.mark.parametrize("reading", [None, 37.2])
async def test_a_single_panel_only_warns(group_hub, monkeypatch, reading):
    """A 1x1 block with a planned angle has no neighbour to leave a hole
    beside, so its angle only ever warns (spec 5.6 step 4): the fixed camera
    never measured, or reading 37.2, still shoots every frame.

    MUTANT "single panel ignored" (`angle_decision` handed ``single_panel =
    False``): the unmeasured camera defers, the one at 37.2 sets the group
    aside. RED on both (observed):
        [None]
        AssertionError: [(1788313989.0, 'info', 'M31: pass 2 took no
        exposures and deferred 1 visits; waiting 300 s before the next
        pass'), (1...tonight: tiles are never laid blind; set aside for
        tonight: a restart tonight does not retry it, the next night does')]
        assert [] == ['L', 'R', 'L', 'R', 'L', 'R']
        [37.2]
        AssertionError: [(1788313689.0, 'info', "sequence 'M31 mosaic'
        started: 6 frames, 3 min integration"), (1788313689.0, 'warning',
        "sequ...at the measured angle; the mosaic is set aside for tonight:
        a restart tonight does not retry it, the next night does')]
        assert [] == ['L', 'R', 'L', 'R', 'L', 'R']
    """
    plan = grid_plan(1, 1, group_kw=FIXED)
    night = await _night(group_hub, monkeypatch, plan,
                         sky=lambda who, n: reading)
    assert night.done, night.trace[-3:]
    assert _shot(night, "1-1") == ["L", "R"] * 3, night.lines[-4:]
    warned = _warnings(night, "a single panel has no neighbours")
    assert len(warned) == 3, warned
    assert night.stored.set_aside == []


async def test_a_skipped_panel_is_still_a_tile_of_the_layout(group_hub,
                                                              monkeypatch):
    """A 1x2 block whose operator skipped panel 1-2 carries one member, 1-1,
    but it is not a 1x1 block: the skipped panel is a tile of the same
    layout (its id stays in ``skipped_ids``), shot another night or
    already, and 1-1 must sit beside it at the layout angle. So a fixed
    camera reading 37.2 sets the group aside, as it would with both panels
    carried, and never falls into the single-panel "only warns" case.

    MUTANT "skipped panels not counted" (`_group_angle_check` counts only the
    members the plan carries): the lone member reads as a single panel, and
    its six frames are shot at the wrong angle with a warning. RED
    (observed):
        AssertionError: [('M31 1-1', 'L'), ('M31 1-1', 'R'), ('M31 1-1',
        'L'), ('M31 1-1', 'R'), ('M31 1-1', 'L'), ('M31 1-1', 'R')]
        assert [('M31 1-1', ...31 1-1', 'R')] == []
          Left contains 6 more items, first extra item: ('M31 1-1', 'L')
    """
    plan = grid_plan(1, 2, group_kw={**FIXED, "skipped_ids": ["p01"]})
    plan = plan.model_copy(update={
        "targets": [t for t in plan.targets if t.id != "p01"]})
    assert [t.id for t in plan.targets] == ["p00"], "premise: one member"
    night = await _night(group_hub, monkeypatch, plan,
                         sky=lambda who, n: 37.2)
    assert night.done, night.trace[-3:]
    assert night.shots() == [], night.shots()
    assert len(night.gotos) == 1, night.gotos
    alerts = _warnings(night, "set aside for tonight")
    assert len(alerts) == 1 and "PA 37.2" in alerts[0], night.lines
    assert not night.said("a single panel has no neighbours"), night.lines
    assert [r["target_id"] for r in night.stored.set_aside] == ["p00"], (
        night.stored.set_aside)


# ------------------------------------------------------------------ controls

async def test_control_a_group_without_a_tolerance_checks_nothing(
        group_hub, monkeypatch):
    """A group with ``pa_deg`` but no ``angle_tolerance_deg`` has no angle
    check (spec 3.4: None disables it): a fixed camera reading 37.2, or
    nothing, shoots every panel and says nothing about the angle."""
    plan = grid_plan(group_kw={"pa_deg": PA})
    night = await _night(group_hub, monkeypatch, plan,
                         sky=lambda who, n: 37.2)
    assert night.done
    for label in PANELS:
        assert _shot(night, label) == ["L", "R"] * 3, label
    assert not night.said("PA 37.2"), night.lines


async def test_the_angle_lines_do_not_move_with_the_site(group_hub,
                                                         group_store,
                                                         monkeypatch):
    """The angle check's lines are words and the camera's own angles (spec
    6.9): the same night, a rotating mosaic whose panel 1-2 is deferred for
    its angle and whose rotator stands in for a missing solve, run at the
    fixture site and at one 10 degrees further south, logs the same lines,
    word for word. A position angle is a property of the camera on the sky,
    not of the site; an altitude or a time would move.

    MUTANT "an angle line carries the panel's altitude" (the deferral's
    ``last_error`` ends with ``_frame_altitude`` of the panel now): RED
    (observed):
        AssertionError: [("M31: the rotator did not bring the camera to the
        mosaic's angle on 1-2: the camera reads PA 40.0 and this mosaic
        is...at 30.0: 10.0 deg apart, beyond the 6.0 deg tolerance (altitude
        64.0); retried on the next pass (1 of 3 consecutive)")]
        assert ["sequence 'M...cutive)', ...] == ["sequence 'M...cutive)',
        ...]

    RE-PINNED FOR WP-31 (backlog ruling D-04, owner-approved 2026-09-30,
    #595). ``engine.start`` now logs a WARNING naming every session its
    singleton silently disarmed. Both nights here run the same
    ``group_hub``/session store, so the second ``one()`` finds the first
    night's session still auto-resume-armed and disarms it, logging
    "starting 'M31 mosaic' disarmed auto-resume for: M31 mosaic" for the
    SECOND run only -- a fact about two runs sharing one store in this
    harness, not a site-derived number, so it is dropped from both lists
    before the word-for-word comparison rather than weakening what this
    case is about.
    """
    from astrodeck.config import Site

    def sky(who, n):
        if (who, n) == (_name("1-2"), 1):
            return 40.0
        return None if who == _name("2-2") else 30.3

    async def one():
        # Each night starts from no record, as the first did: a record left
        # by the first night is stamped on the same fake clock.
        group_hub.last_sky_angle = None
        rot = group_hub.devices["rotator"]
        await rot.sync(PA)
        return await _night(group_hub, monkeypatch,
                            grid_plan(group_kw=ROTATING), sky=sky)

    here = await one()
    group_store.set_site(Site(name="Fixture", latitude=LAT - 10.0,
                              longitude=LON, is_default=False))
    there = await one()
    assert here.done and there.done
    # D-04 (#595): the second run's engine.start sees the first run's
    # session still auto-resume-armed in this shared store and disarms it,
    # logging a warning that names it -- an artefact of running two nights
    # back to back on one store, not a number either site set, so it is
    # dropped from both sides before the comparison.
    said_here = [m for _t, _l, m in here.lines
                if "disarmed auto-resume for" not in m]
    said_there = [m for _t, _l, m in there.lines
                 if "disarmed auto-resume for" not in m]
    assert any("PA 40.0" in m for m in said_here), said_here
    assert any("own report" in m for m in said_here), said_here
    assert said_here == said_there, [
        (a, b) for a, b in zip(said_here, said_there) if a != b][:3]
