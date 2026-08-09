"""Regenerate astrodeck/catalog/data/ngc_extras.tsv from OpenNGC's own CSVs —
the columns tools/build_ngc_catalog.py reads and then throws away.

WHY THIS EXISTS. ``ngc.tsv`` keeps 8 of OpenNGC's 32 columns (id, common name,
type, position, magnitude, size, alias) — everything a telescope needs to
slew somewhere. It drops everything a *description* needs. Per
docs/superpowers/backlog/2026-08-08-object-description-sources.md (the survey
this implements), four of the discarded columns are worth a second look:

  * ``Hubble``  — 30 distinct galaxy-subtype codes across 10,000+ rows. Turns
    "galaxy in Draco" into "spiral galaxy in Draco" — the single best return
    in the survey.
  * ``MinAx``   — paired with ``MajAx`` (already carried, as ``size_arcmin``
    on the ``DSO`` dataclass — NOT duplicated here), gives an axis ratio.
    describe.py uses ratio >= 3 to say "edge-on".
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
sibling test for the 7 boundary disagreements), ``SurfBr``/``PosAng`` (no
consumer in describe.py today), and the *rest* of ``Common names`` beyond the
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


def _load_join_keys() -> dict[str, str]:
    """id -> the raw-CSV lookup key (``alias or id``), read from ngc.tsv."""
    out: dict[str, str] = {}
    with open(NGC_TSV, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.rstrip("\n").split("\t")
            ident = parts[0]
            alias = parts[7] if len(parts) > 7 else ""
            out[ident] = alias or ident
    return out


def main(paths: list[str]) -> int:
    raw: dict[str, dict] = {}
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            for r in csv.DictReader(fh, delimiter=";"):
                raw[designation(r["Name"].strip())] = r

    join_keys = _load_join_keys()

    rows: dict[str, tuple] = {}
    notes_kept = 0
    for ident, key in join_keys.items():
        r = raw.get(key)
        if r is None:
            continue
        hubble = (r.get("Hubble") or "").strip()
        minax_raw = (r.get("MinAx") or "").strip()
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

        if not (hubble or minax or redshift or note):
            continue  # nothing extra for this object -- no row at all
        rows[ident] = (ident, hubble, minax, redshift, note)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="") as fh:
        fh.write("# OpenNGC (https://github.com/mattiaverga/OpenNGC), CC-BY-SA-4.0\n")
        fh.write("# Columns OpenNGC publishes that ngc.tsv itself does not carry.\n")
        fh.write("# Regenerate: python -m astrodeck.catalog.build_ngc_extras "
                 "<NGC.csv> [<addendum.csv>]\n")
        fh.write("# id\thubble\tminax_arcmin\tredshift\tned_note\n")
        for key in sorted(rows):
            fh.write("\t".join(rows[key]) + "\n")
    print(f"wrote {len(rows)} rows to {OUT} ({notes_kept} with a surfaced NED note)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or [str(Path.cwd() / "NGC.csv")]))
