"""Survey-cutout proxy (Sky-Atlas, design spec §4.3 / §9, Owner B).

`GET /api/survey/cutout.jpg` proxies a static HiPS cutout from CDS `hips2fits`,
rendered server-side to JPEG, so the browser displays a plain ``<img>`` under an
SVG we fully own.

Why proxy instead of Aladin Lite / raw tiles (spec §1):
  * sidesteps browser CORS,
  * gives us a disk cache (offline-at-the-scope friendly),
  * lets the cutout be requested in **TAN (gnomonic) projection** so the deg->px
    scale over the W×W image is linear and uniform — our SVG FOV rectangle maps
    to it with exact, dependency-free math (`lib/framing.ts`).

Correctness invariants (spec dispositions — non-negotiable):
  * ``ra_deg = ra_hours * 15`` — the query takes RA in *hours*; hips2fits wants
    degrees. A 15× error lands a random field (critique C2-#8a).
  * ``projection=TAN``, ``coordsys=icrs``, ``format=jpg`` and **no `rot` / no PA
    param** — hips2fits does not rotate the frame by PA; the image is always
    North-up and the SVG rectangle rotates over it (critique C1-A4 / C2-#8).
  * On upstream failure -> ``503 {"detail":"survey unavailable",
    "fallback":"schematic"}`` so the client switches to schematic mode with an
    inline banner (no toast).
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response

from ..auth import CAP_VIEW_STATUS, require
from ..auth.rbac import declare

from ..config import config_store
from ..hub import CAPTURE_DIR
from . import hips_local
from . import survey_pack as pack_mod

router = APIRouter()

# ----------------------------------------------------------------- constants
HIPS2FITS_URL = "https://alasky.cds.unistra.fr/hips-image-services/hips2fits"
_USER_AGENT = "AstroDeck/0.1"
_TIMEOUT_S = 6.0  # was 15.0 — 2 attempts ~= 12.4 s worst case; single-flight stops pileup
_CACHE_MAX_AGE = 86400  # 1 day; cutouts of a fixed field never change

# Survey crop width clamp (px). The displayed survey is square (width == height).
_WIDTH_DEFAULT = 768
_WIDTH_MIN = 256
_WIDTH_MAX = 1200

# Disk cache lives beside the captures so it shares the device's writable volume.
_SURVEY_CACHE_DIR = CAPTURE_DIR / "_survey"

# ------------------------------------------------------------ cache bound (P2-13)
#
# The disk cache shares the capture volume — on a small Pi SD card an unbounded
# pile of cutouts competes with FITS storage and can fill the card. Cap it by
# BOTH age (TTL) and total size; evict on every write (cheap: a single dir scan
# of small jpegs). The cache is purely an optimization, so eviction is best-effort
# and never raises into the request path.
_CACHE_TTL_S = 7 * 86400          # 7 days — a cutout of a fixed field is reusable
                                  # for a long while, but not forever (HiPS surveys
                                  # do get re-released; staleness here is harmless).
_CACHE_MAX_BYTES = 200 * 1024 * 1024   # 200 MB total cap (a few thousand cutouts).
_CACHE_MAX_FILES = 2000           # hard file-count cap (inode pressure on the SD).

# Snap-to-grid caching (Wave-1 spec §5.2): request geometry is SNAPPED onto a
# FOV-scaled grid BEFORE keying and BEFORE the upstream fetch, so the cached
# image always matches its key exactly. The snapped geometry is returned in
# X-Survey-* response headers; the client compensates the <= fov/40 residual
# with a CSS transform (ui/src/lib/surveyView.ts) — zero framing-accuracy loss.
#
# Keys are built from INTEGER bucket indices — never reconstructed floats — so
# two requests in the same bucket can never disagree at a float boundary (the
# pre-snap double-bucket bug, review 2026-07-12 Symptom 1 cause 3).
_FOV_LOG_STEP = 1.02          # fov snapped to a 2% logarithmic grid
_CENTER_STEPS_PER_FOV = 20    # center grid step = snapped_fov / 20 (deg, both axes)
_KEY_SALT = "v2"              # orphan pre-snap cache files (TTL eviction cleans them)

_ONLINE_FOV_MAX_DEG = 4.0   # below this the order-4 pack is soft on 768px (spec §4)
_PACK_KEY_SALT = "v2pk"     # pack renders; stretch normalized to "-" (baked-in)


def _snap_geometry(
    ra_hours: float, dec_deg: float, fov_deg: float
) -> tuple[float, float, float, tuple[int, int, int]]:
    """Snap (ra HOURS, dec, fov) onto the fov-scaled grid.

    Returns (ra_deg, dec_deg, fov_deg) SNAPPED — ra converted hours->DEGREES
    (the unit-critical *15, spec §4.3) and wrapped to [0,360), dec clamped to
    [-90,90] — plus the integer bucket indices (ra_idx, dec_idx, fov_idx).
    The RA step is a fixed sky-plane step (no cos-dec scaling): buckets get
    finer in true angle near the poles, which costs cache hits, never accuracy.
    """
    fov_idx = round(math.log(fov_deg) / math.log(_FOV_LOG_STEP))
    snapped_fov = _FOV_LOG_STEP ** fov_idx
    step = snapped_fov / _CENTER_STEPS_PER_FOV
    ra_deg = (ra_hours * 15.0) % 360.0  # <-- HOURS -> DEGREES (unit-critical)
    ra_idx = round(ra_deg / step)
    dec_idx = round(dec_deg / step)
    snapped_ra = (ra_idx * step) % 360.0
    snapped_dec = max(-90.0, min(90.0, dec_idx * step))
    return snapped_ra, snapped_dec, snapped_fov, (ra_idx, dec_idx, fov_idx)


# ------------------------------------------------------------- single-flight
# Concurrent requests for the SAME cache key coalesce: one goes upstream, the
# rest wait on the per-key lock and then serve the file it cached. Entries are
# refcounted and dropped when the last waiter leaves (no unbounded growth).
# Event-loop-only state: the dict is only mutated between awaits, so no extra
# guard lock is needed. NOTE: if the leader's fetch fails (503), each waiter
# retries upstream itself in turn — a failure is never cached.
_inflight: dict[str, list] = {}  # key -> [asyncio.Lock, refcount]


@asynccontextmanager
async def _single_flight(key: str):
    entry = _inflight.get(key)
    if entry is None:
        entry = [asyncio.Lock(), 0]
        _inflight[key] = entry
    entry[1] += 1
    try:
        async with entry[0]:
            yield
    finally:
        entry[1] -= 1
        if entry[1] <= 0:
            _inflight.pop(key, None)


def _cache_key(idx: tuple[int, int, int], width: int, survey: str, stretch: str) -> str:
    """SHA-1 over the INTEGER bucket indices + discrete params — the disk key."""
    ra_idx, dec_idx, fov_idx = idx
    raw = f"{_KEY_SALT}|{survey}|{ra_idx}|{dec_idx}|{fov_idx}|{width}|{stretch}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _pack_cache_key(idx: tuple[int, int, int], width: int, survey: str) -> str:
    """Disk key for LOCAL pack renders — distinct salt so an upstream upgrade
    is never masked; stretch is normalized ('-') because pack JPEGs bake it in."""
    ra_idx, dec_idx, fov_idx = idx
    raw = f"{_PACK_KEY_SALT}|{survey}|{ra_idx}|{dec_idx}|{fov_idx}|{width}|-"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _hips2fits_params(
    ra_deg: float, dec_deg: float, fov_deg: float, width: int, survey: str, stretch: str
) -> dict[str, str]:
    """Build the hips2fits query. NOTE: no ``rot`` param (spec §4.3)."""
    return {
        "hips": survey,
        "ra": f"{ra_deg:.6f}",
        "dec": f"{dec_deg:.6f}",
        "fov": f"{fov_deg:.6f}",
        "width": str(width),
        "height": str(width),  # square cutout — deg/px uniform in both axes
        "projection": "TAN",
        "coordsys": "icrs",
        "format": "jpg",
        "stretch": stretch,
    }


async def _fetch_cutout(params: dict[str, str]) -> bytes:
    """One request + one retry against hips2fits; returns JPEG bytes or raises.

    Any transport error or non-2xx after the retry raises ``RuntimeError`` so the
    caller can map it to the 503 schematic fallback.
    """
    headers = {"User-Agent": _USER_AGENT}
    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=_TIMEOUT_S, headers=headers) as client:
        for attempt in range(2):  # initial + one retry
            try:
                resp = await client.get(HIPS2FITS_URL, params=params)
                resp.raise_for_status()
                body = resp.content
                if not body:
                    raise RuntimeError("empty survey response")
                return body
            except Exception as exc:  # noqa: BLE001 — uniform fallback to 503
                last_exc = exc
                if attempt == 0:
                    await asyncio.sleep(0.4)
    raise RuntimeError(f"survey upstream failed: {last_exc}")


def _evict_cache(
    cache_dir: Path,
    *,
    ttl_s: float = _CACHE_TTL_S,
    max_bytes: int = _CACHE_MAX_BYTES,
    max_files: int = _CACHE_MAX_FILES,
    now: float | None = None,
) -> None:
    """Bound the survey disk cache by age + total size + file count (P2-13).

    Best-effort, off the event loop, never raises: the cache is an optimization,
    so any I/O hiccup just leaves the cache as-is. Eviction policy:

      1. delete any ``*.jpg`` older than ``ttl_s`` (mtime-based TTL);
      2. if the surviving set still exceeds ``max_bytes`` or ``max_files``, delete
         oldest-first (LRU-ish by mtime) until both caps are satisfied.

    Only ``*.jpg`` cache files are considered — a stray ``*.tmp`` from an
    interrupted write is left for the writer to overwrite/clean and never counts
    against the caps (it is unlinked opportunistically if clearly stale).
    """
    now = time.time() if now is None else now
    try:
        entries: list[tuple[float, int, Path]] = []
        for p in cache_dir.glob("*.jpg"):
            try:
                st = p.stat()
            except OSError:
                continue
            entries.append((st.st_mtime, st.st_size, p))
        # 1) TTL pass — drop anything older than the window.
        survivors: list[tuple[float, int, Path]] = []
        for mtime, size, p in entries:
            if now - mtime > ttl_s:
                try:
                    p.unlink()
                except OSError:
                    survivors.append((mtime, size, p))  # couldn't remove — still counts
            else:
                survivors.append((mtime, size, p))
        # 2) size/count pass — evict oldest-first until under both caps.
        survivors.sort(key=lambda e: e[0])  # oldest mtime first
        total = sum(size for _m, size, _p in survivors)
        count = len(survivors)
        i = 0
        while (total > max_bytes or count > max_files) and i < len(survivors):
            _m, size, p = survivors[i]
            i += 1
            try:
                p.unlink()
                total -= size
                count -= 1
            except OSError:
                pass  # leave it; move on so we never spin
        # 3) opportunistically reap clearly-stale temp files from crashed writes.
        for tmp in cache_dir.glob("*.tmp"):
            try:
                if now - tmp.stat().st_mtime > ttl_s:
                    tmp.unlink()
            except OSError:
                pass
    except OSError:
        pass  # cache eviction is best-effort; never a hard dependency


def _write_cache(path: Path, body: bytes) -> None:
    """Write the JPEG to the disk cache (best-effort; off the event loop), then
    bound the cache (P2-13) so it cannot fill a small Pi SD card."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.replace(path)  # atomic publish so a partial write is never served
    except OSError:
        pass  # cache is an optimization, never a hard dependency
    # Enforce the cache bound after each write. Cheap (one dir scan of small
    # jpegs) and off the event loop (this runs under asyncio.to_thread).
    _evict_cache(path.parent)


# Gated as of 2026-08-03. This and the tile route were the ONLY /api GETs with
# no capability — verified with a TestClient: every sibling returned 401 to an
# anonymous caller and these two returned 200. The boot RBAC assertion could not
# catch it because it only fails un-gated MUTATING routes, and these are GETs.
# They are GETs that WRITE, though: both populate a cache inside the capture
# volume and, with survey.online_fetch on, make outbound network requests — all
# with no credential, and the relay exposes them off-LAN. Their paths were never
# the problem (SHA-1 hex filenames here, registry slug + range-checked ints
# there); the missing credential was.
@router.get("/api/survey/cutout.jpg",
            dependencies=[Depends(require(CAP_VIEW_STATUS))])
@declare(CAP_VIEW_STATUS)
async def survey_cutout(
    ra: float = Query(..., description="Right ascension in HOURS (ra_deg = ra*15)"),
    dec: float = Query(..., ge=-90.0, le=90.0, description="Declination in degrees"),
    fov: float = Query(..., gt=0.0, le=20.0, description="Crop width in degrees"),
    width: int = Query(_WIDTH_DEFAULT, description="Output px (clamped 256-1200)"),
    survey: Literal[
        "CDS/P/DSS2/color", "CDS/P/DSS2/red", "CDS/P/2MASS/color"
    ] = Query("CDS/P/DSS2/color", description="HiPS id"),
    stretch: Literal["linear", "asinh"] = Query("linear"),
) -> Response:
    """Offline-first TAN survey cutout to JPEG, disk-cached (spec §4).

    Routing (spec §4): when ``survey.online_fetch`` is off (the default) the
    LOCAL pack is the only source; when on, upstream hips2fits is tried for
    fov < 4° with the pack as fallback (and, for fov >= 4°, the pack first with
    upstream only as a last resort). Snapped geometry is always echoed in the
    X-Survey-* headers; failure -> 503 schematic fallback.
    """
    width = max(_WIDTH_MIN, min(_WIDTH_MAX, width))
    snapped_ra, snapped_dec, snapped_fov, idx = _snap_geometry(ra, dec, fov)

    up_key = _cache_key(idx, width, survey, stretch)
    pk_key = _pack_cache_key(idx, width, survey)
    up_path = _SURVEY_CACHE_DIR / f"{up_key}.jpg"
    pk_path = _SURVEY_CACHE_DIR / f"{pk_key}.jpg"
    cache_headers = {
        "Cache-Control": f"max-age={_CACHE_MAX_AGE}",
        # Snapped geometry (spec Wave-1 §5.2) — the client reads these to place
        # the frame exactly (residual compensated by a CSS transform).
        "X-Survey-Ra-Deg": f"{snapped_ra:.6f}",
        "X-Survey-Dec-Deg": f"{snapped_dec:.6f}",
        "X-Survey-Fov-Deg": f"{snapped_fov:.6f}",
    }

    online = bool(config_store.cfg().survey.online_fetch)
    want_upstream = online and snapped_fov < _ONLINE_FOV_MAX_DEG

    def _file(path: Path, source: str) -> FileResponse:
        return FileResponse(path, media_type="image/jpeg",
                            headers={**cache_headers, "X-Survey-Source": source})

    # Fast path: an upstream-quality file always wins; a pack file only when
    # upstream isn't wanted (spec §4 step 2 — upstream gets its upgrade chance).
    if up_path.exists():
        return _file(up_path, "upstream")
    if pk_path.exists() and not want_upstream:
        return _file(pk_path, "pack")

    async with _single_flight(up_key if want_upstream else pk_key):
        if up_path.exists():
            return _file(up_path, "upstream")
        if pk_path.exists() and not want_upstream:
            return _file(pk_path, "pack")

        if want_upstream:
            params = _hips2fits_params(snapped_ra, snapped_dec, snapped_fov, width, survey, stretch)
            try:
                body = await _fetch_cutout(params)
                await asyncio.to_thread(_write_cache, up_path, body)
                return Response(body, media_type="image/jpeg",
                                headers={**cache_headers, "X-Survey-Source": "upstream"})
            except RuntimeError:
                pass  # never 503 while the pack can still render (spec §4 step 4)

        pack = pack_mod.pack_present(survey)
        if pack is not None:
            if pk_path.exists():
                return _file(pk_path, "pack")
            try:
                body = await asyncio.to_thread(
                    hips_local.render_cutout, pack, snapped_ra, snapped_dec, snapped_fov, width)
            except Exception:  # noqa: BLE001 — any render failure falls through
                body = None
            if body:
                await asyncio.to_thread(_write_cache, pk_path, body)
                return Response(body, media_type="image/jpeg",
                                headers={**cache_headers, "X-Survey-Source": "pack"})

        if online and not want_upstream:
            # fov >= 4° and the pack failed — upstream as last resort (spec §4 step 6)
            params = _hips2fits_params(snapped_ra, snapped_dec, snapped_fov, width, survey, stretch)
            try:
                body = await _fetch_cutout(params)
                await asyncio.to_thread(_write_cache, up_path, body)
                return Response(body, media_type="image/jpeg",
                                headers={**cache_headers, "X-Survey-Source": "upstream"})
            except RuntimeError:
                pass

    raise HTTPException(
        status_code=503,
        detail={"detail": "survey unavailable", "fallback": "schematic"},
    )
