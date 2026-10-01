"""A save that cannot read the flow it replaces refuses, rather than
anchoring as if there were none (#350; #353 item 5; spec 3.3, 3.6).

THE FAULT (#350). ``FlowStore._stored`` reads the file a save replaces, so
the save rules can take each TARGET's ``frameAnchor`` from it. It swallowed
every exception and answered None, the answer for a NEW flow, so a TRANSIENT
failure to read it (a Windows sharing violation from antivirus or an
indexer, an interrupted read) anchored every TARGET at its CURRENT geometry,
with no ``reanchored`` row. A block nudged under the carry threshold since
its anchor was set re-keyed, and its banked frames stopped counting, in
silence. One instance of #153's family: an error path that returns the
"nothing there" answer. Intermittent by nature, so every case here INJECTS
the failure.

THE RULE. ``_stored`` answers None for a missing file (``FileNotFoundError``)
and for a record that carries another id (a copied file, #353 item 5). A file
that reads but does not parse or validate keyed nothing (the library lists it
as an unreadable row and nothing can run it), so it answers None too, and
saving over it is how it is repaired. Any other failure to read raises
``StoredFlowUnreadable``, a ``FlowLibraryFull`` as ``NewerSchemaFlow`` is, so
``_persist_flow``'s existing arm answers 409 with the sentence and its code;
nothing is written. A file a newer build wrote refuses as ``NewerSchemaFlow``
here too: ``_newer_schema_on_disk`` checks first but reads OSError as "not
newer", so a check whose read failed must not be the only guard.

THE INJECTION aims at ``_stored``'s own read, by its caller's name. Until
#433 (S7) the save's quota scan read the same file first and made a failure
a library row, so failing the first read of the file would have tested it,
not this; since #433 the quota counts file names and reads no record, and
the injection still names the read it means. (``_persist_flow`` read it first
too, through ``flow_store.get``, until #364 took the save's bookkeeping from
``_stored``'s record: ``test_persist_flow_bookkeeping.py``.)

#363, VERIFIED IN S5 (S5-ROUTES). The deep-file case below was run again
under its two recorded mutants in a private copy (``S5-ROUTES-mut``), and
each still turns it red with the error its docstring quotes.

THE GEOMETRY is test_flows_save_rules.py's 3x2 (threshold 9.975'), nudged
6' north, which carries: the anchor stays where the counts started while the
block is drawn 6' away, which is exactly the state a silent re-anchor
destroys.

Every named mutation was run in a private copy of ``server/`` under the
session scratchpad (``s4-save-mut``), from byte copies of ``store.py``, never
in the shared tree (#254); the failure each produced is quoted.
"""
from __future__ import annotations

import json
import shutil
import sys

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.config as config_mod
import astrodeck.hub as hub_mod
from astrodeck.config import ConfigStore
from astrodeck.flows import store as store_mod
from astrodeck.flows.models import FlowGraph, FlowNode, FlowRecord
from astrodeck.flows.save_rules import current_anchor
from astrodeck.flows.store import (FlowLibraryFull, FlowStore,
                                   NewerSchemaFlow, StoredFlowUnreadable)
from astrodeck.persist import PrivatePermissionsError

RA = "00h 42m 44s"
G0, G6 = "+41 00 00", "+41 06 00"


def _params(dec: str) -> dict:
    return {"name": "M31 3x2", "ra": RA, "dec": dec, "rotation": 30,
            "angle": "Rotate to PA", "rows": 2, "cols": 3, "overlap": 25,
            "fovX": 2.0, "fovY": 1.33, "counts": "Accepted subs"}


def _record(dec: str, fid: str = "f1") -> FlowRecord:
    return FlowRecord(id=fid, name="stored", graph=FlowGraph(nodes=[
        FlowNode(id="t", type="target", params=_params(dec))]))


def _geometry(dec: str) -> str:
    node = FlowNode(id="t", type="target", params=_params(dec))
    return current_anchor(node.with_defaults().params)[0]


def _named(anchor: str) -> str:
    """Which geometry an anchor is, by name, so a failure reads as the rule
    it broke rather than as two anchor texts."""
    return {_geometry(G0): "where the counts started",
            _geometry(G6): "the nudged geometry"}.get(anchor, anchor)


def _fail_once(monkeypatch, exc: BaseException, caller: str = "_stored"):
    """Make ``read_json`` raise ``exc`` the first time ``caller`` calls it,
    and read as usual otherwise. Returns the list of paths it failed."""
    real = store_mod.read_json
    fired: list = []

    def fake(path):
        if not fired and sys._getframe(1).f_code.co_name == caller:
            fired.append(path)
            raise exc
        return real(path)

    monkeypatch.setattr(store_mod, "read_json", fake)
    return fired


@pytest.fixture
def store(tmp_path):
    return FlowStore(tmp_path / "flows")


@pytest.fixture
def nudged(store):
    """f1 saved where its counts start, then nudged 6' (carried): the file
    holds the start as its anchor and the block drawn 6' away."""
    store.save(_record(G0))
    _saved, _migrated, rows = store.save_and_report(_record(G6))
    assert rows == [], "premise: 6' carries"
    path = store.dir / "f1.json"
    assert _named(_anchor_on_file(path)) == "where the counts started"
    return path


def _anchor_on_file(path) -> str:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return next(n["params"]["frameAnchor"]
                for n in raw["flow"]["graph"]["nodes"] if n["id"] == "t")


# ============================================================== the fault

class TestAReadErrorRefusesTheSave:
    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        cfg = ConfigStore(path=tmp_path / "astrodeck.json")
        monkeypatch.setattr(config_mod, "config_store", cfg)
        monkeypatch.setattr(hub_mod, "config_store", cfg)
        monkeypatch.setattr(app_module, "config_store", cfg)
        monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
        monkeypatch.setattr(app_module, "flow_store",
                            FlowStore(tmp_path / "flows"))
        with TestClient(app_module.create_app()) as c:
            yield c

    def test_a_permission_error_is_a_409_and_nothing_moves(
            self, client, tmp_path, monkeypatch):
        """PUT the nudged block again while ``_stored``'s read raises
        ``PermissionError`` once: 409 ``stored_unreadable`` with a sentence,
        the file byte-identical, the anchor where the counts started. The
        retry, with the read working, saves and keeps the anchor (control).

        RED under mutant "swallow every read error" (``_stored``'s handlers
        replaced by ``except Exception: return None``, as before the fix):
        the save succeeded (200), listed nothing ([]) and moved the anchor
        to the nudged geometry, as pytest shortened it:

            E   AssertionError: assert (200, [], 'th...ged geometry') == (409, None, '...unts started')
            E     At index 0 diff: 200 != 409

        RED under mutant "the refusal repeats the error's text"
        (``_unreadable`` writes ``({type(e).__name__}: {e})``), the file's
        path in an answer any operator reads (#369, found by S4-SAVE's
        verifier: the injected error carried no file name at first, so this
        check passed under that mutant; the head of the path elided):

            E   AssertionError: a path in the answer
            E   assert '191cd6907e9...4e082fd.json' not in 'the flow on...o save again'
            E     '191cd6907e914168ae19fec3a4e082fd.json' is contained here:
            E       ...\\flows\\191cd6907e914168ae19fec3a4e082fd.json'), and a save measures each TARGET's framing against it to keep its counts; nothing was written, so save again
        """
        body = {"flow": {"name": "stored", "graph": {"nodes": [
            {"id": "t", "type": "target", "x": 0, "y": 0,
             "params": _params(G0)}]}}}
        fid = client.post("/api/flows", json=body)
        assert fid.status_code == 200, fid.text
        fid = fid.json()["id"]
        body["flow"]["graph"]["nodes"][0]["params"] = _params(G6)
        nudge = client.put(f"/api/flows/{fid}", json=body)
        assert nudge.status_code == 200 and nudge.json()["reanchored"] == []
        path = tmp_path / "flows" / f"{fid}.json"
        before = path.read_bytes()

        # Carrying the file name, as the OSError a real open raises does: its
        # text is then "[Errno 13] ...: '<the full path>'", which is what the
        # answer must not repeat. Without a file name the check below could
        # not fail. The path is compared by its file name, because the text
        # spells the path as its repr, backslashes doubled on Windows.
        fired = _fail_once(monkeypatch, PermissionError(
            13, "sharing violation", str(path)))
        r = client.put(f"/api/flows/{fid}", json=body)
        assert fired, "premise: the injected read error fired"
        observed = (r.status_code,
                    r.json().get("reanchored") if r.status_code == 200
                    else None,
                    _named(_anchor_on_file(path)))
        assert observed == (409, None, "where the counts started")
        detail = r.json()["detail"]
        assert detail["code"] == "stored_unreadable"
        assert "could not be read" in detail["detail"]
        assert "PermissionError" in detail["detail"]
        assert path.name not in detail["detail"], "a path in the answer"
        assert path.read_bytes() == before, "the refused save wrote"

        retry = client.put(f"/api/flows/{fid}", json=body)
        assert retry.status_code == 200, retry.text
        assert retry.json()["reanchored"] == []
        assert _named(_anchor_on_file(path)) == "where the counts started"


class TestWhatStoredAnswers:
    def test_the_refusal_is_a_flow_library_collision(self, store, nudged,
                                                     monkeypatch):
        """In the store: ``StoredFlowUnreadable``, a ``FlowLibraryFull`` (so
        ``_persist_flow``'s arm answers 409 without app.py knowing it), with
        its own code, and the file untouched.

        RED under mutant "swallow every read error":

            E   Failed: DID NOT RAISE <class 'astrodeck.flows.store.StoredFlowUnreadable'>
        """
        before = nudged.read_bytes()
        _fail_once(monkeypatch, PermissionError(13, "sharing violation"))
        with pytest.raises(StoredFlowUnreadable) as info:
            store.save(_record(G6))
        assert isinstance(info.value, FlowLibraryFull)
        assert info.value.code == "stored_unreadable"
        assert nudged.read_bytes() == before

    def test_a_failure_to_secure_the_file_refuses_too(self, store, nudged,
                                                      monkeypatch):
        """``read_json`` hardens the file's permissions before it reads, and a
        failure there is ``PrivatePermissionsError``, a RuntimeError, not an
        OSError. It says nothing about what the file holds, so it refuses
        as an OSError does.

        RED under mutant "only an OSError refuses" (``_stored``'s final
        ``except Exception`` arm removed), the error escaping the store,
        which the route would answer 500:

            E   astrodeck.persist.PrivatePermissionsError: cannot secure private file

        and under "swallow every read error" (the save went ahead):

            E   Failed: DID NOT RAISE <class 'astrodeck.flows.store.StoredFlowUnreadable'>
        """
        before = nudged.read_bytes()
        _fail_once(monkeypatch, PrivatePermissionsError(
            "cannot secure private file"))
        with pytest.raises(StoredFlowUnreadable):
            store.save(_record(G6))
        assert nudged.read_bytes() == before

    @pytest.mark.parametrize("content", [
        pytest.param(b"{not json", id="not JSON"),
        pytest.param(b"\xff\xfe\x00garbage", id="not UTF-8"),
        # Nested past the parser's depth: ``json.loads`` raises
        # RecursionError, not a ValueError, and the file still keyed nothing.
        pytest.param(b"[" * 200000, id="nested past the parser's depth"),
        pytest.param(json.dumps({"schema_version": 4, "id": "f1",
                                 "flow": {"graph": "nope"}}).encode(),
                     id="not a flow record"),
    ])
    def test_a_file_that_reads_but_is_not_a_flow_keyed_nothing(
            self, store, content):
        """Control: a damaged file is an unreadable row, which nothing can
        run, so it keyed nothing, and the save over it is its repair: the
        block is anchored where it is drawn, and nothing is listed.

        RED under mutant "every read error refuses" (``_stored``'s
        parse arm raises as the OSError arm does), the repair refused on
        [not JSON], [not UTF-8] and [nested past the parser's depth], for
        example:

            E   astrodeck.flows.store.StoredFlowUnreadable: the flow on disk could not be read (JSONDecodeError), and a save measures each TARGET's framing against it to keep its counts; nothing was written, so save again

        RED under mutant "a deep file refuses" (``RecursionError`` taken out
        of ``_stored``'s parse arm, so it falls to the refusing one) on
        [nested past the parser's depth]:

            E   astrodeck.flows.store.StoredFlowUnreadable: the flow on disk could not be read (RecursionError), and a save measures each TARGET's framing against it to keep its counts; nothing was written, so save again

        RED under mutant "the newer-schema check chokes on a deep file"
        (``_newer_schema_on_disk`` catching OSError and ValueError only, as
        it did before S4-SAVE found this row escaping every save as a 500,
        #363):

            E   RecursionError: maximum recursion depth exceeded while decoding a JSON array from a unicode string

        RED under mutant "a row refuses" (the ``except Exception: return
        None`` after ``_record_of`` raises instead) on [not a flow record]:

            E   astrodeck.flows.store.StoredFlowUnreadable: the flow on disk could not be read (ValidationError), and a save measures each TARGET's framing against it to keep its counts; nothing was written, so save again
        """
        store.dir.mkdir(parents=True)
        (store.dir / "f1.json").write_bytes(content)
        _saved, _migrated, rows = store.save_and_report(_record(G6))
        assert rows == []
        assert _named(_anchor_on_file(store.dir / "f1.json")) == \
            "the nudged geometry"

    def test_a_missing_file_is_a_new_flow(self, store):
        """Control: no file at all is the one answer that was always
        right."""
        _saved, _migrated, rows = store.save_and_report(_record(G6))
        assert rows == []
        assert _named(_anchor_on_file(store.dir / "f1.json")) == \
            "the nudged geometry"

    def test_a_newer_file_the_first_check_missed_is_still_refused(
            self, store, monkeypatch):
        """``_newer_schema_on_disk`` reads the file first and reads an
        ``OSError`` as "not newer". If that read fails and ``_stored``'s
        succeeds, the file a newer build wrote reaches ``_stored``: it
        refuses as ``NewerSchemaFlow``, and the file is not overwritten.

        RED under mutant "a future file lends nothing" (``_stored`` reads
        ``FutureFlowSchema`` as a file that does not parse, None), the newer
        build's file silently downgraded:

            E   Failed: DID NOT RAISE <class 'astrodeck.flows.store.NewerSchemaFlow'>

        (red under "swallow every read error" too, the same line.)
        """
        store.dir.mkdir(parents=True)
        path = store.dir / "f1.json"
        path.write_text(json.dumps({"schema_version": 99, "id": "f1",
                                    "flow": {"id": "f1", "name": "future"}}),
                        encoding="utf-8")
        before = path.read_bytes()
        fired = _fail_once(monkeypatch, PermissionError(13, "busy"),
                           caller="_newer_schema_on_disk")
        with pytest.raises(NewerSchemaFlow):
            store.save(_record(G6))
        assert fired, "premise: the first check's read failed"
        assert path.read_bytes() == before


class TestACopiedFileLendsNoAnchor:
    def test_a_record_with_another_id_is_not_this_flows(self, store):
        """``b.json`` is a copy of ``a.json``, so the record inside it still
        says ``a``: its nodes are another flow's and lend flow ``b`` no
        anchor (#353 item 5). Saved 6' from where A's counts started, B is
        anchored where it is drawn, and nothing is listed; A is untouched.

        RED under mutant "return record" (``_stored`` without its id check),
        B inherited A's anchor and with it A's campaign:

            E   AssertionError: assert 'where the counts started' == 'the nudged geometry'
            E     - the nudged geometry
            E     + where the counts started
        """
        store.save(_record(G0, fid="a"))
        a = store.dir / "a.json"
        a_before = a.read_bytes()
        shutil.copyfile(a, store.dir / "b.json")
        _saved, _migrated, rows = store.save_and_report(_record(G6, fid="b"))
        assert _named(_anchor_on_file(store.dir / "b.json")) == \
            "the nudged geometry"
        assert rows == []
        assert a.read_bytes() == a_before
