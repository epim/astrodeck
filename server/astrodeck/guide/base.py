from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TypedDict


class SettleParams(TypedDict, total=False):
    """Dither settle criteria (UX-24). ``pixels``: the star must stay within this
    many px; ``time``: for at least this many seconds; ``timeout``: give up
    waiting after this many seconds. All optional — a missing key keeps the
    guider's default."""
    pixels: float
    time: float
    timeout: float


@dataclass
class GuideStats:
    guiding: bool = False
    rms_ra: float = 0.0
    rms_dec: float = 0.0
    rms_total: float = 0.0
    snr: float = 0.0
    recent: list[dict] = field(default_factory=list)  # [{t, ra, dec}] — unit per is_arcsec
    #: Whether ``rms_*`` / ``recent`` are in true ARCSEC (an image scale is
    #: known) or in guide-camera PIXELS. The native guider falls back to a 1:1
    #: scale when no guide-scope focal length is configured, so its raw errors
    #: are pixels — reporting them as arcsec is a lie. The UI switches its unit
    #: label (″ vs px) on this flag. (UX-15)
    is_arcsec: bool = False
    #: The arcsec/pixel image scale used to convert the engine's pixel errors,
    #: or 0.0 when unknown (see ``is_arcsec``). Purely informational for the UI.
    image_scale: float = 0.0
    #: Scale actually used to size calibration pulses, including an assumption.
    calibration_image_scale: float | None = None
    image_scale_known: bool = False
    #: Plain-language narration phase (NOV-7 design doc §1.3):
    #: ``"idle" | "finding" | "calibrating" | "settling" | "guiding" | "lost"``,
    #: or ``""`` when unknown. Only ``NativeGuider`` fills this richly; the
    #: PHD2/NINA bridge guider leaves it ``""`` and the UI narration falls back
    #: to the ``guiding`` bool (honest — the bridge doesn't expose these
    #: steps). Additive field: flows through ``hub.py``'s ``stats().__dict__``
    #: spread and the ``bus.publish("guide", **stats().__dict__)`` calls for
    #: free.
    phase: str = ""
    #: GN-03. How many times this guiding session has LOST the star and then
    #: re-established lock on a (possibly different) star. The RMS above is
    #: measured around whatever star is currently locked, so it resets to zero
    #: across a re-lock and cannot see the jump: on 2026-09-06 the native
    #: guider reported 2.3 arcsec while the field walked 40 arcmin in half an
    #: hour, one re-lock at a time. ``relock_arcsec_total`` is the summed
    #: displacement between consecutive locks and ``relock_events`` the last 50
    #: of them as ``[{t, arcsec}]`` — SAME unit contract as ``recent``, so
    #: ``is_arcsec`` governs whether "arcsec" really is arcsec or raw
    #: guide-camera pixels. Only a guider that can see its own lock position
    #: fills these; the PHD2/NINA bridge leaves them at these defaults, and the
    #: sequence engine's re-lock hold is a no-op against them.
    relocks: int = 0
    relock_arcsec_total: float = 0.0
    relock_events: list[dict] = field(default_factory=list)


def rms_total_arcsec(stats: "GuideStats | None") -> float | None:
    """``stats.rms_total`` expressed in ARCSEC, or ``None`` when it cannot be.

    The guide RMS is reported in arcsec only when an image scale is known;
    otherwise it is raw guide-camera PIXELS (``is_arcsec=False``) — and the
    native guider's default ``optics.guide_focal_length_mm`` is unset, so pixels
    is the common case. Every consumer that compares against an ARCSEC threshold
    (the sequencer's ``max_guide_rms`` frame-reject gate, the ``on_guide_rms_above``
    instruction trigger) must go through here: comparing 1.5 "arcsec" against a
    pixel number is a gate ~3x looser than the one the user typed on a typical
    240 mm / 3.76 um guide scope (UX #11).

    Conversion uses ``image_scale`` (arcsec/pixel) when the guider published one;
    with neither flag nor scale the answer is genuinely unknown and ``None`` is
    the honest return — callers SKIP the gate rather than judge in the wrong
    unit. Pure; mirrors ``InstructionsPanel``'s client-side unit handling."""
    if stats is None:
        return None
    rms = getattr(stats, "rms_total", None)
    if rms is None:
        return None
    try:
        rms = float(rms)
    except (TypeError, ValueError):
        return None
    if rms != rms:                                   # NaN
        return None
    if getattr(stats, "is_arcsec", False):
        return rms
    scale = getattr(stats, "image_scale", 0.0) or 0.0
    try:
        scale = float(scale)
    except (TypeError, ValueError):
        return None
    return rms * scale if scale > 0 else None


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

    async def needs_calibration(self) -> bool | None:
        """Will the NEXT ``start_guiding`` have to drive a CALIBRATION WALK?

        ``True`` = yes, no usable calibration is on file for this session;
        ``False`` = a calibration will be reused and the start is the quick
        one; ``None`` = this guider cannot say.

        WHY THE ENGINE ASKS. Every engine await on a device is bounded so a
        wedged transport cannot hang the night, and ``start_guiding`` was
        bounded at 180 s — sized for "select a star and settle", which is what
        it costs when a calibration is on file. A fresh walk on a real mount is
        three to five minutes, and on 2026-09-07 at 03:39 a limit recovery
        changed the pier side, GN-01 discarded the calibration, and that bound
        CUT THE WALK HALF WAY. The run continued unguided for twenty minutes
        and lost a frame to trailing. A backstop that fires inside the normal
        duration of the thing it wraps is not a backstop.

        So the answer picks the bound. ``None`` is treated as ``True`` by the
        sequence engine, deliberately: a guider that cannot say may still
        calibrate (PHD2's ``guide`` RPC does whenever PHD2 has none on file),
        and being wrong the roomy way costs a few extra minutes ONCE on a
        genuinely wedged guider, while being wrong the tight way costs the
        calibration and the target's guiding.

        Must be cheap and must never raise — the engine treats any failure as
        ``None``.
        """
        return None

    @abstractmethod
    async def stop_guiding(self) -> None: ...

    @abstractmethod
    async def dither(self, pixels: float = 3.0,
                     settle: "SettleParams | None" = None) -> None:
        """Dither by ``pixels`` and wait for settle.

        ``settle`` (UX-24) optionally overrides the settle criteria — how still
        the star must be (``pixels`` within ``time`` seconds) and how long to
        wait (``timeout``). None keeps each guider's defaults. The bridge
        (PHD2/NINA) honors all three; the native engine self-manages the
        pixels/time criteria and honors the timeout."""

    @abstractmethod
    def stats(self) -> GuideStats: ...

    def calibration_report(self) -> dict | None:
        """A structured calibration report for the UI (UX-23): pass/fail plus the
        geometry that reveals a bad/flipped calibration (orthogonality error,
        declination, pier side) and any human-readable advisories. Vendor-neutral
        default: None — a guider that owns its calibration opaquely (PHD2/NINA
        surface it their own way) exposes none, and the UI simply omits the panel."""
        return None

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
