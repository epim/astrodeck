"""Did any light reach the sensor? The question a failed plate solve never asked.

THE DEFECT (#251). On the night of 2026-09-24/25 auto-resume retried its
recovery solve every ten minutes for more than two hours, and every attempt
logged "plate solve failed: Not enough stars." The sky was clear (7-10 %
cloud, all high, dewpoint 11.6 C under the air). The last solve frame (12 s,
gain 200, bin 2, filter L, sensor 18.5 C) read median 251, p1 231, p99 273,
std 32.6: the statistics of a dark frame. No sky background above bias and
dark, no gradient, no stars. The optic was capped or covered, and a covered
optic says "not enough stars" in exactly the words a cloudy sky does. Nothing
read the frame that already held the difference, and nobody was told.

WHAT TELLS THEM APART. Cloud, however thick over a lit landscape, lifts the
background above the level the sensor reads with no light on it; a cap does
not. So a failed solve's frame is judged by its MEDIAN against a REFERENCE:
the median this camera reads, at this exposure, gain, offset, binning and
temperature, with no light on it. In order of preference:

  1. the dark library's master for those settings, found by the same
     ``calibration.matcher.best_master`` rule that picks the dark a light is
     calibrated with;
  2. otherwise a bias master at that gain, offset and binning, plus the
     smallest dark current the doubling law allows (see ``reference_for``);
  3. otherwise, when the library was read and holds neither, the same as 2
     with a frame the camera shoots itself, at its shortest exposure, in
     place of the bias master (THE SELF-REFERENCE, below);
  4. otherwise no reference, and no verdict.

The verdicts (``classify``):

  * the median within ``K_SIGMA`` sigma of the reference: NO LIGHT, "the optic
    is capped, covered or obstructed";
  * above the MOST the no-light level could be (the reference's ``ceiling``)
    by more than that: light reached the sensor, so the solver's own words
    ("not enough stars") stand, which is cloud or haze;
  * above the reference but not past its ceiling: no verdict (see below);
  * below it by more than that: the reference does not describe this frame
    (the offset changed, or the master is wrong), so no verdict;
  * a level or a reference that is not a number (a sensor temperature read as
    NaN): no verdict, never no light by default;
  * no reference: no verdict, and the failure keeps today's words and says no
    level check was possible.

A REFERENCE HAS A FLOOR AND A CEILING. A dark master IS the no-light level,
so the two are the same number. A bias-based reference is not: it is the
bias plus the LEAST dark current the doubling law allows, a floor, and the
true level can sit anywhere up to the MOST it allows (the bias alone, with no
dark to bound the dark current by, has no ceiling at all). A frame between
the two may be light and may be dark current the floor left out. Called
"light reaching the sensor", as it was at first, a frame shaped like #251's
(a capped optic at 18.5 C, 11 ADU over a 240 ADU bias, the least #262 asks
an operator to shoot) was worded as the opposite of the truth, and a cloud
verdict ends ResumeArm's no-light spell.

IT NEVER SAYS "NO LIGHT" WITHOUT A REFERENCE. A median alone cannot say it: a
bias pedestal is whatever the offset setting made it, and a camera's dark level
at 18.5 C is not its dark level at -10 C.

THE ERRORS ARE NOT SYMMETRIC, and every choice below leans the same way. A
false "no light" sends one alarming push alert and, when a dark master stood
behind it, backs auto-resume off to an hourly retry under a sky that may clear
in ten minutes (on any other reference the retry stays at ten minutes, #308).
A missed "no light" costs exactly what #251 cost, which is what the rig does
today. So every uncertainty this module bounds from evidence NARROWS the
no-light verdict and never widens it: a frame the model cannot place falls to
today's words. The cloud verdict is held to the same rule from the other side,
since it too is a claim (light is reaching the sensor) that ends a no-light
spell: it is given only past the ceiling.

ONE UNCERTAINTY IS NOT BOUNDED, and it leans the wrong way. The dark master
of option 1 is accepted anywhere inside the matcher's window: by default 5 %
of the exposure and 2 C (the operator can widen the temperature to 50 C), and
at any temperature when either side's is unknown. A master shot warmer or
longer than the frame reads ABOVE the frame's true no-light level, which
widens the no-light band by the difference, so a sky fainter than that reads
as a cap. For a solve exposure the default window moves the dark part by a
few ADU. It is not modelled, because splitting the dark part off the pedestal
needs a bias, and the dark path does not ask for one.

THE SELF-REFERENCE (#262, S2 orchestrator ruling 2, owner list item 20). On
the rig nothing had shot a bias or a dark at the solve's readout, so option 2
had nothing to stand on and the check gave no verdict on the very night it
was built for. So when the library was read and holds neither a dark master
for the frame nor a bias master at its readout, ``failed_solve_error`` shoots
one frame itself, straight away: at the camera's SHORTEST exposure, at the
failed frame's gain, offset and binning, shutter closed, under the hub's
exposure guard, bounded. That frame stands in for the missing bias master,
and ``reference_for`` draws the floor and the ceiling from it exactly as from
a master: plus the least and the most dark current the doubling law allows,
measured from a dark at that readout when the library has one, and no
ceiling without one.

WHY THE SHORTEST EXPOSURE, and never the frame's own. The CMOS cameras this
project drives mostly have no shutter, so "shutter closed" is a request the
sensor cannot honour. Shot at the frame's 12 s through an open optic, the
self-shot is a picture of the same sky, and would call it no light, the one
error this module must not make; through a cap it is a dark, and matches
every capped frame whatever its dark current. At the shortest exposure the
sky and the dark current both vanish: a sky that lifts the 12 s frame by the
3 ADU band lifts a 1 ms frame by 3 x 0.001 / 12 = 0.00025 ADU, and one bright
enough to put a single ADU into 1 ms puts 12 000 into the 12 s frame, far past
any ceiling. What is left is the pedestal, which is what a bias master
measures.

WHAT IT DOES NOT BOUND. Some CMOS cameras read a slightly different pedestal
at their shortest exposures than at long ones, which is why some imagers
calibrate with dark flats rather than bias frames. A self-shot that reads
BELOW the long-exposure pedestal only pushes capped frames out of the band;
one that reads ABOVE it raises the floor by the difference, and a sky fainter
than that could read as a cap. It is not measured, for the reason option 1's
window is not: there is nothing at that readout to measure it against. Nor
does it answer the #262 safety review's question, which it makes live on
every night rather than on the night an operator shoots a master: whether
thick overcast over a dark site, with no Moon, can sit within the band of a
cold sensor's pedestal. The band has been held only against a lit overcast
(``tests/fixtures/star_noise/blank_overcast.npz``).

KEPT FOR THE NIGHT. A pedestal does not move between one failed solve and the
next, and a self-shot on every failure would add an exposure and a download
to each of the recovery ladder's retries. So one is kept per camera, gain,
offset, binning, band of sensor temperature (``SELF_REFERENCE_BAND_C``) and
night (``events.night_key``), in process memory: a fact learned about
tonight's hardware, which the next night asks for again. A shot beside a
failed frame bright enough that light may have reached the shot too (at
dusk, under a lit panel) judges that frame and is not kept, so a later dark
frame is never judged against light (``SELF_REFERENCE_KEEP_LIGHT_ADU``). A
self-shot that fails, runs past its bound or returns a buffer is no
reference, and is not kept, so the next failure tries again; it is never a
crash of the solve path and never a no-light verdict. A cancel that lands
while it exposes propagates.

WHO CALLS IT. Every solve path that exposes its own frame and raises on a
failed solve, through ``failed_solve_error``: ``Hub.solve_and_sync`` (which
covers auto-resume's recovery solve, goto centring and the solve route),
``Hub.sync_rotator_to_sky``, ``Hub._rotate_to_pa_attempts`` and polar
alignment's ``_capture_and_solve``. ``tests/test_failed_solve_says_no_light.py``
scans for a new one. A no-light verdict raises ``NoLightError``, a
``DeviceError``, so every existing caller still catches it, and a caller that
must act on it (``ResumeArm``) branches on the TYPE, never on the text.

EVERY VERDICT SAYS WHAT IT STANDS ON (#308, S3 orchestrator ruling 8). Only a
dark master for the frame's own settings IS that frame's no-light level; a
bias master and a self-shot are floors drawn from a pedestal, and the
self-shot is the one the #262 safety review's question (above) is open
against on every night with a failed solve. So a ``Reference`` carries its
``kind`` as data (``DARK_MASTER``, ``BIAS_MASTER``, ``SELF_SHOT``, or
``EXPLICIT`` for one handed in), and the exception exposes it as
``reference_kind``: ResumeArm backs off to its hourly retry only on a verdict
a dark master stands behind, and asks this, never the evidence line's words.

WHAT IT DOES NOT CHECK. A bound motorised cover or flat panel whose state
could be read directly is #192's, not this module's: this judges the pixels.
"""
from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Any, Callable, Iterable

import numpy as np

from ..calibration.matcher import (LightNeed, MasterRecord, MatchTolerance,
                                   best_master)
from ..devices.base import DeviceError
from ..events import bus, night_key

#: The three verdicts ``classify`` returns.
NO_LIGHT = "no_light"
CLOUD = "cloud"
UNKNOWN = "unknown"

#: The no-light verdict in words. The failure message and ResumeArm's alert
#: both say this; neither carries a number in it.
NO_LIGHT_WORDS = "no light: the optic is capped, covered or obstructed"

#: ``classify``'s reason when it is handed no reference; ``judge_frame``
#: replaces it with the library's own account of what is missing.
NO_REFERENCE_WHY = "there is no no-light reference for this camera"

#: ``classify``'s reason for a frame above a bias-based reference's floor but
#: not past its ceiling. Words only, like every ``why``: it rides a hold's
#: reason. It follows "no level check was possible: " in the failure message.
BELOW_THE_CEILING_WHY = ("with no dark master for this temperature, a level "
                         "this far above the bias could be dark current "
                         "alone, so light cannot be told from it")

#: ``classify``'s reason when the level, the reference or the band it would
#: compare is not a number (a sensor temperature that reads NaN, scaled into
#: the reference by ``reference_for``). Words only, like every ``why``.
NOT_A_NUMBER_WHY = ("the no-light reference for this frame is not a number "
                    "(its sensor temperature or a master's level did not read "
                    "as one), so the level cannot be judged")

#: MAD to Gaussian sigma, the convention ``imaging.darks`` and
#: ``imaging.clouds`` use, so a "robust sigma" is the same number of ADU in
#: every module that prints one.
#:
#: ROBUST BECAUSE OF THE #251 FRAME ITSELF. Its p1 and p99 (231 and 273) put a
#: Gaussian core at (273 - 231) / (2 x 2.326) = 9.0 ADU, while its std was
#: 32.6: 3.6 times that, carried by the hot pixels of a sensor at 18.5 C. A
#: std-based width would be set by a few thousand warm pixels, not by the frame.
MAD_TO_SIGMA = 1.4826

#: Standard error of a sample median, in units of sigma / sqrt(N):
#: sqrt(pi / 2) = 1.2533 for Gaussian data.
MEDIAN_SE_FACTOR = math.sqrt(math.pi / 2.0)

#: One sigma of how far a no-light frame's median can sit from its reference
#: for reasons that are not light, in ADU. Every reference carries this.
#:
#: WHY NOT THE FRAME'S PER-PIXEL SIGMA, which is the obvious unit and the
#: wrong one. The question is where the MEDIAN of millions of pixels sits, not
#: where one pixel does. For the #251 frame, 9.0 ADU per pixel over the
#: 3124 x 2088 pixels of a bin-2 solve frame puts the median's own standard
#: error at 1.2533 x 9.0 / sqrt(6.52e6) = 0.0044 ADU, two thousand times
#: tighter than a pixel. A band drawn in per-pixel sigma is that much wider
#: than the level is known to, and the real overcast frame in
#: ``tests/fixtures/star_noise/blank_overcast.npz`` shows what that costs: its
#: median is 264 with a per-pixel sigma of 22.2, which puts the #251 capped
#: level of 251 only 0.58 of its sigma below it. Any per-pixel band of k >= 0.6
#: would call that overcast sky a capped optic.
#:
#: WHAT IT IS INSTEAD: the black level's own stability plus the median's
#: rounding. The median of integer ADU is an integer, and rounding a
#: continuous level to one is 1 / sqrt(12) = 0.29 ADU of sigma. The rig's
#: black level held still below that across six consecutive 180 s frames on
#: 2026-08-14 (median 240.0 on every one; ``imaging.darks``,
#: ``SOURCE_REF_PIXELS``). Night-to-night drift between a master and tonight's
#: frame is not measured, so this allows 1.0 ADU, a bit over three times the
#: rounding. Narrower would refuse genuinely capped frames whose offset moved a
#: little (they fall to today's words); wider would let a faint sky read as a
#: cap, which is the error this module must not make.
OFFSET_STABILITY_ADU = 1.0

#: The half-width of the no-light band, in sigmas of the level difference:
#: ``sqrt(median standard error^2 + reference sigma^2)``.
#:
#: At 3, a frame truly at its reference falls outside the band about one time
#: in 370 under a Gaussian model, and falls to today's words when it does. With
#: the reference sigma at ``OFFSET_STABILITY_ADU`` the band is 3 x
#: sqrt(0.0044^2 + 1.0^2) = 3.0 ADU for the #251 frame: a third of one pixel's
#: noise, and any sky that lifts the background by more than 3 ADU in the solve
#: exposure is called light.
K_SIGMA = 3.0

#: The range of temperatures over which dark current doubles, in C, for the
#: silicon sensors this project drives. Rules of thumb put it near 6 C; the
#: sensors themselves span roughly 5 to 7.
#:
#: BOTH ENDS ARE USED, so the range needs to be honest, not tight. Scaling a
#: dark measured at -10 C to the 18.5 C of the #251 frame multiplies its dark
#: current by 2^(28.5/7) = 16.8 at the slow end, 2^(28.5/6) = 26.9 in the
#: middle and 2^(28.5/5) = 52 at the fast end: a factor of three between the
#: ends. ``reference_for`` takes whichever end gives LESS dark current as the
#: reference's floor (the slow one for a frame warmer than the dark, the fast
#: one for a colder frame), and the other end as its ceiling. A capped frame
#: whose dark current is really higher than the floor reads above it and gets
#: no verdict; a sky can only hide under the floor if it is fainter than the
#: band, since the true dark current is never below the bound. The nominal
#: 6 C would put the floor (26.9 - 16.8) / 26.9 = 38 % of its own predicted
#: dark current above a sensor at the slow end, and a sky that faint would
#: read as a cap. What the range does NOT bound is a sensor outside it: one
#: doubling faster than every 5 C puts a capped frame past the ceiling, and
#: the verdict there is cloud, which is today's words with a wrong clause.
DARK_DOUBLING_FAST_C = 5.0
DARK_DOUBLING_SLOW_C = 7.0

#: How a failure message and the evidence line name the self-reference (see
#: the module docstring). Words only: it rides a hold's reason.
SELF_REFERENCE_WORDS = "the shortest-exposure frame that stands in for a bias"

#: The self-shot's exposure when the camera reports no minimum of its own
#: (``min_exposure_s``), in seconds. No backend reports one today: the native
#: SDKs take microseconds, and ASCOM's ExposureMin is not read. 1 ms is inside
#: what every camera here accepts and short enough that neither sky nor dark
#: current puts a measurable ADU into it (the module docstring does the sum).
#: A camera that refuses it fails the self-shot, which is no reference: the
#: behaviour before #262.
SELF_REFERENCE_FALLBACK_S = 0.001

#: The bound on the self-shot's exposure, download included, in seconds. A
#: 1 ms frame is normally a few seconds of download. The bound is set by the
#: camera's own worst case instead: the Alpaca camera polls ``imageready`` for
#: the exposure plus 30 s (``devices.alpaca._IMAGEREADY_POLL_MARGIN_S``), then
#: downloads through a client whose per-request timeout is 30 s. So a camera
#: that is merely slow gives up by its own rules first, and the bound cuts only
#: one that has stopped answering. Read at call time, so a test can shrink it.
SELF_REFERENCE_TIMEOUT_S = 60.0

#: The width of the sensor-temperature bands a self-reference is kept per, in
#: C: the matcher's default temperature tolerance, so a self-shot is reused
#: across the span a dark master is accepted across (``MatchTolerance``).
SELF_REFERENCE_BAND_C = MatchTolerance().temp_tol_c

#: The most light a self-shot may have caught and still be kept for the
#: night, in ADU: a tenth of ``OFFSET_STABILITY_ADU``, so a kept shot moves
#: the no-light band's centre by at most 0.1 ADU against its 3 ADU half-width.
#:
#: WHY A KEPT SHOT IS ASKED THIS. "At the shortest exposure the sky vanishes"
#: holds for a night sky, not for every sky a solve can fail under. A 1 ms
#: shot beside a solve that failed at dusk, under a lit flat panel or with the
#: dome lights on catches a few ADU of that light; the frame it was taken for
#: reads thousands of ADU above it and is judged safely, but a shot kept for
#: the night would carry those ADU into every later verdict at that readout,
#: and a faint night sky that many ADU over the pedestal would then read as a
#: capped optic, the one error this module must not make. The failed frame
#: bounds it (``_light_share``): whatever lifts it above the shot, scaled to
#: the shot's length, is the most light the shot can hold. A capped frame or
#: a night sky puts well under this into a 1 ms shot (the #251 frame's 11 ADU
#: over 12 s is 0.001 ADU); a 12 s frame must read some 1200 ADU over the shot
#: before the shot is refused. Clipping understates a saturated frame's light,
#: but at 1 ms a frame clipped at the top of a 16-bit range is still refused
#: at any solve exposure under ten minutes (65 000 x 0.001 / 600 = 0.11); a
#: camera that reported a far shorter minimum would narrow that, and none
#: reports one today (``SELF_REFERENCE_FALLBACK_S``).
SELF_REFERENCE_KEEP_LIGHT_ADU = OFFSET_STABILITY_ADU / 10.0

#: What a ``Reference`` stands on, as data (#308, S3 orchestrator ruling 8):
#: the dark library's master for the frame's own settings, a bias master at
#: its readout, a frame this module shot itself in place of that bias (#262),
#: or a reference handed in (a test, or a caller that measured its own).
#: ``source`` says the same thing in words for the evidence line; code asks
#: this.
DARK_MASTER = "dark_master"
BIAS_MASTER = "bias_master"
SELF_SHOT = "self_shot"
EXPLICIT = "explicit"
REFERENCE_KINDS = (DARK_MASTER, BIAS_MASTER, SELF_SHOT, EXPLICIT)


@dataclass(frozen=True)
class Reference:
    """The median a frame with no light on it reads, in ADU.

    ``sigma`` is one sigma of the uncertainty in ``level`` (see
    ``OFFSET_STABILITY_ADU``). ``source`` names where it came from, in words,
    for the evidence line; ``detail`` carries the master ids and the numbers
    behind it.

    ``level`` is the LEAST the no-light median could be and ``ceiling`` the
    MOST (see the module docstring): None when the two are the same number,
    as they are for a dark master or a reference handed in, and ``math.inf``
    when nothing bounds it from above (a bias with no dark to measure the
    dark current from). ``top`` reads it either way. Kept out of the repr:
    ``LightVerdict.evidence`` prints it, and the recorded test failures that
    show a reference's repr were taken before it existed.

    ``kind`` is one of ``REFERENCE_KINDS`` (#308): what a caller that must
    weigh the verdict asks, since only a dark master is the frame's own
    no-light level. ``EXPLICIT`` by default, so a reference made by hand
    never passes for a master. Out of the repr for ``ceiling``'s reason, and
    checked when made: a misspelt kind would read as "not a dark master"
    without a word."""
    level: float
    sigma: float = OFFSET_STABILITY_ADU
    source: str = "an explicit reference"
    detail: str = ""
    ceiling: float | None = field(default=None, repr=False)
    kind: str = field(default=EXPLICIT, repr=False)

    def __post_init__(self) -> None:
        if self.kind not in REFERENCE_KINDS:
            raise ValueError(f"a no-light reference's kind is one of "
                             f"{REFERENCE_KINDS}, not {self.kind!r}")

    @property
    def top(self) -> float:
        """The most the no-light median could be."""
        return self.level if self.ceiling is None else self.ceiling


@dataclass(frozen=True)
class SelfBias:
    """What a self-shot read (#262): ``level`` its median in ADU, ``detail``
    what the evidence line prints about it (the exposure and readout, and
    whether it was shot just now or kept from earlier tonight)."""
    level: float
    detail: str


#: The self-references taken tonight, keyed by ``_self_reference_key``:
#: (camera name, gain, offset, binning, temperature band, night). Written on
#: the event loop only. Holds one night at a time: a write drops the others.
_SELF_REFERENCES: dict[tuple, SelfBias] = {}


@dataclass(frozen=True)
class LightVerdict:
    """What ``classify`` found, with the numbers behind it.

    ``why`` is in words only: it is the reason given when there is no verdict,
    and it rides the failure message, which becomes a hold's reason and a log
    line, where a number that changes every retry would restart the hold's
    clock (``ResumeArm._set_hold``)."""
    kind: str
    median: float | None = None
    pixel_sigma: float | None = None
    band: float | None = None
    reference: Reference | None = None
    why: str = ""

    def evidence(self) -> str:
        """The numbers, for the one info line per judged frame."""
        if self.median is None:
            return f"{self.kind}: {self.why}"
        head = (f"median {self.median:.1f} ADU, robust sigma "
                f"{self.pixel_sigma:.1f} ADU per pixel")
        if self.reference is None:
            return f"{head}; {self.kind}: {self.why}"
        ref = self.reference
        tail = f" ({ref.detail})" if ref.detail else ""
        # No band when the frame was refused before one was drawn (a
        # constant buffer): this line must never be what fails.
        band = (f"; band +/-{self.band:.2f} ADU" if self.band is not None
                else "")
        # Only a reference whose level is a floor has a ceiling to print.
        if ref.ceiling is None:
            top = ""
        elif math.isinf(ref.ceiling):
            top = ", with no ceiling"
        else:
            top = f", ceiling {ref.ceiling:.1f} ADU"
        return (f"{head}; reference {ref.level:.1f} ADU{top} from "
                f"{ref.source}{tail}{band}; {self.kind}"
                + (f": {self.why}" if self.why else ""))


class FailedSolveError(DeviceError):
    """A plate solve failed on a frame this module judged. ``verdict`` says
    what the frame's light level showed. Raised for the cloud and no-verdict
    cases, so a caller that needs to know "light reached the sensor" (a cloud
    verdict ends ResumeArm's no-light spell) can ask the type and the verdict,
    not the text."""

    def __init__(self, message: str, verdict: LightVerdict):
        super().__init__(message)
        self.verdict = verdict

    @property
    def reference_kind(self) -> str | None:
        """The ``kind`` of the reference the verdict was judged against
        (#308), or None when there was none to judge against."""
        ref = self.verdict.reference
        return None if ref is None else ref.kind


class NoLightError(FailedSolveError):
    """A plate solve failed and its frame read at this camera's no-light
    level: the optic is capped, covered or obstructed (#251). A
    ``DeviceError``, so every caller that already survives a failed solve
    survives this; ResumeArm branches on it to alert once, and backs off
    only when ``reference_kind`` is ``DARK_MASTER`` (#308)."""


def frame_stats(data: Any) -> tuple[float, float, int, bool] | None:
    """``(median, robust per-pixel sigma, finite pixel count, constant)`` of a
    linear frame, or None when it has no finite pixels.

    Float before any subtraction: the frames arrive as uint16, and
    ``data - median`` on unsigned pixels wraps below zero to 65535. float32,
    not float64: it holds every 16-bit ADU exactly, and a bin-2 solve frame
    of 6.5 million pixels then costs 26 MB a copy instead of 52, on a rig
    that may be an Orange Pi."""
    raw = np.asarray(data)
    a = raw.astype(np.float32).ravel()
    if not np.issubdtype(raw.dtype, np.integer):
        a = a[np.isfinite(a)]
    if a.size == 0:
        return None
    median = float(np.median(a))
    sigma = float(np.median(np.abs(a - median))) * MAD_TO_SIGMA
    constant = bool(a.min() == a.max())
    return median, sigma, int(a.size), constant


def classify(data: Any, reference: Reference | None) -> LightVerdict:
    """Judge one failed solve's frame against ``reference``.

    Pure: no I/O, no library. ``judge_frame`` finds the reference; the tests
    hand one in."""
    stats = frame_stats(data)
    if stats is None:
        return LightVerdict(UNKNOWN, reference=reference,
                            why="the frame has no pixels to measure")
    median, pixel_sigma, n, constant = stats
    if constant:
        # Every pixel identical is a buffer, not an exposure (the same test
        # ``imaging.darks`` makes). It would sit at any reference it happened
        # to equal with a band of nothing but the reference's own sigma.
        return LightVerdict(UNKNOWN, median, pixel_sigma, reference=reference,
                            why="every pixel reads the same, so the frame is a "
                                "buffer, not an exposure")
    if reference is None:
        return LightVerdict(UNKNOWN, median, pixel_sigma, why=NO_REFERENCE_WHY)
    se = MEDIAN_SE_FACTOR * pixel_sigma / math.sqrt(n)
    band = K_SIGMA * math.hypot(se, reference.sigma)
    excess = median - reference.level
    if not (math.isfinite(excess) and math.isfinite(band)
            and not math.isnan(reference.top)):
        # A NUMBER THAT IS NOT ONE SUPPORTS NO VERDICT. Every comparison with
        # a NaN is False, so a NaN level passed both tests below and fell
        # through to NO_LIGHT: a sensor temperature that reads NaN, scaled by
        # ``reference_for``, gave a lit sky 60 ADU over its bias the alarm and
        # the hourly backoff this module exists never to give without
        # evidence. So the verdict is asked of numbers, and no light is never
        # the answer by default. ``math.inf`` is a ceiling (a bias alone), not
        # a failure, which is why the top is asked for NaN only.
        return LightVerdict(UNKNOWN, median, pixel_sigma,
                            band if math.isfinite(band) else None, reference,
                            why=NOT_A_NUMBER_WHY)
    if excess > band:
        # ABOVE THE FLOOR IS NOT YET LIGHT. Past the most the no-light level
        # could be, it is; short of that, the frame may be reading dark
        # current the floor left out, and "light is reaching the sensor"
        # would be a claim with nothing under it (see ``Reference``).
        if median - reference.top > band:
            return LightVerdict(CLOUD, median, pixel_sigma, band, reference)
        return LightVerdict(UNKNOWN, median, pixel_sigma, band, reference,
                            why=BELOW_THE_CEILING_WHY)
    if excess < -band:
        # DARKER THAN NO LIGHT IS NOT NO LIGHT. A frame cannot read below the
        # level its sensor reads in the dark, so this one says the reference
        # is wrong for it (the offset changed, a master from another camera),
        # and a wrong reference supports no verdict at all.
        return LightVerdict(UNKNOWN, median, pixel_sigma, band, reference,
                            why="the frame reads darker than the no-light "
                                "reference, so that reference does not "
                                "describe it")
    return LightVerdict(NO_LIGHT, median, pixel_sigma, band, reference)


# ---------------------------------------------------------------- references

@lru_cache(maxsize=32)
def _median_of_file(path: str, built_ts: float) -> float | None:
    """The median of a master's pixels. Cached on the path AND the build time,
    so a rebuilt master is read again rather than served stale."""
    try:
        from astropy.io import fits           # lazy, as every caller here is
        with fits.open(path, memmap=False) as hdul:
            data = hdul[0].data
        stats = frame_stats(data)
    except Exception:                          # noqa: BLE001 - absent, not fatal
        return None
    return None if stats is None else stats[0]


def master_level(record: MasterRecord) -> float | None:
    """The level a master reads, or None when its file cannot be read (a
    master that is not on disk is no reference, not an error)."""
    return _median_of_file(str(record.path), float(record.built_ts))


def _need(frame: Any) -> LightNeed:
    temp = getattr(frame, "temperature_c", None)
    return LightNeed(exposure_s=float(frame.exposure_s), gain=int(frame.gain),
                     offset=int(frame.offset),
                     temp_c=None if temp is None else float(temp),
                     binning=int(frame.binning), filter="")


def _same_readout(need: LightNeed, m: MasterRecord) -> bool:
    return (m.gain == need.gain and m.offset == need.offset
            and m.binning == need.binning)


def _temp_gap(need: LightNeed, m: MasterRecord) -> float:
    if need.temp_c is None or m.temp_c is None:
        return 0.0
    return abs(need.temp_c - m.temp_c)


def _nearest_bias(need: LightNeed,
                  masters: Iterable[MasterRecord]) -> MasterRecord | None:
    """The bias master for this readout, nearest in temperature.

    ANY TEMPERATURE, unlike the calibration bundle's bias match (which holds
    the matcher's tolerance). The recovery solve runs with the sensor at
    ambient, before any run has cooled it (``ResumeArm._recover`` says why),
    while the library's masters were shot at the cooled setpoint, so a
    tolerance would leave the one solve #251 was about with no bias at all.
    The pedestal of a black-clamped CMOS readout barely moves with
    temperature; what it does move by is not modelled, and like every
    unmodelled offset here it can only push a capped frame out of the band."""
    cands = [m for m in masters
             if m.frame_type == "BIAS" and _same_readout(need, m)]
    if not cands:
        return None
    return min(cands, key=lambda m: (_temp_gap(need, m), -m.frame_count))


def _dark_anchor(need: LightNeed,
                 masters: Iterable[MasterRecord]) -> MasterRecord | None:
    """A dark master to measure this readout's dark current from: same gain,
    offset and binning, a real exposure and a known temperature. The one
    nearest the frame's temperature, so the scaling extrapolates least; then
    the longest, whose dark current stands furthest above the pedestal."""
    cands = [m for m in masters
             if m.frame_type == "DARK" and _same_readout(need, m)
             and m.exposure_s > 0 and m.temp_c is not None]
    if not cands:
        return None
    return min(cands, key=lambda m: (_temp_gap(need, m), -m.exposure_s))


def _settings(need: LightNeed) -> str:
    return f"gain {need.gain}, offset {need.offset}, bin {need.binning}"


def reference_for(frame: Any, masters: Iterable[MasterRecord], *,
                  tol: MatchTolerance | None = None,
                  level_of: Callable[[MasterRecord], float | None] = master_level,
                  self_bias: SelfBias | None = None
                  ) -> tuple[Reference | None, str]:
    """``(reference, why_not)`` for ``frame``: the median it would read with
    no light on it, or None and the reason, in words, that nothing can say.

    ``frame`` needs ``exposure_s``, ``gain``, ``offset``, ``binning`` and
    ``temperature_c`` (a ``CameraFrame``). ``level_of`` reads a master's
    median; the tests hand in a table. ``self_bias`` is a self-shot's level
    (#262), used in place of a bias master only when the library has none
    at this readout; ``failed_solve_error`` shoots it."""
    masters = list(masters)
    need = _need(frame)
    # 1. THE DARK MASTER FOR THESE SETTINGS. The matcher's own rule and
    #    tolerances: the dark it picks is the one the project subtracts from a
    #    light at these settings, which is the claim that it IS that light's
    #    no-light image. Its window (5 % of exposure, 2 C) moves the dark part
    #    by up to a quarter, which for a solve exposure is a few ADU; that is
    #    not modelled here without a bias to split the dark part off with.
    dark = best_master(need, masters, tol or MatchTolerance(), "DARK")
    if dark is not None:
        level = level_of(dark)
        if level is not None:
            return Reference(level=level, source="the dark master for these "
                                                 "settings",
                             detail=f"dark master {dark.id}",
                             kind=DARK_MASTER), ""
    # 2. A BIAS, PLUS THE LEAST DARK CURRENT THE DOUBLING LAW ALLOWS. The
    #    rate is measured, never assumed: a dark at this readout, at any
    #    exposure and temperature, less the bias, over its exposure. No camera
    #    rate is written down anywhere in this project, and ``imaging.darks``
    #    explains why inventing one is the defect it exists to catch. With no
    #    dark to measure from (or no temperature to scale by) the dark current
    #    is taken as zero, its true lower bound, so the reference is the bias
    #    itself and a capped frame with real dark current reads above it.
    bias = _nearest_bias(need, masters)
    bias_level = None if bias is None else level_of(bias)
    if bias_level is not None:
        source = ("the bias master plus the least dark current the doubling "
                  "law allows")
        what = f"bias master {bias.id}"
        kind = BIAS_MASTER
    elif self_bias is not None:
        # 3. THE SELF-REFERENCE IN PLACE OF THE BIAS MASTER (#262, S2
        #    orchestrator ruling 2). Only when the library has no bias at
        #    this readout, and it is a bias like any other from here on: the
        #    floor and the ceiling below are drawn from it as from a master,
        #    so a dark at this readout still measures the rate, and without
        #    one there is still no ceiling.
        bias_level = self_bias.level
        source = (f"{SELF_REFERENCE_WORDS}, plus the least dark current the "
                  f"doubling law allows")
        what = self_bias.detail
        # ITS OWN KIND, never the bias master's it stands in for: the
        # self-shot is the reference #308's open question is about.
        kind = SELF_SHOT
    else:
        return None, (f"the calibration library has no dark master for this "
                      f"frame's exposure, {_settings(need)} and temperature, "
                      f"and no bias master at {_settings(need)}")
    #
    #    AND THE MOST IT ALLOWS, AS THE CEILING. The same rate scaled at the
    #    other end of the doubling range (the fast one for a warmer frame, the
    #    slow one for a colder frame). With no dark to measure a rate from,
    #    nothing bounds the dark current from above, so there is no ceiling
    #    and a frame above the bias gets no cloud verdict (``classify``).
    dark_lo, dark_hi = 0.0, math.inf
    how = "no dark to scale from, so no dark current is assumed"
    anchor = _dark_anchor(need, masters)
    anchor_level = None if anchor is None else level_of(anchor)
    if anchor_level is not None and need.temp_c is not None:
        rate = max(0.0, anchor_level - bias_level) / anchor.exposure_s
        gap = need.temp_c - anchor.temp_c
        doubling = DARK_DOUBLING_SLOW_C if gap > 0 else DARK_DOUBLING_FAST_C
        other = DARK_DOUBLING_FAST_C if gap > 0 else DARK_DOUBLING_SLOW_C
        dark_lo = rate * need.exposure_s * 2.0 ** (gap / doubling)
        dark_hi = rate * need.exposure_s * 2.0 ** (gap / other)
        how = (f"dark current {rate:.4f} ADU/s from dark master {anchor.id}, "
               f"scaled {gap:+.1f} C at one doubling per {doubling:g} C "
               f"to {dark_lo:.1f} ADU")
    return Reference(level=bias_level + dark_lo, source=source,
                     detail=f"{what}; {how}",
                     ceiling=bias_level + dark_hi, kind=kind), ""


def _verdict(data: Any, ref: Reference | None, why: str) -> LightVerdict:
    """``classify``, with the reason a missing reference gives replaced by
    ``why``: the library knows WHICH master is missing, and that is the
    sentence that tells the operator what to shoot."""
    verdict = classify(data, ref)
    if verdict.why == NO_REFERENCE_WHY:
        verdict = replace(verdict, why=why)
    return verdict


def _look(frame: Any, library: Any, tol: MatchTolerance | None
          ) -> tuple[LightVerdict | None, list[MasterRecord] | None, str]:
    """The verdict the library alone can give, as ``(verdict, None, "")``;
    or ``(None, masters, why)`` when it was read and holds no master at the
    frame's readout, the one case a self-reference stands in for (#262).
    Blocking: masters are read from disk. Never raises for a missing or
    unreadable library; that is no reference."""
    data = getattr(frame, "data", None)
    if frame is None or data is None:
        return LightVerdict(UNKNOWN, why="there is no frame to measure"), \
            None, ""
    if not getattr(frame, "data_is_linear", True):
        # A NINA-rendered preview is an 8-bit stretch of the frame, so its
        # levels say nothing about ADU.
        return LightVerdict(UNKNOWN, why="the frame is a rendered preview, "
                                         "not linear sensor data"), None, ""
    if library is None:
        return _verdict(data, None, "no calibration library is loaded"), \
            None, ""
    try:
        masters = list(library.list_masters())
    except Exception:                          # noqa: BLE001 - no reference
        return _verdict(data, None, "the calibration library could not be "
                                    "read"), None, ""
    ref, why = reference_for(frame, masters, tol=tol)
    if ref is None:
        return None, masters, why
    return _verdict(data, ref, ""), None, ""


def judge_frame(frame: Any, library: Any,
                tol: MatchTolerance | None = None) -> LightVerdict:
    """The verdict on a failed solve's frame, reading the reference from
    ``library`` (a ``CalibrationLibrary``, or anything with
    ``list_masters()``). Blocking: masters are read from disk. Never raises
    for a missing or unreadable library; that is no reference. Shoots no
    self-reference, which needs the event loop and the camera: that is
    ``failed_solve_error``'s."""
    verdict, _masters, why = _look(frame, library, tol)
    return verdict if verdict is not None else _verdict(frame.data, None, why)


def _judge_against(frame: Any, masters: list[MasterRecord],
                   tol: MatchTolerance | None, bias: SelfBias) -> LightVerdict:
    """The verdict with ``bias``, a self-shot, standing in for the bias
    master the library lacks. Blocking, like ``judge_frame``."""
    ref, why = reference_for(frame, masters, tol=tol, self_bias=bias)
    return _verdict(frame.data, ref, why)


def error_for(verdict: LightVerdict, solver_message: str,
              prefix: str) -> FailedSolveError:
    """The exception a failed solve raises, worded from ``verdict``.

    WORDS ONLY, on every branch. The message becomes a hold's reason and a
    log line on every retry; the numbers are in ``verdict`` and in the one
    evidence line ``failed_solve_error`` logs.

    NO VERDICT KEEPS TODAY'S WORDS. ``f"{prefix}: {solver_message}"`` is the
    message every caller raised before this module existed, and it is kept as
    the start of the message, so nothing that reads it changes; the clause
    after it says no level check was possible and why."""
    if verdict.kind == NO_LIGHT:
        return NoLightError(
            f"{prefix}: {NO_LIGHT_WORDS} (the frame reads at the level this "
            f"camera reads with no light on it; the solver said: "
            f"{solver_message})", verdict)
    if verdict.kind == CLOUD:
        return FailedSolveError(
            f"{prefix}: {solver_message} (the background is above the level "
            f"this camera reads with no light on it, so light is reaching the "
            f"sensor)", verdict)
    return FailedSolveError(
        f"{prefix}: {solver_message} (no level check was possible: "
        f"{verdict.why})", verdict)


def _tolerance() -> MatchTolerance:
    """The operator's calibration match tolerances, as the library's own
    coverage check reads them; the matcher's defaults when there is no
    config."""
    try:
        from ..config import config_store
        cal = config_store.cfg().calibration
        return MatchTolerance(cal.exposure_tol_pct, cal.temp_tol_c)
    except Exception:                          # noqa: BLE001 - defaults
        return MatchTolerance()


def _min_exposure_s(cam: Any) -> float:
    """The camera's shortest exposure, in seconds: its ``min_exposure_s``
    when it reports a positive number, else ``SELF_REFERENCE_FALLBACK_S``."""
    try:
        s = float(getattr(cam, "min_exposure_s", None))
    except (TypeError, ValueError):
        return SELF_REFERENCE_FALLBACK_S
    return s if math.isfinite(s) and s > 0 else SELF_REFERENCE_FALLBACK_S


def _self_reference_key(cam: Any, frame: Any) -> tuple:
    """What a kept self-reference is good for: this camera, the failed
    frame's gain, offset and binning, its ``SELF_REFERENCE_BAND_C`` band of
    sensor temperature (None when the temperature is unknown or not a
    number), and tonight."""
    try:
        temp = float(getattr(frame, "temperature_c", None))
    except (TypeError, ValueError):
        temp = math.nan
    band = (math.floor(temp / SELF_REFERENCE_BAND_C) if math.isfinite(temp)
            else None)
    return (str(getattr(cam, "name", "")), int(frame.gain), int(frame.offset),
            int(frame.binning), band, night_key())


def _light_share(frame: Any, level: float, seconds: float) -> float:
    """The most light, in ADU, a self-shot ``seconds`` long that read
    ``level`` can have caught beside the failed ``frame``: all of the frame's
    rise above it, taken as light arriving at a steady rate, scaled to the
    self-shot's length. Dark current is counted in with the light, which only
    makes the bound larger. Infinite when nothing bounds it: a frame with no
    pixels, a buffer, or no exposure to scale by. Blocking: one median."""
    stats = frame_stats(getattr(frame, "data", None))
    try:
        exposure = float(frame.exposure_s)
    except (TypeError, ValueError):
        return math.inf
    if stats is None or stats[3] or not (math.isfinite(exposure)
                                         and exposure > 0):
        return math.inf
    return max(0.0, stats[0] - level) * seconds / exposure


def _remember(key: tuple, bias: SelfBias) -> None:
    """Keep ``bias`` for the rest of the night, and drop every other night's
    (the night is the key's last part), so the cache never grows past one."""
    for old in [k for k in _SELF_REFERENCES if k[-1] != key[-1]]:
        del _SELF_REFERENCES[old]
    _SELF_REFERENCES[key] = bias


async def _self_reference(frame: Any, hub: Any
                          ) -> tuple[SelfBias | None, str, str]:
    """``(bias, why_not, said)``: tonight's self-reference for ``frame``'s
    readout, from the cache or shot now, or None, the reason in words, and
    what the camera said when it raised (#262, S2 orchestrator ruling 2,
    owner list item 20; the module docstring says why each choice).

    TOTAL but for a cancel. Every way a self-shot can fail is no reference,
    worded after ``SELF_REFERENCE_WORDS``: a failure message is a hold's
    reason, so ``why_not`` is words only, and the camera's own text, which
    may carry a number, rides the evidence line as ``said``. A cancel
    (``ResumeArm.stop_recovery`` cancels the ladder's step) is not a failure:
    ``except Exception`` does not catch it, so it propagates, and the guard's
    ``async with`` frees the camera on its way out."""
    devices = getattr(hub, "devices", None) or {}
    cam = devices.get("camera")
    if cam is None or not getattr(cam, "connected", False):
        return (None, f"there is no connected camera to shoot "
                      f"{SELF_REFERENCE_WORDS}", "")
    key = _self_reference_key(cam, frame)
    kept = _SELF_REFERENCES.get(key)
    if kept is not None:
        return replace(kept, detail=f"{kept.detail}, kept from earlier "
                                    f"tonight"), "", ""
    seconds = _min_exposure_s(cam)
    gain, offset, binning = int(frame.gain), int(frame.offset), \
        int(frame.binning)
    try:
        # THE GUARD IS FREE HERE at all four solve paths, which close their
        # own ``exposure_guard`` before the solve and so before this. It is
        # non-blocking: a camera some other path holds refuses the self-shot
        # ("camera is busy"), which is no reference, and nothing waits.
        async with hub.exposure_guard("no-light reference"):
            shot = await asyncio.wait_for(
                cam.expose(seconds, gain, offset, binning=binning,
                           light=False),
                SELF_REFERENCE_TIMEOUT_S)
    except (asyncio.TimeoutError, TimeoutError):
        return None, f"{SELF_REFERENCE_WORDS} timed out", ""
    except Exception as e:                     # noqa: BLE001 - no reference
        return (None, f"{SELF_REFERENCE_WORDS} failed",
                str(e) or type(e).__name__)
    data = getattr(shot, "data", None)
    if data is None:
        return None, f"{SELF_REFERENCE_WORDS} returned no frame", ""
    if not getattr(shot, "data_is_linear", True):
        return (None, f"{SELF_REFERENCE_WORDS} is a rendered preview, not "
                      f"linear sensor data", "")
    try:
        stats = await asyncio.to_thread(frame_stats, data)
    except Exception as e:                     # noqa: BLE001 - no reference
        return (None, f"{SELF_REFERENCE_WORDS} could not be measured",
                str(e) or type(e).__name__)
    if stats is None:
        return None, f"{SELF_REFERENCE_WORDS} has no pixels to measure", ""
    median, _sigma, _n, constant = stats
    if constant:
        # A buffer at whatever level it holds would stand in for the bias
        # with a band of nothing but its own sigma, as ``classify`` says.
        return (None, f"{SELF_REFERENCE_WORDS} read every pixel the same, so "
                      f"it is a buffer, not an exposure", "")
    bias = SelfBias(level=median, detail=(
        f"self-reference {seconds:g} s at gain {gain}, offset {offset}, "
        f"bin {binning}"))
    # KEPT ONLY WHEN LIGHT CANNOT HAVE REACHED IT (see
    # ``SELF_REFERENCE_KEEP_LIGHT_ADU``). This frame is judged against the
    # shot either way: light in the shot only raises this frame's floor
    # toward a frame that reads far above it.
    share = await asyncio.to_thread(_light_share, frame, median, seconds)
    if share > SELF_REFERENCE_KEEP_LIGHT_ADU:
        return replace(bias, detail=(
            f"{bias.detail}, shot just now and not kept: the failed frame "
            f"reads bright enough that light may have reached it")), "", ""
    _remember(key, bias)
    return replace(bias, detail=f"{bias.detail}, shot just now"), "", ""


async def failed_solve_error(frame: Any, result: Any, *, prefix: str,
                             hub: Any) -> FailedSolveError:
    """THE ONE CALL a solve path makes on a failed solve, and raises:

        if not result.success:
            raise await _light.failed_solve_error(frame, result,
                                                  prefix="...", hub=self)

    Judges ``frame`` against the reference in ``hub.master_library`` off the
    event loop (the medians of a frame and up to three masters), logs one info
    line with the numbers, and returns the exception to raise:
    ``NoLightError`` for a no-light verdict, ``FailedSolveError`` otherwise.

    WHEN THE LIBRARY HOLDS NOTHING AT THE FRAME'S READOUT, it shoots the
    self-reference first (#262, S2 orchestrator ruling 2, owner list item
    20): one frame at ``hub.devices["camera"]``'s shortest exposure, at the
    frame's gain, offset and binning, shutter closed, under
    ``hub.exposure_guard``, within ``SELF_REFERENCE_TIMEOUT_S``, or tonight's
    kept one. Only then: a hub with no library loaded, or one that could not
    be read, takes none, because the self-shot stands in for a master the
    library LACKS and says nothing about a library nobody could read (the
    app always loads one, ``api/app.py``).

    A level check that itself fails is no verdict, never a no-light one, and
    never a crash of the solve path it was asked to explain. A cancel that
    lands during the self-shot propagates."""
    library = getattr(hub, "master_library", None)
    said = ""
    try:
        tol = _tolerance()
        verdict, masters, why = await asyncio.to_thread(_look, frame, library,
                                                        tol)
        if verdict is None:
            bias, why_not, said = await _self_reference(frame, hub)
            if bias is None:
                verdict = await asyncio.to_thread(
                    _verdict, frame.data, None, f"{why}; {why_not}")
            else:
                verdict = await asyncio.to_thread(_judge_against, frame,
                                                  masters, tol, bias)
    except Exception as e:                     # noqa: BLE001 - see docstring
        verdict = LightVerdict(UNKNOWN, why="the level check itself failed")
        bus.log("warning", f"solve light check failed: {e}", "solve")
    tail = f" (the camera said: {said})" if said else ""
    bus.log("info", f"failed solve, light check: {verdict.evidence()}{tail}",
            "solve")
    return error_for(verdict, str(getattr(result, "message", "") or ""),
                     prefix)
