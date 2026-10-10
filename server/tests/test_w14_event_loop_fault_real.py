# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP-96 (#496, code part): the Windows proactor fault reaches the night log,
and the matcher is pinned to the exception CPython really raises.

WP-54 (``test_w8_event_loop_fault.py``) caught the fault, logged it to the
``astrodeck`` logger and turned it into exit code 1, but proved it only with a
HAND-BUILT exception. Two things were left.

* The line went to stderr only. On the rig the supervisor is started detached,
  so stderr may be lost with the process, and the durable record of a night is
  ``captures/logs/<night>.jsonl``. ``_record_fault_in_the_night_log`` puts one
  line there.
* A hand-built exception only proves the matcher against what someone typed
  from the issue. The real-fault tests below run ``asyncio.run`` on a proactor
  loop that has been handed a completion packet for an address its overlapped
  cache never saw. CPython's own ``IocpProactor._poll`` then raises the KeyError,
  its handler calls ``CloseHandle`` on the packet's key, and the OSError
  [WinError 6] comes out of ``run_forever``: the shape #496 recorded, made by
  the interpreter. The key is ODD (0x7777): Windows handle values are multiples
  of four, so ``CloseHandle`` can never close a live handle of this process.

The real-fault tests are WINDOWS ONLY (the proactor does not exist elsewhere).
The dev box is Windows and runs the full suite before every commit; CI's server
job is Linux, so they skip there and the rest of this file (the truncation, the
never-masks-the-exit-code case and the supervisor contract) still runs. Each one
asserts the RAW fault first, so a CPython that fixes the bug turns them red with
a message saying why, instead of passing without ever meeting the fault.

#496 stays open: the daylight check that the rig's supervisor
(``supervisor/supervisor.py``) relaunches the server after a child exit 1 is rig
work, not a test.

MUTATIONS RUN, 2026-10-07, each from a byte backup of this worktree's
server/astrodeck/__main__.py, restored and sha256-verified afterwards, the
mutant text grepped to be gone:

  M1 "the matcher reads the wrong chain" -- ``isinstance(exc.__context__,
  KeyError)`` changed to ``isinstance(exc.__context__, OSError)``. 6 failed
  (4 here, 2 in test_w8_event_loop_fault.py), among them
  test_the_matcher_says_yes_to_the_fault_cpython_really_raises:
      AssertionError: assert False is True
       +  where False = <function _is_windows_proactor_overlapped_fault ...>(
          OSError(9, 'The handle is invalid', None, 6, None))
  The real fault is the argument there, so the failure shows the matcher
  refusing what CPython raises; the hand-built tests see this mutant too.

  M2 "the night-log call is gone" -- the ``_record_fault_in_the_night_log(exc)``
  call removed. 2 failed:
  test_the_real_fault_is_caught_logged_once_and_the_night_log_hears_of_it
      AssertionError: expected exactly one night-log line from 'server', got []
  test_the_line_is_in_the_night_file_even_with_a_subscriber_on_the_dead_loop
      AssertionError: expected one #496 line in the night file, got 0

  M3 "the fault exits 0" -- ``EXIT_EVENT_LOOP_FAULT = 1`` set to ``= 0``, which
  the supervisor reads as a clean stop and does not relaunch. 3 failed (2 here,
  1 in test_w8):
  test_the_fault_exit_code_is_one_the_supervisor_relaunches_on
      assert 0 not in (0, 92)
  test_the_real_fault_is_caught_logged_once_and_the_night_log_hears_of_it
      AssertionError: the supervisor stops for good on 0

  M4 "a logging failure escapes" -- the helper's ``except Exception`` changed
  to ``except KeyboardInterrupt``. 1 failed,
  test_a_logging_failure_never_masks_the_exit_code:
      RuntimeError: the night log is on fire

  M5 "the line keeps the head of the traceback" -- ``trace[-N:]`` changed to
  ``trace[:N]``. 1 failed, test_the_traceback_in_the_line_is_its_tail_not_its_head:
      AssertionError: assert 'THE_END_OF_THE_CHAIN' in 'Windows event-loop fault
      (#496): ... AAAA...'
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys

import pytest

import astrodeck.__main__ as cli
from astrodeck import events
from supervisor import protocol as P
from test_supervisor import FakeProc, _make_sup
from test_w8_event_loop_fault import _FakeServerRaises, _raise_the_496_fault

try:
    import _overlapped
except ImportError:                     # not Windows
    _overlapped = None

# The CPython this repo targets can post a packet to a proactor's port with
# ``_overlapped.PostQueuedCompletionStatus``. Without it there is no honest way
# to provoke the fault, and a fake would be the very thing this file replaces.
real_fault = pytest.mark.skipif(
    sys.platform != "win32"
    or not hasattr(_overlapped, "PostQueuedCompletionStatus"),
    reason="needs the Windows proactor loop and _overlapped."
           "PostQueuedCompletionStatus (CI's server job is Linux)")

#: A completion key no live handle can have: handles are multiples of four.
_ODD_KEY = 0x7777
#: An overlapped address the proactor's cache has never held.
_UNKNOWN_ADDRESS = 0x1234


async def _poison_the_proactor(on_loop=None) -> None:
    loop = asyncio.get_running_loop()
    if on_loop is not None:
        on_loop()
    _overlapped.PostQueuedCompletionStatus(
        loop._proactor._iocp, 0, _ODD_KEY, _UNKNOWN_ADDRESS)
    # The fault comes on the loop's next poll, ms away, whatever the load: a
    # queued packet is returned at once and the timer is not looked at first.
    # This sleep is only the bound for an interpreter that no longer faults.
    await asyncio.sleep(0.5)


class _RealLoopServer:
    """Stands in for ``uvicorn.Server``: ``run()`` is ``asyncio.run`` of a main
    that poisons its own proactor, which is what uvicorn's ``server.run()`` is
    to the loop."""

    def __init__(self, on_loop=None) -> None:
        self._on_loop = on_loop

    def run(self) -> None:
        asyncio.run(_poison_the_proactor(self._on_loop))


def _the_raw_fault() -> BaseException | None:
    """What escapes the poisoned loop with NO wrapper, or None if nothing."""
    try:
        _RealLoopServer().run()
    except BaseException as exc:        # noqa: BLE001 - the point is to look
        return exc
    return None


def _assert_the_raw_fault_is_the_one_in_496(exc: BaseException | None) -> None:
    assert exc is not None, (
        "the poisoned proactor ran to the end with no fault: this CPython no "
        "longer raises #496's OSError, so the rest of this test would pass "
        "without meeting it. A later 3.12.x may have fixed the bug: retire the "
        "handler or point this guard at the new shape, do not delete it blind.")
    assert isinstance(exc, OSError) and getattr(exc, "winerror", None) == 6, (
        f"expected OSError [WinError 6], got {type(exc).__name__}: {exc}")
    assert isinstance(exc.__context__, KeyError), (
        "expected the OSError chained onto the overlapped cache's KeyError, "
        f"got __context__={exc.__context__!r}")


def _night_file_events() -> list[dict]:
    events.flush_night_logs()                   # the file is written off-thread
    out: list[dict] = []
    for path in sorted(events.NightLogWriter.dir().glob("*.jsonl")):
        for raw in path.read_text(encoding="utf-8").splitlines():
            out.append(json.loads(raw))
    return out


# ---- the real fault -----------------------------------------------------------

@real_fault
def test_the_matcher_says_yes_to_the_fault_cpython_really_raises():
    raw = _the_raw_fault()
    _assert_the_raw_fault_is_the_one_in_496(raw)
    assert cli._is_windows_proactor_overlapped_fault(raw) is True


@real_fault
def test_the_real_fault_is_caught_logged_once_and_the_night_log_hears_of_it(
        caplog, bus_lines):
    _assert_the_raw_fault_is_the_one_in_496(_the_raw_fault())

    with caplog.at_level(logging.ERROR, logger="astrodeck"):
        exit_code = cli._run_server_with_fault_handling(_RealLoopServer())

    assert exit_code == cli.EXIT_EVENT_LOOP_FAULT
    assert exit_code != 0, "the supervisor stops for good on 0"
    records = [r for r in caplog.records if r.name == "astrodeck"]
    assert len(records) == 1, (
        f"expected exactly one 'astrodeck' record, got {len(records)}")
    assert records[0].exc_info is not None
    assert records[0].exc_info[0] is OSError

    said = [(lvl, msg) for lvl, msg, src in bus_lines if src == "server"]
    assert len(said) == 1, (
        f"expected exactly one night-log line from 'server', got {said!r}")
    level, message = said[0]
    assert level == "error"
    assert "event-loop fault" in message
    assert "#496" in message
    assert f"exit code {cli.EXIT_EVENT_LOOP_FAULT}" in message
    assert "supervisor" in message and "relaunch" in message
    assert "WinError 6" in message, (
        "the line must carry the traceback's end, the OSError itself")


@real_fault
def test_the_line_is_in_the_night_file_even_with_a_subscriber_on_the_dead_loop(
        monkeypatch):
    """No ``bus_lines`` here: the real ``bus.log`` runs and the jsonl is read
    back. A subscriber made on the loop that then dies is what a connected
    browser is at that moment; its closed loop must not stop the line."""
    monkeypatch.setattr(events.bus, "night_log", events.NightLogWriter())
    subs: list = []
    server = _RealLoopServer(on_loop=lambda: subs.append(events.bus.subscribe()))
    try:
        exit_code = cli._run_server_with_fault_handling(server)
    finally:
        for sub in subs:
            events.bus.unsubscribe(sub)

    assert exit_code == cli.EXIT_EVENT_LOOP_FAULT
    assert len(subs) == 1, "the subscriber was never made on the faulting loop"
    ours = [e for e in _night_file_events()
            if e["type"] == "log"
            and e["data"].get("source") == "server"
            and "#496" in e["data"].get("message", "")]
    assert len(ours) == 1, (
        f"expected one #496 line in the night file, got {len(ours)}")
    data = ours[0]["data"]
    assert data["level"] == "error"
    assert "OSError: [WinError 6]" in data["message"]


# ---- the line itself, on every platform ---------------------------------------

def test_the_traceback_in_the_line_is_its_tail_not_its_head(bus_lines):
    """A long chain keeps its END, where the OSError is, and the line stays
    bounded: a night log is read by a person, line by line."""
    exc = OSError("A" * 6000 + "THE_END_OF_THE_CHAIN")
    cli._record_fault_in_the_night_log(exc)

    (level, message, source), = bus_lines
    assert (level, source) == ("error", "server")
    assert "THE_END_OF_THE_CHAIN" in message
    assert "A" * 3500 not in message
    assert len(message) < 3000 + 800, len(message)


def test_a_logging_failure_never_masks_the_exit_code(monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("the night log is on fire")

    monkeypatch.setattr(events.bus, "log", broken)

    exit_code = cli._run_server_with_fault_handling(
        _FakeServerRaises(_raise_the_496_fault))

    assert exit_code == cli.EXIT_EVENT_LOOP_FAULT


# ---- the exit-code contract with the supervisor -------------------------------

def test_the_fault_exit_code_is_one_the_supervisor_relaunches_on(tmp_path):
    """The supervisor stops for good on EXIT_STOP and swaps versions on
    EXIT_APPLY_UPDATE; anything else is a crash it relaunches, the SAME
    version, after a backoff. The fault must be in that last class, or the
    exit meant to be recovered from is the one that ends the night."""
    assert cli.EXIT_EVENT_LOOP_FAULT not in (P.EXIT_STOP, P.EXIT_APPLY_UPDATE)

    P.Layout(tmp_path).set_current("0.1.0")
    sleeps: list[float] = []
    procs = [FakeProc(cli.EXIT_EVENT_LOOP_FAULT), FakeProc(P.EXIT_STOP)]
    sup = _make_sup(tmp_path, procs, health=lambda v: True, sleeps=sleeps)
    kind, version = sup.run_forever(max_iterations=6)

    assert kind == "stopped" and version == "0.1.0"
    assert sup.launches == ["0.1.0", "0.1.0"], (
        "the supervisor must relaunch the same version after the fault")
    assert sleeps and sleeps[0] > 0, "the relaunch must back off first"
