"""FLOW_SCHEMA 4: the counts notice on read, and the version a save stamps
(#189 Revision 2 ruling 2, spec 3.6, 6.16).

THE READ NEVER CHANGES WHAT A FLOW MEANS. A TARGET or POOL saved before the
mosaic slice has no ``counts`` key, which means "Every sub taken": it counts
rejected frames toward its quota. The owner ruled "Only count accepted
frames", and the save is where that happens (``save_rules``). Until then
every read says ruling 2's line and leaves the value alone, however many
times the flow is opened or run.

THE STAMP IS THE LOWEST VERSION THAT READS THE FILE RIGHT. ``save()`` stamps
4 when the graph uses a meaning a v3 build would misread (``schema_for``), so
a build from S0 to S2 refuses it loudly instead of counting every sub again,
and 3 otherwise, so that build can still open a flow it reads correctly.
Every save switches ``counts``, so in practice a flow with a TARGET or POOL
stamps 4.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.config as config_mod
from astrodeck.config import ConfigStore
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode, FlowRecord
from astrodeck.flows.store import (
    COUNTS_NOTE, FLOW_SCHEMA, ROTATION_234_NOTE, V3_SCHEMA, FlowStore,
    NewerSchemaFlow, schema_for)
from astrodeck.persist import ensure_dir

COUNTS = {"key": "counts", "note": COUNTS_NOTE}
ROTATION = {"key": "rotation", "note": ROTATION_234_NOTE}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    return FlowStore(tmp_path / "flows")


def _graph(*, counts=None, rotation=-1, pool_counts=None) -> dict:
    """dusk -> target -> capture, typed coordinates, as a build before S3
    wrote it: no ``counts`` key unless one is asked for. With
    ``pool_counts`` a POOL is added as well."""
    target = {"name": "NGC 7129", "ra": "21h 42m 30s", "dec": "+66 06 00",
              "rotation": rotation}
    if counts is not None:
        target["counts"] = counts
    nodes = [
        {"id": "d", "type": "dusk", "x": 30, "y": 60,
         "params": {"offset": -30, "stop": "Dawn", "minAlt": 30}},
        {"id": "t", "type": "target", "x": 260, "y": 60, "params": target},
        {"id": "c", "type": "capture", "x": 490, "y": 60,
         "params": {"filter": "L", "exposure": 120, "gain": 100,
                    "bin": "1", "count": 12, "goal": 0}},
    ]
    if pool_counts is not None:
        pool = {"members": "M16, M17"}
        if pool_counts != "missing":
            pool["counts"] = pool_counts
        nodes.append({"id": "p", "type": "pool", "x": 260, "y": 300,
                      "params": pool})
    return {"nodes": nodes, "edges": [
        {"from": "d", "fromPort": "window", "to": "t", "toPort": "arm"},
        {"from": "t", "fromPort": "target", "to": "c", "toPort": "run"}]}


def _put_file(directory, fid: str, schema_version, graph: dict):
    ensure_dir(directory)
    path = directory / f"{fid}.json"
    path.write_text(json.dumps({"schema_version": schema_version, "id": fid,
                                "flow": {"id": fid, "name": f"flow {fid}",
                                         "folder": "My flows",
                                         "graph": graph}}),
                    encoding="utf-8")
    return path


def _notes(record: FlowRecord) -> list[dict]:
    return [n.model_dump() for n in record.migrated]


def _counts(record: FlowRecord, node_id: str = "t"):
    return record.graph.node(node_id).params.get("counts", "<missing>")


# ================================================================ the version

class TestTheVersion:
    def test_the_schema_is_4(self):
        """Moved from test_flows_schema_v3's ``test_the_schema_is_3``.

        RED under mutant "FLOW_SCHEMA left at 3", observed:

            >       assert FLOW_SCHEMA == 4
            E       assert 3 == 4
        """
        assert FLOW_SCHEMA == 4
        assert V3_SCHEMA == 3

    def test_a_v5_file_is_a_row_and_never_a_record(self, store):
        """A newer build's file is listed, refused by ``get`` and never
        overwritten (#153), one version up from this build.

        RED under mutant "the future starts at 6" (``_migrate`` refuses
        ``version > FLOW_SCHEMA + 1``), observed:

            >       assert [r.id for r in store.load_all() if r.id == "f5"] == []
            E       AssertionError: assert ['f5'] == []
            E         Left contains one more item: 'f5'
        """
        path = _put_file(store.dir, "f5", 5, _graph(counts="Accepted subs"))
        before = path.read_bytes()
        assert [r.id for r in store.load_all() if r.id == "f5"] == []
        (row,) = store.unreadable()
        assert row["id"] == "f5"
        assert row["unreadable"] == ("saved by a newer AstroDeck (schema 5); "
                                     "update to open it")
        with pytest.raises(KeyError):
            store.get("f5")
        with pytest.raises(NewerSchemaFlow):
            store.save(FlowRecord(id="f5", name="over it"))
        assert path.read_bytes() == before

    def test_control_a_v4_file_opens(self, store):
        """CONTROL: this build's own version is a record, not a row."""
        _put_file(store.dir, "f4", 4, _graph(counts="Accepted subs"))
        assert store.unreadable() == []
        assert _notes(store.get("f4")) == []


# ================================================================ the read

class TestTheCountsNoticeOnRead:
    def test_the_note_is_ruling_2s_sentence(self):
        """Verbatim from Revision 2, ruling 2: the editors show it while any
        TARGET or POOL counts every sub taken.

        RED under mutant "reword the note" ("rejected subs included"),
        observed:

            E       AssertionError: assert 'This flow co... switches it.' == 'This
                    flow co... switches it.'
            E         -  rejected ones include
            E         ?           ---
            E         +  rejected subs include
            E         ?            +++
        """
        assert COUNTS_NOTE == (
            "This flow counts every sub taken, rejected ones included. New "
            "flows count accepted subs only, and saving this flow switches "
            "it.")

    def test_a_v3_file_with_no_counts_reads_as_attempts_with_the_note_until_saved(
            self, store):
        """The spec's S3 test. Two reads both carry the note and leave the
        value missing (the missing-key default, "Every sub taken"); the file
        is byte-identical after them. The save switches it: the file is v4,
        the TARGET counts accepted subs, and the next read is quiet.

        RED under mutant "switch on load" (``_migrate`` writes "Accepted
        subs" into every TARGET and POOL that counts attempts), observed:

            E           AssertionError: a read switched counts: loading must not
                        change what a flow means
            E           assert 'Accepted subs' == '<missing>'
            E             - <missing>
            E             + Accepted subs
        """
        path = _put_file(store.dir, "f1", 3, _graph())
        before = path.read_bytes()
        first, second = store.get("f1"), store.get("f1")
        for rec in (first, second):
            assert _counts(rec) == "<missing>", (
                "a read switched counts: loading must not change what a "
                "flow means")
            assert _notes(rec) == [COUNTS]
        assert path.read_bytes() == before, "a read wrote the file"

        store.save(second)
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["schema_version"] == 4
        target = next(n for n in raw["flow"]["graph"]["nodes"]
                      if n["id"] == "t")
        assert target["params"]["counts"] == "Accepted subs"
        assert _notes(store.get("f1")) == []

    def test_a_pool_that_counts_attempts_raises_the_note_too(self, store):
        _put_file(store.dir, "f1", 3,
                  _graph(counts="Accepted subs", pool_counts="missing"))
        assert _notes(store.get("f1")) == [COUNTS]

    def test_the_note_is_not_a_version_step(self, store):
        """A v4 file can hold "Every sub taken" (a hand edit, or a writer
        that went around the store), and it still counts attempts, so the
        read still says so.

        RED under mutant "note gated on the version" (the check runs only
        for files older than 4), observed:

            >       assert _notes(rec) == [COUNTS]
            E       AssertionError: assert [] == [{'key': 'cou...witches it.'}]
            E         Right contains one more item: {'key': 'counts', 'note': 'This
                      flow counts every sub taken, rejected ones included. New flows
                      count accepted subs only, and saving this flow switches it.'}
        """
        _put_file(store.dir, "f1", 4, _graph(counts="Every sub taken"))
        rec = store.get("f1")
        assert _counts(rec) == "Every sub taken"
        assert _notes(rec) == [COUNTS]

    def test_control_a_flow_that_counts_accepted_subs_is_quiet(self, store):
        _put_file(store.dir, "f1", 3,
                  _graph(counts="Accepted subs", pool_counts="Accepted subs"))
        assert _notes(store.get("f1")) == []

    def test_control_a_flow_with_no_target_or_pool_is_quiet(self, store):
        graph = _graph()
        graph["nodes"] = [n for n in graph["nodes"] if n["id"] != "t"]
        graph["edges"] = []
        _put_file(store.dir, "f1", 3, graph)
        assert _notes(store.get("f1")) == []

    def test_a_v2_23_4_file_says_both_notes_in_order(self, store):
        """The 23.4 step is unchanged; the counts note follows it."""
        _put_file(store.dir, "f1", 2, _graph(rotation=23.4))
        rec = store.get("f1")
        assert rec.graph.node("t").params["rotation"] == -1
        assert _notes(rec) == [ROTATION, COUNTS]


class TestTheBookkeepingWritersKeepTheVersion:
    """Carry-over 1 still holds at FLOW_SCHEMA 4: a run's ``touch_run`` and
    the folder verbs edit the raw file, so a v3 file stays v3 with its note,
    and a v4 file stays v4.

    RED under mutant "touch_run goes through save" (``touch_run`` calls
    ``self.save(record.model_copy(update=update))``), observed:

        E       AssertionError: a run stamped the file current and switched its
                counts before the operator saved
        E       assert 4 == 3
    """

    def test_a_v3_file_that_counts_attempts_stays_v3_and_keeps_the_note(
            self, store):
        path = _put_file(store.dir, "f1", 3, _graph())
        assert store.touch_run("f1", ts=1234.0, result="ok") is True
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["schema_version"] == 3, (
            "a run stamped the file current and switched its counts before "
            "the operator saved")
        assert "counts" not in raw["flow"]["graph"]["nodes"][1]["params"]
        assert _notes(store.get("f1")) == [COUNTS]

    def test_a_v4_file_stays_v4(self, store):
        path = _put_file(store.dir, "f1", 4, _graph(counts="Accepted subs"))
        assert store.touch_run("f1", ts=1234.0, result="ok") is True
        assert store.rename_folder("My flows", "Winter") == 1
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["schema_version"] == 4
        assert raw["flow"]["folder"] == "Winter"


# ================================================================ the stamp

def _mosaic_graph(**target) -> FlowGraph:
    params = {"name": "M31 3x2", "ra": "00h 42m 44s", "dec": "+41 16 09",
              "rotation": 30, "angle": "Rotate to PA", "counts": "Every sub taken"}
    params.update(target)
    return FlowGraph(nodes=[
        FlowNode(id="t", type="target", x=0, y=0, params=params),
        FlowNode(id="c", type="cycle", x=200, y=0, params={})])


def _loop(from_port: str = "pass", to_node: str = "t",
          to_port: str = "next") -> FlowEdge:
    return FlowEdge(**{"from": "c", "fromPort": from_port, "to": to_node,
                       "toPort": to_port})


class TestTheStamp:
    """``schema_for`` finds each v4 meaning on its own; with none it is 3.

    RED under mutant "always 4" (``schema_for`` returns FLOW_SCHEMA),
    observed:

        test_a_single_target_counting_attempts_stamps_3 and
        test_a_flow_with_no_block_stamps_3 (the ``where`` lines left out):

            E       AssertionError: assert 4 == 3

        test_the_save_stamps_what_schema_for_says, on the flow with no block:

            E           AssertionError: p
            E           assert 4 == 3
    """

    @pytest.mark.parametrize("graph", [
        pytest.param(_mosaic_graph(rows=2, cols=3), id="multi-panel block"),
        pytest.param(_mosaic_graph(angle="Camera fixed at PA"),
                     id="camera fixed at PA"),
        pytest.param(_mosaic_graph(counts="Accepted subs"), id="accepted subs"),
        pytest.param(_mosaic_graph().model_copy(
            update={"settings": {"whenWaiting": "Wait for the mosaic"}}),
            id="wait for the mosaic"),
        pytest.param(_mosaic_graph().model_copy(update={"edges": [_loop()]}),
                     id="loop wire"),
    ])
    def test_each_v4_meaning_stamps_4(self, graph):
        """One case per meaning, so that forgetting any one of them is red on
        its own case.

        RED under each of five mutants, "<meaning> ignored" (that meaning's
        ``found.append`` in ``_v4_meanings`` replaced by ``pass``): only the
        case of that id fails, each with (the ``where`` line left out):

            >       assert schema_for(graph) == 4
            E       AssertionError: assert 3 == 4

        "loop wire ignored" also turns
        ``test_a_pass_wire_that_is_not_the_loop_still_stamps_4`` red, and
        "accepted subs ignored" the POOL case and every test that reads a
        saved file's version.
        """
        assert schema_for(graph) == 4

    def test_the_stamps_values_are_the_vocabularys(self):
        """``schema_for`` matches two option values by their words. Both are
        stored verbatim in saved flows and never reworded, and this pins the
        store's copies to the vocabulary, so a reword on one side cannot
        leave the stamp looking for a word no flow holds.

        RED under mutant "the store's angle reworded" (``_V4_ANGLE =
        "Camera fixed"``), observed (the ``where`` line left out), with the
        "camera fixed at PA" case above red beside it:

            >       assert store_mod._V4_ANGLE in TARGET_ANGLES
            E       AssertionError: assert 'Camera fixed' in ('Any angle',
                    'Rotate to PA', 'Camera fixed at PA')
        """
        import astrodeck.flows.store as store_mod
        from astrodeck.flows.models import FLOW_SETTINGS
        from astrodeck.flows.nodes import TARGET_ANGLES
        assert store_mod._V4_ANGLE in TARGET_ANGLES
        for key, value in store_mod._V4_SETTINGS.items():
            assert value in FLOW_SETTINGS[key]["options"]
            assert value != FLOW_SETTINGS[key]["default"], (
                "the default is what a v3 build does anyway")

    def test_a_pool_that_counts_accepted_subs_stamps_4(self):
        graph = FlowGraph(nodes=[FlowNode(id="p", type="pool", params={
            "counts": "Accepted subs"})])
        assert schema_for(graph) == 4

    def test_a_pass_wire_that_is_not_the_loop_still_stamps_4(self):
        """Wider than ``compile.loop_wires`` on purpose: a v3 build has no
        ``pass`` port at all, so a pass wire into anything is as unreadable
        to it as the loop wire.

        RED under mutant "only a pass -> next wire" (both ends required),
        observed:

            >       assert schema_for(graph) == 4
            E       AssertionError: assert 3 == 4
            E        +  where 3 = schema_for(FlowGraph(nodes=[..., edges=[FlowEdge(
                     id='4c33eed0ccf5', from_='c', fromPort='pass', to='k',
                     toPort='events')], settings={}))
        """
        graph = _mosaic_graph().model_copy(update={"nodes": [
            *_mosaic_graph().nodes,
            FlowNode(id="k", type="condition", x=400, y=0, params={})],
            "edges": [_loop(to_node="k", to_port="events")]})
        assert schema_for(graph) == 4

    def test_a_single_target_counting_attempts_stamps_3(self):
        """A 1x1 at a PA, "Rotate to PA" (a v3 build commands the same
        angle), counting every sub taken, on the default ``whenWaiting``."""
        graph = _mosaic_graph(rows=1, cols=1).model_copy(
            update={"settings": {"whenWaiting":
                                 "Shoot later targets, then come back"}})
        assert schema_for(graph) == 3

    def test_a_flow_with_no_block_stamps_3(self):
        assert schema_for(FlowGraph(nodes=[
            FlowNode(id="d", type="dusk", params={})])) == 3

    def test_the_save_stamps_what_schema_for_says(self, store):
        """Through the writer. A mosaic stamps 4. A flow with no TARGET or
        POOL stamps 3, because nothing in it switched (every save switches
        ``counts``, so a flow with a TARGET always stamps 4)."""
        mosaic = store.save(FlowRecord(id="m", name="mosaic",
                                       graph=_mosaic_graph(rows=2, cols=3)))
        plain = store.save(FlowRecord(id="p", name="plain", graph=FlowGraph(
            nodes=[FlowNode(id="d", type="dusk", params={})])))
        single = store.save(FlowRecord(id="s", name="single",
                                       graph=_mosaic_graph()))
        for rec, want in ((mosaic, 4), (plain, 3), (single, 4)):
            raw = json.loads((store.dir / f"{rec.id}.json")
                             .read_text(encoding="utf-8"))
            assert raw["schema_version"] == want == schema_for(rec.graph), rec.id


# ================================================================ the door

@pytest.fixture
def client(tmp_path, monkeypatch):
    """test_flows_routes' isolation, so nothing reads the real library."""
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


def test_a_put_through_the_api_switches_counts_and_retires_the_note(
        client, tmp_path):
    """The route writes through ``FlowStore.save``, so an editor's PUT of a
    flow it loaded with the note switches the flow and the next GET is
    quiet, with no route of its own doing the switch."""
    path = _put_file(tmp_path / "flows", "f1", 3, _graph())
    got = client.get("/api/flows/f1").json()
    assert got["migrated"] == [COUNTS]
    put = client.put("/api/flows/f1", json={"flow": got})
    assert put.status_code == 200, put.text
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 4
    assert raw["flow"]["graph"]["nodes"][1]["params"]["counts"] == \
        "Accepted subs"
    assert client.get("/api/flows/f1").json()["migrated"] == []
