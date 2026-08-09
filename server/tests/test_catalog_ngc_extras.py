"""The OpenNGC-extras sidecar (Hubble type, minor axis, redshift, a filtered
NED note) build_ngc_extras.py precomputes into ngc_extras.tsv. describe.py's
own tests cover how these fields change a rendered sentence; these tests
cover the sidecar itself — coverage, size, and the two things a bad rebuild
could silently get wrong: the blueshift split and the NED-note allow-list.
"""
from __future__ import annotations

from pathlib import Path

from astrodeck.catalog.build_ngc_extras import worth_surfacing
from astrodeck.catalog.ngc_extras import BY_ID, extras_for
from astrodeck.catalog.objects import CATALOG

DATA_FILE = Path(__file__).resolve().parents[1] / "astrodeck" / "catalog" / "data" / "ngc_extras.tsv"


def test_sidecar_file_exists_and_is_reasonably_sized():
    """The survey's own estimate for this recommended subset was ~410 KB for
    the full six-column set; this build carries four of those columns
    (Hubble, MinAx, Redshift, a filtered NED note — not SurfBr/PosAng/the
    rest of Common names, which have no consumer in describe.py), so a
    smaller real number is expected and is not a regression. What would be a
    regression is an empty or wildly-off file."""
    assert DATA_FILE.is_file()
    size = DATA_FILE.stat().st_size
    assert 100_000 < size < 500_000, f"ngc_extras.tsv is {size} bytes -- unexpected size"


def test_coverage_is_close_to_the_survey_estimate():
    """Survey: 12,135 of 13,370 objects (90.8%) reachable by the full
    recommended subset. This build's narrower column set should land close
    to that, not near it by coincidence but because the same OpenNGC
    population backs both. Loose bounds — the source is a live upstream
    repository, not a frozen fixture."""
    covered = len(BY_ID)
    assert 11_000 < covered < 13_000, f"{covered} rows in the sidecar"


def test_every_catalog_id_either_has_a_row_or_legitimately_has_nothing():
    """Every id extras_for() is ever asked about is a real CATALOG id — the
    lookup must never KeyError, only return None."""
    for obj in CATALOG:
        extras_for(obj.id)  # must not raise


def test_hubble_has_exactly_the_thirty_codes_the_survey_found():
    """Verified directly against a fresh OpenNGC download while building this
    layer: exactly 30 distinct non-blank Hubble values. A future OpenNGC pull
    changing that count is real news, not sidecar-loader noise — this test
    is intentionally an exact-count assertion, not a loose bound."""
    codes = {e.hubble for e in BY_ID.values() if e.hubble}
    assert len(codes) == 30, f"{len(codes)} distinct Hubble codes: {sorted(codes)}"


def test_blueshifted_rows_are_carried_unfiltered_the_guard_is_describes_job():
    """This sidecar's OWN job is to carry the raw z through, sign and all —
    it does not know what "reliable" means, describe.py's _MIN_RELIABLE_MPC
    guard does. 376 rows negative per the survey; this asserts the sidecar
    did not quietly "fix" that by dropping or clamping them, which would
    silently defeat the guard's own test."""
    negative = [ident for ident, e in BY_ID.items() if e.redshift is not None and e.redshift < 0]
    assert len(negative) > 300, f"only {len(negative)} negative-redshift rows carried through"


def test_ned_note_allow_list_keeps_the_two_named_families_and_drops_plumbing():
    """From build_ngc_extras.py's own allow-list: honest-absence / doubtful-
    identification notes and Magellanic Cloud placements pass; HIPASS/SDSS
    survey cross-match trivia — real strings taken from the live OpenNGC
    download while building this — does not."""
    assert worth_surfacing("Nothing here; nominal position.")
    assert worth_surfacing("Nothing at this position.")
    assert worth_surfacing("NGC identification is not certain.")
    assert worth_surfacing("Identification as NGC 1109 is uncertain.")
    assert worth_surfacing("In the Large Magellanic Cloud.")
    assert worth_surfacing("Within boundaries of LMC")

    assert not worth_surfacing("Confused HIPASS source")
    assert not worth_surfacing("Multiple SDSS entries describe this object.")
    assert not worth_surfacing("Extended HIPASS source")
    assert not worth_surfacing("Additional radio sources may contribute to the WMAP flux.")


def test_surfaced_notes_in_the_real_sidecar_all_clear_the_allow_list():
    """Nothing in the shipped file should be there BECAUSE of a filter bug —
    every note actually on disk must independently re-pass worth_surfacing()
    (guards against someone loosening the filter and quietly shipping
    HIPASS/SDSS noise through a different code path)."""
    notes = [e.ned_note for e in BY_ID.values() if e.ned_note]
    assert len(notes) > 500, f"only {len(notes)} surfaced NED notes"
    for note in notes:
        assert worth_surfacing(note), f"shipped a note that fails its own filter: {note!r}"
