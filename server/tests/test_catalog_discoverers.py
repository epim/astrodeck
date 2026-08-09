"""The Wikidata-discoverer sidecar (build_discoverers.py -> discoverers.tsv).
describe.py's own tests cover how a discovery clause changes a rendered
sentence; these tests cover the sidecar and its join — ground truth against
real, independently-known history, the ambiguous-match drop, and size.
"""
from __future__ import annotations

from pathlib import Path

from astrodeck.catalog.discoverers import BY_ID, discoverer_for
from astrodeck.catalog.objects import CATALOG

DATA_FILE = Path(__file__).resolve().parents[1] / "astrodeck" / "catalog" / "data" / "discoverers.tsv"


def test_sidecar_file_exists_and_is_reasonably_sized():
    """Survey estimate: ~335 KB for 7,874 rows. CC0 -- no attribution text to
    carry, unlike ngc_extras.tsv's OpenNGC notice, so nothing inflates it
    beyond the data itself."""
    assert DATA_FILE.is_file()
    size = DATA_FILE.stat().st_size
    assert 200_000 < size < 500_000, f"discoverers.tsv is {size} bytes -- unexpected size"


def test_coverage_is_close_to_the_survey_estimate():
    """Survey: 7,874 objects (58.9%). Loose bounds -- Wikidata is a live,
    editable source, not a frozen fixture, and build_discoverers.py's
    ambiguity rules are intentionally conservative (drop rather than guess),
    so a lower number over time is plausible and is not by itself a bug."""
    covered = len(BY_ID)
    assert 6_500 < covered < 9_000, f"{covered} rows in the sidecar"


def test_every_catalog_id_either_has_a_row_or_legitimately_has_nothing():
    for obj in CATALOG:
        discoverer_for(obj.id)  # must not raise


# ---------------------------------------------------------------------------
# Ground truth. Every value below is a fact about the history of astronomy,
# transcribed from what is independently known (Herschel's own observing
# logs, the standard References for these discoveries), not read back out of
# the sidecar the test is checking.
# ---------------------------------------------------------------------------

def test_known_discoverer_ground_truth_messier_objects():
    """Messier objects are the easiest ground truth in the catalog, per the
    task brief -- but M1 and M13 are especially clean cases because the
    Messier NUMBER is not who found them: M1 (Crab Nebula) was found by John
    Bevis in 1731, 27 years before Messier catalogued it; M13 (Hercules
    Cluster) was found by Edmond Halley in 1714. A join that just echoed
    "Charles Messier" for every M-number would be confidently wrong, not
    confidently right."""
    m1 = discoverer_for("M1")
    assert m1 is not None
    assert m1.name == "John Bevis"
    assert m1.date_iso.startswith("1731")

    m13 = discoverer_for("M13")
    assert m13 is not None
    assert m13.name == "Edmond Halley"
    assert m13.date_iso.startswith("1714")


def test_known_discoverer_ground_truth_ngc_object():
    """NGC 6543 (Cat's Eye Nebula), William Herschel, 15 February 1786 --
    a day-precision date, the richest case the sidecar carries."""
    d = discoverer_for("NGC 6543")
    assert d is not None
    assert d.name == "William Herschel"
    assert d.date_iso == "1786-02-15"
    assert d.precision == "11"  # day precision


def test_messier_object_with_no_wikidata_discoverer_is_an_honest_gap():
    """M31 (Andromeda Galaxy) has no P61 statement on Wikidata at all --
    "known since antiquity" has no single discoverer to record. This must
    come back None, not a guess and not a crash."""
    assert discoverer_for("M31") is None


# ---------------------------------------------------------------------------
# Ambiguity: dropped, not guessed.
# ---------------------------------------------------------------------------

def test_ambiguous_wikidata_code_is_dropped_not_guessed():
    """IC 2062 is a real id in this catalog whose Wikidata P528 code ("IC
    2062") resolves to two distinct Wikidata items (Q593465 and Q3691999 as
    of this writing -- verified by hand against the live SPARQL endpoint
    before this test was written, independently of build_discoverers.py's
    own ambiguous-code counting). The build must have excluded it rather
    than picking either item's facts at random."""
    assert "IC 2062" in {o.id for o in CATALOG}
    assert discoverer_for("IC 2062") is None


def test_no_row_has_a_blank_discoverer_name():
    """Every row that exists must have SOME name -- a build bug that wrote a
    key with an empty value would be worse than not writing the row."""
    for ident, d in BY_ID.items():
        assert d.name.strip(), f"{ident} has a blank discoverer name"


def test_precision_values_are_only_the_ones_wikibase_actually_defines():
    """Sanity check on the sidecar format itself: date_precision is either
    blank (discoverer known, no date at all) or one of Wikidata's
    wikibase:timePrecision codes actually observed in this data (9=year,
    10=month, 11=day) -- a stray value would mean the loader or the build
    script drifted from what describe.py's formatter expects."""
    seen = {d.precision for d in BY_ID.values()}
    assert seen <= {"", "9", "10", "11"}, f"unexpected precision codes: {seen}"
