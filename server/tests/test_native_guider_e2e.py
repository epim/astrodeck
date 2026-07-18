import numpy as np
import pytest
from astrodeck.devices.sim import build_sim_rig
from astrodeck.providers import NATIVE_AVAILABLE

pytestmark = pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native wheel absent")

@pytest.mark.asyncio
async def test_native_guider_converges_on_sim():
    from astrodeck.guide.native import NativeGuider
    rig = build_sim_rig()
    cam = rig["guide_camera"]; tel = rig["telescope"]
    await cam.connect(); await tel.connect()
    # inject a modest drift the guider must cancel
    rig["_rig"].guide_drift_px_s = 0.15
    g = NativeGuider(cam, tel, config={"image_scale_arcsec": 2.0,
                                       "exposure_s": 0.2}, profile_id="test")
    await g.connect()
    await g.start_guiding()          # calibrate + settle
    assert await g.is_active()
    # run a while and assert RMS converges below a threshold
    import asyncio
    await asyncio.sleep(6.0)
    st = g.stats()
    assert st.guiding
    assert st.rms_total < 2.0, f"rms_total={st.rms_total}"
    await g.stop_guiding()
    await g.disconnect()
