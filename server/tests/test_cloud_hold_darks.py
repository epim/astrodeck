"""Calibration during a weather hold.

A hold is dead time with a cooled sensor under a closed sky, which is exactly
the condition darks want — so the hold spends it building the library. The value
is in the MATCHING: a dark is indexed by exposure, gain, offset, binning and
temperature, and a dark that differs in any of those is a dark for a different
night.

The tests that matter here are the bounds. An unbounded version of this feature
fills the disk on a cloudy night and leaves no time to notice the sky cleared.
"""
from __future__ import annotations

from astrodeck.flows.compile import compile_plan
from astrodeck.flows.examples import examples
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.to_plan import plan_extras, to_sequence_plan
from astrodeck.sequence.models import SequencePlan


def _n(nid, ntype, x=0.0, y=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=y, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


class TestTheQueueReachesThePlan:
    def test_a_calibration_queue_sets_cloud_hold_darks(self):
        g = FlowGraph(nodes=[_n("q", "calib", quota=20),
                             _n("t", "target", x=100, name="M31",
                                ra="00h 42m 44s", dec="+41 16 09"),
                             _n("c", "capture", x=200, exposure=180, count=10)],
                      edges=[_e("t", "target", "c", "run")])
        plan, _ = to_sequence_plan(compile_plan(g, "n"))
        assert plan.cloud_hold_darks == 20

    def test_the_m16_example_carries_its_own_quota(self):
        m16 = next(e for e in examples() if e.id == "example-m16")
        plan, _ = to_sequence_plan(compile_plan(m16.graph, m16.name), m16.graph)
        assert plan.cloud_hold_darks == 20

    def test_no_queue_means_no_darks(self):
        """Every existing plan, and every flow without a CALIBRATION QUEUE node,
        must behave byte-identically."""
        g = FlowGraph(nodes=[_n("t", "target", name="M31", ra="00h 42m 44s",
                                dec="+41 16 09"),
                             _n("c", "capture", x=100, exposure=60, count=5)],
                      edges=[_e("t", "target", "c", "run")])
        plan, _ = to_sequence_plan(compile_plan(g, "n"))
        assert plan.cloud_hold_darks == 0
        assert SequencePlan(name="bare").cloud_hold_darks == 0

    def test_a_hold_TOPS_UP_the_library_it_does_not_fill_it(self):
        """A quota is what the library wants in total. A hold is bounded - 45
        minutes by default - and cannot deliver a whole quota of long darks, so
        what it takes is capped. Promising the full quota would be a claim the
        clock cannot keep."""
        assert plan_extras({"automation": {"calibration_queue": {"quota": 500}}}) \
            ["cloud_hold_darks"] == 40

    def test_a_zero_or_missing_quota_asks_for_nothing(self):
        # ASKS FOR NOTHING = the key is absent, not zero. `plan_extras` also
        # carries the end-of-night park/warm fields, so these assert on the
        # darks key alone rather than on the whole dict - an equality against
        # {} would fail the day any other plan field earns its way in, which is
        # a test that breaks on unrelated correct work.
        for compiled in ({"automation": {"calibration_queue": {"quota": 0}}},
                         {"automation": {"calibration_queue": {}}},
                         {"automation": {}},
                         {}):
            assert "cloud_hold_darks" not in plan_extras(compiled)

    def test_a_garbled_quota_does_not_raise_or_smuggle_a_value(self):
        for junk in ("twenty", None):
            assert "cloud_hold_darks" not in plan_extras(
                {"automation": {"calibration_queue": {"quota": junk}}})


class TestTheModelBounds:
    def test_the_field_refuses_a_negative_or_absurd_count(self):
        import pytest
        with pytest.raises(Exception):
            SequencePlan(name="x", cloud_hold_darks=-1)
        with pytest.raises(Exception):
            SequencePlan(name="x", cloud_hold_darks=10_000)

    def test_it_is_an_int_so_the_intent_is_visible_in_the_plan(self):
        """The PLAN tab renders the compiled plan verbatim. A boolean would say
        "some darks"; the number says how many, which is what bounds the cost."""
        p = SequencePlan(name="x", cloud_hold_darks=25)
        assert p.model_dump()["cloud_hold_darks"] == 25


class TestTheQueueStillReportsWhatItCannotDo:
    def test_the_note_names_the_legs_that_are_still_missing(self):
        """Half a feature reported as a whole one is the broken-promise class.
        The darks leg works; order, the if-stale policy, bias and flats do not,
        and the note has to keep saying so."""
        m16 = next(e for e in examples() if e.id == "example-m16")
        _, un = to_sequence_plan(compile_plan(m16.graph, m16.name), m16.graph)
        note = [u for u in un if u["key"] == "automation.calibration_queue"]
        assert note, "the queue must still be reported"
        d = note[0]["detail"]
        assert "darks will be taken" in d
        for missing in ("order", "if-stale", "bias", "flat"):
            assert missing in d, f"the note stops mentioning {missing}"
