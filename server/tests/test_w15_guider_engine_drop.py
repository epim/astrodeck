# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A native guider's engine is only ever dropped on the thread that made it
(#700).

``astrodeck_native.GuideEngine`` is a pyo3 ``unsendable`` class. Let go of on
any thread but the one that made it, it raises "astrodeck_native::GuideEngine
is unsendable, but is being dropped on another thread", which Python can only
report as an unraisable exception (a ``PytestUnraisableExceptionWarning`` in a
test run), and it LEAKS the engine instead of freeing it.

WHO HELD THE LAST REFERENCE. Not the test the warning named: #700 saw it in
``test_w12_dither_zero_settle.py``, which builds no engine at all (a recording
double stands in for the guider), and never when that file ran alone. Scanning
the guider test families after every test (a scratch plugin, 222 tests, ``-n 4``)
found 32 tests that end with a ``NativeGuider`` still holding a real engine,
and, in 21 of them, one that a ``gc.collect()`` then freed: garbage in a
reference cycle (its tasks, events and locks make one) that nobody had
disconnected, waiting for the cyclic collector. The collector runs on
whichever thread allocates when it triggers, and a pytest-xdist worker has a
second thread (execnet's I/O) beside the event loop's: the scan saw
``threads=2`` at the teardown of ``test_zero_pixels_skips_the_dither_and_its_
settle`` itself, with such a guider alive. The same collection on the creating
thread is silent. All of that is reproduced without pytest in the scratch
probe recorded in the first test below.

THE FIX IS TWO THINGS, and a third that is deliberately NOT done. `disconnect`
releases the engine on the loop that is disconnecting it, which is where it was
made. A guider that is garbage without having been disconnected (every one of
the 32) is handled by its finaliser, which, on a thread that did not make the
engine, parks it for the one that did and tells that thread's loop to drop it.
And `stop_guiding` does NOT release it: the meridian flip stops the guider,
re-slews, and then asks the same engine to mirror its calibration
(`flip_calibration`, hub.py and sequence/engine.py) before the restart builds
the next one.

Mutants, each applied from a byte backup and restored (sha256 compared):

MUTANT "the finaliser drops where it stands" (``if owner is None or owner ==
threading.get_ident():`` replaced by ``if True:`` in
`NativeGuider._release_engine`): RED (observed), the unraisable error in
`test_a_guider_collected_on_another_thread_parks_its_engine`:
    E   AssertionError: the engine was dropped off its thread: [RuntimeError(
    'astrodeck_native::GuideEngine is unsendable, but is being dropped on
    another thread')]
MUTANT "parked, never told" (the ``loop.call_soon_threadsafe(
drop_parked_engines)`` call replaced by ``pass``): RED (observed), same test:
    E   AssertionError: the engine parked for this thread was never dropped
    by it: []
MUTANT "a start does not drain" (the ``drop_parked_engines()`` before the
engine is made in `start_guiding` deleted):
`test_the_next_start_drops_what_another_threads_collector_parked` is RED
(observed):
    E   AssertionError: the engine parked for this thread is still parked
    after a start
MUTANT "disconnect keeps the engine" (the ``self._release_engine()`` in
`disconnect` deleted): `test_disconnect_releases_the_engine_on_its_own_thread`
is RED (observed):
    E   assert <builtins.GuideEngine object at 0x...> is None
MUTANT "the finaliser still names the engine" (the ``del engine`` before the
owning loop is told, in `_release_engine`, deleted):
`test_the_drain_may_land_while_the_finaliser_is_still_running` is RED
(observed, every run; the plain parking test fails with it only by luck):
    E   AssertionError: the engine was dropped off its thread: [RuntimeError(
    'astrodeck_native::GuideEngine is unsendable, but is being dropped on
    another thread')]
MUTANT "stop releases it too" (``self._release_engine()`` added to
`stop_guiding`'s ``finally``): `test_stop_keeps_the_engine_for_the_flip` is RED
(observed):
    E   AssertionError: a stop released the engine, and the meridian flip
    still needs it
"""
from __future__ import annotations

import asyncio
import contextlib
import gc
import sys
import threading
import uuid

import pytest

from astrodeck.devices.sim import build_sim_rig
from astrodeck.providers import NATIVE_AVAILABLE

pytest.importorskip("astrodeck_native")  # skip cleanly when the wheel is absent
pytestmark = pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native wheel absent")


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path, monkeypatch):
    import astrodeck.config as config
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def unraisables(monkeypatch):
    """Every unraisable exception the interpreter reports while a test runs,
    as ``(exception, thread name)``: where a wrong-thread drop shows up."""
    seen: list[tuple[BaseException, str]] = []
    monkeypatch.setattr(
        sys, "unraisablehook",
        lambda u: seen.append((u.exc_value, threading.current_thread().name)))
    return seen


def _unsendable(seen) -> list[BaseException]:
    return [exc for exc, _where in seen
            if "GuideEngine is unsendable" in str(exc)]


async def _until(predicate, timeout: float = 20.0) -> bool:
    """Poll with a deadline (never a fixed sleep: #669, #675)."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


async def _guider_with_an_engine(tag: str):
    """A connected NativeGuider over a fresh sim rig whose start was begun and
    then stopped mid-walk, which leaves it holding a real engine and no loop."""
    from astrodeck.devices.base import DeviceError
    from astrodeck.guide.native import NativeGuider

    rig = build_sim_rig()
    cam, tel = rig["guide_camera"], rig["telescope"]
    await cam.connect()
    await tel.connect()
    g = NativeGuider(cam, tel, config={"image_scale_arcsec": 2.0,
                                       "exposure_s": 0.2},
                     profile_id=f"test-drop-{tag}-{uuid.uuid4().hex[:8]}")
    await g.connect()
    start = asyncio.ensure_future(g.start_guiding())
    assert await _until(lambda: g._engine is not None), "no engine was made"
    await g.stop_guiding()
    with contextlib.suppress(DeviceError, asyncio.CancelledError):
        await asyncio.wait_for(start, timeout=60.0)
    # The stopped start's wake-ups still hold the guider through the
    # exception's traceback until the loop has run them; a few turns, counted
    # and not timed, so that what holds it afterwards is the test's own doing.
    for _ in range(5):
        await asyncio.sleep(0)
    return g


def test_the_mechanism_a_real_engine_dropped_on_another_thread(unraisables):
    """The premise of everything below, with no guider in it: a real engine in
    a reference cycle, collected on a worker thread, raises the error the
    issue quotes (and the same collection on the creating thread does not).

    Recorded probe (scratch, outside the suite, CPython 3.12.10, the rig's
    wheel): ``collected on a worker thread -> RuntimeError('astrodeck_native::
    GuideEngine is unsendable, but is being dropped on another thread')``;
    ``collected on the creating thread -> no error``; ``refcount drop on a
    worker thread -> the same RuntimeError``."""
    import astrodeck_native as native

    def make_garbage() -> None:
        holder: dict = {}
        holder["me"] = holder               # only the cyclic collector frees it
        holder["engine"] = native.GuideEngine({"image_scale_arcsec": 2.0})

    gc.collect()                            # nothing else's garbage in the way
    unraisables.clear()
    make_garbage()
    worker = threading.Thread(target=gc.collect, name="collector")
    worker.start()
    worker.join(timeout=30)
    assert len(_unsendable(unraisables)) == 1, unraisables
    unraisables.clear()
    make_garbage()
    gc.collect()
    assert _unsendable(unraisables) == [], unraisables


async def test_a_guider_collected_on_another_thread_parks_its_engine(
        unraisables, monkeypatch):
    """The finaliser: an undisconnected guider freed by a collector running on
    some other thread does not drop its engine there. The engine is parked for
    the thread that made it and dropped by that thread, through the loop it was
    made on, with nothing reported."""
    import weakref

    import astrodeck.guide.native as native_mod

    g = await _guider_with_an_engine("park")
    me = threading.get_ident()
    assert g._engine is not None and g._engine_thread == me
    g.the_cycle = g          # what a real guider's tasks and locks amount to
    drops: list[tuple[int, int]] = []
    real_drop = native_mod.drop_parked_engines

    def drop_and_record() -> int:
        n = real_drop()
        drops.append((threading.get_ident(), n))
        return n
    monkeypatch.setattr(native_mod, "drop_parked_engines", drop_and_record)

    gone = weakref.ref(g)
    del g
    assert gone() is not None, "the guider was freed before the collector ran"
    unraisables.clear()
    drops.clear()
    # ANOTHER thread's collector frees it, as execnet's I/O thread can.
    await asyncio.get_running_loop().run_in_executor(None, gc.collect)
    assert gone() is None, "the other thread's collection did not free it"
    assert _unsendable(unraisables) == [], (
        f"the engine was dropped off its thread: {_unsendable(unraisables)}")
    # the loop it was made on drops what was parked, on this thread
    assert await _until(lambda: any(t == me and n >= 1 for t, n in drops)), (
        f"the engine parked for this thread was never dropped by it: {drops}")
    assert native_mod._PARKED_ENGINES.get(me) in (None, [])


class _WaitsForTheDrain:
    """Stands in for the loop the engine was made on: runs the drain on the
    real loop and holds the calling (collector) thread until it has run, so the
    drain is certain to land while the finaliser is still on the stack."""

    def __init__(self, loop) -> None:
        self.loop = loop

    def call_soon_threadsafe(self, callback, *args) -> None:
        done = threading.Event()

        def run() -> None:
            try:
                callback(*args)
            finally:
                done.set()
        self.loop.call_soon_threadsafe(run)
        assert done.wait(30), "the loop never ran the drain"


async def test_the_drain_may_land_while_the_finaliser_is_still_running(
        unraisables):
    """The owning thread can drain at once, while the collector's thread is
    still inside the finaliser. The engine must then be held by the parked list
    alone: a frame there that still names it would be the last holder, and
    would drop it on the collector's thread when it returned (the first version
    of the fix failed this in half of its runs, by luck of timing; the wait
    makes it certain).

    MUTANT "the finaliser still names the engine" (the ``del engine`` before
    the owning loop is told, in `_release_engine`, deleted): RED (observed):
        E   AssertionError: the engine was dropped off its thread: [RuntimeError(
        'astrodeck_native::GuideEngine is unsendable, but is being dropped on
        another thread')]"""
    import weakref

    g = await _guider_with_an_engine("race")
    g.the_cycle = g
    g._engine_loop = _WaitsForTheDrain(asyncio.get_running_loop())
    gone = weakref.ref(g)
    del g
    assert gone() is not None, "the guider was freed before the collector ran"
    unraisables.clear()
    await asyncio.get_running_loop().run_in_executor(None, gc.collect)
    assert gone() is None, "the other thread's collection did not free it"
    assert _unsendable(unraisables) == [], (
        f"the engine was dropped off its thread: {_unsendable(unraisables)}")


async def test_the_next_start_drops_what_another_threads_collector_parked(
        unraisables):
    """A closed loop cannot be told, so the thread that made the engine drops
    what was parked for it at its next ``start_guiding``, before making one
    more."""
    import astrodeck.guide.native as native_mod
    import astrodeck_native as native

    me = threading.get_ident()
    native_mod._PARKED_ENGINES[me] = [
        native.GuideEngine({"image_scale_arcsec": 2.0})]
    g = await _guider_with_an_engine("drain")
    assert native_mod._PARKED_ENGINES.get(me) is None, (
        "the engine parked for this thread is still parked after a start")
    assert _unsendable(unraisables) == [], unraisables
    await g.disconnect()


async def test_disconnect_releases_the_engine_on_its_own_thread(unraisables):
    """After ``disconnect`` nothing holds the engine but this test, and it dies
    here, on the thread that made it, when the test lets go.

    ``sys.getrefcount`` of an object with one other holder reads 2 (that
    holder and the call's argument); the guider's own ``_engine`` would make
    it 3."""
    g = await _guider_with_an_engine("disconnect")
    engine = g._engine
    assert engine is not None
    assert sys.getrefcount(engine) == 3, (
        "premise: the guider and this test each hold the engine")
    await g.disconnect()
    assert g._engine is None
    assert sys.getrefcount(engine) == 2
    del engine
    assert _unsendable(unraisables) == [], unraisables


async def test_stop_keeps_the_engine_for_the_flip():
    """The meridian flip stops the guider, re-slews and then mirrors the
    calibration held by the SAME engine, so a stop must not release it."""
    g = await _guider_with_an_engine("stop")
    assert g._engine is not None, (
        "a stop released the engine, and the meridian flip still needs it")
    await g.disconnect()


def test_a_guider_that_never_made_an_engine_is_collected_quietly(unraisables):
    """The persistence tests build a guider with ``NativeGuider.__new__`` and
    no ``__init__``: the finaliser must not need an attribute it never set."""
    from astrodeck.guide.native import NativeGuider

    for _ in range(3):
        NativeGuider.__new__(NativeGuider)
    gc.collect()
    assert unraisables == [], unraisables
