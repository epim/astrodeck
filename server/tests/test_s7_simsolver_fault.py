"""The simulator's staged solve failure (#189 S7 item 1, the second of its four
scenarios, walked on the real page by tools/ui_probe/routes_s7.json).

``ASTRODECK_SIM_SOLVE_FAULT = "<ra_hours>,<dec_deg>,<radius_deg>"`` makes every
``SimSolver`` solve whose field lies within the radius, on the great circle,
fail as a solve that finds no solution does, and leaves every other solve as
it was. tools/ui_probe/server_ctl.py sets it (``--sim-solve-fault``) to one
panel centre of the probe's rotating 2x2, so that panel never centres on a
real server and the run sets it aside at its third pass, with nothing mocked
in the process. It is sim-only by construction: the class that reads it
refuses a real rig first, and that refusal is what a real rig gets whatever
the variable says.

The radius the probe uses, 0.12 deg, is inside the geometry it has to fit:
the 2x2's nearest other panel centre is 0.278 deg away (0.371 deg x 0.75,
the simulator's short field side at 25% overlap), and an unsynced simulator
goto lands 0.04 deg x 0.7 off on each axis (``SimTelescope.slew``), so the
faulted panel's every solve is inside it and no other panel's is.

MUTANTS, each run in a private copy of ``server/`` (scratchpad
``S7-PROBE-mut``), never in the shared tree; each failure is quoted where it
was observed.
"""
from __future__ import annotations

import pytest

from astrodeck.solve import SimSolver
from astrodeck.solve import simsolver as simsolver_mod
from astrodeck.solve.simsolver import (SOLVE_FAULT_ENV, SOLVE_FAULT_MESSAGE,
                                       solve_fault)

#: M31's catalogue position: a field centre, not a site.
RA_H, DEC = 0.7122, 41.269


class _Rig:
    """The sim rig as the solver reads it: where the tube truly points."""

    def __init__(self, ra_hours: float, dec_deg: float) -> None:
        self.ra_hours = ra_hours
        self.dec_deg = dec_deg


@pytest.fixture(autouse=True)
def _no_fault_from_outside(monkeypatch):
    """No test inherits a value from the shell that ran the suite."""
    monkeypatch.delenv(SOLVE_FAULT_ENV, raising=False)
    simsolver_mod._warned_fault_values.clear()


def _fault(monkeypatch, ra_h: float, dec: float, radius: float) -> None:
    monkeypatch.setenv(SOLVE_FAULT_ENV, f"{ra_h},{dec},{radius}")


async def test_a_field_inside_the_staged_failure_does_not_solve(monkeypatch, tmp_path):
    """The panel the knob is centred on: its solve fails, in words that say
    the failure was staged, with no solution in it.

    RED under mutant "knob ignored" (``SimSolver.solve``: the
    ``_inside_solve_fault`` branch deleted), observed:

        AssertionError: the staged panel solved: SolveResult(success=True,
        ra_hours=0.7140666666666667, dec_deg=41.297, rotation_deg=0.0,
        pixel_scale_arcsec=1.55, message='solved (simulator)', ...)
    """
    _fault(monkeypatch, RA_H, DEC, 0.12)
    # An unsynced goto's landing: 0.028 deg off in RA and in Dec.
    rig = _Rig(RA_H + 0.028 / 15.0, DEC + 0.028)
    res = await SimSolver(rig, mode="sim").solve(tmp_path / "x.fits")
    assert res.success is False, f"the staged panel solved: {res!r}"
    assert res.message == SOLVE_FAULT_MESSAGE
    assert SOLVE_FAULT_ENV in res.message
    # The shape of every failed solve: no WCS, the numbers left at their defaults.
    assert res.wcs is None and (res.ra_hours, res.dec_deg) == (0.0, 0.0)


async def test_the_nearest_other_panel_still_solves(monkeypatch, tmp_path):
    """CONTROL: the 2x2's nearest other panel centre, 0.278 deg away in Dec,
    solves to the rig's true pointing, as it did before the knob existed.
    Without this the case above passes on a solver that fails everything."""
    _fault(monkeypatch, RA_H, DEC, 0.12)
    rig = _Rig(RA_H, DEC + 0.278)
    res = await SimSolver(rig, mode="sim").solve(tmp_path / "x.fits")
    assert res.success is True, res
    assert (res.ra_hours, res.dec_deg) == (pytest.approx(RA_H), pytest.approx(DEC + 0.278))


async def test_no_knob_is_no_fault(tmp_path):
    """CONTROL: a server started without the variable solves the staged panel's
    field like any other (the default, and every run but the probe's second
    scenario)."""
    assert solve_fault() is None
    res = await SimSolver(_Rig(RA_H, DEC), mode="sim").solve(tmp_path / "x.fits")
    assert res.success is True
    assert res.message == "solved (simulator)"


async def test_the_fault_is_a_circle_on_the_sky_across_ra_zero(monkeypatch, tmp_path):
    """A fault centred just before 0h catches a field just after it: 0.002 h
    of RA at Dec 0 is 0.03 deg on the sky, and 23.998 h apart as numbers.

    RED under mutant "flat RA difference" (``_separation_deg`` returning
    ``math.hypot((ra2_h - ra1_h) * 15, dec2 - dec1)``), observed:

        AssertionError: a field 0.03 deg from the fault across 0h solved:
        SolveResult(success=True, ra_hours=0.001, dec_deg=0.0, rotation_deg=0.0,
        pixel_scale_arcsec=1.55, message='solved (simulator)', ...)
    """
    _fault(monkeypatch, 23.999, 0.0, 0.12)
    res = await SimSolver(_Rig(0.001, 0.0), mode="sim").solve(tmp_path / "x.fits")
    assert res.success is False, f"a field 0.03 deg from the fault across 0h solved: {res!r}"


async def test_the_hint_is_the_field_when_there_is_no_rig(monkeypatch, tmp_path):
    """The offline path (no sim rig, a hint) is faulted by the hint, which is
    then the only word for where the frame was taken; outside it the hint is
    still echoed (CONTROL)."""
    _fault(monkeypatch, RA_H, DEC, 0.12)
    solver = SimSolver(None, mode=None)
    inside = await solver.solve(tmp_path / "x.fits", ra_hint=RA_H, dec_hint=DEC)
    assert inside.success is False
    outside = await solver.solve(tmp_path / "x.fits", ra_hint=RA_H, dec_hint=DEC + 1.0)
    assert outside.success is True and outside.dec_deg == pytest.approx(DEC + 1.0)


@pytest.mark.parametrize("mode", ["nina", "alpaca"])
async def test_a_real_rig_gets_its_refusal_whatever_the_knob_says(monkeypatch, tmp_path, mode):
    """Sim-only by construction: with the knob centred on the field, a real
    rig's answer is still the real-rig refusal (install ASTAP), never the
    staged failure's words. The staged failure is asked after that refusal.

    RED under mutant "knob before the real-rig refusal" (the fault branch
    moved above the ``_REAL_MODES`` check), observed:

        AssertionError: assert 'ASTAP' in 'no solution: this field is inside
        the simulator's staged solve failure (ASTRODECK_SIM_SOLVE_FAULT)'

    for both parameter sets, [nina] and [alpaca].
    """
    _fault(monkeypatch, RA_H, DEC, 0.12)
    res = await SimSolver(_Rig(RA_H, DEC), mode=mode).solve(
        tmp_path / "x.fits", ra_hint=RA_H, dec_hint=DEC)
    assert res.success is False
    assert "ASTAP" in res.message
    assert SOLVE_FAULT_ENV not in res.message


async def test_a_real_motion_rig_gets_its_refusal_too(monkeypatch, tmp_path):
    """The device-flag half of the same guard (a native serial mount on a sim
    camera): the refusal, not the staged failure."""
    _fault(monkeypatch, RA_H, DEC, 0.12)
    res = await SimSolver(_Rig(RA_H, DEC), mode="sim", real_motion=True).solve(
        tmp_path / "x.fits")
    assert res.success is False and "real-motion" in res.message


@pytest.mark.parametrize("raw", ["nonsense", "0.7,41", "0.7,41,0", "0.7,41,-1",
                                 "nan,41,0.1", "0.7,inf,0.1"])
async def test_an_unreadable_value_is_no_fault_and_says_so_once(monkeypatch, tmp_path,
                                                               caplog, raw):
    """A value that is not three finite numbers with a positive radius stages
    nothing (every solve answers as without it), and a warning names it, once
    however many solves read it.

    RED under mutant "a zero radius is a fault" (the ``radius <= 0`` test
    deleted), for ``0.7,41,0``, whose field is the fault's centre, observed:

        AssertionError: ('0.7,41,0', SolveResult(success=False, ...,
        message="no solution: this field is inside the simulator's staged
        solve failure (ASTRODECK_SIM_SOLVE_FAULT)", ...))
    """
    monkeypatch.setenv(SOLVE_FAULT_ENV, raw)
    solver = SimSolver(_Rig(0.7, 41.0), mode="sim")
    with caplog.at_level("WARNING", logger="astrodeck.solve.simsolver"):
        for _ in range(3):
            res = await solver.solve(tmp_path / "x.fits")
            assert res.success is True, (raw, res)
    warned = [r for r in caplog.records if SOLVE_FAULT_ENV in r.getMessage()]
    assert len(warned) == 1, (raw, [r.getMessage() for r in warned])
