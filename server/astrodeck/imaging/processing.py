"""Image display pipeline: auto-stretch, histogram, PNG/JPEG encoding.

The stretch is the classic midtones-transfer-function (MTF) screen stretch
used by PixInsight/NINA/ASIAIR — it makes dim nebulosity visible without
clipping stars.

Two domains live here, and the live-preview overhaul depends on keeping them
straight (spec §2/§4.6):

* the **auto** path (``auto_stretch``/``auto_levels``) derives black/mid/white
  from the frame's own statistics, so the UI handles seed where the image
  actually looks right;
* the **explicit-levels** path (``stretch_with``) replays a user's chosen
  black/mid/white so the client LUT and a future ``/render`` agree.

A histogram of a *linear* light frame is a useless left-edge spike, so the UI
operates on the **display-domain** histogram (``display_histogram``); the linear
one is kept only behind the Advanced disclosure (``compute_histogram``).
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


def auto_levels(data: np.ndarray, target_bg: float = 0.18,
                shadow_clip: float = -2.8) -> tuple[float, float, float]:
    """The (black, mid, white) the auto-stretch implies, in display 0..1.

    ``black``/``white`` are the shadow/highlight clip points (fractions of the
    full 16-bit range); ``mid`` is the midtones balance *in the clipped sub-range*
    that ``stretch_with`` consumes. These seed the UI handles so a Manual stretch
    starts where the image actually looks right (spec §4.6, finding #9).
    """
    x = data.astype(np.float64) / 65535.0
    median = float(np.median(x))
    mad = float(np.median(np.abs(x - median))) * 1.4826
    shadows = max(0.0, min(1.0, median + shadow_clip * mad))
    rng = 1.0 - shadows
    if rng <= 0:
        return 0.0, 0.5, 1.0
    m_bg = float(np.clip((median - shadows) / rng, 1e-6, 1 - 1e-6))
    midtones = _mtf(target_bg, np.array([m_bg]))[0] if m_bg > 0 else 0.5
    midtones = float(np.clip(midtones, 1e-4, 1 - 1e-4))
    return round(shadows, 6), round(midtones, 6), 1.0


def levels_to_mtf(black: float, mid: float, white: float) -> tuple[float, float, float]:
    """Normalize a (black, mid, white) triple into a usable MTF triple.

    Clamps the order (black < white), keeps the midtones balance in (0,1), and
    returns the same three numbers the client LUT uses. Kept as a named function
    so the server ``/render`` (Pass 2) and the client agree on the mapping.
    """
    black = float(np.clip(black, 0.0, 1.0))
    white = float(np.clip(white, 0.0, 1.0))
    if white <= black:
        white = min(1.0, black + 1e-4)
    mid = float(np.clip(mid, 1e-4, 1 - 1e-4))
    return black, mid, white


def stretch_with(data: np.ndarray, black: float, mid: float,
                 white: float) -> np.ndarray:
    """Explicit-levels MTF stretch → float [0,1].

    ``black``/``white`` are shadow/highlight clip fractions of the full 16-bit
    range; ``mid`` is the midtones balance applied inside the clipped sub-range.
    Mirrors the client LUT so a baked download matches the live preview.
    """
    black, mid, white = levels_to_mtf(black, mid, white)
    x = data.astype(np.float64) / 65535.0
    rng = white - black
    xs = np.clip((x - black) / rng, 0, 1)
    return np.clip(_mtf(mid, xs), 0, 1)


def compute_histogram(data: np.ndarray, bins: int = 128) -> list[int]:
    """Histogram of the LINEAR 16-bit data (left-edge spike for a light frame).

    Kept for the Advanced "linear" view; the UI's primary histogram is the
    display-domain one below."""
    hist, _ = np.histogram(data, bins=bins, range=(0, 65535))
    return hist.astype(int).tolist()


def display_histogram(stretched01: np.ndarray, bins: int = 128) -> list[int]:
    """Histogram of the DISPLAY-domain (already-stretched, [0,1]) image so the
    stretch handles have usable travel rather than piling on the left edge."""
    hist, _ = np.histogram(stretched01, bins=bins, range=(0.0, 1.0))
    return hist.astype(int).tolist()


def _encode(img01: np.ndarray, *, max_width: int, fmt: str,
            quality: int) -> tuple[bytes, int, int]:
    """Shared 8-bit encode (mode 'L'), downscaling to ``max_width``."""
    arr8 = (np.clip(img01, 0, 1) * 255).astype(np.uint8)
    pil = Image.fromarray(arr8, mode="L")
    if pil.width > max_width:
        scale = max_width / pil.width
        pil = pil.resize((max_width, max(1, int(pil.height * scale))), Image.BILINEAR)
    buf = io.BytesIO()
    if fmt == "JPEG":
        pil.save(buf, format="JPEG", quality=quality, optimize=False)
    else:
        pil.save(buf, format="PNG", optimize=False, compress_level=3)
    return buf.getvalue(), pil.width, pil.height


def to_png(data: np.ndarray, stretch: bool = True, max_width: int = 1400,
           quality_8bit: bool = True) -> bytes:
    """Encode a frame as PNG for the UI preview (lossless base / download)."""
    img = auto_stretch(data) if stretch else data.astype(np.float64) / 65535.0
    return _encode(img, max_width=max_width, fmt="PNG", quality=0)[0]


def to_jpeg(data: np.ndarray, *, black: float | None = None,
            mid: float | None = None, white: float | None = None,
            max_width: int = 1400, quality: int = 85) -> tuple[bytes, int, int]:
    """Stretched 8-bit JPEG (mode 'L') for the live loop. Returns (bytes, w, h).

    With no explicit levels it uses the auto-stretch; with all three it replays
    those exact levels (so a download matches a manual on-screen stretch)."""
    if black is None or mid is None or white is None:
        img = auto_stretch(data)
    else:
        img = stretch_with(data, black, mid, white)
    return _encode(img, max_width=max_width, fmt="JPEG", quality=quality)


def to_thumb(data_or_img, *, max_width: int = 160, quality: int = 70) -> bytes:
    """~160px JPEG thumbnail for the filmstrip. Accepts either raw 16-bit data
    (auto-stretched first) or pre-encoded display bytes (decoded + downscaled)."""
    if isinstance(data_or_img, (bytes, bytearray)):
        try:
            pil = Image.open(io.BytesIO(bytes(data_or_img))).convert("L")
        except Exception:
            return b""
        if pil.width > max_width:
            scale = max_width / pil.width
            pil = pil.resize((max_width, max(1, int(pil.height * scale))), Image.BILINEAR)
        buf = io.BytesIO()
        pil.save(buf, format="JPEG", quality=quality, optimize=False)
        return buf.getvalue()
    img = auto_stretch(np.asarray(data_or_img))
    return _encode(img, max_width=max_width, fmt="JPEG", quality=quality)[0]


def frame_stats(data: np.ndarray) -> dict:
    return {
        "min": int(data.min()),
        "max": int(data.max()),
        "mean": round(float(data.mean()), 1),
        "median": int(np.median(data)),
        "std": round(float(data.std()), 1),
    }
