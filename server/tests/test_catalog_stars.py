"""Named bright stars in the Atlas search.

The night this came from: the rig would not focus, the fix is to point at a
bright named star, and the search could not find one. The user was handed raw
RA/Dec for Caph by hand. Every test here is a claim about a star a person
actually types at 2am with cold hands.
"""
from __future__ import annotations

import pytest

from astrodeck.catalog import brightstars as bs
from astrodeck.catalog.objects import CATALOG, search_catalog


def _ids(query, limit=25):
    return [r["id"] for r in search_catalog(query, limit=limit)]


# --------------------------------------------------------- the table itself

def test_the_bright_naked_eye_sky_is_covered():
    """Cut at V <= 4.0, which is about what a suburban back garden shows. If
    this number drops, alignment stars have gone missing."""
    assert len(bs.STARS) >= 240
    assert max(s.mag for s in bs.STARS) <= 4.0


@pytest.mark.parametrize("name,ra_hours,dec_deg,mag", [
    # Spot values from the IAU Catalog of Star Names, each independently
    # confirmed against the Yale Bright Star Catalogue (V/50) by HR number.
    ("Polaris", 2.530304, 89.264109, 2.13),
    ("Caph", 0.152968, 59.149781, 2.28),
    ("Vega", 18.615649, 38.783689, 0.03),
    ("Sirius", 6.752477, -16.716116, -1.45),
    ("Betelgeuse", 5.919529, 7.407064, 0.45),
    ("Mizar", 13.398762, 54.925362, 2.23),
])
def test_spot_coordinates(name, ra_hours, dec_deg, mag):
    s = next(s for s in bs.STARS if s.name == name)
    assert abs(s.ra_hours - ra_hours) < 1e-5
    assert abs(s.dec_deg - dec_deg) < 1e-5
    assert s.mag == mag


def test_polaris_sits_where_the_pole_is():
    """An independent sanity check on the one star whose position everybody
    knows by heart, and the one a polar alignment leans on."""
    p = next(s for s in bs.STARS if s.name == "Polaris")
    assert 89.2 < p.dec_deg < 89.3


def test_every_star_is_actually_in_the_constellation_it_claims():
    """A coordinate typo big enough to matter moves a star out of its own
    constellation, and astropy can check all 241 against the IAU boundaries in
    ~10 ms. This is the guard that makes the table trustworthy without a
    network round-trip per row."""
    import astropy.units as u
    from astropy.coordinates import SkyCoord, get_constellation

    coords = SkyCoord(ra=[s.ra_hours * 15.0 for s in bs.STARS] * u.deg,
                      dec=[s.dec_deg for s in bs.STARS] * u.deg)
    found = get_constellation(coords, short_name=True)
    wrong = [(s.name, s.con, f) for s, f in zip(bs.STARS, found) if s.con != f]
    assert wrong == []


def test_every_constellation_has_a_readable_name():
    """A row that renders a bare "Cas" tells a beginner nothing. If a future
    magnitude cut pulls in a constellation with no entry, fail here rather than
    ship the abbreviation to the screen."""
    assert {s.con for s in bs.STARS} <= set(bs.CONSTELLATIONS)


def test_names_are_unique_across_the_whole_catalog():
    """/api/catalog rows are keyed by id in the UI list; a duplicate id silently
    drops a row."""
    ids = [s.name for s in bs.STARS] + [o.id for o in CATALOG]
    assert len(ids) == len(set(ids))


# ------------------------------------------------------------------- search

def test_polaris_resolves():
    assert _ids("Polaris")[0] == "Polaris"
    assert _ids("polaris")[0] == "Polaris"


@pytest.mark.parametrize("query", [
    "Caph", "caph",              # the proper name
    "bet Cas", "bet cas",        # as a chart/SIMBAD writes it
    "beta Cas", "beta cas",      # as a book writes it
    "β Cas", "β cas",            # as a web page writes it
    "betcas", "betacas",         # as a hurried person types it
    "beta Cassiopeiae",          # the full designation
    "beta Cassiopeia",
])
def test_caph_resolves_however_it_is_written(query):
    """This is the specific failure: with only one accepted spelling, the other
    seven send a user who is already stuck to "No matches"."""
    assert _ids(query)[0] == "Caph"


def test_a_greek_letter_is_spelled_out_not_deleted():
    """β is not [a-z0-9]. Squashing the query without spelling it out first
    turns "β Cas" into "cas" — which matches every star in Cassiopeia and
    lands the user on the wrong one."""
    assert bs.latinise_greek("β Cas").strip().startswith("beta")
    hits = _ids("β Cas")
    assert hits[0] == "Caph"


@pytest.mark.parametrize("query,expected", [
    ("alpha UMi", "Polaris"), ("alf UMi", "Polaris"),
    ("alpha Ursae Minoris", "Polaris"),
    ("zeta UMa", "Mizar"), ("eps Ori", "Alnilam"),
    ("xi Gem", "Alzirr"), ("ksi Gem", "Alzirr"),      # both spellings of ξ
    ("theta Aur", "Mahasim"), ("tet Aur", "Mahasim"),
    ("80 UMa", "Alcor"),                              # Flamsteed, no Greek letter
    ("alpha2 Lib", "Zubenelgenubi"),                  # superscripted Bayer
    ("alf Lib", "Zubenelgenubi"),                     # ...still found without it
])
def test_bayer_and_flamsteed_designations_resolve(query, expected):
    assert _ids(query)[0] == expected


def test_a_named_star_outranks_a_coincidental_deep_sky_substring():
    """The ranking rule this whole search change exists for: an exact name beats
    a substring, whatever the magnitudes are. "Mirach" is a 2.07-mag star; if a
    galaxy's name ever contains those letters the star still comes first."""
    assert _ids("Mirach")[0] == "Mirach"
    # "Andromeda" IS the name of M31 and merely the constellation of three
    # stars — the galaxy has to win.
    hits = _ids("andromeda")
    assert hits[0] == "M31"
    assert "Alpheratz" in hits          # ...but the stars are still offered


def test_searching_a_constellation_lists_its_stars():
    """"Which star should I focus on?" is usually asked as "what is up in
    Cassiopeia?" — the constellation is spelled out in every star's row, so it
    is searchable prose."""
    hits = _ids("Cassiopeia", limit=50)
    assert {"Caph", "Schedar", "Ruchbah"} <= set(hits)


def test_star_rows_say_which_star_and_where():
    """The row has to carry information the id does not: a beginner who typed
    "Caph" still needs to know it is β Cas, in Cassiopeia."""
    row = next(r for r in search_catalog("Caph") if r["id"] == "Caph")
    assert row["type"] == "Star"
    assert row["kind"] == "star"
    assert "Cas" in row["name"] and "Cassiopeia" in row["name"]
    assert row["size_arcmin"] == 0.0          # a point source, honestly stated
    assert row["mag"] == 2.28


def test_a_star_with_no_greek_letter_still_reads_correctly():
    """G Sco has a proper name and no Bayer letter. Its row must NOT print the
    bare constellation abbreviation in the designation slot ("Sco · Scorpius"
    reads like a designation and is not one), and "Sco" must not become an
    exact match for this one star out of the whole constellation."""
    star = next(s for s in bs.STARS if s.name == "Fuyue")
    row = next(r for r in search_catalog("Fuyue") if r["id"] == "Fuyue")
    assert row["name"] == "Scorpius"
    assert row["bayer"] == ""
    assert "sco" not in bs.search_keys(star)     # not a designation key
    assert _ids("alpha Sco")[0] == "Antares"     # ...and the real one still is


def test_a_star_search_does_not_bury_the_deep_sky_ids():
    """241 stars all brighter than every galaxy could easily push a Messier id
    off the front of the list. Every deep-sky id must still find itself first."""
    for o in CATALOG:
        assert _ids(o.id)[0] == o.id, o.id
