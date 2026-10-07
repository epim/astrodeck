# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Doctor R4, M1 to M15 and L1 (spec 2026-09-23 flows mosaic, 1.7 and 1.8;
#189 U-09, #169, #184).

ONE POSITIVE AND ONE NEGATIVE CASE PER RULE, and each positive is shown red
under the mutant that drops its rule. Every mutant was run in a private copy
of server/ (scratchpad s3-D-doctor-r7k2, from a byte backup of doctor.py),
never in the shared tree, and the failure each one produced is quoted in the
test it turned red. The independent verification added the boundary and
one-axis cases (M5, M6, M10), the fixed camera with no rotator (M8), the
second loop wire into a 1x1 block (M4), the branched lane under M10 and M10's
"an 8 min visit", after its own mutants of those lines survived the cases
above; those ran in scratchpad s3-D-verify-q9m4 the same way, and all 73 of
the original mutants were re-run there against the final doctor.

The baseline mosaic (``_mosaic``) is a graph a careful person would draw: a
3-column, 2-row block framed at 2.0 x 1.33 deg with 25% overlap, locked at PA
30, counting accepted subs, with AUTOFOCUS, GUIDE and a FILTER CYCLE in its
lane, the loop wire from the cycle's 'pass done' into 'next panel', a DUSK
WINDOW that stops at dawn and a SESSION REPORT. Under a rig that agrees with
it the doctor says NOTHING about it (``TestTheBaselineIsClean``), so every
positive case below is one edit away from silence and names the rule that
edit trips.

Every number a positive case expects is recomputed here, through
``framing.convergence_share`` and ``framing.angle_tolerance_deg`` or from the
block's own params, never copied from the doctor's output.
"""
from __future__ import annotations

import pytest

from astrodeck.catalog import framing
from astrodeck.catalog.coords import parse_dec, parse_ra
from astrodeck.flows import NODE_DEFS, RigFacts, check
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode

M31_RA = "00h 42m 44s"
M31_DEC = "+41° 16′ 09″"

#: The camera that shoots every panel at one angle, the case M6 and M15 price
#: (#175: a rotating block's panels are each commanded their own angle, and
#: both rules are silent for it).
FIXED = "Camera fixed at PA"

#: A rig that agrees with the baseline: the field it was framed with, a
#: rotator, reject guards on, and a hop that costs an eighth of a visit.
CLEAN_RIG = RigFacts(fov_deg=(2.0, 1.33), has_rotator=True,
                     reject_guards_off=False, hop_cost_s=30.0, hop_samples=5)


def _n(nid: str, ntype: str, x: float = 0.0, y: float = 0.0,
       **params) -> FlowNode:
    return FlowNode(id=nid, type=ntype, x=float(x), y=float(y), params=params)


def _e(src: str, sp: str, dst: str, dp: str, eid: str | None = None
       ) -> FlowEdge:
    kw = {"from": src, "fromPort": sp, "to": dst, "toPort": dp}
    if eid is not None:
        kw["id"] = eid
    return FlowEdge(**kw)


def _block(nid: str = "t", x: float = 200, **over) -> FlowNode:
    params = {"name": "M31", "ra": M31_RA, "dec": M31_DEC, "rows": 2,
              "cols": 3, "overlap": 25, "fovX": 2.0, "fovY": 1.33,
              "angle": "Rotate to PA", "rotation": 30,
              "counts": "Accepted subs"}
    params.update(over)
    return _n(nid, "target", x, 0, **params)


def _mosaic(*, loop: bool = True, extra_nodes=(), extra_edges=(),
            dusk_stop: str = "Dawn", **block) -> FlowGraph:
    nodes = [_n("d", "dusk", 0, 0, stop=dusk_stop), _block(**block),
             _n("af", "autofocus", 400), _n("g", "guide", 600),
             _n("cy", "cycle", 800, plan="L 60, R 60, G 60, B 60"),
             _n("r", "report", 1000), *extra_nodes]
    edges = [_e("d", "window", "t", "arm"), _e("t", "target", "af", "run"),
             _e("af", "focused", "g", "run"), _e("g", "guiding", "cy", "run"),
             _e("cy", "complete", "r", "session"), *extra_edges]
    if loop:
        edges.append(_e("cy", "pass", "t", "next", "loop"))
    return FlowGraph(nodes=nodes, edges=edges)


def _hits(issues, marker: str):
    return [i for i in issues if marker in i.text]


def _one(issues, marker: str):
    got = _hits(issues, marker)
    assert len(got) == 1, (marker, [(i.level, i.text) for i in issues])
    return got[0]


def _spec(rows: int, cols: int, overlap: float, dec: float,
          rotation: float = 0.0) -> dict:
    """A block's layout as framing reads it, for recomputing a number. Angle 0
    by default, because that is where Appendix A.1 and A.2 measured."""
    return dict(ra_hours=parse_ra(M31_RA), dec_deg=dec, rows=rows, cols=cols,
                overlap=overlap, rotation_deg=rotation, fov_x_deg=2.0,
                fov_y_deg=1.33)


# Distinctive words of each rule, used to find it in a list of issues.
R4 = "CAPTURE with no TARGET upstream"
M1 = "frame this block"
M2 = "laid out at one camera angle"
M3 = "panels are shot one after another"
M4_ELSEWHERE = "this wire does nothing"
M4_ONE_PANEL = "one panel, nothing to rotate between"
M4_TWICE = "one is enough"
M5 = "this camera now images"
M6 = "convergence turns neighbouring panels"
M7 = "this plan counts accepted subs because"
M8 = "by hand to PA"
M9 = "Run will refuse it"
M10 = "each hop costs"
M11 = "panel names and rules find targets by name"
M12_BRANCH = "the panel lane branches"
M12_MID = "the loop wire starts at"
M13 = "belongs to no TARGET"
M14 = "fires once per panel"
M15 = "leaves no room for camera angle error"
L1 = "part of the TARGET block now"
EVERY_MARKER = (R4, M1, M2, M3, M4_ELSEWHERE, M4_ONE_PANEL, M4_TWICE, M5, M6,
                M7, M8, M9, M10, M11, M12_BRANCH, M12_MID, M13, M14, M15, L1)


class TestTheBaselineIsClean:
    """The negative control every positive case below stands on."""

    def test_the_baseline_mosaic_draws_no_issue_at_all(self):
        """Red under every over-firing mutant that reaches a clean mosaic.
        Observed, for example:

        * "M12 on every pass wire" (the tail's own loop wire read as
          mid-lane): ``AssertionError: [('danger', '▸ TARGET M31 - the loop
          wire starts at FILTER CYCLE, but  come after it in the panel lane.
          Start the loop wire at FILTER CYCLE to shoot them on every panel,
          or give  their own TARGET.')]``
        * "threshold 0" (M6 at any convergence): ``AssertionError: [('warn',
          '▸ TARGET M31 - at Dec 41 meridian convergence turns neighbouring
          panels against each other, which uses 2.7% of their 25% overlap.
          Widen the overlap or use fewer columns.')]``
        * "M3 ignores the loop wire", "every pass wire is elsewhere", "M8
          ignores the rotator", "hop threshold 0", "M14 without the wire" and
          "L1 on every graph" each put their own sentence here the same
          way."""
        issues = check(_mosaic(), rig=CLEAN_RIG)
        assert issues == [], [(i.level, i.text) for i in issues]

    def test_check_still_takes_the_graph_alone(self):
        """Every existing caller calls ``check(graph)``: `rig` is keyword-only
        and optional, like `standards` and `mount`. Mutant "unknown means no
        rotator" turned this red: ``assert [Issue(text='...level='warn')] ==
        []``."""
        assert check(_mosaic()) == []


class TestR4:
    """Rule 4 became R4 (spec 1.7): satisfied by a TARGET, a POOL or a legacy
    SLEW upstream along wires. With a multi-panel block M13 replaces it."""

    TEXT = ("▸ CAPTURE with no TARGET upstream - the loop shoots wherever the "
            "mount happens to point")

    def test_a_capture_with_nothing_upstream_is_warned_in_the_new_words(self):
        """Mutant "drop R4" turned this red: ``AssertionError: ('CAPTURE with
        no TARGET upstream', [('warn', "▸ CAPTURE LOOP - 'run' input
        unwired"), ('note', '▸ no session report sink - the night leaves no
        ledger')])``. Mutant "R4 wording unchanged" (rule 4's old words kept)
        turned it red with ``[..., ('warn', '▸ CAPTURE with no SLE... loop
        shoots wherever the mount happens to point'), ...]``."""
        hit = _one(check(FlowGraph(nodes=[_n("c", "capture", exposure=30)])),
                   R4)
        assert (hit.level, hit.text) == ("warn", self.TEXT)

    def test_a_dusk_window_does_not_point_the_mount(self):
        """Mutant "drop R4" turned this red: ``AssertionError: ('CAPTURE
        with no TARGET upstream', [('note', '▸ no session report sink - the
        night leaves no ledger')])``."""
        g = FlowGraph(nodes=[_n("d", "dusk"), _n("c", "capture", exposure=30)],
                      edges=[_e("d", "window", "c", "run")])
        assert _one(check(g), R4).level == "warn"

    @pytest.mark.parametrize("block", ["target", "pool"])
    def test_a_block_upstream_satisfies_it_with_no_slew(self, block):
        """Mutant "SLEW only" (R4 satisfied by a legacy SLEW alone, rule 4's
        old reading) turned this red for both blocks: ``AssertionError:
        assert not [Issue(text='▸ CAPTURE with no TARGET upstream - the loop
        shoots wherever the mount happens to point', level='warn')]``."""
        # Both blocks' flow output is `target` ("each panel", "best target").
        g = FlowGraph(nodes=[_n("b", block), _n("c", "capture", exposure=30)],
                      edges=[_e("b", "target", "c", "run")])
        assert not _hits(check(g), R4)

    def test_a_legacy_slew_upstream_still_satisfies_it(self):
        """Mutant "SLEW no longer counts" turned this red: ``assert not
        [Issue(text='▸ CAPTURE with no TARGET upstream - the loop shoots
        wherever the mount happens to point', level='warn')]``."""
        g = FlowGraph(nodes=[_n("s", "slew"), _n("c", "capture", exposure=30)],
                      edges=[_e("s", "centered", "c", "run")])
        assert not _hits(check(g), R4)

    def test_with_a_multi_panel_block_m13_replaces_it(self):
        """A stage with no owner in a mosaic graph gets M13, never both.
        Mutant "R4 in mosaic graphs too" turned this red: ``assert not
        [Issue(text='▸ CAPTURE with no TARGET upstream - the loop shoots
        wherever the mount happens to point', level='warn')]``."""
        g = _mosaic(extra_nodes=[_n("c", "capture", 900, 300, exposure=30)])
        issues = check(g)
        assert not _hits(issues, R4)
        assert _one(issues, M13).level == "danger"


class TestM1Framed:
    def test_a_mosaic_with_no_field_is_a_danger(self):
        """Mutant "drop M1" turned this red: ``AssertionError: ('frame this
        block', [])``."""
        hit = _one(check(_mosaic(fovX=0)), M1)
        assert (hit.level, hit.text) == (
            "danger", "▸ TARGET M31 - frame this block: panels are tiled from "
            "the camera's field, and this block has not recorded one.")

    def test_either_axis_is_enough(self):
        """Mutant "fovX only" turned this red: ``AssertionError: ('frame this
        block', [])`` for fovY = 0."""
        assert _one(check(_mosaic(fovY=0)), M1).level == "danger"

    def test_a_single_target_needs_no_field(self):
        """Mutant "M1 on any TARGET" turned this red: ``assert not
        [Issue(text="▸ TARGET M31 - frame this block: panels are tiled from
        the camera's field, and this block has not recorded one.",
        level='danger')]``."""
        assert not _hits(check(_mosaic(rows=1, cols=1, fovX=0)), M1)
        assert not _hits(check(_mosaic()), M1)


class TestM2Angle:
    TEXT = ("▸ TARGET M31 - a mosaic is laid out at one camera angle, and with "
            "no angle the panels will not tile. Lock an angle, or use the angle "
            "the camera measured.")

    def test_any_angle_on_a_mosaic_is_a_danger(self):
        """Mutant "drop M2" turned this red: ``AssertionError: ('laid out at
        one camera angle', [])``."""
        hit = _one(check(_mosaic(angle="Any angle", rotation=-1)), M2)
        assert (hit.level, hit.text) == ("danger", self.TEXT)

    def test_rotate_to_a_negative_pa_is_no_angle_either(self):
        """Spec 3.1: a negative rotation means none, so "Rotate to PA" with
        rotation -1 rotates to nothing. Mutant "drop M2" turned this red:
        ``AssertionError: ('laid out at one camera angle', [])``."""
        assert _one(check(_mosaic(rotation=-1)), M2).level == "danger"

    def test_pa_0_is_a_real_angle(self):
        """North up is a real position angle (#150). Mutant "0 is no angle"
        (``rot > 0``) turned this red: ``AssertionError: assert not
        [Issue(text='▸ TARGET M31 - a mosaic is laid out at one camera angle,
        and with no angle the panels will not tile. Lock an angle, or use the
        angle the camera measured.', level='danger')]``."""
        assert not _hits(check(_mosaic(angle="Camera fixed at PA",
                                       rotation=0)), M2)

    def test_a_single_target_may_take_any_angle(self):
        """Mutant "M2 on any TARGET" turned this red: ``assert not
        [Issue(text='▸ TARGET M31 - a mosaic is laid out at one camera
        angle, and with no angle the panels will not tile. Lock an angle, or
        use the angle the camera measured.', level='danger')]``."""
        assert not _hits(check(_mosaic(rows=1, cols=1, angle="Any angle",
                                       rotation=-1)), M2)


class TestM3Loop:
    def test_no_loop_wire_names_the_tail_to_start_it_at(self):
        """Mutant "drop M3" turned this red: ``AssertionError: ('panels are
        shot one after another', [])``."""
        hit = _one(check(_mosaic(loop=False)), M3)
        assert (hit.level, hit.text) == (
            "warn", "▸ TARGET M31 - panels are shot one after another: a night "
            "cut short leaves the last panels empty. Wire FILTER CYCLE 'pass "
            "done' to TARGET 'next panel' to rotate panels every pass.")

    def test_a_capture_tail_is_the_one_named(self):
        """Mutant "drop M3" turned this red: ``AssertionError: ('panels
        are shot one after another', [])``."""
        g = _mosaic(loop=False,
                    extra_nodes=[_n("ha", "capture", 900, exposure=300,
                                    filter="Ha")],
                    extra_edges=[_e("cy", "complete", "ha", "run")])
        assert "Wire CAPTURE LOOP Ha 'pass done'" in _one(check(g), M3).text

    def test_the_loop_wire_silences_it(self):
        """Mutant "M3 ignores the loop wire" turned this red: ``assert not
        [Issue(text="▸ TARGET M31 - panels are shot one after another: a
        night cut short leaves the last panels empty. Wire FILTER CYCLE 'pass
        done' to TARGET 'next panel' to rotate panels every pass.",
        level='warn')]``."""
        assert not _hits(check(_mosaic()), M3)

    def test_a_lane_with_no_stage_owns_nothing_to_loop(self):
        """A control with no mutant of its own: no named mutant makes M3
        fire for a lane of AUTOFOCUS alone, and it stays here so that one
        that did would show."""
        g = FlowGraph(nodes=[_block(), _n("af", "autofocus", 400)],
                      edges=[_e("t", "target", "af", "run")])
        assert not _hits(check(g), M3)

    def test_a_mid_lane_loop_wire_is_m12s_to_name(self):
        """Mutant "M3 beside M12" (M3 ignores a mid-lane wire) turned this
        red: ``assert not [Issue(text="▸ TARGET M31 - panels are shot one
        after another: a night cut short leaves the last panels empty. Wire
        CAPTURE LOOP Ha 'pass done' to TARGET 'next panel' to rotate panels
        every pass.", level='warn')]``."""
        g = _mosaic(extra_nodes=[_n("ha", "capture", 900, exposure=300,
                                    filter="Ha")],
                    extra_edges=[_e("cy", "complete", "ha", "run")])
        issues = check(g)
        assert _one(issues, M12_MID).level == "danger"
        assert not _hits(issues, M3)


class TestM4LoopWireThatDoesNothing:
    def _two_blocks(self) -> FlowGraph:
        """M31 (3x2) owns FILTER CYCLE; M33 (1x1) owns CAPTURE LOOP Ha, and
        M31's cycle is wired into M33's 'next panel'."""
        return FlowGraph(
            nodes=[_block(), _n("cy", "cycle", 400),
                   _block("t2", 600, name="M33", rows=1, cols=1),
                   _n("ha", "capture", 800, filter="Ha", exposure=300)],
            edges=[_e("t", "target", "cy", "run"),
                   _e("cy", "complete", "t2", "arm"),
                   _e("t2", "target", "ha", "run"),
                   _e("cy", "pass", "t", "next"),
                   _e("cy", "pass", "t2", "next", "stray")])

    def test_a_wire_from_another_blocks_lane_is_warned(self):
        """Mutant "drop M4's warning" turned this red: ``AssertionError: ('this
        wire does nothing', [('warn', "▸ TARGET - 'arm' input unwired"),
        ('warn', '▸ 180s subs with no GUIDE upstream -...he loop - focus
        drift goes uncorrected all night'), ('note', '▸ no session report
        sink - the night leaves no ledger')])``. Mutant "set walk (M4)" turned
        it red the same way."""
        hit = _one(check(self._two_blocks()), M4_ELSEWHERE)
        assert (hit.level, hit.text) == (
            "warn", "▸ TARGET M33 'next panel' - this wire does nothing: "
            "FILTER CYCLE is not in this TARGET's panel lane.")

    def test_a_stage_past_a_dome_is_in_no_lane_by_owner_of(self):
        """The owner is found by ``owner_of``, a chain walk. Mutant "set walk"
        (the wire's source counts as the block's when the block is anywhere
        upstream, ``_flow_upstream_types``) turned this red: ``AssertionError:
        ('this wire does nothing', [('warn', "▸ TARGET - 'arm' input
        unwired"), ('warn', '▸ 180s subs with no GUIDE upstream -...t'),
        ('danger', '▸ DOME with no SAFETY MONITOR - nothing closes the
        shutter on rain. Add one; it fails closed.'), ...])``."""
        g = FlowGraph(
            nodes=[_block(), _n("cy", "cycle", 400), _n("dm", "dome", 600),
                   _n("ha", "capture", 800, filter="Ha", exposure=300)],
            edges=[_e("t", "target", "cy", "run"),
                   _e("cy", "complete", "dm", "run"),
                   _e("dm", "open", "ha", "run"),
                   _e("cy", "pass", "t", "next"),
                   _e("ha", "pass", "t", "next", "past")])
        assert "CAPTURE LOOP Ha is not in" in _one(check(g), M4_ELSEWHERE).text

    def test_a_loop_wire_on_a_one_panel_block_is_a_note(self):
        """No mosaic anywhere in this graph: the note needs only the wire.
        Mutant "drop M4's note" turned this red: ``AssertionError: ('one
        panel, nothing to rotate between', [])``."""
        g = _mosaic(rows=1, cols=1)
        hit = _one(check(g), M4_ONE_PANEL)
        assert (hit.level, hit.text) == (
            "note", "▸ TARGET M31 - one panel, nothing to rotate between: the "
            "'pass done' wire into 'next panel' changes nothing.")

    def test_two_loop_wires_into_one_panel_are_one_note(self):
        """The note is about the block, not each wire: two of them into one
        1x1 block still say it once. Mutant "one-panel note per wire" turned
        this red: ``AssertionError: ('one panel, nothing to rotate between',
        [('note', "▸ TARGET M31 - one panel, nothing to rotate between:
        the 'pass don...ote', "▸ TARGET M31 - one panel, nothing to
        rotate between: the 'pass done' wire into 'next panel' changes
        nothing.")])``."""
        g = _mosaic(rows=1, cols=1,
                    extra_edges=[_e("cy", "pass", "t", "next", "loop2")])
        assert _one(check(g), M4_ONE_PANEL).level == "note"

    def test_two_loop_wires_are_one_too_many(self):
        """Spec 1.4 item 2. Mutant "drop M4's one-is-enough" turned this red:
        ``AssertionError: ('one is enough', [])``."""
        g = _mosaic(extra_edges=[_e("cy", "pass", "t", "next", "loop2")])
        hit = _one(check(g), M4_TWICE)
        assert (hit.level, hit.text) == (
            "note", "▸ TARGET M31 - 2 loop wires reach 'next panel'; one is "
            "enough, and the others change nothing.")

    def test_the_blocks_own_loop_wire_draws_none_of_them(self):
        """Mutant "every pass wire is elsewhere" turned this red: ``assert
        not [Issue(text="▸ TARGET M31 'next panel' - this wire does nothing:
        FILTER CYCLE is not in this TARGET's panel lane.", level='warn')]``."""
        issues = check(_mosaic())
        for marker in (M4_ELSEWHERE, M4_ONE_PANEL, M4_TWICE):
            assert not _hits(issues, marker)


class TestM5LiveField:
    """Rig-dependent: runs only when the rig knows its live field."""

    def test_a_smaller_camera_leaves_gaps(self):
        """The spec's own example. Mutant "drop M5" turned this red:
        ``AssertionError: ('this camera now images', [])``."""
        rig = RigFacts(fov_deg=(1.40, 0.93))
        hit = _one(check(_mosaic(), rig=rig), M5)
        assert (hit.level, hit.text) == (
            "warn", "▸ TARGET M31 - framed for 2.00 x 1.33 deg; this camera "
            "now images 1.40 x 0.93 deg, so the panels would leave gaps. "
            "Re-frame.")

    def test_a_smaller_field_above_the_step_only_thins_the_overlap(self):
        """1.90 deg is 5% under the snapshot but above the 1.5 deg step
        (2.0 x (1 - 25%)), so no gap opens. Mutant "gaps against the field,
        not the step" turned this red: ``AssertionError: assert 'overlap less
        than framed' in '▸ TARGET M31 - framed for 2.00 x 1.33 deg; this
        camera now images 1.90 x 1.26 deg, so the panels would leave gaps.
        Re-frame.'``."""
        hit = _one(check(_mosaic(), rig=RigFacts(fov_deg=(1.90, 1.26))), M5)
        assert "overlap less than framed" in hit.text

    def test_a_larger_field_overlaps_more(self):
        """Mutant "no 'more' branch in M5" turned this red:
        ``AssertionError: assert 'overlap more than framed' in '▸ TARGET M31
        - framed for 2.00 x 1.33 deg; this camera now images 2.10 x 1.40 deg,
        so the panels would overlap less than framed. Re-frame.'``."""
        hit = _one(check(_mosaic(), rig=RigFacts(fov_deg=(2.10, 1.40))), M5)
        assert "overlap more than framed" in hit.text

    def test_a_field_within_two_percent_is_the_same_camera(self):
        """1.5% on both axes. Mutant "any difference" (threshold 0) turned this
        red: ``AssertionError: assert not [Issue(text='▸ TARGET M31 - framed
        for 2.00 x 1.33 deg; this camera now images 2.03 x 1.35 deg, so the
        panels would overlap more than framed. Re-frame.', level='warn')]``."""
        assert not _hits(check(_mosaic(), rig=RigFacts(fov_deg=(2.03, 1.35))),
                         M5)

    def test_one_axis_just_past_two_percent_is_enough(self):
        """2.5% wider, the same height. "More than 2%" is on either axis, and
        the line is at 2%, not anywhere between the 1.5% and 5% the cases
        above leave it. Mutants "drift 4%" and "both axes must drift" each
        turned this red: ``AssertionError: ('this camera now images', [])``."""
        hit = _one(check(_mosaic(), rig=RigFacts(fov_deg=(2.05, 1.33))), M5)
        assert "overlap more than framed" in hit.text

    def test_a_gap_on_one_axis_is_a_gap(self):
        """1.40 deg wide is under the 1.5 deg step and the height is
        unchanged: the rows open gaps whatever the columns do. Mutant "gaps
        need both axes" turned this red: ``AssertionError: assert False``,
        on '▸ TARGET M31 - framed for 2.00 x 1.33 deg; this camera now
        images 1.40 x 1.33 deg, so the panels would overlap less than framed.
        Re-frame.'."""
        hit = _one(check(_mosaic(), rig=RigFacts(fov_deg=(1.40, 1.33))), M5)
        assert hit.text.endswith("so the panels would leave gaps. Re-frame.")


class TestM6Convergence:
    """Spec 1.8 and Appendix A.1, recomputed through framing."""

    def test_the_4x1_at_10pc_at_dec_75_is_warned(self):
        """Spec 1.8's worked case: 4 columns by 1 row, the block this builds
        (``rows=1, cols=4``). It was named rows by columns until S4
        orchestrator ruling 1 (#339) wrote a grid's size columns by rows, as
        the Example and the framing card do, and in words where an operator
        reads it.

        Mutant "threshold 50%" turned this red: ``AssertionError:
        ('convergence turns neighbouring panels', [])``. So did "drop M6"."""
        share = framing.convergence_share(_spec(1, 4, 0.10, 75.0))
        assert share == pytest.approx(0.389, abs=0.0005)
        hit = _one(check(_mosaic(rows=1, cols=4, overlap=10,
                                 dec="+75 00 00", rotation=0, angle=FIXED)), M6)
        assert (hit.level, hit.text) == (
            "warn", f"▸ TARGET M31 - at Dec 75 meridian convergence turns "
            f"neighbouring panels against each other, which uses "
            f"{share * 100:.1f}% of their 10% overlap. Widen the overlap or "
            f"use fewer columns.")
        assert "38.9%" in hit.text

    def test_the_3x3_at_25pc_at_dec_41_is_not(self):
        """3.1% of the overlap. Mutant "threshold 0" turned this red:
        ``AssertionError: assert not [Issue(text='▸ TARGET M31 - at Dec 41
        meridian convergence turns neighbouring panels against each other,
        which uses 3.1% of their 25% overlap. Widen the overlap or use fewer
        columns.', level='warn')]``."""
        share = framing.convergence_share(_spec(3, 3, 0.25, 41.0))
        assert share == pytest.approx(0.031, abs=0.0005)
        assert not _hits(check(_mosaic(rows=3, cols=3, dec="+41 00 00",
                                       rotation=0, angle=FIXED)), M6)

    def test_just_over_a_quarter_is_warned(self):
        """The same 4x1 at 10% at Dec 68 uses 25.8%, just over spec 1.8's
        25%. The two cases above are 38.9% and 3.1%, so on their own they
        hold the line anywhere between. Mutant "threshold 35%" turned this
        red: ``AssertionError: ('convergence turns neighbouring panels',
        [])``."""
        share = framing.convergence_share(_spec(1, 4, 0.10, 68.0))
        assert 0.25 < share < 0.26
        assert _one(check(_mosaic(rows=1, cols=4, overlap=10,
                                  dec="+68 00 00", rotation=0, angle=FIXED)),
                    M6).level == "warn"

    def test_just_under_a_quarter_is_not(self):
        """Dec 66: 23.4%, just under. Mutant "threshold 20%" turned this
        red: ``AssertionError: assert not [Issue(text='▸ TARGET M31 - at
        Dec 66 meridian convergence turns neighbouring panels against each
        other, which uses 23.4% of their 10% overlap. Widen the overlap or
        use fewer columns.', level='warn')]``."""
        share = framing.convergence_share(_spec(1, 4, 0.10, 66.0))
        assert 0.23 < share < 0.25
        assert not _hits(check(_mosaic(rows=1, cols=4, overlap=10,
                                       dec="+66 00 00", rotation=0, angle=FIXED)), M6)

    def test_past_the_budget_m15_speaks_instead(self):
        """A 4x1 at 10% at Dec 80 uses 58.8%: M15's danger, not M6 beside it.
        Mutant "M6 beside M15" turned this red: ``AssertionError: assert not
        [Issue(text='▸ TARGET M31 - at Dec 80 meridian convergence turns
        neighbouring panels against each other, which uses 58.8% of their 10%
        overlap. Widen the overlap or use fewer columns.', level='warn')]``."""
        issues = check(_mosaic(rows=1, cols=4, overlap=10, dec="+80 00 00",
                               rotation=0, angle=FIXED))
        assert _one(issues, M15).level == "danger"
        assert not _hits(issues, M6)


class TestM7CountsDisagree:
    def _two(self, a: str, b: str, second: str = "target") -> FlowGraph:
        nodes = [_block(rows=1, cols=1, counts=a)]
        if second == "target":
            nodes.append(_block("t2", 400, name="M33", rows=1, cols=1,
                                counts=b))
        else:
            nodes.append(_n("p", "pool", 400, counts=b))
        return FlowGraph(nodes=nodes)

    def test_two_blocks_that_disagree_are_warned(self):
        """No mosaic needed: count_mode is one setting for the whole plan.
        Mutant "drop M7" turned this red: ``AssertionError: ('this plan counts
        accepted subs because', [('warn', "▸ TARGET - 'arm' input unwired"),
        ('warn', "▸ TARGET - 'arm' input unwired"), ('note', '▸ no session
        report sink - the night leaves no ledger')])``."""
        hit = _one(check(self._two("Accepted subs", "Every sub taken")), M7)
        assert (hit.level, hit.text) == (
            "warn", "▸ this plan counts accepted subs because TARGET M31 asks "
            "for it; TARGET M33's 'every sub taken' cannot be honoured in the "
            "same run.")

    def test_a_pool_is_a_block_too(self):
        """Mutant "drop M7" turned this red too: ``AssertionError: ('this
        plan counts accepted subs because', [('warn', "▸ TARGET - 'arm'
        input unwired"), ('warn', "▸ TARGET POOL - 'arm' input unwired"),
        ('note', '▸ no session report sink - the night leaves no
        ledger')])``."""
        hit = _one(check(self._two("Accepted subs", "Every sub taken",
                                   "pool")), M7)
        assert "TARGET POOL's 'every sub taken'" in hit.text

    @pytest.mark.parametrize("a,b", [("Accepted subs", "Accepted subs"),
                                     ("Every sub taken", "Every sub taken")])
    def test_blocks_that_agree_are_not(self, a, b):
        """Mutant "M7 whenever two blocks" turned both red, e.g. ``assert
        not [Issue(text="▸ this plan counts accepted subs because TARGET
        M31 and TARGET M33 ask for it;  'every sub taken' cannot be honoured
        in the same run.", level='warn')]``."""
        assert not _hits(check(self._two(a, b)), M7)


class TestM8Rotator:
    """Rig-dependent: runs only when the rig says whether it has a rotator."""

    def _tolerance(self) -> str:
        spec = _spec(2, 3, 0.25, parse_dec(M31_DEC), rotation=30.0)
        return f"{framing.angle_tolerance_deg(spec):.1f}"

    def test_rotate_to_pa_with_no_rotator_is_warned(self):
        """Mutant "drop M8" turned this red: ``AssertionError: ('by hand to
        PA', [])``."""
        hit = _one(check(_mosaic(), rig=RigFacts(has_rotator=False)), M8)
        assert (hit.level, hit.text) == (
            "warn", f"▸ TARGET M31 - no rotator: turn the camera by hand to PA "
            f"30.0 before the first panel. The run measures the angle at every "
            f"panel and holds the mosaic if it is off by more than "
            f"{self._tolerance()} deg.")
        assert self._tolerance() == "6.0"

    @pytest.mark.parametrize("rotator", [True, False],
                             ids=["rotator", "no-rotator"])
    def test_a_fixed_camera_is_a_note_whatever_the_rotator(self, rotator):
        """Mutant "a fixed camera warns" turned this red:
        ``AssertionError: assert 'warn' == 'note'``. Mutant "level by
        rotator, not by angle" (a fixed camera warns when there is no
        rotator) turned the no-rotator case red the same way; the rotator
        case alone left it green."""
        hit = _one(check(_mosaic(angle="Camera fixed at PA"),
                         rig=RigFacts(has_rotator=rotator)), M8)
        assert hit.level == "note"
        assert hit.text.startswith("▸ TARGET M31 - camera fixed: turn the "
                                   "camera by hand to PA 30.0")

    def test_rotate_to_pa_with_a_rotator_is_fine(self):
        """Mutant "M8 ignores the rotator" turned this red:
        ``AssertionError: assert not [Issue(text='▸ TARGET M31 - no rotator:
        turn the camera by hand to PA 30.0 before the first panel. The run
        measures the angle at every panel and holds the mosaic if it is off by
        more than 6.0 deg.', level='warn')]``."""
        assert not _hits(check(_mosaic(), rig=RigFacts(has_rotator=True)), M8)

    @pytest.mark.parametrize("rig", [None, RigFacts()], ids=["none", "empty"])
    def test_an_unknown_rotator_says_nothing(self, rig):
        """Mutant "unknown means no rotator" turned this red for both:
        ``AssertionError: assert not [Issue(text='▸ TARGET M31 - no rotator:
        turn the camera by hand to PA 30.0 before the first panel. The run
        measures the angle at every panel and holds the mosaic if it is off by
        more than 6.0 deg.', level='warn')]``."""
        assert not _hits(check(_mosaic(), rig=rig), M8)
        assert not _hits(check(_mosaic(angle="Camera fixed at PA"), rig=rig),
                         M8)


class TestM9Unbounded:
    """Rig-dependent: previews the ``quota_unbounded`` refusal."""

    OFF = RigFacts(reject_guards_off=True)

    @pytest.mark.parametrize("stop", ["None", "Clock time"])
    def test_accepted_with_no_stop_and_no_guards_is_warned(self, stop):
        """"Clock time" compiles to no stop boundary today, and Run refuses it
        as such. Mutant "drop M9" turned both red: ``AssertionError: ('Run
        will refuse it', [])``. Mutant "any DUSK is a boundary" turned both red
        the same way."""
        hit = _one(check(_mosaic(dusk_stop=stop), rig=self.OFF), M9)
        assert (hit.level, hit.text) == (
            "warn", "▸ this plan counts accepted subs with no stop time and "
            "both reject guards off, so Run will refuse it: a sub the grader "
            "never accepts would be retried without end. Stop the night at "
            "Dawn (DUSK WINDOW), or set Settings > Standards > 'Give up on a "
            "step after'.")

    def test_a_dawn_stop_bounds_it(self):
        """Mutant "M9 ignores the stop" turned this red: ``assert not
        [Issue(text="▸ this plan counts accepted subs with no stop time
        and both reject guards off, so Run will refuse it: a s...ut end. Stop
        the night at Dawn (DUSK WINDOW), or set Settings > Standards > 'Give
        up on a step after'.", level='warn')]``."""
        assert not _hits(check(_mosaic(), rig=self.OFF), M9)

    def test_a_filled_clock_time_stop_bounds_it(self):
        """Backlog WP-09 (#191, 2026-09-30): "Clock time" with a time ON the
        card is a real boundary, unlike the blank card
        ``test_accepted_with_no_stop_and_no_guards_is_warned`` covers -
        M9 must not warn about a stop that is actually going to arrive.

        RED under mutant "M9 always reads Clock time as unbounded" (the
        blank check dropped, so a filled ``stopClock`` still warns),
        observed:

            AssertionError: ('this plan counts accepted subs', [Issue(...
        """
        g = FlowGraph(nodes=[_n("d", "dusk", 0, 0, stop="Clock time",
                               stopClock="05:30"), _block(),
                             _n("af", "autofocus", 400), _n("g", "guide", 600),
                             _n("cy", "cycle", 800,
                                plan="L 60, R 60, G 60, B 60"),
                             _n("r", "report", 1000)],
                     edges=[_e("d", "window", "t", "arm"),
                            _e("t", "target", "af", "run"),
                            _e("af", "focused", "g", "run"),
                            _e("g", "guiding", "cy", "run"),
                            _e("cy", "complete", "r", "session"),
                            _e("cy", "pass", "t", "next", "loop")])
        assert not _hits(check(g, rig=self.OFF), M9)

    def test_a_guard_bounds_it(self):
        """Mutant "M9 ignores the guards" turned this red: ``assert not
        [Issue(text="▸ this plan counts accepted subs with no stop time
        and both reject guards off, so Run will refuse it: a s...ut end. Stop
        the night at Dawn (DUSK WINDOW), or set Settings > Standards > 'Give
        up on a step after'.", level='warn')]``."""
        assert not _hits(check(_mosaic(dusk_stop="None"),
                               rig=RigFacts(reject_guards_off=False)), M9)

    def test_counting_every_sub_taken_is_never_unbounded(self):
        """Mutant "M9 ignores the counts" turned this red: ``assert not
        [Issue(text="▸ this plan counts accepted subs with no stop time
        and both reject guards off, so Run will refuse it: a s...ut end. Stop
        the night at Dawn (DUSK WINDOW), or set Settings > Standards > 'Give
        up on a step after'.", level='warn')]``."""
        assert not _hits(check(_mosaic(dusk_stop="None",
                                       counts="Every sub taken"),
                               rig=self.OFF), M9)

    @pytest.mark.parametrize("rig", [None, RigFacts()], ids=["none", "empty"])
    def test_unknown_guards_say_nothing(self, rig):
        """The control for "guards unknown": RigFacts' None is a state,
        never a reading. Mutant "M9 ignores the guards" (any known value, off
        or on) leaves this green by construction, since None is not known;
        the case stays so that a rule reading None as "off" would show."""
        assert not _hits(check(_mosaic(dusk_stop="None"), rig=rig), M9)


class TestM10HopCost:
    """Rig-dependent: only a MEASURED hop cost is weighed."""

    def test_a_hop_over_a_quarter_of_the_visit_is_noted(self):
        """One pass of L R G B at 60 s is a 4 min visit; a 2 min hop is half
        of it. Mutant "drop M10" turned this red: ``AssertionError: ('each hop
        costs', [])``."""
        rig = RigFacts(hop_cost_s=120.0, hop_samples=5)
        hit = _one(check(_mosaic(), rig=rig), M10)
        assert (hit.level, hit.text) == (
            "note", "▸ TARGET M31 - each hop costs 2 m 00 s (measured over 5 "
            "hops) against a 4 min visit; 2 passes per visit would cut hops "
            "by half.")

    def test_a_minimum_visit_makes_the_visit_longer(self):
        """minVisit 10 runs three 4 min passes: 12 min, so the 2 min hop is a
        sixth. Mutant "minVisit ignored" turned this red: ``AssertionError:
        assert not [Issue(text='▸ TARGET M31 - each hop costs 2 m 00 s
        (measured over 5 hops) against a 4 min visit; 2 passes per visit would
        cut hops by half.', level='note')]``."""
        rig = RigFacts(hop_cost_s=120.0, hop_samples=5)
        assert not _hits(check(_mosaic(minVisit=10), rig=rig), M10)

    def test_a_cheap_hop_is_not(self):
        """Mutant "hop threshold 0" turned this red: ``AssertionError:
        assert not [Issue(text='▸ TARGET M31 - each hop costs 30 s (measured
        over 5 hops) against a 4 min visit; 2 passes per visit would cut hops
        by half.', level='note')]``."""
        assert not _hits(check(_mosaic(), rig=CLEAN_RIG), M10)

    def test_no_measured_hop_says_nothing(self):
        """The control for an unmeasured hop: RigFacts refuses a cost with
        no samples, so there is nothing for M10 to weigh."""
        assert not _hits(check(_mosaic(), rig=RigFacts(has_rotator=True)), M10)

    @pytest.mark.parametrize("hop,noted", [(66.0, True), (54.0, False)],
                             ids=["27.5pc", "22.5pc"])
    def test_the_line_is_a_quarter_of_the_visit(self, hop, noted):
        """Against the 4 min visit, 66 s is 27.5% and 54 s is 22.5%. The cases
        above are 50%, 16.7% and 12.5%, which on their own hold the line
        anywhere from 17% to 37%. Mutant "hop share 30%" turned the 27.5%
        case red: ``AssertionError: assert False is True``; "hop share 20%"
        turned the 22.5% case red: ``AssertionError: assert True is False``,
        on '▸ TARGET M31 - each hop costs 54 s (measured over 5 hops)
        against a 4 min visit; 2 passes per visit would cut hops by
        half.'."""
        rig = RigFacts(hop_cost_s=hop, hop_samples=5)
        assert bool(_hits(check(_mosaic(), rig=rig), M10)) is noted

    def test_a_branched_lane_has_no_pass_to_weigh(self):
        """M12 says the lane branches, so there is no one pass to measure a
        visit by, and M10 keeps out of it. Mutant "M10 weighs a branched
        lane" turned this red: ``assert not [Issue(text='▸ TARGET M31 -
        each hop costs 10 m 00 s (measured over 5 hops) against an 18 min
        visit; 2 passes per visit would cut hops by half.',
        level='note')]``."""
        rig = RigFacts(hop_cost_s=600.0, hop_samples=5)
        issues = check(TestM12Lane._branched(), rig=rig)
        assert _one(issues, M12_BRANCH).level == "danger"
        assert not _hits(issues, M10)

    @pytest.mark.parametrize("stage,key,value", [
        ("t", "passes", "inf"), ("t", "passes", "nan"),
        ("t", "minVisit", "inf"), ("t", "minVisit", "nan"),
        ("cy", "perCycle", "inf")])
    def test_a_count_that_is_no_number_reads_as_its_default(self, stage, key,
                                                             value):
        """The compile route runs the doctor on every edit, and a hand-edited
        file can hold "inf": ``int()`` of it raises, which would be a 500
        where a bad value belongs. Found by fuzzing check() in the private
        copy: ``OverflowError: cannot convert float infinity to integer``.
        Read as the default, the visit is one 4 min pass again. The compile
        has the same fault in its own reading of a FILTER CYCLE (#328).

        Mutant "int of any number" (``_whole`` without its finite test)
        turned the passes and perCycle cases red: ``OverflowError: cannot
        convert float infinity to integer`` and ``ValueError: cannot convert
        float NaN to integer``. Mutant "minVisit not checked" turned the
        minVisit inf case red: ``OverflowError: cannot convert float
        infinity to integer``."""
        g = _mosaic()
        g = g.model_copy(update={"nodes": [
            n.model_copy(update={"params": {**n.params, key: value}})
            if n.id == stage else n for n in g.nodes]})
        rig = RigFacts(hop_cost_s=120.0, hop_samples=5)
        assert _one(check(g, rig=rig), M10).text.endswith(
            "against a 4 min visit; 2 passes per visit would cut hops by "
            "half.")


class TestM11SharedName:
    def test_two_blocks_called_m31_in_a_mosaic_graph(self):
        """Mutant "drop M11" turned this red: ``AssertionError: ('panel names
        and rules find targets by name', [('warn', "▸ TARGET - 'arm' input
        unwired")])``."""
        g = _mosaic(extra_nodes=[_block("t2", 1200, rows=1, cols=1)])
        hit = _one(check(g), M11)
        assert (hit.level, hit.text) == (
            "danger", "▸ two blocks are both called M31; panel names and rules "
            "find targets by name.")

    def test_distinct_names_are_fine(self):
        """Mutant "every name is one name" turned this red: ``assert not
        [Issue(text='▸ two blocks are both called x; panel names and rules
        find targets by name.', level='danger')]``."""
        g = _mosaic(extra_nodes=[_block("t2", 1200, name="M33", rows=1,
                                        cols=1)])
        assert not _hits(check(g), M11)

    def test_a_flow_saved_before_mosaics_is_not_touched(self):
        """Two palette TARGETs of an old build are both the missing-key "M31 -
        Andromeda", and that flow must draw what it always drew. Mutant "M11
        in every graph" turned this red: ``assert not [Issue(text='▸ two
        blocks are both called M31 - Andromeda; panel names and rules find
        targets by name.', level='danger')]``."""
        g = FlowGraph(nodes=[_n("a", "target"), _n("b", "target", 300)])
        assert not _hits(check(g), M11)


class TestM12Lane:
    @staticmethod
    def _mid_lane() -> FlowGraph:
        """TARGET(3x2) -> CYCLE -> CAPTURE Ha -> REPORT, with the loop wire
        leaving the CYCLE (spec 1.4 item 4)."""
        return FlowGraph(
            nodes=[_block(), _n("cy", "cycle", 400),
                   _n("ha", "capture", 600, filter="Ha", exposure=300),
                   _n("r", "report", 800)],
            edges=[_e("t", "target", "cy", "run"),
                   _e("cy", "complete", "ha", "run"),
                   _e("ha", "complete", "r", "session"),
                   _e("cy", "pass", "t", "next", "early")])

    @staticmethod
    def _branched(**block) -> FlowGraph:
        """The block feeds a CYCLE and a CAPTURE Ha side by side."""
        return FlowGraph(
            nodes=[_block(**block), _n("cy", "cycle", 400, 0),
                   _n("ha", "capture", 400, 200, filter="Ha", exposure=300)],
            edges=[_e("t", "target", "cy", "run"),
                   _e("t", "target", "ha", "run")])

    def test_a_loop_wire_from_mid_lane_is_a_danger(self):
        """Spec 1.4 item 4. Mutant "drop M12's mid-lane case" turned this red:
        ``AssertionError: ('the loop wire starts at', [])``."""
        hit = _one(check(self._mid_lane()), M12_MID)
        assert (hit.level, hit.text) == (
            "danger", "▸ TARGET M31 - the loop wire starts at FILTER CYCLE, but "
            "CAPTURE LOOP Ha comes after it in the panel lane. Start the loop "
            "wire at CAPTURE LOOP Ha to shoot it on every panel, or give "
            "CAPTURE LOOP Ha its own TARGET.")

    def test_a_branched_lane_is_a_danger(self):
        """Mutant "drop M12's branch case" turned this red: ``AssertionError:
        ('the panel lane branches', [('warn', "▸ TARGET - 'arm' input
        unwired"), ('warn', '▸ 180s subs with no GUIDE upstream ...he loop -
        focus drift goes uncorrected all night'), ('note', '▸ no session
        report sink - the night leaves no ledger')])``."""
        hit = _one(check(self._branched()), M12_BRANCH)
        assert (hit.level, hit.text) == (
            "danger", "▸ TARGET M31 - the panel lane branches: FILTER CYCLE and "
            "CAPTURE LOOP Ha both follow TARGET M31, so no stage is the last "
            "one and the panels have no loop to run. Make the lane one chain, "
            "or give a branch its own TARGET.")

    def test_the_loop_wire_from_the_tail_is_no_danger(self):
        """Mutant "M12 on every pass wire" turned this red:
        ``AssertionError: assert (not [Issue(text='▸ TARGET M31 - the loop
        wire starts at FILTER CYCLE, but  come after it in the panel lane.
        Start the loop wire at FILTER CYCLE to shoot them on every panel, or
        give  their own TARGET.', level='danger')])``."""
        issues = check(_mosaic())
        assert not _hits(issues, M12_MID) and not _hits(issues, M12_BRANCH)

    def test_a_single_target_may_branch_as_it_always_could(self):
        """Two stages off one single TARGET are a saved flow's right, and it
        must draw what it always drew. Mutant "M12 on every block" turned this
        red: ``assert not [Issue(text='▸ TARGET M31 - the panel lane
        branches: FILTER CYCLE and CAPTURE LOOP Ha both follow TARGET M31, so
        no st...st one and the panels have no loop to run. Make the lane one
        chain, or give a branch its own TARGET.', level='danger')]``."""
        assert not _hits(check(self._branched(rows=1, cols=1)), M12_BRANCH)


class TestM13NoOwner:
    def _dome(self) -> FlowGraph:
        """Spec 1.5 worked case 4: TARGET(3x2) -> CYCLE -> DOME -> CAPTURE."""
        return FlowGraph(
            nodes=[_block(), _n("cy", "cycle", 400), _n("dm", "dome", 600),
                   _n("sf", "safety", 600, 200),
                   _n("ha", "capture", 800, filter="Ha", exposure=300)],
            edges=[_e("t", "target", "cy", "run"),
                   _e("cy", "complete", "dm", "run"),
                   _e("dm", "open", "ha", "run"),
                   _e("cy", "pass", "t", "next")])

    def test_a_capture_past_a_dome_belongs_to_no_target(self):
        """Mutant "set walk (M13)" (a stage is owned when any TARGET or POOL
        is upstream along wires, the doctor's ``_flow_upstream_types``) went
        silent: ``AssertionError: ('belongs to no TARGET', [('warn', "▸
        TARGET - 'arm' input unwired"), ('warn', '▸ 180s subs with no GUIDE
        upstream - s...he loop - focus drift goes uncorrected all night'),
        ('note', '▸ no session report sink - the night leaves no
        ledger')])``. So did "drop M13", the same way."""
        hit = _one(check(self._dome()), M13)
        assert (hit.level, hit.text) == (
            "danger", "▸ CAPTURE LOOP Ha belongs to no TARGET: DOME CONTROL "
            "ends the M31 panel lane. It would shoot nothing. Give it a "
            "TARGET.")

    def test_a_stage_with_nothing_upstream(self):
        """Mutant "drop M13" turned this red: ``AssertionError: ('belongs
        to no TARGET', [('warn', "▸ CAPTURE LOOP - 'run' input
        unwired")])``."""
        g = _mosaic(extra_nodes=[_n("c", "capture", 900, 300, exposure=30)])
        assert _one(check(g), M13).text == (
            "▸ CAPTURE LOOP L belongs to no TARGET: its wires do not lead back "
            "to a TARGET or a TARGET POOL through lane stages alone. It would "
            "shoot nothing. Give it a TARGET.")

    def test_a_second_target_owns_what_follows_it(self):
        """Spec 1.5 worked case 3: TARGET(3x2) -> CYCLE -> TARGET(M33) ->
        CAPTURE. The CAPTURE is M33's. Mutant "M13 owner must be a mosaic"
        turned this red: ``assert not [Issue(text='▸ CAPTURE LOOP Ha belongs
        to no TARGET: its wires do not lead back to a TARGET or a TARGET POOL
        through lane stages alone. It would shoot nothing. Give it a
        TARGET.', level='danger')]``."""
        g = FlowGraph(
            nodes=[_block(), _n("cy", "cycle", 400),
                   _block("t2", 600, name="M33", rows=1, cols=1),
                   _n("ha", "capture", 800, filter="Ha", exposure=300)],
            edges=[_e("t", "target", "cy", "run"),
                   _e("cy", "complete", "t2", "arm"),
                   _e("t2", "target", "ha", "run"),
                   _e("cy", "pass", "t", "next")])
        assert not _hits(check(g), M13)

    def test_without_a_mosaic_r4_speaks_instead(self):
        """Mutant "M13 in every graph" turned this red: ``AssertionError:
        assert not [Issue(text='▸ CAPTURE LOOP L belongs to no TARGET: DUSK
        WINDOW comes before it and ends its lane, and no TARGET comes before
        that. It would shoot nothing. Give it a TARGET.', level='danger')]``.
        Mutant "drop R4" turned it red from the other side: ``assert []``."""
        g = FlowGraph(nodes=[_n("d", "dusk"), _n("c", "capture", exposure=30)],
                      edges=[_e("d", "window", "c", "run")])
        issues = check(g)
        assert not _hits(issues, M13)
        assert _hits(issues, R4)


class TestM14TargetDone:
    def _with_done(self, **block) -> FlowGraph:
        return _mosaic(extra_nodes=[_n("nt", "notify", 1200)],
                       extra_edges=[_e("r", "done", "nt", "do")], **block)

    def test_report_done_on_a_mosaic_fires_per_panel(self):
        """#184. Mutant "drop M14" turned this red: ``AssertionError: ('fires
        once per panel', [])``."""
        hit = _one(check(self._with_done()), M14)
        assert (hit.level, hit.text) == (
            "note", "▸ SESSION REPORT 'target done' fires once per panel, not "
            "once for the mosaic: TARGET M31 has 6 panels, so what the wire "
            "triggers runs once for each panel finished.")

    def test_a_report_with_no_done_wire_fires_nothing(self):
        """Mutant "M14 without the wire" turned this red: ``assert not
        [Issue(text="▸ SESSION REPORT 'target done' fires once per panel,
        not once for the mosaic: TARGET M31 has 6 panels, so what the wire
        triggers runs once for each panel finished.", level='note')]``."""
        assert not _hits(check(_mosaic()), M14)

    def test_a_single_target_finishes_once(self):
        """Beside a mosaic: a REPORT wired only to M33 (one panel) still
        fires once per M31 panel too, because the compiled rule carries no
        ``only_target`` gate at all (``_fire_target_complete`` runs on
        EVERY target's completion, panels included) -- a fact about the
        wire the operator drew for M33, not about M33's own panel count.
        (Without the mosaic the rule never runs at all, and mutant "M14 on
        any TARGET" survived that first version of this test: ``183
        passed``.)

        RULED (#184, backlog WP-34, 2026-09-30): the KNOWN GAP this case
        used to pin -- M14 scoping itself to "the REPORT that ends the
        mosaic's own lane", which read as the spec's words rather than the
        engine's wiring -- is closed. An ``only_target`` gate could not have
        closed it either (panels are named "M31 1-1" and so on, so no
        single name means "the mosaic"): the rule now fires for ANY REPORT
        with a ``done`` wire, anywhere in a graph that has a multi-panel
        block, and names that block and its panel count -- the real
        consequence for the wire under test here, since M31's six panels
        each retrigger it, not M33's own one.

        Mutant "M14 on any TARGET" (the mosaic guard dropped, so the rule
        also fires with no mosaic in the graph at all) turns this red by
        changing the note it finds, not by removing it: with no mosaic the
        hit would name M33's own one panel instead of M31's six --
        ``test_a_report_with_no_done_wire_fires_nothing`` and
        ``test_report_done_on_a_mosaic_fires_per_panel`` are the cases that
        catch the mosaic guard directly."""
        g = _mosaic(
            extra_nodes=[_block("t2", 1200, name="M33", rows=1, cols=1),
                         _n("c2", "capture", 1400, filter="Ha"),
                         _n("r2", "report", 1600),
                         _n("nt", "notify", 1800)],
            extra_edges=[_e("t2", "target", "c2", "run"),
                         _e("c2", "complete", "r2", "session"),
                         _e("r2", "done", "nt", "do")])
        hit = _one(check(g), M14)
        assert (hit.level, hit.text) == (
            "note", "▸ SESSION REPORT 'target done' fires once per panel, not "
            "once for the mosaic: TARGET M31 has 6 panels, so what the wire "
            "triggers runs once for each panel finished.")


class TestM15BudgetSpent:
    def test_convergence_past_half_the_overlap_is_a_danger(self):
        """A 4x1 at 10% at Dec 80. Mutant "k = 0.5" (the angle budget taken
        whole, ignoring convergence) turned this red: ``AssertionError:
        ('leaves no room for camera angle error', [('warn', '▸ TARGET M31 -
        at Dec 80 meridian convergence turns neighbouring panels against each
        other, which uses 58.8% of their 10% overlap. Widen the overlap or use
        fewer columns.')])``. "drop M15" turned it red with ``('leaves no room
        for camera angle error', [])``."""
        spec = _spec(1, 4, 0.10, 80.0)
        share = framing.convergence_share(spec)
        assert share >= framing.ROTATION_BUDGET
        assert framing.angle_tolerance_deg(spec) == 0.0
        hit = _one(check(_mosaic(rows=1, cols=4, overlap=10,
                                 dec="+80 00 00", rotation=0, angle=FIXED)), M15)
        assert (hit.level, hit.text) == (
            "danger", f"▸ TARGET M31 - at Dec 80 meridian convergence alone "
            f"uses {share * 100:.1f}% of the overlap, which leaves no room for "
            f"camera angle error. Widen the overlap or use fewer columns.")

    def test_the_4x1_at_10pc_at_dec_75_does_not_trip_it(self):
        """Spec 1.8: it uses 38.9% and keeps 0.47 deg of tolerance. Mutant
        "threshold 25%" (M15 at M6's line) turned this red: ``AssertionError:
        assert not [Issue(text='▸ TARGET M31 - at Dec 75 meridian convergence
        alone uses 38.9% of the overlap, which leaves no room for camera angle
        error. Widen the overlap or use fewer columns.', level='danger')]``."""
        spec = _spec(1, 4, 0.10, 75.0)
        assert framing.angle_tolerance_deg(spec) == pytest.approx(0.47,
                                                                  abs=0.005)
        assert not _hits(check(_mosaic(rows=1, cols=4, overlap=10,
                                       dec="+75 00 00", rotation=0, angle=FIXED)), M15)

    def test_no_overlap_is_a_spent_budget_in_other_words(self):
        """An infinite share. Mutant "k = 0.5" turned this red, and showed
        what the replacement guards against: ``AssertionError: ('leaves no
        room for camera angle error', [('warn', '▸ TARGET M31 - at Dec 41
        meridian convergence turns neighbouring panels against each other,
        which uses inf% of their 0% overlap. Widen the overlap or use fewer
        columns.')])``."""
        hit = _one(check(_mosaic(overlap=0, angle=FIXED)), M15)
        assert "with no overlap, meridian convergence at Dec 41" in hit.text


class TestL1LegacySlew:
    def test_a_slew_is_noted_from_its_own_tolerance(self):
        """Mutant "drop L1" turned this red: ``AssertionError: ('part of the
        TARGET block now', [('warn', "▸ SLEW + CENTER - 'run' input
        unwired"), ('note', '▸ no session report sink - the night leaves no
        ledger')])``."""
        hit = _one(check(FlowGraph(nodes=[_n("s", "slew")])), L1)
        assert (hit.level, hit.text) == (
            "note", "▸ SLEW + CENTER - this stage is part of the TARGET block "
            "now. Its 0.5 arcmin tolerance never reached the run, which "
            "centred to 1.2 arcmin. Delete it and set centring on the TARGET.")

    def test_the_tolerance_is_the_cards_not_the_default(self):
        """Mutant "L1 from the defaults" turned this red: ``AssertionError:
        assert 'Its 0.8 arcmin tolerance' in '▸ SLEW + CENTER - this stage is
        part of the TARGET block now. Its 0.5 arcmin tolerance never reached
        the run, which centred to 1.2 arcmin. Delete it and set centring on
        the TARGET.'``."""
        hit = _one(check(FlowGraph(nodes=[_n("s", "slew", tol=0.8)])), L1)
        assert "Its 0.8 arcmin tolerance" in hit.text
        # The run's figure is the TARGET's missing-key centring, which is the
        # hub's 0.02 deg: never the card's.
        assert NODE_DEFS["target"].params["centerTol"] == 1.2
        assert "centred to 1.2 arcmin" in hit.text

    def test_no_slew_no_note(self):
        """Mutant "L1 on every graph" turned this red: ``AssertionError:
        assert not [Issue(text='▸ SLEW + CENTER - this stage is part of the
        TARGET block now. Its None arcmin tolerance never reached the run,
        which centred to 1.2 arcmin. Delete it and set centring on the
        TARGET.', level='note')]``."""
        assert not _hits(check(_mosaic()), L1)


class TestTheWordsAtTheEdges:
    """The second wording of each rule that has one: every sentence the
    doctor can say is pinned, so each branch has a case that is red without
    it. Observed, one mutant per case, in order (pytest shortens a long
    string comparison with "..."):

    * "M3 always names a wire": ``AssertionError: assert '\u25b8 TARGET
      M31...s every pass.' == '\u25b8 TARGET M31...s every pass.'`` (on the
      GUIDE case as it was pinned; since #375 the no-port sentence is legacy
      SLEW's alone, and this mutant is shown red on SLEW in
      ``test_flows_doctor_pass_types.py``)
    * "no name is blank": ``assert False`` (the startswith)
    * "a pool's lane is a target's": ``AssertionError: assert '\u25b8
      CAPTURE LO... it a TARGET.' == '\u25b8 CAPTURE LO... it a TARGET.'``
    * "the ender left out": the same shape
    * "an unmeasured tolerance quoted as 0": ``AssertionError: assert False``
      (the endswith)
    * "always double the passes": ``AssertionError: assert '\u25b8 TARGET
      M31...hops by half.' == '\u25b8 TARGET M31...uld cut hops.'``
    * "hops always plural": ``AssertionError: assert '\u25b8 TARGET
      M31...uld cut hops.' == '\u25b8 TARGET M31...uld cut hops.'``
    * "always two blocks": ``AssertionError: assert '\u25b8 two
      blocks...gets by name.' == '\u25b8 3 blocks a...gets by name.'``
    * "one stage after the wire, always": ``AssertionError: assert '\u25b8
      TARGET M31...s own TARGET.' == '\u25b8 TARGET M31...r own TARGET.'``
    * "no typed test": ``AssertionError: assert (not [Issue(text='\u25b8
      TARGET M31 - at Dec 80 meridian convergence alone uses 58.8% of the
      overlap, which leaves no room for camera angle error. Widen the
      overlap or use fewer columns.', level='danger')])``"""

    def test_m3_on_a_lane_that_ends_on_guide(self):
        """GUIDE has 'pass done' since S4 (#331), so M3 names the wire to
        draw from it, as it does for a capture stage.

        A DELIBERATE PIN CHANGE (#375, slice S5). This case pinned "The
        panel lane ends at GUIDE, which has no 'pass done'; end it on a
        FILTER CYCLE or CAPTURE LOOP and wire that to TARGET 'next panel'
        ...", a sentence that was false from the day GUIDE gained the port,
        and it stayed green on it because the doctor read its own list of
        pass-bearing types. The no-port sentence is now legacy SLEW's alone,
        and ``test_flows_doctor_pass_types.py`` pins it there, where the
        mutant "M3 always names a wire" turns it red (this case is the
        mutant's reading now, so the list above no longer holds it).

        RED under mutant "_CAPTURE_TYPES restored" (the doctor's pass wires
        and M3's tail test back on ``_CAPTURE_TYPES``), run in a private
        copy of server/ (scratchpad s5-compile-mut), observed:

            E       AssertionError: assert '\\u25b8 TARGET M31...s every pass.' == '\\u25b8 TARGET M31...s every pass.'
            E         Skipping 88 identical leading characters in diff, use -v to show
            E         - ls empty. Wire GUIDE 'pass done' to TARGET 'next panel' to rotate panels every pass.
            E         + ls empty. The panel lane ends at GUIDE, which has no 'pass done'; end it on a FILTER CYCLE or CAPTURE LOOP and wire that to TARGET 'next panel' to rotate panels every pass.
        """
        g = FlowGraph(
            nodes=[_block(), _n("cy", "cycle", 400), _n("g", "guide", 600)],
            edges=[_e("t", "target", "cy", "run"),
                   _e("cy", "complete", "g", "run")])
        assert _one(check(g), M3).text == (
            "▸ TARGET M31 - panels are shot one after another: a night cut "
            "short leaves the last panels empty. Wire GUIDE 'pass done' to "
            "TARGET 'next panel' to rotate panels every pass.")

    def test_a_block_with_no_name_is_still_named(self):
        assert _one(check(_mosaic(name="", fovX=0)), M1).text.startswith(
            "▸ TARGET (no name) - frame this block")

    def test_m13_names_a_pools_lane(self):
        g = FlowGraph(
            nodes=[_block(), _n("cy", "cycle", 400), _n("p", "pool", 0, 300),
                   _n("cy2", "cycle", 200, 300), _n("dm", "dome", 400, 300),
                   _n("sf", "safety", 400, 500),
                   _n("ha", "capture", 600, 300, filter="Ha")],
            edges=[_e("t", "target", "cy", "run"),
                   _e("p", "target", "cy2", "run"),
                   _e("cy2", "complete", "dm", "run"),
                   _e("dm", "open", "ha", "run")])
        assert _one(check(g), M13).text == (
            "▸ CAPTURE LOOP Ha belongs to no TARGET: DOME CONTROL ends the "
            "TARGET POOL's panel lane. It would shoot nothing. Give it a "
            "TARGET.")

    def test_m13_when_nothing_above_the_ender_is_a_block(self):
        g = _mosaic(extra_nodes=[_n("d2", "dusk", 0, 400),
                                 _n("s2", "capture", 200, 400, filter="SII")],
                    extra_edges=[_e("d2", "window", "s2", "run")])
        assert _one(check(g), M13).text == (
            "▸ CAPTURE LOOP SII belongs to no TARGET: DUSK WINDOW comes before "
            "it and ends its lane, and no TARGET comes before that. It would "
            "shoot nothing. Give it a TARGET.")

    def test_m8_with_no_tolerance_to_quote(self):
        """A block known only by its name cannot be laid out here (no typed
        coordinates), so the tolerance is not computed and not invented."""
        hit = _one(check(_mosaic(ra="", dec=""),
                         rig=RigFacts(has_rotator=False)), M8)
        assert hit.text.endswith("holds the mosaic if it is off by more than "
                                 "this grid can absorb.")

    def test_m10_past_twenty_passes_and_one_hop(self):
        """passes 11: doubling it passes the 20 the TARGET allows; one
        measured hop is a hop, not hops."""
        rig = RigFacts(hop_cost_s=1000.0, hop_samples=1)
        assert _one(check(_mosaic(passes=11), rig=rig), M10).text == (
            "▸ TARGET M31 - each hop costs 16 m 40 s (measured over 1 hop) "
            "against a 44 min visit; a longer minimum visit would cut hops.")

    def test_m10_an_8_min_visit(self):
        """passes 2: two 4 min passes are an 8 min visit, said "an eight".
        Mutant "always 'a'" turned this red: ``AssertionError: assert
        '▸ TARGET M31...hops by half.' == '▸ TARGET M31...hops by
        half.'``, ``-  against an 8 min visit`` against ``+  against a 8
        min visit``. Mutant "'an' for every number" turned the 4 min
        cases above red ("against an 4 min visit")."""
        rig = RigFacts(hop_cost_s=150.0, hop_samples=5)
        assert _one(check(_mosaic(passes=2), rig=rig), M10).text == (
            "▸ TARGET M31 - each hop costs 2 m 30 s (measured over 5 hops) "
            "against an 8 min visit; 4 passes per visit would cut hops by "
            "half.")

    def test_m11_for_three_blocks(self):
        g = _mosaic(extra_nodes=[_block("t2", 1200, rows=1, cols=1),
                                 _block("t3", 1400, rows=1, cols=1)])
        assert _one(check(g), M11).text == (
            "▸ 3 blocks are all called M31; panel names and rules find targets "
            "by name.")

    def test_m12_with_two_stages_after_the_wire(self):
        g = FlowGraph(
            nodes=[_block(), _n("cy", "cycle", 400), _n("af", "autofocus", 600),
                   _n("ha", "capture", 800, filter="Ha")],
            edges=[_e("t", "target", "cy", "run"),
                   _e("cy", "complete", "af", "run"),
                   _e("af", "focused", "ha", "run"),
                   _e("cy", "pass", "t", "next")])
        assert _one(check(g), M12_MID).text == (
            "▸ TARGET M31 - the loop wire starts at FILTER CYCLE, but AUTOFOCUS "
            "and CAPTURE LOOP Ha come after it in the panel lane. Start the "
            "loop wire at CAPTURE LOOP Ha to shoot them on every panel, or give "
            "AUTOFOCUS and CAPTURE LOOP Ha their own TARGET.")

    def test_only_typed_coordinates_are_measured(self):
        """``to_plan`` lays a block out at its typed coordinates only when
        both are typed (``identity.typed_coordinates``), and the doctor
        measures what the run would lay out. An RA of the number 0 is typed:
        it is 0h, laid out and measured exactly as the same RA typed as text.

        DELIBERATE PIN CHANGE (S7 orchestrator ruling 6, #387's residual).
        This case used to pin the opposite: "an RA of 0 as a number is not
        [typed], and the block is resolved by its name", so the doctor did
        not measure it, while the Target modal read the same 0 as 0h and
        previewed a layout the run would never make. Ruling 6 makes a finite
        number typed, 0 included, on both sides. The old pin, run against
        the ruling-6 code, observed:

            E       AssertionError: assert (not [Issue(text='\\u25b8 TARGET M31 - at Dec 80 meridian convergence alone uses 58.8% of the overlap, which leaves no room for camera angle error. Widen the overlap or use fewer columns.', level='danger')])

        RED under the identity.py mutant "falsy non-text blank"
        (``_typed`` back to ``bool(str(value).strip()) if value else
        False``, the reading the old pin held), observed:

            E       AssertionError: ('leaves no room for camera angle error', [])
            E       assert 0 == 1
            FAILED tests/test_flows_doctor_s3.py::TestTheWordsAtTheEdges::test_only_typed_coordinates_are_measured

        The control below stays green under it, as it says.
        """
        typed = check(_mosaic(rows=1, cols=4, overlap=10, ra="00h 00m 00s",
                              dec="+80 00 00", rotation=0, angle=FIXED))
        as_text = _one(typed, M15)
        assert as_text.level == "danger"
        zero = check(_mosaic(rows=1, cols=4, overlap=10, ra=0,
                             dec="+80 00 00", rotation=0, angle=FIXED))
        assert _one(zero, M15) == as_text, (
            "an RA of the number 0 is 0h, measured as the text '00h 00m 00s' is")

    @pytest.mark.parametrize("ra", [None, False, float("nan"), float("inf")],
                             ids=["null", "false", "nan", "inf"])
    def test_control_a_blank_that_is_not_text_is_not_measured(self, ra):
        """CONTROL for the pin change above: what ruling 6 reads as blank
        (null, a bool, NaN, an infinity) places the block by its name, so
        the doctor measures nothing, as it measured nothing before. Green on
        the old reading and the new alike: the fixture tests
        (``test_flows_typed_coordinates.py``) are where these values are
        graded one by one."""
        untyped = check(_mosaic(rows=1, cols=4, overlap=10, ra=ra,
                                dec="+80 00 00", rotation=0))
        assert not _hits(untyped, M15) and not _hits(untyped, M6)


class TestControls:
    #: One graph that trips every rig rule under a full rig: a mosaic with no
    #: dawn stop, and a rig with a smaller camera, no rotator, both guards off
    #: and a dear hop.
    FULL = RigFacts(fov_deg=(1.40, 0.93), has_rotator=False,
                    reject_guards_off=True, hop_cost_s=120.0, hop_samples=5)

    def test_the_full_rig_trips_every_rig_rule(self):
        """Mutants "drop M5", "drop M8", "drop M9" and "drop M10" each
        turned this red with the marker it missed, e.g. ``AssertionError:
        this camera now images``."""
        issues = check(_mosaic(dusk_stop="None"), rig=self.FULL)
        for marker in (M5, M8, M9, M10):
            assert _hits(issues, marker), marker

    @pytest.mark.parametrize("rig", [None, RigFacts()], ids=["none", "empty"])
    def test_no_rig_runs_no_rig_rule(self, rig):
        """``rig=None`` (and a RigFacts that knows nothing) runs no rig rule,
        and every other issue is the one the full rig draws. Mutant "unknown
        means no rotator" turned both red: ``AssertionError: by hand to
        PA``."""
        g = _mosaic(dusk_stop="None")
        bare = check(g, rig=rig)
        for marker in (M5, M8, M9, M10):
            assert not _hits(bare, marker), marker
        full = [i for i in check(g, rig=self.FULL)
                if not any(m in i.text for m in (M5, M8, M9, M10))]
        assert bare == full

    def test_no_em_dash_and_no_forbidden_word_in_any_rule(self):
        """The house rules (spec 1.8, doctor.py): no em-dashes in a shipped
        string, and never the word 'slave'. Checked over every positive case
        above, gathered here into one corpus, which must reach every rule:
        each "drop" mutant turned this red naming the rule it lost, e.g.
        ``AssertionError: {'frame this block'}``."""
        corpus = [
            FlowGraph(nodes=[_n("c", "capture", exposure=30)]),
            _mosaic(fovX=0, angle="Any angle", rotation=-1, loop=False),
            TestM4LoopWireThatDoesNothing()._two_blocks(),
            _mosaic(rows=1, cols=1),
            _mosaic(extra_edges=[_e("cy", "pass", "t", "next", "loop2")]),
            _mosaic(rows=1, cols=4, overlap=10, dec="+75 00 00",
                    rotation=0),
            _mosaic(rows=1, cols=4, overlap=10, dec="+80 00 00",
                    rotation=0),
            _mosaic(overlap=0),
            TestM7CountsDisagree()._two("Accepted subs", "Every sub taken"),
            _mosaic(extra_nodes=[_block("t2", 1200, rows=1, cols=1)]),
            TestM12Lane._mid_lane(),
            TestM12Lane._branched(),
            TestM13NoOwner()._dome(),
            TestM14TargetDone()._with_done(),
            FlowGraph(nodes=[_n("s", "slew")]),
        ]
        texts = []
        for g in corpus:
            texts += [i.text for i in check(g, rig=self.FULL)]
            texts += [i.text for i in check(_mosaic(angle="Camera fixed at "
                                                   "PA"), rig=self.FULL)]
        found = {m for m in EVERY_MARKER if any(m in t for t in texts)}
        assert found == set(EVERY_MARKER), set(EVERY_MARKER) - found
        for t in texts:
            assert "\u2014" not in t, t
            assert "slave" not in t.lower(), t


# ------------------------------------------------ output unchanged before S3
#
# "Output is unchanged for graphs with no multi-panel block and no SLEW,
# except R4's new wording." GOLDEN, and the golden is HEAD's doctor (the one
# before this change), run over this corpus on the S3 vocabulary in the
# private copy, never this doctor's own output. Regenerating it from the new
# doctor would make the control a replay of itself.

OLD_RULE_4 = ("▸ CAPTURE with no SLEW + CENTER upstream - the loop shoots "
              "wherever the mount happens to point")


def unchanged_corpus() -> dict[str, tuple[FlowGraph, bool]]:
    """``{name: (graph, a block points the mount for every capture)}``. No
    multi-panel block and no SLEW anywhere; the flag says which way R4 reads
    each graph's captures (every graph here has all or none).

    Built inline, not from the Examples: S3-W redraws those, and a golden of
    an old graph compared with the doctor on a new one would grade nothing.
    The three lanes at the end are the Examples' shapes with SLEW spliced
    out, which is how S3-W will draw them."""
    lane = [("af", "autofocus"), ("g", "guide")]

    def chain(head: list, tail: list) -> FlowGraph:
        nodes = [_n(nid, t, 200 * i) for i, (nid, t) in
                 enumerate(head + lane + tail)]
        ports = {"dusk": "window", "target": "target", "pool": "target",
                 "autofocus": "focused", "guide": "guiding",
                 "capture": "complete", "cycle": "complete"}
        ins = {"target": "arm", "pool": "arm", "report": "session"}
        edges = [_e(a.id, ports[a.type], b.id, ins.get(b.type, "run"))
                 for a, b in zip(nodes, nodes[1:])]
        return FlowGraph(nodes=nodes, edges=edges)

    corpus = {
        "capture-alone": (FlowGraph(nodes=[_n("c", "capture",
                                             exposure=30)]), False),
        "long-capture-alone": (FlowGraph(nodes=[_n("c", "capture",
                                                  exposure=180)]), False),
        "dusk-capture": (FlowGraph(
            nodes=[_n("d", "dusk"), _n("c", "capture", exposure=60)],
            edges=[_e("d", "window", "c", "run")]), False),
        "target-capture": (FlowGraph(
            nodes=[_n("t", "target"), _n("c", "capture", exposure=30)],
            edges=[_e("t", "target", "c", "run")]), True),
        "pool-cycle": (FlowGraph(
            nodes=[_n("p", "pool"), _n("cy", "cycle")],
            edges=[_e("p", "target", "cy", "run")]), True),
        "queue-hold": (FlowGraph(
            nodes=[_n("cw", "cloudwatch"), _n("q", "calib")],
            edges=[_e("cw", "in", "q", "do")]), True),
        "watchdog": (FlowGraph(
            nodes=[_n("c", "capture", reject=3.0, exposure=30),
                   _n("k", "condition", when="HFR above", threshold=4.0),
                   _n("rf", "refocus")],
            edges=[_e("c", "frame", "k", "events"),
                   _e("k", "fire", "rf", "do")]), False),
        "dome": (FlowGraph(nodes=[_n("dm", "dome")]), True),
        "race": (FlowGraph(nodes=[_n("s", "safety"),
                                  _n("cw", "cloudwatch")]), True),
        "m31-lane-no-slew": (chain([("d", "dusk"), ("t", "target")],
                                   [("cy", "cycle"), ("r", "report")]), True),
        "pool-lane-no-slew": (chain([("d", "dusk"), ("p", "pool")],
                                    [("c", "capture"), ("r", "report")]),
                              True),
        "eaa-lane-no-slew": (chain([("t", "target")], [("c", "capture")]),
                             True),
    }
    return corpus


#: Generated by running HEAD's doctor.py (sha256 eeb7089f7f1c...) over
#: ``unchanged_corpus()`` in the private copy (gen_golden2.py there).
GOLDEN_BEFORE_S3: dict[str, list[tuple[str, str]]] = {
    'capture-alone': [
        ('warn',
         "▸ CAPTURE LOOP - 'run' input unwired"),
        ('warn', OLD_RULE_4),
        ('note',
         '▸ no session report stage - the night will not have a saved report'),
    ],
    'long-capture-alone': [
        ('warn',
         "▸ CAPTURE LOOP - 'run' input unwired"),
        ('warn',
         '▸ 180s subs with no GUIDE upstream - stars will trail at '
         'any real focal length. Add Guide, or shorten the subs.'),
        ('warn',
         '▸ no AUTOFOCUS before the loop - focus drift goes '
         'uncorrected all night'),
        ('warn', OLD_RULE_4),
        ('note',
         '▸ no session report stage - the night will not have a saved report'),
    ],
    'dusk-capture': [
        ('warn',
         '▸ no AUTOFOCUS before the loop - focus drift goes '
         'uncorrected all night'),
        ('warn', OLD_RULE_4),
        ('note',
         '▸ no session report stage - the night will not have a saved report'),
    ],
    'target-capture': [
        ('warn',
         "▸ TARGET - 'arm' input unwired"),
        ('warn', OLD_RULE_4),
        ('note',
         '▸ no session report stage - the night will not have a saved report'),
    ],
    'pool-cycle': [
        ('warn',
         "▸ TARGET POOL - 'arm' input unwired"),
        ('warn',
         '▸ 180s subs with no GUIDE upstream - stars will trail at '
         'any real focal length. Add Guide, or shorten the subs.'),
        ('warn',
         '▸ no AUTOFOCUS before the loop - focus drift goes '
         'uncorrected all night'),
        ('warn', OLD_RULE_4),
        ('note',
         '▸ no session report stage - the night will not have a saved report'),
    ],
    'queue-hold': [
        ('warn',
         "▸ QUEUE wants flats but nothing is wired to 'panel' - "
         'flats will be skipped'),
        ('warn',
         '▸ queue runs but nothing HOLDS the light loop - wire HOLD '
         '/ RESUME from the same trigger'),
        ('note',
         '▸ no session report stage - the night will not have a saved report'),
    ],
    'watchdog': [
        ('warn',
         "▸ CAPTURE LOOP - 'run' input unwired"),
        ('warn', OLD_RULE_4),
        ('warn',
         "▸ watchdog threshold 4.0″ sits above the grader's reject "
         '3.0″ - frames get rejected before the rule can ever fire'),
        ('note',
         '▸ no session report stage - the night will not have a saved report'),
    ],
    'dome': [
        ('warn',
         "▸ DOME CONTROL - 'open' input unwired"),
        ('danger',
         '▸ DOME with no SAFETY MONITOR - nothing closes the shutter'
         ' on rain. Add one; it fails closed.'),
        ('note',
         '▸ no session report stage - the night will not have a saved report'),
    ],
    'race': [
        ('warn',
         '▸ SAFETY watches clouds while CLOUD WATCH is wired - they '
         'race, and safety aborts before the hold can ride it out. '
         'Set SAFETY → Watch for → rain + wind + power.'),
        ('note',
         '▸ no session report stage - the night will not have a saved report'),
    ],
    'm31-lane-no-slew': [
        ('warn', OLD_RULE_4),
    ],
    'pool-lane-no-slew': [
        ('warn', OLD_RULE_4),
    ],
    'eaa-lane-no-slew': [
        ('warn',
         "▸ TARGET - 'arm' input unwired"),
        ('warn', OLD_RULE_4),
        ('note',
         '▸ no session report stage - the night will not have a saved report'),
    ],
}


class TestOutputUnchangedBeforeS3:
    """The acceptance control: for a graph with no multi-panel block and no
    SLEW, the doctor says what it said before S3, except R4. R4 is two edits
    to rule 4 and only two: the new words, and a TARGET or a POOL upstream
    now satisfies it."""

    @pytest.mark.parametrize("name", sorted(GOLDEN_BEFORE_S3))
    def test_the_old_issues_in_the_old_order(self, name):
        """Observed red under: "drop R4" and "R4 wording unchanged" (the four
        graphs with a capture and no block upstream, e.g. ``AssertionError:
        ('capture-alone', [('warn', "▸ CAPTURE LOOP - 'run' input
        unwired"), ('note', '▸ no session report sink - the night leaves no
        ledger')])``); "SLEW only" (the five with a TARGET or POOL upstream);
        "M1 on any TARGET" and "M2 on any TARGET" (the three with a TARGET);
        "M13 in every graph" (the four with an unowned capture); and "L1 on
        every graph" (all twelve, e.g. ``('m31-lane-no-slew', [('note', '▸
        SLEW + CENTER - this stage is part of the TARGET block now. Its None
        arcmin tolerance never reached the run, which centred to 1.2 arcmin.
        Delete it and set centring on the TARGET.')])``)."""
        graph, blocked = unchanged_corpus()[name]
        want = [(lv, "▸ CAPTURE with no TARGET upstream - the loop shoots "
                     "wherever the mount happens to point")
                if tx == OLD_RULE_4 else (lv, tx)
                for lv, tx in GOLDEN_BEFORE_S3[name]
                if not (tx == OLD_RULE_4 and blocked)]
        got = [(i.level, i.text) for i in check(graph)]
        assert got == want, (name, got)

    def test_the_corpus_is_what_the_control_says_it_is(self):
        """No multi-panel block, no SLEW, and both readings of R4 present, so
        the control grades both of its edits."""
        corpus = unchanged_corpus()
        assert set(corpus) == set(GOLDEN_BEFORE_S3)
        for graph, _ in corpus.values():
            assert all(n.type != "slew" for n in graph.nodes)
            assert not any(n.type == "target"
                           and int(n.params.get("rows", 1)) *
                           int(n.params.get("cols", 1)) > 1
                           for n in graph.nodes)
        readings = {(blocked, any(tx == OLD_RULE_4 for _, tx in
                                  GOLDEN_BEFORE_S3[name]))
                    for name, (_, blocked) in corpus.items()}
        assert (True, True) in readings and (False, True) in readings
