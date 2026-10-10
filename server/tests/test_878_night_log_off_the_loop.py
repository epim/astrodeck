# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The night file is written off the event loop (#878; the synchronous-disk-
I/O-on-the-loop class, after #97 and the fingerprint write).

THE DEFECT. ``EventBus._deliver`` called ``NightLogWriter.append`` on the
caller's thread, and ``append`` did the disk work there: ``mkdir`` on a night
roll, open-append-close, the prune. Most ``bus.log`` callers run on the asyncio
loop, so while the disk stalled every log line stopped the loop for as long as
its write took, and with it every HTTP request and websocket. The stall also
logged about itself (the "write took 2.1 s" warnings of #858 were published
from the loop), so it fed itself.

THE FIX. ``append`` only queues the line. One daemon thread, shared by every
writer, takes the queue in order and does the disk work. A reader of the file,
the server's shutdown and an exit hook drain the queue first
(``flush_night_logs``), so a clean stop loses nothing and a crash loses what
was queued. A full queue drops the newest line, counts it, and says how many
once it has drained.

The stall in these cases is an ``open`` in ``astrodeck.events`` that sleeps or
waits on a gate when it is asked to append, which is what a disk under
contention does to the writer. Every gate is bounded and released in a
``finally``, so a regression fails these cases and does not hang them.

Each case names the mutant it was shown RED under; the run is in the
work-package report. The mutants were applied to a byte backup of
``events.py`` and restored from it (never with git):

* M1 "the synchronous call restored": ``NightLogWriter.append`` writes the line
  itself, on the caller's thread, instead of queueing it.
* M2 "no exit hook": the ``atexit.register(flush_night_logs, ...)`` line is
  gone.
* M3 "path resolved at write time": the queued item carries no path and the
  writer thread asks ``path_for`` when it gets to the line.
* M4 "newest line first": the writer thread takes the newest queued line
  before the oldest.
"""
from __future__ import annotations

import asyncio
import builtins
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import httpx
import pytest

import astrodeck.api.app as app_module
import astrodeck.hub as hubmod
from astrodeck import events
from astrodeck.auth import reset_active_provider
from astrodeck.events import EventBus, night_key

#: How long a stalled write takes in the loop cases, and how many lines the
#: case publishes into it. Unfixed, the loop is stopped for STALL_S * LINES.
STALL_S = 1.0
LINES = 3
#: The longest a gated write waits for its gate. Bounded, so a regression
#: that writes on the caller's thread fails the case instead of hanging it.
GATE_MAX_S = 2.0
#: What "the caller did not wait for the disk" means: well under one stall.
PROMPT_S = STALL_S * 0.9


class _SlowOpen:
    """Stands in for ``open`` in ``astrodeck.events``: an append (the night
    file's write) stalls, a read passes through. ``stall_s`` sleeps on every
    append; ``gate`` holds the FIRST append until it is set."""

    def __init__(self, stall_s: float = 0.0,
                 gate: threading.Event | None = None) -> None:
        self.stall_s = stall_s
        self.gate = gate
        self.calls = 0
        self.threads: list[str] = []
        self.started = threading.Event()
        self._gated = False

    def __call__(self, file, mode="r", *args, **kwargs):
        if "a" in mode:
            self.calls += 1
            self.threads.append(threading.current_thread().name)
            self.started.set()
            if self.gate is not None:
                if not self._gated:
                    self._gated = True
                    self.gate.wait(GATE_MAX_S)
            else:
                time.sleep(self.stall_s)
        return builtins.open(file, mode, *args, **kwargs)


@pytest.fixture()
def bus(tmp_path, monkeypatch):
    """A private bus whose night log writes under ``tmp_path``."""
    monkeypatch.setattr(hubmod, "CAPTURE_DIR", tmp_path)
    return EventBus()


def _written(bus: EventBus, night: str | None = None) -> list[str]:
    return [r["data"]["message"]
            for r in bus.night_log.read(night or night_key())]


def _ring(bus: EventBus) -> list[str]:
    return [e["data"]["message"] for e in bus.log_history]


async def _heartbeat(stop: asyncio.Event, lates: list[float]) -> None:
    """How late the loop runs a 10 ms sleep: the figure a stalled loop shows."""
    loop = asyncio.get_running_loop()
    while not stop.is_set():
        t = loop.time()
        await asyncio.sleep(0.01)
        lates.append(loop.time() - t - 0.01)


# ---------------------------------------------- the loop does not wait on disk

async def test_a_log_line_on_the_loop_does_not_wait_for_a_stalled_disk(
        bus, monkeypatch):
    """The defect: four lines published on the loop into a disk that takes
    STALL_S per write held the loop for LINES * STALL_S. Now the lines are
    queued, the loop runs on, and the thread that is stuck is the writer's.

    RED under M1, observed (the loop was stopped for the whole stall):

        E   AssertionError: 3 log lines on the loop took 3.00 s against a disk
            that stalls 1.0 s per write; the loop waited on the disk
        E   assert 3.0032992999767885 < 0.9
    """
    slow = _SlowOpen(stall_s=STALL_S)
    monkeypatch.setattr(events, "open", slow, raising=False)
    stop, lates = asyncio.Event(), []
    beat = asyncio.create_task(_heartbeat(stop, lates))
    await asyncio.sleep(0.05)
    t0 = time.perf_counter()
    for i in range(LINES):
        bus.log("info", f"stall probe {i}", "probe")
        await asyncio.sleep(0)
    took = time.perf_counter() - t0
    await asyncio.sleep(0.05)
    stop.set()
    await beat

    assert took < PROMPT_S, (
        f"{LINES} log lines on the loop took {took:.2f} s against a disk that "
        f"stalls {STALL_S} s per write; the loop waited on the disk")
    assert max(lates) < STALL_S / 2, (
        f"the loop ran a 10 ms sleep {max(lates):.2f} s late while lines "
        "were being logged")

    # Nothing was dropped: every line lands, in order, once the disk does.
    assert await asyncio.to_thread(bus.night_log.flush, 30.0) is True
    assert _written(bus) == [f"stall probe {i}" for i in range(LINES)]
    assert slow.calls == LINES, (
        "the stalled open was never reached, so the case proved nothing")
    assert threading.current_thread().name not in slow.threads, (
        "the write happened on the thread that published the line")


async def test_a_request_on_the_real_app_is_not_delayed_by_a_stalled_night_log(
        monkeypatch):
    """The same stall through the real app: ``POST /api/mount/unpark`` logs
    "mount unparked" on the loop, as most routes log. A request served while
    the night file stalls is served at once, the loop does not run late, and
    the lines are all in the file, in order, afterwards.

    RED under M1, observed:

        E   AssertionError: 3 requests that each log a line took 3.02 s
            against a disk that stalls 1.0 s per write
        E   assert 3.0209431999828666 < 0.9
    """
    class _Mount:
        connected = True

        async def unpark(self) -> None:
            return None

    app = app_module.create_app()
    monkeypatch.setattr(app_module.hub, "require", lambda role: _Mount())
    slow = _SlowOpen(stall_s=STALL_S)
    monkeypatch.setattr(events, "open", slow, raising=False)
    try:
        async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://astrodeck.test") as client:
            stop, lates = asyncio.Event(), []
            beat = asyncio.create_task(_heartbeat(stop, lates))
            await asyncio.sleep(0.05)
            t0 = time.perf_counter()
            for _ in range(LINES):
                r = await client.post("/api/mount/unpark")
                assert r.status_code == 200, r.text
            took = time.perf_counter() - t0
            await asyncio.sleep(0.05)
            stop.set()
            await beat

            assert took < PROMPT_S, (
                f"{LINES} requests that each log a line took {took:.2f} s "
                f"against a disk that stalls {STALL_S} s per write")
            assert max(lates) < STALL_S / 2, max(lates)
            assert await asyncio.to_thread(events.flush_night_logs, 30.0)
            served = await client.get("/api/logs",
                                      params={"night": night_key()})
            assert served.status_code == 200, served.text
            lines = [r["data"]["message"] for r in served.json()]
    finally:
        reset_active_provider()
    assert lines.count("mount unparked") == LINES, lines
    assert slow.calls >= LINES, "the stalled open was never reached"


# ------------------------------------------------------------- order and drain

def test_lines_queued_behind_a_stalled_write_are_written_in_order(
        bus, monkeypatch, tmp_path):
    """Fifty lines go in while the first write is held. They come back out of
    the file in the order they went in, none missing; and while the write was
    held the file did not exist yet, so the lines were on the queue and not
    written by the caller.

    RED under M1, observed (the first ``bus.log`` itself waits for the gate):

        E   AssertionError: 50 log lines took 2.03 s against a held write; the
            caller waited for the disk
        E   assert 2.027241700037848 < (2.0 / 2)

    RED under M4, observed:

        E   AssertionError: assert ['queued 49',...eued 44', ...] ==
            ['queued 0', ...ueued 5', ...]
        E     At index 0 diff: 'queued 49' != 'queued 0'
    """
    gate = threading.Event()
    slow = _SlowOpen(gate=gate)
    monkeypatch.setattr(events, "open", slow, raising=False)
    try:
        t0 = time.perf_counter()
        for i in range(50):
            bus.log("info", f"queued {i}", "probe")
        took = time.perf_counter() - t0
        assert took < GATE_MAX_S / 2, (
            f"50 log lines took {took:.2f} s against a held write; the "
            "caller waited for the disk")
        assert slow.started.wait(5.0), "the writer never reached the disk"
        assert not (tmp_path / "logs" / f"{night_key()}.jsonl").exists(), (
            "a line was written while the write was held")
    finally:
        gate.set()
    assert bus.night_log.flush(10.0) is True
    assert _written(bus) == [f"queued {i}" for i in range(50)]


def test_flush_gives_up_on_a_stalled_disk_and_the_queue_drains_when_it_returns(
        bus, monkeypatch):
    """Shutdown waits for the writer, but only so long: a disk that never
    answers must not hold the process. The lines stay queued, and a second
    flush, after the disk is back, finds all of them written.

    RED under M1: ``bus.log`` itself waits, so the flush is never reached:

        E   AssertionError: 20 log lines took 2.02 s against a held write
        E   assert 2.023031000047922 < (2.0 / 2)
    """
    gate = threading.Event()
    slow = _SlowOpen(gate=gate)
    monkeypatch.setattr(events, "open", slow, raising=False)
    try:
        t0 = time.perf_counter()
        for i in range(20):
            bus.log("info", f"drain {i}", "probe")
        took = time.perf_counter() - t0
        assert took < GATE_MAX_S / 2, (
            f"20 log lines took {took:.2f} s against a held write")
        assert bus.night_log.flush(timeout=0.2) is False, (
            "flush claimed the queue was drained while the disk was held")
    finally:
        gate.set()
    assert events.flush_night_logs(10.0) is True
    assert _written(bus) == [f"drain {i}" for i in range(20)]


def test_a_clean_exit_writes_what_is_still_queued(tmp_path):
    """The process ends with lines still queued behind a slow disk: the exit
    hook drains them, so a clean stop loses nothing. Run in a child process,
    because the hook only runs at interpreter exit.

    RED under M2, observed:

        E   AssertionError:
        E   assert [] == ['exit probe ...exit probe 4']
        E     Right contains 5 more items, first extra item: 'exit probe 0'
    """
    server_dir = Path(events.__file__).resolve().parents[1]
    script = tmp_path / "exit_probe.py"
    script.write_text(textwrap.dedent('''
        import builtins, time
        from astrodeck import events

        def slow(file, mode="r", *a, **k):
            if "a" in mode:
                time.sleep(0.3)
            return builtins.open(file, mode, *a, **k)

        events.open = slow
        for i in range(5):
            events.bus.log("info", f"exit probe {i}", "probe")
        # ends here, with the writer still on the first line
    '''), encoding="utf-8")
    cap = tmp_path / "cap"
    env = {**os.environ,
           "PYTHONPATH": str(server_dir),
           "ASTRODECK_CAPTURE_DIR": str(cap),
           "ASTRODECK_CONFIG_DIR": str(tmp_path / "config")}
    env.pop("ASTRODECK_LOG_PERSIST", None)
    run = subprocess.run([sys.executable, str(script)], env=env,
                         cwd=str(tmp_path), capture_output=True, text=True,
                         timeout=180)
    assert run.returncode == 0, run.stderr[-2000:]
    rows: list[str] = []
    for path in sorted((cap / "logs").glob("*.jsonl")):
        rows += [json.loads(line)["data"]["message"]
                 for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows == [f"exit probe {i}" for i in range(5)], run.stderr[-2000:]


# ------------------------------------------------------ where a line is written

def test_a_line_goes_to_the_root_that_was_current_when_it_was_published(
        bus, monkeypatch, tmp_path):
    """The directory is resolved live, as before, but when the line is queued:
    the capture root can move (a test's fixture teardown; a root change)
    before the writer thread reaches the line, and the line must not follow
    it. Moved late, a stale line would be written, and the prune would run,
    in a directory the process had left.

    RED under M3, observed (the line went to the second root, so the first
    root's file holds only the line that was already being written):

        E   AssertionError: the line followed the capture root: [...]
        E   assert 'queued under the first root' in '{"type":"log","data":
            {"level":"info","message":"published under the first root",
            "source":"probe"},"ts":...}'
    """
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.setattr(hubmod, "CAPTURE_DIR", first)
    gate = threading.Event()
    slow = _SlowOpen(gate=gate)
    monkeypatch.setattr(events, "open", slow, raising=False)
    try:
        bus.log("info", "published under the first root", "probe")
        assert slow.started.wait(5.0)
        bus.log("info", "queued under the first root", "probe")
        monkeypatch.setattr(hubmod, "CAPTURE_DIR", second)
    finally:
        gate.set()
    assert bus.night_log.flush(10.0) is True
    name = f"{night_key()}.jsonl"
    in_first = ((first / "logs" / name).read_text(encoding="utf-8")
                if (first / "logs" / name).exists() else "")
    assert "queued under the first root" in in_first, (
        "the line followed the capture root: "
        f"{sorted(p.as_posix() for p in tmp_path.rglob('*.jsonl'))}")
    assert not (second / "logs").exists()


def test_a_night_roll_in_the_queue_writes_each_night_to_its_own_file(
        bus, monkeypatch, tmp_path):
    """Lines for two nights are queued behind one held write. The roll
    (``mkdir``, then the prune) happens on the writer thread in queue order:
    the first night's lines are in the first night's file before the second
    night's line is written, and each file holds its own night only.

    RED under M4, observed:

        E   AssertionError: assert ['night one, ...t one, first'] ==
            ['night one, ... one, second']
        E     At index 0 diff: 'night one, second' != 'night one, first'
    """
    evening = time.mktime((2026, 7, 26, 22, 0, 0, 0, 0, -1))
    next_evening = time.mktime((2026, 7, 27, 22, 0, 0, 0, 0, -1))
    clock = {"t": evening}
    monkeypatch.setattr(events, "_wall", lambda: clock["t"])
    gate = threading.Event()
    slow = _SlowOpen(gate=gate)
    monkeypatch.setattr(events, "open", slow, raising=False)
    try:
        bus.log("info", "night one, first", "probe")
        bus.log("info", "night one, second", "probe")
        clock["t"] = next_evening
        bus.log("info", "night two, first", "probe")
    finally:
        gate.set()
    assert bus.night_log.flush(10.0) is True
    assert _written(bus, "2026-07-26") == ["night one, first",
                                           "night one, second"]
    assert _written(bus, "2026-07-27") == ["night two, first"]


# --------------------------------------------------------------- a full queue

def test_a_full_queue_drops_the_newest_counts_them_and_says_so(
        bus, monkeypatch):
    """Behind a disk that does not answer the queue fills. The newest lines
    are not queued, and the count is not lost: once the writer has drained,
    the next publish carries one line saying how many. Not before: told into
    a full queue the notice would be dropped, counted and told again on every
    publish for as long as the disk stalled.

    RED under M1 (nothing queues, so nothing is ever dropped), observed:

        E   AssertionError: the queue never filled, so the case proved nothing
        E   assert 0 > 0

    RED under M4, observed:

        E   AssertionError: what was kept is the OLDEST lines, in order
        E   assert ['flood 4', '...1', 'flood 0'] == ['flood 0', '...3', 'flood 4']
    """
    monkeypatch.setattr(events, "NIGHTLOG_QUEUE_MAX", 5)
    gate = threading.Event()
    slow = _SlowOpen(gate=gate)
    monkeypatch.setattr(events, "open", slow, raising=False)
    total = 20
    try:
        for i in range(total):
            bus.log("info", f"flood {i}", "probe")
        assert not [m for m in _ring(bus) if "fell behind" in m], (
            "the notice was told into a full queue")
    finally:
        gate.set()
    assert bus.night_log.flush(10.0) is True

    written = [m for m in _written(bus) if m.startswith("flood ")]
    lost = total - len(written)
    assert lost > 0, "the queue never filled, so the case proved nothing"
    assert written == [f"flood {i}" for i in range(len(written))], (
        "what was kept is the OLDEST lines, in order")

    bus.log("info", "after the flood", "probe")     # carries the notice
    assert bus.night_log.flush(10.0) is True
    notices = [m for m in _ring(bus) if "fell behind" in m]
    assert len(notices) == 1 and f"{lost} lines" in notices[0], notices
    assert notices[0] in _written(bus), "the notice is a log line, in the file"
    bus.log("info", "and again", "probe")
    assert len([m for m in _ring(bus) if "fell behind" in m]) == 1, (
        "the count was told twice")
