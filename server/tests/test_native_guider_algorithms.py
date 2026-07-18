"""P3 gate (P3-T2): end-to-end proof that Lowpass2 and ZFilter are wired all
the way from the AlgoKind selection strings through to a converging sim
guide loop -- not just unit-tested in isolation (see
native/crates/astro-guide/tests/lowpass_zfilter_golden.rs for the algorithm
math itself). Mirrors test_native_guider_e2e.py's
test_native_guider_converges_on_sim, but pins a non-default algorithm pair
(Dec=Lowpass2, RA=ZFilter) via NativeGuider's config dict -> GuideEngine's
ra_algorithm/dec_algorithm keys (astrodeck/guide/native.py
_build_engine_config -> astrodeck-native's parse_algo_kind -> engine.rs's
make_algo). Threshold-only assertions (dossier §0/§17 convergence
precedent): guide RMS is wall-clock/noise-sensitive, so this proves "guides
and holds", not a golden numeric trajectory.
"""
import pytest
from astrodeck.devices.sim import build_sim_rig
from astrodeck.providers import NATIVE_AVAILABLE

pytestmark = pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native wheel absent")


@pytest.mark.asyncio
async def test_native_guider_converges_with_zfilter_ra_and_lowpass2_dec():
    from astrodeck.guide.native import NativeGuider
    rig = build_sim_rig()
    cam = rig["guide_camera"]; tel = rig["telescope"]
    await cam.connect(); await tel.connect()
    # Same modest drift as the default-algorithm e2e test, so this is a
    # like-for-like proof that the non-default pair also holds.
    rig["_rig"].guide_drift_px_s = 0.15
    g = NativeGuider(cam, tel, config={"image_scale_arcsec": 2.0,
                                       "exposure_s": 0.2,
                                       "ra_algorithm": "z_filter",
                                       "dec_algorithm": "lowpass2"},
                     profile_id="test-zfilter-lowpass2")
    await g.connect()
    import asyncio
    await asyncio.wait_for(g.start_guiding(), timeout=120.0)  # calibrate + settle
    assert await g.is_active()
    await asyncio.sleep(6.0)
    st = g.stats()
    assert st.guiding
    assert st.rms_total < 2.0, f"rms_total={st.rms_total}"
    await g.stop_guiding()
    await g.disconnect()
