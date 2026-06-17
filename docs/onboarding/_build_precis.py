#!/usr/bin/env python3
"""
Build the self-contained AstroDeck astrophotography precis page.

Reads the shell (_shell_precis.html) and every section in
_sections_precis/ (in numeric filename order), splits each section into
its STYLE / BODY / SCRIPT blocks, and injects them into the shell at the
three INJECT markers:

    <!--INJECT:STYLES-->    <-  each section's <!--STYLE--> block
    <!--INJECT:SECTIONS-->  <-  each section's <!--BODY--> block (in order)
    <!--INJECT:SCRIPTS-->   <-  each section's <!--SCRIPT--> block

Output: astrophotography-precis.html (one self-contained file; no external
JS, Google Fonts <link> only).

Reproducible: run `python _build_precis.py` from anywhere; paths are
resolved relative to this file.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHELL = HERE / "_shell_precis.html"
SECTIONS_DIR = HERE / "_sections_precis"
OUTPUT = HERE / "astrophotography-precis.html"

# Injection markers in the shell.
MARK_STYLES = "<!--INJECT:STYLES-->"
MARK_SECTIONS = "<!--INJECT:SECTIONS-->"
MARK_SCRIPTS = "<!--INJECT:SCRIPTS-->"

# Per-section block delimiters.
BLOCK_RE = {
    "style": re.compile(r"<!--STYLE-->(.*?)<!--/STYLE-->", re.DOTALL),
    "body": re.compile(r"<!--BODY-->(.*?)<!--/BODY-->", re.DOTALL),
    "script": re.compile(r"<!--SCRIPT-->(.*?)<!--/SCRIPT-->", re.DOTALL),
}

# Leading numeric prefix used to order section files (e.g. "03-polar.html").
NUM_PREFIX_RE = re.compile(r"^(\d+)")


def section_sort_key(p: Path):
    m = NUM_PREFIX_RE.match(p.name)
    return (int(m.group(1)) if m else 1_000_000, p.name)


def extract_block(text: str, kind: str, path: Path) -> str:
    m = BLOCK_RE[kind].search(text)
    if not m:
        raise SystemExit(f"ERROR: {path.name} is missing a <!--{kind.upper()}--> block.")
    return m.group(1).strip("\n")


def main() -> int:
    if not SHELL.is_file():
        raise SystemExit(f"ERROR: shell not found: {SHELL}")
    if not SECTIONS_DIR.is_dir():
        raise SystemExit(f"ERROR: sections dir not found: {SECTIONS_DIR}")

    shell = SHELL.read_text(encoding="utf-8")
    for mark in (MARK_STYLES, MARK_SECTIONS, MARK_SCRIPTS):
        if mark not in shell:
            raise SystemExit(f"ERROR: shell is missing injection marker {mark}")

    section_files = sorted(SECTIONS_DIR.glob("*.html"), key=section_sort_key)
    if not section_files:
        raise SystemExit("ERROR: no section files found.")

    styles: list[str] = []
    bodies: list[str] = []
    scripts: list[str] = []

    for path in section_files:
        text = path.read_text(encoding="utf-8")
        style = extract_block(text, "style", path)
        body = extract_block(text, "body", path)
        script = extract_block(text, "script", path)

        styles.append(f"  /* ===== injected styles: {path.name} ===== */\n{style}")
        bodies.append(f"  <!-- ===== section: {path.name} ===== -->\n{body}")
        if script.strip():
            scripts.append(
                "  <!-- ===== script: {name} ===== -->\n"
                "  <script>\n{body}\n  </script>".format(name=path.name, body=script)
            )

    styles_blob = "\n\n".join(styles)
    sections_blob = "\n\n".join(bodies)
    scripts_blob = "\n\n".join(scripts)

    out = shell
    out = out.replace(MARK_STYLES, styles_blob)
    out = out.replace(MARK_SECTIONS, sections_blob)
    out = out.replace(MARK_SCRIPTS, scripts_blob)

    OUTPUT.write_text(out, encoding="utf-8")

    size = OUTPUT.stat().st_size
    n_scripts = len(scripts)
    print(f"Built: {OUTPUT}")
    print(f"Size:  {size:,} bytes")
    print(f"Sections: {len(section_files)}  (section scripts injected: {n_scripts})")
    for p in section_files:
        print(f"  - {p.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
