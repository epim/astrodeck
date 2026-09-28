"""compile.parse_skip graded against the worked skip cases (spec 2026-09-23
flows mosaic, 3.1 and 3.2; #189 S4 item 1).

THE FIXTURE IS ``fixtures/skip_cases.json``, and it has two readers. This file
grades the server's ``parse_skip`` against it; the Target modal's mirror
(``ui/src/components/flows/framing/framingModel.ts`` ``parseSkip``) is graded
against the same file by ``framingModel.test.ts``. The modal draws a skipped
panel dashed with an X from its own reading of the text, and the run shoots
what this function reads, so the two readings must be one: a panel the modal
draws as live and the run skips is a panel the operator will wait all night
for. Neither side copies the table.

The cases were chosen by hand to pin each rule (range, dedup, grid order,
separators, the Python-wide whitespace and digit sets); the expected values
are what the rules say, and this file is what proves the server says it.

ONE THING THE FIXTURE CANNOT HOLD: an entry of more than 4300 digits makes
``int()`` raise (CPython's integer string limit), so ``parse_skip`` raises
instead of answering, and there is no answer to write down. Recorded on #328
as a third instance of "compile_plan raises on a graph the routes accept";
the mirror reads such an entry as unread, and the case belongs in the table
once the server answers it.

Each mutant below was run in a private scratch copy of ``server/``, never in
the shared tree (#254), and the failure it produced is quoted verbatim.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from astrodeck.flows.compile import parse_skip

FIXTURE = Path(__file__).parent / "fixtures" / "skip_cases.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_parse_skip_reads_every_worked_case(case):
    """MUTANT "parse_skip accepts 0-1" (compile.py: ``1 <= r <= rows`` made
    ``0 <= r <= rows``). Observed:
        E       AssertionError: row zero names no panel: '0-1' on 3x3
        E       assert ([[0, 1]], []) == ([], ['0-1'])
        FAILED tests/test_flows_skip_fixture.py::test_parse_skip_reads_every_worked_case[row zero names no panel]
        1 failed, 34 passed

    MUTANT "no dedup" (compile.py: the ``skip`` set made a list, appended
    to). Observed:
        E       AssertionError: a panel named twice is skipped once: '2-2,2-2' on 3x3
        E       assert ([[2, 2], [2, 2]], []) == ([[2, 2]], [])
        1 failed, 34 passed

    MUTANT "reverse grid order" (compile.py: ``sorted(skip)`` made
    ``sorted(skip, reverse=True)``). Observed, first of four:
        E       AssertionError: two panels, comma: '3-1, 3-2' on 3x2
        E       assert ([[3, 2], [3, 1]], []) == ([[3, 1], [3, 2]], [])
        4 failed, 31 passed

    MUTANT "ASCII strip" (compile.py: ``chunk.strip()`` made
    ``chunk.strip(" \\t\\n\\r")``). Observed, first of three:
        E       AssertionError: no-break and em spaces are stripped: '\\xa01-2\\u2003' on 3x3
        E       assert ([], ['\\xa01-2\\u2003']) == ([[1, 2]], [])
        3 failed, 32 passed

    MUTANT "ASCII digits" (compile.py: ``_SKIP_RE`` compiled with
    ``re.ASCII``, which narrows ``\\s`` too). Observed, the digit case of
    five:
        E       AssertionError: fullwidth digit three: '\\uff13-1' on 3x3
        E       assert ([], ['\\uff13-1']) == ([[3, 1]], [])
        5 failed, 30 passed
    """
    got = parse_skip(case["text"], case["rows"], case["cols"])
    want = (case["skip"], case["unread"])
    assert got == want, (f"{case['id']}: {case['text']!r} on "
                         f"{case['rows']}x{case['cols']}")


def test_the_fixture_holds_the_cases_the_mirror_is_most_likely_to_get_wrong():
    """A control on the table itself: the cases the two languages disagree
    on by default must stay in it, or a mirror written with JavaScript's own
    trim() and ``\\d`` passes a fixture that no longer asks.

    MUTANT "fixture thinned" (the three cases named below deleted from a
    scratch copy of skip_cases.json). Observed:
        E       AssertionError: skip_cases.json no longer asks: ['row zero names no panel', 'fullwidth digit three', 'Python keeps a BOM, JS trim strips it']
        FAILED tests/test_flows_skip_fixture.py::test_the_fixture_holds_the_cases_the_mirror_is_most_likely_to_get_wrong
    """
    ids = {c["id"] for c in CASES}
    assert len(ids) == len(CASES), "two cases share an id"
    must = ["row zero names no panel", "fullwidth digit three",
            "Python keeps a BOM, JS trim strips it",
            "Python strips the file separator, JS trim does not",
            "grid order, not typed order", "a panel named twice is skipped once"]
    missing = [m for m in must if m not in ids]
    assert not missing, f"skip_cases.json no longer asks: {missing}"
    # The characters those cases are about, built from code points so this
    # source file stays ASCII: fullwidth three, BOM, file separator.
    texts = {c["id"]: c["text"] for c in CASES}
    assert chr(0xFF13) in texts["fullwidth digit three"]
    assert texts["Python keeps a BOM, JS trim strips it"].startswith(chr(0xFEFF))
    assert chr(0x1C) in texts["Python strips the file separator, JS trim does not"]


def test_every_expected_skip_is_a_panel_of_its_grid_in_grid_order():
    """The table's own consistency, so a typo in an expected value cannot
    make both readers agree on a wrong answer: every expected pair is a
    panel of its grid, once, in grid order, and never also unread.

    MUTANT "a wrong expectation" (a scratch copy of skip_cases.json with the
    10x10 case expecting [[10, 11]]). Observed:
        E               AssertionError: the last panel of a 10x10: [10, 11] is not a panel of 10x10
        E               assert (10 <= 10 and 11 <= 10)
        FAILED tests/test_flows_skip_fixture.py::test_every_expected_skip_is_a_panel_of_its_grid_in_grid_order
    """
    for c in CASES:
        pairs = [tuple(p) for p in c["skip"]]
        for r, col in pairs:
            assert 1 <= r <= c["rows"] and 1 <= col <= c["cols"], (
                f"{c['id']}: {[r, col]} is not a panel of "
                f"{c['rows']}x{c['cols']}")
        assert pairs == sorted(set(pairs)), f"{c['id']}: not grid order, once"
