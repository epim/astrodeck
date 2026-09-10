"""One flat 420 s bounded a flip that had to contain a 600 s calibration.

MEASURED, 2026-09-09 00:30, NGC 6946, 105 frames planned:

    00:30:48  meridian flip: stopping guiding and re-slewing
    00:31:31  centering attempt 1: 1.5' off target
    00:31:43  centering attempt 2: 0.4' off target
    00:31:43  pier side CHANGED, calibration correctly discarded
    00:31:45  native guider calibrating
              ... under cloud the guide star kept dropping; it never converged
    00:37:48  [error] meridian flip timed out after 420s - aborting
    00:38:08  mount parked

The run died at 34 frames. Everything before 00:31:45 was RIGHT - the flip
re-slewed, solved twice and centred in 55 seconds, and it discarded the
calibration because the pier side really had changed. What was wrong was the
number wrapped round the whole of it.

WHY 420 COULD NOT DO IT. ``hub.meridian_flip`` is stop-guide, re-slew,
plate-solve centre, discard the calibration when the side changed, and START
GUIDING. On a changed side that last step is a FRESH CALIBRATION WALK, and this
engine already knows what one of those costs: ``GUIDE_CALIBRATE_TIMEOUT_S``
(660 s) exists precisely because ``GUIDE_START_TIMEOUT_S`` (180 s) cut one in
half on 2026-09-07, and ``_guide_start_bound`` picks between them by asking
``Guider.needs_calibration``. The flip path never got that treatment. Its bound
was SHORTER than the walk it now had to contain, which makes it a guillotine
rather than a backstop - the same lesson ``TRACKING_RECOVERY_TIMEOUT_S`` and
``GUIDE_CALIBRATE_TIMEOUT_S`` each already carry. Clear skies hide it: a walk
fits in about five minutes and 420 s covers it by accident.

WHY THE FIX IS NOT "MAKE IT BIGGER". A flip that does not flip is the common
case on this mount - the AM5 picks its pier side from the hour angle, so the
lead-time attempt re-slews and stays exactly where it is, at a measured 67 s -
and making every one of those wait out a calibration-sized bound before it can
fail is a worse trade than the bug. So the bound is decided in two stages:
``FLIP_TIMEOUT_S`` covers the flip with a REUSED calibration, and only a guider
that confirms it is walking a fresh one buys the rest of
``FLIP_CALIBRATE_TIMEOUT_S``. See ``SequenceEngine._flip_bounded``.

HOW THE TIMING TESTS WORK. No real minutes are spent: the constants are scaled
down together, keeping the shape of 540 / 720 / 1200 against a ~4-minute walk -
a base bound the fake flip overruns, a calibration bound it fits inside. "The
engine picked the wrong bound" is therefore a cut flip, not a twenty-minute
test.
"""
from __future__ import annotations

import asyncio
import time

import pytest

import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.devices.base import PierSide
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan, schedule
from astrodeck.sequence.engine import SafetyAbort
from astrodeck.sequence.models import ExposureStep, Target

#: The scaled stand-ins, in the same proportions as the real constants.
#: ``WALK`` is how long the fake guide restart takes: comfortably past the base
#: bound, comfortably inside the calibration one. That is the 2026-09-09 shape -
#: a flip whose calibration was still running at eight minutes.
SLEW = 0.10                     # FLIP_SLEW_TIMEOUT_S   (really 540)
SHORT = 0.05                    # GUIDE_START_TIMEOUT_S (really 180)
LONG = 6.0                      # GUIDE_CALIBRATE_TIMEOUT_S (really 660)
WALK = 0.6                      # the fresh calibration walk


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _scale(monkeypatch, *, slew=SLEW, short=SHORT, long_=LONG):
    """Scale all four flip/guide bounds together, keeping the composition the
    shipping constants have — so a test can never accidentally grade a bound
    that is no longer built the way the module builds it."""
    monkeypatch.setattr(engine_mod, "FLIP_SLEW_TIMEOUT_S", slew)
    monkeypatch.setattr(engine_mod, "GUIDE_START_TIMEOUT_S", short)
    monkeypatch.setattr(engine_mod, "GUIDE_CALIBRATE_TIMEOUT_S", long_)
    monkeypatch.setattr(engine_mod, "FLIP_TIMEOUT_S", slew + short)
    monkeypatch.setattr(engine_mod, "FLIP_CALIBRATE_TIMEOUT_S", slew + long_)


@pytest.fixture
def scaled_bounds(monkeypatch):
    _scale(monkeypatch)


async def _noop_gate(context="", target=None):
    return None


class _SlowGuider:
    """A guider whose restart takes ``walk`` seconds, and which models GN-01's
    discard latch: ``flip_calibration`` throws the calibration away, so the
    next start MUST measure a new one and ``needs_calibration`` says so.

    That latch is the whole mechanism the two-stage bound leans on. The engine
    cannot know before the flip whether the pier side will change; it can ask
    afterwards, because the discard the flip itself performs is exactly what
    turns this answer from False to True.
    """
    name = "slow guider"

    def __init__(self, walk: float = WALK):
        self.connected = True
        self.walk = walk
        self.calls: list[str] = []
        self._discarded = False
        self._active = True
        self.starts = 0
        self.finished = 0
        self.cancelled = 0

    async def is_active(self) -> bool:
        return self._active

    async def stop_guiding(self) -> None:
        self.calls.append("stop")
        self._active = False

    async def flip_calibration(self) -> bool:
        self.calls.append("flip_calibration")
        self._discarded = True
        return True

    async def needs_calibration(self) -> bool:
        return self._discarded

    async def start_guiding(self) -> None:
        self.calls.append("start")
        self.starts += 1
        try:
            await asyncio.sleep(self.walk)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        self.finished += 1
        self._active = True

    def stats(self):
        return None


def _flip_engine(sim_hub, monkeypatch, *, the_slew_flips: bool,
                 walk: float = WALK):
    """An engine armed nine minutes before transit, on the REAL
    ``hub.meridian_flip``.

    Only ``goto_and_center`` is faked — it is the slew, and it is the one place
    a mount decides its pier side, so ``the_slew_flips`` is how a test says
    "this mount really did swap sides". Everything from the discard to the
    guide restart is the shipping code. Same shape as
    ``test_the_flip_stops_paying_for_itself``'s fixture, deliberately.
    """
    tel = sim_hub.devices["telescope"]
    lon = sim_hub.site["longitude"]
    ra = (schedule.lst_hours(lon) + 9.0 / 60.0) % 24.0
    t = Target(name="NGC 6946", ra_hours=ra, dec_deg=60.15, center=False,
               autofocus_first=False,
               steps=[ExposureStep(filter="L", exposure_s=0.05, count=1)])
    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan(targets=[t], meridian_flip=True, guide=True)
    e._cfg = None
    e._flip_armed = True
    e._safety_gate = _noop_gate

    st = {"side": "west", "flips": the_slew_flips, "gotos": [], "af": []}

    async def _pier():
        return {"east": PierSide.EAST,
                "west": PierSide.WEST}.get(st["side"], PierSide.UNKNOWN)

    async def _no_countdown():
        return None

    async def _goto(ra_h, dec_d, **kw):
        st["gotos"].append((ra_h, dec_d))
        if st["flips"]:
            st["side"] = "east" if st["side"] == "west" else "west"
        return {"centered": True, "error_arcmin": 0.4}

    async def _hold(target, lead_s=0.0):
        return None

    async def _af(label, **kw):
        st["af"].append(label)
        return True

    monkeypatch.setattr(tel, "pier_side", _pier)
    monkeypatch.setattr(tel, "time_to_meridian_flip", _no_countdown)
    monkeypatch.setattr(sim_hub, "goto_and_center", _goto)
    e._wait_for_flip_point = _hold
    e._autofocus = _af

    guider = _SlowGuider(walk)
    sim_hub.guider = guider
    st["guider"] = guider
    return e, t, st


# ------------------------------------------------- 1. the bound's arithmetic

def test_the_flip_bound_is_composed_from_the_bounds_it_wraps():
    """THE DEFECT AS A SUM. The flip's bound has to be at least the bounds of
    the steps inside it, and the step that killed the run is the fresh
    calibration — whose allowance everywhere else in this engine is
    ``GUIDE_CALIBRATE_TIMEOUT_S``.

    Recomputed from the parts, never written down as a number, so nobody can
    retune ``GUIDE_CALIBRATE_TIMEOUT_S`` or ``GOTO_TIMEOUT_S`` and leave the
    flip bounded at something smaller than what it now contains.
    """
    E = engine_mod
    assert E.FLIP_SLEW_TIMEOUT_S == E.GOTO_TIMEOUT_S + E.GUIDE_OP_TIMEOUT_S, (
        "the flip's base is no longer the re-slew plus the stop-guide it wraps")
    assert E.FLIP_TIMEOUT_S == E.FLIP_SLEW_TIMEOUT_S + E.GUIDE_START_TIMEOUT_S
    assert (E.FLIP_CALIBRATE_TIMEOUT_S
            == E.FLIP_SLEW_TIMEOUT_S + E.GUIDE_CALIBRATE_TIMEOUT_S)
    assert E.FLIP_CALIBRATE_TIMEOUT_S > E.FLIP_TIMEOUT_S, (
        "the two flip bounds collapsed into one, which is the 2026-09-09 bug")
    # ...and the measured base really does fit inside the base bound with room:
    # 55 s of re-slew and two solves on 2026-09-09.
    assert E.FLIP_SLEW_TIMEOUT_S > 5 * 55.0


def test_the_bound_that_killed_the_run_was_smaller_than_the_walk():
    """WHY 420 WAS THE WRONG NUMBER, stated as the inequality that was false.

    The flip has to contain a calibration walk, and the walk's own backstop
    (``guide.native._CAL_TIMEOUT_S``, 600 s) has to be allowed to fire FIRST so
    the morning gets the diagnosis — pulses landed, which leg, how far the star
    walked — instead of "meridian flip timed out — aborting".
    """
    E = engine_mod
    assert 420.0 < E.GUIDE_CALIBRATE_TIMEOUT_S, (
        "premise: the old flat flip bound really was shorter than the "
        "calibration allowance the same engine grants elsewhere")
    assert E.FLIP_CALIBRATE_TIMEOUT_S > E.GUIDE_CALIBRATE_TIMEOUT_S


def test_the_flip_outlasts_the_guiders_own_calibration_backstop():
    """The same rule ``GUIDE_CALIBRATE_TIMEOUT_S`` already lives by, one bound
    out: the guider caps its own walk at ``_CAL_TIMEOUT_S`` and fails there with
    the evidence attached, and an outer bound that races it replaces a
    diagnosis with "timed out — aborting"."""
    from astrodeck.providers import NATIVE_AVAILABLE
    if not NATIVE_AVAILABLE:
        pytest.skip("native wheel absent")
    from astrodeck.guide import native as native_mod

    assert (engine_mod.FLIP_CALIBRATE_TIMEOUT_S
            > engine_mod.FLIP_SLEW_TIMEOUT_S + native_mod._CAL_TIMEOUT_S), (
        "a flip bounded at or below the walk's own backstop preempts the "
        "diagnosis the guider was about to write")


# --------------------------------------------- 2. which bound, and from what

async def test_the_flip_bound_follows_the_guiders_answer(sim_hub,
                                                         scaled_bounds):
    """The same question ``_guide_start_bound`` asks, asked about the flip: a
    guider with a calibration on file gets the base bound, one that must walk a
    fresh one gets the base plus the walk's allowance."""
    E = engine_mod
    g = _SlowGuider()
    sim_hub.guider = g
    eng = SequenceEngine(sim_hub)

    bound_s, label, note = await eng._flip_bound()
    assert bound_s == E.FLIP_SLEW_TIMEOUT_S + E.GUIDE_START_TIMEOUT_S
    assert label == "meridian flip" and note == ""

    g._discarded = True                     # what a real pier change leaves
    bound_s, label, note = await eng._flip_bound()
    assert bound_s == E.FLIP_SLEW_TIMEOUT_S + E.GUIDE_CALIBRATE_TIMEOUT_S
    assert "calibration" in label, label
    assert "cut part way" in note, note


async def test_a_flip_with_no_guider_cannot_contain_a_calibration(
        sim_hub, scaled_bounds):
    """``_guide_start_bound`` answers "cannot say" for an absent guider and
    lands on the roomy bound, which is right for a guide START and wrong for a
    flip: with no guider there is no restart in the flip at all."""
    sim_hub.guider = None
    eng = SequenceEngine(sim_hub)
    bound_s, label, note = await eng._flip_bound()
    assert bound_s == engine_mod.FLIP_TIMEOUT_S
    assert label == "meridian flip" and note == ""


# ------------------------------------------------------- 3. the real defect

async def test_a_slow_calibration_after_a_real_flip_is_not_cut(sim_hub,
                                                               monkeypatch,
                                                               scaled_bounds):
    """THE 2026-09-09 REGRESSION. The pier side changed, the calibration was
    correctly discarded, and the walk ran long — eight minutes, under cloud.
    The flip must be allowed to finish it rather than be cancelled at the base
    bound and take the night down with it."""
    e, t, st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=True)
    g = st["guider"]
    assert WALK > engine_mod.FLIP_TIMEOUT_S, (
        "premise: the walk must overrun the base bound, or this proves nothing")

    await e._maybe_meridian_flip(t, next_exposure_s=180.0)

    assert "flip_calibration" in g.calls, (
        f"premise: a changed pier side must discard the calibration: {g.calls}")
    assert g.finished == 1, "the fresh calibration was cut - this is the defect"
    assert g.cancelled == 0
    assert e._flip_armed is False, "a completed flip must spend its latch"
    assert st["af"] == ["post-flip autofocus"], st["af"]


async def test_the_no_op_flip_is_still_bounded_tightly(sim_hub, monkeypatch,
                                                       scaled_bounds):
    """THE OTHER HALF, and the reason the bound is not simply bigger.

    The AM5's lead-time attempt re-slews and stays put, at a measured 67 s. No
    pier change means no discard means no walk — so a lead-time attempt that
    WEDGES must fail on the base bound, not sit through a calibration-sized one
    at the meridian on a mount that stops tracking at its own limit.
    """
    e, t, st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=False,
                            walk=30.0)
    g = st["guider"]

    t0 = time.monotonic()
    with pytest.raises(SafetyAbort, match="meridian flip timed out"):
        await e._maybe_meridian_flip(t, next_exposure_s=180.0)
    elapsed = time.monotonic() - t0

    assert "flip_calibration" not in g.calls, (
        f"premise: nothing flipped, so nothing may be discarded: {g.calls}")
    assert g.cancelled == 1, "the wedged restart was not cancelled"
    assert elapsed < engine_mod.FLIP_CALIBRATE_TIMEOUT_S / 2, (
        f"the no-op flip waited {elapsed:.2f}s, on its way to the "
        f"{engine_mod.FLIP_CALIBRATE_TIMEOUT_S:.2f}s calibration bound it "
        f"never earned")


async def test_the_no_op_timeout_does_not_claim_a_calibration(
        sim_hub, monkeypatch, scaled_bounds, bus_lines):
    """...and it must not say it cut a walk that never started. The note is
    what the morning reads."""
    e, t, _st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=False,
                             walk=30.0)
    with pytest.raises(SafetyAbort) as caught:
        await e._maybe_meridian_flip(t, next_exposure_s=180.0)
    said = str(caught.value)
    assert said.startswith("meridian flip timed out after"), said
    assert "calibration" not in said, said


async def test_a_flip_that_genuinely_hangs_still_aborts(sim_hub, monkeypatch,
                                                        bus_lines):
    """A backstop that never fires is not a backstop. A flip whose calibration
    goes nowhere still tears the night down — at the calibration-sized bound,
    with a line that says what the cancel cost and what it had been allowed."""
    _scale(monkeypatch, slew=1.0, short=0.5, long_=2.0)
    e, t, st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=True,
                            walk=60.0)
    g = st["guider"]

    t0 = time.monotonic()
    with pytest.raises(SafetyAbort) as caught:
        await e._maybe_meridian_flip(t, next_exposure_s=180.0)
    elapsed = time.monotonic() - t0

    said = str(caught.value)
    assert "fresh calibration" in said, said
    assert "cut part way" in said, said
    assert "for the calibration" in said, said
    assert f"after {engine_mod.FLIP_CALIBRATE_TIMEOUT_S:.0f}s" in said, said
    assert g.cancelled == 1, "the hung walk was left running"
    assert elapsed >= engine_mod.FLIP_TIMEOUT_S, (
        "it aborted before even the base bound")
    errors = [m for (lvl, m, _s) in bus_lines if lvl == "error"]
    assert any("fresh calibration" in m and "cut part way" in m
               for m in errors), errors
    # and the extension announces itself, so a 20-minute flip is explicable
    warns = [m for (lvl, m, _s) in bus_lines if lvl == "warning"]
    assert any("allowing it" in m and "fresh calibration" in m
               for m in warns), warns


async def test_a_wedge_before_the_guider_restart_fails_on_the_base_bound(
        sim_hub, monkeypatch, scaled_bounds):
    """Where the wedge is matters. A flip stuck in the SLEW has discarded
    nothing and owes nothing a walk, so it must not inherit the calibration's
    allowance just because the mount happens to be mid-meridian."""
    e, t, st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=True)

    async def _stuck(ra_h, dec_d, **kw):
        await asyncio.sleep(30)
        return {"centered": True}

    monkeypatch.setattr(sim_hub, "goto_and_center", _stuck)

    t0 = time.monotonic()
    with pytest.raises(SafetyAbort, match="meridian flip timed out"):
        await e._maybe_meridian_flip(t, next_exposure_s=180.0)
    elapsed = time.monotonic() - t0
    assert elapsed < engine_mod.FLIP_CALIBRATE_TIMEOUT_S / 2, (
        f"a wedged slew waited {elapsed:.2f}s for a calibration that had not "
        f"begun")
    assert "flip_calibration" not in st["guider"].calls


async def test_the_abort_is_a_safety_abort_so_the_mount_still_parks(
        sim_hub, monkeypatch, scaled_bounds):
    """Unchanged behaviour, pinned because the bound was rewritten around it:
    a genuine flip timeout is a ``SafetyAbort``, which is what routes the run
    through the shielded park/warm wind-down (00:38:08 on the night)."""
    e, t, _st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=False,
                             walk=30.0)
    with pytest.raises(SafetyAbort):
        await e._maybe_meridian_flip(t, next_exposure_s=180.0)


async def test_a_cancelled_run_does_not_leave_the_flip_running(
        sim_hub, monkeypatch, scaled_bounds):
    """The shield is what lets the first expiry pass without killing the flip,
    and a shield is also how a coroutine gets orphaned. A user abort must still
    cancel the flip, exactly as ``wait_for`` would have."""
    e, t, st = _flip_engine(sim_hub, monkeypatch, the_slew_flips=True,
                            walk=30.0)
    task = asyncio.ensure_future(
        e._maybe_meridian_flip(t, next_exposure_s=180.0))
    await asyncio.sleep(engine_mod.FLIP_TIMEOUT_S + 0.15)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.05)
    assert st["guider"].cancelled == 1, (
        "the flip outlived the run that was cancelling it")
