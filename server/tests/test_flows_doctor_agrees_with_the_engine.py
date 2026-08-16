"""The doctor must not demand a wire the adapter calls redundant.

Found by running a real flow on the rig (0.2.76) and then re-reading it after
the 2026-08-15 reclassification work. Four claims in `doctor.check` had gone
stale against the engine, and two of those contradictions I introduced myself
the day before by moving ports into `to_plan.REDUNDANT_PORTS`:

    doctor rule 1   "CALIBRATION QUEUE - 'do' input unwired"
                    "CALIBRATION QUEUE - 'stop' input unwired"
                    "HOLD / RESUME - 'resume' input unwired"
    doctor rule 11  "campaign repeats nightly but nothing advances the POOL -
                     wire SESSION REPORT 'target done' -> 'advance'"
    doctor rule 12  "campaign has no shutdown lane - wire 'night ends' ->
                     PARK + CLOSE"

against, in `to_plan.REDUNDANT_PORTS`:

    ("holdresume", "resume")  "the hold releases itself when the sky clears"
    ("pool", "advance")       "the scheduler advances the pool itself"
    ("parkclose", "do")       "the night already ends parked with the dust
                               cover shut, whether or not this wire is here"

Whichever is right they cannot both be, and an operator who follows the doctor's
advice ends up drawing a wire the very next panel tells them does nothing.

VERIFIED AGAINST THE CODE, NOT ASSUMED. `plan_extras` sets `cloud_hold_darks`
from the QUEUE'S QUOTA alone — the node merely existing — so an unwired queue
really does top the library up during a weather hold. (`day_darks` is the
exception and is deliberately keyed on the operator's own wire, which is why
`do` being optional does not make the shutdown lane meaningless.)

THE DURABLE PART IS THE STRUCTURAL CHECK BELOW. Fixing three messages is worth
little; asserting that no port can be in `REDUNDANT_PORTS` and simultaneously
demanded by the doctor is what stops the next reclassification from re-opening
the same gap silently.
"""
from __future__ import annotations

import pytest

from astrodeck.flows import NODE_DEFS, check
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.to_plan import HOLD_HONOURED, REDUNDANT_PORTS

#: Every (node type, input port) either table says the engine already answers.
#: BOTH tables, because the first version of this check walked only
#: `REDUNDANT_PORTS` and a sabotage proved the blind spot: the calibration
#: queue's `do`/`stop` live in `HOLD_HONOURED`, so putting them back to REQUIRED
#: re-opened the contradiction and the structural check said nothing.
#: `REDUNDANT_PORTS` is keyed (node, port); `HOLD_HONOURED` is keyed
#: (trigger, action, port) where `action` is the node type.
ANSWERED_ELSEWHERE: set[tuple[str, str]] = (
    set(REDUNDANT_PORTS)
    | {(action, port) for _trigger, action, port in HOLD_HONOURED}
)


def _n(nid: str, ntype: str, **params) -> FlowNode:
    return FlowNode(id=nid, type=ntype, params=params)


def _e(src: str, sp: str, dst: str, dp: str) -> FlowEdge:
    return FlowEdge(**{"from": src, "fromPort": sp, "to": dst, "toPort": dp})


def _texts(graph: FlowGraph) -> list[str]:
    return [i.text for i in check(graph)]


class TestTheTwoTablesCannotContradictEachOther:
    """The structural guard. Everything else in this file is an instance."""

    def test_every_answered_port_is_exempt_from_the_unwired_rule(self):
        for (ntype, port) in sorted(ANSWERED_ELSEWHERE):
            d = NODE_DEFS.get(ntype)
            assert d is not None, f"the tables name no such node: {ntype}"
            if d.port(port, "in") is None:
                continue        # an output-side entry; rule 1 cannot fire on it
            assert port in d.optional_ins, (
                f"to_plan says {ntype}.{port} is already answered by the engine "
                f"but it is a REQUIRED input, so doctor rule 1 tells the "
                f"operator to wire it. The two disagree about the same wire.")

    def test_the_check_covers_both_tables(self):
        """The first version of the check above walked REDUNDANT_PORTS only, and
        a sabotage proved the blind spot: the calibration queue's ports live in
        HOLD_HONOURED, so putting them back to REQUIRED re-opened the
        contradiction while the structural check stayed green."""
        assert ("calib", "do") in ANSWERED_ELSEWHERE
        assert ("calib", "stop") in ANSWERED_ELSEWHERE
        assert ("holdresume", "resume") in ANSWERED_ELSEWHERE
        assert ("parkclose", "do") in ANSWERED_ELSEWHERE

    def test_the_exemptions_are_real_ports(self):
        """A typo in `optional_ins` exempts nothing and the doctor goes on
        demanding a wire the operator cannot see is optional."""
        for t, d in NODE_DEFS.items():
            for pid in d.optional_ins:
                assert d.port(pid, "in") is not None, f"{t}.{pid} is not an input"


class TestTheCalibrationQueueNeedsNoWire:
    def _queue_only(self) -> FlowGraph:
        return FlowGraph(nodes=[_n("q", "calib", flats="Skip", quota=20)],
                         edges=[])

    @pytest.mark.parametrize("port", ["do", "stop"])
    def test_it_is_not_reported_as_unwired(self, port):
        bad = [t for t in _texts(self._queue_only())
               if "CALIBRATION QUEUE" in t and f"'{port}'" in t]
        assert not bad, (
            f"the doctor still tells you to wire the queue's {port!r}, but "
            f"`plan_extras` funds cloud-hold darks from the queue's quota "
            f"alone: {bad}")

    def test_the_queue_alone_draws_no_complaint_at_all(self):
        """A CALIBRATION QUEUE dropped on the canvas with flats off is a
        complete, working thing. Anything said about it here is noise."""
        assert not [t for t in _texts(self._queue_only())
                    if "CALIBRATION QUEUE" in t or "QUEUE" in t]

    def test_flats_without_a_panel_is_STILL_reported(self):
        """The positive control for rule 7. `panel` stays meaningful: a queue
        that wants flats and has no panel really does skip them."""
        g = FlowGraph(nodes=[_n("q", "calib", flats="If stale + panel wired",
                                quota=20)], edges=[])
        assert [t for t in _texts(g) if "panel" in t], _texts(g)


class TestTheHoldReleasesItself:
    def test_resume_is_not_reported_as_unwired(self):
        g = FlowGraph(nodes=[_n("h", "holdresume")], edges=[])
        bad = [t for t in _texts(g) if "'resume'" in t]
        assert not bad, (
            f"the doctor demands a 'resume' wire that to_plan calls redundant: "
            f"{bad}")

    def test_pause_is_STILL_required(self):
        """The positive control. A hold with nothing wired to `pause` never
        triggers, and that IS a broken graph."""
        g = FlowGraph(nodes=[_n("h", "holdresume")], edges=[])
        assert [t for t in _texts(g) if "'pause'" in t], _texts(g)


class TestTheCampaignRulesAreGone:
    """Rules 11 and 12 told a campaign to draw the two wires that
    `REDUNDANT_PORTS` documents as doing nothing."""

    def _campaign(self, *, with_pool: bool) -> FlowGraph:
        nodes = [_n("d", "dusk", repeat="Every clear night")]
        if with_pool:
            nodes.append(_n("p", "pool"))
        return FlowGraph(nodes=nodes, edges=[])

    def test_no_advice_to_wire_the_pools_advance(self):
        bad = [t for t in _texts(self._campaign(with_pool=True))
               if "advance" in t.lower()]
        assert not bad, (
            f"the doctor still tells a campaign to wire POOL 'advance', which "
            f"to_plan says the scheduler does by itself: {bad}")

    def test_no_advice_to_add_a_shutdown_lane(self):
        bad = [t for t in _texts(self._campaign(with_pool=False))
               if "shutdown lane" in t.lower() or "PARK + CLOSE" in t]
        assert not bad, (
            f"the doctor still tells a campaign to wire a shutdown lane, but "
            f"every flow's plan carries park-when-done and the wind-down closes "
            f"the cover: {bad}")


class TestTheDoctorStillHasTeeth:
    """Quieting a class of advice is one edit away from quieting the advice that
    matters. These are the rules that must survive."""

    def test_a_genuinely_required_input_is_still_demanded(self):
        g = FlowGraph(nodes=[_n("s", "slew")], edges=[])
        assert [t for t in _texts(g) if "SLEW" in t and "unwired" in t], _texts(g)

    def test_a_dome_with_no_safety_monitor_is_still_a_danger(self):
        g = FlowGraph(nodes=[_n("d", "dome")], edges=[])
        issues = check(g)
        assert any(i.level == "danger" and "DOME" in i.text for i in issues), \
            [(i.level, i.text) for i in issues]

    def test_a_night_with_no_report_sink_is_still_noted(self):
        g = FlowGraph(nodes=[_n("c", "capture")], edges=[])
        assert [t for t in _texts(g) if "ledger" in t], _texts(g)

    def test_the_shipped_examples_stay_quiet(self):
        """The end-to-end control: the carefully built examples must not have
        acquired a new complaint from any of this.

        `example-eaa` is excluded for the same documented reason
        `test_flows_store` excludes it — it starts at TARGET with no DUSK
        WINDOW above it, so its 'arm' really is unwired, deliberately, and the
        header chip reading "1 OPEN CHECK" is the honest render."""
        from astrodeck.flows import examples as ex
        for rec in ex.examples():
            if rec.id == "example-eaa":
                continue
            warns = [i.text for i in check(rec.graph) if i.level != "note"]
            assert not warns, f"{rec.id} now draws warnings: {warns}"
