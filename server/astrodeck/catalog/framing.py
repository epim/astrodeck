"""Framing-assistant router (Sky-Atlas, design spec §5, Owner C).

The mosaic engine is **server-canonical**: ``compute_mosaic`` here is the byte-for-
byte mirror of ``ui/src/lib/framing.ts`` (``mosaicGrid`` / ``deproject`` /
``mosaicTotalFov``). The Atlas page computes a zero-latency live overlay with the
client mirror while the user drags, but "Send to Plan" always routes through
``POST /api/framing/mosaic`` so the slew targets handed to the engine are
identical to the preview.

Correctness invariants (spec dispositions — keep these exactly in sync with the
client mirror):
  * gnomonic deproject special-cases ``rho < 1e-12 -> (ra0, dec0)`` (no NaN —
    the common "open on target, hit Send" path);
  * every emitted panel RA is wrapped ``ra % 24`` into ``[0, 24)`` (Target.ra_hours
    is ``Field(ge=0, lt=24)`` — an unwrapped RA 422s the whole plan);
  * panel tiling FOV is always bin-1; total mosaic FOV is the tangent-plane
    extent ((cols-(cols-1)*overlap)*fov_x etc.), not raw degrees of RA;
  * panels are ordered boustrophedon (snake) by row to minimise slew travel;
  * J2000 invariant: registration is always J2000, never live JNow mount RA.
"""
from __future__ import annotations

import math

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..config import ARCSEC_PER_RAD

router = APIRouter()

# rho->0 guard threshold (spec §5). Below this the tangent point IS the center, so
# the inverse projection returns (ra0, dec0) verbatim — never divides by rho and
# never ships NaN to the mount on the common "open on target, hit Send" path.
RHO_EPS = 1e-12


# ----------------------------------------------------------------- request model

class MosaicSpecIn(BaseModel):
    """Mirrors the TS ``MosaicSpec`` (``ui/src/types.ts``) plus an optional
    ``date`` so each panel's ``transit_alt`` can be filled from the visibility
    module. ``pixel_size_um`` is optional — supplied only when the caller wants a
    derived ``pixel_scale_arcsec`` in the result (the client computes its own)."""
    ra_hours: float = Field(ge=0, lt=24)
    dec_deg: float = Field(ge=-90, le=90)
    rows: int = Field(1, ge=1, le=10)
    cols: int = Field(1, ge=1, le=10)
    overlap: float = Field(0.0, ge=0.0, le=0.5)
    rotation_deg: float = 0.0
    fov_x_deg: float = Field(gt=0)
    fov_y_deg: float = Field(gt=0)
    # optional — drives per-panel transit_alt when present + visibility available
    date: str | None = None
    # optional — only to derive pixel_scale_arcsec in the result
    pixel_size_um: float | None = None
    focal_length_mm: float | None = None


# ----------------------------------------------------------------- gnomonic math

def _wrap_ra_hours(ra_hours: float) -> float:
    """Normalize RA hours into ``[0, 24)``. Mirrors the client ``wrapRaHours`` and
    the server's ``ra % 24`` — a value rounding to exactly 24 folds back to 0 so
    Target.ra_hours ``Field(lt=24)`` never rejects the plan."""
    r = ra_hours % 24.0
    if r < 0.0:
        r += 24.0
    if r >= 24.0:
        r -= 24.0
    return r


def deproject(
    xi_deg: float, eta_deg: float, ra0_hours: float, dec0_deg: float,
) -> tuple[float, float]:
    """Inverse gnomonic (TAN): standard coords ``(xi, eta)`` in DEGREES -> sky
    ``(ra_hours, dec_deg)``. ``rho -> 0`` is special-cased to the tangent point to
    avoid a divide-by-zero NaN. ``ra_hours`` is normalized to ``[0, 24)``. Byte-
    identical to ``deproject`` in ``ui/src/lib/framing.ts``."""
    xi = math.radians(xi_deg)
    eta = math.radians(eta_deg)
    rho = math.hypot(xi, eta)

    # the common path: open on-target, hit Send. No division, no NaN.
    if rho < RHO_EPS:
        return _wrap_ra_hours(ra0_hours), dec0_deg

    dec0 = math.radians(dec0_deg)
    ra0 = math.radians(ra0_hours * 15.0)
    c = math.atan(rho)
    cos_c = math.cos(c)
    sin_c = math.sin(c)
    cos_dec0 = math.cos(dec0)
    sin_dec0 = math.sin(dec0)

    dec = math.asin(cos_c * sin_dec0 + (eta * sin_c * cos_dec0) / rho)
    ra = ra0 + math.atan2(
        xi * sin_c, rho * cos_dec0 * cos_c - eta * sin_dec0 * sin_c)

    ra_hours = _wrap_ra_hours(math.degrees(ra) / 15.0)
    return ra_hours, math.degrees(dec)


# ----------------------------------------------------------------- core compute

def compute_mosaic(spec: MosaicSpecIn | dict) -> dict:
    """Canonical mosaic panel grid — the importable pure function (no I/O, no
    transit_alt). Mirrors ``mosaicGrid`` + ``mosaicTotalFov`` in
    ``ui/src/lib/framing.ts`` exactly so the client preview == the server slew
    targets. Returns a ``MosaicResult`` dict; panels carry NO ``transit_alt``
    (the route fills that when a ``date`` is supplied)."""
    if isinstance(spec, dict):
        spec = MosaicSpecIn(**spec)

    rows = max(1, round(spec.rows))
    cols = max(1, round(spec.cols))
    overlap = min(0.5, max(0.0, spec.overlap))
    theta = math.radians(spec.rotation_deg)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    step_x = spec.fov_x_deg * (1.0 - overlap)
    step_y = spec.fov_y_deg * (1.0 - overlap)

    panels: list[dict] = []
    for r in range(rows):
        # snake: even rows left->right, odd rows right->left.
        col_order = list(range(cols))
        if r % 2 == 1:
            col_order.reverse()
        for c in col_order:
            gx = (c - (cols - 1) / 2.0) * step_x
            gy = ((rows - 1) / 2.0 - r) * step_y
            xi = gx * cos_t - gy * sin_t
            eta = gx * sin_t + gy * cos_t
            ra_hours, dec_deg = deproject(
                xi, eta, spec.ra_hours, spec.dec_deg)
            panels.append({
                "row": r,
                "col": c,
                "ra_hours": ra_hours,   # already %24-wrapped by deproject
                "dec_deg": dec_deg,
                "rotation_deg": spec.rotation_deg,
            })

    total_fov_x = (cols - (cols - 1) * overlap) * spec.fov_x_deg
    total_fov_y = (rows - (rows - 1) * overlap) * spec.fov_y_deg

    # pixel_scale only when derivable from the optional optics; else 0.0.
    pixel_scale = 0.0
    if (spec.pixel_size_um is not None and spec.pixel_size_um > 0
            and spec.focal_length_mm is not None and spec.focal_length_mm > 0):
        pixel_scale = ARCSEC_PER_RAD * spec.pixel_size_um / spec.focal_length_mm

    return {
        "panels": panels,
        "total_fov_x_deg": total_fov_x,
        "total_fov_y_deg": total_fov_y,
        "frame_fov_x_deg": spec.fov_x_deg,
        "frame_fov_y_deg": spec.fov_y_deg,
        "pixel_scale_arcsec": pixel_scale,
    }


# ----------------------------------------------------------------- route

@router.post("/api/framing/mosaic")
async def post_mosaic(spec: MosaicSpecIn) -> dict:
    """Canonical mosaic for ``MosaicSpecIn`` -> ``MosaicResult``.

    When ``spec.date`` is provided (and the visibility module imports cleanly),
    each panel's ``transit_alt`` is filled with its **peak altitude tonight**
    (NOT the instantaneous "now" alt). astropy transforms run off the event loop.
    """
    result = compute_mosaic(spec)

    if spec.date:
        try:
            import asyncio

            from .visibility import transit_alt_for

            # Bound the fan-out: up to rows*cols (<=100) panels must not all hit
            # the shared default executor at once (starves config/plan disk I/O).
            sem = asyncio.Semaphore(8)

            async def _alt(p: dict) -> float | None:
                try:
                    async with sem:
                        return await asyncio.to_thread(
                            transit_alt_for, p["ra_hours"], p["dec_deg"],
                            date=spec.date)
                except Exception:
                    return None

            alts = await asyncio.gather(*[_alt(p) for p in result["panels"]])
            for p, a in zip(result["panels"], alts):
                if a is not None:
                    p["transit_alt"] = a
        except Exception:
            # visibility unavailable (e.g. astropy import issue) — leave panels
            # without transit_alt rather than failing the mosaic.
            pass

    return result
