"""Do not open the shutter while a meridian flip is owed and has not happened.

2026-09-10/11. At 00:13 the mount refused to track and the flip was abandoned.
The park/unpark recovery re-acquired NGC 7331 and never flipped it: the field
rotation before and after reads 96.74 and 96.6 with the rotator provably
static, which is a re-slew to the same side. The run kept exposing on the wrong
side of the pier, walking 65 arcsec/min, and every one of those subs was thrown
away.

THE FLIP LATCH WAS LEFT ARMED SO A RETRY WOULD FIRE. Across two frames and
twenty-four minutes, no retry fired, and the condition inside that gate which
suppressed it was never found. That is the whole argument for writing this as
an INVARIANT rather than as a better retry: a retry has to understand the bug,
and nobody understood this one. An invariant asserts what must be true before a
photon is worth collecting and refuses when it is not, whatever the state
machine above it did.

WHY THE TEST IS "THE SIDE DID NOT CHANGE" AND NOT "THE SIDE SHOULD BE EAST".
The second form bakes in a pier-side convention. A mount whose east/west sense
is the opposite of ours would then be held forever after a flip that worked
perfectly -- the invariant turning into the outage. So the engine remembers the
side the mount ACTUALLY REPORTED while the target was still east of the
meridian and trips only when the side after the crossing is that same one.
`TestItReasonsFromMeasurementNotConvention` is the class that pins this down; it
runs the whole thing with the convention inverted and expects no hold.
"""
from __future__ import annotations

import asyncio
import time

import pytest

import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.config import AppConfig
from astrodeck.devices.base import PierSide
from astrodeck.hub import Hub
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.engine import StopTarget
from astrodeck.sequence.models import ExposureStep, Target

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _target(name="T", dec=20.0, ra=0.0, count=3):
    return Target(name=name, ra_hours=ra, dec_deg=dec, center=False,
                  autofocus_first=False,
                  steps=[ExposureStep(filter="L", exposure_s=0.05,
                                      count=count)])


def _engine(sim_hub, monkeypatch, *, side="west", ttf_h=-0.5,
            pre_flip="west", dec=20.0, hold_min=0.02, flip=True):
    """An engine whose mount reports ``side`` and whose target is ``ttf_h``
    hours from its flip point (negative = already across).

    ``hours_to_meridian_flip`` is stubbed rather than arranged through the
    target's RA and the clock: the invariant is about the RELATION between the
    countdown and the measured side, and driving it through sidereal time would
    make every assertion here depend on what hour the suite happens to run at.
    """
    t = _target(dec=dec)
    e = SequenceEngine(sim_hub)
    e.plan = SequencePlan(targets=[t], meridian_flip=flip, guide=False)
    cfg = AppConfig()
    cfg.safety.flip_owed_hold_min = hold_min
    e._cfg = cfg
    e._pre_flip_side = pre_flip

    async def _pier():
        return {"east": PierSide.EAST, "west": PierSide.WEST}.get(
            side, PierSide.UNKNOWN)

    monkeypatch.setattr(sim_hub.devices["telescope"], "pier_side", _pier)
    monkeypatch.setattr(engine_mod.schedule, "hours_to_meridian_flip",
                        lambda ra, lon, now=None: ttf_h)
    return e, t


async def _never_flips(e, monkeypatch):
    """The 2026-09-11 mount: the flip gate runs and nothing changes sides."""
    tried = []

    async def _flip(target, next_exposure_s=0.0):
        tried.append(time.time())

    monkeypatch.setattr(e, "_maybe_meridian_flip", _flip)
    monkeypatch.setattr(engine_mod, "FLIP_OWED_POLL_S", 0.01)
    return tried


# ------------------------------------------------------------- it refuses


class TestItRefusesTheFrame:
    async def test_the_night_itself(self, sim_hub, monkeypatch):
        """Past the flip point, the mount still on the side it was on before
        it -- the exact state that produced four hours of binned subs."""
        e, t = _engine(sim_hub, monkeypatch, side="west", ttf_h=-0.4,
                       pre_flip="west")
        tried = await _never_flips(e, monkeypatch)
        with pytest.raises(StopTarget) as got:
            await e._enforce_flip_owed(t)
        assert "flip" in str(got.value).lower()
        assert tried, (
            "the hold never gave the flip gate another attempt; it is meant to "
            "re-arm and retry while it waits, even though the refusal is what "
            "makes it safe")

    async def test_it_says_so_at_error_level(self, sim_hub, monkeypatch):
        """A run that has stopped taking frames must say why, loudly. Four
        hours of 2026-09-11 passed with the log saying nothing was wrong."""
        from astrodeck.events import bus
        lines = []
        monkeypatch.setattr(bus, "log",
                            lambda lvl, msg, src=None, **kw:
                            lines.append((lvl, msg)))
        e, t = _engine(sim_hub, monkeypatch, side="west", ttf_h=-0.4)
        await _never_flips(e, monkeypatch)
        with pytest.raises(StopTarget):
            await e._enforce_flip_owed(t)
        assert any(lvl == "error" and "flip is owed" in msg
                   for lvl, msg in lines), lines

    async def test_flip_owed_is_published_while_it_holds(self, sim_hub,
                                                         monkeypatch):
        """The status field the UI's PIER tile reads. It must be True DURING
        the hold, not merely settable -- a flag that is only ever observed
        after the hold ends is a flag nobody can act on."""
        e, t = _engine(sim_hub, monkeypatch, side="west", ttf_h=-0.4,
                       hold_min=0.05)
        seen = []

        async def _flip(target, next_exposure_s=0.0):
            seen.append(e.flip_owed)

        monkeypatch.setattr(e, "_maybe_meridian_flip", _flip)
        monkeypatch.setattr(engine_mod, "FLIP_OWED_POLL_S", 0.01)
        with pytest.raises(StopTarget):
            await e._enforce_flip_owed(t)
        assert seen and all(seen), (
            f"flip_owed was not True while the engine was refusing to expose: "
            f"{seen}")
        assert e.flip_owed is False, "flip_owed stayed latched after the hold"

    async def test_a_flip_that_arrives_releases_the_hold(self, sim_hub,
                                                         monkeypatch):
        """The control that keeps this from being a worse bug than the one it
        fixes: when the mount DOES flip, the run resumes rather than skipping
        a target that is now perfectly shootable."""
        e, t = _engine(sim_hub, monkeypatch, side="west", ttf_h=-0.4,
                       hold_min=1.0)
        state = {"side": "west"}

        async def _pier():
            return {"east": PierSide.EAST,
                    "west": PierSide.WEST}[state["side"]]

        async def _flip(target, next_exposure_s=0.0):
            state["side"] = "east"          # this time the mount flips

        monkeypatch.setattr(sim_hub.devices["telescope"], "pier_side", _pier)
        monkeypatch.setattr(e, "_maybe_meridian_flip", _flip)
        monkeypatch.setattr(engine_mod, "FLIP_OWED_POLL_S", 0.01)
        await e._enforce_flip_owed(t)       # returns; does NOT raise
        assert e._pre_flip_side == "east", (
            "the new side was not recorded, so the next frame would compare "
            "against a side the mount has left")
        assert e.flip_owed is False


# ------------------------------------------------- and it refuses nothing else


class TestItDoesNotFireOtherwise:
    async def test_before_the_meridian_it_only_watches(self, sim_hub,
                                                       monkeypatch):
        """East of the meridian nothing is owed, and this is where the
        pre-flip side gets recorded."""
        e, t = _engine(sim_hub, monkeypatch, side="west", ttf_h=+2.0,
                       pre_flip=None)
        await e._enforce_flip_owed(t)
        assert e._pre_flip_side == "west", "the pre-flip side was not recorded"
        assert e.flip_owed is False

    async def test_a_flip_that_happened_passes(self, sim_hub, monkeypatch):
        """Side changed across the crossing. That is what a flip looks like
        from here, and it must cost nothing."""
        e, t = _engine(sim_hub, monkeypatch, side="east", ttf_h=-0.4,
                       pre_flip="west")
        await e._enforce_flip_owed(t)
        assert e.flip_owed is False

    async def test_a_target_acquired_already_west_is_not_guarded(
            self, sim_hub, monkeypatch):
        """No pre-flip reading exists, so there is nothing to compare against.
        Correct: a mount that slewed there landed on the side it chose."""
        e, t = _engine(sim_hub, monkeypatch, side="west", ttf_h=-3.0,
                       pre_flip=None)
        await e._enforce_flip_owed(t)
        assert e.flip_owed is False

    async def test_a_plan_with_no_flip_is_not_guarded(self, sim_hub,
                                                      monkeypatch):
        """`meridian_flip=False` is an operator saying they will handle it.
        Holding their run hostage for a flip they switched off is not safety."""
        e, t = _engine(sim_hub, monkeypatch, side="west", ttf_h=-0.4,
                       flip=False)
        await e._enforce_flip_owed(t)
        assert e.flip_owed is False

    async def test_the_exemption_is_the_flip_gates_own_predicate(
            self, sim_hub, monkeypatch):
        """A target the flip gate itself would excuse must not be held here.

        `flip_can_be_skipped` returns False for EVERY mount today, on purpose:
        the over-pole optimisation was disconnected after it cost four nights,
        and its docstring says the geometry reconnects "here and nowhere else"
        if a driver ever reports a mount type. So there is no declination that
        can drive this branch, and a test that hunted for one would be asserting
        the disconnection rather than the exemption.

        What this grades instead is the SEAM: the invariant asks that one
        shared predicate, so whatever it starts answering, the flip gate and
        this invariant answer together. Two copies of "which targets are
        exempt" is how the flip machinery got into trouble the first time.
        """
        from astrodeck.sequence import schedule
        assert schedule.flip_can_be_skipped(89.0, 45.0, "west") is False, (
            "premise: the predicate is expected to excuse nothing today -- if "
            "that changed, this test should be reconsidered, not deleted")

        e, t = _engine(sim_hub, monkeypatch, side="west", ttf_h=-0.4)
        monkeypatch.setattr(engine_mod.schedule, "flip_can_be_skipped",
                            lambda dec, lat, side, **kw: True)
        await e._enforce_flip_owed(t)
        assert e.flip_owed is False, (
            "the invariant held a target the flip gate would have excused, so "
            "it is not reading the shared predicate")

    async def test_an_unreadable_mount_neither_records_nor_trips(
            self, sim_hub, monkeypatch):
        """UNREADABLE IS NOT A VERDICT, in both directions. A dropped serial
        link must not trip the invariant, and it must not be recorded as a
        pre-flip side either -- a bad reading laundered into the baseline is
        how a guard starts lying."""
        async def _dead():
            raise RuntimeError("serial link is wedged")

        e, t = _engine(sim_hub, monkeypatch, side="west", ttf_h=+2.0,
                       pre_flip=None)
        monkeypatch.setattr(sim_hub.devices["telescope"], "pier_side", _dead)
        await e._enforce_flip_owed(t)
        assert e._pre_flip_side is None, "an unreadable mount set the baseline"
        assert e.flip_owed is False

    async def test_zero_disables_it(self, sim_hub, monkeypatch):
        """0 is off, as everywhere else in this config."""
        e, t = _engine(sim_hub, monkeypatch, side="west", ttf_h=-0.4,
                       hold_min=0.0)
        await e._enforce_flip_owed(t)
        assert e.flip_owed is False


# --------------------------------------- measurement, not convention


class TestItReasonsFromMeasurementNotConvention:
    async def test_an_inverted_mount_is_not_held_after_a_good_flip(
            self, sim_hub, monkeypatch):
        """THE TEST THAT RULES OUT THE TEMPTING IMPLEMENTATION.

        A mount whose east/west sense is the opposite of ASCOM's reports EAST
        while the target is still east of the meridian, and WEST after it
        flips. A rule written as "past the meridian, so it ought to be east"
        would hold this rig forever on a flip that worked. The rule written as
        "the side must have CHANGED" gets it right without knowing which
        convention the mount uses.
        """
        e, t = _engine(sim_hub, monkeypatch, side="east", ttf_h=+2.0,
                       pre_flip=None)
        await e._enforce_flip_owed(t)            # pre-meridian: records EAST
        assert e._pre_flip_side == "east"

        monkeypatch.setattr(engine_mod.schedule, "hours_to_meridian_flip",
                            lambda ra, lon, now=None: -0.4)

        async def _west():
            return PierSide.WEST                 # it flipped, to "west"

        monkeypatch.setattr(sim_hub.devices["telescope"], "pier_side", _west)
        await e._enforce_flip_owed(t)            # must NOT hold
        assert e.flip_owed is False

    async def test_the_same_inverted_mount_IS_held_when_it_does_not_flip(
            self, sim_hub, monkeypatch):
        """The other half, or the test above would pass on a rule that never
        fires at all."""
        e, t = _engine(sim_hub, monkeypatch, side="east", ttf_h=+2.0,
                       pre_flip=None)
        await e._enforce_flip_owed(t)
        assert e._pre_flip_side == "east"

        monkeypatch.setattr(engine_mod.schedule, "hours_to_meridian_flip",
                            lambda ra, lon, now=None: -0.4)
        await _never_flips(e, monkeypatch)
        with pytest.raises(StopTarget):
            await e._enforce_flip_owed(t)        # still east: owed


# ----------------------------------------------------- the call site itself


class TestTheRealFrameLoopCallsIt:
    """THE CALL SITE, not the method.

    Every test above invokes `_enforce_flip_owed` directly, so all of them
    would pass with the call deleted from the frame loop -- a perfect
    implementation wired to nothing, which is the exact shape of the defect
    this file exists for. This one drives the real `_run_step`.
    """

    async def test_no_frame_is_taken_while_the_flip_is_owed(self, sim_hub,
                                                            monkeypatch):
        e, t = _engine(sim_hub, monkeypatch, side="west", ttf_h=-0.4,
                       pre_flip="west", hold_min=0.02)
        await _never_flips(e, monkeypatch)

        async def _noop_gate(context="", target=None):
            return None

        e._safety_gate = _noop_gate
        e._done = {}
        e._frames_done = 0
        captured = []
        real_begin = e._begin_frame

        def _spy(ti, si, exposure_s):
            captured.append((ti, si))
            return real_begin(ti, si, exposure_s)

        monkeypatch.setattr(e, "_begin_frame", _spy)

        with pytest.raises(StopTarget):
            await e._run_step(0, 0, t, t.steps[0])
        assert not captured, (
            f"the frame loop opened the shutter {len(captured)} time(s) with a "
            f"meridian flip owed and the mount still on the pre-flip side -- "
            f"the invariant is not wired into the loop")

    async def test_the_same_loop_shoots_normally_when_nothing_is_owed(
            self, sim_hub, monkeypatch):
        """The control. Without it, a call site that raised unconditionally --
        or a `_run_step` too broken to reach the shutter at all -- would pass
        the test above."""
        e, t = _engine(sim_hub, monkeypatch, side="east", ttf_h=-0.4,
                       pre_flip="west")

        async def _noop_gate(context="", target=None):
            return None

        e._safety_gate = _noop_gate
        e._done = {}
        e._frames_done = 0
        captured = []
        real_begin = e._begin_frame

        def _spy(ti, si, exposure_s):
            captured.append((ti, si))
            return real_begin(ti, si, exposure_s)

        monkeypatch.setattr(e, "_begin_frame", _spy)
        await asyncio.wait_for(e._run_step(0, 0, t, t.steps[0]), 120.0)
        assert captured, (
            "no frame was taken even with the flip performed, so the test "
            "above proves nothing about the invariant")
