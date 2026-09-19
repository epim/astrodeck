"""Score a replayed result directory against the case's own truth.

The scorer reads ``truth/`` and ``result/`` and nothing else. It never reads
the scanner's source, never trusts a transform the scanner exported, and never
drops an expected landmark or an expected boundary bin from a denominator: an
omission is reported as an omission, because a metric computed only over the
parts that worked is a claim about the parts that worked.

What each number means is set out in CONTRACT.md's "scores.json" section. Four
things are worth repeating here because they are the places a plausible-looking
implementation goes quietly wrong.

- Solid angle, not pixels. An equirectangular raster over-samples the sky near
  the pole by ``1 / cos(alt)``, so every area and every coverage fraction here
  is weighted by ``cos(alt)``. Counting raster cells equally would let a
  scanner buy coverage by painting the zenith.
- Uncertain boundary bins are unresolved, not open and not blocked. They are
  excluded from the signed error and from both false-area figures, their area
  is reported on its own, and a positive case fails on them. Unknown cannot
  satisfy coverage.
- The observable region comes from the scene and the delivered frames, not
  from whatever the scanner chose to accept.
- A missing file is evidence of absence, not an excuse to skip a metric: no
  capture log means no hold was captured, and a blank panorama means every
  landmark was omitted.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

from . import blobs as blobs_module
from .cases import CASES_DIR
from .geometry import angle_between, sky_vector
from .palette import PALETTE
# The face-normal tolerance the truth paints a surface landmark with. Imported
# rather than copied: the expected-area model has to move with it.
from .truth import _NORMAL_DOT

__all__ = ["first_line_per_frame", "score_case"]

#: The result panorama's shape, from CONTRACT.md's "Result directory". These
#: four are the one definition of the raster mapping in this package:
#: ``sim.report`` and ``sim.corrupt`` import them rather than restate them, so
#: a raster that changed shape could not be read one way by the scorer and
#: another way by the page that draws its answer.
PANORAMA_WIDTH = 1080
PANORAMA_HEIGHT = 300
PANORAMA_ALT_TOP = 90.0
PANORAMA_ALT_SPAN = 100.0

#: Largest per-channel difference at which a pixel is still that palette colour.
COLOUR_TOLERANCE = 40
#: A blob below this fraction of the expected disc area at its altitude is
#: noise rather than a landmark.
MIN_DISC_FRACTION = 0.4
#: How far a blob may sit from a landmark of its own colour and still be it.
MATCH_RADIUS_DEG = 8.0

#: The azimuth and altitude step of the truth horizon and of the cells the
#: false-area figures are summed over, in degrees.
HORIZON_CELL_DEG = 0.1
#: A measured boundary this far below the truth is a missed obstruction.
MISSED_OBSTRUCTION_DEG = 1.0
#: The range of north offsets searched, and its step, in degrees.
NORTH_OFFSET_LIMIT_DEG = 10.0

#: Provisional gates, spec section 9. Fixed for comparability.
GATE_LANDMARK_P95 = 0.5
GATE_LANDMARK_P99 = 1.0
GATE_HORIZON_P95 = 1.0
GATE_OVERLAY_SETTLED_P95 = 0.5
GATE_OVERLAY_MOVING_P95 = 1.0
#: No overlay sample, moving or settled, may be this far out. A percentile
#: cannot see one frame, and one frame that flicks the aim dot across a cell
#: is a fault the user sees.
GATE_OVERLAY_MAX = 10.0
GATE_CAPTURE_P95_MS = 1500
GATE_COVERAGE = 0.95

#: Above this truth turn rate a frame counts as moving rather than settled.
MOVING_RATE_DEG_S = 2.0
#: How late after a hold closes a capture may still land and count.
CAPTURE_GRACE_MS = 1500


# --------------------------------------------------------------------------
# Reading the case and the result
# --------------------------------------------------------------------------


def _read_json(path: Path, default=None):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list:
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line]


def _load_panorama(path: Path):
    """The result panorama as ``(H, W, 4)`` uint8, and what was wrong with it.

    Anything that is not a 1080 x 300 raster with an alpha channel scores as
    EMPTY, and `note` says which. Converting an alpha-less image to RGBA would
    invent alpha 255 for every pixel and hand a scanner that wrote the wrong
    format a perfect coverage score; a panorama of the wrong size would be
    read against the wrong azimuths. Neither is a thing to guess at.
    """
    empty = np.zeros((PANORAMA_HEIGHT, PANORAMA_WIDTH, 4), dtype=np.uint8)
    if not path.is_file():
        return empty, {"width": None, "height": None, "mode": None,
                       "note": "no panorama.png: nothing was painted"}
    with Image.open(path) as image:
        width, height = image.size
        info = {"width": width, "height": height, "mode": image.mode, "note": None}
        if "A" not in image.getbands():
            info["note"] = (f"panorama is {image.mode}, which carries no alpha: "
                            "scored as empty")
            return empty, info
        if (width, height) != (PANORAMA_WIDTH, PANORAMA_HEIGHT):
            info["note"] = (f"panorama is {width} x {height}, not "
                            f"{PANORAMA_WIDTH} x {PANORAMA_HEIGHT}: scored as empty")
            return empty, info
        return np.array(image.convert("RGBA")), info


def _raster_grid(height: int, width: int) -> tuple[np.ndarray, np.ndarray]:
    """Azimuths per column and altitudes per row, in CONTRACT.md's mapping."""
    az = (np.arange(width) + 0.5) / width * 360.0
    alt = PANORAMA_ALT_TOP - np.arange(height) / max(height - 1, 1) * PANORAMA_ALT_SPAN
    return az, alt


def _percentile(values, q):
    if len(values) == 0:
        return None
    return float(np.percentile(np.asarray(values, dtype=np.float64), q))


def _combined_digest(parts) -> str:
    """SHA-256 over concatenated hex digests, ``cases.py``'s own recipe."""
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("ascii"))
    return digest.hexdigest()


# --------------------------------------------------------------------------
# Landmarks
# --------------------------------------------------------------------------


def _expected_disc_areas(scene: dict, c_ref: np.ndarray) -> dict:
    """Each landmark's true angular area in square degrees, and its palette.

    A background disc of angular radius ``r`` covers ``pi r^2``. A surface
    disc is a flat disc of radius ``radius_m`` at distance ``D`` seen at an
    angle, so it covers ``pi radius_m^2 |n . d| / D^2`` steradians: the
    foreshortening belongs in the expected area, because leaving it out claims
    a grazing disc should be two or three times the size it can possibly be.

    A disc on a curved face is clipped again. ``sim.truth`` paints a surface
    landmark only where the hit face's normal is within ``acos(_NORMAL_DOT)``
    of the declared one, so on a host of radius ``R`` only a band
    ``R sin(acos(_NORMAL_DOT))`` wide survives. Where that band is narrower
    than the disc, the expected area is scaled by the ratio of the two widths:
    a lower bound on the clipped area, which is the safe direction for a
    filter that must never discard a landmark. Without it the chart yard's
    ``T1``, a 0.08 m disc on a 0.25 m trunk, is measured against an unclipped
    model it can only ever fill 44 per cent of.

    How many times that ratio applies is the difference between the two curved
    hosts. A cylinder curves in one direction only: the band limits the disc
    across the cylinder and the disc keeps its full extent along the axis, so
    the factor is the ratio. A sphere curves in both, so the same limit
    applies twice and the factor is the ratio squared. Using the linear factor
    for a sphere would expect a cap several times the area it can possibly
    have, and the 40 per cent filter would then discard the landmark it had
    not yet identified.
    """
    band = math.sin(math.acos(_NORMAL_DOT))
    objects = {obj["id"]: obj for obj in scene.get("objects", [])}
    areas: dict[str, tuple[float, int]] = {}
    for landmark in scene.get("landmarks", []):
        radius = float(landmark["radius_deg"])
        areas[landmark["id"]] = (math.pi * radius * radius, int(landmark["palette"]))
    for landmark in scene.get("surface_landmarks", []):
        offset = np.asarray(landmark["centre"], dtype=np.float64) - c_ref
        distance = float(np.linalg.norm(offset))
        normal = np.asarray(landmark["normal"], dtype=np.float64)
        normal = normal / np.linalg.norm(normal)
        facing = abs(float((offset / distance) @ normal))
        radius = float(landmark["radius_m"])
        host = objects.get(landmark["object"], {})
        clipped = 1.0
        if host.get("kind") == "cylinder":
            clipped = min(1.0, float(host["radius"]) * band / radius)
        elif host.get("kind") == "sphere":
            clipped = min(1.0, (float(host["radius"]) * band / radius) ** 2)
        steradians = math.pi * radius * radius * facing * clipped / (distance * distance)
        areas[landmark["id"]] = (steradians * (180.0 / math.pi) ** 2,
                                 int(landmark["palette"]))
    return areas


def _score_landmarks(scene: dict, landmarks: list, c_ref: np.ndarray,
                     panorama: np.ndarray) -> dict:
    height, width = panorama.shape[:2]
    cell_alt = PANORAMA_ALT_SPAN / max(height - 1, 1)
    cell_az_equator = 360.0 / width
    # A cell's true width in azimuth shrinks to nothing at the pole, and a
    # blob centred exactly there would divide by cos(90). The narrowest a cell
    # is taken to be is its width half a cell below the pole.
    cos_floor = math.cos(math.radians(90.0 - cell_alt / 2.0))

    areas = _expected_disc_areas(scene, c_ref)
    by_colour: dict[int, list[str]] = {}
    for name, (_, index) in areas.items():
        by_colour.setdefault(index, []).append(name)
    smallest_overall = min((area for area, _ in areas.values()), default=0.0)

    truth_dirs = {lm["id"]: sky_vector(lm["az"], lm["alt"]) for lm in landmarks}
    matches: dict[str, list[dict]] = {lm["id"]: [] for lm in landmarks}
    spurious = 0

    rgb = panorama[:, :, :3]
    alpha = panorama[:, :, 3]
    for index, colour in enumerate(PALETTE):
        candidates = by_colour.get(index, [])
        smallest = min((areas[name][0] for name in candidates), default=smallest_overall)
        for blob in blobs_module.find(rgb, alpha, colour,
                                      tolerance=COLOUR_TOLERANCE, min_pixels=1):
            alt = PANORAMA_ALT_TOP - blob.cy / max(height - 1, 1) * PANORAMA_ALT_SPAN
            az = (blob.cx + 0.5) / width * 360.0
            cell_az = cell_az_equator * max(math.cos(math.radians(alt)), cos_floor)
            if blob.pixels < MIN_DISC_FRACTION * smallest / (cell_az * cell_alt):
                continue
            direction = sky_vector(az, alt)
            nearest, best = None, MATCH_RADIUS_DEG
            for name in candidates:
                separation = angle_between(direction, truth_dirs[name])
                if separation <= best:
                    nearest, best = name, separation
            if nearest is None:
                spurious += 1
                continue
            matches[nearest].append({"az": az, "alt": alt, "error_deg": best})

    per_landmark = []
    errors = []
    omitted, duplicated, found, slivers = [], [], 0, 0
    for landmark in landmarks:
        name = landmark["id"]
        observable = bool(landmark["observable"])
        hits = sorted(matches[name], key=lambda m: m["error_deg"])
        # A landmark whose centre is occluded may still show a clipped sliver
        # of its disc. Nothing can be demanded of that sliver and its centroid
        # is not the landmark's direction, so it is neither credited nor
        # blamed: "sliver" says which entries those are, and they are the
        # difference between these 52 rows and the aggregates above.
        if observable:
            status = "found" if len(hits) == 1 else ("duplicate" if hits else "omitted")
        else:
            status = "sliver" if hits else "not_observable"
        best = hits[0] if hits else None
        area, _ = areas.get(name, (0.0, -1))
        cell = cell_az_equator * max(math.cos(math.radians(landmark["alt"])), cos_floor)
        per_landmark.append({
            "id": name,
            "observable": observable,
            "truth": {"az": landmark["az"], "alt": landmark["alt"]},
            "measured": None if best is None else {"az": best["az"], "alt": best["alt"]},
            "error_deg": None if best is None else best["error_deg"],
            "expected_px": area / (cell * cell_alt),
            "status": status,
        })
        if status == "found":
            found += 1
            errors.append(best["error_deg"])
        elif status == "duplicate":
            duplicated.append(name)
        elif status == "omitted":
            omitted.append(name)
        elif status == "sliver":
            slivers += 1

    return {
        "expected": sum(1 for lm in landmarks if lm["observable"]),
        "found": found,
        "omitted": omitted,
        "duplicated": duplicated,
        "slivers": slivers,
        "spurious": spurious,
        "errors_deg": {
            "median": _percentile(errors, 50),
            "p95": _percentile(errors, 95),
            "p99": _percentile(errors, 99),
            "max": float(max(errors)) if errors else None,
        },
        "per_landmark": per_landmark,
    }


# --------------------------------------------------------------------------
# Horizon
# --------------------------------------------------------------------------


def _measured_profile(measured: dict | None, truth_bins: int):
    """The measured boundary resampled onto the truth's bins, with its mask.

    Returns ``(alt, resolved, bins)``. Bin ``i`` of a measured profile of ``N``
    bins covers ``[i * 360 / N, (i + 1) * 360 / N)``, so a truth bin takes the
    measured bin its centre falls in. With no measured profile at all nothing
    is resolved: silence is not a flat horizon.
    """
    if not measured or not measured.get("points"):
        return np.zeros(truth_bins), np.zeros(truth_bins, dtype=bool), 0
    points = measured["points"]
    values = np.array([float(p["alt"]) for p in points], dtype=np.float64)
    # The array is the profile. A declared bin count that disagrees with it
    # describes a file that does not exist, so the points win.
    bins = len(points)
    uncertain = np.zeros(bins, dtype=bool)
    for index in measured.get("uncertain_bins", []) or []:
        if 0 <= int(index) < bins:
            uncertain[int(index)] = True
    centres = (np.arange(truth_bins) + 0.5) * (360.0 / truth_bins)
    owner = np.minimum((centres / (360.0 / bins)).astype(np.int64), bins - 1)
    return values[owner], ~uncertain[owner], bins


def _area_below(cos_cumulative: np.ndarray, centres: np.ndarray,
                altitudes: np.ndarray) -> np.ndarray:
    """Cos-weighted area of the altitude cells below each altitude, in sr."""
    index = np.searchsorted(centres, altitudes, side="left")
    return cos_cumulative[index]


def _score_obstacles(reference: dict, truth_bins: int, step: float,
                     alt: np.ndarray, resolved: np.ndarray) -> list:
    """Every declared test obstacle, scored against its own silhouette.

    The envelope cannot answer this question. The chart yard's trunk stands
    under its canopy, so the envelope over the trunk's azimuths is the canopy
    at 45.8 degrees while the trunk's own top is 21.5: a boundary that
    describes the canopy and nothing else looks identical to one that found
    both. ``profile`` from ``reference-horizon.json`` is where THAT object is
    the first thing hit, bin by bin, and the deficit is measured against it.

    ``deficit = profile - measured``, over the bins the object is visible in
    and the measurement resolved. The verdict has two terms, and either one
    misses the obstacle:

    - the MEDIAN deficit above ``MISSED_OBSTRUCTION_DEG``, not the minimum: a
      few bins at the edge of an obstacle straddle a coarse measured bin and
      go deeply negative or positive without the obstacle being lost;
    - ``width_missed_deg`` at or above the width the scene declares the
      obstacle must be found at. The median cannot see this one. The chart
      yard's roof spans 146 degrees, so a 10 degree notch cut out of it leaves
      1366 of 1466 bins right and the median at zero, while the whole of the
      declared minimum width is gone. An obstacle is found when it is found,
      not when most of it is.

    The width term costs a correct boundary nothing: the ideal's measured
    profile is at or above the envelope everywhere, so every deficit is at or
    below zero and every missed width is 0.0, at 3600 bins and at 30.
    """
    scored = []
    for obstacle in reference.get("obstacles", []):
        if "profile" not in obstacle:
            raise ValueError(
                f"reference-horizon.json carries no profile for "
                f"{obstacle['id']}: this case predates the per-obstacle "
                "silhouette, rebuild it with make-case")
        profile = np.asarray(obstacle["profile"], dtype=np.float64)
        if profile.size != truth_bins:
            raise ValueError(f"{obstacle['id']} profile has {profile.size} bins, "
                             f"not the truth's {truth_bins}")
        visible = profile > -10.0
        measurable = visible & resolved
        entry = {
            "id": obstacle["id"],
            "truth_alt_peak": float(obstacle.get("alt_max", -10.0)),
            "deficit_median": None,
            "deficit_p95": None,
            "width_missed_deg": None,
            "min_width_deg": obstacle.get("min_width_deg"),
            # Visible but with nothing resolved over it is a miss: no evidence
            # where evidence was expected.
            "missed": bool(visible.any()),
        }
        if measurable.any():
            deficit = np.clip(profile[measurable], 0.0, 90.0) - alt[measurable]
            median = float(np.median(deficit))
            width_missed = float(
                np.count_nonzero(deficit > MISSED_OBSTRUCTION_DEG) * step)
            declared = obstacle.get("min_width_deg")
            too_narrow = (declared is not None and float(declared) > 0.0
                          and width_missed >= float(declared))
            entry.update({
                "deficit_median": median,
                "deficit_p95": _percentile(deficit, 95),
                "width_missed_deg": width_missed,
                "missed": bool(median > MISSED_OBSTRUCTION_DEG or too_narrow),
            })
        elif not visible.any():
            entry["width_missed_deg"] = 0.0
        scored.append(entry)
    return scored


def _score_horizon(reference: dict, measured: dict | None) -> dict:
    truth_raw = np.asarray(reference["alt_max"], dtype=np.float64)
    truth_bins = int(reference.get("bins", truth_raw.size))
    # The scanner's boundary floor is 0 and its ceiling is the zenith; -10 in
    # the truth means no ray hit anything, not that the sky reaches below the
    # horizontal.
    truth = np.clip(truth_raw, 0.0, 90.0)
    alt, resolved, bins = _measured_profile(measured, truth_bins)

    step = 360.0 / truth_bins
    centres = (np.arange(truth_bins) + 0.5) * step

    cell = math.radians(HORIZON_CELL_DEG)
    alt_centres = np.arange(0.0, 90.0, HORIZON_CELL_DEG) + HORIZON_CELL_DEG / 2.0
    cos_cumulative = np.concatenate(
        [[0.0], np.cumsum(np.cos(np.radians(alt_centres)) * cell * cell)])
    column_sr = float(cos_cumulative[-1])

    signed = alt[resolved] - truth[resolved]
    absolute = np.abs(signed)

    below_truth = _area_below(cos_cumulative, alt_centres, truth[resolved])
    below_measured = _area_below(cos_cumulative, alt_centres, alt[resolved])
    difference = below_truth - below_measured
    false_open = float(np.clip(difference, 0.0, None).sum())
    false_blocked = float(np.clip(-difference, 0.0, None).sum())
    unresolved = float(np.count_nonzero(~resolved) * column_sr)

    obstacles = _score_obstacles(reference, truth_bins, step, alt, resolved)
    missed = [o["id"] for o in obstacles if o["missed"]]

    steps = int(round(NORTH_OFFSET_LIMIT_DEG / step))
    north_offset = None
    best_mean = None
    for shift in sorted(range(-steps, steps + 1), key=lambda k: (abs(k), k)):
        rolled_alt = np.roll(alt, -shift)
        rolled_resolved = np.roll(resolved, -shift)
        if not rolled_resolved.any():
            continue
        mean = float(np.abs(rolled_alt[rolled_resolved] - truth[rolled_resolved]).mean())
        if best_mean is None or mean < best_mean:
            best_mean, north_offset = mean, shift * step

    return {
        "truth_bins": truth_bins,
        "measured_bins": bins,
        "measured_resolution_deg": (360.0 / bins) if bins else None,
        "signed_error_deg": {
            "median": float(np.median(signed)) if signed.size else None,
            "p95": _percentile(absolute, 95),
            "max": float(absolute.max()) if absolute.size else None,
        },
        "false_open_sr": false_open,
        "false_blocked_sr": false_blocked,
        "unresolved_sr": unresolved,
        "obstacles": obstacles,
        "missed_obstructions": missed,
        "north_offset_deg": north_offset,
        "note": ("truth alt_max clipped to [0, 90]: the scanner's floor is 0, "
                 "and -10 in the truth means no ray hit anything"),
    }


# --------------------------------------------------------------------------
# Overlay, capture, coverage
# --------------------------------------------------------------------------


def first_line_per_frame(events: list) -> list:
    """The event lines that count, in delivery order: the FIRST per frame id.

    A `frame_id` on more than one line was delivered more than once. The later
    lines are not samples and are not considered at all, so anything that
    reads `events.jsonl` has to drop them the same way -- the scorer here and
    the timeline `sim.report` draws. Two copies of this rule are two chances
    for a report to plot a point no percentile beside it can account for,
    which is why there is one.

    A line with no `frame_id` is kept: it names no frame, so it cannot be a
    repeat of one.
    """
    seen = set()
    kept = []
    for event in events:
        frame_id = event.get("frame_id")
        if frame_id is not None:
            if frame_id in seen:
                continue
            seen.add(frame_id)
        kept.append(event)
    return kept


def _score_overlay(events: list, frames: list) -> dict:
    """Overlay attitude error per delivered frame, moving and settled apart.

    A percentile over a thousand frames cannot see one bad frame, and one
    frame pointing a degree wrong is exactly the failure the overlay gate is
    about, so `max_deg` and `frames_over_gate` are reported beside them, and
    the maximum has a gate of its own.

    A frame id that arrives more than once is scored once, on its FIRST line.
    Counting a repeat as a second sample would let a scanner raise its own
    sample count by reprocessing a frame, and taking the later line would let
    a second answer overwrite the answer it already gave for that frame.
    `duplicate_frame_ids` is how many ids arrived more than once, and a gate
    reads it: a repeat delivery is a fault, not a free retry.
    """
    truth = {frame["frame_id"]: frame for frame in frames}
    repeats = {}
    for event in events:
        frame_id = event.get("frame_id")
        if frame_id is not None:
            repeats[frame_id] = repeats.get(frame_id, 0) + 1
    duplicate_ids = sum(1 for count in repeats.values() if count > 1)

    moving, settled = [], []
    missing = 0
    considered = 0
    for event in first_line_per_frame(events):
        frame_id = event.get("frame_id")
        considered += 1
        basis = event.get("basis")
        frame = truth.get(frame_id)
        # A line naming a frame the case never delivered is a sample with no
        # pose. It cannot be scored, and it cannot vanish from the
        # denominator either, so it counts as missing.
        if basis is None or frame is None:
            missing += 1
            continue
        error = angle_between(basis["forward"], frame["forward"])
        if float(frame["angular_rate_deg_s"]) > MOVING_RATE_DEG_S:
            moving.append(error)
        else:
            settled.append(error)

    def block(values):
        return {"median_deg": _percentile(values, 50),
                "p95_deg": _percentile(values, 95),
                "max_deg": float(max(values)) if values else None}

    over_gate = (sum(1 for e in moving if e > GATE_OVERLAY_MOVING_P95)
                 + sum(1 for e in settled if e > GATE_OVERLAY_SETTLED_P95))
    return {
        "samples": len(moving) + len(settled),
        "missing_fraction": (missing / considered) if considered else None,
        "frames_over_gate": over_gate,
        "duplicate_frame_ids": duplicate_ids,
        "moving": block(moving),
        "settled": block(settled),
    }


def _score_capture(holds: list, captures: list) -> dict:
    """How each hold ended, walking the holds in time order.

    A hold is SATISFIED by the first unclaimed record inside its window whose
    outcome is `accepted` or `already-captured`, and a record is claimed once
    and never counted again. The 1500 ms grace makes consecutive windows
    overlap by most of a hold, so without the claim a single record answers
    for two holds.

    `already-captured` counts because the route revisits directions: an aim
    can land on a dome cell an earlier aim already photographed, and a scanner
    that declines to photograph it twice is right. Issue #54: counting only
    `accepted` failed 26 of the real still case's holds for correct behaviour.
    What the gate asks is that no hold ended in nothing, not that every hold
    produced a new frame, so the two outcomes are reported apart
    (`holds_captured`, `holds_already_covered`) and gated together.
    """
    accepted_total = sum(1 for c in captures if c.get("outcome") == "accepted")
    satisfying = [c for c in captures
                  if c.get("outcome") in ("accepted", "already-captured")]
    claimed = [False] * len(satisfying)
    latencies = []
    captured = already_covered = 0
    for hold in sorted(holds, key=lambda h: int(h["from_ms"])):
        opens, closes = int(hold["from_ms"]), int(hold["to_ms"])
        for position, capture in enumerate(satisfying):
            if claimed[position]:
                continue
            at = int(capture["at"])
            if opens <= at <= closes + CAPTURE_GRACE_MS:
                claimed[position] = True
                latencies.append(at - opens)
                if capture.get("outcome") == "accepted":
                    captured += 1
                else:
                    already_covered += 1
                break
    return {
        "holds": len(holds),
        "holds_satisfied": len(latencies),
        "holds_captured": captured,
        "holds_already_covered": already_covered,
        # The gate reads this one; it is the satisfied count, kept under its
        # original name so a scores.json from either round is comparable.
        "holds_with_capture": len(latencies),
        "latency_ms": {"p95": _percentile(latencies, 95),
                       "max": int(max(latencies)) if latencies else None},
        "accepted_frames": accepted_total,
    }


def _observable_mask(panorama_shape, camera: dict, frames: list) -> np.ndarray:
    """Which raster cells any delivered frame could have seen.

    A cell's direction is taken from ``c_ref`` and each frame's frustum from
    its truth attitude, ignoring parallax, as the definition of the observable
    region says. Identical attitudes are collapsed first: a still route holds
    the same pose for a dozen consecutive frames and they all see the same
    sky. The test is the pinhole one, ``u`` in ``[0, width]`` and ``v`` in
    ``[0, height]``, written without dividing by ``z``.
    """
    height, width = panorama_shape[:2]
    az, alt = _raster_grid(height, width)
    grid_az, grid_alt = np.meshgrid(np.radians(az), np.radians(alt))
    cos_alt = np.cos(grid_alt)
    cells = np.stack([np.sin(grid_az) * cos_alt,
                      np.cos(grid_az) * cos_alt,
                      np.sin(grid_alt)], axis=-1).reshape(-1, 3).astype(np.float32)

    covered = np.zeros(cells.shape[0], dtype=bool)
    if not frames:
        return covered.reshape(height, width)

    poses = np.unique(np.array(
        [list(f["right"]) + list(f["up"]) + list(f["forward"]) for f in frames],
        dtype=np.float64), axis=0).astype(np.float32)
    kx = float(camera["cx"]) / float(camera["fx"])
    ky = float(camera["cy"]) / float(camera["fy"])
    for start in range(0, poses.shape[0], 64):
        batch = poses[start:start + 64]
        z = cells @ batch[:, 6:9].T
        x = cells @ batch[:, 0:3].T
        y = cells @ batch[:, 3:6].T
        inside = (z > 0.0) & (np.abs(x) <= kx * z) & (np.abs(y) <= ky * z)
        covered |= inside.any(axis=1)
    return covered.reshape(height, width)


def _score_coverage(panorama: np.ndarray, camera: dict, frames: list,
                    summary: dict | None) -> dict:
    height, width = panorama.shape[:2]
    _, alt = _raster_grid(height, width)
    weight = np.repeat(np.cos(np.radians(alt))[:, None], width, axis=1)
    painted = panorama[:, :, 3] == 255

    total = float(weight.sum())
    alpha_fraction = float((weight * painted).sum() / total) if total else None

    observable = _observable_mask(panorama.shape, camera, frames)
    # Rows below altitude -10 are outside the observable region by definition;
    # the contract's raster stops exactly at -10, so this only ever matters if
    # the panorama is taller than the contract says.
    observable &= alt[:, None] >= -10.0
    observable_weight = float((weight * observable).sum())
    observable_fraction = (
        float((weight * observable * painted).sum() / observable_weight)
        if observable_weight else None)

    cells_fraction = None
    if summary and summary.get("cells_total"):
        cells_fraction = float(summary.get("cells_covered", 0)) / float(summary["cells_total"])

    return {
        "panorama_alpha_fraction": alpha_fraction,
        "observable_fraction_covered": observable_fraction,
        "cells_covered_fraction": cells_fraction,
    }


# --------------------------------------------------------------------------
# Gates
# --------------------------------------------------------------------------


def _below(value, threshold) -> bool:
    return value is not None and value < threshold


def _gates(landmarks: dict, horizon: dict, overlay: dict, capture: dict,
           coverage: dict) -> dict:
    # A percentile over an empty group is not a pass. Where evidence was
    # expected and none arrived, the gate fails; where a group is empty
    # because the route never produced one (a still case has no fast pan), the
    # gate is vacuously true. `samples == 0` tells the two apart.
    expected_landmarks = landmarks["expected"] > 0
    errors = landmarks["errors_deg"]
    worst_overlay = max((value for value in (overlay["settled"]["max_deg"],
                                             overlay["moving"]["max_deg"])
                         if value is not None), default=None)
    gates = {
        "landmarks_p95_lt_0_5": (_below(errors["p95"], GATE_LANDMARK_P95)
                                 if expected_landmarks else True),
        "landmarks_p99_lt_1": (_below(errors["p99"], GATE_LANDMARK_P99)
                               if expected_landmarks else True),
        "no_omissions": not landmarks["omitted"],
        "no_duplicates": not landmarks["duplicated"],
        "horizon_p95_lt_1": _below(horizon["signed_error_deg"]["p95"], GATE_HORIZON_P95),
        "no_missed_obstructions": not horizon["missed_obstructions"],
        "no_unresolved_boundary": horizon["unresolved_sr"] == 0.0,
        "overlay_settled_p95_lt_0_5": (
            overlay["samples"] > 0
            and (overlay["settled"]["p95_deg"] is None
                 or overlay["settled"]["p95_deg"] < GATE_OVERLAY_SETTLED_P95)),
        "overlay_moving_p95_lt_1": (
            overlay["samples"] > 0
            and (overlay["moving"]["p95_deg"] is None
                 or overlay["moving"]["p95_deg"] < GATE_OVERLAY_MOVING_P95)),
        # The one gate no percentile can reach. `frames_over_gate` stays
        # informational: it counts samples over their own class's p95
        # threshold, which is a diagnostic, while this is the limit past which
        # a single frame is a failing result.
        "overlay_max_lt_10": (overlay["samples"] > 0
                              and worst_overlay is not None
                              and worst_overlay < GATE_OVERLAY_MAX),
        "no_duplicate_frames": overlay["duplicate_frame_ids"] == 0,
        "capture_p95_le_1500": (
            capture["latency_ms"]["p95"] is not None
            and capture["latency_ms"]["p95"] <= GATE_CAPTURE_P95_MS
        ) if capture["holds"] else True,
        "every_hold_captured": capture["holds_with_capture"] == capture["holds"],
        "coverage_ge_0_95": (coverage["observable_fraction_covered"] is not None
                             and coverage["observable_fraction_covered"] >= GATE_COVERAGE),
    }
    gates["pass"] = all(gates.values())
    return gates


# --------------------------------------------------------------------------


def score_case(case_dir, result_dir=None) -> dict:
    """Score one result directory and write ``scores.json`` into it.

    ``result_dir`` defaults to ``case_dir / "result"``; Task 8's corruption
    tests point it at a copy instead, so nothing here assumes the result sits
    inside the case.
    """
    case_dir = Path(case_dir)
    result_dir = Path(result_dir) if result_dir is not None else case_dir / "result"
    result_dir.mkdir(parents=True, exist_ok=True)
    truth_dir = case_dir / "truth"

    manifest = _read_json(case_dir / "manifest.json", {}) or {}
    scene = _read_json(truth_dir / "scene.json", {}) or {}
    camera = _read_json(truth_dir / "camera.json")
    c_ref = np.asarray(_read_json(truth_dir / "reference.json")["c_ref"], dtype=np.float64)
    landmarks = _read_json(truth_dir / "landmarks.json", []) or []
    reference_horizon = _read_json(truth_dir / "reference-horizon.json")
    holds = _read_json(truth_dir / "holds.json", []) or []
    frames = _read_jsonl(truth_dir / "trajectory.jsonl")

    panorama, panorama_info = _load_panorama(result_dir / "panorama.png")
    summary = _read_json(result_dir / "summary.json")

    landmark_scores = _score_landmarks(scene, landmarks, c_ref, panorama)
    horizon_scores = _score_horizon(reference_horizon,
                                    _read_json(result_dir / "horizon.json"))
    overlay_scores = _score_overlay(_read_jsonl(result_dir / "events.jsonl"), frames)
    capture_scores = _score_capture(holds, _read_jsonl(result_dir / "captures.jsonl"))
    coverage_scores = _score_coverage(panorama, camera, frames, summary)

    case_id = manifest.get("case_id", case_dir.name)
    hashes = manifest.get("hashes", {})
    # The profile belongs to the case directory, which is the thing being
    # scored. Reaching into this checkout's ``cases/`` is the fallback for a
    # directory built before the manifest carried it, and it is a fallback
    # rather than the rule because the definition on this machine need not be
    # the definition the case was built from.
    profile = manifest.get("profile")
    if profile is None:
        definition = _read_json(CASES_DIR / f"{case_id}.json", {}) or {}
        profile = definition.get("profile")

    scores = {
        "schema": 1,
        "case_id": case_id,
        "input_hash": _combined_digest([hashes.get("frames", ""),
                                        hashes.get("observations", "")]),
        "app_commit": ((summary or {}).get("app_commit")
                       or manifest.get("versions", {}).get("app_commit")),
        "profile": profile,
        "panorama": panorama_info,
        "landmarks": landmark_scores,
        "horizon": horizon_scores,
        "overlay": overlay_scores,
        "capture": capture_scores,
        "coverage": coverage_scores,
        "gates": _gates(landmark_scores, horizon_scores, overlay_scores,
                        capture_scores, coverage_scores),
    }
    (result_dir / "scores.json").write_text(
        json.dumps(scores, separators=(",", ":")), encoding="utf-8", newline="\n")
    return scores
