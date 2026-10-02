# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Wizard number parsing: house-style refusals, not CPython's own (WP-26,
backlog ruling D-nn N/A -- no ruling needed, fix shape is the plan's own
text; #501 (a), #546 (b)).

Three readers in ``astrodeck.flows.wizard`` turn operator-typed numbers into
ints: ``_door_rows`` (a ``cycle_plan`` row's seconds, via ``_ROW_RE``'s
``\\d+``), ``cycle_plan_for`` (a quick flow's per-filter exposure override)
and ``_one_channel_exposure_s`` (the one-channel quick flow's exposure).
Before this change each could leak a raw Python exception past the route's
own refusal wording:

* ``_door_rows``: a ``cycle_plan`` row whose seconds run past CPython's
  integer-string-conversion digit limit (4300 digits, ``int()``'s own
  guard) raised ``ValueError`` in CPython's words ("Exceeds the limit
  (4300 digits)..."), not the row-naming sentence every other refusal of
  this reader gives (#501).
* ``cycle_plan_for`` and ``_one_channel_exposure_s``: an infinite exposure
  (JSON ``Infinity``) or one too large for a float (a huge int) raised
  ``OverflowError`` uncaught, which the route (``app.py``, not owned by
  this WP) catches only ``ValueError`` for, so it reached the operator as
  a 500 rather than a refusal (#546).

Both are now refused with a ValueError in this reader's own words, naming
the row (``_door_rows``) or the filter/channel (the other two), so the
existing ``ValueError`` -> 422 conversion covers them without the route
changing at all.
"""
from __future__ import annotations

import pytest

from astrodeck.flows.wizard import (
    KIND_DEEP_SKY, cycle_plan_for, generate_answer, quick,
    _door_rows, _one_channel_exposure_s)

#: One past CPython's int-string-conversion digit limit (4300): long enough
#: that ``int()`` on it alone raises, nowhere near a real exposure.
HUGE_DIGIT_RUN = "9" * 4301

#: A real catalogue row, the same shape test_flows_quick.py uses.
TARGET = {"name": "NGC 6946", "ra": "20h 34m 52s", "dec": "+60 09 14"}


# --------------------------------------------------------- (a) #501, rows
class TestDoorRowDigitLimit:
    def test_an_oversized_digit_run_is_refused_in_house_words(self):
        """RED under mutant "digit-limit guard removed" (the ``try/except
        ValueError`` around ``int(m.group(2))`` in ``_door_rows`` deleted,
        leaving the bare ``int(m.group(2))``), observed verbatim:

            E   ValueError: Exceeds the limit (4300 digits) for integer
            string conversion; use sys.set_int_max_str_digits() to increase
            the limit
        """
        row = f"L {HUGE_DIGIT_RUN}"
        with pytest.raises(ValueError) as e:
            _door_rows(KIND_DEEP_SKY, row, 3, None)
        msg = str(e.value)
        # The house style: names the row, says what is wrong in this
        # reader's own words -- never CPython's "Exceeds the limit".
        assert row in msg, msg
        assert "Exceeds the limit" not in msg, msg
        assert "not a whole number of seconds" in msg, msg

    def test_reaches_the_same_refusal_through_generate_answer(self):
        """The public door (``generate_answer``), not just the helper
        directly: the #501 report was filed against a raw
        ``POST /api/flows/wizard``, which calls this path."""
        with pytest.raises(ValueError) as e:
            generate_answer(KIND_DEEP_SKY, set(), "M31",
                            cycle_plan=f"L {HUGE_DIGIT_RUN}", cycles=3,
                            guiding=True)
        assert "Exceeds the limit" not in str(e.value), str(e.value)

    def test_an_ordinary_row_is_unaffected(self):
        """The guard added for the oversized case must not touch the
        ordinary path: a ten-digit row still reads as the number it is."""
        plan, cycles, rows = _door_rows(KIND_DEEP_SKY, "L 9999999999", 3,
                                        None)
        assert rows == [("L", 9999999999)]


# ------------------------------------------------ (b) #546, quick exposures
class TestQuickExposureOverflow:
    @pytest.mark.parametrize("bad", [float("inf"), -float("inf"), 10 ** 400],
                             ids=["+inf", "-inf", "huge-int"])
    def test_cycle_plan_for_refuses_rather_than_500s(self, bad):
        """RED under mutant "OverflowError not caught" (the ``except
        OverflowError:`` clause in ``cycle_plan_for`` deleted), observed
        verbatim for the ``+inf`` case:

            E   OverflowError: cannot convert float infinity to integer

        and for the ``huge-int`` case:

            E   OverflowError: int too large to convert to float
        """
        with pytest.raises(ValueError) as e:
            cycle_plan_for(["L"], None, {"L": bad})
        msg = str(e.value)
        assert "L" in msg, msg
        assert "Overflow" not in msg, msg

    def test_an_ordinary_override_is_unaffected(self):
        """The guard must not touch the existing fallback path: a filter
        with no override still takes its wheel default, as it always has."""
        plan = cycle_plan_for(["L", "Ha"], None, {"L": 30})
        assert plan == "L 30, Ha 180"

    @pytest.mark.parametrize("bad", [float("inf"), 10 ** 400],
                             ids=["inf", "huge-int"])
    def test_one_channel_exposure_refuses_rather_than_500s(self, bad):
        """RED under mutant "OverflowError not caught" (the ``except
        OverflowError:`` clause in ``_one_channel_exposure_s`` deleted),
        observed verbatim:

            E   OverflowError: cannot convert float infinity to integer
        """
        with pytest.raises(ValueError) as e:
            _one_channel_exposure_s({"OSC": bad})
        msg = str(e.value)
        assert "OSC" in msg, msg
        assert "Overflow" not in msg, msg

    def test_quick_itself_refuses_an_infinite_exposure(self):
        """The whole door (``wizard.quick``), the surface #546 was filed
        against via ``POST /api/flows/quick``: a ``ValueError`` here is what
        the route already turns into a 422, with no route change (the
        route is not owned by this WP and is unchanged)."""
        with pytest.raises(ValueError) as e:
            quick(TARGET, 3, ["L"], {"L": float("inf")})
        assert "Overflow" not in str(e.value), str(e.value)
