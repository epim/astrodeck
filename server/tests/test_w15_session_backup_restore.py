# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A kept session backup can be listed, restored and removed through the API
(#280, the RESTORE half of #266).

#266 made ``DELETE /api/sessions/{id}`` on an unreadable file keep
``<id>.json.bak`` and the thumbnails, because that backup (the copy
``SessionStore.backup`` takes before an ADOPT rewrites the ledger) can be the
last good record of which frames were accepted for which step. After that
answer nothing in AstroDeck could see the backup, restore it or remove it:
``GET /api/sessions`` read ``*.json`` only, no route brought it back, and the
DELETE of a file that was no longer there answered 404 (``load`` raised
``KeyError``). Recovery took a shell on the rig and a rename.

Now (backlog rulings, 2026-09-30 wave 15):

* ``POST /api/sessions/{id}/restore`` replaces a MISSING or UNREADABLE live
  file with its backup, and never a readable one. The backup is judged by the
  one judgment every reader makes (``_parsed`` and ``_session_from_file``),
  and must hold THIS session's id. The restored session is never armed
  (``auto_resume`` False, ``active`` becomes ``dormant``), and the ``.bak``
  stays where it is.
* ``GET /api/sessions`` lists a ``.bak`` with no ``.json`` beside it as a row
  of its own: ``status: "unreadable"`` (so every reader that hides an
  unreadable row hides it), ``backup: true`` and ``orphan: true``.
* ``DELETE`` of such a row removes the ``.bak`` and the thumbnails, so the
  two-step clear of a file whose backup is damaged too (DELETE the
  ``.json``, then DELETE the orphan row) needs no shell.

Each mutant was run from a byte-for-byte backup of the file it changes, the
file restored byte-identical (sha256 compared) afterwards, and the mutant
text grepped for gone. Failures are quoted verbatim (``--tb=short``), wrapped
to fit, ``[...]`` eliding.
"""
from __future__ import annotations

import json
import os

import pytest

import astrodeck.api.app as app_module
import astrodeck.sequence.session as session_mod
from astrodeck.auth import set_active_provider
from astrodeck.sequence.session import Session, SessionFrame, session_store
from test_unreadable_delete_keeps_backup import (
    BAK_NAME, _backup_beside, _sha, _thumb, _tree)
from test_unreadable_sessions_listed_and_deletable import (  # noqa: F401
    KINDS, MTIME, SID, _EngineRunning, _plan, _readable, _reset_provider_after,
    _Viewer, _write, client, store_only)

#: The backup file's mtime, so the orphan row's ``updated_ts`` is a number
#: the test chose rather than a clock.
BAK_MTIME = MTIME + 500
ORPHAN_REASON = "the session file is gone; only its backup remains"


def _restore(client, sid: str = SID):
    return client.post(f"/api/sessions/{sid}/restore")


def _bak_bytes(**fields) -> bytes:
    """A backup ``SessionStore`` can read: the session ``SID``, one accepted
    frame on its step, with ``fields`` overriding the session's own."""
    s = Session(**{"id": SID, "name": "before the adopt", "created_ts": 1.0,
                   "status": "dormant", "plan": _plan(), **fields})
    t = s.plan.targets[0]
    s.frames.append(SessionFrame(ts=1.0, night="n1", target_id=t.id,
                                 step_id=t.steps[0].id, auto_accepted=True))
    return json.dumps(s.model_dump()).encode("utf-8")


def _orphan(data: bytes, mtime: int = BAK_MTIME):
    """``<SID>.json.bak`` with NO ``<SID>.json`` beside it."""
    session_mod._sessions_dir().mkdir(parents=True, exist_ok=True)
    bak = _backup_beside(SID, data)
    os.utime(bak, ns=(mtime * 10**9, mtime * 10**9))
    return bak


def _live():
    return session_store._path(SID)


# ============================================================== the restore

@pytest.mark.parametrize("kind", KINDS)
def test_a_damaged_file_is_replaced_by_its_readable_backup(client, kind):
    """Each kind of unreadable ``<id>.json`` (not JSON, fails validation,
    states no status) is replaced by a readable ``.bak``: the answer is 200,
    ``GET /api/sessions/{id}`` now loads the BACKUP's session (its frame, its
    name), the status is dormant and ``auto_resume`` False, the damaged bytes
    are gone, the ``.bak`` is byte for byte where it was, and nothing else in
    the sessions directory (the thumbnails) changed.

    RED before the route existed, every kind, observed:

        [not_json] E   AssertionError: {"detail":"session not found"}
        E   assert 404 == 200

    The mutants that matter to a readable backup are the next tests': the
    damaged-backup test's "restore copies the bytes without
    _session_from_file", and the armed and active tests' two.
    """
    path = _write(kind)
    bak = _backup_beside(SID, _bak_bytes())
    _thumb(SID)
    bak_sha = _sha(bak)
    before = _tree()

    r = _restore(client)

    assert r.status_code == 200, r.text
    got = client.get(f"/api/sessions/{SID}")
    assert got.status_code == 200, got.text
    s = got.json()
    assert (s["id"], s["name"], s["status"], s["auto_resume"]) == (
        SID, "before the adopt", "dormant", False)
    assert len(s["frames"]) == 1
    assert _sha(bak) == bak_sha
    want = dict(before)
    want[f"{SID}.json"] = _sha(path)
    assert _tree() == want, "the restore touched something besides <id>.json"
    assert want[f"{SID}.json"] != before[f"{SID}.json"], (
        "premise: the damaged bytes are still there")
    body = r.json()
    assert (body["restored"], body["accepted"]) == (SID, 1)
    assert body["backup_ts"] == os.stat(bak).st_mtime
    detail = body["detail"]
    assert f"{SID}.json" in detail and "backup" in detail, detail
    assert "FITS" in detail and "auto-resume is off" in detail, detail
    assert "The session is dormant and auto-resume is off." in detail, detail
    assert "ledger" not in detail.lower(), detail


def test_a_missing_file_is_restored_from_its_backup(client):
    """The orphan case: no ``<id>.json`` at all. The backup becomes the
    session, and the orphan row is gone from the list.

    RED before the route existed: ``assert 404 == 200`` (the 404 said
    ``session not found``).
    """
    _orphan(_bak_bytes())
    assert [r["id"] for r in client.get("/api/sessions").json()["sessions"]
            ] == [SID]

    r = _restore(client)

    assert r.status_code == 200, r.text
    rows = client.get("/api/sessions").json()["sessions"]
    assert [(x["id"], x["status"]) for x in rows] == [(SID, "dormant")]
    assert "orphan" not in rows[0] and rows[0]["accepted"] == 1


@pytest.mark.parametrize("kind", KINDS)
def test_a_damaged_backup_never_replaces_the_file(client, kind):
    """A backup the store cannot read answers 422 naming the store's own
    reason (never pydantic's), with the code ``backup_unreadable``, and BOTH
    files are sha256-identical afterwards: the damaged live file is not
    swapped for another damaged one, and the backup is not touched.

    RED under mutant "restore copies the bytes without _session_from_file"
    (``restore_backup``'s ``session = _session_from_file(raw, session_id)``
    made ``session = Session.model_construct(**raw)``: no judgment, the
    fields taken as they stand), ``[invalid]`` and ``[no_status]``
    (``[not_json]`` stays green: ``_parsed`` refuses it first), observed.
    The unjudged session is saved over the damaged file, and the route then
    dies on its unjudged frames, so the failure is the route's crash and not
    the status line:

        E   AttributeError: 'dict' object has no attribute 'effective'

    RED under mutant "restore judges with pydantic alone" (the same line made
    ``session = Session.model_validate({**raw, 'status': raw.get('status') or
    'dormant'})``: the no-status rule of ``_session_from_file`` skipped),
    ``[no_status]``, observed:

        E   AssertionError: {"restored":"0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa","accepted":1,
        "backup_ts":1791380989.2646883,"detail":"Restored [...] auto-resume is off."}
        E   assert 200 == 422

    and ``[invalid]`` raises pydantic's ``ValidationError`` out of the route
    (``Input should be a valid dictionary or instance of SequencePlan``):
    the words the store never uses.
    """
    live = _write("not_json")
    bak = _backup_beside(SID, KINDS[kind][0])
    live_sha, bak_sha = _sha(live), _sha(bak)

    r = _restore(client)

    assert r.status_code == 422, r.text
    d = r.json()["detail"]
    assert d["code"] == "backup_unreadable"
    assert d["detail"] == f"the backup cannot be read: {KINDS[kind][1]}"
    assert (_sha(live), _sha(bak)) == (live_sha, bak_sha)


def test_the_live_file_is_never_overwritten_when_it_can_be_read(client):
    """RESTORE IS FOR A MISSING OR UNREADABLE FILE, NEVER AN UNDO. A readable
    ``<id>.json`` beside a good ``.bak`` is the current ledger (frames the
    backup does not hold): 409, ``live_session_readable``, both files
    untouched.

    RED under mutant "live-readable guard removed" (the ``raise
    LiveSessionReadable(session_id)`` of ``restore_backup`` made ``pass``),
    observed:

        E   AssertionError: {"restored":"c118d7d73d0a42e583f2d7c892243def","accepted":1,
        "backup_ts":1791380900.4074519,"detail":"Restored [...] auto-resume is off."}
        E   assert 200 == 409

    The good ledger was replaced by the backup's copy.
    """
    s = _readable("dormant")
    live = session_store._path(s.id)
    bak = session_store.backup(s.id)
    live_sha, bak_sha = _sha(live), _sha(bak)

    r = _restore(client, s.id)

    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "live_session_readable"
    assert (_sha(live), _sha(bak)) == (live_sha, bak_sha)


def test_a_live_file_the_os_will_not_open_is_not_called_missing(
        client, monkeypatch):
    """A live file the OS will not open (an antivirus scan, a replace in
    flight) is neither damaged nor missing: ``load`` calls it missing, and a
    restore that took that word would put the backup over a ledger that was
    only locked for a moment. The ``OSError`` reaches the caller, and the file
    is not replaced.

    RED under mutant "a locked live file judged missing" (``restore_backup``'s
    ``except (FileNotFoundError, SessionUnreadable):`` made ``except (OSError,
    SessionUnreadable):``, which is how ``load`` reads a file it cannot open:
    as one that is not there), observed:

        E   Failed: DID NOT RAISE <class 'PermissionError'>

    The backup had replaced the locked file and the route answered 200.
    """
    live = _write("not_json")
    _backup_beside(SID, _bak_bytes())
    live_sha = _sha(live)
    real = session_mod.read_json

    def held_open(p):
        if os.path.basename(str(p)) == live.name:
            raise PermissionError(13, "The process cannot access the file "
                                      "because it is being used by another "
                                      "process", str(p))
        return real(p)

    monkeypatch.setattr(session_mod, "read_json", held_open)

    with pytest.raises(PermissionError):
        _restore(client)

    assert _sha(live) == live_sha, "a locked file was replaced"


def test_a_backup_of_another_session_is_refused(client):
    """A hand-edited backup whose inner id is not the file's stem would make
    ``save`` write a DIFFERENT file (``<inner id>.json``) and leave the one
    asked for missing: 422, ``backup_unreadable``, and no new file anywhere.

    RED under mutant "id check removed" (``restore_backup``'s ``if
    session.id != session_id:`` made ``if False:``), observed:

        E   AssertionError: {"restored":"0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa","accepted":1,
        "backup_ts":1700000500.0,"detail":"Restored [...] auto-resume is off."}
        E   assert 200 == 422

    ``save`` had written ``eeee...json``, a session nobody asked for, and the
    answer said the file asked for was restored.
    """
    other = "e" * 32
    _orphan(_bak_bytes(id=other))
    before = _tree()

    r = _restore(client)

    assert r.status_code == 422, r.text
    d = r.json()["detail"]
    assert d["code"] == "backup_unreadable"
    assert other not in d["detail"], "the other id was echoed"
    assert _tree() == before, "a file was written"
    assert not session_store._path(other).exists()
    assert not _live().exists()


def test_a_restored_session_is_never_armed_and_never_running(client):
    """A backup saved with ``auto_resume`` True and ``active`` (the shape a
    backup taken mid-run has) comes back dormant and disarmed, ON DISK as
    well as in the answer, so it cannot join the auto-resume singleton
    (#595) or read as a run the engine is not making. ``armed()`` and
    ``active()`` find nothing.

    RED under mutant "auto_resume kept" (``session.auto_resume = False`` of
    ``restore_backup`` removed), observed:

        E   AssertionError: assert ('dormant', True) == ('dormant', False)
        E     At index 1 diff: True != False

    RED under mutant "active kept" (``if session.status == "active":`` made
    ``if False:``), observed:

        E   AssertionError: assert ('active', False) == ('dormant', False)
        E     At index 0 diff: 'active' != 'dormant'
    """
    _write("not_json")
    _backup_beside(SID, _bak_bytes(status="active", auto_resume=True))

    r = _restore(client)

    assert r.status_code == 200, r.text
    stored = session_store.load(SID)
    assert (stored.status, stored.auto_resume) == ("dormant", False)
    assert session_store.armed() is None
    assert session_store.active() is None


def test_a_complete_backup_keeps_its_status_and_the_answer_says_so(client):
    """Only ``active`` is demoted: a backup of a complete session comes back
    complete, so the restore does not reopen a finished ledger, and the
    answer names the status the session now has rather than saying dormant by
    rule.

    RED under mutant "dormant by rule" (the answer's ``The session is
    {s.status}`` made ``The session is dormant``), observed:

        E   AssertionError: assert 'The session is complete and auto-resume is off.' in
        'Restored 0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa.json from its backup. [...] The
        session is dormant and auto-resume is off.'
    """
    _write("not_json")
    _backup_beside(SID, _bak_bytes(status="complete"))

    r = _restore(client)

    assert r.status_code == 200, r.text
    assert session_store.load(SID).status == "complete"
    assert "The session is complete and auto-resume is off." in r.json()["detail"]


def test_no_backup_is_a_404_and_the_file_stays(client):
    """No ``.bak`` beside a damaged file: 404 ``no backup``, file untouched.
    A file that was never there, nor anything beside it, is the same answer.

    RED before the route existed: the 404 said ``session not found``, not
    ``no backup``.
    """
    live = _write("invalid")
    live_sha = _sha(live)

    r = _restore(client)

    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "no backup"
    assert _sha(live) == live_sha
    assert _restore(client, "f" * 32).status_code == 404


def test_the_engine_running_that_session_refuses_the_restore(client,
                                                             monkeypatch):
    """While the engine runs THIS session the restore is refused and the
    file stays, because the run's next ledger write puts its own copy back
    over whatever was restored; while it runs another session, the restore
    goes through (the delete route's rule).

    RED under mutant "the engine's word dropped" (``restore_session``'s
    ``_running_it`` made ``return False``, which takes both of its checks),
    observed:

        E   AssertionError: {"restored":"0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa","accepted":1,
        "backup_ts":1791381092.4884205,"detail":"Restored [...] auto-resume is off."}
        E   assert 200 == 409
    """
    live = _write("not_json")
    _backup_beside(SID, _bak_bytes())
    live_sha = _sha(live)
    monkeypatch.setattr(app_module, "engine", _EngineRunning(SID))

    r = _restore(client)

    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "cannot restore a running session"
    assert _sha(live) == live_sha

    monkeypatch.setattr(app_module, "engine", _EngineRunning("d" * 32))
    assert _restore(client).status_code == 200


def test_a_run_that_starts_during_the_drain_still_refuses_the_restore(
        client, monkeypatch):
    """THE CHECK BEFORE THE DRAIN IS A HINT; THE ONE IN THE LOCKED SECTION
    DECIDES (#212, restore-shaped). The drain is an ``await``, and any starter
    (ResumeArm, ``/resume``, Run CONTINUE) can take the session inside it: the
    engine reads as idle at the route's first check and as running THIS
    session by the time the section that writes the file begins. The
    other test's engine runs the session throughout, so the first check
    refuses it and the second is never reached; here only the second can.

    RED under mutant "the locked check dropped" (the ``if _running_it():`` of
    ``restore_session``'s ``with session_store.write_locked():`` section made
    ``if False:``), observed:

        E   AssertionError: {"restored":"0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa","accepted":1,
        "backup_ts":1791386881.6691754,"detail":"Restored [...] auto-resume is off."}
        E   assert 200 == 409

    The backup replaced the file of a session the engine had just started.
    """
    live = _write("not_json")
    _backup_beside(SID, _bak_bytes())
    live_sha = _sha(live)

    class _Engine:
        running = False
        _session = None

        async def drain_thumbs_for_session(self, session_id: str) -> None:
            self.running = True
            self._session = type("S", (), {"id": session_id})()

    monkeypatch.setattr(app_module, "engine", _Engine())

    r = _restore(client)

    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "cannot restore a running session"
    assert _sha(live) == live_sha, "the restore replaced a running session's file"


def test_in_flight_thumbnail_renders_are_drained_before_the_restore(
        client, monkeypatch):
    """A render left from a finished run ends in ``session_store.save`` of the
    engine's in-memory copy, which would put a stale session over the
    restored file (the #212 hazard, restore-shaped). The route cancels them
    first, while the file is still the damaged one.

    RED under mutant "no drain" (the ``await engine.drain_thumbs_for_session``
    line removed from ``restore_session``), observed:

        E   AssertionError: assert [] == [('0242aaaaaa...aaaaa', True)]
        E     Right contains one more item: ('0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa', True)
    """
    live = _write("not_json")
    _backup_beside(SID, _bak_bytes())
    damaged = _sha(live)
    seen: list[tuple[str, bool]] = []

    class _Engine:
        running = False
        _session = None

        async def drain_thumbs_for_session(self, session_id: str) -> None:
            seen.append((session_id, _sha(live) == damaged))

    monkeypatch.setattr(app_module, "engine", _Engine())

    assert _restore(client).status_code == 200
    assert seen == [(SID, True)]


def test_a_viewer_cannot_restore(client):
    """The restore needs ``control.mount``, as the delete does: a viewer
    gets 403 and the file stays.

    RED under mutant "restore gated on view" (``require(CAP_CONTROL_MOUNT)``
    and ``@declare(CAP_CONTROL_MOUNT)`` of the new route made
    ``CAP_VIEW_STATUS``; moving the ``declare`` too, because the app refuses
    to build with a route that declares a capability nothing enforces),
    observed:

        E   AssertionError: {"restored":"0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa","accepted":1,
        "backup_ts":1791381108.7252836,"detail":"Restored [...] auto-resume is off."}
        E   assert 200 == 403
    """
    live = _write("not_json")
    _backup_beside(SID, _bak_bytes())
    live_sha = _sha(live)
    set_active_provider(_Viewer())

    r = _restore(client)

    assert r.status_code == 403, r.text
    assert _sha(live) == live_sha


def test_an_illegal_id_is_not_a_backup(store_only):
    """The store's id guard answers for the restore too: ``KeyError``, which
    the route reads as "no backup"."""
    with pytest.raises(KeyError):
        session_store.restore_backup("..")


# ============================================================== the listing

def test_an_orphan_backup_is_listed_as_its_own_row(client):
    """A ``.bak`` with no ``.json`` beside it is a row: the id is the stem,
    the name the one inside the backup, ``status: "unreadable"`` (so
    ``isUnreadableRow`` and ``listSessions`` hide it from every reader that
    expects a session), the store's sentence, the BACKUP's mtime, and
    ``backup`` and ``orphan`` both true. It sorts in by that mtime.

    RED under mutant "list skips orphans" (``SessionStore.list``'s ``for bak
    in _orphan_backups():`` made ``for bak in []:``), observed:

        E   AssertionError: [{'accepted': 1, 'auto_resume': False, 'created_ts': 1.0,
        'id': '324ed443bb464fea8ef35c15c3af9da5', ...}, {'accepted': 1, [...]}]
        E   assert ['324ed443bb4...a076dbffd34d'] == ['324ed443bb4...a076dbffd34d']
        E     At index 1 diff: 'd714c0e297c64876af1ca076dbffd34d' != '0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        E     Right contains one more item: 'd714c0e297c64876af1ca076dbffd34d'

    and the damaged-orphan test below fails with it (``assert [] ==
    [{'backup': T...': True, ...}]``).
    """
    from test_unreadable_sessions_listed_and_deletable import NEWER, OLDER
    newer = _readable("dormant", NEWER)
    older = _readable("complete", OLDER)
    _orphan(_bak_bytes())

    rows = client.get("/api/sessions").json()["sessions"]

    assert [r["id"] for r in rows] == [newer.id, SID, older.id], rows
    assert rows[1] == {
        "id": SID, "name": "before the adopt", "status": "unreadable",
        "unreadable": ORPHAN_REASON, "updated_ts": float(BAK_MTIME),
        "backup": True, "orphan": True}


def test_a_damaged_orphan_backup_still_has_a_row_named_by_its_id(client):
    """The backup is read to find a name, and a backup that does not parse
    must still be listed (so it can be deleted): the name falls back to the
    id. Whether it would restore is the restore's answer, not the list's.

    RED under mutant "the name read unguarded" (the ``except
    SessionUnreadable: raw = None`` of ``_orphan_row`` removed), observed:

        E   KeyError: 'sessions'

    The unreadable backup's own error escaped ``list``, which the app-wide
    handler answers 500 with no ``sessions`` key: one damaged ``.bak`` cost
    the whole list.
    """
    _orphan(KINDS["not_json"][0])

    rows = client.get("/api/sessions").json()["sessions"]

    assert rows == [{"id": SID, "name": SID, "status": "unreadable",
                     "unreadable": ORPHAN_REASON,
                     "updated_ts": float(BAK_MTIME),
                     "backup": True, "orphan": True}]


def test_a_backup_beside_its_file_is_not_an_orphan(client):
    """CONTROL. A ``.bak`` with a ``.json`` beside it, readable or not, is
    not a row of its own: the unreadable file's row says ``backup: true``
    (#266) and a readable session's backup is not listed at all, so one
    session never appears twice.

    RED under mutant "every backup an orphan" (``_orphan_backups``'s ``if
    bak.is_file() and not live.is_file():`` made ``if bak.is_file():``),
    observed:

        E   AssertionError: assert ['0242aaaaaaa...c4e157673081'] == ['0242aaaaaaa...c4e157673081']
        E     At index 1 diff: '0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa' != '3982a2b23bf541e1a582c4e157673081'
        E     Left contains 2 more items, first extra item: '3982a2b23bf541e1a582c4e157673081'
    """
    s = _readable("dormant")
    session_store.backup(s.id)
    _write("not_json")
    _backup_beside(SID, _bak_bytes())

    rows = client.get("/api/sessions").json()["sessions"]

    assert sorted(r["id"] for r in rows) == sorted([s.id, SID])
    assert [r.get("orphan") for r in rows] == [None, None]


def test_a_session_listed_in_the_walk_is_not_listed_again_as_an_orphan(
        client, monkeypatch):
    """The list is two reads, the walk of the ``.json`` files and the scan for
    orphans, and a DELETE of an unreadable file can land between them: the
    file is gone and its kept backup is an orphan the scan finds, for an id the
    walk already gave a row. One id is one row. Staged by making the scan find
    the backup beside a file the walk listed, which is the state the race
    leaves in one answer.

    RED under mutant "an orphan listed twice" (``list``'s ``if _backup_id(bak)
    not in seen`` made ``if True``), observed:

        E   assert [('0242aaaaaa...aaaaa', None)] == [('0242aaaaaa...aaaaa', None)]
        E     At index 0 diff: ('0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa', True) != ('0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa', None)
        E     Left contains one more item: ('0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa', None)
    """
    _write("not_json")
    bak = _backup_beside(SID, _bak_bytes())
    monkeypatch.setattr(session_mod, "_orphan_backups", lambda: [bak])

    rows = client.get("/api/sessions").json()["sessions"]

    assert [(r["id"], r.get("orphan")) for r in rows] == [(SID, None)], rows


def test_a_backup_that_is_not_a_legal_session_id_is_skipped(client):
    """A ``.bak`` whose stem the store's id guard refuses (a trailing space
    here, which Windows strips and so aliases onto another id) is not
    listed: nothing could address it, and the list must not raise for one
    stray file.

    RED under mutant "illegal stems listed" (``_orphan_backups``'s ``live =
    safe_id_path(directory, _backup_id(bak))`` made ``live = directory /
    f"{_backup_id(bak)}.json"``, so the id is used as it stands), observed:

        E   AssertionError: assert [{'backup': T...': True, ...}] == []
        E     Left contains one more item: {'backup': True, 'id': 'x ', 'name':
        'before the adopt', 'orphan': True, ...}
    """
    session_mod._sessions_dir().mkdir(parents=True, exist_ok=True)
    (session_mod._sessions_dir() / "x .json.bak").write_bytes(_bak_bytes())

    r = client.get("/api/sessions")

    assert r.status_code == 200, r.text
    assert r.json()["sessions"] == []


def test_control_no_scanning_reader_sees_an_orphan(store_only):
    """CONTROL. An orphan is a row of the list and nothing else: the
    readers that count, sweep, arm or start a session never see it, even
    when its backup says ``active`` and armed, and the prune sweep does not
    count it.
    """
    _orphan(_bak_bytes(status="active", auto_resume=True))

    assert session_store.load_all() == []
    assert session_store.active() is None
    assert session_store.armed() is None
    assert session_store.recoverable() is None
    assert session_store.boot_sweep() == 0
    with pytest.raises(KeyError):
        session_store.load(SID)


# ============================================================== the delete

def test_deleting_an_orphan_removes_the_backup_and_the_thumbnails(client):
    """DELETE on an orphan row answers 200 ``{"deleted": id}`` and removes the
    ``.bak`` and the thumbnails directory: there is no ledger left for
    either to belong to, and nothing else lists them. The sessions directory
    is then empty and so is the list.

    RED under mutant "keep_backup left True for an orphan" (the route's
    ``keep_backup=s is None and not orphan`` made the old ``keep_backup=s is
    None``), observed, and with it the two-presses test below:

        E   AssertionError: {"deleted":"0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa","backup_kept":
        "0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa.json.bak","detail":"Removed [...]. Its backup
        [...] remains in the sessions folder, [...]"}
        E   assert {'backup_kept... removes it.'} == {'deleted': '...aaaaaaaaaaaa'}
        E     Left contains 2 more items:

    It answered 200 and left the backup and its row where they were.
    """
    _orphan(_bak_bytes())
    _thumb(SID)
    assert BAK_NAME in _tree()

    r = client.delete(f"/api/sessions/{SID}")

    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": SID}, r.text
    assert _tree() == {}
    assert client.get("/api/sessions").json()["sessions"] == []


def test_deleting_what_is_not_there_is_still_a_404(client):
    """CONTROL. No ``.json`` and no ``.bak``: 404, as always. The orphan rule
    widens what exists, not what a missing id answers.

    RED under mutant "every missing id an orphan" (``has_orphan_backup``'s
    ``return _backup_path(path).is_file() and not path.is_file()`` made
    ``return True``), observed:

        E   AssertionError: {"deleted":"0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
        E   assert 200 == 404
    """
    r = client.delete(f"/api/sessions/{SID}")

    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "session not found"


def test_a_file_and_a_backup_both_damaged_clear_in_two_presses(client):
    """The shape that took a shell: ``.json`` AND ``.bak`` unreadable. The
    restore says 422; DELETE of the row removes the ``.json`` and keeps the
    ``.bak`` (#266); the orphan row that appears then deletes the ``.bak``.
    Two presses and the directory is clean, no shell on the rig.
    """
    _write("invalid")
    _backup_beside(SID, KINDS["not_json"][0])
    _thumb(SID)

    assert _restore(client).status_code == 422

    first = client.delete(f"/api/sessions/{SID}")
    assert first.status_code == 200 and first.json()["backup_kept"] == BAK_NAME
    rows = client.get("/api/sessions").json()["sessions"]
    assert [(r["id"], r["orphan"], r["backup"]) for r in rows] == [
        (SID, True, True)]

    second = client.delete(f"/api/sessions/{SID}")
    assert second.status_code == 200 and second.json() == {"deleted": SID}
    assert _tree() == {}


def test_a_viewer_sees_the_orphan_row_but_cannot_delete_it(client):
    """CONTROL. The row is listed for whoever may read the list, and its
    DELETE is ``control.mount``'s like every other: a viewer gets 403 and
    the backup stays.
    """
    bak = _orphan(_bak_bytes())
    bak_sha = _sha(bak)
    set_active_provider(_Viewer())

    assert [r["id"] for r in
            client.get("/api/sessions").json()["sessions"]] == [SID]
    r = client.delete(f"/api/sessions/{SID}")

    assert r.status_code == 403, r.text
    assert _sha(bak) == bak_sha
