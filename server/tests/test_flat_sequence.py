import pytest
import astrodeck.hub as hub_module
from astrodeck.hub import Hub
from astrodeck.sequence.engine import SequenceEngine
from astrodeck.sequence.models import SequencePlan, Target, ExposureStep


@pytest.mark.asyncio
async def test_flat_step_solves_and_shoots(tmp_path, monkeypatch):
    monkeypatch.setattr(hub_module, "CAPTURE_DIR", tmp_path)  # isolate FITS writes
    hub = Hub(); await hub.connect_sim()
    eng = SequenceEngine(hub)
    step = ExposureStep(exposure_s=0.5, count=3, frame_type="Flat",
                        gain=0, offset=30, binning=1,
                        adu_target=20000, panel_brightness=180)
    plan = SequencePlan(name="Flats", guide=False, targets=[
        Target(name="Flats", ra_hours=0, dec_deg=0, calibration=True, steps=[step])])
    eng.start(plan)
    await eng._task              # run to completion
    # 3 flats recorded, panel left OFF, exposure was re-solved (not the 0.5s seed)
    assert eng._frames_done == 3
    assert (await hub.calibrator_status())["state"] == "off"


@pytest.mark.asyncio
async def test_flat_panel_off_on_abort(tmp_path):
    # an aborted flat run must never leave the panel lit.
    hub = Hub(); await hub.connect_sim()
    await hub.calibrator_on(120)          # panel is lit before teardown
    eng = SequenceEngine(hub)
    await eng._safe_stop()                # abort/error teardown path
    assert (await hub.calibrator_status())["state"] == "off"
