# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
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

Version 2 (CONTRACT.md "Version 2"). A case is scored the version 2 way when
its route is a pan, its ``horizon.json`` is version 2, or it carries a
``grading`` block or a ``visibility.json``; every other case takes the legacy
path below byte for byte and writes the same ``scores.json`` keys it always
did. The version 2 additions are the places a plausible-looking implementation
goes quietly wrong, so the reasons are kept beside the code:

- The polyline is graded over Measured bins only, and an EMPTY Measured set
  is not a clean result: a scanner that blocks everything has measured
  nothing, and 36 or more visible bins say there was something to measure.
- Coverage on a pan route is judged against the CLAIMED footprint (the slit
  the scanner paints), not the frustum of every frame. A perfect slit painter
  covers 0.9397 of the frustum, so the frustum rule cannot be passed by the
  design it is meant to grade.
- The overlay is graded after ONE global yaw is removed, because the scan
  frame's zero is arbitrary for the relative modes. Absolute yaw belongs to
  the north gates.
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
from .geometry import angle_between, sky_angles, sky_vector
from .palette import PALETTE
# The face-normal tolerance the truth paints a surface landmark with. Imported
# rather than copied: the expected-area model has to move with it.
from .truth import _NORMAL_DOT

__all__ = ["delivery_window", "first_line_per_frame", "footprint_pass",
           "global_yaw_deg", "matrix_to_quat", "quat_to_matrix", "rotate_about_up",
           "rotation_angle_deg", "score_case", "truth_matrix"]

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

# --- Version 2 (pan routes and the 720-bin profile), spec 7.6 -----------------

#: The published profile: 720 bins of half a degree. Bin ``i`` covers
#: ``[i / 2, (i + 1) / 2)`` degrees of azimuth.
PROFILE_BINS = 720
PROFILE_BIN_DEG = 360.0 / PROFILE_BINS
#: ``BinState`` of the scanner's ``HorizonDraft``, by value. Overhead is
#: reserved; Edited and Kept are bins a person or an earlier scan settled.
STATE_MEASURED, STATE_LOW, STATE_UNKNOWN, STATE_TALL = 0, 1, 2, 3
STATE_OVERHEAD, STATE_EDITED, STATE_KEPT = 4, 5, 6
STATE_NAMES = {0: "measured", 1: "low", 2: "unknown", 3: "tall",
               4: "overhead", 5: "edited", 6: "kept"}
#: ``no_unresolved_boundary`` fails on any of these: a bin the scanner blocked
#: because it could not say.
UNRESOLVED_STATES = (STATE_LOW, STATE_UNKNOWN, STATE_TALL, STATE_OVERHEAD)
#: ``measured_share`` counts these as a boundary the scanner stood behind.
SETTLED_STATES = (STATE_MEASURED, STATE_EDITED)

GATE_HORIZON_P99 = 2.0
#: A percentile gate is "below" its limit with this much float noise forgiven:
#: a polyline shifted exactly one degree reads 1.0 plus or minus the last bit
#: of an interpolation, and a verdict that depends on that bit is a coin toss.
GATE_EPSILON = 1e-9
#: The empty-Measured rule (RS M1): this many visible bins or more, and a
#: result with nothing Measured has failed rather than abstained.
EMPTY_MEASURED_VISIBLE_BINS = 36
GATE_MEASURED_SHARE = 0.85
GATE_MEASURED_AT_UNOBSERVABLE = 0.05
#: A boundary is only expected to be Measured when this much sky above it is
#: inside the footprint (spec 5.2: the top 6 degrees must match the sky model).
SKY_ABOVE_MEASURED_DEG = 6.0

#: The claimed footprint (RS B2): the slit a scanner paints from one frame.
#: Half-width across the short axis, and the fraction of the long half-axis.
FOOTPRINT_HALF_WIDTH_DEG = 3.0
FOOTPRINT_LONG_FRACTION = 0.9
#: The live-fill rule looks at the central part of the slit only.
LIVE_HALF_WIDTH_DEG = 2.0
GATE_LIVE_FILL_P95_MS = 1000
#: A cell the scan entered and the scanner never reported seen.
LIVE_FILL_NEVER_MS = 10000.0
#: ``first_seen.bin`` counts deciseconds since Begin.
FIRST_SEEN_UNIT_MS = 100.0

GATE_OVERLAY_COMPLETE = 0.95
GATE_LOOP_RESIDUAL_DEG = 0.25
GATE_FOCAL_ERR = 0.005
GATE_NORTH_ERR_DEG = 1.0
NORTH_SIGMA_FACTOR = 2.5
#: Expected landmarks on a pan route keep this much clear sky around the disc.
LANDMARK_FOOTPRINT_MARGIN_DEG = 1.5
#: The scene's own flags, read from ``manifest.grading``.
GRADING_FLAGS = ("still_pivot", "fully_observable", "daylight",
                 "north_graded", "expect_closure")


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
    than the disc, the expected area is scaled by the ratio of the two widths.
    That was written as "a lower bound on the clipped area"; measured against
    the paint (tests/test_sphere_disc_truth.py) it runs 0.4 to 1.2 per cent
    OVER, because the disc is measured along the curved surface. A 40 per cent
    filter does not feel that, but the model is close, not a bound. Without it the chart yard's
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
        row = {
            "id": name,
            "observable": observable,
            "truth": {"az": landmark["az"], "alt": landmark["alt"]},
            "measured": None if best is None else {"az": best["az"], "alt": best["alt"]},
            "error_deg": None if best is None else best["error_deg"],
            "expected_px": area / (cell * cell_alt),
            "status": status,
        }
        # Only a pan route narrows the expectation to the claimed footprint,
        # and says so: a legacy row keeps exactly the keys it always had.
        if "in_footprint" in landmark:
            row["in_footprint"] = bool(landmark["in_footprint"])
        per_landmark.append(row)
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


def _longest_run_deg(under: np.ndarray, step: float) -> float:
    """The widest CONTIGUOUS stretch of azimuth ``under`` is true over.

    Azimuth wraps, so a stretch running through north is ONE stretch and not
    two: an obstacle spanning 350 to 10 degrees that is lost over all 20 of
    them has lost a 20 degree stretch, and reporting two runs of 10 would let
    the obstacle's own position in the array decide its verdict.

    A bin that is not under-reported breaks the run, and so does a bin with no
    resolved measurement over it (``under`` is already false there): an
    unresolved bin is not evidence of a miss, and joining two stretches across
    one would claim a width nothing measured. That is the conservative
    direction for a term that fails a case.
    """
    if not under.any():
        return 0.0
    if under.all():
        return float(under.size) * step
    # Rotating the array so that it begins at a gap turns the circular
    # problem into a linear one: every run is then interior and none is split
    # across the ends.
    rolled = np.roll(under, -int(np.argmin(under)))
    best = run = 0
    for value in rolled:
        run = run + 1 if value else 0
        if run > best:
            best = run
    return float(best) * step


#: THE NARROWEST OBSTRUCTION THE PLANNER IS REQUIRED TO HONOUR, in degrees of
#: azimuth (issue #53). The owner's ruling, 2026-09-23: "the distance one could
#: reasonably put two of the dots on the horizon editor" - an obstruction
#: narrower than a person can draw is one no horizon can carry, scanned or not.
#: Derived from the editor's finest setting, the photo review at full zoom:
#: the strip is 1040 px x zoom wide for 360 degrees, zoom tops out at 4, and a
#: tap within 18 px of a dot grabs it rather than adding one (the editor's
#: REVIEW_* constants). 18 / (1040 * 4 / 360) = 1.5577 degrees. The UI suite's
#: horizonEditorFloor test reads this line against those constants - that
#: direction, because this package must never read production code.
EDITOR_MIN_WIDTH_DEG = 18.0 / (1040.0 * 4.0 / 360.0)


def _score_obstacles(reference: dict, truth_bins: int, step: float,
                     alt: np.ndarray, resolved: np.ndarray,
                     product_bins: int, expected: np.ndarray | None = None) -> list:
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
    - ``width_missed_deg`` at or above ``resolvable_width_deg``, the wider of
      the scene's declared ``min_width_deg`` and one product bin
      (``360 / product_bins``, ``product_bins`` being the number of points
      the scanner's own boundary carries in this result). The median cannot
      see this one. The chart yard's roof spans 146 degrees, so a 10 degree
      notch cut out of it leaves 1366 of 1466 bins right and the median at
      zero, while the whole of the declared minimum width is gone. An
      obstacle is found when it is found, not when most of it is.

    Issue #64: the width term is keyed on the longest contiguous
    under-reported RUN, not on the total of every under-reported bin.
    CONTRACT.md has always stated the rule as "a stretch at least this wide",
    and a stretch is what the planner would meet: the product's profile is
    interpolated between neighbouring azimuth bins, so a stretch wide enough
    to cross is a false-open the planner would slew into, while a scatter of
    tenth-degree bins along a roof edge is jitter at the silhouette, a
    different defect that must not be able to masquerade as the first. The
    two are reported apart -- ``width_missed_deg`` is the longest run and
    ``width_missed_total_deg`` the sum of every under-reported bin -- so the
    jitter is still visible in the score, it just does not decide the
    verdict. Measured on the cached cases at `322e374d`: chartyard-arc075-60's
    roof-south is 41.8 degrees of total over runs of 13.3, 10.2, 7.2, 5.7 and
    5.4, and stays MISSED on its longest run alone; chartyard-still-60's is
    2.6 of total in two runs of 1.3, and was already found.

    Issue #53: the scanner reports 30 azimuth bins, 12 degrees each, and the
    planner interpolates that same resolution, so a stretch of an obstacle
    narrower than one product bin cannot be represented by the product at
    all, whatever the scanner does. Fix round 1 keyed ``resolvable`` on the
    scene's DECLARED ``min_width_deg``, which was wrong: several chart-yard
    obstacles declare a small width as a label (roof-south and wall-east both
    say 10) while their own silhouette is far wider than one bin, and an
    obstacle that is actually wide enough for the product to see is
    resolvable regardless of what number the scene happens to have written
    next to it. What decides resolvability is the obstacle's own visible
    extent -- ``visible_width_deg``, the count of truth bins where it is the
    first thing hit, times the truth's own step -- not the declared value:
    ``resolvable = visible_width_deg >= 360 / product_bins``. The declared
    value still sets the verdict's WIDTH THRESHOLD once an obstacle is
    resolvable (``resolvable_width_deg = max(min_width_deg, 360 /
    product_bins)``), because a resolvable obstacle can still be held to a
    wider minimum than one bin if the scene asks for it; it just cannot be
    the thing that makes an otherwise-wide obstacle unresolvable. An
    obstacle whose own silhouette never reaches one product bin (the chart
    yard's two poles and its trunk, all under 3.2 degrees wide against a 12
    degree bin) is ``resolvable: false``: both verdict terms are suppressed
    for it and its deficit numbers -- and ``visible_width_deg`` and
    ``resolvable_width_deg`` themselves -- are reported for information
    only, never folded into ``missed`` or ``missed_obstructions``.

    The width term, and the wider resolvable-width floor, cost a correct
    boundary nothing: the ideal's measured profile is at or above the
    envelope everywhere, so every deficit is at or below zero and every
    missed width is 0.0, at 3600 bins and at 30.

    ``product_bins`` is 0 when the result carries no boundary at all -- no
    ``result/horizon.json``, or one with an empty ``points`` array. There is
    then no product resolution to defer to, so nothing is excused: every
    obstacle is ``resolvable``, ``resolvable_width_deg`` falls back to the
    scene's declared ``min_width_deg`` (``None`` where the scene declares
    none), and every VISIBLE obstacle is ``missed`` because nothing was
    resolved over it. A scanner that reported no boundary has not found the
    obstacles; suppressing the verdict for want of a bin width would let a
    silent scanner score better than a wrong one.

    ``expected`` is the version 2 addition: a boolean mask over the truth bins
    saying where a scanner could be asked to have measured anything. An
    obstacle is only visible, and so only expected, where its bins are inside
    it; one that lies wholly in the dark, or wholly above the footprint, has
    nothing to miss. ``None`` is every bin, which is the legacy rule.
    """
    bin_width_deg = (360.0 / product_bins) if product_bins else None
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
        if expected is not None:
            visible = visible & expected
        measurable = visible & resolved
        visible_width_deg = float(np.count_nonzero(visible)) * step

        declared = obstacle.get("min_width_deg")
        declared_val = float(declared) if declared is not None else None
        # Issue #53: the declared label is floored at what the editor can
        # represent. A label under it (the chart yard's pole-far, 0.25) asks
        # the scan for something no horizon can carry.
        if declared_val is not None:
            declared_val = max(declared_val, EDITOR_MIN_WIDTH_DEG)
        has_min_width = declared_val is not None and declared_val > 0.0

        if bin_width_deg is not None:
            # The obstacle's OWN silhouette decides resolvability, never the
            # declared label: a wide obstacle the scene under-labels (the
            # chart yard's roof and wall both declare 10 while spanning
            # 146 and 84 degrees) is exactly as resolvable as one correctly
            # labelled. Dropping this comparison (treating every obstacle as
            # unresolvable, or every obstacle as resolvable) is the named
            # mutation `tests/test_score.py`'s `ResolvableWidth` class pins.
            resolvable = visible_width_deg >= bin_width_deg
            resolvable_width_deg = max(declared_val, bin_width_deg) if has_min_width else bin_width_deg
        else:
            resolvable = True
            resolvable_width_deg = declared_val

        entry = {
            "id": obstacle["id"],
            "truth_alt_peak": float(obstacle.get("alt_max", -10.0)),
            "deficit_median": None,
            "deficit_p95": None,
            "width_missed_deg": None,
            "width_missed_total_deg": None,
            "min_width_deg": declared,
            "visible_width_deg": visible_width_deg,
            "resolvable": resolvable,
            "resolvable_width_deg": resolvable_width_deg,
            # Visible but with nothing resolved over it is a miss: no evidence
            # where evidence was expected. Suppressed the same way when the
            # obstacle's own silhouette is narrower than the product can ever
            # resolve.
            "missed": bool(resolvable and visible.any()),
        }
        if measurable.any():
            deficit = np.clip(profile[measurable], 0.0, 90.0) - alt[measurable]
            median = float(np.median(deficit))
            # Back on the truth's own azimuth axis, because a RUN is a fact
            # about neighbouring azimuths and the compressed `deficit` array
            # has already closed every gap the obstacle has.
            under = np.zeros(truth_bins, dtype=bool)
            under[measurable] = deficit > MISSED_OBSTRUCTION_DEG
            width_missed_total = float(np.count_nonzero(under) * step)
            width_missed = _longest_run_deg(under, step)
            # No `has_min_width` guard here: CONTRACT.md's documented formula
            # is `resolvable and width_missed_deg >= resolvable_width_deg`,
            # full stop. `resolvable_width_deg` is already well-defined with
            # no declared `min_width_deg` at all (it falls back to
            # `bin_width_deg` above), so a resolvable obstacle with nothing
            # declared can still be caught by the width term at one product
            # bin -- an extra guard here would silently exempt it instead.
            # Issue #64: the comparison is against the longest RUN and never
            # against `width_missed_total`, which is reported beside it.
            too_narrow = (resolvable and resolvable_width_deg is not None
                          and width_missed >= resolvable_width_deg)
            entry.update({
                "deficit_median": median,
                "deficit_p95": _percentile(deficit, 95),
                "width_missed_deg": width_missed,
                "width_missed_total_deg": width_missed_total,
                "missed": bool(resolvable and (median > MISSED_OBSTRUCTION_DEG or too_narrow)),
            })
        elif not visible.any():
            entry["width_missed_deg"] = 0.0
            entry["width_missed_total_deg"] = 0.0
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

    # `bins` is `_measured_profile`'s own count of points in the result's
    # horizon.json -- the number of azimuth columns THIS result actually
    # carries, whatever the scanner that produced it used. That is
    # `product_bins`: the resolution the obstacle rule's width term must
    # respect, not the case's or the truth's own bin count.
    obstacles = _score_obstacles(reference, truth_bins, step, alt, resolved, bins)
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


def rotate_about_up(vector, deg: float) -> list:
    """Turn a world vector east by ``deg`` degrees about the vertical.

    Azimuth runs clockwise from north, so adding to the azimuth of
    ``[sin az cos alt, cos az cos alt, sin alt]`` is
    ``x' = x cos d + y sin d``, ``y' = y cos d - x sin d``. The scorer removes
    a global yaw with it and `sim.corrupt` puts one in, so there is one copy.
    """
    radians = math.radians(deg)
    cos, sin = math.cos(radians), math.sin(radians)
    x, y, z = (float(v) for v in vector)
    return [x * cos + y * sin, y * cos - x * sin, z]


def _heading_deg(vector) -> float | None:
    """Azimuth of a world direction, or ``None`` when it is vertical."""
    x, y = float(vector[0]), float(vector[1])
    if math.hypot(x, y) < 1e-9:
        return None
    return math.degrees(math.atan2(x, y))


def global_yaw_deg(events: list, truth: dict) -> float:
    """The one yaw that best explains reported against true forward heading.

    The circular mean of reported-minus-truth heading over every delivered
    frame that carries a basis (RS B3). The scan frame's zero is arbitrary for
    the relative predictor modes, so a result turned as a whole is not wrong
    about where the camera pointed relative to itself; where north is, the
    north gates grade. A mean of the raw differences would call 359 and 1
    degrees 180 apart, which is why it is taken on the circle.
    """
    sin_sum = cos_sum = 0.0
    count = 0
    for event in first_line_per_frame(events):
        basis, frame = event.get("basis"), truth.get(event.get("frame_id"))
        if basis is None or frame is None:
            continue
        reported, actual = _heading_deg(basis["forward"]), _heading_deg(frame["forward"])
        if reported is None or actual is None:
            continue
        difference = math.radians(reported - actual)
        sin_sum += math.sin(difference)
        cos_sum += math.cos(difference)
        count += 1
    if not count:
        return 0.0
    return math.degrees(math.atan2(sin_sum, cos_sum))


def _score_overlay(events: list, frames: list, remove_yaw: bool = False,
                   window_ids: set | None = None) -> dict:
    """Overlay attitude error per delivered frame, moving and settled apart.

    ``remove_yaw`` and ``window_ids`` are the pan-route additions (spec 7.6).
    With ``remove_yaw`` one global yaw (:func:`global_yaw_deg`) is taken out
    of every reported basis before it is compared, and the block gains
    ``yaw_removed_deg``. ``window_ids`` are the frame ids delivered between
    Begin and Finish: the block gains ``frames_in_window``,
    ``frames_with_basis`` and ``complete_fraction``, the share of those frames
    the scanner put a pose on (#898). The denominator is the frames delivered,
    never the lines the scanner wrote: a scanner that stops writing lines has
    not shrunk the denominator.

    Issue #59: the error is the MAXIMUM of the three angles between measured
    and true `forward`, `right` and `up`, not `forward` alone. `forward`
    alone is blind to roll: a basis whose `right` and `up` are rotated about
    `forward` by any amount, with `forward` itself left untouched, is a
    perfect match under a forward-only metric and is exactly the corruption
    `sim.corrupt`'s `roll` demonstrates. `max_forward_deg`, `max_right_deg`
    and `max_up_deg` report each axis's own worst angle beside the combined
    figure, because the combined maximum alone does not say which axis moved.

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

    yaw = global_yaw_deg(events, truth) if remove_yaw else 0.0
    posed_ids = set()

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
        posed_ids.add(frame_id)
        if remove_yaw:
            basis = {key: rotate_about_up(basis[key], -yaw)
                     for key in ("right", "up", "forward")}
        forward_error = angle_between(basis["forward"], frame["forward"])
        right_error = angle_between(basis["right"], frame["right"])
        up_error = angle_between(basis["up"], frame["up"])
        sample = {
            "error": max(forward_error, right_error, up_error),
            "forward": forward_error,
            "right": right_error,
            "up": up_error,
        }
        if float(frame["angular_rate_deg_s"]) > MOVING_RATE_DEG_S:
            moving.append(sample)
        else:
            settled.append(sample)

    def block(samples):
        errors = [s["error"] for s in samples]
        return {
            "median_deg": _percentile(errors, 50),
            "p95_deg": _percentile(errors, 95),
            "max_deg": float(max(errors)) if errors else None,
            "max_forward_deg": float(max(s["forward"] for s in samples)) if samples else None,
            "max_right_deg": float(max(s["right"] for s in samples)) if samples else None,
            "max_up_deg": float(max(s["up"] for s in samples)) if samples else None,
        }

    over_gate = (sum(1 for s in moving if s["error"] > GATE_OVERLAY_MOVING_P95)
                 + sum(1 for s in settled if s["error"] > GATE_OVERLAY_SETTLED_P95))
    result = {
        "samples": len(moving) + len(settled),
        "missing_fraction": (missing / considered) if considered else None,
        "frames_over_gate": over_gate,
        "duplicate_frame_ids": duplicate_ids,
        "moving": block(moving),
        "settled": block(settled),
    }
    if remove_yaw:
        result["yaw_removed_deg"] = yaw
    if window_ids is not None:
        with_basis = len(posed_ids & window_ids)
        result["frames_in_window"] = len(window_ids)
        result["frames_with_basis"] = with_basis
        result["complete_fraction"] = (with_basis / len(window_ids)) if window_ids else None
    return result


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
# Version 2: the pan route's footprint, the profile and the diagnostics
# --------------------------------------------------------------------------


def delivery_window(case_dir) -> tuple[float, float]:
    """The ``(begin, finish)`` times, in ms, of the actions the replay was given.

    From ``input/actions.jsonl``, which is the case's own record of when the
    user pressed Begin and Finish, and not from anything the scanner reports
    about itself: a scanner that began late or finished early must not get to
    shrink the set of frames it is asked about. A case with no actions file
    (a hand-built one) is every frame.
    """
    actions = _read_jsonl(Path(case_dir) / "input" / "actions.jsonl")
    begin = next((a["t_ms"] for a in actions if a.get("action") == "begin"), None)
    finish = next((a["t_ms"] for a in actions if a.get("action") == "finish"), None)
    return (float("-inf") if begin is None else float(begin),
            float("inf") if finish is None else float(finish))


def footprint_pass(shape, camera: dict, frames: list,
                   first_half_width_deg: float = LIVE_HALF_WIDTH_DEG):
    """The claimed footprint region, and when each cell was first inside a slit.

    Returns ``(region, first_index)``, both shaped like the raster. A frame's
    claimed footprint (RS B2) is the directions in front of the camera whose
    camera-frame coordinates under the truth pose satisfy ``|x / z| <= tan 3``
    degrees and ``|y / z| <= 0.9 tan(long / 2)``: ``y`` is the long axis of
    the portrait image, so its half-extent is ``cy / fy``. ``region`` is the
    union of those over ``frames``. ``first_index`` is, per cell, the index in
    ``frames`` of the first frame whose slit narrowed to ``first_half_width_deg``
    contains it, or -1: the live-fill gate times the central part of the slit
    only, and ``sim.ideal`` times the whole claimed slit. The test is written
    without dividing by ``z``, and parallax is ignored, as for the legacy
    observable region.

    ``frames`` must be in delivery order. Poses are NOT collapsed here, since
    the order is the answer to "first".
    """
    height, width = shape[:2]
    az, alt = _raster_grid(height, width)
    grid_az, grid_alt = np.meshgrid(np.radians(az), np.radians(alt))
    cos_alt = np.cos(grid_alt)
    cells = np.stack([np.sin(grid_az) * cos_alt,
                      np.cos(grid_az) * cos_alt,
                      np.sin(grid_alt)], axis=-1).reshape(-1, 3).astype(np.float32)

    region = np.zeros(cells.shape[0], dtype=bool)
    first = np.full(cells.shape[0], -1, dtype=np.int64)
    if not frames:
        return region.reshape(height, width), first.reshape(height, width)

    poses = np.array([list(f["right"]) + list(f["up"]) + list(f["forward"])
                      for f in frames], dtype=np.float32)
    k_claimed = math.tan(math.radians(FOOTPRINT_HALF_WIDTH_DEG))
    k_first = math.tan(math.radians(first_half_width_deg))
    k_long = FOOTPRINT_LONG_FRACTION * float(camera["cy"]) / float(camera["fy"])
    for start in range(0, poses.shape[0], 32):
        batch = poses[start:start + 32]
        z = cells @ batch[:, 6:9].T
        x = np.abs(cells @ batch[:, 0:3].T)
        y = np.abs(cells @ batch[:, 3:6].T)
        along = (z > 0.0) & (y <= k_long * z)
        region |= (along & (x <= k_claimed * z)).any(axis=1)
        inside = along & (x <= k_first * z)
        hit = inside.any(axis=1) & (first < 0)
        first[hit] = start + inside[hit].argmax(axis=1)
    return region.reshape(height, width), first.reshape(height, width)


def quat_to_matrix(q) -> np.ndarray:
    """The rotation matrix of a ``[w, x, y, z]`` quaternion, camera to world.

    The camera frame is x right, y up, looking along -z, so the columns are
    ``right``, ``up`` and ``-forward`` (CONTRACT.md "Frames and units").
    """
    w, x, y, z = (float(v) for v in q)
    norm = math.sqrt(w * w + x * x + y * y + z * z)
    if norm == 0.0:
        raise ValueError("the zero quaternion is not a rotation")
    w, x, y, z = w / norm, x / norm, y / norm, z / norm
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ])


def matrix_to_quat(matrix) -> list:
    """The ``[w, x, y, z]`` quaternion of a rotation matrix, ``w >= 0``."""
    m = np.asarray(matrix, dtype=np.float64)
    trace = float(m[0, 0] + m[1, 1] + m[2, 2])
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        q = [0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s,
             (m[1, 0] - m[0, 1]) / s]
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2.0
        q = [(m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s,
             (m[0, 2] + m[2, 0]) / s]
    elif m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2.0
        q = [(m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s,
             (m[1, 2] + m[2, 1]) / s]
    else:
        s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2.0
        q = [(m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s,
             (m[1, 2] + m[2, 1]) / s, 0.25 * s]
    norm = math.sqrt(sum(float(v) * float(v) for v in q))
    q = [float(v) / norm for v in q]
    return q if q[0] >= 0.0 else [-v for v in q]


def truth_matrix(frame: dict) -> np.ndarray:
    """A trajectory frame's camera-to-world rotation, ``[right, up, -forward]``."""
    return np.column_stack([np.asarray(frame["right"], dtype=np.float64),
                            np.asarray(frame["up"], dtype=np.float64),
                            -np.asarray(frame["forward"], dtype=np.float64)])


def rotation_angle_deg(matrix) -> float:
    """The angle of a rotation matrix in degrees.

    ``atan2`` of the sine and cosine rather than ``acos`` of the cosine alone:
    near zero ``acos`` has no precision left, and the loop gate lives at a
    quarter of a degree where an ideal result reads zero.
    """
    m = np.asarray(matrix, dtype=np.float64)
    sine = 0.5 * float(np.linalg.norm([m[2, 1] - m[1, 2], m[0, 2] - m[2, 0],
                                       m[1, 0] - m[0, 1]]))
    cosine = 0.5 * (float(m[0, 0] + m[1, 1] + m[2, 2]) - 1.0)
    return math.degrees(math.atan2(sine, cosine))


def _floats(values, size: int):
    """``values`` as a float64 vector of ``size`` with ``None`` as NaN, or ``None``."""
    if not isinstance(values, list) or len(values) != size:
        return None
    try:
        return np.array([math.nan if v is None else float(v) for v in values],
                        dtype=np.float64)
    except (TypeError, ValueError):
        return None


def _parse_horizon_v2(raw: dict) -> dict:
    """The version 2 ``horizon.json`` as arrays.

    A file that does not parse (a missing array, a wrong length) is an EMPTY
    boundary rather than an exception: every bin Unknown at 90, no polyline,
    and ``note`` says what was wrong. Silence is not a flat horizon, and a
    scanner that wrote something unreadable has measured nothing.
    """
    profile = _floats(raw.get("profile"), PROFILE_BINS)
    traced = _floats(raw.get("profile_traced"), PROFILE_BINS)
    state = _floats(raw.get("profile_state"), PROFILE_BINS)
    points = []
    for point in raw.get("points") or []:
        try:
            points.append((float(point["az"]) % 360.0, float(point["alt"])))
        except (KeyError, TypeError, ValueError):
            points = None
            break
    if profile is None or traced is None or state is None or points is None:
        note = ("horizon.json version 2 is unreadable (profile, profile_traced, "
                f"profile_state of {PROFILE_BINS} entries and a points list "
                "of az and alt are required): scored as an empty boundary")
        return {"profile": np.full(PROFILE_BINS, 90.0),
                "traced": np.full(PROFILE_BINS, math.nan),
                "state": np.full(PROFILE_BINS, STATE_UNKNOWN, dtype=np.int64),
                "points": [], "tau": None, "note": note}
    return {"profile": profile, "traced": traced,
            "state": np.nan_to_num(state, nan=STATE_UNKNOWN).astype(np.int64),
            "points": points, "tau": raw.get("tau"), "note": None}


def _polyline_alt(points: list, az) -> np.ndarray | None:
    """The published polyline at ``az``: linear between points, wrapped at 360.

    The same reading the planner gives a stored horizon. ``None`` for a
    polyline with no points, which has no altitude anywhere.
    """
    if not points:
        return None
    ordered = sorted(points)
    return np.interp(np.asarray(az, dtype=np.float64) % 360.0,
                     [p[0] for p in ordered], [p[1] for p in ordered],
                     period=360.0)


def _parse_visibility(raw: dict | None):
    """``(visible, footprint_top)`` as 720-long arrays, or ``None`` without a file."""
    if not raw:
        return None
    visible = raw.get("visible")
    top = _floats(raw.get("footprint_top_deg"), PROFILE_BINS)
    if not isinstance(visible, list) or len(visible) != PROFILE_BINS or top is None:
        raise ValueError("visibility.json needs 720 visible flags and 720 "
                         "footprint_top_deg values")
    return np.array([bool(v) for v in visible], dtype=bool), top


def _truth_bin_boundary(truth: np.ndarray, owner: np.ndarray) -> np.ndarray:
    """A, the maximum of the 0.1-degree truth over each of the 720 bins."""
    boundary = np.zeros(PROFILE_BINS, dtype=np.float64)
    np.maximum.at(boundary, owner, truth)
    return boundary


def _score_horizon_v2(reference: dict, parsed: dict, visibility) -> dict:
    """The version 2 boundary: the polyline over Measured bins, and the profile.

    The polyline is sampled at the truth's own 0.1-degree azimuths and graded
    only where the bin under it is Measured: a bin the scanner blocked at 90
    because it could not say is not a measurement, and grading the polyline
    there would punish the honesty the profile exists to express.

    ``expected`` is the bins a perfect scanner is asked to have Measured:
    visible, with the boundary plus 6 degrees inside the footprint top. It
    feeds ``measured_share`` and decides which test obstacles can be missed.
    """
    truth_raw = np.asarray(reference["alt_max"], dtype=np.float64)
    truth_bins = int(reference.get("bins", truth_raw.size))
    truth = np.clip(truth_raw, 0.0, 90.0)
    step = 360.0 / truth_bins
    centres = (np.arange(truth_bins) + 0.5) * step
    owner = np.minimum((centres / PROFILE_BIN_DEG).astype(np.int64), PROFILE_BINS - 1)
    state = parsed["state"]

    polyline = _polyline_alt(parsed["points"], centres)
    resolved = (state == STATE_MEASURED)[owner]
    if polyline is None:
        # No polyline means no altitude anywhere: nothing is graded as
        # measured, and the profile check below cannot pass.
        alt = np.zeros(truth_bins)
        resolved = np.zeros(truth_bins, dtype=bool)
    else:
        alt = polyline

    visible = top = None
    if visibility is not None:
        visible, top = visibility
    boundary = _truth_bin_boundary(truth, owner)
    expected = None
    if visible is not None:
        expected = (visible & np.isfinite(top)
                    & (boundary + SKY_ABOVE_MEASURED_DEG <= top))

    signed = alt[resolved] - truth[resolved]
    absolute = np.abs(signed)

    cell = math.radians(HORIZON_CELL_DEG)
    alt_centres = np.arange(0.0, 90.0, HORIZON_CELL_DEG) + HORIZON_CELL_DEG / 2.0
    cos_cumulative = np.concatenate(
        [[0.0], np.cumsum(np.cos(np.radians(alt_centres)) * cell * cell)])
    column_sr = float(cos_cumulative[-1])
    below_truth = _area_below(cos_cumulative, alt_centres, truth[resolved])
    below_measured = _area_below(cos_cumulative, alt_centres, alt[resolved])
    difference = below_truth - below_measured
    unresolved_samples = np.isin(state, UNRESOLVED_STATES)[owner]

    # Rounded to 1e-9 degree before the obstacle rule: a deficit of exactly one
    # degree must not depend on the last bit of an interpolation.
    obstacles = _score_obstacles(
        reference, truth_bins, step, np.round(alt, 9), resolved, PROFILE_BINS,
        expected=None if expected is None else expected[owner])
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

    # G1 at the bin edges, and at any vertex inside a bin: between vertices the
    # line is linear, so its lowest point over a bin is at one of those.
    edges = np.arange(PROFILE_BINS) * PROFILE_BIN_DEG
    if polyline is None:
        below = PROFILE_BINS
    else:
        profile = parsed["profile"]
        left = _polyline_alt(parsed["points"], edges)
        right = _polyline_alt(parsed["points"], edges + PROFILE_BIN_DEG)
        low = (left < profile - 1e-6) | (right < profile - 1e-6)
        for point_az, point_alt in parsed["points"]:
            bin_index = min(int(point_az / PROFILE_BIN_DEG), PROFILE_BINS - 1)
            if point_alt < profile[bin_index] - 1e-6:
                low[bin_index] = True
        below = int(low.sum())

    counts = {STATE_NAMES[code]: int(np.count_nonzero(state == code))
              for code in STATE_NAMES}
    share = unobservable = None
    if expected is not None:
        wanted = int(expected.sum())
        settled = int((expected & np.isin(state, SETTLED_STATES)).sum())
        share = {"expected": wanted, "settled": settled,
                 "share": (settled / wanted) if wanted else None}
        dark = ~visible
        dark_total = int(dark.sum())
        dark_measured = int((dark & (state == STATE_MEASURED)).sum())
        unobservable = {"not_visible": dark_total, "measured": dark_measured,
                        "share": (dark_measured / dark_total) if dark_total else None}

    return {
        "version": 2,
        "truth_bins": truth_bins,
        "measured_bins": PROFILE_BINS,
        "measured_resolution_deg": PROFILE_BIN_DEG,
        "signed_error_deg": {
            "median": float(np.median(signed)) if signed.size else None,
            "p95": _percentile(absolute, 95),
            "p99": _percentile(absolute, 99),
            "max": float(absolute.max()) if absolute.size else None,
        },
        "error_samples": int(absolute.size),
        "empty_measured": not bool(resolved.any()),
        "visible_bins": None if visible is None else int(visible.sum()),
        "states": counts,
        "unresolved_bins": int(np.isin(state, UNRESOLVED_STATES).sum()),
        "below_profile_bins": below,
        "polyline_points": len(parsed["points"]),
        "tau": parsed["tau"],
        "measured_share": share,
        "unknown_where_unobservable": unobservable,
        "false_open_sr": float(np.clip(difference, 0.0, None).sum()),
        "false_blocked_sr": float(np.clip(-difference, 0.0, None).sum()),
        "unresolved_sr": float(np.count_nonzero(unresolved_samples) * column_sr),
        "obstacles": obstacles,
        "missed_obstructions": missed,
        "north_offset_deg": north_offset,
        "note": parsed["note"] or (
            "truth alt_max clipped to [0, 90]; the polyline is graded at the "
            "truth's own azimuths over Measured bins only"),
    }


def _landmark_footprint(landmarks: list, scene: dict, c_ref: np.ndarray,
                        region: np.ndarray) -> list:
    """Landmarks with ``observable`` narrowed to the claimed footprint (7.6).

    A slit painter paints a band, so a landmark outside the band is not
    omitted, it was never asked for. Expected is: observable from ``c_ref``
    AND the whole disc, grown by 1.5 degrees, inside the footprint region. The
    region comes from the route's frusta and the truth poses, never from the
    scanner's alpha: a scanner must not be able to narrow its own exam. Each
    entry gains ``in_footprint`` so a report can say why a landmark is not
    expected.
    """
    radii = {lm["id"]: float(lm["radius_deg"]) for lm in scene.get("landmarks", [])}
    for lm in scene.get("surface_landmarks", []):
        distance = float(np.linalg.norm(np.asarray(lm["centre"], dtype=np.float64) - c_ref))
        radii[lm["id"]] = math.degrees(math.atan2(float(lm["radius_m"]), distance))
    height, width = region.shape
    angles = np.linspace(0.0, 2.0 * math.pi, 24, endpoint=False)

    def inside(az: float, alt: float, radius: float) -> bool:
        centre = sky_vector(az, alt)
        helper = (np.array([0.0, 0.0, 1.0]) if abs(centre[2]) < 0.99
                  else np.array([1.0, 0.0, 0.0]))
        u = np.cross(centre, helper)
        u /= np.linalg.norm(u)
        v = np.cross(centre, u)
        rho = math.radians(radius)
        probes = [centre] + [math.cos(rho) * centre
                             + math.sin(rho) * (math.cos(t) * u + math.sin(t) * v)
                             for t in angles]
        for probe in probes:
            probe_az, probe_alt = sky_angles(probe)
            row = int(round((PANORAMA_ALT_TOP - probe_alt) / PANORAMA_ALT_SPAN
                            * max(height - 1, 1)))
            column = int(probe_az / 360.0 * width) % width
            if not 0 <= row < height or not region[row, column]:
                return False
        return True

    narrowed = []
    for landmark in landmarks:
        radius = radii.get(landmark["id"], 0.0) + LANDMARK_FOOTPRINT_MARGIN_DEG
        covered = inside(float(landmark["az"]), float(landmark["alt"]), radius)
        entry = dict(landmark)
        entry["in_footprint"] = covered
        entry["observable"] = bool(landmark["observable"]) and covered
        narrowed.append(entry)
    return narrowed


def _heading_of_quat(q) -> float:
    """The azimuth of the optical axis of a camera-to-world quaternion."""
    forward = -quat_to_matrix(q)[:, 2]
    return math.degrees(math.atan2(forward[0], forward[1]))


def _score_loop(diagnostics: dict | None, truth: dict) -> dict:
    """The loop-closure residual against the truth poses (RS m4).

    ``qinv(q_early) q_late`` from the keyframes ``loop.match`` names, against
    the same relative rotation between their truth poses, as the angle of the
    rotation that carries one onto the other. A relative rotation is
    unchanged by any yaw applied to the whole world, so a result with the
    wrong north is not charged for it here. Closure by the gyro, or no
    closure, has no image match to measure and is reported as such.
    """
    loop = (diagnostics or {}).get("loop") or {}
    block = {"closed": bool(loop.get("closed")), "method": loop.get("method"),
             "match": loop.get("match"), "residual_deg": None, "note": None}
    match = loop.get("match")
    if not match:
        return block
    try:
        keyframes = {int(k["id"]): k for k in diagnostics.get("keyframes") or []}
        early, late = keyframes[int(match["early_kf"])], keyframes[int(match["late_kf"])]
        reported = quat_to_matrix(early["q"]).T @ quat_to_matrix(late["q"])
        actual = truth_matrix(truth[early["frame_id"]]).T @ truth_matrix(truth[late["frame_id"]])
        block["residual_deg"] = rotation_angle_deg(reported.T @ actual)
    except (KeyError, TypeError, ValueError) as error:
        block["note"] = f"loop.match could not be resolved to truth poses: {error!r}"
    return block


def _score_focal(diagnostics: dict | None, camera: dict) -> dict:
    """``|f_norm x short px - truth fx| / truth fx``, as a fraction."""
    focal = (diagnostics or {}).get("focal") or {}
    short_px = float(min(camera["width"], camera["height"]))
    block = {"f_norm": focal.get("f_norm"), "short_px": short_px,
             "truth_fx": float(camera["fx"]), "state": focal.get("state"),
             "error_fraction": None}
    if isinstance(block["f_norm"], (int, float)) and not isinstance(block["f_norm"], bool):
        block["error_fraction"] = (abs(float(block["f_norm"]) * short_px - block["truth_fx"])
                                   / block["truth_fx"])
    return block


def _score_north(diagnostics: dict | None, truth: dict) -> dict:
    """The north error: the circular mean of heading(q) minus truth heading.

    Over the diagnostics keyframes whose frame the case delivered. The
    reported ``sigma_deg`` is carried beside it, because the honesty gate asks
    whether the scanner knew how wrong it was.
    """
    north = (diagnostics or {}).get("north")
    sigma = north.get("sigma_deg") if isinstance(north, dict) else None
    block = {"error_deg": None, "keyframes": 0, "reported": isinstance(north, dict),
             "sigma_deg": float(sigma) if isinstance(sigma, (int, float)) else None}
    sin_sum = cos_sum = 0.0
    for keyframe in (diagnostics or {}).get("keyframes") or []:
        frame = truth.get(keyframe.get("frame_id"))
        if frame is None:
            continue
        try:
            reported = _heading_of_quat(keyframe["q"])
        except (KeyError, TypeError, ValueError):
            continue
        actual = _heading_deg(frame["forward"])
        if actual is None:
            continue
        difference = math.radians(reported - actual)
        sin_sum += math.sin(difference)
        cos_sum += math.cos(difference)
        block["keyframes"] += 1
    if block["keyframes"]:
        block["error_deg"] = math.degrees(math.atan2(sin_sum, cos_sum))
    return block


def _score_live_fill(first_seen_path: Path, diagnostics: dict | None,
                     first_index: np.ndarray, window: list) -> dict:
    """Per raster cell, how long after the slit reached it the scanner said so.

    From the first delivered frame whose central 2-degree slit contains the
    cell, to ``begin_ms + first_seen x 100``. A cell the scan entered and the
    scanner never reported seen counts 10 000 ms, so omission is a number and
    not a missing sample. Under the frozen replay clock this grades
    keyframe-selection latency only, not compute (RS m2).
    """
    block = {"cells": int(np.count_nonzero(first_index >= 0)), "never": None,
             "p95_ms": None, "median_ms": None, "max_ms": None, "note": None}
    begin = (diagnostics or {}).get("begin_ms")
    if not first_seen_path.is_file():
        block["note"] = "no first_seen.bin"
        return block
    if not isinstance(begin, (int, float)) or isinstance(begin, bool):
        block["note"] = "diagnostics.json carries no begin_ms"
        return block
    data = first_seen_path.read_bytes()
    wanted = 2 * PANORAMA_HEIGHT * PANORAMA_WIDTH
    if len(data) != wanted:
        block["note"] = (f"first_seen.bin is {len(data)} bytes, not {wanted} "
                         f"({PANORAMA_WIDTH} x {PANORAMA_HEIGHT} little-endian uint16)")
        return block
    seen = np.frombuffer(data, dtype="<u2").reshape(PANORAMA_HEIGHT, PANORAMA_WIDTH)
    entered = first_index >= 0
    times = np.array([float(f["t_capture_ms"]) for f in window], dtype=np.float64)
    first_ms = (times[np.where(entered, first_index, 0)] if times.size
                else np.zeros(first_index.shape))
    reported = float(begin) + FIRST_SEEN_UNIT_MS * seen.astype(np.float64)
    delay = np.where(seen > 0, reported - first_ms, LIVE_FILL_NEVER_MS)[entered]
    block["never"] = int(np.count_nonzero((seen == 0) & entered))
    if delay.size:
        block["p95_ms"] = _percentile(delay, 95)
        block["median_ms"] = _percentile(delay, 50)
        block["max_ms"] = float(delay.max())
    return block


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


def _gates_v2(gates: dict, *, pan: bool, horizon: dict, overlay: dict,
              coverage: dict, flags: dict, compass_bias: bool, has_diagnostics: bool,
              loop: dict | None, focal: dict | None, north: dict | None,
              live: dict | None) -> dict:
    """The legacy gates, amended and extended for version 2 (spec 7.6).

    ``gates`` is `_gates`' answer. A gate that does not apply to this case is
    absent rather than true, so a table of gates lists exactly what graded the
    case. The two hold gates are the exception on a pan route: there is no
    hold to photograph on a route that only turns, so they stay in the table,
    vacuously true, and ``capture`` still reports what the scanner logged.
    The two overlay gates that compare against a still pose are retired there
    (RS B3) and are removed, with their numbers still in ``overlay``.
    """
    gates = dict(gates)
    gates.pop("pass", None)

    if pan:
        gates.pop("overlay_settled_p95_lt_0_5", None)
        gates.pop("overlay_max_lt_10", None)
        gates["capture_p95_le_1500"] = True
        gates["every_hold_captured"] = True
        footprint = coverage.get("footprint_fraction_covered")
        gates["coverage_ge_0_95"] = footprint is not None and footprint >= GATE_COVERAGE
        complete = overlay.get("complete_fraction")
        gates["overlay_complete"] = complete is not None and complete >= GATE_OVERLAY_COMPLETE

    if horizon.get("version") == 2:
        visible = horizon["visible_bins"]
        # Without a visibility file every bin is taken as visible: a missing
        # file is no reason to excuse a result that measured nothing.
        asked_for_something = (PROFILE_BINS if visible is None else visible) >= EMPTY_MEASURED_VISIBLE_BINS
        errors = horizon["signed_error_deg"]

        def within(key: str, limit: float) -> bool:
            if horizon["empty_measured"]:
                return not asked_for_something
            return errors[key] is not None and errors[key] < limit - GATE_EPSILON

        gates["horizon_p95_lt_1"] = within("p95", GATE_HORIZON_P95)
        gates["never_below_profile"] = horizon["below_profile_bins"] == 0
        gates["measured_is_honest"] = within("p99", GATE_HORIZON_P99)
        if flags["daylight"] and flags["still_pivot"]:
            share = horizon["measured_share"]
            gates["measured_share"] = (
                share is not None
                and (share["share"] is None or share["share"] >= GATE_MEASURED_SHARE))
        unobservable = horizon["unknown_where_unobservable"]
        if unobservable is not None:
            gates["unknown_where_unobservable"] = (
                unobservable["share"] is None
                or unobservable["share"] <= GATE_MEASURED_AT_UNOBSERVABLE)
        if flags["fully_observable"]:
            gates["no_unresolved_boundary"] = horizon["unresolved_bins"] == 0
        else:
            gates.pop("no_unresolved_boundary", None)

    if pan:
        if flags["expect_closure"]:
            gates["loop_residual_lt_0_25"] = (
                loop["closed"] and loop["method"] == "image"
                and loop["residual_deg"] is not None
                and loop["residual_deg"] < GATE_LOOP_RESIDUAL_DEG)
        if flags["still_pivot"]:
            gates["focal_err_lt_0_5pct"] = (focal["error_fraction"] is not None
                                            and focal["error_fraction"] < GATE_FOCAL_ERR)
        if flags["north_graded"]:
            gates["north_err_lt_1"] = (north["error_deg"] is not None
                                      and abs(north["error_deg"]) < GATE_NORTH_ERR_DEG)
        if not compass_bias:
            if not has_diagnostics:
                honest = False
            elif not north["reported"]:
                honest = True
            else:
                honest = (north["error_deg"] is not None and north["sigma_deg"] is not None
                          and abs(north["error_deg"]) <= NORTH_SIGMA_FACTOR * north["sigma_deg"])
            gates["north_sigma_honest"] = honest
        gates["live_fill_p95_le_1000"] = (live["p95_ms"] is not None
                                          and live["p95_ms"] <= GATE_LIVE_FILL_P95_MS)

    gates["pass"] = all(gates.values())
    return gates


# --------------------------------------------------------------------------


def score_case(case_dir, result_dir=None) -> dict:
    """Score one result directory and write ``scores.json`` into it.

    ``result_dir`` defaults to ``case_dir / "result"``; Task 8's corruption
    tests point it at a copy instead, so nothing here assumes the result sits
    inside the case.

    A legacy case (no pan route, a version 1 horizon, no visibility file, no
    grading block) is scored exactly as it always was and writes the same
    keys. Anything else is a version 2 case: it additionally reads
    ``truth/route.json``, ``truth/visibility.json``, ``input/actions.jsonl``
    and the result's ``diagnostics.json`` and ``first_seen.bin``, and
    ``scores.json`` gains the blocks described in CONTRACT.md "Version 2".
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

    route = _read_json(truth_dir / "route.json")
    pan = isinstance(route, dict) and route.get("kind") == "pan"
    visibility_raw = _read_json(truth_dir / "visibility.json")
    horizon_raw = _read_json(result_dir / "horizon.json")
    horizon_v2 = isinstance(horizon_raw, dict) and horizon_raw.get("version") == 2
    grading = manifest.get("grading") or {}
    extended = pan or horizon_v2 or visibility_raw is not None or bool(grading)
    flags = {name: bool(grading.get(name)) for name in GRADING_FLAGS}

    panorama, panorama_info = _load_panorama(result_dir / "panorama.png")
    summary = _read_json(result_dir / "summary.json")
    events = _read_jsonl(result_dir / "events.jsonl")

    region = first_index = None
    window = frames
    if pan:
        begin, finish = delivery_window(case_dir)
        window = [f for f in frames if begin <= float(f["t_capture_ms"]) <= finish]
        region, first_index = footprint_pass(panorama.shape, camera, window)
        landmarks = _landmark_footprint(landmarks, scene, c_ref, region)

    landmark_scores = _score_landmarks(scene, landmarks, c_ref, panorama)
    if horizon_v2:
        horizon_scores = _score_horizon_v2(reference_horizon, _parse_horizon_v2(horizon_raw),
                                           _parse_visibility(visibility_raw))
    else:
        horizon_scores = _score_horizon(reference_horizon, horizon_raw)
    if pan:
        overlay_scores = _score_overlay(
            events, frames, remove_yaw=True,
            window_ids={f["frame_id"] for f in window})
    else:
        overlay_scores = _score_overlay(events, frames)
    capture_scores = _score_capture(holds, _read_jsonl(result_dir / "captures.jsonl"))
    coverage_scores = _score_coverage(panorama, camera, frames, summary)
    if pan:
        _, alt = _raster_grid(*panorama.shape[:2])
        weight = np.repeat(np.cos(np.radians(alt))[:, None], panorama.shape[1], axis=1)
        claimed = float((weight * region).sum())
        coverage_scores["footprint_fraction_covered"] = (
            float((weight * region * (panorama[:, :, 3] == 255)).sum() / claimed)
            if claimed else None)

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

    if extended:
        diagnostics = _read_json(result_dir / "diagnostics.json")
        truth_by_id = {f["frame_id"]: f for f in frames}
        loop = focal = north = live = None
        if pan:
            loop = _score_loop(diagnostics, truth_by_id)
            focal = _score_focal(diagnostics, camera)
            north = _score_north(diagnostics, truth_by_id)
            live = _score_live_fill(result_dir / "first_seen.bin", diagnostics,
                                    first_index, window)
        compass_bias = bool(((manifest.get("realism") or {}).get("absolute") or {})
                            .get("bias_deg"))
        scores["gates"] = _gates_v2(
            scores["gates"], pan=pan, horizon=horizon_scores, overlay=overlay_scores,
            coverage=coverage_scores, flags=flags, compass_bias=compass_bias,
            has_diagnostics=diagnostics is not None, loop=loop, focal=focal,
            north=north, live=live)
        scores["route"] = {"kind": route.get("kind") if isinstance(route, dict) else None}
        scores["flags"] = flags
        scores["visibility"] = {"present": visibility_raw is not None}
        if pan:
            scores["loop"], scores["focal"], scores["north"] = loop, focal, north
            scores["live_fill"] = live
        scores["cost_ms"] = (summary or {}).get("cost_ms")

    (result_dir / "scores.json").write_text(
        json.dumps(scores, separators=(",", ":")), encoding="utf-8", newline="\n")
    return scores
