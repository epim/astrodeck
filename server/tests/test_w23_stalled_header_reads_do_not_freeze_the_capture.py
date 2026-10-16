# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A stalled device does not freeze a saved capture after the exposure
(WP-215, #1009; the class of #814, #815, #935 and #999).

After every exposure ``Hub.capture`` builds the frame's header snapshot
(``Hub._capture_snapshot``, and in it ``Hub._frame_meta``). Their docstrings say
every read is guarded so a hung device never blocks the save, and the guards
were ``try/except Exception``: they catch an error and not a read that never
returns. Five reads sat behind them with no bound: the mount's position, the
camera's cooler, the focuser's position and temperature, and the rotator's
mechanical angle. A device whose SDK or HTTP read stalls (USB, a COM host, an
Alpaca server that accepts and never answers) froze the capture after the frame
was taken, with the camera lane already released. The centring loop, the run
and the live view stopped on a header read.

THE FIX. Each read goes through ``Hub._status_read`` (#814), bounded by
``STATUS_DEVICE_READ_TIMEOUT_S``: the same helper, the same keys and the same
bound as the status poll's own read of the same device, so the poll and the
header share ONE read in flight per device and a device that stays dead for a
night's frames costs one worker thread, not one a frame. The existing guards
turn the stall into an omitted card (the field stays None). The focuser's
temperature is asked only after its position answered, so a dead focuser costs
the capture one bound and not two. The stall is said once, by
``_status_read``, with the bound it missed; the guard's own ``say_swallowed``
does not say it a second time.

Each case stalls ONE device's read AFTER the exposure (a coroutine that waits on
an event the case releases at the end), with the bound shortened, the status
poller stopped (it reads the same devices through the same helper and would add
a read the case did not make), and a wall-clock limit well under what a hung
capture takes.

Named mutants, each run from a byte backup of ``hub.py`` and restored with a
byte copy (md5 compared). The first failing assertion is quoted.

* "unbounded focuser position" (``_frame_meta`` awaiting ``foc.get_position()``
  directly, the unfixed read) -> ``test_a_stalled_device_does_not_hold_the_
  capture[focuser-position]``: Failed, "the capture waited on a device whose
  read never returned".
* "unbounded mount position" (``_capture_snapshot`` awaiting
  ``tel.get_position()`` directly) -> the ``mount`` case, the same way.
* "the thermometer first" (``_frame_meta`` asking the focuser temperature
  before the position, inside the same guard) -> ``test_a_dead_focuser_costs_
  the_capture_one_bound_not_two``: AssertionError, one temperature read begun
  under a position read that had not answered.
* "the guard says it again" (``say_swallowed`` without its stalled-read
  return) -> ``test_a_stall_is_said_once``: AssertionError, a second line
  naming the header for the same stall.

The controls pass on the unfixed tree as well, which is what makes them
controls: a healthy rig writes every card the cases take away, and a Stop that
lands while a read waits still cancels it.
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
#: A capture on the simulator finishes in a second or two; a hung one never
#: does. Several bounds of headroom, so a case that stalls four devices is not
#: failed by arithmetic, and far under the 90 s of a wheel move.
LIMIT_S = 12.0

#: (device, the method that stalls, the cards that go missing with it).
STALLS = [
    pytest.param("focuser", "get_position", {"FOCPOS", "FOCTEMP"},
                 id="focuser-position"),
    pytest.param("focuser", "get_temperature", {"FOCTEMP"},
                 id="focuser-temperature"),
    pytest.param("rotator", "get_mechanical_position", {"ROTATANG", "ROTMECH"},
                 id="rotator"),
    pytest.param("telescope", "get_position",
                 {"OBJCTRA", "OBJCTDEC", "MOUNTRA", "MOUNTDEC"}, id="mount"),
    pytest.param("camera", "get_cooler", {"SET-TEMP"}, id="cooler"),
]
EVERY_CARD = {"FOCPOS", "FOCTEMP", "ROTATANG", "ROTMECH", "OBJCTRA", "OBJCTDEC",
              "MOUNTRA", "MOUNTDEC", "SET-TEMP"}


@pytest.fixture(autouse=True)
def short_bound(monkeypatch):
    """The status bound shortened so a stall does not wait out the real one.
    Read at call time. ``raising=False``: on code without the constant a case
    fails on what it grades, not on a missing attribute."""
    monkeypatch.setattr(hub_module, "STATUS_DEVICE_READ_TIMEOUT_S", BOUND_S,
                        raising=False)


@pytest.fixture(autouse=True)
async def quiet_status_poller(sim_hub):
    """Stop the hub's own two-second status poller. It reads these devices
    through the same ``_status_read`` and a poll that lands inside a case would
    add a read the case did not make (or join the one it did)."""
    task = sim_hub._status_task
    sim_hub._status_task = None
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    # A cooler that is ON, so SET-TEMP is a card the healthy rig writes.
    await sim_hub.devices["camera"].set_cooler(True, -10.0)
    yield


def _record_logs(monkeypatch) -> list[tuple[str, str, str]]:
    lines: list[tuple[str, str, str]] = []
    real = events_mod.bus.log

    def record(level, message, source="hub", **kw):
        lines.append((level, message, source))
        return real(level, message, source, **kw)
    monkeypatch.setattr(events_mod.bus, "log", record)
    return lines


class _Stalled:
    """One device method made to never return until ``release`` is set. Every
    call is counted and timed. The header reads only happen once the exposure
    has finished, so the stall is a read that FOLLOWS the shutter (the premise
    of the issue)."""

    def __init__(self, hub, monkeypatch, device: str, method: str):
        self.release = asyncio.Event()
        self.calls = 0
        self.began: list[float] = []
        dev = hub.devices[device]
        real = getattr(dev, method)

        async def stalled(*args, **kwargs):
            self.calls += 1
            self.began.append(time.monotonic())
            await self.release.wait()           # a USB stall inside the SDK
            return await real(*args, **kwargs)

        monkeypatch.setattr(dev, method, stalled)


async def _within(coro, what: str):
    try:
        return await asyncio.wait_for(coro, LIMIT_S)
    except asyncio.TimeoutError:
        pytest.fail(f"{what} waited on a device whose read never returned")


def _header(hub) -> fits.Header:
    saved = Path(hub.last_frame.saved_path)
    assert saved.exists(), saved
    with fits.open(saved) as hdul:
        return hdul[0].header.copy()


async def _save(hub):
    return await _within(
        hub.capture(0.05, 100, 30, 1, save=True, target=""), "the capture")


# ----------------------------------------------------------------- controls


async def test_a_healthy_rig_writes_every_card_the_cases_take_away(sim_hub):
    """CONTROL. The premise the stall cases depend on: when every device
    answers, the file carries each of the cards, so a card missing from the
    cases below is the stall's doing and not the fixture's."""
    await _save(sim_hub)
    header = _header(sim_hub)
    missing = sorted(c for c in EVERY_CARD if c not in header)
    assert not missing, f"the healthy header lacks {missing}"


# --------------------------------------------------------- one device stalls


@pytest.mark.parametrize("device,method,cards", STALLS)
async def test_a_stalled_device_does_not_hold_the_capture(
        sim_hub, monkeypatch, device, method, cards):
    """The device's read never returns. The capture comes back after about one
    bound, the file is written, and the cards that read feeds are not in it
    while every other card still is."""
    stalled = _Stalled(sim_hub, monkeypatch, device, method)
    began = time.monotonic()
    try:
        await _save(sim_hub)
        waited = time.monotonic() - began
        assert stalled.calls >= 1, "premise: the capture read the device"
        assert waited >= BOUND_S * 0.5, (
            f"the capture did not wait on the device at all ({waited:.2f} s)")
        assert waited < BOUND_S * 8, (
            f"the capture took {waited:.2f} s; the bound is {BOUND_S} s")
        header = _header(sim_hub)
        present = sorted(c for c in cards if c in header)
        assert not present, f"a card from a read that never returned: {present}"
        lost = sorted(c for c in EVERY_CARD - cards if c not in header)
        assert not lost, f"cards of healthy devices went with it: {lost}"
    finally:
        stalled.release.set()


@pytest.mark.parametrize("device,method,cards", STALLS)
async def test_a_stalled_device_does_not_hold_an_unsaved_frame_or_its_promote(
        sim_hub, monkeypatch, device, method, cards):
    """The same snapshot is taken for a frame that is not saved, because it
    may be promoted minutes later (D-SES-4). A stall holds neither the capture
    nor the promote, and the promoted file lacks the same cards."""
    stalled = _Stalled(sim_hub, monkeypatch, device, method)
    try:
        info = await _within(
            sim_hub.capture(0.05, 100, 30, 1, save=False, target=""),
            "the capture")
        assert isinstance(info, dict) and info.get("id"), info
        await _within(sim_hub.promote_last_frame(), "the promote")
        header = _header(sim_hub)
        present = sorted(c for c in cards if c in header)
        assert not present, f"a card from a read that never returned: {present}"
    finally:
        stalled.release.set()


async def test_every_header_device_stalled_costs_each_one_bound(
        sim_hub, monkeypatch):
    """The worst night: the mount, the cooler, the focuser and the rotator all
    stall. Each is waited on once, the focuser once and not twice, and the
    frame is still saved, with the cards of none of them."""
    stalls = [_Stalled(sim_hub, monkeypatch, d, m)
              for d, m in (("telescope", "get_position"),
                           ("camera", "get_cooler"),
                           ("focuser", "get_position"),
                           ("focuser", "get_temperature"),
                           ("rotator", "get_mechanical_position"))]
    began = time.monotonic()
    try:
        await _save(sim_hub)
        waited = time.monotonic() - began
        # Four devices, four bounds; five reads would be five.
        assert waited < BOUND_S * 4 + 3.0, (
            f"{waited:.2f} s for four stalled devices at {BOUND_S} s each")
        header = _header(sim_hub)
        assert not (EVERY_CARD & set(header)), sorted(EVERY_CARD & set(header))
        assert stalls[3].calls == 0, (
            "the focuser temperature was asked of a focuser that had not "
            "answered its position")
    finally:
        for s in stalls:
            s.release.set()


async def test_a_dead_focuser_costs_the_capture_one_bound_not_two(
        sim_hub, monkeypatch):
    """The focuser's position read stalls. Its temperature read, in the same
    snapshot, would wait the same bound again on the same dead device, so it is
    not made: the capture costs one bound, not two."""
    position = _Stalled(sim_hub, monkeypatch, "focuser", "get_position")
    temperature = _Stalled(sim_hub, monkeypatch, "focuser", "get_temperature")
    try:
        await _save(sim_hub)
        assert position.calls == 1, position.calls
        assert temperature.calls == 0, (
            f"{temperature.calls} temperature read begun under a position "
            f"read that had not answered")
    finally:
        position.release.set()
        temperature.release.set()


# ---------------------------------------- the stall belongs to the read only


@pytest.mark.parametrize("device,method", [
    pytest.param("focuser", "get_position", id="focuser"),
    pytest.param("telescope", "get_position", id="mount"),
    pytest.param("camera", "get_cooler", id="cooler"),
    pytest.param("rotator", "get_mechanical_position", id="rotator"),
])
async def test_a_read_still_inside_the_driver_is_not_stacked_under_a_new_one(
        sim_hub, monkeypatch, device, method):
    """A read that has not returned keeps its worker thread. The next frame's
    read joins it rather than starting another under it, so a device that stays
    dead for a night's frames costs one thread and not one a frame."""
    stalled = _Stalled(sim_hub, monkeypatch, device, method)
    try:
        for _ in range(3):
            await _save(sim_hub)
        assert stalled.calls == 1, (
            f"{stalled.calls} reads were started under one that had not "
            f"returned")
    finally:
        stalled.release.set()


async def test_the_status_poll_and_the_header_share_one_read(
        sim_hub, monkeypatch):
    """The status poll reads the focuser's position through the same helper
    and the same key, so the read one of them has left in the driver is the
    read the other waits on."""
    stalled = _Stalled(sim_hub, monkeypatch, "focuser", "get_position")
    try:
        await _within(sim_hub.poll_status(), "the status poll")
        await _save(sim_hub)
        assert stalled.calls == 1, stalled.calls
    finally:
        stalled.release.set()


async def test_a_stall_is_said_once(sim_hub, monkeypatch):
    """The warning names the device's read and the bound, once for a stall that
    lasts several frames, and the guard that turns it into an omitted card does
    not say it again."""
    logs = _record_logs(monkeypatch)
    stalled = _Stalled(sim_hub, monkeypatch, "focuser", "get_position")
    try:
        for _ in range(3):
            await _save(sim_hub)
        said = [ln for ln in logs if "has not returned" in ln[1]]
        assert len(said) == 1 and said[0][0] == "warning", logs
        assert "focuser position read" in said[0][1], said[0][1]
        assert f"{BOUND_S:g} s" in said[0][1], said[0][1]
        again = [ln for ln in logs if "the frame header went without" in ln[1]]
        assert not again, again
    finally:
        stalled.release.set()


async def test_a_device_that_recovers_is_read_again(sim_hub, monkeypatch):
    """The stall belongs to the read that stalled. When the driver finally
    answers, the next frame asks again and its header carries the value; a
    device is not given up on for the rest of the session."""
    stalled = _Stalled(sim_hub, monkeypatch, "focuser", "get_position")
    try:
        await _save(sim_hub)
        assert "FOCPOS" not in _header(sim_hub), "premise: the read stalled"
        stalled.release.set()
        await asyncio.sleep(0.1)                 # the held read finishes

        await _save(sim_hub)
        assert "FOCPOS" in _header(sim_hub)
        assert stalled.calls == 2, stalled.calls
    finally:
        stalled.release.set()


@pytest.mark.parametrize("device,method", [
    pytest.param("focuser", "get_position", id="focuser"),
    pytest.param("telescope", "get_position", id="mount"),
    pytest.param("camera", "get_cooler", id="cooler"),
    pytest.param("rotator", "get_mechanical_position", id="rotator"),
])
async def test_a_stop_still_cancels_the_header_read(
        sim_hub, monkeypatch, device, method):
    """CONTROL. A cancel that lands while the read waits is the caller's Stop
    and reaches it: the guards around the read are for the device's errors,
    not for this."""
    stalled = _Stalled(sim_hub, monkeypatch, device, method)
    # A bound long enough that the cancel lands inside the wait.
    monkeypatch.setattr(hub_module, "STATUS_DEVICE_READ_TIMEOUT_S", 30.0)
    task = asyncio.ensure_future(
        sim_hub.capture(0.05, 100, 30, 1, save=True, target=""))
    try:
        for _ in range(100):
            await asyncio.sleep(0.05)
            if stalled.calls:
                break
        assert stalled.calls, "premise: the capture reached the device"
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5.0)
    finally:
        stalled.release.set()
