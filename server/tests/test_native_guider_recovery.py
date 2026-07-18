"""P2 gate (p2-t2-brief.md): star-lost recovery, mid-session
``flip_calibration``, and calibration-persistence reuse, all against the sim
rig (dossier §3.3 star-lost/recovery, §9 item 4 flip, §8.4/§9 calibration
reuse). Threshold-based assertions only — no wall-clock-anchored PE
comparisons. Fixture shape mirrors ``test_native_guider_e2e.py``/
``test_native_guider_dither.py``.
"""
from __future__ import annotations

import asyncio
import math
import time
import uuid

import pytest
from astrodeck.devices.sim import build_sim_rig
from astrodeck.providers import NATIVE_AVAILABLE

pytestmark = pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native wheel absent")


def _profile_id(tag: str) -> str:
    """A fresh profile id per test invocation. ``CONFIG_DIR/guider/`` is
    gitignored but NOT test-isolated (P1's ``_persist_calibration`` writes to
    the real ``CONFIG_DIR``), so leftover files from a previous run could
    otherwise satisfy the P2 reuse-compatibility gate with stale numbers and
    change a scenario's timing out from under it."""
    return f"test-recovery-{tag}-{uuid.uuid4().hex[:8]}"


async def _wait_until(predicate, timeout: float, interval: float = 0.2) -> bool:
    """Poll ``predicate`` (a zero-arg callable) until it's truthy or
    ``timeout`` seconds elapse. Returns whether it became true."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return bool(predicate())


@pytest.mark.asyncio
async def test_star_lost_recovery_reacquires_and_resumes():
    """Scenario 1: inject a cloud (blank the guide star), assert the guider
    reports not-active, then — mimicking the sequence engine's
    ``_maybe_recover_guiding`` contract (``engine.py:1908-1924``: poll
    ``is_active()``, call ``start_guiding()`` again on a real loss) — restore
    the star and restart; guiding must resume, FAST (the persisted
    calibration reused rather than a full recalibration walk)."""
    from astrodeck.guide.native import NativeGuider

    rig = build_sim_rig()
    cam, tel, srig = rig["guide_camera"], rig["telescope"], rig["_rig"]
    await cam.connect()
    await tel.connect()
    g = NativeGuider(cam, tel, config={"image_scale_arcsec": 2.0,
                                       "exposure_s": 0.2},
                     profile_id=_profile_id("lost"))
    await g.connect()
    await asyncio.wait_for(g.start_guiding(), timeout=120.0)
    assert await g.is_active()
    await asyncio.sleep(1.0)  # a few converged frames before the loss

    # Inject the cloud: SimGuideCamera renders no star (T9 field idiom —
    # SimRig.guide_star_hidden, opt-in, off by default) from here on.
    srig.guide_star_hidden = True

    # The engine tolerates a locally-missing star for LOST_STAR_TIMEOUT_S
    # (dossier §13, 20s) before it ever reports lock_lost, then
    # NativeGuider's own bounded reacquire budget (_REACQUIRE_BUDGET
    # consecutive star_lost frames) runs out shortly after — THAT is the
    # "not-active" transition _maybe_recover_guiding polls for.
    became_inactive = await _wait_until(lambda: not g.stats().guiding,
                                        timeout=60.0)
    assert became_inactive, "expected guiding to report inactive after sustained star loss"
    assert not await g.is_active()

    # The cloud clears. Recovery is an explicit restart (the guide loop task
    # itself exited when the budget ran out) — the sequence engine's job,
    # mimicked directly here.
    srig.guide_star_hidden = False
    t0 = time.monotonic()
    await asyncio.wait_for(g.start_guiding(), timeout=60.0)
    elapsed = time.monotonic() - t0
    assert await g.is_active()
    assert g.stats().guiding
    # A fresh calibration walk in this sim is dozens of real pulse_guide legs
    # (tens of real seconds — see test_persisted_calibration_reused_across_
    # guider_instances for a direct pulse-count proof); reuse must be far
    # faster than that (dossier §8.4/§9 reuse-if-compatible).
    assert elapsed < 15.0, f"expected a fast reuse-based restart, took {elapsed:.1f}s"

    await g.stop_guiding()
    await g.disconnect()


@pytest.mark.asyncio
async def test_flip_calibration_mid_session_keeps_guiding_bounded():
    """Scenario 2: after start_guiding, flip the calibration mid-session
    (the guider-level contract hub.meridian_flip calls, hub.py:1853-1863) and
    assert guiding resumes without a runaway — the sim doesn't physically
    model a pier flip, so this is a stability/contract check (dossier §9
    item 4: flip_calibration must complete cleanly and guiding must keep
    running and reporting finite, bounded stats), not a reconvergence check."""
    from astrodeck.guide.native import NativeGuider

    rig = build_sim_rig()
    cam, tel = rig["guide_camera"], rig["telescope"]
    await cam.connect()
    await tel.connect()
    g = NativeGuider(cam, tel, config={"image_scale_arcsec": 2.0,
                                       "exposure_s": 0.2},
                     profile_id=_profile_id("flip"))
    await g.connect()
    await asyncio.wait_for(g.start_guiding(), timeout=120.0)
    assert await g.is_active()
    await asyncio.sleep(1.0)

    ok = await g.flip_calibration()
    assert ok is True, "flip_calibration must report success on a valid calibration"

    # Continued frames after the flip: the loop must keep running (no crash,
    # no unbounded blowup — well under the 20s star-lost grace period, so a
    # "not found" excursion here cannot yet trip star-lost recovery).
    await asyncio.sleep(2.0)
    st = g.stats()
    assert st.guiding, f"expected guiding to still be reported active after the flip, got {st}"
    assert math.isfinite(st.rms_total), f"rms_total is not finite: {st.rms_total}"
    assert st.rms_total < 200.0, f"rms blew up unboundedly: {st.rms_total}"

    await g.stop_guiding()
    await g.disconnect()


@pytest.mark.asyncio
async def test_persisted_calibration_reused_across_guider_instances():
    """Scenario 3: a NEW NativeGuider (fresh in-process engine, no shared
    state) against the SAME profile_id reuses the persisted calibration —
    proven by a pulse_guide spy recording ZERO calls during start_guiding()
    (a calibration walk is dozens of pulse_guide legs; the reuse path issues
    none of them — start_guiding() returns before the guide loop task, the
    only other pulse_guide caller, has even been scheduled)."""
    from astrodeck.guide.native import NativeGuider

    rig = build_sim_rig()
    cam, tel = rig["guide_camera"], rig["telescope"]
    await cam.connect()
    await tel.connect()
    profile = _profile_id("persist")

    g1 = NativeGuider(cam, tel, config={"image_scale_arcsec": 2.0,
                                        "exposure_s": 0.2}, profile_id=profile)
    await g1.connect()
    await asyncio.wait_for(g1.start_guiding(), timeout=120.0)
    assert await g1.is_active()
    await g1.stop_guiding()
    await g1.disconnect()

    g2 = NativeGuider(cam, tel, config={"image_scale_arcsec": 2.0,
                                        "exposure_s": 0.2}, profile_id=profile)
    await g2.connect()

    pulses: list[tuple[str, int]] = []
    real_pulse_guide = tel.pulse_guide

    async def _spy_pulse_guide(direction, ms):
        pulses.append((direction, ms))
        return await real_pulse_guide(direction, ms)

    tel.pulse_guide = _spy_pulse_guide
    t0 = time.monotonic()
    try:
        await asyncio.wait_for(g2.start_guiding(), timeout=60.0)
        elapsed = time.monotonic() - t0
    finally:
        # Restore synchronously (no `await` since) — before the just-spawned
        # loop task has had any chance to run and call pulse_guide itself.
        tel.pulse_guide = real_pulse_guide

    assert await g2.is_active()
    assert pulses == [], (
        f"expected zero pulse_guide calls during a reuse-based start_guiding "
        f"(a calibration walk would issue dozens), got {len(pulses)}: {pulses[:5]}")
    assert elapsed < 15.0, f"expected a fast reuse-based start, took {elapsed:.1f}s"

    await g2.stop_guiding()
    await g2.disconnect()
