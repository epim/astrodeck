"""Sun, Moon and planets in Atlas search — checked against JPL Horizons.

GROUND TRUTH. Every position below came from JPL Horizons (ssd.jpl.nasa.gov,
fetched 2026-07-31): astrometric ICRF RA/Dec, observed from Greenwich
(51.4778 N, 0.0015 W, 46 m) at 2026-07-31 04:00:00 UTC. A pinned instant and a
pinned place is the only way to test an ephemeris at all — "roughly where Mars
is" is exactly the kind of claim that made the night this work came out of go
wrong.

The acceptance bar is one degree, which these clear by two orders of magnitude
(the residual is annual aberration, ~20"). The loose bound is deliberate: it is
the bar that fails LOUDLY if someone reintroduces the barycentric-ICRS bug that
moved the Moon 17 degrees, without failing every time astropy refines a
constant in the third decimal.
"""
from __future__ import annotations

import datetime as dt
import math

import pytest

from astrodeck.catalog import solar_system as ss
from astrodeck.catalog.objects import search_catalog
from astrodeck.config import ConfigStore

# 2026-07-31 04:00:00 UTC
WHEN = dt.datetime(2026, 7, 31, 4, 0, 0, tzinfo=dt.timezone.utc).timestamp()

# body -> (RA deg, Dec deg, V mag, apparent diameter arcsec) per JPL Horizons.
HORIZONS = {
    "sun": (130.03397, 18.35958, -26.710, 1889.989),
    "moon": (327.34573, -14.23318, -12.189, 1822.331),
    "mercury": (109.71356, 19.80609, 0.447, 8.121688),
    "venus": (173.43070, 2.89408, -4.282, 20.68098),
    "mars": (81.43343, 23.28325, 1.357, 4.680579),
    "jupiter": (128.91854, 19.12880, -1.782, 31.28702),
    "saturn": (14.19474, 3.33323, 0.635, 18.45204),
    "uranus": (62.67902, 20.90523, 5.766, 3.543193),
    "neptune": (4.12495, 0.26888, 7.712, 2.330356),
}


@pytest.fixture
def store(tmp_path, monkeypatch):
    """An isolated ConfigStore sited at Greenwich, installed where the catalog
    reads it. Greenwich because a topocentric ephemeris needs a real observer
    and the prime meridian is the one site no rig is actually at."""
    import astrodeck.config as config_mod

    s = ConfigStore(path=tmp_path / "astrodeck.json")
    cfg = s.cfg()
    cfg.site = cfg.site.model_copy(update={
        "name": "Greenwich", "latitude": 51.4778, "longitude": -0.0015,
        "elevation_m": 46.0, "is_default": False,
    })
    monkeypatch.setattr(config_mod, "config_store", s)
    return s


def _sep_deg(ra_hours: float, dec_deg: float, ra_deg2: float, dec2: float) -> float:
    ra1, ra2 = math.radians(ra_hours * 15.0), math.radians(ra_deg2)
    d1, d2 = math.radians(dec_deg), math.radians(dec2)
    cos = (math.sin(d1) * math.sin(d2)
           + math.cos(d1) * math.cos(d2) * math.cos(ra1 - ra2))
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


# ------------------------------------------------------------------- positions

@pytest.mark.parametrize("key", sorted(HORIZONS))
def test_position_matches_jpl_horizons_within_a_degree(store, key):
    ra_deg, dec, _, _ = HORIZONS[key]
    p = ss.position(key, WHEN)
    sep = _sep_deg(p["ra_hours"], p["dec_deg"], ra_deg, dec)
    assert sep < 1.0, f"{key} is {sep:.3f} deg from the Horizons position"


def test_the_moon_is_right_to_arcminutes_not_merely_to_a_degree(store):
    """The Moon is the one body where the frame mistakes are visible: it is the
    nearest (so topocentric parallax is a degree) and the fastest. Held to a
    tighter bound than the rest so a regression cannot hide inside the
    one-degree acceptance."""
    ra_deg, dec, _, _ = HORIZONS["moon"]
    p = ss.position("moon", WHEN)
    sep_arcmin = _sep_deg(p["ra_hours"], p["dec_deg"], ra_deg, dec) * 60.0
    assert sep_arcmin < 2.0, f"moon off by {sep_arcmin:.2f} arcmin"


@pytest.mark.parametrize("key", sorted(HORIZONS))
def test_apparent_diameter_matches_horizons(store, key):
    """The disc size is computed from the ephemeris distance, so agreeing with
    Horizons to a percent is independent evidence the DISTANCE is right too —
    which the RA/Dec check alone cannot see."""
    _, _, _, diam_arcsec = HORIZONS[key]
    ours = ss.position(key, WHEN)["size_arcmin"] * 60.0
    assert abs(ours - diam_arcsec) / diam_arcsec < 0.01


@pytest.mark.parametrize("key", sorted(set(HORIZONS) - {"saturn"}))
def test_magnitude_matches_horizons(store, key):
    """Within 0.15 mag. Saturn is excluded and tested separately: our model is
    the globe only."""
    _, _, mag, _ = HORIZONS[key]
    ours = ss.position(key, WHEN)["mag"]
    assert abs(ours - mag) < 0.15, f"{key}: {ours:.2f} vs Horizons {mag}"


def test_saturn_magnitude_is_the_globe_only_and_reads_faint(store):
    """Saturn's rings can add most of a magnitude and are not modelled. Pinning
    the KNOWN error keeps it a documented limitation instead of a surprise: if
    someone adds a ring model this test is what tells them to delete it."""
    ours = ss.position("saturn", WHEN)["mag"]
    truth = HORIZONS["saturn"][2]
    assert 0.0 < ours - truth < 0.5


def test_positions_are_computed_not_stored(store):
    """The whole reason these are not table rows: the Moon moves ~30 arcmin an
    hour, so two evaluations an hour apart must NOT agree."""
    a = ss.position("moon", WHEN)
    b = ss.position("moon", WHEN + 3600.0)
    moved = _sep_deg(a["ra_hours"], a["dec_deg"], b["ra_hours"] * 15.0, b["dec_deg"])
    assert 0.3 < moved < 1.0


def test_row_carries_the_instant_it_was_computed_for(store):
    r = ss.row("mars", WHEN)
    assert r["ephemeris_unix"] == WHEN
    assert r["topocentric"] is True


# --------------------------------------------------------------- unknown site

def test_moon_is_geocentric_and_says_so_when_the_site_is_unset(tmp_path, monkeypatch):
    """lat 0 / lon 0 is the Atlantic, not a location. Rather than pretend, an
    unconfigured rig gets the geocentric Moon and a row that admits it — the
    difference is up to a degree, twice the Moon's own width."""
    import astrodeck.config as config_mod

    s = ConfigStore(path=tmp_path / "astrodeck.json")
    assert s.cfg().site.is_default is True
    monkeypatch.setattr(config_mod, "config_store", s)

    r = ss.row("moon", WHEN)
    assert r["topocentric"] is False
    assert "geocentric" in r["name"]
    # ...and it really is the geocentric place, not the topocentric one.
    ra_deg, dec, _, _ = HORIZONS["moon"]
    assert _sep_deg(r["ra_hours"], r["dec_deg"], ra_deg, dec) > 0.1


# ------------------------------------------------------------------- the Sun

def test_the_sun_is_not_a_search_result_by_default(store):
    """Sun avoidance is armed out of the box, so the Sun is not offered as an
    ordinary target — searching for it finds anything BUT the Sun."""
    assert ss.sun_is_offered() is False
    ids = [r["id"] for r in search_catalog("sun", limit=50, when=WHEN)]
    assert "Sun" not in ids
    ids = [r["id"] for r in search_catalog("sol", limit=50, when=WHEN)]
    assert "Sun" not in ids


def test_a_blocked_sun_can_say_why_it_is_blocked(store):
    reason = ss.sun_block_reason()
    assert reason and "30" in reason and "avoidance" in reason.lower()


def test_the_search_gate_is_the_slew_gate(store):
    """The search must not invent a second solar policy. Disarming avoidance —
    the same config field Hub._check_solar reads, admin-only — is what makes the
    Sun appear, and zeroing the cone does it too, exactly as the gate treats
    both as inert."""
    cfg = store.cfg()
    cfg.safety = cfg.safety.model_copy(update={"solar_avoidance": False})
    assert ss.sun_is_offered() is True
    assert ss.sun_block_reason() is None
    assert "Sun" in [r["id"] for r in search_catalog("sun", limit=50, when=WHEN)]

    cfg.safety = cfg.safety.model_copy(update={"solar_avoidance": True,
                                               "solar_exclusion_deg": 0.0})
    assert ss.sun_is_offered() is True


def test_the_sun_row_names_the_filter_it_needs(store):
    cfg = store.cfg()
    cfg.safety = cfg.safety.model_copy(update={"solar_avoidance": False})
    r = ss.row("sun", WHEN)
    assert "filtered scope" in r["name"]


def test_a_body_inside_the_cone_says_the_mount_will_refuse_it(store):
    """On this date Mercury is ~19 deg from the Sun, inside the default 30 deg
    cone: the mount WILL reject the slew. The row says so before the user taps
    it, instead of letting them discover it as a 409."""
    r = ss.row("mercury", WHEN)
    assert r["sun_separation_deg"] < 30.0
    assert "the mount will refuse this slew" in r["name"]
    # ...and a body well clear of the Sun carries no such claim.
    assert "refuse" not in ss.row("saturn", WHEN)["name"]


# -------------------------------------------------------------------- search

@pytest.mark.parametrize("query,expected", [
    ("Mars", "Mars"), ("mars", "Mars"), ("MARS", "Mars"),
    ("Moon", "Moon"), ("moon", "Moon"), ("luna", "Moon"),
    ("jupiter", "Jupiter"), ("Saturn", "Saturn"), ("venus", "Venus"),
    ("mercury", "Mercury"), ("uranus", "Uranus"), ("neptune", "Neptune"),
])
def test_solar_system_bodies_resolve_by_name(store, query, expected):
    assert search_catalog(query, when=WHEN)[0]["id"] == expected


def test_a_planet_outranks_a_coincidental_deep_sky_substring(store):
    """A named body must never lose to a DSO that merely contains its letters.
    "sun" is the live example in this catalog: M63 is the Sunflower Galaxy."""
    ids = [r["id"] for r in search_catalog("sunflower", limit=10, when=WHEN)]
    assert ids[0] == "M63"
    # Mars is an exact body name; nothing coincidental may come first.
    assert search_catalog("mars", limit=10, when=WHEN)[0]["id"] == "Mars"


def test_planets_answer_the_type_query(store):
    ids = [r["id"] for r in search_catalog("planet", limit=20, when=WHEN)]
    assert {"Mars", "Jupiter", "Saturn", "Venus"} <= set(ids)
    assert "Sun" not in ids


def test_browsing_with_an_empty_query_stays_deep_sky(store):
    """The empty query is the "what shall I image?" browse. A planet whose
    position is only true for this second does not belong in a browse list, and
    would sort above every galaxy in it."""
    rows = search_catalog("", limit=200, when=WHEN)
    assert all(r["kind"] == "dso" for r in rows)


def test_rows_are_shaped_like_catalog_rows(store):
    """Same keys the deep-sky rows carry, so /api/catalog can stamp alt/az on
    them without knowing what kind of thing they are."""
    r = ss.row("mars", WHEN)
    for key in ("id", "name", "type", "ra_hours", "dec_deg", "mag", "size_arcmin"):
        assert key in r, key
    assert isinstance(r["ra_hours"], float) and 0.0 <= r["ra_hours"] < 24.0
    assert -90.0 <= r["dec_deg"] <= 90.0


# --------------------------------------------------------------- via the API
# Green unit tests are not the same as "the user can see it": these go through
# the route the Atlas actually calls.

@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    from astrodeck.api.app import create_app
    from astrodeck.config import Site

    s = ConfigStore(path=tmp_path / "astrodeck.json")
    s.set_site(Site(name="Greenwich", latitude=51.4778, longitude=-0.0015,
                    elevation_m=46.0), expected_version=None)
    monkeypatch.setattr(config_mod, "config_store", s)
    monkeypatch.setattr(hub_mod, "config_store", s)
    with TestClient(create_app()) as c:
        yield c


@pytest.mark.parametrize("query,expected", [
    ("Caph", "Caph"), ("bet Cas", "Caph"), ("Polaris", "Polaris"),
    ("Mars", "Mars"), ("Moon", "Moon"),
])
def test_the_route_the_atlas_calls_finds_them(client, query, expected):
    r = client.get("/api/catalog", params={"q": query})
    assert r.status_code == 200, r.text
    rows = r.json()
    assert rows and rows[0]["id"] == expected
    # /api/catalog stamps live alt/az on every row it returns; a body computed
    # at request time has to survive that untouched.
    assert -90.0 <= rows[0]["alt"] <= 90.0
    assert 0.0 <= rows[0]["az"] < 360.0


def test_the_route_never_hands_back_the_sun(client):
    rows = client.get("/api/catalog", params={"q": "sun"}).json()
    assert "Sun" not in [r["id"] for r in rows]


def test_the_moon_row_says_how_lit_it_is(store):
    """A 93%-lit Moon is the reason tonight's narrowband target failed, and it
    is not something the user can see from a target list otherwise."""
    r = ss.row("moon", WHEN)
    assert "% lit" in r["name"]
    assert "waxing" in r["name"] or "waning" in r["name"]
    assert 0.0 <= r["illumination"] <= 1.0
