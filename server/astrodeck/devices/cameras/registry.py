"""In-tree registry of camera adapters. Real backends register their FACTORY so
the parametrized contract suite (test_camera_contract.py) and optional in-tree
discovery can enumerate every brand. Out-of-tree brands use the framework's
entry-point discovery instead — this registry is for the bundled ones."""
from __future__ import annotations

from typing import Callable

from .adapter import CameraAdapter

_REGISTRY: dict[str, Callable[[], CameraAdapter]] = {}


def register_adapter(vendor: str, factory: Callable[[], CameraAdapter]) -> None:
    if vendor in _REGISTRY:
        raise ValueError(f"camera adapter {vendor!r} already registered")
    _REGISTRY[vendor] = factory


def iter_adapters() -> list[tuple[str, Callable[[], CameraAdapter]]]:
    return sorted(_REGISTRY.items())


def clear_registry() -> None:
    _REGISTRY.clear()
