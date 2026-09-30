"""A flow lane runs once: a drawn loop is refused, not silently deleted (#149).

THE DEFECT. ``flow_order`` walks the flow lane with Kahn's algorithm, and a
cycle simply stops the walk: every node in the loop is dropped. Its docstring
said a loop could not be drawn. It can -- one wire per input does not stop a
back-edge, and the editor's replace-on-drop turns FILTER CYCLE ``complete`` ->
TARGET ``arm`` into exactly that, by swapping out the TARGET's dusk wire.
``validation_errors`` had no cycle check, so the save succeeded, the compile
held no frames for any looped stage, and the doctor -- which reasons along
wires, and every wire was still there -- had nothing to say.

THE RULE. ``FlowGraph.validation_errors`` reports every flow back-edge a
deterministic DFS finds, as ``FLOW_LOOP_REFUSAL`` filled with the two nodes'
labels. It already sits on every path that matters: ``store.save`` (422 via
``_persist_flow``), ``/run``, and both compile routes (``structural``). EVENT
wires are exempt: an event means "whenever", and REPORT ``done`` -> POOL
``advance`` pointing backwards is how a campaign loops.
"""
from __future__ import annotations

import itertools
import json

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.config as config_mod
from astrodeck.config import ConfigStore
from astrodeck.flows import wizard as flow_wizard
from astrodeck.flows.examples import examples
from astrodeck.flows.models import FLOW_LOOP_REFUSAL, FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.store import FlowStore
from astrodeck.persist import ensure_dir

LOOP = FLOW_LOOP_REFUSAL.format(src="FILTER CYCLE", dst="TARGET")


@pytest.fixture
def client(tmp_path, monkeypatch):
    """test_flows_routes' isolation: config store, CONFIG_DIR, CAPTURE_DIR and
    the flow store singleton."""
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(app_module, "flow_store", FlowStore(tmp_path / "flows"))
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c


def _e(a, ap, b, bp) -> FlowEdge:
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def _quick() -> FlowGraph:
    """The rig's own shape: the quick wizard's FILTER CYCLE night."""
    return flow_wizard.quick(
        {"name": "NGC 7331", "ra": "22h 37m 05s", "dec": "+34 24 56"},
        subs_per_filter=15, filters=["L", "R", "G", "B"],
        wheel=["L", "R", "G", "B"]).graph


def _drawn_loop() -> FlowGraph:
    """What replace-on-drop makes of a circle: the operator drags FILTER CYCLE
    ``complete`` onto TARGET ``arm``, and the editor REPLACES the arm input's
    existing wire (from DUSK) with it. One wire per input, and a loop."""
    g = _quick()
    cycle = next(n for n in g.nodes if n.type == "cycle")
    target = next(n for n in g.nodes if n.type == "target")
    edges = [e for e in g.edges if not (e.to == target.id and e.toPort == "arm")]
    assert len(edges) == len(g.edges) - 1, "the dusk wire was not there to replace"
    edges.append(_e(cycle.id, "complete", target.id, "arm"))
    return g.model_copy(update={"edges": edges})


def _dump(g: FlowGraph) -> dict:
    return g.model_dump(by_alias=True)


def _pre_s0_file(directory, fid: str, g: FlowGraph):
    """The looped flow as a build before S0 saved it -- it was legal then."""
    ensure_dir(directory)
    (directory / f"{fid}.json").write_text(json.dumps({
        "schema_version": 2, "id": fid,
        "flow": {"id": fid, "name": "looped", "graph": _dump(g)}}),
        encoding="utf-8")


class TestTheRefusal:
    def test_the_sentence(self):
        """T2 mirrors this literal in the editor's drop resolvers and T6 pins
        the two together; this pins the server's half.

        RED under mutant "reword the refusal":

            AssertionError: assert 'this flow lo...uns only once' ==
            'this flow lo...ane runs once'
        """
        assert FLOW_LOOP_REFUSAL == (
            "this flow loops back on itself at {src} -> {dst}; "
            "a flow lane runs once")

    def test_the_drawn_loop_is_a_structural_error_naming_both_nodes(self):
        """RED under mutant "remove the check":

            AssertionError: assert 'this flow loops back on itself at FILTER
            CYCLE -> TARGET; a flow lane runs once' in []
        """
        assert LOOP in _drawn_loop().validation_errors()

    def test_every_back_edge_is_reported(self):
        """ALL at once, the way ``validation_errors`` reports everything: a
        client that fixes one loop should not have to re-post to learn of the
        next.

        RED under mutant "stop at the first back-edge":

            AssertionError: assert ['this flow l...ne runs once'] ==
            ['this flow l...ne runs once']
              Right contains one more item: 'this flow loops back on itself at
              CAPTURE LOOP -> SLEW + CENTER; a flow lane runs once'
        """
        g = FlowGraph(
            nodes=[FlowNode(id="t", type="target", x=0, y=0),
                   FlowNode(id="y", type="cycle", x=200, y=0),
                   FlowNode(id="s", type="slew", x=0, y=300),
                   FlowNode(id="c", type="capture", x=200, y=300)],
            edges=[_e("t", "target", "y", "run"), _e("y", "complete", "t", "arm"),
                   _e("s", "centered", "c", "run"), _e("c", "complete", "s", "run")])
        loops = [m for m in g.validation_errors() if "loops back" in m]
        assert loops == [
            FLOW_LOOP_REFUSAL.format(src="FILTER CYCLE", dst="TARGET"),
            FLOW_LOOP_REFUSAL.format(src="CAPTURE LOOP", dst="SLEW + CENTER")]

    def test_the_walk_follows_the_canvas_not_the_list(self):
        """Deterministic by CANVAS position (roots in x, y order, then the
        leftmost unvisited node), the same tie-break ``flow_order`` uses, so
        the sentence names the wire that closes the circle back to its
        leftmost stage however the node list happens to be ordered.

        RED under mutant "start from unvisited nodes in list order":

            AssertionError: the loop was reported at a wire that depends on
            list order
            assert 'this flow loops back on itself at FILTER CYCLE -> TARGET;
            a flow lane runs once' in ['this flow loops back on itself at
            GUIDE -> FILTER CYCLE; a flow lane runs once']
        """
        g = _drawn_loop()
        flipped = g.model_copy(update={"nodes": list(reversed(g.nodes))})
        for graph in (g, flipped):
            loops = [m for m in graph.validation_errors() if "loops back" in m]
            assert LOOP in loops, (
                "the loop was reported at a wire that depends on list order")
            assert len(loops) == 1, loops

    def test_control_an_input_wired_twice_is_not_called_a_loop(self):
        """CONTROL for the colour rule: a second wire into a stage the walk has
        already FINISHED is a cross edge, not a loop. The graph is still
        refused (fan-in), but the sentence must not claim a circle that is not
        there.

        RED under mutant "any visited node closes a loop" (GREY or BLACK):

            AssertionError: a cross edge was reported as a loop
            assert not ['this flow loops back on itself at DUSK WINDOW ->
            TARGET; a flow lane runs once']
        """
        g = FlowGraph(
            nodes=[FlowNode(id="a", type="dusk", x=0, y=0),
                   FlowNode(id="t", type="target", x=200, y=0),
                   FlowNode(id="b", type="dusk", x=0, y=300)],
            edges=[_e("a", "window", "t", "arm"), _e("b", "window", "t", "arm")])
        errors = g.validation_errors()
        assert "input target.arm is wired twice" in errors
        assert not [m for m in errors if "loops back" in m], (
            "a cross edge was reported as a loop")


class TestEveryDoorRefusesIt:
    def test_save_refuses_it_with_422(self, client):
        """The recorded RED is the #149 defect itself.

        RED under mutant "remove the check":

            Failed: a drawn loop SAVED (200). Its compile has no frames for the
            looped stages (plan targets: []) and the doctor said nothing
            (issues: []).
        """
        r = client.post("/api/flows",
                        json={"flow": {"name": "looped", "graph": _dump(_drawn_loop())}})
        if r.status_code != 422:
            fid = r.json()["id"]
            comp = client.post(f"/api/flows/{fid}/compile").json()
            pytest.fail(
                f"a drawn loop SAVED ({r.status_code}). Its compile has no "
                f"frames for the looped stages (plan targets: "
                f"{comp['plan']['targets']}) and the doctor said nothing "
                f"(issues: {[i['text'] for i in comp['issues']]}).")
        assert "FILTER CYCLE -> TARGET" in r.json()["detail"]["detail"]

    def test_put_refuses_it_with_422_and_keeps_the_good_flow(self, client):
        """RED under mutant "remove the check":

            AssertionError: PUT replaced a runnable flow with a loop
            assert 200 == 422
        """
        good = client.post("/api/flows",
                           json={"flow": {"name": "fine", "graph": _dump(_quick())}})
        assert good.status_code == 200, good.text
        fid = good.json()["id"]
        r = client.put(f"/api/flows/{fid}",
                       json={"flow": {"name": "fine", "graph": _dump(_drawn_loop())}})
        assert r.status_code == 422, "PUT replaced a runnable flow with a loop"
        assert "FILTER CYCLE -> TARGET" in r.json()["detail"]["detail"]
        stored = client.get(f"/api/flows/{fid}").json()["graph"]["edges"]
        assert len(stored) == len(_quick().edges)

    def test_run_refuses_a_loop_saved_before_s0(self, client, tmp_path):
        """A pre-S0 build saved loops happily, so they are on disk. ``/run``
        refuses them by name.

        RED under mutant "remove the check" (the run falls through to the
        compile, which finds nothing to run and says something else):

            AssertionError: assert 'FILTER CYCLE -> TARGET' in 'this flow has
            no target the run could point at - add a TARGET node with
            coordinates, or a POOL whose members are catalogue names'
        """
        _pre_s0_file(tmp_path / "flows", "old-loop", _drawn_loop())
        r = client.post("/api/flows/old-loop/run", json={"accept_unmapped": True})
        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "invalid_graph"
        assert "FILTER CYCLE -> TARGET" in detail["detail"]

    def test_both_compile_routes_list_it_under_structural(self, client, tmp_path):
        """RED under mutant "remove the check":

            AssertionError: the compile said nothing structural about a loop
            assert 'this flow loops back on itself at FILTER CYCLE -> TARGET; a
            flow lane runs once' in []
        """
        draft = client.post("/api/flows/compile",
                            json={"graph": _dump(_drawn_loop()), "name": "d"})
        assert draft.status_code == 200, draft.text
        assert LOOP in draft.json()["structural"], (
            "the compile said nothing structural about a loop")
        _pre_s0_file(tmp_path / "flows", "old-loop", _drawn_loop())
        stored = client.post("/api/flows/old-loop/compile")
        assert LOOP in stored.json()["structural"]

    def test_renaming_its_folder_still_works(self, tmp_path, monkeypatch):
        """A loop saved before S0 is refused where it would RUN, not where it
        is merely filed. Re-parenting does not touch the graph, so a folder
        rename must not trip over it halfway through the folder -- the route
        catches only ReadOnlyFlow, so the ValueError would be a 500 with some
        flows moved and some not.

        RED under mutant "rename through save()" (the pre-S0 shape):

            ValueError: this flow loops back on itself at FILTER CYCLE ->
            TARGET; a flow lane runs once
        """
        monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
        store = FlowStore(tmp_path / "flows")
        _pre_s0_file(store.dir, "old-loop", _drawn_loop())
        raw = json.loads((store.dir / "old-loop.json").read_text(encoding="utf-8"))
        raw["flow"]["folder"] = "Winter"
        (store.dir / "old-loop.json").write_text(json.dumps(raw), encoding="utf-8")
        assert store.rename_folder("Winter", "Spring") == 1
        assert store.get("old-loop").folder == "Spring"
        # Still refused where it matters.
        assert LOOP in store.get("old-loop").graph.validation_errors()


class TestWhatStaysLegal:
    """RED under mutant "check every edge kind" (the event wire counts), on the
    campaign example and on the backward event wire below:

        AssertionError: Campaign - best of 4, month-scale: ['this flow loops
        back on itself at SESSION REPORT -> TARGET POOL; a flow lane runs once']

    The other six examples and the wizard matrix stay green under it (none
    carries a backward event wire), so each is a control against a check that
    refuses a legal graph, not a detector for that mutant.
    """

    @pytest.mark.parametrize("ex", examples(), ids=lambda e: e.id)
    def test_every_shipped_example_validates(self, ex):
        assert ex.graph.validation_errors() == [], (
            f"{ex.name}: {ex.graph.validation_errors()}")

    def test_the_whole_wizard_option_matrix_validates(self):
        """Every kind x every subset of the six chips (3 x 64 graphs), plus the
        quick flow with and without a wheel and the guider.

        No mutant of the loop check reaches this today: every wizard flow lane
        is a chain, and a chain has no back-edge in any walk order. It is the
        control for the day a generator draws something else -- the mosaic
        slice's wizard adds the loop wire, an EVENT wire, which is precisely
        what "check every edge kind" would refuse. Shown able to fail under the
        vocabulary mutant "CAPTURE LOOP frame becomes a flow port" (nodes.py):

            AssertionError: assert [('Deep-sky t...ents)']), ...] == []
              Left contains 97 more items, first extra item: ('Deep-sky
              target', ('HFR watchdog',), ["Flow output can't feed an event
              input (capture.frame \u2192 condition.events)"])
        """
        opts = flow_wizard.AUTOMATION_OPTIONS
        bad = []
        for kind in flow_wizard.KINDS:
            for k in range(len(opts) + 1):
                for combo in itertools.combinations(opts, k):
                    g = flow_wizard.generate(kind, combo, "M 31")
                    if g.validation_errors():
                        bad.append((kind, combo, g.validation_errors()))
        # The unguided Ha case holds its sub at 90 s. Since H4 (#518) an
        # unguided quick flow at or past the unguided line (120 s) is refused,
        # and Ha's 180 s default is past it, so this case, which passed no
        # exposures before H4, raised the ValueError instead of building a
        # graph. The graph under test is the same shape either way.
        for filters, guided, exposures in ((["L", "R"], True, None),
                                           ([], False, None),
                                           (["Ha"], False, {"Ha": 90})):
            g = flow_wizard.quick({"name": "M 31", "ra": "00h 42m 44s",
                                   "dec": "+41 16 09"}, subs_per_filter=3,
                                  filters=filters, exposures_s=exposures,
                                  guided=guided).graph
            if g.validation_errors():
                bad.append(("quick", filters, g.validation_errors()))
        assert bad == []

    def test_a_backward_event_wire_is_legal(self):
        """REPORT ``done`` -> POOL ``advance`` points backwards across the
        whole lane, and it is the campaign's loop. An event means "whenever",
        so it closes no flow circle.

        RED under mutant "check every edge kind":

            AssertionError: assert ['this flow l...ne runs once'] == []
              Left contains one more item: 'this flow loops back on itself at
              SESSION REPORT -> TARGET POOL; a flow lane runs once'
        """
        g = FlowGraph(
            nodes=[FlowNode(id="p", type="pool", x=0, y=0),
                   FlowNode(id="s", type="slew", x=200, y=0),
                   FlowNode(id="c", type="capture", x=400, y=0),
                   FlowNode(id="r", type="report", x=600, y=0)],
            edges=[_e("p", "target", "s", "run"), _e("s", "centered", "c", "run"),
                   _e("c", "complete", "r", "session"),
                   _e("r", "done", "p", "advance")])
        assert g.validation_errors() == []
