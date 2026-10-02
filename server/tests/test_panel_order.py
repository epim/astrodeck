# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The mosaic panel order: which panel a group visits next (mosaic spec 5.2,
5.9, 2.3; U-01, #189).

``astrodeck/sequence/panel_order.py`` is the one order function the S2
scheduler, ResumeArm and the progress route share. It is pure: the caller
passes one snapshot per pass, and the function walks no ledger and reads no
clock. What is pinned here, each piece against a named mutation of that module
(observed failures recorded per test, every one run in a private scratch copy
of ``server/``, never in the shared tree):

* the snake index is the index of ``framing.compute_mosaic``'s panel list, for
  a 3x2 and a 2x2 and a spread of other shapes, and a position outside the
  grid raises instead of colliding with another panel's index;
* with nothing banked every policy runs the snake: a 3x2 (3 columns by 2 rows)
  is ``1-1, 1-2, 1-3, 2-3, 2-2, 2-1``, a 2x2 is ``1-1, 1-2, 2-2, 2-1``;
* ``least_complete``: fraction banked ascending, then the least recently
  visited with never-visited first, then the snake;
* ``setting_first``: time to floor ascending with None (never sets tonight)
  last, then fraction banked, then the snake, and nothing else;
* ``grid``: the snake, rotated to start after the panel visited last, even
  when that panel has left the list;
* every panel passed in comes out exactly once, and the output does not depend
  on the order the caller listed the panels in;
* a non-finite number or an unknown policy raises instead of sorting a panel
  somewhere arbitrary;
* the function reads no clock, imports nothing that could walk a ledger, and
  does not touch the caller's list or snapshot.

Every input grid is listed in REVERSED row-major order (``_grid``), so no case
can pass by handing its input back: that order is neither the snake nor the
row-major order a tie-break mutant produces.

The controls are the cases where nothing should change: one panel, every panel
complete, and a seeded uneven case where the least complete panel leads.
"""
from __future__ import annotations

import ast
import random
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pytest

from astrodeck.catalog import framing
from astrodeck.sequence import panel_order
from astrodeck.sequence.panel_order import (
    POLICIES,
    OrderSnapshot,
    order_panels,
    panel_label,
    snake_index,
)

#: Any wall-clock instant will do; the visit times are built against it.
T0 = 1_790_000_000.0


@dataclass(frozen=True)
class _Panel:
    """The three attributes the order function reads from a group member. A
    ``sequence.models.Target`` carries the same three names; this stand-in has
    nothing else, so the function cannot lean on anything more."""

    id: str
    panel_row: int | None
    panel_col: int | None


def _p(label: str) -> _Panel:
    """The panel a 1-based ``row-col`` label names (spec 2.3). Parsed here, not
    through the module, so a mutant of ``panel_label`` cannot agree with
    itself."""
    r, c = (int(x) for x in label.split("-"))
    return _Panel(id=f"target-{label}", panel_row=r - 1, panel_col=c - 1)


def _grid(cols: int, rows: int) -> list[_Panel]:
    """Every panel of a ``cols`` x ``rows`` grid, in REVERSED row-major order
    (see the module docstring)."""
    return [_p(f"{r + 1}-{c + 1}")
            for r in reversed(range(rows)) for c in reversed(range(cols))]


def _labels(panels) -> list[str]:
    return [panel_label(p.panel_row, p.panel_col) for p in panels]


def _by_label(values: dict[str, object]) -> dict[str, object]:
    """A snapshot map written by label, keyed by target id as the function
    expects."""
    return {f"target-{k}": v for k, v in values.items()}


def _mosaic(cols: int, rows: int) -> list[dict]:
    """``compute_mosaic``'s panel list for a grid. The sky position and the
    field are arbitrary; only the list order is read."""
    return framing.compute_mosaic({
        "ra_hours": 10.0, "dec_deg": 20.0, "rows": rows, "cols": cols,
        "overlap": 0.25, "rotation_deg": 0.0,
        "fov_x_deg": 2.0, "fov_y_deg": 1.33,
    })["panels"]


# ---------------------------------------------------------------------------
# The snake index and the labels
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("cols", "rows"),
    [
        pytest.param(3, 2, id="3x2"),
        pytest.param(2, 2, id="2x2"),
        pytest.param(1, 1, id="1x1"),
        pytest.param(4, 1, id="4x1"),
        pytest.param(1, 4, id="1x4"),
        pytest.param(3, 3, id="3x3"),
        pytest.param(10, 10, id="10x10"),
    ],
)
def test_snake_index_is_the_index_of_compute_mosaics_panel_list(cols, rows):
    """The snake index computed from ``(panel_row, panel_col, cols)`` is the
    position ``compute_mosaic`` gives the panel. The 3x2 and the 2x2 are the
    acceptance's; the others are the edges (one panel, one row, one column)
    and the largest grid the spec model allows.

    MUTATION "snake_index row-major" (``row * cols + col``, no reversal on odd
    rows). Observed: the 1x1, 4x1 and 1x4 cases stay green, which is the
    control (a grid with no second row, or one column, has nothing to
    reverse), and the four others go red:
        [3x2]    assert [0, 1, 2, 5, 4, 3] == [0, 1, 2, 3, 4, 5]
                   At index 3 diff: 5 != 3
        [2x2]    assert [0, 1, 3, 2] == [0, 1, 2, 3]
                   At index 2 diff: 3 != 2
        [3x3]    assert [0, 1, 2, 5, 4, 3, ...] == [0, 1, 2, 3, 4, 5, ...]
                   At index 3 diff: 5 != 3
        [10x10]  assert [0, 1, 2, 3, 4, 5, ...] == [0, 1, 2, 3, 4, 5, ...]
                   At index 10 diff: 19 != 10
    """
    panels = _mosaic(cols, rows)
    assert len(panels) == cols * rows
    got = [snake_index(p["row"], p["col"], cols) for p in panels]
    assert got == list(range(cols * rows))


def test_labels_are_one_based_row_col():
    """Spec 2.3: 1-based ``row-col``, the same numbers ``to_plan`` puts in a
    panel's name (``<name> <row+1>-<col+1>``, 3.3).

    MUTATION "0-based labels" (``f"{row}-{col}"``). Observed, here:
        AssertionError: assert '0-0' == '1-1'
    and 17 order tests below go red with it, since each compares against
    literal labels, for example test_nothing_banked_2x2_runs_in_snake_order:
        AssertionError: assert ['0-0', '0-1', '1-1', '1-0']
        == ['1-1', '1-2', '2-2', '2-1']
    """
    assert panel_label(0, 0) == "1-1"
    assert panel_label(1, 2) == "2-3"
    assert panel_label(9, 0) == "10-1"


@pytest.mark.parametrize(
    ("row", "col", "cols"),
    [
        pytest.param(0, 2, 2, id="col == cols"),
        pytest.param(1, 5, 3, id="col beyond cols"),
        pytest.param(0, -1, 3, id="negative col"),
        pytest.param(-1, 0, 3, id="negative row"),
        pytest.param(None, 0, 3, id="no row"),
        pytest.param(0, None, 3, id="no col"),
        pytest.param(0, 0, 0, id="no columns"),
    ],
)
def test_a_position_outside_the_grid_raises(row, col, cols):
    """A position outside the grid has no snake index. Computed anyway it
    would collide with a real panel's (``(0, 2)`` in a 2-wide grid is index
    2, which is also ``(1, 1)``), and two panels would tie on the one key that
    is meant to be unique. That is a caller bug, a member without its grid
    position or the wrong ``cols``, so it raises.

    MUTATION "no range check" (the ``row < 0 or not 0 <= col < cols`` raise
    removed). Observed: the None and zero-column cases still pass, on their
    own guards, and the four range cases go red, each with:
        Failed: DID NOT RAISE <class 'ValueError'>
    """
    with pytest.raises(ValueError):
        snake_index(row, col, cols)


def test_a_member_or_a_last_visit_outside_the_grid_raises_from_the_order():
    """The same guard reached through ``order_panels``: a member whose column
    is past ``cols``, a member with no grid position, and a last-visited
    position outside the grid under ``grid``.

    MUTATION "no range check" (as above). Observed:
        Failed: DID NOT RAISE <class 'ValueError'>
    """
    with pytest.raises(ValueError, match="outside"):
        order_panels([_p("1-1"), _p("1-3")], cols=2)
    with pytest.raises(ValueError):
        order_panels([_Panel("target-x", None, None)], cols=2)
    with pytest.raises(ValueError, match="outside"):
        order_panels(_grid(2, 2), cols=2, policy="grid",
                     snapshot=OrderSnapshot(last_visited=(0, 2)))


# ---------------------------------------------------------------------------
# Nothing banked: the snake
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("policy", POLICIES)
def test_nothing_banked_3x2_runs_in_snake_order(policy):
    """Spec 5.2's worked example: a 3x2 on night one with nothing banked,
    nothing visited and no time to floor known. Every key ties, so the snake
    decides, and the order is also ``compute_mosaic``'s own list.

    MUTATION "ties by row-major, not snake" (the order's tie-break key is
    ``panel_row * cols + panel_col``). Observed, all three policies red, the
    3x2 putting 2-1 before 2-3:
        AssertionError: assert ['1-1', '1-2'... '2-2', '2-3']
        == ['1-1', '1-2'... '2-2', '2-1']
          At index 3 diff: '2-1' != '2-3'

    MUTATION "snake_index row-major" (see above) gives the same three.

    MUTATION "no snake tie-break" (the snake dropped from the
    ``least_complete`` key, so ties keep the caller's order). Observed, the
    least_complete case red with the reversed row-major input handed back:
        AssertionError: assert ['2-3', '2-2'... '1-2', '1-1']
        == ['1-1', '1-2'... '2-2', '2-1']
          At index 0 diff: '2-3' != '1-1'

    MUTATION "drop a panel with no time-to-floor". Observed, the
    setting_first case red, every panel lost:
        AssertionError: assert [] == ['1-1', '1-2'... '2-2', '2-1']
          Right contains 6 more items, first extra item: '1-1'
    """
    got = _labels(order_panels(_grid(3, 2), cols=3, policy=policy))
    assert got == ["1-1", "1-2", "1-3", "2-3", "2-2", "2-1"]
    assert got == [panel_label(p["row"], p["col"]) for p in _mosaic(3, 2)]


@pytest.mark.parametrize("policy", POLICIES)
def test_nothing_banked_2x2_runs_in_snake_order(policy):
    """The 2x2 of S2's rotation test: ``1-1, 1-2, 2-2, 2-1``.

    MUTATION "ties by row-major, not snake". Observed, all three red with:
        AssertionError: assert ['1-1', '1-2', '2-1', '2-2']
        == ['1-1', '1-2', '2-2', '2-1']
          At index 2 diff: '2-1' != '2-2'
    """
    got = _labels(order_panels(_grid(2, 2), cols=2, policy=policy))
    assert got == ["1-1", "1-2", "2-2", "2-1"]
    assert got == [panel_label(p["row"], p["col"]) for p in _mosaic(2, 2)]


# ---------------------------------------------------------------------------
# least_complete
# ---------------------------------------------------------------------------

def test_least_complete_orders_by_fraction_banked_ascending():
    """The least complete panel goes first. The fractions are chosen so that
    ascending, descending and the snake are three different orders. One panel
    (2-3) is missing from the map, which is the snapshot of a panel with no
    frames in the ledger: nothing banked.

    MUTATION "fraction descending" (``-fraction`` in the key). Observed:
        AssertionError: assert ['2-2', '2-1'... '1-2', '2-3']
        == ['2-3', '1-2'... '2-1', '2-2']
          At index 0 diff: '2-2' != '2-3'

    MUTATION "missing fraction counts as complete" (a panel missing from the
    map defaults to 1.0). Observed:
        AssertionError: assert ['1-2', '1-3'... '2-3', '2-2']
        == ['2-3', '1-2'... '2-1', '2-2']
          At index 0 diff: '1-2' != '2-3'
    """
    snap = OrderSnapshot(fraction_banked=_by_label({
        "1-1": 0.6, "1-2": 0.2, "1-3": 0.4, "2-2": 1.0, "2-1": 0.8,
    }))
    got = _labels(order_panels(_grid(3, 2), cols=3, snapshot=snap))
    assert got == ["2-3", "1-2", "1-3", "1-1", "2-1", "2-2"]


def test_least_complete_puts_fraction_before_visit_time():
    """Fraction banked is the primary key; the visit time only breaks ties.
    Here the less complete panel was visited MORE recently, and still leads.

    MUTATION "visit time before fraction" (the key reads the visit, then the
    fraction). Observed:
        AssertionError: assert ['1-2', '1-1'] == ['1-1', '1-2']
          At index 0 diff: '1-2' != '1-1'

    MUTATION "fraction descending". Observed, the same line.
    """
    snap = OrderSnapshot(
        fraction_banked=_by_label({"1-1": 0.1, "1-2": 0.5}),
        last_visit_ts=_by_label({"1-1": T0 + 300, "1-2": T0 + 100}),
    )
    got = _labels(order_panels([_p("1-2"), _p("1-1")], cols=2, snapshot=snap))
    assert got == ["1-1", "1-2"]


def test_least_complete_ties_go_to_never_visited_then_least_recent_then_snake():
    """Every fraction ties. Never-visited panels come first, then the oldest
    visit, then the snake among panels that still tie.

    The 2x2 is laid out so that the three tie-break mutants each give a
    different wrong order: the visit times run AGAINST the snake (1-1 newest,
    2-2 oldest), and the one never-visited panel (2-1, missing from the map)
    is last in the snake. The 3x2 has four never-visited panels, which tie on
    everything but the snake.

    MUTATION "never-visited not preferred" (never-visited sorts after every
    visited panel). Observed:
        AssertionError: assert ['2-2', '1-2', '1-1', '2-1']
        == ['2-1', '2-2', '1-2', '1-1']
          At index 0 diff: '2-2' != '2-1'

    MUTATION "most recently visited first" (``-visited`` in the key).
    Observed:
        AssertionError: assert ['2-1', '1-1', '1-2', '2-2']
        == ['2-1', '2-2', '1-2', '1-1']
          At index 1 diff: '1-1' != '2-2'

    MUTATION "ties by row-major, not snake". Observed, the 2x2 passing (no
    two of its panels tie there) and the 3x2 red:
        AssertionError: assert ['1-3', '2-1'... '1-2', '1-1']
        == ['1-3', '2-3'... '1-2', '1-1']
          At index 1 diff: '2-1' != '2-3'

    MUTATION "no snake tie-break". Observed, the 3x2 red:
        AssertionError: assert ['2-3', '2-2'... '1-2', '1-1']
        == ['1-3', '2-3'... '1-2', '1-1']
          At index 0 diff: '2-3' != '1-3'
    """
    snap = OrderSnapshot(
        fraction_banked=_by_label({k: 0.25 for k in ("1-1", "1-2", "2-2", "2-1")}),
        last_visit_ts=_by_label({"1-1": T0 + 300, "1-2": T0 + 200,
                                 "2-2": T0 + 100}),
    )
    got = _labels(order_panels(_grid(2, 2), cols=2, snapshot=snap))
    assert got == ["2-1", "2-2", "1-2", "1-1"]

    snap = OrderSnapshot(
        last_visit_ts=_by_label({"1-1": T0 + 100, "1-2": T0 + 50,
                                 "2-3": None}),
    )
    got = _labels(order_panels(_grid(3, 2), cols=3, snapshot=snap))
    assert got == ["1-3", "2-3", "2-2", "2-1", "1-2", "1-1"]


# ---------------------------------------------------------------------------
# setting_first
# ---------------------------------------------------------------------------

def test_setting_first_orders_by_time_to_floor_with_none_last():
    """The panel that sets soonest goes first; a panel that never sets
    tonight goes last, but it goes. 1-3 carries an explicit None and 2-2 is
    missing from the map, the two spellings of "does not set tonight".

    MUTATION "drop a panel with no time-to-floor" (``setting_first`` filters
    out the panels whose time to floor is None). Observed:
        AssertionError: assert ['2-3', '1-2', '2-1', '1-1']
        == ['2-3', '1-2'... '1-3', '2-2']
          Right contains 2 more items, first extra item: '1-3'

    MUTATION "None first" (a panel that never sets sorts before the timed
    ones). Observed:
        AssertionError: assert ['1-3', '2-2'... '2-1', '1-1']
        == ['2-3', '1-2'... '1-3', '2-2']
          At index 0 diff: '1-3' != '2-3'

    MUTATION "time to floor descending" (``-floor`` in the key). Observed:
        AssertionError: assert ['1-1', '2-1'... '1-3', '2-2']
        == ['2-3', '1-2'... '1-3', '2-2']
          At index 0 diff: '1-1' != '2-3'
    """
    snap = OrderSnapshot(time_to_floor_s=_by_label({
        "1-1": 7200.0, "1-2": 3600.0, "1-3": None, "2-3": 1800.0,
        "2-1": 5400.0,
    }))
    got = _labels(order_panels(_grid(3, 2), cols=3, policy="setting_first",
                               snapshot=snap))
    assert got == ["2-3", "1-2", "2-1", "1-1", "1-3", "2-2"]


def test_setting_first_ties_go_to_fraction_then_snake_and_not_to_visit_time():
    """Among panels that set at the same moment, and among the panels that do
    not set tonight, the less complete goes first, then the snake. The visit
    times in the snapshot would reorder the timed panels if the policy read
    them (2-3 visited before 1-2); the spec's tie-breaks for this policy are
    fraction and snake only.

    MUTATION "setting_first ties skip fraction" (straight to the snake).
    Observed:
        AssertionError: assert ['1-1', '1-2'... '2-2', '2-1']
        == ['1-2', '2-3'... '1-3', '2-1']
          At index 0 diff: '1-1' != '1-2'

    MUTATION "setting_first ties use visit time" (the least recent visit,
    never-visited first, between the fraction and the snake). Observed:
        AssertionError: assert ['2-3', '1-2'... '1-3', '2-1']
        == ['1-2', '2-3'... '1-3', '2-1']
          At index 0 diff: '2-3' != '1-2'

    MUTATION "None first". Observed:
        AssertionError: assert ['2-2', '1-3'... '2-3', '1-1']
        == ['1-2', '2-3'... '1-3', '2-1']
          At index 0 diff: '2-2' != '1-2'
    """
    snap = OrderSnapshot(
        time_to_floor_s=_by_label({"1-1": 3600.0, "1-2": 3600.0,
                                   "2-3": 3600.0}),
        fraction_banked=_by_label({"1-1": 0.5, "1-2": 0.25, "1-3": 0.5,
                                   "2-3": 0.25, "2-2": 0.25, "2-1": 0.5}),
        last_visit_ts=_by_label({"2-3": T0 + 100, "1-2": T0 + 200}),
    )
    got = _labels(order_panels(_grid(3, 2), cols=3, policy="setting_first",
                               snapshot=snap))
    assert got == ["1-2", "2-3", "1-1", "2-2", "1-3", "2-1"]


# ---------------------------------------------------------------------------
# grid
# ---------------------------------------------------------------------------

def test_grid_is_the_snake_rotated_to_start_after_the_last_visited_panel():
    """``grid`` walks the snake from the panel after the one visited last,
    and wraps. The fractions, visit times and times to floor in the snapshot
    would reorder the panels under either other policy; ``grid`` reads none
    of them (5.2 gives it no tie-break). Each runs AGAINST the snake inside
    the first stretch of the walk (2-2 then 2-1): 2-1 is less complete, sets
    sooner and was visited longer ago, so a key that read any of the three
    ascending, however it sorted a None, would put 2-1 ahead of 2-2.

    MUTATION "grid not rotated" (the last-visited panel ignored). Observed:
        AssertionError: assert ['1-1', '1-2', '2-2', '2-1']
        == ['2-2', '2-1', '1-1', '1-2']
          At index 0 diff: '1-1' != '2-2'

    MUTATIONS "grid reads X": the rotation, then X, then the snake.
    Observed for "grid reads fraction" and for "grid reads time to floor"
    with None last (as ``setting_first`` sorts it):
        AssertionError: assert ['2-1', '2-2', '1-2', '1-1']
        == ['2-2', '2-1', '1-1', '1-2']
          At index 0 diff: '2-1' != '2-2'
    and for "grid reads time to floor" with None as 0, and "grid reads visit
    time" with never-visited first (as ``least_complete`` sorts it), last,
    or as 0:
        AssertionError: assert ['2-1', '2-2', '1-1', '1-2']
        == ['2-2', '2-1', '1-1', '1-2']
          At index 0 diff: '2-1' != '2-2'
    The verifier added the visit times and moved the times to floor (1-1 had
    60 s, 2-2 99999 s, 2-1 none) after "grid reads time to floor" with None
    last and every "grid reads visit time" passed here.
    """
    snap = OrderSnapshot(
        last_visited=(0, 1),     # 1-2
        fraction_banked=_by_label({"1-1": 0.9, "1-2": 0.0, "2-2": 0.5,
                                   "2-1": 0.1}),
        time_to_floor_s=_by_label({"2-1": 60.0, "2-2": 99999.0,
                                   "1-2": 30.0}),
        # 1-2 is the panel visited last, so its visit is the newest.
        last_visit_ts=_by_label({"1-1": T0 + 100, "2-1": T0 + 200,
                                 "2-2": T0 + 300, "1-2": T0 + 400}),
    )
    got = _labels(order_panels(_grid(2, 2), cols=2, policy="grid",
                               snapshot=snap))
    assert got == ["2-2", "2-1", "1-1", "1-2"]

    snap = OrderSnapshot(last_visited=(0, 2))     # 1-3
    got = _labels(order_panels(_grid(3, 2), cols=3, policy="grid",
                               snapshot=snap))
    assert got == ["2-3", "2-2", "2-1", "1-1", "1-2", "1-3"]


def test_grid_rotates_after_a_last_visited_panel_that_has_left_the_list():
    """The panel visited last may have completed on that visit, so it is no
    longer among the panels to order. The rotation still starts after its
    place in the snake.

    MUTATION "grid not rotated". Observed:
        AssertionError: assert ['1-1', '2-2', '2-1'] == ['2-2', '2-1', '1-1']
          At index 0 diff: '1-1' != '2-2'
    """
    live = [_p("2-1"), _p("2-2"), _p("1-1")]      # 1-2 completed
    snap = OrderSnapshot(last_visited=(0, 1))     # 1-2
    got = _labels(order_panels(live, cols=2, policy="grid", snapshot=snap))
    assert got == ["2-2", "2-1", "1-1"]


def test_grid_after_the_last_panel_of_the_snake_wraps_to_the_first():
    """Control: the last-visited panel is the snake's final one (2-1 in a
    2x2), so the rotation wraps to a plain snake. This case passes under
    "grid not rotated", on purpose (observed); the two tests above are the
    ones that tell that mutant apart. It does go red under "snake_index
    row-major", which misplaces the last visit itself:
        AssertionError: assert ['2-2', '1-1', '1-2', '2-1']
        == ['1-1', '1-2', '2-2', '2-1']
          At index 0 diff: '2-2' != '1-1'
    """
    snap = OrderSnapshot(last_visited=(1, 0))     # 2-1
    got = _labels(order_panels(_grid(2, 2), cols=2, policy="grid",
                               snapshot=snap))
    assert got == ["1-1", "1-2", "2-2", "2-1"]


# ---------------------------------------------------------------------------
# A policy only reorders
# ---------------------------------------------------------------------------

def test_every_panel_comes_out_exactly_once_whatever_the_order_asked():
    """300 seeded snapshots per policy over grids from 1x1 to 5x5, each with
    a random subset of its panels live (skipped or complete panels are not
    passed), ties drawn on purpose, some keys missing and some None, and a
    random last-visited position that may not be live. Every panel passed in
    comes out exactly once, as the same object, and the output does not
    depend on the order the panels were listed in (the final tie-break, the
    snake index, is unique per panel).

    MUTATION "drop a panel with no time-to-floor". Observed (the Counter keys
    are object ids, different on every run):
        AssertionError: setting_first 4x2 lost or duplicated a panel
          Right contains 2 more items:

    MUTATION "no snake tie-break". Observed:
        AssertionError: least_complete 4x5 depends on the input order
          At index 2 diff: 'target-3-3' != 'target-5-3'

    MUTATION "reads the clock" (see the purity tests). This test catches it
    only INTERMITTENTLY: red in 3 of 7 runs, green in the rest, because two
    reads of the clock often return the same value and then the snake breaks
    the tie as it should. Observed when red, the clock ordering never-visited
    panels by when it was read:
        AssertionError: least_complete 3x5 depends on the input order
          At index 0 diff: 'target-2-3' != 'target-2-2'
    The two purity tests below are the deterministic guard: red in all 7.
    """
    rng = random.Random(20260925)
    for policy in POLICIES:
        for _ in range(300):
            cols, rows = rng.randint(1, 5), rng.randint(1, 5)
            every = _grid(cols, rows)
            live = rng.sample(every, rng.randint(1, len(every)))
            fraction, visited, floor = {}, {}, {}
            for p in live:
                if rng.random() < 0.8:
                    fraction[p.id] = rng.choice([0.0, 1 / 7, 2 / 7, 0.5, 1.0])
                if rng.random() < 0.8:
                    visited[p.id] = rng.choice([None, T0 + 100, T0 + 200])
                if rng.random() < 0.8:
                    floor[p.id] = rng.choice([None, 600.0, 1800.0, 3600.0])
            last = None
            if rng.random() < 0.7:
                last = (rng.randrange(rows), rng.randrange(cols))
            snap = OrderSnapshot(fraction_banked=fraction, last_visit_ts=visited,
                                 time_to_floor_s=floor, last_visited=last)

            got = order_panels(live, cols=cols, policy=policy, snapshot=snap)
            shape = f"{policy} {cols}x{rows}"
            assert Counter(map(id, got)) == Counter(map(id, live)), (
                f"{shape} lost or duplicated a panel")

            shuffled = list(live)
            rng.shuffle(shuffled)
            again = order_panels(shuffled, cols=cols, policy=policy,
                                 snapshot=snap)
            assert [p.id for p in again] == [p.id for p in got], (
                f"{shape} depends on the input order")


# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("policy", POLICIES)
def test_control_one_panel_comes_out_alone(policy):
    """Control: a 1x1 block, or a mosaic with one live panel left. Whatever
    the snapshot says about it, it is the order. Every mutant above leaves
    it passing except the one that drops panels.

    MUTATION "drop a panel with no time-to-floor". Observed, the
    setting_first case red (the one panel never sets tonight):
        assert (0 == 1)
    """
    only = _p("1-1")
    snap = OrderSnapshot(fraction_banked={only.id: 1.0},
                         last_visit_ts={only.id: T0},
                         time_to_floor_s={only.id: None},
                         last_visited=(0, 0))
    got = order_panels([only], cols=1, policy=policy, snapshot=snap)
    assert len(got) == 1 and got[0] is only


@pytest.mark.parametrize("policy", POLICIES)
def test_control_every_panel_complete_keeps_every_panel(policy):
    """Control: every panel complete, nothing visited, nothing setting. The
    function removes nothing (the scheduler drops complete panels before it
    asks), and with every key tied the snake stands.

    MUTATION "drop a panel with no time-to-floor". Observed, the
    setting_first case red:
        AssertionError: assert [] == ['1-1', '1-2'... '2-2', '2-1']
          Right contains 6 more items, first extra item: '1-1'
    """
    snap = OrderSnapshot(fraction_banked={p.id: 1.0 for p in _grid(3, 2)})
    got = _labels(order_panels(_grid(3, 2), cols=3, policy=policy,
                               snapshot=snap))
    assert got == ["1-1", "1-2", "1-3", "2-3", "2-2", "2-1"]


def test_control_seeded_uneven_progress_the_least_complete_panel_leads():
    """Control: a 3x3 part way through a campaign, banked counts drawn from a
    seeded generator out of 21 owed frames per panel, no visit times. The
    least complete panel leads, and the whole order is the one an
    independent oracle gives: sort by fraction, then by the panel's index in
    ``compute_mosaic``'s own list (not the module's ``snake_index``).

    MUTATION "fraction descending". Observed, the most complete panel
    leading:
        AssertionError: assert '2-3' == '1-2'
    """
    rng = random.Random(189)
    every = _grid(3, 3)
    fraction = {p.id: rng.randint(0, 21) / 21 for p in every}
    snap = OrderSnapshot(fraction_banked=fraction)
    got = order_panels(every, cols=3, snapshot=snap)

    mosaic_index = {(m["row"], m["col"]): i for i, m in enumerate(_mosaic(3, 3))}
    oracle = sorted(every, key=lambda p: (fraction[p.id],
                                          mosaic_index[(p.panel_row,
                                                        p.panel_col)]))
    least = min(fraction.values())
    assert sum(1 for v in fraction.values() if v == least) == 1, (
        "the seed must give one least complete panel for this control to "
        "say anything")
    assert _labels(got)[0] == _labels(oracle)[0]
    assert fraction[got[0].id] == least
    assert _labels(got) == _labels(oracle)


# ---------------------------------------------------------------------------
# Bad input raises
# ---------------------------------------------------------------------------

def test_an_unknown_policy_raises():
    """``TargetGroup.order`` is a Literal, so an unknown policy here is a
    caller bug. Sorting it as some default would hide the bug and quietly run
    a policy nobody chose (5.2 names ``catch_up`` as rejected, so it is the
    name used).

    MUTATION "unknown policy falls back to grid" (no membership check, and
    the final branch serves anything). Observed:
        Failed: DID NOT RAISE <class 'ValueError'>
    """
    with pytest.raises(ValueError, match="catch_up"):
        order_panels(_grid(2, 2), cols=2, policy="catch_up")


@pytest.mark.parametrize(
    "snap",
    [
        pytest.param(OrderSnapshot(fraction_banked={"target-1-2": float("nan")}),
                     id="NaN fraction"),
        pytest.param(OrderSnapshot(fraction_banked={"target-1-2": None}),
                     id="None fraction"),
        pytest.param(OrderSnapshot(last_visit_ts={"target-2-1": float("inf")}),
                     id="infinite visit time"),
        pytest.param(OrderSnapshot(time_to_floor_s={"target-2-2": float("nan")}),
                     id="NaN time to floor"),
    ],
)
@pytest.mark.parametrize("policy", POLICIES)
def test_a_non_finite_number_in_the_snapshot_raises(snap, policy):
    """A NaN compares false with everything, so a sort puts that panel
    anywhere and says nothing: the order is wrong without looking wrong. A
    non-finite number, or a fraction of None, is a caller bug and raises, under
    every policy, because the snapshot is one object for the pass whichever
    policy reads it.

    MUTATION "no finiteness check" (the ``math.isfinite`` raise removed).
    Observed: the three None-fraction cases still pass, on their own guard,
    and all nine NaN and infinite cases go red, each with:
        Failed: DID NOT RAISE <class 'ValueError'>
    """
    with pytest.raises(ValueError):
        order_panels(_grid(2, 2), cols=2, policy=policy, snapshot=snap)


# ---------------------------------------------------------------------------
# Purity
# ---------------------------------------------------------------------------

def test_the_order_reads_no_clock_and_leaves_its_inputs_alone(monkeypatch):
    """The acceptance's purity: no clock, and the caller's list and snapshot
    are exactly as they were. Every clock the standard library offers is made
    to raise for the duration of the calls.

    MUTATION "reads the clock" (a never-visited panel's visit time taken as
    ``time.time()``). Observed:
        AssertionError: panel_order read the clock

    MUTATION "sorts in place" (``panels.sort(key=...)``, returning the
    caller's list). Observed, the caller's list left in the last call's
    order (``grid`` after 1-1):
        AssertionError: assert ['1-2', '1-3'... '2-1', '1-1']
        == ['2-3', '2-2'... '1-2', '1-1']
          At index 0 diff: '1-2' != '2-3'
    """
    def boom(*_a, **_k):
        raise AssertionError("panel_order read the clock")

    panels = _grid(3, 2)
    before = list(panels)
    fraction = _by_label({"1-2": 0.5})
    visited = _by_label({"1-1": T0})
    floor = _by_label({"2-1": 60.0, "1-3": None})
    snap = OrderSnapshot(fraction_banked=fraction, last_visit_ts=visited,
                         time_to_floor_s=floor, last_visited=(0, 0))
    copies = (dict(fraction), dict(visited), dict(floor))

    with monkeypatch.context() as m:
        for name in ("time", "monotonic", "perf_counter", "time_ns",
                     "monotonic_ns", "perf_counter_ns"):
            m.setattr(time, name, boom)
        results = [order_panels(panels, cols=3, policy=policy, snapshot=snap)
                   for policy in POLICIES]

    assert _labels(panels) == _labels(before)
    assert all(a is b for a, b in zip(panels, before))
    assert (fraction, visited, floor) == copies
    assert all(r is not panels for r in results)


def test_the_module_imports_nothing_that_could_walk_a_ledger_or_read_a_clock():
    """The static half of the purity promise: ``panel_order`` imports only the
    standard library's pure modules. A ledger (``session``), the engine, the
    config or a clock (``time``, ``datetime``) would each be a new import.

    MUTATION "reads the clock" (as above, which adds ``import time``).
    Observed:
        AssertionError: panel_order imports {'time'}
    """
    tree = ast.parse(Path(panel_order.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add("." * node.level + (node.module or ""))
    allowed = {"__future__", "math", "collections.abc", "dataclasses",
               "typing"}
    assert imported <= allowed, f"panel_order imports {imported - allowed}"
