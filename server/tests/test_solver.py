# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Plate-solver selection + the SimSolver false-solve guard (review 5d / P0-1).

The SimSolver is only ever a fallback when ASTAP isn't installed. On a real rig
that fallback must NOT echo the pointing hint back as a centered solve (it would
silently fake-center a real mount on a discovery miss). These tests pin that the
sim solver refuses for real (nina/alpaca) modes while still working for the
genuine simulator, and that ``get_solver`` plumbs the mode through."""
from __future__ import annotations

from pathlib import Path

import pytest

from _simhub import sim_hub  # noqa: F401 (fixture import)
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
    sol, why = _wcs_from_astap(tmp_path / "solve.ini", wcs_path)   # .ini absent -> uses .wcs
    assert sol is not None and why is None
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
    sol, why = _wcs_from_astap(tmp_path / "scaleless.ini", wcs_path)
    assert sol is None
    assert why is not None and "plate scale" in why, why


# ----------------------------------- a degenerate scale is no scale either (#943)
# The guard above caught a result with NO CD*/CDELT* cards. A reference point
# with an all-zero CD (wcslib reads that as 1 deg/pixel), a singular one, a
# zero CDELT or a NaN is the same bogus WCS by another route, and used to come
# back as a WcsSolution inside a successful SolveResult.

_REF_CARDS = {"CRVAL1": 83.8221, "CRVAL2": -5.3911,
              "CRPIX1": 512.0, "CRPIX2": 512.0}
_PIXEL_DEG = 0.0004305

_UNUSABLE = {
    "all-zero CD": {"CD1_1": 0.0, "CD1_2": 0.0, "CD2_1": 0.0, "CD2_2": 0.0},
    "singular CD": {"CD1_1": 1e-4, "CD1_2": 1e-4, "CD2_1": 1e-4, "CD2_2": 1e-4},
    "CD with only its first term": {"CD1_1": -_PIXEL_DEG},
    "zero CDELT pair": {"CDELT1": 0.0, "CDELT2": 0.0},
}
#: A FITS card cannot hold these, so only the .ini can deliver them.
_UNREPRESENTABLE = {
    "NaN in the CD": {"CD1_1": "nan", "CD1_2": 0.0, "CD2_1": 0.0,
                      "CD2_2": _PIXEL_DEG},
    "inf in the CD": {"CD1_1": -_PIXEL_DEG, "CD1_2": "inf", "CD2_1": 0.0,
                      "CD2_2": _PIXEL_DEG},
    "NaN CDELT": {"CDELT1": "nan", "CDELT2": _PIXEL_DEG},
}
_USABLE = {
    "north-up CD": {"CD1_1": -_PIXEL_DEG, "CD1_2": 0.0, "CD2_1": 0.0,
                    "CD2_2": _PIXEL_DEG},
    "CDELT and CROTA2": {"CDELT1": -_PIXEL_DEG, "CDELT2": _PIXEL_DEG,
                         "CROTA2": 12.5},
}


def _write_headerlet(path: Path, cards: dict, drop: tuple = ()) -> Path:
    """A headerlet of the reference point and ``cards``, less the ``drop``."""
    from astropy.io import fits as _fits
    hdr = _fits.Header()
    hdr["CTYPE1"] = "RA---TAN"
    hdr["CTYPE2"] = "DEC--TAN"
    for key, value in {**_REF_CARDS, **cards}.items():
        if key not in drop:
            hdr[key] = value
    hdr.totextfile(str(path))
    return path


def _write_ini(path: Path, cards: dict, **extra) -> Path:
    path.write_text("".join(f"{k}={v}\n"
                            for k, v in {**extra, **_REF_CARDS, **cards}.items()))
    return path


@pytest.mark.parametrize("cards", list(_UNUSABLE.values()),
                         ids=list(_UNUSABLE))
def test_wcs_from_astap_none_when_the_headerlet_scale_is_unusable(tmp_path, cards):
    """RED under mutation "the guard back to the scale-less test" (``if not
    sol.has_usable_scale():`` of ``_wcs_from_astap`` -> ``if sol.cd11 is None
    and sol.cdelt1 is None:``), observed on every case here, in the .ini test
    (which also holds the NaN and inf cases) and in the ``AstapSolver.solve``
    test below:

        E   AssertionError: assert WcsSolution(crval1=83.8221, crval2=-5.3911, ...) is None
    """
    from astrodeck.solve.astap import _wcs_from_astap
    wcs_path = _write_headerlet(tmp_path / "x.wcs", cards)
    sol, why = _wcs_from_astap(tmp_path / "x.ini", wcs_path)
    assert sol is None
    assert why is not None and "plate scale" in why, why


@pytest.mark.parametrize("cards", list({**_UNUSABLE, **_UNREPRESENTABLE}.values()),
                         ids=list({**_UNUSABLE, **_UNREPRESENTABLE}))
def test_wcs_from_astap_none_when_the_ini_scale_is_unusable(tmp_path, cards):
    from astrodeck.solve.astap import _wcs_from_astap
    ini = _write_ini(tmp_path / "x.ini", cards)
    sol, why = _wcs_from_astap(ini, tmp_path / "x.wcs")   # no headerlet
    assert sol is None
    assert why is not None and "plate scale" in why, why


@pytest.mark.parametrize("source", ["headerlet", "ini"])
@pytest.mark.parametrize("cards", list(_USABLE.values()), ids=list(_USABLE))
def test_wcs_from_astap_keeps_a_usable_scale(tmp_path, source, cards):
    """The control: the stricter guard must not reach a real solution, and
    this is what shows the cases above get as far as the guard."""
    from astrodeck.solve.astap import _wcs_from_astap
    if source == "headerlet":
        sol, why = _wcs_from_astap(tmp_path / "x.ini",
                                   _write_headerlet(tmp_path / "x.wcs", cards))
    else:
        sol, why = _wcs_from_astap(_write_ini(tmp_path / "x.ini", cards),
                                   tmp_path / "x.wcs")
    assert sol is not None and why is None
    assert sol.has_usable_scale()
    assert sol.crval1 == pytest.approx(_REF_CARDS["CRVAL1"])


# ------------------------------- why a solved frame has no WCS, per cause (#1025)
# `_wcs_from_astap` returned None for every one of these and `AstapSolver.solve`
# reported them all as ``success`` with no WCS and "solved by ASTAP", so each
# consumer (the hub's stamp, the sn_animation tool) guessed which it was.

def _write_raw_ini(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


_NO_WCS = {
    "headerlet without CRVAL1": (
        lambda tp: (tp / "x.ini", _write_headerlet(
            tp / "x.wcs", _USABLE["north-up CD"], drop=("CRVAL1",))),
        ("headerlet", "CRVAL1")),
    "headerlet without CRPIX2": (
        lambda tp: (tp / "x.ini", _write_headerlet(
            tp / "x.wcs", _USABLE["north-up CD"], drop=("CRPIX2",))),
        ("headerlet", "CRPIX2")),
    "headerlet with a non-numeric reference card": (
        lambda tp: (tp / "x.ini", _write_headerlet(
            tp / "x.wcs", {**_USABLE["north-up CD"], "CRVAL1": "abc"})),
        ("could not be read", "ValueError")),
    "headerlet with no scale": (
        lambda tp: (tp / "x.ini", _write_headerlet(tp / "x.wcs", {})),
        ("plate scale",)),
    ".ini without a reference point and no headerlet": (
        lambda tp: (_write_raw_ini(tp / "x.ini", "CDELT2=0.0004\n"),
                    tp / "x.wcs"),
        ("no WCS headerlet", "CRVAL1", "CRPIX2")),
    ".ini with a non-numeric reference card and no headerlet": (
        lambda tp: (_write_ini(tp / "x.ini", {"CRVAL2": "abc"}), tp / "x.wcs"),
        ("could not be read", "ValueError")),
    "no result files at all": (
        lambda tp: (tp / "x.ini", tp / "x.wcs"),
        ("could not be read", "FileNotFoundError")),
}


@pytest.mark.parametrize("build,words", list(_NO_WCS.values()),
                         ids=list(_NO_WCS))
def test_wcs_from_astap_says_why_it_returns_no_wcs(tmp_path, build, words):
    """RED under mutation "two reasons dropped" (the headerlet-card and the
    unreadable-card returns of ``_wcs_from_astap`` made ``return None,
    None``), observed on every case that reaches either (five of the seven)
    and on test_the_causes_of_no_wcs_are_not_all_one_answer:

        E   AssertionError: None
    """
    from astrodeck.solve.astap import _wcs_from_astap
    ini, wcs_path = build(tmp_path)
    sol, why = _wcs_from_astap(ini, wcs_path)
    assert sol is None
    assert why is not None and all(w in why for w in words), why


def test_the_causes_of_no_wcs_are_not_all_one_answer(tmp_path):
    """The defect: one answer for every cause. Five causes an operator would
    treat differently (a headerlet missing its reference point, an .ini with
    none, a solution with no scale, a card that is not a number, files that
    are not there) give five sentences."""
    from astrodeck.solve.astap import _wcs_from_astap
    kinds = ("headerlet without CRVAL1",
             ".ini without a reference point and no headerlet",
             "headerlet with no scale",
             "headerlet with a non-numeric reference card",
             "no result files at all")
    reasons = set()
    for i, kind in enumerate(kinds):
        build, _words = _NO_WCS[kind]
        folder = tmp_path / str(i)
        folder.mkdir()
        _sol, why = _wcs_from_astap(*build(folder))
        reasons.add(why)
    assert len(reasons) == len(kinds), reasons


class _FakeAstapProcess:
    returncode = 0

    async def wait(self):
        return 0

    def kill(self):
        pass


async def _solve_with_headerlet(tmp_path, monkeypatch, cards, drop=()):
    """``AstapSolver.solve`` against a fake ``astap_cli`` that leaves the .ini
    and the .wcs headerlet ASTAP would, for the image ``light.fits``."""
    import asyncio
    image = tmp_path / "light.fits"
    image.write_bytes(b"")

    async def fake_exec(*args, **kwargs):
        _write_ini(image.with_suffix(".ini"),
                   {"CDELT1": -_PIXEL_DEG, "CDELT2": _PIXEL_DEG,
                    "CROTA2": 12.5}, PLTSOLVD="T")
        _write_headerlet(image.with_suffix(".wcs"), cards, drop)
        return _FakeAstapProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return await AstapSolver("astap_cli").solve(image)


async def test_astap_solve_carries_a_usable_headerlet_wcs(tmp_path, monkeypatch):
    """The control for the next test: the fake reaches ``_wcs_from_astap``."""
    res = await _solve_with_headerlet(tmp_path, monkeypatch,
                                      _USABLE["north-up CD"])
    assert res.success
    assert res.wcs is not None and res.wcs.cd11 == pytest.approx(-_PIXEL_DEG)
    assert res.wcs_dropped_reason is None


@pytest.mark.parametrize("cards", list(_UNUSABLE.values()),
                         ids=list(_UNUSABLE))
async def test_astap_solve_drops_a_headerlet_wcs_it_cannot_use(
        tmp_path, monkeypatch, cards):
    """The solve still reports where ASTAP says it is looking (the position is
    not in doubt), but the unusable matrix is not handed on as a WCS, so the
    hub stamps nothing, calibrates no rotator from it and identifies no
    field from it."""
    res = await _solve_with_headerlet(tmp_path, monkeypatch, cards)
    assert res.success
    assert res.ra_hours == pytest.approx(_REF_CARDS["CRVAL1"] / 15.0)
    assert res.wcs is None
    assert res.wcs_dropped_reason is not None, "dropped, and it does not say why"
    assert "plate scale" in res.wcs_dropped_reason, res.wcs_dropped_reason


async def test_astap_solve_says_a_headerlet_without_its_reference_point_was_dropped(
        tmp_path, monkeypatch):
    """#1025. The solve is good (the .ini has the position) and the headerlet
    is not, and the result says which card it lacked rather than "solved by
    ASTAP" beside no WCS.

    RED under mutation "the reason not carried" (``AstapSolver.solve`` calls
    ``_result_from_ini(kv, wcs)`` and drops the second return), observed:

        E   AssertionError: dropped, and it does not say why
    """
    res = await _solve_with_headerlet(tmp_path, monkeypatch,
                                      _USABLE["north-up CD"], drop=("CRVAL1",))
    assert res.success
    assert res.wcs is None
    assert res.wcs_dropped_reason == "the WCS headerlet has no CRVAL1 card"


async def test_astap_solve_says_a_solve_with_no_headerlet_and_no_scale_was_dropped(
        tmp_path, monkeypatch):
    """The .ini-only path: the reference point is there, the scale is not."""
    res = await _solve_with_files(tmp_path, monkeypatch, {})
    assert res.success
    assert res.wcs is None
    assert res.wcs_dropped_reason is not None, "dropped, and it does not say why"
    assert "plate scale" in res.wcs_dropped_reason, res.wcs_dropped_reason


async def test_a_solve_that_failed_has_no_wcs_reason_to_give(tmp_path,
                                                             monkeypatch):
    """A solve that found nothing has no WCS to have dropped: its own message
    says why it failed, and the field stays None rather than repeat it."""
    import asyncio
    image = tmp_path / "light.fits"
    image.write_bytes(b"")

    async def fake_exec(*args, **kwargs):
        _write_raw_ini(image.with_suffix(".ini"),
                       "PLTSOLVD=F\nERROR=No solution found\n")
        return _FakeAstapProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    res = await AstapSolver("astap_cli").solve(image)
    assert not res.success
    assert res.message == "No solution found"
    assert res.wcs is None and res.wcs_dropped_reason is None


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


# ------------------------------------ an unstated scale is None, never 0.0 (#973)
# `_result_from_ini` read `abs(float(kv.get("CDELT2", 0))) * 3600`, so a solve
# that succeeded and whose .ini had no CDELT2 reported a pixel scale of zero.
# The scale is now CDELT2 when the .ini states it, else the scale of the CD
# matrix the same solve produced, else None.

async def _solve_with_files(tmp_path, monkeypatch, ini_cards, headerlet=None):
    """``AstapSolver.solve`` against a fake ``astap_cli`` that leaves a
    successful .ini holding ``ini_cards`` (and a headerlet, when given)."""
    import asyncio
    image = tmp_path / "light.fits"
    image.write_bytes(b"")

    async def fake_exec(*args, **kwargs):
        _write_ini(image.with_suffix(".ini"), ini_cards, PLTSOLVD="T")
        if headerlet is not None:
            _write_headerlet(image.with_suffix(".wcs"), headerlet)
        return _FakeAstapProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return await AstapSolver("astap_cli").solve(image)


def _rotated_cd(deg: float) -> dict:
    import math
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return {"CD1_1": -_PIXEL_DEG * c, "CD1_2": _PIXEL_DEG * s,
            "CD2_1": -_PIXEL_DEG * s, "CD2_2": -_PIXEL_DEG * c}


#: (.ini cards, headerlet cards or None). No case has a CDELT2 in the .ini.
_SCALE_FROM_THE_WCS = {
    "CD in the .ini": ({**_USABLE["north-up CD"]}, None),
    "CD in the headerlet": ({}, _USABLE["north-up CD"]),
    "rotated CD in the .ini": (_rotated_cd(33.0), None),
    "CDELT1 alone in the .ini": ({"CDELT1": -_PIXEL_DEG}, None),
}
_SCALE_NOT_STATED = {
    "no CD and no CDELT": ({}, None),
    "all-zero CD": (_UNUSABLE["all-zero CD"], None),
    "singular CD": (_UNUSABLE["singular CD"], None),
    "CDELT2 of zero": ({"CDELT1": -_PIXEL_DEG, "CDELT2": 0.0}, None),
    "NaN CDELT2": ({"CDELT1": -_PIXEL_DEG, "CDELT2": "nan"}, None),
}


@pytest.mark.parametrize("case", list(_SCALE_FROM_THE_WCS.values()),
                         ids=list(_SCALE_FROM_THE_WCS))
async def test_astap_scale_comes_from_the_cd_matrix_when_cdelt2_is_absent(
        tmp_path, monkeypatch, case):
    """RED under mutation "the old read" (the scale lines of
    ``_result_from_ini`` back to ``scale = abs(float(kv.get('CDELT2', 0))) *
    3600``), observed on every case:

        E   assert 0.0 == 1.549799... +- 1.5e-06
    """
    ini_cards, headerlet = case
    res = await _solve_with_files(tmp_path, monkeypatch, ini_cards, headerlet)
    assert res.success
    assert res.pixel_scale_arcsec == pytest.approx(_PIXEL_DEG * 3600.0)


@pytest.mark.parametrize("case", list(_SCALE_NOT_STATED.values()),
                         ids=list(_SCALE_NOT_STATED))
async def test_astap_scale_is_none_when_the_solve_states_none(
        tmp_path, monkeypatch, case):
    """A solve that succeeded and states no scale reports it unknown, which a
    consumer can tell from a scale of zero.

    RED under the same mutation, observed on every case:

        E   assert 0.0 is None
    """
    ini_cards, headerlet = case
    res = await _solve_with_files(tmp_path, monkeypatch, ini_cards, headerlet)
    assert res.success
    assert res.pixel_scale_arcsec is None


async def test_astap_scale_still_reads_cdelt2_when_the_ini_has_it(
        tmp_path, monkeypatch):
    """The control: the path every real ASTAP .ini takes is unchanged."""
    res = await _solve_with_files(
        tmp_path, monkeypatch,
        {"CDELT1": -_PIXEL_DEG, "CDELT2": _PIXEL_DEG, "CROTA2": 12.5})
    assert res.pixel_scale_arcsec == pytest.approx(_PIXEL_DEG * 3600.0)


def test_result_from_ini_without_cdelt2_is_not_a_scale_of_zero():
    """The issue's own shape: `_result_from_ini` fed an .ini with no CDELT2."""
    from astrodeck.solve.astap import _result_from_ini
    base = {"PLTSOLVD": "T", "CRVAL1": "150.0", "CRVAL2": "20.0",
            "CROTA2": "0.0"}
    res = _result_from_ini(base, None)
    assert res.success and res.pixel_scale_arcsec is None
    assert _result_from_ini({**base, "CDELT2": "0.0003"},
                            None).pixel_scale_arcsec == pytest.approx(1.08)


@pytest.mark.parametrize("terms,expected", [
    (dict(cd11=-_PIXEL_DEG, cd12=0.0, cd21=0.0, cd22=_PIXEL_DEG), 1.0),
    (dict(cd11=-2 * _PIXEL_DEG, cd12=0.0, cd21=0.0, cd22=_PIXEL_DEG / 2), 1.0),
    (dict(cdelt1=-_PIXEL_DEG, cdelt2=_PIXEL_DEG, crota2=12.5), 1.0),
    (dict(cdelt1=-_PIXEL_DEG), 1.0),
    (dict(cd11=0.0, cd12=0.0, cd21=0.0, cd22=0.0), None),
    (dict(cdelt1=_PIXEL_DEG, cdelt2=0.0), None),
    (dict(), None),
], ids=["CD", "CD with unequal axes (geometric mean)", "CDELT pair",
        "CDELT1 alone", "all-zero CD", "zero CDELT2", "no scale cards"])
def test_wcs_solution_states_its_pixel_scale(terms, expected):
    from astrodeck.solve.base import WcsSolution
    sol = WcsSolution(**{k.lower(): v for k, v in _REF_CARDS.items()}, **terms)
    got = sol.pixel_scale_arcsec()
    if expected is None:
        assert got is None
    else:
        assert got == pytest.approx(_PIXEL_DEG * 3600.0 * expected)


async def test_guide_offset_note_says_unknown_for_a_scale_the_solve_did_not_state(
        sim_hub, tmp_path, monkeypatch):
    """The consumer #973 names: ``measure_guide_offset`` printed the scale into
    the stored offset's note. The imaging solve states none (its .ini has no
    CDELT2 and no CD), the guide solve states one.

    RED under mutation "the old read" (see above), observed:

        E   AssertionError: {'camera': 'Sim Camera 533MM', 'guide': {...}, ...
        E   assert 0.0 is None

    and under mutation "the note prints a number whatever it is" (``_scale_text``
    of ``hub.py`` -> ``return f'{scale or 0.0:.2f}"/px'``), observed:

        E   assert 'main 0.00"/p...uide 1.55"/px' == 'main scale u...uide 1.55"/px'
    """
    import asyncio
    import astrodeck.providers as providers

    async def fake_exec(*args, **kwargs):
        image = Path(args[args.index("-f") + 1])
        stated = ({"CDELT1": -_PIXEL_DEG, "CDELT2": _PIXEL_DEG}
                  if "guide_offset_guide" in image.name else {})
        _write_ini(image.with_suffix(".ini"), {**stated, "CROTA2": 10.0},
                   PLTSOLVD="T")
        return _FakeAstapProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(providers, "pick_solver",
                        lambda hub: AstapSolver("astap_cli"))

    out = await sim_hub.measure_guide_offset(exposure_s=0.05,
                                             guide_exposure_s=0.05)

    assert out["main"]["ok"] and out["guide"]["ok"], out
    assert out["main"]["scale"] is None, out
    assert out["guide"]["scale"] == pytest.approx(_PIXEL_DEG * 3600.0), out
    assert out["offset"]["note"] == 'main scale unknown, guide 1.55"/px', out
