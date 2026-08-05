"""#21: the idle guide-cam preview served ONE frame and then froze.

``NativeGuider.guide_frame``'s on-demand branch was gated on ``_last_frame is
None`` — and nothing ever empties that cache. ``stop_guiding`` deliberately
keeps the last frame, and the only other writer is the guide loop, so the first
exposure the guider ever took became the permanent answer: the "Guide cam" panel
on Capture/Polar re-requests every 2.5 s (GuideFramePreview.tsx) and got that one
frame re-encoded all night, off a camera that was connected, idle and free.

An emptiness test cannot express "the picture is old". Age can, so the cache
carries a timestamp and the idle branch fires on it.

Nothing here needs the native wheel, the hub or the route: these drive
``guide_frame`` against a fake camera.
"""
from __future__ import annotations

import asyncio
import contextlib

import numpy as np

import astrodeck.guide.native as nativemod
from astrodeck.devices.base import Camera, CameraFrame
from astrodeck.guide.native import NativeGuider

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _star_frame(x: int = 32) -> np.ndarray:
    a = np.full((64, 64), 300, dtype="uint16")
    a[32, x] = 40000
    return a


class _FakeGuideCam(Camera):
    """The smallest camera that can hand back a guide frame, plus the two knobs
    the overlap tests need: a count taken the moment ``expose`` is ENTERED (a
    second start is the event we must never produce, and counting on return
    would miss it) and a gate, because a 1 s exposure plus USB readout plus a
    PNG encode can outlast the panel's poll and instant fakes hide that."""

    def __init__(self, name: str = "ZWO ASI guide"):
        super().__init__(name)
        self.connected = True
        self.started = 0
        self.gate: asyncio.Event | None = None
        self.fail: str | None = None
        self.star_x = 32

    async def connect(self) -> None: self.connected = True
    async def disconnect(self) -> None: self.connected = False
    async def abort_exposure(self) -> None: pass

    async def expose(self, seconds, gain, offset, binning=1, light=True,
                     save=False, target="") -> CameraFrame:
        self.started += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise RuntimeError(self.fail)
        return CameraFrame(data=_star_frame(self.star_x), exposure_s=seconds,
                           gain=gain, offset=offset, binning=binning,
                           bayer_pattern=None, temperature_c=None, timestamp=0.0)


def _guider(cam: _FakeGuideCam) -> NativeGuider:
    g = NativeGuider(cam, object(), config={"exposure_s": 0.01}, profile_id=None)
    g.connected = True
    return g


# --------------------------------------------------------------- the freeze

async def test_a_poll_past_the_ttl_reaches_the_sensor_again(monkeypatch):
    """THE bug. Poll, poll, poll: the second one inside the TTL is honestly the
    cache (the sensor has had no time to show anything new), the third one after
    it must be a real exposure."""
    monkeypatch.setattr(nativemod, "_IDLE_PREVIEW_TTL_S", 0.05)
    cam = _FakeGuideCam()
    g = _guider(cam)

    first = await g.guide_frame()
    assert first and first[:8] == PNG_MAGIC
    assert cam.started == 1

    assert await g.guide_frame() == first
    assert cam.started == 1, "inside the TTL the cache is the honest answer"

    await asyncio.sleep(0.06)
    cam.star_x = 20                      # the field drifted, as a guide field does
    later = await g.guide_frame()
    assert cam.started == 2, "a stale cache must be re-exposed, not re-encoded"
    assert later != first, "the bytes have to follow the sensor"


async def test_a_shared_imaging_sensor_is_never_re_exposed_on_age(monkeypatch):
    """The OAG case, where the "guide camera" IS the imaging camera.

    The age gate above is right for a rig with a camera of its own to expose,
    and wrong here: this sensor belongs to the imaging train, so re-grabbing it
    every couple of seconds would interrupt a running sequence for a panel
    nobody is guiding with. Emptiness still grabs, so such a rig keeps exactly
    the one-frame-per-panel-open behaviour it had before the age gate existed.

    The hub refuses this case on its guide-CAMERA path but reaches that test
    only when no guider answered, so a native guider walks straight past it."""
    monkeypatch.setattr(nativemod, "_IDLE_PREVIEW_TTL_S", 0.05)
    cam = _FakeGuideCam()
    g = _guider(cam)
    g.shares_the_imaging_sensor = True

    first = await g.guide_frame()
    assert first and first[:8] == PNG_MAGIC
    assert cam.started == 1, "an empty cache still grabs once"

    await asyncio.sleep(0.06)
    cam.star_x = 20
    assert await g.guide_frame() == first
    assert cam.started == 1, "the imaging sensor must not be taken on age"


async def test_a_dedicated_guide_camera_is_still_re_exposed_on_age(monkeypatch):
    """The guard above must key on the SHARED sensor, not switch the fix off.
    A rig with its own guide camera keeps getting fresh pictures."""
    monkeypatch.setattr(nativemod, "_IDLE_PREVIEW_TTL_S", 0.05)
    cam = _FakeGuideCam()
    g = _guider(cam)
    assert g.shares_the_imaging_sensor is False, "dedicated is the default"

    await g.guide_frame()
    await asyncio.sleep(0.06)
    await g.guide_frame()
    assert cam.started == 2


def test_the_ttl_sits_below_the_panel_poll():
    """GuideFramePreview.tsx re-requests every 2500 ms. A TTL at or above that
    hands every poll a "fresh" cache and freezes the panel again — with a number
    holding it shut this time instead of a boolean."""
    assert 0 < nativemod._IDLE_PREVIEW_TTL_S < 2.5


async def test_an_exposure_the_guider_took_itself_counts_as_a_fresh_cache(
        monkeypatch):
    """``_last_frame`` and ``_last_frame_at`` are ONE fact and have to be
    written together. ``_expose`` is the other writer (the calibration walk, the
    Guiding Assistant, every loop frame); leaving the clock behind there would
    make a frame taken half a second ago read as stale and put a second
    exposure on a sensor that had just delivered one."""
    monkeypatch.setattr(nativemod, "_IDLE_PREVIEW_TTL_S", 5.0)
    cam = _FakeGuideCam()
    g = _guider(cam)

    await g._expose()
    assert cam.started == 1
    assert await g.guide_frame()
    assert cam.started == 1, "the frame the guider just took is not stale"


async def test_the_running_guide_loop_still_owns_the_sensor(monkeypatch):
    """The age test must not reach past the ``not loop_running`` guard. While
    ``_loop_task`` is exposing, its last frame IS the truth and a second
    ``expose()`` on the same sensor would fight it."""
    monkeypatch.setattr(nativemod, "_IDLE_PREVIEW_TTL_S", 0.0)
    cam = _FakeGuideCam()
    g = _guider(cam)
    g._last_frame = _star_frame()
    g._last_frame_at = 0.0                       # as stale as a cache gets
    running = asyncio.Event()
    g._loop_task = asyncio.create_task(running.wait())
    try:
        png = await g.guide_frame()
        assert png and png[:8] == PNG_MAGIC
        assert cam.started == 0
    finally:
        running.set()
        await g._loop_task


# ------------------------------------------------- one sensor, many pollers

async def test_two_overlapping_polls_share_one_exposure(monkeypatch):
    """Now that the idle branch can fire on age it fires on nearly every poll,
    and the panel starts a new request every 2.5 s whether or not the last one
    landed. Two ``expose()`` calls racing one buffer is torn frames, which the
    panel renders as the same uninformative "unavailable". The hub's
    single-flight guards the guide-CAMERA branch and never reaches this one."""
    monkeypatch.setattr(nativemod, "_IDLE_PREVIEW_TTL_S", 0.0)
    cam = _FakeGuideCam()
    cam.gate = asyncio.Event()
    g = _guider(cam)

    first = asyncio.create_task(g.guide_frame())
    await asyncio.sleep(0.02)
    second = asyncio.create_task(g.guide_frame())
    await asyncio.sleep(0.02)
    assert cam.started == 1, "the second poll must JOIN the exposure in flight"

    cam.gate.set()
    a, b = await asyncio.gather(first, second)
    assert a and a == b
    assert cam.started == 1


async def test_a_poller_that_walks_away_does_not_abort_the_exposure(monkeypatch):
    """The request task dies when the browser drops the <img> load. That has to
    cancel the WAIT, not the exposure — killing a guide camera mid-readout to
    serve a panel nobody is watching is how it stays wedged for the run after."""
    monkeypatch.setattr(nativemod, "_IDLE_PREVIEW_TTL_S", 0.0)
    cam = _FakeGuideCam()
    cam.gate = asyncio.Event()
    g = _guider(cam)

    first = asyncio.create_task(g.guide_frame())
    await asyncio.sleep(0.02)
    first.cancel()
    second = asyncio.create_task(g.guide_frame())
    await asyncio.sleep(0.02)
    cam.gate.set()

    png = await second
    assert png and png[:8] == PNG_MAGIC
    assert cam.started == 1, "the surviving poll rode the exposure already running"
    with contextlib.suppress(asyncio.CancelledError):
        await first


# ----------------------------------------------------- the never-raises floor

async def test_a_failed_re_exposure_keeps_serving_the_frame_it_already_has(
        monkeypatch):
    """A camera that refuses one poll must not blank a panel that had a picture
    a moment ago. Old-but-real beats nothing, and the route must not start
    404-ing because a single USB read failed."""
    monkeypatch.setattr(nativemod, "_IDLE_PREVIEW_TTL_S", 0.05)
    cam = _FakeGuideCam()
    g = _guider(cam)
    first = await g.guide_frame()
    assert first

    await asyncio.sleep(0.06)
    cam.fail = "USB read failed"
    assert await g.guide_frame() == first


async def test_a_camera_that_never_answers_returns_none_rather_than_raising():
    """With nothing cached there is nothing to fall back to, and the contract is
    None (the route answers 404) — never an exception out of a coroutine whose
    docstring promises it never raises, which the route would turn into a 500."""
    cam = _FakeGuideCam()
    cam.fail = "camera not responding"
    g = _guider(cam)
    assert await g.guide_frame() is None
