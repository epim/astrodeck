"""The em-dash compatibility shim and the copy-rules scan (S7k, decision D-SKY-4).

CORRECTING A CLAIM IN DEVIATIONS.md. D-SKY-4's ledger entry says the Atlas
placeholder's em-dash was kept mounted verbatim because "the server matches
that exact string for the beginner example". No such string exists.
`squash_designation` (catalog/objects.py:184-198) strips `[^a-z0-9]+` from
BOTH the query and the stored id before comparing them, so the designation
"M 31" resolves however it is typed - a plain hyphen, an em-dash, an en-dash,
a non-breaking space, a stray apostrophe, none of it survives the squash. The
placeholder's punctuation was never load-bearing; nothing on the server side
ever asked for an em-dash specifically, so S7a's rewrite of the placeholder to
a spaced hyphen (`ui/src/components/atlas/CatalogSearch.tsx:78`) cost nothing.

TWO THINGS LIVE IN THIS FILE.

1. A compatibility shim for stray punctuation, written as tests rather than
   as production code. `squash_designation` already treated an em-dash as
   just another separator before this file existed - there was no gap to
   patch. The "shim" is the guarantee itself, pinned here so it cannot regress
   silently: if `_SEPARATORS` is ever narrowed (Sabotage B, below), one of
   these tests goes red instead of a beginner's copy-pasted designation
   quietly going back to matching nothing.
2. `test_no_user_facing_server_string_carries_an_em_dash`, an ast-based sweep
   that fails the moment a NEW em-dash (or en-dash, right arrow, curly quote,
   or emoji) reaches a user through one of the modules that produce
   catalogue-search copy. It reads `ast.Constant` string literals directly
   rather than grepping text, specifically so it can skip docstrings (for
   readers of the source, not for users) while still catching an offender
   buried inside an f-string.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import astrodeck
from astrodeck.catalog.objects import search, squash_designation

#: server/astrodeck - every module path below is relative to this.
_ASTRODECK_ROOT = Path(astrodeck.__file__).resolve().parent

#: The punctuation this scan treats as a hard fail if it reaches a user.
#: Em-dash, en-dash, right arrow, left/right curly double quotes. Written as
#: escapes, not the raw glyphs, so this source file itself carries none of
#: them. Emoji are checked separately (a range test, not a membership test).
_BAD_CHARS = "\u2014\u2013\u2192\u201c\u201d"


def _is_emoji(ch: str) -> bool:
    cp = ord(ch)
    return (0x1F300 <= cp <= 0x1FAFF) or (0x2600 <= cp <= 0x27BF)


def _docstring_constant_ids(tree: ast.AST) -> set[int]:
    """id() of every ``ast.Constant`` that IS a docstring: the first statement
    of a module, class or function body, and nothing else. A dict value, a
    default argument, an f-string fragment - none of those are "the first
    statement of a body", so none of them are exempted here."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = node.body
            first = body[0] if body else None
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                ids.add(id(first.value))
    return ids


#: The `re` functions whose pattern argument is not copy: it is matched
#: against what a USER TYPES, never shown to them. `objects.py:520` builds
#: `_COORD_RE` with `[m'’]`/`[s"”]` in its character classes - a
#: curly quote accepted as an alternative to a straight one for arcmin/arcsec
#: in a typed coordinate, exactly the kind of stray punctuation this file's
#: shim already tolerates elsewhere. Scanning that pattern for "does a curly
#: quote appear" would be scanning the wrong direction of the pipe.
_RE_PATTERN_FUNCS = {"compile", "match", "fullmatch", "search",
                     "sub", "subn", "split", "findall", "finditer"}


def _regex_pattern_constant_ids(tree: ast.AST) -> set[int]:
    """id() of every ``ast.Constant`` that is (part of) the pattern argument
    of a call to one of ``_RE_PATTERN_FUNCS`` on the ``re`` module - found
    structurally (which call, which argument), not by guessing from the text
    that a string "looks like a regex"."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in _RE_PATTERN_FUNCS
                and isinstance(func.value, ast.Name) and func.value.id == "re"):
            continue
        pattern_arg = None
        if node.args:
            pattern_arg = node.args[0]
        else:
            for kw in node.keywords:
                if kw.arg == "pattern":
                    pattern_arg = kw.value
                    break
        if pattern_arg is None:
            continue
        for sub in ast.walk(pattern_arg):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                ids.add(id(sub))
    return ids


def _offenders(relpath: str) -> list[str]:
    """``file:line: string`` for every non-docstring, non-regex-pattern string
    literal in ``relpath`` (relative to astrodeck/) that carries an em-dash,
    en-dash, right arrow, curly quote or emoji. Empty when the module is
    clean."""
    path = _ASTRODECK_ROOT / relpath
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    skip = _docstring_constant_ids(tree) | _regex_pattern_constant_ids(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant)
                and isinstance(node.value, str)):
            continue
        if id(node) in skip:
            continue
        s = node.value
        if any(ch in s for ch in _BAD_CHARS) or any(_is_emoji(c) for c in s):
            found.append(f"{relpath}:{node.lineno}: {s!r}")
    return found


# ----------------------------------------------------------- the shim, as tests

@pytest.mark.parametrize("sep", [
    "\u2014",   # em dash
    "\u2013",   # en dash
    "\u00a0",   # non-breaking space
    "\u2019",   # right single quote
], ids=["em-dash", "en-dash", "nbsp", "right-single-quote"])
def test_squash_designation_drops_stray_punctuation(sep):
    assert squash_designation(f"M {sep} 31") == "m31"


def test_an_em_dash_in_a_designation_still_resolves():
    """End to end, not just the squash: a query typed with an em-dash (a
    beginner pasting a designation out of a book or a website, dash and all)
    still has to find the object through the real ``search()``, not just
    through the helper."""
    result = search("M \u2014 31")
    assert result.rows
    assert result.rows[0]["id"] == "M31"


def test_stale_placeholder_text_returns_a_note_not_a_silent_empty():
    """If a client still ships the OLD placeholder (em-dash and all) as a
    literal query - a stale cached bundle sending its own default text
    unedited - the result is an explanation, not nothing. ``notes`` exists
    exactly so a screen showing zero rows can say why (SearchResult, and the
    no-match note at catalog/objects.py:659-664)."""
    result = search("Search catalog \u2014 e.g. M 31")
    assert result.notes
    assert not result.rows


# ----------------------------------------------------------------- the sweep

#: S7a already swept these clean; keeping them here means a NEW offender in
#: any one of them fails this suite immediately instead of waiting for a future
#: audit to notice.
#:
#: `weather.py` joined the list in T-R7-21a (item 22). It was graded by a
#: separate strict-xfail below, because it built its live "high cloud forecast
#: tonight" bus.log line with an EN DASH between the two clock times, and the
#: marker existed to keep that finding visible without failing the suite. The
#: string is a hyphen now, so the marker is gone and the module is graded like
#: every other: a regression FAILS here rather than xpassing there.
_CLEAN_MODULES = [
    "catalog/objects.py",
    "catalog/solar_system.py",
    "catalog/region.py",
    "sequence/policy.py",
    "weather.py",
    # ``describe()``'s object-brief joiner (`{name} - {body}`) carried an
    # em-dash the screenshot walk caught on every object brief; fixed and
    # clean end to end (the module's one other em-dash is inside
    # ``describe``'s own docstring, which this scan already exempts).
    "catalog/describe.py",
    # The ephemeris package, whole. Every module in it writes sentences a user
    # reads verbatim -- the "no elements downloaded" and "not on the list"
    # refusals, the staleness warnings, the satellite briefs, the comet ones --
    # and they were written after S7a's sweep, so nothing had ever graded them.
    "catalog/ephemeris/elements.py",
    "catalog/ephemeris/satellites.py",
    "catalog/ephemeris/comets.py",
    "catalog/ephemeris/passes.py",
    "catalog/ephemeris/routes.py",
    # ``planning.py``'s strings reach Session and Sky as the plan's own words.
    "planning.py",
]

# ``flows/tonight.py`` is DELIBERATELY NOT on this list. Its ``_NO_SITE``
# string (rendered on Session Now) carried an em-dash and is fixed above, but
# the module's other ~25 narrative strings (campaign-ledger notes, watchdog
# rule copy, moon/meridian lines) still do, and a handful of its em/en-dash
# LITERALS are not copy at all - `_norm_coord`'s `.replace("—", "-")`
# chain matches punctuation the TARGET node ships, the same "matched against
# what a caller wrote, never shown to them" shape this scan already exempts
# for a regex pattern's own literal. Sweeping the narrative copy AND teaching
# the scanner that second exemption is real work with real regression risk
# across Session/Monitor screens this task's screenshot walk never reached -
# left to a dedicated follow-up rather than done here as a side effect of one
# string.


@pytest.mark.parametrize("relpath", _CLEAN_MODULES)
def test_no_user_facing_server_string_carries_an_em_dash(relpath):
    """Scans STRING LITERALS ONLY under the modules that produce user-facing
    copy - comments and docstrings are for readers of the source and are left
    alone, which is the whole reason this uses ast and not a grep. The module
    list is a list to be grown, not a claim to have found every surface."""
    # (A `re.compile(...)` pattern argument is excluded too - see
    # `_regex_pattern_constant_ids` - because it matches what a user TYPES, it
    # is never text shown TO one.)
    offenders = _offenders(relpath)
    assert not offenders, "\n" + "\n".join(offenders)
