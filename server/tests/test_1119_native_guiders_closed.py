# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A test that makes a NativeGuider does not leave its engine to the garbage
collector (#1119, #700).

``astrodeck_native.GuideEngine`` is a pyo3 ``unsendable`` class: let go of on
any thread but the one that made it, it raises "is unsendable, but is being
dropped on another thread" and leaks. A guider that a test leaves behind is
garbage in a reference cycle, and the cyclic collector frees it on whichever
thread happens to allocate when it triggers, which under xdist is as likely to
be execnet's I/O thread as the event loop's (test_w15_guider_engine_drop.py has
the whole account). The product now parks such an engine for its own thread,
but a test suite should not lean on that: ``_native_fixtures`` releases every
guider a test made, on the thread the test ran on, as the test ends.

MUTANT "the fixture closes nothing" (``native_guiders_closed`` yields and
returns without releasing): `test_every_guider_a_test_made_is_released` is
RED, ``assert <builtins.GuideEngine object at 0x...> is None``.
"""
from __future__ import annotations

import asyncio
import contextlib
import gc
import uuid
import weakref

import pytest

from _native_fixtures import native_guiders_closed
from astrodeck.devices.sim import build_sim_rig

pytestmark = pytest.mark.native


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path, monkeypatch):
    import astrodeck.config as config
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    return tmp_path


async def _guider_with_an_engine(tag: str):
    """A connected NativeGuider over a fresh sim rig, begun and stopped
    mid-walk, which leaves it holding a real engine and no loop."""
    from astrodeck.devices.base import DeviceError
    from astrodeck.guide.native import NativeGuider

    rig = build_sim_rig()
    cam, tel = rig["guide_camera"], rig["telescope"]
    await cam.connect()
    await tel.connect()
    g = NativeGuider(cam, tel, config={"image_scale_arcsec": 2.0,
                                       "exposure_s": 0.2},
                     profile_id=f"test-closed-{tag}-{uuid.uuid4().hex[:8]}")
    await g.connect()
    start = asyncio.ensure_future(g.start_guiding())
    loop = asyncio.get_running_loop()
    end = loop.time() + 20.0
    while g._engine is None and loop.time() < end:
        await asyncio.sleep(0.01)
    assert g._engine is not None, "no engine was made"
    await g.stop_guiding()
    with contextlib.suppress(DeviceError, asyncio.CancelledError):
        await asyncio.wait_for(start, timeout=60.0)
    # The stopped start's wake-ups hold the guider through the exception's
    # traceback until the loop has run them: a few turns, counted and not
    # timed, so that what holds it afterwards is the test's own doing.
    for _ in range(5):
        await asyncio.sleep(0)
    return g


async def test_every_guider_a_test_made_is_released():
    with native_guiders_closed():
        first = await _guider_with_an_engine("first")
        second = await _guider_with_an_engine("second")
        assert first._engine is not None and second._engine is not None
    assert first._engine is None
    assert second._engine is None


async def test_a_guider_made_before_the_block_is_left_alone():
    """The block closes what was made inside it, not every guider alive."""
    outside = await _guider_with_an_engine("outside")
    with native_guiders_closed():
        pass
    assert outside._engine is not None
    await outside.disconnect()


async def test_the_block_does_not_keep_a_guider_alive():
    """It holds weak references: a guider the test dropped is freed by the
    collector as before, and the block finds nothing to close."""
    with native_guiders_closed():
        g = await _guider_with_an_engine("weak")
        await g.disconnect()
        gone = weakref.ref(g)
        g.the_cycle = g
        del g
        gc.collect()
        assert gone() is None, "the block kept the guider alive"


async def test_the_constructor_is_put_back():
    from astrodeck.guide.native import NativeGuider

    before = NativeGuider.__init__
    with native_guiders_closed():
        assert NativeGuider.__init__ is not before
    assert NativeGuider.__init__ is before


def test_the_suite_applies_it_to_every_test():
    """The autouse fixture in conftest.py is what puts the block around each
    test; without it nothing above is in force. Inside an ordinary test the
    constructor is the noting one."""
    from astrodeck.guide.native import NativeGuider

    assert hasattr(NativeGuider.__init__, "__wrapped__"), (
        "conftest.py's _native_guiders_are_closed is not wrapping this test")
