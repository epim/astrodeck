"""A1 (final-branch-review I2): the native guider absorbs a transient
guide-camera exposure fault via bounded retry+backoff, and dies loudly (honest
death: _lost, not guiding) on a persistent fault. Drives the sim's
guide_expose_fail_next_n knob; needs no native wheel (NativeGuider._expose /
_guide_loop / _calibrate are pure Python over the sim camera).

#24-adjacent, #28: "a transient guide-camera exposure fault" is a claim about
FAULTS, and the envelope only ever caught ``DeviceError``. The adapters under it
raise more than that — alpaca.py catches ``(DeviceError, httpx.HTTPError,
OSError)`` in ten places, a Windows serial read raises ``OSError``, a bad packet
raises ``struct.error`` — and every one of those went STRAIGHT past the retry
and killed the guide loop through its defensive handler (no ``_lost``, so the
sequence engine's recovery never even saw a star loss). The suite could not
catch it because the sim's injector raised the one type that WAS handled; it now
takes a settable exception type (``guide_expose_fail_exc``), and the tests at the
bottom of this file inject non-``DeviceError`` faults down each of the three
paths.
"""
import asyncio
import time

import httpx
import pytest

import astrodeck.guide.native as nativemod
from astrodeck.devices.base import CameraFrame, DeviceError
from astrodeck.devices.sim import build_sim_rig
from astrodeck.guide.native import NativeGuider

# Fast/slow lane (pyproject markers): this whole module runs the guide loop at
# REAL dwell on purpose (see ``_real_dwell`` below), so it is wall-clock-bound
# by construction -- `pytest -m 'not slow'` skips it for the inner loop, while
# every gate and CI still run the FULL suite unfiltered.
pytestmark = pytest.mark.slow


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


# ------------------------------- the fault does not have to be a DeviceError
#
# #28. Every test above injects DeviceError, which is what the envelope already
# caught, so the whole module passed against a retry that covered one exception
# type out of the family the adapters actually leak. httpx.ReadTimeout is the
# concrete one: an Alpaca guide camera on a busy ASCOM Remote times out mid-poll,
# and alpaca.py's own `except (DeviceError, httpx.HTTPError, OSError)` triples
# say plainly that it reaches callers.


@pytest.mark.asyncio
async def test_a_transport_fault_is_absorbed_like_a_deviceerror(monkeypatch):
    """(a) Two ``httpx.ReadTimeout``s then a frame: one ``_expose`` call, one
    frame out. Nothing about "transient exposure fault" was ever a statement
    about which Python class the driver chose to raise."""
    guider, cam, tel = _guider(monkeypatch)
    await cam.connect()
    cam.guide_expose_fail_exc = httpx.ReadTimeout
    cam.guide_expose_fail_next_n = 2
    frame = await guider._expose()
    assert frame is not None
    assert cam.guide_expose_fail_next_n == 0        # both absorbed


@pytest.mark.asyncio
async def test_a_transport_fault_exhausts_into_a_deviceerror(monkeypatch):
    """The exhausted case stays ``DeviceError`` whatever went in, because that
    is the single handled channel ``_guide_loop`` and ``_calibrate`` watch."""
    guider, cam, tel = _guider(monkeypatch)
    await cam.connect()
    cam.guide_expose_fail_exc = httpx.ReadTimeout
    cam.guide_expose_fail_next_n = 10_000
    with pytest.raises(DeviceError):
        await guider._expose()


@pytest.mark.asyncio
async def test_a_stop_mid_exposure_is_still_not_retried(monkeypatch):
    """The contract the broadening must not break. ``asyncio.CancelledError`` is
    a ``BaseException``, so widening the caught set cannot swallow it — asserted
    here rather than assumed, because the whole point of this fix is that a
    claim about which exceptions are handled had never been tested."""
    assert not issubclass(asyncio.CancelledError, Exception)
    guider, cam, tel = _guider(monkeypatch)
    await cam.connect()
    cam.guide_expose_fail_exc = asyncio.CancelledError
    cam.guide_expose_fail_next_n = 3
    with pytest.raises(asyncio.CancelledError):
        await guider._expose()
    assert cam.guide_expose_fail_next_n == 2, "exactly one attempt — no retry"


@pytest.mark.asyncio
async def test_a_driver_that_returns_no_frame_is_a_retryable_fault(monkeypatch):
    """(2) ``Camera.expose`` is annotated ``-> CameraFrame`` and the annotation
    is not enforced; an out-of-tree driver that returns ``None`` on failure made
    ``frame.data`` an ``AttributeError`` — unretried, and out through the loop's
    defensive handler. The hub added this guard on its own preview path; the
    guide loop had none."""
    guider, cam, tel = _guider(monkeypatch)
    await cam.connect()
    attempts = []

    async def _answers_nothing(*a, **k):
        attempts.append(1)
        return None

    monkeypatch.setattr(cam, "expose", _answers_nothing)
    with pytest.raises(DeviceError) as e:
        await guider._expose()
    assert "no frame" in str(e.value)
    assert len(attempts) == nativemod._EXPOSE_RETRIES + 1, "retried, not raised at once"


@pytest.mark.asyncio
async def test_one_missing_frame_is_absorbed_like_any_other_fault(monkeypatch):
    """...and a driver that returns None ONCE is a transient fault like any
    other, so a frame that arrives on the retry is served."""
    guider, cam, tel = _guider(monkeypatch)
    await cam.connect()
    real = cam.expose
    calls = []

    async def _first_one_is_nothing(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            return None
        return await real(*a, **k)

    monkeypatch.setattr(cam, "expose", _first_one_is_nothing)
    frame = await guider._expose()
    assert isinstance(frame, CameraFrame)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_transport_faults_feed_the_same_honest_death_budget(monkeypatch):
    """(b) The budget is about the CAMERA being wedged, not about the type it
    raises: five consecutive exhausted ``httpx.ReadTimeout`` frames kill the loop
    through the honest-death path (``_lost``), and four do not.

    ``_lost`` is the discriminator that matters. Before the fix the loop died on
    the FIRST transport fault through its defensive handler, which sets
    ``_active = False`` but leaves ``_lost`` False — so guiding stopped and the
    sequence engine's ``_maybe_recover_guiding`` was never told a star was
    lost."""
    per_frame = nativemod._EXPOSE_RETRIES + 1

    guider, cam, tel = _guider(monkeypatch)
    await cam.connect()
    guider._engine = _StubEngine()
    guider._active = True
    guider._stop.clear()
    cam.guide_expose_fail_exc = httpx.ReadTimeout
    cam.guide_expose_fail_next_n = 5 * per_frame
    await asyncio.wait_for(guider._guide_loop(), timeout=10.0)
    assert guider._lost is True
    assert guider._active is False
    assert cam.guide_expose_fail_next_n == 0, "died on the 5th exactly"

    # Four exhausted transport frames: still guiding, and the successes that
    # follow reset the counter (a night of transients never sums to a death).
    guider, cam, tel = _guider(monkeypatch)
    await cam.connect()
    guider._engine = _StubEngine()
    guider._active = True
    guider._stop.clear()
    cam.guide_expose_fail_exc = httpx.ReadTimeout
    cam.guide_expose_fail_next_n = 4 * per_frame
    task = asyncio.ensure_future(guider._guide_loop())
    seen = []

    def drained():
        seen.append(guider._fault_frames)
        return cam.guide_expose_fail_next_n == 0 or task.done()

    assert await _wait_until(drained)
    assert not task.done()
    assert max(seen) == 4, "four exhausted frames, counted as faults, no death"
    assert guider._lost is False
    after = guider._engine.frames
    assert await _wait_until(lambda: guider._engine.frames > after or task.done())
    assert not task.done()
    assert guider._fault_frames == 0
    guider._stop.set()
    await asyncio.wait_for(task, timeout=5.0)


@pytest.mark.asyncio
async def test_calibration_surfaces_a_transport_fault_as_a_deviceerror(monkeypatch):
    """(c) ``start_guiding`` -> ``_calibrate`` -> ``_expose``: the caller is
    written against one handled channel, so a raw ``httpx.ReadTimeout`` out of
    here is an unhandled exception in whatever asked for guiding."""
    guider, cam, tel = _guider(monkeypatch)
    await cam.connect()
    await tel.connect()
    cam.guide_expose_fail_exc = httpx.ReadTimeout
    cam.guide_expose_fail_next_n = 10_000
    with pytest.raises(DeviceError):
        await guider._calibrate()
