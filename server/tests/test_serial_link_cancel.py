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


async def test_open_sets_a_write_timeout():
    """pyserial's default ``write_timeout`` is None — block forever. A stalled
    CDC-ACM TX buffer would then park the worker in ``write()`` with no deadline
    at all (and the cancel-join would have to wait it out)."""
    seen = {}

    class _FakeSerialMod:
        @staticmethod
        def Serial(port, baud, **kw):
            seen.update(port=port, baud=baud, **kw)
            return FakeSerial()

    import astrodeck.devices.serial_link as mod
    orig, mod.serial = mod.serial, _FakeSerialMod
    try:
        link = SerialLink("COM-TEST", 9600)
        await link.open()
    finally:
        mod.serial = orig
    assert seen["timeout"] == 0.2 and seen["write_timeout"] == 2.0


class StuckLink(SerialLink):
    """An exchange whose worker ignores the deadline entirely (the parked-write /
    stuck-syscall case the exchange deadline cannot govern)."""

    def __init__(self, ser, block_s=1.2):
        super().__init__("COM-STUCK")
        self._ser = ser
        self._block_s = block_s

    def _read_until_hash(self, deadline):
        time.sleep(self._block_s)
        return "stuck"


async def test_unreturning_exchange_marks_the_link_unusable_instead_of_wedging():
    """The join is bounded TWICE. If even the hard bound expires, the port is
    declared unusable so the lock is released and later commands fail fast (the
    driver reopens) — rather than every future mount command blocking forever."""
    link = StuckLink(FakeSerial())

    t = asyncio.create_task(link.request("GR", timeout=0.1))
    await asyncio.sleep(0.03)
    t0 = time.monotonic()
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    elapsed = time.monotonic() - t0

    assert 0.4 <= elapsed < 1.1              # bounded by timeout + 0.5, not by 1.2
    assert not link._lock.locked()           # the lock IS released
    assert link._ser is None                 # ...and the link is marked unusable
    with pytest.raises(LinkError):           # so the next command fails fast
        await link.request("GR", timeout=0.1)
    # close() must stay callable (and not touch the handle the orphan still uses)
    await link.close()


async def test_an_abandoned_link_advertises_that_it_needs_reopening():
    """The flag that makes "(it will be reopened)" a fact instead of a comment.

    On 2026-08-09 this exact path fired at 02:38:26 and the mount answered
    nothing for the next five and a half hours, through sunrise: an abandoned
    link and a deliberately closed one both just left ``_ser`` None, so no
    caller could tell "recover me" from "stay shut" and nothing tried. Pin the
    distinction at the transport, where it is made."""
    link = StuckLink(FakeSerial())
    assert not link.needs_reopen, "precondition: a fresh link is not in recovery"

    t = asyncio.create_task(link.request("GR", timeout=0.1))
    await asyncio.sleep(0.03)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t

    assert link.needs_reopen, (
        "an abandoned port must ASK to be reopened — this flag is the only "
        "thing standing between a stalled exchange and a dead mount until "
        "somebody restarts the process")
    # And it says which kind of shut it is. "link not open" reads as "nobody
    # connected the mount" and sent the 2026-08-09 reader hunting cables and
    # USB enumeration, both of which were entirely healthy.
    with pytest.raises(LinkError, match="dropped"):
        await link.request("GR", timeout=0.1)


async def test_a_deliberate_close_is_not_a_fault_to_recover_from():
    """``close`` must CLEAR the flag, or a reopen loop fights every teardown."""
    link = StuckLink(FakeSerial())
    t = asyncio.create_task(link.request("GR", timeout=0.1))
    await asyncio.sleep(0.03)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    assert link.needs_reopen, "precondition: abandoned"

    await link.close()

    assert not link.needs_reopen, (
        "a close somebody asked for must stay closed; only a FAULT asks to be "
        "reopened")


async def test_a_failed_reopen_leaves_the_link_still_asking_to_be_reopened():
    """``open`` clears the flag only on SUCCESS.

    Clearing it up front would make one failed reopen look like a healthy link,
    and the driver would stop trying — the same silent-give-up shape as the bug
    this whole change exists to fix."""
    class _Refuses:
        Serial = staticmethod(
            lambda *a, **k: (_ for _ in ()).throw(OSError("port busy")))

    link = StuckLink(FakeSerial())
    t = asyncio.create_task(link.request("GR", timeout=0.1))
    await asyncio.sleep(0.03)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    assert link.needs_reopen, "precondition: abandoned"
    link._ser = None

    import astrodeck.devices.serial_link as sl
    old, sl.serial = sl.serial, _Refuses()
    try:
        with pytest.raises(LinkError):
            await link.open()
    finally:
        sl.serial = old

    assert link.needs_reopen, (
        "the reopen failed, so the link is still broken and must still say so")


async def test_request_on_a_closed_link_raises_linkerror_not_attributeerror():
    """The not-open check lives INSIDE the lock: ``close`` nulls ``_ser`` while
    holding it, so a teardown racing a poll must still surface as ``LinkError``
    (call sites catch only that)."""
    ser = FakeSerial()
    link = TracedLink(ser)

    t = asyncio.create_task(link.request("GR", timeout=0.2))
    await asyncio.sleep(0.03)
    closer = asyncio.create_task(link.close())
    racer = asyncio.create_task(link.request("GR", timeout=0.2))

    with pytest.raises(LinkError):
        await t
    await closer
    with pytest.raises(LinkError):
        await racer


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
