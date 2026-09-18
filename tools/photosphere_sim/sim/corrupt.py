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
leaves it no longer perpendicular to ``right`` and ``up``. The scorer reads
``forward`` and nothing else, and these are faults, not device models.
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
# would be an erasure that leaves the landmarks findable.
from .score import COLOUR_TOLERANCE

__all__ = ["CORRUPTIONS", "apply"]

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
    return 90.0 - np.arange(height) / max(height - 1, 1) * 100.0


def _horizon_altitudes(horizon: dict) -> np.ndarray:
    return np.array([float(p["alt"]) for p in horizon["points"]], dtype=np.float64)


def _set_horizon_altitudes(horizon: dict, values: np.ndarray) -> None:
    for point, value in zip(horizon["points"], values):
        point["alt"] = float(value)


def _edit_horizon(out_dir: Path, edit) -> None:
    """Apply ``edit(horizon)`` to ``horizon.json`` if there is one to edit."""
    path = out_dir / "horizon.json"
    horizon = _read_json(path)
    if not horizon or not horizon.get("points"):
        return
    edit(horizon)
    _write_json(path, horizon)


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


def _rotate_about_up(vector, deg: float) -> list:
    """Turn a world vector east by ``deg`` degrees about the vertical.

    Azimuth runs clockwise from north, so adding to the azimuth of
    ``[sin az cos alt, cos az cos alt, sin alt]`` is
    ``x' = x cos d + y sin d``, ``y' = y cos d - x sin d``.
    """
    radians = math.radians(deg)
    cos, sin = math.cos(radians), math.sin(radians)
    x, y, z = (float(v) for v in vector)
    return [x * cos + y * sin, y * cos - x * sin, z]


def _yaw(case_dir: Path, out_dir: Path, deg: float = 1.0) -> None:
    """Turn the whole result east by ``deg``: raster, boundary and overlay.

    The raster is rolled by a whole number of columns so the azimuth put in is
    exact, and the boundary is rolled by the matching whole number of bins.
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

    _edit_horizon(out_dir, edit)

    def turn(events):
        for event in events:
            basis = _basis_vectors(event)
            if basis is None:
                continue
            for key in ("right", "up", "forward"):
                basis[key] = _rotate_about_up(basis[key], deg)

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
        rows = np.clip(np.rint((90.0 - source_alt) / 100.0 * max(height - 1, 1)),
                       0, height - 1).astype(np.int64)
        _write_panorama(out_dir, image[rows])

    def edit(horizon):
        altitudes = _horizon_altitudes(horizon)
        _set_horizon_altitudes(horizon, np.clip(_warp_altitude(altitudes, scale),
                                                0.0, 90.0))

    _edit_horizon(out_dir, edit)

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

    _edit_horizon(out_dir, edit)

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

    _edit_horizon(out_dir, edit)


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
    height, width = (existing.shape[0], existing.shape[1]) if existing is not None else (300, 1080)
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

    _edit_horizon(out_dir, edit)


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


#: Every corruption by the name the CLI and the tests use.
CORRUPTIONS = {
    "yaw": _yaw,
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
