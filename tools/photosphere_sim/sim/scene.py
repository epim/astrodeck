"""The declarative scene description: what the simulated world contains.

A scene is a JSON file under ``scenes/``, and the schema is in CONTRACT.md's
"Scene schema" section. This module loads one and checks its structure; it
computes nothing about visibility, which is :mod:`sim.truth`'s job.

Angles in a scene file are degrees and lengths are metres in the fictional ENU
world (``x`` east, ``y`` north, ``z`` up). There are no geodetic coordinates
anywhere in the simulator.

What ``load`` checks is structure only: the schema version, that every object
kind is one this simulator can intersect, that palette indices exist, and that
every id a surface landmark or a test obstacle refers to is a real object. The
scene's stylistic invariants (object colours are never palette colours and
have a channel spread below 60) are asserted by the tests rather than here, so
that they read as claims about the chart yard rather than as loader behaviour.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .palette import PALETTE

__all__ = ["OBJECT_KINDS", "SCHEMA", "Scene", "load"]

SCHEMA = 1

#: The object kinds :mod:`sim.truth` can ray-cast, with their required keys.
OBJECT_KINDS = {
    "plane": ("z",),
    "box": ("min", "max"),
    "cylinder": ("base", "radius", "height"),
    "sphere": ("centre", "radius"),
}


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


def _check_palette(path: Path, landmark: dict) -> None:
    index = landmark.get("palette")
    if not isinstance(index, int) or not 0 <= index < len(PALETTE):
        raise ValueError(f"{path}: landmark {landmark.get('id')!r} palette {index!r}")
