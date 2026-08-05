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

import asyncio
import logging
import math

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..auth import CAP_VIEW_SITE_DERIVED, require
from ..auth.rbac import declare
from ..config import ARCSEC_PER_RAD

router = APIRouter()
log = logging.getLogger(__name__)

# rho->0 guard threshold (spec §5). Below this the tangent point IS the center, so
# the inverse projection returns (ra0, dec0) verbatim — never divides by rho and
# never ships NaN to the mount on the common "open on target, hit Send" path.
RHO_EPS = 1e-12


# ----------------------------------------------------------------- request model

class MosaicSpecIn(BaseModel):
    """Mirrors the TS ``MosaicSpec`` (``ui/src/types.ts``) plus the two ways to
    ask for per-panel ``transit_alt`` — ``date`` (a named night) or
    ``transit_alt`` (tonight). ``pixel_size_um`` is optional — supplied only when
    the caller wants a derived ``pixel_scale_arcsec`` in the result (the client
    computes its own)."""
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
    # Ask for TONIGHT's peak altitude per panel WITHOUT naming a night.
    #
    # It has to be its own flag because ``VisibilityNight.date`` cannot be
    # replayed into ``date`` above: ``visibility.compute_night`` reports the UTC
    # date of the night's solar-midnight anchor, while ``_night_anchor_unix``
    # reads ``date`` as the civil date of the EVENING and adds 24 h. Measured at
    # every longitude <= 0 that round-trip lands one whole night late (lon -120:
    # anchor 2026-08-02T08:00Z is reported as "2026-08-02", which replays as
    # 2026-08-03T08:00Z). A client echoing the date back would have been handed
    # TOMORROW night's altitudes under tonight's chart — a plausible wrong
    # answer, which this module already treats as worse than an error. Asking
    # for "tonight" out loud is the only version that cannot drift.
    transit_alt: bool = False
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


# ------------------------------------------------- per-panel transit altitude

def _why(e: BaseException) -> str:
    """One-line cause for a panel that has no transit altitude.

    Carries the exception TYPE, because the message alone is often empty — the
    ``FileNotFoundError`` the config-store cold-load race raised (fixed
    2026-08-01) stringifies to nothing at all, and an empty reason is exactly
    the silence this field exists to end.
    """
    detail = str(e).strip()
    return f"{type(e).__name__}: {detail}" if detail else type(e).__name__


async def _stamp_transit_alt(panels: list[dict], date: str | None) -> None:
    """Fill each panel's ``transit_alt`` for ``date`` (``None`` = tonight), and
    where that is impossible put the REASON on the panel as
    ``transit_alt_error``.

    This was two bare ``except Exception`` swallows that logged nothing and said
    nothing: a panel that failed simply had no ``transit_alt`` key, so the mosaic
    answered for some panels and was silent about the others, and the client
    could not tell "not asked for" from "we tried and could not". That silence is
    why the config-store cold-load race surfaced only as an intermittent red test
    instead of a report — and it would hide the next cause (an astropy fault, an
    unset site, an OSError, a future writer race) exactly as well. A panel with
    no altitude now names what stopped it, on the wire and in the server log.

    Raises ``HTTPException(422)`` for a malformed ``date`` — see
    ``visibility.check_night_date``: ``_night_anchor_unix`` silently falls back
    to TONIGHT on an unparseable date, so without this an off-by-one month in
    any client got tonight's altitudes labelled as the night it asked for.
    """
    try:
        # Lazy: visibility pulls astropy + the hub/auth stack, and a mosaic that
        # asked for neither a night nor tonight must not pay for it.
        from .visibility import check_night_date, transit_alt_for
    except Exception as e:  # noqa: BLE001 - report it; never fail the mosaic
        why = _why(e)
        log.warning("mosaic transit altitudes unavailable: %s", why)
        for p in panels:
            p["transit_alt_error"] = f"visibility unavailable ({why})"
        return

    # None/"" past this point means TONIGHT — and reaching here at all means the
    # route saw ``transit_alt=true``, i.e. the caller asked for tonight in words.
    # That gate replaces the blanket early-return this used to do: a caller that
    # merely FORGOT its date still gets nothing, so it can never be handed
    # tonight's altitudes labelled as the night it thought it asked for.
    date = check_night_date(date)

    # Bound the fan-out: up to rows*cols (<=100) panels must not all hit the
    # shared default executor at once (starves config/plan disk I/O).
    sem = asyncio.Semaphore(8)

    async def _alt(p: dict) -> tuple[float | None, str | None]:
        try:
            async with sem:
                v = await asyncio.to_thread(
                    transit_alt_for, p["ra_hours"], p["dec_deg"], date=date)
            # A non-finite altitude is not a measurement. It would also take the
            # WHOLE mosaic down: starlette's JSONResponse renders with
            # allow_nan=False, so one NaN panel 500s the response.
            if v is None or not math.isfinite(v):
                return None, f"visibility returned no usable altitude ({v!r})"
            return float(v), None
        except Exception as e:  # noqa: BLE001 - one bad panel, not a dead mosaic
            return None, _why(e)

    results = await asyncio.gather(*[_alt(p) for p in panels])
    for p, (alt, reason) in zip(panels, results):
        if alt is not None:
            p["transit_alt"] = alt
        else:
            p["transit_alt_error"] = reason or "transit altitude not computed"
    lost = [p for p in panels if "transit_alt_error" in p]
    if lost:
        log.warning("%d of %d mosaic panels have no transit altitude: %s",
                    len(lost), len(panels), lost[0]["transit_alt_error"])


# ----------------------------------------------------------------- route

@router.post("/api/framing/mosaic",
             dependencies=[Depends(require(CAP_VIEW_SITE_DERIVED))])
@declare(CAP_VIEW_SITE_DERIVED)
async def post_mosaic(spec: MosaicSpecIn) -> dict:
    """Canonical mosaic for ``MosaicSpecIn`` -> ``MosaicResult``.

    With ``spec.date`` (a named night) or ``spec.transit_alt`` (tonight), each
    panel's ``transit_alt`` is filled with its **peak altitude that night** (NOT
    the instantaneous "now" alt); a panel the visibility module could not answer
    for carries ``transit_alt_error`` saying why. astropy transforms run off the
    event loop.

    Neither flag => no altitudes AND no error keys. Silence is the right answer
    to a question nobody asked; the Atlas "Send to Plan" path posts exactly this
    shape and must not pay for astropy on up to 100 panels.

    GATED ON view.status LIKE EVERY OTHER READ SURFACE, and NOT exempt from the
    route-capability assertion. It was exempt once, as "pure stateless compute --
    mutates no state, commands no device, so it is read-equivalent". That
    rationale was about MUTATION and never covered what the response contains.
    Once ``transit_alt`` landed, the answer became a function of the observing
    site: peak altitude for a given dec IS the site's latitude, recoverable by
    sweeping dec and reading off the maximum. The 2026-08-01 cross-cut review did
    exactly that against this route with no principal at all and recovered the
    latitude to a tenth of a degree. Precise coordinates are treated as a secret
    everywhere else in this codebase (test_rbac_enforcement keeps them out of the
    bus log); an unauthenticated caller must not be able to ask the rig where it
    is. Gating also closes an ungated 100-panel astropy fan-out on a box that may
    be an SBC.
    """
    result = compute_mosaic(spec)

    if spec.date or spec.transit_alt:
        await _stamp_transit_alt(result["panels"], spec.date)

    return result
