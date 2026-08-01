"""Catalog search and coordinate math."""
import pytest

from astrodeck.catalog import (CATALOG, altaz, format_dec, format_ra,
                               parse_dec, parse_ra, search_catalog)


def _ids(query):
    return [r["id"] for r in search_catalog(query)]


def test_search_by_id_and_name():
    assert search_catalog("M42")[0]["id"] == "M42"
    hits = search_catalog("andromeda")
    assert hits and hits[0]["id"] == "M31"
    assert search_catalog("galaxy")  # type search works


# A designation is conventionally written spaced ("M 31", "NGC 3372") but the
# ids here are stored inconsistently, so a plain substring test used to fail one
# convention or the other in BOTH directions — including the exact example the
# Atlas placeholder offers. Spacing and case must be irrelevant.
@pytest.mark.parametrize("query,expected", [
    ("M 31", "M31"), ("m 31", "M31"), ("m31", "M31"), ("M31", "M31"),
    ("NGC 3372", "NGC 3372"), ("ngc3372", "NGC 3372"),
    ("ngc 3372", "NGC 3372"), ("NGC3372", "NGC 3372"),
    ("IC 434", "IC 434"), ("ic434", "IC 434"),
    ("Sh2-155", "Sh2-155"), ("sh2 155", "Sh2-155"), ("sh2155", "Sh2-155"),
])
def test_designation_search_ignores_spacing_and_case(query, expected):
    assert _ids(query) == [expected]


def test_every_catalog_id_is_findable_spaced_and_unspaced():
    """Neither storage convention may be privileged: every id must be found
    both as it is written here and with its separators removed."""
    for o in CATALOG:
        assert o.id in _ids(o.id), o.id
        assert o.id in _ids(o.id.replace(" ", "").replace("-", "")), o.id


def test_name_and_type_search_still_substring():
    # Prose fields keep the plain substring test — a space there is a real word
    # boundary, so squashing them would let a query straddle two words.
    #
    # "andromeda" no longer returns M31 ALONE, because the star list spells out
    # each star's constellation and three named stars live in Andromeda. It
    # still returns M31 FIRST: "Andromeda Galaxy" starts with the query, the
    # stars merely contain it, and rank beats magnitude (all three stars are
    # brighter than M31 and would otherwise have buried it).
    assert _ids("andromeda")[0] == "M31"
    assert _ids("orion nebula") == ["M42"]
    assert "M42" in _ids("emission nebula")
    assert _ids("orionnebula") == []


def test_short_and_punctuation_queries_do_not_match_everything():
    # "m3" is a genuine prefix of three ids and must keep matching all three;
    # a punctuation-only query squashes to "" and must NOT match every object.
    assert set(_ids("m3")) == {"M3", "M31", "M33"}
    assert _ids("-") == []
    assert _ids("/") == ["NGC 2264"]  # matches the NAME "Cone Nebula / Xmas Tree"
    # An empty query still browses the DEEP-SKY catalog and nothing else: the
    # 241 named stars are all brighter than every object in it and a planet's
    # position is only true for the instant it was computed, so neither belongs
    # in a "what shall I image tonight?" browse. Both answer typed queries.
    assert len(search_catalog("", limit=99)) == len(CATALOG)


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


def test_search_stamps_difficulty():
    row = search_catalog("M42")[0]
    assert row["id"] == "M42"
    assert row["difficulty"] == "easy"
    assert row["difficulty_source"] == "heuristic"
    assert isinstance(row["surface_brightness"], float)
    # a curated-override target carries its source through search too.
    horse = next(r for r in search_catalog("horsehead") if r["id"] == "IC 434")
    assert horse["difficulty"] == "hard" and horse["difficulty_source"] == "curated"
