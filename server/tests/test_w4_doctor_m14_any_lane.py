# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""M14 fires for ANY REPORT 'target done' wire in a graph that has a
multi-panel block, not only the REPORT that ends that block's own lane
(#184, owner comment 2026-09-25, "Doctor M14 is narrower than the engine").

BEFORE THIS FIX, M14 asked whose lane the REPORT ended: its one flow parent
had to be the multi-panel block itself, or a stage the block owns. The
engine does not scope the compiled rule that way at all - REPORT 'target
done' compiles to an ungated `on_target_complete` (`compile._trigger_for`;
neither `compile.py` nor `to_plan.py` sets `only_target`), and the engine's
`_fire_target_complete` runs the rule at EVERY target's completion, mosaic
panel or not. So a REPORT drawn after an unrelated single-panel TARGET,
beside a mosaic in another lane, looked safe under the old rule and was
not: the wire still fired once per panel of the mosaic it never touched.
An `only_target` gate cannot close this either - it is a name gate, and
panels are named "M31 1-1" and so on, so no single name means "the mosaic".

WP-34's owned files do not include `server/tests/test_flows_doctor_s3.py`,
so its `TestM14TargetDone.test_a_single_target_finishes_once` - which pins
exactly the old, narrower silence this fix removes - is now stale and is
reported in this work package's return rather than edited here (see the
plan's own fix shape: "The S3 test that pins the silence then flips to a
positive case"). This file is the free-standing positive case for the
corrected rule, independent of that file's helpers.

NAMED MUTANT: "M14 scoped to owning lane" (restoring the single-flow-parent
check this fix removes) turns `test_a_report_in_another_lane_is_flagged`
red. See that test's docstring for the verbatim failing assertion.
"""
from __future__ import annotations

from astrodeck.flows import check
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode

#: Catalogue coordinates for a made-up TARGET block, not the observing site
#: (the privacy rule is about the rig's own location, never a deep-sky
#: object's RA/Dec).
_RA = "00h 42m 44s"
_DEC = "+41° 16′ 09″"

M14_MARKER = "fires once per panel"


def _n(nid: str, ntype: str, x: float = 0.0, y: float = 0.0,
       **params) -> FlowNode:
    return FlowNode(id=nid, type=ntype, x=float(x), y=float(y), params=params)


def _e(src: str, sp: str, dst: str, dp: str) -> FlowEdge:
    return FlowEdge(**{"from": src, "fromPort": sp, "to": dst, "toPort": dp})


def _block(nid: str, name: str, rows: int, cols: int, x: float) -> FlowNode:
    """A fully-framed multi-panel TARGET, so only M14 has anything to say."""
    return _n(nid, "target", x, 0, name=name, ra=_RA, dec=_DEC, rows=rows,
             cols=cols, overlap=25, fovX=2.0, fovY=1.33,
             angle="Rotate to PA", rotation=30, counts="Accepted subs")


def _hits(issues, marker: str):
    return [i for i in issues if marker in i.text]


def _mosaic_beside_a_single_target(*, mosaic_rows: int = 3,
                                   mosaic_cols: int = 2) -> FlowGraph:
    """The issue's own evidence, reproduced: a rotating mosaic in its own
    lane (loop wire, no REPORT wired to 'done'), beside an unrelated
    single-panel TARGET whose own REPORT DOES wire 'done' onward. Mirrors
    `server/tests/test_flows_doctor_s3.py::TestM14TargetDone.
    test_a_single_target_finishes_once`'s shape (not its code), because
    that file is outside this WP's owned files."""
    mosaic = _block("m", "M31", mosaic_rows, mosaic_cols, x=0)
    nodes = [
        mosaic,
        _n("af", "autofocus", 200),
        _n("g", "guide", 400),
        _n("cy", "cycle", 600, plan="L 60, R 60"),
        _n("r1", "report", 800),
        _block("single", "M33", 1, 1, x=1200),
        _n("c2", "capture", 1400),
        _n("r2", "report", 1600),
        _n("nt", "notify", 1800),
    ]
    edges = [
        _e("m", "target", "af", "run"),
        _e("af", "focused", "g", "run"),
        _e("g", "guiding", "cy", "run"),
        _e("cy", "complete", "r1", "session"),
        _e("cy", "pass", "m", "next"),          # the mosaic's own loop wire
        _e("single", "target", "c2", "run"),
        _e("c2", "complete", "r2", "session"),
        _e("r2", "done", "nt", "do"),            # the wire M14 must catch
    ]
    return FlowGraph(nodes=nodes, edges=edges)


def test_a_report_in_another_lane_is_flagged():
    """The REPORT that fires is M33's, not the mosaic's own: M14 must still
    name the mosaic, because the engine's `on_target_complete` fires on
    every target's completion in the whole plan, not only targets in the
    REPORT's own lane.

    Mutant "M14 scoped to owning lane" (re-adding the removed single-parent
    check: `ps = parents.get(n.id, []); if len(ps) != 1: continue; up =
    ps[0]; mblock = up if up.type == "target" else owner_of(graph, up.id);
    if mblock is None or not is_multi_panel(mblock): continue`) turned this
    red, verbatim (run from a byte backup, restored and sha256-verified
    afterwards):

        AssertionError: [('warn', "...'arm' input unwired"), ('warn',
        "...'arm' input unwired"), ('warn', '...120s subs with no GUIDE
        upstream...'), ('warn', '...no AUTOFOCUS before the loop...')]
        assert 0 == 1
         +  where 0 = len([])

    - the hit list was empty because r2's one flow parent is M33, a
    single-panel TARGET, not the mosaic."""
    g = _mosaic_beside_a_single_target()
    hits = _hits(check(g), M14_MARKER)
    assert len(hits) == 1, [(i.level, i.text) for i in check(g)]
    assert hits[0].level == "note"
    assert "TARGET M31 has 6 panels" in hits[0].text
    assert "TARGET M33" not in hits[0].text, (
        "the single-panel block beside it is not a mosaic and must not be "
        "named")


def test_a_report_with_no_done_wire_still_draws_nothing():
    """The mosaic-anywhere-in-the-graph scoping does not turn every REPORT
    into a hit: one with no outgoing 'done' wire names nothing, same as
    before this fix (`r1` above, fed only by the cycle's 'complete')."""
    g = _mosaic_beside_a_single_target()
    # r1's issue set, if M14 covered every REPORT, would duplicate r2's; it
    # must not appear at all because r1 has no 'done' edge.
    issues = check(g)
    done_reports = {e.to for e in g.edges if e.fromPort == "done"}
    assert done_reports == {"nt"}, "only r2 -> nt wires 'done' in this graph"
    # Exactly one hit (r2's), not two: proves r1 contributes nothing.
    assert len(_hits(issues, M14_MARKER)) == 1


def test_every_mosaic_in_the_graph_is_named():
    """Two multi-panel blocks, one REPORT 'done' wire: the message names
    BOTH mosaics and both panel counts, because the ungated trigger fires on
    every panel of every mosaic in the plan, not just the nearest one."""
    a = _block("a", "A", 2, 2, x=0)      # 4 panels
    b = _block("b", "B", 1, 3, x=400)    # 3 panels
    nodes = [a, b, _n("r", "report", 800), _n("nt", "notify", 1000)]
    edges = [_e("r", "done", "nt", "do")]
    g = FlowGraph(nodes=nodes, edges=edges)
    hit = _hits(check(g), M14_MARKER)
    assert len(hit) == 1, [(i.level, i.text) for i in check(g)]
    text = hit[0].text
    assert "TARGET A has 4 panels" in text
    assert "TARGET B has 3 panels" in text
    assert "and" in text, "both mosaics must be named, joined like a list"


def test_no_multi_panel_block_at_all_draws_nothing():
    """Sanity: a single-panel TARGET with a 'done' wire is not a mosaic, so
    M14 stays silent (unchanged from before this fix)."""
    solo = _block("s", "M42", 1, 1, x=0)
    nodes = [solo, _n("c", "capture", 200), _n("r", "report", 400),
             _n("nt", "notify", 600)]
    edges = [_e("s", "target", "c", "run"), _e("c", "complete", "r", "session"),
             _e("r", "done", "nt", "do")]
    g = FlowGraph(nodes=nodes, edges=edges)
    assert _hits(check(g), M14_MARKER) == []
