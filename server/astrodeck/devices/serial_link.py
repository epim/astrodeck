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
            self._ser = await asyncio.to_thread(
                serial.Serial, self.port_path, self.baud, timeout=0.2)
        except Exception as exc:  # noqa: BLE001 - surface as one link error
            raise LinkError(f"cannot open {self.port_path}: {exc}") from exc

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
        """Send ``cmd`` (unframed, e.g. ``"GR"``) and read per ``reply`` mode."""
        if self._ser is None:
            raise LinkError("link not open")
        async with self._lock:
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
            return await asyncio.to_thread(_exchange)

    async def close(self) -> None:
        ser, self._ser = self._ser, None
        if ser is not None:
            try:
                await asyncio.to_thread(ser.close)
            except Exception:  # noqa: BLE001 - best-effort close
                pass
