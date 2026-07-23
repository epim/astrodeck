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


def test_format_dec_fits_space_separated_signed():
    assert format_dec_fits(-5.3911) == "-05 23 28"
    assert format_dec_fits(5.3911) == "+05 23 28"
