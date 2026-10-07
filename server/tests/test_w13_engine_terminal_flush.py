# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#668 follow-up (backlog wave 13 integration, item 5(a)): the two terminal
paths ``SequenceEngine._finalize_report``'s own docstring names as never
reaching ``_wind_down``'s ``await self.reporter.flush()`` -- the
``except asyncio.CancelledError`` and ``except Exception`` arms of ``_run``
-- plus a THIRD, ``abort()``'s own ``_finalize_report("aborted")`` for a run
cancelled before its task ever took a turn (so ``_run``'s own try body, and
both its arms above, never ran at all).

Each test forces the real engine down the one arm it names, with a
``record_safety``/``mark_skipped`` write ARTIFICIALLY DELAYED behind a gate
the test controls (the same technique test_w13_report_flush.py uses on
``SessionReporter`` directly, here through the real ``_run``/``abort`` code
paths) so the write is PROVABLY still in flight at the moment that arm would,
without the fix, have let its caller believe the run was over. Mirrors the
end-to-end proof test_s7_sim_solve_failure.py already is for `_wind_down`'s
own flush.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.models import ExposureStep, Target
from astrodeck.sequence.report import SessionReporter

pytestmark = pytest.mark.asyncio

_BOUND_S = 5.0
_NOT_DONE_WAIT_S = 0.3


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety, "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _long_plan(**kw) -> SequencePlan:
    """Long enough that the run is still going when the cancel/exception
    lands -- a plan that self-completes would test a different ending."""
    return SequencePlan(name="w13-flush-me", guide=False, dither_every=0,
                        autofocus_every=0, meridian_flip=False, targets=[Target(
                            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
                            center=False, autofocus_first=False,
                            steps=[ExposureStep(filter="L", exposure_s=0.05,
                                                count=400)])], **kw)


async def wait_for(predicate, timeout=30.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


def _gate_the_reporters_writes(reporter: SessionReporter) -> asyncio.Event:
    """Monkeypatch-free gate (no fixture needed): replaces the reporter's own
    ``_write_async`` with one parked behind the returned ``asyncio.Event``,
    so any write ``_schedule_write`` fires from this point on is PROVABLY
    still pending until the test releases the gate."""
    gate = asyncio.Event()
    real_write_async = reporter._write_async

    async def _delayed_write_async():
        await gate.wait()
        await real_write_async()

    reporter._write_async = _delayed_write_async
    return gate


async def test_a_bare_cancellation_flushes_a_pending_write_before_it_ends(
        sim_hub):
    """The ``except asyncio.CancelledError`` arm: a bare cancel (the polite-
    shutdown shape, #565) must not let the task finish while a
    ``record_safety`` write it is responsible for is still in flight.

    Named mutant: the ``await self.reporter.flush()`` this wave's integration
    added to this arm removed. RED, expected shape (the task finishes at
    once instead of blocking on the gate):

        AssertionError: the CancelledError arm ended without waiting for the
        pending write (flush() was not awaited)
    """
    engine = SequenceEngine(sim_hub)
    engine.start(_long_plan())
    assert await wait_for(lambda: engine._frames_done >= 1), "no frame was taken"

    gate = _gate_the_reporters_writes(engine.reporter)
    engine.reporter.record_safety("w13-followup: pending at cancel", "test")

    engine._task.cancel()
    await asyncio.sleep(_NOT_DONE_WAIT_S)
    assert not engine._task.done(), (
        "the CancelledError arm ended without waiting for the pending write "
        "(flush() was not awaited)")

    gate.set()
    done, _pending = await asyncio.wait({engine._task}, timeout=_BOUND_S)
    assert engine._task in done, f"the task never finished {_BOUND_S}s after the gate opened"
    try:
        await engine._task
    except asyncio.CancelledError:
        pass

    loaded = SessionReporter.load(engine.reporter.id)
    assert loaded is not None and any(
        e.get("reason") == "w13-followup: pending at cancel"
        for e in loaded.safety_events), (
        "the pending event never reached disk: " + repr(loaded))


async def test_an_engine_exception_flushes_a_pending_write_before_it_ends(
        sim_hub, monkeypatch):
    """The ``except Exception`` arm: a plain crash inside the run must not
    let ``_run`` return while a write it is responsible for is still
    in flight.

    Named mutant: the ``await self.reporter.flush()`` this wave's
    integration added to this arm removed. RED, same shape as the
    cancellation test above."""
    engine = SequenceEngine(sim_hub)

    gate: list[asyncio.Event] = []

    async def _boom(plan):
        g = _gate_the_reporters_writes(engine.reporter)
        gate.append(g)
        engine.reporter.record_safety("w13-followup: pending at crash", "test")
        raise RuntimeError("synthetic crash for #668 follow-up coverage")

    monkeypatch.setattr(engine, "_run_scheduled", _boom)
    engine.start(_long_plan())

    assert await wait_for(lambda: len(gate) == 1), "the run never reached _run_scheduled"
    await asyncio.sleep(_NOT_DONE_WAIT_S)
    assert not engine._task.done(), (
        "the Exception arm ended without waiting for the pending write "
        "(flush() was not awaited)")

    gate[0].set()
    done, _pending = await asyncio.wait({engine._task}, timeout=_BOUND_S)
    assert engine._task in done, f"the task never finished {_BOUND_S}s after the gate opened"
    await engine._task   # the Exception arm swallows the crash; must not re-raise here

    loaded = SessionReporter.load(engine.reporter.id)
    assert loaded is not None and any(
        e.get("reason") == "w13-followup: pending at crash"
        for e in loaded.safety_events), (
        "the pending event never reached disk: " + repr(loaded))


async def test_a_cancel_before_the_first_turn_flushes_a_pending_write(
        sim_hub):
    """The SEVENTH terminal path: ``abort()``'s own
    ``_finalize_report("aborted")`` for a run cancelled before its task ever
    took a turn, so ``_run``'s own try body -- and both arms the two tests
    above cover -- never ran at all. ``abort()`` itself must not return
    while a write it is responsible for is still in flight.

    Realising "before its first turn" takes care: wrapping ``abort()`` in
    its OWN ``asyncio.create_task`` reintroduces exactly the ambiguity this
    wants to avoid, because the run task (scheduled first, by ``start()``)
    would then get the FIRST turn once the loop regains control, before the
    newly-created abort task gets its own -- which reaches ``_run``'s own
    ``except CancelledError`` arm instead (the first test above), not this
    one. So the run task is cancelled and reaped DIRECTLY here, with no
    ``await`` at all between ``start()`` and the cancel (a never-started
    coroutine's ``Task.cancel()`` makes its very first step raise
    ``CancelledError`` before any of its own body runs -- see the engine's
    own comment on this) -- and ``engine.abort()`` is then awaited
    DIRECTLY (not through a further ``create_task``), so its own
    synchronous prefix (seeing ``self._task.done()`` already true) runs on
    the same turn as everything above it, reaching its fallback
    ``_finalize_report`` exactly as a genuine never-started cancel would.

    Named mutant: the ``await self.reporter.flush()`` this wave's
    integration added inside ``abort()`` removed."""
    engine = SequenceEngine(sim_hub)
    engine.start(_long_plan())
    # NO await between start() and the cancel below: the run task must not
    # get a turn at all.
    engine._task.cancel()
    try:
        await engine._task
    except asyncio.CancelledError:
        pass
    assert engine._frames_done == 0, (
        "premise broken: the run task took a turn before being cancelled")
    assert engine.state.get("state") not in ("complete", "aborted", "error"), (
        "premise broken: _run's own handler already finalized a terminal "
        "state, so abort()'s own fallback below would never run")

    gate = _gate_the_reporters_writes(engine.reporter)
    engine.reporter.record_safety("w13-followup: pending before first turn", "test")

    abort_task = asyncio.create_task(engine.abort())
    await asyncio.sleep(_NOT_DONE_WAIT_S)
    assert not abort_task.done(), (
        "abort() returned without waiting for the pending write (flush() "
        "was not awaited)")

    gate.set()
    await asyncio.wait_for(abort_task, timeout=_BOUND_S)

    loaded = SessionReporter.load(engine.reporter.id)
    assert loaded is not None and any(
        e.get("reason") == "w13-followup: pending before first turn"
        for e in loaded.safety_events), (
        "the pending event never reached disk: " + repr(loaded))
