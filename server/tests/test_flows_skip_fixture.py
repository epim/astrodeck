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

THE DIGIT RUNS (#441). An entry of more than 4300 digits makes ``int()``
raise (CPython's integer string limit, leading zeros counted), and until S7
``parse_skip`` raised with it instead of answering, so the table could not
hold the case. It answers now: such an entry is unread, as the mirror
already read it, so the table holds #441's 5000-digit entry, 5000 zeros and
a 1 (row 1 to arithmetic, a refusal to ``int()``), and a run of exactly 4300
digits as the control that is read. ``test_s7_compile_digit_runs.py`` holds
the guard itself.

Each mutant below was run in a private scratch copy of ``server/``, never in
the shared tree (#254), and the failure it produced is quoted verbatim.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from astrodeck.flows.compile import parse_skip

FIXTURE = Path(__file__).parent / "fixtures" / "skip_cases.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]

#: The #441 cases by id: two runs ``int()`` refuses, and the control at the
#: limit that it reads.
DIGITS_PAST = "a 5000-digit column int() refuses names no panel (#441)"
ZEROS_PAST = "5000 zeros and a 1: int() counts the zeros"
DIGITS_AT = "exactly 4300 digits is read (the limit, a control)"


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

    MUTANT "guard removed" (compile.py: ``parse_skip``'s ``int()`` pair
    unguarded again, as #441 found it; S7-COMPILE). Observed on the two
    digit-run cases past the limit, and on nothing else:

        E           ValueError: Exceeds the limit (4300 digits) for integer string conversion: value has 5000 digits; use sys.set_int_max_str_digits() to increase the limit
        E           ValueError: Exceeds the limit (4300 digits) for integer string conversion: value has 5001 digits; use sys.set_int_max_str_digits() to increase the limit
        FAILED tests/test_flows_skip_fixture.py::test_parse_skip_reads_every_worked_case[a 5000-digit column int() refuses names no panel (#441)]
        FAILED tests/test_flows_skip_fixture.py::test_parse_skip_reads_every_worked_case[5000 zeros and a 1: int() counts the zeros]
        2 failed, 36 passed
    """
    got = parse_skip(case["text"], case["rows"], case["cols"])
    want = (case["skip"], case["unread"])
    assert got == want, (f"{case['id']}: {_shown(case['text'])} on "
                         f"{case['rows']}x{case['cols']}")


def _shown(text: str) -> str:
    """A case's text for a failure message: whole, unless it is one of the
    digit runs, which would bury the message under 5000 digits."""
    if len(text) <= 80:
        return repr(text)
    return f"{text[:24]!r}... ({len(text)} characters)"


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
            "grid order, not typed order", "a panel named twice is skipped once",
            DIGITS_PAST, ZEROS_PAST, DIGITS_AT]
    missing = [m for m in must if m not in ids]
    assert not missing, f"skip_cases.json no longer asks: {missing}"
    # The characters those cases are about, built from code points so this
    # source file stays ASCII: fullwidth three, BOM, file separator.
    texts = {c["id"]: c["text"] for c in CASES}
    assert chr(0xFF13) in texts["fullwidth digit three"]
    assert texts["Python keeps a BOM, JS trim strips it"].startswith(chr(0xFEFF))
    assert chr(0x1C) in texts["Python strips the file separator, JS trim does not"]
    # And the runs #441 is about straddle CPython's limit, leading zeros
    # counted, so a fixture whose runs were shortened asks nothing.
    runs = {cid: [len(side) for side in texts[cid].split("-")]
            for cid in (DIGITS_PAST, ZEROS_PAST, DIGITS_AT)}
    limit = sys.get_int_max_str_digits()
    assert max(runs[DIGITS_PAST]) > limit and max(runs[ZEROS_PAST]) > limit, (
        f"the #441 runs no longer pass the {limit}-digit limit: {runs}")
    assert max(runs[DIGITS_AT]) == limit, runs


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
