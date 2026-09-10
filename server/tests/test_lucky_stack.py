"""Lucky imaging: the selection, the ranking metric, and the alignment.

The frames are synthetic planetary discs - a limb, some banding, a background
with noise in it - because that is the shape the two design choices in
imaging/lucky.py are about: a metric that must not be limb-dominated, and an
alignment that has no stars to match.
"""
from __future__ import annotations

import numpy as np
import pytest

from astrodeck.imaging.lucky import disc_centroid, lucky_stack, sharpness

W = H = 64
BG = 500.0


def _gauss(img: np.ndarray, sigma: float) -> np.ndarray:
    """Separable Gaussian blur, numpy only (no scipy in this tree)."""
    if sigma <= 0:
        return img
    k = max(1, int(round(3 * sigma)))
    x = np.arange(-k, k + 1, dtype=np.float64)
    g = np.exp(-0.5 * (x / sigma) ** 2)
    g /= g.sum()
    out = np.apply_along_axis(lambda m: np.convolve(m, g, mode="same"), 1, img)
    return np.apply_along_axis(lambda m: np.convolve(m, g, mode="same"), 0, out)


def _disc(cx: float, cy: float, *, blur: float = 0.0, radius: float = 12.0,
          noise_seed: int | None = None) -> np.ndarray:
    """A banded disc at a SUB-PIXEL centre, optionally blurred and noised."""
    ys, xs = np.indices((H, W)).astype(np.float64)
    rr = np.hypot(xs - cx, ys - cy)
    body = np.clip(radius + 0.5 - rr, 0.0, 1.0)          # soft, sub-pixel limb
    bands = 0.5 + 0.5 * np.sin((ys - cy) * 1.3)          # surface detail
    img = body * (2000.0 + 1500.0 * bands)
    img = _gauss(img, blur)
    img = img + BG
    if noise_seed is not None:
        img = img + np.random.default_rng(noise_seed).normal(0.0, 20.0, img.shape)
    return img


#: 20 frames: 5 that landed through calm air, 15 that did not. The sub-pixel
#: offsets are the seeing moving the disc around, which is what the alignment
#: has to undo.
_OFFSETS = [(0.4, -0.3), (-1.2, 0.6), (0.9, 1.4), (-0.5, -1.1), (1.3, 0.2),
            (-2.1, 1.7), (0.7, -2.4), (2.2, 0.9), (-1.6, -0.4), (0.1, 2.1),
            (1.8, -1.3), (-0.9, 0.5), (2.4, 1.1), (-2.3, -1.9), (0.3, 1.6),
            (1.1, -0.7), (-1.4, 2.2), (0.6, 0.8), (-0.2, -1.5), (1.9, 1.3)]
SHARP = [0, 1, 2, 3, 4]


@pytest.fixture
def burst():
    """(frames, true centres). Frames 0-4 are sharp, 5-19 are not."""
    frames, centres = [], []
    for i, (dx, dy) in enumerate(_OFFSETS):
        cx, cy = 32.0 + dx, 32.0 + dy
        blur = 0.6 if i in SHARP else 2.5 + 0.1 * i
        frames.append(_disc(cx, cy, blur=blur, noise_seed=100 + i))
        centres.append((cx, cy))
    return frames, centres


# ------------------------------------------------------------------ the metric

def test_sharpness_orders_a_blur_ladder_monotonically():
    """Gradient energy must fall every time the frame gets softer - if it does
    not, the ranking is noise and the 'lucky' selection is a lottery."""
    ladder = [sharpness(_disc(32.0, 32.0, blur=b))
              for b in (0.0, 0.5, 1.0, 2.0, 3.0, 4.0)]
    assert ladder == sorted(ladder, reverse=True), ladder
    assert all(b < a for a, b in zip(ladder, ladder[1:])), (
        f"a blur step that did not lower the score: {ladder}")
    assert ladder[0] > ladder[-1] * 5, (
        "the metric must SEPARATE, not merely order - a 5 percent spread over "
        "the whole ladder cannot pick 25 percent of a burst")


def test_sharpness_refuses_a_frame_that_is_not_two_dimensional():
    with pytest.raises(ValueError, match="2-D frame"):
        sharpness(np.zeros((4, 4, 3)))


# --------------------------------------------------------------- the centroid

def test_the_disc_centroid_finds_a_sub_pixel_centre():
    for cx, cy in ((32.0, 32.0), (28.4, 35.7), (36.2, 27.1)):
        got = disc_centroid(_disc(cx, cy, blur=0.8, noise_seed=7))
        assert got is not None
        assert abs(got[0] - cx) < 0.5 and abs(got[1] - cy) < 0.5, (
            f"centroid {got} for a disc truly at {(cx, cy)}")


def test_a_frame_with_nothing_above_background_has_no_centroid():
    flat = np.full((H, W), BG) + np.random.default_rng(1).normal(0, 20, (H, W))
    assert disc_centroid(flat) is None


# -------------------------------------------------------------- the selection

def test_lucky_stack_keeps_exactly_the_sharp_frames(burst):
    """SABOTAGE TARGET: reverse the sharpness sort in lucky_stack.

    Reversed, it keeps the WORST quarter of the burst - and still returns a
    stack, still returns a picture of the planet, still looks plausible in the
    UI. The only thing that changes is that the detail the whole selection
    exists to preserve is gone."""
    frames, _ = burst
    result = lucky_stack(frames, keep_pct=25.0)
    assert result.kept == SHARP, (
        f"kept {result.kept}; the five sharp frames are {SHARP}")
    assert result.reference in SHARP
    assert len(result.scores) == len(frames)
    assert min(result.scores[i] for i in SHARP) > max(
        result.scores[i] for i in range(len(frames)) if i not in SHARP), (
        "precondition: the sharp frames really do score above every blurred one")


def test_keep_pct_decides_how_many_survive(burst):
    frames, _ = burst
    assert len(lucky_stack(frames, keep_pct=50.0).kept) == 10
    assert len(lucky_stack(frames, keep_pct=5.0).kept) == 1
    assert len(lucky_stack(frames, keep_pct=100.0).kept) == 20


# -------------------------------------------------------------- the alignment

def test_the_stacked_disc_lands_where_the_reference_frame_put_it(burst):
    frames, centres = burst
    result = lucky_stack(frames, keep_pct=25.0)
    truth = centres[result.reference]
    got = disc_centroid(result.image)
    assert got is not None
    assert abs(got[0] - truth[0]) < 0.5 and abs(got[1] - truth[1]) < 0.5, (
        f"stack centroid {got} against the reference frame's true centre {truth}")
    assert result.aligned == len(result.kept), (
        "every kept frame had a measurable disc, so every one should have been "
        "shifted rather than stacked where it fell")
    assert result.image.dtype == np.float32


def test_stacking_actually_reduces_noise(burst):
    """The other half of the bargain: selection keeps detail, stacking kills
    noise. If the background of the stack is no quieter than one frame's, the
    frames were not combined."""
    frames, _ = burst
    result = lucky_stack(frames, keep_pct=50.0)
    corner = (slice(0, 12), slice(0, 12))            # off the disc entirely
    single = float(np.std(frames[result.reference][corner]))
    stacked = float(np.std(result.image[corner]))
    assert stacked < single * 0.8, f"single {single:.1f} vs stacked {stacked:.1f}"


def test_frames_of_different_shapes_are_refused(burst):
    frames, _ = burst
    frames[3] = frames[3][:, :-4]
    with pytest.raises(ValueError, match="same recording"):
        lucky_stack(frames, keep_pct=25.0)


def test_an_empty_burst_is_refused():
    with pytest.raises(ValueError, match="at least one frame"):
        lucky_stack([])
