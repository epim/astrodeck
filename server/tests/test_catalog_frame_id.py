# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
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

from astrodeck.catalog.framing import (
    compute_mosaic, deproject, panel_convergence_deg, project)
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


# ----------------------------------------------- the per-panel position angle
#
# GOLDEN VECTORS FOR ``compute_mosaic``'S TWO PER-PANEL KEYS (#175, backlog
# WP-124), the same numbers as ``GOLDEN`` in
# ``ui/src/lib/__tests__/w16PanelPa.test.ts``: ``convergence_deg`` (local north
# at the panel on the grid's tangent plane, from +eta toward +xi) and
# ``pa_deg`` (the layout angle plus it, wrapped into [0, 360)). Printed from
# the TypeScript mirror at full precision; the two languages agree to 1.3e-10
# deg (the trig libraries' last place, taken up by the projection's 1e-4 deg
# probe), so both hold them to 1e-8. The sign is the rig's: image up is north
# rotated toward WEST by CROTA2 (confirmed on a real solve, #175), which puts
# positive convergence on the panels WEST of the centre (column 0).

_PANEL_BASE = dict(ra_hours=6.0, dec_deg=75.0, rows=3, cols=3, overlap=0.25,
                   rotation_deg=0.0, fov_x_deg=2.0, fov_y_deg=1.33)

_PANEL_PA_GOLDEN = [
    # (name, spec overrides, [(row, col, convergence_deg, pa_deg), ...])
    ("Dec 75, layout 0 (issue #175's 5.97 deg)", {}, [
        (0, 0, 5.9654296824786535, 5.9654296824786535),
        (0, 1, 0, 0),
        (0, 2, -5.9654296824786535, 354.03457031752134),
        (1, 2, -5.580364025849719, 354.41963597415025),
        (1, 1, 0, 0),
        (1, 0, 5.580364025976295, 5.580364025976295),
        (2, 0, 5.2418652672010495, 5.2418652672010495),
        (2, 1, 0, 0),
        (2, 2, -5.2418652672010495, 354.75813473279896),
    ]),
    ("Dec 41, layout 0 (issue #175's 1.32 deg)", {"dec_deg": 41.0}, [
        (0, 0, 1.3237314315092013, 1.3237314315092013),
        (0, 1, 0, 0),
        (0, 2, -1.3237314315092013, 358.6762685684908),
        (1, 2, -1.303705065822304, 358.6962949341777),
        (1, 1, 0, 0),
        (1, 0, 1.303705065822304, 1.303705065822304),
        (2, 0, 1.2842755162870618, 1.2842755162870618),
        (2, 1, 0, 0),
        (2, 2, -1.2842755162870618, 358.71572448371296),
    ]),
    ("Dec 75, layout 358 (the wrap)", {"rotation_deg": 358.0}, [
        (0, 0, 5.845261522679594, 3.845261522679607),
        (0, 1, -0.13894296730754027, 357.8610570326925),
        (0, 2, -6.076987218756715, 351.9230127812433),
    ]),
    ("Dec 60, layout 37, 2 rows by 3 columns",
     {"dec_deg": 60.0, "rows": 2, "cols": 3, "rotation_deg": 37.0}, [
         (0, 0, 2.5541353140949665, 39.554135314094964),
         (0, 1, 0.5262056577787382, 37.52620565777874),
         (0, 2, -1.6182654363403373, 35.38173456365966),
         (1, 2, -2.6331231655053107, 34.36687683449469),
         (1, 1, -0.5136848230263885, 36.48631517697361),
         (1, 0, 1.4958461166961985, 38.4958461166962),
     ]),
    ("Dec 80, 4 columns by 1 row, 10% overlap",
     {"dec_deg": 80.0, "rows": 1, "cols": 4, "overlap": 0.1}, [
         (0, 0, 14.962769174968981, 14.962769174968981),
         (0, 1, 5.0907153626880515, 5.0907153626880515),
         (0, 2, -5.090715362751404, 354.9092846372486),
         (0, 3, -14.962769174968981, 345.03723082503103),
     ]),
]


@pytest.mark.parametrize("name,over,rows", _PANEL_PA_GOLDEN,
                         ids=[g[0] for g in _PANEL_PA_GOLDEN])
def test_panel_pa_matches_the_typescript_mirror(name, over, rows):
    """``compute_mosaic``'s ``convergence_deg`` and ``pa_deg`` are the
    numbers the TypeScript mirror prints for the same layouts, both ways
    round: this file holds the server to them, ``w16PanelPa.test.ts`` the
    mirror.

    Mutant "n_i sign flipped" (``panel_convergence_deg`` returning the
    negative) fails the first row of every case, e.g.:
        AssertionError: ("Dec 75, layout 0 (issue #175's 5.97 deg)", 0, 0)
        assert -5.965429682478653 == 5.9654296824786535 ± 1.0e-08
    Mutant "pa_deg = rotation_deg" fails its ``pa_deg`` in every case:
        AssertionError: ("Dec 75, layout 0 (issue #175's 5.97 deg)", 0, 0)
        assert 0.0 == 5.9654296824786535 ± 1.0e-08
    """
    panels = {(p["row"], p["col"]): p
              for p in compute_mosaic({**_PANEL_BASE, **over})["panels"]}
    for row, col, conv, pa in rows:
        p = panels[(row, col)]
        assert p["convergence_deg"] == pytest.approx(conv, abs=1e-8), (
            name, row, col)
        assert p["pa_deg"] == pytest.approx(pa, abs=1e-8), (name, row, col)


def test_local_north_at_the_pole_turns_by_the_ra_difference():
    """Within ``NORTH_PROBE_DEG`` of the pole the probe goes south and its
    answer is reversed. The vector is the one the mirror prints (to the
    bit), and its value is the analytic one: at the pole local north is
    rotated from the grid's up by 15 deg per hour of RA from the tangent
    point.

    Mutant "pole probe not reversed" (``sign`` fixed at 1) fails:
        assert -165.00054541329604 == 14.999454586703967 ± 1.0e-08
    """
    n = panel_convergence_deg({"ra_hours": 3.0, "dec_deg": 89.99995}, 4.0, 89.5)
    assert n == pytest.approx(14.999454586703967, abs=1e-8)
    assert n == pytest.approx(15.0, abs=0.001)


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
