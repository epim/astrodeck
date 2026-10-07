# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The other three parks tell the sun watch (#747, the #696 class; wave 15
integration of WP-113).

#696. The sun watch's blind fallback projects from the last position IT read.
A park it did not see leaves that position stale, and if the link then drops
before its next tick it logs a false "SUN WATCH: ... Parking now." error (which
pages the owner) and sends a park to a mount that is already parked. WP-113
gave it ``SunWatch.note_parked()`` and made the engine's wind-down and
``dawn_park`` call it. Three more paths park the mount and did not:

* the operator's PARK, ``POST /api/mount/park`` (the likeliest real-world case);
* the roof close's park-for-the-roof, ``POST /api/dome/close``;
* the engine's ``_fenced_park``, the auto-reopen roof close, which parks through
  ``_park_and_read_back``.

Each now calls it AFTER a park that returned (the engine's: that read back as
parked), through the hub (``getattr(hub, "sun_watch", None)``, which
``SunWatch.__init__`` sets), the way the wind-down does. A park that raised, or
one the engine could not confirm, tells nobody. The hook never raises into the
path that called it.

The spy records into one list beside the mount's own ``park``, so the ORDER is
asserted as well as the count: the note comes after the park, never before it.

Named mutants, each run from a byte backup of the file named and restored
byte-identically (sha256 compared, the mutant text grepped absent); the case
that failed and its first assertion, verbatim:

* ``api/app.py``: ``_tell_sun_watch_the_mount_parked()`` deleted from the
  ``/api/mount/park`` route's ``_park()`` ->
  ``test_the_operators_park_tells_the_sun_watch_after_the_park``,
  ``AssertionError: the operator's park never told the sun watch: ['park']``.
* ``api/app.py``: the same call deleted from the dome-close route ->
  ``test_the_roofs_park_tells_the_sun_watch_before_the_shutter_moves``,
  ``AssertionError: the roof close's park never told the sun watch: ['park',
  'shutter']``.
* ``sequence/engine.py``: the ``if parked:`` block deleted from ``_fenced_park``
  -> ``test_the_auto_reopen_close_tells_the_sun_watch_after_a_confirmed_park``,
  ``AssertionError: the auto-reopen close's park never told the sun watch:
  ['park']``.
* ``api/app.py``: the helper moved BEFORE ``await tel.park()`` in the operator's
  route -> the order case,
  ``AssertionError: the operator's park told the sun watch before the park:
  ['note', 'park']``.
* ``api/app.py``: the helper's ``try``/``except`` removed ->
  ``test_a_sun_watch_that_raises_does_not_fail_the_operators_park``,
  ``AssertionError: a bookkeeping failure turned a park that worked into a
  failed route: [... ('error', 'goto failed: the sun watch broke', 'goto')]``.
"""
from __future__ import annotations

import asyncio
import time

import pytest

import astrodeck.api.app as app_module
from astrodeck.devices.base import DeviceError
from astrodeck.sequence.engine import SequenceEngine

from test_dome_routes import client  # noqa: F401  (fixture import)
from test_idle_park_hold import sim_hub, temp_store  # noqa: F401  (fixtures)


class _SunWatchSpy:
    """What ``hub.sun_watch`` is for these cases: ``note_parked`` writes into
    the same list the mount's own park writes into."""

    def __init__(self, events: list[str], *, raises: bool = False):
        self.events = events
        self.raises = raises

    def note_parked(self) -> None:
        self.events.append("note")
        if self.raises:
            raise RuntimeError("the sun watch broke")


def _record_parks(tel, events: list[str], monkeypatch, *, fail=None) -> None:
    """The mount's park, recorded into ``events`` as it RETURNS. ``fail`` makes
    it raise instead, and records nothing."""
    real = tel.park

    async def park():
        if fail is not None:
            raise fail
        await real()
        events.append("park")

    monkeypatch.setattr(tel, "park", park)


def _wait(predicate, c, tries=400) -> bool:
    for _ in range(tries):
        if predicate():
            return True
        c.get("/api/status")     # give the app loop a chance to run bg tasks
        time.sleep(0.02)
    return predicate()


def _lane_done(lane: str) -> bool:
    task = app_module.hub._busy.get(lane)
    return task is None or task.done()


# ----------------------------------------------------------- the operator's park

def test_the_operators_park_tells_the_sun_watch_after_the_park(
        client, monkeypatch):
    """CONTROL and the case: POST /api/mount/park parks the sim mount, and the
    sun watch hears of it once, after the park returned."""
    assert client.post("/api/connect/sim").status_code == 200
    hub = app_module.hub
    events: list[str] = []
    monkeypatch.setattr(hub, "sun_watch", _SunWatchSpy(events), raising=False)
    _record_parks(hub.devices["telescope"], events, monkeypatch)

    assert client.post("/api/mount/park").status_code == 200
    assert _wait(lambda: "note" in events or _lane_done("goto"), client)
    _wait(lambda: _lane_done("goto"), client)

    assert "note" in events, (
        f"the operator's park never told the sun watch: {events}")
    assert events == ["park", "note"], (
        f"the operator's park told the sun watch before the park: {events}")


def test_a_park_that_failed_tells_nobody(client, monkeypatch):
    """A park that raised was not confirmed: the sun watch is not told, so its
    blind fallback keeps projecting from a pointing that may still be true."""
    assert client.post("/api/connect/sim").status_code == 200
    hub = app_module.hub
    events: list[str] = []
    monkeypatch.setattr(hub, "sun_watch", _SunWatchSpy(events), raising=False)
    _record_parks(hub.devices["telescope"], events, monkeypatch,
                  fail=DeviceError("the mount would not park"))

    assert client.post("/api/mount/park").status_code == 200
    assert _wait(lambda: _lane_done("goto"), client)

    assert events == [], f"a failed park told the sun watch: {events}"


def test_a_sun_watch_that_raises_does_not_fail_the_operators_park(
        client, monkeypatch, bus_lines):
    """The note is bookkeeping: it cannot turn a park that worked into a failed
    route (the 'mount parked' line, written after it, must still be written)."""
    assert client.post("/api/connect/sim").status_code == 200
    hub = app_module.hub
    events: list[str] = []
    monkeypatch.setattr(hub, "sun_watch", _SunWatchSpy(events, raises=True),
                        raising=False)
    _record_parks(hub.devices["telescope"], events, monkeypatch)

    assert client.post("/api/mount/park").status_code == 200
    assert _wait(lambda: _lane_done("goto") and "note" in events, client)

    assert events == ["park", "note"], events
    assert any(m == "mount parked" for _lv, m, _s in bus_lines), (
        "a bookkeeping failure turned a park that worked into a failed route: "
        f"{bus_lines}")


# ------------------------------------------------------------- the roof's park

def test_the_roofs_park_tells_the_sun_watch_before_the_shutter_moves(
        client, monkeypatch):
    assert client.post("/api/connect/sim").status_code == 200
    hub = app_module.hub
    events: list[str] = []
    monkeypatch.setattr(hub, "sun_watch", _SunWatchSpy(events), raising=False)
    _record_parks(hub.devices["telescope"], events, monkeypatch)
    dome = hub.devices["dome"]
    real_close = dome.close_shutter

    async def close_shutter():
        events.append("shutter")
        await real_close()

    monkeypatch.setattr(dome, "close_shutter", close_shutter)

    assert client.post("/api/dome/close").status_code == 200
    assert _wait(lambda: "shutter" in events and _lane_done("dome"), client)

    assert "note" in events, (
        f"the roof close's park never told the sun watch: {events}")
    assert events == ["park", "note", "shutter"], (
        f"the sun watch was told out of order around the roof close: {events}")


def test_a_roof_close_that_parks_nothing_tells_nobody(client, monkeypatch):
    """CONTROL: a roof that needs no park (``requires_park_before_close`` False)
    makes the route park nothing, so there is no park to tell anyone about."""
    assert client.post("/api/connect/sim").status_code == 200
    hub = app_module.hub
    events: list[str] = []
    monkeypatch.setattr(hub, "sun_watch", _SunWatchSpy(events), raising=False)
    _record_parks(hub.devices["telescope"], events, monkeypatch)
    dome = hub.devices["dome"]
    monkeypatch.setattr(dome, "requires_park_before_close", False,
                        raising=False)

    assert client.post("/api/dome/close").status_code == 200
    assert _wait(lambda: _lane_done("dome"), client)

    assert events == [], f"a close with no park told the sun watch: {events}"


# ------------------------------------------------- the auto-reopen roof close

async def test_the_auto_reopen_close_tells_the_sun_watch_after_a_confirmed_park(
        sim_hub, monkeypatch, bus_lines):
    events: list[str] = []
    monkeypatch.setattr(sim_hub, "sun_watch", _SunWatchSpy(events),
                        raising=False)
    tel = sim_hub.devices["telescope"]
    _record_parks(tel, events, monkeypatch)
    engine = SequenceEngine(sim_hub)

    parked = await engine._fenced_park()

    assert parked is True, "premise: the sim mount parked and read back as parked"
    assert "note" in events, (
        f"the auto-reopen close's park never told the sun watch: {events}")
    assert events == ["park", "note"], (
        f"the auto-reopen close told the sun watch before the park: {events}")


async def test_an_auto_reopen_park_that_did_not_confirm_tells_nobody(
        sim_hub, monkeypatch, bus_lines):
    events: list[str] = []
    monkeypatch.setattr(sim_hub, "sun_watch", _SunWatchSpy(events),
                        raising=False)
    tel = sim_hub.devices["telescope"]
    _record_parks(tel, events, monkeypatch, fail=DeviceError("no park"))
    engine = SequenceEngine(sim_hub)

    parked = await engine._fenced_park()

    assert parked is False, "premise: the park failed"
    assert events == [], f"a park that failed told the sun watch: {events}"


async def test_a_sun_watch_that_raises_does_not_fail_the_auto_reopen_close(
        sim_hub, monkeypatch, bus_lines):
    events: list[str] = []
    monkeypatch.setattr(sim_hub, "sun_watch",
                        _SunWatchSpy(events, raises=True), raising=False)
    tel = sim_hub.devices["telescope"]
    _record_parks(tel, events, monkeypatch)
    engine = SequenceEngine(sim_hub)

    parked = await engine._fenced_park()

    assert parked is True, (
        "a bookkeeping failure turned a park that worked into a failed park")
    assert events == ["park", "note"], events


async def test_a_hub_with_no_sun_watch_is_left_alone(
        sim_hub, monkeypatch, bus_lines):
    """Every test double, and a build without the net, has no ``sun_watch``."""
    monkeypatch.delattr(sim_hub, "sun_watch", raising=False)
    engine = SequenceEngine(sim_hub)

    assert await engine._fenced_park() is True
