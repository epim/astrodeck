# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Catalog search and coordinate math."""
import re

import pytest

from astrodeck.catalog import (CATALOG, altaz, format_dec, format_ra,
                               parse_dec, parse_ra, search_catalog)


def _ids(query, limit=25):
    return [r["id"] for r in search_catalog(query, limit)]


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
    # FIRST, not ONLY. This asserted a one-element list while the catalogue was
    # 64 hand-written rows; with the real NGC/IC catalogue behind it (2026-08-07)
    # "IC 434" is also a prefix of IC 4345/4346/4348 and "M3" of M30..M38, and
    # those are legitimate neighbours to offer. What must never change is that
    # the designation the user typed comes back at the TOP — that is what
    # RANK_EXACT beating RANK_PREFIX is for, and it is the actual claim here.
    ids = _ids(query)
    assert ids and ids[0] == expected, ids[:5]


#: Ids per sweep test. Each `search_catalog` call is a linear scan of all 13,370
#: rows (9.2 ms measured 2026-11-07, 2 searches per id), so 500 ids cost about
#: 9 s of CPU here and about 40 s on a hosted runner four times slower. The
#: whole sweep as ONE test took 246 s here and 940 s on CI (#1210): one xdist
#: worker busy for 16 minutes, and CI's 300 s stack dump fired on it every run.
_SWEEP_CHUNK = 500


def _sweep_chunks() -> list[tuple[str, list[str]]]:
    """(label, ids) pairs: ids grouped by catalogue prefix (NGC, IC, M, ...) and
    each group cut into runs of `_SWEEP_CHUNK`, so a failure names a catalogue
    and xdist can spread the sweep over every worker."""
    by_catalogue: dict[str, list[str]] = {}
    for o in CATALOG:
        by_catalogue.setdefault(re.match(r"[A-Za-z]*", o.id).group(0), []).append(o.id)
    return [(f"{name}-{i // _SWEEP_CHUNK}", ids[i:i + _SWEEP_CHUNK])
            for name, ids in by_catalogue.items()
            for i in range(0, len(ids), _SWEEP_CHUNK)]


_SWEEP_CHUNKS = _sweep_chunks()


@pytest.mark.parametrize("ids", [pytest.param(ids, id=label)
                                 for label, ids in _SWEEP_CHUNKS])
def test_every_catalog_id_finds_itself_first_however_it_is_spelled(ids):
    """Every id, both spellings, at the TOP of its own results.

    THE MOST EXPENSIVE TEST IN THIS SUITE, and worth knowing the price of: each
    `search_catalog` call is a linear scan of all 13,370 rows (9.2 ms measured
    2026-11-07), so one walk of the catalogue is ~123 seconds of pure CPU and
    both spellings ~246. It is split into `_SWEEP_CHUNK`-id runs so the cost
    spreads over the xdist workers (#1210). It earns that by being the only
    check that no id anywhere is unreachable or outranked: the shape that made
    "M 31" and "ngc3372" each find nothing.

    IT USED TO BE THREE WALKS, in two files, and one of them established
    nothing. This test asserted `o.id in _ids(o.id)`; `test_catalog_stars.py`
    separately asserted `_ids(o.id)[0] == o.id` over the same catalogue with the
    same call. The second implies the first, so 13,370 searches — 92 seconds of
    every CI run — were re-deriving a weaker form of a claim already proved
    three files away. Merged here, at two walks:

      * spaced, i.e. as the id is written;
      * unspaced, which is how a beginner copying off a website types it.

    Both now assert FIRST rather than merely present, which is stronger than
    either original and costs nothing: verified against all 13,370 before the
    assertion was tightened.

    THE STAR CLAIM RIDES ALONG, and is the reason first-ness matters rather
    than membership: `search_catalog` merges 241 named stars, all brighter than
    every galaxy in here, and brightness breaks ties. Without this, a Messier id
    could be pushed off the front of its own search by a star that merely shares
    letters with it.
    """
    for oid in ids:
        assert _ids(oid)[0] == oid, oid
        unspaced = oid.replace(" ", "").replace("-", "")
        assert _ids(unspaced)[0] == oid, f"{oid} as {unspaced!r}"


def test_the_sweep_chunks_partition_the_whole_catalogue():
    """The split must not lose an id: every catalogue id is in exactly one
    chunk, so the chunked sweep above covers what the single walk covered."""
    swept = [oid for _label, ids in _SWEEP_CHUNKS for oid in ids]
    assert sorted(swept) == sorted(o.id for o in CATALOG)
    assert len(set(swept)) == len(swept)


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
    # "m3" is a genuine prefix of every M3x id and must keep matching them —
    # with the exact M3 first (see the designation test above for why this
    # stopped being an equality once the real catalogue landed).
    m3 = _ids("m3", limit=99)
    assert m3[0] == "M3", m3[:5]
    assert {"M31", "M33"} <= set(m3), m3[:10]
    # …and a punctuation-only query matches NOTHING, on either field. This
    # stays an equality — it is the guard against a query that matches the
    # whole catalogue, and a bigger catalogue only makes it matter more.
    #
    # "/" used to return NGC 2264, whose name is "Cone Nebula / Xmas Tree", and
    # that was asserted deliberately. It stops being true here on purpose: a
    # query with no letter or digit in it names nothing, and against the real
    # catalogue "-" alone was pulling back "Eight-Burst Nebula", "ESO208-021"
    # and twenty others. A row you reached by typing a slash is not a search
    # result, it is an accident with a coordinate attached.
    assert _ids("-") == []
    assert _ids("/") == []
    # An empty query still browses the DEEP-SKY catalog and nothing else: the
    # 241 named stars are all brighter than every object in it and a planet's
    # position is only true for the instant it was computed, so neither belongs
    # in a "what shall I image tonight?" browse. Both answer typed queries.
    # (…and it is bounded by `limit`, which is the only thing standing between
    # a browse and the whole catalogue now that the catalogue is real.)
    browse = search_catalog("", limit=99)
    assert len(browse) == min(99, len(CATALOG))
    assert all(r["kind"] == "dso" for r in browse), "a browse is deep-sky only"


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
