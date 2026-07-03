"""PHD2 guider transport tests (P1-6).

Covers the new resilience behavior that the live rig needs and that previously
had ZERO coverage:

* reconnect with backoff after an unexpected socket EOF (the _listen task used to
  die and never restart),
* pending RPC futures are failed (not leaked) on a drop, and stats() stops
  reporting stale "Guiding",
* Alert / CalibrationFailed events unblock a waiting start_guiding with a friendly
  reason instead of a ~90s settle timeout,
* flip_calibration() issues the PHD2 RPC and is a guarded no-op when not connected.

A tiny in-process fake PHD2 JSON server backs these so the REAL socket/_listen
path is exercised (no mock of the class under test).
"""
from __future__ import annotations

import asyncio
import json

import pytest

from astrodeck.guide.phd2 import PHD2Guider


class FakePHD2:
    """Minimal PHD2 JSON event-server stand-in. Echoes a result for any RPC,
    records the methods it saw, and exposes hooks to push async events and to
    forcibly drop the client connection (to test reconnect)."""

    def __init__(self):
        self.server: asyncio.AbstractServer | None = None
        self.port = 0
        self.methods: list[str] = []
        self._writer: asyncio.StreamWriter | None = None
        self._reader: asyncio.StreamReader | None = None
        self.client_connected = asyncio.Event()
        self.rpc_result_for: dict[str, object] = {}   # method -> result
        self.rpc_error_for: dict[str, str] = {}        # method -> error message
        self.swallow: set[str] = set()                 # methods to NOT answer

    async def start(self):
        self.server = await asyncio.start_server(
            self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def _handle(self, reader, writer):
        self._reader, self._writer = reader, writer
        self.client_connected.set()
        try:
            while True:
                line = await reader.readline()
                if not line:
                    return
                try:
                    req = json.loads(line)
                except ValueError:
                    continue
                method = req.get("method")
                self.methods.append(method)
                if method in self.swallow:
                    continue   # leave the caller's RPC in-flight (never answer)
                if method in self.rpc_error_for:
                    resp = {"jsonrpc": "2.0", "id": req.get("id"),
                            "error": {"message": self.rpc_error_for[method]}}
                else:
                    resp = {"jsonrpc": "2.0", "id": req.get("id"),
                            "result": self.rpc_result_for.get(method, 0)}
                writer.write((json.dumps(resp) + "\r\n").encode())
                await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            return

    async def push_event(self, ev: dict):
        assert self._writer is not None
        self._writer.write((json.dumps(ev) + "\r\n").encode())
        await self._writer.drain()

    def drop_client(self):
        """Hard-close the current client socket to simulate an EOF/drop."""
        if self._writer is not None:
            self._writer.close()
            self._writer = None
            self.client_connected.clear()

    async def stop(self):
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:
                pass
        if self.server is not None:
            self.server.close()
            # Don't await wait_closed(): a lingering client handler coroutine can
            # keep it pending. Closing the listening socket is enough for the test.


@pytest.fixture
async def phd2():
    srv = FakePHD2()
    await srv.start()
    g = PHD2Guider(host="127.0.0.1", port=srv.port)
    # speed up reconnect backoff so the test isn't slow
    g._RECONNECT_BACKOFF = (0.05,)
    try:
        yield srv, g
    finally:
        await g.disconnect()
        await srv.stop()


async def _wait(cond, timeout=3.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if cond():
            return True
        await asyncio.sleep(0.02)
    return False


# ----------------------------------------------------------- connect + RPC

async def test_connect_and_rpc(phd2):
    srv, g = phd2
    await g.connect()
    assert g.connected is True
    await g.stop_guiding()
    assert "stop_capture" in srv.methods


# ----------------------------------------------------------- reconnect on EOF

async def test_reconnect_after_socket_drop(phd2):
    srv, g = phd2
    await g.connect()
    assert await _wait(lambda: srv.client_connected.is_set())

    # Simulate an unexpected drop: PHD2 closes the socket.
    srv.drop_client()
    # connected flips False on the EOF...
    assert await _wait(lambda: g.connected is False)
    # ...and then the reconnect loop re-establishes the link + a fresh listener.
    assert await _wait(lambda: g.connected is True, timeout=5.0)
    # the new socket is usable
    await g.stop_guiding()
    assert srv.methods.count("stop_capture") >= 1


async def test_disconnect_cancels_reconnect_loop(phd2):
    """disconnect() while the reconnect loop is running must cancel that loop,
    not leave an orphaned task racing to revive a discarded guider (P1-6)."""
    srv, g = phd2
    await g.connect()
    assert await _wait(lambda: srv.client_connected.is_set())
    # Drop the client AND stop the server so reconnect keeps failing and stays
    # parked in its backoff loop.
    srv.drop_client()
    await srv.stop()
    assert await _wait(
        lambda: g._reconnect_task is not None and not g._reconnect_task.done())
    await g.disconnect()
    assert g._reconnect_task is None
    assert g.connected is False


async def test_reconnect_aborts_if_disconnect_races_open(phd2):
    """If disconnect() flips _closing while _reconnect is parked inside _open(),
    the freshly opened socket must be discarded rather than reviving the guider
    (which would double-connect and duplicate guide events forever, P1-6)."""
    srv, g = phd2
    await g.connect()
    # Simulate the race: _open succeeds, but _closing is set during it.
    orig_open = g._open

    async def open_then_closing():
        await orig_open()
        g._closing = True

    g._open = open_then_closing
    g.connected = False
    g._listen_task = None
    await g._reconnect()
    # The abandoned reconnect must NOT have revived the guider.
    assert g.connected is False
    assert g._listen_task is None


async def test_pending_rpc_fails_on_drop_not_leak(phd2):
    srv, g = phd2
    # Tell the fake server to never answer this RPC so it stays in-flight.
    srv.swallow.add("get_star_image")
    await g.connect()
    assert await _wait(lambda: srv.client_connected.is_set())

    # Start an RPC that will never be answered, then drop the socket. The pending
    # future must FAIL (not hang until its 10s timeout) and _pending must drain.
    task = asyncio.create_task(g._rpc("get_star_image", [15], timeout=10))
    assert await _wait(lambda: bool(g._pending)), "RPC should be pending"
    srv.drop_client()
    with pytest.raises((ConnectionError, RuntimeError)):
        await task
    assert await _wait(lambda: not g._pending)


# -------------------------------------------------- guiding-state reachability

async def test_guidestep_event_marks_guiding(phd2):
    """A real PHD2 sends AppState only in its initial connect burst; a later
    start-guiding arrives as GuideStep/StartGuiding. is_active()/stats().guiding
    must become True from those, or per-frame recovery + meridian-flip restart
    break for the whole night."""
    srv, g = phd2
    await g.connect()
    assert g.stats().guiding is False
    await srv.push_event({"Event": "GuideStep", "RADistanceRaw": 0.1,
                          "DECDistanceRaw": -0.2, "SNR": 20})
    assert await _wait(lambda: g.stats().guiding is True)
    assert await g.is_active() is True


async def test_start_guiding_event_marks_guiding(phd2):
    srv, g = phd2
    await g.connect()
    await srv.push_event({"Event": "StartGuiding"})
    assert await _wait(lambda: g.stats().guiding is True)


async def test_looping_and_stop_events_clear_guiding(phd2):
    srv, g = phd2
    await g.connect()
    await srv.push_event({"Event": "StartGuiding"})
    assert await _wait(lambda: g.stats().guiding is True)
    await srv.push_event({"Event": "GuidingStopped"})
    assert await _wait(lambda: g.stats().guiding is False)


async def test_connect_queries_real_app_state(phd2):
    """On (re)connect we ask PHD2 for its real state via get_app_state rather
    than assuming "Stopped" — a reconnect mid-guiding must not report idle."""
    srv, g = phd2
    srv.rpc_result_for["get_app_state"] = "Guiding"
    await g.connect()
    assert "get_app_state" in srv.methods
    assert g.stats().guiding is True


async def test_stats_not_guiding_after_drop(phd2):
    srv, g = phd2
    await g.connect()
    # Pretend PHD2 told us it's guiding.
    await srv.push_event({"Event": "AppState", "State": "Guiding"})
    assert await _wait(lambda: g.stats().guiding is True)
    srv.drop_client()
    # after the drop, stats must stop claiming "Guiding"
    assert await _wait(lambda: g.stats().guiding is False)


# ----------------------------------------------------------- Alert / cal-fail

async def test_calibration_failed_event_fails_settle_fast(phd2):
    srv, g = phd2
    await g.connect()

    async def fire_calfail():
        await asyncio.sleep(0.1)
        await srv.push_event({"Event": "CalibrationFailed",
                              "Reason": "star did not move enough"})

    asyncio.create_task(fire_calfail())
    with pytest.raises(RuntimeError) as ei:
        await g.start_guiding()
    assert "calibration failed" in str(ei.value).lower()


async def test_alert_error_event_fails_settle_fast(phd2):
    srv, g = phd2
    await g.connect()

    async def fire_alert():
        await asyncio.sleep(0.1)
        await srv.push_event({"Event": "Alert",
                              "Msg": "Calibration cannot start: no star",
                              "Type": "error"})

    asyncio.create_task(fire_alert())
    with pytest.raises(RuntimeError) as ei:
        await g.start_guiding()
    assert "calibration cannot start" in str(ei.value).lower()


# ----------------------------------------------------------- flip_calibration

async def test_flip_calibration_issues_rpc(phd2):
    srv, g = phd2
    srv.rpc_result_for["flip_calibration"] = True
    await g.connect()
    ok = await g.flip_calibration()
    assert ok is True
    assert "flip_calibration" in srv.methods


async def test_flip_calibration_noop_when_not_connected(phd2):
    srv, g = phd2
    # never connected → guarded no-op, returns False, no raise
    ok = await g.flip_calibration()
    assert ok is False


async def test_flip_calibration_warns_on_rpc_error(phd2):
    srv, g = phd2
    srv.rpc_error_for["flip_calibration"] = "no calibration to flip"
    await g.connect()
    ok = await g.flip_calibration()
    assert ok is False   # error is swallowed → False, flip still proceeds


async def test_can_flip_calibration_flag():
    g = PHD2Guider()
    assert g.can_flip_calibration is True
