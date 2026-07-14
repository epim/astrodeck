"""Local HiPS -> TAN cutout renderer (offline-pack spec §3).

Same output contract as the hips2fits proxy: square TAN JPEG, North-up,
East-left, no rotation. Pure function core, no FastAPI imports; the survey
route calls render_cutout via asyncio.to_thread (never on the event loop).

Bit conventions (HiPS 1.0 §4.2), pinned by tests + the dev ground-truth check:
the within-tile nested sub-index de-interleaves into two coordinates; against
real CDS DSS2-color tiles (M31/NGC 205/M32, Step 6) the JPEG raster stores the
cell with its COLUMN taken from the ODD bits and its ROW from the EVEN bits,
with NO additional vertical flip. (The brief's initial guess — x from even bits
+ a vertical flip — mirrored/misplaced the imagery; the ground-truth check
flipped both constants below to their verified values.)
"""
from __future__ import annotations

import io
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

from .survey_pack import read_manifest, tile_path

# Verified against real CDS DSS2-color tiles (Step 6 ground-truth check):
#   _X_FROM_EVEN_BITS = False -> tile COLUMN comes from the ODD sub-index bits
#   _JPG_FLIP_Y      = False -> tile ROW is the EVEN bits with no vertical flip
# (Both were flipped from the brief's best-guess Trues; with the guesses M31's
#  bright core sampled dark sky at the wrong tile corner. See module docstring.)
_X_FROM_EVEN_BITS = False
_JPG_FLIP_Y = False
_ORDER0_TILE_ARCSEC = 58.6324 * 3600.0   # healpix order-0 pixel side, sqrt(pi/3) rad
_JPEG_QUALITY = 85
_TILE_LRU = 48                           # ~36 MB worst case at 512px RGB


class PackUnavailable(RuntimeError):
    """No usable pack manifest — the route falls through (spec §4 step 5)."""


def _order_for(fov_deg: float, width: int, tile_width: int, max_order: int) -> int:
    """Smallest order whose tile scale <= output scale, clamped (spec §3)."""
    s_out = fov_deg * 3600.0 / width
    k = 0
    while k < max_order and (_ORDER0_TILE_ARCSEC / (tile_width * 2 ** k)) > s_out:
        k += 1
    return k


def _deinterleave(sub: np.ndarray, s: int) -> tuple[np.ndarray, np.ndarray]:
    """Split a 2s-bit nested sub-index into (x, y) tile coordinates."""
    x = np.zeros_like(sub)
    y = np.zeros_like(sub)
    for b in range(s):
        x |= ((sub >> (2 * b)) & 1) << b
        y |= ((sub >> (2 * b + 1)) & 1) << b
    return (x, y) if _X_FROM_EVEN_BITS else (y, x)


@lru_cache(maxsize=_TILE_LRU)
def _load_tile(pack_str: str, k: int, npix: int, tile_width: int):
    """Tile file -> RGB ndarray, or None (missing/corrupt -> black fill)."""
    p = tile_path(Path(pack_str), k, npix)
    try:
        arr = np.asarray(Image.open(p).convert("RGB"))
    except Exception:  # noqa: BLE001 — any unreadable tile renders black
        return None
    if arr.shape != (tile_width, tile_width, 3):
        return None
    return arr


def render_cutout(pack: Path, ra_deg: float, dec_deg: float,
                  fov_deg: float, width: int) -> bytes:
    man = read_manifest(pack)
    if man is None:
        raise PackUnavailable(f"no pack manifest under {pack}")
    tile_width = int(man.get("tile_width", 512))
    s = tile_width.bit_length() - 1
    if 2 ** s != tile_width:
        raise PackUnavailable(f"tile_width {tile_width} is not a power of two")
    max_order = int(man["order"])
    k = _order_for(fov_deg, width, tile_width, max_order)

    from astropy.wcs import WCS  # deferred: keeps module import light
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [ra_deg, dec_deg]
    w.wcs.crpix = [(width + 1) / 2.0, (width + 1) / 2.0]
    scale = fov_deg / width
    w.wcs.cdelt = [-scale, scale]        # RA grows LEFT (East-left), dec UP

    cols = np.arange(width)
    rows = np.arange(width)
    px, py = np.meshgrid(cols, (width - 1) - rows)  # row 0 (top) = +dec
    lon, lat = w.all_pix2world(px, py, 0)

    from astropy_healpix import HEALPix
    import astropy.units as u
    hp = HEALPix(nside=2 ** (k + s), order="nested")
    h = hp.lonlat_to_healpix(lon * u.deg, lat * u.deg)
    npix = h >> (2 * s)
    sub = h & ((1 << (2 * s)) - 1)
    tx, ty = _deinterleave(sub, s)
    tile_row = (tile_width - 1 - ty) if _JPG_FLIP_Y else ty

    out = np.zeros((width, width, 3), np.uint8)
    for t in np.unique(npix):
        arr = _load_tile(str(pack), k, int(t), tile_width)
        if arr is None:
            continue                      # black fill (spec §3)
        m = npix == t
        out[m] = arr[tile_row[m], tx[m]]

    buf = io.BytesIO()
    Image.fromarray(out).save(buf, "JPEG", quality=_JPEG_QUALITY)
    return buf.getvalue()
