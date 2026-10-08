# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Does each saved light cover the panel it was shot for? (#177, the server
half: ``sequence/coverage.py``, WP-123.)

The ledger counts frames; nothing used to say whether those frames cover the
tile they were counted toward. ``coverage.coverage_report`` reads each banked
light's plate-solved WCS from its FITS header and lays it over the panel's own
footprint (``framing.panel_footprint``, the corner geometry ``reframe_carry``
measures). It is ADVISORY: a flag never changes a panel's completion, and a
light with no WCS is ``unstamped``, never covered.

THE FIXTURES are synthetic headers, written without a pixel (the verifier reads
headers only, and a file with no data section is the proof of it): CRVAL at the
panel centre, a CD matrix at 1.55 arcsec per pixel in the CROTA2 convention
(east to the left, as every real solve writes it), NAXIS from a 4144 x 2822
sensor. The sky coordinates are made up; no test here has a site.

NUMBERS ARE COMPUTED, NOT GUESSED (the lesson of the plan numbers). The panel
is 1.784 x 1.215 deg, so a light shifted along the panel's own x axis by a
fraction ``s`` of its width covers ``1 - s`` of it, a light turned a quarter
turn covers ``fov_y / fov_x`` of it (0.681: the square the two share), and one
solved at half the scale covers a quarter. The tangent point of a shifted
light differs from the panel's, so the assertions carry a tolerance of a
percent, far below any threshold they are compared with.

MUTATIONS. Each test that guards a branch names the mutant of
``sequence/coverage.py`` (or ``catalog/framing.py``) it kills and quotes the
assertion that mutant produced. They were run from a byte backup of the
module inside this worktree, with bytecode writing off and the module's pyc
deleted (a same-size mutant must not be served from a stale one), restored
byte-identically (SHA-256 compared) and grepped clean. The four the brief
names are quoted at their tests; the rest, each observed red under this
file's own run (the count is how many of its tests failed), are:

* "the panel outline in ``_CORNERS``' Z order" (``for i in (0, 1, 3, 2)`` ->
  ``(0, 1, 2, 3)``: a bow tie, not a rectangle): 19 failed.
* "the clip's winding ignored" (``clip = _counter_clockwise(clip)`` ->
  ``list(clip)``): 18; ``TestPolygons`` fails with ``assert 0.0 == 0.5 +-
  5.0e-07``.
* "the footprint's half-width and half-height swapped": 16.
* "the angle read off the y edge, not the x edge": 7.
* "the overlap divided by the light's area, not the panel's": 2; a light at
  half scale then covers all of itself: ``assert 1.0 == 0.25 +- 0.01``.
* "no memo" (``_MEMO[key] = footprint`` -> ``pass``): 2; and "concurrent
  misses both read": 1, ``assert 2 == 1``.
* "the single-panel floor ignored" (``if single:`` -> ``if False:``): 3;
  ``assert 1.0 == 0.9`` for a block with an overlap of 0.
* "a scale-less or all-zero CD accepted" (``if not _has_scale(header):`` ->
  ``if False:``): 2; ``assert (3, 1, 2) == (3, 0, 3)``.
* "far frames not guarded" (``< FAR_COS`` -> ``< -2.0``): 1; a light 89
  degrees away is no longer flagged, ``assert 0 == 1``.
* "rejected lights counted": ``assert 4 == 2``; "darks counted": ``assert 3
  == 2``; "single decided by the member count": 1.
* "the offset in the wrong units" (``* 60.0`` -> ``* 3600.0``): ``assert
  3212.38 == 53.526666666666664 +- 0.535267``.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
import warnings
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrodeck.catalog import framing
from astrodeck.catalog.coords import angular_sep_deg
from astrodeck.imaging.fitsio import write_wcs
from astrodeck.sequence import coverage
from astrodeck.sequence.models import (ExposureStep, SequencePlan, Target,
                                       TargetGroup)
from astrodeck.sequence.session import Session, SessionFrame
from astrodeck.solve.base import WcsSolution

# A made-up panel centre: not round, and not near the poles or the RA seam.
RA = 5.4321
DEC = 23.4567
NX, NY = 4144, 2822
SCALE = 1.55                                    # arcsec per pixel
FOV_X = NX * SCALE / 3600.0                     # 1.78422 deg
FOV_Y = NY * SCALE / 3600.0                     # 1.21506 deg
GROUP = "grp"
STEP = "stp-l"
DARK_STEP = "stp-dark"


# ------------------------------------------------------------------ fixtures

def _header(*, ra_hours: float, dec_deg: float, rho_deg: float = 0.0,
            scale: float = SCALE, nx: int = NX, ny: int = NY,
            stamped: bool = True, scale_less: bool = False,
            mirrored: bool = False) -> fits.Header:
    """The header of a saved light. ``rho_deg`` is CROTA2: the CD matrix is
    the standard one (east to the left, ``cdelt1 < 0``), the shape ASTAP
    writes, so the pixel x axis points at ``rho + 180`` and the footprint
    comparison has to fold a half turn to see the camera at its planned
    angle."""
    hdr = fits.Header()
    hdr["SIMPLE"] = True
    hdr["BITPIX"] = 16
    hdr["NAXIS"] = 2
    hdr["NAXIS1"] = nx
    hdr["NAXIS2"] = ny
    hdr["IMAGETYP"] = "Light"
    if not stamped:
        return hdr
    s = scale / 3600.0
    # ``mirrored``: the opposite parity (east to the right), which the
    # footprint's outline then runs the other way round.
    c1, c2 = (s, s) if mirrored else (-s, s)
    rho = math.radians(rho_deg)
    hdr["CTYPE1"] = "RA---TAN"
    hdr["CTYPE2"] = "DEC--TAN"
    hdr["CUNIT1"] = "deg"
    hdr["CUNIT2"] = "deg"
    hdr["CRVAL1"] = ra_hours * 15.0
    hdr["CRVAL2"] = dec_deg
    hdr["CRPIX1"] = (nx + 1) / 2.0
    hdr["CRPIX2"] = (ny + 1) / 2.0
    if not scale_less:
        hdr["CD1_1"] = c1 * math.cos(rho)
        hdr["CD1_2"] = -c2 * math.sin(rho)
        hdr["CD2_1"] = c1 * math.sin(rho)
        hdr["CD2_2"] = c2 * math.cos(rho)
    return hdr


def _write(path: Path, header: fits.Header) -> Path:
    """A FITS file that is ONLY a header block: the data section the header
    promises (25 MB) is not there. A reader that touched a pixel would
    fail or warn on it, so every test in this file is also a proof that the
    verifier reads headers alone."""
    path.write_bytes(
        header.tostring(sep="", endcard=True, padding=True).encode("ascii"))
    return path


def _at(dx_deg: float, dy_deg: float, theta_deg: float,
        ra_hours: float = RA, dec_deg: float = DEC) -> tuple[float, float]:
    """The sky point ``(dx, dy)`` degrees from ``(ra, dec)`` along a panel's
    OWN axes, the panel turned to ``theta``: where a light shifted by that
    much is centred."""
    t = math.radians(theta_deg)
    xi = dx_deg * math.cos(t) - dy_deg * math.sin(t)
    eta = dx_deg * math.sin(t) + dy_deg * math.cos(t)
    return framing.deproject(xi, eta, ra_hours, dec_deg)


def _plan(*, rows: int = 1, cols: int = 1, overlap: float = 0.25,
          pa: float | None = None, rotate: bool = False,
          member_rotation: float | None = None,
          panels=((RA, DEC),), geometry: dict | None = None) -> SequencePlan:
    light = ExposureStep(id=STEP, filter="L", exposure_s=60, count=20)
    dark = ExposureStep(id=DARK_STEP, filter="L", exposure_s=60, count=5,
                        frame_type="Dark")
    targets = []
    for i, (ra, dec) in enumerate(panels):
        targets.append(Target(
            id=f"pnl-{i}", name=f"P {i + 1}", ra_hours=ra, dec_deg=dec,
            steps=[light.model_copy(), dark.model_copy()],
            mosaic_group=GROUP, panel_row=i // cols, panel_col=i % cols,
            rotation_deg=member_rotation))
    group = TargetGroup(
        id=GROUP, name="block", rotate=rotate, pa_deg=pa,
        geometry=geometry if geometry is not None else {
            "rows": rows, "cols": cols, "overlap": overlap,
            "fov_x": FOV_X, "fov_y": FOV_Y})
    return SequencePlan(name="t", targets=targets, groups=[group])


def _frame(path: Path | str, *, target: str = "pnl-0", step: str = STEP,
           accepted: bool = True, override: str | None = None) -> SessionFrame:
    return SessionFrame(target_id=target, step_id=step, path=str(path),
                        auto_accepted=accepted, override=override)


def _session(plan: SequencePlan, frames: list[SessionFrame]) -> Session:
    return Session(plan=plan, frames=frames)


def _one(report: dict, index: int = 0) -> dict:
    """The one group's ``index``-th panel row."""
    assert len(report["groups"]) == 1
    return report["groups"][0]["panels"][index]


def _on_tile_light(tmp_path: Path, name: str = "on.fits", *, rho: float = 0.0,
                   dx: float = 0.0, dy: float = 0.0, theta: float = 0.0,
                   **header) -> Path:
    """A light centred ``(dx, dy)`` degrees off the panel (in the panel's own
    axes, turned to ``theta``), solved at CROTA2 ``rho``."""
    ra, dec = _at(dx, dy, theta)
    return _write(tmp_path / name,
                  _header(ra_hours=ra, dec_deg=dec, rho_deg=rho, **header))


@pytest.fixture(autouse=True)
def _fresh_memo():
    """The header memo is process-wide; a test that counts reads starts from
    nothing, and none leaks its files' keys into the next."""
    coverage.clear_memo()
    yield
    coverage.clear_memo()


# ===================================================== the public footprint

class TestPanelFootprint:
    def test_it_is_the_geometry_corners_measures(self):
        """``framing.panel_footprint`` is the corner math ``_corners`` ran
        inline, factored out: for every panel of a turned 3 x 2 grid it
        equals what ``_corners`` answers for that panel, to the last bit
        (``_corners`` now calls it, so this pins the factoring against an
        INDEPENDENT copy of the old loop written here).

        RED under mutation "the footprint swaps its half-width and
        half-height" (``framing.panel_footprint``'s ``gx, gy = sx * fov_x /
        2.0, sy * fov_y / 2.0`` -> ``gx, gy = sx * fov_y / 2.0, sy * fov_x
        / 2.0``), 15 of this file's tests fail, this one with:

            AssertionError: panel (0, 0) corner 0 moved:
            (5.353321619263148, 22.773289020832426) !=
            (5.324843217806967, 22.856303797063546)
        """
        frame = {"ra_hours": RA, "dec_deg": DEC, "rows": 3, "cols": 2,
                 "overlap": 0.25, "rotation_deg": 33.0,
                 "fov_x": FOV_X, "fov_y": FOV_Y}
        panels = framing.compute_mosaic({
            "ra_hours": RA, "dec_deg": DEC, "rows": 3, "cols": 2,
            "overlap": 0.25, "rotation_deg": 33.0,
            "fov_x_deg": FOV_X, "fov_y_deg": FOV_Y})["panels"]
        held = framing._corners(frame, None)
        assert len(held) == 6, "premise: six panels laid out"
        for p in panels:
            theta = math.radians(p["rotation_deg"])
            expected = []
            for sx, sy in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
                gx, gy = sx * FOV_X / 2.0, sy * FOV_Y / 2.0
                expected.append(framing.deproject(
                    gx * math.cos(theta) - gy * math.sin(theta),
                    gx * math.sin(theta) + gy * math.cos(theta),
                    p["ra_hours"], p["dec_deg"]))
            got = framing.panel_footprint(p["ra_hours"], p["dec_deg"],
                                          p["rotation_deg"], FOV_X, FOV_Y)
            key = (p["row"], p["col"])
            assert len(got) == 4
            for i in range(4):
                assert got[i] == expected[i], (
                    f"panel {key} corner {i} moved: {got[i]} != {expected[i]}")
                assert held[key][i] == expected[i], (
                    f"_corners' panel {key} corner {i} moved")

    def test_it_lays_the_rectangle_out_in_its_own_tangent_plane(self):
        """Projected back about the panel's own centre, the four corners are
        the rectangle's, turned to the panel's angle: half a width east-west
        and half a height north-south at PA 0, and the same rectangle
        turned by ``pa`` otherwise."""
        for pa in (0.0, 90.0, 33.0):
            pts = [framing.project(ra, dec, RA, DEC) for ra, dec in
                   framing.panel_footprint(RA, DEC, pa, FOV_X, FOV_Y)]
            t = math.radians(pa)
            for (sx, sy), (xi, eta) in zip(
                    ((-1, -1), (-1, 1), (1, -1), (1, 1)), pts):
                gx, gy = sx * FOV_X / 2.0, sy * FOV_Y / 2.0
                assert xi == pytest.approx(gx * math.cos(t) - gy * math.sin(t),
                                           abs=1e-9)
                assert eta == pytest.approx(gx * math.sin(t) + gy * math.cos(t),
                                            abs=1e-9)


# ============================================================ the clipper

class TestPolygons:
    SQUARE = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]

    def test_area_is_signed_by_winding(self):
        assert coverage.polygon_area(self.SQUARE) == pytest.approx(1.0)
        assert coverage.polygon_area(self.SQUARE[::-1]) == pytest.approx(-1.0)

    def test_two_squares_shifted_by_half_share_half(self):
        other = [(x + 0.5, y) for x, y in self.SQUARE]
        got = coverage.clip_convex(other, self.SQUARE)
        assert abs(coverage.polygon_area(got)) == pytest.approx(0.5)

    def test_a_clockwise_subject_clips_the_same(self):
        """The subject's winding never enters: a clockwise subject over a
        counter-clockwise clip, and the other way round, share the same
        area."""
        other = [(x + 0.5, y) for x, y in self.SQUARE]
        for subject, clip in ((other[::-1], self.SQUARE),
                              (other, self.SQUARE[::-1]),
                              (other[::-1], self.SQUARE[::-1])):
            got = coverage.clip_convex(subject, clip)
            assert abs(coverage.polygon_area(got)) == pytest.approx(0.5)

    def test_disjoint_and_contained(self):
        far = [(x + 5.0, y) for x, y in self.SQUARE]
        assert coverage.clip_convex(far, self.SQUARE) == []
        inner = [(0.25 + x * 0.5, 0.25 + y * 0.5) for x, y in self.SQUARE]
        assert abs(coverage.polygon_area(
            coverage.clip_convex(inner, self.SQUARE))) == pytest.approx(0.25)
        assert abs(coverage.polygon_area(
            coverage.clip_convex(self.SQUARE, inner))) == pytest.approx(0.25)

    def test_a_turned_square_over_itself(self):
        """A unit square turned 45 degrees about its centre shares the
        octagon: ``2 * (sqrt(2) - 1)`` of it."""
        c = 0.5
        t = math.radians(45)
        turned = [(c + (x - c) * math.cos(t) - (y - c) * math.sin(t),
                   c + (x - c) * math.sin(t) + (y - c) * math.cos(t))
                  for x, y in self.SQUARE]
        got = abs(coverage.polygon_area(coverage.clip_convex(turned,
                                                             self.SQUARE)))
        assert got == pytest.approx(2.0 * (math.sqrt(2.0) - 1.0), abs=1e-9)


# ===================================================== a light on its panel

class TestOnTheTile:
    def test_a_light_exactly_on_its_panel_is_covered_and_not_flagged(
            self, tmp_path):
        """The control: a light centred on its panel, shot at the planned
        angle of a fixed camera, has an overlap of 1, an offset of 0 and a
        measured angle of 0 from the plan, and is not flagged. (The solve's
        x axis points at PA + 180, so the angle comes out at 0 only with the
        half turn folded: see ``test_a_half_turn_is_the_same_footprint``.)
        """
        path = _on_tile_light(tmp_path, rho=30.0, theta=30.0)
        report = coverage.coverage_report(
            _session(_plan(pa=30.0), [_frame(path)]))
        row = _one(report)
        assert (row["frames"], row["stamped"], row["unstamped"],
                row["flagged"]) == (1, 1, 0, 0)
        assert row["worst_overlap_frac"] == pytest.approx(1.0, abs=1e-4)
        assert row["worst_offset_arcmin"] == pytest.approx(0.0, abs=0.01)
        assert row["worst_angle_off_deg"] == pytest.approx(0.0, abs=0.01)

    def test_a_light_half_a_panel_off_is_flagged(self, tmp_path):
        """CRVAL moved half a panel width along the panel's own x axis: it
        covers half the tile (0.5) and is flagged, at an offset of half the
        field's width in arcminutes.

        RED under mutation "the threshold comparison inverted" (``overlap_frac
        < threshold - ON_TILE_EPS`` -> ``overlap_frac >= threshold -
        ON_TILE_EPS``; 14 tests fail), here:

            assert 0 == 1                       (flagged)

        and in the control above:

            assert (1, 1, 0, 1) == (1, 1, 0, 0)
              At index 3 diff: 1 != 0
        """
        path = _on_tile_light(tmp_path, dx=FOV_X / 2.0, theta=0.0)
        row = _one(coverage.coverage_report(
            _session(_plan(pa=0.0), [_frame(path)])))
        assert row["flagged"] == 1
        assert row["worst_overlap_frac"] == pytest.approx(0.5, abs=0.01)
        assert row["worst_offset_arcmin"] == pytest.approx(
            FOV_X / 2.0 * 60.0, rel=0.01)

    def test_a_two_percent_offset_is_not_flagged(self, tmp_path):
        path = _on_tile_light(tmp_path, dx=FOV_X * 0.02, theta=0.0)
        row = _one(coverage.coverage_report(
            _session(_plan(pa=0.0), [_frame(path)])))
        assert row["flagged"] == 0
        assert row["worst_overlap_frac"] == pytest.approx(0.98, abs=0.005)

    def test_a_quarter_turn_on_a_non_square_panel_is_flagged(self, tmp_path):
        """Centred and the right size, but turned 90 degrees on a 1.78 x 1.22
        panel: they share the square ``fov_y`` on a side, which is
        ``fov_y / fov_x`` = 0.681 of the tile. Flagged, and the angle off the
        plan reads 90.

        RED under mutation "the footprint ignoring Target.rotation_deg" is a
        different test (below); this one is graded against the plan's own
        angle and would stay green under it."""
        path = _on_tile_light(tmp_path, rho=90.0, theta=0.0)
        row = _one(coverage.coverage_report(
            _session(_plan(pa=0.0), [_frame(path)])))
        assert row["flagged"] == 1
        assert row["worst_overlap_frac"] == pytest.approx(FOV_Y / FOV_X,
                                                          abs=0.005)
        assert row["worst_angle_off_deg"] == pytest.approx(90.0, abs=0.01)

    def test_a_half_turn_is_the_same_footprint(self, tmp_path):
        """A rectangle turned half a turn is the same footprint, and the
        solve's own x axis already points at PA + 180 (east is left), so the
        ordinary light at the planned angle reads 180 from it unfolded. Two
        lights, the standard and its mirrored twin (CROTA2 + 180, which flips
        the pixel x axis back to PA): both are 0 degrees off, neither is
        flagged.

        RED under mutation "angle compared without the mod-180 equivalence"
        (the fold ``% 180.0`` -> ``% 360.0`` in ``angle_off_deg``, so the
        half turn is kept): the measured x axis is at 210 for a plan of 30,
        and this and the control above fail with

            assert 180.0 == 0.0 +- 0.01
              Obtained: 180.0
              Expected: 0.0 +- 0.01

        THE OVERLAP ITSELF NEEDS NO FOLD, and the mutant leaves every overlap
        number as it was: both outlines are rectangles, symmetric under a half
        turn, so the clip is blind to which way round a corner ring runs. The
        mod-180 equivalence is carried by the angle the answer reports
        (``worst_angle_off_deg``), which is what ``angle_check`` judges by.
        """
        standard = _on_tile_light(tmp_path, "std.fits", rho=30.0, theta=30.0)
        twin = _on_tile_light(tmp_path, "twin.fits", rho=210.0, theta=30.0)
        row = _one(coverage.coverage_report(_session(
            _plan(pa=30.0), [_frame(standard), _frame(twin)])))
        assert row["stamped"] == 2
        assert row["flagged"] == 0
        assert row["worst_overlap_frac"] == pytest.approx(1.0, abs=1e-4)
        assert row["worst_angle_off_deg"] == pytest.approx(0.0, abs=0.01)

    def test_a_mirrored_solve_is_covered_the_same(self, tmp_path):
        """A WCS of the other parity (east to the right, ``cdelt1 > 0``):
        its corner ring runs the other way round on the sky, and the verdict
        is the same: covered, 0 degrees off, and half a panel off is half."""
        on = _on_tile_light(tmp_path, "m_on.fits", rho=30.0, theta=30.0,
                            mirrored=True)
        off = _on_tile_light(tmp_path, "m_off.fits", rho=30.0, theta=30.0,
                             dx=FOV_X / 2.0, mirrored=True)
        row = _one(coverage.coverage_report(_session(
            _plan(pa=30.0), [_frame(on), _frame(off)])))
        assert (row["stamped"], row["flagged"]) == (2, 1)
        assert row["worst_overlap_frac"] == pytest.approx(0.5, abs=0.01)
        control = _one(coverage.coverage_report(_session(
            _plan(pa=30.0), [_frame(on)])))
        assert control["worst_overlap_frac"] == pytest.approx(1.0, abs=1e-4)
        assert control["worst_angle_off_deg"] == pytest.approx(0.0, abs=0.01)

    def test_angle_off_deg_folds_to_ninety(self):
        """The helper itself, at the cases that matter: the angle error is
        the distance mod 180, in [0, 90]."""
        f = coverage.angle_off_deg
        assert f(210.0, 30.0) == pytest.approx(0.0)
        assert f(30.0, 30.0) == pytest.approx(0.0)
        assert f(120.0, 30.0) == pytest.approx(90.0)
        assert f(35.0, 30.0) == pytest.approx(5.0)
        assert f(25.0, 30.0) == pytest.approx(5.0)
        assert f(-170.0, 30.0) == pytest.approx(20.0)

    def test_across_ra_zero(self, tmp_path):
        """A panel straddling the 0h seam: the light on it covers it whole,
        and one shifted half a width east covers half, though the RA of the
        light's corners jumps from 23.99 h to 0.01 h."""
        ra = 0.002                                      # 0.03 deg east of 0h
        centred = _write(tmp_path / "c.fits", _header(ra_hours=ra, dec_deg=DEC))
        e_ra, e_dec = _at(FOV_X / 2.0, 0.0, 0.0, ra, DEC)
        shifted = _write(tmp_path / "s.fits",
                         _header(ra_hours=e_ra, dec_deg=e_dec))
        wrap = _plan(pa=0.0, panels=((ra, DEC),))
        row = _one(coverage.coverage_report(_session(
            wrap, [_frame(centred), _frame(shifted)])))
        assert row["stamped"] == 2
        assert row["flagged"] == 1
        assert row["worst_overlap_frac"] == pytest.approx(0.5, abs=0.01)
        assert min(framing.project(r, d, ra, DEC)[0] for r, d in
                   framing.panel_footprint(ra, DEC, 0.0, FOV_X, FOV_Y)) < 0, (
            "premise: the panel's corners are on both sides of the seam")

    def test_a_light_solved_at_the_wrong_plate_scale_is_partial(self, tmp_path):
        """The WCS is measured and the plan's field is the nominal one
        (#168): a light solved at half the plan's scale, centred, covers a
        quarter of the tile, which is the intended signal."""
        path = _on_tile_light(tmp_path, scale=SCALE / 2.0)
        row = _one(coverage.coverage_report(
            _session(_plan(pa=0.0), [_frame(path)])))
        assert row["worst_overlap_frac"] == pytest.approx(0.25, abs=0.01)
        assert row["flagged"] == 1

    def test_a_light_that_covers_the_whole_tile_with_room_to_spare_is_covered(
            self, tmp_path):
        """Coverage, not equality: a light at twice the plan's scale holds the
        whole panel."""
        path = _on_tile_light(tmp_path, scale=SCALE * 2.0)
        row = _one(coverage.coverage_report(
            _session(_plan(pa=0.0), [_frame(path)])))
        assert row["worst_overlap_frac"] == pytest.approx(1.0, abs=1e-4)
        assert row["flagged"] == 0

    def test_a_light_on_the_far_side_of_the_sky_is_off_the_tile(self, tmp_path):
        """A frame a long way off still has a WCS and is measured: nothing of
        it on the tile, flagged, its offset the great-circle separation in
        arcminutes, and no NaN anywhere. (RA moved 100 degrees at this
        declination is 89 degrees of sky, so every corner is past the
        projection's useful range.)"""
        far_ra = (RA + 100.0 / 15.0) % 24.0
        path = _write(tmp_path / "far.fits",
                      _header(ra_hours=far_ra, dec_deg=DEC))
        row = _one(coverage.coverage_report(
            _session(_plan(pa=0.0), [_frame(path)])))
        assert row["stamped"] == 1
        assert row["flagged"] == 1
        assert row["worst_overlap_frac"] == 0.0
        assert row["worst_offset_arcmin"] == pytest.approx(
            angular_sep_deg(RA, DEC, far_ra, DEC) * 60.0, rel=0.01)
        assert row["worst_angle_off_deg"] is None
        assert all(isinstance(row[k], float) and math.isfinite(row[k])
                   for k in ("worst_overlap_frac", "worst_offset_arcmin"))


# ==================================================== which angle the plan has

class TestThePlannedAngle:
    def test_a_rotate_group_is_judged_at_the_members_rotation(self, tmp_path):
        """A rotate group commands each panel's OWN angle
        (``Target.rotation_deg``; per panel after #175), and the light shot
        at it covers the tile even where the group's ``pa_deg`` says
        something else.

        RED under mutation "the footprint ignoring Target.rotation_deg"
        (``_planned_angle`` answering ``group.pa_deg`` for a rotate group):
        the plan is laid out at 0 and the light was shot at 90, so

            assert 1 == 0                       (flagged)
        """
        path = _on_tile_light(tmp_path, rho=90.0, theta=90.0)
        plan = _plan(pa=0.0, rotate=True, member_rotation=90.0)
        row = _one(coverage.coverage_report(_session(plan, [_frame(path)])))
        assert row["flagged"] == 0
        assert row["worst_overlap_frac"] == pytest.approx(1.0, abs=1e-4)
        assert row["worst_angle_off_deg"] == pytest.approx(0.0, abs=0.01)

    def test_a_fixed_camera_is_judged_at_the_groups_angle(self, tmp_path):
        """A fixed camera (``rotate`` false) has no commanded angle on its
        members: the plan's angle is the group's ``pa_deg``."""
        ok = _on_tile_light(tmp_path, "ok.fits", rho=40.0, theta=40.0)
        off = _on_tile_light(tmp_path, "off.fits", rho=130.0, theta=40.0)
        row = _one(coverage.coverage_report(_session(
            _plan(pa=40.0), [_frame(ok), _frame(off)])))
        assert (row["stamped"], row["flagged"]) == (2, 1)
        assert row["worst_angle_off_deg"] == pytest.approx(90.0, abs=0.01)

    def test_with_no_planned_angle_only_the_offset_is_judged(self, tmp_path):
        """"Any angle" (``pa_deg`` None): the light is judged at its OWN
        measured angle, so a turn is nobody's error and only the offset
        counts. Turned 37 degrees and centred: covered, no angle to report.
        The same light shifted half a width along ITS OWN x axis is
        flagged."""
        on = _on_tile_light(tmp_path, "on.fits", rho=37.0)
        shifted = _on_tile_light(tmp_path, "sh.fits", rho=37.0,
                                 dx=FOV_X / 2.0, theta=37.0)
        report = coverage.coverage_report(_session(
            _plan(pa=None), [_frame(on), _frame(shifted)]))
        row = _one(report)
        assert (row["stamped"], row["flagged"]) == (2, 1)
        assert row["worst_overlap_frac"] == pytest.approx(0.5, abs=0.01)
        assert row["worst_angle_off_deg"] is None
        control = _one(coverage.coverage_report(_session(
            _plan(pa=None), [_frame(on)])))
        assert control["flagged"] == 0
        assert control["worst_overlap_frac"] == pytest.approx(1.0, abs=1e-4)

    def test_a_negative_angle_is_no_angle(self, tmp_path):
        """-1 is how an "any angle" block is written everywhere else
        (``to_plan._angles``); it is not a position angle to hold a turned
        light to."""
        path = _on_tile_light(tmp_path, rho=37.0)
        row = _one(coverage.coverage_report(_session(
            _plan(pa=-1.0), [_frame(path)])))
        assert row["flagged"] == 0
        assert row["worst_angle_off_deg"] is None

    def test_a_fixed_camera_ignores_a_members_own_rotation(self, tmp_path):
        """Only a rotate group commands its members' angles (``to_plan``
        writes ``rotation_deg`` on a member only for "rotate"); a fixed
        camera is planned at the group's ``pa_deg`` whatever a stale member
        carries. A light shot at the group's 40 is on its tile although the
        member says 90.

        RED under mutation "a fixed camera reads the member's rotation
        too" (``_planned_angle``'s ``(member.rotation_deg, group.pa_deg) if
        group.rotate else (group.pa_deg,)`` -> ``(member.rotation_deg,
        group.pa_deg)``): the plan is laid at 90 and the light was shot at
        40, so ``assert 1 == 0`` (flagged)."""
        path = _on_tile_light(tmp_path, rho=40.0, theta=40.0)
        plan = _plan(pa=40.0, rotate=False, member_rotation=90.0)
        row = _one(coverage.coverage_report(_session(plan, [_frame(path)])))
        assert row["flagged"] == 0
        assert row["worst_angle_off_deg"] == pytest.approx(0.0, abs=0.01)

    def test_a_rotate_member_with_no_angle_falls_back_to_the_groups(
            self, tmp_path):
        """A rotate group whose member carries no angle of its own is
        planned at the group's: a light turned 90 from it is flagged, and
        reads 90 off, not "no angle".

        RED under mutation "no fallback to the group's angle"
        (``candidates = (member.rotation_deg,)`` for a rotate group): the
        panel is laid at the light's own angle, so the turned light covers
        its tile and ``assert 0 == 1`` (flagged)."""
        path = _on_tile_light(tmp_path, rho=130.0, theta=40.0)
        plan = _plan(pa=40.0, rotate=True, member_rotation=None)
        row = _one(coverage.coverage_report(_session(plan, [_frame(path)])))
        assert row["flagged"] == 1
        assert row["worst_angle_off_deg"] == pytest.approx(90.0, abs=0.01)


# ============================================================= the threshold

class TestTheThreshold:
    def test_a_multi_panel_threshold_is_one_minus_half_the_overlap(self):
        assert coverage.on_tile_threshold(0.25, single=False) == pytest.approx(
            0.875)
        assert coverage.on_tile_threshold(0.5, single=False) == pytest.approx(
            0.75)
        assert coverage.on_tile_threshold(0.0, single=False) == 1.0

    def test_a_single_panel_has_the_floor(self):
        """No neighbour to absorb an edge: a lone panel is held to 0.9 whatever
        overlap its block carries (an overlap of 0 would otherwise demand
        1.0, which no real solve meets)."""
        for overlap in (0.0, 0.1, 0.25, 0.5):
            assert coverage.on_tile_threshold(
                overlap, single=True) == coverage.SINGLE_PANEL_ON_TILE == 0.9

    def test_the_same_light_is_flagged_on_a_single_panel_and_not_in_a_grid(
            self, tmp_path):
        """11 percent off: 0.89 on tile. Under a lone panel's 0.9 that is
        flagged; under a 2 x 1 grid's 0.875 (25 percent overlap) it is not.
        Pins both branches of the rule, and the group's own threshold on the
        wire."""
        path = _on_tile_light(tmp_path, dx=FOV_X * 0.11, theta=0.0)
        single = coverage.coverage_report(
            _session(_plan(pa=0.0, rows=1, cols=1), [_frame(path)]))
        grid = coverage.coverage_report(_session(
            _plan(pa=0.0, rows=1, cols=2, panels=((RA, DEC), (RA + 1.0, DEC))),
            [_frame(path)]))
        assert _one(single)["worst_overlap_frac"] == pytest.approx(0.89,
                                                                   abs=0.005)
        assert _one(single)["flagged"] == 1
        assert single["groups"][0]["threshold"] == pytest.approx(0.9)
        assert _one(grid)["flagged"] == 0
        assert grid["groups"][0]["threshold"] == pytest.approx(0.875)

    def test_the_overlap_is_clamped_as_compute_mosaic_clamps_it(self):
        """``compute_mosaic`` takes an overlap of 0 to 0.5 and the threshold
        is read off the overlap the grid was really laid out with.

        RED under mutation "no clamp" (``min(0.5, max(0.0, float(overlap)))``
        -> ``float(overlap)``): ``assert 0.55 == 0.75 +- 7.5e-07`` for an
        overlap of 0.9."""
        assert coverage.on_tile_threshold(0.9, single=False) == pytest.approx(
            0.75)
        assert coverage.on_tile_threshold(-0.3, single=False) == 1.0

    def test_a_light_exactly_on_its_tile_is_not_flagged_by_rounding(
            self, tmp_path):
        """A block with no overlap has a threshold of exactly 1.0, and a
        light exactly on its tile measures 1 - 1e-13 or so (almost every
        angle does: the premise below asserts it for these). Without the
        slack that ideal frame is flagged on rounding.

        RED under mutation "no slack" (``ON_TILE_EPS = 1e-6`` -> ``0.0``):
        ``AssertionError: rho 7.0``, with ``assert (1, 1) == (1, 0)`` (the
        first angle is flagged)."""
        some_below_one = False
        for rho in (7.0, 30.0, 63.0, 100.0, 151.0):
            path = _on_tile_light(tmp_path, f"r{int(rho)}.fits", rho=rho)
            plan = _plan(pa=rho, rows=1, cols=2, overlap=0.0,
                         panels=((RA, DEC), (RA + 1.0, DEC)))
            fp = coverage.frame_footprint(str(path))
            measured = coverage.check_frame(
                fp, ra_hours=RA, dec_deg=DEC, fov_x=FOV_X, fov_y=FOV_Y,
                planned_pa=rho)
            some_below_one = some_below_one or measured.overlap_frac < 1.0
            report = coverage.coverage_report(_session(plan, [_frame(path)]))
            assert report["groups"][0]["threshold"] == 1.0
            row = _one(report)
            assert (row["stamped"], row["flagged"]) == (1, 0), f"rho {rho}"
        assert some_below_one, (
            "premise: an exactly on-tile light measures a hair under 1.0")

    def test_a_group_with_one_member_and_a_grid_is_not_single(self, tmp_path):
        """Single is the GRID's size, not how many panels are left: a 3 x 3
        block with eight panels skipped has neighbours' overlap to spend."""
        plan = _plan(pa=0.0, rows=3, cols=3)
        assert coverage.coverage_report(_session(plan, []))[
            "groups"][0]["threshold"] == pytest.approx(0.875)

    def test_single_falls_back_to_the_members_when_the_grid_is_missing(self):
        plan = _plan(pa=0.0, geometry={"fov_x": FOV_X, "fov_y": FOV_Y,
                                       "overlap": 0.25})
        assert coverage.coverage_report(_session(plan, []))[
            "groups"][0]["threshold"] == pytest.approx(0.9)


# ============================================================== not stamped

class TestUnstamped:
    def test_a_light_with_no_wcs_is_unstamped_never_covered(self, tmp_path):
        """Three banked lights and none solved: ``stamped`` 0, ``unstamped``
        3, nothing flagged, and NO overlap to report: an unverified frame is
        not a covered one.

        RED under mutation "an unstamped frame counted as covered"
        (``_panel_row``'s ``continue`` for a frame with no footprint replaced
        by ``check = FrameCheck(1.0, 0.0, None)``: an overlap of 1.0, counted
        stamped), 6 tests fail, this one with:

            assert 3 == 0                       (stamped)

        and the honest-count test below with ``assert (5, 5, 0, 1) == (5, 2,
        3, 1)``.
        """
        files = [_write(tmp_path / f"u{i}.fits",
                        _header(ra_hours=RA, dec_deg=DEC, stamped=False))
                 for i in range(3)]
        row = _one(coverage.coverage_report(
            _session(_plan(pa=0.0), [_frame(p) for p in files])))
        assert row["frames"] == 3
        assert row["stamped"] == 0
        assert row["unstamped"] == 3
        assert row["flagged"] == 0
        assert row["worst_overlap_frac"] is None
        assert row["worst_offset_arcmin"] is None
        assert row["worst_angle_off_deg"] is None

    def test_stamped_of_banked_is_reported_honestly(self, tmp_path):
        """Five banked, two solved: two stamped, three unstamped, and the two
        are judged."""
        good = _on_tile_light(tmp_path, "g.fits")
        off = _on_tile_light(tmp_path, "o.fits", dx=FOV_X / 2.0)
        bare = [_write(tmp_path / f"u{i}.fits",
                       _header(ra_hours=RA, dec_deg=DEC, stamped=False))
                for i in range(3)]
        row = _one(coverage.coverage_report(_session(
            _plan(pa=0.0),
            [_frame(good), _frame(off)] + [_frame(p) for p in bare])))
        assert (row["frames"], row["stamped"], row["unstamped"],
                row["flagged"]) == (5, 2, 3, 1)

    def test_the_worst_of_several_stamped_lights_is_reported(self, tmp_path):
        """Three solved lights on one panel: on its tile, a quarter of a
        width off and turned 10 degrees, half a width off and turned 30. The
        row carries the WORST of each: the smallest overlap, the largest
        offset and the largest angle error.

        RED under mutation "the worst offset is the least" (``max(worst_offset,
        check.offset_arcmin)`` -> ``min(...)``): ``worst_offset_arcmin`` reads
        0.0, not 53.5; and "the worst angle is the least" likewise."""
        good = _on_tile_light(tmp_path, "good.fits", rho=0.0)
        quarter = _on_tile_light(tmp_path, "quarter.fits", rho=10.0,
                                 dx=FOV_X / 4.0)
        half = _on_tile_light(tmp_path, "half.fits", rho=30.0,
                              dx=FOV_X / 2.0)
        row = _one(coverage.coverage_report(_session(
            _plan(pa=0.0), [_frame(good), _frame(quarter), _frame(half)])))
        assert row["stamped"] == 3
        assert row["worst_overlap_frac"] < 0.5
        assert row["worst_offset_arcmin"] == pytest.approx(
            FOV_X / 2.0 * 60.0, rel=0.01)
        # The light's own axes are measured in the PANEL's tangent plane, so a
        # light shifted half a width east carries the convergence of the
        # meridians between the two tangent points (0.38 degrees here): 30 to
        # within half a degree, not to the hundredth.
        assert row["worst_angle_off_deg"] == pytest.approx(30.0, abs=0.5)

    def test_a_missing_file_a_blank_path_and_a_scale_less_wcs_are_unstamped(
            self, tmp_path):
        """Every way a ledger row has no usable WCS: a path the disk no
        longer holds (NINA saved it on another host, or it was tidied away),
        a row that never had one, and a header that names a projection and a
        reference point but no scale, which astropy would read as one degree
        per pixel and so as a footprint that covers everything."""
        scale_less = _write(tmp_path / "noscale.fits",
                            _header(ra_hours=RA, dec_deg=DEC, scale_less=True))
        row = _one(coverage.coverage_report(_session(_plan(pa=0.0), [
            _frame(tmp_path / "gone.fits"), _frame(""),
            _frame(scale_less)])))
        assert (row["frames"], row["stamped"], row["unstamped"]) == (3, 0, 3)
        assert row["worst_overlap_frac"] is None

    def test_a_garbled_file_is_unstamped_and_never_raises(self, tmp_path):
        junk = tmp_path / "junk.fits"
        junk.write_bytes(b"not a fits file" * 100)
        row = _one(coverage.coverage_report(
            _session(_plan(pa=0.0), [_frame(junk)])))
        assert (row["stamped"], row["unstamped"]) == (0, 1)

    def test_a_path_with_a_nul_byte_is_unstamped_and_never_raises(self):
        """``os.stat`` raises ValueError, not OSError, for a path with an
        embedded NUL; a ledger row is data, and the report is a summary that
        must not 500 on one damaged row.

        RED under mutation "the stat guard only catches OSError"
        (``except (OSError, ValueError):`` -> ``except OSError:`` in
        ``frame_footprint``): ``ValueError: stat: embedded null character in
        path``."""
        bad = "captures" + chr(0) + "light.fits"
        row = _one(coverage.coverage_report(
            _session(_plan(pa=0.0), [_frame(bad)])))
        assert (row["frames"], row["stamped"], row["unstamped"]) == (1, 0, 1)

    def test_a_scale_with_no_projection_is_unstamped(self, tmp_path):
        """CD cards and a reference point but no CTYPE: wcslib reads two
        linear axes, not a sky, and there is no longitude to take the
        footprint's corners from. A header like that is no plate solution.

        RED under mutation "the celestial guard gone" (``if not
        wcs.has_celestial:`` -> ``if False:`` in ``_read_footprint``): the
        corners are read off the wrong axes (``wcs.wcs.lng`` is -1, so both
        coordinates are the last one) and ``frame_footprint`` answers a
        degenerate outline instead of None: ``assert FrameFootprint(...) is
        None``. The report alone would not show it: the outline has no
        area, and the area guard in ``check_frame`` happens to catch it, so
        the guard is graded where it acts."""
        hdr = _header(ra_hours=RA, dec_deg=DEC)
        del hdr["CTYPE1"], hdr["CTYPE2"]
        path = _write(tmp_path / "linear.fits", hdr)
        assert coverage.frame_footprint(str(path)) is None
        row = _one(coverage.coverage_report(_session(
            _plan(pa=0.0), [_frame(path)])))
        assert (row["stamped"], row["unstamped"], row["flagged"]) == (0, 1, 0)

    def test_a_singular_cd_is_unstamped(self, tmp_path):
        """A CD matrix of zeros is a scale of nothing: every corner is the
        reference point, a footprint with no area, and a frame that covers no
        part of any tile is not a covered one."""
        hdr = _header(ra_hours=RA, dec_deg=DEC)
        for key in ("CD1_1", "CD1_2", "CD2_1", "CD2_2"):
            hdr[key] = 0.0
        row = _one(coverage.coverage_report(_session(
            _plan(pa=0.0), [_frame(_write(tmp_path / "zero.fits", hdr))])))
        assert (row["stamped"], row["unstamped"], row["flagged"]) == (0, 1, 0)

    def test_a_footprint_with_a_nan_cannot_be_checked(self):
        """NaN never passes (every comparison with it is false, so a NaN
        footprint would be neither covered nor flagged). A FITS header cannot
        hold one, so the guard is graded where one could arrive: the check
        itself answers ``None``, which the report counts as unstamped."""
        nan = coverage.FrameFootprint(
            corners=((float("nan"), 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)))
        assert coverage.check_frame(
            nan, ra_hours=RA, dec_deg=DEC, fov_x=FOV_X, fov_y=FOV_Y,
            planned_pa=0.0) is None

    def test_a_group_with_no_usable_field_leaves_every_frame_unverified(
            self, tmp_path):
        """A block with no camera field recorded has no footprint to lay a
        light on, so its lights are unverified, not covered."""
        path = _on_tile_light(tmp_path)
        plan = _plan(pa=0.0, geometry={"rows": 1, "cols": 1, "overlap": 0.25})
        row = _one(coverage.coverage_report(_session(plan, [_frame(path)])))
        assert (row["frames"], row["stamped"], row["unstamped"]) == (1, 0, 1)


# ======================================================= which frames count

class TestWhichFramesCount:
    def test_only_effective_lights_of_members_are_walked(self, tmp_path):
        """A rejected frame, a manual reject, a dark and another target's
        frame are not this panel's coverage. A manual accept of an
        auto-rejected frame IS (``effective()``, the rule the ledger counts
        by)."""
        off = _on_tile_light(tmp_path, "off.fits", dx=FOV_X / 2.0)
        frames = [
            _frame(off),                                    # counts
            _frame(off, accepted=False),                    # auto-rejected
            _frame(off, override="reject"),                 # operator reject
            _frame(off, step=DARK_STEP),                    # a dark
            _frame(off, target="somewhere-else"),           # not a member
            _frame(off, accepted=False, override="accept"),  # operator keeps
        ]
        row = _one(coverage.coverage_report(_session(_plan(pa=0.0), frames)))
        assert row["frames"] == 2
        assert row["flagged"] == 2

    def test_every_member_is_listed_with_its_grid_place(self, tmp_path):
        """A panel with no frames (set aside, deferred, not reached) is still
        a row, zeroed, so a map can draw the grid; a skipped panel is not a
        member and has none."""
        plan = _plan(pa=0.0, rows=1, cols=3,
                     panels=((RA, DEC), (RA + 0.1, DEC), (RA + 0.2, DEC)))
        path = _on_tile_light(tmp_path)
        report = coverage.coverage_report(_session(plan, [_frame(path)]))
        panels = report["groups"][0]["panels"]
        assert [(p["target_id"], p["row"], p["col"]) for p in panels] == [
            ("pnl-0", 0, 0), ("pnl-1", 0, 1), ("pnl-2", 0, 2)]
        assert panels[0]["frames"] == 1
        for empty in panels[1:]:
            assert (empty["frames"], empty["stamped"], empty["unstamped"],
                    empty["flagged"]) == (0, 0, 0, 0)
            assert empty["worst_overlap_frac"] is None

    def test_each_panel_is_judged_against_its_own_centre(self, tmp_path):
        """A light that covers panel 0 does not cover panel 1: the same file
        under each target id."""
        plan = _plan(pa=0.0, rows=1, cols=2,
                     panels=((RA, DEC), (RA + 1.0, DEC)))
        path = _on_tile_light(tmp_path)
        report = coverage.coverage_report(_session(plan, [
            _frame(path, target="pnl-0"), _frame(path, target="pnl-1")]))
        a, b = report["groups"][0]["panels"]
        assert a["flagged"] == 0
        assert b["flagged"] == 1
        assert b["worst_overlap_frac"] == 0.0

    def test_a_frame_whose_step_left_the_plan_still_counts(self, tmp_path):
        """A dormant session's plan is editable, so a banked light can name a
        step the plan no longer holds. It was shot for its panel and keeps
        the benefit of the doubt; only a step that IS a dark or a flat drops
        a frame.

        RED under mutation "a vanished step drops its frames" (``if step is
        not None and step.frame_type != "Light":`` -> ``if step is None or
        step.frame_type != "Light":``): ``assert (0, 0) == (1, 1)``."""
        path = _on_tile_light(tmp_path)
        row = _one(coverage.coverage_report(_session(
            _plan(pa=0.0), [_frame(path, step="stp-gone")])))
        assert (row["frames"], row["stamped"]) == (1, 1)

    def test_a_plan_with_no_groups_has_nothing_to_report(self, tmp_path):
        path = _on_tile_light(tmp_path)
        plan = SequencePlan(name="plain", targets=[Target(
            id="t", name="M42", ra_hours=RA, dec_deg=DEC,
            steps=[ExposureStep(id=STEP, exposure_s=60, count=3)])])
        assert coverage.coverage_report(_session(
            plan, [_frame(path, target="t")])) == {"groups": []}

    def test_a_calibration_target_in_a_group_is_not_a_panel(self):
        plan = _plan(pa=0.0)
        plan.targets.append(Target(
            id="flats", name="flats", ra_hours=RA, dec_deg=DEC,
            calibration=True, mosaic_group=GROUP,
            steps=[ExposureStep(id="stp-f", exposure_s=1, count=2,
                                frame_type="Flat")]))
        report = coverage.coverage_report(_session(plan, []))
        assert [p["target_id"] for p in report["groups"][0]["panels"]] == [
            "pnl-0"]


# =========================================================== what it costs

class TestTheMemo:
    def _reads(self, monkeypatch) -> list[str]:
        calls: list[str] = []
        real = coverage.fits.getheader

        def counting(path, *a, **kw):
            calls.append(str(path))
            return real(path, *a, **kw)

        monkeypatch.setattr(coverage.fits, "getheader", counting)
        return calls

    def test_a_header_is_read_once_per_path_and_mtime(self, tmp_path,
                                                      monkeypatch):
        """A night is thousands of header reads, and the modal polls: a second
        report over the same files reads none. A file stamped since (its
        mtime moved) is read again, and a file with no WCS is remembered as
        having none until it changes.

        RED under mutation "no memo" (``frame_footprint``'s ``if hit is not
        _MISSING:`` -> ``if False and hit is not _MISSING:``):

            AssertionError: one read per file, however often it is asked
            assert 4 == 2

        RED under mutation "memo keyed without the mtime" (``key = (path,
        st.st_mtime_ns, st.st_size)`` -> ``key = (path, 0, 0)``): the stamped
        file is never read again, and

            AssertionError: only the changed file is read again
            assert 2 == 3
        """
        solved = _on_tile_light(tmp_path, "s.fits")
        bare = _write(tmp_path / "b.fits",
                      _header(ra_hours=RA, dec_deg=DEC, stamped=False))
        session = _session(_plan(pa=0.0), [_frame(solved), _frame(bare)])
        calls = self._reads(monkeypatch)
        first = coverage.coverage_report(session)
        second = coverage.coverage_report(session)
        assert first == second
        assert len(calls) == 2, "one read per file, however often it is asked"

        # The bare file is stamped now: same path, a later mtime.
        _write(bare, _header(ra_hours=RA, dec_deg=DEC))
        st = os.stat(bare)
        os.utime(bare, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        third = _one(coverage.coverage_report(session))
        assert len(calls) == 3, "only the changed file is read again"
        assert (third["stamped"], third["unstamped"]) == (2, 0)

    def test_concurrent_polls_read_a_cold_header_once(self, tmp_path,
                                                      monkeypatch):
        """The first report of a night is seconds of header reads and the modal
        polls on a timer, so a second poll arrives while the first is still
        reading. The first reader is held INSIDE the header read; a second
        caller for the same file must wait for it and find what it read, not
        start a read of its own. (The second is given 0.3 s to be wrong; the
        right answer takes no time at all and the assertion is on the count.)

        RED under mutation "concurrent misses both read" (the second lookup
        under ``_READ_LOCK`` removed, so the lock only serialises):

            AssertionError: a cold file is read once, not once per poller
            assert 2 == 1
        """
        path = str(_on_tile_light(tmp_path))
        entered, release = threading.Event(), threading.Event()
        calls: list[str] = []
        real = coverage.fits.getheader

        def slow(p, *a, **kw):
            calls.append(str(p))
            entered.set()
            assert release.wait(10.0), "premise: the first reader is released"
            return real(p, *a, **kw)

        monkeypatch.setattr(coverage.fits, "getheader", slow)
        results: list = []
        first = threading.Thread(
            target=lambda: results.append(coverage.frame_footprint(path)))
        second = threading.Thread(
            target=lambda: results.append(coverage.frame_footprint(path)))
        first.start()
        assert entered.wait(10.0), "premise: the first reader is mid-read"
        second.start()
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline and len(calls) < 2:
            time.sleep(0.005)
        release.set()
        first.join(10.0)
        second.join(10.0)
        assert not first.is_alive() and not second.is_alive()
        assert len(calls) == 1, "a cold file is read once, not once per poller"
        assert len(results) == 2 and results[0] is not None
        assert results[0] == results[1]

    def test_a_stamp_that_grows_the_header_is_read_again_at_the_same_mtime(
            self, tmp_path):
        """The size is in the key beside the mtime: a stamp that lands inside
        one timestamp tick of the read that found no WCS (a coarse clock, a
        network share) still moves the file's size when it spills the header
        into another 2880-byte block, and is read again.

        RED under mutation "the memo keyed without the size" (``key = (path,
        st.st_mtime_ns, st.st_size)`` -> ``key = (path, st.st_mtime_ns,
        0)``): the bare read is served for the stamped file and
        ``assert None is not None`` fails."""
        path = tmp_path / "grow.fits"
        _write(path, _header(ra_hours=RA, dec_deg=DEC, stamped=False))
        assert coverage.frame_footprint(str(path)) is None, "premise: bare"
        before = os.stat(path)
        stamped = _header(ra_hours=RA, dec_deg=DEC)
        for i in range(40):
            stamped.add_comment(f"filler {i}")
        _write(path, stamped)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        after = os.stat(path)
        assert after.st_mtime_ns == before.st_mtime_ns, (
            "premise: the stamp landed in the same tick")
        assert after.st_size > before.st_size, "premise: the header grew"
        assert coverage.frame_footprint(str(path)) is not None

    def test_the_memo_is_bounded(self, tmp_path, monkeypatch):
        monkeypatch.setattr(coverage, "MEMO_MAX", 10)
        for i in range(25):
            coverage.frame_footprint(str(_on_tile_light(tmp_path, f"{i}.fits")))
        assert coverage.memo_size() <= 10

    def test_pixels_are_never_read(self, tmp_path):
        """The files here are a header and nothing else, so the 25 MB of data
        the header promises is absent. Premise: reading the pixels of such a
        file does not give an image; the verifier still answers."""
        path = _on_tile_light(tmp_path)
        with pytest.raises(Exception):
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                fits.getdata(path)
        assert coverage.frame_footprint(str(path)) is not None


# ================================================== the real stamp, read back

class TestTheStampTheHubWrites:
    def test_a_light_stamped_by_write_wcs_is_read_back(self, tmp_path):
        """The reader against the WRITER: a small real FITS (pixels and all),
        stamped in place by ``fitsio.write_wcs`` from a ``WcsSolution``, which
        is what ``Hub._solve_and_stamp`` does to every saved light. A fixture
        written by the test would prove the reader reads the test's header;
        this proves it reads the production one (CD matrix, EQUINOX, RADESYS).
        The panel is the frame's own field, so the stamped light covers it
        and is not flagged, and a stamp half a field away is."""
        nx, ny, scale = 64, 48, SCALE
        fov = (nx * scale / 3600.0, ny * scale / 3600.0)
        s = scale / 3600.0

        def stamped(name: str, ra_hours: float, dec_deg: float):
            path = tmp_path / name
            fits.PrimaryHDU(np.zeros((ny, nx), dtype=np.uint16)).writeto(path)
            write_wcs(path, WcsSolution(
                crval1=ra_hours * 15.0, crval2=dec_deg,
                crpix1=(nx + 1) / 2.0, crpix2=(ny + 1) / 2.0,
                cd11=-s, cd12=0.0, cd21=0.0, cd22=s))
            return path

        on = stamped("on.fits", RA, DEC)
        sh_ra, sh_dec = _at(fov[0] / 2.0, 0.0, 0.0)
        off = stamped("off.fits", sh_ra, sh_dec)
        bare = tmp_path / "bare.fits"
        fits.PrimaryHDU(np.zeros((ny, nx), dtype=np.uint16)).writeto(bare)
        plan = _plan(pa=0.0, geometry={"rows": 1, "cols": 1, "overlap": 0.25,
                                       "fov_x": fov[0], "fov_y": fov[1]})
        row = _one(coverage.coverage_report(
            _session(plan, [_frame(on), _frame(off), _frame(bare)])))
        assert (row["frames"], row["stamped"], row["unstamped"],
                row["flagged"]) == (3, 2, 1, 1)
        assert row["worst_overlap_frac"] == pytest.approx(0.5, abs=0.01)


class TestAStampThatCarriesOnlyCdelt:
    """``fitsio._apply_wcs`` writes a CD matrix OR, when the solver gave no
    CD, CDELT1/CDELT2 and CROTA2 (the ``.ini`` fallback of ``astap``). Both
    are plate solutions, and astropy reads the second through its own CROTA2
    convention, which makes this the INDEPENDENT check on the angle the
    hand-built CD fixtures of this file assume."""

    @pytest.mark.parametrize("rho", [0.0, 30.0, 123.0])
    def test_a_cdelt_and_crota2_stamp_is_read_at_its_angle(self, tmp_path, rho):
        """A light stamped by ``write_wcs`` with CDELT and CROTA2 ``rho`` is
        on a panel planned at ``rho`` (covered, 0 degrees off) and is turned
        a quarter from one planned at ``rho + 90`` (a 64 x 48 field shares
        the 0.75 square: flagged, 90 off).

        RED under mutation "the CDELT scale not recognised" (``_has_scale``'s
        ``elif "CDELT1" in header:`` -> ``elif False:``): the light is
        unstamped and ``assert (1, 0, 1) == (1, 1, 0)``."""
        nx, ny, scale = 64, 48, SCALE
        s = scale / 3600.0
        fov = (nx * s, ny * s)
        path = tmp_path / "cdelt.fits"
        fits.PrimaryHDU(np.zeros((ny, nx), dtype=np.uint16)).writeto(path)
        write_wcs(path, WcsSolution(
            crval1=RA * 15.0, crval2=DEC, crpix1=(nx + 1) / 2.0,
            crpix2=(ny + 1) / 2.0, cdelt1=-s, cdelt2=s, crota2=rho))
        geometry = {"rows": 1, "cols": 1, "overlap": 0.25,
                    "fov_x": fov[0], "fov_y": fov[1]}
        right = _one(coverage.coverage_report(_session(
            _plan(pa=rho, geometry=geometry), [_frame(path)])))
        assert (right["frames"], right["stamped"], right["unstamped"]) == (
            1, 1, 0)
        assert right["flagged"] == 0
        assert right["worst_overlap_frac"] == pytest.approx(1.0, abs=1e-4)
        assert right["worst_angle_off_deg"] == pytest.approx(0.0, abs=0.01)
        turned = _one(coverage.coverage_report(_session(
            _plan(pa=(rho + 90.0) % 360.0, geometry=geometry),
            [_frame(path)])))
        assert turned["flagged"] == 1
        assert turned["worst_overlap_frac"] == pytest.approx(0.75, abs=0.005)
        assert turned["worst_angle_off_deg"] == pytest.approx(90.0, abs=0.01)


# ====================================================== the run-start note

class TestTheStampingNote:
    def test_a_mosaic_with_solving_off_cannot_be_checked(self):
        plan = _plan(pa=0.0)
        assert coverage.stamping_note(plan, False) == (
            "coverage cannot be checked: frames are not being plate-solved")

    def test_solving_on_has_no_note(self):
        assert coverage.stamping_note(_plan(pa=0.0), True) is None

    def test_a_group_of_only_calibration_targets_is_no_mosaic(self):
        """A group whose members are all calibration targets has no panel
        to cover, so a run of it has nothing to say it cannot check.

        RED under mutation "a calibration target counted as a panel"
        (``not t.calibration`` dropped from ``stamping_note``): the sentence
        is answered for a plan with no mosaic."""
        plan = _plan(pa=0.0)
        plan.targets[0].calibration = True
        assert coverage.stamping_note(plan, False) is None

    def test_no_mosaic_has_no_note(self):
        plain = SequencePlan(name="plain", targets=[Target(
            id="t", name="M42", ra_hours=RA, dec_deg=DEC,
            steps=[ExposureStep(id=STEP, exposure_s=60, count=3)])])
        assert coverage.stamping_note(plain, False) is None


# ================================================================ the wire

class TestTheAnswerCarriesNoSky:
    """What a viewer may read (spec 6.9, #19): fractions, counts and offsets
    in arcminutes. No RA or Dec of a frame, nothing site-derived. The route's
    test holds the same list at the wire."""

    GROUP_KEYS = {"group_id", "name", "threshold", "panels"}
    PANEL_KEYS = {"target_id", "name", "row", "col", "frames", "stamped",
                  "unstamped", "flagged", "worst_overlap_frac",
                  "worst_offset_arcmin", "worst_angle_off_deg"}

    def test_every_key_is_on_the_list(self, tmp_path):
        path = _on_tile_light(tmp_path, dx=FOV_X / 2.0)
        report = coverage.coverage_report(
            _session(_plan(pa=0.0), [_frame(path)]))
        assert set(report) == {"groups"}
        for group in report["groups"]:
            assert set(group) == self.GROUP_KEYS
            for panel in group["panels"]:
                assert set(panel) == self.PANEL_KEYS

    def test_the_answer_is_plain_json(self, tmp_path):
        path = _on_tile_light(tmp_path, dx=FOV_X / 2.0)
        report = coverage.coverage_report(
            _session(_plan(pa=0.0), [_frame(path)]))
        assert json.loads(json.dumps(report, allow_nan=False)) == report

    def test_the_report_does_not_move_the_session(self, tmp_path):
        """Derived on demand and written nowhere: the ledger a report is
        made from is byte-identical afterwards."""
        path = _on_tile_light(tmp_path, dx=FOV_X / 2.0)
        session = _session(_plan(pa=0.0), [_frame(path)])
        before = session.model_dump_json()
        coverage.coverage_report(session)
        assert session.model_dump_json() == before
