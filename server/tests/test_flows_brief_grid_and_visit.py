"""The STORY brief's mosaic sentences: the grid written columns by rows (S4
orchestrator ruling 1, #339) and the visit the run really makes (spec 5.3,
#189 S4 item 14).

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
        two = _sentence(brief(_mosaic(skip="1-3, 2-3")), "M31 is a")
        assert "shooting 4 of its 6 panels (panels 1-3, and 2-3 skipped, " \
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
