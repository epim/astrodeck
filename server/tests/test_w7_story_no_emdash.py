# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""WP-50 (b), #561: the STORY tab's rows carry no em-dash.

``tonight.py``'s ``_story`` wrote U+2014 into several rows - every BUDGET row,
the moon rows, the meridian row, the dusk-flats row, the no-dusk row, and the
ANY unsafe rule - against the R7 copy rule the #/next sheet states: hyphens,
never em-dashes. The UI-side fix (``plainDashes`` on ``row.msg``,
``TonightStoryList.tsx``) is covered by ``tonightDom.test.tsx``; this file
holds the SERVER half of #561's fix shape ("the server stops writing
em-dashes") directly against ``_story``'s own output, so a future sentence
added to this function is graded by a check that can actually fail.

A made-up site, not the observatory's.
"""
from __future__ import annotations

import datetime as _dt

from astrodeck.flows.models import FlowEdge, FlowGraph, FlowNode
from astrodeck.flows.tonight import resolve_tonight

SITE = {"latitude": 40.0, "longitude": -105.0, "elevation_m": 1600.0,
        "is_default": False}
TWILIGHT = -12.0
JUNE = _dt.datetime(2026, 6, 15, 20, 0, tzinfo=_dt.timezone.utc).timestamp()
DECEMBER = _dt.datetime(2026, 12, 15, 20, 0, tzinfo=_dt.timezone.utc).timestamp()

M31 = {"name": "M31", "ra": "00h 42m 44s", "dec": "+41 16 09"}
EM_DASH = "—"


def _n(nid, ntype, x=0.0, y=0.0, **params):
    return FlowNode(id=nid, type=ntype, x=x, y=y, params=params)


def _e(a, ap, b, bp):
    return FlowEdge(**{"from": a, "fromPort": ap, "to": b, "toPort": bp})


def _flow_with_rules() -> FlowGraph:
    """A DOME + DUSK-FLATS flow arming M31 with a Ha CAPTURE LOOP (banked
    figure) and an L, R FILTER CYCLE (cycle's banked figure), a SAFETY
    monitor wired to abort (the ANY unsafe rule), so every BUDGET shape and
    the ANY rule all draw a row from one resolve."""
    return FlowGraph(nodes=[
        _n("d", "dusk", offset=-30, stop="Dawn", minAlt=30),
        _n("df", "duskflats", window="Civil to nautical", count=12,
          method="Translucent lens cap"),
        _n("t", "target", x=100, **M31),
        _n("c", "capture", x=200, filter="Ha", exposure=900, gain=100,
          bin="1", count=8, goal=2.0),
        _n("y", "cycle", x=300, plan="L 60, R 60", cycles=10, perCycle=1,
          gain=100, bin="1"),
        _n("s", "safety", x=0, y=100, source="Cloud + rain sensor",
          watch="Clouds + rain + wind (standalone)", stale="Unsafe (fail closed)"),
        _n("h", "holdresume", x=100, y=100),
    ], edges=[
        _e("d", "window", "t", "arm"),
        _e("t", "target", "c", "run"),
        _e("c", "complete", "y", "run"),
        _e("s", "unsafe", "h", "pause"),
    ])


def _resolve(*, banked=None, now=JUNE):
    kw = {} if banked is None else {"banked": banked}
    return resolve_tonight(_flow_with_rules(), SITE, now=now,
                           twilight_deg=TWILIGHT, **kw)


def _rows(out: dict) -> list[str]:
    """Every STORY row's ``msg``.

    W7 FOLLOW-ON (WP-50, #561 class): the "what gets imaged" row
    (``_target_rows``'s "above X deg - slew, center, focus..." sentence)
    used to be excluded here, because that row was NOT one of #561's six
    catalogued shapes (BUDGET, moon, meridian, dusk-flats, no-dusk, ANY
    unsafe) and still carried its own em-dash. ``_target_rows`` now writes
    a plain hyphen there (and in its "never clears" and "No coordinates
    for" rows), so this helper covers every row, with no exclusion left.
    ``_target_rows``'s "Pool re-scores" row still carries an em-dash, but
    this fixture's flow is not pooled, so it never reaches that branch."""
    return [s["msg"] for s in out["story"]]


class TestNoRowCarriesAnEmDash:
    def test_every_row_is_clean_with_a_ledger(self):
        """Both BUDGET shapes (a capture and a cycle) with a ledger read,
        plus the ANY unsafe rule: every row checked for #561's six
        catalogued shapes at once.

        RED under mutant "the capture row's dash restored" (``_story``'s
        banked-capture-with-ledger sentence's ``"... h goal{panels} - tonight
        adds "`` put back to ``"... h goal{panels} — tonight adds "``),
        observed:
            AssertionError: an em-dash in a STORY row: 'Ha: 1 h banked for
            these targets / 2 h goal — tonight adds ≈2 h; the
            session ledger resumes the remainder next clear night'
        """
        out = _resolve(banked=lambda: {"Ha": 1.0, "L": 0.2, "R": 0.1})
        rows = _rows(out)
        offenders = [m for m in rows if EM_DASH in m]
        assert offenders == [], f"an em-dash in a STORY row: {offenders}"
        budget_lines = [s["msg"] for s in out["story"] if s["label"] == "BUDGET"]
        assert len(budget_lines) == 2, (
            f"premise: a capture row and a cycle row: {budget_lines}")

    def test_every_row_is_clean_with_no_ledger(self):
        """The no-ledger BUDGET sentences (both shapes), which are worded
        differently from the with-ledger ones and carried their own dashes."""
        out = _resolve(banked=None)
        offenders = [m for m in _rows(out) if EM_DASH in m]
        assert offenders == [], f"an em-dash in a STORY row: {offenders}"

    def test_the_moon_and_meridian_rows_are_clean_in_december(self):
        """December at this site: the moon rises AND sets in the dark
        window (unlike June's fixture), and M31 crosses the meridian, so
        both of #561's moon sentences and the meridian sentence all draw."""
        out = _resolve(now=DECEMBER)
        moon_or_meridian = [m for m in _rows(out)
                            if "Moon" in m or "meridian" in m]
        assert len(moon_or_meridian) >= 2, (
            f"premise: at least a moon row and the meridian row: "
            f"{moon_or_meridian}")
        offenders = [m for m in moon_or_meridian if EM_DASH in m]
        assert offenders == [], f"an em-dash in a STORY row: {offenders}"
