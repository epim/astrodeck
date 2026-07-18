import math
import numpy as np
import pytest

native = pytest.importorskip("astrodeck_native")  # skip cleanly when wheel absent

def _gaussian_frame(w, h, cx, cy, amp=4000.0, sg=1.6, bg=100):
    yy, xx = np.mgrid[0:h, 0:w]
    g = amp * np.exp(-(((xx-cx)**2 + (yy-cy)**2) / (2*sg*sg)))
    return np.clip(bg + g, 0, 65535).astype(np.uint16)

def test_guide_star_find_returns_candidates():
    frame = _gaussian_frame(64, 64, 32.4, 30.6)
    stars, meta = native.guide_star_find(frame)
    assert len(stars) >= 1
    s = stars[0]
    assert abs(s["x"] - 32.4) < 0.6 and abs(s["y"] - 30.6) < 0.6
    assert s["snr"] > 5.0
    assert "sat_thresh" in meta

def test_engine_process_pulse_pair_shape():
    cfg = {"image_scale_arcsec": 2.0, "ra_algorithm": "hysteresis",
           "dec_algorithm": "resist_switch", "dec_guide_mode": "auto"}
    e = native.GuideEngine(cfg)
    # inject a calibration so the offset->pulse path is deterministic
    e.load_calibration({"x_rate": 0.01, "y_rate": 0.01, "x_angle": 0.0,
                        "y_angle": math.pi/2, "y_angle_error": 0.0,
                        "declination": 0.0, "pier_side": "west",
                        "ra_parity": "even", "dec_parity": "even",
                        "rotator_angle": 0.0, "binning": 1, "is_valid": True})
    e.begin_guiding()
    f0 = _gaussian_frame(64, 64, 32.0, 32.0)
    a0 = e.process(f0, 0.0, 2.0)  # establishes lock
    assert a0["action"] in ("idle", "pulse_pair", "settle")
    f1 = _gaussian_frame(64, 64, 37.0, 32.0)  # +5px in x
    a1 = e.process(f1, 2.0, 2.0)
    assert a1["action"] == "pulse_pair"
    assert a1["ra"] is not None and a1["ra"]["dir"] == "west"
    assert a1["ra"]["ms"] > 0

def test_calibration_roundtrips():
    e = native.GuideEngine({"image_scale_arcsec": 2.0})
    cal = {"x_rate": 0.02, "y_rate": 0.018, "x_angle": 0.1, "y_angle": 1.6,
           "y_angle_error": 0.0, "declination": 0.2, "pier_side": "east",
           "ra_parity": "odd", "dec_parity": "even", "rotator_angle": 0.0,
           "binning": 1, "is_valid": True}
    e.load_calibration(cal)
    out = e.dump_calibration()
    assert out is not None
    assert abs(out["x_rate"] - 0.02) < 1e-9 and out["pier_side"] == "east"


# ---- lock_lost reason disambiguation (T8 obligation, P1-T7 review binding:
# the Action enum overloads LockLost for three distinct failures; process()'s
# "reason" field must let the host distinguish re-acquire vs recalibrate vs
# settle-timeout) ----

def _ident_cal():
    return {"x_rate": 0.01, "y_rate": 0.01, "x_angle": 0.0,
            "y_angle": math.pi/2, "y_angle_error": 0.0,
            "declination": 0.0, "pier_side": "west",
            "ra_parity": "even", "dec_parity": "even",
            "rotator_angle": 0.0, "binning": 1, "is_valid": True}


def test_process_reason_star_lost():
    e = native.GuideEngine({})
    e.load_calibration(_ident_cal())
    e.begin_guiding()
    a = e.process(_gaussian_frame(64, 64, 32.0, 32.0), 0.0, 2.0)  # lock
    assert a["action"] == "idle" and a["reason"] is None
    blank = np.full((64, 64), 100, dtype=np.uint16)  # starless frames
    t, last = 2.0, None
    for _ in range(30):
        last = e.process(blank, t, 2.0)
        t += 2.0
        if last["action"] == "lock_lost":
            break
    assert last["action"] == "lock_lost"
    assert last["reason"] == "star_lost"


def test_process_reason_settle_timeout():
    # P2-T1: dither() now does a REAL lock shift + fast recenter (dossier
    # §11.2), not the P1 stub's immediate settle-wait. dither(3.0, 3.0) with
    # the default search_region (15px) recenters in exactly ONE step (step
    # size 0.7*15=10.5px > the 3*sqrt(2)~=4.24px dither distance), so the
    # very first settling frame is a fast-recenter "pulse_pair", not
    # "settle" -- proving process()'s "action" alone cannot tell a recenter
    # frame apart from normal guiding, which is exactly why stats() gained
    # the "settling" key (P2-T1 Produces line / punch-list #3).
    e = native.GuideEngine({})
    e.load_calibration(_ident_cal())
    e.begin_guiding()
    e.process(_gaussian_frame(64, 64, 32.0, 32.0), 0.0, 2.0)  # lock (32,32)
    e.dither(3.0, 3.0)  # new lock ~(35,35); opens the default 1.5px/10s/60s
                         # settle window; 1-step recenter (3 < 10.5)
    off = _gaussian_frame(64, 64, 42.0, 32.0)  # 10px-ish error: never in range

    # First settling frame: fast recenter fires (and finishes, for this
    # dither distance) before any Settle wait. The 60s timeout clock
    # anchors here (t=2) regardless.
    a = e.process(off, 2.0, 2.0)
    assert a["action"] == "pulse_pair" and a["reason"] is None
    assert e.stats()["settling"] is True, (
        "the settle window must stay open across a fast-recenter frame, "
        "not just frames whose action is literally 'settle'")

    # Out-of-tolerance dwell frame: since the P2-T1 fix round the dwell keeps
    # GUIDING (guider.cpp:1517-1521 -- settle is a parallel monitor, not a
    # phase that suspends guiding), so this frame carries an ordinary
    # correction while stats()["settling"] stays True.
    a = e.process(off, 30.0, 2.0)
    assert a["action"] == "pulse_pair" and a["reason"] is None
    assert e.stats()["settling"] is True
    a = e.process(off, 62.0, 2.0)  # past the 60 s deadline (anchored at t=2)
    assert a["action"] == "lock_lost"
    assert a["reason"] == "settle_timeout"
    assert e.stats()["settling"] is False
    # the window and shadow flag both clear: guiding resumes next frame
    a = e.process(_gaussian_frame(64, 64, 37.0, 32.0), 64.0, 2.0)
    assert a["action"] == "pulse_pair" and a["reason"] is None


def test_process_reason_calibration_failed():
    e = native.GuideEngine({})
    static = _gaussian_frame(64, 64, 32.0, 32.0)  # star never moves
    e.begin_calibration(32.0, 32.0)
    t, cal_steps, last = 0.0, 0, None
    for _ in range(100):
        last = e.process(static, t, 2.0)
        t += 2.0
        if last["action"] == "cal_step":
            cal_steps += 1
        else:
            break
    assert last["action"] == "lock_lost"
    assert last["reason"] == "calibration_failed"
    # upstream-literal budget: max_steps + 1 = 61 pulses before GO_WEST fails
    assert cal_steps == 61
