"""describe() — one true sentence for every catalog object, composed from
whatever fields it actually has. See describe.py's module docstring for the
degrade rules; these tests check the things that can quietly go wrong:
a type code (or Hubble code) with no phrase (renders the raw code, or
crashes at the worst possible moment — request time), a row that degrades
all the way to nothing instead of to a shorter true sentence, and the two
guards #195/#196 added — a blueshifted or too-nearby redshift must never
print a distance, and an ambiguous Wikidata match must never print a
discoverer.
"""
from __future__ import annotations

import re

import pytest

from astrodeck.catalog.describe import (
    HUBBLE_PHRASE,
    TYPE_PHRASE,
    _hubble_phrase,
    _type_phrase,
    describe,
)
from astrodeck.catalog.ngc_extras import BY_ID as NGC_EXTRAS_BY_ID
from astrodeck.catalog.objects import CATALOG, _TYPE_NAMES


def test_every_type_code_present_in_the_data_has_a_phrase():
    """objects._TYPE_NAMES is the authoritative set of codes CATALOG can ever
    contain (_load_bulk skips anything not in it). TYPE_PHRASE must cover the
    same set — not a superset that happens to include them, the SAME set, so
    a code retired from one side is caught on the other too."""
    assert set(TYPE_PHRASE) == set(_TYPE_NAMES)
    # And every code actually present in the loaded catalog right now:
    codes_in_use = {o.type for o in CATALOG}
    assert codes_in_use <= set(TYPE_PHRASE)


def test_unmapped_type_code_fails_loudly():
    """A fifth OpenNGC type introduced by a future data pull must not render
    as its raw two-letter code ("PN in Draco") — it must fail a test before
    it ever reaches a user."""
    with pytest.raises(KeyError, match="ZZ"):
        _type_phrase("ZZ")


def test_every_hubble_code_present_in_the_data_has_a_phrase():
    """Same contract as TYPE_PHRASE, for the sidecar build_ngc_extras.py
    produced: every Hubble code actually present in ngc_extras.tsv right now
    must have an entry in HUBBLE_PHRASE, so a future OpenNGC pull that
    introduces a 31st code fails a test rather than printing "Sbc galaxy in
    Draco" to a user."""
    codes_in_use = {e.hubble for e in NGC_EXTRAS_BY_ID.values() if e.hubble}
    assert codes_in_use, "expected at least one Hubble code in the sidecar"
    assert codes_in_use <= set(HUBBLE_PHRASE)


def test_unmapped_hubble_code_fails_loudly():
    with pytest.raises(KeyError, match="ZZ"):
        _hubble_phrase("ZZ")


@pytest.mark.parametrize("obj_id,expected", [
    # Has a common name AND a published magnitude, PLUS a Wikidata discoverer
    # with a day-precision date (William Herschel, 1786-02-15 — verified
    # against the live Wikidata item by hand before this test was written).
    ("NGC 6543",
     "Cat's Eye Nebula — planetary nebula in Draco · mag 9.0 · "
     "discovered by William Herschel on 15 February 1786"),
    # Hubble type refines "galaxy" -> "spiral galaxy" (OpenNGC's Hubble=Sb).
    # No distance clause: M31's redshift is NEGATIVE (blueshifted, Local
    # Group infall) — this is the #195 blueshift guard's own motivating
    # example, straight from the survey. No discoverer either: M31's
    # Wikidata item carries no P61 statement at all ("known since antiquity"
    # has no single discoverer to record) — an honest gap, not a bug.
    ("M31", "Andromeda Galaxy — spiral galaxy in Andromeda · mag 3.4"),
    # No common name (name == id in the data), has a magnitude, PLUS a Hubble
    # refinement ("E" -> "elliptical galaxy") and a positive, far-enough
    # redshift that clears the >= 10 Mpc reliability floor (z=0.010617 ->
    # ~148 Mly at H0=70 — computed independently before this test was
    # written, not read back out of the implementation).
    ("ESO107-004", "Elliptical galaxy in Pavo · mag 12.0 · ~148 Mly away"),
    # Has a common name, no published magnitude (objects.MAG_UNKNOWN), and
    # NONE of the new layers add anything: not a galaxy (no Hubble), no
    # OpenNGC redshift at all, not an NGC/IC-coded id (no Wikidata join
    # possible) — the floor still holds exactly as before #195/#196.
    ("B033", "Horsehead Nebula — dark nebula in Orion"),
    # Neither a common name nor a magnitude — the floor case, also untouched
    # by either new layer (open cluster, no Hubble; redshift present but
    # far below the reliability floor; no Wikidata join).
    ("H13", "Open cluster in Ara"),
])
def test_specific_examples(obj_id, expected):
    by_id = {o.id: o for o in CATALOG}
    assert describe(by_id[obj_id]) == expected


def test_edge_on_galaxy_gets_the_edge_on_prefix():
    """NGC 891 and NGC 4565 are two of the catalog's own curated rows AND two
    of astronomy's most famous edge-on galaxies — real ground truth, not
    derived from the implementation. Both clear the >= 3:1 major/minor axis
    ratio by a wide margin on this catalog's own numbers."""
    by_id = {o.id: o for o in CATALOG}
    assert describe(by_id["NGC 891"]).startswith(
        "Silver Sliver Galaxy — edge-on spiral galaxy in")
    assert describe(by_id["NGC 4565"]).startswith(
        "Needle Galaxy — edge-on spiral galaxy in")


def test_face_on_galaxy_gets_no_edge_on_prefix():
    """M51 (Whirlpool) is a famous face-on spiral — the negative control for
    the edge-on test above."""
    by_id = {o.id: o for o in CATALOG}
    assert "edge-on" not in describe(by_id["M51"])


_DISTANCE_RE = re.compile(r"~(-?\d+) Mly away")


def test_blueshift_and_near_guard_never_prints_a_bad_distance():
    """The #195 guard, checked over the WHOLE catalog rather than one
    example: every distance clause describe() ever prints must be a positive
    number, and (independently, reading ngc_extras.py directly rather than
    trusting describe()'s own arithmetic) no object whose redshift implies
    fewer than 10 Mpc — blueshifted or not — should have produced one at
    all. 376 rows are blueshifted; this must catch every one of them, not
    just M31."""
    from astrodeck.catalog.describe import _C_KM_S, _H0_KM_S_MPC, _MIN_RELIABLE_MPC

    for obj in CATALOG:
        s = describe(obj)
        m = _DISTANCE_RE.search(s)
        if m is None:
            continue
        assert int(m[1]) > 0, f"{obj.id} printed a non-positive distance: {s!r}"
        extra = NGC_EXTRAS_BY_ID.get(obj.id)
        d_mpc = _C_KM_S * extra.redshift / _H0_KM_S_MPC
        assert d_mpc >= _MIN_RELIABLE_MPC, (
            f"{obj.id} printed a distance from a redshift implying only "
            f"{d_mpc:.2f} Mpc, under the {_MIN_RELIABLE_MPC} Mpc floor: {s!r}")


def test_no_object_ever_prints_a_negative_distance():
    """Belt-and-braces: scan every rendered sentence for the literal failure
    mode this guard exists to prevent, independent of knowing which objects
    have a redshift at all."""
    for obj in CATALOG:
        assert "~-" not in describe(obj), f"{obj.id}: {describe(obj)!r}"


def test_ambiguous_wikidata_match_yields_no_discoverer_clause():
    """IC 2062 is a real catalog object whose Wikidata P528 code resolves to
    TWO distinct items (checked by hand against the live SPARQL endpoint
    before this test was written) — build_discoverers.py must have dropped
    it rather than guessing, so describe() must print no discoverer clause
    for it at all."""
    from astrodeck.catalog.discoverers import discoverer_for

    assert discoverer_for("IC 2062") is None
    by_id = {o.id: o for o in CATALOG}
    if "IC 2062" in by_id:
        assert "discovered by" not in describe(by_id["IC 2062"])


def test_bce_discovery_date_does_not_crash_and_says_bce():
    """NGC 869 (the Double Cluster) carries a Wikidata discovery date of
    "-0129-01-01" — Hipparchus's ~2nd-century-BCE observation, in Wikidata's
    astronomical year numbering (year 0 = 1 BCE). A naive
    ``"-0129-01-01".split("-")`` shreds the leading sign instead of the year;
    this is the regression test for that."""
    by_id = {o.id: o for o in CATALOG}
    s = describe(by_id["NGC 869"])
    assert "discovered by Hipparchus in 130 BCE" in s


def test_describe_never_returns_empty_or_placeholder_for_any_object():
    """Run over the entire 13,370-object catalog, not a sample. Never "",
    never whitespace-only, and never the specific anti-pattern this task
    calls out by name."""
    for obj in CATALOG:
        s = describe(obj)
        assert isinstance(s, str)
        assert s.strip() != "", f"{obj.id} described as empty/whitespace"
        assert "unknown object" not in s.lower(), (
            f"{obj.id} rendered a placeholder instead of a real sentence: {s!r}")
