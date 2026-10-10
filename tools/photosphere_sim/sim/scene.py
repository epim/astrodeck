# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
"""The declarative scene description: what the simulated world contains.

A scene is a JSON file under ``scenes/``, and the schema is in CONTRACT.md's
"Scene schema" section. This module loads one and checks its structure; it
computes nothing about visibility, which is :mod:`sim.truth`'s job.

Angles in a scene file are degrees and lengths are metres in the fictional ENU
world (``x`` east, ``y`` north, ``z`` up). There are no geodetic coordinates
anywhere in the simulator.

What ``load`` checks is structure only: the schema version, that every object
kind is one this simulator can intersect, that palette indices exist, that an
object's optional ``material`` is one the renderer can paint (SPEC-v2 13.4),
and that every id a surface landmark or a test obstacle refers to is a real
object. A material is a rendering detail: :mod:`sim.truth` never reads it,
because it changes no geometry, so it is checked here and drawn only by
``renderer/materials.js``. The scene's stylistic invariants (object colours
are never palette colours and have a channel spread below 60) are asserted by
the tests rather than here, so that they read as claims about the chart yard
rather than as loader behaviour.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from .palette import PALETTE

__all__ = ["MATERIAL_KINDS", "OBJECT_KINDS", "SCHEMA", "Scene", "load"]

SCHEMA = 1

#: The object kinds :mod:`sim.truth` can ray-cast, with their required keys.
OBJECT_KINDS = {
    "plane": ("z",),
    "box": ("min", "max"),
    "cylinder": ("base", "radius", "height"),
    "sphere": ("centre", "radius"),
}

#: The material kinds ``renderer/materials.js`` can paint, with the keys each
#: one requires. A material carries exactly these and nothing else, so a
#: misspelt key is an error here rather than a default the renderer invents.
#: Every key is required and none has a default, because a default would be a
#: second copy of the recipe for the Python and JavaScript sides to keep equal.
MATERIAL_KINDS = {
    "flat": (),
    "noise": ("seed", "cells", "octaves", "mod"),
    "stripes": ("count", "duty", "colour2"),
}

#: ``cells * 2**(octaves - 1)``, the finest octave's lattice width, is capped
#: so that one bad scene cannot ask the renderer for a lattice of billions.
MAX_NOISE_COLUMNS = 4096

#: A stripe narrower than two of the 256 texels it is drawn with is not a
#: stripe, so ``count`` stops at half the texture width.
MAX_STRIPES = 128


@dataclass(frozen=True)
class Scene:
    """One world description, as loaded from ``scenes/<name>.json``.

    The fields hold the JSON as it was written (dictionaries and lists, angles
    in degrees, lengths in metres), in file order, because file order decides
    ties between coincident surfaces and which landmark disc wins an overlap.
    """

    name: str
    seed: int
    background: dict
    landmarks: list[dict]
    objects: list[dict]
    surface_landmarks: list[dict]
    test_obstacles: list[dict]

    #: Scratch space for derived products that are expensive and immutable,
    #: such as the background texture :mod:`sim.truth` samples. Never part of
    #: the scene's identity, so it is excluded from ``repr`` and equality.
    cache: dict = field(default_factory=dict, init=False, repr=False, compare=False)

    def object_ids(self) -> list[str]:
        """The object ids, in file order."""
        return [obj["id"] for obj in self.objects]


def load(path) -> Scene:
    """Read a scene file and check its structure. Raises ``ValueError``."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise ValueError(f"{path}: schema {data.get('schema')!r}, expected {SCHEMA}")

    scene = Scene(
        name=str(data["name"]),
        seed=int(data["seed"]),
        background=data["background"],
        landmarks=list(data.get("landmarks", [])),
        objects=list(data.get("objects", [])),
        surface_landmarks=list(data.get("surface_landmarks", [])),
        test_obstacles=list(data.get("test_obstacles", [])),
    )

    background = scene.background
    if background.get("kind") != "directional":
        raise ValueError(f"{path}: unsupported background {background.get('kind')!r}")
    texture = background["texture"]
    if texture.get("kind") != "value-noise":
        raise ValueError(f"{path}: unsupported texture {texture.get('kind')!r}")

    ids = scene.object_ids()
    if len(set(ids)) != len(ids):
        raise ValueError(f"{path}: duplicate object ids")
    for obj in scene.objects:
        kind = obj.get("kind")
        if kind not in OBJECT_KINDS:
            raise ValueError(f"{path}: object {obj.get('id')!r} has kind {kind!r}")
        for key in OBJECT_KINDS[kind]:
            if key not in obj:
                raise ValueError(f"{path}: {kind} {obj.get('id')!r} needs {key!r}")
        if len(obj.get("colour", ())) != 3:
            raise ValueError(f"{path}: object {obj.get('id')!r} needs an rgb colour")
        if "material" in obj:
            _check_material(path, obj)

    for lm in scene.landmarks:
        _check_palette(path, lm)
    for lm in scene.surface_landmarks:
        _check_palette(path, lm)
        if lm.get("object") not in ids:
            raise ValueError(f"{path}: landmark {lm.get('id')!r} is on no known object")
    for obstacle in scene.test_obstacles:
        if obstacle.get("object") not in ids:
            raise ValueError(f"{path}: obstacle {obstacle.get('id')!r} is on no object")

    return scene


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _check_material(path: Path, obj: dict) -> None:
    """Check an object's optional ``material`` against SPEC-v2 13.4.

    ``flat`` is the object's own colour and carries nothing. ``noise`` needs a
    ``seed`` (an unsigned 32-bit integer, the lattice hash's range), ``cells``
    (at least 2, the smallest lattice with a row to interpolate), ``octaves``
    and ``mod``, the factor range ``[lo, hi]`` with ``0 <= lo <= hi``.
    ``stripes`` needs a ``count``, a ``duty`` strictly between 0 and 1, and a
    ``colour2`` of three 8-bit channels.

    A plane can only be ``flat``: the renderer re-centres the ground on the
    camera every frame so that it stays infinite, and a texture on it would
    swim across the floor as the camera moved.
    """
    name = obj.get("id")
    material = obj["material"]

    def fail(why: str):
        raise ValueError(f"{path}: object {name!r} material {why}")

    if not isinstance(material, dict):
        fail(f"must be an object, not {type(material).__name__}")
    kind = material.get("kind")
    if kind not in MATERIAL_KINDS:
        fail(f"has kind {kind!r}, expected one of {sorted(MATERIAL_KINDS)}")
    required = MATERIAL_KINDS[kind]
    for key in required:
        if key not in material:
            fail(f"{kind} needs {key!r}")
    unknown = sorted(set(material) - {"kind", *required})
    if unknown:
        fail(f"{kind} has unknown keys {unknown}")
    if kind != "flat" and obj.get("kind") == "plane":
        fail(f"{kind} is not allowed on a plane; the ground follows the camera")

    if kind == "noise":
        seed, cells, octaves, mod = (material[k] for k in required)
        if not _is_int(seed) or not 0 <= seed <= 0xFFFFFFFF:
            fail(f"noise seed {seed!r} must be an integer in 0..4294967295")
        if not _is_int(cells) or cells < 2:
            fail(f"noise cells {cells!r} must be an integer of at least 2")
        if not _is_int(octaves) or octaves < 1:
            fail(f"noise octaves {octaves!r} must be an integer of at least 1")
        if cells * 2 ** (octaves - 1) > MAX_NOISE_COLUMNS:
            fail(f"noise cells {cells} x 2^{octaves - 1} exceeds {MAX_NOISE_COLUMNS} columns")
        if (not isinstance(mod, (list, tuple)) or len(mod) != 2
                or not all(_is_number(m) for m in mod) or not 0 <= mod[0] <= mod[1]):
            fail(f"noise mod {mod!r} must be [lo, hi] with 0 <= lo <= hi")
    elif kind == "stripes":
        count, duty, colour2 = (material[k] for k in required)
        if not _is_int(count) or not 1 <= count <= MAX_STRIPES:
            fail(f"stripes count {count!r} must be an integer in 1..{MAX_STRIPES}")
        if not _is_number(duty) or not 0 < duty < 1:
            fail(f"stripes duty {duty!r} must be a number strictly between 0 and 1")
        if (not isinstance(colour2, (list, tuple)) or len(colour2) != 3
                or not all(_is_int(c) and 0 <= c <= 255 for c in colour2)):
            fail(f"stripes colour2 {colour2!r} must be three integers in 0..255")


def _check_palette(path: Path, landmark: dict) -> None:
    index = landmark.get("palette")
    if not isinstance(index, int) or not 0 <= index < len(PALETTE):
        raise ValueError(f"{path}: landmark {landmark.get('id')!r} palette {index!r}")
