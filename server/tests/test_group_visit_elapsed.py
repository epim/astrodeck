# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""``state.group.visit_elapsed_s`` is the time into the current visit (#189 S2,
#318; spec 5.10).

The published group state carries how long the mosaic has been on the
current panel, for the Monitor's visit readout. Nothing graded the number:
the S2 review published a constant 0 in a private scratch copy of server/
(#254) and every S2 test still passed (396 passed), because the state tests
compare the key set and the other fields only.

THE NIGHT, on the clocked simulator (tests/_group_harness.py): a 2x2 of L
and R, count 3, two passes a visit, 30 s frames and no overhead on the fake
clock. Each visit shoots L R L R, so at its four frames the state reads 0,
30, 60 and 90 s into the visit, and the next panel's visit starts at 0
again: the clock starts after the hop and restarts with every visit.

MUTANT "elapsed always 0" (`_group_state` publishing ``0 * round(...)``):
RED (observed, ``-n0``):
    E       AssertionError: [('1-1', 'L', 0), ('1-1', 'R', 0), ('1-1', 'L',
            0), ('1-1', 'R', 0), ('1-2', 'L', 0), ('1-2', 'R', 0), ...]
    E         At index 1 diff: ('1-1', 'R', 0) != ('1-1', 'R', 30)
MUTANT "the run's clock, not the visit's" (`_group_state` measuring from
``self._started_at``): RED (observed, ``-n0``):
    E       AssertionError: [('1-1', 'L', 0), ('1-1', 'R', 30), ('1-1', 'L',
            60), ('1-1', 'R', 90), ('1-2', 'L', 120), ('1-2', 'R', 150), ...]
    E         At index 4 diff: ('1-2', 'L', 120) != ('1-2', 'L', 0)
"""
from __future__ import annotations

from _group_harness import (GROUP_NAME, Night, grid_plan, group_hub,  # noqa: F401
                            group_store)


async def test_the_published_visit_clock_runs_within_each_visit(
        group_hub, monkeypatch):
    night = Night(group_hub, monkeypatch)
    try:
        done = await night.run(grid_plan(group_kw={"visit_passes": 2}))
    finally:
        await night.close()
    assert done, night.trace[-3:]
    first_pass = [(c["target"][len(GROUP_NAME) + 1:], c["filter"],
                   c["group"]["visit_elapsed_s"]) for c in night.captures][:16]
    want = [(lb, f, s) for lb in ("1-1", "1-2", "2-2", "2-1")
            for f, s in (("L", 0), ("R", 30), ("L", 60), ("R", 90))]
    assert first_pass == want, first_pass
