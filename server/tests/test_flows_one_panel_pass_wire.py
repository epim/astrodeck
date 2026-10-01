"""A pass wire into a one-panel TARGET is structure, not a loss (S4
orchestrator ruling 3, #349; spec 1.4 item 4).

Spec 1.4 item 4 designed a NOTE for a ``pass`` wire into a 1x1 block: "one
panel, nothing to rotate between". S3 built the note in the doctor (M4) and
left the compile emitting the wire as a ``<type>.pass`` rule, which
``to_plan`` reports as a rule that will not run, at warn level, so ``/run``
refused until the operator accepted a loss the doctor had just called
harmless. The likely way in is a 3x2 turned back into a single target, which
keeps its loop wire.

S4 orchestrator ruling 3 picks the note: the compile consumes the wire as
structure (``compile.one_panel_pass_wires``), emits no rule for it, ``to_plan``
reports no loss, and the doctor's M4 note stands. The doctor reads the same
list, so the note and the consumption are one set.

The controls are the two pass wires that still mean nothing and are still
losses: one from a stage of ANOTHER block's lane (M4's warning, "this wire
does nothing", a stage cut off by a DOME included), and one into a port that
is not a TARGET's ``next``.

MUTANTS were run from byte backups in a private copy of ``server/`` under the
session scratchpad (``s4-compile-mut``, and the re-verifier's
``s4-compile-verify2-mut`` for the AUTOFOCUS and GUIDE cases it added), never
in the shared tree; each observed failure is quoted in the test it turned
red.
"""
from __future__ import annotations

import pytest

from astrodeck.flows import doctor
from astrodeck.flows.compile import compile_plan, one_panel_pass_wires
from astrodeck.flows.models import FlowGraph
from astrodeck.flows.to_plan import losses, to_sequence_plan
from test_flows_continue import rig, sim_rig  # noqa: F401 (fixtures)

M42 = {"name": "M42", "ra": "05h 35m 17s", "dec": "-05 23 28"}
M78 = {"name": "M78", "ra": "05h 46m 46s", "dec": "+00 00 50"}
M31_3X2 = {"name": "M31", "ra": "00h 42m 44s", "dec": "+41 16 09",
           "rows": 2, "cols": 3, "fovX": 2.0, "fovY": 1.33, "rotation": 30,
           "angle": "Rotate to PA"}

#: M4's note, as the doctor says it for the block named M42.
NOTE = ("▸ TARGET M42 - one panel, nothing to rotate between: the 'pass "
        "done' wire into 'next panel' changes nothing.")


def _node(nid: str, kind: str, x: float = 0, y: float = 0, **params) -> dict:
    return {"id": nid, "type": kind, "x": x, "y": y, "params": params}


def _capture(nid: str, filt: str = "L", x: float = 200, y: float = 0) -> dict:
    """A CAPTURE LOOP with no integration goal and no reject, so the
    compile reports nothing about it and a loss can only come from a wire."""
    return _node(nid, "capture", x, y, filter=filt, exposure=0.05, gain=100,
                 bin="1", count=4, goal=0, reject=0)


def _wire(a: str, ap: str, b: str, bp: str) -> dict:
    return {"from": a, "fromPort": ap, "to": b, "toPort": bp}


def lane() -> dict:
    """TARGET M42 (one panel) -> CAPTURE LOOP, and the capture's 'pass done'
    into the TARGET's 'next panel': the wire a 3x2 turned back into a
    single target keeps."""
    return {"nodes": [_node("t", "target", **M42), _capture("c")],
            "edges": [_wire("t", "target", "c", "run"),
                      _wire("c", "pass", "t", "next")]}


def mid_lane() -> dict:
    """TARGET M42 -> FILTER CYCLE -> CAPTURE LOOP, the wire from the CYCLE,
    which is not the tail. On a mosaic that is M12; with one panel there is
    no later panel to skip the CAPTURE on, and the note stands."""
    return {"nodes": [_node("t", "target", **M42),
                      _node("cy", "cycle", 150, plan="L 60", cycles=2),
                      _capture("c", x=300)],
            "edges": [_wire("t", "target", "cy", "run"),
                      _wire("cy", "complete", "c", "run"),
                      _wire("cy", "pass", "t", "next")]}


def beside_a_mosaic() -> dict:
    """``lane()`` beside a real 3x2 with its own loop wire, so the graph is
    scoped by wires and the loop wires are looked for at all."""
    g = lane()
    g["nodes"] += [_node("m", "target", 0, 300, **M31_3X2),
                   _node("mc", "cycle", 200, 300, plan="L 60", cycles=2)]
    g["edges"] += [_wire("m", "target", "mc", "run"),
                   _wire("mc", "pass", "m", "next")]
    return g


def from_a_stage(stage: str):
    """TARGET M42 -> CAPTURE LOOP -> ``stage``, and the ``stage``'s 'pass
    done' into the TARGET's 'next panel': an AUTOFOCUS or GUIDE, which have
    the port since S4 (#331), ending a single target's lane."""
    def build() -> dict:
        return {"nodes": [_node("t", "target", **M42), _capture("c"),
                          _node("s", stage, 300)],
                "edges": [_wire("t", "target", "c", "run"),
                          _wire("c", "complete", "s", "run"),
                          _wire("s", "pass", "t", "next")]}
    build.__name__ = f"from_{stage}"
    return build


def other_lane() -> dict:
    """CONTROL. Two single targets, each with its own CAPTURE, and M42's
    capture wired into M78's 'next panel'."""
    return {"nodes": [_node("t", "target", **M42), _capture("c"),
                      _node("t2", "target", 0, 300, **M78),
                      _capture("c2", "R", y=300)],
            "edges": [_wire("t", "target", "c", "run"),
                      _wire("t2", "target", "c2", "run"),
                      _wire("c", "pass", "t2", "next")]}


def past_a_dome() -> dict:
    """CONTROL. TARGET M42 -> CAPTURE -> DOME -> CAPTURE Ha, and the Ha
    capture's wire into M42's 'next panel'. The DOME ends the lane, so the
    Ha capture is nobody's (``owner_of``), and the wire is from another lane
    as M4 reads it."""
    return {"nodes": [_node("t", "target", **M42), _capture("c"),
                      _node("dm", "dome", 300), _capture("ha", "Ha", x=400)],
            "edges": [_wire("t", "target", "c", "run"),
                      _wire("c", "complete", "dm", "run"),
                      _wire("dm", "open", "ha", "run"),
                      _wire("ha", "pass", "t", "next")]}


def not_next() -> dict:
    """CONTROL. The capture's 'pass done' into a NOTIFY: not a TARGET's
    'next panel' at all."""
    return {"nodes": [_node("t", "target", **M42), _capture("c"),
                      _node("n", "notify", 300)],
            "edges": [_wire("t", "target", "c", "run"),
                      _wire("c", "pass", "n", "do")]}


def into_its_arm() -> dict:
    """CONTROL. The capture's 'pass done' into its own TARGET's 'arm', a
    flow input (validation refuses an event wire there; the compile routes
    still see it). The right block, the wrong port: still not 'next panel'."""
    return {"nodes": [_node("t", "target", **M42), _capture("c")],
            "edges": [_wire("t", "target", "c", "run"),
                      _wire("c", "pass", "t", "arm")]}


def _compile(raw: dict):
    g = FlowGraph.model_validate(raw)
    compiled = compile_plan(g, "one panel")
    plan, unmapped = to_sequence_plan(compiled, g, flow_id="f-349")
    return g, compiled, plan, unmapped


def _about_pass(unmapped: list[dict]) -> list[dict]:
    return [u for u in unmapped if ".pass" in u["key"] or ".pass" in u["detail"]]


class TestTheWireIsConsumed:
    @pytest.mark.parametrize("raw", [lane, mid_lane, beside_a_mosaic],
                             ids=["tail", "mid-lane", "beside-a-mosaic"])
    def test_no_rule_no_loss(self, raw):
        """The compile emits no rule for the wire, ``to_plan`` holds no entry
        for it, and nothing is left for ``/run`` to ask about.

        RED under mutant "emit the one-panel pass wire as a rule" (the
        compile's consumed set is the loop wires alone, as S3 built it),
        observed on each case, e.g. tail:

            AssertionError: assert [{'action': '...apture.pass'}] == []
              Left contains one more item: {'action': 'target', 'to_port':
              'next', 'when': 'capture.pass'}

        RED under mutant "no one-panel wires" (``one_panel_pass_wires``
        answers nothing), observed the same way on each case.
        """
        g, compiled, plan, unmapped = _compile(raw())
        assert [r for r in compiled["instructions"]
                if r["when"].endswith(".pass")] == []
        assert _about_pass(unmapped) == []
        assert losses(unmapped) == []
        assert plan.instructions == []

    @pytest.mark.parametrize("raw", [lane, mid_lane, beside_a_mosaic],
                             ids=["tail", "mid-lane", "beside-a-mosaic"])
    def test_the_doctors_note_stands(self, raw):
        """M4's note, once, and no M4 warning: the sentence that says the
        wire changes nothing is now the whole truth about it. Green on the
        code and under "emit the one-panel pass wire as a rule", which
        moves the compile and not the doctor.

        RED under mutant "no one-panel wires" (``one_panel_pass_wires``
        answers nothing), because the doctor reads the compile's list,
        observed on each case, e.g. tail:

            assert [] == [('note', '▸ ...es nothing.')]
              Right contains one more item: ('note', "▸ TARGET M42 - one
              panel, nothing to rotate between: the 'pass done' wire into
              'next panel' changes nothing.")
        """
        issues = doctor.check(FlowGraph.model_validate(raw()))
        assert [(i.level, i.text) for i in issues
                if "nothing to rotate between" in i.text] == [("note", NOTE)]
        assert not [i for i in issues if "this wire does nothing" in i.text]

    @pytest.mark.parametrize("stage", ["autofocus", "guide"])
    def test_from_an_autofocus_or_a_guide_consumed_and_noted(self, stage):
        """The compile consumes a one-panel wire from any stage with the
        port (``compile.PASS_TYPES``, which took in AUTOFOCUS and GUIDE with
        #331), so the doctor's note must follow it there: consumed with no
        rule and no loss, and noted once, not passed over in silence. The
        doctor's own ``pass_wires`` still lists only the two capture stages
        (#375), which is why M4 walks the compile's list too.

        Added by the S4-COMPILE re-verifier (2026-09-27): the note had
        followed ``pass_wires`` alone, so these wires were consumed with no
        word from the doctor, while both docstrings said the note and the
        consumption were one set.

        RED under mutant "the note walks pass_wires alone" (``doctor.py``'s
        M4 loop back to ``for e in pass_wires:``), observed on both (and on
        nothing else in this file or ``test_flows_doctor_s3.py``), e.g.
        autofocus:

            assert [] == [('note', '\\u25b8 ...es nothing.')]
              Right contains one more item: ('note', "\\u25b8 TARGET M42 -
              one panel, nothing to rotate between: the 'pass done' wire
              into 'next panel' changes nothing.")
        """
        g, compiled, _plan, unmapped = _compile(from_a_stage(stage)())
        assert [(e.from_, e.to) for e in one_panel_pass_wires(g)] == \
            [("s", "t")], "premise: the compile consumes it"
        assert compiled["instructions"] == [] and losses(unmapped) == []
        issues = doctor.check(g)
        assert [(i.level, i.text) for i in issues
                if "nothing to rotate between" in i.text] == [("note", NOTE)]

    def test_control_a_wire_the_compile_does_not_consume_is_not_noted(self):
        """CONTROL for the walk over the compile's list: an AUTOFOCUS pass
        wire into ANOTHER block's 'next panel' is not consumed (it is still
        a rule that will not run), so it draws no note. Green on the code
        and under "the note walks pass_wires alone". The doctor's warning
        for it is #375's, and not asserted here either way.

        The doctor's half cannot go red on its own: M4 tests the owner
        before the note, so a wire from another lane reaches the warning
        whatever the note's condition says. What can move is the compile's
        list, which the note walks: RED under the compile mutant "one-panel
        wires ignore the owner", observed on the premise:

            AssertionError: assert [FlowEdge(id=...oPort='next')] == []
              Left contains one more item: FlowEdge(id='e631560787f9',
              from_='af', fromPort='pass', to='t2', toPort='next')
        """
        raw = other_lane()
        raw["nodes"].append(_node("af", "autofocus", 300))
        raw["edges"] += [_wire("c", "complete", "af", "run"),
                         _wire("af", "pass", "t2", "next")]
        raw["edges"] = [e for e in raw["edges"] if e["fromPort"] != "pass"
                        or e["from"] == "af"]
        g, _compiled, _plan, unmapped = _compile(raw)
        assert one_panel_pass_wires(g) == []
        assert [u["key"] for u in losses(unmapped)] == [
            "instructions[autofocus.pass -> target]"], "premise: a loss"
        assert not [i for i in doctor.check(g)
                    if "nothing to rotate between" in i.text]

    def test_it_makes_nothing_a_cycle(self):
        """Consumed as nothing: with one panel there is no pass to rotate,
        so the M42 capture does not become a one-slot cycle the way a
        CAPTURE inside a mosaic's circle does, and the mosaic beside it
        rotates as it did.

        RED under mutant "a one-panel wire loops the block" (``looped``
        rebuilt from the consumed set, so the 1x1 counts as looped), and
        under "a loop wire into any block" (``_loop_edge_keys`` drops its
        multi-panel test), observed for both:

            AssertionError: assert 1 is None
             +  where 1 = <built-in method get of dict object at 0x...>(
             'per_visit')
        """
        _g, compiled, plan, _un = _compile(beside_a_mosaic())
        single = next(t for t in compiled["targets"] if t["node_id"] == "t")
        mosaic = next(t for t in compiled["targets"] if t["node_id"] == "m")
        assert single["steps"][0].get("per_visit") is None
        assert single["loop"] is False and mosaic["loop"] is True
        m42 = next(t for t in plan.targets if t.name == "M42")
        assert m42.acquisition == "blocks"
        assert {g.mode for g in plan.groups} == {"rotate"}

    def test_the_doctor_and_the_compile_read_one_list(self):
        """The list itself: exactly the wire, for each consumed shape, and
        nothing for a control.

        RED under mutant "one-panel wires ignore the owner", observed:

            AssertionError: other_lane
            assert [('c', 't2')] == []

        RED under mutant "one-panel wires into any port", observed:

            AssertionError: into_its_arm
            assert [('c', 't')] == []

        RED under mutant "no one-panel wires", observed:

            AssertionError: lane
            assert [] == [('c', 't')]
        """
        for raw, want in ((lane, [("c", "t")]), (mid_lane, [("cy", "t")]),
                          (beside_a_mosaic, [("c", "t")]),
                          (other_lane, []), (past_a_dome, []),
                          (not_next, []), (into_its_arm, [])):
            wires = one_panel_pass_wires(FlowGraph.model_validate(raw()))
            assert [(e.from_, e.to) for e in wires] == want, raw.__name__


class TestTheOtherPassWiresAreStillLosses:
    @pytest.mark.parametrize("raw,key", [
        (other_lane, "instructions[capture.pass -> target]"),
        (past_a_dome, "instructions[capture.pass -> target]"),
        (not_next, "instructions[capture.pass -> notify]"),
        (into_its_arm, "instructions[capture.pass -> target]")],
        ids=["another-blocks-lane", "past-a-dome", "not-next",
             "into-its-arm"])
    def test_still_a_rule_that_will_not_run(self, raw, key):
        """CONTROLS. A pass wire from a stage of another block's lane, and one
        into a port that is not a TARGET's 'next panel', are still emitted,
        still a rule that will not run, and still a loss ``/run`` asks
        about. Green on the code.

        RED under mutant "one-panel wires ignore the owner"
        (``one_panel_pass_wires`` drops its ``owner_of`` test), observed on
        another-blocks-lane and past-a-dome:

            AssertionError: assert [] == ['instruction...s -> target]']
              Right contains one more item: 'instructions[capture.pass ->
              target]'

        RED under mutant "one-panel wires into any port"
        (``one_panel_pass_wires`` drops its ``NEXT_PORT`` test and its
        TARGET test), observed on into-its-arm, the same way. not-next
        stays green under it: a NOTIFY owns no lane, so the ``owner_of``
        test alone keeps that wire a rule, and into-its-arm is the case
        where only the port says no.

        RED under mutant "every pass wire consumed silently" (to_plan's
        ``_instructions`` skips any ``.pass`` trigger), observed on all
        four, e.g. not-next:

            AssertionError: assert [] == ['instruction...s -> notify]']
              Right contains one more item: 'instructions[capture.pass ->
              notify]'
        """
        _g, _compiled, _plan, unmapped = _compile(raw())
        rows = [u for u in losses(unmapped) if "will not run" in u["detail"]]
        assert [u["key"] for u in rows] == [key]
        assert rows[0]["level"] == "warn"

    @pytest.mark.parametrize("raw", [other_lane, past_a_dome],
                             ids=["another-blocks-lane", "past-a-dome"])
    def test_m4_still_warns_for_another_lane(self, raw):
        """CONTROL. M4's warning, not its note."""
        issues = doctor.check(FlowGraph.model_validate(raw()))
        warned = [i for i in issues if "this wire does nothing" in i.text]
        assert [i.level for i in warned] == ["warn"]
        assert not [i for i in issues if "nothing to rotate between" in i.text]


class TestRun:
    async def test_run_starts_on_the_simulator_with_no_accept_flag(
            self, sim_rig):
        """``POST /api/flows/{id}/run`` with no flag at all starts the saved
        flow on the simulator: the route's own compile, nothing to accept.

        RED under mutant "emit the one-panel pass wire as a rule", and under
        "no one-panel wires", observed (the run's question is back; the
        long line cut):

            AssertionError: {"detail":{"detail":"parts of this flow do not
            survive the compile","code":"unmapped","unmapped":[{"key":
            "instructions[capture.pass -> target]","detail":"this rule will
            not run: the engine has no 'capture.pass' trigger; the engine has
            no 'target' action. It stays in the graph and in the compiled
            plan, and starts working the day the engine learns the
            trigger","level":"warn"},{"key":"cooling.set...
            assert 409 == 200
        """
        fid = await sim_rig.save_flow(lane())
        r = await sim_rig.run(fid)
        assert r.status_code == 200, r.text
        assert r.json()["started"] is True
        assert _about_pass(r.json()["unmapped"]) == []
        assert sim_rig.starts[-1].won

    async def test_control_another_lanes_wire_still_asks(self, rig):
        """CONTROL. The same route refuses the other-lane wire until it is
        accepted, naming it. Green on the code.

        RED under mutant "one-panel wires ignore the owner", and under
        "every pass wire consumed silently", observed:

            AssertionError: {"started":true,"flow_id":"...","frames":16,
            "unmapped":[],"session":{"id":"...","night":1,"continued":false,
            "kept":0,"new":4,"dropped":0}}
            assert 200 == 409
        """
        fid = await rig.save_flow(other_lane())
        r = await rig.run(fid)
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "unmapped"
        assert [u["key"] for u in losses(detail["unmapped"])] == [
            "instructions[capture.pass -> target]"]
