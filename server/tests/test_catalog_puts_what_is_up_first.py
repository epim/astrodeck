"""The first rows of a target search should be targets you can actually shoot.

Seen on the rig 2026-08-19 from 37N: opening MOUNT and searching shows, in
order, the Large Magellanic Cloud at -17 deg, the Carina Nebula at -29, NGC 292
at -28, NGC 6231 at -85. They are the brightest things in the catalogue and
permanently invisible from this latitude. The operator scans a list whose most
prominent entries can never be observed, while the things that ARE up are
further down.

Ordering by magnitude is right for "which of these is impressive". It is wrong
for "which of these can I point at tonight", which is the question a GOTO list
answers.
"""
from __future__ import annotations

from astrodeck.catalog import order_by_observability


def _row(name, alt, mag):
    return {"name": name, "alt": alt, "mag": mag}


def test_targets_below_the_horizon_sink():
    rows = [_row("LMC", -17.0, 0.3), _row("Carina", -29.0, 1.0),
            _row("Pleiades", 69.0, 1.6), _row("NGC 6231", -85.0, 2.6),
            _row("Alnilam", 51.0, 1.7)]
    out = order_by_observability(rows)
    assert [r["name"] for r in out[:2]] == ["Pleiades", "Alnilam"], (
        [r["name"] for r in out])
    assert [r["name"] for r in out[2:]] == ["LMC", "Carina", "NGC 6231"]


def test_the_SEARCH_ORDER_is_preserved_within_each_group():
    """This reorders by observability and nothing else.

    The incoming order is RELEVANCE — an exact name match outranks a bright
    object that merely matched the type — and relevance is not magnitude. The
    rows below are deliberately NOT in magnitude order, because a first version
    of this test used rows that happened to be, and a sabotage that re-sorted by
    magnitude instead of partitioning passed it unchanged.
    """
    rows = [_row("exact match, dim", 40.0, 9.0),
            _row("fuzzy match, bright", 30.0, 1.0),
            _row("exact match, dim, down", -5.0, 8.0),
            _row("fuzzy match, bright, down", -50.0, 0.5)]
    out = [r["name"] for r in order_by_observability(rows)]
    assert out == ["exact match, dim", "fuzzy match, bright",
                   "exact match, dim, down", "fuzzy match, bright, down"], out


def test_rows_with_no_altitude_are_left_exactly_where_they_are():
    """A viewer without `view.site_derived` gets no alt at all. Inventing an
    order for them would be inventing a horizon they are not allowed to know."""
    rows = [{"name": "a", "mag": 1.0}, {"name": "b", "mag": 2.0}]
    assert [r["name"] for r in order_by_observability(rows)] == ["a", "b"]


def test_a_mixed_list_only_moves_the_ones_it_can_judge():
    rows = [_row("down", -10.0, 1.0), {"name": "unknown", "mag": 2.0},
            _row("up", 45.0, 3.0)]
    out = [r["name"] for r in order_by_observability(rows)]
    assert out.index("up") < out.index("down"), out


def test_the_horizon_is_zero_not_a_comfort_margin():
    """A target at +2 deg is up. It may be through the neighbour's roof, but that
    is the horizon mask's judgement to make, not this function's."""
    rows = [_row("low but up", 2.0, 5.0), _row("just under", -0.5, 1.0)]
    assert [r["name"] for r in order_by_observability(rows)] == ["low but up", "just under"]
