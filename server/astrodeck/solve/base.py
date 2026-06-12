from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SolveResult:
    success: bool
    ra_hours: float = 0.0
    dec_deg: float = 0.0
    rotation_deg: float = 0.0
    pixel_scale_arcsec: float = 0.0
    message: str = ""


class PlateSolver(ABC):
    name: str = "solver"

    @abstractmethod
    async def solve(self, fits_path: Path, *, ra_hint: float | None = None,
                    dec_hint: float | None = None,
                    fov_deg_hint: float | None = None) -> SolveResult: ...
