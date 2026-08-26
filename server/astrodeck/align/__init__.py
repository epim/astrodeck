"""Alignment geometry: where the instruments point relative to each other."""
from .guide_offset import (
    GuideOffset,
    STALE_AFTER_DAYS,
    aim_for,
    offset_from_solves,
    staleness,
)

__all__ = [
    "GuideOffset",
    "STALE_AFTER_DAYS",
    "aim_for",
    "offset_from_solves",
    "staleness",
]
