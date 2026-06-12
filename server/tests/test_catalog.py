"""Catalog search and coordinate math."""
from astrodeck.catalog import (altaz, format_dec, format_ra, parse_dec,
                               parse_ra, search_catalog)


def test_search_by_id_and_name():
    assert search_catalog("M42")[0]["id"] == "M42"
    hits = search_catalog("andromeda")
    assert hits and hits[0]["id"] == "M31"
    assert search_catalog("galaxy")  # type search works


def test_parse_ra_formats():
    assert abs(parse_ra("5h 35m 17s") - 5.58806) < 1e-3
    assert abs(parse_ra("05:35:17") - 5.58806) < 1e-3
    assert abs(parse_ra("5.5881") - 5.5881) < 1e-9


def test_parse_dec_formats():
    assert abs(parse_dec("-05:23:28") - (-5.39111)) < 1e-3
    assert abs(parse_dec("-5° 23' 28\"") - (-5.39111)) < 1e-3
    assert abs(parse_dec("+41.269") - 41.269) < 1e-9


def test_format_roundtrip():
    assert abs(parse_ra(format_ra(5.5881)) - 5.5881) < 1e-3
    assert abs(parse_dec(format_dec(-5.391)) - (-5.391)) < 1e-3


def test_altaz_sane():
    # Polaris-ish: altitude should be near the site latitude from anywhere north
    alt, az = altaz(2.53, 89.26, lat_deg=37.77, lon_deg=-122.42)
    assert abs(alt - 37.77) < 2.0
    assert 0 <= az < 360
