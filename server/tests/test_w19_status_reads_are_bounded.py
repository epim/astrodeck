# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Every device read in the status poll is bounded (WP-164, #814).

WHAT WENT WRONG. WP-143 (#724) bounded the imaging camera's temperature read in
``Hub.poll_status`` and left every other device read awaited directly: the
mount's position, tracking, park, slew and rate and the meridian built from it,
the focuser's position, temperature and motion, the filter wheel's position and
motion, the dome's shutter, the rotator's angle, motion and direction, and the
camera's dew heater, fan and cooler. A USB or HTTP stall in any of them held the
whole two-second frame and the ``/api/status`` caller for as long as the call
stayed in the driver.

THE FIX. ``Hub._status_read`` is the shape of ``Hub._imaging_temperature`` for
every one of those reads: the read runs as ONE task in flight per device and
reading, the poll waits on it for at most ``STATUS_DEVICE_READ_TIMEOUT_S``, and a
read that has not returned raises ``StatusReadStalled``, which each block's
existing guard turns into the same absence as a read that failed (the block off
the frame, a key null or absent). A device that has just failed to answer is
not asked again in the same poll, and the stall is said once.

Each case stalls ONE read of ONE device on the simulator's rig (a coroutine that
waits on an event the case releases at the end) and polls with the bound
shortened, so a stall does not wait out the real one.

Named mutants, each run from a byte backup of ``hub.py`` and restored with a
byte copy (md5 compared). The first failing assertion is quoted with the case
that raised it.

* "unbounded" (``done, _pending = await asyncio.wait({probe}, timeout=
  STATUS_DEVICE_READ_TIMEOUT_S)`` in ``_status_read`` made ``await probe``) ->
  ``test_a_stalled_read_does_not_hold_up_the_status_frame`` (every case):
  Failed, "the status frame waited on a stalled ..." out of the case's own
  2.5 s ``asyncio.wait_for`` on the poll.
* "probes stack" (``if held is not None and held[0] is dev and not
  held[1].done():`` made ``if False:``) ->
  ``test_a_read_still_inside_the_driver_is_not_stacked_under_a_new_one``:
  AssertionError, "4 focuser temperature reads were started under one that had
  not returned".
* "a device that did not answer is asked again" (``if role in stalled:`` made
  ``if False:``) -> ``test_a_device_that_did_not_answer_is_not_asked_again_in_
  the_same_poll``: AssertionError, "the mount was asked for its tracking after
  it had failed to answer for its position" (and the mount-tail cases, which
  hold the frame one bound per read).
* "said every poll" (``if key not in said:`` made ``if True:``) ->
  ``test_a_stall_is_said_once_and_a_recovery_is_quiet``: AssertionError, "a
  stall that lasted four polls was announced 4 times".
* "never said again" (``said.discard(key)`` removed) -> the same case:
  AssertionError, "a second stall after a recovery was not announced: 1".
* "a raised read is read as a stall" (``stalled.add(role)`` also made on the
  result's exception) -> ``test_a_read_that_raises_is_not_a_stall``:
  AssertionError, "a focuser temperature read that raised kept the rest of the
  focuser from being read".
* "the exception is not retrieved" (the done-callback ``lambda t:
  t.cancelled() or t.exception()`` made ``lambda t: None``) ->
  ``test_a_late_failure_leaves_no_unretrieved_exception``: AssertionError, "1
  read(s) were dropped with an exception nobody retrieved".
* "the replaced device waits on the old one's read" (``held[0] is dev`` dropped)
  -> ``test_a_device_that_was_replaced_is_not_made_to_wait_on_the_old_ones_
  read``: AssertionError, "the replacement focuser was never asked: its poll
  waited on the read of the one it replaced".
* "not started eagerly" (``if hasattr(asyncio, "eager_task_factory"):`` made
  ``if False:``) -> ``test_an_instant_read_costs_the_poll_no_trip_round_the_
  event_loop``: AssertionError, "the poll went round the event loop for a read
  that answered at once".
* "the bound widened" (``STATUS_DEVICE_READ_TIMEOUT_S = 2.0`` made ``50.0``) ->
  ``test_the_bound_is_short``: AssertionError, 50.0.
* "the meridian is not bounded" (the ``read("mount", "meridian", ...)`` call
  made ``await self._compute_meridian(tel, ra, dec)``) ->
  ``test_a_stalled_read_does_not_hold_up_the_status_frame[meridian.pier_side]``
  and ``[meridian.flip_time]``: Failed, "the status frame waited on a stalled
  telescope pier_side".
"""
from __future__ import annotations

import asyncio
import gc
import time

import pytest

import astrodeck.events as events_mod
import astrodeck.hub as hub_module
from _simhub import sim_hub  # noqa: F401 (fixture import)


@pytest.fixture
def short_bound(monkeypatch):
    """The bound shortened so a test of a stall does not wait out the real one.
    The constant is read at call time. ``raising=False``: on code without it a
    case fails on what it grades (a frame that never comes), not on a missing
    attribute."""
    monkeypatch.setattr(hub_module, "STATUS_DEVICE_READ_TIMEOUT_S", 0.25,
                        raising=False)


def _record_logs(monkeypatch) -> list[tuple[str, str, str]]:
    lines: list[tuple[str, str, str]] = []

    def record(level, message, source="hub", **_kw):
        lines.append((level, message, source))
    monkeypatch.setattr(events_mod.bus, "log", record)
    return lines


def _stall(monkeypatch, dev, method: str, release: asyncio.Event, *,
           then=None, raises: BaseException | None = None) -> list[str]:
    """Replace ``dev.method`` with a read that does not return until
    ``release`` is set, then returns ``then`` (or raises ``raises``). Returns
    the list the replacement appends to on every call."""
    calls: list[str] = []

    async def stalled(*_a, **_kw):
        calls.append(method)
        await release.wait()
        if raises is not None:
            raise raises
        return then
    monkeypatch.setattr(dev, method, stalled)
    return calls


def _prime_camera(monkeypatch, cam) -> None:
    """A camera that answers for a dew heater and a fan, which the sim's does
    not: without values there is nothing to find absent."""
    async def dew():
        return 35

    async def fan():
        return 70
    monkeypatch.setattr(cam, "get_dew_heater", dew)
    monkeypatch.setattr(cam, "get_fan_power", fan)


def _has(status: dict, path: str) -> bool:
    node = status
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


def _value(status: dict, path: str):
    node = status
    for part in path.split("."):
        node = node[part]
    return node


async def _poll(hub, what: str) -> dict:
    try:
        return await asyncio.wait_for(hub.poll_status(), 2.5)
    except asyncio.TimeoutError:
        pytest.fail(f"the status frame waited on a stalled {what}")


# (id, device, method, keys that must be ABSENT, keys that must be present and
# NULL, keys that must still be present). An absent key is the answer for a
# read that failed; the block's other readings stay.
_MOUNT_GONE = ["mount", "meridian"]
CASES = [
    ("mount.position", "telescope", "get_position", _MOUNT_GONE, [],
     ["focuser", "filterwheel", "rotator", "camera"]),
    ("mount.tracking", "telescope", "get_tracking", _MOUNT_GONE, [],
     ["focuser", "camera"]),
    ("mount.park", "telescope", "is_parked", _MOUNT_GONE, [], ["focuser"]),
    ("mount.slew", "telescope", "is_slewing", _MOUNT_GONE, [], ["focuser"]),
    ("mount.rate", "telescope", "get_tracking_rate", _MOUNT_GONE, [],
     ["focuser"]),
    ("meridian.pier_side", "telescope", "pier_side", ["meridian"], [],
     ["mount", "focuser"]),
    ("meridian.flip_time", "telescope", "time_to_meridian_flip", ["meridian"],
     [], ["mount", "focuser"]),
    ("focuser.position", "focuser", "get_position", ["focuser"], [],
     ["mount", "filterwheel", "camera"]),
    ("focuser.temperature", "focuser", "get_temperature", ["focuser.moving"],
     ["focuser.temperature"], ["focuser.position", "mount"]),
    ("focuser.motion", "focuser", "is_moving", ["focuser.moving"], [],
     ["focuser.position", "focuser.temperature"]),
    ("wheel.position", "filterwheel", "get_position", ["filterwheel"], [],
     ["focuser", "camera"]),
    ("wheel.motion", "filterwheel", "is_moving", ["filterwheel.moving"], [],
     ["filterwheel.position", "filterwheel.names"]),
    ("dome.shutter", "dome", "shutter_state", ["dome"], [],
     ["rotator", "camera"]),
    ("rotator.angle", "rotator", "get_mechanical_position", ["rotator"], [],
     ["camera", "dome"]),
    ("rotator.motion", "rotator", "is_moving", ["rotator"], [],
     ["camera", "dome"]),
    ("rotator.direction", "rotator", "get_reverse", ["rotator"], [],
     ["camera", "dome"]),
    ("camera.dew_heater", "camera", "get_dew_heater",
     ["camera.dew_heater", "camera.fan_power", "camera.cooler"], [],
     ["camera.temperature", "camera.width"]),
    ("camera.fan", "camera", "get_fan_power",
     ["camera.fan_power", "camera.cooler"], [],
     ["camera.temperature", "camera.dew_heater"]),
    ("camera.cooler", "camera", "get_cooler", ["camera.cooler"], [],
     ["camera.temperature", "camera.dew_heater", "camera.fan_power"]),
]


def test_the_bound_is_short():
    """One status period (the loop sleeps two seconds between polls) at most: a
    stall may cost the frame no more than the frame is worth."""
    bound = hub_module.STATUS_DEVICE_READ_TIMEOUT_S
    assert 0 < bound <= 2.0, bound


async def test_a_healthy_poll_publishes_every_block_and_asks_each_read_once(
        sim_hub, monkeypatch):
    """CONTROL. Nothing stalls: every block the cases below take readings from
    is on the frame, and each read is made once a poll, not once more for the
    bound."""
    _prime_camera(monkeypatch, sim_hub.devices["camera"])
    counts = {"get_position": 0}
    foc = sim_hub.devices["focuser"]
    real = foc.get_position

    async def counted():
        counts["get_position"] += 1
        return await real()
    monkeypatch.setattr(foc, "get_position", counted)

    for _ in range(3):
        status = await sim_hub.poll_status()

    for path in ("mount.tracking", "mount.parked", "mount.slewing",
                 "mount.tracking_rate", "meridian.status", "focuser.position",
                 "focuser.temperature", "focuser.moving",
                 "filterwheel.position", "filterwheel.moving", "dome.shutter",
                 "rotator.mech_deg", "rotator.moving", "rotator.reverse",
                 "camera.temperature", "camera.dew_heater", "camera.fan_power",
                 "camera.cooler"):
        assert _has(status, path), f"a healthy poll left {path} off the frame"
    assert status["camera"]["dew_heater"] == 35
    assert status["camera"]["fan_power"] == 70
    assert counts["get_position"] == 3, counts


@pytest.mark.parametrize("device,method,absent,null,present",
                         [c[1:] for c in CASES], ids=[c[0] for c in CASES])
async def test_a_stalled_read_does_not_hold_up_the_status_frame(
        sim_hub, short_bound, monkeypatch, device, method, absent, null,
        present):
    """One read of one device never returns. The frame comes anyway, the
    reading is off it exactly as a read that failed would leave it, and
    everything else on it is still there."""
    _prime_camera(monkeypatch, sim_hub.devices["camera"])
    release = asyncio.Event()
    try:
        _stall(monkeypatch, sim_hub.devices[device], method, release)

        status = await _poll(sim_hub, f"{device} {method}")

        for path in absent:
            assert not _has(status, path), (
                f"{path} was published from a {device} whose {method} had not "
                "returned")
        for path in null:
            assert _has(status, path) and _value(status, path) is None, (
                f"{path} should be null, not {_has(status, path)!r}")
        for path in present:
            assert _has(status, path), (
                f"a stalled {device} {method} took {path} off the frame")
    finally:
        release.set()


async def test_a_device_that_did_not_answer_is_not_asked_again_in_the_same_poll(
        sim_hub, short_bound, monkeypatch):
    """The mount's position never returns. Its tracking, park, slew and rate
    reads and the meridian's pier side would only join the stall, so none is
    made: bounding one read of the block would bound nothing."""
    tel = sim_hub.devices["telescope"]
    release = asyncio.Event()
    asked: list[str] = []
    for method in ("get_tracking", "is_parked", "is_slewing",
                   "get_tracking_rate", "pier_side", "time_to_meridian_flip"):
        real = getattr(tel, method)

        def wrap(real=real, method=method):
            async def counted(*a, **kw):
                asked.append(method)
                return await real(*a, **kw)
            return counted
        monkeypatch.setattr(tel, method, wrap())
    try:
        _stall(monkeypatch, tel, "get_position", release)

        await _poll(sim_hub, "telescope get_position")

        assert asked == [], (
            f"the mount was asked for {asked} after it had failed to answer "
            "for its position")
    finally:
        release.set()


async def test_two_stalled_devices_each_cost_the_frame_one_bound(
        sim_hub, short_bound, monkeypatch):
    """The mount and the filter wheel on one bad hub. Each is bounded, so the
    frame costs about two bounds, not two forever."""
    release = asyncio.Event()
    try:
        _stall(monkeypatch, sim_hub.devices["telescope"], "get_position",
               release)
        _stall(monkeypatch, sim_hub.devices["filterwheel"], "get_position",
               release)
        t0 = time.monotonic()

        status = await _poll(sim_hub, "telescope and filter wheel")

        assert time.monotonic() - t0 < 2.0
        assert "mount" not in status and "filterwheel" not in status
        assert "focuser" in status and "camera" in status
    finally:
        release.set()


async def test_a_read_still_inside_the_driver_is_not_stacked_under_a_new_one(
        sim_hub, short_bound, monkeypatch):
    release = asyncio.Event()
    foc = sim_hub.devices["focuser"]
    try:
        calls = _stall(monkeypatch, foc, "get_temperature", release, then=7.5)

        for _ in range(4):
            await _poll(sim_hub, "focuser get_temperature")

        assert len(calls) == 1, (
            f"{len(calls)} focuser temperature reads were started under one "
            "that had not returned")
    finally:
        release.set()
    # ...and once it has returned, a poll asks again and publishes the answer.
    # Polled against a wall-clock deadline (#669).
    async def answers():
        return 7.5
    monkeypatch.setattr(foc, "get_temperature", answers)
    deadline = time.monotonic() + 5.0
    status = await sim_hub.poll_status()
    while (status["focuser"]["temperature"] is None
           and time.monotonic() < deadline):
        await asyncio.sleep(0.01)
        status = await sim_hub.poll_status()
    assert status["focuser"]["temperature"] == 7.5, (
        "the stall stuck: the read that returned was never asked for again")


async def test_a_stall_is_said_once_and_a_recovery_is_quiet(
        sim_hub, short_bound, monkeypatch):
    logs = _record_logs(monkeypatch)
    foc = sim_hub.devices["focuser"]
    first = asyncio.Event()
    try:
        _stall(monkeypatch, foc, "get_temperature", first, then=7.5)
        for _ in range(4):
            await _poll(sim_hub, "focuser get_temperature")
    finally:
        first.set()

    def said():
        return [ln for ln in logs
                if "temperature" in ln[1] and ln[0] == "warning"]
    assert len(said()) == 1, (
        f"a stall that lasted four polls was announced {len(said())} times")
    assert said()[0][2] == "focuser", said()[0]
    assert "not returned" in said()[0][1], said()[0]

    # Recovered: the next answer clears it, so a SECOND stall is said again.
    async def answers():
        return 7.5
    monkeypatch.setattr(foc, "get_temperature", answers)
    deadline = time.monotonic() + 5.0
    status = await sim_hub.poll_status()
    while (status["focuser"]["temperature"] is None
           and time.monotonic() < deadline):
        await asyncio.sleep(0.01)
        status = await sim_hub.poll_status()
    assert status["focuser"]["temperature"] == 7.5
    assert len(said()) == 1, "a recovery was announced as a stall"
    second = asyncio.Event()
    try:
        _stall(monkeypatch, foc, "get_temperature", second, then=7.5)
        await _poll(sim_hub, "focuser get_temperature")
    finally:
        second.set()
    assert len(said()) == 2, (
        f"a second stall after a recovery was not announced: {len(said())}")


async def test_a_read_that_raises_is_not_a_stall(sim_hub, short_bound,
                                                 monkeypatch):
    """CONTROL, and the behaviour that was always there: a read that RAISES
    answered, and reads as before. The rest of its device is still read, and
    nothing is said. The bound is for a read that does not return."""
    logs = _record_logs(monkeypatch)
    foc = sim_hub.devices["focuser"]

    async def broken():
        raise RuntimeError("unplugged probe")
    monkeypatch.setattr(foc, "get_temperature", broken)

    status = await sim_hub.poll_status()

    assert status["focuser"]["temperature"] is None
    assert "moving" in status["focuser"], (
        "a focuser temperature read that raised kept the rest of the focuser "
        "from being read")
    assert not [ln for ln in logs if "not returned" in ln[1]], logs

    async def lost():
        raise RuntimeError("link down")
    monkeypatch.setattr(foc, "get_position", lost)
    status = await sim_hub.poll_status()
    assert "focuser" not in status, "a focuser that raised for its position"


async def test_a_late_failure_leaves_no_unretrieved_exception(
        sim_hub, short_bound, monkeypatch):
    """The read that times out is a task nobody is awaiting any more, so when it
    ENDS in an error (the driver gives up after the poll did) its exception has
    to be retrieved by the task's own done-callback, or the loop writes "Task
    exception was never retrieved" at garbage collection."""
    seen: list[dict] = []
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "call_exception_handler", seen.append)
    foc = sim_hub.devices["focuser"]
    release = asyncio.Event()
    try:
        _stall(monkeypatch, foc, "get_temperature", release,
               raises=RuntimeError("driver gave up"))
        await _poll(sim_hub, "focuser get_temperature")        # times out
    finally:
        release.set()
    # The worker leaves the driver and raises into a task nobody awaits.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        held = getattr(sim_hub, "_status_reads", {}).get("focuser.temperature")
        finished = held is not None and held[1].done()
        del held          # a reference here would keep the task from being dropped
        if finished:
            break
        await asyncio.sleep(0.01)

    # A poll that finds the finished probe replaces it, and the replaced task is
    # what gets collected. The driver answers this time: a second failure would
    # keep the first task alive through its own traceback and hide the thing
    # being looked for.
    async def answers():
        return 7.5
    monkeypatch.setattr(foc, "get_temperature", answers)
    await sim_hub.poll_status()
    await asyncio.sleep(0)
    gc.collect()

    unretrieved = [c for c in seen if "never retrieved" in str(c.get("message"))]
    assert unretrieved == [], (
        f"{len(unretrieved)} read(s) were dropped with an exception nobody "
        "retrieved")


async def test_a_device_that_was_replaced_is_not_made_to_wait_on_the_old_ones_read(
        sim_hub, short_bound, monkeypatch):
    """A profile re-activation (or a reconnect) puts a NEW device object in the
    hub while the old one's read may still be inside its driver. The new one
    must be asked its own position, not wait out the old one's read and then
    publish the old one's number as its own."""
    old = sim_hub.devices["focuser"]
    fresh = type(old)(sim_hub.sim_rig, "Replacement focuser")
    await fresh.connect()
    release = asyncio.Event()
    asked: list[str] = []

    async def position():
        asked.append("get_position")
        return 4321
    monkeypatch.setattr(fresh, "get_position", position)
    try:
        _stall(monkeypatch, old, "get_position", release)
        await _poll(sim_hub, "focuser get_position")             # times out

        sim_hub.devices["focuser"] = fresh
        status = await _poll(sim_hub, "replacement focuser get_position")

        assert asked == ["get_position"], (
            "the replacement focuser was never asked: its poll waited on the "
            "read of the one it replaced")
        assert status["focuser"]["position"] == 4321
    finally:
        release.set()
        sim_hub.devices["focuser"] = old


@pytest.mark.skipif(not hasattr(asyncio, "eager_task_factory"),
                    reason="eager task start is Python 3.12+")
async def test_an_instant_read_costs_the_poll_no_trip_round_the_event_loop(
        sim_hub):
    """The read of a device that answers without suspending (the simulator, any
    backend serving a cached value) is finished before the helper returns: the
    event loop is never handed control. Status polls run concurrently with
    themselves (the background loop and /api/status), and the ORDER in which
    they reach their later device reads is part of what the suite grades."""
    foc = sim_hub.devices["focuser"]
    ran: list[int] = []
    asyncio.get_running_loop().call_soon(ran.append, 1)

    value = await sim_hub._status_read(set(), "focuser", "position", foc,
                                       foc.get_position)

    assert value is not None
    assert ran == [], (
        "the poll went round the event loop for a read that answered at once")
    await asyncio.sleep(0)
    assert ran == [1], "premise: the callback was runnable all along"
