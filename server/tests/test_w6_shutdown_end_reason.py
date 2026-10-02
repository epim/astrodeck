# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A polite server shutdown finalizes with its own end reason, distinct from
an operator's STOP (#565).

``SequenceEngine._run``'s ``except asyncio.CancelledError`` arm is reached two
ways: ``abort()`` (an operator's STOP, ``/api/disconnect``, a profile apply or
activate -- all deliberate human actions that set ``_aborting`` before they
cancel the task) and a bare cancellation of the task that never went through
``abort()`` at all, which is what a polite server shutdown does: the ASGI
lifespan stops every OTHER background service in its own ``finally`` block
but never touches the sequence engine, so the run task is cancelled by the
event loop's own shutdown (Ctrl+C, a service stop) and lands here with
``_aborting`` still False. Both used to finalize "aborted", so
``GET /api/sequence/recoverable`` (``app._why_dormant``) could not tell a
restart an operator chose from one they never asked for, and the recoverable
card said "You stopped it" either way.

These tests drive the REAL engine and a REAL bare cancellation (not a
stand-in for ``_run``, which is what test_h4_recoverable_says_why.py's `rig`
fixture uses and why that file's `_die` helper is unaffected by this fix: its
stand-in has no ``except CancelledError`` arm of its own to pick a word in).
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.hub as hub_module
from astrodeck.api.app import RESTART_END_REASON, _why_dormant
from astrodeck.flows.store import FlowStore
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence.report import SessionReport
from astrodeck.sequence.session import Session


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety, "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


async def wait_for(predicate, timeout=30.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


def _long_plan(**kw) -> SequencePlan:
    """Long enough that the run is still going when the cancel lands -- a
    plan that self-completes would test the complete path, not this one."""
    return SequencePlan(name="shutdown-me", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False, targets=[Target(
                            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
                            center=False, autofocus_first=False,
                            steps=[ExposureStep(filter="L", exposure_s=0.05,
                                                count=400)])], **kw)


async def _cancel_like_a_shutdown(engine: SequenceEngine) -> None:
    """A bare cancel of the run task, the one thing a polite server shutdown
    does to it (see this module's docstring): never through ``abort()``, so
    ``_aborting`` stays False."""
    engine._task.cancel()
    try:
        await engine._task
    except (asyncio.CancelledError, Exception):
        pass


async def test_a_bare_cancellation_finalizes_shutdown_not_aborted(sim_hub):
    """The engine's own `except CancelledError` arm picks the word from
    `_aborting`, not from the fact that it was cancelled at all.

    Mutant "the fixed word" (the arm keeps finalizing "aborted" whatever
    `_aborting` says, as it did before #565): RED --
        AssertionError: a bare cancellation (no abort()) finalized 'aborted',
        the operator's word, not 'shutdown'
    """
    engine = SequenceEngine(sim_hub)
    engine.start(_long_plan())
    assert await wait_for(lambda: engine._frames_done >= 1), "no frame was taken"
    assert engine._aborting is False, "premise: abort() was never called"

    await _cancel_like_a_shutdown(engine)

    reason = engine.reporter.build().end_reason
    assert reason != "aborted", (
        f"a bare cancellation (no abort()) finalized {reason!r}, the "
        f"operator's word, not 'shutdown'")
    assert reason == "shutdown", (
        f"a bare cancellation should finalize 'shutdown': got {reason!r}")


async def test_control_an_operator_abort_still_finalizes_aborted(sim_hub):
    """CONTROL. The word a STOP leaves is unchanged: `_aborting` is True for
    the whole of `abort()`'s own cancel-and-await, so this is the one path
    that still reaches the except arm with it set.
    """
    engine = SequenceEngine(sim_hub)
    engine.start(_long_plan())
    assert await wait_for(lambda: engine._frames_done >= 1), "no frame was taken"

    await engine.abort()

    reason = engine.reporter.build().end_reason
    assert reason == "aborted", (
        f"an operator STOP should still finalize 'aborted': got {reason!r}")


def test_why_dormant_reads_shutdown_verbatim_and_not_as_a_restart():
    """`app._why_dormant` carries the report's own word verbatim (#487's
    mechanism); #565 only adds a new word for it to carry, never new logic
    there. Pinned here so a future change to `_why_dormant` cannot special-
    case "shutdown" into the restart word, which would undo the split this
    WP built: the operator's recoverable card (D-13, wave 7) depends on the
    three being told apart.

    Mutant "shutdown reads as a restart" (`_why_dormant` maps end_reason
    "shutdown" to `RESTART_END_REASON` instead of returning it verbatim):
    RED --
        AssertionError: a polite shutdown's own report reads 'restart', not
        its own word
    """
    session = Session(id="s", name="n", origin="plan")
    session.crash_resumes = 0
    last = SessionReport(id="r", end_reason="shutdown")

    reason = _why_dormant(session, last)

    assert reason == "shutdown", (
        f"a polite shutdown's own report reads {reason!r}, not its own word")
    assert reason != RESTART_END_REASON
    assert reason != "aborted"


def test_flow_result_names_shutdown_explicitly():
    """`_FLOW_RESULT` gets the new word (fix shape (a)), named rather than
    left to `_record_flow_result`'s `.get(reason, "warn")` default -- which
    answers the same value either way and so cannot be graded by watching
    that default fire; the key's presence has to be asked for directly.

    Mutant "shutdown missing from _FLOW_RESULT" (the entry deleted): RED --
        AssertionError: 'shutdown' not in {...}
    """
    assert "shutdown" in SequenceEngine._FLOW_RESULT, (
        "'shutdown' not in " + repr(SequenceEngine._FLOW_RESULT))
    assert SequenceEngine._FLOW_RESULT["shutdown"] == "warn"


async def test_a_shutdown_ending_is_recorded_on_the_flow_it_came_from(
        sim_hub, tmp_path, monkeypatch):
    """End to end: a flow run a polite shutdown cancels still records a
    result on its flow (`_record_flow_result`), the same as every other
    non-"complete" ending -- the finalize path never special-cased "aborted"
    as the only reason worth recording, and "shutdown" takes the same path.
    """
    import astrodeck.flows.store as store_module
    store = FlowStore(tmp_path / "flows")
    monkeypatch.setattr(store_module, "flow_store", store)
    from astrodeck.flows.models import FlowGraph, FlowRecord
    rec = store.save(FlowRecord(name="mine", graph=FlowGraph()))

    engine = SequenceEngine(sim_hub)
    engine.start(_long_plan(), origin="flow", origin_id=rec.id)
    assert await wait_for(lambda: engine._frames_done >= 1), "no frame was taken"

    await _cancel_like_a_shutdown(engine)

    assert store.get(rec.id).last_result == "warn", (
        f"a shutdown-ended flow run should read 'warn': "
        f"{store.get(rec.id).last_result!r}")
