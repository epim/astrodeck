# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Never expose while something is deliberately moving the mount, and re-centre
after guiding comes back.

Both behaviours were bought with real frames on 2026-08-10. After the meridian
flip the guider's flipped calibration diverged; recovery called
``start_guiding()``, which returns when the loop STARTS rather than when it has
settled, and the engine opened the shutter on the next line — straight into
calibration's deliberate mount pulses. The frames came out with ~35-pixel
diagonal star trails. Meanwhile the field, unguided, walked 128 arcmin out of a
101'x67' frame and nothing re-centred it, because centring only ever ran at
target start and after a flip.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.guide.base import GuideStats
from astrodeck.hub import Hub
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target

pytestmark = pytest.mark.anyio


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    """Same shape as test_sequence.py's fixture: a sim rig with the sun cone
    disarmed (these are mechanics tests, not date-dependent ones) and the fast
    legacy sim guider, since what is under test is the ENGINE's ordering rather
    than the native guider's calibration walk."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    _safety = hub_module.config_store.cfg().safety
    monkeypatch.setattr(_safety, "solar_avoidance", False)
    monkeypatch.setenv("ASTRODECK_SIM_LEGACY_GUIDER", "1")
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _phase(hub, value: str):
    """Make the guider report ``value`` as its narration phase."""
    hub.guider.stats = lambda: GuideStats(guiding=(value == "guiding"), phase=value)


def _target(**kw):
    base = dict(name="NGC 6946", ra_hours=20.5808, dec_deg=60.1539, center=True,
                autofocus_first=False,
                steps=[ExposureStep(filter="L", exposure_s=0.01, count=1)])
    base.update(kw)
    return Target(**base)


async def test_the_shutter_waits_while_the_guider_is_calibrating(sim_hub, monkeypatch):
    monkeypatch.setattr(engine_mod, "GUIDE_QUIET_POLL_S", 0.01)
    engine = SequenceEngine(sim_hub)
    engine.plan = SequencePlan(guide=True, recover_guiding=True)
    _phase(sim_hub, "calibrating")

    waiter = asyncio.ensure_future(engine._await_guider_quiet("test", timeout_s=5.0))
    await asyncio.sleep(0.05)
    assert not waiter.done(), "opened the shutter while the mount was being pulsed"

    _phase(sim_hub, "guiding")                     # calibration finishes
    assert await asyncio.wait_for(waiter, timeout=2.0) is True


async def test_a_guider_stuck_calibrating_does_not_freeze_the_night(sim_hub, monkeypatch):
    """The 2026-08-10 case: the guide star was too faint, so calibration never
    converged. Waiting forever would have cost the whole night instead of some
    frames — the wait is bounded and says so."""
    monkeypatch.setattr(engine_mod, "GUIDE_QUIET_POLL_S", 0.01)
    engine = SequenceEngine(sim_hub)
    engine.plan = SequencePlan(guide=True, recover_guiding=True)
    _phase(sim_hub, "calibrating")

    got = await asyncio.wait_for(
        engine._await_guider_quiet("test", timeout_s=0.05), timeout=2.0)
    assert got is False


async def test_guiding_and_finding_are_not_treated_as_mount_motion(sim_hub, monkeypatch):
    """Guiding's job is to hold the star still and finding only reads frames.
    Blocking on either would stall every frame of a healthy run."""
    monkeypatch.setattr(engine_mod, "GUIDE_QUIET_POLL_S", 0.01)
    engine = SequenceEngine(sim_hub)
    engine.plan = SequencePlan(guide=True)
    for phase in ("guiding", "finding", "idle"):
        _phase(sim_hub, phase)
        assert await asyncio.wait_for(
            engine._await_guider_quiet("test", timeout_s=0.2), timeout=1.0) is True


def test_an_unguided_stretch_is_bounded_by_one_frame():
    """WHAT BOUNDS THE DAMAGE (issue #27).

    The report is 25 minutes of exposure with the guider at `phase=idle` and no
    recovery attempted. Every case in this file drives `_maybe_recover_guiding`
    DIRECTLY, so between them they prove the recovery does the right thing and
    say nothing at all about how often it is asked - and "how often" is the
    entire complaint. A recovery that works perfectly and is consulted once a
    target is indistinguishable from the night in that report.

    It is consulted at the frame boundary, before the exposure, so the worst
    case is ONE frame of unguided sky. That is the ceiling the issue asks for,
    and it is a property of where the call sits rather than of what it does.

    Asserted on source order because that is what the claim is about. Driving a
    run would prove the recovery fires, which the cases below already do; it
    would not prove there is no path through the loop that skips it.

    MUTATION: move the `_maybe_recover_guiding` call below `await self._capture`.
    Observed: this fails - the guider is consulted after the frame it was
    supposed to protect.
    MUTATION: delete the call. Observed: this fails on the first assertion, and
    every other case in this file stays green, because they all call the method
    themselves.
    """
    import inspect
    src = inspect.getsource(SequenceEngine._run_step)
    recover = src.find("_maybe_recover_guiding")
    capture = src.find("await self._capture")
    assert recover != -1, (
        "the frame loop no longer consults guiding recovery at all; an "
        "unguided stretch is then bounded by nothing")
    assert capture != -1, "this case can no longer find the exposure it guards"
    assert recover < capture, (
        "guiding recovery is consulted AFTER the exposure, so every frame is "
        "taken on last frame's answer")


async def test_recovery_recentres_BEFORE_it_resumes_guiding(sim_hub, monkeypatch):
    """Order is the point. Re-centring slews, so doing it after start_guiding
    would tear down the guiding we had just paid to re-establish."""
    monkeypatch.setattr(engine_mod, "GUIDE_QUIET_POLL_S", 0.01)
    engine = SequenceEngine(sim_hub)
    engine.plan = SequencePlan(guide=True, recover_guiding=True)
    calls: list[str] = []

    async def fake_center(ra, dec, rotation_deg=None):
        calls.append("center")
    async def fake_start():
        calls.append("guide")
        sim_hub.guider._guiding = True
    monkeypatch.setattr(sim_hub, "goto_and_center", fake_center)
    monkeypatch.setattr(sim_hub.guider, "start_guiding", fake_start)

    sim_hub.guider._guiding = False                 # guiding is down
    _phase(sim_hub, "idle")
    await engine._maybe_recover_guiding(_target())

    assert calls == ["center", "guide"], f"wrong order: {calls}"


async def test_a_target_that_opted_out_of_centring_is_not_recentred(sim_hub, monkeypatch):
    """``center=False`` is an existing statement of intent. Recovery honours it
    rather than inventing new policy — a mosaic panel or a deliberately offset
    framing must not be silently re-pointed at the catalogue position."""
    monkeypatch.setattr(engine_mod, "GUIDE_QUIET_POLL_S", 0.01)
    engine = SequenceEngine(sim_hub)
    engine.plan = SequencePlan(guide=True, recover_guiding=True)
    centred = []
    async def fake_center(ra, dec, rotation_deg=None):
        centred.append(True)
    monkeypatch.setattr(sim_hub, "goto_and_center", fake_center)

    sim_hub.guider._guiding = False
    _phase(sim_hub, "idle")
    await engine._maybe_recover_guiding(_target(center=False))
    assert centred == []


async def test_a_calibration_target_is_never_recentred(sim_hub, monkeypatch):
    """Darks/bias/flats carry dummy coordinates and never slew. Re-centring one
    would point the mount at (0,0) in the middle of a dark library."""
    monkeypatch.setattr(engine_mod, "GUIDE_QUIET_POLL_S", 0.01)
    engine = SequenceEngine(sim_hub)
    engine.plan = SequencePlan(guide=True, recover_guiding=True)
    centred = []
    async def fake_center(ra, dec, rotation_deg=None):
        centred.append(True)
    monkeypatch.setattr(sim_hub, "goto_and_center", fake_center)

    sim_hub.guider._guiding = False
    _phase(sim_hub, "idle")
    await engine._maybe_recover_guiding(_target(calibration=True, center=True))
    assert centred == []


async def test_healthy_guiding_neither_recentres_nor_restarts(sim_hub, monkeypatch):
    """The gate runs before EVERY frame. A run that is guiding fine must pay
    nothing at all — no slew, no restart."""
    engine = SequenceEngine(sim_hub)
    engine.plan = SequencePlan(guide=True, recover_guiding=True)
    touched = []
    async def fake_center(ra, dec, rotation_deg=None):
        touched.append("center")
    async def fake_start():
        touched.append("guide")
    monkeypatch.setattr(sim_hub, "goto_and_center", fake_center)
    monkeypatch.setattr(sim_hub.guider, "start_guiding", fake_start)

    sim_hub.guider._guiding = True                  # healthy
    await engine._maybe_recover_guiding(_target())
    assert touched == []


async def test_a_failed_recentre_still_resumes_guiding(sim_hub, monkeypatch):
    """A re-centre that cannot solve leaves the mount exactly where it already
    was, which is where it would have been without this block. Losing guiding
    as well would turn one degraded frame into a dead night."""
    monkeypatch.setattr(engine_mod, "GUIDE_QUIET_POLL_S", 0.01)
    engine = SequenceEngine(sim_hub)
    engine.plan = SequencePlan(guide=True, recover_guiding=True)
    started = []
    async def boom(ra, dec, rotation_deg=None):
        raise RuntimeError("plate solve failed: no solution")
    async def fake_start():
        started.append(True)
        sim_hub.guider._guiding = True
    monkeypatch.setattr(sim_hub, "goto_and_center", boom)
    monkeypatch.setattr(sim_hub.guider, "start_guiding", fake_start)

    sim_hub.guider._guiding = False
    _phase(sim_hub, "idle")
    await engine._maybe_recover_guiding(_target())
    assert started == [True]


# ------------------------------------------------------------------- #72
# The recovery loop had no bound. On 2026-09-19 it ran 2.5 hours and 18
# losses on NGC 7129; on 2026-09-20 it ran on NGC 7331 from a calibration 28
# degrees out of square. Throughout, the sequence said `running` and one
# trailed frame landed per cycle, so a supervisor watching state saw health
# and a supervisor watching the frame COUNT saw progress.

async def test_recovery_is_bounded_and_counts_attempts_not_failures(
        sim_hub, monkeypatch):
    """The bound counts ATTEMPTS since the last banked frame.

    Counting failures would have read zero all night: on 2026-09-20 recovery
    kept succeeding -- "native guider calibrated and guiding" -- and losing
    the star again minutes later. start_guiding below therefore SUCCEEDS
    every time, exactly as it did on the rig, and the guider goes down again
    before the next pass.

    MUTATION: delete the `if self._guiding_recoveries >=
    _MAX_GUIDING_RECOVERIES:` block from _maybe_recover_guiding. Observed
    under it: attempts keeps climbing past the bound (6 of 6 passes recover)
    and the first assertion fails.
    """
    monkeypatch.setattr(engine_mod, "GUIDE_QUIET_POLL_S", 0.01)
    engine = SequenceEngine(sim_hub)
    engine.plan = SequencePlan(guide=True, recover_guiding=True)
    attempts: list[int] = []

    async def fake_center(ra, dec, rotation_deg=None):
        pass

    async def fake_start():
        attempts.append(1)
        sim_hub.guider._guiding = True      # recovery SUCCEEDS, as it did

    monkeypatch.setattr(sim_hub, "goto_and_center", fake_center)
    monkeypatch.setattr(sim_hub.guider, "start_guiding", fake_start)

    for _ in range(6):
        sim_hub.guider._guiding = False     # ...and the star is lost again
        _phase(sim_hub, "idle")
        await engine._maybe_recover_guiding(_target())

    assert len(attempts) == engine_mod._MAX_GUIDING_RECOVERIES, (
        f"{len(attempts)} recovery attempts without a frame; the bound is "
        f"{engine_mod._MAX_GUIDING_RECOVERIES}")
    assert engine.state.get("detail") == "guiding lost; recovery stood down", (
        f"the run does not say it stood down: {engine.state.get('detail')!r}")


async def test_a_banked_frame_clears_the_recovery_bound(sim_hub, monkeypatch):
    """Clearing the counter re-arms recovery, so the bound is per-drought and
    not per-run. Without that, one bad patch of cloud early on would exhaust
    the bound and leave the rest of a long night unguided.

    SCOPE, stated because the test name could promise more than it delivers:
    this drives the re-arm by setting the counter directly, the way the
    frame-accept path does. It does NOT exercise that path, which lives deep
    in the capture loop; deleting the reset line there would leave this test
    green. What it does pin is the DESIGN choice of a clearable counter over a
    one-way latch, which was the tempting simpler implementation.

    MUTATION: make the stand-down unconditional (`if True:` in place of the
    bound comparison), the degenerate a latch collapses to. Observed: the
    second burst attempts nothing and the final assertion fails 2 != 4.
    """
    monkeypatch.setattr(engine_mod, "GUIDE_QUIET_POLL_S", 0.01)
    engine = SequenceEngine(sim_hub)
    engine.plan = SequencePlan(guide=True, recover_guiding=True)
    attempts: list[int] = []

    async def fake_center(ra, dec, rotation_deg=None):
        pass

    async def fake_start():
        attempts.append(1)
        sim_hub.guider._guiding = True

    monkeypatch.setattr(sim_hub, "goto_and_center", fake_center)
    monkeypatch.setattr(sim_hub.guider, "start_guiding", fake_start)

    for _ in range(4):
        sim_hub.guider._guiding = False
        _phase(sim_hub, "idle")
        await engine._maybe_recover_guiding(_target())
    assert len(attempts) == engine_mod._MAX_GUIDING_RECOVERIES

    engine._guiding_recoveries = 0          # what banking a frame does

    for _ in range(4):
        sim_hub.guider._guiding = False
        _phase(sim_hub, "idle")
        await engine._maybe_recover_guiding(_target())
    assert len(attempts) == 2 * engine_mod._MAX_GUIDING_RECOVERIES, (
        "a banked frame did not re-arm recovery, so one bad patch of cloud "
        "ends guiding for the rest of the night")


# ------------------------------------------------------------------ #108
async def test_a_starting_run_always_publishes_a_frame_counter(sim_hub, monkeypatch):
    """A supervisor must be able to tell "no frames yet" from "no counter".

    `start()` replaces the published state wholesale, and `_run` does not
    publish a progress block until after the safety gates, the slew, the
    autofocus and the plate solve. For those minutes GET /api/sequence/state
    carried no progress.frames_done at all. My night supervisor read that
    absence as a counter that had not moved, called it a stall, and aborted a
    healthy NGC 7331 run on 2026-09-19.

    MUTATION: restore `self.state = {"state": "idle"}` without the progress
    key. Observed: KeyError 'progress' on the first assertion.
    """
    engine = SequenceEngine(sim_hub)
    engine.plan = SequencePlan(guide=False, recover_guiding=False)

    # Never let the task run: this is about the window BEFORE its first turn,
    # which is the window the supervisor polled into.
    async def never(*_a, **_k):
        await asyncio.sleep(3600)

    monkeypatch.setattr(engine, "_run", never)
    engine.start(SequencePlan(guide=False, recover_guiding=False))
    try:
        assert "progress" in engine.state, (
            "a run that has been started publishes no progress block at all, "
            "so a supervisor cannot distinguish 'no frames yet' from "
            "'no counter'")
        assert engine.state["progress"]["frames_done"] == 0
    finally:
        if engine._task:
            engine._task.cancel()
