"""Build a case directory (CONTRACT.md's "Case directory" section) from a
case definition, a scene, a route and a renderer.

``build_case`` is the only thing another module is meant to call; the
``*_DIR`` constants and :func:`flat_renderer` are exposed too because the CLI
(:mod:`sim.__main__`) and the tests need them without duplicating path logic.

Nothing here depends on the current working directory: every scene, route and
case-definition path is resolved against this package's own location, so
``build_case`` behaves the same whether it is driven by the CLI (run from
``tools/photosphere_sim``) or by a test (run from the repository root).
"""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
from pathlib import Path
from typing import Callable, Iterator

import numpy as np
import PIL
from PIL import Image

from . import scene as scene_module
from . import sensors
from . import trajectory as trajectory_module
from . import truth as truth_module
from .geometry import Camera

__all__ = ["CASES_DIR", "ROUTES_DIR", "SCENES_DIR", "build_case", "flat_renderer"]

ROOT = Path(__file__).resolve().parent.parent
SCENES_DIR = ROOT / "scenes"
ROUTES_DIR = ROOT / "routes"
CASES_DIR = ROOT / "cases"


def flat_renderer(scene, camera: Camera, frames) -> Iterator[np.ndarray]:
    """Mid-grey (128, 128, 128) frames of the camera's size, one per frame.

    Exists for tests and for Task 6 to use before Task 4's real renderer
    lands; it reads neither the scene nor any frame's pose, which is exactly
    what makes its output byte-identical across builds and across the two
    routes and both field of view cases.
    """
    grey = np.full((camera.height, camera.width, 3), 128, dtype=np.uint8)
    for _ in frames:
        yield grey.copy()


def _write_jsonl(path: Path, records: list) -> None:
    lines = "\n".join(json.dumps(record, separators=(",", ":")) for record in records)
    text = lines + "\n" if records else ""
    path.write_text(text, encoding="utf-8", newline="\n")


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8", newline="\n")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_concatenated_digests(paths: list) -> str:
    """SHA-256 of the concatenation of each path's own SHA-256 hex digest, in
    the order given. This is CONTRACT.md's recipe for the ``frames`` hash;
    the same recipe is used for ``truth``, over every file under ``truth/``
    sorted by relative path, since the contract does not spell that one out
    but the two need the same determinism guarantee."""
    digest = hashlib.sha256()
    for path in paths:
        digest.update(_sha256_hex(path.read_bytes()).encode("ascii"))
    return digest.hexdigest()


def build_case(case_def: dict, out_root: Path, renderer: Callable) -> Path:
    """Write one case directory under ``out_root`` and return its path.

    ``renderer(scene, camera, frames)`` must yield one ``(H, W, 3)`` uint8
    array per frame, in ``frames`` order. Writes ``input/`` and ``truth/`` and
    ``manifest.json``; ``result/`` is the replay's to create, not this
    function's.
    """
    out_dir = Path(out_root) / case_def["case_id"]
    frames_dir = out_dir / "input" / "frames"
    truth_dir = out_dir / "truth"
    frames_dir.mkdir(parents=True, exist_ok=True)
    truth_dir.mkdir(parents=True, exist_ok=True)

    scene_path = SCENES_DIR / f"{case_def['scene']}.json"
    scene = scene_module.load(scene_path)
    route = json.loads((ROUTES_DIR / f"{case_def['route']}.json").read_text(encoding="utf-8"))
    camera_def = case_def["camera"]
    camera = Camera(camera_def["width"], camera_def["height"], camera_def["fov_short_deg"])
    fps = case_def["fps"]

    traj = trajectory_module.build(route, fps)

    # ``strict`` for two reasons: a renderer that yields the wrong number of
    # frames is a defect rather than a short case, and it makes zip ask the
    # renderer for one frame past the last, which is what lets a generator
    # holding a browser open (``sim.render.CaseRenderer``) run its own
    # shutdown here instead of whenever the garbage collector gets to it.
    for frame, image in zip(traj.frames, renderer(scene, camera, traj.frames), strict=True):
        Image.fromarray(image, mode="RGB").save(frames_dir / f"{frame.frame_id}.png")

    frame_obs = [
        {
            "kind": record["kind"],
            "frame_id": record["frame_id"],
            "t_capture_ms": record["t_capture_ms"],
            "t_present_ms": record["t_present_ms"],
            "width": camera.width,
            "height": camera.height,
            "file": record["file"],
        }
        for record in sensors.frame_records(traj)
    ]
    orientation_obs = sensors.orientation_events(traj)
    observations = sorted(
        frame_obs + orientation_obs,
        key=lambda r: r["t_present_ms"] if r["kind"] == "frame" else r["t_receive_ms"],
    )
    observations_path = out_dir / "input" / "observations.jsonl"
    _write_jsonl(observations_path, observations)

    last_present_ms = max(r["t_present_ms"] for r in frame_obs)
    actions = [{"t_ms": 0, "action": "begin"},
              {"t_ms": last_present_ms + 1000, "action": "finish"}]
    _write_jsonl(out_dir / "input" / "actions.jsonl", actions)

    (truth_dir / "scene.json").write_bytes(scene_path.read_bytes())
    _write_json(truth_dir / "camera.json", {
        "width": camera.width, "height": camera.height, "fov_short_deg": camera.fov_short_deg,
        "fx": camera.fx, "fy": camera.fy, "cx": camera.cx, "cy": camera.cy, "distortion": None,
    })
    _write_json(truth_dir / "reference.json", {"c_ref": [float(v) for v in traj.c_ref]})

    trajectory_records = [
        {
            "frame_id": frame.frame_id,
            "t_capture_ms": frame.t_capture_ms,
            "position": [float(v) for v in frame.position],
            "right": [float(v) for v in frame.basis.right],
            "up": [float(v) for v in frame.basis.up],
            "forward": [float(v) for v in frame.basis.forward],
            "az": frame.az,
            "alt": frame.alt,
            "angular_rate_deg_s": frame.angular_rate_deg_s,
        }
        for frame in traj.frames
    ]
    _write_jsonl(truth_dir / "trajectory.jsonl", trajectory_records)

    _write_json(truth_dir / "landmarks.json", truth_module.landmark_directions(scene, traj.c_ref))
    _write_json(truth_dir / "reference-horizon.json", truth_module.horizon(scene, traj.c_ref))
    _write_json(truth_dir / "holds.json", [
        {"index": h.index, "az": h.az, "alt": h.alt, "from_ms": h.from_ms, "to_ms": h.to_ms}
        for h in traj.holds
    ])

    frame_names = sorted(p.name for p in frames_dir.glob("*.png"))
    truth_paths = sorted((p for p in truth_dir.rglob("*") if p.is_file()),
                         key=lambda p: p.relative_to(truth_dir).as_posix())
    hashes = {
        "frames": _hash_concatenated_digests([frames_dir / name for name in frame_names]),
        "observations": _sha256_hex(observations_path.read_bytes()),
        "truth": _hash_concatenated_digests(truth_paths),
    }

    # Every library whose output is hashed into this case, so that a case
    # directory says what produced it. ``numpy`` and ``Pillow`` are in the
    # list because they are on the path from a pose to a PNG on disk: the
    # truth arrays are numpy's arithmetic and every frame is Pillow's encoder,
    # so two case directories whose hashes differ can be told apart by this
    # block rather than by guesswork. ``webgl_renderer`` is the string the
    # page itself reports for the GL implementation that drew the frames
    # (SwiftShader under the headless Chromium), which is the one version
    # here that no package manifest records. A renderer that knows those
    # three says so; ``flat_renderer`` does not, and leaves them null.
    versions = {"three": None, "chromium": None, "webgl_renderer": None,
                "python": platform.python_version(),
                "numpy": np.__version__, "pillow": PIL.__version__,
                "app_commit": None}
    versions.update(getattr(renderer, "versions", None) or {})

    manifest = {
        "schema": 1,
        "case_id": case_def["case_id"],
        "seed": case_def["seed"],
        "scene": case_def["scene"],
        "route": case_def["route"],
        "camera": {"width": camera.width, "height": camera.height,
                   "fov_short_deg": camera.fov_short_deg},
        "fps": fps,
        "expected": case_def["expected"],
        # The profile the case declares, carried here so a scored result
        # names it from the case directory it was scored in rather than from
        # whatever ``cases/`` happens to hold on the machine doing the
        # scoring.
        "profile": case_def.get("profile"),
        "hashes": hashes,
        "versions": versions,
    }
    _write_json(out_dir / "manifest.json", manifest)

    return out_dir
