"""Sub-project A acceptance gate (spec §5): the UNATTENDED NIGHT scenario on
the closed-loop sim, deterministic under a virtual clock. Exercises A1 (fault
absorption via NativeGuider._expose), A2 (PPEC survives a mid-run dither), and
A5 (stop persists + start restores the trained model, retain-or-reset gate).
CI-wired (native job).

One deterministic e2e proves the whole wave composes:

  (1) PPEC guides and TRAINS on an injected periodic error;
  (2) a mid-run 2-exposure guide-camera fault is ABSORBED by the retry envelope
      (guiding continues; no honest death) — A1, final-branch-review I2;
  (3) a mid-run RA dither does NOT reset the trained model (the gear-time gap is
      compensated, settle window opens then closes) — A2, P4-T1 rulings A+C;
  (4) a STOP persists the trained model to `<profile>-gp.json` and a fresh START
      on the SAME calibration RESTORES the WHOLE window (retain-or-reset gate,
      amended spec §3-A5), giving immediate post-restart prediction quality that
      beats a from-scratch PPEC over the same early window — A5.

NON-DEFAULT TRUE PERIOD (A-T5 re-review, binding). The engine's periodic kernel
ships tuned to `GpParams::periodic_period = 200 s` and that period is NOT part of
the engine-config surface. The learned period is also NOT persisted across a
stop/start (ledgered follow-up: upstream persists it, we don't yet — only the
measurement window is dumped/restored). So this gate injects a TRUE PE period of
250 s (`_PE_PERIOD_S`) — deliberately != the 200 s kernel default — so that
period-learning actually matters: the restore leg must RE-LEARN the period off
the restored window (the FFT re-estimation re-engages immediately because the
restored gear time already exceeds `min_periods · P`), and still beat a
cold-start arm. `_VT_BASE = 1000 s` is an integer number of periods for both 200
and 250, so the injected sinusoid starts at phase 0 on the engine's gear-time
axis (clean, reproducible geometry).

DETERMINISM. `sim.time` is monkeypatched to a virtual clock advancing a fixed
logical `dt = 5 s` per frame — the same `dt` handed to `process` — so the sim's
PE phase and the engine's synthesized gear clock stay aligned frame-for-frame,
and the seeing jitter (seeded off the clock) replays identically between arms.
The stop/start DOWNTIME the retention gate reads is made deterministic too:
`guide.native.time` is monkeypatched to a SEPARATE controllable wall clock
(independent of the sim's PE clock, which resets to `_VT_BASE` each run) so
`dumped_at`/downtime are exact — a short, fixed 30 s (`_RETENTION_DOWNTIME_S`),
clearly within the retain gate (40% of the 200 s kernel period = 80 s). The run
is reproducible: restored/scratch RMS are bit-identical run-to-run on a box and
carry ~27% head-room against cross-platform FP drift (observed on this box:
restored ~0.44 px vs scratch ~0.60 px, ratio ~0.73).
"""
import asyncio
import math
import time as _realtime

import pytest

import astrodeck.config as configmod
import astrodeck.devices.sim as simmod
import astrodeck.guide.native as nativemod
from astrodeck.devices.sim import build_sim_rig
from astrodeck.guide.native import NativeGuider
from astrodeck.providers import NATIVE_AVAILABLE

pytestmark = pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native wheel absent")

# --- gate constants ---------------------------------------------------------
_LOGICAL_DT_S = 5.0        # PE-phase advance per frame == engine `process` dt
_KERNEL_PERIOD_S = 200.0   # the engine's native GpParams default period (kernel)
_PE_PERIOD_S = 250.0       # injected TRUE period, != kernel default (see docstr)
_PE_AMPLITUDE_PX = 4.0     # strong periodic error, so PPEC's advantage is legible
_SEEING_PX = 0.1           # small: PE dominates
_GUIDE_SCALE = 0.5         # arcsec/px; also the engine image scale (consistent)
_RENDER_EXP_S = 0.1        # real exposure — only sizes the rendered star flux
_LEARN_FRAMES = 100        # 500 gear-s > 2·200 s blend/FFT threshold (~2 periods)
_SETTLE_FRAMES = 20        # frames (~100 gear-s) for the mid-run dither to settle
_MEASURE_FRAMES = 40       # ~1 period of post-restart window (restored vs scratch)
_VT_BASE = 1000.0          # virtual-clock start; integer periods for 200 AND 250
# Deterministic stop/start downtime, clearly WITHIN the retain-or-reset gate
# (amended spec §3-A5): < 40% of the 200 s kernel period = 80 s. Short stop.
_RETENTION_DOWNTIME_S = 30.0
# A-T1 review punch item: bound the whole scenario's wall clock so a regression
# that lets an exposure hang (e.g. a removed Alpaca imageready deadline) fails
# FAST in CI instead of wedging the job. pytest-timeout is not a suite
# dependency (checked), so the bound is applied here with asyncio. Generous
# against a slow CI box (a normal run is well under a minute); a true hang is
# unbounded, so any finite cap fails it fast.
_GATE_TIMEOUT_S = 240.0


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


class _NativeClock:
    """Stand-in for the ``time`` module inside ``astrodeck.guide.native``: only
    the wall-clock ``time()`` the retention persistence stamps (``dumped_at``)
    and reads (downtime) is virtualized, to a value we set explicitly around the
    stop/start — kept separate from the sim's PE clock (which resets to
    ``_VT_BASE`` each run, and would otherwise yield a negative downtime).
    ``monotonic``/``sleep`` still delegate to real time."""

    def __init__(self) -> None:
        self.wall = 10_000.0

    def time(self) -> float:
        return self.wall

    def __getattr__(self, name):
        return getattr(_realtime, name)


def _rms(errs):
    return math.sqrt(sum(e * e for e in errs) / len(errs)) if errs else 0.0


async def _calibrate(cam, tel, engine_cfg, clock):
    """Drive one calibration walk (PE off) and return the resulting Cal dict."""
    import astrodeck_native as native
    eng = native.GuideEngine(engine_cfg)
    clock.vt += _LOGICAL_DT_S
    frame = await cam.expose(_RENDER_EXP_S, 100, 30, binning=1)
    stars, _ = native.guide_star_find(frame.data)
    assert stars
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


async def _new_ppec_guider(cal, profile_id):
    """Build a fresh RA=PPEC NativeGuider on a fresh sim rig with the injected
    (non-default) periodic error, load the shared calibration, and begin
    guiding — the same expose->process->pulse surface start_guiding drives,
    minus the calibration walk (reused Cal), so every leg starts from the same
    geometry."""
    import astrodeck_native as native
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
    g = NativeGuider(cam, tel, config={
        "ra_algorithm": "ppec", "image_scale_arcsec": _GUIDE_SCALE,
        "exposure_s": _RENDER_EXP_S}, profile_id=profile_id)
    rates = await tel.guide_rates()
    g._engine = native.GuideEngine(g._build_engine_config(rates))
    g._engine.load_calibration({k: v for k, v in cal.items()
                                if k != "image_scale_arcsec"})
    g._engine.begin_guiding()
    g._active = True
    return g, cam, tel


async def _drive(g, cam, tel, clock, n, *, fault_at=None, dither_at=None):
    """Drive ``n`` guide frames (expose -> process -> dispatch), advancing the
    virtual clock one logical ``dt`` per frame; return the per-frame RA error
    (px). Optionally inject a transient 2-exposure camera fault at ``fault_at``
    (A1: absorbed inside a single ``_expose``) and/or an RA dither at
    ``dither_at`` (A2: PPEC must not reset)."""
    errs = []
    for i in range(n):
        clock.vt += _LOGICAL_DT_S
        if fault_at is not None and i == fault_at:
            cam.guide_expose_fail_next_n = 2  # A1: transient 2-exposure fault
        if dither_at is not None and i == dither_at:
            g._engine.dither(3.0, 0.0)  # A2: RA dither, PPEC must NOT reset
        frame = await g._expose()  # A1 retry envelope absorbs the fault
        a = g._engine.process(frame.data, frame.timestamp, _LOGICAL_DT_S)
        k = a["action"]
        if k == "pulse":
            await tel.pulse_guide(a["dir"], int(a["ms"]))
        elif k == "pulse_pair":
            if a.get("ra"):
                await tel.pulse_guide(a["ra"]["dir"], int(a["ra"]["ms"]))
            if a.get("dec"):
                await tel.pulse_guide(a["dec"]["dir"], int(a["dec"]["ms"]))
        rec = g._engine.stats().get("recent", [])
        if rec:
            errs.append(float(rec[-1][1]))
    return errs


async def _unattended_night(clock, nclock, tmp_path):
    # Shared quiet-sky calibration (PE off), reused by every run below.
    clock.vt = _VT_BASE
    rig = build_sim_rig()
    r = rig["_rig"]
    cam0, tel0 = rig["guide_camera"], rig["telescope"]
    r.guide_scale_arcsec_px = _GUIDE_SCALE
    r.guide_pe_amplitude_px = 0.0
    r.guide_seeing_px = 0.05
    r.guide_drift_px_s = 0.0
    await cam0.connect()
    await tel0.connect()
    cal = await _calibrate(
        cam0, tel0,
        {"image_scale_arcsec": _GUIDE_SCALE, "exposure_s": _RENDER_EXP_S,
         "ra_algorithm": "ppec"},
        clock)

    # --- Run 1: train + absorb a camera fault + dither (no reset) ---
    clock.vt = _VT_BASE
    g1, cam1, tel1 = await _new_ppec_guider(cal, "unattended")
    await _drive(g1, cam1, tel1, clock, _LEARN_FRAMES,
                 fault_at=_LEARN_FRAMES // 3)
    assert cam1.guide_expose_fail_next_n == 0, "A1: 2-exposure fault absorbed"
    assert g1._lost is False and g1._active is True, "guiding survived the fault"

    pre = g1._engine.dump_gp_window()
    assert len(pre) > 10, "PPEC trained before the dither"
    await _drive(g1, cam1, tel1, clock, _SETTLE_FRAMES, dither_at=0)
    post = g1._engine.dump_gp_window()
    assert len(post) > 10, "A2: dither did NOT reset the trained model"
    assert len(post) >= len(pre), "A2: the trained window survived the dither"
    assert g1._engine.stats()["settling"] is False, "settle window closed"

    # --- Stop: persist the trained model to disk (clean stop only) ---
    nclock.wall = 10_000.0  # dumped_at
    await g1.stop_guiding()
    gp_file = tmp_path / "guider" / "unattended-gp.json"
    assert gp_file.exists(), "A5: PPEC model persisted on stop"
    persisted_ct = len(post)  # what dump_gp_window returned at stop

    # --- Run 2: fresh engine, SAME calibration, restore the model ---
    # Retain-or-reset GATE (amended spec §3-A5): a SHORT downtime (30 s, well
    # inside 40% of the 200 s kernel period = 80 s) restores the WHOLE window,
    # not a trim — restored count == persisted count.
    clock.vt = _VT_BASE
    g2, cam2, tel2 = await _new_ppec_guider(cal, "unattended")
    nclock.wall = 10_000.0 + _RETENTION_DOWNTIME_S
    g2._restore_gp_window()
    restored = g2._engine.dump_gp_window()
    assert len(restored) > 10, "A5: retention restored a window"
    assert len(restored) == persisted_ct, (
        "A5: retain-or-reset gate restored the WHOLE window (no trim) for a "
        "downtime within the retention horizon")
    restored_errs = await _drive(g2, cam2, tel2, clock, _MEASURE_FRAMES)
    restored_rms = _rms(restored_errs)

    # --- Baseline: fresh PPEC, NO retention, same early window ---
    clock.vt = _VT_BASE
    g3, cam3, tel3 = await _new_ppec_guider(cal, "scratch")
    scratch_errs = await _drive(g3, cam3, tel3, clock, _MEASURE_FRAMES)
    scratch_rms = _rms(scratch_errs)

    assert restored_rms < scratch_rms, (
        f"A5: retention must give immediate post-restart prediction quality "
        f"(restored={restored_rms:.4f}px vs scratch={scratch_rms:.4f}px)")


@pytest.mark.asyncio
async def test_unattended_night(monkeypatch, tmp_path):
    clock = _VirtualClock()
    nclock = _NativeClock()
    monkeypatch.setattr(simmod, "time", clock)
    monkeypatch.setattr(nativemod, "time", nclock)
    monkeypatch.setattr(nativemod, "_EXPOSE_BACKOFF_S", (0.0, 0.0, 0.0))
    monkeypatch.setattr(configmod, "CONFIG_DIR", tmp_path)
    # A-T1 punch item: fail fast on a hang rather than wedge the CI job.
    await asyncio.wait_for(
        _unattended_night(clock, nclock, tmp_path), timeout=_GATE_TIMEOUT_S)
