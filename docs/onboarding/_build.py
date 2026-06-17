#!/usr/bin/env python3
"""Assemble the AstroDeck primer from _shell.html + _sections/*.html."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SECTIONS_DIR = ROOT / "_sections"
SHELL = ROOT / "_shell.html"
OUT = ROOT / "astrophotography-primer.html"

BLOCK_RE = {
    "style":  re.compile(r"<!--STYLE-->(.*?)<!--/STYLE-->", re.S),
    "body":   re.compile(r"<!--BODY-->(.*?)<!--/BODY-->", re.S),
    "script": re.compile(r"<!--SCRIPT-->(.*?)<!--/SCRIPT-->", re.S),
}

def extract(text, kind, fname):
    m = BLOCK_RE[kind].search(text)
    if not m:
        raise SystemExit(f"MISSING {kind} block in {fname}")
    return m.group(1).strip("\n")

section_files = sorted(SECTIONS_DIR.glob("*.html"))
assert section_files, "no section files found"

styles, bodies, scripts, ids = [], [], [], []

for f in section_files:
    text = f.read_text(encoding="utf-8")
    num = f.stem.split("-", 1)[0]
    pretty = f.stem
    style  = extract(text, "style", f.name)
    body   = extract(text, "body", f.name)
    script = extract(text, "script", f.name)

    # capture the section id for QA
    idm = re.search(r'<section[^>]*\bid="([^"]+)"', body)
    ids.append(idm.group(1) if idm else "??")

    banner = f"  /* ===================== SECTION {num}: {pretty} ===================== */"
    styles.append(banner + "\n" + style)

    bodies.append(
        f"    <!-- ===================== SECTION {num}: {pretty} ===================== -->\n"
        + body
    )

    # wrap each section script in an IIFE-safe block comment header
    scripts.append(
        f"  <!-- section {num} ({pretty}) behavior -->\n"
        f"  <script>\n{script}\n  </script>"
    )

shell = SHELL.read_text(encoding="utf-8")

styles_blob  = "\n\n".join(styles)
bodies_blob  = "\n\n".join(bodies)
scripts_blob = "\n\n".join(scripts)

# Inject. Preserve indentation context of each marker.
shell = shell.replace("  <!--INJECT:STYLES-->", styles_blob)
shell = shell.replace("    <!--INJECT:SECTIONS-->", bodies_blob)
shell = shell.replace("  <!--INJECT:SCRIPTS-->", scripts_blob)

# sanity: no markers left
for marker in ("<!--INJECT:STYLES-->", "<!--INJECT:SECTIONS-->", "<!--INJECT:SCRIPTS-->"):
    if marker in shell:
        raise SystemExit(f"marker not replaced: {marker}")

OUT.write_text(shell, encoding="utf-8")

print(f"sections: {len(section_files)}")
print(f"ids: {ids}")
print(f"bytes: {OUT.stat().st_size}")
print(f"out: {OUT}")
