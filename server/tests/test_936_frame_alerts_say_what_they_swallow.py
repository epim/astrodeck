# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#936: `SequenceEngine._frame_alerts_tick` says what its guard catches.

THE DEFECT. The per-frame dead-man ping and heartbeat were each wrapped in
``except Exception: pass``, the shape #811 found on the dispatcher's timer. The
docstring called both calls "already hardened never to raise", which is the
claim #811 showed is not a safeguard: whatever escaped (``get_config()`` at the
top of the inline ping, an enqueue that raised, a change nobody has written
yet) was lost without a trace, and the only evidence of the failure was the
thing that failed.

THE FIX. Each guard now hands what it caught to the dispatcher's own latch
(`AlertDispatcher.report_failure`, the mechanism #811 added): one warning per
DISTINCT exception type, the type's name and never its text (the text can quote
the dead-man url, whose path is the ping secret, #694, or a sink's token), and a
stage that works again forgets its set. The frame path has stages of its own
(`STAGE_FRAME_*`), so a heartbeat that works there cannot re-arm what the
timer's heartbeat already said.

MUTANTS RUN, each from a byte backup restored byte-identically (md5sum
compared); the counts are over this file's 12 cases.

  M0  the defect restored: both guards a ``pass`` again. 11 failed, e.g. the
      ping case `AssertionError: []` (nothing said), the works-again cases
      `AssertionError: ([0, 1, 2, 3, 4, 5], [])`, and the real run on the
      simulator, which shoots every frame and says nothing.
  M1  the dead-man arm of `_frame_alerts_tick` alone. 7 failed.
  M2  the heartbeat arm alone. 7 failed.
  M3  no latch (``if kind in said:`` -> ``if False:`` in `_say_failure`). 9
      failed: the same warning once per frame.
  M4  a stage that works again keeps its set (`report_recovery` -> no-op). 2
      failed, the two works-again cases.
  M5  the frame path shares the timer's stage names (`STAGE_FRAME_*` equal to
      `_STAGE_*`). 1 failed, the independence case.
  M6  the exception's text in the line (``{kind}`` -> ``{kind}: {exc}``). 6
      failed, the line carried the ping secret.
  M7  `bus.log` unguarded in `_say_failure` (``except Exception`` ->
      ``except ZeroDivisionError``). 1 failed, the bus-cannot-log case.
  M8  the heartbeat's message build moved outside its guard. 1 failed, the
      message-cannot-be-built case.
"""
from __future__ import annotations

import asyncio
import logging

import httpx
import pytest

import astrodeck.alerting as alerting_mod
from _group_harness import Night, group_hub, group_store, single
from astrodeck.alerting import AlertDispatcher
from astrodeck.config import AppConfig
from astrodeck.events import EventBus
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import SequencePlan

#: A healthchecks-shaped path: the PATH is the ping secret (#694), so a line
#: that carries any of these pieces has leaked it.
_SECRET = "3f2a9c1e-0000-4abc-9def-secretuuid"
_SECRET_URL = f"https://user:pw@hc.example.org:8443/ping/{_SECRET}?rid=RUNSECRET"
_SECRET_PIECES = ("3f2a9c1e", "secretuuid", "/ping/", "hc.example.org",
                  "RUNSECRET", "user:pw", "SEKRET-TOKEN")


def _warnings(sub) -> list[str]:
    out = []
    while not sub.empty():
        ev = sub.get_nowait()
        if ev.type == "log" and ev.data.get("level") == "warning":
            out.append(ev.data["message"])
    return out


def _leaked(line: str) -> list[str]:
    return [p for p in _SECRET_PIECES if p in line]


def _dispatcher(get_config=None):
    """A dispatcher with no ``run()`` pipeline, whose bus is its own, and the
    subscription that reads what it says."""
    bus = EventBus(persist=False)
    disp = AlertDispatcher(bus, get_config or (lambda: AppConfig()))
    disp._client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda req: httpx.Response(200)))
    return disp, bus, bus.subscribe()


def _bare_engine(disp: AlertDispatcher) -> SequenceEngine:
    """A `SequenceEngine` carrying only what `_frame_alerts_tick` and
    `_get_dispatcher` read (the `__new__` + hand-set-attrs pattern of
    test_w1_frame_alerts_off_the_frame_path.py)."""
    eng = SequenceEngine.__new__(SequenceEngine)
    eng.dispatcher = disp
    eng.hub = None
    eng._plan = None
    eng._frames_done = 3
    return eng


async def _ticks(eng: SequenceEngine, n: int) -> None:
    """``n`` frames' ticks. A tick that raised would end the loop here, as it
    would end the capture loop."""
    for _ in range(n):
        await eng._frame_alerts_tick()


# ---------------------------------------------------------------- the ping

async def test_a_dead_man_ping_that_raises_is_said_once_and_the_frame_goes_on(
        monkeypatch):
    disp, _bus, sub = _dispatcher()
    eng = _bare_engine(disp)
    pings: list[int] = []
    beats: list[str] = []

    async def boom():
        pings.append(1)
        raise RuntimeError(f"the monitor object blew up: {_SECRET_URL}")

    async def beat(message):
        beats.append(message)

    monkeypatch.setattr(disp, "deadman_ping", boom)
    monkeypatch.setattr(disp, "emit_heartbeat", beat)
    await _ticks(eng, 4)
    assert len(pings) == 4 and len(beats) == 4, (pings, beats)
    said = _warnings(sub)
    assert len(said) == 1, said
    assert "dead-man ping" in said[0] and "RuntimeError" in said[0], said
    assert not _leaked(said[0]), f"the line carried a secret: {said[0]}"
    await disp._client.aclose()


async def test_a_heartbeat_that_raises_is_said_once_and_the_frame_goes_on(
        monkeypatch):
    disp, _bus, sub = _dispatcher()
    eng = _bare_engine(disp)
    pings: list[int] = []
    beats: list[int] = []

    async def ping():
        pings.append(1)

    async def boom(message):
        beats.append(1)
        raise KeyError(f"sink token SEKRET-TOKEN missing from {_SECRET_URL}")

    monkeypatch.setattr(disp, "deadman_ping", ping)
    monkeypatch.setattr(disp, "emit_heartbeat", boom)
    await _ticks(eng, 4)
    assert len(pings) == 4 and len(beats) == 4, (pings, beats)
    said = _warnings(sub)
    assert len(said) == 1, said
    assert "heartbeat" in said[0] and "KeyError" in said[0], said
    assert not _leaked(said[0]), f"the line carried a secret: {said[0]}"
    await disp._client.aclose()


async def test_both_calls_failing_say_one_line_each(monkeypatch):
    """The two stages are two latches: a ping that fails does not stand in for
    the heartbeat that fails beside it."""
    disp, _bus, sub = _dispatcher()
    eng = _bare_engine(disp)

    async def boom_ping():
        raise RuntimeError("a")

    async def boom_beat(message):
        raise RuntimeError("a")

    monkeypatch.setattr(disp, "deadman_ping", boom_ping)
    monkeypatch.setattr(disp, "emit_heartbeat", boom_beat)
    await _ticks(eng, 3)
    said = _warnings(sub)
    assert len(said) == 2, said
    assert sum("dead-man ping" in s for s in said) == 1, said
    assert sum("heartbeat" in s for s in said) == 1, said
    await disp._client.aclose()


async def test_a_message_that_cannot_be_built_is_said_and_does_not_end_the_frame(
        monkeypatch):
    """The heartbeat's text is built inside the guard (the plan it reads can be
    mid-edit): a plan whose ``total_frames`` raises is the heartbeat's failure,
    said, and the ping before it has already gone."""
    disp, _bus, sub = _dispatcher()
    eng = _bare_engine(disp)
    pings: list[int] = []

    class _Plan:
        name = "night"

        def total_frames(self):
            raise ZeroDivisionError("a plan nobody finished")

    async def ping():
        pings.append(1)

    monkeypatch.setattr(disp, "deadman_ping", ping)
    eng._plan = _Plan()
    await _ticks(eng, 3)
    said = _warnings(sub)
    assert len(pings) == 3, pings
    assert len(said) == 1 and "ZeroDivisionError" in said[0], said
    await disp._client.aclose()


# ------------------------------------------------------- the latch's rules

async def test_a_different_failure_is_said_again_and_a_repeat_is_not(
        monkeypatch):
    """One line per DISTINCT failure: A, A, B, B, A is two lines."""
    disp, _bus, sub = _dispatcher()
    eng = _bare_engine(disp)
    errors = [RuntimeError("a"), RuntimeError("a"), KeyError("b"), KeyError("b"),
              RuntimeError("a")]
    calls: list[int] = []

    async def flaky():
        n = len(calls)
        calls.append(n)
        raise errors[n]

    monkeypatch.setattr(disp, "deadman_ping", flaky)
    await _ticks(eng, len(errors))
    said = _warnings(sub)
    assert len(said) == 2, said
    assert "RuntimeError" in said[0] and "KeyError" in said[1], said
    await disp._client.aclose()


@pytest.mark.parametrize("method,word", [("deadman_ping", "dead-man ping"),
                                         ("emit_heartbeat", "heartbeat")])
async def test_a_call_that_works_again_says_its_next_failure_afresh(
        method, word, monkeypatch):
    """The latch is 'nothing said yet', not 'never again': a call that fails,
    works, and fails again is two lines, so the second night's trouble is not
    lost behind the first night's."""
    disp, _bus, sub = _dispatcher()
    eng = _bare_engine(disp)
    calls: list[int] = []

    async def alternates(*args):
        n = len(calls)
        calls.append(n)
        if n % 2 == 0:
            raise ValueError("fails on every other frame")

    monkeypatch.setattr(disp, method, alternates)
    await _ticks(eng, 6)
    # Both methods are patched in turn: the other one is the real, working
    # bare-dispatcher call and never raises.
    said = _warnings(sub)
    assert len(calls) == 6 and len(said) == 3, (calls, said)
    assert all(word in s and "ValueError" in s for s in said), said
    await disp._client.aclose()


async def test_a_bus_that_cannot_take_the_line_does_not_end_the_frame(
        monkeypatch, caplog):
    """Saying the failure must not be able to break the frame it reports on: a
    ``bus.log`` that raises goes to the module's logger instead, once."""
    disp, bus, _sub = _dispatcher()
    eng = _bare_engine(disp)

    async def boom():
        raise RuntimeError(f"the monitor object blew up: {_SECRET_URL}")

    def broken(*args, **kwargs):
        raise OSError("the night log is gone")

    monkeypatch.setattr(disp, "deadman_ping", boom)
    monkeypatch.setattr(bus, "log", broken)
    with caplog.at_level(logging.WARNING, logger="astrodeck.alerting"):
        await _ticks(eng, 3)
    lines = [r.getMessage() for r in caplog.records
             if r.name == "astrodeck.alerting"]
    assert len(lines) == 1 and "RuntimeError" in lines[0], lines
    assert not _leaked(lines[0]), f"the fallback line carried a secret: {lines[0]}"
    await disp._client.aclose()


async def test_a_frame_path_call_that_works_does_not_rearm_the_timers_latch(
        monkeypatch):
    """The frame path's stages are its own. The timer's heartbeat said its
    ValueError; the frame path's heartbeat then works (and forgets ITS stage);
    the timer's next ValueError must still be a repeat, not news, or one
    failing path would say itself again every time the other one worked."""
    disp, _bus, sub = _dispatcher()
    eng = _bare_engine(disp)

    disp._say_failure(alerting_mod._STAGE_HEARTBEAT, ValueError("timer"))
    assert len(_warnings(sub)) == 1
    await _ticks(eng, 2)                    # the frame path's heartbeat works
    disp._say_failure(alerting_mod._STAGE_HEARTBEAT, ValueError("timer again"))
    assert _warnings(sub) == []
    await disp._client.aclose()


# ------------------------------------------- the real dispatcher, unpatched

async def test_a_real_dispatcher_whose_config_cannot_be_read_is_said_per_stage():
    """No monkeypatched dispatcher method: a bare dispatcher whose config store
    raises is the ``get_config()`` at the top of the inline ping that #811's
    docstring names, and the one at the top of the inline heartbeat's sink
    lookup. Both escape into the engine's guard, which used to drop them."""
    def unreadable():
        raise RuntimeError(f"config unreadable: {_SECRET_URL} SEKRET-TOKEN")

    disp, _bus, sub = _dispatcher(unreadable)
    eng = _bare_engine(disp)
    await _ticks(eng, 3)
    said = _warnings(sub)
    assert len(said) == 2, said
    assert sum("dead-man ping" in s for s in said) == 1, said
    assert sum("heartbeat" in s for s in said) == 1, said
    assert all("RuntimeError" in s for s in said), said
    assert not [s for s in said if _leaked(s)], said
    await disp._client.aclose()


async def test_a_live_dispatcher_whose_enqueue_raises_is_said_and_the_tick_returns():
    """Once ``run()`` has started the pipeline the heartbeat only enqueues; an
    enqueue that raises is the synchronous escape the pipelined ping task's
    own report (#811) cannot see, because it happens on the caller's task."""
    disp, _bus, sub = _dispatcher()
    disp._outbox_ready = asyncio.Event()    # what run() sets: the pipeline is live

    def full_of_secrets(alert):
        raise OSError(f"outbox broke on {_SECRET_URL}")

    disp._enqueue = full_of_secrets
    eng = _bare_engine(disp)
    try:
        await asyncio.wait_for(_ticks(eng, 3), timeout=5.0)
    finally:
        if disp._deadman_task is not None:
            disp._deadman_task.cancel()
        await disp._client.aclose()
    said = _warnings(sub)
    assert len(said) == 1 and "heartbeat" in said[0] and "OSError" in said[0], said
    assert not _leaked(said[0]), said[0]


# ------------------------------------------------- the real engine, a night

async def test_a_night_whose_frame_alerts_raise_shoots_every_frame_and_says_so_once(
        group_hub, monkeypatch):
    """The real frame loop on the clocked simulator, a dispatcher injected the
    way the app wires it (``engine.dispatcher``), both calls raising on every
    frame. Every frame still lands, the run finishes, and the night hears of
    each failure once."""
    plan = SequencePlan(
        name="frame alerts", guide=False, dither_every=0, autofocus_every=0,
        meridian_flip=False, park_when_done=False, warm_cooler_when_done=False,
        recover_guiding=False, targets=[single("Solo", count=3)])
    disp, _bus, sub = _dispatcher()
    pings: list[int] = []
    beats: list[int] = []

    async def boom_ping():
        pings.append(1)
        raise RuntimeError(f"the monitor object blew up: {_SECRET_URL}")

    async def boom_beat(message):
        beats.append(1)
        raise KeyError(f"sink token SEKRET-TOKEN missing from {_SECRET_URL}")

    monkeypatch.setattr(disp, "deadman_ping", boom_ping)
    monkeypatch.setattr(disp, "emit_heartbeat", boom_beat)
    night = Night(group_hub, monkeypatch)
    night.engine.dispatcher = disp
    try:
        night.done = await night.run(plan, wall_s=60.0)
    finally:
        await night.close()
        await disp._client.aclose()
    assert night.done, night.lines[-4:]
    assert night.shots() == [("Solo", "L")] * 3, night.shots()
    assert len(pings) >= 3 and len(beats) >= 3, (pings, beats)
    said = _warnings(sub)
    assert len(said) == 2, said
    assert sum("dead-man ping" in s and "RuntimeError" in s for s in said) == 1, said
    assert sum("heartbeat" in s and "KeyError" in s for s in said) == 1, said
    assert not [s for s in said if _leaked(s)], said
