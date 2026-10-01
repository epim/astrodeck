"""#24: with no site saved, the catalogue withholds rather than invents.

`hub.site` defaults to latitude 0, longitude 0 with `is_default` True. Every
alt/az in `/api/catalog` is f(site, target), and the whole of
`/api/catalog/tonight` is f(site) - so at the default both answer confidently
for the Gulf of Guinea, in rows that look exactly like correct ones.

THE TWO ROUTES GET DIFFERENT ANSWERS, and the difference is whether anything
useful survives losing the site.

  * `/api/catalog` is a SEARCH. Name, type, magnitude and RA/Dec are catalogue
    facts that do not depend on where the rig is, so the rows stay and only the
    derived fields go. That is not a new path: it is exactly what a caller
    without `view.site_derived` already receives, so an unset site takes the
    road that is already built and tested rather than one of its own.

    Owner's ruling, 2026-09-22: "Can the catalog not work without exposing the
    location? Just don't leak the lat/lon." The same shape answers both
    questions - a caller who may not know where the rig is, and a rig that does
    not know either, are both served the facts and not the derivations.

  * `/api/catalog/tonight` is a RANKING BUILT FROM THE NIGHT. Windows, transit
    altitudes and the ordering are all f(site); strip them and nothing is left
    but the catalogue in arbitrary order. It refuses with 409 `no_site`,
    because this is the list an operator picks targets off and a wrong answer
    there is worse than no answer by the width of a night.

MUTATIONS RUN, and what each printed:

  M1, `derived_ok = principal.has(CAP_VIEW_SITE_DERIVED)` - the defect, alt/az
  computed from the default site. 2 failed: the withheld-fields case and the
  ordering case.

  M2, delete the `tonight` refusal. 1 failed: the tonight case, which then
  returns 200 and a full ranking computed for 0,0.

  M3, `sited = True`. 3 failed: both search cases and the note - the check that
  the search route reads one predicate rather than re-deriving it.

  M4, refuse the SEARCH route too when unsited. 2 failed, led by the case
  holding the owner's ruling that the catalogue keeps working. That mutation is
  the tempting one - it is what the `tonight` route does, and doing the same
  thing twice looks like consistency until you notice the two routes lose
  different amounts when the site goes.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.auth import (principal_for_role, reset_active_provider,
                            set_active_provider)
from astrodeck.config import ConfigStore, Site


class _FixedPrincipal:
    name = "fake"

    def __init__(self, principal):
        self._principal = principal

    async def resolve(self, request):
        return self._principal


@pytest.fixture
def unsited(tmp_path, monkeypatch):
    """An app whose config has never had a site saved.

    Set on the store's live config rather than through `set_site`, which forces
    `is_default` False - "a user-saved site is, by definition, no longer the
    default" (config.py). There is no supported way to SAVE an unset site,
    which is right, and it means this state is only ever reached by never
    having saved one: a fresh install, which is exactly who this is for.
    """
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_mod, "config_store", store)
    monkeypatch.setattr(app_module, "config_store", store)
    store.cfg().site = Site(name="", latitude=0.0, longitude=0.0,
                            elevation_m=0.0, is_default=True)
    assert store.cfg().site.is_default is True, "the fixture did not reach an unset site"
    set_active_provider(_FixedPrincipal(principal_for_role("admin")))
    with TestClient(app_module.create_app()) as c:
        yield c
    reset_active_provider()


@pytest.fixture
def sited(tmp_path, monkeypatch):
    """The control: the same app with a real site."""
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_mod, "config_store", store)
    monkeypatch.setattr(app_module, "config_store", store)
    store.set_site(Site(name="Somewhere", latitude=40.0, longitude=-74.0,
                        elevation_m=10.0), expected_version=None)
    set_active_provider(_FixedPrincipal(principal_for_role("admin")))
    with TestClient(app_module.create_app()) as c:
        yield c
    reset_active_provider()


# ------------------------------------------------------- the search still works

def test_the_catalogue_still_answers_without_a_site(unsited):
    """THE OWNER'S RULING. An unconfigured rig can still look M31 up: what it
    is, how bright it is, and where it is ON THE SKY. None of that depends on
    where the telescope is standing."""
    rows = unsited.get("/api/catalog?q=M31").json()
    assert isinstance(rows, list) and rows, (
        "the catalogue went empty without a site; the rows are catalogue facts "
        "and do not depend on where the rig is")
    row = rows[0]
    for field in ("name", "ra_hours", "dec_deg"):
        assert field in row, f"{field} is a catalogue fact and must survive: {row}"


def test_the_site_derived_fields_are_withheld_rather_than_invented(unsited):
    """THE DEFECT. At 0,0 these would be an altitude and an azimuth for the
    Gulf of Guinea, in a row indistinguishable from a correct one."""
    rows = unsited.get("/api/catalog?q=M31").json()
    for row in rows:
        assert "alt" not in row and "az" not in row, (
            f"an altitude was published for a rig with no site: {row}")


def test_a_real_site_still_gets_them(sited):
    """The control, and it is load-bearing: a withhold that withheld from
    everybody would pass the case above and break the product."""
    rows = sited.get("/api/catalog?q=M31").json()
    assert rows, "premise: the search found something"
    assert any("alt" in r for r in rows), (
        "no row carried an altitude for an admin at a real site")


def test_the_reason_is_told_to_someone_who_could_fix_it(unsited):
    """A holder whose numbers are missing is owed the reason, and this one is
    fixable in one screen. Without it the rows look identical to a viewer's and
    the UI cannot tell which of the two it is showing."""
    body = unsited.get("/api/catalog?q=M31&explain=1").json()
    notes = " ".join(body.get("notes", []))
    assert "no observing site" in notes, f"the notes do not say why: {notes}"
    assert "Settings" in notes, f"the notes do not say what to do: {notes}"


def test_the_ordering_does_not_claim_to_know_what_is_up(unsited):
    """`order_by_observability` sinks what cannot be pointed at. Run against
    the wrong hemisphere it does the opposite of its name, so it must not run
    at all - and it is inside the same branch as the alt/az, which is what
    this pins."""
    rows = unsited.get("/api/catalog?q=nebula").json()
    assert not any("alt" in r for r in rows), (
        "an unsited search was ordered by an observability it cannot know")


# ------------------------------------------------------ tonight has no degraded form

def test_tonight_refuses_rather_than_ranking_the_gulf_of_guinea(unsited):
    r = unsited.get("/api/catalog/tonight")
    assert r.status_code == 409, (
        f"tonight ranked the whole catalogue without a site: {r.status_code}")
    detail = r.json().get("detail") or {}
    assert detail.get("code") == "no_site", detail
    assert "Settings" in detail.get("detail", ""), detail


def test_tonight_works_at_a_real_site(sited):
    """The control for the refusal above."""
    r = sited.get("/api/catalog/tonight")
    assert r.status_code == 200, r.text
