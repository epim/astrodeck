"""The simulator's mount slew and rotator move dwell are PACING, not physics
(#207).

``devices.sim._sim_delay`` is the one knob the suite's fast path
(``ASTRODECK_FAST_TEST=1``, set for every test by conftest's
``_fast_sim_delays``) turns to zero. Until #207 two sim sleeps did not go
through it: ``SimTelescope.slew``'s per-step dwell (4 deg/s, capped at 8 s)
and ``SimRotator.move_mechanical``'s (5 deg/s). Every sim-hub goto test paid
both in real time, while the fixture docstring in test_goto_rotation.py said
the fast path already faked the slew.

The spy below stands in for the ``asyncio`` module inside
``astrodeck.devices.sim`` and nowhere else. It records who asked to sleep and
for how long, and then REALLY sleeps for that long: it never shortens a
sleep, so the delay it records is the wall-clock the code under test costs.

The numbers below are computed by hand from the sim's constants, not read
back from the code under test:

- The goto. ``SimRig`` starts at 5.59 h, -5.39 deg with a 0.04 deg pointing
  error, so a slew to 5.0 h, +10 deg aims at 5.001867 h, +10.028 deg: 17.76
  deg of travel, 4.44 s at 4 deg/s, int(4.44 / 0.2) = 22 steps of 0.2019 s.
- The rotator. 70 deg (the move test_goto_rotation.py's rotate case makes)
  is 35 steps of 2 deg at 5 deg/s: 35 sleeps of 0.4 s, 14.0 s.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest

from astrodeck.devices import sim
from astrodeck.devices.sim import SimRig, SimRotator, SimTelescope

_REAL_SLEEP = asyncio.sleep

SLEW = "SimTelescope.slew"
ROTATE = "SimRotator.move_mechanical"

#: The field every sim-hub goto test uses (test_goto_rotation.py,
#: test_rotation_unavailable.py).
GOTO_RA, GOTO_DEC = 5.0, 10.0
GOTO_STEPS = 22
GOTO_DWELL_S = 4.4409

ROTATE_DEG = 70.0
ROTATE_STEPS = 35

#: The control's rotator move: 5 steps of 2.12 deg, 0.424 s each at 5 deg/s.
#: Not a round 10 on purpose. 2.0 deg steps (10 deg, or the 70 above) sum to
#: the target exactly, so a final snap to the target that only one of the two
#: runs made would leave the same bits and the control could not see it.
#: Five 2.12 deg steps sum to 10.600000000000001, one ulp past the target,
#: so the end state is 10.6 only because the snap ran.
CONTROL_ROTATE_DEG = 10.6
CONTROL_ROTATE_STEPS = 5
CONTROL_ROTATE_STEP_S = 0.424

#: How early ``asyncio.sleep`` may return on Windows: one tick of the system
#: clock (15.625 ms). A wall-clock floor that ignores it is a coin toss.
WINDOWS_TICK_S = 0.016


class _SimSleepSpy:
    """Stands in for ``asyncio`` inside ``astrodeck.devices.sim``.

    Every attribute but ``sleep`` is the real module's, so the sim's events,
    tasks and locks are untouched. ``sleep`` records the caller's qualified
    name and the requested delay, samples the rig when the test asks it to,
    and returns the REAL ``asyncio.sleep(delay)`` for the caller to await."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, float]] = []
        self.samples: list[tuple[str, tuple[str, ...]]] = []
        self.snapshot = None

    def __getattr__(self, name):
        return getattr(asyncio, name)

    def sleep(self, delay, *args, **kwargs):
        # A plain function, not a coroutine, so frame 1 is the sim method
        # that called ``asyncio.sleep`` and nothing in between.
        caller = sys._getframe(1).f_code.co_qualname
        self.calls.append((caller, delay))
        if self.snapshot is not None:
            self.samples.append((caller, self.snapshot()))
        return _REAL_SLEEP(delay, *args, **kwargs)

    def by(self, qualname: str) -> list[float]:
        return [d for (who, d) in self.calls if who == qualname]

    def reset(self) -> None:
        self.calls.clear()
        self.samples.clear()
        self.snapshot = None


@pytest.fixture
def sleep_spy(monkeypatch):
    spy = _SimSleepSpy()
    monkeypatch.setattr(sim, "asyncio", spy)
    return spy


@pytest.fixture
def _real_sim_pacing(_fast_sim_delays, monkeypatch):
    """Opt ONE test out of the suite-wide fast path, so the real slew and
    rotator dwell is measured and not only reasoned about. Depends on
    ``_fast_sim_delays`` so the suite-wide setenv runs first and this delenv
    wins."""
    monkeypatch.delenv("ASTRODECK_FAST_TEST", raising=False)
    yield


async def test_a_sim_goto_makes_no_real_sleep_under_the_fast_path(sleep_spy):
    """The goto the sim-hub tests make, from the sim's default pointing.

    MUTANT 'slew sleep unwrapped' (``await asyncio.sleep(duration / steps)``,
    the pre-#207 line). Observed, RED:
        AssertionError: under the fast path the slew still paid 22 real
        sleeps, 4.44 s in total
    MUTANT 'slew skips the yield under the fast path' (the sleep made only
    when ``_sim_delay`` returns non-zero). Observed, RED:
        AssertionError: the slew must still yield to the loop once per step
        under the fast path (22 steps), or an abort racing a slew in flight
        can no longer land mid-slew; it yielded 0 times
    MUTANT 'slew steps from the paced dwell' (see the control below) is RED
    here too, on the same assertion: "... it yielded 2 times".
    """
    rig = SimRig()
    assert (rig.ra_hours, rig.dec_deg) == (5.59, -5.39), (
        "precondition: the slew starts from the sim's default pointing")
    assert os.environ.get("ASTRODECK_FAST_TEST") == "1", (
        "precondition: the fast path is on")

    await SimTelescope(rig).slew(GOTO_RA, GOTO_DEC)

    slept = sleep_spy.by(SLEW)
    paid = [d for d in slept if d > 0]
    assert paid == [], (
        f"under the fast path the slew still paid {len(paid)} real sleeps, "
        f"{sum(paid):.2f} s in total")
    assert len(slept) == GOTO_STEPS, (
        f"the slew must still yield to the loop once per step under the fast "
        f"path ({GOTO_STEPS} steps), or an abort racing a slew in flight can "
        f"no longer land mid-slew; it yielded {len(slept)} times")


async def test_a_70_degree_rotator_move_makes_no_real_sleep_under_the_fast_path(
        sleep_spy):
    """MUTANT 'rotator sleep unwrapped' (``await asyncio.sleep(abs(step) /
    self.MOVE_RATE)``, the pre-#207 line). Observed, RED:
        AssertionError: under the fast path the rotator still paid 35 real
        sleeps, 14.00 s in total
    MUTANT 'rotator skips the yield under the fast path'. Observed, RED:
        AssertionError: the rotator must still yield to the loop once per
        step under the fast path (35 steps), or a halt racing a move in
        flight can no longer land mid-move; it yielded 0 times
    MUTANT 'rotator steps from the paced dwell' (see the control below) is
    RED here too, on the same assertion: "... it yielded 1 times".
    """
    rig = SimRig()
    assert rig.rotator_mech_deg == 0.0, "precondition: the rotator starts at 0"

    await SimRotator(rig).move_mechanical(ROTATE_DEG)

    slept = sleep_spy.by(ROTATE)
    paid = [d for d in slept if d > 0]
    assert paid == [], (
        f"under the fast path the rotator still paid {len(paid)} real "
        f"sleeps, {sum(paid):.2f} s in total")
    assert len(slept) == ROTATE_STEPS, (
        f"the rotator must still yield to the loop once per step under the "
        f"fast path ({ROTATE_STEPS} steps), or a halt racing a move in flight "
        f"can no longer land mid-move; it yielded {len(slept)} times")


def _hexed(*values: float) -> tuple[str, ...]:
    """Floats as ``float.hex``: equal strings are equal bits, which ``==`` on
    floats does not promise (it equates 0.0 with -0.0)."""
    return tuple(v.hex() for v in values)


async def _moves(spy: _SimSleepSpy, rotate_deg: float):
    """The goto and a rotator move on a fresh rig. Returns the path (the rig
    sampled at every sleep, then its final state) and the timings."""
    spy.reset()
    rig = SimRig()
    spy.snapshot = lambda: _hexed(rig.ra_hours, rig.dec_deg,
                                  rig.rotator_mech_deg)
    t0 = time.perf_counter()
    await SimTelescope(rig).slew(GOTO_RA, GOTO_DEC)
    t1 = time.perf_counter()
    await SimRotator(rig).move_mechanical(rotate_deg)
    t2 = time.perf_counter()
    final = _hexed(rig.ra_hours, rig.dec_deg, rig.rotator_mech_deg)
    path = list(spy.samples) + [("final", final)]
    return path, final, t1 - t0, t2 - t1, spy.by(SLEW), spy.by(ROTATE)


async def test_pacing_changes_no_simulated_value_and_the_real_dwell_exists(
        _real_sim_pacing, sleep_spy, monkeypatch):
    """The realism anchor and the control, in the one test that opts out of
    the fast path.

    ANCHOR: with ``ASTRODECK_FAST_TEST`` unset the goto still dwells 4.44 s
    in 22 steps and the rotator 0.424 s per 2.12 deg step, requested AND
    spent. The rotator half moves 10.6 deg, not 70: its path is a function
    of the travel alone (``max(1, int(|delta| / 2))`` steps of equal size),
    so five steps prove what thirty-five would, and 70 deg would cost this
    one test 14 s of real dwell to say it. Why 10.6 and not 10: see
    ``CONTROL_ROTATE_DEG``.

    CONTROL: the same moves on a fresh rig with the fast path back on land
    on bit-identical values, compared at EVERY step and at the end.
    Comparing only the end would miss a mutant that changes the path but
    not where it ends: at step fraction 1 any step count lands on the same
    bits.

    Samples: the rig at each of the slew's 22 sleeps (taken before each
    step moves it), at each of the rotator's 5 (taken after), then the
    final state: 28 in all.

    MUTANT 'slew dwell always zero' (``await asyncio.sleep(0.0)``). The two
    fast-path tests above stay GREEN under it, which is why this anchor
    exists. Observed, RED:
        AssertionError: with the fast path off the slew must still dwell
        4.44 s in real time; it asked for 0.00 s over 22 sleeps
    MUTANT 'rotator dwell always zero' (fast-path tests GREEN). Observed,
    RED:
        AssertionError: with the fast path off the rotator must still dwell
        0.424 s per 2.12 deg step; it asked for [0.0, 0.0, 0.0, 0.0, 0.0]
    MUTANT 'spy shortens the sleep' (THIS file's ``_SimSleepSpy.sleep``
    returning ``_REAL_SLEEP(0)``: the harness, not the sim). The requested
    dwell is right, so only the wall-clock floor can see it. Observed, RED:
        AssertionError: the slew asked for 4.44 s and took 0.00 s
    and with only the rotator's sleeps shortened (``_REAL_SLEEP(0 if
    caller == ROTATE else delay)``), RED on the other floor:
        AssertionError: the rotator asked for 2.12 s and took 0.00 s
    MUTANT 'slew steps from the paced dwell' (``steps = max(2,
    int(_sim_delay(duration) / 0.2))``). The end state is the same bits,
    so a control on the end alone would stay GREEN. Observed, RED:
        AssertionError: the simulated path differs with the fast path on:
        28 samples against 8, first difference at sample 1; final agrees:
        True
    MUTANT 'rotator steps from the paced dwell' (``steps = max(1,
    int(_sim_delay(abs(delta)) / 2.0))``). Observed, RED:
        AssertionError: the simulated path differs with the fast path on:
        28 samples against 24, first difference at sample 22; final agrees:
        True
    MUTANT 'slew lands on the command under the fast path' (after the step
    loop, ``if not _sim_delay(1.0): self.rig.ra_hours = ra_hours``; the
    fast-path tests stay GREEN). Observed, RED:
        AssertionError: the simulated path differs with the fast path on:
        28 samples against 28, first difference at sample 22; final agrees:
        False
    MUTANT 'rotator skips the final snap under the fast path' (``if
    _sim_delay(1.0):`` in front of ``self.rig.rotator_mech_deg = target``;
    the fast-path tests stay GREEN). With the control rotator at a round
    10 deg it was GREEN here too ("2 passed"): five 2.0 deg steps sum to
    10.0 exactly. At 10.6 deg, observed, RED:
        AssertionError: the simulated path differs with the fast path on:
        28 samples against 28, first difference at sample 27; final agrees:
        False
    """
    assert os.environ.get("ASTRODECK_FAST_TEST") is None, (
        "precondition: this test runs with the fast path off")
    real_path, real_final, slew_s, rot_s, slew_sleeps, rot_sleeps = (
        await _moves(sleep_spy, CONTROL_ROTATE_DEG))

    # ANCHOR. Asked for, and paid for.
    assert len(slew_sleeps) == GOTO_STEPS and sum(slew_sleeps) == (
        pytest.approx(GOTO_DWELL_S, abs=1e-3)), (
        f"with the fast path off the slew must still dwell "
        f"{GOTO_DWELL_S:.2f} s in real time; it asked for "
        f"{sum(slew_sleeps):.2f} s over {len(slew_sleeps)} sleeps")
    assert rot_sleeps == pytest.approx(
            [CONTROL_ROTATE_STEP_S] * CONTROL_ROTATE_STEPS), (
        f"with the fast path off the rotator must still dwell "
        f"{CONTROL_ROTATE_STEP_S} s per 2.12 deg step; it asked for "
        f"{rot_sleeps}")
    assert slew_s >= sum(slew_sleeps) - GOTO_STEPS * WINDOWS_TICK_S, (
        f"the slew asked for {sum(slew_sleeps):.2f} s and took {slew_s:.2f} s")
    assert rot_s >= sum(rot_sleeps) - CONTROL_ROTATE_STEPS * WINDOWS_TICK_S, (
        f"the rotator asked for {sum(rot_sleeps):.2f} s and took {rot_s:.2f} s")

    # CONTROL. The same moves with the pacing collapsed.
    monkeypatch.setenv("ASTRODECK_FAST_TEST", "1")
    fast_path, fast_final, _, _, fast_slew, fast_rot = (
        await _moves(sleep_spy, CONTROL_ROTATE_DEG))
    assert [d for d in fast_slew + fast_rot if d > 0] == [], (
        "precondition: the second run really is on the fast path")

    first = next((i for i, (a, b) in enumerate(zip(real_path, fast_path))
                  if a != b), min(len(real_path), len(fast_path)))
    assert fast_path == real_path, (
        f"the simulated path differs with the fast path on: "
        f"{len(real_path)} samples against {len(fast_path)}, first "
        f"difference at sample {first}; final agrees: "
        f"{fast_final == real_final}")
