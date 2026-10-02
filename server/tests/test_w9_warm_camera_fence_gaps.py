# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#281 class, WP-66 follow-on: two more one-shot device commands inside
``warm_camera`` ran with no teardown-epoch recheck immediately before them.

WP-66 (``test_w9_teardown_epoch_fence.py``) fenced the bounded sensor and
ambient reads and the lock wait. It missed two commands that can still run
after the fence's own last checkpoint but before this file's additions:

1. THE EARLY EXIT TO ``_warm_now`` (``cam.set_cooler(False)``) on the
   "ramp disabled" path (``not ramp``, with an existing ramp this call must
   first cancel -- ``await self._cancel_warm_locked(...)`` is itself a
   bounded await a teardown can land behind, the same shape ``cool_camera``'s
   own fenced gap already proved) and on the "sensor already at/above
   ambient" path (no awaited gap of its own, fenced anyway so the invariant
   -- every one-shot command is rechecked immediately before it is sent --
   does not depend on no future edit ever inserting one).
2. THE DELEGATED-BACKEND BRANCH (``self_warms=True``, e.g. the NINA bridge):
   its own ``await warm_fn(minutes)`` / ``await cam.set_cooler(False)``, and
   ``self._warm_task = asyncio.create_task(...)`` right after it returns --
   a genuine awaited gap, since the delegated command itself is awaited.

Each test below drives ``warm_camera`` to within one line of its own new
checkpoint and shows the epoch moving in that exact gap stops the command (or
the ramp task) rather than merely being caught by an earlier, already-fenced
checkpoint. Every mutation named below was applied to a byte backup of
``hub.py`` in this worktree only (never the shared tree, #254), the quoted
failure observed, then the backup restored and SHA-256 compared.
"""
from __future__ import annotations

import asyncio

import pytest

from astrodeck import cooling
from astrodeck.config import config_store
from astrodeck.hub import Hub
from test_w9_teardown_epoch_fence import (_BOUND_S, _DYING_S, _DyingRamp,
                                          _settle, _StallingCamera)


@pytest.fixture(autouse=True)
def _a_store_of_its_own(monkeypatch, tmp_path):
    """Same isolation as ``test_w9_teardown_epoch_fence.py``'s fixture of the
    same name: ``warm_camera`` reads ``config_store.cfg()`` (ramp-enabled,
    ambient, rate), and this file's tests must not depend on -- or leave
    behind -- whatever another test left in the process-wide store."""
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)


class _DelegatedCamera(_StallingCamera):
    """A ``self_warms`` backend (the NINA bridge's shape): its own ``warm``
    coroutine is awaited instead of ``set_cooler``. ``_on_warm``, when set,
    is awaited from inside ``warm()`` -- it lets a test act (e.g. move the
    teardown epoch) exactly while that await is in flight, standing in for
    whatever a concurrent teardown would have finished doing by the time it
    returns. Left unset (the default), ``warm()`` resolves immediately."""

    def __init__(self) -> None:
        super().__init__()
        self.self_warms = True
        self.warm_calls: list[int] = []
        self.warm_started = asyncio.Event()
        self._on_warm = None

    async def warm(self, minutes: int) -> None:
        self.warm_calls.append(minutes)
        self.warm_started.set()
        if self._on_warm is not None:
            await self._on_warm()


async def test_ramp_disabled_sends_no_cooler_off_to_a_camera_the_teardown_disconnects(
        bus_lines):
    """``ramp=False`` with an existing ramp first cancels it
    (``_cancel_warm_locked``), which awaits a dying task's reap -- the exact
    bounded-await-while-holding-``_warm_lock`` shape #281 was found in, just
    reached through the OTHER early-exit than WP-66's own tests drive. A
    teardown that starts during that reap and is then cancelled still
    disconnects the camera (#267); the "ramp disabled" exit's own
    ``cam.set_cooler(False)`` must not still run once the reap resolves.

    MUTANT (the recheck before this exit's ``_warm_now`` call removed) --
    RED, observed verbatim:

        AssertionError: assert 'teardown' in (("at the caller's request"))
         +  where "at the caller's request" = <built-in method get of dict
         object at 0x...>('note')

    (the camera's own ``cam.calls`` also carried the ``set_cooler`` call the
    recheck exists to stop).
    """
    hub = Hub()
    cam = _StallingCamera()
    hub.devices["camera"] = cam
    hub._warm_state = {"active": True, "note": "warming"}
    ramp = _DyingRamp(hub, _DYING_S)
    hub._warm_task = ramp.task

    warm_task = asyncio.create_task(hub.warm_camera(ramp=False))
    try:
        await asyncio.wait_for(ramp.dying.wait(), timeout=_BOUND_S)
        await asyncio.sleep(0)
        assert hub._warm_lock.locked(), (
            "the ramp=False exit must hold _warm_lock across "
            "_cancel_warm_locked's reap, or this test is not forcing the "
            "window at all")
        assert hub._warm_task is None, (
            "_cancel_warm_locked must clear _warm_task before the ramp "
            "finishes dying")

        teardown_task = asyncio.create_task(hub._teardown())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert hub._teardown_epoch == 1, (
            "the teardown must bump the fence before it can block on the "
            "lock this warm holds, or the race this test forces never "
            "happens")
        assert not teardown_task.done()

        teardown_task.cancel()
        await asyncio.wait({teardown_task}, timeout=_BOUND_S)
        assert teardown_task.cancelled()
        assert cam.connected is False, (
            "the teardown's cleanup must still disconnect the camera even "
            "when cancelled waiting for the warm lock (#267)")

        state = await asyncio.wait_for(warm_task, timeout=_BOUND_S)
    finally:
        await _settle(warm_task)
        extra = hub._warm_task
        if extra is not None:
            await _settle(extra)

    assert state["active"] is False, state
    assert "teardown" in (state.get("note") or ""), state
    assert cam.calls == [], (
        f"a device command reached the camera after its disconnect: "
        f"{cam.calls}")


async def test_pointless_sends_no_cooler_off_when_the_epoch_moves_first(
        monkeypatch):
    """"Already at/above ambient" has no awaited gap of its own between the
    last read's recheck and this exit, so a real concurrent teardown cannot
    land there today -- the recheck exists so that stays true after a future
    edit, not because of a race reachable right now. Proven by moving the
    epoch from inside ``cooling.warm_is_pointless`` itself, the last call
    before the exit, which stands in for "whatever lands in this gap".

    MUTANT (the recheck before this exit's ``_warm_now`` call removed) --
    RED, observed verbatim:

        AssertionError: assert 'teardown' in (('the sensor is already at
        25.0 C, at or above ambient - nothing to ramp'))

    (``cam.calls`` also carried the ``set_cooler`` call the recheck exists
    to stop).
    """
    hub = Hub()
    cam = _StallingCamera()
    hub.devices["camera"] = cam
    real_pointless = cooling.warm_is_pointless

    def pointless_once_the_epoch_has_moved(start_c, ambient_c):
        hub._teardown_epoch += 1
        return real_pointless(start_c, ambient_c)

    monkeypatch.setattr(cooling, "warm_is_pointless",
                        pointless_once_the_epoch_has_moved)
    cam._temp_c = 25.0  # already above the 20 C fallback ambient: pointless
    warm_task = asyncio.create_task(hub.warm_camera())
    await asyncio.wait_for(cam.temp_read_started.wait(), timeout=_BOUND_S)
    cam.temp_release.set()
    state = await asyncio.wait_for(warm_task, timeout=_BOUND_S)

    assert state["active"] is False, state
    assert "teardown" in (state.get("note") or ""), state
    assert cam.calls == [], (
        f"a device command reached the camera after the epoch moved: "
        f"{cam.calls}")
    assert hub._warm_task is None


async def test_delegated_send_is_skipped_when_the_epoch_moves_first(
        monkeypatch):
    """The delegated branch's own ``warm_fn``/``set_cooler`` has no awaited
    gap of its own either (same reasoning as the pointless test above):
    proven by moving the epoch from inside ``cooling.warm_minutes``, the
    last call before ``warm_fn`` is read and awaited.

    MUTANT (the recheck before the delegated send removed) -- RED, observed
    verbatim (the downstream create_task recheck still catches the stale
    epoch afterward, so ``state["note"]`` still says "teardown" and no ramp
    task is created; it is the command having reached the backend at all
    that this recheck exists to prevent):

        AssertionError: the backend's warm() was called after the epoch
        moved: [15]
        assert [15] == []
    """
    hub = Hub()
    cam = _DelegatedCamera()
    hub.devices["camera"] = cam
    real_minutes = cooling.warm_minutes

    def minutes_once_the_epoch_has_moved(start_c, ambient_c, rate):
        hub._teardown_epoch += 1
        return real_minutes(start_c, ambient_c, rate)

    monkeypatch.setattr(cooling, "warm_minutes", minutes_once_the_epoch_has_moved)
    warm_task = asyncio.create_task(hub.warm_camera())
    await asyncio.wait_for(cam.temp_read_started.wait(), timeout=_BOUND_S)
    cam.temp_release.set()
    state = await asyncio.wait_for(warm_task, timeout=_BOUND_S)

    assert state["active"] is False, state
    assert "teardown" in (state.get("note") or ""), state
    assert cam.warm_calls == [], (
        f"the backend's warm() was called after the epoch moved: "
        f"{cam.warm_calls}")
    assert cam.calls == []
    assert hub._warm_task is None


async def test_no_ramp_task_is_created_when_the_epoch_moves_during_the_delegated_send(
        bus_lines):
    """The delegated branch's send IS awaited, so a teardown genuinely can
    land while it is in flight -- here the backend's own ``warm()`` bumps
    the epoch before returning, standing in for a concurrent teardown
    finishing during that await. The command the backend already accepted
    cannot be unsent, but no ramp TASK may be created to track it once the
    camera is gone.

    MUTANT (the recheck before ``self._warm_task = asyncio.create_task(...)``
    removed) -- RED, observed verbatim:

        AssertionError: a ramp task was created after the epoch moved while
        sending the delegated warm
        assert <Task cancelled name='Task-3' coro=<Hub._warm_ramp() ...>>
        is None
    """
    hub = Hub()
    cam = _DelegatedCamera()
    hub.devices["camera"] = cam

    async def _bump_epoch_mid_send() -> None:
        hub._teardown_epoch += 1

    cam._on_warm = _bump_epoch_mid_send

    warm_task = asyncio.create_task(hub.warm_camera())
    try:
        await asyncio.wait_for(cam.temp_read_started.wait(), timeout=_BOUND_S)
        cam.temp_release.set()
        state = await asyncio.wait_for(warm_task, timeout=_BOUND_S)
    finally:
        await _settle(warm_task)
        extra = hub._warm_task
        if extra is not None:
            await _settle(extra)

    assert cam.warm_calls, "premise: the backend's warm() was actually sent"
    assert hub._warm_task is None, (
        "a ramp task was created after the epoch moved while sending the "
        "delegated warm")
    assert state["active"] is False, state
    assert "teardown" in (state.get("note") or ""), state


async def test_warm_camera_still_creates_a_ramp_normally_with_no_teardown_in_the_way():
    """CONTROL: nothing races any of the three new checkpoints above. A
    normal delegated warm still creates a ramp and returns active, so none
    of the added rechecks fire unconditionally."""
    hub = Hub()
    cam = _DelegatedCamera()
    hub.devices["camera"] = cam
    warm_task = asyncio.create_task(hub.warm_camera())
    try:
        await asyncio.wait_for(cam.temp_read_started.wait(), timeout=_BOUND_S)
        cam.temp_release.set()
        state = await asyncio.wait_for(warm_task, timeout=_BOUND_S)
    finally:
        await _settle(warm_task)
    assert state["active"] is True, state
    assert cam.warm_calls, "the delegated backend must have been asked to warm"
    assert hub._warm_task is not None
    await _settle(hub._warm_task)
