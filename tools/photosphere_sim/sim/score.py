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

__all__ = ["score_case"]

#: The result panorama's shape, from CONTRACT.md's "Result directory".
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


def _load_panorama(path: Path) -> np.ndarray:
    """The result panorama as ``(H, W, 4)`` uint8.

    A panorama that was never written is a fully transparent one of the
    contract's size: the scanner painted nothing, which is a score, not an
    error.
    """
    if not path.is_file():
        return np.zeros((PANORAMA_HEIGHT, PANORAMA_WIDTH, 4), dtype=np.uint8)
    with Image.open(path) as image:
        return np.array(image.convert("RGBA"))


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
    """
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
        steradians = math.pi * radius * radius * facing / (distance * distance)
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
    omitted, duplicated, found = [], [], 0
    for landmark in landmarks:
        name = landmark["id"]
        hits = sorted(matches[name], key=lambda m: m["error_deg"])
        status = "found" if len(hits) == 1 else ("duplicate" if hits else "omitted")
        best = hits[0] if hits else None
        per_landmark.append({
            "id": name,
            "truth": {"az": landmark["az"], "alt": landmark["alt"]},
            "measured": None if best is None else {"az": best["az"], "alt": best["alt"]},
            "error_deg": None if best is None else best["error_deg"],
            "status": status,
        })
        # A landmark whose centre is occluded may still show a clipped sliver
        # of its disc. Nothing can be demanded of that sliver and its centroid
        # is not the landmark's direction, so it is neither counted nor
        # blamed: it is excluded here rather than left to inflate the errors.
        if not landmark["observable"]:
            continue
        if status == "found":
            found += 1
            errors.append(best["error_deg"])
        elif status == "duplicate":
            duplicated.append(name)
        else:
            omitted.append(name)

    return {
        "expected": sum(1 for lm in landmarks if lm["observable"]),
        "found": found,
        "omitted": omitted,
        "duplicated": duplicated,
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


def _span_mask(centres: np.ndarray, az_from: float, az_to: float) -> np.ndarray:
    if az_from <= az_to:
        return (centres >= az_from) & (centres < az_to)
    return (centres >= az_from) | (centres < az_to)


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

    missed = []
    for obstacle in reference.get("obstacles", []):
        if obstacle.get("az_from") is None or obstacle.get("az_to") is None:
            continue  # never the first thing hit from c_ref: nothing to find
        mask = _span_mask(centres, float(obstacle["az_from"]), float(obstacle["az_to"]))
        if not mask.any():
            continue
        # The obstacle's own alt_max is its peak at one azimuth; what it
        # guarantees across its whole span is the lowest truth altitude in the
        # span, and that is what the measured boundary must not fall below.
        # Comparing a per-span minimum against the peak would call every
        # obstacle that is not flat-topped missed, in the ideal result too.
        truth_alt = float(truth[mask].min())
        seen = mask & resolved
        measured_alt = float(alt[seen].min()) if seen.any() else None
        if measured_alt is None or measured_alt < truth_alt - MISSED_OBSTRUCTION_DEG:
            missed.append({"id": obstacle["id"], "truth_alt": truth_alt,
                           "measured_alt": measured_alt})

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
        "missed_obstructions": missed,
        "north_offset_deg": north_offset,
        "note": ("truth alt_max clipped to [0, 90]: the scanner's floor is 0, "
                 "and -10 in the truth means no ray hit anything"),
    }


# --------------------------------------------------------------------------
# Overlay, capture, coverage
# --------------------------------------------------------------------------


def _score_overlay(events: list, frames: list) -> dict:
    truth = {frame["frame_id"]: frame for frame in frames}
    moving, settled = [], []
    missing = 0
    for event in events:
        basis = event.get("basis")
        if basis is None:
            missing += 1
            continue
        frame = truth.get(event.get("frame_id"))
        if frame is None:
            continue
        error = angle_between(basis["forward"], frame["forward"])
        if float(frame["angular_rate_deg_s"]) > MOVING_RATE_DEG_S:
            moving.append(error)
        else:
            settled.append(error)
    return {
        "samples": len(moving) + len(settled),
        "missing_fraction": (missing / len(events)) if events else None,
        "moving": {"median_deg": _percentile(moving, 50),
                   "p95_deg": _percentile(moving, 95)},
        "settled": {"median_deg": _percentile(settled, 50),
                    "p95_deg": _percentile(settled, 95)},
    }


def _score_capture(holds: list, captures: list) -> dict:
    accepted = [c for c in captures if c.get("outcome") == "accepted"]
    latencies = []
    for hold in holds:
        opens, closes = int(hold["from_ms"]), int(hold["to_ms"])
        for capture in accepted:
            at = int(capture["at"])
            if opens <= at <= closes + CAPTURE_GRACE_MS:
                latencies.append(at - opens)
                break
    return {
        "holds": len(holds),
        "holds_with_capture": len(latencies),
        "latency_ms": {"p95": _percentile(latencies, 95),
                       "max": int(max(latencies)) if latencies else None},
        "accepted_frames": len(accepted),
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

    panorama = _load_panorama(result_dir / "panorama.png")
    summary = _read_json(result_dir / "summary.json")

    landmark_scores = _score_landmarks(scene, landmarks, c_ref, panorama)
    horizon_scores = _score_horizon(reference_horizon,
                                    _read_json(result_dir / "horizon.json"))
    overlay_scores = _score_overlay(_read_jsonl(result_dir / "events.jsonl"), frames)
    capture_scores = _score_capture(holds, _read_jsonl(result_dir / "captures.jsonl"))
    coverage_scores = _score_coverage(panorama, camera, frames, summary)

    case_id = manifest.get("case_id", case_dir.name)
    hashes = manifest.get("hashes", {})
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
