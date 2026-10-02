# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Tonight shows the compiled DUSK WINDOW: a Clock-time Start/Stop, and a
Stop of None (#191).

THE DEFECT, IN TWO PLACES.

* ``resolve_tonight`` only ever drew the autorun window at ``dusk + offset``
  (for ``start_mode == "dusk"``) or at ``now`` for anything else - so a
  "Clock time" Start, which compiles to ``start_mode: "time"`` with its own
  ``start_time``, still drew the window opening at ``now``, and the STORY
  tab's "the window opens" row said "No dusk window in this flow" for a
  flow that plainly had one. The same held for a "Clock time" Stop: nothing
  drew ``window_stop_unix`` for it at all.
* THE STORY'S CLOSING LINE claimed "Dawn: loop ends, mount parks, camera
  warms" UNCONDITIONALLY, whatever the DUSK WINDOW's Stop said - the same
  broken promise the auto-resume tooltip made, per the owner's 2026-09-24
  answer on #189 ("It automatically parks at dawn"). A flow with Stop
  "None" read a promise the run does not keep.

THE FIX. ``resolve_tonight`` resolves a "Clock time" boundary the same way
``schedule._resolve_event_ts`` does (the nearest occurrence of the card's
own "HH:MM" within +/-12h of ``now``), for both the window's start and its
stop, and the STORY tab's opening and closing rows say what the compiled
schedule actually does: a Clock stop parks too (``park_when_done`` runs
whenever the run ends, not only at dawn) but at ITS OWN clock, and a Stop
of "None" warns rather than promises.

SITE is a fake mid-latitude site (test_flows_tonight.py's own, never the
observatory's). NOW is a fixed UTC instant, late afternoon local, so
"tonight" is ahead of it and a clock time a few hours later resolves to
tonight rather than rolling to tomorrow.

Mutants were run from a byte backup inside this worktree (never in the
shared tree), and each failure is quoted verbatim.
"""
from __future__ import annotations

import datetime as _dt

from astrodeck.flows.models import FlowGraph, FlowNode
from astrodeck.flows.tonight import resolve_tonight
from astrodeck.sequence.schedule import _clock_time_near_now

SITE = {"latitude": 40.0, "longitude": -105.0, "elevation_m": 1600.0,
       "is_default": False}
TWILIGHT = -12.0

#: Late afternoon UTC at the site (SITE is ~7h behind UTC in June), so
#: tonight is ahead and a clock time a few hours later is tonight's.
NOW = _dt.datetime(2026, 6, 15, 20, 0, tzinfo=_dt.timezone.utc).timestamp()


def _dusk_only_flow(start="Astro dusk", stop="Dawn", start_clock="",
                    stop_clock="") -> FlowGraph:
    return FlowGraph(nodes=[FlowNode(id="d", type="dusk", params={
        "start": start, "offset": -30, "startClock": start_clock,
        "stop": stop, "stopClock": stop_clock, "minAlt": 30,
    })])


def _tonight(graph, **kw):
    kw.setdefault("twilight_deg", TWILIGHT)
    return resolve_tonight(graph, SITE, now=NOW, **kw)


# --------------------------------------------------------------- the window

def test_a_clock_time_start_draws_the_window_at_its_own_clock_not_now():
    """Mutant "time mode falls to now" (the ``elif start_mode == "time"``
    branch removed from ``resolve_tonight``, falling to the ``else: t_now``
    it shared with "no window at all"): RED, observed:
        assert 1781553600.0 == 1781589600.0
    """
    out = _tonight(_dusk_only_flow(start="Clock time", start_clock="23:00"))
    assert out["ok"] is True, out["reason"]
    want = _clock_time_near_now("23:00", NOW)
    assert want is not None and want != NOW
    assert out["night"]["window_start_unix"] == want


def test_a_clock_time_stop_draws_the_window_closing_at_its_own_clock():
    """Before this fix nothing resolved a "Clock time" Stop at all - the
    ``else`` branch answered ``None``, the same as "no stop"."""
    out = _tonight(_dusk_only_flow(stop="Clock time", stop_clock="05:00"))
    assert out["ok"] is True, out["reason"]
    want = _clock_time_near_now("05:00", NOW)
    assert want is not None
    assert out["night"]["window_stop_unix"] == want


def test_a_stop_of_none_leaves_the_window_with_no_close():
    out = _tonight(_dusk_only_flow(stop="None"))
    assert out["night"]["window_stop_unix"] is None


# ------------------------------------------------------------- the story tab

def test_the_story_says_the_window_opens_at_the_clock_for_a_clock_start():
    """Before this fix this row read "No dusk window in this flow - the run
    starts when you press RUN", although the flow had a DUSK WINDOW with a
    real, compiled start time.

    Mutant "time start has no story row" (the ``elif start_mode == "time"``
    branch removed from ``_story``, falling to its ``else``): RED, observed:
        assert 'clock time' in 'no dusk window in this flow — the run
        starts when you press run, and stops when you stop it'
    """
    out = _tonight(_dusk_only_flow(start="Clock time", start_clock="23:00"))
    first = out["story"][0]
    assert "clock time" in first["msg"].lower(), first
    assert first["t_unix"] == out["night"]["window_start_unix"]


def test_the_story_s_closing_line_matches_a_clock_time_stop():
    """A CLOCK STOP PARKS TOO: ``park_when_done`` runs whenever the run
    ends, not only at dawn, so the sentence says so at ITS OWN clock rather
    than naming "Dawn" for an instant nowhere near it.

    NOT NECESSARILY THE LAST ROW (unlike a Dawn stop): a clock stop can
    land earlier than other night events (moonset, astronomical dark), so
    this is found by its own timestamp, not by list position - the timeline
    is a chronological list, and an early stop is not "the end" of it."""
    out = _tonight(_dusk_only_flow(stop="Clock time", stop_clock="05:00"))
    stop_unix = out["night"]["window_stop_unix"]
    assert stop_unix is not None
    rows = [r for r in out["story"] if r["t_unix"] == stop_unix]
    assert len(rows) == 1, out["story"]
    assert "mount parks" in rows[0]["msg"], rows[0]
    assert "Dawn" not in rows[0]["msg"], rows[0]


def test_the_story_s_closing_line_warns_when_no_stop_is_configured():
    """Mutant "the closing line ignores stop_mode" (the code before this
    fix, always ``row(dawn, "Dawn: loop ends, mount parks, camera
    warms...")``): RED, observed:
        assert 'not parked' in 'Dawn: loop ends, mount parks, camera warms.'
    """
    out = _tonight(_dusk_only_flow(stop="None"))
    closer = out["story"][-1]
    assert "not parked" in closer["msg"], closer
    assert closer["tone"] == "warn", closer


def test_the_story_s_closing_line_is_unchanged_for_a_dawn_stop():
    """The control: the ordinary case reads exactly as it always has."""
    out = _tonight(_dusk_only_flow(stop="Dawn"))
    closer = out["story"][-1]
    assert closer["msg"] == "Dawn: loop ends, mount parks, camera warms"
    assert closer["t_unix"] == out["night"]["dawn_unix"]
