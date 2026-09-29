"""The doctor reads a pass wire from every type the vocabulary gives the port
(#375; spec 2026-09-23 flows mosaic, 1.3, 1.4 item 4, 1.5 items 5-6, 1.8 M3,
M4 and M12).

S4 gave AUTOFOCUS and GUIDE the ``pass`` output (#331), and the compile read
it at once: ``compile.PASS_TYPES`` is read off the vocabulary, so
``_trigger_for``, ``one_panel_pass_wires`` and ``lane_refusals`` all took the
two new types in. The doctor kept its own list, ``_CAPTURE_TYPES`` (CAPTURE
LOOP and FILTER CYCLE), and built its pass wires from it, so three of its
rules read a graph the compile no longer reads:

* M3 told the operator a lane ending on an AUTOFOCUS or GUIDE had no 'pass
  done' to wire, and to end it on a capture stage instead, although the wire
  could be drawn from the stage it named;
* M12 was missing for a pass wire from an AUTOFOCUS or GUIDE mid-lane, a
  graph ``lane_refusals`` refuses and Run will not start, so the doctor
  called runnable with a warning a flow Run refuses;
* M4 was silent on such a wire from another block's lane, which the compile
  emits as a rule that will not run.

The doctor now builds its pass wires from ``compile.PASS_TYPES``, the one
list, and keeps the no-port sentence for the one lane type left without the
port, legacy SLEW.

Every mutant below was run in a private scratch copy of ``server/``
(``s5-compile-mut``) from a byte backup of ``doctor.py``, never in the shared
tree (#254), and the failure it produced is quoted verbatim in the test it
turned red. The mutant this file is built around is "_CAPTURE_TYPES
restored": the pass wires and M3's tail test back on ``_CAPTURE_TYPES``, as
#375 found them.
"""
from __future__ import annotations

import pytest

from astrodeck.flows import check
from astrodeck.flows.compile import (
    LANE_TYPES, PASS_TYPES, compile_plan, lane_refusals)
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.nodes import NODE_DEFS
from astrodeck.flows.to_plan import GraphNotRunnable, to_sequence_plan

M31_RA = "00h 42m 44s"
M31_DEC = "+41 16 09"

M3 = "panels are shot one after another"
M4_ELSEWHERE = "this wire does nothing"
M4_ONE_PANEL = "one panel, nothing to rotate between"
M12_MID = "the loop wire starts at"

#: The two lane types #331 gave the port.
NEW_PASS_TYPES = ("autofocus", "guide")


def _n(nid: str, ntype: str, x: float = 0.0, y: float = 0.0,
       **params) -> FlowNode:
    return FlowNode(id=nid, type=ntype, x=float(x), y=float(y), params=params)


def _e(src: str, sp: str, dst: str, dp: str, eid: str | None = None
       ) -> FlowEdge:
    kw = {"from": src, "fromPort": sp, "to": dst, "toPort": dp}
    if eid is not None:
        kw["id"] = eid
    return FlowEdge(**kw)


def _block(nid: str = "t", x: float = 0, **over) -> FlowNode:
    """The baseline 3x2 of ``test_flows_doctor_s3.py``: framed, locked at PA
    30, counting accepted subs, so M1, M2 and M7 stay silent."""
    params = {"name": "M31", "ra": M31_RA, "dec": M31_DEC, "rows": 2,
              "cols": 3, "overlap": 25, "fovX": 2.0, "fovY": 1.33,
              "angle": "Rotate to PA", "rotation": 30,
              "counts": "Accepted subs"}
    params.update(over)
    return _n(nid, "target", x, 0, **params)


#: The output port each lane type's flow leaves by, so a lane can be drawn
#: through any of them.
_FLOW_OUT = {t: next(p.id for p in NODE_DEFS[t].outs if p.kind == "flow")
             for t in LANE_TYPES}


def _lane(*types: str, loop_from: str | None = None, **block) -> FlowGraph:
    """TARGET M31 (3x2) -> FILTER CYCLE -> each of ``types`` in turn, the
    stages named s1, s2, ... after the cycle, and a pass wire from the stage
    ``loop_from`` names (a node id) into the block's 'next panel'."""
    nodes = [_block(**block), _n("cy", "cycle", 200,
                                 plan="L 60, R 60, G 60, B 60")]
    edges = [_e("t", "target", "cy", "run")]
    prev = "cy"
    for i, ntype in enumerate(types, start=1):
        nid = f"s{i}"
        params = {"filter": "Ha", "exposure": 300} if ntype == "capture" else {}
        nodes.append(_n(nid, ntype, 200 + 200 * i, **params))
        edges.append(_e(prev, _FLOW_OUT[_type(nodes, prev)], nid, "run"))
        prev = nid
    if loop_from is not None:
        edges.append(_e(loop_from, "pass", "t", "next", "loop"))
    return FlowGraph(nodes=nodes, edges=edges)


def _type(nodes: list[FlowNode], nid: str) -> str:
    return next(n.type for n in nodes if n.id == nid)


def _hits(issues, marker: str):
    return [i for i in issues if marker in i.text]


def _one(issues, marker: str):
    got = _hits(issues, marker)
    assert len(got) == 1, (marker, [(i.level, i.text) for i in issues])
    return got[0]


def _label(ntype: str) -> str:
    return NODE_DEFS[ntype].label


def test_the_premise_autofocus_and_guide_have_the_port_and_slew_does_not():
    """THE PREMISE the three rules below stand on, read off the vocabulary:
    every lane type has the ``pass`` port but legacy SLEW (spec 1.3, 1.5
    item 6). If a type lost or gained it, the cases below would be about a
    vocabulary that no longer exists, so this says so first."""
    assert set(NEW_PASS_TYPES) <= PASS_TYPES
    assert LANE_TYPES - PASS_TYPES == {"slew"}
    for t in NEW_PASS_TYPES:
        assert NODE_DEFS[t].port("pass", "out").label == "pass done"


class TestM3NamesTheWire:
    @pytest.mark.parametrize("tail", NEW_PASS_TYPES)
    def test_a_lane_ending_on_an_autofocus_or_a_guide_is_told_to_wire_it(
            self, tail):
        """The stage has 'pass done', so M3 names the wire to draw from it,
        as it does for a capture stage. RE-PINNED on purpose from the
        sentence #375 found false ("The panel lane ends at GUIDE, which has
        no 'pass done'; end it on a FILTER CYCLE or CAPTURE LOOP ..."):
        ``test_flows_doctor_s3.py::TestTheWordsAtTheEdges::
        test_m3_on_a_lane_that_ends_on_guide`` moves with it.

        RED under mutant "_CAPTURE_TYPES restored", observed on both, e.g.
        autofocus (the diff line cut):

            E       assert ('warn', '\\u25b8 T... every pass.') == ('warn', '\\u25b8 T... every pass.')
            E         At index 1 diff: "\\u25b8 TARGET M31 - panels are shot one after another: a night cut short leaves the last panels empty. The panel lane ends at AUTOFOCUS, which has no 'pass done'; end it on a FILTER CYCLE or CAPTURE LOOP and wire that to TARGET 'next panel' to rotate panels every pass." != ...
        """
        hit = _one(check(_lane(tail)), M3)
        assert (hit.level, hit.text) == (
            "warn", f"▸ TARGET M31 - panels are shot one after another: a "
            f"night cut short leaves the last panels empty. Wire "
            f"{_label(tail)} 'pass done' to TARGET 'next panel' to rotate "
            f"panels every pass.")

    def test_a_lane_ending_on_a_legacy_slew_keeps_the_no_port_sentence(self):
        """Legacy SLEW is the one lane type with no 'pass done' (spec 1.5
        item 6), so for it alone M3 says there is no wire to draw from the
        tail. It took over this pin from the GUIDE case above.

        RED under mutant "M3 always names a wire" (the no-port branch
        deleted, so every tail is told to wire its 'pass done'), observed:

            E       AssertionError: assert '\\u25b8 TARGET M31...s every pass.' == '\\u25b8 TARGET M31...s every pass.'
            E         Skipping 88 identical leading characters in diff, use -v to show
            E         - ls empty. The panel lane ends at SLEW + CENTER, which has no 'pass done'; end it on a FILTER CYCLE or CAPTURE LOOP and wire that to TARGET 'next panel' to rotate panels every pass.
            E         + ls empty. Wire SLEW + CENTER 'pass done' to TARGET 'next panel' to rotate panels every pass.
        """
        hit = _one(check(_lane("slew")), M3)
        assert hit.text == (
            "▸ TARGET M31 - panels are shot one after another: a night "
            "cut short leaves the last panels empty. The panel lane ends at "
            "SLEW + CENTER, which has no 'pass done'; end it on a FILTER "
            "CYCLE or CAPTURE LOOP and wire that to TARGET 'next panel' to "
            "rotate panels every pass.")

    @pytest.mark.parametrize("tail", sorted(LANE_TYPES))
    def test_the_advice_follows_the_vocabulary_for_every_lane_type(self, tail):
        """One rule for every lane type, read off ``PASS_TYPES``: a tail with
        the port is told to wire it, the one without is told it has none.
        A type that gains or loses the port moves its sentence with it.

        RED under mutant "_CAPTURE_TYPES restored" on autofocus and guide
        (capture, cycle and slew stay green), observed, e.g. guide:

            E       AssertionError: guide

        RED under mutant "M3 always names a wire" on slew alone, observed:

            E       AssertionError: slew
        """
        text = _one(check(_lane(tail)), M3).text
        named = f"Wire {_label(tail) if tail != 'capture' else 'CAPTURE LOOP Ha'} 'pass done'"
        assert (named in text) == (tail in PASS_TYPES), tail
        assert ("which has no 'pass done'" in text) == (tail not in PASS_TYPES)

    @pytest.mark.parametrize("tail", NEW_PASS_TYPES)
    def test_control_the_tails_own_loop_wire_silences_it(self, tail):
        """CONTROL: the loop wire from the AUTOFOCUS or GUIDE tail is the
        block's own loop wire (``loop_wires``), so M3, M4 and M12 have
        nothing to say, and the compile makes the group rotate. Green on the
        code and under "_CAPTURE_TYPES restored" (M3 reads the compile's
        ``loop_wires``, which match the port as a string)."""
        g = _lane(tail, loop_from="s1")
        issues = check(g)
        for marker in (M3, M4_ELSEWHERE, M4_ONE_PANEL, M12_MID):
            assert not _hits(issues, marker), (marker, issues)
        (entry,) = compile_plan(g, "n")["targets"]
        assert entry["loop"] is True


class TestM12ForAMidLaneWire:
    @pytest.mark.parametrize("stage", NEW_PASS_TYPES)
    def test_a_mid_lane_wire_from_an_autofocus_or_a_guide_is_a_danger(
            self, stage):
        """#375 item 2: FILTER CYCLE -> AUTOFOCUS -> CAPTURE LOOP Ha, the
        loop wire from the AUTOFOCUS. The compile refuses it
        (``lane_refusals`` M12, so ``to_plan`` raises ``GraphNotRunnable``),
        and the doctor now says so at danger level, and M3, which spoke in
        its place, is silent.

        RED under mutant "_CAPTURE_TYPES restored", observed on both, e.g.
        autofocus (M3 in M12's place, the warning #375 quotes):

            E       AssertionError: ('the loop wire starts at', [('warn', "\\u25b8 TARGET - 'arm' input unwired"), ('warn', '\\u25b8 no AUTOFOCUS before the loop - fo... leaves the last panels empty. Wire CAPTURE LOOP Ha 'pass done' to TARGET 'next panel' to rotate panels every pass.")])
        """
        g = _lane(stage, "capture", loop_from="s1")
        issues = check(g)
        hit = _one(issues, M12_MID)
        assert (hit.level, hit.text) == (
            "danger", f"▸ TARGET M31 - the loop wire starts at "
            f"{_label(stage)}, but CAPTURE LOOP Ha comes after it in the "
            f"panel lane. Start the loop wire at CAPTURE LOOP Ha to shoot it "
            f"on every panel, or give CAPTURE LOOP Ha its own TARGET.")
        assert not _hits(issues, M3)
        with pytest.raises(GraphNotRunnable):
            to_sequence_plan(compile_plan(g, "n"), g, flow_id="f-375")

    @pytest.mark.parametrize("stage,after", [
        ("autofocus", ("capture",)), ("guide", ("capture",)),
        ("autofocus", ("guide", "capture")), ("guide", ("autofocus",))],
        ids=["af-then-capture", "guide-then-capture", "af-then-guide-capture",
             "guide-then-af"])
    def test_it_names_the_stages_lane_refusals_names(self, stage, after):
        """The doctor's M12 and the compile's are one finding: the wire's
        source is the stage ``lane_refusals`` refuses, for every lane with a
        mid-lane AUTOFOCUS or GUIDE wire, whatever comes after it.

        RED under mutant "_CAPTURE_TYPES restored", observed on all four,
        e.g. af-then-capture:

            E       AssertionError: assert set() == {'s1'}
        """
        g = _lane(stage, *after, loop_from="s1")
        refused = {r["node_id"] for r in lane_refusals(g) if r["code"] == "M12"}
        assert refused == {"s1"}, "premise: the compile refuses the wire"
        said = [i.text for i in _hits(check(g), M12_MID)]
        named = {n.id for n in g.nodes if n.type in LANE_TYPES
                 and any(f"starts at {self._label_of(n)}," in t for t in said)}
        assert named == refused

    @staticmethod
    def _label_of(node: FlowNode) -> str:
        return (f"CAPTURE LOOP {node.params.get('filter')}"
                if node.type == "capture" else _label(node.type))

    def test_control_a_mid_lane_capture_stage_is_still_named(self):
        """CONTROL: the mid-lane wire the doctor always read, from the FILTER
        CYCLE, is named as before. Green under "_CAPTURE_TYPES restored"."""
        g = _lane("capture", loop_from="cy")
        assert "starts at FILTER CYCLE," in _one(check(g), M12_MID).text


class TestM4ForAWireFromAnotherLane:
    @staticmethod
    def _two_blocks(stage: str, *, into: str) -> FlowGraph:
        """M31 (3x2) owns FILTER CYCLE -> ``stage``; M33 (1x1, after it)
        owns CAPTURE LOOP Ha. M31's own loop wire leaves its tail, and a
        second pass wire leaves the same tail into ``into``'s 'next panel'
        (M33's: a block whose lane the stage is not in)."""
        return FlowGraph(
            nodes=[_block(), _n("cy", "cycle", 200), _n("s", stage, 400),
                   _block("t2", 600, name="M33", rows=1, cols=1),
                   _n("ha", "capture", 800, filter="Ha", exposure=300)],
            edges=[_e("t", "target", "cy", "run"),
                   _e("cy", "complete", "s", "run"),
                   _e("s", _FLOW_OUT[stage], "t2", "arm"),
                   _e("t2", "target", "ha", "run"),
                   _e("s", "pass", "t", "next", "loop"),
                   _e("s", "pass", into, "next", "stray")])

    @pytest.mark.parametrize("stage", NEW_PASS_TYPES)
    def test_a_wire_from_another_blocks_lane_does_nothing_and_is_warned(
            self, stage):
        """#375 item 3's other half: the AUTOFOCUS or GUIDE is in M31's
        lane, so its pass wire into M33's 'next panel' does nothing, and the
        compile emits it as ``<type>.pass``, a rule that will not run. The
        doctor now warns, as it does for a capture stage's.

        RED under mutant "_CAPTURE_TYPES restored", observed on both, e.g.
        autofocus:

            E       AssertionError: ('this wire does nothing', [('warn', "\\u25b8 TARGET - 'arm' input unwired"), ('warn', '\\u25b8 180s subs with no GUIDE upstream -...al focal length. Add Guide, or shorten the subs.'), ('note', '\\u25b8 no session report sink - the night leaves no ledger')])
        """
        g = self._two_blocks(stage, into="t2")
        hit = _one(check(g), M4_ELSEWHERE)
        assert (hit.level, hit.text) == (
            "warn", f"▸ TARGET M33 'next panel' - this wire does nothing: "
            f"{_label(stage)} is not in this TARGET's panel lane.")
        rules = [r["when"] for r in compile_plan(g, "n")["instructions"]]
        assert rules == [f"{stage}.pass"], "premise: the compile emits it"
        assert not _hits(check(g), M4_ONE_PANEL)

    @pytest.mark.parametrize("stage", NEW_PASS_TYPES)
    def test_control_the_blocks_own_loop_wire_is_not_warned(self, stage):
        """CONTROL: the same graph with the stray wire into M31's own 'next
        panel' is two copies of one loop wire: "one is enough", never "this
        wire does nothing". Green on the code and under "_CAPTURE_TYPES
        restored"."""
        issues = check(self._two_blocks(stage, into="t"))
        assert not _hits(issues, M4_ELSEWHERE)
        assert _one(issues, "one is enough").level == "note"
