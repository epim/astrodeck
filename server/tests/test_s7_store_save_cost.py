# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A flow save reads the one flow it writes, however many the library holds
(#433; spec 3.3, the store owns the save).

THE FAULT (#433). ``FlowStore.save_and_report`` answered its ``MAX_FLOWS``
check from ``{r.id for r in self._on_disk()}``: a walk of the library that
read, parsed, migrated and validated every stored flow, on every save, to
count them. #433 measured, through the wizard route, 0.08 s a save over the
first 48 into an empty library and 0.75 s a save averaged over 768. Every
door that saves (the editor, the wizard, the quick flow) paid it.
Deterministic, and it grows with the library.

THE RULE. The quota is answered from the directory's file names
(``list_json``, a listing that parses nothing) and from whether the file at
the id is there, which is how ``PlanLibrary.save`` answers its own. The only
file a save reads is the one it writes: ``_newer_schema_on_disk`` and
``_stored``, whose refusals (``NewerSchemaFlow``, ``StoredFlowUnreadable``)
are about that file and still refuse.

What the quota counts moved with it, and each move is a case below: every
file is counted, a file this build cannot read among them (it takes the
disk and the listing's time like any other), and a save over the file at
its own id is an upsert even when that file is unreadable, so a full
library can still have a damaged flow repaired. The old count took only
the readable records' ids, so a full library refused that repair as a new
flow.

THE SPY is ``store.read_json``, through which every read of a stored flow
passes (``_entries``, ``_newer_schema_on_disk``, ``_stored``,
``touch_run``), and ``store._record_of``, which parses one into a record.

Every named mutation was run in a private copy of ``server/`` under the
session scratchpad (``S7-STORE-mut``), from byte copies, never in the shared
tree (#254); the failure each produced is quoted where it went red.
"""
from __future__ import annotations

import json
import sys

import pytest

import astrodeck.flows.store as store_mod
from astrodeck.flows.models import FlowGraph, FlowRecord
from astrodeck.flows.store import (FlowLibraryFull, FlowStore,
                                   NewerSchemaFlow, StoredFlowUnreadable)

#: How many flows the library holds before the save under test. #433's
#: measurement grew one to 768; 200 keeps the setup near a tenth of a
#: second and the count unmistakable.
STORED = 200

#: The flow the saves under test write, stored among the others.
TARGET = "f100"

GRAPH = {"nodes": [
    {"id": "t", "type": "target", "x": 0, "y": 0,
     "params": {"name": "M42", "ra": "05h 35m 17s", "dec": "-05 23 28"}},
    {"id": "c", "type": "capture", "x": 200, "y": 0,
     "params": {"filter": "L", "exposure": 60, "count": 3}}],
    "edges": [{"from": "t", "fromPort": "target", "to": "c",
               "toPort": "run"}]}


def _record(fid: str) -> FlowRecord:
    return FlowRecord(id=fid, name=f"flow {fid}",
                      graph=FlowGraph.model_validate(GRAPH))


@pytest.fixture
def store(tmp_path) -> FlowStore:
    """A library of ``STORED`` flows, ``f000`` to ``f199``: the first written
    by the store's own serialiser (``_write``, the file a save writes), the
    rest its bytes under their own ids, so every one is a readable record
    the old quota walk had to parse. Copied rather than written 200 times
    through ``write_json_atomic``, whose private-file hardening cost 1.6 s
    a library on Windows."""
    s = FlowStore(tmp_path / "flows")
    s._write(_record("f000"))
    text = (s.dir / "f000.json").read_text(encoding="utf-8")
    assert text.count("f000") == 3, "premise: the id, the flow's id, its name"
    for i in range(1, STORED):
        fid = f"f{i:03d}"
        (s.dir / f"{fid}.json").write_text(text.replace("f000", fid),
                                           encoding="utf-8")
    return s


def _spy(monkeypatch, *, fail_in: str | None = None) -> list:
    """Record the file name of every ``read_json`` the store makes, and every
    ``_record_of`` parse as ``"<parse>"``. With ``fail_in``, a read made by
    that caller of the target's file raises ``PermissionError`` (a sharing
    violation), as ``test_persist_flow_bookkeeping.py`` injects one."""
    seen: list = []
    real_read, real_record = store_mod.read_json, store_mod._record_of

    def read(path):
        seen.append(path.name)
        if (fail_in is not None and path.name == f"{TARGET}.json"
                and sys._getframe(1).f_code.co_name == fail_in):
            raise PermissionError(13, "sharing violation")
        return real_read(path)

    def record(raw, **kw):
        seen.append("<parse>")
        return real_record(raw, **kw)

    monkeypatch.setattr(store_mod, "read_json", read)
    monkeypatch.setattr(store_mod, "_record_of", record)
    return seen


def _others(seen: list, name: str) -> list:
    return [n for n in seen if n not in (name, "<parse>")]


class TestASaveReadsOneFlow:
    def test_an_update_parses_no_other_flow(self, store, monkeypatch):
        """An update of one flow among ``STORED`` reads that flow's file and
        no other, and parses one record: the one it replaces.

        RED under mutant "existing built from _on_disk()" (the quota
        answered from ``{r.id for r in self._on_disk()}`` again, with
        ``_on_disk`` put back), observed:

            E   AssertionError: a save parsed 199 other stored flows
            E   assert ['f000.json',...05.json', ...] == []
            E     Left contains 199 more items, first extra item: 'f000.json'

        The same mutant turns this file's three cases that count reads red
        (199, 200, 199), both unreadable-file cases, and
        ``test_persist_flow_bookkeeping.py``'s walk count.
        """
        seen = _spy(monkeypatch)
        store.save(_record(TARGET))
        others = _others(seen, f"{TARGET}.json")
        assert others == [], (
            f"a save parsed {len(others)} other stored flows")
        assert seen.count("<parse>") == 1, seen.count("<parse>")

    def test_a_new_flow_parses_no_stored_flow(self, store, monkeypatch):
        """A new flow into the same library reads nothing but its own
        (absent) file, and parses nothing.

        RED under mutant "existing built from _on_disk()", observed:

            E   AssertionError: a save parsed 200 other stored flows
            E   assert ['f000.json',...05.json', ...] == []
            E     Left contains 200 more items, first extra item: 'f000.json'
        """
        seen = _spy(monkeypatch)
        store.save(_record("fresh"))
        others = _others(seen, "fresh.json")
        assert others == [], (
            f"a save parsed {len(others)} other stored flows")
        assert seen.count("<parse>") == 0, seen.count("<parse>")
        assert (store.dir / "fresh.json").is_file()


class TestTheRefusalsOnTheWrittenIdStillRefuse:
    def test_control_a_newer_builds_file_still_refuses(self, store,
                                                       monkeypatch):
        """Control: the file at the id was written by a newer build (schema
        99). The save refuses with ``NewerSchemaFlow``, the file is byte for
        byte what it was, and still no other flow was read.

        No #433 mutant turns it red: the refusal comes before the quota. It
        can fail: under "newer files overwritten" (``save_and_report``'s
        ``newer`` made None and ``_stored``'s ``FutureFlowSchema`` arm
        answering None), observed:

            E   Failed: DID NOT RAISE <class 'astrodeck.flows.store.NewerSchemaFlow'>
        """
        path = store.dir / f"{TARGET}.json"
        path.write_text(json.dumps({"schema_version": 99, "id": TARGET,
                                    "flow": {"id": TARGET,
                                             "name": "future"}}),
                        encoding="utf-8")
        before = path.read_bytes()
        seen = _spy(monkeypatch)
        with pytest.raises(NewerSchemaFlow):
            store.save(_record(TARGET))
        assert path.read_bytes() == before
        assert _others(seen, path.name) == []

    def test_control_a_read_that_fails_still_refuses(self, store,
                                                     monkeypatch):
        """Control: the save's own read of the file it replaces fails (a
        sharing violation). The save refuses with ``StoredFlowUnreadable``
        and writes nothing, and still no other flow was read.

        Its refusal held under every #433 mutant; its read count went red
        under "existing built from _on_disk()", whose walk reads before the
        refusal (``Left contains 199 more items``). The refusal can fail:
        under "a failed read is no flow" (``_stored``'s ``except OSError``
        answering None), observed:

            E   Failed: DID NOT RAISE <class 'astrodeck.flows.store.StoredFlowUnreadable'>
        """
        path = store.dir / f"{TARGET}.json"
        before = path.read_bytes()
        seen = _spy(monkeypatch, fail_in="_stored")
        with pytest.raises(StoredFlowUnreadable):
            store.save(_record(TARGET))
        assert path.read_bytes() == before
        assert _others(seen, path.name) == []


class TestTheQuotaFromTheFileNames:
    def test_a_full_library_refuses_a_new_flow_and_takes_an_update(
            self, store, monkeypatch):
        """At ``MAX_FLOWS`` files a new id is refused and an update of a
        stored one is written.

        RED under mutant "the quota counts nothing" (the check removed),
        observed:

            E   Failed: DID NOT RAISE <class 'astrodeck.flows.store.FlowLibraryFull'>

        and under "an update refused when full" (the check made
        ``len(list_json(self.dir)) >= MAX_FLOWS`` alone), observed:

            E   astrodeck.flows.store.FlowLibraryFull: the flow library is full (200)
        """
        monkeypatch.setattr(store_mod, "MAX_FLOWS", STORED)
        with pytest.raises(FlowLibraryFull):
            store.save(_record("fresh"))
        assert not (store.dir / "fresh.json").exists()
        store.save(_record(TARGET).model_copy(update={"name": "renamed"}))
        stored = json.loads((store.dir / f"{TARGET}.json")
                            .read_text(encoding="utf-8"))
        assert stored["flow"]["name"] == "renamed"

    def test_an_unreadable_file_counts_toward_the_quota(self, store,
                                                        monkeypatch):
        """A file this build cannot read is a file: at ``MAX_FLOWS`` files,
        one of them unreadable, a new flow is refused. The old count took
        only the readable records, so it let one more past.

        RED under mutant "existing built from _on_disk()", observed:

            E   Failed: DID NOT RAISE <class 'astrodeck.flows.store.FlowLibraryFull'>

        and the same line under "the quota counts nothing".
        """
        (store.dir / f"{TARGET}.json").write_text("{not json",
                                                  encoding="utf-8")
        monkeypatch.setattr(store_mod, "MAX_FLOWS", STORED)
        with pytest.raises(FlowLibraryFull):
            store.save(_record("fresh"))
        assert not (store.dir / "fresh.json").exists()

    def test_a_full_library_can_repair_an_unreadable_file(self, store,
                                                          monkeypatch):
        """A save over an unreadable file at its own id is an upsert, so a
        library whose readable flows alone fill the quota can still have the
        damaged one repaired (the store's rule: saving over such a file is
        how it gets repaired). The old count read the repair as a new flow,
        since the file held no record, and refused it.

        RED under mutant "existing built from _on_disk()", observed:

            E   astrodeck.flows.store.FlowLibraryFull: the flow library is full (199)

        and the same line under "an update refused when full".
        """
        path = store.dir / f"{TARGET}.json"
        path.write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(store_mod, "MAX_FLOWS", STORED - 1)
        repaired = store.save(_record(TARGET))
        assert repaired.id == TARGET
        assert json.loads(path.read_text(encoding="utf-8"))["id"] == TARGET
