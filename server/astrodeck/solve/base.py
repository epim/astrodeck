# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import math
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

    def has_usable_scale(self) -> bool:
        """Whether this solution states a plate scale a reader can use, judged
        on the numbers ``fitsio._apply_wcs`` would write: the CD matrix (a
        missing term written as 0.0) or else the CDELT pair (a missing CDELT2
        written as CDELT1), with a non-zero determinant (product, for CDELT)
        and every number finite.

        An all-zero CD reads back as the CDELT=1 default (1 deg/pixel), a
        singular CD or a zero CDELT as a wcslib error, and a NaN raises out of
        the header half way through the block, after CTYPE and CRVAL are
        already in it. One test for every layer that must not believe such a
        solution: the ASTAP parser, the file writer and the hub."""
        if self.cd11 is not None:
            terms = [0.0 if t is None else float(t)
                     for t in (self.cd11, self.cd12, self.cd21, self.cd22)]
            det = terms[0] * terms[3] - terms[1] * terms[2]
        elif self.cdelt1 is not None:
            d1 = float(self.cdelt1)
            d2 = d1 if self.cdelt2 is None else float(self.cdelt2)
            terms = [d1, d2]
            det = d1 * d2
            if self.crota2 is not None:
                terms.append(float(self.crota2))
        else:
            return False
        others = (self.crval1, self.crval2, self.crpix1, self.crpix2,
                  self.equinox)
        return det != 0.0 and all(
            math.isfinite(float(v)) for v in (det, *terms, *others))

    def pixel_scale_arcsec(self) -> float | None:
        """The plate scale in arcsec per pixel, the geometric mean of the two
        axes (``sqrt(|det|)`` of the CD matrix, or of the CDELT pair), or None
        when this solution has no usable scale. The scale ``has_usable_scale``
        vouches for, read the same way ``fitsio._apply_wcs`` writes it."""
        if not self.has_usable_scale():
            return None
        if self.cd11 is not None:
            cd = [0.0 if t is None else float(t)
                  for t in (self.cd11, self.cd12, self.cd21, self.cd22)]
            det = cd[0] * cd[3] - cd[1] * cd[2]
        else:
            d1 = float(self.cdelt1)
            det = d1 * (d1 if self.cdelt2 is None else float(self.cdelt2))
        return math.sqrt(abs(det)) * 3600.0


@dataclass
class SolveResult:
    success: bool
    ra_hours: float = 0.0
    dec_deg: float = 0.0
    rotation_deg: float = 0.0
    #: None when the solver did not state a scale (#973): a 0.0 here reads as
    #: a scale of zero, as a missing CROTA2 read as rotation 0 (#146). A
    #: consumer that prints it says "unknown"; one that computes with it
    #: falls back, as ``polar.native`` does with ``or``.
    pixel_scale_arcsec: float | None = None
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
