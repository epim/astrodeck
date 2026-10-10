# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#997: the server tests' mirror of ``humanizeLog`` mirrors the function as it
is now, not as it was before #792 and #960.

Five test modules kept a hand copy of the rewrite rules as bare word pairs
("plate" and "solve", "guid" and "lost", "nina" and a "5"). The real function
stopped rewriting a line that merely mentions those things, so the copies were
stricter than reality, and the vacuity test in test_850_hub_sync_refused pinned
the retired behaviour: it asserted the mirror trips on "the plate solve worked
but the mount refused", which the UI shows as written.

There is now one mirror, ``_humanizer_mirror``, which reads its regular
expressions out of ui/src/lib/humanize.ts. The cases below grade it BEHAVIOUR
by behaviour against fixtures/humanizer_mirror_cases.json, the same file
humanizeRewrites.test.ts holds the real function to, so a rule changed on
either side turns one CI job red.

Mutant "retired pairs back" (``humanizer_rule`` tests ``"plate" in m and
"solve" in m`` and ``"guid" in m and "lost" in m`` in place of the regex
literals): RED on test_the_retired_bare_pairs_are_not_rewrites, on nine of
the corpus rows (the ones that carry the pairs without being the report) and
on test_850_hub_sync_refused's
test_the_humanizer_mirror_trips_on_a_line_the_ui_rewrites.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import _humanizer_mirror as mirror
from _humanizer_mirror import humanizer_rewrites, humanizer_rule

CASES_FILE = Path(__file__).resolve().parent / "fixtures" / "humanizer_mirror_cases.json"
CASES = json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=[
    f"{i:02d}-{c.get('rule') or 'kept'}" for i, c in enumerate(CASES)])
def test_the_mirror_agrees_with_the_shared_corpus(case):
    """Each line is replaced by the rule the corpus names, or by none."""
    got = humanizer_rule(case["message"], case.get("source", ""))
    assert got == case["rule"], (case["message"], case.get("source", ""), got)


def test_the_corpus_is_not_vacuous():
    """Every rule has a line it fires on and a line it leaves alone, so a
    mirror that always said True, or always None, fails the corpus."""
    rules = {c["rule"] for c in CASES}
    assert rules == {None, "lane", "camera", "nina", "plate", "guid"}, rules
    assert sum(1 for c in CASES if c["rule"] is None) >= 10
    assert sum(1 for c in CASES if c["rule"] is not None) >= 10


def test_the_retired_bare_pairs_are_not_rewrites():
    """The #997 premise: a line that holds the words but is not the report
    reaches the operator as written."""
    for line in ("the plate solve worked but the mount refused",
                 "re-centring after guiding was lost",
                 "the plate solve after the restart worked",
                 "slow request GET /api/nina/health: still waiting after 15.0 s"):
        assert not humanizer_rewrites(line), line


def test_the_reports_the_rules_were_written_for_are_rewrites():
    """And the rules still fire on a bare report, so the mirror can say True."""
    assert humanizer_rule("camera not responding") == "camera"
    assert humanizer_rule("NINA HTTP 500") == "nina"
    assert humanizer_rule("plate solve failed") == "plate"
    assert humanizer_rule("guiding was lost") == "guid"


def test_the_regular_expressions_are_read_from_the_ui_source():
    """The mirror holds no copy of the three rules, so there is nothing to go
    stale; the scan finds each one, and a name that is not there fails loudly
    rather than compiling to something that never matches."""
    for name in ("HTTP_5XX", "PLATE_SOLVE_FAILED", "GUIDING_LOST",
                 "LANE_CONFLICT_RE"):
        assert mirror._literal(name).pattern, name
    with pytest.raises(AssertionError, match="scan is broken"):
        mirror._literal("NO_SUCH_RULE_IN_HUMANIZE_TS")
