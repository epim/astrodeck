"""Every mutating API route is reachable from the app, or says why it is not.

THE DOMINANT DEFECT CLASS IN THIS PROJECT is a feature that is built,
unit-tested, and unreachable by the real path. Two of them landed in the same
week and both were found by an operator on a rig at night, not by the suite:

* ``POST /api/rotator/sync-to-sky`` shipped in 0.2.65 to answer "there is no way
  to sync the rotator without moving it" — and no UI code ever called it, so the
  complaint it was written for was still true after it shipped (#168).
* ``PUT /api/guide/camera-settings`` shipped with dials that PUT to it, and
  422'd every request (a separate bug), which nothing noticed because no test
  crossed the seam either.

Both halves were tested. The seam between them never was. This is that seam,
asserted the cheap way: a route that changes something must be named somewhere
in the UI source, or appear below with a reason.

DELIBERATELY LIMITED. It proves a route is REFERENCED, not that the reference
is correct or reachable — a button behind a permission nobody has would still
pass. It is a smoke detector for whole features wired to nothing, and that is
the failure that keeps happening.

TWO WAYS IT WAS BLIND, both closed on 2026-08-08 (#197):

* A COMMENT SATISFIED IT. ``_is_referenced`` matched the whole file as text, so
  when the UI stopped calling ``PUT /api/guide/camera-settings`` a stale comment
  in GuideQuickBar.tsx that still named the path kept the route looking wired.
  A detector a comment can satisfy is a detector a comment can blind, and that
  is precisely the shape it exists to catch. It now matches only inside STRING
  LITERALS — where a URL a caller actually fetches has to live — which excludes
  ``//``, ``/* */`` and JSX ``{/* */}`` comments by construction rather than by
  a list of comment syntaxes to remember.
* IT ONLY READ app.py. Nine mutating routes are declared on ``APIRouter``s in
  other modules — every user-management route, and the whole local-auth login
  flow — and none of them was ever in the table. A detector that silently omits
  a ninth of what it is checking is the same failure one layer up.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pytest

_SERVER = Path(__file__).resolve().parents[1]
_UI_SRC = _SERVER.parent / "ui" / "src"
_PKG = _SERVER / "astrodeck"

#: Mutating routes with no UI caller ON PURPOSE. Each needs a reason — the list
#: is documentation, and a bare path here defeats the whole test.
_NO_UI_CALLER = {
    "/api/connect/sim":
        "demo/test entry point; the app connects rigs through profiles. The "
        "ONE route the comment fix changed the verdict on: ui/src names this "
        "path exactly once, inside a comment, so under the old whole-file "
        "match it looked wired and it never was. The exemption was already "
        "right; now it is right for a checkable reason",
    "/api/connect/alpaca":
        "profile-driven: the UI edits a profile and connects it, it never "
        "posts a bare Alpaca connect",
    "/api/connect/nina":
        "as /api/connect/alpaca — reached by connecting a NINA profile",
    "/api/connect/phd2":
        "as /api/connect/alpaca — reached by connecting a PHD2 profile",
    "/api/safety/simulate":
        "a test hook for driving the safety monitor; deliberately has no "
        "button, since a UI that can fake 'unsafe' can also fake 'safe'",
    "/api/remote/config":
        "written by the relay pairing flow and the installer, not by hand",
    "/api/guide/camera-settings":
        "as /api/polar/solve-settings below — a compatibility alias onto "
        "PUT /api/camera/frame-settings?scope=guide, which the UI calls "
        "(GuideQuickBar -> store.setFrameSettings). This is the route that "
        "exposed the comment hole: it was NOT flagged when the UI stopped "
        "calling it, because a stale comment in GuideQuickBar.tsx still named "
        "the path. `_is_referenced` now reads string literals only, so the "
        "same comment would no longer cover for it (#197)",
    "/api/polar/solve-settings":
        "a COMPATIBILITY ALIAS, not an unreachable feature: since #176 it is a "
        "thin delegate onto PUT /api/camera/frame-settings?scope=solve, which "
        "the UI does call (ui/src/store.ts setFrameSettings). Same store, same "
        "validation, same `frames` announcement — so the feature behind this "
        "path is reachable, and test_frame_settings.py asserts the two doors "
        "open onto one room. The path stays because external clients and "
        "test_polar_solve_settings.py name it",
}

#: Routes that are UNREACHABLE AND SHOULD NOT BE — found by this test on
#: 2026-08-08, the day it was written, and not yet triaged with the owner.
#:
#: Separate from the list above on purpose. Folding a real gap into "deliberate"
#: is exactly how a detector becomes a rubber stamp: the entry stops being a
#: finding and becomes a permission slip. These are findings. The test below
#: refuses to let the list GROW, so tomorrow's orphan cannot hide among
#: yesterday's.
_KNOWN_UNREACHABLE = {
    "/api/auth/revoke":
        "no UI anywhere revokes a session; sign-everyone-out exists only over "
        "the API",
    "/api/auth/unrevoke": "the other half of the same missing screen",
    "/api/calibrator/on":
        "flat-panel control has no UI at all — the whole calibrator device is "
        "driveable only by the sequencer's flat targets",
    "/api/calibrator/off": "as /api/calibrator/on",
    "/api/calibrator/cover": "as /api/calibrator/on",
}


#: Every mutating decoration, on ``@app`` in api/app.py and on ``@router`` in
#: the seven modules that mount their own APIRouter. Both quote styles, because
#: a table that silently drops a route is the failure this file is about.
_ROUTE_DECL = re.compile(
    r"""@(?:app|router)\.(?:post|put|delete|patch)\(\s*['"](/(?:api|auth)/[^'"]+)['"]""")


def _routes() -> set[str]:
    """Every mutating route the server declares (POST/PUT/DELETE/PATCH).

    THE WHOLE PACKAGE, not just api/app.py. ``/api/users``, ``/auth/local`` and
    seven more live on APIRouters in other modules, and reading one file meant
    nine mutating routes — including all of user management — were never
    checked by anything here.
    """
    out: set[str] = set()
    for path in sorted(_PKG.rglob("*.py")):
        out |= set(_ROUTE_DECL.findall(path.read_text(encoding="utf-8")))
    return out


def _ui_text() -> str:
    files = [p for ext in ("*.ts", "*.tsx")
             for p in _UI_SRC.rglob(ext)
             if "__tests__" not in p.parts]
    assert files, f"no UI sources under {_UI_SRC} — the test would pass vacuously"
    return "\n".join(p.read_text(encoding="utf-8", errors="ignore")
                     for p in files)


def _string_literals(src: str) -> list[str]:
    """The contents of every string literal in some TS/TSX source.

    A URL the browser actually requests is a string at the point it is
    requested — ``api.post("/api/x")``, ``` `/api/x/${id}` ```, a constant in a
    table. Prose about a route is not. Scanning for literals therefore drops
    ``//``, ``/* */`` and JSX ``{/* */}`` comments as a CONSEQUENCE of what it
    keeps, rather than by stripping three comment syntaxes and hoping there
    isn't a fourth.

    Two deliberate imprecisions, both chosen so a mis-parse costs a missed
    warning and never a false alarm:

      * A ``'`` or ``"`` string may not span a newline in JS, so a quote that
        reaches end-of-line is not a string at all — it is an apostrophe in
        prose or a quote inside a regex literal. Scanning resumes one character
        later, which CONTAINS the mis-read to that one quote instead of letting
        it swallow the rest of the file.
      * A template literal is taken whole, interpolations included. That can
        pull a little code into the haystack; it cannot hide a call.
    """
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == "/" and i + 1 < n:
            if src[i + 1] == "/":                       # line comment
                j = src.find("\n", i)
                i = n if j < 0 else j
                continue
            if src[i + 1] == "*":                       # block / JSX comment
                j = src.find("*/", i + 2)
                i = n if j < 0 else j + 2
                continue
        if c in "\"'`":
            j, buf = i + 1, []
            while j < n:
                d = src[j]
                if d == "\\":                            # escape: skip the pair
                    j += 2
                    continue
                if d == c or (d == "\n" and c != "`"):
                    break
                buf.append(d)
                j += 1
            if j < n and src[j] == c:
                out.append("".join(buf))
                i = j + 1
            else:
                i += 1                                   # not a string; recover
            continue
        i += 1
    return out


@lru_cache(maxsize=4)
def _callable_text(blob: str) -> str:
    """``blob`` reduced to the parts of it a URL can be requested from."""
    return "\n".join(_string_literals(blob))


def _is_referenced(route: str, blob: str) -> bool:
    """Is this route named in the UI, somewhere it could actually be called?

    Path parameters are template holes (``/api/foo/{id}/bar``), and the UI
    builds those with interpolation, so match on the literal prefix before the
    first hole. That is looser than an exact match and deliberately so: a false
    PASS here costs a missed warning, a false FAIL costs a suite nobody trusts.

    What it is NOT loose about is WHERE the name appears. Matching the raw file
    let a comment stand in for a caller — see this module's docstring — so the
    haystack is the file's string literals only.
    """
    head = route.split("{")[0].rstrip("/")
    return head in _callable_text(blob)


def test_the_route_list_is_not_empty():
    """The parser's own positive control. A regex that silently stops matching
    would make every assertion below vacuous."""
    routes = _routes()
    assert len(routes) > 50, (
        f"only {len(routes)} mutating routes parsed out of the package — the "
        f"pattern has probably drifted")
    assert "/api/rotator/sync-to-sky" in routes


def test_the_table_reaches_past_app_py():
    """Nine mutating routes are declared on APIRouters outside api/app.py, and
    for as long as the parser read one file none of them was checked by
    anything. One from each of the two modules that matter most — user
    management and login — so shrinking the scan back to app.py fails here
    rather than passing quietly with a ninth of the table missing."""
    routes = _routes()
    for path in ("/api/users", "/auth/local", "/api/framing/mosaic",
                 "/api/visibility/order"):
        assert path in routes, (
            f"{path} is a mutating route the server declares and the table "
            f"does not contain it — the scan is not reading the whole package")


def test_every_mutating_route_is_reachable_from_the_ui():
    blob = _ui_text()
    known = set(_NO_UI_CALLER) | set(_KNOWN_UNREACHABLE)
    orphans = sorted(r for r in _routes()
                     if r not in known and not _is_referenced(r, blob))
    assert orphans == [], (
        "these routes change something on the rig and nothing in the UI calls "
        "them, so the feature they implement cannot be reached by a user. Wire "
        "them up, or add each to _NO_UI_CALLER with the reason:\n  "
        + "\n  ".join(orphans))


def test_the_exemption_list_carries_reasons_and_stays_current():
    """An allowlist that outlives its routes is how this test rots into a
    rubber stamp."""
    routes = _routes()
    for path, why in {**_NO_UI_CALLER, **_KNOWN_UNREACHABLE}.items():
        assert why.strip(), f"{path} is exempted with no reason"
        assert path in routes, (
            f"{path} is exempted but no longer exists — drop it from the list")


def test_the_known_gap_list_does_not_grow():
    """Today's orphan must not hide among yesterday's.

    The count is the assertion. Fixing one of these means lowering the number,
    which is the only direction it may move without a deliberate edit and a
    reason in the diff.
    """
    assert len(_KNOWN_UNREACHABLE) <= 5, (
        "a new unreachable route was filed as a known gap instead of being "
        "wired up or exempted with a reason")


def test_a_known_gap_is_not_also_claimed_deliberate():
    """The two lists say opposite things; an entry in both means nobody
    decided."""
    overlap = set(_NO_UI_CALLER) & set(_KNOWN_UNREACHABLE)
    assert overlap == set(), overlap


def test_the_detector_would_have_caught_the_rotator_button():
    """The regression that motivated this file, run against a UI that lacks it.

    Without this the test above could be passing because the detector cannot
    detect anything.
    """
    assert not _is_referenced("/api/rotator/sync-to-sky",
                              "api.post('/api/rotator/rotate-to-pa')")
    assert _is_referenced("/api/rotator/sync-to-sky",
                          'api.post("/api/rotator/sync-to-sky", {})')


# ------------------------------------------------- a comment is not a caller

_ROUTE = "/api/guide/camera-settings"


@pytest.mark.parametrize("source", [
    f"// PUT {_ROUTE} used to be sent from here\nconst x = 1;",
    f"  const x = 1; // superseded by {_ROUTE}",
    f"/* the old door was {_ROUTE} */\nconst x = 1;",
    f"/**\n * @deprecated use {_ROUTE}\n */\nexport const x = 1;",
    f"<div>{{/* TODO: wire {_ROUTE} back up */}}</div>",
    f"// https://example.invalid{_ROUTE}\nconst x = 1;",
])
def test_a_comment_does_not_count_as_a_caller(source):
    """THE FINDING. Every one of these passed the old whole-file match, and the
    fifth shape — a JSX comment — is the one that actually shipped."""
    assert not _is_referenced(_ROUTE, source), source


@pytest.mark.parametrize("source", [
    f'api.put("{_ROUTE}", body)',
    f"api.put('{_ROUTE}', body)",
    f"api.put(`{_ROUTE}`, body)",
    f"const P = `{_ROUTE}?scope=${{scope}}`;",
    f'const T = {{ guide: "{_ROUTE}" }};   // the table IS the call site',
    f'  await fetch("{_ROUTE}", {{ method: "PUT" }});\n',
])
def test_a_real_call_site_still_counts(source):
    """The other half. A detector tightened until it flags working code is one
    the next person turns off, which is worse than the hole it closed."""
    assert _is_referenced(_ROUTE, source), source


def test_an_apostrophe_in_prose_cannot_blind_the_rest_of_the_file():
    """The one way a literal scanner can go badly wrong: an unpaired quote that
    swallows everything after it. A ``'`` string cannot span a newline in JS, so
    the scan gives up on that quote at end-of-line and recovers — the call two
    lines below is still found."""
    src = ("// it's the rotator panel that owns this\n"
           "const re = /[\"']/;\n"
           f'api.put("{_ROUTE}", body)\n')
    assert _is_referenced(_ROUTE, src)


def test_the_real_ui_still_reads_as_a_ui():
    """A scanner that mis-parsed the real sources would report every route
    unreferenced, and the orphan assertion would then be flagging the parser
    rather than the app. Guard it with a route nothing could take away."""
    text = _callable_text(_ui_text())
    assert len(text) > 50_000, f"only {len(text)} chars of string literal"
    for path in ("/api/status", "/api/mount/goto", "/auth/local"):
        assert path in text, f"{path} vanished from the UI's string literals"
