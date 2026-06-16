"""Plate solving plugins.

`get_solver()` returns the best available solver: ASTAP if installed,
otherwise the simulator solver (which answers with the sim mount's true
pointing — exercising the full center/sync loop offline).
"""
from __future__ import annotations

from .base import PlateSolver, SolveResult
from .astap import AstapSolver, find_astap
from .simsolver import SimSolver

__all__ = ["PlateSolver", "SolveResult", "AstapSolver", "SimSolver",
           "find_astap", "get_solver"]


def get_solver(sim_rig=None, mode: str | None = None) -> PlateSolver:
    """Best available solver. ASTAP wins when installed (the only solver trusted
    on a real rig). Otherwise fall back to ``SimSolver`` — carrying ``mode`` so
    that fallback REFUSES to fake-solve a real (nina/alpaca) rig (review 5d)
    instead of echoing the pointing hint as a centered solve."""
    astap = find_astap()
    if astap:
        return AstapSolver(astap)
    return SimSolver(sim_rig, mode=mode)
