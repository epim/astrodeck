"""#203: a solar-system row must not be a function of where the rig stands.

THE FINDING. `catalog/solar_system.position` evaluated every body
TOPOCENTRICALLY, and those rows go to anyone with `view.status`. Only the Moon
was withheld. Measured on this tree at 2026-08-18 06:00 UTC, geocentric vs this
rig's latitude:

    Moon      3307"  (0.92 deg)      713 km of distance_km
    Venus       12.8"                1,704 km
    Sun          7.5"                3,269 km
    Mercury      6.1"                3,263 km
    Mars         4.1"                2,829 km
    Jupiter      1.3"                3,374 km
    Saturn       1.0"               -1,126 km
    Uranus       0.4"                1,598 km
    Neptune      0.4"               -1,781 km

Eight distances along eight different lines of sight is a trilateration with
more equations than unknowns. The module's own note conceded it and filed the
decision: "Withholding them too is a judgement call ... filed rather than made
here".

WHY THE TESTS BELOW ARE SHAPED LIKE THIS. The same note names the constraint:

    a filter written against field NAMES cannot withhold f(lat, lon)

So the tests are not per-field either. The central one computes every row at
TWO SITES 1000 km APART and asserts the whole dict is equal — every key, and
every key anyone adds later. A field introduced next year is covered the day it
is written, which is the only kind of coverage this class of defect responds to.

The fix it guards: a caller without `view.site_derived` gets a different
OBSERVER, not a smaller row. The site is never an input, so nothing downstream
can carry it.
"""
import math

import pytest

from astrodeck.catalog import objects, region
from astrodeck.catalog import solar_system as ss
from astrodeck.config import Site, config_store

#: A fixed instant, so nothing here depends on when the suite runs.
WHEN = 1_787_040_000.0            # 2026-08-18 06:00 UTC

#: Two real places 1000 km apart. Far enough that topocentric parallax is
#: unmistakable, close enough that both are plausible amateur sites.
SITE_A = (37.40, -122.10, 30.0)
SITE_B = (34.05, -118.24, 90.0)   # ~560 km; still 1,000x any tolerance here


def _set_site(lat, lon, elev):
    config_store.set_site(Site(name="Somewhere", latitude=lat, longitude=lon,
                               elevation_m=elev, is_default=False),
                          expected_version=config_store.cfg().version)
    region._reset_ephemeris_cache()


def _unset_site():
    """`set_site` forces is_default=False by design ("a user-saved site is, by
    definition, no longer the default"), so an unconfigured rig has to be
    reached by mutating the loaded config directly."""
    config_store.cfg().site.is_default = True
    region._reset_ephemeris_cache()


@pytest.fixture(autouse=True)
def _a_configured_site():
    """Every test here needs a REAL site, because the leak only exists when
    there is one to leak. An unconfigured rig has always been geocentric."""
    _set_site(*SITE_A)
    yield
    region._reset_ephemeris_cache()


def _rows(site, *, site_derived):
    _set_site(*site)
    return {b.key: ss.row(b.key, WHEN, site_derived=site_derived)
            for b in ss.BODIES}


# ------------------------------------------------------- the whole-row test

def test_no_field_of_any_row_moves_when_the_rig_does():
    """THE FINDING, CLOSED. Whole-dict equality, every body, two sites 560 km
    apart. Not a list of fields — a list of fields is what the module's own
    note says cannot work, and a field added next year would slip straight
    past one."""
    a = _rows(SITE_A, site_derived=False)
    b = _rows(SITE_B, site_derived=False)
    assert a.keys() == b.keys()
    for key in a:
        assert a[key] == b[key], (
            f"{key}'s row changed when the rig moved 560 km — "
            f"it is still f(lat, lon)")


def test_the_positive_control_the_test_above_needs():
    """Without this, the test above passes for free the day someone breaks the
    ephemeris into returning constants. A privileged caller MUST see the site."""
    a = _rows(SITE_A, site_derived=True)
    b = _rows(SITE_B, site_derived=True)
    moved = [k for k in a if a[k] != b[k]]
    assert set(moved) == set(a), (
        f"only {len(moved)} of {len(a)} bodies moved for a privileged caller — "
        f"the ephemeris is not reading the site at all")


def test_the_leak_this_closed_was_real():
    """The measurement, executable — and stated the way the finding states it:
    against the GEOCENTRIC value, which is what an observer at an unknown place
    would compute. Every published `distance_km` sat a thousand kilometres or
    more from it, and each one is the observer's own distance along that body's
    line of sight. Eight of them, at eight geometries, is a trilateration.

    Not site A against site B: that difference is the RESIDUAL between two
    guesses (5 km for Mars), and reading it as the leak would understate this
    by three orders of magnitude.

    Measured at SITE_A and WHEN, `distance_km` minus its geocentric value:

        sun 4065   venus 3970   neptune 3929   mercury 3656   jupiter 3540
        saturn 3501   moon 3125   mars 1464   uranus 647

    The bound is the projection of the observer's own 6378 km radius onto the
    line of sight, so a body low in the sky offsets less — Uranus at 647 km is
    that, not a weaker leak. 500 km is the floor here because it is under the
    smallest of them and still a hundred times any plausible rounding."""
    topo = _rows(SITE_A, site_derived=True)
    free = _rows(SITE_A, site_derived=False)
    for key in topo:
        shift = abs(topo[key]["distance_km"] - free[key]["distance_km"])
        assert shift > 500, f"{key} sat only {shift} km off geocentric"
    moon_sep = math.hypot(
        (topo["moon"]["ra_hours"] - free["moon"]["ra_hours"]) * 15.0
        * math.cos(math.radians(topo["moon"]["dec_deg"])),
        topo["moon"]["dec_deg"] - free["moon"]["dec_deg"])
    assert moon_sep > 0.5, f"the Moon sat only {moon_sep:.3f} deg off geocentric"


# --------------------------------------------------------- and still usable

@pytest.mark.parametrize("key", [b.key for b in ss.BODIES if b.key != "moon"])
def test_a_site_free_planet_is_still_the_right_planet(key):
    """The trade. A viewer's row must stay accurate enough to be worth showing:
    a search result is a name and a place in the sky, and the mount
    plate-solves. 30" is an order of magnitude under the widest measured shift
    (12.8" for Venus) and far under any marker on any Atlas zoom."""
    truth = ss.row(key, WHEN, site_derived=True)
    free = ss.row(key, WHEN, site_derived=False)
    sep = math.hypot(
        (truth["ra_hours"] - free["ra_hours"]) * 15.0
        * math.cos(math.radians(truth["dec_deg"])),
        truth["dec_deg"] - free["dec_deg"]) * 3600.0
    assert sep < 30.0, f"{key} is {sep:.1f}\" out — too far to show"
    assert truth["constellation"] == free["constellation"]


def test_the_moon_is_withheld_rather_than_degraded():
    """The one body this fix does NOT cover, on purpose. Geocentric it is 0.92
    deg out — nearly two lunar diameters, visibly wrong to anyone who looks up.
    A wrong marker is not an improvement on a stated refusal."""
    truth = ss.row("moon", WHEN, site_derived=True)
    free = ss.row("moon", WHEN, site_derived=False)
    sep = abs(truth["dec_deg"] - free["dec_deg"])
    assert sep > 0.1, "the Moon's parallax vanished; re-check this whole file"
    assert "Moon" in ss.SITE_DERIVED_BODIES

    found = objects.search("moon", when=WHEN, site_derived=False)
    assert not [r for r in found.rows if r["id"] == "Moon"]
    assert any("Moon" in n for n in found.notes), (
        "withheld in silence — indistinguishable from an empty catalogue")


def test_the_row_says_which_reason_it_is_geocentric_for():
    """Two reasons, two sentences. The Atlas card's copy is "no site is set",
    which is FALSE for a viewer on a configured rig — and advice to change a
    setting they cannot change and that is already correct."""
    assert ss.row("mars", WHEN, site_derived=True)["geocentric_reason"] is None
    assert (ss.row("mars", WHEN, site_derived=False)["geocentric_reason"]
            == "not_permitted")
    _unset_site()
    assert (ss.row("mars", WHEN, site_derived=True)["geocentric_reason"]
            == "site_unset")


# ------------------------------------------------------- the other two doors

def test_search_hands_a_viewer_the_site_free_rows():
    """`objects.search` is the higher-precision half of the same oracle: the
    caller picks the target and can type as many as they like."""
    _set_site(*SITE_A)
    a = {r["id"]: r for r in objects.search(
        "mars", when=WHEN, site_derived=False).rows if r["kind"] == "solar_system"}
    _set_site(*SITE_B)
    b = {r["id"]: r for r in objects.search(
        "mars", when=WHEN, site_derived=False).rows if r["kind"] == "solar_system"}
    assert a and a == b, "a search result moved with the rig"


def test_the_atlas_region_hands_a_viewer_the_site_free_rows():
    """Through `region_rows`, which is what `/api/catalog/region` calls — NOT
    through `solar_system_rows`, which is only the helper it calls.

    That distinction found a live leak. A first draft tested the helper, went
    green, and `scored_region_rows` was still calling it as
    `solar_system_rows(when)` — default `site_derived=True` — while dropping the
    Moon by name in the loop underneath. Every other body went out topocentric
    through a gate written to stop exactly that. Test the door, not the hinge.
    """
    _set_site(*SITE_A)
    mars = ss.row("mars", WHEN, site_derived=True)
    ra, dec = mars["ra_hours"], mars["dec_deg"]
    rows_a, _notes, _trunc = region.region_rows(ra, dec, 5.0, when=WHEN,
                                                site_derived=False)
    a = [r for r in rows_a if r.get("kind") == "solar_system"]
    _set_site(*SITE_B)
    rows_b, _n2, _t2 = region.region_rows(ra, dec, 5.0, when=WHEN,
                                          site_derived=False)
    b = [r for r in rows_b if r.get("kind") == "solar_system"]
    assert a, "no body in the region — this test would pass on anything"
    assert a == b, "an Atlas marker moved with the rig"


def test_the_note_stops_being_a_promise_the_code_does_not_keep():
    """`_MOON_WITHHELD_NOTE` has always ended "Everything else on this map is
    the same from anywhere on Earth." Until #203 that was false — every other
    body was computed for this rig's own latitude and longitude. The sentence
    and the behaviour are pinned together here so they cannot part again."""
    assert "the same from anywhere on Earth" in region._MOON_WITHHELD_NOTE
    a = _rows(SITE_A, site_derived=False)
    b = _rows(SITE_B, site_derived=False)
    assert [a[k] for k in a if k != "moon"] == [b[k] for k in b if k != "moon"]


def test_the_helper_under_it_is_site_free_too():
    """The hinge, separately, because `objects_in_frame` reaches it by a
    different route."""
    _set_site(*SITE_A)
    a, _ = region.solar_system_rows(WHEN, site_derived=False)
    _set_site(*SITE_B)
    b, _ = region.solar_system_rows(WHEN, site_derived=False)
    assert a and a == b


def test_the_ephemeris_cache_does_not_cross_the_capability_boundary():
    """THE TRAP IN THIS CHANGE. `_ephem_cache` is MODULE-LEVEL and the
    capability is per-CALLER, so a topocentric set computed for an operator
    would be served to the viewer who asked thirty seconds later. That leak
    needs two readers to appear, which is the one condition a single-caller
    test never reproduces — so it gets its own."""
    region._reset_ephemeris_cache()
    privileged, _ = region.solar_system_rows(site_derived=True)
    viewer, _ = region.solar_system_rows(site_derived=False)
    assert privileged != viewer, (
        "the viewer was served the operator's cached topocentric rows")
    again, _ = region.solar_system_rows(site_derived=False)
    assert again == viewer, "the viewer's own set is not being cached at all"


def test_the_preview_broadcast_carries_no_parallax():
    """`objects_in_frame` rides the `preview` websocket event, which goes to
    every connected client at whatever capability each holds — the widest
    channel in the product. It used to ask for TOPOCENTRIC rows and drop the
    Moon from them by name, leaving eight planets on the wire still carrying
    thousands of kilometres of parallax in `distance_km`.

    THE FRAME IS AIMED AT A PLANET ON PURPOSE, and the count is asserted before
    the comparison. A first draft used the fixture's default pointing (Orion),
    found no bodies in it, and compared two empty lists — so reverting the fix
    left it green. A test whose subject can be absent has to say so out loud.
    """
    from test_catalog_frame_id import _plate      # the module's own fixture

    _set_site(*SITE_A)
    mars = ss.row("mars", WHEN, site_derived=True)
    # 3 deg across, so the planet is comfortably inside whichever observer the
    # code under test picks — the point is what the row CARRIES, not placement.
    wcs = _plate(ra_deg=mars["ra_hours"] * 15.0, dec_deg=mars["dec_deg"],
                 scale_deg=3.0 / 400.0, w=400, h=400)
    a = [r for r in region.objects_in_frame(wcs, 400, 400, when=WHEN)["objects"]
         if r.get("kind") == "solar_system"]
    _set_site(*SITE_B)
    b = [r for r in region.objects_in_frame(wcs, 400, 400, when=WHEN)["objects"]
         if r.get("kind") == "solar_system"]
    assert a, "no body landed in the frame — this test would pass on anything"
    assert a == b, "a body on the preview broadcast moved with the rig"
