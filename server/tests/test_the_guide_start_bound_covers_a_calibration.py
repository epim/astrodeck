"""The 180 s guide-start bound cut a fresh calibration and the run went blind.

MEASURED, 2026-09-07 03:39. A meridian-limit recovery parked, unparked and
re-centred the mount. That changed the pier side; GN-01 refuses to reuse a
calibration measured on the other side, so the guide restart had to walk a
fresh one - three to five minutes on this rig. The restart was bounded at
``GUIDE_START_TIMEOUT_S = 180.0``, ``_bounded`` cancels the awaitable on
timeout, and the walk was severed part way through. The recovery's handler
caught it and logged

    guiding did not restart after the recovery (...) - continuing unguided

and the target ran unguided for twenty minutes, losing a frame to trailing.

WHY ONE NUMBER COULD NOT DO BOTH. 180 s is right for a start that REUSES a
calibration: one guide exposure, a star-find, load, settle. It is inside the
normal duration of a start that has to MEASURE one. A backstop that fires
during the ordinary operation of the thing it wraps is not a backstop, it is a
guillotine - the same lesson ``TRACKING_RECOVERY_TIMEOUT_S`` already carries in
its own comment, learned again one bound along.

SO THE ENGINE ASKS. ``Guider.needs_calibration`` is the vendor-neutral
question; the native guider answers it with exactly the checks its own
``start_guiding`` is about to make, and a guider that cannot say gets the roomy
bound because PHD2's ``guide`` RPC calibrates too when PHD2 has nothing on
file.

HOW THE TIMING TESTS WORK. Real time is not spent: the two constants are scaled
down together (a short bound that a 0.5 s start overruns, a long one it fits
inside), so "the engine picked the wrong bound" is a cut start rather than a
five-minute test.
"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest

import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.guide.base import Guider
from astrodeck.hub import Hub
from astrodeck.providers import NATIVE_AVAILABLE
from astrodeck.sequence import SequenceEngine, SequencePlan
from astrodeck.sequence.engine import SafetyAbort
from astrodeck.sequence.models import ExposureStep, Target

#: The scaled stand-ins. ``SHORT`` is the reuse bound, ``LONG`` the calibration
#: bound, and ``WALK`` is how long the fake start takes: longer than SHORT,
#: comfortably inside LONG. Exactly the shape of 180 / 660 / ~240.
SHORT = 0.15
LONG = 6.0
WALK = 0.6


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


@pytest.fixture
def scaled_bounds(monkeypatch):
    monkeypatch.setattr(engine_mod, "GUIDE_START_TIMEOUT_S", SHORT)
    monkeypatch.setattr(engine_mod, "GUIDE_CALIBRATE_TIMEOUT_S", LONG)


class _SlowGuider(Guider):
    """A guider whose start takes ``WALK`` seconds and says whether that is a
    calibration walk. ``answer`` of ``"silent"`` models the PHD2/NINA bridge and
    the sim: no ``needs_calibration`` at all."""

    name = "slow guider"

    def __init__(self, answer: bool | str) -> None:
        self.connected = True
        self.answer = answer
        self.starts = 0
        self.finished = 0
        self.cancelled = 0
        #: The recovery only puts guiding back if it was guiding before it
        #: parked, which is the state of the rig at 03:39.
        self.active = True
        if answer == "silent":
            # Not merely returning None - ABSENT, which is what an older or
            # third-party guider looks like to `getattr(guider, ...)`.
            del type(self).needs_calibration

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def start_guiding(self) -> None:
        self.starts += 1
        try:
            await asyncio.sleep(WALK)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        self.finished += 1

    async def stop_guiding(self) -> None:
        self.active = False

    async def is_active(self) -> bool:
        return self.active

    async def dither(self, pixels: float = 3.0, settle=None) -> None: ...

    def stats(self):
        from astrodeck.guide.base import GuideStats
        return GuideStats()

    async def needs_calibration(self) -> bool | None:
        return None if self.answer == "unknown" else bool(self.answer)


def _guider(answer):
    """A fresh ``_SlowGuider`` subclass per test, so deleting the probe method
    for the "silent" case cannot leak into another test."""
    cls = type("_Slow", (_SlowGuider,), {})
    if answer == "silent":
        g = object.__new__(cls)
        cls.needs_calibration = None        # not callable -> treated as absent
        _SlowGuider.__init__(g, True)
        return g
    return cls(answer)


def _target() -> Target:
    return Target(name="M42", ra_hours=5.5881, dec_deg=-5.3911, center=False,
                  autofocus_first=False,
                  steps=[ExposureStep(filter="L", exposure_s=0.05, count=1)])


def _engine(hub, guider) -> SequenceEngine:
    hub.guider = guider
    eng = SequenceEngine(hub)
    eng._cfg = None                       # warn-and-continue, the default
    eng.plan = SequencePlan(name="p", guide=True, meridian_flip=False,
                            autofocus_every=0, dither_every=0,
                            targets=[_target()])
    return eng


# ------------------------------------------------- the constant, against the
# ------------------------------------------------- guider's OWN walk backstop

@pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native wheel absent")
def test_the_guide_start_bound_outlasts_the_walk():
    """The engine's bound must be BIGGER than the walk's own backstop.

    The native guider caps its calibration at ``_CAL_TIMEOUT_S`` and fails
    there with the evidence attached - pulses landed, which leg, how far the
    star walked, how many starless frames. An outer bound set to the same
    number races that and usually wins, and what the operator gets instead is
    "timed out - aborting" with none of it. Recomputed from both constants so
    they cannot drift back together.
    """
    from astrodeck.guide import native

    assert engine_mod.GUIDE_CALIBRATE_TIMEOUT_S > native._CAL_TIMEOUT_S, (
        f"the engine bound ({engine_mod.GUIDE_CALIBRATE_TIMEOUT_S:.0f}s) does "
        f"not outlast the guider's own calibration backstop "
        f"({native._CAL_TIMEOUT_S:.0f}s), so it preempts the diagnosis")
    assert (engine_mod.GUIDE_CALIBRATE_TIMEOUT_S
            > engine_mod.GUIDE_START_TIMEOUT_S), "the two bounds collapsed"


# --------------------------------------------------------- choosing the bound

@pytest.mark.parametrize("answer,expect_s", [
    (True, "long"), (False, "short"), ("unknown", "long"), ("silent", "long"),
])
async def test_the_bound_follows_the_guiders_answer(sim_hub, scaled_bounds,
                                                    answer, expect_s):
    """A guider that says it must calibrate gets the roomy bound; one with a
    calibration on file gets the tight one; one that cannot say gets the roomy
    one, because being wrong that way costs minutes and being wrong the other
    way costs the calibration."""
    eng = _engine(sim_hub, _guider(answer))
    bound_s, label, note = await eng._guide_start_bound()
    assert bound_s == (LONG if expect_s == "long" else SHORT), label
    if expect_s == "short":
        assert label == "start guiding", label
        assert note == ""
    else:
        assert "calibration" in label, (
            f"the bound's label must say what was bounded: {label!r}")
        assert "cut" in note, note


async def test_a_probe_that_wedges_does_not_wedge_the_night(sim_hub,
                                                            scaled_bounds,
                                                            monkeypatch):
    """The question about the bound must not become another unbounded await -
    and it must not tear the run down either. A guider too wedged to answer
    falls through to the roomy bound and lets the START be the thing that
    fails."""
    monkeypatch.setattr(engine_mod, "MOUNT_QUERY_TIMEOUT_S", 0.05)
    g = _guider(True)

    async def _hangs():
        await asyncio.sleep(30)
        return True

    g.needs_calibration = _hangs
    eng = _engine(sim_hub, g)
    bound_s, label, _note = await eng._guide_start_bound()
    assert bound_s == LONG, label


# ---------------------------------------------- the real target-setup path

async def test_a_fresh_calibration_start_is_not_cut(sim_hub, scaled_bounds):
    """The regression, on the engine's real target-setup path. The start takes
    longer than the reuse bound; because the guider says it is a walk, it is
    allowed to finish."""
    g = _guider(True)
    eng = _engine(sim_hub, g)
    await eng._setup_target(0, eng.plan.targets[0])
    assert g.finished == 1, "the fresh calibration was cut"
    assert g.cancelled == 0


async def test_the_reuse_bound_is_still_the_tight_one(sim_hub, scaled_bounds):
    """The control, and the thing that stops this fix from becoming "make every
    bound bigger". A guider that HAS a calibration and still cannot start
    inside the reuse bound is wedged, and the night still ends rather than
    hanging on it."""
    g = _guider(False)
    eng = _engine(sim_hub, g)
    with pytest.raises(SafetyAbort, match="start guiding timed out"):
        await eng._setup_target(0, eng.plan.targets[0])
    assert g.cancelled == 1
    assert g.finished == 0


async def test_the_cut_says_the_calibration_was_cut(sim_hub, monkeypatch,
                                                    bus_lines):
    """(b) of the fix. "start guiding failed" told the morning nothing about
    the walk it had just severed. The timeout line must name both what was
    bounded and what the cancel cost."""
    monkeypatch.setattr(engine_mod, "GUIDE_START_TIMEOUT_S", SHORT)
    monkeypatch.setattr(engine_mod, "GUIDE_CALIBRATE_TIMEOUT_S", SHORT)
    g = _guider(True)
    eng = _engine(sim_hub, g)
    with pytest.raises(SafetyAbort) as caught:
        await eng._setup_target(0, eng.plan.targets[0])

    said = str(caught.value)
    assert "fresh calibration" in said, said
    assert "cut part way" in said, said
    errors = [m for (lvl, m, _s) in bus_lines if lvl == "error"]
    assert any("fresh calibration" in m and "cut part way" in m
               for m in errors), errors


# ------------------------------------------- the limit-recovery restart path

async def test_the_recovery_restart_makes_the_same_choice(sim_hub,
                                                          scaled_bounds,
                                                          monkeypatch):
    """The path of the incident. ``_recover_from_tracking_refusal`` parks,
    unparks and re-centres - which is what changes the pier side, which is what
    discards the calibration - so this restart is the one most likely to be a
    full walk. It used the flat 180 s.
    """
    from astrodeck.sequence import engine as E

    g = _guider(True)
    eng = _engine(sim_hub, g)
    target = eng.plan.targets[0]

    # Everything the recovery does to the mount is tested in
    # test_flip_before_the_limit.py; this test is about the guide restart, so
    # the recovery is driven straight to it.
    monkeypatch.setattr(E.schedule, "dark_enough", lambda *a, **k: True)
    monkeypatch.setattr(hub_module.config_store.cfg().site, "is_default", False)

    ok = await eng._recover_from_tracking_refusal(target)
    assert ok is True, "premise: the recovery itself has to run to its end"
    assert g.starts >= 1, "the recovery never restarted guiding"
    assert g.finished >= 1, (
        "the recovery's guide restart was cut - this is the 03:39 defect")
    assert g.cancelled == 0


# --------------------------------- what the native guider answers, and why

pytestmark_native = pytest.mark.skipif(not NATIVE_AVAILABLE,
                                       reason="native wheel absent")


@pytest.fixture
def cal_dir(tmp_path, monkeypatch):
    import astrodeck.config as config
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    return tmp_path


def _cal_dict(pier: str, scale: float = 2.0) -> dict:
    return {"x_rate": 0.0035, "y_rate": 0.0031, "x_angle": 0.7853981634,
            "y_angle": 2.3561944902, "y_angle_error": 0.0,
            "declination": -0.0941, "pier_side": pier,
            "ra_parity": "unknown", "dec_parity": "unknown",
            "rotator_angle": 0.0, "binning": 1, "is_valid": True,
            "image_scale_arcsec": scale}


def _plant(root, profile: str, pier: str, scale: float = 2.0) -> None:
    d = root / "guider"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{profile}.json").write_text(json.dumps(_cal_dict(pier, scale)),
                                       encoding="utf-8")


async def _native(pier: str = "east", profile: str | None = None):
    from astrodeck.devices.base import PierSide
    from astrodeck.devices.sim import build_sim_rig
    from astrodeck.guide.native import NativeGuider

    rig = build_sim_rig()
    cam, tel = rig["guide_camera"], rig["telescope"]
    await cam.connect()
    await tel.connect()

    async def _pier() -> PierSide:
        return PierSide(pier)

    tel.pier_side = _pier
    g = NativeGuider(cam, tel,
                     config={"image_scale_arcsec": 2.0, "exposure_s": 0.2},
                     profile_id=profile or f"needs-cal-{uuid.uuid4().hex[:8]}")
    await g.connect()
    return g


@pytestmark_native
async def test_no_persisted_calibration_means_a_walk(cal_dir):
    g = await _native()
    assert await g.needs_calibration() is True
    await g.disconnect()


@pytestmark_native
async def test_a_usable_persisted_calibration_means_no_walk(cal_dir):
    profile = f"needs-cal-{uuid.uuid4().hex[:8]}"
    _plant(cal_dir, profile, "east")
    g = await _native(pier="east", profile=profile)
    assert await g.needs_calibration() is False
    await g.disconnect()


@pytestmark_native
async def test_a_pier_change_means_a_walk_and_says_nothing_yet(cal_dir,
                                                               monkeypatch):
    """GN-01's refusal is what made the 03:39 restart a walk in the first
    place, so it has to show up here. And the probe must stay QUIET: the start
    itself logs "recalibrating" a moment later, and the same sentence twice
    reads as two decisions."""
    from astrodeck.events import bus

    said: list[str] = []
    real = bus.log
    monkeypatch.setattr(bus, "log", lambda lvl, msg, src="hub": (
        said.append(str(msg)), real(lvl, msg, src))[1])

    profile = f"needs-cal-{uuid.uuid4().hex[:8]}"
    _plant(cal_dir, profile, "east")
    g = await _native(pier="west", profile=profile)
    assert await g.needs_calibration() is True
    assert not any("recalibrating" in s for s in said), said
    await g.disconnect()


@pytestmark_native
async def test_a_cleared_calibration_means_a_walk(cal_dir):
    """The GN-01 discard latch. This is the state the 03:39 restart was in.

    Asserted TWICE, and the second is the one that matters. Deleting the file
    is the easy half; the latch exists because the in-memory calibration
    outlived the delete and the next persist wrote it straight back, so a file
    ON DISK is not evidence that the session has a calibration. The probe must
    agree with ``_persist_calibration``'s own refusal, or the engine would pick
    the 180 s bound for the very restart GN-01 turned into a walk.
    """
    profile = f"needs-cal-{uuid.uuid4().hex[:8]}"
    _plant(cal_dir, profile, "east")
    g = await _native(pier="east", profile=profile)
    assert await g.needs_calibration() is False       # premise
    g.clear_calibration()
    assert await g.needs_calibration() is True
    _plant(cal_dir, profile, "east")                  # a file is back on disk
    assert await g.needs_calibration() is True, (
        "the discard latch is what makes a cleared calibration stay cleared; "
        "a file on disk must not un-discard it")
    await g.disconnect()


@pytestmark_native
async def test_a_calibration_at_the_wrong_binning_means_a_walk(cal_dir):
    """``_cal_reusable`` is the third gate ``start_guiding`` applies, so the
    probe has to apply it too or the two would disagree on exactly the nights
    a rescale would have misguided."""
    profile = f"needs-cal-{uuid.uuid4().hex[:8]}"
    _plant(cal_dir, profile, "east")
    g = await _native(pier="east", profile=profile)
    g._binning = 2
    assert await g.needs_calibration() is True
    await g.disconnect()


# ----------------------------- (c) what a CUT start leaves behind

@pytestmark_native
async def test_a_cut_start_leaves_the_guider_startable_again(cal_dir):
    """A cancelled ``start_guiding`` must leave nothing that blocks the next one.

    The loop task is never created and ``_active`` is never set (both are the
    last lines of the start), and the pulse driver stops the mount on its own
    cancel. ``_phase_hint`` was the one that leaked: the reuse path sets
    "finding" and relied on the SUCCESS line to clear it, so a cut start left
    ``stats().phase`` narrating "finding" forever - and GuideView dims Start,
    Force Recalibrate and Stop on that hint, so the controls that would restart
    guiding after the cut were exactly the ones that went away.
    """
    profile = f"needs-cal-{uuid.uuid4().hex[:8]}"
    _plant(cal_dir, profile, "east")
    g = await _native(pier="east", profile=profile)
    assert await g.needs_calibration() is False       # premise: the reuse path

    first = {"n": 0}
    real_expose = g._expose

    async def _slow_first():
        first["n"] += 1
        if first["n"] == 1:
            await asyncio.sleep(30)
        return await real_expose()

    g._expose = _slow_first
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(g.start_guiding(), 0.4)

    assert g._active is False
    assert g._loop_task is None or g._loop_task.done()
    assert g._phase_hint is None, (
        f"a cut start left the phase hint at {g._phase_hint!r}; the panel "
        f"narrates it and dims the buttons that would recover the rig")
    assert g.stats().phase == "idle"

    # ...and the start that follows really works.
    await asyncio.wait_for(g.start_guiding(), 120.0)
    assert await g.is_active()
    await g.stop_guiding()
    await g.disconnect()
