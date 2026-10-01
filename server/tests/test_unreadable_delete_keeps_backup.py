"""DELETE of an unreadable session file keeps its ``.bak`` and says so (#266).

#242 let ``DELETE /api/sessions/{id}`` remove a file the store cannot read,
and it removed the file's ``.bak`` with it. The only ``.bak`` a session has
is the copy ``SessionStore.backup`` takes before an ADOPT rewrites the
ledger, so for a ledger damaged after an ADOPT that copy can be the last good
record of which frames were accepted for which step. The operator saw
"unreadable: not valid JSON", pressed the only control the row offered, and
lost the copy that could have been recovered. A destructive action removed
more than the thing it named.

Now, for an unreadable file (spec owner list item 9, H2 orchestrator ruling
12 as amended by #242):

* DELETE removes ``<id>.json`` and nothing else. ``<id>.json.bak`` stays byte
  for byte, and so does the thumbnails directory, because the thumbnails
  belong to the ledger the backup holds.
* The answer names the backup and says it remains.
* ``GET /api/sessions`` marks the row ``backup: true`` while a ``.bak`` sits
  beside the file, so the confirm can say what stays before the tap.

A READABLE session's DELETE is unchanged: the operator deleted a ledger they
could read, and a ``.bak`` left behind would be a copy nothing lists and
nothing removes (the control below). So is an unreadable file with no
backup: it goes with its thumbnails, as #242 built it.

Each mutant was run in a private copy of ``server/`` (never the shared tree,
#254) from the pristine bytes of the file it changes, restored and sha256
compared after each. Failures are quoted verbatim (``--tb=short``), wrapped
to fit, ``[...]`` eliding.
"""
from __future__ import annotations

import hashlib
import json

import pytest

import astrodeck.sequence.session as session_mod
from astrodeck.sequence.session import Session, SessionFrame, session_store
from test_unreadable_sessions_listed_and_deletable import (  # noqa: F401
    KINDS, MTIME, SID, _plan, _readable, _write, client)

BAK_NAME = f"{SID}.json.bak"


def _good_backup_bytes() -> bytes:
    """A backup ``SessionStore`` can read: the session ``SID`` as it was
    before a (notional) ADOPT, one accepted frame on its step."""
    s = Session(id=SID, name="before the adopt", created_ts=1.0,
                status="dormant", plan=_plan())
    t = s.plan.targets[0]
    s.frames.append(SessionFrame(ts=1.0, night="n1", target_id=t.id,
                                 step_id=t.steps[0].id, auto_accepted=True))
    return json.dumps(s.model_dump()).encode("utf-8")


def _backup_beside(sid: str, data: bytes):
    path = session_store._path(sid)
    bak = path.with_suffix(path.suffix + ".bak")
    bak.write_bytes(data)
    return bak


def _thumb(sid: str):
    thumbs = session_store.thumbs_dir(sid)
    thumbs.mkdir(parents=True)
    jpg = thumbs / ("c" * 32 + ".jpg")
    jpg.write_bytes(b"\xff\xd8thumb")
    return jpg


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree() -> dict[str, str]:
    """Every file under the sessions directory, by relative path, with its
    sha256: what "removes only <id>.json" is measured against."""
    root = session_mod._sessions_dir()
    return {p.relative_to(root).as_posix(): _sha(p)
            for p in root.rglob("*") if p.is_file()}


# ------------------------------------------------------ the backup survives

@pytest.mark.parametrize("kind", KINDS)
def test_delete_of_an_unreadable_file_removes_only_that_file(client, kind):
    """The unreadable ``<id>.json`` goes; its readable ``.bak`` stays byte
    for byte (sha256) and so does the thumbnail, and nothing else in the
    sessions directory changes. The answer names the backup and says it
    remains.

    RED under mutant "delete unlinks both" (``delete``'s ``keep =
    keep_backup and bak.is_file()`` made ``keep = False``, which is the
    #242 code), every kind, observed:

        [not_json]     assert _tree() == want
        E   AssertionError: assert {} == {'0242aaaaaaa...7b7299fcd17e'}
        E     Right contains 2 more items:
        E     {'0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa.json.bak': '691a7e3f59c3[...]',
        E      '0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa/thumbs/cccccccccccccccccccccccccccccccc.jpg':
        '5d2ed9110959[...]'}

    and the same under ``[invalid]`` and ``[no_status]`` (the ``.bak``'s
    sha256 differs, since each run's plan has new ids). The backup and the
    thumbnail were both gone. test_unreadable_sessions_listed_and_deletable
    .py's ``test_each_kind_is_deletable`` went red with it, every kind
    (``assert (False, False, False) == (False, True, True)``), and so did
    the stat test below.

    RED under mutant "the route never asks to keep" (``keep_backup=s is
    None`` made ``keep_backup=False`` in ``delete_session``), every kind:

        the same seven failures, word for word but for the ``.bak``'s sha256.

    RED under mutant "the thumbnails go with the kept backup" (``delete``'s
    kept branch runs the thumbs ``rmtree`` before it returns), every kind:

        [not_json] E   AssertionError: assert {'0242aaaaaaa...a9d585df1aa1'} == {'0242aaaaaaa...7b7299fcd17e'}
        E     Omitting 1 identical items, use -vv to show
        E     Right contains 1 more item:
        E     {'0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa/thumbs/cccccccccccccccccccccccccccccccc.jpg':
        '5d2ed91109595956500dc8ec08b760bb2eb0cc31152ceac8eeb37b7299fcd17e'}

    RED under mutant "the answer does not name the backup" (the route's
    ``detail`` reduced to ``f"Removed {kept.stem}."``), every kind:

        [not_json] E   AssertionError: Removed 0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa.json.
        E   assert '0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa.json.bak' in 'Removed 0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa.json.'
    """
    path = _write(kind)
    good = _good_backup_bytes()
    # Premise: the backup is a ledger the store would read, so it is the
    # "last good copy" the issue is about and not just a file of that name.
    assert session_mod._session_from_file(json.loads(good), SID).id == SID
    bak = _backup_beside(SID, good)
    _thumb(SID)
    bak_sha = _sha(bak)
    before = _tree()
    assert f"{SID}.json" in before and BAK_NAME in before, before

    r = client.delete(f"/api/sessions/{SID}")

    assert r.status_code == 200, r.text
    assert not path.exists()
    # The disk before the words: what the operator loses is the point.
    want = dict(before)
    del want[f"{SID}.json"]
    assert _tree() == want
    assert _sha(bak) == bak_sha
    body = r.json()
    detail = body.pop("detail", "")
    assert body == {"deleted": SID, "backup_kept": BAK_NAME}, r.text
    assert BAK_NAME in detail, detail
    assert "remains" in detail, detail


# ------------------------------------------------------------- the listing

@pytest.mark.parametrize("backup", ["with", "without"])
def test_the_unreadable_row_says_when_a_backup_exists(client, backup):
    """The row carries ``backup: true`` while a ``.bak`` sits beside the file,
    and no ``backup`` key when none does: the #242 row, unchanged.

    RED under mutant "the row never says backup" (``_unreadable_row``'s
    ``if _has_backup(path):`` made ``if False:``), ``[with]``, observed:

        E   AssertionError: assert [{'id': '0242...d JSON', ...}] == [{'backup': T...adable', ...}]
        E     At index 0 diff: {'id': '0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'name':
        '0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'status': 'unreadable', 'unreadable':
        'not valid JSON', 'updated_ts': 1700000000.0} != {'id': [...],
        'updated_ts': 1700000000.0, 'backup': True}

    RED under mutant "the row always says backup" (the same test made
    ``if True:``), ``[without]``, observed:

        E   AssertionError: assert [{'backup': T...adable', ...}] == [{'id': '0242...d JSON', ...}]
        E     At index 0 diff: {'id': [...], 'updated_ts': 1700000000.0,
        'backup': True} != {'id': [...], 'updated_ts': 1700000000.0}

    and with it the stat-refused control below (``assert [True] ==
    [None]``) and test_unreadable_sessions_listed_and_deletable.py's
    listing test, every kind (``Left contains 1 more item: {'backup':
    True}``).
    """
    _write("not_json")
    if backup == "with":
        _backup_beside(SID, _good_backup_bytes())

    rows = client.get("/api/sessions").json()["sessions"]

    want = {"id": SID, "name": SID, "status": "unreadable",
            "unreadable": KINDS["not_json"][1], "updated_ts": float(MTIME)}
    if backup == "with":
        want["backup"] = True
    assert rows == [want]


def test_control_a_backup_the_os_will_not_stat_is_not_claimed(client,
                                                              monkeypatch):
    """CONTROL. A ``.bak`` whose stat the OS refuses is not claimed, and the
    list still answers: one file nobody can look at must not cost the whole
    list, the rule ``_unreadable_row`` already keeps for the file itself.

    RED under mutant "the backup's stat unguarded" (``_has_backup``'s
    ``try``/``except OSError: return False`` removed), observed:

        astrodeck\\sequence\\session.py:364: in _has_backup
            return _backup_path(path).is_file()
        tests\\test_unreadable_delete_keeps_backup.py:213: in is_file
            raise PermissionError(13, "Access is denied", str(self.p))
        E   PermissionError: [Errno 13] Access is denied: 'C:\\\\Users\\\\[...]
        \\\\captures\\\\sessions\\\\0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa.json.bak'

    raised out of ``GET /api/sessions``: the whole list, for one file.

    A REAL BACKUP SITS THERE, so "not claimed" can only come from the
    refused stat, never from there being nothing to find. Without it the
    refusal, injected at ``_backup_path``, went unasked by a ``_has_backup``
    that builds the path itself, and the test passed with the guard gone.
    RED under verifier mutant "the stat around the seam, unguarded"
    (``_has_backup``'s body made ``return path.with_suffix(path.suffix +
    ".bak").is_file()``, no ``try``), observed; before the backup was
    written here, all nine tests in this file passed under it:

        E   assert [True] == [None]
    """
    _write("not_json")
    _backup_beside(SID, _good_backup_bytes())

    class _Refused:
        def __init__(self, p):
            self.p = p

        def is_file(self):
            raise PermissionError(13, "Access is denied", str(self.p))

    real = session_mod._backup_path
    monkeypatch.setattr(session_mod, "_backup_path",
                        lambda p: _Refused(real(p)))

    r = client.get("/api/sessions")

    assert r.status_code == 200, r.text
    assert [row.get("backup") for row in r.json()["sessions"]] == [None]


def test_a_backup_the_os_will_not_stat_stops_the_delete_before_it_starts(
        client, monkeypatch):
    """``delete`` looks for the backup BEFORE it removes anything, so a stat
    the OS refuses fails the delete with the unreadable file still there:
    had the file gone first, a backup nobody could see would be all that
    was left, and the answer would never have named it. The error reaches
    the caller (the route answers 500); nothing is half done.

    RED under mutant "the backup looked for after the unlink" (``keep =
    keep_backup and bak.is_file()`` moved below ``path.unlink()``),
    observed:

        E   AssertionError: the unreadable file went before the backup was looked for
        E   assert False
        E    +  where False = exists()
        E    +    where exists = WindowsPath('C:/Users/bear/AppData/Local/Temp/
        pytest-of-bear/[...]/captures/sessions/0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa.json').exists

    The same failure under "delete unlinks both" and "the route never asks
    to keep" (the stat then comes from the unlink branch's ``bak.exists()``,
    after the file has gone). Under "the kept branch keyed on the flag
    alone" it never stats at all, removes the file and dies naming the
    backup: ``AttributeError: '_Refused' object has no attribute 'name'``.
    """
    path = _write("no_status")
    before = _sha(path)

    class _Refused:
        def __init__(self, p):
            self.p = p

        def is_file(self):
            raise PermissionError(13, "Access is denied", str(self.p))

        def exists(self):
            return self.is_file()

    real = session_mod._backup_path
    monkeypatch.setattr(session_mod, "_backup_path",
                        lambda p: _Refused(real(p)))

    with pytest.raises(PermissionError):
        client.delete(f"/api/sessions/{SID}")

    assert path.exists(), (
        "the unreadable file went before the backup was looked for")
    assert _sha(path) == before


# ----------------------------------------------------------------- controls

def test_control_a_readable_sessions_backup_still_goes(client):
    """CONTROL. A readable dormant session's DELETE removes the file, the
    ``.bak`` the real ADOPT path takes (``SessionStore.backup``) and the
    thumbnails, and answers exactly ``{"deleted": id}``, as documented: the
    operator deleted a ledger they could read.

    RED under mutant "every delete keeps the backup" (``keep_backup=s is
    None`` made ``keep_backup=True`` in ``delete_session``), observed:

        E   AssertionError: {"deleted":"9f7f62f5f7184b77a81a32ab9b9a5a79",
        "backup_kept":"9f7f62f5f7184b77a81a32ab9b9a5a79.json.bak","detail":
        "Removed 9f7f62f5f7184b77a81a32ab9b9a5a79.json, which could not be
        read. Its backup 9f7f62f5f7184b77a81a32ab9b9a5a79.json.bak remains in
        the sessions folder, [...] if the backup itself is intact."}
        E   assert {'backup_kept...f is intact.'} == {'deleted': '...32ab9b9a5a79'}
        E     Left contains 2 more items:

    The words were false there too: that file could be read. The #242
    control, test_unreadable_sessions_listed_and_deletable.py's
    ``test_control_a_readable_dormant_session_deletes_as_before``, went red
    with it: ``assert (True, True) == (False, False)``.
    """
    s = _readable("dormant")
    bak = session_store.backup(s.id)
    jpg = _thumb(s.id)
    assert bak.exists(), "premise: the ADOPT backup was not taken"

    r = client.delete(f"/api/sessions/{s.id}")

    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": s.id}, r.text
    assert (session_store._path(s.id).exists(), bak.exists(),
            jpg.parent.parent.exists()) == (False, False, False)


def test_control_an_unreadable_file_with_no_backup_goes_with_its_thumbs(
        client):
    """CONTROL. With no ``.bak`` beside it, an unreadable file goes with its
    thumbnails and the answer is exactly ``{"deleted": id}``, as #242 built
    it: there is no ledger left for them to belong to, and nothing to name.

    RED under mutant "the kept branch keyed on the flag alone" (``keep =
    keep_backup and bak.is_file()`` made ``keep = keep_backup``), observed:

        E   AssertionError: {"deleted":"0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "backup_kept":"0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa.json.bak","detail":
        "Removed 0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa.json, which could not be
        read. Its backup 0242aaaaaaaaaaaaaaaaaaaaaaaaaaaa.json.bak remains in
        the sessions folder, [...] if the backup itself is intact."}
        E   assert {'backup_kept...f is intact.'} == {'deleted': '...aaaaaaaaaaaa'}
        E     Left contains 2 more items:

    It named a backup that does not exist, and kept the thumbnails for it.
    """
    path = _write("invalid")
    jpg = _thumb(SID)

    r = client.delete(f"/api/sessions/{SID}")

    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": SID}, r.text
    assert (path.exists(), jpg.parent.parent.exists()) == (False, False)
    assert client.get("/api/sessions").json()["sessions"] == []
