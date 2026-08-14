"""The cooler gate: capture may not start OR RESUME on a sensor that is not cold.

The 2026-08-14 handoff makes this the head of HOLD / RESUME's checklist, ahead of
filter restore, re-centring, refocus and guiding, and the ordering is the whole
point: pointing and focus are worth nothing on a frame the temperature already
ruined, and a night's darks are indexed by temperature - a warm light has no
matching dark in the library and never will get one.

WHAT IT COSTS WHEN IT IS ABSENT, measured: on 2026-08-12 a midnight redeploy
raced the camera's connect and this rig put 63 frames on disk at +16 C against a
-5 C library. The start-of-run gate was fixed then. This file covers the OTHER
half - every reason a hold exists is also a reason cooling might have stopped
inside it (a daybreak park warms the camera by design, a power cycle drops the
TEC, a cooler fault drifts), and the loop used to resume into whatever the sensor
happened to read.

Two behaviours are pinned:

* the gate runs on resume, and it runs BEFORE the mount is re-pointed;
* "stable" means HELD, not touched. A sensor descending past its setpoint is in
  band for one sample, and the old check returned on that sample - printing
  "cooler stable at -5.0 C" about a camera still falling through -5 towards -12.
"""
from __future__ import annotations

import asyncio

import pytest

from astrodeck.sequence import engine as mod
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import SequencePlan


class _Cam:
    """A camera whose temperature follows a scripted series of readings."""

    def __init__(self, temps):
        self.connected = True
        self.can_cool = True
        self.temps = list(temps)
        self.commanded = None
        self.reads = 0

    async def set_cooler(self, on, target):
        self.commanded = target

    async def get_temperature(self):
        self.reads += 1
        # The last value repeats for ever, so a script says "…and then it stays".
        return self.temps[min(self.reads - 1, len(self.temps) - 1)]


class _Hub:
    def __init__(self, cam):
        self.devices = {"camera": cam}
        self.guider = None
        self.site = {}
        self.master_library = None


def _engine(cam, *, cool_to=-5.0):
    eng = SequenceEngine(_Hub(cam))
    eng.plan = SequencePlan(name="n", cool_to=cool_to)
    eng._set_state = lambda **kw: None

    async def _cp():
        return None
    eng._checkpoint = _cp
    return eng


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    """Run the probe loop at full speed. The DURATIONS are what is under test,
    not the wall clock, so the sleep is removed and the thresholds are shrunk in
    proportion - a test that actually waited two minutes would be one nobody
    runs."""
    monkeypatch.setattr(mod, "COOLER_PROBE_EVERY_S", 0.0)
    monkeypatch.setattr(mod, "COOLER_STABLE_S", 0.05)
    monkeypatch.setattr(mod, "COOLER_SETTLE_MAX_S", 1.0)


class TestStableMeansHeld:
    @pytest.mark.asyncio
    async def test_a_sensor_that_never_left_the_band_needs_no_settle(self):
        """The already-cold rig, and every simulator run. There was no descent to
        be caught in the middle of, so two minutes of waiting would prove nothing
        the readings have not already shown."""
        cam = _Cam([-5.0])
        eng = _engine(cam)
        assert await eng._cool_and_wait(-5.0, 30) is True
        assert cam.commanded == -5.0, "the cooler must still be commanded ON"
        assert cam.reads <= 2, f"waited on an already-cold sensor ({cam.reads} reads)"

    @pytest.mark.asyncio
    async def test_a_descent_through_the_band_is_not_stable(self):
        """THE DEFECT THIS FILE EXISTS FOR. The sensor passes -5 on its way to
        -12: in band for exactly one sample. Returning there would put "cooler
        stable at -5.0 C" in the log about a camera that is still falling, and
        every frame after it carries a SET-TEMP nothing settled at."""
        # 20.0, then a single in-band sample, then out the far side, then home.
        cam = _Cam([20.0, 10.0, -5.0, -12.0, -9.0, -5.2, -5.0, -5.0, -5.0])
        eng = _engine(cam)
        assert await eng._cool_and_wait(-5.0, 30) is True
        # It must NOT have returned on read 3, the transit sample.
        assert cam.reads > 3, f"returned on the transit sample (reads={cam.reads})"

    @pytest.mark.asyncio
    async def test_leaving_the_band_restarts_the_settle(self):
        """A half-completed hold either side of an excursion is not a hold."""
        cam = _Cam([20.0, -5.0, -9.0, -5.0, -5.0, -5.0, -5.0])
        eng = _engine(cam)
        assert await eng._cool_and_wait(-5.0, 30) is True
        assert cam.reads >= 5, "the excursion did not restart the settle"

    @pytest.mark.asyncio
    async def test_a_sensor_that_never_arrives_fails_rather_than_waits_for_ever(self):
        cam = _Cam([20.0])
        eng = _engine(cam)
        assert await eng._cool_and_wait(-5.0, 0.05) is False


class TestTheGateOnResume:
    @pytest.mark.asyncio
    async def test_the_gate_re_checks_the_sensor(self):
        cam = _Cam([-5.0])
        eng = _engine(cam)
        await eng._cooler_gate("resumed after 30 min of cloud")
        assert cam.commanded == -5.0, "the gate never asked for the setpoint"

    @pytest.mark.asyncio
    async def test_a_plan_with_no_cooling_intent_is_not_blocked(self):
        """An uncooled rig never asked for a temperature. Blocking it on a cooler
        it does not use would stop those runs dead, and `cool_to=None` is exactly
        how a plan says "no intent"."""
        cam = _Cam([20.0])
        eng = _engine(cam, cool_to=None)
        await asyncio.wait_for(eng._cooler_gate("resume"), timeout=2.0)
        assert cam.commanded is None, "commanded a cooler the plan never asked for"

    def test_the_resume_path_gates_BEFORE_it_re_points_the_mount(self):
        """Order, read off the source rather than trusted from the docstring.

        The handoff's checklist is ordered and this is its head. Re-centring a
        warm camera just points it accurately at frames no dark will match, so a
        gate that ran after `_setup_target` would be doing the expensive half of
        the resume on a sensor nobody had checked.

        Checked as SOURCE POSITIONS because the alternative - running a cloud
        hold end to end with a fake sky - would pass just as happily with the two
        calls swapped, which is the one thing this needs to detect.
        """
        import inspect
        src = inspect.getsource(SequenceEngine._hold_for_clear)
        gate = src.find("_cooler_gate")
        setup = src.find("_setup_target")
        assert gate != -1, "the cloud hold no longer gates the cooler on resume"
        assert setup != -1, "the cloud hold no longer re-acquires the target"
        assert gate < setup, (
            "the cooler gate must run BEFORE the mount is re-pointed - a "
            "re-centred warm camera is accurately aimed at unusable frames")
