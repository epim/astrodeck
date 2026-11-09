# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""``ui/src/types.ts`` mirrors the coverage route's answer (#177 package 2,
WP 6.5).

``CoverageAnswer``, ``CoverageGroup`` and ``CoveragePanel`` are what the
framing modal reads in run mode. They are compared with what the server sends,
both ways round, in the style of test_types_mirror_status.py (whose parser and
JSON-type check this file imports): a key the server sends that types.ts lacks
is data no screen can read, and a key types.ts declares that the server never
sends reads undefined for ever.

Two comparisons, because the wire has two kinds of fact:

* THE KEYS, level by level, against ``test_w16_coverage_route.ALLOWED``, the
  allow-list the route's own test holds the wire to (spec 6.9, #19). types.ts
  and the route are then one list, and a key added to the route has to be
  added here in a diff somebody reads.
* THE VALUES, against ``sequence.coverage.coverage_report`` on real header-only
  lights: one panel with a flagged frame (every count and fraction a number)
  and one with nothing stamped (the three ``worst_*`` null), so a member the
  type must admit as null is seen null once and as a number once.

MUTATIONS (byte backup of ui/src/types.ts, restored byte-identically):
``unstamped`` removed from ``CoveragePanel`` -> "the coverage panel type
drifted: the server sends ['unstamped'], which types.ts does not declare".
"""
from __future__ import annotations

import pytest

from astrodeck.sequence import coverage
from astrodeck.sequence.session import Session
from test_types_mirror_groups import TYPES_TS, _interface
from test_types_mirror_status import _drift, _wrong
from test_w16_coverage_route import ALLOWED
from test_w16_sequence_coverage import (FOV_X, _frame, _header, _on_tile_light,
                                        _plan, _write)

pytestmark = pytest.mark.skipif(
    not TYPES_TS.exists(), reason="ui/ not present (server-only checkout)")


def test_the_coverage_types_declare_the_keys_the_route_is_held_to():
    """``CoverageAnswer`` is the route's top level, ``CoverageGroup`` a group
    and ``CoveragePanel`` a panel; the session object inside the answer is an
    inline type, so its keys are read from the member's own text."""
    for name, level in (("CoverageAnswer", "top"), ("CoverageGroup", "group"),
                        ("CoveragePanel", "panel")):
        declared = set(_interface(name))
        assert declared == ALLOWED[level], _drift(
            f"the coverage {level} type ({name})", ALLOWED[level], declared)
    session = _interface("CoverageAnswer")["session"]
    for key in sorted(ALLOWED["session"]):
        assert f"{key}:" in session, (
            f"CoverageAnswer.session does not declare {key}: {session}")
    assert "null" in session, "the session is null for a flow with none"


def test_the_coverage_panel_type_admits_the_values_the_verifier_computes(
        tmp_path):
    """A real report over two panels: 1-1 holds an on-tile light and one half
    a field off (a flagged frame, every value a number); 1-2 holds only an
    unstamped light (nothing to measure, the ``worst_*`` null)."""
    coverage.clear_memo()
    on = _on_tile_light(tmp_path, "on.fits")
    off = _on_tile_light(tmp_path, "off.fits", dx=FOV_X / 2.0)
    bare = _write(tmp_path / "bare.fits",
                  _header(ra_hours=5.4321, dec_deg=23.4567, stamped=False))
    plan = _plan(rows=1, cols=2, pa=0.0, panels=((5.0, 23.0), (5.2, 23.0)))
    session = Session(plan=plan, frames=[
        _frame(on, target="pnl-0"), _frame(off, target="pnl-0"),
        _frame(bare, target="pnl-1")])
    group = coverage.coverage_report(session)["groups"][0]
    rows = group["panels"]
    assert rows[0]["flagged"] >= 1 and rows[0]["worst_overlap_frac"] is not None, (
        "premise: 1-1 holds a flagged frame with every measure a number")
    assert rows[1]["stamped"] == 0 and rows[1]["worst_overlap_frac"] is None, (
        "premise: 1-2 holds nothing stamped, so its measures are null")

    ts = _interface("CoveragePanel")
    for row in rows:
        assert set(row) == set(ts), _drift(
            "the coverage panel type", set(row), set(ts))
        assert not _wrong(row, ts), (
            f"types.ts does not admit what the verifier computes: "
            f"{_wrong(row, ts)}")
    ts_group = _interface("CoverageGroup")
    assert set(group) == set(ts_group), _drift(
        "the coverage group type", set(group), set(ts_group))
    assert not _wrong({k: v for k, v in group.items() if k != "panels"},
                      ts_group)
