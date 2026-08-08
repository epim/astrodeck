"""Build astrodeck/catalog/data/ngc.tsv from OpenNGC.

The hand-written CATALOG in objects.py is 64 rows. On 2026-08-07 an operator
could not start a session on the Cat's Eye Nebula (NGC 6543) because it simply
was not in it — and there is no coordinate entry anywhere in the UI, so an
object outside those 64 was unreachable by any path.

SOURCE: OpenNGC (https://github.com/mattiaverga/OpenNGC), CC-BY-SA-4.0.
It carries the NGC and IC catalogues with J2000 positions, magnitudes, sizes,
Messier cross-references and 131 common names. We take a FILTERED subset — the
things a telescope is pointed at — not all 13,970 rows: duplicates, plain
stars, star pairs and objects with no position are dropped, and a galaxy with
no magnitude at all is not a target anybody can plan.

Run:  python tools/build_ngc_catalog.py <path-to-NGC.csv> [<path-to-addendum.csv>]
Output is deterministic (sorted), so a rebuild diffs cleanly.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "astrodeck" / "catalog" / "data" / "ngc.tsv"

#: OpenNGC type code -> our short type. Anything not here is not a target we
#: will offer: Dup (duplicate entry), * and ** (single/double stars — the
#: bright-star catalogue owns those), NonEx, Other.
TYPES = {
    "G": "GX", "GPair": "GX", "GTrpl": "GX", "GGroup": "GX",
    "PN": "PN", "SNR": "SNR", "HII": "EN", "EmN": "EN", "Neb": "EN",
    "RfN": "RN", "DrkN": "DN", "OCl": "OC", "GCl": "GC", "Cl+N": "OC",
    # NGC/IC-numbered stars and pairs. They ARE catalogue entries people look
    # up ("what is NGC 2169?"), and refusing to find something that exists is
    # the whole defect this file was written to close.
    "*": "STAR", "**": "STAR", "*Ass": "STAR",
    "Other": "OTHER",
}

#: NOT filtered on brightness. The first cut dropped anything fainter than
#: mag 15 and anything OpenNGC had no magnitude for, which quietly threw away
#: 5,800 real objects — the same "the catalogue does not carry it" dead end,
#: just further out. An object with no published magnitude is carried with
#: MAG_UNKNOWN, which sorts it last in a browse and reads as "unknown" rather
#: than as a brightness nobody measured.
MAG_UNKNOWN = 99.0


def hms_to_hours(s: str) -> float | None:
    try:
        h, m, sec = (float(x) for x in s.split(":"))
    except (ValueError, AttributeError):
        return None
    return h + m / 60.0 + sec / 3600.0


def dms_to_deg(s: str) -> float | None:
    try:
        body = s.strip()
        sign = -1.0 if body.startswith("-") else 1.0
        d, m, sec = (float(x) for x in body.lstrip("+-").split(":"))
    except (ValueError, AttributeError):
        return None
    return sign * (d + m / 60.0 + sec / 3600.0)


def designation(raw: str) -> str:
    """OpenNGC writes NGC0224/IC0434; humans write NGC 224 / IC 434."""
    for prefix in ("NGC", "IC"):
        if raw.startswith(prefix):
            return f"{prefix} {raw[len(prefix):].lstrip('0') or '0'}"
    return raw


def main(paths: list[str]) -> int:
    rows: dict[str, tuple] = {}
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            for r in csv.DictReader(fh, delimiter=";"):
                kind = TYPES.get(r["Type"].strip())
                if kind is None:
                    continue
                ra = hms_to_hours(r["RA"])
                dec = dms_to_deg(r["Dec"])
                if ra is None or dec is None:
                    continue
                mag_raw = (r.get("V-Mag") or r.get("B-Mag") or "").strip()
                try:
                    mag = float(mag_raw)
                except ValueError:
                    mag = MAG_UNKNOWN
                try:
                    size = float((r.get("MajAx") or "").strip())
                except ValueError:
                    size = 0.0

                ident = designation(r["Name"].strip())
                common = (r.get("Common names") or "").split(",")[0].strip()
                messier = (r.get("M") or "").strip().lstrip("0")
                # A Messier object is known by its M number first: that is what
                # the eyepiece, the book and the user all call it.
                if messier:
                    ident, alias = f"M{messier}", ident
                else:
                    alias = ""
                rows[ident] = (ident, common, kind, round(ra, 6), round(dec, 6),
                               mag, round(size, 2), alias)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="") as fh:
        fh.write("# OpenNGC (https://github.com/mattiaverga/OpenNGC), CC-BY-SA-4.0\n")
        fh.write("# id\tcommon\ttype\tra_hours\tdec_deg\tmag\tsize_arcmin\talias\n")
        for key in sorted(rows):
            fh.write("\t".join(str(x) for x in rows[key]) + "\n")
    print(f"wrote {len(rows)} objects to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or [str(Path.cwd() / "NGC.csv")]))
