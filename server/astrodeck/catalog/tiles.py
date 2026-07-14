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
