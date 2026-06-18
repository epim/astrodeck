"""W3.7 MOTION SERIALIZATION invariant (owner-pinned).

The single mount has ONE motion authority. These tests prove the two halves of
the invariant added to the hub:

  * ``_motion_lock`` SERIALIZES the device-touching section of every motion path
    (slew/park/jog/center step) so two commits can never interleave on the wire;
  * ``_motion_epoch`` FENCES a stale slew: a STOP/abort/park/deadman bumps the
    epoch FIRST, so a motion path that was accepted just before the abort (and is
    now awaiting the lock, or sitting in its setup awaits) re-checks the epoch at
    the pre-dispatch point and ABANDONS -- the device ``slew()`` is never called.

The motivating race (owner words): "a stale REMOTE slew accepted just before a
LOCAL abort is fenced out." A remote admin's goto and a local STOP can arrive in
either order; if the STOP wins (even by a hair, and even if the goto task was
already past its cancellation point), the mount must NOT slew afterward.
"""
from __future__ import annotations

import asyncio

import pytest

from astrodeck.devices.base import Telescope
from astrodeck.hub import Hub


class _GatedTelescope(Telescope):
    """A mount that records every ``slew`` and lets a test GATE the slew await so
    it can land an abort while a goto is mid-flight."""

    def __init__(self) -> None:
        super().__init__("Gated Mount")
        self.connected = True
        self.slews: list[tuple[float, float]] = []
        self.stops = 0
        self.slew_gate = asyncio.Event()   # set() to release a blocked slew
        self.slew_entered = asyncio.Event()  # set when slew() begins
        self.block_slew = False

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def get_position(self):
        return 0.0, 0.0

    async def slew(self, ra_hours, dec_deg):
        self.slew_entered.set()
        if self.block_slew:
            await self.slew_gate.wait()
        self.slews.append((ra_hours, dec_deg))

    async def sync(self, ra_hours, dec_deg):  # pragma: no cover
        pass

    async def set_tracking(self, on):
        pass

    async def get_tracking(self):  # pragma: no cover
        return True

    async def park(self):
        pass

    async def unpark(self):
        pass

    async def is_parked(self):
        return False

    async def is_slewing(self):  # pragma: no cover
        return False

    async def move_axis(self, axis, rate):
        pass

    async def stop(self) -> None:
        self.stops += 1


def _hub_with_mount():
    hub = Hub()
    tel = _GatedTelescope()
    hub.devices["telescope"] = tel
    # Disable the sun gate for these pure-motion tests (no site configured => the
    # default-site solar check is inert, but be explicit).
    hub._check_solar = lambda *a, **k: None
    return hub, tel


@pytest.mark.asyncio
async def test_stale_slew_fenced_after_local_abort():
    """The core race: a goto is accepted and is awaiting the motion lock when a
    LOCAL abort bumps the epoch. When the goto finally acquires the lock it sees
    the advanced epoch and ABANDONS -- the device ``slew()`` is never dispatched."""
    hub, tel = _hub_with_mount()

    # Simulate an in-flight motion already holding the lock (e.g. a prior slew
    # the abort is about to stop). The new goto will queue behind it.
    await hub._motion_lock.acquire()
    try:
        goto = asyncio.create_task(hub.goto_and_center(10.0, 20.0, max_attempts=1))
        await asyncio.sleep(0.02)  # let goto reach the lock acquire and block
        assert not tel.slews, "slew must not have run while lock is held"
        # LOCAL abort lands NOW: bump the fence FIRST (exactly what /api/mount/stop
        # and the deadman do), then we release the lock.
        hub.bump_motion_epoch()
    finally:
        hub._motion_lock.release()

    result = await asyncio.wait_for(goto, timeout=5.0)
    # The stale goto saw the advanced epoch and abandoned: NO slew reached the mount.
    assert tel.slews == [], f"stale slew should be fenced, got {tel.slews}"
    assert result.get("aborted") is True


@pytest.mark.asyncio
async def test_clean_goto_still_slews_when_not_aborted():
    """Control: with NO abort, the same goto path DOES dispatch the slew (the
    fence only blocks when the epoch advanced)."""
    hub, tel = _hub_with_mount()
    # max_attempts=1 + no camera => solve_and_sync raises and degrades to raw GoTo
    # AFTER the slew, so the slew itself is what we assert.
    result = await asyncio.wait_for(
        hub.goto_and_center(5.0, 5.0, max_attempts=1), timeout=5.0)
    assert tel.slews == [(5.0, 5.0)], "clean goto must slew exactly once"
    assert result.get("aborted") is not True


@pytest.mark.asyncio
async def test_motion_lock_serializes_two_gotos():
    """Two concurrent gotos cannot interleave their device-touching sections: the
    motion lock forces them strictly one-after-another (no overlapping slew)."""
    hub, tel = _hub_with_mount()
    tel.block_slew = True

    g1 = asyncio.create_task(hub.goto_and_center(1.0, 1.0, max_attempts=1))
    await asyncio.wait_for(tel.slew_entered.wait(), timeout=5.0)
    # g1 is now INSIDE slew() holding the motion lock. Start g2.
    tel.slew_entered = asyncio.Event()  # reset for g2
    g2 = asyncio.create_task(hub.goto_and_center(2.0, 2.0, max_attempts=1))
    await asyncio.sleep(0.05)
    # g2 must be blocked on the lock -> its slew has NOT entered yet.
    assert not tel.slew_entered.is_set(), "g2 slew ran while g1 held the lock"
    # Release g1's slew; both complete; g1 ran strictly before g2.
    tel.slew_gate.set()
    await asyncio.wait_for(asyncio.gather(g1, g2), timeout=5.0)
    assert tel.slews[0] == (1.0, 1.0)
    assert (2.0, 2.0) in tel.slews


@pytest.mark.asyncio
async def test_bump_epoch_is_monotonic():
    """Every abort advances the fence monotonically (so a re-check is reliable)."""
    hub, _tel = _hub_with_mount()
    e0 = hub._motion_epoch
    e1 = hub.bump_motion_epoch()
    e2 = hub.bump_motion_epoch()
    assert e1 == e0 + 1 and e2 == e1 + 1
    assert not hub._motion_committed_clean(e0)
    assert hub._motion_committed_clean(e2)
