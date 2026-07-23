"""Live-stacking core: brightest-star alignment, running-mean accumulation with
per-pixel coverage, drift-reject, and auto re-anchor. Synthetic shifted frames."""
import math
import numpy as np
from astrodeck.imaging import LiveStacker, brightest_centroid, align_offset
from astrodeck.imaging import detect_stars


def star_frame(cx: float, cy: float, *, shape=(200, 240), bg=400.0, noise=5.0,
               flux=300_000.0, sigma=1.6, seed=3) -> np.ndarray:
    """One bright star (the alignment anchor) at (cx, cy) on a faint background."""
    rng = np.random.default_rng(seed)
    h, w = shape
    img = rng.normal(bg, noise, shape)
    xs = np.arange(w) - cx
    ys = (np.arange(h) - cy)[:, None]
    img += flux * np.exp(-(xs**2 + ys**2) / (2 * sigma**2)) / (2 * math.pi * sigma**2)
    return np.clip(img, 0, 65535).astype(np.uint16)


def test_brightest_centroid_picks_highest_flux():
    f = star_frame(120.0, 100.0)
    c = brightest_centroid(detect_stars(f))
    assert c is not None
    assert abs(c[0] - 120.0) < 1.5 and abs(c[1] - 100.0) < 1.5

def test_brightest_centroid_none_when_no_stars():
    assert brightest_centroid([]) is None

def test_align_offset_arithmetic():
    assert align_offset((100.0, 100.0), (103.0, 98.0)) == (3.0, -2.0)

def test_first_sub_seeds_and_mean_matches():
    s = LiveStacker()
    o = s.add(star_frame(120.0, 100.0), 120.0)
    assert o.accepted and o.frames == 1 and o.reason == ""
    assert abs(o.integrated_s - 120.0) < 1e-6
    assert s.mean() is not None and s.mean().shape == (200, 240)

def test_shifted_sub_registers_onto_reference():
    s = LiveStacker()
    a = star_frame(120.0, 100.0)
    s.add(a, 120.0)
    # star moved +3 in x, -2 in y (roll keeps it far from edges)
    b = np.roll(a, shift=(-2, 3), axis=(0, 1))
    o = s.add(b, 120.0, stars=detect_stars(b))
    assert o.accepted and o.frames == 2
    assert round(o.dx) == 3 and round(o.dy) == -2
    # both subs' star sits at the reference (120,100) in the mean -> that pixel is
    # brighter than the un-covered corner, and integration doubled.
    m = s.mean()
    assert m[100, 120] > m[0, 0]
    assert abs(s.integrated_s - 240.0) < 1e-6

def test_drift_beyond_threshold_is_rejected():
    s = LiveStacker(reject_frac=0.08)   # 0.08 * min(200,240)=200 -> 16 px
    s.add(star_frame(120.0, 100.0), 120.0)
    o = s.add(star_frame(160.0, 100.0), 120.0)   # 40 px drift > 16
    assert not o.accepted and o.reason == "drift"
    assert o.frames == 1 and o.rejected == 1

def test_three_consecutive_drifts_reanchor():
    s = LiveStacker(reject_frac=0.08, reanchor_after=3)
    s.add(star_frame(120.0, 100.0), 120.0)
    for _ in range(2):
        o = s.add(star_frame(170.0, 100.0), 120.0)
        assert o.reason == "drift"
    o = s.add(star_frame(170.0, 100.0), 120.0)   # 3rd consecutive -> reseed
    assert o.reason == "reseed" and o.frames == 1

def test_size_change_reseeds():
    s = LiveStacker()
    s.add(star_frame(120.0, 100.0), 120.0)
    o = s.add(star_frame(60.0, 50.0, shape=(100, 120)), 120.0)
    assert o.reason == "size" and o.frames == 1 and s.mean().shape == (100, 120)

def test_no_stars_rejected_without_reanchor_spiral():
    s = LiveStacker()
    s.add(star_frame(120.0, 100.0), 120.0)
    blank = np.full((200, 240), 400, dtype=np.uint16)
    o = s.add(blank, 120.0, stars=[])
    assert not o.accepted and o.reason == "no_stars" and o.frames == 1

def test_reset_clears_accumulator():
    s = LiveStacker()
    s.add(star_frame(120.0, 100.0), 120.0)
    s.reset()
    assert s.frames == 0 and s.integrated_s == 0.0 and s.mean() is None
