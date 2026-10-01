"""The rotator approaches from one side, and says when the camera did not
follow (#526 parts 1 and 2, H4 orchestrator ruling 3).

On 2026-09-28, after the meridian flip on NGC 7331, the rotate loop commanded
-5.7 degrees. The rotator reported that it had moved -5.7, and the next solve
read the camera +0.5 degrees from where it had been; the loop then said only
that it was not converging. The data fit play in the train (a reversal eaten
before the camera turns) better than anything else. The ruling:

* every rotator move arrives from one fixed direction, increasing mechanical
  angle: a move the other way goes ``ROTATOR_BACKLASH_DEG`` (5) past its
  target and comes back, and ``rotation.one_sided_moves`` is the pure plan;
* the overshoot never leaves a limited mechanical range; there the move is
  made direct and the log says the one-side approach was skipped;
* a move whose solved sky change is under half the move commanded logs "the
  rotator moved but the camera did not", with the numbers, before the loop's
  not-converging abort.

The hub cases run on the simulator, whose rotator now models play
(``SimRig.rotator_backlash_deg``, the camera's angle ``rotator_mech_deg``, the
motor's ``rotator_mech_deg + rotator_slack_deg``) and records every mechanical
move it is asked for (``SimRotator.moves``). The CAA's real play is not
measured here: only the rig can.

Every mutation named below was run in a private scratch copy of ``server/``
(issue #254, #475), never in the shared tree. The failure each produced is
recorded verbatim on the test that caught it.
"""
from __future__ import annotations

import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)

from astrodeck.config import RotatorConfig, config_store
from astrodeck.devices.sim import SimRig, SimRotator
from astrodeck.rotation import ROTATOR_BACKLASH_DEG, one_sided_moves

#: The 2026-09-28 numbers: the rotator at mechanical 137.53, the camera at
#: sky PA 90.83 (so a -5.7 correction lands at 85.13, mechanical 131.83).
MECH = 137.53
PA = 90.83
MOVED_WORDS = "the rotator moved but the camera did not"
SKIPPED_WORDS = "the one-side approach was skipped at the range edge"


def _approx(values):
    return [pytest.approx(v, abs=1e-9) for v in values]


# ------------------------------------------------------------ the pure plan


def test_the_backlash_is_five_degrees():
    """The ruling's number, named once (H4 orchestrator ruling 3).

    Mutation 'a different backlash' (``ROTATOR_BACKLASH_DEG = 4.0``) went red
    here, and on every case below that plans an overshoot:

        >       assert ROTATOR_BACKLASH_DEG == 5.0
        E       assert 4.0 == 5.0
    """
    assert ROTATOR_BACKLASH_DEG == 5.0


def test_a_correction_against_the_approach_is_two_moves():
    """-5.7 degrees from 137.53: past the target by 5, then back to it, so
    the last leg turns the approach way.

    Mutation 'direct move whatever the direction' (``one_sided_moves``
    returns ``RotatorMoves((target,))`` before it reads the travel) went red
    on six cases: this one, the wrap case, the limited-range case, the
    range-edge case (on its ``skipped``), the hub's -5.7 case and the hub's
    range-edge case (no skipped line). Here:

        >       assert list(plan.moves) == _approx([MECH - 5.7 - 5.0, MECH - 5.7]), plan
        E       AssertionError: RotatorMoves(moves=(131.83,), skipped=None)
        E       assert [131.83] == [126.83000000....83 ± 1.0e-09]

    Mutation 'no fold to the shortest way' (below) went red on the same six,
    here with the same failure.
    """
    plan = one_sided_moves(MECH, MECH - 5.7, "full", 0.0)
    assert list(plan.moves) == _approx([MECH - 5.7 - 5.0, MECH - 5.7]), plan
    assert plan.skipped is None


def test_a_correction_with_the_approach_is_one_move():
    """Control: +5.7 degrees already arrives from the approach side."""
    plan = one_sided_moves(MECH, MECH + 5.7, "full", 0.0)
    assert list(plan.moves) == _approx([MECH + 5.7]), plan
    assert plan.skipped is None


def test_a_move_across_zero_is_planned_the_way_it_travels():
    """From 2 to 358 on a full range is -4 degrees, the shortest way, so it
    overshoots to 353 and comes back up to 358.

    Red under 'direct move whatever the direction' and under 'no fold to the
    shortest way', each time:

        >       assert list(plan.moves) == _approx([353.0, 358.0]), plan
        E       AssertionError: RotatorMoves(moves=(358.0,), skipped=None)
        E       assert [358.0] == [353.0 ± 1.0e...8.0 ± 1.0e-09]
    """
    plan = one_sided_moves(2.0, 358.0, "full", 0.0)
    assert list(plan.moves) == _approx([353.0, 358.0]), plan


def test_a_move_down_a_limited_range_is_planned_down():
    """170 to 10 on a half range from 0 is -160 degrees, which is both the
    shortest way and the way inside the range (a range of 180 degrees or
    less has no other), so it overshoots to 5 and comes back.

    Mutation 'no fold to the shortest way' (``mechanical_travel`` returns
    ``raw``, the travel always read upward) went red here and on the five
    other cases 'direct move whatever the direction' reddens:

        >       assert list(plan.moves) == _approx([5.0, 10.0]), plan
        E       AssertionError: RotatorMoves(moves=(10.0,), skipped=None)
        E       assert [10.0] == [5.0 ± 1.0e-0...0.0 ± 1.0e-09]

    A first version of ``mechanical_travel`` worked the travel out inside
    the range; the mutant 'shortest way on a limited range', which removed
    that branch, left all 16 cases green, because for a span of 180 degrees
    or less the two are the same number. The branch was removed.
    """
    plan = one_sided_moves(170.0, 10.0, "half", 0.0)
    assert list(plan.moves) == _approx([5.0, 10.0]), plan


def test_the_overshoot_never_leaves_a_limited_range():
    """Control, and the edge: on a half range from 0, a move down to 3 would
    overshoot to 358, outside the range, so it is made direct and says why.
    A move down to 10 overshoots to 5, inside, and is two moves.

    Mutation 'range edge ignored' (the ``span is not None and ...`` test
    made ``if False:``) went red here and on the hub's range-edge case.
    Re-run by the H4-HUB verifier on the test as it now reads (``if
    mod360(target - edge) < backlash_deg:`` made ``if False:``), it went red
    on the same two and on the full-range case below, with the same
    failure here:

        >       assert list(edge.moves) == _approx([3.0]), edge
        E       AssertionError: RotatorMoves(moves=(358.0, 3.0), skipped=None)
        E       assert [358.0, 3.0] == [3.0 ± 1.0e-09]

    Under 'direct move whatever the direction' and 'no fold to the shortest
    way' the move list is right by accident, and the ``skipped`` is not:

        >       assert edge.skipped and SKIPPED_WORDS in edge.skipped, edge
        E       AssertionError: RotatorMoves(moves=(3.0,), skipped=None)
    """
    edge = one_sided_moves(20.0, 3.0, "half", 0.0)
    assert list(edge.moves) == _approx([3.0]), edge
    assert edge.skipped and SKIPPED_WORDS in edge.skipped, edge
    inside = one_sided_moves(20.0, 10.0, "half", 0.0)
    assert list(inside.moves) == _approx([5.0, 10.0]), inside
    assert inside.skipped is None


def test_on_a_full_range_the_overshoot_never_crosses_mechanical_zero():
    """A full range's edge is mechanical 0 (added by the H4-HUB verifier). A
    move down from 10 to 3 would overshoot to 358: on a rotator with travel
    limits, which turns the long way rather than wrapping through 0, that is
    355 degrees up and 355 back for a 7 degree move. So it is made direct
    and says why. Controls: down to 5 exactly overshoots to 0, which does
    not cross it, and is two moves; and the wrap case above (2 to 358)
    overshoots to 353, nowhere near 0.

    Mutation 'no edge at mechanical 0 on a full range' (the edge test run
    only when ``span is not None``, as the implementer wrote it) went red
    here alone:

        >       assert list(edge.moves) == _approx([3.0]), edge
        E       AssertionError: RotatorMoves(moves=(358.0, 3.0), skipped=None)
        E       assert [358.0, 3.0] == [3.0 ± 1.0e-09]
    """
    edge = one_sided_moves(10.0, 3.0, "full", 0.0)
    assert list(edge.moves) == _approx([3.0]), edge
    assert edge.skipped and SKIPPED_WORDS in edge.skipped, edge
    assert "mechanical 0°" in edge.skipped, edge
    at_zero = one_sided_moves(10.0, 5.0, "full", 0.0)
    assert list(at_zero.moves) == _approx([0.0, 5.0]), at_zero
    assert at_zero.skipped is None
    # range_start_deg means nothing on a full range: the edge stays at 0.
    shifted = one_sided_moves(10.0, 3.0, "full", 200.0)
    assert list(shifted.moves) == _approx([3.0]), shifted


def test_a_zero_travel_move_is_direct():
    """#584 R1: the rotator is already AT the target. ``travel >= 0.0`` is the
    branch that sends a zero travel down the "already the approach way, one
    move" path rather than treating it as "against the approach direction"
    and sending a pointless overshoot-and-return.

    Mutation 'a zero travel counts as against the approach' (``travel >= 0.0``
    narrowed to ``travel > 0.0``, #584's R1) went red here:

        >       assert plan.moves == (40.0,), plan
        E       AssertionError: RotatorMoves(moves=(35.0, 40.0), skipped=None)
        E       assert (35.0, 40.0) == (40.0,)
        E
        E         At index 0 diff: 35.0 != 40.0
        E         Left contains one more item: 40.0
    """
    plan = one_sided_moves(40.0, 40.0, "full", 0.0)
    assert plan.moves == (40.0,), plan
    assert plan.skipped is None, plan


def test_an_exact_180_degree_travel_ties_to_the_approach_way():
    """#584 R4: ``mechanical_travel``'s docstring says a tie at exactly 180
    degrees goes to +180 (``raw <= 180.0``), matching how
    ``SimRotator.move_mechanical`` travels — so a move exactly half the
    full range away is never treated as running against the approach
    direction, and is one move, not an overshoot-and-return for nothing.

    Mutation 'the 180 tie breaks the other way' (``raw <= 180.0`` narrowed to
    ``raw < 180.0``, #584's R4) went red on the first assertion, before the
    second ever ran:

        >       assert mechanical_travel(40.0, 220.0) == 180.0
        E       assert -180.0 == 180.0
        E        +  where -180.0 = <function mechanical_travel at 0x...>(40.0, 220.0)
    """
    from astrodeck.rotation import mechanical_travel
    assert mechanical_travel(40.0, 220.0) == 180.0
    plan = one_sided_moves(40.0, 220.0, "full", 0.0)
    assert plan.moves == (220.0,), plan
    assert plan.skipped is None, plan


# ------------------------------------------------------- the simulator's play


async def test_the_sim_train_takes_up_its_play_on_a_reversal():
    """The model the hub cases stand on. Four degrees of play, the motor at
    the top of it (the last move was upward): a 3 degree reversal turns the
    motor and not the camera; the next 3 take up the last degree of play and
    turn the camera 2. The motor reports every degree it turned.

    Mutation 'play ignored in the sim' (``SimRotator._turn``'s ``half <= 0``
    branch taken always) went red here, and on the camera-did-not-follow
    case, where the camera then followed and the rotation converged:

        >       assert rig.rotator_mech_deg == pytest.approx(100.0), \\
        E       AssertionError: the camera turned inside the play
        E       assert 97.0 == 100.0 ± 1.0e-04
    """
    rig = SimRig()
    rig.rotator_backlash_deg = 4.0
    rig.rotator_slack_deg = 2.0
    rig.rotator_mech_deg = 100.0
    rot = SimRotator(rig)
    assert await rot.get_mechanical_position() == pytest.approx(102.0)

    await rot.move_mechanical(99.0)
    assert rig.rotator_mech_deg == pytest.approx(100.0), \
        "the camera turned inside the play"
    assert await rot.get_mechanical_position() == pytest.approx(99.0)
    await rot.move_mechanical(96.0)
    assert rig.rotator_mech_deg == pytest.approx(98.0)
    assert await rot.get_mechanical_position() == pytest.approx(96.0)
    assert rot.moves == _approx([99.0, 96.0])


async def test_a_sim_rotator_with_no_play_moves_the_camera_exactly():
    """Control: the default rig has no play, and the camera lands on the
    target as it always did, bit for bit."""
    rig = SimRig()
    rot = SimRotator(rig)
    await rot.move_mechanical(99.0)
    assert rig.rotator_mech_deg == 99.0
    assert rig.rotator_slack_deg == 0.0


# ----------------------------------------------------------- the rotate loop


def _rig_at(hub, *, play: float, slack: float) -> None:
    """The motor at ``MECH``, the camera ``slack`` below it, and the camera
    clocked so that its sky PA is ``PA``."""
    rig = hub.sim_rig
    rig.rotator_backlash_deg = play
    rig.rotator_slack_deg = slack
    rig.rotator_mech_deg = MECH - slack
    rig.rotator_pa_offset_deg = PA - rig.rotator_mech_deg


def _camera_pa(hub) -> float:
    rig = hub.sim_rig
    return (rig.rotator_mech_deg + rig.rotator_pa_offset_deg) % 360.0


async def test_a_minus_5_7_correction_overshoots_and_returns(sim_hub,
                                                             bus_lines):
    """The 2026-09-28 correction on a train with 4 degrees of play, the motor
    at the top of it. Two moves, 5 past and back; the return leg takes up the
    play, so the camera lands on the target and the loop converges on its
    second solve.

    Mutation 'the loop bypasses the approach' (``_rotate_to_pa_attempts``
    calls ``rot.move_to(...)`` as before, ``_approach_rotator`` unused) went
    red here, and on the hub's range-edge case (no skipped line):

        >       assert rot.moves == _approx([MECH - 10.7, MECH - 5.7]), rot.moves
        E       AssertionError: [131.83, 127.83000000000001]
        E       assert [131.83, 127.83000000000001] == [126.83 ± 1.0....83 ± 1.0e-09]

    The direct move lost 4 degrees to the play, so the loop needed a third
    solve. 'direct move whatever the direction' and 'no fold to the shortest
    way' went red here with the same failure.
    """
    _rig_at(sim_hub, play=4.0, slack=2.0)
    rot = sim_hub.devices["rotator"]
    rot.moves.clear()

    result = await sim_hub.rotate_to_pa(PA - 5.7)

    assert rot.moves == _approx([MECH - 10.7, MECH - 5.7]), rot.moves
    assert result["rotated"] is True and result["attempts"] == 2, result
    assert _camera_pa(sim_hub) == pytest.approx(PA - 5.7, abs=1e-6)
    assert any("comes back" in m for _, m, src in bus_lines
               if src == "rotator"), bus_lines
    assert not any(MOVED_WORDS in m for _, m, _ in bus_lines), bus_lines


async def test_a_correction_in_the_approach_direction_is_one_move(sim_hub):
    """Control: +5.7 on the same train is one move, and lands."""
    _rig_at(sim_hub, play=4.0, slack=2.0)
    rot = sim_hub.devices["rotator"]
    rot.moves.clear()

    result = await sim_hub.rotate_to_pa(PA + 5.7)

    assert rot.moves == _approx([MECH + 5.7]), rot.moves
    assert result["rotated"] is True and result["attempts"] == 2, result
    assert _camera_pa(sim_hub) == pytest.approx(PA + 5.7, abs=1e-6)


async def test_at_the_range_edge_the_move_is_direct_and_says_so(sim_hub,
                                                                bus_lines):
    """Control: a half range starting at mechanical 128. The -5.7 correction
    ends at 131.83, 3.83 inside the range, so a 5 degree overshoot would
    cross its start. The move is made direct, and the log says the one-side
    approach was skipped at the range edge. No play here, so the direct
    move still lands.

    Mutation 'range edge ignored' (see above) went red here:

        >       assert rot.moves == _approx([MECH - 5.7]), rot.moves
        E       AssertionError: [126.83000000000001, 131.83]
        E       assert [126.83000000000001, 131.83] == [131.83 ± 1.0e-09]

    'the loop bypasses the approach', 'direct move whatever the direction'
    and 'no fold to the shortest way' each went red here on the missing
    line:

        >       assert len(skipped) == 1, bus_lines
        E       AssertionError: [('info', 'sky angle PA 90.8° from the rotate to PA solve (pier east); rotator calibrated: it read 137.5°, now 90.8° (...r'), ('warning', 'camera rotated — guide calibration may be stale; PHD2 will re-calibrate or flip as needed', 'guide')]
        E       assert 0 == 1
    """
    config_store.set_rotator(RotatorConfig(range_type="half",
                                           range_start_deg=128.0,
                                           tolerance_deg=1.0))
    _rig_at(sim_hub, play=0.0, slack=0.0)
    rot = sim_hub.devices["rotator"]
    rot.moves.clear()

    result = await sim_hub.rotate_to_pa(PA - 5.7)

    assert rot.moves == _approx([MECH - 5.7]), rot.moves
    assert result["rotated"] is True, result
    assert result["adjusted_to"] is None, result
    skipped = [m for _, m, src in bus_lines
               if src == "rotator" and SKIPPED_WORDS in m]
    assert len(skipped) == 1, bus_lines


# ------------------------------------------------- the motion fence, per leg


async def test_a_stop_mid_overshoot_abandons_the_return_leg(sim_hub,
                                                             bus_lines,
                                                             monkeypatch):
    """#574: the one-side approach's overshoot leg is a second
    ``move_mechanical`` the loop-top fence (``_rotate_to_pa_attempts``) never
    sees, because each one WAITS for the rotator to arrive — seconds the
    engine's rotate holds no busy-task name for, so a STOP cancels neither
    it nor a goto. A STOP landing right after the overshoot leg lands must
    still stop the RETURN leg; sending it anyway is the camera moving after
    the operator pressed STOP.

    Mutation 'no check before the second leg' (the
    ``self._motion_committed_clean(epoch)`` call inside ``_approach_rotator``
    removed, both legs always sent) went red here:

        >       assert rot.moves == _approx([MECH - 10.7]), rot.moves
        E       AssertionError: [126.82999999999996, 131.82999999999996]
        E       assert [126.82999999...2999999999996] == [126.83 ± 1.0e-09]
        E
        E         Left contains one more item: 131.82999999999996
    """
    _rig_at(sim_hub, play=0.0, slack=0.0)
    rot = sim_hub.devices["rotator"]
    rot.moves.clear()

    real_move = SimRotator.move_mechanical
    calls = {"n": 0}

    async def move_then_stop(self, mech_deg):
        # The real move lands first (the overshoot leg completes — the rig
        # really did turn, which is exactly why a late-arriving STOP matters:
        # there is a real camera orientation to leave alone now), THEN the
        # fence is bumped, the same order a STOP mid-wait would land in.
        await real_move(self, mech_deg)
        calls["n"] += 1
        if calls["n"] == 1:
            sim_hub.bump_motion_epoch()

    monkeypatch.setattr(SimRotator, "move_mechanical", move_then_stop)

    result = await sim_hub.rotate_to_pa(PA - 5.7)

    assert rot.moves == _approx([MECH - 10.7]), rot.moves
    assert result["rotated"] is False and result["aborted"] is True, result
    assert result["attempts"] == 1, result
    assert result["pa_deg"] == pytest.approx(PA), result
    assert result["error_deg"] == pytest.approx(5.7, abs=1e-6), result
    assert any("rotate abandoned: aborted" in m for _, m, src in bus_lines
               if src == "rotator"), bus_lines


# ------------------------------------------- the camera that did not follow


async def test_a_move_the_camera_did_not_follow_is_named_before_the_abort(
        sim_hub, bus_lines):
    """A train with 30 degrees of play, the motor at the top of it: the
    one-side move (-10.7, then +5) turns the motor through all of it and the
    camera not at all. The second solve reads the same PA, the loop gives up
    as not converging, and before that the log says what happened, with the
    commanded, the reported and the solved numbers.

    Mutation 'generic not-converging only' (the ``if last_move is not None``
    test made ``if False:`` in ``_rotate_to_pa_attempts``) went red here and
    on the just-under-half case:

        >       assert len(said) == 1, [m for _, m, _ in bus_lines]
        E       AssertionError: ['sky angle PA 90.8° from the rotate to PA solve (pier east); rotator calibrated: it read 137.5°, now 90.8° (offset 46...arget, error, commanded): [(1, 90.8, 85.1, 5.7, -5.7), (2, 90.8, 85.1, 5.7, -5.7)]); continuing without rotation', ...]
        E       assert 0 == 1
    """
    _rig_at(sim_hub, play=30.0, slack=15.0)
    sim_hub.sim_rig.ra_hours, sim_hub.sim_rig.dec_deg = 5.0, 10.0

    result = await sim_hub.goto_and_center(5.0, 10.0, rotation_deg=PA - 5.7)

    assert result.get("rotation_skipped") is True, result
    msgs = [m for _, m, _ in bus_lines]
    said = [i for i, m in enumerate(msgs) if MOVED_WORDS in m]
    assert len(said) == 1, [m for _, m, _ in bus_lines]
    line = msgs[said[0]]
    assert "commanded -5.7°" in line and "turn +0.0°" in line, line
    assert "the rotator reports -5.7°" in line, line
    aborted = [i for i, m in enumerate(msgs) if "not converging" in m]
    assert aborted and said[0] < aborted[0], msgs


async def test_a_move_the_camera_followed_logs_no_such_line(sim_hub,
                                                            bus_lines):
    """Control: the same correction with no play. The camera follows the
    whole move and the loop converges, and nothing says it did not."""
    _rig_at(sim_hub, play=0.0, slack=0.0)

    result = await sim_hub.rotate_to_pa(PA - 5.7)

    assert result["rotated"] is True, result
    assert not any(MOVED_WORDS in m for _, m, _ in bus_lines), bus_lines


def _scripted_pas(monkeypatch, pas):
    """Each solve reports the next PA of ``pas`` (then the last), the way
    ``test_rotate_to_pa_convergence.py`` scripts the sky: the loop is real,
    only the sky's answer is set."""
    from astrodeck.solve.base import SolveResult
    from astrodeck.solve.simsolver import SimSolver
    seen = {"n": 0}

    async def solve(self, fits_path, **kw):
        pa = pas[min(seen["n"], len(pas) - 1)]
        seen["n"] += 1
        return SolveResult(True, ra_hours=0.0, dec_deg=0.0, rotation_deg=pa,
                           message="scripted")

    monkeypatch.setattr(SimSolver, "solve", solve)


async def test_exactly_half_the_move_is_following(sim_hub, monkeypatch,
                                                  bus_lines):
    """Control at the boundary: commanded -10.0 and the camera turned -5.0,
    exactly half. "Under half" is the ruling's line, so this is a camera
    that followed, and nothing is said; the next move then converges.

    Mutation 'at half counts' (``<=`` in place of ``<`` in the follow test)
    went red here alone:

        >       assert not any(MOVED_WORDS in m for _, m, _ in bus_lines), bus_lines
        E       AssertionError: [('info', 'sky angle PA 90.0° from the rotate to PA solve (pier east); rotator calibrated: it read 0.0°, now 90.0° (of... ('info', 'rotator attempt 2/5: solved PA 85.0°, target 80.0°, error 5.0°, commanding -5.0° to 80.0°', 'rotator'), ...]
        E       assert not True
    """
    _scripted_pas(monkeypatch, [90.0, 85.0, 80.0])

    result = await sim_hub.rotate_to_pa(80.0)

    assert result["rotated"] is True and result["attempts"] == 3, result
    assert not any(MOVED_WORDS in m for _, m, _ in bus_lines), bus_lines


async def test_just_under_half_the_move_is_named(sim_hub, monkeypatch,
                                                 bus_lines):
    """The other side of that line: -4.9 of -10.0 is under half, and said,
    though the loop goes on (the error still shrank enough).

    Red under 'generic not-converging only':

        >       assert len(said) == 1 and "commanded -10.0°" in said[0] \\
        E       AssertionError: []
        E       assert (0 == 1)
    """
    _scripted_pas(monkeypatch, [90.0, 85.1, 80.0])

    result = await sim_hub.rotate_to_pa(80.0)

    assert result["rotated"] is True, result
    said = [m for _, m, _ in bus_lines if MOVED_WORDS in m]
    assert len(said) == 1 and "commanded -10.0°" in said[0] \
        and "turn -4.9°" in said[0], said
