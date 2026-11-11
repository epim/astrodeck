# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP 10.5 (#1227): the simulated camera and the simulated solver describe the
same sky.

The camera renders a field and the solver stamps where it points. A sim frame's
pixels and its solution used to contradict each other: half a turn apart, at
two different image scales, with the rotator ignored by the camera. What is
graded, each by a round trip a half-built fix cannot pass:

* ORIENTATION AND ROTATION. A star at a known sky position is rendered at a
  rotator angle, the frame is saved and solved, and the WCS the solver stamped
  must put the star's pixels on that sky position. At angle 0 this fails by a
  half turn; at any other angle a camera that ignores the rotator fails too.
* THE ANGLE READ BACK. The solve reports the angle the camera was clocked at:
  the rotator's mechanical angle plus the camera's clocking on it.
* SCALE. The same round trip at a star 0.1 degrees from the centre fails by
  tens of pixels when the camera and the solver use different plate scales.
* BINNING. A binned frame covers the same sky in fewer, larger pixels, so the
  solver's scale is the unbinned scale times the binning, and the star lands
  where that WCS says.
* DETERMINISM. Two renders of the same pointing and angle are byte-identical:
  the angle is derived from the rig, never from a clock.

Mutant (a byte backup in the scratch directory, restored byte-identically):
``M1-half-turn``  the camera renders at the rotator's angle plus 180 degrees.
    ``test_a_stars_pixels_sit_where_the_solved_wcs_puts_its_sky_position``
    fails at every angle: the star is on the far side of the centre.
"""
import math

import numpy as np
import pytest
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.wcs import WCS

from astrodeck.devices.base import CameraFrame
from astrodeck.devices.sim import SimCamera, SimRig
from astrodeck.imaging.fitsio import save_fits, stamp_wcs
from astrodeck.solve.simsolver import SimSolver

RA0, DEC0 = 5.5, 41.2            # a made-up pointing, hours and degrees
# The star's offset from the pointing is 0.1 degrees, so it stays on the sensor
# at every rotator angle, binned or not.
EAST_DEG, NORTH_DEG = 0.08, 0.06
# The plate scale of an unbinned sim frame: the 1.4 degrees the camera has
# always drawn across its 1216 pixels, written out here and not imported so
# that a changed constant is a failing test.
SCALE_ARCSEC = 1.4 * 3600.0 / 1216


def _rig_at(rotator_mech: float, pa_offset: float = 0.0) -> SimRig:
    rig = SimRig()
    rig._ra_hours, rig.dec_deg = RA0, DEC0
    rig.rotator_mech_deg = rotator_mech
    rig.rotator_pa_offset_deg = pa_offset
    return rig


def _star_position() -> tuple[float, float]:
    """The star's sky position, degrees: east and north of the pointing."""
    return (RA0 * 15.0 + EAST_DEG / math.cos(math.radians(DEC0)),
            DEC0 + NORTH_DEG)


def _render_one_star(rig: SimRig, binning: int) -> np.ndarray:
    """The sim camera's own star renderer, holding ONE star at
    ``_star_position`` and nothing else."""
    cam = SimCamera(rig)
    star_ra, star_dec = _star_position()
    tile = (math.floor(star_ra / 0.5), math.floor((star_dec + 90.0) / 0.5))
    mine = np.array([[star_ra, star_dec, 8.0]])
    cam._stars_for_tile = (lambda tx, ty: mine if (tx, ty) == tile
                           else np.empty((0, 3)))
    h, w = cam.sensor_height // binning, cam.sensor_width // binning
    return cam._render_stars(h, w, 60.0, 100, binning, np.random.default_rng(1))


def _centroid(field: np.ndarray) -> tuple[float, float]:
    """The star's (row, col) as a flux-weighted centroid around the brightest
    pixel, sub-pixel."""
    row, col = np.unravel_index(int(np.argmax(field)), field.shape)
    r0, c0 = max(0, row - 8), max(0, col - 8)
    win = field[r0:row + 9, c0:col + 9]
    rows, cols = np.mgrid[r0:r0 + win.shape[0], c0:c0 + win.shape[1]]
    return float((rows * win).sum() / win.sum()), float((cols * win).sum() / win.sum())


async def _solved_wcs(rig: SimRig, field: np.ndarray, binning: int, tmp_path):
    """Save the frame the way a light is saved, solve it with the sim solver,
    stamp the solution, and read it back as an astropy WCS."""
    data = np.clip(field + 100.0, 0, 65535).astype(np.uint16)
    frame = CameraFrame(data=data, exposure_s=60.0, gain=100, offset=30,
                        binning=binning, bayer_pattern=None, temperature_c=0.0,
                        timestamp=0.0)
    path = save_fits(frame, tmp_path / "sim.fits")
    result = await SimSolver(rig, mode="sim").solve(path)
    assert result.success and result.wcs is not None
    assert stamp_wcs(path, result.wcs)
    return result, WCS(fits.getheader(path))


@pytest.mark.parametrize("rotator_mech,pa_offset", [
    (0.0, 0.0), (30.0, 0.0), (135.0, 0.0), (200.0, 0.0), (300.0, 0.0),
    (50.0, 12.0)])
async def test_a_stars_pixels_sit_where_the_solved_wcs_puts_its_sky_position(
        rotator_mech, pa_offset, tmp_path):
    rig = _rig_at(rotator_mech, pa_offset)
    field = _render_one_star(rig, 1)
    _result, wcs = await _solved_wcs(rig, field, 1, tmp_path)
    row, col = _centroid(field)
    got = SkyCoord(*wcs.wcs_pix2world(col, row, 0), unit="deg")
    truth = SkyCoord(*_star_position(), unit="deg")
    miss_px = got.separation(truth).arcsec / SCALE_ARCSEC
    assert miss_px < 0.5, (
        f"the solver's WCS puts the star the camera drew {miss_px:.1f} px from "
        f"its sky position, at rotator {rotator_mech} + clocking {pa_offset}")


@pytest.mark.parametrize("rotator_mech,pa_offset", [(0.0, 0.0), (135.0, 0.0),
                                                    (50.0, 12.0)])
async def test_the_solve_reads_back_the_angle_the_camera_was_clocked_at(
        rotator_mech, pa_offset, tmp_path):
    rig = _rig_at(rotator_mech, pa_offset)
    result, wcs = await _solved_wcs(rig, _render_one_star(rig, 1), 1, tmp_path)
    angle = (rotator_mech + pa_offset) % 360.0
    assert result.rotation_deg == pytest.approx(angle)
    # The angle the WCS itself carries: north's direction on the sensor. The
    # CD matrix of a CROTA2 solution turns the pixel axes by that angle.
    cd = wcs.pixel_scale_matrix
    measured = math.degrees(math.atan2(-cd[0][1], cd[1][1])) % 360.0
    assert measured == pytest.approx(angle, abs=1e-6)


async def test_a_binned_frame_is_the_same_sky_in_larger_pixels(tmp_path):
    rig = _rig_at(90.0)
    field = _render_one_star(rig, 2)
    assert field.shape == (456, 608)
    result, wcs = await _solved_wcs(rig, field, 2, tmp_path)
    assert result.pixel_scale_arcsec == pytest.approx(2 * SCALE_ARCSEC)
    row, col = _centroid(field)
    got = SkyCoord(*wcs.wcs_pix2world(col, row, 0), unit="deg")
    truth = SkyCoord(*_star_position(), unit="deg")
    miss_px = got.separation(truth).arcsec / (2 * SCALE_ARCSEC)
    assert miss_px < 0.5, f"the binned star is {miss_px:.1f} binned px off"


async def test_an_unbinned_solve_states_the_unbinned_scale(tmp_path):
    rig = _rig_at(0.0)
    result, _wcs = await _solved_wcs(rig, _render_one_star(rig, 1), 1, tmp_path)
    assert result.pixel_scale_arcsec == pytest.approx(SCALE_ARCSEC)


def test_two_renders_at_one_pointing_and_angle_are_identical():
    first = _render_one_star(_rig_at(77.0, 3.0), 1)
    again = _render_one_star(_rig_at(77.0, 3.0), 1)
    assert np.array_equal(first, again)


@pytest.mark.parametrize("binning,shift", [(1, (0.0, 0.0)), (2, (0.0, 0.0)),
                                           (1, (0.6, 0.45))])
def test_the_fixed_rich_field_lands_on_the_pixels_it_names(binning, shift):
    """The fixture the two autofocus tests install draws each star where its
    pixel is named (centroid within 0.3 px), at either binning, and a shift
    moves every star by that much; without this a 'shifted' field could be the
    same field."""
    from _w10_5_rich_field import _pixel_positions, install_rich_field
    rig = _rig_at(0.0)
    cam = SimCamera(rig)
    install_rich_field(cam, shift_px=shift)
    h, w = cam.sensor_height // binning, cam.sensor_width // binning
    field = cam._render_stars(h, w, 5.0, 100, binning, np.random.default_rng(1))
    stars = _pixel_positions(cam.sensor_width, cam.sensor_height)
    checked = 0
    for col, row, mag in stars[np.argsort(stars[:, 2])][:120]:
        gap = np.hypot(stars[:, 0] - col, stars[:, 1] - row)
        if np.sort(gap)[1] < 25:
            continue                     # a neighbour would pull the centroid
        want_c = (col + shift[0] - (cam.sensor_width - 1) / 2) / binning + (w - 1) / 2
        want_r = (row + shift[1] - (cam.sensor_height - 1) / 2) / binning + (h - 1) / 2
        r0, c0 = int(round(want_r)), int(round(want_c))
        win = field[r0 - 6:r0 + 7, c0 - 6:c0 + 7]
        rows, cols = np.mgrid[r0 - 6:r0 + 7, c0 - 6:c0 + 7]
        got_r = float((rows * win).sum() / win.sum())
        got_c = float((cols * win).sum() / win.sum())
        assert abs(got_r - want_r) < 0.3 and abs(got_c - want_c) < 0.3, (
            f"star mag {mag:.1f} named at ({want_c:.2f}, {want_r:.2f}) drawn "
            f"at ({got_c:.2f}, {got_r:.2f})")
        checked += 1
    assert checked >= 5
