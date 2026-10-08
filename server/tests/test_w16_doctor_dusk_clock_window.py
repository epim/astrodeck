# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The doctor warns when DUSK WINDOW's Clock-time Stop is not after its Clock-
time Start (#719, backlog WP-144; wave 16 integration).

THE DEFECT. A flow with Start "Clock time" 22:00 and Stop "Clock time" 21:00
compiled and ran: both clock cards resolve independently to the occurrence
nearest now (``schedule._clock_time_near_now``, within 12 hours), so the Stop
resolves at or before the Start and the run ends before it begins, with
nothing on the card saying so.

THE RULE (``doctor.check``, beside M9): for a DUSK WINDOW whose Start and Stop
are both "Clock time", read the two "HH:MM" cards, and warn when the forward
gap from Start to Stop is 0 or more than 720 minutes. Silent for a gap of
exactly 720 (the edge), a night past midnight (22:00 to 03:30 is 330), a blank
or unreadable card (M9 and the compile already speak for a blank Stop), and
any other Start or Stop mode (a sun Start needs a site, and that is Tonight's
to say).

A DESIGN-TIME WARNING, NOT THE ENGINE'S PREDICATE: the cards snap
independently to the occurrence within 12 h of the moment the window is
frozen, so a gap under twelve hours can still resolve backwards at some hours
of the day. This catches the windows that are wrong at every hour; the test
for it says so rather than claiming more.

NAMED MUTANTS. Each was run from a byte backup of ``flows/doctor.py`` inside
the integration worktree, restored byte-identically (sha256 compared) and the
mutant text grepped out, under one worker. The assertion each broke is
recorded verbatim on the test that caught it.
"""
from __future__ import annotations

import pytest

from astrodeck.flows.doctor import check
from astrodeck.flows.models import FlowGraph, FlowNode

ROW = "▸ DUSK WINDOW - Stop "


def _dusk(start="Clock time", stop="Clock time", start_clock="22:00",
          stop_clock="21:00", **extra) -> FlowGraph:
    params = {"start": start, "stop": stop, "startClock": start_clock,
              "stopClock": stop_clock, **extra}
    return FlowGraph(nodes=[FlowNode(id="d", type="dusk", x=0.0, y=0.0,
                                     params=params)], edges=[])


def _rows(graph: FlowGraph) -> list:
    return [i for i in check(graph) if i.text.startswith(ROW)]


def test_the_issues_case_a_stop_an_hour_before_the_start_warns():
    """22:00 start, 21:00 stop: 23 hours forward, so the Stop resolves before
    the Start at every hour of the day. The words are the rule's.

    RED under mutant "the rule removed" (the ``if gap == 0 or gap > CLOCK_
    WINDOW_MAX_MIN:`` made ``if False:``), observed:

        AssertionError: []
        assert 0 == 1
         +  where 0 = len([])
    """
    rows = _rows(_dusk())
    assert len(rows) == 1, rows
    assert rows[0].level == "warn"
    assert rows[0].text == (
        "▸ DUSK WINDOW - Stop 21:00 is not after Start 22:00 within a night: "
        "a clock stop resolves to the occurrence nearest now, so this run "
        "would end before it begins. Set a Stop later than the Start (past "
        "midnight is fine, up to 12 hours).")


def test_equal_times_warn():
    """A gap of zero: the Stop IS the Start.

    RED under mutant "equal times pass" (``gap == 0 or`` dropped from the
    test), observed:

        AssertionError: assert 0 == 1
         +  where 0 = len([])
    """
    assert len(_rows(_dusk(start_clock="22:00", stop_clock="22:00"))) == 1


def test_a_night_past_midnight_is_silent():
    """22:00 to 03:30 is 330 minutes forward: the normal imaging night.

    The mutant that guards the fold is "the gap is not folded into a day"
    (``(ended - began) % 1440`` made ``ended - began``): 21:00 reads 60
    minutes BEFORE 22:00 and goes silent, so it is the issue's-case test
    above that goes red (``assert 0 == 1``); this one stays green under it,
    since 03:30 reads 1110 minutes before 22:00 and is silent either way.
    """
    assert _rows(_dusk(start_clock="22:00", stop_clock="03:30")) == []


@pytest.mark.parametrize("stop, warns", [("10:00", False), ("10:01", True)])
def test_exactly_twelve_hours_is_the_edge(stop, warns):
    """22:00 to 10:00 is 720 minutes: allowed. One minute more is not.

    RED under mutant "twelve hours warns" (``gap > CLOCK_WINDOW_MAX_MIN`` made
    ``gap >= CLOCK_WINDOW_MAX_MIN``), observed on the 10:00 case:

        AssertionError: assert (1 == 1) is False
    """
    assert (len(_rows(_dusk(start_clock="22:00", stop_clock=stop))) == 1) is warns


@pytest.mark.parametrize("start_clock, stop_clock", [
    ("22:00", ""), ("", "21:00"), ("", ""), ("00:30", ""),
    ("22:00", "tonight"), ("late", "21:00"), ("22", "21"), ("22:00", "ab:cd"),
    ("22:00", None), (None, "21:00"),
])
def test_a_blank_or_unreadable_card_says_nothing(start_clock, stop_clock):
    """M9 and the compile speak for a blank Stop, and a card that is not
    "HH:MM" is not this rule's to read. ``00:30`` with a blank Stop is the
    case a blank read as midnight would warn on (1410 minutes forward).

    RED under mutant "a blank card reads as midnight" (``_clock_minutes``
    answering 0 for a blank), observed on the ``("00:30", "")`` case:

        AssertionError: assert [Issue(...)] == []
    """
    assert _rows(_dusk(start_clock=start_clock, stop_clock=stop_clock)) == []


@pytest.mark.parametrize("start, stop", [
    ("Astro dusk", "Clock time"),    # a sun Start: Tonight's to say
    ("Clock time", "Dawn"),
    ("Clock time", "None"),
    ("Civil dusk", "Dawn"),
])
def test_only_a_clock_start_and_a_clock_stop_are_read(start, stop):
    """CONTROL: the cards are read only when the modes say "Clock time" for
    BOTH ends; a reversed-looking pair under any other mode is just a card the
    window ignores.

    RED under mutant "the Start mode is not asked" (the ``n.params.get(
    "start") != "Clock time"`` test dropped), observed on the first case:

        AssertionError: assert [Issue(...)] == []
    """
    assert _rows(_dusk(start=start, stop=stop,
                       start_clock="22:00", stop_clock="21:00")) == []


def test_each_dusk_window_is_judged_on_its_own_cards():
    """Two DUSK WINDOWs, one right and one wrong, give exactly one row, and
    it names the wrong one's cards."""
    ok = FlowNode(id="a", type="dusk", x=0.0, y=0.0, params={
        "start": "Clock time", "stop": "Clock time",
        "startClock": "22:00", "stopClock": "03:30"})
    bad = FlowNode(id="b", type="dusk", x=0.0, y=0.0, params={
        "start": "Clock time", "stop": "Clock time",
        "startClock": "20:00", "stopClock": "19:00"})
    rows = _rows(FlowGraph(nodes=[ok, bad], edges=[]))
    assert len(rows) == 1 and "Stop 19:00" in rows[0].text, rows
