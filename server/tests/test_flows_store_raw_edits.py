"""The flow store's bookkeeping writers edit the file they found (carry-over 1,
#150), a list walks the directory once, and a damaged example-*.json is one
row, not two (carry-over 7, server half, #153).

RAW EDITS. ``rename_folder``, ``delete_folder`` and ``touch_run`` used to write
the MIGRATED record back through ``_write``, which stamps FLOW_SCHEMA. So
re-parenting a v2 flow, or merely running it, rewrote an inherited 23.4 to -1,
stamped the file 3, and threw away the one piece of evidence -- the file's own
version -- that let the next read say so. The operator was told nothing, or
told once in a log line they may never have seen. Now only ``save()`` stamps
FLOW_SCHEMA: the three bookkeeping writers change their own fields in the raw
JSON and nothing else, so a v2 file stays v2, and the note keeps being said
until the operator saves the flow.

ONE SCAN. ``GET /api/flows`` called ``load_all()`` and then ``unreadable()``,
two walks of the directory, each parsing and validating every file. Besides
the cost, the two halves of one response could disagree about a file written
between them.

UNIQUE IDS. The Examples were shadowed only by a READABLE file carrying their
id, so a damaged ``example-m16.json`` produced the example (from code) AND the
file's unreadable row: two list entries with one id, one of them unopenable,
and ``get`` answering with the example while ``delete`` refused the file. The
rule is now one rule, everywhere: a file on disk takes its id from the
Examples, readable or not.
"""
from __future__ import annotations

import copy
import json
from collections import Counter

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.config as config_mod
import astrodeck.flows.store as store_mod
from astrodeck.config import ConfigStore
from astrodeck.flows.examples import examples
from astrodeck.flows.models import EXAMPLES_FOLDER, MY_FLOWS_FOLDER
from astrodeck.flows.store import ROTATION_234_NOTE, FlowStore, ReadOnlyFlow
from astrodeck.persist import ensure_dir

NOTE = [{"key": "rotation", "note": ROTATION_234_NOTE}]


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    return FlowStore(tmp_path / "flows")


@pytest.fixture
def client(tmp_path, monkeypatch):
    """test_flows_routes' isolation: the config store, CONFIG_DIR, CAPTURE_DIR
    and the flow store singleton, so nothing here reads the developer's real
    library. ``client.store`` is the store the routes use."""
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path)
    flows = FlowStore(tmp_path / "flows")
    monkeypatch.setattr(app_module, "flow_store", flows)
    app = app_module.create_app()
    with TestClient(app) as c:
        c.store = flows
        yield c


def _graph(rotation) -> dict:
    """dusk -> target -> capture, node types this build knows."""
    return {
        "nodes": [
            {"id": "d", "type": "dusk", "x": 30, "y": 60,
             "params": {"offset": -30, "stop": "Dawn", "minAlt": 30}},
            {"id": "t", "type": "target", "x": 260, "y": 60,
             "params": {"name": "NGC 7129", "ra": "21h 42m 30s",
                        "dec": "+66 06 00", "rotation": rotation}},
            {"id": "c", "type": "capture", "x": 490, "y": 60,
             "params": {"filter": "L", "exposure": 120, "gain": 100,
                        "bin": "1", "count": 12, "goal": 0}},
        ],
        "edges": [
            {"from": "d", "fromPort": "window", "to": "t", "toPort": "arm"},
            {"from": "t", "fromPort": "target", "to": "c", "toPort": "run"},
        ],
    }


def _put_file(directory, fid: str, schema_version, *, rotation=-1,
              folder: str = "Winter", graph: dict | None = None):
    """A flow file the way an older (or newer) build's writer left it: every
    card field present, timestamps fixed so a change to one is visible."""
    ensure_dir(directory)
    path = directory / f"{fid}.json"
    path.write_text(json.dumps({
        "schema_version": schema_version, "id": fid,
        "flow": {"id": fid, "name": f"flow {fid}", "folder": folder,
                 "tagline": "", "readonly": False,
                 "created_ts": 1000.0, "updated_ts": 1000.0,
                 "last_run": None, "last_result": "",
                 "graph": graph if graph is not None else _graph(rotation)}}),
        encoding="utf-8")
    return path


def _read(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _rotation_on_disk(raw: dict):
    return next(n for n in raw["flow"]["graph"]["nodes"]
                if n["type"] == "target")["params"]["rotation"]


# The three bookkeeping writers, each driven the way the product drives it:
# the two folder verbs through their routes, touch_run as run_flow and the
# engine's finalize call it. Each returns the fields it is allowed to change.

def _rename(client) -> dict:
    r = client.post("/api/flows/folders",
                    json={"name": "Winter", "new_name": "Spring"})
    assert r.status_code == 200 and r.json()["moved"] == 1, r.text
    return {"folder": "Spring"}


def _delete_folder(client) -> dict:
    r = client.delete("/api/flows/folders/Winter")
    assert r.status_code == 200 and r.json()["moved"] == 1, r.text
    return {"folder": MY_FLOWS_FOLDER}


def _touch_run(client) -> dict:
    assert client.store.touch_run("f1", ts=1234.0, result="ok") is True
    return {"last_run": 1234.0, "last_result": "ok"}


EDITS = {"rename_folder": _rename, "delete_folder": _delete_folder,
         "touch_run": _touch_run}
REPARENTS = {"rename_folder", "delete_folder"}


def _expected(before: dict, after: dict, changed: dict, edit: str) -> dict:
    """``before`` with exactly the edit's own fields changed. A re-parent also
    stamps ``updated_ts`` (it moved the flow); a run never does, because a run
    is not an edit (test_run_provenance)."""
    want = copy.deepcopy(before)
    want["flow"].update(changed)
    if edit in REPARENTS:
        want["flow"]["updated_ts"] = after["flow"]["updated_ts"]
    return want


# ======================================================== (1) RAW EDITS

class TestTheBookkeepingWritersKeepTheFilesVersion:
    """Carry-over 1 (#150): only ``save()`` stamps FLOW_SCHEMA.

    RED under mutant "write the migrated record" -- both writers restored to
    ``self._write(record.model_copy(update=...))``, the code as it stood --
    for all three edits (touch_run shown; rename_folder and delete_folder fail
    on the same line with their own names):

        AssertionError: the touch_run rewrote the file: it must stay the
        version it was until the operator saves
        assert 3 == 2

    RED under mutant "no copy before _migrate" (``_record_of`` hands the raw
    dict it was given straight to ``_migrate``, which rewrites it in place,
    and the writers then write that dict) -- the file stays v2 but the
    migration has leaked into it, for all three edits:

        AssertionError: assert -1 == 23.4
         +  where -1 = _rotation_on_disk({'flow': {'created_ts': 1000.0,
         'folder': 'Winter', 'graph': {...}, 'id': 'f1', ...}, 'id': 'f1',
         'schema_version': 2})
    """

    @pytest.mark.parametrize("edit", sorted(EDITS))
    def test_a_v2_23_4_file_stays_v2_and_still_says_the_note(
            self, client, tmp_path, edit):
        path = _put_file(tmp_path / "flows", "f1", 2, rotation=23.4)
        before = _read(path)
        assert client.get("/api/flows/f1").json()["migrated"] == NOTE, (
            "premise: a v2 23.4 reads with the note")

        changed = EDITS[edit](client)

        after = _read(path)
        assert after["schema_version"] == 2, (
            f"the {edit} rewrote the file: it must stay the version it was "
            f"until the operator saves")
        assert _rotation_on_disk(after) == 23.4
        assert after == _expected(before, after, changed, edit), (
            f"the {edit} changed more than its own fields")
        if edit in REPARENTS:
            assert after["flow"]["updated_ts"] > 1000.0, "a move is a change"
        got = client.get("/api/flows/f1").json()
        assert got["migrated"] == NOTE, (
            f"the {edit} retired the note, and the operator never saw it")
        for key, value in changed.items():
            assert got[key] == value

    @pytest.mark.parametrize("edit", sorted(EDITS))
    def test_control_a_v3_file_keeps_v3_and_its_deliberate_23_4(
            self, client, tmp_path, edit):
        """CONTROL: a v3 file's 23.4 was set on purpose; the edit leaves it,
        and the file's version, exactly as they were, and the next read says
        nothing. The version and the angle survive every mutant below; what
        catches them is that the file is not otherwise untouched.

        RED under mutant "write the migrated record", for all three edits: it
        stamps 3 over a 3 and v3 does not migrate, but a trip through the
        model re-serialises the graph (an ``id`` on every edge, ``x: 490``
        becomes ``490.0``):

            AssertionError: the touch_run changed more than its own fields
            assert {'flow': {'cr...a_version': 3} == {'flow': {'cr...a_version': 3}
              Differing items:
              {'flow': {'created_ts': 1000.0, 'folder': 'Winter', 'graph':
              {'edges': [{'from': 'd', 'fromPort': 'window', 'id': '190...',
              ...}, 'type': 'capture', 'x': 490.0, ...}]}, 'id': 'f1', ...}}
              != {'flow': {..., 'x': 490, ...}}

        RED under mutant "no copy before _migrate", for all three edits,
        because ``_migrate`` writes its read-time ``migrated`` list into the
        dict it is given (the full diff's one line):

            AssertionError: the touch_run changed more than its own fields
            +         'migrated': [],
        """
        path = _put_file(tmp_path / "flows", "f1", 3, rotation=23.4)
        before = _read(path)

        changed = EDITS[edit](client)

        after = _read(path)
        assert after["schema_version"] == 3
        assert _rotation_on_disk(after) == 23.4
        assert after == _expected(before, after, changed, edit), (
            f"the {edit} changed more than its own fields")
        assert client.get("/api/flows/f1").json()["migrated"] == []

    @pytest.mark.parametrize("edit", sorted(EDITS))
    def test_the_edit_is_atomic_and_keeps_a_backup(self, client, tmp_path, edit):
        """Through ``write_json_atomic``: a staging file, fsync, replace, and a
        ``.bak`` of the bytes it replaced. A crash mid-write never leaves a
        half file where a flow was.

        RED under mutant "plain write" (``path.write_text(json.dumps(raw))``
        in ``_edit_raw``), for all three edits:

            AssertionError: the touch_run wrote the file in place: no .bak of
            what it replaced
            assert False
             +  where False = exists()
        """
        path = _put_file(tmp_path / "flows", "f1", 2, rotation=23.4)
        before = path.read_bytes()
        EDITS[edit](client)
        bak = path.with_suffix(".json.bak")
        assert bak.exists(), (
            f"the {edit} wrote the file in place: no .bak of what it replaced")
        assert bak.read_bytes() == before


class TestFilesThisBuildCannotOpenAreNeverEdited:
    """A row is read-only (#153): the bookkeeping writers leave a newer
    build's file, a file that does not parse and a record that fails
    validation byte-identical, and the Examples stay refused.

    RED under mutant "re-parent by the raw folder" (``rename_folder`` walks
    every file that parses and matches ``raw["flow"]["folder"]``, with no
    readability gate) -- all three rows moved with the good file:

        AssertionError: the rename moved a row
        assert 4 == 1
         +  where 4 = rename_folder('Winter', 'Spring')

    and for the folder delete, through the same loop:

        AssertionError: assert 4 == 1
         +  where 4 = delete_folder('Winter')

    RED under mutant "touch_run edits the file without opening it" (read the
    raw JSON at the id's path and write the run into it):

        AssertionError: touch_run wrote into 'future'
        assert True is False
    """

    def _rows(self, directory):
        """Three files in Winter this build cannot open, and one it can."""
        _put_file(directory, "good", 2, rotation=23.4)
        future = _put_file(directory, "future", 9, rotation=23.4)
        weird = _graph(-1)
        weird["nodes"][2]["type"] = "warpdrive"
        strange = _put_file(directory, "strange", 3, graph=weird)
        badver = directory / "badver.json"
        badver.write_text(json.dumps(
            {"schema_version": "three",
             "flow": {"id": "badver", "folder": "Winter"}}), encoding="utf-8")
        return {p.stem: p for p in (future, strange, badver)}

    def test_a_rename_moves_only_what_it_can_open(self, store):
        rows = self._rows(store.dir)
        before = {k: p.read_bytes() for k, p in rows.items()}
        assert {r["id"] for r in store.unreadable()} == set(rows), (
            "premise: all three are rows")
        assert store.rename_folder("Winter", "Spring") == 1, (
            "the rename moved a row")
        assert {k: p.read_bytes() for k, p in rows.items()} == before
        assert store.get("good").folder == "Spring"

    def test_a_folder_delete_moves_only_what_it_can_open(self, store):
        rows = self._rows(store.dir)
        before = {k: p.read_bytes() for k, p in rows.items()}
        assert store.delete_folder("Winter") == 1
        assert {k: p.read_bytes() for k, p in rows.items()} == before

    def test_touch_run_writes_into_no_row(self, store):
        rows = self._rows(store.dir)
        before = {k: p.read_bytes() for k, p in rows.items()}
        for fid in rows:
            assert store.touch_run(fid, ts=1.0, result="ok") is False, (
                f"touch_run wrote into {fid!r}")
        assert {k: p.read_bytes() for k, p in rows.items()} == before

    def test_control_the_examples_stay_refused(self, store):
        """CONTROL: as before -- the Examples folder is fixed, and a run of an
        example id records nothing, whatever file carrying that id is on disk:
        a READABLE one (a user file shadowing it, the case the refusal actually
        holds shut, because it opens), a damaged one, or none.

        RED under mutant "touch_run drops the example refusal" (its first
        ``if any(e.id == flow_id ...)`` removed):

            AssertionError: a run of 'example-m31' was recorded
            assert True is False
        """
        with pytest.raises(ReadOnlyFlow):
            store.rename_folder(EXAMPLES_FOLDER, "Mine")
        with pytest.raises(ReadOnlyFlow):
            store.delete_folder(EXAMPLES_FOLDER)
        readable = _put_file(store.dir, "example-m31", 3)
        damaged = store.dir / "example-m16.json"
        damaged.write_text('{"schema_version": 3, "fl', encoding="utf-8")
        before = {p: p.read_bytes() for p in (readable, damaged)}
        for fid in ("example-m31", "example-m16", "example-nb"):
            assert store.touch_run(fid, ts=1.0, result="ok") is False, (
                f"a run of {fid!r} was recorded")
        assert {p: p.read_bytes() for p in before} == before


def _retag(path, **flow_fields) -> None:
    """Change fields inside a stored file's ``flow`` object by hand, the way
    a copied or hand-edited file differs from what ``save`` writes."""
    raw = _read(path)
    raw["flow"].update(flow_fields)
    path.write_text(json.dumps(raw), encoding="utf-8")


class TestTouchRunWritesOnlyTheFlowItNames:
    """``touch_run`` opens ONE file, the one at ``flow_id``, and writes into
    it only when it holds that flow and the flow is not read-only. Both
    guards are its last ``if``; the tests above never reach either, because
    every file they run has a matching id and ``readonly: False``."""

    def test_a_read_only_file_on_disk_records_no_run(self, store):
        """``save`` refuses a read-only record, so a file carrying
        ``readonly: true`` was not written by it, and nothing may write into
        it now. The refusal was ``record.readonly or <an example id>``
        before the raw edit, and the example half alone does not cover it.

        RED under mutant "touch_run ignores readonly" (``or record.readonly``
        dropped from the last guard):

            AssertionError: touch_run wrote into a read-only flow
            assert True is False
             +  where True = touch_run('fixture', ts=1.0, result='ok')
        """
        path = _put_file(store.dir, "fixture", 3)
        _retag(path, readonly=True)
        before = path.read_bytes()
        assert store.get("fixture").readonly is True, (
            "premise: it opens, as a read-only flow")
        assert store.touch_run("fixture", ts=1.0, result="ok") is False, (
            "touch_run wrote into a read-only flow")
        assert path.read_bytes() == before
        # CONTROL: the same file without the flag is written, so the flag is
        # what refused it.
        mine = _put_file(store.dir, "mine", 3)
        assert store.touch_run("mine", ts=1.0, result="ok") is True
        assert _read(mine)["flow"]["last_run"] == 1.0

    def test_a_file_holding_another_flow_is_not_written(self, store):
        """``stray.json`` holding flow ``other`` -- a file copied by hand.
        The library lists it as ``other``; a run of ``stray`` addresses a
        file that holds a different flow, and a run of ``other`` has no file
        of that name. Neither writes, and no file appears: the old writer
        went through ``get`` and ``_path(record.id)``, so recording a run of
        ``other`` CREATED ``other.json``, a second file carrying one id.

        RED under mutant "touch_run ignores the id inside" (``record.id !=
        flow_id`` dropped from the last guard):

            AssertionError: touch_run('stray') wrote into the file of flow
            'other'
            assert True is False
             +  where True = touch_run('stray', ts=1.0, result='ok')
        """
        path = _put_file(store.dir, "stray", 3)
        _retag(path, id="other")
        before = path.read_bytes()
        assert store.get("other").name == "flow stray", (
            "premise: the library lists the file under the id inside it")
        assert store.touch_run("stray", ts=1.0, result="ok") is False, (
            "touch_run('stray') wrote into the file of flow 'other'")
        assert store.touch_run("other", ts=1.0, result="ok") is False
        assert path.read_bytes() == before
        assert sorted(p.name for p in store.dir.iterdir()) == ["stray.json"]


class TestAFileWithNoFlowObjectStillMoves:
    def test_a_rename_writes_the_folder_into_it_and_finishes_the_folder(
            self, store):
        """Every FlowRecord field has a default, so a file holding no
        ``flow`` object at all opens, as a flow in My flows. A rename of My
        flows must give it one to carry the folder, and must not raise
        partway through the folder: that is the partial move and the 500
        that ``rename_folder``'s docstring says it exists to prevent.

        RED under mutant "edit_raw assumes a flow object" (the
        ``isinstance(flow, dict)`` branch in ``_edit_raw`` deleted):

            AttributeError: 'NoneType' object has no attribute 'update'
        """
        ensure_dir(store.dir)
        bare = store.dir / "bare.json"          # sorts before "zzz"
        bare.write_text(json.dumps({"schema_version": 2, "id": "bare"}),
                        encoding="utf-8")
        later = _put_file(store.dir, "zzz", 2, rotation=23.4,
                          folder=MY_FLOWS_FOLDER)
        assert store.rename_folder(MY_FLOWS_FOLDER, "Spring") == 2
        raw = _read(bare)
        assert raw["schema_version"] == 2 and raw["id"] == "bare"
        assert raw["flow"]["folder"] == "Spring"
        assert set(raw["flow"]) == {"folder", "updated_ts"}
        after = _read(later)
        assert after["flow"]["folder"] == "Spring", (
            "the rename stopped partway through the folder")
        assert after["schema_version"] == 2 and _rotation_on_disk(after) == 23.4


# ========================================================= (2) ONE SCAN

class TestAListIsOneScan:
    def test_get_flows_walks_the_directory_once(self, client, tmp_path,
                                                monkeypatch):
        """One ``_scan`` answers both halves of the response.

        RED under mutant "route calls load_all and unreadable" (the two
        ``asyncio.to_thread`` calls ``list_flows`` made before):

            AssertionError: GET /api/flows walked the flow directory 2 times
            assert 2 == 1
        """
        flows = tmp_path / "flows"
        _put_file(flows, "good", 3)
        _put_file(flows, "future", 9)
        walks: list = []
        real = store_mod.list_json

        def counting(directory):
            walks.append(directory)
            return real(directory)

        monkeypatch.setattr(store_mod, "list_json", counting)
        listed = client.get("/api/flows").json()
        by_id = {r["id"]: r for r in listed}
        assert "good" in by_id and not by_id["good"].get("unreadable"), (
            "premise: the card half is in the response")
        assert by_id["future"]["unreadable"], (
            "premise: the row half is in the response")
        assert "example-m16" in by_id, "premise: the Examples are in it"
        assert len(walks) == 1, (
            f"GET /api/flows walked the flow directory {len(walks)} times")


# ======================================================= (3) UNIQUE IDS

DAMAGE = {
    "truncated": '{"schema_version": 3, "fl',
    "future": json.dumps({"schema_version": 9, "id": "example-m16",
                          "flow": {"id": "example-m16", "name": "M16 later",
                                   "folder": EXAMPLES_FOLDER}}),
    "unknown type": json.dumps({"schema_version": 3, "id": "example-m16",
                                "flow": {"id": "example-m16", "graph": {
                                    "nodes": [{"id": "a",
                                               "type": "warpdrive"}]}}}),
}


def _damage_m16(directory, kind: str):
    ensure_dir(directory)
    path = directory / "example-m16.json"
    path.write_text(DAMAGE[kind], encoding="utf-8")
    return path


class TestADamagedExampleFileIsOneRow:
    @pytest.mark.parametrize("kind", sorted(DAMAGE))
    def test_it_is_listed_once_as_its_row_and_get_answers_404(
            self, client, tmp_path, kind):
        """RED under mutant "exclude only readable records" (the Examples are
        shadowed by ``{r.id for r in mine}`` alone, the rule as it stood), for
        each kind of damage:

            AssertionError: example-m16 is listed 2 times
            assert 2 == 1
        """
        _damage_m16(tmp_path / "flows", kind)
        listed = [r for r in client.get("/api/flows").json()
                  if r["id"] == "example-m16"]
        assert len(listed) == 1, f"example-m16 is listed {len(listed)} times"
        assert listed[0]["unreadable"], "the one entry is the file's row"
        assert client.get("/api/flows/example-m16").status_code == 404
        assert "example-m16" not in {r.id for r in client.store.load_all()}
        with pytest.raises(KeyError):
            client.store.get("example-m16")

    def test_the_folder_counts_are_the_listed_rows(self, client, tmp_path):
        """A header counts what the grid under it shows.

        RED under mutant "folders() keeps its own rule" (the old ``folders``
        body: a scan of its own, shadowing Examples only by readable ids):

            AssertionError: assert {'Examples': ..., 'Winter': 1} ==
            {'Examples': ..., 'Winter': 1}
              Differing items:
              {'Examples': 7} != {'Examples': 6}

        RED under mutant "exclude only readable records" too, where the list
        and the headers agree on the wrong answer and only the count says so:

            AssertionError: premise: m16's file shadows it
            assert 7 == 6
        """
        _damage_m16(tmp_path / "flows", "truncated")
        _put_file(tmp_path / "flows", "good", 3, folder="Winter")
        listed = client.get("/api/flows").json()
        shown = Counter(r["folder"] for r in listed)
        counts = {f["name"]: f["count"]
                  for f in client.get("/api/flows/folders").json()}
        assert counts == dict(shown)
        assert counts[EXAMPLES_FOLDER] == 6, "premise: m16's file shadows it"

    def test_delete_removes_the_file_and_the_example_comes_back(
            self, client, tmp_path):
        """The example is code, so deleting the file that shadows it is how an
        operator gets it back -- and the only way, because ``save`` refuses an
        example's id.

        RED under mutant "delete refuses every example id first" (the old
        order: the example check before the file):

            AssertionError: a damaged example file could not be deleted
            assert 403 == 200
        """
        path = _damage_m16(tmp_path / "flows", "truncated")
        r = client.delete("/api/flows/example-m16")
        assert r.status_code == 200, "a damaged example file could not be deleted"
        assert r.json() == {"deleted": "example-m16"}
        assert not path.exists()
        got = client.get("/api/flows/example-m16")
        assert got.status_code == 200 and got.json()["readonly"] is True
        listed = [r for r in client.get("/api/flows").json()
                  if r["id"] == "example-m16"]
        assert len(listed) == 1 and not listed[0].get("unreadable")

    def test_control_an_example_with_no_file_is_still_refused(self, store):
        """CONTROL: with nothing on disk the id is the shipped example, and
        that is never deleted.

        RED under mutant "delete of an example id never refuses" (the refusal
        dropped with the reorder), as are test_flows_store's
        ``test_an_example_cannot_be_deleted`` (the same line) and
        test_flows_routes' ``test_an_example_cannot_be_overwritten_or_deleted``
        (``assert 404 == 403``):

            Failed: DID NOT RAISE <class 'astrodeck.flows.store.ReadOnlyFlow'>
        """
        with pytest.raises(ReadOnlyFlow):
            store.delete("example-m16")
        assert store.get("example-m16").readonly is True

    def test_control_a_readable_file_with_an_example_id_still_shadows_it(
            self, store):
        """CONTROL: the rule is "any file", not "only an unreadable one". A
        readable user file carrying an example's id wins, as it always has.

        RED under mutant "shadow only by unreadable rows" (``taken`` built from
        the row ids alone):

            AssertionError: example-m16 is listed 2 times
            assert 2 == 1
        """
        _put_file(store.dir, "example-m16", 3, folder=MY_FLOWS_FOLDER)
        records, rows = store.listing()
        ids = [r.id for r in records] + [r["id"] for r in rows]
        assert ids.count("example-m16") == 1, (
            f"example-m16 is listed {ids.count('example-m16')} times")
        assert store.get("example-m16").readonly is False
        assert store.get("example-m16").name == "flow example-m16"

    def test_control_a_copied_file_shadows_by_the_id_inside_it(self, store):
        """CONTROL, the other half of the rule: a readable file shadows an
        example by the id INSIDE it as well as by its name. A copy saved
        under another name -- ``m16-backup.json`` holding flow
        ``example-m16`` -- is listed under the id inside, so without this
        half the example and the copy are two entries with one id, which is
        the defect this class exists to close. The test above cannot see it,
        because there the file's name and the id inside are the same.

        RED under mutant "shadow by stems only" (``taken.add(record.id)``
        dropped from ``_scan``):

            AssertionError: example-m16 is listed 2 times
            assert 2 == 1
        """
        path = _put_file(store.dir, "m16-backup", 3, folder=MY_FLOWS_FOLDER)
        _retag(path, id="example-m16")
        records, rows = store.listing()
        ids = [r.id for r in records] + [r["id"] for r in rows]
        assert ids.count("example-m16") == 1, (
            f"example-m16 is listed {ids.count('example-m16')} times")
        assert store.get("example-m16").name == "flow m16-backup"
        counts = {f["name"]: f["count"] for f in store.folders()}
        assert counts[EXAMPLES_FOLDER] == len(examples()) - 1
