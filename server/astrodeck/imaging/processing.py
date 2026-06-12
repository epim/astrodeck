"""Image display pipeline: auto-stretch, histogram, PNG encoding.

The stretch is the classic midtones-transfer-function (MTF) screen stretch
used by PixInsight/NINA/ASIAIR — it makes dim nebulosity visible without
clipping stars.
"""
from __future__ import annotations

import io

import numpy as np
from PIL import Image


def _mtf(m: float, x: np.ndarray) -> np.ndarray:
    """Midtones transfer function, x in [0,1]."""
    return (m - 1) * x / ((2 * m - 1) * x - m)


def auto_stretch(data: np.ndarray, target_bg: float = 0.18,
                 shadow_clip: float = -2.8) -> np.ndarray:
    """Return display-stretched float image in [0,1]."""
    x = data.astype(np.float64) / 65535.0
    median = float(np.median(x))
    # MAD-based noise estimate, like the PI STF
    mad = float(np.median(np.abs(x - median))) * 1.4826
    shadows = max(0.0, min(1.0, median + shadow_clip * mad))
    rng = 1.0 - shadows
    if rng <= 0:
        return np.clip(x, 0, 1)
    xs = np.clip((x - shadows) / rng, 0, 1)
    m_bg = float(np.clip((median - shadows) / rng, 1e-6, 1 - 1e-6))
    midtones = _mtf(target_bg, np.array([m_bg]))[0] if m_bg > 0 else 0.5
    midtones = float(np.clip(midtones, 1e-4, 1 - 1e-4))
    return np.clip(_mtf(midtones, xs), 0, 1)


def compute_histogram(data: np.ndarray, bins: int = 128) -> list[int]:
    hist, _ = np.histogram(data, bins=bins, range=(0, 65535))
    return hist.astype(int).tolist()


def to_png(data: np.ndarray, stretch: bool = True, max_width: int = 1400,
           quality_8bit: bool = True) -> bytes:
    """Encode a frame for the UI preview."""
    img = auto_stretch(data) if stretch else data.astype(np.float64) / 65535.0
    arr8 = (img * 255).astype(np.uint8)
    pil = Image.fromarray(arr8, mode="L")
    if pil.width > max_width:
        scale = max_width / pil.width
        pil = pil.resize((max_width, int(pil.height * scale)), Image.BILINEAR)
    buf = io.BytesIO()
    pil.save(buf, format="PNG", optimize=False, compress_level=3)
    return buf.getvalue()


def frame_stats(data: np.ndarray) -> dict:
    return {
        "min": int(data.min()),
        "max": int(data.max()),
        "mean": round(float(data.mean()), 1),
        "median": int(np.median(data)),
        "std": round(float(data.std()), 1),
    }
