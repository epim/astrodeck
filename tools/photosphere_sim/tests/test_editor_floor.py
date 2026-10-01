"""#53: the narrowest obstruction the planner honours comes from the editor.

The owner's ruling (2026-09-23): the narrowest obstruction is "the distance one
could reasonably put two of the dots on the horizon editor". The number
is re-derived from the editor's own source on the UI side
(ui/.../__tests__/horizonEditorFloor.test.ts), since the simulator may not read
production code; this pins the constant and that it floors a declared label.

MUTATIONS RUN, and what each printed:

  M1, EDITOR_MIN_WIDTH_DEG = 1.0 (the model's whole-degree floor, the tempting
  wrong answer). 1 failed: test_the_floor_is_the_number_the_ruling_derives, 1.0 against 1.5577.

  M2, drop the `max(declared_val, EDITOR_MIN_WIDTH_DEG)`. 1 failed:
  test_a_label_under_the_floor_is_raised_to_it - resolvable width 0.25.
"""
import unittest

import numpy as np

from sim import score

class EditorFloor(unittest.TestCase):
    def test_the_floor_is_the_number_the_ruling_derives(self):
        """The derivation itself is checked against the editor's source from the
        UI side (horizonEditorFloor.test.ts), because this package must never
        read production code (test_independence)."""
        self.assertAlmostEqual(score.EDITOR_MIN_WIDTH_DEG, 18.0 / (1040.0 * 4.0 / 360.0), places=12)
        self.assertAlmostEqual(score.EDITOR_MIN_WIDTH_DEG, 1.5577, places=3)

    def test_a_label_under_the_floor_is_raised_to_it(self):
        """Through the real scorer, with no product bins so the declared label
        is the only floor (the path where it decides)."""
        bins = 3600
        profile = np.full(bins, -10.0)
        profile[1800:1803] = 12.0                    # 0.3 degrees wide, like pole-far
        ref = {"obstacles": [{"id": "thin", "min_width_deg": 0.25, "alt_max": 12.0,
                              "profile": profile.tolist()}]}
        out = score._score_obstacles(ref, bins, 0.1, np.zeros(bins), np.ones(bins, dtype=bool), None)
        row = out[0] if isinstance(out, list) else out["obstacles"][0]
        self.assertAlmostEqual(row["resolvable_width_deg"], score.EDITOR_MIN_WIDTH_DEG, places=9)


if __name__ == "__main__":
    unittest.main()
