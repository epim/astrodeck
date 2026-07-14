# Atlas Client-Side HiPS Tile Engine ("slippy sky") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the survey `<img>` in the Atlas with a WebGL HiPS tile renderer that never blanks at any drag speed and zooms smoothly through the tile pyramid; grow the offline pack on-demand through a new server tile route; fold "Use camera FOV" into the Lock toggle and drop the dead stretch buttons. The hips2fits cutout route stays byte-for-byte untouched as the WebGL-unavailable fallback.

**Architecture:** New server route `catalog/tiles.py` (raw HiPS tile passthrough + on-demand pack growth) beside the untouched cutout route. New pure client math: `lib/healpix.ts` (nested HEALPix, golden-vector gated against astropy-healpix), `lib/tileView.ts` (order pick + visible set + mesh), `lib/tileCache.ts` (LRU + negative-cache + priority). New GL layer `lib/tileGL.ts` and component `components/atlas/TileEngine.tsx`. SkyCanvas mounts TileEngine when a module-level WebGL probe passes, else the existing `<img>` pipeline. Controls cleanup in `SurveyControls.tsx`/`AtlasView.tsx`.

**Tech Stack:** FastAPI, httpx, astropy-healpix (already a dep from the offline-pack work — no new server dep); React+TS UI with the repo's `api`/zustand idioms; raw WebGL1 (first GL context in the repo, greenfield per seam §9).

**Spec:** `docs/superpowers/specs/2026-07-14-atlas-tile-engine-design.md` (read §0 Constraints carried forward before touching survey.py/SkyCanvas).
**Seam file (verbatim ground truth, read before coding):** `.superpowers/sdd/seams/tile-engine.md`.

## Global Constraints

- **Offline-first (frozen):** with `config.survey.online_fetch = false`, NO code path may make an upstream network request. The tile route's offline-miss branch returns 404 **before constructing any httpx client** — test-enforced via the `_Boom` constructor pattern copied from `test_survey.py` (a stand-in `httpx.AsyncClient` whose `__init__` raises `AssertionError`).
- **Cutout route/survey.py/cache keys byte-untouched:** `/api/survey/cutout.jpg`, `survey.py`'s routing, `_cache_key`/`_pack_cache_key`, `_snap_geometry`, `_evict_cache`, and every existing survey/cutout test stay byte-for-byte unchanged. The tile route lives in a NEW module; `survey.py` is not edited.
- **North-up, East-left:** the sky never rotates. Screen mapping is `x = W/2 − xi·pxPerDeg`, `y = H/2 − eta·pxPerDeg` (from `project()`), exactly matching FovOverlay. The FovOverlay SVG is untouched and still sits on top.
- **Pack tree location + `_evict_cache`:** on-demand tiles live in the SAME `CAPTURE_DIR/_survey_pack/<slug>/Norder{k}/Dir…/Npix….jpg` tree the bulk fetcher writes (`survey_pack.tile_path`); `_evict_cache` (survey.py) never touches the pack tree and is not edited.
- **Night dimmer via CSS filter:** brightness rides `filter: brightness(...)` on the canvas element, exactly as it rode the `<img>` — no GL work.
- **Server tests run from `server/`:** `./.venv/Scripts/python.exe -m pytest -q <file>` (bash path: `/c/Users/bear/astro/server/.venv/Scripts/python.exe`).
- **UI checks:** `npm run build` (= `tsc -b && vite build`) + self-executing `npx tsx src/lib/__tests__/<name>.test.ts` from `ui/`. There is NO vitest. `resolveJsonModule` is NOT set and MUST NOT be added — tests read the golden JSON via Node `fs` at runtime under tsx (see Task 2 for the `@ts-ignore node:fs` idiom that keeps `tsc -b` clean without `@types/node`).
- **Every commit message ends with the two trailers** (shown in each commit block as extra `-m` paragraphs):
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL`

---

### Task 1: Server tile route (`catalog/tiles.py`) + `SLUG_REGISTRY`

**Suggested model:** sonnet

**Files:**
- Modify: `server/astrodeck/catalog/survey_pack.py` (add `SLUG_REGISTRY`)
- Create: `server/astrodeck/catalog/tiles.py`
- Modify: `server/astrodeck/api/app.py` (import + `include_router`, seam §6)
- Test: `server/tests/test_survey_tiles.py`

**Interfaces:**
- Consumes: `pack_dir`, `tile_path` (survey_pack, seam §6); `config_store.cfg().survey.online_fetch` (seam §7); `httpx`; `shutil.disk_usage`.
- Produces: `SLUG_REGISTRY: dict[str, dict]` in survey_pack.py (slug → `{"survey": str, "mirrors": list[str]}`); `router` (APIRouter) in tiles.py serving `GET /api/survey/tile/{slug}/{order}/{npix}.jpg`; module constant `_TILE_CACHE_MIN_FREE_BYTES = 200 * 1024 * 1024`; injectable `_TILE_TRANSPORT` for tests.

**Auth posture (seam §6):** plain `@router.get(...)` in the catalog module — NO `Depends(require(...))`, NO `@declare(...)` — exactly like the cutout route. The global `_auth_mw` still session-gates every `/api/*` path; the tile route just lacks a per-route capability decorator, matching the cutout route.

**VERIFY note (spec §1):** `dss2color` mirrors are verified. The `dss2red`/`twomass` mirror path strings are PLACEHOLDERS — before shipping, the implementer MUST `curl` each mirror's `<base>/properties` (ESA first choice), fix the paths to whatever actually answers, and record in the report what was verified. The registry STRUCTURE is the requirement.

- [ ] **Step 1: Add `SLUG_REGISTRY` to `survey_pack.py`** — after `_MIRRORS` (line 34):

```python
# On-demand tile registry (tile-engine spec §1): slug -> survey id + ordered
# mirror bases. dss2color mirrors mirror _MIRRORS above (verified). The
# dss2red/twomass paths are PLACEHOLDERS — VERIFY each against <base>/properties
# at implementation time (ESA slug names differ from CDS); the structure is the
# contract. On-demand tiles for every slug land under PACK_ROOT/<slug>/, so
# remove_pack(slug) already deletes them (spec §1; no v1 UI for red/2MASS).
SLUG_REGISTRY: dict[str, dict] = {
    "dss2color": {
        "survey": "CDS/P/DSS2/color",
        "mirrors": [
            "https://skies.esac.esa.int/DSSColor",
            "https://alasky.cds.unistra.fr/DSS/DSSColor",
            "https://alaskybis.cds.unistra.fr/DSS/DSSColor",
        ],
    },
    "dss2red": {
        "survey": "CDS/P/DSS2/red",
        "mirrors": [
            "https://skies.esac.esa.int/DSS2Red",            # VERIFY (ESA)
            "https://alasky.cds.unistra.fr/DSS/DSS2Merged",  # VERIFY (CDS)
            "https://alaskybis.cds.unistra.fr/DSS/DSS2Merged",
        ],
    },
    "twomass": {
        "survey": "CDS/P/2MASS/color",
        "mirrors": [
            "https://alasky.cds.unistra.fr/2MASS/Color",     # VERIFY (CDS)
            "https://skies.esac.esa.int/2MASS/color",        # VERIFY (ESA)
        ],
    },
}
```

- [ ] **Step 2: Write the failing tests** — `server/tests/test_survey_tiles.py` (full file):

```python
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
```

- [ ] **Step 3: Run to verify failure** — from `server/`: `./.venv/Scripts/python.exe -m pytest -q tests/test_survey_tiles.py`. Expected: collection error (`No module named 'astrodeck.catalog.tiles'`).

- [ ] **Step 4: Implement `server/astrodeck/catalog/tiles.py`** (complete module):

```python
"""On-demand HiPS tile route + pack growth (tile-engine spec §1).

`GET /api/survey/tile/{slug}/{order}/{npix}.jpg` — the browser tile engine's
raw-tile source. Pack hit -> immutable FileResponse. Miss + offline -> 404
no-store (ZERO httpx construction). Miss + online -> mirror fetch (injectable
transport), JPEG-SOI check, atomic .tmp->replace into the pack tree, serve;
disk guard serves without caching under 200 MB free. Auth posture matches the
cutout route (plain @router.get, no per-route require/declare; seam §6). NEVER
touches survey_pack.fetch_state (bulk fetch vs browsing are separate; spec §1).
"""
from __future__ import annotations

import asyncio
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response

from ..config import config_store
from . import survey_pack as pack_mod

router = APIRouter()

_USER_AGENT = "AstroDeck/0.1"
_TILE_TIMEOUT_S = 6.0                                 # tiles are small (spec §1)
_TILE_CACHE_MIN_FREE_BYTES = 200 * 1024 * 1024        # Pi-card disk guard (spec §1)
# Injectable transport for tests (httpx.MockTransport); None -> real network.
_TILE_TRANSPORT: httpx.BaseTransport | None = None
_IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}
_NO_STORE = {"Cache-Control": "no-store"}


# --------------------------------------------------------- per-tile single-flight
# Local copy of survey.py:_single_flight (spec §1 — copy the pattern, do NOT
# import the private helper): concurrent requests for the same missing tile
# coalesce on a refcounted per-key lock; waiters serve the file the leader wrote.
_tile_inflight: dict[str, list] = {}  # key -> [asyncio.Lock, refcount]


@asynccontextmanager
async def _tile_single_flight(key: str):
    entry = _tile_inflight.get(key)
    if entry is None:
        entry = [asyncio.Lock(), 0]
        _tile_inflight[key] = entry
    entry[1] += 1
    try:
        async with entry[0]:
            yield
    finally:
        entry[1] -= 1
        if entry[1] <= 0:
            _tile_inflight.pop(key, None)


async def _fetch_tile(mirrors: list[str], order: int, npix: int) -> bytes | None:
    """First mirror that returns a valid JPEG wins; one retry each, 6 s timeout."""
    subdir = f"Norder{order}/Dir{(npix // 10000) * 10000}/Npix{npix}.jpg"
    headers = {"User-Agent": _USER_AGENT}
    async with httpx.AsyncClient(timeout=_TILE_TIMEOUT_S, headers=headers,
                                 transport=_TILE_TRANSPORT) as client:
        for base in mirrors:
            url = f"{base}/{subdir}"
            for attempt in range(2):          # initial + one retry (spec §1)
                try:
                    r = await client.get(url)
                    r.raise_for_status()
                    body = r.content
                    if not body.startswith(b"\xff\xd8"):
                        raise RuntimeError("not a JPEG")
                    return body
                except Exception:  # noqa: BLE001 — uniform per-attempt failure
                    if attempt == 0:
                        await asyncio.sleep(0.3)
    return None


def _free_bytes(pack: Path) -> int:
    probe = pack
    while not probe.exists():                 # disk_usage needs an existing path
        probe = probe.parent
    return shutil.disk_usage(probe).free


def _write_tile(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(body)
    tmp.replace(path)                          # atomic publish (survey_pack idiom)


@router.get("/api/survey/tile/{slug}/{order}/{npix}.jpg")
async def survey_tile(slug: str, order: int, npix: int) -> Response:
    reg = pack_mod.SLUG_REGISTRY.get(slug)
    if reg is None:
        raise HTTPException(status_code=404, detail="unknown slug", headers=dict(_NO_STORE))
    if not (0 <= order <= 9):
        raise HTTPException(status_code=422, detail="order out of range [0,9]")
    if not (0 <= npix < 12 * 4 ** order):
        raise HTTPException(status_code=422, detail="npix out of range for order")

    pack = pack_mod.pack_dir(slug)
    path = pack_mod.tile_path(pack, order, npix)
    if path.exists():
        return FileResponse(path, media_type="image/jpeg", headers=dict(_IMMUTABLE))

    online = bool(config_store.cfg().survey.online_fetch)
    if not online:
        # ZERO httpx construction on the offline path (spec §1, _Boom-tested).
        raise HTTPException(status_code=404, detail="tile unavailable", headers=dict(_NO_STORE))

    key = f"{slug}/{order}/{npix}"
    async with _tile_single_flight(key):
        if path.exists():                      # a coalesced leader just wrote it
            return FileResponse(path, media_type="image/jpeg", headers=dict(_IMMUTABLE))
        body = await _fetch_tile(reg["mirrors"], order, npix)
        if body is None:
            raise HTTPException(status_code=404, detail="tile unavailable",
                                headers=dict(_NO_STORE))
        if _free_bytes(pack) < _TILE_CACHE_MIN_FREE_BYTES:
            return Response(body, media_type="image/jpeg", headers=dict(_IMMUTABLE))
        await asyncio.to_thread(_write_tile, path, body)
        return Response(body, media_type="image/jpeg", headers=dict(_IMMUTABLE))
```

- [ ] **Step 5: Register the router in `app.py`** (seam §6). Add the import beside the survey import (app.py line 44): `from ..catalog.tiles import router as tiles_router`. Add the include beside `app.include_router(survey_router)` (line 656):

```python
    app.include_router(survey_router)
    app.include_router(tiles_router)
    app.include_router(framing_router)
    app.include_router(visibility_router)
```

- [ ] **Step 6: VERIFY mirror paths (REQUIRED, report the result).** From `server/`, `curl -sI` each `dss2red`/`twomass` mirror's `/properties` (ESA first). Correct `SLUG_REGISTRY`'s two placeholder path strings to whatever answers 200 with `hips_tile_format = jpeg`. Leave `dss2color` untouched (verified). State in the report exactly which paths you confirmed.

- [ ] **Step 7: Run tests to verify pass** — `./.venv/Scripts/python.exe -m pytest -q tests/test_survey_tiles.py`. Expected: 10 passed. Then a boot/regression sweep proving the cutout route is untouched: `./.venv/Scripts/python.exe -m pytest -q tests/test_survey.py tests/test_survey_tiles.py`. Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add server/astrodeck/catalog/survey_pack.py server/astrodeck/catalog/tiles.py server/astrodeck/api/app.py server/tests/test_survey_tiles.py
git commit -m "feat(survey): on-demand HiPS tile route — pack-grow, single-flight, disk guard, offline 404 (spec §1)" \
  -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>" \
  -m "Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

---

### Task 2: HEALPix TS port + golden vectors (`lib/healpix.ts`)

**Suggested model:** opus

**Files:**
- Create: `server/scripts/gen_healpix_vectors.py` (new dir; standalone script)
- Create (committed, generated): `ui/src/lib/__tests__/healpix.vectors.json`
- Create: `ui/src/lib/healpix.ts`
- Test: `ui/src/lib/__tests__/healpix.test.ts`

**Interfaces:**
- Consumes: astropy-healpix + astropy.units (generator, server-side, already installed); nothing in healpix.ts (pure, no DOM).
- Produces (exact signatures, spec §2 — reused by Tasks 3/4):
  - `ang2pixNested(order: number, raDeg: number, decDeg: number): number`
  - `pixUV2ang(order: number, npix: number, u: number, v: number): { raDeg: number; decDeg: number }`
  - `parentOf(npix: number): number`
  - `childUVRect(npix: number, levelsUp: number): { u0: number; v0: number; size: number }`

**Convention anchoring (the risk center, spec §2 + hips_local.py):** the within-tile axis mapping MUST match the server renderer's PROVEN constants. In `hips_local.py`: `_X_FROM_EVEN_BITS = False` (line 32) and `_JPG_FLIP_Y = False` (line 33); `_deinterleave` (lines 52–59) accumulates `x` from EVEN bits and `y` from ODD bits and returns `(y, x)`; the render (lines 106–107, 115) samples `arr[row = even bits, col = odd bits]` with no vertical flip. So: **tile COLUMN ← odd bits, tile ROW ← even bits; `u` runs along columns (odd bits), `v` runs along rows (even bits).** In HEALPix nested decomposition, even bits → `ix` and odd bits → `iy`; astropy-healpix `healpix_to_lonlat(dx, dy)` aligns `dx` with `ix` and `dy` with `iy`. Therefore **`dx = v` (row/even/ix), `dy = u` (col/odd/iy)** — the generator emits `healpix_to_lonlat(..., dx=v, dy=u)`, and `pixUV2ang` uses `fx = ix + v`, `fy = iy + u`. WebGL texture upload must NOT flip Y (Task 4), so texcoord `v = 0` = JPEG row 0 = tile top, matching `_JPG_FLIP_Y = False`.

- [ ] **Step 1: Write `server/scripts/gen_healpix_vectors.py`** (complete script):

```python
"""Emit golden HEALPix vectors from astropy-healpix (server truth) for the TS
port's tsx test (tile-engine spec §2). Run from server/:

    ./.venv/Scripts/python.exe scripts/gen_healpix_vectors.py

Writes ui/src/lib/__tests__/healpix.vectors.json (committed). The (u,v)->sky
convention is pinned to hips_local.py (column<-odd bits, row<-even bits, no
vertical flip; lines 32-33, 52-59, 106-107, 115): u runs along columns (odd
bits -> iy -> dy), v along rows (even bits -> ix -> dx). So healpix_to_lonlat
is called with dx=v, dy=u.
"""
from __future__ import annotations

import json
from pathlib import Path

import astropy.units as u
from astropy_healpix import HEALPix, healpix_to_lonlat

OUT = (Path(__file__).resolve().parents[2]
       / "ui" / "src" / "lib" / "__tests__" / "healpix.vectors.json")

# ang2pix coverage: equatorial band, polar caps, near band boundaries
# (arcsin(2/3) ~= +/-41.81 deg — sampled just off it to avoid exact-tie flakiness),
# both poles, RA wrap (0.1 / 359.9).
DECS = [0.0, 15.0, -15.0, 30.0, -30.0, 41.80, 41.82, -41.80, -41.82,
        60.0, -60.0, 85.0, -85.0, 89.99, -89.99]
RAS = [0.1, 90.0, 200.0, 359.9]


def _interleave(ix: int, iy: int) -> int:
    b = 0
    for i in range(16):
        b |= ((ix >> i) & 1) << (2 * i)
        b |= ((iy >> i) & 1) << (2 * i + 1)
    return b


def _central_pixel(order: int, face: int) -> int:
    nside = 2 ** order
    half = nside // 2
    return face * nside * nside + _interleave(half, half)


def main() -> int:
    ang2pix: list[dict] = []
    for order in range(0, 10):
        hp = HEALPix(nside=2 ** order, order="nested")
        for dec in DECS:
            for ra in RAS:
                npix = int(hp.lonlat_to_healpix(ra * u.deg, dec * u.deg))
                ang2pix.append({"order": order, "raDeg": ra, "decDeg": dec, "npix": npix})

    uv_grid = [0.0, 0.25, 0.5, 1.0]
    pixuv: list[dict] = []
    for order in (0, 3, 6, 9):
        for face in range(12):
            npix = _central_pixel(order, face)
            for vv in uv_grid:          # v along rows (even/ix) -> dx
                for uu in uv_grid:      # u along cols (odd/iy)  -> dy
                    lon, lat = healpix_to_lonlat(
                        npix, nside=2 ** order, dx=vv, dy=uu, order="nested")
                    pixuv.append({"order": order, "npix": int(npix),
                                  "u": uu, "v": vv,
                                  "raDeg": float(lon.to_value(u.deg)),
                                  "decDeg": float(lat.to_value(u.deg))})

    # 3 parity vectors around M31 (ra 10.68 deg, dec 41.27 deg) for Task 7's
    # cross-check test.
    order = 8
    hp = HEALPix(nside=2 ** order, order="nested")
    m31 = int(hp.lonlat_to_healpix(10.68 * u.deg, 41.27 * u.deg))
    parity: list[dict] = []
    for uu, vv in [(0.5, 0.5), (0.25, 0.75), (1.0, 0.0)]:
        lon, lat = healpix_to_lonlat(m31, nside=2 ** order, dx=vv, dy=uu, order="nested")
        parity.append({"order": order, "npix": m31, "u": uu, "v": vv,
                       "raDeg": float(lon.to_value(u.deg)),
                       "decDeg": float(lat.to_value(u.deg))})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"ang2pix": ang2pix, "pixUV2ang": pixuv,
                               "parity": parity}, indent=1), encoding="utf-8")
    print(f"wrote {OUT} — ang2pix={len(ang2pix)} pixUV2ang={len(pixuv)} parity={len(parity)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Run it: from `server/`, `./.venv/Scripts/python.exe scripts/gen_healpix_vectors.py`. Expected: `ang2pix=600 pixUV2ang=768 parity=3` written. (Both counts exceed the spec minima of 200 / 300.)

- [ ] **Step 2: Write the failing test** — `ui/src/lib/__tests__/healpix.test.ts`:

```ts
// healpix.test.ts — golden-vector gate for lib/healpix.ts (tile-engine spec §2).
// Inline-assert harness (no vitest); runs via `npx tsx`. Reads the committed
// astropy-healpix vectors via Node fs at runtime (resolveJsonModule is off and
// MUST NOT be added; @ts-ignore keeps `tsc -b` clean without @types/node).
import { ang2pixNested, pixUV2ang, parentOf, childUVRect } from "../healpix";
// @ts-ignore  no @types/node in this browser-targeted UI project; tsx supplies fs at runtime
import { readFileSync } from "node:fs";

interface AngCase { order: number; raDeg: number; decDeg: number; npix: number; }
interface UvCase { order: number; npix: number; u: number; v: number; raDeg: number; decDeg: number; }
interface Vectors { ang2pix: AngCase[]; pixUV2ang: UvCase[]; parity: UvCase[]; }

const vectors = JSON.parse(
  readFileSync(new URL("./healpix.vectors.json", import.meta.url), "utf8"),
) as Vectors;

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const DEG = Math.PI / 180;
function angSepDeg(ra1: number, d1: number, ra2: number, d2: number): number {
  const c = Math.sin(d1 * DEG) * Math.sin(d2 * DEG)
    + Math.cos(d1 * DEG) * Math.cos(d2 * DEG) * Math.cos((ra1 - ra2) * DEG);
  return Math.acos(Math.min(1, Math.max(-1, c))) / DEG;
}

test(`ang2pixNested matches all ${vectors.ang2pix.length} astropy vectors`, () => {
  for (const c of vectors.ang2pix) {
    const got = ang2pixNested(c.order, c.raDeg, c.decDeg);
    assert(got === c.npix,
      `order ${c.order} ra ${c.raDeg} dec ${c.decDeg}: expected ${c.npix}, got ${got}`);
  }
});

test(`pixUV2ang within 1e-6 deg of all ${vectors.pixUV2ang.length} astropy vectors`, () => {
  for (const c of vectors.pixUV2ang) {
    const p = pixUV2ang(c.order, c.npix, c.u, c.v);
    const sep = angSepDeg(p.raDeg, p.decDeg, c.raDeg, c.decDeg);
    assert(sep < 1e-6,
      `order ${c.order} npix ${c.npix} u ${c.u} v ${c.v}: sep ${sep} deg`);
  }
});

// Hand cases: nested parent is npix >> 2.
test("parentOf strips the two low bits (nested)", () => {
  assert(parentOf(0b1101) === 0b11, "parentOf(13) == 3");
  assert(parentOf(47) === 11, "parentOf(47) == 11");
  assert(parentOf(0) === 0, "parentOf(0) == 0");
});

// Hand cases: childUVRect quadrant walk. size = 2^-levelsUp; even bits -> v (row),
// odd bits -> u (col). One level up, low 2 bits (ix=even=bit0, iy=odd=bit1):
//   sub 0b00 -> (u0,v0)=(0,0); 0b01 -> ix=1,iy=0 -> v0=0.5,u0=0;
//   0b10 -> ix=0,iy=1 -> v0=0,u0=0.5; 0b11 -> v0=0.5,u0=0.5.
test("childUVRect one level: quadrant corners", () => {
  const s = 0.5;
  assert(childUVRect(0b00, 1).u0 === 0 && childUVRect(0b00, 1).v0 === 0 && childUVRect(0b00, 1).size === s, "00");
  assert(childUVRect(0b01, 1).v0 === 0.5 && childUVRect(0b01, 1).u0 === 0, "01 -> v0=.5");
  assert(childUVRect(0b10, 1).u0 === 0.5 && childUVRect(0b10, 1).v0 === 0, "10 -> u0=.5");
  assert(childUVRect(0b11, 1).u0 === 0.5 && childUVRect(0b11, 1).v0 === 0.5, "11 -> (.5,.5)");
});

test("childUVRect two levels: size is 0.25 and offsets are quarter-grid", () => {
  const r = childUVRect(0b1011, 2); // low4: ix from bits0,2; iy from bits1,3
  assert(r.size === 0.25, "size 0.25");
  assert(Number.isInteger(r.u0 / 0.25) && Number.isInteger(r.v0 / 0.25), "on the quarter grid");
});

console.log(`healpix.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
```

- [ ] **Step 3: Run to verify failure** — from `ui/`: `npx tsx src/lib/__tests__/healpix.test.ts`. Expected: fails to resolve `../healpix` (module missing).

- [ ] **Step 4: Implement `ui/src/lib/healpix.ts`** (complete module — the standard chealpix nested formulas, ported):

```ts
// healpix.ts — nested HEALPix math in TypeScript (tile-engine spec §2). Pure,
// no DOM. Ported from the canonical chealpix ang2pix_nest / pix2ang_nest, made
// continuous for fractional (u,v). Golden-vector gated against astropy-healpix
// (__tests__/healpix.test.ts). Within-tile convention is pinned to
// server/astrodeck/catalog/hips_local.py (column<-odd bits, row<-even bits, no
// vertical flip; lines 32-33, 52-59, 106-107, 115): u runs along columns (odd
// bits -> iy), v along rows (even bits -> ix).

const DEG = Math.PI / 180;
const RAD = 180 / Math.PI;
const HALFPI = Math.PI / 2;
// Face longitude/ring base indices (Gorski et al. 2005; chealpix jpll/jrll).
const JRLL = [2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4];
const JPLL = [1, 3, 5, 7, 0, 2, 4, 6, 1, 3, 5, 7];

function interleave(ix: number, iy: number): number {
  // even bits <- ix, odd bits <- iy (standard nested sub-index)
  let b = 0;
  for (let i = 0; i < 16; i++) {
    b |= ((ix >> i) & 1) << (2 * i);
    b |= ((iy >> i) & 1) << (2 * i + 1);
  }
  return b;
}

/** ang2pix (nested). raDeg/decDeg in degrees; returns the nested pixel index. */
export function ang2pixNested(order: number, raDeg: number, decDeg: number): number {
  const nside = 2 ** order;
  const z = Math.sin(decDeg * DEG);           // z = cos(colatitude) = sin(dec)
  const za = Math.abs(z);
  const phi = (((raDeg % 360) + 360) % 360) * DEG;
  let tt = (phi / HALFPI) % 4;                  // in [0,4)
  if (tt < 0) tt += 4;

  let ix: number;
  let iy: number;
  let face: number;
  if (za <= 2 / 3) {                            // equatorial region
    const temp1 = nside * (0.5 + tt);
    const temp2 = nside * (z * 0.75);
    const jp = Math.floor(temp1 - temp2);      // ascending edge index
    const jm = Math.floor(temp1 + temp2);      // descending edge index
    const ifp = Math.floor(jp / nside);
    const ifm = Math.floor(jm / nside);
    if (ifp === ifm) face = (ifp & 3) + 4;
    else if (ifp < ifm) face = ifp & 3;
    else face = (ifm & 3) + 8;
    ix = jm & (nside - 1);
    iy = (nside - 1) - (jp & (nside - 1));
  } else {                                      // polar region
    let ntt = Math.floor(tt);
    if (ntt >= 4) ntt = 3;
    const tp = tt - ntt;
    const tmp = nside * Math.sqrt(3 * (1 - za));
    let jp = Math.floor(tp * tmp);
    let jm = Math.floor((1 - tp) * tmp);
    if (jp >= nside) jp = nside - 1;
    if (jm >= nside) jm = nside - 1;
    if (z >= 0) { face = ntt; ix = (nside - 1) - jm; iy = (nside - 1) - jp; }
    else { face = ntt + 8; ix = jp; iy = jm; }
  }
  return face * nside * nside + interleave(ix, iy);
}

/** parentOf — the nested parent one order up (npix >> 2). */
export function parentOf(npix: number): number {
  return npix >> 2;
}

/** pixUV2ang — sky point of a FRACTIONAL (u,v) in [0,1]^2 inside nested pixel
 *  `npix` at `order`. u along columns (odd bits/iy/dy), v along rows
 *  (even bits/ix/dx). Returns raDeg in [0,360), decDeg in [-90,90]. */
export function pixUV2ang(
  order: number, npix: number, u: number, v: number,
): { raDeg: number; decDeg: number } {
  const nside = 2 ** order;
  const npface = nside * nside;
  const face = Math.floor(npix / npface);
  const ipf = npix - face * npface;
  let ix = 0;
  let iy = 0;
  for (let b = 0; b < order; b++) {
    ix |= ((ipf >> (2 * b)) & 1) << b;         // even bits -> ix (row / v)
    iy |= ((ipf >> (2 * b + 1)) & 1) << b;     // odd bits  -> iy (col / u)
  }
  const fx = ix + v;                            // dx = v
  const fy = iy + u;                            // dy = u
  const jr = JRLL[face] * nside - fx - fy;      // continuous ring coordinate
  const jpt = fx - fy;                          // continuous horizontal
  const nl4 = 4 * nside;
  let z: number;
  let nr: number;
  if (jr < nside) {                             // north polar cap
    nr = jr;
    z = 1 - (nr * nr) / (3 * npface);
  } else if (jr <= 3 * nside) {                 // equatorial belt
    nr = nside;
    z = ((2 * nside - jr) * 2) / (3 * nside);
  } else {                                      // south polar cap
    nr = nl4 - jr;
    z = (nr * nr) / (3 * npface) - 1;
  }
  const phi = nr <= 1e-12 ? 0 : (JPLL[face] + jpt / nr) * (Math.PI / 4);
  let raDeg = phi * RAD;
  raDeg = ((raDeg % 360) + 360) % 360;
  const decDeg = 90 - Math.acos(Math.min(1, Math.max(-1, z))) * RAD;
  return { raDeg, decDeg };
}

/** childUVRect — where this pixel's [0,1]^2 lands inside its ancestor `levelsUp`
 *  levels up: size = 2^-levelsUp; the low 2*levelsUp bits deinterleave into
 *  (subx = even -> v, suby = odd -> u). Used for parent-texture crops. */
export function childUVRect(
  npix: number, levelsUp: number,
): { u0: number; v0: number; size: number } {
  const size = 1 / (1 << levelsUp);
  const sub = npix & ((1 << (2 * levelsUp)) - 1);
  let subx = 0;
  let suby = 0;
  for (let b = 0; b < levelsUp; b++) {
    subx |= ((sub >> (2 * b)) & 1) << b;       // even bits -> row (v)
    suby |= ((sub >> (2 * b + 1)) & 1) << b;   // odd bits  -> col (u)
  }
  return { u0: suby * size, v0: subx * size, size };
}
```

- [ ] **Step 5: Run to verify pass** — from `ui/`: `npx tsx src/lib/__tests__/healpix.test.ts` (expected: `... passed, 0 failed`) and `npm run build` (expected: clean; the `@ts-ignore node:fs` line and the runtime JSON read compile without `@types/node`). If any `pixUV2ang` vector fails, the fix is a sign/offset in the continuous ring block OR a `dx<->dy` swap between the generator and `pixUV2ang` (they must agree AND match hips_local — Task 7's parity gate is the ultimate arbiter); adjust, regenerate, re-run.

- [ ] **Step 6: Commit**

```bash
git add server/scripts/gen_healpix_vectors.py ui/src/lib/healpix.ts ui/src/lib/__tests__/healpix.test.ts ui/src/lib/__tests__/healpix.vectors.json
git commit -m "feat(atlas): nested HEALPix TS port gated by astropy-healpix golden vectors (spec §2)" \
  -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>" \
  -m "Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

---

### Task 3: Tile view math (`lib/tileView.ts`)

**Suggested model:** opus

**Files:**
- Create: `ui/src/lib/tileView.ts`
- Test: `ui/src/lib/__tests__/tileView.test.ts`

**Interfaces:**
- Consumes: `ang2pixNested`, `pixUV2ang` (healpix.ts, Task 2); `project`, `deproject` (framing.ts, seam §1). `project(ra_hours, dec_deg, ra0_hours, dec0_deg) → {xi, eta}` in DEGREES; `deproject(xi_deg, eta_deg, ra0_hours, dec0_deg) → {ra_hours, dec_deg}`. The math takes RA in DEGREES (centerRaDeg) and converts to hours for framing (`ra0h = centerRaDeg / 15`; `pixUV2ang`/`deproject` RA outputs ×15 before `ang2pixNested`).
- Produces (exact signatures, spec §3 — reused by Task 4):
  - `tileOrderFor(fovDeg: number, viewportPx: number): number`
  - `visibleTiles(centerRaDeg: number, centerDecDeg: number, fovDeg: number, viewportPx: number, order: number): number[]`
  - `tileMesh(order: number, npix: number, centerRaDeg: number, centerDecDeg: number, fovDeg: number, viewportPx: number): { positions: Float32Array; uvs: Float32Array; indices: Uint16Array }`
  - `ancestorUV(npix: number, levelsUp: number, uvs: Float32Array): Float32Array`

- [ ] **Step 1: Write the failing test** — `ui/src/lib/__tests__/tileView.test.ts`:

```ts
// tileView.test.ts — pure tests for lib/tileView.ts (tile-engine spec §3).
// Inline-assert harness (no vitest); runs via `npx tsx`.
import { tileOrderFor, visibleTiles, tileMesh, ancestorUV } from "../tileView";
import { ang2pixNested, pixUV2ang } from "../healpix";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function near(a: number, b: number, tol: number, msg = ""): void {
  if (Math.abs(a - b) > tol) throw new Error(`${msg} expected ~${b}, got ${a}`);
}

const DEG = Math.PI / 180;
function angSepDeg(ra1: number, d1: number, ra2: number, d2: number): number {
  const c = Math.sin(d1 * DEG) * Math.sin(d2 * DEG)
    + Math.cos(d1 * DEG) * Math.cos(d2 * DEG) * Math.cos((ra1 - ra2) * DEG);
  return Math.acos(Math.min(1, Math.max(-1, c))) / DEG;
}

// --- tileOrderFor: honest expected values. Tile scale = 211076.64/(512*2^k)
//     arcsec/px (211076.64 = 58.6324*3600); viewport scale = fov*3600/px.
//   fov 1.5, px 768 -> sOut=7.03125; 412.26/2^k <= 7.03 -> k=6 (6.44).
//   fov 0.5, px 768 -> sOut=2.34375; -> k=8 (1.61).
//   fov 10,  px 360 -> sOut=100;     -> k=3 (51.5).
//   fov 60,  px 512 -> sOut=421.9;   -> k=0 (412.26 <= 421.9).
//   fov 0.02,px 768 -> tiny sOut     -> clamps to k=9.
test("tileOrderFor table", () => {
  assert(tileOrderFor(1.5, 768) === 6, "1.5/768 -> 6");
  assert(tileOrderFor(0.5, 768) === 8, "0.5/768 -> 8");
  assert(tileOrderFor(10, 360) === 3, "10/360 -> 3");
  assert(tileOrderFor(60, 512) === 0, "60/512 -> 0");
  assert(tileOrderFor(0.02, 768) === 9, "0.02/768 -> clamp 9");
});

// --- visibleTiles: known view. Assert center tile present, all tiles within an
//     angular bound of the view center, and a sane count.
test("visibleTiles contains the center tile and is bounded", () => {
  const ra = 45, dec = 20, fov = 1.5, px = 768;
  const order = tileOrderFor(fov, px);
  const set = visibleTiles(ra, dec, fov, px, order);
  const center = ang2pixNested(order, ra, dec);
  assert(set.includes(center), "includes center tile");
  assert(set.length >= 4 && set.length <= 30, `count sane, got ${set.length}`);
  const bound = fov * 1.15; // margin-diagonal upper bound (deg)
  for (const npix of set) {
    const c = pixUV2ang(order, npix, 0.5, 0.5);
    assert(angSepDeg(c.raDeg, c.decDeg, ra, dec) < bound + fov,
      `tile ${npix} within angular bound`);
  }
});

// --- tileMesh: for the tile containing the view center, the (0.5,0.5) middle
//     vertex lands at the viewport center. 4x4 subdiv => 25 verts, index 12*2.
test("tileMesh center vertex at viewport center", () => {
  const order = 6, px = 512, fov = 1.0;
  // Choose the tile whose center IS the view center so the middle vertex maps
  // exactly to (W/2, H/2).
  const npix = ang2pixNested(order, 45, 20);
  const c = pixUV2ang(order, npix, 0.5, 0.5); // the tile's true center
  const mesh = tileMesh(order, npix, c.raDeg, c.decDeg, fov, px);
  assert(mesh.positions.length === 25 * 2, "25 vertices");
  assert(mesh.uvs.length === 25 * 2, "25 uvs");
  assert(mesh.indices.length === 32 * 3, "32 triangles");
  const mid = (2 * 5 + 2) * 2; // r=2,c=2 vertex (n=5 across)
  near(mesh.positions[mid], px / 2, 0.5, "mid x");
  near(mesh.positions[mid + 1], px / 2, 0.5, "mid y");
});

// --- tileMesh: North-up / East-left orientation via two known offset points.
//     A vertex NORTH of center (higher dec) has smaller screen y (up); a vertex
//     EAST of center (higher RA) has smaller screen x (left).
test("tileMesh orientation: North up, East left", () => {
  const order = 5, px = 600, fov = 2.0;
  const npix = ang2pixNested(order, 80, 10);
  const c = pixUV2ang(order, npix, 0.5, 0.5);
  const mesh = tileMesh(order, npix, c.raDeg, c.decDeg, fov, px);
  // Sample two mesh vertices and compare against their sky positions.
  let north = -1;
  let east = -1;
  for (let i = 0; i < 25; i++) {
    const uu = (i % 5) / 4;
    const vv = Math.floor(i / 5) / 4;
    const sky = pixUV2ang(order, npix, uu, vv);
    if (sky.decDeg > c.decDeg + 0.05 && north < 0) north = i;
    // East = higher RA (small wrap-safe delta)
    const dRa = ((sky.raDeg - c.raDeg + 540) % 360) - 180;
    if (dRa > 0.05 && east < 0) east = i;
  }
  assert(north >= 0 && east >= 0, "found north + east sample verts");
  assert(mesh.positions[north * 2 + 1] < px / 2, "north vertex is ABOVE center (up)");
  assert(mesh.positions[east * 2] < px / 2, "east vertex is LEFT of center");
});

// --- ancestorUV: quadrant remap. Child npix ...b01 one level up sits in the
//     v-high half (even bit set) at u-low; its UVs rescale into [v0,v0+0.5].
test("ancestorUV rescales child UVs into the ancestor rect", () => {
  const uvs = new Float32Array([0, 0, 1, 0, 0, 1, 1, 1]);
  const out = ancestorUV(0b01, 1, uvs); // childUVRect: u0=0, v0=0.5, size=0.5
  near(out[0], 0.0, 1e-9, "u corner");
  near(out[1], 0.5, 1e-9, "v corner -> v0");
  near(out[6], 0.5, 1e-9, "u=1 -> u0+size");
  near(out[7], 1.0, 1e-9, "v=1 -> v0+size");
});

console.log(`tileView.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
```

- [ ] **Step 2: Run to verify failure** — from `ui/`: `npx tsx src/lib/__tests__/tileView.test.ts`. Expected: fails to resolve `../tileView`.

- [ ] **Step 3: Implement `ui/src/lib/tileView.ts`** (complete module):

```ts
// tileView.ts — tile order pick, visible set, screen mesh, and parent-crop UVs
// for the Atlas tile engine (tile-engine spec §3). Pure, npx-tsx testable.
// Screen mapping is North-up / East-left, matching FovOverlay:
//   x = W/2 - xi*pxPerDeg,  y = H/2 - eta*pxPerDeg   (project() from framing.ts).
import { project, deproject } from "./framing";
import { ang2pixNested, pixUV2ang, childUVRect } from "./healpix";

const ORDER0_TILE_ARCSEC = 58.6324 * 3600; // 211076.64: healpix order-0 pixel side
const TILE_PX = 512;                        // DSS2/2MASS HiPS tile width
const MAX_ORDER = 9;
const SUBDIV = 4;                           // 4x4 quad -> 25 verts, 32 tris

/** Smallest order k whose tile scale <= the viewport scale, clamped [0,9].
 *  Same rule as hips_local._order_for (tile_width=512, max_order=9). */
export function tileOrderFor(fovDeg: number, viewportPx: number): number {
  const sOut = (fovDeg * 3600) / viewportPx;
  let k = 0;
  while (k < MAX_ORDER && ORDER0_TILE_ARCSEC / (TILE_PX * 2 ** k) > sOut) k++;
  return k;
}

/** Nested pixels covering the viewport +15% margin (the margin ring doubles as
 *  the prefetch set). Samples a 24x24 grid of ξ/η, deprojects to sky, ang2pix,
 *  dedupes. (At the picked order a tile spans >= ~500 px so 24x24 cannot skip
 *  one.) viewportPx is positionally required (order after it is used); the grid
 *  is angular, so it is not read. */
export function visibleTiles(
  centerRaDeg: number, centerDecDeg: number, fovDeg: number,
  viewportPx: number, order: number,
): number[] {
  const ra0h = centerRaDeg / 15;
  const half = 0.5 * fovDeg * 1.15;
  const N = 24;
  const seen = new Set<number>();
  for (let i = 0; i < N; i++) {
    for (let j = 0; j < N; j++) {
      const xi = -half + 2 * half * (i / (N - 1));
      const eta = -half + 2 * half * (j / (N - 1));
      const sky = deproject(xi, eta, ra0h, centerDecDeg);
      seen.add(ang2pixNested(order, sky.ra_hours * 15, sky.dec_deg));
    }
  }
  return [...seen];
}

/** A 4x4 subdivided quad for one tile: vertex (u,v) -> pixUV2ang -> project ->
 *  screen px. 25 vertices, 32 triangles. UVs are the tile's own [0,1] texcoords
 *  (u=col, v=row) — texture upload must not flip Y so v=0 = tile top. */
export function tileMesh(
  order: number, npix: number, centerRaDeg: number, centerDecDeg: number,
  fovDeg: number, viewportPx: number,
): { positions: Float32Array; uvs: Float32Array; indices: Uint16Array } {
  const ra0h = centerRaDeg / 15;
  const pxPerDeg = viewportPx / fovDeg;
  const half = viewportPx / 2;
  const n = SUBDIV + 1;
  const positions = new Float32Array(n * n * 2);
  const uvs = new Float32Array(n * n * 2);
  for (let r = 0; r <= SUBDIV; r++) {
    for (let c = 0; c <= SUBDIV; c++) {
      const uu = c / SUBDIV;
      const vv = r / SUBDIV;
      const sky = pixUV2ang(order, npix, uu, vv);
      const p = project(sky.raDeg / 15, sky.decDeg, ra0h, centerDecDeg);
      const idx = (r * n + c) * 2;
      positions[idx] = half - p.xi * pxPerDeg;      // East-left
      positions[idx + 1] = half - p.eta * pxPerDeg; // North-up
      uvs[idx] = uu;
      uvs[idx + 1] = vv;
    }
  }
  const indices = new Uint16Array(SUBDIV * SUBDIV * 6);
  let t = 0;
  for (let r = 0; r < SUBDIV; r++) {
    for (let c = 0; c < SUBDIV; c++) {
      const a = r * n + c;
      const b = r * n + c + 1;
      const d = (r + 1) * n + c;
      const e = (r + 1) * n + c + 1;
      indices[t++] = a; indices[t++] = b; indices[t++] = d;
      indices[t++] = b; indices[t++] = e; indices[t++] = d;
    }
  }
  return { positions, uvs, indices };
}

/** Rescale a tile's own UVs into its ancestor's texture rect (parent
 *  upsampling): u' = u0 + u*size, v' = v0 + v*size, via childUVRect. */
export function ancestorUV(npix: number, levelsUp: number, uvs: Float32Array): Float32Array {
  const { u0, v0, size } = childUVRect(npix, levelsUp);
  const out = new Float32Array(uvs.length);
  for (let i = 0; i < uvs.length; i += 2) {
    out[i] = u0 + uvs[i] * size;
    out[i + 1] = v0 + uvs[i + 1] * size;
  }
  return out;
}
```

- [ ] **Step 4: Run to verify pass** — from `ui/`: `npx tsx src/lib/__tests__/tileView.test.ts` (expected: `... passed, 0 failed`) and `npm run build` (clean). If the orientation test fails, the sign is in `tileMesh`'s `half - p.xi*pxPerDeg` / `half - p.eta*pxPerDeg` lines (they must match FovOverlay's North-up/East-left mapping) — do not weaken the direction assertions.

- [ ] **Step 5: Commit**

```bash
git add ui/src/lib/tileView.ts ui/src/lib/__tests__/tileView.test.ts
git commit -m "feat(atlas): tile order pick + visible set + screen mesh + parent-crop UVs (spec §3)" \
  -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>" \
  -m "Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

---

### Task 4: GL layer + cache helpers + renderer (`lib/tileGL.ts`, `lib/tileCache.ts`, `components/atlas/TileEngine.tsx`)

**Suggested model:** opus

**Files:**
- Create: `ui/src/lib/tileCache.ts` (pure — priority + LRU + negative cache)
- Test: `ui/src/lib/__tests__/tileCache.test.ts`
- Create: `ui/src/lib/tileGL.ts` (WebGL wrapper — exempt from unit tests, spec §7)
- Create: `ui/src/components/atlas/TileEngine.tsx`

**Interfaces:**
- Consumes: `pixUV2ang`, `parentOf` (healpix.ts); `tileOrderFor`, `visibleTiles`, `tileMesh`, `ancestorUV` (tileView.ts); `u` from `lib/base` (URL base, seam §2); React.
- Produces: `tilePriority`, `LruSet<V>`, `NegativeCache` (tileCache.ts); `initTileGL`, `TileGL`, `TileDraw` (tileGL.ts); `TileEngine` + `TileEngineProps` (exact spec §4 props).

- [ ] **Step 1: Write the failing test** — `ui/src/lib/__tests__/tileCache.test.ts`:

```ts
// tileCache.test.ts — pure cache/queue bookkeeping (tile-engine spec §4).
import { tilePriority, LruSet, NegativeCache } from "../tileCache";
import { ang2pixNested } from "../healpix";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

test("tilePriority: center tile ranks below (nearer than) an edge tile", () => {
  const order = 6, ra = 45, dec = 20;
  const center = ang2pixNested(order, ra, dec);
  const edge = ang2pixNested(order, ra + 0.8, dec + 0.8);
  assert(tilePriority(center, order, ra, dec) < tilePriority(edge, order, ra, dec),
    "center priority < edge priority");
});

test("LruSet evicts oldest, get promotes recency, onEvict fires", () => {
  const evicted: string[] = [];
  const lru = new LruSet<string>(2, (v) => evicted.push(v));
  lru.set("a", "A");
  lru.set("b", "B");
  assert(lru.get("a") === "A", "a present");   // promotes a
  lru.set("c", "C");                            // evicts b (oldest)
  assert(!lru.has("b"), "b evicted");
  assert(evicted.length === 1 && evicted[0] === "B", "onEvict got B");
  assert(lru.has("a") && lru.has("c"), "a + c remain");
  assert(lru.size === 2, "size capped");
});

test("NegativeCache blocks within ttl and clears after", () => {
  const nc = new NegativeCache(45_000);
  nc.mark("k", 1000);
  assert(nc.blocked("k", 1000) === true, "blocked at t0");
  assert(nc.blocked("k", 40_000) === true, "blocked before ttl");
  assert(nc.blocked("k", 46_001) === false, "cleared after ttl");
  assert(nc.blocked("k", 46_050) === false, "stays cleared (entry dropped)");
});

console.log(`tileCache.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
```

- [ ] **Step 2: Run to verify failure** — `npx tsx src/lib/__tests__/tileCache.test.ts`. Expected: `../tileCache` unresolved.

- [ ] **Step 3: Implement `ui/src/lib/tileCache.ts`** (complete module):

```ts
// tileCache.ts — pure loader bookkeeping for the tile engine (tile-engine
// spec §4): fetch priority (distance from view center), an insertion-ordered
// LRU (Map-backed), and a TTL negative cache. No DOM.
import { pixUV2ang } from "./healpix";

const DEG = Math.PI / 180;

/** Angular distance (deg) from a tile's center to the view center — the loader
 *  queue's priority key (smaller = fetch sooner). */
export function tilePriority(
  npix: number, order: number, centerRaDeg: number, centerDecDeg: number,
): number {
  const c = pixUV2ang(order, npix, 0.5, 0.5);
  const a = Math.sin(c.decDeg * DEG) * Math.sin(centerDecDeg * DEG)
    + Math.cos(c.decDeg * DEG) * Math.cos(centerDecDeg * DEG)
      * Math.cos((c.raDeg - centerRaDeg) * DEG);
  return Math.acos(Math.min(1, Math.max(-1, a))) / DEG;
}

/** Insertion-ordered LRU. get() promotes recency; set() evicts the oldest past
 *  the cap (calling onEvict so callers can free ImageBitmaps / GL textures). */
export class LruSet<V> {
  private map = new Map<string, V>();
  constructor(private cap: number, private onEvict?: (v: V) => void) {}

  has(key: string): boolean {
    return this.map.has(key);
  }

  get(key: string): V | undefined {
    const v = this.map.get(key);
    if (v !== undefined) {
      this.map.delete(key);
      this.map.set(key, v);
    }
    return v;
  }

  set(key: string, v: V): void {
    if (this.map.has(key)) this.map.delete(key);
    this.map.set(key, v);
    while (this.map.size > this.cap) {
      const oldest = this.map.keys().next().value as string;
      const ev = this.map.get(oldest) as V;
      this.map.delete(oldest);
      this.onEvict?.(ev);
    }
  }

  clear(): void {
    if (this.onEvict) for (const v of this.map.values()) this.onEvict(v);
    this.map.clear();
  }

  get size(): number {
    return this.map.size;
  }
}

/** TTL negative cache: a key marked failed at `now` is blocked until now+ttl.
 *  A lapsed entry is dropped on the read that clears it. */
export class NegativeCache {
  private until = new Map<string, number>();
  constructor(private ttlMs: number) {}

  mark(key: string, now: number): void {
    this.until.set(key, now + this.ttlMs);
  }

  blocked(key: string, now: number): boolean {
    const t = this.until.get(key);
    if (t === undefined) return false;
    if (t <= now) {
      this.until.delete(key);
      return false;
    }
    return true;
  }
}
```

- [ ] **Step 4: Run tileCache test to pass** — `npx tsx src/lib/__tests__/tileCache.test.ts`. Expected: `... passed, 0 failed`.

- [ ] **Step 5: Implement `ui/src/lib/tileGL.ts`** (complete module — the ONLY file touching the GL API, spec §4):

```ts
// tileGL.ts — thin WebGL1 wrapper for the tile engine (tile-engine spec §4).
// The only file that touches the GL API. initTileGL returns null on failure so
// the caller falls back to the <img> pipeline. One textured-quad shader pair;
// LINEAR + CLAMP_TO_EDGE, NO Y-flip on upload (texcoord v=0 = tile top, matching
// hips_local _JPG_FLIP_Y=False); a keyed GPU-texture LRU (128).

export interface TileDraw {
  positions: Float32Array; // device px, origin top-left, x right / y down
  uvs: Float32Array;
  indices: Uint16Array;
  texture: WebGLTexture;
}

export interface TileGL {
  texFor(key: string): WebGLTexture | undefined;
  uploadTile(key: string, bitmap: ImageBitmap): WebGLTexture;
  drawTiles(draws: TileDraw[], sizePx: number): void;
  dispose(): void;
}

const VERT = `
attribute vec2 a_pos;
attribute vec2 a_uv;
uniform vec2 u_viewport;
varying vec2 v_uv;
void main() {
  vec2 clip = vec2(a_pos.x / u_viewport.x * 2.0 - 1.0,
                   1.0 - a_pos.y / u_viewport.y * 2.0);
  gl_Position = vec4(clip, 0.0, 1.0);
  v_uv = a_uv;
}`;

const FRAG = `
precision mediump float;
uniform sampler2D u_tex;
varying vec2 v_uv;
void main() { gl_FragColor = texture2D(u_tex, v_uv); }`;

const GPU_LRU = 128;

function compile(gl: WebGLRenderingContext, type: number, src: string): WebGLShader | null {
  const sh = gl.createShader(type);
  if (!sh) return null;
  gl.shaderSource(sh, src);
  gl.compileShader(sh);
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
    gl.deleteShader(sh);
    return null;
  }
  return sh;
}

export function initTileGL(canvas: HTMLCanvasElement): TileGL | null {
  const gl = (canvas.getContext("webgl", { premultipliedAlpha: false })
    || canvas.getContext("experimental-webgl")) as WebGLRenderingContext | null;
  if (!gl) return null;
  const vs = compile(gl, gl.VERTEX_SHADER, VERT);
  const fs = compile(gl, gl.FRAGMENT_SHADER, FRAG);
  if (!vs || !fs) return null;
  const prog = gl.createProgram();
  if (!prog) return null;
  gl.attachShader(prog, vs);
  gl.attachShader(prog, fs);
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return null;

  const aPos = gl.getAttribLocation(prog, "a_pos");
  const aUv = gl.getAttribLocation(prog, "a_uv");
  const uViewport = gl.getUniformLocation(prog, "u_viewport");
  const uTex = gl.getUniformLocation(prog, "u_tex");
  const posBuf = gl.createBuffer();
  const uvBuf = gl.createBuffer();
  const idxBuf = gl.createBuffer();

  // Keyed GPU-texture LRU (128). Map preserves insertion order for eviction.
  const textures = new Map<string, WebGLTexture>();

  function texFor(key: string): WebGLTexture | undefined {
    const t = textures.get(key);
    if (t) { textures.delete(key); textures.set(key, t); } // promote
    return t;
  }

  function uploadTile(key: string, bitmap: ImageBitmap): WebGLTexture {
    const tex = gl.createTexture() as WebGLTexture;
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, 0); // v=0 = tile top (no flip)
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, bitmap);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    textures.set(key, tex);
    while (textures.size > GPU_LRU) {
      const oldest = textures.keys().next().value as string;
      const ev = textures.get(oldest);
      textures.delete(oldest);
      if (ev) gl.deleteTexture(ev);
    }
    return tex;
  }

  function drawTiles(draws: TileDraw[], sizePx: number): void {
    gl.viewport(0, 0, sizePx, sizePx);
    gl.clearColor(0, 0, 0, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    if (draws.length === 0) return;
    gl.useProgram(prog);
    gl.uniform2f(uViewport, sizePx, sizePx);
    gl.uniform1i(uTex, 0);
    gl.activeTexture(gl.TEXTURE0);
    gl.enableVertexAttribArray(aPos);
    gl.enableVertexAttribArray(aUv);
    for (const d of draws) {
      gl.bindBuffer(gl.ARRAY_BUFFER, posBuf);
      gl.bufferData(gl.ARRAY_BUFFER, d.positions, gl.DYNAMIC_DRAW);
      gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ARRAY_BUFFER, uvBuf);
      gl.bufferData(gl.ARRAY_BUFFER, d.uvs, gl.DYNAMIC_DRAW);
      gl.vertexAttribPointer(aUv, 2, gl.FLOAT, false, 0, 0);
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, idxBuf);
      gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, d.indices, gl.DYNAMIC_DRAW);
      gl.bindTexture(gl.TEXTURE_2D, d.texture);
      gl.drawElements(gl.TRIANGLES, d.indices.length, gl.UNSIGNED_SHORT, 0);
    }
  }

  function dispose(): void {
    for (const t of textures.values()) gl.deleteTexture(t);
    textures.clear();
    gl.deleteBuffer(posBuf);
    gl.deleteBuffer(uvBuf);
    gl.deleteBuffer(idxBuf);
    gl.deleteProgram(prog);
    gl.deleteShader(vs);
    gl.deleteShader(fs);
    const lose = gl.getExtension("WEBGL_lose_context");
    lose?.loseContext();
  }

  return { texFor, uploadTile, drawTiles, dispose };
}
```

- [ ] **Step 6: Implement `ui/src/components/atlas/TileEngine.tsx`** (complete component — thin over Tasks 2–4; NO unit test, build must pass):

```tsx
// TileEngine.tsx — the WebGL survey layer SkyCanvas mounts when a WebGL probe
// passes (tile-engine spec §4). Fetches raw HiPS tiles from /api/survey/tile,
// warps them through the exact TAN projection, upsamples from parent tiles so it
// never blanks. Pure math lives in healpix.ts / tileView.ts / tileCache.ts; this
// component is the loader queue + rAF draw loop over them.
import { useCallback, useEffect, useRef, type JSX } from "react";
import { u } from "../../lib/base";
import { parentOf } from "../../lib/healpix";
import { tileOrderFor, visibleTiles, tileMesh, ancestorUV } from "../../lib/tileView";
import { tilePriority, LruSet, NegativeCache } from "../../lib/tileCache";
import { initTileGL, type TileGL, type TileDraw } from "../../lib/tileGL";

export interface TileEngineProps {
  centerRaDeg: number;
  centerDecDeg: number;
  fovDeg: number;
  slug: string;
  onlineFetch: boolean;
  brightness: number;
  onFirstTile: () => void;
  onAllFailing: () => void;
}

const CONCURRENCY = 6;
const BITMAP_LRU = 256;
const NEG_TTL_MS = 45_000;
const PARENT_WALK = 5;

export function TileEngine(props: TileEngineProps): JSX.Element {
  const {
    centerRaDeg, centerDecDeg, fovDeg, slug, onlineFetch, brightness,
    onFirstTile, onAllFailing,
  } = props;

  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const glRef = useRef<TileGL | null>(null);
  const bitmaps = useRef(new LruSet<ImageBitmap>(BITMAP_LRU, (b) => b.close()));
  const negcache = useRef(new NegativeCache(NEG_TTL_MS));
  const inflight = useRef(new Map<string, AbortController>());
  const dirty = useRef(true);
  const firstDrawn = useRef(false);
  const everDrew = useRef(false);
  const consecFail = useRef(0);

  // Latest view snapshot for the rAF loop (avoids re-subscribing the loop).
  const view = useRef({ centerRaDeg, centerDecDeg, fovDeg, slug, onlineFetch });
  view.current = { centerRaDeg, centerDecDeg, fovDeg, slug, onlineFetch };

  const keyOf = (order: number, npix: number): string => `${slug}/${order}/${npix}`;

  // Init GL once. If it returns null (should not, given SkyCanvas's probe) the
  // loop simply draws nothing.
  useEffect(() => {
    const c = canvasRef.current;
    if (c) glRef.current = initTileGL(c);
    dirty.current = true;
    const flightMap = inflight.current;
    const bmp = bitmaps.current;
    return () => {
      glRef.current?.dispose();
      glRef.current = null;
      flightMap.forEach((a) => a.abort());
      flightMap.clear();
      bmp.clear();
    };
  }, []);

  // Prop changes dirty the scene.
  useEffect(() => { dirty.current = true; }, [centerRaDeg, centerDecDeg, fovDeg, slug, onlineFetch]);

  // A survey (slug) change resets first-tile / failure bookkeeping.
  useEffect(() => {
    firstDrawn.current = false;
    everDrew.current = false;
    consecFail.current = 0;
  }, [slug]);

  const enqueue = useCallback((order: number, npix: number) => {
    const key = keyOf(order, npix);
    if (bitmaps.current.has(key) || inflight.current.has(key)) return;
    if (negcache.current.blocked(key, performance.now())) return;
    if (inflight.current.size >= CONCURRENCY) return; // retried next dirty frame
    const ac = new AbortController();
    inflight.current.set(key, ac);
    void (async () => {
      try {
        const res = await fetch(u(`/api/survey/tile/${key}.jpg`), { signal: ac.signal });
        if (!res.ok) throw new Error(String(res.status));
        const bmp = await createImageBitmap(await res.blob());
        bitmaps.current.set(key, bmp);
        consecFail.current = 0;
        dirty.current = true;
      } catch (e) {
        if ((e as Error).name === "AbortError") return;
        negcache.current.mark(key, performance.now());
        consecFail.current += 1;
        if (consecFail.current >= 8 && !everDrew.current) onAllFailing();
      } finally {
        inflight.current.delete(key);
      }
    })();
  }, [slug, onAllFailing]);

  // rAF dirty-driven draw loop.
  useEffect(() => {
    let raf = 0;
    const loop = () => {
      raf = requestAnimationFrame(loop);
      if (!dirty.current) return;
      const gl = glRef.current;
      const canvas = canvasRef.current;
      if (!gl || !canvas) return;
      dirty.current = false;

      const v = view.current;
      const dpr = window.devicePixelRatio || 1;
      const cssPx = canvas.clientWidth || 360;
      const sizePx = Math.round(cssPx * dpr);
      if (canvas.width !== sizePx) { canvas.width = sizePx; canvas.height = sizePx; }

      const order = tileOrderFor(v.fovDeg, cssPx);
      const tiles = visibleTiles(v.centerRaDeg, v.centerDecDeg, v.fovDeg, cssPx, order);
      const draws: TileDraw[] = [];

      for (const npix of tiles) {
        const mesh = tileMesh(order, npix, v.centerRaDeg, v.centerDecDeg, v.fovDeg, cssPx);
        const dpos = new Float32Array(mesh.positions.length);
        for (let i = 0; i < dpos.length; i++) dpos[i] = mesh.positions[i] * dpr;

        const ownKey = keyOf(order, npix);
        const ownBmp = bitmaps.current.get(ownKey);
        if (ownBmp) {
          const tex = gl.texFor(ownKey) ?? gl.uploadTile(ownKey, ownBmp);
          draws.push({ positions: dpos, uvs: mesh.uvs, indices: mesh.indices, texture: tex });
          continue;
        }
        // Walk parents up to 5 levels for the nearest loaded ancestor.
        let p = npix;
        for (let lv = 1; lv <= PARENT_WALK && order - lv >= 0; lv++) {
          p = parentOf(p);
          const ak = keyOf(order - lv, p);
          const ab = bitmaps.current.get(ak);
          if (ab) {
            const tex = gl.texFor(ak) ?? gl.uploadTile(ak, ab);
            draws.push({
              positions: dpos, uvs: ancestorUV(npix, lv, mesh.uvs),
              indices: mesh.indices, texture: tex,
            });
            break;
          }
        }
        // else: no texture at any level -> black this frame (arrives later).
      }

      gl.drawTiles(draws, sizePx);

      // Schedule fetches, nearest-first (the +15% margin ring is the prefetch set).
      const byPri = [...tiles].sort((a, b) =>
        tilePriority(a, order, v.centerRaDeg, v.centerDecDeg)
        - tilePriority(b, order, v.centerRaDeg, v.centerDecDeg));
      for (const npix of byPri) enqueue(order, npix);

      if (draws.length > 0) {
        everDrew.current = true;
        if (!firstDrawn.current) { firstDrawn.current = true; onFirstTile(); }
      }
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [enqueue, onFirstTile]);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden
      className="absolute inset-0 w-full h-full"
      style={{ filter: `brightness(${brightness})` }}
    />
  );
}
```

- [ ] **Step 7: Build + tests** — from `ui/`: `npm run build` (expected: clean — TileEngine typechecks under strict/noUnused) and re-run tileCache: `npx tsx src/lib/__tests__/tileCache.test.ts` (all pass). `slug` in `keyOf` is read from the enclosing render; the `enqueue` dep list includes `slug` so a survey change rebuilds it.

- [ ] **Step 8: Commit**

```bash
git add ui/src/lib/tileCache.ts ui/src/lib/__tests__/tileCache.test.ts ui/src/lib/tileGL.ts ui/src/components/atlas/TileEngine.tsx
git commit -m "feat(atlas): WebGL tile renderer — GL wrapper, loader queue/LRU/negative-cache, parent upsampling (spec §4)" \
  -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>" \
  -m "Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

---

### Task 5: SkyCanvas integration + grab-the-sky drag flip

**Suggested model:** opus

**Files:**
- Modify: `ui/src/components/atlas/SkyCanvas.tsx` (WebGL probe, conditional TileEngine mount, `panTo` sign flip, native non-passive wheel-zoom listener, tile skeleton/degraded wiring, `onlineFetch` prop)
- Modify: `ui/src/views/AtlasView.tsx` (pass `onlineFetch` to SkyCanvas — the value already exists at line 154)
- Possibly modify: `ui/src/lib/__tests__/surveyView.test.ts` (only if a drag-direction comment references the sign)

**Interfaces:**
- Consumes: `initTileGL` (Task 4 — module-level probe), `TileEngine` (Task 4).
- Produces: SkyCanvas gains `onlineFetch?: boolean`; a module-level `tileGLSupported()` probe; conditional render of `<TileEngine/>` vs the untouched `<img>`.

**What is conditional vs untouched (seam §2 img-only vs shared).** The ONLY edits to the img path are the mount GUARD (`&& !useTileEngine`) and a new sibling `<TileEngine/>`; every img-pipeline piece stays byte-identical: `shownUrl`/`shownGeom`/`shownUrlRef`, `debounceRef`/`genRef`/`abortRef`/`retryRef`, `targetUrl` memo, `loadSurvey`, the 300 ms debounce effect, the survey-change-clears effect, the unmount-cleanup effect, `imgTransform` memo, `slowLoad`/`everLoaded` and their skeleton/chip JSX, and the `<img>` element itself. Shared pieces (pointer/pan/keyboard, sizing, night dimmer, FovOverlay+compass, HTML labels, footer) are unchanged except the single `panTo` sign line and the wheel handler, which moves from the React synthetic `onWheel` to a native non-passive listener (Step 5 — spec §5 "Wheel-zoom page-scroll trap": React ≥17 registers synthetic `onWheel` as PASSIVE, so the existing `e.preventDefault()` is silently ignored and the page scrolls).

- [ ] **Step 1: Module-level WebGL probe (SkyCanvas.tsx top, after imports).** Add import `import { initTileGL } from "../../lib/tileGL";` and `import { TileEngine } from "./TileEngine";`, then:

```ts
// One-time WebGL capability probe (spec §5): try initTileGL on a 1x1 canvas.
// Cached so every SkyCanvas mount shares one probe result.
let _tileGLProbe: boolean | null = null;
function tileGLSupported(): boolean {
  if (_tileGLProbe === null) {
    try {
      const c = document.createElement("canvas");
      c.width = 1;
      c.height = 1;
      const g = initTileGL(c);
      _tileGLProbe = g !== null;
      g?.dispose();
    } catch {
      _tileGLProbe = false;
    }
  }
  return _tileGLProbe;
}

// survey id -> tile slug (UI mirror of the server SLUG_REGISTRY, spec §6).
// schematic / unknown -> null -> the <img> fallback pipeline.
const SURVEY_SLUGS: Record<string, string> = {
  "CDS/P/DSS2/color": "dss2color",
  "CDS/P/DSS2/red": "dss2red",
  "CDS/P/2MASS/color": "twomass",
};
```

- [ ] **Step 2: Add the `onlineFetch` prop.** In `SkyCanvasProps` after `degradedText?: string;` add:

```ts
  /** config.survey.online_fetch — passed through to the tile engine (spec §4). */
  onlineFetch?: boolean;
```

Destructure it in the props block (default `false`): add `onlineFetch = false,` to the destructuring list. Then near the other derived values (after `cssPerDeg`):

```ts
  const surveySlug = SURVEY_SLUGS[survey] ?? null;
  const useTileEngine = mode === "survey" && surveySlug !== null && tileGLSupported();
  const [tileDrew, setTileDrew] = useState(false);
```

Add a small effect to reset the tile-first flag on survey change (separate from the untouched img survey-change effect):

```ts
  // Tile engine first-draw flag resets when the survey (slug) changes.
  useEffect(() => { setTileDrew(false); }, [survey]);

  const onTileFirst = useCallback(() => {
    setTileDrew(true);
    onSurveyLoad?.();
  }, [onSurveyLoad]);
  const onTileAllFailing = useCallback(() => {
    onSurveyError?.();
  }, [onSurveyError]);
```

- [ ] **Step 3: `panTo` sign flip (spec §0.2/§5).** Replace the horizontal line + comment in `panTo` (seam §2, SkyCanvas.tsx:285-296). Change:

```ts
      // viewBox is N-up: +x is East/RA-increasing on the sky image (the survey is
      // mirrored for RA, but the overlay frame uses the SAME projection so the
      // visual stays consistent). Dragging right moves the sky left under the frame.
      const dXiDeg = -(dxPx / cssPerDeg);
```

to:

```ts
      // Grab-the-sky, both axes (spec §5): drag right pulls the sky right
      // (map-style), revealing what lay to the left. Vertical was already correct.
      const dXiDeg = dxPx / cssPerDeg;
```

(The `dEtaDeg` line and everything else in `panTo` are unchanged. Arrow-key nudge uses `deproject` directly, not `panTo`, so it is unaffected — seam §2.)

- [ ] **Step 4: Mount TileEngine + gate the img.** In the layer-1 block (seam §2, SkyCanvas.tsx:411-428) insert the TileEngine sibling BEFORE the `<img>` and add `!useTileEngine` to the img guard:

```tsx
        {/* 1a. WebGL tile engine (spec §5): mounts for survey mode when a slug
              maps and WebGL is available; else the <img> pipeline below. */}
        {useTileEngine && surveySlug && (
          <TileEngine
            centerRaDeg={center.ra_hours * 15}
            centerDecDeg={center.dec_deg}
            fovDeg={fovZoomDeg}
            slug={surveySlug}
            onlineFetch={onlineFetch}
            brightness={imageBrightness}
            onFirstTile={onTileFirst}
            onAllFailing={onTileAllFailing}
          />
        )}

        {/* 1b. survey image — kept EXACTLY as-is; the tile engine gates it off. */}
        {mode === "survey" && !useTileEngine && shownUrl && (
          <img
            src={shownUrl}
            /* ...unchanged... */
          />
        )}
```

Add a tile-path skeleton beside the existing first-load skeleton (do not edit the img skeleton):

```tsx
        {/* first-ever tile-engine skeleton — until the first texture draws */}
        {useTileEngine && !tileDrew && (
          <div className="absolute inset-0 grid place-items-center text-dim text-xs" aria-hidden>
            <span className="animate-pulse">LOADING {survey.split("/").pop()}…</span>
          </div>
        )}
```

The degraded banner + `role="status"` region already consume `surveyDegraded`/`degradedText` — driven for the tile path by `onTileAllFailing → onSurveyError` and cleared by `onTileFirst → onSurveyLoad` (AtlasView machinery unchanged). No debounce is inserted for the tile engine's per-pointermove `onCenterChange` (spec §5); its own loader queue handles fetch cadence.

- [ ] **Step 5: Native non-passive wheel-zoom listener (spec §5 "Wheel-zoom page-scroll trap").** React ≥17 registers the synthetic `onWheel` prop as a PASSIVE listener, so SkyCanvas's existing `e.preventDefault()` inside `onWheel` is silently ignored — wheel zoom over the Atlas also scrolls the page. Fix: REMOVE the React handler and JSX prop, replace with a real non-passive listener attached to the box container in a `useEffect` — so it applies in ALL modes (tile engine, img fallback, schematic); do NOT attach inside TileEngine.

  1. DELETE the `onWheel` handler (SkyCanvas.tsx:350-355, the `// ---- wheel zoom ----` block) and the `onWheel={onWheel}` JSX prop on the box `<div>` (SkyCanvas.tsx:407).
  2. REMOVE `type WheelEvent as RWheelEvent` from the React import list (SkyCanvas.tsx:20-23) — it becomes unused and `noUnusedLocals` would fail the build.
  3. ADD, near the other shared handlers (where the deleted block was) — refs keep the once-attached listener seeing the current `fovZoomDeg`/`onZoom` without re-attaching per render:

```ts
  // ---- wheel zoom (native, non-passive) ----
  // React >=17 registers synthetic onWheel as a PASSIVE listener, so
  // e.preventDefault() in a React handler is silently ignored and the page
  // scrolls under the Atlas (spec §5 "Wheel-zoom page-scroll trap"). Attach a
  // real { passive: false } listener to the box instead — it serves ALL modes
  // (tile engine, img fallback, schematic). Refs keep the handler current
  // without re-attaching on every zoom change.
  const fovZoomRef = useRef(fovZoomDeg);
  fovZoomRef.current = fovZoomDeg;
  const onZoomRef = useRef(onZoom);
  onZoomRef.current = onZoom;
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const handler = (e: WheelEvent) => {
      e.preventDefault(); // honored: registered with passive: false
      const factor = e.deltaY > 0 ? 1.12 : 1 / 1.12;
      onZoomRef.current(clampZoom(fovZoomRef.current * factor));
    };
    el.addEventListener("wheel", handler, { passive: false });
    return () => el.removeEventListener("wheel", handler);
  }, []);
```

  The zoom-factor logic (1.12 step, `clampZoom`) is byte-identical to the deleted handler; only the registration mechanism changes. `boxRef` is set on the box `<div>` before this effect runs, and the empty dep list is correct because both changing values are read through refs.

- [ ] **Step 6: AtlasView passes `onlineFetch`.** In the `<SkyCanvas …/>` mount (seam §3, AtlasView.tsx:730-750) add `onlineFetch={onlineFetch}` (the value already exists at AtlasView.tsx:154). No other AtlasView change in this task.

- [ ] **Step 7: Check surveyView.test.** From `ui/`, inspect `src/lib/__tests__/surveyView.test.ts` for any assertion/comment that references drag-direction sign. The transform math in `lib/surveyView.ts` is img-path-only and unchanged, so no test edit is expected; if a stale comment references "drag right moves sky left", update the comment only. State the outcome in the report.

- [ ] **Step 8: Build + all tsx tests** — from `ui/`: `npm run build` (expected: clean), then all tsx tests:

```bash
for f in src/lib/__tests__/*.test.ts; do npx tsx "$f" || exit 1; done
```

Expected: clean build, every file `... 0 failed`.

- [ ] **Step 9: Manual wheel check (REQUIRED — note the outcome in the report).** No automated tsx test is possible for passive-listener behavior. Run `npm run dev`, open the Atlas inside a page tall enough to scroll, and wheel over the sky canvas: the view must zoom (1.12 steps, clamped) and the PAGE MUST NOT SCROLL, in each of the three modes (tile engine, img fallback — force WebGL off via the browser devtools or a temporary `tileGLSupported = () => false`, schematic). Wheel outside the canvas must still scroll the page normally.

- [ ] **Step 10: Commit**

```bash
git add ui/src/components/atlas/SkyCanvas.tsx ui/src/views/AtlasView.tsx
git commit -m "feat(atlas): mount WebGL tile engine (webgl-probed), grab-the-sky drag flip, non-passive wheel zoom; img pipeline is the fallback (spec §5)" \
  -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>" \
  -m "Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

---

### Task 6: Controls cleanup — Lock absorbs "Use camera FOV", drop stretch buttons

**Suggested model:** sonnet

**Files:**
- Modify: `ui/src/types.ts` (`FramingSession += prev_zoom_deg?: number`)
- Modify: `ui/src/views/AtlasView.tsx` (`onCameraFovLock` save/restore; stop passing `stretch`/`onStretchChange` to SurveyControls)
- Modify: `ui/src/components/atlas/SurveyControls.tsx` (remove "Use camera FOV" button + `useCameraFov`; remove stretch group + `stretch`/`onStretchChange` props)

**Interfaces:**
- Consumes: `setFraming`/`framing` (seam §3-4). `framing` is pure in-memory Zustand (seam §4) — `prev_zoom_deg` is additive, not persisted, zero-migration.
- Produces: `FramingSession.prev_zoom_deg?: number`; a Lock toggle that saves current zoom on-lock and restores on-unlock. Store `stretch` field + server `stretch` query param survive untouched (SkyCanvas fallback still sends `stretch={stretch}`).

- [ ] **Step 1: types.ts.** In `FramingSession` (types.ts:809-819) add after `fovZoomDeg`:

```ts
  prev_zoom_deg?: number;                  // zoom saved by the FOV-lock toggle (in-memory; not persisted)
```

- [ ] **Step 2: AtlasView `onCameraFovLock` save/restore (spec §6).** Replace the handler (AtlasView.tsx:430-434):

```ts
  // FOV lock (spec §6): on -> save the current zoom + apply camera FOV x1.6;
  // off -> restore the saved zoom (clamped) if present, else keep current.
  const onCameraFovLock = (locked: boolean) => {
    setCameraFovLock(locked);
    const clamp = (v: number) => Math.min(10, Math.max(0.1, v));
    if (locked) {
      if (frameFovDeg > 0)
        setFraming({ prev_zoom_deg: fovZoomDeg, fovZoomDeg: clamp(frameFovDeg * 1.6) });
    } else if (framing.prev_zoom_deg != null) {
      setZoom(clamp(framing.prev_zoom_deg));
    }
  };
```

(`setFraming` and `framing` are already in scope in AtlasView; `setZoom` is `setFraming({ fovZoomDeg })`.)

- [ ] **Step 3: AtlasView stops passing stretch to SurveyControls.** In the `<SurveyControls …/>` mount (AtlasView.tsx:756-778) REMOVE the two lines `stretch={stretch}` and `onStretchChange={setStretch}`. Keep everything else. `setStretch` and `framing.stretch` remain (SkyCanvas still receives `stretch={stretch}` for the fallback img URL). The `stretch` binding from the `framing` destructure (AtlasView.tsx:384) stays (still passed to SkyCanvas).

- [ ] **Step 4: SurveyControls props cleanup.** In `SurveyControlsProps` (SurveyControls.tsx:25-55) REMOVE `stretch: "linear" | "asinh";` and `onStretchChange: (stretch: "linear" | "asinh") => void;`. Remove them from the destructure (SurveyControls.tsx:62-68).

- [ ] **Step 5: SurveyControls remove the stretch group + the "Use camera FOV" button + `useCameraFov`.** Delete the entire Stretch `<div className="flex flex-col gap-1">…</div>` block (SurveyControls.tsx:106-126) — the survey `<label>` becomes the only child of the `flex-wrap` row. Delete the `useCameraFov` helper (SurveyControls.tsx:77-80) and the "Use camera FOV" `<button>` (SurveyControls.tsx:144-152). Keep the Lock `<div>`+`Toggle` (it drives `onCameraFovLock`, which now does the save/apply/restore) and `fitObject`.

- [ ] **Step 6: Build + tsx tests** — from `ui/`: `npm run build` (expected: clean — no unused `stretch`/`onStretchChange`/`useCameraFov`), then all tsx tests via the Task 5 Step 8 loop (expected: all pass). No new pure helper emerged, so no new test file.

- [ ] **Step 7: Commit**

```bash
git add ui/src/types.ts ui/src/views/AtlasView.tsx ui/src/components/atlas/SurveyControls.tsx
git commit -m "feat(ui/atlas): Lock toggle absorbs Use-camera-FOV (save/restore zoom); drop dead stretch buttons (spec §6)" \
  -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>" \
  -m "Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

---

### Task 7: Visual parity dev-gate + cross-check test + README + full suite

**Suggested model:** sonnet

**Files:**
- Create: `ui/src/lib/__tests__/tileParity.test.ts` (reads the `parity` vectors)
- Modify: `README.md` (tile-engine paragraph; replace stale pan/cutout copy if present)

**Interfaces:**
- Consumes: `pixUV2ang` (healpix.ts); the `parity` array in `healpix.vectors.json` (Task 2); `hips_local.render_cutout` (server, unchanged) for the visual gate.

- [ ] **Step 1: Automated cross-check test** — `ui/src/lib/__tests__/tileParity.test.ts`. Reads the 3 designated `parity` vectors (generated Python-side against M31 in Task 2) and asserts `pixUV2ang` matches within 1e-6 deg (angular distance):

```ts
// tileParity.test.ts — pins pixUV2ang against 3 Python-generated M31 samples
// tagged "parity" in healpix.vectors.json (tile-engine spec §7 cross-check).
import { pixUV2ang } from "../healpix";
// @ts-ignore  no @types/node; tsx supplies fs at runtime
import { readFileSync } from "node:fs";

interface UvCase { order: number; npix: number; u: number; v: number; raDeg: number; decDeg: number; }
const parity = (JSON.parse(
  readFileSync(new URL("./healpix.vectors.json", import.meta.url), "utf8"),
) as { parity: UvCase[] }).parity;

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const DEG = Math.PI / 180;
function angSepDeg(ra1: number, d1: number, ra2: number, d2: number): number {
  const c = Math.sin(d1 * DEG) * Math.sin(d2 * DEG)
    + Math.cos(d1 * DEG) * Math.cos(d2 * DEG) * Math.cos((ra1 - ra2) * DEG);
  return Math.acos(Math.min(1, Math.max(-1, c))) / DEG;
}

test("pixUV2ang matches the 3 M31 parity samples within 1e-6 deg", () => {
  assert(parity.length === 3, "3 parity vectors present");
  for (const c of parity) {
    const p = pixUV2ang(c.order, c.npix, c.u, c.v);
    assert(angSepDeg(p.raDeg, p.decDeg, c.raDeg, c.decDeg) < 1e-6,
      `parity npix ${c.npix} u ${c.u} v ${c.v}`);
  }
});

console.log(`tileParity.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
```

Run: `npx tsx src/lib/__tests__/tileParity.test.ts` (expected: pass).

- [ ] **Step 2: Visual parity dev-gate (REQUIRED — report the outcome).** Mirrors the offline-pack renderer's ground-truth step that caught the bit-convention errors. Two renders of the SAME M31 view (ra 10.68° = 0.712 h, dec 41.27°, fov 1.5°, 768 px):
  1. **Tile engine:** with online fetch ON, run `npm run dev`, open the Atlas on M31 at fov 1.5°, let tiles load, and export the canvas — in the browser console: `copy(document.querySelector('canvas.astro-surface canvas, canvas').toDataURL('image/png'))`, or add a temporary `canvas.toDataURL()` dump; save to `.superpowers/sdd/m31_tile_engine.png`.
  2. **Server truth:** ensure an order ≥ 5 `dss2color` pack exists (`./.venv/Scripts/python.exe -m astrodeck.catalog.survey_pack fetch --order 5`), then from `server/`:

```python
from pathlib import Path
from astrodeck.catalog.hips_local import render_cutout
from astrodeck.catalog.survey_pack import pack_dir
out = Path("../.superpowers/sdd/m31_server_render.jpg")
out.write_bytes(render_cutout(pack_dir("dss2color"), 10.68, 41.27, 1.5, 768))
print("server render:", out.resolve())
```

View BOTH (the Read tool renders images). M31's core, NGC 205, and M32 must sit in the SAME position, SAME orientation (North up), SAME E/W handedness. If the tile-engine image is mirrored or rotated relative to the server render, the fix is a `dx<->dy` swap in BOTH `gen_healpix_vectors.py` and `pixUV2ang` (regenerate vectors, re-run Tasks 2–3 tests) and/or the `tileMesh` sign lines — never weaken the tests. State the outcome explicitly in the report.

- [ ] **Step 3: README.** Replace any stale pan/cutout description with one tile-engine paragraph:

```markdown
## Slippy-sky Atlas

The Atlas renders survey imagery as a smoothly pan/zoomable WebGL tile map:
the browser fetches raw HiPS tiles through the server tile route
(`/api/survey/tile/...`), warps them through the exact TAN projection, and
upsamples from parent tiles so the view never blanks while you drag. Online
deep-zooms grow the offline pack on disk as a side effect. Where WebGL is
unavailable the Atlas falls back to the classic `<img>` cutout pipeline (the
`/api/survey/cutout.jpg` route), which is otherwise unchanged. Drag is
grab-the-sky on both axes (drag right pulls the sky right).
```

- [ ] **Step 4: Full suites (controller-level).**
  - Server, from `server/`: `./.venv/Scripts/python.exe -m pytest -q` (expect the prior suite + the ~10 new tile-route tests, 0 failures; proves the cutout/survey tests are still green untouched).
  - UI, from `ui/`: `npm run build` and the Task 5 Step 8 tsx loop (all `0 failed`).

- [ ] **Step 5: Commit**

```bash
git add ui/src/lib/__tests__/tileParity.test.ts README.md
git commit -m "test(atlas): pixUV2ang M31 parity cross-check + slippy-sky README; full-suite gate (spec §7)" \
  -m "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>" \
  -m "Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

---

## Post-plan verification (controller, not a task)

1. Full server suite from `server/`: `./.venv/Scripts/python.exe -m pytest -q` (0 failures; cutout/survey tests untouched-green).
2. From `ui/`: `npm run build` + every `src/lib/__tests__/*.test.ts` via `npx tsx` (all `0 failed`).
3. Push + `gh run watch` per grounded-CI rule.
4. Restart the UI, drive the M31 view through a real WebGL browser (smooth pan/zoom, no blanking, grab-the-sky direction), confirm the `<img>` fallback still renders when WebGL is forced off, and hand the user the manual smoke list (Lock save/restore, online-fetch pack growth on disk, degraded banner on all-failing).

## Known-minor (accepted, do not fix in this plan)

- `dss2red`/`twomass` mirror path strings ship only after the Task 1 Step 6 `curl` verification; until then those two slugs may 404 on-demand (dss2color is the default and verified).
- The GPU-texture LRU (128) and ImageBitmap LRU (256) can evict a texture still referenced by an in-flight parent-walk; the next dirty frame re-uploads from the bitmap (or re-fetches) — soft, never blank.
- `visibleTiles`' `viewportPx` parameter is unused (the grid is angular); it is kept for signature parity and is positionally safe (`order` after it is used, so `noUnusedParameters` does not flag it).

---

## Self-review

### Spec §§1–8 coverage (every bullet → task)

| Spec area | Requirement | Task |
|---|---|---|
| §0.1 | Client HiPS tile renderer replaces survey `<img>` | 4, 5 |
| §0.2 | Grab-the-sky drag on both axes (panTo + fallback) | 5 |
| §0.3 | Controls cleanup: Use-camera-FOV folded into Lock; stretch removed | 6 |
| §0 frozen | offline-first / cutout untouched / North-up East-left / pack tree + `_evict_cache` / night dimmer CSS | Globals + 1, 3, 5 |
| §1 | Tile route: slug registry + mirrors; validation 404/422; pack-hit immutable; offline 404 no-store zero-httpx; online mirror fetch + SOI + atomic write; single-flight; 200 MB disk guard; never touches `fetch_state` | 1 |
| §2 | `ang2pixNested`/`pixUV2ang`/`parentOf`/`childUVRect`; golden vectors from astropy-healpix; (u,v) convention pinned to hips_local | 2 |
| §3 | `tileOrderFor`/`visibleTiles`/`tileMesh`/`ancestorUV`; screen mapping | 3 |
| §4 | `tileGL` (init/upload/draw/LRU128/dispose); `TileEngine` props + loader (concurrency 6, AbortController, ImageBitmap LRU 256, negative-cache 45 s), rAF dirty loop, parent-walk ≤5, dpr sizing, CSS brightness, onFirstTile once, onAllFailing after 8 consecutive fails + zero-ever-drawn | 4 |
| §5 | SkyCanvas mounts TileEngine when survey && webglOK (module probe) else untouched img; panTo flip; native non-passive wheel-zoom listener (page-scroll trap) + manual check; skeleton→onFirstTile; degraded via onAllFailing→onSurveyError; slug map; no debounce for tile updates | 5 |
| §6 | Lock save/apply×1.6/restore via `prev_zoom_deg`; remove stretch group+props; picker unchanged; UI slug mirror | 6 |
| §7 | healpix golden gate; tileView tests; server tile-route pytest (all branches + single-flight + disk-guard); visual parity dev-gate + 3-sample cross-check; GL exempt; builds green | 2,3,1,7 |
| §8 | Out of scope (pinch/inertia/rotation/crossfade/eviction/red-2MASS UI/retiring cutout) — not implemented | — |

### Gaps found + fixed during drafting

- Spec §2's comment says `_X_FROM_EVEN_BITS=False, _JPG_FLIP_Y=False`; I verified the SHIPPED `hips_local.py` (lines 32-33) matches and cited the exact lines, then derived `pixUV2ang`/generator `dx=v, dy=u` from the deinterleave (lines 52-59) + sampling (106-107, 115) rather than trusting the spec prose. Task 7's parity gate + tileView orientation test backstop the derivation.
- Seam §8: `resolveJsonModule` is off and must not change → tests read the JSON via `node:fs` with a single `@ts-ignore` (no `@types/node`), matching the repo's `globalThis`-process idiom for keeping `tsc -b` clean.
- Seam §2: img-path "not edited" reconciled — only the mount GUARD changes; every img effect/state/element is byte-identical and enumerated.
- `noUnusedParameters`: `visibleTiles(viewportPx)` unused but safe (trailing rule); documented.

### Type-consistency pass (names/signatures across tasks)

All four §2 signatures, all four §3 signatures, and `TileEngineProps` are reproduced EXACTLY as specced. Cross-task types check: `pixUV2ang` returns `{raDeg,decDeg}` (consumed by tileView/tileCache/tests); `tileMesh` returns `{positions:Float32Array,uvs:Float32Array,indices:Uint16Array}` (consumed by TileEngine → `TileDraw`); `childUVRect` returns `{u0,v0,size}` (consumed by `ancestorUV`); slug strings `dss2color/dss2red/twomass` identical in server `SLUG_REGISTRY` and UI `SURVEY_SLUGS`; tile URL shape `/api/survey/tile/{slug}/{order}/{npix}.jpg` identical in route + TileEngine `keyOf`. No signature drift found.
