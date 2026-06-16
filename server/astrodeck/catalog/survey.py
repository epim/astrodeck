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
import time
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response

from ..hub import CAPTURE_DIR

router = APIRouter()

# ----------------------------------------------------------------- constants
HIPS2FITS_URL = "https://alasky.cds.unistra.fr/hips-image-services/hips2fits"
_USER_AGENT = "AstroDeck/0.1"
_TIMEOUT_S = 15.0
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

# Cache-key quantization (spec §4.3): finer than the draft so a fine-framing
# nudge doesn't return a stale image while the overlay moves (critique C2-#15).
#   ra -> 0.0002 h  (~3 arcsec)   dec -> 0.002 deg   fov -> 0.01 deg
_Q_RA_H = 0.0002
_Q_DEC_DEG = 0.002
_Q_FOV_DEG = 0.01


def _quantize(value: float, step: float) -> float:
    """Round ``value`` to the nearest ``step`` (stable, sign-correct cache key)."""
    return round(value / step) * step


def _cache_key(
    ra_hours: float,
    dec_deg: float,
    fov_deg: float,
    width: int,
    survey: str,
    stretch: str,
) -> str:
    """SHA-1 over the *quantized* params — the disk-cache key only.

    The interactive client requests un-quantized coords and debounces 300 ms;
    quantization here just collapses near-identical fields onto one cached file.
    """
    qra = _quantize(ra_hours, _Q_RA_H)
    qdec = _quantize(dec_deg, _Q_DEC_DEG)
    qfov = _quantize(fov_deg, _Q_FOV_DEG)
    raw = f"{survey}|{qra:.4f}|{qdec:.4f}|{qfov:.4f}|{width}|{stretch}"
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


@router.get("/api/survey/cutout.jpg")
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
    """Proxy a TAN survey cutout to JPEG, disk-cached; 503 -> schematic fallback."""
    width = max(_WIDTH_MIN, min(_WIDTH_MAX, width))
    ra_deg = ra * 15.0  # <-- HOURS -> DEGREES (spec §4.3, unit-critical)

    key = _cache_key(ra, dec, fov, width, survey, stretch)
    cache_path = _SURVEY_CACHE_DIR / f"{key}.jpg"
    cache_headers = {"Cache-Control": f"max-age={_CACHE_MAX_AGE}"}

    if cache_path.exists():
        return FileResponse(cache_path, media_type="image/jpeg", headers=cache_headers)

    params = _hips2fits_params(ra_deg, dec, fov, width, survey, stretch)
    try:
        body = await _fetch_cutout(params)
    except RuntimeError:
        # Upstream unreachable/slow/blank — honest 503 so the client draws the
        # offline schematic (inline banner, no toast). Overlay math is unchanged.
        raise HTTPException(
            status_code=503,
            detail={"detail": "survey unavailable", "fallback": "schematic"},
        )

    # Persist off the event loop (small image, but disk I/O shouldn't block).
    await asyncio.to_thread(_write_cache, cache_path, body)
    return Response(body, media_type="image/jpeg", headers=cache_headers)
