"""Survey-cutout proxy tests (Sky-Atlas Owner B, design spec §4.3 / §9).

These pin the unit-critical behavior that the spec calls out explicitly:
  * ``ra_deg = ra_hours * 15`` — a 15x error lands a random field (C2-#8a).
  * the hips2fits query is ``projection=TAN``, ``coordsys=icrs`` with **no `rot`
    param** (C1-A4 / C2-#8).
  * width is clamped to [256, 1200].
  * a successful fetch is disk-cached; a second request is served from cache
    without touching the network.
  * an upstream failure returns ``503`` with the schematic fallback payload.

httpx is fully mocked so the suite never reaches the network.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

import astrodeck.catalog.survey as survey_mod


class _FakeResponse:
    def __init__(self, content: bytes = b"\xff\xd8jpegbytes", status: int = 200):
        self.content = content
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=None, response=None)  # type: ignore[arg-type]


class _FakeClient:
    """Captures the params of the last GET so tests can assert the URL math."""

    last_params: dict | None = None
    fail: bool = False

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None):
        _FakeClient.last_params = params
        if _FakeClient.fail:
            raise httpx.ConnectError("down")
        return _FakeResponse()


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Redirect the disk cache to tmp so tests never pollute captures/_survey.
    monkeypatch.setattr(survey_mod, "_SURVEY_CACHE_DIR", tmp_path / "_survey")
    monkeypatch.setattr(survey_mod.httpx, "AsyncClient", _FakeClient)
    _FakeClient.last_params = None
    _FakeClient.fail = False

    # Mount only the survey router so the test is independent of other lanes.
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(survey_mod.router)
    with TestClient(app) as c:
        yield c


def test_ra_hours_to_degrees_and_no_rot(client):
    # ra=1.0h must become ra=15.0 deg; TAN + icrs; NO rot param.
    r = client.get("/api/survey/cutout.jpg", params={"ra": 1.0, "dec": 41.0, "fov": 1.5})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    p = _FakeClient.last_params
    assert p is not None
    assert float(p["ra"]) == pytest.approx(15.0)  # 1.0h * 15 == 15 deg
    assert p["projection"] == "TAN"
    assert p["coordsys"] == "icrs"
    assert p["format"] == "jpg"
    assert "rot" not in p  # spec: no PA param


def test_width_clamped(client):
    client.get("/api/survey/cutout.jpg", params={"ra": 2.0, "dec": 0, "fov": 1, "width": 5000})
    assert int(_FakeClient.last_params["width"]) == 1200
    client.get("/api/survey/cutout.jpg", params={"ra": 2.0, "dec": 0, "fov": 1, "width": 10})
    assert int(_FakeClient.last_params["width"]) == 256


def test_cache_hit_skips_network(client):
    params = {"ra": 0.7123, "dec": 41.27, "fov": 1.5}
    r1 = client.get("/api/survey/cutout.jpg", params=params)
    assert r1.status_code == 200
    assert "max-age=86400" in r1.headers.get("cache-control", "")
    # Second identical request: force the network to fail; cache must still serve.
    _FakeClient.fail = True
    r2 = client.get("/api/survey/cutout.jpg", params=params)
    assert r2.status_code == 200  # served from disk cache, not the (now-failing) net


def test_upstream_failure_returns_503_schematic(client):
    _FakeClient.fail = True
    r = client.get("/api/survey/cutout.jpg", params={"ra": 5.5, "dec": -24, "fov": 2})
    assert r.status_code == 503
    body = r.json()["detail"]
    assert body["fallback"] == "schematic"


def test_unknown_survey_is_422(client):
    # survey is whitelisted (Literal) like stretch — an unknown id 422s rather
    # than polluting the cache / proxying an arbitrary HiPS string.
    r = client.get(
        "/api/survey/cutout.jpg",
        params={"ra": 1.0, "dec": 41.0, "fov": 1.5, "survey": "CDS/P/evil"},
    )
    assert r.status_code == 422, r.text


def test_cache_key_quantization_is_stable():
    # Two sub-3-arcsec-different RAs collapse onto the same cache key; a coarser
    # nudge does not (finer key than the draft — C2-#15).
    k1 = survey_mod._cache_key(0.71230, 41.270, 1.50, 768, "CDS/P/DSS2/color", "linear")
    k2 = survey_mod._cache_key(0.71235, 41.270, 1.50, 768, "CDS/P/DSS2/color", "linear")
    k3 = survey_mod._cache_key(0.80000, 41.270, 1.50, 768, "CDS/P/DSS2/color", "linear")
    assert k1 == k2
    assert k1 != k3
