# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP-103 (#16, job 2c): the 2 s status poll probes the GUIDE camera too.

WHAT WAS MISSING. Wave 14's WP-89 made an idle removed camera visible: the ZWO
and Player One adapters raise ``CameraGone`` for the SDK's own "closed/removed"
codes, and ``NativeCamera.get_temperature`` marks the drop and says so once. But
the only idle read that reaches that path is the sensor temperature, and the
status poll made it for the IMAGING camera alone. The guide camera is the device
that was actually stranded on 2026-09-12, and unplugged while nothing was
exposing it kept reading connected (``status.guide_camera.connected`` True, the
Equipment screen green) until a human noticed. So the poll now asks the guide
camera the same question, and the side effect IS the measurement: the answer is
discarded.

THE PROBE IS BOUNDED TWICE. It waits at most five seconds, so a USB stall on the
guide camera cannot hold up the whole status frame (the imaging camera's read has
no such bound and is left as it is). And it is one probe in flight at a time: a
call that is still blocked inside the SDK when the wait ends keeps its worker
thread, and a fresh probe every two seconds on top of it would walk the default
executor to exhaustion, starving every other ``asyncio.to_thread`` on the server
(exposures, solves, the guider) over a camera that is only being asked how warm
it is.

Named mutants, each run from a byte backup inside the worktree and restored
byte-identically (sha256 compared, and the mutant text grepped out of the
restored file); every one turned at least one case red. The first failing
assertion is quoted, with the case that raised it. All are in ``hub.py``
``poll_status``.

* h1_probe_deleted -- the ``if gcam is not None and getattr(gcam,
  "connected", False):`` guard made ``if False:``. Five cases fail. First:
  ``test_an_idle_removed_guide_camera_reads_disconnected_after_one_poll``,
  ``AssertionError: an idle guide camera that reports itself removed must read
  disconnected after one poll``.
* h2_probe_ignores_connected -- the guard made ``if gcam is not None:``.
  ``test_ten_polls_of_one_removed_guide_camera_write_one_warning``,
  ``AssertionError: a camera that read disconnected must not be asked again``
  (and ``test_a_guide_camera_that_reads_disconnected_is_not_probed``).
* h3_probe_unbounded -- ``await asyncio.wait({probe}, timeout=5.0)`` made
  ``await probe``. ``test_a_hung_guide_camera_does_not_hold_up_the_status_frame``
  and ``test_a_probe_still_blocked_is_not_stacked_under_a_new_one`` both raise
  ``TimeoutError`` out of the case's own 3 s ``asyncio.wait_for`` on the poll.
* h4_probes_stack -- ``if probe is None or probe.done():`` made ``if True:``.
  ``test_a_probe_still_blocked_is_not_stacked_under_a_new_one``,
  ``AssertionError: 4 probes were started under one that had not returned``.
* h5_bound_widened -- ``timeout=5.0`` made ``timeout=50.0``.
  ``test_a_hung_guide_camera_does_not_hold_up_the_status_frame``,
  ``AssertionError: [50.0]`` (the recorded bound).
* h6_exception_not_retrieved -- the done-callback ``lambda t: t.cancelled() or
  t.exception()`` made ``lambda t: None``.
  ``test_a_probe_that_raises_leaves_no_unretrieved_exception``,
  ``AssertionError: 1 guide-camera probe(s) were dropped with an exception
  nobody retrieved``. (Observed first as a SURVIVOR: the case polled once, and
  the hub holds the latest probe, so nothing was ever dropped to report. It now
  polls twice, because the second poll is what drops the first.)
* h7_guide_step_reached_later (#813) -- two ``await asyncio.sleep(0)`` before
  ``gcam = self.devices.get("guide_camera")``, i.e. the guide-probe step is two
  trips round the event loop further into ``poll_status`` than it was. With the
  hub's status loop left running, the healthy-camera control failed four runs
  of four, ``assert 4 == 3``: the loop's first poll reached the step after the
  case had registered its camera, and read it once itself. It is green under
  the same mutant now that the loop is stopped
  (``_no_background_status_poll``).
"""
from __future__ import annotations

import asyncio
import gc
import threading
import time

import numpy as np
import pytest

import astrodeck.events as events_mod
import astrodeck.hub as hub_module
from _simhub import sim_hub  # noqa: F401  (fixture import)
from astrodeck.devices.cameras.adapter import (CameraAdapter, CameraCapabilities,
                                               CameraGone)
from astrodeck.devices.cameras.engine import NativeCamera


class _Adapter(CameraAdapter):
    """A native adapter whose temperature read can report the camera gone, fail
    some other way, or block until the test lets it go."""

    def __init__(self, *, gone: bool = False, raises: Exception | None = None,
                 block: threading.Event | None = None):
        self.gone = gone
        self.raises = raises
        self.block = block
        self.temperature_reads = 0

    def capabilities(self) -> CameraCapabilities:
        return CameraCapabilities(
            sensor_width=4, sensor_height=2, pixel_size_um=3.76, bit_depth=16,
            bayer_pattern=None, gain_range=(0, 600), offset_range=(0, 255),
            bin_modes=(1,), roi_supported=True, has_cooler=False,
            has_dew_heater=False, max_adu=65535)

    def open(self, index: int) -> None:
        pass

    def close(self) -> None:
        pass

    def start_exposure(self, *, seconds, gain, offset, roi, light) -> None:
        pass

    def image_ready(self) -> bool:
        return True

    def read_frame(self) -> bytes:
        return np.zeros(4 * 2, dtype="<u2").tobytes()

    def abort(self) -> None:
        pass

    def get_temperature(self):
        self.temperature_reads += 1
        if self.block is not None:
            self.block.wait(30.0)
        if self.raises is not None:
            raise self.raises
        if self.gone:
            raise CameraGone("ASI SDK ASIGetControlValue failed (CAMERA_REMOVED, "
                             "code 5)")
        return 12.5


def _record_logs(monkeypatch) -> list[tuple[str, str, str]]:
    lines: list[tuple[str, str, str]] = []

    def record(level, message, source="hub", **_kw):
        lines.append((level, message, source))
    monkeypatch.setattr(events_mod.bus, "log", record)
    return lines


@pytest.fixture(autouse=True)
async def _no_background_status_poll(sim_hub):
    """Stop the hub's own 2 s status loop before the case starts (#813).

    ``Hub.connect_sim`` starts ``_status_loop``, and its first poll runs while
    the case is still registering its guide camera. Whether that poll reaches
    its guide-probe step before or after the registration depends on how many
    trips round the event loop ``poll_status`` makes on the way, so a case that
    grades a read COUNT was graded against a task it did not start: one read
    too many when the poll lands after the registration. Every case here calls
    ``poll_status`` itself and counts what THOSE calls do, so the loop goes.

    So does a probe it may have left in flight on the sim's own guide camera.
    The hub keeps ONE probe, and a poll that finds it unfinished waits on it
    instead of asking the camera under test.
    """
    poll = sim_hub._status_task
    if poll is not None and not poll.done():
        poll.cancel()
        await asyncio.gather(poll, return_exceptions=True)
    assert poll is None or poll.done(), "premise: the hub's status loop is off"
    stale = getattr(sim_hub, "_guide_probe", None)
    if stale is not None:
        await asyncio.wait({stale}, timeout=5.0)
        assert stale.done(), "premise: no probe is left in flight"
    yield


async def _with_guide_camera(hub, adapter) -> NativeCamera:
    cam = NativeCamera(adapter, name="Guide ASI")
    await cam.connect()
    hub.devices["guide_camera"] = cam
    assert cam.connected is True, "precondition"
    return cam


async def test_an_idle_removed_guide_camera_reads_disconnected_after_one_poll(
        sim_hub):
    """m1 and m5. Nothing is exposing, nothing is guiding: the ONLY thing that
    can notice the unplug is the status poll, and the same frame that notices
    must already say so (so the probe runs before the descriptor is built)."""
    adapter = _Adapter(gone=True)
    cam = await _with_guide_camera(sim_hub, adapter)

    status = await sim_hub.poll_status()

    assert cam.connected is False, (
        "an idle guide camera that reports itself removed must read "
        "disconnected after one poll")
    assert status["guide_camera"]["connected"] is False, (
        "and the frame that noticed must say so, not the next one")


async def test_ten_polls_of_one_removed_guide_camera_write_one_warning(
        sim_hub, monkeypatch):
    """A dead camera is polled every tick. The line is written on the
    transition (WP-89's ``_mark_dropped``), and the probe stops asking a camera
    that has already said no, so there is nothing to repeat."""
    logs = _record_logs(monkeypatch)
    adapter = _Adapter(gone=True)
    await _with_guide_camera(sim_hub, adapter)

    for _ in range(10):
        await sim_hub.poll_status()

    drops = [ln for ln in logs if "no longer answering" in ln[1]]
    assert len(drops) == 1, drops
    assert drops[0][0] == "warning"
    assert "Guide ASI" in drops[0][1]
    assert adapter.temperature_reads == 1, (
        "a camera that read disconnected must not be asked again")


async def test_a_guide_camera_that_reads_disconnected_is_not_probed(sim_hub):
    """m2. ``connected`` False is already the answer; asking a closed handle
    only to be told so again is a read of nothing every two seconds."""
    adapter = _Adapter()
    cam = await _with_guide_camera(sim_hub, adapter)
    await cam.disconnect()
    reads = adapter.temperature_reads

    await sim_hub.poll_status()

    assert adapter.temperature_reads == reads


async def test_a_healthy_guide_camera_is_asked_once_a_poll_and_left_connected(
        sim_hub, monkeypatch):
    """The control: a camera that answers is probed (so the next unplug is
    noticed) and nothing about the frame or the log changes."""
    logs = _record_logs(monkeypatch)
    adapter = _Adapter()
    cam = await _with_guide_camera(sim_hub, adapter)

    for _ in range(3):
        status = await sim_hub.poll_status()

    assert adapter.temperature_reads == 3
    assert cam.connected is True
    assert status["guide_camera"]["connected"] is True
    assert [ln for ln in logs if ln[0] in ("warning", "error")] == []


async def test_a_probe_that_fails_some_other_way_does_not_cost_the_status_frame(
        sim_hub):
    """The probe's answer is discarded, whatever it is: a camera that raises
    something that is not ``CameraGone`` must not take the imaging camera's
    block, or anything else on the frame, with it."""
    cam = await _with_guide_camera(sim_hub, _Adapter(raises=RuntimeError("x")))

    status = await sim_hub.poll_status()

    assert "camera" in status and "mount" in status
    assert cam.connected is True, "an unexplained error is not evidence of a drop"


async def test_a_probe_that_raises_leaves_no_unretrieved_exception(
        sim_hub, monkeypatch):
    """m6. The probe runs as a task whose result nobody awaits, so its exception
    has to be retrieved by the task's own done-callback or the loop writes
    "Task exception was never retrieved" at garbage collection, one per poll."""
    seen: list[dict] = []
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "call_exception_handler", seen.append)
    await _with_guide_camera(sim_hub, _Adapter(raises=RuntimeError("x")))

    # The loop reports an unretrieved exception when the task is garbage
    # collected, and the hub holds the latest probe, so it is the SECOND poll
    # (which replaces a finished probe) that drops the first one.
    await sim_hub.poll_status()
    await sim_hub.poll_status()
    await asyncio.sleep(0)
    gc.collect()

    unretrieved = [c for c in seen if "never retrieved" in str(c.get("message"))]
    assert unretrieved == [], (
        f"{len(unretrieved)} guide-camera probe(s) were dropped with an "
        "exception nobody retrieved")


class _AsyncioProxy:
    """``hub.asyncio`` with the probe's wait recorded and shortened, so a test of
    a five-second bound does not take five seconds. Everything else is the real
    module."""

    def __init__(self, probe_of):
        self.probe_of = probe_of
        self.bounds: list[float | None] = []

    def __getattr__(self, name):
        return getattr(asyncio, name)

    async def wait(self, fs, *, timeout=None, **kw):
        if any(self.probe_of(f) for f in fs):
            self.bounds.append(timeout)
            timeout = 0.2 if timeout is None else min(timeout, 0.2)
        return await asyncio.wait(fs, timeout=timeout, **kw)


def _is_the_probe_of(cam):
    def check(fut) -> bool:
        coro = getattr(fut, "get_coro", lambda: None)()
        frame = getattr(coro, "cr_frame", None)
        return (frame is not None
                and coro.cr_code.co_name == "get_temperature"
                and frame.f_locals.get("self") is cam)
    return check


async def test_a_hung_guide_camera_does_not_hold_up_the_status_frame(
        sim_hub, monkeypatch):
    """m3. The guide camera's read is bounded at five seconds, because the
    probe's answer is worth less than the frame.

    (This once also said the imaging camera's read had no bound. It has one
    since WP-143, #724: ``Hub._imaging_temperature``, bounded by
    ``STATUS_CAMERA_TEMPERATURE_TIMEOUT_S``, graded in
    test_w16_status_temperature_is_bounded.py.)"""
    release = threading.Event()
    try:
        cam = await _with_guide_camera(sim_hub, _Adapter(block=release))
        proxy = _AsyncioProxy(_is_the_probe_of(cam))
        monkeypatch.setattr(hub_module, "asyncio", proxy)

        status = await asyncio.wait_for(sim_hub.poll_status(), 3.0)

        assert "mount" in status
        assert proxy.bounds and all(b is not None and 0 < b <= 5.0
                                    for b in proxy.bounds), proxy.bounds
        assert cam.connected is True, "a slow answer is not a missing camera"
    finally:
        release.set()


async def test_a_probe_still_blocked_is_not_stacked_under_a_new_one(
        sim_hub, monkeypatch):
    """m4. A call still inside the SDK when the wait ended keeps its worker
    thread. A new probe every two seconds on top of it would take a thread per
    poll from the default executor until nothing else on the server could use
    one, so while one is outstanding no second is started."""
    release = threading.Event()
    try:
        adapter = _Adapter(block=release)
        cam = await _with_guide_camera(sim_hub, adapter)
        monkeypatch.setattr(hub_module, "asyncio",
                            _AsyncioProxy(_is_the_probe_of(cam)))

        for _ in range(4):
            await asyncio.wait_for(sim_hub.poll_status(), 3.0)

        assert adapter.temperature_reads == 1, (
            f"{adapter.temperature_reads} probes were started under one that "
            "had not returned")
    finally:
        release.set()
    # ...and once it has returned, a poll asks again. Polled against a
    # wall-clock deadline (#669): the worker thread leaves the SDK on its own
    # schedule.
    deadline = time.monotonic() + 5.0
    while adapter.temperature_reads < 2 and time.monotonic() < deadline:
        await sim_hub.poll_status()
        await asyncio.sleep(0.01)
    assert adapter.temperature_reads == 2
