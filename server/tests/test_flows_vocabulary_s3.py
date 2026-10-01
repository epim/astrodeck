"""Mosaic slice S3, task V: the TARGET vocabulary, `create_params`, legacy
SLEW, the flow-level settings table and the rig-facts value (#189 U-09 part,
advances #190).

Spec: docs/superpowers/specs/2026-09-23-flows-mosaic-target-block-design.md,
sections 1.2 (ports), 1.3 items 1-2 (the `pass` output, "all done"), 1.6
(`FlowGraph.settings`, `FLOW_SETTINGS`, `whenWaiting`), 1.7 (`LEGACY_TYPES`,
the palette), 3.1 (params, `create_params`, `counts`, `frameAnchor`), 3.3 (rig
facts), and Revision 2 rulings 1, 2 and 9.

THE ONE RULE THIS FILE KEEPS: A MISSING KEY MEANS WHAT IT MEANT BEFORE THE KEY
EXISTED. The memory note "a semantics flip needs a migration" is the defect
class: making a new meaning the missing-key default silently re-means every
saved flow. So the new choices (accepted subs only, no inherited angle, blank
coordinates) are written out explicitly when a block is CREATED
(`create_params`), and `with_defaults` only ever fills in the old behaviour.
While the compile read none of the new keys, the controls at the bottom of
this file proved that by compiling every Example, and a stored S2 lane, with
each new key set to a value other than its default. The compile task (S3-CP)
then read five of them for every block; those were retired from the controls
in the integration of S3, which now hold that a SINGLE PANEL reads none of
its mosaic keys and that each retired key does change the compile.

Every behaviour below was shown RED under a named mutation, run in a private
scratch copy of server/ (never the shared tree, issue #254). The observed
failure is quoted in the test that caught it.

DELIBERATE PIN CHANGES, recorded here so nobody reads them as drift:

* still 21 node types, and now 1 of them is legacy (SLEW + CENTER, spec 1.7);
  the palette offers 20. `test_flows_graph.py` changed its palette test from
  "the palette is NODE_DEFS" to "the palette is NODE_DEFS less LEGACY_TYPES".
* TARGET gained the optional event input `next` ("next panel"), so the pinned
  optional-input set in `test_flows_graph.py` gained ("target", "next").
* TARGET's output `target` is relabelled "each panel"; CAPTURE LOOP's and
  FILTER CYCLE's `complete` are relabelled "all done". Port IDS are unchanged,
  so every saved wire survives; only the words on the card change.
* S4 (#331): AUTOFOCUS and GUIDE gained the structural event output `pass`
  ("pass done"), appended last, so `focused` and `guiding` keep their rows.
  `test_autofocus_and_guide_gain_pass_done` pins it,
  `TestAutofocusAndGuidePassWires` holds what their pass wires compile to,
  and `TestTheNewPassOutputsChangeNoShippedPlan` holds that no Example's or
  wizard graph's plan moved.
"""
from __future__ import annotations

import ast
import dataclasses
import json
import math
from pathlib import Path

import pytest

from astrodeck.flows import check, wizard
from astrodeck.flows import compile as flows_compile
from astrodeck.flows.compile import (
    compile_plan, is_multi_panel, one_panel_pass_wires)
from astrodeck.flows.examples import examples
from astrodeck.flows.models import (
    FLOW_SETTINGS, FlowEdge, FlowGraph, FlowNode, FlowRecord, resolve_setting)
from astrodeck.flows.nodes import (
    COUNT_MODES, LEGACY_TYPES, NODE_DEFS, PALETTE_GROUPS, TARGET_ANGLES,
    create_params, default_params, target_angle)
from astrodeck.flows.rig import RigFacts
from astrodeck.flows.store import (
    FLOW_SCHEMA, V3_SCHEMA, _v4_meanings, schema_for)
from astrodeck.flows.to_plan import to_sequence_plan


def _n(nid: str, ntype: str, **params) -> FlowNode:
    return FlowNode(id=nid, type=ntype, params=params)


def _e(src: str, sp: str, dst: str, dp: str) -> FlowEdge:
    return FlowEdge(**{"from": src, "fromPort": sp, "to": dst, "toPort": dp})


#: Spec 3.1's missing-key column, typed out HERE rather than read back from
#: `nodes.py`, so a drifted default is caught by a second hand and not agreed
#: with by the same one. Every value is the OLD meaning: one panel, no field
#: recorded, today's hub centring (0.02 deg = 1.2 arcmin, 3 attempts), and
#: counting every sub taken, which is what a flow saved before S3 did.
MISSING_KEY = {
    "rows": 1, "cols": 1, "overlap": 25, "fovX": 0, "fovY": 0,
    "fovFrom": "", "skip": "", "passes": 1, "minVisit": 0,
    "order": "Least complete first", "centerTol": 1.2, "centerTries": 3,
    "ifNotCentred": "Auto", "counts": "Every sub taken", "frameAnchor": "",
}

#: What a block is written out with when it is CREATED (spec 3.1's "Created as"
#: column, Revision 2 rulings 2 and 9): blank coordinates, so a typed name can
#: never land on M31's (#190); no angle nobody chose (I-04); accepted subs only.
CREATED_AS = {"name": "", "ra": "", "dec": "", "rotation": -1,
              "angle": "Any angle", "counts": "Accepted subs"}


# ============================================================== the TARGET params

class TestMissingKeyDefaultsKeepTheOldMeaning:
    def test_a_target_saved_before_s3_resolves_to_one_panel_counting_every_sub(self):
        """A TARGET as S2 stored it: name, coordinates, rotation. Loading it
        must add the new keys with their OLD meaning and change nothing else.
        """
        stored = {"name": "NGC 7331", "ra": "22h 37m 04s",
                  "dec": "+34° 24′ 56″", "rotation": 23.4}
        n = FlowNode(id="t", type="target", params=dict(stored)).with_defaults()
        for k, v in stored.items():
            assert n.params[k] == v, f"the stored {k} was overwritten"
        for k, v in MISSING_KEY.items():
            assert n.params[k] == v and type(n.params[k]) is type(v), (
                f"missing {k} resolved to {n.params.get(k)!r}, not the old "
                f"meaning {v!r}")

    @pytest.mark.parametrize("ntype", ["target", "pool"])
    def test_a_block_with_no_counts_key_counts_every_sub_taken(self, ntype):
        """Revision 2, ruling 2: "Loading never changes a flow's meaning: a
        flow with no counts key ... compiles to attempts as before." Only a
        SAVE switches it (a later task, `_persist_flow`).

        Mutant 'counts missing-key default Accepted subs' (the TARGET's and the
        POOL's `counts` in NODE_DEFS params set to "Accepted subs"), observed:

            E       AssertionError: a target saved before S3 now counts
                    'Accepted subs': loading re-meant it without a save
            E       assert 'Accepted subs' == 'Every sub taken'
            FAILED ...test_a_block_with_no_counts_key_counts_every_sub_taken[target]
        """
        n = FlowNode(id="b", type=ntype, params={}).with_defaults()
        assert n.params["counts"] == "Every sub taken", (
            f"a {ntype} saved before S3 now counts {n.params['counts']!r}: "
            f"loading re-meant it without a save")

    def test_angle_gets_no_missing_key_default(self):
        """`angle` is derived from `rotation` at compile time (spec 3.1), so a
        stored TARGET with a real PA and no angle key must stay that way. A
        missing-key "Any angle" would silently turn a block that commands the
        rotator to PA 23.4 into one that commands nothing.

        Mutant 'angle given a missing-key default' (`"angle": "Any angle"`
        added to TARGET's params), observed:

            E       AssertionError: with_defaults wrote angle='Any angle' into
                    a stored TARGET with rotation 23.4; the angle is derived
                    from rotation, never defaulted
            E       assert 'angle' not in {'angle': 'Any angle', 'centerTol':
                    1.2, 'centerTries': 3, 'cols': 1, ...}
        """
        n = FlowNode(id="t", type="target",
                     params={"name": "M31", "rotation": 23.4}).with_defaults()
        assert "angle" not in n.params, (
            f"with_defaults wrote angle={n.params['angle']!r} into a stored "
            f"TARGET with rotation 23.4; the angle is derived from rotation, "
            f"never defaulted")
        assert "angle" not in default_params("target")
        assert target_angle(n.params) == "Rotate to PA"

    def test_name_ra_dec_and_rotation_keep_todays_missing_key_defaults(self):
        """Deferred (spec 3.1 says blank; this task keeps today's). A saved
        TARGET with no coordinates still resolves to M31's, as it did on S2,
        because blanking them here would re-mean every such flow on load. New
        blocks get blanks through `create_params` instead."""
        p = default_params("target")
        assert (p["name"], p["ra"], p["dec"], p["rotation"]) == (
            "M31 - Andromeda", "00h 42m 44s", "+41° 16′ 09″", -1)

    def test_a_stored_s3_value_survives_the_merge(self):
        n = FlowNode(id="t", type="target",
                     params={"counts": "Accepted subs", "rows": 3,
                             "angle": "Camera fixed at PA"}).with_defaults()
        assert (n.params["counts"], n.params["rows"], n.params["angle"]) == (
            "Accepted subs", 3, "Camera fixed at PA")

    def test_the_default_choices_are_members_of_their_vocabularies(self):
        """A default outside its own option list opens a select on nothing
        (the UI pins the same for its option lists)."""
        assert TARGET_ANGLES == ("Any angle", "Rotate to PA",
                                 "Camera fixed at PA")
        assert COUNT_MODES == ("Every sub taken", "Accepted subs")
        for ntype in ("target", "pool"):
            assert default_params(ntype)["counts"] in COUNT_MODES
            assert create_params(ntype)["counts"] in COUNT_MODES
        assert create_params("target")["angle"] in TARGET_ANGLES


class TestTheDerivedAngle:
    @pytest.mark.parametrize("params,angle", [
        ({"rotation": -1}, "Any angle"),
        ({"rotation": -0.5}, "Any angle"),
        # 0 IS A REAL POSITION ANGLE (north up), the #150 lesson. Reading it as
        # "any" would stop a block that asks for PA 0 commanding the rotator.
        ({"rotation": 0}, "Rotate to PA"),
        ({"rotation": 23.4}, "Rotate to PA"),
        ({"rotation": "23.4"}, "Rotate to PA"),
        # Unparseable or empty falls to "no constraint", exactly as compile's
        # `_num(rotation, -1)` does, never to PA 0.
        ({"rotation": ""}, "Any angle"),
        ({"rotation": "abc"}, "Any angle"),
        ({}, "Any angle"),
        # A stored choice wins over the derivation.
        ({"rotation": 30, "angle": "Camera fixed at PA"}, "Camera fixed at PA"),
        ({"rotation": 30, "angle": ""}, "Rotate to PA"),
    ])
    def test_the_angle_a_node_means(self, params, angle):
        """Mutant 'rotation 0 read as any angle' (`< 0` changed to `<= 0` in
        `target_angle`), observed:

            E       AssertionError: for params {'rotation': 0}
            E       assert 'Any angle' == 'Rotate to PA'
            FAILED ...test_the_angle_a_node_means[params2-Rotate to PA]
        """
        assert target_angle(params) == angle, f"for params {params}"


class TestCreateParams:
    def test_a_created_target_is_written_with_the_created_as_column(self):
        """Mutant 'create_params ignores the Created-as column' (the property
        returns `dict(self.params)` only), observed:

            E           AssertionError: create_params('target')
                        name='M31 - Andromeda', not the Created-as ''
            E           assert 'M31 - Andromeda' == ''
        """
        p = create_params("target")
        for k, v in CREATED_AS.items():
            assert p.get(k) == v, (
                f"create_params('target') {k}={p.get(k)!r}, not the Created-as "
                f"{v!r}")
        # Everything not in the column is the missing-key default.
        for k, v in MISSING_KEY.items():
            if k not in CREATED_AS:
                assert p[k] == v, k
        assert set(p) == set(default_params("target")) | {"angle"}

    def test_the_nodedef_property_and_the_function_agree(self):
        assert NODE_DEFS["target"].create_params == create_params("target")
        assert NODE_DEFS["pool"].create_params == create_params("pool")

    def test_a_created_pool_counts_accepted_subs(self):
        """Ruling 2: "POOL gets the same treatment, so a new pool-only flow is
        not the one new flow that counts rejects"."""
        assert create_params("pool")["counts"] == "Accepted subs"
        assert default_params("pool")["counts"] == "Every sub taken"
        assert {k: v for k, v in create_params("pool").items()
                if k != "counts"} == {k: v for k, v in
                                      default_params("pool").items()
                                      if k != "counts"}

    def test_every_other_type_is_created_with_its_defaults(self):
        """The control: only TARGET and POOL have a Created-as column."""
        for t in NODE_DEFS:
            if t in ("target", "pool"):
                continue
            assert create_params(t) == default_params(t), t

    def test_the_column_names_only_keys_something_reads(self):
        """A Created-as key that is neither a param nor the derived angle is a
        value written into every new block that nothing reads."""
        for t, d in NODE_DEFS.items():
            extra = set(d.created_as) - set(d.params) - {"angle"}
            assert not extra, f"{t} is created with unread keys {extra}"

    def test_it_is_a_fresh_copy(self):
        p = create_params("target")
        p["name"] = "edited"
        p["counts"] = "edited"
        assert create_params("target")["name"] == ""
        assert NODE_DEFS["target"].created_as["counts"] == "Accepted subs"
        assert create_params("nonesuch") == {}


# ================================================================== ports

class TestPorts:
    def test_target_gains_an_optional_next_panel_input(self):
        d = NODE_DEFS["target"]
        assert [(p.id, p.label, p.kind) for p in d.ins] == [
            ("arm", "arm", "flow"), ("next", "next panel", "event")]
        assert "next" in d.optional_ins
        assert [(p.id, p.label, p.kind) for p in d.outs] == [
            ("target", "each panel", "flow")]

    @pytest.mark.parametrize("ntype", ["capture", "cycle"])
    def test_capture_and_cycle_gain_pass_done(self, ntype):
        """The `pass` output is appended, so the existing ports keep their
        positions on the card and every saved wire keeps its socket.

        Mutant 'pass port missing' (FILTER CYCLE's `_e("pass", "pass done")`
        removed from nodes.py), observed:

            E       Right contains one more item: ('pass', 'pass done', 'event')
            FAILED ...test_capture_and_cycle_gain_pass_done[cycle]
        """
        assert [(p.id, p.label, p.kind) for p in NODE_DEFS[ntype].outs] == [
            ("complete", "all done", "flow"),
            ("frame", "frame graded", "event"),
            ("pass", "pass done", "event")]

    @pytest.mark.parametrize("ntype,flow_out", [("autofocus", "focused"),
                                                ("guide", "guiding")])
    def test_autofocus_and_guide_gain_pass_done(self, ntype, flow_out):
        """S4, #331: either can be the last stage of a panel lane, and the
        loop wire must leave the last stage, so each carries the same
        structural ``pass`` output as CAPTURE LOOP. Appended LAST, so the
        flow output keeps its row on the card and every saved wire its
        socket.

        Mutant 'AUTOFOCUS has no pass' (``_e("pass", "pass done")`` dropped
        from AUTOFOCUS's outs in nodes.py), observed:
            E       Right contains one more item: ('pass', 'pass done', 'event')
            FAILED ...test_autofocus_and_guide_gain_pass_done[autofocus-focused]
        """
        assert [(p.id, p.label, p.kind) for p in NODE_DEFS[ntype].outs] == [
            (flow_out, flow_out, "flow"), ("pass", "pass done", "event")]

    def test_still_21_types_and_one_flow_input_each(self):
        """Spec 1.1: every node type has at most one flow input, so a lane is a
        chain. `next` is an EVENT input and keeps that true."""
        assert len(NODE_DEFS) == 21
        for t, d in NODE_DEFS.items():
            assert sum(p.kind == "flow" for p in d.ins) <= 1, t

    def test_a_saved_s2_graph_still_validates(self):
        """The port ids did not change, so an S2-era lane loads and wires."""
        g = FlowGraph(
            nodes=[_n("d", "dusk"), _n("t", "target"), _n("c", "cycle"),
                   _n("r", "report")],
            edges=[_e("d", "window", "t", "arm"), _e("t", "target", "c", "run"),
                   _e("c", "complete", "r", "session")])
        assert g.validation_errors() == []

    def test_the_loop_wire_is_a_legal_backward_event_wire(self):
        """`cycle.pass -> target.next` points back up the lane. It is an event
        wire, so it closes no flow loop (#149) and validates."""
        g = FlowGraph(
            nodes=[_n("d", "dusk"), _n("t", "target"), _n("c", "cycle"),
                   _n("r", "report")],
            edges=[_e("d", "window", "t", "arm"), _e("t", "target", "c", "run"),
                   _e("c", "complete", "r", "session"),
                   _e("c", "pass", "t", "next")])
        assert g.validation_errors() == []

    def test_doctor_rule_1_does_not_ask_for_next(self):
        """A single target never wires `next`; rule 1 must not call it unwired.

        Positive control in the same graph: drop the `arm` wire and rule 1
        still names it, so the silence about `next` is the exemption and not
        a rule that stopped running.

        Mutant 'next left out of optional_ins' (TARGET's `optional_ins`
        removed), observed:

            E       AssertionError: rule 1 asks for a wire only a mosaic loop
                    uses: ["▸ TARGET - 'next panel' input unwired"]
            E       assert not ["▸ TARGET - 'next panel' input unwired"]
        """
        lane = [_n("d", "dusk"), _n("t", "target"), _n("c", "capture"),
                _n("r", "report")]
        wires = [_e("t", "target", "c", "run"), _e("c", "complete", "r", "session")]
        g = FlowGraph(nodes=lane, edges=[_e("d", "window", "t", "arm"), *wires])
        nagged = [i.text for i in check(g) if "next panel" in i.text]
        assert not nagged, f"rule 1 asks for a wire only a mosaic loop uses: {nagged}"
        unarmed = FlowGraph(nodes=lane, edges=wires)
        assert any("TARGET - 'arm' input unwired" in i.text
                   for i in check(unarmed))


# ================================== AUTOFOCUS and GUIDE pass wires (S4, #331)

#: A single target with its own coordinates, so ``to_plan`` needs no search.
M42 = {"name": "M42", "ra": "05h 35m 17s", "dec": "-05 23 28"}


def _pass_lane(stage: str, *, rows: int = 1, cols: int = 1, to: str = "t",
               to_port: str = "next") -> FlowGraph:
    """TARGET M42 -> FILTER CYCLE -> ``stage`` (an AUTOFOCUS or a GUIDE, so
    the lane's tail), and the stage's 'pass done' wired into ``to``'s
    ``to_port``. A NOTIFY stands on the canvas for a wire that goes to no
    TARGET."""
    return FlowGraph(
        nodes=[FlowNode(id="t", type="target",
                        params={**M42, "rows": rows, "cols": cols}),
               FlowNode(id="cy", type="cycle", x=200,
                        params={"plan": "L 60", "cycles": 2}),
               FlowNode(id="s", type=stage, x=400),
               FlowNode(id="n", type="notify", x=600)],
        edges=[_e("t", "target", "cy", "run"), _e("cy", "complete", "s", "run"),
               _e("s", "pass", to, to_port)])


class TestAutofocusAndGuidePassWires:
    """What a pass wire out of an AUTOFOCUS or a GUIDE compiles to, now that
    either has the port (S4, #331; spec 1.3 item 1, 1.4): exactly what one
    out of a CAPTURE LOOP or a FILTER CYCLE compiles to. The loop wire from
    a multi-panel lane's tail is consumed as the loop; one into a one-panel
    block from its own lane is consumed as nothing (S4 orchestrator ruling
    3); any other is emitted as ``<type>.pass``, a trigger the engine does
    not have, which ``to_plan`` reports as a rule that will not run, at warn
    level. Mutants in a private scratch copy (#254)."""

    def test_the_types_with_a_pass_output(self):
        """``compile.PASS_TYPES`` is read off the vocabulary: every lane type
        but legacy SLEW.

        Mutant 'AUTOFOCUS has no pass' (nodes.py), observed:
            E       Extra items in the right set:
            E       'autofocus'
            FAILED ...test_the_types_with_a_pass_output
        """
        assert flows_compile.PASS_TYPES == {"autofocus", "guide", "capture",
                                            "cycle"}

    @pytest.mark.parametrize("stage", ["autofocus", "guide"])
    def test_trigger_for_names_the_pass_wire(self, stage):
        """Mutant 'AUTOFOCUS and GUIDE read as graded frames' (compile.py
        ``_trigger_for``: ``if node.type in ("autofocus", "guide"): return
        "on_frame_graded"`` as its first line, the shape a capture stage's
        pass wire had before S3 gave it its own branch), observed:
            E       AssertionError: assert 'on_frame_graded' == 'autofocus.pass'
            FAILED ...test_trigger_for_names_the_pass_wire[autofocus]
            FAILED ...test_trigger_for_names_the_pass_wire[guide]
        """
        assert flows_compile._trigger_for(FlowNode(id="s", type=stage),
                                          "pass") == f"{stage}.pass"

    @pytest.mark.parametrize("stage", ["autofocus", "guide"])
    def test_a_pass_wire_that_is_not_the_loop_will_not_run_at_warn(self, stage):
        """Into a NOTIFY: no TARGET's ``next``, so neither the loop nor the
        one-panel wire, and emitted; ``to_plan`` reports it at warn level.

        Under the mutant 'AUTOFOCUS and GUIDE read as graded frames' above,
        observed:
            E       AssertionError: assert [('on_frame_g...d', 'notify')] ==
                    [('autofocus.pass', 'notify')]
            E         At index 0 diff: ('on_frame_graded', 'notify') !=
                      ('autofocus.pass', 'notify')
            FAILED ...test_a_pass_wire_that_is_not_the_loop_will_not_run_at_warn[autofocus]
            FAILED ...test_a_pass_wire_that_is_not_the_loop_will_not_run_at_warn[guide]
            (4 failed with the trigger cases, 115 deselected)
        """
        g = _pass_lane(stage, to="n", to_port="do")
        assert g.validation_errors() == []
        compiled = compile_plan(g, "pass wire")
        assert [(r["when"], r["action"]) for r in compiled["instructions"]] \
            == [(f"{stage}.pass", "notify")]
        _plan, unmapped = to_sequence_plan(compiled, g, flow_id="f-331")
        assert [(u["key"], u["level"]) for u in unmapped
                if ".pass" in u["key"]] == \
            [(f"instructions[{stage}.pass -> notify]", "warn")]

    @pytest.mark.parametrize("stage", ["autofocus", "guide"])
    def test_the_wire_from_a_mosaics_tail_is_its_loop(self, stage):
        """Mutant 'only a capture stage loops' (compile.py ``loop_wires``:
        ``and tail.type in ("capture", "cycle")`` added to its filter, the
        rule as the vocabulary stood before #331), observed:
            E       AssertionError: assert (False, [{'ac...focus.pass'}]) == (True, [])
            E         At index 0 diff: False != True
            FAILED ...test_the_wire_from_a_mosaics_tail_is_its_loop[autofocus]
            FAILED ...test_the_wire_from_a_mosaics_tail_is_its_loop[guide]
            and test_flows_panel_lane.py's carry cases whose tail is an AUTOFOCUS or
            a GUIDE with it: assert ('af', []) == ('af', ['loop']).
        """
        g = _pass_lane(stage, rows=3, cols=2)
        assert g.validation_errors() == []
        compiled = compile_plan(g, "mosaic")
        assert (compiled["targets"][0]["loop"], compiled["instructions"]) == \
            (True, [])

    @pytest.mark.parametrize("stage", ["autofocus", "guide"])
    def test_a_pass_wire_into_a_one_panel_block_is_consumed(self, stage):
        """A 3x2 that looped from an AUTOFOCUS, turned back into a single
        target, keeps its wire: consumed as nothing, as one from a FILTER
        CYCLE is (S4 orchestrator ruling 3), not a loss ``/run`` asks about.

        Mutant 'one-panel consumption for CAPTURE and CYCLE only'
        (compile.py ``one_panel_pass_wires``: ``src.type not in PASS_TYPES``
        back to ``src.type not in ("capture", "cycle")``), observed:
            E       AssertionError: assert [] == ['s']
            E         Right contains one more item: 's'
            FAILED ...test_a_pass_wire_into_a_one_panel_block_is_consumed[autofocus]
            FAILED ...test_a_pass_wire_into_a_one_panel_block_is_consumed[guide]
        """
        g = _pass_lane(stage)
        assert g.validation_errors() == []
        assert [e.from_ for e in one_panel_pass_wires(g)] == ["s"]
        compiled = compile_plan(g, "one panel")
        assert compiled["instructions"] == []
        _plan, unmapped = to_sequence_plan(compiled, g, flow_id="f-331")
        assert [u for u in unmapped if ".pass" in u["key"]] == []


class TestALoopFromAnAutofocusStampsSchema4:
    """Spec 3.6: the writer stamps FLOW_SCHEMA 4 when a file uses a meaning a
    v3 build would misread, a loop wire among them, read as any wire out of
    a ``pass`` output or into a ``next`` input whatever stage it leaves
    (``store._v4_meanings``). A loop wire leaving an AUTOFOCUS is one: no
    build before S0 has the port, and an S3 build has TARGET's ``next`` but
    not AUTOFOCUS's ``pass``.

    WHAT AN S3 BUILD DOES WITH SUCH A FILE, run against the committed S3
    code (git archive of HEAD 812fcf9e into a scratch directory): it is
    schema 4, which an S3 build does not refuse as a future schema, and its
    reader does not run ``validation_errors``, so it is NOT listed as
    unreadable. It opens, and its compile reads the wire as the loop (the
    lane functions match the port as a string). What refuses it is
    validation: save and ``/run`` answer 422 with "autofocus has no output
    port 'pass'". Loud, and nothing runs, which is the row the downgrade
    matrix gives a loop wire on a build older than S0.

    Observed (scratch script S4-U331-mut/downgrade.py, the file written by
    this build's FlowStore.save at schema_version 4, then read by the S3
    build's own store, validation, compile and save):

        S3 listing: records ['af-loop', ...the eight Examples],
                    unreadable rows []
        S3 validation_errors: ["autofocus has no output port 'pass'"]
        S3 compile: loop [True] instructions []
        S3 save refused: ValueError autofocus has no output port 'pass'

    and S3's run route answers the same structural error with a 422. S3's
    classic canvas draws no wire from a port its vocabulary lacks
    (``FlowWireLayer``: ``portPos`` refuses to guess), so the loop is not
    on screen there; the refusal is what names it.
    """

    def test_a_mosaic_looping_from_an_autofocus_stamps_4(self):
        g = _pass_lane("autofocus", rows=3, cols=2)
        assert schema_for(g) == FLOW_SCHEMA == 4

    def test_the_wire_alone_stamps_4(self):
        """With a 3x2 the grid alone stamps 4, so the wire's share is held on
        a graph whose ONLY v4 meaning is the wire: one panel, counting every
        sub taken, no angle. The control is the same graph without it.

        Mutant 'no loop-wire meaning' (store.py ``_v4_meanings``: the
        loop-wire test deleted), observed:
            E       AssertionError: assert [] == ['loop wire']
            E         Right contains one more item: 'loop wire'
            FAILED ...test_the_wire_alone_stamps_4
            (the 3x2 case stays green: its grid stamps 4 by itself, which is why
            the wire is held on its own)
        """
        g = _pass_lane("autofocus")
        assert _v4_meanings(g) == ["loop wire"]
        assert schema_for(g) == FLOW_SCHEMA
        bare = g.model_copy(update={
            "edges": [e for e in g.edges if e.fromPort != "pass"]})
        assert (_v4_meanings(bare), schema_for(bare)) == ([], V3_SCHEMA)


# ============================================================ legacy SLEW

class TestLegacySlew:
    def test_slew_is_the_one_legacy_type(self):
        assert LEGACY_TYPES == frozenset({"slew"})
        assert LEGACY_TYPES <= set(NODE_DEFS), "a legacy type must still load"

    def test_slew_left_the_palette_and_nothing_else_did(self):
        """Spec 1.7: SLEW + CENTER is part of the TARGET block now, so it is
        hidden from the palette; every other type is still offered once.

        Mutant 'slew left in PALETTE_GROUPS' (RIG OPS restored to start with
        "slew"), observed:

            E       AssertionError: the palette still offers a legacy type:
                    {'slew'}
            E       assert not ({'abort', 'autofocus', 'calib', 'capture',
                    'cloudwatch', 'condition', ...} & frozenset({'slew'}))
        """
        listed = [t for _group, types in PALETTE_GROUPS for t in types]
        assert not set(listed) & LEGACY_TYPES, (
            f"the palette still offers a legacy type: {set(listed) & LEGACY_TYPES}")
        assert set(listed) == set(NODE_DEFS) - LEGACY_TYPES
        assert len(listed) == 20 == len(set(listed))

    def test_a_saved_graph_with_a_slew_still_loads_validates_and_compiles(self):
        """No graph rewrite (spec 1.7): `FlowNode._known_type` would refuse an
        unknown type before any migration ran, so SLEW stays in NODE_DEFS."""
        g = FlowGraph(
            nodes=[_n("d", "dusk"), _n("t", "target"), _n("s", "slew"),
                   _n("c", "capture")],
            edges=[_e("d", "window", "t", "arm"), _e("t", "target", "s", "run"),
                   _e("s", "centered", "c", "run")])
        assert g.validation_errors() == []
        assert compile_plan(g)["targets"][0]["steps"], "the slew lane lost its steps"


# ========================================================== flow settings

class TestFlowSettings:
    def test_the_table_is_ruling_1(self):
        """Revision 2, ruling 1: the owner's default, and the one other value.

        Mutant 'resolver ignores the stored value' (`resolve_setting` returns
        the default unconditionally) is caught by the next test; the TS mirror
        is pinned against this literal by flowSettingsParity.test.ts."""
        assert FLOW_SETTINGS == {"whenWaiting": {
            "default": "Shoot later targets, then come back",
            "options": ["Shoot later targets, then come back",
                        "Wait for the mosaic"]}}

    def test_the_resolver(self):
        """Mutant 'resolver ignores the stored value' (`resolve_setting`
        returns `spec["default"]` unconditionally), observed:

            E       AssertionError: assert 'Shoot later ...hen come back' ==
                    'Wait for the mosaic'
        """
        assert resolve_setting({}, "whenWaiting") == \
            "Shoot later targets, then come back"
        assert resolve_setting(None, "whenWaiting") == \
            "Shoot later targets, then come back"
        assert resolve_setting({"whenWaiting": "Wait for the mosaic"},
                               "whenWaiting") == "Wait for the mosaic"
        assert FlowGraph(settings={"whenWaiting": "Wait for the mosaic"}) \
            .setting("whenWaiting") == "Wait for the mosaic"

    def test_a_value_this_build_does_not_know_resolves_to_the_default(self):
        """A newer build's third choice, or a hand-written typo, is not one of
        the two behaviours this engine has. It reads as the owner's default,
        and the stored value is kept so a newer build reads it back."""
        g = FlowGraph(settings={"whenWaiting": "Teleport"})
        assert g.setting("whenWaiting") == "Shoot later targets, then come back"
        assert g.settings == {"whenWaiting": "Teleport"}

    def test_asking_for_an_undeclared_setting_is_a_programming_error(self):
        with pytest.raises(KeyError):
            resolve_setting({}, "nonesuch")

    def test_a_graph_saved_without_settings_loads_and_round_trips(self):
        """Every flow saved before S3 has no `settings`. It must load, resolve
        to the default, and come back out equal, through the model and through
        the store (which is how it reaches disk).

        Mutant 'with_defaults writes the settings defaults' (with_defaults
        fills `settings` from FLOW_SETTINGS), observed:

            E       AssertionError: with_defaults wrote settings into a graph
                    that had none: {'whenWaiting': 'Shoot later targets, then
                    come back'}
            E       assert {'whenWaiting...en come back'} == {}
        """
        raw = {"nodes": [{"id": "t", "type": "target", "x": 0, "y": 0,
                          "params": {"name": "M31"}}], "edges": []}
        g = FlowGraph.model_validate(raw)
        assert g.settings == {}
        assert g.setting("whenWaiting") == "Shoot later targets, then come back"
        assert g.with_defaults().settings == {}, (
            f"with_defaults wrote settings into a graph that had none: "
            f"{g.with_defaults().settings}")
        again = FlowGraph.model_validate(json.loads(
            json.dumps(g.model_dump(by_alias=True))))
        assert again == g

    def test_the_store_round_trips_a_graph_with_and_without_settings(self, tmp_path):
        from astrodeck.flows.store import FlowStore
        store = FlowStore(tmp_path)
        bare = store.save(FlowRecord(name="bare", graph=FlowGraph(
            nodes=[_n("t", "target", name="M31")])))
        set_ = store.save(FlowRecord(name="set", graph=FlowGraph(
            nodes=[_n("t", "target", name="M31")],
            settings={"whenWaiting": "Wait for the mosaic"})))
        assert store.get(bare.id).graph == bare.graph
        assert store.get(bare.id).graph.settings == {}
        assert store.get(set_.id).graph.setting("whenWaiting") == \
            "Wait for the mosaic"

    @pytest.mark.parametrize("bad", [{"a": 1}, [1, 2], ("x",)])
    def test_settings_are_flat_scalars(self, bad):
        """flowsTypes.ts types a setting as a scalar; a nested value is a graph
        the editor cannot draw an input for.

        Mutant 'flat-scalar check dropped' (the settings validator loops over
        nothing and returns `v` unchecked), observed:

            E       Failed: DID NOT RAISE <class
                    'pydantic_core._pydantic_core.ValidationError'>
            FAILED ...test_settings_are_flat_scalars[bad0]
        """
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="flat scalar"):
            FlowGraph(settings={"whenWaiting": bad})

    def test_the_settings_map_is_bounded(self):
        """Bounded like nodes (400) and edges (800): a POSTed graph cannot
        carry an unbounded settings map into every save and every compile.
        32 is far above the one setting S3 declares; 33 keys are refused.

        Mutant 'settings unbounded' (`max_length=32` removed), observed:

            E       Failed: DID NOT RAISE <class
                    'pydantic_core._pydantic_core.ValidationError'>
        """
        from pydantic import ValidationError
        FlowGraph(settings={f"k{i}": i for i in range(32)})
        with pytest.raises(ValidationError):
            FlowGraph(settings={f"k{i}": i for i in range(33)})

    def test_scalars_and_null_are_accepted(self):
        g = FlowGraph(settings={"a": "x", "b": 1, "c": 2.5, "d": True, "e": None})
        assert g.settings["d"] is True and g.settings["e"] is None
        assert FlowGraph(settings={"whenWaiting": None}).setting(
            "whenWaiting") == "Shoot later targets, then come back"


# ============================================================== rig facts

class TestRigFacts:
    def test_rigfacts_with_no_arguments_knows_nothing(self):
        """Every field optional, and its unknown is None / empty / zero
        samples, never a plausible reading. A rule that needs a fact skips
        when it is unknown (spec 3.3).

        Mutant 'has_rotator defaults False', observed:

            E       AssertionError: RigFacts() claims to know
                    has_rotator=False; an unknown rig must not read as a rig
                    without a rotator
            E       assert False is None
        """
        f = RigFacts()
        assert f.fov_deg is None
        assert f.fov_from == ""
        assert f.hop_cost_s is None and f.hop_samples == 0
        assert f.has_rotator is None, (
            f"RigFacts() claims to know has_rotator={f.has_rotator!r}; an "
            f"unknown rig must not read as a rig without a rotator")
        assert f.reject_guards_off is None

    def test_it_is_frozen_and_hashable(self):
        """Mutant 'RigFacts not frozen' (`frozen=True` replaced by
        `eq=True, unsafe_hash=True`, so it stays hashable), observed:

            E       Failed: DID NOT RAISE <class
                    'dataclasses.FrozenInstanceError'>
        """
        f = RigFacts(fov_deg=(2.0, 1.33), has_rotator=True)
        with pytest.raises(dataclasses.FrozenInstanceError):
            f.has_rotator = False                          # type: ignore[misc]
        assert hash(f) == hash(RigFacts(fov_deg=(2.0, 1.33), has_rotator=True))

    def test_the_field_is_normalised_to_a_float_pair(self):
        f = RigFacts(fov_deg=[2, 1.33], fov_from="profile Refractor")
        assert f.fov_deg == (2.0, 1.33) and type(f.fov_deg[0]) is float
        assert isinstance(f.fov_deg, tuple)

    @pytest.mark.parametrize("kwargs", [
        {"fov_deg": (0.0, 1.33)},           # 0 is "unknown", which is None
        {"fov_deg": (2.0, -1.0)},
        {"fov_deg": (math.nan, 1.0)},
        {"fov_deg": (math.inf, 1.0)},
        {"fov_deg": (2.0,)},
        {"fov_deg": "2x1.33"},
        {"hop_cost_s": -1.0, "hop_samples": 3},
        {"hop_cost_s": math.nan, "hop_samples": 3},
        {"hop_cost_s": 160.0},              # a measured cost with no samples
        {"hop_samples": 4},                 # samples with no cost
        {"hop_samples": -1},
        {"has_rotator": "yes"},
        {"reject_guards_off": 1},
        {"fov_from": None},
    ])
    def test_a_reading_that_is_not_a_reading_is_refused(self, kwargs):
        """The route must say "unknown" as None, not as a zero or a string:
        M5 would read a (0, 1.33) field as a camera that images nothing.

        Mutant 'fov validation dropped' (`__post_init__` returns at once),
        observed, for {'fov_deg': (0.0, 1.33)}:

            E       Failed: DID NOT RAISE <class 'ValueError'>
            FAILED ...test_a_reading_that_is_not_a_reading_is_refused[kwargs0]
        """
        with pytest.raises(ValueError):
            RigFacts(**kwargs)

    def test_a_measured_hop_carries_its_sample_count(self):
        f = RigFacts(hop_cost_s=160.0, hop_samples=6, has_rotator=False,
                     reject_guards_off=True)
        assert (f.hop_cost_s, f.hop_samples) == (160.0, 6)
        assert f.has_rotator is False and f.reject_guards_off is True

    def test_the_module_is_pure(self):
        """No devices, no config, no clock: it may import only the standard
        library, so the doctor and compile stay importable without a rig."""
        src = Path(__file__).resolve().parents[1] / "astrodeck" / "flows" / "rig.py"
        tree = ast.parse(src.read_text(encoding="utf-8"))
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                roots.add("." if node.level else (node.module or "").split(".")[0])
        assert roots <= {"__future__", "dataclasses", "math", "typing"}, roots


# ================================ controls: a single panel reads no mosaic key

#: One deliberately NON-default value for every S3 key a SINGLE-PANEL block's
#: compile does not read: the mosaic's own keys, which `_target_entry` reads
#: only for a block of more than one panel. A compile that read any of them
#: for one panel would change its output; one that reads none cannot tell
#: these from the defaults. (Stripping the keys instead would prove nothing:
#: `compile_plan` runs `with_defaults` first, so a stripped graph and a
#: defaulted one are the same graph by construction.)
#:
#: `rows` and `cols` are NOT here: the compile reads them, through
#: `compile.is_multi_panel`, to switch a graph to scoping by wires (spec 1.5).
#:
#: RETIRED BY THE COMPILE TASK (S3-CP), AS THIS CLASS'S DOCSTRING ASKED. The
#: keys in RETIRED below were here while the compile read none of the S3 keys.
#: S3-CP made it read them for every block, one panel or many: `angle`
#: (`compile.angle_code`), `centerTol` and `centerTries` (the entry's
#: `centre`), `counts` on a TARGET and on a POOL (`count_mode_of`) and
#: `frameAnchor` (the entry's `frame_anchor`, which `to_plan` keys a 1x1 on).
#: So they left PERTURBED in the integration of S3 and moved to RETIRED, where
#: the opposite is held: setting one DOES change the compile.
PERTURBED = {
    "target": {"overlap": 35, "fovX": 2.0, "fovY": 1.33,
               "fovFrom": "profile Refractor", "skip": "1-1", "passes": 2,
               "minVisit": 10, "order": "Grid order",
               "ifNotCentred": "Shoot anyway"},
}

#: The keys the compile reads for every block since S3-CP, each with a value
#: other than the one `_s2_lane` resolves to (its TARGET has rotation 12.5, so
#: its derived angle is "Rotate to PA", not "Camera fixed at PA").
RETIRED = {
    "target": {"centerTol": 0.5, "centerTries": 5, "counts": "Accepted subs",
               "frameAnchor": "{\"rows\": 3}", "angle": "Camera fixed at PA"},
    "pool": {"counts": "Accepted subs"},
}


def _perturb(graph: FlowGraph, only: str | None = None, *,
             table: dict = PERTURBED,
             single_panel_only: bool = False) -> FlowGraph:
    def over(n: FlowNode) -> dict:
        if single_panel_only and is_multi_panel(n):
            return {}
        keys = table.get(n.type, {})
        return {k: v for k, v in keys.items() if only in (None, k)}
    return graph.model_copy(update={"nodes": [
        n.model_copy(update={"params": {**n.params, **over(n)}})
        for n in graph.nodes]})


def _compiled(graph: FlowGraph, name: str) -> str:
    return json.dumps(compile_plan(graph, name), sort_keys=True,
                      ensure_ascii=False, default=str)


def _s2_lane() -> FlowGraph:
    """A hand-built S2 lane, stored with no S3 keys at all: a TARGET and a
    POOL, each owning a stage."""
    return FlowGraph(
        nodes=[_n("d", "dusk"),
               _n("t", "target", name="NGC 7331", ra="22h 37m 04s",
                  dec="+34° 24′ 56″", rotation=12.5),
               _n("c", "cycle"), _n("p", "pool"), _n("k", "capture"),
               _n("r", "report")],
        edges=[_e("d", "window", "t", "arm"), _e("t", "target", "c", "run"),
               _e("c", "complete", "p", "arm"), _e("p", "target", "k", "run"),
               _e("k", "complete", "r", "session")])


class TestASinglePanelReadsNoMosaicKey:
    """Once TRANSITIONAL CONTROLS named TestCompileReadsNoneOfTheNewKeysYet:
    while the compile read none of the S3 keys, every key was perturbed here,
    and the class asked the compile task to take out each key it started
    reading, in the same change. S3-CP started reading five of them for
    every block; the integration of S3 took them out (RETIRED above) and
    renamed the class for what it still holds: a block of ONE panel compiles
    the same whatever its mosaic keys say ("a single target reads as one",
    `compile._target_entry`). The retired keys are held the other way round
    by `test_each_retired_key_changes_the_compile`.

    Checked against HEAD on 2026-09-25, before S3-CP: a dump of
    `compile_plan` for all seven Examples taken before task V and one taken
    after it were byte-identical (sha256 per Example, the scratch script
    dump_examples.py).

    Mutant 'a single panel carries the mosaic too' (in a private scratch copy
    of compile.py, `if rows * cols > 1:` in `_target_entry` made `if True:`),
    observed after the retirement, for example-m31:

        E  - v_from": "profile Refractor", "fov_x": 2, "fov_y": 1.33,
             "frame_anchor": "", "order": "Grid order", "overlap": 35,
             "passes": 2, "require_centred": false, "rows": 1, "skip":
             [[1, 1]], "visit_min": 10, ...
        14 failed, 11 passed

    (the five Examples with a TARGET, and all nine keys of the S2 lane; the
    campaign and pool Examples hold only POOLs, so they cannot see it).
    """

    def test_the_examples_carry_the_new_keys(self):
        """Guard: the Examples are built from `default_params`, so every one
        of their TARGETs now carries the S3 keys."""
        seen = 0
        for rec in examples():
            for n in rec.graph.nodes:
                if n.type == "target":
                    seen += 1
                    assert set(MISSING_KEY) <= set(n.params), rec.id
        assert seen >= 5, seen

    @pytest.mark.parametrize("rec", examples(), ids=lambda r: r.id)
    def test_each_example_compiles_the_same_whatever_single_panels_say(
            self, rec):
        """Every Example, the eighth (a 3x2 mosaic, S3-W) included: its
        multi-panel block is left as it is, because a mosaic reads these keys
        by design, and its single panels (none, there) are perturbed."""
        assert len(examples()) == 8
        assert _compiled(rec.graph, rec.name) == _compiled(
            _perturb(rec.graph, single_panel_only=True), rec.name)

    def test_the_mosaic_example_does_read_them(self):
        """The known positive for the exemption above: perturbed as a single
        panel is, the eighth Example's 3x2 block compiles differently, so the
        `single_panel_only` exemption is what keeps it out, not a key the
        compile ignores everywhere."""
        rec = next(r for r in examples() if r.id == "example-m31-mosaic")
        assert _compiled(rec.graph, rec.name) != _compiled(
            _perturb(rec.graph), rec.name)

    @pytest.mark.parametrize("key", sorted(PERTURBED["target"]))
    def test_a_stored_s2_lane_compiles_the_same_whatever_each_mosaic_key_says(
            self, key):
        """Per key, so a later task that reads one for a single panel takes
        it out deliberately."""
        g = _s2_lane()
        assert g.validation_errors() == []
        assert _compiled(g, "s2") == _compiled(_perturb(g, key), "s2")

    @pytest.mark.parametrize("ntype,key", sorted(
        (t, k) for t, keys in RETIRED.items() for k in keys))
    def test_each_retired_key_changes_the_compile(self, ntype, key):
        """The retirement's known positive: each key taken out of PERTURBED
        is one the compile now reads for a single panel, so setting it on
        the stored S2 lane changes the compile.

        Mutant 'counts ignored' (in a private scratch copy of compile.py,
        `count_mode_of` returning "attempts" whatever it is given), observed:

            E  assert '{"automation": {}, "instructions": [], "name": "s2",
               ...' != '{"automation": {}, "instructions": [], ...'
            FAILED ...test_each_retired_key_changes_the_compile[pool-counts]
            FAILED ...test_each_retired_key_changes_the_compile[target-counts]
            2 failed, 23 passed, 63 deselected
        """
        g = _s2_lane()
        only_this = {ntype: {key: RETIRED[ntype][key]}}
        assert _compiled(g, "s2") != _compiled(
            _perturb(g, key, table=only_this), "s2")


# ============== control: the new pass outputs change no Example's or wizard's plan

def _s3_vocabulary(monkeypatch) -> None:
    """The vocabulary as S3 left it: AUTOFOCUS and GUIDE without ``pass``,
    and ``compile.PASS_TYPES`` read off that vocabulary (it is computed once,
    at import, so it is patched with it)."""
    for t in ("autofocus", "guide"):
        d = NODE_DEFS[t]
        monkeypatch.setitem(NODE_DEFS, t, dataclasses.replace(
            d, outs=tuple(p for p in d.outs if p.id != "pass")))
    monkeypatch.setattr(flows_compile, "PASS_TYPES",
                        frozenset({"capture", "cycle"}))


#: Every wizard kind, each with every automation chip and with none, and the
#: quick flow, all with typed coordinates so no catalogue search runs.
_NGC7331 = ("22h 37m 04s", "+34 24 56")
_FIELD = RigFacts(fov_deg=(2.0, 1.33), has_rotator=True)


def _wizard_graphs() -> dict[str, FlowGraph]:
    everything = set(wizard.AUTOMATION_OPTIONS)
    out: dict[str, FlowGraph] = {}
    for opts, tag in ((everything, "all"), (set(), "none")):
        out[f"deep-sky-{tag}"] = wizard.generate(
            wizard.KIND_DEEP_SKY, opts, "NGC 7331", coords=_NGC7331)
        out[f"eaa-{tag}"] = wizard.generate(
            wizard.KIND_EAA, opts, "NGC 7331", coords=_NGC7331)
        out[f"pool-{tag}"] = wizard.generate(
            wizard.KIND_POOL, opts, "M16, M17, NGC 6946")
        out[f"mosaic-{tag}"] = wizard.generate(
            wizard.KIND_MOSAIC, opts, "NGC 7331", coords=_NGC7331, rows=2,
            cols=3, angle_mode=wizard.ROTATE_TO_PA, pa_deg=30.0, rig=_FIELD)
    out["quick"] = wizard.quick(
        {"name": "NGC 7331", "ra": _NGC7331[0], "dec": _NGC7331[1]},
        filters=["L", "R", "G", "B"]).graph
    return out


class TestTheNewPassOutputsChangeNoShippedPlan:
    """CONTROL (S4, #331): giving AUTOFOCUS and GUIDE a ``pass`` output
    changes no compiled plan of any Example or of any graph the wizard
    makes. None of them draws a wire from either port, and a port nobody
    wires must mean nothing. Each is compiled twice, under this build's
    vocabulary and under S3's (``_s3_vocabulary``), and the two must be
    byte-identical.

    Checked against the tree before this task as well: a dump of
    ``compile_plan`` for all eight Examples and these nine wizard graphs,
    taken with the task's production files at their backups and again with
    them changed, was byte-identical (sha256 per graph, the scratch script
    S4-U331-mut/dump_plans.py).

    The known positive is ``test_the_harness_sees_the_vocabulary``: without
    it a swap that reached nothing would leave every case green.
    """

    @pytest.mark.parametrize("rec", examples(), ids=lambda r: r.id)
    def test_each_example_compiles_the_same(self, rec, monkeypatch):
        """Mutant of examples.py 'an Example wires its AUTOFOCUS pass to a
        NOTIFY' (a scratch copy: example-m16's AUTOFOCUS given a wire from
        'pass done' into its NOTIFY's 'do', which this build emits as a rule
        and S3's vocabulary cannot read at all), observed:
            E   assert '{"automation...d": "n7"}]}]}' == '{"automation...d": "n7"}]}]}'
            E     - action": "notify", "to_port": "do", "when": "autofocus.pass"},
                  {"action": "holdresume", "threshold": 40, "to_port": "pause", ...
            FAILED ...test_each_example_compiles_the_same[example-m16]
            (1 failed, 17 passed)
        """
        now = _compiled(rec.graph, rec.name)
        _s3_vocabulary(monkeypatch)
        assert _compiled(rec.graph, rec.name) == now

    @pytest.mark.parametrize("key", sorted(_wizard_graphs()))
    def test_each_wizard_graph_compiles_the_same(self, key, monkeypatch):
        graph = _wizard_graphs()[key]
        assert graph.validation_errors() == [], key
        now = _compiled(graph, key)
        _s3_vocabulary(monkeypatch)
        assert _compiled(graph, key) == now

    def test_the_harness_sees_the_vocabulary(self, monkeypatch):
        """An AUTOFOCUS whose 'pass done' feeds a NOTIFY: emitted as a rule
        on this build, and under S3's vocabulary not an event wire at all
        (the instructions pass reads a wire's kind off its source port, and
        S3's AUTOFOCUS has no such port), so it compiles to nothing. The
        swap reaches the compile.

        Mutant 'the swap reaches nothing' (``_s3_vocabulary``'s body made
        ``return``), observed:
            E   assert '{"automation": {}, "instructions": [{"action": "notify",
                "to_port": "do", "when": "autofocus.pass"}], "name": "af", ...'
                != '{"automation": {}, "instructions": [{"action": "notify", ...'
            FAILED ...test_the_harness_sees_the_vocabulary
        """
        g = _pass_lane("autofocus", to="n", to_port="do")
        now = _compiled(g, "af")
        _s3_vocabulary(monkeypatch)
        assert _compiled(g, "af") != now
