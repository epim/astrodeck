from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class GuideStats:
    guiding: bool = False
    rms_ra: float = 0.0
    rms_dec: float = 0.0
    rms_total: float = 0.0
    snr: float = 0.0
    recent: list[dict] = field(default_factory=list)  # [{t, ra, dec}] arcsec


class Guider(ABC):
    name: str = "guider"
    connected: bool = False

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def start_guiding(self) -> None:
        """Select star, calibrate if needed, begin guiding; returns once settled."""

    @abstractmethod
    async def stop_guiding(self) -> None: ...

    @abstractmethod
    async def dither(self, pixels: float = 3.0) -> None:
        """Dither and wait for settle."""

    @abstractmethod
    def stats(self) -> GuideStats: ...

    async def is_active(self) -> bool:
        """Whether the guider is really guiding right now (queries the backend
        where possible, rather than a local flag) — used to detect a lost star."""
        return self.stats().guiding
