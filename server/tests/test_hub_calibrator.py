import pytest
from astrodeck.hub import Hub


@pytest.mark.asyncio
async def test_hub_calibrator_control():
    hub = Hub(); await hub.connect_sim()
    assert "covercalibrator" in hub.devices
    await hub.calibrator_on(150)
    st = await hub.calibrator_status()
    assert st["state"] == "ready" and st["brightness"] == 150
    await hub.calibrator_off()
    assert (await hub.calibrator_status())["state"] == "off"


@pytest.mark.asyncio
async def test_calibrator_status_none_when_absent():
    hub = Hub()   # nothing connected
    assert await hub.calibrator_status() is None
