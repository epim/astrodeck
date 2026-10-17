# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#1049: ``Hub.solve_and_sync`` says why there are no field markers when the
solver solved and refused its own WCS.

``AstapSolver`` returns a successful ``SolveResult`` with ``wcs=None`` and, since
#1025, a ``wcs_dropped_reason`` when it cannot use the headerlet. The per-frame
stamping path logs that reason; ``solve_and_sync`` published nothing and said
nothing, so every centring attempt on such a solve repeated the silence.

NAMED MUTANT M1049 "the branch removed" (the ``elif result.wcs is None and
result.wcs_dropped_reason:`` block in hub.py deleted): RED, no "field markers
unavailable" line.
"""
from __future__ import annotations

from _simhub import sim_hub  # noqa: F401 (fixture import)

from astrodeck.solve.base import SolveResult

REASON = ("the solution states no usable plate scale (no CD or CDELT cards, "
          "or a zero, singular or non-finite matrix)")


class _Solver:
    name = "Dropped"

    def __init__(self, reason):
        self.reason = reason

    async def solve(self, fits_path, *, ra_hint=None, dec_hint=None,
                    fov_deg_hint=None):
        return SolveResult(True, ra_hours=ra_hint or 5.4321,
                           dec_deg=dec_hint or 12.3456,
                           pixel_scale_arcsec=1.55, message="solved",
                           wcs=None, wcs_dropped_reason=self.reason)


def _use(monkeypatch, reason):
    import astrodeck.providers as providers_module
    solver = _Solver(reason)
    monkeypatch.setattr(providers_module, "pick_solver", lambda hub: solver)


async def test_a_dropped_wcs_is_said_with_the_solvers_reason(
        sim_hub, monkeypatch, bus_lines):
    _use(monkeypatch, REASON)
    out = await sim_hub.solve_and_sync(exposure_s=0.05)
    assert out["solver"] == "Dropped"            # the sync still went through
    said = [(lvl, m) for lvl, m, src in bus_lines
            if src == "solve" and m.startswith("field markers unavailable")]
    assert said == [("info", f"field markers unavailable: {REASON}")], bus_lines


async def test_no_reason_means_no_invented_line(sim_hub, monkeypatch, bus_lines):
    """A solver that kept no WCS and gave no reason gets no guessed cause."""
    _use(monkeypatch, None)
    await sim_hub.solve_and_sync(exposure_s=0.05)
    assert not [m for _, m, _ in bus_lines
                if m.startswith("field markers unavailable")], bus_lines
