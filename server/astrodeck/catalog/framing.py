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

THE RE-FRAME CARRY AND THE ANGLE BUDGET ARE SERVER-ONLY (#189, spec 3.3, A.1,
A.2, A.5). ``reframe_carry`` decides when a moved layout keeps its counts,
``convergence_share`` and ``angle_tolerance_deg`` how much of the overlap
meridian convergence and a fixed camera's angle may spend. The modal gets
their answers from this route (``reframe``) and from the compile; an offline
mirror of them is the modal's to write (S4), against these as the reference.
"""
from __future__ import annotations

import asyncio
import logging
import math
import sys

from fastapi import APIRouter, Depends
from pydantic import (BaseModel, ConfigDict, Field, field_validator,
                      model_validator)

from ..auth import CAP_VIEW_SITE_DERIVED, require
from ..auth.rbac import declare
from ..config import ARCSEC_PER_RAD, Optics
from ..config import fov_deg as _config_fov_deg
# ``identity`` is pure (hashlib, json, uuid) and holds the one definition of
# "the same geometry" (its canonical text) and of an anchor's stored form, so
# the carry reads both from it rather than keeping a second copy here.
from ..flows import identity
from .coords import angular_sep_deg

router = APIRouter()
log = logging.getLogger(__name__)

# rho->0 guard threshold (spec §5). Below this the tangent point IS the center, so
# the inverse projection returns (ra0, dec0) verbatim — never divides by rho and
# never ships NaN to the mount on the common "open on target, hit Send" path.
RHO_EPS = 1e-12

# h->0 guard for the FORWARD projection: h is the cosine of the angular distance
# from the tangent point, so it reaches zero 90 degrees away and goes negative
# behind it. Clamping to a tiny positive value yields a huge but FINITE offset
# instead of an Infinity/NaN, which every caller then clips off-frame cleanly.
# Same constant and same reasoning as ``ui/src/lib/framing.ts``.
H_EPS = 1e-12

#: The one default overlap between neighbouring panels (spec 2.4 GRID): 25%,
#: as the FRACTION ``compute_mosaic`` takes. One server constant, exported so
#: the clients stop carrying two (``store.ts`` wrote 0.25 and
#: ``next/hubs/sky/frame/mosaic.ts`` 0.15). It is what a new block is framed
#: with. It is NOT what an omitted ``overlap`` means to this route (still 0),
#: and NOT ``identity.SINGLE_OVERLAP``: that is a stored contract, the value a
#: TARGET saved before S3 is keyed with, and must not follow this one if the
#: default is ever re-decided.
DEFAULT_OVERLAP = 0.25

#: Ruling 3 (#189 Revision 2): "if the re-frame is less than half the width of
#: the overlap, carry over" the panel counts. The owner's first guess, named so
#: that it can be revisited with S7's measured centring residual: at the limit
#: a carried move spends the half of the overlap A.2 leaves for pointing error.
REFRAME_CARRY_FRACTION = 0.5

#: The share of the overlap meridian convergence and a fixed camera's angle
#: error may use TOGETHER (A.2, Revision 1): ``k = max(0, ROTATION_BUDGET -
#: c)``. The other half is left for pointing error. It is the same half as
#: ``angle_check.angle_tolerance_deg``'s default ``k``, which is A.2 with no
#: convergence. Not the carry fraction, though both are a half today: they are
#: two budgets, decided separately.
ROTATION_BUDGET = 0.5

#: How far north of a panel centre local north is sampled (A.1): small enough
#: that the projection's curvature over the step is negligible, far larger
#: than its rounding.
NORTH_PROBE_DEG = 1e-4

#: A panel's four corners, as signs of its half-width and half-height.
_CORNERS = ((-1, -1), (-1, 1), (1, -1), (1, 1))


def fov_deg_from_optics(optics: Optics) -> tuple[float, float, float]:
    """Field of view (width, height, diagonal) in degrees for the rig's
    configured optics (#168) -- the server half of "one FOV function serves
    both UIs". Delegates straight to ``config.fov_deg`` at bin 1, same as
    ``hub.effective_optics``.

    ``optics.reducer`` IS NEVER APPLIED HERE, matching ``Optics.reducer``'s own
    doc (config.py:97-111) and ``f_ratio``'s precedent (config.py): the reducer
    is recorded, not multiplied, so ``focal_length_mm`` alone drives the field
    -- already the reduced number if "USE THE REDUCED FOCAL LENGTH" was
    pressed, the explicit number otherwise. This is the SAME rule the client
    mirrors apply: ``ui/src/lib/framing.ts``'s ``fovFromOptics`` and
    ``ui/src/next/lib/fov.ts``'s ``fovDeg`` both read only the focal length,
    through the one shared ``fovDegFromSensorMm`` formula. Before #168,
    ``next/lib/fov.ts`` multiplied the reducer in while this server path and
    ``fovFromOptics`` did not, so a mosaic framed from config and a UI preview
    of the same config could disagree by the reducer factor whenever one was
    recorded. A test pins all three to the same field for one config.
    """
    return _config_fov_deg(optics.focal_length_mm, optics.pixel_size_um,
                            optics.sensor_width_px, optics.sensor_height_px)


# ----------------------------------------------------------------- request model

class MosaicAnchorIn(BaseModel):
    """A block's anchor in ``MosaicSpecIn``'s own words (spec 3.3): the
    geometry its counts started at. The route takes the stored
    ``frameAnchor`` text too, which is what the save compares against; this
    form is for a caller that holds the geometry rather than the text.

    One rule differs from ``MosaicSpecIn``: the camera field may be 0. A
    block framed before MATCH CAMERA holds none, and that is a legal anchor
    (its carry threshold is 0). ``rotation_deg`` None or negative is "any
    angle", as everywhere a block's rotation is read (#150).

    NaN and the infinities are refused, as the stored text refuses them
    (``identity.anchor_geometry``): measured, a NaN frame's corners are
    all NaN, the shared separation clamps each to a move of 0, and the
    answer is a carry (#324)."""
    model_config = ConfigDict(allow_inf_nan=False)

    ra_hours: float = Field(ge=0, lt=24)
    dec_deg: float = Field(ge=-90, le=90)
    rows: int = Field(1, ge=1, le=10)
    cols: int = Field(1, ge=1, le=10)
    overlap: float = Field(0.0, ge=0.0, le=0.5)
    rotation_deg: float | None = 0.0
    fov_x_deg: float = Field(ge=0)
    fov_y_deg: float = Field(ge=0)


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
    # optional — the block's anchor (spec 3.3, ruling 3): its stored
    # ``frameAnchor`` text, or the geometry as ``MosaicAnchorIn``. Given, the
    # answer carries ``reframe``, ``reframe_carry(anchor, this layout)``, which
    # the modal shows live and which is the verdict the save will reach.
    # Blank is no anchor (a block saved before S3), and gets no ``reframe``.
    anchor: str | MosaicAnchorIn | None = None

    @field_validator("anchor")
    @classmethod
    def _anchor_text_is_an_anchor(cls, value):
        """Text that is not an anchor is the caller's error, a 422 naming
        what is wrong (``identity.anchor_geometry`` raises ``ValueError``),
        not a 500 from inside the answer."""
        if isinstance(value, str):
            identity.anchor_geometry(value)
        return value

    @model_validator(mode="after")
    def _a_reframe_needs_a_finite_layout(self):
        """With an anchor, the layout asked for is measured against it,
        and a NaN or an infinite angle or field would be measured as a
        carry (``_frame``): the caller's error, a 422. Without one the
        route answers as it always has, so this is the anchor's check
        alone, and a caller that sends no anchor sees no new refusal."""
        anchor = self.anchor
        if anchor is None or (isinstance(anchor, str)
                              and not anchor.strip()):
            return self
        for name in ("rotation_deg", "fov_x_deg", "fov_y_deg"):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(
                    f"{name} is not finite ({getattr(self, name)!r}), so "
                    f"there is no layout to measure against the anchor")
        return self


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


def project(
    ra_hours: float, dec_deg: float, ra0_hours: float, dec0_deg: float,
) -> tuple[float, float]:
    """Forward gnomonic (TAN): sky ``(ra_hours, dec_deg)`` -> standard coords
    ``(xi, eta)`` in DEGREES about the tangent point ``(ra0, dec0)``. Byte-
    identical to ``project`` in ``ui/src/lib/framing.ts`` — the same expression,
    the same guard, the same order of operations — and pinned to it by the golden
    vectors shared between ``tests/test_catalog_frame_id.py`` and
    ``ui/src/lib/__tests__/framing.test.ts``.

    THE INVERSE OF ``deproject`` ABOVE, and the reason this module now carries
    both: placing a catalogued object on a solved camera frame needs sky ->
    plane, and until now the only forward gnomonic in the tree was in TypeScript.
    A second one written locally inside the frame-identification code would be
    exactly the "two projections that drift apart" defect this package's header
    comments keep naming, so it lives here, beside its inverse, in the module
    that already declares itself the client mirror.

    ``xi`` increases with RA (East) and ``eta`` with declination (North), which
    is the FITS intermediate-world-coordinate convention — so the pair can be fed
    straight through an inverted CD matrix to get pixels.
    """
    ra = math.radians(ra_hours * 15.0)
    dec = math.radians(dec_deg)
    ra0 = math.radians(ra0_hours * 15.0)
    dec0 = math.radians(dec0_deg)
    dra = ra - ra0

    sin_dec = math.sin(dec)
    cos_dec = math.cos(dec)
    sin_dec0 = math.sin(dec0)
    cos_dec0 = math.cos(dec0)
    cos_dra = math.cos(dra)

    # cosine of the angular distance from the tangent point
    h = sin_dec * sin_dec0 + cos_dec * cos_dec0 * cos_dra
    safe_h = H_EPS if abs(h) < H_EPS else h

    xi = (cos_dec * math.sin(dra)) / safe_h
    eta = (sin_dec * cos_dec0 - cos_dec * sin_dec0 * cos_dra) / safe_h
    return math.degrees(xi), math.degrees(eta)


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


# ------------------------------------------------- re-frame carry (ruling 3)

def _is_any_angle(rotation_deg: float | None) -> bool:
    """None or negative is "any angle" (#150), as ``identity`` keys it."""
    return rotation_deg is None or float(rotation_deg) < 0.0


def _frame(value) -> dict:
    """A layout as ``reframe_carry`` compares it, in the anchor's own field
    names (``identity.anchor_geometry``'s shape): ``rows``, ``cols``,
    ``overlap`` (a fraction), ``rotation_deg``, ``fov_x``, ``fov_y`` and
    either ``ra_hours`` and ``dec_deg`` or ``name`` (or both, for a named
    frame the caller has placed).

    Taken from an anchor's stored text, a ``MosaicSpecIn`` or
    ``MosaicAnchorIn``, or a mapping already in that shape. A blank anchor is
    refused: "no anchor yet" is the SAVE's case, where the current geometry
    becomes the anchor (spec 3.3), and there is nothing here to compare.

    An anchor that RECORDS a field (``identity.RECORDED_FIELDS``, ruling 4)
    comes back laid out with it (``_laid_out``): as a frame it is the field
    the camera has, whatever field its key was made with."""
    if isinstance(value, str):
        held = identity.anchor_geometry(value)
        if held is None:
            raise ValueError(
                "a blank anchor is no anchor yet: the save makes the current "
                "geometry the anchor (spec 3.3), so there is nothing to "
                "compare")
        return _laid_out(held)
    if isinstance(value, (MosaicSpecIn, MosaicAnchorIn)):
        return _finite({
            "ra_hours": value.ra_hours, "dec_deg": value.dec_deg,
            "rotation_deg": value.rotation_deg, "rows": value.rows,
            "cols": value.cols, "overlap": value.overlap,
            "fov_x": value.fov_x_deg, "fov_y": value.fov_y_deg})
    frame = dict(value)
    missing = [k for k in identity.SHAPE_FIELDS if k not in frame]
    if missing or not ("name" in frame or "ra_hours" in frame):
        raise ValueError(f"a frame needs {list(identity.SHAPE_FIELDS)} and "
                         f"either ra_hours and dec_deg or a name; it has "
                         f"{sorted(frame)}")
    return _finite(_laid_out(frame))


def _laid_out(frame: dict) -> dict:
    """``frame`` with the field it RECORDS in place of the field it keys
    (#351, S4 orchestrator ruling 4): a completed anchor keys the field of
    none it was anchored with and records the camera's field beside it
    (``identity.complete_anchor``). Laid out, it is the recorded field, so
    its corners are where the camera's frame corners are, its threshold is
    half that field's overlap, and against the same field it is one
    geometry ("unchanged"). A frame that records nothing is returned as it
    is; half a recorded pair is refused, as the stored text refuses it."""
    recorded = [k for k in identity.RECORDED_FIELDS if k in frame]
    if not recorded:
        return frame
    if len(recorded) != len(identity.RECORDED_FIELDS):
        raise ValueError(f"a recorded field is a pair, "
                         f"{list(identity.RECORDED_FIELDS)}, and this frame "
                         f"holds only {recorded}")
    out = {k: v for k, v in frame.items()
           if k not in identity.RECORDED_FIELDS}
    out["fov_x"], out["fov_y"] = (frame["recorded_fov_x"],
                                  frame["recorded_fov_y"])
    return out


def _finite(frame: dict) -> dict:
    """``frame``, once every number in it is finite; None is "any angle"
    and passes.

    A NaN or an infinity is refused, not measured, because measured it
    CARRIES: every corner deprojects to NaN, ``coords.angular_sep_deg``
    clamps its cosine with ``max(-1.0, min(1.0, nan))``, which is 1.0, and
    each corner "moves" 0.0. A campaign would keep its counts across a
    geometry nobody can lay out, a plausible wrong answer. The stored
    text already refuses one (``identity.anchor_geometry``); this gives a
    mapping and the two request models the same refusal (#324)."""
    for key in ("ra_hours", "dec_deg", "rotation_deg", "overlap",
                "fov_x", "fov_y"):
        value = frame.get(key)
        if value is not None and not math.isfinite(float(value)):
            raise ValueError(f"frame field {key!r} is not finite: "
                             f"{value!r}")
    return frame


def _grid(frame: dict) -> tuple[int, int]:
    """(rows, cols), rounded and floored at 1 as ``compute_mosaic`` does."""
    return max(1, round(frame["rows"])), max(1, round(frame["cols"]))


def _overlap_fraction(frame: dict) -> float:
    """The overlap, clamped to [0, 0.5] as ``compute_mosaic`` clamps it."""
    return min(0.5, max(0.0, float(frame["overlap"])))


def _same_geometry(a: dict, b: dict) -> bool:
    """Whether two frames are ONE geometry: the same canonical text, which is
    the same identity key. Below the key's precision (1e-6 h of RA is
    0.054") two values are one geometry, so an anchor read back from its own
    rounded text is unchanged from what it was written from."""
    def text(f: dict) -> str:
        rows, cols = _grid(f)
        shape = dict(rows=rows, cols=cols, overlap=f["overlap"],
                     fov_x=f["fov_x"], fov_y=f["fov_y"])
        if "name" in f:
            return identity.canonical_name(f["name"], f["rotation_deg"],
                                           **shape)
        return identity.canonical_geometry(f["ra_hours"], f["dec_deg"],
                                           f["rotation_deg"], **shape)
    return text(a) == text(b)


def _overlap_width_deg(frame: dict) -> float:
    """``w`` of spec 3.3: the overlap width of the frame's grid, the narrower
    of the strips that exist, ``overlap x fov_x`` between columns and
    ``overlap x fov_y`` between rows. A 1x1 has neither, and takes
    ``overlap x min(fov_x, fov_y)``: the strip it would share with a
    neighbour on its narrower side."""
    overlap = _overlap_fraction(frame)
    fov_x, fov_y = float(frame["fov_x"]), float(frame["fov_y"])
    rows, cols = _grid(frame)
    widths = []
    if cols > 1:
        widths.append(overlap * fov_x)
    if rows > 1:
        widths.append(overlap * fov_y)
    return min(widths) if widths else overlap * min(fov_x, fov_y)


def _corners(frame: dict, at: tuple[float, float] | None) -> dict:
    """Every panel's four corners, ``{(row, col): [(ra_hours, dec_deg) x 4]}``.

    The panels come from ``compute_mosaic``, the one projection, and each
    corner is placed at its panel's angle in THAT PANEL's own tangent plane
    with ``deproject``, where the camera frame actually lies. "Any angle" is
    laid out at 0: no angle is recorded, so none can have moved, and two
    any-angle frames compare their positions and fields alone.

    A frame that holds a name and no coordinates (a name-keyed block's
    anchor) is laid out at ``at``, where the catalogue puts the object now;
    without one there is nowhere to lay it out."""
    if "ra_hours" in frame:
        ra_hours, dec_deg = frame["ra_hours"], frame["dec_deg"]
    elif at is not None:
        ra_hours, dec_deg = at
    else:
        raise ValueError(
            f"the frame of {frame.get('name')!r} holds a name and no "
            f"coordinates: pass at=(ra_hours, dec_deg), where the catalogue "
            f"puts it now")
    rotation = frame["rotation_deg"]
    angle = 0.0 if _is_any_angle(rotation) else float(rotation)
    rows, cols = _grid(frame)
    fov_x, fov_y = float(frame["fov_x"]), float(frame["fov_y"])
    # ``model_construct``, not the constructor: ``MosaicSpecIn`` refuses a
    # camera field of 0, a request rule for this route (a mosaic with no
    # field tiles nothing), and a block with no field recorded is a legal
    # anchor whose panels all sit at its centre. ``compute_mosaic`` itself is
    # well defined there, and is the only projection used.
    spec = MosaicSpecIn.model_construct(
        ra_hours=ra_hours, dec_deg=dec_deg, rows=rows, cols=cols,
        overlap=_overlap_fraction(frame), rotation_deg=angle,
        fov_x_deg=fov_x, fov_y_deg=fov_y)
    corners: dict = {}
    for p in compute_mosaic(spec)["panels"]:
        theta = math.radians(p["rotation_deg"])
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        points = []
        for sx, sy in _CORNERS:
            gx, gy = sx * fov_x / 2.0, sy * fov_y / 2.0
            # The same turn ``compute_mosaic`` gives the grid, so a corner
            # sits where the camera frame's corner is.
            xi = gx * cos_t - gy * sin_t
            eta = gx * sin_t + gy * cos_t
            points.append(deproject(xi, eta, p["ra_hours"], p["dec_deg"]))
        corners[(p["row"], p["col"])] = points
    return corners


def reframe_carry(anchor, new, *,
                  at: tuple[float, float] | None = None) -> dict:
    """Whether a re-framed block keeps its counts: ruling 3's "if the re-frame
    is less than half the width of the overlap, carry over", as one pure
    function (spec 3.3). The save calls it to decide whether to keep a
    block's anchor, and this route to show the modal the same verdict live.

    ``anchor`` is the geometry the counts started at, ``new`` the layout as
    framed now; each is an anchor's stored text, a ``MosaicSpecIn`` or
    ``MosaicAnchorIn``, or a mapping in ``identity.anchor_geometry``'s shape.
    ``at`` places a frame that holds a name and no coordinates.

    Answers ``{carry, max_move_deg, threshold_deg, reason}``, where
    ``reason`` is the rule that decided, in the order they are asked:

    * ``"identity"``: another named object, or a name against typed
      coordinates. Another key, so no move is measured.
    * ``"grid"``: rows or cols differ. A panel of another grid is another
      panel, so no move is measured either.
    * ``"angle"``: the angle crosses between "any" and a set value. Frames
      shot at an unknown angle are not frames at a known one.
    * ``"unchanged"``: one geometry to the identity key's precision. Carried,
      with a move of 0, whatever the threshold. This is what keeps a block
      with no camera field: its threshold is 0, and ``0 < 0`` is false.
    * ``"move"``: laid out with ``compute_mosaic``, ``max_move_deg`` is the
      largest angular distance any panel corner travels (``_corners``).
      Corners, not centres, so that one number covers a shift, a turn (which
      moves a single panel's corners around a centre that stays put) and a
      change of camera field. Carried when it is under the threshold.

    ``threshold_deg`` is ``REFRAME_CARRY_FRACTION`` of the overlap width of
    the ANCHOR's grid (``_overlap_width_deg``), never the new one's: the
    anchor is what the counts started at, and a threshold read from the new
    grid would let an edit raise its own allowance. It is 0 for a block with
    no camera field, so only an unchanged geometry keeps its anchor. An
    anchor that records a field beside a keyed field of none (a COMPLETED
    anchor, ``identity.complete_anchor``, #351, S4 orchestrator ruling 4) is
    laid out with the recorded field and takes its threshold from it
    (``_frame``), so a block whose field was recorded after its counts
    started is judged, from then on, against the field the camera has. The
    anchor is compared with the new layout, never with the previous save, so
    that moves under the threshold cannot add up across saves; which anchor
    to pass is the caller's, and the save's is always the stored one.
    Completing an anchor is the caller's too (the save's and the route's),
    and never this function's: here an anchor with no field is judged as
    one.

    ``max_move_deg`` is None where no move was measured. Every value is plain
    JSON, so the route ships the answer as it is."""
    old, now = _frame(anchor), _frame(new)
    threshold = REFRAME_CARRY_FRACTION * _overlap_width_deg(old)

    def verdict(carry: bool, move: float | None, reason: str) -> dict:
        return {"carry": carry, "max_move_deg": move,
                "threshold_deg": float(threshold), "reason": reason}

    if old.get("name") != now.get("name"):
        return verdict(False, None, "identity")
    if _grid(old) != _grid(now):
        return verdict(False, None, "grid")
    if _is_any_angle(old["rotation_deg"]) != _is_any_angle(now["rotation_deg"]):
        return verdict(False, None, "angle")
    if _same_geometry(old, now):
        return verdict(True, 0.0, "unchanged")
    before, after = _corners(old, at), _corners(now, at)
    move = max(angular_sep_deg(ra0, dec0, ra1, dec1)
               for key, points in before.items()
               for (ra0, dec0), (ra1, dec1) in zip(points, after[key]))
    return verdict(move < threshold, move, "move")


# ------------------------------------------- convergence and angle budget

def _local_north_deg(panel: dict, ra0_hours: float, dec0_deg: float) -> float:
    """Local north at a panel centre, as a direction on the GRID's tangent
    plane: degrees from +eta toward +xi (east). Found as A.1 found it, by
    projecting a point ``NORTH_PROBE_DEG`` north of the centre; within that
    distance of the pole the probe goes south instead, and is reversed."""
    ra, dec = panel["ra_hours"], panel["dec_deg"]
    step = NORTH_PROBE_DEG if dec + NORTH_PROBE_DEG <= 90.0 else -NORTH_PROBE_DEG
    x0, y0 = project(ra, dec, ra0_hours, dec0_deg)
    x1, y1 = project(ra, dec + step, ra0_hours, dec0_deg)
    sign = 1.0 if step > 0 else -1.0
    return math.degrees(math.atan2(sign * (x1 - x0), sign * (y1 - y0)))


def _seam_share(lever_deg: float, turn_deg: float, width_deg: float) -> float:
    """The share of one overlap strip a relative turn uses at the far corner:
    the shared edge swings by ``lever x sin(turn)`` against a strip
    ``width`` wide (A.1). No strip and a swing is every gap there is: inf."""
    swing = lever_deg * abs(math.sin(math.radians(turn_deg)))
    if swing == 0.0:
        return 0.0
    if width_deg <= 0.0:
        return math.inf
    return swing / width_deg


def convergence_share(spec: MosaicSpecIn | dict) -> float:
    """The share of the overlap meridian convergence uses at the worst seam
    of the grid (A.1): 0.389 for a 4x1 (4 columns by 1 row, S4 orchestrator
    ruling 1) of 2.0 x 1.33 deg at 10% at Dec 75, 0.031 for a 3x3 at 25% at
    Dec 41. Doctor M6 warns on it, and ``angle_tolerance_deg`` spends what
    it leaves.

    Every panel is shot at one angle against LOCAL north, and laid out on
    the grid's tangent plane local north turns from panel to panel
    (``_local_north_deg``). Neighbours are turned against each other by the
    difference, so their shared edge swings and eats the overlap at the far
    corner. Panels side by side in a row share a vertical edge: it swings by
    ``(fov_y / 2) sin(delta)`` against the ``overlap x fov_x`` strip, which is
    A.1's formula. Panels one above the other in a column share a horizontal
    edge: ``(fov_x / 2) sin(delta)`` against ``overlap x fov_y``.

    A.1 WEIGHED ONLY THE FIRST, because it was measured at angle 0, where a
    row is what differs in RA and the column pairs barely turn. At any other
    angle the columns differ in RA too, and at 90 deg they are the only
    pairs that do: a 3x3 at 25% at Dec 75 turned 90 deg uses 21.6% down its
    columns and 0.9% along its rows. Both kinds are weighed here, each
    against its own strip, and wherever A.1 was measured this agrees with it
    (#321 asks for the spec to say so).

    A single panel has no neighbour and answers 0. A grid with no overlap
    answers ``math.inf`` if anything turns at all, which is not JSON: a
    caller that shows it says "no overlap"."""
    if isinstance(spec, dict):
        spec = MosaicSpecIn(**spec)
    rows, cols = max(1, round(spec.rows)), max(1, round(spec.cols))
    overlap = min(0.5, max(0.0, spec.overlap))
    fov_x, fov_y = spec.fov_x_deg, spec.fov_y_deg
    north = {(p["row"], p["col"]): _local_north_deg(p, spec.ra_hours,
                                                    spec.dec_deg)
             for p in compute_mosaic(spec)["panels"]}

    def turn(a: float, b: float) -> float:
        return abs((a - b + 180.0) % 360.0 - 180.0)

    share = 0.0
    for (r, c), here in north.items():
        if c + 1 < cols:        # side by side: a shared vertical edge
            share = max(share, _seam_share(
                fov_y / 2.0, turn(here, north[(r, c + 1)]), overlap * fov_x))
        if r + 1 < rows:        # one above the other: a shared horizontal edge
            share = max(share, _seam_share(
                fov_x / 2.0, turn(here, north[(r + 1, c)]), overlap * fov_y))
    return share


def angle_tolerance_deg(spec: MosaicSpecIn | dict) -> float:
    """How far a fixed camera may sit off the planned angle before the corner
    overlap runs out, for THIS grid (A.2, combined with convergence by
    Revision 1): 5.97 deg for a 3x3 of 2.0 x 1.33 deg at 25% at Dec 41, 0.47
    deg for a 4x1 at 10% at Dec 75. What ``to_plan`` puts on a group as
    ``angle_tolerance_deg``, and what the modal's tolerance line says.

    Convergence and angle error eat the same corners, so they share one
    budget: ``k = max(0, ROTATION_BUDGET - c)`` with ``c =
    convergence_share(spec)``. Together they never use more than half the
    overlap, and the other half is left for pointing error. The angle that
    spends ``k`` is ``angle_check.angle_tolerance_deg``'s A.2 formula, the one
    the run's angle check was written and tested against, so there is one
    copy of it; it answers 0 for a ``k`` at or below zero, not finite (an
    infinite share, from a grid with no overlap) or for no overlap. That 0 is
    M15, a grid whose convergence has spent the budget, and never a negative
    angle. For a single row, column or panel no hole can open between panels,
    so the number is conservative there."""
    # Imported here, not at the top: ``sequence`` loads the engine, which a
    # mosaic preview has no use for.
    from ..sequence.angle_check import angle_tolerance_deg as a2_tolerance
    if isinstance(spec, dict):
        spec = MosaicSpecIn(**spec)
    # No floor here: ``a2_tolerance`` is the one that reads k <= 0 as none.
    k = ROTATION_BUDGET - convergence_share(spec)
    return a2_tolerance(spec.fov_x_deg, spec.fov_y_deg,
                        min(0.5, max(0.0, spec.overlap)), k)


# ------------------------------------------------- per-panel transit altitude

def _why(e: BaseException) -> str:
    """One-line cause for a panel that has no transit altitude.

    Carries the exception TYPE, because the message alone is often empty — the
    ``FileNotFoundError`` the config-store cold-load race raised (fixed
    2026-08-01) stringifies to nothing at all, and an empty reason is exactly
    the silence this field exists to end.

    EXCEPT FOR AN OPERATOR-WORDED ONE WITH A MESSAGE (#405 item 1).
    ``visibility.NoSite`` carries a sentence written for the operator ("no
    observing site is saved, ... save the site in Settings"), and the type in
    front of it put "NoSite:" in the PANELS night card, the Sky hub's framing
    card and the classic Atlas's mosaic summary on every rig with no site,
    which is every fresh install. Its message is the whole reason. With an
    empty message it keeps the type, the one thing left to say.
    """
    detail = str(e).strip()
    if not detail:
        return type(e).__name__
    if _operator_worded(e):
        return detail
    return f"{type(e).__name__}: {detail}"


def _operator_worded(e: BaseException) -> bool:
    """Whether ``e`` is an exception whose message was written for the
    operator: ``visibility.NoSite``, the one the transit altitudes raise.

    Looked up in ``sys.modules``, never imported: ``visibility`` pulls astropy
    and the hub and auth stack, and ``_why`` also explains a failure to
    import it, when importing it again would fail again. An exception can
    only be a ``NoSite`` once the module that defines the class has loaded."""
    visibility = sys.modules.get(f"{__package__}.visibility")
    no_site = getattr(visibility, "NoSite", None)
    return no_site is not None and isinstance(e, no_site)


async def _stamp_transit_alt(panels: list[dict], date: str | None, *,
                             site: dict | None = None) -> None:
    """Fill each panel's ``transit_alt`` for ``date`` (``None`` = tonight), and
    where that is impossible put the REASON on the panel as
    ``transit_alt_error``.

    AT ``site``, WHEN GIVEN (#336). The site is handed to
    ``visibility.transit_alt_for``, so a caller that resolves a night for a
    site of its own gets its panels' altitudes for that site. Before #336
    this took no site, and every altitude was the hub's: Tonight drew a
    block's curve at the site it was handed and its panels at the
    configured one, and a script resolving a synthetic site printed panel
    altitudes at the real one, which is a latitude oracle (#140). ``None``
    reads the hub's site through ``compute_night``, which is what the
    mosaic route below wants: that route answers for the rig's own site,
    and it passes nothing.

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
                    transit_alt_for, p["ra_hours"], p["dec_deg"], date=date,
                    site=site)
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

def _reframe_answer(spec: MosaicSpecIn) -> dict | None:
    """The route's ``reframe``: ``reframe_carry`` of the given anchor against
    the layout asked for, or None when no anchor (or a blank one) was given.

    A NAMED ANCHOR holds no position, because a name-keyed block's position
    is the catalogue's answer, and a planet's moves. The modal frames such a
    block where the catalogue puts it now, which is the spec's position, so
    its change of SHAPE is measured there, with the anchor's name carried
    over. Whether the name still resolves to that object is the save's to
    decide: it is the save that resolves names.

    MATCH CAMERA FOLLOWS RULING 4 (#351, spec 2.5). An anchor with no
    camera field, against a layout that differs from it only by recording
    one, is COMPLETED first (``identity.completes``), as the save completes
    it (``save_rules._anchor_on_save``): laid out with the layout's field,
    it is one geometry with it and carries. Judged as a field of none it
    would read "restart" for a save that keeps every id, and the modal
    would ask a question about a restart that never happens."""
    anchor = spec.anchor
    if anchor is None or (isinstance(anchor, str) and not anchor.strip()):
        return None
    held = _frame(anchor)
    now = _frame(spec)
    if "name" in held:
        now["name"] = held["name"]
    if identity.completes(held, now):
        held = {**held, "fov_x": now["fov_x"], "fov_y": now["fov_y"]}
    return reframe_carry(held, now, at=(spec.ra_hours, spec.dec_deg))


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

    With ``spec.anchor`` (the block's stored ``frameAnchor``, or the geometry
    as ``MosaicAnchorIn``), the answer adds ``reframe``: how far this layout
    moves every panel corner against the anchor, what the anchor's grid
    allows, and whether the counts carry (``reframe_carry``, spec 2.5 and
    3.3). The modal shows it live, and it is the verdict the save reaches, so
    the modal cannot promise a carry the save refuses. No anchor, or a blank
    one, and there is no ``reframe`` key. It adds nothing site-derived (a move
    and a threshold, from geometry the caller sent) and changes nothing about
    the gate below.

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

    reframe = _reframe_answer(spec)
    if reframe is not None:
        result["reframe"] = reframe

    return result
