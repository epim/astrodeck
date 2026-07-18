"""P4 acceptance gate (spec §5): PPEC beats hysteresis on strong injected
periodic error, plus the native.py config-passthrough for `ppec` + `blc_pulse_ms`.

DITHER-FREE (binding review condition): the engine RESETS PPEC on dither today
(a ledgered parity gap), so this gate is two CONTINUOUS guiding runs with no
dither call anywhere.

------------------------------------------------------------------------------
GATE-SCENARIO ADJUDICATION (constants below vs the P4-T2 brief's literals)
------------------------------------------------------------------------------
The brief pins `guide_pe_amplitude_px = 4.0`, `guide_pe_period_s = 120`. The
controller AUTHORIZED scaling the scenario down (note #1): "the literals are
means, not ends" — the gate's PURPOSE is "PPEC beats hysteresis on strong
injected PE", and the constants exist only to make that demonstrable within the
suite's time budget (the suite was just cut to ~100s; an 8-minute gate is
unacceptable).

Two facts drive the design here:

  (a) PPEC's periodic kernel ships tuned to `periodic_period = 200 s`
      (`GpParams::default`), and that period is NOT configurable through the
      engine config surface — only the algorithm KIND flows through
      `EngineConfig`. PPEC's FFT period re-estimation and its hysteresis→GP
      blend are both gated on `t > min_periods · period_length`, i.e. on
      `t > 2 · 200 s = 400 s` of ENGINE time before the predictor engages with a
      period far from 200. So a literally-short PE period (20–40 s) would NOT be
      learned quickly with the shipped, parity-reviewed defaults — the predictor
      would sit in its hysteresis blend for ~400 engine-seconds regardless.

  (b) The sim's PE advances on `time.time()` (`SimRig.guide_star_px`), while the
      engine's internal GP clock is synthesized from the per-frame `exposure_s`
      sums (P4-T1 review M6). Driving the loop against real wall-clock makes the
      two clocks drift apart non-uniformly (variable per-frame overhead), which
      both slows the gate and injects the very flakiness the controller warned
      against.

Resolution — TIME COMPRESSION instead of period reduction. We keep the PE
amplitude at the pinned 4.0 px and keep the PE period at the engine's NATIVE
200 s default (so PPEC's kernel is correctly tuned with NO engine change and
maximum parity), and we drive the loop under a VIRTUAL CLOCK: `sim.time` is
monkeypatched so each frame advances the sim's PE phase by a fixed LOGICAL
`dt = 5 s` — the same `dt` handed to `GuideEngine.process` — so the PE clock and
the engine clock stay perfectly aligned. 100 learning frames = 500 engine-
seconds > the 400 s blend/FFT threshold, giving PPEC ≳2 full periods of data
before the 50-frame (~1.25-period) measurement window opens. The whole gate
(shared calibration + both arms) completes in ~45 s of real wall-clock.

Because the virtual clock is deterministic, the run is bit-reproducible: the PE
phase AND the seeing jitter (seeded off the clock) are identical frame-for-frame
between the two arms, so the RMS difference is PURELY algorithmic. The `< 0.8 ×`
margin is generous against the measured ratio (~0.46 on this box) and against
cross-platform FP drift; there is no run-to-run variance to be flaky about.

Same drift/seeing/PE conditions per arm; same post-learning window definition
for both. Calibration is run once with PE off (a quiet-sky calibration) and the
resulting Cal is reused by both arms, so the geometry is identical too.
"""
import math
import time as _realtime

import pytest

from astrodeck.providers import NATIVE_AVAILABLE

pytestmark = pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native wheel absent")

# --- gate constants (see the adjudication above) ---------------------------
_LOGICAL_DT_S = 5.0        # PE-phase advance per frame == engine `process` dt
_PE_PERIOD_S = 200.0       # the engine's native GpParams default period
_PE_AMPLITUDE_PX = 4.0     # the brief's pinned amplitude (kept)
_SEEING_PX = 0.1           # small: PE dominates so the advantage is legible
_GUIDE_SCALE = 0.5         # arcsec/px; also the engine image scale (consistent)
_RENDER_EXP_S = 0.1        # real exposure — only sizes the rendered star flux
_LEARN_FRAMES = 100        # 500 engine-s > 400 s blend/FFT threshold (~2.5 P)
_MEASURE_FRAMES = 50       # ~1.25 periods of post-learning window
_VT_BASE = 1000.0          # virtual-clock start (identical phase per arm)


class _VirtualClock:
    """Stand-in for the ``time`` module inside ``astrodeck.devices.sim``:
    ``time()`` returns a controllable virtual instant (drives the PE phase and
    the jitter seed); everything else (``monotonic`` for the exposure dwell,
    etc.) delegates to the real ``time`` module."""

    def __init__(self) -> None:
        self.vt = _VT_BASE

    def time(self) -> float:
        return self.vt

    def __getattr__(self, name):  # monotonic / sleep / ... -> real time
        return getattr(_realtime, name)


async def _calibrate(cam, tel, engine_cfg, clock):
    """Drive one calibration walk (PE off) and return the resulting Cal dict."""
    import astrodeck_native as native

    eng = native.GuideEngine(engine_cfg)
    clock.vt += _LOGICAL_DT_S
    frame = await cam.expose(_RENDER_EXP_S, 100, 30, binning=1)
    stars, _meta = native.guide_star_find(frame.data)
    assert stars, "calibration requires a guide star"
    eng.begin_calibration(float(stars[0]["x"]), float(stars[0]["y"]))
    _ra, dec_deg = await tel.get_position()
    pier = (await tel.pier_side()).value
    eng.set_scope_pointing(math.radians(float(dec_deg)), pier, "unknown",
                           "unknown", 0.0, 1)
    for _ in range(4000):
        clock.vt += _LOGICAL_DT_S
        frame = await cam.expose(_RENDER_EXP_S, 100, 30, binning=1)
        a = eng.process(frame.data, frame.timestamp, _LOGICAL_DT_S)
        if a["action"] == "cal_step":
            await tel.pulse_guide(a["dir"], int(a["ms"]))
            continue
        cal = eng.dump_calibration()
        if cal and cal.get("is_valid"):
            return cal
    raise AssertionError("calibration did not complete")


async def _guide_arm(ra_algo: str, cal: dict, clock) -> float:
    """Guide one arm (given RA algorithm) against the shared calibration and
    return the RA RMS (px) over the post-learning window. Builds the engine
    config THROUGH ``native.py`` so the `ra_algorithm` passthrough is exercised,
    then drives the same expose->process->pulse loop ``NativeGuider`` runs."""
    import astrodeck_native as native
    from astrodeck.devices.sim import build_sim_rig
    from astrodeck.guide.native import NativeGuider

    clock.vt = _VT_BASE  # identical PE phase + jitter for both arms
    rig = build_sim_rig()
    r = rig["_rig"]
    cam, tel = rig["guide_camera"], rig["telescope"]
    r.guide_scale_arcsec_px = _GUIDE_SCALE
    r.guide_pe_amplitude_px = _PE_AMPLITUDE_PX
    r.guide_pe_period_s = _PE_PERIOD_S
    r.guide_seeing_px = _SEEING_PX
    r.guide_drift_px_s = 0.0
    await cam.connect()
    await tel.connect()

    guider = NativeGuider(cam, tel, config={
        "ra_algorithm": ra_algo,
        "image_scale_arcsec": _GUIDE_SCALE,
        "exposure_s": _RENDER_EXP_S,
    }, profile_id=None)
    rates = await tel.guide_rates()
    engine_cfg = guider._build_engine_config(rates)  # native.py passthrough
    assert engine_cfg["ra_algorithm"] == ra_algo

    eng = native.GuideEngine(engine_cfg)
    eng.load_calibration({k: v for k, v in cal.items()
                          if k != "image_scale_arcsec"})
    eng.begin_guiding()

    errs: list[float] = []
    for _ in range(_LEARN_FRAMES + _MEASURE_FRAMES):
        clock.vt += _LOGICAL_DT_S
        frame = await cam.expose(_RENDER_EXP_S, 100, 30, binning=1)
        a = eng.process(frame.data, frame.timestamp, _LOGICAL_DT_S)
        kind = a["action"]
        if kind == "pulse":
            await tel.pulse_guide(a["dir"], int(a["ms"]))
        elif kind == "pulse_pair":
            if a.get("ra"):
                await tel.pulse_guide(a["ra"]["dir"], int(a["ra"]["ms"]))
            if a.get("dec"):
                await tel.pulse_guide(a["dec"]["dir"], int(a["dec"]["ms"]))
        # kind in {"idle","settle","lock_lost"} -> no correction this frame
        rec = eng.stats().get("recent", [])
        if rec:
            errs.append(float(rec[-1][1]))  # ra_err_px

    window = errs[_LEARN_FRAMES:]
    assert window, "no post-learning frames were recorded"
    return math.sqrt(sum(e * e for e in window) / len(window))


@pytest.mark.asyncio
async def test_ppec_beats_hysteresis_on_injected_pe(monkeypatch):
    """P4 GATE (spec §5): with a strong injected periodic error, RA=PPEC has a
    strictly lower post-learning RMS than RA=Hysteresis. Dither-free."""
    import astrodeck.devices.sim as simmod
    from astrodeck.devices.sim import build_sim_rig

    clock = _VirtualClock()
    monkeypatch.setattr(simmod, "time", clock)

    # One shared calibration, PE OFF (quiet-sky calibration).
    clock.vt = _VT_BASE
    rig = build_sim_rig()
    r = rig["_rig"]
    cam, tel = rig["guide_camera"], rig["telescope"]
    r.guide_scale_arcsec_px = _GUIDE_SCALE
    r.guide_pe_amplitude_px = 0.0
    r.guide_seeing_px = 0.05
    r.guide_drift_px_s = 0.0
    await cam.connect()
    await tel.connect()
    cal = await _calibrate(
        cam, tel,
        {"image_scale_arcsec": _GUIDE_SCALE, "exposure_s": _RENDER_EXP_S},
        clock,
    )

    hyst_rms = await _guide_arm("hysteresis", cal, clock)
    ppec_rms = await _guide_arm("ppec", cal, clock)

    # Generous margin (controller note #2): the measured ratio is ~0.46; assert
    # a strict win with head-room against cross-platform FP drift.
    assert ppec_rms < 0.8 * hyst_rms, (
        f"PPEC must beat hysteresis on injected PE: "
        f"ppec_rms={ppec_rms:.4f}px hyst_rms={hyst_rms:.4f}px "
        f"ratio={ppec_rms / hyst_rms:.3f} (want < 0.8)"
    )
    # Sanity: both arms actually guided (residual well below the 4 px PE).
    assert hyst_rms < 2.0, f"hysteresis arm did not converge: {hyst_rms:.4f}px"
    assert ppec_rms < 1.5, f"ppec arm did not converge: {ppec_rms:.4f}px"


def test_native_config_passes_ppec_and_blc():
    """native.py `_build_engine_config` passes `ra_algorithm='ppec'` and
    `blc_pulse_ms` through its allowlist, and the engine accepts the RA-PPEC
    config (controller note #4)."""
    import astrodeck_native as native
    from astrodeck.devices.sim import build_sim_rig
    from astrodeck.guide.native import NativeGuider

    rig = build_sim_rig()
    g = NativeGuider(rig["guide_camera"], rig["telescope"], config={
        "ra_algorithm": "ppec",
        "blc_pulse_ms": 150,
        "image_scale_arcsec": 0.5,
    }, profile_id=None)
    cfg = g._build_engine_config(None)
    assert cfg["ra_algorithm"] == "ppec"
    assert cfg["blc_pulse_ms"] == 150
    eng = native.GuideEngine(cfg)  # RA-PPEC + static BLC config must construct
    assert eng is not None


def test_native_config_rejects_dec_ppec():
    """PPEC is RA-only (dossier §6.8): native.py forwards a Dec `ppec` string,
    and the PyO3 validation layer rejects it with a ValueError (defense in
    depth; the engine keeps a ResistSwitch Dec fallback)."""
    import astrodeck_native as native
    from astrodeck.devices.sim import build_sim_rig
    from astrodeck.guide.native import NativeGuider

    rig = build_sim_rig()
    g = NativeGuider(rig["guide_camera"], rig["telescope"], config={
        "dec_algorithm": "ppec",
        "image_scale_arcsec": 0.5,
    }, profile_id=None)
    cfg = g._build_engine_config(None)
    assert cfg["dec_algorithm"] == "ppec"
    with pytest.raises(ValueError):
        native.GuideEngine(cfg)
