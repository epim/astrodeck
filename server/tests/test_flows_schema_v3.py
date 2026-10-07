# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""FLOW_SCHEMA 3: the 23.4 migration (#150) and the files this build cannot read (#153).

TWO DEFECTS, ONE FILE FORMAT.

#150. The TARGET node shipped with ``rotation: 23.4`` as its palette default --
the M31 example's own angle, copied into the vocabulary. A rotation of 0 or
more is a real position angle (``to_plan``), so every palette-dropped, wizard
and quick-flow target asked a connected rotator for PA 23.4, an angle nobody
chose. The default is now -1 ("any angle"). A stored 23.4 is the same class as
the rotation-0 flip that needed schema 2: nothing inside the flow tells an
inherited 23.4 from a deliberate one, so the file's own version is the only
evidence. v1 and v2 files that say 23.4 are rewritten to -1 with a one-time
note; a v3 file that says 23.4 was saved after the default changed, so it
means it.

#153. ``_on_disk`` swallowed every load failure and ``_migrate`` passed any
``schema_version >= 2`` through unchanged. So a file written by a NEWER build
loaded with THIS build's meaning, and a damaged file vanished from the library
as if it had been deleted. Both now come back as visible, read-only library
rows that cannot be opened, compiled or run.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.config as config_mod
import astrodeck.flows.store as store_mod
from astrodeck.config import ConfigStore
from astrodeck.flows import wizard as flow_wizard
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowGraph, FlowNode, FlowRecord
from astrodeck.flows.nodes import default_params
from astrodeck.flows.store import (
    FLOW_SCHEMA, ROTATION_234_NOTE, V4_SCHEMA, FlowStore, NewerSchemaFlow)
from astrodeck.flows.to_plan import to_sequence_plan
from astrodeck.persist import ensure_dir


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    return FlowStore(tmp_path / "flows")


@pytest.fixture
def client(tmp_path, monkeypatch):
    """The same isolation as test_flows_routes: the config store, CONFIG_DIR,
    CAPTURE_DIR (the doctor's geometry scan walks it live) and the flow store
    singleton, so no test here reads the developer's real library."""
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


def _graph(rotation=-1) -> dict:
    """dusk -> target -> capture, only node types this build knows.

    The TARGET says ``counts: "Accepted subs"`` so that a read of it raises
    only the note this file is about. A TARGET with no ``counts`` key counts
    every sub taken, and from FLOW_SCHEMA 4 every read of one says ruling 2's
    ``counts`` note as well, whatever the file's version (that note, and the
    two notes side by side, are test_flows_schema_v4's). The 23.4 migration
    reads ``rotation`` alone, so the extra key changes nothing it decides."""
    return {
        "nodes": [
            {"id": "d", "type": "dusk", "x": 30, "y": 60,
             "params": {"offset": -30, "stop": "Dawn", "minAlt": 30}},
            {"id": "t", "type": "target", "x": 260, "y": 60,
             "params": {"name": "NGC 7129", "ra": "21h 42m 30s",
                        "dec": "+66 06 00", "rotation": rotation,
                        "counts": "Accepted subs"}},
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
              folder: str = "My flows", flow_extra: dict | None = None,
              graph: dict | None = None):
    """Write a flow file the way an older (or newer) build left it on disk."""
    ensure_dir(directory)
    flow = {"id": fid, "name": f"flow {fid}", "folder": folder,
            "graph": graph if graph is not None else _graph(rotation)}
    flow.update(flow_extra or {})
    path = directory / f"{fid}.json"
    path.write_text(json.dumps({"schema_version": schema_version, "id": fid,
                                "flow": flow}), encoding="utf-8")
    return path


def _rotation(record: FlowRecord):
    return next(n for n in record.graph.nodes if n.type == "target").params["rotation"]


def _notes(record: FlowRecord) -> list[dict]:
    return [n.model_dump() for n in record.migrated]


# ================================================================ #150, the read

class TestTheOldPaletteDefaultMigrates:
    # `test_the_schema_is_3` pinned FLOW_SCHEMA == 3 here. The mosaic slice
    # raised it to 4 (#189, spec 3.6), and the pin moved to
    # test_flows_schema_v4, which owns what 4 means. What stays here is the
    # 23.4 step, unchanged: a v1 or v2 file is still older than 3.

    def test_the_note_says_what_happened_and_what_to_do(self):
        """Pinned verbatim: the operator reads this on every read until they
        save the flow (S1-06; it used to be said once), and it has to say
        both that the 23.4 was never theirs AND that a deliberate 23.4 (a
        duplicated M31 example) was rewritten too.

        RED under mutant "reword the note":

            AssertionError: assert 'angle 23.4 w... you want it.' ==
            'angle 23.4 w...you meant it.'
        """
        assert ROTATION_234_NOTE == (
            'angle 23.4 was the old palette default and commanded a connected '
            'rotator to PA 23.4; it now reads "any angle". Set it again if you '
            'meant it.')

    def test_a_v2_file_holding_23_4_migrates_with_the_note(self, store):
        """RED under mutant "migration keyed on `!= 23.4`":

            AssertionError: a v2 23.4 is the palette default nobody chose and
            must read as 'any angle'
            assert 23.4 == -1
        """
        _put_file(store.dir, "f1", 2, rotation=23.4)
        rec = store.get("f1")
        assert _rotation(rec) == -1, (
            "a v2 23.4 is the palette default nobody chose and must read as "
            "'any angle'")
        assert _notes(rec) == [{"key": "rotation", "note": ROTATION_234_NOTE}]

    def test_a_v2_file_holding_30_is_left_alone(self, store):
        """RED under mutant "migration keyed on `!= 23.4`":

            AssertionError: 30 is an angle somebody typed
            assert -1 == 30
        """
        _put_file(store.dir, "f1", 2, rotation=30)
        rec = store.get("f1")
        assert _rotation(rec) == 30, "30 is an angle somebody typed"
        assert _notes(rec) == []

    def test_a_v3_file_holding_23_4_is_deliberate(self, store):
        """The writer stamps 3, so a 23.4 in a v3 file was saved AFTER the
        default changed: somebody set it.

        RED under mutant "the migration ignores the file version" (the v2->v3
        step runs for every file):

            AssertionError: a v3 file was written after the default changed;
            its 23.4 was typed on purpose
            assert -1 == 23.4
        """
        _put_file(store.dir, "f1", 3, rotation=23.4)
        rec = store.get("f1")
        assert _rotation(rec) == 23.4, (
            "a v3 file was written after the default changed; its 23.4 was "
            "typed on purpose")
        assert _notes(rec) == []

    def test_a_v1_file_holding_23_4_migrates_too(self, store):
        """23.4 was the default under v1 as well, and the migration is a chain:
        a v1 file takes v1->v2 AND v2->v3.

        RED under mutant "v1->v2 returns early" (the pre-chain shape):

            AssertionError: a v1 file's 23.4 was the palette default too
            assert 23.4 == -1
        """
        _put_file(store.dir, "f1", 1, rotation=23.4)
        rec = store.get("f1")
        assert _rotation(rec) == -1, "a v1 file's 23.4 was the palette default too"
        assert _notes(rec) == [{"key": "rotation", "note": ROTATION_234_NOTE}]

    def test_control_a_v1_zero_still_becomes_any_angle_without_a_note(self, store):
        """CONTROL: the v1->v2 step is unchanged by the chain. A v1 0 meant
        "any angle" and still reads -1, and it carries no note, because the
        meaning did not change -- only its spelling did.

        RED under mutant "the v1->v2 rewrite dropped":

            AssertionError: assert 0 == -1
        """
        _put_file(store.dir, "f1", 1, rotation=0)
        rec = store.get("f1")
        assert _rotation(rec) == -1
        assert _notes(rec) == []

    def test_a_stored_migrated_key_never_surfaces_as_a_note(self, store):
        """The note is COMPUTED from the file's version on every read, never
        read back. A file that carries a ``migrated`` key (a hand edit, or a
        writer that forgot to strip it) must not replay a stale note forever.

        RED under mutant "the loader keeps a stored note" (``setdefault``
        instead of assignment):

            AssertionError: a note came from the file, not from a migration
            assert [{'key': 'rot...te': 'stale'}] == []
              Left contains one more item: {'key': 'rotation', 'note': 'stale'}
        """
        _put_file(store.dir, "f1", 3, rotation=23.4,
                  flow_extra={"migrated": [{"key": "rotation", "note": "stale"}]})
        rec = store.get("f1")
        assert _notes(rec) == [], "a note came from the file, not from a migration"


# ====================================================== #150, the note is not data

class TestTheNoteIsNeverPersisted:
    def test_get_carries_the_note_and_a_put_retires_it(self, client, tmp_path):
        """The note travels on ``GET /api/flows/{id}`` (FastAPI serialises the
        record with the same dump the writer uses, which is why the field is
        not ``Field(exclude=True)``), and the save strips it: the file is
        current, the angle -1, no ``migrated`` key, and the next GET is quiet.
        "Current" is 4 since the mosaic slice: this TARGET counts accepted
        subs, a meaning a v3 build would misread (``store.schema_for``). It
        was 3 when this test was written; any version of 3 or more is what
        marks the -1 as the operator's.

        RE-PINNED FOR BACKLOG WP-85 (#195, wave 14 integration):
        ``FLOW_SCHEMA`` is 5 now, but the file this save writes is still 4.
        ``schema_for`` stamps 5 only for a graph with DUSK Automatic resume
        Off or a DUSK that owes the v4 -> v5 note, and stamps 4
        (``V4_SCHEMA``) for any other graph that uses a v4 meaning, as this
        TARGET's accepted-sub count does. The assertion therefore pins
        ``V4_SCHEMA``, not ``FLOW_SCHEMA``; what it grades (the note is not
        persisted, the angle is -1 and current) is unchanged.

        RED under mutant "persist migrated" (the writer dumps the field):

            AssertionError: the note is a message about a read, not a property
            of the flow
            assert 'migrated' not in {'created_ts': 1790241056.1204362,
            'folder': 'My flows', 'graph': {'edges': [{'from': 'd', ...}, ...]},
            'id': 'f1', ...}

        (The second GET stays quiet even under that mutant, because the loader
        replaces any stored note -- which is why the FILE is asserted on.)
        """
        path = _put_file(tmp_path / "flows", "f1", 2, rotation=23.4)
        got = client.get("/api/flows/f1")
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["migrated"] == [{"key": "rotation", "note": ROTATION_234_NOTE}]

        put = client.put("/api/flows/f1", json={"flow": body})
        assert put.status_code == 200, put.text

        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk["schema_version"] == V4_SCHEMA == 4 < FLOW_SCHEMA
        target = next(n for n in on_disk["flow"]["graph"]["nodes"]
                      if n["type"] == "target")
        assert target["params"]["rotation"] == -1
        assert "migrated" not in on_disk["flow"], (
            "the note is a message about a read, not a property of the flow")
        assert client.get("/api/flows/f1").json()["migrated"] == []

    def test_a_client_cannot_plant_a_note_through_a_save(self, client, tmp_path):
        """``save()`` never keeps a ``migrated`` sent in the body: the response
        is the stored record, and the stored record has no note.

        RED under mutant "save keeps the body's migrated":

            AssertionError: a note is the server's to raise
            assert [{'key': 'rot...e': 'forged'}] == []
              Left contains one more item: {'key': 'rotation', 'note': 'forged'}
        """
        body = {"name": "planted", "graph": _graph(rotation=-1),
                "migrated": [{"key": "rotation", "note": "forged"}]}
        saved = client.post("/api/flows", json={"flow": body})
        assert saved.status_code == 200, saved.text
        assert saved.json()["migrated"] == [], "a note is the server's to raise"
        fid = saved.json()["id"]
        raw = json.loads((tmp_path / "flows" / f"{fid}.json").read_text(encoding="utf-8"))
        assert "migrated" not in raw["flow"]

    def test_touch_run_writes_no_note(self, store):
        """``touch_run`` is the other writer (a run's start and its finalize
        both call it). It writes no note, and -- REWRITTEN DELIBERATELY for
        carry-over 1 (#150) -- it no longer rewrites the record either.

        This test used to pin ``raw["schema_version"] == 3``: a run wrote the
        MIGRATED record back at FLOW_SCHEMA, so the first run of a v2 flow
        rewrote its inherited 23.4 to -1 and retired the note for good. Only
        ``save()`` stamps FLOW_SCHEMA now. ``touch_run`` edits ``last_run`` and
        ``last_result`` in the raw file, which stays v2 with its 23.4 until the
        operator saves, and the next read still says the note.
        (test_flows_store_raw_edits pins the byte-level "nothing else changed".)

        RED under mutant "write the migrated record" (``touch_run`` restored to
        ``self._write(record.model_copy(update=update))``, the code as it
        stood):

            AssertionError: a run stamped the file current, and the note is
            gone before the operator saved
            assert 3 == 2

        RED under mutant "no copy before _migrate" (``_record_of`` migrates the
        raw dict in place and ``touch_run`` writes that dict) -- the version
        survives but the migration leaks into the file:

            AssertionError: a run's bookkeeping wrote the read-time note into
            the file
            assert 'migrated' not in {'folder': 'My flows', 'graph':
            {'edges': [{'from': 'd', 'fromPort': 'window', 'to': 't',
            'toPort': 'arm'}, {'from': '...': 12, 'exposure': 120, 'filter':
            'L', ...}, 'type': 'capture', 'x': 490, ...}]}, 'id': 'f1',
            'last_result': 'ok', ...}
        """
        path = _put_file(store.dir, "f1", 2, rotation=23.4)
        assert store.touch_run("f1", ts=1234.0, result="ok") is True
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["schema_version"] == 2, (
            "a run stamped the file current, and the note is gone before the "
            "operator saved")
        assert "migrated" not in raw["flow"], (
            "a run's bookkeeping wrote the read-time note into the file")
        assert next(n for n in raw["flow"]["graph"]["nodes"]
                    if n["type"] == "target")["params"]["rotation"] == 23.4
        assert raw["flow"]["last_run"] == 1234.0
        assert raw["flow"]["last_result"] == "ok"
        assert _notes(store.get("f1")) == [
            {"key": "rotation", "note": ROTATION_234_NOTE}]


@pytest.fixture
def run_client(client, monkeypatch):
    """``client`` with the camera, the sun check and ``engine.start`` stubbed
    (test_plan_identity's ``api``), so ``/run`` goes through every guard and
    stops at the engine. The fresh config store's default site never blocks the
    horizon pre-flight."""
    monkeypatch.setattr(app_module.hub, "_check_solar", lambda *a, **kw: None)
    monkeypatch.setattr(app_module.hub, "require", lambda role: object())
    starts: list = []
    monkeypatch.setattr(app_module.engine, "start",
                        lambda plan, **kw: starts.append(plan))
    client.starts = starts
    return client


def _said(bus_lines) -> list[tuple[str, str]]:
    return [(lvl, m) for lvl, m, _src in bus_lines if "23.4" in m]


class TestARunSaysTheNoteUntilTheFlowIsSaved:
    """Formerly ``TestARunSaysTheNoteItRetires``, REWRITTEN DELIBERATELY for
    carry-over 1 (#150), because it pinned the old behaviour: ``touch_run``
    wrote the migrated record back at FLOW_SCHEMA, so the first run of a v2
    flow retired the note for good and its one log line was the only time
    anybody was told.

    The new truth: only ``save()`` stamps FLOW_SCHEMA. ``/run`` compiles the
    MIGRATED graph, ``touch_run`` edits ``last_run`` in the raw file, and the
    file stays v2 with its 23.4 until the operator saves. So every run of an
    unsaved v2 23.4 flow shoots at "any angle" AND says so. A run started from
    a list (SESSION / NOW, the library's RUN verb, the wizard) never opens the
    editor that otherwise says it, so the run has to, and it has to keep
    saying it: an operator who missed the line on night one would otherwise
    never hear it on night two."""

    def test_every_run_of_an_unsaved_v2_23_4_flow_says_the_note(
            self, run_client, tmp_path, bus_lines):
        """RED under mutant "no note on the run" (the ``bus.log`` in
        ``run_flow``'s ``rec.migrated`` loop replaced by ``pass``, as the diff
        first shipped):

            AssertionError: the note reached nobody, or only the first run
            assert [] == [('warning', ...u meant it.")]
              Right contains 2 more items, first extra item: ('warning',
              'flow \\'flow f1\\': angle 23.4 was the old palette default and
              commanded a connected rotator to PA 23.4; it now reads "any
              angle". Set it again if you meant it.')

        RED under mutant "write the migrated record" (``touch_run`` restored
        to ``self._write(record.model_copy(update=update))``, the code as it
        stood) -- the first run stamps the file 3 and the second is silent:

            AssertionError: the note reached nobody, or only the first run
            assert [('warning', ...u meant it.")] == [('warning', ...u meant it.")]
              Right contains one more item: ('warning', 'flow \\'flow f1\\':
              angle 23.4 was the old palette default and commanded a connected
              rotator to PA 23.4; it now reads "any angle". Set it again if
              you meant it.')
        """
        path = _put_file(tmp_path / "flows", "f1", 2, rotation=23.4)
        for _ in range(2):
            r = run_client.post("/api/flows/f1/run",
                                json={"accept_unmapped": True})
            assert r.status_code == 200, r.text
        assert [p.targets[0].rotation_deg for p in run_client.starts] == [
            None, None], "premise: both runs shot the migrated angle"
        said = ("warning", f"flow 'flow f1': {ROTATION_234_NOTE}")
        assert _said(bus_lines) == [said, said], (
            "the note reached nobody, or only the first run")
        # ...because nothing has made the file current: it is still the v2
        # file the operator has not saved, and the next read says so too.
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["schema_version"] == 2
        assert raw["flow"]["last_run"] is not None, "premise: the run was recorded"
        assert run_client.get("/api/flows/f1").json()["migrated"] == [
            {"key": "rotation", "note": ROTATION_234_NOTE}]

    def test_control_a_deliberate_23_4_is_run_without_a_word(
            self, run_client, tmp_path, bus_lines):
        """CONTROL: a v3 file's 23.4 was set on purpose. It runs at 23.4 and
        nothing is said, because the line is the READ's finding, not a
        reaction to the number.

        RED under mutant "the run re-derives the note from the angle" (log
        ROTATION_234_NOTE for any target whose rotation is 23.4, instead of
        ``rec.migrated``) - and the case above goes red with it, because the
        graph the run holds is already migrated to -1:

            AssertionError: a deliberate 23.4 was reported as migrated
            assert [('warning', ...u meant it.")] == []
        """
        _put_file(tmp_path / "flows", "f1", 3, rotation=23.4)
        r = run_client.post("/api/flows/f1/run", json={"accept_unmapped": True})
        assert r.status_code == 200, r.text
        assert [p.targets[0].rotation_deg for p in run_client.starts] == [23.4]
        assert _said(bus_lines) == [], "a deliberate 23.4 was reported as migrated"

    def test_a_refused_run_says_nothing_and_keeps_the_note(
            self, run_client, tmp_path, bus_lines, monkeypatch):
        """A start the engine refuses says nothing: the line tells the operator
        what a run is shooting, and nothing shot. The file stays v2, so the
        next read (the editor, or the next run) still carries the note.

        RED under mutant "note said before the guards" (the loop moved above
        ``hub.require``):

            AssertionError: a refused run said the note, and the editor will
            say it again
            assert [('warning', ...u meant it.")] == []
              Left contains one more item: ('warning', 'flow \\'flow f1\\':
              angle 23.4 was the old palette default ...')
        """
        from astrodeck.devices.base import DeviceError

        def no_camera(role):
            raise DeviceError("no camera connected")

        monkeypatch.setattr(app_module.hub, "require", no_camera)
        path = _put_file(tmp_path / "flows", "f1", 2, rotation=23.4)
        r = run_client.post("/api/flows/f1/run", json={"accept_unmapped": True})
        assert r.status_code >= 400, r.text
        assert run_client.starts == [], "premise: the engine was never started"
        assert _said(bus_lines) == [], (
            "a refused run said the note, and the editor will say it again")
        assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 2
        assert run_client.get("/api/flows/f1").json()["migrated"] == [
            {"key": "rotation", "note": ROTATION_234_NOTE}]


# ============================================================ #150, the default

class TestAFlowWithNoAngleHasNoAngle:
    """#150's red test. Today (the default 23.4) both of these compile to
    ``rotation_deg == 23.4`` -- a connected rotator commanded to a fixture
    value on every wizard and quick flow.

    RED under mutant "restore the 23.4 palette default" (nodes.py), which is
    the code as it stood -- all three tests:

        assert 23.4 == -1
        AssertionError: a wizard flow nobody gave an angle commanded PA 23.4
        assert 23.4 is None
        AssertionError: a quick flow nobody gave an angle commanded PA 23.4
        assert 23.4 is None
    """

    def test_the_palette_default_is_any_angle(self):
        assert default_params("target")["rotation"] == -1

    def test_a_wizard_flow_compiles_to_no_rotation(self):
        rec = flow_wizard.generate_record(flow_wizard.KIND_DEEP_SKY,
                                          flow_wizard.DEFAULT_OPTIONS, "M 31")
        plan, _ = to_sequence_plan(compile_plan(rec.graph, rec.name), rec.graph)
        assert plan.targets[0].rotation_deg is None, (
            f"a wizard flow nobody gave an angle commanded PA "
            f"{plan.targets[0].rotation_deg}")

    def test_a_quick_flow_compiles_to_no_rotation(self):
        rec = flow_wizard.quick({"name": "NGC 7331", "ra": "22h 37m 05s",
                                 "dec": "+34 24 56"},
                                subs_per_filter=5, filters=["L", "R"],
                                wheel=["L", "R", "G", "B"])
        plan, _ = to_sequence_plan(compile_plan(rec.graph, rec.name), rec.graph)
        assert plan.targets[0].rotation_deg is None, (
            f"a quick flow nobody gave an angle commanded PA "
            f"{plan.targets[0].rotation_deg}")


# ================================================= #153, files this build can't read

def _v9_graph() -> dict:
    """A future file whose node types ALL exist here -- the dangerous case,
    because nothing but the version says it means something else."""
    return _graph(rotation=23.4)


class TestAFutureFileIsRefusedLoudly:
    def test_a_v9_file_is_a_row_and_never_a_record(self, store):
        """RED under mutant "drop the refusal" (``_migrate`` passes v9
        through):

            AssertionError: a file a newer build wrote loaded with this build's
            meaning
            assert 'future' not in {'example-campaign', 'example-cycle',
            'example-eaa', 'example-m16', 'example-m31', 'example-nb', ...}
        """
        _put_file(store.dir, "future", 9, graph=_v9_graph(), folder="Winter")
        ids = {r.id for r in store.load_all()}
        assert "future" not in ids, (
            "a file a newer build wrote loaded with this build's meaning")
        with pytest.raises(KeyError):
            store.get("future")
        rows = store.unreadable()
        assert [r["id"] for r in rows] == ["future"]
        row = rows[0]
        assert row["unreadable"] == (
            "saved by a newer AstroDeck (schema 9); update to open it")
        assert row["readonly"] is True
        assert row["folder"] == "Winter", "the row sits in the folder it names"
        assert row["name"] == "flow future"

    def test_the_row_has_the_card_shape(self, store):
        """The library renders rows and cards with one component, so a row is
        a card plus ``unreadable``: every key a card has, and nothing that
        could make it runnable.

        RED under mutant "the row lacks a card key" (no ``updated_ts``):

            AssertionError: {'updated_ts'}
            assert {'folder', 'i...eadonly', ...} <= {'folder', 'i...eadonly', ...}
              Extra items in the left set:
              'updated_ts'
        """
        _put_file(store.dir, "future", 9, graph=_v9_graph())
        row = store.unreadable()[0]
        card = FlowRecord(name="x").card()
        assert set(card) <= set(row), set(card) - set(row)
        assert set(row) - set(card) == {"unreadable"}
        assert row["stages"] == 3 and row["wires"] == 2

    def test_the_api_lists_it(self, client, tmp_path):
        """``GET /api/flows`` appends the rows after the cards.

        RED under mutant "list_flows does not append the rows":

            AssertionError: the newer file vanished from the library
            assert {'future'} <= set()

        RED under mutant "drop the refusal" (it loads, so it is a plain card
        with no ``unreadable`` and no row):

            AssertionError: the newer file vanished from the library
            assert {'future'} <= set()
        """
        _put_file(tmp_path / "flows", "future", 9, graph=_v9_graph())
        listed = client.get("/api/flows").json()
        rows = {r["id"]: r for r in listed if r.get("unreadable")}
        assert {"future"} <= set(rows), "the newer file vanished from the library"
        assert rows["future"]["readonly"] is True
        assert rows["future"]["unreadable"] == (
            "saved by a newer AstroDeck (schema 9); update to open it")

    def test_get_compile_and_run_answer_404(self, client, tmp_path):
        """Nothing but the library can reach it: ``get`` never returns a row.

        RED under mutant "drop the refusal":

            AssertionError: GET /api/flows/future opened a file a newer build
            wrote
            assert 200 == 404
        """
        _put_file(tmp_path / "flows", "future", 9, graph=_v9_graph())
        for method, url in (("get", "/api/flows/future"),
                            ("post", "/api/flows/future/compile"),
                            ("post", "/api/flows/future/run")):
            kw = {"json": {}} if url.endswith("/run") else {}
            r = getattr(client, method)(url, **kw)
            assert r.status_code == 404, (
                f"{method.upper()} {url} opened a file a newer build wrote")

    def test_folders_count_the_row_where_it_says_it_lives(self, client, tmp_path):
        """RED under mutant "folders() ignores the rows":

            AssertionError: a folder holding only unreadable rows must still
            be listed, with its count
            assert None == 1
        """
        _put_file(tmp_path / "flows", "future", 9, graph=_v9_graph(),
                  folder="Winter")
        counts = {f["name"]: f["count"] for f in
                  client.get("/api/flows/folders").json()}
        assert counts.get("Winter") == 1, (
            "a folder holding only unreadable rows must still be listed, with "
            "its count")

    def test_a_row_files_itself_by_the_models_folder_rule(self, store):
        """The folder comes from a file this build could not validate, so it
        goes through ``FlowRecord``'s own folder rule: a legal one is
        normalised the way a record's would be, anything else is filed under
        My flows. A raw copy would put a path-shaped string into the library's
        folder headers, which nothing else in the library can do.

        RED under mutant "row folder raw" (``folder = flow["folder"]``):

            AssertionError: a row's folder skipped the model's folder rule
            assert {'odd': '../....: ' Winter/ '} == {'odd': 'My f...ed': 'Winter'}
              Differing items:
              {'odd': '../../etc'} != {'odd': 'My flows'}
              {'padded': ' Winter/ '} != {'padded': 'Winter'}
        """
        _put_file(store.dir, "odd", 9, graph=_v9_graph(), folder="../../etc")
        _put_file(store.dir, "padded", 9, graph=_v9_graph(), folder=" Winter/ ")
        rows = {r["id"]: r["folder"] for r in store.unreadable()}
        assert rows == {"odd": "My flows", "padded": "Winter"}, (
            "a row's folder skipped the model's folder rule")
        assert "../../etc" not in {f["name"] for f in store.folders()}


class TestADamagedFileIsVisible:
    def test_a_truncated_file_and_an_unknown_type_file_are_listed(self, store):
        """RED under mutant "`continue` on failure" (the old swallow):

            AssertionError: damaged files vanished from the library as if
            deleted
            assert set() == {'broken', 'strange'}
        """
        ensure_dir(store.dir)
        good = _put_file(store.dir, "good", 3)
        text = good.read_text(encoding="utf-8")
        (store.dir / "broken.json").write_text(text[: len(text) // 2],
                                               encoding="utf-8")
        weird = _graph()
        weird["nodes"][2]["type"] = "warpdrive"
        _put_file(store.dir, "strange", 3, graph=weird)

        rows = {r["id"]: r for r in store.unreadable()}
        assert set(rows) == {"broken", "strange"}, (
            "damaged files vanished from the library as if deleted")
        assert rows["broken"]["unreadable"].startswith("unreadable: ")
        assert "not valid JSON" in rows["broken"]["unreadable"]
        assert rows["broken"]["name"] == "broken", "no name inside; the file's"
        assert "unknown node type 'warpdrive'" in rows["strange"]["unreadable"]
        assert rows["strange"]["unreadable"].startswith("unreadable: ")
        assert all(r["readonly"] is True for r in rows.values())
        # The good one is still a record, and neither broken one is.
        assert {r.id for r in store.load_all()} >= {"good"}
        with pytest.raises(KeyError):
            store.get("broken")
        with pytest.raises(KeyError):
            store.get("strange")

    def test_no_reason_names_the_store_directory(self, store, monkeypatch):
        """The library is VIEWER-readable. A reason is for the operator to act
        on, and the server's filesystem layout is not something a viewer needs
        (or gets anywhere else). An OSError's own text carries the full path,
        so the unreadable-file case is the one that would leak.

        RED under mutant "the reason is the exception's own text"
        (``f"unreadable: {e}"``), the temp path elided:

            AssertionError: the reason for 'locked' names the server's
            directory: "unreadable: [Errno 13] Permission denied:
            'C:\\\\\\\\Users\\\\\\\\...\\\\\\\\flows\\\\\\\\locked.json'"
        """
        ensure_dir(store.dir)
        (store.dir / "cut.json").write_text('{"schema_version": 2, "fl',
                                            encoding="utf-8")
        (store.dir / "list.json").write_text("[1, 2]", encoding="utf-8")
        (store.dir / "badver.json").write_text(
            json.dumps({"schema_version": "three", "flow": {}}), encoding="utf-8")
        weird = _graph()
        weird["nodes"][0]["type"] = "warpdrive"
        _put_file(store.dir, "strange", 2, graph=weird)
        _put_file(store.dir, "future", 9, graph=_v9_graph())
        _put_file(store.dir, "locked", 3)

        real_read = store_mod.read_json

        def read(path):
            if path.name == "locked.json":
                raise PermissionError(13, "Permission denied", str(path))
            return real_read(path)

        monkeypatch.setattr(store_mod, "read_json", read)
        rows = store.unreadable()
        assert {r["id"] for r in rows} == {"cut", "list", "badver", "strange",
                                           "future", "locked"}
        # EVERY SPELLING OF THE DIRECTORY, including the repr one. An OSError
        # quotes its filename through repr(), which doubles each backslash on
        # Windows, so a needle of plain str(dir) never matches the leak this
        # test exists for. That is exactly how the first draft of it passed
        # with the mutant below in place.
        plain = {str(store.dir), str(store.dir.resolve())}
        needles = plain | {p.replace("\\", "\\\\") for p in plain} | {
            store.dir.as_posix(), store.dir.resolve().as_posix()}
        for row in rows:
            for needle in needles:
                assert needle not in row["unreadable"], (
                    f"the reason for {row['id']!r} names the server's "
                    f"directory: {row['unreadable']!r}")

    def test_a_schema_version_that_is_not_finite_is_damage(self, store):
        """``Infinity`` is not JSON, but Python's parser accepts the token, and
        ``int(inf)`` raises OverflowError, not the ValueError ``_schema_of``
        turned into a reason. So the row said only "OverflowError", and a save
        over the id raised out of ``save()`` into a 500 (the file survived,
        by accident). It is a version that is not a number, filed with the
        others: a row that says so, and a save over it repairs it.

        RED under mutant "OverflowError escapes _schema_of" (the except
        tuple without it, which is the code as the implementer left it):

            AssertionError: assert 'unreadable: OverflowError' == 'unreadable: ... not a number'
              - unreadable: its schema version is not a number
              + unreadable: OverflowError
        """
        ensure_dir(store.dir)
        for fid, token in (("inf", "Infinity"), ("neginf", "-Infinity")):
            (store.dir / f"{fid}.json").write_text(
                '{"schema_version": %s, "flow": {}}' % token, encoding="utf-8")
        rows = store.unreadable()
        assert len(rows) == 2
        for row in rows:
            assert row["unreadable"] == (
                "unreadable: its schema version is not a number")
        store.save(FlowRecord(id="inf", name="repaired"))
        assert store.get("inf").name == "repaired"

    def test_control_a_readable_library_has_no_rows(self, store):
        """CONTROL: v1, v2 and v3 files are records, not rows.

        RED under mutant "refusal at >= FLOW_SCHEMA" (a current file refused
        as future):

            AssertionError: assert [{'folder': '...': None, ...}] == []
              Left contains one more item: {'folder': 'My flows', 'id': 'c',
              'last_result': '', 'last_run': None, ...}
        """
        for fid, version in (("a", 1), ("b", 2), ("c", 3)):
            _put_file(store.dir, fid, version)
        assert store.unreadable() == []
        assert {"a", "b", "c"} <= {r.id for r in store.load_all()}


class TestANewerFileIsNeverOverwritten:
    def test_the_store_refuses_and_the_file_is_byte_identical(self, store):
        """Overwriting it would silently downgrade whatever the newer build
        meant. The operator updates, or deletes it on purpose.

        RED under mutant "drop the overwrite guard":

            Failed: DID NOT RAISE <class 'astrodeck.flows.store.NewerSchemaFlow'>
        """
        path = _put_file(store.dir, "future", 9, graph=_v9_graph())
        before = path.read_bytes()
        with pytest.raises(NewerSchemaFlow) as info:
            store.save(FlowRecord(id="future", name="clobber",
                                  graph=FlowGraph(nodes=[FlowNode(id="a", type="dusk")])))
        assert info.value.code == "newer_schema"
        assert path.read_bytes() == before

    def test_the_api_answers_409_newer_schema(self, client, tmp_path):
        """PUT and POST both land in ``save()``.

        RED under mutant "drop the overwrite guard":

            AssertionError: PUT overwrote a file a newer build wrote
            assert 200 == 409
        """
        path = _put_file(tmp_path / "flows", "future", 9, graph=_v9_graph())
        before = path.read_bytes()
        put = client.put("/api/flows/future",
                         json={"flow": {"name": "clobber", "graph": _graph()}})
        assert put.status_code == 409, "PUT overwrote a file a newer build wrote"
        assert put.json()["detail"]["code"] == "newer_schema"
        post = client.post("/api/flows",
                           json={"flow": {"id": "future", "name": "clobber",
                                          "graph": _graph()}})
        assert post.status_code == 409, post.text
        assert path.read_bytes() == before

    def test_control_a_damaged_file_can_be_saved_over(self, store):
        """CONTROL: the guard is about MEANING, not damage. A truncated file
        holds nothing a save could downgrade, and saving a good record over it
        is how an operator repairs it (the writer keeps a ``.bak``).

        RED under mutant "the guard refuses any unreadable file":

            astrodeck.flows.store.NewerSchemaFlow: a newer AstroDeck saved this
            flow (schema 4); update to edit it, or save under a new name
        """
        ensure_dir(store.dir)
        (store.dir / "broken.json").write_text('{"schema_version": 3, "fl',
                                               encoding="utf-8")
        store.save(FlowRecord(id="broken", name="repaired"))
        assert store.get("broken").name == "repaired"
        assert store.unreadable() == []
