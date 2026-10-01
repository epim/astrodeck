"""The STORY brief names every block where its lane runs (#470 items 1 and
2), and a DUSK offset that is no finite number never raises (#423).

EVERY BLOCK, IN FLOW ORDER (#470). ``tonight.brief`` wrote one block
sentence, the first POOL's select sentence or, with no POOL, the first
TARGET's arm sentence, ahead of every lane. On the S4 band fixture's
three-lane graph (DUSK -> TARGET M16, a rotating 3x2 -> CAPTURE Ha, then
TARGET M31 -> CAPTURE L, then POOL M13, M92 -> CAPTURE L) that read "It then
selects the best of M13, M92 ..." straight after the arming sentence, then
M16's mosaic, then the three lanes: M31 was never named (item 1), and the
pool's sentence came first although its lane runs last (item 2). The S5
integration had already put each lane's stages under their block, "For M31
it captures ..." (item 3); those stay as it left them. Now ``brief`` walks
the blocks and stages in the run cursor's order (``tonight._brief_walk``,
``compile.flow_order``): each TARGET is armed and each POOL selects where the
cursor reaches it, a multi-panel TARGET's mosaic sentences follow its own,
and each lane follows where its first stage runs. A flow of one block, every
Example among them, reads byte for byte as before (the control).

A DUSK OFFSET THAT IS NO FINITE NUMBER (#423). ``brief`` opened the DUSK
sentence with ``int(_num(offset))``: ``int()`` of the infinity ``_num``
reads out of "inf" or a JSON ``1e999`` raised ``OverflowError: cannot
convert float infinity to integer``, "nan" raised ``ValueError``, and
``_num``'s own ``float()`` raised ``OverflowError: int too large to convert
to float`` on a 400-digit integer, because ``tonight._num`` caught only
``TypeError`` and ``ValueError``. A DUSK offset is no count, so validation
lets a save store any of them, and ``resolve_tonight`` calls ``brief``
unconditionally, so the whole Tonight answer raised. Now the offset is read
finite-only, as the compile reads it into ``start_offset_min``
(``compile._finite``, which reads each as 0), ``tonight._num`` catches
``OverflowError`` as ``compile._num`` and ``doctor._num`` do, and the brief
says the offset cannot be read and that none is applied. The fix is on the
brief's path: whether validation should refuse such an offset at the save is
``flows/models.py``'s question, not this file's.

Every test names the mutation it guards and quotes the failure it produced
("[...]" marks where a long line is cut). Each mutant ran in a private copy
of ``server/`` (scratchpad ``S7-TONIGHT-mut``, the copy's ``tonight.py``
mutated from a byte backup and restored with its sha256 checked), never in
the shared tree. Run against the tree before the change (the committed
``tonight.py`` in the same copy), the twelve cases that are not controls
went red and the nine controls stayed green. The verifier added two cases
(a block the walk never reaches, and the rig sentence of a flow with no
stage), each red on the committed ``tonight.py`` and under its own mutant,
run in its private copy ``S7-TONIGHT-verify-mut``. A comment added to
``tonight.py`` left every case green (the control on the harness).
"""
from __future__ import annotations

import datetime as _dt
import json
import re

import pytest

from astrodeck.flows.compile import compile_plan, flow_order
from astrodeck.flows.examples import examples
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.tonight import (_num, banked_hours_from_reports, brief,
                                     resolve_tonight)
from astrodeck.hub import Hub


def _n(nid, ntype, x=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=0.0, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def _sentences(text: str) -> list[str]:
    return re.split(r"(?<=\.) ", text)


def _band_graph() -> FlowGraph:
    """The S4 band fixture's graph, node for node (its test builds it)."""
    from test_flows_tonight_band_fixture import _graph
    return _graph()


def _right_to_left() -> FlowGraph:
    """TARGET M31 -> CAPTURE L -> TARGET M33 -> CAPTURE Ha, drawn right to
    left: canvas order is the reverse of the order the flow runs."""
    return FlowGraph(
        nodes=[_n("u", "target", x=0, name="M33", ra="01h 33m 50s",
                  dec="+30 39 36"),
               _n("h", "capture", x=200, filter="Ha", exposure=300,
                  gain=100, bin="1", count=4),
               _n("l", "capture", x=400, filter="L", exposure=60, gain=100,
                  bin="1", count=5),
               _n("t", "target", x=600, name="M31", ra="00h 42m 44s",
                  dec="+41 16 09")],
        edges=[_e("t", "target", "l", "run"), _e("l", "complete", "u", "arm"),
               _e("u", "target", "h", "run")])


def _block_needle(node: FlowNode) -> str:
    """The words of a block's own sentence that name it."""
    if node.type == "pool":
        return f"selects the best of {node.params.get('members')} - "
    return f"arms {node.params.get('name')}."


def _blocks_in_brief_order(graph: FlowGraph, text: str) -> list[str]:
    """The ids of the graph's blocks, in the order the brief names them; a
    block the brief never names is left out."""
    found = [(text.find(_block_needle(n)), n.id) for n in graph.nodes
             if n.type in ("target", "pool")]
    return [nid for at, nid in sorted(found) if at >= 0]


def _blocks_in_plan_order(graph: FlowGraph) -> list[str]:
    """The ids of the blocks the compile makes entries for, in the order of
    its entries (a POOL's members are one block)."""
    out: list[str] = []
    for t in compile_plan(graph).get("targets") or []:
        nid = str(t.get("node_id"))
        if nid not in out:
            out.append(nid)
    return out


# ================================================= #470: every block, in order

class TestEveryBlockWhereItsLaneRuns:
    def test_the_band_fixtures_graph_names_m31_and_the_pool_follows_its_lane(
            self):
        """The band fixture's three lanes read block, then lane: M16 is
        armed and its mosaic said, then its Ha stage; M31 is armed, then its
        L stage; the pool selects, then its L stage.

        RED under the tonight.py mutant "first block only" (a block's
        sentence written for the first block the walk reaches alone, as
        ``brief`` wrote one), observed:

            AssertionError: M31 is never named: ['This flow arms at
            astronomical dusk (\\u221230 min).', 'It then arms M16.', 'M16
            is a 3x2 mosaic of 6 panels at 25% overlap, [...]', 'For M16 it
            captures Ha 300 s \\xd7 2 (gain 100, bin 1).', 'For M31 it
            captures L 60 s \\xd7 5 (gain 100, bin 1).', 'For every member
            of the pool it captures L 60 s \\xd7 5 (gain 100, bin 1).', 'If
            the active target sinks to the 30\\xb0 floor, it is set as[...]

        RED under the tonight.py mutant "pool sentence first" (the walk
        sorted with every POOL ahead, ``sorted(_brief_walk(g), key=lambda
        n: n.type != "pool")``, where ``brief`` put the pool's sentence),
        observed:

            AssertionError: the pool's sentence does not follow M31's lane:
            ['This flow arms at astronomical dusk (\\u221230 min).', 'It
            then selects the best of M13, M92 - above 30\\xb0, at least
            40\\xb0 from the moon (if up), within 4 h of the meridian.', 'It
            then arms M16.', 'M16 is a 3x2 mosaic of 6 panels at 25%
            overlap, [...]', 'For M16 it captures Ha 300 s \\xd7 2 (gain
            100, bin 1).', 'It then arms M31.', 'For M31 it captures L[...]
        """
        said = _sentences(brief(_band_graph(), hop_cost_s=160.0))
        m31 = "It then arms M31."
        m31_lane = "For M31 it captures L 60 s × 5 (gain 100, bin 1)."
        pool = ("It then selects the best of M13, M92 - above 30°, at least "
                "40° from the moon (if up), within 4 h of the meridian.")
        assert m31 in said, f"M31 is never named: {said}"
        assert pool in said and said.index(pool) == said.index(m31_lane) + 1, (
            f"the pool's sentence does not follow M31's lane: {said}")
        # The first sentence is the DUSK's, "This flow arms at ...".
        skeleton = [s for s in said[1:]
                    if " arms " in s or " selects " in s or " captures " in s
                    or " is a 3x2 mosaic " in s]
        assert skeleton == [
            "It then arms M16.",
            "M16 is a 3x2 mosaic of 6 panels at 25% overlap, laid out at PA "
            "30° with the rotator turned to it at every panel.",
            "For M16 it captures Ha 300 s × 2 (gain 100, bin 1).",
            m31, m31_lane, pool,
            "For every member of the pool it captures L 60 s × 5 (gain 100, "
            "bin 1)."], f"the blocks and their lanes read {skeleton}"

    def test_blocks_read_in_flow_order_not_canvas_order(self):
        """A flow drawn right to left reads in the order it runs: M31, its
        L stage, then M33 and its Ha stage, which the Ha stage's own wire
        gives to M33 alone.

        RE-PINNED (W2 integration, backlog WP-19(a), #151, owner-approved
        2026-09-30): this graph has two owner blocks (M31, M33), so
        ``needs_wire_scoping`` now turns on for it regardless of mosaics,
        and Ha's wire (``u.target -> h.run``) gives it to M33 alone -- the
        canvas-order leak this case used to pin on purpose (I-05: Ha also
        read as M31's) is exactly what #151's general case closes. The
        block-and-lane ORDER this test is really about (M31 before M33,
        each followed by its own lane, never canvas order's right-to-left
        draw) is unchanged; only the now-correct ownership of Ha moved.

        RED under the tonight.py mutant "blocks in canvas order"
        (``_brief_walk``'s walk sorted by canvas x instead of taken from
        ``flow_order``), observed:

            AssertionError: This flow arms M33. For M33 it captures Ha 300 s
            × 4 (gain 100, bin 1). For M31 it captures L 60 s × 5 (gain 100,
            bin 1). It then arms M31.

        It is RED under "first block only" too (M33 never armed:
        "[...] (gain 100, bin 1). For M33 it captures Ha [...]"). The tree
        before the change named the first TARGET in canvas order and no
        other, observed: "AssertionError: This flow arms M33. For M31 it
        captures L 60 s × 5 (gain 100, bin 1). For M33 it captures Ha 300 s
        × 4 (gain 100, bin 1)."
        """
        text = brief(_right_to_left())
        assert text == (
            "This flow arms M31. For M31 it captures L 60 s × 5 (gain 100, bin "
            "1). It then arms M33. For M33 it captures Ha 300 s × 4 "
            "(gain 100, bin 1)."), text

    def test_a_block_the_walk_never_reaches_is_still_named(self):
        """A TARGET inside a flow loop, which validation refuses but the
        editor can draw, is one ``flow_order`` never reaches; it follows the
        walked nodes in canvas order, as an unreached stage always did,
        because the brief reads back the graph the operator drew.

        RED under the tonight.py mutant "unreached blocks dropped"
        (``_brief_walk``'s ``rest`` taking capture stages only, as
        ``_capture_stages`` did), observed (S7-TONIGHT verifier, private
        copy ``S7-TONIGHT-verify-mut``):

            AssertionError: This flow arms M31. For M31 it captures L 60 s
            × 5 (gain 100, bin 1). Belonging to no TARGET, and so shooting
            nothing, it captures Ha 60 s × 5 (gain 100, bin 1).

        The tree before the change named the first TARGET in the node list
        and no other, observed: "AssertionError: This flow arms M33. For
        M31 it captures L 60 s × 5 (gain 100, bin 1). Belonging to no
        TARGET, and so shooting nothing, it captures Ha [...]".
        """
        def target(nid, x, name):
            return _n(nid, "target", x=x, name=name, ra="00h 42m 44s",
                      dec="+41 16 09")

        def capture(nid, x, filt):
            return _n(nid, "capture", x=x, filter=filt, exposure=60,
                      gain=100, bin="1", count=5)

        graph = FlowGraph(
            nodes=[target("b", 400, "M33"), capture("h", 600, "Ha"),
                   target("a", 0, "M31"), capture("l", 200, "L")],
            edges=[_e("a", "target", "l", "run"), _e("b", "target", "h", "run"),
                   _e("h", "complete", "b", "arm")])
        assert graph.validation_errors(), "premise: validation refuses it"
        assert [n.id for n in flow_order(graph)] == ["a", "l"], (
            "premise: the walk never reaches M33 or its stage")
        text = brief(graph)
        assert text == (
            "This flow arms M31. For M31 it captures L 60 s × 5 (gain 100, bin "
            "1). It then arms M33. Belonging to no TARGET, and so shooting "
            "nothing, it captures Ha 60 s × 5 (gain 100, bin 1)."), text

    def test_with_no_stage_the_rig_sentence_follows_the_last_block(self):
        """The flow-wide "For each target it autofocuses ..." is said before
        the first lane; a flow with no capture stage has no lane, and says
        it after its last block. One block with no stage is the control: it
        reads as it did before the walk.

        RED under the tonight.py mutant "no rig without a stage" (the
        closing ``seg.extend(rig_owed)`` after the walk removed), observed
        (S7-TONIGHT verifier, private copy ``S7-TONIGHT-verify-mut``):

            AssertionError: ('This flow arms M31.', 'This flow arms M31. It
            then arms M33.')

        The tree before the change read the one-block flow as pinned here
        and the two-block flow without M33, observed: "At index 1 diff:
        'This flow arms M31. For each target it autofocuses (v-curve
        sweep).' != 'This flow arms M31. It then arms M33. For each target
        it autofocuses (v-curve sweep).'".
        """
        def target(nid, x, name):
            return _n(nid, "target", x=x, name=name, ra="00h 42m 44s",
                      dec="+41 16 09")

        af = _n("f", "autofocus", x=200, method="V-curve sweep")
        one = FlowGraph(nodes=[target("a", 0, "M31"), af],
                        edges=[_e("a", "target", "f", "run")])
        two = FlowGraph(nodes=[target("a", 0, "M31"), af,
                               target("b", 400, "M33")],
                        edges=[_e("a", "target", "f", "run"),
                               _e("f", "focused", "b", "arm")])
        got = (brief(one), brief(two))
        assert got == (
            "This flow arms M31. For each target it autofocuses (v-curve "
            "sweep).",
            "This flow arms M31. It then arms M33. For each target it "
            "autofocuses (v-curve sweep)."), got

    def test_the_brief_names_the_blocks_in_the_order_the_compile_lists_them(
            self):
        """Held to the compile: over the band fixture's graph, the right to
        left flow and every Example, the brief names every block the
        compile makes an entry for, in the order of the compile's entries.

        RED under "pool sentence first", observed:

            AssertionError: band: the brief names ['p', 't', 'u'], the plan
            runs ['t', 'u', 'p']

        under "first block only":

            AssertionError: band: the brief names ['t'], the plan runs
            ['t', 'u', 'p']

        under "blocks in canvas order":

            AssertionError: right to left: the brief names ['u', 't'], the
            plan runs ['t', 'u']

        and on the tree before the change: "AssertionError: band: the brief
        names ['p'], the plan runs ['t', 'u', 'p']".
        """
        graphs = [("band", _band_graph()), ("right to left", _right_to_left())]
        graphs += [(r.id, r.graph) for r in examples()]
        for label, graph in graphs:
            text = brief(graph)
            said, plan = (_blocks_in_brief_order(graph, text),
                          _blocks_in_plan_order(graph))
            assert said == plan, (
                f"{label}: the brief names {said}, the plan runs {plan}")


# ============================== control: a flow of one block reads as before

#: Every Example's brief as it read before #470's walk, recorded from the
#: tree before the change (the plain call, and the eighth Example with a
#: measured hop of 160 s, which is the one Example whose brief reads it).
EXAMPLE_BRIEFS = {
    "example-campaign": (
        "This flow arms at astronomical dusk (−30 min), opens the dome and "
        "binds it to the mount. It then selects the best of M33, NGC 7331, IC "
        "1396, M45 - above 30°, at least 40° from the moon (if up), within 4 "
        "h of the meridian. For each target it autofocuses (v-curve sweep), "
        "guides with PHD2 (settle below 1.5″, dither every 3 frames). Capture "
        "interleaves one sub per filter per pass - L 60 s × 45, R 60 s × 45, "
        "G 60 s × 45, B 60 s × 45, Ha 180 s × 45, OIII 180 s × 45, SII 180 s "
        "× 45 - so every channel grows evenly. When a target's quota is met, "
        "a session report is cut and the pool advances to the next best - "
        "finished targets are never re-selected. If the active target sinks "
        "to the 30° floor, it is set aside for tonight - a restart tonight "
        "does not retry it, the next night does - and the next best takes "
        "over. If cloud cover above 40% is detected, imaging pauses at the "
        "frame boundary and the calibration queue banks whatever the library "
        "lacks (darks → bias → flats-if-panel); once the sky holds clear for "
        "4 min it re-cools the sensor to setpoint and waits for it to "
        "stabilize, restores the filter, re-centers, refocuses if drifted, "
        "and resumes at the same slot. When astronomical night ends, the "
        "mount parks and the dust flap + dome closes, then the camera warms; "
        "the flow re-arms at the next dusk and resumes mid-cycle from the "
        "ledger. Rain, wind, or power failure aborts and parks "
        "unconditionally - a stale reading counts as unsafe. Once all 4 "
        "targets hold their 45-cycle quota, the rig stays parked."),
    "example-m31": (
        "This flow arms at astronomical dusk (−30 min). It then arms M31 - "
        "Andromeda. For each target it autofocuses (v-curve sweep), guides "
        "with PHD2 (settle below 1.5″, dither every 3 frames). It captures L "
        "120 s × 24 (gain 100, bin 1). A session report is appended when the "
        "run ends. Rain, wind, or power failure aborts and parks "
        "unconditionally - a stale reading counts as unsafe."),
    "example-m16": (
        "This flow arms at astronomical dusk (−30 min), opens the dome and "
        "binds it to the mount, and shoots 15 flats per filter (translucent "
        "lens cap) in the twilight window. It then arms M16 - Eagle. For each "
        "target it autofocuses (v-curve sweep), guides with PHD2 (settle "
        "below 1.5″, dither every 3 frames). It captures Ha 180 s × 20 (gain "
        "100, bin 1). A session report is appended when the run ends. If "
        "cloud cover above 40% is detected, imaging pauses at the frame "
        "boundary and the calibration queue banks whatever the library lacks "
        "(darks → bias → flats-if-panel); once the sky holds clear for 4 min "
        "it re-cools the sensor to setpoint and waits for it to stabilize, "
        "restores the filter, re-centers, refocuses if drifted, and resumes "
        "at the same slot. Rain, wind, or power failure aborts and parks "
        "unconditionally - a stale reading counts as unsafe."),
    "example-cycle": (
        "This flow arms at astronomical dusk (−30 min). It then arms M33 - "
        "Triangulum. For each target it autofocuses (v-curve sweep), guides "
        "with PHD2 (settle below 1.5″, dither every 3 frames). Capture "
        "interleaves one sub per filter per pass - L 60 s × 45, R 60 s × 45, "
        "G 60 s × 45, B 60 s × 45, Ha 180 s × 45, OIII 180 s × 45, SII 180 s "
        "× 45 - so every channel grows evenly. A session report is appended "
        "when the run ends. If cloud cover above 40% is detected, imaging "
        "pauses at the frame boundary and the calibration queue banks "
        "whatever the library lacks (darks → bias → flats-if-panel); once the "
        "sky holds clear for 4 min it re-cools the sensor to setpoint and "
        "waits for it to stabilize, restores the filter, re-centers, "
        "refocuses if drifted, and resumes at the same slot."),
    "example-pool": (
        "This flow arms at astronomical dusk (−30 min). It then selects the "
        "best of M16, M17, M8, NGC 6946 - above 30°, at least 40° from the "
        "moon (if up), within 4 h of the meridian. For each target it "
        "autofocuses (v-curve sweep), guides with PHD2 (settle below 1.5″, "
        "dither every 3 frames). It captures Ha 180 s × 20 (gain 100, bin 1). "
        "A session report is appended when the run ends. If the active target "
        "sinks to the 30° floor, it is set aside for tonight - a restart "
        "tonight does not retry it, the next night does - and the next best "
        "takes over."),
    "example-nb": (
        "This flow arms at astronomical dusk (−30 min). It then arms NGC 7000 "
        "- North America. For each target it autofocuses (v-curve sweep), "
        "guides with PHD2 (settle below 1.5″, dither every 3 frames). It "
        "captures Ha 300 s × 12 (gain 100, bin 1). A session report is "
        "appended when the run ends."),
    "example-eaa": (
        "This flow arms M27 - Dumbbell. For each target it autofocuses "
        "(v-curve sweep). It captures L 4 s × 60 (gain 300, bin 2). A session "
        "report is appended when the run ends."),
    "example-m31-mosaic": (
        "This flow arms at astronomical dusk (−30 min). It then arms M31. M31 "
        "is a 3x2 mosaic of 6 panels at 25% overlap, laid out at PA 55.0° "
        "with the rotator turned to it at every panel. After 1 pass of its "
        "filters on a panel it moves on to the next (least complete first), "
        "and comes back until every panel has its subs. The hop between "
        "panels has not been measured on this rig yet. For each target it "
        "autofocuses (v-curve sweep), guides with PHD2 (settle below 1.5″, "
        "dither every 3 frames). Capture interleaves one sub per filter per "
        "pass - L 120 s × 20, R 120 s × 20, G 120 s × 20, B 120 s × 20 - so "
        "every channel grows evenly. A session report is appended when the "
        "run ends. Rain, wind, or power failure aborts and parks "
        "unconditionally - a stale reading counts as unsafe."),
    "example-m31-mosaic@160": (
        "This flow arms at astronomical dusk (−30 min). It then arms M31. M31 "
        "is a 3x2 mosaic of 6 panels at 25% overlap, laid out at PA 55.0° "
        "with the rotator turned to it at every panel. After 1 pass of its "
        "filters on a panel it moves on to the next (least complete first), "
        "and comes back until every panel has its subs. A hop between panels "
        "takes about 2 m 40 s, as measured on this rig. For each target it "
        "autofocuses (v-curve sweep), guides with PHD2 (settle below 1.5″, "
        "dither every 3 frames). Capture interleaves one sub per filter per "
        "pass - L 120 s × 20, R 120 s × 20, G 120 s × 20, B 120 s × 20 - so "
        "every channel grows evenly. A session report is appended when the "
        "run ends. Rain, wind, or power failure aborts and parks "
        "unconditionally - a stale reading counts as unsafe."),
}

#: THE ONE CLAUSE H4 CHANGED ON PURPOSE (#506). The brief used to name the
#: GUIDE card's own params, which the run never uses (``to_plan``'s
#: NODE_SETTINGS["guide"]: guider, settle and dither come from Rig >
#: Guider), so every Example read "guides with PHD2 (...)" whatever the rig
#: guided with. Since H4 it names the rig's guider (``_guide_clause``), and
#: with no rig facts handed in, as ``brief(graph)`` is called here, it says
#: where they come from instead. The pins above are kept as recorded before
#: #470's walk; the test swaps this clause and nothing else (re-pinned by the
#: H4 integration).
GUIDE_CLAUSE_BEFORE_H4 = "guides with PHD2 (settle below 1.5″, dither every 3 frames)"
GUIDE_CLAUSE_NO_RIG = "guides (guider, settle and dither from Rig > Guider)"


def _as_h4_reads(pin: str) -> str:
    """A pin as it reads since #506: the GUIDE clause swapped, once."""
    return pin.replace(GUIDE_CLAUSE_BEFORE_H4, GUIDE_CLAUSE_NO_RIG)


class TestOneBlockReadsAsBefore:
    def test_all_eight_examples_read_byte_for_byte_as_before(self):
        """CONTROL. Every Example is one block, so the walk changes none:
        each reads byte for byte as it did, with no hop, with a hop of 160 s,
        and with its compile handed in, and the pins name all eight.
        "first block only" and "pool sentence first" left it green (one
        block, no second to misplace), as did the tree before the change.

        RED under the tonight.py mutant "rig sentence first" (the flow-wide
        "For each target it autofocuses ..." said before the walk instead
        of before the first lane), observed:

            AssertionError: example-campaign reads differently: This flow
            arms at astronomical dusk (\\u221230 min), opens the dome and
            binds it to the mount. For each target it autofocuses (v-curve
            sweep), guides with PHD2 (settle below 1.5\\u2033, dither every
            3 frames). It then selects the best of M33, NGC 7331, IC 1396,
            M45 - [...]

        (and ``test_flows_campaign_brief``'s dangling "It then" case, on
        EAA: "For each target it autofocuses (v-curve sweep). It then arms
        M27 - Dumbbell. [...]"). RED under "blocks in canvas order" too,
        since the Campaign's POOL is drawn right of its FILTER CYCLE,
        observed: "example-campaign reads differently: [...] dither every 3
        frames). Capture interleaves one sub per filter per pass - [...] -
        so every channel grows evenly. It then selects the best of M33,
        [...]".

        RE-PINNED FOR H4: every pin is read through ``_as_h4_reads``, the
        GUIDE clause swapped for the no-rig one (#506), after a check that
        each pin carries the old clause exactly once where its Example has a
        GUIDE stage (all but EAA), so the swap cannot pass by matching
        nothing. Before the re-pin, the tree after H4 read (observed):

            AssertionError: example-campaign reads differently: This flow
            arms at astronomical dusk (\\u221230 min), [...] For each target
            it autofocuses (v-curve sweep), guides (guider, settle and
            dither from Rig > Guider). Capture interleaves [...]

        RED under H4-ROUTES-A's mutant "brief reads the card params" (the
        ``_guide_clause(rig)`` stage put back as the card's "guides with
        {provider} (settle below {settle}″, dither every {dither}
        frames)"), re-run by the H4 integration, observed:

            AssertionError: example-campaign reads differently: This flow
            arms at astronomical dusk (\\u221230 min), [...] For each target
            it autofocuses (v-curve sweep), guides with PHD2 (settle below
            1.5\\u2033, dither every 3 frames). Capture interleaves [...]
        """
        ids = [r.id for r in examples()]
        assert len(ids) == 8 and all(i in EXAMPLE_BRIEFS for i in ids), ids
        for key, pin in EXAMPLE_BRIEFS.items():
            want = 0 if key == "example-eaa" else 1
            assert pin.count(GUIDE_CLAUSE_BEFORE_H4) == want, (
                f"premise: {key}'s pin carries the old GUIDE clause "
                f"{pin.count(GUIDE_CLAUSE_BEFORE_H4)} times, not {want}")
        for r in examples():
            pin = _as_h4_reads(EXAMPLE_BRIEFS[r.id])
            hop = _as_h4_reads(EXAMPLE_BRIEFS.get(f"{r.id}@160",
                                                  EXAMPLE_BRIEFS[r.id]))
            got = (brief(r.graph), brief(r.graph, hop_cost_s=160.0),
                   brief(r.graph, plan=compile_plan(r.graph, r.name)))
            assert got == (pin, hop, pin), (
                f"{r.id} reads differently: {got[0]}")


# ================================= #423: a DUSK offset that is no finite number

#: The stored shapes #423 names, each as ``json.loads`` gives it back from a
#: flow file, and "nan", which raised too: ``(value, how the brief quotes
#: it)``.
UNREADABLE = {
    "the text inf": ("inf", "'inf'"),
    "a JSON 1e999": (json.loads("1e999"), "inf"),
    "a 400-digit integer": (json.loads("1" + "0" * 399),
                            "a number of more than 15 digits"),
    "the text nan": ("nan", "'nan'"),
}

#: A synthetic site (40 N 105 W, the band fixture's, NOT the observatory's)
#: and an instant on its night.
SITE = {"latitude": 40.0, "longitude": -105.0, "elevation_m": 1600.0,
        "is_default": False}
JUNE = _dt.datetime(2026, 6, 15, 20, 0, tzinfo=_dt.timezone.utc).timestamp()


@pytest.fixture
def synthetic_hub(monkeypatch):
    """The hub on the synthetic site, so nothing the answer is built from
    can come from the configured one."""
    monkeypatch.setattr(Hub, "site", property(lambda self: {
        "name": "synthetic", **SITE, "horizon_min_deg": 0.0}))


def _with_offset(value) -> FlowGraph:
    """The Campaign Example with its DUSK WINDOW's ``offset`` set to
    ``value``, through ``model_validate`` of the dumped graph, as a stored
    file is read."""
    ex = next(e for e in examples() if e.id == "example-campaign")
    raw = ex.graph.model_dump(by_alias=True)
    (dusk,) = [n for n in raw["nodes"] if n["type"] == "dusk"]
    dusk["params"]["offset"] = value
    return FlowGraph.model_validate(raw)


def _unread(shown: str) -> str:
    return (f"This flow arms at astronomical dusk (its offset, {shown}, "
            f"cannot be read as a number of minutes, so none is applied), "
            f"opens the dome and binds it to the mount.")


class TestADuskOffsetThatIsNoNumber:
    @pytest.mark.parametrize("shape", list(UNREADABLE))
    def test_the_brief_says_it_cannot_be_read_and_raises_nothing(self, shape):
        """A save stores the value (validation refuses nothing), the compile
        reads it as no offset, and the brief says the offset cannot be read
        and that none is applied, where it raised.

        RED under the tonight.py mutant "int() of the raw offset"
        (``off = int(_num(p.get("offset")))`` and its ``if off:`` put back
        for the finite-only reading), on "the text inf" and "a JSON 1e999",
        observed:

            >           off = int(_num(p.get("offset")))
            E           OverflowError: cannot convert float infinity to
            integer
            astrodeck\\flows\\tonight.py:1649: OverflowError

        on "the text nan": "ValueError: cannot convert float NaN to
        integer", and on "a 400-digit integer", which ``_num`` now reads as
        0, so nothing raised and nothing was said:

            AssertionError: This flow arms at astronomical dusk, opens the
            dome and binds it to the mount. [...]
            - This flow arms at astronomical dusk (its offset, a number of
            more than 15 digits, cannot be read as a number of minutes, so
            none is applied), opens the dome and binds it to the mount.
            + This flow arms at astronomical dusk, opens the dome and binds
            it to the mount.

        The tree before the change raised on all four, the 400-digit
        integer in ``_num``: "OverflowError: int too large to convert to
        float".
        """
        value, shown = UNREADABLE[shape]
        graph = _with_offset(value)
        assert graph.validation_errors() == [], "premise: a save stores it"
        schedule = compile_plan(graph)["schedule"]
        assert schedule["start_offset_min"] == 0, (
            f"premise: the compile applies no offset: {schedule}")
        text = brief(graph)
        assert _sentences(text)[0] == _unread(shown), text

    @pytest.mark.parametrize("shape", list(UNREADABLE))
    def test_the_whole_tonight_answer_never_raises(self, shape,
                                                   synthetic_hub):
        """``resolve_tonight`` answers such a flow, its brief says the offset
        cannot be read, and its window opens at dusk, as the brief says.

        RED under "int() of the raw offset", observed (the "inf" case):

            astrodeck\\flows\\tonight.py:666: in resolve_tonight
            [...]
            >           off = int(_num(p.get("offset")))
            E           OverflowError: cannot convert float infinity to
            integer

        with the same four failures as the case above, one per shape.
        """
        value, shown = UNREADABLE[shape]
        out = resolve_tonight(_with_offset(value), SITE, now=JUNE,
                              twilight_deg=-12.0)
        assert out["ok"], out["reason"]
        assert _sentences(out["brief"])[0] == _unread(shown), out["brief"]
        night = out["night"]
        assert night["window_start_unix"] == night["dusk_unix"], night

    def test_tonights_num_reads_an_integer_past_a_float_as_its_default(self):
        """``tonight._num`` of a 400-digit integer is its default, as
        ``compile._num``'s is, and a report row holding one banks nothing
        rather than taking the fold down.

        RED under the tonight.py mutant "_num without OverflowError"
        (``except (TypeError, ValueError, OverflowError)`` made ``except
        (TypeError, ValueError)``), observed:

            E           OverflowError: int too large to convert to float
            astrodeck\\flows\\tonight.py:109: OverflowError

        No other test here went red under it: the brief reads the offset
        through ``_not_finite`` and ``compile._finite``, not ``_num``.
        """
        huge = json.loads("1" + "0" * 399)
        assert (_num(huge), _num(huge, 7.0)) == (0.0, 7.0)
        rows = [{"filter": "L", "integration_s": huge},
                {"filter": "L", "integration_s": 3600}]
        assert banked_hours_from_reports([{"by_filter": rows}]) == {"L": 1.0}


class TestAFiniteOffsetReadsAsBefore:
    #: ``offset -> what the DUSK sentence says after "astronomical dusk"``.
    #: A fraction is cut toward zero, and text that is no number at all
    #: (blank, or letters) is no offset, both as ``int(_num(...))`` read
    #: them. That the compile keeps the fraction and the run then refuses
    #: it is #483, which this control pins as it was rather than settles.
    FINITE = {-30: " (−30 min)", "-30": " (−30 min)", 45: " (+45 min)",
              -30.7: " (−30 min)", 0: "", "": "", "abc": ""}

    @pytest.mark.parametrize("value", list(FINITE), ids=repr)
    def test_the_dusk_sentence_reads_as_it_did(self, value):
        """CONTROL. A finite offset, and text that is no number, read as
        they did, with no word about an unreadable offset.

        RED under the tonight.py mutant "no int() of the finite offset"
        (``off = int(_finite(raw))`` made ``off = _finite(raw)``), on
        -30.7 only, observed:

            AssertionError: This flow arms at astronomical dusk (\\u221231
            min), opens the dome [...]
            - This flow arms at astronomical dusk (\\u221230 min), op
            + This flow arms at astronomical dusk (\\u221231 min), op

        RED under the tonight.py mutant "text judged unreadable"
        (``_not_finite``'s ``except (TypeError, ValueError): return False``
        made ``return True``), on "" and "abc", observed:

            - This flow arms at astronomical dusk, opens the dome and binds
            it to the mount.
            + This flow arms at astronomical dusk (its offset, 'abc', cannot
            be read as a number of minutes, so none is applied), opens the
            dome and binds it to the mount.

        (and the same with ``''`` for the blank).
        """
        text = brief(_with_offset(value))
        want = (f"This flow arms at astronomical dusk{self.FINITE[value]}, "
                f"opens the dome and binds it to the mount.")
        assert _sentences(text)[0] == want, text

    def test_the_window_opens_at_the_offset(self, synthetic_hub):
        """CONTROL. Tonight's window for an offset of -30 opens 30 min
        before dusk, and its brief says "(−30 min)", as it did."""
        out = resolve_tonight(_with_offset(-30), SITE, now=JUNE,
                              twilight_deg=-12.0)
        night = out["night"]
        assert night["window_start_unix"] == night["dusk_unix"] - 1800.0
        assert out["brief"].startswith(
            "This flow arms at astronomical dusk (−30 min),"), out["brief"]
