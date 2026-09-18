"""Ground truth for a scene: what a ray from a given point actually meets.

This is the oracle every later stage is scored against, so it is analytic and
deliberately dull: ray/plane, ray/slab, ray/finite-vertical-cylinder and
ray/sphere, nearest positive parameter wins, ties to the earlier object in
file order. Nothing here is sampled or rendered, and nothing here imports the
process under test.

Conventions, all from CONTRACT.md:

- Angles are degrees in every signature; radians appear only inside a body.
- Directions are world ENU vectors; ``t`` is metres along a unit direction.
- Equirectangular images: column ``x`` is azimuth ``(x + 0.5) / W * 360``, row
  ``y`` is altitude ``90 - (y + 0.5) / H * 180``. The finished panorama is the
  one exception the contract declares: its rows run ``90 - y / (H - 1) * 100``.

Three conventions the schema text leaves to the implementation, written down
here because Task 4's renderer regenerates the same texture in JavaScript and
has to agree pixel for pixel:

1. The noise lattice of an octave is sampled at ``u = az / 360 * columns``
   (wrapping) and ``v = (90 - alt) / 180 * (rows - 1)`` (clamped). Because
   ``rows = columns / 2 + 1``, that makes the lattice cells square and puts
   lattice row 0 exactly at the zenith and row ``rows - 1`` at the nadir.
2. The grey value is truncated, not rounded: ``floor(grey0 + sum / ceiling *
   (grey1 - grey0))``, which is ``Math.floor`` in JavaScript. ``ceiling`` is
   the octave sum's exclusive bound, ``2 - 0.5 ** (octaves - 1)``, which is
   the contract's 1.875 for four octaves.
3. A stripe great circle through ``(az_k, 0)`` tilted by ``tilt`` has pole
   ``n = [cos(az_k) cos(tilt), -sin(az_k) cos(tilt), -sin(tilt)]``: the
   vertical circle's pole rotated about the through-point by ``+tilt``, so
   every stripe leans the same way. A pixel is on the stripe when
   ``|d . n| < sin(width / 2)``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .geometry import sky_angles, sky_vector
from .palette import PALETTE
from .scene import Scene

__all__ = [
    "BACKGROUND_SAMPLE",
    "Hit",
    "background_texture",
    "horizon",
    "ideal_panorama",
    "intersect",
    "landmark_directions",
    "lattice_hash",
]

#: The equirectangular size at which :func:`intersect` samples the background,
#: as the contract's intersection rule declares. 4096 columns is 0.088 degrees
#: per pixel, an order finer than the 1 degree landmark discs.
BACKGROUND_SAMPLE = (4096, 2048)

_U32 = 0xFFFFFFFF

#: Face-normal alignment a surface landmark demands of the hit face.
_NORMAL_DOT = 0.99


@dataclass(frozen=True)
class Hit:
    """What one batch of rays met, one row per ray.

    ``t`` is metres along the (normalised) direction and is ``inf`` for the
    background, which is directional and so has no distance. ``object_id`` is
    ``"background"`` there. ``colour`` is the 8-bit colour the ideal camera
    would record, and ``landmark_id`` is ``""`` unless the ray landed inside a
    landmark disc.
    """

    t: np.ndarray
    object_id: np.ndarray
    colour: np.ndarray
    landmark_id: np.ndarray


# --------------------------------------------------------------------------
# The background texture: value noise, stripes and landmark discs
# --------------------------------------------------------------------------


def lattice_hash(seed: int, octave: int, i: int, j: int) -> int:
    """The uint32 hash behind one noise lattice value, as CONTRACT.md declares.

    Python and JavaScript must produce the same integer here, so every product
    is truncated to 32 bits (``Math.imul`` and ``>>> 0`` on the other side) and
    every shift is unsigned. Divide by 2**32 for the lattice value in [0, 1).
    """
    h = (int(seed)
         ^ ((int(octave) * 0x9E3779B1) & _U32)
         ^ ((int(i) * 0x85EBCA77) & _U32)
         ^ ((int(j) * 0xC2B2AE3D) & _U32)) & _U32
    h ^= h >> 16
    h = (h * 0x7FEB352D) & _U32
    h ^= h >> 15
    h = (h * 0x846CA68B) & _U32
    h ^= h >> 16
    return h


def _lattice_grid(seed: int, octave: int, rows: int, columns: int) -> np.ndarray:
    """:func:`lattice_hash` over a whole lattice, as floats in [0, 1)."""
    i = np.arange(columns, dtype=np.uint32)[None, :]
    j = np.arange(rows, dtype=np.uint32)[:, None]
    h = (np.uint32(seed & _U32)
         ^ np.uint32((octave * 0x9E3779B1) & _U32)
         ^ (i * np.uint32(0x85EBCA77))
         ^ (j * np.uint32(0xC2B2AE3D)))
    h = h ^ (h >> np.uint32(16))
    h = h * np.uint32(0x7FEB352D)
    h = h ^ (h >> np.uint32(15))
    h = h * np.uint32(0x846CA68B)
    h = h ^ (h >> np.uint32(16))
    return h.astype(np.float64) / 4294967296.0


def _octave_lattice_shape(cells: int, octave: int) -> tuple[int, int]:
    """``(rows, columns)`` of octave ``octave``: ``cells * 2**o`` columns by
    ``cells * 2**(o - 1) + 1`` rows, so the cells are square."""
    columns = cells * 2 ** octave
    return (columns // 2 + 1, columns)


def _pixel_azimuths(width: int) -> np.ndarray:
    return (np.arange(width) + 0.5) / width * 360.0


def _pixel_altitudes(height: int) -> np.ndarray:
    return 90.0 - (np.arange(height) + 0.5) / height * 180.0


def _value_noise(texture: dict, az: np.ndarray, alt: np.ndarray) -> np.ndarray:
    """The octave sum over the grid ``alt`` x ``az``, in [0, ceiling).

    Bilinear in each octave, wrapping in azimuth and clamped in altitude. The
    weights are separable, so the columns and rows are prepared once and the
    only two-dimensional work is the four lattice gathers.
    """
    cells = int(texture["cells"])
    octaves = int(texture["octaves"])
    seed = int(texture["seed"])
    total = np.zeros((alt.size, az.size), dtype=np.float64)
    for octave in range(octaves):
        rows, columns = _octave_lattice_shape(cells, octave)
        lattice = _lattice_grid(seed, octave, rows, columns)

        u = az / 360.0 * columns
        iu = np.floor(u)
        fu = u - iu
        i0 = iu.astype(np.int64) % columns
        i1 = (i0 + 1) % columns

        v = (90.0 - alt) / 180.0 * (rows - 1)
        jv = np.floor(v)
        j0 = np.clip(jv.astype(np.int64), 0, rows - 2)
        fv = np.clip(v - j0, 0.0, 1.0)
        j1 = j0 + 1

        wu0, wu1 = (1.0 - fu)[None, :], fu[None, :]
        wv0, wv1 = (1.0 - fv)[:, None], fv[:, None]
        upper = lattice[np.ix_(j0, i0)] * wu0 + lattice[np.ix_(j0, i1)] * wu1
        lower = lattice[np.ix_(j1, i0)] * wu0 + lattice[np.ix_(j1, i1)] * wu1
        total += (upper * wv0 + lower * wv1) * 0.5 ** octave
    return total


def _stripe_mask(stripes: dict, az: np.ndarray, alt: np.ndarray) -> np.ndarray:
    """True where a direction is within half a stripe width of a great circle."""
    count = int(stripes["count"])
    tilt = math.radians(float(stripes["tilt_deg"]))
    half = math.sin(math.radians(float(stripes["width_deg"]) / 2.0))
    sin_az, cos_az = np.sin(np.radians(az))[None, :], np.cos(np.radians(az))[None, :]
    sin_alt, cos_alt = np.sin(np.radians(alt))[:, None], np.cos(np.radians(alt))[:, None]
    mask = np.zeros((alt.size, az.size), dtype=bool)
    for k in range(count):
        a = math.radians(k * 360.0 / count)
        nx = math.cos(a) * math.cos(tilt)
        ny = -math.sin(a) * math.cos(tilt)
        nz = -math.sin(tilt)
        dot = cos_alt * (sin_az * nx + cos_az * ny) + sin_alt * nz
        mask |= np.abs(dot) < half
    return mask


def _disc_window(az: np.ndarray, alt: np.ndarray, centre_az: float,
                 centre_alt: float, radius: float) -> tuple[np.ndarray, np.ndarray]:
    """The rows and columns a disc of this radius can possibly reach.

    Altitude differs from the centre by no more than the angular radius, and
    azimuth by no more than ``asin(sin(radius) / cos(alt))`` when that is
    defined, the classic tangency bound for a small circle; a disc near a pole
    gets every column. One pixel of margin is added at each edge, and the
    exact predicate is still evaluated inside the window.
    """
    row_margin = 180.0 / alt.size
    rows = np.nonzero(np.abs(alt - centre_alt) <= radius + row_margin)[0]
    denominator = math.cos(math.radians(centre_alt))
    sin_radius = math.sin(math.radians(radius))
    if denominator <= sin_radius:
        columns = np.arange(az.size)
    else:
        half = math.degrees(math.asin(sin_radius / denominator)) + 360.0 / az.size
        delta = np.abs((az - centre_az + 180.0) % 360.0 - 180.0)
        columns = np.nonzero(delta <= half)[0]
    return rows, columns


def background_texture(scene: Scene, width: int, height: int) -> np.ndarray:
    """The background as an equirectangular ``(height, width, 3)`` uint8 image.

    Value noise mapped into the declared grey range, then the stripe great
    circles, then the landmark discs in their palette colours, exactly the
    order the schema declares. Task 4's renderer builds this same image from
    the same JSON, which is why every step is written down in the module
    docstring rather than left to the code.

    No two discs in the chart yard overlap: its closest pair, two neighbours on
    the cap ring, clears the sum of its radii by 1.995 degrees, and
    ``test_no_two_background_discs_touch`` keeps every scene that way. The tie
    rule below is for a scene that does overlap anyway. Where two discs do
    overlap the EARLIER one in file order wins, the same way :func:`intersect`
    and the object tie rule resolve a tie, which is why the discs are painted
    from the last declared to the first: a painter's "later covers earlier"
    would otherwise disagree with the truth evaluator, and the renderer's image
    would not match the truth colour there. A synthetic two-disc scene in
    ``test_overlapping_discs_resolve_the_same_way_in_both_products`` pins it.
    """
    texture = scene.background["texture"]
    grey0, grey1 = (float(g) for g in texture["grey"])
    octaves = int(texture["octaves"])
    ceiling = 2.0 - 0.5 ** (octaves - 1)
    stripe_grey = float(texture["stripes"]["grey"])

    az = _pixel_azimuths(width)
    alt = _pixel_altitudes(height)
    image = np.empty((height, width, 3), dtype=np.uint8)
    block = max(1, 1_000_000 // max(width, 1))
    for start in range(0, height, block):
        stop = min(start + block, height)
        rows = alt[start:stop]
        value = np.floor(grey0 + _value_noise(texture, az, rows) / ceiling
                         * (grey1 - grey0))
        value[_stripe_mask(texture["stripes"], az, rows)] = stripe_grey
        image[start:stop] = value.astype(np.uint8)[:, :, None]

    sin_az, cos_az = np.sin(np.radians(az)), np.cos(np.radians(az))
    sin_alt, cos_alt = np.sin(np.radians(alt)), np.cos(np.radians(alt))
    for landmark in reversed(scene.landmarks):
        radius = float(landmark["radius_deg"])
        rows, columns = _disc_window(az, alt, float(landmark["az"]),
                                     float(landmark["alt"]), radius)
        if rows.size == 0 or columns.size == 0:
            continue
        centre = sky_vector(landmark["az"], landmark["alt"])
        dot = (cos_alt[rows][:, None]
               * (sin_az[columns][None, :] * centre[0]
                  + cos_az[columns][None, :] * centre[1])
               + sin_alt[rows][:, None] * centre[2])
        inside = dot > math.cos(math.radians(radius))
        if not inside.any():
            continue
        patch = image[np.ix_(rows, columns)]
        patch[inside] = PALETTE[int(landmark["palette"])]
        image[np.ix_(rows, columns)] = patch
    return image


def _background_sample(scene: Scene) -> np.ndarray:
    """The cached :data:`BACKGROUND_SAMPLE`-sized texture :func:`intersect` reads."""
    key = ("background-sample",) + BACKGROUND_SAMPLE
    image = scene.cache.get(key)
    if image is None:
        image = background_texture(scene, *BACKGROUND_SAMPLE)
        scene.cache[key] = image
    return image


# --------------------------------------------------------------------------
# Ray casting
# --------------------------------------------------------------------------


def _prepare(scene: Scene) -> list[dict]:
    """Per-object arrays for the caster, in file order."""
    prepared = []
    for obj in scene.objects:
        kind = obj["kind"]
        item = {"kind": kind, "id": obj["id"],
                "colour": np.asarray(obj["colour"], dtype=np.uint8)}
        if kind == "plane":
            item["z"] = float(obj["z"])
        elif kind == "box":
            item["min"] = np.asarray(obj["min"], dtype=np.float64)
            item["max"] = np.asarray(obj["max"], dtype=np.float64)
        elif kind == "cylinder":
            item["base"] = np.asarray(obj["base"], dtype=np.float64)
            item["radius"] = float(obj["radius"])
            item["height"] = float(obj["height"])
        else:
            item["centre"] = np.asarray(obj["centre"], dtype=np.float64)
            item["radius"] = float(obj["radius"])
        prepared.append(item)
    return prepared


def _hit_plane(obj, origin, dirs, want_normal):
    """A horizontal plane at ``z``, infinite. Its normal faces the ray."""
    count = dirs.shape[0]
    t = np.full(count, np.inf)
    dz = dirs[:, 2]
    live = dz != 0.0
    candidate = (obj["z"] - origin[2]) / dz[live]
    t[live] = np.where(candidate > 0.0, candidate, np.inf)
    normal = None
    if want_normal:
        normal = np.zeros((count, 3))
        normal[:, 2] = np.where(dz > 0.0, -1.0, 1.0)
    return t, normal


def _hit_box(obj, origin, dirs, want_normal):
    """An axis-aligned box by slabs; the normal is the entered face's, outward."""
    count = dirs.shape[0]
    low = np.full(count, -np.inf)
    high = np.full(count, np.inf)
    low_axis = np.zeros(count, dtype=np.int64)
    high_axis = np.zeros(count, dtype=np.int64)
    for axis in range(3):
        component = dirs[:, axis]
        live = component != 0.0
        outside = not obj["min"][axis] <= origin[axis] <= obj["max"][axis]
        near = np.full(count, np.inf if outside else -np.inf)
        far = np.full(count, -np.inf if outside else np.inf)
        inverse = 1.0 / component[live]
        first = (obj["min"][axis] - origin[axis]) * inverse
        second = (obj["max"][axis] - origin[axis]) * inverse
        near[live] = np.minimum(first, second)
        far[live] = np.maximum(first, second)
        entering = near > low
        low_axis = np.where(entering, axis, low_axis)
        low = np.where(entering, near, low)
        leaving = far < high
        high_axis = np.where(leaving, axis, high_axis)
        high = np.where(leaving, far, high)

    valid = low <= high
    entered = valid & (low > 0.0)
    t = np.where(entered, low, np.where(valid & (high > 0.0), high, np.inf))
    normal = None
    if want_normal:
        axis = np.where(entered, low_axis, high_axis)
        rows = np.arange(count)
        sign = np.where(entered, -1.0, 1.0) * np.sign(dirs[rows, axis])
        normal = np.zeros((count, 3))
        normal[rows, axis] = sign
    return t, normal


def _hit_cylinder(obj, origin, dirs, want_normal):
    """A finite cylinder with a vertical axis, side and both caps."""
    count = dirs.shape[0]
    base = obj["base"]
    radius = obj["radius"]
    height = obj["height"]
    ox, oy, oz = origin[0] - base[0], origin[1] - base[1], origin[2] - base[2]
    dx, dy, dz = dirs[:, 0], dirs[:, 1], dirs[:, 2]

    quadratic = dx * dx + dy * dy
    linear = 2.0 * (ox * dx + oy * dy)
    constant = ox * ox + oy * oy - radius * radius
    slanted = quadratic > 0.0
    discriminant = linear * linear - 4.0 * quadratic * constant
    rooted = slanted & (discriminant >= 0.0)
    root = np.zeros(count)
    root[rooted] = np.sqrt(discriminant[rooted])
    denominator = np.where(slanted, 2.0 * quadratic, 1.0)
    side = np.full(count, np.inf)
    for sign in (-1.0, 1.0):
        candidate = (-linear + sign * root) / denominator
        z = oz + candidate * dz
        good = (rooted & (candidate > 0.0) & (z >= 0.0) & (z <= height)
                & (candidate < side))
        side = np.where(good, candidate, side)

    cap = np.full(count, np.inf)
    cap_top = np.zeros(count, dtype=bool)
    live = dz != 0.0
    for cap_z, is_top in ((0.0, False), (height, True)):
        candidate = np.full(count, np.inf)
        candidate[live] = (cap_z - oz) / dz[live]
        reach = np.where(np.isfinite(candidate), candidate, 0.0)
        x, y = ox + reach * dx, oy + reach * dy
        good = (live & (candidate > 0.0) & (x * x + y * y <= radius * radius)
                & (candidate < cap))
        cap = np.where(good, candidate, cap)
        cap_top = np.where(good, is_top, cap_top)

    t = np.minimum(side, cap)
    normal = None
    if want_normal:
        on_cap = cap < side
        reach = np.where(np.isfinite(t), t, 0.0)
        x, y = ox + reach * dx, oy + reach * dy
        length = np.hypot(x, y)
        length = np.where(length > 0.0, length, 1.0)
        normal = np.zeros((count, 3))
        normal[:, 0] = x / length
        normal[:, 1] = y / length
        normal[on_cap] = 0.0
        normal[on_cap, 2] = np.where(cap_top[on_cap], 1.0, -1.0)
    return t, normal


def _hit_sphere(obj, origin, dirs, want_normal):
    """A sphere; the normal is outward from its centre."""
    count = dirs.shape[0]
    offset = np.asarray(origin, dtype=np.float64) - obj["centre"]
    quadratic = np.einsum("ij,ij->i", dirs, dirs)
    linear = 2.0 * (dirs @ offset)
    constant = float(offset @ offset) - obj["radius"] ** 2
    discriminant = linear * linear - 4.0 * quadratic * constant
    rooted = discriminant >= 0.0
    root = np.zeros(count)
    root[rooted] = np.sqrt(discriminant[rooted])
    near = (-linear - root) / (2.0 * quadratic)
    far = (-linear + root) / (2.0 * quadratic)
    t = np.where(rooted & (near > 0.0), near,
                 np.where(rooted & (far > 0.0), far, np.inf))
    normal = None
    if want_normal:
        reach = np.where(np.isfinite(t), t, 0.0)[:, None]
        point = np.asarray(origin, dtype=np.float64) + reach * dirs - obj["centre"]
        normal = point / obj["radius"]
    return t, normal


_HITTERS = {"plane": _hit_plane, "box": _hit_box,
            "cylinder": _hit_cylinder, "sphere": _hit_sphere}


def _cast(prepared, origin, dirs, want_normal=False):
    """Nearest positive hit over every object: ``(t, object index, normal)``.

    ``object index`` is -1 where nothing was hit. Comparison is strict, so a
    tie stays with the object that came first in the scene file.
    """
    count = dirs.shape[0]
    t_best = np.full(count, np.inf)
    index = np.full(count, -1, dtype=np.int64)
    normal = np.zeros((count, 3)) if want_normal else None
    for position, obj in enumerate(prepared):
        t, face = _HITTERS[obj["kind"]](obj, origin, dirs, want_normal)
        better = t < t_best
        t_best = np.where(better, t, t_best)
        index = np.where(better, position, index)
        if want_normal:
            normal[better] = face[better]
    return t_best, index, normal


def _direction_angles(dirs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Azimuths in [0, 360) and altitudes in degrees for unit directions."""
    az = np.degrees(np.arctan2(dirs[:, 0], dirs[:, 1])) % 360.0
    alt = np.degrees(np.arcsin(np.clip(dirs[:, 2], -1.0, 1.0)))
    return az, alt


def intersect(scene: Scene, origin, dirs) -> Hit:
    """Cast ``dirs`` from ``origin`` and report what each ray met.

    ``origin`` is a world point in metres and ``dirs`` is ``(N, 3)`` of world
    directions, which are normalised here so that ``t`` comes back in metres.
    Rays that pass everything closer than the background's ``distance_m`` are
    background: ``t`` is ``inf``, the colour is the background texture sampled
    nearest-neighbour at :data:`BACKGROUND_SAMPLE`, and a direction inside a
    landmark disc takes that landmark's palette colour and id instead. Where
    discs overlap the earlier one in file order wins, which is how
    :func:`background_texture` paints them too.
    """
    origin = np.asarray(origin, dtype=np.float64)
    dirs = np.asarray(dirs, dtype=np.float64)
    if dirs.ndim != 2 or dirs.shape[1] != 3:
        raise ValueError(f"expected directions of shape (N, 3), got {dirs.shape}")
    unit = dirs / np.linalg.norm(dirs, axis=1, keepdims=True)

    prepared = _prepare(scene)
    t, index, normal = _cast(prepared, origin, unit,
                             want_normal=bool(scene.surface_landmarks))
    distance = float(scene.background["distance_m"])
    background = (index < 0) | (t >= distance)

    names = np.array([obj["id"] for obj in prepared] + ["background"])
    colours = np.array([obj["colour"] for obj in prepared] + [[0, 0, 0]],
                       dtype=np.uint8)
    lookup = np.where(background, len(prepared), np.maximum(index, 0))
    object_id = names[lookup]
    colour = colours[lookup]

    all_ids = ([lm["id"] for lm in scene.landmarks]
               + [lm["id"] for lm in scene.surface_landmarks])
    landmark_id = np.full(unit.shape[0], "",
                          dtype=f"<U{max((len(i) for i in all_ids), default=1)}")

    if background.any():
        rows = np.nonzero(background)[0]
        named = np.zeros(rows.size, dtype=bool)
        if scene.landmarks:
            centres = np.array([sky_vector(lm["az"], lm["alt"])
                                for lm in scene.landmarks])
            cosines = np.cos(np.radians([float(lm["radius_deg"])
                                         for lm in scene.landmarks]))
            inside = (unit[rows] @ centres.T) > cosines[None, :]
            named = inside.any(axis=1)
            first = inside.argmax(axis=1)[named]
            target = rows[named]
            disc_colours = np.array([PALETTE[int(lm["palette"])]
                                     for lm in scene.landmarks], dtype=np.uint8)
            disc_ids = np.array([lm["id"] for lm in scene.landmarks])
            colour[target] = disc_colours[first]
            landmark_id[target] = disc_ids[first]

        # The disc colour wins over the texture wherever both apply, so the
        # texture is only sampled for the rays a disc did not claim, and a
        # batch that lands entirely inside discs never builds one.
        plain = rows[~named]
        if plain.size:
            sample = _background_sample(scene)
            height, width = sample.shape[:2]
            az, alt = _direction_angles(unit[plain])
            column = np.clip(np.floor(az / 360.0 * width).astype(np.int64),
                             0, width - 1)
            row = np.clip(np.floor((90.0 - alt) / 180.0 * height).astype(np.int64),
                          0, height - 1)
            colour[plain] = sample[row, column]

    positions = {obj["id"]: position for position, obj in enumerate(prepared)}
    for landmark in scene.surface_landmarks:
        rows = np.nonzero((~background) & (index == positions[landmark["object"]])
                          & (landmark_id == ""))[0]
        if rows.size == 0:
            continue
        point = origin + t[rows][:, None] * unit[rows]
        centre = np.asarray(landmark["centre"], dtype=np.float64)
        face = np.asarray(landmark["normal"], dtype=np.float64)
        face = face / np.linalg.norm(face)
        near = np.linalg.norm(point - centre, axis=1) <= float(landmark["radius_m"])
        aligned = (normal[rows] @ face) > _NORMAL_DOT
        target = rows[near & aligned]
        colour[target] = PALETTE[int(landmark["palette"])]
        landmark_id[target] = landmark["id"]

    return Hit(t=np.where(background, np.inf, t), object_id=object_id,
               colour=colour, landmark_id=landmark_id)


# --------------------------------------------------------------------------
# The derived truth products
# --------------------------------------------------------------------------


def _arc(hit_bins: np.ndarray, bins: int) -> tuple[float | None, float | None]:
    """The smallest azimuth arc (bin edges) containing every bin in ``hit_bins``.

    Found as the complement of the largest gap between consecutive hit bins,
    which handles a run that wraps through north: the result then has
    ``az_to < az_from``. An empty set gets ``(None, None)``, and a tie between
    two equal largest gaps takes the earlier one.
    """
    if hit_bins.size == 0:
        return (None, None)
    step = 360.0 / bins
    if hit_bins.size == bins:
        return (0.0, 360.0)
    gaps = np.append(np.diff(hit_bins), hit_bins[0] + bins - hit_bins[-1])
    widest = int(np.argmax(gaps))
    first = int(hit_bins[(widest + 1) % hit_bins.size])
    last = int(hit_bins[widest])
    return (first * step, (last + 1) * step)


def horizon(scene: Scene, c_ref, bins: int = 3600, alt_step: float = 0.05) -> dict:
    """The highest obstruction at every azimuth, as ``reference-horizon.json``.

    One ray per (bin centre, altitude) with altitudes from -10 to 90 degrees in
    steps of ``alt_step``; ``alt_max[b]`` is the highest of those altitudes
    whose nearest hit is an object rather than the background, and -10 when
    none of them is. The defaults are 3600 bins and 0.05 degrees, which is 7.2
    million rays, so the rays are cast in chunks of whole azimuth bins.

    ``obstacles`` carries one entry per declared test obstacle: the azimuth
    range in which that object is the FIRST thing hit at some altitude, and the
    highest altitude at which that is true. An obstacle that is never the first
    hit reports ``az_from``/``az_to`` of ``None`` and ``alt_max`` of -10.
    """
    origin = np.asarray(c_ref, dtype=np.float64)
    prepared = _prepare(scene)
    distance = float(scene.background["distance_m"])

    altitudes = np.linspace(-10.0, 90.0, int(round(100.0 / alt_step)) + 1)
    sin_alt = np.sin(np.radians(altitudes))
    cos_alt = np.cos(np.radians(altitudes))
    alt_max = np.full(bins, -10.0)

    positions = {obj["id"]: position for position, obj in enumerate(prepared)}
    watched = [(o["id"], positions[o["object"]]) for o in scene.test_obstacles]
    seen = {name: np.zeros(bins, dtype=bool) for name, _ in watched}
    tops = {name: -10.0 for name, _ in watched}

    chunk = max(1, 200_000 // altitudes.size)
    for start in range(0, bins, chunk):
        stop = min(start + chunk, bins)
        az = (np.arange(start, stop) + 0.5) * (360.0 / bins)
        sin_az = np.sin(np.radians(az))[:, None]
        cos_az = np.cos(np.radians(az))[:, None]
        dirs = np.empty((stop - start, altitudes.size, 3))
        dirs[:, :, 0] = sin_az * cos_alt[None, :]
        dirs[:, :, 1] = cos_az * cos_alt[None, :]
        dirs[:, :, 2] = sin_alt[None, :]

        t, index, _ = _cast(prepared, origin, dirs.reshape(-1, 3))
        solid = ((index >= 0) & (t < distance)).reshape(stop - start, altitudes.size)
        anything = solid.any(axis=1)
        highest = altitudes.size - 1 - np.argmax(solid[:, ::-1], axis=1)
        alt_max[start:stop] = np.where(anything, altitudes[highest], -10.0)

        which = index.reshape(stop - start, altitudes.size)
        for name, position in watched:
            mine = solid & (which == position)
            seen[name][start:stop] = mine.any(axis=1)
            by_altitude = mine.any(axis=0)
            if by_altitude.any():
                top = altitudes[altitudes.size - 1 - np.argmax(by_altitude[::-1])]
                tops[name] = max(tops[name], float(top))

    obstacles = []
    for declared in scene.test_obstacles:
        az_from, az_to = _arc(np.nonzero(seen[declared["id"]])[0], bins)
        obstacles.append({"id": declared["id"], "az_from": az_from, "az_to": az_to,
                          "alt_max": tops[declared["id"]],
                          "min_width_deg": declared["min_width_deg"]})
    return {"bins": bins, "alt_max": [float(a) for a in alt_max],
            "obstacles": obstacles}


def landmark_directions(scene: Scene, c_ref) -> list[dict]:
    """Every landmark's direction from ``c_ref``, as ``landmarks.json``.

    A background landmark's direction is the disc's own ``(az, alt)``, because
    the background is directional; a surface landmark's is the direction to its
    centre. ``observable`` is true when a ray from ``c_ref`` that way reports
    that landmark's id, so an occluded disc, or a face turned too far away to
    pass the normal test, is false.
    """
    origin = np.asarray(c_ref, dtype=np.float64)
    entries: list[tuple] = []
    dirs = []
    for landmark in scene.landmarks:
        az, alt = float(landmark["az"]), float(landmark["alt"])
        entries.append((landmark["id"], int(landmark["palette"]), az, alt, "background"))
        dirs.append(sky_vector(az, alt))
    for landmark in scene.surface_landmarks:
        offset = np.asarray(landmark["centre"], dtype=np.float64) - origin
        az, alt = sky_angles(offset)
        entries.append((landmark["id"], int(landmark["palette"]), az, alt, "surface"))
        dirs.append(offset / np.linalg.norm(offset))

    if not entries:
        return []
    hit = intersect(scene, origin, np.array(dirs))
    return [{"id": name, "colour": list(PALETTE[index]), "az": az, "alt": alt,
             "observable": bool(hit.landmark_id[row] == name), "kind": kind}
            for row, (name, index, az, alt, kind) in enumerate(entries)]


def ideal_panorama(scene: Scene, c_ref, width: int = 1080,
                   height: int = 300) -> np.ndarray:
    """The panorama a perfect scanner would hand back, ``(height, width, 4)``.

    One ray per pixel, in CONTRACT.md's result-panorama mapping: column ``x``
    is azimuth ``(x + 0.5) / width * 360`` and row ``y`` is altitude
    ``90 - y / (height - 1) * 100``. Alpha is 255 everywhere, because an ideal
    scanner leaves nothing unpainted.
    """
    az = (np.arange(width) + 0.5) / width * 360.0
    sin_az = np.sin(np.radians(az))[None, :]
    cos_az = np.cos(np.radians(az))[None, :]
    image = np.empty((height, width, 4), dtype=np.uint8)
    image[:, :, 3] = 255
    block = max(1, 60_000 // max(width, 1))
    for start in range(0, height, block):
        stop = min(start + block, height)
        alt = 90.0 - np.arange(start, stop) / (height - 1) * 100.0
        sin_alt = np.sin(np.radians(alt))[:, None]
        cos_alt = np.cos(np.radians(alt))[:, None]
        dirs = np.empty((stop - start, width, 3))
        dirs[:, :, 0] = sin_az * cos_alt
        dirs[:, :, 1] = cos_az * cos_alt
        dirs[:, :, 2] = np.broadcast_to(sin_alt, (stop - start, width))
        hit = intersect(scene, c_ref, dirs.reshape(-1, 3))
        image[start:stop, :, :3] = hit.colour.reshape(stop - start, width, 3)
    return image
