"""Asyncio-safe serial transport for native serial drivers (AM5N first).

One ``SerialLink`` owns one pyserial handle. All blocking pyserial calls run in
``asyncio.to_thread``; a single ``asyncio.Lock`` serializes whole
request/response exchanges so concurrent hub polls can never interleave frames.

Reply modes mirror the AM5 wire truth (see devices/lx200.py):
  - ``"hash"``  : read until ``#``; returns the reply WITHOUT the ``#``.
  - ``"ack"``   : read ONE byte (``1``/``0``); if that byte is ``e`` the refusal
                  form (``e14#``) is completed by reading to ``#`` -> ``"e14"``.
  - ``"none"``  : fire-and-forget (motion class) — write only, returns None.
"""
from __future__ import annotations

import asyncio
import contextlib

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

    def _abandon(self, task) -> None:
        """The orphaned exchange outlived even the hard join bound: give up on
        the port rather than hand it back with an unknown writer on it.

        ``_ser`` is dropped (so the lock can be released and later commands fail
        fast with ``LinkError`` -> the driver reopens) and the never-joined task's
        eventual result is consumed so it can't log "exception was never
        retrieved" minutes later. The OS handle is deliberately NOT closed: the
        worker thread is still using it."""
        self._ser = None
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
                raise LinkError("link not open")

            def _exchange():
                import time
                self._ser.reset_input_buffer()
                self._ser.write(lx200.build(cmd))
                deadline = time.monotonic() + timeout
                if reply == "hash":
                    return self._read_until_hash(deadline)
                if reply == "ack":
                    return self._read_ack(deadline)
                return None
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
            if ser is not None:
                try:
                    await asyncio.to_thread(ser.close)
                except Exception:  # noqa: BLE001 - best-effort close
                    pass
