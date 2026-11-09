# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""What a TargetGroup dumps, and the strategy a mosaic may not name (WP 5.1a,
kept by WP 5.1b2).

WP 5.1a gave TargetGroup ordered ``phases``; WP 5.1b2 removed them again
(#1074 is a chain of stages inside one target, ``ExposureStep.phase``, held by
tests/test_w5_1b2_chain_phases.py), and this file keeps the two things 5.1a
pinned that still hold:

* a plan with no chain and no pool dumps byte for byte as it did before 5.1a
  (the digests below were taken on the tree before that work package);
* ``pool_strategy`` is absent from a group's dump while None, and a mosaic that
  names one is refused at the start (``plan_identity_errors``).
"""
from __future__ import annotations

import hashlib
import json

from _group_harness import (grid_plan, golden_flow_plan, plain_mosaic_plan,
                            ra_at)
from astrodeck.sequence.models import (ExposureStep, SequencePlan, Target,
                                       TargetGroup, plan_identity_errors)

GID = "cycles"


def _member(tid: str, name: str) -> Target:
    return Target(id=tid, name=name, ra_hours=ra_at(-2.0), dec_deg=40.0,
                  center=True, autofocus_first=False, acquisition="cycle",
                  mosaic_group=GID,
                  steps=[ExposureStep(id=f"{tid}-L", filter="L",
                                      exposure_s=30.0, count=2)])


def group_plan(**group_kw) -> SequencePlan:
    """Two members of one group."""
    return SequencePlan(
        name="two members", guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=False, park_when_done=False,
        warm_cooler_when_done=False, recover_guiding=False,
        targets=[_member("c1", "Cycle 1"), _member("c2", "Cycle 2")],
        groups=[TargetGroup(id=GID, name="Cycles", **group_kw)])


# ------------------------------------------------------ identity refusals

def test_a_well_formed_group_is_accepted():
    assert plan_identity_errors(group_plan()) == []


def test_a_pool_strategy_on_a_mosaic_is_refused():
    errors = plan_identity_errors(group_plan(pool_strategy="priority"))
    assert any("mosaic" in e and "'priority'" in e for e in errors), errors


def test_a_pool_strategy_on_a_pool_is_accepted():
    assert plan_identity_errors(
        group_plan(kind="pool", pool_strategy="priority")) == []


# ------------------------------------------- no stored plan moves (rule 14)

def _digest(dump: dict) -> str:
    return hashlib.sha256(json.dumps(
        dump, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _plain_plan() -> SequencePlan:
    plan = plain_mosaic_plan()
    for i, t in enumerate(plan.targets):
        t.id = f"plain-{i}"
        for j, s in enumerate(t.steps):
            s.id = f"plain-{i}-{j}"
    return plan


#: Taken on the tree before WP 5.1a, with the dumps below.
BASE_DIGESTS = {
    "mosaic plan with a group":
        "e6ed997c28bee6ee9d3f61576ffd7e7ec4be52badd7803b66f3328b47f3753bf",
    "mosaic plan, sequential":
        "a40a361660b4d3e158b0942d1c354768ef0c8ba36458c2566c15a4f4c1d9b10b",
    "golden flow plan":
        "645d93d229504e7f8381fe1e37a3760af137f398bf0d48f64a87a5ac280b6ab6",
    "plan with no groups":
        "99025cda15dae769ef68a9afc331a0b94dc4ed7e28dffa46d6edbf877372bfda",
    "a bare group":
        "bf40de0657497c3f749ca9af4d5e5938d68d13a5c338bb87527ac8692e5e6f9a",
    "a group with options set":
        "6a1d8cc7353aa7ea29ac08415e99a747d8096e2a1677cb1de7bab6127e354069",
}


def test_plans_without_the_new_fields_dump_byte_for_byte_as_before():
    dumps = {
        "mosaic plan with a group": grid_plan(),
        "mosaic plan, sequential": grid_plan(group_kw={"mode": "sequential"}),
        "golden flow plan": golden_flow_plan(),
        "plan with no groups": _plain_plan(),
    }
    got = {name: _digest(plan.model_dump(mode="json"))
           for name, plan in dumps.items()}
    got["a bare group"] = _digest(
        TargetGroup(id="g", name="G").model_dump(mode="json"))
    got["a group with options set"] = _digest(TargetGroup(
        id="g", name="G", mode="sequential", order="grid"
    ).model_dump(mode="json"))
    assert got == BASE_DIGESTS


def test_the_pool_strategy_appears_in_the_dump_only_when_set():
    bare = TargetGroup(id="g").model_dump(mode="json")
    assert "pool_strategy" not in bare and "phases" not in bare
    full = TargetGroup(id="g", kind="pool",
                       pool_strategy="priority").model_dump(mode="json")
    assert full["pool_strategy"] == "priority"
    assert TargetGroup.model_validate(full) == TargetGroup(
        id="g", kind="pool", pool_strategy="priority")
