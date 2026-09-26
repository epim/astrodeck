"""The TARGET block's compile entry (spec 3.2; #189 U-09, #170, #151).

A TARGET node still compiles to ONE entry, so the PLAN tab shows one block as
one mosaic, and `to_plan` expands it into panels and a group
(`test_flows_to_plan_mosaic.py`). What the entry gains is what the block
means: its angle, its grid, whether the loop wire rotates its panels, its
centring, and what it counts. Each is read from the node's own params and
nothing else, because `compile_plan` is pure and the PLAN tab renders it
verbatim.

The cases that matter most are the ones that keep an OLD graph meaning what
it meant. A block saved before `angle` existed must keep commanding its PA
(ruling 9), and one saved before `counts` existed must keep counting every sub
(ruling 2); a missing-key default that flipped either would change a saved
night without anybody touching it (the "semantics flip needs a migration"
class). The mutants recorded below are run from a byte backup in a private
copy of server/ (scratchpad s3-cp-mut), never in the shared tree.
"""
from __future__ import annotations

import pytest

from astrodeck.flows.compile import compile_plan, parse_skip
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode

M31 = {"name": "M31", "ra": "00h 42m 44s", "dec": "+41 16 09"}
M33 = {"name": "M33", "ra": "01h 33m 51s", "dec": "+30 39 37"}
#: A camera field, so a grid is framed (M1 is `to_plan`'s refusal, not this).
FIELD = {"fovX": 2.0, "fovY": 1.33}


def _n(nid, ntype, x=0.0, y=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=y, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def _block(*, rows=3, cols=2, loop=True, stages=("cycle",), settings=None,
           after=(), **target) -> FlowGraph:
    """DUSK -> TARGET -> the stages in a chain -> whatever ``after`` names.

    ``loop`` wires the last stage's ``pass`` into the TARGET's ``next``, the
    loop wire as the editor draws it. ``after`` is a list of extra nodes the
    last stage's ``complete`` feeds, in a chain."""
    params = {**M31, **FIELD, "rotation": 30, "angle": "Rotate to PA",
              "rows": rows, "cols": cols, **target}
    nodes = [_n("d", "dusk"), _n("t", "target", x=100, **params)]
    edges = [_e("d", "window", "t", "arm")]
    prev, port = "t", "target"
    for i, kind in enumerate(stages):
        sid = f"s{i}"
        extra = {"exposure": 60, "count": 5} if kind == "capture" else {}
        nodes.append(_n(sid, kind, x=200 + 100 * i, **extra))
        edges.append(_e(prev, port, sid, "run"))
        prev, port = sid, "complete"
    if loop and stages:
        edges.append(_e(prev, "pass", "t", "next"))
    for node in after:
        nodes.append(node)
        in_port = "session" if node.type == "report" else (
            "run" if node.type in ("capture", "cycle", "dome") else "arm")
        edges.append(_e(prev, port, node.id, in_port))
        prev, port = node.id, ("target" if node.type in ("target", "pool")
                               else "complete" if node.type in
                               ("capture", "cycle") else "open")
    return FlowGraph(nodes=nodes, edges=edges, settings=settings or {})


def _entry(graph: FlowGraph, node_id: str = "t") -> dict:
    return next(t for t in compile_plan(graph, "n")["targets"]
                if t["node_id"] == node_id)


# ------------------------------------------------------------------ the angle

class TestTheAngle:
    @pytest.mark.parametrize("stored,code", [
        ("Any angle", "any"), ("Rotate to PA", "rotate"),
        ("Camera fixed at PA", "fixed")])
    def test_a_stored_angle_is_the_blocks(self, stored, code):
        """Mutant "the angle is always derived" (`angle_code` never reads the
        stored `angle`), observed:

            AssertionError: assert 'rotate' == 'any'
            AssertionError: assert 'rotate' == 'fixed'
        """
        g = _block(rows=1, cols=1, loop=False, angle=stored, rotation=30)
        assert _entry(g)["angle"] == code

    @pytest.mark.parametrize("rotation,code", [(-1, "any"), (0, "rotate"),
                                               (23.4, "rotate")])
    def test_with_no_angle_key_the_rotation_decides(self, rotation, code):
        """RULING 9: a block saved before `angle` existed keeps commanding the
        PA it always commanded, so the M31 Example stays at Rotate to PA 23.4,
        and 0 is north up, a real angle (#150).

        Mutant "a missing angle reads Any angle" (`angle_code` reads a node
        with no `angle` as "Any angle"), observed for rotation 0 and 23.4:

            AssertionError: assert 'any' == 'rotate'
        """
        g = _block(rows=1, cols=1, loop=False, rotation=rotation)
        node = g.node("t")
        node.params.pop("angle")
        assert _entry(g)["angle"] == code

    def test_words_this_build_does_not_offer_read_as_the_rotation(self):
        """A newer build's fourth choice, or a hand edit: guessing any of the
        three from unknown words would be inventing, so the rotation alone
        decides, as for a block with no angle at all.

        Mutant "unknown angle words read Any angle", observed:

            AssertionError: assert 'any' == 'rotate'
        """
        g = _block(rows=1, cols=1, loop=False, angle="Upside down", rotation=12)
        assert _entry(g)["angle"] == "rotate"
        g = _block(rows=1, cols=1, loop=False, angle="Upside down", rotation=-1)
        assert _entry(g)["angle"] == "any"


# ----------------------------------------------------------------- the grid

class TestTheGrid:
    def test_a_single_panel_block_has_no_mosaic(self):
        """Spec 3.2: `mosaic` is null when rows x cols is 1. The anchor rides
        on the entry instead (spec 3.3), so a nudge the save carried keeps a
        single target's id too.

        Mutant "every block is a group" (the grid branch taken for 1x1),
        observed:

            AssertionError: assert ({'cols': 1, 'fov_from': '', 'fov_x': 2, 'fov_y': 1.33, ...} is None)
        """
        g = _block(rows=1, cols=1, loop=False, frameAnchor="{}", skip="1-1")
        entry = _entry(g)
        assert entry["mosaic"] is None and entry["loop"] is False
        assert entry["frame_anchor"] == "{}"

    def test_a_grid_carries_every_key_of_spec_3_2(self):
        """Every key, from the node's own params. `overlap` stays the percent
        the block holds (`to_plan` divides it by 100), `order` stays the
        operator's words (`to_plan` maps them), and `skip` is parsed to
        1-based panels in grid order.

        Mutant "skip read 0-based" (`parse_skip` stores ``[r - 1, c - 1]``),
        observed:

            AssertionError: assert {'cols': 2, '...y': 1.33, ...} == {'cols': 2, '...y': 1.33, ...}
              Omitting 12 identical items, use -vv to show
              Differing items:
              {'skip': [[0, 1], [2, 0]]} != {'skip': [[1, 2], [3, 1]]}
        """
        g = _block(overlap=35, fovFrom="profile Refractor", skip="3-1, 1-2",
                   order="Grid order", passes=2, minVisit=10,
                   ifNotCentred="Skip it this pass", frameAnchor="{\"x\":1}")
        assert _entry(g)["mosaic"] == {
            "rows": 3, "cols": 2, "overlap": 35, "fov_x": 2.0, "fov_y": 1.33,
            "fov_from": "profile Refractor", "skip": [[1, 2], [3, 1]],
            "order": "Grid order", "passes": 2, "visit_min": 10,
            "require_centred": True,
            "when_waiting": "Shoot later targets, then come back",
            "frame_anchor": "{\"x\":1}"}

    @pytest.mark.parametrize("choice,required", [
        ("Auto", True), ("Skip it this pass", True), ("Shoot anyway", False)])
    def test_only_shoot_anyway_lets_a_panel_off_its_tile(self, choice,
                                                         required):
        """Auto means skip for a mosaic panel (spec 2.4 CENTRING).

        Mutant "only Skip it this pass keeps a panel on its tile"
        (`require_centred` is ``ifNotCentred == "Skip it this pass"``),
        observed for Auto:

            assert False is True
        """
        g = _block(ifNotCentred=choice)
        assert _entry(g)["mosaic"]["require_centred"] is required

    @pytest.mark.parametrize("stored,read", [
        (None, "Shoot later targets, then come back"),
        ("Wait for the mosaic", "Wait for the mosaic"),
        ("Hold everything", "Shoot later targets, then come back")])
    def test_when_waiting_is_the_flows_setting(self, stored, read):
        """One answer per FLOW (Revision 2 ruling 1), copied onto the block
        through `FlowGraph.setting`, so a flow saved without it reads the
        owner's default and a value this build does not know does too.

        Mutant "when_waiting is always the default" (the compile writes the
        default's words whatever the flow says), observed:

            AssertionError: assert 'Shoot later ...hen come back' == 'Wait for the mosaic'
        """
        settings = {} if stored is None else {"whenWaiting": stored}
        assert _entry(_block(settings=settings))["mosaic"]["when_waiting"] \
            == read

    def test_a_skip_that_names_no_panel_is_said_not_swallowed(self):
        """A skip that silently skipped nothing shoots a panel the operator
        took out. The panel it could not read is named in the compile's
        notes, and the ones it could read still count.

        Mutant "an unread skip is dropped in silence" (the note is not
        appended), observed:

            AssertionError: assert [] == ['t']
              Right contains one more item: 't'
        """
        g = _block(skip="1-2, 4-1, 2 3, 0-1")
        compiled = compile_plan(g, "n")
        entry = next(t for t in compiled["targets"] if t["node_id"] == "t")
        assert entry["mosaic"]["skip"] == [[1, 2]]
        warned = [n["node_id"] for n in compiled.get("notes", [])
                  if n["level"] == "warn"]
        assert warned == ["t"]
        text = next(n["text"] for n in compiled["notes"] if n["node_id"] == "t")
        for token in ("'4-1'", "'2 3'", "'0-1'"):
            assert token in text, text
        assert "'1-2'" not in text, text

    def test_the_skip_parser_dedupes_and_orders(self):
        """Mutant "skip read 0-based", observed:

            assert ([[0, 0], [0, 1], [1, 0]], []) == ([[1, 1], [1, 2], [2, 1]], [])
        """
        assert parse_skip("2-1; 1-1,2-1 , 1 - 2", 2, 2) == (
            [[1, 1], [1, 2], [2, 1]], [])
        assert parse_skip("", 3, 3) == ([], [])


# ------------------------------------------------------------------ the loop

class TestTheLoopWire:
    def _instructions(self, g):
        return compile_plan(g, "n")["instructions"]

    def test_the_legal_loop_wire_is_the_blocks_loop_and_not_a_rule(self):
        """Spec 1.4 items 1 and 3: `pass` from the tail of a multi-panel
        block's lane into its own `next` is `loop`, and it is consumed as
        structure, never emitted as an instruction.

        Mutant "emit it" (the instructions pass no longer skips the loop
        wire), observed:

            AssertionError: assert [{'action': '...'cycle.pass'}] == []
              Left contains one more item: {'action': 'target', 'to_port': 'next', 'when': 'cycle.pass'}
        """
        g = _block()
        assert _entry(g)["loop"] is True
        assert self._instructions(g) == []

    def test_two_copies_of_the_loop_wire_are_both_the_loop(self):
        """More than one pass wire into `next` "changes nothing" (1.4 item
        2): neither copy becomes a rule the run would call a loss.

        Mutant "consume the loop wire once" (the instructions pass skips the
        first copy and emits the second), observed:

            AssertionError: assert [{'action': '...'cycle.pass'}] == []
              Left contains one more item: {'action': 'target', 'to_port': 'next', 'when': 'cycle.pass'}

        (Mutant "one copy of the loop wire only", ``loop_wires(...)[:1]`` in
        `_loop_edge_keys`, SURVIVED, and is equivalent: every legal copy has
        the same four ends, so the set of keys is the same either way.)
        """
        g = _block()
        g = g.model_copy(update={"edges": [*g.edges,
                                           _e("s0", "pass", "t", "next")]})
        assert _entry(g)["loop"] is True
        assert self._instructions(g) == []

    def test_no_wire_no_loop(self):
        """Mutant "loop whenever the block is a mosaic", observed:

            assert True is False
        """
        assert _entry(_block(loop=False))["loop"] is False

    def test_a_pass_wire_from_a_mid_lane_stage_is_not_the_loop(self):
        """M12's shape: the wire leaves FILTER CYCLE while CAPTURE LOOP comes
        after it. Not the loop, so it is left in the instructions, where
        `to_plan` refuses the graph (M12) or calls it a rule that will not
        run.

        Mutant "any pass wire from the lane is the loop" (`loop_wires`
        accepts every lane stage), observed:

            assert True is False
        """
        g = _block(stages=("cycle", "capture"), loop=False)
        g = g.model_copy(update={"edges": [*g.edges,
                                           _e("s0", "pass", "t", "next")]})
        assert _entry(g)["loop"] is False
        assert [r["when"] for r in self._instructions(g)] == ["cycle.pass"]

    def test_a_loop_wire_on_one_panel_rotates_nothing(self):
        """M4's note: "one panel, nothing to rotate between". Not legal (1.4
        item 1 needs a multi-panel block), so `loop` stays False and the wire
        is emitted, which `to_plan` reports as a rule that will not run.

        Mutant "a loop wire into any block" (`_loop_edge_keys` drops the
        multi-panel test), observed on the second graph (on the first, with
        no mosaic, no loop wire is looked for at all):

            AssertionError: assert [] == [('cycle.pass', 'next')]
              Right contains one more item: ('cycle.pass', 'next')
        """
        g = _block(rows=1, cols=1)
        assert _entry(g)["loop"] is False
        assert [r["when"] for r in self._instructions(g)] == ["cycle.pass"]
        # The same block beside a real mosaic, so the graph is scoped by
        # wires and the loop wires are looked for at all: the mosaic's is
        # consumed, the 1x1's still is not.
        other = {**M33, **FIELD, "rotation": 5, "angle": "Rotate to PA",
                 "rows": 2, "cols": 2}
        g = FlowGraph(
            nodes=[*g.nodes, _n("m", "target", x=100, y=300, **other),
                   _n("mc", "cycle", x=200, y=300)],
            edges=[*g.edges, _e("m", "target", "mc", "run"),
                   _e("mc", "pass", "m", "next")])
        assert _entry(g)["loop"] is False and _entry(g, "m")["loop"] is True
        assert [(r["when"], r["to_port"]) for r in self._instructions(g)] == \
            [("cycle.pass", "next")]
        assert len(self._instructions(g)) == 1


# ---------------------------------------------------------------- centring

class TestCentre:
    def test_the_missing_key_centring_is_what_the_hub_does(self):
        """1.2 arcmin and 3 tries: the hub's own 0.02 deg and 3 (spec 3.1).

        Mutant "missing-key centerTol 0.5" (``nodes.py``), observed:

            AssertionError: assert {'attempts': ..._arcmin': 0.5} == {'attempts': ..._arcmin': 1.2}
              Omitting 1 identical items, use -vv to show
              Differing items:
              {'tol_arcmin': 0.5} != {'tol_arcmin': 1.2}
        """
        g = _block(rows=1, cols=1, loop=False)
        for key in ("centerTol", "centerTries"):
            g.node("t").params.pop(key, None)
        assert _entry(g)["centre"] == {"tol_arcmin": 1.2, "attempts": 3}

    def test_the_blocks_own_centring_is_carried(self):
        """Mutant "centre fixed at the defaults", observed:

            AssertionError: assert {'attempts': ..._arcmin': 1.2} == {'attempts': ..._arcmin': 0.5}
              Differing items:
              {'attempts': 3} != {'attempts': 5}
              {'tol_arcmin': 1.2} != {'tol_arcmin': 0.5}
        """
        g = _block(rows=1, cols=1, loop=False, centerTol=0.5, centerTries=5)
        assert _entry(g)["centre"] == {"tol_arcmin": 0.5, "attempts": 5}

    def test_a_legacy_slews_tolerance_is_not_carried(self):
        """Spec 1.7: a SLEW card's `tol` never reached the run, and carrying
        0.5 would silently tighten every saved flow's centring.

        Mutant "carry the SLEW tol" (the TARGET's `tol_arcmin` is taken from
        a SLEW node in the graph when there is one), observed:

            AssertionError: assert {'attempts': ..._arcmin': 0.5} == {'attempts': ..._arcmin': 1.2}
              Omitting 1 identical items, use -vv to show
              Differing items:
              {'tol_arcmin': 0.5} != {'tol_arcmin': 1.2}
        """
        g = _block(rows=1, cols=1, loop=False, stages=())
        g = FlowGraph(
            nodes=[*g.nodes, _n("sl", "slew", x=150, tol=0.5, retries=7),
                   _n("c", "capture", x=250, exposure=60, count=5)],
            edges=[*g.edges, _e("t", "target", "sl", "run"),
                   _e("sl", "centered", "c", "run")])
        assert _entry(g)["centre"] == {"tol_arcmin": 1.2, "attempts": 3}

    def test_a_centring_that_is_not_a_number_is_left_to_the_hub(self):
        """None, which `to_plan` leaves unset: the run then centres to the
        hub's own numbers, which are the missing-key ones, and never to a
        number the compile made up.

        Mutant "garbage centring reads the default" (``_num(value, 1.2)``
        and ``_num(value, 3)``), observed:

            AssertionError: assert {'attempts': ..._arcmin': 1.2} == {'attempts': ...arcmin': None}
              Differing items:
              {'attempts': 3} != {'attempts': None}
              {'tol_arcmin': 1.2} != {'tol_arcmin': None}
        """
        g = _block(rows=1, cols=1, loop=False, centerTol="tight",
                   centerTries="lots")
        assert _entry(g)["centre"] == {"tol_arcmin": None, "attempts": None}


# ------------------------------------------------------------- count mode

class TestCountMode:
    def test_a_block_saved_before_counts_existed_counts_every_sub(self):
        """RULING 2's missing-key half. A node with no `counts` key is read
        through the missing-key default, "Every sub taken", which is what it
        counted when it was saved.

        Mutant "missing-key Accepted" (``nodes.py``: TARGET's and POOL's
        missing-key `counts` default "Accepted subs"), observed:

            AssertionError: assert 'accepted' == 'attempts'
        """
        g = _block(rows=1, cols=1, loop=False)
        g.node("t").params.pop("counts", None)
        assert _entry(g)["count_mode"] == "attempts"

    @pytest.mark.parametrize("stored,mode", [
        ("Accepted subs", "accepted"), ("Every sub taken", "attempts"),
        ("accepted subs ", "accepted"), ("Best ones", "attempts")])
    def test_the_blocks_words_decide(self, stored, mode):
        """Mutant "count_mode always attempts", observed for both spellings
        of accepted subs:

            AssertionError: assert 'attempts' == 'accepted'
        """
        g = _block(rows=1, cols=1, loop=False, counts=stored)
        assert _entry(g)["count_mode"] == mode

    def test_every_pool_member_carries_the_pools_count_mode(self):
        """Mutant "a pool entry carries no count_mode", observed:

            KeyError: 'count_mode'
        """
        g = FlowGraph(nodes=[_n("p", "pool", members="M16, M17",
                                counts="Accepted subs"),
                             _n("c", "capture", x=100, exposure=60, count=5)],
                      edges=[_e("p", "target", "c", "run")])
        modes = [t["count_mode"] for t in compile_plan(g, "n")["targets"]]
        assert modes == ["accepted", "accepted"]


# ---------------------------------------------------------------- followers

class TestFollows:
    def test_what_the_tail_feeds_follows_the_mosaic(self):
        """Spec 1.6: targets reachable from the tail's "all done" are the
        group's followers, a POOL's members included.

        Mutant "no follows" (the entry never carries the key), observed:

            KeyError: 'follows'

        Mutant "every block has followers" (any TARGET's tail, one panel or
        many, has followers), observed:

            AssertionError: assert ['t2', 't2'] == ['t', 't']
              At index 0 diff: 't2' != 't'
        """
        g = _block(after=[_n("t2", "target", x=400, **M33),
                          _n("k", "capture", x=500, exposure=60, count=5),
                          _n("p", "pool", x=600, members="M16, M17")])
        compiled = compile_plan(g, "n")
        by = {}
        for t in compiled["targets"]:
            by.setdefault(t["node_id"], []).append(t)
        assert by["t2"][0]["follows"] == "t"
        assert [m["follows"] for m in by["p"]] == ["t", "t"]
        assert "follows" not in by["t"][0]

    def test_a_block_fed_by_the_mosaic_itself_is_not_a_follower(self):
        """Only the TAIL's output is what runs next (1.5 item 6). A TARGET
        wired straight off the mosaic's own output is a second branch, not
        what comes after the panels.

        Mutant "followers start at the block" (`_followers` starts from the
        block's own flow children), observed:

            AssertionError: assert 'follows' not in {'angle': 'any', 'centre': {'attempts': 3, 'tol_arcmin': 1.2}, 'count_mode': 'attempts', 'dec': '+30 39 37', ...}
        """
        g = _block()
        g = FlowGraph(nodes=[*g.nodes, _n("t2", "target", x=150, y=200, **M33)],
                      edges=[*g.edges, _e("t", "target", "t2", "arm")],
                      settings=g.settings)
        assert "follows" not in _entry(g, "t2")

    def test_a_target_after_two_mosaics_follows_the_nearer(self):
        """A -> B -> C: C follows B and B follows A, the order the gates open
        in.

        Mutant "the farther mosaic wins" (`_followers` keeps the largest
        depth), observed:

            AssertionError: assert 't' == 'b'
        """
        b_params = {**M33, **FIELD, "rotation": 10, "angle": "Rotate to PA",
                    "rows": 2, "cols": 2}
        g = _block(after=[_n("b", "target", x=400, **b_params),
                          _n("bc", "cycle", x=500)])
        g = FlowGraph(
            nodes=[*g.nodes, _n("c", "target", x=700, name="NGC 7331",
                                ra="22h 37m 04s", dec="+34 24 56")],
            edges=[*g.edges, _e("bc", "pass", "b", "next"),
                   _e("bc", "complete", "c", "arm")],
            settings=g.settings)
        assert _entry(g, "b")["follows"] == "t"
        assert _entry(g, "c")["follows"] == "b"

    def test_a_graph_with_no_mosaic_has_no_followers(self):
        """Mutant "every block has followers", observed:

            assert False
             +  where False = all(<generator object TestFollows.test_a_graph_with_no_mosaic_has_no_followers.<locals>.<genexpr> at 0x000002485B1E6B50>)
        """
        g = _block(rows=1, cols=1, loop=False,
                   after=[_n("t2", "target", x=400, **M33)])
        assert all("follows" not in t
                   for t in compile_plan(g, "n")["targets"])


# ------------------------------------------------ stages inside the circle

class TestStagesOnEveryPanel:
    def test_a_capture_inside_the_circle_is_a_one_slot_cycle(self):
        """Spec 1.3 item 4: one frame per pass on every panel, so a mono lane
        needs no one-filter FILTER CYCLE to rotate.

        Mutant "no per_visit inside the circle", observed:

            AssertionError: assert None == 1
        """
        g = _block(stages=("capture",))
        step = _entry(g)["steps"][0]
        assert step.get("per_visit") == 1

    def test_outside_the_circle_a_capture_is_unchanged(self):
        """Control: an unlooped mosaic and a single target keep the capture
        step exactly as it was.

        Mutant "per_visit on every capture in a mosaic graph", observed:

            AssertionError: assert 1 is None
        """
        for g in (_block(stages=("capture",), loop=False),
                  _block(rows=1, cols=1, stages=("capture",), loop=False)):
            assert _entry(g)["steps"][0].get("per_visit") is None

    def test_stages_are_concatenated_in_lane_order_not_canvas_order(self):
        """1.3 item 5: TARGET -> CYCLE -> CAPTURE Ha gives every panel the
        cycle's steps and then the capture, even with the CAPTURE drawn to
        the LEFT of the cycle.

        Mutant "canvas order instead of the walk" (the compile sorts the
        walk by canvas position), observed:

            AssertionError: assert ['cycle'] == ['cycle', 'capture']
              Right contains one more item: 'capture'
        """
        g = _block(stages=("cycle", "capture"))
        g.node("s1").x = 50
        steps = _entry(g)["steps"]
        assert [s.get("strategy", "capture") for s in steps] == \
            ["cycle", "capture"]
        assert [s["node_id"] for s in steps] == ["s0", "s1"]
