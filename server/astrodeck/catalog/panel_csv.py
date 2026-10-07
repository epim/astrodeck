# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Panel CSV import and export, in the Telescopius shape (#178).

An operator who plans a mosaic elsewhere used to retype it, and the angle was
the likeliest thing to get wrong. This module converts a block's panels to and
from a file with the columns ``Pane, RA, DEC, Position Angle (East), width,
height, Overlap, Row, Column``. It is PURE (text in, text out, no I/O) and is
the one place that holds every number and the angle convention, so the angle
cannot be wrong in two places: the modal only moves text, and the two routes
(``api/app.py``) only call it.

THE ANGLE CONVENTION, STATED ONCE. AstroDeck's ``rotation_deg`` increases from
north through west (the sense of FITS CROTA2); the file's ``Position Angle
(East)`` is the position angle east of north, which increases from north
through east. Each is 360 minus the other, mod 360, so ``pa_east_from_rotation``
and ``rotation_from_pa_east`` are the same function (``_turn(-x)``) and a
layout at 30 is written 330 and read back as 30.

NINA reports a third number again, 180 minus the rotation (#145), a half turn
from this one. The footprint of a centred rectangle is the same either way
(``angle_check`` compares angles mod 180) but the top of the frame is not, so a
file whose angle puts the top toward the south is imported with a warning
rather than silently (``HALF_TURN_WARNING``).

The export keeps the angle mod 360 (30 -> 330 -> 30) and writes the BLOCK'S
layout angle, not any per-panel angle a later change adds: this file describes
one rigid grid at one angle.

WHAT IS NOT KNOWN YET. No real Telescopius export has been checked, so four
things are decided here by assumption and every one is said where it is made
(a warning on import, a ``# units:`` line on export):

  * RA and DEC text: sexagesimal (``00h 42m 44s``, ``00:42:44``) or decimal
    are read; decimal RA is hours, or degrees when the header says so or a
    value is past 24. We WRITE ``hh:mm:ss.sssss`` and ``+dd:mm:ss.ssss``.
  * the unit of width and height: read from the header text (``width
    (arcmins)``) or the ``# units:`` line, else degrees, said. We write degrees.
  * Row and Column: 1-based, row 1 at the NORTH edge and column 1 at the WEST
    edge of the grid at angle 0, which is AstroDeck's own labelling
    (``naming.panel_label``). A file that counts from 0 is read as such; a
    file that numbers from the other edges does not fit its own grid and says
    so, as any mis-laid file does.
  * Overlap: a percent on export; on import ``%``, the header or the
    ``# units:`` line decide, else a column whose values are all at most 0.5 is
    a fraction (no percent overlap is below one half of one percent in a
    mosaic) and anything else a percent.

THE GRID FROM THE PANES. AstroDeck holds a RIGID grid (rows x cols at one
angle, one overlap) with skipped cells. The centre is fitted, not averaged:
project every pane about the current centre, subtract where its cell lies in
that layout (``compute_mosaic``, the one projection), average the difference
and move the centre by it; repeat until it stops moving. On a full grid the
cell offsets average to zero, which is "project all pane centres about their
mean and average"; with a skipped cell they do not, and a plain mean of three
panes of a 2x2 is a quarter step off. The plain mean of RA and Dec is wrong
twice more: RA degrees shrink by cos(Dec), and across 0h the mean of 23.9995
and 0.0005 is 12. The fit is then VERIFIED: the derived grid is laid out and
each given pane compared with its cell, and a file that is not a rigid grid is
imported as the nearest one and says by how much (``RIGID_TOLERANCE``).
"""
from __future__ import annotations

import csv
import io
import math
import re
from collections import Counter
from collections.abc import Iterable

from pydantic import BaseModel, Field

from ..flows.compile import parse_skip
from ..flows.to_plan import GRID_MAX, OVERLAP_MAX_PCT
from . import coords
from .framing import MosaicSpecIn, _wrap_ra_hours, compute_mosaic, deproject, project

#: The columns, in the file's own order. The names are the contract: the
#: importer reads a header by what it contains, and the exporter writes these.
COLUMNS = ("Pane", "RA", "DEC", "Position Angle (East)", "width", "height",
           "Overlap", "Row", "Column")

#: The convention, as the sentence the file's leading ``#`` lines, the export
#: route's header and the import answer all carry. The modal shows THIS text
#: and keeps no copy.
CONVENTION = (
    "Position Angle (East) is the position angle east of north: it increases "
    "from north through east. AstroDeck's rotation increases from north "
    "through west, so rotation = 360 - Position Angle (East); a rotation of "
    "30 is written 330 and read back as 30.")

#: Said when a file's angle puts the top of the frame toward the south.
HALF_TURN_WARNING = (
    "Position Angle (East) {pa} puts the top of the frame toward the south "
    "(AstroDeck rotation {rotation}). The panels land in the same place at "
    "the opposite half turn, so a file from a tool that reports 180 minus "
    "the rotation, as NINA does, is a half turn off here: check the angle "
    "against the camera.")

#: The text of a file is capped: a 10 x 10 grid is about 12 KB.
MAX_TEXT_CHARS = 65_536
#: The panes of a file: a full grid at the limit. More are duplicates or a
#: grid AstroDeck cannot hold.
MAX_PANES = GRID_MAX * GRID_MAX

#: How far a given pane may sit from its cell of the derived grid before the
#: file is "not a rigid grid": 2 percent of the smaller field.
RIGID_TOLERANCE = 0.02

#: A camera field wider than this is not a tile of the sky's tangent plane.
FOV_MAX_DEG = 60.0

#: Provenance the imported field is labelled with in the modal's camera line.
FOV_FROM = "imported from a panel file"

#: The angle is held to a millionth of a degree (0.0036 arcsec of rotation):
#: far below any rotator, and it makes 12.3 -> 347.7 -> 12.3 return the same
#: number instead of 12.300000000000011.
ANGLE_PLACES = 6

#: The fit of the grid centre moves it by the mean residual until it moves
#: less than this, at most this many times. Two or three passes settle it; the
#: cap only bounds a file that is nowhere near a grid.
_FIT_ITERATIONS = 25
_FIT_SETTLED_DEG = 1e-10

#: Sexagesimal precision on export: 1e-5 second of time (1.5e-4 arcsec) and
#: 1e-4 arcsec, so a round trip returns every panel centre to well under a
#: millionth of a degree.
_RA_TICKS = 10 ** 5
_DEC_TICKS = 10 ** 4

_SIZE_FACTORS = {"degrees": 1.0, "arcmin": 1.0 / 60.0, "arcsec": 1.0 / 3600.0}


class PanelCsvError(ValueError):
    """A file or a layout that cannot be a panel file; the message says what
    is wrong, and the routes answer it as a 422."""


# ------------------------------------------------------------ route bodies

class PanelCsvExportIn(MosaicSpecIn):
    """``POST /api/framing/mosaic/csv``: the layout as ``MosaicSpecIn`` takes
    it, plus the block's ``skip`` text. ``date``, ``transit_alt`` and
    ``anchor`` are accepted (it is the modal's own request) and never read:
    nothing in the file is a function of the site."""
    skip: str = Field("", max_length=4000)


class PanelCsvImportIn(BaseModel):
    """``POST /api/framing/mosaic/import``. The size is checked by
    ``parse_csv`` (``MAX_TEXT_CHARS``), so an oversize file is refused with
    its reason and a body model does not echo 64 KB back in a 422."""
    text: str


# ------------------------------------------------------------- the angle

def _turn(deg: float) -> float:
    """``deg`` in [0, 360), to ``ANGLE_PLACES`` (360.0 folds to 0.0, and a
    negative zero to a plain one)."""
    r = round(deg % 360.0, ANGLE_PLACES)
    return 0.0 if r >= 360.0 else r + 0.0


def pa_east_from_rotation(rotation_deg: float) -> float:
    """The file's angle for an AstroDeck rotation: 30 -> 330."""
    return _turn(-rotation_deg)


def rotation_from_pa_east(pa_east_deg: float) -> float:
    """AstroDeck's rotation for a file's angle: 330 -> 30."""
    return _turn(-pa_east_deg)


# --------------------------------------------------------------- text forms

def _trim(value: float, places: int) -> str:
    """``value`` with trailing zeros dropped and one decimal kept: 330.0."""
    s = f"{value:.{places}f}".rstrip("0")
    return s + "0" if s.endswith(".") else s


def _ra_parts(hours: float) -> tuple[int, int, str]:
    ticks = round((hours % 24.0) * 3600 * _RA_TICKS) % (24 * 3600 * _RA_TICKS)
    h, rem = divmod(ticks, 3600 * _RA_TICKS)
    m, rem = divmod(rem, 60 * _RA_TICKS)
    return h, m, f"{rem / _RA_TICKS:08.5f}"


def _dec_parts(deg: float) -> tuple[str, int, int, str]:
    ticks = round(abs(deg) * 3600 * _DEC_TICKS)
    d, rem = divmod(ticks, 3600 * _DEC_TICKS)
    m, rem = divmod(rem, 60 * _DEC_TICKS)
    return "-" if deg < 0 and ticks else "+", d, m, f"{rem / _DEC_TICKS:07.4f}"


def _file_ra(hours: float) -> str:
    h, m, s = _ra_parts(hours)
    return f"{h:02d}:{m:02d}:{s}"


def _file_dec(deg: float) -> str:
    sign, d, m, s = _dec_parts(deg)
    return f"{sign}{d:02d}:{m:02d}:{s}"


def _node_ra(hours: float) -> str:
    """RA as a TARGET node stores it (``coords.parse_ra`` and the modal's
    mirror both read it): ``00h 42m 44.31000s``."""
    h, m, s = _ra_parts(hours)
    return f"{h:02d}h {m:02d}m {s}s"


def _node_dec(deg: float) -> str:
    """Dec as a TARGET node stores it: ``+41 deg 16' 08.9000"`` with the
    degree sign (U+00B0), as ``coords.parse_dec`` reads it."""
    sign, d, m, s = _dec_parts(deg)
    return f"{sign}{d:02d}\u00b0 {m:02d}' {s}\""


# ------------------------------------------------------------------ export

def export_csv(spec: MosaicSpecIn, skip: str | Iterable[tuple[int, int]] = "") -> str:
    """The layout as a panel file: ``COLUMNS``, one row per SHOT panel in the
    file's own row-major order, under ``#`` lines that state the convention
    and the units (this importer skips them).

    The panels come from ``compute_mosaic``, the one projection; ``skip`` is
    the block's skip text (1-based ``r-c``, read by ``compile.parse_skip``) or
    its pairs, and a skipped panel is left out, so its cell is the file's
    missing cell. The angle is the block's layout angle in the file's sense.
    A layout with no angle (negative: "any angle"), a non-finite one, or no
    panel left to write is refused: a file of panes with no angle is a
    guess."""
    rotation = float(spec.rotation_deg)
    if not math.isfinite(rotation) or rotation < 0.0:
        raise PanelCsvError(
            "the layout has no angle to write (any angle, or not a number): "
            "choose a camera angle first, the file carries a position angle")
    if not (math.isfinite(spec.fov_x_deg) and math.isfinite(spec.fov_y_deg)):
        raise PanelCsvError("the camera field is not a finite number")
    rows, cols = max(1, round(spec.rows)), max(1, round(spec.cols))
    if isinstance(skip, str):
        skipped = {tuple(rc) for rc in parse_skip(skip, rows, cols)[0]}
    else:
        skipped = {(int(r), int(c)) for r, c in skip}
    overlap = min(0.5, max(0.0, spec.overlap))
    panels = sorted(
        (p for p in compute_mosaic(spec)["panels"]
         if (p["row"] + 1, p["col"] + 1) not in skipped),
        key=lambda p: (p["row"], p["col"]))
    if not panels:
        raise PanelCsvError("every panel is skipped, so there is nothing to write")

    pa = _trim(pa_east_from_rotation(rotation), ANGLE_PLACES)
    out = io.StringIO()
    out.write("# AstroDeck panel file in the Telescopius column shape (issue 178).\n")
    out.write(f"# {CONVENTION}\n")
    out.write("# RA and DEC are J2000, hh:mm:ss.sssss and +dd:mm:ss.ssss. Row and "
              "Column count from 1: row 1 is the north edge and column 1 the "
              "west edge of the grid at angle 0.\n")
    out.write("# units: ra=hms width=degrees height=degrees overlap=percent\n")
    w = csv.writer(out, lineterminator="\n")
    w.writerow(COLUMNS)
    for n, p in enumerate(panels, 1):
        w.writerow([
            n, _file_ra(p["ra_hours"]), _file_dec(p["dec_deg"]), pa,
            _trim(spec.fov_x_deg, 9), _trim(spec.fov_y_deg, 9),
            _trim(overlap * 100.0, 9), p["row"] + 1, p["col"] + 1])
    return out.getvalue()


# ------------------------------------------------------------------ import

#: Columns a file must have, by the kind ``_classify`` gives a header.
_REQUIRED = (("ra", "RA"), ("dec", "DEC"), ("width", "width"),
             ("height", "height"), ("row", "Row"), ("column", "Column"))


def _classify(header: str) -> str | None:
    """What a header names: ``pa``, ``width``, ``height``, ``overlap``,
    ``row``, ``column``, ``ra``, ``dec``, ``pane`` or None. Read from the
    name without its bracketed unit, so ``Pane width (arcmins)`` is a width
    and not a pane."""
    name = re.sub(r"[\(\[].*?[\)\]]", " ", header.lower())
    name = re.sub(r"[\s_.:-]+", " ", name).strip()
    if "position angle" in name or name in ("pa", "angle") or name.startswith("pa "):
        return "pa"
    if "width" in name:
        return "width"
    if "height" in name:
        return "height"
    if "overlap" in name:
        return "overlap"
    if name.startswith("row"):
        return "row"
    if name.startswith("col"):
        return "column"
    if name == "ra" or name.startswith("ra ") or "right ascension" in name:
        return "ra"
    if name in ("dec", "de") or name.startswith("dec ") or "declination" in name:
        return "dec"
    if name.startswith("pane") or name in ("panel", "name", "id", "#"):
        return "pane"
    return None


def _header_unit(header: str) -> str | None:
    """The unit a header's text names, or None."""
    h = header.lower()
    if re.search(r"arc\s*sec|\u2033|\"", h):
        return "arcsec"
    if re.search(r"arc\s*min|\bmins?\b|\u2032|'", h):
        return "arcmin"
    if re.search(r"deg|\u00b0", h):
        return "degrees"
    if re.search(r"hour|\bhrs?\b|\bhms\b", h):
        return "hours"
    if "%" in h or re.search(r"percent|pct", h):
        return "percent"
    if re.search(r"fraction|ratio", h):
        return "fraction"
    return None


def _units_line(comments: list[str]) -> dict[str, str]:
    """The ``# units: key=value ...`` line of a file, as {key: value}."""
    for c in comments:
        if c.lower().startswith("units:"):
            return {k.lower(): v.lower() for k, v in
                    re.findall(r"(\w+)\s*=\s*([\w%]+)", c)}
    return {}


def _number(text: str, what: str, where: str) -> float:
    """``text`` as a finite number: 0-9 only (as ``coords`` reads digits,
    #359), and neither NaN nor an infinity (the #324 rule)."""
    t = text.strip()
    try:
        if not t.isascii():
            raise ValueError
        value = float(t)
    except ValueError:
        raise PanelCsvError(f"{where}: {what} {text!r} is not a number") from None
    if not math.isfinite(value):
        raise PanelCsvError(f"{where}: {what} {text!r} is not a finite number")
    return value


def _whole(text: str, what: str, where: str) -> int:
    value = _number(text, what, where)
    if not value.is_integer():
        raise PanelCsvError(f"{where}: {what} {text!r} is not a whole number")
    if value < 0:
        raise PanelCsvError(f"{where}: {what} {text!r} is below 0")
    return int(value)


def _ra_cell(text: str, where: str) -> tuple[str, float]:
    """``("hms", hours)`` for a sexagesimal RA, ``("decimal", value)`` for a
    plain number whose unit is the file's to decide."""
    t = text.strip()
    if re.search(r"[hms:\s]", t):
        try:
            hours = coords.parse_ra(t)
        except ValueError:
            raise PanelCsvError(f"{where}: RA {text!r} is not a time or a number") from None
        if not math.isfinite(hours):
            raise PanelCsvError(f"{where}: RA {text!r} is not a finite number")
        return "hms", hours
    return "decimal", _number(t, "RA", where)


def _dec_cell(text: str, where: str) -> float:
    try:
        deg = coords.parse_dec(text)
    except ValueError:
        raise PanelCsvError(f"{where}: DEC {text!r} is not an angle or a number") from None
    if not math.isfinite(deg):
        raise PanelCsvError(f"{where}: DEC {text!r} is not a finite number")
    if not -90.0 <= deg <= 90.0:
        raise PanelCsvError(f"{where}: DEC {text!r} is outside -90 to 90 degrees")
    return deg


def _most_common(values: list):
    """The most common value, the first seen among ties, and whether the
    values were not all one."""
    counts = Counter(values)
    return counts.most_common(1)[0][0], len(counts) > 1


def _mean_direction(points: list[tuple[float, float]]) -> tuple[float, float]:
    """The mean of ``(ra_hours, dec_deg)`` points as a direction on the
    sphere: right across 0h and at any declination, where averaging RA and
    Dec is not."""
    x = y = z = 0.0
    for ra_h, dec in points:
        ra, d = math.radians(ra_h * 15.0), math.radians(dec)
        x += math.cos(d) * math.cos(ra)
        y += math.cos(d) * math.sin(ra)
        z += math.sin(d)
    norm = math.sqrt(x * x + y * y + z * z)
    if norm < 1e-9:
        raise PanelCsvError("the panes do not lie in one part of the sky")
    return (_wrap_ra_hours(math.degrees(math.atan2(y, x)) / 15.0),
            math.degrees(math.asin(max(-1.0, min(1.0, z / norm)))))


def _layout(ra_h: float, dec: float, layout: dict) -> dict:
    """The cells of the layout about ``(ra_h, dec)``, ``{(row, col):
    (ra_hours, dec_deg)}`` 0-based, from ``compute_mosaic``."""
    spec = MosaicSpecIn(ra_hours=ra_h, dec_deg=dec, **layout)
    return {(p["row"], p["col"]): (p["ra_hours"], p["dec_deg"])
            for p in compute_mosaic(spec)["panels"]}


def _fit_centre(given: dict, layout: dict) -> tuple[float, float]:
    """The grid centre whose layout best matches the given panes (see the
    module docstring): from their mean direction, move by the mean difference
    between each pane and its cell, on the tangent plane about the current
    centre, until it settles."""
    ra0, dec0 = _mean_direction(list(given.values()))
    for _ in range(_FIT_ITERATIONS):
        cells = _layout(ra0, dec0, layout)
        dxi = deta = 0.0
        for cell, (ra_h, dec) in given.items():
            gx, gy = project(ra_h, dec, ra0, dec0)
            px, py = project(*cells[cell], ra0, dec0)
            dxi += gx - px
            deta += gy - py
        dxi /= len(given)
        deta /= len(given)
        if math.hypot(dxi, deta) < _FIT_SETTLED_DEG:
            break
        ra0, dec0 = deproject(dxi, deta, ra0, dec0)
    return ra0, dec0


def _cells_text(cells: list[tuple[int, int]]) -> str:
    return ", ".join(f"{r}-{c}" for r, c in cells)


def parse_csv(text: str) -> dict:
    """A panel file as a draft of a TARGET node's params and the warnings that
    came with it: ``{"draft": {...}, "warnings": [...], "convention": ...}``.

    The draft is in the node's own shape (``ra`` and ``dec`` as text, ``rows``,
    ``cols``, ``overlap`` in percent, ``fovX``/``fovY`` in degrees,
    ``rotation`` in AstroDeck's sense, ``skip`` as text, ``fovFrom``). Rows are
    taken in any order (the file's is row-major, ``compute_mosaic``'s a
    snake). Raises ``PanelCsvError`` for a file that cannot be a grid: past
    ``GRID_MAX`` a side, a non-finite number, no panes, an oversize file."""
    if not isinstance(text, str):
        raise PanelCsvError("the file is not text")
    if len(text) > MAX_TEXT_CHARS:
        raise PanelCsvError(f"the file is larger than {MAX_TEXT_CHARS} characters, "
                            f"which no panel file is")
    if "\x00" in text:
        raise PanelCsvError("the file holds a NUL character, so it is not a text file")
    warnings: list[str] = []

    numbered: list[tuple[int, str]] = []
    comments: list[str] = []
    for n, line in enumerate(re.split(r"\r\n|\r|\n", text.lstrip("\ufeff")), 1):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            comments.append(stripped.lstrip("#").strip())
        else:
            numbered.append((n, line))
    if len(numbered) < 2:
        raise PanelCsvError("the file holds no panes: it needs a header row and "
                            "a row for each pane")
    if len(numbered) - 1 > MAX_PANES:
        raise PanelCsvError(f"the file lists {len(numbered) - 1} panes; a grid of "
                            f"{GRID_MAX} by {GRID_MAX} has at most {MAX_PANES}")

    head = numbered[0][1]
    delimiter = max((",", ";", "\t"), key=head.count)
    reader = csv.reader([line for _, line in numbered], delimiter=delimiter,
                        skipinitialspace=True)
    try:
        rows = list(reader)
    except csv.Error as e:
        raise PanelCsvError(f"the file is not a table: {e}") from None
    header = [h.strip() for h in rows[0]]
    column: dict[str, int] = {}
    for i, h in enumerate(header):
        kind = _classify(h)
        if kind is not None:
            column.setdefault(kind, i)
    missing = [label for kind, label in _REQUIRED if kind not in column]
    if missing:
        raise PanelCsvError(f"the file has no {', '.join(missing)} column; it needs "
                            f"{', '.join(label for _, label in _REQUIRED)}")

    declared = {k: _header_unit(v) or v for k, v in _units_line(comments).items()}

    def unit_of(kind: str) -> str | None:
        return (_header_unit(header[column[kind]]) if kind in column else None) \
            or declared.get(kind)

    # ---- the panes, cell by cell
    panes: list[dict] = []
    for i, fields in enumerate(rows[1:], 1):
        if not any(f.strip() for f in fields):
            continue
        where = f"line {numbered[i][0]}"

        def cell(kind: str, _fields=fields) -> str:
            j = column.get(kind)
            return _fields[j].strip() if j is not None and j < len(_fields) else ""

        for kind, label in _REQUIRED:
            if cell(kind) == "":
                raise PanelCsvError(f"{where}: {label} is blank")
        ra_kind, ra_value = _ra_cell(cell("ra"), where)
        overlap_text = cell("overlap")
        panes.append({
            "where": where,
            "row": _whole(cell("row"), "Row", where),
            "col": _whole(cell("column"), "Column", where),
            "ra_kind": ra_kind, "ra": ra_value,
            "dec": _dec_cell(cell("dec"), where),
            "width": _number(cell("width"), "width", where),
            "height": _number(cell("height"), "height", where),
            "pa": (_number(cell("pa"), "Position Angle (East)", where)
                   if cell("pa") else None),
            "overlap": (_number(overlap_text.rstrip("% "), "Overlap", where)
                        if overlap_text else None),
            "overlap_pct_sign": overlap_text.endswith("%"),
        })
    if not panes:
        raise PanelCsvError("the file holds no panes: every row is blank")

    # ---- RA: decimal values are hours unless said or past 24
    ra_unit = unit_of("ra")
    decimals = [p["ra"] for p in panes if p["ra_kind"] == "decimal"]
    if decimals:
        if ra_unit not in ("hours", "degrees"):
            if max(decimals) > 24.0:
                ra_unit = "degrees"
                warnings.append("RA has no unit and a value past 24, which can only "
                                "be degrees: its decimals are read as degrees")
            else:
                ra_unit = "hours"
                warnings.append("RA has no unit: its decimals are read as hours")
    for p in panes:
        hours = p["ra"] / 15.0 if p["ra_kind"] == "decimal" and ra_unit == "degrees" \
            else p["ra"]
        if not 0.0 <= hours <= 24.0:
            raise PanelCsvError(f"{p['where']}: RA is outside 0 to 24 hours")
        p["ra_h"] = _wrap_ra_hours(hours)

    # ---- the field: width and height, each in its own unit
    assumed = []
    for kind in ("width", "height"):
        unit = unit_of(kind)
        if unit not in _SIZE_FACTORS:
            unit = "degrees"
            assumed.append(kind)
        for p in panes:
            value = p[kind]
            if not value > 0.0:
                raise PanelCsvError(f"{p['where']}: {kind} must be above 0")
            deg = round(value * _SIZE_FACTORS[unit], 9)
            # Checked AFTER the rounding: a width of 1e-300, or 1e-6 arcsec,
            # is above 0 as written and 0.0 as held, and a field of 0 is what
            # MosaicSpecIn refuses (gt=0) from inside the fit, as a 500.
            if not deg > 0.0:
                raise PanelCsvError(f"{p['where']}: {kind} {p[kind]:g} is too small "
                                    f"to be a camera field")
            p[kind + "_deg"] = deg
    for kind in ("width", "height"):
        widest = max(p[kind + "_deg"] for p in panes)
        if widest > FOV_MAX_DEG:
            hint = " (the file gave no unit: is it in arcminutes?)" if kind in assumed else ""
            raise PanelCsvError(f"{kind} {widest:g} degrees is wider than the "
                                f"{FOV_MAX_DEG:g} degrees a camera field can be{hint}")
    if assumed:
        warnings.append(
            f"{' and '.join(assumed)} carry{'s' if len(assumed) == 1 else ''} no unit, "
            f"so {'it is' if len(assumed) == 1 else 'they are'} read as degrees "
            f"(a header such as 'width (arcmins)' or a '# units: width=arcmin "
            f"height=arcmin' line says otherwise)")
    (fov_x, fov_y), field_differs = _most_common(
        [(p["width_deg"], p["height_deg"]) for p in panes])
    if field_differs:
        warnings.append(f"the panes carry different widths or heights: the most "
                        f"common, {fov_x:g} x {fov_y:g} degrees, is used")

    # ---- the angle
    pas = [round(p["pa"], ANGLE_PLACES) for p in panes if p["pa"] is not None]
    if pas:
        pa_east, angle_differs = _most_common(pas)
        if angle_differs:
            warnings.append(f"the panes carry {len(set(pas))} different angles: the "
                            f"most common, {pa_east:g} east of north, is used")
    else:
        pa_east = 0.0
        warnings.append("the file has no Position Angle (East) values: the grid is "
                        "laid out at 0 (north up)")
    rotation = rotation_from_pa_east(pa_east)
    if 90.0 < _turn(pa_east) < 270.0:
        warnings.append(HALF_TURN_WARNING.format(pa=f"{pa_east:g}", rotation=f"{rotation:g}"))

    # ---- the overlap, to a percent
    given_overlap = [p for p in panes if p["overlap"] is not None]
    if given_overlap:
        unit = unit_of("overlap")
        values = [p["overlap"] for p in given_overlap]
        if unit not in ("percent", "fraction"):
            if any(p["overlap_pct_sign"] for p in given_overlap):
                unit = "percent"
            elif 0.0 < max(values) <= 0.5:
                unit = "fraction"
                warnings.append("Overlap has no unit and no value above 0.5: it is read "
                                "as a fraction (0.25 is 25 percent)")
            else:
                unit = "percent"
        pct_values = [round(v * 100.0 if unit == "fraction" else v, 9) for v in values]
        if min(pct_values) < 0.0:
            raise PanelCsvError(f"Overlap {min(values):g} is below 0")
        overlap_pct, overlap_differs = _most_common(pct_values)
        if overlap_differs:
            warnings.append(f"the panes carry different overlaps: the most common, "
                            f"{overlap_pct:g} percent, is used")
        if overlap_pct > OVERLAP_MAX_PCT:
            warnings.append(f"an overlap of {overlap_pct:g} percent is more than "
                            f"AstroDeck holds: it is set to {OVERLAP_MAX_PCT:g} percent")
            overlap_pct = OVERLAP_MAX_PCT
    else:
        overlap_pct = 0.0
        warnings.append("the file has no Overlap values: the overlap is taken as 0 percent")

    # ---- the grid: cells, counting from 0 or 1, one pane each
    base_row = 0 if min(p["row"] for p in panes) == 0 else 1
    base_col = 0 if min(p["col"] for p in panes) == 0 else 1
    if base_row == 0 or base_col == 0:
        warnings.append("Row and Column count from 0 in this file: they are read from 1 here")
    n_rows = max(p["row"] for p in panes) - base_row + 1
    n_cols = max(p["col"] for p in panes) - base_col + 1
    if n_rows > GRID_MAX or n_cols > GRID_MAX:
        raise PanelCsvError(f"the file's grid is {n_cols} columns by {n_rows} rows; "
                            f"AstroDeck holds at most {GRID_MAX} a side")
    given: dict[tuple[int, int], tuple[float, float]] = {}
    twice: list[tuple[int, int]] = []
    for p in panes:
        cell_rc = (p["row"] - base_row, p["col"] - base_col)
        if cell_rc in given:
            twice.append((cell_rc[0] + 1, cell_rc[1] + 1))
        else:
            given[cell_rc] = (p["ra_h"], p["dec"])
    if twice:
        warnings.append(f"the file has more than one pane at {_cells_text(twice)}: "
                        f"the first of each is used")
    absent = [(r + 1, c + 1) for r in range(n_rows) for c in range(n_cols)
              if (r, c) not in given]
    if absent:
        warnings.append(f"the file has no pane at {_cells_text(absent)}: "
                        f"{'that cell is' if len(absent) == 1 else 'those cells are'} "
                        f"set to skip")

    # ---- the centre, then the check that the file was a rigid grid
    layout = dict(rows=n_rows, cols=n_cols, overlap=overlap_pct / 100.0,
                  rotation_deg=rotation, fov_x_deg=fov_x, fov_y_deg=fov_y)
    ra0, dec0 = _fit_centre(given, layout)
    cells = _layout(ra0, dec0, layout)
    off = [math.hypot(*project(ra_h, dec, *cells[cell]))
           for cell, (ra_h, dec) in given.items()]
    limit = RIGID_TOLERANCE * min(fov_x, fov_y)
    far = [d for d in off if d > limit]
    if far:
        warnings.append(
            f"these panes are not a rigid grid; imported as the nearest grid, "
            f"{len(far)} pane{'' if len(far) == 1 else 's'} off by up to "
            f"{max(far) * 60.0:.1f} arcmin")

    return {
        "draft": {
            "ra": _node_ra(ra0), "dec": _node_dec(dec0),
            "rows": n_rows, "cols": n_cols, "overlap": float(overlap_pct),
            "fovX": fov_x, "fovY": fov_y, "fovFrom": FOV_FROM,
            "rotation": rotation, "skip": _cells_text(absent),
        },
        "warnings": warnings,
        "convention": CONVENTION,
    }
