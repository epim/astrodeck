# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Every accepted-count read of the ledger goes through one memo (#516; #189
spec section 10 risk 8: "counts are snapshotted once per pass").

THE DEFECT. In accepted count mode the engine asked the session for its
accepted frames about a dozen times per banked frame, and every ask walked
every frame the project holds (`Session.accepted`, `accepted_by_step`,
`total_accepted`): `_quota_met` twice a frame, the "[n/N]" count, the ETA's
`_remaining_hops` and `_step_complete`, the published session sub-state,
`_order_snapshot`. A tail-extended memo already existed for one caller
(`_accepted_now`, #537) and the rest bypassed it. 12.6 of the 12.75 walks a
banked frame cost on test_s7_ledger_cost's 2000-frame night were these.

AS BUILT. `SequenceEngine._ledger_counts` is the one memo: the ledger's
accepted frames by step, for ANY count mode (the session sub-state publishes
`accepted` in attempts mode too), extended from the frames banked since it
was last asked, and handed out as a read-only view (`MappingProxyType`)
because the dict under it is extended in place. `_accepted_now` is it in
accepted mode and None otherwise, as before, so the attempts-mode readers
(`_step_complete`, `_remaining_hops`) still read ``_done``.

THE CASES run on the real `SequenceEngine`. The memo cases set a plan and a
session on an engine and bank frames by hand, with the session's ledger a
list that counts its own walks (``iter``, as test_s7_ledger_cost's meter
does: a slice, which the tail extension takes, is not a walk). The quota
case drives the real `_run_step` on the simulator hub with only the quality
verdict scripted (the `test_session_quota._script_gate` pattern), under a
wall bound so that a memo that never moves ends the test in finite time and
not the suite (#523).

Every named mutant was run from a byte backup inside the worktree, restored
and sha256-checked after each, and the mutant text grepped out (#254).
"""
from __future__ import annotations

import asyncio
import time
from types import MappingProxyType

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence.session import Session, SessionFrame, session_store

STEP_A = "a-L"
STEP_B = "b-L"
#: Real seconds the quota night may take before it is called hung. The night
#: is five 0.05 s exposures and takes a few seconds; a memo that never moves
#: never ends it.
NIGHT_WALL_S = 30.0
#: Walks of the ledger the five-frame night makes, and may make: the memo's
#: first build, and the two ``Session.owed()`` reads the run's ending makes
#: (test_s7_ledger_cost's meter counts them as ``owed``: 2). None per frame.
NIGHT_WALKS = 3


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    _safety = hub_module.config_store.cfg().safety
    monkeypatch.setattr(_safety, "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


class _Walked(list):
    """A ledger that counts the times it is walked: ``iter`` is a walk, and
    a slice, ``len`` and ``append`` are not."""

    walks = 0

    def __iter__(self):
        self.walks += 1
        return super().__iter__()


def _plan(count_mode: str = "accepted", *, count: int = 3) -> SequencePlan:
    """Two single-step targets, A and B, with the step ids `STEP_A` and
    `STEP_B`."""
    return SequencePlan(
        name="memo", guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=False, count_mode=count_mode,
        targets=[Target(id=tid, name=tid.upper(), ra_hours=5.5881,
                        dec_deg=-5.3911, center=False, autofocus_first=False,
                        steps=[ExposureStep(id=sid, filter="L", exposure_s=0.05,
                                            count=count)])
                 for tid, sid in (("a", STEP_A), ("b", STEP_B))])


def _frame(step_id: str, accepted: bool = True) -> SessionFrame:
    return SessionFrame(ts=1.0, night="n", target_id=step_id.split("-")[0],
                        step_id=step_id, auto_accepted=accepted)


def _engine(hub, count_mode: str = "accepted", *, frames=()) -> tuple[
        SequenceEngine, Session, _Walked]:
    """An engine holding a plan and a session whose ledger counts its walks,
    with ``frames`` in it."""
    eng = SequenceEngine(hub)
    plan = _plan(count_mode)
    eng.plan = plan
    s = Session(name="memo", plan=plan, status="active")
    ledger = _Walked(frames)
    s.frames = ledger
    assert s.frames is ledger, "premise: the session keeps the counting list"
    eng._session = s
    return eng, s, ledger


@pytest.mark.parametrize("count_mode", ["accepted", "attempts"])
async def test_the_memo_follows_every_banked_frame_and_walks_the_ledger_once(
        sim_hub, count_mode):
    """Sixty frames banked one at a time, accepted and rejected over two
    steps, and the count asked after each: it is the count of the accepted
    frames so far, at every frame, and the ledger was walked ONCE, by the
    first ask (the empty ledger it started from). The same in both count
    modes: the memo is mode-independent, `_accepted_now` is it in accepted
    mode and None in attempts mode, where `_step_complete` reads ``_done``.

    MUTANT "no memo" (`_ledger_counts` returns ``MappingProxyType(s.
    accepted_by_step())``, a fresh walk at every ask). RED (observed, both
    cases):
        AssertionError: 60 banked frames walked the ledger 61 times; the
        memo extends from the tail, so only the first ask may
        assert 61 == 1
    MUTANT "memo never invalidated" (the tail slice taken as ``frames[
    seen[1]:seen[1]]``: the map taken first is handed back whatever has been
    banked since). RED (observed, both cases):
        AssertionError: after frame 2 (a-L, accepted True) the memo says
        {'b-L': 1}, the ledger holds {'a-L': 1, 'b-L': 1}
        assert {'b-L': 1} == {'a-L': 1, 'b-L': 1}
    MUTANT "memo mode-dependent" (`_ledger_counts` answers an empty map in
    attempts mode). RED (observed, case attempts):
        AssertionError: after frame 1 (b-L, accepted True) the memo says {},
        the ledger holds {'a-L': 0, 'b-L': 1}
        assert {} == {'b-L': 1}
    """
    eng, s, ledger = _engine(sim_hub, count_mode)
    want = {STEP_A: 0, STEP_B: 0}
    for i in range(60):
        sid = STEP_A if i % 3 else STEP_B
        accepted = i % 4 != 3
        s.frames.append(_frame(sid, accepted))
        want[sid] += int(accepted)
        got = {k: v for k, v in eng._ledger_counts().items()}
        assert got == {k: v for k, v in want.items() if v}, (
            f"after frame {i + 1} ({sid}, accepted {accepted}) the memo "
            f"says {got}, the ledger holds {want}")
    assert ledger.walks == 1, (
        f"60 banked frames walked the ledger {ledger.walks} times; the memo "
        f"extends from the tail, so only the first ask may")
    if count_mode == "attempts":
        assert eng._accepted_now() is None
    else:
        assert dict(eng._accepted_now()) == dict(eng._ledger_counts())


async def test_the_memo_hands_out_a_view_nobody_can_write_through(sim_hub):
    """The dict under the memo is extended in place, so a caller that kept
    or wrote to it would corrupt the next answer. Both ways in hand out a
    read-only view: an assignment through either raises, and the count
    stays what the ledger says.

    MUTANT "the live dict handed out" (`_ledger_counts` returns the memo's
    own dict). RED (observed):
        AssertionError: a dict is handed out, writable
        assert False
    """
    eng, s, _ = _engine(sim_hub, frames=[_frame(STEP_A), _frame(STEP_A, False)])
    for view in (eng._ledger_counts(), eng._accepted_now()):
        assert isinstance(view, MappingProxyType), (
            f"a {type(view).__name__} is handed out, writable")
        with pytest.raises(TypeError):
            view[STEP_A] = 99
        with pytest.raises(TypeError):
            view["z-L"] = 1
    assert dict(eng._ledger_counts()) == {STEP_A: 1}


async def test_a_replaced_session_is_counted_afresh(sim_hub):
    """The memo is the session object's: a new one (the next run's, `start`
    resets it, but also a resume that hands the engine a fresh copy) is
    walked, and answers for its own frames, even when it holds as many as
    the one before. Counted by length alone, the second session of four
    frames would be answered with the first's.

    MUTANT "memo keyed on length only" (the ``seen[0] is not s`` identity
    check dropped from `_ledger_counts`). RED (observed):
        AssertionError: the new session was answered with the old one's
        count: {'a-L': 4}
        assert {'a-L': 4} == {'b-L': 4}
    """
    eng, first, _ = _engine(sim_hub, frames=[_frame(STEP_A)] * 4)
    assert dict(eng._ledger_counts()) == {STEP_A: 4}
    second = Session(name="other", plan=eng.plan, status="active")
    second.frames = _Walked([_frame(STEP_B)] * 4)
    eng._session = second
    assert dict(eng._ledger_counts()) == {STEP_B: 4}, (
        "the new session was answered with the old one's count: "
        f"{dict(eng._ledger_counts())}")
    assert second.frames.walks == 1, "the new session was walked once"


async def test_a_ledger_shorter_than_the_memo_saw_is_counted_afresh(sim_hub):
    """The same session object whose ledger is somehow SHORTER than the memo
    saw (nothing in a run shortens it; the memo does not trust that) is
    walked again rather than answered from a count of frames that are gone.

    MUTANT "memo ignores a shorter ledger" (the ``seen[1] > n`` test dropped
    from `_ledger_counts`). RED (observed):
        AssertionError: a ledger of 2 frames answered {'a-L': 5}
        assert {'a-L': 5} == {'a-L': 2}
    """
    eng, s, ledger = _engine(sim_hub, frames=[_frame(STEP_A)] * 5)
    assert dict(eng._ledger_counts()) == {STEP_A: 5}
    del s.frames[2:]
    assert dict(eng._ledger_counts()) == {STEP_A: 2}, (
        f"a ledger of 2 frames answered {dict(eng._ledger_counts())}")
    assert ledger.walks == 2


@pytest.mark.parametrize("count_mode", ["accepted", "attempts"])
async def test_the_published_session_count_is_the_ledgers_in_either_mode(
        sim_hub, count_mode):
    """`_set_state`'s ``session.accepted`` is `Session.total_accepted()` in
    attempts mode too, where `_accepted_now` is None: the sub-state reads
    the mode-free memo. It moves with every banked frame, and the publish
    does not walk the ledger.

    MUTANT "session count from `_accepted_now`" (`_set_state` sums
    ``(self._accepted_now() or {}).values()``). RED (observed, case
    attempts):
        assert 0 == 2
    MUTANT "`_set_state` bypasses the memo" (``self._session.
    total_accepted()``, the call before #516). RED (observed, both cases):
        AssertionError: a publish walked the ledger again for a count the
        memo holds
        assert 2 == 1
    """
    eng, s, ledger = _engine(sim_hub, count_mode,
                             frames=[_frame(STEP_A), _frame(STEP_A, False),
                                     _frame(STEP_B)])
    eng._set_state()
    assert eng.state["session"]["accepted"] == 2
    s.frames.append(_frame(STEP_B))
    s.frames.append(_frame(STEP_A, False))
    walks = ledger.walks
    eng._set_state()
    assert ledger.walks == walks, (
        "a publish walked the ledger again for a count the memo holds")
    assert eng.state["session"]["accepted"] == 3
    # The oracle is asked last: it walks the ledger, which is the point of it.
    assert eng.state["session"]["accepted"] == s.total_accepted()
    assert eng.state["session"]["count_mode"] == count_mode


async def test_every_reader_answers_from_the_memo_without_a_walk(
        sim_hub, monkeypatch):
    """Once the memo is warm, `_step_complete` (with no map handed in),
    `_remaining_hops`, `_drop_complete` and the set-aside line's owed count
    each read it and none walks the ledger: the readers #516 named. The
    answers are the ledger's.

    MUTANT "`_step_complete` bypasses the memo" (its fallback asks
    ``self._session.accepted_by_step()``). RED (observed):
        AssertionError: the readers walked the ledger 2 more time(s) after
        the memo was warm
        assert 3 == 1
    MUTANT "`_remaining_hops` bypasses the memo" (it takes ``self._session.
    accepted_by_step()``, as before #516). RED (observed):
        AssertionError: the readers walked the ledger 2 more time(s) after
        the memo was warm
        assert 3 == 1
    MUTANT "the set-aside line bypasses the memo" (``owed`` asks
    ``self._session.accepted(step.id)``). RED (observed):
        AssertionError: the readers walked the ledger 1 more time(s) after
        the memo was warm
        assert 2 == 1
    """
    eng, s, ledger = _engine(sim_hub, frames=[_frame(STEP_A)] * 3
                             + [_frame(STEP_B)])
    a, b = eng.plan.targets
    eng._acquiring_ti = 0          # A is being acquired: its hop is paid
    eng._ledger_counts()
    warm = ledger.walks
    assert eng._step_complete(a, a.steps[0]) is True
    assert eng._step_complete(b, b.steps[0]) is False
    assert eng._remaining_hops() == 1, "B still owes frames, A is done"
    eng._acquiring_ti = None
    assert eng._remaining_hops() == 0, "before the first acquisition: one less"
    eng._acquiring_ti = 0
    remaining = [a, b]
    eng._drop_complete(remaining, {id(a): 0, id(b): 1})
    assert [t.id for t in remaining] == ["b"]
    monkeypatch.setattr(eng, "_persist_set_aside", lambda *a, **k: None)
    eng._set_step_aside(b, b.steps[0], f"{b.id}:{STEP_B}", 3)
    assert "its 2 frame(s) remain pending" in eng._set_aside[f"b:{STEP_B}"]
    assert ledger.walks == warm, (
        f"the readers walked the ledger {ledger.walks - warm} more time(s) "
        f"after the memo was warm")


async def test_the_quota_flips_exactly_at_the_step_count_and_the_run_ends(
        sim_hub, monkeypatch):
    """The real `_run_step` and its `_quota_met`, on the simulator. Count 3,
    verdicts reject, reject, accept, accept, accept: the loop stops at the
    third ACCEPTED frame, the fifth exposure, not the third exposure and not
    a sixth. The ledger holds five frames, three accepted; the run completes
    and drops its memo with the session it belonged to.

    MUTANT "memo never invalidated" (the tail slice taken as ``frames[
    seen[1]:seen[1]]``): the count stays at the first ask's, so the quota is
    never met and the run never ends; the wall bound reports it. RED
    (observed):
        AssertionError: the run did not end within 30 s of real time: 43
        frames in the ledger, state 'running'
        assert False
    MUTANT "`_quota_met` bypasses the memo" (``self._session.accepted(
    step.id)``, as before #516). RED (observed):
        AssertionError: a night of 5 banked frames walked its ledger 10
        times, against a budget of 3: the memo's first build and the run's
        two ``owed()`` reads at its end, and nothing per frame
        assert 10 <= 3
    MUTANT "the shown count bypasses the memo" (``[n/N]``'s ``self.
    _session.accepted(step.id) + 1``). RED (observed):
        AssertionError: a night of 5 banked frames walked its ledger 8
        times, against a budget of 3 (the same words)
        assert 8 <= 3
    MUTANT "memo kept after the run" (``_accepted_seen`` not cleared where
    the session is let go). RED (observed):
        AssertionError: the memo outlived the session it counted
        assert (Session(id=...), 5, {'a-L': 3}) is None
    (`_remaining_hops` and `_set_state` bypassed, in the night: 14 and 13
    walks; `_step_complete`'s fallback, 5; the memo off, 40.)
    """
    verdicts = [False, False, True, True, True]
    seq = list(verdicts)
    monkeypatch.setattr(SequenceEngine, "_check_quality",
                        lambda self, info, *, record=True, calibration=False:
                        seq.pop(0) if seq else True)
    plan = _plan(count=3)
    plan.targets = plan.targets[:1]
    eng = SequenceEngine(sim_hub)
    eng.start(plan)
    sid = eng._session.id
    walked = _Walked(eng._session.frames)
    eng._session.frames = walked
    try:
        deadline = time.monotonic() + NIGHT_WALL_S
        while eng.running and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        ended = not eng.running
        still = eng.state.get("state")
    finally:
        if eng.running:
            await eng.abort()
    assert ended, (
        f"the run did not end within {NIGHT_WALL_S:g} s of real time: "
        f"{len(walked)} frames in the ledger, state {still!r}")
    s = session_store.load(sid)
    assert (len(s.frames), s.accepted(STEP_A)) == (5, 3), (
        f"the ledger holds {len(s.frames)} frames, {s.accepted(STEP_A)} "
        f"accepted; the step is 3 accepted, so 5 exposures")
    assert eng.state.get("state") == "complete", eng.state
    assert eng._accepted_seen is None, (
        "the memo outlived the session it counted")
    assert walked.walks <= NIGHT_WALKS, (
        f"a night of {len(walked)} banked frames walked its ledger "
        f"{walked.walks} times, against a budget of {NIGHT_WALKS}: the "
        f"memo's first build and the run's two ``owed()`` reads at its "
        f"end, and nothing per frame")
