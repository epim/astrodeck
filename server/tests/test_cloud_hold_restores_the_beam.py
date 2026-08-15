"""A cloud hold must put the science filter back before the run resumes.

THE HAZARD, and what it is not. The first version of this file said eighteen
180 s subs of NGC 6946 on 2026-08-12 were taken through the blackout slot and
called it fifty-four minutes of a clear night lost. That was wrong, and the
pixels say so: those frames share 73% of their brightest pixels with a genuine
Ha sub of the same target and 4% with a real dark. Light reached the sensor.
What wrote FILTER='Dark' on them was the wheel reporting a slot it was not on -
see `SnowflakeWheel.get_position` and `test_filter_moving.py`.

What IS true, and is why every test below stays:

  * the hold drives the wheel to the blackout slot on purpose, because a
    filterless calibration step is exactly how `_apply_filter` is told to go
    there;
  * that slot demonstrably BLANKS - the six hold darks from that night are
    black, sharing 4% of their brightest pixels with any light frame and 10%
    with each other, which is noise agreeing with noise;
  * `_apply_filter` runs ONCE, at the top of `_run_step`, above the frame loop,
    and the hold is dispatched from inside it.

So whoever moves the wheel from inside that loop owns putting it back, and
nobody did. Had the wheel obeyed the goto, every remaining sub of the step
would have been a black frame - and the run would have reported them as taken.

There is a second casualty in the same place. `_cloud_probe` is what decides
whether the sky has cleared, and it too was taken through whatever the hold left
in the beam. Through a blanked slot that probe reads a black frame as cloud and
the hold NEVER RELEASES, so the cost is not eighteen frames but the whole night
to the 45-minute abort.
"""
from __future__ import annotations

import pytest

import astrodeck.sequence.engine as engine_mod
from astrodeck.devices.sim import SimFilterWheel, SimRig
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target

#: The interrupted science step. "Ha" is slot 4 on the sim wheel; slot 7 is its
#: blackout carrier, which is the shape of the real 8-slot wheel this was found
#: on.
STEP = ExposureStep(exposure_s=180.0, gain=125, offset=30, binning=1,
                    count=30, filter="Ha")
SCIENCE_SLOT = 4
BLACKOUT_SLOT = 7


class _Hub:
    def __init__(self, wheel):
        self.devices = {"filterwheel": wheel}
        self.master_library = None
        self.guider = None
        self.site = {}

    def require(self, role):
        return self.devices[role]


async def _wheel() -> SimFilterWheel:
    fw = SimFilterWheel(SimRig())
    await fw.connect()
    fw.rig.filter_slot = SCIENCE_SLOT
    return fw


def _engine(fw, *, quota=20) -> SequenceEngine:
    eng = SequenceEngine(_Hub(fw))
    eng.plan = SequencePlan(name="n", cloud_hold_darks=quota, cool_to=-5.0,
                            apply_filter_offsets=False)
    eng._hold_step = STEP
    eng._hold_darks_want = None
    eng._hold_darks_taken = 0
    eng._set_state = lambda **kw: None
    eng._index_of_target = lambda t: 0
    return eng


def _stub_the_guards(eng, monkeypatch, *, probe_clear=True):
    """Everything the hold loop calls that is not what this file is about.

    The wheel, `_apply_filter` and `_restore_beam` are all REAL - the defect
    lives in which call sites move the wheel and which put it back, so faking
    any of those three would test the fake."""
    async def _none(*a, **k):
        return None

    async def _probe(*a, **k):
        return False if probe_clear else True

    for name in ("_checkpoint", "_safety_gate", "_frame_alerts_tick",
                 "_stand_down_guider", "_cooler_gate", "_setup_target"):
        monkeypatch.setattr(eng, name, _none)
    monkeypatch.setattr(eng, "_enforce_stop_boundary", lambda *a, **k: None)
    monkeypatch.setattr(eng, "_cloud_probe", _probe)
    # The loop sleeps a REAL 120 s between probes on any pass that took no
    # dark, which is every pass of the failure test below. Without this the
    # worker is killed for taking four minutes and the result reads as a crash
    # rather than as a sleep.
    monkeypatch.setattr(engine_mod, "CLOUD_PROBE_EVERY_S", 0.01)


def _calibration_that_moves_the_wheel(eng, *, then_raise=False):
    """Stand in for `_run_calibration` doing the ONE thing that matters here.

    The real one calls `_apply_filter(step)` on the calibration step, which is
    what drives the wheel to the blackout slot. That call is reproduced rather
    than stubbed away, because a `_run_calibration` that did not move the wheel
    would leave nothing for the restore to fix and the test would pass against
    the unfixed engine."""
    async def _run(ti, target):
        await eng._apply_filter(target.steps[0])
        if then_raise:
            raise RuntimeError("camera fell over mid-dark")
    return _run


async def test_the_dark_really_does_leave_the_wheel_on_the_blackout_slot(
        monkeypatch):
    """The premise. Without this the rest of the file could pass vacuously."""
    fw = await _wheel()
    eng = _engine(fw)
    dark = Target(name="d", ra_hours=0.0, dec_deg=0.0, calibration=True,
                  steps=[ExposureStep(exposure_s=180.0, gain=125, offset=30,
                                      binning=1, count=1, frame_type="Dark")])
    await eng._apply_filter(dark.steps[0])
    assert await fw.get_position() == BLACKOUT_SLOT


async def test_the_hold_leaves_the_science_filter_in_the_beam(monkeypatch):
    """The regression. Before the fix the wheel was still on slot 7 here, and
    every remaining frame of the step went through it."""
    fw = await _wheel()
    eng = _engine(fw)
    _stub_the_guards(eng, monkeypatch)
    monkeypatch.setattr(eng, "_run_calibration",
                        _calibration_that_moves_the_wheel(eng))
    await eng._hold_for_clear("test cloud", None)
    assert await fw.get_position() == SCIENCE_SLOT, (
        "the run resumed with the blackout slot in the light path - this is the "
        "18 frames of 2026-08-12")


async def test_the_probe_sees_the_sky_through_the_SCIENCE_filter(monkeypatch):
    """The second casualty, and on a properly blanked wheel the worse one.

    The probe is the hold's only evidence about the sky. Taken through a blanked
    slot it reads a black frame as cloud and the hold holds until it aborts."""
    fw = await _wheel()
    eng = _engine(fw)
    seen: list[int] = []
    _stub_the_guards(eng, monkeypatch)
    monkeypatch.setattr(eng, "_run_calibration",
                        _calibration_that_moves_the_wheel(eng))

    async def _probe(*a, **k):
        seen.append(await fw.get_position())
        return False

    monkeypatch.setattr(eng, "_cloud_probe", _probe)
    await eng._hold_for_clear("test cloud", None)
    assert seen, "the hold must probe at least once"
    assert all(s == SCIENCE_SLOT for s in seen), (
        f"a probe judged the sky through slot(s) {sorted(set(seen))}; a blanked "
        "slot reads as cloud and the hold can never release")


async def test_a_dark_that_FAILS_still_gets_the_beam_back(monkeypatch):
    """`_hold_darks` catches its own failures and returns False, and the wheel
    may already have moved before whatever failed. Gating the restore on "a dark
    was taken" would leave the beam wrong on exactly the path that reports
    nothing happened."""
    fw = await _wheel()
    eng = _engine(fw)
    _stub_the_guards(eng, monkeypatch)
    monkeypatch.setattr(eng, "_run_calibration",
                        _calibration_that_moves_the_wheel(eng, then_raise=True))
    await eng._hold_for_clear("test cloud", None)
    assert await fw.get_position() == SCIENCE_SLOT


async def test_a_step_with_no_filter_moves_nothing(monkeypatch):
    """A calibration run holds for cloud too, and its step names no filter.
    Restoring "no filter" must not drive the wheel anywhere."""
    fw = await _wheel()
    fw.rig.filter_slot = BLACKOUT_SLOT
    eng = _engine(fw)
    eng._hold_step = ExposureStep(exposure_s=180.0, gain=125, offset=30,
                                  binning=1, count=5, frame_type="Dark")
    _stub_the_guards(eng, monkeypatch)
    monkeypatch.setattr(eng, "_run_calibration",
                        _calibration_that_moves_the_wheel(eng))
    await eng._hold_for_clear("test cloud", None)
    assert await fw.get_position() == BLACKOUT_SLOT


class _Wheel:
    """The narrowest thing `_opaque_slot_in_beam` can read: names + flags +
    a position. Not a SimFilterWheel, because these tests are about the
    DECISION `Hub._blackout_light_cards` makes from it, not about wheel
    mechanics."""
    connected = True
    filter_names = ["L", "R", "G", "B", "Ha", "OIII", "SII", "Dark"]
    filter_opaque = [False] * 7 + [True]


def _cards(frame_type, slot, filt="Dark"):
    from astrodeck.hub import Hub
    return Hub._blackout_light_cards(object.__new__(Hub), frame_type, slot, filt)


def test_a_light_through_the_blackout_slot_is_carded():
    """The detector that would have caught the night of 2026-08-12 the same
    night, instead of in a header nobody reads until stacking."""
    cards = dict((k, v) for k, v, _c in _cards("Light", 7))
    assert cards["BEAMOK"] is False
    assert "slot 7" in cards["BEAMWHY"] and "blackout" in cards["BEAMWHY"]


def test_a_light_through_a_REAL_filter_is_not_carded():
    """`_opaque_slot_in_beam` returns None unless the current slot is flagged,
    so an ordinary sub must carry no card at all - an absent card has to keep
    meaning "fine", exactly as DARKOK does."""
    assert _cards("Light", None, "Ha") == []


def test_a_DARK_through_the_blackout_slot_is_NOT_carded():
    """That is the whole point of the slot. The dark check owns darks; this
    check must not second-guess it and card every correct calibration frame."""
    assert _cards("Dark", 7) == []
    assert _cards("Bias", 7) == []


def test_a_FLAT_through_the_blackout_slot_IS_carded():
    """A flat is a light-path frame. A flat through a blanked slot is as
    worthless as a sub through one, and it is not the dark check's business."""
    assert _cards("Flat", 7) != []
