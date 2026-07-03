"""FITS header correctness (save_fits)."""
import re

import numpy as np
from astropy.io import fits
from astropy.time import Time

from astrodeck.devices.base import CameraFrame
from astrodeck.imaging.fitsio import save_fits

# FITS 4.0 sec 4.4.2: 'YYYY-MM-DDThh:mm:ss[.s...]' with NO timezone designator.
_DATE_OBS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$")


def _frame(timestamp: float = 1_772_775_791.123456) -> CameraFrame:
    return CameraFrame(
        data=np.zeros((16, 16), dtype=np.uint16),
        exposure_s=1.0,
        gain=100,
        offset=10,
        binning=1,
        bayer_pattern=None,
        temperature_c=-10.0,
        timestamp=timestamp,
    )


def test_date_obs_has_no_timezone_suffix(tmp_path):
    path = save_fits(_frame(), tmp_path / "light.fits")
    with fits.open(path) as hdul:
        date_obs = hdul[0].header["DATE-OBS"]
    assert "+" not in date_obs and "Z" not in date_obs
    assert _DATE_OBS_RE.match(date_obs), date_obs


def test_date_obs_parses_as_fits_time(tmp_path):
    # astropy's strict FITS-format Time parser rejects a timezone-suffixed
    # DATE-OBS -- this is exactly what downstream tools (plate-solvers,
    # session sorters) use, and what the malformed header used to break.
    path = save_fits(_frame(), tmp_path / "light.fits")
    with fits.open(path) as hdul:
        date_obs = hdul[0].header["DATE-OBS"]
    t = Time(date_obs, format="fits")
    assert t.isot.startswith("2026-03-")
