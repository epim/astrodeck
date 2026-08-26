"""The guide scope points somewhere else, and the offset has to survive a flip.

The operator can see the misalignment by eye and asked for it measured and
corrected automatically. The measurement is two plate solves; this file is the
arithmetic between them, tested without a sky.

THE PROPERTY THAT MATTERS is not "the number is about right" -- it is that the
SAME BOLTED GEOMETRY produces the SAME stored offset whatever the instrument's
orientation, because a German mount flips 180 degrees at the meridian and a
rotator turns independently. An offset stored as a raw RA/Dec pair is right
until the first flip and silently wrong after, and "silently wrong after the
flip" is a whole night of subtly miscentred frames.
"""
import math

import pytest

from astrodeck.align import (
    STALE_AFTER_DAYS,
    GuideOffset,
    aim_for,
    offset_from_solves,
    staleness,
)
from astrodeck.align.guide_offset import _offset_by, _sep_pa

NOW = 1_787_800_000.0


def test_sep_pa_recovers_a_known_displacement():
    """The primitive, both ways round. Build a point at a known separation and
    position angle, then measure it back."""
    for pa in (0.0, 45.0, 90.0, 180.0, 271.0, 359.0):
        for sep in (60.0, 600.0, 3600.0):
            ra2, dec2 = _offset_by(5.0, 20.0, sep, pa)
            got_sep, got_pa = _sep_pa(5.0, 20.0, ra2, dec2)
            assert got_sep == pytest.approx(sep, abs=1e-4), (sep, pa)
            assert got_pa == pytest.approx(pa % 360.0, abs=1e-6), (sep, pa)


def test_north_is_position_angle_zero():
    """Orientation is the thing a sign error hides in, so it is pinned alone.

    Due north of a point is a higher declination at the same RA; due east is a
    higher RA. Getting this backwards produces an offset that is exactly wrong
    in a way every round-trip test would still accept.
    """
    north_sep, north_pa = _sep_pa(5.0, 20.0, 5.0, 20.1)
    assert north_pa == pytest.approx(0.0, abs=1e-6)
    assert north_sep == pytest.approx(360.0, abs=0.5)

    _, east_pa = _sep_pa(5.0, 20.0, 5.01, 20.0)
    assert east_pa == pytest.approx(90.0, abs=0.2)


def test_the_same_geometry_measures_the_same_after_a_meridian_flip():
    """THE POINT OF THE WHOLE MODULE.

    One rigid guide scope, measured at two instrument orientations 180 degrees
    apart -- which is exactly what a German mount does at the meridian. The
    stored offset must be identical, because the hardware did not move.
    """
    # Before the flip: imaging frame at PA 10, guide centre 12 arcmin at sky PA 40.
    guide_ra, guide_dec = _offset_by(5.0, 20.0, 720.0, 40.0)
    before = offset_from_solves(
        main_ra_hours=5.0, main_dec_deg=20.0, main_pa_deg=10.0,
        guide_ra_hours=guide_ra, guide_dec_deg=guide_dec, measured_ts=NOW)

    # After: the instrument has rotated 180, so the same physical offset now
    # appears at sky PA 220 against an imaging frame at PA 190.
    guide_ra2, guide_dec2 = _offset_by(5.0, 20.0, 720.0, 220.0)
    after = offset_from_solves(
        main_ra_hours=5.0, main_dec_deg=20.0, main_pa_deg=190.0,
        guide_ra_hours=guide_ra2, guide_dec_deg=guide_dec2, measured_ts=NOW)

    assert before.sep_arcsec == pytest.approx(after.sep_arcsec, abs=1e-6)
    assert before.pa_deg == pytest.approx(after.pa_deg, abs=1e-6), (
        "an instrument-frame offset must not change when the instrument turns")
    # And the raw sky angles really did differ, or this test proves nothing.
    assert abs(before.measured_pa_deg - after.measured_pa_deg) == pytest.approx(180.0)


def test_aim_for_reproduces_the_measured_displacement():
    """Forward, closing on the same function that built the measurement.

    The first version asserted an INVERSE -- walk back from the guide centre to
    the OTA along pa+180 -- and missed by 0.5 arcsec at dec 20 and 13 arcsec at
    dec 84, because the back azimuth on a sphere is not the forward azimuth
    plus 180. There is no inverse now; every real use is forward.
    """
    main_ra, main_dec, main_pa = 5.0, 20.0, 10.0
    guide_ra, guide_dec = _offset_by(main_ra, main_dec, 720.0, 40.0)
    off = offset_from_solves(
        main_ra_hours=main_ra, main_dec_deg=main_dec, main_pa_deg=main_pa,
        guide_ra_hours=guide_ra, guide_dec_deg=guide_dec, measured_ts=NOW)
    aim = aim_for(ra_hours=main_ra, dec_deg=main_dec, offset=off,
                  current_pa_deg=main_pa)
    # 1e-9 deg is 3.6 microarcsec. The first draft used 1e-12 and went red on
    # 1.5e-12 of float noise through two trig round trips -- tightening past
    # the arithmetic's own conditioning, which tests the FPU and not the code.
    assert aim[0] == pytest.approx(guide_ra, abs=1e-9)
    assert aim[1] == pytest.approx(guide_dec, abs=1e-9)


def test_aim_for_follows_the_current_orientation():
    """A correction ignoring `current_pa_deg` would be exactly 180 degrees
    wrong for the second half of a night, and would still pass a round-trip
    taken at the measurement angle."""
    off = GuideOffset(sep_arcsec=720.0, pa_deg=30.0, measured_ts=NOW,
                      measured_pa_deg=0.0)
    a = aim_for(ra_hours=5.0, dec_deg=20.0, offset=off, current_pa_deg=0.0)
    b = aim_for(ra_hours=5.0, dec_deg=20.0, offset=off, current_pa_deg=180.0)
    sep, _ = _sep_pa(a[0], a[1], b[0], b[1])
    assert sep == pytest.approx(1440.0, abs=1.0), (
        "a 180 degree flip must place the aim point on the opposite side")


def test_a_far_northern_field_is_not_a_flat_plane():
    """cos(dec) is not a detail near the pole."""
    main_ra, main_dec = 2.0, 84.0
    guide_ra, guide_dec = _offset_by(main_ra, main_dec, 900.0, 115.0)
    off = offset_from_solves(
        main_ra_hours=main_ra, main_dec_deg=main_dec, main_pa_deg=0.0,
        guide_ra_hours=guide_ra, guide_dec_deg=guide_dec, measured_ts=NOW)
    aim = aim_for(ra_hours=main_ra, dec_deg=main_dec, offset=off,
                  current_pa_deg=0.0)
    assert aim[0] == pytest.approx(guide_ra, abs=1e-9)
    assert aim[1] == pytest.approx(guide_dec, abs=1e-9)
    # The RA displacement really is large up here, or this proves nothing.
    assert abs(guide_ra - main_ra) * 15.0 * 3600.0 > 900.0


def test_no_measurement_says_so_without_scolding():
    msg = staleness(None, now=NOW)
    assert msg is not None
    assert "no guide-scope offset" in msg


def test_a_fresh_offset_on_the_same_hardware_is_not_stale():
    off = GuideOffset(sep_arcsec=700.0, pa_deg=30.0, measured_ts=NOW,
                      measured_pa_deg=0.0, camera="main", guide_camera="guide")
    assert staleness(off, now=NOW + 3600.0, camera="main",
                     guide_camera="guide") is None


def test_a_different_camera_outranks_a_recent_measurement():
    """Five minutes old and wrong beats thirty days old and right."""
    off = GuideOffset(sep_arcsec=700.0, pa_deg=30.0, measured_ts=NOW,
                      measured_pa_deg=0.0, camera="main", guide_camera="guide")
    msg = staleness(off, now=NOW + 300.0, camera="main", guide_camera="other")
    assert msg is not None and "guide camera" in msg

    msg2 = staleness(off, now=NOW + 300.0, camera="swapped", guide_camera="guide")
    assert msg2 is not None and "imaging camera" in msg2


def test_age_warns_only_past_the_threshold():
    off = GuideOffset(sep_arcsec=700.0, pa_deg=30.0, measured_ts=NOW,
                      measured_pa_deg=0.0, camera="m", guide_camera="g")
    day = 86400.0
    inside = staleness(off, now=NOW + (STALE_AFTER_DAYS - 1) * day,
                       camera="m", guide_camera="g")
    outside = staleness(off, now=NOW + (STALE_AFTER_DAYS + 1) * day,
                        camera="m", guide_camera="g")
    assert inside is None
    assert outside is not None and "days ago" in outside


def test_staleness_never_refuses():
    """Recorded as a test because it is a decision, not an implementation
    detail: "a stale one should warn not block". Every branch returns a
    sentence or None -- there is no exception to raise and no False to check,
    so a caller CANNOT accidentally turn this into a gate."""
    off = GuideOffset(sep_arcsec=700.0, pa_deg=30.0, measured_ts=0.0,
                      measured_pa_deg=0.0, camera="a", guide_camera="b")
    for kwargs in ({}, {"camera": "z"}, {"guide_camera": "z"},
                   {"camera": "z", "guide_camera": "z"}):
        out = staleness(off, now=NOW, **kwargs)
        assert out is None or isinstance(out, str)
