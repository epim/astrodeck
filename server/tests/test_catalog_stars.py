"""Named bright stars in the Atlas search.

The night this came from: the rig would not focus, the fix is to point at a
bright named star, and the search could not find one. The user was handed raw
RA/Dec for Caph by hand. Every test here is a claim about a star a person
actually types at 2am with cold hands.
"""
from __future__ import annotations

import math

import pytest

from astrodeck.catalog import brightstars as bs
from astrodeck.catalog.objects import CATALOG, search_catalog


def _ids(query, limit=25):
    return [r["id"] for r in search_catalog(query, limit=limit)]


# --------------------------------------------------------- the table itself

#: The 25 brightest stars in the night sky, in the order every reference prints
#: them. This list is NOT derived from the table below — that is the entire
#: point of it: `len(STARS) >= 240` only restates what was typed, while a named
#: external list fails loudly if a magnitude cut, a rename or a bad merge drops
#: a star a user will certainly ask for by name.
BRIGHTEST_25 = [
    "Sirius", "Canopus", "Rigil Kentaurus", "Arcturus", "Vega", "Capella",
    "Rigel", "Procyon", "Achernar", "Betelgeuse", "Hadar", "Altair", "Acrux",
    "Aldebaran", "Antares", "Spica", "Pollux", "Fomalhaut", "Deneb", "Mimosa",
    "Regulus", "Adhara", "Castor", "Gacrux", "Shaula",
]


def test_every_one_of_the_25_brightest_stars_is_present():
    """The stars a stuck beginner reaches for are the brightest ones. Checked
    against an external list of names, not against the table's own length."""
    have = {s.name for s in bs.STARS}
    assert [n for n in BRIGHTEST_25 if n not in have] == []


def test_the_magnitude_cut_is_where_it_says_it_is():
    """V <= 4.0, "roughly a suburban back garden". A row fainter than the stated
    cut is a transcription error, not a bonus."""
    assert max(s.mag for s in bs.STARS) <= 4.0
    assert min(s.mag for s in bs.STARS) == -1.45          # Sirius, and nothing brighter


@pytest.mark.parametrize("name,ra_hours,dec_deg,mag", [
    # Spot values transcribed from the IAU Catalog of Star Names. These pin the
    # table against change; they are NOT independent evidence that it is right
    # (same source), which is what the geometry tests further down are for.
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


def _sep_deg(a, b) -> float:
    """Angular separation between two NamedStars, in degrees."""
    ra1, ra2 = math.radians(a.ra_hours * 15.0), math.radians(b.ra_hours * 15.0)
    d1, d2 = math.radians(a.dec_deg), math.radians(b.dec_deg)
    cos = (math.sin(d1) * math.sin(d2)
           + math.cos(d1) * math.cos(d2) * math.cos(ra1 - ra2))
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def _star(name: str):
    return next(s for s in bs.STARS if s.name == name)


# ------------------------------------------------- checks against outside numbers
# The constellation-boundary test above catches a degree-scale typo. These catch
# the arcminute-scale one, and they do it WITHOUT trusting the table: every
# number on the right-hand side is a published separation that no one derived
# from these rows, and each one constrains two or three rows jointly. This is
# the answer to "the provenance claim cannot be re-run" — it is a weaker claim
# than a full catalog join, and unlike the old one it is a claim this repo can
# actually make.

def test_the_belt_of_orion_is_straight_and_evenly_spaced():
    """Alnitak, Alnilam and Mintaka are the most-looked-at line in the sky:
    collinear, and ~1.35 deg apart end to end. If any one of the three had a
    transcribed digit wrong the line would bend, and the bend shows up as the
    two hops no longer summing to the span."""
    zeta, eps, delta = _star("Alnitak"), _star("Alnilam"), _star("Mintaka")
    hop1, hop2 = _sep_deg(zeta, eps), _sep_deg(eps, delta)
    span = _sep_deg(zeta, delta)
    assert 1.2 < hop1 < 1.5, hop1
    assert 1.2 < hop2 < 1.5, hop2
    # Collinear to better than an arcminute: on a straight line the two hops sum
    # to the span exactly, and any kink makes the sum exceed it.
    assert 0.0 <= (hop1 + hop2) - span < 1.0 / 60.0


def test_mizar_and_alcor_are_the_published_eleven_arcminutes_apart():
    """The naked-eye double everyone tests their sight on: 11.8'. A pair this
    tight is the sharpest constraint in the table — an error of one arcminute
    in either row shows up here and nowhere else."""
    sep_arcmin = _sep_deg(_star("Mizar"), _star("Alcor")) * 60.0
    assert 11.5 < sep_arcmin < 12.1, sep_arcmin


def test_the_pointers_point_at_polaris():
    """Dubhe and Merak are 5.37 deg apart, and the line through them reaches
    Polaris 28.7 deg on. This is how a beginner finds north, and it is three
    rows checked against two published numbers at once."""
    dubhe, merak, polaris = _star("Dubhe"), _star("Merak"), _star("Polaris")
    assert abs(_sep_deg(dubhe, merak) - 5.37) < 0.05
    assert abs(_sep_deg(dubhe, polaris) - 28.7) < 0.1


def test_no_two_rows_are_the_same_point_of_sky():
    """A copy-paste that duplicates a coordinate pair leaves two rows at zero
    separation and is otherwise invisible. The tightest genuine pair in the
    table is alpha Cen A/B at ~16", so anything under 5" is a duplicate."""
    stars = bs.STARS
    too_close = [(a.name, b.name, _sep_deg(a, b) * 3600.0)
                 for i, a in enumerate(stars) for b in stars[i + 1:]
                 if _sep_deg(a, b) * 3600.0 < 5.0]
    assert too_close == []


def test_no_designation_is_claimed_twice():
    """Two rows sharing "bet Cas" would mean one of them has the wrong letter,
    and search would return whichever sorted first. alpha Cen is the one real
    exception: Rigil Kentaurus and Toliman are both alpha Cen, because they are
    both alpha Cen."""
    seen: dict[tuple[str, str], list[str]] = {}
    for s in bs.STARS:
        if s.bayer:
            seen.setdefault((s.bayer, s.con), []).append(s.name)
    doubled = {k: v for k, v in seen.items() if len(v) > 1}
    assert doubled == {("alf", "Cen"): ["Rigil Kentaurus", "Toliman"]}


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


# THE "STARS MUST NOT BURY THE DEEP SKY" SWEEP LIVES IN test_catalog.py NOW,
# as `test_every_catalog_id_finds_itself_first_however_it_is_spelled`.
#
# It was here as `test_a_star_search_does_not_bury_the_deep_sky_ids`, walking
# all 13,370 ids and asserting each finds itself FIRST — while test_catalog.py
# walked the same catalogue twice more, once asserting the strictly weaker
# `o.id in _ids(o.id)`. Three walks of a 6.9 ms search is 276 seconds of every
# CI run, and one of the three could not fail unless another already had.
#
# The merged test makes the same call (`search_catalog` merges the stars, so
# the star claim is exactly what first-ness tests) and carries this rationale
# in its docstring. Everything below stays here: it is about what a STAR row
# says, which is this file's subject.
