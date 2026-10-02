# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A fresh acquisition starts the dither cadence over (#163, spec 5.6 step 9).

`_frames_since_dither` counted frames across targets: target B inherited
however many frames A had shot since A's last dither. A fresh slew and centre
is already a new pointing, so B's first frames paid for a dither the hop had
made pointless, and B's cadence ran out of step with its own frames.
`_setup_target` now resets the counter on every acquisition.

The acceptance scenario as written, "dither every 3 frames, A shoots 2, and
B's first frame spends no dither", CANNOT FAIL on its own: the counter
arrives at B's first frame holding 2, and 2 is under the threshold of 3, so
B's first frame never dithered even before the fix. Under the "no reset"
mutant it stays green. The defect in that scenario is B's SECOND frame, which
dithered after one frame at the new pointing. So that case asserts the whole
order, and a second case has A shoot a full cadence, the scenario in which B's
first frame itself used to dither.

Real engine runs on the sim rig with a recording guider double: plan.guide is
off, so no guide start runs, and a connected guider is all the dither gate
asks for.
"""
from __future__ import annotations

import asyncio

import pytest

import astrodeck.hub as hub_module
from astrodeck.devices.sim import SimTelescope
from astrodeck.guide.base import Guider, GuideStats
from astrodeck.hub import Hub
from astrodeck.sequence import ExposureStep, SequenceEngine, SequencePlan, Target


@pytest.fixture
async def sim_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(hub_module.config_store.cfg().safety,
                        "solar_avoidance", False)
    monkeypatch.setattr(SimTelescope, "SLEW_RATE_DEG_S", 1.0e6)
    h = Hub()
    await h.connect_sim()
    yield h
    await h.disconnect_all()


class _DitherGuider(Guider):
    """Connected, never active, records each dither into the shared log."""

    name = "dither recorder"

    def __init__(self, log: list[str]) -> None:
        self.connected = True
        self.log = log

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def start_guiding(self) -> None: ...

    async def stop_guiding(self) -> None: ...

    async def is_active(self) -> bool:
        return False

    async def dither(self, pixels: float = 3.0, settle=None) -> None:
        self.log.append("dither")

    def stats(self) -> GuideStats:
        return GuideStats()


def _target(name: str, ra: float, *steps: tuple[str, int]) -> Target:
    return Target(name=name, ra_hours=ra, dec_deg=20.0, center=False,
                  autofocus_first=False,
                  steps=[ExposureStep(filter=f, exposure_s=0.02, count=n)
                         for f, n in steps])


async def _run(hub, monkeypatch, targets, *, dither_every: int) -> list[str]:
    """Run the plan to completion; return frames and dithers in order. A
    frame is logged as its target name and filter, e.g. ``"A:L"``."""
    log: list[str] = []
    hub.guider = _DitherGuider(log)
    e = SequenceEngine(hub)
    real_capture = e._capture

    async def capture(step, target, **kw):
        if target is not None and kw.get("save", True):
            log.append(f"{target.name}:{step.filter}")
        return await real_capture(step, target, **kw)

    monkeypatch.setattr(e, "_capture", capture)
    e.start(SequencePlan(name="dither", guide=False, meridian_flip=False,
                         safety_check=False, autofocus_every=0,
                         dither_every=dither_every, targets=targets))
    await asyncio.wait_for(asyncio.shield(e._task), 60)
    assert e.state.get("state") == "complete", (
        f"premise: the run finished: {e.state.get('state')} "
        f"{e.state.get('detail')} {log}")
    return log


async def test_a_full_cadence_on_a_does_not_spend_bs_first_frame(
        sim_hub, monkeypatch):
    """Dither every 3; A shoots 3, so the counter is AT the threshold when
    the run hops. B's first frame must not dither: the hop re-pointed.

    Mutant "no reset" (the `_frames_since_dither = 0` in `_setup_target`
    deleted): RED -
        AssertionError: B's first frame spent a dither the hop had already
        paid for: ['A:L', 'A:L', 'A:L', 'dither', 'B:L', 'B:L', 'B:L']
    """
    log = await _run(sim_hub, monkeypatch,
                     [_target("A", 6.00, ("L", 3)), _target("B", 6.05, ("L", 3))],
                     dither_every=3)
    assert log == ["A:L", "A:L", "A:L", "B:L", "B:L", "B:L"], (
        f"B's first frame spent a dither the hop had already paid for: {log}")


async def test_bs_cadence_counts_bs_own_frames(sim_hub, monkeypatch):
    """The acceptance scenario, graded on its whole order: dither every 3, A
    shoots 2. B dithers after its own third frame, not after its first.

    Mutant "no reset": RED -
        AssertionError: B's cadence counted A's frames: ['A:L', 'A:L', 'B:L',
        'dither', 'B:L', 'B:L', 'B:L']
    """
    log = await _run(sim_hub, monkeypatch,
                     [_target("A", 6.00, ("L", 2)), _target("B", 6.05, ("L", 4))],
                     dither_every=3)
    assert log.index("B:L") == 2 and "dither" not in log[:3], (
        f"premise: B's first frame spent no dither: {log}")
    assert log == ["A:L", "A:L", "B:L", "B:L", "B:L", "dither", "B:L"], (
        f"B's cadence counted A's frames: {log}")


async def test_control_the_cadence_within_one_target_is_unchanged(
        sim_hub, monkeypatch):
    """CONTROL. One target, two filters: the cadence runs across the filter
    change exactly as it always has. Only an acquisition resets it.

    Mutant "reset before every frame" (``self._frames_since_dither = 0``
    added just above the dither check in `_run_steps`): RED -
        AssertionError: the cadence inside one target moved: ['A:L', 'A:L',
        'A:L', 'A:L', 'A:R', 'A:R', 'A:R']
    """
    log = await _run(sim_hub, monkeypatch,
                     [_target("A", 6.00, ("L", 4), ("R", 3))], dither_every=3)
    assert log == ["A:L", "A:L", "A:L", "dither", "A:L", "A:R", "A:R",
                   "dither", "A:R"], (
        f"the cadence inside one target moved: {log}")
