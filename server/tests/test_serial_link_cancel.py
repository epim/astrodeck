"""SerialLink cancel/close race: the single-owner-port invariant.

``asyncio.to_thread`` cannot cancel its worker. If a cancelled ``request``
released the ``asyncio.Lock`` while the orphaned exchange was still using the
pyserial handle, the NEXT exchange would ``reset_input_buffer`` + ``write`` +
``read`` on top of it — interleaved, corrupt framing (the real path:
``ZwoAm5Telescope.slew`` fires ``:Q#`` from its ``except`` while a settle poll
may still be in flight). These tests pin the bounded join instead.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from astrodeck.devices.serial_link import LinkError, SerialLink


class FakeSerial:
    """Thread-safe pyserial stand-in. ``read`` hands back one armed byte at a
    time and otherwise blocks for a short beat (pyserial's read timeout), so an
    exchange with nothing armed stays inside its read loop until the deadline —
    exactly the window a cancel opens."""

    def __init__(self):
        self._lk = threading.Lock()
        self._pending = bytearray()
        self.resets = 0
        self.writes: list[bytes] = []
        self.closed = False

    def arm(self, data: bytes) -> None:
        with self._lk:
            self._pending.extend(data)

    # --- pyserial surface (called from the worker thread) ---
    def reset_input_buffer(self):
        with self._lk:
            self.resets += 1

    def write(self, b):
        with self._lk:
            self.writes.append(bytes(b))

    def read(self, n=1):
        time.sleep(0.005)                      # stand-in for the 0.2s read timeout
        with self._lk:
            if self._pending:
                b = bytes(self._pending[:1])
                del self._pending[:1]
                return b
        return b""

    def close(self):
        self.closed = True


class TracedLink(SerialLink):
    """SerialLink instrumented at the exchange's READ phase (worker-thread side)
    so a test can see, exactly, how many exchanges are touching the port."""

    def __init__(self, ser):
        super().__init__("COM-TEST")
        self._ser = ser
        self._trace_lk = threading.Lock()
        self.inside = 0
        self.max_inside = 0

    def _mark(self, delta):
        with self._trace_lk:
            self.inside += delta
            self.max_inside = max(self.max_inside, self.inside)

    def _read_until_hash(self, deadline):
        self._mark(1)
        try:
            return super()._read_until_hash(deadline)
        finally:
            self._mark(-1)

    def _read_ack(self, deadline):
        self._mark(1)
        try:
            return super()._read_ack(deadline)
        finally:
            self._mark(-1)


@pytest.mark.parametrize("reply, wire", [("hash", b"10:13:56#"), ("ack", b"1")])
async def test_cancel_joins_orphan_before_releasing_the_port(reply, wire):
    ser = FakeSerial()
    link = TracedLink(ser)

    t = asyncio.create_task(link.request("GR", reply=reply, timeout=0.3))
    await asyncio.sleep(0.03)                  # let the exchange reach its read
    assert link.inside == 1

    t0 = time.monotonic()
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    elapsed = time.monotonic() - t0

    # The cancel WAITED for the orphaned worker (bounded by the exchange's own
    # deadline) instead of handing the port over mid-frame.
    assert link.inside == 0
    assert elapsed >= 0.1
    assert not link._lock.locked()

    # …so the next exchange gets clean framing: nothing else is on the wire.
    ser.arm(wire)
    got = await link.request("GR", reply=reply, timeout=0.3)
    assert got == wire.rstrip(b"#").decode()
    assert link.max_inside == 1                # never two exchanges at once


async def test_close_waits_for_an_in_flight_exchange():
    """``close`` must not null ``_ser`` / close the OS handle under a blocking
    read — it takes the same lock every exchange holds."""
    ser = FakeSerial()
    link = TracedLink(ser)

    t = asyncio.create_task(link.request("GR", timeout=0.3))
    await asyncio.sleep(0.03)
    closer = asyncio.create_task(link.close())
    await asyncio.sleep(0.05)
    assert ser.closed is False                 # still queued behind the exchange
    assert link._ser is ser

    with pytest.raises(LinkError):             # the exchange times out normally
        await t
    await closer
    assert ser.closed is True and link._ser is None
    assert link.inside == 0
