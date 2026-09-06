"""Asyncio-safe serial transport for native serial drivers (AM5N first).

One ``SerialLink`` owns one pyserial handle. All blocking pyserial calls run in
``asyncio.to_thread``; a single ``asyncio.Lock`` serializes whole
request/response exchanges so concurrent hub polls can never interleave frames.

``request_sync`` is the one exception, and a deliberate one: a pulse-guide STOP
command must reach the mount even while the event loop is blocked (GN-02), so it
is callable from ANY thread and never touches the loop. Because it cannot take
the ``asyncio.Lock``, the port is ALSO guarded at the thread level by
``_port_lock``, which both it and the async exchange's worker hold for the whole
write+read.

Reply modes mirror the AM5 wire truth (see devices/lx200.py):
  - ``"hash"``  : read until ``#``; returns the reply WITHOUT the ``#``.
  - ``"ack"``   : read ONE byte (``1``/``0``); if that byte is ``e`` the refusal
                  form (``e14#``) is completed by reading to ``#`` -> ``"e14"``.
  - ``"none"``  : fire-and-forget (motion class) — write only, returns None.
"""
from __future__ import annotations

import asyncio
import contextlib
import threading
import time

try:  # guarded: absent pyserial must not break imports (non-serial installs)
    import serial  # type: ignore
except ImportError:  # pragma: no cover - exercised only without the dep
    serial = None  # type: ignore

from . import lx200


class LinkError(Exception):
    """Transport-level failure (open/read timeout/port gone)."""


class SerialLink:
    def __init__(self, port_path: str, baud: int = 9600):
        self.port_path = port_path
        self.baud = baud            # USB-CDC: value is a no-op on the AM5
        self._ser = None
        self._lock = asyncio.Lock()
        #: THREAD-level guard on the handle, held for the whole write+read by
        #: the async exchange's worker AND by ``request_sync``. The asyncio lock
        #: cannot serialize those two against each other: one of them has no
        #: loop to await on.
        self._port_lock = threading.Lock()
        #: Set by ``_abandon``, cleared by ``open``/``close``. See ``needs_reopen``.
        self._abandoned = False

    @property
    def is_open(self) -> bool:
        """Is there a usable handle right now? The question a driver deciding
        whether to reopen should ask — it stays true through a FAILED reopen,
        where ``needs_reopen`` cannot (a failed reopen has already been through
        ``close``, which by design clears the abandoned flag)."""
        return self._ser is not None

    @property
    def needs_reopen(self) -> bool:
        """True when the port was ABANDONED rather than deliberately closed, so
        it has to be reopened before it can carry anything again.

        THE DISTINCTION IS THE WHOLE POINT. ``close()`` is a teardown somebody
        asked for and must stay shut; ``_abandon`` is a fault nobody chose and
        is supposed to be recovered from. Until 2026-08-09 the two were
        indistinguishable from outside — both just left ``_ser`` None — so
        ``_abandon``'s promise that "the driver reopens" had nothing to key off
        and was never kept. That morning the link was abandoned at 02:38:26 and
        every mount command failed for the next five and a half hours, through
        sunrise, while the dawn-park net retried 139 times without once
        attempting the reopen this flag now makes possible."""
        return self._ser is None and self._abandoned

    async def open(self) -> None:
        if serial is None:
            raise LinkError("pyserial is not installed")
        try:
            # write_timeout is NOT optional: pyserial's default is None (block
            # forever). A bumped cable / stalled CDC-ACM TX buffer would park the
            # worker thread inside write() with no deadline at all, which the
            # cancel-join below would then have to wait out.
            self._ser = await asyncio.to_thread(
                serial.Serial, self.port_path, self.baud,
                timeout=0.2, write_timeout=2.0)
        except Exception as exc:  # noqa: BLE001 - surface as one link error
            raise LinkError(f"cannot open {self.port_path}: {exc}") from exc
        # Only on success: a failed reopen must leave the link STILL flagged as
        # needing one, or a driver that retries would see a healthy-looking link
        # and stop trying.
        self._abandoned = False

    def _abandon(self, task) -> None:
        """The orphaned exchange outlived even the hard join bound: give up on
        the port rather than hand it back with an unknown writer on it.

        ``_ser`` is dropped (so the lock can be released and later commands fail
        fast with ``LinkError`` -> the driver reopens) and the never-joined task's
        eventual result is consumed so it can't log "exception was never
        retrieved" minutes later. The OS handle is deliberately NOT closed: the
        worker thread is still using it."""
        self._ser = None
        self._abandoned = True
        # ...and neither is the thread guard: the orphan holds ``_port_lock``
        # and will never release it. Handing that lock to the REOPENED port
        # would wedge every later exchange behind a dead thread — the whole
        # failure this method exists to avoid, one layer down. The orphan keeps
        # the old lock (it still guards the handle it is using); the next open
        # gets a fresh one.
        self._port_lock = threading.Lock()
        task.add_done_callback(
            lambda t: None if t.cancelled() else t.exception())
        try:
            from ..events import bus
            bus.log("error",
                    f"serial {self.port_path}: exchange did not return — "
                    "marking the link unusable (it will be reopened)", "mount")
        except Exception:       # pragma: no cover - logging must never raise here
            pass

    def _read_until_hash(self, deadline: float) -> str:
        """Blocking helper (thread): accumulate bytes until '#' or deadline."""
        import time
        buf = bytearray()
        while time.monotonic() < deadline:
            b = self._ser.read(1)
            if not b:
                continue
            if b == b"#":
                return buf.decode("ascii", "replace")
            buf.extend(b)
        raise LinkError(
            f"timeout waiting for '#' on {self.port_path} (got {bytes(buf)!r})")

    def _read_ack(self, deadline: float) -> str:
        """Blocking helper (thread): one ack byte; 'e' completes to '#'."""
        import time
        while time.monotonic() < deadline:
            b = self._ser.read(1)
            if not b:
                continue
            if b == b"e":
                rest = self._read_until_hash(deadline)
                return "e" + rest
            return b.decode("ascii", "replace")
        raise LinkError(f"timeout waiting for ack on {self.port_path}")

    def _raw_exchange(self, cmd: str, reply: str, timeout: float) -> str | None:
        """One write + read on the handle. CALLER HOLDS ``_port_lock``.

        Blocking; runs on a worker thread (``request``) or on whatever thread
        called ``request_sync``. Never on the event loop."""
        self._ser.reset_input_buffer()
        self._ser.write(lx200.build(cmd))
        deadline = time.monotonic() + timeout
        if reply == "hash":
            return self._read_until_hash(deadline)
        if reply == "ack":
            # Read it even though nobody wants the value: an unread ack byte is
            # left in the input buffer and answers the NEXT command instead.
            return self._read_ack(deadline)
        return None

    def _shut_error(self) -> LinkError:
        """Which kind of shut is this? (See ``request`` for why it matters.)"""
        return LinkError(
            "the link was dropped after a stalled exchange and has not been "
            "reopened" if self._abandoned else "link not open")

    def request_sync(self, cmd: str, *, reply: str = "hash",
                     timeout: float = 1.5) -> str | None:
        """``request`` for callers that have no event loop to await on.

        WHY THIS EXISTS (GN-02, rig 2026-09-06). ``pulse_guide`` emulates a
        pulse as start / sleep / stop, and the mount moves at 15 arcsec per
        second in between — so the sleep is not a delay, it is the LENGTH OF THE
        MOVE. When something blocks the loop thread (a synchronous frame
        readout, a filter-wheel move) the stop is queued behind it and the mount
        keeps going: +83, +128 and +35 arcsec jumps in one night. A stop must be
        able to leave from a plain thread timer, which means a door into this
        transport that does not go through the loop.

        Safe against the async path because both take ``_port_lock`` for the
        whole write+read. The wait for it is BOUNDED (``timeout + 0.5``, the
        same bound ``request`` uses to join an orphan) and failing it raises
        ``LinkError``: a caller on a timer thread must never be parked behind a
        stuck exchange, it must fall back to whoever can recover the port.

        No relink and no ``_abandon`` here, deliberately — both are loop-thread
        recovery paths. This door reports the failure and lets the coroutine
        that armed it deal with the consequences."""
        # Fail fast BEFORE the lock: after an abandonment the orphan holds the
        # old lock forever, and a dead port is not worth two seconds of a
        # watchdog thread's life to discover.
        if self._ser is None:
            raise self._shut_error()
        if not self._port_lock.acquire(timeout=timeout + 0.5):
            raise LinkError(
                f"{self.port_path} is busy: an exchange did not finish within "
                f"{timeout + 0.5:.1f}s")
        try:
            if self._ser is None:       # closed while we waited for the lock
                raise self._shut_error()
            return self._raw_exchange(cmd, reply, timeout)
        finally:
            self._port_lock.release()

    async def request(self, cmd: str, *, reply: str = "hash",
                      timeout: float = 1.5) -> str | None:
        """Send ``cmd`` (unframed, e.g. ``"GR"``) and read per ``reply`` mode.

        CANCEL SAFETY (single-owner port). ``asyncio.to_thread`` cannot cancel
        the worker: if the awaiting coroutine is cancelled or times out
        mid-exchange, the thread keeps writing/reading ``self._ser``. Releasing
        ``self._lock`` at that moment would let the NEXT exchange
        ``reset_input_buffer`` + ``write`` on top of the orphan -> interleaved,
        corrupt framing (a real path: ``ZwoAm5Telescope.slew`` halts with ``:Q#``
        from its ``except`` while the settle poll may still be in flight). So on
        an abnormal exit we JOIN the orphaned exchange before the ``async with``
        releases the lock. That join is BOUNDED TWICE: by the exchange's own
        deadline (``timeout``, default 1.5 s) and, because a worker can also park
        in a syscall the deadline doesn't govern, by a hard
        ``wait_for(timeout + 0.5)``. If even that expires the port is declared
        UNUSABLE (``_ser = None``) instead of being handed back with an unknown
        in-flight writer on it: the lock is released, every later command fails
        fast with ``LinkError``, and the driver reopens rather than the whole
        mount subsystem wedging until process restart."""
        async with self._lock:
            # Inside the lock: close() nulls _ser while holding it, so checking
            # outside would race a teardown into AttributeError (call sites catch
            # only LinkError).
            if self._ser is None:
                # Say WHICH kind of shut this is. "link not open" was the only
                # sentence available on 2026-08-09 and it reads as "nobody
                # connected the mount", which sent the reader looking at cables
                # and USB enumeration -- both of which were fine.
                raise LinkError(
                    "the link was dropped after a stalled exchange and has not "
                    "been reopened" if self._abandoned else "link not open")

            def _exchange():
                # The thread guard, not just the asyncio one: request_sync can
                # be writing from a timer thread while this worker reads.
                with self._port_lock:
                    return self._raw_exchange(cmd, reply, timeout)
            task = asyncio.ensure_future(asyncio.to_thread(_exchange))
            try:
                return await asyncio.shield(task)
            finally:
                if not task.done():
                    # Suppress a second cancel (and any exchange error) so the
                    # join always completes and the original exception wins.
                    with contextlib.suppress(BaseException):
                        try:
                            await asyncio.wait_for(asyncio.shield(task),
                                                   timeout + 0.5)
                        except asyncio.TimeoutError:
                            self._abandon(task)
                elif not task.cancelled():
                    # Already finished (normal path, or it raised in the same
                    # tick a cancel landed): mark its result retrieved so a
                    # cancelled-away exchange error can't log "never retrieved".
                    task.exception()

    async def close(self) -> None:
        """Close the port. Takes ``self._lock`` so teardown waits for any
        in-flight exchange instead of nulling ``self._ser`` (AttributeError) or
        closing the OS handle under a blocking read."""
        async with self._lock:
            ser, self._ser = self._ser, None
            # A deliberate close is not a fault to recover from: clearing this
            # is what keeps a reopen loop from fighting a teardown. Also makes
            # close() the safe way to reset an abandoned link before reopening.
            self._abandoned = False
            if ser is not None:
                try:
                    await asyncio.to_thread(self._close_under_port_lock, ser)
                except Exception:  # noqa: BLE001 - best-effort close
                    pass

    def _close_under_port_lock(self, ser) -> None:
        """Close the handle once no thread is mid-exchange on it (bounded).

        ``request_sync`` runs OUTSIDE the asyncio lock, so the lock above does
        not wait for it; the thread guard does. A guard that never comes free
        (an orphaned exchange) must not stop the close: after the bound the
        handle is closed regardless, which is what ``_abandon`` would have
        left it to anyway."""
        got = self._port_lock.acquire(timeout=2.0)
        try:
            ser.close()
        finally:
            if got:
                self._port_lock.release()
