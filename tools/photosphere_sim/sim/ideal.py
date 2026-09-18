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
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from . import truth as truth_module
from .geometry import look_basis
from .scene import load as load_scene

__all__ = ["make_ideal_result"]

#: Milliseconds after a hold opens at which the ideal scanner captures it.
#: Well inside the 1500 ms gate and well inside the shortest hold.
CAPTURE_DELAY_MS = 700


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


def make_ideal_result(case_dir, out_dir, c_ref_offset=(0.0, 0.0, 0.0),
                      horizon_bins: int = 3600) -> Path:
    """Write a complete ``result/`` for ``case_dir`` and return its path.

    ``c_ref_offset`` (metres, ENU) moves the point the panorama is rendered
    from; everything else stays on the case's own ``c_ref``, so an offset
    shows up exactly where a wrong reference position should, in the landmark
    directions. ``horizon_bins`` is the resolution of ``horizon.json``: 3600
    matches the truth bin for bin, and a smaller number is the coarser format
    a real scanner may be limited to.
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
