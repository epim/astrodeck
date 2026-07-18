"""P3 gate (P3-T2): end-to-end proof that the non-default guide algorithms
(Lowpass, Lowpass2, ZFilter) are wired all the way from the AlgoKind
selection strings through to a converging sim guide loop -- not just
unit-tested in isolation (see
native/crates/astro-guide/tests/lowpass_zfilter_golden.rs for the algorithm
math itself). Mirrors test_native_guider_e2e.py's
test_native_guider_converges_on_sim, but pins non-default algorithm pairs
via NativeGuider's config dict -> GuideEngine's ra_algorithm/dec_algorithm
keys (astrodeck/guide/native.py _build_engine_config -> astrodeck-native's
parse_algo_kind -> engine.rs's make_algo). Threshold-only assertions
(dossier §0/§17 convergence precedent): guide RMS is
wall-clock/noise-sensitive, so this proves "guides and holds", not a golden
numeric trajectory.

Pairs: (RA=ZFilter, Dec=Lowpass2) is the brief's original P3-gate pair;
(RA=Lowpass, Dec=ResistSwitch) was added in the P3-T2 fix round (review
coverage item 4) so the "lowpass" string is also exercised end to end.
"""
import asyncio

import pytest
from astrodeck.devices.sim import build_sim_rig
from astrodeck.providers import NATIVE_AVAILABLE

pytestmark = pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native wheel absent")


async def _assert_converges(ra_algorithm: str, dec_algorithm: str) -> None:
    from astrodeck.guide.native import NativeGuider
    rig = build_sim_rig()
    cam = rig["guide_camera"]; tel = rig["telescope"]
    await cam.connect(); await tel.connect()
    # Same modest drift as the default-algorithm e2e test, so each pair is a
    # like-for-like proof that the non-default algorithms also hold.
    rig["_rig"].guide_drift_px_s = 0.15
    g = NativeGuider(cam, tel, config={"image_scale_arcsec": 2.0,
                                       "exposure_s": 0.2,
                                       "ra_algorithm": ra_algorithm,
                                       "dec_algorithm": dec_algorithm},
                     profile_id=f"test-{ra_algorithm}-{dec_algorithm}")
    await g.connect()
    await asyncio.wait_for(g.start_guiding(), timeout=120.0)  # calibrate + settle
    assert await g.is_active()
    await asyncio.sleep(6.0)
    st = g.stats()
    assert st.guiding
    assert st.rms_total < 2.0, f"rms_total={st.rms_total}"
    await g.stop_guiding()
    await g.disconnect()


@pytest.mark.asyncio
async def test_native_guider_converges_with_zfilter_ra_and_lowpass2_dec():
    await _assert_converges("z_filter", "lowpass2")


@pytest.mark.asyncio
async def test_native_guider_converges_with_lowpass_ra():
    await _assert_converges("lowpass", "resist_switch")
