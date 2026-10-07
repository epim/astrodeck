# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Regenerate astrodeck/catalog/data/ngc_extras.tsv from OpenNGC's own CSVs —
the columns tools/build_ngc_catalog.py reads and then throws away.

WHY THIS EXISTS. ``ngc.tsv`` keeps 8 of OpenNGC's 32 columns (id, common name,
type, position, magnitude, size, alias) — everything a telescope needs to
slew somewhere. It drops everything a *description* needs. Per
docs/superpowers/backlog/2026-08-08-object-description-sources.md (the survey
this implements), four of the discarded columns are worth a second look
(and #181 added a fifth, ``PosAng``, below):

  * ``Hubble``  — 30 distinct galaxy-subtype codes across 10,000+ rows. Turns
    "galaxy in Draco" into "spiral galaxy in Draco" — the single best return
    in the survey.
  * ``MinAx``   — paired with ``MajAx`` (already carried, as ``size_arcmin``
    on the ``DSO`` dataclass — NOT duplicated here), gives an axis ratio.
    describe.py uses ratio >= 3 to say "edge-on".
  * ``PosAng``  — (#181) the position angle of the major axis, north through
    east. With MinAx/MajAx it is the object's SHAPE, which the Atlas draws as
    an ellipse and SUGGEST GRID tiles by. Carried as two more columns,
    ``axis_ratio`` and ``posang_deg``, written by ``shape_columns`` below.
  * ``Redshift``— converts to a distance. 376 rows are BLUESHIFTED (Local
    Group / Virgo infall); describe.py is the one that guards against a
    negative or noise-dominated distance, this script just carries the raw
    z value through unfiltered.
  * ``NED notes``— 17% of rows carry a real English sentence from NED. Most
    of it is survey-plumbing noise (HIPASS/SDSS cross-match trivia); an
    ALLOW-LIST here keeps only the two families a beginner should be told
    about: the honest "there is nothing at this position" / "this ID is not
    certain" admissions, and "this is in a Magellanic Cloud" placements.
    Everything else in that column is dropped, not shipped wholesale.

Deliberately NOT re-imported: ``Const`` (ours is already computed by
build_constellations.py and agrees on 13,362/13,369 — see that script's
sibling test for the 7 boundary disagreements), ``SurfBr`` (no consumer
today), and the *rest* of ``Common names`` beyond the
first (that is objects.py's search-alias territory, out of this task's scope
and out of this file's).

WHY A SIDECAR, NOT NEW COLUMNS BOLTED ONTO ngc.tsv. Same reasoning as
build_constellations.py: ngc.tsv is a straight OpenNGC extract with its own
CC BY-SA 4.0 notice, and objects.py's loader reads a fixed 7-or-8-column
shape from it. A second sidecar, keyed by the same ``id`` objects.CATALOG
already uses, covers every row uniformly without touching either.

THE JOIN. This script does NOT re-derive which OpenNGC row became which
catalog id — it reads that answer back out of ngc.tsv itself. Every Messier
row in ngc.tsv already carries its underlying NGC/IC designation in column 8
(``alias`` — see build_ngc_catalog.py: ``if messier: ident, alias =
f"M{messier}", ident``), and it is populated for ALL 109 Messier rows and NO
others (checked directly: 0 non-Messier rows have a non-empty alias). So the
raw-CSV lookup key for any id in ngc.tsv is simply ``alias or id`` — no need
to reimplement Messier-number bookkeeping or import anything from
server/tools/, which this task does not own.

Run from the ``server/`` directory, pointing at a fresh OpenNGC checkout
(same source as build_ngc_catalog.py):

    python -m astrodeck.catalog.build_ngc_extras <path-to-NGC.csv> [<path-to-addendum.csv>]

Fetch a fresh copy first:
    curl -LO https://raw.githubusercontent.com/mattiaverga/OpenNGC/master/database_files/NGC.csv
    curl -LO https://raw.githubusercontent.com/mattiaverga/OpenNGC/master/database_files/addendum.csv

No network in this script itself — same convention as build_ngc_catalog.py.
Output is deterministic (sorted by id), so a rebuild diffs cleanly.
"""
from __future__ import annotations

import csv
import math
import re
import sys
from pathlib import Path

DATA_DIR = Path(__file__).with_name("data")
NGC_TSV = DATA_DIR / "ngc.tsv"
OUT = DATA_DIR / "ngc_extras.tsv"


def designation(raw: str) -> str:
    """Identical to build_ngc_catalog.py's own helper (duplicated rather than
    imported — that module lives under server/tools/, out of this task's
    scope, and this is 5 lines). OpenNGC writes NGC0224/IC0434; ngc.tsv
    (and Wikidata's P528, separately) write NGC 224 / IC 434."""
    for prefix in ("NGC", "IC"):
        if raw.startswith(prefix):
            return f"{prefix} {raw[len(prefix):].lstrip('0') or '0'}"
    return raw


# ---------------------------------------------------------------------------
# NED notes: allow-list, not a denylist. The column is 17% populated and most
# of it ("Confused HIPASS source", "Multiple SDSS entries describe this
# object") is survey cross-match trivia that means nothing to someone reading
# a target card. These two families are what the survey found worth keeping:
#
#   1. Honest absence / doubtful identification — "Nothing here; nominal
#      position.", "NGC identification is not certain.", and the long tail of
#      per-object variants ("Identification as NGC 1109 is uncertain.",
#      "HOLM 264C does not exist (it is probably a plate defect)."). This is
#      the exact class of honesty describe.py already builds for elsewhere
#      (MAG_UNKNOWN, the "no constellation on file" defensive branch): an
#      object whose catalogue entry is probably wrong should say so before
#      someone spends an hour of clear sky on it.
#   2. Magellanic Cloud placement — "In the Large Magellanic Cloud.", "Within
#      boundaries of LMC" — real character, not noise.
# ---------------------------------------------------------------------------
_HONEST_ABSENCE_RE = re.compile(
    r"\bnothing (here|at this position|in this position|obvious here)\b"
    r"|\bdoes not exist\b"
    r"|\bis (very )?(not (certain|sure)|uncertain)\b",
    re.IGNORECASE,
)
_MAGELLANIC_RE = re.compile(r"magellanic cloud|\blmc\b|\bsmc\b", re.IGNORECASE)


def worth_surfacing(note: str) -> bool:
    """True for the two NED-note families the survey found worth a user's
    time; False for everything else (HIPASS/SDSS/APM/plate cross-match
    trivia, star-count asides, etc.), which is dropped rather than shipped."""
    return bool(_HONEST_ABSENCE_RE.search(note) or _MAGELLANIC_RE.search(note))


def _parse_axis(raw: str) -> float | None:
    """A MajAx/MinAx/PosAng cell as a finite float, or None for blank and for
    anything that is not a number (float() accepts "nan" and "inf")."""
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def shape_columns(majax_raw: str, minax_raw: str, posang_raw: str,
                  catalogue_size: float | None = None) -> tuple[str, str]:
    """The ``axis_ratio`` and ``posang_deg`` cells for one OpenNGC row (#181).

    ``axis_ratio`` is MinAx / MajAx to three places. It is a RATIO, not a minor
    axis, because the size the catalogue shows is not always OpenNGC's: the
    curated rows carry the sizes an imager uses (M31 is 190', OpenNGC says
    177.83'), and a minor axis taken from OpenNGC beside a major axis taken from
    the curated list would draw a different galaxy. objects.shape_of multiplies
    the ratio by whichever size is shown.

    ``posang_deg`` is PosAng folded onto [0, 180): an ellipse looks the same
    turned half a circle, and OpenNGC publishes 180 on 44 rows and 359 on one.
    A PosAng of 0 is a real angle (due north) and is written as "0.0"; only a
    missing or unreadable PosAng is the empty cell.

    Both are empty when there is nothing honest to say, and a circle is then
    the fallback:

      * either axis missing or zero: no ratio (the angle is its own cell and is
        kept if OpenNGC published one);
      * MinAx longer than MajAx: the axes are confused and a PosAng measured
        along "the major axis" cannot be trusted either, so BOTH cells are
        empty;
      * ``catalogue_size`` (the size ngc.tsv carries for this id, itself a
        MajAx) disagrees with this row's MajAx to ngc.tsv's two places: upstream
        changed the row after ngc.tsv was built, and a ratio from one snapshot
        on a size from another is a ratio of two different measurements. BOTH
        cells are empty. Found rebuilding this file from a current download:
        IC 2105 is 0.65' in ngc.tsv and 3.0' x 1.5' upstream today.
        ``None`` skips this check.
    """
    maj = _parse_axis(majax_raw)
    minor = _parse_axis(minax_raw)
    pa = _parse_axis(posang_raw)

    if catalogue_size is not None:
        # ngc.tsv writes a missing MajAx as 0.0, so compare like with like.
        if abs(round(maj or 0.0, 2) - catalogue_size) > 0.005:
            return "", ""

    ratio = ""
    if maj is not None and minor is not None and maj > 0 and minor > 0:
        if minor > maj:
            return "", ""
        rounded = round(minor / maj, 3)
        if rounded > 0:
            ratio = f"{rounded:.3f}"

    angle = ""
    if pa is not None:
        # A second % 180 after rounding: 179.96 rounds to 180.0, which is 0.
        angle = f"{round(pa % 180.0, 1) % 180.0:.1f}"
    return ratio, angle


def _load_join() -> dict[str, tuple[str, float | None]]:
    """id -> (the raw-CSV lookup key ``alias or id``, ngc.tsv's size_arcmin for
    that id or None if the cell is unreadable), read from ngc.tsv."""
    out: dict[str, tuple[str, float | None]] = {}
    with open(NGC_TSV, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.rstrip("\n").split("\t")
            ident = parts[0]
            alias = parts[7] if len(parts) > 7 else ""
            size = _parse_axis(parts[6]) if len(parts) > 6 else None
            out[ident] = (alias or ident, size)
    return out


def main(paths: list[str]) -> int:
    raw: dict[str, dict] = {}
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            for r in csv.DictReader(fh, delimiter=";"):
                raw[designation(r["Name"].strip())] = r

    join = _load_join()

    rows: dict[str, tuple] = {}
    notes_kept = 0
    for ident, (key, catalogue_size) in join.items():
        r = raw.get(key)
        if r is None:
            continue
        hubble = (r.get("Hubble") or "").strip()
        majax_raw = (r.get("MajAx") or "").strip()
        minax_raw = (r.get("MinAx") or "").strip()
        posang_raw = (r.get("PosAng") or "").strip()
        redshift_raw = (r.get("Redshift") or "").strip()
        note_raw = (r.get("NED notes") or "").strip()

        try:
            minax = f"{float(minax_raw):.2f}" if minax_raw else ""
        except ValueError:
            minax = ""
        try:
            redshift = f"{float(redshift_raw):.6f}" if redshift_raw else ""
        except ValueError:
            redshift = ""
        note = note_raw if note_raw and worth_surfacing(note_raw) else ""
        if note:
            notes_kept += 1
        axis_ratio, posang = shape_columns(majax_raw, minax_raw, posang_raw,
                                           catalogue_size)

        if not (hubble or minax or redshift or note or axis_ratio or posang):
            continue  # nothing extra for this object -- no row at all
        # The five original columns first and in their original order, so a
        # loader (or a reader's eye) written before #181 still finds them.
        rows[ident] = (ident, hubble, minax, redshift, note, axis_ratio, posang)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="") as fh:
        fh.write("# OpenNGC (https://github.com/mattiaverga/OpenNGC), CC-BY-SA-4.0\n")
        fh.write("# Columns OpenNGC publishes that ngc.tsv itself does not carry.\n")
        fh.write("# Regenerate: python -m astrodeck.catalog.build_ngc_extras "
                 "<NGC.csv> [<addendum.csv>]\n")
        fh.write("# id\thubble\tminax_arcmin\tredshift\tned_note\t"
                 "axis_ratio\tposang_deg\n")
        for key in sorted(rows):
            fh.write("\t".join(rows[key]) + "\n")
    shaped = sum(1 for row in rows.values() if row[5])
    angled = sum(1 for row in rows.values() if row[6])
    print(f"wrote {len(rows)} rows to {OUT} ({notes_kept} with a surfaced NED "
          f"note, {shaped} with an axis ratio, {angled} with a position angle)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or [str(Path.cwd() / "NGC.csv")]))
