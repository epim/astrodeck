"""Per-night stacking, the meridian flip, the shared stretch and the ratio
light curve — on synthetic 400x300 skies small enough to run in seconds.

The synthetic sky is deliberately the shape of the real problem: a galaxy, a
field of stars, a transient that brightens over three nights, a dither on every
sub, HALF OF NIGHT TWO ROTATED 180 DEGREES (the meridian flip nothing in the
FITS records), a cross-night centring offset of 60 pixels, and a transparency
change between nights that the ratio photometry has to divide away.
"""
from __future__ import annotations

import math
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pytest

from astrodeck.imaging import nightstack as ns

H, W = 300, 400
GALAXY = (150.0, 200.0)
SN_YX = (118.0, 236.0)
SKY_LEVEL = 900.0
READ_NOISE = 7.0


# --------------------------------------------------------------------------
# a synthetic sky
# --------------------------------------------------------------------------

def _star_field(n: int = 30, seed: int = 11, min_sep: float = 46.0):
    """``n`` stars, none within ``min_sep`` of another or of the transient."""
    rng = np.random.default_rng(seed)
    pts: list[tuple[float, float, float]] = []
    guard = 0
    while len(pts) < n and guard < 20000:
        guard += 1
        y = float(rng.uniform(30, H - 30))
        x = float(rng.uniform(30, W - 30))
        if math.hypot(y - SN_YX[0], x - SN_YX[1]) < 56.0:
            continue
        if any(math.hypot(y - py, x - px) < min_sep for py, px, _ in pts):
            continue
        pts.append((y, x, float(rng.uniform(600, 9000))))
    return pts


STARS = _star_field()
#: the brightest star well clear of the galaxy, used as the "did not vary"
#: control in the photometry test
CONTROL = max((s for s in STARS
               if math.hypot(s[0] - GALAXY[0], s[1] - GALAXY[1]) > 90),
              key=lambda s: s[2])


def render(*, dy: float = 0.0, dx: float = 0.0, flip: bool = False,
           sn: float = 0.0, transparency: float = 1.0, sky: float = SKY_LEVEL,
           noise: float = READ_NOISE, seed: int = 0,
           stars=None, hot: tuple[int, int] | None = None) -> np.ndarray:
    """One synthetic sub. ``dy``/``dx`` move the SKY; ``flip`` then turns the
    whole frame over, which is what a meridian flip does to the sensor."""
    rng = np.random.default_rng(seed)
    yy = np.arange(H, dtype=np.float32)[:, None]
    xx = np.arange(W, dtype=np.float32)[None, :]
    img = np.zeros((H, W), dtype=np.float32)

    def blob(cy, cx, amp, sy, sx=None):
        sx = sy if sx is None else sx
        return amp * np.exp(-(((yy - cy) / sy) ** 2 + ((xx - cx) / sx) ** 2))

    img += blob(GALAXY[0] + dy, GALAXY[1] + dx, 2600.0 * transparency, 40.0, 26.0)
    for sy, sx, amp in (STARS if stars is None else stars):
        img += blob(sy + dy, sx + dx, amp * transparency, 2.0)
    if sn:
        img += blob(SN_YX[0] + dy, SN_YX[1] + dx, sn * transparency, 2.0)
    if flip:
        img = img[::-1, ::-1].copy()
    img = img + sky + rng.normal(0, noise, img.shape).astype(np.float32)
    if hot is not None:
        img[hot] = 62000.0                   # planted AFTER the flip: a hot
        #                                      pixel belongs to the SENSOR, not
        #                                      to the sky, so it does not move
    return img.astype(np.float32)


# --------------------------------------------------------------------------
# nights and names
# --------------------------------------------------------------------------

@pytest.mark.parametrize("stamp,expected", [
    ("2026-09-07 21:30:00", date(2026, 9, 7)),     # evening -> its own date
    ("2026-09-08 01:30:00", date(2026, 9, 7)),     # after midnight -> the night before
    ("2026-09-08 11:59:59", date(2026, 9, 7)),     # a minute before the rollover
    ("2026-09-08 12:00:00", date(2026, 9, 8)),     # noon exactly -> the new night
    ("2026-01-01 03:00:00", date(2025, 12, 31)),   # across the year boundary
])
def test_night_of_rolls_at_local_noon(stamp, expected):
    assert ns.night_of(datetime.fromisoformat(stamp)) == expected


def test_night_of_accepts_a_posix_timestamp():
    when = datetime(2026, 9, 8, 2, 0, 0)
    assert ns.night_of(when.timestamp()) == date(2026, 9, 7)


def test_parse_frame_name_reads_the_capture_template():
    ref = ns.parse_frame_name(Path("Light_NGC 7331_Oiii_2026-09-08_013005_0042.fits"))
    assert ref is not None
    assert ref.frame_type == "Light"
    assert ref.target == "NGC 7331"
    assert ref.filter_name == "Oiii"
    assert ref.when == datetime(2026, 9, 8, 1, 30, 5)
    assert ref.index == 42
    assert ref.night == date(2026, 9, 7)


def test_parse_frame_name_keeps_an_underscore_in_the_target():
    ref = ns.parse_frame_name(Path("Light_Barnard_33_Ha_2026-09-08_213000_0001.fits"))
    assert ref is not None and ref.target == "Barnard_33"
    assert ref.filter_name == "Ha"


@pytest.mark.parametrize("name", [
    "master_bias.fits",                       # not the template
    "Light_NGC 7331_L_2026-13-40_213000_0001.fits",   # impossible date
    "Light_NGC 7331_L_2026-09-08_213000_0001.txt",    # not a FITS
])
def test_parse_frame_name_returns_none_for_anything_else(name):
    assert ns.parse_frame_name(Path(name)) is None


def test_scan_frames_notes_every_file_it_skips(tmp_path):
    (tmp_path / "Light_T_L_2026-09-07_213000_0001.fits").write_bytes(b"")
    (tmp_path / "Dark_T_L_2026-09-07_213000_0002.fits").write_bytes(b"")
    (tmp_path / "notes.fits").write_bytes(b"")
    (tmp_path / "readme.txt").write_text("ignored")
    frames, notes = ns.scan_frames(tmp_path, filter_name="L")
    assert [f.path.name for f in frames] == ["Light_T_L_2026-09-07_213000_0001.fits"]
    assert any("notes.fits" in n for n in notes)
    assert any("Dark" in n for n in notes)
    assert not any("readme.txt" in n for n in notes)   # not a FITS at all


def test_group_by_night_buckets_across_midnight():
    names = ["Light_T_L_2026-09-07_213000_0001.fits",
             "Light_T_L_2026-09-08_003000_0002.fits",
             "Light_T_L_2026-09-08_213000_0003.fits"]
    frames = [ns.parse_frame_name(Path(n)) for n in names]
    grouped = ns.group_by_night(frames)
    assert list(grouped) == [date(2026, 9, 7), date(2026, 9, 8)]
    assert len(grouped[date(2026, 9, 7)]) == 2
    assert len(grouped[date(2026, 9, 8)]) == 1


# --------------------------------------------------------------------------
# registration
# --------------------------------------------------------------------------

REG = dict(bin_factor=2, refine_size=256)


@pytest.mark.parametrize("dy,dx", [(0, 0), (3, -3), (-7, 11), (60, -20), (-25, 60)])
def test_register_translation_recovers_a_known_shift(dy, dx):
    ref = render(seed=1)
    moved = render(dy=dy, dx=dx, seed=2)
    a = ns.register_translation(ref, moved, **REG)
    assert not a.flipped
    assert abs(a.dy + dy) <= 1 and abs(a.dx + dx) <= 1
    assert a.score > ns.MIN_REGISTRATION_SCORE


@pytest.mark.parametrize("dy,dx", [(0, 0), (5, 4), (-25, 33), (60, -20)])
def test_register_translation_detects_the_meridian_flip(dy, dx):
    """Nothing in the FITS says the mount flipped, so the registration has to
    find it. The recovered alignment must also actually undo the flip."""
    ref = render(seed=1)
    flipped = render(dy=dy, dx=dx, flip=True, seed=3)
    a = ns.register_translation(ref, flipped, **REG)
    assert a.flipped is True
    assert a.score > ns.MIN_REGISTRATION_SCORE
    placed = ns.place(flipped, a)
    good = np.isfinite(placed)
    rms = float(np.sqrt(np.mean((placed[good] - ref[good]) ** 2)))
    assert rms < 60.0, f"flip not undone: rms {rms}"


def test_register_translation_scores_an_unrelated_field_below_the_threshold():
    ref = render(seed=1)
    other = render(stars=_star_field(seed=4242), seed=5)
    blank = (np.zeros((H, W), np.float32) + SKY_LEVEL +
             np.random.default_rng(6).normal(0, READ_NOISE, (H, W))).astype(np.float32)
    assert ns.register_translation(ref, other, **REG).score < ns.MIN_REGISTRATION_SCORE
    assert ns.register_translation(ref, blank, **REG).score < ns.MIN_REGISTRATION_SCORE


def test_compose_equals_placing_twice():
    """A frame aligned to its night's reference, and that reference aligned to
    night one, must land in the same place as one composed alignment.

    Compared only where the two-step version still HAS data: going through an
    intermediate grid throws away whatever fell off its edge, which is the
    other reason the CLI composes instead of re-gridding.
    """
    sky = render(seed=7)
    for outer in (ns.Alignment(9, -4, 9.0, False), ns.Alignment(-6, 11, 9.0, True)):
        for inner in (ns.Alignment(2, 3, 9.0, False), ns.Alignment(-5, 7, 9.0, True)):
            twice = ns.place(ns.place(sky, inner), outer)
            once = ns.place(sky, ns.compose(outer, inner))
            kept = np.isfinite(twice)
            assert kept.any()
            assert np.array_equal(twice[kept], once[kept]), f"{outer} o {inner}"
            assert np.isfinite(once).sum() >= kept.sum()


# --------------------------------------------------------------------------
# combining
# --------------------------------------------------------------------------

def test_combine_rejects_a_planted_hot_pixel():
    """The hot pixel sits at the same SENSOR pixel in every sub; the dither
    puts it on a different piece of SKY each time, so after alignment it is a
    lone outlier and the median/MAD clip drops it."""
    hot = (150, 210)
    frames, shifts = [], []
    for i, (dy, dx) in enumerate([(0, 0), (3, -2), (-3, 2), (2, 3), (-2, -3), (1, 1)]):
        frames.append(render(dy=dy, dx=dx, seed=100 + i, hot=hot))
        shifts.append(ns.Alignment(-dy, -dx, 50.0, False))
    stacked = ns.combine(frames, shifts)
    clean = ns.combine([render(dy=dy, dx=dx, seed=100 + i)
                        for i, (dy, dx) in enumerate(
                            [(0, 0), (3, -2), (-3, 2), (2, 3), (-2, -3), (1, 1)])],
                       shifts)
    window = np.s_[hot[0] - 4:hot[0] + 5, hot[1] - 4:hot[1] + 5]
    assert np.nanmax(stacked[window]) < np.nanmax(clean[window]) + 200.0
    assert np.nanmax(stacked[window]) < 20000.0     # 62000 planted


def test_combine_leaves_the_unreached_border_nan():
    frames = [render(dy=4, seed=200), render(dy=8, seed=201)]
    shifts = [ns.Alignment(-4, 0, 50.0, False), ns.Alignment(-8, 0, 50.0, False)]
    out = ns.combine(frames, shifts)
    assert np.isnan(out[H - 1, W // 2]), "no frame reached the last row"
    assert np.isfinite(out[H // 2, W // 2])
    filled = ns.fill_nan(out)
    assert np.isfinite(filled).all()


def test_combine_honours_a_bounds_window():
    frames = [render(seed=210), render(seed=211)]
    shifts = [ns.Alignment(0, 0, 50.0, False)] * 2
    full = ns.combine(frames, shifts)
    win = ns.combine(frames, shifts, bounds=(60, 80, 200, 300))
    assert win.shape == (140, 220)
    assert np.allclose(win, full[60:200, 80:300], equal_nan=True)


def test_combine_rejects_a_mismatched_shift_count():
    with pytest.raises(ValueError):
        ns.combine([render(seed=1)], [])


# --------------------------------------------------------------------------
# stretch
# --------------------------------------------------------------------------

def test_stretch_is_the_same_mapping_on_every_night():
    """The whole point of sharing parameters: identical pixel values must map
    to identical greys on every night, and a night that really is brighter
    must come out brighter."""
    night1 = render(sn=400.0, seed=300)
    night3 = render(sn=3000.0, transparency=1.0, seed=302)
    params = ns.stretch_params(night1)

    a = ns.stretch(night1, params)
    b = ns.stretch(night1.copy(), params)
    assert np.array_equal(a, b)
    assert a.dtype == np.uint8

    probe = np.array([[params.black, (params.black + params.white) / 2,
                       params.white]], dtype=np.float32)
    assert np.array_equal(ns.stretch(probe, params), ns.stretch(probe, params))

    y, x = int(SN_YX[0]), int(SN_YX[1])
    later = ns.stretch(night3, params)
    assert later[y, x] > a[y, x], "a brighter transient must render brighter"


def test_stretch_without_params_derives_its_own_and_therefore_flickers():
    """Documents the trap the shared stretch exists to avoid."""
    night1 = render(sn=400.0, seed=310)
    night3 = render(sn=3000.0, transparency=2.0, seed=311)
    p1 = ns.stretch_params(night1)
    p3 = ns.stretch_params(night3)
    assert p3.white != p1.white


# --------------------------------------------------------------------------
# photometry
# --------------------------------------------------------------------------

def test_relative_flux_rises_for_the_transient_and_holds_for_a_constant_star():
    """Three nights, a transient brightening 400 -> 1100 -> 2600, and the
    transparency deliberately swinging 1.0 -> 0.7 -> 1.3 underneath it. The
    ratio has to see the transient and not the sky."""
    nights = [(400.0, 1.0, 900.0), (1100.0, 0.7, 1500.0), (2600.0, 1.3, 700.0)]
    images = [render(sn=sn, transparency=t, sky=sky, seed=400 + i)
              for i, (sn, t, sky) in enumerate(nights)]

    comps = ns.find_comparison_stars(images[0], 4, aperture_px=6.0,
                                     annulus=(10.0, 18.0), exclude=[SN_YX])
    assert len(comps) >= 3

    sn_ratios, sn_errs = [], []
    for img in images:
        ph = ns.relative_flux(img, SN_YX[0], SN_YX[1], comps,
                              aperture_px=6.0, annulus=(10.0, 18.0))
        assert math.isfinite(ph.ratio) and ph.n_comparison == len(comps)
        sn_ratios.append(ph.ratio)
        sn_errs.append(ph.error)
    assert sn_ratios[0] < sn_ratios[1] < sn_ratios[2]
    # the rise clears the quoted uncertainty, which on a transient sitting on a
    # galaxy is dominated by the galaxy's gradient across the sky annulus and
    # not by the read noise
    assert sn_ratios[1] - sn_ratios[0] > sn_errs[0] + sn_errs[1]
    assert sn_ratios[2] - sn_ratios[1] > sn_errs[1] + sn_errs[2]
    assert sn_ratios[2] > 2.0 * sn_ratios[0]

    # a star that did not vary, measured against the OTHER comparisons
    control = CONTROL[0], CONTROL[1]
    others = [c for c in comps
              if math.hypot(c[0] - control[0], c[1] - control[1]) > 5.0]
    assert len(others) >= 2
    flat = [ns.relative_flux(img, control[0], control[1], others,
                             aperture_px=6.0, annulus=(10.0, 18.0))
            for img in images]
    base = flat[0]
    for ph in flat[1:]:
        tol = 3.0 * (base.error + ph.error)
        assert abs(ph.ratio - base.ratio) <= tol, (
            f"constant star moved {ph.ratio - base.ratio:.5f} > {tol:.5f} "
            f"despite the transparency change")


def test_relative_flux_is_nan_without_comparisons():
    ph = ns.relative_flux(render(seed=500), SN_YX[0], SN_YX[1], [])
    assert math.isnan(ph.ratio) and ph.n_comparison == 0


def test_find_comparison_stars_avoids_the_transient_and_saturation():
    img = render(sn=4000.0, seed=510)
    img[60:64, 60:64] = 65000.0                 # a saturated blob
    comps = ns.find_comparison_stars(img, 6, aperture_px=6.0,
                                     annulus=(10.0, 18.0), exclude=[SN_YX])
    assert comps
    for y, x in comps:
        assert math.hypot(y - SN_YX[0], x - SN_YX[1]) >= 40.0
        assert math.hypot(y - 62, x - 62) > 8.0


def test_aperture_and_annulus_measure_what_they_say():
    flat = np.full((H, W), 100.0, dtype=np.float32)
    total, npix = ns.aperture_sum(flat, 150.0, 200.0, 5.0)
    assert npix > 60 and abs(total - 100.0 * npix) < 1e-3
    med, sd, n = ns.annulus_background(flat, 150.0, 200.0, 10.0, 18.0)
    assert abs(med - 100.0) < 1e-6 and sd == 0.0 and n > 100
    net, err, sky = ns.net_flux(flat, 150.0, 200.0, 5.0, (10.0, 18.0))
    assert abs(net) < 1e-3 and abs(sky - 100.0) < 1e-6 and err == 0.0


# --------------------------------------------------------------------------
# crop, annotate, animation
# --------------------------------------------------------------------------

def test_crop_bounds_slides_instead_of_shrinking():
    """Every night's frame must be the SAME size or the GIF cannot be made."""
    a = ns.crop_bounds((H, W), 5.0, 5.0, 100, 160)         # up against a corner
    b = ns.crop_bounds((H, W), 150.0, 200.0, 100, 160)     # in the middle
    assert (a[2] - a[0], a[3] - a[1]) == (100, 160)
    assert (b[2] - b[0], b[3] - b[1]) == (100, 160)
    assert a[0] == 0 and a[1] == 0
    big = ns.crop_bounds((H, W), 150.0, 200.0, 9999, 9999)
    assert big == (0, 0, H, W)


def test_crop_around_returns_the_window():
    img = render(seed=600)
    out = ns.crop_around(img, 150.0, 200.0, 100, 160)
    assert out.shape == (100, 160)
    assert np.array_equal(out, img[100:200, 120:280])


def test_annotate_burns_the_date_and_a_circle_in():
    grey = np.full((120, 200), 40, dtype=np.uint8)
    out = ns.annotate(grey, "2026-09-07", [ns.Mark(100.0, 60.0, 14.0, "SN")])
    assert out.shape == (120, 200, 3) and out.dtype == np.uint8
    assert out.max() > 200, "nothing was drawn"
    # the circle is an outline, so the marked pixel itself is untouched
    assert tuple(out[60, 100]) == (40, 40, 40)


def test_assemble_gif_writes_one_frame_per_night(tmp_path):
    from PIL import Image
    frames = [ns.annotate(np.full((60, 80), v, np.uint8), f"n{v}")
              for v in (30, 120, 220)]
    path = ns.assemble_gif(frames, tmp_path / "a.gif", 250)
    assert path.exists()
    with Image.open(path) as im:
        assert im.n_frames == 3
        assert im.info["duration"] == 250


def test_plot_lightcurve_writes_a_png(tmp_path):
    from PIL import Image
    p = ns.plot_lightcurve([("2026-09-05", 0.10, 0.01), ("2026-09-06", 0.22, 0.01),
                            ("2026-09-07", 0.51, 0.02)], tmp_path / "lc.png")
    with Image.open(p) as im:
        assert im.size == (900, 520)


def test_plot_lightcurve_survives_having_nothing_to_plot(tmp_path):
    p = ns.plot_lightcurve([("2026-09-05", float("nan"), float("nan"))],
                           tmp_path / "lc.png")
    assert p.exists()


# --------------------------------------------------------------------------
# calibration
# --------------------------------------------------------------------------

def _write_fits(path: Path, data: np.ndarray, **cards) -> Path:
    from astropy.io import fits
    path.parent.mkdir(parents=True, exist_ok=True)
    hdu = fits.PrimaryHDU(np.clip(data, 0, 65535).astype(np.uint16))
    for k, v in cards.items():
        hdu.header[k] = v
    hdu.writeto(path, overwrite=True)
    return path


def test_build_master_and_calibrate(tmp_path):
    rng = np.random.default_rng(9)
    pattern = np.tile(np.linspace(90, 110, W, dtype=np.float32), (H, 1))
    for i in range(4):
        _write_fits(tmp_path / "bias" / f"b{i}.fits",
                    pattern + rng.normal(0, 2, (H, W)))
    vignette = np.outer(np.hanning(H) * 0.4 + 0.6, np.hanning(W) * 0.4 + 0.6)
    for i in range(3):
        _write_fits(tmp_path / "flats" / f"f{i}.fits",
                    pattern + 20000.0 * vignette, FILTER="L")

    notes: list[str] = []
    bias, flats = ns.load_masters(tmp_path / "bias", tmp_path / "flats",
                                  ["L"], notes=notes)
    assert bias is not None and bias.shape == (H, W)
    assert abs(float(np.median(bias)) - 100.0) < 2.0
    assert "L" in flats and abs(float(np.median(flats["L"])) - 1.0) < 1e-3
    assert any("master bias" in n for n in notes)

    # a light whose sky AND stars are both vignetted, which is what a real one is
    light = pattern + 1500.0 * vignette
    corner = np.s_[10:40, 10:40]
    middle = np.s_[140:170, 180:210]

    raw = ns.calibrate(light, bias=bias)
    assert abs(float(np.median(raw[corner])) - float(np.median(raw[middle]))) > 500.0

    out = ns.calibrate(light, bias=bias, flat=flats["L"])
    # the vignette is gone: the corners now sit at the same level as the middle
    assert abs(float(np.median(out[corner])) -
               float(np.median(out[middle]))) < 60.0


def test_load_masters_is_best_effort_when_the_directory_is_missing(tmp_path):
    notes: list[str] = []
    bias, flats = ns.load_masters(tmp_path / "nope", tmp_path / "also-nope",
                                  ["L"], notes=notes)
    assert bias is None and flats == {}
    assert len(notes) == 2


def test_calibrate_ignores_a_master_of_the_wrong_shape():
    light = render(seed=700)
    out = ns.calibrate(light, bias=np.zeros((10, 10), np.float32))
    assert out.shape == light.shape
    assert abs(float(np.median(out))) < 1.0        # pedestal still applied


def test_fits_frames_loads_lazily(tmp_path):
    paths = [_write_fits(tmp_path / f"s{i}.fits", render(seed=800 + i))
             for i in range(3)]
    seq = ns.FitsFrames(paths)
    assert len(seq) == 3
    first = seq[0]
    assert first.shape == (H, W) and first.dtype == np.float32
    assert abs(float(np.median(first))) < 1.0      # pedestal applied on read


# --------------------------------------------------------------------------
# the CLI, end to end
# --------------------------------------------------------------------------

NIGHTS = [
    # (calendar date of the evening, transient amplitude, sky shift, flipped subs)
    ("2026-09-05", 400.0, (0, 0), 0),
    ("2026-09-06", 1100.0, (60, -20), 2),
    ("2026-09-07", 2600.0, (-25, 60), 0),
]


def _build_captures(root: Path) -> Path:
    """Three nights of four subs each, in the real filename template. The last
    sub of every night is stamped after midnight, so the noon boundary is
    exercised by the CLI and not only by the unit test."""
    captures = root / "NGC 7331"
    seed = 0
    for evening, sn, (ndy, ndx), flips in NIGHTS:
        y, m, d = (int(v) for v in evening.split("-"))
        for k, (stamp_date, hhmmss) in enumerate([
                (evening, "210000"), (evening, "220000"), (evening, "230000"),
                (f"{y:04d}-{m:02d}-{d + 1:02d}", "003000")]):
            seed += 1
            dither = ((k % 3) - 1) * 3, ((k // 2) - 1) * 3
            _write_fits(
                captures / f"Light_NGC 7331_L_{stamp_date}_{hhmmss}_{seed:04d}.fits",
                render(dy=ndy + dither[0], dx=ndx + dither[1],
                       flip=k >= 4 - flips, sn=sn, seed=seed),
                FILTER="L", OBJECT="NGC 7331", EXPTIME=60.0)
    # something the scanner must skip rather than choke on
    _write_fits(captures / "master_L.fits", np.zeros((H, W), np.float32))
    return captures


def _fake_wcs(sn_ra: float, sn_dec: float, y: float, x: float):
    from astropy.wcs import WCS
    w = WCS(naxis=2)
    w.wcs.crpix = [x + 1.0, y + 1.0]         # FITS pixels are 1-based
    w.wcs.crval = [sn_ra, sn_dec]
    w.wcs.cdelt = [-0.968 / 3600.0, 0.968 / 3600.0]
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    return w


def test_cli_end_to_end(tmp_path):
    from PIL import Image
    from tools import sn_animation

    captures = _build_captures(tmp_path)
    out = tmp_path / "out"
    sn_ra, sn_dec = 339.267, 34.416
    calls: list[Path] = []

    def fake_solve(path, *, ra_hint, dec_hint, fov_deg, log):
        calls.append(Path(path))
        return _fake_wcs(sn_ra, sn_dec, SN_YX[0], SN_YX[1])

    lines: list[str] = []
    rc = sn_animation.main(
        ["--captures", str(captures), "--out", str(out), "--filter", "L",
         "--sn-ra", str(sn_ra), "--sn-dec", str(sn_dec),
         "--crop", "240x160", "--comparison", "4", "--aperture", "6",
         "--annulus", "10", "18", "--frame-ms", "400", "--bin", "2"],
        solve_fn=fake_solve, log=lines.append)
    report = "\n".join(lines)
    assert rc == 0, report

    pngs = sorted(p.name for p in out.glob("2026-*.png"))
    assert pngs == ["2026-09-05.png", "2026-09-06.png", "2026-09-07.png"], report
    assert calls and calls[0].name == "reference.fits"

    for name in pngs:
        with Image.open(out / name) as im:
            assert im.size == (240, 160)

    with Image.open(out / "animation.gif") as im:
        assert im.n_frames == 3
        assert im.info["duration"] == 400

    csv_path = out / "lightcurve.csv"
    assert csv_path.exists(), report
    rows = [r.strip().split(",") for r in
            csv_path.read_text(encoding="utf-8").strip().splitlines()]
    assert rows[0] == ["night", "n_frames", "ratio", "err"]
    assert [r[0] for r in rows[1:]] == ["2026-09-05", "2026-09-06", "2026-09-07"]
    assert all(int(r[1]) == 4 for r in rows[1:]), report
    ratios = [float(r[2]) for r in rows[1:]]
    assert ratios[0] < ratios[1] < ratios[2], f"{ratios}\n{report}"
    assert (out / "lightcurve.png").exists()

    # the flipped subs of night two were found and used, not dropped
    assert "2 flipped" in report, report
    # the non-template file was skipped with a note, not silently
    assert "master_L.fits" in report, report


def test_cli_without_a_transient_still_animates(tmp_path):
    from tools import sn_animation

    captures = _build_captures(tmp_path)
    out = tmp_path / "out"
    lines: list[str] = []
    rc = sn_animation.main(
        ["--captures", str(captures), "--out", str(out), "--crop", "240x160",
         "--bin", "2"],
        solve_fn=lambda *a, **k: pytest.fail("must not solve without --sn-ra"),
        log=lines.append)
    assert rc == 0
    assert len(list(out.glob("2026-*.png"))) == 3
    assert (out / "animation.gif").exists()
    assert not (out / "lightcurve.csv").exists()
    assert not (out / "reference.fits").exists()
    assert any("no transient position" in ln for ln in lines)


def test_cli_reports_an_empty_directory(tmp_path):
    from tools import sn_animation
    lines: list[str] = []
    rc = sn_animation.main(["--captures", str(tmp_path)], log=lines.append)
    assert rc == 2
    assert any("no L light frames" in ln for ln in lines)


def test_parse_crop_rejects_nonsense():
    import argparse
    from tools import sn_animation
    assert sn_animation.parse_crop("1600x1100") == (1100, 1600)
    with pytest.raises(argparse.ArgumentTypeError):
        sn_animation.parse_crop("wide")
    with pytest.raises(argparse.ArgumentTypeError):
        sn_animation.parse_crop("4x4")
