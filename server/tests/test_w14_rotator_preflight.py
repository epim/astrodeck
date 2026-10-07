# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP-88 (#145, #594): the rotator preflight -- something finally CALLS the
sign calibration and the nightly self-test.

``Hub.learn_rotator_sign`` (R-4, #145) and ``Hub.rotator_self_test`` (D-05,
#594) were both built, tested and never called by anything outside the tests.
On a real rig ``connect`` leaves the sign unmeasured, ``rotate_to_pa`` refuses
a rotation while it is, and nothing in the product could measure it: every
automated rotation was refused as "sign not learned" until somebody ran the
calibration by hand, which the product had no way to do.

``Hub.ensure_rotator_ready`` is the trigger. ``goto_and_center`` runs it in its
rotation branch, ONLY when the rotator actually has to move (backlog ruling
for WP-88: the shortcut branch moves nothing, so it must not buy a 22 degree
preflight), and ``POST /api/rotator/preflight`` runs it on the operator's
button. It measures what is unmeasured and nothing else, so it is idempotent
per connect; a teardown resets both values, so the next connect measures
again.

Solves are counted by the KIND of frame the solver was asked to read
(``rotsign`` and ``rotselftest`` are the two preflight kinds), the idiom
``test_rotate_shortcut.py`` uses, so the counts are of real solves made by the
real calibrations against the sim rotator.

MUTANTS RUN (each from a byte backup of hub.py inside this worktree, taken and
restored by scripts/mutate.py, sha256 compared before and after, the mutant text
grepped out afterwards). 26 cases in this file; failures per mutant:

  M1 "preflight call removed from goto_and_center" (the
  ``await self.ensure_rotator_ready()`` line replaced with ``pass``). 4 failed:
  the first-hop case, the second-hop case, the slipping-coupling case and the
  reconnect case.

  M2 "sign re-measured every time" (``if self._rotator_sky_sign is None:`` in
  ``ensure_rotator_ready`` made ``if True:``). 6 failed, among them
  test_a_second_goto_adds_no_preflight_exposures and
  test_ensure_rotator_ready_is_idempotent_and_reports_what_ran.

  M2b "follow test re-run every time" (the twin on ``_rotation_trusted``).
  4 failed: the second-hop, idempotent, concurrent and stays-failed cases.

  M3 "step sign not flipped at the range edge" (``_preflight_step_deg`` returns
  ``step_deg`` where it returned ``-step_deg``). 7 failed: the four sign
  range-edge cases and the three self-test ones; the controls stayed green.

  M4 "preflight hoisted above the shortcut" (``await self.ensure_rotator_ready()``
  inserted before the ``_rotation_already_set`` call). 2 failed:
  test_the_shortcut_branch_runs_no_preflight and
  test_a_goto_with_a_stuck_rotator_degrades_not_aborts (the hoisted call sits
  outside the try that degrades a rotate failure, so a stuck rotator escaped
  ``goto_and_center`` altogether).

  M5 "no lock" (``async with self._rotator_preflight_lock:`` replaced with
  ``if True:``). 1 failed: test_concurrent_preflights_measure_once.

  M6 "the motion fence is not read" (the ``_motion_committed_clean(epoch)``
  check in ``_rotator_calibration_move`` made ``if False:``). 2 failed: the two
  abort cases.

  M7 "a failed self-test raises" (``ensure_rotator_ready`` raises DeviceError
  after a self-test that left ``trusted`` False). 2 failed: the slipping-coupling
  goto case and test_a_failed_self_test_returns_normally_and_stays_failed.

  M8 / M8b "status omits sky_sign / trusted" (one line deleted from the
  rotator block of ``poll_status``). 2 failed each, KeyError on the omitted key.

The failing assertion each produced is recorded verbatim on the test that
caught it.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)

from astrodeck import rotation as _rotation
from astrodeck.config import RotatorConfig, config_store
from astrodeck.devices.base import DeviceError
from astrodeck.devices.sim import SimRotator
from astrodeck.solve.simsolver import SimSolver

#: The goto target the rotation cases use (the field test_goto_rotation.py and
#: test_rotate_shortcut.py use).
RA, DEC = 5.0, 10.0

#: A solve frame's name since #532: ``<kind>-<12 hex digits>.fits``.
_UNIQUE = re.compile(r"^(?P<kind>.+)-[0-9a-f]{12}\.fits$")


def _kind_of(fits_path) -> str:
    name = Path(fits_path).name
    m = _UNIQUE.match(name)
    return f"{m.group('kind')}.fits" if m else name


@pytest.fixture(autouse=True)
def _mount_on_the_target(sim_hub):
    sim_hub.sim_rig.ra_hours = RA
    sim_hub.sim_rig.dec_deg = DEC


@pytest.fixture
def solves(monkeypatch):
    """The kind of file every solver call was asked to solve, in order.
    Delegates to the real ``SimSolver.solve``, so every solve still measures
    the sim rig's true angle."""
    seen: list[str] = []
    real = SimSolver.solve

    async def spy(self, fits_path, **kw):
        seen.append(_kind_of(fits_path))
        return await real(self, fits_path, **kw)

    monkeypatch.setattr(SimSolver, "solve", spy)
    return seen


def _unmeasured(hub) -> None:
    """A real rig's connect: neither the sign nor the follow test is known.
    ``connect_sim`` declares the sign (the simulator's physics are a known
    +1), so the preflight is only exercised once it is forgotten."""
    hub._rotator_sky_sign = None
    hub._rotation_trusted = None


def _preflight_solves(seen: list[str]) -> list[str]:
    return [k for k in seen if k in ("rotsign.fits", "rotselftest.fits")]


# ---------------------------------------------------- the trigger (a), (b)


async def test_goto_on_a_never_measured_rig_learns_the_sign_tests_the_rotator_and_rotates(
        sim_hub, solves):
    """(a) The defect, end to end. A rig that has never measured its sign and
    never run the self-test, asked to goto a target at an angle it must turn
    to: before WP-88 this returned ``rotation_skipped`` ("sign has not been
    learned") because nothing could learn it. Now the first hop measures the
    sign (two solves), runs the follow test (two solves), and rotates.

    Mutation M1 'preflight call removed from goto_and_center' went red here:

        >       assert "rotation_skipped" not in result, result
        E       AssertionError: {'attempts': 2, 'centered': True,
        'error_arcmin': 0.176842248926498, 'rotation': None, ...}
        E       assert 'rotation_skipped' not in {'attempts': 2, 'centered':
        True, 'error_arcmin': 0.176842248926498, 'rotation': None, ...}
    """
    _unmeasured(sim_hub)

    result = await sim_hub.goto_and_center(RA, DEC, rotation_deg=45.0)

    assert "rotation_skipped" not in result, result
    assert result["rotation"]["rotated"] is True, result
    assert sim_hub._rotator_sky_sign == 1
    assert sim_hub._rotation_trusted is True
    pre = _preflight_solves(solves)
    assert pre == ["rotsign.fits"] * 2 + ["rotselftest.fits"] * 2, solves
    # The preflight ran BEFORE the rotate loop it unblocks, not after it.
    assert solves.index("rotate.fits") > max(
        i for i, k in enumerate(solves) if k == "rotselftest.fits"), solves
    truth = (sim_hub.sim_rig.rotator_mech_deg
             + sim_hub.sim_rig.rotator_pa_offset_deg) % 360.0
    assert abs((truth - 45.0 + 90.0) % 180.0 - 90.0) <= 1.0, truth


async def test_a_second_goto_adds_no_preflight_exposures(sim_hub, solves):
    """(b) Idempotent per connect: the second hop of a rotating mosaic (a
    different angle, so the shortcut is not what saves it) buys no preflight
    solve at all. Both values are known after the first.

    Mutation M2 'sign re-measured every time' (``if self._rotator_sky_sign is
    None:`` made ``if True:``) went red here:

        >       assert _preflight_solves(solves) == [], solves
        E       AssertionError: ['rotsign.fits', 'rotsign.fits', 'rotate.fits',
        'rotate.fits', 'solve.fits']
        E       assert ['rotsign.fit...rotsign.fits'] == []
        E         Left contains 2 more items, first extra item: 'rotsign.fits'
    """
    _unmeasured(sim_hub)
    await sim_hub.goto_and_center(RA, DEC, rotation_deg=45.0)
    assert sim_hub._rotator_sky_sign == 1 and sim_hub._rotation_trusted is True
    solves.clear()

    result = await sim_hub.goto_and_center(RA, DEC, rotation_deg=120.0)

    assert result["rotation"]["rotated"] is True, result
    assert _preflight_solves(solves) == [], solves
    assert "rotate.fits" in solves, "the second hop did not rotate at all"


async def test_ensure_rotator_ready_is_idempotent_and_reports_what_ran(
        sim_hub, solves):
    """Both known means no exposure, and the return says what ran.

    Mutation M2 (see above) went red here on the second call:

        >       assert again["ran"] == [], again
        E       AssertionError: {'sign': 1, 'trusted': True, 'ran': ['sign']}
    """
    _unmeasured(sim_hub)

    first = await sim_hub.ensure_rotator_ready()
    assert first == {"sign": 1, "trusted": True,
                     "ran": ["sign", "self_test"]}, first
    n = len(solves)

    again = await sim_hub.ensure_rotator_ready()

    assert again["ran"] == [], again
    assert again["sign"] == 1 and again["trusted"] is True, again
    assert len(solves) == n, "an idempotent call exposed"


async def test_only_the_missing_half_is_measured(sim_hub, solves):
    """The sim declares its sign at connect, so only the follow test is owed:
    the sign is not re-measured just because the other half is."""
    sim_hub._rotation_trusted = None
    assert sim_hub._rotator_sky_sign == 1

    result = await sim_hub.ensure_rotator_ready()

    assert result["ran"] == ["self_test"], result
    assert _preflight_solves(solves) == ["rotselftest.fits"] * 2, solves


async def test_concurrent_preflights_measure_once(sim_hub, solves):
    """The routes and a goto can both ask: two callers at once must produce
    ONE measurement, the second waiting for the first and finding both values
    known. Without the lock both see 'unmeasured' before either finishes and
    the rotator is turned twice.

    Mutation M5 'no lock' (``async with self._rotator_preflight_lock:``
    replaced with ``if True:``) went red here:

        >       assert sorted([a["ran"], b["ran"]]) == [[], ["sign", "self_test"]], (a, b)
        E       AssertionError: ({'ran': ['sign', 'self_test'], 'sign': 1,
        'trusted': True}, {'ran': ['sign', 'self_test'], 'sign': 1,
        'trusted': True})
        E       assert [['sign', 'se... 'self_test']] == [[], ['sign', 'self_test']]
        E         At index 0 diff: ['sign', 'self_test'] != []
    """
    _unmeasured(sim_hub)

    a, b = await asyncio.gather(sim_hub.ensure_rotator_ready(),
                                sim_hub.ensure_rotator_ready())

    assert sorted([a["ran"], b["ran"]]) == [[], ["sign", "self_test"]], (a, b)
    assert _preflight_solves(solves).count("rotsign.fits") == 2, solves
    assert _preflight_solves(solves).count("rotselftest.fits") == 2, solves


# ----------------------------------------------- a failed test (c), errors


async def test_a_slipping_coupling_fails_the_self_test_and_the_goto_degrades(
        sim_hub, solves, bus_lines):
    """(c) 50 degrees of play with the motor at the bottom of it: the +20
    degree step is absorbed and the camera does not move (#594's shape). The
    preflight returns NORMALLY with trusted False (ruling 2), the goto
    degrades to ``rotation_skipped`` through the D-05 refusal, and the line
    that says why names D-05.

    Mutation M7 'a failed self-test raises' went red here:

        >       assert any("D-05" in m for _, m, src in bus_lines
                               if src == "rotator" and "failed" in m), bus_lines
        E       AssertionError: [('info', 'sky angle PA 0.0 ... from the rotator
        self-test solve ...', 'solve'), ...]
        E       assert False
        E        +  where False = any(<generator object ...>)

    (the goto still degrades to ``rotation_skipped``, since it degrades ANY
    rotate failure, but the line that says why no longer names D-05; and on
    ``test_a_failed_self_test_returns_normally_and_stays_failed`` the
    DeviceError escapes: ``astrodeck.devices.base.DeviceError: rotator
    self-test failed``).
    """
    sim_hub._rotation_trusted = None          # the sim already declares +1
    rig = sim_hub.sim_rig
    rig.rotator_backlash_deg = 50.0
    rig.rotator_slack_deg = -25.0

    result = await sim_hub.goto_and_center(RA, DEC, rotation_deg=45.0)

    assert sim_hub._rotation_trusted is False
    assert result.get("rotation_skipped") is True, result
    assert any("D-05" in m for _, m, src in bus_lines
               if src == "rotator" and "failed" in m), bus_lines
    assert _preflight_solves(solves) == ["rotselftest.fits"] * 2, solves


async def test_a_failed_self_test_returns_normally_and_stays_failed(
        sim_hub, solves):
    """A failed self-test is an ANSWER, not an error: ``ensure_rotator_ready``
    returns it so the caller can word it. And it stays False until the rig
    reconnects (``_teardown`` resets it): a second call exposes nothing and
    does not quietly re-test a coupling that already failed."""
    sim_hub._rotation_trusted = None
    rig = sim_hub.sim_rig
    rig.rotator_backlash_deg = 50.0
    rig.rotator_slack_deg = -25.0

    first = await sim_hub.ensure_rotator_ready()
    n = len(solves)
    again = await sim_hub.ensure_rotator_ready()

    assert first["trusted"] is False and first["ran"] == ["self_test"], first
    assert again == {"sign": 1, "trusted": False, "ran": []}, again
    assert len(solves) == n, "a failed rig was re-tested without a reconnect"


async def test_a_failed_sign_measurement_propagates_and_runs_no_self_test(
        sim_hub, solves, monkeypatch):
    """A stuck rotator cannot learn a sign, and the follow test is not run on
    top of it: the DeviceError propagates (rotation stays refused, the caller
    degrades) and neither value is invented."""
    async def frozen_move(self, mech_deg):
        pass  # commanded, but the rig never actually turns

    monkeypatch.setattr(SimRotator, "move_mechanical", frozen_move)
    _unmeasured(sim_hub)

    with pytest.raises(DeviceError, match="too little"):
        await sim_hub.ensure_rotator_ready()

    assert sim_hub._rotator_sky_sign is None
    assert sim_hub._rotation_trusted is None
    assert "rotselftest.fits" not in solves, solves


async def test_a_goto_with_a_stuck_rotator_degrades_not_aborts(
        sim_hub, monkeypatch):
    """The goto still centres: a preflight that cannot measure is one more
    rotate failure, degraded to ``rotation_skipped`` like the sign refusal."""
    async def frozen_move(self, mech_deg):
        pass

    monkeypatch.setattr(SimRotator, "move_mechanical", frozen_move)
    _unmeasured(sim_hub)

    result = await sim_hub.goto_and_center(RA, DEC, rotation_deg=45.0)

    assert result.get("rotation_skipped") is True, result
    assert result["centered"] is True, result


# --------------------------------------------- the shortcut (d), reset (e)


async def test_the_shortcut_branch_runs_no_preflight(sim_hub, solves):
    """(d) The camera already reads the target (calibrated, unmoved), so the
    rotate shortcut answers and NOTHING moves: a 22 degree preflight there
    would turn a rotator that needed no turning. Both values stay unmeasured,
    and no preflight frame is solved.

    Mutation M4 'preflight hoisted above the shortcut' (``await
    self.ensure_rotator_ready()`` inserted before the ``_rotation_already_
    set`` call) went red here:

        >       assert _preflight_solves(solves) == [], solves
        E       AssertionError: ['rotsign.fits', 'rotsign.fits',
        'rotselftest.fits', 'rotselftest.fits', 'rotate.fits', 'rotate.fits',
        ...]
        E       assert ['rotsign.fit...elftest.fits'] == []
        E         Left contains 4 more items, first extra item: 'rotsign.fits'
    """
    rig = sim_hub.sim_rig
    rig.rotator_pa_offset_deg = 20.0
    rig.rotator_mech_deg = 10.0
    await sim_hub.sync_rotator_to_sky()
    assert sim_hub.last_sky_angle["calibrated"] is True
    _unmeasured(sim_hub)
    solves.clear()
    mech_before = rig.rotator_mech_deg

    result = await sim_hub.goto_and_center(RA, DEC, rotation_deg=30.5)

    assert _preflight_solves(solves) == [], solves
    assert result["rotation"]["shortcut"] is True, result
    assert sim_hub._rotator_sky_sign is None
    assert sim_hub._rotation_trusted is None
    assert rig.rotator_mech_deg == pytest.approx(mech_before)


async def test_a_reconnect_resets_both_and_the_next_goto_measures_again(
        sim_hub, solves):
    """(e) Teardown resets both values (a reconnect may bring back a different
    or re-coupled rotator), so the next rotating hop measures again; that is
    why a preflight that is once-per-connect is also once-per-reconnect."""
    _unmeasured(sim_hub)
    await sim_hub.goto_and_center(RA, DEC, rotation_deg=45.0)
    assert sim_hub._rotator_sky_sign == 1 and sim_hub._rotation_trusted is True

    await sim_hub.disconnect_all()
    assert sim_hub._rotator_sky_sign is None
    assert sim_hub._rotation_trusted is None
    await sim_hub.connect_sim()
    sim_hub._rotator_sky_sign = None     # a real rig's connect leaves it so
    sim_hub.sim_rig.ra_hours, sim_hub.sim_rig.dec_deg = RA, DEC
    solves.clear()

    result = await sim_hub.goto_and_center(RA, DEC, rotation_deg=120.0)

    assert result["rotation"]["rotated"] is True, result
    assert _preflight_solves(solves) == (
        ["rotsign.fits"] * 2 + ["rotselftest.fits"] * 2), solves


# ----------------------------------------------------- the range edge (f)


class _HardLimitRotator(SimRotator):
    """A rotator that cannot leave its configured arc: a commanded angle the
    range folds elsewhere is ignored, as a hard-limited driver (the CAA's
    min/max degree limits) refuses it. The plain ``SimRotator`` follows any
    command, so only this one turns 'the step left the arc' into the
    'did not move' the preflight would then report."""

    async def move_mechanical(self, mech_deg: float) -> None:
        rcfg = config_store.cfg().rotator
        want = _rotation.mod360(mech_deg)
        if not _rotation.angle_equals(
                _rotation.target_mechanical_position(
                    want, rcfg.range_type, rcfg.range_start_deg), want, 1e-6):
            self.moves.append(want)       # commanded, and refused by the stop
            return
        await super().move_mechanical(mech_deg)


def _arm_range(sim_hub, range_type, start, mech0) -> _HardLimitRotator:
    config_store.set_rotator(RotatorConfig(range_type=range_type,
                                           range_start_deg=start,
                                           tolerance_deg=1.0))
    rig = sim_hub.sim_rig
    rot = _HardLimitRotator(rig)
    rot.connected = True
    sim_hub.devices["rotator"] = rot
    rig.rotator_mech_deg = mech0
    rig.rotator_pa_offset_deg = 0.0
    return rot


#: (range_type, range_start_deg, the mechanical angle the calibration starts
#: at, the step it is asked for, the first mechanical angle it must command).
#: A half range from 0 sweeps [0, 180), a quarter [0, 90), and the wrapped
#: half from 245 sweeps [245, 65). The first four sit within one step of the
#: END of the sweep, where a +step leaves the arc and must turn the other way;
#: the last two are controls that must not flip.
_SIGN_EDGE_CASES = [
    pytest.param("half", 0.0, 179.0, 2.0, 177.0, id="half-end"),
    pytest.param("quarter", 0.0, 89.0, 2.0, 87.0, id="quarter-end"),
    pytest.param("half", 245.0, 64.0, 2.0, 62.0, id="half-wrapped-start"),
    pytest.param("half", 0.0, 179.9, 2.0, 177.9, id="half-end-fractional"),
    pytest.param("half", 0.0, 90.0, 2.0, 92.0, id="mid-sweep-no-flip"),
    pytest.param("full", 0.0, 359.0, 2.0, 1.0, id="full-range-never-flips"),
]

_TEST_EDGE_CASES = [
    pytest.param("half", 0.0, 170.0, 20.0, 150.0, id="half-end"),
    pytest.param("quarter", 0.0, 80.0, 20.0, 60.0, id="quarter-end"),
    pytest.param("half", 245.0, 60.0, 20.0, 40.0, id="half-wrapped-start"),
    pytest.param("half", 0.0, 90.0, 20.0, 110.0, id="mid-sweep-no-flip"),
    pytest.param("full", 0.0, 350.0, 20.0, 10.0, id="full-range-never-flips"),
]


@pytest.mark.parametrize(
    "range_type,start,mech0,step,first_move", _SIGN_EDGE_CASES)
async def test_the_sign_step_stays_inside_the_allowed_arc(
        sim_hub, range_type, start, mech0, step, first_move):
    """(f) A step that would leave the arc is turned the other way, else a
    hard-limited rotator ignores it and the measurement dies as 'moved too
    little'. The first command stays inside the arc and the sign is learned.

    Mutation M3 'step sign not flipped at the range edge' (``_preflight_step_
    deg`` returns ``step_deg`` unchanged) went red on the four edge cases,
    not on the two controls, e.g. the half-range one:

        >       learned = await sim_hub.learn_rotator_sign(step_deg=step)
        E       astrodeck.devices.base.DeviceError: rotator sign: commanded 2
        degrees but the rotator only moved +0.00 degrees mechanically - too
        little to read a sign from safely; check it is connected and free to turn

    (the hard-limited rotator refused the +2 command that left the arc: the
    'moved too little' death the flip exists to avoid.)
    """
    _unmeasured(sim_hub)
    rot = _arm_range(sim_hub, range_type, start, mech0)

    learned = await sim_hub.learn_rotator_sign(step_deg=step)

    assert rot.moves[0] == pytest.approx(first_move), rot.moves
    assert learned["sign"] == 1, learned
    assert sim_hub._rotator_sky_sign == 1


@pytest.mark.parametrize(
    "range_type,start,mech0,step,first_move", _TEST_EDGE_CASES)
async def test_the_self_test_step_stays_inside_the_allowed_arc(
        sim_hub, range_type, start, mech0, step, first_move):
    """(f) The same fold for the follow test. It reads the fraction off a move
    that really happened, so a command the stop would refuse reads as a failed
    coupling: a healthy rotator at the end of its sweep must PASS.

    Mutation M3 went red on the three edge cases, not the two controls:

        >       result = await sim_hub.rotator_self_test(step_deg=step)
        E       astrodeck.devices.base.DeviceError: rotator self-test: commanded
        20 degrees but the rotator only moved +0.00 degrees mechanically - too
        little to measure safely; check it is connected and free to turn
    """
    _unmeasured(sim_hub)
    rot = _arm_range(sim_hub, range_type, start, mech0)

    result = await sim_hub.rotator_self_test(step_deg=step)

    assert rot.moves[0] == pytest.approx(first_move), rot.moves
    assert result["passed"] is True, result


# ---------------------------------------------------------- the motion fence


async def _abort_on_first_solve(monkeypatch, hub) -> None:
    real = SimSolver.solve
    state = {"n": 0}

    async def solve(self, fits_path, **kw):
        state["n"] += 1
        if state["n"] == 1:
            hub.bump_motion_epoch()      # a STOP lands during the first solve
        return await real(self, fits_path, **kw)

    monkeypatch.setattr(SimSolver, "solve", solve)


async def test_an_abort_before_the_sign_move_sends_no_move(
        sim_hub, monkeypatch):
    """A STOP that lands while the first solve runs must not be followed by a
    22 degree move the operator just cancelled: the calibration reads the
    motion fence immediately before it commands the rotator, as
    ``_approach_rotator_mechanical`` does, and abandons.

    Mutation M6 'the motion fence is not read' went red here:

        >       with pytest.raises(DeviceError, match="abort"):
        E       Failed: DID NOT RAISE <class 'astrodeck.devices.base.DeviceError'>
    """
    _unmeasured(sim_hub)
    rot = sim_hub.devices["rotator"]
    rot.moves.clear()
    await _abort_on_first_solve(monkeypatch, sim_hub)

    with pytest.raises(DeviceError, match="abort"):
        await sim_hub.learn_rotator_sign(step_deg=2.0)

    assert rot.moves == [], rot.moves
    assert sim_hub._rotator_sky_sign is None


async def test_an_abort_before_the_self_test_move_sends_no_move(
        sim_hub, monkeypatch):
    """The same fence on the follow test, and an aborted test says nothing
    about the coupling: ``_rotation_trusted`` is left as it was.

    Mutation M6 went red here on the same "DID NOT RAISE"."""
    _unmeasured(sim_hub)
    rot = sim_hub.devices["rotator"]
    rot.moves.clear()
    await _abort_on_first_solve(monkeypatch, sim_hub)

    with pytest.raises(DeviceError, match="abort"):
        await sim_hub.rotator_self_test(step_deg=20.0)

    assert rot.moves == [], rot.moves
    assert sim_hub._rotation_trusted is None


# ------------------------------------------------------------- status block


async def test_status_publishes_the_sign_and_the_trust(sim_hub):
    """Ruling 4: ``rotator.sky_sign`` (1, -1 or null) and ``rotator.trusted``
    (true, false or null), so the panel can say what the rig knows. Null means
    'not measured', which is not the same as False ('measured, and failed').

    Mutation 'status omits the keys' (the two lines deleted) went red here:

        >       assert block["sky_sign"] is None, block
        E       KeyError: 'sky_sign'
    """
    _unmeasured(sim_hub)
    block = (await sim_hub.poll_status())["rotator"]
    assert block["sky_sign"] is None, block
    assert block["trusted"] is None, block

    sim_hub._rotator_sky_sign = -1
    sim_hub._rotation_trusted = True
    block = (await sim_hub.poll_status())["rotator"]
    assert block["sky_sign"] == -1 and block["trusted"] is True, block

    sim_hub._rotation_trusted = False
    block = (await sim_hub.poll_status())["rotator"]
    assert block["trusted"] is False, block


async def test_status_follows_a_real_preflight(sim_hub):
    """The published values are the hub's own state after the preflight, not a
    copy that could drift: measured through the real calibrations."""
    _unmeasured(sim_hub)
    await sim_hub.ensure_rotator_ready()

    block = (await sim_hub.poll_status())["rotator"]

    assert block["sky_sign"] == 1 and block["trusted"] is True, block
