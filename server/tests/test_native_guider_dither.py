"""P2-T1 e2e: ``NativeGuider.dither`` against the sim rig — exercises the
REAL engine dither + fast-recenter + settle lifecycle (dossier §11/§12),
wired end to end through the settle-wait handshake (P2-T1 punch-list #3:
``_sync_settle_window`` tracks ``stats()["settling"]`` rather than any
single frame's Action shape). Fixture shape mirrors
``test_native_guider_e2e.py``'s converge test.
"""
import asyncio

import pytest
from astrodeck.devices.sim import build_sim_rig
from astrodeck.providers import NATIVE_AVAILABLE

pytestmark = pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native wheel absent")


@pytest.mark.asyncio
async def test_native_guider_dither_settles_and_reconverges():
    from astrodeck.guide.native import NativeGuider

    rig = build_sim_rig()
    cam = rig["guide_camera"]
    tel = rig["telescope"]
    await cam.connect()
    await tel.connect()
    g = NativeGuider(cam, tel, config={"image_scale_arcsec": 2.0,
                                       "exposure_s": 0.2}, profile_id="test-dither")
    await g.connect()
    # Bounded via the repo's timeout idiom (asyncio.wait_for — pytest-timeout
    # is not a dependency), matching test_native_guider_e2e.py.
    await asyncio.wait_for(g.start_guiding(), timeout=120.0)  # calibrate + settle
    assert await g.is_active()

    # Let the loop run a moment so the first few accepted frames land in
    # `recent` before we disturb it.
    await asyncio.sleep(1.0)
    pre_rms = g.stats().rms_total

    # Fire the dither concurrently and grab a mid-flight snapshot: while the
    # engine's settle window is open (fast recenter, then the dwell wait —
    # dossier §11.2/§12), stats().guiding must read False (the SAME
    # `guiding` flag MonitorView/Sparkline/etc. already key off — spec
    # §3.2/§3.5), directly proving the dither's disturbance registered.
    dither_task = asyncio.create_task(g.dither(3.0))
    saw_settling = False
    for _ in range(50):  # up to ~5s of polling
        await asyncio.sleep(0.1)
        if not g.stats().guiding:
            saw_settling = True
            break
        if dither_task.done():
            break
    assert saw_settling, "expected stats().guiding to go False while the dither settle window is open"

    # dither() blocks until the settle window closes (Done) or fails/times
    # out — propagate any exception (a DeviceError here is a real test
    # failure, not an expected outcome).
    await asyncio.wait_for(dither_task, timeout=90.0)

    # Reconvergence: guiding resumed, and the post-settle RMS is back down
    # near (or below) the pre-dither steady state — the settle dwell (dossier
    # §12) would not have completed at all if the star hadn't actually
    # reconverged to within tolerance of the new lock.
    st = g.stats()
    assert st.guiding, f"expected guiding to resume after settle, got {st}"
    assert await g.is_active()
    assert st.rms_total < max(2.0, pre_rms * 3.0), (
        f"expected reconvergence: pre_rms={pre_rms} post_rms={st.rms_total}")
    assert len(st.recent) >= 1, "expected accepted post-settle guide samples"

    await g.stop_guiding()
    await g.disconnect()
