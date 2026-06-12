"""Simulator solver: answers with the sim mount's true pointing.

This makes the full goto→solve→sync→re-slew centering loop work end to end
with the simulated rig, including the deliberate pointing error the sim
mount introduces.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from .base import PlateSolver, SolveResult


class SimSolver(PlateSolver):
    name = "Simulator"

    def __init__(self, sim_rig=None):
        self.sim_rig = sim_rig

    async def solve(self, fits_path: Path, *, ra_hint: float | None = None,
                    dec_hint: float | None = None,
                    fov_deg_hint: float | None = None) -> SolveResult:
        await asyncio.sleep(1.2)  # pretend to work
        if self.sim_rig is not None:
            return SolveResult(True, ra_hours=self.sim_rig.ra_hours,
                               dec_deg=self.sim_rig.dec_deg,
                               rotation_deg=0.0, pixel_scale_arcsec=1.55,
                               message="solved (simulator)")
        if ra_hint is not None and dec_hint is not None:
            return SolveResult(True, ra_hours=ra_hint, dec_deg=dec_hint,
                               rotation_deg=0.0, pixel_scale_arcsec=1.55,
                               message="solved (simulator, from hint)")
        return SolveResult(False, message="simulator solver needs a hint or sim rig")
