"""API tests for POST /api/profiles/{id}/clear-overrides (#129).

The provenance block added in the same change can finally SAY "profile X pins
the built-in simulator for polar alignment". This route is what makes that
disclosure actionable: without it the user's only recourse is to delete the
profile, because the Profiles tab has no editor and the one whole-profile write
takes a payload the client can only get from a wire-REDACTED GET.

The load-bearing assertion here is not that the file changed — ``test_profiles``
covers the store — it is that ``/api/config``'s ``effective`` block AGREES on the
next read. The active profile is served from an in-memory cache that
``providers.override_with_layer`` and ``profiles.resolve_optics`` read; a stale
entry would leave the cleared pin still winning while the console, having
re-fetched the config, showed it as gone. That is the same console-disagrees-
with-rig failure the whole change exists to end, so it is pinned rather than
assumed.

Repo convention: TestClient against a temp ConfigStore + tmp profiles dir, no
real rig, boot auto-connect OFF.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.config import ConfigStore, Optics
from astrodeck.profiles import Profile, ProfileDevice


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(app_module.NO_AUTOCONNECT_ENV_VAR, "1")
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    from astrodeck.profiles import profiles as profile_lib
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(profile_lib, "_dir", tmp_path / "profiles")

    app = app_module.create_app()
    with TestClient(app) as c:
        yield c, temp_store, profile_lib


def _active_profile(profile_lib, store, **kw) -> str:
    """Persist a profile and make it the ACTIVE one. Activation is done by
    writing the pointer directly rather than through the connect route: this
    file is about the layering rule, and a real connect would drag a rig into a
    test that has nothing to say about one."""
    prof = Profile(name="Rig", primary_backend="sim", **kw)
    profile_lib.save(prof)
    store.set_active_profile(prof.id)
    app_module.hub.invalidate_profile_cache()
    return prof.id


def _effective(c, key: str) -> dict:
    return c.get("/api/config").json()["effective"][key]


def test_clearing_a_pin_flips_the_reported_layer(client):
    """The whole point: the console must stop showing the pin on the very next
    read. A cache that outlived the write would keep the rig on the simulator
    while the UI reported it cleared."""
    c, store, lib = client
    pid = _active_profile(lib, store, providers={"polar_align": "sim"})

    before = _effective(c, "providers.polar_align")
    assert before["layer"] == "profile" and before["value"] == "sim"

    r = c.post(f"/api/profiles/{pid}/clear-overrides",
               json={"providers": ["polar_align"]})
    assert r.status_code == 200, r.text

    after = _effective(c, "providers.polar_align")
    assert after["layer"] != "profile"
    assert after["profile"] is None


def test_clearing_optics_flips_every_optics_key_at_once(client):
    """A profile optics block wins WHOLE, so clearing it has to release all
    seven fields — including the ones the profile never deliberately set, which
    is exactly why they were being overridden in the first place."""
    c, store, lib = client
    pid = _active_profile(lib, store, optics=Optics(focal_length_mm=250.0))
    store.set_optics(Optics(focal_length_mm=530.0))

    assert _effective(c, "optics.focal_length_mm")["value"] == 250.0
    assert _effective(c, "optics.telescope_name")["layer"] == "profile"

    r = c.post(f"/api/profiles/{pid}/clear-overrides", json={"optics": True})
    assert r.status_code == 200, r.text

    focal = _effective(c, "optics.focal_length_mm")
    assert focal["value"] == 530.0 and focal["layer"] != "profile"
    assert _effective(c, "optics.telescope_name")["layer"] != "profile"


def test_clearing_one_capability_leaves_the_others_pinned(client):
    c, store, lib = client
    pid = _active_profile(lib, store,
                          providers={"polar_align": "sim", "solve": "astap"})

    c.post(f"/api/profiles/{pid}/clear-overrides",
           json={"providers": ["polar_align"]})

    assert _effective(c, "providers.polar_align")["layer"] != "profile"
    solve = _effective(c, "providers.solve")
    assert solve["layer"] == "profile" and solve["value"] == "astap"


def test_the_row_returned_is_the_fresh_one(client):
    """The client re-renders from this row; returning the pre-clear state would
    show the pin as still present until something else refetched."""
    c, store, lib = client
    pid = _active_profile(lib, store, providers={"guide": "sim"},
                          optics=Optics(focal_length_mm=250.0))
    row = c.post(f"/api/profiles/{pid}/clear-overrides",
                 json={"providers": ["guide"], "optics": True}).json()
    assert row["providers"] is None
    assert row["optics"] is None


def test_missing_profile_is_404_not_500(client):
    """profiles.clear_overrides resolves through safe_id_path, which raises
    KeyError on a refused id; the route must map it like every sibling does
    rather than leaking a traceback with absolute paths in it."""
    c, _, _ = client
    # Backslash shapes only: an encoded FORWARD slash is normalised into a real
    # path separator before routing, so `..%2F..%2Fx` never reaches this route
    # at all (405) and would be testing httpx rather than the guard.
    for bad in ("does-not-exist", "..%5Cx", "%2E%2E%5C%2E%2E%5Cpwned"):
        r = c.post(f"/api/profiles/{bad}/clear-overrides", json={"optics": True})
        assert r.status_code == 404, (bad, r.status_code, r.text)


def test_an_empty_body_changes_nothing(client):
    """Both fields default to "change nothing", so a malformed request is inert
    rather than destructive."""
    c, store, lib = client
    pid = _active_profile(lib, store, providers={"guide": "sim"})
    r = c.post(f"/api/profiles/{pid}/clear-overrides", json={})
    assert r.status_code == 200, r.text
    assert _effective(c, "providers.guide")["layer"] == "profile"


def test_device_secrets_survive_the_clear(client):
    """The reason this is a server route at all. A client read-modify-write
    would start from the REDACTED GET and write the blanks back."""
    c, store, lib = client
    pid = _active_profile(
        lib, store, providers={"guide": "sim"},
        devices=[ProfileDevice(role="camera",
                               extra={"password": "hunter2", "host": "10.0.0.5"})])
    c.post(f"/api/profiles/{pid}/clear-overrides", json={"providers": ["guide"]})
    # at rest, not over the wire — the wire copy is redacted by design
    assert lib.get(pid).devices[0].extra["password"] == "hunter2"
