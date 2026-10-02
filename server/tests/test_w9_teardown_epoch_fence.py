# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#281 (WP-66): a teardown epoch fence for ``warm_camera`` and ``cool_camera``.

THE RACE (found reading the #267 fix, never seen on a rig). Both methods take
``_warm_lock`` and then await something bounded while holding it: ``warm_camera``
awaits its own sensor/ambient reads, ``cool_camera`` awaits ``cancel_warm``,
which can itself be waiting out a dying ramp's reap. A teardown's own
``cancel_warm(finalize=True)`` needs the very same lock, so a teardown that
starts during either window blocks on it -- and a teardown CANCELLED while it
blocks there still runs its cleanup (#267's own fix: the cleanup is in a
``finally``), disconnecting every device, because it is the only thing left
that can. The warm or cool call then resumes holding a ``cam`` reference the
cleanup has already cut loose, and either creates a ramp against it
(``warm_camera``) or sends it a setpoint (``cool_camera``).

THE FIX. ``Hub._teardown`` bumps a new ``_teardown_epoch`` counter as its
first action -- before it ever touches ``_warm_lock`` -- so the bump lands
regardless of how far the teardown gets. ``warm_camera`` and ``cool_camera``
read the epoch before their own first await on the lock and compare it again
after every await that follows (the lock wait itself, each read); a mismatch
means a teardown has begun, and the call abandons rather than touching the
camera further. Same shape as the ``_motion_epoch`` fence in this file and
the idle-stop fence (#270, ``sequence/engine.py``'s ``_idle_stop_epoch``).

Every mutation below is named, run from a byte backup of ``hub.py`` in this
worktree (never the shared tree, #254), with the observed failure quoted
verbatim in the test it was shown red under.
"""
from __future__ import annotations

import asyncio

import pytest

from astrodeck.config import config_store
from astrodeck.devices.base import Camera, DeviceError
from astrodeck.hub import Hub

#: Every bound below is well inside this; a hang reads as a clear timeout
#: rather than the suite's own default.
_BOUND_S = 3.0
#: How long the dying ramp in the cool_camera test takes to die once
#: cancelled -- the same 0.2 s test_teardown_cancel_cleanup.py uses, long
#: enough that a concurrent teardown reliably lands while the reap is open.
_DYING_S = 0.2


@pytest.fixture(autouse=True)
def _a_store_of_its_own(monkeypatch, tmp_path):
    """Same isolation as test_cooler_warm_ramp.py's fixture of the same name.
    Without it a successful ``cool_camera`` here would write a standing
    setpoint into the process-wide config store shared by the whole worker
    (#227) -- and this file deliberately drives ``cool_camera`` right up to
    (but never across) that write."""
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)


class _StallingCamera(Camera):
    """A coolable camera whose sensor read blocks until released, so a test
    can park ``warm_camera`` mid-read -- holding ``_warm_lock`` across a
    bounded await, exactly where #281 was found. ``get_ambient_temperature``
    is left at ``Camera``'s inert default (returns None, no await needed to
    block on), so the one controllable stall in this double is the sensor
    read the race under test actually needs."""

    def __init__(self) -> None:
        super().__init__("Stalling Cam")
        self.connected = True
        self.can_cool = True
        self.calls: list[tuple[bool, float | None]] = []
        self.temp_read_started = asyncio.Event()
        self.temp_release = asyncio.Event()
        self._temp_c = -10.0

    async def connect(self) -> None:               # pragma: no cover - unused
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def expose(self, *args, **kwargs):        # pragma: no cover - unused
        raise DeviceError("not used")

    async def abort_exposure(self) -> None:         # pragma: no cover - unused
        return None

    async def set_cooler(self, on, target_c=None) -> None:
        self.calls.append((on, target_c))

    async def get_temperature(self) -> float | None:
        self.temp_read_started.set()
        await self.temp_release.wait()
        return self._temp_c


class _DyingRamp:
    """Stands in for the task ``_warm_task`` holds while a ramp is in flight,
    the same shape test_teardown_cancel_cleanup.py's ``rig`` fixture uses:
    cancelling it does not finish at once, so ``cancel_warm``'s reap
    (``cool_camera``'s path into #281) has somewhere to wait. ``dying`` is
    set the instant the cancel is actually delivered, so a test can tell the
    reap has started rather than merely been asked for."""

    def __init__(self, hub: Hub, dying_s: float) -> None:
        self.dying = asyncio.Event()
        self.task = asyncio.create_task(self._run(hub, dying_s))

    async def _run(self, hub: Hub, dying_s: float) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.dying.set()
            await asyncio.sleep(dying_s)
            raise
        finally:
            if hub._warm_state is not None:
                hub._warm_state["active"] = False
                hub._warm_state["note"] = "warm complete"


async def _settle(task: asyncio.Task) -> None:
    """Drain a task the test is abandoning, so a mutant that leaves a ramp or
    a teardown running never leaks a pending task into the next test on this
    worker."""
    if not task.done():
        task.cancel()
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=_BOUND_S)
    except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
        pass


# --------------------------------------------------------- warm_camera (#281)


async def test_warm_camera_creates_no_ramp_on_a_camera_the_teardown_disconnects(
        bus_lines):
    """``warm_camera`` is parked inside its bounded sensor read, holding
    ``_warm_lock``. A teardown starts -- bumping ``_teardown_epoch`` as its
    first, lock-free action -- then blocks taking that same lock via its own
    ``cancel_warm(finalize=True)``. Cancelling the teardown there (a shutdown
    racing it, #281's own scenario) still runs its cleanup, because the
    cleanup is in a ``finally`` (#267): the camera is disconnected with no
    ramp ever having existed for the teardown to have owed a cooler-off for.
    Only then is the stalled sensor read released. ``warm_camera`` must come
    back inactive, having created no ramp and sent no command to the camera
    the teardown had already let go of.

    MUTANT (``_teardown_committed_clean`` always says clean -- its
    ``return self._teardown_epoch == fence`` replaced by ``return True``,
    which disables every one of ``warm_camera``'s three checkpoints and
    ``cool_camera``'s one at once) -- RED, observed verbatim:

        AssertionError: a ramp was created on a camera the teardown had
        disconnected; _warm_task=<Task cancelled name='Task-6'
        coro=<Hub._warm_ramp() done, defined at .../hub.py:5461>>
    """
    hub = Hub()
    cam = _StallingCamera()
    hub.devices["camera"] = cam

    warm_task = asyncio.create_task(hub.warm_camera())
    try:
        await asyncio.wait_for(cam.temp_read_started.wait(), timeout=_BOUND_S)
        assert hub._warm_lock.locked(), (
            "warm_camera must hold _warm_lock across the sensor read, or "
            "this test is not forcing the #281 window at all")

        teardown_task = asyncio.create_task(hub._teardown())
        # Let the teardown run its synchronous prefix (the epoch bump) and
        # reach the point where it blocks acquiring _warm_lock.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert hub._teardown_epoch == 1, (
            "the teardown must bump the fence before it can block on the "
            "lock warm_camera holds, or the race this test forces never "
            "happens")
        assert not teardown_task.done(), (
            "the teardown ended before it could have blocked on the lock "
            "warm_camera holds")

        teardown_task.cancel()
        await asyncio.wait({teardown_task}, timeout=_BOUND_S)
        assert teardown_task.cancelled(), (
            "the teardown's own cancel must still reach the caller (#235)")
        assert cam.connected is False, (
            "the teardown's cleanup must still disconnect the camera even "
            "when cancelled waiting for the warm lock (#267)")

        # Only now does the stalled sensor read return -- to a camera that
        # is, by this point, already gone.
        cam.temp_release.set()
        state = await asyncio.wait_for(warm_task, timeout=_BOUND_S)
    finally:
        await _settle(warm_task)
        extra = hub._warm_task
        if extra is not None:
            await _settle(extra)

    assert hub._warm_task is None, (
        f"a ramp was created on a camera the teardown had disconnected; "
        f"_warm_task={hub._warm_task!r}")
    assert state["active"] is False, state
    assert "teardown" in (state.get("note") or ""), state
    assert cam.calls == [], (
        f"a device command reached the camera after its disconnect: "
        f"{cam.calls}")
    said = [m for (_lvl, m, src) in bus_lines
            if src == "camera" and "warm_camera" in m and "teardown" in m]
    assert said, bus_lines


async def test_warm_camera_runs_the_ramp_normally_with_no_teardown_in_the_way(
        bus_lines):
    """CONTROL: nothing races this warm. It must still create a ramp and
    return an active state -- so the fence above is a narrow check, not a
    blanket refusal.

    MUTANT (the fence captured AFTER the lock instead of before --
    ``fence = self._teardown_epoch`` moved from before ``async with
    self._warm_lock:`` to the line right after it) would still pass this
    control, which is why the race tests above, not this one, are what pin
    the fence's placement; this test exists so a future change that makes
    the fence fire unconditionally is caught here first.
    """
    hub = Hub()
    cam = _StallingCamera()
    hub.devices["camera"] = cam
    warm_task = asyncio.create_task(hub.warm_camera())
    try:
        await asyncio.wait_for(cam.temp_read_started.wait(), timeout=_BOUND_S)
        cam.temp_release.set()
        state = await asyncio.wait_for(warm_task, timeout=_BOUND_S)
    finally:
        await _settle(warm_task)
    assert state["active"] is True, state
    assert hub._warm_task is not None
    await _settle(hub._warm_task)


# --------------------------------------------------------- cool_camera (#281)


async def test_cool_camera_sends_no_setpoint_to_a_camera_the_teardown_disconnects(
        bus_lines):
    """``cool_camera`` is inside ``cancel_warm``'s reap, waiting for a dying
    ramp to finish -- the exact window #281's report names for this half of
    the fix: ``_cancel_warm_locked`` has already cleared ``_warm_task`` to
    None, so a teardown racing in here owes no cooler-off of its own and
    believes there is nothing left to do. A teardown starts (bumping the
    fence first, as above) and blocks on the very same ``_warm_lock`` trying
    to send its own finalizing cooler-off. Cancelling the teardown there
    still disconnects the camera (#267's cleanup, in its ``finally``). Once
    the ramp then actually dies and ``cancel_warm`` returns, ``cool_camera``
    must refuse rather than command the camera the teardown has already let
    go of.

    MUTANT (``_teardown_committed_clean`` always says clean -- its
    ``return self._teardown_epoch == fence`` replaced by ``return True``,
    the same single-line mutant the warm_camera test above is shown red
    under) -- RED, observed verbatim:

        Failed: cool_camera sent [(True, -10.0)] to a camera the teardown
        had disconnected, instead of refusing
    """
    hub = Hub()
    cam = _StallingCamera()
    hub.devices["camera"] = cam
    hub._warm_state = {"active": True, "note": "warming"}
    ramp = _DyingRamp(hub, _DYING_S)
    hub._warm_task = ramp.task

    cool_task = asyncio.create_task(hub.cool_camera(-10.0))
    try:
        await asyncio.wait_for(ramp.dying.wait(), timeout=_BOUND_S)
        await asyncio.sleep(0)
        assert hub._warm_lock.locked(), (
            "cool_camera must hold _warm_lock while it waits out the dying "
            "ramp, or this test is not forcing the #281 window at all")
        assert hub._warm_task is None, (
            "_cancel_warm_locked must clear _warm_task before the ramp "
            "finishes dying, or the teardown below would (correctly) owe "
            "its own cooler-off and this test would be pinning #267, not "
            "#281")

        teardown_task = asyncio.create_task(hub._teardown())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert hub._teardown_epoch == 1, (
            "the teardown must bump the fence before it can block on the "
            "lock cool_camera holds, or the race this test forces never "
            "happens")
        assert not teardown_task.done(), (
            "the teardown ended before it could have blocked on the lock "
            "cool_camera holds")

        teardown_task.cancel()
        await asyncio.wait({teardown_task}, timeout=_BOUND_S)
        assert teardown_task.cancelled()
        assert cam.connected is False, (
            "the teardown's cleanup must still disconnect the camera even "
            "when cancelled waiting for the warm lock (#267)")

        try:
            await asyncio.wait_for(cool_task, timeout=_BOUND_S)
        except DeviceError:
            pass
        else:
            pytest.fail(
                f"cool_camera sent {cam.calls} to a camera the teardown had "
                f"disconnected, instead of refusing")
    finally:
        await _settle(cool_task)

    assert cam.calls == [], (
        f"a device command reached the camera after its disconnect: "
        f"{cam.calls}")
    assert config_store.cfg().cooling.setpoint_c is None, (
        "a setpoint was recorded for a camera the cooling command never "
        "actually reached")
    said = [m for (_lvl, m, src) in bus_lines
            if src == "camera" and "cool_camera" in m and "teardown" in m]
    assert said, bus_lines


async def test_cool_camera_cools_normally_with_no_teardown_in_the_way():
    """CONTROL: nothing races this cool. It must still reach the camera and
    record the standing setpoint."""
    hub = Hub()
    cam = _StallingCamera()
    hub.devices["camera"] = cam
    await hub.cool_camera(-12.0)
    assert cam.calls == [(True, -12.0)]
    assert config_store.cfg().cooling.setpoint_c == -12.0
