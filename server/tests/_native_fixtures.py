# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Closing the native guiders a test makes (#1119, #700).

``astrodeck_native.GuideEngine`` is pyo3 ``unsendable``: dropped on a thread
other than the one that made it, it raises and leaks. A ``NativeGuider`` a test
leaves behind is garbage in a reference cycle (its tasks, events and locks make
one), and the cyclic collector frees it on whichever thread allocates when it
triggers; under xdist that is as likely to be execnet's I/O thread as the event
loop's, which is how a test that builds no engine at all was blamed for the
drop (test_w15_guider_engine_drop.py).

``native_guiders_closed`` is the test side of the cure: it notes every guider
made inside the block, by weak reference, and releases each one's engine as the
block ends, on the thread the test ran on, which is the thread that made it.
The suite wraps every test in it (conftest.py, ``_native_guiders_are_closed``),
so no test leans on the product's own parking of an engine collected elsewhere.
"""
from __future__ import annotations

import contextlib
import functools
import weakref
from collections.abc import Iterator

import pytest


@contextlib.contextmanager
def native_guiders_closed() -> Iterator[list[weakref.ref]]:
    """Release the engine of every ``NativeGuider`` made inside the block when
    the block ends. Yields the weak references taken so far, for a test to
    read; the list is empty, and nothing is patched, when the native wheel is
    absent."""
    from astrodeck.providers import NATIVE_AVAILABLE

    made: list[weakref.ref] = []
    if not NATIVE_AVAILABLE:
        yield made
        return
    from astrodeck.guide.native import NativeGuider

    real_init = NativeGuider.__init__

    @functools.wraps(real_init)
    def noting_init(self, *args, **kwargs) -> None:
        real_init(self, *args, **kwargs)
        made.append(weakref.ref(self))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(NativeGuider, "__init__", noting_init)
        try:
            yield made
        finally:
            for ref in made:
                guider = ref()
                if guider is not None:
                    guider._release_engine()
