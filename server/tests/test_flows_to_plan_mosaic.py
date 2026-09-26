"""A TARGET block with a grid, as a plan the engine can run (spec 3.3;
#189 U-09, #170, #151).

`to_plan` expands one compiled block into one Target per panel and one
TargetGroup. The expansion goes through `framing.compute_mosaic`, the one
projection, so the run slews to exactly what the Atlas previewed; a second
copy of the projection here would be the "third copy" spec 3.3 forbids.

WHAT MUST HOLD, AND WHY EACH IS HERE:

* the panels' IDS are a function of the flow, the block, its anchor and the
  panel, and never of `skip`. The ledger counts frames by step id alone, so
  an id that moved when a neighbour was skipped would orphan that panel's
  frames;
* the loop wire is STRUCTURE: it makes the group rotate and is never a rule,
  because a rule the engine cannot run is a loss the operator must accept for
  the one wire that works;
* a mosaic that cannot tile (no camera field, no angle) or whose wires cannot
  say what to shoot on each panel (M12, M13) is REFUSED, not run as something
  else;
* the centring the run uses is the TARGET's, never a legacy SLEW card's.

Every mutant below was run from a byte backup in a private copy of server/
(scratchpad s3-cp-mut), never in the shared tree; the failure quoted is the
one observed. The cases marked as added by the S3-CP verifier were run the
same way in scratchpad s3-cp-verify-x7q4: each covers a rule every earlier
mutant left green.
"""
from __future__ import annotations

import json

import pytest

from astrodeck.catalog import framing
from astrodeck.flows import identity
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FLOW_SETTINGS, FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.rig import RigFacts
from astrodeck.flows.store import FlowStore
from astrodeck.flows.to_plan import (GROUP_ORDERS, WAIT_FOR_THE_MOSAIC,
                                     GraphNotRunnable, losses,
                                     to_sequence_plan)
from astrodeck.sequence.models import plan_identity_errors

FLOW_ID = "flow-s3cp"
M31 = {"name": "M31", "ra": "00h 42m 44s", "dec": "+41 16 09"}
M33 = {"name": "M33", "ra": "01h 33m 51s", "dec": "+30 39 37"}
FIELD = {"fovX": 2.0, "fovY": 1.33}


def _n(nid, ntype, x=0.0, y=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=y, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def _mosaic(*, rows=2, cols=2, loop=True, stages=("cycle",), settings=None,
            after=(), **target) -> FlowGraph:
    """DUSK -> TARGET (a framed 2x2 at Rotate to PA 30) -> the stages in a
    chain, with the loop wire from the last stage when ``loop``, and the
    ``after`` nodes chained off the tail's "all done"."""
    params = {**M31, **FIELD, "rotation": 30, "angle": "Rotate to PA",
              "rows": rows, "cols": cols, **target}
    nodes = [_n("d", "dusk"), _n("t", "target", x=100, **params)]
    edges = [_e("d", "window", "t", "arm")]
    prev, port = "t", "target"
    for i, kind in enumerate(stages):
        sid = f"s{i}"
        extra = ({"exposure": 60, "count": 5, "goal": 0}
                 if kind == "capture" else {})
        nodes.append(_n(sid, kind, x=200 + 100 * i, **extra))
        edges.append(_e(prev, port, sid, "run"))
        prev, port = sid, "complete"
    if loop and stages:
        edges.append(_e(prev, "pass", "t", "next"))
    for node in after:
        nodes.append(node)
        edges.append(_e(prev, port, node.id,
                        "run" if node.type in ("capture", "cycle") else "arm"))
        prev, port = node.id, ("target" if node.type in ("target", "pool")
                               else "complete")
    return FlowGraph(nodes=nodes, edges=edges, settings=settings or {})


def _plan(graph: FlowGraph, *, flow_id: str = FLOW_ID, rig=None):
    return to_sequence_plan(compile_plan(graph, "n"), graph, flow_id=flow_id,
                            rig=rig)


def _spec(**over) -> dict:
    """The 2x2's layout in `compute_mosaic`'s words."""
    return {"ra_hours": 0 + 42 / 60 + 44 / 3600,
            "dec_deg": 41 + 16 / 60 + 9 / 3600, "rows": 2, "cols": 2,
            "overlap": 0.25, "rotation_deg": 30.0, "fov_x_deg": 2.0,
            "fov_y_deg": 1.33, **over}


def _key(**grid) -> str:
    """The block's key as `to_plan` must make it: `identity.target_key` of
    the typed geometry, with no anchor, and the block's grid."""
    s = _spec(**grid)
    return identity.target_key(
        {**M31}, s["ra_hours"], s["dec_deg"], s["rotation_deg"],
        canonical=None, rows=s["rows"], cols=s["cols"], overlap=s["overlap"],
        fov_x=s["fov_x_deg"], fov_y=s["fov_y_deg"])


def _texts(unmapped) -> str:
    return "\n".join(u["detail"] for u in unmapped)


# ============================================================ the expansion

class TestTheExpansion:
    def test_one_target_per_panel_in_the_servers_convention(self):
        """The panels `compute_mosaic` lays out, in its order (rows from the
        top, the second row snaking back), named 1-based and carrying their
        0-based row and column, at exactly the coordinates it gives.

        Mutant "0-based panel names" (the name spells ``{row}-{col}``),
        observed:

            AssertionError: assert ['M31 0-0', '...1', 'M31 1-0'] == ['M31 1-1', '...2', 'M31 2-1']
              At index 0 diff: 'M31 0-0' != 'M31 1-1'

        Mutant "1-based panel rows" (``panel_row = row + 1``), observed:

            assert [(1, 0), (1, ...2, 1), (2, 0)] == [(0, 0), (0, ...1, 1), (1, 0)]
              At index 0 diff: (1, 0) != (0, 0)
        """
        plan, _ = _plan(_mosaic())
        want = framing.compute_mosaic(_spec())["panels"]
        assert [t.name for t in plan.targets] == \
            ["M31 1-1", "M31 1-2", "M31 2-2", "M31 2-1"]
        assert [(t.panel_row, t.panel_col) for t in plan.targets] == \
            [(p["row"], p["col"]) for p in want]
        for t, p in zip(plan.targets, want):
            assert t.ra_hours == pytest.approx(p["ra_hours"], abs=1e-12)
            assert t.dec_deg == pytest.approx(p["dec_deg"], abs=1e-12)

    def test_every_panel_carries_what_a_hop_needs(self):
        """The group's id, the PA for a rotator, `cycle` acquisition, the
        block's centring, the fresh-focus skip (a hop moves no focuser, spec
        5.6) and the dusk schedule.

        Mutant "panels sweep at every hop" (``autofocus_skip_if_fresh``
        False), observed:

            AssertionError: assert False is True
        """
        plan, _ = _plan(_mosaic(centerTol=0.8, centerTries=4))
        group = plan.groups[0]
        for t in plan.targets:
            assert t.mosaic_group == group.id
            assert t.rotation_deg == 30.0
            assert t.acquisition == "cycle"
            assert (t.center_tolerance_arcmin, t.center_attempts) == (0.8, 4)
            assert t.autofocus_skip_if_fresh is True
            assert t.schedule.start_mode == "dusk"
            assert t.after_group is None
            assert [s.filter for s in t.steps] == \
                ["L", "R", "G", "B", "Ha", "OIII", "SII"]

    @pytest.mark.parametrize("angle,commanded", [
        ("Rotate to PA", 30.0), ("Camera fixed at PA", None)])
    def test_only_rotate_commands_the_rotator(self, angle, commanded):
        """"Camera fixed at" never commands the rotator (spec 2.4): the grid
        is laid out at the PA and the run checks the angle instead.

        Mutant "every set angle is commanded" (`_angles` returns the layout
        angle as the commanded one), observed:

            assert {30.0} == {None}
              Extra items in the left set:
              30.0
              Extra items in the right set:
              None
        """
        plan, _ = _plan(_mosaic(angle=angle))
        assert {t.rotation_deg for t in plan.targets} == {commanded}
        group = plan.groups[0]
        assert group.pa_deg == 30.0
        assert group.rotate is (angle == "Rotate to PA")

    def test_the_group_carries_the_block(self):
        """One TargetGroup per block (spec 3.4): its id over the anchor key,
        the visit bound (`minVisit` minutes as seconds), the order mapped
        onto the engine's words, the angle budget from
        `framing.angle_tolerance_deg` and the provenance.

        Mutant "visit_min_s is minutes" (no x 60), observed:

            assert (2, 10.0) == (2, 600.0)
              At index 1 diff: 10.0 != 600.0
        """
        plan, _ = _plan(_mosaic(passes=2, minVisit=10, order="Setting first",
                                fovFrom="profile Refractor"))
        (group,) = plan.groups
        key = _key()
        assert group.id == identity.group_id(FLOW_ID, "t", key)
        assert (group.name, group.kind, group.mode) == ("M31", "mosaic",
                                                        "rotate")
        assert (group.visit_passes, group.visit_min_s) == (2, 600.0)
        assert group.order == "setting_first"
        assert group.require_centred is True
        assert group.angle_tolerance_deg == pytest.approx(
            framing.angle_tolerance_deg(_spec()))
        assert group.skipped_ids == []
        assert group.geometry == {"rows": 2, "cols": 2, "overlap": 0.25,
                                  "fov_x": 2.0, "fov_y": 1.33,
                                  "fov_from": "profile Refractor", "key": key}

    @pytest.mark.parametrize("words,code", [
        ("Least complete first", "least_complete"),
        ("Setting first", "setting_first"), ("Grid order", "grid"),
        ("Whatever", "least_complete")])
    def test_the_order_is_mapped(self, words, code):
        """Mutant "order is always least complete", observed:

            AssertionError: assert 'least_complete' == 'setting_first'
            AssertionError: assert 'least_complete' == 'grid'
        """
        plan, _ = _plan(_mosaic(order=words))
        assert plan.groups[0].order == code
        assert GROUP_ORDERS.get(words, "least_complete") == code

    @pytest.mark.parametrize("choice,required", [
        ("Auto", True), ("Skip it this pass", True), ("Shoot anyway", False)])
    def test_require_centred_is_false_only_for_shoot_anyway(self, choice,
                                                            required):
        """Mutant "require_centred always True" (the group ignores the
        entry), observed for Shoot anyway:

            AssertionError: assert True is False
        """
        plan, _ = _plan(_mosaic(ifNotCentred=choice))
        assert plan.groups[0].require_centred is required

    def test_a_single_panel_block_stays_one_plain_target(self):
        """Spec 3.3: a 1x1 block is today's single target plus its centring,
        with no group and no panel fields.

        Mutant "every block is a group" (``compile.py`` takes the grid
        branch for a 1x1 too), observed:

            AssertionError: assert ['M31 1-1'] == ['M31']
              At index 0 diff: 'M31 1-1' != 'M31'
        """
        plan, _ = _plan(_mosaic(rows=1, cols=1, loop=False, centerTol=0.5))
        assert [t.name for t in plan.targets] == ["M31"]
        (t,) = plan.targets
        assert plan.groups == []
        assert (t.mosaic_group, t.panel_row, t.panel_col) == (None, None, None)
        assert t.center_tolerance_arcmin == 0.5 and t.center_attempts == 3
        assert t.autofocus_skip_if_fresh is False
        assert t.rotation_deg == 30.0


# ===================================================== a block of one panel

class TestASinglePanelBlock:
    """A 1x1 block is one plain Target (spec 3.3), but it is still a block:
    its angle decides what the rotator is told, and its ANCHOR keys it, as a
    mosaic's does. Added by the S3-CP verifier: each of the three rules below
    survived every mutant the first reading ran."""

    @staticmethod
    def _target(**target):
        plan, _ = _plan(_mosaic(rows=1, cols=1, loop=False, **target))
        (t,) = plan.targets
        return t

    @staticmethod
    def _id_of(key: str) -> str:
        return identity.target_id(identity.group_id(FLOW_ID, "t", key), 0, 0)

    def test_camera_fixed_at_pa_commands_no_rotator(self):
        """Spec 2.4 and 3.3: "Camera fixed at" is checked and never
        commanded, for one panel as for many. Commanding it would turn a
        camera the operator said is fixed. "Rotate to PA" is the control.

        Mutant "a single fixed-camera target commands its PA" (the 1x1
        branch of `to_sequence_plan` tells the rotator the layout angle),
        observed:

            AssertionError: assert 30.0 is None

        (the next line, the Target's repr, left out).
        """
        assert self._target(angle="Camera fixed at PA").rotation_deg is None
        assert self._target(angle="Rotate to PA").rotation_deg == 30.0

    def test_a_fixed_camera_keys_on_the_angle_it_is_laid_out_at(self):
        """`identity.anchor_for` holds "camera fixed at" at its planned PA,
        and the save writes that text as a block's first anchor, so the same
        block unsaved must key on the PA too: keyed on the angle it commands
        (none), its first save would move its id and orphan what it banked.

        Mutant "a single target keyed on the commanded angle" (the 1x1 key
        made from the rotator's angle, not the layout's), observed:

            AssertionError: assert 'ce5d05fd57f4...7bd6baced6638' == '2a9a9a56e185...59b66876bd7ed'
              - 2a9a9a56e1855e709af59b66876bd7ed
              + ce5d05fd57f45200b037bd6baced6638
        """
        s = _spec()
        key = identity.target_key({**M31}, s["ra_hours"], s["dec_deg"], 30.0,
                                  canonical=None)
        unsaved = self._target(angle="Camera fixed at PA")
        assert unsaved.id == self._id_of(key)
        anchor = identity.anchor_for({**M31}, s["ra_hours"], s["dec_deg"],
                                     30.0, canonical=None)
        saved = self._target(angle="Camera fixed at PA", frameAnchor=anchor)
        assert saved.id == unsaved.id

    def test_the_anchor_keys_a_single_target_too(self):
        """Spec 3.3 (a 1x1 block's anchor rides on its entry) and ruling 3: a
        nudge the save carried keeps a single target's id. Drawn 21" north
        of its anchor, the target keys as the anchor, and the same block
        with no anchor keys where it is drawn (the control: S1's key).

        Mutant "a single target ignores its anchor" (the 1x1 key made with
        no anchor), observed:

            AssertionError: assert 'f96c804392b3...5965d0ae1d1c5' == '2a9a9a56e185...59b66876bd7ed'
              - 2a9a9a56e1855e709af59b66876bd7ed
              + f96c804392b357638315965d0ae1d1c5
        """
        s = _spec()
        anchor = identity.anchor_for({**M31}, s["ra_hours"], s["dec_deg"],
                                     30.0, canonical=None)
        nudged = {**M31, "dec": "+41 16 30"}
        at_anchor = self._id_of(identity.anchor_key(anchor))
        assert self._target(dec=nudged["dec"], frameAnchor=anchor).id == \
            at_anchor
        drawn = identity.target_key(nudged, s["ra_hours"],
                                    41 + 16 / 60 + 30 / 3600, 30.0,
                                    canonical=None)
        assert self._target(dec=nudged["dec"]).id == self._id_of(drawn)
        assert self._id_of(drawn) != at_anchor


# ================================================================ identity

class TestIdentity:
    def test_ids_are_stable_across_two_compiles(self):
        """Mutant "a fresh group id every compile" (the group id is a uuid4
        even with a flow id), observed (the digits are uuid4s, so they
        differ on every run):

            AssertionError: assert ['09faba0f030...f87c24cf4c29'] == ['cbe88f18de3...a7ded04ba8c8']
              At index 0 diff: '09faba0f030550dd8c5dbed41cb7804a' != 'cbe88f18de3259c3aa8f354457533f85'
        """
        one, _ = _plan(_mosaic())
        two, _ = _plan(_mosaic())
        assert [t.id for t in one.targets] == [t.id for t in two.targets]
        assert [[s.id for s in t.steps] for t in one.targets] == \
            [[s.id for s in t.steps] for t in two.targets]
        gid = identity.group_id(FLOW_ID, "t", _key())
        assert [t.id for t in one.targets] == [
            identity.target_id(gid, t.panel_row, t.panel_col)
            for t in one.targets]

    def test_a_skip_moves_one_id_and_no_other(self):
        """`skip` is in no key (spec 3.3), so skipping 2-2 moves exactly that
        panel's id into `skipped_ids` and leaves the group's id and the other
        three panels' ids, and their steps' ids, where they were.

        Mutant "skip in the key" (the block key made from the skip list as
        well), observed:

            AssertionError: assert {'M31 1-1': '...498524dae238'} == {'M31 1-1': '...775bce3feb24'}
              Differing items:
              {'M31 1-2': '048bdd7513b650fcb2c67f586229c599'} != {'M31 1-2': 'bb8cc2dd62925afeb83cedfca07f5e2a'}
              {'M31 2-1': '2b67339367745cbc9175498524dae238'} != {'M31 2-1': 'e7555aa850c153509eb2775bce3feb24'}
              {'M31 1-1': '6145d2e4a4b757baa38e35b239ed4e4d'} != {'M31 1-1': '6cc9815073ad5e62a16cc548b1bae2f5'}
        """
        whole, _ = _plan(_mosaic())
        cut, _ = _plan(_mosaic(skip="2-2"))
        before = {t.name: t.id for t in whole.targets}
        after = {t.name: t.id for t in cut.targets}
        assert set(before) - set(after) == {"M31 2-2"}
        assert after == {k: v for k, v in before.items() if k != "M31 2-2"}
        assert cut.groups[0].id == whole.groups[0].id
        assert cut.groups[0].skipped_ids == [before["M31 2-2"]]
        steps = {t.name: [s.id for s in t.steps] for t in whole.targets}
        assert all(steps[t.name] == [s.id for s in t.steps]
                   for t in cut.targets)

    def test_the_anchor_keys_the_block_not_its_current_geometry(self):
        """Ruling 3: with a stored anchor the ids hang off it, so a nudge the
        save carried keeps them. Here the block is drawn 0.1 deg off its
        anchor and keys exactly as its anchor does.

        Mutant "the anchor is ignored" (`_block_key` passes no anchor),
        observed:

            AssertionError: assert '6606c86b27b2...f77622e427c8a' == '86da8131b37d...ed01a83a5dc47'
        """
        s = _spec()
        anchor = identity.anchor_for(
            {**M31}, s["ra_hours"], s["dec_deg"], 30.0, canonical=None,
            rows=2, cols=2, overlap=0.25, fov_x=2.0, fov_y=1.33)
        plan, _ = _plan(_mosaic(dec="+41 22 09", frameAnchor=anchor))
        assert plan.groups[0].id == identity.group_id(
            FLOW_ID, "t", identity.anchor_key(anchor))
        assert plan.groups[0].id == identity.group_id(FLOW_ID, "t", _key())

    def test_an_unreadable_anchor_refuses_rather_than_guesses(self):
        """Mutant "the anchor is ignored", observed:

            Failed: DID NOT RAISE <class 'astrodeck.flows.to_plan.GraphNotRunnable'>
        """
        with pytest.raises(GraphNotRunnable, match="frame anchor"):
            _plan(_mosaic(frameAnchor="{not json"))

    def test_with_no_flow_id_the_ids_are_fresh_but_agree(self):
        """An unsaved preview: a fresh group id per compile, and the panels
        keyed off it, so the plan is still coherent.

        Mutant "1-based panel rows", observed (the ids hang off a uuid4, so
        the digits differ on every run):

            AssertionError: assert ['ad02aea9376...755ea0254013'] == ['a4722f3b3eb...fc168483815a']
        """
        one, _ = _plan(_mosaic(skip="1-1"), flow_id="")
        two, _ = _plan(_mosaic(skip="1-1"), flow_id="")
        assert one.groups[0].id != two.groups[0].id
        gid = one.groups[0].id
        assert [t.id for t in one.targets] == [
            identity.target_id(gid, t.panel_row, t.panel_col)
            for t in one.targets]
        assert one.groups[0].skipped_ids == [identity.target_id(gid, 0, 0)]
        assert plan_identity_errors(one) == []

    def test_every_panel_skipped_is_no_group_at_all(self):
        """A group of no members is refused at every start path; a block
        that shoots nothing is left out, with a warn that says so.

        Mutant "an all-skipped block is an empty group", observed:

            AssertionError: assert ([TargetGroup(...c76c28239d'})] == []
        """
        g = _mosaic(skip="1-1, 1-2, 2-1, 2-2",
                    after=[_n("t2", "target", x=400, **M33),
                           _n("k", "capture", x=500, exposure=60, count=5,
                              goal=0)])
        plan, un = _plan(g)
        assert plan.groups == [] and [t.name for t in plan.targets] == ["M33"]
        assert "every panel of TARGET M31 is skipped" in _texts(un)


# ================================================================== the mode

class TestMode:
    def test_the_loop_wire_rotates_and_its_absence_is_panel_first(self):
        """Spec 1.4 item 1. Deleting the wire changes only the mode, never
        which stages the panels own.

        Mutant "always rotate" (``mode`` is "rotate" whatever `loop` says),
        observed:

            AssertionError: assert 'rotate' == 'sequential'
        """
        looped, _ = _plan(_mosaic())
        plain, _ = _plan(_mosaic(loop=False))
        assert looped.groups[0].mode == "rotate"
        assert plain.groups[0].mode == "sequential"
        assert [[s.id for s in t.steps] for t in looped.targets] == \
            [[s.id for s in t.steps] for t in plain.targets]

    def test_a_capture_only_lane_cycles_only_inside_the_circle(self):
        """1.3 items 4 and 5: the loop wire marks every panel `cycle`, with
        one frame per visit; without it a capture lane shoots in blocks.

        Mutant "acquisition only from a cycle step" (`loop` ignored),
        observed:

            AssertionError: assert {'blocks'} == {'cycle'}
              Extra items in the left set:
              'blocks'
              Extra items in the right set:
              'cycle'
        """
        looped, _ = _plan(_mosaic(stages=("capture",)))
        plain, _ = _plan(_mosaic(stages=("capture",), loop=False))
        assert {t.acquisition for t in looped.targets} == {"cycle"}
        assert {s.per_visit for t in looped.targets for s in t.steps} == {1}
        assert {s.count for t in looped.targets for s in t.steps} == {5}
        assert {t.acquisition for t in plain.targets} == {"blocks"}

    def test_a_filter_cycle_cycles_without_the_loop_wire(self):
        """Spec 3.3: `cycle` when any step cycles OR the loop wire rotates.
        A panel-first mosaic of a FILTER CYCLE still rotates its filters
        within each panel, as a single target with a FILTER CYCLE does; in
        blocks it would shoot every L before the first R. (Added by the
        S3-CP verifier: the case above only reaches the loop half.)

        Mutant "acquisition only from the loop wire" (the cycle steps
        ignored), observed:

            AssertionError: assert {'blocks'} == {'cycle'}
              Extra items in the left set:
              'blocks'
              Extra items in the right set:
              'cycle'
        """
        plain, _ = _plan(_mosaic(loop=False))
        assert plain.groups[0].mode == "sequential"
        assert {t.acquisition for t in plain.targets} == {"cycle"}


# ============================================================== followers

class TestFollowers:
    def _flow(self, **settings):
        return _mosaic(settings=settings, after=[
            _n("t2", "target", x=400, **M33),
            _n("k", "capture", x=500, exposure=60, count=5, goal=0),
            _n("p", "pool", x=600, members="M16, M17")])

    def test_wait_for_the_mosaic_gates_every_follower(self):
        """Spec 1.6: under "Wait for the mosaic" a follower waits for the
        group (`after_group`), a pool's members included.

        Mutant "followers never wait" (`after_group` is never set),
        observed:

            AssertionError: assert {None} == {'86da8131b37...d01a83a5dc47'}
        """
        plan, _ = _plan(self._flow(whenWaiting=WAIT_FOR_THE_MOSAIC))
        gid = plan.groups[0].id
        after = {t.name: t.after_group for t in plan.targets}
        assert {after["M33"], after["M16"], after["M17"]} == {gid}
        assert {after[n] for n in after if n.startswith("M31 ")} == {None}
        assert plan_identity_errors(plan) == []

    def test_the_default_gates_nobody(self):
        """Under the default the engine lets followers fill the mosaic's
        gaps, from plan order, so no field is set.

        Mutant "followers always wait" (the `when_waiting` test dropped),
        observed:

            AssertionError: assert {None, '86da8...d01a83a5dc47'} == {None}
              Extra items in the left set:
              '86da8131b37d59b398ded01a83a5dc47'
        """
        plan, _ = _plan(self._flow())
        assert {t.after_group for t in plan.targets} == {None}
        # Plan order is what the engine reads a follower from: after the
        # group's last member.
        names = [t.name for t in plan.targets]
        assert names.index("M33") > max(
            i for i, n in enumerate(names) if n.startswith("M31 "))

    def test_the_setting_is_one_of_the_flows_declared_options(self):
        """The words `to_plan` matches are the flow's own declared option,
        not a copy that can drift from `FLOW_SETTINGS`.

        Mutant "the waiting words misspelt" (``"Wait for mosaic"``),
        observed:

            AssertionError: assert 'Wait for mosaic' in ['Shoot later targets, then come back', 'Wait for the mosaic']
        """
        assert WAIT_FOR_THE_MOSAIC in FLOW_SETTINGS["whenWaiting"]["options"]
        assert WAIT_FOR_THE_MOSAIC != FLOW_SETTINGS["whenWaiting"]["default"]


# ============================================================= count mode

V3_FLOW = {
    "schema_version": 3,
    "id": "v3-no-counts",
    "flow": {
        "id": "v3-no-counts", "name": "saved on S2", "folder": "My flows",
        "graph": {
            "nodes": [
                {"id": "t", "type": "target", "x": 0, "y": 0,
                 "params": {"name": "M31", "ra": "00h 42m 44s",
                            "dec": "+41 16 09", "rotation": -1}},
                {"id": "c", "type": "capture", "x": 100, "y": 0,
                 "params": {"filter": "L", "exposure": 60, "gain": 100,
                            "bin": "1", "count": 5, "goal": 0}}],
            "edges": [{"from": "t", "fromPort": "target", "to": "c",
                       "toPort": "run"}]}}}


class TestCountMode:
    def test_a_v3_flow_with_no_counts_key_compiles_to_attempts(self, tmp_path):
        """RULING 2: only a SAVE switches a block to accepted subs. A v3 file
        written before `counts` existed, loaded and compiled with nobody
        saving it, counts every sub taken, as it did when it was saved.

        Mutant "missing-key Accepted" (``nodes.py``: TARGET's missing-key
        `counts` default "Accepted subs"), observed:

            AssertionError: assert 'accepted' == 'attempts'
        """
        store = FlowStore(tmp_path)
        (tmp_path / "v3-no-counts.json").write_text(json.dumps(V3_FLOW),
                                                   encoding="utf-8")
        rec = store.get("v3-no-counts")
        assert "counts" not in next(n for n in rec.graph.nodes
                                    if n.type == "target").params
        plan, _ = to_sequence_plan(compile_plan(rec.graph, rec.name),
                                   rec.graph, flow_id=rec.id)
        assert plan.count_mode == "attempts"

    def test_any_block_asking_for_accepted_subs_decides(self):
        """Mutant "count_mode always attempts" (``compile.py``), observed:

            AssertionError: assert 'attempts' == 'accepted'
        """
        g = _mosaic(counts="Accepted subs")
        plan, un = _plan(g)
        assert plan.count_mode == "accepted"
        assert not any(u["key"] == "count_mode" for u in un)

    def test_two_blocks_that_disagree_are_m7_a_loss(self):
        """count_mode is one plan-wide setting, so one of the two cannot be
        honoured, and the run must not start as if it could.

        Mutant "no M7" (`_count_mode` never appends its note), observed:

            StopIteration
        """
        g = _mosaic(counts="Accepted subs", after=[
            _n("t2", "target", x=400, counts="Every sub taken", **M33),
            _n("k", "capture", x=500, exposure=60, count=5, goal=0)])
        plan, un = _plan(g)
        assert plan.count_mode == "accepted"
        row = next(u for u in un if u["key"] == "count_mode")
        assert row in losses(un)
        assert row["detail"] == (
            "this plan counts accepted subs because TARGET M31 asks for it; "
            "TARGET M33's 'every sub taken' cannot be honoured in the same "
            "run")

    def test_a_block_that_shoots_nothing_asks_for_nothing(self):
        """The count mode is settled by the blocks that made it into the
        plan. A mosaic whose every panel is skipped is left out, so its
        "accepted subs" cannot decide how M33 is counted, and M33's "every
        sub taken" is no disagreement with a block that is not there. (Added
        by the S3-CP verifier.)

        Mutant "the count mode counts dropped blocks" (`_count_mode` reads
        an entry that built no target), observed:

            AssertionError: assert 'accepted' == 'attempts'
        """
        g = _mosaic(counts="Accepted subs", skip="1-1, 1-2, 2-1, 2-2",
                    after=[_n("t2", "target", x=400, counts="Every sub taken",
                              **M33),
                           _n("k", "capture", x=500, exposure=60, count=5,
                              goal=0)])
        plan, un = _plan(g)
        assert [t.name for t in plan.targets] == ["M33"]
        assert plan.count_mode == "attempts"
        assert not any(u["key"] == "count_mode" for u in un)


# =============================================================== centring

class TestCentring:
    def test_the_targets_centring_reaches_the_run(self):
        """#170: `centerTol` 0.5 is the target's `center_tolerance_arcmin`.

        Mutant "centring never reaches the Target" (`_centring` reads no
        ``centre`` and returns ``{}``), observed:

            AssertionError: assert None == 0.5
        """
        plan, _ = _plan(_mosaic(rows=1, cols=1, loop=False, centerTol=0.5))
        assert plan.targets[0].center_tolerance_arcmin == 0.5

    def test_a_legacy_slews_tolerance_does_not(self):
        """Spec 1.7: a SLEW's `tol` 0.5 never reached a run, and carrying it
        would tighten every saved flow's centring. The target centres to the
        TARGET's 1.2.

        Mutant "carry the SLEW tol" (``compile.py``: the TARGET's
        `tol_arcmin` taken from a SLEW node in the graph), observed:

            AssertionError: assert 0.5 == 1.2
        """
        g = FlowGraph(
            nodes=[_n("t", "target", **M31), _n("sl", "slew", x=100, tol=0.5),
                   _n("c", "capture", x=200, exposure=60, count=5, goal=0)],
            edges=[_e("t", "target", "sl", "run"),
                   _e("sl", "centered", "c", "run")])
        plan, _ = _plan(g)
        assert plan.targets[0].center_tolerance_arcmin == 1.2

    @pytest.mark.parametrize("tol,tries", [(0, 3), (-1, 3), (31, 3),
                                           (1.2, 0), (1.2, 11), (1.2, 2.5)])
    def test_a_centring_the_run_cannot_use_is_refused(self, tol, tries):
        """Target's own bounds, checked where the editor can show them: a
        ValidationError out of /run would be a 500.

        Mutant "centring never reaches the Target", observed for all six:

            Failed: DID NOT RAISE <class 'astrodeck.flows.to_plan.GraphNotRunnable'>
        """
        g = _mosaic(rows=1, cols=1, loop=False, centerTol=tol,
                    centerTries=tries)
        with pytest.raises(GraphNotRunnable, match="centring"):
            _plan(g)


# ========================================================== the loop wire

class TestTheLoopWireIsNotARule:
    def test_the_loop_wire_emits_no_instruction(self):
        """Mutant "emit it" (``compile.py`` no longer skips the loop wire):
        the compiled rule appears (``test_flows_compile_entry_s3.py``
        observes it), `to_plan` keeps it out of the plan as no engine
        trigger, and with it comes a 'will not run' line, observed:

            AssertionError: assert ['instruction...s -> target]'] == []
              Left contains one more item: 'instructions[cycle.pass -> target]'
        """
        plan, un = _plan(_mosaic())
        assert plan.instructions == []
        assert [u["key"] for u in un if "will not run" in u["detail"]] == []

    def test_any_other_pass_wire_reads_will_not_run(self):
        """1.3 item 1: a pass wire anywhere else is an illegal trigger, a
        warn the run holds for. Here, a loop wire on a block of one panel,
        which has nothing to rotate between.

        Mutant "every pass wire consumed silently" (`_instructions` skips
        any ``.pass`` trigger), observed:

            AssertionError: assert [] == ['instruction...s -> target]']
              Right contains one more item: 'instructions[cycle.pass -> target]'
        """
        g = _mosaic(rows=1, cols=1)
        plan, un = _plan(g)
        rows = [u for u in un if "will not run" in u["detail"]]
        assert [u["key"] for u in rows] == ["instructions[cycle.pass -> target]"]
        assert rows[0] in losses(un)
        assert plan.instructions == []


# ================================================================ refusals

def _branched() -> FlowGraph:
    g = _mosaic(loop=False)
    return FlowGraph(nodes=[*g.nodes, _n("x", "capture", x=200, y=200,
                                         exposure=60, count=5, goal=0)],
                     edges=[*g.edges, _e("t", "target", "x", "run")])


def _mid_lane_loop() -> FlowGraph:
    g = _mosaic(stages=("cycle", "capture"), loop=False)
    return g.model_copy(update={"edges": [*g.edges,
                                          _e("s0", "pass", "t", "next")]})


def _orphan_stage() -> FlowGraph:
    g = _mosaic()
    return FlowGraph(
        nodes=[*g.nodes, _n("dm", "dome", x=400),
               _n("x", "capture", x=500, filter="Ha", exposure=60, count=5,
                  goal=0)],
        edges=[*g.edges, _e("s0", "complete", "dm", "run"),
               _e("dm", "open", "x", "run")])


class TestRefusals:
    @pytest.mark.parametrize("graph,words", [
        (lambda: _mosaic(fovX=0), "frame this block"),
        (lambda: _mosaic(fovY=0), "frame this block"),
        (lambda: _mosaic(angle="Any angle"), "with no angle the panels will "
                                              "not tile"),
        (lambda: _mosaic(rotation=-1, angle="Rotate to PA"),
         "with no angle the panels will not tile"),
        (_branched, "panel lane branches"),
        (_mid_lane_loop, "the loop wire starts at FILTER CYCLE, but CAPTURE "
                         "LOOP L comes after it"),
        (_orphan_stage, "CAPTURE LOOP Ha belongs to no TARGET: DOME CONTROL "
                        "ends the lane above it"),
    ], ids=["M1-fovX", "M1-fovY", "M2-any", "M2-no-pa", "M12-branch",
            "M12-mid-lane", "M13"])
    def test_a_mosaic_that_cannot_run_is_refused(self, graph, words):
        """M1, M2, M12 and M13 (spec 1.8) are not losses to accept: there is
        no plan here that shoots what the canvas draws.

        Mutant "drop the M1 refusal", observed (M1 cases; the projection's
        own ValidationError escapes, which /run would answer with a 500):

            pydantic_core._pydantic_core.ValidationError: 1 validation error for MosaicSpecIn
            fov_x_deg
              Input should be greater than 0 [type=greater_than, input_value=0.0, input_type=float]

        Mutant "drop the M2 refusal", observed (M2 cases):

            pydantic_core._pydantic_core.ValidationError: 1 validation error for MosaicSpecIn
            rotation_deg
              Input should be a valid number [type=float_type, input_value=None, input_type=NoneType]

        Mutant "drop the M12 refusal", observed (both M12 cases):

            Failed: DID NOT RAISE <class 'astrodeck.flows.to_plan.GraphNotRunnable'>

        Mutant "drop the M13 refusal", observed (M13 case):

            Failed: DID NOT RAISE <class 'astrodeck.flows.to_plan.GraphNotRunnable'>
        """
        with pytest.raises(GraphNotRunnable) as err:
            _plan(graph())
        assert words in str(err.value), str(err.value)

    def test_every_refusal_is_said_at_once(self):
        """A client that fixes one and is told about the next makes N round
        trips; all of a graph's refusals come back together.

        Mutant "only the first refusal", observed:

            AssertionError: CAPTURE LOOP Ha belongs to no TARGET: DOME CONTROL ends the lane above it. In a flow with a mosaic a stage is shot only on the block its wires lead back to, so it would shoot nothing. Give it a TARGET.
        """
        g = _orphan_stage()
        g.node("t").params.update(fovX=0, angle="Any angle")
        with pytest.raises(GraphNotRunnable) as err:
            _plan(g)
        for words in ("frame this block", "with no angle",
                      "belongs to no TARGET"):
            assert words in str(err.value), str(err.value)

    def test_controls_the_same_shapes_framed_and_chained_run(self):
        """Each refused shape, repaired, compiles: the refusals watch the
        fault and nothing near it.

        Mutant "the tail's own loop wire is M12" (`lane_refusals` no longer
        exempts the tail), observed:

            astrodeck.flows.to_plan.GraphNotRunnable: the loop wire starts at FILTER CYCLE, but  come after it in the TARGET M31 panel lane. Start the loop wire at FILTER CYCLE to shoot every stage on every panel, or give  a TARGET of their own.
        """
        for g in (_mosaic(), _mosaic(stages=("cycle", "capture"))):
            plan, _ = _plan(g)
            assert plan.groups and plan_identity_errors(plan) == []

    def test_a_graph_with_no_mosaic_is_never_refused_for_its_lane(self):
        """M13 is a mosaic graph's rule: under canvas order the same DOME
        -> CAPTURE is appended to every earlier target, as it always was.

        Mutant "lane refusals in every graph" (`lane_refusals` does not
        return early without a mosaic), observed:

            astrodeck.flows.to_plan.GraphNotRunnable: CAPTURE LOOP Ha belongs to no TARGET: DOME CONTROL ends the lane above it. In a flow with a mosaic a stage is shot only on the block its wires lead back to, so it would shoot nothing. Give it a TARGET.
        """
        g = _orphan_stage()
        g.node("t").params.update(rows=1, cols=1)
        plan, _ = _plan(g)
        assert [t.name for t in plan.targets] == ["M31"]


class TestTheGridIsBounded:
    """A grid number the group or the projection cannot hold is refused
    naming the block and the field (`_mosaic_numbers`), where the editor
    shows it as the plan's danger row and /run answers 422. Unchecked, the
    projection's or the group's own ValidationError escapes instead, which
    both routes would answer with a 500. (Added by the S3-CP verifier: no
    test reached these refusals.)"""

    @pytest.mark.parametrize("field,value,words", [
        ("rows", 11, "a grid of rows of 11 cannot be used"),
        ("overlap", 60, "an overlap (percent) of 60 cannot be used"),
        ("overlap", -5, "an overlap (percent) of -5 cannot be used"),
        ("passes", 0, "a number of passes per visit of 0 cannot be used"),
        ("passes", 21, "a number of passes per visit of 21 cannot be used"),
        ("minVisit", 181, "a visit (minutes) of 181 cannot be used"),
        ("minVisit", -1, "a visit (minutes) of -1 cannot be used"),
    ])
    def test_a_number_out_of_range_is_refused(self, field, value, words):
        """Mutant "no grid bounds" (only an unreadable value is refused),
        observed for all seven, the projection's or the group's own error
        escaping, for rows 11 and passes 21:

            pydantic_core._pydantic_core.ValidationError: 1 validation error for MosaicSpecIn
            rows
              Input should be less than or equal to 10 [type=less_than_equal, input_value=11, input_type=int]

            pydantic_core._pydantic_core.ValidationError: 1 validation error for SequencePlan
            groups.0.visit_passes
              Input should be less than or equal to 20 [type=less_than_equal, input_value=21, input_type=int]
        """
        with pytest.raises(GraphNotRunnable) as err:
            _plan(_mosaic(**{field: value}))
        assert f"TARGET M31: {words}" in str(err.value), str(err.value)

    @pytest.mark.parametrize("field,value,words", [
        ("passes", 2.5, "a number of passes per visit of 2.5 cannot be used "
                        "- it must be a whole number from 1 to 20"),
        ("overlap", "lots", "an overlap (percent) of None cannot be used"),
        ("minVisit", "soon", "a visit (minutes) of None cannot be used"),
    ])
    def test_a_number_that_is_not_one_is_refused(self, field, value, words):
        """Mutant "a grid number is used as it comes" (no check at all),
        observed for all three (the seven cases above went red with it too):

            pydantic_core._pydantic_core.ValidationError: 1 validation error for SequencePlan
            groups.0.visit_passes
              Input should be a valid integer, got a number with a fractional part [type=int_from_float, input_value=2.5, input_type=float]

            TypeError: unsupported operand type(s) for /: 'NoneType' and 'float'

            TypeError: unsupported operand type(s) for *: 'NoneType' and 'float'
        """
        with pytest.raises(GraphNotRunnable) as err:
            _plan(_mosaic(**{field: value}))
        assert words in str(err.value), str(err.value)

    def test_controls_every_bound_itself_is_accepted(self):
        """The refusals watch the range and nothing inside it: both ends of
        every bound compile to a group.

        Mutant "the bounds are exclusive" (``low < value < high``),
        observed:

            astrodeck.flows.to_plan.GraphNotRunnable: TARGET M31: a grid of rows of 10 cannot be used - it must be a whole number from 1 to 10
        """
        top, _ = _plan(_mosaic(rows=10, cols=1, overlap=50, passes=20,
                               minVisit=180))
        assert (len(top.targets), top.groups[0].visit_passes,
                top.groups[0].visit_min_s) == (10, 20, 10800.0)
        low, _ = _plan(_mosaic(overlap=0, passes=1, minVisit=0))
        assert (low.groups[0].geometry["overlap"], low.groups[0].visit_passes,
                low.groups[0].visit_min_s) == (0.0, 1, 0.0)


# ===================================================================== M5

class TestM5:
    def test_a_live_field_smaller_than_the_step_is_a_loss(self):
        """Spec 1.8 M5: framed for 2.00 x 1.33 deg at 25% overlap, a step of
        1.50 x 0.9975 deg; a camera that now images 1.40 x 0.93 deg leaves
        gaps. A loss: /run holds until it is accepted.

        Mutant "M5 is a note" (the row written at ``note``), observed:

            AssertionError: assert {'detail': 'framed for 2.00 x 1.33 deg; this camera now images 1.40 x 0.93 deg, so the panels would leave gaps. Re-frame.', 'key': 'targets[M31].mosaic.fov', 'level': 'note'} in []
        """
        _plan_, un = _plan(_mosaic(), rig=RigFacts(fov_deg=(1.40, 0.93)))
        row = next(u for u in un if u["key"] == "targets[M31].mosaic.fov")
        assert row in losses(un)
        assert row["detail"] == (
            "framed for 2.00 x 1.33 deg; this camera now images 1.40 x 0.93 "
            "deg, so the panels would leave gaps. Re-frame.")

    @pytest.mark.parametrize("rig", [
        None, RigFacts(), RigFacts(fov_deg=(1.6, 1.07)),
        RigFacts(fov_deg=(2.0, 1.33))],
        ids=["no-rig", "nothing-known", "smaller-but-covers-the-step", "same"])
    def test_m5_only_with_a_field_that_leaves_gaps(self, rig):
        """Controls: no rig facts, facts with no field, and a field smaller
        than the snapshot but still larger than the step, give no M5.

        Mutant "compare with the whole field, not the step" (the test reads
        ``live < fov``), observed on smaller-but-covers-the-step:

            AssertionError: assert ['targets[M31].mosaic.fov'] == []
              Left contains one more item: 'targets[M31].mosaic.fov'

        Mutant "M5 whenever the facts are given" (the size test dropped),
        observed on smaller-but-covers-the-step and on same, as above.

        Mutant "M5 without asking whether the rig is known" (the rig and its
        field are not checked for None), observed on no-rig:

            AttributeError: 'NoneType' object has no attribute 'fov_deg'
        """
        _plan_, un = _plan(_mosaic(), rig=rig)
        assert [u["key"] for u in un if u["key"].endswith(".mosaic.fov")] == []

    @pytest.mark.parametrize("live", [(1.40, 1.07), (1.6, 0.93)],
                             ids=["only-x-short", "only-y-short"])
    def test_one_short_axis_is_enough(self, live):
        """M5 is "on either axis" (spec 1.8): the step is 1.50 x 0.9975 deg,
        and a camera short on one axis alone leaves a gap along that axis.
        The case above is short on both, so it cannot tell "either" from
        "both". (Added by the S3-CP verifier.)

        Mutant "M5 only when both axes are short" (``and`` for ``or``),
        observed on both:

            AssertionError: assert [] == ['targets[M31].mosaic.fov']
              Right contains one more item: 'targets[M31].mosaic.fov'
        """
        _plan_, un = _plan(_mosaic(), rig=RigFacts(fov_deg=live))
        assert [u["key"] for u in losses(un)
                if u["key"].endswith(".mosaic.fov")] == \
            ["targets[M31].mosaic.fov"]


# ====================================================== identity errors

def _shapes():
    yield "2x2 rotating", _mosaic()
    yield "3x2 panel-first, skip", _mosaic(rows=3, cols=2, loop=False,
                                           skip="3-1")
    yield "capture lane", _mosaic(stages=("cycle", "capture"))
    yield "wait, with followers", _mosaic(
        settings={"whenWaiting": WAIT_FOR_THE_MOSAIC},
        after=[_n("t2", "target", x=400, **M33),
               _n("k", "capture", x=500, exposure=60, count=5, goal=0),
               _n("p", "pool", x=600, members="M16, M17")])
    b = {**M33, **FIELD, "rotation": 10, "angle": "Camera fixed at PA",
         "rows": 2, "cols": 3}
    g = _mosaic(after=[_n("b", "target", x=400, **b), _n("bc", "cycle", x=500)],
                settings={"whenWaiting": WAIT_FOR_THE_MOSAIC})
    yield "two mosaics", g.model_copy(update={"edges": [
        *g.edges, _e("bc", "pass", "b", "next")]})
    yield "name-keyed", _mosaic(ra="", dec="")


@pytest.mark.parametrize("flow_id", [FLOW_ID, ""])
@pytest.mark.parametrize("label,graph", list(_shapes()),
                         ids=[s[0] for s in _shapes()])
def test_every_compiled_mosaic_passes_the_identity_checks(label, graph,
                                                          flow_id):
    """`plan_identity_errors` is asked on all five start paths (spec 3.5): a
    compile that produced a plan it refuses would be a flow that can never
    run. Every group has members, no id repeats, no panel is calibration,
    every `after_group` names a group, and no two targets share a name.

    Mutant "panels named after the block" (every panel named "<name>"),
    observed:

        AssertionError: 2x2 rotating
        assert ["target name...l them apart"] == []
          Left contains one more item: "target name 'M31' is used by 4 targets and the plan carries groups, so a panel label cannot tell them apart"
    """
    plan, _ = _plan(graph, flow_id=flow_id)
    assert plan.groups
    assert plan_identity_errors(plan) == [], label

