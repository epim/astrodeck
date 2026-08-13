"""The CLOUD WATCH threshold is a dial wired to nothing, and now it says so.

THE DEFECT. `_eval_predicate` reads `threshold` for the MEASURED triggers —
`hfr_above`, `guide_rms_above` — and ignores it for the four that answer with a
verdict somebody else already reached. `clouds_in` returns `ctx.cloudy`, full
stop. But the CLOUD WATCH node offers a threshold, the compiler carries it into
the rule, `to_sequence_plan` copies it into the plan, and the PLAN tab renders
it. An operator can set that dial to 10 or to 90 and the night behaves
identically.

That is the house's dominant shape exactly: a control that accepts a value,
echoes it back, and is consulted by nothing. It was NOT in the unmapped list, so
it failed silently — which is the half that makes this class expensive.

NOT WIRED, DELIBERATELY. The node's dial is a 0-100 number; the detector's
answer is a boolean it reaches from bright-star density and contrast, calibrated
against a real cloudy frame. Inventing a mapping between the two would swap a
validated decision for a guess, and the flows handoff's rule is to report
ambiguity rather than resolve it.
"""
from __future__ import annotations

import pytest

from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.to_plan import BOOLEAN_TRIGGERS, to_sequence_plan
from astrodeck.sequence.instructions import _eval_predicate, TriggerContext


def _n(nid, ntype, x=0.0, y=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=y, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def _cloud_flow(threshold=40):
    return FlowGraph(
        nodes=[_n("t", "target", name="M31", ra="00h 42m 44s", dec="+41 16 09"),
               _n("c", "capture", x=100, exposure=180, count=10),
               _n("cw", "cloudwatch", y=300, threshold=threshold),
               _n("h", "holdresume", x=200, y=300)],
        edges=[_e("t", "target", "c", "run"),
               _e("cw", "in", "h", "pause")])


class TestTheEvaluatorReallyIgnoresIt:
    """The premise, asserted rather than assumed — if this ever stops being
    true, the note below becomes the lie instead."""

    @pytest.mark.parametrize("kind", ["clouds_in", "unsafe", "panel_ready"])
    @pytest.mark.parametrize("threshold", [0.0, 40.0, 99.0])
    def test_a_verdict_trigger_answers_the_same_at_any_threshold(
            self, kind, threshold):
        ctx = TriggerContext(now_ts=0.0, cloudy=True, unsafe=True,
                             panel_ready=True)
        assert _eval_predicate(kind, threshold, None, ctx) is True

    def test_a_measured_trigger_genuinely_uses_it(self):
        """The contrast that makes the point: same call shape, and here the
        number decides."""
        ctx = TriggerContext(now_ts=0.0, frame_hfr=3.0)
        assert _eval_predicate("hfr_above", 2.0, None, ctx) is True
        assert _eval_predicate("hfr_above", 4.0, None, ctx) is False


class TestTheLossIsReported:
    def test_a_cloud_threshold_is_named_in_the_unmapped_list(self):
        _, un = to_sequence_plan(compile_plan(_cloud_flow(40), "n"))
        note = [u for u in un if u["key"] == "instructions[on_clouds_in].threshold"]
        assert note, "the dead dial is dropped silently"
        assert "changes nothing" in note[0]["detail"]

    def test_the_note_quotes_the_operators_own_number_back(self):
        """A note saying "some threshold is ignored" makes the operator hunt for
        it. Naming the value they typed points straight at the control."""
        _, un = to_sequence_plan(compile_plan(_cloud_flow(85), "n"))
        note = [u for u in un
                if u["key"] == "instructions[on_clouds_in].threshold"]
        assert note and "85" in note[0]["detail"]

    def test_the_threshold_still_reaches_the_plan(self):
        """Reported, not stripped. The PLAN tab renders the compiled plan
        verbatim, and removing the field would hide the evidence for the very
        note above."""
        plan, _ = to_sequence_plan(compile_plan(_cloud_flow(40), "n"))
        cloud = [i for i in plan.instructions if i.trigger == "on_clouds_in"]
        assert cloud and cloud[0].threshold == 40.0

    def test_a_measured_rule_is_NOT_reported(self):
        """The note must stay rare enough to be read. An HFR rule's threshold
        does reach the engine, so flagging it would train operators to skim
        past the list — which is how a real loss goes unnoticed."""
        g = FlowGraph(
            nodes=[_n("t", "target", name="M31", ra="00h 42m 44s",
                      dec="+41 16 09"),
                   _n("c", "capture", x=100, exposure=180, count=10),
                   _n("cond", "condition", y=300, when="HFR above", threshold=3.2),
                   _n("nf", "notify", x=200, y=300)],
            edges=[_e("t", "target", "c", "run"),
                   _e("cond", "fire", "nf", "do")])
        _, un = to_sequence_plan(compile_plan(g, "n"))
        assert not [u for u in un if u["key"].endswith("].threshold")]


class TestTheSetMatchesTheEvaluator:
    def test_every_boolean_trigger_listed_really_ignores_thresholds(self):
        """The set is a claim about another module. If a trigger listed here
        started reading its threshold, this file's whole premise would be
        wrong and the note would become the false statement."""
        ctx = TriggerContext(now_ts=0.0, cloudy=True, unsafe=True,
                             panel_ready=True)
        for trigger in BOOLEAN_TRIGGERS:
            kind = trigger.removeprefix("on_")
            lo = _eval_predicate(kind, 0.0, None, ctx)
            hi = _eval_predicate(kind, 1e9, None, ctx)
            assert lo == hi, f"{kind} reads its threshold after all"
