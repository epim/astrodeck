"""D-05 (backlog ruling, owner-approved 2026-09-30; #594): the rotator's
coupling is physically loose, and "a loose coupling reads as a healthy
rotator to everything except a plate solve" (the owner, on #594, after
confirming the hardware had come loose again). Two changes, both scoped to
stay clear of WP-32a's existing suite and the base NINA-parity convergence
suite:

* a NEW, dedicated nightly self-test (``Hub.rotator_self_test``) commands
  one KNOWN step and solves before and after it; failing it (less than
  ``ROTATOR_SELF_TEST_FOLLOW_FRACTION``, D-05's 90%, followed) takes
  rotation off for the rest of the night (``Hub._rotation_trusted = False``)
  until the next self-test passes -- this IS D-05's "follow-check refuses"
  half, as a clean, one-shot measurement with nothing else able to blur it;
* ``rotate_to_pa`` refuses outright once a self-test has FAILED, the same
  shape WP-32a's sign refusal already uses (no special plumbing in
  ``goto_and_center`` -- it degrades like any other rotate failure).

``_rotate_to_pa_attempts``'s own per-attempt follow check (WP-32a, #526) is
UNCHANGED: still a warning, still at its original fraction
(``ROTATE_FOLLOW_FRACTION``, 0.5). An EARLIER version of this change raised
that threshold to 0.9 and turned the warning into a refusal directly, and
running it against the wider suite found it was the wrong file: it broke
three tests in test_h4_rotator_backlash.py (two of which deliberately script
an under-followed attempt that still converges), seven in
test_rotate_to_pa_convergence.py (the follow check pre-empted the generic
"not converging" abort's own message on any scripted divergence), and two in
test_mosaic_spec_claims.py (the mosaic spec document still says 0.5). None of
those four files are owned by this WP (hub.py, rotation.py only), so the
correctly-scoped fix is the one here: D-05's stricter, refusing number lives
only in the self-test's own dedicated measurement, which is new code with no
prior tests to contradict. See ``ROTATE_FOLLOW_FRACTION``'s docstring in
hub.py for the same reasoning in place.

Every mutation named below was run in a private scratch copy of ``server/``
(issue #254, #475), never in the shared tree. The failure each produced is
recorded verbatim on the test that caught it.
"""
from __future__ import annotations

import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)

from astrodeck.devices.base import DeviceError
from astrodeck.rotation import follow_fraction


# --------------------------------------------------------- the pure fraction


def test_follow_fraction_is_measured_against_what_more_was_turned():
    """D-05's own comparison: half the commanded move followed is 0.5, not
    0.0 or 1.0 -- and the sign of either argument never matters, only size.

    Mutation 'follow fraction forced to 1.0' (``follow_fraction``'s body
    replaced with ``return 1.0``) went red here:

        >       assert follow_fraction(5.0, 10.0) == pytest.approx(0.5)
        E       assert 1.0 == 0.5 ± 5.0e-07
    """
    assert follow_fraction(5.0, 10.0) == pytest.approx(0.5)
    assert follow_fraction(-9.0, 10.0) == pytest.approx(0.9)
    assert follow_fraction(9.0, -10.0) == pytest.approx(0.9)
    assert follow_fraction(10.0, 10.0) == pytest.approx(1.0)


def test_follow_fraction_of_a_zero_commanded_move_is_full():
    """A move of zero cannot be under-followed -- there is nothing to
    compare against, so this reads as trusted (1.0) rather than raising a
    ZeroDivisionError on an input ``_rotate_to_pa_attempts`` never produces
    today but a pure function must not crash on.

    Mutation 'the zero-commanded guard is removed' (``if commanded_deg ==
    0.0: return 1.0`` made ``if False: ...``, falling through to the plain
    division) went red here:

        >       assert follow_fraction(0.0, 0.0) == 1.0
        E       ZeroDivisionError: float division by zero
    """
    assert follow_fraction(0.0, 0.0) == 1.0


# ------------------------------------- the rotate loop's own check, untouched


def _scripted_pas(monkeypatch, pas):
    """Each solve reports the next PA of ``pas`` (then the last), exactly as
    ``test_h4_rotator_backlash.py``'s own helper of the same name does, for
    the same reason: the loop under test is real, only the sky's answer is
    scripted, decoupled from ``SimRotator``'s physical model entirely."""
    from astrodeck.solve.base import SolveResult
    from astrodeck.solve.simsolver import SimSolver
    seen = {"n": 0}

    async def solve(self, fits_path, **kw):
        pa = pas[min(seen["n"], len(pas) - 1)]
        seen["n"] += 1
        return SolveResult(True, ra_hours=0.0, dec_deg=0.0, rotation_deg=pa,
                           message="scripted")

    monkeypatch.setattr(SimSolver, "solve", solve)


async def test_a_followed_correction_between_the_two_thresholds_is_quiet(
        sim_hub, monkeypatch, bus_lines):
    """80% of a -10.0 degree move followed (-8.0 solved) sits ABOVE WP-32a's
    own warning line (0.5, unchanged by this WP) and BELOW D-05's self-test
    line (0.9, which lives only in ``rotator_self_test``): neither fires, and
    the loop converges exactly as it would have before D-05. This is the
    regression guard that pins D-05's number to the self-test alone -- it
    must not leak into every ordinary rotation's in-flight retries."""
    _scripted_pas(monkeypatch, [90.0, 82.0, 80.0])

    result = await sim_hub.rotate_to_pa(80.0)

    assert result["rotated"] is True and result["attempts"] == 3, result
    assert not any("rotator moved but the camera did not" in m
                  for _, m, _ in bus_lines), bus_lines


# ---------------------------------------------------------- the self-test


async def test_rotator_self_test_passes_with_no_play(sim_hub, bus_lines):
    """Control: the default sim rig has no backlash, so a 20 degree step is
    followed in full and the self-test passes, trusting the rotator."""
    result = await sim_hub.rotator_self_test(step_deg=20.0)

    assert result["passed"] is True, result
    assert result["fraction"] == pytest.approx(1.0, abs=1e-6), result
    assert sim_hub._rotation_trusted is True
    assert any("rotator self-test passed" in m for _, m, src in bus_lines
              if src == "rotator"), bus_lines


async def test_rotator_self_test_fails_when_the_coupling_slips(sim_hub,
                                                                bus_lines):
    """A train with 50 degrees of play, the motor already at the bottom of
    it: the self-test's +20 degree step is entirely absorbed into slack (the
    whole step stays inside the 50 degree dead zone) and the camera does not
    move at all -- exactly the shape #594 measured on the real rig (a 103
    degree move turned the camera 2.8 degrees).

    Mutation 'self-test ignores its own threshold' (``passed = fraction >=
    ROTATOR_SELF_TEST_FOLLOW_FRACTION`` replaced with ``passed = True``)
    went red here:

        >       assert result["passed"] is False, result
        E       assert True is False
    """
    rig = sim_hub.sim_rig
    rig.rotator_backlash_deg = 50.0
    rig.rotator_slack_deg = -25.0  # the bottom of the play

    result = await sim_hub.rotator_self_test(step_deg=20.0)

    assert result["passed"] is False, result
    assert result["fraction"] == pytest.approx(0.0, abs=1e-6), result
    assert result["mechanical_travel_deg"] == pytest.approx(20.0, abs=0.01), \
        result
    assert sim_hub._rotation_trusted is False
    said = [m for _, m, src in bus_lines
           if src == "rotator" and "rotator self-test FAILED" in m]
    assert len(said) == 1 and "594" in said[0], bus_lines


async def test_rotator_self_test_refuses_on_a_rotator_that_did_not_move(
        sim_hub, monkeypatch):
    """A stuck or disconnected rotator must not silently report a result
    either way -- there is nothing to measure a fraction from -- and a
    failed measurement must not flip a PRIOR verdict (mirrors
    ``learn_rotator_sign``'s own refusal, test_w4_rotator_sign.py)."""
    async def frozen_move(self, mech_deg):
        pass  # commanded, but the rig never actually turns

    from astrodeck.devices.sim import SimRotator
    monkeypatch.setattr(SimRotator, "move_mechanical", frozen_move)

    with pytest.raises(DeviceError, match="too little"):
        await sim_hub.rotator_self_test(step_deg=20.0)
    assert sim_hub._rotation_trusted is None, \
        "a failed measurement must not stick"


async def test_rotate_to_pa_proceeds_when_the_self_test_has_never_run(
        sim_hub):
    """D-05 requires the self-test only "before the first rotating mosaic",
    not before every rotation -- a fresh connect (``_rotation_trusted`` is
    None) must rotate exactly as it did before D-05. Every other test in
    this file that never calls ``rotator_self_test`` already relies on this;
    this one names it."""
    assert sim_hub._rotation_trusted is None
    result = await sim_hub.rotate_to_pa(45.0)
    assert result["rotated"] is True, result


async def test_rotate_to_pa_refuses_after_a_failed_self_test(sim_hub):
    """The gate D-05 asks for: once a self-test has FAILED, rotation is off
    for the night, not just for the attempt that measured it.

    Mutation 'the night-long refusal is removed' (``rotate_to_pa``'s ``if
    self._rotation_trusted is False: raise ...`` deleted) went red here:

        >       with pytest.raises(DeviceError, match="D-05"):
        E       Failed: DID NOT RAISE <class '...DeviceError'>
    """
    sim_hub._rotation_trusted = False

    with pytest.raises(DeviceError, match="D-05") as exc:
        await sim_hub.rotate_to_pa(45.0)
    assert "night" in str(exc.value), exc.value


async def test_goto_and_center_degrades_a_failed_self_test_too(sim_hub):
    """``goto_and_center`` already degrades any rotate failure to
    ``rotation_skipped`` (the sign refusal is "just another one of those,
    not a special case that needs its own plumbing" -- test_w4_rotator_
    sign.py); the self-test refusal above is the same shape again."""
    sim_hub._rotation_trusted = False
    sim_hub.sim_rig.ra_hours, sim_hub.sim_rig.dec_deg = 5.0, 10.0

    result = await sim_hub.goto_and_center(5.0, 10.0, rotation_deg=45.0)

    assert result.get("rotation_skipped") is True, result
