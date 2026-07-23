"""Kasten-Young airmass + FITS-convention sexagesimal formatters (PRO-2 F-B)."""
import pytest

from astrodeck.catalog.coords import airmass, format_dec_fits, format_ra_fits


def test_airmass_zenith_is_one():
    assert airmass(90.0) == pytest.approx(1.0, abs=0.01)


def test_airmass_thirty_degrees_near_two():
    # sec(60 deg) = 2.0; Kasten-Young is a touch under
    assert 1.9 < airmass(30.0) < 2.05


def test_airmass_none_at_or_below_horizon():
    assert airmass(0.0) is None
    assert airmass(-5.0) is None


def test_format_ra_fits_space_separated():
    assert format_ra_fits(5.5883) == "05 35 17.9"


def test_format_ra_fits_seconds_rollover_carries():
    # 05:35:59.97 must carry into 05 36 00.0, never render the invalid "60.0".
    assert format_ra_fits(5.0 + 35.0 / 60.0 + 59.97 / 3600.0) == "05 36 00.0"


def test_format_ra_fits_wraps_24h_not_to_24():
    # A value rounding up through 24h wraps to 00, never "24 00 00.0".
    assert format_ra_fits(23.0 + 59.0 / 60.0 + 59.97 / 3600.0) == "00 00 00.0"
    assert format_ra_fits(0.0) == "00 00 00.0"


def test_format_dec_fits_space_separated_signed():
    assert format_dec_fits(-5.3911) == "-05 23 28"
    assert format_dec_fits(5.3911) == "+05 23 28"
