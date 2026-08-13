"""A flow-driven night cools to the rig's own setpoint.

THE DEFECT. The flow vocabulary has no cooling node, and neither ``compile_plan``
nor ``to_sequence_plan`` ever set ``cool_to`` — so every flow-driven run left it
None, the engine skipped ``_cool_and_wait`` entirely, and the night shot at
whatever temperature the sensor had drifted to. The rig's dark library is
indexed at SET-TEMP -5.0 °C; frames taken at an unknown temperature cannot be
calibrated by it, which makes the night's data worth much less than the night.

Found by reading the compiled plan of a running flow — the same way the park
defect was — not by a test, because nothing in the compile is *wrong*; the field
is simply absent, and absent fields are what ``extra="ignore"`` was already
teaching this codebase to distrust.

THE FIX IS AN INJECTION, NOT A DEFAULT. ``to_sequence_plan`` never reads config;
the caller passes the rig's standing ``cooling.setpoint_c``. No setpoint means no
expressed intent, and the run behaves exactly as before rather than having a
temperature picked for it.
"""
from __future__ import annotations

from astrodeck.flows.compile import compile_plan
from astrodeck.flows.examples import examples
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.to_plan import to_sequence_plan


def _graph() -> FlowGraph:
    return FlowGraph(
        nodes=[FlowNode(id="t", type="target", x=0, y=0,
                        params={"name": "M31", "ra": "00h 42m 44s",
                                "dec": "+41 16 09"}),
               FlowNode(id="c", type="capture", x=100, y=0,
                        params={"exposure": 180, "count": 30, "filter": "Ha"})],
        edges=[FlowEdge(**{"from": "t", "fromPort": "target",
                           "to": "c", "toPort": "run"})])


class TestTheSetpointReachesTheRun:
    def test_the_rigs_setpoint_becomes_the_plans(self):
        plan, _ = to_sequence_plan(compile_plan(_graph(), "n"), cool_to=-5.0)
        assert plan.cool_to == -5.0

    def test_no_configured_setpoint_leaves_the_run_unchanged(self):
        """A rig with no setpoint has expressed no intent to cool. Inventing one
        would be picking a number on the operator's behalf — and every plan that
        ran before this feature existed must still behave byte-identically."""
        plan, _ = to_sequence_plan(compile_plan(_graph(), "n"))
        assert plan.cool_to is None
        plan2, _ = to_sequence_plan(compile_plan(_graph(), "n"), cool_to=None)
        assert plan2.cool_to is None

    def test_a_warm_setpoint_is_carried_too(self):
        """Not everyone cools below zero, and some rigs deliberately sit at
        ambient. The field is the operator's number, not a sign test."""
        plan, _ = to_sequence_plan(compile_plan(_graph(), "n"), cool_to=15.0)
        assert plan.cool_to == 15.0

    def test_zero_is_a_setpoint_and_not_an_absence(self):
        """0 °C is a real target. A truthiness check here would silently drop
        it — the same class of bug as treating None as 'no'."""
        plan, _ = to_sequence_plan(compile_plan(_graph(), "n"), cool_to=0.0)
        assert plan.cool_to == 0.0

    def test_every_shipped_example_carries_it(self):
        for ex in examples():
            plan, _ = to_sequence_plan(compile_plan(ex.graph, ex.name),
                                       ex.graph, cool_to=-5.0)
            assert plan.cool_to == -5.0, f"{ex.id} would shoot uncooled"


class TestTheRouteActuallyPassesIt:
    def test_EVERY_call_site_passes_the_setpoint(self):
        """A parameter nothing passes is a dead control — the exact class this
        project keeps finding. The unit tests above all pass with the routes
        never using it, so this checks the call sites themselves.

        EVERY site, not the first one: writing this against a single match is
        what exposed the real bug. The preview route compiled WITHOUT the
        setpoint while the run compiled with it, so the PLAN tab would have
        rendered a night that differed from the one that ran — the same
        preview-outruns-the-plan shape as the park defect, one layer up.

        Matched on the keyword within a window, so ordinary reformatting passes
        and deletion fails.
        """
        import pathlib
        import re

        import astrodeck.api.app as app_mod
        src = pathlib.Path(app_mod.__file__).read_text(encoding="utf-8")
        sites = [m.start() for m in re.finditer(r"to_sequence_plan\(", src)]
        assert len(sites) >= 2, (
            f"expected the compile and run routes to both build a plan; "
            f"found {len(sites)} call site(s)")
        for i in sites:
            window = src[i:i + 400]
            assert "cool_to=" in window, (
                f"a to_sequence_plan call at offset {i} does not pass cool_to — "
                f"if it is the run, the night shoots at whatever the sensor "
                f"drifted to; if it is the preview, it shows a different night")
            assert "setpoint_c" in window, (
                f"the call at offset {i} no longer sources cool_to from the "
                f"rig's standing setpoint")
