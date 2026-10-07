# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""What the ledger costs at thousands of frames, and the budget it is held to
(#189 spec section 10 risk 8 and Appendix A.3; #514, #515, #516).

RISK 8 SAID: "`accepted_by_step` walks every frame, and the session file is
rewritten on every frame. Counts are snapshotted once per pass. The cost at
thousands of frames per project is unmeasured; measure it in S7." A session
holds every frame a project has banked, on every night, and the engine
writes it from the event loop after each one, so the cost of a banked frame
grows with the ledger.

THE MEASUREMENT. The development box is an Intel Core Ultra 9 275HX (24
logical CPUs), running Windows 11 and Python 3.12.10. It was LOADED
throughout by other agents' suites: Windows reported 93% CPU when the
measurements began, and 91% to 93% around the third block below.

Direct: one call on a synthetic session whose frames have the shape a camera
with pixels writes: a capture path, a thumb path and four metrics. Median /
p95 over 21 to 41 calls, in ms. The third block is the reproduce command,
``python tests/test_s7_ledger_cost.py``, on the tree as submitted, with
`_rig_session`'s frames. The first two blocks came from a scratch script
whose capture path was seven characters longer, which is the whole
difference in bytes.

    frames  file     save_run_state           save         load       one walk
    before (json.dumps, and a full re-read in every save_run_state)
    1 000   575 KB     21.8 / 23.3    13.6 / 14.3    4.0 / 4.7  0.079 / 0.085
    3 000   1.68 MB    50.4 / 60.1    32.4 / 33.6  11.8 / 14.3  0.245 / 0.331
    10 000  5.55 MB  158.6 / 168.8  107.7 / 115.2  41.7 / 45.3  0.837 / 1.261
    after (pydantic_core, and no re-read after this process's write)
    1 000   575 KB       8.5 / 8.9      8.4 / 9.1    3.6 / 3.9  0.078 / 0.083
    3 000   1.68 MB    14.7 / 15.9    14.5 / 15.7  10.4 / 19.8  0.235 / 0.254
    10 000  5.55 MB    37.1 / 39.3    37.0 / 42.0  38.9 / 41.6  1.052 / 1.176
    the reproduce command, the tree as submitted
    1 000   568 KB       7.0 / 7.7      6.9 / 7.3    3.6 / 4.2  0.081 / 0.086
    3 000   1.66 MB    13.3 / 14.1    12.9 / 13.5  10.1 / 11.8  0.247 / 0.270
    10 000  5.48 MB    35.1 / 36.4    34.3 / 37.2  39.3 / 47.7  0.834 / 0.952

``load`` is what every ``save_run_state`` used to pay on top of its
``save``; it no longer does (the column is kept to show what it was). The
file is the same size before and after, because the text is the same byte
for byte. Before the change, the 10 000-frame save broke down, in medians,
as ``model_dump`` 6.7 ms, then ``json.dumps(indent=2)`` 84.5 ms, then the
private atomic write 14.3 ms. The re-read broke down as the read 8.6 ms,
``json.loads`` 13.3 ms and ``model_validate`` 12.2 ms.
``pydantic_core.to_json`` does the ``json.dumps`` part in 13.1 ms. A ledger
walk is unchanged by this task; what a frame pays for walks is their count
times the table's column.

The clocked night (``_group_harness``): a 2x2 mosaic, accepted counting, one
pass a visit, L and R, 250 each, so 2000 frames in 1000 visits. Per banked
frame, over frames 500 to 2000:

    before: 12.75 full walks (12.64 of them accepted_by_step), 1.0 save,
            1.0 full re-read, 387 bytes of file (Windows, CRLF lines);
            the night ran in 65.3 s of wall time
    after:  12.75 full walks, 1.0 save, 0 re-reads, 387 bytes; 33.9 s
    after #516 (every read through the engine's one memo, below):
            0.12 full walks, all of them the order snapshot's own scan of
            the last visits, once a pass of four visits; ``accepted_by_step``
            ran 3 times in the whole night (the memo's first build, and the
            two ``Session.owed()`` reads of the run's ending) against 25 268
            before; 1.0 save, 0 re-reads, 387 bytes

THE BUDGET, pinned below as deterministic assertions. Per banked frame:

* ONE save, the ledger write (``test_a_2000_frame_mosaic_night...``). A
  camera with pixels pays a second one today, from the thumbnail render
  (#515, an engine.py lever for S7-ENG-SAFE). The harness's frames carry no
  pixels, so it measures the ledger write alone.
* NO re-read of the session file after this process's own write (#514,
  fixed in ``session.py``).
* AT MOST ``NIGHT_WALKS_MAX`` full walks of the ledger on the night's plan
  shape, against 0.12 measured (12.75 before #516). Spec risk 8's "once per
  pass" held only for ``_order_snapshot``; the other 12.6 were the engine
  asking the session again at each check, and they all go through
  ``SequenceEngine._ledger_counts`` now: one memo, extended from the frames
  banked since it was last asked, handed out read-only. The walk left is
  ``_order_snapshot``'s own scan of the last visits, once a pass. The budget
  sits between that and the least a bypass adds: a reader that walks again
  is at least as costly as the order snapshot's scan (0.25 with it bypassed),
  and every other reader costs a walk or two a frame.
* AT MOST ``NIGHT_BYTES_PER_FRAME_MAX`` bytes of file per frame on the
  harness's frames (387 measured), and ``RIG_BYTES_PER_FRAME_MAX`` on a
  rig-shaped frame (548 to 568 measured, 10 000 to 1000 frames).
* The serialiser the save uses costs under ``SERIALISER_RATIO_MAX`` of a
  stdlib ``json.dumps(indent=2)`` of the same session (0.24 measured).

THE WALL-CLOCK LINE, recorded rather than asserted. At 10 000 frames, the
ledger work for one banked frame stays under 100 ms of event-loop time on
this box. The event loop also answers every UI request and socket, and 0.1 s
is the response time a person reads as instant. Before this change it was
158.6 ms for the write plus 12.75 x 0.837 ms of walks, about 170 ms, and
about 330 ms on a camera with pixels (two writes). That was out of budget,
and it is #514. After it, it is 35.1 ms plus 12.75 x 0.834 ms, about 46 ms,
or about 81 ms with the thumbnail's second write (#515). Since #516 the
walks are 0.12 x 0.834 ms, about 0.1 ms, so the write is nearly all of it:
about 35 ms, or about 70 ms with the thumbnail's second write. The Orange Pi
appliance has not been measured; #515 and #516 are the levers that widen the
margin on a slower host.

The only wall-clock bound asserted is a RATIO. The save's serialisation is
timed against stdlib ``json.dumps`` of the same dump, interleaved in one
thread, and the minimum of each is compared. Load slows both alike, which is
what lets the margin hold under ``-n auto`` on a loaded Windows box (the
#124 class). The threshold sits between the measured value and the old
serialiser's value by a factor of about two on each side.

THE SESSION MODEL is unchanged: ``SESSION_SCHEMA`` stays 1, no field was
added, and the file's text is what it was. Every named mutant was run in a
private copy of ``server/`` under the session scratchpad (``S7-LEDGER-mut``;
the verifier's "record made even when the write raised" in
``S7-LEDGER-verify-mut``), from byte backups, never in the shared tree
(#254). The failure each one produced is quoted, verbatim, where it went
red.
"""
from __future__ import annotations

import contextlib
import json
import math
import os
import statistics
import time
from pathlib import Path

import pytest

import astrodeck.hub as hub_module
from _group_harness import Night, grid_plan, group_hub, group_store  # noqa: F401
from astrodeck.persist import write_json_atomic
from astrodeck.sequence import session as session_mod
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target
from astrodeck.sequence.session import (Session, SessionFrame, SessionStore,
                                        session_store, session_text)

# ------------------------------------------------------------------ the budget

#: Full walks of ``Session.frames`` per banked frame on the night's plan
#: shape: 0.12 measured, 12.75 before the one memo (#516). The walk left is
#: `_order_snapshot`'s scan of the last visits, once a pass; one reader
#: bypassing the memo adds at least as much again (0.25 with that one
#: bypassed), so the budget sits between the two.
NIGHT_WALKS_MAX = 0.15
#: Bytes of session file per banked frame on the harness's frames: 387
#: measured on Windows, where each line ends CRLF (fewer on Linux).
NIGHT_BYTES_PER_FRAME_MAX = 400.0
#: Bytes of session file per frame on a rig-shaped frame (`_rig_session`),
#: counted as the whole file over its frames, so the plan is in it too.
RIG_BYTES_PER_FRAME_MAX = 600.0
#: The save's serialisation against stdlib ``json.dumps(indent=2)`` of the
#: same session, minimum against minimum.
SERIALISER_RATIO_MAX = 0.5

#: The night: a 2x2 mosaic of L and R, ``COUNT`` of each, one pass a visit.
COUNT = 250
BANKED = 2 * 2 * 2 * COUNT
#: The steady part of the night the per-frame numbers are taken over: from
#: this frame to the last. The first hundreds are left out so that the
#: start's one-off saves and walks are not spread over the frames.
WINDOW_FROM = 500
#: Real seconds the night may take before the harness calls it hung. It took
#: 34 s to 44 s on the development box under load; xdist on a busier box can
#: take a few times that.
NIGHT_WALL_S = 900.0

#: Sizes of the direct measurements (the task's 1k, 3k and 10k).
SIZES = (1000, 3000, 10000)
#: Interleaved timings in the serialiser ratio; the minimum of each is used.
RATIO_REPS = 7

FILTERS = ("L", "R", "G", "B", "Ha", "Oiii", "Sii")
NIGHT_KEY = "m31-mosaic-20260902-214809"


# ------------------------------------------------------------------ builders

def _rig_plan(frames: int) -> SequencePlan:
    """A 3x2 mosaic of seven filters, with enough counted to owe ``frames``."""
    per = frames // (6 * len(FILTERS)) + 1
    targets = [Target(id=f"p{r}{c}", name=f"M31 {r + 1}-{c + 1}",
                      ra_hours=0.7, dec_deg=41.0, mosaic_group="g",
                      panel_row=r, panel_col=c,
                      steps=[ExposureStep(id=f"p{r}{c}-{f}", filter=f,
                                          exposure_s=60.0, count=per)
                             for f in FILTERS])
               for r in range(2) for c in range(3)]
    return SequencePlan(name="M31 mosaic", count_mode="accepted",
                        targets=targets)


def _rig_frame(plan: SequencePlan, i: int) -> SessionFrame:
    """Frame ``i`` as the engine writes it on a camera with pixels: the
    saved FITS path, the thumb the render stamps, and all four metrics
    (`_record_session_frame`). Every seventeenth is rejected."""
    steps = [(t.id, s.id, s.filter) for t in plan.targets for s in t.steps]
    tid, sid, filt = steps[i % len(steps)]
    return SessionFrame(
        ts=1788313689.0 + 70.0 * i, night=NIGHT_KEY, target_id=tid,
        step_id=sid,
        path=(r"C:\AstroDeck\captures\2026-09-02\M31 mosaic"
              rf"\M31 mosaic_{tid}_{filt}_60s_{i:05d}.fits"),
        thumb=f"thumbs/{i:032x}.jpg",
        metrics={"hfr": 2.13, "stars": 412.0, "guide_rms": 0.61,
                 "sensor_temp_c": -10.0},
        auto_accepted=(i % 17 != 0))


def _rig_session(frames: int) -> Session:
    plan = _rig_plan(frames)
    s = Session(name="M31 mosaic", created_ts=1.0, status="active", plan=plan,
                nights=[NIGHT_KEY], auto_resume=True)
    s.frames.extend(_rig_frame(plan, i) for i in range(frames))
    return s


#: The store methods the counters below wrap on the class.
_WRAPPED = ("save", "load", "save_run_state")


def _unshadow(mp) -> None:
    """Take off the singleton ``session_store`` any of ``_WRAPPED`` a test
    elsewhere left in its ``__dict__``, for the length of ``mp``, which puts
    them back as found (#522).

    ``monkeypatch.setattr(session_store, "save", ...)`` reads the old value
    with ``getattr``, the BOUND method, and its undo puts that into the
    instance's ``__dict__``, where there was nothing. It shadows the class
    for the rest of the worker, so a counter on ``SessionStore.save`` counts
    nothing the singleton does. The full suite ran
    ``test_resume_crash_loop.py`` before this file on one worker, and every
    save count here read 0 (observed, verbatim):
        AssertionError: one ledger write at 1000 frames made 0 saves and 0
        reads of the file

    MUTANT "no unshadow" (this body replaced by ``pass``), run after
    ``test_resume_crash_loop.py`` in one process: the premise checks in
    ``_counting`` and ``_LedgerMeter`` now say so first. RED (observed):
        AssertionError: premise: session_store.save is the counted method
        assert <function SessionStore.save at 0x0000014A87AD7240> is
        <function _counting.<locals>.wrapper at 0x0000014A8679B560>"""
    for name in _WRAPPED:
        if name in vars(session_store):
            mp.delattr(session_store, name)


@contextlib.contextmanager
def _counting(*names: str):
    """Count every call of each ``SessionStore`` method in ``names`` for the
    length of the block, then put the methods back. A monkeypatch of its own,
    because the test's carries ``store_dir``'s directory and must not be
    undone early: a read after an undo would go to the real captures. The
    singleton is unshadowed first, and the wrappers are checked to be what
    it calls, so a count of 0 is a count and not a counter nobody reaches."""
    calls = {name: 0 for name in names}
    with pytest.MonkeyPatch.context() as mp:
        _unshadow(mp)
        for name in names:
            def wrapper(*a, _real=getattr(SessionStore, name), _name=name,
                        **kw):
                calls[_name] += 1
                return _real(*a, **kw)
            mp.setattr(SessionStore, name, wrapper)
            assert getattr(session_store, name).__func__ is wrapper, (
                f"premise: session_store.{name} is the counted method")
        yield calls


@pytest.fixture
def store_dir(tmp_path, monkeypatch) -> Path:
    """The store writes under a directory of the test's own."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    return tmp_path / "captures" / "sessions"


def _timed(fn, reps: int) -> tuple[float, float]:
    """(median, p95) of ``reps`` calls of ``fn``, in seconds."""
    out = []
    for _ in range(reps):
        t = time.perf_counter()
        fn()
        out.append(time.perf_counter() - t)
    out.sort()
    return (statistics.median(out),
            out[min(len(out) - 1, round(0.95 * (len(out) - 1)))])


# ------------------------------------------------------------- the night

class _LedgerMeter:
    """Counts what the ledger costs a clocked night, save by save.

    * ``walks``: full walks of ``Session.frames``, of any kind, by anyone.
      The run's session gets a list that counts its own ``iter``, swapped in
      as ``start`` returns and before the run's first step, so a walk through
      ``accepted_by_step`` and the engine's own loop in ``_order_snapshot``
      count alike. pydantic's dump reads the list without ``iter``.
    * ``calls``: ``accepted_by_step``, ``recorded_by_step``, ``owed`` and
      ``total_accepted``, by name, for the report.
    * ``saves`` and ``loads``: ``SessionStore.save`` (which the per-frame
      ``save_run_state`` goes through) and ``SessionStore.load`` (its
      re-read).
    * ``rows``: at each save, ``(frames, file bytes, walks, saves, loads,
      seconds the save took)``, so any stretch of the night can be divided
      by the frames banked in it."""

    def __init__(self, monkeypatch):
        self.walks = 0
        self.saves = 0
        self.loads = 0
        self.calls: dict[str, int] = {}
        self.rows: list[tuple[int, int, int, int, int, float]] = []
        meter = self

        class CountedFrames(list):
            def __iter__(self):
                meter.walks += 1
                return super().__iter__()

        self.CountedFrames = CountedFrames
        for name in ("accepted_by_step", "recorded_by_step", "owed",
                     "total_accepted"):
            self._count_calls(monkeypatch, name)
        real_save, real_load = SessionStore.save, SessionStore.load

        def save(store, session):
            meter.saves += 1
            t = time.perf_counter()
            real_save(store, session)
            took = time.perf_counter() - t
            size = store._path(session.id).stat().st_size
            meter.rows.append((len(session.frames), size, meter.walks,
                               meter.saves, meter.loads, took))

        def load(store, session_id):
            meter.loads += 1
            return real_load(store, session_id)

        _unshadow(monkeypatch)
        monkeypatch.setattr(SessionStore, "save", save)
        monkeypatch.setattr(SessionStore, "load", load)
        for name, counted in (("save", save), ("load", load)):
            assert getattr(session_store, name).__func__ is counted, (
                f"premise: session_store.{name} is the counted method")

    def _count_calls(self, monkeypatch, name: str) -> None:
        real = getattr(Session, name)
        meter = self

        def wrapper(session, *a, **kw):
            meter.calls[name] = meter.calls.get(name, 0) + 1
            return real(session, *a, **kw)

        monkeypatch.setattr(Session, name, wrapper)

    def watch(self, engine) -> None:
        real_start = engine.start
        meter = self

        def start(*a, **kw):
            out = real_start(*a, **kw)
            engine._session.frames = meter.CountedFrames(engine._session.frames)
            return out

        engine.start = start

    def per_frame(self, since: int) -> dict:
        """Per banked frame, from the save of frame ``since`` to the save of
        the last frame (its own save, not the finalize's after it)."""
        first = next(r for r in self.rows if r[0] >= since)
        last_n = max(r[0] for r in self.rows)
        last = next(r for r in self.rows if r[0] == last_n)
        n = last[0] - first[0]
        took = [r[5] for r in self.rows if r[0] >= last_n - 200]
        return {
            "frames": last_n, "window": [first[0], last[0]],
            "walks": (last[2] - first[2]) / n,
            "saves": (last[3] - first[3]) / n,
            "loads": (last[4] - first[4]) / n,
            "bytes": (last[1] - first[1]) / n,
            "file_bytes": last[1],
            "save_ms_median_last_200": round(statistics.median(took) * 1e3, 2),
            "calls": dict(self.calls),
        }


@pytest.mark.slow
async def test_a_2000_frame_mosaic_night_keeps_the_ledger_budget(
        group_hub, monkeypatch):
    """A clocked night banks 2000 frames on a 2x2 mosaic and pays, per
    banked frame: one save, no re-read, at most ``NIGHT_WALKS_MAX`` full
    walks of the ledger, and at most ``NIGHT_BYTES_PER_FRAME_MAX`` bytes of
    file.

    THE WALK BUDGET since #516 (0.15, against 0.12 measured and 12.75 before
    the one memo, `SequenceEngine._ledger_counts`). The next three mutants
    were run on that code, each from a byte backup, restored and
    sha256-checked after, the one test run alone:

    MUTANT "no memo" (`_ledger_counts` returns ``MappingProxyType(s.
    accepted_by_step())``, a fresh walk at every ask). RED (observed):
        AssertionError: a banked frame walks the ledger 13.37 times, against
        a budget of 0.15: {'frames': 2000, 'window': [500, 2000], 'walks':
        13.374, 'saves': 1.0, 'loads': 0.0, 'bytes': 387.0013333333333,
        'file_bytes': 782861, 'save_ms_median_last_200': 26.03, 'calls':
        {'accepted_by_step': 26518, 'owed': 2}}
        assert 13.374 <= 0.15

    MUTANT "`_quota_met` bypasses the memo" (``self._session.accepted(
    step.id)`` in place of ``self._ledger_counts().get(step.id, 0)``: one
    reader, asked twice a frame). RED (observed):
        AssertionError: a banked frame walks the ledger 3.12 times, against
        a budget of 0.15: {'frames': 2000, 'window': [500, 2000], 'walks':
        3.1246666666666667, 'saves': 1.0, 'loads': 0.0, 'bytes': 387.0,
        'file_bytes': 782860, 'save_ms_median_last_200': 26.29, 'calls':
        {'accepted_by_step': 6003, 'owed': 2}}
        assert 3.1246666666666667 <= 0.15

    MUTANT "`_order_snapshot` bypasses the memo" (its map taken as
    ``self._session.accepted_by_step()``, once a pass: the cheapest reader to
    bring back, which is why the budget sits under the 0.25 it makes). RED
    (observed):
        AssertionError: a banked frame walks the ledger 0.25 times, against
        a budget of 0.15: {'frames': 2000, 'window': [500, 2000], 'walks':
        0.24933333333333332, 'saves': 1.0, 'loads': 0.0, 'bytes': 387.0,
        'file_bytes': 782861, 'save_ms_median_last_200': 31.33, 'calls':
        {'accepted_by_step': 253, 'owed': 2}}
        assert 0.24933333333333332 <= 0.15

    THE MUTANTS BELOW were observed in S7, when the walk count was 12.75 and
    the budget 13 (a 'walks' of 12.749 and an ``assert ... <= 13.0`` in
    their quotes), and are kept as the record of what each one does:

    MUTANT "a second save per frame" (``save_run_state`` calls
    ``self.save(session)`` twice). RED (observed):
        AssertionError: a banked frame costs 2.0 session saves, against a
        budget of one: {'frames': 2000, 'window': [500, 2000], 'walks':
        12.749333333333333, 'saves': 2.0, 'loads': 0.0, 'bytes':
        387.0013333333333, 'file_bytes': 782694, 'save_ms_median_last_200':
        17.3, 'calls': {'accepted_by_step': 25267, 'total_accepted': 5001,
        'owed': 2}}
        assert 2.0 == 1.0

    MUTANT "the re-read every frame" (``save_run_state`` without the stamp
    check: it loads the file before every write, the code before #514).
    RED (observed):
        AssertionError: a banked frame re-reads the session file 1.0 times,
        against a budget of none: {'frames': 2000, 'window': [500, 2000],
        'walks': 12.749333333333333, 'saves': 1.0, 'loads': 1.0, 'bytes':
        387.0006666666667, 'file_bytes': 782694, 'save_ms_median_last_200':
        13.59, 'calls': {'accepted_by_step': 25267, 'total_accepted': 5001,
        'owed': 2}}
        assert 1.0 == 0.0

    MUTANT "accepted_by_step for every step each frame" (engine.py:
    ``_set_state``'s ``session`` sub-state sums ``self._session.accepted(
    s.id)`` over every step of the plan instead of one ``total_accepted()``:
    the same number, a walk per step at every published state). RED
    (observed):
        AssertionError: a banked frame walks the ledger 30.25 times, against
        a budget of 13: {'frames': 2000, 'window': [500, 2000], 'walks':
        30.249333333333333, 'saves': 1.0, 'loads': 0.0, 'bytes': 387.0,
        'file_bytes': 782694, 'save_ms_median_last_200': 14.84, 'calls':
        {'accepted_by_step': 60274, 'owed': 2}}
        assert 30.249333333333333 <= 13.0

    MUTANT "indent 4" (``session_text`` writes ``indent=4``). RED
    (observed):
        AssertionError: a banked frame adds 485.0 bytes to the session file,
        against a budget of 400: {'frames': 2000, 'window': [500, 2000],
        'walks': 12.749333333333333, 'saves': 1.0, 'loads': 0.0, 'bytes':
        485.0, 'file_bytes': 981434, 'save_ms_median_last_200': 13.95,
        'calls': {'accepted_by_step': 25267, 'total_accepted': 5001,
        'owed': 2}}
        assert 485.0 <= 400.0
    """
    meter = _LedgerMeter(monkeypatch)
    plan = grid_plan(2, 2, panel_kw={"count": COUNT, "per_visit": 1,
                                     "exposure_s": 2.0},
                     count_mode="accepted")
    night = Night(group_hub, monkeypatch)
    meter.watch(night.engine)
    try:
        done = await night.run(plan, wall_s=NIGHT_WALL_S)
    finally:
        await night.close()
    assert done, f"the night did not end within {NIGHT_WALL_S:g} s"
    assert night.states[-1].get("state") == "complete", night.states[-1]
    got = meter.per_frame(WINDOW_FROM)
    assert got["frames"] == BANKED, got
    assert len(night.captures) == BANKED, (
        f"premise: every exposure was banked ({len(night.captures)} shot)")
    assert got["saves"] == 1.0, (
        f"a banked frame costs {got['saves']} session saves, against a "
        f"budget of one: {got}")
    assert got["walks"] > 0, (
        f"premise: the meter sees the ledger's walks (none counted): {got}")
    assert got["loads"] == 0.0, (
        f"a banked frame re-reads the session file {got['loads']} times, "
        f"against a budget of none: {got}")
    assert got["walks"] <= NIGHT_WALKS_MAX, (
        f"a banked frame walks the ledger {got['walks']:.2f} times, against "
        f"a budget of {NIGHT_WALKS_MAX:g}: {got}")
    assert got["bytes"] <= NIGHT_BYTES_PER_FRAME_MAX, (
        f"a banked frame adds {got['bytes']:.1f} bytes to the session file, "
        f"against a budget of {NIGHT_BYTES_PER_FRAME_MAX:g}: {got}")
    back = session_store.load(night.session_id)
    assert len(back.frames) == BANKED, (
        "the file does not hold the night's frames, so the saves counted are "
        "not the ledger's")


# -------------------------------------------------- one save, 1k to 10k

@pytest.mark.parametrize("frames", SIZES)
def test_one_ledger_write_at_n_frames_is_one_save_no_read_and_the_bytes(
        frames, store_dir):
    """The per-frame write (``save_run_state``) on a session of 1000, 3000
    and 10 000 rig-shaped frames: one save, no read of the file, and at most
    ``RIG_BYTES_PER_FRAME_MAX`` bytes of file a frame. The file then holds
    every frame. The first save is the one ``engine.start`` makes, so the
    write under test is an ordinary frame's.

    MUTANT "the re-read every frame". RED (observed):
        AssertionError: one ledger write at 1000 frames made 1 saves and 1
        reads of the file
        assert (1, 1) == (1, 0)
    and the same at 3000 and 10 000 frames.

    MUTANT "indent 4". RED (observed):
        AssertionError: 682372 bytes for 1000 frames is 682.4 a frame, against
        a budget of 600
        assert (682372 / 1000) <= 600.0
    and at 3000 frames 662.0 a frame, at 10 000 654.8.
    """
    s = _rig_session(frames - 1)
    session_store.save(s)
    with _counting("save", "load") as calls:
        s.frames.append(_rig_frame(s.plan, frames - 1))
        session_store.save_run_state(s)
    assert (calls["save"], calls["load"]) == (1, 0), (
        f"one ledger write at {frames} frames made {calls['save']} saves and "
        f"{calls['load']} reads of the file")
    size = session_store._path(s.id).stat().st_size
    assert size / frames <= RIG_BYTES_PER_FRAME_MAX, (
        f"{size} bytes for {frames} frames is {size / frames:.1f} a frame, "
        f"against a budget of {RIG_BYTES_PER_FRAME_MAX:g}")
    assert len(session_store.load(s.id).frames) == frames


def test_the_save_serialises_for_under_half_of_stdlib_json(store_dir,
                                                          monkeypatch):
    """At 10 000 frames the save's serialisation costs under
    ``SERIALISER_RATIO_MAX`` of stdlib ``json.dumps(indent=2)`` over the same
    session (#514): the minimum of ``RATIO_REPS`` timed ``save`` calls,
    against the minimum of as many ``model_dump`` plus ``json.dumps``,
    interleaved, with the disk write stubbed so only the serialisation is
    timed. The text the save hands the writer is ``session_text``'s, and it
    is ``json.dumps``'s byte for byte, so the file's format has not moved.

    MUTANT "json.dumps serialiser" (``session_text`` returns
    ``json.dumps(session.model_dump(), indent=2, ensure_ascii=False)``, the
    serialiser before #514). RED (observed):
        AssertionError: a save's serialisation took 84.0 ms against 83.5 ms
        for stdlib json.dumps (ratio 1.01, budget 0.5)
        assert 1.0055724704402664 < 0.5

    MUTANT "save writes through write_json_atomic" (``save`` calls
    ``write_json_atomic(path, session.model_dump(), backup=False)``, the
    write before #514, and never ``session_text``). RED (observed):
        AssertionError: the save handed the writer 0 texts in 7 saves
        assert 0 == 7
    """
    s = _rig_session(10000)
    session_store.save(s)      # the file exists: every timed save is an upsert
    written: list[str] = []
    monkeypatch.setattr(session_mod, "write_private_text_atomic",
                        lambda path, text: written.append(text))
    ours, stdlib = [], []
    for _ in range(RATIO_REPS):
        t = time.perf_counter()
        session_store.save(s)
        ours.append(time.perf_counter() - t)
        t = time.perf_counter()
        reference = json.dumps(s.model_dump(), indent=2, ensure_ascii=False)
        stdlib.append(time.perf_counter() - t)
    assert len(written) == RATIO_REPS, (
        f"the save handed the writer {len(written)} texts in {RATIO_REPS} "
        f"saves")
    reference = json.dumps(s.model_dump(), indent=2, ensure_ascii=False)
    assert written[-1] == session_text(s) == reference, (
        "the save did not write session_text's text, or that text is no "
        "longer json.dumps(indent=2)'s")
    ratio = min(ours) / min(stdlib)
    assert ratio < SERIALISER_RATIO_MAX, (
        f"a save's serialisation took {min(ours) * 1e3:.1f} ms against "
        f"{min(stdlib) * 1e3:.1f} ms for stdlib json.dumps (ratio "
        f"{ratio:.2f}, budget {SERIALISER_RATIO_MAX:g})")


def test_a_nan_metric_is_written_as_nan_and_read_back(store_dir):
    """A NaN or infinite metric (a failed HFR fit) is written as ``NaN`` /
    ``Infinity``, as ``json.dumps`` wrote it, and the file still loads with
    the value in place.

    MUTANT "model_dump_json serialiser" (``session_text`` returns
    ``session.model_dump_json(indent=2)``, which writes NaN as null).
    RED (observed):
        AssertionError: the non-finite metrics were not written as the JSON
        constants
    (the file said ``"hfr": null``; read back, it fails validation, which a
    probe confirmed: ``SessionUnreadable: ... (fails validation)``).
    """
    s = _rig_session(3)
    s.frames[1].metrics["hfr"] = math.nan
    s.frames[2].metrics["guide_rms"] = math.inf
    session_store.save(s)
    text = session_store._path(s.id).read_text(encoding="utf-8")
    assert '"hfr": NaN' in text and '"guide_rms": Infinity' in text, (
        "the non-finite metrics were not written as the JSON constants")
    back = session_store.load(s.id)
    assert math.isnan(back.frames[1].metrics["hfr"])
    assert back.frames[2].metrics["guide_rms"] == math.inf


# ---------------------------------------- the re-read the write skips (#514)

def _frames_of(n: int) -> Session:
    s = _rig_session(n)
    session_store.save(s)
    return s


def test_the_ledger_write_reads_nothing_after_this_processes_own_write(
        store_dir):
    """Three ledger writes in a row read the file none of the times. A read
    by somebody else in between (``load``, whose ``read_json`` re-hardens
    the file) does not move its stamp.

    MUTANT "the re-read every frame". RED (observed):
        AssertionError: 3 reads of the file in three ledger writes
        assert 3 == 0
    """
    s = _frames_of(50)
    session_store.load(s.id)
    with _counting("load") as calls:
        for i in range(3):
            s.frames.append(_rig_frame(s.plan, 50 + i))
            session_store.save_run_state(s)
    assert calls["load"] == 0, (
        f"{calls['load']} reads of the file in three ledger writes")
    assert [f.id for f in session_store.load(s.id).frames] == [
        f.id for f in s.frames]


def test_the_operators_write_in_this_process_wins_without_a_read(store_dir):
    """The PATCH that disarms a live session saves a fresh copy with
    ``auto_resume`` False. The engine's next ledger write, from its own
    stale copy that still says True, keeps the operator's False on disk and
    on its copy, and reads nothing to do it: the record of the PATCH's write
    carries the value. (``test_abort_stays_aborted`` pins the value through a
    read; this pins it with none.)

    MUTANT "a stamp match keeps the engine's copy" (``save_run_state`` skips
    the re-read on a match but applies nothing from the record). RED
    (observed):
        AssertionError: the engine's copy stayed stale
        assert True is False
    and ``test_abort_stays_aborted``'s primitive went red with it:
        AssertionError: the run clobbered an operator-owned field
    """
    engine_copy = _frames_of(5)
    operator = session_store.load(engine_copy.id)
    operator.auto_resume = False
    session_store.save(operator)
    with _counting("load") as calls:
        engine_copy.frames.append(_rig_frame(engine_copy.plan, 5))
        session_store.save_run_state(engine_copy)
    assert calls["load"] == 0, (
        "the write read the file to learn the operator's edit")
    assert engine_copy.auto_resume is False, "the engine's copy stayed stale"
    back = session_store.load(engine_copy.id)
    assert (back.auto_resume, len(back.frames)) == (False, 6), (
        "the ledger write undid the operator's disarm, or lost the frame")


def test_a_save_that_raised_leaves_the_record_of_the_last_write(
        store_dir, monkeypatch):
    """The operator's disarm (``auto_resume`` False) raises in the write,
    before its replace: the route answers an error and the file still says
    True. The engine's next ledger write keeps True, the file's value, which
    is what the re-read before #514 kept, and reads nothing to do it: a save
    that raised records nothing, so the record of the last write that
    returned still describes the file. A record made regardless would hand
    the run a value the file never held, and the operator's failed edit would
    take effect a frame later, unannounced.

    MUTANT "the record is made even when the write raised" (``save`` wraps
    the write in ``try``/``finally`` and records the stamp and the values in
    the ``finally``). RED (observed):
        AssertionError: the run took a value the file never held
        assert False is True
    """
    engine_copy = _frames_of(5)
    operator = session_store.load(engine_copy.id)
    operator.auto_resume = False

    def refused(path, text):
        raise OSError("the disk refused the write")

    with monkeypatch.context() as mp:
        mp.setattr(session_mod, "write_private_text_atomic", refused)
        with pytest.raises(OSError):
            session_store.save(operator)
    assert session_store.load(engine_copy.id).auto_resume is True, (
        "premise: the failed save left the file as it was")
    with _counting("load") as calls:
        engine_copy.frames.append(_rig_frame(engine_copy.plan, 5))
        session_store.save_run_state(engine_copy)
    assert engine_copy.auto_resume is True, (
        "the run took a value the file never held")
    assert calls["load"] == 0, (
        f"{calls['load']} reads: the record of the last write was dropped")
    back = session_store.load(engine_copy.id)
    assert (back.auto_resume, len(back.frames)) == (True, 6)


def test_a_write_from_outside_the_process_is_read(store_dir):
    """Another writer replaces the file (a restore, a second process, an
    editor that saves by rename) with ``auto_resume`` False. The engine's
    next ledger write reads the file and keeps False: the replace gave the
    file a new stamp.

    MUTANT "a record is trusted without its stamp" (``save_run_state``
    takes the record whenever one exists). RED (observed):
        AssertionError: 0 reads after an outside write
        assert 0 == 1
    """
    engine_copy = _frames_of(5)
    path = session_store._path(engine_copy.id)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["auto_resume"] = False
    write_json_atomic(path, raw, backup=False)
    with _counting("load") as calls:
        session_store.save_run_state(engine_copy)
    assert calls["load"] == 1, f"{calls['load']} reads after an outside write"
    assert engine_copy.auto_resume is False
    assert session_store.load(engine_copy.id).auto_resume is False


def test_an_edit_in_place_of_the_same_size_is_read(store_dir):
    """A hand edit made in place, keeping the file's inode and its size
    (``auto_resume`` true to false, and one letter off the name), is read,
    and the edit is kept. The mtime is set two seconds on, standing for an
    edit made after the write, as a person's is. Only the mtime tells this
    file from the one the store wrote.

    MUTANT "the stamp is the file size alone". RED (observed):
        AssertionError: 0 reads after an edit in place
        assert 0 == 1

    MUTANT "the stamp is the inode alone". RED (observed):
        AssertionError: 0 reads after an edit in place
        assert 0 == 1
    """
    engine_copy = _frames_of(5)
    path = session_store._path(engine_copy.id)
    before = path.stat()
    raw = path.read_bytes()
    edited = (raw.replace(b'"auto_resume": true', b'"auto_resume": false', 1)
              .replace(b'"name": "M31 mosaic"', b'"name": "M31 mosai"', 1))
    assert edited != raw and len(edited) == len(raw), "premise: same size"
    with open(path, "r+b") as f:
        f.write(edited)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 2_000_000_000))
    after = path.stat()
    assert (after.st_ino, after.st_size) == (before.st_ino, before.st_size), (
        "premise: the edit kept the inode and the size")
    with _counting("load") as calls:
        session_store.save_run_state(engine_copy)
    assert calls["load"] == 1, f"{calls['load']} reads after an edit in place"
    assert engine_copy.auto_resume is False
    assert session_store.load(engine_copy.id).auto_resume is False


# ------------------------------------------------------- the reproduce command

def measure(sizes=SIZES) -> list[str]:
    """The direct table in the module docstring: median / p95 of one
    ``save_run_state``, ``save``, ``load`` and ``accepted_by_step`` at each
    size, on rig-shaped frames, in a temporary directory."""
    import platform
    import tempfile
    lines = [f"{platform.processor()} ({os.cpu_count()} logical CPUs), "
             f"{platform.platform()}, Python {platform.python_version()}"]
    real_dir = hub_module.CAPTURE_DIR
    with tempfile.TemporaryDirectory() as td:
        hub_module.CAPTURE_DIR = Path(td) / "captures"
        try:
            for n in sizes:
                s = _rig_session(n)
                session_store.save(s)
                size = session_store._path(s.id).stat().st_size
                reps = 41 if n <= 3000 else 21
                srs = _timed(lambda: session_store.save_run_state(s), reps)
                save = _timed(lambda: session_store.save(s), reps)
                load = _timed(lambda: session_store.load(s.id), reps)
                walk = _timed(s.accepted_by_step, reps * 5)
                lines.append(
                    f"{n:>6}  {size / 1e6:5.2f} MB ({size / n:.0f} B/frame)  "
                    + "  ".join(f"{name} {m * 1e3:.3g} / {p * 1e3:.3g}"
                                for name, (m, p) in (
                                    ("save_run_state", srs), ("save", save),
                                    ("load", load), ("walk", walk))))
        finally:
            hub_module.CAPTURE_DIR = real_dir
    return lines


if __name__ == "__main__":
    for line in measure():
        print(line)
