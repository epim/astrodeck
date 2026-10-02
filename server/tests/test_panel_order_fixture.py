# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The mosaic panel order's server copy graded against the table the Target
modal's copy is graded against too (#412 item 1; spec 5.2, 2.4 PANELS).

``sequence/panel_order.py`` orders the panels the run visits. The modal
numbers its PANELS rows with a TypeScript copy of two of its keys, the snake
index and least complete first (``PanelsSection.tsx``: ``snakeIndex``,
``panelRows``), because it has the progress route's counts and neither visit
times nor the site. Two copies of one rule drift the day one of them is
edited, and the modal's numbers on the sky and in the rows would then not be
the order the run uses, with every test on each side still green. So both
are graded against ``fixtures/panel_order_cases.json``: this file against
``order_panels`` and ``snake_index``, and
``ui/src/components/flows/framing/__tests__/panelOrderFixture.test.ts``
against ``panelRows`` and ``snakeIndex``. The fixture is read, never
copied, on both sides.

Every mutant below was run in a private copy of ``server/`` (scratchpad
``S5-TONIGHT-mut``, from byte backups), never in the shared tree.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from astrodeck.flows.to_plan import GROUP_ORDERS
from astrodeck.sequence.panel_order import (OrderSnapshot, order_panels,
                                            panel_label, snake_index)

FIXTURE = Path(__file__).parent / "fixtures" / "panel_order_cases.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))

#: Every key the fixture may hold, so a column added to it cannot pass here
#: by being skipped (the UI test checks the same lists).
TOP_KEYS = {"about", "snake", "cases"}
SNAKE_KEYS = {"cols", "row0", "col0", "index"}
CASE_KEYS = {"name", "rows", "cols", "order", "policy", "skip", "panels",
             "expect"}
PANEL_KEYS = {"row", "col", "banked", "total"}


@dataclass(frozen=True)
class _Panel:
    """What ``order_panels`` reads of a group member."""
    id: str
    panel_row: int
    panel_col: int


def test_the_fixture_holds_only_what_both_sides_grade():
    """The keys, and the premises the ``about`` states: every panel of a
    case's grid is either skipped or listed with a total above 0, and each
    case's ORDER is one both copies share."""
    assert set(CASES) == TOP_KEYS
    assert all(set(s) == SNAKE_KEYS for s in CASES["snake"])
    for case in CASES["cases"]:
        assert set(case) == CASE_KEYS, case["name"]
        assert all(set(p) == PANEL_KEYS and p["total"] > 0
                   for p in case["panels"]), case["name"]
        listed = {(p["row"], p["col"]) for p in case["panels"]}
        skipped = {tuple(rc) for rc in case["skip"]}
        grid = {(r, c) for r in range(1, case["rows"] + 1)
                for c in range(1, case["cols"] + 1)}
        assert listed | skipped == grid and not listed & skipped, case["name"]
        assert case["policy"] in ("least_complete", "grid"), case["name"]


@pytest.mark.parametrize(
    "s", CASES["snake"],
    ids=lambda s: f"{s['row0']},{s['col0']} of {s['cols']}")
def test_snake_index(s):
    """RED under mutant "snake reversed on odd rows" (``snake_index``'s
    ``row % 2 == 0`` made ``row % 2 == 1``, so even rows run east to west
    and odd rows west to east), observed, in 10 of the 14 cases (the three
    of a 1-column grid read the same both ways, as does the middle column
    of a grid 3 wide, 1,1 of 3), among them:

        E       assert 2 == 0
        E        +  where 2 = snake_index(0, 0, 3)
        E       assert 3 == 5
        E        +  where 3 = snake_index(1, 0, 3)
        E       assert 6 == 7
        E        +  where 6 = snake_index(3, 0, 2)
    """
    assert snake_index(s["row0"], s["col0"], s["cols"]) == s["index"]


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: c["name"])
def test_order_panels_runs_each_case(case):
    """Each case's shot panels, handed to ``order_panels`` in reverse grid
    order under the case's policy with the fraction banked as the snapshot
    (no visit times, no last visit), come out in the case's order; and
    ``to_plan`` reads the case's ORDER text as its policy.

    RED under mutant "snake reversed on odd rows", observed, in five of the
    eight cases (15 failed, 8 passed in this file with the snake cases
    above). The part-banked least-complete case, the 2x2 with a skip and
    the column of 3 stay green under it: their ties fall on panels the
    reversed snake still orders the same way. The first:

        E       AssertionError: 3x2 on night one, least complete first:
            everything ties, so the snake decides (5.2's worked example)
        E       assert ['1-3', '1-2'... '2-2', '2-3'] == ['1-1', '1-2'...
            '2-2', '2-1']
        E         At index 0 diff: '1-3' != '1-1'

    Control: a comment added to ``panel_order.py`` left all 23 green.
    """
    assert GROUP_ORDERS[case["order"]] == case["policy"], case["name"]
    members = [_Panel(f"p{p['row']}-{p['col']}", p["row"] - 1, p["col"] - 1)
               for p in reversed(case["panels"])]
    fraction = {f"p{p['row']}-{p['col']}": p["banked"] / p["total"]
                for p in case["panels"]}
    ordered = order_panels(members, cols=case["cols"], policy=case["policy"],
                           snapshot=OrderSnapshot(fraction_banked=fraction))
    got = [panel_label(m.panel_row, m.panel_col) for m in ordered]
    assert got == case["expect"], case["name"]
