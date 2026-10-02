# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""A coordinate is read with the digits 0-9 and no others (#359).

``catalog/coords.py`` said a digit that merely looks like one (fullwidth,
Devanagari) "is left alone to fail loudly", and it did not fail: Python's
``\\d`` and ``float()`` are Unicode-wide, so ``parse_ra`` read fullwidth
zeros as zeros and ``parse_dec`` read Arabic-Indic digits as digits. The
modal's preview mirror (``framingModel.ts`` ``parseRaHours`` /
``parseDecDeg``) reads 0-9 only, so the preview dropped a coordinate the run
would shoot. The rule is now the comment's: the patterns are compiled with
``re.ASCII``, and the decimal fallback, which is ``float()`` and so wide
too, refuses a foreign digit itself and names it.

``compile.parse_skip`` keeps reading Unicode digits on purpose (a panel
label is not a coordinate; ``skip_cases.json`` pins it), and the last case
here holds that the two rules stayed apart.

Every script is built from the ASCII text by a digit table, and every
lookalike is written as a ``\\u`` escape, as test_coords_parse.py does: a
test whose subject is which character this is must not depend on the reader
telling them apart.

Every mutant below was run in a private copy of ``server/`` under the
session scratchpad (``s5-srvsmall-mut``), from a byte backup, never in the
shared tree.
"""
from __future__ import annotations

import pytest

from astrodeck.catalog.coords import parse_dec, parse_ra

#: 00h 42m 44s, M31's RA, in hours.
M31_RA = 42 / 60 + 44 / 3600
#: +41 16 09, M31's Dec, in degrees.
M31_DEC = 41 + 16 / 60 + 9 / 3600

#: The first digit of each script's run of ten (Unicode encodes every
#: decimal digit set as ten consecutive code points, zero first).
SCRIPTS = {
    "fullwidth": 0xFF10,
    "Arabic-Indic": 0x0660,
    "Devanagari": 0x0966,
}


def _in(script: str, text: str) -> str:
    """``text`` with every ASCII digit written in ``script``."""
    zero = SCRIPTS[script]
    return text.translate({ord("0") + i: zero + i for i in range(10)})


def _name(script: str, digit: str) -> str:
    """How the refusal names one of ``script``'s digits: its code point and
    its Unicode name, e.g. ``U+FF14 FULLWIDTH DIGIT FOUR``."""
    import unicodedata
    ch = _in(script, digit)
    return f"U+{ord(ch):04X} {unicodedata.name(ch)}"


# ------------------------------------------------------------ the refusals

#: The sexagesimal forms a pasted coordinate arrives in, in ASCII.
SEXAGESIMAL = [(parse_ra, "00h 42m 44s"), (parse_ra, "00:42:44"),
               (parse_ra, "00h 42.73m"), (parse_dec, "+41\u00b0 16' 09\""),
               (parse_dec, "41:16:09"), (parse_dec, "-41 16")]
#: The decimal forms, which no pattern takes and ``float()`` reads.
DECIMAL = [(parse_ra, "0.7122"), (parse_dec, "41.5"), (parse_dec, "-5.391")]


def _ids(cases):
    return [f"{parse.__name__}-{i}" for i, (parse, _) in enumerate(cases)]


def _refused(parse, text: str) -> str:
    """The message ``parse`` refuses ``text`` with; a failure naming what it
    read instead."""
    with pytest.raises(ValueError) as refused:
        got = parse(text)
        pytest.fail(f"{parse.__name__}({text!r}) read {got!r}")
    return str(refused.value)


@pytest.mark.parametrize("script", list(SCRIPTS))
@pytest.mark.parametrize("parse, ascii_text", SEXAGESIMAL,
                         ids=_ids(SEXAGESIMAL))
def test_a_sexagesimal_coordinate_in_another_script_is_refused(
        parse, ascii_text, script):
    """Every digit in ``script``: refused, and the refusal names the value
    and the first digit it could not read, so the operator is not left to
    hunt for a difference their screen does not show.

    RED under mutant "re.ASCII dropped" (the flag taken off the four
    patterns): all 18 cases, every form in every script, and no decimal
    case. Observed, the digits written here as escapes:

        E           Failed: parse_ra('\uff10\uff10h \uff14\uff12m
        \uff14\uff14s') read 0.7122222222222222
        E           Failed: parse_ra('\u0660\u0660h \u0664\u0662m
        \u0664\u0664s') read 0.7122222222222222
        E           Failed: parse_ra('\u0966\u0966h \u096a\u0968m
        \u096a\u096as') read 0.7122222222222222
        E           Failed: parse_dec('\uff14\uff11:\uff11\uff16:\uff10\uff19')
        read 41.26916666666666

    RED under mutant "fallback unguarded" (``_decimal``'s check made ``if
    False``): all 18, since no pattern takes these and ``float()``'s own
    refusal names no digit. Observed:

        E       AssertionError: could not convert string to float:
        '\u0660\u0660h \u0664\u0662m \u0664\u0664s'
        E       assert 'U+0660 ARABIC-INDIC DIGIT ZERO' in "could not
        convert string to float: '\u0660\u0660h \u0664\u0662m
        \u0664\u0664s'"
    """
    text = _in(script, ascii_text)
    message = _refused(parse, text)
    assert repr(text) in message, message
    assert _name(script, ascii_text.lstrip("+-")[0]) in message, message


@pytest.mark.parametrize("script", list(SCRIPTS))
@pytest.mark.parametrize("parse, ascii_text", DECIMAL, ids=_ids(DECIMAL))
def test_a_decimal_coordinate_in_another_script_is_refused(
        parse, ascii_text, script):
    """The decimal fallback is ``float()``, which reads every Unicode
    decimal digit whatever the patterns above it do, so it refuses a
    foreign digit itself.

    RED under mutant "fallback unguarded": all 9. Observed, one per script:

        E           Failed: parse_ra('\uff10.\uff17\uff11\uff12\uff12') read
        0.7122
        E           Failed: parse_dec('\u0664\u0661.\u0665') read 41.5
        E           Failed: parse_dec('-\u096b.\u0969\u096f\u0967') read
        -5.391

    Green under "re.ASCII dropped": no pattern ever sees these."""
    text = _in(script, ascii_text)
    message = _refused(parse, text)
    assert repr(text) in message, message
    assert _name(script, ascii_text.lstrip("+-")[0]) in message, message


@pytest.mark.parametrize("parse, text, bad", [
    (parse_ra, "00h 4\uff12m 44s", "\uff12"),
    (parse_dec, "+41 16 0\u0669", "\u0669"),
    (parse_dec, "41.2\u096c9", "\u096c")], ids=["ra", "dec", "decimal"])
def test_one_foreign_digit_among_ascii_ones_is_refused(parse, text, bad):
    """The case a paste really produces: one foreign digit among ASCII
    ones, which reads as one number on screen. It is the one named.

    RED under mutant "re.ASCII dropped", ``ra`` and ``dec``, observed:

        E           Failed: parse_ra('00h 4\uff12m 44s') read
        0.7122222222222222
        E           Failed: parse_dec('+41 16 0\u0669') read 41.26916666666666

    RED under mutant "fallback unguarded", all three, observed:

        E       AssertionError: could not convert string to float: '00h
        4\uff12m 44s'
        E       assert 'U+FF12' in "could not convert string to float: '00h
        4\uff12m 44s'"
        E           Failed: parse_dec('41.2\u096c9') read 41.269
    """
    message = _refused(parse, text)
    assert f"U+{ord(bad):04X}" in message, message


# ------------------------------------------------------------ the controls

def test_ascii_digits_still_read():
    """The control for every refusal: the same coordinates in 0-9. Green
    under every mutant in this file."""
    assert parse_ra("00h 42m 44s") == pytest.approx(M31_RA)
    assert parse_ra("00:42:44") == pytest.approx(M31_RA)
    assert parse_ra("0.7122") == pytest.approx(0.7122)
    assert parse_dec("+41\u00b0 16' 09\"") == pytest.approx(M31_DEC)
    assert parse_dec("-5.391") == pytest.approx(-5.391)


def test_typographic_dashes_and_quotes_are_still_normalised():
    """The fold in ``_TYPOGRAPHIC`` runs before the ASCII patterns, so a
    true minus, both dashes, primes and curly quotes still read. The
    control: green under every refusal's mutant above.

    RED under mutant "fold skipped" (``parse_dec``'s ``translate`` taken
    out), which shows it can fail, observed:

        E       ValueError: could not convert string to float: '\u221213  49\u2032
        00\u2033'
    """
    m16 = -(13 + 49 / 60)
    for text in ("\u221213\u00b0 49\u2032 00\u2033", "\u201313:49:00",
                 "\u201413:49:00", "-13\u00b0 49\u2019 00\u201d",
                 "-13\u00b0 49\u2018 00\u201c"):
        assert parse_dec(text) == pytest.approx(m16), text
    assert parse_ra("00h\u00a042m\u00a044s") == pytest.approx(M31_RA)


def test_every_space_python_knows_still_separates_the_fields():
    """``re.ASCII`` narrows ``\\s`` as well as ``\\d``, and these were read
    before #359: a thin space, a narrow no-break space (French typography
    sets one before a unit) and an ideographic space (CJK input, the same
    keyboards fullwidth digits come from). The UI mirror's ``pyStrip`` and
    ``WS`` are Python's ``str.isspace()`` set, so narrowing here would have
    the preview draw a coordinate the run cannot read.

    RED under mutant "whitespace narrowed with the digits" (``_SP`` a bare
    ``\\s``, which ``re.ASCII`` narrows to the ASCII six), observed:

        E       ValueError: could not convert string to float:
        '00h\\u200942m\\u200944s'
    """
    for space in ("\u2009", "\u202f", "\u3000"):
        assert parse_ra(f"00h{space}42m{space}44s") == pytest.approx(M31_RA)
        assert parse_dec(f"+41{space}16{space}09") == pytest.approx(M31_DEC)


def test_a_panel_label_still_reads_unicode_digits():
    """The rule coords.py's comment states for the skip list: a panel label
    is not a coordinate, so ``compile.parse_skip`` reads a fullwidth "3-1"
    as panel 3-1 (``skip_cases.json`` pins it for both mirrors). Held here
    because this file's refusal is the change that could be copied there by
    someone making the two agree.

    RED under mutant "parse_skip made ASCII too" (``re.ASCII`` on
    ``compile._SKIP_RE``), observed:

        E       AssertionError: assert ([], ['\uff13-1']) == ([[3, 1]], [])
    """
    from astrodeck.flows.compile import parse_skip
    assert parse_skip("\uff13-1", 3, 3) == ([[3, 1]], [])
    assert parse_skip("\u0663-\u0661", 3, 3) == ([[3, 1]], [])
