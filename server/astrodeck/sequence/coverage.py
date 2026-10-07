# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Does each saved light cover the panel it was shot for? (#177, server half.)

THE GAP THIS CLOSES. A mosaic panel is "complete" when the ledger has counted
enough frames toward it. Nothing checked that those frames cover the tile: a
centring that degraded to a raw GoTo, a camera at the wrong angle, a solve at
the wrong plate scale all bank frames that count and cover something else, and
it was found at stitching. The per-frame plate solve already writes a WCS into
every saved light when ``solve_saved_lights`` is on (``Hub._solve_and_stamp``);
this module reads it back and lays it over the panel.

THE CHECK IS ADVISORY, AND SAYS ONLY WHAT IT CAN SEE (backlog ruling, WP-123):

* A light with no WCS is ``unstamped``: never solved, solve failed, file gone
  (NINA saved it on another host, or it was tidied away), a header that names a
  projection but no scale (astropy would read one degree per pixel and call the
  whole sky covered), or a footprint that has no area or is not a number. An
  unverified frame is not a covered one, so it has no overlap, and it is
  counted apart from the stamped ones ("12 banked, 7 solved").
* A flag is never an action. It changes no panel's completion, no order, no
  count: ``coverage_report`` reads a session and writes nothing, and the ledger
  is never an output of it (the session lock and the background stamping task
  make a write from here a race), so the answer is derived on demand.

THE GEOMETRY. The panel is the rectangle ``framing.panel_footprint`` lays out
(the corner math ``reframe_carry`` measures, one copy of it). The light is the
four corners ``WCS.calc_footprint`` gives from the header's NAXIS, taken at the
outer pixel edges. Both are projected into the PANEL's own tangent plane with
``framing.project``, clipped (``clip_convex``) and compared by area: the
fraction of the PANEL the light covers. A light bigger than the panel that
holds it whole is covered; a light solved at half the plan's scale covers a
quarter (the WCS is measured and the plan's field is the nominal one, #168,
which is the intended signal).

THE ANGLE. Which angle the panel is laid at, by the rule of the engine's own
hop check (``angle_check``): a rotate group's member commands its own
``Target.rotation_deg`` (per panel after #175), a fixed camera is planned at
the group's ``pa_deg``, and with no angle at all ("any", ``pa_deg`` None or
negative, as ``to_plan._angles`` writes it) the panel is laid at the light's
OWN measured angle, so a turn is nobody's error and only the offset counts.
When there IS a planned angle the light's angle is compared with it mod 180
(``angle_off_deg``): a rectangle turned half a turn covers the same sky, and a
standard solve (east to the left, ``cdelt1 < 0``) has its pixel x axis at
PA + 180, so the ordinary light at the planned angle reads 180 off it
unfolded. The overlap itself needs no fold: both outlines are the polygons.

THE THRESHOLD is ``on_tile_threshold``: a light is flagged when it covers
less of its panel than ``1 - overlap / 2``, the pointing share of the
overlap budget (Appendix A.2 reserves half the overlap for pointing error and
the other half for convergence and the camera's angle). A lone panel has no
neighbour to absorb an edge and is held to ``SINGLE_PANEL_ON_TILE``.

WHAT IT COSTS. A night is thousands of lights and the modal polls, so a header
is read once per (path, mtime, size) (``frame_footprint``), header only
(``astropy.io.fits.getheader``: no pixel is read), and a file with no WCS is
remembered as having none until it changes (a stamp rewrites the header in
place, which moves its mtime). Each answer is cheap after that: four corners,
one clip.

WHAT LEAVES. Fractions, counts, an offset in arcminutes and an angle in
degrees: nothing derived from the site, and no RA or Dec of a frame (spec 6.9;
a key-name filter cannot withhold a value a route computes and names itself,
#19). The footprints stay in this module's memo and in local variables. The
route holds the keys to an allow-list at the wire.

THE UI MAP is a later package; this is the verifier and the route's answer.
"""
from __future__ import annotations

import math
import os
import threading
import warnings
from dataclasses import dataclass

from astropy.io import fits
from astropy.wcs import WCS

from ..catalog import framing
from ..catalog.coords import angular_sep_deg
from .models import SequencePlan, Target, TargetGroup
from .session import Session, SessionFrame

#: The share of a block's overlap held for POINTING error (Appendix A.2:
#: "half the overlap is reserved for pointing error"; convergence and the
#: camera's angle spend the other half, ``framing.ROTATION_BUDGET``). A panel
#: may be mis-pointed by this share of the overlap, as a fraction of its field,
#: before the neighbour's seam is spent, so a light covering less than
#: ``1 - POINTING_SHARE * overlap`` of its panel is flagged.
POINTING_SHARE = 0.5

#: What a LONE panel must still cover. With no neighbour there is no overlap to
#: reserve a share of, and the block's overlap setting is meaningless for it
#: (an overlap of 0 would demand 1.0, which no real solve meets), so it is held
#: to a flat 90 percent.
SINGLE_PANEL_ON_TILE = 0.9

#: Slack on the comparison: a light exactly on its tile measures 1 - 1e-12 and
#: a zero-overlap block's threshold is exactly 1.0, so without it the ideal
#: frame would be flagged on rounding.
ON_TILE_EPS = 1e-6

#: A frame corner this far from the panel's centre (cosine of the angle
#: between them, 60 degrees) cannot be laid on the panel's tangent plane
#: usefully: a camera field is degrees across, so a corner that far away is a
#: frame shot on the other side of the sky or a footprint of absurd scale.
#: Either way it is nowhere near the tile: overlap 0, flagged, and the
#: separation is measured on the sphere rather than through a projection that
#: blows up at 90 degrees.
FAR_COS = 0.5

#: The memo's size bound. A night is a few thousand lights and a campaign
#: several nights; at about 300 bytes an entry this is a few MB, and when it
#: fills the OLDEST tenth is dropped (insertion order), never the whole memo.
MEMO_MAX = 20000

#: The sentence for a mosaic that runs with ``solve_saved_lights`` off.
#: Stamping is not turned on silently: it costs an ASTAP run per frame, which
#: on the Pi is the operator's call.
STAMPING_OFF_NOTE = ("coverage cannot be checked: frames are not being "
                     "plate-solved")

_CD_KEYS = ("CD1_1", "CD1_2", "CD2_1", "CD2_2")


# --------------------------------------------------------------- polygons

def polygon_area(points: list[tuple[float, float]]) -> float:
    """The signed area of a polygon (shoelace): positive when the points run
    counter-clockwise, negative when clockwise, 0 for fewer than three."""
    n = len(points)
    if n < 3:
        return 0.0
    total = 0.0
    for i in range(n):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % n]
        total += x0 * y1 - x1 * y0
    return total / 2.0


def _counter_clockwise(points: list[tuple[float, float]]
                       ) -> list[tuple[float, float]]:
    return list(points) if polygon_area(points) >= 0.0 else list(points)[::-1]


def clip_convex(subject: list[tuple[float, float]],
                clip: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """``subject`` clipped to the convex polygon ``clip`` (Sutherland-
    Hodgman): the outline of the part they share, ``[]`` when they share
    none. Either may run either way round: the clip is turned
    counter-clockwise first, because that is what makes "left of an edge"
    mean "inside it", and the subject's winding never enters (it is only
    ever cut, point by point, and keeps its own)."""
    clip = _counter_clockwise(clip)
    out = list(subject)
    for i in range(len(clip)):
        if not out:
            break
        ax, ay = clip[i]
        bx, by = clip[(i + 1) % len(clip)]

        def side(p: tuple[float, float]) -> float:
            # Positive left of a -> b: the inside of a counter-clockwise clip.
            return (bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax)

        src, out = out, []
        prev = src[-1]
        d_prev = side(prev)
        for cur in src:
            d_cur = side(cur)
            if d_cur >= 0.0:
                if d_prev < 0.0:
                    out.append(_meet(prev, cur, d_prev, d_cur))
                out.append(cur)
            elif d_prev >= 0.0:
                out.append(_meet(prev, cur, d_prev, d_cur))
            prev, d_prev = cur, d_cur
    return out


def _meet(p: tuple[float, float], q: tuple[float, float], d_p: float,
          d_q: float) -> tuple[float, float]:
    """Where the segment ``p -> q`` crosses the clip edge, from the two
    signed distances to it (one of each sign, so the divisor is not 0)."""
    t = d_p / (d_p - d_q)
    return p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])


def angle_off_deg(measured_deg: float, planned_deg: float) -> float:
    """How far the light's angle is from the plan's, in [0, 90]: the distance
    MOD 180, because a rectangle turned half a turn is the same footprint
    (the framing equivalence ``angle_check`` and
    ``rotation.angle_equals_mod180`` hold)."""
    return abs((measured_deg - planned_deg + 90.0) % 180.0 - 90.0)


# ---------------------------------------------------------- the light's WCS

@dataclass(frozen=True)
class FrameFootprint:
    """A solved light's outline: the four corners of its pixel grid on the
    sky, ``(ra_hours, dec_deg)`` each, in the ring order ``calc_footprint``
    gives: ``(0.5, 0.5)``, ``(0.5, NY + 0.5)``, ``(NX + 0.5, NY + 0.5)``,
    ``(NX + 0.5, 0.5)``, so corner 0 to corner 3 runs along pixel x.
    Held in the memo and never returned by a route."""
    corners: tuple[tuple[float, float], ...]


_MISSING = object()
_MEMO: dict[tuple[str, int, int], FrameFootprint | None] = {}
_MEMO_LOCK = threading.Lock()
#: Held across a header read, so concurrent misses for one file read it once.
_READ_LOCK = threading.Lock()


def clear_memo() -> None:
    """Forget every header read (a test's reset)."""
    with _MEMO_LOCK:
        _MEMO.clear()


def memo_size() -> int:
    with _MEMO_LOCK:
        return len(_MEMO)


def _has_scale(header) -> bool:
    """Whether the header carries a plate scale of its own, and a real one.

    A WCS with a projection and a reference point but no scale is read by
    astropy as one degree per pixel, a footprint that covers every panel on
    the sky; and an all-zero CD matrix is read the same way (wcslib falls
    back to a CDELT of 1), so the keys are read here and the scale judged
    from them: the determinant of the CD matrix, or the product of the CDELTs
    (as ``fitsio.write_wcs`` writes either), finite and not zero."""
    try:
        if any(key in header for key in _CD_KEYS):
            a, b, c, d = (float(header.get(key, 0.0)) for key in _CD_KEYS)
            det = a * d - b * c
        elif "CDELT1" in header:
            det = float(header["CDELT1"]) * float(header.get("CDELT2", 1.0))
        else:
            return False
    except (TypeError, ValueError):
        return False
    return math.isfinite(det) and det != 0.0


def _read_footprint(path: str) -> FrameFootprint | None:
    """The outline of the light at ``path`` from its header, or ``None`` for
    anything that is not a usable plate solution. Never raises: a header this
    cannot read is a frame it cannot verify, and the report is a summary that
    must not 500 because one file is damaged."""
    try:
        with warnings.catch_warnings():
            # astropy warns about a header's non-standard cards and, for a
            # file whose data section is short, about truncation. Neither is
            # this module's to say, and the report must not spam a log once
            # per frame per poll.
            warnings.simplefilter("ignore")
            header = fits.getheader(path)
            if not _has_scale(header):
                return None
            nx, ny = int(header["NAXIS1"]), int(header["NAXIS2"])
            if nx <= 0 or ny <= 0:
                return None
            wcs = WCS(header)
            if not wcs.has_celestial:
                return None
            world = wcs.calc_footprint(axes=(nx, ny), center=False)
        corners = tuple(
            (float(row[wcs.wcs.lng]) / 15.0, float(row[wcs.wcs.lat]))
            for row in world)
    except Exception:           # noqa: BLE001 - unreadable is "cannot verify"
        return None
    if len(corners) != 4 or not all(
            math.isfinite(a) and math.isfinite(b) for a, b in corners):
        return None
    return FrameFootprint(corners=corners)


def frame_footprint(path: str) -> FrameFootprint | None:
    """The outline of the saved light at ``path``, memoised per (path, mtime,
    size), or ``None`` when it has no usable WCS (``_read_footprint``). A path
    that is blank or no longer on disk is ``None`` and is not memoised: the
    stat is the whole cost, and a file that appears later must be read.

    The size is in the key beside the mtime so a stamp that lands within one
    timestamp tick of an earlier read still reads again; a stamp rewrites the
    header in place (``fitsio.write_wcs``) and moves one or both.

    A COLD MEMO IS READ ONCE, NOT ONCE PER POLLER. The first report of a
    night's few thousand lights is seconds of header reads (longer on the
    Pi), and the modal polls on a timer, so a second poll arrives while the
    first is still reading. Misses are taken under one lock and looked up
    again inside it, so the second poll finds what the first has read instead
    of reading it a second time."""
    if not path:
        return None
    try:
        st = os.stat(path)
    except (OSError, ValueError):
        # ValueError: ``os.stat`` raises it, not an OSError, for a path with
        # an embedded NUL. A ledger row is data from a file on disk, and the
        # report must not 500 on one damaged row.
        return None
    key = (path, st.st_mtime_ns, st.st_size)
    with _MEMO_LOCK:
        hit = _MEMO.get(key, _MISSING)
    if hit is not _MISSING:
        return hit                                  # type: ignore[return-value]
    with _READ_LOCK:
        with _MEMO_LOCK:
            hit = _MEMO.get(key, _MISSING)
        if hit is not _MISSING:
            return hit                              # type: ignore[return-value]
        footprint = _read_footprint(path)
        with _MEMO_LOCK:
            if len(_MEMO) >= MEMO_MAX:
                for stale in list(_MEMO)[:max(1, MEMO_MAX // 10)]:
                    del _MEMO[stale]
            _MEMO[key] = footprint
    return footprint


# ---------------------------------------------------------------- the check

@dataclass(frozen=True)
class FrameCheck:
    """One light measured against its panel."""
    #: The fraction of the PANEL the light covers, in [0, 1].
    overlap_frac: float
    #: How far the light's centre is from the panel's, arcminutes.
    offset_arcmin: float
    #: The light's angle from the plan's, degrees in [0, 90] (mod 180);
    #: ``None`` when the plan has no angle (the panel is laid at the light's
    #: own) or the light is nowhere near the tile.
    angle_off_deg: float | None


def _unit(ra_hours: float, dec_deg: float) -> tuple[float, float, float]:
    ra, dec = math.radians(ra_hours * 15.0), math.radians(dec_deg)
    return (math.cos(dec) * math.cos(ra), math.cos(dec) * math.sin(ra),
            math.sin(dec))


def _off_the_tile(footprint: FrameFootprint, ra_hours: float,
                  dec_deg: float) -> FrameCheck:
    """A light nowhere near the panel: nothing of it on the tile, and its
    centre (the mean of its corners' directions) measured on the sphere."""
    vectors = [_unit(ra, dec) for ra, dec in footprint.corners]
    x, y, z = (sum(v[i] for v in vectors) for i in range(3))
    norm = math.sqrt(x * x + y * y + z * z)
    if norm < 1e-9:
        separation = 180.0
    else:
        separation = angular_sep_deg(
            ra_hours, dec_deg,
            math.degrees(math.atan2(y, x)) / 15.0,
            math.degrees(math.asin(max(-1.0, min(1.0, z / norm)))))
    return FrameCheck(overlap_frac=0.0, offset_arcmin=separation * 60.0,
                      angle_off_deg=None)


def check_frame(footprint: FrameFootprint, *, ra_hours: float, dec_deg: float,
                fov_x: float, fov_y: float,
                planned_pa: float | None) -> FrameCheck | None:
    """Lay a solved light over the panel centred at ``(ra_hours, dec_deg)``
    with field ``fov_x`` x ``fov_y`` degrees, and measure it. ``planned_pa``
    is the angle the panel is laid at, or ``None`` to lay it at the light's
    own. ``None`` back when the light cannot be measured: a corner that is not
    a number, or an outline with no area."""
    if not all(math.isfinite(a) and math.isfinite(b)
               for a, b in footprint.corners):
        return None
    centre = _unit(ra_hours, dec_deg)
    for ra, dec in footprint.corners:
        if sum(a * b for a, b in zip(centre, _unit(ra, dec))) < FAR_COS:
            return _off_the_tile(footprint, ra_hours, dec_deg)
    light = [framing.project(ra, dec, ra_hours, dec_deg)
             for ra, dec in footprint.corners]
    if not all(math.isfinite(v) for point in light for v in point):
        return None
    if polygon_area(light) == 0.0:
        return None
    # The light's own angle: where its pixel x axis points in the panel's
    # plane (corner 0 to corner 3), which is the angle ``compute_mosaic``
    # turns a panel's x axis to. East-left solves read PA + 180 here.
    measured = math.degrees(math.atan2(light[3][1] - light[0][1],
                                       light[3][0] - light[0][0]))
    pa = measured if planned_pa is None else planned_pa
    ring = framing.panel_footprint(ra_hours, dec_deg, pa, fov_x, fov_y)
    # ``panel_footprint`` is a Z (``_CORNERS``); the outline is 0, 1, 3, 2.
    panel = [framing.project(ring[i][0], ring[i][1], ra_hours, dec_deg)
             for i in (0, 1, 3, 2)]
    panel_area = abs(polygon_area(panel))
    if not panel_area > 0.0:
        return None
    shared = abs(polygon_area(clip_convex(light, panel)))
    cx = sum(p[0] for p in light) / 4.0
    cy = sum(p[1] for p in light) / 4.0
    return FrameCheck(
        overlap_frac=max(0.0, min(1.0, shared / panel_area)),
        offset_arcmin=math.hypot(cx, cy) * 60.0,
        angle_off_deg=(None if planned_pa is None
                       else angle_off_deg(measured, planned_pa)))


def on_tile_threshold(overlap: float, *, single: bool) -> float:
    """The fraction of its panel a light must cover not to be flagged: ``1 -
    POINTING_SHARE * overlap`` for a panel with neighbours (``overlap`` a
    fraction, clamped to [0, 0.5] as ``compute_mosaic`` clamps it), and
    ``SINGLE_PANEL_ON_TILE`` for a lone panel."""
    if single:
        return SINGLE_PANEL_ON_TILE
    return 1.0 - POINTING_SHARE * min(0.5, max(0.0, float(overlap)))


# ------------------------------------------------------------- the report

def _finite(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _planned_angle(group: TargetGroup, member: Target) -> float | None:
    """The angle a panel was planned at, or ``None`` for "any".

    A rotate group commands each member's own ``rotation_deg``; a fixed
    camera holds the group's ``pa_deg`` (``to_plan._angles``: a fixed block's
    members carry no angle of their own). A rotate member with no angle of
    its own falls back to the group's. A negative angle is "no constraint"
    everywhere it is read (#150), never a position angle."""
    candidates = ((member.rotation_deg, group.pa_deg) if group.rotate
                  else (group.pa_deg,))
    for value in candidates:
        angle = _finite(value)
        if angle is not None and angle >= 0.0:
            return angle
    return None


def _grid_is_single(group: TargetGroup, members: list[Target]) -> bool:
    """A lone panel: the block's grid is 1 x 1. The GRID, not how many
    panels are left (a 3 x 3 block with eight skipped has neighbours' overlap
    to spend); the members and skips stand in when the geometry holds no
    grid."""
    geometry = group.geometry or {}
    rows, cols = _finite(geometry.get("rows")), _finite(geometry.get("cols"))
    if rows is not None and cols is not None:
        return round(rows) * round(cols) <= 1
    return len(members) + len(group.skipped_ids) <= 1


def _field(group: TargetGroup) -> tuple[float, float] | None:
    geometry = group.geometry or {}
    fov_x, fov_y = _finite(geometry.get("fov_x")), _finite(geometry.get("fov_y"))
    if fov_x is None or fov_y is None or fov_x <= 0.0 or fov_y <= 0.0:
        return None
    return fov_x, fov_y


def _panel_row(member: Target, group: TargetGroup,
               field: tuple[float, float] | None, threshold: float,
               frames: list[SessionFrame]) -> dict:
    planned = _planned_angle(group, member)
    stamped = flagged = 0
    worst_overlap: float | None = None
    worst_offset: float | None = None
    worst_angle: float | None = None
    for frame in frames:
        footprint = frame_footprint(frame.path)
        check = (None if footprint is None or field is None else
                 check_frame(footprint, ra_hours=member.ra_hours,
                             dec_deg=member.dec_deg, fov_x=field[0],
                             fov_y=field[1], planned_pa=planned))
        if check is None:
            continue                # unverified: never counted as covered
        stamped += 1
        if check.overlap_frac < threshold - ON_TILE_EPS:
            flagged += 1
        worst_overlap = (check.overlap_frac if worst_overlap is None
                         else min(worst_overlap, check.overlap_frac))
        worst_offset = (check.offset_arcmin if worst_offset is None
                        else max(worst_offset, check.offset_arcmin))
        if check.angle_off_deg is not None:
            worst_angle = (check.angle_off_deg if worst_angle is None
                           else max(worst_angle, check.angle_off_deg))
    return {
        "target_id": member.id, "name": member.name,
        "row": member.panel_row, "col": member.panel_col,
        "frames": len(frames), "stamped": stamped,
        "unstamped": len(frames) - stamped, "flagged": flagged,
        "worst_overlap_frac": (None if worst_overlap is None
                               else round(worst_overlap, 4)),
        "worst_offset_arcmin": (None if worst_offset is None
                                else round(worst_offset, 2)),
        "worst_angle_off_deg": (None if worst_angle is None
                                else round(worst_angle, 2)),
    }


def coverage_report(session: Session) -> dict:
    """Per mosaic group and per panel, how much of the banked lights covers
    the tile: ``{"groups": [{group_id, name, threshold, panels: [{target_id,
    name, row, col, frames, stamped, unstamped, flagged, worst_overlap_frac,
    worst_offset_arcmin, worst_angle_off_deg}]}]}``.

    ``frames`` is the effective (``SessionFrame.effective()``, the rule the
    ledger counts by) Light frames of the panel. ``stamped`` is how many of
    them could be laid on the tile, ``unstamped`` the rest (see the module
    docstring: unverified, never covered), ``flagged`` how many of the
    stamped cover less than ``threshold`` of it. The ``worst_*`` are None
    where nothing was stamped (and the angle where the plan has none). Every
    member panel is listed, with zeros where it has no frames (set aside,
    deferred, not reached), so a map can draw the grid; a skipped panel is
    not a member. A plan with no groups answers ``{"groups": []}``.

    Pure and read-only: header reads and arithmetic, and no write to the
    session or the ledger."""
    plan = session.plan
    steps = {step.id: step for target in plan.targets for step in target.steps}
    by_target: dict[str, list[SessionFrame]] = {}
    for frame in session.frames:
        if not frame.effective():
            continue
        step = steps.get(frame.step_id)
        # A dark or flat has no stars to solve and no tile to cover. A frame
        # whose step is gone from the plan keeps the benefit of the doubt: it
        # was shot for its panel, and a dormant session's plan is editable.
        if step is not None and step.frame_type != "Light":
            continue
        by_target.setdefault(frame.target_id, []).append(frame)
    groups = []
    for group in plan.groups:
        members = [t for t in plan.targets
                   if t.mosaic_group == group.id and not t.calibration]
        field = _field(group)
        overlap = _finite((group.geometry or {}).get("overlap")) or 0.0
        threshold = on_tile_threshold(
            overlap, single=_grid_is_single(group, members))
        groups.append({
            "group_id": group.id, "name": group.name,
            "threshold": round(threshold, 4),
            "panels": [_panel_row(m, group, field, threshold,
                                  by_target.get(m.id, []))
                       for m in members]})
    return {"groups": groups}


def stamping_note(plan: SequencePlan,
                  solve_saved_lights: bool) -> str | None:
    """The note for a mosaic that runs with solving off, or ``None``.

    ``solve_saved_lights`` is ``AppConfig``'s master switch for stamping a
    WCS into each saved light. Off, no light carries one this run and the
    check has nothing to read; the sentence says so instead of letting an
    empty report read as a covered one. This module never turns stamping on
    (it costs an ASTAP run per frame, and on the Pi that is the operator's
    call). A plan with no mosaic has nothing to check and no note."""
    if solve_saved_lights:
        return None
    for group in plan.groups:
        if any(t.mosaic_group == group.id and not t.calibration
               for t in plan.targets):
            return STAMPING_OFF_NOTE
    return None
