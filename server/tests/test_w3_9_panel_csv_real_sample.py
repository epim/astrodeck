# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The panel file against the one real Telescopius export that is known, and the
five conventions as named constants (#178, #830, backlog consolidation WP 3.9).

THE FIXTURE. ``REAL_EXPORT`` is the header and three rows a user pasted on the
Telescopius forum (forum.telescopius.com/t/telescopius-csv-format-problem/1753,
read 2026-11-06): a centre row with ``-`` for Row and Column, and the two panes
of a 1 x 2 grid. It is not a file and not a 2 x 2, so it settles RA/DEC text,
the size unit, 1-based rows and the overlap unit outright and the sign of the
angle only as consistent with three numbers (see ``panel_csv``'s docstring).
The text carries the characters the page showed: the right single quote and the
ordinal indicator.

THE EXPORT IS WRITTEN IN THAT SHAPE (coordinator ruling, fix round 1): the same
header, a ``Center`` row, the same marks, arcminutes, a percent, no comment
lines. ``TestTheExportIsTheRealShape`` holds every cell of our export to the
form of the matching cell of the sample.

Before this file the importer refused it: ``hr`` and the apostrophe and double
quote of a time are not what ``coords.parse_ra`` reads, the ordinal indicator is
not a degree sign, and the centre row's ``-`` is not a row number.

NAMED MUTANTS (each from a byte backup of ``catalog/panel_csv.py``, restored and
sha256 compared):
  "angle not negated" (``PA_SIGN = 1``): four tests are RED, among them
  ``test_the_real_sample_imports_as_the_grid_it_is``, observed:
      assert not True
       +  where True = _warned({...}, 'rigid grid')
  (the panes then sit 11.2 arcminutes off the grid the flipped angle lays out)
  and ``test_the_sample_rotation_is_360_minus_its_angle``:
      assert 189.31 == 170.69 +- 1.7e-04
  "old export header" (``COLUMNS`` with bare ``width`` and ``height``):
  ``TestTheExportIsTheRealShape::test_the_export_header_is_the_samples`` is RED.
  "centre row read as a pane" (``_is_centre_row`` returns False):
  ``test_the_real_sample_imports_as_the_grid_it_is`` is RED, observed:
      PanelCsvError: line 2: Row '-' is not a number
"""
from __future__ import annotations

import math
import re

import pytest

from astrodeck.catalog import panel_csv
from astrodeck.catalog.framing import MosaicSpecIn, compute_mosaic
from astrodeck.catalog.panel_csv import (
    COLUMNS, DEFAULT_DECIMAL_RA_UNIT, DEFAULT_SIZE_UNIT, EXPORT_DEC_FORM,
    EXPORT_OVERLAP_UNIT, EXPORT_RA_FORM, EXPORT_SIZE_UNIT, FILE_ORIGIN,
    FRACTION_CEILING, PA_SIGN, export_csv, pa_east_from_rotation, parse_csv,
    rotation_from_pa_east)

REAL_EXPORT = (
    "Pane, RA, DEC, Position Angle (East), Pane width (arcmins), "
    "Pane height (arcmins), Overlap, Row, Column\n"
    "Center, 05hr 05\u2019 30\", -07\u00ba 03\u2019 20\", 189.30, 86.40, 86.40, 20%, -, -\n"
    "Pane 1, 05hr 05\u2019 07\", -07\u00ba 37\u2019 26\", 189.31, 86.40, 86.40, 20%, 1, 1\n"
    "Pane 2, 05hr 05\u2019 52\", -06\u00ba 29\u2019 14\", 189.29, 86.40, 86.40, 20%, 2, 1\n")

#: The centre row's own position, to the arcsecond.
CENTRE_RA_HOURS = 5 + 5 / 60 + 30 / 3600
CENTRE_DEC_DEG = -(7 + 3 / 60 + 20 / 3600)


def _spec(rows: int = 2, cols: int = 2, rotation: float = 30.0) -> MosaicSpecIn:
    return MosaicSpecIn(ra_hours=10.0, dec_deg=30.0, rows=rows, cols=cols,
                        overlap=0.25, rotation_deg=rotation,
                        fov_x_deg=2.0, fov_y_deg=1.5)


def _warned(answer: dict, needle: str) -> bool:
    return any(needle in w for w in answer["warnings"])


def _draft_centre(draft: dict) -> tuple[float, float]:
    from astrodeck.catalog import coords
    return coords.parse_ra(draft["ra"]), coords.parse_dec(draft["dec"])


class TestTheRealSample:
    def test_the_real_sample_imports_as_the_grid_it_is(self):
        answer = parse_csv(REAL_EXPORT)
        draft = answer["draft"]
        assert (draft["rows"], draft["cols"]) == (2, 1)
        assert draft["fovX"] == pytest.approx(86.4 / 60.0)
        assert draft["fovY"] == pytest.approx(86.4 / 60.0)
        assert draft["overlap"] == pytest.approx(20.0)
        assert draft["skip"] == ""
        # The fit puts the grid on the file's own centre row to the file's own
        # rounding: its panes are written to the second of time, 15 arcsec of RA.
        ra, dec = _draft_centre(draft)
        assert abs(ra - CENTRE_RA_HOURS) * 15.0 * math.cos(math.radians(dec)) < 10 / 3600
        assert abs(dec - CENTRE_DEC_DEG) < 10 / 3600
        assert not _warned(answer, "rigid grid"), answer["warnings"]
        # Every convention was stated by the file, so none is assumed aloud.
        assert not _warned(answer, "no unit"), answer["warnings"]
        assert not _warned(answer, "count from 0"), answer["warnings"]

    def test_the_sample_lays_row_one_at_the_frames_top(self):
        """CONSISTENCY OF THE ANGLE SIGN, not a documented fact: the file has
        row 1 SOUTH and WEST of row 2 (its position angle of about 189 puts the
        frame's top toward the south, and the grid runs 9 degrees east of north
        from row 1 to row 2), so the imported layout must put row 1 there too.
        A sign of +1 keeps row 1 south but tilts the grid the other way, and
        row 1 lands EAST of row 2."""
        draft = parse_csv(REAL_EXPORT)["draft"]
        ra, dec = _draft_centre(draft)
        panels = compute_mosaic(MosaicSpecIn(
            ra_hours=ra, dec_deg=dec, rows=draft["rows"], cols=draft["cols"],
            overlap=draft["overlap"] / 100.0, rotation_deg=draft["rotation"],
            fov_x_deg=draft["fovX"], fov_y_deg=draft["fovY"]))["panels"]
        by_row = {p["row"]: p for p in panels}
        south_dec, north_dec = by_row[0]["dec_deg"], by_row[1]["dec_deg"]
        assert south_dec < north_dec
        assert by_row[0]["ra_hours"] < by_row[1]["ra_hours"]

    def test_the_sample_rotation_is_360_minus_its_angle(self):
        draft = parse_csv(REAL_EXPORT)["draft"]
        assert draft["rotation"] == pytest.approx(360.0 - 189.31)

    def test_the_two_panes_run_nine_degrees_east_of_north_from_row_one_to_two(self):
        """The arithmetic the module docstring cites: pane 1 to pane 2 is 68
        arcminutes of Dec and 11 of true RA, a bearing of 9.3 degrees east of
        north, the file's own angle less a half turn."""
        d_dec = (-(6 + 29 / 60 + 14 / 3600)) - (-(7 + 37 / 60 + 26 / 3600))
        d_ra = (52 - 7) / 3600 * 15.0 * math.cos(math.radians(CENTRE_DEC_DEG))
        assert d_dec * 60.0 == pytest.approx(68.2, abs=0.1)
        assert math.degrees(math.atan2(d_ra, d_dec)) == pytest.approx(9.3, abs=0.1)

    def test_a_copy_that_lost_its_typographic_marks_reads_the_same(self):
        """The same rows with ASCII apostrophes and degree signs (a download
        rather than a copy) are the same file."""
        plain = (REAL_EXPORT.replace("\u2019", "'").replace("\u00ba", "\u00b0"))
        assert parse_csv(plain)["draft"] == parse_csv(REAL_EXPORT)["draft"]

    def test_a_file_that_is_only_a_centre_row_has_no_panes(self):
        head, centre = REAL_EXPORT.splitlines()[:2]
        with pytest.raises(panel_csv.PanelCsvError, match="no panes"):
            parse_csv(head + "\n" + centre + "\n")


def _export_lines(spec: MosaicSpecIn | None = None) -> list[list[str]]:
    """The export's lines as cells, header first (cells are joined by ``, ``)."""
    text = export_csv(spec or _spec())
    return [ln.split(", ") for ln in text.splitlines()]


def _form(cell: str) -> str:
    """A cell as the shape of its characters: each run of digits as ``9``."""
    return re.sub(r"\d+", "9", cell)


class TestTheExportIsTheRealShape:
    def test_the_export_header_is_the_samples(self):
        sample_header = REAL_EXPORT.splitlines()[0].split(", ")
        assert _export_lines()[0] == sample_header
        assert tuple(sample_header) == COLUMNS

    def test_every_cell_has_the_form_of_the_samples(self):
        sample = [ln.split(", ") for ln in REAL_EXPORT.splitlines()]
        ours = _export_lines()
        assert ours[1][0] == "Center" and sample[1][0] == "Center"
        # The centre row and a pane row, cell by cell, but for the numbers'
        # lengths.
        for mine, theirs in ((ours[1], sample[1]), (ours[2], sample[2])):
            assert len(mine) == len(theirs)
            for col in (1, 2, 4, 5, 6, 7, 8):
                if mine[col] == "-":
                    assert theirs[col] == "-"
                    continue
                assert _form(mine[col]).replace("+", "-") == _form(theirs[col]), (col, mine[col], theirs[col])
        assert ours[2][0] == "Pane 1" and sample[2][0] == "Pane 1"
        # The angle is a number with decimals, as the sample's is.
        assert re.fullmatch(r"\d+\.\d+", ours[2][3])

    def test_the_marks_are_the_samples_characters(self):
        assert panel_csv.RA_HOUR_MARK == "hr"
        assert panel_csv.RA_MINUTE_MARK == "\u2019"
        assert panel_csv.RA_SECOND_MARK == '"'
        assert panel_csv.DEC_DEGREE_MARK == "\u00ba"
        assert panel_csv.DEC_MINUTE_MARK == "\u2019"
        assert panel_csv.DEC_SECOND_MARK == '"'
        pane = _export_lines()[2]
        assert re.fullmatch(r"\d\d" + "hr" + r" \d\d\u2019 \d\d\"", pane[1]), pane[1]
        assert re.fullmatch(r"[+-]\d\d\u00ba \d\d\u2019 \d\d\"", pane[2]), pane[2]

    def test_the_file_has_no_comment_lines_and_no_quoting(self):
        text = export_csv(_spec())
        assert not [ln for ln in text.splitlines() if ln.startswith("#")]
        assert "\"\"" not in text, "a double quote was doubled by a CSV writer"

    def test_the_sizes_are_arcminutes_and_the_overlap_a_percent(self):
        pane = _export_lines()[2]
        assert EXPORT_SIZE_UNIT == "arcmin"
        assert pane[4:7] == ["120.00", "90.00", "25%"]

    def test_the_export_imports_back_unchanged(self):
        """Our own file reads back as the layout it was written from: the
        grid, the field, the overlap and the angle exactly, the centre to the
        whole second the file holds."""
        answer = parse_csv(export_csv(_spec(rotation=30.0)))
        draft = answer["draft"]
        assert (draft["rows"], draft["cols"], draft["skip"]) == (2, 2, "")
        assert (draft["fovX"], draft["fovY"], draft["overlap"]) == (2.0, 1.5, 25.0)
        assert draft["rotation"] == 30.0
        assert answer["warnings"] == []
        ra, dec = _draft_centre(draft)
        assert abs(ra - 10.0) * 15.0 < 10 / 3600 and abs(dec - 30.0) < 10 / 3600


class TestTheImporterReadsAWronglyDecodedCopy:
    def test_the_thai_decoding_of_the_ordinal_reads_as_the_ordinal(self):
        """The forum paste shows the ordinal as two Thai letters: its UTF-8
        bytes C2 BA read as Windows-874."""
        assert "\u0e22\u0e1a".encode("cp874") == "\u00ba".encode("utf-8")
        mangled = REAL_EXPORT.replace("\u00ba", "\u0e22\u0e1a")
        assert mangled != REAL_EXPORT
        assert parse_csv(mangled)["draft"] == parse_csv(REAL_EXPORT)["draft"]

    def test_the_latin1_decoding_of_the_ordinal_reads_as_the_ordinal(self):
        assert "\u00ba".encode("utf-8").decode("latin-1") == "\u00c2\u00ba"
        mangled = REAL_EXPORT.replace("\u00ba", "\u00c2\u00ba")
        assert parse_csv(mangled)["draft"] == parse_csv(REAL_EXPORT)["draft"]


class TestTheFiveConventionsAreNamed:
    def test_the_angle_sign_is_the_one_that_writes_330_for_30(self):
        assert PA_SIGN == -1
        assert pa_east_from_rotation(30.0) == 330.0
        assert rotation_from_pa_east(330.0) == 30.0

    def test_rows_and_columns_are_written_from_the_file_origin(self):
        panes = [c for c in _export_lines()[2:]]
        assert FILE_ORIGIN == 1
        assert min(int(c[7]) for c in panes) == FILE_ORIGIN
        assert min(int(c[8]) for c in panes) == FILE_ORIGIN

    def test_a_file_counting_from_zero_is_read_from_the_origin_and_said(self):
        zero = []
        for ln in export_csv(_spec()).splitlines():
            cells = ln.split(", ")
            if cells[0].startswith("Pane ") and cells[7].isdigit():
                cells[7], cells[8] = str(int(cells[7]) - 1), str(int(cells[8]) - 1)
            zero.append(", ".join(cells))
        answer = parse_csv("\n".join(zero) + "\n")
        assert (answer["draft"]["rows"], answer["draft"]["cols"]) == (2, 2)
        assert _warned(answer, f"from {FILE_ORIGIN} here")

    def test_a_size_with_no_unit_is_read_in_the_default_unit_and_said(self):
        text = ("Pane,RA,DEC,Position Angle (East),width,height,Overlap,Row,Column\n"
                "1,05:00:00,10:00:00,0,2.0,1.5,25%,1,1\n")
        answer = parse_csv(text)
        assert DEFAULT_SIZE_UNIT == "degrees"
        assert answer["draft"]["fovX"] == 2.0 and answer["draft"]["fovY"] == 1.5
        assert _warned(answer, f"read as {DEFAULT_SIZE_UNIT}")

    def test_a_decimal_ra_with_no_unit_is_read_in_the_default_unit_and_said(self):
        text = ("Pane,RA,DEC,Position Angle (East),width,height,Overlap,Row,Column\n"
                "1,5.0,10:00:00,0,2.0,1.5,25%,1,1\n")
        answer = parse_csv(text)
        assert DEFAULT_DECIMAL_RA_UNIT == "hours"
        assert answer["draft"]["ra"].startswith("05h 00m")
        assert _warned(answer, f"read as {DEFAULT_DECIMAL_RA_UNIT}")

    def test_the_export_writes_the_overlap_as_a_percent(self):
        assert EXPORT_OVERLAP_UNIT == "percent"
        assert _export_lines()[2][6] == "25%"

    @pytest.mark.parametrize("value, pct", [(FRACTION_CEILING, 50.0),
                                            (0.25, 25.0),
                                            (FRACTION_CEILING + 0.01, 0.51)])
    def test_an_overlap_with_no_unit_is_a_fraction_up_to_the_ceiling(self, value, pct):
        text = ("Pane,RA,DEC,Position Angle (East),width,height,Overlap,Row,Column\n"
                f"1,05:00:00,10:00:00,0,2.0,1.5,{value},1,1\n")
        assert parse_csv(text)["draft"]["overlap"] == pytest.approx(pct)

    def test_the_forms_are_named(self):
        assert EXPORT_RA_FORM == "hh" + "hr mm\u2019 ss\""
        assert EXPORT_DEC_FORM == "+dd" + "\u00ba mm\u2019 ss\""

    def test_a_2x2_at_30_round_trips_through_the_conventions(self):
        draft = parse_csv(export_csv(_spec(rotation=30.0)))["draft"]
        assert draft["rotation"] == 30.0
        assert (draft["rows"], draft["cols"]) == (2, 2)
        assert draft["overlap"] == pytest.approx(25.0)
        assert draft["fovX"] == 2.0 and draft["fovY"] == 1.5
