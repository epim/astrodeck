"""Weather tile proxy tests (weather spec §6/§14): Literal/range 422, disabled
= 404 no-store + zero-httpx (_Boom), verbatim IEM slugs, TTL disk cache hit
skips the network, PNG-magic validation, failure never cached, RBAC gate."""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

import astrodeck.api.app as app_module
from astrodeck.auth import (principal_for_role, reset_active_provider,
                            set_active_provider)
from astrodeck.config import ConfigStore

_PNG = b"\x89PNG\r\n\x1a\n" + b"tilebytes"


class _FakeResp:
    def __init__(self, content: bytes, status: int = 200):
        self.content = content
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=None, response=None)  # type: ignore[arg-type]


class _FakeTileClient:
    body = _PNG
    fail = False
    calls = 0
    last_url = None

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        type(self).calls += 1
        type(self).last_url = url
        if type(self).fail:
            raise httpx.ConnectError("down")
        return _FakeResp(type(self).body)


class _BoomTile:
    def __init__(self, *a, **kw):
        raise AssertionError("httpx client constructed while weather disabled")


class _FixedProvider:
    name = "fixed"

    def __init__(self, principal):
        self._principal = principal

    async def resolve(self, request):
        return self._principal


@pytest.fixture
def client(tmp_path, monkeypatch):
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    import astrodeck.weather as weather_mod
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.cfg().weather.enabled = True             # site stays default: the
    monkeypatch.setattr(config_mod, "config_store", store)   # poller no-ops,
    monkeypatch.setattr(hub_mod, "config_store", store)      # only the proxy
    monkeypatch.setattr(app_module, "config_store", store)   # fetches
    monkeypatch.setattr(weather_mod, "config_store", store)
    monkeypatch.setattr(hub_mod, "CAPTURE_DIR", tmp_path / "captures")
    monkeypatch.setattr(app_module, "_WEATHER_TILE_CACHE_DIR",
                        tmp_path / "_weather_tiles")
    monkeypatch.setattr(app_module.httpx, "AsyncClient", _FakeTileClient)
    _FakeTileClient.body = _PNG
    _FakeTileClient.fail = False
    _FakeTileClient.calls = 0
    _FakeTileClient.last_url = None
    reset_active_provider()                        # open default => admin
    app = app_module.create_app()
    with TestClient(app) as c:
        yield c, store
    reset_active_provider()


def test_layer_and_range_validation_422(client):
    c, store = client
    assert c.get("/api/weather/tile/wind/5/1/1.png").status_code == 422
    assert c.get("/api/weather/tile/radar/2/1/1.png").status_code == 422
    assert c.get("/api/weather/tile/radar/12/1/1.png").status_code == 422
    assert c.get("/api/weather/tile/radar/5/32/1.png").status_code == 422  # x >= 2**5
    assert c.get("/api/weather/tile/radar/5/1/-1.png").status_code == 422


def test_disabled_404_no_store_zero_httpx(client, monkeypatch):
    c, store = client
    store.cfg().weather.enabled = False
    monkeypatch.setattr(app_module.httpx, "AsyncClient", _BoomTile)
    r = c.get("/api/weather/tile/radar/5/8/12.png")
    assert r.status_code == 404
    assert r.headers.get("cache-control") == "no-store"


def test_tile_served_verbatim_slugs_and_ttl_cache(client):
    c, store = client
    r1 = c.get("/api/weather/tile/radar/5/8/12.png")
    assert r1.status_code == 200
    assert r1.headers["cache-control"] == "private, max-age=240"
    assert r1.content == _PNG
    # verbatim live-verified slug (spec §6) in the hardcoded upstream URL
    assert _FakeTileClient.last_url == (
        "https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/"
        "nexrad-n0q-900913/5/8/12.png")
    # TTL cache hit skips the network entirely
    _FakeTileClient.fail = True
    r2 = c.get("/api/weather/tile/radar/5/8/12.png")
    assert r2.status_code == 200 and _FakeTileClient.calls == 1
    _FakeTileClient.fail = False
    r3 = c.get("/api/weather/tile/satellite/5/8/12.png")
    assert r3.status_code == 200
    assert r3.headers["cache-control"] == "private, max-age=600"
    assert _FakeTileClient.last_url == (
        "https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/"
        "goes_east_fulldisk_ch13/5/8/12.png")


def test_png_validation_and_failure_not_cached(client):
    c, store = client
    _FakeTileClient.body = b"\xff\xd8not-a-png"    # JPEG SOI, not PNG magic
    r = c.get("/api/weather/tile/radar/6/10/20.png")
    assert r.status_code == 502
    assert r.headers.get("cache-control") == "no-store"
    _FakeTileClient.body = _PNG
    r2 = c.get("/api/weather/tile/radar/6/10/20.png")   # failure was NOT cached
    assert r2.status_code == 200 and r2.content == _PNG


def test_upstream_down_502_no_store(client):
    c, store = client
    _FakeTileClient.fail = True
    r = c.get("/api/weather/tile/satellite/7/40/50.png")
    assert r.status_code == 502
    assert r.headers.get("cache-control") == "no-store"


def test_tile_route_view_site_precise_gated(client):
    """Radar tiles centered on the site reveal the site area — holders only
    (spec §6/§8)."""
    c, store = client
    set_active_provider(_FixedProvider(principal_for_role("viewer")))
    assert c.get("/api/weather/tile/radar/5/8/12.png").status_code == 403
    set_active_provider(_FixedProvider(principal_for_role("operator")))
    assert c.get("/api/weather/tile/radar/5/8/12.png").status_code == 403
