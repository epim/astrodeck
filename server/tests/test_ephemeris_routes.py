"""The three ephemeris routes as ROUTES: what they cost the box, and what they
answer when they refuse.

The capability gates on these are graded in ``test_ephemeris_redaction.py`` and
``test_rbac_enforcement.py``; nothing here re-tests them. This file is about the
two things a gate cannot say:

* ``GET /api/satellites/passes`` is seconds of CPU per call. One at a time, per
  box, and the second caller is TOLD so rather than queued behind an answer it
  has stopped waiting for.
* ``GET /api/ephemeris/status`` stats and may parse two JSON files, and it is
  polled while a refresh runs, so it is answered on a worker thread.

The endpoint coroutines are called DIRECTLY rather than through a TestClient,
because the property under test is concurrency: two overlapping requests on one
event loop is exactly what a second phone (or the relay replaying one) produces,
and a synchronous test client cannot make them overlap. ``@declare`` returns the
function unchanged (auth/rbac.py:125-129) and the FastAPI decorator does too, so
the object imported here IS the endpoint the router serves.
"""
from __future__ import annotations

import asyncio
import json
import threading

import pytest
from fastapi import HTTPException

from astrodeck.catalog.ephemeris import elements as el
from astrodeck.catalog.ephemeris import passes as passes_mod
from astrodeck.catalog.ephemeris import routes as R
from astrodeck.catalog.ephemeris import satellites as sats

_EMPTY = {"passes": [], "horizon_source": "none", "elements": {}, "notes": []}


def _ask():
    """One ``GET /api/satellites/passes``, with the Query defaults spelled out
    (calling the endpoint directly means no FastAPI to resolve them)."""
    return R.satellite_passes(hours=1.0, min_alt_deg=0.0, ids=None)


# ============================================ one pass search at a time

def test_a_second_pass_search_while_one_is_running_is_refused(monkeypatch):
    """THE COST IS THE POINT. The search is thousands of SGP4 evaluations plus a
    frame transform each, per satellite: ``asyncio.to_thread`` keeps it off the
    event loop but not off the CPU, and the guider, the sequence engine and the
    2-second status poll are all on these cores. Nothing stopped three of them
    running at once -- a phone whose user pressed the card twice was enough.

    The second caller gets a 409 it can retry, with a machine code, rather than
    being QUEUED: a queue would hand it an answer computed for an instant it has
    forgotten about, minutes later.

    The second call is bounded by ``wait_for``, so a build with the gate removed
    fails here (blocked on the semaphore) instead of hanging the suite."""
    loop_holder: dict = {}
    release = threading.Event()
    started = asyncio.Event()

    def _slow(hours, min_alt_deg, ids, when):
        loop_holder["loop"].call_soon_threadsafe(started.set)
        assert release.wait(10.0), "the first search was never released"
        return dict(_EMPTY)

    monkeypatch.setattr(passes_mod, "find_passes", _slow)

    async def _go():
        loop_holder["loop"] = asyncio.get_running_loop()
        first = asyncio.create_task(_ask())
        await asyncio.wait_for(started.wait(), 10.0)

        try:
            second = await asyncio.wait_for(_ask(), 5.0)
        except asyncio.TimeoutError:
            release.set()
            await first
            pytest.fail(
                "the second pass search QUEUED behind the first instead of "
                "being refused: both callers pay for a full search, and the "
                "second gets an answer computed minutes after it asked")
        assert second.status_code == 409, (
            "a second pass search ran beside the first")
        body = json.loads(second.body)
        assert body["code"] == "passes_busy"
        assert "already running" in body["detail"]

        release.set()
        assert await asyncio.wait_for(first, 10.0) == _EMPTY

    asyncio.run(_go())


def test_the_gate_reopens_when_a_search_finishes(monkeypatch):
    """The other half: a 409 for the second caller is only tolerable because the
    refusal is momentary. A gate that leaked one permit per request would take
    two presses to make the card permanently unusable, and the failure would
    look like a server that had 'stopped answering'."""
    monkeypatch.setattr(passes_mod, "find_passes",
                        lambda *a, **kw: dict(_EMPTY))

    async def _go():
        for _ in range(3):
            assert await _ask() == _EMPTY
        assert not R._PASSES_GATE.locked()

    asyncio.run(_go())


def test_the_gate_reopens_when_a_search_raises(monkeypatch):
    """And it reopens on the failing path too. ``SatellitesUnavailable`` (no
    site set) is the refusal a fresh rig hits on its first press, so a gate that
    kept the permit on an exception would lock the route for the whole run of
    the server the first time somebody asked before setting a location."""
    def _boom(*a, **kw):
        raise sats.SatellitesUnavailable(sats.SITE_UNSET_NOTE)

    monkeypatch.setattr(passes_mod, "find_passes", _boom)

    async def _go():
        with pytest.raises(HTTPException) as exc:
            await _ask()
        assert exc.value.status_code == 409
        # Coded, not a bare string: retrying cannot fix an unset site, so the
        # card has to be able to tell this 409 from ``passes_busy`` without
        # matching on a sentence. ``detail`` still carries the sentence, which
        # is what the UI prints.
        assert exc.value.detail["code"] == "satellites_unavailable"
        assert "Settings" in exc.value.detail["detail"]
        assert not R._PASSES_GATE.locked(), (
            "a failed search kept the gate shut")

    asyncio.run(_go())


# ==================================== the status route is not free either

def test_the_status_route_is_answered_off_the_event_loop(monkeypatch):
    """``snapshot`` stats both element files and parses whichever the memo has
    not got, and the Sky settings card polls this route for as long as a refresh
    is in flight. On the event loop that is a stall in the status stream, which
    is the same loop the relay and the 2-second poll ride.

    Graded by THREAD IDENTITY rather than by reading the source: a future edit
    that drops the ``to_thread`` fails here."""
    seen: dict = {}

    def _snapshot(now=None):
        seen["worker"] = threading.get_ident()
        return {"satellites": {}, "comets": {}, "fetching": [],
                "last_outcome": {}}

    monkeypatch.setattr(el.ephemeris_store, "snapshot", _snapshot)

    async def _go():
        seen["loop"] = threading.get_ident()
        return await R.ephemeris_status()

    body = asyncio.run(_go())
    assert body["fetching"] == []
    assert seen["worker"] != seen["loop"], (
        "snapshot ran on the event loop thread")
