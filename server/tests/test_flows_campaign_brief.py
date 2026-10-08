# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The CAMPAIGN tab and the STORY tab's generated brief.

Both are new in the 2026-08-14 export and both are specified in PROSE only - the
export changed no screenshot, so there is no reference render for either. That
makes the tests the only thing standing between the spec and a plausible
invention, and two inventions were available here:

* the prototype hard-codes each pool member's progress (`fr = [1, 0.51, 0.1, …]`)
  and the completion estimate ("~6 clear nights"). The README says production
  reads the first from the session ledger and the second from the scheduler.
  The ledger exists. THE PROJECTION DOES NOT - nothing on this server forecasts
  clear nights - so the number is not printed at all.
* the brief is prose, and prose is the easiest thing in this codebase to write
  confidently and wrongly. Every sentence here is asserted against the params it
  claims to quote, so a sentence that stops tracking its param fails rather than
  merely reading well.
"""
from __future__ import annotations

import pytest

from astrodeck.flows.compile import compile_plan
from astrodeck.flows.examples import examples
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.flows.tonight import (_campaign, brief,
                                     frames_by_target_from_reports)


def _ex(flow_id: str):
    return next(e for e in examples() if e.id == flow_id).graph


def _without_ids(value):
    """``value`` with every ``id`` key dropped, recursively: ``to_sequence_plan``
    mints a fresh uuid for each instruction, target and step, so two plans of
    one graph are never equal until those are set aside."""
    if isinstance(value, dict):
        return {k: _without_ids(v) for k, v in value.items() if k != "id"}
    if isinstance(value, list):
        return [_without_ids(v) for v in value]
    return value


class _FB:
    def __init__(self, filter, frames):        # noqa: A002
        self.filter, self.frames = filter, frames


class _TB:
    def __init__(self, name, by_filter):
        self.name, self.by_filter = name, by_filter


class _Rep:
    def __init__(self, targets):
        self.targets = targets


# ============================================================== the brief

class TestTheBriefQuotesTheGraph:
    def test_every_number_comes_from_a_param(self):
        """"Edit a param, the sentence changes" is the export's own rule, so a
        changed param must be visible in the prose and the OLD value gone.

        DELIBERATE PIN CHANGE (backlog WP-112, #707, wave 15 integration). This
        pinned the cloud threshold as a number the brief QUOTES: it asserted
        "40%" in the brief, set the CLOUD WATCH dial to 25 and asserted "25%"
        replaced it. That pinned the #707 bug itself: the engine never reads the
        CLOUD WATCH dial (the cloud verdict comes from the frame's own judgement
        and the safety monitor, not from this percentage), so a brief that quoted
        it told the operator a number that decided nothing. WP-112 stopped
        quoting it. The rule still holds for every number the brief DOES quote
        (the case below, ``test_the_cycle_table_is_spelled_out_slot_by_slot``,
        and the others); what this case pins is the other half of it: a dial the
        engine ignores is NOT in the prose, so changing it leaves the brief
        identical.

        Named mutant "the dial quoted again" (``flows/tonight.py``'s ``brief``:
        the cloud sentence's opening made ``f"If cloud cover is above
        {cw.params.get('threshold')}% (this trigger fires on ..."``), run from
        a byte backup and restored byte-identically (sha256 compared): RED,
        ``assert '40%' not in before``, with the brief reading "If cloud cover
        is above 40% (this trigger fires on the cloud detector's own verdict),
        imaging pauses at the frame boundary ...".
        """
        g = _ex("example-cycle")
        before = brief(g)
        assert "40%" not in before, before

        cw = next(n for n in g.nodes if n.type == "cloudwatch")
        cw.params["threshold"] = 25
        assert brief(g) == before, (
            "a dial the engine never reads changed the brief: the sentence "
            "quotes a number that decides nothing")

    def test_the_hold_sentence_quotes_no_clear_for_dial(self):
        """#743, the same class as #707 one clause later in the same sentence.
        CLOUD WATCH's ``clearFor`` dial ("Clear must hold", 4 minutes) fed the
        brief's "once the sky holds clear for 4 min it ..." and nothing else:
        no reader of ``clearFor`` exists in the engine, which releases a hold
        after ``CLOUD_RESUME_CLEAR_PROBES`` consecutive clear check frames
        (``_hold_for_clear``). So the operator who read "4 min" and moved the
        dial to 15 was promised a patience the run does not have, and the
        brief changed while the night did not.

        The sentence now says what the engine does (consecutive check frames
        read clear) and prints no minutes, so this pins the same two halves
        #707's case does: the old number is gone, and changing the dial leaves
        the brief identical.

        Named mutant "the clearFor dial quoted again" (``flows/tonight.py``'s
        ``brief``: the cloud sentence's clause put back to ``t += (f"; once
        the sky holds clear for {cw.params.get('clearFor')} min it")``), run
        from a byte backup and restored byte-identically (sha256 compared):
        RED, ``AssertionError: the cloud sentence still quotes the CLOUD WATCH
        clearFor dial (4 min), which the engine never reads: ... once the sky
        holds clear for 4 min it re-cools the sensor to setpoint and waits for
        it to stabilize, ...``.

        Named mutant "the dial carried in other words" (the clause made
        ``t += (f"; once consecutive check frames read clear for
        {cw.params.get('clearFor')} of them it")``, so the first pair of
        assertions passes and only the second half can catch it), same
        method: RED, ``AssertionError: the clearFor dial changed the brief:
        the sentence quotes a number the engine never reads``.
        """
        g = _ex("example-cycle")
        cw = next(n for n in g.nodes if n.type == "cloudwatch")
        assert cw.params["clearFor"] == 4, (
            "premise: the dial holds the number the old sentence quoted")
        before = brief(g)
        assert "4 min" not in before and "holds clear for" not in before, (
            f"the cloud sentence still quotes the CLOUD WATCH clearFor dial "
            f"(4 min), which the engine never reads: {before}")

        cw.params["clearFor"] = 17
        after = brief(g)
        assert after == before, (
            "the clearFor dial changed the brief: the sentence quotes a "
            "number the engine never reads")
        assert "17" not in after, after

    def test_the_clear_for_dial_reaches_no_plan_field(self):
        """THE PREMISE of the case above, so the sentence cannot be right only
        by luck: the compile and the plan are identical whatever ``clearFor``
        holds (the fresh uuid every entry carries is not a setting). The day
        the dial is carried to the engine this goes red, and the brief should
        then say the number again, because it would be one the run acts on."""
        def plan_of(clear_for):
            g = _ex("example-cycle")
            next(n for n in g.nodes
                 if n.type == "cloudwatch").params["clearFor"] = clear_for
            compiled = compile_plan(g, "x")
            plan, unmapped = to_sequence_plan(compiled, g)
            return compiled, _without_ids(plan.model_dump()), unmapped

        assert plan_of(4) == plan_of(17), (
            "clearFor now changes the compiled plan or what it reports: the "
            "engine reads it, so Tonight's brief may quote it again")

    def test_the_hold_sentence_says_what_releases_the_hold(self):
        """What the sentence says INSTEAD of the minutes is the engine's own
        rule: it resumes when the check frames read clear in a row
        (``_hold_for_clear``'s ``clear_streak``). "Consecutive" is only true
        while the engine needs more than one, which is the premise asserted
        first: at 1 the word would claim a streak nothing requires.

        Named mutant "the release said without its streak" (the clause made
        ``t += "; once the sky reads clear it"``), run from a byte backup:
        RED, ``AssertionError: ... once the sky reads clear it re-cools ...``.
        """
        from astrodeck.sequence.engine import CLOUD_RESUME_CLEAR_PROBES
        assert CLOUD_RESUME_CLEAR_PROBES >= 2, (
            "premise: the engine needs a streak, so 'consecutive' is true")
        b = brief(_ex("example-cycle"))
        assert "; once consecutive check frames read clear it re-cools" in b, b

    def test_the_cycle_table_is_spelled_out_slot_by_slot(self):
        b = brief(_ex("example-cycle"))
        for slot in ("L 60 s × 45", "Ha 180 s × 45", "SII 180 s × 45"):
            assert slot in b, f"{slot!r} missing from: {b}"

    def test_the_cooler_gate_is_named_in_the_resume_checklist(self):
        """The operator's way of noticing the gate is missing. It is the first
        step of the checklist and the export puts it there deliberately."""
        b = brief(_ex("example-campaign"))
        assert "re-cools the sensor to setpoint and waits for it to stabilize" in b
        i_cool = b.index("re-cools the sensor")
        assert i_cool < b.index("restores the filter") < b.index("re-centers")

    def test_skipping_the_cooler_check_removes_the_sentence(self):
        g = _ex("example-campaign")
        hold = next(n for n in g.nodes if n.type == "holdresume")
        hold.params["cooler"] = "Skip check"
        assert "re-cools the sensor" not in brief(g)

    def test_a_campaign_says_it_comes_back_and_a_single_night_does_not(self):
        camp, one = brief(_ex("example-campaign")), brief(_ex("example-m16"))
        assert "re-arms at the next dusk" in camp
        assert "re-arms" not in one

    def test_the_advance_wire_changes_what_the_report_sentence_says(self):
        assert "the pool advances to the next best" in brief(_ex("example-campaign"))
        assert "A session report is appended when the run ends." in brief(_ex("example-m16"))

    def test_a_sentence_appears_only_when_its_node_does(self):
        """EAA has no dusk, no guider, no cloud watch, no safety monitor."""
        b = brief(_ex("example-eaa"))
        for absent in ("arms at astronomical dusk", "guides with", "cloud cover",
                       "Rain, wind, or power"):
            assert absent not in b, f"{absent!r} in a flow with no such node: {b}"

    def test_the_paragraph_never_opens_with_a_dangling_it_then(self):
        """PROTOTYPE DEFECT, fixed. `brief()` opens the target sentence with a
        fixed "It then", which reads correctly after the arming sentence and is
        broken English without one. EAA has no DUSK WINDOW, so its brief began
        "It then arms M27 - Dumbbell." with nothing for "it" to refer to."""
        b = brief(_ex("example-eaa"))
        assert not b.startswith("It then"), b
        assert b.startswith("This flow arms M27"), b

    def test_an_empty_graph_is_an_empty_brief_not_a_sentence_about_nothing(self):
        assert brief(FlowGraph(nodes=[], edges=[])) == ""
        assert brief(None) == ""

    @pytest.mark.parametrize("ex", examples(), ids=lambda e: e.id)
    def test_no_shipped_brief_carries_an_em_dash(self, ex):
        b = brief(ex.graph)
        assert "—" not in b and "–" not in b, b

    @pytest.mark.parametrize("ex", examples(), ids=lambda e: e.id)
    def test_every_brief_is_whole_sentences(self, ex):
        b = brief(ex.graph)
        if not b:
            return
        assert b[0].isupper(), b
        assert b.endswith("."), b
        assert "  " not in b, f"double space in: {b}"
        assert "None" not in b, f"an unset param reached the prose: {b}"


def _n(nid, ntype, x=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=0.0, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def _mosaic(*, loop=True, skip="3-1", passes=1, angle="Rotate to PA",
            slew=False):
    """dusk -> TARGET M31 3x2 -> [SLEW ->] CYCLE, with the pass wire from the
    cycle into the block's `next` when ``loop``."""
    nodes = [_n("d", "dusk"),
             _n("t", "target", x=100, name="M31", ra="00h 42m 44s",
                dec="+41 16 09", rows=3, cols=2, overlap=25, rotation=30,
                angle=angle, fovX=2.0, fovY=1.33, skip=skip, passes=passes,
                order="Setting first"),
             _n("y", "cycle", x=300)]
    edges = [_e("d", "window", "t", "arm")]
    if slew:
        nodes.append(_n("s", "slew", x=200, tol=0.5, solver="ASTAP"))
        edges += [_e("t", "target", "s", "run"), _e("s", "centered", "y", "run")]
    else:
        edges.append(_e("t", "target", "y", "run"))
    if loop:
        edges.append(_e("y", "pass", "t", "next"))
    return FlowGraph(nodes=nodes, edges=edges)


class TestTheBriefReadsAMosaic:
    """S3 item 5 and spec 1.7: a multi-panel TARGET gets its own sentences,
    quoting its params, and the SLEW + CENTER sentence is gone."""

    def test_no_slew_sentence(self):
        """A legacy SLEW's tolerance and solver never reached the run, so the
        brief no longer repeats them ("slews and plate-solves to within
        0.5′ (ASTAP)"). The node is still on the canvas for the doctor.

        Mutant "keep the SLEW clause" (the sentence restored) failed:
            E   assert 'slews and plate-solves' not in 'This flow a...rows
                evenly.'
            E     'slews and plate-solves' is contained here:
            E       target it slews and plate-solves to within 0.5\\u2032
                (ASTAP). Capture interleaves one sub per filter per pass - ...
        """
        g = _mosaic(slew=True)
        assert any(n.type == "slew" for n in g.nodes), \
            "premise: the legacy SLEW is on the canvas"
        b = brief(g)
        assert "slews and plate-solves" not in b, b
        assert "0.5′" not in b and "ASTAP" not in b, b

    def test_the_mosaic_sentence_quotes_the_block(self):
        """Grid, panels shot and skipped, overlap, angle, order, passes and
        the hop, each from the node's own params.

        Mutant "no mosaic sentence" (``brief`` never adds one) failed, and in
        the three tests below that read the sentence:
            E   AssertionError: 'M31 is a 3x2 mosaic shooting 5 of its 6
                panels (3-1 skipped) at 25% overlap, laid out at PA 30\\xb0
                with the rotator turned to it at every panel.' missing from:
                This flow arms at astronomical dusk (\\u221230 min). It then

        DELIBERATE PIN CHANGE (mosaic S4, S4 orchestrator ruling 1, #339;
        re-pinned by the S4 integration, #398): S3 wrote the grid rows by
        columns, "3x2" for this block of 3 rows and 2 columns, while the
        Examples and the framing card write columns by rows. The brief now
        writes the size columns first, and where a panel label shares the
        sentence it says the size once in words and says the labels are
        row-column, so "2x3" and "3-1" cannot be read into each other. The
        failure quoted above is S3's; the sentences asserted below are S4's.

        Re-observed on the S4 wording in scratchpad/s4-integrate-q7m2.
        Mutant "no mosaic sentence" (the ``seg.extend(_mosaic_sentences(...))``
        that adds them made to add nothing), 5 failed, this one first:
            E           AssertionError: 'M31 is a mosaic of 2 columns by 3
                rows shooting 5 of its 6 panels (panel 3-1 skipped, written
                row-column) at 25% overlap, laid out at PA 30\\xb0 with the
                rotator turned to it at every panel.' missing from: This flow
                arms at astronomical dusk (\\u221230 min). It then arms M31.
                Capture interleaves one sub per filter per pass - ...

        Mutant "the size rows by columns, as S3 wrote it" (the no-skip
        sentence's ``{cols}x{rows}`` made ``{rows}x{cols}``), which the
        skipped-panel sentence cannot see, since it says the size in words;
        only this test failed:
            E       assert 'M31 is a 2x3 mosaic of 6 panels at 25% overlap' in
                'This flow arms at astronomical dusk (\\u221230 min). It then
                arms M31. M31 is a 3x2 mosaic of 6 panels at 25% overlap,
                laid...
        """
        b = brief(_mosaic())
        for part in ("M31 is a mosaic of 2 columns by 3 rows shooting 5 of "
                     "its 6 panels (panel 3-1 skipped, written row-column) at "
                     "25% overlap, laid out at PA 30° with the rotator turned "
                     "to it at every panel.",
                     "After 1 pass of its filters on a panel it moves on to "
                     "the next (setting first), and comes back until every "
                     "panel has its subs.",
                     "The hop between panels has not been measured on this "
                     "rig yet."):
            assert part in b, f"{part!r} missing from: {b}"
        two = brief(_mosaic(passes=2, skip=""))
        assert "M31 is a 2x3 mosaic of 6 panels at 25% overlap" in two, two
        assert "After 2 passes of its filters" in two, two

    def test_the_loop_wire_decides_rotating_or_one_at_a_time(self):
        """With the pass wire the panels rotate every pass; without it they
        run one at a time (spec 1.4: deleting the wire changes only the
        mode).

        Mutant "loop wire ignored" (every mosaic read as rotating) failed:
            E   assert 'It shoots one panel at a time, each finished before
                the next (setting first).' in 'This flow arms at astronomical
                dusk (\\u221230 min). It then arms M31. M31 is a 3x2 mosaic
                shooting 5 of its 6 panels (3-1 ...
        """
        b = brief(_mosaic(loop=False))
        assert "It shoots one panel at a time, each finished before the " \
               "next (setting first)." in b, b
        assert "moves on to the next" not in b, b

    def test_a_measured_hop_is_quoted_and_a_seed_never_is(self):
        """A measured 160 s hop reads "about 2 m 40 s"; no cost, or one that
        is no measurement, reads "not measured yet", never the engine's
        150 s seed.

        Mutant "hop cost ignored" (the measured cost never quoted) failed:
            E   AssertionError: assert 'A hop between panels takes about 2 m
                40 s, as measured on this rig.' in 'This flow arms at
                astronomical dusk (\\u221230 min). It then arms M31. M31 is a
                3x2 mosaic shooting 5 of its 6 panels (3-1 ...
        """
        assert "A hop between panels takes about 2 m 40 s, as measured on " \
               "this rig." in brief(_mosaic(), hop_cost_s=160.0)
        assert "about 45 s" in brief(_mosaic(), hop_cost_s=45.2)
        for cost in (None, 0, -1.0, float("nan")):
            b = brief(_mosaic(), hop_cost_s=cost)
            assert "has not been measured on this rig yet" in b, (cost, b)
            assert "150" not in b and "2 m 30 s" not in b, (cost, b)

    def test_a_fixed_camera_says_the_run_checks_the_angle(self):
        b = brief(_mosaic(angle="Camera fixed at PA"))
        assert "laid out at PA 30° with the camera fixed there by hand, and " \
               "the run checks the angle at every panel." in b, b
        assert "rotator" not in b, b

    def test_a_block_at_no_angle_says_it_cannot_run(self):
        """A grid at "Any angle" is laid out at no angle, so the brief says
        its panels will not tile and the run refuses it (M2), rather than
        naming an angle nobody set.

        Mutant "no words for no angle" (the any-angle clause dropped) failed:
            E   AssertionError: 'M31 is a 3x2 mosaic shooting 5 of its 6
                panels (3-1 skipped) at 25% overlap, at no set angle, so its
                panels will not tile and it cannot run.' missing from: This
                flow arms at astronomical dusk (\\u221230 min). It then arms
                M31. M31 is a 3x2 mosaic shooting 5 of its 6 panels (3-1
                skipped) at 25% overlap. After 1 pass of its filters on a
                panel it moves on to the next (setting first), ...

        DELIBERATE PIN CHANGE (S4 orchestrator ruling 1, #339; re-pinned by
        the S4 integration, #398): the sentence now writes the grid as
        ``test_the_mosaic_sentence_quotes_the_block`` says. Re-observed on the
        S4 wording in scratchpad/s4-integrate-q7m2, the same mutant, and
        only this test failed:
            E       AssertionError: 'M31 is a mosaic of 2 columns by 3 rows
                shooting 5 of its 6 panels (panel 3-1 skipped, written
                row-column) at 25% overlap, at no set angle, so its panels
                will not tile and it cannot run.' missing from: This flow arms
                at astronomical dusk (\\u221230 min). It then arms M31. M31 is
                a mosaic of 2 columns by 3 rows shooting 5 of its 6 panels
                (panel 3-1 skipped, written row-column) at 25% overlap. After
                1 pass of its filters on a panel it moves on to the next
                (setting first), ...
        """
        b = brief(_mosaic(angle="Any angle"))
        part = ("M31 is a mosaic of 2 columns by 3 rows shooting 5 of its 6 "
                "panels (panel 3-1 skipped, written row-column) at 25% "
                "overlap, at no set angle, so its panels will not tile and it "
                "cannot run.")
        assert part in b, f"{part!r} missing from: {b}"
        assert "laid out at PA" not in b, b

    def test_control_a_single_target_brief_has_no_mosaic_sentence(self):
        """A 1x1 block is today's single target: no mosaic words, and a hop
        cost changes nothing about its brief."""
        g = _ex("example-m16")
        assert "mosaic" not in brief(g)
        assert brief(g, hop_cost_s=160.0) == brief(g)

    @pytest.mark.parametrize("kw", [dict(), dict(loop=False),
                                    dict(angle="Camera fixed at PA"),
                                    dict(skip="1-1, 3-2", passes=3)])
    def test_every_mosaic_brief_is_whole_sentences(self, kw):
        b = brief(_mosaic(**kw), hop_cost_s=95.0)
        assert b[0].isupper() and b.endswith("."), b
        assert "  " not in b and "None" not in b, b
        assert "—" not in b and "–" not in b, b


class TestTheBriefJoinsAListAsEnglish:
    """#407: a list of two takes no comma, a list of three or more keeps the
    Oxford comma. ``_join_and`` wrote ", and" before the last item whatever
    the length, so a mosaic with exactly two skipped panels briefed as
    "(panels 1-1, and 3-2 skipped, ...)". Its other caller, the cloud hold's
    checklist, has the same shape: a hold whose cooler, re-centre and
    refocus steps are all off has two steps.

    Every length is pinned, through ``_join_and`` itself and through the two
    sentences that call it, so a fix for two that broke three (or the
    reverse) cannot pass.

    Every mutant below was run in a private copy of ``server/`` (scratchpad
    ``S5-TONIGHT-mut``, from byte backups), never in the shared tree.

    RED under mutant "the old _join_and" (its two-item branch removed, so
    every list of two or more ends ", and" as before #407), observed: the
    two-panel test, the checklist test and the lengths test failed (3
    failed, 55 passed), and the three-panel test stayed green:

        E       AssertionError: This flow arms at astronomical dusk (−30
            min). It then arms M31. M31 is a mosaic of 2 columns by 3 rows
            shooting 4 of its 6 panels (panels 1-1, and 3-2 skipped, written
            row-column) at 25% overlap, laid out at PA 30° with the rotator
            turned to it at every panel. After 3 passes of its filters on a
            panel it moves on to the next (setting first), and comes back
            until every panel has its subs. The hop between panels has not
            been measured on this rig yet. Capture interleaves one sub per
            filter per pass - L 60 s × 45, R 60 s × 45, G 60 s × 45, B 60 s
            × 45, Ha 180 s × 45, OIII 180 s × 45, SII 180 s × 45 - so every
            channel grows evenly.
        E       assert '(panels 1-1 and 3-2 skipped, written row-column)' in
            'This flow arms at astronomical dusk (−30 min). It then arms
            M31. M31 is a mosaic of 2 columns by 3 rows shooting 4 of...'

        E       AssertionError: If cloud cover above 40% is detected,
            imaging pauses at the frame boundary and the calibration queue
            banks whatever the library lacks (darks → bias →
            flats-if-panel); once the sky holds clear for 4 min it restores
            the filter, and resumes at the same slot

        E         Differing items:
        E         {2: 'a, and b'} != {2: 'a and b'}

    (and ``test_flows_brief_grid_and_visit``'s re-pinned two-panel line).

    RED under mutant "no Oxford comma" (the list of three or more joined
    ``", ".join(parts[:-1]) + " and " + parts[-1]``), observed: the
    three-panel test, the checklist test (on its five-step control) and the
    lengths test failed (3 failed, 55 passed), and the two-panel test stayed
    green:

        E       AssertionError: This flow arms at astronomical dusk (−30
            min). It then arms M31. M31 is a mosaic of 2 columns by 3 rows
            shooting 3 of its 6 panels (panels 1-1, 2-2 and 3-2 skipped,
            written row-column) at 25% overlap, ...
        E       assert ', and resumes at the same slot.' in 'This flow arms
            at astronomical dusk (−30 min), opens the dome and binds it to
            the mount. ...'
        E         Differing items:
        E         {3: 'a, b and c'} != {3: 'a, b, and c'}
        E         {4: 'a, b, c and d'} != {4: 'a, b, c, and d'}
    """

    def test_two_skipped_panels_take_no_comma(self):
        """The #407 graph: 3 rows of 2 with 1-1 and 3-2 skipped."""
        b = brief(_mosaic(skip="1-1, 3-2", passes=3))
        assert "(panels 1-1 and 3-2 skipped, written row-column)" in b, b
        assert "1-1, and" not in b, b

    def test_three_skipped_panels_keep_the_oxford_comma(self):
        b = brief(_mosaic(skip="1-1, 2-2, 3-2"))
        assert "(panels 1-1, 2-2, and 3-2 skipped, written row-column)" \
            in b, b

    def test_a_two_step_resume_checklist_takes_no_comma(self):
        """The Campaign Example's hold with its cooler gate, re-centre and
        refocus all off leaves two steps; with them on, five (control: the
        Oxford comma stays)."""
        g = _ex("example-campaign")
        hold = next(n for n in g.nodes if n.type == "holdresume")
        full = brief(g)
        assert ("re-cools the sensor to setpoint and waits for it to "
                "stabilize, restores the filter, re-centers, ") in full, full
        assert ", and resumes at the same slot." in full, full
        hold.params.update(cooler="Skip check", recenter="Stay put",
                           refocus="Never")
        two = brief(g)
        sentence = next(s for s in two.split(". ")
                        if s.startswith("If cloud cover"))
        assert sentence.endswith(
            "it restores the filter and resumes at the same slot"), sentence

    def test_every_length_of_list(self):
        from astrodeck.flows.tonight import _join_and
        items = ["a", "b", "c", "d"]
        got = {n: _join_and(items[:n]) for n in range(5)}
        assert got == {0: "", 1: "a", 2: "a and b", 3: "a, b, and c",
                       4: "a, b, c, and d"}


# =========================================================== the campaign tab

class TestWhatTheCampaignTabWillSay:
    def test_no_pool_means_campaigns_need_one(self):
        c = _campaign(_ex("example-m16"), None)
        assert c["has_pool"] is False and c["is_campaign"] is False
        assert "campaigns need one" in c["note"]

    def test_a_pool_without_repeat_is_told_that_automatic_resume_is_on(self):
        """RE-PINNED FOR BACKLOG WP-85 (#195, wave 14 integration). This case
        asserted that the note says ``set DUSK WINDOW -> Repeat``, which was
        the 0.3.40 way to make a flow resume. The editor no longer offers
        Repeat (DUSK WINDOW's Automatic resume replaced it, default On), so
        the note says which of the two this flow is, and it must not mention
        Repeat at all. The Off variant, which names CONTINUE, is graded in
        test_w14_autoresume_tonight.py.

        RE-PINNED AGAIN FOR BACKLOG WP-118 (#195, wave 16 integration): the
        campaign now keys on Automatic resume and the flow's shape
        (``compile.campaign_block``), not on DUSK WINDOW's retired ``repeat``,
        so ``example-pool`` (a pool, Automatic resume at its default On) IS a
        campaign, and with no ledger its note is the campaign's own "The
        session log is unavailable ..." sentence. The "Automatic resume is on"
        sentence is now reached only by a pool with NO DUSK WINDOW, which
        carries no opinion and resumes as it always has; this case grades
        both."""
        c = _campaign(_ex("example-pool"), None)
        assert c["has_pool"] is True and c["is_campaign"] is True
        assert c["note"].startswith(
            "The session log is unavailable, so captured totals cannot be "
            "shown. "), c["note"]
        assert "Repeat" not in c["note"], c["note"]
        bare = _ex("example-pool")
        bare.nodes = [n for n in bare.nodes if n.type != "dusk"]
        c = _campaign(bare, None)
        assert c["has_pool"] is True and c["is_campaign"] is False
        assert c["note"] == (
            "Automatic resume is on (DUSK WINDOW): a subsequent night "
            "resumes this flow where the session log left off."), c["note"]
        assert "Repeat" not in c["note"], c["note"]

    def test_the_members_are_the_pools_own_names_and_quota(self):
        c = _campaign(_ex("example-campaign"), None)
        assert [m["name"] for m in c["members"]] == [
            "M33", "NGC 7331", "IC 1396", "M45"]
        assert all(m["quota"] == 45 for m in c["members"])

    def test_no_ledger_reports_None_and_never_zero(self):
        """"0 of 45 banked" and "nobody has looked" are different sentences, and
        only one of them should make an operator re-plan a month."""
        c = _campaign(_ex("example-campaign"), None)
        assert c["has_ledger"] is False
        assert all(m["banked"] is None and m["pct"] is None for m in c["members"])
        assert "captured totals cannot be shown" in c["note"]

    def test_a_ledger_that_raises_is_no_ledger_rather_than_a_crash(self):
        def boom():
            raise OSError("captures/ is not mounted")
        c = _campaign(_ex("example-campaign"), boom)
        assert c["has_ledger"] is False
        assert all(m["banked"] is None for m in c["members"])

    def test_a_cycle_is_complete_only_when_EVERY_slot_has_its_sub(self):
        """THE ONE THAT MATTERS. 45 L and no Ha is zero complete cycles of an
        LRGBSHO table; counting total frames would report "45/45 · DONE" and
        retire a target holding one channel out of seven."""
        lots_of_L = {"M33": {"L": 45}}
        c = _campaign(_ex("example-campaign"), lambda: lots_of_L)
        m33 = c["members"][0]
        assert m33["banked"] == 0, f"a one-channel target counted as {m33}"
        assert m33["done"] is False

    def test_the_minimum_slot_sets_the_count(self):
        bank = {"M33": {"L": 20, "R": 20, "G": 20, "B": 20,
                        "Ha": 12, "OIII": 20, "SII": 20}}
        c = _campaign(_ex("example-campaign"), lambda: bank)
        assert c["members"][0]["banked"] == 12

    def test_a_member_that_meets_its_quota_is_done(self):
        full = {f: 45 for f in ("L", "R", "G", "B", "Ha", "OIII", "SII")}
        c = _campaign(_ex("example-campaign"), lambda: {"M45": full})
        by_name = {m["name"]: m for m in c["members"]}
        assert by_name["M45"]["banked"] == 45
        assert by_name["M45"]["done"] is True and by_name["M45"]["pct"] == 100
        assert by_name["M33"]["banked"] == 0, "an unshot member borrowed progress"

    def test_subs_per_pass_above_one_divides_the_count(self):
        g = _ex("example-campaign")
        next(n for n in g.nodes if n.type == "cycle").params["perCycle"] = 3
        bank = {"M33": {f: 30 for f in ("L", "R", "G", "B", "Ha", "OIII", "SII")}}
        c = _campaign(g, lambda: bank)
        assert c["members"][0]["banked"] == 10, "30 subs at 3 per pass is 10 passes"

    def test_no_completion_DATE_is_ever_claimed(self):
        """The prototype prints "pool complete in ~6 clear nights". Nothing here
        forecasts clear nights, and an invented number would be the most
        quotable thing on the screen and the least true."""
        bank = {"M33": {f: 45 for f in ("L", "R", "G", "B", "Ha", "OIII", "SII")}}
        c = _campaign(_ex("example-campaign"), lambda: bank)
        note = c["note"]
        assert "clear nights" not in note or "not forecast" in note, note
        assert "~" not in note, f"a tilde-estimate crept in: {note}"
        assert "135 cycles left" in note, note      # 3 members x 45
        assert "not forecast" in note, note

    def test_the_remaining_work_is_stated_exactly(self):
        bank = {"M33": {f: 45 for f in ("L", "R", "G", "B", "Ha", "OIII", "SII")}}
        c = _campaign(_ex("example-campaign"), lambda: bank)
        # 3 members x 45 cycles x 1 sub x 7 slots
        assert "945 subs" in c["note"], c["note"]


class TestTheLedgerFold:
    def test_accepted_frames_are_summed_per_target_and_filter(self):
        reps = [
            _Rep([_TB("M33", [_FB("Ha", 5), _FB("L", 2)]),
                  _TB("M45", [_FB("L", 9)])]),
            _Rep([_TB("M33", [_FB("Ha", 3)])]),
        ]
        assert frames_by_target_from_reports(reps) == {
            "M33": {"Ha": 8, "L": 2}, "M45": {"L": 9}}

    def test_an_unnamed_target_is_skipped_rather_than_bucketed_under_blank(self):
        assert frames_by_target_from_reports([_Rep([_TB("", [_FB("L", 4)])])]) == {}

    def test_no_reports_is_an_empty_mapping(self):
        assert frames_by_target_from_reports([]) == {}
        assert frames_by_target_from_reports(None) == {}


class TestTheCampaignBlockIsNotDroppedInSilence:
    """`SequencePlan` has nowhere to put `campaign`, so the block is reported.

    Dropping it without saying so would be the defect the whole unmapped list
    exists to prevent. But so is the opposite, and this class used to require
    it: it asserted the note says "images ONE night" and "will not re-arm" at
    DANGER weight, and neither had been true since the multi-night session
    machinery landed. A test that demands a false sentence keeps the sentence
    alive through every review, which is how this one survived.

    What the note must do is say what actually happens - the run comes back and
    picks up - and name the part that really is approximate, the stop condition.
    `test_campaign_across_nights.py` runs the machinery that makes the first
    half true.
    """

    def test_a_campaign_flow_reports_what_it_WILL_do(self):
        from astrodeck.flows.compile import compile_plan
        from astrodeck.flows.to_plan import to_sequence_plan
        g = _ex("example-campaign")
        _, un = to_sequence_plan(compile_plan(g, "camp"), g)
        hits = [u for u in un if u["key"] == "campaign"]
        assert hits, [u["key"] for u in un]
        detail = hits[0]["detail"]
        assert "comes back the next night" in detail, detail
        assert "session log" in detail, detail
        assert "skipped rather than reshot" in detail, detail
        assert hits[0]["level"] == "warn", hits[0]

    @pytest.mark.parametrize("has_ledger", [True, False])
    def test_the_dawn_sentence_matches_the_PLAN_not_the_design(self, has_ledger):
        """The tab said "Dawn parks + closes; the cooler stays cold for day
        darks", three times, and two thirds of it was false.

        Parking is real. Closing is not - no dome action reaches the engine, and
        `to_plan` reports that at danger weight, so this line was contradicting
        a warning on the same screen. The cooler clause is the expensive one:
        `plan_extras` sets `warm_cooler_when_done=True`, so the TEC ramps up at
        dawn. Someone who read that sentence and walked away expecting a cold
        sensor for day darks gets a warm one, and a dark library indexed at a
        temperature the frames were never at.

        Bound to the PLAN rather than to a fixed string, so the sentence cannot
        be right today and quietly wrong after the next change to `plan_extras`.
        The design does ask for a cold cooler; delivering it means changing the
        run, not the copy."""
        from astrodeck.flows.compile import compile_plan
        from astrodeck.flows.to_plan import to_sequence_plan
        g = _ex("example-campaign")
        plan, _ = to_sequence_plan(compile_plan(g, "camp"), g)
        frames = {"M16": {"L": 40}} if has_ledger else {}
        note = _campaign(g, frames)["note"]
        if plan.warm_cooler_when_done:
            assert "cooler stays cold" not in note, note
            assert "warms the camera" in note, note
        if plan.park_when_done:
            assert "parks the mount" in note, note

    def test_a_single_night_flow_says_nothing_about_campaigns(self):
        from astrodeck.flows.compile import compile_plan
        from astrodeck.flows.to_plan import to_sequence_plan
        g = _ex("example-m16")
        _, un = to_sequence_plan(compile_plan(g, "m16"), g)
        assert not [u for u in un if u["key"] == "campaign"]
