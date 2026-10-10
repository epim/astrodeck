# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A centring that missed is not imaged on (#852).

On 2026-10-07 a lone target logged "centering converged to 255.0' —
continuing" and shot a field 4.25 degrees from NGC 7331, and the guiding-loss
hold passed its re-centre's ``did_not_move`` result only to the sky-angle
record, recalibrated and imaged a field 2.8 degrees away for 28 frames.

The rule graded here (`SequenceEngine._centring_miss`): a centring that did
not centre stops the target when its measured field is past the centring
ceiling, a quarter of the field's SHORT side (ruling R8; 15' with no optics,
never below the target's own tolerance), or when the mount did not carry out
the correction (``did_not_move``, P4's ``goto_not_arrived``) and the field is
outside the ceiling or unmeasured. The same result INSIDE the ceiling is
imaged, with one warning carrying the figure (ruling R3 as overridden by the
orchestrator). Every site that centres reads it: setup, the three mid-run
re-centres, the flip and the tracking recovery.

The hub is the recording double of test_centring_settings_reach_goto.py
(answers scripted at the ``goto_and_center`` interface), given optics where a
case needs them: 1.123 x 0.75 deg, so the ceiling is 0.75 * 60 * 0.25 =
11.25'. Coordinates are fictional. Every mutant named below was applied to a
byte copy of the production file, run under the suite's normal command, and
the file restored from the copy with its sha256 checked.
"""
from __future__ import annotations

import time

import pytest

import astrodeck.flows.tonight as tonight_mod
import astrodeck.sequence.engine as engine_mod
from astrodeck.sequence.engine import (
    CENTRING_DID_NOT_MOVE, CENTRING_NO_LIGHT, CENTRING_NOT_ARRIVED,
    CENTRING_TOO_FAR, CENTRING_UNSOLVED_TWICE, FIELD_HOLDS_SPENT,
    POSITION_UNKNOWN_STOP, RECOVERY_RECENTRES_SPENT, SequenceEngine,
    StopTarget)
from astrodeck.sequence.group_rules import CENTRING, PanelDeferred
from astrodeck.sequence.models import TargetGroup

from test_centring_settings_reach_goto import (_after_a_lost_star,
                                               _after_a_walking_field,
                                               _after_the_sweep, _guided,
                                               _setup, _target)
from test_850_engine_sync_refused import (_flip_with, _humanizer_rewrites,
                                          _scripted, flip_hub)  # noqa: F401

#: 67.4' x 45' (the rig of 2026-10-07): the ceiling is 45 * 0.25 = 11.25'.
OPTICS = {"fov_w_deg": 1.123, "fov_h_deg": 0.75}


@pytest.fixture(autouse=True)
def _the_window_is_open(monkeypatch):
    """The no-light hold bounds itself by the target's own window
    (`flows.tonight.target_own_window`), computed for the hour the suite
    runs. Answered "unknown" here, so the hold's retries depend on its own
    count and not on the clock (a time-of-day dependence that makes the
    #850 hold test fail at some hours)."""
    monkeypatch.setattr(tonight_mod, "target_own_window",
                        lambda *a, **kw: None)


def _with_optics(hub) -> None:
    hub.effective_optics = lambda: dict(OPTICS)


def _did_not_move(err: float = 255.0) -> dict:
    return {"centered": False, "error_arcmin": err, "attempts": 2,
            "did_not_move": True}


def _continuing(lines) -> list[str]:
    return [m for _l, m, _s in lines if "continuing" in m]


def _warnings(lines) -> list[str]:
    return [m for lvl, m, src in lines if lvl == "warning" and src == "sequence"]


# ======================================================= target setup, single

async def test_a_single_target_that_did_not_move_stops_at_acquisition(
        bus_lines):
    """The 2026-10-07 shape: the correction slew changed nothing, 255' off.
    The target stops with the fixed words, after the idle bookkeeping, and
    nothing says "continuing".

    MUTANT "setup continues on a miss" (the ``elif
    self._centring_miss(result, target) is not None`` arm in
    `_setup_target` deleted): RED -
        Failed: DID NOT RAISE <class 'astrodeck.sequence.engine.StopTarget'>
    """
    t = _target(name="Fictional A")
    e, hub = _setup(t)
    _scripted(hub, [_did_not_move(255.0)])
    with pytest.raises(StopTarget) as ei:
        await e._setup_target(0, t)
    assert str(ei.value) == f"centring at acquisition: {CENTRING_DID_NOT_MOVE}"
    assert _continuing(bus_lines) == [], _continuing(bus_lines)
    assert e._tracked_target is t, "the stop ran before the bookkeeping"
    assert any(w.startswith("Fictional A: stopping this target; centring at "
                            "acquisition: the mount did not move, still "
                            "255.0' off target") for w in _warnings(bus_lines)
               ), bus_lines


async def test_a_single_target_far_off_stops(bus_lines):
    """A measured field 30' off on a 45' short side: past the ceiling.

    MUTANT "no ceiling" (``if err is not None and not inside`` in
    `_centring_miss` made ``if err is not None and False``): RED -
        Failed: DID NOT RAISE <class 'astrodeck.sequence.engine.StopTarget'>
    """
    t = _target(name="Fictional A")
    e, hub = _setup(t)
    _with_optics(hub)
    _scripted(hub, [{"centered": False, "error_arcmin": 30.0, "attempts": 3}])
    with pytest.raises(StopTarget) as ei:
        await e._setup_target(0, t)
    assert str(ei.value) == f"centring at acquisition: {CENTRING_TOO_FAR}"
    assert any("still 30.0' off target, past the 11.2' limit" in w
               for w in _warnings(bus_lines)), bus_lines


@pytest.mark.parametrize("err, stops", [(11.25, False), (11.26, True)],
                         ids=["at the ceiling", "just past it"])
async def test_the_ceiling_is_a_quarter_of_the_short_side(err, stops,
                                                          bus_lines):
    """0.75 deg * 60 * 0.25 = 11.25'. Exactly at it goes on; past it stops.

    MUTANT "inclusive ceiling" (``err <= self._centring_ceiling_arcmin``
    made ``<``): RED, "at the ceiling" -
        astrodeck.sequence.engine.StopTarget: centring at acquisition: the
        field is too far off target to image
    MUTANT "long side" (``min(w, h)`` made ``max(w, h)``): RED, "just past
    it" -
        Failed: DID NOT RAISE <class 'astrodeck.sequence.engine.StopTarget'>
    """
    t = _target(name="Fictional A")
    e, hub = _setup(t)
    _with_optics(hub)
    _scripted(hub, [{"centered": False, "error_arcmin": err, "attempts": 3}])
    if stops:
        with pytest.raises(StopTarget):
            await e._setup_target(0, t)
        return
    await e._setup_target(0, t)
    assert any("centering ended 11.2' off target" in m
               for m in _continuing(bus_lines)), bus_lines


@pytest.mark.parametrize("err, stops", [(14.9, False), (15.1, True)])
async def test_without_optics_the_ceiling_is_fifteen_arcmin(err, stops,
                                                            bus_lines):
    """No optics (the double has no ``effective_optics``): 15', the hub's own
    floor for a real displacement.

    MUTANT ``CENTRING_CEILING_NO_OPTICS_ARCMIN = 60.0``: RED, 15.1 -
        Failed: DID NOT RAISE <class 'astrodeck.sequence.engine.StopTarget'>
    """
    t = _target(name="Fictional A")
    e, hub = _setup(t)
    _scripted(hub, [{"centered": False, "error_arcmin": err, "attempts": 3}])
    if stops:
        with pytest.raises(StopTarget):
            await e._setup_target(0, t)
    else:
        await e._setup_target(0, t)
        assert _continuing(bus_lines), bus_lines


def test_the_ceiling_never_drops_below_the_target_tolerance():
    """A target that asked to centre to 20' is not stopped at 15'.

    MUTANT "the tolerance ignored" (``return max(ceiling, tol)`` made
    ``return ceiling``): RED -
        AssertionError: assert 'the field is too far off target to image' is None
    """
    t = _target(center_tolerance_arcmin=20.0)
    e, hub = _setup(t)
    _with_optics(hub)
    assert e._centring_miss(
        {"centered": False, "error_arcmin": 15.0}, t) is None


async def test_a_near_miss_continues_without_saying_converged(bus_lines):
    """4' off after three attempts is inside the ceiling: imaging goes on,
    and the line says what happened, not "converged".

    MUTANT "converged" (`_say_centring_ended` reverted to
    ``f"{target.name}: centering converged to {err:.1f}' — continuing"``):
    RED -
        AssertionError: ["Fictional A: centering converged to 4.0' — continuing"]
    """
    t = _target(name="Fictional A")
    e, hub = _setup(t)
    _scripted(hub, [{"centered": False, "error_arcmin": 4.0, "attempts": 3}])
    await e._setup_target(0, t)
    lines = _continuing(bus_lines)
    assert any("centering ended 4.0' off target after 3 attempts" in m
               and m.endswith("— continuing") for m in lines), lines
    assert not any("converged" in m for _l, m, _s in bus_lines), lines


async def test_a_no_light_retry_that_solved_and_missed_stops(bus_lines):
    """The REAL no-light hold: the first centring found nothing, the retry
    solved and the mount did not move, 255' off. A stop, not "still no
    light" (finding N1).

    MUTANT "post-hold miss unread" (the ``elif self._centring_miss(result,
    target) is not None: miss_stop = result`` after the hold removed): RED -
        Failed: DID NOT RAISE <class 'astrodeck.sequence.engine.StopTarget'>
    """
    t = _target(name="Fictional A")
    e, hub = _setup(t)
    _scripted(hub, [{"centered": False, "error_arcmin": None},
                    _did_not_move(255.0)])
    with pytest.raises(StopTarget) as ei:
        await e._setup_target(0, t)
    assert len(hub.gotos) == 2, f"premise: one retry: {hub.gotos}"
    assert str(ei.value) == f"centring at acquisition: {CENTRING_DID_NOT_MOVE}"
    assert not any("still no light" in m for _l, m, _s in bus_lines), bus_lines


async def test_a_goto_that_did_not_arrive_stops_and_never_holds_for_light(
        bus_lines):
    """P4's ``goto_not_arrived`` with no figure and no failed solve: read
    before the no-light arm, so it stops rather than holding for light
    (seam S4).

    MUTANT "the key unread" (the ``goto_not_arrived`` branch of `_unmoved`
    removed): RED -
        Failed: DID NOT RAISE <class 'astrodeck.sequence.engine.StopTarget'>
    """
    t = _target(name="Fictional A")
    e, hub = _setup(t)
    _scripted(hub, [{"centered": False, "error_arcmin": None,
                     "goto_not_arrived": True,
                     "goto_reason": "the mount stopped short"}])
    with pytest.raises(StopTarget) as ei:
        await e._setup_target(0, t)
    assert str(ei.value) == f"centring at acquisition: {CENTRING_NOT_ARRIVED}"
    assert len(hub.gotos) == 1, f"a goto that did not arrive held for light: {hub.gotos}"


# ============================================ ruling R3 (orchestrator override)

@pytest.mark.parametrize("answer, what", [
    (_did_not_move(8.0), "the correction slew did not move the mount"),
    ({"centered": False, "error_arcmin": 8.0, "attempts": 1,
      "goto_not_arrived": True}, "the mount did not reach the target"),
], ids=["did not move", "did not arrive"])
async def test_an_unmoved_mount_inside_the_ceiling_is_imaged(answer, what,
                                                             bus_lines):
    """Ruling R3 as overridden: the mount did not carry out the correction,
    but the solved field is 8' off, inside the 11.25' ceiling, so it still
    frames the target. Imaging goes on, ONE warning carries the figure, and
    the pointing counts as verified by that solve.

    MUTANT "every unmoved result stops" (``return None if inside else
    unmoved`` in `_centring_miss` made ``return unmoved``): RED, both -
        astrodeck.sequence.engine.StopTarget: centring at acquisition: the
        mount did not move to correct the pointing
    """
    t = _target(name="Fictional A")
    e, hub = _setup(t)
    _with_optics(hub)
    _scripted(hub, [answer])
    await e._setup_target(0, t)
    said = [w for w in _warnings(bus_lines) if "imaging goes on" in w]
    assert said == [f"Fictional A: {what}, so imaging goes on: the field is "
                    f"8.0' off target, inside the 11.2' limit"], bus_lines
    assert e._pointing_unverified_for is None


async def test_an_unmoved_mount_inside_the_ceiling_restarts_the_hold(bus_lines):
    """The same rule mid-run: the walking-field hold's re-centre did not
    move the mount and the field is 8' off. No stop; guiding restarts.

    MUTANT "every unmoved result stops": RED -
        astrodeck.sequence.engine.StopTarget: re-centring after the guided
        field walked: the mount did not move to correct the pointing
    """
    t = _target(name="Fictional B")
    e, hub = _guided(t)
    _with_optics(hub)
    _scripted(hub, [_did_not_move(8.0)])
    await _after_a_walking_field(e, t)
    assert hub.guider.calls[-1] == "start", hub.guider.calls
    assert any("the correction slew did not move the mount, so imaging goes "
               "on" in w for w in _warnings(bus_lines)), bus_lines


# ================================================== the angle lock (#852 4)

async def test_a_miss_never_locks_the_angle(monkeypatch, bus_lines):
    """An unframed target in a run with a session locks its angle from the
    acquisition's first fresh solve (ruling 9). A solve of the WRONG field
    must not become that lock: the stop comes before `_settle_locked_angle`.
    The control half proves the harness reaches the lock at all.

    MUTANT "stop at the end" (the ``if miss_stop is not None:`` block moved
    from after the sync stop to just after the ``self._settle_locked_angle``
    call): RED -
        AssertionError: a miss locked the angle: {'pa_deg': 42.0, ...}
    """
    from astrodeck.sequence.session import Session
    monkeypatch.setattr(engine_mod.session_store, "save_run_state",
                        lambda s: None)
    for answer, locks in ((_did_not_move(255.0), False),
                          ({"centered": True, "error_arcmin": 0.3}, True)):
        t = _target(name="Fictional A")
        e, hub = _setup(t)
        e._session = Session(name="s", created_ts=time.time(),
                             status="active", plan=e.plan)
        hub.last_sky_angle = {"pa_deg": 42.0, "exposed_at": time.time() + 60,
                              "solved_at": time.time() + 61,
                              "source": "plate solve + sync"}
        _scripted(hub, [answer])
        if locks:
            await e._setup_target(0, t)
            assert e._session.locked_angle(t.id) is not None, (
                "premise: the harness never reaches the lock")
        else:
            with pytest.raises(StopTarget):
                await e._setup_target(0, t)
            assert e._session.locked_angle(t.id) is None, (
                f"a miss locked the angle: {e._session.locked_angle(t.id)}")


# ======================================================== target setup, panels

def _member(e, t, *, require_centred: bool) -> TargetGroup:
    g = TargetGroup(id="g1", name="Fictional mosaic",
                    require_centred=require_centred)
    e._group_of = lambda target: g if target is t else None
    e._hop_angle_within = lambda *a, **kw: False
    return g


async def test_a_panel_without_require_centred_stops_on_a_miss(bus_lines):
    """A panel set to "shoot anyway" is still not shot on a field 255' off:
    it stops, and its visit defers it (TARGET_STOP).

    MUTANT "lone targets only" (the miss arm's condition made ``elif member
    is None and self._centring_miss(result, target) is not None``): RED -
        Failed: DID NOT RAISE <class 'astrodeck.sequence.engine.StopTarget'>
    """
    t = _target(name="Fictional A 1-1", mosaic_group="g1")
    e, hub = _setup(t)
    _member(e, t, require_centred=False)
    _scripted(hub, [_did_not_move(255.0)])
    with pytest.raises(StopTarget) as ei:
        await e._setup_target(0, t)
    assert str(ei.value) == f"centring at acquisition: {CENTRING_DID_NOT_MOVE}"


async def test_control_a_required_panel_still_defers(bus_lines):
    """CONTROL. A panel that requires centring keeps today's deferral, kind
    CENTRING; only the wording changes ("ended", not "converged").

    MUTANT "miss arm placed before require_centred" (the two ``elif`` arms
    swapped): RED -
        astrodeck.sequence.engine.StopTarget: centring at acquisition: the
        mount did not move to correct the pointing
    """
    t = _target(name="Fictional A 1-1", mosaic_group="g1")
    e, hub = _setup(t)
    _member(e, t, require_centred=True)
    _scripted(hub, [_did_not_move(255.0)])
    with pytest.raises(PanelDeferred) as ei:
        await e._setup_target(0, t)
    assert ei.value.kind == CENTRING
    assert ei.value.last_error == "ended 255.0' off target"


# ============================================== the mid-run re-centres

_WHERE = {
    _after_the_sweep: "re-centring after the unguided sweep",
    _after_a_lost_star: "re-centring after the guide star went missing",
    _after_a_walking_field: "re-centring after the guided field walked",
}


@pytest.mark.parametrize(
    "recentre", [_after_the_sweep, _after_a_lost_star, _after_a_walking_field],
    ids=["after the unguided sweep", "after a lost star",
         "after a walking field"])
async def test_every_mid_run_re_centre_stops_on_a_miss(recentre, bus_lines):
    """The hold path used to read a re-centre's result only for its sky
    angle and resume guiding on a field 130-166' off (#852 item 1). Each
    mid-run re-centre now stops the target on a miss, before the guider is
    restarted.

    MUTANT "the hold re-centre ignores a miss" (the
    ``self._stop_if_centring_missed(res, target, where)`` in
    `_recentre_for_hold`'s attempt loop removed): RED, "after a lost star"
    and "after a walking field" -
        Failed: DID NOT RAISE <class 'astrodeck.sequence.engine.StopTarget'>
    MUTANT "the sweep re-centre ignores a miss" (the
    `_stop_if_centring_missed` call in `_recentre_after_unguided_focus`
    removed): RED, "after the unguided sweep" -
        Failed: DID NOT RAISE <class 'astrodeck.sequence.engine.StopTarget'>
    """
    t = _target(name="Fictional B")
    e, hub = _guided(t)
    _scripted(hub, [_did_not_move(255.0)])
    with pytest.raises(StopTarget) as ei:
        await recentre(e, t)
    assert str(ei.value) == f"{_WHERE[recentre]}: {CENTRING_DID_NOT_MOVE}"
    assert "start" not in hub.guider.calls, hub.guider.calls


async def test_the_flip_stops_on_a_miss_and_spares_a_centring_off_target(
        flip_hub, monkeypatch, bus_lines):
    """The flip re-centres whatever the target says. A target that asked
    for centring stops on a miss; one with centring off gets the warning
    and goes on, as #850's refused sync does.

    MUTANT "the flip stops everyone" (``centring_wanted=flip_centring_wanted``
    in the flip's `_stop_if_centring_missed` call made
    ``centring_wanted=True``): RED, the centring-off half -
        astrodeck.sequence.engine.StopTarget: re-centring after the meridian
        flip: the mount did not move to correct the pointing
    MUTANT "the flip ignores a miss" (that call removed): RED, the
    centring-on half -
        Failed: DID NOT RAISE <class 'astrodeck.sequence.engine.StopTarget'>
    """
    e, t, st = _flip_with(flip_hub, monkeypatch, _did_not_move(255.0))
    t.center = True
    with pytest.raises(StopTarget) as ei:
        await e._maybe_meridian_flip(t, next_exposure_s=180.0)
    assert str(ei.value) == (
        f"re-centring after the meridian flip: {CENTRING_DID_NOT_MOVE}")

    e, t, st = _flip_with(flip_hub, monkeypatch, _did_not_move(255.0))
    t.center = False
    before = len(bus_lines)
    await e._maybe_meridian_flip(t, next_exposure_s=180.0)
    said = [m for lvl, m, _s in bus_lines[before:]
            if lvl == "warning" and "centring is off, so imaging goes on" in m]
    assert said == ["T: re-centring after the meridian flip: centring is off, "
                    "so imaging goes on, 255.0' off target"], bus_lines


# ============================== the evidence at each call site (R4, #851)

async def test_a_sweep_re_centre_that_raised_marks_the_pointing_unverified(
        bus_lines):
    """The re-centre after the unguided sweep raised: guiding starts at the
    current pointing, which nobody measured, so the target's lights from
    here are left out of the report's integration (ruling R4).

    MUTANT "the sweep's failure marks nothing" (``self._mark_pointing_
    unverified(target)`` removed from `_recentre_after_unguided_focus`'s
    ``except`` arm): RED -
        AssertionError: a failed sweep re-centre left the pointing trusted
    """
    t = _target(name="Fictional B")
    e, hub = _guided(t)

    async def goto(*args, **kwargs):
        hub.gotos.append((args, dict(kwargs)))
        raise RuntimeError("the link dropped")
    hub.goto_and_center = goto
    await _after_the_sweep(e, t)
    assert len(hub.gotos) == 1, "premise: the re-centre was tried"
    assert e._pointing_unverified_for == t.id, (
        "a failed sweep re-centre left the pointing trusted")


async def test_a_sweep_re_centre_that_centred_clears_the_mark(bus_lines):
    """A target marked unverified is re-centred after the sweep, and that
    centring converged: good evidence, so its lights count again.

    MUTANT "the sweep's result is not evidence" (the `_note_centring_evidence`
    call at the end of `_recentre_after_unguided_focus` removed): RED -
        AssertionError: a centred sweep re-centre left the target unverified
    """
    t = _target(name="Fictional B")
    e, hub = _guided(t)
    e._pointing_unverified_for = t.id
    _scripted(hub, [{"centered": True, "error_arcmin": 0.3, "attempts": 1}])
    await _after_the_sweep(e, t)
    assert len(hub.gotos) == 1, "premise: the re-centre ran"
    assert e._pointing_unverified_for is None, (
        "a centred sweep re-centre left the target unverified")


async def test_a_flip_re_centre_that_centred_clears_the_mark(
        flip_hub, monkeypatch, bus_lines):
    """The REAL ``hub.meridian_flip``: a target marked unverified flips, and
    the flip's re-centre converged. Good evidence, so its lights count
    again, and a disagreement counted before it is retired.

    MUTANT "the flip's result is not evidence" (``if flip_centring_wanted:
    self._note_centring_evidence(flip_result, target)`` removed): RED -
        AssertionError: a centred flip left the target unverified
    """
    e, t, st = _flip_with(flip_hub, monkeypatch,
                          {"centered": True, "error_arcmin": 0.2,
                           "attempts": 1})
    t.center = True
    e._pointing_unverified_for = t.id
    await e._maybe_meridian_flip(t, next_exposure_s=180.0)
    assert st["gotos"], "premise: the flip must have re-slewed"
    assert e._pointing_unverified_for is None, (
        "a centred flip left the target unverified")


# ===================================================== the surfaced strings

_ALL_WHERES = [
    "centring at acquisition",
    "re-centring after the unguided sweep",
    "re-centring after the guide star went missing",
    "re-centring after the guided field walked",
    "re-centring after the pointing re-check",
    "re-centring after the meridian flip",
    "re-centring after the tracking recovery",
]
_FIXED = [CENTRING_DID_NOT_MOVE, CENTRING_TOO_FAR, CENTRING_NOT_ARRIVED,
          CENTRING_UNSOLVED_TWICE, CENTRING_NO_LIGHT, FIELD_HOLDS_SPENT,
          RECOVERY_RECENTRES_SPENT, POSITION_UNKNOWN_STOP]


@pytest.mark.parametrize("name", ["A" * 7, "B" * 24], ids=["7", "24"])
async def test_the_strings_keep_the_rules(name, bus_lines):
    """Every new line and stop text, built by the real helpers with every
    ``where`` and names of 7 and 24 characters:

    - nothing trips the UI's humanizer (its four rules);
    - every stop line has its outcome ("stopping this target") inside the
      137 characters the UI keeps, and every "goes on" line its "imaging
      goes on";
    - the position-unknown line's whole action, through "then Trust
      position.", fits inside 137 for both names, and it carries no goto,
      slew or "go to" (the SAFETY RULE);
    - no StopTarget or abort text carries a digit (fixed words, #618).

    MUTANT "a figure in a fixed sentence" (``CENTRING_UNSOLVED_TWICE`` made
    "the field did not solve twice in 5 s, so the pointing is unknown"):
    RED, both -
        AssertionError: a digit in a fixed text: 'the field did not solve
        twice in 5 s, so the pointing is unknown'
    MUTANT "the name first" (``_stop_run_position_unknown``'s line built as
    ``f"{name}: {self._POSITION_UNKNOWN_ACTION} the run stops ..."``): RED,
    the 24-character name -
        AssertionError: the action ends at 157
    """
    for text in _FIXED + [f"{w}: {c}" for w in _ALL_WHERES for c in _FIXED]:
        assert not any(ch.isdigit() for ch in text), (
            f"a digit in a fixed text: {text!r}")
        assert not _humanizer_rewrites(text), text
    t = _target(name=name)
    e, hub = _setup(t)
    _with_optics(hub)
    lines = []
    for where in _ALL_WHERES:
        for res in (_did_not_move(255.0), _did_not_move(None),
                    {"centered": False, "error_arcmin": 255.0},
                    {"centered": False, "error_arcmin": 255.0,
                     "goto_not_arrived": True},
                    {"centered": False, "error_arcmin": None,
                     "goto_not_arrived": True}):
            stop = e._centring_miss_line(res, t, where, goes_on=False)
            assert stop.index("stopping this target") < 137, stop
            lines.append(stop)
            on = e._centring_miss_line(res, t, where, goes_on=True)
            assert on.index("imaging goes on") + len("imaging goes on") <= 137, on
            lines.append(on)
        inside = e._unmoved_inside_line(_did_not_move(11.2), t, where)
        assert inside.index("imaging goes on") + len("imaging goes on") <= 137, (
            inside)
        lines.append(inside)
        lines.append(f"{name}: skipped — {where}: {CENTRING_UNSOLVED_TWICE}")
    e._say_centring_ended(t, {"centered": False, "error_arcmin": 4.0,
                              "attempts": 3})
    before = len(bus_lines)
    with pytest.raises(engine_mod.PositionUnknownStop) as ei:
        await e._stop_run_position_unknown(
            t, "re-centring after the guide star went missing")
    assert str(ei.value) == POSITION_UNKNOWN_STOP
    unknown = [m for _l, m, _s in bus_lines[before:]]
    assert len(unknown) == 1, unknown
    end = unknown[0].index("then Trust position.") + len("then Trust position.")
    assert end <= 137, f"the action ends at {end}"
    assert unknown[0].index("Trust position") < 60, unknown[0]
    for word in ("goto", "slew", "go to"):
        assert word not in unknown[0].lower(), unknown[0]
    lines += [m for _l, m, _s in bus_lines]
    for line in lines:
        assert not _humanizer_rewrites(line), f"the humanizer rewrites: {line!r}"
