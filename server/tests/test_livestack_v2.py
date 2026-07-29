"""Live stacking: sub-pixel accumulation, trail rejection, weak alignment.

test_livestack.py covers the v1 contract (seed, coverage plane, drift-reject,
re-anchor) and still passes unchanged — the point of these is the behaviour v1
did not have, each one a thing that visibly ruined a live stack:

  * a half-pixel drift smearing every star, because shifts were integer;
  * a satellite crossing the field and staying in the stack forever;
  * a stack silently trusting a one-star alignment as if it were a match.
"""
from __future__ import annotations

import math

import numpy as np

from astrodeck.imaging.livestack import LiveStacker, MIN_FRAMES_FOR_CLIP


def field(dx: float = 0.0, dy: float = 0.0, *, shape=(160, 200), bg=400.0,
          noise=3.0, seed=7) -> np.ndarray:
    """A field of six stars — enough for the constellation match — shifted by
    (dx, dy). Sub-pixel offsets are real: the Gaussians are evaluated at the
    fractional centre, not rounded."""
    rng = np.random.default_rng(seed)
    h, w = shape
    img = rng.normal(bg, noise, shape)
    xs = np.arange(w)
    ys = np.arange(h)[:, None]
    for cx, cy, flux in ((40, 30, 300_000), (150, 45, 240_000),
                         (90, 110, 180_000), (170, 130, 150_000),
                         (25, 125, 120_000), (120, 75, 90_000)):
        sigma = 1.8
        img += flux * np.exp(
            -((xs - cx - dx) ** 2 + (ys - cy - dy) ** 2) / (2 * sigma ** 2)
        ) / (2 * math.pi * sigma ** 2)
    return np.clip(img, 0, 65535).astype(np.uint16)


def _peak_sharpness(img: np.ndarray) -> float:
    """Max pixel of the brightest star. A smeared star spreads the same flux
    over more pixels, so its peak drops — this is the measurement that makes
    "sub-pixel alignment keeps stars round" falsifiable."""
    return float(img.max())


# ------------------------------------------------------ sub-pixel accumulation

def test_a_half_pixel_drift_does_not_smear_the_stack():
    """The v1 failure this exists for: whole-pixel shifts round a 0.5 px drift
    to 0 or 1, so every other sub lands half a pixel off and the stack's stars
    are visibly fatter than a single sub's."""
    subs = [field(0.0, 0.0), field(0.5, 0.5), field(1.0, 1.0), field(1.5, 1.5)]
    s = LiveStacker()
    for f in subs:
        s.add(f, 60.0)
    stacked = s.mean()
    assert stacked is not None
    assert s.frames == 4
    # The stacked peak must stay close to a single sub's. Averaging always
    # softens a little (noise differs); smearing by half a pixel costs far more.
    assert _peak_sharpness(stacked) > 0.80 * _peak_sharpness(subs[0])


def test_sub_pixel_shifts_are_measured_not_rounded():
    s = LiveStacker()
    s.add(field(0, 0), 60.0)
    o = s.add(field(2.5, -1.5), 60.0)
    assert o.accepted
    assert abs(o.dx - 2.5) < 0.4 and abs(o.dy + 1.5) < 0.4
    assert o.support >= 3          # a real constellation match, not the fallback


def test_the_mean_of_identical_subs_is_that_sub():
    """Coverage-plane sanity: averaging N copies must not drift the level."""
    f = field()
    s = LiveStacker()
    for _ in range(5):
        s.add(f, 30.0)
    m = s.mean()
    assert m is not None
    assert abs(float(m.mean()) - float(f.mean())) < 1.0


# --------------------------------------------------------- outlier rejection

def _with_trail(img: np.ndarray, row: int = 70) -> np.ndarray:
    """A satellite: one saturated row straight across the frame."""
    out = img.copy()
    out[row, :] = 60000
    return out


def test_a_satellite_trail_is_kept_out_of_the_stack():
    s = LiveStacker()
    for _ in range(MIN_FRAMES_FOR_CLIP + 2):
        s.add(field(), 30.0)
    clean = s.mean()
    assert clean is not None
    before = float(clean[70].mean())

    o = s.add(_with_trail(field()), 30.0)
    assert o.accepted
    assert o.clipped > 100, "the trail should have been clipped, pixel by pixel"
    after = float(s.mean()[70].mean())
    # The trail row must be essentially where it was — not pulled toward 60000.
    assert after - before < 50, f"trail leaked into the stack ({before} -> {after})"


def test_the_trail_does_not_become_a_dark_streak():
    """Dropping the outlier instead of substituting the mean would leave that
    row with fewer samples — visible as a dark band once the levels stretch."""
    s = LiveStacker()
    for _ in range(MIN_FRAMES_FOR_CLIP + 2):
        s.add(field(), 30.0)
    s.add(_with_trail(field()), 30.0)
    m = s.mean()
    assert m is not None
    row, neighbour = float(m[70].mean()), float(m[68].mean())
    assert abs(row - neighbour) < 40, "the clipped row diverged from its neighbours"


def test_clipping_waits_for_enough_frames_to_estimate_noise():
    """Below MIN_FRAMES_FOR_CLIP the variance estimate is meaningless, and
    clipping on it would eat real stars."""
    s = LiveStacker()
    s.add(field(), 30.0)
    o = s.add(_with_trail(field()), 30.0)
    assert o.clipped == 0


def test_clipping_can_be_disarmed():
    s = LiveStacker(clip_sigma=0)
    for _ in range(MIN_FRAMES_FOR_CLIP + 2):
        s.add(field(), 30.0)
    o = s.add(_with_trail(field()), 30.0)
    assert o.clipped == 0


def test_real_stars_survive_clipping():
    """The guard on the guard: a stack that clips its own stars would look
    'clean' and be worthless."""
    s = LiveStacker()
    subs = [field() for _ in range(MIN_FRAMES_FOR_CLIP + 4)]
    for f in subs:
        s.add(f, 30.0)
    m = s.mean()
    assert m is not None
    assert _peak_sharpness(m) > 0.80 * _peak_sharpness(subs[0])


# ------------------------------------------------------------ weak alignment

def _one_star(cx, cy, shape=(160, 200)):
    rng = np.random.default_rng(11)
    h, w = shape
    img = rng.normal(400, 3.0, shape)
    xs = np.arange(w) - cx
    ys = (np.arange(h) - cy)[:, None]
    img += 300_000 * np.exp(-(xs ** 2 + ys ** 2) / (2 * 1.8 ** 2)) / (2 * math.pi * 1.8 ** 2)
    return np.clip(img, 0, 65535).astype(np.uint16)


def test_a_single_star_field_still_stacks_and_says_it_is_weak():
    """Refusing here would make Live View useless on the fields that need it
    most — a planetary nebula at long focal length, or a sky with three stars
    above the light pollution. It falls back to v1's anchor and reports it."""
    s = LiveStacker()
    s.add(_one_star(100, 80), 30.0)
    o = s.add(_one_star(103, 78), 30.0)
    assert o.accepted
    assert o.reason == "weak_align"
    assert o.support == 1
    assert abs(o.dx - 3) < 1.5 and abs(o.dy + 2) < 1.5


def test_a_real_constellation_is_not_reported_as_weak():
    s = LiveStacker()
    s.add(field(), 30.0)
    o = s.add(field(2, 2), 30.0)
    assert o.accepted and o.reason == "" and o.support >= 3


def test_a_starless_sub_is_rejected_outright():
    s = LiveStacker()
    s.add(field(), 30.0)
    blank = np.full((160, 200), 400, dtype=np.uint16)
    o = s.add(blank, 30.0)
    assert not o.accepted and o.reason == "no_stars"
    assert s.frames == 1


def test_reset_clears_everything_including_the_clip_counter():
    s = LiveStacker()
    for _ in range(MIN_FRAMES_FOR_CLIP + 2):
        s.add(field(), 30.0)
    s.add(_with_trail(field()), 30.0)
    assert s.clipped > 0
    s.reset()
    assert s.frames == 0 and s.rejected == 0 and s.clipped == 0
    assert s.mean() is None
