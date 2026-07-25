"""A1 (final-branch-review I2): the native guider absorbs a transient
guide-camera exposure fault via bounded retry+backoff, and dies loudly (honest
death: _lost, not guiding) on a persistent fault. Drives the sim's
guide_expose_fail_next_n knob; needs no native wheel (NativeGuider._expose /
_guide_loop / _calibrate are pure Python over the sim camera)."""
import asyncio
import time

import pytest

import astrodeck.guide.native as nativemod
from astrodeck.devices.base import DeviceError
from astrodeck.devices.sim import build_sim_rig
from astrodeck.guide.native import NativeGuider


@pytest.fixture(autouse=True)
def _real_dwell(_fast_sim_delays, monkeypatch):
    """This whole module drives ``_guide_loop`` in REAL TIME and asserts on the
    loop's live state (frames counted so far, task not yet finished), so it must
    opt OUT of the suite-wide ``ASTRODECK_FAST_TEST`` fast path (conftest's
    ``_fast_sim_delays``). The guide camera's exposure dwell is the loop's ONLY
    pacing — zeroing it makes the loop busy-spin thousands of iterations per
    second, which both burns the fault budget before the assertions run and
    pegs a core. Depends on ``_fast_sim_delays`` so the suite-wide setenv is
    guaranteed to run FIRST and this delenv then wins for the test."""
    monkeypatch.delenv("ASTRODECK_FAST_TEST", raising=False)
    yield


def _guider(monkeypatch):
    monkeypatch.setattr(nativemod, "_EXPOSE_BACKOFF_S", (0.0, 0.0, 0.0))
    rig = build_sim_rig()
    cam, tel = rig["guide_camera"], rig["telescope"]
    return NativeGuider(cam, tel, config={"exposure_s": 0.05}, profile_id=None), cam, tel


class _StubEngine:
    """Minimal engine stand-in so _guide_loop's host-side fault accounting can
    be driven past successful exposures without the native wheel: `process`
    counts frames and plays back scripted Actions (a genuine star loss is
    `{"action": "lock_lost", "reason": "star_lost"}`; script exhausted ->
    ordinary idle frames), `stats` reports a steady guiding phase."""

    def __init__(self, actions=()):
        self.frames = 0
        self._actions = list(actions)

    def process(self, data, ts, exposure_s):
        self.frames += 1
        if self._actions:
            return self._actions.pop(0)
        return {"action": "idle"}

    def stats(self):
        return {"guiding": True, "settling": False, "recent": []}


async def _wait_until(cond, timeout_s=15.0):
    """Poll `cond` (no real backoff sleeps are in play — _EXPOSE_BACKOFF_S is
    zeroed — so this only rides out 0.05 s sim exposures)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if cond():
            return True
        await asyncio.sleep(0.01)
    return False


@pytest.mark.asyncio
async def test_two_frame_fault_absorbed_by_retry(monkeypatch):
    guider, cam, tel = _guider(monkeypatch)
    await cam.connect()
    cam.guide_expose_fail_next_n = 2  # 2 faults then success -> absorbed in one _expose
    frame = await guider._expose()
    assert frame is not None
    assert cam.guide_expose_fail_next_n == 0  # fully consumed


@pytest.mark.asyncio
async def test_persistent_fault_exhausts_retries(monkeypatch):
    guider, cam, tel = _guider(monkeypatch)
    await cam.connect()
    cam.guide_expose_fail_next_n = 10_000  # every attempt faults
    with pytest.raises(DeviceError):
        await guider._expose()


@pytest.mark.asyncio
async def test_persistent_fault_honest_death(monkeypatch):
    guider, cam, tel = _guider(monkeypatch)
    await cam.connect()
    cam.guide_expose_fail_next_n = 10_000  # camera wedged: every exposure faults
    guider._active = True
    guider._stop.clear()
    await guider._guide_loop()  # returns when the fault budget trips honest death
    assert guider._lost is True
    assert guider._active is False
    assert guider.stats().guiding is False


@pytest.mark.asyncio
async def test_fault_frames_reset_on_success(monkeypatch):
    """Review fix round (a-t2-review Important): a below-budget fault burst, a
    good frame, then another below-budget burst must NOT accumulate toward the
    budget — the success resets _fault_frames, so transient faults spread
    across a night never sum to an honest death. Fails if the reset-on-success
    line in _guide_loop is removed (4 + 4 would then trip the budget of 5)."""
    per_frame = nativemod._EXPOSE_RETRIES + 1  # attempts one exhausted frame eats
    guider, cam, tel = _guider(monkeypatch)
    await cam.connect()
    engine = _StubEngine()
    guider._engine = engine
    guider._active = True
    guider._stop.clear()
    cam.guide_expose_fail_next_n = 4 * per_frame  # burst 1: 4 exhausted frames
    task = asyncio.ensure_future(guider._guide_loop())
    assert await _wait_until(lambda: engine.frames >= 1 or task.done())
    assert not task.done()  # burst 1 (below budget) absorbed; a frame succeeded
    cam.guide_expose_fail_next_n = 4 * per_frame  # burst 2: 4 more
    assert await _wait_until(
        lambda: cam.guide_expose_fail_next_n == 0 or task.done())
    assert not task.done()  # under the mutation, death hits mid-burst-2
    after = engine.frames
    assert await _wait_until(lambda: engine.frames > after or task.done())
    assert not task.done()  # a post-burst-2 success went through: still alive
    assert guider._lost is False
    assert guider._active is True
    assert guider.stats().guiding is True
    guider._stop.set()
    await asyncio.wait_for(task, timeout=5.0)


@pytest.mark.asyncio
async def test_fault_budget_is_five_and_independent_of_reacquire(monkeypatch):
    """Review fix round (a-t2-review Important): the device-fault budget is
    exactly _FAULT_FRAME_BUDGET = 5 consecutive exhausted exposures — the 5th
    kills the loop with zero injected faults left over, while 4 do not — and it
    is separate from the engine-side star-lost _REACQUIRE_BUDGET (8): 4
    exhausted frames followed by 7 genuine star-loss frames (11 bad frames
    total) leaves the loop alive, because neither budget alone is crossed and
    device faults never feed the reacquire counter. Fails if
    _FAULT_FRAME_BUDGET is changed (e.g. conflated with _REACQUIRE_BUDGET's
    8)."""
    per_frame = nativemod._EXPOSE_RETRIES + 1

    # Exactly 5 consecutive exhausted exposures -> device-fault honest death.
    guider, cam, tel = _guider(monkeypatch)
    await cam.connect()
    guider._engine = _StubEngine()
    guider._active = True
    guider._stop.clear()
    cam.guide_expose_fail_next_n = 5 * per_frame
    # A budget > 5 never dies here (exposures succeed once the injected faults
    # run out), so the loop would spin until this wait_for times the test out.
    await asyncio.wait_for(guider._guide_loop(), timeout=10.0)
    assert guider._lost is True
    assert guider._active is False
    assert cam.guide_expose_fail_next_n == 0  # died on the 5th exactly, not earlier

    # 4 exhausted frames + 7 genuine star losses: alive. Device faults counted
    # 4/5, star losses 7/8 — separate budgets, no cross-contamination.
    guider, cam, tel = _guider(monkeypatch)
    await cam.connect()
    engine = _StubEngine(
        actions=[{"action": "lock_lost", "reason": "star_lost"}] * 7)
    guider._engine = engine
    guider._active = True
    guider._stop.clear()
    cam.guide_expose_fail_next_n = 4 * per_frame
    task = asyncio.ensure_future(guider._guide_loop())
    assert await _wait_until(lambda: engine.frames >= 8 or task.done())
    assert not task.done()
    assert guider._lost is False
    assert guider._active is True
    assert guider._reacquire == 7    # fed only by the 7 genuine star losses
    assert guider._fault_frames == 0  # reset by the successful exposures
    guider._stop.set()
    await asyncio.wait_for(task, timeout=5.0)


@pytest.mark.asyncio
async def test_calibration_fault_aborts_with_deviceerror(monkeypatch):
    guider, cam, tel = _guider(monkeypatch)
    await cam.connect()
    await tel.connect()
    cam.guide_expose_fail_next_n = 10_000
    # _calibrate's first line is `await self._expose()`; an exhausted retry
    # raises DeviceError (a handled channel), never a raw camera exception —
    # so start_guiding's caller survives the calibration abort.
    with pytest.raises(DeviceError):
        await guider._calibrate()
