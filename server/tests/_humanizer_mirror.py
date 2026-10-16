# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The one Python mirror of ``humanizeLog`` (ui/src/lib/humanize.ts) for tests
that grade a server-written line against it (#997).

A server line the UI rewrites never reaches the operator in our words, so many
tests assert ``not humanizer_rewrites(line)``. Five of them kept a hand copy of
the rules as they stood before #792 and #960 (two bare words anywhere in the
text: "plate" and "solve", "guid" and "lost", "nina" and a "5"), and those
copies went on over-constraining server wording after the UI stopped rewriting
it. A copy nothing keeps goes stale, so this module has no copy of the three
regular expressions: it READS them out of humanize.ts (``const NAME = /.../;``)
and compiles them. What it does hold, the order of the rules and their keyword
tests, is graded against the real function from both sides by
fixtures/humanizer_mirror_cases.json (test_997_humanizer_mirror.py here,
humanizeRewrites.test.ts in the UI).

The regular expressions are anchored or token-based and use no flag, so JS and
``re`` read them alike.

The other thing a server line is held to is the length budget (#1024):
``CLIP_AT`` below, read out of humanize.ts the same way. A line that fits it is
never touched, so a test that wants "the operator reads all of this" asserts
``len(line) <= CLIP_AT``. The 137-character cut that many tests used to pin was
retired by #792, and a number copied into a test is a copy nothing keeps.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

HUMANIZE_TS = (Path(__file__).resolve().parents[2]
               / "ui" / "src" / "lib" / "humanize.ts")


@lru_cache(maxsize=None)
def _literal(name: str) -> re.Pattern[str]:
    """The regex literal humanize.ts assigns to ``const <name>``, compiled.
    Fails loudly when the scan finds nothing: a mirror that cannot read the
    rule proves nothing."""
    src = HUMANIZE_TS.read_text(encoding="utf-8")
    m = re.search(rf"^const {name} =\s*/(.+)/;$", src, re.MULTILINE)
    if m is None:
        raise AssertionError(
            f"cannot find `const {name} = /.../;` in {HUMANIZE_TS}: the scan "
            "is broken, so the humanizer mirror proves nothing")
    return re.compile(m.group(1))


@lru_cache(maxsize=None)
def humanizer_clip_at() -> int:
    """The length ``humanizeLog`` lets a line keep whole: the number in
    ``export const CLIP_AT = N;``. A longer line is shortened to whole
    sentences, so a test that needs the operator to read all of a line asserts
    ``len(line) <= CLIP_AT``. Fails loudly when the scan finds nothing."""
    src = HUMANIZE_TS.read_text(encoding="utf-8")
    m = re.search(r"^export const CLIP_AT = (\d+);$", src, re.MULTILINE)
    if m is None:
        raise AssertionError(
            f"cannot find `export const CLIP_AT = N;` in {HUMANIZE_TS}: the "
            "scan is broken, so a length budget read from it proves nothing")
    return int(m.group(1))


#: The budget itself, for the tests that grade a surfaced line against it.
CLIP_AT = humanizer_clip_at()


def shown_whole(text: str) -> str:
    """What the operator reads of ``text``: all of it, because it fits the
    budget. A line past ``CLIP_AT`` is shortened to whole sentences, which this
    does not model; it fails instead, so a test that slices ``shown_whole(line)``
    to ask "is the cause visible" cannot pass on a line the UI would clip."""
    assert len(text) <= CLIP_AT, (
        f"{len(text)} characters, past the {CLIP_AT} the UI keeps whole: "
        f"{text!r}")
    return text


def humanizer_rule(text: str, source: str = "") -> str | None:
    """The ``humanizeLog`` rule that would replace ``text`` whole, or None when
    the line reaches the operator as written: ``lane`` (the busy-lane 409),
    ``camera``, ``nina``, ``plate`` or ``guid``, in the function's order.
    ``source`` is the log line's source ("capture" opens the camera rule)."""
    if _literal("LANE_CONFLICT_RE").match(text.strip()):
        return "lane"
    m = text.lower()
    if (source == "capture" or "camera" in m) and any(
            w in m for w in ("not responding", "timeout", "disconnect")):
        return "camera"
    if "nina" in m and (_literal("HTTP_5XX").search(m) or "http" in m
                        or "error" in m):
        return "nina"
    if _literal("PLATE_SOLVE_FAILED").search(m):
        return "plate"
    if _literal("GUIDING_LOST").search(m):
        return "guid"
    return None


def humanizer_rewrites(text: str, source: str = "") -> bool:
    """Whether ``humanizeLog`` would replace ``text`` with its own sentence."""
    return humanizer_rule(text, source) is not None
