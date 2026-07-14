"""Tests for the local HiPS TAN renderer (offline-pack spec §3, §7).
Synthetic packs only — no licensed imagery in the repo."""
import io
import json
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import astrodeck.catalog.hips_local as hl
from astrodeck.catalog.survey_pack import tile_path

TILE_W = 64  # power of two; renderer must honor manifest tile_width, not 512


def make_pack(root: Path, order: int, color_for) -> Path:
    """Synthetic pack: every tile a solid RGB from color_for(k, npix)."""
    pack = root / "dss2color"
    for k in range(order + 1):
        for npix in range(12 * 4 ** k):
            p = tile_path(pack, k, npix)
            p.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (TILE_W, TILE_W), color_for(k, npix)).save(
                p, "JPEG", quality=95)
    (pack / "pack.json").write_text(json.dumps(
        {"survey": "CDS/P/DSS2/color", "slug": "dss2color", "order": order,
         "tile_width": TILE_W, "tile_count": sum(12 * 4 ** k for k in range(order + 1)),
         "fetched_at": 0, "bytes": 1}), encoding="utf-8")
    return pack


def _mean_rgb(img: bytes, box: tuple[int, int, int, int]) -> np.ndarray:
    arr = np.asarray(Image.open(io.BytesIO(img)).convert("RGB").crop(box), float)
    return arr.reshape(-1, 3).mean(axis=0)


def _npix_at(order: int, ra_deg: float, dec_deg: float) -> int:
    from astropy_healpix import HEALPix
    import astropy.units as u
    return int(HEALPix(nside=2 ** order, order="nested").lonlat_to_healpix(
        ra_deg * u.deg, dec_deg * u.deg))


def test_order_selection_math():
    # 64px tiles: order-k scale = 58.6324*3600/(64*2^k) arcsec/px
    # fov=30 deg, width=256 -> s_out=421.9"/px; order0 tile=3297"/px -> climbs
    assert hl._order_for(30.0, 256, 64, max_order=5) == 3   # 412"/px <= 421.9
    assert hl._order_for(1.0, 768, 64, max_order=5) == 5    # clamped to pack depth
    assert hl._order_for(250.0, 256, 64, max_order=5) == 0  # s_out 3516 >= 3298 -> base
    assert hl._order_for(30.0, 256, 512, max_order=5) == 0  # 512px tiles: 412"/px at k=0


def test_deinterleave_hand_cases():
    # Ground-truth convention (Step 6, real CDS tiles): tile COLUMN comes from
    # the ODD sub-index bits, ROW from the EVEN bits -> _deinterleave returns
    # (col_from_odd, row_from_even). For 0b1101: even=0b11, odd=0b10.
    col, row = hl._deinterleave(np.array([0b1101]), s=2)
    assert (int(col[0]), int(row[0])) == (0b10, 0b11)  # col<-odd bits, row<-even
    col, row = hl._deinterleave(np.array([0]), s=3)
    assert (int(col[0]), int(row[0])) == (0, 0)
    col, row = hl._deinterleave(np.array([(1 << 6) - 1]), s=3)  # all ones
    assert (int(col[0]), int(row[0])) == (7, 7)


def test_tile_targeting_center_color(tmp_path):
    # Cutout centered inside a known order-1 healpix pixel is dominated by
    # that tile's color (astropy-healpix is the truth for which tile that is).
    target = _npix_at(1, 45.0, 30.0)
    pack = make_pack(tmp_path, 1, lambda k, n: (250, 30, 30) if (k, n) == (1, target)
                     else (10, 10, 10))
    img = hl.render_cutout(pack, 45.0, 30.0, 5.0, 128)
    mid = _mean_rgb(img, (48, 48, 80, 80))
    assert mid[0] > 150 and mid[1] < 80  # strongly red at center


def test_orientation_north_up_east_left(tmp_path):
    # North tile red, south tile blue, east tile green, west tile yellow;
    # centered between them the rendered halves must land accordingly.
    n = _npix_at(1, 45.0, 40.0)
    s = _npix_at(1, 45.0, 10.0)
    e = _npix_at(1, 65.0, 25.0)
    w = _npix_at(1, 25.0, 25.0)
    colors = {n: (250, 20, 20), s: (20, 20, 250), e: (20, 250, 20), w: (250, 250, 20)}
    pack = make_pack(tmp_path, 1, lambda k, np_: colors.get(np_, (0, 0, 0)))
    img = hl.render_cutout(pack, 45.0, 25.0, 40.0, 200)
    top = _mean_rgb(img, (80, 0, 120, 40))
    bottom = _mean_rgb(img, (80, 160, 120, 200))
    left = _mean_rgb(img, (0, 80, 40, 120))
    right = _mean_rgb(img, (160, 80, 200, 120))
    assert top[0] > bottom[0]      # red (north, +dec) at the TOP
    assert bottom[2] > top[2]      # blue (south) at the bottom
    assert left[1] > right[1]      # green (east, +RA) on the LEFT (E-left)
    assert right[0] > left[0] and right[1] > left[1] * 0.5  # yellow (west) right
    # NOTE (implementer): healpix pixels are diamonds — if an assert fails
    # because a sample box straddles a pixel edge, tune the sample points/boxes.
    # The DIRECTION relations (N top / S bottom / E left / W right) are the
    # requirement; do not weaken those to make a box placement pass.


def test_missing_tile_fills_black_no_raise(tmp_path):
    target = _npix_at(0, 45.0, 30.0)
    pack = make_pack(tmp_path, 0, lambda k, n: (200, 200, 200))
    tile_path(pack, 0, target).unlink()
    img = hl.render_cutout(pack, 45.0, 30.0, 5.0, 64)
    assert _mean_rgb(img, (24, 24, 40, 40)).max() < 40  # black fill at center


def test_absent_manifest_raises(tmp_path):
    with pytest.raises(hl.PackUnavailable):
        hl.render_cutout(tmp_path / "nope", 45.0, 30.0, 5.0, 64)


def test_render_perf_768(tmp_path):
    pack = make_pack(tmp_path, 0, lambda k, n: (60, 60, 60))
    hl.render_cutout(pack, 45.0, 30.0, 5.0, 128)  # warm the tile LRU
    t0 = time.perf_counter()
    hl.render_cutout(pack, 44.0, 29.0, 5.0, 768)
    assert time.perf_counter() - t0 < 0.15  # spec §3 budget (generous)
