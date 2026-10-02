# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Small asyncio helpers shared across the server.

Only what more than one module needs and asyncio does not already say in one
line. Each helper names the defect that made it necessary.
"""
from __future__ import annotations

import asyncio


async def reap(task: asyncio.Future) -> None:
    """Wait for ``task``, which the caller has just cancelled, to finish.

    Whatever the task ends with is absorbed: its own ``CancelledError``, or an
    exception raised on its way out. A cancel aimed at the CALLER is not: it
    ends the caller with ``CancelledError``, once the task has finished.

    THE SHAPES THIS REPLACES (#235, widened to these two spellings by #252)::

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

        # or, written with BaseException (catches CancelledError too) or as
        # a try/except instead of a suppress:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    A caller cancelled while it waits there passes the cancel on to ``task``
    (``Task.cancel`` cancels the future its task is waiting on), and the
    ``CancelledError`` that comes back is the task's and the caller's at once.
    The suppress cannot tell them apart and eats both, so the caller runs on
    past a cancel as if nobody had asked, and whoever cancelled it, and is
    awaiting it, waits for it to finish on its own. It needs the cancel to land
    while the task is dying, so it is intermittent by construction: the native
    guider's stop has the longest window, a guide exposure in flight.

    ``gather(..., return_exceptions=True)`` returns the task's
    ``CancelledError`` or exception as a result, while a cancel of the caller
    cancels the gather itself, which raises it once its child is done. So the
    caller's cancel is kept, and the task is still dead when the caller hears
    it, which the replaced shape also promised (a loop's teardown cannot land
    on whatever the caller's canceller does next).
    """
    await asyncio.gather(task, return_exceptions=True)
