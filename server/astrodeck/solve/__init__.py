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


def get_solver(sim_rig=None) -> PlateSolver:
    astap = find_astap()
    if astap:
        return AstapSolver(astap)
    return SimSolver(sim_rig)
