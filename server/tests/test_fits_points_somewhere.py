"""A saved frame says which way the telescope was facing (#23).

Altitude per frame is what separates a tree, a cloud bank and plain extinction
when the star count drops - and altitude ALONE cannot, because all three are
"low". The azimuth was being computed beside it in `_frame_meta` and assigned
to `_az`, a discarded local.

`SITELAT`/`SITELONG`/`SITEELEV` and `OBJCTALT`/`AIRMASS` were already written
(guarded on a non-default site), and the issue's privacy caution is already
answered deliberately: every gallery route is authenticated and FITS download
needs `view.media` precisely because these cards disclose the site. Nothing
here changes that; `CENTAZ` is derived from the same coordinates and inherits
the same gate.

These drive the real writer against a real FITS file, because the whole
question is which cards come out the other end.
"""
from __future__ import annotations

import numpy as np
import pytest

from astrodeck.imaging.fitsio import FrameMeta, save_fits
from astrodeck.devices.base import CameraFrame


def _frame() -> CameraFrame:
    return CameraFrame(data=np.zeros((4, 4), dtype=np.uint16),
                       exposure_s=1.0, gain=100, offset=10, binning=1,
                       bayer_pattern="", temperature_c=None,
                       timestamp=1_757_000_000.0)


def _header(tmp_path, **meta):
    from astropy.io import fits
    path = tmp_path / "f.fits"
    save_fits(_frame(), path, meta=FrameMeta(**meta))
    with fits.open(path) as hdul:
        return dict(hdul[0].header)


def test_the_azimuth_reaches_the_file(tmp_path):
    """The gap. Nothing wrote the direction the scope was facing.

    MUTATION: delete the `CENTAZ` card. Observed: this fails with the key
    missing, and a reader is back to altitude alone.
    """
    hdr = _header(tmp_path, obj_alt_deg=41.5, obj_az_deg=118.25)
    assert hdr.get("CENTAZ") == pytest.approx(118.25), (
        f"no azimuth in the saved frame: {sorted(hdr)}")


def test_the_altitude_is_written_under_both_names(tmp_path):
    """`OBJCTALT` is what this repo's own readers use; `CENTALT` is what NINA
    writes and what most downstream tooling greps for. Both, because a
    duplicated card is cheaper than an analysis that cannot find the number.

    MUTATION: drop the `CENTALT` line. Observed: this fails while the OBJCTALT
    assertion still passes, which is the half a NINA-shaped reader needs.
    """
    hdr = _header(tmp_path, obj_alt_deg=41.5)
    assert hdr.get("OBJCTALT") == pytest.approx(41.5)
    assert hdr.get("CENTALT") == pytest.approx(41.5)


def test_a_frame_with_no_pointing_carries_no_pointing_cards(tmp_path):
    """Omit, never placeholder - the writer's own rule (spec section 8). A
    dark has no sky position, and a zero would be a lie a reader cannot detect.

    MUTATION: write `CENTAZ` unconditionally with `float(m.obj_az_deg or 0)`.
    Observed: a dark comes out claiming due north and this fails.
    """
    hdr = _header(tmp_path)
    for card in ("CENTAZ", "CENTALT", "OBJCTALT", "AIRMASS", "SITELAT", "SITELONG"):
        assert card not in hdr, f"{card} was written for a frame with no pointing"


def test_the_site_cards_still_ride_along_when_there_is_a_site(tmp_path):
    """Not a change - they were already written - but this file is where a
    reader will look for what a frame discloses, and an assertion is a better
    answer than a paragraph.

    The values are an arbitrary real place; the gating that stops these
    reaching anyone without `view.media` lives in the gallery routes.
    """
    hdr = _header(tmp_path, site_lat_deg=40.0, site_lon_deg=-74.0,
                  site_elev_m=17.0)
    assert hdr.get("SITELAT") == pytest.approx(40.0)
    assert hdr.get("SITELONG") == pytest.approx(-74.0)
    assert hdr.get("SITEELEV") == pytest.approx(17.0)


def test_an_azimuth_of_zero_is_written_and_not_swallowed(tmp_path):
    """Due north is a real azimuth. A `if m.obj_az_deg:` test would drop it,
    which is the falsy-zero mistake this codebase has made before with a
    rotator position angle.

    MUTATION: change `_finite(m.obj_az_deg)` to `m.obj_az_deg`. Observed: the
    card vanishes and this fails.
    """
    hdr = _header(tmp_path, obj_alt_deg=10.0, obj_az_deg=0.0)
    assert "CENTAZ" in hdr, "an azimuth of exactly 0 (due north) was dropped"
    assert hdr["CENTAZ"] == pytest.approx(0.0)
