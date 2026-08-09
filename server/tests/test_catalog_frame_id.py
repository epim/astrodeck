"""Naming the field a camera frame is pointed at (#182), server side.

Three layers, and each one is here because a wrong answer at that layer looks
completely fine at the layer above:

  * ``catalog.framing.project`` -- the forward gnomonic, pinned to the
    TypeScript mirror by GOLDEN VECTORS printed from ``ui/src/lib/framing.ts``
    itself. A projection that is subtly wrong still places markers, just not on
    the objects they name.
  * ``catalog.region.WcsPlate`` / ``objects_in_frame`` -- placing catalogued
    objects on a solved plate. A sign flip here draws a mirror image of the sky
    and nothing anywhere reports it.
  * ``catalog.region.identify_field`` -- which single object NAMES the field,
    and whether the app is confident enough to write that name into a file.

The pixel/path/counter invariants that depend on all three live in
``test_field_identification.py``.
"""
from __future__ import annotations

import math

import pytest

from astrodeck.catalog.framing import deproject, project
from astrodeck.catalog.region import (
    WcsPlate,
    frame_geometry,
    identify_field,
    objects_in_frame,
)
from astrodeck.solve.base import WcsSolution


# --------------------------------------------------------------------- helpers

def _plate(ra_deg: float = 83.822, dec_deg: float = -5.391,
           scale_deg: float = 2.78e-4, w: int = 1000, h: int = 800,
           rot_deg: float = 0.0) -> WcsSolution:
    """A valid TAN plate centred on (ra, dec) for a w x h frame.

    CRPIX is the FITS 1-based centre of the array, i.e. array index (w-1)/2 plus
    one -- writing ``w/2`` instead is the half-pixel error that makes every
    marker look "nearly right".
    """
    r = math.radians(rot_deg)
    # RA increases to the LEFT on a normal sky image, hence the negative cd11.
    cd11 = -scale_deg * math.cos(r)
    cd12 = scale_deg * math.sin(r)
    cd21 = scale_deg * math.sin(r)
    cd22 = scale_deg * math.cos(r)
    return WcsSolution(crval1=ra_deg, crval2=dec_deg,
                       crpix1=(w - 1) / 2.0 + 1.0, crpix2=(h - 1) / 2.0 + 1.0,
                       cd11=cd11, cd12=cd12, cd21=cd21, cd22=cd22)


# ============================================================ the forward gnomonic
#
# GOLDEN VECTORS, printed from ui/src/lib/framing.ts's own `project` at full
# double precision and pasted here verbatim. This is the lock-step guarantee:
# the Atlas draws with the TypeScript one and the frame overlay places with the
# Python one, and the two must not be able to drift a pixel apart. Copy the same
# four rows into ui/src/lib/__tests__/framing.test.ts if that side ever grows a
# golden-vector case of its own.

_GOLDEN = [
    # (ra_hours, dec_deg, ra0_hours, dec0_deg, xi_deg, eta_deg)
    (12.05, 20.3, 12.0, 20.0, 0.70345937090164001, 0.30160012341664172),
    # ACROSS THE RA WRAP: tangent point at 23.95h, object at 0.05h. A projection
    # that subtracted hours before converting would put this object 24 hours
    # away and off every frame.
    (0.05, 41.27, 23.95, 41.0, 1.1275173155000049, 0.27973793070782355),
    # NEAR THE POLE, where lines of RA converge and a naive box test is
    # meaningless.
    (5.5, -89.0, 5.0, -88.5, 0.13052504570300855, -0.50856677899440861),
    # 12 HOURS AWAY -- 90 degrees-ish from the tangent point, where h is small
    # and the guard decides whether the answer is huge-but-finite or NaN.
    (18.0, 66.0, 6.0, 66.0, 4.2651655123876357e-15, 63.633409774123315),
]


@pytest.mark.parametrize("ra,dec,ra0,dec0,xi,eta", _GOLDEN)
def test_project_matches_the_typescript_mirror(ra, dec, ra0, dec0, xi, eta):
    px, peta = project(ra, dec, ra0, dec0)
    assert px == pytest.approx(xi, rel=1e-12, abs=1e-15)
    assert peta == pytest.approx(eta, rel=1e-12, abs=1e-15)


def test_project_is_the_inverse_of_deproject():
    """The two halves of the one projection this package owns must compose to
    identity, or a marker placed by one and read back by the other walks."""
    for ra0, dec0 in [(12.0, 20.0), (0.0, -35.0), (6.0, 88.0)]:
        for dra, ddec in [(0.0, 0.0), (0.05, 0.3), (-0.2, -1.1)]:
            ra, dec = (ra0 + dra) % 24.0, max(-89.9, min(89.9, dec0 + ddec))
            xi, eta = project(ra, dec, ra0, dec0)
            back_ra, back_dec = deproject(xi, eta, ra0, dec0)
            assert back_ra == pytest.approx(ra, abs=1e-9)
            assert back_dec == pytest.approx(dec, abs=1e-9)


def test_project_behind_the_tangent_point_is_finite_not_nan():
    """h -> 0 at 90 degrees and goes negative behind; the guard must yield a
    huge FINITE offset the caller clips, never a NaN that silently compares
    false against every frame bound and disappears."""
    xi, eta = project(0.0, 0.0, 12.0, 0.0)     # exactly opposite
    assert math.isfinite(xi) and math.isfinite(eta)


# ================================================================ world <-> pixel

def test_a_known_sky_position_lands_on_a_known_pixel():
    """The tangent point must land on CRPIX, to well under a pixel -- including
    the FITS-1-based to numpy-0-based offset."""
    w, h = 1000, 800
    wcs = _plate(w=w, h=h)
    plate = WcsPlate(wcs)
    x, y = plate.to_pixel(wcs.crval1 / 15.0, wcs.crval2)
    assert x == pytest.approx((w - 1) / 2.0, abs=1e-6)
    assert y == pytest.approx((h - 1) / 2.0, abs=1e-6)
    # ...and one pixel east of centre is one pixel from centre, not two or none.
    ra, dec = plate.to_sky((w - 1) / 2.0 + 1.0, (h - 1) / 2.0)
    bx, by = plate.to_pixel(ra, dec)
    assert bx == pytest.approx((w - 1) / 2.0 + 1.0, abs=1e-6)
    assert by == pytest.approx((h - 1) / 2.0, abs=1e-6)


def test_pixel_round_trip_holds_across_the_whole_frame_and_a_rotation():
    w, h = 1000, 800
    plate = WcsPlate(_plate(w=w, h=h, rot_deg=37.0))
    for x, y in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (321, 654)]:
        ra, dec = plate.to_sky(x, y)
        bx, by = plate.to_pixel(ra, dec)
        assert bx == pytest.approx(x, abs=1e-6)
        assert by == pytest.approx(y, abs=1e-6)


def test_a_cdelt_crota_plate_with_no_cd_matrix_still_places_objects():
    """``WcsSolution`` declares BOTH forms and ``fitsio._apply_wcs`` writes
    whichever the solver produced, so a plate that never carried a CD matrix
    must place objects rather than silently drawing an empty overlay."""
    wcs = WcsSolution(crval1=83.822, crval2=-5.391, crpix1=500.5, crpix2=400.5,
                      cdelt1=-2.78e-4, cdelt2=2.78e-4, crota2=0.0)
    plate = WcsPlate(wcs)
    x, y = plate.to_pixel(83.822 / 15.0, -5.391)
    assert x == pytest.approx(499.5, abs=1e-6)
    assert y == pytest.approx(399.5, abs=1e-6)


def test_a_scaleless_wcs_is_refused_rather_than_read_as_one_degree_per_pixel():
    """astropy reads a CD-less, CDELT-less header back as a silent 1 deg/px
    plate. That is the single most dangerous wrong answer available here -- it
    would place every catalogued object in the sky inside a 30-arcminute frame
    -- so the plate refuses to exist instead."""
    with pytest.raises(ValueError, match="no scale"):
        WcsPlate(WcsSolution(crval1=10.0, crval2=10.0, crpix1=1.0, crpix2=1.0))


def test_a_singular_cd_matrix_is_refused():
    with pytest.raises(ValueError, match="singular"):
        WcsPlate(WcsSolution(crval1=10.0, crval2=10.0, crpix1=1.0, crpix2=1.0,
                             cd11=1e-4, cd12=1e-4, cd21=1e-4, cd22=1e-4))


def test_frame_geometry_measures_the_circumscribing_circle_from_the_corners():
    w, h = 1000, 800
    scale = 2.78e-4
    g = frame_geometry(_plate(scale_deg=scale, w=w, h=h), w, h)
    assert g["fov_w_deg"] == pytest.approx(w * scale, rel=1e-9)
    assert g["fov_h_deg"] == pytest.approx(h * scale, rel=1e-9)
    half_diag = 0.5 * math.hypot((w - 1) * scale, (h - 1) * scale)
    # Generous rather than exact is the correct error: a cone too small drops
    # real objects out of a corner and nothing reports it.
    assert g["radius_deg"] == pytest.approx(half_diag, rel=1e-3)


# ================================================================ what is in it

def test_m42_is_found_and_placed_at_the_centre_of_a_frame_pointed_at_m42():
    w, h = 1000, 800
    res = objects_in_frame(_plate(w=w, h=h), w, h, kinds=("dso",))
    ids = [o["id"] for o in res["objects"]]
    assert "M42" in ids, f"M42 not in a frame centred on M42: {ids[:8]}"
    m42 = next(o for o in res["objects"] if o["id"] == "M42")
    assert m42["x"] == pytest.approx((w - 1) / 2.0, abs=6)
    assert m42["y"] == pytest.approx((h - 1) / 2.0, abs=6)
    assert m42["inside"] is True
    # size_px is the catalogued extent through THIS plate, not a fixed glyph.
    assert m42["size_px"] > 100


def test_an_object_outside_the_frame_is_excluded():
    """A 0.28 x 0.22 degree frame on M42 does not contain M31, and a query that
    returned it would be a cone-radius bug dressed as a catalogue hit."""
    w, h = 1000, 800
    res = objects_in_frame(_plate(w=w, h=h), w, h, kinds=("dso",))
    assert "M31" not in [o["id"] for o in res["objects"]]


def test_objects_carry_no_altitude_or_azimuth():
    """Every row is f(site, target) the moment it carries alt/az, and this
    payload rides the preview websocket, which is broadcast. The Atlas route has
    the same guard for the same reason."""
    w, h = 1000, 800
    res = objects_in_frame(_plate(w=w, h=h), w, h)
    for row in res["objects"]:
        assert not any(k in row for k in ("alt_deg", "az_deg", "altitude", "azimuth")), row


def test_the_moon_is_withheld_without_the_site_capability():
    """Lunar parallax reaches about a degree, so the Moon's apparent position IS
    a location oracle -- and a frame overlay that published it beside a WCS
    would be a sharper one than the search box this repo already fixed."""
    # A frame centred on wherever the Moon is right now, so it is unambiguously
    # inside the circle and can only be missing because it was withheld.
    from astrodeck.catalog import solar_system as ss

    try:
        moon = ss.row("moon", None)
    except ss.EphemerisUnavailable:                     # pragma: no cover
        pytest.skip("no ephemeris available on this box")
    wcs = _plate(ra_deg=moon["ra_hours"] * 15.0, dec_deg=moon["dec_deg"],
                 scale_deg=5e-3, w=400, h=400)
    res = objects_in_frame(wcs, 400, 400, site_derived=False)
    assert "Moon" not in [o["id"] for o in res["objects"]], (
        "the Moon was published in a frame block without view.site_derived. "
        "Its apparent RA/Dec is f(lat, lon) to about a degree, and this payload "
        "rides the broadcast preview event beside a WCS — which makes it a "
        "sharper location oracle than the search box this repo already fixed")
    assert any("Moon" in n for n in res["notes"]), \
        "the Moon was dropped without saying so, which reads as an empty sky"


def test_the_moon_note_is_absent_from_a_frame_the_moon_is_not_in():
    """``region_rows`` raises its withholding note as soon as a site-derived body
    EXISTS, which is right for a whole-sky map and wrong for a half-degree frame
    -- it would put "the Moon is not marked" under every single exposure and
    imply the Moon was in it. A note that fires on every frame is a note nobody
    reads on the one frame it means something."""
    w, h = 1000, 800
    res = objects_in_frame(_plate(w=w, h=h), w, h)      # 0.28 deg on M42
    assert not any("Moon" in n for n in res["notes"]), res["notes"]


# ================================================================ identification

def _row(oid: str, sep_deg: float, *, inside: bool = True, label: str | None = None):
    return {
        "id": oid, "label": label or oid, "kind": "dso", "type": "Galaxy",
        "describe": f"{oid} — galaxy", "sep_deg": sep_deg, "inside": inside,
    }


def test_one_dominant_object_names_the_field_confidently():
    ident = identify_field(
        [(12.0, _row("M 27", 0.02)), (2.0, _row("NGC 6830", 0.15))], fov_deg=0.5)
    assert ident is not None
    assert ident["id"] == "M 27"
    assert ident["confident"] is True
    assert ident["runner_up"] == "NGC 6830"
    assert ident["sep_arcmin"] == pytest.approx(1.2, abs=0.01)


def test_two_comparable_objects_produce_a_winner_that_is_not_confident():
    """The normal state of a rich field. ``confident`` gates everything that
    WRITES, so the honest answer is "here are two" -- not a coin flip stamped
    into a FITS header every stacker downstream will believe."""
    ident = identify_field(
        [(9.0, _row("NGC 6992", 0.03)), (8.0, _row("NGC 6995", 0.04))], fov_deg=0.5)
    assert ident is not None
    assert ident["confident"] is False
    assert ident["runner_up"] == "NGC 6995"


def test_nothing_whose_centre_is_in_the_frame_names_it():
    """A showpiece just off the corner is worth a marker and is not what you are
    pointed at."""
    assert identify_field([(20.0, _row("M 31", 0.4, inside=False))], fov_deg=0.5) is None
    assert identify_field([], fov_deg=0.5) is None


def test_being_off_centre_costs_score_rather_than_multiplying_it():
    """The regression guard for a real bug in the first draft: the design's
    multiplicative centrality factor inverts the ordering whenever the score is
    negative -- which is most of the catalogue, since an unmeasured, unnamed
    object scores about -61. Two FAINT objects, the better one off centre: it
    must still be able to win, and the penalty must still be able to flip it."""
    near_faint = (-40.0, _row("PGC 1", 0.0))
    far_bright = (-36.0, _row("PGC 2", 0.24))     # ~edge of a 0.5 deg field
    ident = identify_field([near_faint, far_bright], fov_deg=0.5)
    assert ident is not None
    # -36 - 6*(0.24/0.25) = -41.8 vs -40.0 at centre -> the centre wins,
    # and it wins BECAUSE of the penalty, not because -40 > -36.
    assert ident["id"] == "PGC 1"
    # Move the brighter one to the centre too and it takes the field back.
    assert identify_field(
        [near_faint, (-36.0, _row("PGC 2", 0.001))], fov_deg=0.5)["id"] == "PGC 2"


def test_identify_field_falls_back_to_a_circle_when_rows_have_no_inside_flag():
    """The pointing-derived path has no plate and therefore no rectangle; the
    same function must still answer, with 'within half a field' as the test."""
    rows = [(12.0, {"id": "M 27", "label": "Dumbbell", "kind": "dso",
                    "type": "Planetary Nebula", "describe": "x", "sep_deg": 0.1})]
    assert identify_field(rows, fov_deg=1.0)["id"] == "M 27"
    assert identify_field(rows, fov_deg=0.1) is None
