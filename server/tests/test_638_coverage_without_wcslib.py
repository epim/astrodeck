# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#638: the coverage footprint no longer uses astropy.wcs (WCSLIB, LGPL).

astropy.wcs is allowed HERE, as the oracle the first-party corners are held to.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from astropy.io import fits
from astropy.wcs import WCS

from astrodeck.sequence import coverage

SERVER = Path(__file__).resolve().parents[1]
ROOT = SERVER.parent


def _header(nx=1000, ny=800, **cards):
    h = fits.Header()
    h["NAXIS"] = 2
    h["NAXIS1"] = nx
    h["NAXIS2"] = ny
    h["CTYPE1"] = "RA---TAN"
    h["CTYPE2"] = "DEC--TAN"
    h["CRVAL1"] = 150.0
    h["CRVAL2"] = 30.0
    h["CRPIX1"] = nx / 2 + 0.5
    h["CRPIX2"] = ny / 2 + 0.5
    for k, v in cards.items():
        h[k] = v
    return h


def _oracle(header):
    nx, ny = header["NAXIS1"], header["NAXIS2"]
    w = WCS(header)
    rows = w.calc_footprint(axes=(nx, ny), center=False)
    return [(float(r[0]) / 15.0, float(r[1])) for r in rows]


def _assert_same(header):
    got = coverage._corner_footprint(header, header["NAXIS1"], header["NAXIS2"])
    want = _oracle(header)
    assert got is not None and len(got) == 4
    for (ga, gd), (wa, wd) in zip(got, want):
        da = abs((ga - wa) * 15.0)
        da = min(da, 360.0 - da)
        # RA converges at the pole; compare the arc, not the raw RA.
        import math
        da *= max(math.cos(math.radians(wd)), 1e-3)
        assert da < 1e-6, (got, want)
        assert abs(gd - wd) < 1e-6, (got, want)


S = 1.5 / 3600.0  # 1.5 arcsec per pixel, in degrees

CASES = {
    "cd_plain": dict(CD1_1=-S, CD1_2=0.0, CD2_1=0.0, CD2_2=S),
    "cd_rotated": dict(CD1_1=-S * 0.8, CD1_2=S * 0.6, CD2_1=S * 0.6, CD2_2=S * 0.8),
    "cd_asymmetric": dict(CD1_1=-S * 0.8, CD1_2=S * 0.5, CD2_1=S * 0.7, CD2_2=S * 0.9),
    "cdelt_pc": dict(CDELT1=-S, CDELT2=S, PC1_1=0.9, PC1_2=-0.1, PC2_1=0.1, PC2_2=0.9),
    "cdelt_pc_identity": dict(CDELT1=-S, CDELT2=S),
    "cdelt_crota2": dict(CDELT1=-S, CDELT2=S, CROTA2=37.0),
    "cdelt_crota2_aniso": dict(CDELT1=-S, CDELT2=1.2 * S, CROTA2=-111.0),
    "near_pole": dict(CRVAL2=89.5, CD1_1=-S, CD1_2=0.0, CD2_1=0.0, CD2_2=S),
    "at_pole": dict(CRVAL2=90.0, CD1_1=-S, CD1_2=0.0, CD2_1=0.0, CD2_2=S),
    "south": dict(CRVAL2=-62.0, CD1_1=-S, CD1_2=0.0, CD2_1=0.0, CD2_2=S),
    "ra_zero": dict(CRVAL1=0.01, CD1_1=-S, CD1_2=0.0, CD2_1=0.0, CD2_2=S),
    "ra_zero_rot": dict(CRVAL1=359.99, CD1_1=-S * 0.5, CD1_2=S * 0.866,
                        CD2_1=S * 0.866, CD2_2=S * 0.5),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_corners_match_astropy(name):
    _assert_same(_header(**CASES[name]))


def _sip(order):
    cards = dict(CD1_1=-S, CD1_2=2e-7, CD2_1=1.5e-7, CD2_2=S,
                 CTYPE1="RA---TAN-SIP", CTYPE2="DEC--TAN-SIP",
                 A_ORDER=order, B_ORDER=order)
    # Real-looking, small: a few pixels at the frame edge.
    a = {(2, 0): 3.1e-7, (1, 1): -2.2e-7, (0, 2): 1.4e-7,
         (3, 0): -4e-11, (2, 1): 2e-11, (1, 2): -1e-11, (0, 3): 3e-11}
    b = {(2, 0): -1.7e-7, (1, 1): 2.6e-7, (0, 2): -3.3e-7,
         (3, 0): 1e-11, (2, 1): -3e-11, (1, 2): 2e-11, (0, 3): -2e-11}
    for (p, q), c in a.items():
        if p + q <= order:
            cards["A_%d_%d" % (p, q)] = c
    for (p, q), c in b.items():
        if p + q <= order:
            cards["B_%d_%d" % (p, q)] = c
    return cards


@pytest.mark.parametrize("order", [2, 3])
def test_sip_corners_match_astropy(order):
    h = _header(**_sip(order))
    _assert_same(h)
    # The distortion is real here: it moves a corner by more than the bound.
    plain = _header(**{k: v for k, v in _sip(order).items()
                       if not k.startswith(("A_", "B_"))
                       and k not in ("CTYPE1", "CTYPE2")})
    a = coverage._corner_footprint(h, 1000, 800)
    b = coverage._corner_footprint(plain, 1000, 800)
    moved = max(abs(x[1] - y[1]) for x, y in zip(a, b))
    assert moved > 1e-6


def test_non_tan_is_none():
    h = _header(CD1_1=-S, CD1_2=0.0, CD2_1=0.0, CD2_2=S)
    h["CTYPE1"] = "RA---SIN"
    h["CTYPE2"] = "DEC--SIN"
    assert coverage._corner_footprint(h, 1000, 800) is None
    h["CTYPE1"] = "RA---ARC"
    h["CTYPE2"] = "DEC--ARC"
    assert coverage._corner_footprint(h, 1000, 800) is None


def test_missing_cards_and_singular_cd_are_none(tmp_path):
    h = _header(CD1_1=1e-4, CD1_2=2e-4, CD2_1=2e-4, CD2_2=4e-4)  # det 0
    assert coverage._corner_footprint(h, 1000, 800) is None
    h = _header()  # no scale at all
    assert coverage._corner_footprint(h, 1000, 800) is None
    h = _header(CD1_1=-S, CD2_2=S)
    del h["CRVAL1"]
    with pytest.raises(KeyError):
        coverage._corner_footprint(h, 1000, 800)


def _write(tmp_path, header, name="f.fits"):
    import numpy as np
    path = tmp_path / name
    hdu = fits.PrimaryHDU(np.zeros((header["NAXIS2"], header["NAXIS1"]),
                                   dtype="uint8"))
    for k in header:
        if k not in ("NAXIS", "NAXIS1", "NAXIS2", "SIMPLE", "BITPIX", "EXTEND"):
            hdu.header[k] = header[k]
    hdu.writeto(path)
    return str(path)


def test_read_footprint_end_to_end(tmp_path):
    coverage.clear_memo()
    h = _header(**CASES["cd_rotated"])
    fp = coverage._read_footprint(_write(tmp_path, h))
    assert fp is not None
    want = _oracle(h)
    for (a, d), (wa, wd) in zip(fp.corners, want):
        assert abs(a - wa) * 15 < 1e-6 and abs(d - wd) < 1e-6
    sing = _header(CD1_1=1e-4, CD1_2=2e-4, CD2_1=2e-4, CD2_2=4e-4)
    assert coverage._read_footprint(_write(tmp_path, sing, "s.fits")) is None
    bad = _header(**CASES["cd_plain"])
    bad["CTYPE1"] = "GLON-TAN"
    bad["CTYPE2"] = "GLAT-TAN"
    assert coverage._read_footprint(_write(tmp_path, bad, "b.fits")) is None


@pytest.mark.parametrize("module", ["astrodeck.api.app", "astrodeck.__main__",
                                    "astrodeck.sequence.coverage", "astrodeck.persist"])
def test_astropy_wcs_is_not_imported(module):
    # -I drops PYTHONPATH and the cwd, so the checkout under test is put on
    # sys.path by hand and asserted, else an installed copy would be probed.
    code = ("import sys, importlib\n"
            "sys.path.insert(0, %r)\n"
            "importlib.import_module(%r)\n"
            "import astrodeck\n"
            "assert astrodeck.__file__.startswith(%r), astrodeck.__file__\n"
            "bad = sorted(m for m in sys.modules if m == 'astropy.wcs' "
            "or m.startswith('astropy.wcs.'))\n"
            "print('LOADED', bad)\n"
            "sys.exit(1 if bad else 0)\n" % (str(SERVER), module, str(SERVER)))
    r = subprocess.run([sys.executable, "-I", "-c", code], cwd=str(SERVER),
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr


def test_entry_module_does_not_import_astropy_wcs():
    code = ("import sys, runpy\n"
            "sys.path.insert(0, %r)\n"
            "ns = runpy.run_path(%r, run_name='entry_probe')\n"
            "bad = [m for m in sys.modules if m == 'astropy.wcs']\n"
            "sys.exit(1 if bad else 0)\n"
            % (str(SERVER), str(ROOT / "packaging" / "entry.py")))
    r = subprocess.run([sys.executable, "-I", "-c", code], cwd=str(SERVER),
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
