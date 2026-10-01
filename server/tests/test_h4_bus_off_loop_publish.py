"""A publish off the loop's thread hands delivery to the loop (#480; the
loop-bound-state-from-a-worker-thread class).

THE DEFECT. ``EventBus._deliver`` put each event into every subscriber's
``asyncio.Queue`` with ``put_nowait``, on whatever thread called
``bus.log``. Those queues belong to the event loop, and code handed to
``asyncio.to_thread`` logs too: ``SessionReporter._persist`` says a failed
snapshot write from the snapshot's worker thread. ``put_nowait`` from a
foreign thread wakes a parked ``get()`` through ``loop.call_soon``, which is
not thread-safe:

* normally the wake-up is queued but the loop is not woken, so a quiet loop
  shows the line only when something else wakes it (0.28 s in #480's probe,
  intermittent and load-dependent);
* under asyncio's debug mode it raises ``RuntimeError: Non-thread-safe
  operation invoked on an event loop other than the current one`` out of
  ``bus.log``, which in ``_persist`` escapes the worker into a task nobody
  awaits.

THE FIX. The bus records the loop each subscriber subscribed from, and a
publish made off that loop's thread hands the delivery to it with
``call_soon_threadsafe``. The ring and the night log are still appended at
once, on the caller's thread. With no loop running a subscriber is served
inline, as before; one whose loop has closed is skipped.

Each case names the mutants it was shown RED under, with the failure
observed, verbatim (pytest's own lines, long ones wrapped). The mutants were
applied in a private scratch copy of server/ (scratchpad/H4-H4-BUS-mut,
under the session's scratch root), never in the shared tree (#254):

* "deliver inline from the worker thread": ``_fan_out`` serves a subscriber
  whose loop is running on another thread with ``_offer`` at once, as
  before the fix, instead of handing it to that loop.
* "hand off even on the loop": the on-loop branch is dropped, so a publish
  made on the subscriber's own loop is handed off too.
* "hand off whenever no loop runs here": a subscriber whose loop is stopped,
  not running, is handed off rather than served inline.
* "inline into a closed loop": a subscriber whose loop has closed is served
  inline rather than skipped.
* "hand the whole _deliver to the loop": #480's suggested shape read
  literally; a publish off the loop defers the ring and the night log along
  with the delivery.
"""
from __future__ import annotations

import asyncio
import threading
import time
import types

from astrodeck.events import EventBus, night_key

#: How long a reader waits before the case calls it not woken. Five seconds,
#: so a loop that is only woken by the reader's own timeout is plainly late.
WAIT_S = 5.0
#: What "promptly" means: a handed-off line lands in milliseconds, and a line
#: nothing woke the loop for lands at WAIT_S. Wide enough for a loaded runner.
PROMPT_S = 1.0


def _debug_run(monkeypatch, scenario):
    """Run ``scenario()`` on a fresh loop created with PYTHONASYNCIODEBUG set,
    the switch #480 names: asyncio reads it when a loop is built, and in
    debug mode a non-thread-safe call from a foreign thread raises instead
    of passing quietly.

    Not ``asyncio.run``. When the raise comes out of ``set_result`` the
    getter is finished but its task's wake-up was never scheduled, so that
    task can neither finish nor be cancelled, and ``asyncio.run``'s shutdown,
    which cancels every task and waits for them, would hang the case instead
    of failing it. Here the loop is closed with such a task left pending."""
    monkeypatch.setenv("PYTHONASYNCIODEBUG", "1")
    loop = asyncio.new_event_loop()
    try:
        assert loop.get_debug(), (
            "precondition: the loop is in debug mode, or the case cannot see "
            "the RuntimeError")
        return loop.run_until_complete(scenario())
    finally:
        loop.run_until_complete(loop.shutdown_default_executor())
        loop.close()


async def _read(reader: asyncio.Future):
    """The event a parked reader got within WAIT_S, or None. Waited on with
    ``asyncio.wait``, which cancels nothing: a reader stuck on a wake-up that
    was never scheduled cannot be cancelled either (see ``_debug_run``)."""
    done, _pending = await asyncio.wait({reader}, timeout=WAIT_S)
    if not done:
        reader._log_destroy_pending = False     # it can never finish
        return None
    return reader.result()


def test_a_worker_threads_log_wakes_a_parked_subscriber_promptly(monkeypatch):
    """#480's probe, as a case: a subscriber parked in ``get()`` on a loop
    with nothing else to do, and a plain worker thread calling ``bus.log``.
    Not ``asyncio.to_thread``: its completion wakes the loop by itself and
    would hide a delivery that did not.

    RED under "deliver inline from the worker thread". Observed:

        E   AssertionError: bus.log raised on the worker thread:
            [RuntimeError('Non-thread-safe operation invoked on an event
            loop other than the current one')]
        E   assert [RuntimeError...current one')] == []
    """
    bus = EventBus(persist=False)

    async def scenario():
        q = bus.subscribe()
        errors: list[BaseException] = []

        def worker():
            try:
                bus.log("warning", "from the worker", "probe")
            except BaseException as exc:          # noqa: BLE001 - reported
                errors.append(exc)

        reader = asyncio.ensure_future(q.get())
        await asyncio.sleep(0)                    # the reader is parked
        started = time.monotonic()
        thread = threading.Thread(target=worker, name="h4-bus-probe")
        thread.start()
        ev = await _read(reader)
        latency = time.monotonic() - started
        thread.join()
        return ev, latency, errors

    ev, latency, errors = _debug_run(monkeypatch, scenario)
    assert errors == [], f"bus.log raised on the worker thread: {errors!r}"
    assert ev is not None, "the parked subscriber was never woken"
    assert ev.data == {"level": "warning", "message": "from the worker",
                       "source": "probe"}
    assert latency < PROMPT_S, (
        f"the line took {latency:.2f} s to wake an idle loop")


def test_control_an_on_loop_publish_is_delivered_synchronously_in_order(
        monkeypatch):
    """CONTROL: the publishes made on the loop, which is nearly all of them,
    are served exactly as before: in the queue when ``publish`` returns, in
    the order they were made, with no loop turn in between.

    RED under "hand off even on the loop". Observed:

        E   asyncio.queues.QueueEmpty
    """
    bus = EventBus(persist=False)

    async def scenario():
        q = bus.subscribe()
        bus.publish("status", i=0)
        bus.log("info", "one", "probe")
        bus.publish("status", i=1)
        # No await since the publishes: all three are already there.
        return [q.get_nowait() for _ in range(3)], q.qsize()

    got, left = _debug_run(monkeypatch, scenario)
    assert [(e.type, e.data) for e in got] == [
        ("status", {"i": 0}),
        ("log", {"level": "info", "message": "one", "source": "probe"}),
        ("status", {"i": 1}),
    ]
    assert left == 0


def test_control_with_no_loop_running_delivery_is_inline_and_a_closed_loop_is_skipped():
    """CONTROL: a subscriber that subscribed with no loop, or on a loop that
    is stopped (no thread is running it), is served at once, inline, as
    before: there is no loop thread to race. One whose loop has CLOSED is
    skipped, and the publish does not raise. Its reader cannot exist any
    more; this one was parked in ``get()`` when the loop closed, so an
    inline put would wake a task on a closed loop.

    RED under "inline into a closed loop". Observed:

        E   RuntimeError: Event loop is closed

    RED under "hand off whenever no loop runs here". Observed:

        E   assert 0 == 1
        E    +  where 0 = qsize()
        E    +    where qsize = <Subscription at 0x18e53a90170 maxsize=500
            _getters[1]>.qsize
    """
    bus = EventBus(persist=False)
    no_loop = bus.subscribe()

    async def park():
        q = bus.subscribe()
        reader = asyncio.ensure_future(q.get())
        await asyncio.sleep(0)                    # parked in get()
        return q, reader

    async def park_bare():
        # What ``Queue.get()`` does on an empty queue, without the task
        # around it: a getter future of this loop in ``_getters``, with a
        # done-callback standing in for the task's wake-up. A real task
        # cannot be left parked on a closed loop: collecting its coroutine
        # later cancels its getter, which calls ``call_soon`` on the closed
        # loop and raises again, into the next test's warnings.
        q = bus.subscribe()
        getter = asyncio.get_running_loop().create_future()
        getter.add_done_callback(lambda _f: None)
        q._getters.append(getter)
        return q

    stopped = asyncio.new_event_loop()
    closed = asyncio.new_event_loop()
    try:
        q_stopped, r_stopped = stopped.run_until_complete(park())
        q_closed = closed.run_until_complete(park_bare())
        closed.close()

        bus.publish("status", i=1)                # must not raise

        assert no_loop.qsize() == 1
        assert q_stopped.qsize() == 1
        assert q_closed.qsize() == 0
        # The stopped loop runs again and its parked reader has the event.
        ev = stopped.run_until_complete(asyncio.wait_for(r_stopped, WAIT_S))
        assert ev.data == {"i": 1}
    finally:
        stopped.close()


def test_control_the_night_log_line_is_written_before_the_loop_runs(
        tmp_path, monkeypatch):
    """CONTROL: only the subscribers wait for the loop. The worker thread
    runs while the loop thread is blocked joining it, so the loop cannot
    have run anything, and the line is already in the night file and the
    ring, and not yet in the subscriber's queue. One loop turn later it is.

    RED under "hand the whole _deliver to the loop". Observed:

        E   AssertionError: assert [] == ['written at once']
        E     Right contains one more item: 'written at once'

    RED under "deliver inline from the worker thread" (the queue is filled
    from the worker; the debug-mode RuntimeError does not show here, since
    no reader is parked). Observed:

        E   assert 1 == 0
    """
    import astrodeck.hub as hubmod
    monkeypatch.setattr(hubmod, "CAPTURE_DIR", tmp_path)
    bus = EventBus()

    async def scenario():
        q = bus.subscribe()
        thread = threading.Thread(
            target=bus.log, args=("warning", "written at once", "probe"),
            name="h4-bus-night-log")
        thread.start()
        thread.join()                             # the loop thread is blocked
        on_disk = [r["data"]["message"]
                   for r in bus.night_log.read(night_key())]
        in_ring = [r["data"]["message"] for r in bus.log_history]
        queued = q.qsize()
        ev = await asyncio.wait_for(q.get(), WAIT_S)
        return on_disk, in_ring, queued, ev

    on_disk, in_ring, queued, ev = asyncio.run(scenario())
    assert on_disk == ["written at once"]
    assert in_ring == ["written at once"]
    assert queued == 0
    assert ev.data["message"] == "written at once"


def test_the_session_reporters_snapshot_warning_from_its_worker_thread(
        monkeypatch):
    """The named real caller: ``SessionReporter._write_async`` runs
    ``_persist`` through ``asyncio.to_thread``, and a failed snapshot write
    says so with ``bus.log`` from that worker thread. Under asyncio's debug
    mode the warning reaches the subscriber and nothing raises.

    RED under "deliver inline from the worker thread": the RuntimeError
    comes out of ``bus.log`` in ``_persist``, through ``asyncio.to_thread``,
    out of ``await reporter._write_async()``. Observed:

        E   AssertionError: the snapshot write raised: [RuntimeError('Non-
            thread-safe operation invoked on an event loop other than the
            current one')]
        E   assert [RuntimeError...current one')] == []
    """
    from astrodeck.sequence import report as report_mod
    bus = EventBus(persist=False)
    monkeypatch.setattr(report_mod, "bus", bus)

    def full_disk(*_a, **_k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(report_mod, "write_json_atomic", full_disk)

    raised: list[BaseException] = []

    async def scenario():
        q = bus.subscribe()
        # Parked, as the WS stream's reader is on a quiet loop: a put with no
        # getter waiting never reaches ``call_soon``, and cannot raise.
        reader = asyncio.ensure_future(q.get())
        await asyncio.sleep(0)
        reporter = report_mod.SessionReporter(types.SimpleNamespace(name="probe"))
        try:
            await reporter._write_async()
        except BaseException as exc:              # noqa: BLE001 - reported
            raised.append(exc)
        return await _read(reader)

    ev = _debug_run(monkeypatch, scenario)
    assert raised == [], f"the snapshot write raised: {raised!r}"
    assert ev is not None, "the warning never reached the subscriber"
    assert ev.type == "log"
    assert ev.data["level"] == "warning" and ev.data["source"] == "report"
    assert ev.data["message"].startswith(
        "session report write failed (snapshot) at reports/"), ev.data
