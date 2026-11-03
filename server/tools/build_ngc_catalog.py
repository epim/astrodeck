# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Build astrodeck/catalog/data/ngc.tsv and ngc_extras.tsv from OpenNGC.

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

THE PIN (#804). The input is ONE commit of OpenNGC, OPENNGC_COMMIT below, and
the two CSVs are checked against SNAPSHOT_SHA256 after they are fetched. A hand
downloaded NGC.csv was the original input and nobody recorded which one; a
rebuild from a later download changed 6 sidecar rows and left two of them
disagreeing with ngc.tsv about the object's size. The build therefore takes no
CSV path, never reads a branch, and refuses a ref that is not a full commit id.
ngc.tsv and the sidecar ngc_extras.tsv are written in one run from the one
snapshot, and ngc.tsv's header records the commit and both checksums.

To move the pin: change OPENNGC_COMMIT, run once to read the two sha256
values from the mismatch message, record them in SNAPSHOT_SHA256, run again,
review the diff of both files, and update the row counts in
tests/test_w1_11_openngc_pin.py and the hashes in
tools/licence/artifact-registry.json.

Run from the server/ directory:  python tools/build_ngc_catalog.py [<download-dir>]
(the download directory defaults to a temporary one; give one to keep the CSVs).
Output is deterministic (sorted), so a rebuild diffs cleanly.
"""
from __future__ import annotations

import csv
import hashlib
import re
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable, BinaryIO

SERVER = Path(__file__).resolve().parents[1]
OUT = SERVER / "astrodeck" / "catalog" / "data" / "ngc.tsv"

#: The one OpenNGC commit the shipped catalogue files are built from
#: (master as of 2026-10-18, "Revise data from Simbad").
OPENNGC_COMMIT = "be3cd6c5498806a2fbb2f7e0452ef802dbb02be0"

#: sha256 of each CSV at that commit. A mismatch is an error, never a warning.
SNAPSHOT_SHA256 = {
    "NGC.csv": "54abea0b57b0a103831bfc7a8e3a99a23acfbf4bb581fcf3d8af539cbb826c84",
    "addendum.csv": "c93f983dda6780b76b7b2e9271a856a892627710148a982ee98101a54c642fdd",
}

_RAW_URL = "https://raw.githubusercontent.com/mattiaverga/OpenNGC/{commit}/database_files/{name}"

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


def require_pinned(commit: str) -> str:
    """The commit id, or ValueError when it is not a full 40-hex commit id.
    A branch name (master, HEAD) moves; a short id is ambiguous."""
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError(f"OpenNGC must be read at a full commit id, not {commit!r}")
    return commit


def snapshot_url(name: str) -> str:
    return _RAW_URL.format(commit=require_pinned(OPENNGC_COMMIT), name=name)


def fetch_snapshot(dest: Path,
                   opener: Callable[[str], BinaryIO] = urllib.request.urlopen) -> list[Path]:
    """Download the pinned CSVs into ``dest`` and return their paths, NGC.csv
    first. Nothing is left in ``dest`` for a file whose sha256 differs from
    SNAPSHOT_SHA256."""
    dest.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for name, expected in SNAPSHOT_SHA256.items():
        with opener(snapshot_url(name)) as response:
            body = response.read()
        actual = hashlib.sha256(body).hexdigest()
        if actual != expected:
            raise ValueError(f"{name} at {OPENNGC_COMMIT} has sha256 {actual}, "
                             f"expected {expected}")
        path = dest / name
        path.write_bytes(body)
        paths.append(path)
    return paths


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


def build_rows(paths: list[Path]) -> dict[str, tuple]:
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
    return rows


def write_tsv(rows: dict[str, tuple], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as fh:
        fh.write("# OpenNGC (https://github.com/mattiaverga/OpenNGC), CC-BY-SA-4.0\n")
        fh.write(f"# OpenNGC commit: {require_pinned(OPENNGC_COMMIT)}\n")
        for name, digest in SNAPSHOT_SHA256.items():
            fh.write(f"# {name} sha256: {digest}\n")
        fh.write("# id\tcommon\ttype\tra_hours\tdec_deg\tmag\tsize_arcmin\talias\n")
        for key in sorted(rows):
            fh.write("\t".join(str(x) for x in rows[key]) + "\n")


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        print(__doc__)
        return 2
    with tempfile.TemporaryDirectory() as scratch:
        paths = fetch_snapshot(Path(argv[0]) if argv else Path(scratch))
        rows = build_rows(paths)
        write_tsv(rows, OUT)
        print(f"wrote {len(rows)} objects to {OUT}")
        # The sidecar joins on ngc.tsv, so it is rebuilt from the same files in
        # the same run: the two can no longer come from different snapshots.
        sys.path.insert(0, str(SERVER))
        from astrodeck.catalog import build_ngc_extras
        return build_ngc_extras.main([str(p) for p in paths])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
