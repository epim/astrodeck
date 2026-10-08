# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Tonight places a flat-PANEL DUSK FLATS block where the engine runs it
(#744, with #603 job B's semantics; wave 17 integration).

THE CLAIM NOTHING KEPT. ``resolve_tonight`` returned the block's Sun window
(``flats.start_unix`` / ``end_unix``, read out of the node's "Wait for" text)
for every method, and the timeline drew a FLATS block there. The engine's
stage (``SequenceEngine._dusk_flats``) runs the flat-PANEL method once per
night, when the run reaches it, before the first light, and does NOT wait for
the Sun band: a panel is a constant light source. So for a panel block the
timeline told the operator to plan the evening around an hour the engine does
not use. The STORY row was already timed at the run's start (WP-134); the
structured answer the timeline draws from was not.

THE RULE, as this file holds it. For a block the engine RUNS as a panel the
answer drops the window times (``start_unix`` / ``end_unix`` are None, so a
client that knows nothing of the new keys draws nothing wrong) and says where it
happens: ``runs_at_start`` true and ``at_unix`` the run's start, the SAME
instant the STORY row is timed at. For every block the engine does not run as
a panel (the lens cap and the twilight sky, which are carried and not run; a
panel block the plan drops as unusable; the switch off) the answer is what it
always was: the window, ``runs_at_start`` false, ``at_unix`` None.

Site: made up (``test_w15_dusk_flats_claim``'s), and the hub is pinned to it.

NAMED MUTANTS (each run from a byte backup inside this worktree, restored
byte-identically with sha256 compared); the observed failure is quoted in the
test that catches it.
"""
from __future__ import annotations

import pytest

from astrodeck.flows import to_plan
from test_w15_dusk_flats_claim import (  # noqa: F401  (synthetic_hub is a fixture)
    _flats_row, _hand, _tonight, synthetic_hub,
)


def test_a_panel_block_is_not_placed_at_the_sun_window():
    """The flat-panel method: no window times, and the run's start instead,
    which is the instant the STORY row is timed at.

    MUTANT "panel keeps the Sun window" (tonight.py: ``if runs_at_start:``
    made ``if False:``, so the window is resolved for every method) turned
    this red (1 failed, 6 passed), observed:

        AssertionError: a panel block was placed at the Sun window: start
        1781577473.753357 end 1781579812.2024536
        assert (1781577473.753357 is None)
    """
    out = _tonight(_hand())
    flats = out["flats"]
    assert flats["runs_at_start"] is True, flats
    assert flats["start_unix"] is None and flats["end_unix"] is None, (
        f"a panel block was placed at the Sun window: start "
        f"{flats['start_unix']} end {flats['end_unix']}")
    start = out["night"]["window_start_unix"]
    assert start is not None and flats["at_unix"] == start, flats
    assert _flats_row(out["story"])["t_unix"] == flats["at_unix"], (
        "the structured answer and the STORY row disagree about when")


@pytest.mark.parametrize("method", ["Translucent lens cap", "Twilight sky"])
def test_a_block_the_engine_does_not_run_keeps_its_window(method):
    """The lens cap and the twilight sky are carried and not run: they are
    drawn at the Sun window, as before, and worded "drawn but not run" in the
    STORY.

    MUTANT "every method is at the start" (tonight.py: ``runs_at_start, _plan,
    _why = _dusk_flats_runs(plan_dict, graph)`` made ``= True, None, ''``)
    turned both cases red, and the dropped-block and switch-off cases with
    them (5 failed, 2 passed), observed:

        AssertionError: {'adu_target': 28500, 'at_unix': 1781582625.402832,
        'count': 3, 'end_unix': None, ...}
        assert (True is False)
    """
    out = _tonight(_hand(method=method))
    flats = out["flats"]
    assert flats["runs_at_start"] is False and flats["at_unix"] is None, flats
    assert flats["start_unix"] is not None and flats["end_unix"] is not None, flats
    assert flats["start_unix"] < flats["end_unix"] < out["night"]["dusk_unix"]
    assert "is drawn but not run" in _flats_row(out["story"])["msg"]


@pytest.mark.parametrize("bad", [{"count": 0}, {"count": 5000}])
def test_a_panel_block_the_plan_drops_keeps_its_window_and_is_not_promised(bad):
    """A panel block with a count the stage refuses is NOT run, so it is not
    placed at the run's start either: it stays at its window, "drawn but not
    run: the engine cannot run it as set". The mark follows what the engine
    does, not what the node's Method says."""
    out = _tonight(_hand(**bad))
    flats = out["flats"]
    assert flats["runs_at_start"] is False and flats["at_unix"] is None, flats
    assert flats["start_unix"] is not None, flats
    assert "cannot run it as set" in _flats_row(out["story"])["msg"]


def test_with_the_switch_off_a_panel_block_keeps_its_window(monkeypatch):
    """``to_plan.DUSK_FLATS_WIRED`` is the one switch (#192): off, nothing is
    run, so nothing is placed at the run's start.

    MUTANT "copy ignores the switch" (``_dusk_flats_runs``'s ``if not
    to_plan.DUSK_FLATS_WIRED: return ...`` removed) turned this red (1 failed,
    6 passed), observed:

        AssertionError: {'adu_target': 28500, 'at_unix': 1781582625.402832,
        'count': 3, 'end_unix': None, ...}
        assert (True is False)
    """
    monkeypatch.setattr(to_plan, "DUSK_FLATS_WIRED", False)
    flats = _tonight(_hand())["flats"]
    assert flats["runs_at_start"] is False and flats["at_unix"] is None, flats
    assert flats["start_unix"] is not None and flats["end_unix"] is not None, flats


def test_no_site_derived_figure_travels_in_the_new_keys():
    """The Tonight answer is served to roles with no site view (#19). The run
    start is a time the answer already carries (``night.window_start_unix``);
    the new keys add no other site-derived value: ``at_unix`` IS that value and
    ``runs_at_start`` is a boolean."""
    out = _tonight(_hand())
    flats = out["flats"]
    assert isinstance(flats["runs_at_start"], bool)
    assert flats["at_unix"] == out["night"]["window_start_unix"]
