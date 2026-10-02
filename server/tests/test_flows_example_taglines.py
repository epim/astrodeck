# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The M31 mosaic Example's tagline says only what holds (spec S3 item 5,
#353).

S3 shipped the eighth Example with the tagline "...every pass of the LRGB
cycle hops to the next panel, so a night cut short still covers all of
M31." That holds only once the first rotation is done: the group visits
one panel a pass, so a night cut short after three visits leaves three of
the six panels empty, and "covers all of M31" is then a promise about sky
nobody imaged. The tagline is printed on the library card, which is where
an operator chooses a flow.

What does hold, and what it now says: every full rotation deepens every
panel, and a night cut short leaves the panels within a pass of each other.
That rests on three settings of the Example, pinned below beside the words,
so a change to any of them turns this red before the card can promise
something the run will not keep:

* one ROTATING group (the loop wire), so a pass hops to the next panel;
* ONE PASS A VISIT with NO MINIMUM VISIT, so each visit adds one pass to one
  panel (at two passes a visit a panel could run two passes ahead);
* LEAST COMPLETE FIRST, so the next panel visited is one of the furthest
  behind, and no panel is visited twice before every panel is visited once.

Every test names the mutation it guards and quotes the failure it produced,
each run in a private copy of ``server/`` (scratchpad ``s4-tonight-mut``,
from byte backups), never in the shared tree.
"""
from __future__ import annotations

from astrodeck.flows.compile import compile_plan
from astrodeck.flows.examples import examples
from astrodeck.flows.to_plan import to_sequence_plan

#: The tagline, word for word.
TAGLINE = ("One TARGET laid out as six panels along the galaxy, 25% overlap: "
           "every pass of the LRGB cycle hops to the next panel, so each "
           "full rotation deepens every panel, and a night cut short leaves "
           "the panels within a pass of each other.")


def _eighth():
    ex = examples()[-1]
    assert ex.id == "example-m31-mosaic", "premise: the eighth Example"
    return ex


class TestTheMosaicExamplesTagline:
    def test_it_says_only_what_holds(self):
        """The tagline, word for word, and not the promise S3 shipped.

        RED under mutant "the old tagline restored" (``_m31_mosaic``'s
        tagline back to "...hops to the next panel, so a night cut short
        still covers all of M31."), observed:

            AssertionError: the M31 mosaic Example promises a night cut
            short covers all of M31, which holds only after a full rotation
            assert 'covers all of M31' not in 'One TARGET ... all of M31.'
              'covers all of M31' is contained here:
                ort still covers all of M31.
        """
        tagline = _eighth().tagline
        assert "covers all of M31" not in tagline, (
            "the M31 mosaic Example promises a night cut short covers all "
            "of M31, which holds only after a full rotation")
        assert tagline == TAGLINE
        assert "—" not in tagline and "–" not in tagline, \
            "an Example's strings carry no em or en dash (examples.py)"

    def test_the_settings_it_stands_on(self):
        """The words hold because the Example compiles to one rotating group
        at one pass a visit, no minimum visit, least complete first.

        RED under mutant "two passes a visit" (the Example's block given
        ``passes: 2``), observed:

            AssertionError: the Example compiles to [('rotate', 2, 0.0,
            'least_complete')]; its tagline says a night cut short leaves the
            panels within a pass of each other
            assert [('rotate', 2...st_complete')] == [('rotate',
            1...st_complete')]
              At index 0 diff: ('rotate', 2, 0.0, 'least_complete') !=
              ('rotate', 1, 0.0, 'least_complete')
        """
        ex = _eighth()
        plan, _ = to_sequence_plan(compile_plan(ex.graph, ex.name), ex.graph,
                                   flow_id=ex.id)
        shape = [(g.mode, g.visit_passes, g.visit_min_s, g.order)
                 for g in plan.groups]
        assert shape == [("rotate", 1, 0.0, "least_complete")], (
            f"the Example compiles to {shape}; its tagline says a night cut "
            f"short leaves the panels within a pass of each other")
        assert len(plan.targets) == 6, "premise: six panels"

    def test_control_the_other_seven_are_untouched(self):
        """Control: the seven transcribed Examples keep their taglines; only
        the mosaic's words changed. Green on the code and under both
        mutants above."""
        before = {
            "example-campaign": "The pool hands out targets until every quota "
                                "is met;",
            "example-m31": "Dusk-gated deep-sky run:",
            "example-m16": "Dome opens at dusk,",
            "example-cycle": "One sub per filter per pass",
            "example-pool": "One lane, four candidates",
            "example-nb": "Moon-tolerant Ha:",
            "example-eaa": "No guiding, no rules:",
        }
        got = {e.id: e.tagline for e in examples()[:-1]}
        assert set(got) == set(before)
        for fid, opening in before.items():
            assert got[fid].startswith(opening), (fid, got[fid])
