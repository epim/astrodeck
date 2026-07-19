"""A1 (final-branch-review I2): the native guider absorbs a transient
guide-camera exposure fault via bounded retry+backoff, and dies loudly (honest
death: _lost, not guiding) on a persistent fault. Drives the sim's
guide_expose_fail_next_n knob; needs no native wheel (NativeGuider._expose /
_guide_loop / _calibrate are pure Python over the sim camera)."""
import pytest

import astrodeck.guide.native as nativemod
from astrodeck.devices.base import DeviceError
from astrodeck.devices.sim import build_sim_rig
from astrodeck.guide.native import NativeGuider


def _guider(monkeypatch):
    monkeypatch.setattr(nativemod, "_EXPOSE_BACKOFF_S", (0.0, 0.0, 0.0))
    rig = build_sim_rig()
    cam, tel = rig["guide_camera"], rig["telescope"]
    return NativeGuider(cam, tel, config={"exposure_s": 0.05}, profile_id=None), cam, tel


@pytest.mark.asyncio
async def test_two_frame_fault_absorbed_by_retry(monkeypatch):
    guider, cam, tel = _guider(monkeypatch)
    await cam.connect()
    cam.guide_expose_fail_next_n = 2  # 2 faults then success -> absorbed in one _expose
    frame = await guider._expose()
    assert frame is not None
    assert cam.guide_expose_fail_next_n == 0  # fully consumed


@pytest.mark.asyncio
async def test_persistent_fault_exhausts_retries(monkeypatch):
    guider, cam, tel = _guider(monkeypatch)
    await cam.connect()
    cam.guide_expose_fail_next_n = 10_000  # every attempt faults
    with pytest.raises(DeviceError):
        await guider._expose()


@pytest.mark.asyncio
async def test_persistent_fault_honest_death(monkeypatch):
    guider, cam, tel = _guider(monkeypatch)
    await cam.connect()
    cam.guide_expose_fail_next_n = 10_000  # camera wedged: every exposure faults
    guider._active = True
    guider._stop.clear()
    await guider._guide_loop()  # returns when the fault budget trips honest death
    assert guider._lost is True
    assert guider._active is False
    assert guider.stats().guiding is False


@pytest.mark.asyncio
async def test_calibration_fault_aborts_with_deviceerror(monkeypatch):
    guider, cam, tel = _guider(monkeypatch)
    await cam.connect()
    await tel.connect()
    cam.guide_expose_fail_next_n = 10_000
    # _calibrate's first line is `await self._expose()`; an exhausted retry
    # raises DeviceError (a handled channel), never a raw camera exception —
    # so start_guiding's caller survives the calibration abort.
    with pytest.raises(DeviceError):
        await guider._calibrate()
