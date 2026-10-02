# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""No file under ``server/`` cites the nonexistent "S4 item 14" (#412 item 3).

The spec's S4 has items 1 to 6. The work five places cited as "#189 S4 item
14" (or "S4, item 14"), the brief's visit sentence, the budget's minimum
visit and the eighth Example's tagline, is recorded under S3 item 5 and #353
(Revision 8 row 30), so the citation pointed a reader at nothing. Each now
cites the spec's label, "S3 item 5, #353". This file keeps the citation from
coming back: it reads every text source under ``server/`` and names each
file and line that still carries it.

THIS FILE IS THE ONE PLACE THE CITATION IS SPELT, since it must name what it
hunts, and it is left out of its own scan. The scan skips what is not source:
the virtualenv, bytecode caches, the build and egg-info copies a packaging
run writes, the pytest cache, and the developer's real config directory,
which it must never read (``REAL_CONFIG``).

The mutant was run in a private copy of ``server/`` (scratchpad
``S5-TONIGHT-mut``, from byte backups), never in the shared tree.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]

#: "S4 item 14" or "S4, item 14", as the five places wrote it.
CITATION = re.compile(r"S4,?\s+item\s+14\b")

#: Directories that hold no source of ours.
SKIP_DIRS = {".venv", "__pycache__", ".pytest_cache", "build",
             "node_modules"}
#: THE DEVELOPER'S REAL CONFIG, ``server/config/`` (gitignored), is never
#: walked: it is no source, it can hold the site, and the suite's guard
#: (``conftest``'s real-config net, #341, #361) fails any test that lists or
#: opens it. The first draft of this scan did both, and the guard named it.
REAL_CONFIG = SERVER / "config"
#: The text sources the scan reads.
SUFFIXES = {".py", ".md", ".json", ".txt", ".toml", ".cfg", ".ini", ".yaml",
            ".yml", ".ps1", ".sh"}


def _sources():
    """Every text source under ``server/``, the skipped directories pruned
    before they are walked (the virtualenv alone holds thousands of files)."""
    me = Path(__file__).resolve()
    for root, dirs, files in os.walk(SERVER):
        dirs[:] = [d for d in dirs
                   if d not in SKIP_DIRS and not d.endswith(".egg-info")
                   and Path(root) / d != REAL_CONFIG]
        for name in files:
            path = Path(root) / name
            if path.suffix in SUFFIXES and path.resolve() != me:
                yield path


def test_no_source_cites_s4_item_14():
    """RED under mutant "one citation restored" (``tonight.py``'s
    ``_visits_per_panel`` docstring back to "THE MINIMUM VISIT IS COUNTED
    (#189 S4, item 14)"), observed; the failure names the file and line, not
    the text, so this docstring can quote it:

        E       AssertionError: cited as the spec's S3 item 5 and #353
            (Revision 8 row 30), not an S4 item the spec does not have:
            astrodeck/flows/tonight.py:998
        E       assert ['astrodeck/f...night.py:998'] == []
        E         Left contains one more item: 'astrodeck/flows/tonight.py:998'

    (The line number is the private copy's at the time of the run.)
    """
    stale = []
    for path in _sources():
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), 1):
            if CITATION.search(line):
                stale.append(f"{path.relative_to(SERVER).as_posix()}:{number}")
    assert stale == [], (
        "cited as the spec's S3 item 5 and #353 (Revision 8 row 30), not an "
        "S4 item the spec does not have: " + ", ".join(stale))


def test_control_the_scan_reads_the_files_it_guards():
    """Control: the scan reaches the four files the citation was taken out
    of, and the pattern finds both spellings, so a green run is not a scan
    of nothing."""
    seen = {p.relative_to(SERVER).as_posix() for p in _sources()}
    for rel in ("astrodeck/flows/tonight.py", "astrodeck/flows/examples.py",
                "tests/test_flows_brief_grid_and_visit.py",
                "tests/test_flows_example_taglines.py"):
        assert rel in seen, rel
    assert CITATION.search("(#189 S4 item 14)")
    assert CITATION.search("(#189 S4, item 14)")
    assert not CITATION.search("(spec S3 item 5, #353)")
