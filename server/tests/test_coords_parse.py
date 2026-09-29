"""Coordinate parsing, and specifically the characters a coordinate ARRIVES in.

Nobody types a declination. They copy it — from Stellarium, from SIMBAD, from a
forum post, from a design tool that silently autocorrects ' into the right
single quote as you type. Every one of those sources may hand over a typographic
prime (U+2032) or a true minus sign (U+2212) instead of the ASCII characters the
regexes match.

That mattered here concretely: all four Flows example targets carried U+2032 /
U+2033 / U+2212 in their declinations and could not be turned into a real
target. The examples were fixed at source, and the parser was widened so the
next paste from anywhere does not reproduce the same dead end.

The failure mode is what makes this worth a test file of its own — two strings
that render IDENTICALLY on screen, one of which parses and one of which does
not. An operator handed that at 2am has no way to see the difference.

Invisible and lookalike characters are written as \\u escapes below ON PURPOSE.
A test whose subject is "which character is this" must not itself depend on the
reader being able to tell them apart.
"""
from __future__ import annotations

import pytest

from astrodeck.catalog.coords import parse_dec, parse_ra

#: +41 16 09 in decimal degrees — M31's declination.
M31_DEC = 41.0 + 16 / 60 + 9 / 3600
#: -13 49 00 — M16's, and the one that carries a sign.
M16_DEC = -(13.0 + 49 / 60)
#: 20 59 17 in decimal hours — NGC 7000's RA.
NGC7000_RA = 20 + 59 / 60 + 17 / 3600


class TestAsciiStillWorks:
    """The widening must not have moved the baseline."""

    @pytest.mark.parametrize("text", ["+41° 16' 09\"", "+41:16:09", "41 16 09"])
    def test_the_three_ascii_spellings(self, text):
        assert parse_dec(text) == pytest.approx(M31_DEC, abs=1e-9)

    def test_a_bare_decimal(self):
        assert parse_dec("-5.391") == pytest.approx(-5.391)

    def test_ra_sexagesimal_and_decimal(self):
        assert parse_ra("05h 35m 17s") == pytest.approx(5 + 35 / 60 + 17 / 3600)
        assert parse_ra("5.5883") == pytest.approx(5.5883)


class TestTypographicInput:
    def test_primes(self):
        """U+2032 / U+2033 — what a properly typeset table uses for arcmin and
        arcsec, and what every one of our own examples shipped with."""
        assert parse_dec("+41° 16′ 09″") == pytest.approx(M31_DEC, abs=1e-9)

    def test_the_true_minus_sign(self):
        """U+2212 is not a hyphen. It reads as one, sorts near one, and is the
        correct character for a negative number — so a dec table that was set
        with any care will use it."""
        assert parse_dec("−13° 49′ 00″") == pytest.approx(M16_DEC, abs=1e-9)

    def test_curly_quotes_from_an_autocorrecting_editor(self):
        assert parse_dec("+41° 16’ 09”") == pytest.approx(M31_DEC, abs=1e-9)

    def test_a_non_breaking_space(self):
        """HTML copy-paste is full of them, and they are invisible on screen —
        the worst possible thing to ask an operator to spot."""
        assert parse_dec("+41° 16' 09\"") == pytest.approx(M31_DEC, abs=1e-9)

    def test_an_en_dash_as_a_minus(self):
        assert parse_dec("–13:49:00") == pytest.approx(M16_DEC, abs=1e-9)

    def test_ra_gets_the_same_fold(self):
        """RA is written h/m/s and never with primes, but it is pasted from the
        same places — so non-breaking spaces between its fields are the case
        that actually shows up."""
        assert parse_ra("20h 59m 17s") == pytest.approx(NGC7000_RA)


class TestGarbageStillRaises:
    """The fold covers characters whose ASCII meaning is unambiguous. What must
    NOT happen is a string that isn't a coordinate quietly becoming a number:
    raising sends the operator back to fix their input, whereas a returned 0.0
    would point the telescope at the celestial equator and look like success."""

    @pytest.mark.parametrize("text", ["forty-one", "", "N/A", "+", "north"])
    def test_it_raises_rather_than_returning_a_number(self, text):
        with pytest.raises(ValueError):
            parse_dec(text)

    def test_a_digit_that_is_not_0_to_9_is_refused_by_name(self):
        """A fullwidth or Devanagari digit is refused, naming the character,
        not read as the digit it looks like (#359).

        DELIBERATE PIN CHANGE (#359, S5-SRVSMALL; re-pinned by the S5/S6
        integration, S56-INTEG). This case was
        ``test_unicode_decimal_digits_are_accepted_and_that_is_fine`` and
        pinned the opposite: Python's float() reads fullwidth and Devanagari
        digits, and the parser inherited that, "documented rather than
        fought". But the Target modal's preview mirror (``framingModel.ts``)
        reads 0-9 only, so the preview dropped a coordinate the run would
        shoot; the rule is now 0-9 on both sides, and
        test_coords_ascii_digits.py holds it across scripts and both parsers.
        The old pin, run against this tree, observed:
            ValueError: '\\uff14\\uff11.5' has a digit that is not 0-9
            (U+FF14 FULLWIDTH DIGIT FOUR); retype it with 0-9
        This case keeps the fold's own promise in this file's terms: a string
        that is not a coordinate in 0-9 raises rather than quietly becoming a
        number.

        MUTANT "the fallback checks no digit" (coords.py ``_decimal``'s
        ``if ch.isdecimal() and not ch.isascii():`` written ``if False:``,
        so the fallback is Python's Unicode-wide float() again), run in the
        private copy scratchpad S56-INTEG-mut: RED (observed):
            Failed: DID NOT RAISE <class 'ValueError'>
        """
        with pytest.raises(ValueError) as refused:
            parse_dec("４１.5")
        assert "U+FF14 FULLWIDTH DIGIT FOUR" in str(refused.value)
