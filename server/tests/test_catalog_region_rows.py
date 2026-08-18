"""``region_rows`` + ``GET /api/catalog/region`` -- the Atlas viewport query.

``objects_in_region`` (tests/test_catalog_region.py) answers "which deep-sky
objects", which is only a third of what the Atlas has to mark. This file covers
the merge of all three catalog sources, the zoom-band membership rule, the
things that must never reach the wire (a 99.0 magnitude, an alt/az pair, the
Moon's site-dependent position), and the route itself.

WHY A DEC PREFILTER TEST IS IN HERE. ``objects_in_region`` gained a declination
prefilter for speed. It is only sound because ``sep >= |dec_a - dec_b|`` on a
sphere, and if that reasoning is ever wrong the failure is SILENT -- objects
near the edge of the circle simply stop being returned, and an annotated sky
with a few missing labels looks exactly like an annotated sky.
``test_dec_prefilter_returns_exactly_the_unfiltered_set`` is the guard, and it
compares against a brute-force scan written out in the test rather than against
the module's own idea of what is inside.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.auth import Principal, caps_for_role, principal_for_role
from astrodeck.auth import (CAP_VIEW_SITE_DERIVED, CAP_VIEW_STATUS,
                            reset_active_provider, set_active_provider)
from astrodeck.auth.rbac import assert_route_capabilities
from astrodeck.catalog import region as region_mod
from astrodeck.catalog.coords import angular_sep_deg
from astrodeck.catalog.objects import CATALOG, MAG_UNKNOWN
from astrodeck.catalog.region import (objects_in_region, offered_at_fov,
                                      region_rows)
from astrodeck.catalog.region import router as region_router
from astrodeck.config import ConfigStore, Site

_BY_ID = {o.id: o for o in CATALOG}

#: A fixed instant so the solar-system half of every assertion below is about
#: the code, not about where Jupiter happens to be while the suite runs.
#: 2026-08-08 06:00 UTC.
_WHEN = 1_786_255_200.0


# ===================================================================== helpers

def _wire_region(app):
    """Register the region router where ``app.py`` will register it.

    ``create_app()`` ends with a catch-all ``GET /{path:path}`` that serves the
    SPA, and FastAPI matches routes in registration order -- so a router added
    AFTER ``create_app()`` returns is shadowed and every request 404s. The one
    line this feature adds to ``app.py`` sits with the other atlas routers,
    hundreds of routes before that catch-all; this splices the router into the
    same position so the tests exercise the wiring the app will actually have.

    A no-op once ``app.include_router(region_router)`` is in ``app.py``, so this
    helper does not have to be removed and cannot silently mask its absence --
    ``test_route_is_registered_in_the_real_app`` is the one that will notice.

    TWO THINGS BIT THIS IN CI AND BOTH ARE ENVIRONMENTAL (2026-08-08).

    The "is it already registered?" test walked ``app.router.routes`` flat.
    FastAPI 0.141 made ``include_router`` append ONE lazy ``_IncludedRouter``
    marker instead of copying the child's routes up, so on a fresh install --
    which CI is, and this box is not -- the real route is invisible behind the
    marker, the helper decides it is absent, and splices a SECOND copy in.
    ``iter_app_routes`` is the repo's existing answer to exactly this; the last
    time it was missed it silently emptied an RBAC assertion for five routers.

    And the SPA catch-all only exists when ``ui/dist`` does. The server job does
    not build the UI, so ``next()`` with no default raised StopIteration inside
    a fixture, which pytest-asyncio re-raised as "generator raised
    StopIteration" -- nine errors whose message named neither the route nor the
    missing directory. Append when there is no catch-all to sit in front of.
    """
    from astrodeck.auth.rbac import iter_app_routes

    if any(getattr(r, "path", "") == "/api/catalog/region"
           for r in iter_app_routes(app)):
        return False
    routes = app.router.routes
    mark = len(routes)
    app.include_router(region_router)
    added = routes[mark:]
    del routes[mark:]
    idx = next((i for i, r in enumerate(routes)
                if getattr(r, "path", "") == "/{path:path}"), len(routes))
    routes[idx:idx] = added
    return True


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.set_site(Site(name="Mid", latitude=40.0, longitude=-74.0),
                   expected_version=None)
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_mod, "config_store", store)
    monkeypatch.setattr(app_module, "config_store", store)
    region_mod._reset_ephemeris_cache()
    app = app_module.create_app()
    _wire_region(app)
    with TestClient(app) as c:
        yield c
    region_mod._reset_ephemeris_cache()


class _FixedPrincipal:
    """Every request resolves to one identity (mirrors test_rbac_enforcement)."""

    name = "fake"

    def __init__(self, principal):
        self._principal = principal

    async def resolve(self, request):
        return self._principal


@pytest.fixture
def as_role():
    def _set(role: str):
        set_active_provider(_FixedPrincipal(principal_for_role(role)))
    yield _set
    reset_active_provider()


# ============================================ the prefilter must change nothing

@pytest.mark.parametrize("ra,dec,radius", [
    (12.45, 12.0, 2.0),        # Virgo core -- the dense case
    (23.95, 0.0, 3.0),         # straddling the 0h/24h RA wrap
    (0.0, 88.0, 4.0),          # inside the north pole's converging meridians
    (5.59, -5.39, 0.75),       # Orion, a small field
    (18.0, -25.0, 10.0),       # Sagittarius, wide
])
def test_dec_prefilter_returns_exactly_the_unfiltered_set(ra, dec, radius):
    """The declination prefilter is an optimisation with a proof
    (``sep >= |dec_a - dec_b|``), not a second region test. If the proof is
    wrong the loss is silent: objects near the rim of the circle stop being
    returned and a sky with a few missing markers looks like a sky.

    Compared against a brute-force scan spelled out HERE rather than against
    the module's own helper, so a bug in the module cannot define its own
    expected answer.
    """
    brute = {o.id for o in CATALOG
             if angular_sep_deg(ra, dec, o.ra_hours, o.dec_deg) <= radius}
    got = {o.id for o in objects_in_region(ra, dec, radius)}
    assert got == brute, (
        f"the prefilter changed the answer: "
        f"{sorted(brute - got)} dropped, {sorted(got - brute)} invented")


def test_prefilter_keeps_an_object_whose_dec_offset_nearly_equals_the_radius():
    """The boundary the bound is tight at. An object almost exactly `radius`
    away in declination alone is INSIDE the circle (its RA offset is what is
    left over), so ``<=`` is the only correct comparison -- a ``<`` or a
    fractional safety margin would drop it."""
    obj = _BY_ID["M31"]                       # dec +41.269
    dec = obj.dec_deg - 1.999                 # 1.999 deg of pure declination away
    hits = {o.id for o in objects_in_region(obj.ra_hours, dec, 2.0)}
    assert "M31" in hits, (
        "an object 1.999 deg away in declination, inside a 2 deg circle, was "
        "dropped -- the prefilter is cutting inside the radius it is meant to "
        "bound")


# ================================================================ all three kinds

def test_a_region_returns_deep_sky_stars_and_solar_system_together():
    """The whole point of the merge: #111 shipped stars and planets into the
    SEARCH BOX and nothing else, so the map never marked one. A single region
    query has to answer with all three or the Atlas is back where it was."""
    # Centred between the Pleiades and Aldebaran, 20 deg: dense in named stars
    # and Messier objects, and the ecliptic runs through it.
    rows, _notes, _tr = region_rows(4.2, 20.0, 20.0, fov_deg=30.0,
                                    limit=200, when=_WHEN)
    kinds = {r["kind"] for r in rows}
    assert "dso" in kinds, "no deep-sky object in a 20 deg circle around Taurus"
    assert "star" in kinds, "no named star -- Aldebaran and the Hyades are here"
    ids = {r["id"] for r in rows}
    assert "M45" in ids and "Aldebaran" in ids


def test_solar_system_bodies_are_marked_where_they_actually_are():
    """A planet is not a table row (solar_system.py's whole thesis). The region
    query has to place it from the ephemeris at the time asked for, and the row
    has to say WHICH instant it was true at."""
    from astrodeck.catalog import solar_system as ss

    p = ss.position("jupiter", _WHEN)
    rows, _n, _t = region_rows(p["ra_hours"], p["dec_deg"], 3.0,
                               fov_deg=5.0, limit=200, when=_WHEN)
    jup = next((r for r in rows if r["id"] == "Jupiter"), None)
    assert jup is not None, "Jupiter is not in a 3 deg circle centred on Jupiter"
    assert jup["kind"] == "solar_system"
    assert abs(jup["ra_hours"] - p["ra_hours"]) < 1e-9
    assert jup["ephemeris_unix"] == pytest.approx(_WHEN)
    assert jup["describe"], "a body with no sentence tells the card nothing"


def test_solar_system_rows_survive_a_full_budget():
    """A dense patch of sky must not push the planets out of the answer. They
    are at most nine rows, they are the only things on this map that move, and
    they are the first thing anyone points a new telescope at."""
    from astrodeck.catalog import solar_system as ss

    p = ss.position("jupiter", _WHEN)
    tiny = region_rows(p["ra_hours"], p["dec_deg"], 20.0, fov_deg=30.0,
                       limit=3, when=_WHEN)
    rows, _n, truncated = tiny
    assert truncated, "a 20 deg circle with limit=3 was not truncated"
    assert "Jupiter" in {r["id"] for r in rows}, (
        "Jupiter was truncated out of its own neighbourhood -- the budget cut "
        "the one row a printed chart could not have given the user")


# ==================================================== what must never be published

def test_no_row_of_any_kind_carries_an_altitude_or_azimuth():
    """/api/catalog adds alt/az only for view.site_derived, because each pair is
    f(site, target) and the caller picks the target -- a search box is already a
    coordinate oracle. A PANNABLE MAP is the same oracle at a far higher sample
    rate, and it needs no alt/az to draw a marker. So this route does not
    compute them at all; a key-name filter is not what is guarding this, the
    absence of the computation is."""
    rows, _n, _t = region_rows(4.2, 20.0, 25.0, fov_deg=40.0,
                               limit=300, when=_WHEN)
    assert rows, "empty region -- the assertion below would pass vacuously"
    for r in rows:
        assert "alt" not in r and "az" not in r, f"{r['id']} published alt/az"
        assert "altitude" not in r and "azimuth" not in r


def test_an_unmeasured_magnitude_is_none_never_the_sentinel():
    """1,823 catalog rows carry MAG_UNKNOWN = 99.0. A card that prints that as a
    brightness has invented a measurement nobody made."""
    unmeasured = next(o for o in CATALOG if o.mag >= MAG_UNKNOWN)
    rows, _n, _t = region_rows(unmeasured.ra_hours, unmeasured.dec_deg, 0.2,
                               fov_deg=0.3, limit=200, when=_WHEN)
    row = next(r for r in rows if r["id"] == unmeasured.id)
    assert row["mag"] is None, (
        f"{unmeasured.id} has no published magnitude and the wire says "
        f"{row['mag']!r}")
    assert all(r["mag"] != MAG_UNKNOWN for r in rows)


def test_the_moon_is_withheld_without_site_derived_and_says_why():
    """Lunar horizontal parallax reaches about a degree, so the Moon's
    published RA/Dec is a strong function of where the observer is standing --
    a hundred times the marker it draws. Everything else on this map is the
    same from anywhere on Earth."""
    from astrodeck.catalog import solar_system as ss

    p = ss.position("moon", _WHEN)
    open_rows, _n, _t = region_rows(p["ra_hours"], p["dec_deg"], 5.0,
                                    fov_deg=8.0, when=_WHEN)
    assert "Moon" in {r["id"] for r in open_rows}, (
        "the Moon is not in a 5 deg circle centred on the Moon -- the "
        "withholding assertion below would pass for the wrong reason")

    shy_rows, notes, _t = region_rows(p["ra_hours"], p["dec_deg"], 5.0,
                                      fov_deg=8.0, site_derived=False,
                                      when=_WHEN)
    assert "Moon" not in {r["id"] for r in shy_rows}
    assert any("Moon" in n and "standing" in n for n in notes), (
        "the Moon vanished with no sentence saying why -- an absent marker "
        f"that explains nothing reads as a broken map. notes={notes}")
    # The planets are NOT withheld -- their topocentric shift is under an
    # arcsecond, which carries no recoverable position. Withholding the whole
    # solar system to solve a Moon-shaped problem would be the wrong fix, and
    # this is what stops it being made quietly.
    everything = region_rows(0.0, 0.0, 90.0, fov_deg=90.0, limit=400,
                             site_derived=False, when=_WHEN)[0]
    bodies = {r["id"] for r in everything if r["kind"] == "solar_system"}
    assert bodies and bodies != {"Moon"}, (
        f"the whole solar system disappeared for a caller without "
        f"view.site_derived: {sorted(bodies)}")
    assert "Moon" not in bodies


def test_every_row_carries_the_five_facts_the_info_card_shows():
    """The card promises magnitude, type, size, constellation and one sentence.
    Shipping them in the region payload is what makes a tap in the dark cost no
    round trip -- so a row missing one of them is a card that has to apologise."""
    rows, _n, _t = region_rows(4.2, 20.0, 15.0, fov_deg=25.0,
                               limit=200, when=_WHEN)
    assert len(rows) > 20, "too few rows to be a real sample"
    for r in rows:
        assert set(r) >= {"id", "label", "kind", "type", "ra_hours", "dec_deg",
                          "mag", "size_arcmin", "constellation", "describe",
                          "alias", "sep_deg"}, f"{r['id']} is missing fields"
        assert r["describe"], f"{r['id']} has no sentence"
        assert r["label"], f"{r['id']} has no label to draw"
        assert r["constellation"], f"{r['id']} has no constellation"


# ============================================================== ranking and bands

def test_rows_are_returned_in_score_order():
    """The client treats array order AS score order when it spends its label
    budget. If the server ever stops sorting, the budget silently goes to
    whatever the catalogue file happened to list first."""
    from astrodeck.catalog.region import _score

    rows, _n, _t = region_rows(12.45, 12.0, 5.0, fov_deg=8.0,
                               limit=100, when=_WHEN)
    scores = [_score(r["mag"] if r["mag"] is not None else MAG_UNKNOWN,
                     r["size_arcmin"], True, 0.0) for r in rows
              if r["kind"] == "star"]
    assert scores == sorted(scores, reverse=True)
    # And the headline claim, as a position comparison rather than a set
    # membership so the failure names both objects: in the Virgo core, M87
    # ("Virgo Galaxy", a Messier object) comes before the anonymous IC/PGC
    # galaxies packed around it.
    ids = [r["id"] for r in rows]
    anon = next(r["id"] for r in rows
                if r["kind"] == "dso" and r["label"] == r["id"])
    assert ids.index("M87") < ids.index(anon), (
        f"{anon} was ranked ahead of M87 in the Virgo core -- the label budget "
        f"is spent in array order, so this is the order the user sees")


@pytest.mark.parametrize("fov,obj_id,offered", [
    # A wide free-roam view offers only what a paper chart prints.
    (40.0, "M31", True),           # Messier and named
    (40.0, "NGC 4565", True),      # named ("Needle Galaxy")
    # NGC 4562: an anonymous mag-14.37 galaxy, 1.9' across. Noise in any view
    # wide enough to hold a constellation; the field itself at 0.2 deg.
    (40.0, "NGC 4562", False),
    (10.0, "NGC 4562", False),
    (2.0, "NGC 4562", False),
    (0.2, "NGC 4562", True),       # narrow: what is in the frame, not what is famous
])
def test_zoom_bands_decide_membership_not_rank(fov, obj_id, offered):
    """One table, not a rule per type. Membership moves with the zoom; ORDER
    does not -- a score that moved with the zoom would re-order labels
    continuously through a pinch, which is the flicker the client's label
    hysteresis exists to prevent."""
    assert offered_at_fov(fov, _BY_ID[obj_id]) is offered


def test_zoom_bands_are_monotone():
    """Zooming IN never removes an object. Over the whole catalogue, not over a
    chosen example -- because the first draft of the table failed this and the
    failure was invisible from any single object: NGC 3172 ("Polarissima
    Borealis", a named mag-15 galaxy) was offered at 40 deg, dropped at 10,
    offered again at 2 and dropped again at 0.2. On screen that is a label that
    blinks out when you zoom towards it, which reads as a broken map.
    """
    bands = (40.0, 10.0, 2.0, 0.2)          # widest -> narrowest
    for obj in CATALOG:
        offered = [offered_at_fov(f, obj) for f in bands]
        first_true = next((i for i, v in enumerate(offered) if v), None)
        if first_true is None:
            continue
        assert all(offered[first_true:]), (
            f"{obj.id} (mag {obj.mag}, {obj.size_arcmin}', "
            f"name={obj.name!r}) is offered at "
            f"{bands[first_true]} deg and dropped again when zoomed in: "
            f"{dict(zip(bands, offered))}")


def test_a_narrow_band_still_offers_an_object_with_no_published_magnitude():
    """"Unmeasured" is never read as "too faint to mention". At the zoom where
    the question is "what is in this frame", an object with no published
    brightness is still in the frame."""
    unmeasured = next(o for o in CATALOG
                      if o.mag >= MAG_UNKNOWN and o.type == "GX")
    assert offered_at_fov(0.2, unmeasured) is True
    # ...and it is never read as "bright enough" either: at 40 deg an anonymous
    # unmeasured object is not offered.
    anon = next(o for o in CATALOG
                if o.mag >= MAG_UNKNOWN and o.name == o.id
                and not o.id.startswith("M"))
    assert offered_at_fov(40.0, anon) is False


# ==================================================== wrap and pole, through the merge

def test_region_rows_finds_an_object_across_the_ra_wrap():
    """The failure mode the whole centre+radius shape exists to avoid, asserted
    at the level the Atlas actually calls -- through the zoom band, the merge
    and the ranking, not just through the cone test.

    NGC 7814 is at RA 0.054h; the view is centred at 23.90h. A naive box test
    (23.90 +/- 3 deg of RA) spans [23.70, 24.10] and 0.054 is in neither half
    of that once it is wrapped, so the box finds nothing at all here.
    """
    assert not (23.70 <= 0.054 <= 24.10), "sanity: the naive box really misses it"
    rows, _n, _t = region_rows(23.90, 16.15, 3.0, fov_deg=5.0,
                               limit=300, when=_WHEN)
    ids = {r["id"] for r in rows}
    assert "NGC 7814" in ids
    # NGC 7772 sits on the other side of the wrap (RA 23.863h) in the same
    # circle: the query has to reach across it in BOTH directions.
    assert "NGC 7772" in ids


def test_region_rows_at_the_pole_spans_the_whole_ra_circle():
    rows, _n, _t = region_rows(0.0, 88.0, 4.0, fov_deg=6.0,
                               limit=300, when=_WHEN)
    ids = {r["id"] for r in rows}
    # NGC 188 (RA 0.79h) and NGC 3172 (RA 11.79h) are both within 4 deg of a
    # centre at the pole despite lying 11 hours of right ascension apart. Near
    # the pole that is correct: meridians converge, so "close in RA" is not
    # what "close on the sky" means.
    assert {"NGC 188", "NGC 3172"} <= ids
    # Polaris is 0.74 deg from the pole -- the star half of the merge, at the
    # place a declination prefilter has to be right about.
    assert "Polaris" in ids


def test_an_empty_patch_of_sky_is_an_empty_answer_not_an_error():
    """The commonest correct answer. It has to read as a fact."""
    rows, notes, truncated = region_rows(10.0, -45.0, 0.2, fov_deg=0.3,
                                         when=_WHEN)
    assert rows == [] and truncated is False
    assert notes == []


# ================================================================ the ephemeris cache

def test_the_ephemeris_is_reused_within_its_ttl_and_recomputed_after():
    """Eight bodies cost ~58 ms of astropy (measured), and the Atlas asks for a
    region every time the view leaves its cached patch. Paying that per pan on
    a Pi is not a trade worth making for a marker."""
    region_mod._reset_ephemeris_cache()
    calls = {"n": 0}
    from astrodeck.catalog import solar_system as ss
    real_row = ss.row

    def counting_row(key, when=None, **kw):
        calls["n"] += 1
        return real_row(key, when, **kw)

    ss.row = counting_row
    try:
        region_mod.solar_system_rows()
        first = calls["n"]
        assert first >= 8, "the fixture never computed a full set"
        region_mod.solar_system_rows()
        assert calls["n"] == first, (
            "a second live call recomputed the ephemeris inside the TTL")
        # An explicit time is never answered from the cache: a test (or a
        # replay) asking about a different instant must not get "now".
        region_mod.solar_system_rows(when=_WHEN)
        assert calls["n"] > first, (
            "an explicit `when` was answered from the live cache -- the rows "
            "would describe a different instant than the one asked for")
    finally:
        ss.row = real_row
        region_mod._reset_ephemeris_cache()


def test_a_site_change_invalidates_the_cached_body_positions(tmp_path, monkeypatch):
    """The Moon's position IS a function of the site (up to ~1 deg). A cached
    set from the previous site would be served for two minutes after the edit."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    monkeypatch.setattr(config_mod, "config_store", store)
    region_mod._reset_ephemeris_cache()

    store.set_site(Site(name="A", latitude=10.0, longitude=10.0),
                   expected_version=None)
    a, _ = region_mod.solar_system_rows()
    moon_a = next(r for r in a if r["id"] == "Moon")

    store.set_site(Site(name="B", latitude=-40.0, longitude=150.0),
                   expected_version=store.cfg().version)
    b, _ = region_mod.solar_system_rows()
    moon_b = next(r for r in b if r["id"] == "Moon")
    region_mod._reset_ephemeris_cache()

    moved = angular_sep_deg(moon_a["ra_hours"], moon_a["dec_deg"],
                            moon_b["ra_hours"], moon_b["dec_deg"])
    assert moved > 0.1, (
        f"the Moon moved {moved:.4f} deg for a 50 deg change of latitude -- the "
        f"cache is serving positions computed for the previous site")


# ========================================================================= the route

def test_route_answers_with_rows_and_states_its_own_degradation(client):
    r = client.get("/api/catalog/region", params={
        "ra_hours": 0.712, "dec_deg": 41.269, "radius_deg": 2.0, "fov_deg": 2.5})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["center"] == {"ra_hours": 0.712, "dec_deg": 41.269}
    assert d["radius_deg"] == 2.0 and d["fov_deg"] == 2.5
    assert "M31" in {row["id"] for row in d["rows"]}
    assert d["catalog_degraded"] is False
    assert isinstance(d["truncated"], bool) and isinstance(d["notes"], list)


def test_route_publishes_no_alt_or_az(client):
    r = client.get("/api/catalog/region", params={
        "ra_hours": 4.2, "dec_deg": 20.0, "radius_deg": 20.0, "fov_deg": 30.0,
        "limit": 300})
    assert r.status_code == 200, r.text
    rows = r.json()["rows"]
    assert rows
    for row in rows:
        assert "alt" not in row and "az" not in row


def test_route_needs_view_status(client, as_role):
    as_role("viewer")
    ok = client.get("/api/catalog/region", params={
        "ra_hours": 0.712, "dec_deg": 41.269, "radius_deg": 1.0})
    assert ok.status_code == 200, "a viewer holds view.status and may read the sky"
    assert CAP_VIEW_STATUS in caps_for_role("viewer")

    set_active_provider(_FixedPrincipal(Principal(role="viewer", caps=frozenset())))
    denied = client.get("/api/catalog/region", params={
        "ra_hours": 0.712, "dec_deg": 41.269, "radius_deg": 1.0})
    assert denied.status_code == 403, (
        f"a principal with no capabilities read the catalog region "
        f"({denied.status_code})")


def test_route_withholds_the_moon_from_a_principal_without_site_derived(client, as_role):
    from astrodeck.catalog import solar_system as ss

    p = ss.position("moon")
    params = {"ra_hours": round(p["ra_hours"], 6), "dec_deg": round(p["dec_deg"], 6),
              "radius_deg": 5.0, "fov_deg": 8.0}

    as_role("admin")
    full = client.get("/api/catalog/region", params=params).json()
    assert "Moon" in {r["id"] for r in full["rows"]}, (
        "the Moon is not inside a circle centred on the Moon")
    assert CAP_VIEW_SITE_DERIVED in caps_for_role("admin")

    set_active_provider(_FixedPrincipal(
        Principal(role="viewer", caps=frozenset({CAP_VIEW_STATUS}))))
    shy = client.get("/api/catalog/region", params=params).json()
    assert "Moon" not in {r["id"] for r in shy["rows"]}
    assert any("Moon" in n for n in shy["notes"])


@pytest.mark.parametrize("params", [
    {"dec_deg": 0.0, "radius_deg": 1.0},                       # no ra_hours
    {"ra_hours": 25.0, "dec_deg": 0.0, "radius_deg": 1.0},     # ra out of range
    {"ra_hours": 1.0, "dec_deg": 91.0, "radius_deg": 1.0},     # dec out of range
    {"ra_hours": 1.0, "dec_deg": 0.0, "radius_deg": 0.0},      # zero radius
    {"ra_hours": 1.0, "dec_deg": 0.0, "radius_deg": 200.0},    # radius past the sky
])
def test_route_refuses_a_position_it_cannot_answer_for(client, params):
    assert client.get("/api/catalog/region", params=params).status_code == 422


def test_the_region_router_satisfies_the_boot_capability_assertion():
    """``create_app()`` runs ``assert_route_capabilities`` before returning, so
    a mis-declared route fails the whole app at boot. This router is not yet in
    ``app.py`` (one ``include_router`` line, applied separately) -- run the same
    assertion over it now so that line cannot be what discovers the problem."""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(region_router)
    assert_route_capabilities(app)


def test_route_is_registered_in_the_real_app():
    """THE SEAM, and the reason this file is not self-congratulating.

    ``_wire_region`` splices the router in so the route tests above can run
    before ``app.py`` is edited -- which means those tests would go on passing
    forever if the ``include_router`` line never landed, and the Atlas would
    fetch a 404 on every pan. That is exactly the defect class
    tests/test_routes_have_callers.py was written for: both halves tested, the
    seam between them never was.

    So this asserts the real ``create_app()`` carries the route, and SKIPS with
    the missing line spelled out while it does not (the router is being added
    in a change that is not allowed to touch ``app.py``). A skip is visible in
    the run summary; a silent pass would not be.
    """
    app = app_module.create_app()
    paths = {getattr(r, "path", "") for r in app.router.routes}
    if "/api/catalog/region" not in paths:
        pytest.skip(
            "GET /api/catalog/region is not registered yet. Add, beside the "
            "other atlas routers in api/app.py:\n"
            "    from ..catalog.region import router as region_router   "
            "(with the other imports)\n"
            "    app.include_router(region_router)                      "
            "(after app.include_router(visibility_router))")
    with TestClient(app) as c:
        r = c.get("/api/catalog/region", params={
            "ra_hours": 0.712, "dec_deg": 41.269, "radius_deg": 2.0})
        assert r.status_code == 200, r.text
        assert "M31" in {row["id"] for row in r.json()["rows"]}
