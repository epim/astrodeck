"""The meridian flip's re-centre carries the angle (#160; mosaic spec Revision
2 ruling 9, 5.7).

Ruling 9: a target with a set angle has the rotator commanded to it at every
acquisition, "resume, flip re-centre and night", and it is never assumed to be
wherever an earlier move left it. ``Hub.meridian_flip`` re-centres through
``goto_and_center`` and had no way to say an angle, so the flip was the one
acquisition that could not command it. It now takes ``rotation_deg`` and passes
it on; with ``None`` the call it makes is exactly today's, keyword for keyword,
so every existing caller is unchanged.

After a flip the camera is 180 degrees from where it was on the sky, under a
rotator that did not move, and 5.7 says the rotator is never turned 180 degrees
for it: a centred rectangle turned half a turn covers the same sky. The rotate
shortcut (``test_rotate_shortcut.py``) is what makes the flip re-centre honour
that without a rotate solve; the last case here runs the two together.

Every mutation named below was run in a private scratch copy of ``server/``
(issue #254), never in the shared tree. The failure each produced is recorded
verbatim on the test that caught it.

RE-PINNED FOR #532 (H4). Every solve frame now has a name of its own
(``rotate-<token>.fits``), so the last case's spy records each solve by its
kind, the name with the token taken out. Left comparing raw names, its
``"rotate.fits" not in seen`` would have passed whether or not the rotate loop
ran.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)

from astrodeck.solve.simsolver import SimSolver

RA, DEC = 5.0, 10.0

#: A solve frame's name since #532: ``<kind>-<12 hex digits>.fits``.
_UNIQUE = re.compile(r"^(?P<kind>.+)-[0-9a-f]{12}\.fits$")


def _kind_of(fits_path) -> str:
    """``rotate.fits`` for ``rotate-4c1297bee2b9.fits``; a name without a
    token is kept as it is."""
    name = Path(fits_path).name
    m = _UNIQUE.match(name)
    return f"{m.group('kind')}.fits" if m else name


#: What the spy answers in place of a real re-centre.
_CANNED = {"centered": True, "error_arcmin": 0.2, "attempts": 1,
           "rotation": None}


@pytest.fixture
def recentre_calls(sim_hub, monkeypatch):
    """Every call ``meridian_flip`` makes to ``goto_and_center``, exactly as
    made: the positional arguments and the keyword arguments, apart. The spy
    answers a canned result so these cases grade the CALL, not the goto."""
    calls: list[tuple[tuple, dict]] = []

    async def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return dict(_CANNED)

    monkeypatch.setattr(sim_hub, "goto_and_center", spy)
    return calls


async def test_without_an_angle_the_recentre_call_is_todays(sim_hub,
                                                             recentre_calls):
    """Both spellings of "no angle" make today's call: two positional
    arguments and no keywords at all.

    Mutation 'always pass the angle' (``goto_and_center(ra_hours, dec_deg,
    rotation_deg=rotation_deg)`` unconditionally) went red here, on the
    first spelling:

        >       assert recentre_calls == [((RA, DEC), {})], recentre_calls
        E       AssertionError: [((5.0, 10.0), {'rotation_deg': None})]
        E       assert [((5.0, 10.0)..._deg': None})] == [((5.0, 10.0), {})]
    """
    await sim_hub.meridian_flip(RA, DEC)
    assert recentre_calls == [((RA, DEC), {})], recentre_calls
    recentre_calls.clear()
    await sim_hub.meridian_flip(RA, DEC, rotation_deg=None)
    assert recentre_calls == [((RA, DEC), {})], recentre_calls


async def test_a_set_angle_reaches_the_recentre(sim_hub, recentre_calls):
    """Mutation 'rotation dropped' (the flip calls ``goto_and_center(ra_hours,
    dec_deg)`` whatever it was given) went red here and on the PA 0 case:

        >       assert recentre_calls == [((RA, DEC), {"rotation_deg": 30.0})], recentre_calls
        E       AssertionError: [((5.0, 10.0), {})]
        E       assert [((5.0, 10.0), {})] == [((5.0, 10.0)..._deg': 30.0})]
    """
    result = await sim_hub.meridian_flip(RA, DEC, rotation_deg=30.0)
    assert recentre_calls == [((RA, DEC), {"rotation_deg": 30.0})], recentre_calls
    # The flip's own keys ride on the re-centre's result, as before.
    assert result["centered"] is True and "flipped" in result, result


async def test_pa_zero_is_an_angle(sim_hub, recentre_calls):
    """PA 0 is a real angle, north up (``flows/store.py`` says why), so the
    most familiar way to get this condition wrong in this codebase must not
    drop it.

    Mutation 'truthy PA' (``if not rotation_deg:`` in place of ``if
    rotation_deg is None:``) went red here alone:

        >       assert recentre_calls == [((RA, DEC), {"rotation_deg": 0.0})], recentre_calls
        E       AssertionError: [((5.0, 10.0), {})]
        E       assert [((5.0, 10.0), {})] == [((5.0, 10.0)...n_deg': 0.0})]
    """
    await sim_hub.meridian_flip(RA, DEC, rotation_deg=0.0)
    assert recentre_calls == [((RA, DEC), {"rotation_deg": 0.0})], recentre_calls


async def test_a_flip_recentre_at_the_half_turn_twin_does_not_turn_the_rotator(
        sim_hub, monkeypatch):
    """The real re-centre on the simulator, no spy. The rotator was calibrated
    at PA 30 and has not moved, and the flip carries PA 210.5.

    On a real German mount the flip turns the field half a turn under the
    rotator, so a rotator calibrated at 30 before the flip reads 30 while the
    camera is at 210 on the sky, and the planned angle it is asked for sits
    about 180 from the reading. The simulator models neither half of that: its
    solver reports the mechanical-plus-clocking angle whatever the pier side,
    and its mount did not change sides here (``flipped`` is False below). So
    the case asks for the half-turn twin of the reading instead, which puts
    the same 180-degree difference in front of the same mod-180 arithmetic.
    The flip leaves the rotator where it is, takes no rotate solve, and says
    so in its result (5.7: the rotator is never turned 180 degrees for a
    flip).

    Mutation 'rotation dropped' went red here too, on the result carrying no
    rotation at all:

        >       assert rot is not None and rot["shortcut"] is True, result
        E       AssertionError: {'attempts': 2, 'centered': True, 'error_arcmin': 0.176842248926498, 'flipped': False, ...}
        E       assert (None is not None)

    The shortcut's own mutants reach it through the flip as well:
    'no shortcut' and 'no mod 180' (both named in test_rotate_shortcut.py)
    each went red here, because the rotate loop ran and its result carries no
    shortcut flag. The loop converged at the twin without turning the
    rotator, so the flag is the assertion that tells the two paths apart:

        >       assert rot is not None and rot["shortcut"] is True, result
        E       KeyError: 'shortcut'
    """
    rig = sim_hub.sim_rig
    rig.ra_hours, rig.dec_deg = RA, DEC
    rig.rotator_pa_offset_deg = 20.0
    rig.rotator_mech_deg = 10.0
    await sim_hub.sync_rotator_to_sky()
    assert sim_hub.last_sky_angle["calibrated"] is True
    seen: list[str] = []
    real = SimSolver.solve

    async def spy(self, fits_path, **kw):
        seen.append(_kind_of(fits_path))
        return await real(self, fits_path, **kw)

    monkeypatch.setattr(SimSolver, "solve", spy)

    result = await sim_hub.meridian_flip(RA, DEC, rotation_deg=210.5)

    rot = result.get("rotation")
    assert rot is not None and rot["shortcut"] is True, result
    assert rot["error_deg"] == pytest.approx(0.5, abs=0.01), rot
    assert "rotate.fits" not in seen, seen
    # The spy saw the centring solves, so the line above is a count of a
    # list that holds solves, not of an empty one.
    assert "solve.fits" in seen, seen
    assert rig.rotator_mech_deg == pytest.approx(10.0)
    assert result["centered"] is True, result
