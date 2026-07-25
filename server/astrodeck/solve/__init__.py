"""Plate solving plugins.

`get_solver()` returns the best available solver: ASTAP if installed,
otherwise the simulator solver (which answers with the sim mount's true
pointing — exercising the full center/sync loop offline).
"""
from __future__ import annotations

from .base import PlateSolver, SolveResult
from .astap import AstapSolver, find_astap
from .gate import wcs_should_solve
from .simsolver import SimSolver

__all__ = ["PlateSolver", "SolveResult", "AstapSolver", "SimSolver",
           "find_astap", "get_solver", "wcs_should_solve"]


def get_solver(sim_rig=None, mode: str | None = None,
               real_motion: bool = False) -> PlateSolver:
    """Best available solver. ASTAP wins when installed (the only solver trusted
    on a real rig). Otherwise fall back to ``SimSolver`` — carrying ``mode`` AND
    ``real_motion`` so that fallback REFUSES to fake-solve any rig with real
    motion hardware (review 5d + driver-framework A-minor 2: the device-borne
    ``hardware`` flag covers native serial mounts whose session name the old
    mode denylist never knew)."""
    astap = find_astap()
    if astap:
        return AstapSolver(astap)
    return SimSolver(sim_rig, mode=mode, real_motion=real_motion)
