# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The STORY brief's mosaic sentences: the grid written columns by rows (S4
orchestrator ruling 1, #339), the visit the run really makes (spec 5.3, S3
item 5, #353), and every capture stage the pass is made of (#395).

THE GRID. The eighth Example is named "M31 3x2, rotating" (2 rows of 3
columns), the framing card and the spec's tables write ``cols x rows`` as a
camera field is written, width by height, and S3's brief wrote ``rows x
cols``, pulled by the row-column panel labels: the same block read "3x2" in
the library and "2x3" in its brief. S4 orchestrator ruling 1: a size is
columns by rows; the labels stay row-column; where a size and a label share
a sentence (the skipped panels), the size is said once in words, "3 columns
by 2 rows". The first test reads the eighth Example's brief against the
Example's own name, as #339 asked, so the two cannot drift apart again.

THE VISIT. A visit ends at a round boundary once it has made ``passes``
rounds AND lasted ``minVisit``, so it makes ``max(passes, ceil(minVisit /
pass))`` passes. S3's brief quoted ``passes`` alone: on the default cycle
(a 13-minute pass) at a 30 min minimum it said a panel is left after one
pass while the run stays for three. The brief now states the real visit,
through ``readouts.visit_passes``, the helper the Target modal's RUN
section prints the same number with.

The brief reads no site and no clock (it is prose about the graph), so no
site is pinned here.

Every test names the mutation it guards and quotes the failure it produced,
each run in a private copy of ``server/`` (scratchpad ``s4-tonight-mut``,
from byte backups), never in the shared tree.
"""
from __future__ import annotations

import re
from pathlib import Path

from astrodeck.catalog import framing
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.examples import examples
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.readouts import readouts
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.flows.tonight import _budget, brief

FLOW = "flow-brief-grid-and-visit"


def _n(nid, ntype, x=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=0.0, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def _mosaic(*, rows=2, cols=3, skip="", passes=1, min_visit=0, loop=True,
            cycle=None):
    """TARGET M31 at ``rows`` by ``cols``, framed, at Rotate to PA 30, then a
    FILTER CYCLE (the node's default, a 13-minute pass, unless ``cycle``
    gives its params), with the loop wire when ``loop``."""
    nodes = [_n("t", "target", name="M31", ra="00h 42m 44s", dec="+41 16 09",
                rows=rows, cols=cols, overlap=25, rotation=30,
                angle="Rotate to PA", fovX=2.0, fovY=1.33, skip=skip,
                passes=passes, minVisit=min_visit,
                order="Least complete first"),
             _n("y", "cycle", x=200, **(cycle or {}))]
    edges = [_e("t", "target", "y", "run")]
    if loop:
        edges.append(_e("y", "pass", "t", "next"))
    return FlowGraph(nodes=nodes, edges=edges)


def _sentence(text: str, starts: str) -> str:
    """The one sentence of ``text`` that begins with ``starts``."""
    found = [s for s in re.split(r"(?<=\.) ", text) if s.startswith(starts)]
    assert len(found) == 1, f"no single sentence starts {starts!r}: {text}"
    return found[0]


class TestTheGridIsColumnsByRows:
    def test_the_eighth_examples_brief_says_its_own_name(self):
        """The eighth Example's name says "3x2", its block holds 3 columns
        and 2 rows, and its brief calls it a 3x2: read off the name, so a
        rename or a brief that turns back cannot pass.

        RED under mutant "rows x cols" (the brief's size written
        ``{rows}x{cols}`` again, as S3 built it), observed:

            AssertionError: the Example is named 'M31 3x2, rotating' and its
            brief says: This flow arms at astronomical dusk (−30 min). It
            then arms M31. M31 is a 2x3 mosaic of 6 panels at 25% overlap,
            laid out at PA 55.0° with the rotator turned to it at every
            panel. After 1 pass of its filters on a panel it moves on to the
            next (least complete first), ...
            assert 'M31 is a 3x2 mosaic of 6 panels' in 'This flow arms at
            astronomical dusk (−30 min). It then arms M31. M31 is a 2x3
            mosaic of 6 panels at 25% overlap, laid...'

        (and ``test_flows_tonight_band_fixture``'s grader, whose fixture
        holds the brief).
        """
        ex = examples()[-1]
        assert ex.id == "example-m31-mosaic", "premise: the eighth Example"
        block = next(n for n in ex.graph.nodes if n.type == "target")
        (size,) = re.findall(r"\b(\d+)x(\d+)\b", ex.name)
        assert (int(size[0]), int(size[1])) == (block.params["cols"],
                                                block.params["rows"]), \
            "premise: the Example's name is its columns by its rows"
        text = brief(ex.graph)
        want = f"M31 is a {size[0]}x{size[1]} mosaic of 6 panels"
        assert want in text, (
            f"the Example is named {ex.name!r} and its brief says: {text}")

    def test_a_skip_says_the_size_once_in_words(self):
        """2 rows of 3 with panel 2-1 skipped: the size and a label share the
        sentence, so the size is "3 columns by 2 rows", said once, the label
        stays row-column and the sentence says so, and neither "3x2" nor
        "2x3" is in it.

        RED under mutant "the skip sentence keeps NxM" (the size written
        ``{cols}x{rows}`` in the skip sentence too), observed:

            AssertionError: M31 is a 3x2 mosaic shooting 5 of its 6 panels
            (panel 2-1 skipped, written row-column) at 25% overlap, laid out
            at PA 30° with the rotator turned to it at every panel.
            assert 0 == 1
             +  where 0 = <built-in method count of str object at ...>('3
             columns by 2 rows')

        RED under mutant "columns and rows swapped in words"
        (``grid_size(cols, rows)``), observed:

            AssertionError: M31 is a mosaic of 2 columns by 3 rows shooting 5
            of its 6 panels (panel 2-1 skipped, written row-column) at 25%
            overlap, laid out at PA 30° with the rotator turned to it at
            every panel.
            assert 0 == 1
        """
        sentence = _sentence(brief(_mosaic(skip="2-1")), "M31 is a")
        assert sentence.count("3 columns by 2 rows") == 1, sentence
        assert "(panel 2-1 skipped, written row-column)" in sentence, sentence
        assert "3x2" not in sentence and "2x3" not in sentence, sentence
        # DELIBERATE PIN CHANGE (#407, S5-TONIGHT): a list of two takes no
        # comma, so the two skipped panels read "1-3 and 2-3", where S4 pinned
        # "1-3, and 2-3". RED under mutant "the old _join_and" (see
        # test_flows_campaign_brief's TestTheBriefJoinsAListAsEnglish),
        # observed, this test alone in this file:
        #   E       AssertionError: M31 is a mosaic of 3 columns by 2 rows
        #   shooting 4 of its 6 panels (panels 1-3, and 2-3 skipped, written
        #   row-column) at 25% overlap, laid out at PA 30° with the rotator
        #   turned to it at every panel.
        two = _sentence(brief(_mosaic(skip="1-3, 2-3")), "M31 is a")
        assert "shooting 4 of its 6 panels (panels 1-3 and 2-3 skipped, " \
               "written row-column)" in two, two

    def test_control_a_square_grid_reads_the_same_either_way(self):
        """Control: a 2x2 is "2x2" whichever way it is read, and a 1-column
        grid in words is singular. Green on the code and under "rows x
        cols", which cannot tell a square grid's two readings apart and does
        not reach a sentence with a skip. Red, as it must be, under the two
        mutants of the words: "the skip sentence keeps NxM" gave "M31 is a
        1x3 mosaic shooting 2 of its 3 panels ..." and "columns and rows
        swapped in words" gave "M31 is a mosaic of 3 columns by 1 row
        shooting 2 of its 3 panels ..."."""
        assert "M31 is a 2x2 mosaic of 4 panels" in brief(_mosaic(rows=2,
                                                                  cols=2))
        tall = _sentence(brief(_mosaic(rows=3, cols=1, skip="2-1")),
                         "M31 is a")
        assert "a mosaic of 1 column by 3 rows shooting 2 of its 3" in tall, \
            tall


class TestTheVisitIsTheOneTheRunMakes:
    def test_a_minimum_visit_is_stated(self):
        """The default cycle, a 13-minute pass (4 x 60 + 3 x 180 = 780 s), at
        one pass a visit and a 30 min minimum: the run stays three passes,
        and the brief says three, and why.

        A 26 min minimum is exactly two 13-minute passes (1560 s over 780 s),
        and the visit is two, not three: the ceiling is taken of an exact
        quotient.

        RED under mutant "passes alone" (``_mosaic_sentences`` states the
        passes as typed and never asks ``_visit_passes``), observed:

            AssertionError: After 1 pass of its filters on a panel it moves on
            to the next (least complete first), and comes back until every
            panel has its subs.
            assert 'After 3 passes of its filters on a panel' in 'After 1
            pass of its filters on a panel it moves on to the next (least
            complete first), and comes back until every panel has its subs.'
        """
        text = brief(_mosaic(min_visit=30))
        visit = _sentence(text, "After")
        assert "After 3 passes of its filters on a panel" in visit, visit
        why = _sentence(text, "A visit is")
        assert why == ("A visit is 3 passes rather than the 1 asked, since it "
                       "lasts at least its 30 min minimum and a pass takes "
                       "13 min."), why
        assert "After 2 passes of its filters" in brief(_mosaic(min_visit=26))

    def test_its_number_is_the_modals(self):
        """The brief's visit is the Target modal's ``visit_passes`` for the
        same block (``readouts``), at two minimums, so the brief cannot
        count one visit and the RUN section another.

        RED under mutant "passes alone", observed:

            AssertionError: assert 'After 3 passes of its filters' in 'This
            flow arms M31. M31 is a 3x2 mosaic of 6 panels at 25% overlap,
            laid out at PA 30° with the rotator turned to it ...'
        """
        for minimum, want in ((30, 3), (45, 4)):
            graph = _mosaic(min_visit=minimum)
            compiled = compile_plan(graph, "n")
            plan, _ = to_sequence_plan(compiled, graph, flow_id=FLOW)
            (block,) = readouts(compiled, plan, None).values()
            assert block["visit_passes"] == want, "premise: the modal's bound"
            assert f"After {want} passes of its filters" in brief(graph)

    def test_control_the_minimum_already_met_changes_nothing(self):
        """Controls, green on the code and under "passes alone": with no
        minimum the sentence is S3's; three passes at a 30 min minimum
        already last 39 min, so the visit is the three asked and no reason
        is given; and a lane run panel-first has no visit to state."""
        assert "After 1 pass of its filters on a panel it moves on to the " \
               "next (least complete first)" in brief(_mosaic())
        assert "A visit is" not in brief(_mosaic())
        met = brief(_mosaic(passes=3, min_visit=30))
        assert "After 3 passes of its filters" in met and "A visit is" \
            not in met, met
        assert "It shoots one panel at a time" in brief(
            _mosaic(min_visit=30, loop=False))

    def test_the_pass_is_the_blocks_own_stages(self):
        """A pass of L and R at 300 s, two subs of each a pass (perCycle 2):
        20 minutes, so a 30 min minimum makes two passes, and the reason
        says a pass takes 20 min.

        RED under mutant "passes alone", observed:

            AssertionError: This flow arms M31. M31 is a 3x2 mosaic of 6
            panels at 25% overlap, laid out at PA 30° with the rotator turned
            to it at every panel. After 1 pass of its filters on a panel it
            moves on to the next (least complete first), and comes back
            until every panel has its subs. ...
        """
        text = brief(_mosaic(min_visit=30, cycle=dict(
            plan="L 300, R 300", cycles=4, perCycle=2)))
        assert "After 2 passes of its filters" in text, text
        assert "a pass takes 20 min." in text, text

    def test_a_capture_in_the_lane_is_part_of_the_pass(self):
        """A rotating 2x2 whose lane is a FILTER CYCLE (Ha 300 s, one sub a
        pass) and then a CAPTURE (OIII 300 s x 2, one sub a pass): a pass
        is both, 600 s, so a 12 min minimum makes a visit of two passes,
        the Target modal's ``visit_passes`` for the same block. The budget
        counts its hops at that visit: 6 rounds a panel at 2 a visit is 3
        visits, 12 hops for the 2x2, 0.53 h at 160 s, the modal's
        ``visits_total`` priced at the same hop.

        RED under mutant "the pass leaves captures out" (``_pass_s`` adds
        nothing for a step that is not a cycle, so the pass is the cycle's
        300 s alone and the visit ceil(720 / 300) = 3 passes), observed
        (S4-TONIGHT re-verifier, scratchpad ``s4-tonight-reverify-mut``):

            AssertionError: This flow arms M31. M31 is a 2x2 mosaic of 4
            panels at 25% overlap, laid out at PA 30° with the rotator
            turned to it at every panel. After 3 passes of its filters on a
            panel it moves on to the next (least complete first), and comes
            back until every panel has its subs. A visit is 3 passes rather
            than the 1 asked, since it lasts at least its 12 min minimum
            and a pass takes 5 min. ...

        Every other test in this file and ``test_flows_tonight_cycle_
        budget`` stayed green under that mutant: their lanes are a cycle
        alone, or a mixed lane at no minimum, where the pass is never read.
        """
        graph = FlowGraph(
            nodes=[_n("t", "target", name="M31", ra="00h 42m 44s",
                      dec="+41 16 09", rows=2, cols=2, overlap=25,
                      rotation=30, angle="Rotate to PA", fovX=2.0, fovY=1.33,
                      passes=1, minVisit=12, order="Least complete first"),
                   _n("y", "cycle", x=200, plan="Ha 300", cycles=6,
                      perCycle=1, gain=100, bin="1"),
                   _n("o", "capture", x=300, filter="OIII", exposure=300,
                      gain=100, bin="1", count=2, goal=1)],
            edges=[_e("t", "target", "y", "run"),
                   _e("y", "complete", "o", "run"),
                   _e("o", "pass", "t", "next")])
        compiled = compile_plan(graph, "n")
        plan, _ = to_sequence_plan(compiled, graph, flow_id=FLOW)
        (block,) = readouts(compiled, plan, None).values()
        assert (block["pass_s"], block["visit_passes"],
                block["visits_total"]) == (600.0, 2, 12), \
            "premise: a 10 minute pass, two a visit, 12 visits"
        text = brief(graph)
        assert "After 2 passes of its filters" in text, text
        assert "a pass takes 10 min." in text, text
        hops = [b["hop_h"] for b in _budget(compiled, None, 160.0)]
        assert round(sum(hops), 2) == round(
            block["visits_total"] * 160 / 3600, 2) == 0.53, hops


def _rotating(stages, *, min_visit=12):
    """A rotating 2x2 of M31 whose lane is ``stages`` in order, each a
    ``(id, type, x, params)``, with the loop wire from the last. The x of
    each stage is given, so a case can draw the lane against canvas order."""
    nodes = [_n("t", "target", name="M31", ra="00h 42m 44s", dec="+41 16 09",
                rows=2, cols=2, overlap=25, rotation=30, angle="Rotate to PA",
                fovX=2.0, fovY=1.33, passes=1, minVisit=min_visit,
                order="Least complete first")]
    edges, prev, port = [], "t", "target"
    for sid, stype, x, params in stages:
        nodes.append(_n(sid, stype, x=x, **params))
        edges.append(_e(prev, port, sid, "run"))
        prev, port = sid, "complete"
    edges.append(_e(prev, "pass", "t", "next"))
    return FlowGraph(nodes=nodes, edges=edges)


#: A capture stage's sentence as the brief writes it: the first "It
#: captures", a later one "It then captures", each ``filter exposure s x
#: count`` and its gain and bin.
_CAPTURE = re.compile(r"It (?:then )?captures (\S+) (\d+(?:\.\d+)?) s × "
                      r"(\S+) \(gain [^,]+, bin [^)]+\)\.")
#: A FILTER CYCLE's sentence: the first "Capture interleaves", a later one
#: "It then interleaves", the subs each filter takes a pass, and the table.
_CYCLE = re.compile(r"(?:Capture|It then) interleaves (one sub|(\d+) subs) "
                    r"per filter per pass - (.+?) - so every channel grows "
                    r"evenly\.")
_SLOT = re.compile(r"(\S+) (\d+(?:\.\d+)?) s × \S+")


def _named_stages(text: str) -> list[tuple[str, float]]:
    """Every capture stage the brief names, in the order it names them, as
    ``(filters, shutter seconds one pass takes of it)``: a cycle's slot
    exposures times the subs it says each filter takes a pass, and a
    capture's exposure once, since inside the loop a CAPTURE LOOP is a
    one-slot cycle that takes one frame a pass (spec 1.3 item 4; the
    compile's ``per_visit = 1``)."""
    found: list[tuple[int, str, float]] = []
    for m in _CYCLE.finditer(text):
        per = 1 if m.group(1) == "one sub" else int(m.group(2))
        slots = _SLOT.findall(m.group(3))
        found.append((m.start(), " ".join(f for f, _ in slots),
                      sum(float(e) for _, e in slots) * per))
    for m in _CAPTURE.finditer(text):
        found.append((m.start(), m.group(1), float(m.group(2))))
    return [(names, s) for _, names, s in sorted(found)]


def _stated_pass_s(text: str) -> float:
    """The pass the visit sentence states, "a pass takes 10 min.", in s."""
    (minutes,) = re.findall(r"a pass takes (\d+(?:\.\d+)?) min\.", text)
    return float(minutes) * 60.0


class TestEveryCaptureStageIsNamed:
    """#395: the brief describes every capture stage in lane order, and the
    pass its visit sentence states is the sum of the stages it names.

    ``brief`` picked one stage, ``if cyc is not None: ... elif cap is not
    None: ...``, so a lane of a FILTER CYCLE then a CAPTURE LOOP named the
    cycle alone, and a lane of two CAPTURE LOOPs named the first. Since S4
    the visit sentence prices the pass out of every step the block owns
    (``_pass_s``), so the brief said "a pass takes 10 min" of a lane whose
    only named stage takes 5. Each stage now has its sentence, in the
    cursor's order along the wires (``compile.flow_order``), the first worded
    as before and each later one "It then ...", and a cycle that takes more
    than one sub of each filter a pass says how many.

    The cases below read the stages back out of the prose (``_named_stages``)
    and add them up, so the check is the reader's: what the brief says a
    pass is made of must come to what it says a pass takes.

    Every mutant below was run in a private copy of ``server/`` (scratchpad
    ``S5-TONIGHT-mut``, from byte backups), never in the shared tree.

    RED under mutant "if cyc / elif cap restored" (the loop over every stage
    replaced by S4's two branches on ``_first(g, "cycle")`` and
    ``_first(g, "capture")``), observed: all four lane cases failed (4
    failed, 10 passed), and the single-stage control and every other test in
    this file and in ``test_flows_campaign_brief`` stayed green. The first:

        E       AssertionError: named [('Ha', 300.0)] of a pass the brief
            states as 600 s: This flow arms M31. M31 is a 2x2 mosaic of 4
            panels at 25% overlap, laid out at PA 30° with the rotator
            turned to it at every panel. After 2 passes of its filters on a
            panel it moves on to the next (least complete first), and comes
            back until every panel has its subs. A visit is 2 passes rather
            than the 1 asked, since it lasts at least its 12 min minimum and
            a pass takes 10 min. The hop between panels has not been
            measured on this rig yet. Capture interleaves one sub per filter
            per pass - Ha 300 s × 6 - so eve
        E       assert [('Ha', 300.0)] == [('Ha', 300.0...OIII', 300.0)]
        E         Right contains one more item: ('OIII', 300.0)

    The two CAPTUREs' brief ended "... It captures Ha 300 s × 4 (gain 100,
    bin 1)." with no OIII, the lane drawn against canvas order named
    ``[('Ha', 300.0)]`` alone, and the perCycle case read "one sub per
    filter per pass", ``[('L R', 600.0)]``.

    RED under mutant "one sub per filter, whatever perCycle" (the cycle
    sentence's count of subs a pass fixed at "one sub"), observed, in the
    perCycle case alone (1 failed, 13 passed):

        E       AssertionError: named [('L R', 600.0)] of a pass the brief
            states as 1200 s: This flow arms M31. M31 is a 3x2 mosaic of 6
            panels at 25% overlap, ... A visit is 2 passes rather than the 1
            asked, since it lasts at least its 30 min minimum and a pass
            takes 20 min. The hop between panels has not been measured on
            this rig yet. Capture interleaves one sub per filter per pass -
            L 300 s × 4, R 300 s × 4 - so every channel grows evenly.
        E       assert [('L R', 600.0)] == [('L R', 1200.0)]

    RED under mutant "canvas order" (the stages taken in canvas x order
    instead of along the wires), observed, in the lane drawn against canvas
    order alone (1 failed, 13 passed):

        E       AssertionError: [('OIII', 300.0), ('Ha', 300.0)]
        E       assert [('OIII', 300...('Ha', 300.0)] == [('Ha', 300.0...OIII',
            300.0)]
        E         At index 0 diff: ('OIII', 300.0) != ('Ha', 300.0)

    The counts above were taken before the verifier added the last two lane
    cases (a cycle after a capture, a stage the walk never reaches). Re-run
    on the file with them, from byte backups in a private copy: "if cyc /
    elif cap restored" 5 failed, 11 passed (the cycle after a capture fails
    too, since S4's branches named the cycle alone, and the unreached-stage
    case stays green, since ``_first`` found its cycle off the canvas); "one
    sub per filter, whatever perCycle" and "canvas order" 1 failed, 15
    passed each, the same single cases as above.
    """

    def test_a_cycle_then_a_capture(self):
        """The #395 graph: CYCLE Ha 300 s (one sub a pass, 6 cycles), then
        CAPTURE OIII 300 s x 2, at a 12 min minimum. The pass is 600 s, the
        compile's own (``readouts``' ``pass_s``), and the brief names both
        stages, the cycle first, and says a pass takes 10 min."""
        graph = _rotating([
            ("y", "cycle", 200, dict(plan="Ha 300", cycles=6, perCycle=1,
                                     gain=100, bin="1")),
            ("o", "capture", 300, dict(filter="OIII", exposure=300,
                                       gain=100, bin="1", count=2, goal=1))])
        compiled = compile_plan(graph, "n")
        plan, _ = to_sequence_plan(compiled, graph, flow_id=FLOW)
        (block,) = readouts(compiled, plan, None).values()
        assert block["pass_s"] == 600.0, "premise: the compile's pass"
        text = brief(graph)
        named = _named_stages(text)
        assert named == [("Ha", 300.0), ("OIII", 300.0)], (
            f"named {named} of a pass the brief states as "
            f"{_stated_pass_s(text):g} s: {text}")
        assert sum(s for _, s in named) == _stated_pass_s(text) == 600.0
        assert "It then captures OIII 300 s × 2 (gain 100, bin 1)." in text

    def test_two_captures(self):
        """Two CAPTURE LOOPs, Ha then OIII, each one frame a pass in the
        loop: both are named, the second "It then captures", and the pass
        is their sum."""
        graph = _rotating([
            ("a", "capture", 200, dict(filter="Ha", exposure=300, gain=100,
                                       bin="1", count=4, goal=1)),
            ("b", "capture", 300, dict(filter="OIII", exposure=300,
                                       gain=100, bin="1", count=4, goal=1))])
        text = brief(graph)
        named = _named_stages(text)
        assert named == [("Ha", 300.0), ("OIII", 300.0)], (
            f"named {named} of a pass the brief states as "
            f"{_stated_pass_s(text):g} s: {text}")
        assert sum(s for _, s in named) == _stated_pass_s(text)
        assert "It captures Ha 300 s × 4 (gain 100, bin 1). It then " \
               "captures OIII 300 s × 4 (gain 100, bin 1)." in text, text

    def test_lane_order_is_the_wires_not_the_canvas(self):
        """Ha is wired first and drawn to the right of OIII: the brief names
        Ha first, the order the run shoots them in."""
        graph = _rotating([
            ("a", "capture", 500, dict(filter="Ha", exposure=300, gain=100,
                                       bin="1", count=4, goal=1)),
            ("b", "capture", 200, dict(filter="OIII", exposure=300,
                                       gain=100, bin="1", count=4, goal=1))])
        named = _named_stages(brief(graph))
        assert named == [("Ha", 300.0), ("OIII", 300.0)], named

    def test_a_cycle_says_the_subs_each_filter_takes_a_pass(self):
        """L and R at 300 s, two subs of each a pass: the sentence says "2
        subs per filter per pass", so the named stage is the 20 min the
        visit sentence prices, where "one sub" would add up to 10."""
        text = brief(_mosaic(min_visit=30, cycle=dict(
            plan="L 300, R 300", cycles=4, perCycle=2)))
        named = _named_stages(text)
        assert named == [("L R", 1200.0)], (
            f"named {named} of a pass the brief states as "
            f"{_stated_pass_s(text):g} s: {text}")
        assert sum(s for _, s in named) == _stated_pass_s(text)
        assert "Capture interleaves 2 subs per filter per pass - L 300 s × " \
               "4, R 300 s × 4 - so every channel grows evenly." in text

    def test_a_cycle_after_a_capture_says_it_then(self):
        """A CAPTURE LOOP (Ha 300 s) then a FILTER CYCLE (L and R at 300 s,
        one sub each a pass), at a 20 min minimum: the cycle is the second
        stage, so its sentence says "It then interleaves", not the first
        stage's "Capture interleaves", which would read as the lane's
        start; and the two add up to the 15 min pass. (``_CYCLE`` accepts
        either lead, so ``_named_stages`` alone cannot tell them apart.)

        RED under mutant "later cycle worded as first" (``_stage_sentence``'s
        cycle lead fixed at "Capture interleaves"), observed (1 failed, 15
        passed; added by the S5-TONIGHT verifier):

            E       AssertionError: This flow arms M31. M31 is a 2x2 mosaic
                of 4 panels at 25% overlap, ... A visit is 2 passes rather
                than the 1 asked, since it lasts at least its 20 min minimum
                and a pass takes 15 min. The hop between panels has not been
                measured on this rig yet. It captures Ha 300 s × 4 (gain
                100, bin 1). Capture interleaves one sub per filter per pass
                - L 300 s × 4, R 300 s × 4 - so every channel grows evenly.
            E       assert 'It captures Ha 300 s × 4 (gain 100, bin 1). It
                then interleaves one sub per filter per pass - L 300 s × 4,
                R 300 s × 4 - so every channel grows evenly.' in 'This flow
                arms M31. ...'
        """
        graph = _rotating([
            ("a", "capture", 200, dict(filter="Ha", exposure=300, gain=100,
                                       bin="1", count=4, goal=1)),
            ("y", "cycle", 300, dict(plan="L 300, R 300", cycles=4,
                                     perCycle=1, gain=100, bin="1"))],
            min_visit=20)
        text = brief(graph)
        named = _named_stages(text)
        assert named == [("Ha", 300.0), ("L R", 600.0)], named
        assert sum(s for _, s in named) == _stated_pass_s(text) == 900.0
        assert ("It captures Ha 300 s × 4 (gain 100, bin 1). It then "
                "interleaves one sub per filter per pass - L 300 s × 4, R 300 "
                "s × 4 - so every channel grows evenly.") in text, text
        assert "Capture interleaves" not in text, text

    def test_a_stage_the_walk_never_reaches_is_still_named(self):
        """A FILTER CYCLE wired back into its TARGET's ``arm`` (the flow
        loop #149 describes, which validation refuses by name but the
        editor can draw): ``compile.flow_order`` drops both nodes, and the
        brief still names the cycle, as it did when it read the first cycle
        off the canvas, because it describes the graph the operator drew
        (``_capture_stages``' canvas-order tail).

        RED under mutant "unreached stages dropped" (``_capture_stages``
        returning the walked stages alone), observed (1 failed, 15 passed;
        added by the S5-TONIGHT verifier):

            E       AssertionError: This flow arms M31.
            E       assert [] == [('L R', 120.0)]
            E         Right contains one more item: ('L R', 120.0)
        """
        graph = FlowGraph(
            nodes=[_n("t", "target", name="M31", ra="00h 42m 44s",
                      dec="+41 16 09"),
                   _n("y", "cycle", x=200, plan="L 60, R 60", cycles=10,
                      perCycle=1)],
            edges=[_e("t", "target", "y", "run"),
                   _e("y", "complete", "t", "arm")])
        assert any("loops back on itself" in e
                   for e in graph.validation_errors()), \
            "premise: validation refuses this loop"
        text = brief(graph)
        assert _named_stages(text) == [("L R", 120.0)], text
        assert ("Capture interleaves one sub per filter per pass - L 60 s × "
                "10, R 60 s × 10 - so every channel grows evenly.") in text

    def test_control_one_stage_reads_as_it_did(self):
        """Controls: a lane of one stage keeps its sentence word for word, a
        cycle at one sub a pass says "one sub", and every shipped Example
        names exactly the one capture stage it draws. Green on the code and
        under every mutant above."""
        text = brief(_mosaic(min_visit=30))
        assert ("Capture interleaves one sub per filter per pass - L 60 s × "
                "45, R 60 s × 45, G 60 s × 45, B 60 s × 45, Ha 180 s × 45, "
                "OIII 180 s × 45, SII 180 s × 45 - so every channel grows "
                "evenly.") in text, text
        assert "It then" not in text, text
        for ex in examples():
            stages = [n for n in ex.graph.nodes
                      if n.type in ("cycle", "capture")]
            assert len(stages) == 1, f"premise: {ex.id} draws one stage"
            assert len(_named_stages(brief(ex.graph))) == 1, ex.id


class TestFramingSaysTheWorkedCaseColumnsFirst:
    def test_no_1x4_is_left_in_framing(self):
        """The #339 comment's last instance: ``convergence_share`` and
        ``angle_tolerance_deg`` named doctor M6's worked case "1x4", which
        spec A.1 now calls the 4x1 (4 columns by 1 row). Read off the source
        of the two functions, the one place the size is written.

        RED under mutant "the 1x4 restored" (``convergence_share``'s
        docstring back to "0.389 for a 1x4 of 2.0 x 1.33 deg"), observed:

            AssertionError: framing.py still writes the worked case rows by
            columns
              '1x4' is contained here:
                389 for a 1x4 of 2.0 x 1.33 deg at 10% at Dec 75,
        """
        source = Path(framing.__file__).read_text(encoding="utf-8")
        assert "1x4" not in source, (
            "framing.py still writes the worked case rows by columns")
        assert source.count("4x1") >= 2, \
            "premise: both docstrings name the worked case"
