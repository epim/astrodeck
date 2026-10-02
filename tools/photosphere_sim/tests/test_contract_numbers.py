# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""CONTRACT.md's worked examples must still be what the scorer measures (#89).

The contract quotes case-specific measured numbers in the present tense and
nothing re-derived them. A tracer fix (`3fbd2039`) inverted one: the paragraph
said `wall-east` was a real width miss with `width_missed_deg` 12.0 and
`no_missed_obstructions` correctly false, while the case had read 0.0 and true
since the fix. The pass that moved the measurement updated
`docs/ui-rebuild/18-photosphere-simulator-baseline.md`, which has a date and a
refresh procedure, and not the contract, which has neither.

This checks the class rather than that one paragraph, and it checks it in BOTH
directions:

- the numbers below must equal what `scores.json` says, so a code change that
  moves a measurement fails here;
- the numbers must still appear in CONTRACT.md as written, so editing the
  prose without re-deriving fails here too.

Either alone would let the pair drift apart, which is exactly how the stale
paragraph survived.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
CONTRACT = _ROOT / "CONTRACT.md"
CACHE_CASES = _ROOT / "cache" / "cases"

#: (case, obstacle, field, value, the text CONTRACT.md must still carry).
#: Sourced from CONTRACT.md's horizon section, the `chartyard-arc075-60`
#: roof-south worked example and the `wall-east` note beside it. `width_missed_deg`
#: is the LONGEST under-reported run and `width_missed_total_deg` the sum,
#: which is the distinction #64 introduced and this example exists to show.
QUOTED = [
    ("chartyard-arc075-60", "roof-south", "width_missed_total_deg", 29.8, "29.8"),
    ("chartyard-arc075-60", "roof-south", "width_missed_deg", 10.2, "10.2"),
    ("chartyard-arc075-60", "wall-east", "width_missed_deg", 0.0, None),
    ("chartyard-arc075-60", "wall-east", "width_missed_total_deg", 0.0, None),
]


def _scores(case: str) -> dict | None:
    p = CACHE_CASES / case / "result" / "scores.json"
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _obstacle(scores: dict, obstacle_id: str) -> dict:
    for row in (scores.get("horizon") or {}).get("obstacles") or []:
        if row.get("id") == obstacle_id:
            return row
    raise AssertionError(f"no obstacle {obstacle_id!r} in the scored horizon")


class ContractNumbersAreStillMeasured(unittest.TestCase):
    """MUTATION: change 29.8 to 30.8 in CONTRACT.md's roof-south sentence.
    Observed: the prose check fails naming the missing text, which is the
    direction that caught nothing before."""

    def test_the_contract_still_says_what_it_says(self):
        """Unconditional: it reads a committed file, so a fresh clone runs it."""
        text = CONTRACT.read_text(encoding="utf-8")
        for case, obstacle, field, _value, quoted in QUOTED:
            if quoted is None:
                continue
            with self.subTest(case=case, obstacle=obstacle, field=field):
                # assertTrue, not assertIn: assertIn appends the haystack to
                # the failure, and the haystack here is the whole contract --
                # 42 KB of it, which buries the one line that matters.
                self.assertTrue(
                    quoted in text,
                    f"CONTRACT.md no longer contains {quoted!r} for {obstacle}'s "
                    f"{field}. Either the prose was edited without re-deriving "
                    f"the number, or the measurement moved and only this table "
                    f"was updated. Re-score {case} and correct both.")

    def test_the_scorer_still_measures_them(self):
        """Skips loudly without the recordings, as the replay cases do."""
        seen = 0
        for case, obstacle, field, value, _quoted in QUOTED:
            scores = _scores(case)
            if scores is None:
                continue
            seen += 1
            with self.subTest(case=case, obstacle=obstacle, field=field):
                actual = _obstacle(scores, obstacle).get(field)
                self.assertAlmostEqual(
                    actual, value, places=1,
                    msg=f"CONTRACT.md quotes {field} {value} for {obstacle} on "
                        f"{case}; the scorer now says {actual}. The contract is "
                        f"what a second implementation is written against, so a "
                        f"stale number there is worse than a stale one in the "
                        f"baseline document.")
        if not seen:
            raise unittest.SkipTest(
                f"no scored recordings under {CACHE_CASES}; render and score "
                f"them to check the contract's numbers (README: sim render)")

    def test_the_case_the_example_is_about_still_passes_its_gate(self):
        """The paragraph's last clause: `no_missed_obstructions` passes on that
        case BECAUSE the longest run is under the threshold. A change that
        moved the run past it without touching the prose would leave the
        contract asserting the opposite of the gate."""
        scores = _scores("chartyard-arc075-60")
        if scores is None:
            raise unittest.SkipTest("chartyard-arc075-60 is not scored here")
        self.assertIs(
            (scores.get("gates") or {}).get("no_missed_obstructions"), True,
            "CONTRACT.md says this case's no_missed_obstructions passes")


if __name__ == "__main__":      # pragma: no cover
    unittest.main()
