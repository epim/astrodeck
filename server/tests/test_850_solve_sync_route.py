# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#850 (route half): pressing Solve & Sync where the mount refuses the sync
does not toast "solve failed".

``POST /api/mount/solve_sync`` spawns ``hub.solve_and_sync`` on the ``solve``
lane, and ``_spawn`` words every failure "<lane> failed: ...". For the case
#850 was found on (the AM5 at its home position answers ``e11`` to every
sync) the toast read "solve failed: ZWO AM5: sync refused ...", although the
field solved and it was the mount that refused. The route now wraps the call:
``SyncRefused`` and ``SyncUnverified`` are logged at ERROR level (the UI
toasts every error line) as "sync not taken: <the driver's words>" on source
"solve", and never reach ``_spawn``'s generic line. Every other exception
still does, unchanged.

Route tests in the style of test_api_mount.py: a real ``create_app()`` and
TestClient, with the hub singleton's ``require`` and ``solve_and_sync``
monkeypatched to fakes. Each surfaced line is graded against the UI's
``humanizeLog`` rules (mirrored by ``_humanizer_rewrites``), its
140-character cut and the fictional, non-round solve's spellings.

Each test names the mutant of api/app.py it was shown red under; the mutants
were applied to a byte copy of app.py and the file was restored from that
copy and checked by sha256.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.devices.base import DeviceError, SyncRefused, SyncUnverified

#: Where the fake solve found the field. Fictional and NON-ROUND, so a leak
#: of ``e.solved`` into the line is caught by every spelling below.
FAR_RA, FAR_DEC = 5.4321, 12.8765

#: The driver's own e11 words for a tube at home (#850), so the route test
#: cannot drift from what the operator is really shown. Safe order: Trust
#: position first, and no slew while the position is unknown.
from astrodeck.devices.backends.zwo_am5 import (  # noqa: E402
    SYNC_E11_AT_HOME_REASON as _E11_REASON)
#: The shape of the driver's refusal message: name, what happened, the
#: reason, then the reply.
_REFUSED_MSG = f"ZWO AM5: sync refused - {_E11_REASON} (reply 'e11')"
#: The shape of the driver's unverified message.
_UNVERIFIED_MSG = ("ZWO AM5: sync not confirmed: the link failed during the "
                   "sync, so whether the mount took it is unknown")


def _humanizer_rewrites(text: str) -> bool:
    """True when the UI's ``humanizeLog`` (ui/src/lib/humanize.ts:110-138)
    would replace ``text`` with a canned sentence. Its four text rules, in
    its order, lower-cased substring tests."""
    m = text.lower()
    if "camera" in m and any(k in m for k in ("not responding", "timeout",
                                              "disconnect")):
        return True
    if "nina" in m and any(k in m for k in ("5", "http", "error")):
        return True
    if "plate" in m and "solve" in m:
        return True
    if "guid" in m and "lost" in m:
        return True
    return False


def _coordinate_spellings(ra: float, dec: float) -> list[str]:
    out = [repr((ra, dec)), str((ra, dec)), f"{ra}, {dec}", str([ra, dec])]
    for v in (ra, dec, ra * 15.0):
        out += [repr(v), str(v)]
        for p in range(1, 5):
            out += [f"{v:.{p}f}", f"{v:+.{p}f}"]
    return sorted(set(out))


def _shown(line: str) -> str:
    """What the UI shows of a line: cut to 137 plus an ellipsis past 140."""
    return line if len(line) <= 140 else line[:137]


@pytest.fixture
def client():
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def solve_lane(monkeypatch):
    """The hub singleton with the solve lane empty, the devices present and
    ``solve_and_sync`` replaced. Returns a setter for what the fake raises
    and the list of calls it saw."""
    hub = app_module.hub
    prior = hub._busy.pop("solve", None)
    monkeypatch.setattr(hub, "require", lambda role: object())
    state: dict = {"raise": None, "calls": []}

    async def run():
        exc = state["raise"]
        if exc is not None:
            raise exc
        return {"ra_hours": FAR_RA, "dec_deg": FAR_DEC}

    def solve_and_sync(*a, **k):
        # Counted when CALLED, not when awaited: a coroutine made and then
        # thrown away is a call too.
        state["calls"].append((a, k))
        return run()

    monkeypatch.setattr(hub, "solve_and_sync", solve_and_sync)
    yield state
    task = hub._busy.pop("solve", None)
    if task is not None and not task.done():
        task.cancel()
    if prior is not None:
        hub._busy["solve"] = prior


def _press_and_wait(client) -> None:
    r = client.post("/api/mount/solve_sync")
    assert r.status_code == 200, r.text
    assert r.json() == {"started": "solve"}
    task = app_module.hub._busy["solve"]
    deadline = time.monotonic() + 10.0
    while not task.done() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert task.done(), "the solve lane never finished"
    # The lane ends quietly: nothing escaped the wrapper and _spawn's handler.
    assert task.exception() is None


def _solve_errors(bus_lines) -> list[str]:
    return [m for lvl, m, s in bus_lines if lvl == "error" and s == "solve"]


@pytest.mark.parametrize("kind,code,msg", [
    (SyncRefused, "e11", _REFUSED_MSG),
    (SyncUnverified, "", _UNVERIFIED_MSG),
])
def test_a_sync_not_taken_is_one_error_line_that_never_says_solve_failed(
        client, solve_lane, bus_lines, kind, code, msg):
    """The mount refused (or did not confirm) the sync: ONE error line on
    source "solve", "sync not taken: <the driver's message>", and no "solve
    failed" line anywhere. The action in the e11 advice survives the UI's
    cut, no humanizer rule rewrites it, and the solve attached to the
    exception never reaches it.

    NAMED MUTANT "the route spawns solve_and_sync bare" (the route back to
    ``_spawn("solve", hub.solve_and_sync())``): RED in both cases, the error
    line is ``"solve failed: ZWO AM5: sync refused - ..."``, not ``"sync not
    taken: ..."``.

    NAMED MUTANT "the wrapper catches only SyncRefused": RED on the
    ``SyncUnverified`` case, the line is ``"solve failed: ..."``.

    NAMED MUTANT "the line logged at warning" (``bus.log("warning", ...)`` in
    the wrapper): RED in both cases, ``assert [] == ["sync not taken:
    ..."]``: a warning is not toasted, so the operator would see nothing.

    NAMED MUTANT "the solve leaks into the line" (`` at {e.solved}`` appended
    to the wrapper's line): RED in both cases, on the exact-words assertion.
    """
    e = kind(msg, code=code, reason="stand-in", residual_deg=None)
    e.solved = (FAR_RA, FAR_DEC)
    solve_lane["raise"] = e

    _press_and_wait(client)

    assert len(solve_lane["calls"]) == 1
    errors = _solve_errors(bus_lines)
    assert errors == [f"sync not taken: {msg}"], errors
    assert not any("solve failed" in m for _, m, _ in bus_lines), bus_lines
    line = errors[0]
    assert not _humanizer_rewrites(line), line
    for spelling in _coordinate_spellings(FAR_RA, FAR_DEC):
        assert spelling not in line, (spelling, line)
    if kind is SyncRefused:
        for phrase in ("sync not taken", "Trust position",
                       "a sync away from the pole"):
            assert phrase in _shown(line), (phrase, line)
    else:
        assert "whether the mount took it" in _shown(line), line


def test_any_other_failure_still_reaches_the_lanes_own_handler(
        client, solve_lane, bus_lines):
    """A plain ``DeviceError`` (a failed solve, a camera that would not
    expose) is not a sync the mount declined: it still reaches ``_spawn``'s
    handler and is logged exactly as before, "solve failed: <e>", and no
    "sync not taken" line appears.

    NAMED MUTANT "the wrapper catches every DeviceError" (its ``except``
    widened to ``DeviceError``): RED, the line is ``"sync not taken: plate
    solve failed: Not enough stars."``.
    """
    solve_lane["raise"] = DeviceError("plate solve failed: Not enough stars.")

    _press_and_wait(client)

    errors = _solve_errors(bus_lines)
    assert errors == ["solve failed: plate solve failed: Not enough stars."], \
        errors
    assert not any("sync not taken" in m for _, m, _ in bus_lines), bus_lines


def test_a_solve_that_syncs_logs_no_error(client, solve_lane, bus_lines):
    """CONTROL: a Solve & Sync that works logs no error line at all."""
    _press_and_wait(client)

    assert len(solve_lane["calls"]) == 1
    assert _solve_errors(bus_lines) == []


async def test_a_cancel_goes_through_the_wrapper_untouched(bus_lines):
    """A cancel is not a sync not taken: it passes through the wrapper so
    ``_spawn`` can log "solve cancelled" and re-raise (#252).

    NAMED MUTANT "the wrapper swallows every exception" (``except
    BaseException``): RED, ``Failed: DID NOT RAISE <class
    'asyncio.exceptions.CancelledError'>``.
    """
    async def cancelled():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await app_module._sync_not_taken_is_not_a_failed_solve(cancelled)
    assert bus_lines == []


def test_a_busy_lane_refuses_before_solve_and_sync_is_even_called(
        client, solve_lane):
    """When the solve lane is busy the route answers 409 and the hub's
    ``solve_and_sync`` is never called: the wrapper takes the METHOD, so a
    refused spawn leaves no coroutine behind to be "never awaited".

    NAMED MUTANT "the wrapper takes the coroutine" (the wrapper awaits its
    argument as a coroutine and the route passes it
    ``hub.solve_and_sync()``): RED, ``assert [((), {})] == []`` on the
    calls: the coroutine was made before ``_spawn`` refused the lane.
    """
    hub = app_module.hub

    async def never():
        await asyncio.sleep(3600)

    loop = asyncio.new_event_loop()
    try:
        busy = loop.create_task(never())
        hub._busy["solve"] = busy
        r = client.post("/api/mount/solve_sync")
        assert r.status_code == 409, r.text
        assert solve_lane["calls"] == []
        busy.cancel()
        try:
            loop.run_until_complete(busy)
        except asyncio.CancelledError:
            pass
    finally:
        hub._busy.pop("solve", None)
        loop.close()
