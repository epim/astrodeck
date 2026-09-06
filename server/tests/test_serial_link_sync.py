"""``SerialLink.request_sync``: one port, two callers, no interleaving.

GN-02 gives the AM5 driver a way to put a pulse's STOP command on the wire from
a plain thread while the event loop is blocked. That entry point bypasses the
``asyncio.Lock`` (it has no loop to await on), so the port needs a guard at the
THREAD level: ``_port_lock``, taken by both the async exchange's worker and by
``request_sync``. Without it the stop would ``reset_input_buffer`` + ``write``
on top of an in-flight read — the exact corrupt-framing failure the asyncio lock
exists to prevent, arriving through a different door.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from astrodeck.devices.serial_link import LinkError, SerialLink


class FakeSerial:
    """Thread-safe pyserial stand-in: ``read`` hands back one armed byte at a
    time and otherwise blocks for a short beat (the read timeout)."""

    def __init__(self):
        self._lk = threading.Lock()
        self._pending = bytearray()
        self.writes: list[bytes] = []
        self.resets = 0
        self.closed = False

    def arm(self, data: bytes) -> None:
        with self._lk:
            self._pending.extend(data)

    def reset_input_buffer(self):
        with self._lk:
            self.resets += 1

    def write(self, b):
        with self._lk:
            self.writes.append(bytes(b))

    def read(self, n=1):
        time.sleep(0.005)
        with self._lk:
            if self._pending:
                b = bytes(self._pending[:1])
                del self._pending[:1]
                return b
        return b""

    def close(self):
        self.closed = True


async def test_request_sync_waits_for_an_in_flight_exchange():
    ser = FakeSerial()
    link = SerialLink("COM-TEST")
    link._ser = ser

    # An async read is in flight and parked in its read loop (nothing armed).
    task = asyncio.create_task(link.request("GR", timeout=2.0))
    await asyncio.sleep(0.05)
    assert ser.writes == [b":GR#"]

    out: dict = {}

    def worker():
        try:
            out["ret"] = link.request_sync("Qn", reply="none")
        except BaseException as exc:      # noqa: BLE001 - reported, not raised
            out["err"] = exc

    th = threading.Thread(target=worker)
    th.start()
    await asyncio.sleep(0.15)
    # THE ASSERTION. The stop is queued behind the reply, not spliced into it.
    assert ser.writes == [b":GR#"], "request_sync wrote into a live exchange"
    assert "ret" not in out and "err" not in out

    ser.arm(b"10:13:56#")
    assert await task == "10:13:56"
    th.join(2.0)
    assert not th.is_alive()
    assert out.get("err") is None, out.get("err")
    assert ser.writes == [b":GR#", b":Qn#"]


async def test_request_sync_on_a_closed_port_raises_link_error():
    link = SerialLink("COM99")
    with pytest.raises(LinkError, match="link not open"):
        link.request_sync("GR")


async def test_request_sync_on_an_abandoned_port_says_so():
    """The abandoned-vs-closed distinction survives the sync door too — a
    dropped link must read as "reopen me", not as "nobody plugged it in"."""
    link = SerialLink("COM-TEST")
    link._ser = None
    link._abandoned = True
    with pytest.raises(LinkError, match="dropped"):
        link.request_sync("Qn", reply="none")


async def test_request_sync_reads_the_ack_so_nothing_is_left_behind():
    """Ack mode consumes its one byte. A stop that left the '1' in the input
    buffer would hand it to the NEXT exchange as a reply to a different
    command (the AM5 answers :Te# with '1')."""
    ser = FakeSerial()
    link = SerialLink("COM-TEST")
    link._ser = ser
    ser.arm(b"1")
    assert link.request_sync("Te", reply="ack") == "1"
    assert ser.writes == [b":Te#"]
    assert ser._pending == bytearray()


async def test_request_sync_gives_up_rather_than_blocking_forever():
    """A worker parked on the port (the abandoned-exchange case) must not park
    the caller with it: the wait is bounded and fails as a LinkError."""
    ser = FakeSerial()
    link = SerialLink("COM-TEST")
    link._ser = ser
    link._port_lock.acquire()             # stand in for a stuck exchange
    try:
        t0 = time.monotonic()
        with pytest.raises(LinkError, match="busy|did not"):
            await asyncio.to_thread(link.request_sync, "Qn", reply="none",
                                    timeout=0.05)
        assert time.monotonic() - t0 < 2.0
    finally:
        link._port_lock.release()
    assert ser.writes == []
