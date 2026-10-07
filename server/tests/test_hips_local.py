# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Tests for the local HiPS TAN renderer (offline-pack spec §3, §7).
Synthetic packs only — no licensed imagery in the repo."""
import io
import json
import math
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
    # Budget exists to catch algorithmic regressions (a per-pixel Python loop
    # costs seconds for 768^2 = 590k pixels); 1.0s absorbs slow shared CI
    # runners while still failing an order-of-magnitude regression.
    assert time.perf_counter() - t0 < 1.0


# ----------------------------------------------- the TAN grid, without WCSLIB
#
# #638 part A. ``render_cutout`` used to build an ``astropy.wcs.WCS`` for one
# job: turn a pixel grid into sky coordinates. astropy's ``_wcs`` extension
# statically links WCSLIB (LGPL-3.0-or-later), and this was the ONE production
# use of it (every other astropy use is time/units/coordinates/io.fits). The
# grid is now a private vectorised inverse gnomonic in ``hips_local``.
#
# The old path survives HERE, as the oracle: ``astropy.wcs`` is a test-only
# import now, and each test below pins the new grid to what the old code
# produced, so the swap cannot move a single pixel unnoticed.

#: (ra0, dec0, fov, width). Chosen for the corners of the formula, not for
#: typical use: both poles at the 0.1 degree offset the brief names, an RA
#: wrap in both directions, and odd AND even widths (an odd width has a pixel
#: ON the tangent point, so the rho -> 0 branch runs; an even width has none).
_GRID_CASES = [
    pytest.param(45.0, 30.0, 5.0, 128, id="mid-latitude-even"),
    pytest.param(45.0, 30.0, 5.0, 127, id="mid-latitude-odd"),
    pytest.param(359.9, 10.0, 6.0, 64, id="ra-wrap-below-360-even"),
    pytest.param(0.05, -12.0, 6.0, 65, id="ra-wrap-above-0-odd"),
    pytest.param(180.0, 0.0, 40.0, 100, id="equator-wide-even"),
    pytest.param(10.0, 89.9, 4.0, 64, id="north-pole-even"),
    pytest.param(250.0, 89.9, 30.0, 63, id="north-pole-wide-odd"),
    pytest.param(123.0, -89.9, 4.0, 65, id="south-pole-odd"),
    pytest.param(359.95, -89.9, 20.0, 64, id="south-pole-wrap-even"),
]


def _wcs_grid(ra0, dec0, fov, width):
    """The grid the OLD ``render_cutout`` built, kept verbatim as the oracle:
    the same WCS setup and the same pixel grid, including the row flip."""
    from astropy.wcs import WCS  # test-only: production no longer imports it
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [ra0, dec0]
    w.wcs.crpix = [(width + 1) / 2.0, (width + 1) / 2.0]
    scale = fov / width
    w.wcs.cdelt = [-scale, scale]
    cols = np.arange(width)
    rows = np.arange(width)
    px, py = np.meshgrid(cols, (width - 1) - rows)
    return w.all_pix2world(px, py, 0)


def _sep_deg(lon1, lat1, lon2, lat2):
    """Great-circle separation in degrees, elementwise. Haversine, because the
    comparison is of numbers 1e-9 degrees apart, where a cosine-based form has
    no digits left, and a plain lon/lat difference would call 359.99999 and
    0.00001 a whole turn apart."""
    l1, b1, l2, b2 = (np.radians(np.asarray(a, float))
                      for a in (lon1, lat1, lon2, lat2))
    h = (np.sin((b2 - b1) / 2) ** 2
         + np.cos(b1) * np.cos(b2) * np.sin((l2 - l1) / 2) ** 2)
    return np.degrees(2 * np.arcsin(np.sqrt(np.clip(h, 0.0, 1.0))))


@pytest.mark.parametrize("ra0,dec0,fov,width", _GRID_CASES)
def test_tan_grid_matches_astropy_wcs(ra0, dec0, fov, width):
    """The new grid is the old WCS grid, to 1e-9 degrees (3.6 micro-arcsec).

    MUTANT tan_grid_xi_sign: ``xi = np.radians(-scale * (px - half))`` with the
    minus dropped (RA growing RIGHT) fails every case, the first with
    ``AssertionError: max separation 4.958e+00 deg from the WCS grid``.
    MUTANT tan_grid_no_ra_wrap: ``np.mod(ra, 360.0)`` -> ``ra`` in the
    ``lon = np.where(at_centre, ...)`` line fails the three cases whose
    field reaches NEGATIVE RA (ra-wrap-above-0-odd and the two pole cases
    south-pole-odd and north-pole-even) with ``AssertionError: RA must
    land in [0, 360)``. The cases that overshoot 360 instead are rescued by
    the fold line below it, which the half-open-range test pins.
    MUTANT tan_grid_row_flip: ``(width - 1) - rows`` -> ``rows`` (the picture
    upside down) fails every case, the first with
    ``AssertionError: max separation 4.958e+00 deg from the WCS grid``."""
    lon, lat = hl._tan_grid(ra0, dec0, fov, width)
    ref_lon, ref_lat = _wcs_grid(ra0, dec0, fov, width)
    assert lon.shape == lat.shape == (width, width)
    assert np.isfinite(lon).all() and np.isfinite(lat).all()
    assert ((lon >= 0.0) & (lon < 360.0)).all(), "RA must land in [0, 360)"
    assert ((ref_lon >= 0.0) & (ref_lon < 360.0)).all()
    worst = float(_sep_deg(lon, lat, ref_lon, ref_lat).max())
    assert worst < 1e-9, f"max separation {worst:.3e} deg from the WCS grid"


@pytest.mark.parametrize("ra0,dec0,fov,width", _GRID_CASES)
def test_tan_grid_matches_framing_deproject(ra0, dec0, fov, width):
    """The same grid, derived from the contract instead of from astropy: pixel
    (row, col) sits at standard coordinates xi = -scale*(col - half) (RA grows
    LEFT) and eta = scale*((width-1-row) - half) (row 0 is NORTH), and
    ``catalog.framing.deproject`` -- the scalar inverse gnomonic pinned
    byte-for-byte to ``ui/src/lib/framing.ts`` -- says where that is.

    Two independent oracles (a C library and a second implementation of the
    same formula) agreeing with one grid is the evidence; either alone could
    share a convention error with the code under test."""
    from astrodeck.catalog import framing

    lon, lat = hl._tan_grid(ra0, dec0, fov, width)
    scale = fov / width
    half = (width - 1) / 2.0
    # Every pixel of a small grid; the corners, the centre cross and a seeded
    # scatter of a large one (the scalar function is the slow side here).
    if width <= 20:
        picks = [(r, c) for r in range(width) for c in range(width)]
    else:
        rng = np.random.default_rng(638)
        mid = width // 2
        picks = [(0, 0), (0, width - 1), (width - 1, 0), (width - 1, width - 1),
                 (mid, mid), (mid, 0), (0, mid), (width - 1, mid), (mid, width - 1)]
        picks += [(int(r), int(c)) for r, c in rng.integers(0, width, (300, 2))]
    for row, col in picks:
        xi = -scale * (col - half)
        eta = scale * ((width - 1 - row) - half)
        ra_h, dec = framing.deproject(xi, eta, ra0 / 15.0, dec0)
        sep = float(_sep_deg(lon[row, col], lat[row, col], ra_h * 15.0, dec))
        assert sep < 1e-9, (
            f"pixel (row {row}, col {col}) is {sep:.3e} deg from "
            f"framing.deproject({xi:.6f}, {eta:.6f}, ...)")


def test_tan_grid_ra_range_is_half_open_at_the_rounding_edge():
    """A negative RA smaller than a float's spacing near 360 comes back from
    ``np.mod`` (and from ``%``) AS 360.0, which is not in ``[0, 360)``. The
    centre column of an odd width keeps RA exactly at ``ra0`` and the centre
    pixel is substituted from ``ra0`` directly, so a field centred on such an
    RA exercises both routes. The fold must cover both, which means it runs
    AFTER the centre substitution. ``survey`` only ever passes RA already in
    ``[0, 360)``, so this is a guard on the function's own contract.

    MUTANT tan_grid_fold_off: ``lon = np.where(lon >= 360.0, lon - 360.0,
    lon)`` -> ``lon = lon`` fails with ``AssertionError: 33 pixel(s) at RA
    360.0`` (the centre column); MUTANT tan_grid_fold_before_centre (the
    fold moved above the centre substitution) fails with ``AssertionError:
    1 pixel(s) at RA 360.0`` (the centre pixel alone)."""
    lon, _ = hl._tan_grid(-1e-15, 20.0, 5.0, 33)
    assert ((lon >= 0.0) & (lon < 360.0)).all(), (
        f"{int((lon >= 360.0).sum())} pixel(s) at RA 360.0")


@pytest.mark.parametrize("dec0", [90.0, -90.0])
@pytest.mark.parametrize("width", [32, 33])
def test_tan_grid_at_the_exact_pole_is_continuous_and_framings(dec0, width):
    """``survey._snap_geometry`` clamps dec to +-90, so a cutout CAN be centred
    on a pole exactly (a 10 degree field on the pole snaps to dec 90.0). There
    the old WCS path and this one part ways, on purpose, at +90 only: WCSLIB
    flips its default LONPOLE at dec0 = +90, which turned the whole image 180
    degrees against the same field at dec0 = 89.999999 (measured 2.74 degrees
    of separation on a 2 degree field) and left -90 alone. The grid here follows
    ``framing.deproject`` -- the convention the client projects with -- so it is
    continuous through the pole and agrees with that function at the pole
    itself. ``test_tan_grid_matches_astropy_wcs`` holds the new grid to WCS at
    +-89.9, where WCSLIB is continuous, and that is as close as the two can be
    compared.

    MUTANT wcs_grid_restored (``_tan_grid`` returning the old astropy WCS grid
    again): fails at +90 with ``pole turned 2.74e+00 deg against the field a
    millionth of a degree off it``; at -90 the old grid is continuous and the
    framing comparison is what the case rests on."""
    from astrodeck.catalog import framing

    ra0, fov = 100.0, 2.0
    at_pole = hl._tan_grid(ra0, dec0, fov, width)
    inward = -math.copysign(1e-6, dec0)       # one millionth of a degree off
    near = hl._tan_grid(ra0, dec0 + inward, fov, width)
    turned = float(_sep_deg(*at_pole, *near).max())
    assert turned < 1e-5, (
        f"pole turned {turned:.2e} deg against the field a millionth of a "
        f"degree off it")

    scale = fov / width
    half = (width - 1) / 2.0
    for row in range(0, width, 5):
        for col in range(0, width, 5):
            xi = -scale * (col - half)
            eta = scale * ((width - 1 - row) - half)
            ra_h, dec = framing.deproject(xi, eta, ra0 / 15.0, dec0)
            sep = float(_sep_deg(at_pole[0][row, col], at_pole[1][row, col],
                                 ra_h * 15.0, dec))
            assert sep < 1e-9, (
                f"pixel (row {row}, col {col}) is {sep:.3e} deg from "
                f"framing.deproject at the pole")


def test_tan_grid_rho_guard_is_framings():
    """``framing`` guards rho -> 0 with 1e-12 and returns the tangent point
    verbatim; the private copy must use the same threshold, or an odd-width
    cutout's centre pixel could be a NaN in one projection and the tangent
    point in the other. (``hips_local`` cannot import ``framing`` for it: that
    module pulls FastAPI in, and this one is a pure function core.)

    MUTANT rho_eps_drift: ``_RHO_EPS = 1e-12`` -> ``1e-9`` fails with
    ``assert 1e-09 == 1e-12``."""
    from astrodeck.catalog import framing
    assert hl._RHO_EPS == framing.RHO_EPS


@pytest.mark.parametrize("dec0", [89.9, -89.9, 30.0])
def test_tan_grid_odd_width_centre_is_the_tangent_point(dec0):
    """An odd width puts a pixel exactly ON the tangent point, where the
    formula divides 0 by 0. It must come back as (ra0, dec0), not a NaN.

    MUTANT tan_grid_centre_guard_off: ``at_centre = rho < _RHO_EPS`` ->
    ``rho < 0.0`` fails with ``assert np.float64(nan) == 89.9 +- 1.0e-12``."""
    lon, lat = hl._tan_grid(123.456, dec0, 3.0, 33)
    assert lon[16, 16] == pytest.approx(123.456, abs=1e-12)
    assert lat[16, 16] == pytest.approx(dec0, abs=1e-12)
    assert np.isfinite(lon).all() and np.isfinite(lat).all()


def test_tan_grid_even_width_straddles_the_tangent_point():
    """An even width has no centre pixel: the four middle ones sit a half pixel
    either side, so they are equidistant from (ra0, dec0) and the mean of their
    dec is dec0. That is the (width - 1) / 2 centre, not width / 2."""
    ra0, dec0, fov, width = 100.0, 20.0, 6.0, 8
    lon, lat = hl._tan_grid(ra0, dec0, fov, width)
    mid = [(3, 3), (3, 4), (4, 3), (4, 4)]
    seps = [float(_sep_deg(lon[r, c], lat[r, c], ra0, dec0)) for r, c in mid]
    half_diag = (fov / width) * (2 ** 0.5) / 2.0
    assert seps == pytest.approx([half_diag] * 4, rel=1e-3)
    assert float(np.mean([lat[r, c] for r, c in mid])) == pytest.approx(dec0, abs=1e-3)


def test_tan_grid_is_north_up_east_left():
    """Direct statement of the output contract, independent of both oracles:
    dec falls going down the image; RA falls going right (East is LEFT)."""
    lon, lat = hl._tan_grid(45.0, 25.0, 10.0, 51)
    mid = 25
    assert lat[0, mid] > lat[mid, mid] > lat[-1, mid]
    assert lon[mid, 0] > lon[mid, mid] > lon[mid, -1]


@pytest.mark.parametrize("dec0,width", [(87.5, 9), (82.0, 7), (45.0, 7)])
def test_tan_grid_pixel_on_the_pole_is_finite(dec0, width):
    """Rounding can push arcsin's argument a hair past 1 for a pixel that lands
    exactly on the celestial pole, and arcsin of that is NaN, which would go
    into HEALPix and poison the whole cutout. The field is built so that the
    top-centre pixel IS the pole: its standard coordinate eta is the tangent of
    the pole's angular distance from the tangent point.

    These (dec0, width) pairs were found by scanning for the ones where the
    unclipped argument really does come out above 1; a pole that happened to
    round to 0.9999999999999999 would test nothing.

    MUTANT tan_grid_no_arcsin_clip: removing ``np.clip(..., -1.0, 1.0)`` from
    ``sin_dec`` fails all three cases with ``AssertionError: a pixel on the
    pole came back as NaN``."""
    k = (width - 1) / 2.0
    eta_pole = math.degrees(math.tan(math.radians(90.0 - dec0)))
    fov = eta_pole / k * width
    lon, lat = hl._tan_grid(10.0, dec0, fov, width)
    assert np.isfinite(lat).all() and np.isfinite(lon).all(), (
        "a pixel on the pole came back as NaN")
    assert lat[0, width // 2] == pytest.approx(90.0, abs=1e-5)
    assert lat.max() <= 90.0 and lat.min() >= -90.0


def test_render_cutout_does_not_need_astropy_wcs(tmp_path, monkeypatch):
    """The frozen binaries carry WCSLIB (LGPL-3.0) only through astropy.wcs. A
    cutout must render with that import REFUSED, which is the property the
    release needs if the owner decides to exclude it (#638).

    MUTANT wcs_import_restored: putting ``from astropy.wcs import WCS`` back in
    ``render_cutout`` fails with ``ModuleNotFoundError: import of astropy.wcs
    halted; None in sys.modules``."""
    import sys
    # astropy_healpix is imported FIRST so the block below can only catch a use
    # of astropy.wcs by the code under test, not by a dependency loading.
    import astropy_healpix  # noqa: F401
    pack = make_pack(tmp_path, 0, lambda k, n: (90, 120, 150))
    monkeypatch.setitem(sys.modules, "astropy.wcs", None)
    img = hl.render_cutout(pack, 45.0, 30.0, 5.0, 64)
    assert Image.open(io.BytesIO(img)).size == (64, 64)


def test_hips_local_has_no_astropy_wcs_import():
    """Structural twin of the test above: no import statement anywhere in the
    module, deferred or not, names astropy.wcs.

    MUTANT wcs_import_restored fails this one with ``AssertionError: line 152
    imports astropy.wcs``."""
    import ast
    tree = ast.parse(Path(hl.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names = [node.module or ""] + [
                f"{node.module}.{a.name}" for a in node.names]
        elif isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        else:
            continue
        assert not any(n == "astropy.wcs" or n.startswith("astropy.wcs.")
                       for n in names), (
            f"line {node.lineno} imports astropy.wcs")


def _make_gradient_pack(root: Path, order: int) -> Path:
    """Every tile is a distinct 2-D gradient, so a pixel's colour names the
    tile AND the position within it. Solid tiles (``make_pack``) cannot see a
    flipped row axis or a mirrored column inside a tile; this can."""
    pack = root / "dss2color"
    ramp = np.linspace(0, 255, TILE_W).astype(np.uint8)
    for k in range(order + 1):
        for npix in range(12 * 4 ** k):
            p = tile_path(pack, k, npix)
            p.parent.mkdir(parents=True, exist_ok=True)
            arr = np.zeros((TILE_W, TILE_W, 3), np.uint8)
            arr[:, :, 0] = ramp[None, :]                 # varies with column
            arr[:, :, 1] = ramp[:, None]                 # varies with row
            arr[:, :, 2] = (npix * 37 + k * 11) % 256    # names the tile
            Image.fromarray(arr).save(p, "JPEG", quality=95)
    (pack / "pack.json").write_text(json.dumps(
        {"survey": "CDS/P/DSS2/color", "slug": "dss2color", "order": order,
         "tile_width": TILE_W,
         "tile_count": sum(12 * 4 ** k for k in range(order + 1)),
         "fetched_at": 0, "bytes": 1}), encoding="utf-8")
    return pack


@pytest.mark.parametrize("ra0,dec0,fov,width", [
    pytest.param(45.0, 30.0, 40.0, 128, id="mid-latitude"),
    pytest.param(359.9, 10.0, 20.0, 65, id="ra-wrap-odd"),
    pytest.param(10.0, 89.9, 8.0, 64, id="north-pole"),
    pytest.param(200.0, -89.9, 8.0, 63, id="south-pole-odd"),
])
def test_render_cutout_matches_the_old_bytes(tmp_path, monkeypatch,
                                             ra0, dec0, fov, width):
    """Render the same pack through the new grid and through the OLD astropy
    one (swapped in as ``_tan_grid``), and the JPEG bytes are identical.

    MUTANT render_row_flip_swapped: ``(width - 1) - rows`` -> ``rows`` in
    ``_tan_grid`` renders the cutout upside down; all four configs fail with
    ``AssertionError: the cutout is not the one the WCS path produced``.
    MUTANT render_xi_sign: dropping the minus on ``xi`` mirrors it; all four
    configs fail with the same message."""
    pack = _make_gradient_pack(tmp_path, 2)
    new = hl.render_cutout(pack, ra0, dec0, fov, width)
    monkeypatch.setattr(hl, "_tan_grid", _wcs_grid)
    old = hl.render_cutout(pack, ra0, dec0, fov, width)
    assert new == old, "the cutout is not the one the WCS path produced"
    # And the gradient pack really is sensitive to the grid: a vertically
    # flipped grid renders something else, so equality above means something.
    monkeypatch.setattr(
        hl, "_tan_grid",
        lambda *a: tuple(np.flipud(g) for g in _wcs_grid(*a)))
    assert hl.render_cutout(pack, ra0, dec0, fov, width) != old
