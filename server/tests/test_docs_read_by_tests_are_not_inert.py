# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A path that CI treats as inert must not be named by any test (#1267).

`tools/ci/areas.json` lists paths whose change selects no CI job. A document a
test reads must never be on that list, or an edit to it turns main red with no
run to see it. This is a static guard: it reads test source and looks for a
literal that names a path under an inert pattern, including the form built
from parts (`"docs" / "superpowers" / "plans"`). It cannot see a path that is
assembled at run time from a variable or a directory listing; the conservative
rule in areas.json covers that: a directory is inert only when no test opened
a file in it in the run-time read map of WP 7.2 either.

Prose is not a read: a comment, or a docstring, that names a plan document
(many tests cite the plan that motivated them) is not counted. A path inside
an ordinary string literal, or an f-string, is.
"""
from __future__ import annotations

import io
import json
import re
import tokenize
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_AREAS = _ROOT / "tools" / "ci" / "areas.json"
_SELF = Path(__file__).resolve()

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"(?m)(?<![:\"'`\w])//.*$")
_STRING_JOIN = re.compile(r"""["']\s*/\s*["']""")
_TRIPLE = ("'''", '"""')


def inert_patterns() -> list[str]:
    return json.loads(_AREAS.read_text(encoding="utf-8"))["inert"]


def literal_prefix(pattern: str) -> str:
    """The fixed text of a glob before its first wildcard."""
    cut = min((i for i in (pattern.find("*"), pattern.find("?")) if i >= 0), default=len(pattern))
    return pattern[:cut]


def python_code_text(source: str) -> str:
    """Source with comments and triple-quoted strings (prose) removed."""
    kept = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING and tok.string.lstrip("rbfuRBFU").startswith(_TRIPLE):
            continue
        kept.append(tok.string)
    return " ".join(kept)


def script_code_text(source: str) -> str:
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", source))


def names_path(code: str, prefix: str) -> bool:
    """True when code names `prefix`, whole or as joined parts."""
    joined = _STRING_JOIN.sub("/", code.replace("\\\\", "/"))
    return prefix in joined or prefix.rstrip("/") + '"' in joined


def scanned_sources() -> list[tuple[Path, str]]:
    found = []
    for path in sorted((_ROOT / "server" / "tests").rglob("*.py")):
        if path != _SELF:
            found.append((path, "python"))
    for suffix in ("*.test.ts", "*.test.tsx"):
        for path in sorted((_ROOT / "ui" / "src").rglob(suffix)):
            found.append((path, "script"))
    return found


def code_of(path: Path, kind: str) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    return python_code_text(text) if kind == "python" else script_code_text(text)


def test_no_test_names_a_path_the_ci_map_treats_as_inert():
    prefixes = {p: literal_prefix(p) for p in inert_patterns()}
    hits = []
    for path, kind in scanned_sources():
        code = code_of(path, kind)
        for pattern, prefix in prefixes.items():
            if names_path(code, prefix):
                hits.append("%s names %s (inert pattern %s)" % (path.relative_to(_ROOT).as_posix(), prefix, pattern))
    assert hits == [], "a test names a path that tools/ci/areas.json lists as inert:\n" + "\n".join(hits)


def test_the_inert_list_still_names_the_plan_documents():
    # The list is what 8.10 added for documentation nobody reads; losing the
    # entry would make every plan edit run the whole server suite again.
    assert "docs/superpowers/plans/**" in inert_patterns()


def test_the_scan_sees_a_whole_path_literal():
    code = python_code_text('p = open("docs/superpowers/plans/x.md")\n')
    assert names_path(code, "docs/superpowers/plans/")


def test_the_scan_sees_a_path_built_from_parts():
    code = python_code_text('p = ROOT / "docs" / "superpowers" / "plans" / "x.md"\n')
    assert names_path(code, "docs/superpowers/plans/")


def test_the_scan_sees_a_path_in_an_fstring():
    code = python_code_text('p = Path(f"docs/superpowers/plans/{name}.md")\n')
    assert names_path(code, "docs/superpowers/plans/")


def test_the_scan_sees_a_path_in_a_script_literal():
    code = script_code_text("const t = readFileSync('docs/superpowers/plans/x.md');\n")
    assert names_path(code, "docs/superpowers/plans/")


def test_the_scan_does_not_count_a_comment_or_a_docstring():
    source = (
        '"""Motivated by docs/superpowers/plans/x.md."""\n'
        "# see docs/superpowers/plans/x.md\n"
        "x = 1\n"
    )
    assert not names_path(python_code_text(source), "docs/superpowers/plans/")
    script = "// docs/superpowers/plans/x.md\n/* docs/superpowers/plans/y.md */\nconst x = 1;\n"
    assert not names_path(script_code_text(script), "docs/superpowers/plans/")


def test_an_exact_file_pattern_is_matched_by_its_whole_name():
    assert literal_prefix("docs/comhost.md") == "docs/comhost.md"
    assert names_path('open ( "docs/comhost.md" )', "docs/comhost.md")
    assert literal_prefix("docs/superpowers/plans/**") == "docs/superpowers/plans/"
