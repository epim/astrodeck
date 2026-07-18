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

    #: Provider FAMILY this guider belongs to, used by
    #: ``providers.actual_guide_family`` to report the ACTUAL serving guider on
    #: the status badge (and so tag the UI's same-night RMS ticks truthfully —
    #: P5-T1 fix round C1). The vendor-neutral default is ``"backend"`` (the
    #: PHD2/NINA bridge family); the native-engine guiders override to
    #: ``"native"``. Never the finer ``astrodeck``/``sim`` badge split — that is
    #: a RIG property (real vs simulated devices), decided by the resolver.
    provider_family: str = "backend"

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

    #: Whether this guider can flip its calibration for a meridian flip. Backends
    #: that support it (PHD2) override to True; the default is a safe no-op.
    can_flip_calibration: bool = False

    async def flip_calibration(self) -> bool:
        """Flip the guider's calibration across a meridian flip (P1-6).

        On a German equatorial mount, after the mount flips to the far side of
        the pier the guide directions are reversed; guiding the OLD calibration
        runs the mount away from the star (runaway). ``hub.meridian_flip`` calls
        this between stopping and restarting guiding.

        Vendor-neutral default: a guider that cannot flip its calibration is a
        clear, logged NO-OP returning ``False`` (never an error), so the meridian
        flip still completes — it just relies on a fresh calibration on restart.
        Backends that can flip override this and return ``True`` on success."""
        from ..events import bus
        bus.log("warning",
                f"{self.name}: cannot flip guider calibration for the meridian "
                "flip; will rely on a fresh calibration after the flip", "guide")
        return False

    async def guide_frame(self) -> bytes | None:
        """A small auto-stretched PNG of the guide-star region for the live UI,
        or ``None`` when no frame is available (no guider, no current star image,
        backend doesn't expose one). Vendor-neutral: every guider implements this
        against whatever its backend can surface (PHD2 ``get_star_image`` RPC,
        a synthesized sim frame, ...). Implementations must be cheap and must
        NEVER raise — they return ``None`` on any failure so the endpoint can
        answer 404 instead of 500."""
        return None
