# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP-54 (#496, backlog ruling D-12, owner-approved 2026-09-30): the Windows
proactor fault that killed a probe server mid-walk.

#496 recorded an OSError ``[WinError 6] The handle is invalid`` escaping
straight out of ``run_forever()`` (through uvicorn's ``server.run()``), while
it was unwinding a ``KeyError`` that CPython's ``IocpProactor._poll`` raised
popping a stale completion-port address from its overlapped cache. That
``KeyError`` handler itself calls ``_winapi.CloseHandle(key)``; when the
handle is already gone, THAT raises the OSError, chained via
``__context__``. Nothing inside ``_poll`` catches it, so it is a bare,
unhandled exception by the time it reaches ``_cmd_run`` -- never a callback
exception an ``asyncio`` loop exception handler would see.

D-12 keeps the proactor loop (the server uses ``asyncio.create_subprocess_exec``
in ``astrodeck/solve/astap.py``, which the selector loop cannot run on
Windows) and asks for this ONE fault shape to be caught narrowly, logged
once with its traceback, and turned into a deliberate non-zero exit so the
rig's supervisor (scripts/lib/RigRestart.ps1) restarts the server. Any OTHER
exception out of running the server -- including an OSError that merely
looks similar -- must keep today's behaviour: propagate unchanged.

These tests drive the matcher and the run-wrapper directly with a fake
server/fake loop (per WP-54's instructions: never by killing a real server).

MUTATIONS RUN, 2026-10-02, each in a byte backup of this worktree's
server/astrodeck/__main__.py, restored and sha256-verified afterwards (grepped
to confirm the mutant text was gone):

  M1 "the fault is never caught" -- ``_run_server_with_fault_handling``'s
  ``try/except OSError`` block deleted, leaving a bare ``server.run(); return
  0`` (restores the pre-fix behaviour: the exact #496 fault tears the
  process down as an unhandled exception instead of a logged, deliberate
  exit). 1 failed, test_the_496_fault_is_caught_logged_once_and_exits_nonzero,
  the synthetic fault propagated straight out of the call instead of being
  caught:
      OSError: [WinError 6] The handle is invalid
      tests\\test_w8_event_loop_fault.py:79: OSError

  M2 "the match is widened to any OSError sharing this winerror" -- the
  matcher's final ``return isinstance(exc.__context__, KeyError)`` line
  changed to ``return True`` (so any OSError with ``winerror == 6``, even
  with no KeyError behind it, is treated as the #496 fault). 1 failed,
  test_an_oserror_winerror_6_without_a_keyerror_context_propagates_unchanged:
      Failed: DID NOT RAISE <class 'OSError'>
  (the look-alike OSError -- same winerror, no KeyError chained onto it --
  was swallowed and turned into a logged exit-1 instead of propagating,
  which would mask a different bug as the Windows proactor fault)

  M3 "the log call drops the traceback" -- the ``exc_info=exc`` keyword
  removed from the ``logging.getLogger("astrodeck").error(...)`` call. 1
  failed, test_the_496_fault_is_caught_logged_once_and_exits_nonzero:
      AssertionError: D-12 requires the fault to be logged WITH its traceback
      assert None is not None
  (``record.exc_info`` was ``None``, i.e. the log entry no longer carried
  the traceback D-12 requires)
"""
from __future__ import annotations

import logging

import pytest

import astrodeck.__main__ as cli


def _raise_the_496_fault() -> None:
    """Reproduce #496's exact exception shape: an OSError [WinError 6]
    chained (via implicit ``__context__``, not ``raise ... from``) onto a
    KeyError, exactly as CPython's ``IocpProactor._poll`` produces it when
    ``_winapi.CloseHandle`` fails on an already-invalid handle while it is
    unwinding the KeyError from the overlapped cache miss."""
    try:
        raise KeyError(1710290246384)  # the overlapped-cache address, per #496
    except KeyError:
        fault = OSError()
        fault.winerror = 6
        fault.strerror = "The handle is invalid"
        raise fault


class _FakeServerRaises:
    """Stands in for ``uvicorn.Server``: its ``run()`` is the one call
    ``_run_server_with_fault_handling`` wraps. A fake, not a real server or
    loop, per WP-54's instructions."""

    def __init__(self, raiser) -> None:
        self._raiser = raiser

    def run(self) -> None:
        self._raiser()


class _FakeServerReturns:
    """A server whose ``run()`` returns normally (the ordinary shutdown
    path), so the wrapper must not alter today's behaviour for a clean
    exit."""

    def run(self) -> None:
        return None


def test_the_496_fault_is_caught_logged_once_and_exits_nonzero(caplog):
    server = _FakeServerRaises(_raise_the_496_fault)

    with caplog.at_level(logging.ERROR, logger="astrodeck"):
        exit_code = cli._run_server_with_fault_handling(server)

    assert exit_code == cli.EXIT_EVENT_LOOP_FAULT
    assert exit_code != 0, "D-12 requires a deliberate non-zero exit"
    records = [r for r in caplog.records if r.name == "astrodeck"]
    assert len(records) == 1, (
        f"expected exactly one log record for the fault, got {len(records)}")
    record = records[0]
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None, (
        "D-12 requires the fault to be logged WITH its traceback")
    assert record.exc_info[0] is OSError


def test_an_unrelated_oserror_propagates_unchanged():
    """A plain OSError (no KeyError behind it -- a real I/O failure) is NOT
    the #496 fault and must keep today's behaviour: propagate."""
    def _raise_unrelated() -> None:
        raise OSError(2, "No such file or directory")

    server = _FakeServerRaises(_raise_unrelated)

    with pytest.raises(OSError):
        cli._run_server_with_fault_handling(server)


def test_an_oserror_winerror_6_without_a_keyerror_context_propagates_unchanged():
    """Same winerror as #496, but nothing chained behind it -- still not a
    match, since the narrow signature requires BOTH."""
    def _raise_lookalike() -> None:
        fault = OSError()
        fault.winerror = 6
        fault.strerror = "The handle is invalid"
        raise fault

    server = _FakeServerRaises(_raise_lookalike)

    with pytest.raises(OSError):
        cli._run_server_with_fault_handling(server)


def test_a_keyerror_that_escapes_directly_propagates_unchanged():
    """Only OSError is ever caught here -- a bare KeyError escaping
    ``server.run()`` (not chained into the WinError 6) is some other bug and
    must surface normally, not be mistaken for #496."""
    def _raise_bare_keyerror() -> None:
        raise KeyError(1710290246384)

    server = _FakeServerRaises(_raise_bare_keyerror)

    with pytest.raises(KeyError):
        cli._run_server_with_fault_handling(server)


def test_a_clean_shutdown_is_unaffected():
    assert cli._run_server_with_fault_handling(_FakeServerReturns()) == 0


def test_matcher_true_only_for_the_exact_496_shape():
    try:
        _raise_the_496_fault()
    except OSError as exc:
        assert cli._is_windows_proactor_overlapped_fault(exc) is True
    else:  # pragma: no cover - the helper above always raises
        pytest.fail("expected _raise_the_496_fault to raise")


def test_matcher_false_for_other_winerror_with_keyerror_context():
    try:
        try:
            raise KeyError("x")
        except KeyError:
            fault = OSError()
            fault.winerror = 2
            raise fault
    except OSError as exc:
        assert cli._is_windows_proactor_overlapped_fault(exc) is False


def test_matcher_false_for_non_oserror():
    assert cli._is_windows_proactor_overlapped_fault(KeyError("x")) is False
