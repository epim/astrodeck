# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP-37 (c) / #194: a combined cover+calibrator (a flip-flat, whose light
panel is the underside of the cover itself) must have its cover CLOSED
before the panel is switched on for a flat, not opened after.

Before the fix, ``_run_calibration``'s flat-auto block (engine.py) called
``calibrator_on`` and then, when the device had a cover, ``open_cover`` --
exactly backwards for a flip-flat, which needs the cover closed over the
aperture with the panel lit through it. Opening the cover after lighting the
panel points the lit panel away from the aperture: at night a dark sky, at
dusk a sky flat, while the exposure solver chases a light source that is not
in the beam.

Starts the cover OPEN -- nothing today auto-opens it for a light target
either (#192, a separate open gap), so this stands in for whatever left it
open before this step ran (the manual calibrator route, an earlier manual
open) -- so the sim's own default-closed start cannot hide a regression: the
assertion is that the flat step itself closes it, not that it was never
opened.

Named mutant: ``close_cover`` reverted to ``open_cover`` (the pre-fix order)
-- RED, observed verbatim:

    AssertionError: the cover was not closed while the panel was lit for the
    flat's first trial exposure: {'state': 'ready', 'cover_state': 'open'}
"""
from __future__ import annotations

import pytest

import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target

pytestmark = pytest.mark.asyncio


async def _flat_plan() -> SequencePlan:
    step = ExposureStep(exposure_s=0.5, count=1, frame_type="Flat",
                        gain=0, offset=30, binning=1,
                        adu_target=20000, panel_brightness=180)
    return SequencePlan(name="Flats", guide=False, targets=[
        Target(name="Flats", ra_hours=0, dec_deg=0, calibration=True,
              steps=[step])])


async def test_flat_step_closes_the_cover_before_lighting_the_panel(
        tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    hub = Hub()
    await hub.connect_sim()
    # the premise: this sim device has a cover at all, and it starts open --
    # standing in for whatever left it open before this step ran, since the
    # device defaults to closed -- so the test cannot pass merely because of
    # that default.
    cc = hub.calibrator
    assert getattr(cc, "has_cover", False), "premise: the sim panel has a cover"
    await hub.open_cover()
    assert (await hub.calibrator_status())["cover_state"] == "open", (
        "premise: the cover starts this run open")

    eng = SequenceEngine(hub)
    plan = await _flat_plan()

    # Recorded at the FIRST capture the flat step takes (the metering solver's
    # first trial exposure) -- the earliest point a camera frame could be
    # affected by where the lit panel is pointed.
    seen: dict = {}
    real_capture = hub.capture

    async def _capture(exposure_s, *a, **kw):
        if "cover_state" not in seen:
            status = await hub.calibrator_status()
            seen["state"] = status["state"]
            seen["cover_state"] = status["cover_state"]
        return await real_capture(exposure_s, *a, **kw)

    monkeypatch.setattr(hub, "capture", _capture)

    eng.start(plan)
    await eng._task

    assert seen.get("state") == "ready", (
        f"premise: the panel was lit for the flat's first trial exposure: "
        f"{seen}")
    assert seen.get("cover_state") == "closed", (
        f"the cover was not closed while the panel was lit for the flat's "
        f"first trial exposure: {seen}")
    # Left closed afterwards too, by ``_panel_off_safe`` exactly as before
    # this fix -- unchanged by it, and unrelated to #192 (nothing auto-opens
    # the cover for a LATER light target either way).
    assert (await hub.calibrator_status())["cover_state"] == "closed", (
        "the cover was not left closed after the flat step ended")


async def test_flat_step_with_no_cover_still_lights_the_panel(
        tmp_path, monkeypatch):
    """CONTROL: a calibrator with no cover (``has_cover`` False) must not be
    asked to close one -- ``close_cover`` raises ``DeviceError`` on the base
    class for exactly this case (devices/base.py), so a panel with no cover
    must still shoot its flats."""
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    hub = Hub()
    await hub.connect_sim()
    monkeypatch.setattr(hub.calibrator, "has_cover", False)

    eng = SequenceEngine(hub)
    plan = await _flat_plan()
    eng.start(plan)
    await eng._task

    assert eng._frames_done == 1
    assert (await hub.calibrator_status())["state"] == "off"  # panel-off at teardown
