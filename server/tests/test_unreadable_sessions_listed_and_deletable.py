# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A session file the store cannot read is listed, and DELETE removes it (#242).

Three kinds of file count, and the store names each in its own words:

* ``NOT_JSON``: the file does not parse (a write cut short, a hand copy).
  ``load`` used to call it "no such session", so DELETE answered 404 for a
  file on disk (the comment on #242).
* ``INVALID``: JSON that ``Session`` does not validate. DELETE loaded it as
  its first step, and the app-wide handler answered 500.
* ``NO_STATUS``: a file that states no status (#218, H2 orchestrator
  ruling 12). Before H2 it loaded as ``active`` and DELETE answered 409;
  since H2 it was the 500.

Every scanning reader skipped all three without a word, so nothing in the
API could see or remove them: a shell on the rig or a factory reset, both a
human touching the rig. Now ``GET /api/sessions`` lists each as a row with
``status: "unreadable"``, the reason and the file's mtime, and no ledger
field, sorted in with the rest; DELETE removes it inside the write-locked
section it already had (#212), and keeps a ``.bak`` beside it with the
thumbs, since that can be the last good copy of the ledger (#266,
test_unreadable_delete_keeps_backup.py). The readers that must never treat
it as a session still skip it.

Each mutant was run from a byte-for-byte backup of the file it changes and
the file was restored byte-identical (sha256 compared) afterwards. Failures
are quoted verbatim (``--tb=short``), wrapped to fit, ``[...]`` eliding.
"""
from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.hub as hub_module
import astrodeck.sequence.session as session_mod
from astrodeck.auth import (principal_for_role, reset_active_provider,
                            set_active_provider)
from astrodeck.config import ConfigStore
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence.session import (INVALID, NO_STATUS, NOT_JSON,
                                        Session, SessionFrame, session_store)

#: 32 hex like every session id, fixed so the failures quoted name one file.
SID = "0242" + "a" * 28
FLOW = "f" * 32

#: Each kind: the bytes on disk, the reason the store gives, the row's name.
#: Every one carries the fields some reader filters on (a flow origin,
#: ``auto_resume``, a frame, ``active`` where it can state a status), so a
#: reader that stopped skipping would have something to find.
_FIELDS = {"id": SID, "origin": "flow", "origin_id": FLOW,
           "auto_resume": True, "created_ts": 1.0, "updated_ts": 1.0,
           "frames": [{"id": "b" * 32, "step_id": "s1"}]}
KINDS = {
    "not_json": ((json.dumps({**_FIELDS, "status": "active"})[:-40])
                 .encode("utf-8"), NOT_JSON, SID),
    "invalid": (json.dumps({**_FIELDS, "name": "damaged", "status": "active",
                            "plan": 5}).encode("utf-8"), INVALID, "damaged"),
    "no_status": (json.dumps({**_FIELDS, "name": "hand-edited",
                              "extra_key": 1}).encode("utf-8"),
                  NO_STATUS, "hand-edited"),
}

#: The unreadable file's mtime, and two readable sessions either side of it,
#: so "sorted in with the rest" has a place to be wrong.
MTIME = 1_700_000_000
NEWER, OLDER = MTIME + 3600.0, MTIME - 3600.0


@pytest.fixture(autouse=True)
def _reset_provider_after():
    yield
    reset_active_provider()


@pytest.fixture
def client(tmp_path, monkeypatch):
    """test_sessions_api.py's harness: the real app, a throwaway config and
    captures directory, open auth (admin) unless a test sets a provider."""
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_module, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    monkeypatch.setattr(app_module, "configure_provider_from_auth",
                        lambda _auth: app_module.get_active_provider())
    reset_active_provider()
    with TestClient(app_module.create_app()) as c:
        yield c


@pytest.fixture
def store_only(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")


class _Viewer:
    name = "fake"

    async def resolve(self, request):
        return principal_for_role("viewer")


def _write(kind: str):
    """The unreadable file for ``kind``, with its mtime pinned to ``MTIME``."""
    path = session_store._path(SID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(KINDS[kind][0])
    os.utime(path, ns=(MTIME * 10**9, MTIME * 10**9))
    return path


def _fingerprint(path) -> tuple[bytes, int]:
    return path.read_bytes(), os.stat(path).st_mtime_ns


def _plan() -> SequencePlan:
    return SequencePlan(name="p", targets=[Target(
        name="M42", ra_hours=5.5881, dec_deg=-5.3911,
        steps=[ExposureStep(filter="L", exposure_s=60, count=2)])])


def _readable(status: str, updated_ts: float | None = None) -> Session:
    """A readable session. With ``updated_ts`` it is written by hand, so the
    timestamp is the one given and not the save's clock."""
    s = Session(name=f"readable {status}", created_ts=1.0, status=status,
                plan=_plan())
    s.frames.append(SessionFrame(ts=1.0, night="n1",
                                 target_id=s.plan.targets[0].id,
                                 step_id=s.plan.targets[0].steps[0].id))
    if updated_ts is None:
        session_store.save(s)
    else:
        s.updated_ts = updated_ts
        path = session_store._path(s.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(s.model_dump()), encoding="utf-8")
    return s


def _side_files(sid: str):
    """A ``.bak`` and a thumbnail beside session ``sid``'s file."""
    path = session_store._path(sid)
    bak = path.with_suffix(path.suffix + ".bak")
    bak.write_bytes(b"copy")
    thumbs = session_store.thumbs_dir(sid)
    thumbs.mkdir(parents=True)
    (thumbs / ("c" * 32 + ".jpg")).write_bytes(b"jpg")
    return bak, thumbs.parent


# ------------------------------------------------------------------ listed

@pytest.mark.parametrize("kind", KINDS)
def test_each_kind_is_listed_with_its_reason(client, kind):
    """The row is exactly ``{id, name, status: "unreadable", unreadable,
    updated_ts}``: no ledger field (dict equality), the store's reason (never
    pydantic's message), the file's mtime, sorted between the two readable
    sessions either side of it.

    RED under mutant "list skips unreadable files" (``list``'s ``if s is
    None:`` branch reduced to ``continue``), every kind, observed:

        [not_json] E   AssertionError: [{'accepted': 1, 'auto_resume': False,
        'created_ts': 1.0, 'id': '42635766ee434e5eb947ea3b6efcd436', ...},
        {'accepted': 1, 'auto_resume': False, 'created_ts': 1.0, 'id':
        'd05148c4cf364ea19e4fc15df5a7af79', ...}]
        E   assert ['42635766ee4...c15df5a7af79'] == ['42635766ee4...c15df5a7af79']
        E     At index 1 diff: 'd05148c4cf364ea19e4fc15df5a7af79' != '0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        E     Right contains one more item: 'd05148c4cf364ea19e4fc15df5a7af79'

    and the same under ``[invalid]`` and ``[no_status]`` (other ids).

    RED under mutant "not-JSON read as missing" (``_parsed`` raises
    ``FileNotFoundError`` for a ``ValueError``, which is what
    ``read_json_or`` made of it), ``not_json`` alone, observed:

        [not_json] E   At index 1 diff: 'ab0b428fb82743c4ae2a9bbd2636a76f' != '0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        E     Right contains one more item: 'ab0b428fb82743c4ae2a9bbd2636a76f'
    """
    newer = _readable("dormant", NEWER)
    older = _readable("complete", OLDER)
    _write(kind)
    _bytes, reason, name = KINDS[kind]

    rows = client.get("/api/sessions").json()["sessions"]

    assert [r["id"] for r in rows] == [newer.id, SID, older.id], rows
    assert rows[1] == {"id": SID, "name": name, "status": "unreadable",
                       "unreadable": reason, "updated_ts": float(MTIME)}
    assert rows[0]["status"] == "dormant" and rows[0]["accepted"] == 1


# --------------------------------------------------------------- deletable

@pytest.mark.parametrize("kind", KINDS)
def test_each_kind_is_deletable(client, kind):
    """DELETE removes the file, keeps its ``.bak`` and its thumbs directory
    (#266: even a ``.bak`` that is not a ledger, since the delete never reads
    it), and the list shows the file no more: the one row left is the kept
    ``.bak``, listed as an ORPHAN row.

    DELIBERATE PIN CHANGE (backlog WP-109, #280, wave 15 integration). This
    said the list was EMPTY after the delete. WP-109 lists a kept backup whose
    session file is gone as an orphan row (so the operator can restore or
    remove it through the API and both UIs), and this delete keeps the ``.bak``
    on purpose, so the list is no longer empty: it holds exactly that backup.
    What the case still pins is that the deleted FILE is not listed (no row of
    the unreadable kind), and that the one row is the orphan.

    RED under mutant "delete loads the session first" (the route's first
    ``except SessionUnreadable: s = None`` removed, i.e. ``session_store.
    load`` restored as its first step), every kind, observed:

        [not_json] E   AssertionError: {"detail":"session file is unreadable:
        0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa (not valid JSON)","code":"session_unreadable"}
        E   assert 500 == 200
        [invalid] E   AssertionError: {"detail":"session file is unreadable:
        0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa (fails validation)","code":"session_unreadable"}
        E   assert 500 == 200
        [no_status] E   AssertionError: {"detail":"session file is unreadable:
        0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa (it has no status)","code":"session_unreadable"}
        E   assert 500 == 200

    RED under mutant "the locked re-read refuses an unreadable file" (the
    same clause removed from the write-locked section instead), every kind,
    observed:

        the same three failures, word for word: the re-read inside the lock
        raises what the first step no longer does.

    RED under mutant "not-JSON read as missing" (above), ``not_json`` alone:

        [not_json] E   AssertionError: {"detail":"session not found"}
        E   assert 404 == 200

    RED under mutant "delete unlinks both" (#266: ``delete``'s ``keep =
    keep_backup and bak.is_file()`` made ``keep = False``), every kind:

        [not_json] E   assert (False, False, False) == (False, True, True)
        E     At index 1 diff: False != True
    """
    path = _write(kind)
    bak, side = _side_files(SID)

    r = client.delete(f"/api/sessions/{SID}")

    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == SID
    assert (path.exists(), bak.exists(), side.exists()) == (False, True,
                                                             True)
    rows = client.get("/api/sessions").json()["sessions"]
    assert [(r["id"], r.get("orphan")) for r in rows] == [(SID, True)], (
        f"the list after the delete must hold only the kept backup, as an "
        f"orphan row: {rows}")


class _EngineRunning:
    """Enough engine for ``delete_session``: a live run of session ``sid``."""

    running = True

    def __init__(self, sid: str) -> None:
        self._session = type("S", (), {"id": sid})()

    async def drain_thumbs_for_session(self, session_id: str) -> None:
        return None


@pytest.mark.parametrize(("whose", "status"), [("this", 409),
                                                ("another", 200)])
def test_the_engines_word_still_decides_for_an_unreadable_file(
        client, monkeypatch, whose, status):
    """An unreadable file has no status to refuse on, so the engine's word is
    the only one left: while the engine runs THIS session (its file damaged
    by hand mid-run, say) the delete is refused and the file stays, because
    the run's next ledger write would put it back; while it runs another
    session, the file goes.

    RED under mutant "the engine's word dropped" (``running_it or`` removed
    from the locked section's refusal), ``[this]``, observed:

        E   AssertionError: {"deleted":"0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
        E   assert 200 == 409

    RED under mutant "any run blocks" (``and ours.id == session_id``
    removed from ``running_it``), ``[another]``, observed:

        E   AssertionError: {"detail":"cannot delete a running session"}
        E   assert 409 == 200
    """
    path = _write("no_status")
    before = _fingerprint(path)
    monkeypatch.setattr(app_module, "engine", _EngineRunning(
        SID if whose == "this" else "d" * 32))

    r = client.delete(f"/api/sessions/{SID}")

    assert r.status_code == status, r.text
    if status == 409:
        assert r.json()["detail"] == "cannot delete a running session"
        assert _fingerprint(path) == before
    else:
        assert not path.exists()


# ---------------------------------------------------------------- controls

def test_control_a_readable_active_session_still_answers_409(client):
    """CONTROL. Letting an unreadable file through must not let a running
    session through: the file's ``active`` still refuses.

    RED under mutant "the file's active dropped" (both ``s.status ==
    "active"`` tests in ``delete_session`` made ``False``), observed:

        E   AssertionError: assert 'cannot delet...hat is active' == 'cannot delet...nning session'
        E     - cannot delete a running session
        E     + cannot delete a session that is active

    Still a 409, from ``_DELETABLE_STATUSES``, which is why the sentence is
    asserted as well as the code.
    """
    s = _readable("active")
    path = session_store._path(s.id)
    before = _fingerprint(path)

    r = client.delete(f"/api/sessions/{s.id}")

    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "cannot delete a running session"
    assert _fingerprint(path) == before


def test_control_a_readable_dormant_session_deletes_as_before(client):
    """CONTROL. A readable dormant session is removed with its side files,
    exactly as before.

    RED under mutant "every readable session refused" (``if s is not None
    and s.status not in _DELETABLE_STATUSES:`` made ``if s is not None:``),
    observed:

        E   AssertionError: {"detail":"cannot delete a session that is dormant"}
        E   assert 409 == 200
    """
    s = _readable("dormant")
    bak, side = _side_files(s.id)

    r = client.delete(f"/api/sessions/{s.id}")

    assert r.status_code == 200, r.text
    assert not session_store._path(s.id).exists()
    assert (bak.exists(), side.exists()) == (False, False)


@pytest.mark.parametrize("kind", KINDS)
def test_control_a_viewer_cannot_delete_it(client, kind):
    """CONTROL. The unreadable file is deletable by whoever may delete a
    session, ``CAP_CONTROL_MOUNT``, and no one else: a viewer sees the row
    and gets 403, and the file stays.

    RED under mutant "delete gated on view" (the route's dependency
    ``require(CAP_CONTROL_MOUNT)`` made ``require(CAP_VIEW_STATUS)``),
    observed:

        [not_json] E   AssertionError: {"deleted":"0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
        E   assert 200 == 403

    and the same under ``[invalid]`` and ``[no_status]``. Changing the
    dependency alone is refused when the app is built
    (``RouteCapabilityError``: the route "DECLARES capability
    ['control.mount'] that no require() dependency enforces"), an ERROR at
    setup; the mutant moves the ``@declare`` too, so the test itself runs.
    """
    path = _write(kind)
    before = _fingerprint(path)
    set_active_provider(_Viewer())

    assert [r["id"] for r in
            client.get("/api/sessions").json()["sessions"]] == [SID]
    r = client.delete(f"/api/sessions/{SID}")

    assert r.status_code == 403, r.text
    assert _fingerprint(path) == before


def test_control_a_file_the_os_will_not_open_is_not_damage(client,
                                                           monkeypatch):
    """CONTROL. A session file the OS will not open (an antivirus scan, a
    replace in flight on Windows) is not damage, since nothing says it will
    stay that way: ``_parsed`` lets the ``OSError`` through, so the list
    neither shows it nor calls it unreadable, and DELETE answers 404 as it
    always has and leaves the file. Called unreadable, a good dormant ledger
    would be one tap from gone because a scanner held it for a moment. Once
    the file opens again it is listed as the session it is (the premise).

    RED under mutant "a locked file reads as damage" (``_parsed``'s
    ``except ValueError`` widened to ``except (ValueError,
    PermissionError)``), observed:

        E   AssertionError: ([{'id': '25dfe676d7c446689b18ce72bc7ebfcc',
        'name': '25dfe676d7c446689b18ce72bc7ebfcc', 'status': 'unreadable',
        'unreadable': 'not valid JSON', ...}],
        '{"deleted":"25dfe676d7c446689b18ce72bc7ebfcc"}')
        E   assert ([{'id': '25d...', ...}], 200) == ([], 404)

    The readable session was listed as damaged, and then deleted. Before
    this test the mutant was green on this file, test_session_without_status
    .py and test_session_store.py (59 passed): the rule was in a comment and
    in no test.
    """
    s = _readable("dormant")
    path = session_store._path(s.id)
    before = _fingerprint(path)
    real = session_mod.read_json

    def held_open(p):
        if os.path.basename(str(p)) == path.name:
            raise PermissionError(13, "The process cannot access the file "
                                      "because it is being used by another "
                                      "process", str(p))
        return real(p)

    monkeypatch.setattr(session_mod, "read_json", held_open)
    listed = client.get("/api/sessions").json()["sessions"]
    r = client.delete(f"/api/sessions/{s.id}")

    assert (listed, r.status_code) == ([], 404), (listed, r.text)
    assert _fingerprint(path) == before
    monkeypatch.setattr(session_mod, "read_json", real)
    assert [row["id"] for row in
            client.get("/api/sessions").json()["sessions"]] == [s.id], (
        "premise: the session is not listed even when it opens")


# --------------------------------------------------- the readers still skip

READERS = {
    "load_all": lambda: [s.id for s in session_store.load_all()],
    "armed": lambda: session_store.armed(),
    "active": lambda: session_store.active(),
    "current_for_flow": lambda: session_store.current_for_flow(FLOW),
}
NOTHING = {"load_all": []}                      # the rest answer None

#: Every reader on every kind it can be made to fail on. ``active`` and
#: ``current_for_flow`` scan the raw dicts and are held on ``no_status`` by
#: test_session_without_status.py; here they are held on the other two.
CASES = ([(r, k) for r in ("load_all", "armed") for k in KINDS]
         + [(r, k) for r in ("active", "current_for_flow")
            for k in ("not_json", "invalid")])


@pytest.mark.parametrize(("reader", "kind"), CASES,
                         ids=[f"{r}-{k}" for r, k in CASES])
def test_the_readers_that_count_it_still_skip_it(store_only, reader, kind):
    """Listed is not readable: each reader, on the unreadable file alone,
    finds nothing.

    RED under mutant "load_all keeps the unreadable entries" (``if s is not
    None`` dropped from ``load_all``), ``load_all`` and ``armed``, every
    kind, observed:

        [load_all-not_json] E   AttributeError: 'NoneType' object has no attribute 'id'
        [armed-not_json] E   AttributeError: 'NoneType' object has no attribute 'status'

    and the same under ``invalid`` and ``no_status``. The four raw-scan
    cases below stay green under it: they never read ``load_all``.

    RED under mutant "active raises on an unreadable file" (``active``'s
    ``except SessionUnreadable: continue`` removed), ``active-invalid``:

        E   pydantic_core._pydantic_core.ValidationError: 1 validation error for Session
        E   plan
        E     Input should be a valid dictionary or instance of SequencePlan
        [type=model_type, input_value=5, input_type=int]
        E   astrodeck.sequence.session.SessionUnreadable: session file is
        unreadable: 0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa (fails validation)

    RED under mutant "active's scan reads through _parsed" (``_scan_status``
    reads ``_parsed(path)`` in place of ``read_json_or(path)``),
    ``active-not_json``:

        E   json.decoder.JSONDecodeError: Expecting ',' delimiter: line 1 column 222 (char 221)
        E   astrodeck.sequence.session.SessionUnreadable: session file is
        unreadable: 0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa (not valid JSON)

    RED under mutant "newest_for_flow raises on an unreadable file" (its
    ``except SessionUnreadable: continue`` removed),
    ``current_for_flow-invalid``:

        the same four lines as "active raises on an unreadable file".

    RED under mutant "newest_for_flow reads through _parsed",
    ``current_for_flow-not_json``:

        the same two lines as "active's scan reads through _parsed".
    """
    _write(kind)
    got = READERS[reader]()
    assert got == NOTHING.get(reader), (
        f"{reader} read an unreadable file ({kind}) as a session: {got!r}")


@pytest.mark.parametrize("kind", KINDS)
def test_the_boot_sweep_neither_counts_nor_rewrites_it(store_only, kind):
    """``active`` on disk at boot means the process died mid-run; a file the
    store cannot read is no evidence of that, so the sweep leaves it alone.
    ``not_json`` and ``invalid`` state ``active`` in their text.

    RED under mutant "load_all keeps the unreadable entries", every kind:

        [not_json] E   AttributeError: 'NoneType' object has no attribute 'status'

    and the same under ``[invalid]`` and ``[no_status]``.
    """
    path = _write(kind)
    before = _fingerprint(path)
    assert session_store.boot_sweep() == 0
    assert _fingerprint(path) == before


@pytest.mark.parametrize("kind", KINDS)
def test_the_prune_sweep_never_removes_it(store_only, monkeypatch, kind):
    """The prune sweep deletes the oldest complete or abandoned sessions over
    the cap. It ran (one of two complete sessions is left), and the
    unreadable file is still there, byte for byte.

    RED under mutant "load_all keeps the unreadable entries", every kind:

        [not_json] E   AttributeError: 'NoneType' object has no attribute 'status'

    and the same under ``[invalid]`` and ``[no_status]``: the sweep runs
    inside the second ``save``.
    """
    monkeypatch.setattr(session_mod, "MAX_SESSIONS", 1)
    path = _write(kind)
    before = _fingerprint(path)
    first = _readable("complete")
    second = _readable("complete")
    left = [s.id for s in session_store.load_all()]
    assert len(left) == 1 and left[0] in (first.id, second.id), (
        f"premise: the prune sweep did not run: {left}")
    assert _fingerprint(path) == before
