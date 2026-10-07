# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
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

THE MOSAIC RULES (S3, spec 1.8) MAKE TWO MORE CLAIMS ABOUT THE ENGINE, and the
last two classes hold each one against the code it describes rather than
against a restatement: M9 says Run will refuse a plan, so it is graded against
``quota_unbounded`` on the plan ``to_plan`` builds; M13 says a stage will shoot
nothing, so it is graded against the steps ``compile_plan`` gives each target.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from astrodeck.flows import NODE_DEFS, RigFacts, check
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.to_plan import (HOLD_HONOURED, REDUNDANT_PORTS,
                                     to_sequence_plan)
from astrodeck.sequence.models import quota_unbounded

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


def _n(nid: str, ntype: str, x: float = 0.0, **params) -> FlowNode:
    return FlowNode(id=nid, type=ntype, x=float(x), params=params)


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
        assert [t for t in _texts(g) if "will not have a saved report" in t], _texts(g)

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


class TestTheExamplesUnderTheMosaicRules:
    """S3 (spec 1.7, 1.8): the Examples gain nothing above note, and the only
    notes they gain are L1's, one per legacy SLEW + CENTER they still draw.

    RECORDED 2026-09-25, before S3-W redraws the Examples: all seven carry one
    SLEW + CENTER (node n4 in each of example-m31, -m16, -pool, -nb, -eaa,
    -campaign and -cycle), so each draws exactly one L1 note: "\u25b8 SLEW +
    CENTER - this stage is part of the TARGET block now. Its 0.5 arcmin
    tolerance never reached the run, which centred to 1.2 arcmin. Delete it
    and set centring on the TARGET." Those seven notes are expected until S3-W
    drops SLEW from the Examples, and this class needs no edit when it does:
    it counts the SLEW nodes each graph has rather than pinning seven."""

    @pytest.mark.parametrize("rig", [None, RigFacts()],
                             ids=["no-rig", "rig-knows-nothing"])
    def test_nothing_above_note(self, rig):
        """`example-eaa` is left out for the reason the test above gives.
        Mutant "M1 on any TARGET" turned both red: ``AssertionError:
        example-m31 now draws warnings: [('danger', "\u25b8 TARGET M31 -
        Andromeda - frame this block: panels are tiled from the camera's
        field, and this block has not recorded one.")]``; "M2 on any TARGET"
        the same way on example-m16."""
        from astrodeck.flows import examples as ex
        for rec in ex.examples():
            if rec.id == "example-eaa":
                continue
            warns = [(i.level, i.text) for i in check(rec.graph, rig=rig)
                     if i.level != "note"]
            assert not warns, f"{rec.id} now draws warnings: {warns}"

    def test_the_only_new_notes_are_one_l1_per_slew(self):
        """Mutant "drop L1" turned this red: ``AssertionError:
        example-campaign draws 0 L1 notes for 1 SLEW``. Mutant "L1 on every
        graph" (one note per graph, not per SLEW) left it green, because
        every Example carries exactly one SLEW today; the S3 file's unchanged
        corpus is what catches that one."""
        from astrodeck.flows import examples as ex
        ledger = '▸ no session report stage - the night will not have a saved report'
        for rec in ex.examples():
            notes = [i.text for i in check(rec.graph) if i.level == "note"]
            l1 = [t for t in notes if "part of the TARGET block now" in t]
            slews = [n for n in rec.graph.nodes if n.type == "slew"]
            assert len(l1) == len(slews), (
                f"{rec.id} draws {len(l1)} L1 notes for {len(slews)} SLEW")
            assert all(t == ledger for t in notes if t not in l1), (
                rec.id, notes)


class TestM9AgreesWithTheRunsRefusal:
    """M9 says "Run will refuse it". Run refuses with ``quota_unbounded``, on
    the plan ``to_plan`` builds from the compile, so the doctor's warning is
    graded against exactly that, with both reject guards off as the rig fact
    says. A DUSK stop the compile cannot map ("Clock time" today) is the case
    a restated rule would get wrong."""

    GUARDS_OFF = SimpleNamespace(max_consecutive_rejects=0,
                                 max_consecutive_rejects_night=0)

    def _flow(self, stop, counts: str) -> FlowGraph:
        nodes = [_n("t", "target", 200, name="M31", ra="00h 42m 44s",
                    dec="+41 16 09", counts=counts),
                 _n("c", "capture", 400, exposure=60, count=5)]
        edges = [_e("t", "target", "c", "run")]
        if stop is not None:
            nodes.append(_n("d", "dusk", 0, stop=stop))
            edges.append(_e("d", "window", "t", "arm"))
        return FlowGraph(nodes=nodes, edges=edges)

    @pytest.mark.parametrize("counts", ["Accepted subs", "Every sub taken"])
    @pytest.mark.parametrize("stop", ["Dawn", "None", "Clock time", None],
                             ids=["dawn", "none", "clock-time", "no-dusk"])
    def test_the_warning_is_the_refusal(self, stop, counts):
        """Observed red, each with this assertion's own message:

        * "drop M9": the accepted none, clock-time and no-dusk cases,
          ``AssertionError: M9 says False, quota_unbounded says True``;
        * "any DUSK is a boundary" (the doctor reads the DUSK node, not the
          compile's schedule): the accepted none and clock-time cases, the
          same message;
        * "M9 ignores the stop": the accepted dawn case, ``AssertionError: M9
          says True, quota_unbounded says False``;
        * "M9 ignores the counts": the three attempts cases that have no
          stop, the same message."""
        graph = self._flow(stop, counts)
        plan, _ = to_sequence_plan(compile_plan(graph), graph)
        refuses = quota_unbounded(plan, self.GUARDS_OFF)
        warned = any("Run will refuse it" in i.text for i in
                     check(graph, rig=RigFacts(reject_guards_off=True)))
        assert warned == refuses, (
            f"M9 says {warned}, quota_unbounded says {refuses}")

    @pytest.mark.parametrize("stop", ["Dawn", "None"])
    def test_a_later_block_that_asks_decides_the_mode(self, stop):
        """The count mode is accepted when ANY block asks for it
        (``to_plan._count_mode``), so the block that asks need not be the
        first one drawn: here M31 counts every sub and M33, after it, asks
        for accepted subs. Mutant "the first block decides" (M9 reads the
        first TARGET or POOL drawn) turned the no-stop case red:
        ``AssertionError: M9 says False, quota_unbounded says True``."""
        nodes = [_n("d", "dusk", 0, stop=stop),
                 _n("t", "target", 200, name="M31", ra="00h 42m 44s",
                    dec="+41 16 09", counts="Every sub taken"),
                 _n("c", "capture", 400, exposure=60, count=5),
                 _n("t2", "target", 600, name="M33", ra="01h 33m 51s",
                    dec="+30 39 37", counts="Accepted subs"),
                 _n("c2", "capture", 800, exposure=60, count=5, filter="R")]
        edges = [_e("d", "window", "t", "arm"), _e("t", "target", "c", "run"),
                 _e("c", "complete", "t2", "arm"),
                 _e("t2", "target", "c2", "run")]
        graph = FlowGraph(nodes=nodes, edges=edges)
        plan, _ = to_sequence_plan(compile_plan(graph), graph)
        refuses = quota_unbounded(plan, self.GUARDS_OFF)
        assert refuses is (stop == "None")
        warned = any("Run will refuse it" in i.text for i in
                     check(graph, rig=RigFacts(reject_guards_off=True)))
        assert warned == refuses, (
            f"M9 says {warned}, quota_unbounded says {refuses}")

    def test_both_answers_are_reached(self):
        """The control for the parametrised case: a grader that only ever saw
        one answer could not tell a rule from a constant."""
        seen = set()
        for stop in ("Dawn", "None"):
            graph = self._flow(stop, "Accepted subs")
            plan, _ = to_sequence_plan(compile_plan(graph), graph)
            seen.add(quota_unbounded(plan, self.GUARDS_OFF))
        assert seen == {True, False}


def _stage(nid: str, filt: str, x: float) -> FlowNode:
    return _n(nid, "capture", x, filter=filt)


class TestM13AgreesWithTheCompile:
    """M13 says a stage "would shoot nothing". The compile is what decides
    which target's steps a stage becomes, so the stages M13 names must be
    exactly the capture and cycle stages no compiled target carries."""

    @staticmethod
    def _label(node: FlowNode) -> str:
        if node.type == "capture":
            return f"CAPTURE LOOP {node.params.get('filter')}"
        return NODE_DEFS[node.type].label

    @staticmethod
    def _mosaic() -> FlowNode:
        return _n("t", "target", 0, name="M31", rows=2, cols=3)

    def _cases(self) -> dict[str, FlowGraph]:
        return {
            # Spec 1.5 worked case 4: DOME ends the lane.
            "dome-ends-the-lane": FlowGraph(
                nodes=[self._mosaic(), _n("cy", "cycle", 200),
                       _n("dm", "dome", 400), _stage("ha", "Ha", 600)],
                edges=[_e("t", "target", "cy", "run"),
                       _e("cy", "complete", "dm", "run"),
                       _e("dm", "open", "ha", "run")]),
            # Worked case 3: the second TARGET owns what follows it.
            "second-target-owns": FlowGraph(
                nodes=[self._mosaic(), _n("cy", "cycle", 200),
                       _n("t2", "target", 400, name="M33"),
                       _stage("ha", "Ha", 600)],
                edges=[_e("t", "target", "cy", "run"),
                       _e("cy", "complete", "t2", "arm"),
                       _e("t2", "target", "ha", "run")]),
            # Worked case 2: both stages are the mosaic's.
            "cycle-then-capture": FlowGraph(
                nodes=[self._mosaic(), _n("cy", "cycle", 200),
                       _stage("ha", "Ha", 400)],
                edges=[_e("t", "target", "cy", "run"),
                       _e("cy", "complete", "ha", "run")]),
            # A stage nothing leads into, and one under a SLEW that nothing
            # leads into, beside a mosaic.
            "orphans": FlowGraph(
                nodes=[self._mosaic(), _n("cy", "cycle", 200),
                       _stage("o3", "OIII", 400), _n("s", "slew", 600),
                       _stage("s2", "SII", 800)],
                edges=[_e("t", "target", "cy", "run"),
                       _e("s", "centered", "s2", "run")]),
            # A POOL owns its lane in a graph with a mosaic.
            "pool-lane": FlowGraph(
                nodes=[self._mosaic(), _n("cy", "cycle", 200),
                       _n("p", "pool", 400), _stage("ha", "Ha", 600)],
                edges=[_e("t", "target", "cy", "run"),
                       _e("p", "target", "ha", "run")]),
        }

    @pytest.mark.parametrize("case", ["dome-ends-the-lane",
                                      "second-target-owns",
                                      "cycle-then-capture", "orphans",
                                      "pool-lane"])
    def test_m13_names_exactly_the_stages_the_compile_drops(self, case):
        """Observed red:

        * "set walk (M13)" (a stage counts as owned when any TARGET or POOL
          is upstream along wires, ``_flow_upstream_types``):
          ``AssertionError: dome-ends-the-lane: the doctor names [], the
          compile drops ['CAPTURE LOOP Ha']``;
        * "drop M13": that, and ``orphans: the doctor names [], the compile
          drops ['CAPTURE LOOP OIII', 'CAPTURE LOOP SII']``;
        * "M13 owner must be a mosaic": ``second-target-owns: the doctor
          names ['CAPTURE LOOP Ha'], the compile drops []``, and pool-lane the
          same way."""
        graph = self._cases()[case]
        compiled = compile_plan(graph)
        carried = {s.get("node_id") for t in compiled["targets"]
                   for s in t["steps"]}
        dropped = sorted(self._label(n) for n in graph.nodes
                         if n.type in ("capture", "cycle")
                         and n.id not in carried)
        named = sorted(i.text.removeprefix("\u25b8 ").split(
            " belongs to no TARGET")[0] for i in check(graph)
            if "belongs to no TARGET" in i.text)
        assert named == dropped, (
            f"{case}: the doctor names {named}, the compile drops {dropped}")

    def test_the_cases_reach_both_answers(self):
        """At least one case drops a stage and at least one drops none."""
        outcomes = set()
        for graph in self._cases().values():
            compiled = compile_plan(graph)
            carried = {s.get("node_id") for t in compiled["targets"]
                       for s in t["steps"]}
            outcomes.add(any(n.type in ("capture", "cycle")
                             and n.id not in carried for n in graph.nodes))
        assert outcomes == {True, False}
