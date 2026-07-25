import numpy as np
import pytest
from astrodeck.devices.sim import build_sim_rig
from astrodeck.providers import NATIVE_AVAILABLE

pytestmark = [
    pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native wheel absent"),
    # Fast/slow lane (pyproject markers): this module is deliberately
    # wall-clock-bound -- it buys timing realism, not extra assertions --
    # so `pytest -m 'not slow'` skips it for the inner loop. Every gate and
    # CI still run the FULL suite unfiltered.
    pytest.mark.slow,
]


@pytest.fixture
def _real_dwell(_fast_sim_delays, monkeypatch):
    """Timing-realism anchor: THIS most-end-to-end native-guider test opts OUT
    of the suite-wide ``ASTRODECK_FAST_TEST`` fast-path (see conftest's
    ``_fast_sim_delays``) so the sim's real exposure dwell + pulse sleeps run at
    wall-clock. It is the one test that must prove the guide loop CONVERGES in
    real time — against a real-time drift/periodic-error/seeing model at the true
    guide cadence — not merely under logical time. Depends on ``_fast_sim_delays``
    so the suite-wide setenv is guaranteed to run FIRST, and this delenv (on the
    same function-scoped monkeypatch) then wins for the duration of this test."""
    monkeypatch.delenv("ASTRODECK_FAST_TEST", raising=False)
    yield


@pytest.mark.asyncio
async def test_native_guider_converges_on_sim(_real_dwell):
    from astrodeck.guide.native import NativeGuider
    rig = build_sim_rig()
    cam = rig["guide_camera"]; tel = rig["telescope"]
    await cam.connect(); await tel.connect()
    # inject a modest drift the guider must cancel
    rig["_rig"].guide_drift_px_s = 0.15
    g = NativeGuider(cam, tel, config={"image_scale_arcsec": 2.0,
                                       "exposure_s": 0.2}, profile_id="test")
    await g.connect()
    import asyncio
    # Bounded via the repo's timeout idiom (asyncio.wait_for — pytest-timeout is
    # not a dependency): calibration is internally deadline-capped at 180s, so a
    # wedged CI run fails here instead of hanging the job (milestone review I3).
    await asyncio.wait_for(g.start_guiding(), timeout=120.0)  # calibrate + settle
    assert await g.is_active()
    # run a while and assert RMS converges below a threshold
    await asyncio.sleep(6.0)
    st = g.stats()
    assert st.guiding
    assert st.rms_total < 2.0, f"rms_total={st.rms_total}"
    await g.stop_guiding()
    await g.disconnect()


@pytest.mark.asyncio
async def test_stats_converts_engine_pixels_to_arcsec():
    """Milestone review I1: the engine reports guide errors in PIXELS but the
    GuideStats bus contract is ARCSEC (base.py's recent doc; phd2.py multiplies
    PHD2's raw px by its pixel scale the same way). NativeGuider.stats() must
    convert every error quantity by the configured image scale (arcsec/px) —
    the sim rig's 1"/px scale masked this. Uses a stub engine at a non-1.0
    scale so engine-px * scale == published-arcsec is asserted exactly."""
    from astrodeck.guide.native import NativeGuider
    rig = build_sim_rig()
    g = NativeGuider(rig["guide_camera"], rig["telescope"],
                     config={"image_scale_arcsec": 2.5}, profile_id=None)

    class _StubEngine:
        def stats(self):
            return {"guiding": True, "rms_ra": 0.4, "rms_dec": 0.3,
                    "rms_total": 0.5, "snr": 42.0,
                    "recent": [[100.0, 1.0, -2.0]]}

    g._engine = _StubEngine()
    g._active = True
    st = g.stats()
    assert st.guiding
    # engine px * 2.5 "/px == published arcsec
    assert st.rms_ra == pytest.approx(1.0)      # 0.4 px
    assert st.rms_dec == pytest.approx(0.75)    # 0.3 px
    assert st.rms_total == pytest.approx(1.25)  # 0.5 px
    assert st.snr == pytest.approx(42.0)        # unitless: NOT scaled
    assert st.recent[-1]["ra"] == pytest.approx(2.5)    # 1.0 px
    assert st.recent[-1]["dec"] == pytest.approx(-5.0)  # -2.0 px
    assert st.recent[-1]["t"] == pytest.approx(100.0)   # time: NOT scaled
