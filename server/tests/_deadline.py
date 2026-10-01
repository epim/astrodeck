"""A wall-clock deadline for polling loops in tests (#610).

``for _ in range(N): ...; await asyncio.sleep(dt)`` gives Linux and Windows
very different real time for the SAME test: Linux sleeps close to what it
asks for, but Windows rounds every sleep up to its own timer's ~15.6 ms
resolution. A loop sized against a Windows dev box can get 2-15x LESS real
wall time on a Linux CI runner (`range(2000) x sleep(0.001)` is ~31 s on
Windows, ~2 s on Linux), so it fails there whenever the runner is loaded --
first seen as a real CI failure and fixed ad hoc in a580fc4f; the rest of
the loops of this shape are catalogued in #610.

``wait_until`` replaces the round count with a ``time.monotonic()`` budget
in seconds, so both platforms get the same wall-clock patience regardless
of how long any one sleep actually takes. It never raises on a timeout: a
caller that wants its own failure message (naming what it was waiting for,
or state collected along the way) asserts on the return value itself,
exactly as it used to assert on the loop having found its condition."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable


async def wait_until(cond: Callable[[], bool], *, timeout_s: float,
                     interval_s: float = 0.01) -> bool:
    """Poll ``cond()`` until it is true or ``timeout_s`` of real time has
    passed, sleeping ``interval_s`` between polls (checked BEFORE each
    sleep, so a condition already true costs no sleep at all). Returns
    whether ``cond`` became true in time; a timeout is not an error here,
    only a ``False`` -- the caller decides what that means."""
    deadline = time.monotonic() + timeout_s
    while True:
        if cond():
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(interval_s)
