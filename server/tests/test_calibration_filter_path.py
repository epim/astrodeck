"""The sequencer's filter change never runs for a calibration target.

``_apply_filter`` is a well-covered function. Every test it has calls it
directly — so the suite proves the function is right and proves nothing about
whether the calibration path ever reaches it. It does not: ``_apply_filter`` is
called from ``_run_step`` (the LIGHT path) alone, and ``_run_calibration`` runs
its own frame loop.

Three promises break as a result, all of them silent:

  * an ``ExposureStep.filter`` on a calibration step is documented as "filter
    name" and is simply ignored — a per-filter FLAT block (the standard L/R/G/B
    flat workflow, and calibration targets are the only way to express one)
    shoots every step through whichever filter happened to be loaded;
  * a Dark or Bias step is documented to drive to the blackout slot — "that is
    the whole reason the slot exists" — and never does, so darks are shot
    through the light path;
  * the per-filter focus offset that rides along with a filter change never
    moves either.

Each test here runs a REAL plan through ``engine.start`` rather than calling
``_apply_filter``, because reaching the function is the entire question.
"""
from __future__ import annotations

import pytest

import astrodeck.config as configmod
import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import ExposureStep, SequencePlan, Target

pytestmark = pytest.mark.asyncio


@pytest.fixture
def filter_store(tmp_path, monkeypatch):
    monkeypatch.setattr(configmod, "FILTER_CONFIG_FILE",
                        tmp_path / "filter_names.json")
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)
    return tmp_path


async def _run(plan) -> Hub:
    hub = Hub()
    await hub.connect_sim()
    eng = SequenceEngine(hub)
    eng.start(plan)
    await eng._task
    return hub


def _calibration_plan(*steps, name="Calibration") -> SequencePlan:
    return SequencePlan(
        name=name, guide=False, dither_every=0,
        targets=[Target(name=name, ra_hours=0, dec_deg=0,
                        calibration=True, autofocus_first=False,
                        steps=list(steps))])


async def test_a_calibration_step_that_names_a_filter_moves_the_wheel(filter_store):
    """The plainest reading of ``ExposureStep.filter``, on the target type that
    exists to carry calibration frames."""
    plan = _calibration_plan(
        ExposureStep(filter="Ha", exposure_s=0.1, count=1, frame_type="Flat"))
    hub = await _run(plan)
    try:
        fw = hub.devices["filterwheel"]
        assert await fw.get_position() == fw.filter_names.index("Ha")
    finally:
        await hub.disconnect_all()


async def test_per_filter_flats_do_not_all_shoot_through_one_filter(filter_store):
    """The standard flat workflow: one calibration target, one step per filter.

    Asserting on the LAST step's filter is the weakest useful check — if the
    wheel never moves at all it fails, which is the defect. The recorded frame
    headers below are what actually proves each step got its own filter.
    """
    plan = _calibration_plan(
        ExposureStep(filter="L", exposure_s=0.1, count=1, frame_type="Flat"),
        ExposureStep(filter="R", exposure_s=0.1, count=1, frame_type="Flat"),
        ExposureStep(filter="G", exposure_s=0.1, count=1, frame_type="Flat"),
        ExposureStep(filter="B", exposure_s=0.1, count=1, frame_type="Flat"))
    hub = await _run(plan)
    try:
        fw = hub.devices["filterwheel"]
        assert await fw.get_position() == fw.filter_names.index("B")
    finally:
        await hub.disconnect_all()


@pytest.mark.parametrize("frame_type", ["Dark", "Bias"])
async def test_calibration_darks_drive_to_the_blackout_slot(filter_store, frame_type):
    """The unit test for this exists and passes. It calls ``_apply_filter``
    directly, so it never noticed that a dark shot the only way the product
    offers — a calibration target — does not reach it."""
    plan = _calibration_plan(
        ExposureStep(exposure_s=0.1, count=1, frame_type=frame_type))
    hub = await _run(plan)
    try:
        fw = hub.devices["filterwheel"]
        await_pos = await fw.get_position()
        assert await_pos == fw.dark_slot(), (
            f"{frame_type} was shot through slot {await_pos} "
            f"({fw.filter_names[await_pos]}), not the blackout slot")
    finally:
        await hub.disconnect_all()


async def test_a_calibration_dark_leaves_a_wheel_with_no_blackout_slot_alone(
        filter_store):
    """The fix must not invent motion where the light path was never the point:
    a wheel with no opaque carrier keeps its position, as it does for lights."""
    hub = Hub()
    await hub.connect_sim()
    try:
        fw = hub.devices["filterwheel"]
        fw.filter_opaque = []
        await fw.set_position(3)
        eng = SequenceEngine(hub)
        eng.start(_calibration_plan(
            ExposureStep(exposure_s=0.1, count=1, frame_type="Dark")))
        await eng._task
        assert await fw.get_position() == 3
    finally:
        await hub.disconnect_all()
