"""Azimuth stays inside [0, 360) after rounding.

FOUND BY CI, as `assert 360.0 < 360.0` on Polaris from Greenwich, on a commit
whose successor passed. That "passed on the next run" is the whole character of
it: Polaris sits due north, so its azimuth hovers at the 0/360 seam, and only
when it is within a twentieth of a degree of due north does `round(az, 1)` tip
to 360.0. It appears and disappears with the sidereal clock.

`altaz` is not wrong — it returns 359.97, which is a fine azimuth. ROUNDING
introduces the out-of-range value, so the fix belongs at every rounding site and
there were four of them: two in the API, two in visibility.

This is a PRODUCTION bug the test surfaced, not a flaky test. The route
documents [0, 360) and the Atlas consumes it; anything bucketing or normalising
by azimuth gets an out-of-range input roughly whenever something is due north.
"""
from __future__ import annotations

import pytest

from astrodeck.catalog import round_az_deg


class TestTheSeamAtDueNorth:
    # 359.95 is NOT in this list and that is not an oversight: the float just
    # below the decimal midpoint, so round(359.95, 1) is 359.9. Guessing the
    # boundary put it here on the first draft and this test rejected it, which
    # is the behaviour I wanted from it.
    @pytest.mark.parametrize("az", [359.951, 359.96, 359.97, 359.99,
                                    359.999, 360.0])
    def test_anything_that_would_round_up_to_360_becomes_zero(self, az):
        assert round(az, 1) == 360.0, "precondition: this input does round up"
        got = round_az_deg(az)
        assert got == 0.0, f"{az} -> {got}, which is outside [0, 360)"

    @pytest.mark.parametrize("az", [0.0, 0.04, 12.34, 180.0, 359.9, 359.94,
                                    359.95])
    def test_everything_else_rounds_normally(self, az):
        got = round_az_deg(az)
        assert 0.0 <= got < 360.0
        assert got == pytest.approx(round(az, 1))

    def test_360_and_0_are_the_same_direction_so_it_wraps_not_clamps(self):
        """Clamping to 359.9 would be a lie about where the telescope points —
        a twentieth of a degree on the wrong side of north. 0 is the same
        bearing and is inside the range."""
        assert round_az_deg(360.0) == 0.0
        assert round_az_deg(359.97) != 359.9


class TestItSurvivesRubbishInput:
    @pytest.mark.parametrize("az,want", [(-0.02, 0.0), (-90.0, 270.0),
                                         (720.0, 0.0), (450.0, 90.0)])
    def test_out_of_range_input_is_normalised(self, az, want):
        assert round_az_deg(az) == want

    def test_the_invariant_holds_across_a_whole_turn(self):
        """The property, not a handful of cases: no input anywhere on the
        circle may produce something outside the half-open range."""
        for i in range(0, 3600001, 7):
            got = round_az_deg(i / 10000.0)
            assert 0.0 <= got < 360.0, f"{i / 10000.0} -> {got}"


class TestEveryRoundingSiteUsesIt:
    def test_no_bare_az_rounding_survives(self):
        """A helper nothing calls is the defect still shipping. Four sites
        rounded azimuth and all four had the bug; this fails if a fifth appears,
        or if one reverts."""
        import pathlib
        import re

        import astrodeck.api.app as app_mod
        import astrodeck.catalog.visibility as vis_mod

        bad = []
        for mod in (app_mod, vis_mod):
            src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
            for m in re.finditer(r"round\(\s*(?:float\()?[^)]*az[^)]*\)", src):
                frag = m.group(0)
                if "round_az_deg" not in frag:
                    line = src[:m.start()].count("\n") + 1
                    bad.append(f"{mod.__name__}:{line}  {frag}")
        assert not bad, "azimuth rounded without the wrap:\n  " + "\n  ".join(bad)
