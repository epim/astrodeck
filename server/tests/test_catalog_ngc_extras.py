# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The OpenNGC-extras sidecar (Hubble type, minor axis, redshift, a filtered
NED note, and the object's shape: axis ratio and position angle)
build_ngc_extras.py precomputes into ngc_extras.tsv. describe.py's own tests
cover how these fields change a rendered sentence; these tests cover the
sidecar itself — coverage, size, and the things a bad rebuild could silently
get wrong: the blueshift split, the NED-note allow-list, and (#181) the shape
columns the Atlas draws ellipses from.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from astrodeck.catalog import build_ngc_extras as builder
from astrodeck.catalog import ngc_extras as ngc_extras_mod
from astrodeck.catalog.build_ngc_extras import shape_columns, worth_surfacing
from astrodeck.catalog.ngc_extras import BY_ID, extras_for
from astrodeck.catalog.objects import (CATALOG, _dso_row, search_catalog,
                                       shape_fields, shape_of)

DATA_FILE = Path(__file__).resolve().parents[1] / "astrodeck" / "catalog" / "data" / "ngc_extras.tsv"

_BY_CATALOG_ID = {o.id: o for o in CATALOG}


def test_sidecar_file_exists_and_is_reasonably_sized():
    """The survey's own estimate for this recommended subset was ~410 KB for
    the full six-column set; this build carries four of those columns
    (Hubble, MinAx, Redshift, a filtered NED note — not SurfBr or the rest of
    Common names, which have no consumer) plus the two shape columns #181 added
    (axis ratio and position angle, about 11 bytes on each of ~10,800 rows), so
    a number near the survey's is expected and is not a regression. What
    would be a regression is an empty or wildly-off file."""
    assert DATA_FILE.is_file()
    size = DATA_FILE.stat().st_size
    assert 100_000 < size < 600_000, f"ngc_extras.tsv is {size} bytes -- unexpected size"


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


# ========================================================= object shape (#181)
#
# The catalogue served one size per object and no orientation, so the Atlas drew
# every outline as a circle and SUGGEST GRID tiled an elongated galaxy as if it
# were round. OpenNGC publishes MajAx, MinAx and PosAng on the same row; this is
# the package that carries them to the wire. Nothing here is site-derived: an
# axis ratio and a position angle are properties of the object.

_HEADER = ["id", "hubble", "minax_arcmin", "redshift", "ned_note",
           "axis_ratio", "posang_deg"]


def _sidecar_lines():
    text = DATA_FILE.read_text(encoding="utf-8")
    return [ln for ln in text.splitlines() if ln and not ln.startswith("#")]


def test_header_names_the_two_shape_columns_after_the_five_old_ones():
    """The five original columns stay first and in order, so a loader written
    before #181 still reads every row by position."""
    text = DATA_FILE.read_text(encoding="utf-8")
    header = next(ln for ln in text.splitlines() if ln.startswith("# id"))
    assert header[2:].split("\t") == _HEADER


def test_every_row_has_exactly_seven_cells():
    """A cell short or long shifts every column after it; the loader would
    drop the row (or, for a 5-cell row, serve it with no shape) and nothing
    would say so. Counted on the raw line: a trailing empty cell is a tab."""
    bad = [ln[:40] for ln in _sidecar_lines() if ln.count("\t") != 6]
    assert not bad, f"{len(bad)} rows are not 7 cells wide, first: {bad[:3]}"


def test_m31_shape_pairs_the_ratio_with_the_catalogues_own_size():
    """THE PAIRING. The catalogue shows M31 at 190' (the curated size an imager
    uses); OpenNGC's own major axis is 177.83' and its minor axis 69.66'. The
    minor axis served has to be the catalogue's size times OpenNGC's RATIO
    (0.392), 74.5'. Subtracting OpenNGC's difference from the shown size
    (190 - (177.83 - 69.66) = 81.8') mixes two different measurements of the
    same galaxy and draws the wrong ellipse.

    MUTANT shape_of_difference: in ``objects.shape_of`` write
    ``minor = obj.size_arcmin - (extra.minax / extra.axis_ratio - extra.minax)``
    in place of ``obj.size_arcmin * extra.axis_ratio``. Fails with:
    ``assert 81.95591836734695 == 74.48 +- 0.01`` (and, for M51,
    ``assert 8.956721504112808 == 9.361 +- 0.01``).
    """
    obj = _BY_CATALOG_ID["M31"]
    assert obj.size_arcmin != pytest.approx(177.83, abs=1.0), (
        "precondition: this test only separates the ratio from the difference "
        "while the catalogue shows a size other than OpenNGC's own")
    shape = shape_of(obj)
    assert shape is not None, "M31 has an OpenNGC minor axis and a position angle"
    major, minor, pa = shape
    assert major == obj.size_arcmin
    assert minor == pytest.approx(obj.size_arcmin * 0.392, abs=0.01)
    assert pa == 35.0


def test_a_second_curated_size_separates_the_ratio_from_the_difference_too():
    """M51: curated 11', OpenNGC 13.71 x 11.67 (ratio 0.851). Ratio gives
    9.36'; the difference rule gives 8.96'. A fixture of one object would let a
    rule that happened to agree on M31 pass."""
    obj = _BY_CATALOG_ID["M51"]
    major, minor, pa = shape_of(obj)
    assert minor == pytest.approx(obj.size_arcmin * 0.851, abs=0.01)
    assert pa == 163.0


def test_search_rows_carry_the_minor_axis_and_position_angle():
    row = next(r for r in search_catalog("M31") if r["id"] == "M31")
    assert row["minor_arcmin"] > 0
    assert row["minor_arcmin"] < row["size_arcmin"], (
        "a minor axis at or past the major axis is a circle or a lie")
    assert 0 <= row["pa_deg"] < 180


def test_no_position_angle_is_none_never_zero():
    """IC 434 (the Flame Nebula): OpenNGC publishes 90' x 30' and NO PosAng.
    The ratio is real and is served; the angle is unknown and stays None. A 0
    here would tell every consumer the nebula runs north-south."""
    extra = extras_for("IC 434")
    assert extra is not None and extra.axis_ratio == pytest.approx(0.333)
    assert extra.posang_deg is None
    obj = _BY_CATALOG_ID["IC 434"]
    major, minor, pa = shape_of(obj)
    assert minor > 0 and pa is None
    row = _dso_row(obj)
    assert row["minor_arcmin"] > 0 and row["pa_deg"] is None


def test_a_real_position_angle_of_zero_is_zero_not_missing():
    """IC 342 is published at PosAng 0, a real angle (due north). Anything
    that tests the angle for truth rather than for None turns it into
    "unknown" and the outline loses its orientation.

    MUTANT falsy_zero_pa_on_the_wire: in ``objects.shape_fields`` write
    ``"pa_deg": pa or None``. Fails with: ``assert (None is not None)``.
    """
    obj = _BY_CATALOG_ID["IC 342"]
    pa = shape_of(obj)[2]
    assert pa is not None and pa == 0.0
    wire = _dso_row(obj)["pa_deg"]
    assert wire is not None and wire == 0.0


def test_a_published_angle_of_180_is_folded_onto_zero():
    """An ellipse looks the same turned half a circle, so PA is a value mod 180
    and the wire promises [0, 180). OpenNGC publishes 44 rows at exactly 180
    (and one at 359); NGC 1421 is one of them."""
    assert extras_for("NGC 1421").posang_deg == 0.0


@pytest.mark.parametrize("ident", ["NGC 6543", "Sh2-155"])
def test_an_object_with_no_minor_axis_has_no_shape_and_says_null(ident):
    """NGC 6543 has a major axis and no MinAx; Sh2-155 is not an OpenNGC row at
    all. With no ratio there is no ellipse to describe, and a circle stays the
    honest fallback: shape_of is None and the row carries both keys as null
    (additive keys the client can rely on being present)."""
    obj = _BY_CATALOG_ID[ident]
    assert shape_of(obj) is None
    row = _dso_row(obj)
    assert "minor_arcmin" in row and "pa_deg" in row
    assert row["minor_arcmin"] is None and row["pa_deg"] is None
    assert shape_fields(obj) == {"minor_arcmin": None, "pa_deg": None}


def test_every_shipped_ratio_and_angle_is_inside_its_range():
    """The whole file, not a chosen example. A ratio above 1 would put the
    minor axis past the major; an angle outside [0, 180) breaks the contract
    the wire states (OpenNGC itself publishes up to 359)."""
    ratios = [e.axis_ratio for e in BY_ID.values() if e.axis_ratio is not None]
    angles = [e.posang_deg for e in BY_ID.values() if e.posang_deg is not None]
    assert len(ratios) > 10_000 and len(angles) > 10_000, (
        f"{len(ratios)} ratios / {len(angles)} angles -- the shape columns are "
        "missing or empty")
    assert all(0.0 < r <= 1.0 for r in ratios), (
        f"ratio out of (0, 1]: {sorted(ratios)[:3]} .. {sorted(ratios)[-3:]}")
    assert all(0.0 <= a < 180.0 for a in angles), (
        f"angle out of [0, 180): {sorted(angles)[-3:]}")


def test_an_object_with_no_size_has_no_shape():
    """An ellipse scaled from nothing is a point with an orientation. The ratio
    belongs to the sidecar row and survives whatever the catalogue says about
    the size, so shape_of has to look at the size itself: M31 with its size
    taken away is None, and the wire says null for both keys.

    MUTANT size_zero_guard_dropped: in ``objects.shape_of`` remove
    ``or obj.size_arcmin <= 0`` from the guard. Fails with: ``assert (0.0, 0.0, 35.0) is None``.
    """
    sizeless = dataclasses.replace(_BY_CATALOG_ID["M31"], size_arcmin=0.0)
    assert shape_of(sizeless) is None
    assert shape_fields(sizeless) == {"minor_arcmin": None, "pa_deg": None}


def test_the_minor_axis_on_the_wire_is_rounded_to_three_places():
    """A size times a three-place ratio carries more digits than either
    (1.5' x 0.333 is 0.4995' before rounding, and a float adds noise past
    that), and digits beyond the ratio's own precision are noise on every row
    of every region payload. The control comes first: unrounded, plenty of
    objects would fail the loop below, so the loop can fail.

    MUTANT shape_round_dropped: in ``objects.shape_fields`` write
    ``"minor_arcmin": minor`` in place of ``round(minor, 3)``. Fails with
    ``assert (7.279999999999999 is None or 7.279999999999999 == 7.28)`` (M16).
    """
    unrounded = [o.id for o in CATALOG
                 if shape_of(o) is not None
                 and shape_of(o)[1] != round(shape_of(o)[1], 3)]
    assert len(unrounded) > 100, "control: raw products must carry extra digits"
    for o in CATALOG:
        minor = shape_fields(o)["minor_arcmin"]
        assert minor is None or minor == round(minor, 3), o.id


# ------------------------------------------------------------------ the loader

def _load_text(tmp_path, monkeypatch, text):
    path = tmp_path / "ngc_extras.tsv"
    path.write_text(text, encoding="utf-8", newline="")
    monkeypatch.setattr(ngc_extras_mod, "_DATA_FILE", path)
    return ngc_extras_mod._load()


def test_an_old_five_column_line_still_loads_beside_a_seven_column_one(
        tmp_path, monkeypatch):
    """A sidecar written before #181 (5 cells) must keep loading: those rows
    come back with no shape rather than being dropped, and a new-format row
    beside them carries its own. A line of any other width is still skipped, as
    it always was -- a ragged row is a damaged row.

    MUTANT old_width_check: in ``ngc_extras._load`` keep ``len(parts) != 5``.
    Fails with: ``KeyError: 'NEW 1'`` (and the real-file tests see 0 rows).
    MUTANT new_width_only: write ``len(parts) != 7``. Fails with:
    ``KeyError: 'OLD 1'``.
    """
    got = _load_text(tmp_path, monkeypatch, (
        "# a comment\n"
        "OLD 1\tSb\t1.20\t0.010000\t\n"
        "NEW 1\tSc\t2.00\t0.020000\t\t0.500\t35.0\n"
        "RAGGED 1\tSb\t1.00\t0.010000\t\t0.500\n"))
    old = got["OLD 1"]
    assert old.minax == 1.2 and old.hubble == "Sb"
    assert old.axis_ratio is None and old.posang_deg is None
    new = got["NEW 1"]
    assert new.axis_ratio == 0.5 and new.posang_deg == 35.0
    assert new.minax == 2.0 and new.redshift == 0.02
    assert "RAGGED 1" not in got


def test_empty_shape_cells_load_as_none_and_a_zero_angle_loads_as_zero(
        tmp_path, monkeypatch):
    got = _load_text(tmp_path, monkeypatch, (
        "A 1\tSb\t1.00\t\t\t\t\n"
        "B 1\tSb\t1.00\t\t\t0.250\t\n"
        "C 1\tSb\t1.00\t\t\t0.250\t0.0\n"))
    assert got["A 1"].axis_ratio is None and got["A 1"].posang_deg is None
    assert got["B 1"].axis_ratio == 0.25 and got["B 1"].posang_deg is None
    assert got["C 1"].posang_deg == 0.0 and got["C 1"].posang_deg is not None


@pytest.mark.parametrize("ratio", ["0", "-0.5", "1.5", "nan", "inf", "abc"])
def test_a_ratio_cell_outside_zero_to_one_is_dropped_not_served(
        tmp_path, monkeypatch, ratio):
    """The loader never raises and never serves a value the wire's contract
    forbids: float('nan') and float('inf') parse happily and would otherwise
    reach a canvas as an ellipse radius. The angle beside it is its own cell
    and survives."""
    e = _load_text(tmp_path, monkeypatch,
                   f"X 1\tSb\t1.00\t\t\t{ratio}\t10.0\n")["X 1"]
    assert e.axis_ratio is None
    assert e.posang_deg == 10.0


@pytest.mark.parametrize("pa", ["-5.0", "180.0", "nan", "inf", "x"])
def test_an_angle_cell_outside_zero_to_180_is_dropped_not_served(
        tmp_path, monkeypatch, pa):
    """Folding onto [0, 180) is the builder's job; a cell that arrives outside
    it is damaged, and the loader refuses it instead of guessing."""
    e = _load_text(tmp_path, monkeypatch,
                   f"X 1\tSb\t1.00\t\t\t0.5\t{pa}\n")["X 1"]
    assert e.posang_deg is None
    assert e.axis_ratio == 0.5


# ----------------------------------------------------------------- the builder

@pytest.mark.parametrize("maj,mn,pa,size,expected", [
    # OpenNGC's M31 row against ngc.tsv's size for it.
    ("177.83", "69.66", "35", 177.83, ("0.392", "35.0")),
    # Three places, rounded, not truncated.
    ("3", "1", "10", 3.0, ("0.333", "10.0")),
    ("3", "2", "10", 3.0, ("0.667", "10.0")),
    # Missing or zero on either axis: no ratio. The angle is its own cell and
    # is carried when OpenNGC published one.
    ("0.9", "", "", 0.9, ("", "")),
    ("", "1.0", "20", 0.0, ("", "20.0")),
    ("0", "1.0", "20", 0.0, ("", "20.0")),
    ("10", "0", "20", 10.0, ("", "20.0")),
    ("10", "0.0", "", 10.0, ("", "")),
    # A ratio that rounds to nothing is no ratio.
    ("100", "0.01", "5", 100.0, ("", "5.0")),
    # Unparseable cells are missing cells.
    ("big", "1", "10", 0.0, ("", "10.0")),
    ("10", "wide", "up", 10.0, ("", "")),
    # No PosAng: the ratio stands alone (IC 434, the Flame Nebula).
    ("90", "30", "", 90.0, ("0.333", "")),
    # PosAng is an angle mod 180: 180 is 0, 359 is 179, and 0 stays 0.
    ("10", "5", "180", 10.0, ("0.500", "0.0")),
    ("10", "5", "359", 10.0, ("0.500", "179.0")),
    ("10", "5", "0", 10.0, ("0.500", "0.0")),
    ("10", "5", "179.96", 10.0, ("0.500", "0.0")),     # rounds up to 180.0
    ("10", "5", "-10", 10.0, ("0.500", "170.0")),
    # No cross-check when the caller has no catalogue size to offer.
    ("3.0", "1.5", "40", None, ("0.500", "40.0")),
    # A drift of four hundredths is still a different measurement: IC 3231 is
    # 0.65' in ngc.tsv and 0.69' x 0.47' upstream today.
    ("0.69", "0.47", "40", 0.65, ("", "")),
    # float() reads "nan" and "inf" as numbers and neither is an angle or an
    # axis: an unreadable PosAng is a missing one (never the text "nan" in the
    # file), and an unreadable MinAx leaves the angle OpenNGC did publish.
    ("10", "5", "nan", 10.0, ("0.500", "")),
    ("10", "5", "inf", 10.0, ("0.500", "")),
    ("10", "inf", "10", 10.0, ("", "10.0")),
])
def test_shape_columns(maj, mn, pa, size, expected):
    """Five mutants run against this table.

    MUTANT no_fold: ``angle = f"{round(pa, 1):.1f}"`` in place of the
    ``% 180`` fold. Fails with ``assert ('0.500', '180.0') == ('0.500',
    '0.0')``.

    MUTANT falsy_zero_pa: ``if pa:`` in place of ``if pa is not None:``, so a
    real angle of zero vanishes. Fails with ``assert ('0.500', '') ==
    ('0.500', '0.0')``.

    MUTANT ratio_inverted: ``round(maj / minor, 3)``. Fails with ``assert
    ('2.553', '35.0') == ('0.392', '35.0')``.

    MUTANT axis_finite_dropped: ``_parse_axis`` returns ``value`` without the
    ``math.isfinite`` test. Fails with ``assert ('0.500', 'nan') == ('0.500',
    '')`` (the nan and inf PosAng rows, and the inf MinAx row).

    MUTANT skew_tolerance_loose: the catalogue-size tolerance ``> 0.005``
    becomes ``> 0.5``. Fails with ``assert ('0.681', '40.0') == ('', '')``
    (the IC 3231 row, a drift of four hundredths)."""
    assert shape_columns(maj, mn, pa, size) == expected


def test_a_minor_axis_longer_than_the_major_is_not_a_shape():
    """OpenNGC has none today. If a future pull has one, the two axes are
    confused and the angle (measured along the major axis) is untrustworthy
    too, so both cells go empty rather than serving a minor axis past the
    major one.

    MUTANT no_major_guard: in ``shape_columns`` turn ``if minor > maj:`` into
    ``if False:``. Fails with ``assert ('2.000', '40.0') == ('', '')``.
    """
    assert shape_columns("5", "10", "40", 5.0) == ("", "")


def test_a_row_whose_major_axis_moved_since_ngc_tsv_was_built_has_no_shape():
    """The ratio is OpenNGC's MinAx over ITS MajAx, applied to the size
    ngc.tsv carries. If upstream changed the row after ngc.tsv was built the
    two are different measurements. Real instance, found rebuilding this file
    from a current download: IC 2105 is 0.65' in ngc.tsv and 3.0' x 1.5' in
    OpenNGC today. The honest answer is a circle, not a ratio from one
    snapshot on a size from another.

    MUTANT no_skew_guard: in ``shape_columns`` delete the early return on a
    major-axis mismatch (``if False:``). Fails with ``assert ('0.500',
    '40.0') == ('', '')``.
    """
    assert shape_columns("3.0", "1.5", "40", 0.65) == ("", "")
    # The same row agreeing to the catalogue's own two-place rounding is fine.
    assert shape_columns("177.834", "69.66", "35", 177.83) == ("0.392", "35.0")
    # And a major axis that vanished upstream while ngc.tsv still has a size.
    assert shape_columns("", "1.0", "40", 4.0) == ("", "")


def test_main_writes_seven_cells_in_order_and_keeps_the_five_old_ones(
        tmp_path, monkeypatch):
    """The wiring from OpenNGC's row to the file, on a four-object fixture:
    a normal row (PosAng 190 folds to 10), a row whose major axis disagrees
    with ngc.tsv (shape withheld, the rest kept), a row whose ONLY content is a
    position angle (kept: the angle alone is worth a line), and a row with
    nothing worth a line (not written at all).

    MUTANT main_skips_the_cross_check: in ``main`` pass ``None`` where
    ``catalogue_size`` goes. Fails with ``At index 1 diff`` between the NGC 2
    row as written (it gains the cells ``0.500`` and ``40.0``) and the row
    expected (both empty): the skewed row keeps a shape it must not.

    MUTANT keep_condition_ignores_angle: drop ``or posang`` from the "anything
    worth a row" test in ``main``. Fails with ``Right contains one more
    item``: the NGC 4 row, whose only content is ``33.0``, is not written.
    """
    ngc = tmp_path / "ngc.tsv"
    ngc.write_text(
        "# id\tcommon\ttype\tra_hours\tdec_deg\tmag\tsize_arcmin\talias\n"
        "NGC 1\tOne\tGX\t1.0\t2.0\t10.0\t10.0\t\n"
        "NGC 2\tTwo\tGX\t1.0\t2.0\t10.0\t0.65\t\n"
        "NGC 3\tThree\tPN\t1.0\t2.0\t10.0\t0.9\t\n"
        "NGC 4\tFour\tGX\t1.0\t2.0\t10.0\t2.0\t\n", encoding="utf-8")
    csv_path = tmp_path / "NGC.csv"
    csv_path.write_text(
        "Name;Type;MajAx;MinAx;PosAng;Hubble;Redshift;NED notes\n"
        "NGC0001;G;10.0;5.0;190;Sb;0.01;\n"
        "NGC0002;G;3.0;1.5;40;;;\n"
        "NGC0003;PN;0.9;;;;;\n"
        "NGC0004;G;2.0;;33;;;\n", encoding="utf-8")
    out = tmp_path / "out.tsv"
    monkeypatch.setattr(builder, "NGC_TSV", ngc)
    monkeypatch.setattr(builder, "OUT", out)
    assert builder.main([str(csv_path)]) == 0
    lines = out.read_text(encoding="utf-8").splitlines()
    header = next(ln for ln in lines if ln.startswith("# id"))
    assert header[2:].split("\t") == _HEADER
    assert [ln for ln in lines if not ln.startswith("#")] == [
        "NGC 1\tSb\t5.00\t0.010000\t\t0.500\t10.0",
        "NGC 2\t\t1.50\t\t\t\t",
        "NGC 4\t\t\t\t\t\t33.0",
    ]
