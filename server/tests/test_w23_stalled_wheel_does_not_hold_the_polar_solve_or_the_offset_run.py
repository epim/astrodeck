# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A stalled filter wheel does not hold the native TPPA's solve step or the
filter-offset run for ever (WP-215, #1010; the class of #815, #935 and #999).

Two places awaited the wheel with no bound, and the ``except Exception`` around
each catches a wheel that raised and not one that never answers:

* ``polar.native._apply_solve_filter``, the step that moves the wheel off a
  narrowband or opaque slot before a polar solve frame: ``get_position`` and
  ``set_position``. It runs before the first exposure and before every solve
  frame after it, so a wheel that stalled held the alignment's start for ever.
* ``Hub.learn_filter_offsets``, the per-filter autofocus run: the reference
  slot's ``get_position`` and every slot's ``set_position``.

A native wheel whose SDK read stalls in USB never trips a transport timeout,
and ``AlpacaFilterWheel.set_position`` polls while the wheel reports -1 (a
wheel jammed between slots), so no bound of the transport's ends the wait.

THE FIX bounds each await as the centring path's are: the position read by
``STATUS_DEVICE_READ_TIMEOUT_S`` (this runs before every solve frame, #999),
the move by ``SOLVE_WHEEL_MOVE_TIMEOUT_S``. A bare ``TimeoutError`` has no
text, so the line carries the bound.

* The polar step says the wheel is stalled, with the bound, and the solve goes
  ahead on whatever is loaded, which is what it does when the wheel raises.
* The offset run ends with a ``DeviceError`` that says the wheel is stalled and
  the bound it missed (published as the run's ``failed`` state), and writes no
  offsets: a half-measured wheel keeps the offsets it had.

Each case stalls one await of the wheel (a coroutine that waits on an event the
case releases at the end) with ONLY the bound under test shortened. The other
stays at its real value, so a read that waited on the move bound, or a move
that waited on the read bound, fails on the case's own wall-clock limit.

Named mutants, each run from a byte backup of the file and restored with a byte
copy (md5 compared). The first failing assertion is quoted.

* "polar read unbounded" (``native._apply_solve_filter`` awaiting
  ``fw.get_position()`` directly) -> ``test_a_wheel_whose_position_read_never_
  returns_does_not_hold_the_polar_solve``: Failed, "the polar solve's filter
  step waited on a wheel that never answered".
* "polar move unbounded" (``fw.set_position(slot)`` awaited directly) -> the
  move case, the same way.
* "polar read on the move bound" (``SOLVE_WHEEL_MOVE_TIMEOUT_S`` where the
  position read has ``STATUS_DEVICE_READ_TIMEOUT_S``) -> the read case, on its
  wall-clock limit.
* "offset run unbounded" (``learn_filter_offsets`` awaiting the reference read
  and the slot move directly) -> both offset cases, the same way.
* "stall reads as a bare timeout" (the ``DeviceError`` raised with the empty
  text of the ``TimeoutError``) -> the offset cases' text assertions.

The controls pass on the unfixed tree as well, which is what makes them
controls: a wheel that answers is moved and read, a wheel that raises still
does not end the solve, and a Stop that lands while the wheel is awaited still
cancels the step.
"""
from __future__ import annotations

import asyncio
import contextlib
import time

import numpy as np
import pytest

import astrodeck.config as configmod
import astrodeck.events as events_mod
import astrodeck.focus.autofocus as af_module
import astrodeck.hub as hub_module
from astrodeck.config import FrameSettingsConfig, config_store
from astrodeck.devices.base import DeviceError
from astrodeck.polar import native as nat
from _simhub import sim_hub  # noqa: F401 (fixture import)

#: The bound under test, shortened. The other bound is left at its real value.
BOUND_S = 0.3
#: Well under the 90 s of a wheel move and over several of BOUND_S.
LIMIT_S = 12.0

WHEEL = ["L", "R", "G", "B", "S", "Ha", "Oiii", "Dark"]


def _record_logs(monkeypatch) -> list[tuple[str, str, str]]:
    lines: list[tuple[str, str, str]] = []
    real = events_mod.bus.log

    def record(level, message, source="hub", **kw):
        lines.append((level, message, source))
        return real(level, message, source, **kw)
    monkeypatch.setattr(events_mod.bus, "log", record)
    return lines


async def _within(coro, what: str):
    try:
        return await asyncio.wait_for(coro, LIMIT_S)
    except asyncio.TimeoutError:
        pytest.fail(f"{what} waited on a wheel that never answered")


# ------------------------------------------------- the polar solve-filter step


class _Wheel:
    """A bare wheel as the polar tests build one. ``never`` names the method
    that never returns until ``release`` is set."""
    connected = True
    filter_names = list(WHEEL)

    def __init__(self, never: str | None = None, position: int = 7):
        self.position = position
        self.moves: list[int] = []
        self.reads = 0
        self.never = never
        self.release = asyncio.Event()

    async def get_position(self):
        self.reads += 1
        if self.never == "get_position":
            await self.release.wait()
        return self.position

    async def set_position(self, slot):
        self.moves.append(slot)
        if self.never == "set_position":
            await self.release.wait()
        self.position = slot


class _Hub:
    def __init__(self, wheel):
        self.devices = {"filterwheel": wheel}


@pytest.fixture(autouse=True)
def _fresh_frames():
    """The solve scope is PERSISTED, so a test that names a filter would
    otherwise leak into whatever runs next in this worker."""
    config_store.set_frames(FrameSettingsConfig())
    yield
    config_store.set_frames(FrameSettingsConfig())


async def test_a_wheel_whose_position_read_never_returns_does_not_hold_the_polar_solve(
        monkeypatch):
    """The wheel's position read never returns. The step gives up at the READ
    bound (the move bound is left at its real 90 s), says the wheel is stalled
    with the bound, commands no move, and returns so the solve can go ahead."""
    monkeypatch.setattr(hub_module, "STATUS_DEVICE_READ_TIMEOUT_S", BOUND_S,
                        raising=False)
    logs = _record_logs(monkeypatch)
    wheel = _Wheel(never="get_position")
    began = time.monotonic()
    try:
        await _within(nat._apply_solve_filter(_Hub(wheel), "L"),
                      "the polar solve's filter step")
        waited = time.monotonic() - began
        assert BOUND_S * 0.5 < waited < BOUND_S * 8, (
            f"the step gave up after {waited:.2f} s; the bound is {BOUND_S} s")
        assert wheel.moves == [], "a wheel that could not be read was moved"
        said = [m for lvl, m, src in logs
                if lvl == "warning" and "stalled" in m]
        assert len(said) == 1, logs
        assert "position read" in said[0], said[0]
        assert f"{BOUND_S:g} s" in said[0], said[0]
        assert "native TPPA" in said[0], said[0]
    finally:
        wheel.release.set()


async def test_a_wheel_whose_move_never_finishes_does_not_hold_the_polar_solve(
        monkeypatch):
    """The wheel is read, and the move to luminance never finishes (a wheel
    jammed between slots). The step gives up at the MOVE bound (the read bound
    is left at its real 2 s, which is not what the case waits on), says the
    wheel is stalled, names the slot it was sent to and the bound, and returns."""
    monkeypatch.setattr(hub_module, "SOLVE_WHEEL_MOVE_TIMEOUT_S", BOUND_S,
                        raising=False)
    logs = _record_logs(monkeypatch)
    wheel = _Wheel(never="set_position", position=7)      # Dark: must move
    began = time.monotonic()
    try:
        await _within(nat._apply_solve_filter(_Hub(wheel), "L"),
                      "the polar solve's filter step")
        waited = time.monotonic() - began
        assert BOUND_S * 0.5 < waited < BOUND_S * 8, (
            f"the step gave up after {waited:.2f} s; the bound is {BOUND_S} s")
        assert wheel.moves == [0], wheel.moves
        said = [m for lvl, m, src in logs
                if lvl == "warning" and "stalled" in m]
        assert len(said) == 1, logs
        assert "move to 'L'" in said[0], said[0]
        assert f"{BOUND_S:g} s" in said[0], said[0]
    finally:
        wheel.release.set()




async def test_a_healthy_wheel_is_read_and_moved_off_narrowband(monkeypatch):
    """CONTROL. The premise the stall cases depend on: a wheel that answers is
    read and, parked on the dark slot, moved to luminance once, with nothing
    said about it."""
    logs = _record_logs(monkeypatch)
    wheel = _Wheel(position=7)
    await _within(nat._apply_solve_filter(_Hub(wheel), "L"),
                  "the polar solve's filter step")
    assert wheel.moves == [0], wheel.moves
    assert not [m for lvl, m, src in logs if lvl == "warning"], logs


async def test_a_wheel_that_raises_is_still_said_as_before(monkeypatch):
    """CONTROL. The generic guard is untouched: a wheel that RAISES is the
    old line, and is not reported as a stall."""
    logs = _record_logs(monkeypatch)
    wheel = _Wheel(position=7)

    async def broken(slot):
        raise RuntimeError("wheel jam")
    monkeypatch.setattr(wheel, "set_position", broken)
    await _within(nat._apply_solve_filter(_Hub(wheel), "L"),
                  "the polar solve's filter step")
    said = [m for lvl, m, src in logs if lvl == "warning"]
    assert len(said) == 1 and "could not move the filter wheel" in said[0], said
    assert "stalled" not in said[0], said[0]


@pytest.mark.parametrize("never", ["get_position", "set_position"])
async def test_a_stop_still_cancels_the_polar_wheel_await(monkeypatch, never):
    """CONTROL. A cancel that lands while the wheel is awaited is the caller's
    Stop and reaches it: the guard around the step is for the wheel's errors,
    not for this."""
    monkeypatch.setattr(hub_module, "STATUS_DEVICE_READ_TIMEOUT_S", 30.0)
    monkeypatch.setattr(hub_module, "SOLVE_WHEEL_MOVE_TIMEOUT_S", 30.0)
    wheel = _Wheel(never=never)
    task = asyncio.ensure_future(nat._apply_solve_filter(_Hub(wheel), "L"))
    try:
        await asyncio.sleep(0.1)
        assert not task.done(), "premise: the step is waiting on the wheel"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5.0)
    finally:
        wheel.release.set()


# ------------------------------------------- through the polar solve's caller


class _Frame:
    timestamp = 1000.0
    data = np.zeros((4, 6), dtype="u2")


class _Cam:
    name = "cam"

    def __init__(self):
        self.exposures: list[tuple] = []

    async def expose(self, exposure_s, gain, offset, binning=1):
        self.exposures.append((exposure_s, gain, offset, binning))
        return _Frame()


class _Tel:
    async def get_position(self):
        return 5.0, 40.0


class _Solver:
    async def solve(self, path, **kw):
        class _R:
            success = True
            ra_hours, dec_deg = 5.0, 40.0
            rotation_deg, pixel_scale_arcsec = 0.0, 1.55
            message = "ok"
        return _R()


class _SolveHub(_Hub):
    def __init__(self, wheel):
        super().__init__(wheel)
        self.cam, self.tel = _Cam(), _Tel()
        self.site = {"latitude": 45.0, "longitude": -116.0}

    def require(self, role):
        return self.cam if role == "camera" else self.tel

    async def from_mount_frame(self, tel, ra, dec):
        return ra, dec

    def effective_optics(self):
        return {"fov_h_deg": 1.2, "image_scale_arcsec_px": 1.55}

    def exposure_guard(self, what):
        return contextlib.nullcontext()

    async def _publish_preview(self, frame):
        pass


@pytest.mark.parametrize("never,bound", [
    pytest.param("get_position", "STATUS_DEVICE_READ_TIMEOUT_S", id="read"),
    pytest.param("set_position", "SOLVE_WHEEL_MOVE_TIMEOUT_S", id="move"),
])
async def test_a_polar_solve_frame_is_exposed_and_solved_through_a_stalled_wheel(
        monkeypatch, tmp_path, never, bound):
    """Through the real caller. The wheel stalls in the step before the
    exposure; the frame is still exposed and solved, which is what 'the solve
    goes ahead on whatever is loaded' means. Before the bound the exposure was
    never reached."""
    monkeypatch.setattr(hub_module, bound, BOUND_S, raising=False)
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module, "save_fits", lambda frame, path, **kw: None)
    from astrodeck.polar.session import PolarAlignSession
    session = PolarAlignSession(hub=None)
    session.set_solve_settings(filter="L")
    wheel = _Wheel(never=never, position=7)
    hub = _SolveHub(wheel)
    try:
        _frame, result, _geom = await _within(
            nat._capture_and_solve(hub, _Solver(), session),
            "the polar solve")
        assert result.success
        assert len(hub.cam.exposures) == 1, hub.cam.exposures
    finally:
        wheel.release.set()


# ------------------------------------------------------ the filter-offset run


@pytest.fixture
def offsets_rig(sim_hub, tmp_path, monkeypatch):
    """The sim rig's wheel with three slots and offsets the run must keep, the
    wheel's moves recorded, and the autofocus replaced by one that finds focus
    at once (the sweep is not what these cases grade)."""
    monkeypatch.setattr(configmod, "FILTER_CONFIG_FILE",
                        tmp_path / "filter_names.json")
    fw = sim_hub.devices["filterwheel"]
    fw.filter_names = ["L", "Ha", "OIII"]
    fw.filter_offsets = [0, 77, 0]
    fw.filter_narrowband = [False, False, False]
    fw.filter_opaque = [False, False, False]
    state = {"moves": [], "release": asyncio.Event(), "stall_move_to": None,
             "stall_read": False, "reads": 0}

    async def get_position():
        state["reads"] += 1
        if state["stall_read"]:
            await state["release"].wait()
        return fw.rig.filter_slot

    async def set_position(slot):
        state["moves"].append(int(slot))
        if state["stall_move_to"] == int(slot):
            await state["release"].wait()
        fw.rig.filter_slot = int(slot)

    async def fake_af(cam, foc, **kw):
        return af_module.AutofocusResult(
            True, 5000 + 50 * fw.rig.filter_slot, 2.1, [], "ok")

    monkeypatch.setattr(fw, "get_position", get_position)
    monkeypatch.setattr(fw, "set_position", set_position)
    monkeypatch.setattr(af_module, "run_autofocus", fake_af)
    state["fw"] = fw
    try:
        yield state
    finally:
        state["release"].set()


async def test_a_wheel_that_answers_lets_the_offset_run_finish(
        sim_hub, offsets_rig):
    """CONTROL. The run measures every slot and writes the offsets, so a run
    that fails in the cases below is failing on the stall."""
    out = await _within(sim_hub.learn_filter_offsets(exposure_s=0.01),
                        "the offset run")
    assert sorted(offsets_rig["moves"]) == [0, 1, 2]
    assert out["offsets"] == [0, 50, 100], out


async def test_a_wheel_whose_position_read_never_returns_does_not_hold_the_offset_run(
        sim_hub, offsets_rig, monkeypatch):
    """The reference slot is read from the wheel when none is named, and that
    read never returns. The run ends at the READ bound (the move bound is left
    at its real 90 s) with an error that says the wheel is stalled and the
    bound it missed, and nothing was moved or written."""
    monkeypatch.setattr(hub_module, "STATUS_DEVICE_READ_TIMEOUT_S", BOUND_S,
                        raising=False)
    offsets_rig["stall_read"] = True
    fw = offsets_rig["fw"]
    began = time.monotonic()
    with pytest.raises(DeviceError) as caught:
        await _within(sim_hub.learn_filter_offsets(exposure_s=0.01),
                      "the offset run")
    waited = time.monotonic() - began
    assert BOUND_S * 0.5 < waited < BOUND_S * 8, (
        f"the run gave up after {waited:.2f} s; the bound is {BOUND_S} s")
    text = str(caught.value)
    assert "stalled" in text and "position read" in text, text
    assert f"{BOUND_S:g} s" in text, text
    assert offsets_rig["moves"] == []
    assert list(fw.filter_offsets) == [0, 77, 0]


@pytest.mark.parametrize("slot,label", [
    pytest.param(0, "L", id="reference"),
    pytest.param(2, "OIII", id="later-slot"),
])
async def test_a_wheel_whose_move_never_finishes_does_not_hold_the_offset_run(
        sim_hub, offsets_rig, monkeypatch, slot, label):
    """A move to a slot never finishes (a wheel jammed between slots). The run
    ends at the MOVE bound with an error that names the wheel as stalled, the
    slot and the bound, publishes its failed state with the same text, and
    writes no offsets: a half-measured wheel keeps the ones it had."""
    monkeypatch.setattr(hub_module, "SOLVE_WHEEL_MOVE_TIMEOUT_S", BOUND_S,
                        raising=False)
    offsets_rig["stall_move_to"] = slot
    fw = offsets_rig["fw"]
    began = time.monotonic()
    with pytest.raises(DeviceError) as caught:
        await _within(sim_hub.learn_filter_offsets(ref_slot=0, exposure_s=0.01),
                      "the offset run")
    waited = time.monotonic() - began
    assert BOUND_S * 0.5 < waited < BOUND_S * 8, (
        f"the run gave up after {waited:.2f} s; the bound is {BOUND_S} s")
    text = str(caught.value)
    assert "stalled" in text and f"move to {label}" in text, text
    assert f"{BOUND_S:g} s" in text, text
    published = events_mod.bus.operation_snapshots["filter_offsets"]
    assert published["state"] == "failed", published
    assert "stalled" in published["error"], published
    assert list(fw.filter_offsets) == [0, 77, 0]
    assert offsets_rig["moves"][-1] == slot, offsets_rig["moves"]


async def test_a_stop_still_cancels_the_offset_runs_wheel_await(
        sim_hub, offsets_rig, monkeypatch):
    """CONTROL. A cancel that lands while the run waits on the wheel is the
    caller's Stop and reaches it."""
    monkeypatch.setattr(hub_module, "SOLVE_WHEEL_MOVE_TIMEOUT_S", 30.0)
    offsets_rig["stall_move_to"] = 0
    task = asyncio.ensure_future(
        sim_hub.learn_filter_offsets(ref_slot=0, exposure_s=0.01))
    await asyncio.sleep(0.2)
    assert not task.done(), "premise: the run is waiting on the wheel"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5.0)
