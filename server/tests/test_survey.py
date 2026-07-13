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

import os
import time

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
    # ra=1.0h must become ~15.0 deg (then grid-snapped); TAN + icrs; NO rot param.
    r = client.get("/api/survey/cutout.jpg", params={"ra": 1.0, "dec": 41.0, "fov": 1.5})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    p = _FakeClient.last_params
    assert p is not None
    snapped_fov = float(p["fov"])
    step = snapped_fov / survey_mod._CENTER_STEPS_PER_FOV
    # hours -> degrees, then snapped to the fov-scaled grid (within half a step)
    assert abs(float(p["ra"]) - 15.0) <= step / 2 + 1e-9
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
    # Coords inside one fov/20 bucket collapse onto ONE key; a >1-step nudge
    # does not. Keys are pure functions of INTEGER indices — no float flip.
    keys = set()
    for i in range(100):
        *_geom, idx = survey_mod._snap_geometry(0.712300 + i * 1e-7, 41.270, 1.5)
        keys.add(survey_mod._cache_key(idx, 768, "CDS/P/DSS2/color", "linear"))
    assert len(keys) == 1

    _ra, _dec, fov, idx1 = survey_mod._snap_geometry(1.0, 40.0, 1.5)
    step = fov / survey_mod._CENTER_STEPS_PER_FOV
    *_g2, idx2 = survey_mod._snap_geometry(1.0 + (step * 0.4) / 15.0, 40.0, 1.5)
    *_g3, idx3 = survey_mod._snap_geometry(1.0 + (step * 1.5) / 15.0, 40.0, 1.5)
    assert idx1 == idx2       # within one step -> same bucket
    assert idx1 != idx3       # 1.5 steps away -> different bucket
    k1 = survey_mod._cache_key(idx1, 768, "CDS/P/DSS2/color", "linear")
    k3 = survey_mod._cache_key(idx3, 768, "CDS/P/DSS2/color", "linear")
    assert k1 != k3


# ------------------------------------------------------- cache eviction (P2-13)

def _make_cutout(cache_dir, name: str, size_bytes: int, age_s: float):
    """Drop a fake cutout of `size_bytes` with an mtime `age_s` in the past."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    p = cache_dir / name
    p.write_bytes(b"\x00" * size_bytes)
    past = time.time() - age_s
    os.utime(p, (past, past))
    return p


def test_evict_drops_files_past_ttl(tmp_path):
    cache = tmp_path / "_survey"
    fresh = _make_cutout(cache, "fresh.jpg", 10, age_s=10)
    stale = _make_cutout(cache, "stale.jpg", 10, age_s=999_999)
    survey_mod._evict_cache(cache, ttl_s=3600,
                            max_bytes=10**9, max_files=10**6)
    assert fresh.exists()
    assert not stale.exists()       # past the TTL -> evicted


def test_evict_enforces_size_cap_oldest_first(tmp_path):
    cache = tmp_path / "_survey"
    # Three 100-byte files; cap at 250 bytes => the single oldest must be evicted.
    old = _make_cutout(cache, "a.jpg", 100, age_s=300)
    mid = _make_cutout(cache, "b.jpg", 100, age_s=200)
    new = _make_cutout(cache, "c.jpg", 100, age_s=100)
    survey_mod._evict_cache(cache, ttl_s=10**9, max_bytes=250, max_files=10**6)
    assert not old.exists()         # oldest evicted first
    assert mid.exists() and new.exists()
    total = sum(p.stat().st_size for p in cache.glob("*.jpg"))
    assert total <= 250


def test_evict_enforces_file_count_cap(tmp_path):
    cache = tmp_path / "_survey"
    paths = [_make_cutout(cache, f"f{i}.jpg", 10, age_s=1000 - i) for i in range(5)]
    survey_mod._evict_cache(cache, ttl_s=10**9, max_bytes=10**9, max_files=3)
    remaining = sorted(p.name for p in cache.glob("*.jpg"))
    assert len(remaining) == 3
    # the three NEWEST survive (oldest-first eviction); f0..f4 ascending age means
    # f0 is the oldest mtime (age 1000) -> evicted, f4 newest (age 996) -> kept.
    assert remaining == ["f2.jpg", "f3.jpg", "f4.jpg"]


def test_evict_is_noop_on_missing_dir(tmp_path):
    # Must never raise even if the cache dir doesn't exist yet.
    survey_mod._evict_cache(tmp_path / "does-not-exist")


def test_write_cache_triggers_eviction(tmp_path, monkeypatch):
    cache = tmp_path / "_survey"
    # Pre-fill with two stale files, then write a fresh one with a tiny TTL so the
    # post-write eviction sweeps the stale pair.
    _make_cutout(cache, "old1.jpg", 10, age_s=999_999)
    _make_cutout(cache, "old2.jpg", 10, age_s=999_999)
    monkeypatch.setattr(survey_mod, "_CACHE_TTL_S", 3600)
    survey_mod._write_cache(cache / "new.jpg", b"\xff\xd8jpeg")
    names = sorted(p.name for p in cache.glob("*.jpg"))
    assert names == ["new.jpg"]     # stale pair evicted by the write-time bound


def test_snapped_geometry_headers_on_miss_and_hit(client):
    params = {"ra": 1.0, "dec": 41.0, "fov": 1.5}
    r1 = client.get("/api/survey/cutout.jpg", params=params)
    assert r1.status_code == 200
    ra1 = float(r1.headers["x-survey-ra-deg"])
    dec1 = float(r1.headers["x-survey-dec-deg"])
    fov1 = float(r1.headers["x-survey-fov-deg"])
    step = fov1 / survey_mod._CENTER_STEPS_PER_FOV
    assert abs(ra1 - 15.0) <= step / 2 + 1e-9
    assert abs(dec1 - 41.0) <= step / 2 + 1e-9
    assert abs(fov1 - 1.5) / 1.5 <= 0.011          # 2% log grid -> within ~1%
    # upstream was asked for EXACTLY the snapped geometry the header reports
    p = _FakeClient.last_params
    assert float(p["ra"]) == pytest.approx(ra1)
    assert float(p["dec"]) == pytest.approx(dec1)
    assert float(p["fov"]) == pytest.approx(fov1)
    # cache hit returns identical headers (and no network — force it to fail)
    _FakeClient.fail = True
    r2 = client.get("/api/survey/cutout.jpg", params=params)
    assert r2.status_code == 200
    assert r2.headers["x-survey-ra-deg"] == r1.headers["x-survey-ra-deg"]
    assert r2.headers["x-survey-dec-deg"] == r1.headers["x-survey-dec-deg"]
    assert r2.headers["x-survey-fov-deg"] == r1.headers["x-survey-fov-deg"]


def test_snap_clamps_dec_and_wraps_ra():
    ra_deg, dec_deg, _fov, _idx = survey_mod._snap_geometry(23.9999, 89.999, 2.0)
    assert 0.0 <= ra_deg < 360.0
    assert -90.0 <= dec_deg <= 90.0


def test_timeout_and_salt_constants():
    # 6 s x 2 attempts + 0.4 s sleep ~= 12.8 s worst case (was ~30.4 s).
    assert survey_mod._TIMEOUT_S == 6.0
    assert survey_mod._KEY_SALT == "v2"   # orphans pre-snap cache entries
