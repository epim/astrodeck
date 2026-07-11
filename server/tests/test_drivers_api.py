"""Driver API routes (equipment-drivers spec §3.1/§3.2): CRUD + probe + the
merged describe surface. In-process via TestClient against a temp ConfigStore
(repo convention — see test_connect_api.py)."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from astrodeck.config import config_store
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)   # force fresh load from tmp
    import astrodeck.drivers as drv
    drv.invalidate()
    from astrodeck.api import app as app_module
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c


def test_drivers_crud_roundtrip(client):
    r = client.post("/api/config/drivers",
                    json={"type": "nina", "host": "astrotown.lan"})
    assert r.status_code == 200
    d = r.json()["driver"]
    assert d["id"].startswith("nina-") and d["port"] == 1888

    r = client.patch(f"/api/config/drivers/{d['id']}", json={"enabled": False})
    assert r.status_code == 200 and r.json()["driver"]["enabled"] is False

    r = client.delete(f"/api/config/drivers/{d['id']}")
    assert r.status_code == 200 and r.json()["deleted"] == d["id"]


def test_drivers_validation_and_404(client):
    assert client.post("/api/config/drivers",
                       json={"type": "asiair", "host": "h"}).status_code == 422
    assert client.post("/api/config/drivers",
                       json={"type": "nina", "host": "  "}).status_code == 422
    assert client.patch("/api/config/drivers/nope",
                        json={"port": 2}).status_code == 404
    assert client.delete("/api/config/drivers/nope").status_code == 404
    assert client.post("/api/drivers/nope/probe").status_code == 404


def test_get_drivers_returns_roles_and_implicit_rows(client):
    r = client.get("/api/drivers")
    assert r.status_code == 200
    body = r.json()
    assert "camera" in body["roles"]
    ids = {d["id"] for d in body["drivers"]}
    assert {"sim", "astrodeck", "astap"} <= ids
    sim = next(d for d in body["drivers"] if d["id"] == "sim")
    assert sim["implicit"] is True and sim["status"]["reachable"] is True


def test_probe_route_accepts_implicit_ids(client):
    assert client.post("/api/drivers/sim/probe").status_code == 200


# ============================================ RBAC redaction (post-review, item 1)
# GET /api/drivers requires only view.status, but a configured driver's row
# also carried host/port/extra verbatim -- the SAME endpoint detail
# config.backend is required to WRITE (a viewer-level endpoint/DDNS leak).
# Harness mirrors test_rbac_enforcement.py / test_sun_guard.py: install a
# FIXED principal via the active-provider slot BEFORE entering TestClient,
# with the lifespan's provider reinstall neutralized so it can't clobber it.

from astrodeck.auth import (principal_for_role, reset_active_provider,  # noqa: E402
                            set_active_provider)


class _FakeAuthProvider:
    name = "fake"

    def __init__(self, principal):
        self._principal = principal

    async def resolve(self, request):
        return self._principal


def _rbac_app(tmp_path, monkeypatch, principal):
    """Isolated app + ConfigStore with a FIXED principal installed (mirrors
    test_rbac_enforcement.py's ``_make_client``). Returns ``(config_store,
    app)`` -- the caller enters ``TestClient(app)`` itself."""
    from astrodeck.config import config_store
    monkeypatch.setattr(config_store, "_path", tmp_path / "astrodeck.json")
    monkeypatch.setattr(config_store, "_cfg", None)
    import astrodeck.drivers as drv
    drv.invalidate()
    from astrodeck.api import app as app_module
    monkeypatch.delenv(app_module.AUTH_ENV_VAR, raising=False)
    # Don't let the lifespan re-install a provider over the test-installed one.
    monkeypatch.setattr(app_module, "configure_provider_from_auth",
                        lambda _auth: app_module.get_active_provider())
    reset_active_provider()
    set_active_provider(_FakeAuthProvider(principal))
    return config_store, app_module.create_app()


@pytest.fixture(autouse=True)
def _reset_provider_after():
    """Every test in this module restores the open default afterwards, so an
    installed fixed-principal never leaks into another test module."""
    yield
    reset_active_provider()


def _fake_nina_probe(monkeypatch):
    """Avoid a real network probe of the fabricated 'astrotown.lan' host --
    these tests assert on redaction shape, not reachability."""
    import astrodeck.drivers as drv

    async def fake(host, port):
        return drv._ok([{"role": "camera", "name": "cam"}], ["autofocus"])

    monkeypatch.setitem(drv._PROBES, "nina", fake)


def test_get_drivers_redacts_host_port_extra_for_viewer(tmp_path, monkeypatch):
    """A viewer (view.status only, NOT config.backend) reading GET /api/drivers
    must not see a configured driver's host/port/extra -- status/offers and
    the top-level roles list survive untouched."""
    store, app = _rbac_app(tmp_path, monkeypatch, principal_for_role("viewer"))
    _fake_nina_probe(monkeypatch)
    store.add_driver("nina", "astrotown.lan", label="my nina")
    with TestClient(app) as c:
        r = c.get("/api/drivers")
    assert r.status_code == 200
    body = r.json()
    nina = next(d for d in body["drivers"] if d["type"] == "nina")
    assert "host" not in nina and "port" not in nina and "extra" not in nina
    assert nina["id"] and nina["label"] == "my nina" and nina["enabled"] is True
    assert nina["status"]["reachable"] is True
    assert nina["offers"]["devices"] == [{"role": "camera", "name": "cam"}]
    assert "camera" in body["roles"]              # top-level roles untouched


def test_get_drivers_full_for_config_backend_holder(tmp_path, monkeypatch):
    """A principal holding config.backend (e.g. admin) sees host/port
    verbatim -- unchanged behavior for a caller who could WRITE it anyway."""
    store, app = _rbac_app(tmp_path, monkeypatch, principal_for_role("admin"))
    _fake_nina_probe(monkeypatch)
    store.add_driver("nina", "astrotown.lan")
    with TestClient(app) as c:
        r = c.get("/api/drivers")
    nina = next(d for d in r.json()["drivers"] if d["type"] == "nina")
    assert nina["host"] == "astrotown.lan" and nina["port"] == 1888


def test_get_drivers_default_test_app_is_admin_baseline(client, monkeypatch):
    """No provider installed (the shared ``client`` fixture) -> the open
    default every other CRUD test in this file already relies on: full
    detail, matching today's pre-fix shape."""
    from astrodeck.config import config_store
    _fake_nina_probe(monkeypatch)
    config_store.add_driver("nina", "astrotown.lan")
    r = client.get("/api/drivers")
    nina = next(d for d in r.json()["drivers"] if d["type"] == "nina")
    assert nina["host"] == "astrotown.lan"
