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

So check it against the pixels. This module reports only what a single frame
can honestly prove, and it names the slot when the caller tells it which slot
was in the beam — "the flag you ticked is contradicted" is the sentence that
closes the loop, and "light is reaching the sensor" is not.

WHAT IT PROVES
  * The frame is PINNED at the sensor ceiling — median at full well, or a
    large fraction of pixels clipped. A dark never looks like this.
  * The frame's BULK LEVEL sits above what the bias pedestal plus this
    EXPOSURE's worth of dark current can reach. The ceiling scales with
    ``exposure_s`` because one constant cannot cover both a 0.001 s bias and a
    600 s dark: set for the long dark it is nearly blind on the short one,
    which is how a leak one stop weaker than the observed daylight case would
    have sailed through.
  * The frame contains STARS — resolved, round, frame-wide point sources. This
    is the night-time form of the same defect (an empty slot with the scope
    uncapped, at 2 a.m. rather than at noon) and it is the one case where a
    single frame proves a leak outright rather than merely failing to
    contradict one. See SOURCE_SPREAD_MIN_PX for the measurements that set the
    bar and for the four dark-frame pathologies it had to be kept clear of.
  * The readout is railed at some sub-ceiling value, or every pixel is
    identical — a buffer, not a measurement.

WHAT IT MUST NOT FLAG, and why it doesn't
  * A warm sensor / a long exposure with real dark current. Both raise the
    median, and the level ceiling rises with exposure to let them through.
  * Amp glow in one corner. The level verdict keys off the MEDIAN and off the
    FRACTION of clipped pixels, neither of which a corner can move (a 72x72
    saturated patch is 0.5% of a 1024x1024 frame); the star verdict keys off
    sources spread over HALF THE FRAME, and glow is one corner of it.
  * Hot pixels. ``max`` is useless here and using it is the trap — the real
    overcast-sky fixture ``tests/fixtures/star_noise/blank_overcast.npz`` has
    max 65535 from ONE hot pixel, the exact same max as the daylight leak.
    Their medians differ by 250x. One saturated pixel is 0.0001% of that
    frame; the leak was 100%.
  * Hot COLUMNS and cosmic-ray tracks, which are the two things that do look
    like a frame full of sources. Both are linear; a star is round.

WHAT IT CANNOT PROVE, and does not claim
  * A faint STARLESS leak — twilight glow, a defocused leak, moonlight on a
    closed dome slit — that lifts the level by less than this exposure's
    dark-current allowance. Nothing in a single frame separates that from
    "bias + dark current" BY LEVEL: both are a raised, quiet pedestal. The
    signal that would settle it is a SECOND exposure — dark current scales
    with exposure time from the bias pedestal, a leak scales from zero — and
    the caller has those frames while this function sees one.
  * A leak whose stars this rig cannot resolve: a badly undersampled scope
    (sources under SOURCE_SPREAD_MIN_PX across), a trailed frame (sources
    above SOURCE_ECC_MAX), a sparse field, or a leak confined to less than
    half the frame. All four fall back to "plausible", never to "verified".

That asymmetry is deliberate and it runs one way: a negative verdict is
evidence, a positive one is only the absence of it. Which is why the accepted
sentence says *plausible* dark.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

import numpy as np

from .stars import Star, detect_stars

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

#: Median-over-full-well ceiling for a dark of DARK_RAMP_FULL_S or longer. A
#: frame whose bulk sits at a quarter of full well has no headroom to calibrate
#: anything, whatever put it there.
LEVEL_FRAC_MAX = 0.25

#: Median-over-full-well ceiling at ZERO exposure — the bias pedestal alone,
#: which is offset plus read noise and nothing else. 6% is 3900 ADU in a 16-bit
#: container, generous against the few hundred to ~2000 ADU that offset settings
#: on this project's cameras actually produce.
#:
#: This constant is why ``exposure_s`` exists. With one flat 25% ceiling,
#: ``judge_dark`` accepted a 1 s frame sitting at 15700 ADU as a "plausible
#: dark" — a level no bias and no 1 s dark can reach by dark current, and
#: exactly what the same empty slot produces at dusk instead of at noon. The
#: rig's daylight frames happened to be pinned at 65535 so the saturation test
#: caught them; one stop weaker and nothing would have.
BIAS_LEVEL_FRAC_MAX = 0.06

#: Exposure at which the dark-current allowance reaches LEVEL_FRAC_MAX. Dark
#: current is linear in time, so the ceiling ramps linearly from
#: BIAS_LEVEL_FRAC_MAX to LEVEL_FRAC_MAX across this span and then stops.
#:
#: 600 s is a CHOSEN ramp length, not a measured one — this module does not know
#: any camera's dark-current rate, and pretending to would be the same invented
#: number it exists to catch. What it is chosen against: the plan schema admits
#: exposures up to 3600 s (``sequence/models.py``), and every one of those gets
#: the same ceiling a 600 s dark gets, i.e. the loosest bar the module ever
#: applies. The ramp only ever makes SHORT frames stricter than the flat 25%,
#: which is where the flat ceiling was blind.
DARK_RAMP_FULL_S = 600.0

#: Fraction of pixels sitting at exactly the frame maximum at/above which the
#: readout is railed rather than merely peaky. A majority, because that is what
#: "the ADC is pinned" means; a real dark's maximum is one hot pixel. Guarded by
#: the frame MINIMUM piling up too — a low-gain bias whose read noise quantises
#: to two ADU values also puts half its pixels at the maximum, and it puts the
#: other half at the minimum. A rail piles at the top only.
PINNED_FRAC_MAX = 0.5

#: A source's flux divided by its peak's excess over the frame level: the
#: number of pixels' worth of peak the source spreads across, which for a
#: Gaussian PSF is 2*pi*sigma^2. THE discriminator between a star and the
#: bright things a genuine dark contains, measured here (1024x1024 frames, the
#: committed real star field against synthetic pathologies):
#:
#:     real star field (fixture)   30.8 / 37.6 / 44.5   (p10 / median / p90)
#:     5000 hot pixels             2.1 /  3.1 /  4.1
#:     20000 warm pixels           4.1 /  6.1 /  8.1
#:     amp glow, 8000 ADU corner   2.1 /  3.5 /  5.9
#:
#: A hot pixel and its neighbour spread over two pixels; a noise peak riding a
#: smooth glow gradient spreads over a few. 12 is a Gaussian of sigma 1.4 px —
#: below anything this rig resolves and 1.5x above the worst false source.
SOURCE_SPREAD_MIN_PX = 12.0

#: Eccentricity above which a source does not count as a star. This is the
#: guard for the two pathologies that DO pass the spread test, because both are
#: extended — in one direction only. Measured the same way:
#:
#:     real star field (fixture)   0.51 / 0.65 / 0.75   (p10 / median / p90)
#:     hot columns and rows        1.00 / 1.00 / 1.00
#:     80 cosmic-ray tracks        0.97 / 0.98 / 0.99
#:
#: Without it, a sensor with five hot columns produced 200 "stars" and 80
#: cosmic-ray tracks produced 150 — a realistic count for a 600 s dark on a
#: large sensor, i.e. a real dark condemned as a light leak.
SOURCE_ECC_MAX = 0.85

#: The frame is split into this many zones per side and a leak has to light at
#: least SOURCE_ZONE_FRAC_MIN of them. Sky fills the frame; the things a dark
#: frame goes wrong with are local — amp glow is one corner, a hot column is
#: one strip. On the real star field 16 of 16 zones are lit; the worst
#: synthetic pathology reaches 4.
SOURCE_ZONE_GRID = 4
SOURCE_ZONE_FRAC_MIN = 0.5

#: Sources needed before the frame is called a light leak. The margin either
#: side of it is the whole reason to trust the verdict: across every frame
#: tested, the weakest true leak (the real star field dimmed to a tenth of its
#: contrast above the pedestal) kept 51 sources over 15 zones, and the worst
#: dark-frame pathology (five hot columns and three hot rows) kept 5 over 4.
SOURCE_MIN = 12

#: THE AREA THOSE MEASUREMENTS WERE MADE ON, and the reason the constant above
#: is not a number that travels.
#:
#: Every figure quoted for SOURCE_MIN, SOURCE_SPREAD_MIN_PX and SOURCE_ECC_MAX
#: came off 1024x1024 fixtures. `SOURCE_MIN` is therefore a count PER THIS MANY
#: PIXELS, and applying it unscaled to a bigger sensor compares a 26-megapixel
#: frame's source count against a one-megapixel frame's bar.
#:
#: MEASURED on the rig, 2026-08-14, on six consecutive 180 s frames whose
#: background was identical to four decimal places (median 240.0, spread
#: 4.4478):
#:
#:     sources   6   8   9  19  10  13
#:     zones     5   6   5  11  10  10
#:     verdict   .   .   .  STARS  .  STARS
#:
#: Two genuinely black frames were condemned as light leaks by a detector
#: whose count was drifting with noise, and the DARKOK=False card that put on
#: them makes the calibration library EXCLUDE them. The check was throwing away
#: good darks.
SOURCE_REF_PIXELS = 1024 * 1024

#: Verdicts that mean LIGHT REACHED THE SENSOR, as opposed to the readout being
#: broken ("clipped", "constant", "empty"), which says nothing about the light
#: path. Only these can contradict an operator's ``filter_opaque`` tick — see
#: ``opaque_claim_refuted``.
LIGHT_VERDICTS = frozenset({"saturated", "elevated", "stars"})

#: Typographic characters this module's operator sentences use, mapped to the
#: ASCII a FITS header can hold. Anything still outside printable ASCII after
#: this is dropped rather than replaced with a marker: a stray '?' in the middle
#: of an evidence sentence reads as corruption, and the numbers are what matter.
_ASCII_FOLD = {"—": "-", "–": "-", "‘": "'", "’": "'",
               "“": '"', "”": '"', "…": "...", "°": " deg",
               "×": "x", "−": "-", "µ": "u", "σ": "sigma"}


def _ascii_card(text: str) -> str:
    """``text`` reduced to the printable ASCII a FITS header value permits."""
    out = "".join(_ASCII_FOLD.get(ch, ch) for ch in str(text))
    return "".join(ch for ch in out if " " <= ch <= "~")


@dataclass
class DarkResult:
    """Per-frame dark-plausibility verdict plus the raw metrics behind it.

    ``reason`` is written to be shown to the operator unchanged — it names the
    numbers that decided it, because "that is not a dark" without an ADU value
    is exactly the unfalsifiable claim this module exists to replace."""
    is_dark: bool
    verdict: str              # dark | saturated | elevated | stars | clipped | constant | empty
    level: float              # median ADU — the frame's bulk level
    level_frac: float         # level / saturation_adu
    level_frac_limit: float   # ceiling applied, from exposure_s; names why it fired
    spread: float             # robust sigma (MAD*1.4826) ADU
    peak: float               # max ADU (a hot pixel; never a verdict on its own)
    saturated_pixels: int     # pixels at/above the saturation ADU
    saturated_frac: float     # saturated_pixels / frame size
    saturation_adu: float     # ceiling used: camera MaxADU, else the container's
    sources: int | None       # star-like sources; None = not measured
    source_zones: int | None  # zones of SOURCE_ZONE_GRID^2 holding one; None = not measured
    opaque_slot: int | None   # wheel slot the operator flagged opaque, if told
    reason: str

    def to_dict(self) -> dict:
        return {
            "is_dark": self.is_dark,
            "verdict": self.verdict,
            "level": round(self.level, 1),
            "level_frac": round(self.level_frac, 4),
            "level_frac_limit": round(self.level_frac_limit, 4),
            "spread": round(self.spread, 1),
            "peak": round(self.peak, 1),
            "saturated_pixels": int(self.saturated_pixels),
            "saturated_frac": round(self.saturated_frac, 6),
            "saturation_adu": round(self.saturation_adu, 1),
            "sources": self.sources,
            "source_zones": self.source_zones,
            "opaque_slot": self.opaque_slot,
            "reason": self.reason,
        }

    def fits_cards(self) -> list[tuple[str, object, str]]:
        """``(keyword, value, comment)`` for the saved frame's FITS header.

        THE VERDICT HAS TO REACH THE FILE, and this is the only place that
        knows what to call it. A verdict that lives in a preview event and a
        log line stops the operator seeing tonight's white frames and does
        nothing about the damage path: ``CalibrationLibrary._bucket_raw``
        rebuilds masters by walking every ``*.fits`` under the capture root and
        keys them purely on headers, so a rejected dark with no card on it is
        stacked into a master dark on the next rebuild exactly as if it had
        passed.

        ``DARKOK`` is the card that closes that: the library skips a frame
        whose ``DARKOK`` is False and MUST default it to True when the card is
        absent, so every dark taken before this check existed still indexes.
        ``DARKWHY`` carries the operator sentence; astropy splits it over
        CONTINUE cards when it exceeds one card's 68 characters.

        The sentence is ASCII-folded on the way out. ``reason`` is written for a
        human and uses typographic punctuation; a FITS header value may hold
        only printable ASCII, and astropy raises on anything else — so an
        unfolded em dash would make the WRITE fail on exactly the frames this
        check rejects. The frame would be saved with no verdict at all, or not
        saved. Found by the first test that ran the check through a real
        capture, which is the argument for having one."""
        return [
            ("DARKOK", bool(self.is_dark), "Dark-plausibility check passed"),
            ("DARKCHK", _ascii_card(self.verdict), "Dark check verdict"),
            ("DARKWHY", _ascii_card(self.reason), "Dark check evidence, in ADU"),
        ]


def opaque_claim_refuted(result: DarkResult) -> int | None:
    """The filter slot whose ``filter_opaque`` tick this frame CONTRADICTS, or
    ``None``.

    The frames stopping is not the fix. ``FilterWheel.dark_slot()`` returns the
    first flagged-opaque slot and the sequence engine drives every dark and
    bias to it, so a flag that survives its own refutation routes tomorrow
    night's darks through the same empty slot. This is the slot to retract or
    surface as disputed.

    ``None`` unless light was actually measured: a railed readout, a constant
    buffer or an empty one are camera faults and say nothing whatever about
    what the slot is made of. And "contradicts" is the strongest claim
    available even then — light on the sensor with that slot commanded means
    either the slot is not blanked or the wheel never reached it. Which of the
    two is not in the pixels."""
    if result.is_dark or result.opaque_slot is None:
        return None
    return result.opaque_slot if result.verdict in LIGHT_VERDICTS else None


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


def _clean_exposure(exposure_s: float | int | None) -> float | None:
    """``exposure_s`` as a usable number, or ``None`` for "not stated".

    A negative or non-finite exposure is not a short exposure, it is a caller
    that does not know — and treating it as 0 would apply the module's
    STRICTEST ceiling to a frame nobody has measured."""
    if exposure_s is None:
        return None
    try:
        v = float(exposure_s)
    except (TypeError, ValueError):
        return None
    if not isfinite(v) or v < 0.0:
        return None
    return v


def _level_limit(exposure_s: float | None, level_frac_max: float,
                 bias_level_frac_max: float) -> float:
    """The median-over-full-well ceiling this exposure is allowed to reach.

    Linear in exposure because dark current is, from the bias pedestal at t=0
    to ``level_frac_max`` at DARK_RAMP_FULL_S and flat after. With no exposure
    stated it is ``level_frac_max`` — the LOOSEST bar, never a guessed-at
    stricter one, so telling ``judge_dark`` the exposure can only ever tighten
    the verdict and never invent a rejection out of missing metadata."""
    if exposure_s is None:
        return level_frac_max
    floor = min(bias_level_frac_max, level_frac_max)
    ramp = min(1.0, exposure_s / DARK_RAMP_FULL_S) if DARK_RAMP_FULL_S > 0 else 1.0
    return floor + (level_frac_max - floor) * ramp


def _exposure_phrase(exposure_s: float | None) -> str:
    if exposure_s is None:
        return "a dark of unstated length"
    if exposure_s <= 0.0:
        return "a bias"
    return f"a {exposure_s:g} s dark"


def _slot_label(slot: int, filter_name: str) -> str:
    name = (filter_name or "").strip()
    return f'slot {slot} ("{name}")' if name else f"slot {slot}"


def _light_clause(slot: int | None, filter_name: str) -> str:
    """The sentence that names the false assertion, when the caller said which
    slot was in the beam.

    Without it the copy reads identically for a cap left off and for a blackout
    flag on an empty slot, and only one of those is a thing the software can
    fix. It stops short of "that slot is empty": light on the sensor with that
    slot commanded is also what a wheel that never moved produces."""
    if slot is None:
        return "Light is reaching the sensor."
    return (f"Light is reaching the sensor through {_slot_label(slot, filter_name)}, "
            f"which is flagged blackout — so either that slot is not blanked or "
            f"the wheel never reached it.")


def source_floor(n_pixels: int, per_ref: int = SOURCE_MIN) -> int | None:
    """Sources a leak must reach on a frame of ``n_pixels``, or ``None`` when
    the star test cannot be applied to a frame this big at all.

    ``None`` IS THE POINT, and it is why this returns an Optional rather than a
    bigger number. Scaling the floor by area is only half an answer, because
    ``detect_stars`` stops at ``DEFAULT_MAX_STARS``: past about seventeen
    megapixels the scaled bar is above the most sources the detector can ever
    return, so the test could not fire however bright the leak. A test that
    cannot fire must SAY it is not testing, not sit there looking like a pass.

    That the two numbers cross is not a coincidence to be tuned away. MEASURED
    on 2026-08-15: one frame through every slot of the rig's wheel, same field,
    same night, 26 MP sensor. Filtered source count and zones:

        L 184/16   R 200/16   G 199/16   B 200/16
        S  49/10   Ha   7/5   Oiii 65/14   Dark 17/9

    The star test is wrong in BOTH directions here. The blackout slot - a frame
    with no light in it at all - scores 17 sources over 9 zones and is CONDEMNED
    as a leak. A real Ha frame of the same field scores 7 over 5 and PASSES as a
    plausible dark. The distributions are inverted, so no threshold on this
    number separates them.

    NOT because the detector is blind, which is what this docstring first said.
    ``detect_stars`` returns its full 200 on every one of those real frames; it
    is the spread and roundness filter in ``_sky_sources`` that collapses the
    count, and it collapses hardest exactly where a frame has few BRIGHT stars -
    which is what narrowband looks like. The earlier "19 sources on a real Ha
    sub" reading came from a night whose own log records failed autofocus,
    failed guiding and a plate solve refused for "Not enough stars": a bad
    frame, generalized into a bad detector.

    So the abstention stands, on stronger evidence than it was written with.
    """
    from .stars import DEFAULT_MAX_STARS
    floor = max(per_ref, int(round(per_ref * n_pixels / SOURCE_REF_PIXELS)))
    return None if floor > DEFAULT_MAX_STARS else floor


def _sky_sources(fimg: np.ndarray, level: float, stars: list[Star] | None,
                 spread_min: float, ecc_max: float,
                 grid: int) -> tuple[int | None, int | None]:
    """``(star-like sources, zones holding one)``, or ``(None, None)`` when the
    frame is the wrong shape to ask.

    Three conditions, each one earning its place against a measured
    dark-frame pathology (see the constants): the source has to SPREAD like a
    PSF rather than like a hot pixel, be ROUND rather than a column or a track,
    and — collectively — cover the frame rather than one corner of it."""
    if fimg.ndim != 2 or min(fimg.shape) < 16:
        return None, None
    if stars is None:
        stars = detect_stars(fimg)
    h, w = fimg.shape
    zones: set[tuple[int, int]] = set()
    n = 0
    for s in stars:
        # Excess over the FRAME level, not over a local background: under amp
        # glow the local background is high, which shrinks the ratio and makes
        # this test stricter exactly where false sources come from.
        if s.flux / max(s.peak - level, 1.0) < spread_min or s.ecc > ecc_max:
            continue
        n += 1
        zones.add((min(grid - 1, int(s.y / h * grid)),
                   min(grid - 1, int(s.x / w * grid))))
    return n, len(zones)


def judge_dark(
    data: np.ndarray,
    *,
    full_well: int | float | None = None,
    exposure_s: float | int | None = None,
    stars: list[Star] | None = None,
    opaque_slot: int | None = None,
    filter_name: str = "",
    saturated_frac_max: float = SATURATED_FRAC_MAX,
    level_frac_max: float = LEVEL_FRAC_MAX,
    bias_level_frac_max: float = BIAS_LEVEL_FRAC_MAX,
    pinned_frac_max: float = PINNED_FRAC_MAX,
    source_min: int = SOURCE_MIN,
) -> DarkResult:
    """Judge whether a linear frame plausibly IS a dark (or bias).

    Args:
        data: 2D linear frame as read off the sensor. Must be linear and
            unstretched — every threshold here is in ADU.
        full_well: the camera's saturation ADU (Alpaca MaxADU, carried on
            ``CameraFrame.full_well``). ``None``/0 falls back to the pixel
            container's ceiling; the pinned-readout test still works without it.
        exposure_s: the frame's exposure. Sets the level ceiling (see
            ``_level_limit``); omitting it applies the loosest ceiling, which
            is nearly blind on a short frame.
        stars: pre-detected stars, if the caller already ran ``detect_stars`` on
            THIS array — the capture path has them, and reusing them makes the
            star test free. ``None`` detects here, and only if the cheap
            statistics have not already settled the verdict.
        opaque_slot: the wheel slot this frame was shot through, when the
            operator has flagged it opaque. Given it, a rejection names the
            contradicted flag instead of guessing at a cap left off, and
            ``opaque_claim_refuted`` says which flag to retract.
        filter_name: that slot's label, for the operator sentence.
        saturated_frac_max: clipped-pixel fraction at/above which the frame is
            rejected. Raise it for a sensor with pathological amp glow.
        level_frac_max: median-over-full-well ceiling for a long dark.
        bias_level_frac_max: the same ceiling at zero exposure. Raise it for a
            camera run at an unusually high offset.
        pinned_frac_max: fraction of pixels at exactly the frame maximum
            at/above which the readout is judged railed.
        source_min: star-like sources needed to call the frame a light leak.

    Returns:
        DarkResult. ``is_dark`` False always carries a ``reason`` naming the
        numbers; ``is_dark`` True means "nothing in this frame contradicts a
        dark", not "no light reached the sensor" (see the module docstring).
    """
    img = np.asarray(data)
    sat = (float(full_well) if full_well and float(full_well) > 0
           else _container_ceiling(img))
    exp = _clean_exposure(exposure_s)
    limit = _level_limit(exp, level_frac_max, bias_level_frac_max)

    if img.size == 0:
        return DarkResult(
            is_dark=False, verdict="empty", level=0.0, level_frac=0.0,
            level_frac_limit=limit, spread=0.0, peak=0.0, saturated_pixels=0,
            saturated_frac=0.0, saturation_adu=sat, sources=None,
            source_zones=None, opaque_slot=opaque_slot,
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
    light = _light_clause(opaque_slot, filter_name)

    def _result(is_dark: bool, verdict: str, reason: str,
                sources: int | None = None,
                source_zones: int | None = None) -> DarkResult:
        return DarkResult(
            is_dark=is_dark, verdict=verdict, level=level, level_frac=level_frac,
            level_frac_limit=limit, spread=spread, peak=peak,
            saturated_pixels=n_sat, saturated_frac=sat_frac, saturation_adu=sat,
            sources=sources, source_zones=source_zones, opaque_slot=opaque_slot,
            reason=reason,
        )

    # 1. Clipped at full well. Ordered first because it is the observed defect
    #    and the most specific thing that can be said about the frame. Every
    #    test before the star test is a cheap reduction, so a frame settled
    #    here never pays for a detection pass.
    if sat_frac >= saturated_frac_max:
        if level >= sat_at:
            return _result(
                False, "saturated",
                f"not a dark: the whole frame is saturated — median {level:.0f} "
                f"ADU is full well and {_pct(sat_frac)} of pixels are clipped. "
                f"{light}",
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

    # 4. Flooded but not yet clipped — the same leak an hour before sunset, or
    #    the same leak at any hour on a frame short enough that dark current
    #    explains nothing.
    if level_frac >= limit:
        return _result(
            False, "elevated",
            f"not a dark: median {level:.0f} ADU is {_pct(level_frac)} of full "
            f"well ({sat:.0f} ADU), over the {_pct(limit)} ceiling that "
            f"{_exposure_phrase(exp)} reaches from the bias pedestal plus dark "
            f"current. {light}",
        )

    # 5. Stars. The night-time form of the defect, and the only test here that
    #    PROVES a leak rather than failing to contradict one: dark current does
    #    not make round, resolved, frame-wide point sources.
    n_src, n_zones = _sky_sources(fimg, level, stars, SOURCE_SPREAD_MIN_PX,
                                  SOURCE_ECC_MAX, SOURCE_ZONE_GRID)
    zone_total = SOURCE_ZONE_GRID * SOURCE_ZONE_GRID
    # SCALED TO THIS FRAME, and None when it cannot be scaled at all. Every
    # source number here was measured on a 1024x1024 fixture; `source_min` is a
    # count per that area, not a constant that travels to a 26 MP sensor.
    floor = source_floor(fimg.size, source_min)
    if (floor is not None and n_src is not None and n_src >= floor
            and n_zones >= SOURCE_ZONE_FRAC_MIN * zone_total):
        return _result(
            False, "stars",
            f"not a dark: {n_src} round, resolved sources spread across "
            f"{n_zones} of the frame's {zone_total} zones, on a frame whose "
            f"median is only {level:.0f} ADU. Dark current does not make stars. "
            f"{light}",
            sources=n_src, source_zones=n_zones,
        )

    # Nothing contradicts a dark. "plausible", not "verified": see the module
    # docstring on what one frame cannot settle.
    # THE ABSTENTION IS SAID OUT LOUD. A frame too big for the star test to
    # mean anything reads exactly like a frame that passed it, and the operator
    # who later finds a leaked dark in their library deserves to know which of
    # the two this was.
    if n_src is None:
        found = "no source test on a frame this shape"
    elif floor is None:
        found = (f"{n_src} source(s), but the star test does not apply at "
                 f"{fimg.size / 1e6:.0f} MP - it is calibrated on 1 MP frames "
                 f"and the detector cannot return enough sources to clear the "
                 f"scaled bar")
    else:
        found = "no star-like sources"
    return _result(
        True, "dark",
        f"plausible dark: median {level:.0f} ADU ({_pct(level_frac)} of full "
        f"well, ceiling {_pct(limit)} for {_exposure_phrase(exp)}), spread "
        f"{spread:.0f} ADU, {_pixels(n_sat)} clipped, {found}.",
        sources=n_src, source_zones=n_zones,
    )
