# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A save's bookkeeping comes from the one read that can refuse (#364; spec
3.3, #350's sibling).

THE FAULT (#364). ``_persist_flow`` re-derived ``created_ts``, ``last_run``
and ``last_result`` from ``flow_store.get``: a walk of the whole library
through ``FlowStore._entries``, which turns ANY failure to read a file into
an unreadable row. So a transient read error on the flow's file at that
moment (a Windows sharing violation from antivirus or an indexer) made
``get`` answer "no such flow", and the save wrote the flow as created now
and never run. #350 had closed the same hole for the anchors, but
``FlowStore._stored`` reads the file separately, a moment later, so when the
listing's read failed and ``_stored``'s worked the save went ahead and the
card of a flow that had run said NEVER RUN, with nothing said to anyone.

THE RULE. ``FlowStore.save_and_report`` reads the file it replaces once
(``_stored``) and takes the three fields (``store.BOOKKEEPING``) from that
record (``_bookkeeping``), whatever the record it is given carries.
``_persist_flow`` reads nothing itself. A failure of that one read refuses
the save (409 ``stored_unreadable``, #350), and a failure of the listing's
read changes nothing.

INTERMITTENT BY NATURE, so every case here INJECTS the failure: ``read_json``
raising ``PermissionError`` from a named caller, ``_entries`` for the
listing and ``_stored`` for the save's own read. The history under test is
a flow that RAN (``touch_run``: ``last_run`` 1234.0, ``last_result`` "ok"),
because a flow that never ran looks the same reset or not.

Every named mutation was run in a private copy of ``server/`` under the
session scratchpad (``S5-ROUTES-mut``; the cases S7 changed for #433 in
``S7-STORE-mut``), from byte copies, never in the shared tree (#254); the
failure each produced is quoted where it went red.
"""
from __future__ import annotations

import json
import shutil
import sys
import time

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.flows.store as store_mod
from astrodeck.flows.models import FlowGraph, FlowNode, FlowRecord
from astrodeck.flows.store import BOOKKEEPING, FlowStore

#: The run ``touch_run`` records on the flow before each case.
RAN = {"last_run": 1234.0, "last_result": "ok"}

GRAPH = {"nodes": [
    {"id": "t", "type": "target", "x": 0, "y": 0,
     "params": {"name": "M42", "ra": "05h 35m 17s", "dec": "-05 23 28"}},
    {"id": "c", "type": "capture", "x": 200, "y": 0,
     "params": {"filter": "L", "exposure": 60, "count": 3}}],
    "edges": [{"from": "t", "fromPort": "target", "to": "c",
               "toPort": "run"}]}


def _record(fid: str = "f1", **over) -> FlowRecord:
    return FlowRecord(id=fid, name="ran once",
                      graph=FlowGraph.model_validate(GRAPH), **over)


def _on_disk(path) -> dict:
    """The three fields as the file holds them."""
    flow = json.loads(path.read_text(encoding="utf-8"))["flow"]
    return {k: flow[k] for k in BOOKKEEPING}


def _fail_reads(monkeypatch, caller: str, name: str, *, times: int | None
                = None) -> list:
    """Make ``read_json`` raise ``PermissionError`` when ``caller`` reads the
    file called ``name``: every time, or the first ``times`` times. Every
    other read works. Returns the list of reads it failed."""
    real = store_mod.read_json
    fired: list = []

    def fake(path):
        if (sys._getframe(1).f_code.co_name == caller
                and getattr(path, "name", "") == name
                and (times is None or len(fired) < times)):
            fired.append(path)
            raise PermissionError(13, "sharing violation", str(path))
        return real(path)

    monkeypatch.setattr(store_mod, "read_json", fake)
    return fired


# ================================================================ the route

@pytest.fixture
def client(isolated_config, tmp_path, monkeypatch):
    store = FlowStore(tmp_path / "flows")
    monkeypatch.setattr(app_module, "flow_store", store)
    app = app_module.create_app()
    isolated_config.sweep()
    with TestClient(app) as c:
        c.store = store
        yield c


def _ran_flow(client) -> tuple[str, object, dict]:
    """A flow saved through the route and then run once (``touch_run``):
    its id, its file and its bookkeeping as the file holds it."""
    r = client.post("/api/flows", json={"flow": {"name": "ran once",
                                                 "graph": GRAPH}})
    assert r.status_code == 200, r.text
    fid = r.json()["id"]
    assert client.store.touch_run(fid, ts=RAN["last_run"],
                                  result=RAN["last_result"])
    path = client.store.dir / f"{fid}.json"
    history = _on_disk(path)
    assert {k: history[k] for k in RAN} == RAN, "premise: the run is recorded"
    return fid, path, history


class TestTheListingReadDecidesNothing:
    def test_a_listing_read_error_leaves_the_history(self, client,
                                                     monkeypatch):
        """PUT the flow while every read the LISTING makes of its file
        (``_entries``) raises ``PermissionError``, and the save's own read
        works: the save succeeds, and the answer and the file keep the
        flow's ``created_ts``, ``last_run`` and ``last_result``.

        THE PREMISE IS SHOWN ON A LIST (S7, #433). A save walked the
        library for its quota until #433, so the PUT itself proved the
        injection reached the listing's read. Since #433 a save lists file
        names and reads no other flow, so the injection would never fire
        in the PUT and the case could pass whatever the save did with a
        listing; a ``GET /api/flows`` under it shows it firing first.

        RED under mutant "the prior is read through the listing"
        (``save_and_report`` taking the three from a walk of the library,
        ``self._scan()[0]`` since #433 removed ``_on_disk``, rather than
        from ``_stored``'s record), the flow that ran answered and stored
        as created at the save and never run, observed (S7, on the premise
        shown by the GET):

            E   AssertionError: the answer reset the history
            E   assert {'created_ts'...st_run': None} == {'created_ts'..._run': 1234.0}
            E     Differing items:
            E     {'last_run': None} != {'last_run': 1234.0}
            E     {'last_result': ''} != {'last_result': 'ok'}
            E     {'created_ts': 1790645805.932444} != {'created_ts': 1790645805.8879788}

        RED the same way under mutant "the route reads the prior itself"
        (HEAD's ``_persist_flow``: its ``flow_store.get`` put back, and the
        store's ``_bookkeeping`` taken out), which is the code #364 was
        filed against (``{'created_ts': 1790646667.3327284} !=
        {'created_ts': 1790646667.2853444}`` and the two above), and under
        "the store keeps the record's bookkeeping" (the ``**_bookkeeping``
        taken out and no ``get`` put back), where the PUT's own record, built
        fresh, is what the file got; both run again in S7.
        """
        fid, path, history = _ran_flow(client)
        fired = _fail_reads(monkeypatch, "_entries", path.name)
        assert client.get("/api/flows").status_code == 200
        assert fired, "premise: the listing's read of the file failed"
        r = client.put(f"/api/flows/{fid}", json={"flow": {
            "name": "ran once", "graph": GRAPH}})
        assert r.status_code == 200, r.text
        assert {k: r.json()[k] for k in BOOKKEEPING} == history, \
            "the answer reset the history"
        assert _on_disk(path) == history, "the file reset the history"

    def test_no_walk_of_the_library_per_save(self, client, monkeypatch):
        """No read of its own, and none for the quota: ``_persist_flow``
        adds no walk to find the prior (#364), and since S7 the store's
        quota check lists file names instead of walking the library
        (#433), so a save walks it no times. (S5 left the quota walk and
        asserted at most one; ``test_s7_store_save_cost.py`` holds that no
        other flow is read at all.)

        RED under mutant "the route reads the prior itself" (HEAD before
        #364: ``_persist_flow``'s ``flow_store.get`` put back), observed:

            E   AssertionError: a save walked the library 1 times
            E   assert [1] == []
            E     Left contains one more item: 1

        and under "existing built from _on_disk()" (#433's quota walk put
        back) and "the prior is read through the listing", the same three
        lines.
        """
        fid, _path, _history = _ran_flow(client)
        walks: list = []
        real = FlowStore._entries

        def counted(self, *a, **kw):
            walks.append(1)
            return real(self, *a, **kw)

        monkeypatch.setattr(FlowStore, "_entries", counted)
        r = client.put(f"/api/flows/{fid}", json={"flow": {
            "name": "ran once", "graph": GRAPH}})
        assert r.status_code == 200, r.text
        assert walks == [], f"a save walked the library {len(walks)} times"


class TestAReadErrorRefusesAndNeverResets:
    def test_the_saves_own_read_failing_refuses_and_keeps_the_history(
            self, client, monkeypatch):
        """``_stored``'s read raises once: 409 ``stored_unreadable``, the
        file byte for byte what it was, the run still on it. The retry, with
        the read working, saves and keeps the history (control).

        RED under mutant "swallow every read error" (``_stored``'s handlers
        replaced by ``except Exception: return None``, as before #350), the
        save went ahead and, the one read now answering "no such flow",
        wrote the flow as new, observed (the answer's text cut):

            E   AssertionError: {"id":"2b8ac4241cea453980c8d6266d520455","name":"ran once",...,"created_ts":1790608542.1507237,"updated_ts":1790608542.1507237,"last_run":null,"last_result":"",...}
            E   assert 200 == 409
            E    +  where 200 = <Response [200 OK]>.status_code

        RED under "the store keeps the record's bookkeeping" at the retry,
        which then wrote the PUT's fresh record over the run.
        """
        fid, path, history = _ran_flow(client)
        before = path.read_bytes()
        fired = _fail_reads(monkeypatch, "_stored", path.name, times=1)
        r = client.put(f"/api/flows/{fid}", json={"flow": {
            "name": "ran once", "graph": GRAPH}})
        assert fired, "premise: the save's own read failed"
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "stored_unreadable"
        assert path.read_bytes() == before, "the refused save wrote"

        retry = client.put(f"/api/flows/{fid}", json={"flow": {
            "name": "ran once", "graph": GRAPH}})
        assert retry.status_code == 200, retry.text
        assert _on_disk(path) == history

    def test_control_both_reads_failing_refuses(self, client, monkeypatch):
        """Control: the listing's read and the save's own both failing is
        a refusal too, never a reset (it refused before #364 as well, so no
        #364 mutant turns it red; "swallow every read error" does, with
        ``assert 200 == 409``)."""
        fid, path, _history = _ran_flow(client)
        before = path.read_bytes()
        real = store_mod.read_json

        def fake(p):
            if (sys._getframe(1).f_code.co_name in ("_entries", "_stored")
                    and getattr(p, "name", "") == path.name):
                raise PermissionError(13, "sharing violation")
            return real(p)

        monkeypatch.setattr(store_mod, "read_json", fake)
        r = client.put(f"/api/flows/{fid}", json={"flow": {
            "name": "ran once", "graph": GRAPH}})
        assert r.status_code == 409, r.text
        assert path.read_bytes() == before


# ================================================================ the store

@pytest.fixture
def store(tmp_path):
    return FlowStore(tmp_path / "flows")


class TestTheStoreOwnsTheBookkeeping:
    def test_a_listing_read_error_leaves_the_history_in_the_store(
            self, store, monkeypatch):
        """The store half of the first case, with no route: every door
        that saves through ``save_and_report`` keeps the history. The
        injection is shown reaching the listing's read on ``listing()``
        first, as the route case shows it on a GET (S7, #433: a save no
        longer lists anything that could fire it).

        RED under mutant "the prior is read through the listing", observed
        (S7, on the premise shown by ``listing()``):

            E   AssertionError: assert {'created_ts'...st_run': None} == {'created_ts'..._run': 1234.0}
            E     Differing items:
            E     {'last_run': None} != {'last_run': 1234.0}
            E     {'last_result': ''} != {'last_result': 'ok'}
            E     {'created_ts': 1790645809.8161893} != {'created_ts': 1790645809.7782638}

        and under "the route reads the prior itself" and "the store keeps
        the record's bookkeeping", where the store wrote what the record
        carried (a record built fresh never ran), with the same three items;
        all three run again in S7.
        """
        store.save(_record())
        store.touch_run("f1", ts=RAN["last_run"], result=RAN["last_result"])
        path = store.dir / "f1.json"
        history = _on_disk(path)
        fired = _fail_reads(monkeypatch, "_entries", path.name)
        store.listing()
        assert fired, "premise: the listing's read of the file failed"
        saved, _migrated, _rows = store.save_and_report(_record())
        assert {k: getattr(saved, k) for k in BOOKKEEPING} == history
        assert _on_disk(path) == history

    def test_a_record_cannot_forge_a_run(self, store):
        """A NEW flow saved with a run and a creation time on its record is
        stored as created at the save and never run: the store, not the
        record, owns the three, so no door can forge a green card.

        RED under mutant "the store keeps the record's bookkeeping" (the
        ``**_bookkeeping(prior, now)`` taken out of ``save_and_report``),
        observed:

            E   AssertionError: assert {'created_ts'...ast_run': 1.0} == {'created_ts'...st_run': None}
            E     Omitting 1 identical items, use -vv to show
            E     Differing items:
            E     {'last_run': 1.0} != {'last_run': None}
            E     {'last_result': 'ok'} != {'last_result': ''}

        (the created_ts compared is the stored record's own, so it is the
        identical item; the next line checks it lies within the save.) Red
        the same way under "the route reads the prior itself", which is
        HEAD: there only the route kept a client from forging a run, and any
        other door could. The same mutant turns test_flows_routes.py's two
        ``TestServerOwnedFields`` cases red (``assert (1.0 is None)``,
        ``assert 0.0 == 1790608538.7938323``), since the route now leaves
        the three to the store.
        """
        t0 = time.time()
        saved = store.save(_record(created_ts=5.0, last_run=1.0,
                                   last_result="ok"))
        t1 = time.time()
        stored = _on_disk(store.dir / "f1.json")
        assert stored == {"created_ts": saved.created_ts, "last_run": None,
                          "last_result": ""}
        assert t0 <= stored["created_ts"] <= t1

    def test_an_update_keeps_the_files_history_not_the_records(self, store):
        """An update whose record says created at 0 and never run keeps the
        file's creation time and run.

        RED under mutant "the store keeps the record's bookkeeping",
        observed:

            E   AssertionError: assert {'created_ts'...st_run': None} == {'created_ts'..._run': 1234.0}
            E     Differing items:
            E     {'last_run': None} != {'last_run': 1234.0}
            E     {'last_result': ''} != {'last_result': 'ok'}
            E     {'created_ts': 0.0} != {'created_ts': 1790608538.0055645}

        and the same way under "the route reads the prior itself".
        """
        store.save(_record())
        store.touch_run("f1", ts=RAN["last_run"], result=RAN["last_result"])
        path = store.dir / "f1.json"
        history = _on_disk(path)
        store.save(_record(created_ts=0.0, last_run=None, last_result=""))
        assert _on_disk(path) == history

    def test_control_a_copied_file_lends_no_history(self, store):
        """Control: ``b.json`` is a copy of ``a.json``, which ran, so the
        record in it still says ``a`` (#353 item 5). Its run is A's, and
        flow B saved over it is created at the save and never run; A keeps
        its own. ``_stored`` answers None for a record with another id, as
        the listing's ``get("b")`` found no record called ``b`` before."""
        store.save(_record("a"))
        store.touch_run("a", ts=RAN["last_run"], result=RAN["last_result"])
        a = store.dir / "a.json"
        a_history = _on_disk(a)
        shutil.copyfile(a, store.dir / "b.json")
        t0 = time.time()
        store.save(_record("b"))
        b = _on_disk(store.dir / "b.json")
        assert (b["last_run"], b["last_result"]) == (None, "")
        assert b["created_ts"] >= t0
        assert _on_disk(a) == a_history
