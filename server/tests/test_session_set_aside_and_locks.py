# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Session gains the night's set-aside records and the locked angles (#189
S2 T1, #208; spec 3.4, 5.1, 6.7, Revision 2 ruling 9).

``Session.set_aside`` makes "set aside for tonight" survive a crash: the
engine records each panel (or step) it set aside with the night key, and a
crash-resume the same night reads the records for ``events.night_key()`` and
does not retry those panels, while a later night ignores them. Nothing else
of the group's run state is persisted; a resume recomputes it from the ledger.

``Session.locked_angles`` holds ruling 9's lock: an unframed TARGET takes the
position angle its first imaging-camera solve measures, and from then on that
angle behaves exactly like a planned one, every acquisition, resume and night.
The FIRST lock wins. A lock that a later solve could overwrite is the data
half of ruling 9's mutant "lock re-read on every acquisition": the angle would
drift from night to night and the frames would not stack.

Both are additive with ``SESSION_SCHEMA`` still 1. ``Session`` has no
``extra="forbid"``, so a build that predates the fields loads a file carrying
them and ignores them, and this build loads a file without them.

Every test that guards a branch names the mutant it kills and quotes the
failure that mutant produced, observed by running it in a private copy of
``server/`` (so no other suite saw the mutant), from a byte-for-byte backup of
``session.py``, with the copy's sha256 compared against the backup afterwards.
Against ``session.py`` as it stood before this slice, every test here but the
schema pin was red: 14 failed, 1 passed, on ``AttributeError: 'Session'
object has no attribute 'set_aside'`` (and ``'note_set_aside'``,
``'lock_angle'``) and, for the dump, ``assert {} == {'locked_angl...et_aside':
[]}``.
"""
from __future__ import annotations

import math
import time

import pytest

import astrodeck.hub as hub_module
from astrodeck.events import night_key
from astrodeck.persist import write_json_atomic
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence.session import (SESSION_SCHEMA, Session,
                                        SessionStore)

#: The two keys this slice adds to a session file, at their empty defaults.
NEW_KEYS = {"set_aside": [], "locked_angles": {}}


def _plan() -> SequencePlan:
    return SequencePlan(name="mosaic", guide=False, dither_every=0,
                        meridian_flip=False,
                        targets=[Target(id="t1", name="M31 1-1", ra_hours=0.7,
                                        dec_deg=41.3, center=False,
                                        autofocus_first=False,
                                        steps=[ExposureStep(
                                            id="s1", filter="L",
                                            exposure_s=1.0, count=2)])])


def _nights() -> tuple[str, str]:
    """Last night's key and tonight's, from ``events.night_key``, the key a
    crash-resume asks with. Both taken at 22:00 local so the noon rollover and
    a DST change cannot put them on one date."""
    now = time.localtime()
    tonight_ts = time.mktime((now.tm_year, now.tm_mon, now.tm_mday,
                              22, 0, 0, 0, 0, -1))
    last_ts = time.mktime((now.tm_year, now.tm_mon, now.tm_mday - 1,
                           22, 0, 0, 0, 0, -1))
    last, tonight = night_key(last_ts), night_key(tonight_ts)
    assert last != tonight
    return last, tonight


@pytest.fixture
def store(tmp_path, monkeypatch) -> SessionStore:
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    return SessionStore()


# ---------------------------------------------------------- additive

def test_the_schema_stays_1():
    """Additive fields need no migration, and a bump would read as one: a
    build that refuses a newer schema would refuse every session this build
    saves.

    RED under mutant "bump SESSION_SCHEMA" (``SESSION_SCHEMA = 2``):

        E   assert (2, 2) == (1, 1)
    """
    assert (SESSION_SCHEMA, Session().schema_version) == (1, 1)


#: A session file exactly as a build before S2 wrote it, spelled out rather
#: than dumped from today's models, so nothing here asks the fields under test
#: to build it: no ``set_aside``, no ``locked_angles``, and a plan whose
#: target has no panel keys and which has no ``groups``.
PRE_S2_ID = "5e2b" * 8
PRE_S2_FILE = {
    "id": PRE_S2_ID, "schema_version": 1, "name": "before S2",
    "created_ts": 1.0, "updated_ts": 1.0, "status": "dormant",
    "plan": {"name": "mosaic", "guide": False, "dither_every": 0,
             "meridian_flip": False, "instructions": [],
             "targets": [{"id": "t1", "name": "M31 1-1", "ra_hours": 0.7,
                          "dec_deg": 41.3, "center": False,
                          "autofocus_first": False, "mosaic_group": None,
                          "center_tolerance_arcmin": None,
                          "center_attempts": None,
                          "autofocus_skip_if_fresh": False,
                          "steps": [{"id": "s1", "filter": "L",
                                     "exposure_s": 1.0, "count": 2}]}]},
    "nights": [], "frames": [], "auto_resume": False, "origin": "",
    "origin_id": "", "crash_resumes": 0,
}


def test_a_file_written_before_the_fields_loads(store):
    """A session saved by a build before this slice has neither key. It loads,
    through ``load`` and through ``load_all`` (the reader every scan uses, so
    a file it skips is one boot_sweep, recoverable and armed never see), and
    both fields read empty.

    RED under mutant "set_aside is required" (``set_aside: list[dict]`` with
    no default): the file is unreadable, so it would vanish from every scan:

        E   pydantic_core._pydantic_core.ValidationError: 1 validation error
            for Session
        E   set_aside
        E     Field required [type=missing, input_value={'id':
              '5e2b5e2b5e2b5e2b5... '', 'crash_resumes': 0}, input_type=dict]
        E   astrodeck.sequence.session.SessionUnreadable: session file is
            unreadable: 5e2b5e2b5e2b5e2b5e2b5e2b5e2b5e2b (fails validation)

    RED under mutant "locked_angles is required", with the same lines naming
    ``locked_angles``. (Both mutants also break every ``Session()`` in this
    file; this test is the one that shows the stored file refused.)
    """
    write_json_atomic(store._path(PRE_S2_ID), PRE_S2_FILE, backup=False)

    loaded = store.load(PRE_S2_ID)
    assert (loaded.set_aside, loaded.locked_angles) == ([], {})
    assert [x.id for x in store.load_all()] == [PRE_S2_ID]


def test_the_dump_gains_the_two_keys_and_nothing_else():
    """The session file grows by exactly the two keys at their empty defaults;
    every other top-level key and value is what it was.

    RED under mutant "set_aside defaults to a placeholder record" (``Field(
    default_factory=lambda: [{}])``):

        E   AssertionError: assert {'locked_angl..._aside': [{}]} ==
            {'locked_angl...et_aside': []}
        E     Omitting 1 identical items, use -vv to show
        E     Differing items:
        E     {'set_aside': [{}]} != {'set_aside': []}

    RED under mutant "set_aside is not persisted" (``exclude=True``):

        E   AssertionError: assert {'locked_angles': {}} ==
            {'locked_angl...et_aside': []}
        E     Omitting 1 identical items, use -vv to show
        E     Right contains 1 more item:
        E     {'set_aside': []}
    """
    s = Session(name="before S2", created_ts=1.0, status="dormant",
                plan=_plan())
    raw = s.model_dump(mode="json")
    old = {k: v for k, v in raw.items() if k not in NEW_KEYS}
    again = Session.model_validate(old).model_dump(mode="json")
    assert {k: again[k] for k in again if k not in old} == NEW_KEYS
    assert {k: again[k] for k in old} == old


# ---------------------------------------------------------- set aside

def test_a_set_aside_record_has_the_spec_shape():
    """``{target_id, step_id | None, reason, night}`` (3.4): a panel set aside
    whole has no step; the per-step reject guard's record names its step.

    RED under mutant "a whole panel's step_id written as ''" (``step_id or
    ""``, so a reader asking ``step_id is None`` for "the whole panel" never
    finds one):

        E   AssertionError: assert [{'night': '2...et_id': 't1'}] ==
            [{'night': '2...et_id': 't1'}]
        E     At index 0 diff: {'target_id': 't1', 'step_id': '', 'reason':
              'window closed', 'night': '2026-09-24'} != {'target_id': 't1',
              'step_id': None, 'reason': 'window closed', 'night':
              '2026-09-24'}
    """
    s = Session()
    s.note_set_aside("t1", "window closed", night="2026-09-24")
    s.note_set_aside("t1", "10 rejects in a row", night="2026-09-24",
                     step_id="s1")
    assert s.set_aside == [
        {"target_id": "t1", "step_id": None, "reason": "window closed",
         "night": "2026-09-24"},
        {"target_id": "t1", "step_id": "s1", "reason": "10 rejects in a row",
         "night": "2026-09-24"}]


def test_set_aside_records_accumulate():
    """ADDITIVE, not a slot: the second panel set aside tonight must not erase
    the first, or a crash-resume retries the first.

    RED under mutant "the writer replaces the list" (``self.set_aside =
    [record]``):

        E   AssertionError: assert ['t2'] == ['t1', 't2']
    """
    s = Session()
    s.note_set_aside("t1", "floor", night="2026-09-24")
    s.note_set_aside("t2", "guiding did not start", night="2026-09-24")
    assert [r["target_id"] for r in s.set_aside] == ["t1", "t2"]


def test_a_record_with_no_night_is_refused():
    """No night key is ever "", so a record without one would be read by no
    night at all: the set-aside would silently not survive the crash it is
    for. Said at the write, where the caller can still fix it.

    RED under mutant "accept an empty night" (the check removed):

        E   Failed: DID NOT RAISE <class 'ValueError'>
    """
    s = Session()
    with pytest.raises(ValueError, match="night"):
        s.note_set_aside("t1", "floor", night="")
    assert s.set_aside == []


def test_only_tonights_records_are_read(store):
    """The crash-resume half, through the store: the records are saved, the
    process dies, and the resumed run reads the file back and asks for
    tonight's. Last night's record is history, and that panel is retried.

    RED under mutant "the helper ignores night" (every record returned):

        E   AssertionError: assert ['t-last', 't-tonight'] == ['t-tonight']

    RED under mutant "set_aside is not persisted" (``Field(default_factory=
    list, exclude=True)``): the crash loses tonight's record too:

        E   AssertionError: assert [] == ['t-tonight']
    """
    last, tonight = _nights()
    s = Session(name="mosaic", created_ts=1.0, status="active", plan=_plan())
    s.note_set_aside("t-last", "window closed", night=last)
    s.note_set_aside("t-tonight", "floor", night=tonight)
    store.save(s)

    resumed = store.load(s.id)
    assert [r["target_id"] for r in resumed.set_aside_on(tonight)] == [
        "t-tonight"]


def test_control_a_night_with_no_records_reads_none():
    """CONTROL: tomorrow reads nothing, so every panel is retried."""
    last, tonight = _nights()
    s = Session()
    s.note_set_aside("t1", "floor", night=last)
    assert s.set_aside_on(tonight) == []


# ---------------------------------------------------------- locked angles

def test_the_first_lock_wins():
    """Ruling 9: the first solve's angle is the target's angle from then on. A
    later lock, a later night's first solve included, leaves it as it was, and
    the helper answers the angle IN FORCE, which is what the caller commands.

    RED under mutant "lock overwrites" (the ``if held is not None: return
    held`` removed; the data half of ruling 9's "lock re-read on every
    acquisition"):

        E   AssertionError: assert {'exposed_at'...ntring solve'} ==
            {'exposed_at'...ntring solve'}
        E     Omitting 1 identical items, use -vv to show
        E     Differing items:
        E     {'exposed_at': 190.0} != {'exposed_at': 90.0}
        E     {'pa_deg': 40.0} != {'pa_deg': 12.5}
        E     {'solved_at': 200.0} != {'solved_at': 100.0}
    """
    s = Session()
    first = s.lock_angle("t1", 12.5, solved_at=100.0, exposed_at=90.0,
                         source="centring solve")
    want = {"pa_deg": 12.5, "solved_at": 100.0, "exposed_at": 90.0,
            "source": "centring solve"}
    assert first == want
    again = s.lock_angle("t1", 40.0, solved_at=200.0, exposed_at=190.0,
                         source="centring solve")
    assert again == want
    assert s.locked_angle("t1") == want


def test_each_target_locks_its_own_angle():
    """Keyed by target id: a mosaic's panels, and the targets of one plan,
    each lock their own. A panel's lock goes with its id, so re-framing the
    block, which re-anchors and re-keys the ids (3.3, ruling 3), is what
    clears it, as ruling 9 says.

    RED under mutant "one lock per session" (the held record looked up as
    ``next(iter(self.locked_angles.values()), None)``): t2 is handed t1's
    angle and nothing is locked for it:

        E   assert [12.5, 12.5, None] == [12.5, 200.0, 200.0]
    """
    s = Session()
    s.lock_angle("t1", 12.5, solved_at=1.0, exposed_at=None, source="solve")
    given = s.lock_angle("t2", 200.0, solved_at=2.0, exposed_at=None,
                         source="solve")
    assert [(s.locked_angle("t1") or {}).get("pa_deg"), given["pa_deg"],
            (s.locked_angle("t2") or {}).get("pa_deg")] == [12.5, 200.0, 200.0]


def test_control_an_unlocked_target_reads_none():
    """CONTROL: no lock is None, never an angle of 0 (0 is a real PA).

    RED under mutant "an unlocked target reads angle 0" (the reader defaults
    to ``{"pa_deg": 0.0}``):

        E   AssertionError: assert {'pa_deg': 0.0} is None
    """
    s = Session()
    s.lock_angle("t1", 0.0, solved_at=1.0, exposed_at=None, source="solve")
    assert s.locked_angle("t2") is None
    assert s.locked_angle("t1")["pa_deg"] == 0.0


def test_a_lock_survives_the_store(store):
    """The resumed night reads the lock back from the file.

    RED under mutant "locked_angles is not persisted" (``Field(
    default_factory=dict, exclude=True)``):

        E   AssertionError: assert None == {'exposed_at': None, 'pa_deg':
            12.5, 'solved_at': 100.0, 'source': 'centring solve'}
    """
    s = Session(name="mosaic", created_ts=1.0, status="active", plan=_plan())
    s.lock_angle("t1", 12.5, solved_at=100.0, exposed_at=None,
                 source="centring solve")
    store.save(s)
    assert store.load(s.id).locked_angle("t1") == {
        "pa_deg": 12.5, "solved_at": 100.0, "exposed_at": None,
        "source": "centring solve"}


@pytest.mark.parametrize("angle", [math.nan, math.inf, -math.inf],
                         ids=["nan", "inf", "-inf"])
def test_a_lock_refuses_an_angle_that_is_not_a_number(angle):
    """A failed solve can hand back NaN for its rotation. Locked, it is the
    angle every later night commands, and the file carrying it cannot be
    served: ``JSONResponse`` renders with ``allow_nan=False``, so every
    route that returns the session answers 500. Refused at the lock, and
    nothing is written.

    RED under mutant "accept a non-finite angle" (the ``math.isfinite``
    check removed), on every parameter:

        E   Failed: DID NOT RAISE <class 'ValueError'>
    """
    s = Session()
    with pytest.raises(ValueError, match="angle"):
        s.lock_angle("t1", angle, solved_at=1.0, exposed_at=None,
                     source="solve")
    assert s.locked_angles == {}
