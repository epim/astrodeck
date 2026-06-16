"""Plate-solver selection + the SimSolver false-solve guard (review 5d / P0-1).

The SimSolver is only ever a fallback when ASTAP isn't installed. On a real rig
that fallback must NOT echo the pointing hint back as a centered solve (it would
silently fake-center a real mount on a discovery miss). These tests pin that the
sim solver refuses for real (nina/alpaca) modes while still working for the
genuine simulator, and that ``get_solver`` plumbs the mode through."""
from __future__ import annotations

from pathlib import Path

import pytest

from astrodeck.solve import SimSolver, get_solver
from astrodeck.solve.astap import AstapSolver


class _FakeRig:
    ra_hours = 5.5
    dec_deg = 41.2


async def test_simsolver_refuses_in_nina_mode(tmp_path):
    """A SimSolver built for a real (nina) rig must FAIL with a clear message even
    when handed a hint — never echo the hint as a successful solve."""
    solver = SimSolver(sim_rig=None, mode="nina")
    res = await solver.solve(tmp_path / "x.fits", ra_hint=10.0, dec_hint=30.0)
    assert res.success is False
    assert "ASTAP" in res.message  # tells the operator how to fix it


async def test_simsolver_refuses_in_alpaca_mode(tmp_path):
    solver = SimSolver(sim_rig=None, mode="alpaca")
    res = await solver.solve(tmp_path / "x.fits", ra_hint=10.0, dec_hint=30.0)
    assert res.success is False


async def test_simsolver_refuses_even_with_a_rig_object_in_nina_mode(tmp_path):
    """Mode wins over a stray sim_rig: refusing on a real rig is the priority."""
    solver = SimSolver(sim_rig=_FakeRig(), mode="nina")
    res = await solver.solve(tmp_path / "x.fits")
    assert res.success is False


async def test_simsolver_still_solves_for_the_simulator(tmp_path):
    """The legitimate offline path still works: sim mode with a sim rig solves to
    the rig's true pointing (the center/sync loop depends on this)."""
    solver = SimSolver(sim_rig=_FakeRig(), mode="sim")
    res = await solver.solve(tmp_path / "x.fits")
    assert res.success is True
    assert res.ra_hours == pytest.approx(5.5)
    assert res.dec_deg == pytest.approx(41.2)


async def test_simsolver_solves_from_hint_when_no_mode(tmp_path):
    """No mode / sim mode with only a hint (no rig) still echoes the hint — this
    is the offline-demo behavior; the refusal only triggers for real modes."""
    solver = SimSolver(sim_rig=None, mode=None)
    res = await solver.solve(tmp_path / "x.fits", ra_hint=3.0, dec_hint=-7.0)
    assert res.success is True
    assert res.ra_hours == pytest.approx(3.0)
    assert res.dec_deg == pytest.approx(-7.0)


def test_get_solver_passes_mode_to_simsolver_fallback(monkeypatch):
    """When ASTAP isn't found, get_solver must hand the hub mode to the SimSolver
    so the fallback can refuse on a real rig."""
    import astrodeck.solve as solve_pkg
    monkeypatch.setattr(solve_pkg, "find_astap", lambda: None)
    s = get_solver(sim_rig=None, mode="nina")
    assert isinstance(s, SimSolver)
    assert s.mode == "nina"


def test_get_solver_prefers_astap_when_present(monkeypatch):
    import astrodeck.solve as solve_pkg
    monkeypatch.setattr(solve_pkg, "find_astap", lambda: r"C:\fake\astap.exe")
    s = get_solver(sim_rig=None, mode="nina")
    assert isinstance(s, AstapSolver)
