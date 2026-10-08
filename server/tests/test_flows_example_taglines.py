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
from astrodeck.flows.tonight import _campaign, brief

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
        mutants above.

        WP-112 RE-PIN (#192's copy sweep, #603 job A): the M16 Example's
        tagline no longer opens "Dome opens at dusk, lens-cap flats in the
        twilight window" (nothing opens the dome and no engine stage runs
        DUSK FLATS), so its pinned opening is the new one, "Full-service
        night:". The words are held by test_w15_dusk_flats_claim.py. Every
        other Example keeps its opening."""
        before = {
            "example-campaign": "The pool hands out targets until every quota "
                                "is met;",
            "example-m31": "Dusk-gated deep-sky run:",
            "example-m16": "Full-service night:",
            "example-cycle": "One sub per filter per pass",
            "example-pool": "One lane, four candidates",
            "example-nb": "Moon-tolerant Ha:",
            "example-eaa": "No guiding, no rules:",
        }
        got = {e.id: e.tagline for e in examples()[:-1]}
        assert set(got) == set(before)
        for fid, opening in before.items():
            assert got[fid].startswith(opening), (fid, got[fid])


#: The campaign Example's tagline, word for word (#746). Its first sentence is
#: the one it always had; the dawn clause is now the CAMPAIGN tab's own.
CAMPAIGN_TAGLINE = ("The pool hands out targets until every quota is met; "
                    "'target done' loops back to advance it. Dawn parks the "
                    "mount and warms the camera, and each dusk resumes "
                    "mid-cycle from the session log.")

#: The dawn fact the tagline and the CAMPAIGN tab (`tonight._DAWN`) both say,
#: in the same words, so a reader of the library card and a reader of the tab
#: cannot be told two different things about one night.
DAWN_FACT = "parks the mount and warms the camera"


def _campaign_example():
    ex = next(e for e in examples() if e.id == "example-campaign")
    return ex


class TestTheCampaignExamplesTagline:
    """#746: the library card said "Dawn parks + closes with the cooler held
    cold for day darks", and the CAMPAIGN tab on the same page said the
    cooler does NOT stay cold for day darks and that the dome is not driven.
    Two surfaces disagreed about one night, and the card is the one an
    operator reads when choosing a flow.

    The card now says only what both agree on and the compiled plan keeps:
    the mount parks, the camera warms, each dusk resumes. It makes no claim
    about the dome and none about the cooler. It does NOT say day darks are
    taken either: the wind-down does take them for this Example (the queue is
    wired to SHUTDOWN COMPLETE and the plan funds `day_darks`), between the
    park and the warm, but the CAMPAIGN tab's `_DAWN` still denies them, and a
    card that disagreed with the tab again would re-open this issue. That
    sentence belongs to Tonight's copy, not to this card.
    """

    def test_it_makes_no_promise_the_campaign_tab_denies(self):
        """The card, word for word, and neither half of the old promise.

        RED under mutant "the old tagline restored" (``_campaign``'s tagline
        back to "... Dawn parks + closes with the cooler held cold for day
        darks, and each dusk resumes mid-cycle from the session log."),
        observed:

            AssertionError: the campaign Example promises the cooler is held
            cold for day darks, which the CAMPAIGN tab denies (_DAWN: 'the
            cooler does not stay cold for day darks')
        """
        tagline = _campaign_example().tagline
        assert "cold" not in tagline.lower(), (
            "the campaign Example promises the cooler is held cold for day "
            "darks, which the CAMPAIGN tab denies (_DAWN: 'the cooler does "
            "not stay cold for day darks')")
        assert "closes" not in tagline, (
            "the campaign Example says dawn closes something; the CAMPAIGN "
            "tab says the dome is not driven")
        assert tagline == CAMPAIGN_TAGLINE
        assert "—" not in tagline and "–" not in tagline, \
            "an Example's strings carry no em or en dash (examples.py)"

    def test_it_says_the_dawn_fact_the_campaign_tab_says(self):
        """The two surfaces share the dawn fact in the same words, and the
        tab still denies the cold hold the card no longer promises (its
        premise: were the tab to start promising it, this card could too).

        RED under mutant "the dawn fact reworded" (the tagline's "Dawn parks
        the mount and warms the camera" made "Dawn parks and warms"),
        observed: ``AssertionError: the card and the CAMPAIGN tab no longer
        say the same thing about dawn: 'parks the mount and warms the camera'
        is missing from the card``.
        """
        ex = _campaign_example()
        note = _campaign(ex.graph, None)["note"]
        assert DAWN_FACT in note, f"premise: the tab's dawn fact moved: {note}"
        assert "does not stay cold" in note, (
            f"premise: the tab no longer denies a cooler hold: {note}")
        assert DAWN_FACT in ex.tagline, (
            f"the card and the CAMPAIGN tab no longer say the same thing "
            f"about dawn: {DAWN_FACT!r} is missing from the card")

    def test_each_claim_it_makes_is_one_the_plan_keeps(self):
        """A claim nothing keeps is the class this card was in. The mount
        parks and the camera warms because ``plan_extras`` says so for every
        flow-derived plan, and the dusk resumes because the plan resumes
        across nights and Tonight's own brief says the flow re-arms.

        RED under mutant "the Example stops being a campaign" (the campaign
        Example's DUSK WINDOW override ``{"repeat": "Nightly until pool
        complete"}`` made ``{"repeat": "Single night"}``), observed:

            AssertionError: Tonight's brief no longer says the flow comes
            back at dusk

        (the dawn-fact case above goes red under it too, on its premise: the
        tab's note stops being the campaign's). A mutant on the plan flags
        themselves would be a change to ``to_plan.plan_extras``, a file this
        work package does not own; those assertions are the flags read
        straight off the compiled plan.
        """
        ex = _campaign_example()
        plan, _ = to_sequence_plan(compile_plan(ex.graph, ex.name), ex.graph,
                                   flow_id=ex.id)
        assert plan.park_when_done is True, "the card says dawn parks"
        assert plan.warm_cooler_when_done is True, "the card says it warms"
        assert plan.resume_across_nights is True, (
            "the card says each dusk resumes")
        assert "re-arms at the next dusk" in brief(ex.graph), (
            "Tonight's brief no longer says the flow comes back at dusk")
