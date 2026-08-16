"""The two Tonight surfaces disagreed with each other about the same night.

`tonight.brief` - the NIGHT story - said of a PARK + CLOSE node:

    "When astronomical night ends, the mount parks and the dust flap + dome
     closes with the cooler held cold, banking capped day darks."

Four claims. Two are false. `plan_extras` hardcodes `warm_cooler_when_done=True`
for every flow-derived plan, so the cooler is never held; and no lane shoots
darks after a shutdown at all - the calibration queue's darks are only ever
taken inside a cloud hold, matched to the step the clouds interrupted.

Meanwhile the CAMPAIGN tab's `_DAWN`, on the same page, said the opposite:
"Dawn parks the mount and warms the camera - the dome is not driven and the
cooler does not stay cold for day darks."

So the app contradicted itself about what the rig would do overnight, and only
one of the two could be checked against anything: the existing binding test
covers the TIMELINE rows (`story`), not `brief`, and only for park and warm.

WHY THIS SURFACE PARTICULARLY. Tonight is the page an operator reads BEFORE
walking away for the night. A promise made there is one they will not be awake
to check.

THE DROPPED DIALS BELONG IN THE UNMAPPED LIST, not in a hedge in the story. A
sentence saying the cooler "might" be held is worse than one saying what the
night does; `inert_nodes` is where "you set this and it reaches nothing" goes.
"""
from __future__ import annotations

import pytest

from astrodeck.flows import examples as ex
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.to_plan import NODE_LOSS, inert_nodes, to_sequence_plan
from astrodeck.flows.tonight import resolve_tonight

SITE = {"latitude": 37.0, "longitude": -122.0, "elevation_m": 100.0}
WHEN = 1755000000.0


def _campaign():
    rec = [f for f in ex.examples() if f.id == "example-campaign"][0]
    return rec, compile_plan(rec.graph, rec.name)


def _brief_text(rec, compiled=None) -> str:
    """THE GRAPH, not the compiled dict.

    `resolve_tonight` takes either, but `brief(graph)` needs the graph: handed a
    compiled plan it gets `None` and returns a string with no dawn sentence in
    it at all. The first version of this file passed the compiled dict, so every
    assertion below ran against an empty paragraph and all of them passed -
    including the two that exist to catch a false promise. A test that cannot
    fail is the thing it was written to prevent.
    """
    out = resolve_tonight(rec.graph, SITE, name=rec.name, now=WHEN)
    if not out.get("ok"):
        pytest.skip(f"cannot resolve at this instant: {out.get('reason')}")
    text = str(out.get("brief") or "")
    assert "night ends" in text, (
        "the dawn sentence is missing from the brief entirely, so nothing "
        "below is actually being checked - see this docstring")
    return text


class TestTheBriefDoesNotPromiseTheCoolerOrDayDarks:
    def test_it_does_not_claim_the_cooler_is_held(self):
        rec, compiled = _campaign()
        plan, _ = to_sequence_plan(compiled, rec.graph)
        text = _brief_text(rec, compiled)
        if "held cold" in text:
            assert plan.warm_cooler_when_done is False, (
                "the night story promises the cooler is held cold and the plan "
                "warms it - the operator is told their day darks will happen "
                "on a sensor that will be at ambient")

    def test_it_does_not_claim_day_darks_are_banked(self):
        rec, compiled = _campaign()
        text = _brief_text(rec, compiled)
        assert "day darks" not in text, (
            "the night story still promises day darks; nothing shoots them - "
            "the only automatic darks in the engine are the cloud hold's")

    def test_it_still_says_the_things_that_ARE_true(self):
        """The guard against fixing a lie by deleting the sentence. The mount
        really does park and the camera really is warmed, and an operator needs
        to be told both - the warm is why the rig is not ready for darks."""
        rec, compiled = _campaign()
        plan, _ = to_sequence_plan(compiled, rec.graph)
        text = _brief_text(rec, compiled)
        assert "parks" in text, "the story stopped mentioning the park"
        assert plan.park_when_done is True
        assert "warms" in text, (
            "the story does not say the camera is warmed, which is the fact "
            "that makes 'no day darks' make sense")
        assert plan.warm_cooler_when_done is True


class TestTheTwoTabsAgree:
    def test_neither_surface_claims_a_cooler_hold(self):
        """The contradiction itself, as one assertion. `_DAWN` (campaign tab)
        and `brief` (night story) describe the same dawn on the same rig."""
        from astrodeck.flows.tonight import _DAWN
        rec, compiled = _campaign()
        text = _brief_text(rec, compiled)
        assert "does not stay cold" in _DAWN, (
            "_DAWN changed - if the cooler hold was BUILT, this whole file "
            "needs rewriting rather than relaxing")
        assert "held cold" not in text, (
            "the night story and the campaign tab disagree about the cooler")


class TestTheDroppedDialIsReportedInstead:
    def test_parkclose_gets_a_specific_loss_not_the_generic_one(self):
        rec, _compiled = _campaign()
        rows = [n for n in inert_nodes(rec.graph)
                if n["key"] == "nodes.parkclose"]
        assert rows, "PARK + CLOSE is no longer reported at all"
        detail = rows[0]["detail"]
        assert "cooler" in detail, (
            "the one dial on this node that genuinely reaches nothing is not "
            f"named: {detail}")
        assert detail == NODE_LOSS["parkclose"]

    def test_it_does_not_claim_the_park_is_lost(self):
        """The over-report the generic sentence would make. Someone told their
        night does not park will go and park it by hand, or not run at all."""
        rec, compiled = _campaign()
        plan, _ = to_sequence_plan(compiled, rec.graph)
        detail = [n for n in inert_nodes(rec.graph)
                  if n["key"] == "nodes.parkclose"][0]["detail"]
        assert plan.park_when_done is True, "premise: the plan does park"
        assert "do not reach the run" not in detail, (
            "PARK + CLOSE still gets the blanket sentence, which tells the "
            "operator the park is lost when the plan parks unconditionally")

    def test_every_other_node_still_gets_the_generic_sentence(self):
        """The override table must stay a table of exceptions. If a second node
        quietly needed one, this catches it as an unexplained mismatch rather
        than letting a wrong sentence ship."""
        rec, _ = _campaign()
        for row in inert_nodes(rec.graph):
            kind = row["key"].split(".", 1)[1]
            if kind in NODE_LOSS:
                continue
            assert "do not reach the run" in row["detail"], (
                f"{kind} has bespoke wording that is not in NODE_LOSS")
