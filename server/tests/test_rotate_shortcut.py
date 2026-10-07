# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The hub's rotate shortcut (#189 U-06; mosaic spec 5.6 step 3 and 5.7).

``goto_and_center`` rotates before it centres, and until this shortcut the
rotate loop always paid for at least one solve, exposure included, even when
the camera was already at the angle. On a rotating mosaic that is one rotate
solve per hop, every hop of the night, for an angle that has not changed since
the last panel: the centring solve at the end of every hop calibrates the
rotator (``sky_angle.note_solved_rotation``), and nothing turns it between
panels.

The shortcut skips the rotate solve when all three hold:

* the rotator is CALIBRATED: the newest sky-angle record (``hub.last_sky_angle``)
  says calibrated, and it is this rotator's calibration (the rotator object is
  synced, and holds the offset that record wrote);
* it has NOT MOVED since: its mechanical angle is within
  ``sky_angle.MOVED_TOL_DEG`` of the one that calibration was taken at;
* it already READS the target within ``RotatorConfig.tolerance_deg``, MOD 180.
  A centred rectangle turned half a turn covers the same sky (5.7), and a
  meridian flip turns the field 180 degrees under a rotator that never moved,
  so without the mod the shortcut would miss every hop after a flip.

Every case counts the SOLVER'S calls on the simulator, told apart by the file
each path writes (``_solve/rotsync-<token>.fits`` for the calibration below,
``_solve/rotate-<token>.fits`` for the rotate loop, ``_solve/solve-<token>.fits``
for the centring). The count is of real solves made by the real rotate loop
against the sim rotator, not of calls to a double.

RE-PINNED FOR #532 (H4). Every solve frame now has a name of its own, so the
spy records each call by its KIND, the name with the token taken out
(``rotate-4c1297bee2b9.fits`` is recorded as ``rotate.fits``). The counts, and
the failures recorded below from before the change, read exactly as they did.
Left counting the raw names, the counts of zero would have passed whatever the
shortcut did; ``test_h4_solve_frame_unique_names.py`` pins the names
themselves.

THE CONTROLS' COUNTS ARE COMPUTED, and one differs from the task's shorthand.
When a single condition fails, the real rotate loop runs. With the camera
truly within tolerance it converges on its first solve (moved, moving,
uncalibrated, one rotator swapped for another: one solve each). With the
camera truly outside tolerance it must solve, move and solve again to verify
(outside tolerance, a tighter configured tolerance, and the re-seated camera:
two). "One solve each" holds for every
control whose camera is at the angle; outside tolerance cannot be one, because
the loop does not trust a move it has not measured.

Every mutation named below was run in a private scratch copy of ``server/``
(issue #254), never in the shared tree. The failure each produced is recorded
verbatim on the test that caught it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)

from astrodeck import sky_angle
from astrodeck.config import RotatorConfig, config_store
from astrodeck.devices.sim import SimRotator
from astrodeck.solve.base import SolveResult
from astrodeck.solve.simsolver import SimSolver

#: The goto target every case uses (the field test_goto_rotation.py uses).
RA, DEC = 5.0, 10.0


@pytest.fixture(autouse=True)
def _mount_on_the_target(sim_hub):
    """Start the sim mount on the goto target, as test_rotation_unavailable.py
    does: every slew still happens, and nothing graded here depends on how far
    the mount travelled.

    RE-PINNED FOR WP-88 (#145, wave 14 integration): the sim rotator's
    coupling is declared known-good (``_rotation_trusted = True``, as
    ``connect_sim`` already declares the sign), so the rotator preflight is a
    no-op here. These cases grade the shortcut's three conditions, and the
    first-hop self-test would leave the camera 20 degrees off and make every
    one of them solve twice."""
    sim_hub.sim_rig.ra_hours = RA
    sim_hub.sim_rig.dec_deg = DEC
    sim_hub._rotation_trusted = True


#: A solve frame's name since #532: ``<kind>-<12 hex digits>.fits``.
_UNIQUE = re.compile(r"^(?P<kind>.+)-[0-9a-f]{12}\.fits$")


def _kind_of(fits_path) -> str:
    """The frame's kind as a file name, ``rotate.fits`` for
    ``rotate-4c1297bee2b9.fits``: the token taken out, so the counts below
    count paths, not names. A name without a token is kept as it is."""
    name = Path(fits_path).name
    m = _UNIQUE.match(name)
    return f"{m.group('kind')}.fits" if m else name


@pytest.fixture
def solves(monkeypatch):
    """The kind of file every solver call was asked to solve, in order (see
    ``_kind_of``). Delegates to the real ``SimSolver.solve``, so every solve
    still measures the sim rig's true angle."""
    seen: list[str] = []
    real = SimSolver.solve

    async def spy(self, fits_path, **kw):
        seen.append(_kind_of(fits_path))
        return await real(self, fits_path, **kw)

    monkeypatch.setattr(SimSolver, "solve", spy)
    return seen


def _rotate_solves(seen: list[str]) -> int:
    return seen.count("rotate.fits")


async def _calibrate(hub, *, offset_deg: float, mech_deg: float) -> dict:
    """Put the sim camera at ``mech + offset`` true PA and calibrate the
    rotator from a real solve (the ``rotator sync`` path), as any centring
    solve would. Returns the calibration record, asserted calibrated so a
    case that means to start calibrated cannot start otherwise."""
    rig = hub.sim_rig
    rig.rotator_pa_offset_deg = offset_deg
    rig.rotator_mech_deg = mech_deg
    await hub.sync_rotator_to_sky()
    rec = hub.last_sky_angle
    assert rec is not None and rec["calibrated"] is True, rec
    return rec


# --- the shortcut ----------------------------------------------------------


async def test_calibrated_unmoved_and_at_the_angle_skips_the_rotate_solve(
        sim_hub, solves):
    """All three hold: the camera reads PA 30.0 after a calibration, nothing
    has turned it, and the target is 30.5 with a 1.0 degree tolerance.

    Mutation 'no shortcut' (``_rotation_already_set`` returns ``None`` at its
    top, so the rotate loop always runs) went red here, and on the twin case
    and the moved-within-jitter case, on the same assertion:

        >       assert _rotate_solves(solves) == 0, solves
        E       AssertionError: ['rotate.fits', 'solve.fits', 'solve.fits']
        E       assert 1 == 0
    """
    await _calibrate(sim_hub, offset_deg=20.0, mech_deg=10.0)
    solves.clear()
    mech_before = sim_hub.sim_rig.rotator_mech_deg

    result = await sim_hub.goto_and_center(RA, DEC, rotation_deg=30.5)

    assert _rotate_solves(solves) == 0, solves
    # The centring still ran and still solved: only the rotate solve is saved.
    assert result["centered"] is True, result
    assert "solve.fits" in solves, solves
    assert sim_hub.sim_rig.rotator_mech_deg == pytest.approx(mech_before)
    rot = result["rotation"]
    assert rot["rotated"] is True and rot["shortcut"] is True, rot
    assert rot["attempts"] == 0, rot
    assert rot["pa_deg"] == pytest.approx(30.0, abs=0.01), rot
    assert rot["error_deg"] == pytest.approx(0.5, abs=0.01), rot
    # In words: what made a solve unnecessary, with the numbers.
    assert "calibrated" in rot["reason"] and "not moved" in rot["reason"], rot
    assert "30.0" in rot["reason"] and "30.5" in rot["reason"], rot
    assert "rotation_skipped" not in result
    assert "rotation_unavailable" not in result


async def test_the_half_turn_twin_takes_the_shortcut(sim_hub, solves):
    """The camera reads PA 30.0 and the target is 210.5: the same footprint
    turned 180 degrees, 0.5 degrees off. It is what every hop after a meridian
    flip looks like, since the flip turns the field under a rotator that never
    moved, and 5.7 says the rotator is never turned 180 degrees for it.

    Mutation 'no mod 180' (``angle_equals`` in place of
    ``angle_equals_mod180`` in the tolerance check) went red here alone; the
    straight case stayed green:

        >       assert _rotate_solves(solves) == 0, solves
        E       AssertionError: ['rotate.fits', 'solve.fits', 'solve.fits']
        E       assert 1 == 0
    """
    await _calibrate(sim_hub, offset_deg=20.0, mech_deg=10.0)
    solves.clear()
    mech_before = sim_hub.sim_rig.rotator_mech_deg

    result = await sim_hub.goto_and_center(RA, DEC, rotation_deg=210.5)

    assert _rotate_solves(solves) == 0, solves
    assert result["rotation"]["shortcut"] is True, result
    assert result["rotation"]["error_deg"] == pytest.approx(0.5, abs=0.01)
    assert sim_hub.sim_rig.rotator_mech_deg == pytest.approx(mech_before)


async def test_read_back_jitter_below_the_moved_tolerance_still_counts_as_unmoved(
        sim_hub, solves):
    """A rotator at rest reads back a hair off the angle it was calibrated at.
    Anything within ``sky_angle.MOVED_TOL_DEG`` (0.1) is "not moved", the
    same line the calibration itself draws, so a 0.05 degree wobble keeps the
    shortcut.

    Mutation 'exact mechanical match' (``moved <= 0.0`` in place of
    ``moved <= _sky_angle.MOVED_TOL_DEG``) went red here alone:

        >       assert _rotate_solves(solves) == 0, solves
        E       AssertionError: ['rotate.fits', 'solve.fits', 'solve.fits']
        E       assert 1 == 0
    """
    await _calibrate(sim_hub, offset_deg=20.0, mech_deg=10.0)
    solves.clear()
    sim_hub.sim_rig.rotator_mech_deg = 10.05

    result = await sim_hub.goto_and_center(RA, DEC, rotation_deg=30.5)

    assert _rotate_solves(solves) == 0, solves
    assert result["rotation"]["shortcut"] is True, result


# --- the controls: one condition fails, the rotate loop runs ----------------


async def test_moved_since_the_calibration_solves(sim_hub, solves):
    """Calibrated, and the camera still reads within tolerance of the target
    (30.5 against 30.5), but the rotator has turned 0.5 degrees since its
    calibration. A move is exactly when the reading stops being evidence, so
    the loop measures. The camera is truly at the angle: one solve.

    Mutation 'ignore moved' (the mechanical comparison removed) went red here
    and on the NaN case:

        >       assert _rotate_solves(solves) == 1, solves
        E       AssertionError: ['solve.fits', 'solve.fits']
        E       assert 0 == 1
    """
    await _calibrate(sim_hub, offset_deg=20.0, mech_deg=10.0)
    solves.clear()
    sim_hub.sim_rig.rotator_mech_deg = 10.5

    result = await sim_hub.goto_and_center(RA, DEC, rotation_deg=30.5)

    assert _rotate_solves(solves) == 1, solves
    assert "shortcut" not in result["rotation"], result
    assert result["rotation"]["rotated"] is True, result


async def test_a_rotator_reporting_motion_solves(sim_hub, solves):
    """Calibrated, at the calibrated angle, within tolerance, but the rotator
    says it is moving. A reading taken mid-move is not where it will stop, so
    the loop runs. Here the loop itself then refuses to calibrate from a frame
    exposed during motion and degrades to ``rotation_skipped``, which is the
    loop's own answer; the point is that it ran (one rotate solve).

    Mutation 'ignore motion' (the ``is_moving`` test removed) went red here
    alone:

        >       assert _rotate_solves(solves) == 1, solves
        E       AssertionError: ['solve.fits', 'solve.fits']
        E       assert 0 == 1
    """
    await _calibrate(sim_hub, offset_deg=20.0, mech_deg=10.0)
    sim_hub.devices["rotator"]._moving = True
    solves.clear()

    result = await sim_hub.goto_and_center(RA, DEC, rotation_deg=30.5)

    assert _rotate_solves(solves) == 1, solves
    assert result.get("rotation_skipped") is True, result


async def test_never_calibrated_solves(sim_hub, solves):
    """No sky angle has been recorded at all. The rotator reads PA 30 (offset
    0 on a camera whose true PA is 30), within tolerance of 30.5, and has not
    moved; but nothing has ever tied its reading to the sky. One solve.

    This case needs no mutant of its own: with no record there is nothing to
    read, and every mutant that drops a check still returns early on it. The
    two calibration mutants are caught by the next two cases.
    """
    rig = sim_hub.sim_rig
    rig.rotator_pa_offset_deg = 0.0
    rig.rotator_mech_deg = 30.0
    assert sim_hub.last_sky_angle is None
    assert await sim_hub.devices["rotator"].get_position() == pytest.approx(30.0)

    result = await sim_hub.goto_and_center(RA, DEC, rotation_deg=30.5)

    assert _rotate_solves(solves) == 1, solves
    assert "shortcut" not in result["rotation"], result


async def test_a_newest_record_that_refused_to_calibrate_solves(sim_hub, solves):
    """The rotator WAS calibrated, and then a newer solve could not calibrate
    it (here: the rotator reported motion when that frame was exposed). The
    newest record is the one that decides, and it says not calibrated.

    Mutation 'ignore the calibrated flag' (the ``rec.get("calibrated")`` test
    removed) SURVIVES this case, and that was expected: ``sky_angle`` writes
    ``offset_deg`` only on a calibration, so this real refused record carries
    ``None`` there and the offset comparison turns it away as well. The next
    case pins the flag on its own and went red under that mutation.
    """
    await _calibrate(sim_hub, offset_deg=20.0, mech_deg=10.0)
    cam = sim_hub.devices["camera"]
    ctx = await sky_angle.exposure_context(sim_hub, cam)
    moving = sky_angle.ExposureAngle(
        at=ctx.at, camera=ctx.camera, rotator=ctx.rotator,
        mech_deg=ctx.mech_deg, moving=True, pier_side=ctx.pier_side)
    rec = await sky_angle.note_solved_rotation(
        sim_hub, SolveResult(True, ra_hours=RA, dec_deg=DEC, rotation_deg=30.0),
        source="a test solve", context=moving)
    assert rec is sim_hub.last_sky_angle and rec["calibrated"] is False, rec
    solves.clear()

    result = await sim_hub.goto_and_center(RA, DEC, rotation_deg=30.5)

    assert _rotate_solves(solves) == 1, solves
    assert "shortcut" not in result["rotation"], result


async def test_a_record_that_says_not_calibrated_is_not_a_calibration(
        sim_hub, solves):
    """The flag is the contract, whatever else a record carries. A refused
    record from ``sky_angle`` today has no offset, so the case above is also
    turned away by the offset comparison, and a mutant dropping the flag test
    survives it (recorded there). This case pins the flag alone: a record
    that says ``calibrated: False`` while carrying this rotator's own offset
    and mechanical angle, so nothing but the flag refuses it. It is the record
    a refactor of ``sky_angle`` that wrote the offset before deciding would
    produce.

    Mutation 'ignore the calibrated flag' went red here alone:

        >       assert _rotate_solves(solves) == 1, solves
        E       AssertionError: ['solve.fits', 'solve.fits']
        E       assert 0 == 1
    """
    rec = await _calibrate(sim_hub, offset_deg=20.0, mech_deg=10.0)
    sim_hub.last_sky_angle = dict(rec, calibrated=False,
                                  reason="refused, for the test")
    solves.clear()

    result = await sim_hub.goto_and_center(RA, DEC, rotation_deg=30.5)

    assert _rotate_solves(solves) == 1, solves
    assert "shortcut" not in result["rotation"], result


async def test_a_calibration_of_another_rotator_object_solves(sim_hub, solves):
    """The record is calibrated, but by the rotator object this hub held
    before a reconnect; the one in the rig now has never been synced. Rigged
    so that nothing ELSE tells them apart: the true PA is the mechanical angle
    (offset 0), so the calibration wrote offset 0, the fresh object's default
    offset is also 0, and it reads 30.0, unmoved, within tolerance.

    Mutation 'no identity check' (the ``synced`` test and the offset
    comparison both removed) went red here and on the re-seated case, and
    mutation 'offset is enough' (the ``synced`` test alone removed) went red
    here alone, both on:

        >       assert _rotate_solves(solves) == 1, solves
        E       AssertionError: ['solve.fits', 'solve.fits']
        E       assert 0 == 1
    """
    rec = await _calibrate(sim_hub, offset_deg=0.0, mech_deg=30.0)
    assert rec["offset_deg"] == pytest.approx(0.0)
    fresh = SimRotator(sim_hub.sim_rig)
    await fresh.connect()
    sim_hub.devices["rotator"] = fresh
    assert fresh.synced is False and fresh.sync_offset_deg == 0.0
    solves.clear()

    result = await sim_hub.goto_and_center(RA, DEC, rotation_deg=30.5)

    assert _rotate_solves(solves) == 1, solves
    assert "shortcut" not in result["rotation"], result


async def test_a_synced_rotator_whose_offset_is_not_the_newest_calibration_solves(
        sim_hub, solves):
    """Rotator A is calibrated; then rotator B takes its place and is
    calibrated after the camera was re-seated five degrees round in its clamp;
    then A is put back. A is synced and unmoved and reads 30.0, but the newest
    calibration is B's, with B's offset, and it says the camera is really at
    35.0. The loop measures: 4.5 degrees off, so solve, move, verify (two).

    Mutation 'synced is enough' (the offset comparison removed, the ``synced``
    test kept) went red here alone, as did 'no identity check':

        >       assert _rotate_solves(solves) == 2, solves
        E       AssertionError: ['solve.fits', 'solve.fits']
        E       assert 0 == 2
    """
    await _calibrate(sim_hub, offset_deg=20.0, mech_deg=10.0)
    a = sim_hub.devices["rotator"]
    b = SimRotator(sim_hub.sim_rig)
    await b.connect()
    sim_hub.devices["rotator"] = b
    rec_b = await _calibrate(sim_hub, offset_deg=25.0, mech_deg=10.0)
    sim_hub.devices["rotator"] = a
    assert a.synced and await a.get_position() == pytest.approx(30.0)
    assert rec_b["offset_deg"] != pytest.approx(a.sync_offset_deg)
    solves.clear()

    result = await sim_hub.goto_and_center(RA, DEC, rotation_deg=30.5)

    assert _rotate_solves(solves) == 2, solves
    assert "shortcut" not in result["rotation"], result


async def test_a_record_with_no_finite_mechanical_angle_solves(sim_hub, solves):
    """A calibration record whose mechanical angle is NaN ties the reading to
    nothing. ``nan > tol`` is False, so a check written "return when moved
    beyond tolerance" reads NaN as unmoved; this one must fail closed.

    Mutation 'NaN passes' (``if moved > _sky_angle.MOVED_TOL_DEG`` in place
    of ``if not moved <= _sky_angle.MOVED_TOL_DEG``) went red here alone, and
    so did 'ignore moved':

        >       assert _rotate_solves(solves) == 1, solves
        E       AssertionError: ['solve.fits', 'solve.fits']
        E       assert 0 == 1
    """
    rec = await _calibrate(sim_hub, offset_deg=20.0, mech_deg=10.0)
    sim_hub.last_sky_angle = dict(rec, mechanical_deg=float("nan"))
    solves.clear()

    result = await sim_hub.goto_and_center(RA, DEC, rotation_deg=30.5)

    assert _rotate_solves(solves) == 1, solves
    assert "shortcut" not in result["rotation"], result


async def test_outside_tolerance_solves(sim_hub, solves):
    """Calibrated and unmoved, but the camera reads 30.0 and the target is
    35.0, beyond the 1.0 degree tolerance. The loop solves, moves and solves
    again to verify: two.

    Mutation 'ignore tolerance' (the ``angle_equals_mod180`` test removed)
    went red here and on the rotator-config case:

        >       assert _rotate_solves(solves) == 2, solves
        E       AssertionError: ['solve.fits', 'solve.fits']
        E       assert 0 == 2
    """
    await _calibrate(sim_hub, offset_deg=20.0, mech_deg=10.0)
    solves.clear()

    result = await sim_hub.goto_and_center(RA, DEC, rotation_deg=35.0)

    assert _rotate_solves(solves) == 2, solves
    assert "shortcut" not in result["rotation"], result
    truth = (sim_hub.sim_rig.rotator_mech_deg
             + sim_hub.sim_rig.rotator_pa_offset_deg) % 360.0
    assert abs(((truth - 35.0) + 90.0) % 180.0 - 90.0) <= 1.0, truth


async def test_the_tolerance_is_the_rotator_config(sim_hub, solves):
    """The shortcut works to the rotate loop's own bound,
    ``RotatorConfig.tolerance_deg``, not a number of its own. At 0.25 degrees
    a camera 0.5 off is outside it, so the same case as the first test now
    solves (the camera is truly 0.5 off, so solve, move, verify: two).

    Mutation 'fixed 1 degree' (``tol = 1.0`` in place of the configured
    ``tolerance_deg``) went red here alone:

        >       assert _rotate_solves(solves) == 2, solves
        E       AssertionError: ['rotsync.fits', 'solve.fits', 'solve.fits']
        E       assert 0 == 2
    """
    config_store.set_rotator(RotatorConfig(tolerance_deg=0.25))
    await _calibrate(sim_hub, offset_deg=20.0, mech_deg=10.0)

    result = await sim_hub.goto_and_center(RA, DEC, rotation_deg=30.5)

    assert _rotate_solves(solves) == 2, solves
    assert "shortcut" not in result["rotation"], result
