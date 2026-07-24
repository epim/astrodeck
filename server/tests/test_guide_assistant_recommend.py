"""Pure-logic tests for guide/assistant.py (design 2026-07-24 §5): the
``recommend`` rules (one parametrized fn), ``reduce_phaseA`` on a synthetic
drift+sinusoid, and ``BacklashRun.compute`` on synthetic N/S traces. No hardware,
no Rust wheel — this whole module is pure.
"""
import math

import numpy as np
import pytest

from astrodeck.guide import assistant as ga


def _current():
    return {"ra_algorithm": "hysteresis", "dec_algorithm": "resist_switch",
            "blc_pulse_ms": 0, "ra_params": {}, "dec_params": {}}


def _rec(recs, key):
    for r in recs:
        if r.key == key:
            return r
    return None


# --------------------------------------------------------------- recommend()
# ONE parametrized test over the design §1.3 rules (6 rows).
@pytest.mark.parametrize("phase_a, backlash, check", [
    # high backlash -> seed the measured pulse
    (ga.PhaseAResult(image_scale_arcsec=2.0, image_scale_known=True),
     ga.BacklashResult(bl_ms=430, result_code=ga.BL_VALID),
     lambda recs, rep: _rec(recs, "blc_pulse_ms").recommended == 430),
    # ~zero backlash (VALID) -> floored to 0
    (ga.PhaseAResult(image_scale_arcsec=2.0, image_scale_known=True),
     ga.BacklashResult(bl_ms=0, result_code=ga.BL_VALID),
     lambda recs, rep: _rec(recs, "blc_pulse_ms").recommended == 0),
    # high drift / poor polar -> advisory band is "bad" (never applied)
    (ga.PhaseAResult(drift_per_min_px=600.0, image_scale_arcsec=2.0,
                     image_scale_known=True),
     ga.BacklashResult(result_code=ga.BL_VALID),
     lambda recs, rep: rep["polar"]["tone"] == "bad"),
    # calm seeing -> tighter min-move than the smart-formula base
    (ga.PhaseAResult(jitter_px=0.02, rms_ra_px=0.3, image_scale_arcsec=2.0,
                     image_scale_known=True),
     ga.BacklashResult(result_code=ga.BL_VALID),
     lambda recs, rep: (_rec(recs, "min_move").recommended
                        < ga._smart_min_move(2.0, True)
                        and _rec(recs, "min_move").recommended >= 0.15)),
    # clear periodic error -> PPEC offered as an ADVANCED opt-in (never auto)
    (ga.PhaseAResult(pe_period_s=200.0, pe_amplitude_px=1.0, rms_ra_px=0.9,
                     image_scale_arcsec=2.0, image_scale_known=True),
     ga.BacklashResult(result_code=ga.BL_VALID),
     lambda recs, rep: (_rec(recs, "ra_algorithm_ppec") is not None
                        and _rec(recs, "ra_algorithm_ppec").advanced is True
                        and _rec(recs, "ra_algorithm_ppec").recommended == "ppec"
                        # the default RA algorithm stays Hysteresis (conservative)
                        and _rec(recs, "ra_algorithm").recommended == "hysteresis")),
    # sanity / too-few -> backlash floored to 0 with low confidence
    (ga.PhaseAResult(image_scale_arcsec=2.0, image_scale_known=True),
     ga.BacklashResult(bl_ms=999, result_code=ga.BL_SANITY),
     lambda recs, rep: (_rec(recs, "blc_pulse_ms").recommended == 0
                        and _rec(recs, "blc_pulse_ms").confidence == "low")),
])
def test_recommend_rules(phase_a, backlash, check):
    cur = _current()
    recs = ga.recommend(phase_a, backlash, cur)
    rep = ga.report_dict(phase_a, backlash, recs, cur, [])
    assert check(recs, rep)
    # every recommendation carries the full before/after contract (§1.3).
    for r in recs:
        d = r.to_dict()
        assert set(d) >= {"field", "current", "recommended", "unit",
                          "rationale", "confidence", "advanced"}
    # PPEC / Lowpass2 are NEVER the non-advanced default recommendation (D3).
    for key in ("ra_algorithm", "dec_algorithm"):
        r = _rec(recs, key)
        assert r.advanced is False
        assert r.recommended in ("hysteresis", "resist_switch")


def test_recommend_no_ppec_without_periodic_term():
    """No dominant period => PPEC is not even offered (conservative gate)."""
    a = ga.PhaseAResult(pe_period_s=None, pe_amplitude_px=2.0,
                        image_scale_arcsec=2.0, image_scale_known=True)
    recs = ga.recommend(a, ga.BacklashResult(result_code=ga.BL_VALID), _current())
    assert _rec(recs, "ra_algorithm_ppec") is None


# --------------------------------------------------------------- reduce_phaseA
def test_reduce_phaseA_slope_amplitude_jitter():
    """Known drift + sinusoid + tiny jitter recovers the slope (px/min), the PE
    amplitude/period, and a small jitter (design §1.1)."""
    drift, amp, period = 0.1, 1.0, 20.0     # px/s, px, s
    t = np.linspace(0.0, 60.0, 120)
    rng = np.random.default_rng(7)
    x = 320.0 + amp * np.sin(2 * math.pi * t / period) + rng.normal(0, 0.01, t.size)
    y = 240.0 + drift * t + rng.normal(0, 0.01, t.size)
    samples = [(float(t[i]), float(x[i]), float(y[i])) for i in range(t.size)]

    a = ga.reduce_phaseA(samples, 2.0, True)
    assert a.drift_per_min_px == pytest.approx(drift * 60.0, abs=0.2)  # ~6 px/min
    assert 0.85 <= a.pe_amplitude_px <= 1.35
    assert a.pe_period_s is not None and 18.0 <= a.pe_period_s <= 22.0
    assert a.jitter_px < 0.3                # slow terms rejected -> small
    assert a.rms_dec_px < 0.1              # drift de-trended out


def test_reduce_phaseA_empty_is_safe():
    a = ga.reduce_phaseA([], 1.0, False)
    assert a.n == 0 and a.drift_per_min_px == 0.0 and a.pe_period_s is None


# --------------------------------------------------------------- BacklashRun
def _run_with_traces(y_rate, meas_ms, north, south, drift_per_sec=0.0):
    r = ga.BacklashRun(y_rate, drift_per_sec, frame_w=640, frame_h=480, margin=15)
    r._meas_ms = meas_ms
    r._msmt_start_t = 0.0
    r._msmt_end_t = 0.0
    r._north_ys = list(north)
    r._south_ys = list(south)
    return r


@pytest.mark.parametrize("y_rate, meas_ms, north, south, code, bl_pos", [
    # clean backlash: north climbs 4px/step, south lags 2 steps then retraces.
    (0.008, 500,
     [0, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40],
     [40, 40, 40, 36, 32, 28, 24],
     ga.BL_VALID, True),
    # wild-negative south => SANITY, bl_px clamped to 0.
    (4.0, 1,
     [0, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40],
     [40, -40, -120],
     ga.BL_SANITY, False),
])
def test_backlash_compute(y_rate, meas_ms, north, south, code, bl_pos):
    r = _run_with_traces(y_rate, meas_ms, north, south)
    res = r.compute()
    assert res.result_code == code
    if bl_pos:
        assert res.bl_px > 0 and res.bl_ms > 0
    else:
        assert res.bl_px == 0.0                 # negative clamp (dossier §10.3)


def test_backlash_compute_too_few_north():
    """<=3 north samples is TOO_FEW_NORTH regardless of the south trace."""
    r = _run_with_traces(0.008, 500, [0, 4, 8], [8, 4, 0])
    assert r.compute().result_code == ga.BL_TOO_FEW_NORTH
