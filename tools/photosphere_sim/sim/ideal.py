# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The result directory a perfect scanner would have written.

The scorer has to be developed against an output whose every number is known
in advance, otherwise a scorer bug and a scanner bug are indistinguishable:
the ideal result is that output. It is built from the case's own truth, so
scoring it measures nothing but the scorer's own quantisation, and anything it
reports above that is the scorer's.

It is also the base for Task 8's corruptions: ``c_ref_offset`` renders the
panorama from the wrong reference position, and ``horizon_bins`` re-bins the
boundary the way a coarser output format would. Both are deliberate faults
with a known signature, not options a real scanner has.

Nothing here reads ``result/``; everything comes from ``truth/``.

On a pan route (``truth/route.json`` says ``kind: "pan"``) the ideal is the
version 2 result of CONTRACT.md: a 720-bin profile and polyline built from the
truth horizon and ``truth/visibility.json``, diagnostics whose keyframes are
the truth poses, a ``first_seen.bin`` from the claimed footprint, and the truth
basis on every delivered frame. It reads ``input/actions.jsonl`` as well, for
the Begin and Finish times the scorer's window is made of. A legacy route
keeps the result it always had, byte for byte.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

from . import truth as truth_module
from .geometry import angle_between, look_basis
from .scene import load as load_scene
# The scorer's own definitions of the footprint, the profile and the pose
# maths, imported rather than restated: an ideal that disagreed with the
# scorer about what the footprint is would pass every gate for the wrong
# reason or fail them for it.
from .score import (FIRST_SEEN_UNIT_MS, FOOTPRINT_HALF_WIDTH_DEG, PANORAMA_HEIGHT,
                    PANORAMA_WIDTH, PROFILE_BIN_DEG, PROFILE_BINS,
                    SKY_ABOVE_MEASURED_DEG, STATE_LOW, STATE_MEASURED, STATE_TALL,
                    STATE_UNKNOWN, delivery_window, footprint_pass, matrix_to_quat,
                    truth_matrix)

__all__ = ["make_ideal_result", "step_polyline"]

#: Milliseconds after a hold opens at which the ideal scanner captures it.
#: Well inside the 1500 ms gate and well inside the shortest hold.
CAPTURE_DELAY_MS = 700

#: The ideal's keyframes: one every this many degrees of truth turn.
KEYFRAME_STEP_DEG = 4.0
#: A loop closes between keyframes at least this many degrees of turn apart,
#: and only once the whole scan has turned at least ``LOOP_CLOSE_DEG``.
LOOP_MIN_TURN_DEG = 330.0
LOOP_CLOSE_DEG = 350.0
#: The scanner's reported north sigma is never below this (spec 4.12).
IDEAL_NORTH_SIGMA_DEG = 2.0
#: How far either side of a bin edge the ideal polyline takes to climb a step.
#: Under half a truth sample (0.05 degrees), so no truth sample sees the climb.
POLYLINE_STEP_DEG = 0.01


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list:
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line]


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8", newline="\n")


def _write_jsonl(path: Path, records: list) -> None:
    lines = "\n".join(json.dumps(record, separators=(",", ":")) for record in records)
    path.write_text(lines + "\n" if records else "", encoding="utf-8", newline="\n")


def _binned_horizon(alt_max: np.ndarray, bins: int) -> np.ndarray:
    """The truth profile reduced to ``bins`` bins, each bin's maximum.

    Bin ``i`` covers ``[i * 360 / bins, (i + 1) * 360 / bins)``, as the result
    contract declares, and takes the highest truth altitude of every truth bin
    whose centre falls inside it. Taking the maximum is the conservative
    choice a horizon has to make: a bin that contains an obstruction is
    blocked to the obstruction's height, however narrow the obstruction is.
    """
    truth_bins = alt_max.size
    centres = (np.arange(truth_bins) + 0.5) * (360.0 / truth_bins)
    owner = np.minimum((centres / (360.0 / bins)).astype(np.int64), bins - 1)
    binned = np.zeros(bins, dtype=np.float64)
    np.maximum.at(binned, owner, alt_max)
    return binned


# --------------------------------------------------------------------------
# Pan routes: the version 2 result
# --------------------------------------------------------------------------


def _is_pan(truth_dir: Path) -> bool:
    """True when ``truth/route.json`` exists and says ``kind: "pan"``."""
    route = truth_dir / "route.json"
    return route.is_file() and _read_json(route).get("kind") == "pan"


def step_polyline(profile, delta_deg: float = POLYLINE_STEP_DEG) -> list:
    """The tightest polyline that is never below ``profile`` (G1).

    Between two bins of equal value the line is flat, and across a step it
    climbs from the lower bin's value to the higher over ``delta_deg`` either
    side of the bin edge, with a vertex AT the edge at the higher value. That
    is as close to the profile as a line that must clear both bins at their
    shared edge can get, so scoring it measures the profile's own half-degree
    quantisation and the scorer, and nothing a polyline simplifier adds.
    Flat edge vertices are dropped: they are collinear. A profile with no step
    at all (a perfectly flat horizon) keeps two vertices, so there is still a
    line.
    """
    values = [float(v) for v in profile]
    count = len(values)
    points = []
    for k in range(count):
        left, right = values[(k - 1) % count], values[k]
        if left == right:
            continue
        edge = k * PROFILE_BIN_DEG
        points.append(((edge - delta_deg) % 360.0, left))
        points.append((edge, max(left, right)))
        points.append((edge + delta_deg, right))
    if not points:
        points = [(0.0, values[0]), (180.0, values[0])]
    points.sort(key=lambda p: p[0])
    return [{"az": az, "alt": alt} for az, alt in points]


def _ideal_horizon_v2(reference: dict, visibility: dict) -> dict:
    """``horizon.json`` version 2, from the truth horizon and the visibility file.

    Per 0.5-degree bin, with A the maximum of the 0.1-degree truth over it:
    Unknown where the bin is not visible; Tall where A + 6 is above the
    footprint top (the sky model needs 6 degrees above the boundary and the
    slit does not reach that high); Measured otherwise. A Measured bin
    publishes A, every other bin publishes 90, and the traced altitude is A
    wherever a boundary was found at all (Measured and Tall).
    """
    truth = np.clip(np.asarray(reference["alt_max"], dtype=np.float64), 0.0, 90.0)
    centres = (np.arange(truth.size) + 0.5) * (360.0 / truth.size)
    owner = np.minimum((centres / PROFILE_BIN_DEG).astype(np.int64), PROFILE_BINS - 1)
    boundary = np.zeros(PROFILE_BINS)
    np.maximum.at(boundary, owner, truth)

    visible = np.array([bool(v) for v in visibility["visible"]])
    top = np.array([np.nan if v is None else float(v)
                    for v in visibility["footprint_top_deg"]])
    if visible.size != PROFILE_BINS or top.size != PROFILE_BINS:
        raise ValueError("visibility.json needs 720 visible flags and footprint tops")
    inside = np.isfinite(top) & (boundary + SKY_ABOVE_MEASURED_DEG <= top)
    state = np.where(~visible | ~np.isfinite(top), STATE_UNKNOWN,
                     np.where(inside, STATE_MEASURED, STATE_TALL))
    profile = np.where(state == STATE_MEASURED, boundary, 90.0)
    found = np.isin(state, (STATE_MEASURED, STATE_TALL))
    return {
        "version": 2, "interpolation": "linear-wrap", "profile_bins": PROFILE_BINS,
        "profile": [float(v) for v in profile],
        "profile_traced": [float(a) if f else None for a, f in zip(boundary, found)],
        "profile_state": [int(v) for v in state],
        "points": step_polyline(profile), "tau": 0.0, "bins": PROFILE_BINS,
        "uncertain_bins": [int(i) for i in
                           np.nonzero(np.isin(state, (STATE_LOW, STATE_UNKNOWN, STATE_TALL)))[0]],
    }


def _headings(frames: list) -> list:
    """The unwrapped heading of each frame's forward, degrees clockwise from north."""
    unwrapped, previous, total = [], None, 0.0
    for frame in frames:
        forward = frame["forward"]
        heading = math.degrees(math.atan2(forward[0], forward[1]))
        if previous is not None:
            total += (heading - previous + 180.0) % 360.0 - 180.0
        unwrapped.append(total)
        previous = heading
    return unwrapped


def _keyframes(window: list) -> list:
    """Indices into ``window`` of the truth keyframes: one every 4 degrees of turn."""
    headings = _headings(window)
    chosen = []
    for index, heading in enumerate(headings):
        if not chosen or abs(heading - headings[chosen[-1]]) >= KEYFRAME_STEP_DEG:
            chosen.append(index)
    return chosen


def _zero_stats() -> dict:
    return {"n": 0, "p50": 0.0, "p95": 0.0, "max": 0.0}


def _ideal_diagnostics(camera: dict, window: list, begin_ms: float,
                       finish_ms: float) -> tuple[dict, set]:
    """``diagnostics.json`` from truth, and the set of keyframe frame ids.

    The keyframes are the truth poses every 4 degrees of turn, with their
    truth rotations as ``q``. The loop match is the last keyframe against the
    earlier one that looks most nearly the same way, provided the turn between
    them is at least 330 degrees; its residual against the truth poses is then
    zero. The focal length is the truth's, the north offset 0 and its sigma the
    scanner's own floor of 2 degrees.
    """
    headings = _headings(window)
    chosen = _keyframes(window)
    keyframes = []
    for number, index in enumerate(chosen):
        frame = window[index]
        keyframes.append({
            "id": number, "frame_id": frame["frame_id"],
            "t_ms": int(frame["t_capture_ms"]),
            "q": matrix_to_quat(truth_matrix(frame)),
            "cls": "aligned", "sigma_deg": 0.1,
        })

    unwrapped = headings[chosen[-1]] - headings[chosen[0]] if chosen else 0.0
    match = None
    if len(chosen) > 1:
        late = chosen[-1]
        candidates = [(angle_between(window[i]["forward"], window[late]["forward"]), n)
                      for n, i in enumerate(chosen[:-1])
                      if headings[late] - headings[i] >= LOOP_MIN_TURN_DEG]
        if candidates:
            match = {"early_kf": min(candidates)[1], "late_kf": len(chosen) - 1}
    closed = match is not None and abs(unwrapped) >= LOOP_CLOSE_DEG
    short_px = min(camera["width"], camera["height"])
    diagnostics = {
        "version": 1, "scanner": "pano", "sensor_only": False,
        "begin_ms": begin_ms, "finish_ms": finish_ms,
        "predictor_mode": None, "mode_changes": [], "axis_mapping": None,
        "tau_ms": 0.0, "tau_sigma_ms": 0.0, "tau_pairs": 0, "tau_applied": False,
        "focal": {"state": "locked", "f_norm": float(camera["fx"]) / short_px,
                  "sd_pct": 0.0, "ratios": 0,
                  "short_fov_deg": float(camera["fov_short_deg"])},
        "loop": {"closed": closed, "method": "image" if closed else None,
                 "pre_deg": [0.0, 0.0, 0.0] if closed else None,
                 "post_deg": 0.0 if closed else None,
                 "match": match if closed else None, "unwrapped_deg": unwrapped},
        "north": {"offset_deg": 0.0, "sigma_deg": IDEAL_NORTH_SIGMA_DEG, "spread_deg": 0.0,
                  "samples": len(window), "n_eff": float(len(window)), "stable": True,
                  "source": "ideal"},
        "declination_applied": False, "keyframes": keyframes,
        "keyframe_ms": _zero_stats(), "readback_ms": _zero_stats(), "stale_refusals": 0,
        "extractor": "tracer",
    }
    return diagnostics, {k["frame_id"] for k in keyframes}


def _ideal_first_seen(camera: dict, window: list, begin_ms: float) -> bytes:
    """``first_seen.bin``: the claimed footprint, timed from its first frame.

    Every cell the union of the claimed slits covers is reported seen when the
    first slit reached it, in deciseconds since Begin and never less than 1
    (0 means never). The whole 3-degree slit is timed, not the central part the
    live-fill gate looks at, so the ideal is never late by that gate.
    """
    _, first = footprint_pass((PANORAMA_HEIGHT, PANORAMA_WIDTH), camera, window,
                              first_half_width_deg=FOOTPRINT_HALF_WIDTH_DEG)
    times = np.array([float(f["t_capture_ms"]) for f in window], dtype=np.float64)
    seen = np.zeros(first.shape, dtype=np.int64)
    reached = first >= 0
    seen[reached] = np.rint((times[first[reached]] - begin_ms) / FIRST_SEEN_UNIT_MS)
    seen[reached] = np.clip(seen[reached], 1, 65535)
    return seen.astype("<u2").tobytes()


def _write_pan_result(case_dir: Path, out_dir: Path, manifest: dict) -> None:
    """The version 2 files of a perfect scanner on a pan route.

    ``horizon.json`` version 2, ``diagnostics.json``, ``first_seen.bin``, an
    ``events.jsonl`` carrying the truth basis on every delivered frame plus
    ``keyframe`` and ``cls``, a keyframe ``captures.jsonl`` and a
    ``summary.json`` whose ``cells_*`` are null and whose ``cost_ms`` is
    zero. Needs ``truth/visibility.json``: what a perfect scanner may claim
    to have measured is exactly what that file says was visible.
    """
    truth_dir = case_dir / "truth"
    visibility_path = truth_dir / "visibility.json"
    if not visibility_path.is_file():
        raise ValueError(f"{truth_dir} has a pan route and no visibility.json: "
                         "the ideal result has nothing to say which bins were visible")
    camera = _read_json(truth_dir / "camera.json")
    frames = _read_jsonl(truth_dir / "trajectory.jsonl")
    begin, finish = delivery_window(case_dir)
    window = [f for f in frames if begin <= float(f["t_capture_ms"]) <= finish]
    begin_ms = begin if math.isfinite(begin) else float(window[0]["t_capture_ms"])
    finish_ms = finish if math.isfinite(finish) else float(window[-1]["t_capture_ms"])

    _write_json(out_dir / "horizon.json",
                _ideal_horizon_v2(_read_json(truth_dir / "reference-horizon.json"),
                                  _read_json(visibility_path)))
    diagnostics, keyframe_ids = _ideal_diagnostics(camera, window, begin_ms, finish_ms)
    _write_json(out_dir / "diagnostics.json", diagnostics)
    (out_dir / "first_seen.bin").write_bytes(_ideal_first_seen(camera, window, begin_ms))

    events, captures, taken = [], [], 0
    for frame in frames:
        t_ms = int(frame["t_capture_ms"])
        is_keyframe = frame["frame_id"] in keyframe_ids
        if is_keyframe:
            captures.append({"at": t_ms, "frame_id": frame["frame_id"],
                             "outcome": "accepted", "kf": taken,
                             "detail": "first" if taken == 0 else "aligned",
                             "rate_deg_s": float(frame["angular_rate_deg_s"])})
            taken += 1
        events.append({
            "t_ms": t_ms, "frame_id": frame["frame_id"],
            "compass_ready": True, "tilt_ready": True, "aim": None,
            "basis": {"right": list(frame["right"]), "up": list(frame["up"]),
                      "forward": list(frame["forward"])},
            "frame_count": taken, "cue": "turning",
            "keyframe": is_keyframe, "cls": "aligned" if is_keyframe else None,
        })
    _write_jsonl(out_dir / "events.jsonl", events)
    _write_jsonl(out_dir / "captures.jsonl", captures)
    _write_json(out_dir / "summary.json", {
        "frames_delivered": len(frames),
        "events_delivered": len(events),
        "frames_accepted": len(captures),
        "cells_total": None,
        "cells_covered": None,
        "elapsed_ms": int(max((f["t_capture_ms"] for f in frames), default=0)),
        "app_commit": manifest.get("versions", {}).get("app_commit"),
        "cost_ms": {"callback": _zero_stats(), "callback_ref": _zero_stats(),
                    "ref_unit_ms": 0.0},
    })


def make_ideal_result(case_dir, out_dir, c_ref_offset=(0.0, 0.0, 0.0),
                      horizon_bins: int = 3600) -> Path:
    """Write a complete ``result/`` for ``case_dir`` and return its path.

    ``c_ref_offset`` (metres, ENU) moves the point the panorama is rendered
    from; everything else stays on the case's own ``c_ref``, so an offset
    shows up exactly where a wrong reference position should, in the landmark
    directions. ``horizon_bins`` is the resolution of ``horizon.json``: 3600
    matches the truth bin for bin, and a smaller number is the coarser format
    a real scanner may be limited to. A pan route ignores ``horizon_bins``: its
    horizon is the 720-bin version 2 profile.
    """
    case_dir = Path(case_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    truth_dir = case_dir / "truth"
    scene = load_scene(truth_dir / "scene.json")
    c_ref = np.asarray(_read_json(truth_dir / "reference.json")["c_ref"], dtype=np.float64)
    frames = _read_jsonl(truth_dir / "trajectory.jsonl")
    holds = _read_json(truth_dir / "holds.json")
    reference_horizon = _read_json(truth_dir / "reference-horizon.json")
    manifest = _read_json(case_dir / "manifest.json")

    panorama = truth_module.ideal_panorama(
        scene, c_ref + np.asarray(c_ref_offset, dtype=np.float64))
    Image.fromarray(panorama, mode="RGBA").save(out_dir / "panorama.png")

    if _is_pan(truth_dir):
        _write_pan_result(case_dir, out_dir, manifest)
        return out_dir

    # The scanner's boundary floor is 0: it describes what blocks the sky, and
    # -10 in the truth means "nothing was hit", not "the sky reaches -10".
    alt_max = np.clip(np.asarray(reference_horizon["alt_max"], dtype=np.float64), 0.0, 90.0)
    binned = _binned_horizon(alt_max, horizon_bins)
    _write_json(out_dir / "horizon.json", {
        "bins": horizon_bins,
        "points": [{"az": (i + 0.5) * 360.0 / horizon_bins, "alt": float(a)}
                   for i, a in enumerate(binned)],
        "uncertain_bins": [],
    })

    events = []
    for frame in frames:
        t_ms = int(frame["t_capture_ms"])
        inside = next((h for h in holds if h["from_ms"] <= t_ms <= h["to_ms"]), None)
        events.append({
            "t_ms": t_ms,
            "frame_id": frame["frame_id"],
            "compass_ready": True,
            "tilt_ready": True,
            "aim": None if inside is None else int(inside["index"]),
            "basis": {"right": list(frame["right"]),
                      "up": list(frame["up"]),
                      "forward": list(frame["forward"])},
            "frame_count": sum(1 for h in holds if h["to_ms"] <= t_ms),
            "cue": "hold" if inside is not None else "move",
        })
    _write_jsonl(out_dir / "events.jsonl", events)

    captures = []
    for hold in holds:
        basis = look_basis(hold["az"], hold["alt"])
        as_json = {"right": [float(v) for v in basis.right],
                   "up": [float(v) for v in basis.up],
                   "forward": [float(v) for v in basis.forward]}
        captures.append({
            "at": int(hold["from_ms"]) + CAPTURE_DELAY_MS,
            "outcome": "accepted",
            "cell": int(hold["index"]),
            "basis": as_json,
            "sensor_basis": as_json,
            "adjusted": False,
        })
    _write_jsonl(out_dir / "captures.jsonl", captures)

    _write_json(out_dir / "summary.json", {
        "frames_delivered": len(frames),
        "events_delivered": len(events),
        "frames_accepted": len(captures),
        "cells_total": 1,
        "cells_covered": 1,
        "elapsed_ms": int(max((f["t_capture_ms"] for f in frames), default=0)),
        "app_commit": manifest.get("versions", {}).get("app_commit"),
    })

    return out_dir
