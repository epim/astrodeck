# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""R-4 (#145): the learned sky/mechanical sign.

Every rotator move before R-4 assumed ``sky = mech - offset`` (sign +1: sky
and mechanical angle move together). Measured on the rig, pier west: they run
opposite, one for one (15 small steps, every one agreeing, issue #145). A
rotation built on the wrong sign does not just mis-size its correction, it
REVERSES it -- the 2026-08-08 incident that spun a camera through more than a
full revolution over five rotate attempts while the error GREW on every move.

This file covers three things, from the backlog plan's WP-32a (c):

* the pure conversion (``rotation.sky_to_mechanical`` / ``mechanical_to_sky``
  / ``map_sky_target``) actually applies whichever sign it is given, rather
  than quietly assuming +1;
* ``Hub.rotate_to_pa`` REFUSES outright while the sign is unmeasured
  (``Hub._rotator_sky_sign`` is None) -- guessing could turn the camera the
  wrong way;
* ``Hub.learn_rotator_sign`` measures it correctly under BOTH signs: +1 from
  the simulator's own (otherwise hardcoded, #145) physics, and -1 from a
  scripted solver that stands in for a rig whose train runs the other way
  (the simulator's solver cannot itself produce a -1 relationship --
  ``SimSolver.solve`` hardcodes ``rotation_deg = rotator_mech_deg +
  rotator_pa_offset_deg`` -- so a -1 rig is modelled by scripting the solve,
  exactly as ``test_rotate_to_pa_convergence.py`` scripts the sky for other
  purposes).

Every mutation named below was run in a private scratch copy of ``server/``
(issue #254, #475), never in the shared tree. The failure each produced is
recorded verbatim on the test that caught it.
"""
from __future__ import annotations

import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)

from astrodeck.devices.base import DeviceError
from astrodeck.hub import Hub
from astrodeck.rotation import (
    map_sky_target,
    mechanical_to_sky,
    sky_to_mechanical,
)


# -------------------------------------------------------- the pure math


def test_sky_to_mechanical_sign_minus_one_disagrees_with_plus_one():
    """Same inputs, opposite sign, different answers -- the baseline fact a
    'sign forced to +1' mutant erases.

    Mutation 'sign forced to +1' (``_rotate_to_pa_attempts``'s
    ``sky_sign=sign`` call into ``map_sky_target`` hand-edited back to the
    pre-R-4 call with no ``sky_sign``, which defaults to +1) cannot be run
    against this test directly -- it is a pure-function test with no hub --
    but the SAME wrong answer (30.0 where -1 needs 50.0) is exactly what a
    hub that silently used +1 regardless of what it measured would compute
    for its first move. See ``test_rotate_to_pa_converges_under_a_negative_
    sign`` below for the hub-level version of this same mutant, where it
    does go red.
    """
    assert sky_to_mechanical(20.0, 40.0, 10.0, sky_sign=1) == pytest.approx(30.0)
    assert sky_to_mechanical(20.0, 40.0, 10.0, sky_sign=-1) == pytest.approx(50.0)


def test_mechanical_to_sky_is_the_inverse_for_either_sign():
    """Round-trips through the SAME anchor, so the conversion commands a
    move to the sky angle actually asked for, not its mirror."""
    for sign in (1, -1):
        mech = sky_to_mechanical(123.4, 40.0, 10.0, sky_sign=sign)
        back = mechanical_to_sky(mech, 40.0, 10.0, sky_sign=sign)
        assert back == pytest.approx(123.4), (sign, mech, back)


def test_map_sky_target_default_sign_is_unchanged_for_pre_r4_callers():
    """This module's own pre-R-4 tests (test_rotation.py) call
    ``map_sky_target`` with no ``sky_sign`` at all, so the default must keep
    reading exactly as before R-4.

    (#671 part 2: api/app.py's manual-move route used to be the other caller
    that left ``sky_sign`` at its default -- WP-53 (#589, #626) changed that,
    and it now passes ``sky_sign=hub._effective_rotator_sign()``, anchored on
    ``Hub._rotator_sync_anchor``, so a measured sign reaches it too. The
    default this test pins is kept for the case ``_effective_rotator_sign``
    itself falls back to: an UNMEASURED rotator, where +1 is still what
    every rotator before R-4 assumed.)"""
    assert map_sky_target(120.0, 78.5, 30.0, "full", 0.0) == pytest.approx(120.0)
    assert map_sky_target(100.0, 0.0, 40.0, "half", 245.0) == pytest.approx(280.0)


def test_map_sky_target_sign_changes_which_mechanical_target_is_reached():
    """The same desired sky PA maps to a DIFFERENT mechanical target
    depending on the sign: sign +1 needs the half-range fold here (lands on
    the 180-degree twin, at 280) and sign -1 does not -- its direct
    mechanical target (250) is already inside the range, so the sky target
    comes back UNCHANGED. A sign silently forced to +1 would answer 280.0
    for both."""
    assert map_sky_target(100.0, 15.0, 40.0, "half", 245.0,
                          sky_sign=1) == pytest.approx(280.0)
    assert map_sky_target(100.0, 15.0, 40.0, "half", 245.0,
                          sky_sign=-1) == pytest.approx(100.0)


# --------------------------------------------- the hub refuses until learned


async def test_rotate_to_pa_refuses_until_the_sign_is_learned(sim_hub):
    """``connect_sim`` sets the sign immediately (its physics are a KNOWN
    +1, not a guess); forcing it back to None here is standing in for a real
    rig's connect, which leaves it unmeasured on purpose (R-4, #145).

    Mutation 'refusal removed' (``rotate_to_pa``'s ``if sign is None:``
    changed to ``if False:``, the raise now dead code) went red here — not
    with "did not raise", but with the WRONG exception: ``sign`` (still
    None) reaches the pure math uncontrolled and blows up deep inside the
    loop instead of being refused up front, which is the exact "guessing
    could turn the camera the wrong way" this refusal exists to prevent:

        >       await sim_hub.rotate_to_pa(120.0)
        ...
        astrodeck\\rotation.py:107: in sky_to_mechanical
        >       return mod360(mech_pos + sky_sign * (sky_deg - sky_now))
        E       TypeError: unsupported operand type(s) for *: 'NoneType' and
        'float'
    """
    sim_hub._rotator_sky_sign = None
    with pytest.raises(DeviceError, match="sign"):
        await sim_hub.rotate_to_pa(120.0)


async def test_the_refusal_names_r4_and_never_guesses(sim_hub):
    """The line a human reads has to say WHY, not just that it failed
    (project style: a refusal with no reason is as bad as a silent one).

    Mutation 'refusal removed' (see the previous test) went red here too, on
    the same uncontrolled ``TypeError`` in place of the intended
    ``DeviceError`` — ``pytest.raises(DeviceError)`` does not catch it:

        >       with pytest.raises(DeviceError) as exc:
        E       TypeError: unsupported operand type(s) for *: 'NoneType' and
        'float'
    """
    sim_hub._rotator_sky_sign = None
    with pytest.raises(DeviceError) as exc:
        await sim_hub.rotate_to_pa(120.0)
    msg = str(exc.value)
    assert "has not been learned" in msg, msg
    assert "145" in msg, msg


async def test_goto_and_center_degrades_when_the_sign_is_unknown(
        sim_hub, monkeypatch):
    """``goto_and_center`` already degrades any rotate failure to
    ``rotation_skipped`` (solve_and_sync parity) -- the sign refusal is just
    another one of those, not a special case that needs its own plumbing.

    RE-PINNED FOR WP-88 (#145, wave 14 integration). Before the rotator
    preflight, an unlearned sign reached ``rotate_to_pa`` and was refused
    there, so this graded the degrade path by leaving the sign None. The
    goto now calls ``Hub.ensure_rotator_ready`` first, which LEARNS the sign
    on the sim and rotates (that is the #145 fix), so a None sign no longer
    reaches the refusal. To keep grading the refusal's degrade path the
    preflight is stubbed to an async no-op that learns nothing, the state of
    a preflight that ran and could not measure (``sign`` None). The
    assertion is the one this case always made.
    """
    async def _no_preflight(self, **kw):
        return {"sign": None, "trusted": None, "ran": []}

    monkeypatch.setattr(Hub, "ensure_rotator_ready", _no_preflight)
    sim_hub._rotator_sky_sign = None
    sim_hub.sim_rig.ra_hours, sim_hub.sim_rig.dec_deg = 5.0, 10.0
    result = await sim_hub.goto_and_center(5.0, 10.0, rotation_deg=90.0)
    assert result.get("rotation_skipped") is True, result


# -------------------------------------------------- learning the sign


def _signed_solver(monkeypatch, sign: int, const_deg: float = 0.0):
    """Stand in for a rig whose optical train has the given sign: the solve
    reports sky PA = ``sign * mech + const_deg`` (mod 360), read LIVE off the
    sim rig's own mechanical angle, so it tracks real moves ``learn_rotator_
    sign`` and ``rotate_to_pa`` command during the test -- not a fixed list
    of answers, the way ``test_h4_rotator_backlash.py``'s ``_scripted_pas``
    scripts the sky for a different purpose (there the loop's OWN math is
    under test and the sky must not react to its moves; here the sign
    relationship itself is what is under test, so it must).

    ``SimSolver.solve`` cannot itself be configured this way -- it hardcodes
    ``rotation_deg = rotator_mech_deg + rotator_pa_offset_deg`` (sign +1,
    #145) -- so this patches the solve directly, exactly as the sim's own
    test suite already does to script other things the simulator's fixed
    physics cannot represent."""
    from astrodeck.solve.base import SolveResult
    from astrodeck.solve.simsolver import SimSolver

    async def solve(self, fits_path, **kw):
        mech = self.sim_rig.rotator_mech_deg
        pa = (sign * mech + const_deg) % 360.0
        return SolveResult(True, ra_hours=self.sim_rig.ra_hours,
                           dec_deg=self.sim_rig.dec_deg, rotation_deg=pa,
                           message="scripted sign")

    monkeypatch.setattr(SimSolver, "solve", solve)


async def test_learn_rotator_sign_measures_plus_one_from_the_sim(sim_hub):
    """Control: the simulator's own (unscripted) physics ARE sign +1
    (#145) -- ``learn_rotator_sign`` must read that back, not just repeat
    whatever ``connect_sim`` already set."""
    sim_hub._rotator_sky_sign = None            # as if freshly, unmeasured
    sim_hub.sim_rig.rotator_mech_deg = 40.0
    sim_hub.sim_rig.rotator_pa_offset_deg = 12.0

    learned = await sim_hub.learn_rotator_sign(step_deg=2.0)

    assert learned["sign"] == 1, learned
    assert sim_hub._rotator_sky_sign == 1
    assert learned["mechanical_travel_deg"] == pytest.approx(2.0, abs=0.01)
    assert learned["sky_travel_deg"] == pytest.approx(2.0, abs=0.01)


async def test_learn_rotator_sign_measures_minus_one(sim_hub, monkeypatch):
    """The #145 measurement itself, reproduced: a train that runs the sky
    PA opposite to the mechanical angle must be learned as -1, not +1."""
    _signed_solver(monkeypatch, -1, const_deg=50.0)
    sim_hub._rotator_sky_sign = None
    sim_hub.sim_rig.rotator_mech_deg = 40.0

    learned = await sim_hub.learn_rotator_sign(step_deg=2.0)

    assert learned["sign"] == -1, learned
    assert sim_hub._rotator_sky_sign == -1
    assert learned["mechanical_travel_deg"] == pytest.approx(2.0, abs=0.01)
    assert learned["sky_travel_deg"] == pytest.approx(-2.0, abs=0.01)


async def test_learn_rotator_sign_refuses_on_a_rotator_that_did_not_move(
        sim_hub, monkeypatch):
    """A stuck or disconnected rotator must not silently 'learn' a sign from
    solve noise: with no real travel, there is nothing to read a sign from."""
    async def frozen_move(self, mech_deg):
        pass  # commanded, but the rig never actually turns

    from astrodeck.devices.sim import SimRotator
    monkeypatch.setattr(SimRotator, "move_mechanical", frozen_move)
    sim_hub._rotator_sky_sign = None

    with pytest.raises(DeviceError, match="too little"):
        await sim_hub.learn_rotator_sign(step_deg=2.0)
    assert sim_hub._rotator_sky_sign is None, "a failed measurement must not stick"


# --------------------------------------- the loop actually APPLIES the sign


async def test_rotate_to_pa_converges_under_a_negative_sign(sim_hub, monkeypatch):
    """The point of R-4: the loop must drive a REAL correction under a -1
    train, not merely store the number ``learn_rotator_sign`` returned.
    ``rotation_deg`` the hub reports for ``rotated: True`` is only as good as
    the physical camera angle it leaves behind, so this checks the latter.

    Mutation 'sign forced to +1' (``rotate_to_pa``'s ``sign =
    self._rotator_sky_sign`` changed to ``sign = 1``) went red here, before
    this assertion ever ran -- forcing +1 against a real -1 train commands
    every correction backwards, so the solved error GROWS attempt to
    attempt (65.0 -> 50.0 -> 80.0) exactly like the un-signed loop did on
    2026-08-08, and the loop's own not-converging guard raises first:

        >       result = await sim_hub.rotate_to_pa(123.0)
        E       astrodeck.devices.base.DeviceError: rotator is not
        converging, so it has been stopped after 3 attempts rather than
        turned further: the error went 50.0° -> 80.0° across the last move.
        Attempts (attempt, solved PA, target, error, commanded): [(1, 8.0,
        123.0, 65.0, -65.0), (2, 73.0, 123.0, 50.0, 50.0), (3, 23.0, 123.0,
        80.0, -80.0)]
    """
    _signed_solver(monkeypatch, -1, const_deg=50.0)
    sim_hub._rotator_sky_sign = None
    sim_hub.sim_rig.rotator_mech_deg = 40.0
    learned = await sim_hub.learn_rotator_sign(step_deg=2.0)
    assert learned["sign"] == -1, learned

    result = await sim_hub.rotate_to_pa(123.0)

    assert result["rotated"] is True, result
    true_pa = (-1.0 * sim_hub.sim_rig.rotator_mech_deg + 50.0) % 360.0
    d = abs((true_pa - 123.0 + 90.0) % 180.0 - 90.0)   # mod-180 distance
    assert d <= 1.0, (result, true_pa)
