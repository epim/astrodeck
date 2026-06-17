"""API tests for the Stage B pluggable-backend connect surface (api/app.py).

Covers the endpoints owned by the Api lane that are NEW in Stage B:

* GET ``/api/backends`` returns the ``list_backends()`` shape, ordered by name.
* POST ``/api/connect/rig`` connects a whole rig by RigSpec and reports per-role
  ``RoleResult`` tri-state; an explicit override whose role the backend can't
  fill is rejected 422, while a primary-DERIVED resolution of the same role is
  accepted.
* GET ``/api/discover/{backend}`` delegates to the named backend's ``discover``;
  an unknown backend -> 404.

Repo convention: in-process via ``TestClient`` against a temp ConfigStore; no
real rig is touched (sim is hostless). No ``unittest.mock`` - backends come from
the real registry. ``ASTRODECK_NO_AUTOCONNECT`` is set so the lifespan does not
auto-connect during these tests (they connect explicitly).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.config import ConfigStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Isolated app: config + profiles redirected to tmp, boot auto-connect OFF."""
    monkeypatch.setenv(app_module.NO_AUTOCONNECT_ENV_VAR, "1")
    temp_store = ConfigStore(path=tmp_path / "astrodeck.json")
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    from astrodeck.profiles import profiles as profile_lib
    monkeypatch.setattr(config_mod, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "config_store", temp_store)
    monkeypatch.setattr(app_module, "config_store", temp_store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    # profiles is a module singleton; redirect its storage dir to tmp.
    monkeypatch.setattr(profile_lib, "_dir", tmp_path / "profiles")

    app = app_module.create_app()
    with TestClient(app) as c:
        try:
            yield c, temp_store
        finally:
            try:
                c.post("/api/disconnect")
            except Exception:
                pass


# ----------------------------------------------------------------- GET /backends

def test_backends_lists_registry_shape_ordered(client):
    c, _ = client
    r = c.get("/api/backends")
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, list) and body
    names = [b["name"] for b in body]
    # ordered by name, and the four self-registering backends are present.
    assert names == sorted(names)
    for expected in ("sim", "nina", "native", "phd2"):
        assert expected in names, expected
    # every row carries the list_backends() shape.
    for b in body:
        assert set(b) >= {"name", "label", "roles", "discoverable"}
        assert isinstance(b["roles"], list)
        assert isinstance(b["discoverable"], bool)


# ----------------------------------------------------- POST /connect/rig (sim ok)

def test_connect_rig_sim_returns_results_and_backend_links(client):
    c, _ = client
    r = c.post("/api/connect/rig", json={"primary": "sim", "roles": {}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "summary" in body
    assert "backend_links" in body
    assert "results" in body and isinstance(body["results"], list)
    # per-role RoleResult tri-state fields.
    for rr in body["results"]:
        assert set(rr) >= {"role", "ok", "error", "attempted"}
    # at least one role came up ok (sim fills everything).
    assert any(rr["ok"] for rr in body["results"])


def test_connect_rig_unknown_primary_is_422(client):
    c, _ = client
    r = c.post("/api/connect/rig", json={"primary": "does-not-exist", "roles": {}})
    assert r.status_code == 422, r.text


# ----------------------------------------------- POST /connect/rig (reject rule)

def test_connect_rig_rejects_explicit_override_role_backend_cannot_fill(client):
    """An EXPLICIT ``safety`` -> ``nina`` override is the one role NINA can't fill,
    so the server rejects it 422 before any connection is attempted."""
    c, _ = client
    from astrodeck.devices.backend import get_backend
    assert "safety" not in get_backend("nina").roles  # premise of the test
    r = c.post("/api/connect/rig", json={
        "primary": "sim",
        "roles": {"safety": {"backend": "nina"}},
    })
    assert r.status_code == 422, r.text
    assert "safety" in r.json()["detail"]


def test_connect_rig_unknown_override_backend_is_422(client):
    c, _ = client
    r = c.post("/api/connect/rig", json={
        "primary": "sim",
        "roles": {"camera": {"backend": "no-such-backend"}},
    })
    assert r.status_code == 422, r.text


def test_connect_rig_primary_derived_role_is_accepted(client):
    """A ``nina`` primary that resolves ``switch`` from the primary (NOT an
    explicit override) must NOT be rejected - the reject-rule is explicit-only.

    NINA is host-based; the orchestrator degrades a per-role open failure rather
    than raising, so this asserts the request is ACCEPTED (no 422 / 502), not
    that NINA is reachable in the test environment."""
    c, _ = client
    from astrodeck.devices.backend import get_backend
    assert "switch" in get_backend("nina").roles  # premise: nina fills switch
    r = c.post("/api/connect/rig", json={"primary": "nina", "roles": {}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "results" in body and "backend_links" in body


# --------------------------------------------------------- GET /discover/{backend}

def test_discover_backend_delegates(client):
    c, _ = client
    # sim is discoverable and returns a JSON-able list without network work.
    r = c.get("/api/discover/sim")
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)


def test_discover_unknown_backend_is_404(client):
    c, _ = client
    r = c.get("/api/discover/no-such-backend")
    assert r.status_code == 404, r.text


# ------------------------------------------- NON-BREAKING: legacy connect routes

def test_legacy_connect_sim_still_works(client):
    """The pre-Stage-B /api/connect/sim + /api/disconnect path must keep working
    byte-for-byte (the live rig uses it). Stage B is purely additive."""
    c, _ = client
    r = c.post("/api/connect/sim")
    assert r.status_code == 200, r.text
    assert c.get("/api/status").json().get("mode") == "sim"
    d = c.post("/api/disconnect")
    assert d.status_code == 200, d.text
    assert d.json() == {"ok": True}
    assert c.get("/api/status").json().get("mode") == "none"


def test_legacy_connect_alpaca_validation_unchanged(client):
    """The legacy /api/connect/alpaca 422 guard (safety role requires a
    safetymonitor dev_type) is untouched by Stage B."""
    c, _ = client
    r = c.post("/api/connect/alpaca", json={
        "role": "safety", "host": "127.0.0.1", "port": 11111,
        "dev_type": "telescope", "dev_num": 0,
    })
    assert r.status_code == 422, r.text


# ----------------------------------- profiles CRUD carries the new RigSpec fields

def test_profiles_crud_roundtrips_rigspec_fields(client):
    """Create/read/list/rename/delete over the existing /api/profiles endpoints,
    and confirm the NEW Stage B fields (``primary_backend`` + per-device
    ``extra``) ride through the Profile body untouched (additive - pydantic
    defaults fill old shapes)."""
    c, _ = client
    # create: a native-primary rig with a per-device extra payload.
    r = c.post("/api/profiles", json={
        "name": "Backyard Rig",
        "primary_backend": "native",
        "devices": [{
            "role": "camera", "backend": "native", "host": "10.0.0.5",
            "port": 11111, "dev_type": "camera", "dev_num": 0,
            "name": "ASI2600", "extra": {"cooler": True},
        }],
    })
    assert r.status_code == 200, r.text
    pid = r.json()["id"]

    # read back: the new fields survived persistence.
    got = c.get(f"/api/profiles/{pid}").json()
    assert got["primary_backend"] == "native"
    assert got["devices"][0]["extra"] == {"cooler": True}

    # list includes it.
    rows = c.get("/api/profiles").json()
    assert any(p["id"] == pid for p in rows)

    # rename (PATCH) keeps the id, changes the name.
    r = c.patch(f"/api/profiles/{pid}", json={"name": "Renamed Rig"})
    assert r.status_code == 200, r.text
    assert c.get(f"/api/profiles/{pid}").json()["name"] == "Renamed Rig"

    # delete removes it (404 afterwards).
    assert c.delete(f"/api/profiles/{pid}").status_code == 200
    assert c.get(f"/api/profiles/{pid}").status_code == 404
