# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A rich, fixed star field for the sim camera (WP 10.5, #1227).

Two autofocus tests used to rest on the sim camera's default catalog, whose
frame holds only a dozen stars bright enough to survive a defocused sweep: a
sweep's verdict hung on the last few of them, and moving every star by a third
of a pixel (on unmodified main) turned one of the tests red. They now install
this field instead. It is part of each test's fixture:

* ``N_STARS`` stars at fixed pixel positions of the UNBINNED frame (a seeded
  generator, so the same on every machine), kept ``EDGE_PX`` pixels inside the
  sensor so a defocused star is never half off the frame;
* magnitudes from ``MAG_BRIGHT`` to ``MAG_FAINT``, many faint and some bright,
  so the sweep's best point resolves well over the 50 stars of a rich field and
  its wings, bloated to many pixels, still show dozens;
* placed by the sim's own sky projection (the camera's tangent plane and CD
  matrix) from the rig's pointing, so the camera draws each star on the pixel
  asked for, whatever the binning.

``shift_px`` moves the whole field by a fraction of a pixel; the tests must not
care, and that is the property the catalog fixture lacked.
"""
import math

import numpy as np

from astrodeck.devices.sim import (SIM_PIXEL_SCALE_ARCSEC, sim_camera_sky_pa_deg,
                                   sim_cd_matrix)

N_STARS = 400
EDGE_PX = 80
MAG_BRIGHT = 3.0
MAG_FAINT = 9.0
SEED = 20261111


def _pixel_positions(width: int, height: int) -> np.ndarray:
    rng = np.random.default_rng(SEED)
    cols = rng.uniform(EDGE_PX, width - EDGE_PX, N_STARS)
    rows = rng.uniform(EDGE_PX, height - EDGE_PX, N_STARS)
    mags = MAG_BRIGHT + (MAG_FAINT - MAG_BRIGHT) * rng.power(1.5, N_STARS)
    return np.column_stack([cols, rows, mags])


def _sky_of_pixel(cam, col: float, row: float) -> tuple[float, float]:
    """The sky position (ra_deg, dec_deg) the camera draws at unbinned pixel
    (``col``, ``row``): the CD matrix out to the tangent plane, then the
    inverse TAN projection about the rig's pointing."""
    rig = cam.rig
    cd11, cd12, cd21, cd22 = sim_cd_matrix(SIM_PIXEL_SCALE_ARCSEC,
                                           sim_camera_sky_pa_deg(rig))
    dx = col - (cam.sensor_width - 1) / 2.0
    dy = row - (cam.sensor_height - 1) / 2.0
    xi = math.radians(cd11 * dx + cd12 * dy)
    eta = math.radians(cd21 * dx + cd22 * dy)
    ra0 = math.radians(rig.ra_hours * 15.0)
    dec0 = math.radians(rig.dec_deg)
    denom = math.cos(dec0) - eta * math.sin(dec0)
    ra = ra0 + math.atan2(xi, denom)
    dec = math.atan2((math.sin(dec0) + eta * math.cos(dec0)) * math.cos(ra - ra0),
                     denom)
    return math.degrees(ra), math.degrees(dec)


def install_rich_field(cam, shift_px: tuple[float, float] = (0.0, 0.0)) -> int:
    """Make ``cam`` draw the fixed rich field and nothing else, around the
    rig's pointing as it is now. Returns the number of stars installed."""
    tiles: dict[tuple[int, int], list[list[float]]] = {}
    for col, row, mag in _pixel_positions(cam.sensor_width, cam.sensor_height):
        ra, dec = _sky_of_pixel(cam, col + shift_px[0], row + shift_px[1])
        key = (math.floor(ra / 0.5), math.floor((dec + 90.0) / 0.5))
        tiles.setdefault(key, []).append([ra, dec, mag])
    arrays = {k: np.array(v) for k, v in tiles.items()}
    cam._stars_for_tile = lambda tx, ty: arrays.get((tx, ty), np.empty((0, 3)))
    return N_STARS
