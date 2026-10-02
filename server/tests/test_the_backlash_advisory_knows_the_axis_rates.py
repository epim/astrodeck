# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""#14: the engine's "RA and Dec rates vary" advisory cannot pass on a mount
whose two axes pulse at different rates, and it blames backlash.

THE ADVISORY IS A LITERAL PORT of PHD2's `scope.cpp:900-918` (see
`astro-guide/src/calibration.rs:730-742`): it compares the measured
`x_rate / y_rate` against `cos(dec)`. That identity holds only when the
hardware drives both axes at ONE rate, so that the whole difference between
them is the cos(dec) foreshortening of RA.

This rig's mount does not. The AM5N pulses RA at 1.0x sidereal and Dec at
0.5x, measured over 10 s GR/GD deltas and written into the driver as
`_PULSE_RA_RATE_DEG_S = 0.004178` and `_PULSE_DEC_RATE_DEG_S = 0.002089`
(`devices/backends/zwo_am5.py:127-130`). The expected ratio on this mount is
therefore 2 cos(dec), the check compares it against cos(dec), and the gap is
cos(dec) itself - 0.82 at dec +34, four times the 0.20 tolerance. The advisory
cannot pass at any declination it is willing to judge.

WHAT THAT COST. It fired twice, unprompted, on 2026-09-13/14, and is what
opened #14 ("often caused by large Dec backlash"). The investigation that
followed spent most of a session on a mechanical hypothesis that nothing
observed required; the evidence pack's own summary
(`docs/investigations/2026-09-14-dec-axis-saturation.md`) lists as fix 3 "teach
the rate-variance advisory the per-axis guide rates before it blames
backlash", and marks the suspicion UNVERIFIED pending someone reading what the
advisory actually compares. This is that, verified and closed.

The regrade is deliberately narrow: it changes nothing unless the mount
reports two DIFFERENT rates, and when it cannot re-judge - no rates, a
symmetric mount, an unknown or too-polar declination - the engine's warning
goes out untouched. A regrade that guessed at a missing input would be the
same mistake pointing the other way.

MUTATIONS RUN, and what each killed:

  M1, `expected = math.cos(dec_rad) * asymmetry` -> `math.cos(dec_rad)`, which
  is the defect itself restored. 9 failed / 12 passed: every declination case,
  the rig's own case, the tolerance boundary, the report, and - the one worth
  noticing - the CONTROL, because with the wrong expectation even a genuinely
  bad ratio is wrong about by how much.

  M2, `if abs(expected - actual) <= _MAX_CAL_RA_RATE_DRIFT:` -> `if True:`, a
  regrade that mutes everything. 3 failed: the control, the boundary, and the
  report's real-warning case. Nothing else notices, which is exactly why the
  control is here.

  M3, drop the symmetric-mount guard. 1 failed: a mount with one rate on both
  axes stops getting the engine's own verdict.

  M4, the report appends every advisory ungraded. 1 failed: the panel and the
  log disagree again.
"""
from __future__ import annotations

import math

import pytest

from astrodeck.guide.native import regrade_rate_advisory
from test_native_guide_optics_scale import _guider_with_cal  # rootdir-relative

#: The engine's sentence, verbatim (calibration.rs:738).
ADVISORY = ("Advisory: Calibration completed but RA and Dec rates vary by an "
            "unexpected amount (often caused by large Dec backlash)")
#: And a different one, which must pass through whatever the rates are.
ORTHO = ("Advisory: Calibration completed but RA/Dec axis angles are "
         "questionable and guiding may be impaired")

#: deg/s, as `zwo_am5.py` reports them: RA 1.0x sidereal, Dec 0.5x.
AM5 = (0.004178, 0.002089)
#: A mount that drives both axes at one rate, which is what the engine assumes.
SYMMETRIC = (0.004178, 0.004178)


def _cal(dec_deg: float, ratio: float) -> dict:
    """A calibration whose measured `x_rate / y_rate` is exactly `ratio`."""
    return {"is_valid": True, "declination": math.radians(dec_deg),
            "x_rate": 0.02 * ratio, "y_rate": 0.02,
            "y_angle_error": 0.0, "pier_side": "west", "binning": 1}


# ------------------------------------------------------- the rig's own case

def test_the_rigs_two_to_one_mount_is_not_accused_of_backlash():
    """THE BUG. At dec +34.4 (NGC 7331, the night in #14) a correct
    calibration on this mount measures 2 cos(dec) = 1.65, the engine expects
    cos(dec) = 0.82, and the operator is told to go looking for backlash."""
    dec = 34.4
    measured = 2.0 * math.cos(math.radians(dec))
    level, text = regrade_rate_advisory(ADVISORY, _cal(dec, measured), AM5)
    assert level == "info", (
        f"a correct calibration on a 2:1 mount still raised a warning: {text}")
    assert "is not raised" in text, (
        f"an info line that reads like the accusation is no better than the "
        f"accusation: {text}")
    assert "1.65" in text and "asymmetry" in text, (
        f"the regrade must show its working, or the next reader re-derives "
        f"it from scratch as this one did: {text}")


@pytest.mark.parametrize("dec", [0.0, 15.0, 34.4, 45.0, 59.9])
def test_it_holds_across_every_declination_the_check_will_judge(dec):
    """Nothing here is tuned to one target: the correction is the RATE RATIO,
    and the declination cancels out of it."""
    measured = 2.0 * math.cos(math.radians(dec))
    level, _text = regrade_rate_advisory(ADVISORY, _cal(dec, measured), AM5)
    assert level == "info"


def test_the_advisory_the_issue_quotes_cannot_have_come_from_ngc_7129():
    """A FINDING, recorded as a test because it corrects #14's evidence.

    #14 quotes both advisories against the NGC 7129 night, but 7129 is at dec
    +66.1 and `DEC_COMP_LIMIT` is 60 degrees - past it the engine does not
    compare the rates at all. Only the ORTHOGONALITY advisory can fire at that
    declination. So the rate advisory in that issue came from the NGC 7331
    calibration (dec +34.4), and the two quoted lines are from different
    calibrations rather than one doubly-unhappy walk."""
    seven_129 = _cal(66.1, 2.0 * math.cos(math.radians(66.1)))
    assert regrade_rate_advisory(ADVISORY, seven_129, AM5) == ("warning",
                                                               ADVISORY), (
        "the regrade must decline exactly where the engine's check declines")
    assert 66.1 > math.degrees(math.pi / 3.0)


def test_the_engine_check_cannot_pass_on_this_mount():
    """The premise, stated as a test so it cannot quietly stop being true.

    Without the regrade the gap IS cos(dec), at every declination the check
    is willing to judge, and the tolerance is 0.20."""
    for dec in (0.0, 20.0, 34.4, 50.0, 59.0):
        c = math.cos(math.radians(dec))
        gap = abs(c - 2.0 * c)          # expected cos(dec) vs measured 2cos(dec)
        assert gap > 0.20, (
            f"at dec {dec} the engine's own check would have passed on a 2:1 "
            f"mount, and this whole regrade would be unnecessary")


# ------------------------------------------------- the control: a real gap

def test_a_ratio_the_asymmetry_does_not_explain_is_still_a_warning():
    """THE OTHER HALF, and the one that makes this a fix rather than a mute.
    A mount whose measured ratio is wrong EVEN AFTER the asymmetry is taken
    into account has something actually wrong with it, and the warning has to
    survive - carrying both numbers, so the next reader does not have to
    reconstruct them."""
    dec = 34.4
    predicted = 2.0 * math.cos(math.radians(dec))          # 1.65
    level, text = regrade_rate_advisory(ADVISORY, _cal(dec, predicted + 0.9),
                                        AM5)
    assert level == "warning"
    assert "2.55" in text and "1.65" in text, text
    assert "backlash" in text, "the real cause must still be named"


def test_the_boundary_is_the_engines_own_tolerance():
    """Just inside stays quiet, just outside warns. Pinned because a regrade
    with its own private tolerance would disagree with the engine about what
    "unexpected" means."""
    dec = 34.4
    predicted = 2.0 * math.cos(math.radians(dec))
    assert regrade_rate_advisory(ADVISORY, _cal(dec, predicted + 0.19),
                                 AM5)[0] == "info"
    assert regrade_rate_advisory(ADVISORY, _cal(dec, predicted + 0.21),
                                 AM5)[0] == "warning"


# --------------------------------------- where the regrade must not reach

@pytest.mark.parametrize("rates, why", [
    (None, "the driver answered nothing"),
    (SYMMETRIC, "one rate on both axes: the engine compared the right things"),
    ((0.004178, 0.0), "a zero Dec rate is not a ratio"),
    ((float("nan"), 0.002089), "a rate that is not a number"),
])
def test_an_unregradable_mount_keeps_the_engines_warning(rates, why):
    """A ratio that cannot be computed is not evidence that the advisory is
    wrong. Every one of these passes the engine's sentence through verbatim,
    at warning."""
    level, text = regrade_rate_advisory(ADVISORY, _cal(34.4, 1.65), rates)
    assert (level, text) == ("warning", ADVISORY), why


@pytest.mark.parametrize("dec_rad", [997.0, math.radians(75.0)])
def test_a_declination_the_engine_would_not_judge_is_not_judged_here(dec_rad):
    """997.0 is the UNKNOWN_DECLINATION sentinel; 75 degrees is past
    DEC_COMP_LIMIT (60), where upstream declines to compare at all. The
    regrade declines in exactly the same two places."""
    cal = _cal(0.0, 1.65)
    cal["declination"] = dec_rad
    assert regrade_rate_advisory(ADVISORY, cal, AM5) == ("warning", ADVISORY)


def test_a_different_advisory_is_never_touched():
    """The orthogonality advisory is about angles, not rates, and #111 made it
    load-bearing: it now gates calibration REUSE. Regrading it away would put
    a 39.8-degree calibration back into service."""
    assert regrade_rate_advisory(ORTHO, _cal(34.4, 1.65), AM5) == ("warning",
                                                                   ORTHO)


def test_a_calibration_with_no_rates_keeps_the_warning():
    """An older wheel, or a persisted file from before the rates were dumped:
    no x_rate/y_rate means no measured ratio to re-judge."""
    cal = {"is_valid": True, "declination": math.radians(34.4),
           "y_angle_error": 0.0, "pier_side": "west", "binning": 1}
    assert regrade_rate_advisory(ADVISORY, cal, AM5) == ("warning", ADVISORY)


# ------------------------------------------- and what the operator is shown

def test_the_report_drops_the_regraded_advisory():
    """The panel and the night log read the same function, so they cannot
    disagree about whether this mount has a backlash problem."""
    dec = 34.4
    g = _guider_with_cal(_cal(dec, 2.0 * math.cos(math.radians(dec))),
                         [ADVISORY])
    g._axis_guide_rates = AM5
    assert g.calibration_report()["advisories"] == []


def test_the_report_keeps_a_real_one():
    dec = 34.4
    g = _guider_with_cal(_cal(dec, 2.0 * math.cos(math.radians(dec)) + 0.9),
                         [ADVISORY])
    g._axis_guide_rates = AM5
    shown = g.calibration_report()["advisories"]
    assert len(shown) == 1 and "backlash" in shown[0], shown


def test_a_guider_that_never_read_the_rates_shows_the_engines_words():
    """`_axis_guide_rates` is None until something reads the driver. The
    default has to be "say what the engine said", not "say nothing"."""
    dec = 34.4
    g = _guider_with_cal(_cal(dec, 2.0 * math.cos(math.radians(dec))),
                         [ADVISORY])
    assert g._axis_guide_rates is None, "premise: nothing has read the rates"
    assert g.calibration_report()["advisories"] == [ADVISORY]
