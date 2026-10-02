# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Ruling 2 at the doors: every save switches ``counts`` and its answer says
so, and a read says what a dormant session keeps (#189 Revision 2 ruling 2,
spec 3.3; task S3-A).

The switch itself is ``save_rules.prepare_save``, applied by the store's one
writer (``FlowStore.save_and_report``). What this file holds is the ROUTE
half, which the pure tests cannot see:

* ``_persist_flow`` writes through ``save_and_report`` and answers with what
  the save did: ``migrated`` carries ``{"key": "counts", ...}`` when the save
  switched a TARGET or POOL to accepted subs, and ``reanchored`` lists every
  block whose counts the save restarted (empty here; the geometry half is
  test_flows_reframe_end_to_end.py). POST, PUT, the wizard and the quick flow
  all answer through it, so a generator that wrote the old meaning is still
  switched at the door, and said to be.
* ``GET /api/flows/{id}`` adds ruling 2's second sentence to the counts note,
  "Its armed session keeps its count until you CONTINUE.", when the session
  Run would continue (``current_for_flow``) is dormant, and only then. The
  ledger is counted by its frozen plan's ``count_mode``, so a save does not
  recount it; CONTINUE asks first when the recount changes a total (spec
  5.9, ``accept_recount``; S4 orchestrator ruling 2, #348).

No site data anywhere: M31's and M16's catalogue positions, never an
observer's. Every test names the mutant it kills and quotes the failure it
produced. Each mutant was written over a byte-for-byte copy of the file in a
private copy of ``server/`` under the session scratchpad
(``s3-a-routes-k7m2``); the shared tree was never mutated.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.config as config_mod
import astrodeck.hub as hub_mod
from astrodeck.config import ConfigStore
from astrodeck.flows.save_rules import ACCEPTED_SUBS
from astrodeck.flows.store import COUNTS_NOTE, FlowStore
from astrodeck.persist import ensure_dir
from astrodeck.sequence.session import Session, session_store

#: What a read says while a TARGET or POOL counts every sub taken.
COUNTS = {"key": "counts", "note": COUNTS_NOTE}


@pytest.fixture
def client(tmp_path, monkeypatch):
    """test_flows_routes' isolation: a throwaway config store, flow library
    and captures directory (the session store resolves ``hub.CAPTURE_DIR``
    live, so the sessions these tests seed land there too)."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_mod, "config_store", store)
    monkeypatch.setattr(app_module, "config_store", store)
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(app_module, "flow_store", FlowStore(tmp_path / "flows"))
    with TestClient(app_module.create_app()) as c:
        yield c


def _graph(*, target_counts=None, pool_counts="Every sub taken") -> dict:
    """dusk -> TARGET -> CAPTURE, and a POOL beside it. ``None`` leaves the
    key out, which is how every flow saved before S3 holds it."""
    target = {"name": "M31", "ra": "00h 42m 44s", "dec": "+41 16 09"}
    if target_counts is not None:
        target["counts"] = target_counts
    pool = {"members": "M16, M17"}
    if pool_counts is not None:
        pool["counts"] = pool_counts
    return {
        "nodes": [
            {"id": "d", "type": "dusk", "x": 30, "y": 60,
             "params": {"offset": -30, "stop": "Dawn", "minAlt": 30}},
            {"id": "t", "type": "target", "x": 260, "y": 60, "params": target},
            {"id": "p", "type": "pool", "x": 260, "y": 260, "params": pool},
            {"id": "c", "type": "capture", "x": 490, "y": 60,
             "params": {"filter": "L", "exposure": 120, "gain": 100,
                        "bin": "1", "count": 12, "goal": 0}},
        ],
        "edges": [
            {"from": "d", "fromPort": "window", "to": "t", "toPort": "arm"},
            {"from": "t", "fromPort": "target", "to": "c", "toPort": "run"},
        ],
    }


def _put_file(root, fid: str, schema: int, graph: dict):
    """A flow file as an older build left it on disk."""
    ensure_dir(root)
    path = root / f"{fid}.json"
    path.write_text(json.dumps({"schema_version": schema, "id": fid,
                                "flow": {"id": fid, "name": f"flow {fid}",
                                         "graph": graph}}), encoding="utf-8")
    return path


def _counts_on_disk(path) -> dict[str, object]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {n["id"]: n["params"].get("counts")
            for n in raw["flow"]["graph"]["nodes"]
            if n["type"] in ("target", "pool")}


SWITCHED = [{"key": "counts", "note": app_module.COUNTS_SWITCHED_NOTE}]


# ============================================================ the switch

class TestEverySaveSwitchesAndSaysSo:
    def test_a_put_of_a_v3_flow_switches_every_target_and_pool(self, client,
                                                                tmp_path):
        """The editor's own round trip: a v3 file whose TARGET has no
        ``counts`` key and whose POOL says "Every sub taken" is read with
        ruling 2's note, PUT back as it was read, and written with "Accepted
        subs" on both. The answer says so in ``migrated`` and lists no
        re-anchored block, and the next read is quiet.

        RED under mutant "not wired" (``_persist_flow`` writes through
        ``flow_store.save`` and answers ``[]`` for both lists), observed:

            AssertionError: the save's answer says it switched the counts
            assert [] == [{'key': 'cou...es included'}]
              Right contains one more item: {'key': 'counts', 'note': 'now
              counts accepted subs only: this save switched every TARGET and
              POOL that counted every sub taken, rejected ones included'}

        RED under mutant "the store skips the rules" (``flows/store.py``:
        ``save_and_report``'s ``prepare_save`` call replaced by ``migrated,
        reanchored = [], []``, so nothing is switched or anchored), with the
        same two lines, at the answer, before the file is read.
        """
        path = _put_file(tmp_path / "flows", "f1", 3,
                         _graph(target_counts=None))
        got = client.get("/api/flows/f1")
        assert got.status_code == 200, got.text
        assert got.json()["migrated"] == [COUNTS], "premise: the read says it"

        put = client.put("/api/flows/f1", json={"flow": got.json()})
        assert put.status_code == 200, put.text
        answer = put.json()
        assert answer["migrated"] == SWITCHED, (
            "the save's answer says it switched the counts")
        assert answer["reanchored"] == []
        assert _counts_on_disk(path) == {"t": ACCEPTED_SUBS,
                                         "p": ACCEPTED_SUBS}
        assert client.get("/api/flows/f1").json()["migrated"] == []

    def test_a_post_says_so_and_a_save_that_switched_nothing_says_nothing(
            self, client, tmp_path):
        """A new flow POSTed in the old shape is switched and says so; saving
        the answer again switches nothing, so ``migrated`` is empty. The note
        reports what THIS save did, never what the flow once was.

        RED under mutant "always say counts" (``_save_answer`` writes the
        counts entry whatever ``migrated`` holds), observed at the second
        save:

            AssertionError: a save that switched nothing says nothing
            assert [{'key': 'cou...es included'}] == []
              Left contains one more item: {'key': 'counts', 'note': 'now
              counts accepted subs only: this save switched every TARGET and
              POOL that counted every sub taken, rejected ones included'}

        Red too under "not wired", at the first save.
        """
        first = client.post("/api/flows", json={"flow": {
            "name": "old shape", "graph": _graph(target_counts=None)}})
        assert first.status_code == 200, first.text
        assert first.json()["migrated"] == SWITCHED
        fid = first.json()["id"]
        assert _counts_on_disk(tmp_path / "flows" / f"{fid}.json") == {
            "t": ACCEPTED_SUBS, "p": ACCEPTED_SUBS}

        again = client.put(f"/api/flows/{fid}", json={"flow": first.json()})
        assert again.status_code == 200, again.text
        assert again.json()["migrated"] == [], (
            "a save that switched nothing says nothing")
        assert again.json()["reanchored"] == []

    def test_the_answer_keeps_the_wire_alias(self, client):
        """The answer is built by hand now (the record plus the report), and
        ``FlowEdge``'s source field is ``from_`` with alias ``from``: a dump
        without ``by_alias`` would answer every wire as ``from_``, and the
        editor, which stores the answer, would lose every wire on the next
        save.

        RED under mutant "no alias" (``_save_answer`` dumps with
        ``by_alias=False``), observed:

            AssertionError: {'fromPort': 'window', 'from_': 'd', 'id':
            '623e64bc1d85', 'to': 't', ...}
            assert ('from' in {'fromPort': 'window', 'from_': 'd', 'id':
            '623e64bc1d85', 'to': 't', ...})
        """
        out = client.post("/api/flows", json={"flow": {
            "name": "wires", "graph": _graph()}}).json()
        for e in out["graph"]["edges"]:
            assert "from" in e and "from_" not in e, e


class TestTheGeneratorsGoThroughTheSameDoor:
    """A generated flow is saved by ``_persist_flow`` too, so whatever the
    generator wrote, the door applies the rules and the answer carries the
    report. The generators create accepted subs already (``create_params``),
    so each is wrapped here to hand the door the OLD meaning, as a stale
    generator would: the switch, and the sentence, must still happen."""

    @staticmethod
    def _forget_counts(record):
        nodes = [n.model_copy(update={"params": {
                     k: v for k, v in n.params.items() if k != "counts"}})
                 if n.type in ("target", "pool") else n
                 for n in record.graph.nodes]
        return record.model_copy(update={
            "graph": record.graph.model_copy(update={"nodes": nodes})})

    def test_the_wizard(self, client, tmp_path, monkeypatch):
        """RED under mutant "the wizard saves around the door" (the wizard
        route's ``_persist_flow(answer.record)`` replaced by
        ``(await asyncio.to_thread(flow_store.save, answer.record))
        .model_dump(mode="json", by_alias=True)``), observed:

            AssertionError: assert [] == [{'key': 'cou...es included'}]
              Right contains one more item: {'key': 'counts', 'note': 'now
              counts accepted subs only: this save switched every TARGET and
              POOL that counted every sub taken, rejected ones included'}

        (The store switched the counts; only the door can say it did.)
        """
        real = app_module.flow_wizard.generate_answer

        def stale(*a, **kw):
            answer = real(*a, **kw)
            return type(answer)(record=self._forget_counts(answer.record),
                                notes=answer.notes)

        monkeypatch.setattr(app_module.flow_wizard, "generate_answer", stale)
        r = client.post("/api/flows/wizard", json={
            "kind": app_module.flow_wizard.KIND_DEEP_SKY, "target": "M31"})
        assert r.status_code == 200, r.text
        assert r.json()["migrated"] == SWITCHED
        assert r.json()["reanchored"] == []
        path = tmp_path / "flows" / f"{r.json()['id']}.json"
        assert set(_counts_on_disk(path).values()) == {ACCEPTED_SUBS}

    def test_the_quick_flow(self, client, tmp_path, monkeypatch):
        """RED under mutant "the quick flow saves around the door"
        (``saved = await _persist_flow(record)`` replaced by ``saved =
        (await asyncio.to_thread(flow_store.save, record)).model_dump(
        mode="json", by_alias=True)``), observed:

            AssertionError: assert [] == [{'key': 'cou...es included'}]
              Right contains one more item: {'key': 'counts', 'note': 'now
              counts accepted subs only: this save switched every TARGET and
              POOL that counted every sub taken, rejected ones included'}
        """
        real = app_module.flow_wizard.quick

        def stale(*a, **kw):
            return self._forget_counts(real(*a, **kw))

        monkeypatch.setattr(app_module.flow_wizard, "quick", stale)
        r = client.post("/api/flows/quick", json={
            "target": {"name": "M31", "ra": "00h 42m 44s",
                       "dec": "+41 16 09"},
            "subs": 3, "filters": [], "run": False})
        assert r.status_code == 200, r.text
        flow = r.json()["flow"]
        assert flow["migrated"] == SWITCHED
        assert flow["reanchored"] == []
        path = tmp_path / "flows" / f"{flow['id']}.json"
        assert set(_counts_on_disk(path).values()) == {ACCEPTED_SUBS}


# ================================================ the dormant session's line

def _session(fid: str, status: str, created: float) -> Session:
    s = Session(name=f"{fid}@{created}", created_ts=created, status=status,
                origin="flow", origin_id=fid)
    session_store.save(s)
    return s


WITH_ADDENDUM = [{"key": "counts",
                  "note": f"{COUNTS_NOTE} {app_module.COUNTS_DORMANT_ADDENDUM}"}]


class TestTheReadSaysWhatTheSessionKeeps:
    def test_the_line_is_ruling_twos_words(self):
        """Pinned verbatim: the owner wrote the sentence (Revision 2, ruling
        2), and both editors print what the server sends.

        RED under mutant "the addendum reworded" ("armed" dropped from
        ``COUNTS_DORMANT_ADDENDUM``), observed:

            AssertionError: assert 'Its session ...you CONTINUE.' ==
            'Its armed se...you CONTINUE.'
              - Its armed session keeps its count until you CONTINUE.
              ?    ------
              + Its session keeps its count until you CONTINUE.
        """
        assert app_module.COUNTS_DORMANT_ADDENDUM == (
            "Its armed session keeps its count until you CONTINUE.")

    @pytest.mark.parametrize("status, says", [
        ("dormant", True), ("active", False), ("complete", False),
        ("abandoned", False)])
    def test_only_a_dormant_session_adds_it(self, client, tmp_path, status,
                                            says):
        """The session Run would continue decides: dormant adds the line; a
        session that is running, finished or abandoned keeps nothing a
        CONTINUE would recount, so the note is ruling 2's first sentence
        alone.

        RED under mutant "never added" (the route's addendum condition
        replaced by ``if False:``), for ``dormant``, observed:

            AssertionError: assert [{'key': 'cou...witches it.'}] ==
            [{'key': 'cou...u CONTINUE.'}]
              At index 0 diff: {'key': 'counts', 'note': 'This flow counts
              every sub taken, rejected ones included. New flows count
              accepted subs only, and saving this flow switches it.'} != ...

        RED under mutant "any session" (``latest is not None and
        latest.status == "dormant"`` -> ``latest is not None``), for
        ``active`` and ``complete`` (an abandoned newest is already None),
        observed:

            AssertionError: assert [{'key': 'cou...u CONTINUE.'}] ==
            [{'key': 'cou...witches it.'}]
              At index 0 diff: {'key': 'counts', 'note': 'This flow counts
              every sub taken, rejected ones included. New flows count
              accepted subs only, and saving this flow switches it. Its armed
              session keeps its count until you CONTINUE.'} != ...
        """
        _put_file(tmp_path / "flows", "f1", 3, _graph(target_counts=None))
        _session("f1", status, created=100.0)
        got = client.get("/api/flows/f1").json()["migrated"]
        assert got == (WITH_ADDENDUM if says else [COUNTS])

    def test_the_newest_session_decides_not_any_dormant_one(self, client,
                                                            tmp_path):
        """An older dormant session left by a START OVER is a ledger the
        operator chose to leave (spec 5.9); Run starts fresh when the newest
        is complete, so there is no armed session to keep a count.

        RED under mutant "newest dormant" (the route asks
        ``newest_for_flow(flow_id, ("dormant",))`` instead of
        ``current_for_flow``), observed:

            AssertionError: assert [{'key': 'cou...u CONTINUE.'}] ==
            [{'key': 'cou...witches it.'}]
              At index 0 diff: {'key': 'counts', 'note': 'This flow counts
              every sub taken, rejected ones included. New flows count
              accepted subs only, and saving this flow switches it. Its armed
              session keeps its count until you CONTINUE.'} != ...

        Red too under "any session", with the same lines.
        """
        _put_file(tmp_path / "flows", "f1", 3, _graph(target_counts=None))
        _session("f1", "dormant", created=100.0)
        _session("f1", "complete", created=200.0)
        assert client.get("/api/flows/f1").json()["migrated"] == [COUNTS]

    def test_control_no_counts_note_no_line(self, client, tmp_path):
        """A flow that already counts accepted subs has no note to extend, so
        a dormant session adds nothing: the line is an addendum to ruling
        2's note, never a note of its own. And another flow's dormant
        session is not this flow's. Green on the code and under every
        mutant named in this file."""
        _put_file(tmp_path / "flows", "f1", 3,
                  _graph(target_counts=ACCEPTED_SUBS, pool_counts=ACCEPTED_SUBS))
        _session("f1", "dormant", created=100.0)
        assert client.get("/api/flows/f1").json()["migrated"] == []
        _put_file(tmp_path / "flows", "f2", 3, _graph(target_counts=None))
        assert client.get("/api/flows/f2").json()["migrated"] == [COUNTS]

    def test_the_line_is_never_written_into_the_file(self, client, tmp_path):
        """The read's line travels on the read only: PUT back, the save
        strips ``migrated`` as it always has, and the file holds no note.
        It pins that the addendum rides the read's note, never the record.
        Green on the code; under "never added" it is red at its premise, the
        first assertion, with the lines quoted above."""
        path = _put_file(tmp_path / "flows", "f1", 3,
                         _graph(target_counts=None))
        _session("f1", "dormant", created=100.0)
        body = client.get("/api/flows/f1").json()
        assert body["migrated"] == WITH_ADDENDUM
        assert client.put("/api/flows/f1",
                          json={"flow": body}).status_code == 200
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert "migrated" not in raw["flow"]
