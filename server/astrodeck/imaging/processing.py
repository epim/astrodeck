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
import math

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
    so the server ``/render.png`` and the client agree on the mapping.

    TOTAL by contract: the result is always three finite numbers, whatever it is
    handed. ``/render.png`` takes these three straight off the query string as
    plain floats, and FastAPI accepts ``?black=nan`` for a ``float`` param —
    ``np.clip`` PROPAGATES NaN rather than clamping it, so a NaN used to survive
    every guard here, poison the whole stretched array, and encode as a valid
    84-byte all-black PNG returned with HTTP 200. A user asking for a baked
    export got a blank frame and no error. ``+-inf`` was never a problem (clip
    saturates it to the range end, which is the sensible reading of "stretch
    infinitely"), so only the NaN case substitutes the neutral default.
    """
    black = _neutral_if_nan(black, 0.0)
    mid = _neutral_if_nan(mid, 0.5)
    white = _neutral_if_nan(white, 1.0)
    black = float(np.clip(black, 0.0, 1.0))
    white = float(np.clip(white, 0.0, 1.0))
    if white <= black:
        white = min(1.0, black + 1e-4)
    mid = float(np.clip(mid, 1e-4, 1 - 1e-4))
    return black, mid, white


def _neutral_if_nan(v: float, default: float) -> float:
    """``v`` unless it is NaN (which has no meaningful clamp), else ``default``.
    ``float(v)`` first so a numpy scalar or an int is handled the same way."""
    v = float(v)
    return default if math.isnan(v) else v


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
    """Shared 8-bit encode (grey), downscaling to ``max_width``.

    ``img01`` must be a 2-D display-domain array in [0,1]; the returned width and
    height are MEASURED off the encoded image, never predicted, because the hub
    publishes them as ``display_width``/``display_height`` and the client sizes
    the stage from them.

    The two lines below are the only place in the preview path where raw bytes
    meet a separately-supplied row length, so they are the only place a stride
    can go wrong — and PIL will not stop it. ``Image.fromarray(arr, mode="L")``
    does NOT check that the buffer is one byte per pixel: it lays the image over
    the buffer at stride == width and reads ``width`` bytes per row. Hand it a
    2-byte-per-pixel (uint16) array under mode "L" and every output row starts
    half a row further into the previous one, so the frame comes out squeezed,
    sheared diagonally and repeated down the canvas — encoded happily, returned
    with HTTP 200, indistinguishable downstream from a good frame. That is the
    artefact the Capture preview showed on 2026-07-31. So: the array is forced
    to one byte per pixel FIRST, the image is then built from the array's own
    dtype (no ``mode=`` reinterpretation — also removed outright in Pillow 13),
    and anything that is not a plain 2-D frame is refused rather than guessed
    at. A missing preview is honest; a sheared one is a lie about the sky.
    """
    a = np.asarray(img01)
    if a.ndim != 2:
        raise ValueError(
            f"preview encode needs a 2-D frame, got shape {a.shape}; "
            "encoding it would shear the image rather than fail")
    # uint8 == one byte per pixel == row stride is exactly the frame width.
    arr8 = (np.clip(a, 0, 1) * 255).astype(np.uint8)
    pil = Image.fromarray(arr8)          # dtype-driven: 2-D uint8 -> mode "L"
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
           quality_8bit: bool = True, *, black: float | None = None,
           mid: float | None = None, white: float | None = None) -> bytes:
    """Encode a frame as PNG for the UI preview (lossless base / download).

    With all three of ``black``/``mid``/``white`` given, replays those exact
    levels (``stretch_with``) so a server-baked render matches the live preview
    LUT — mirrors ``to_jpeg``. Otherwise auto-stretches (``stretch=True``) or
    passes the linear data through (``stretch=False``)."""
    if black is not None and mid is not None and white is not None:
        img = stretch_with(data, black, mid, white)
    else:
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
