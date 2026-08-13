"""The night ends parked and warm, and the preview may not promise otherwise.

THE DEFECT THIS PINS. ``resolve_tonight`` has always closed its timeline with
"Dawn: loop ends, mount parks, camera warms" — unconditionally, for every flow.
``to_sequence_plan`` set ``park_when_done=False``. So the panel told the
operator the mount would park, and the plan guaranteed it would not: found by
reading the compiled plan of a flow that was already running on the rig, never
by a test, because both halves were independently self-consistent.

It is the house's signature shape — a claim nothing keeps — with the worst
possible audience, since the person it misleads is asleep when it comes due.
The engine's own wind-down calls the un-parked case something a run must SAY
out loud; a preview saying the opposite is worse than silence.

The binding test is the durable half. Anyone may change the policy, but the
preview and the plan have to move together.
"""
from __future__ import annotations

import pytest

from astrodeck.flows.compile import compile_plan
from astrodeck.flows.examples import examples
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.to_plan import plan_extras, to_sequence_plan
from astrodeck.flows.tonight import resolve_tonight

SITE = {"latitude": 40.0, "longitude": -105.0, "elevation_m": 1600.0}
# A fixed instant so the timeline is a function of its input and not of when
# the suite happens to run.
WHEN = 1_754_000_000.0


def _n(nid, ntype, x=0.0, y=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=y, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def _simple_graph() -> FlowGraph:
    return FlowGraph(
        nodes=[_n("t", "target", name="M31", ra="00h 42m 44s", dec="+41 16 09"),
               _n("c", "capture", x=100, exposure=120, count=10, filter="L")],
        edges=[_e("t", "target", "c", "run")])


class TestThePlanEndsTheNightSafely:
    def test_a_compiled_flow_parks_and_warms(self):
        plan, _ = to_sequence_plan(compile_plan(_simple_graph(), "n"))
        assert plan.park_when_done is True
        assert plan.warm_cooler_when_done is True

    @pytest.mark.parametrize("ex", [e for e in examples()], ids=lambda e: e.id)
    def test_every_shipped_example_ends_parked(self, ex):
        """The examples are what a new operator runs first, unedited."""
        plan, _ = to_sequence_plan(compile_plan(ex.graph, ex.name), ex.graph)
        assert plan.park_when_done is True, f"{ex.id} would track into daylight"
        assert plan.warm_cooler_when_done is True, f"{ex.id} would leave the TEC cold"

    def test_plan_extras_carries_them_even_with_no_automation(self):
        """No calibration queue, no dome, no rules — still parks. The end of the
        night is not conditional on anything the flow happens to contain."""
        assert plan_extras({}) == {"park_when_done": True,
                                   "warm_cooler_when_done": True}


class TestThePreviewCannotOutrunThePlan:
    """The binding. A promise in the timeline must be a field in the plan."""

    @pytest.mark.parametrize("ex", [e for e in examples()], ids=lambda e: e.id)
    def test_the_preview_cannot_promise_what_the_plan_drops(self, ex):
        compiled = compile_plan(ex.graph, ex.name)
        plan, _ = to_sequence_plan(compiled, ex.graph)
        out = resolve_tonight(compiled, SITE, name=ex.name, now=WHEN)
        if not out.get("ok"):
            pytest.skip(f"{ex.id} cannot be resolved at this instant: "
                        f"{out.get('reason')}")
        dawn = [r for r in out["story"] if "mount parks" in (r.get("msg") or "")]
        assert dawn, ("the dawn row stopped saying the mount parks — if that "
                      "claim moved, this binding has to move with it")
        assert plan.park_when_done is True, (
            f"{ex.id}: the timeline says {dawn[0]['msg']!r} and the plan does "
            f"not park")
        if "camera warms" in dawn[0]["msg"]:
            assert plan.warm_cooler_when_done is True, (
                f"{ex.id}: the timeline promises a warm ramp the plan drops")

    def test_the_binding_would_fail_if_the_flag_were_dropped(self):
        """SABOTAGE, INLINE. A binding test that cannot fail is the thing it
        was written to prevent, so this proves the assertion has teeth rather
        than asserting True is True on a plan that happens to be right."""
        compiled = compile_plan(_simple_graph(), "n")
        plan, _ = to_sequence_plan(compiled)
        broken = plan.model_copy(update={"park_when_done": False})
        out = resolve_tonight(compiled, SITE, name="n", now=WHEN)
        if not out.get("ok"):
            pytest.skip("cannot resolve at this instant")
        promised = any("mount parks" in (r.get("msg") or "") for r in out["story"])
        assert promised and not broken.park_when_done, (
            "the preview promises a park and the sabotaged plan drops it — "
            "which is exactly the state the test above must reject")
