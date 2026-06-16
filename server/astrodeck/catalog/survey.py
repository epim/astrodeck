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


def _write_cache(path: Path, body: bytes) -> None:
    """Write the JPEG to the disk cache (best-effort; off the event loop)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.replace(path)  # atomic publish so a partial write is never served
    except OSError:
        pass  # cache is an optimization, never a hard dependency


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
