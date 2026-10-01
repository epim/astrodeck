"""The mosaic angle check: a pure verdict on the sky angle a hop's centring
solve recorded, and the Appendix A.2 tolerance it is judged against (mosaic
spec 5.6 step 4, Appendix A.2, S1 item 11; U-04, #189).

``astrodeck/sequence/angle_check.py`` is not called by the engine in S1. What
is pinned here is the arithmetic the S2 group driver will lean on, each piece
against a named mutation of that module (observed failures recorded per test):

* the A.2 tolerance, to 0.01 deg, for every row of both A.2 tables, including
  the combined convergence budget ``k = 0.5 - c``;
* a zero, negative or non-finite budget is zero tolerance, and a budget no
  angle can spend saturates at 90 deg instead of raising a math domain error;
* the comparison is mod 180 (a centred rectangle turned 180 deg has the same
  footprint, ``rotation.py:26-30``), across the 0/180 wrap and on both sides of
  the planned angle;
* a record exposed before the hop started, or one with no finite PA or no
  exposure time, is ``no_measurement``, never a pass;
* NaN arguments raise instead of comparing false and passing every angle;
* the record the real ``sky_angle.note_solved_rotation`` writes is read
  correctly.

The controls are the cases where nothing should change: an angle inside the
tolerance, a record exposed exactly at the hop start, a tolerance of zero, an
overlap of zero.
"""
from __future__ import annotations

import math
import time
from types import SimpleNamespace

import pytest

from astrodeck import sky_angle
from astrodeck.sequence.angle_check import (
    AngleVerdict,
    angle_tolerance_deg,
    angle_verdict,
)
from astrodeck.solve.base import SolveResult

#: Any wall-clock instant will do; it only has to be the same one the records
#: are built against.
HOP_START = 1_790_000_000.0


def _rec(pa, *, exposed_at=HOP_START + 20.0, solved_at=None) -> dict:
    """A record in ``note_solved_rotation``'s shape (``sky_angle.py:263-275``).
    Only ``pa_deg`` and ``exposed_at`` are read; the rest is there so a reader
    that reached for the wrong key would find a plausible wrong value."""
    return {
        "pa_deg": pa,
        "exposed_at": exposed_at,
        "solved_at": (exposed_at + 4.0) if solved_at is None else solved_at,
        "source": "goto",
        "pier_side": "west",
        "camera": "imaging",
        "calibrated": False,
        "reason": "no rotator is connected",
        "mechanical_deg": None,
        "rotator_before_deg": None,
        "offset_deg": None,
    }


def _verdict(record, *, planned=30.0, since=HOP_START, tol=6.0) -> AngleVerdict:
    return angle_verdict(record, planned_pa_deg=planned, since_ts=since,
                         tolerance_deg=tol)


# ---------------------------------------------------------------------------
# angle_tolerance_deg: Appendix A.2
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("fov_x", "fov_y", "overlap", "k", "expected"),
    [
        # A.2, k = 0.5 (half the overlap)
        pytest.param(2.0, 1.33, 0.25, 0.5, 6.36, id="2.0x1.33 at 25%"),
        pytest.param(2.0, 1.33, 0.15, 0.5, 3.36, id="2.0x1.33 at 15%"),
        pytest.param(2.0, 1.33, 0.10, 0.5, 2.12, id="2.0x1.33 at 10%"),
        pytest.param(0.9, 0.6, 0.25, 0.5, 6.38, id="0.9x0.6 at 25%"),
        # A.2 combined with convergence, k = 0.5 - c (c from A.1)
        pytest.param(2.0, 1.33, 0.25, 0.5 - 0.013, 6.20, id="3x3 at 25% Dec 20"),
        pytest.param(2.0, 1.33, 0.25, 0.5 - 0.031, 5.97, id="3x3 at 25% Dec 41"),
        pytest.param(2.0, 1.33, 0.25, 0.5 - 0.062, 5.57, id="3x3 at 25% Dec 60"),
        pytest.param(2.0, 1.33, 0.25, 0.5 - 0.138, 4.60, id="3x3 at 25% Dec 75"),
        pytest.param(2.0, 1.33, 0.10, 0.5 - 0.091, 1.73, id="1x4 at 10% Dec 41"),
        pytest.param(2.0, 1.33, 0.10, 0.5 - 0.389, 0.47, id="1x4 at 10% Dec 75"),
    ],
)
def test_the_appendix_a2_tables(fov_x, fov_y, overlap, k, expected):
    """Every row of both A.2 tables, to 0.01 deg. The numbers were recomputed
    from ``sin(theta) = k * min(ov*fy/(fx*(1-ov)), ov*fx/(fy*(1-ov)))``
    before this test was written; the spec's table is the second source.

    MUTATION "step without (1 - overlap)" (both terms lose their ``(1 - ov)``
    divisor). Observed, all ten rows red, for example:
        FAILED ...[2.0x1.33 at 25%] - assert 4.7682136673591256 == 6.36 ± 0.01
        FAILED ...[1x4 at 10% Dec 75] - assert 0.422932637224612 == 0.47 ± 0.01

    MUTATION "k ignored" (the formula always uses 0.5). Observed: the four
    k = 0.5 rows stay green, which is the control, and all six combined rows
    go red, for example:
        FAILED ...[3x3 at 25% Dec 41] - assert 6.363355801228608 == 5.97 ± 0.01
        FAILED ...[1x4 at 10% Dec 75] - assert 2.1172425640746666 == 0.47 ± 0.01
    """
    assert angle_tolerance_deg(fov_x, fov_y, overlap, k) == pytest.approx(
        expected, abs=0.01)


def test_k_defaults_to_half_the_overlap():
    """The default budget is A.2's k = 0.5.

    MUTATION "default k = 1.0". Observed:
        assert 12.806943224507252 == 6.36 ± 0.01
    """
    assert angle_tolerance_deg(2.0, 1.33, 0.25) == pytest.approx(6.36, abs=0.01)


def test_a_portrait_panel_gets_the_same_tolerance():
    """The binding neighbour is the one across the SHORT side, whichever axis
    that is, so turning the panel 90 deg (1.33 x 2.0) must not change the
    answer. The landscape rows above cannot tell the two terms of the ``min``
    apart, because for them the first term is always the smaller.

    MUTATION "only the first term of the min". Observed:
        assert 14.514592245567865 == 6.36 ± 0.01
    """
    landscape = angle_tolerance_deg(2.0, 1.33, 0.25)
    portrait = angle_tolerance_deg(1.33, 2.0, 0.25)
    assert portrait == pytest.approx(6.36, abs=0.01)
    assert portrait == pytest.approx(landscape, abs=1e-12)


@pytest.mark.parametrize("k", [0.0, -0.1, -0.5, math.nan],
                         ids=["zero", "convergence past half", "all spent", "nan"])
def test_a_spent_budget_is_zero_tolerance(k):
    """``k = 0.5 - c`` goes to zero or below once convergence alone uses half
    the overlap (M15). That is zero tolerance, never a negative number an
    ``error > tolerance`` check would read as "always off" by accident. A NaN
    budget is zero too: unguarded, ``min(1.0, nan)`` is 1.0 and it comes back
    as 90 deg, the widest tolerance there is.

    MUTATION "drop the k guard". Observed:
        FAILED ...[convergence past half] - assert -1.2701604782688998 == 0.0
        FAILED ...[all spent] - assert -6.363355801228608 == 0.0
        FAILED ...[nan] - assert 90.0 == 0.0
    (the "zero" case stays green: asin(0) is already 0, which is why the
    guard is pinned on the negative and NaN cases.)
    """
    assert angle_tolerance_deg(2.0, 1.33, 0.25, k) == 0.0


@pytest.mark.parametrize("overlap", [0.25, 0.0], ids=["25%", "no overlap"])
def test_an_infinite_budget_is_zero_tolerance(overlap):
    """``k = 0.5 - c`` is infinite only when the convergence arithmetic has
    gone wrong upstream (``c = -inf``). The ``no overlap`` case is the one that
    matters: ``inf * 0`` is NaN, ``min(1.0, nan)`` is 1.0, and a layout with no
    overlap at all came back with the widest tolerance there is, 90 deg. Zero
    is the answer that cannot lay tiles blind, as for a NaN budget.

    MUTATION "k guard lets inf through" (``if not k > 0.0``, the guard as first
    written). Observed:
        FAILED ...[25%] - assert 90.0 == 0.0
        FAILED ...[no overlap] - assert 90.0 == 0.0
    """
    assert angle_tolerance_deg(2.0, 1.33, overlap, math.inf) == 0.0


def test_a_budget_no_angle_can_spend_saturates_at_90():
    """At 90% overlap on a square panel, k = 0.5 asks for more shift than any
    turn can produce (sin would be 4.5). Mod 180 the largest possible angle
    error is 90 deg, so that is the tolerance, not a math domain error.

    MUTATION "no clamp on sin". Observed:
        ValueError: math domain error
    """
    assert angle_tolerance_deg(1.0, 1.0, 0.9) == pytest.approx(90.0, abs=1e-9)
    # Control: exactly at the edge is 90, and just under it is not clamped. A
    # square panel at 50% overlap makes sin equal k exactly in binary floating
    # point (0.5 / 0.5), so k = 1.0 is sin = 1.0 with no rounding, where the
    # 2/3-overlap route to the same edge lands one ulp under it.
    assert angle_tolerance_deg(1.0, 1.0, 0.5, 1.0) == 90.0
    assert angle_tolerance_deg(1.0, 1.0, 0.5, 0.999) == pytest.approx(
        87.437, abs=0.001)


@pytest.mark.parametrize("overlap", [25.0, 1.0, 1.5, -0.1],
                         ids=["a percent", "one", "above one", "negative"])
def test_an_overlap_outside_the_formula_domain_raises(overlap):
    """Overlap is a fraction in [0, 1) (``framing.py:62`` validates [0, 0.5]).
    25 meaning 25% would otherwise come back as a confident negative angle.

    MUTATION "drop the overlap domain check". Observed:
        FAILED ...[a percent] - Failed: DID NOT RAISE <class 'ValueError'>
        FAILED ...[one] - ZeroDivisionError: float division by zero
        FAILED ...[above one] - AssertionError: Regex pattern did not match.
        FAILED ...[negative] - Failed: DID NOT RAISE <class 'ValueError'>
    ("above one" does raise a ValueError, math.asin's own domain error on
    sin = -2.26, which the ``match`` on "overlap" refuses to count.)
    """
    with pytest.raises(ValueError, match="overlap"):
        angle_tolerance_deg(2.0, 1.33, overlap)


def test_the_overlap_domain_edges_are_accepted():
    """Control for the domain check: no overlap is a legal layout with no angle
    tolerance at all, and framing's own upper bound works."""
    assert angle_tolerance_deg(2.0, 1.33, 0.0) == 0.0
    assert 0.0 < angle_tolerance_deg(2.0, 1.33, 0.5) < 90.0


@pytest.mark.parametrize(("fov_x", "fov_y"),
                         [(0.0, 1.33), (2.0, -1.33)],
                         ids=["zero width", "negative height"])
def test_a_non_positive_field_raises(fov_x, fov_y):
    """MUTATION "drop the fov check". Observed:
        FAILED ...[zero width] - ZeroDivisionError: float division by zero
        FAILED ...[negative height] - Failed: DID NOT RAISE <class 'ValueError'>
    """
    with pytest.raises(ValueError, match="fov"):
        angle_tolerance_deg(fov_x, fov_y, 0.25)


@pytest.mark.parametrize(("fov_x", "fov_y"),
                         [(math.nan, 1.33), (2.0, math.inf)],
                         ids=["nan width", "infinite height"])
def test_a_non_finite_field_raises(fov_x, fov_y):
    """A NaN field is the dangerous one: ``nan <= 0`` is false, so it slips
    past the sign check, the formula yields NaN, and ``min(1.0, nan)`` is 1.0,
    so it would come back as the widest possible tolerance.

    MUTATION "fov read with float() instead of the finite check". Observed:
        FAILED ...[nan width] - Failed: DID NOT RAISE <class 'ValueError'>
        FAILED ...[infinite height] - Failed: DID NOT RAISE <class 'ValueError'>
    """
    with pytest.raises(ValueError, match="fov"):
        angle_tolerance_deg(fov_x, fov_y, 0.25)


# ---------------------------------------------------------------------------
# angle_verdict
# ---------------------------------------------------------------------------

def test_an_angle_beyond_the_tolerance_is_off():
    """The spec's own example: planned 30, the camera reads 37.2, tolerance 6.0.
    The reason names both numbers, as 5.6's alert must.

    MUTATION "inverted comparison" (``error < tolerance`` is off). Observed:
        AssertionError: assert 'ok' == 'off'
    """
    v = _verdict(_rec(37.2))
    assert v.kind == "off"
    assert v.error_deg == pytest.approx(7.2, abs=0.01)
    assert v.measured_deg == pytest.approx(37.2)
    assert "37.2" in v.reason and "30.0" in v.reason and "6.0" in v.reason
    # Control: inside the tolerance on the same side.
    ok = _verdict(_rec(35.9))
    assert ok.kind == "ok"
    assert ok.error_deg == pytest.approx(5.9, abs=0.01)


def test_an_error_equal_to_the_tolerance_is_ok():
    """5.6 acts "beyond" the tolerance, so an error exactly at it passes. 36.0
    minus 30.0 is exactly 6.0 in binary floating point, so this is a real
    boundary, not a rounding accident.

    MUTATION ">= instead of >". Observed:
        AssertionError: assert 'off' == 'ok'
    """
    v = _verdict(_rec(36.0))
    assert v.error_deg == 6.0
    assert v.kind == "ok"


def test_the_180_degree_twin_is_the_same_footprint():
    """PA 210.4 is PA 30.4 turned half a turn: a centred rectangle covers the
    same sky either way (``rotation.py:26-30``). The reason says so, since
    "reads PA 210.4, 0.4 deg from 30.0" would otherwise read as a typo.

    MUTATION "compare mod 360". Observed:
        assert 179.6 == 0.4 ± 0.01

    MUTATION "drop the twin note". Observed:
        AssertionError: assert '30.4' in 'the camera reads PA 210.4 and this
        mosaic is laid out at 30.0: 0.4 deg apart, within the 6.0 deg tolerance'

    MUTATION "twin note always" (the note on every reading). Survived the
    first version of this test, which had no control; observed with it:
        AssertionError: assert 'turned 180' not in 'the camera ...eg tolerance'
          'turned 180' is contained here:
            PA 210.4, turned 180 deg) and this mosaic is laid out at 30.0: 0.4
            deg apart, within the 6.0 deg tolerance
    """
    v = _verdict(_rec(210.4))
    assert v.error_deg == pytest.approx(0.4, abs=0.01)
    assert v.kind == "ok"
    assert v.measured_deg == pytest.approx(210.4)
    assert "30.4" in v.reason
    # Control: a reading on the planned angle's own side names no twin. There
    # the note is only noise the operator has to read past.
    near = _verdict(_rec(30.4))
    assert near.kind == "ok"
    assert "turned 180" not in near.reason


def test_the_comparison_wraps_at_180():
    """Planned 179.5 and measured 0.3 are 0.8 deg apart across the wrap.

    MUTATION "plain abs difference". Observed:
        assert 179.2 == 0.8 ± 0.01
    (and on the twin case above: assert 180.4 == 0.4 ± 0.01)
    """
    v = _verdict(_rec(0.3), planned=179.5)
    assert v.error_deg == pytest.approx(0.8, abs=0.01)
    assert v.kind == "ok"


def test_an_angle_below_the_planned_one_folds_to_the_short_side():
    """(29.6 - 30) mod 180 is 179.6; the error is the short way round, 0.4.
    The wrap and twin cases above do not catch a fold that forgets the short
    side, because both of them land on the short side already.

    MUTATION "one-sided fold" (``(m - p) % 180`` with no ``min``). Observed:
        assert 179.6 == 0.4 ± 0.01
    """
    v = _verdict(_rec(29.6))
    assert v.error_deg == pytest.approx(0.4, abs=0.01)
    assert v.kind == "ok"
    off = _verdict(_rec(22.8))
    assert off.error_deg == pytest.approx(7.2, abs=0.01)
    assert off.kind == "off"


def test_an_exact_match_is_ok_even_at_zero_tolerance():
    """Control: a tolerance of 0 (M15, the budget is spent) is legal, and only
    an angle that is off at all fails it."""
    assert _verdict(_rec(30.0), tol=0.0).kind == "ok"
    assert _verdict(_rec(30.5), tol=0.0).kind == "off"


def test_a_record_exposed_before_the_hop_is_no_measurement():
    """The newest record reads PA 30.0, a perfect match, but its frame was
    exposed 30 s before this hop started: it describes the last panel, not this
    one. Its solve finished after the hop started, so ``solved_at`` is the
    wrong clock to judge it by. The record's angle is withheld from the verdict
    so a stale number cannot be mistaken for a measurement.

    MUTATION "ignore exposed_at" (no freshness check). Observed:
        AssertionError: assert 'ok' == 'no_measurement'

    MUTATION "freshness from solved_at". Observed:
        AssertionError: assert 'ok' == 'no_measurement'

    MUTATION "stale reason omits the age". Observed:
        AssertionError: assert 'exposed 30.0 s before this hop' in 'the newest
        sky angle (PA 30.0) was exposed before this hop started, so this hop
        measured no angle'

    MUTATION "age sign inverted" (``exposed - since``). Survived the first
    version of this test, which asserted "30.0 s" alone; observed with the
    whole phrase:
        AssertionError: assert 'exposed 30.0 s before this hop' in 'the newest
        sky angle (PA 30.0) was exposed -30.0 s before this hop started, so
        this hop measured no angle'
    """
    v = _verdict(_rec(30.0, exposed_at=HOP_START - 30.0,
                      solved_at=HOP_START + 5.0))
    assert v.kind == "no_measurement"
    assert v.measured_deg is None and v.error_deg is None
    # The whole phrase, not "30.0 s" alone, which "-30.0 s" also contains.
    assert "exposed 30.0 s before this hop" in v.reason


def test_a_record_exposed_at_the_hop_start_counts():
    """5.6: ``exposed_at`` must be AT or after the hop start.

    MUTATION "strictly after" (``exposed_at <= since_ts`` is stale). Observed:
        AssertionError: assert 'no_measurement' == 'ok'
    """
    assert _verdict(_rec(30.4, exposed_at=HOP_START)).kind == "ok"
    # Control: after the start is fresh under either reading.
    assert _verdict(_rec(30.4, exposed_at=HOP_START + 0.001)).kind == "ok"


def test_no_record_is_no_measurement():
    """``hub.last_sky_angle`` is ``None`` until an imaging solve records one.

    MUTATION "drop the None check". Observed:
        AttributeError: 'NoneType' object has no attribute 'get'
    """
    v = _verdict(None)
    assert v.kind == "no_measurement"
    assert v.measured_deg is None and v.error_deg is None
    assert v.reason


@pytest.mark.parametrize("pa", [math.nan, math.inf, None, "n/a"],
                         ids=["nan", "inf", "none", "text"])
def test_a_record_with_no_finite_pa_is_no_measurement(pa):
    """NaN is the case that matters: its error is NaN, ``nan > tolerance`` is
    false, and the verdict would be a pass.

    MUTATION "drop the finiteness check" (``float()`` alone). Observed:
        FAILED ...[nan] - AssertionError: assert 'ok' == 'no_measurement'
        FAILED ...[inf] - AssertionError: assert 'ok' == 'no_measurement'
    (``None`` and text still fail ``float()`` and stay green.)
    """
    v = _verdict(_rec(pa))
    assert v.kind == "no_measurement"
    assert v.measured_deg is None and v.error_deg is None


@pytest.mark.parametrize("key", ["pa_deg", "exposed_at"])
def test_a_record_missing_a_field_is_no_measurement(key):
    """A record from a stand-in hub, or an older build, may lack a field.
    That is a missing measurement, not a crash in the hop.

    MUTATION "index pa_deg instead of .get". Observed:
        FAILED ...[pa_deg] - KeyError: 'pa_deg'

    MUTATION "index exposed_at instead of .get". Observed:
        FAILED ...[exposed_at] - KeyError: 'exposed_at'
    """
    rec = _rec(30.0)
    del rec[key]
    v = _verdict(rec)
    assert v.kind == "no_measurement"
    assert v.measured_deg is None and v.error_deg is None


@pytest.mark.parametrize("exposed_at", [None, math.nan, "later"],
                         ids=["none", "nan", "text"])
def test_a_record_with_no_exposure_time_is_no_measurement(exposed_at):
    """Nothing shows such a record came from this hop, so it cannot count.

    MUTATION "a missing exposure time counts as fresh". Observed:
        FAILED ...[none] - AssertionError: assert 'ok' == 'no_measurement'
        FAILED ...[nan] - AssertionError: assert 'ok' == 'no_measurement'
        FAILED ...[text] - AssertionError: assert 'ok' == 'no_measurement'
    """
    v = _verdict(_rec(30.0, exposed_at=exposed_at, solved_at=HOP_START + 5.0))
    assert v.kind == "no_measurement"
    assert v.measured_deg is None and v.error_deg is None


class TestArgumentsThatWouldPassEverything:
    """Every comparison with NaN is false. A NaN tolerance or planned angle
    would make ``error > tolerance`` false for every angle, and a NaN hop start
    would make ``exposed_at < since_ts`` false for every record, so each would
    silently pass whatever the camera read. These are caller bugs, and they
    raise."""

    def test_a_nan_tolerance_raises(self):
        """MUTATION "tolerance read with float()". Observed:
            Failed: DID NOT RAISE <class 'ValueError'>
        """
        with pytest.raises(ValueError, match="tolerance_deg"):
            _verdict(_rec(37.2), tol=math.nan)

    def test_a_negative_tolerance_raises(self):
        """MUTATION "no negative-tolerance check". Observed:
            Failed: DID NOT RAISE <class 'ValueError'>
        """
        with pytest.raises(ValueError, match="tolerance_deg"):
            _verdict(_rec(30.0), tol=-1.0)

    def test_a_nan_planned_angle_raises(self):
        """MUTATION "planned angle read with float()". Observed:
            Failed: DID NOT RAISE <class 'ValueError'>
        """
        with pytest.raises(ValueError, match="planned_pa_deg"):
            _verdict(_rec(37.2), planned=math.nan)

    def test_a_nan_hop_start_raises(self):
        """MUTATION "hop start read with float()". Observed:
            Failed: DID NOT RAISE <class 'ValueError'>
        """
        with pytest.raises(ValueError, match="since_ts"):
            _verdict(_rec(30.0, exposed_at=HOP_START - 30.0), since=math.nan)

    def test_a_none_planned_angle_raises(self):
        """``TargetGroup.pa_deg`` is ``None`` when no angle was planned, and
        then the check is not to be called at all (``angle_tolerance_deg =
        None`` disables it). Calling it anyway is refused, not passed.

        MUTATION "planned angle read with float()". Observed:
            TypeError: float() argument must be a string or a real number,
            not 'NoneType'
        (a crash, not a pass, but not the ValueError the engine will catch.)
        """
        with pytest.raises(ValueError, match="planned_pa_deg"):
            _verdict(_rec(30.0), planned=None)


async def test_reads_the_record_note_solved_rotation_writes():
    """The helper against the real producer, not a hand-built dict: a stand-in
    hub with an imaging camera and no rotator, and a record written by
    ``sky_angle.note_solved_rotation`` itself.

    Re-pinned for WP-30a (#292): ``note_solved_rotation`` now keeps the
    record with the newest ``exposed_at`` on ``hub.last_sky_angle``, so a
    second call with an older ``context`` no longer overwrites a newer held
    record (that guard has its own tests in test_h4_solve_frame_unique_names
    and test_polar_solve_settings). Calling it a second time here with a
    stale context would therefore leave ``hub.last_sky_angle`` unchanged
    (still "fresh") and never reach the ``no_measurement`` branch this test
    means to exercise. Build the stale record directly instead, in the same
    shape ``note_solved_rotation`` writes, and assign it to
    ``hub.last_sky_angle`` by hand -- this test's job is ``angle_verdict``'s
    reading of that shape, not the overwrite guard.

    MUTATION "freshness from solved_at". Observed:
        AssertionError: assert 'ok' == 'no_measurement'

    The same test also goes red under "ignore exposed_at" (the same line),
    and under "compare mod 360", "plain abs difference" and "inverted
    comparison" (AssertionError: assert 'off' == 'ok', on the fresh record).
    """
    cam = SimpleNamespace(name="imaging")
    hub = SimpleNamespace(devices={"camera": cam}, last_sky_angle=None)
    hop_start = time.time() - 60.0
    solve = SolveResult(success=True, rotation_deg=210.4)

    fresh = sky_angle.ExposureAngle(at=hop_start + 10.0, camera=cam,
                                    rotator=None, mech_deg=None, moving=None,
                                    pier_side=None)
    rec = await sky_angle.note_solved_rotation(hub, solve, source="goto",
                                               context=fresh)
    assert rec is not None and hub.last_sky_angle is rec
    v = angle_verdict(hub.last_sky_angle, planned_pa_deg=30.0,
                      since_ts=hop_start, tolerance_deg=6.0)
    assert v.kind == "ok"
    assert v.measured_deg == pytest.approx(210.4)
    assert v.error_deg == pytest.approx(0.4, abs=0.01)

    # A record in the same shape note_solved_rotation writes, but for an
    # exposure that predates the hop -- built directly rather than via a
    # second call, which the newest-exposed_at guard would correctly refuse
    # to let overwrite the fresh record above.
    stale_rec = dict(rec)
    stale_rec.update(pa_deg=sky_angle.mod360(solve.rotation_deg),
                      exposed_at=hop_start - 30.0,
                      solved_at=time.time())
    # Precondition: the stale frame's solve really does land after the hop
    # started, so a solved_at reader would take it.
    assert stale_rec["solved_at"] >= hop_start
    v = angle_verdict(stale_rec, planned_pa_deg=30.0,
                      since_ts=hop_start, tolerance_deg=6.0)
    assert v.kind == "no_measurement"
