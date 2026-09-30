"""The #72 guiding-recovery bound must survive an unguided banked frame
(#134; backlog ruling WP-01 (c), owner-approved 2026-09-30).

`_record_frame` used to reset `_guiding_recoveries` on ANY banked frame,
rejected-but-kept ones included. On 2026-09-23 the mount's serial link died
(#133) and the run kept exposing: each cycle made one recovery
attempt (`_maybe_recover_guiding`), which failed because `start_guiding`
raised the mount's own transport error, then banked one unguided, trailed
frame under `hfr_reject_action = "warn"` — and banking that frame reset the
counter the failed attempt had just spent. The durable log shows 62
"attempting recovery (1/2)" lines and zero "(2/2)"s across 2.5 hours; the
stand-down `_maybe_recover_guiding` exists to reach never ran. A banked
frame is evidence an exposure finished, not evidence recovery worked.

Reproduced below without the mount or the serial link: `start_guiding`
fails every time, and each cycle banks one frame through the real
`_record_frame` — never by setting `_guiding_recoveries` by hand, so
`_frame_was_guided` (the new evidence check) is exercised too, not only the
conditional it feeds.
"""
from __future__ import annotations

import pytest

import astrodeck.hub as hub_module
import astrodeck.sequence.engine as engine_mod
from astrodeck.hub import Hub
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target

STOOD_DOWN = "guiding lost; recovery stood down"


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    """Same shape as test_engine_guide_quiet.py's fixture: a sim rig with the
    sun cone disarmed (this is a mechanics test, not a date-dependent one)
    and the fast legacy sim guider (`ASTRODECK_SIM_LEGACY_GUIDER`), since
    what is under test is `_record_frame`'s own conditional, not the native
    guider's calibration walk."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    _safety = hub_module.config_store.cfg().safety
    monkeypatch.setattr(_safety, "solar_avoidance", False)
    monkeypatch.setenv("ASTRODECK_SIM_LEGACY_GUIDER", "1")
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _target(**kw) -> Target:
    # center=False: this test is about the recovery bound and the record-frame
    # reset, not about re-centring, so the mount stays put and no goto is made.
    base = dict(name="NGC 6946", ra_hours=20.5808, dec_deg=60.1539, center=False,
               autofocus_first=False,
               steps=[ExposureStep(filter="L", exposure_s=0.01, count=1)])
    base.update(kw)
    return Target(**base)


def _engine(sim_hub) -> SequenceEngine:
    engine = SequenceEngine(sim_hub)
    engine.plan = SequencePlan(guide=True, recover_guiding=True)
    return engine


async def test_recovery_bound_survives_unguided_frames(sim_hub, monkeypatch):
    """`start_guiding` fails every time (the #133 shape: a device transport
    error), so the guider is inactive at every capture. Six cycles of
    (attempt recovery, bank one trailed frame) must reach the bound in
    exactly `_MAX_GUIDING_RECOVERIES` attempts and stand down — not run all
    six, which is what an unconditional reset in `_record_frame` gives.

    Mutant "the original unconditional reset" (`_record_frame`'s
    ``if guided: self._guiding_recoveries = 0`` made an unconditional
    ``self._guiding_recoveries = 0``): RED -
        AssertionError: 6 recovery attempts without a frame; the bound is 2
    """
    engine = _engine(sim_hub)
    target = _target()
    step = target.steps[0]
    key = f"{target.id}:{step.id}"
    attempts: list[int] = []

    async def fails():
        attempts.append(1)
        raise RuntimeError("WriteFile failed (device does not recognize the "
                           "command)")

    monkeypatch.setattr(sim_hub.guider, "start_guiding", fails)

    for i in range(6):
        sim_hub.guider._guiding = False           # the star is lost
        await engine._maybe_recover_guiding(target)
        # One trailed, UNGUIDED frame banked per cycle regardless — exactly
        # the shape `hfr_reject_action = "warn"` produced on the rig, six
        # cycles straight through whether or not the bound has already
        # engaged. Driven through the real `_record_frame`/
        # `_frame_was_guided`, never by setting the counter by hand.
        guided = await engine._frame_was_guided()
        assert guided is False, "premise: the guider is down at capture time"
        engine._record_frame(key, i, target, step, {}, accepted=False,
                             guided=guided)

    assert len(attempts) == engine_mod._MAX_GUIDING_RECOVERIES, (
        f"{len(attempts)} recovery attempts without a frame; the bound is "
        f"{engine_mod._MAX_GUIDING_RECOVERIES}")
    assert engine.state.get("detail") == STOOD_DOWN, (
        f"the run does not say it stood down: {engine.state.get('detail')!r}")


async def test_control_a_guided_banked_frame_still_rearms_recovery(sim_hub,
                                                                    monkeypatch):
    """CONTROL. The same shape, but the guider is ACTIVE at the frame that
    gets banked: that frame is real evidence recovery held, and must still
    re-arm the bound (`_frame_was_guided` is a gate, not a one-way latch).
    Driven through `_record_frame`, not by setting the counter by hand
    (test_engine_guide_quiet.py's `test_a_banked_frame_clears_the_recovery_
    bound` already pins the by-hand shape; this pins the real evidence
    path)."""
    engine = _engine(sim_hub)
    target = _target()
    step = target.steps[0]
    key = f"{target.id}:{step.id}"

    async def fails():
        raise RuntimeError("no guide star found")

    monkeypatch.setattr(sim_hub.guider, "start_guiding", fails)

    for i in range(2):
        sim_hub.guider._guiding = False
        await engine._maybe_recover_guiding(target)
    assert engine._guiding_recoveries == engine_mod._MAX_GUIDING_RECOVERIES, (
        "premise: two failed attempts must reach the bound")

    # The guider comes back on its own (a re-lock, or the next target's hop)
    # and THIS frame is exposed with it active throughout.
    sim_hub.guider._guiding = True
    guided = await engine._frame_was_guided()
    assert guided is True, "premise: the guider is active at capture time"
    engine._record_frame(key, 99, target, step, {}, guided=guided)
    assert engine._guiding_recoveries == 0, (
        "a frame exposed with the guider active throughout did not re-arm "
        "recovery")

    for i in range(2):
        sim_hub.guider._guiding = False
        await engine._maybe_recover_guiding(target)
    assert engine._guiding_recoveries == engine_mod._MAX_GUIDING_RECOVERIES, (
        "the re-armed bound was not reachable a second time")
