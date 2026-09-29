"""The STORY brief names whose each capture stage is when a flow has several
lanes (#470, the half of #395 S5-TONIGHT's verifier found open).

S5 made the brief name every capture stage (#395), but read them as one
chain after the mosaic's sentences, "It captures ... It then captures ...".
On a flow of several blocks every lane's stages then read as the first
block's: a rotating 2x2 M31 whose lane is a CAPTURE of Ha 300 s x 4, beside
a TARGET M33 whose lane is a CAPTURE of L 60 s x 5, said "a pass takes 5
min" of M31 and then named 360 s of stages under it, and never named M33.
The S4 band fixture's graph read as M16 shooting Ha, then L, then L, where
the two L stages are M31's and the pool's.

``tonight._stage_sentences`` now groups the stages by the blocks the
compile shoots them for (``tonight._receivers``: ``owner_of`` with a
multi-panel TARGET in the graph, every block the walk passed before the
stage without one) and opens each lane's first stage with them, "For M33 it
captures ...". A flow whose stages all go to one block reads as before, and
the last test holds ``_receivers`` to the compile's own plan, so the brief
cannot name a lane the run does not shoot.

Every test names the mutation it guards and quotes the failure it produced
("[...]" marks where a long line is cut). Each mutant ran in a private copy
of ``server/`` (scratchpad ``S5-FINAL-INTEG-mut``, applied from a byte
backup and restored with its sha256 checked), never in the shared tree. A
comment added to ``tonight.py`` left all seven green (the control).
"""
from __future__ import annotations

import re

from astrodeck.flows.compile import compile_plan
from astrodeck.flows.examples import examples
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.tonight import _mosaic_entries, _pass_s, _receivers, brief


def _n(nid, ntype, x=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=0.0, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def _sentences(text: str) -> list[str]:
    return re.split(r"(?<=\.) ", text)


def _m31_mosaic(x=0.0):
    return _n("t", "target", x=x, name="M31", ra="00h 42m 44s",
              dec="+41 16 09", rows=2, cols=2, overlap=25, rotation=30,
              angle="Rotate to PA", fovX=2.0, fovY=1.33, passes=1,
              minVisit=12, order="Least complete first")


def _two_lanes() -> FlowGraph:
    """The verifier's case: a rotating 2x2 M31 whose lane is one CAPTURE of
    Ha 300 s x 4 (its pass, 300 s), then a TARGET M33 whose lane is one
    CAPTURE of L 60 s x 5. No validation error."""
    return FlowGraph(
        nodes=[_m31_mosaic(),
               _n("c", "capture", x=200, filter="Ha", exposure=300,
                  gain=100, bin="1", count=4),
               _n("u", "target", x=400, name="M33", ra="01h 33m 50s",
                  dec="+30 39 36"),
               _n("l", "capture", x=600, filter="L", exposure=60, gain=100,
                  bin="1", count=5)],
        edges=[_e("t", "target", "c", "run"), _e("c", "pass", "t", "next"),
               _e("c", "complete", "u", "arm"),
               _e("u", "target", "l", "run")])


def _named_seconds(sentences: list[str]) -> float:
    """The shutter a pass of the named CAPTURE sentences takes: one frame
    of each, as ``_pass_s`` counts a capture step in a loop."""
    total = 0.0
    for s in sentences:
        m = re.search(r"captures \S+ (\d+(?:\.\d+)?) s ×", s)
        if m:
            total += float(m.group(1))
    return total


def _past_a_dome() -> FlowGraph:
    """M31's rotating 2x2 and its CAPTURE of Ha, then a DOME, then a CAPTURE
    of L in the DOME's lane: the L stage belongs to no TARGET (M13)."""
    return FlowGraph(
        nodes=[_m31_mosaic(),
               _n("c", "capture", x=200, filter="Ha", exposure=300,
                  gain=100, bin="1", count=4),
               _n("d", "dome", x=400),
               _n("l", "capture", x=600, filter="L", exposure=60, gain=100,
                  bin="1", count=5)],
        edges=[_e("t", "target", "c", "run"), _e("c", "pass", "t", "next"),
               _e("c", "complete", "d", "run"),
               _e("d", "done", "l", "run")])


class TestSeveralLanesNameTheirBlock:
    def test_a_mosaics_stages_are_the_pass_it_states_and_the_next_lane_is_named(
            self):
        """The stages named under M31 add up to the pass its visit sentence
        states, and M33's stage is said to be M33's.

        RED under the tonight.py mutant "every stage read as one chain"
        (``_stage_sentences``' ``if len(lanes) <= 1:`` made ``if True:``,
        the brief as S5 left it):

            AssertionError: M31's stages: [] (0 s) under a pass of 300 s:
            This flow arms M31. M31 is a 2x2 mosaic of 4 panels at 25%
            overlap, [...] A visit is 3 passes rather than the 1 asked,
            since it las[...]
            assert [] == ['For M31 it ...100, bin 1).']
              Right contains one more item: 'For M31 it captures Ha 300 s ×
              4 (gain 100, bin 1).'
        """
        g = _two_lanes()
        text = brief(g)
        entry = _mosaic_entries(g, None)["t"]
        stated = _pass_s(entry)
        assert stated == 300.0, f"premise: M31's pass is its Ha frame: {stated}"
        assert "a pass takes 5 min" in text, text
        said = _sentences(text)
        m31 = [s for s in said if s.startswith("For M31 it captures")]
        assert m31 == ["For M31 it captures Ha 300 s × 4 (gain 100, bin 1)."], (
            f"M31's stages: {m31} ({_named_seconds(m31):g} s) under a pass of "
            f"{stated:g} s: {text}")
        assert _named_seconds(m31) == stated
        assert "For M33 it captures L 60 s × 5 (gain 100, bin 1)." in said, text
        assert not any(s.startswith("It then captures") for s in said), text

    def test_the_band_fixtures_three_lanes_each_follow_their_block(self):
        """The S4 band fixture's graph (M16's 3x2, M31, a POOL of M13 and
        M92, one CAPTURE each): each stage is said to be its own block's,
        in the order the lanes run.

        RED under "every stage read as one chain":

            AssertionError: the stage sentences are ['It captures Ha 300 s ×
            2 (gain 100, bin 1).', 'It then captures L 60 s × 5 (gain 100,
            bin 1).', 'It then captures L 60 s × 5 (gain 100, bin 1).']
        """
        from test_flows_tonight_band_fixture import _graph
        said = [s for s in _sentences(brief(_graph()))
                if " captures " in s or " interleaves " in s]
        assert said == [
            "For M16 it captures Ha 300 s × 2 (gain 100, bin 1).",
            "For M31 it captures L 60 s × 5 (gain 100, bin 1).",
            "For every member of the pool it captures L 60 s × 5 (gain 100, "
            "bin 1)."], f"the stage sentences are {said}"

    def test_with_no_mosaic_a_stage_names_every_block_the_compile_gives_it(
            self):
        """With no multi-panel TARGET the compile's canvas-order rule gives a
        stage to every block the walk passed before it (its leak is I-05),
        and the brief says so rather than read the stage as one block's.

        RED under the tonight.py mutant "owner_of in a graph with no mosaic"
        (``_receivers``' ``mosaic = any(...)`` made ``mosaic = True``):

            AssertionError: For M31 it captures Ha 300 s × 4 (gain 100, bin
            1). For M33 it captures L 60 s × 5 (gain 100, bin 1).
            assert 'For M31 and M33 it captures L 60 s × 5 (gain 100, bin
            1).' in 'This flow arms M31. For M31 it captures Ha 300 s × 4
            (gain 100, bin 1). For M33 it captures L 60 s × 5 (gain 100,
            bin 1).'

        It is RED under "every stage read as one chain" too.
        """
        g = _two_lanes()
        single = g.model_copy(update={"nodes": [
            n.model_copy(update={"params": {**n.params, "rows": 1,
                                            "cols": 1}})
            if n.id == "t" else n for n in g.nodes]})
        text = brief(single)
        tail = text[text.index("arms M31") + len("arms M31. "):]
        assert "For M31 and M33 it captures L 60 s × 5 (gain 100, bin 1)." in (
            text), tail
        assert "For M31 it captures Ha 300 s × 4 (gain 100, bin 1)." in text, (
            tail)

    def test_a_stage_no_block_holds_says_it_shoots_nothing(self):
        """In a graph with a mosaic, a stage past a DOME belongs to no TARGET
        (M13) and the compile gives it to nobody; the brief says so.

        RED under the tonight.py mutant "an unowned stage read as the blocks
        before it" (``_receivers``' ``else ()`` made ``else
        tuple(passed)``):

            AssertionError: ['It captures Ha 300 s × 4 (gain 100, bin
            1).', 'It then captures L 60 s × 5 (gain 100, bin 1).']
            assert ['It captures...100, bin 1).'] == ['For M31 it
            ...100, bin 1).']

        (with the stage given to M31 there is one lane again, so neither
        stage names a block). It is RED under "every stage read as one
        chain" too, and the last test's plan check reads this graph.
        """
        said = [s for s in _sentences(brief(_past_a_dome()))
                if " captures " in s]
        assert said == [
            "For M31 it captures Ha 300 s × 4 (gain 100, bin 1).",
            "Belonging to no TARGET, and so shooting nothing, it captures L "
            "60 s × 5 (gain 100, bin 1)."], said


class TestOneLaneReadsAsItDid:
    def test_a_lane_of_one_block_names_no_block(self):
        """CONTROL. A single TARGET's lane of a cycle then a capture, and a
        POOL's lane, are briefed as before: "Capture interleaves ...", "It
        then captures ...", no block named.

        RED under the tonight.py mutant "a lone lane names its block"
        (``if len(lanes) <= 1:`` made ``if len(lanes) < 1:``):

            AssertionError: For M31 it interleaves one sub per filter per
            pass - L 60 s × 6 - so every channel grows evenly. It then
            captures OIII 300 s × 2 (gain 100, bin 1).
            assert 'Capture interleaves one sub per filter per pass - L 60 s
            × 6 - so every channel grows evenly.' in 'This flow arms M31.
            For M31 it interleaves one sub per filter per pass - L 60 s × 6
            - so every channel grows evenly. It then captures OIII 300 s ×
            2 (gain 100, bin 1).'
        """
        g = FlowGraph(
            nodes=[_n("t", "target", name="M31", ra="00h 42m 44s",
                      dec="+41 16 09"),
                   _n("y", "cycle", x=200, plan="L 60", cycles=6, perCycle=1,
                      gain=100, bin="1"),
                   _n("o", "capture", x=400, filter="OIII", exposure=300,
                      gain=100, bin="1", count=2)],
            edges=[_e("t", "target", "y", "run"),
                   _e("y", "complete", "o", "run")])
        text = brief(g)
        tail = text[text.index("arms M31") + len("arms M31. "):]
        assert ("Capture interleaves one sub per filter per pass - L 60 s × 6 "
                "- so every channel grows evenly.") in text, tail
        assert "It then captures OIII 300 s × 2 (gain 100, bin 1)." in text, (
            tail)
        assert " it captures" not in text and "For M31" not in text, tail

    def test_no_example_names_a_block(self):
        """CONTROL. Every Example is one lane, so no Example's brief names a
        block: the grouping changed no Example.

        RED under "a lone lane names its block":

            AssertionError: ['example-campaign: For every member of the pool
            it interleaves one sub per filter per pass - L 60 s × 45, R 60 s
            × 45...× 20 (gain 100, bin 1).', 'example-nb: For NGC 7000 -
            North America it captures Ha 300 s × 12 (gain 100, bin 1).',
            ...]
            assert ['example-cam...bin 1).', ...] == []
              Left contains 8 more items, first extra item: [...]
        """
        named = [f"{r.id}: {s}" for r in examples()
                 for s in _sentences(brief(r.graph))
                 if s.startswith(("For ", "Belonging to no TARGET"))
                 and (" it captures " in s or " it interleaves " in s)]
        assert named == [], named


class TestTheLanesAreTheCompilesOwn:
    def test_each_stage_names_the_blocks_the_plan_shoots_it_for(self):
        """``_receivers`` against the compile: for each graph, each stage's
        blocks are the blocks whose compiled entries carry a step with the
        stage's node id. It covers both scoping rules, a stage no block
        holds, a POOL whose members box names nobody (the compile makes it
        no entry), and every Example.

        RED under the tonight.py mutant "a pool with no members receives"
        (``_receives`` made ``return True``):

            AssertionError: empty pool: the brief says {'c': ('p',)}, the
            plan shoots {'c': ()}

        under "owner_of in a graph with no mosaic":

            AssertionError: no mosaic: the brief says {'c': ('t',), 'l':
            ('u',)}, the plan shoots {'c': ('t',), 'l': ('t', 'u')}

        and under "an unowned stage read as the blocks before it":

            AssertionError: past a dome: the brief says {'c': ('t',), 'l':
            ('t',)}, the plan shoots {'c': ('t',), 'l': ()}
        """
        g = _two_lanes()
        no_mosaic = g.model_copy(update={"nodes": [
            n.model_copy(update={"params": {**n.params, "rows": 1,
                                            "cols": 1}})
            if n.id == "t" else n for n in g.nodes]})
        empty_pool = FlowGraph(
            nodes=[_n("p", "pool", members=" , "),
                   _n("c", "capture", x=200, filter="L", exposure=60,
                      gain=100, bin="1", count=5)],
            edges=[_e("p", "target", "c", "run")])
        from test_flows_tonight_band_fixture import _graph
        graphs = [("two lanes", g), ("no mosaic", no_mosaic),
                  ("empty pool", empty_pool), ("past a dome", _past_a_dome()),
                  ("band", _graph())]
        graphs += [(r.id, r.graph) for r in examples()]
        for label, graph in graphs:
            plan = compile_plan(graph)
            shot: dict[str, list[str]] = {}
            for t in plan.get("targets") or []:
                for s in t.get("steps") or []:
                    blocks = shot.setdefault(str(s.get("node_id")), [])
                    if t.get("node_id") not in blocks:
                        blocks.append(str(t.get("node_id")))
            said = {sid: tuple(b.id for b in blocks)
                    for sid, blocks in _receivers(graph).items()}
            plan_says = {sid: tuple(shot.get(sid, [])) for sid in said}
            assert said == plan_says, (
                f"{label}: the brief says {said}, the plan shoots {plan_says}")
