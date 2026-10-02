# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A stored session with a frame type outside the four is named, and an armed
one never drops out of auto-resume without a word (#416, S4 review item 8).

Since #334 a plan step's ``frame_type`` must read as one of Light, Dark, Bias
or Flat through ``fitsio.frame_type_name``. Before it the field was plain
``str``, so a session on disk can hold a step whose frame type is
``DarkFlat``, and that session no longer validates. Two things then happened,
both in silence:

* ``GET /api/sessions`` listed it as unreadable with the reason "fails
  validation" and nothing else, so the operator could not tell what to
  repair. Now the reason names the step: its target, its index and filter,
  the field and the value, in words the store builds from the file. Never
  pydantic's rendering of the error, which quotes values from anywhere in the
  file (a frame's absolute path), in a list viewers read.
* ``SessionStore.armed()`` read through ``load_all``, which skips a file it
  cannot read, so a dormant session armed for auto-resume dropped out of
  auto-resume and the tick saw what "disarmed from the UI" looks like. Now
  ``armed`` logs one warning naming the session and the reason, once per
  file per process, and never rewrites the file.

The history search recorded on #416 found one spelling beyond the four that a
client wrote: lower-case ``light``, from both UIs' "send panels to plan".
``frame_type_name`` reads it (case and blanks ignored), and the last test here
holds that a stored session carrying it, or any case of the four, still loads,
lists and arms.

Every mutant was run in a private copy of ``server/``
(``scratchpad/S5-SESSION-mut/server``), never in the shared tree. Failures
are quoted verbatim (``--tb=short``), wrapped to fit, ``[...]`` eliding.
"""
from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
import astrodeck.hub as hub_module
from astrodeck.auth import reset_active_provider
from astrodeck.config import ConfigStore
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence.session import (INVALID, Session, SessionUnreadable,
                                        session_store)

#: 32 hex like every session id, fixed so the failures quoted name one file.
SID = "0416" + "d" * 28
NAME = "NGC 7000 campaign"
MTIME = 1_700_000_000

#: The reason ``list`` gives, and the warning ``armed`` logs, for the file
#: whose second step (filter Ha) says ``DarkFlat``.
DARKFLAT = ("fails validation: target 'M42', step 2 of 2 (filter 'Ha'): "
            "frame_type 'DarkFlat' is not one of Light, Dark, Bias, Flat "
            "(in any case)")
WARNING = (f"auto-resume: session '{NAME}' ({SID}) is armed but its file "
           f"cannot be read, so auto-resume will not start it ({DARKFLAT}). "
           f"Repair the file, or delete it from the sessions list.")


@pytest.fixture(autouse=True)
def _reset_provider_after():
    yield
    reset_active_provider()


@pytest.fixture
def client(tmp_path, monkeypatch):
    """test_sessions_api.py's harness: the real app, a throwaway config and
    captures directory, open auth (admin)."""
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
    """The store alone, in a directory of this test's own. ``armed`` says
    each file once per PROCESS, keyed by its path, so a directory no other
    test shares is what keeps one test's warning from silencing another's."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")


def _record(*, status: str = "dormant", auto_resume: bool = True,
            target_name: str = "M42") -> dict:
    """A session as the store writes it: target M42 with an L step and an
    Ha step, armed and dormant unless told otherwise. Edited by hand
    afterwards, the way a file reaches this state."""
    plan = SequencePlan(name=NAME, targets=[Target(
        name="M42", ra_hours=5.5881, dec_deg=-5.3911,
        steps=[ExposureStep(filter="L", exposure_s=60, count=2),
               ExposureStep(filter="Ha", exposure_s=300, count=2)])])
    s = Session(id=SID, name=NAME, created_ts=1.0, updated_ts=1.0,
                status=status, plan=plan, auto_resume=auto_resume)
    raw = json.loads(json.dumps(s.model_dump()))
    raw["plan"]["targets"][0]["name"] = target_name
    return raw


def _step(raw: dict, index: int) -> dict:
    return raw["plan"]["targets"][0]["steps"][index]


def _darkflat(**kw) -> dict:
    raw = _record(**kw)
    _step(raw, 1)["frame_type"] = "DarkFlat"
    return raw


def _write(raw, *, text: str | None = None, sid: str = SID):
    """``raw`` (or ``text`` verbatim) as session ``sid``'s file, its mtime
    pinned to ``MTIME``."""
    path = session_store._path(sid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw) if text is None else text,
                    encoding="utf-8")
    os.utime(path, ns=(MTIME * 10**9, MTIME * 10**9))
    return path


def _fingerprint(path) -> tuple[bytes, int]:
    return path.read_bytes(), os.stat(path).st_mtime_ns


# ------------------------------------------------------------ the list row

def test_a_darkflat_step_is_listed_naming_target_step_filter_field_value(
        client):
    """``GET /api/sessions`` lists the file as unreadable, and the reason
    names the target (M42), the step's index (2 of 2) and filter (Ha), the
    field (frame_type) and the value (DarkFlat). The row is otherwise the
    #242 row, and listing it does not touch the file.

    RED under mutant "the reason is pydantic's first line only"
    (``_session_from_file`` raises with ``str(e).splitlines()[0]``),
    observed:

        E   assert [{'id': '0416...ession', ...}] == [{'id': '0416...
        case)', ...}]
        E     At index 0 diff: {'id': '0416dddddddddddddddddddddddddddd',
        'name': 'NGC 7000 campaign', 'status': 'unreadable', 'unreadable':
        '1 validation error for Session', 'updated_ts': 1700000000.0} !=
        {'id': '0416dddddddddddddddddddddddddddd', 'name': 'NGC 7000
        campaign', 'status': 'unreadable', 'unreadable': "fails validation:
        target 'M42', step 2 of 2 (filter 'Ha'): frame_type 'DarkFlat' is
        not one of Light, Dark, Bias, Flat (in any case)", 'updated_ts':
        1700000000.0}
    """
    path = _write(_darkflat())
    before = _fingerprint(path)

    rows = client.get("/api/sessions").json()["sessions"]

    assert rows == [{"id": SID, "name": NAME, "status": "unreadable",
                     "unreadable": DARKFLAT, "updated_ts": float(MTIME)}]
    assert _fingerprint(path) == before
    # The same words reach ``load``'s refusal, which the app-wide handler
    # answers with.
    with pytest.raises(SessionUnreadable) as info:
        session_store.load(SID)
    assert info.value.reason == DARKFLAT


def _no_filter_missing_exposure(raw: dict) -> dict:
    _step(raw, 0)["filter"] = None
    del _step(raw, 0)["exposure_s"]
    return raw


def _count_zero(raw: dict) -> dict:
    _step(raw, 0)["count"] = 0
    return raw


#: Each: the edit, the reason. A step field other than the frame type is
#: named with pydantic's words for the RULE it broke (they describe the
#: constraint, never another part of the file); a missing one is said to be
#: missing, not quoted as None; a step with no filter says so; a target with
#: a blank name is named by its place.
OTHER_FIELDS = {
    "count": (_count_zero,
              "fails validation: target 'M42', step 1 of 2 (filter 'L'): "
              "count 0: input should be greater than 0"),
    "missing_exposure_no_filter": (
        _no_filter_missing_exposure,
        "fails validation: target 'M42', step 1 of 2 (no filter): "
        "exposure_s is missing"),
    "blank_target_name": (
        lambda raw: raw,
        "fails validation: target 1, step 2 of 2 (filter 'Ha'): "
        "frame_type 'DarkFlat' is not one of Light, Dark, Bias, Flat "
        "(in any case)"),
    "two_errors": (
        lambda raw: _count_zero(raw),
        "fails validation: target 'M42', step 1 of 2 (filter 'L'): "
        "count 0: input should be greater than 0 (and 1 more error)"),
}


@pytest.mark.parametrize("case", OTHER_FIELDS)
def test_any_step_field_is_named_with_its_rule(store_only, case):
    """The words are the step's, whatever field of it failed. ``two_errors``
    holds DarkFlat on step 2 as well as the count on step 1: the first is
    named, in pydantic's order, and the rest are counted, since repairing
    the one named would not make the file load.

    RED under mutant "only frame_type is named" (the step words return None
    for any other field), ``count`` and ``missing_exposure_no_filter`` and
    ``two_errors``, observed:

        [count] E     At index 0 diff: ('0416dddddddddddddddddddddddddddd',
        'unreadable', 'fails validation') != ('0416ddd[...]', 'unreadable',
        "fails validation: target 'M42', step 1 of 2 (filter 'L'): count 0:
        input should be greater than 0")
        [missing_exposure_no_filter] E     At index 0 diff: ('0416ddd[...]',
        'unreadable', 'fails validation') != ('0416ddd[...]', 'unreadable',
        "fails validation: target 'M42', step 1 of 2 (no filter):
        exposure_s is missing")
        [two_errors] E     At index 0 diff: ('0416ddd[...]', 'unreadable',
        "fails validation: target 'M42', step 2 of 2 (filter 'Ha'):
        frame_type 'DarkFlat' is not one of Light, Dark, Bias, Flat (in any
        case) (and 1 more error)") != ('0416ddd[...]', 'unreadable', "fails
        validation: target 'M42', step 1 of 2 (filter 'L'): count 0: input
        should be greater than 0 (and 1 more error)")

    RED under mutant "a missing field is quoted as None" (no ``missing``
    branch), ``missing_exposure_no_filter``, observed:

        E     At index 0 diff: ('0416ddd[...]', 'unreadable', "fails
        validation: target 'M42', step 1 of 2 (no filter): exposure_s None:
        field required") != ('0416ddd[...]', 'unreadable', "fails
        validation: target 'M42', step 1 of 2 (no filter): exposure_s is
        missing")

    RED under mutant "no filter quoted as None" (the filter always
    quoted), ``missing_exposure_no_filter``, observed:

        E     At index 0 diff: ('0416ddd[...]', 'unreadable', "fails
        validation: target 'M42', step 1 of 2 (filter None): exposure_s is
        missing") != ('0416ddd[...]', 'unreadable', "fails validation:
        target 'M42', step 1 of 2 (no filter): exposure_s is missing")

    RED under mutant "a blank target name quoted" (the name used whenever
    it is a string), ``blank_target_name``, observed:

        E     At index 0 diff: ('0416ddd[...]', 'unreadable', "fails
        validation: target '', step 2 of 2 (filter 'Ha'): frame_type
        'DarkFlat' is not one of Light, Dark, Bias, Flat (in any case)") !=
        ('0416ddd[...]', 'unreadable', "fails validation: target 1, step 2
        of 2 (filter 'Ha'): frame_type 'DarkFlat' is not one of Light,
        Dark, Bias, Flat (in any case)")

    RED under mutant "the other errors go unsaid" (no count of the rest),
    ``two_errors``, observed:

        E     At index 0 diff: ('0416ddd[...]', 'unreadable', "fails
        validation: target 'M42', step 1 of 2 (filter 'L'): count 0: input
        should be greater than 0") != ('0416ddd[...]', 'unreadable', "fails
        validation: target 'M42', step 1 of 2 (filter 'L'): count 0: input
        should be greater than 0 (and 1 more error)")
    """
    edit, reason = OTHER_FIELDS[case]
    if case in ("blank_target_name", "two_errors"):
        raw = _darkflat(target_name="" if case == "blank_target_name"
                        else "M42")
    else:
        raw = _record()
    _write(edit(raw))

    assert [(r["id"], r["status"], r["unreadable"])
            for r in session_store.list()] == [(SID, "unreadable", reason)]


def test_control_a_failure_outside_any_step_keeps_the_bare_reason(
        store_only):
    """CONTROL. A frame whose metric holds a file path fails validation
    outside every step. The reason stays the store's bare ``INVALID``, and
    the path appears nowhere in the row: pydantic's rendering of the error
    would quote it (``input_value='D:/captures/...'``), and the list is read
    by viewers.

    RED under mutant "the fallback is pydantic's text" (a failure outside a
    step answers ``f"{INVALID}: {e}"``), observed:

        E     At index 0 diff: ('0416dddddddddddddddddddddddddddd', "fails
        validation: 1 validation error for Session\\nframes.0.metrics.hfr\\n
        Input should be a valid number, unable to parse string as a number
        [type=float_parsing, input_value='D:/captures/M42/Light_0001.fits',
        input_type=str]\\n    For further information visit
        https://errors.pydantic.dev/2.13/v/float_parsing") !=
        ('0416dddddddddddddddddddddddddddd', 'fails validation')
    """
    raw = _record()
    raw["frames"] = [{"id": "b" * 32, "step_id": _step(raw, 0)["id"],
                      "metrics": {"hfr": "D:/captures/M42/Light_0001.fits"}}]
    _write(raw)

    rows = session_store.list()

    assert [(r["id"], r["unreadable"]) for r in rows] == [(SID, INVALID)]
    assert "Light_0001" not in json.dumps(rows)


# ---------------------------------------------------------- the armed scan

def test_an_armed_unreadable_session_is_said_once_and_never_rewritten(
        store_only, bus_lines):
    """``armed`` meets the armed, dormant DarkFlat file on three ticks. It
    answers None each time (the file is not a session it can start), logs
    ONE warning naming the session and the reason, and leaves the file's
    bytes and mtime as they were.

    RED under mutant "the armed scan skips silently" (``armed`` passes over
    a file it cannot read, as ``load_all`` did for it before #416),
    observed:

        E   assert [] == [('warning', ..., 'sequence')]
        E     Right contains one more item: ('warning', "auto-resume: session
        'NGC 7000 campaign' (0416dddddddddddddddddddddddddddd) is armed but
        its file cannot ... not one of Light, Dark, Bias, Flat (in any
        case)). Repair the file, or delete it from the sessions list.",
        'sequence')

    RED under mutant "warns on every tick" (the once-per-file record is
    never consulted), observed:

        E   assert [('warning', ..., 'sequence')] == [('warning', ...,
        'sequence')]
        E     Left contains 2 more items, first extra item: ('warning',
        "auto-resume: session 'NGC 7000 campaign'
        (0416dddddddddddddddddddddddddddd) is armed but its file cannot ...
        not one of Light, Dark, Bias, Flat (in any case)). Repair the file,
        or delete it from the sessions list.", 'sequence')
    """
    path = _write(_darkflat())
    before = _fingerprint(path)

    assert [session_store.armed() for _tick in range(3)] == [None] * 3

    assert bus_lines == [("warning", WARNING, "sequence")]
    assert _fingerprint(path) == before


def test_an_armed_file_left_active_by_a_restart_is_said_too(store_only,
                                                           bus_lines):
    """A deploy over a live run leaves the file ``active``: the engine was
    writing it when the process stopped, and ``boot_sweep``, which turns an
    active session dormant at boot, reads through ``load_all`` and cannot
    sweep a file it cannot read. Readable, that session would have been
    swept and resumed, so it drops out of auto-resume just the same, and is
    said just the same, once.

    RED under mutant "dormant only" (the raw status must be dormant),
    observed:

        E   assert [] == [('warning', ..., 'sequence')]
        E     Right contains one more item: ('warning', "auto-resume: session
        'NGC 7000 campaign' (0416dddddddddddddddddddddddddddd) is armed but
        its file cannot ... not one of Light, Dark, Bias, Flat (in any
        case)). Repair the file, or delete it from the sessions list.",
        'sequence')

    Also RED under "the armed scan skips silently" (the same failure) and
    under "warns on every tick":

        E     Left contains one more item: ('warning', "auto-resume: session
        'NGC 7000 campaign' (0416dddddddddddddddddddddddddddd) is armed but
        its file cannot ... [...]", 'sequence')
    """
    path = _write(_darkflat(status="active"))
    before = _fingerprint(path)

    assert session_store.armed() is None
    assert session_store.armed() is None

    assert bus_lines == [("warning", WARNING, "sequence")]
    assert _fingerprint(path) == before


def test_two_armed_unreadable_files_are_each_said_once(store_only, bus_lines):
    """ONCE PER FILE, not once per process. Two armed DarkFlat files in one
    directory are two campaigns that will not resume, so each is said, once,
    however many ticks meet them.

    The tests above each hold one such file, so a record that silenced every
    file after the first passed each of them run alone (the tests share one
    process only by chance of scheduling); this one holds two in the same
    walk.

    RED under mutant "once per process, not per file" (``key = "armed"`` in
    place of the file's path), observed (verifier, private copy
    ``scratchpad/S5-SESSION-verify-mut/server``):

        E   assert [('warning', ..., 'sequence')] == [('warning', ...,
        'sequence')]
        E     Right contains one more item: ('warning', "auto-resume: session
        'Veil campaign' (0416eeeeeeeeeeeeeeeeeeeeeeeeeeee) is armed but its
        file cannot be r... not one of Light, Dark, Bias, Flat (in any
        case)). Repair the file, or delete it from the sessions list.",
        'sequence')

    Also RED under "warns on every tick" (six warnings, not two) and under
    "the armed scan skips silently" (none).
    """
    sid2, name2 = "0416" + "e" * 28, "Veil campaign"
    second = _darkflat()
    second["id"], second["name"] = sid2, name2
    paths = [_write(_darkflat()), _write(second, sid=sid2)]
    before = [_fingerprint(p) for p in paths]

    assert [session_store.armed() for _tick in range(3)] == [None] * 3

    assert bus_lines == [
        ("warning", WARNING, "sequence"),
        ("warning", WARNING.replace(f"'{NAME}' ({SID})",
                                    f"'{name2}' ({sid2})"), "sequence")]
    assert [_fingerprint(p) for p in paths] == before


#: Unreadable files that were never going to be resumed, each of them holding
#: whatever else a reader might take for armed.
NOT_ARMED = {
    "disarmed": lambda: _write(_darkflat(auto_resume=False)),
    "complete": lambda: _write(_darkflat(status="complete")),
    "no_status": lambda: _write({k: v for k, v in _darkflat().items()
                                 if k != "status"}),
    "not_json": lambda: _write(None, text=json.dumps(_record())[:-40]),
}


@pytest.mark.parametrize("case", NOT_ARMED)
def test_control_an_unreadable_file_that_was_not_armed_logs_nothing(
        store_only, bus_lines, case):
    """CONTROL. Auto-resume would not have started any of these had it been
    readable (disarmed; complete; a file that states no status is no
    session, #218; a file that is not JSON states nothing), so nothing
    dropped out of auto-resume, and ``armed`` says nothing and writes
    nothing.

    RED under mutant "warns for any unreadable file" (the armed condition
    dropped), every case, observed:

        [disarmed], [complete] and [no_status], each:
        E   assert [('warning', ..., 'sequence')] == []
        E     Left contains one more item: ('warning', "auto-resume: session
        'NGC 7000 campaign' (0416dddddddddddddddddddddddddddd) is armed but
        its file cannot ... not one of Light, Dark, Bias, Flat (in any
        case)). Repair the file, or delete it from the sessions list.",
        'sequence')
        [not_json]:
        E     Left contains one more item: ('warning', "auto-resume: session
        '0416dddddddddddddddddddddddddddd' (0416dddddddddddddddddddddddddddd)
        is armed but i... so auto-resume will not start it (not valid
        JSON). Repair the file, or delete it from the sessions list.",
        'sequence')
    """
    path = NOT_ARMED[case]()
    before = _fingerprint(path)

    assert session_store.armed() is None

    assert bus_lines == []
    assert _fingerprint(path) == before


def test_control_a_readable_armed_session_is_still_the_one_armed(
        store_only, bus_lines):
    """CONTROL. Beside the DarkFlat file, a readable dormant armed session is
    still what ``armed`` answers: the warning is said and the scan goes on.
    The file ``armed`` warns about is the unreadable one only.

    Also RED under "the armed scan skips silently", observed:

        E   assert [] == [('warning', ..., 'sequence')]
        E     Right contains one more item: ('warning', "auto-resume: session
        'NGC 7000 campaign' (0416dddddddddddddddddddddddddddd) is armed but
        its file cannot ... [...]", 'sequence')
    """
    _write(_darkflat())
    good = Session(name="readable", created_ts=2.0, status="dormant",
                   plan=SequencePlan(name="readable"), auto_resume=True)
    session_store.save(good)

    armed = session_store.armed()

    assert armed is not None and armed.id == good.id
    assert bus_lines == [("warning", WARNING, "sequence")]


# ------------------------------------------- the spellings clients wrote

@pytest.mark.parametrize("stored, read", [
    ("light", "Light"),          # both UIs' "send panels to plan" (#416)
    ("LIGHT", "Light"),
    (" Dark ", "Dark"),
    ("", "Light"),
])
def test_control_every_spelling_a_client_wrote_still_loads_lists_and_arms(
        store_only, bus_lines, stored, read):
    """CONTROL, and the migration the history search on #416 asked for.
    Lower-case ``light`` is the one spelling beyond the four that a client
    ever wrote (``AtlasView.tsx`` and the Sky hub's ``mosaic.ts``); it, any
    case of the four and a blank are read by ``frame_type_name`` as one of
    the four, so the session loads with the canonical spelling, lists as a
    session and is the one armed, with nothing said.

    RED under mutant "frame_type_name reads case-sensitively" (in
    ``imaging/fitsio.py``, ``_BY_FOLDED`` keyed by the four spellings
    unfolded, and looked up without ``lower()``), ``light`` and ``LIGHT``,
    observed:

        [light-Light] E   pydantic_core._pydantic_core.ValidationError: 1
        validation error for Session
        E   plan.targets.0.steps.1.frame_type
        E     Value error, frame type 'light' is not one of Light, Dark,
        Bias, Flat (in any case) [type=value_error, input_value='light',
        input_type=str]
        [...]
        E   astrodeck.sequence.session.SessionUnreadable: session file is
        unreadable: 0416dddddddddddddddddddddddddddd (fails validation:
        target 'M42', step 2 of 2 (filter 'Ha'): frame_type 'light' is not
        one of Light, Dark, Bias, Flat (in any case))

    and the same for ``LIGHT``.
    """
    raw = _record()
    _step(raw, 1)["frame_type"] = stored
    _write(raw)

    loaded = session_store.load(SID)
    armed = session_store.armed()

    assert loaded.plan.targets[0].steps[1].frame_type == read
    assert [(r["id"], r["status"]) for r in session_store.list()] == [
        (SID, "dormant")]
    assert armed is not None and armed.id == SID
    assert bus_lines == []
