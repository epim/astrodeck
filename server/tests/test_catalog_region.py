"""objects_in_region() — the bounded-region query both the camera-frame
overlay and the Atlas viewport need. The centre+radius query is implemented
with a true spherical separation (coords.angular_sep_deg), specifically so
RA-wrap and pole cases work by construction rather than by special-casing —
these tests prove that with real catalog objects, not synthetic points, and
each one names a naive box-test failure mode it would have caught.
"""
from __future__ import annotations

from astrodeck.catalog.coords import angular_sep_deg
from astrodeck.catalog.objects import CATALOG
from astrodeck.catalog.region import objects_in_region

_BY_ID = {o.id: o for o in CATALOG}


def test_ra_wrap_finds_a_real_object_across_0h_24h():
    """IC 5385 sits at RA 0.106583h, Dec -0.076667deg (J2000). A centre at
    23.95h/0.0deg with a 3deg radius is 2.35deg from it by true angular
    separation -- well inside the circle. A naive box test
    (ra_min <= ra <= ra_max with ra_min=20.95, ra_max=23.95) rejects it
    outright, because 0.106583 does not lie between 20.95 and 23.95 -- the
    box never accounts for the wrap back through 0h.
    """
    assert _BY_ID["IC 5385"].ra_hours == 0.106583  # the real catalog row
    assert not (20.95 <= 0.106583 <= 23.95), "sanity: the naive box really misses it"

    hits = objects_in_region(23.95, 0.0, 3.0)
    assert "IC 5385" in {o.id for o in hits}


def test_pole_region_finds_objects_spread_across_all_ra():
    """Three real catalog objects near the north celestial pole -- NGC 188
    (RA 0.79h), NGC 1544 (RA 5.04h), NGC 3172 (RA 11.79h) -- are all within
    4deg of a centre at (RA 0h, Dec 88deg) by true angular separation, despite
    spanning nearly 11 hours of right ascension between them. Near the pole
    that's correct: lines of RA converge to a point, so "close in RA" is not
    what "close on the sky" means there. A box test on RA would treat NGC
    3172 (11h away from the centre's RA) as nowhere near, and be wrong.
    """
    hits = objects_in_region(0.0, 88.0, 4.0)
    ids = {o.id for o in hits}
    expected = {"NGC 188", "NGC 3172", "NGC 1544"}
    assert expected <= ids

    ras = [_BY_ID[i].ra_hours for i in expected]
    assert max(ras) - min(ras) > 8.0, "sanity: these really do span most of the RA circle"


def test_empty_region_returns_nothing():
    """A patch of sky with no catalogued object anywhere near it."""
    ra, dec, radius = 10.0, -45.0, 0.5
    nearest = min(angular_sep_deg(ra, dec, o.ra_hours, o.dec_deg) for o in CATALOG)
    assert nearest > radius, "sanity: the nearest real object really is outside radius"

    assert objects_in_region(ra, dec, radius) == []


def test_dense_region_ranking_prefers_a_named_prominent_object():
    """The Virgo Cluster core (centre ~12.45h/+12deg) packs dozens of
    catalogued galaxies into a couple of degrees -- exactly the "hundreds of
    objects, most of them noise" case ranking exists for. M87 (mag 9.0,
    common name "Virgo Galaxy", a Messier object) and M86 (mag 8.86, no
    common name in this catalog, also Messier) are both real rows in this
    region; M86 is nominally BRIGHTER. If ranking were magnitude alone, M86
    would win. It doesn't: the common-name bonus in _rank_score puts M87
    ahead, which is the whole reason that signal exists — a named showpiece
    beats an anonymous designation a fraction of a magnitude brighter.
    """
    hits = objects_in_region(12.45, 12.0, 2.0)
    ids = [o.id for o in hits]
    assert "M87" in ids and "M86" in ids
    assert ids.index("M87") < ids.index("M86")


def test_limit_truncates_the_ranked_list():
    full = objects_in_region(12.45, 12.0, 2.0)
    top3 = objects_in_region(12.45, 12.0, 2.0, limit=3)
    assert top3 == full[:3]
    assert len(top3) == 3


def test_custom_catalog_narrows_the_search():
    subset = [_BY_ID["M87"], _BY_ID["IC 5385"]]  # nowhere near each other
    hits = objects_in_region(12.45, 12.0, 2.0, catalog=subset)
    assert [o.id for o in hits] == ["M87"]
