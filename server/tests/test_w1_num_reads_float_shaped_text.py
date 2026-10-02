# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""``compile._num`` reads float-shaped whole-number text as a number (#547).

THE DEFECT. ``_num`` computed ``int(v) if float(v).is_integer() else
float(v)``. ``int()`` never parses a decimal point or an exponent, so for
text like "-30.0", "10.0" or "1e1" - each an integral float - the ``int(v)``
branch raised ``ValueError`` on the ORIGINAL text, and the surrounding
``except`` caught it and answered ``default``, silently. A DUSK offset, a
FILTER CYCLE count or a capture's exposure typed or stored that way
compiled to the default rather than the number the card held.

THE FIX tries ``int(v)`` first - exact for ANY plain integer text, of any
length, because Python's ints are arbitrary precision - and only falls back
to ``int(f)`` (truncating the float already computed) when ``int(v)``
itself raises, which happens only for the float-shaped case #547 is about.

A REGRESSION THE FIRST ATTEMPT AT THIS FIX MADE, and this file guards
against it: reading EVERY integral float as ``int(f)`` unconditionally
(without trying ``int(v)`` first) corrupts a 300-plus digit integer stored
as text - ``float()`` collapses it to ~17 significant digits, so ``int(f)``
hands back a DIFFERENT huge number, silently. That is the same "a claim
nothing keeps" shape #547 itself is, so the fix must not trade one silent
corruption for another (test_flows_compile_never_raises.py's own
301-digit-exposure control caught this when it was tried).

Mutant "int(v) restored" (the code before this fix,
``int(v) if float(v).is_integer() else float(v)``): every float-shaped case
below goes RED, reading ``default`` instead of the number, verbatim:

    assert 0 == 10
     +  where 0 = _num('10.0')

The plain int/float and big-digit-string controls stay green under that
mutant, since ``int(v)``/``float(v)`` already read those correctly - this
file could not tell the mutant from the fix without them.
"""
from __future__ import annotations

import math

from astrodeck.flows.compile import _num


def test_float_shaped_whole_number_text_reads_as_the_number():
    assert _num("10.0") == 10
    assert _num("-30.0") == -30
    assert _num("1e1") == 10
    assert _num("0.0") == 0
    # The TYPE matters as much as the value (#328's own reading, still true
    # here): a whole number reads as an ``int``, so a later ``int()`` of it
    # (a count, a cycle) does not raise on it a second time.
    assert isinstance(_num("10.0"), int)
    assert isinstance(_num("-30.0"), int)
    assert isinstance(_num("1e1"), int)


def test_a_fractional_number_is_unaffected():
    # `_num` never truncates a real fraction - only a WHOLE number stored as
    # float-shaped text was ever misread.
    assert _num("10.5") == 10.5
    assert _num(2.7) == 2.7


def test_plain_integers_and_numbers_pass_through_unchanged():
    assert _num("10") == 10
    assert _num(10) == 10
    assert _num(-5) == -5
    assert _num(0) == 0


def test_unreadable_text_still_answers_the_default():
    assert _num("abc", default=7) == 7
    assert _num(None, default=3) == 3
    assert _num([1, 2], default=1) == 1


def test_infinity_and_nan_are_not_integral_and_pass_through():
    # `_num` hands these back as themselves - `_finite` is what turns them
    # into `default` - so neither is touched by #547's fix at all, which
    # only ever changes the INTEGRAL branch.
    assert _num("inf") == float("inf")
    assert math.isnan(_num("nan"))


def test_a_huge_integer_stored_as_text_keeps_every_digit():
    """The regression #547's fix must not cause (see the module docstring):
    a 301-digit exposure read exactly by the old code (``int()`` of the raw
    text has arbitrary precision) must still read exactly, not rounded
    through float64.

    Mutant "int(f) always" (the integral branch read as ``int(f)``
    unconditionally, the ``int(v)`` attempt dropped): RED, observed:
        assert 1000000000000000052504760255204420248... == 100000...(301 digits)
         +  where 1000000000000000052504760255204420248... = _num('1000...0')
    """
    text = "1" + "0" * 300
    got = _num(text)
    assert got == int(text), "a 301-digit integer must read back exactly"
    assert len(str(got)) == 301, "the float round trip must not have run"
