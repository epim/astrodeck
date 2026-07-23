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
    wcs: "WcsSolution | None" = None


class PlateSolver(ABC):
    name: str = "solver"

    @abstractmethod
    async def solve(self, fits_path: Path, *, ra_hint: float | None = None,
                    dec_hint: float | None = None,
                    fov_deg_hint: float | None = None) -> SolveResult: ...
