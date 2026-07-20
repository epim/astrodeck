"""Image-based cloud detection.

A cloudy frame loses its *bright* stars. Cloud cover scatters and blocks
starlight, so the frame's peak-to-noise contrast flattens and the population of
genuinely bright point sources collapses: under clear sky the brightest stars
stand tens to hundreds of sigma above the background; under cloud the brightest
thing in the frame is a handful of sigma up. This module scores a single linear
frame for cloud cover from those two robust signals.

Why *bright*-star count, not raw star count: on a bright, noisy cloudy frame the
plain k-sigma detector happily returns a hundred "stars" that are really 5-6
sigma noise peaks (verified on real cloud: 158 detections, none above 6 sigma,
median HFR bloated to ~5.5px). Counting those reads "clear" when the sky is
solid cloud. Requiring a star's *peak* to stand well above the noise
(``bright_sigma``) rejects the noise peaks — a real star clears it by a wide
margin, a noise peak never does.

This is an *image-derived* signal, complementary to the forecast-based cloud
cover in ``weather.py`` (Open-Meteo ``cloud_cover``): it looks at the sky the
camera actually sees.

Caveats — a single reading is advisory:
  * Assumes a reasonable exposure and roughly focused optics; a very short sub
    or a badly defocused frame also lacks bright tight stars.
  * The clear-reference thresholds are a per-rig calibration (aperture, focal
    length, sky). Prefer a trend across frames over one frame's verdict.
  * A bright *moonlit* cloud deck is still starless, so it reads cloudy — the
    operationally correct answer for imaging.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .stars import Star, detect_stars


@dataclass
class CloudResult:
    """Per-frame cloud verdict plus the raw metrics behind it.

    ``score`` is a smooth 0.0 (clear) .. 1.0 (fully clouded) blend; ``cloudy``
    is ``score >= threshold``. The raw fields are kept so a caller (or a human
    reading the preview payload) can see *why* and recalibrate for their rig.
    """
    cloudy: bool
    score: float              # 0.0 clear .. 1.0 fully clouded
    bright_stars: int         # stars whose peak stands >= bright_sigma over noise
    bright_density: float     # bright stars per megapixel
    contrast: float           # (bright percentile - background) / noise, "x noise"
    reason: str

    def to_dict(self) -> dict:
        return {
            "cloudy": self.cloudy,
            "score": round(self.score, 3),
            "bright_stars": int(self.bright_stars),
            "bright_density": round(self.bright_density, 2),
            "contrast": round(self.contrast, 1),
            "reason": self.reason,
        }


def _background(img: np.ndarray) -> tuple[float, float]:
    """Robust background (median) and noise (MAD*1.4826), matching detect_stars."""
    bg = float(np.median(img))
    noise = float(np.median(np.abs(img - bg))) * 1.4826
    if noise <= 0:
        noise = max(1.0, float(img.std()))
    return bg, noise


def frame_contrast(data: np.ndarray) -> float:
    """Peak-to-noise contrast: how far the brightest real signal stands above
    the background noise, in units of noise sigma.

    Uses a high percentile (99.9th) rather than ``max`` for the peak so a single
    hot pixel cannot masquerade as a bright star. Clear sky → tens to hundreds;
    cloud → a few.
    """
    img = data.astype(np.float64)
    bg, noise = _background(img)
    p_high = float(np.percentile(img, 99.9))
    return (p_high - bg) / noise


def cloud_score(
    data: np.ndarray,
    *,
    stars: list[Star] | None = None,
    bright_sigma: float = 8.0,
    clear_bright_density: float = 0.5,
    clear_contrast: float = 12.0,
    star_weight: float = 0.55,
    threshold: float = 0.5,
) -> CloudResult:
    """Score a single linear frame for cloud cover.

    Args:
        data: 2D linear image (uint16/float). Must be linear — a stretched frame
            breaks the contrast metric.
        stars: pre-detected stars if the caller already ran ``detect_stars`` (the
            capture path has them); ``None`` detects here. Peaks are inspected,
            so the ``Star`` objects are needed (not just a count).
        bright_sigma: a star counts as "bright" when its peak stands at least
            this many noise-sigma above background. Noise peaks sit ~5-6 sigma;
            real stars clear this by a wide margin.
        clear_bright_density: bright stars per megapixel at/above which the star
            signal is fully clear. Per-rig calibration knob.
        clear_contrast: peak-to-noise contrast at/above which the contrast signal
            is fully clear.
        star_weight: blend weight on the bright-star signal vs contrast (0..1).
        threshold: ``score`` at/above which ``cloudy`` is True.

    Returns:
        CloudResult with the boolean verdict, blended score and raw metrics.
    """
    img = data.astype(np.float64)
    bg, noise = _background(img)

    if stars is None:
        stars = detect_stars(data)
    bright = sum(1 for s in stars if (s.peak - bg) / noise >= bright_sigma)

    megapixels = max(data.size / 1_000_000.0, 1e-6)
    bright_density = bright / megapixels
    p_high = float(np.percentile(img, 99.9))
    contrast = (p_high - bg) / noise

    # Each sub-signal saturates to 1.0 (fully cloudy) as it drops to zero and to
    # 0.0 (clear) once it reaches its clear reference.
    s_stars = float(np.clip(1.0 - bright_density / clear_bright_density, 0.0, 1.0))
    s_contrast = float(np.clip(1.0 - contrast / clear_contrast, 0.0, 1.0))
    score = star_weight * s_stars + (1.0 - star_weight) * s_contrast
    cloudy = score >= threshold

    if cloudy:
        bits = []
        if s_stars > 0.5:
            bits.append(f"{bright} bright stars")
        if s_contrast > 0.5:
            bits.append(f"low contrast ({contrast:.0f}x noise)")
        reason = "cloudy: " + (", ".join(bits) if bits else f"score {score:.2f}")
    else:
        reason = f"clear ({bright} bright stars, {contrast:.0f}x noise)"

    return CloudResult(
        cloudy=cloudy,
        score=score,
        bright_stars=int(bright),
        bright_density=bright_density,
        contrast=contrast,
        reason=reason,
    )
