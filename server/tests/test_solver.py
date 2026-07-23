"""Plate-solver selection + the SimSolver false-solve guard (review 5d / P0-1).

The SimSolver is only ever a fallback when ASTAP isn't installed. On a real rig
that fallback must NOT echo the pointing hint back as a centered solve (it would
silently fake-center a real mount on a discovery miss). These tests pin that the
sim solver refuses for real (nina/alpaca) modes while still working for the
genuine simulator, and that ``get_solver`` plumbs the mode through."""
from __future__ import annotations

from pathlib import Path

import pytest

from astrodeck.solve import SimSolver, get_solver
from astrodeck.solve.astap import AstapSolver


class _FakeRig:
    ra_hours = 5.5
    dec_deg = 41.2


@pytest.mark.parametrize("mode", ["nina", "alpaca"])
async def test_simsolver_refuses_in_real_modes(tmp_path, mode):
    """A SimSolver built for a real (nina/alpaca) rig must FAIL with a clear message
    even when handed a hint — never echo the hint as a successful solve."""
    solver = SimSolver(sim_rig=None, mode=mode)
    res = await solver.solve(tmp_path / "x.fits", ra_hint=10.0, dec_hint=30.0)
    assert res.success is False
    assert "ASTAP" in res.message  # tells the operator how to fix it


async def test_simsolver_refuses_even_with_a_rig_object_in_nina_mode(tmp_path):
    """Mode wins over a stray sim_rig: refusing on a real rig is the priority."""
    solver = SimSolver(sim_rig=_FakeRig(), mode="nina")
    res = await solver.solve(tmp_path / "x.fits")
    assert res.success is False


async def test_simsolver_still_solves_for_the_simulator(tmp_path):
    """The legitimate offline path still works: sim mode with a sim rig solves to
    the rig's true pointing (the center/sync loop depends on this)."""
    solver = SimSolver(sim_rig=_FakeRig(), mode="sim")
    res = await solver.solve(tmp_path / "x.fits")
    assert res.success is True
    assert res.ra_hours == pytest.approx(5.5)
    assert res.dec_deg == pytest.approx(41.2)


async def test_simsolver_solves_from_hint_when_no_mode(tmp_path):
    """No mode / sim mode with only a hint (no rig) still echoes the hint — this
    is the offline-demo behavior; the refusal only triggers for real modes."""
    solver = SimSolver(sim_rig=None, mode=None)
    res = await solver.solve(tmp_path / "x.fits", ra_hint=3.0, dec_hint=-7.0)
    assert res.success is True
    assert res.ra_hours == pytest.approx(3.0)
    assert res.dec_deg == pytest.approx(-7.0)


def test_get_solver_passes_mode_to_simsolver_fallback(monkeypatch):
    """When ASTAP isn't found, get_solver must hand the hub mode to the SimSolver
    so the fallback can refuse on a real rig."""
    import astrodeck.solve as solve_pkg
    monkeypatch.setattr(solve_pkg, "find_astap", lambda: None)
    s = get_solver(sim_rig=None, mode="nina")
    assert isinstance(s, SimSolver)
    assert s.mode == "nina"


def test_get_solver_prefers_astap_when_present(monkeypatch):
    import astrodeck.solve as solve_pkg
    monkeypatch.setattr(solve_pkg, "find_astap", lambda: r"C:\fake\astap.exe")
    s = get_solver(sim_rig=None, mode="nina")
    assert isinstance(s, AstapSolver)


def test_wcs_from_astap_parse(tmp_path):
    from astropy.io import fits as _fits
    from astrodeck.solve.astap import _wcs_from_astap
    hdr = _fits.Header()
    hdr["CTYPE1"] = "RA---TAN"
    hdr["CTYPE2"] = "DEC--TAN"
    hdr["CRVAL1"] = 83.8221
    hdr["CRVAL2"] = -5.3911
    hdr["CRPIX1"] = 512.0
    hdr["CRPIX2"] = 512.0
    hdr["CD1_1"] = -0.0004305
    hdr["CD1_2"] = 0.0
    hdr["CD2_1"] = 0.0
    hdr["CD2_2"] = 0.0004305
    wcs_path = tmp_path / "solve.wcs"
    hdr.totextfile(str(wcs_path))
    sol = _wcs_from_astap(tmp_path / "solve.ini", wcs_path)   # .ini absent -> uses .wcs
    assert sol is not None
    assert sol.crval1 == pytest.approx(83.8221)
    assert sol.crpix1 == pytest.approx(512.0)
    assert sol.cd11 == pytest.approx(-0.0004305)
    assert sol.cd22 == pytest.approx(0.0004305)


def test_wcs_from_astap_none_when_scaleless(tmp_path):
    # A headerlet with a reference point but NO CD*/CDELT* is bogus (astropy
    # would default to 1 deg/px); _wcs_from_astap must return None, not a
    # scale-less WcsSolution (spec §8: never write a bogus value).
    from astropy.io import fits as _fits
    from astrodeck.solve.astap import _wcs_from_astap
    hdr = _fits.Header()
    hdr["CTYPE1"] = "RA---TAN"
    hdr["CTYPE2"] = "DEC--TAN"
    hdr["CRVAL1"] = 83.8221
    hdr["CRVAL2"] = -5.3911
    hdr["CRPIX1"] = 512.0
    hdr["CRPIX2"] = 512.0
    wcs_path = tmp_path / "scaleless.wcs"
    hdr.totextfile(str(wcs_path))
    assert _wcs_from_astap(tmp_path / "scaleless.ini", wcs_path) is None


async def test_simsolver_returns_wcs(tmp_path):
    import numpy as np
    from astrodeck.devices.base import CameraFrame
    from astrodeck.imaging.fitsio import save_fits
    frame = CameraFrame(data=np.zeros((32, 32), dtype=np.uint16), exposure_s=1.0,
                        gain=100, offset=10, binning=1, bayer_pattern=None,
                        temperature_c=-10.0, timestamp=1_772_775_791.0)
    path = save_fits(frame, tmp_path / "sim.fits")
    rig = _FakeRig()                                  # ra_hours=5.5, dec_deg=41.2
    solver = SimSolver(sim_rig=rig, mode="sim")
    res = await solver.solve(path)
    assert res.success and res.wcs is not None
    assert res.wcs.crval1 == pytest.approx(rig.ra_hours * 15.0)
    assert res.wcs.crval2 == pytest.approx(rig.dec_deg)


def test_sim_wcs_cd_matches_canonical_crota(tmp_path):
    # The sim WCS CD matrix must follow the canonical CROTA convention (FITS WCS
    # Paper II), verified against astropy's pixel_scale_matrix at several
    # rotations. A prior sign mismatch on the off-diagonals turned the sky the
    # wrong way for non-zero rotation (rot=0 was — and stays — unaffected).
    import math  # noqa: F401 - kept for parity with the module under test
    import numpy as np
    from astropy.wcs import WCS
    from astrodeck.solve.simsolver import _sim_wcs

    scale_arcsec = 1.55
    scale = scale_arcsec / 3600.0
    for rot_deg in (0.0, 30.0, 90.0, 210.0):
        sol = _sim_wcs(tmp_path / "absent.fits", 5.5, 41.2, rot_deg, scale_arcsec)
        cd = np.array([[sol.cd11, sol.cd12], [sol.cd21, sol.cd22]])
        w = WCS(naxis=2)
        w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
        w.wcs.cdelt = [-scale, scale]
        w.wcs.crota = [0.0, rot_deg]
        assert np.allclose(cd, w.pixel_scale_matrix, atol=1e-12), rot_deg
    # rot=0 stays diagonal (RA flip only) — the common, previously-tested path.
    z = _sim_wcs(tmp_path / "absent.fits", 5.5, 41.2, 0.0, scale_arcsec)
    assert z.cd12 == 0.0 and z.cd21 == 0.0
    assert z.cd11 == pytest.approx(-scale) and z.cd22 == pytest.approx(scale)
