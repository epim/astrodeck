# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#668: ``SessionReporter.flush()``.

``record_safety`` / ``mark_skipped`` / ``record_sky_angle`` (and
``record_frame`` / ``record_policy``) all end in ``_schedule_write``, which
is fire-and-forget: ``loop.create_task(self._write_async())``, never
awaited. A wind-down's roof-close event is recorded that way, AFTER the
run's own terminal state and ``_finalize_report`` have already run (the dome
is closed as part of winding down, not before the run is declared ended), so
a reader fetching the report the instant the wind-down returns could win or
lose the race against that write landing on disk -- intermittently, since it
depends on how the event loop happens to interleave the wind-down's
remaining awaits against the scheduled task. Found by the wave 7 integrator
2026-10-02 running ``test_s7_sim_solve_failure.py`` back to back on
byte-identical code and seeing both outcomes; the integration's own stopgap
(c8635a27) loosened that test's assertion rather than fix the race.

``flush()`` is the fix: await every pending write before the caller goes on
to declare the report final. Two things about it are tested here in
isolation (``server/tests/test_s7_sim_solve_failure.py``'s restored exact
assertion is the end-to-end proof, through the real engine and wind-down):

1. It actually waits -- a write ``_schedule_write`` has fired is still
   incomplete when ``flush()`` is called, and the event it carries is on
   disk only once ``flush()`` has returned.
2. It is cancel-safe (the #235/#252 class
   ``test_no_task_await_eats_its_callers_cancel.py`` scans the whole package
   for): a cancel of whoever is awaiting ``flush()`` must still end that
   coroutine with ``CancelledError``, not be swallowed by whatever the
   pending write(s) end with.
"""
from __future__ import annotations

import asyncio

import astrodeck.hub as hub_module
import pytest

from astrodeck.sequence.models import SequencePlan
from astrodeck.sequence.report import SessionReporter

pytestmark = pytest.mark.asyncio

_BOUND_S = 2.0


@pytest.fixture(autouse=True)
def isolate_reports(tmp_path, monkeypatch):
    """Redirect CAPTURE_DIR so reports land in tmp, never the real captures/
    (mirrors test_report.py's fixture of the same name)."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    return tmp_path


async def test_flush_waits_for_a_write_schedule_write_fired_but_not_finished(
        monkeypatch):
    """The exact #668 race, forced: ``_write_async`` is held behind a gate
    the test controls, so the scheduled write is PROVABLY still in flight
    when ``flush()`` is called, and ``flush()`` must not return before it
    has actually reached disk.

    MUTANT "flush() returns without waiting" (its body replaced with a bare
    ``return``) -- RED, observed verbatim:

        AssertionError: flush() returned before the delayed write even
        started running
        assert not True
         +  where True = <built-in method done of _asyncio.Task object...>()
    """
    plan = SequencePlan(name="N")
    r = SessionReporter(plan)
    gate = asyncio.Event()
    real_write_async = r._write_async

    async def _delayed_write_async():
        await gate.wait()
        await real_write_async()

    monkeypatch.setattr(r, "_write_async", _delayed_write_async)

    # record_safety only SCHEDULES this write (_schedule_write); the task is
    # created but parked on `gate`, exactly the moment-in-time #668 names.
    r.record_safety("roof closed over parked gear (wind-down)", "close_roof")

    flush_task = asyncio.create_task(r.flush())
    await asyncio.sleep(0)   # let flush() actually start gathering
    assert not flush_task.done(), (
        "flush() returned before the delayed write even started running")

    gate.set()
    await asyncio.wait_for(flush_task, timeout=_BOUND_S)
    assert flush_task.exception() is None

    loaded = SessionReporter.load(r.id)
    assert loaded is not None and any(
        e["action"] == "close_roof" for e in loaded.safety_events), (
        "flush() returned before the delayed write reached disk")


async def test_control_an_unscheduled_flush_returns_at_once():
    """CONTROL: nothing pending. ``flush()`` returns immediately."""
    r = SessionReporter(SequencePlan(name="N"))
    await asyncio.wait_for(r.flush(), timeout=_BOUND_S)


async def test_a_cancelled_flush_caller_does_not_run_on(monkeypatch):
    """A cancel of whoever awaits ``flush()`` must end THEM with
    ``CancelledError`` within the bound, not be eaten by however the pending
    write(s) end (#235/#252 class). ``_write_async`` here never finishes on
    its own -- only a cancel ends it -- so the write is provably still
    pending at the moment of the cancel.

    MUTANT "flush() swallows a cancel" (its ``await asyncio.gather(...)``
    wrapped in ``try: ... except BaseException: pass``, the #252
    ``suppress``-equivalent shape) -- RED, observed verbatim:

        AssertionError: flush() ate its caller's cancel: the caller ran on
        (the code after it ran: True)

    NOT ALSO CAUGHT BY THE GENERIC GUARD (filed as #681). The #235/#252 scan
    (``test_no_task_await_eats_its_callers_cancel.py``) only recognises
    "awaits a task" as a bare name/attribute/subscript or ``wait_for``/
    ``shield`` of one -- this mutant's ``await asyncio.gather(...)`` is none
    of those, so the scan's ``_awaits_a_task`` says no and the ``try`` is
    never even inspected for a swallowing handler; it stayed green under this
    exact mutant, off this work package's files (the guard file is ours to
    edit, but widening its AST shape is #681's fix, not WP-59's). This test
    is therefore flush()'s ONLY guard against the shape today.
    """
    r = SessionReporter(SequencePlan(name="N"))

    async def _never_finishes_on_its_own():
        await asyncio.Event().wait()

    monkeypatch.setattr(r, "_write_async", _never_finishes_on_its_own)
    r.record_safety("x", "y")

    ran_after: list[bool] = []

    async def caller() -> None:
        await r.flush()
        ran_after.append(True)

    caller_task = asyncio.create_task(caller())
    await asyncio.sleep(0)   # let flush() start gathering the pending write
    caller_task.cancel()
    await asyncio.wait({caller_task}, timeout=_BOUND_S)
    assert caller_task.done(), (
        f"the caller had not ended {_BOUND_S} s after its cancel")
    assert caller_task.cancelled(), (
        f"flush() ate its caller's cancel: the caller ran on (the code "
        f"after it ran: {bool(ran_after)})")
    assert not ran_after
