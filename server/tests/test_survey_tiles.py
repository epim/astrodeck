"""Tests for the on-demand HiPS tile route (tile-engine spec §1, §7)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import astrodeck.catalog.tiles as tiles_mod
import astrodeck.catalog.survey_pack as pack_mod

_JPEG = b"\xff\xd8\xff\xe0" + b"tilebytes" + b"0" * 40


class _Boom:
    """httpx.AsyncClient stand-in whose CONSTRUCTION fails the test (spec §7)."""
    def __init__(self, *a, **kw):
        raise AssertionError("httpx client constructed with online_fetch=False")


def _mock_transport(counter: dict | None = None, *, jpeg: bool = True, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        if counter is not None:
            counter["n"] = counter.get("n", 0) + 1
        if status >= 400:
            return httpx.Response(status)
        return httpx.Response(200, content=_JPEG if jpeg else b"<html>nope</html>")
    return httpx.MockTransport(handler)


def _make_client(tmp_path, monkeypatch, *, online: bool):
    from astrodeck.config import ConfigStore
    monkeypatch.setattr(pack_mod, "PACK_ROOT", tmp_path / "_survey_pack")
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.cfg().survey.online_fetch = online
    monkeypatch.setattr(tiles_mod, "config_store", store)
    tiles_mod._TILE_TRANSPORT = None
    app = FastAPI()
    app.include_router(tiles_mod.router)
    return TestClient(app)


@pytest.fixture
def offline(tmp_path, monkeypatch):
    return _make_client(tmp_path, monkeypatch, online=False)


@pytest.fixture
def online(tmp_path, monkeypatch):
    return _make_client(tmp_path, monkeypatch, online=True)


def test_pack_hit_serves_immutable(offline):
    pack = pack_mod.pack_dir("dss2color")
    p = pack_mod.tile_path(pack, 3, 5)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(_JPEG)
    r = offline.get("/api/survey/tile/dss2color/3/5.jpg")
    assert r.status_code == 200
    assert r.content == _JPEG
    assert r.headers["Cache-Control"] == "public, max-age=31536000, immutable"


def test_offline_miss_404_no_store_no_client(offline, monkeypatch):
    monkeypatch.setattr(tiles_mod.httpx, "AsyncClient", _Boom)
    r = offline.get("/api/survey/tile/dss2color/4/17.jpg")
    assert r.status_code == 404
    assert r.json()["detail"] == "tile unavailable"
    assert r.headers["Cache-Control"] == "no-store"


def test_unknown_slug_404(offline):
    r = offline.get("/api/survey/tile/nope/0/0.jpg")
    assert r.status_code == 404
    assert r.json()["detail"] == "unknown slug"


def test_order_out_of_range_422(offline):
    assert offline.get("/api/survey/tile/dss2color/10/0.jpg").status_code == 422


def test_npix_out_of_range_422(offline):
    # order 0 has 12 tiles -> npix 12 is out of range
    assert offline.get("/api/survey/tile/dss2color/0/12.jpg").status_code == 422


def test_online_miss_fetches_writes_and_serves(online):
    counter: dict = {}
    tiles_mod._TILE_TRANSPORT = _mock_transport(counter)
    r = online.get("/api/survey/tile/dss2color/4/17.jpg")
    assert r.status_code == 200 and r.content == _JPEG
    assert r.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert counter["n"] == 1
    # file persisted -> a second GET is a pack hit (no new upstream call)
    assert pack_mod.tile_path(pack_mod.pack_dir("dss2color"), 4, 17).read_bytes() == _JPEG
    r2 = online.get("/api/survey/tile/dss2color/4/17.jpg")
    assert r2.status_code == 200 and counter["n"] == 1


def test_online_upstream_fail_404(online):
    tiles_mod._TILE_TRANSPORT = _mock_transport(status=500)
    r = online.get("/api/survey/tile/dss2color/4/17.jpg")
    assert r.status_code == 404
    assert r.headers["Cache-Control"] == "no-store"


def test_online_non_jpeg_404(online):
    tiles_mod._TILE_TRANSPORT = _mock_transport(jpeg=False)
    assert online.get("/api/survey/tile/dss2color/4/17.jpg").status_code == 404


def test_disk_guard_serves_without_caching(online, monkeypatch):
    tiles_mod._TILE_TRANSPORT = _mock_transport()
    monkeypatch.setattr(tiles_mod.shutil, "disk_usage",
                        lambda p: SimpleNamespace(free=1000))  # < 200 MB
    r = online.get("/api/survey/tile/dss2color/4/17.jpg")
    assert r.status_code == 200 and r.content == _JPEG
    # served but NOT written to the pack tree (Pi-card guard, spec §1)
    assert not pack_mod.tile_path(pack_mod.pack_dir("dss2color"), 4, 17).exists()


def test_single_flight_coalesces_concurrent_misses(tmp_path, monkeypatch):
    from astrodeck.config import ConfigStore
    monkeypatch.setattr(pack_mod, "PACK_ROOT", tmp_path / "_survey_pack")
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.cfg().survey.online_fetch = True
    monkeypatch.setattr(tiles_mod, "config_store", store)
    counter: dict = {}
    tiles_mod._TILE_TRANSPORT = _mock_transport(counter)
    app = FastAPI()
    app.include_router(tiles_mod.router)

    async def hit(client):
        return await client.get("/api/survey/tile/dss2color/6/1234.jpg")

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            rs = await asyncio.gather(*(hit(c) for _ in range(6)))
        return rs

    rs = asyncio.run(run())
    assert all(r.status_code == 200 for r in rs)
    assert counter["n"] == 1  # leader fetched; waiters served the file it wrote
