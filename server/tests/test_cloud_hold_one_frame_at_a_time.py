"""A cloud hold builds darks ONE frame at a time, never in a batch.

THE DEFECT THIS PINS, which was mine and shipped. `_hold_darks` took the whole
shortfall in a single `_run_calibration` call. At the exposure that actually
matters — 180 s, the sub length this rig shoots — twenty darks is SIXTY MINUTES
inside one pass of the hold loop, and two things follow from that:

  * the hold's own timeout is evaluated at the TOP of the loop, so it could not
    fire for an hour. The hold's detail line tells the operator "parks after
    45 min" in those words. It was not a bound anything kept.

  * the sky went unwatched for that hour. A hold exists to release itself when
    the weather passes; one that stops looking for an hour would sit through a
    clearing at minute five and not notice until minute sixty. The feature
    defeats its own purpose in exactly the conditions it was built for.

Neither shows up as a wrong number — every earlier test still passed, because
they asked "does it shoot the right count" and not "over what span". The guards
themselves were never disarmed (`_run_calibration` re-checks all four per
frame), so this was never dangerous; it was a promise, and a purpose, quietly
broken.
"""
from __future__ import annotations

import numpy as np

from astrodeck.calibration.library import CalibrationLibrary
from astrodeck.devices.base import CameraFrame
from astrodeck.imaging.fitsio import save_fits
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import ExposureStep, SequencePlan

STEP = ExposureStep(exposure_s=180.0, gain=125, offset=30, binning=1,
                    count=30, filter="Ha")


class _Hub:
    def __init__(self, library=None):
        self.master_library = library
        self.devices = {}
        self.guider = None
        self.site = {}


def _engine(tmp_path, quota=20):
    eng = SequenceEngine(_Hub(CalibrationLibrary(lambda: tmp_path)))
    eng.plan = SequencePlan(name="n", cloud_hold_darks=quota, cool_to=-5.0)
    eng._hold_step = STEP
    eng._hold_darks_want = None
    eng._hold_darks_taken = 0
    eng._calls = []

    async def _fake_run_calibration(ti, target):
        eng._calls.append(target)

    eng._run_calibration = _fake_run_calibration
    eng._index_of_target = lambda t: 0
    eng._set_state = lambda **kw: None
    return eng


def _banked(tmp, n, *, exp=180.0, gain=125, temp=-5.0):
    for i in range(n):
        f = CameraFrame(data=np.full((16, 12), 100 + i, np.uint16), exposure_s=exp,
                        gain=gain, offset=30, binning=1, bayer_pattern=None,
                        temperature_c=temp, timestamp=1_772_000_000.0 + i)
        save_fits(f, tmp / f"dark_{i}.fits", frame_type="Dark")


class TestOneFramePerCall:
    async def test_a_single_call_shoots_exactly_one_dark(self, tmp_path):
        eng = _engine(tmp_path)
        assert await eng._hold_darks(None) is True
        assert len(eng._calls) == 1
        steps = eng._calls[0].steps
        assert len(steps) == 1 and steps[0].count == 1, (
            "a batch here is an hour the hold cannot check its own timeout")

    async def test_repeated_calls_accumulate_to_the_shortfall_and_then_stop(
            self, tmp_path):
        _banked(tmp_path, 14)                       # 6 short of a quota of 20
        eng = _engine(tmp_path)
        fired = [await eng._hold_darks(None) for _ in range(12)]
        assert fired == [True] * 6 + [False] * 6
        assert len(eng._calls) == 6
        assert all(t.steps[0].count == 1 for t in eng._calls)

    async def test_a_full_library_never_shoots(self, tmp_path):
        _banked(tmp_path, 25)
        eng = _engine(tmp_path)
        assert await eng._hold_darks(None) is False
        assert eng._calls == []

    async def test_the_library_is_walked_once_not_before_every_frame(
            self, tmp_path):
        """The shortfall is a measurement, not a per-frame question. Re-walking
        the capture root before each dark would put a full filesystem scan
        between every exposure of a cloudy night."""
        eng = _engine(tmp_path)
        walks = {"n": 0}
        real = eng._hold_darks_shortfall

        def counting(step, quota):
            walks["n"] += 1
            return real(step, quota)

        eng._hold_darks_shortfall = counting
        for _ in range(5):
            await eng._hold_darks(None)
        assert walks["n"] == 1, f"walked the library {walks['n']} times"


class TestTheReturnValueDrivesTheProbeCadence:
    """The caller skips its sleep when a dark was taken, because a 180 s
    exposure already outlasts the probe interval — sleeping after it would only
    delay the next look at the sky."""

    async def test_it_returns_true_only_when_a_frame_was_actually_shot(
            self, tmp_path):
        _banked(tmp_path, 19)                       # exactly one short
        eng = _engine(tmp_path)
        assert await eng._hold_darks(None) is True
        assert await eng._hold_darks(None) is False

    async def test_a_failure_returns_false_and_does_not_raise(self, tmp_path):
        """A calibration hiccup must not turn a passing cloud into an aborted
        night — and it must not convince the caller it waited, either."""
        eng = _engine(tmp_path)

        async def _boom(ti, target):
            raise RuntimeError("camera fell over")

        eng._run_calibration = _boom
        assert await eng._hold_darks(None) is False

    async def test_no_quota_means_no_frames_and_no_wait_claim(self, tmp_path):
        eng = _engine(tmp_path, quota=0)
        assert await eng._hold_darks(None) is False
        assert eng._calls == []


class TestTheDarkGoesThroughTheBlackoutSlot:
    async def test_the_step_names_no_filter(self, tmp_path):
        """`_apply_filter` drives to the wheel's dark slot only when a step
        names NO filter and is a dark or bias. Naming the light's filter here
        would shoot every hold dark through Ha."""
        eng = _engine(tmp_path)
        await eng._hold_darks(None)
        step = eng._calls[0].steps[0]
        assert not step.filter, (
            "a hold dark named a filter, so the wheel stays on the light path")
        assert step.frame_type == "Dark"

    async def test_it_matches_the_interrupted_step_exactly(self, tmp_path):
        eng = _engine(tmp_path)
        await eng._hold_darks(None)
        s = eng._calls[0].steps[0]
        assert (s.exposure_s, s.gain, s.offset, s.binning) == (180.0, 125, 30, 1)
