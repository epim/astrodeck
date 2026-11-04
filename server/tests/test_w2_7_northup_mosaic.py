# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""What the Sky card's north-up 2x2 becomes on the server (#790, #1116 C-10).

The Sky card now sends a grid at the unturned dial as "Rotate to PA 0"
(``commandedPa(0, 4)`` is 0), the wizard's ``pa_deg = 0``. This pins the other
half: that answer, on a made-up centre at high declination, compiles to a
rotating block whose four panels are each commanded their own angle, with the
tolerance A.2 gives a corrected block, and that "Any angle" for the same grid
is refused. Zero is a position angle (#150), so the block at layout 0 still
commands its corners.
"""
from __future__ import annotations

import pytest

from astrodeck.flows import wizard
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.rig import RigFacts
from astrodeck.flows.to_plan import to_sequence_plan

CENTRE = ("06h 00m 00s", "+75 00 00")
RIG = RigFacts(fov_deg=(2.0, 1.33), has_rotator=True)


def _graph(**kw):
    return wizard.generate(wizard.KIND_MOSAIC, set(), "Probe", coords=CENTRE,
                           rows=2, cols=2, overlap_pct=10.0, rig=RIG, **kw)


def test_a_north_up_2x2_compiles_to_per_panel_correction():
    graph = _graph(angle_mode=wizard.ROTATE_TO_PA, pa_deg=0.0)
    plan, _ = to_sequence_plan(compile_plan(graph, "n"), graph,
                               flow_id="flow-w2-7", rig=RIG)
    group = plan.groups[0]
    assert group.rotate and group.pa_deg == 0.0
    angles = sorted(t.rotation_deg for t in plan.targets)
    assert len(angles) == 4 and angles[0] != angles[-1], angles
    assert any(abs(a - group.pa_deg) > 1.0 for a in angles), angles


def test_any_angle_for_a_grid_is_refused():
    with pytest.raises(ValueError, match="not 'Any angle'"):
        _graph(angle_mode="Any angle")
