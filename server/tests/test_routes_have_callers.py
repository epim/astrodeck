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
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_SERVER = Path(__file__).resolve().parents[1]
_UI_SRC = _SERVER.parent / "ui" / "src"
_APP = _SERVER / "astrodeck" / "api" / "app.py"

#: Mutating routes with no UI caller ON PURPOSE. Each needs a reason — the list
#: is documentation, and a bare path here defeats the whole test.
_NO_UI_CALLER = {
    "/api/connect/sim":
        "demo/test entry point; the app connects rigs through profiles",
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
        "(GuideQuickBar -> store.setFrameSettings). NOTE: this was NOT flagged "
        "when the UI stopped calling it, because a stale comment in "
        "GuideQuickBar.tsx still named the path and `_is_referenced` matches "
        "the whole file, comments included. A detector that a comment can "
        "satisfy is one a comment can also blind",
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


def _routes() -> set[str]:
    """Every mutating route the server declares (POST/PUT/DELETE/PATCH)."""
    src = _APP.read_text(encoding="utf-8")
    return set(re.findall(
        r'@app\.(?:post|put|delete|patch)\(\s*"(/api/[^"]+)"', src))


def _ui_text() -> str:
    files = [p for ext in ("*.ts", "*.tsx")
             for p in _UI_SRC.rglob(ext)
             if "__tests__" not in p.parts]
    assert files, f"no UI sources under {_UI_SRC} — the test would pass vacuously"
    return "\n".join(p.read_text(encoding="utf-8", errors="ignore")
                     for p in files)


def _is_referenced(route: str, blob: str) -> bool:
    """Is this route named in the UI?

    Path parameters are template holes (``/api/foo/{id}/bar``), and the UI
    builds those with interpolation, so match on the literal prefix before the
    first hole. That is looser than an exact match and deliberately so: a false
    PASS here costs a missed warning, a false FAIL costs a suite nobody trusts.
    """
    head = route.split("{")[0].rstrip("/")
    return head in blob


def test_the_route_list_is_not_empty():
    """The parser's own positive control. A regex that silently stops matching
    would make every assertion below vacuous."""
    routes = _routes()
    assert len(routes) > 50, (
        f"only {len(routes)} mutating routes parsed out of app.py — the "
        f"pattern has probably drifted")
    assert "/api/rotator/sync-to-sky" in routes


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
