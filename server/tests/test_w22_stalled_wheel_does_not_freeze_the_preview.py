# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A stalled filter wheel does not freeze the post-exposure preview (WP-200, #999).

After every exposure the hub asks the wheel which slot it is on, twice over:
what is the filter called (``Hub._active_filter_name``, the FITS FILTER card)
and is this slot flagged blackout (``Hub._opaque_slot_in_beam``, the dark
judgement and the cloud verdict). Both go through ``Hub._wheel_slot``, which
awaited ``fw.get_position()`` with no bound. A native wheel whose SDK read
stalls in USB never trips a transport timeout, so the exposure was taken, the
camera lane released, and the caller never returned: the centring loop, the
run and the live view stopped on a read that only feeds a header card and a
judgement. The same read follows the exposure in every centring solve, rotate,
rotator sync and guide offset (``Hub._publish_preview`` reads it itself, with
no slot passed down), in every capture (``Hub.capture`` reads it once and
passes it on), and in the plan preflight's wheel check.

THE FIX. ``Hub._wheel_slot`` reads through ``Hub._status_read`` (#814), bounded
by ``STATUS_DEVICE_READ_TIMEOUT_S``: the same bound as the status poll's own
read of the same wheel's position, and not the 90 s of a wheel MOVE
(``SOLVE_WHEEL_MOVE_TIMEOUT_S``), because a read that feeds a label must not
cost a live-view frame a minute and a half. A read that does not return
answers None, which is what a read that raised has always answered: the slot
is unknown, the FILTER card is left off, no blackout slot is named, and the
frame goes on to be saved and published. ``_status_read`` also keeps ONE read
in flight per wheel, so a stalled read is never stacked under a new one (a
fresh worker thread per frame would walk the default executor to exhaustion),
and says the stall once.

Each case stalls the wheel's position read AFTER the exposure (a coroutine that
waits on an event the case releases at the end), with the bound shortened and
the move bound left at its real 90 s (the rotate case shortens it, and says
why), so a read that waited on the wrong bound fails on the case's own
wall-clock limit.

Named mutants, each run from a byte backup of ``hub.py`` and restored with a
byte copy (md5 compared). The first failing assertion is quoted.

* "unbounded" (``_wheel_slot`` made to await ``fw.get_position()`` directly,
  the unfixed read) -> ``test_the_wheel_slot_read_gives_up_and_answers_unknown``:
  Failed, "the wheel-slot read waited on a wheel whose position never returned"
  (and every capture, solve, rotate, preview and preflight case the same way).
* "the move bound" (the read bounded by ``SOLVE_WHEEL_MOVE_TIMEOUT_S`` instead
  of ``STATUS_DEVICE_READ_TIMEOUT_S``) -> the same cases: Failed, on the case's
  own wall-clock limit, because the shortened bound is not the one in force.
  The rotate case shortens the move bound itself (its second frame's borrow
  is a read of its own) and so passes this mutant.
* "reads stack" (a fresh ``asyncio.wait_for(fw.get_position(), ...)`` on every
  call instead of ``_status_read``) -> ``test_a_read_still_inside_the_driver_
  is_not_stacked_under_a_new_one``: AssertionError, "3 wheel position reads
  were started under one that had not returned"; ``test_the_status_poll_and_
  the_preview_share_one_read`` and ``test_a_stall_is_said_once`` fail with it.
* "a stall is read as a slot" (``_wheel_slot`` answering ``0`` where it
  answers None) -> ``test_the_wheel_slot_read_gives_up_and_answers_unknown``:
  AssertionError, ``assert 0 is None``, and ``test_a_stalled_capture_is_saved_
  without_a_filter_label``: the FITS file carries ``FILTER = 'L'``.
* "a cancel is eaten" (``except BaseException`` in ``_wheel_slot``) ->
  ``test_a_stop_still_cancels_the_wheel_read``: Failed, "DID NOT RAISE
  CancelledError".

The controls pass on the unfixed tree as well, which is what makes them
controls: a wheel that answers is named (the slot, the name, the opaque
judgement, the FILTER card), and a Stop that lands while the read waits still
cancels it.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from astropy.io import fits

import astrodeck.events as events_mod
import astrodeck.hub as hub_module
from _simhub import sim_hub  # noqa: F401 (fixture import)

BOUND_S = 0.3
#: A capture or a solve on the simulator finishes in a second or two; a hung
#: one never does. Well under the 90 s move bound, so a stall that waited on
#: the wrong bound fails here.
LIMIT_S = 12.0


@pytest.fixture
def short_bound(monkeypatch):
    """The status bound shortened so a stall does not wait out the real one.
    Read at call time. ``SOLVE_WHEEL_MOVE_TIMEOUT_S`` is left alone on
    purpose. ``raising=False``: on code without the constant a case fails on
    what it grades, not on a missing attribute."""
    monkeypatch.setattr(hub_module, "STATUS_DEVICE_READ_TIMEOUT_S", BOUND_S,
                        raising=False)


@pytest.fixture(autouse=True)
async def quiet_status_poller(sim_hub):
    """Stop the hub's own two-second status poller. It reads the same wheel
    through the same ``_status_read``, and a poll that lands inside a case
    would add a read the case did not make (or join the one it did). The cases
    that mean to involve a poll call ``poll_status`` themselves."""
    task = sim_hub._status_task
    sim_hub._status_task = None
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    yield


def _record_logs(monkeypatch) -> list[tuple[str, str, str]]:
    lines: list[tuple[str, str, str]] = []
    real = events_mod.bus.log

    def record(level, message, source="hub", **kw):
        lines.append((level, message, source))
        return real(level, message, source, **kw)
    monkeypatch.setattr(events_mod.bus, "log", record)
    return lines


class _Wheel:
    """The sim wheel with its ``get_position`` scripted. The Nth call
    (1-based) never returns while ``stalled(N, exposed)`` is true, until
    ``release`` is set; ``exposed`` is True once the camera has finished an
    exposure, so a case can stall only the reads that FOLLOW the shutter and
    leave the borrow's and the label's reads before it healthy. Every call is
    recorded with the time it began."""

    def __init__(self, hub, monkeypatch, *, stalled=lambda n, exposed: exposed):
        self.fw = hub.devices["filterwheel"]
        self.stalled = stalled
        self.release = asyncio.Event()
        self.exposed = False
        self.reads: list[int] = []
        self.began: list[float] = []
        self.hung: list[int] = []           # the reads that were made to stall
        real_get = self.fw.get_position

        async def get_position():
            self.reads.append(len(self.reads) + 1)
            self.began.append(time.monotonic())
            if self.stalled(len(self.reads), self.exposed):
                self.hung.append(len(self.reads))
                await self.release.wait()          # a USB stall inside the SDK
            return await real_get()

        monkeypatch.setattr(self.fw, "get_position", get_position)

        cam = hub.devices["camera"]
        real_expose = cam.expose

        async def expose(*args, **kwargs):
            frame = await real_expose(*args, **kwargs)
            self.exposed = True
            return frame

        monkeypatch.setattr(cam, "expose", expose)


async def _within(coro, what: str):
    try:
        return await asyncio.wait_for(coro, LIMIT_S)
    except asyncio.TimeoutError:
        pytest.fail(f"{what} waited on a wheel whose position never returned")


# --------------------------------------------------------- the wheel-slot read


async def test_the_wheel_slot_read_gives_up_and_answers_unknown(
        sim_hub, monkeypatch, short_bound):
    """The wheel's position read never returns. ``_wheel_slot`` gives up at
    the bound and answers None, "the wheel could not be read", which every
    caller treats as an unknown slot. It does not answer a slot nobody read."""
    wheel = _Wheel(sim_hub, monkeypatch, stalled=lambda n, exposed: True)
    began = time.monotonic()
    try:
        got = await _within(sim_hub._wheel_slot(), "the wheel-slot read")
        waited = time.monotonic() - began
        assert got is None, got
        assert wheel.reads == [1], wheel.reads
        assert BOUND_S * 0.5 < waited < BOUND_S * 6, (
            f"the read gave up after {waited:.2f} s; the bound is {BOUND_S} s")
    finally:
        wheel.release.set()


async def test_a_wheel_that_answers_is_named(sim_hub, monkeypatch, short_bound):
    """CONTROL. The premise the stall cases depend on: a wheel that answers is
    read, and the slot it reports is the slot the name and the opaque
    judgement are made from."""
    _Wheel(sim_hub, monkeypatch, stalled=lambda n, exposed: False)
    fw = sim_hub.devices["filterwheel"]
    sim_hub.sim_rig.filter_slot = 4

    assert await sim_hub._wheel_slot() == 4
    assert await sim_hub._active_filter_name() == fw.filter_names[4]
    assert await sim_hub._opaque_slot_in_beam() is None
    sim_hub.sim_rig.filter_slot = 7
    assert await sim_hub._opaque_slot_in_beam() == 7


async def test_a_stalled_wheel_names_no_filter_and_no_blackout_slot(
        sim_hub, monkeypatch, short_bound):
    """The two questions the preview asks of the wheel, asked by their own
    callers with no slot passed down (the plan preflight, and
    ``_publish_preview`` on a solve frame): each gives up at the bound and
    answers "cannot say" (an empty name, no blackout slot)."""
    wheel = _Wheel(sim_hub, monkeypatch, stalled=lambda n, exposed: True)
    try:
        assert await _within(sim_hub._active_filter_name(),
                             "the filter-name read") == ""
        assert await _within(sim_hub._opaque_slot_in_beam(),
                             "the opaque-slot read") is None
        assert len(wheel.reads) >= 1
    finally:
        wheel.release.set()


async def test_a_read_still_inside_the_driver_is_not_stacked_under_a_new_one(
        sim_hub, monkeypatch, short_bound):
    """A read that has not returned keeps its worker thread. The next frame's
    read joins it rather than starting another under it, so a wheel that stays
    dead for a night's frames costs one thread and not one a frame."""
    wheel = _Wheel(sim_hub, monkeypatch, stalled=lambda n, exposed: True)
    try:
        for _ in range(3):
            assert await _within(sim_hub._wheel_slot(),
                                 "the wheel-slot read") is None
        assert len(wheel.reads) == 1, (
            f"{len(wheel.reads)} wheel position reads were started under one "
            f"that had not returned")
    finally:
        wheel.release.set()


async def test_the_status_poll_and_the_preview_share_one_read(
        sim_hub, monkeypatch, short_bound):
    """The status poll reads this wheel's position through the same helper
    and the same key, so a read that one of them has left in the driver is the
    read the other waits on, and a stalled wheel costs the rig one thread
    whoever asks."""
    wheel = _Wheel(sim_hub, monkeypatch, stalled=lambda n, exposed: True)
    try:
        status = await _within(sim_hub.poll_status(), "the status poll")
        assert "filterwheel" not in status, "premise: the poll went without it"
        assert await _within(sim_hub._wheel_slot(),
                             "the wheel-slot read") is None
        assert wheel.reads == [1], wheel.reads
    finally:
        wheel.release.set()


async def test_a_stall_is_said_once(sim_hub, monkeypatch, short_bound):
    """The warning names the wheel's position read, once for a stall that
    lasts several frames, and not as a bare timeout."""
    logs = _record_logs(monkeypatch)
    wheel = _Wheel(sim_hub, monkeypatch, stalled=lambda n, exposed: True)
    try:
        for _ in range(3):
            await _within(sim_hub._wheel_slot(), "the wheel-slot read")
        said = [ln for ln in logs if "position read" in ln[1]]
        assert len(said) == 1 and said[0][0] == "warning", logs
        assert "filterwheel" in said[0][1], said[0][1]
        assert f"{BOUND_S:g} s" in said[0][1], said[0][1]
        assert "()" not in said[0][1], said[0][1]
    finally:
        wheel.release.set()


async def test_a_wheel_that_recovers_is_read_again(
        sim_hub, monkeypatch, short_bound):
    """The stall belongs to the read that stalled. When the driver finally
    answers, the next frame asks again and gets the slot; a wheel is not given
    up on for the rest of the session."""
    wheel = _Wheel(sim_hub, monkeypatch, stalled=lambda n, exposed: n == 1)
    sim_hub.sim_rig.filter_slot = 5
    try:
        assert await _within(sim_hub._wheel_slot(),
                             "the wheel-slot read") is None
        wheel.release.set()
        await asyncio.sleep(0.05)                  # the held read finishes

        assert await _within(sim_hub._wheel_slot(),
                             "the wheel-slot read") == 5
        assert wheel.reads == [1, 2], wheel.reads
    finally:
        wheel.release.set()


async def test_a_stop_still_cancels_the_wheel_read(
        sim_hub, monkeypatch, short_bound):
    """A cancel that lands while the read waits is the caller's Stop and
    reaches it. The read's own "never raises" is for the device's errors, not
    for this."""
    wheel = _Wheel(sim_hub, monkeypatch, stalled=lambda n, exposed: True)
    # A bound long enough that the cancel lands inside the wait.
    monkeypatch.setattr(hub_module, "STATUS_DEVICE_READ_TIMEOUT_S", 30.0)
    task = asyncio.ensure_future(sim_hub._wheel_slot())
    try:
        await asyncio.sleep(0.1)
        assert not task.done(), "premise: the read is waiting on the wheel"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5.0)
    finally:
        wheel.release.set()


# ------------------------------------------------------ through real callers


@pytest.mark.parametrize("save", [False, True])
async def test_a_capture_returns_when_the_wheel_stalls_after_the_exposure(
        sim_hub, monkeypatch, short_bound, save):
    """The frame is exposed, the wheel is asked which slot it is on, and the
    read never returns. The capture comes back after about one bound with the
    frame published (the preview event's info), and the wheel was asked the
    once the exposure's two questions share."""
    wheel = _Wheel(sim_hub, monkeypatch)
    try:
        info = await _within(
            sim_hub.capture(0.05, 100, 30, 1, save=save, target=""),
            "the capture")
        returned = time.monotonic()
        assert wheel.hung, "premise: the capture's read of the wheel stalled"
        assert returned - wheel.began[0] >= BOUND_S * 0.5, (
            "the capture did not wait on the wheel at all")
        assert isinstance(info, dict) and info.get("id"), (
            f"the frame was not published: {info!r}")
        assert wheel.reads == [1], (
            f"the exposure's FILTER name and opaque-slot judgement are one "
            f"question about one instant: {wheel.reads}")
        if save:
            assert info["filter"] == "", info
    finally:
        wheel.release.set()


async def test_a_stalled_capture_is_saved_without_a_filter_label(
        sim_hub, monkeypatch, short_bound):
    """The file is written, and its header does not name a filter nobody read.
    An absent card is the honest answer for a slot that is unknown; a name
    taken from a default would be the wrong-filter header this hub has already
    shipped a night of."""
    wheel = _Wheel(sim_hub, monkeypatch)
    try:
        await _within(
            sim_hub.capture(0.05, 100, 30, 1, save=True, target=""),
            "the capture")
        saved = Path(sim_hub.last_frame.saved_path)
        assert saved.exists(), saved
        with fits.open(saved) as hdul:
            header = hdul[0].header
        assert "FILTER" not in header, header.get("FILTER")
        assert "BEAMOK" not in header
    finally:
        wheel.release.set()


async def test_a_capture_through_a_healthy_wheel_still_names_its_filter(
        sim_hub, monkeypatch, short_bound):
    """CONTROL. The label is there when the wheel answers, so its absence in
    the case above is the stall's doing and not the fixture's."""
    _Wheel(sim_hub, monkeypatch, stalled=lambda n, exposed: False)
    sim_hub.sim_rig.filter_slot = 2
    fw = sim_hub.devices["filterwheel"]

    info = await sim_hub.capture(0.05, 100, 30, 1, save=True, target="")

    assert info["filter"] == fw.filter_names[2], info
    with fits.open(Path(sim_hub.last_frame.saved_path)) as hdul:
        assert hdul[0].header["FILTER"] == fw.filter_names[2]


async def test_a_solve_returns_when_the_wheel_stalls_after_the_exposure(
        sim_hub, monkeypatch, short_bound):
    """The centring solve's frame, through the real caller. Its borrow and
    label reads (before the shutter) answer; the preview's read after the
    exposure never returns. ``_publish_preview`` reads the wheel itself on this
    path, with no slot passed down, and the solve used to sit there for ever
    with the camera lane released."""
    wheel = _Wheel(sim_hub, monkeypatch)
    try:
        await _within(sim_hub.solve_and_sync(exposure_s=0.05), "the solve")
        assert wheel.exposed, "premise: the solve reached its exposure"
        assert wheel.hung, "premise: the preview's read of the wheel stalled"
    finally:
        wheel.release.set()


async def test_a_rotate_returns_when_the_wheel_stalls_after_the_exposure(
        sim_hub, monkeypatch, short_bound):
    """The same read after each of the rotate's solve frames. The wheel stays
    dead after the first frame, so the second frame's BORROW read (already
    bounded, #815) stalls too: its bound is shortened with the status one, so
    the case grades the preview's read and not a 90 s wait before the shutter."""
    monkeypatch.setattr(hub_module, "SOLVE_WHEEL_MOVE_TIMEOUT_S", BOUND_S,
                        raising=False)
    sim_hub.sim_rig.rotator_pa_offset_deg = 20.0
    sim_hub.sim_rig.rotator_mech_deg = 10.0
    wheel = _Wheel(sim_hub, monkeypatch)
    try:
        result = await _within(sim_hub.rotate_to_pa(40.0), "the rotate")
        assert wheel.exposed, "premise: the rotate reached an exposure"
        assert wheel.hung, "premise: a read of the wheel after a frame stalled"
        assert result["rotated"] is True, result
    finally:
        wheel.release.set()


async def test_the_preview_of_a_frame_is_published_without_the_wheel(
        sim_hub, monkeypatch, short_bound):
    """``_publish_preview`` on its own, the default sentinel meaning "read the
    wheel yourself": it comes back with the frame's info, and the cloud
    verdict, which a known blackout slot would have withheld, is there because
    an unknown slot withholds nothing."""
    wheel = _Wheel(sim_hub, monkeypatch, stalled=lambda n, exposed: False)
    await sim_hub.capture(0.05, 100, 30, 1, save=False, target="")
    frame = sim_hub.last_frame
    wheel.stalled = lambda n, exposed: True
    try:
        info = await _within(sim_hub._publish_preview(frame),
                             "the preview")
        assert isinstance(info, dict) and info.get("id"), info
        assert "cloud" in info, sorted(info)
    finally:
        wheel.release.set()
