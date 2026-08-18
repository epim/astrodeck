"""The Moon's position in ``/api/catalog`` is a location oracle (#193).

WHY THIS IS THE SAME BUG AS THE ONE ALREADY CLOSED. ``/api/catalog`` withholds
ALT/AZ from a caller without ``view.site_derived``, because each pair is
f(site, target) and the caller picks the target. The Moon's RA/Dec is f(site)
too, and by a much larger amount than anyone expects a *catalogue* row to be:
lunar horizontal parallax reaches about a degree. This repo has already had a
viewer recover the rig's site to 2.9 km through exactly this shape, and the
lesson recorded from it was that a filter written against field NAMES cannot
withhold f(lat, lon) — which is why the Moon is withheld WHOLE here, RA, Dec,
distance_km and size_arcmin together.

The Atlas marker layer (``catalog/region.py``) has gated the same body on the
same capability since it shipped. This file is the search box brought to that
gate, plus the measurement that establishes what the OTHER bodies do — because
"the rest are safe" was an assumption in a comment, and an assumption in a
comment is what this class of finding is made of.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.auth import (CAP_VIEW_SITE_DERIVED, CAP_VIEW_STATUS, Principal,
                            caps_for_role, principal_for_role,
                            reset_active_provider, set_active_provider)
from astrodeck.catalog import solar_system as ss
from astrodeck.catalog.coords import angular_sep_deg
from astrodeck.catalog.objects import search
from astrodeck.config import ConfigStore, Site

#: A fixed instant, so every assertion is about the code and not about where the
#: Moon happens to be while the suite runs. 2026-08-08 06:00 UTC.
_WHEN = 1_786_255_200.0

#: Two sites about 15,000 km apart. Neither is this rig's, and neither is near
#: it: the whole point of the finding is that a published position leaks the
#: site, so the test that proves it must not carry one.
_FAR_A = (51.5, 0.0)
_FAR_B = (-33.9, 151.2)
#: ~1.1 km apart. The resolution question: not "can you tell Sydney from
#: London", but "can you tell one suburb from the next".
_NEAR = (51.51, 0.0)


class _FixedPrincipal:
    name = "fake"

    def __init__(self, principal):
        self._principal = principal

    async def resolve(self, request):
        return self._principal


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
    with TestClient(app_module.create_app()) as c:
        yield c


@pytest.fixture
def as_role():
    def _set(role: str):
        set_active_provider(_FixedPrincipal(principal_for_role(role)))
    yield _set
    reset_active_provider()


@pytest.fixture
def at_site(tmp_path, monkeypatch):
    """Move the observer. Returns a setter, so one test can sample two sites."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    monkeypatch.setattr(config_mod, "config_store", store)

    def _set(lat: float, lon: float):
        store.set_site(Site(name="Somewhere", latitude=lat, longitude=lon),
                       expected_version=None)
    return _set


# ============================================== the oracle, before it is closed

def test_the_moon_row_IS_the_observers_position(at_site):
    """The finding itself, measured rather than asserted.

    If this ever stops holding, the gate below is guarding nothing and this
    file should say so instead of quietly passing.
    """
    at_site(*_FAR_A)
    a = search("moon", when=_WHEN).rows[0]
    at_site(*_FAR_B)
    b = search("moon", when=_WHEN).rows[0]
    at_site(*_NEAR)
    near = search("moon", when=_WHEN).rows[0]

    far_sep = angular_sep_deg(a["ra_hours"], a["dec_deg"],
                              b["ra_hours"], b["dec_deg"])
    assert far_sep > 0.5, (
        f"two sites 15,000 km apart moved the Moon only {far_sep * 3600:.1f}\" "
        f"— parallax is not reaching this row and the gate may be pointless")
    near_sep = angular_sep_deg(a["ra_hours"], a["dec_deg"],
                               near["ra_hours"], near["dec_deg"]) * 3600.0
    assert near_sep > 0.2, (
        f"1.1 km of ground moved the Moon {near_sep:.3f}\" — the row resolves "
        f"the observer to about a kilometre, which is the finding")
    # Not only the angles: a key-name filter that stripped ra/dec would still
    # be publishing the site through these two.
    assert a["distance_km"] != b["distance_km"]
    assert a["size_arcmin"] != b["size_arcmin"]


def test_the_planets_shift_too_and_this_test_says_by_how_much(at_site):
    """"The rest are safe" is the assumption this file refuses to inherit.

    Measured here: six of the eight non-lunar bodies move by MORE than an
    arcsecond between two sites, and every one of them moves its published
    ``distance_km``. That is a real residual signal — smaller than the Moon's
    by two orders of magnitude, but not the zero a comment once claimed. It is
    recorded as a number so the decision to leave them published is a decision
    someone made against evidence, and so the day it stops being acceptable
    there is something to point at.
    """
    at_site(*_FAR_A)
    a = {r["id"]: r for r in search("planet", when=_WHEN).rows}
    at_site(*_FAR_B)
    b = {r["id"]: r for r in search("planet", when=_WHEN).rows}
    assert len(a) >= 5, f"too few planets to measure: {sorted(a)}"

    shifts = {k: angular_sep_deg(a[k]["ra_hours"], a[k]["dec_deg"],
                                 b[k]["ra_hours"], b[k]["dec_deg"]) * 3600.0
              for k in a if k in b}
    assert max(shifts.values()) < 60.0, (
        f"a non-lunar body moved more than an arcminute between two sites — "
        f"it now leaks as loudly as the Moon and needs the same gate: {shifts}")
    assert any(v > 1.0 for v in shifts.values()), (
        f"no planet shifted past an arcsecond; if the ephemeris genuinely "
        f"stopped applying parallax, SITE_DERIVED_BODIES' measured table is "
        f"stale: {shifts}")


# ================================================================== the gate

def test_a_caller_without_site_derived_gets_no_moon_and_is_told_why():
    found = search("moon", when=_WHEN, site_derived=False)
    assert "Moon" not in {r["id"] for r in found.rows}, (
        "the Moon's topocentric position was published to a caller that may "
        "not know where this rig is")
    assert any("Moon" in n and "standing" in n for n in found.notes), (
        f"the Moon vanished with no sentence saying why — a silent hole is "
        f"indistinguishable from a broken search. notes={found.notes}")


def test_the_same_query_still_answers_for_a_caller_that_may_see_it():
    """The positive control. A gate that withheld the Moon from everyone would
    pass the test above and would have broken the feature."""
    rows = search("moon", when=_WHEN, site_derived=True).rows
    assert "Moon" in {r["id"] for r in rows}


def test_the_gate_withholds_the_body_not_a_list_of_field_names():
    """No half-Moon: a row with its position stripped would still carry
    distance_km and size_arcmin, both of which are the same parallax in
    different units. The recorded lesson is that a key-name filter cannot
    withhold f(lat, lon), so the row does not exist at all."""
    for row in search("moon", when=_WHEN, site_derived=False).rows:
        assert row.get("kind") != "solar_system" or row["id"] != "Moon"


def test_the_planets_are_not_collateral_damage():
    """Withholding the whole solar system to solve a Moon-shaped problem would
    pass every assertion above and would be the wrong fix."""
    rows = search("planet", when=_WHEN, site_derived=False).rows
    bodies = {r["id"] for r in rows if r["kind"] == "solar_system"}
    assert len(bodies) >= 5, f"the planets disappeared too: {sorted(bodies)}"
    assert "Moon" not in bodies


def test_a_withheld_body_costs_no_ephemeris(monkeypatch):
    """Skipped BEFORE the ephemeris runs, not computed and then dropped.

    A body that is evaluated and discarded still answers the question through
    the clock: the request that touches the Moon takes ~27 ms longer than the
    one that does not, which is a slower oracle for the same secret.
    """
    calls: list[str] = []
    real = ss.position
    monkeypatch.setattr(ss, "position",
                        lambda key, when=None, **kw: (calls.append(key),
                                                      real(key, when, **kw))[1])
    search("moon", when=_WHEN, site_derived=False)
    assert "moon" not in calls, (
        f"the Moon's position was computed and then thrown away: {calls}")
    search("moon", when=_WHEN, site_derived=True)
    assert "moon" in calls, "positive control: the spy never saw a computation"


def test_the_withheld_set_is_pinned_to_the_copy_that_describes_it():
    """``_MOON_WITHHELD_NOTE`` names the Moon and quotes its ~1 degree. Adding a
    second body to the set without revising that sentence would tell a user the
    wrong thing about the wrong body."""
    assert set(ss.SITE_DERIVED_BODIES) == {"Moon"}


# ==================================================================== the route

def test_route_withholds_the_moon_from_a_viewer(client, as_role):
    as_role("admin")
    full = client.get("/api/catalog", params={"q": "moon", "explain": 1}).json()
    assert "Moon" in {r["id"] for r in full["results"]}, (
        "an admin cannot find the Moon — the assertion below would pass for "
        "the wrong reason")
    assert CAP_VIEW_SITE_DERIVED in caps_for_role("admin")

    set_active_provider(_FixedPrincipal(
        Principal(role="viewer", caps=frozenset({CAP_VIEW_STATUS}))))
    shy = client.get("/api/catalog", params={"q": "moon", "explain": 1}).json()
    assert "Moon" not in {r["id"] for r in shy["results"]}
    assert any("Moon" in n for n in shy["notes"]), (
        f"withheld with no explanation on the wire: {shy['notes']}")


def test_route_withholds_the_moon_on_the_bare_list_shape_too(client):
    """``/api/catalog`` without ``explain`` returns a bare LIST, as it always
    has. The gate must not live in the explain branch."""
    set_active_provider(_FixedPrincipal(
        Principal(role="viewer", caps=frozenset({CAP_VIEW_STATUS}))))
    try:
        rows = client.get("/api/catalog", params={"q": "moon"}).json()
        assert isinstance(rows, list)
        assert "Moon" not in {r["id"] for r in rows}
    finally:
        reset_active_provider()


def test_a_viewer_can_still_find_a_planet_through_the_route(client):
    """The route-level positive control for the whole gate."""
    set_active_provider(_FixedPrincipal(
        Principal(role="viewer", caps=frozenset({CAP_VIEW_STATUS}))))
    try:
        rows = client.get("/api/catalog", params={"q": "jupiter"}).json()
        assert "Jupiter" in {r["id"] for r in rows}
    finally:
        reset_active_provider()
