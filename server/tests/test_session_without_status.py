# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A session file with no ``status`` key has no status (#218, H2
orchestrator ruling 12, spec "Still waiting on the owner" item 9).

``Session.status`` defaults to "active", which is right for a session the
engine builds in code (it passes the status anyway) and wrong for a file read
off disk: a file that does not say what became of its session was read as a
RUNNING one. The readers then disagreed about it. ``load`` answered active,
``active()`` (a raw-dict scan) did not find it, and ``boot_sweep`` (through
``load_all``) swept it: dormant, one crash counted that never happened, and
the default-filled model saved over the file, dropping every key the model
does not know. Three such counts and ResumeArm gives up and stows the rig.

The ruling: every ``SessionStore`` reader treats such a file as unreadable.
``load`` raises ``SessionUnreadable`` naming the reason, and every scanning
reader skips it, the rule they already keep for a file that fails validation.
Since H3 (#242) ``list`` shows it, as a row with ``status: "unreadable"`` and
the reason and never as a session, so ``DELETE`` can remove it; that half is
``test_unreadable_sessions_listed_and_deletable.py``'s, and here ``list`` is
held only to the first half: none of its sessions is this file.

Written first and run against the unfixed store: 9 RED (``load``; ``load_all``
and ``list`` on both shapes; the sweep on both shapes; the sweep-to-ResumeArm
chain; ``engine.start``). The rest were green: the raw scans behind
``active`` and the flow lookups already missed the file, and the controls
held (the invalid-file control's ``reason`` check was added afterwards; the
unfixed exception has no ``reason``).

Mutants, each run from a byte-for-byte backup of ``session.py``, which was
restored byte-identical (sha256 compared) after every one:

  M1 "status defaults to active on read": ``_stated_status`` answers
     ``raw.get("status", "active")``. #218's default, applied at the one place
     every reader now asks a file its status, so it reaches all of them.
  M2 "missing status reads as dormant": the tempting wrong fix. The model
     default becomes "dormant" and the refusal in ``_session_from_file`` is
     removed, so a status-less file loads as a dormant session: ``boot_sweep``
     leaves it alone, but ``armed``/``recoverable`` hand it to ResumeArm and
     the recover routes.
  M3 "status required on the model": the other tempting fix. ``status: str``
     with no default, so every ``Session(...)`` built in code without one
     raises, and a status-less file is refused without its reason.
  M4 "the refusal inverted": ``if _stated_status(raw) is not None``, so every
     VALID file is refused and the status-less one is read.
  M5 "validation errors escape": ``_session_from_file`` stops wrapping a
     pydantic error in ``SessionUnreadable``.
  M6 "reason dropped from the message": ``SessionUnreadable`` ignores
     ``reason`` when it builds its message.
  M7 "load_all bypasses the refusal": ``load_all`` validates with
     ``Session.model_validate`` directly, as it did before the fix.
  M8 "status checked before validation": ``_session_from_file`` refuses a
     status-less file before it validates it, so a file damaged in other ways
     too is reported only as missing its status.

Every collected test goes RED under at least one of them.

Re-run in H3 (#242), after ``load_all`` and ``list`` moved onto one walk of
the directory (``SessionStore._entries``) and ``list`` began to show the
file as an unreadable row: M1, M2 and M4 as written, and M7 as that walk
validating with ``Session.model_validate`` directly (its ``except
SessionUnreadable`` widened to ``except Exception``). Every RED claimed
below held, test for test; ``list`` is now read for its session rows only.
M7 in that form also turns ``test_control_an_invalid_file_is_still_unreadable``
red, its row's reason being "".

Failures quoted below are verbatim pytest output, wrapped to fit, with
``[...]`` eliding the middle of a long model repr.

The file is written by hand, as #218's was: nothing in the store writes a
session without a status, which is exactly why the default was never seen.
"""
from __future__ import annotations

import json
import os

import pytest

import astrodeck.hub as hub_module
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence.session import (INVALID, Session, SessionUnreadable,
                                        session_store)

#: 32 hex, like every session id. Fixed so the failures quoted below name the
#: same file every run.
SID = "0218" + "e" * 28
FLOW = "f" * 32

#: #218's hand-written file, verbatim.
ISSUE_218 = {"id": SID, "name": "hand-edited", "extra_key": 1}

#: The same file carrying every field a scanning reader filters on, so each
#: reader has something to find: ``newest_for_flow``/``current_for_flow`` look
#: for the flow's origin, ``armed`` for ``auto_resume``, ``recoverable`` for a
#: frame. ``crash_resumes`` is 2 so the sweep's false count would be the
#: third, the one ResumeArm gives up on (``RESUME_GIVE_UP_AFTER``).
LOADED = {**ISSUE_218, "origin": "flow", "origin_id": FLOW,
          "auto_resume": True, "crash_resumes": 2,
          "created_ts": 1.0, "updated_ts": 1.0,
          "frames": [{"id": "a" * 32, "step_id": "s1"}]}

SHAPES = {"issue_218": ISSUE_218, "loaded": LOADED}

#: Any mtime well in the past. A rewrite stamps the current time, so pinning
#: this first makes "the file was rewritten" visible even if the rewrite lands
#: inside one tick of the filesystem clock.
OLD_NS = 1_600_000_000 * 10**9


@pytest.fixture(autouse=True)
def capture_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    return tmp_path


def _write(raw: dict, sid: str = SID):
    path = session_store._path(sid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw), encoding="utf-8")
    os.utime(path, ns=(OLD_NS, OLD_NS))
    return path


def _fingerprint(path) -> tuple[bytes, int]:
    return path.read_bytes(), os.stat(path).st_mtime_ns


def _darks() -> SequencePlan:
    return SequencePlan(name="d", targets=[Target(
        name="darks", ra_hours=0.0, dec_deg=0.0, calibration=True,
        center=False, autofocus_first=False,
        steps=[ExposureStep(filter="Dark", exposure_s=1, gain=100, count=1)])])


# ------------------------------------------------------------------- load

def test_load_refuses_a_file_with_no_status_and_says_why():
    """RED under M1, M2 and M4, observed verbatim (all three):

        E   Failed: DID NOT RAISE <class
        'astrodeck.sequence.session.SessionUnreadable'>

    RED under M3 and M6, where the file is refused but the reason is lost,
    observed verbatim (both):

        E   AssertionError: assert 'session file...eeeeeeeeeeeee' ==
        'session file...as no status)'
        E     Skipping 49 identical leading characters in diff, use -v to show
        E     - eeeeeeeeeee (it has no status)
        E     + eeeeeeeeeee
    """
    _write(ISSUE_218)
    with pytest.raises(SessionUnreadable) as info:
        session_store.load(SID)
    assert str(info.value) == (
        f"session file is unreadable: {SID} (it has no status)")
    assert (info.value.session_id, info.value.reason) == (
        SID, "it has no status")


# ------------------------------------------------------ scanning readers

READERS = {
    "load_all": lambda: [s.id for s in session_store.load_all()],
    # The SESSION rows only: its unreadable row is expected (#242), and is
    # pinned with its reason in test_unreadable_sessions_listed_and_deletable.
    "list": lambda: [r["id"] for r in session_store.list()
                     if r["status"] != "unreadable"],
    "active": lambda: session_store.active(),
    "armed": lambda: session_store.armed(),
    "recoverable": lambda: session_store.recoverable(),
    "newest_for_flow": lambda: session_store.newest_for_flow(
        FLOW, ("active", "dormant", "complete", "abandoned")),
    "current_for_flow": lambda: session_store.current_for_flow(FLOW),
}
NOTHING = {"load_all": [], "list": []}          # the rest answer None

#: Every reader on ``loaded``, and on #218's own file the readers that could
#: find it. #218's file has no origin, no ``auto_resume`` and no frame, so the
#: flow lookups, ``armed`` and ``recoverable`` skip it whatever its status
#: reads as. Those four pairs were run and stayed green under M1 to M7, i.e.
#: they could not fail, so they are not collected.
CASES = [(r, "loaded") for r in READERS] + [
    (r, "issue_218") for r in ("load_all", "list", "active")]


@pytest.mark.parametrize(("reader", "shape"), CASES,
                         ids=[f"{r}-{s}" for r, s in CASES])
def test_every_scanning_reader_skips_it(reader, shape):
    """Each reader, on the status-less file alone, finds nothing.

    ``load_all`` and ``list``: RED under M1, M2, M4 and M7, both shapes,
    observed verbatim (``list`` reads the same with its own name):

        E   AssertionError: load_all read a file with no status as a session:
        ['0218eeeeeeeeeeeeeeeeeeeeeeeeeeee']

    ``active``: RED under M1, both shapes; ``newest_for_flow`` and
    ``current_for_flow``: RED under M1. Observed verbatim:

        [active-issue_218] E   AssertionError: active read a file with no
        status as a session: Session(id='0218eeeeeeeeeeeeeeeeeeeeeeeeeeee',
        schema_version=1, name='hand-edited', created_ts=0.0, updated_ts=0.0,
        status='active', [...]
        [newest_for_flow-loaded] E   AssertionError: newest_for_flow read a
        file with no status as a session:
        Session(id='0218eeeeeeeeeeeeeeeeeeeeeeeeeeee', [...] status='active',
        [...] auto_resume=True, origin='flow',
        origin_id='ffffffffffffffffffffffffffffffff', crash_resumes=2) == None
        [current_for_flow-loaded] E   AssertionError: current_for_flow read
        a file with no status as a session: Session(id='0218eeee[...]',
        [...] status='active', [...]

    These three are green under M2, M4 and M7: their raw scans compare
    ``_stated_status`` and never reach the model's default, and only M1
    changes what that answers.

    ``armed`` and ``recoverable``: RED under M2, observed verbatim:

        [armed-loaded] E   AssertionError: armed read a file with no status
        as a session: Session(id='0218eeeeeeeeeeeeeeeeeeeeeeeeeeee',
        schema_version=1, name='hand-edited', created_ts=1.0, updated_ts=1.0,
        status='dormant', [...] auto_resume=True, [...]
        [recoverable-loaded] E   AssertionError: recoverable read a file with
        no status as a session: Session(id='0218eeee[...]', [...]
        status='dormant', [...]

    Green under M1, whose default is active and so neither armed nor
    recoverable: M1 reaches them through the sweep, in
    ``test_the_sweep_cannot_hand_it_to_resume_arm``.
    """
    _write(SHAPES[shape])
    got = READERS[reader]()
    assert got == NOTHING.get(reader), (
        f"{reader} read a file with no status as a session: {got!r}")


# ------------------------------------------------------------- boot sweep

@pytest.mark.parametrize("shape", SHAPES)
def test_boot_sweep_neither_counts_nor_rewrites_it(shape):
    """The sweep's whole premise is that ``active`` on disk means the process
    died mid-run. A file that states no status is not evidence of that, so it
    must not be counted, set dormant or written back.

    RED under M1, M4 and M7 with #218's numbers, observed verbatim
    (``issue_218``, identical under all three):

        E   AssertionError: observed {'count': 1, 'status': 'dormant',
        'crash_resumes': 1, 'extra_key kept': False, 'bytes unchanged': False,
        'mtime unchanged': False}

    and on ``loaded`` the false crash is the third, ResumeArm's give-up count:

        E   AssertionError: observed {'count': 1, 'status': 'dormant',
        'crash_resumes': 3, 'extra_key kept': False, 'bytes unchanged': False,
        'mtime unchanged': False}

    Green under M2, which reads the file as dormant, and the sweep takes only
    active sessions: M2's harm is the readers above and the chain below.
    """
    raw = SHAPES[shape]
    path = _write(raw)
    before = _fingerprint(path)
    n = session_store.boot_sweep()
    after = json.loads(path.read_text(encoding="utf-8"))
    observed = {
        "count": n,
        "status": after.get("status"),
        "crash_resumes": after.get("crash_resumes"),
        "extra_key kept": after.get("extra_key") == 1,
        "bytes unchanged": path.read_bytes() == before[0],
        "mtime unchanged": os.stat(path).st_mtime_ns == before[1],
    }
    expected = {
        "count": 0, "status": None,
        "crash_resumes": raw.get("crash_resumes"),
        "extra_key kept": True, "bytes unchanged": True,
        "mtime unchanged": True}
    assert observed == expected, f"observed {observed}"


def test_the_sweep_cannot_hand_it_to_resume_arm():
    """#218's consequence one step on: the sweep's rewrite makes the file a
    DORMANT session, and if it carried ``auto_resume`` and a frame, ResumeArm
    and the recover routes now find a session nobody started.

    RED under M1 and M7, observed verbatim (M1):

        E   AssertionError: after the boot sweep, armed() found a file that
        states no status: Session(id='0218eeeeeeeeeeeeeeeeeeeeeeeeeeee',
        schema_version=1, name='hand-edited', created_ts=1.0,
        updated_ts=1790294478.166941, status='dormant', [...]
        auto_resume=True, origin='flow',
        origin_id='ffffffffffffffffffffffffffffffff', crash_resumes=3)

    RED under M2 without the sweep's help: the same message with
    ``updated_ts=1.0`` and ``crash_resumes=2``.
    """
    _write(LOADED)
    session_store.boot_sweep()
    armed, recoverable = session_store.armed(), session_store.recoverable()
    assert armed is None, (
        f"after the boot sweep, armed() found a file that states no status: "
        f"{armed!r}")
    assert recoverable is None, (
        f"after the boot sweep, recoverable() found a file that states no "
        f"status: {recoverable!r}")


# ------------------------------------------------- engine.start singleton

class _StubHub:
    """Enough hub for SequenceEngine.start to attach a session; the run is
    aborted at once, so no device is touched (test_resume_after_restart)."""
    devices: dict = {}
    site: dict = {}
    looping = False

    def require(self, role):
        raise RuntimeError(f"no {role} in this test")

    def get(self, role, default=None):
        return default


async def test_engine_start_never_rewrites_it():
    """``engine.start`` disarms every other armed session and saves each one
    it touches. On a status-less file carrying ``auto_resume`` that save wrote
    the default-filled model over it, stamping a status into a file that never
    stated one.

    ``loaded`` is used because it carries ``auto_resume: true``: #218's own
    file is unarmed, so the loop would skip it under any status.

    RED under M1, M4 and M7, observed verbatim (M1; the dict goes on to
    ``'auto_resume': False`` and ``'crash_resumes': 2``, and has no
    ``extra_key``):

        E   AssertionError: engine.start rewrote a file that states no status:
        now {'id': '0218eeeeeeeeeeeeeeeeeeeeeeeeeeee', 'schema_version': 1,
        'name': 'hand-edited', 'created_ts': 1.0,
        'updated_ts': 1790294478.1884274, 'status': 'active', [...]

    RED under M2 with ``'status': 'dormant'`` in the same place.
    """
    from astrodeck.sequence.engine import SequenceEngine
    path = _write(LOADED)
    before = _fingerprint(path)
    eng = SequenceEngine(_StubHub())
    eng.start(_darks())
    try:
        assert _fingerprint(path) == before, (
            f"engine.start rewrote a file that states no status: now "
            f"{json.loads(path.read_text(encoding='utf-8'))!r}")
    finally:
        await eng.abort()


async def test_control_engine_start_disarms_a_file_that_states_its_status():
    """CONTROL for ``test_engine_start_never_rewrites_it``. The same harness,
    on ``LOADED`` with ``status: dormant``: an armed session that is not this
    run's, so the singleton loop disarms it and saves it. That proves the stub
    hub lets ``start`` reach the loop, so the test above is green because the
    loop skips the status-less file and not because the start stopped short
    of it. The loop sits in an ``except Exception: pass``, so a start that
    never reached it would say nothing.

    RED under M4 (the valid file is refused, so the loop never sees it),
    observed verbatim:

        E   AssertionError: engine.start did not disarm a file that states
        its status: ('dormant', True)
        E   assert ('dormant', True) == ('dormant', False)
    """
    from astrodeck.sequence.engine import SequenceEngine
    path = _write({**LOADED, "status": "dormant"})
    eng = SequenceEngine(_StubHub())
    eng.start(_darks())
    try:
        after = json.loads(path.read_text(encoding="utf-8"))
        got = (after.get("status"), after.get("auto_resume"))
        assert got == ("dormant", False), (
            f"engine.start did not disarm a file that states its status: "
            f"{got!r}")
    finally:
        await eng.abort()


# ---------------------------------------------------------------- controls

def test_control_a_file_that_states_its_status_loads_and_sweeps_as_before():
    """CONTROL. The same hand-written file with a status is a session: it
    loads, every reader finds it, and an ``active`` one is swept, counted
    and rewritten exactly as before. A dormant one is left alone.

    RED under M4, observed verbatim:

        E   astrodeck.sequence.session.SessionUnreadable: session file is
        unreadable: 0218eeeeeeeeeeeeeeeeeeeeeeeeeeee (it has no status)
    """
    path = _write({**ISSUE_218, "status": "active"})
    s = session_store.load(SID)
    assert (s.id, s.name, s.status) == (SID, "hand-edited", "active")
    assert [x.id for x in session_store.load_all()] == [SID]
    assert [r["id"] for r in session_store.list()] == [SID]
    assert session_store.active().id == SID

    assert session_store.boot_sweep() == 1
    after = json.loads(path.read_text(encoding="utf-8"))
    assert (after["status"], after["crash_resumes"]) == ("dormant", 1)
    assert "extra_key" not in after      # the model's save, as it always was

    dormant = "d" * 32
    dpath = _write({"id": dormant, "name": "left", "status": "dormant"},
                   dormant)
    dbefore = _fingerprint(dpath)
    assert session_store.boot_sweep() == 0
    assert _fingerprint(dpath) == dbefore


def test_control_loaded_with_a_status_is_armed_and_found_by_its_flow():
    """CONTROL. ``LOADED`` plus ``status: dormant`` is a real armed session
    with a frame: ``armed``, ``recoverable`` and the flow lookups all find it.
    Proves the skip tests above are not green because the fixture is unfit
    for those readers.

    RED under M4, observed verbatim:

        E   AssertionError: assert None == '0218eeeeeeeeeeeeeeeeeeeeeeeeeeee'
        E    +  where None = getattr(None, 'id', None)
        E    +    where None = armed()
    """
    _write({**LOADED, "status": "dormant"})
    assert getattr(session_store.armed(), "id", None) == SID
    assert getattr(session_store.recoverable(), "id", None) == SID
    assert getattr(session_store.newest_for_flow(FLOW, ("dormant",)),
                   "id", None) == SID
    assert getattr(session_store.current_for_flow(FLOW), "id", None) == SID


def test_control_a_session_built_in_code_keeps_its_default():
    """CONTROL. The refusal is about FILES. A ``Session`` built in code keeps
    its "active" default, and saving one writes the status out, so it loads.

    RED under M3, observed verbatim:

        E   pydantic_core._pydantic_core.ValidationError: 1 validation error
        for Session
        E   status
        E     Field required [type=missing, input_value={}, input_type=dict]

    RED under M2, observed verbatim:

        E   AssertionError: assert 'dormant' == 'active'

    RED under M4 (the saved file states its status, and is refused), observed
    verbatim (the id is the fresh session's own, so it differs every run):

        E   astrodeck.sequence.session.SessionUnreadable: session file is
        unreadable: b990b8662e7b49c088910488c9207814 (it has no status)
    """
    s = Session()
    assert s.status == "active"
    session_store.save(s)
    assert json.loads(session_store._path(s.id).read_text(
        encoding="utf-8"))["status"] == "active"
    assert session_store.load(s.id).status == "active"


def test_control_an_invalid_file_is_still_unreadable():
    """CONTROL. ``{"plan": "not a plan"}`` fails validation. It still raises
    ``SessionUnreadable`` from ``load`` with the validation error as its cause
    (it states no status either, and the damage is what gets reported), and
    ``load_all`` still skips it; ``list`` shows it as unreadable, never as a
    session (#242). The reason is the store's ``INVALID``: it was "" before
    H3, and pydantic's message stays on ``__cause__``.

    RED under M5, observed verbatim:

        E   pydantic_core._pydantic_core.ValidationError: 1 validation error
        for Session
        E   plan
        E     Input should be a valid dictionary or instance of SequencePlan
        [type=model_type, input_value='not a plan', input_type=str]

    RED under mutant "validation failure keeps the bare message" (H3, #242:
    ``_session_from_file`` raises ``SessionUnreadable(session_id)`` with no
    reason, as it did before), observed verbatim:

        E   AssertionError: assert '' == 'fails validation'
        E     - fails validation

    RED under M8, where the missing status is reported and the damage is
    not, observed verbatim:

        E   AssertionError: assert False
        E    +  where False = isinstance(None, <class
        'pydantic_core._pydantic_core.ValidationError'>)
        E    +    where None = SessionUnreadable('session file is unreadable:
        0218eeeeeeeeeeeeeeeeeeeeeeeeeeee (it has no status)').__cause__
    """
    from pydantic import ValidationError
    _write({"plan": "not a plan"})
    with pytest.raises(SessionUnreadable) as info:
        session_store.load(SID)
    assert isinstance(info.value.__cause__, ValidationError)
    assert info.value.reason == INVALID
    assert session_store.load_all() == []
    assert [(r["id"], r["status"], r["unreadable"])
            for r in session_store.list()] == [(SID, "unreadable", INVALID)]
