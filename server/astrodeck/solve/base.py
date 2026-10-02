# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class WcsSolution:
    """Plain-number celestial WCS (serializable, unit-testable). CD matrix is
    preferred; cdelt/crota are an accepted fallback (astropy reads either)."""
    crval1: float
    crval2: float
    crpix1: float
    crpix2: float
    cd11: float | None = None
    cd12: float | None = None
    cd21: float | None = None
    cd22: float | None = None
    cdelt1: float | None = None
    cdelt2: float | None = None
    crota2: float | None = None
    ctype1: str = "RA---TAN"
    ctype2: str = "DEC--TAN"
    cunit: str = "deg"
    equinox: float = 2000.0
    radesys: str = "ICRS"


@dataclass
class SolveResult:
    success: bool
    ra_hours: float = 0.0
    dec_deg: float = 0.0
    rotation_deg: float = 0.0
    pixel_scale_arcsec: float = 0.0
    message: str = ""
    #: False when the solver did not report a rotation at all (issue #146).
    #: ``rotation_deg`` then reads 0.0 for the consumers that need a float, and
    #: that 0 must never be believed: since every imaging solve calibrates the
    #: rotator, a 0 that means "unknown" would re-sync it to PA 0.
    rotation_known: bool = True
    wcs: "WcsSolution | None" = None


class PlateSolver(ABC):
    name: str = "solver"

    @abstractmethod
    async def solve(self, fits_path: Path, *, ra_hint: float | None = None,
                    dec_hint: float | None = None,
                    fov_deg_hint: float | None = None,
                    downsample: int = 0) -> SolveResult:
        """Solve ``fits_path``. ``downsample`` is an OPTIONAL speed/precision
        trade (0 = the solver's own automatic choice, which is what every
        pre-existing caller gets); solvers that have no such knob accept and
        ignore it. Additive with a default, so no existing caller changes."""
        ...
