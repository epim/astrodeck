"""Polar alignment session: sim convergence + NINA message parsing."""
import asyncio
import json

import pytest

from astrodeck.hub import Hub


class _FakeWS:
    """Minimal async-context-manager / async-iterator NINA TPPA websocket:
    yields the given raw messages then ends the `async for` cleanly (a normal
    close), exercising the terminal-state path."""

    def __init__(self, messages):
        self._messages = list(messages)
        self.sent: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send(self, data):
        self.sent.append(data)

    def __aiter__(self):
        self._it = iter(self._messages)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


async def _wait(predicate, timeout=45.0):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.2)
    return False


async def test_sim_polar_converges():
    h = Hub()  # mode "none" → sim polar driver
    try:
        await h.polar.start()
        assert h.polar.running
        assert await _wait(lambda: h.polar.state["state"] == "done")
        assert h.polar.state["total_error"] < 1.0
        assert h.polar.state["progress"] == 1.0
    finally:
        await h.polar.stop()


async def test_sim_polar_stop():
    h = Hub()
    await h.polar.start()
    await asyncio.sleep(0.5)
    await h.polar.stop()
    assert not h.polar.running
    assert h.polar.state["state"] == "idle"


async def test_sim_source_set_synchronously():
    """start() must resolve the driver source BEFORE returning, so the REST
    handler that reads state["source"] right after start() sees "sim"/"nina"
    (the driver task body hasn't run yet)."""
    h = Hub()  # no nina → sim
    try:
        await h.polar.start()
        assert h.polar.state["source"] == "sim"
    finally:
        await h.polar.stop()


async def test_nina_source_set_synchronously(monkeypatch):
    import websockets
    h = Hub()
    h.nina_client = type("N", (), {"host": "127.0.0.1", "port": 1})()
    # A websocket that never yields; we only assert the synchronous source set.
    monkeypatch.setattr(websockets, "connect", lambda url, **kw: _FakeWS([]))
    try:
        await h.polar.start()
        assert h.polar.state["source"] == "nina"
    finally:
        await h.polar.stop()


async def test_nina_clean_close_publishes_done(monkeypatch):
    """When NINA closes the TPPA websocket cleanly after producing a
    measurement, the session must reach a terminal "done" — never stick on
    "running" forever."""
    import websockets
    h = Hub()
    h.nina_client = type("N", (), {"host": "127.0.0.1", "port": 1})()
    msgs = [json.dumps({"Response": {"AzimuthError": 0.05, "AltitudeError": 0.03,
                                     "TotalError": 0.058}})]
    monkeypatch.setattr(websockets, "connect", lambda url, **kw: _FakeWS(msgs))
    await h.polar.start()
    assert await _wait(lambda: h.polar.state["state"] == "done")
    assert h.polar.state["progress"] == 1.0
    assert not h.polar.running


async def test_nina_clean_close_without_measurement_errors(monkeypatch):
    """A TPPA socket that closes before any measurement must land on a terminal
    "error", not linger on "running"."""
    import websockets
    h = Hub()
    h.nina_client = type("N", (), {"host": "127.0.0.1", "port": 1})()
    monkeypatch.setattr(websockets, "connect", lambda url, **kw: _FakeWS([]))
    await h.polar.start()
    assert await _wait(lambda: h.polar.state["state"] == "error")
    assert not h.polar.running


def test_nina_tppa_parsing_deg_to_arcmin():
    h = Hub()
    sess = h.polar
    # NINA reports degrees; 0.05° == 3.0′, 0.03° == 1.8′
    sess._handle_nina({"Response": {"AzimuthError": 0.05, "AltitudeError": 0.03,
                                    "TotalError": 0.058}})
    assert abs(sess.state["az_error"] - 3.0) < 0.05
    assert abs(sess.state["alt_error"] - 1.8) < 0.05
    assert abs(sess.state["total_error"] - 3.5) < 0.1
    assert sess.state["state"] == "running"


def test_nina_tppa_status_progress():
    h = Hub()
    sess = h.polar
    sess._handle_nina({"Response": {"Status": "measuring point 2/3", "Progress": 0.4}})
    assert sess.state["message"] == "measuring point 2/3"
    assert sess.state["progress"] == 0.4


def test_nina_tppa_progress_never_negative():
    """NINA reports Progress == -1 during indeterminate ("Solving…") phases.
    We must never publish a negative progress (the UI hides the bar on it);
    instead hold the last known progress and clamp to [0, 1]."""
    h = Hub()
    sess = h.polar
    sess._handle_nina({"Response": {"Progress": 0.4}})
    assert sess.state["progress"] == 0.4
    # indeterminate solve → holds 0.4, not -1.0
    sess._handle_nina({"Response": {"Status": "Solving…", "Progress": -1}})
    assert sess.state["progress"] == 0.4
    assert sess.state["message"] == "Solving…"
    # an over-unity value clamps to 1.0
    sess._handle_nina({"Response": {"Progress": 1.5}})
    assert sess.state["progress"] == 1.0
