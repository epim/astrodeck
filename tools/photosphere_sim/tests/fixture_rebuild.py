# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""Compare a committed fixture with a rebuild of it, frames and visibility included (#1156).

``compare_case(committed, fresh)`` returns a list of problems, empty when the
committed case is what the rebuild produced. Frames and ``truth/visibility.json``
are the two things a flat-renderer rebuild cannot check, so they are all this
compares; ``test_wp19_integrity`` owns the rest.

The tolerance, and why:

- A frame is equal when its PNG bytes are equal. That is what happens on the
  machine that recorded the fixtures: two builds there gave 116 files, all
  byte identical, ``manifest.json`` included.
- Otherwise a frame is equal when it has the same size and no channel of any
  pixel differs by more than ``FRAME_TOLERANCE`` levels of 255. A different
  GL rasteriser or PNG encoder moves a pixel by rounding, a level or so. A
  stale fixture (a scene, route, camera or palette that moved on) moves
  landmark and horizon edges by tens of levels over hundreds of pixels, which
  is far outside it. This has been measured only on the recording machine,
  where the difference is zero; the bound is a judgement, not a measurement
  on the CI image.
- ``visibility.json`` is a function of the frames. When every frame is byte
  equal it must be byte equal as well. When frames differ within tolerance it
  must have the same keys, the same lengths and the same ``visible`` mask, and
  every number within ``VISIBILITY_REL`` of the committed value (the
  statistics are taken over those pixels).

When something differs and the manifests record other tool versions, each
problem is followed by the versions that differ, so a red run reads as "the
environment moved" or "the fixture is stale" and not as a float mismatch.
"""

from __future__ import annotations

import io
import json
import math
import pathlib

import numpy as np
from PIL import Image

#: Levels of 255 per channel that a rebuilt frame may differ by.
FRAME_TOLERANCE = 2

#: Relative difference allowed in a visibility statistic when the frames are
#: not byte equal. A mask flip or a missing key is never tolerated.
VISIBILITY_REL = 0.01

VERSION_KEYS = ("three", "chromium", "python", "numpy", "pillow")


def compare_case(committed: pathlib.Path, fresh: pathlib.Path) -> list:
    """Problems found comparing ``committed`` with the rebuild at ``fresh``."""
    problems, frames_exact = _compare_frames(committed, fresh)
    problems += _compare_visibility(committed, fresh, frames_exact)
    if problems:
        problems += _version_notes(committed, fresh)
    return problems


def _frames(case_dir: pathlib.Path) -> dict:
    return {p.name: p for p in sorted((case_dir / "input" / "frames").glob("*.png"))}


def _compare_frames(committed: pathlib.Path, fresh: pathlib.Path) -> tuple:
    problems = []
    exact = True
    committed_frames, fresh_frames = _frames(committed), _frames(fresh)
    for name in sorted(set(committed_frames) ^ set(fresh_frames)):
        where = "committed only" if name in committed_frames else "rebuild only"
        problems.append(f"{committed.name}: frame {name} is in the {where}")
        exact = False
    for name in sorted(set(committed_frames) & set(fresh_frames)):
        if committed_frames[name].read_bytes() == fresh_frames[name].read_bytes():
            continue
        exact = False
        problem = _frame_problem(committed.name, name, committed_frames[name], fresh_frames[name])
        if problem:
            problems.append(problem)
    return problems, exact


def _frame_problem(case: str, name: str, committed: pathlib.Path, fresh: pathlib.Path):
    a = _pixels(committed)
    b = _pixels(fresh)
    if a.shape != b.shape:
        return f"{case}: frame {name} is {a.shape[1]}x{a.shape[0]} committed, {b.shape[1]}x{b.shape[0]} rebuilt"
    worst = int(np.abs(a - b).max())
    if worst > FRAME_TOLERANCE:
        pixels = int((np.abs(a - b).max(axis=2) > FRAME_TOLERANCE).sum())
        return (f"{case}: frame {name} is stale: {pixels} pixels differ by more than "
                f"{FRAME_TOLERANCE} levels, the worst by {worst}")
    return None


def _pixels(path: pathlib.Path) -> np.ndarray:
    with Image.open(io.BytesIO(path.read_bytes())) as image:
        return np.array(image.convert("RGB"), dtype=np.int16)


def _compare_visibility(committed: pathlib.Path, fresh: pathlib.Path, frames_exact: bool) -> list:
    name = "truth/visibility.json"
    a, b = committed / name, fresh / name
    if a.exists() != b.exists():
        return [f"{committed.name}: {name} is in the {'committed case' if a.exists() else 'rebuild'} only"]
    if not a.exists() or a.read_bytes() == b.read_bytes():
        return []
    if frames_exact:
        return [f"{committed.name}: {name} differs from the rebuild of byte-identical frames"]
    return _visibility_problems(committed.name, json.loads(a.read_text(encoding="utf-8")),
                                json.loads(b.read_text(encoding="utf-8")))


def _visibility_problems(case: str, committed: dict, fresh: dict) -> list:
    if sorted(committed) != sorted(fresh):
        return [f"{case}: visibility.json keys differ: {sorted(committed)} against {sorted(fresh)}"]
    problems = []
    for key in sorted(committed):
        if not _same(committed[key], fresh[key]):
            problems.append(f"{case}: visibility.json {key} differs beyond {VISIBILITY_REL:.0%}")
    return problems


def _same(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=VISIBILITY_REL, abs_tol=1e-6)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def _version_notes(committed: pathlib.Path, fresh: pathlib.Path) -> list:
    recorded = _versions(committed)
    rebuilt = _versions(fresh)
    return [f"{committed.name}: manifest records {key} {recorded[key]}, this rebuild used {rebuilt[key]}"
            for key in VERSION_KEYS
            if key in recorded and key in rebuilt and recorded[key] != rebuilt[key]]


def _versions(case_dir: pathlib.Path) -> dict:
    manifest = json.loads((case_dir / "manifest.json").read_text(encoding="utf-8"))
    return manifest.get("versions", {})
