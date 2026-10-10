# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Known corruptions of a correct result directory, for proving the scorer fails.

Spec section 10: before trusting a green report, run known corruptions against
otherwise correct output and check that each one produces the expected
affected metrics with a quantitative response. These are the corruptions. Each
one takes a result directory, writes a new one, and changes exactly the thing
it names; every other file is copied through byte for byte.

Three rules the whole module keeps.

- A corruption is a defect with a known size, not a random mess. ``yaw`` puts
  in a whole number of raster columns so the azimuth it adds is exact, and
  ``focal`` remaps altitudes through a closed-form warp. If the corruption's
  own size were approximate, the scorer's answer could not be checked against
  it.
- A corruption never touches the truth. It reads ``truth/`` only where the
  corruption is defined in terms of the scene (``wrong-reference`` renders the
  panorama from the wrong point), and it writes only into the output result
  directory.
- The copy does not carry ``scores.json`` or ``report.html`` over. Those
  describe the result that was corrupted, and a stale score sitting in a
  corrupted directory is exactly the kind of evidence that gets believed.

What the corrupted basis vectors mean is worth stating, because two of these
deliberately produce a basis no real device could export: ``mirror`` reflects
the basis, which is improper, and ``focal`` warps ``forward`` alone, which
leaves it no longer perpendicular to ``right`` and ``up``. These are faults,
not device models. Issue #59: the scorer reads ``right`` and ``up`` as well
as ``forward`` now, so ``focal``'s untouched ``right``/``up`` still read as
zero per-axis error while the whole warp lands on ``forward``, and
``mirror``'s reflected ``right``/``up`` carry their own large error beside
it. ``roll`` is the odd one out: it rotates ``right`` and ``up`` about their
own ``forward`` and leaves that forward untouched, which IS a basis a real
device can hold -- it is the fault the fix exists to catch, not another
impossible one.

Version 2 results (CONTRACT.md "Version 2") carry a 720-bin profile, a
polyline and diagnostics beside the legacy files. Every corruption that
touches the boundary, the poses or the north has a version 2 form, and the
eleven corruptions written for version 2 refuse a result that does not carry
the file they act on: a corruption that did not happen would be scored as a
clean result and read as a scorer that cannot fail.
"""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import numpy as np
from PIL import Image

from . import truth as truth_module
from .geometry import sky_angles, sky_vector
from .palette import PALETTE
from .scene import load as load_scene
# The decoder's own colour tolerance: erasing less than the decoder can see
# would be an erasure that leaves the landmarks findable. And the raster
# mapping the scorer reads a result by: a corruption of a known size has to be
# expressed in the same mapping the size will be measured in. The profile
# bins, their states and the pose maths come from the scorer for the same
# reason: a corruption of a known size is read back in the scorer's own units.
from .score import (COLOUR_TOLERANCE, PANORAMA_ALT_SPAN, PANORAMA_ALT_TOP,
                    PANORAMA_HEIGHT, PANORAMA_WIDTH, PROFILE_BIN_DEG,
                    PROFILE_BINS, STATE_LOW, STATE_MEASURED, STATE_UNKNOWN,
                    matrix_to_quat, quat_to_matrix, rotate_about_up)

__all__ = ["CORRUPTIONS", "apply"]

#: ``uncertain_bins`` of a version 2 horizon lists these states (spec 3.4).
UNCERTAIN_STATES = (1, 2, 3)

#: The grey that ``erase-landmarks`` paints over a landmark. No palette colour
#: is within the decoder's tolerance of it, and it sits inside the background
#: noise range the chart yard's texture uses.
ERASED_GREY = (110, 110, 110)


# --------------------------------------------------------------------------
# Reading and writing the pieces of a result directory
# --------------------------------------------------------------------------


def _read_json(path: Path):
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, separators=(",", ":")),
                    encoding="utf-8", newline="\n")


def _read_jsonl(path: Path) -> list:
    if not path.is_file():
        return []
    return [json.loads(line) for line
            in path.read_text(encoding="utf-8").splitlines() if line]


def _write_jsonl(path: Path, records: list) -> None:
    lines = "\n".join(json.dumps(record, separators=(",", ":")) for record in records)
    path.write_text(lines + "\n" if records else "", encoding="utf-8", newline="\n")


def _read_panorama(out_dir: Path):
    path = out_dir / "panorama.png"
    if not path.is_file():
        return None
    with Image.open(path) as image:
        return np.array(image.convert("RGBA"))


def _write_panorama(out_dir: Path, image: np.ndarray) -> None:
    Image.fromarray(np.ascontiguousarray(image), mode="RGBA").save(
        out_dir / "panorama.png")


def _column_azimuths(width: int) -> np.ndarray:
    return (np.arange(width) + 0.5) / width * 360.0


def _row_altitudes(height: int) -> np.ndarray:
    return PANORAMA_ALT_TOP - np.arange(height) / max(height - 1, 1) * PANORAMA_ALT_SPAN


def _horizon_altitudes(horizon: dict) -> np.ndarray:
    return np.array([float(p["alt"]) for p in horizon["points"]], dtype=np.float64)


def _set_horizon_altitudes(horizon: dict, values: np.ndarray) -> None:
    for point, value in zip(horizon["points"], values):
        point["alt"] = float(value)


def _edit_horizon(out_dir: Path, edit, edit_v2=None) -> None:
    """Apply ``edit(horizon)`` to ``horizon.json`` if there is one to edit.

    A version 2 horizon is edited by ``edit_v2`` instead: its points are a
    polyline addressed by azimuth and its bins live in the profile arrays, so
    the version 1 edit (a point per bin) would corrupt something else. A
    corruption with no version 2 form refuses a version 2 horizon.
    """
    path = out_dir / "horizon.json"
    horizon = _read_json(path)
    if horizon and horizon.get("version") == 2:
        if edit_v2 is None:
            raise ValueError("this corruption has no version 2 form")
        edit_v2(horizon)
        _write_json(path, horizon)
        return
    if not horizon or not horizon.get("points"):
        return
    edit(horizon)
    _write_json(path, horizon)


def _require_horizon_v2(out_dir: Path, name: str) -> dict:
    """The version 2 horizon of ``out_dir``, or a refusal naming ``name``."""
    horizon = _read_json(out_dir / "horizon.json")
    if not horizon or horizon.get("version") != 2:
        raise ValueError(f"{name} acts on a version 2 horizon.json, and this "
                         "result does not carry one")
    return horizon


def _v2_arrays(horizon: dict):
    """``(profile, traced, state)`` of a version 2 horizon as numpy arrays.

    ``traced`` has NaN where the file has ``null``; ``state`` is integer.
    """
    profile = np.array(horizon["profile"], dtype=np.float64)
    traced = np.array([math.nan if v is None else float(v)
                       for v in horizon["profile_traced"]], dtype=np.float64)
    state = np.array(horizon["profile_state"], dtype=np.int64)
    return profile, traced, state


def _v2_store(horizon: dict, profile, traced, state) -> None:
    """Write the three arrays back, and re-derive ``uncertain_bins`` from them."""
    horizon["profile"] = [float(v) for v in profile]
    horizon["profile_traced"] = [None if math.isnan(v) else float(v) for v in traced]
    horizon["profile_state"] = [int(v) for v in state]
    horizon["uncertain_bins"] = [int(i) for i in np.nonzero(np.isin(state, UNCERTAIN_STATES))[0]]


def _sorted_points(points) -> list:
    return sorted(({"az": float(p["az"]) % 360.0, "alt": float(p["alt"])} for p in points),
                  key=lambda p: p["az"])


def _lift_polyline(points, profile, changed) -> list:
    """Raise a polyline until it is again at or above ``profile`` (G1).

    ``changed`` marks the bins whose profile the caller has just changed. A
    polyline with few vertices has long straight pieces, and raising one
    vertex of such a piece tilts the whole of it, so first every bin edge from
    one bin before the changed bins to one bin after them gets a vertex at the
    line's own height there. Then every vertex is lifted to the bin it sits
    in, and every bin edge to the higher of the two bins that share it. The
    damage is the changed bins and one shoulder bin either side, whatever the
    polyline looked like.

    Only ever raises, so what was above the profile stays above it, and a
    result that has had its bins relabelled blocked keeps a polyline that
    blocks them. The corruptions that change the profile call this so that the
    only gate they break on the polyline's side is the one they are about.
    """
    lifted = _sorted_points(points)
    if not lifted:
        return lifted
    edges = np.arange(PROFILE_BINS) * PROFILE_BIN_DEG
    original = np.interp(edges, [p["az"] for p in lifted], [p["alt"] for p in lifted],
                         period=360.0)
    near = set()
    for index in np.nonzero(changed)[0]:
        near.update(int((index + offset) % PROFILE_BINS) for offset in (-1, 0, 1, 2))
    for k in sorted(near):
        if not any(abs(p["az"] - edges[k]) < 1e-9 for p in lifted):
            lifted.append({"az": float(edges[k]), "alt": float(original[k])})
    lifted.sort(key=lambda p: p["az"])
    for point in lifted:
        bin_index = min(int(point["az"] / PROFILE_BIN_DEG), PROFILE_BINS - 1)
        point["alt"] = max(point["alt"], float(profile[bin_index]))
    needed = np.maximum(np.roll(profile, 1), profile)
    for k in range(PROFILE_BINS):
        at = next((p for p in lifted if abs(p["az"] - edges[k]) < 1e-9), None)
        if at is not None:
            at["alt"] = max(at["alt"], float(needed[k]))
    return lifted


def _edit_diagnostics(out_dir: Path, edit, required: str | None = None) -> None:
    """Apply ``edit(diagnostics)`` to ``diagnostics.json``.

    Absent is a no-op for the legacy corruptions, which have always acted on
    results that carry none. ``required`` names a version 2 corruption that
    cannot act without it, and refuses.
    """
    path = out_dir / "diagnostics.json"
    diagnostics = _read_json(path)
    if diagnostics is None:
        if required is not None:
            raise ValueError(f"{required} acts on diagnostics.json, and this "
                             "result does not carry one")
        return
    edit(diagnostics)
    _write_json(path, diagnostics)


def _turn_pose(q, deg: float) -> list:
    """A camera-to-world quaternion turned east by ``deg`` about the vertical.

    Left-multiplied, so it is the world that turns: the heading of the optical
    axis moves by exactly ``deg`` and the relative rotation between two poses
    turned together is unchanged.
    """
    turn = np.array([[math.cos(math.radians(deg)), math.sin(math.radians(deg)), 0.0],
                     [-math.sin(math.radians(deg)), math.cos(math.radians(deg)), 0.0],
                     [0.0, 0.0, 1.0]])
    return matrix_to_quat(turn @ quat_to_matrix(q))


def _edit_events(out_dir: Path, edit) -> None:
    """Apply ``edit(events)`` to the parsed ``events.jsonl`` and write it back."""
    path = out_dir / "events.jsonl"
    events = _read_jsonl(path)
    if not events:
        return
    edit(events)
    _write_jsonl(path, events)


def _basis_vectors(event: dict):
    basis = event.get("basis")
    if not basis:
        return None
    return basis


# --------------------------------------------------------------------------
# The corruptions
# --------------------------------------------------------------------------


def _yaw(case_dir: Path, out_dir: Path, deg: float = 1.0) -> None:
    """Turn the whole result east by ``deg``: raster, boundary and overlay.

    The raster is rolled by a whole number of columns so the azimuth put in is
    exact, and the boundary is rolled by the matching whole number of bins.

    A version 2 result turns the same way: the profile arrays roll by whole
    bins, the polyline's vertices move by the same whole number of half
    degrees, and every keyframe pose in ``diagnostics.json`` turns by ``deg``
    exactly. The poses turn because a result with the wrong north has wrong
    headings in every file that carries one, and a yaw that left the
    diagnostics alone would be a yaw the north gates could not see.
    """
    deg = float(deg)
    image = _read_panorama(out_dir)
    if image is not None:
        width = image.shape[1]
        _write_panorama(out_dir, np.roll(image, int(round(deg / 360.0 * width)), axis=1))

    def edit(horizon):
        altitudes = _horizon_altitudes(horizon)
        bins = altitudes.size
        shift = int(round(deg / (360.0 / bins)))
        _set_horizon_altitudes(horizon, np.roll(altitudes, shift))
        horizon["uncertain_bins"] = sorted(
            (int(index) + shift) % bins for index in horizon.get("uncertain_bins") or [])

    def edit_v2(horizon):
        profile, traced, state = _v2_arrays(horizon)
        shift = int(round(deg / PROFILE_BIN_DEG))
        _v2_store(horizon, np.roll(profile, shift), np.roll(traced, shift),
                  np.roll(state, shift))
        horizon["points"] = _sorted_points(
            {"az": point["az"] + shift * PROFILE_BIN_DEG, "alt": point["alt"]}
            for point in horizon["points"])

    _edit_horizon(out_dir, edit, edit_v2)

    def turn(events):
        for event in events:
            basis = _basis_vectors(event)
            if basis is None:
                continue
            for key in ("right", "up", "forward"):
                basis[key] = rotate_about_up(basis[key], deg)

    _edit_events(out_dir, turn)

    def turn_poses(diagnostics):
        for keyframe in diagnostics.get("keyframes") or []:
            keyframe["q"] = _turn_pose(keyframe["q"], deg)

    _edit_diagnostics(out_dir, turn_poses)


def _rotate_about_axis(vector, axis, deg: float) -> list:
    """Rotate ``vector`` by ``deg`` degrees about the unit ``axis``.

    Rodrigues' formula, ``v cos(d) + (axis x v) sin(d) + axis (axis . v) (1 -
    cos(d))``. ``axis`` is renormalised so the caller may pass a basis vector
    straight through even if it carries rounding error.
    """
    radians = math.radians(deg)
    cos, sin = math.cos(radians), math.sin(radians)
    v = np.asarray([float(x) for x in vector], dtype=np.float64)
    a = np.asarray([float(x) for x in axis], dtype=np.float64)
    a = a / np.linalg.norm(a)
    rotated = v * cos + np.cross(a, v) * sin + a * float(np.dot(a, v)) * (1.0 - cos)
    return [float(x) for x in rotated]


def _roll(case_dir: Path, out_dir: Path, deg: float = 3.0) -> None:
    """Turn every event's ``right`` and ``up`` about its OWN ``forward`` by ``deg``.

    Issue #59: the overlay metric used to read `forward` alone, so a roll --
    `right` and `up` rotated about the aim while `forward` itself holds still
    -- was invisible to it end to end. This is the corruption that proves the
    fix: `forward` is untouched here on purpose, and only a metric that also
    reads `right` and `up` can see anything moved.

    Only the overlay carries roll. The raster and the boundary are built from
    `forward` alone (CONTRACT.md's raster mapping has no notion of the image's
    own up), so this corruption never touches `panorama.png` or
    `horizon.json`, unlike `yaw`, `focal` and `mirror`.

    Rotating `right` and `up` by the same angle about `forward` keeps the
    basis orthonormal: `forward` is untouched, ``right`` and ``up`` are each
    rotated in the plane perpendicular to it (they are already perpendicular
    to `forward` in a real basis), so lengths and the three pairwise dot
    products are preserved to floating-point precision.
    """
    deg = float(deg)

    def turn(events):
        for event in events:
            basis = _basis_vectors(event)
            if basis is None:
                continue
            axis = basis["forward"]
            for key in ("right", "up"):
                basis[key] = _rotate_about_axis(basis[key], axis, deg)

    _edit_events(out_dir, turn)


def _north_wrap(case_dir: Path, out_dir: Path) -> None:
    """A yaw of 350 degrees: 10 degrees west, and never 350 degrees of error.

    A scorer that subtracted azimuths without wrapping would report 350. The
    shift is past the landmark matching radius, so the honest answer is a
    north offset of -10 plus the omissions it caused.
    """
    _yaw(case_dir, out_dir, deg=350.0)


def _warp_altitude(alt_deg, scale: float):
    """``atan(scale tan alt)``, written so that 90 degrees stays 90 degrees."""
    radians = np.radians(alt_deg)
    return np.degrees(np.arctan2(scale * np.sin(radians), np.cos(radians)))


def _unwarp_altitude(alt_deg, scale: float):
    """The inverse of :func:`_warp_altitude`: ``atan(tan alt / scale)``."""
    radians = np.radians(alt_deg)
    return np.degrees(np.arctan2(np.sin(radians), scale * np.cos(radians)))


def _focal(case_dir: Path, out_dir: Path, scale: float = 1.05) -> None:
    """A focal-length error: every altitude moves to ``atan(scale tan alt)``.

    Nothing moves at the horizontal or at the zenith and the error peaks near
    45 degrees, which is why a scorer that reported one median over all
    altitudes could report a number well inside the gate while the worst
    landmarks are outside it.
    """
    scale = float(scale)
    image = _read_panorama(out_dir)
    if image is not None:
        height = image.shape[0]
        # Destination row `y` shows the altitude the warp sends there, so its
        # content comes from the un-warped altitude. Nearest neighbour: this
        # is a corruption, and interpolation would blur it as well as move it.
        wanted = _row_altitudes(height)
        source_alt = _unwarp_altitude(wanted, scale)
        rows = np.clip(np.rint((PANORAMA_ALT_TOP - source_alt) / PANORAMA_ALT_SPAN
                               * max(height - 1, 1)),
                       0, height - 1).astype(np.int64)
        _write_panorama(out_dir, image[rows])

    def edit(horizon):
        altitudes = _horizon_altitudes(horizon)
        _set_horizon_altitudes(horizon, np.clip(_warp_altitude(altitudes, scale),
                                                0.0, 90.0))

    def edit_v2(horizon):
        profile, traced, state = _v2_arrays(horizon)
        _v2_store(horizon, np.clip(_warp_altitude(profile, scale), 0.0, 90.0),
                  np.clip(_warp_altitude(traced, scale), 0.0, 90.0), state)
        for point in horizon["points"]:
            point["alt"] = float(np.clip(_warp_altitude(point["alt"], scale), 0.0, 90.0))

    _edit_horizon(out_dir, edit, edit_v2)

    def warp(events):
        for event in events:
            basis = _basis_vectors(event)
            if basis is None:
                continue
            az, alt = sky_angles(basis["forward"])
            basis["forward"] = [float(v) for v in
                                sky_vector(az, float(_warp_altitude(alt, scale)))]

    _edit_events(out_dir, warp)


def _flip_vertical(case_dir: Path, out_dir: Path) -> None:
    """Reverse the raster's rows: a portrait readback handled upside down.

    The raster alone, as the plan scopes it. Altitude ``a`` ends up at
    ``80 - a``, which is past the matching radius for every landmark the chart
    yard has, so this reads as a wipeout rather than as a measurement.
    """
    image = _read_panorama(out_dir)
    if image is not None:
        _write_panorama(out_dir, image[::-1])


def _mirror(case_dir: Path, out_dir: Path) -> None:
    """Reverse the columns: ``az -> 360 - az`` for the raster, boundary and overlay."""
    image = _read_panorama(out_dir)
    if image is not None:
        _write_panorama(out_dir, image[:, ::-1])

    def edit(horizon):
        altitudes = _horizon_altitudes(horizon)
        bins = altitudes.size
        _set_horizon_altitudes(horizon, altitudes[::-1])
        horizon["uncertain_bins"] = sorted(
            bins - 1 - int(index) for index in horizon.get("uncertain_bins") or [])

    def edit_v2(horizon):
        profile, traced, state = _v2_arrays(horizon)
        _v2_store(horizon, profile[::-1], traced[::-1], state[::-1])
        horizon["points"] = _sorted_points(
            {"az": 360.0 - point["az"], "alt": point["alt"]} for point in horizon["points"])

    _edit_horizon(out_dir, edit, edit_v2)

    def reflect(events):
        for event in events:
            basis = _basis_vectors(event)
            if basis is None:
                continue
            for key in ("right", "up", "forward"):
                x, y, z = (float(v) for v in basis[key])
                basis[key] = [-x, y, z]

    _edit_events(out_dir, reflect)


def _section_columns(width: int, az0: float, width_deg: float) -> np.ndarray:
    """The raster columns whose azimuth lies in ``[az0, az0 + width_deg)``."""
    azimuths = _column_azimuths(width)
    return np.nonzero(((azimuths - float(az0)) % 360.0) < float(width_deg))[0]


def _duplicate_section(case_dir: Path, out_dir: Path, az0: float = 60.0,
                       width: float = 40.0) -> None:
    """Copy ``[az0, az0 + width)`` over the next ``width`` degrees.

    High up, where a degree of azimuth is a small angle, the copy lands inside
    the matching radius of the landmark it came from and is reported as a
    duplicate; lower down it lands outside and is reported as spurious.
    """
    image = _read_panorama(out_dir)
    if image is None:
        return
    raster_width = image.shape[1]
    source = _section_columns(raster_width, az0, width)
    step = int(round(float(width) / 360.0 * raster_width))
    image[:, (source + step) % raster_width] = image[:, source]
    _write_panorama(out_dir, image)


def _remove_section(case_dir: Path, out_dir: Path, az0: float = 100.0,
                    width: float = 40.0) -> None:
    """Blank ``[az0, az0 + width)``: unpainted raster, unresolved boundary.

    The boundary bins inside the range are set to 90 and listed as uncertain.
    The 90 is deliberate and must not be scored: a scanner that says "unknown"
    and writes a blocking altitude has still said unknown, and a scorer that
    read the altitude anyway would credit it with a boundary it disclaimed.
    """
    image = _read_panorama(out_dir)
    if image is not None:
        columns = _section_columns(image.shape[1], az0, width)
        image[:, columns, 3] = 0
        _write_panorama(out_dir, image)

    def edit(horizon):
        altitudes = _horizon_altitudes(horizon)
        bins = altitudes.size
        centres = (np.arange(bins) + 0.5) * (360.0 / bins)
        inside = np.nonzero(((centres - float(az0)) % 360.0) < float(width))[0]
        altitudes[inside] = 90.0
        _set_horizon_altitudes(horizon, altitudes)
        uncertain = set(int(i) for i in horizon.get("uncertain_bins") or [])
        uncertain.update(int(i) for i in inside)
        horizon["uncertain_bins"] = sorted(uncertain)

    def edit_v2(horizon):
        # Unknown at 90 with no traced altitude, and a polyline that blocks
        # the section: the version 2 way of saying "I could not see here".
        profile, traced, state = _v2_arrays(horizon)
        centres = (np.arange(PROFILE_BINS) + 0.5) * PROFILE_BIN_DEG
        inside = ((centres - float(az0)) % 360.0) < float(width)
        profile[inside], traced[inside], state[inside] = 90.0, math.nan, STATE_UNKNOWN
        _v2_store(horizon, profile, traced, state)
        horizon["points"] = _lift_polyline(horizon["points"], profile, inside)

    _edit_horizon(out_dir, edit, edit_v2)


def _wrong_reference(case_dir: Path, out_dir: Path, offset_m=(1.0, 0.0, 0.0)) -> None:
    """Re-render the panorama from ``c_ref + offset_m`` metres.

    The only corruption that needs the case: the scene and the reference
    position are what a wrong reference position is defined against. A scalar
    is taken as metres east.
    """
    if np.isscalar(offset_m):
        offset = np.array([float(offset_m), 0.0, 0.0])
    else:
        offset = np.asarray([float(v) for v in offset_m], dtype=np.float64)
    truth_dir = Path(case_dir) / "truth"
    scene = load_scene(truth_dir / "scene.json")
    c_ref = np.asarray(_read_json(truth_dir / "reference.json")["c_ref"], dtype=np.float64)
    existing = _read_panorama(out_dir)
    height, width = ((existing.shape[0], existing.shape[1]) if existing is not None
                     else (PANORAMA_HEIGHT, PANORAMA_WIDTH))
    panorama = truth_module.ideal_panorama(scene, c_ref + offset, width=width, height=height)
    _write_panorama(out_dir, panorama)


def _box_blur(rgb: np.ndarray, radius: int) -> np.ndarray:
    """A ``2 radius + 1`` box blur, wrapping in azimuth and clamped in altitude.

    Wrapping matters: a blur that treated column 0 and column 1079 as edges
    would leave a seam at north that is a second, undeclared corruption.
    """
    radius = int(radius)
    if radius <= 0:
        return rgb
    size = 2 * radius + 1
    values = rgb.astype(np.float64)

    padded = np.concatenate([values[:, -radius:], values, values[:, :radius]], axis=1)
    cumulative = np.cumsum(padded, axis=1)
    cumulative = np.concatenate(
        [np.zeros((values.shape[0], 1, values.shape[2])), cumulative], axis=1)
    values = (cumulative[:, size:] - cumulative[:, :-size]) / size

    padded = np.concatenate([np.repeat(values[:1], radius, axis=0), values,
                             np.repeat(values[-1:], radius, axis=0)], axis=0)
    cumulative = np.cumsum(padded, axis=0)
    cumulative = np.concatenate(
        [np.zeros((1, values.shape[1], values.shape[2])), cumulative], axis=0)
    values = (cumulative[size:] - cumulative[:-size]) / size

    return np.clip(np.rint(values), 0, 255).astype(np.uint8)


def _blur(case_dir: Path, out_dir: Path, radius_px: int = 5) -> None:
    """Blur the colours and leave the alpha alone.

    Leaving alpha alone is the point: the panorama still claims every cell as
    painted, so nothing but the landmark decoding can notice.
    """
    image = _read_panorama(out_dir)
    if image is None:
        return
    image[:, :, :3] = _box_blur(image[:, :, :3], radius_px)
    _write_panorama(out_dir, image)


def _erase_landmarks(case_dir: Path, out_dir: Path) -> None:
    """Paint every pixel the decoder could read as a palette colour grey.

    Every pixel within the decoder's own tolerance of any palette colour, not
    only the exact ones: erasing less than the decoder can see would leave the
    landmarks findable at the edges.
    """
    image = _read_panorama(out_dir)
    if image is None:
        return
    rgb = image[:, :, :3].astype(np.int16)
    painted = np.zeros(rgb.shape[:2], dtype=bool)
    for colour in PALETTE:
        target = np.asarray(colour, dtype=np.int16).reshape(1, 1, 3)
        painted |= np.abs(rgb - target).max(axis=2) <= COLOUR_TOLERANCE
    image[painted, :3] = np.asarray(ERASED_GREY, dtype=np.uint8)
    _write_panorama(out_dir, image)


def _empty(case_dir: Path, out_dir: Path) -> None:
    """Nothing painted and nothing captured: the refusal-shaped failure."""
    image = _read_panorama(out_dir)
    if image is not None:
        image[:, :, 3] = 0
        _write_panorama(out_dir, image)
    (out_dir / "captures.jsonl").write_text("", encoding="utf-8", newline="\n")


def _erase_horizon_strip(case_dir: Path, out_dir: Path, alt_max: float = 15.0) -> None:
    """Blank the sky below ``alt_max`` and declare every boundary bin uncertain.

    The strip that carries the whole horizon is a small part of the sphere by
    solid angle, so the panorama still reports most of its area painted. This
    is the case that proves total area cannot stand in for boundary evidence.
    """
    image = _read_panorama(out_dir)
    if image is not None:
        rows = _row_altitudes(image.shape[0]) <= float(alt_max)
        image[rows, :, 3] = 0
        _write_panorama(out_dir, image)

    def edit(horizon):
        horizon["uncertain_bins"] = list(range(len(horizon["points"])))

    def edit_v2(horizon):
        _, _, state = _v2_arrays(horizon)
        profile = np.full(PROFILE_BINS, 90.0)
        _v2_store(horizon, profile, np.full(PROFILE_BINS, math.nan),
                  np.full(state.shape, STATE_UNKNOWN))
        horizon["points"] = _lift_polyline(horizon["points"], profile,
                                           np.ones(PROFILE_BINS, dtype=bool))

    _edit_horizon(out_dir, edit, edit_v2)


def _brightness(case_dir: Path, out_dir: Path, gain: float = 1.2) -> None:
    """Scale the colours and change no geometry at all: the control."""
    image = _read_panorama(out_dir)
    if image is None:
        return
    scaled = np.rint(image[:, :, :3].astype(np.float64) * float(gain))
    image[:, :, :3] = np.clip(scaled, 0, 255).astype(np.uint8)
    _write_panorama(out_dir, image)


def _forward(event: dict):
    basis = event.get("basis")
    return None if not basis else np.asarray(basis["forward"], dtype=np.float64)


def _substitute_pose(case_dir: Path, out_dir: Path, back: int = 50,
                     index: int | None = None) -> None:
    """Give ONE event the basis an earlier event carried.

    With ``index`` unset the pair chosen is the one ``back`` events apart whose
    two bases differ by the most, so the substitution is the largest this
    route allows and the choice is deterministic. One frame in a thousand
    cannot move a percentile, which is why the scorer reports ``max_deg`` and
    ``frames_over_gate`` beside them.
    """
    back = int(back)

    def substitute(events):
        chosen = index
        if chosen is None:
            # The smallest dot product is the widest angle between the two
            # forward vectors, so this picks the most different pair.
            best = None
            for position in range(back, len(events)):
                now, then = _forward(events[position]), _forward(events[position - back])
                if now is None or then is None:
                    continue
                separation = float(np.dot(now, then))
                if best is None or separation < best:
                    best, chosen = separation, position
        if chosen is None or chosen - back < 0:
            raise ValueError("no event pair to substitute a pose between")
        events[chosen]["basis"] = json.loads(json.dumps(events[chosen - back]["basis"]))

    _edit_events(out_dir, substitute)


def _duplicate_frame(case_dir: Path, out_dir: Path, index: int | None = None) -> None:
    """Deliver one frame twice: the same ``frame_id`` on two event lines."""

    def duplicate(events):
        position = len(events) // 2 if index is None else int(index)
        events.insert(position + 1, json.loads(json.dumps(events[position])))

    _edit_events(out_dir, duplicate)


# --------------------------------------------------------------------------
# Version 2 corruptions (spec 7.6: each is the one that must fail its gate)
# --------------------------------------------------------------------------


def _bins_from(state: np.ndarray, wanted: int, az0: float) -> np.ndarray:
    """Measured bins in azimuth order starting at ``az0``, the first ``wanted``."""
    measured = np.nonzero(state == STATE_MEASURED)[0]
    if measured.size == 0:
        raise ValueError("no Measured bin to relabel")
    ordered = measured[np.argsort((measured * PROFILE_BIN_DEG - float(az0)) % 360.0,
                                  kind="stable")]
    return ordered[:max(1, int(wanted))]


def _flat_run_middle(profile: np.ndarray, state: np.ndarray):
    """The middle bin of the longest run of Measured bins at one altitude."""
    best_length, best_start = 0, None
    start = 0
    for index in range(1, PROFILE_BINS + 1):
        ends = (index == PROFILE_BINS or state[index] != STATE_MEASURED
                or abs(profile[index] - profile[start]) > 1e-9)
        if ends:
            if state[start] == STATE_MEASURED and index - start > best_length:
                best_length, best_start = index - start, start
            start = index
    return None if best_start is None else best_start + best_length // 2


def _shift_line_down(case_dir: Path, out_dir: Path, deg: float = 1.0) -> None:
    """Lower the published polyline by ``deg`` degrees everywhere.

    Nothing else moves: not the profile, the states or the panorama. The line
    is now ``deg`` below what was measured, which is a degree of false-open
    sky at every azimuth, and exactly the size the horizon gate is stated in.
    The polyline is also below the profile it was built from, so
    `never_below_profile` fails with it by construction.
    """
    horizon = _require_horizon_v2(out_dir, "shift-line-down-1")
    for point in horizon["points"]:
        point["alt"] = float(point["alt"]) - float(deg)
    _write_json(out_dir / "horizon.json", horizon)


def _dent_one_bin(case_dir: Path, out_dir: Path, bin_index=None, deg: float = 2.0) -> None:
    """Pull the polyline ``deg`` degrees below the profile across one bin.

    Both edges of the bin get a vertex ``deg`` below the bin's value, so the
    line is under the profile at both ends of the bin (G1) and the dent is
    local. By default the bin is the middle of the longest flat run of
    Measured bins, where no test obstacle stands and the line is otherwise
    exact.
    """
    horizon = _require_horizon_v2(out_dir, "dent-one-bin")
    profile, _, state = _v2_arrays(horizon)
    if bin_index is None:
        bin_index = _flat_run_middle(profile, state)
        if bin_index is None:
            raise ValueError("no Measured bin to dent")
    bin_index = int(bin_index) % PROFILE_BINS
    level = float(profile[bin_index]) - float(deg)
    # A vertex one bin out each side holds the line where it was, so the dent
    # is two ramps of one bin and a floor of one, however sparse the polyline.
    azimuths = [((bin_index + offset) % PROFILE_BINS) * PROFILE_BIN_DEG
                for offset in (-1, 0, 1, 2)]
    points = _sorted_points(horizon["points"])
    held = np.interp([azimuths[0], azimuths[3]], [p["az"] for p in points],
                     [p["alt"] for p in points], period=360.0)
    kept = [p for p in points
            if all(abs(p["az"] - az) > 1e-9 for az in azimuths)]
    horizon["points"] = sorted(
        kept + [{"az": azimuths[0], "alt": float(held[0])},
                {"az": azimuths[1], "alt": level}, {"az": azimuths[2], "alt": level},
                {"az": azimuths[3], "alt": float(held[1])}],
        key=lambda p: p["az"])
    _write_json(out_dir / "horizon.json", horizon)


def _relabel_unknown_measured(case_dir: Path, out_dir: Path) -> None:
    """Call every Unknown bin Measured, and change nothing else.

    A scanner that claims a measurement where it had none. The published
    altitude stays at 90, so the claim is wrong by the whole distance to the
    truth: this fails `unknown_where_unobservable` (its own gate) and, because
    each such bin is now graded, `measured_is_honest` and, once those bins are
    more than 5 per cent of the Measured azimuths, `horizon_p95_lt_1`.
    """
    horizon = _require_horizon_v2(out_dir, "relabel-unknown-measured")
    profile, traced, state = _v2_arrays(horizon)
    unknown = state == STATE_UNKNOWN
    if not unknown.any():
        raise ValueError("relabel-unknown-measured needs a result with Unknown bins")
    state[unknown] = STATE_MEASURED
    _v2_store(horizon, profile, traced, state)
    _write_json(out_dir / "horizon.json", horizon)


def _relabel_measured_low(case_dir: Path, out_dir: Path, fraction: float = 0.2,
                          az0: float = 0.0) -> None:
    """Call a run of Measured bins Low: too timid to publish what it measured.

    The first ``fraction`` of the Measured bins in azimuth order from ``az0``
    become Low at 90, keeping their traced altitude, and the polyline is lifted
    to block them. One contiguous run keeps the damage to its two ends; the
    share of Measured bins falls by ``fraction`` and by nothing else.
    """
    horizon = _require_horizon_v2(out_dir, "relabel-measured-low")
    profile, traced, state = _v2_arrays(horizon)
    count = math.ceil(float(fraction) * int((state == STATE_MEASURED).sum()))
    chosen = _bins_from(state, count, az0)
    state[chosen], profile[chosen] = STATE_LOW, 90.0
    _v2_store(horizon, profile, traced, state)
    horizon["points"] = _lift_polyline(horizon["points"], profile,
                                       np.isin(np.arange(PROFILE_BINS), chosen))
    _write_json(out_dir / "horizon.json", horizon)


def _mark_bins_unknown(case_dir: Path, out_dir: Path, count: int = 20,
                       az0: float = 0.0) -> None:
    """Call ``count`` contiguous Measured bins Unknown, blocked at 90.

    The traced altitude goes with them (an Unknown bin has no boundary), and
    the polyline is lifted to block the run. A fully observable case must have
    no such bin.
    """
    horizon = _require_horizon_v2(out_dir, "mark-bins-unknown")
    profile, traced, state = _v2_arrays(horizon)
    chosen = _bins_from(state, count, az0)
    state[chosen], profile[chosen], traced[chosen] = STATE_UNKNOWN, 90.0, math.nan
    _v2_store(horizon, profile, traced, state)
    horizon["points"] = _lift_polyline(horizon["points"], profile,
                                       np.isin(np.arange(PROFILE_BINS), chosen))
    _write_json(out_dir / "horizon.json", horizon)


def _drop_poses(case_dir: Path, out_dir: Path, keep_every: int = 10) -> None:
    """Null the basis on all but every ``keep_every``-th event line: 90 per cent.

    The lines stay, so the frames are still delivered and still named; only
    the pose is withheld. The poses that remain are exact.
    """
    path = out_dir / "events.jsonl"
    events = _read_jsonl(path)
    if not events:
        raise ValueError("drop-poses-90pct acts on events.jsonl, and this result has none")
    for position, event in enumerate(events):
        if position % int(keep_every):
            event["basis"] = None
    _write_jsonl(path, events)


def _yaw_ramp(case_dir: Path, out_dir: Path, deg: float = 3.0) -> None:
    """A yaw error growing linearly from 0 to ``deg`` across the scan's poses.

    Applied to the overlay only. One global yaw removes the mean of the ramp
    and leaves half of it either side, which is what a scan frame that drifts
    looks like and what the single-yaw removal cannot hide.
    """
    path = out_dir / "events.jsonl"
    events = _read_jsonl(path)
    posed = [event for event in events if _basis_vectors(event) is not None]
    if len(posed) < 2:
        raise ValueError("yaw-ramp needs at least two events with a basis")
    for position, event in enumerate(posed):
        turn = float(deg) * position / (len(posed) - 1)
        for key in ("right", "up", "forward"):
            event["basis"][key] = rotate_about_up(event["basis"][key], turn)
    _write_jsonl(path, events)


def _inflate_closure(case_dir: Path, out_dir: Path, deg: float = 0.5) -> None:
    """Turn the late keyframe of the loop match by ``deg`` about the vertical.

    The relative rotation the closure reports is then off by ``deg``, which is
    the loop residual to the degree. Only one of the keyframes moves, so the
    north error shifts by ``deg`` over the keyframe count and stays far inside
    its gate.
    """
    def edit(diagnostics):
        match = (diagnostics.get("loop") or {}).get("match")
        if not match:
            raise ValueError("inflate-closure needs a loop.match to inflate")
        late = next((k for k in diagnostics.get("keyframes") or []
                     if k.get("id") == match["late_kf"]), None)
        if late is None:
            raise ValueError("inflate-closure: the late keyframe is not in the result")
        late["q"] = _turn_pose(late["q"], float(deg))

    _edit_diagnostics(out_dir, edit, required="inflate-closure")


def _scale_focal(case_dir: Path, out_dir: Path, scale: float = 1.01) -> None:
    """Multiply the reported normalised focal length by ``scale``."""
    def edit(diagnostics):
        focal = diagnostics.get("focal")
        if not isinstance(focal, dict) or focal.get("f_norm") is None:
            raise ValueError("scale-focal-1.01 needs a focal.f_norm to scale")
        focal["f_norm"] = float(focal["f_norm"]) * float(scale)

    _edit_diagnostics(out_dir, edit, required="scale-focal-1.01")


def _shrink_north_sigma(case_dir: Path, out_dir: Path, sigma_deg: float = 0.1) -> None:
    """Set the reported north sigma to ``sigma_deg``: a scanner overconfident
    about a north it did not get exactly right."""
    def edit(diagnostics):
        north = diagnostics.get("north")
        if not isinstance(north, dict):
            raise ValueError("shrink-north-sigma needs a reported north")
        north["sigma_deg"] = float(sigma_deg)

    _edit_diagnostics(out_dir, edit, required="shrink-north-sigma")


def _delay_first_seen(case_dir: Path, out_dir: Path, deciseconds: int = 20) -> None:
    """Report every seen cell ``deciseconds`` tenths of a second late: 2 s.

    Cells never seen stay at 0, which is "never".
    """
    path = out_dir / "first_seen.bin"
    if not path.is_file():
        raise ValueError("delay-first-seen-2s acts on first_seen.bin, and this "
                         "result does not carry one")
    seen = np.frombuffer(path.read_bytes(), dtype="<u2").copy()
    late = seen > 0
    seen[late] = np.minimum(seen[late].astype(np.int64) + int(deciseconds),
                            65535).astype("<u2")
    path.write_bytes(seen.astype("<u2").tobytes())


#: Every corruption by the name the CLI and the tests use.
CORRUPTIONS = {
    "yaw": _yaw,
    "roll": _roll,
    "north-wrap": _north_wrap,
    "focal": _focal,
    "flip-vertical": _flip_vertical,
    "mirror": _mirror,
    "duplicate-section": _duplicate_section,
    "remove-section": _remove_section,
    "wrong-reference": _wrong_reference,
    "blur": _blur,
    "erase-landmarks": _erase_landmarks,
    "empty": _empty,
    "erase-horizon-strip": _erase_horizon_strip,
    "brightness": _brightness,
    "substitute-pose": _substitute_pose,
    "duplicate-frame": _duplicate_frame,
    "shift-line-down-1": _shift_line_down,
    "dent-one-bin": _dent_one_bin,
    "relabel-unknown-measured": _relabel_unknown_measured,
    "relabel-measured-low": _relabel_measured_low,
    "mark-bins-unknown": _mark_bins_unknown,
    "drop-poses-90pct": _drop_poses,
    "yaw-ramp": _yaw_ramp,
    "inflate-closure": _inflate_closure,
    "scale-focal-1.01": _scale_focal,
    "shrink-north-sigma": _shrink_north_sigma,
    "delay-first-seen-2s": _delay_first_seen,
}


def apply(case_dir, result_dir, name: str, out_dir, **params) -> Path:
    """Copy ``result_dir`` to ``out_dir``, corrupt it with ``name``, return it.

    ``case_dir`` supplies the scene and the reference position that
    ``wrong-reference`` is defined against; every other corruption ignores it.
    ``params`` are the corruption's own, and an unknown one raises rather than
    being silently ignored: a corruption that did not happen would be scored
    as a clean result and read as a scorer that cannot fail.
    """
    handler = CORRUPTIONS.get(name)
    if handler is None:
        raise ValueError(f"unknown corruption {name!r}; known: "
                         f"{', '.join(sorted(CORRUPTIONS))}")
    case_dir, result_dir, out_dir = Path(case_dir), Path(result_dir), Path(out_dir)
    if not result_dir.is_dir():
        raise ValueError(f"no result directory at {result_dir}")
    if out_dir.resolve() == result_dir.resolve():
        raise ValueError("a corruption writes a new result directory: "
                         "out_dir must not be result_dir")
    shutil.copytree(result_dir, out_dir, dirs_exist_ok=True)
    # The source's score and report describe the result that was corrupted.
    # Carrying them over would leave a green report sitting in a broken
    # directory, which is the failure this whole module exists to prevent.
    for stale in ("scores.json", "report.html"):
        (out_dir / stale).unlink(missing_ok=True)
    handler(case_dir, out_dir, **params)
    return out_dir
