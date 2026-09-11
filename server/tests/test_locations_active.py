"""``AppConfig.active_location_id``: WHICH saved location is live (#D-FU-1).

The Sky hub's site picker used to keep this in the browser
(``astrodeck-next-sky-site`` in localStorage), which is the owner's rule about
rig data being broken in the smallest possible way: the rig knew which
coordinates it was using and could not say which SAVED ENTRY they came from, so
a second phone showed no site selected while the mount pointed from one, and
clearing a browser's storage silently orphaned the pointer.

Two seams, and both are in ``api/app.py`` rather than ``locations.py``:
``POST /api/locations/{id}/apply`` sets it INSIDE the same ``AppConfig``
mutation that writes the site and the horizon, and ``DELETE /api/locations/{id}``
clears it when it is the one going away.

Repo convention (tests/test_locations_horizon.py): monkeypatch + TestClient, NO
unittest.mock, an isolated ConfigStore and LocationStore per test.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.auth import (CAP_CONFIG_SAFETY, CAP_CONFIG_SITE_OPTICS,
                            CAP_VIEW_STATUS, Principal, reset_active_provider,
                            set_active_provider)
from astrodeck.config import ConfigStore
from astrodeck.locations import LocationStore


# --------------------------------------------------------------------- harness

def _make_client(tmp_path, monkeypatch):
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    import astrodeck.locations as loc_mod
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    monkeypatch.setattr(app_module, "configure_provider_from_auth",
                        lambda _auth: app_module.get_active_provider())
    locs = LocationStore(path=tmp_path / "locations.json")
    monkeypatch.setattr(loc_mod, "location_store", locs)
    monkeypatch.setattr(app_module, "location_store", locs)
    reset_active_provider()
    return temp_store, locs, app_module.create_app()


class _FakeProvider:
    name = "fake"

    def __init__(self, principal):
        self._principal = principal

    async def resolve(self, request):
        return self._principal


def _install(principal):
    set_active_provider(_FakeProvider(principal))


@pytest.fixture(autouse=True)
def _reset_provider_after():
    yield
    reset_active_provider()


def _writer() -> Principal:
    """Applying a location with a horizon needs the safety cap as well;
    ``view.status`` is the floor ``GET /api/config`` reads through."""
    return Principal(role="custom", email=None, jti=None,
                     caps=frozenset({CAP_VIEW_STATUS, CAP_CONFIG_SITE_OPTICS,
                                     CAP_CONFIG_SAFETY}))


_BACKYARD = {"name": "Backyard", "latitude": 40.5, "longitude": -74.25,
             "elevation_m": 12.0, "horizon_points": [[0.0, 22.0]]}
_RIDGE = {"name": "Ridge", "latitude": 41.0, "longitude": -74.0,
          "elevation_m": 300.0}


# ------------------------------------------------------------------ the tests

def test_apply_records_which_location_is_live_and_a_second_apply_replaces_it(
        tmp_path, monkeypatch):
    """The pointer moves with the coordinates, in ONE save.

    ``version`` bumping exactly once per apply is the assertion behind the
    atomicity claim: two saves would mean a window in which the new site is
    live against a pointer still naming the old one.
    """
    cfg, _locs, app = _make_client(tmp_path, monkeypatch)
    _install(_writer())
    with TestClient(app) as c:
        assert cfg.cfg().active_location_id is None, (
            "a rig that has applied nothing points at nothing")

        first = c.post("/api/locations", json=_BACKYARD).json()["id"]
        second = c.post("/api/locations", json=_RIDGE).json()["id"]

        before = cfg.cfg().version
        r = c.post(f"/api/locations/{first}/apply")
        assert r.status_code == 200, r.text
        assert cfg.cfg().active_location_id == first
        assert cfg.cfg().site.latitude == pytest.approx(40.5)
        assert cfg.cfg().version == before + 1, (
            "the pointer and the coordinates must land in ONE version bump")

        assert c.post(f"/api/locations/{second}/apply").status_code == 200
        assert cfg.cfg().active_location_id == second
        assert cfg.cfg().site.latitude == pytest.approx(41.0)

        # ...and it is ON THE WIRE, so a second client shows the right row
        # selected instead of guessing from the coordinates. It rides
        # ``GET /api/config`` (through ``redacted``) rather than a route of its
        # own, and it is SERVER-OWNED: ``ConfigPatchBody`` does not model it, so
        # a client cannot point it at a location it never applied.
        echoed = c.get("/api/config")
        assert echoed.status_code == 200, echoed.text
        assert echoed.json()["active_location_id"] == second


def test_deleting_the_live_location_clears_the_pointer_but_not_the_site(
        tmp_path, monkeypatch):
    """A pointer to a location that no longer exists is worse than no pointer:
    the picker shows a name with nothing behind it and no way to clear it.

    THE COORDINATES STAY. Deleting the saved entry is not a request to forget
    where the rig is standing - the mount is still there, the horizon the
    engine gates slews with is still the one traced at it, and blanking
    ``config.site`` here would take both.
    """
    cfg, _locs, app = _make_client(tmp_path, monkeypatch)
    _install(_writer())
    with TestClient(app) as c:
        lid = c.post("/api/locations", json=_BACKYARD).json()["id"]
        assert c.post(f"/api/locations/{lid}/apply").status_code == 200
        assert cfg.cfg().active_location_id == lid

        assert c.delete(f"/api/locations/{lid}").status_code == 200
        assert cfg.cfg().active_location_id is None
        assert cfg.cfg().site.latitude == pytest.approx(40.5), (
            "deleting the saved entry moved the rig")
        assert cfg.cfg().safety.horizon == [(0.0, 22.0)], (
            "deleting the saved entry erased the safety floor")


def test_deleting_a_different_location_leaves_the_pointer_alone(
        tmp_path, monkeypatch):
    """The half a naive ``active_location_id = None`` in the delete handler gets
    wrong: tidying up an unused saved site would silently deselect the one the
    rig is actually standing at."""
    cfg, _locs, app = _make_client(tmp_path, monkeypatch)
    _install(_writer())
    with TestClient(app) as c:
        live = c.post("/api/locations", json=_BACKYARD).json()["id"]
        spare = c.post("/api/locations", json=_RIDGE).json()["id"]
        assert c.post(f"/api/locations/{live}/apply").status_code == 200

        before = cfg.cfg().version
        assert c.delete(f"/api/locations/{spare}").status_code == 200
        assert cfg.cfg().active_location_id == live
        assert cfg.cfg().version == before, (
            "a delete that changes nothing must not bump the config version, "
            "or every open client loses its field-level write token for free")


def test_typing_coordinates_into_the_site_clears_the_pointer(
        tmp_path, monkeypatch):
    """"None when the coordinates were typed in" is the pointer's contract, and
    ``PUT /api/site`` is the route that types them in.

    Left alone, the pointer outlives the site it described: the Sky hub shows
    BACKYARD selected while the rig stands at the numbers somebody just typed,
    which is a label contradicting the coordinates printed beside it -- and the
    obvious repair (press the selected row again) would move the mount back."""
    cfg, _locs, app = _make_client(tmp_path, monkeypatch)
    _install(_writer())
    with TestClient(app) as c:
        lid = c.post("/api/locations", json=_BACKYARD).json()["id"]
        assert c.post(f"/api/locations/{lid}/apply").status_code == 200
        assert cfg.cfg().active_location_id == lid

        r = c.put("/api/site", json={"site": {
            "name": "Somewhere else", "latitude": 44.0, "longitude": -71.0,
            "elevation_m": 100.0}})
        assert r.status_code == 200, r.text
        assert cfg.cfg().active_location_id is None, (
            "the site moved and the pointer still names the location it came "
            "from")
        assert cfg.cfg().site.latitude == pytest.approx(44.0)


def test_renaming_the_site_in_place_keeps_the_pointer(tmp_path, monkeypatch):
    """The other direction, so the clear above cannot be a blanket one: a save
    that corrects the name or the elevation without moving the rig is an edit to
    how the same spot is DESCRIBED, and deselecting the applied location there
    would cost the pointer to a typo fix."""
    cfg, _locs, app = _make_client(tmp_path, monkeypatch)
    _install(_writer())
    with TestClient(app) as c:
        lid = c.post("/api/locations", json=_BACKYARD).json()["id"]
        assert c.post(f"/api/locations/{lid}/apply").status_code == 200

        r = c.put("/api/site", json={"site": {
            "name": "Backyard (east lawn)",
            "latitude": _BACKYARD["latitude"],
            "longitude": _BACKYARD["longitude"], "elevation_m": 13.0}})
        assert r.status_code == 200, r.text
        assert cfg.cfg().active_location_id == lid, (
            "a rename with the coordinates unchanged deselected the location")


def test_moving_the_active_row_clears_the_pointer(tmp_path, monkeypatch):
    """``PUT /api/locations/{id}`` can rewrite the coordinates of the very row
    the site was applied from, and it deliberately does NOT push them into
    ``config.site`` -- editing the library is not a request to move the mount.
    The pointer is then naming a row whose numbers are not the ones the rig is
    using, which is the same false claim ``set_site`` clears.

    Graded on ``ConfigStore.clear_active_location``, which is the half of this
    that lives in config.py. The route half is one call in ``api/app.py``'s
    ``update_location`` (owned by FIX-S2); this pins the behaviour it needs."""
    cfg, _locs, app = _make_client(tmp_path, monkeypatch)
    _install(_writer())
    with TestClient(app) as c:
        live = c.post("/api/locations", json=_BACKYARD).json()["id"]
        spare = c.post("/api/locations", json=_RIDGE).json()["id"]
        assert c.post(f"/api/locations/{live}/apply").status_code == 200

    before = cfg.cfg().version
    assert cfg.clear_active_location(spare) is False, (
        "editing an unused saved site must not deselect the live one")
    assert cfg.cfg().active_location_id == live
    assert cfg.cfg().version == before, (
        "a clear that changed nothing bumped the config version, which costs "
        "every open client its field-level write token")

    assert cfg.clear_active_location(live) is True
    assert cfg.cfg().active_location_id is None
    assert cfg.cfg().version == before + 1
    assert cfg.cfg().site.latitude == pytest.approx(40.5), (
        "clearing the pointer moved the rig")


def test_an_old_config_with_no_pointer_reads_as_none(tmp_path, monkeypatch):
    """The field is additive: a config written before it existed loads with the
    pointer unset rather than failing validation."""
    path = tmp_path / "astrodeck.json"
    path.write_text('{"version": 3, "site": {"name": "Old", "latitude": 1.0, '
                    '"longitude": 2.0}}', encoding="utf-8")
    store = ConfigStore(path=path)
    assert store.cfg().active_location_id is None
    assert store.cfg().site.name == "Old"
