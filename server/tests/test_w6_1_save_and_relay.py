# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The flow side of "Measure at the first solve" (WP 6.1, #187): the measured
angle is SERVER-OWNED, written by the relay through the store's one writer,
and ruling 3 decides carry or restart exactly as it does for a save.

THE GEOMETRY is test_flows_reframe_end_to_end.py's: a 3x2 of 2.0 x 1.33 deg
panels at 25% overlap at M31's catalogue position (no observer is involved).
Its carry threshold is 0.5 x 0.25 x 1.33 deg = 9.975 arcmin, and a grid turned
by 1 deg moves its corners well under that, by 37 deg well over it.

Each case names the mutant it was shown RED under.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.config as config_mod
import astrodeck.hub as hub_mod
from astrodeck.config import ConfigStore
from astrodeck.flows import store as store_mod
from astrodeck.flows.compile import compile_plan
from astrodeck.flows.models import FlowGraph, FlowRecord
from astrodeck.flows.store import FlowStore
from astrodeck.flows.to_plan import relay_layout, to_sequence_plan
from astrodeck.sequence.models import LayoutRelayRefused

MEASURE = "Measure at the first solve"
RA, DEC = "00h 42m 44s", "+41° 16′ 09″"
WHEN = 1788313689.0


def _graph(*, rotation, angle=MEASURE, rows=2, cols=3) -> dict:
    target = {"name": "M31", "ra": RA, "dec": DEC, "rotation": rotation,
              "angle": angle, "rows": rows, "cols": cols, "overlap": 25,
              "fovX": 2.0, "fovY": 1.33}
    return {"nodes": [
                {"id": "t", "type": "target", "x": 0, "y": 0,
                 "params": target},
                {"id": "c", "type": "cycle", "x": 230, "y": 0,
                 "params": {"plan": "L 60, R 60", "cycles": 2}}],
            "edges": [
                {"from": "t", "fromPort": "target", "to": "c",
                 "toPort": "run"},
                {"from": "c", "fromPort": "pass", "to": "t",
                 "toPort": "next"}]}


def _record(graph: dict, fid: str = "f-measure") -> FlowRecord:
    return FlowRecord.model_validate({"id": fid, "name": "measure",
                                      "graph": graph})


@pytest.fixture
def store(tmp_path):
    return FlowStore(tmp_path / "flows")


def _node(store: FlowStore, fid: str = "f-measure"):
    return next(n for n in store.get(fid).graph.nodes if n.id == "t")


def _ids(store: FlowStore, fid: str = "f-measure") -> list[str]:
    rec = store.get(fid)
    plan, _ = to_sequence_plan(compile_plan(rec.graph, rec.name), rec.graph,
                               flow_id=fid, when=WHEN)
    return sorted([g.id for g in plan.groups]
                  + [t.id for t in plan.targets]
                  + [s.id for t in plan.targets for s in t.steps])


# ------------------------------------------------------------ server-owned

def test_a_stale_save_keeps_the_measured_rotation_the_anchor_and_the_ids(store):
    """A client save (an editor left open since before the night) carries the
    rotation it loaded. The measurement stands. MUTANT "rotation is the
    client's" (no override in prepare_save): the rotation reads 0.0 and the
    ids are another campaign's. RED: ``assert 0.0 == 31.0``."""
    store.save_and_report(_record(_graph(rotation=30)))
    store.save_and_report(_record(_graph(rotation=30)),
                          measured={"t": 31.0})
    after = (_node(store).params["rotation"],
             _node(store).params["frameAnchor"], _ids(store))
    assert after[0] == 31.0
    store.save(_record(_graph(rotation=30)))        # the stale editor
    assert (_node(store).params["rotation"],
            _node(store).params["frameAnchor"], _ids(store)) == after


def test_the_put_route_keeps_it_too(tmp_path, monkeypatch):
    cfg = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", cfg)
    monkeypatch.setattr(hub_mod, "config_store", cfg)
    monkeypatch.setattr(app_module, "config_store", cfg)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    flows = FlowStore(tmp_path / "flows")
    monkeypatch.setattr(app_module, "flow_store", flows)
    with TestClient(app_module.create_app()) as client:
        body = {"flow": {"name": "measure", "graph": _graph(rotation=30)}}
        fid = client.post("/api/flows", json=body).json()["id"]
        flows.save_and_report(_record(_graph(rotation=30), fid),
                              measured={"t": 37.0})
        before = (_node(flows, fid).params["rotation"],
                  _node(flows, fid).params["frameAnchor"], _ids(flows, fid))
        assert before[0] == 37.0
        r = client.put(f"/api/flows/{fid}", json=body)
        assert r.status_code == 200, r.text
        assert r.json()["reanchored"] == []
        assert (_node(flows, fid).params["rotation"],
                _node(flows, fid).params["frameAnchor"],
                _ids(flows, fid)) == before


def test_switching_out_of_the_mode_is_a_normal_edit(store):
    """MUTANT "kept whatever the angle": the rotation stays 31.0."""
    store.save_and_report(_record(_graph(rotation=30)))
    store.save_and_report(_record(_graph(rotation=30)), measured={"t": 31.0})
    store.save(_record(_graph(rotation=10, angle="Camera fixed at PA")))
    node = _node(store)
    assert (node.params["angle"], node.params["rotation"]) == (
        "Camera fixed at PA", 10)


def test_switching_into_the_mode_takes_the_clients_rotation_once(store):
    store.save(_record(_graph(rotation=20, angle="Camera fixed at PA")))
    store.save(_record(_graph(rotation=20)))
    assert _node(store).params["rotation"] == 20


def test_a_new_flow_in_the_mode_takes_what_it_was_sent(store):
    store.save(_record(_graph(rotation=-1)))
    assert _node(store).params["rotation"] == -1


def test_a_measured_value_only_lands_on_a_block_in_the_mode(store):
    store.save(_record(_graph(rotation=10, angle="Camera fixed at PA")))
    store.save_and_report(_record(_graph(rotation=10,
                                         angle="Camera fixed at PA")),
                          measured={"t": 99.0})
    assert _node(store).params["rotation"] == 10


# ---------------------------------------------------------- the downgrade

def test_a_block_in_the_mode_stamps_a_schema_an_older_build_refuses(tmp_path):
    """A 1x1 block is no multi-panel block, so nothing else stamps it: an
    older build reads an unknown angle from the rotation, and a stored
    rotation of 31 would command the rotator. MUTANT "not a v4 meaning":
    the file is stamped 3. RED: ``assert 3 == 4``."""
    flows = FlowStore(tmp_path / "flows")
    flows.save(_record(_graph(rotation=31, rows=1, cols=1)))
    raw = json.loads((tmp_path / "flows" / "f-measure.json")
                     .read_text(encoding="utf-8"))
    assert raw["schema_version"] == store_mod.V4_SCHEMA
    flows.save(_record(_graph(rotation=31, rows=1, cols=1,
                              angle="Camera fixed at PA")))
    raw = json.loads((tmp_path / "flows" / "f-measure.json")
                     .read_text(encoding="utf-8"))
    assert raw["schema_version"] == store_mod.V4_SCHEMA   # one panel, fixed


# ----------------------------------------------------------------- the relay

def test_a_turn_under_the_threshold_keeps_every_id_and_the_anchor(store):
    """MUTANT "the relay always re-anchors": the ids move and a row is
    listed. RED: ``assert {...} == {...}`` on the id sets."""
    store.save(_record(_graph(rotation=30)))
    before = _ids(store)
    anchor = _node(store).params["frameAnchor"]
    got = relay_layout(store, "f-measure", "t", 31.0, now=WHEN)
    assert got.reanchored is None
    assert _node(store).params["rotation"] == 31.0
    assert _node(store).params["frameAnchor"] == anchor
    assert _ids(store) == before
    assert got.group.pa_deg == 31.0 and got.group.measure_angle
    assert sorted(t.id for t in got.members) == sorted(
        i for i in before if i in {t.id for t in got.members})
    assert len(got.members) == 6


def test_a_turn_over_the_threshold_restarts_and_the_ids_are_the_next_compiles(
        store):
    """MUTANT "the relay keeps the old anchor": the ids stay and the row is
    missing. The returned ids are what a fresh compile of the stored flow
    makes, which is what night two will find."""
    store.save(_record(_graph(rotation=30)))
    before = _ids(store)
    got = relay_layout(store, "f-measure", "t", 37.0, now=WHEN)
    assert got.reanchored is not None
    assert got.reanchored["node_id"] == "t"
    assert got.reanchored["reason"] == "move"
    assert got.reanchored["max_move_deg"] > got.reanchored["threshold_deg"]
    after = _ids(store)
    assert not set(before) & set(after)
    given = sorted([got.group.id] + [t.id for t in got.members]
                   + [s.id for t in got.members for s in t.steps])
    assert given == after
    assert _node(store).params["rotation"] == 37.0


def test_a_block_with_no_angle_yet_restarts_on_the_angle_reason(store):
    store.save(_record(_graph(rotation=-1)))
    got = relay_layout(store, "f-measure", "t", 37.0, now=WHEN)
    assert got.reanchored["reason"] == "angle"
    assert got.reanchored["max_move_deg"] is None


def test_the_relay_is_not_an_edit_or_a_run(store, tmp_path):
    """It stamps no run: ``last_run`` and ``last_result`` are the store's
    bookkeeping and the relay is neither a run's start nor its end."""
    store.save(_record(_graph(rotation=30)))
    store.touch_run("f-measure", ts=1234.0, result="ok")
    relay_layout(store, "f-measure", "t", 31.0, now=WHEN)
    rec = store.get("f-measure")
    assert (rec.last_run, rec.last_result) == (1234.0, "ok")


def test_the_relay_reports_the_saved_times_the_session_compares(store):
    store.save(_record(_graph(rotation=30)))
    prior = store.get("f-measure").updated_ts
    got = relay_layout(store, "f-measure", "t", 31.0, now=WHEN)
    assert got.prior_updated_ts == prior
    assert got.saved_ts == store.get("f-measure").updated_ts
    assert got.saved_ts >= prior


def test_a_save_that_lands_between_the_read_and_the_write_is_not_lost(store):
    """The store has no lock (every route saves through a worker thread, the
    last writer wins), so the relay reads the saved time again just before
    it writes and starts over if it moved. MUTANT "no re-read": the relay
    writes its stale copy over the operator's rename. RED:
    ``assert 'renamed' == ...``."""
    store.save(_record(_graph(rotation=30)))
    real_get = store.get
    calls = {"n": 0}

    def get(fid):
        out = real_get(fid)
        calls["n"] += 1
        if calls["n"] == 1:
            # The operator saves (a rename) after the relay's first read.
            store.save(out.model_copy(update={"name": "renamed"}))
        return out

    store.get = get
    relay_layout(store, "f-measure", "t", 31.0, now=WHEN)
    store.get = real_get
    assert store.get("f-measure").name == "renamed"
    assert _node(store).params["rotation"] == 31.0


@pytest.mark.parametrize("why, mutate, words", [
    ("no such flow", lambda s: s.delete("f-measure"), "no longer exists"),
    ("another mode", lambda s: s.save(_record(_graph(
        rotation=10, angle="Camera fixed at PA"))), "no longer"),
    ("no such block", lambda s: s.save(_record({
        "nodes": [{"id": "z", "type": "target", "x": 0, "y": 0,
                   "params": {"name": "M31", "ra": RA, "dec": DEC}}],
        "edges": []})), "no longer"),
])
def test_the_relay_refuses_in_words_and_changes_nothing(store, why, mutate,
                                                        words):
    store.save(_record(_graph(rotation=30)))
    mutate(store)
    with pytest.raises(LayoutRelayRefused) as e:
        relay_layout(store, "f-measure", "t", 37.0, now=WHEN)
    assert words in str(e.value), (why, str(e.value))


def test_the_shipped_examples_are_read_only_and_the_relay_says_so(store):
    with pytest.raises(LayoutRelayRefused) as e:
        relay_layout(store, "m31", "t", 37.0, now=WHEN)
    assert "read-only" in str(e.value) or "no longer" in str(e.value)


# --------------------------------------------------------------- the binding

def test_the_apps_engine_relays_through_the_store_it_has_now(tmp_path,
                                                             monkeypatch):
    """The callback is installed on the app's engine beside the dispatcher and
    reads ``flow_store`` when it runs. MUTANT "no binding" (the line removed):
    ``assert None is not None``. MUTANT "bound at import" (the partial of the
    store the module had): the relay writes the real library, not this one.
    RED: the rotation reads 30.0."""
    flows = FlowStore(tmp_path / "flows")
    monkeypatch.setattr(app_module, "flow_store", flows)
    flows.save(_record(_graph(rotation=30)))
    assert app_module.engine.layout_relay is not None
    got = app_module.engine.layout_relay("f-measure", "t", 31.0)
    assert got.group.pa_deg == 31.0
    assert _node(flows).params["rotation"] == 31.0


# ------------------------------------------------------------------ the lock

def test_two_saves_of_one_flow_at_once_both_land(store, monkeypatch):
    """A route's save and the relay's run on worker threads. Each reads the
    file it replaces, applies the save rules and writes, so without a lock the
    later write carries the earlier one's stale copy. The first save is held
    inside the save rules until the second has read the same prior (or half a
    second passes, which is what happens with the lock: the second cannot
    enter), then both go on.

    MUTANT "no lock" (``save_and_report`` does not take ``_save_lock``): both
    saves read rotation 30 and the later write drops the other's change.
    RED: ``assert 30 == 31.0`` or ``assert 'measure' == 'renamed'``."""
    import threading
    from astrodeck.flows import store as store_module

    store.save(_record(_graph(rotation=30)))
    real = store_module.prepare_save
    entered = [threading.Event(), threading.Event()]
    seen = []

    def prepare(record, prior, **kw):
        me = len(seen)
        seen.append(prior.graph.nodes[0].params["rotation"])
        entered[me].set()
        entered[1 - me].wait(0.5)
        return real(record, prior, **kw)

    monkeypatch.setattr(store_module, "prepare_save", prepare)
    renamed = _record(_graph(rotation=30)).model_copy(update={"name": "renamed"})
    errors = []

    def relay():
        try:
            store.save_and_report(_record(_graph(rotation=30)),
                                  measured={"t": 31.0})
        except Exception as e:      # noqa: BLE001 - reported below
            errors.append(e)

    def route():
        try:
            store.save(renamed)
        except Exception as e:      # noqa: BLE001 - reported below
            errors.append(e)

    a, b = threading.Thread(target=relay), threading.Thread(target=route)
    a.start()
    entered[0].wait(2)
    b.start()
    a.join(10)
    b.join(10)
    monkeypatch.setattr(store_module, "prepare_save", real)
    assert errors == [], errors
    stored = store.get("f-measure")
    assert next(n for n in stored.graph.nodes
                if n.id == "t").params["rotation"] == 31.0
    assert stored.name == "renamed"
