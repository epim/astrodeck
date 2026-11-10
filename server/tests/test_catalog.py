# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Catalog search and coordinate math."""
import random
import re

import pytest

from astrodeck.catalog import (CATALOG, altaz, format_dec, format_ra,
                               parse_dec, parse_ra, search_catalog)
from astrodeck.catalog import objects
from astrodeck.catalog.brightstars import latinise_greek


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


#: Ids per sweep test. A `search_catalog` call costs about 1 ms through the
#: candidate index (2 searches per id), so 500 ids cost about 1.3 s here. Before
#: the index each call was a linear scan of all 13,370 rows (9.2 ms), 500 ids
#: cost 9 s, and the whole sweep as ONE test took 246 s here and 940 s on CI
#: (#1210): one xdist worker busy for 16 minutes, and CI's 300 s stack dump
#: fired on it every run. The chunks stay so a failure names its catalogue.
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

    It was the most expensive test in this suite: each `search_catalog` call
    was a linear scan of all 13,370 rows (9.2 ms measured 2026-11-07), so both
    spellings of every id were ~246 seconds of pure CPU (#1210, #1261). The
    search now ranks only the rows whose keys, name or type contain the query
    (`_candidate_rows`), about 1 ms, and the whole sweep is about 33 seconds. It
    is still split into `_SWEEP_CHUNK`-id runs (#1210), and still the only check
    that no id anywhere is unreachable or outranked: the shape that made
    "M 31" and "ngc3372" each find nothing.

    MUTANT "an IC-less designation index" (``_INDEX`` built without the IC
    rows) fails exactly the eleven IC chunks, before the candidate index and
    after it.

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


# ---------------------------------------------- the candidate index changes nothing


def _reference_search(query, limit=25, when=None, site_derived=True):
    """``objects.search`` as it was before the candidate index: every row of
    ``_INDEX`` ranked by a Python loop (9 ms a search). Kept whole, as the
    reference the indexed ``search`` must equal row for row, note for note."""
    q = query.strip().lower()
    qs = objects.squash_designation(latinise_greek(q))
    qk = objects._prose_key(q)
    scored = []
    for o, keys, name, type_name in objects._INDEX:
        if not q:
            rank = objects.RANK_SUBSTRING
        else:
            rank = objects._best(
                objects._rank_keys(qs, keys),
                objects._rank_prose_keyed(qk, (name,)),
                objects.RANK_TYPE if q in type_name else None)
        if rank is not None:
            scored.append((rank, o.mag, o.id, o))
    notes = []
    if q:
        body_hits, notes = objects._solar_system_hits(q, qs, when, site_derived)
        sat_hits, sat_notes = objects._satellite_hits(q, qs, when, site_derived)
        comet_hits, comet_notes = objects._comet_hits(q, qs, when, site_derived)
        notes = notes + sat_notes + comet_notes
        for rank, r in (objects._star_hits(q, qs) + body_hits + sat_hits
                        + comet_hits):
            mag = r.get("mag")
            scored.append((rank, objects.MAG_UNKNOWN if mag is None else mag,
                           r["id"], r))
    scored.sort(key=lambda t: (t[0], t[1], t[2]))
    rows = [objects._dso_row(x) if isinstance(x, objects.DSO) else x
            for _, _, _, x in scored[:limit]]
    if not rows and q:
        coord = objects.parse_coordinates(query)
        if coord is not None:
            rows = [coord]
    if not rows and q and not notes:
        notes = notes + [
            f'Nothing in the catalogue matches "{query.strip()}". It carries '
            f"{len(CATALOG)} deep-sky objects (Messier, NGC and IC) plus named "
            f"stars and the planets - try a designation like NGC 6543, or type "
            f'a position: "17 58 33 +66 38".']
    return objects.SearchResult(rows=rows, notes=notes)


#: One id in this many is compared on both spellings. Every id would cost the
#: reference scan 26,740 x 9 ms, the price this index removed; the sweep above
#: already proves every id finds itself first through the indexed search.
_REFERENCE_STRIDE = 80
_PINNED_NOW = 1_800_000_000.0


def _free_text_queries(count: int = 200) -> list[str]:
    """Deterministic free text drawn from the catalogue: name fragments, id
    fragments, type words, aliases, plus the shapes the search guards (empty
    needles, punctuation, apostrophes, Greek, positions, bodies)."""
    rng = random.Random(1261)
    shapes = ["-", "/", "'", "  ", "m", "n", "a", "1", "m3", "ngc", "ic 4", "sh2",
              "galaxy", "gala", "emission nebula", "globular", "open cluster",
              "cat's eye", "cats eye", "andromeda", "orion nebula",
              "orionnebula", "beta cas", "β cas", "mars", "moon", "sun",
              "iss", "17 58 33 +66 38", "269.65 +66.63", "no such thing zq"]
    names = [o.name for o in CATALOG if len(o.name) > 4]
    ids = [o.id for o in CATALOG]
    out = list(shapes)
    while len(out) < count:
        kind = rng.randrange(3)
        if kind == 0:
            word = rng.choice(names)
            i = rng.randrange(len(word) - 2)
            out.append(word[i:i + rng.randrange(2, 7)])
        elif kind == 1:
            word = rng.choice(ids)
            out.append(word[:rng.randrange(1, len(word) + 1)])
        else:
            out.append(rng.choice(ids).replace(" ", "").lower()[1:])
    return out


def _same_answer(query, limit=25):
    got = objects.search(query, limit, _PINNED_NOW)
    want = _reference_search(query, limit, _PINNED_NOW)
    assert got.rows == want.rows, (
        f"{query!r} limit {limit}: ids now {[r['id'] for r in got.rows][:6]}, "
        f"before the index {[r['id'] for r in want.rows][:6]}")
    assert got.notes == want.notes, query


def test_the_indexed_search_answers_as_the_scan_did_for_free_text():
    """200 free-text queries at limit 25, every fourth also at 500 (the whole
    tail of the ranking), plus the browse: same rows in the same order, same
    notes.

    MUTANT "the type tier is dropped from the candidates" (the loop over
    ``_ROWS_OF_TYPE`` in ``_candidate_rows`` removed) -- RED, observed:

        AssertionError: 'm' limit 500: ids now ['Moon', 'Mercury', 'Mars',
        'Mimosa', 'M45', 'Miaplacidus'], before the index [the same six, and
        184 more rows after them]"""
    queries = _free_text_queries()
    assert len(set(queries)) > 150, "premise: the queries differ"
    for n, query in enumerate(queries):
        _same_answer(query)
        if n % 4 == 0:
            _same_answer(query, 500)
    _same_answer("")


def test_the_indexed_search_answers_as_the_scan_did_for_catalogue_ids():
    """Every ``_REFERENCE_STRIDE``-th catalogue id, spaced and unspaced, and
    one id from each catalogue prefix, at limit 25.

    MUTANT "the alias keys are left out of ``_KEY_INDEX``" (``for k in
    keys[:1]``) -- RED, observed:

        AssertionError: 'M1' limit 25: ids now ['M10', 'M19', 'M14', 'M17',
        'M12', 'M11'], before the index ['M1', 'M10', 'M19', 'M14', 'M13',
        'M17']
    """
    sample = [o.id for o in CATALOG[::_REFERENCE_STRIDE]]
    sample += [ids[0] for _label, ids in _SWEEP_CHUNKS]
    sample += ["M31", "NGC 224", "NGC 6720", "M57"]
    for oid in sample:
        _same_answer(oid)
        _same_answer(oid.replace(" ", "").replace("-", ""))
