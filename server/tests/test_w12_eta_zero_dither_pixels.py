# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A 0 px dither must not budget a settle wait in the ETA (#560 WP-58
follow-on, found by WP-58's own verifier; backlog wave 12).

WP-58 made the engine SKIP a 0 px dither outright at both call sites -- no
guider call, no settle wait (``self._policy.dither_pixels > 0`` gates both
the cadence block and the ``dither`` instruction action). But
``SequenceEngine.compute_eta`` kept computing ``dithers_remaining`` from
``dither_every`` alone, with no look at ``self._policy.dither_pixels``, so a
run with ``dither_every`` set and ``dither_pixels`` 0 still budgeted
``DITHER_COST_S`` (or a measured mean) for every dither the cadence implies,
even though none of them will ever fire. The ETA counted down slower than
the run could possibly finish.

Fix: ``compute_eta`` computes ``dithers_remaining`` as 0 whenever
``self._policy.dither_pixels <= 0``, the same gate WP-58 put at both dither
call sites, so a dither that will never happen never costs anything and
never withholds confidence on being "measured" either.
"""
from __future__ import annotations

import pytest

from _simhub import a_real_site

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target
from astrodeck.sequence.engine import DITHER_COST_S


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    # Mirrors test_monitor_telemetry.py's sim_hub: a fresh Hub on the sim rig
    # with the sun-exclusion cone disarmed (fixed M42 target, date-independent)
    # and a real site (#24) so meridian/window geometry has no 0,0 surprises.
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    a_real_site(monkeypatch)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


def _plan(*, dither_pixels: float, dither_every: int = 2,
         count: int = 10) -> SequencePlan:
    """One target, ``count`` frames, no guide start (so no guider need be
    wired), a dither cadence of ``dither_every`` and the distance under
    test. No autofocus cadence and ``meridian_flip`` never fires immediately
    after ``start()`` (``hub.last_meridian`` is still None), so the only
    event-cost term in play is the dither one."""
    return SequencePlan(
        name="eta-dither",
        targets=[Target(
            name="M42", ra_hours=5.5881, dec_deg=-5.3911,
            center=False, autofocus_first=False,
            steps=[ExposureStep(filter="Ha", exposure_s=0.05, gain=100,
                                count=count)],
        )],
        guide=False, dither_every=dither_every, dither_pixels=dither_pixels,
        autofocus_every=0,
    )


async def test_a_zero_px_dither_budgets_no_eta_cost(sim_hub):
    """10 frames, dither every 2: the cadence implies 5 dithers, but
    ``dither_pixels=0`` means WP-58 skips every one of them. The ETA must
    budget none of their cost.

    RED under the named mutant "the new guard removed" (``dithers_remaining``
    reverted to ``(frames_remaining // d_every) if d_every else 0``, with no
    look at ``dither_pixels``), observed:

        AssertionError: a 0 px dither budgeted 40s of dither cost the
        engine will never pay
    """
    engine = SequenceEngine(sim_hub)
    engine.start(_plan(dither_pixels=0.0))
    eta = engine.compute_eta()
    assert eta["events_cost_s"] == 0, (
        f"a 0 px dither budgeted {eta['events_cost_s']}s of dither cost "
        f"the engine will never pay")
    await engine.abort()


async def test_control_a_nonzero_dither_still_budgets_its_cost(sim_hub):
    """CONTROL: the same cadence with a real dither distance still prices
    all 5 dithers at the seed cost -- the guard above must silence only a
    dither that will not fire, never one that will.
    """
    engine = SequenceEngine(sim_hub)
    engine.start(_plan(dither_pixels=3.0))
    eta = engine.compute_eta()
    assert eta["events_cost_s"] == pytest.approx(5 * DITHER_COST_S)
    await engine.abort()
