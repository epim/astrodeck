# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The assembled mosaic preview: every panel's live stack on one picture.

A mosaic night keeps one stack per panel (``sessionstack.SessionStacker``) and
each is a picture of its own tile. This lays them side by side where the sky
puts them, so the operator can see whether the mosaic is going to cover the
field and where the seams fall (#779, #172 part B).

IT IS A RENDER, NOT A STACK. Nothing here folds one panel into another's
accumulators: the panels' running means are read (``SessionStacker.
panel_planes``, a read that changes nothing), resampled onto a canvas and
blended where they overlap, and the canvas is thrown away with the response.

THE PLACEMENT. Each panel is a ``Placement``: the centre, the angle and the
footprint the plan gave it. The stacker never sees the plan (it must not
import the planning layer); the caller turns a plan into this table
(``placements_for``) and hands it in. The footprint is
``framing.panel_footprint``, the one corner math the coverage check and the
re-frame carry measure, and the canvas is the gnomonic plane
(``framing.project``) about the mosaic's mean direction.

The picture reads the way a sky chart is read: north up, east to the left. A
panel's grid is the array the stacker was fed, which is the array saved to the
FITS file the plate solve reads (nothing between the camera and the stack
flips a row or a column). That solve reads it in FITS order, row 0 being y = 1,
and a standard solution (CD1_1 < 0, CD2_2 > 0, the one the simulator's solver
stamps and ``coverage`` calls standard) runs x west and y north. So at a panel
angle of 0 a grid's columns run toward the picture's right and its first row is
the picture's BOTTOM edge; the angle turns the panel the way
``panel_footprint`` turns its rectangle. A rig whose solutions have the
opposite parity (a mirrored optical train) is not read here: that parity is in
the stamped WCS, which this does not consult.

THE GRID OF ITS OWN. A restarted panel can sit on a coarser grid than its
neighbours (the memory budget coarsens it), so no scale is assumed: each
panel's grid is laid on its own footprint, which is the panel's pixel scale
(the footprint's field over the grid's size, the downsample factor and the
sensor's scale included). The map from the canvas into a panel's grid is the
plane-to-plane map between two gnomonic projections, which is a homography,
so it is solved exactly from the footprint's four corners and applied to every
pixel at once. A grid finer than the canvas is block-averaged down first
(``block_mean``), so a coarse picture is not a sampled one.

THE BOUND. The long edge is at most ``size``, and the picture is never finer
than the finest panel's own grid: a small mosaic on a huge ``size`` is not
upsampled into a large blank-looking file.

THE OVERLAP is feathered: a panel's weight rises from 0 at its edge to 1 over
``FEATHER_FRACTION`` of its short side, and where panels overlap the picture
is their weighted mean. A pixel only one panel covers is that panel's value
whatever its weight.

A HOLE. A panel in the table with no stack in the stacker's answer (never
stacked, or released by the memory budget and not restarted) is black with its
footprint outlined. The stacker's answer is the only source of pixels, so the
picture cannot show a stack the stacker no longer holds.

ONE STRETCH. The composite's rule applied across panels: per-channel black
and one shared range (``stretch_channels``), taken over every covered pixel
of the canvas once. Seams then show coverage, and a panel that is brighter is
drawn brighter, instead of every panel being stretched to the same look.

WHAT LEAVES. Pixels and counts: the response names no sky coordinate (the
route is gated like the other stack pixel routes, and nothing here is derived
from the site).
"""
from __future__ import annotations

import io
import math
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

from ..catalog import framing
from ..sequence import coverage
from ..sequence.models import SequencePlan
from ..sequence.session import Session
from .sessionstack import PanelPlanes, block_mean, mix_channels, stretch_channels

#: Blank border around the footprints, in pixels, so an outline on the edge is
#: whole.
MARGIN = 4
#: A panel's weight reaches 1 this far in from its edge, as a share of its
#: short side. The overlap is typically 10 to 20 percent of the field, so the
#: blend spans it.
FEATHER_FRACTION = 0.1
OUTLINE_WIDTH = 3
OUTLINE_RGB = (235, 235, 235)
JPEG_QUALITY = 85
#: The footprint's corners (``panel_footprint``'s Z order: -x-y, -x+y, +x-y,
#: +x+y) that a panel's grid corners (0, 0), (w, 0), (w, h), (0, h) are laid on:
#: columns run toward -x (west at angle 0) and rows toward +y (north).
_PIXEL_CORNERS = (2, 0, 1, 3)
#: The ring order of a footprint's outline.
_OUTLINE = (0, 1, 3, 2)


@dataclass(frozen=True)
class Placement:
    """One panel of the mosaic, as the plan places it. ``key`` is the stack's
    panel key (the target id) and ``name`` the target's name, which keys a
    panel the stack was fed with no id."""
    key: str
    name: str
    ra_hours: float
    dec_deg: float
    pa_deg: float
    fov_x: float
    fov_y: float


@dataclass(frozen=True)
class Assembly:
    """The picture before it is stretched: each channel's blended plane and
    where it has data, the panels that are holes with their outlines on the
    canvas (``[(x, y)] x 4`` in ring order), and one row per panel for the
    caption."""
    planes: dict[str, np.ndarray]
    cover: dict[str, np.ndarray]
    holes: list[Placement]
    outlines: dict[str, list[tuple[float, float]]]
    panels: list[dict]
    height: int
    width: int


@dataclass(frozen=True)
class _Frame:
    """The canvas: the tangent point and the plane's scale, and the canvas
    pixel of the tangent plane's origin."""
    tangent: tuple[float, float]
    scale: float
    cx: float
    cy: float
    height: int
    width: int

    def corner(self, ra_hours: float, dec_deg: float) -> tuple[float, float]:
        xi, eta = framing.project(ra_hours, dec_deg, *self.tangent)
        return self.cx - xi / self.scale, self.cy - eta / self.scale


# ----------------------------------------------------------- the placements
def placements_for(plan: SequencePlan,
                   stacked: Collection[str]) -> list[Placement]:
    """The panels of the mosaic group the stack is holding, as a table.

    The group is the one that has the most panels in ``stacked`` (panel keys:
    target ids, or names for a stack fed with no id), the first on a tie. A
    group with no field recorded cannot be laid out and is passed over; a plan
    with no mosaic places nothing. A panel with no angle is laid at 0, as the
    coverage check lays an "any angle" panel."""
    best: list[Placement] = []
    best_hits = -1
    for group in plan.groups:
        if group.kind != "mosaic":
            continue
        field = coverage.group_field(group)
        members = [t for t in plan.targets
                   if t.mosaic_group == group.id and not t.calibration]
        if field is None or not members:
            continue
        hits = sum(1 for t in members if t.id in stacked or t.name in stacked)
        if hits > best_hits:
            best_hits = hits
            best = [Placement(
                key=t.id, name=t.name, ra_hours=t.ra_hours, dec_deg=t.dec_deg,
                pa_deg=coverage.planned_angle(group, t) or 0.0,
                fov_x=field[0], fov_y=field[1]) for t in members]
    return best


def session_of_run(run_id: str, live: Session | None,
                   load_all: Callable[[], Iterable[Session]]
                   ) -> Session | None:
    """The session whose plan placed the panels of this stack: the live one,
    else the stored session that has the stack's run among its nights. The
    stack's run id is the engine's report id, which ``Session.nights`` lists.
    None when neither is found (including a stack with no run yet)."""
    if live is not None:
        return live
    if not run_id:
        return None
    for session in load_all():
        if run_id in session.nights:
            return session
    return None


# ------------------------------------------------------------- the geometry
def _tangent_point(rings: Iterable[Sequence[tuple[float, float]]]
                   ) -> tuple[float, float]:
    """The mean direction of every footprint corner, as ``(ra_hours,
    dec_deg)``: the plane's origin, so no panel is far from it."""
    x = y = z = 0.0
    for ring in rings:
        for ra_hours, dec_deg in ring:
            ra, dec = math.radians(ra_hours * 15.0), math.radians(dec_deg)
            x += math.cos(dec) * math.cos(ra)
            y += math.cos(dec) * math.sin(ra)
            z += math.sin(dec)
    norm = math.sqrt(x * x + y * y + z * z)
    return ((math.degrees(math.atan2(y, x)) % 360.0) / 15.0,
            math.degrees(math.asin(max(-1.0, min(1.0, z / norm)))))


def _frame(rings: Mapping[str, Sequence[tuple[float, float]]],
           finest_deg: float | None, size: int) -> _Frame:
    """The canvas that holds every footprint with its long edge at most
    ``size`` pixels, and no finer than ``finest_deg`` a pixel (None: no panel
    is stacked, so no grid sets a floor)."""
    tangent = _tangent_point(rings.values())
    pts = [framing.project(ra, dec, *tangent)
           for ring in rings.values() for ra, dec in ring]
    xi_max = max(p[0] for p in pts)
    eta_max = max(p[1] for p in pts)
    span_x = xi_max - min(p[0] for p in pts)
    span_y = eta_max - min(p[1] for p in pts)
    scale = max(span_x, span_y) / max(1, int(size) - 2 * MARGIN)
    if finest_deg is not None:
        scale = max(scale, finest_deg)
    return _Frame(
        tangent=tangent, scale=scale,
        cx=MARGIN + xi_max / scale, cy=MARGIN + eta_max / scale,
        height=math.ceil(span_y / scale) + 2 * MARGIN,
        width=math.ceil(span_x / scale) + 2 * MARGIN)


def _canvas_corners(ring: Sequence[tuple[float, float]],
                    frame: _Frame) -> list[tuple[float, float]]:
    """A footprint's four corners on the canvas, in ring order."""
    return [frame.corner(ra, dec) for ra, dec in ring]


def _homography(src: Sequence[tuple[float, float]],
                dst: Sequence[tuple[float, float]]) -> np.ndarray:
    """The 3x3 projective map taking the four ``src`` points to the four
    ``dst`` points (the direct linear solve, with the corner fixed at 1)."""
    rows = []
    rhs = []
    for (x, y), (u, v) in zip(src, dst):
        rows.append([x, y, 1.0, 0.0, 0.0, 0.0, -u * x, -u * y])
        rows.append([0.0, 0.0, 0.0, x, y, 1.0, -v * x, -v * y])
        rhs += [u, v]
    a, b, c, d, e, f, g, h = np.linalg.solve(np.array(rows), np.array(rhs))
    return np.array([[a, b, c], [d, e, f], [g, h, 1.0]])




# ------------------------------------------------------------- the blending
def _blend_panel(acc: dict[str, np.ndarray], wsum: dict[str, np.ndarray],
                 placement: Placement, panel: PanelPlanes,
                 ring: Sequence[tuple[float, float]], frame: _Frame) -> None:
    """Lay one panel's planes onto the canvas accumulators: the planes
    resampled bilinearly through the homography from the canvas into the
    panel's own grid, weighted by the distance to the panel's edge."""
    grid_w = next(iter(panel.planes.values())).shape[1]
    reduce_by = max(1, int(frame.scale // (placement.fov_x / grid_w)))
    planes = {c: block_mean(p, reduce_by).astype(np.float32)
              for c, p in panel.planes.items()}
    h, w = next(iter(planes.values())).shape
    corners = _canvas_corners(ring, frame)
    h_map = _homography([corners[i] for i in _PIXEL_CORNERS],
                        [(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)])
    u0 = max(0, math.floor(min(c[0] for c in corners)))
    u1 = min(frame.width, math.ceil(max(c[0] for c in corners)) + 1)
    v0 = max(0, math.floor(min(c[1] for c in corners)))
    v1 = min(frame.height, math.ceil(max(c[1] for c in corners)) + 1)
    xs, ys = np.meshgrid(np.arange(u0, u1) + 0.5, np.arange(v0, v1) + 0.5)
    den = h_map[2, 0] * xs + h_map[2, 1] * ys + 1.0
    u = (h_map[0, 0] * xs + h_map[0, 1] * ys + h_map[0, 2]) / den
    v = (h_map[1, 0] * xs + h_map[1, 1] * ys + h_map[1, 2]) / den
    edge = np.minimum(np.minimum(u, w - u), np.minimum(v, h - v))
    ramp = max(1.0, FEATHER_FRACTION * min(w, h))
    weight = np.where(edge > 0.0, np.clip(edge / ramp, 0.0, 1.0),
                      0.0).astype(np.float32)
    x = np.clip(u - 0.5, 0.0, w - 1.0)
    y = np.clip(v - 0.5, 0.0, h - 1.0)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    x1, y1 = np.minimum(x0 + 1, w - 1), np.minimum(y0 + 1, h - 1)
    fx, fy = (x - x0).astype(np.float32), (y - y0).astype(np.float32)
    for channel, plane in planes.items():
        sample = ((1.0 - fy) * ((1.0 - fx) * plane[y0, x0] + fx * plane[y0, x1])
                  + fy * ((1.0 - fx) * plane[y1, x0] + fx * plane[y1, x1]))
        acc[channel][v0:v1, u0:u1] += weight * sample
        wsum[channel][v0:v1, u0:u1] += weight


def place_panels(placements: Sequence[Placement],
                 panels: Mapping[str, PanelPlanes],
                 size: int) -> Assembly | None:
    """Every panel of the table laid on one canvas, or None for an empty table.

    ``panels`` is the stacker's answer (``SessionStacker.panel_planes``), by
    panel key; a placement finds its panel by key, else by name. A placement
    with no panel there is a hole."""
    if not placements:
        return None
    rings = {p.key: framing.panel_footprint(p.ra_hours, p.dec_deg, p.pa_deg,
                                            p.fov_x, p.fov_y)
             for p in placements}
    held = {p.key: panels.get(p.key) or panels.get(p.name) for p in placements}
    finest = [p.fov_x / next(iter(held[p.key].planes.values())).shape[1]
              for p in placements if held[p.key] is not None]
    frame = _frame(rings, min(finest) if finest else None, size)
    shape = (frame.height, frame.width)
    channels = {c for panel in held.values() if panel is not None
                for c in panel.planes}
    acc = {c: np.zeros(shape, np.float32) for c in channels}
    wsum = {c: np.zeros(shape, np.float32) for c in channels}
    holes: list[Placement] = []
    outlines: dict[str, list[tuple[float, float]]] = {}
    rows: list[dict] = []
    for p in placements:
        panel = held[p.key]
        if panel is None:
            holes.append(p)
            corners = _canvas_corners(rings[p.key], frame)
            outlines[p.key] = [corners[i] for i in _OUTLINE]
        else:
            _blend_panel(acc, wsum, p, panel, rings[p.key], frame)
        rows.append({"key": p.key, "target": p.name,
                     "state": "hole" if panel is None else "stacked",
                     "frames": 0 if panel is None else panel.frames})
    cover = {c: wsum[c] > 0.0 for c in channels}
    planes = {c: np.divide(acc[c], wsum[c], out=np.zeros(shape, np.float32),
                           where=cover[c])
              for c in channels}
    return Assembly(planes=planes, cover=cover, holes=holes, outlines=outlines,
                    panels=rows, height=frame.height, width=frame.width)


# ---------------------------------------------------------------- the render
def _stretch(assembly: Assembly) -> dict[str, np.ndarray]:
    """One stretch over the whole canvas: the statistics are the covered
    pixels' (a hole or an unlit corner is not sky and must not set the black
    point), counted once where panels overlap."""
    shape = (assembly.height, assembly.width)
    covered = {c: m for c, m in assembly.cover.items() if m.any()}
    flat = stretch_channels({c: assembly.planes[c][m]
                             for c, m in covered.items()})
    out: dict[str, np.ndarray] = {}
    for channel, mask in covered.items():
        plane = np.zeros(shape, np.float32)
        plane[mask] = flat[channel]
        out[channel] = plane
    return out


def render(assembly: Assembly) -> tuple[bytes, dict]:
    """(JPEG bytes, meta) for an assembly: the stretched mosaic with every
    hole's footprint outlined. ``meta`` carries the size, the counts and one
    row per panel (key, target, ``stacked`` or ``hole``, frames)."""
    stretched = _stretch(assembly)
    rgb = (mix_channels(stretched, assembly.cover) if stretched
           else np.zeros((assembly.height, assembly.width, 3), np.float32))
    image = Image.fromarray((rgb * 255.0 + 0.5).astype(np.uint8), mode="RGB")
    draw = ImageDraw.Draw(image)
    for outline in assembly.outlines.values():
        draw.line(outline + [outline[0]], fill=OUTLINE_RGB,
                  width=OUTLINE_WIDTH, joint="curve")
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=False)
    meta = {"width": image.width, "height": image.height,
            "panels": assembly.panels,
            "stacked": len(assembly.panels) - len(assembly.holes),
            "holes": len(assembly.holes)}
    return buf.getvalue(), meta


def assemble(placements: Sequence[Placement],
             panels: Mapping[str, PanelPlanes],
             size: int) -> tuple[bytes, dict] | None:
    """The whole preview: ``place_panels`` then ``render``; None for an empty
    table."""
    assembly = place_panels(placements, panels, size)
    return None if assembly is None else render(assembly)
