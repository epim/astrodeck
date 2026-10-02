# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A digit run past CPython's 4300-digit limit reads as nothing, never as a
ValueError (#441; the #328 / #362 class; spec 2026-09-23 flows mosaic, 3.2).

``int()`` of a run of more than ``sys.get_int_max_str_digits()`` decimal
digits (4300 by default) raises ``ValueError``, and two readers on the
compile path called it on a ``\\d+`` match with no guard:

* ``nodes.parse_cycle_plan``, a FILTER CYCLE slot's exposure, which
  ``compile_plan`` reads for every FILTER CYCLE and ``doctor.check`` reads
  through ``_longest_sub_s`` and ``_pass_seconds``;
* ``compile.parse_skip``, a TARGET's ``skip`` entry, which ``compile_plan``
  reads for every multi-panel block and Tonight reads for its sentences.

``FlowGraph.validation_errors()`` accepts both graphs, so a save stored the
flow, and every compile of it (the editor's, ``/run``'s, Tonight's) was a
500 from then on. Now each reader says what it says of any entry it cannot
read: ``parse_cycle_plan`` drops the slot (its documented policy,
"UNPARSEABLE ENTRIES ARE DROPPED"), and ``parse_skip`` lists the entry as
unread, as it lists a row past the last. The regex is not narrowed:
"01-002" is panel 1-2 (``skip_cases.json`` pins it), and CPython counts a
leading zero as a digit, so 5000 zeros and a 1 is refused by ``int()``
although its value is 1.

Where else this is held: ``skip_cases.json`` carries the 5000-digit entries,
graded by ``test_flows_skip_fixture.py`` and by the modal's mirror in
``framingModel.test.ts``; ``test_flows_compile_never_raises.py`` compiles
both #441 graphs and asks the doctor; ``test_compile_route_never_500.py``
sends them through the routes.

Each mutant below was run in a private scratch copy of ``server/``
(``S7-COMPILE-mut`` under the session scratchpad), never in the shared tree
(#254), and the failure it produced is quoted verbatim.
"""
from __future__ import annotations

import sys

import pytest

from astrodeck.flows.compile import parse_skip
from astrodeck.flows.nodes import parse_cycle_plan

#: CPython's limit, read rather than assumed, so the runs below straddle it
#: wherever the suite runs.
LIMIT = sys.get_int_max_str_digits()

PAST = "9" * 5000
AT = "9" * LIMIT


def test_the_premise_int_refuses_a_run_past_the_limit():
    """THE PREMISE: the runs below are on the two sides of the limit, and
    ``int()`` refuses the long one, leading zeros counted. Were the limit
    lifted (``sys.set_int_max_str_digits(0)``), the guard would have
    nothing to catch and the cases would pass for no reason."""
    assert LIMIT == 4300
    assert int(AT) > 0
    for run in (PAST, "0" * 5000 + "1"):
        with pytest.raises(ValueError, match="4300 digits"):
            int(run)


class TestParseCyclePlan:
    def test_a_slot_whose_exposure_int_refuses_is_dropped(self):
        """The slot past the limit is dropped and the slots round it are
        kept, in wheel order.

        MUTANT "guard removed" (``parse_cycle_plan``'s ``int()`` unguarded,
        as #441 found it). Observed:

            >               out.append((m.group(1), int(m.group(2))))
            E               ValueError: Exceeds the limit (4300 digits) for integer string conversion: value has 5000 digits; use sys.set_int_max_str_digits() to increase the limit

        and on the leading-zeros case below it ("value has 5001 digits");
        2 failed, 5 passed.
        """
        plan = f"L 60, R {PAST}, G 120"
        assert parse_cycle_plan(plan) == [("L", 60), ("G", 120)]

    def test_leading_zeros_count_toward_the_limit(self):
        """5000 zeros and a 5 is five seconds to arithmetic and a refusal to
        ``int()``: dropped, as the server cannot read it."""
        assert parse_cycle_plan("Ha " + "0" * 5000 + "5") == []

    def test_control_a_run_at_the_limit_is_read(self):
        """CONTROL: exactly ``LIMIT`` digits is a number ``int()`` reads, so
        the guard drops only what ``int()`` refuses. And the ordinary table
        reads as it always did."""
        assert parse_cycle_plan(f"OIII {AT}") == [("OIII", int(AT))]
        assert parse_cycle_plan("L 60, R 60, G 60, B 60, Ha 180") == [
            ("L", 60), ("R", 60), ("G", 60), ("B", 60), ("Ha", 180)]


class TestParseSkip:
    def test_an_entry_int_refuses_is_unread(self):
        """#441's entry on a 3x2 is unread, and the entries round it are
        read as always.

        MUTANT "guard removed" (``parse_skip``'s ``int()`` pair unguarded,
        as #441 found it). Observed:

            >           r, c = int(m.group(1)), int(m.group(2))
            E           ValueError: Exceeds the limit (4300 digits) for integer string conversion: value has 5000 digits; use sys.set_int_max_str_digits() to increase the limit

        and on the leading-zeros case below it ("value has 5001 digits");
        2 failed, 5 passed.
        """
        entry = "1-" + "1" * 5000
        assert parse_skip(f"2-1, {entry}, 1-2", 3, 2) == (
            [[1, 2], [2, 1]], [entry])

    def test_leading_zeros_count_toward_the_limit(self):
        """5000 zeros and a 1 is row 1 to arithmetic and a refusal to
        ``int()``: unread, not panel 1-1."""
        entry = "0" * 5000 + "1-1"
        assert parse_skip(entry, 3, 3) == ([], [entry])

    def test_control_the_regex_is_not_narrowed(self):
        """CONTROL: the fix is a guard round ``int()``, not a narrower
        pattern. "01-002" is still panel 1-2, and a row of exactly ``LIMIT``
        digits is read by ``int()`` and then refused as a row past the last,
        the answer it always had."""
        assert parse_skip("01-002", 3, 3) == ([[1, 2]], [])
        at = "0" * (LIMIT - 1) + "1-1"
        assert parse_skip(at, 3, 3) == ([[1, 1]], [])
        big = f"{AT}-1"
        assert parse_skip(big, 3, 3) == ([], [big])
