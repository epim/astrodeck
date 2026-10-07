#!/usr/bin/env python
# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Tabulate the no-light check's verdicts from the durable night logs (#308).

Every failed plate solve judges its own frame (``astrodeck.solve.light``): the
frame's median against the level its camera reads with no light on it, inside
a band of a few ADU. The band has never been held against real frames of a
THICK, MOONLESS overcast at the solve's readout, where a cold sensor under a
dark sky could read inside it and be called "optic capped" (#308). The server
logs one line per judged frame, ``failed solve, light check: ...``, and since
#308 the line ends its evidence with the frame's own readout (exposure, bin,
gain, offset, sensor temperature). This tool reads those lines back out of
``captures/logs/<night>.jsonl`` and prints a table per night:

    python tools/no_light_audit.py                         # captures/logs
    python tools/no_light_audit.py captures/logs/2026-10-14.jsonl
    python tools/no_light_audit.py captures/logs --moonless 2026-10-14,2026-10-15

    On the rig (ssh lands in PowerShell). ``tools/`` is not part of a release
    bundle (``scripts/build_release.py`` ships only the licence texts from
    it), so copy this one file over first, then name the logs directory:
    cd C:\\Users\\James\\AstroDeck; & .\\venv\\Scripts\\python.exe no_light_audit.py captures\\logs --moonless 2026-10-14

One row per judged frame: the time, the verdict (no_light, cloud, unknown,
narrowband), which reference it stood on (dark_master, bias_master, self_shot),
the frame's median, the reference level, the band, the margin, and the readout.

    margin = (median - reference) / band

so a margin inside -1..+1 is inside the band, which is what a no-light verdict
is made of (when the reference is a floor, a frame above it can still be no
verdict: the line's own words say so). The numbers worth a look are the
no_light rows on a night you know was overcast and moonless, on a cold sensor.

NO SITE INPUT, AND NO SKY. The tool is given no latitude, longitude or site
label, and it never computes where the Sun or the Moon is: that needs the
site, and a tool that held it would be one more place for it to leak. The
operator reads moonless nights off a calendar and passes them as
``--moonless`` (night keys, the log files' own ``YYYY-MM-DD``: local
noon-to-noon, as ``events.night_key``); those nights' rows are marked.

Read-only: it opens the logs for reading and writes nothing. Standard library
only, so it runs on the rig's venv or any Python; no network. ASCII output.

Lines logged before the readout clause existed still parse, with the readout
shown as ``-``. A light-check line the pattern cannot read is listed by file
and line number and the tool exits 1: the format drifted, and
``test_failed_solve_says_no_light.py`` feeds this parser the lines the server
really emits so that it goes red first.

Exit codes: 0 ran; 1 some light-check lines did not parse; 2 bad usage or
nothing to read.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Iterable, TextIO

#: What every judged frame's log line starts with (``solve/light.py``).
PREFIX = "failed solve, light check:"

REPO = Path(__file__).resolve().parent.parent
DEFAULT_LOGS = REPO / "captures" / "logs"

#: One pattern for every shape the line takes. ``LightVerdict.evidence()`` has
#: three: no median ("unknown: why"), a median and no reference, and the full
#: one; the readout clause and the camera's own words are optional after it.
#: The camera's words are free text, which is why they close the line and the
#: readout sits before them.
_LINE = re.compile(
    r"^failed solve, light check: "
    r"(?:median (?P<median>\S+) ADU, robust sigma (?P<sigma>\S+) ADU per pixel; )?"
    r"(?:reference (?P<reference>\S+) ADU"
    r"(?:, ceiling (?P<ceiling>\S+) ADU|(?P<unbounded>, with no ceiling))?"
    r" from (?P<source>.+?)(?:; band \+/-(?P<band>\S+) ADU)?; )?"
    r"(?P<kind>no_light|cloud|unknown|narrowband)(?:: (?P<why>.*?))?"
    r"(?:; frame (?P<exposure>\S+) s bin (?P<binning>\S+) gain (?P<gain>\S+) "
    r"offset (?P<offset>\S+)(?:, sensor (?P<sensor>\S+) C)?)?"
    r"(?: \(the camera said: .*\))?$",
    re.DOTALL)

#: The words ``Reference.source`` carries, to the ``Reference.kind`` they
#: stand for. The kind is data in the server and only words in the line, so
#: the test that round-trips the real lines pins this table to it.
_SOURCE_KINDS = (
    ("shortest-exposure frame", "self_shot"),
    ("dark master", "dark_master"),
    ("bias master", "bias_master"),
    ("explicit reference", "explicit"),
)

_NIGHT = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class Reading:
    """One judged frame, as its log line says it. ``ceiling`` is None when the
    reference has no separate ceiling and ``math.inf`` for "with no ceiling"."""
    kind: str
    median: float | None = None
    sigma: float | None = None
    reference: float | None = None
    ceiling: float | None = None
    source: str | None = None
    ref_kind: str | None = None
    band: float | None = None
    why: str = ""
    exposure_s: float | None = None
    binning: int | None = None
    gain: int | None = None
    offset: int | None = None
    sensor_c: float | None = None
    night: str = ""
    ts: float | None = None
    moonless: bool = False

    @property
    def margin(self) -> float | None:
        """``(median - reference) / band``, or None when any is missing."""
        if self.median is None or self.reference is None or not self.band:
            return None
        m = (self.median - self.reference) / self.band
        return m if math.isfinite(m) else None


def _kind_of(source: str | None) -> str | None:
    if source is None:
        return None
    for words, kind in _SOURCE_KINDS:
        if words in source:
            return kind
    return "other"


def _opt(text: str | None, conv):
    return None if text is None else conv(text)


def parse_line(message: str) -> Reading | None:
    """The ``Reading`` a light-check log message holds, or None when it is not
    one or the pattern cannot read it."""
    m = _LINE.match(message)
    if m is None:
        return None
    try:
        return Reading(
            kind=m["kind"],
            median=_opt(m["median"], float),
            sigma=_opt(m["sigma"], float),
            reference=_opt(m["reference"], float),
            ceiling=(math.inf if m["unbounded"]
                     else _opt(m["ceiling"], float)),
            source=m["source"],
            ref_kind=_kind_of(m["source"]),
            band=_opt(m["band"], float),
            why=m["why"] or "",
            exposure_s=_opt(m["exposure"], float),
            binning=_opt(m["binning"], int),
            gain=_opt(m["gain"], int),
            offset=_opt(m["offset"], int),
            sensor_c=_opt(m["sensor"], float))
    except ValueError:
        return None


@dataclass
class Audit:
    """What a read of the logs found."""
    files: list[Path]
    readings: list[Reading]
    #: ``(file, line number)`` of every light-check line the pattern missed.
    unparsed: list[tuple[Path, int]]
    #: Lines that held the prefix but were not JSON (a torn final write).
    unreadable: int = 0


def log_files(paths: Iterable[Path]) -> list[Path]:
    """The night logs ``paths`` name: a file as itself, a directory as its
    ``*.jsonl`` in name order (the names are dates, so that is time order)."""
    out: list[Path] = []
    for p in paths:
        if p.is_dir():
            found = sorted(p.glob("*.jsonl"))
        elif p.is_file():
            found = [p]
        else:
            raise FileNotFoundError(str(p))
        out.extend(f for f in found if f not in out)
    return out


def collect(files: Iterable[Path], moonless: frozenset[str] = frozenset()
            ) -> Audit:
    """Read every light-check line out of ``files`` (read-only)."""
    files = list(files)
    audit = Audit(files=files, readings=[], unparsed=[])
    for path in files:
        night = path.stem
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for number, raw in enumerate(fh, 1):
                if PREFIX not in raw:
                    continue
                try:
                    ev = json.loads(raw)
                except ValueError:
                    audit.unreadable += 1
                    continue
                if not isinstance(ev, dict) or ev.get("type") != "log":
                    continue
                data = ev.get("data")
                message = data.get("message") if isinstance(data, dict) else None
                if not isinstance(message, str) or not message.startswith(PREFIX):
                    continue
                reading = parse_line(message)
                if reading is None:
                    audit.unparsed.append((path, number))
                    continue
                ts = ev.get("ts")
                audit.readings.append(replace(
                    reading, night=night, moonless=night in moonless,
                    ts=float(ts) if isinstance(ts, (int, float)) else None))
    return audit


# ---------------------------------------------------------------- the table

_COLUMNS = (
    ("time", 8), ("verdict", 9), ("reference", 12), ("median", 8),
    ("level", 8), ("band", 6), ("margin", 7), ("exp_s", 6), ("bin", 3),
    ("gain", 5), ("off", 4), ("sensor_C", 8), ("moonless", 8),
)


def _num(value: float | None, spec: str) -> str:
    return "-" if value is None else format(value, spec)


def _clock(ts: float | None) -> str:
    try:
        return "-" if ts is None else datetime.fromtimestamp(ts).strftime(
            "%H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return "-"


def _cells(r: Reading) -> list[str]:
    return [
        _clock(r.ts), r.kind, r.ref_kind or "-", _num(r.median, ".1f"),
        _num(r.reference, ".1f"), _num(r.band, ".2f"),
        _num(r.margin, "+.2f"), _num(r.exposure_s, "g"),
        _num(r.binning, "d"), _num(r.gain, "d"), _num(r.offset, "d"),
        _num(r.sensor_c, ".1f"), "M" if r.moonless else "",
    ]


def _row(cells: list[str]) -> str:
    return "  ".join(c.rjust(w) if i >= 3 else c.ljust(w)
                     for i, (c, (_n, w)) in enumerate(zip(cells, _COLUMNS))
                     ).rstrip()


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def render(audit: Audit, out: TextIO) -> None:
    """The table, one block per night, and a count line under each."""
    header = _row([name for name, _w in _COLUMNS])
    by_night: dict[str, list[Reading]] = {}
    for r in audit.readings:
        by_night.setdefault(r.night, []).append(r)
    for night, rows in by_night.items():
        moonless = any(r.moonless for r in rows)
        print(f"== {night}{' (moonless)' if moonless else ''} ==", file=out)
        print(header, file=out)
        for r in rows:
            print(_row(_cells(r)), file=out)
        counts: dict[str, int] = {}
        for r in rows:
            counts[r.kind] = counts.get(r.kind, 0) + 1
        said = ", ".join(f"{k} {n}" for k, n in sorted(counts.items()))
        bare = sum(1 for r in rows if r.exposure_s is None)
        extra = (f"; {bare} without a readout (logged before #308, or the "
                 f"frame had none to print)" if bare else "")
        print(f"{len(rows)} judged: {said}{extra}", file=out)
        print(file=out)
    print(f"{_count(len(audit.readings), 'judged frame')} on "
          f"{_count(len(by_night), 'night')} in "
          f"{_count(len(audit.files), 'file')}", file=out)


# ------------------------------------------------------------------- the CLI

def _moonless_nights(text: str) -> frozenset[str]:
    nights = [t.strip() for t in text.split(",") if t.strip()]
    for n in nights:
        if not _NIGHT.match(n):
            raise argparse.ArgumentTypeError(
                f"{n!r} is not a night key (YYYY-MM-DD)")
    return frozenset(nights)


def main(argv: list[str] | None = None, out: TextIO | None = None) -> int:
    out = sys.stdout if out is None else out
    ap = argparse.ArgumentParser(
        prog="no_light_audit",
        description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("paths", nargs="*", type=Path,
                    help=f"night logs or directories of them "
                         f"(default: {DEFAULT_LOGS})")
    ap.add_argument("--moonless", type=_moonless_nights, default=frozenset(),
                    metavar="NIGHT,NIGHT",
                    help="night keys (YYYY-MM-DD, as the log files are "
                         "named) the operator knows had no Moon; their rows "
                         "are marked. The tool never computes the Moon.")
    args = ap.parse_args(argv)

    try:
        files = log_files(args.paths or [DEFAULT_LOGS])
    except FileNotFoundError as exc:
        print(f"no_light_audit: no such file or directory: {exc}",
              file=sys.stderr)
        return 2
    if not files:
        print("no_light_audit: no *.jsonl night logs to read", file=sys.stderr)
        return 2

    audit = collect(files, args.moonless)
    render(audit, out)

    unseen = sorted(args.moonless - {f.stem for f in files})
    if unseen:
        print(f"note: no log was read for moonless night(s) "
              f"{', '.join(unseen)}", file=sys.stderr)
    if audit.unreadable:
        print(f"note: {audit.unreadable} light-check line(s) were not JSON "
              f"and were skipped", file=sys.stderr)
    if audit.unparsed:
        print("the audit's pattern no longer reads these light-check lines "
              "(the format drifted):", file=sys.stderr)
        for path, number in audit.unparsed:
            print(f"  {path}:{number}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
