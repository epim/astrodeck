"""After every slew that left the rotator untouched, the sky angle its solve
measured is recorded in the session report (#526 part 3, H4 orchestrator
ruling 3; #145 and S8, which wait on this data).

WHY. On 2026-09-28 the camera's sky angle moved 2.7 degrees over a plain
re-slew with nothing commanded (the no-op flip re-slew, the
rotator read by the rotate shortcut and not moved), and several degrees
with pointing and pier side at a fixed mechanical angle: slip in the train
or play in the rotator. Nothing kept those numbers but the night log's
lines. S8's convergence correction needs the sign of the sky angle on both
pier sides (#145), and S7 item 2 is meant to measure the play, so every
slew whose angle came from the train and not from a rotator move is now a
row in the report.

WHAT IS RECORDED (`SessionReporter.record_sky_angle`, the report's
``sky_angles``): ``exposed_at``, ``target``, ``pier_side``,
``mechanical_deg`` (the rotator's mechanical angle, or null), ``pa_deg``
and ``source``, taken from ``hub.last_sky_angle`` when that record is fresh
for the slew (exposed at or after it began, `angle_check.fresh_sky_angle`).
NO ALT/AZ, and nothing else from the hub's record: a key allow-list below
holds the row to those six (a mount's altitude is a latitude oracle, #140).
A flip re-slew's ``exposed_at`` is still the time of a computed meridian
event (the #166 class); withholding it from a viewer's report is the
route's, filed as #567.

WHICH SLEWS: a target setup (every hop is one) and the flip gate's re-slew,
a flip that changed nothing included. "Untouched": the slew commanded no
angle, or it commanded one and the rotate shortcut answered it without
moving the rotator (U-06), or there was no rotator to command. A slew whose
rotate loop ran, or tried and failed, moved the rotator, and its angle is
the loop's, not the train's.

THE NIGHTS are the clocked simulator (tests/_group_harness.py). The
harness's ``sky`` script stands for the centring solve each goto ends with,
exposed at the goto on the fake clock.

Each case names the mutant it was shown RED under, with the failure
observed, verbatim (long lines wrapped). Every mutant was applied in a
private scratch copy of server/ (scratchpad H4-ENG-C-mut, from a byte
backup), never in the shared tree (#254):

* "record skipped": `_setup_target`'s call to `_record_sky_angle` made a
  no-op.
* "the flip records nothing": `_maybe_meridian_flip`'s call made a no-op.
* "only a flip that flipped records": that call handed the record only
  when the hub's flip result says ``flipped``.
* "the whole record": the row updated with the hub's whole record after
  `record_sky_angle` wrote it.
* "attach drops the rows": `attach_existing` not restoring ``sky_angles``.
* "recorded whatever the rotator did": `_rotator_untouched` answering True.
* "the shortcut counts as a move": `_rotator_untouched` answering False for
  a commanded angle whatever the rotate result said.
* "stale record recorded": `_record_sky_angle` taking the record whether or
  not it is fresh for the slew.

Added by the H4-ENG-C verifier, each run in its private copy
H4-ENG-C-verify-mut from a byte backup:

* "no rotator counts as touched": `_rotator_untouched` answering False for
  ``rotation_unavailable``.
* "the flip's record taken from any time": the flip gate's call given
  ``since=0.0`` in place of ``since=_t0``, so a record from before the
  re-slew passes as its own.
* "pier side stored raw": `record_sky_angle` storing the pier side as
  handed, not "east", "west" or None.
"""
from __future__ import annotations

from _group_harness import (LON, T0, Night, group_hub, group_store,  # noqa: F401
                            single)
from astrodeck.sequence import SequencePlan, schedule
from astrodeck.sequence.report import SessionReporter

#: The only keys a row may carry.
ROW_KEYS = {"exposed_at", "target", "pier_side", "mechanical_deg", "pa_deg",
            "source"}
MECH = 137.53


def _plan(*targets, flip: bool = False, **kw) -> SequencePlan:
    return SequencePlan(name="sky angles", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=flip,
                        park_when_done=False, warm_cooler_when_done=False,
                        recover_guiding=False, targets=list(targets), **kw)


async def _run(night: Night, plan: SequencePlan) -> list[dict]:
    """Run ``plan`` and answer the report's rows as the disk has them."""
    try:
        night.done = await night.run(plan, wall_s=120.0)
    finally:
        await night.close()
    assert night.done, f"premise: the run ended: {night.trace[-3:]}"
    rid = night.engine.reporter.id
    report = SessionReporter.load(rid)
    assert report is not None, f"premise: the report {rid} is on disk"
    return [dict(r) for r in report.sky_angles]


def _sky(pa: dict[str, float], side: str = "west"):
    """Each goto's centring solve: ``pa[target]``, on ``side``, at the
    fixed mechanical angle, with the fields the hub's record carries that
    a row must NOT (the camera, the calibration)."""
    def sky(who: str, n: int):
        return {"pa_deg": pa[who], "pier_side": side, "mechanical_deg": MECH,
                "camera": "Sim Camera", "calibrated": True,
                "offset_deg": 44.7}
    return sky


async def test_two_unrotated_targets_give_two_rows(group_hub, monkeypatch):
    """A then B, neither with an angle, each centred once. Each setup's
    solve is recorded, one row per target, in the order they were shot,
    with exactly the six keys and the solve's own values: the PA, the
    rotator's mechanical angle, the pier side, the source and the moment
    the frame was exposed (the goto). A crash-resume that re-attaches the
    report keeps them.

    MUTANT "record skipped": RED (observed):
        AssertionError: the report's sky angles are [] after two unrotated
        setups
    MUTANT "the whole record": RED (observed), the hub's record's other
    keys reaching the report:
        AssertionError: a row carries keys beyond the allow-list:
        ['calibrated', 'camera', 'offset_deg', 'reason',
        'rotator_before_deg', 'solved_at']
    MUTANT "attach drops the rows": RED (observed):
        AssertionError: a re-attached report lost its sky angles: []
    (The tree before the fix failed every case in this file (observed),
    the report having no ``sky_angles`` at all:
        AttributeError: 'SessionReport' object has no attribute
        'sky_angles'
    .)
    """
    plan = _plan(single("A", count=2), single("B", count=2, ha_h=-2.0))
    night = Night(group_hub, monkeypatch, t0=T0,
                  sky=_sky({"A": 92.7, "B": 278.5}))
    rows = await _run(night, plan)
    gotos = {who: t for t, who in night.gotos}
    assert sorted(gotos) == ["A", "B"] and len(night.gotos) == 2, (
        f"premise: one centred setup each: {night.gotos}")
    assert [r.get("target") for r in rows] == ["A", "B"], (
        f"the report's sky angles are {rows} after two unrotated setups")
    extra = sorted({k for r in rows for k in r} - ROW_KEYS)
    assert extra == [], f"a row carries keys beyond the allow-list: {extra}"
    assert rows == [
        {"exposed_at": gotos["A"], "target": "A", "pier_side": "west",
         "mechanical_deg": MECH, "pa_deg": 92.7,
         "source": "plate solve + sync"},
        {"exposed_at": gotos["B"], "target": "B", "pier_side": "west",
         "mechanical_deg": MECH, "pa_deg": 278.5,
         "source": "plate solve + sync"}], rows
    again = SessionReporter.attach_existing(night.engine.reporter.id)
    kept = [dict(r) for r in again.build().sky_angles] if again else None
    assert kept == rows, f"a re-attached report lost its sky angles: {kept}"


async def test_control_a_slew_that_turned_the_rotator_adds_no_row(
        group_hub, monkeypatch):
    """CONTROL. Three targets with a planned angle, each centred once. C's
    rotate loop ran and moved the rotator (the goto's ``rotation`` is the
    loop's result): its angle is the loop's, and it adds no row. D's rotate
    loop tried and failed (``rotation_skipped``): the rotator may have
    moved, and it adds no row. E's angle was answered by the rotate
    shortcut, which read the rotator at the angle and did not move it, the
    2026-09-28 no-op re-slew case: its angle is the train's, and it adds one.

    MUTANT "recorded whatever the rotator did": RED (observed):
        AssertionError: rows for slews whose rotate loop moved the rotator,
        or tried to: ['C', 'D', 'E']
    MUTANT "the shortcut counts as a move": RED (observed):
        AssertionError: the rotate shortcut left the rotator where it was
        and E's angle went unrecorded: []
    (and the flip case below RED too, its two re-slews commanding the
    lock.)
    """
    loop = {"rotated": True, "pa_deg": 30.0, "attempts": 2,
            "error_deg": 0.3}
    shortcut = {"rotated": True, "pa_deg": 30.4, "adjusted_to": None,
                "attempts": 0, "error_deg": 0.4, "shortcut": True,
                "reason": "no rotate solve"}

    def goto(who: str, n: int, result: dict) -> dict:
        if who == "C":
            return {**result, "rotation": dict(loop)}
        if who == "D":
            return {**result, "rotation": None, "rotation_skipped": True}
        return {**result, "rotation": dict(shortcut)}

    plan = _plan(single("C", count=1, rotation_deg=30.0),
                 single("D", count=1, rotation_deg=30.0, ha_h=-2.2),
                 single("E", count=1, rotation_deg=30.0, ha_h=-2.0))
    night = Night(group_hub, monkeypatch, t0=T0, goto=goto,
                  sky=_sky({"C": 30.1, "D": 34.0, "E": 30.4}))
    rows = await _run(night, plan)
    commanded = [c["rotation_deg"] for c in night.goto_calls]
    assert commanded == [30.0, 30.0, 30.0], (
        f"premise: every setup commanded the angle: {night.goto_calls}")
    moved = [r["target"] for r in rows if r["target"] in ("C", "D")]
    assert moved == [], (
        f"rows for slews whose rotate loop moved the rotator, or tried to: "
        f"{[r['target'] for r in rows]}")
    assert [r["target"] for r in rows] == ["E"], (
        f"the rotate shortcut left the rotator where it was and E's angle "
        f"went unrecorded: {rows}")


async def test_control_a_slew_whose_solve_measured_nothing_adds_no_row(
        group_hub, monkeypatch):
    """CONTROL. A then B, neither with an angle, and B's centring solve
    measures no angle (the solve failed, or reported no rotation): the
    hub's record is still A's, exposed before B's slew began, so it is not
    fresh for B's slew and B adds no row. A stale record measured another
    slew, and recorded under B it would put A's angle at B's pointing.

    MUTANT "stale record recorded" (`_record_sky_angle` taking ``rec``
    whether or not it is fresh): RED (observed):
        AssertionError: B's solve measured nothing and B still has a row:
        [('A', 92.7), ('B', 92.7)]
    """
    def sky(who: str, n: int):
        return None if who == "B" else {"pa_deg": 92.7, "pier_side": "west",
                                        "mechanical_deg": MECH}

    plan = _plan(single("A", count=2), single("B", count=2, ha_h=-2.0))
    night = Night(group_hub, monkeypatch, t0=T0, sky=sky)
    rows = await _run(night, plan)
    assert [who for _t, who in night.gotos] == ["A", "B"], (
        f"premise: one centred setup each: {night.gotos}")
    got = [(r["target"], r["pa_deg"]) for r in rows]
    assert got[:1] == [("A", 92.7)], f"premise: A's setup has its row: {got}"
    assert got == [("A", 92.7)], (
        f"B's solve measured nothing and B still has a row: {got}")


async def test_a_commanded_angle_with_no_rotator_to_turn_adds_a_row(
        group_hub, monkeypatch):
    """F has a planned angle and the rig has no rotator to turn: the hub
    centres without rotating and says ``rotation_unavailable``. Nothing
    moved a rotator, so F's angle is the train's and it adds a row, with
    no mechanical angle to give.

    MUTANT "no rotator counts as touched": RED (observed):
        AssertionError: F commanded an angle with no rotator to turn, and
        its angle went unrecorded: []
    """
    def goto(who: str, n: int, result: dict) -> dict:
        return {**result, "rotation": None, "rotation_unavailable": True}

    def sky(who: str, n: int):
        return {"pa_deg": 31.2, "pier_side": "west", "mechanical_deg": None}

    plan = _plan(single("F", count=1, rotation_deg=30.0))
    night = Night(group_hub, monkeypatch, t0=T0, goto=goto, sky=sky)
    rows = await _run(night, plan)
    assert [c["rotation_deg"] for c in night.goto_calls] == [30.0], (
        f"premise: the setup commanded the angle: {night.goto_calls}")
    got = [(r["target"], r["pa_deg"], r["mechanical_deg"]) for r in rows]
    assert got == [("F", 31.2, None)], (
        f"F commanded an angle with no rotator to turn, and its angle went "
        f"unrecorded: {got}")


async def test_a_side_nobody_could_name_is_stored_as_none(group_hub,
                                                         monkeypatch):
    """A's solve reads its pier side as "East" and B's as "unknown": the
    row keeps "east", and None for B, the answer for "nobody could say",
    so a reader grouping the rows by side (#145) meets two values and
    None, never a spelling.

    MUTANT "pier side stored raw": RED (observed):
        AssertionError: the rows' pier sides are ['East', 'unknown']
    """
    def sky(who: str, n: int):
        return {"pa_deg": 92.7, "mechanical_deg": MECH,
                "pier_side": "East" if who == "A" else "unknown"}

    plan = _plan(single("A", count=1), single("B", count=1, ha_h=-2.0))
    night = Night(group_hub, monkeypatch, t0=T0, sky=sky)
    rows = await _run(night, plan)
    assert [r["target"] for r in rows] == ["A", "B"], (
        f"premise: one row per setup: {rows}")
    sides = [r["pier_side"] for r in rows]
    assert sides == ["east", None], f"the rows' pier sides are {sides}"


def _past_s(plan: SequencePlan, t: float) -> float:
    return -schedule.hours_to_meridian_flip(plan.targets[0].ra_hours, LON,
                                            t) * 3600.0


async def test_every_flip_re_slew_is_a_row_the_no_op_included(group_hub,
                                                               monkeypatch):
    """A crosses the meridian 15 min in, flips on at the plan's 10 min lead.
    Three slews: the setup; the lead-time flip attempt, which on the
    simulator (it picks its side from the hour angle) changes nothing; and
    the retry past the meridian, which flips. A has no planned angle, so it
    locks the angle of its first shot (ruling 9) and each flip re-centre
    commands that lock; the rotator sits calibrated and unmoved at it, so
    the rotate shortcut answers each without moving it. That is the
    2026-09-28 night exactly: the no-op re-slew read the angle 2.7
    degrees off with the shortcut saying the rotator had not moved. Each
    slew solved a sky angle with the rotator untouched, so there are three
    rows, the pier side of each as its solve read it.

    MUTANT "the flip records nothing": RED (observed):
        AssertionError: A's rows are [('west', 0.0)]; its slews were the
        setup, the no-op re-slew and the flip at [-900.0, -600.0, 15.0] s
    MUTANT "only a flip that flipped records": RED (observed), the no-op
    re-slew, the one the rig's slip was seen on, gone:
        AssertionError: A's rows are [('west', 0.0), ('east', 912.5)]; its
        slews were the setup, the no-op re-slew and the flip at [-900.0,
        -600.0, 15.0] s
    (The rows' times are seconds after the setup's goto, the slews' seconds
    of hour angle from A's meridian.)
    """
    tel = group_hub.devices["telescope"]

    def sky(who: str, n: int):
        # The side the goto just latched, as the hub's exposure context reads
        # it, and a PA per slew.
        return {"pa_deg": (92.7, 90.2, 278.5)[min(n, 3) - 1],
                "pier_side": tel._latched_side.value,
                "mechanical_deg": MECH}

    def goto(who: str, n: int, result: dict) -> dict:
        # A flip re-centre commanding the lock, answered by the shortcut.
        if n == 1:
            return result
        return {**result, "rotation": {"rotated": True, "pa_deg": 92.8,
                                       "attempts": 0, "shortcut": True,
                                       "reason": "no rotate solve"}}

    plan = _plan(single("A", count=40, ha_h=-0.25), flip=True)
    night = Night(group_hub, monkeypatch, t0=T0, sky=sky, goto=goto)
    rows = await _run(night, plan)
    assert [c["rotation_deg"] for c in night.goto_calls] == [None, 92.7,
                                                             92.7], (
        f"premise: the setup commanded no angle and each flip re-centre "
        f"commanded the lock: {night.goto_calls}")
    slews = [round(_past_s(plan, t), 1) for t, who in night.gotos if who == "A"]
    assert len(slews) == 3 and night.said(
        "A: the meridian flip re-slewed but the mount still reports pier "
        "side west") and night.said(
        "A: meridian flip complete (pier side west -> east)"), (
        f"premise: the setup, a no-op lead-time re-slew and a flip: {slews} "
        f"s, {night.said('A: ')}")
    got = [(r["pier_side"], round(r["exposed_at"] - night.gotos[0][0], 1))
           for r in rows]
    gone = [round(t - night.gotos[0][0], 1) for t, _w in night.gotos]
    assert got == [("west", gone[0]), ("west", gone[1]), ("east", gone[2])], (
        f"A's rows are {got}; its slews were the setup, the no-op re-slew "
        f"and the flip at {slews} s")
    assert [r["pa_deg"] for r in rows] == [92.7, 90.2, 278.5], rows


async def test_control_a_flip_re_slew_whose_solve_measured_nothing_adds_no_row(
        group_hub, monkeypatch):
    """CONTROL. The flip night above, with the no-op re-slew's centring
    solve measuring nothing: the hub's record is still the setup's, exposed
    before the re-slew began, so it is not the re-slew's and adds no row.
    Recorded, it would put the setup's angle at the no-op re-slew, the one
    slew the rig's slip was seen on.

    MUTANT "the flip's record taken from any time": RED (observed):
        AssertionError: A's rows are [('west', 0.0), ('west', 0.0),
        ('east', 912.5)]; the no-op re-slew solved nothing
    The mutant "stale record recorded" turns it RED too, with the same line
    (observed).
    """
    tel = group_hub.devices["telescope"]

    def sky(who: str, n: int):
        if n == 2:
            return None
        return {"pa_deg": 92.7 if n == 1 else 278.5,
                "pier_side": tel._latched_side.value,
                "mechanical_deg": MECH}

    def goto(who: str, n: int, result: dict) -> dict:
        if n == 1:
            return result
        return {**result, "rotation": {"rotated": True, "pa_deg": 92.8,
                                       "attempts": 0, "shortcut": True,
                                       "reason": "no rotate solve"}}

    plan = _plan(single("A", count=40, ha_h=-0.25), flip=True)
    night = Night(group_hub, monkeypatch, t0=T0, sky=sky, goto=goto)
    rows = await _run(night, plan)
    assert len(night.gotos) == 3 and night.said(
        "A: the meridian flip re-slewed but the mount still reports pier "
        "side west") and night.said(
        "A: meridian flip complete (pier side west -> east)"), (
        f"premise: the setup, a no-op lead-time re-slew and a flip: "
        f"{night.gotos}, {night.said('A: ')}")
    got = [(r["pier_side"], round(r["exposed_at"] - night.gotos[0][0], 1))
           for r in rows]
    gone = [round(t - night.gotos[0][0], 1) for t, _w in night.gotos]
    assert got == [("west", gone[0]), ("east", gone[2])], (
        f"A's rows are {got}; the no-op re-slew solved nothing")
