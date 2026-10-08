# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Panel CSV import and export in the Telescopius shape, with the angle
convention stated once (#178, WP-130, backlog wave 16).

An operator who plans a mosaic elsewhere had to retype it, and the angle is
the likeliest thing to get wrong: AstroDeck's ``rotation_deg`` increases from
north through west, a file's ``Position Angle (East)`` from north through
east. ``catalog/panel_csv.py`` holds every number and the convention, so the
angle cannot be wrong in two places; the modal only moves text.

WHAT IS GRADED
  * the angle both ways, and the 330 the export writes for a layout at 30;
  * a 2x2 at 30 round-trips, panel centres to 1e-6 degree and the angle to
    exactly 30, also across RA 0h and at a high declination;
  * a file in the documented Telescopius shape (a SYNTHESISED fixture, laid
    out by an independent first-principles script, not by AstroDeck's own
    projection) with the top of the frame at 30 east of north imports as
    rotation 330 and as a rigid grid, which is also what pins the SIGN of the
    angle against the geometry rather than against itself;
  * a pane moved by 10 percent of the field says so, and one moved by 1
    percent does not; a missing cell becomes ``skip``;
  * a BOM, CRLF line ends, comment lines, a semicolon delimiter, units in the
    header, decimal RA, row/column counting from 0 and a shuffled order;
  * every refusal: past GRID_MAX a side, non-finite numbers (the #324 rule),
    an oversize file, a file with no panes;
  * the two routes: the same answers, the gate (view.status, nothing here is
    derived from the site) and a 422 that names what is wrong.

OPEN QUESTIONS, for the issue (it stays open until a REAL Telescopius export
is checked): the RA/DEC text format, the width/height unit, whether Row and
Column are 1-based and where row 1 sits, and whether Overlap is a percent or
a fraction. The fixture is built to the documented nine columns.

MUTANTS, each run from a byte backup of ``catalog/panel_csv.py`` inside this
worktree, sha256 compared on restore and the mutant text grepped out, with
the failure quoted verbatim:
  M1 "export unconverted": ``export_csv`` writes ``rotation`` where
     ``pa_east_from_rotation(rotation)`` stood. Red in
     test_a_2x2_at_layout_angle_30_writes_330_and_round_trips:
       AssertionError: assert ['30.0', '30....30.0', '30.0'] == ['330.0', '33...0.0', '330.0']
  M2 "import does not negate": ``parse_csv`` reads ``_turn(pa_east)`` where
     ``rotation_from_pa_east(pa_east)`` stood. Red in the same test, and in
     test_the_telescopius_fixture_imports_as_rotation_330:
       assert 330.0 == 30.0
       assert 30.0 == 330.0
  M3 "plain mean centre": the fitted centre replaced by the arithmetic mean of
     the panes' RA and Dec. Red in the same test (15 arcsec off at Dec 41,
     the tangent plane is not the (RA, Dec) plane), at every RA 0h and
     high-declination case, and in the missing-cell case:
       assert 0.004233953592692369 < 1e-06
       these panes are not a rigid grid; imported as the nearest grid, 6 panes off by up to 1420.6 arcmin
       these panes are not a rigid grid; imported as the nearest grid, 3 panes off by up to 18.0 arcmin
  M4 "skip ignored": ``export_csv`` lays out every panel. Red in
     test_skipped_panels_are_left_out_and_come_back_as_skip:
       assert 9 == 6
  M5 "no verification": the layout is not compared with the panes. Red in
     test_a_pane_moved_ten_percent_of_the_field_is_not_a_rigid_grid:
       assert False + where False = _warned({...}, 'not a rigid grid')
  M6 "tolerance tightened": ``RIGID_TOLERANCE`` 0.02 -> 0.001. Red in
     test_a_pane_moved_one_percent_is_still_a_grid:
       these panes are not a rigid grid; imported as the nearest grid, 4 panes off by up to 0.6 arcmin
  M7 "gate raised": the export route requires ``view.site_derived``. Red in
     test_a_viewer_may_use_both_routes_because_nothing_here_is_site_derived:
       assert 403 == 200
  M9 "half-turn warning removed": red in
     test_the_half_turn_warning_fires_where_up_points_south:
       assert False is True + where False = _warned({...}, 'south')
  M10 "skip text dropped": the draft's ``skip`` is "". Red in
     test_skipped_panels_are_left_out_and_come_back_as_skip and
     test_a_missing_cell_becomes_skip_and_the_grid_is_still_rigid:
       assert '' == '1-1, 2-2, 3-3'
       assert '' == '2-2'
  M11 "overlap always a fraction": the overlap is multiplied by 100 whatever
     its unit. Red in the round trip (an overlap of 2500 percent, clamped):
       assert 50.0 == 25.0
  M16 "field checked before the rounding" (added by the verifier, who found the
     500 by fuzzing): a width of 1e-300 is above 0 as written and 0.0 as held.
     Red in test_a_field_that_rounds_to_zero_is_refused_not_a_500 (see there).
"""
from __future__ import annotations

import csv
import io
import math
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.auth import (CAP_VIEW_STATUS, principal_for_role,
                            reset_active_provider, set_active_provider)
from astrodeck.auth import rbac
from astrodeck.catalog import panel_csv
from astrodeck.catalog.coords import parse_dec, parse_ra
from astrodeck.catalog.framing import MosaicSpecIn, compute_mosaic, project
from astrodeck.flows.to_plan import GRID_MAX

FIXTURE = Path(__file__).parent / "fixtures" / "telescopius_2x2_pa30.csv"

#: A 2x2 of 2.0 x 1.33 deg at 25%, laid out at 30 (AstroDeck's rotation).
SPEC = dict(ra_hours=0.7122, dec_deg=41.0, rows=2, cols=2, overlap=0.25,
            rotation_deg=30.0, fov_x_deg=2.0, fov_y_deg=1.33)

COLUMNS = ["Pane", "RA", "DEC", "Position Angle (East)", "width", "height",
           "Overlap", "Row", "Column"]


# ------------------------------------------------------------------ helpers

def _read(text: str) -> tuple[list[str], list[dict]]:
    """The header and the data rows of a file, comment lines skipped."""
    lines = [ln for ln in text.splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    rows = list(csv.reader(lines))
    return rows[0], [dict(zip(rows[0], r)) for r in rows[1:]]


def _write(rows: list[dict], header: list[str] = COLUMNS,
           comments: tuple[str, ...] = (), eol: str = "\n") -> str:
    out = io.StringIO()
    w = csv.writer(out, lineterminator=eol)
    w.writerow(header)
    for r in rows:
        w.writerow([r.get(h, "") for h in header])
    return eol.join(comments) + (eol if comments else "") + out.getvalue()


def _panels_of(draft: dict) -> list[dict]:
    """The panels a draft lays out, through the one projection."""
    spec = MosaicSpecIn(
        ra_hours=parse_ra(draft["ra"]), dec_deg=parse_dec(draft["dec"]),
        rows=draft["rows"], cols=draft["cols"],
        overlap=draft["overlap"] / 100.0, rotation_deg=draft["rotation"],
        fov_x_deg=draft["fovX"], fov_y_deg=draft["fovY"])
    return compute_mosaic(spec)["panels"]


def _sep_deg(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Separation of two (ra_hours, dec_deg) points, by the tangent plane
    about the second: exact at the sub-degree distances compared here, and
    unlike an arccos of a cosine it keeps its precision at 1e-6 degree."""
    xi, eta = project(a[0], a[1], b[0], b[1])
    return math.hypot(xi, eta)


def _centres(panels: list[dict]) -> dict:
    return {(p["row"], p["col"]): (p["ra_hours"], p["dec_deg"]) for p in panels}


def _exported_rows(spec: dict, skip: str = "") -> tuple[str, list[dict]]:
    text = panel_csv.export_csv(MosaicSpecIn(**spec), skip)
    return text, _read(text)[1]


def _warned(answer: dict, needle: str) -> bool:
    return any(needle in w for w in answer["warnings"])


# --------------------------------------------------------------- the angle

def test_the_angle_runs_the_opposite_way_in_the_file():
    """AstroDeck's rotation increases north through west, the file's angle
    north through east, so each is 360 minus the other, mod 360."""
    assert panel_csv.pa_east_from_rotation(30.0) == 330.0
    assert panel_csv.rotation_from_pa_east(330.0) == 30.0
    assert panel_csv.pa_east_from_rotation(0.0) == 0.0
    assert panel_csv.rotation_from_pa_east(0.0) == 0.0
    assert panel_csv.pa_east_from_rotation(180.0) == 180.0
    assert panel_csv.rotation_from_pa_east(360.0) == 0.0
    assert panel_csv.rotation_from_pa_east(-30.0) == 30.0
    # An angle with no short decimal form comes back as the same number.
    assert panel_csv.rotation_from_pa_east(
        panel_csv.pa_east_from_rotation(12.3)) == 12.3


# ------------------------------------------------------------- the export

def test_the_export_is_the_nine_columns_and_states_the_convention():
    text, rows = _exported_rows(SPEC)
    header, _ = _read(text)
    assert header == COLUMNS
    comments = [ln for ln in text.splitlines() if ln.startswith("#")]
    assert comments, "the file states its convention in leading # lines"
    assert panel_csv.CONVENTION in "\n".join(comments)
    # Row-major, the file's own order, not compute_mosaic's snake.
    assert [(r["Row"], r["Column"]) for r in rows] == [
        ("1", "1"), ("1", "2"), ("2", "1"), ("2", "2")]
    assert [r["Pane"] for r in rows] == ["1", "2", "3", "4"]


def test_a_2x2_at_layout_angle_30_writes_330_and_round_trips():
    """The golden round trip: export, import, every panel centre to 1e-6
    degree and the angle to exactly 30.

    Mutant "export unconverted" (``export_csv`` writes ``spec.rotation_deg``
    where ``pa_east_from_rotation(spec.rotation_deg)`` stood) failed here:
       AssertionError: assert ['30.0', '30....30.0', '30.0'] == ['330.0', '33...0.0', '330.0']
    Mutant "import does not negate" (``rotation_from_pa_east`` returns the
    file's angle as read) failed here, the round trip returned 330:
       assert 330.0 == 30.0
    """
    text, rows = _exported_rows(SPEC)
    assert [r["Position Angle (East)"] for r in rows] == ["330.0"] * 4

    answer = panel_csv.parse_csv(text)
    draft = answer["draft"]
    assert draft["rotation"] == 30.0
    assert (draft["rows"], draft["cols"]) == (2, 2)
    assert draft["overlap"] == 25.0
    assert (draft["fovX"], draft["fovY"]) == (2.0, 1.33)
    assert draft["skip"] == ""
    assert not _warned(answer, "rigid"), answer["warnings"]

    want = _centres(compute_mosaic(MosaicSpecIn(**SPEC))["panels"])
    got = _centres(_panels_of(draft))
    assert sorted(want) == sorted(got)
    for cell in want:
        assert _sep_deg(got[cell], want[cell]) < 1e-6, cell


@pytest.mark.parametrize("ra_hours, dec_deg", [
    (0.0005, 10.0),        # the grid straddles RA 0h
    (23.9995, -20.0),      # and from the other side
    (5.0, 80.5),           # high declination: the plain mean of RA is wrong
    (12.0, -85.0),
])
def test_the_round_trip_holds_across_ra_zero_and_near_the_pole(ra_hours, dec_deg):
    """The centre is fitted on the tangent plane, not averaged in RA and
    Dec: at high declination RA degrees shrink by cos(dec), and across 0h the
    plain mean of 23.9995 and 0.0005 is 12.

    Mutant "plain mean centre" (the fitted centre replaced by the arithmetic
    mean of the panes' RA and Dec) failed here:
       these panes are not a rigid grid; imported as the nearest grid, 6 panes off by up to 1420.6 arcmin
    """
    spec = {**SPEC, "ra_hours": ra_hours, "dec_deg": dec_deg, "rows": 3,
            "cols": 2, "rotation_deg": 47.5}
    text, _ = _exported_rows(spec)
    answer = panel_csv.parse_csv(text)
    assert answer["draft"]["rotation"] == 47.5
    assert not _warned(answer, "rigid"), answer["warnings"]
    want = _centres(compute_mosaic(MosaicSpecIn(**spec))["panels"])
    got = _centres(_panels_of(answer["draft"]))
    for cell in want:
        assert _sep_deg(got[cell], want[cell]) < 1e-6, (cell, ra_hours, dec_deg)


def test_a_1x1_and_the_angles_0_and_180():
    for rotation in (0.0, 180.0):
        spec = {**SPEC, "rows": 1, "cols": 1, "rotation_deg": rotation}
        text, rows = _exported_rows(spec)
        assert [r["Position Angle (East)"] for r in rows] == [f"{rotation}"]
        draft = panel_csv.parse_csv(text)["draft"]
        assert draft["rotation"] == rotation
        assert (draft["rows"], draft["cols"], draft["skip"]) == (1, 1, "")
        assert _sep_deg(_centres(_panels_of(draft))[(0, 0)],
                        (SPEC["ra_hours"], SPEC["dec_deg"])) < 1e-6


def test_skipped_panels_are_left_out_and_come_back_as_skip():
    """A skipped panel is not shot, so it is not in the file; its cell is
    then missing, which the import reads back as the same ``skip``.

    Mutant "skip ignored" (``export_csv`` laid out every panel) failed here:
       assert 9 == 6
    """
    spec = {**SPEC, "rows": 3, "cols": 3}
    text, rows = _exported_rows(spec, "1-1, 2-2, 3-3, 9-9")
    cells = [(r["Row"], r["Column"]) for r in rows]
    assert len(cells) == 6
    for gone in (("1", "1"), ("2", "2"), ("3", "3")):
        assert gone not in cells
    answer = panel_csv.parse_csv(text)
    assert answer["draft"]["skip"] == "1-1, 2-2, 3-3"
    assert not _warned(answer, "rigid"), answer["warnings"]
    want = _centres(compute_mosaic(MosaicSpecIn(**spec))["panels"])
    for cell, got in _centres(_panels_of(answer["draft"])).items():
        assert _sep_deg(got, want[cell]) < 1e-6, cell


def test_the_export_refuses_a_layout_with_no_angle_or_no_panel():
    with pytest.raises(panel_csv.PanelCsvError, match="angle"):
        panel_csv.export_csv(MosaicSpecIn(**{**SPEC, "rotation_deg": -1.0}))
    with pytest.raises(panel_csv.PanelCsvError, match="angle"):
        panel_csv.export_csv(MosaicSpecIn.model_construct(
            **{**SPEC, "rotation_deg": float("nan")}))
    with pytest.raises(panel_csv.PanelCsvError, match="every panel"):
        panel_csv.export_csv(MosaicSpecIn(**SPEC), "1-1, 1-2, 2-1, 2-2")
    with pytest.raises(panel_csv.PanelCsvError, match="finite"):
        panel_csv.export_csv(MosaicSpecIn.model_construct(
            **{**SPEC, "fov_x_deg": float("inf")}))


# ------------------------------------------------------------- the import

def test_the_telescopius_fixture_imports_as_rotation_330():
    """A file in the documented shape, the top of its frame at position angle
    30 east of north, is a grid rotated 330 in AstroDeck's sense.

    The pane centres were laid out by an independent script, so the grid is
    rigid only if the sign is right: this is the geometry's check on the
    convention, not the convention's check on itself.

    Mutant "import does not negate" failed here, the angle read 30 and the
    panes did not fit it:
       assert 30.0 == 330.0
    """
    answer = panel_csv.parse_csv(FIXTURE.read_text(encoding="utf-8"))
    draft = answer["draft"]
    assert draft["rotation"] == 330.0
    assert (draft["rows"], draft["cols"]) == (2, 2)
    assert draft["skip"] == ""
    assert draft["overlap"] == 20.0
    assert (draft["fovX"], draft["fovY"]) == (2.0, 1.33)
    assert not _warned(answer, "rigid"), answer["warnings"]
    centre = (parse_ra(draft["ra"]), parse_dec(draft["dec"]))
    # M31's catalogue place, which the fixture was laid out about.
    assert _sep_deg(centre, (0.7123055555555556, 41.26916666666667)) < 1e-3
    # The file carries no unit for width and height: said, not assumed.
    assert _warned(answer, "degrees"), answer["warnings"]


def test_a_pane_moved_ten_percent_of_the_field_is_not_a_rigid_grid():
    """AstroDeck can only hold a rigid grid with skips, so a file whose panes
    are not one is imported as the nearest grid and says by how much.

    Mutant "no verification" (the layout is not compared with the panes)
    failed here, nothing warned:
       assert False + where False = _warned({...}, 'not a rigid grid')
    """
    _, rows = _exported_rows(SPEC)
    moved = [dict(r) for r in rows]
    # 10 percent of the 1.33 degree height, north, on one pane.
    moved[0]["DEC"] = f"{parse_dec(moved[0]['DEC']) + 0.133:.9f}"
    answer = panel_csv.parse_csv(_write(moved))
    assert _warned(answer, "not a rigid grid"), answer["warnings"]
    warning = next(w for w in answer["warnings"] if "rigid" in w)
    assert "imported as the nearest grid" in warning
    # The fit shares the move out, so all four sit off the grid it chose: the
    # moved pane by three quarters of the move (6.0 arcmin).
    assert "4 panes off by up to 6.0 arcmin" in warning, warning


def test_a_pane_moved_one_percent_is_still_a_grid():
    _, rows = _exported_rows(SPEC)
    moved = [dict(r) for r in rows]
    moved[0]["DEC"] = f"{parse_dec(moved[0]['DEC']) + 0.0133:.9f}"
    answer = panel_csv.parse_csv(_write(moved))
    assert not _warned(answer, "rigid"), answer["warnings"]


def test_a_missing_cell_becomes_skip_and_the_grid_is_still_rigid():
    """The centre of a grid with a hole is not the mean of its panes: the
    fit measures each pane against ITS cell. The mean of three panes of a
    2x2 sits a quarter of a step off the centre, which would read as a
    mis-laid grid.

    Mutant "plain mean centre" failed here too:
       these panes are not a rigid grid; imported as the nearest grid, 3 panes off by up to 18.0 arcmin
    """
    _, rows = _exported_rows(SPEC)
    answer = panel_csv.parse_csv(_write([r for r in rows
                                         if (r["Row"], r["Column"]) != ("2", "2")]))
    assert answer["draft"]["skip"] == "2-2"
    assert (answer["draft"]["rows"], answer["draft"]["cols"]) == (2, 2)
    assert not _warned(answer, "rigid"), answer["warnings"]
    assert _warned(answer, "2-2"), answer["warnings"]
    want = _centres(compute_mosaic(MosaicSpecIn(**SPEC))["panels"])
    for cell, got in _centres(_panels_of(answer["draft"])).items():
        assert _sep_deg(got, want[cell]) < 1e-6, cell


def test_a_bom_crlf_comments_and_a_shuffled_order_are_read():
    text, rows = _exported_rows(SPEC)
    plain = panel_csv.parse_csv(text)["draft"]
    messy = "\ufeff" + _write(list(reversed(rows)), comments=("# a note", "#"),
                              eol="\r\n")
    assert panel_csv.parse_csv(messy)["draft"] == plain


def test_a_semicolon_delimited_file_is_read():
    text, rows = _exported_rows(SPEC)
    lines = [ln for ln in text.splitlines() if not ln.startswith("#")]
    semicolons = "\n".join(ln.replace(",", ";") for ln in lines)
    assert panel_csv.parse_csv(semicolons)["draft"] == \
        panel_csv.parse_csv(text)["draft"]


def test_the_unit_of_width_and_height_is_read_from_the_header():
    _, rows = _exported_rows(SPEC)
    arcmin = [{**r, "width": "120", "height": "79.8"} for r in rows]
    header = [h + " (arcmins)" if h in ("width", "height") else h
              for h in COLUMNS]
    renamed = [{(h + " (arcmins)" if h in ("width", "height") else h): v
                for h, v in r.items()} for r in arcmin]
    answer = panel_csv.parse_csv(_write(renamed, header))
    assert answer["draft"]["fovX"] == 2.0
    assert answer["draft"]["fovY"] == 1.33
    assert not _warned(answer, "degrees"), answer["warnings"]


def test_the_units_line_of_our_own_export_is_read_back():
    """The export's ``# units:`` comment is the one place a unit is written
    for a file whose columns are the bare nine names."""
    text, rows = _exported_rows(SPEC)
    assert "units:" in text
    in_arcmin = text.replace("width=degrees", "width=arcmin").replace(
        "height=degrees", "height=arcmin")
    answer = panel_csv.parse_csv(in_arcmin)
    assert answer["draft"]["fovX"] == pytest.approx(2.0 / 60.0)


def test_ra_may_be_hms_decimal_hours_or_degrees():
    _, rows = _exported_rows({**SPEC, "rows": 1, "cols": 1})
    one = rows[0]
    want_h = parse_ra(one["RA"])

    def ra_of(cell: str, header: str = "RA") -> float:
        row = {**one, header: cell}
        text = _write([row], [header if h == "RA" else h for h in COLUMNS])
        answer = panel_csv.parse_csv(text)
        return parse_ra(answer["draft"]["ra"])

    assert ra_of("00h 42m 44.0s") == pytest.approx(parse_ra("00:42:44"), abs=1e-9)
    assert ra_of(f"{want_h:.9f}") == pytest.approx(want_h, abs=1e-8)
    assert ra_of(f"{want_h * 15:.9f}", "RA (deg)") == pytest.approx(want_h, abs=1e-8)
    # No unit and past 24: it can only be degrees, and says so.
    far = {**one, "RA": f"{(18.0 + want_h) * 15 % 360:.6f}"}
    answer = panel_csv.parse_csv(_write([far]))
    assert parse_ra(answer["draft"]["ra"]) == pytest.approx((18.0 + want_h) % 24, abs=1e-6)
    assert _warned(answer, "degrees"), answer["warnings"]


def test_rows_and_columns_may_count_from_zero():
    _, rows = _exported_rows(SPEC)
    zero = [{**r, "Row": str(int(r["Row"]) - 1), "Column": str(int(r["Column"]) - 1)}
            for r in rows]
    answer = panel_csv.parse_csv(_write(zero))
    one_based = panel_csv.parse_csv(_write(rows))
    assert answer["draft"] == one_based["draft"]
    assert _warned(answer, "from 0"), answer["warnings"]


def test_panes_that_disagree_on_the_angle_use_the_most_common_and_say_so():
    _, rows = _exported_rows(SPEC)
    odd = [dict(r) for r in rows]
    odd[3]["Position Angle (East)"] = "200"
    answer = panel_csv.parse_csv(_write(odd))
    assert answer["draft"]["rotation"] == 30.0
    assert _warned(answer, "angle"), answer["warnings"]


def test_two_panes_in_one_cell_keep_the_first_and_say_so():
    _, rows = _exported_rows(SPEC)
    twice = rows + [{**rows[0], "Pane": "5"}]
    answer = panel_csv.parse_csv(_write(twice))
    assert _warned(answer, "1-1"), answer["warnings"]
    assert not _warned(answer, "rigid"), answer["warnings"]


def test_headers_are_read_by_what_they_name_not_by_their_spelling():
    """A real export may spell its columns otherwise: a unit in brackets,
    underscores, a long name. Read by the name without its unit, so a
    ``Pane width (arcmins)`` is a width and not a pane."""
    _, rows = _exported_rows({**SPEC, "rows": 1, "cols": 1})
    one = rows[0]
    header = ["Panel", "Right Ascension (hours)", "Declination", "PA",
              "Pane width (arcmins)", "Pane_height_(arcmins)", "Overlap (%)",
              "Row", "Col"]
    cells = [one["Pane"], "0.7122", one["DEC"], "330", "120", "79.8", "25%",
             "1", "1"]
    answer = panel_csv.parse_csv(_write([dict(zip(header, cells))], header))
    d = answer["draft"]
    assert (d["rotation"], d["rows"], d["cols"]) == (30.0, 1, 1)
    assert (d["fovX"], d["fovY"], d["overlap"]) == (2.0, 1.33, 25.0)
    assert parse_ra(d["ra"]) == pytest.approx(0.7122, abs=1e-8)
    assert answer["warnings"] == []


def test_the_half_turn_warning_fires_where_up_points_south():
    """A file whose angle puts the top of the frame toward the south may have
    been written in a half-turn convention (NINA's is 180 minus the
    rotation): the footprint is the same, the frame upside down."""
    _, rows = _exported_rows({**SPEC, "rows": 1, "cols": 1})
    for pa, expect in (("30", False), ("330", False), ("200", True), ("180", True)):
        answer = panel_csv.parse_csv(_write([{**rows[0], "Position Angle (East)": pa}]))
        assert _warned(answer, "south") is expect, (pa, answer["warnings"])


# ------------------------------------------------------------ the refusals

def _one(**cells) -> list[dict]:
    _, rows = _exported_rows({**SPEC, "rows": 1, "cols": 1})
    return [{**rows[0], **cells}]


def test_a_grid_past_the_limit_is_refused():
    _, rows = _exported_rows({**SPEC, "rows": 1, "cols": 1})
    wide = [{**rows[0], "Column": str(c)} for c in range(1, GRID_MAX + 2)]
    with pytest.raises(panel_csv.PanelCsvError, match=str(GRID_MAX)):
        panel_csv.parse_csv(_write(wide))
    tall = [{**rows[0], "Row": str(r)} for r in range(1, GRID_MAX + 2)]
    with pytest.raises(panel_csv.PanelCsvError, match=str(GRID_MAX)):
        panel_csv.parse_csv(_write(tall))
    # Exactly the limit is a grid.
    ok = [{**rows[0], "Column": str(c)} for c in range(1, GRID_MAX + 1)]
    assert panel_csv.parse_csv(_write(ok))["draft"]["cols"] == GRID_MAX


@pytest.mark.parametrize("column", ["RA", "DEC", "Position Angle (East)",
                                    "width", "height", "Overlap"])
@pytest.mark.parametrize("token", ["nan", "inf", "-inf", "NaN", "Infinity"])
def test_a_non_finite_number_is_refused(column, token):
    """The #324 rule: a NaN laid out is a layout of NaN corners that every
    comparison reads as 'no move'. Refused where it is read."""
    with pytest.raises(panel_csv.PanelCsvError, match="finite|number"):
        panel_csv.parse_csv(_write(_one(**{column: token})))


@pytest.mark.parametrize("cells, match", [
    ({"DEC": "91"}, "DEC"),
    ({"RA": "25h 00m 00s"}, "RA"),
    ({"width": "0"}, "width"),
    ({"height": "-1"}, "height"),
    ({"width": "75"}, "wider"),
    ({"Overlap": "-5"}, "Overlap"),
    ({"Row": "1.5"}, "Row"),
    ({"Column": "x"}, "Column"),
    ({"Row": "-1"}, "Row"),
])
def test_values_that_cannot_be_a_panel_are_refused(cells, match):
    with pytest.raises(panel_csv.PanelCsvError, match=match):
        panel_csv.parse_csv(_write(_one(**cells), COLUMNS))


def test_a_field_that_rounds_to_zero_is_refused_not_a_500(client):
    """A width of 1e-300 degrees, or of 1e-6 arcsec, is above 0 as written
    and 0.0 as held (nine places), and MosaicSpecIn refuses a field of 0 from
    inside the centre fit: the verifier's fuzz found a 500 from a route a
    viewer may call. It is refused where it is read, with its reason, in the
    parser and as a 422 at the route.

    Mutant "checked before the rounding" (``if not deg > 0.0`` in
    ``parse_csv`` made ``if False:``) failed here, pydantic's own refusal
    escaped from ``_layout``:
       pydantic_core._pydantic_core.ValidationError: 1 validation error for MosaicSpecIn
       fov_x_deg
         Input should be greater than 0 [type=greater_than, input_value=0.0, input_type=float]
    """
    in_arcsec = [h + " (arcsec)" if h == "width" else h for h in COLUMNS]
    for cells, header in (
        ({"width": "1e-300"}, COLUMNS),
        ({"height": "4e-10"}, COLUMNS),
        ({"width": "5e-324"}, COLUMNS),
        ({"width (arcsec)": "1e-6"}, in_arcsec),
    ):
        row = {("width (arcsec)" if h == "width" and header is in_arcsec else h): v
               for h, v in _one()[0].items()}
        text = _write([{**row, **cells}], header)
        with pytest.raises(panel_csv.PanelCsvError, match="too small"):
            panel_csv.parse_csv(text)
        r = client.post("/api/framing/mosaic/import", json={"text": text})
        assert r.status_code == 422, (cells, r.status_code, r.text)
        assert "too small" in r.text, (cells, r.text)
    # The smallest field the nine places hold is still a field.
    answer = panel_csv.parse_csv(_write(_one(width="1e-9")))
    assert answer["draft"]["fovX"] == 1e-9


def test_a_file_that_is_not_a_panel_file_is_refused():
    with pytest.raises(panel_csv.PanelCsvError, match="no panes"):
        panel_csv.parse_csv("")
    with pytest.raises(panel_csv.PanelCsvError, match="no panes"):
        panel_csv.parse_csv("# only a note\n")
    with pytest.raises(panel_csv.PanelCsvError, match="no panes"):
        panel_csv.parse_csv(",".join(COLUMNS) + "\n")
    with pytest.raises(panel_csv.PanelCsvError, match="Column"):
        panel_csv.parse_csv(_write(_one(), [h for h in COLUMNS if h != "Column"]))
    with pytest.raises(panel_csv.PanelCsvError, match="RA.*DEC|DEC.*RA|RA"):
        panel_csv.parse_csv("Name,Notes\nx,y\n")
    with pytest.raises(panel_csv.PanelCsvError, match="NUL|control"):
        panel_csv.parse_csv(_write(_one()).replace("Pane", "Pa\x00ne"))


def test_an_oversize_file_and_too_many_panes_are_refused():
    big = "# " + "x" * panel_csv.MAX_TEXT_CHARS + "\n" + _write(_one())
    with pytest.raises(panel_csv.PanelCsvError, match="larger"):
        panel_csv.parse_csv(big)
    many = [{**_one()[0], "Row": "1", "Column": "1"}] * (GRID_MAX * GRID_MAX + 1)
    with pytest.raises(panel_csv.PanelCsvError, match="panes"):
        panel_csv.parse_csv(_write(many))


def test_a_file_with_no_overlap_or_angle_column_says_what_it_assumed():
    _, rows = _exported_rows({**SPEC, "rows": 1, "cols": 1})
    bare = [h for h in COLUMNS if h not in ("Overlap", "Position Angle (East)")]
    answer = panel_csv.parse_csv(_write(rows, bare))
    assert answer["draft"]["rotation"] == 0.0
    assert answer["draft"]["overlap"] == 0.0
    assert _warned(answer, "Position Angle"), answer["warnings"]
    assert _warned(answer, "Overlap"), answer["warnings"]


def test_overlap_as_a_fraction_a_percent_sign_and_over_the_limit():
    _, rows = _exported_rows({**SPEC, "rows": 1, "cols": 2})
    # A fraction: every value is at most 0.5, which no percent overlap is.
    frac = [{**r, "Overlap": "0.25"} for r in rows]
    assert panel_csv.parse_csv(_write(frac))["draft"]["overlap"] == 25.0
    # A percent sign says percent, even for 0.25.
    pct = [{**r, "Overlap": "0.25%"} for r in rows]
    assert panel_csv.parse_csv(_write(pct))["draft"]["overlap"] == 0.25
    # AstroDeck holds at most 50 percent: clamped, and said.
    over = [{**r, "Overlap": "60"} for r in rows]
    answer = panel_csv.parse_csv(_write(over))
    assert answer["draft"]["overlap"] == 50.0
    assert _warned(answer, "50"), answer["warnings"]


def test_the_answer_carries_the_convention_for_the_modal_to_show():
    answer = panel_csv.parse_csv(_write(_one()))
    assert answer["convention"] == panel_csv.CONVENTION
    assert sorted(answer["draft"]) == sorted(
        ["ra", "dec", "rows", "cols", "overlap", "fovX", "fovY", "fovFrom",
         "rotation", "skip"])
    assert isinstance(answer["draft"]["ra"], str)
    assert isinstance(answer["draft"]["dec"], str)


# --------------------------------------------------------------- the routes

class _Fake:
    name = "fake"

    def __init__(self, principal):
        self._principal = principal

    async def resolve(self, request):
        return self._principal


@pytest.fixture(autouse=True)
def _reset_provider_after():
    yield
    reset_active_provider()


@pytest.fixture
def client():
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c


def test_the_export_route_answers_the_file_with_a_download_name(client):
    body = {**SPEC, "skip": "2-2"}
    r = client.post("/api/framing/mosaic/csv", json=body)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert 'attachment; filename="' in r.headers["content-disposition"]
    assert r.headers["x-panel-csv-convention"] == panel_csv.CONVENTION
    _, rows = _read(r.text)
    assert len(rows) == 3
    assert {x["Position Angle (East)"] for x in rows} == {"330.0"}


def test_the_import_route_answers_the_draft_the_warnings_and_the_convention(client):
    text, _ = _exported_rows(SPEC)
    r = client.post("/api/framing/mosaic/import", json={"text": text})
    assert r.status_code == 200, r.text
    answer = r.json()
    assert answer["draft"]["rotation"] == 30.0
    assert answer["warnings"] == []
    assert answer["convention"] == panel_csv.CONVENTION


def test_a_json_nan_or_infinity_is_a_422_not_a_file(client):
    """Python's json accepts the tokens NaN and Infinity, so the route sees
    them: an angle of NaN or a field of infinity is refused with its reason,
    never written into a file."""
    import json
    for field, token in (("rotation_deg", "NaN"), ("fov_x_deg", "Infinity")):
        raw = json.dumps({**SPEC, field: "@@"}).replace('"@@"', token)
        r = client.post("/api/framing/mosaic/csv", content=raw,
                        headers={"Content-Type": "application/json"})
        assert r.status_code == 422, (field, r.text)
        assert "text/csv" not in r.headers.get("content-type", ""), field


def test_the_routes_refuse_what_is_not_a_file_with_the_reason(client):
    bad = client.post("/api/framing/mosaic/import", json={"text": "Name,Notes\nx,y\n"})
    assert bad.status_code == 422
    assert "RA" in bad.text and "DEC" in bad.text
    huge = client.post("/api/framing/mosaic/import",
                       json={"text": "x" * (panel_csv.MAX_TEXT_CHARS + 1)})
    assert huge.status_code == 422
    noangle = client.post("/api/framing/mosaic/csv",
                          json={**SPEC, "rotation_deg": -1})
    assert noangle.status_code == 422
    assert "angle" in noangle.text


def test_a_viewer_may_use_both_routes_because_nothing_here_is_site_derived(client):
    """view.status is enough and is what the routes declare and enforce:
    the answer is a function of the caller's own numbers and of no site.

    Mutant "gate raised" (the export route requires ``view.site_derived``)
    failed here, the viewer was refused:
       assert 403 == 200
    """
    set_active_provider(_Fake(principal_for_role("viewer")))
    text, _ = _exported_rows(SPEC)
    assert client.post("/api/framing/mosaic/import", json={"text": text}).status_code == 200
    assert client.post("/api/framing/mosaic/csv", json=SPEC).status_code == 200
    ours = [r for r in rbac.iter_app_routes(client.app)
            if getattr(r, "path", "") in ("/api/framing/mosaic/csv",
                                          "/api/framing/mosaic/import")]
    assert len(ours) == 2
    for route in ours:
        assert rbac._dependency_caps(route) == {CAP_VIEW_STATUS}, route.path
        assert rbac._marker_caps(route) == {CAP_VIEW_STATUS}, route.path
