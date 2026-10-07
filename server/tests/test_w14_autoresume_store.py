# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""FLOW_SCHEMA 5: DUSK WINDOW's `autoResume`, the one-time note for a saved
"Single night", and the version a save stamps (#195, owner ruling 7 on #189,
spec 3.6; WP-85).

WHAT THE READ DOES. 0.3.40 (schema 4) read `repeat`'s own default, "Single
night", as "do not resume", and nothing inside a file tells a choice of it from
the default. The read does not try to repair that: it maps the flow to what
the missing `autoResume` means, On, and says so, with the owner's note
verbatim, on every read of the file until the operator saves it (the 23.4
pattern). It never writes `autoResume` into the graph, and above all it never
maps "Single night" to Off, which would silently disarm every saved flow.

WHAT A SAVE DOES. `schema_for` is three-tier: 5 for a graph whose DUSK says
Off or still owes the note, 4 for any other v4 meaning, 3 for none. The first
tier is what retires the note: a save writes the graph as the operator kept
it, with no `autoResume` in it, so the file version is the only evidence the
note was seen.

NAMED MUTANTS (each run from a byte backup inside this worktree, restored and
sha256-compared, the mutant text grepped out afterwards; the failing assertion
is quoted in the docstring of the test that catches it):

* "migration note maps Single night to Off": `_migrate` writes
  ``autoResume: "Off"`` into every DUSK that owes the note;
* "the note never retires": `schema_for` stops stamping 5 for a DUSK that
  owes the note.
"""
from __future__ import annotations

import json

import pytest

import astrodeck.config as config_mod
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode, FlowRecord
from astrodeck.flows.nodes import dusk_auto_resume
from astrodeck.flows.store import (
    AUTO_RESUME_NOTE, COUNTS_NOTE, FLOW_SCHEMA, V3_SCHEMA, V4_SCHEMA, FlowStore,
    NewerSchemaFlow, _v4_meanings, schema_for)
from astrodeck.persist import ensure_dir

OWNERS_NOTE = ("'Single night' never stopped the next night's automatic "
               "resume; this flow now shows that as ON. Turn it off if you "
               "meant one night only.")
AUTO = {"key": "autoResume", "note": AUTO_RESUME_NOTE}
COUNTS = {"key": "counts", "note": COUNTS_NOTE}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    return FlowStore(tmp_path / "flows")


def _graph(**dusk_params) -> dict:
    """dusk -> target -> capture as a v4 build wrote it, the DUSK carrying
    exactly ``dusk_params`` and the TARGET already counting accepted subs, so
    the counts note is not in the way."""
    return {"nodes": [
        {"id": "d", "type": "dusk", "x": 30, "y": 60, "params": dict(dusk_params)},
        {"id": "t", "type": "target", "x": 260, "y": 60,
         "params": {"name": "NGC 7129", "ra": "21h 42m 30s",
                    "dec": "+66 06 00", "rotation": -1,
                    "counts": "Accepted subs"}},
        {"id": "c", "type": "capture", "x": 490, "y": 60,
         "params": {"filter": "L", "exposure": 120, "gain": 100,
                    "bin": "1", "count": 12, "goal": 0}}],
        "edges": [
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


def _dusk_params(record: FlowRecord) -> dict:
    return record.graph.node("d").params


def _flow_graph(**dusk_params) -> FlowGraph:
    return FlowGraph(
        nodes=[FlowNode(id="d", type="dusk", x=0, y=0, params=dict(dusk_params)),
               FlowNode(id="t", type="target", x=200, y=0,
                        params={"name": "M42", "ra": "05h 34m 32s",
                                "dec": "+22 00 52"})],
        edges=[FlowEdge(**{"from": "d", "fromPort": "window",
                           "to": "t", "toPort": "arm"})])


# ================================================================ the version

def test_the_versions():
    assert (FLOW_SCHEMA, V4_SCHEMA, V3_SCHEMA) == (5, 4, 3)


def test_the_note_is_the_owners_words_verbatim():
    """Ruling 7 on #189, quoted in #195: the sentence is not paraphrased."""
    assert AUTO_RESUME_NOTE == OWNERS_NOTE


# ============================================================ the migration note

def test_a_v4_single_night_reads_as_on_and_says_so(store):
    """The saved flow of #195's first test: `repeat: "Single night"`, no
    `autoResume`. It reads with the note, the graph is untouched, and what it
    means is On: the compile writes no ``resume_across_nights`` and the plan
    resumes.

    MUTANT "migration note maps Single night to Off" (`_migrate` writing
    ``autoResume: "Off"`` into each DUSK that owes the note) turned this red,
    run from a byte backup and restored and sha256-verified afterwards:

        AssertionError: the read rewrote the DUSK: it must only say what the
        missing key means
        assert {'autoResume'...Single night'} == {'repeat': 'Single night'}
          Left contains 1 more item:
          {'autoResume': 'Off'}
    """
    _put_file(store.dir, "f1", 4, _graph(repeat="Single night"))
    rec = store.get("f1")
    assert _notes(rec) == [AUTO]
    assert _dusk_params(rec) == {"repeat": "Single night"}, (
        "the read rewrote the DUSK: it must only say what the missing key means")
    assert dusk_auto_resume(_dusk_params(rec)) is True, (
        "a saved 'Single night' must NOT read as Off: that silently disarms "
        "every saved flow")
    compiled = compile_plan(rec.graph, "f")
    assert "resume_across_nights" not in compiled, (
        f"the migrated flow compiles to {compiled.get('resume_across_nights')!r}; "
        f"a saved 'Single night' must resume")


def test_the_note_is_said_on_every_read_and_the_file_is_not_touched(store):
    """The 23.4 pattern: every read says it, the file is byte-identical after
    them, and a run's `touch_run` (a bookkeeping writer that edits the raw
    file) neither stamps 5 nor retires the note."""
    path = _put_file(store.dir, "f1", 4, _graph(repeat="Single night"))
    before = path.read_bytes()
    assert _notes(store.get("f1")) == [AUTO]
    assert _notes(store.get("f1")) == [AUTO]
    assert path.read_bytes() == before, "a read wrote the file"
    assert store.touch_run("f1", ts=1.0, result="ok") is True
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 4
    assert _notes(store.get("f1")) == [AUTO], "touch_run retired the note"


def test_a_save_retires_the_note_and_stamps_five(store):
    """Only a save has seen the migrated graph and kept it, so only a save
    retires the note, by writing the version the note cannot be said at.

    MUTANT "the note never retires" (`schema_for` no longer stamping 5 for a
    DUSK that owes the note) turned this red, run from a byte backup and
    restored and sha256-verified afterwards:

        AssertionError: a save of a DUSK that owed the note must stamp 5, or
        the note is said again on every read for ever
        assert 4 == 5
    """
    path = _put_file(store.dir, "f1", 4, _graph(repeat="Single night"))
    store.save(store.get("f1"))
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 5, (
        "a save of a DUSK that owed the note must stamp 5, or the note is "
        "said again on every read for ever")
    dusk = next(n for n in raw["flow"]["graph"]["nodes"] if n["id"] == "d")
    assert dusk["params"] == {"repeat": "Single night"}, (
        "the save wrote an autoResume the operator never chose")
    assert _notes(store.get("f1")) == [], "the note outlived the save"


@pytest.mark.parametrize("dusk, version, why", [
    ({"repeat": "Nightly until pool complete"}, 4, "another repeat value"),
    ({"repeat": "Nightly ×30"}, 4, "another repeat value"),
    ({"repeat": "Single night", "autoResume": "On"}, 4, "a stated On"),
    ({"repeat": "Single night", "autoResume": "Off"}, 4, "a stated Off"),
    ({"repeat": "Single night", "autoResume": ""}, 4, "a blank autoResume"),
    ({"offset": -30}, 4, "no repeat key at all: the operator never chose it"),
    ({"repeat": "Single night"}, 5, "a file written at schema 5"),
])
def test_when_the_note_is_not_said(store, dusk, version, why):
    _put_file(store.dir, "f1", version, _graph(**dusk))
    assert _notes(store.get("f1")) == [], why


def test_a_flow_with_no_dusk_window_has_no_note(store):
    graph = _graph()
    graph["nodes"] = [n for n in graph["nodes"] if n["type"] != "dusk"]
    graph["edges"] = [e for e in graph["edges"] if e["from"] != "d"]
    _put_file(store.dir, "f1", 4, graph)
    assert _notes(store.get("f1")) == []


def test_the_note_stands_beside_the_others_in_the_order_the_chain_runs(store):
    """A v2 file with a rotation of 23.4, a TARGET counting every sub and a
    "Single night" DUSK says all three: the rotation note (v2 -> v3), the
    auto-resume note (v4 -> v5), then the counts note (not a version step)."""
    graph = _graph(repeat="Single night")
    target = next(n for n in graph["nodes"] if n["id"] == "t")
    target["params"].update(rotation=23.4, counts="Every sub taken")
    _put_file(store.dir, "f1", 2, graph)
    keys = [n["key"] for n in _notes(store.get("f1"))]
    assert keys == ["rotation", "autoResume", "counts"]


def test_a_file_from_a_newer_build_is_still_refused(store):
    """FLOW_SCHEMA 5 is this build's own: a 6 is the future."""
    _put_file(store.dir, "f1", 6, _graph())
    with pytest.raises(KeyError):
        store.get("f1")
    rows = store.unreadable()
    assert rows and "schema 6" in rows[0]["unreadable"]
    with pytest.raises(NewerSchemaFlow):
        store.save(FlowRecord(id="f1", name="x", graph=_flow_graph()))


# ================================================================== the stamp

def test_schema_for_is_three_tier():
    """5 for Off or a DUSK that still owes the note; 4 for another v4 meaning;
    3 for none."""
    assert schema_for(_flow_graph(autoResume="Off")) == 5
    assert schema_for(_flow_graph(repeat="Single night")) == 5
    assert schema_for(_flow_graph(autoResume="On")) == 3, (
        "a stated On with no other meaning is a v3 file's reading")
    assert schema_for(_flow_graph(repeat="Single night",
                                  autoResume="On")) == 3, (
        "a stated On is a downgrade-matrix row, not a version (spec 3.6)")
    assert schema_for(_flow_graph(repeat="Nightly ×30")) == 3
    with_counts = _flow_graph(autoResume="On")
    with_counts.nodes[1].params["counts"] = "Accepted subs"
    assert schema_for(with_counts) == 4, "another v4 meaning is 4"
    with_counts.node("d").params["autoResume"] = "Off"
    assert schema_for(with_counts) == 5, "five outranks four"


def test_auto_resume_off_is_the_sixth_v4_meaning():
    assert "auto-resume off" in _v4_meanings(_flow_graph(autoResume="Off"))
    for quiet in ({"autoResume": "On"}, {"repeat": "Single night"}, {}):
        assert "auto-resume off" not in _v4_meanings(_flow_graph(**quiet)), quiet


def test_the_save_of_an_off_flow_stamps_five(store):
    rec = FlowRecord(id="f1", name="off", graph=_flow_graph(autoResume="Off"))
    store.save(rec)
    raw = json.loads((store.dir / "f1.json").read_text(encoding="utf-8"))
    assert raw["schema_version"] == 5
    assert raw["flow"]["graph"]["nodes"][0]["params"] == {"autoResume": "Off"}
    assert _notes(store.get("f1")) == []
