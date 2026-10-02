# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""PARK + CLOSE's canvas warning must not claim more than the node itself owns.

#646: ``NODE_LOSS["parkclose"]`` told the operator "'Hold cold (day darks)' is
not honoured and no darks are taken after a shutdown". The first half is true -
this node's own cooler toggle never reaches the engine, the camera warms at the
end of every run regardless of it. The second half is false whenever a
CALIBRATION QUEUE is wired to SHUTDOWN COMPLETE: `plan_extras` then funds the
``day_darks`` lane and the wind-down spends it between the park and the warm
ramp (see ``test_day_darks.py``). The shipped campaign example wires exactly
that, so its own warning was telling the operator who built it that a lane
their own flow runs does not exist.

The fix narrows the warning to what the node's OWN setting fails to do, and
names the lane that actually funds day darks instead of pretending there is
none. This file compiles that example (the premise: the lane really is wired
and really is funded) and reads the node's note straight out of
``NODE_LOSS`` - the table `inert_nodes` renders it from - rather than
re-deriving the wording here.
"""
from __future__ import annotations

from astrodeck.flows import examples as ex
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.to_plan import NODE_LOSS, to_sequence_plan


def _campaign():
    """The shipped example that wires SHUTDOWN COMPLETE -> CALIBRATION QUEUE,
    compiled with its graph so `inert_nodes` can see the PARK + CLOSE node."""
    rec = [f for f in ex.examples() if f.id == "example-campaign"][0]
    compiled = compile_plan(rec.graph, rec.name)
    plan, unmapped = to_sequence_plan(compiled, rec.graph)
    return compiled, plan, unmapped


def _parkclose_row(unmapped: list[dict]) -> dict:
    rows = [u for u in unmapped if u["key"] == "nodes.parkclose"]
    assert len(rows) == 1, (
        f"expected exactly one PARK + CLOSE note, got {rows}")
    return rows[0]


def test_the_campaign_actually_funds_day_darks():
    """THE PREMISE. If the example stops wiring SHUTDOWN COMPLETE to a
    CALIBRATION QUEUE, the rest of this file is testing a warning against a
    flow that was never a counter-example to it."""
    _compiled, plan, _unmapped = _campaign()
    assert plan.day_darks > 0, (
        "the campaign example no longer funds the day-darks lane, so it no "
        "longer disproves the PARK + CLOSE warning's old claim that no darks "
        "are taken after a shutdown")


def test_the_warning_does_not_claim_no_darks_are_taken():
    """THE FIX. Named mutant: restore the old sentence - 'is not honoured and
    no darks are taken after a shutdown' - and this fails because the overclaim
    is back despite the campaign funding the lane one line above."""
    _compiled, _plan, unmapped = _campaign()
    detail = _parkclose_row(unmapped)["detail"]
    assert "no darks are taken" not in detail, (
        f"PARK + CLOSE still claims no darks are taken after a shutdown, "
        f"while the campaign it is drawn on funds exactly that lane: {detail!r}")


def test_the_warning_names_the_lane_that_does():
    """An operator who wired SHUTDOWN COMPLETE -> CALIBRATION QUEUE needs to be
    told their wire is the thing taking the darks - not left to guess why the
    canvas and the night disagree."""
    detail = NODE_LOSS["parkclose"]
    assert "on_shutdown_complete" in detail, (
        f"the warning does not name the lane that funds day darks: {detail!r}")


def test_the_warning_still_says_this_node_s_own_setting_does_nothing():
    """The true half must survive the reword: PARK + CLOSE's own cooler toggle
    genuinely reaches nothing, and an operator relying on ITS 'Hold cold' to
    keep the sensor cold still needs the warning."""
    detail = NODE_LOSS["parkclose"]
    assert "Hold cold" in detail, (
        f"the warning stopped naming the setting it is actually about: "
        f"{detail!r}")
