"""The per-frame-WCS solve gate (per-frame-wcs spec §2.2).

Pure + dependency-free on purpose: the hub's background WCS worker asks this
before spending 2-10 s of ASTAP CPU on a frame, and it must be unit-testable
without a hub, a subprocess or a FITS file.

This is the self-contained reading of "only tag good frames" (decision D3): we
deliberately do NOT couple to the sequence engine's ``_check_quality`` verdict,
which lands *after* ``capture()`` returns and does not exist at all for live-loop
or single captures. A detected-star floor covers the real intent — don't burn a
solve on a cloud/trail frame ASTAP would fail on anyway — for every capture path.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only (keeps this module import-free)
    from ..config import WcsStampConfig


def wcs_should_solve(star_count: "int | None", cfg: "WcsStampConfig") -> bool:
    """True when this frame is worth plate-solving for a WCS stamp.

    * ``min_stars <= 0`` (the default) disables the gate entirely — every saved
      light is offered to the solver, exactly as before this knob existed.
    * ``star_count is None`` ALLOWS the solve: frames from backends that report
      no star count (NINA-rendered previews) must not be punished by a gate the
      user set for locally-measured frames.
    * Otherwise the frame passes only with at least ``min_stars`` detected stars.
    """
    try:
        min_stars = int(getattr(cfg, "min_stars", 0) or 0)
    except (TypeError, ValueError):
        return True
    if min_stars <= 0:
        return True
    if star_count is None:
        return True
    try:
        return int(star_count) >= min_stars
    except (TypeError, ValueError):
        return True
