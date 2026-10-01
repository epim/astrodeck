"""WP-37 (e) / #623: ``config.fov_deg`` must use the exact trig formula the
two UI mirrors use, not ``image_scale_arcsec_px``'s rounded ``ARCSEC_PER_RAD``
(206.265, not the true 206264.806.../1000).

Before the fix, #168 had already made ``ui/src/lib/framing.ts``'s
``fovFromOptics`` and ``ui/src/next/lib/fov.ts``'s ``fovDeg`` agree with EACH
OTHER exactly, by routing both through ``fovDegFromSensorMm``'s
``(sensor_mm / focal_mm) * (180 / pi)``. ``config.fov_deg`` kept its older
``image_scale_arcsec_px``-based formula, so the server and the UIs disagreed
by about 1.6e-6 degree at typical amateur rigs -- real, but far below
anything a plate solve or a mosaic tiling measures, which is how it survived
the W3 integration that fixed the UI-to-UI gap (test_w3_fov_reducer_consistency.py
pins the server against ITSELF, so it could not see this).

THE SAME FIXTURE every sibling test in this family uses (500mm, 3.76um,
6248x4176 APS-C): ``test_w3_fov_reducer_consistency.py`` (server),
``ui/src/next/lib/__tests__/w3FovOneFormula.test.ts`` and
``ui/src/lib/__tests__/framing.test.ts``'s "fovFromOptics sanity" (UI). This
file does not execute the TS mirrors -- it has no runtime to do that from --
so it pins the SAME formula applied to the SAME numbers by hand, which is
exactly what the UI tests above also do for their own side, at the same
1e-9 tolerance the UI's own cross-check (w3FovOneFormula.test.ts) uses. The
three therefore agree by construction (one formula, one fixture, checked
independently on each side) rather than by rounding to the same number of
digits.

Named mutant: revert ``fov_deg`` to the old formula (route through
``image_scale_arcsec_px`` and divide by 3600 instead of computing
``(sensor_mm / focal_mm) * (180 / pi)`` directly) --
``test_fov_deg_matches_the_exact_trig_formula`` goes red, observed verbatim:
    AssertionError: fov_deg's width does not match the exact trig formula at
    1e-9: 2.692042437333333 != 2.6920399085909925
``test_fov_deg_no_longer_agrees_with_the_rounded_constant`` goes red too
(the gap collapses to exactly 0.0, since the mutant IS that formula):
    AssertionError: fov_deg's width should differ from the rounded-constant
    formula by a small but real amount (#623 measured ~1.6e-6 deg on a
    different rig); got a gap of 0.0
"""
from __future__ import annotations

import math

from astrodeck.config import fov_deg

# Same rig as test_w3_fov_reducer_consistency.py / w3FovOneFormula.test.ts /
# framing.test.ts's "fovFromOptics sanity".
FOCAL_MM = 500.0
PIXEL_UM = 3.76
W_PX = 6248
H_PX = 4176


def _exact_fov_deg(focal_mm: float, pixel_um: float, w_px: int, h_px: int):
    """The UIs' formula, built by hand from the same primitives they start
    from (pixel pitch in microns * pixel count, converted to mm by /1000),
    so this is a second, independent computation of the same thing
    ``config.fov_deg`` must now return -- not a call into it."""
    rad_per_deg = 180.0 / math.pi
    sensor_w_mm = pixel_um * w_px / 1000.0
    sensor_h_mm = pixel_um * h_px / 1000.0
    fw = (sensor_w_mm / focal_mm) * rad_per_deg
    fh = (sensor_h_mm / focal_mm) * rad_per_deg
    return fw, fh, (fw * fw + fh * fh) ** 0.5


def test_fov_deg_matches_the_exact_trig_formula():
    want_w, want_h, want_diag = _exact_fov_deg(FOCAL_MM, PIXEL_UM, W_PX, H_PX)
    got_w, got_h, got_diag = fov_deg(FOCAL_MM, PIXEL_UM, W_PX, H_PX)
    assert abs(got_w - want_w) < 1e-9, (
        f"fov_deg's width does not match the exact trig formula at 1e-9: "
        f"{got_w!r} != {want_w!r}")
    assert abs(got_h - want_h) < 1e-9, (
        f"fov_deg's height does not match the exact trig formula at 1e-9: "
        f"{got_h!r} != {want_h!r}")
    assert abs(got_diag - want_diag) < 1e-9, (
        f"fov_deg's diagonal does not match the exact trig formula at 1e-9: "
        f"{got_diag!r} != {want_diag!r}")
    # Anchors to the family's known worked value (framing.test.ts's own
    # tolerance), same as test_w3_fov_reducer_consistency.py.
    assert abs(got_w - 2.69) < 0.05
    assert abs(got_h - 1.80) < 0.05


def test_fov_deg_no_longer_agrees_with_the_rounded_constant():
    """#623's gap, demonstrated the other way around: the OLD formula (through
    ``image_scale_arcsec_px``'s rounded ``ARCSEC_PER_RAD``) must now
    DISAGREE with ``fov_deg``'s answer, by the ~1.6e-6 deg #623 measured --
    small, but well clear of the 1e-9 the exact formula is pinned to above,
    so a regression back to the rounded constant cannot hide in float noise."""
    from astrodeck.config import ARCSEC_PER_RAD, image_scale_arcsec_px

    s = image_scale_arcsec_px(FOCAL_MM, PIXEL_UM, binning=1)
    old_w = s * W_PX / 3600.0
    got_w, _, _ = fov_deg(FOCAL_MM, PIXEL_UM, W_PX, H_PX)
    gap = abs(got_w - old_w)
    assert 1e-8 < gap < 1e-4, (
        f"fov_deg's width should differ from the rounded-constant formula by "
        f"a small but real amount (#623 measured ~1.6e-6 deg on a different "
        f"rig); got a gap of {gap!r}")
    # ARCSEC_PER_RAD stays the rounded constant: only fov_deg's own formula
    # changed, not the shared arcsec/px readout both UIs still round the
    # same way.
    assert ARCSEC_PER_RAD == 206.265


def test_fov_deg_still_zero_on_an_unset_focal_length():
    """The div-by-zero guard the old formula had (via
    ``image_scale_arcsec_px``) must survive the rewrite: zero, never NaN or
    an exception, so a rig with no focal length recorded still shows the
    dashed placeholder instead of crashing a route."""
    assert fov_deg(0.0, PIXEL_UM, W_PX, H_PX) == (0.0, 0.0, 0.0)
