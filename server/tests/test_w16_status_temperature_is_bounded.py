# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The status poll's read of the imaging camera's temperature is bounded
(WP-143, #724).

WHAT WENT WRONG. ``Hub.poll_status`` awaited ``cam.get_temperature()`` with no
bound. For a native camera that call is ``asyncio.to_thread`` of an SDK read, so
a USB stall inside ``ASIGetControlValue`` or ``POAGetConfig`` held up the WHOLE
status frame (mount, focuser, rotator, guider, everything after it, and the
``/api/status`` caller waiting on it) for as long as the call stayed inside the
driver. WP-103 bounded the new guide-camera probe at five seconds and left this
read alone because it was outside that package's region.

THE FIX. The read runs as ONE task in flight, and the poll waits on it for at
most ``STATUS_CAMERA_TEMPERATURE_TIMEOUT_S``:

* a stall costs the frame that long and no more;
* the camera block is still built, with ``temperature`` null (an unknown
  reading, which every client already renders as "cannot say"), not dropped;
* the camera's other readings in that block (cooler, dew heater, fan) are
  skipped for the tick, because they are reads of the same stalled device and
  would simply join the stall: bounding one read of three would bound nothing;
* a read that is still inside the SDK keeps its worker thread, so no second one
  is started on top of it (a new read every two seconds would walk the default
  executor to exhaustion);
* the stall is said once when it starts, not every two seconds for as long as
  it lasts.

NOT FIXED HERE, and the same class (a status poll awaiting a device call with no
bound): every other device read in ``poll_status`` (mount, focuser, filter
wheel, rotator, dome) and the guider's. Reported in the work package's return.

Named mutants, each run from a byte backup of ``hub.py`` inside this worktree and
restored byte-identically (sha256 compared, the mutant text grepped absent). The
first failing assertion is quoted verbatim, with the case that raised it.

* "unbounded" (the poll awaits the probe instead of waiting on it for the bound:
  ``done, _pending = await asyncio.wait({probe}, timeout=...)`` made ``await
  probe``) ->
  ``test_a_stalled_temperature_read_does_not_hold_up_the_status_frame`` (and
  five more), ``TimeoutError`` out of the case's own 3 s ``asyncio.wait_for`` on
  the poll.
* "the block is dropped on a timeout" (the timed-out branch raises
  ``asyncio.TimeoutError`` into the camera block's ``except``) ->
  ``test_a_timed_out_read_leaves_the_camera_block_with_a_null_temperature``,
  ``AssertionError: a stalled temperature read took the camera block off the
  frame``.
* "probes stack" (``if held is not None and held[0] is cam and not
  held[1].done():`` made ``if False:``) ->
  ``test_a_read_still_inside_the_driver_is_not_stacked_under_a_new_one``,
  ``AssertionError: 4 temperature reads were started under one that had not
  returned``.
* "the other reads join the stall" (``temp, _cam_answered = await
  self._imaging_temperature(cam)`` made ``temp, _ = ...`` followed by
  ``_cam_answered = True``, so the dew heater, fan and cooler reads are made
  from a camera that did not answer) ->
  ``test_a_stalled_temperature_read_does_not_become_a_stalled_cooler_read``,
  ``TimeoutError`` out of the case's own 3 s ``asyncio.wait_for`` on the poll.
* "the exception is not retrieved" (the done-callback ``lambda t: t.cancelled()
  or t.exception()`` made ``lambda t: None``; observed first as a SURVIVOR, see
  the comment in the case: the replaced probe was kept alive by a second
  failure's traceback) ->
  ``test_a_late_failure_leaves_no_unretrieved_exception``,
  ``AssertionError: 1 temperature read(s) were dropped with an exception nobody
  retrieved``.
* "said every poll" (``self._imaging_stalled = True`` made ``pass``) ->
  ``test_a_stall_is_said_once_and_a_recovery_is_quiet``,
  ``AssertionError: a stall that lasted four polls was announced 4 times``.
* "the bound widened" (``STATUS_CAMERA_TEMPERATURE_TIMEOUT_S = 2.0`` made
  ``50.0``) -> ``test_the_bound_is_short``, ``AssertionError: 50.0``.
* "not started eagerly" (``if hasattr(asyncio, "eager_task_factory"):`` made
  ``if False:``) ->
  ``test_an_instant_read_costs_the_poll_no_trip_round_the_event_loop``,
  ``AssertionError: the poll went round the event loop for a read that answered
  at once``; and, observed on the first version of this change, which had no
  eager start,
  ``test_w15_the_status_poll_probes_the_guide_camera.py::test_a_healthy_guide_
  camera_is_asked_once_a_poll_and_left_connected`` failed in every order but
  first with ``assert 4 == 3`` (see the case below for why).

Three more, added by the independent verifier after it found that the author's
mutants never reached them (``_CoolerAdapter`` declares neither a dew heater nor
a fan, so the poll never asked the adapter for them, and the camera identity in
the reuse test was never exercised by a replaced camera):

* "the dew heater read is not skipped" (the first of the three ``if
  _cam_answered else None)`` guards in ``poll_status`` made ``if True else
  None)``) ->
  ``test_a_stalled_temperature_read_does_not_become_a_stalled_dew_or_fan_read``,
  ``Failed: the dew heater or the fan was read from a camera that had just
  failed to answer, and held the status frame``.
* "the fan read is not skipped" (the second of the three guards made ``if True
  else None)``) -> the same case, the same text.
* "the replaced camera waits on the old one's read" (``held[0] is cam`` dropped
  from ``_imaging_temperature``'s reuse test) ->
  ``test_a_camera_that_was_replaced_is_not_made_to_wait_on_the_old_ones_read``,
  ``AssertionError: the replacement camera was never asked: its poll waited on
  the read of the camera it replaced``.
"""
from __future__ import annotations

import asyncio
import gc
import threading
import time

import pytest

import astrodeck.events as events_mod
import astrodeck.hub as hub_module
from _simhub import sim_hub  # noqa: F401  (fixture import)
from astrodeck.devices.cameras.adapter import CameraCapabilities
from astrodeck.devices.cameras.engine import NativeCamera

from test_w15_the_status_poll_probes_the_guide_camera import _Adapter


class _CoolerAdapter(_Adapter):
    """The same adapter with a cooler whose reads stall on the SAME event as the
    temperature read: one USB stall, every read of that camera inside it."""

    def capabilities(self) -> CameraCapabilities:
        caps = super().capabilities()
        return CameraCapabilities(
            sensor_width=caps.sensor_width, sensor_height=caps.sensor_height,
            pixel_size_um=caps.pixel_size_um, bit_depth=caps.bit_depth,
            bayer_pattern=None, gain_range=caps.gain_range,
            offset_range=caps.offset_range, bin_modes=caps.bin_modes,
            roi_supported=True, has_cooler=True, has_dew_heater=False,
            max_adu=caps.max_adu)

    def _stall(self):
        if self.block is not None:
            self.block.wait(30.0)

    def get_cooler_on(self):
        self.cooler_reads = getattr(self, "cooler_reads", 0) + 1
        self._stall()
        return True

    def get_target_temp(self):
        self._stall()
        return -10.0

    def get_cooler_power(self):
        self._stall()
        return 40


async def _with_imaging_camera(hub, adapter) -> NativeCamera:
    cam = NativeCamera(adapter, name="Imaging ASI")
    await cam.connect()
    hub.devices["camera"] = cam
    assert cam.connected is True, "precondition"
    return cam


@pytest.fixture
def short_bound(monkeypatch):
    """The bound shortened so a test of a stall does not wait out the real one.
    The constant is read at call time, so patching the module attribute works."""
    monkeypatch.setattr(hub_module, "STATUS_CAMERA_TEMPERATURE_TIMEOUT_S", 0.25)


def _record_logs(monkeypatch) -> list[tuple[str, str, str]]:
    lines: list[tuple[str, str, str]] = []

    def record(level, message, source="hub", **_kw):
        lines.append((level, message, source))
    monkeypatch.setattr(events_mod.bus, "log", record)
    return lines


def test_the_bound_is_short():
    """One status period (the loop sleeps two seconds between polls) at most: a
    stall may cost the frame no more than the frame is worth."""
    bound = hub_module.STATUS_CAMERA_TEMPERATURE_TIMEOUT_S
    assert 0 < bound <= 2.0, bound


async def test_a_healthy_read_is_published_and_asked_once_a_poll(sim_hub):
    """CONTROL. A camera that answers has its temperature in the frame, one read
    per poll, and nothing about the block changes."""
    adapter = _Adapter()
    await _with_imaging_camera(sim_hub, adapter)

    for _ in range(3):
        status = await sim_hub.poll_status()

    assert status["camera"]["temperature"] == 12.5
    assert adapter.temperature_reads == 3, adapter.temperature_reads


@pytest.mark.skipif(not hasattr(asyncio, "eager_task_factory"),
                    reason="eager task start is Python 3.12+")
async def test_an_instant_read_costs_the_poll_no_trip_round_the_event_loop(
        sim_hub):
    """The read of a camera that answers without suspending (the simulator, any
    backend serving a cached value) is finished before the helper returns: the
    event loop is never handed control. A status poll runs concurrently with
    itself (the background loop and /api/status), and the ORDER in which those
    polls reach their later device reads is part of what the suite grades:
    ``test_w15_the_status_poll_probes_the_guide_camera`` counts a guide
    camera's reads against the polls the test made, which holds only while the
    background poll's first pass is as short as it always was. A first version
    of the bound, one ordinary task and a wait, added three trips round the
    loop to every poll and that count came out four, in every order but first."""
    ran: list[int] = []
    asyncio.get_running_loop().call_soon(ran.append, 1)

    temp, answered = await sim_hub._imaging_temperature(
        sim_hub.devices["camera"])

    assert answered is True and temp is not None, (temp, answered)
    assert ran == [], (
        "the poll went round the event loop for a read that answered at once")
    await asyncio.sleep(0)
    assert ran == [1], "premise: the callback was runnable all along"


async def test_a_stalled_temperature_read_does_not_hold_up_the_status_frame(
        sim_hub, short_bound):
    release = threading.Event()
    try:
        await _with_imaging_camera(sim_hub, _Adapter(block=release))

        status = await asyncio.wait_for(sim_hub.poll_status(), 3.0)

        assert "mount" in status and "focuser" in status
    finally:
        release.set()


async def test_a_timed_out_read_leaves_the_camera_block_with_a_null_temperature(
        sim_hub, short_bound):
    release = threading.Event()
    try:
        await _with_imaging_camera(sim_hub, _Adapter(block=release))

        status = await asyncio.wait_for(sim_hub.poll_status(), 3.0)

        assert "camera" in status, (
            "a stalled temperature read took the camera block off the frame")
        assert "temperature" in status["camera"] and (
            status["camera"]["temperature"] is None), status["camera"]
        assert status["camera"]["width"] == 4, (
            "the rest of the block is still the camera's own")
    finally:
        release.set()


async def test_a_read_still_inside_the_driver_is_not_stacked_under_a_new_one(
        sim_hub, short_bound):
    release = threading.Event()
    adapter = _Adapter(block=release)
    try:
        await _with_imaging_camera(sim_hub, adapter)

        for _ in range(4):
            await asyncio.wait_for(sim_hub.poll_status(), 3.0)

        assert adapter.temperature_reads == 1, (
            f"{adapter.temperature_reads} temperature reads were started under "
            "one that had not returned")
    finally:
        release.set()
    # ...and once it has returned, a poll asks again and publishes the answer.
    # Polled against a wall-clock deadline (#669): the worker thread leaves the
    # SDK on its own schedule.
    deadline = time.monotonic() + 5.0
    status = await sim_hub.poll_status()
    while (status["camera"]["temperature"] is None
           and time.monotonic() < deadline):
        await asyncio.sleep(0.01)
        status = await sim_hub.poll_status()
    assert status["camera"]["temperature"] == 12.5, (
        "the stall stuck: the read that returned was never asked for again")


async def test_a_stalled_temperature_read_does_not_become_a_stalled_cooler_read(
        sim_hub, short_bound):
    """One USB stall holds every read of that camera. The temperature read is
    bounded, and the cooler reads that follow it in the same block must not wait
    out the stall instead: the frame would still hang, one read later."""
    release = threading.Event()
    try:
        await _with_imaging_camera(sim_hub, _CoolerAdapter(block=release))

        status = await asyncio.wait_for(sim_hub.poll_status(), 3.0)

        assert status["camera"]["temperature"] is None
        assert "cooler" not in status["camera"], (
            "the cooler was read from a camera that had just failed to answer")
    finally:
        release.set()


async def test_a_camera_that_answers_still_publishes_its_cooler(sim_hub):
    """CONTROL for the case above: skipping the cooler is for a camera that did
    not answer, and only for that tick."""
    await _with_imaging_camera(sim_hub, _CoolerAdapter())

    status = await sim_hub.poll_status()

    assert status["camera"]["temperature"] == 12.5
    assert status["camera"]["cooler"]["on"] is True, status["camera"]


async def test_a_late_failure_leaves_no_unretrieved_exception(sim_hub,
                                                              short_bound,
                                                              monkeypatch):
    """The read that times out is a task nobody is awaiting any more, so when it
    ENDS in an error (the driver gives up after the poll did) its exception has
    to be retrieved by the task's own done-callback, or the loop writes "Task
    exception was never retrieved" at garbage collection."""
    seen: list[dict] = []
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "call_exception_handler", seen.append)
    release = threading.Event()
    adapter = _Adapter(block=release, raises=RuntimeError("driver gave up"))
    try:
        await _with_imaging_camera(sim_hub, adapter)
        await asyncio.wait_for(sim_hub.poll_status(), 3.0)   # times out
    finally:
        release.set()
    # The worker leaves the SDK and raises into a task nobody awaits.
    # Polled against a wall-clock deadline (#669).
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        held = getattr(sim_hub, "_imaging_probe", None)
        finished = held is not None and held[1].done()
        del held          # a reference here would keep the task from being dropped
        if finished:
            break
        await asyncio.sleep(0.01)
    # A poll that finds the finished probe replaces it, and the replaced task is
    # what gets collected. The driver answers this time: a second failure would
    # keep the first task alive through its own traceback (the old probe is a
    # local of the frame the exception passed through) and hide the very thing
    # being looked for.
    adapter.raises = None
    await sim_hub.poll_status()
    await asyncio.sleep(0)
    gc.collect()

    unretrieved = [c for c in seen if "never retrieved" in str(c.get("message"))]
    assert unretrieved == [], (
        f"{len(unretrieved)} temperature read(s) were dropped with an "
        "exception nobody retrieved")


async def test_a_camera_that_reports_gone_still_reads_disconnected(sim_hub):
    """CONTROL, and the behaviour that was always there: a read that RAISES (the
    idle-unplug notice, WP-89) reaches the caller as before. The bound is for a
    read that does not return, not a way to swallow one that does."""
    cam = await _with_imaging_camera(sim_hub, _Adapter(gone=True))

    await sim_hub.poll_status()

    assert cam.connected is False, (
        "an idle camera that reports itself removed must read disconnected "
        "after one poll")


async def test_a_stall_is_said_once_and_a_recovery_is_quiet(sim_hub,
                                                            short_bound,
                                                            monkeypatch):
    logs = _record_logs(monkeypatch)
    release = threading.Event()
    adapter = _Adapter(block=release)
    try:
        await _with_imaging_camera(sim_hub, adapter)
        for _ in range(4):
            await asyncio.wait_for(sim_hub.poll_status(), 3.0)
    finally:
        release.set()

    said = [ln for ln in logs if "temperature" in ln[1] and ln[0] == "warning"]
    assert len(said) == 1, (
        f"a stall that lasted four polls was announced {len(said)} times")
    assert said[0][2] == "camera", said[0]

    # Recovered: the next answer clears it, so a SECOND stall is said again.
    deadline = time.monotonic() + 5.0
    status = await sim_hub.poll_status()
    while (status["camera"]["temperature"] is None
           and time.monotonic() < deadline):
        await asyncio.sleep(0.01)
        status = await sim_hub.poll_status()
    assert status["camera"]["temperature"] == 12.5
    again = threading.Event()
    adapter.block = again
    try:
        await asyncio.wait_for(sim_hub.poll_status(), 3.0)
    finally:
        again.set()
    said = [ln for ln in logs if "temperature" in ln[1] and ln[0] == "warning"]
    assert len(said) == 2, (
        f"a second stall after a recovery was not announced: {len(said)}")


class _HeaterFanAdapter(_Adapter):
    """The same adapter with a dew heater and a controllable fan whose reads
    stall on the SAME event as the temperature read: one USB stall, every read
    of that camera inside it. (``_CoolerAdapter`` declares neither, so the status
    poll never reaches the adapter for them and cannot tell a guarded read from
    an unguarded one.)"""

    def capabilities(self) -> CameraCapabilities:
        caps = super().capabilities()
        return CameraCapabilities(
            sensor_width=caps.sensor_width, sensor_height=caps.sensor_height,
            pixel_size_um=caps.pixel_size_um, bit_depth=caps.bit_depth,
            bayer_pattern=None, gain_range=caps.gain_range,
            offset_range=caps.offset_range, bin_modes=caps.bin_modes,
            roi_supported=True, has_cooler=False, has_dew_heater=True,
            max_adu=caps.max_adu, has_fan_control=True)

    def get_dew_heater(self):
        if self.block is not None:
            self.block.wait(30.0)
        return 35

    def get_fan_power(self):
        if self.block is not None:
            self.block.wait(30.0)
        return 70


async def test_a_stalled_temperature_read_does_not_become_a_stalled_dew_or_fan_read(
        sim_hub, short_bound):
    """The dew heater and the fan are each read in their own guard after the
    temperature, and each is skipped on a tick where the camera did not answer.
    Skip only the cooler and the frame still hangs, one read later, on whichever
    of the two was left."""
    release = threading.Event()
    try:
        await _with_imaging_camera(sim_hub, _HeaterFanAdapter(block=release))

        try:
            status = await asyncio.wait_for(sim_hub.poll_status(), 3.0)
        except asyncio.TimeoutError:
            pytest.fail("the dew heater or the fan was read from a camera that "
                        "had just failed to answer, and held the status frame")

        assert status["camera"]["temperature"] is None
        assert "dew_heater" not in status["camera"], status["camera"]
        assert "fan_power" not in status["camera"], status["camera"]
    finally:
        release.set()


async def test_a_camera_that_answers_still_publishes_its_dew_heater_and_fan(
        sim_hub):
    """CONTROL for the case above: the two are skipped for a camera that did not
    answer, and only for that tick."""
    await _with_imaging_camera(sim_hub, _HeaterFanAdapter())

    status = await sim_hub.poll_status()

    assert status["camera"]["temperature"] == 12.5
    assert status["camera"]["dew_heater"] == 35, status["camera"]
    assert status["camera"]["fan_power"] == 70, status["camera"]


async def test_a_camera_that_was_replaced_is_not_made_to_wait_on_the_old_ones_read(
        sim_hub, short_bound):
    """A profile re-activation (or a reconnect) puts a NEW camera object in the
    hub while the old one's read may still be inside its driver. The new camera
    must be asked its own temperature, not wait out the old camera's read and
    then publish the old camera's number as its own."""
    release = threading.Event()
    stuck, fresh = _Adapter(block=release), _Adapter()
    try:
        await _with_imaging_camera(sim_hub, stuck)
        await asyncio.wait_for(sim_hub.poll_status(), 3.0)       # times out
        assert stuck.temperature_reads == 1, "premise: the old read is held"

        await _with_imaging_camera(sim_hub, fresh)               # replaced
        status = await asyncio.wait_for(sim_hub.poll_status(), 3.0)

        assert fresh.temperature_reads == 1, (
            "the replacement camera was never asked: its poll waited on the "
            "read of the camera it replaced")
        assert status["camera"]["temperature"] == 12.5
    finally:
        release.set()
