"""Is this "dark" actually dark?

The filter wheel's blackout slot is a USER ASSERTION. No wheel reports which
slot carries no glass — the operator ticks ``filter_opaque`` and from then on
every dark and bias is shot through whatever that slot really holds. On
2026-08-01 the ticked slot turned out to be EMPTY rather than blanked, and
darks taken through it in daylight came back

    1s  bin1   median 65535   max 65535
    4s  bin1   median 65535   max 65535
    10s bin1   median 65535   max 65535

— three white frames, filed as a dark library, with nothing anywhere saying
so. Subtract one of those from a light and the light is gone. An assertion the
software never checks is the same class of defect as a focuser reporting
success on a move it never made.

So check it against the pixels. This module is deliberately NARROW: it reports
only what a single frame can honestly prove.

WHAT IT PROVES
  * The frame is PINNED at the sensor ceiling — median at full well, or a
    large fraction of pixels clipped. A dark never looks like this.
  * The frame's BULK LEVEL sits far above any bias pedestal. A dark sits at
    the bias pedestal plus dark current; at a quarter of full well the frame
    has no headroom left to calibrate anything, whatever put it there.
  * The readout is railed at some sub-ceiling value, or every pixel is
    identical — a buffer, not a measurement.

WHAT IT MUST NOT FLAG, and why it doesn't
  * A warm sensor / a long exposure with real dark current. Both raise the
    median, but into the low percent of full well — nowhere near
    ``level_frac_max``.
  * Amp glow in one corner. The verdict keys off the MEDIAN and off the
    FRACTION of clipped pixels, both of which a corner cannot move: a 72x72
    saturated glow patch is 0.5% of a 1024x1024 frame.
  * Hot pixels. ``max`` is useless here and using it is the trap — the real
    overcast-sky fixture ``tests/fixtures/star_noise/blank_overcast.npz`` has
    max 65535 from ONE hot pixel, the exact same max as the daylight leak.
    Their medians differ by 250x. One saturated pixel is 0.0001% of that
    frame; the leak was 100%.

WHAT IT CANNOT PROVE, and does not claim
    A FAINT leak. Nothing in a single frame separates "bias + dark current"
    from "bias + a little light": both are a raised, quiet pedestal, and the
    real star-field fixture (median 310 ADU on a 4 s Milky Way sub) is
    genuinely indistinguishable from a bias by level. A verdict of ``dark``
    here means "nothing in this frame contradicts it", which is why the
    sentence says *plausible* dark. The signal that would settle it is a
    SECOND exposure: dark current scales with exposure time from the bias
    pedestal, a leak scales from zero. The caller has those frames; this
    function sees one.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: Saturation ADU assumed when the camera reports no MaxADU and the pixels are
#: not an integer type. Every camera in this project delivers a 16-bit
#: container; a 12/14-bit sensor inside one is caught by the pinned-readout
#: test instead, which needs no metadata at all.
DEFAULT_SATURATION_ADU = 65535.0

#: A pixel counts as clipped at/above this fraction of the saturation ADU.
SATURATED_TOL = 0.999

#: Clipped-pixel fraction at/above which the frame is not a dark. Sits ~2x
#: above the worst plausible amp glow (a 72x72 saturated corner on a 1024x1024
#: frame is 0.49%) and 50x below the observed daylight leak (100%).
SATURATED_FRAC_MAX = 0.01

#: Median-over-full-well at/above which the frame is not a dark. A hot 300 s
#: dark at high gain still lands in the low percent; a frame whose bulk sits at
#: a quarter of full well has no headroom to calibrate anything.
LEVEL_FRAC_MAX = 0.25

#: Fraction of pixels sitting at exactly the frame maximum at/above which the
#: readout is railed rather than merely peaky. A majority, because that is what
#: "the ADC is pinned" means; a real dark's maximum is one hot pixel. Guarded by
#: the frame MINIMUM piling up too — a low-gain bias whose read noise quantises
#: to two ADU values also puts half its pixels at the maximum, and it puts the
#: other half at the minimum. A rail piles at the top only.
PINNED_FRAC_MAX = 0.5


@dataclass
class DarkResult:
    """Per-frame dark-plausibility verdict plus the raw metrics behind it.

    ``reason`` is written to be shown to the operator unchanged — it names the
    numbers that decided it, because "that is not a dark" without an ADU value
    is exactly the unfalsifiable claim this module exists to replace."""
    is_dark: bool
    verdict: str              # dark | saturated | elevated | clipped | constant | empty
    level: float              # median ADU — the frame's bulk level
    level_frac: float         # level / saturation_adu
    spread: float             # robust sigma (MAD*1.4826) ADU
    peak: float               # max ADU (a hot pixel; never a verdict on its own)
    saturated_pixels: int     # pixels at/above the saturation ADU
    saturated_frac: float     # saturated_pixels / frame size
    saturation_adu: float     # ceiling used: camera MaxADU, else the container's
    reason: str

    def to_dict(self) -> dict:
        return {
            "is_dark": self.is_dark,
            "verdict": self.verdict,
            "level": round(self.level, 1),
            "level_frac": round(self.level_frac, 4),
            "spread": round(self.spread, 1),
            "peak": round(self.peak, 1),
            "saturated_pixels": int(self.saturated_pixels),
            "saturated_frac": round(self.saturated_frac, 6),
            "saturation_adu": round(self.saturation_adu, 1),
            "reason": self.reason,
        }


def _container_ceiling(img: np.ndarray) -> float:
    """Saturation ADU inferred from the pixel container when the camera did not
    report MaxADU. Deliberately the CONTAINER, not a guess at the sensor: a
    12-bit sensor in a 16-bit container is caught by the pinned-readout test,
    which is metadata-free, rather than by a made-up ceiling."""
    if np.issubdtype(img.dtype, np.integer):
        return float(np.iinfo(img.dtype).max)
    return DEFAULT_SATURATION_ADU


def _pct(frac: float) -> str:
    """Percent for an operator-facing sentence: keeps a full-frame 100% short
    and a one-hot-pixel 0.0001% from rounding to a lie ("0%")."""
    p = frac * 100.0
    if p >= 10:
        return f"{p:.0f}%"
    if p >= 1:
        return f"{p:.1f}%"
    if p > 0:
        return f"{p:.2g}%"
    return "0%"


def _pixels(n: int) -> str:
    return f"{n} pixel" if n == 1 else f"{n} pixels"


def judge_dark(
    data: np.ndarray,
    *,
    full_well: int | float | None = None,
    saturated_frac_max: float = SATURATED_FRAC_MAX,
    level_frac_max: float = LEVEL_FRAC_MAX,
    pinned_frac_max: float = PINNED_FRAC_MAX,
) -> DarkResult:
    """Judge whether a linear frame plausibly IS a dark (or bias).

    Args:
        data: 2D linear frame as read off the sensor. Must be linear and
            unstretched — every threshold here is in ADU.
        full_well: the camera's saturation ADU (Alpaca MaxADU, carried on
            ``CameraFrame.full_well``). ``None``/0 falls back to the pixel
            container's ceiling; the pinned-readout test still works without it.
        saturated_frac_max: clipped-pixel fraction at/above which the frame is
            rejected. Raise it for a sensor with pathological amp glow.
        level_frac_max: median-over-full-well at/above which the frame is
            rejected as light-flooded.
        pinned_frac_max: fraction of pixels at exactly the frame maximum
            at/above which the readout is judged railed.

    Returns:
        DarkResult. ``is_dark`` False always carries a ``reason`` naming the
        numbers; ``is_dark`` True means "nothing in this frame contradicts a
        dark", not "no light reached the sensor" (see the module docstring).
    """
    img = np.asarray(data)
    sat = (float(full_well) if full_well and float(full_well) > 0
           else _container_ceiling(img))

    if img.size == 0:
        return DarkResult(
            is_dark=False, verdict="empty", level=0.0, level_frac=0.0,
            spread=0.0, peak=0.0, saturated_pixels=0, saturated_frac=0.0,
            saturation_adu=sat,
            reason="not a dark: the frame has no pixels — the camera returned "
                   "an empty buffer",
        )

    # float64 before any subtraction: these arrive as uint16 and `img - level`
    # on unsigned pixels wraps 0-1 to 65535, which would fabricate a saturated
    # frame out of a perfectly good bias.
    fimg = img.astype(np.float64)
    level = float(np.median(fimg))
    # Same MAD convention as clouds._background, so "spread" means the same
    # number of ADU everywhere in this package.
    spread = float(np.median(np.abs(fimg - level))) * 1.4826
    peak = float(fimg.max())
    floor = float(fimg.min())

    sat_at = sat * SATURATED_TOL
    n_sat = int(np.count_nonzero(fimg >= sat_at))
    sat_frac = n_sat / float(img.size)
    level_frac = level / sat if sat > 0 else 0.0
    n_peak = int(np.count_nonzero(fimg >= peak))
    pinned_frac = n_peak / float(img.size)
    floor_frac = int(np.count_nonzero(fimg <= floor)) / float(img.size)

    def _result(is_dark: bool, verdict: str, reason: str) -> DarkResult:
        return DarkResult(
            is_dark=is_dark, verdict=verdict, level=level, level_frac=level_frac,
            spread=spread, peak=peak, saturated_pixels=n_sat,
            saturated_frac=sat_frac, saturation_adu=sat, reason=reason,
        )

    # 1. Clipped at full well. Ordered first because it is the observed defect
    #    and the most specific thing that can be said about the frame.
    if sat_frac >= saturated_frac_max:
        if level >= sat_at:
            return _result(
                False, "saturated",
                f"not a dark: the whole frame is saturated — median {level:.0f} "
                f"ADU is full well and {_pct(sat_frac)} of pixels are clipped. "
                f"Light is reaching the sensor.",
            )
        return _result(
            False, "saturated",
            f"not a dark: {_pct(sat_frac)} of the frame ({_pixels(n_sat)}) is "
            f"clipped at {sat:.0f} ADU while the median is only {level:.0f} ADU. "
            f"Clipped pixels carry no dark signal to subtract.",
        )

    # 2. Every pixel identical. Not a dark and not a light — not a measurement.
    #    Tested on min==max, not on spread==0: a railed 12-bit readout also has
    #    a zero MAD but a real minimum well below its rail (case 3).
    if floor == peak:
        return _result(
            False, "constant",
            f"not a dark: every one of the {img.size} pixels reads exactly "
            f"{level:.0f} ADU. A sensor with any read noise cannot produce "
            f"that — this frame is a buffer, not an exposure.",
        )

    # 3. Readout railed below the container ceiling — a 12/14-bit sensor in a
    #    16-bit container whose MaxADU we were never told, or a driver clamp.
    #    Needs no camera metadata: a real dark's maximum is one hot pixel, so a
    #    MAJORITY of pixels sharing the maximum can only be a rail — unless the
    #    minimum is equally piled, which is a quantised low-gain bias (two ADU
    #    values, half the pixels in each) and a perfectly good dark.
    if (pinned_frac >= pinned_frac_max and floor_frac < pinned_frac_max
            and peak < sat_at):
        return _result(
            False, "clipped",
            f"not a dark: {_pct(pinned_frac)} of the frame is pinned at exactly "
            f"{peak:.0f} ADU. The readout is railed there, so this frame "
            f"measures the rail, not the dark signal.",
        )

    # 4. Flooded but not yet clipped — the same leak an hour before sunset.
    if level_frac >= level_frac_max:
        return _result(
            False, "elevated",
            f"not a dark: median {level:.0f} ADU is {_pct(level_frac)} of full "
            f"well ({sat:.0f} ADU). A dark sits at the bias pedestal, a few "
            f"percent at most — light is reaching the sensor.",
        )

    # Nothing contradicts a dark. "plausible", not "verified": see the module
    # docstring on what one frame cannot settle.
    return _result(
        True, "dark",
        f"plausible dark: median {level:.0f} ADU ({_pct(level_frac)} of full "
        f"well), spread {spread:.0f} ADU, {_pixels(n_sat)} clipped.",
    )
