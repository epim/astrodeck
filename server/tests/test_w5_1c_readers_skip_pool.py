# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Every reader of ``plan.groups`` reads only a ``kind: "mosaic"`` group as a
mosaic (WP 5.1c, #183, #184).

THE DEFECT. A POOL is a group of candidates, not a grid. Until it became a
group every ``plan.groups`` entry was a mosaic, and these readers kept that
assumption: the Tonight readouts walk broke at the first pool member and
dropped every later block's readouts; the coverage report listed the pool as
a mosaic with panels at no grid place; the stamping note warned about
"MOSAIC - coverage cannot be checked" for a plan whose only group is a pool;
the session list row named the pool a mosaic; the mosaic preview considered
it a tile set.

The plans are built with a pool group directly, as test_w5_pool_group.py
does: the compile that emits one is another work package's. Nothing here
reads a clock or a site.

NAMED MUTANT (the engine's, held in test_w5_1c_engine_pool_is_not_a_mosaic.py):
`_fire_target_complete` reading every group as a mosaic.
"""
from __future__ import annotations

from astrodeck.flows.progress import flow_progress
from astrodeck.flows.readouts import readouts
from astrodeck.imaging import mosaic_preview
from astrodeck.sequence import coverage
from astrodeck.sequence.models import (ExposureStep, SequencePlan, Target,
                                       TargetGroup)
from astrodeck.sequence.session import Session, mosaics_of
from test_flows_progress_route import TARGET_POOL_AND_MOSAIC, _compiled

POOL = "pool-1"
FLOW = "fx-1c"


def _pooled_plan() -> tuple[dict, SequencePlan]:
    """The TARGET, POOL, MOSAIC flow's compile and plan, with the pool's two
    candidates moved onto a pool group listed BEFORE the mosaic's group."""
    compiled, plan = _compiled(TARGET_POOL_AND_MOSAIC, FLOW)
    assert [t.name for t in plan.targets[1:3]] == ["M42", "M31"], (
        "premise: targets 1 and 2 are the flow's pool")
    candidates = {t.id for t in plan.targets[1:3]}
    targets = [t.model_copy(update={"mosaic_group": POOL})
               if t.id in candidates else t for t in plan.targets]
    pool = TargetGroup(id=POOL, name="M42, M31", kind="pool",
                       mode="sequential", pool_strategy="best")
    return compiled, plan.model_copy(update={"targets": targets,
                                             "groups": [pool, *plan.groups]})


def _lone_pool_plan(*, geometry: dict | None = None) -> SequencePlan:
    """Two candidates and nothing else: a plan whose only group is a pool."""
    step = ExposureStep(id="s", filter="L", exposure_s=60.0, count=3)
    targets = [Target(id=f"c{i}", name=f"Cand {i}", ra_hours=1.0 + i,
                      dec_deg=20.0, mosaic_group=POOL, steps=[step])
               for i in (1, 2)]
    pool = TargetGroup(id=POOL, name="Pool", kind="pool",
                       geometry=geometry or {})
    return SequencePlan(name="pool", targets=targets, groups=[pool])


def test_a_pool_does_not_end_the_readouts_walk():
    compiled, plan = _pooled_plan()
    got = readouts(compiled, plan, None)
    assert set(got) == {"t", "m"}, (
        f"the TARGET and the mosaic after the pool keep their readouts: "
        f"{sorted(got)}")
    assert got["m"]["panels"] == 3 and got["m"]["subs_per_panel"] > 0


def test_a_plan_with_no_pool_reads_its_readouts_as_before():
    compiled, plan = _compiled(TARGET_POOL_AND_MOSAIC, FLOW)
    assert set(readouts(compiled, plan, None)) == {"t", "m"}


def test_progress_reads_the_pool_as_candidates_not_mosaic_panels():
    """Already so on arrival (progress finds a pool's members by identity and
    only a block with a grid is a mosaic): held here so it stays so."""
    compiled, plan = _pooled_plan()
    blocks = flow_progress(compiled, plan, None, flow_id=FLOW)["blocks"]
    pool = next(b for b in blocks if b.get("kind") == "pool")
    assert [p["name"] for p in pool["panels"]] == ["M42", "M31"]
    assert all((p["row"], p["col"]) == (None, None) for p in pool["panels"])
    assert [b["name"] for b in blocks if "grid" in b] == ["M16"]


def test_the_coverage_report_lists_mosaic_groups_only():
    plan = _lone_pool_plan()
    assert coverage.coverage_report(Session(plan=plan, frames=[])) == {
        "groups": []}


def test_the_stamping_note_is_for_a_mosaic_not_a_pool():
    plan = _lone_pool_plan()
    assert coverage.stamping_note(plan, False) is None
    assert coverage.stamping_note(_pooled_plan()[1], False) == (
        coverage.STAMPING_OFF_NOTE), "a mosaic beside the pool still warns"


def test_the_session_list_row_names_no_pool_a_mosaic():
    assert mosaics_of(_lone_pool_plan()) == []
    kept = mosaics_of(_pooled_plan()[1])
    assert [m["name"] for m in kept] == ["M16"], kept


def test_the_mosaic_preview_does_not_lay_a_pool_out_as_tiles():
    """A pool group never carries a field; given one anyway, the kind alone
    keeps it out of the tile set."""
    plan = _lone_pool_plan(geometry={"fov_x": 2.0, "fov_y": 1.3,
                                     "rows": 1, "cols": 2})
    assert mosaic_preview.placements_for(plan, {"c1", "c2"}) == []
