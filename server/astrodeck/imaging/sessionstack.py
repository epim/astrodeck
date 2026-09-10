"""Session stack: one running mean per FILTER, composited into a colour preview.

Live View (``livestack.LiveStacker``) stacks whatever the camera is pointed at
right now, in one channel, and is thrown away when the loop stops. This is the
other half of the same idea and answers a different question: WHILE A SEQUENCE
RUNS, what is the picture actually going to look like? The monitor page shows
the last sub, which is a single frame through a single filter, and nothing in
the app has ever shown the frames added together, let alone in colour.

So: one ``LiveStacker`` per filter, fed only the frames the sequence's quality
gate ACCEPTED, and a composite that maps each filter's running mean onto a
colour channel. It is a PREVIEW, not data. Nothing here is the file you would
process later; the real frames are on disk untouched.

Three decisions worth knowing about:

**It is downsampled before anything else happens.** A ``LiveStacker`` holds
three float32 planes (sum, sum of squares, coverage) at frame size. On the
6252x4176 sensor this rig actually uses that is 313 MB PER FILTER, and a
seven-filter night would ask for 2.1 GB on a box with 4. Every frame is block-
averaged down first, by a factor chosen so the long edge lands under
``MAX_STACK_SIDE``: factor 4 on that sensor, 1563x1044, 19.6 MB per filter and
137 MB for all seven. That is a real preview at a real cost. The floor of
``MIN_DOWNSAMPLE`` = 2 means even a small sensor pays half.

**Channels are aligned to each other, not just within themselves.** Each
filter's stacker registers its own frames against its own first frame, so a
channel is internally sharp. But the OTHER channel seeded off a different
frame, after a dither or a filter change, and nothing has ever made the two
agree. Misregistered channels are the one defect that makes a colour composite
look broken rather than rough: every star wears a red shadow. The composite
therefore registers each channel's reference constellation against the busiest
channel's and shifts by whole pixels. Registration failing is not an error, it
just means no shift.

**The black point is per channel, the scale is shared.** Black is each
channel's own background median, so the sky comes out neutral instead of
whichever filter has the brightest background tinting the whole image. The
range above it is the SAME number for every channel (the widest channel's
99.8th percentile above its own background), because that is what makes the
picture a colour picture: a target twice as bright in red as in green has to
come out red, and per-channel white points would normalise exactly that
difference away. An asinh curve on top pulls the faint end up. Deliberately
NOT the app's MTF auto-stretch, which derives its own black AND white per
frame: that per-channel freedom is the thing a composite must not have.

**A one-shot-colour frame is debayered, not block-averaged.** ``channel_for``
maps ``OSC`` and an empty filter name onto ``L``, which for a MONO camera with
no filter is the honest answer. For a bayered sensor it was quietly the wrong
one, and the reason is worth stating because it looks like it ought to work:
:func:`downsample_factor` only ever returns EVEN factors, so the block mean
above averages whole 2x2 Bayer cells and produces a clean, artefact-free
image -- of ``(R + 2G + B) / 4``. No colour survives it, so an OSC rig's
composite was grey no matter how many hours went into it. When a frame carries
a Bayer pattern AND the filter claims no bandpass, it is split by
:func:`debayer_superpixel` into R, G and B at half resolution first, and the
REMAINING binning (``factor // 2``) is done on the three planes. Net reduction
is identical, so an OSC channel and a mono channel land on the same grid and
composite together. A bayered frame through a NAMED filter keeps the old path
on purpose: the operator has told us the bandpass, there is no colour in the
frame the filter did not put there, and the 2x2 mean is then exactly the
luminance we want. See :func:`channels_for`.

**Backfill and the live path share one lock.** ``add`` is called from the
sequence engine's frame loop (on the event loop thread) and, while a backfill
runs, from a worker thread reading old subs off disk. Everything that touches
the accumulators is inside ``self._lock``; the disk read and the FITS decode
are the caller's job and happen outside it, so the lock is held for one
already-binned frame's accumulation and never for I/O. The de-duplication
check is inside the same critical section as the accumulation it guards --
checking "have I seen this frame" and then stacking it in two separate locked
regions is exactly how the same sub gets folded in twice.
"""
from __future__ import annotations

import io
import math
import os
import threading
import time
from dataclasses import dataclass

import numpy as np
from PIL import Image

from .livestack import LiveStacker, _axis_slices
from .registration import register

#: The composite is a preview. Bin every incoming frame down until its long edge
#: is at most this, so the memory a filter costs does not scale with the sensor.
MAX_STACK_SIDE = 2048
#: ...and never accumulate at full resolution even on a small sensor. A 2x2 mean
#: also halves the read noise before the stacker's own clipping looks at it.
MIN_DOWNSAMPLE = 2
#: Re-render no more often than this, however hard the UI polls. The render is
#: a percentile plus an asinh over a few megapixels per channel; at one frame
#: every few minutes there is nothing new to see in between anyway.
MIN_RENDER_INTERVAL_S = 3.0
#: Default long edge of the delivered JPEG.
DEFAULT_PREVIEW_SIZE = 1600
#: Where the white point sits, per channel.
WHITE_PERCENTILE = 99.8
#: asinh steepness. Higher lifts the faint end harder.
ASINH_A = 30.0

#: Every filter name this rig has ever written into a FITS header, folded onto
#: the seven channels the composite understands. Anything unrecognised -- a
#: dual-band filter, a CLS, a name the operator typed -- lands on ``L`` and
#: contributes brightness without claiming a colour, which is the honest answer
#: for a filter whose bandpass we do not know.
_ALIASES: dict[str, str] = {
    "R": "R", "RED": "R",
    "G": "G", "GREEN": "G",
    "B": "B", "BLUE": "B",
    "L": "L", "LUM": "L", "LUMINANCE": "L", "CLEAR": "L", "C": "L",
    "NONE": "L", "OSC": "L", "": "L",
    "HA": "Ha", "H-ALPHA": "Ha", "H_ALPHA": "Ha", "HALPHA": "Ha", "H": "Ha",
    "OIII": "Oiii", "O3": "Oiii", "O-III": "Oiii", "O_III": "Oiii",
    "SII": "Sii", "S2": "Sii", "S-II": "Sii", "S_II": "Sii",
}

#: Which stacked channels feed each display channel, in order. A display channel
#: is the MEAN of whichever of its contributors exist, so R+Ha both present
#: blend rather than one silently winning, and Oiii alone comes out teal because
#: it feeds both green and blue.
CHANNEL_MIX: dict[str, tuple[str, ...]] = {
    "r": ("R", "Ha", "Sii"),
    "g": ("G", "Oiii"),
    "b": ("B", "Oiii"),
}
#: Channel order for deterministic tie-breaks and for the status readout.
CHANNEL_ORDER = ("L", "R", "G", "B", "Ha", "Oiii", "Sii")


#: The three planes a debayered one-shot-colour frame feeds, in composite order.
OSC_CHANNELS = ("R", "G", "B")

#: Bayer patterns this rig can see, and the two spellings they arrive in. The
#: FITS ``BAYERPAT`` card and the Alpaca/NINA paths give the full four-letter
#: form; the native ZWO and Player One bindings report the TOP-LEFT PAIR only
#: (``devices/cameras/zwo_asi_sdk.py`` ``_BAYER``), and ``CameraFrame`` carries
#: whichever the driver produced. Both are accepted so an OSC camera is not
#: debayered on one backend and averaged to grey on another.
_BAYER_4 = ("RGGB", "BGGR", "GRBG", "GBRG")
_BAYER_2 = {"RG": "RGGB", "BG": "BGGR", "GR": "GRBG", "GB": "GBRG"}


def channel_for(filter_name: str | None) -> str:
    """The composite channel a filter name feeds. Never raises, never empty."""
    key = (filter_name or "").strip().upper()
    return _ALIASES.get(key, "L")


def normalise_bayer(pattern: str | None) -> str | None:
    """A Bayer pattern as one of :data:`_BAYER_4`, or None when there is none.

    None is the answer for a mono sensor, for an empty card, AND for anything
    unrecognised -- guessing a pattern would swap red for blue over the whole
    session, which is worse than the grey composite this replaces.
    """
    p = (pattern or "").strip().upper()
    if p in _BAYER_4:
        return p
    return _BAYER_2.get(p)


def effective_bayer(pattern: str | None, binning: int | None = 1) -> str | None:
    """The pattern to actually debayer with, given the frame's binning.

    Binning above 1x1 SUMS a 2x2 (or larger) block on the sensor, so the colours
    are already mixed in the pixels that arrive and the mosaic is gone. The card
    still says ``RGGB`` -- it describes the sensor, not the readout -- so a
    debayer driven off the card alone would split a binned frame into three
    planes of the same grey. Both callers (the live frame loop and the backfill)
    go through here.
    """
    if int(binning or 1) > 1:
        return None
    return normalise_bayer(pattern)


def channels_for(filter_name: str | None,
                 bayer_pattern: str | None = None) -> tuple[str, ...]:
    """The composite channels one frame feeds.

    ``("R", "G", "B")`` for a bayered frame whose filter claims no bandpass --
    an OSC camera shooting broadband, which is the whole point of owning one.
    A single channel otherwise, including for a bayered frame through a NAMED
    filter: ``Ha`` on an OSC is a monochrome measurement of one line, and the
    only thing splitting it into three planes would show is the sensor's colour
    filter array.
    """
    ch = channel_for(filter_name)
    if ch == "L" and normalise_bayer(bayer_pattern):
        return OSC_CHANNELS
    return (ch,)


def debayer_superpixel(data: np.ndarray,
                       pattern: str | None) -> dict[str, np.ndarray] | None:
    """Split a Bayer mosaic into R, G, B at HALF resolution. None if not bayered.

    One 2x2 cell becomes one output pixel: the single R photosite, the mean of
    the two G photosites, the single B photosite. No interpolation at all, which
    is the right trade here for three reasons and not merely the cheap one:

      * the accumulator is binned to ``MAX_STACK_SIDE`` regardless, so a
        bilinear or VNG demosaic's extra detail is thrown away one step later;
      * halving both axes IS half of the binning the memory policy already
        demands, so this composes with :func:`block_mean` instead of fighting
        it -- the caller finishes the job with ``factor // 2`` and every channel
        lands on the same grid as a mono channel binned by ``factor``;
      * an interpolating demosaic invents correlations between neighbouring
        pixels, and the stacker's sigma clip reads those as signal.

    The frame is cropped to whole cells, which also means the crop starts at the
    sensor origin -- the only place the pattern in the header is known to apply.
    """
    pat = normalise_bayer(pattern)
    if pat is None:
        return None
    a = np.asarray(data)
    if a.ndim != 2:
        return None
    h = (a.shape[0] // 2) * 2
    w = (a.shape[1] // 2) * 2
    if h < 2 or w < 2:
        return None
    q = a[:h, :w].astype(np.float32)
    tl, tr = q[0::2, 0::2], q[0::2, 1::2]
    bl, br = q[1::2, 0::2], q[1::2, 1::2]
    #: (red cell, the two green cells, blue cell) for each pattern.
    layout = {
        "RGGB": (tl, (tr, bl), br),
        "BGGR": (br, (tr, bl), tl),
        "GRBG": (tr, (tl, br), bl),
        "GBRG": (bl, (tl, br), tr),
    }
    r, (g1, g2), b = layout[pat]
    planes = {"R": r, "G": (g1 + g2) * 0.5, "B": b}
    return {c: np.clip(np.rint(v), 0, 65535).astype(np.uint16)
            for c, v in planes.items()}


def frame_key(path: str | os.PathLike | None) -> str:
    """A stable identity for one sub on disk, or "" when it has none.

    Used to make the backfill idempotent, so it must agree between the live path
    (which knows the frame as ``info["saved_path"]``) and the backfill (which
    knows it as the session ledger's ``path``). Both are strings the app itself
    wrote, so ``normcase``/``normpath`` is enough and, unlike ``resolve()``,
    costs no stat and cannot be changed under us by a symlink.
    """
    if not path:
        return ""
    return os.path.normcase(os.path.normpath(str(path)))


def downsample_factor(shape: tuple[int, int], *, max_side: int = MAX_STACK_SIDE,
                      minimum: int = MIN_DOWNSAMPLE) -> int:
    """Power-of-two binning factor that brings ``shape``'s long edge under
    ``max_side``, floored at ``minimum``.

    Powers of two only: an arbitrary factor makes the crop below throw away up
    to factor-1 rows, and doubling keeps that at a handful of pixels.
    """
    long_edge = max(int(shape[0]), int(shape[1]))
    f = max(1, int(minimum))
    while long_edge // f > max_side:
        f *= 2
    return f


def block_mean(data: np.ndarray, factor: int) -> np.ndarray:
    """Mean of every ``factor`` x ``factor`` block, as uint16.

    The frame is cropped to a whole number of blocks first, so at factor 4 a
    6252x4176 frame loses nothing (both divide) and an odd sensor loses at most
    three rows and three columns off the far edge -- pixels no stack is going
    to miss.
    """
    f = max(1, int(factor))
    if f == 1:
        return np.asarray(data)
    a = np.asarray(data)
    h = (a.shape[0] // f) * f
    w = (a.shape[1] // f) * f
    if h == 0 or w == 0:
        return a
    view = a[:h, :w].astype(np.float32).reshape(h // f, f, w // f, f)
    return np.clip(np.rint(view.mean(axis=(1, 3))), 0, 65535).astype(np.uint16)


@dataclass
class ChannelStatus:
    channel: str
    frames: int
    integrated_s: float
    rejected: int

    def to_dict(self) -> dict:
        return {"channel": self.channel, "frames": self.frames,
                "integrated_s": round(self.integrated_s, 1),
                "rejected": self.rejected}


@dataclass
class BackfillProgress:
    """Where the "stack the subs I already shot" pass has got to.

    Lives on the stacker rather than on whatever spawned the pass because the
    STATUS ROUTE is what the UI polls, and a counter the UI cannot reach is not
    a progress counter. The stacker still reads nothing off disk: the caller
    walks the files and reports each one through :meth:`SessionStacker.
    backfill_step`.

    ``done`` counts frames CONSIDERED (added + skipped + failed), so
    ``done == total`` is the completion test whatever happened to each frame,
    and a stall is visible as a ``done`` that stops moving.
    """
    running: bool = False
    total: int = 0
    done: int = 0
    #: folded into a channel
    added: int = 0
    #: already in the stack -- the live path or an earlier backfill took it
    skipped: int = 0
    #: unreadable, or refused by the stacker (no stars, drifted off the field)
    failed: int = 0
    #: the channel the frame in hand landed on, for a live caption
    channel: str = ""
    #: set when the pass itself died; a single frame failing only bumps `failed`
    error: str = ""
    started_ts: float = 0.0
    finished_ts: float = 0.0

    def to_dict(self) -> dict:
        return {
            "running": self.running, "total": self.total, "done": self.done,
            "added": self.added, "skipped": self.skipped,
            "failed": self.failed, "channel": self.channel,
            "error": self.error,
            "started_ts": round(self.started_ts, 3) or None,
            "finished_ts": round(self.finished_ts, 3) or None,
        }


def stretch_channels(planes: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Every channel's running mean -> display [0,1], on ONE scale.

    Per-channel black (that channel's background median) neutralises the sky;
    one shared range above it (the widest channel's 99.8th percentile over its
    own background) keeps the channels comparable, so a target brighter in red
    than in green stays red. asinh lifts the faint end.

    A set of channels with no range at all -- a single flat frame through a
    blackout slot -- comes back black rather than as an amplified noise field.
    """
    blacks = {c: float(np.median(m)) for c, m in planes.items()}
    rng = 0.0
    for c, m in planes.items():
        rng = max(rng, float(np.percentile(m, WHITE_PERCENTILE)) - blacks[c])
    if not math.isfinite(rng) or rng <= 0:
        return {c: np.zeros(m.shape, dtype=np.float32)
                for c, m in planes.items()}
    out: dict[str, np.ndarray] = {}
    for c, m in planes.items():
        z = np.clip((np.asarray(m, dtype=np.float32) - blacks[c]) / rng, 0.0, 1.0)
        out[c] = (np.arcsinh(z * ASINH_A) / math.asinh(ASINH_A)).astype(np.float32)
    return out


class SessionStacker:
    """One ``LiveStacker`` per filter plus the composite over them.

    ``enabled`` is the user's switch and survives a run ending; ``reset`` throws
    the pixels away. ``add`` is the only write path; it is called from the
    sequence engine's frame loop once per ACCEPTED light, and from a backfill
    worker for the subs the run accepted BEFORE the switch was flipped. Both
    hold ``self._lock`` for the whole accumulation -- see the module docstring.
    """

    def __init__(self, *, max_side: int = MAX_STACK_SIDE,
                 clip_sigma: float = 4.0,
                 min_render_interval_s: float = MIN_RENDER_INTERVAL_S) -> None:
        self.max_side = int(max_side)
        self.clip_sigma = float(clip_sigma)
        self.min_render_interval_s = float(min_render_interval_s)
        self.enabled = False
        #: Guards every read and write of the accumulators, the identity, the
        #: seen-set and the render cache. RLock because ``add`` can call
        #: ``reset`` and ``rgb_preview`` calls ``status``.
        self._lock = threading.RLock()
        self._stacks: dict[str, LiveStacker] = {}
        #: Frames already folded in, by :func:`frame_key`. THE reason enabling,
        #: disabling and re-enabling cannot double-count, and the reason a
        #: backfill skips whatever the live path has already taken. A frame with
        #: no key (a NINA sub, an unsaved capture) is stacked and not recorded:
        #: there is nothing to compare a later sighting against.
        self._seen: set[str] = set()
        #: Bumped by ``reset``/``stop``. A backfill worker carries the value it
        #: started with and gives up when it changes, so switching the stack off
        #: or pressing Reset stops the disk reads instead of filling a stack the
        #: operator just threw away.
        self._generation = 0
        self._backfill = BackfillProgress()
        self._target = ""
        #: Which RUN these pixels belong to (the engine's report id). A second
        #: run on the same target is a new picture -- the mount was re-centred,
        #: the rotator may have moved, and last night's stack is not this
        #: night's -- so the identity is (target, run), not target alone.
        self._session = ""
        self._factor = 0
        #: bumped on every accepted add, so a client can tell "the picture
        #: changed" from "I polled again" without decoding the JPEG.
        self._seq = 0
        #: Rendered JPEGs by CHANNEL, with ``None`` for the composite. One
        #: entry per view the UI can ask for, because a client watching the Ha
        #: channel and a client watching the composite are polling the same
        #: stacker and a single-slot cache would make each of them re-render
        #: the other's picture on every poll.
        self._cache: dict[str | None, tuple[bytes, dict]] = {}
        #: ``(seq, size, rendered_at)`` per cache entry -- the age and the
        #: identity have to be per entry too, or a fresh composite render would
        #: make a stale channel render look current.
        self._cache_stamp: dict[str | None, tuple[int, int, float]] = {}
        self._rendered_at = 0.0

    # ------------------------------------------------------------- lifecycle
    def start(self) -> dict:
        self.enabled = True
        return self.status()

    def stop(self) -> dict:
        """Switch off AND release the accumulators. Leaving 137 MB of float32
        planes alive behind a switch the user has turned off is not a cache, it
        is a leak with a nice name."""
        self.enabled = False
        self.reset()
        return self.status()

    def reset(self, target: str = "", session: str = "") -> dict:
        """Throw the pixels away. The OPERATOR's reset (and ``stop``), so it
        bumps the generation and any backfill in flight gives up -- carrying on
        filling a stack somebody just cleared would undo the button press."""
        with self._lock:
            self._generation += 1
            if not self._backfill.running:
                self._backfill = BackfillProgress()
            # A pass IN FLIGHT keeps its counters: the generation bump is what
            # stops it, and it stamps its own "stopped" on the way out. Handing
            # it a fresh zeroed block instead would leave the operator with
            # "1 of 0" -- the worker's remaining steps landing on a counter that
            # never knew about them.
            self._reseed(target, session)
            return self.status()

    def _reseed(self, target: str, session: str) -> None:
        """Drop the pixels and adopt a new (target, run) identity.

        Deliberately does NOT bump the generation: ``add`` calls this the first
        time a frame names a target, which on a freshly started stack is EVERY
        first frame -- including the backfill's own. Aborting a backfill on the
        reseed its own first frame caused would make the feature a one-frame
        no-op. The worker watches the identity instead (see ``run_backfill``).
        """
        self._stacks.clear()
        self._seen.clear()
        self._target = target
        self._session = session
        self._factor = 0
        self._seq += 1
        self._cache.clear()
        self._cache_stamp.clear()
        self._rendered_at = 0.0

    # ------------------------------------------------------------- accumulate
    def add(self, data: np.ndarray, filter_name: str | None,
            exposure_s: float, *, target: str | None = None,
            session: str | None = None,
            bayer_pattern: str | None = None,
            key: str | None = None) -> str | None:
        """Fold one accepted light into its filter's stack.

        Returns the channels it landed on joined by ``+`` (``"L"``, ``"Ha"``,
        or ``"R+G+B"`` for a debayered OSC sub), or None when nothing was
        stacked -- disabled, no pixels, no stars to register on, or a frame this
        stacker has already consumed. A target or run different from the one in
        hand resets first: a stack that spans two objects is not a picture of
        either.

        ``bayer_pattern`` is the sub's own mosaic (``CameraFrame.bayer_pattern``
        live, ``BAYERPAT`` off disk); pass it through :func:`effective_bayer`
        with the frame's binning first. ``key`` is the frame's identity on disk
        and is what makes a second sighting a no-op -- see :attr:`_seen`.

        THE WHOLE BODY IS UNDER THE LOCK. The de-duplication check and the
        accumulation it guards have to be one critical section, or a backfill
        worker and the frame loop can both pass the check for the same sub and
        both stack it. The caller does the expensive I/O (reading and decoding
        the FITS) before it gets here.
        """
        if not self.enabled:
            return None
        arr = np.asarray(data)
        if arr.ndim != 2 or arr.size == 0:
            return None
        ident = frame_key(key)
        with self._lock:
            if ident and ident in self._seen:
                return None
            new_target = self._target if target is None else (target or "")
            new_session = self._session if session is None else (session or "")
            if (new_target, new_session) != (self._target, self._session):
                self._reseed(new_target, new_session)
            if not self._factor:
                self._factor = downsample_factor(arr.shape,
                                                 max_side=self.max_side)
            factor = self._factor

            planes, shared = self._planes(arr, filter_name, bayer_pattern,
                                          factor)
            landed: list[str] = []
            for ch, small in planes.items():
                stack = self._stacks.get(ch)
                if stack is None:
                    stack = LiveStacker(clip_sigma=self.clip_sigma)
                    self._stacks[ch] = stack
                outcome = stack.add(small, float(exposure_s or 0.0),
                                    stars=shared)
                if outcome.accepted:
                    landed.append(ch)
            if not landed:
                return None
            # Recorded only on a frame that actually contributed, so a sub the
            # stacker refused can be retried by a later backfill rather than
            # being permanently written off.
            if ident:
                self._seen.add(ident)
            self._seq += 1
            return "+".join(landed)

    def _planes(self, arr: np.ndarray, filter_name: str | None,
                bayer_pattern: str | None, factor: int
                ) -> tuple[dict[str, np.ndarray], list | None]:
        """The channel planes one frame contributes, binned to the stack grid,
        plus the star list to register all of them against (None = let each
        stacker detect its own).

        For an OSC frame the three planes are registered against ONE
        constellation, detected on the green plane. That is not an optimisation.
        Left to themselves the R stacker would measure its own shift off the red
        photosites and the B stacker off the blue, they would disagree by a
        fraction of a pixel on a good night and by whole pixels on a red or blue
        field with few stars, and every star in the composite would wear a
        coloured fringe -- the one defect the module docstring calls out as
        making a composite look broken rather than rough. Green is the reference
        because it has two photosites per cell and so the best signal to detect
        on. Identical inputs also mean the three stackers make identical
        accept/reject/reseed decisions, so the channels stay frame-for-frame in
        step.
        """
        chans = channels_for(filter_name, bayer_pattern)
        if chans != OSC_CHANNELS:
            return {chans[0]: block_mean(arr, factor)}, None
        deb = debayer_superpixel(arr, bayer_pattern)
        if deb is None:                       # unreachable via channels_for
            return {channel_for(filter_name): block_mean(arr, factor)}, None
        # The superpixel split already halved both axes, so it has done one
        # power of two of the binning the memory policy asks for; finish the
        # job. factor is even by construction (MIN_DOWNSAMPLE == 2, doubling
        # only), so the two compose exactly and an OSC channel lands on the
        # same grid as a mono channel binned by `factor`.
        rest = max(1, factor // 2)
        planes = {c: block_mean(p, rest) for c, p in deb.items()}
        from .stars import detect_stars
        return planes, detect_stars(planes["G"])

    # -------------------------------------------------------------- backfill
    @property
    def generation(self) -> int:
        """Bumped by every ``reset``/``stop``. A backfill worker compares this
        against the value it started with to notice that the stack it is filling
        has been thrown away."""
        return self._generation

    @property
    def backfill(self) -> BackfillProgress:
        return self._backfill

    def has_frame(self, key: str | None) -> bool:
        """Whether this sub is already in the stack. A cheap pre-check so a
        backfill can skip the disk read; ``add`` re-checks under the lock, which
        is the check that actually decides."""
        ident = frame_key(key)
        if not ident:
            return False
        with self._lock:
            return ident in self._seen

    def backfill_begin(self, total: int) -> BackfillProgress:
        """Arm the progress counter for a pass over ``total`` frames."""
        with self._lock:
            self._backfill = BackfillProgress(running=True, total=int(total),
                                              started_ts=time.time())
            if not total:
                # Nothing to do is DONE, not pending. A counter that sits at
                # "0 of 0, running" forever is a hang as far as the UI can tell.
                self._backfill.running = False
                self._backfill.finished_ts = self._backfill.started_ts
            return self._backfill

    def backfill_step(self, *, added: str = "", skipped: bool = False,
                      failed: bool = False) -> BackfillProgress:
        """One frame considered. Exactly one of the three outcomes."""
        with self._lock:
            p = self._backfill
            p.done += 1
            if skipped:
                p.skipped += 1
            elif failed:
                p.failed += 1
            else:
                p.added += 1
                p.channel = added
            return p

    def backfill_finish(self, error: str = "") -> BackfillProgress:
        with self._lock:
            p = self._backfill
            p.running = False
            p.error = str(error or "")
            p.finished_ts = time.time()
            p.channel = ""
            return p

    # ---------------------------------------------------------------- status
    @property
    def seq(self) -> int:
        return self._seq

    @property
    def target(self) -> str:
        return self._target

    @property
    def session(self) -> str:
        return self._session

    def channels(self) -> list[ChannelStatus]:
        with self._lock:
            out = [ChannelStatus(ch, s.frames, s.integrated_s, s.rejected)
                   for ch, s in self._stacks.items()]
        out.sort(key=lambda c: CHANNEL_ORDER.index(c.channel)
                 if c.channel in CHANNEL_ORDER else 99)
        return out

    def status(self) -> dict:
        with self._lock:
            chans = self.channels()
            age = (round(time.time() - self._rendered_at, 1)
                   if self._rendered_at else None)
            return {
                "enabled": self.enabled,
                "target": self._target,
                "session": self._session,
                "seq": self._seq,
                "channels": [c.to_dict() for c in chans],
                "frames": sum(c.frames for c in chans),
                "integrated_s": round(sum(c.integrated_s for c in chans), 1),
                "rejected": sum(c.rejected for c in chans),
                "mode": self.mode(),
                "downsample": self._factor,
                "has_image": bool(chans),
                "render_age_s": age,
                "backfill": self._backfill.to_dict(),
            }

    def mode(self) -> str | None:
        """What kind of composite the channels in hand make: broadband colour,
        narrowband colour, or a single-channel grey. None when empty."""
        have = {c.channel for c in self.channels()}
        if not have:
            return None
        if have & {"R", "G", "B"}:
            return "rgb"
        if have & {"Ha", "Oiii", "Sii"}:
            return "narrowband"
        return "mono"

    # -------------------------------------------------------------- composite
    def _aligned_planes(self) -> tuple[dict[str, np.ndarray], int, int] | None:
        """Every channel's running mean, on one grid, shifted onto the busiest
        channel's reference frame."""
        means: dict[str, np.ndarray] = {}
        for ch, s in self._stacks.items():
            m = s.mean()
            if m is not None:
                means[ch] = m
        if not means:
            return None
        # The busiest channel is the reference: it has the most frames behind
        # its constellation and the least to gain from being moved.
        primary = max(means, key=lambda c: (self._stacks[c].frames,
                                            -CHANNEL_ORDER.index(c)
                                            if c in CHANNEL_ORDER else -99))
        h, w = means[primary].shape
        ref_stars = getattr(self._stacks[primary], "_ref_stars", [])

        out: dict[str, np.ndarray] = {}
        for ch, m in means.items():
            if m.shape != (h, w):
                # Binning changed mid-session. Resample rather than drop the
                # channel: half a colour image beats none. Through mode "F",
                # because Pillow's uint16 "I;16" images do not resample.
                m = np.asarray(
                    Image.fromarray(m.astype(np.float32), mode="F")
                    .resize((w, h), Image.BILINEAR)).astype(np.uint16)
            if ch == primary:
                out[ch] = m
                continue
            reg = None
            cur_stars = getattr(self._stacks[ch], "_ref_stars", [])
            if ref_stars and cur_stars:
                try:
                    reg = register(ref_stars, cur_stars)
                except Exception:
                    reg = None
            if reg is None:
                out[ch] = m
                continue
            dx, dy = int(round(reg.dx)), int(round(reg.dy))
            if abs(dx) >= w or abs(dy) >= h:
                out[ch] = m               # a shift that empties the frame is wrong
                continue
            shifted = np.zeros_like(m)
            ys_dst, ys_src = _axis_slices(h, dy)
            xs_dst, xs_src = _axis_slices(w, dx)
            shifted[ys_dst, xs_dst] = m[ys_src, xs_src]
            out[ch] = shifted
        return out, h, w

    def compose(self) -> np.ndarray | None:
        """The composite as float RGB in [0,1], shape (h, w, 3). None when empty.

        L is folded in last and as a LUMINANCE SUBSTITUTION: each channel gets
        ``L - y`` added, where ``y`` is the composite's own luminance. The
        differences between channels (the colour) survive untouched while the
        brightness becomes L's, which is what LRGB means. It also degenerates
        correctly: a session with nothing but L has zero colour, so the same
        line produces grey rather than needing a second code path.
        """
        with self._lock:
            got = self._aligned_planes()
            if got is None:
                return None
            planes, h, w = got
        # Out of the lock from here: `planes` is already a private copy of every
        # channel's mean, so a frame landing mid-render changes the NEXT picture
        # rather than this one -- and the render is the slowest thing the
        # stacker does, which is exactly what must not block the frame loop.
        stretched = stretch_channels(planes)

        rgb = np.zeros((h, w, 3), dtype=np.float32)
        for i, key in enumerate(("r", "g", "b")):
            parts = [stretched[c] for c in CHANNEL_MIX[key] if c in stretched]
            if parts:
                rgb[:, :, i] = np.mean(parts, axis=0)

        lum = stretched.get("L")
        if lum is not None:
            y = (0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1]
                 + 0.114 * rgb[:, :, 2])
            rgb += (lum - y)[:, :, None]
        return np.clip(rgb, 0.0, 1.0)

    # ------------------------------------------------------------ render cache
    def _cached(self, key: str | None, size: int,
                now: float) -> tuple[bytes, dict] | None:
        """The stored render for one view, or None. Call under the lock.

        The rule is the one the composite has always used, applied per entry: a
        render is reused until a frame is added AND ``min_render_interval_s``
        has passed since THAT entry was built, so a phone polling every second
        costs one dict lookup rather than a percentile over a few megapixels.
        """
        got = self._cache.get(key)
        if got is None:
            return None
        seq, cached_size, at = self._cache_stamp.get(key, (-1, 0, 0.0))
        if cached_size != int(size):
            return None
        if seq == self._seq or now - at < self.min_render_interval_s:
            return got
        return None

    def _store(self, key: str | None, size: int, seq: int, now: float,
               payload: tuple[bytes, dict]) -> tuple[bytes, dict]:
        """Keep one render. Call under the lock.

        ``seq`` is the value read BEFORE the render started, not the one that
        stands now: stamping the entry with the current seq would label this
        picture with a frame it does not contain, and the next poll would be
        served the stale one as current.
        """
        self._cache[key] = payload
        self._cache_stamp[key] = (int(seq), int(size), now)
        self._rendered_at = now
        # Bound the cache at one composite plus one per channel. It cannot grow
        # past that by construction -- the keys are None and the output of
        # `channel_for`, which is always one of CHANNEL_ORDER -- so this loop is
        # unreachable today. It is here so that adding an alias that folds onto
        # an eighth channel cannot quietly turn the cache into a leak, and it
        # drops the entry from BOTH dicts rather than only flagging it.
        while len(self._cache) > len(CHANNEL_ORDER) + 1:
            oldest = min(self._cache,
                         key=lambda k: self._cache_stamp.get(k, (0, 0, 0.0))[2])
            self._cache.pop(oldest, None)
            self._cache_stamp.pop(oldest, None)
        return payload

    def rgb_preview(self, size: int = DEFAULT_PREVIEW_SIZE, *,
                    quality: int = 85) -> tuple[bytes, dict] | None:
        """(JPEG bytes, meta) for the composite, or None when nothing is stacked.

        Cached under the key ``None`` -- see :meth:`_cached`.
        """
        now = time.time()
        with self._lock:
            hit = self._cached(None, size, now)
            if hit is not None:
                return hit
            # The seq the render is ABOUT to be built from, read before the lock
            # is dropped.
            seq_at_start = self._seq

        rgb = self.compose()
        if rgb is None:
            with self._lock:
                self._cache.pop(None, None)
                self._cache_stamp.pop(None, None)
            return None
        arr8 = (rgb * 255.0 + 0.5).astype(np.uint8)
        pil = Image.fromarray(arr8, mode="RGB")
        if size and pil.width > int(size):
            scale = int(size) / pil.width
            pil = pil.resize((int(size), max(1, int(pil.height * scale))),
                             Image.BILINEAR)
        buf = io.BytesIO()
        pil.save(buf, format="JPEG", quality=quality, optimize=False)

        with self._lock:
            meta = dict(self.status())
            meta.update({"width": pil.width, "height": pil.height})
            return self._store(None, size, seq_at_start, now,
                               (buf.getvalue(), meta))

    def channel_preview(self, channel: str, size: int = DEFAULT_PREVIEW_SIZE,
                        *, quality: int = 85) -> tuple[bytes, dict] | None:
        """(JPEG bytes, meta) for ONE channel's running mean, or None.

        The accumulators were always per channel; this is the render that was
        missing, so "show me just Ha" stops being a colour filter over the
        composite and becomes the Ha stack.

        ``channel`` may be either a channel name (``"Ha"``) or the operator's
        own filter name (``"H-alpha"``, ``"HALPHA"``): it is resolved through
        :func:`channel_for`, which is the same fold ``add`` used to decide which
        accumulator the frame went into, so the two cannot disagree. The channel
        it actually rendered comes back in ``meta["channel"]`` -- the caller
        asked in the operator's vocabulary and has to be told the answer in the
        stacker's.

        None means REFUSE, and the route turns it into a 404. Two ways to get
        there and both matter:

          * an empty name is not "the L channel", it is "no channel asked for".
            ``channel_for("")`` is ``L``, so folding it would serve one filter
            to a caller who asked for the picture;
          * a channel with no accumulator (``Sii`` on a night that shot none)
            has no pixels. Rendering it anyway would produce a black frame,
            which on a monitor page at 3am is indistinguishable from a dead
            sensor or a closed shutter.
        """
        name = (channel or "").strip()
        if not name:
            return None
        key = channel_for(name)
        now = time.time()
        with self._lock:
            if key not in self._stacks:
                return None
            hit = self._cached(key, size, now)
            if hit is not None:
                return hit
            mean = self._stacks[key].mean()
            if mean is None:
                return None
            seq_at_start = self._seq

        # Out of the lock from here: `LiveStacker.mean()` builds a fresh array,
        # so a frame landing mid-render changes the NEXT picture, not this one.
        #
        # DELIBERATELY NOT `_aligned_planes`. That shifts every channel onto the
        # busiest channel's reference frame, which is what stops a COMPOSITE
        # wearing coloured fringes. One channel is already on its own reference
        # frame -- it is the frame its own stacker registered every one of its
        # subs against -- so there is nothing to align it to, and a shift here
        # would move the picture for no reason.
        #
        # A one-entry `stretch_channels` degenerates correctly rather than
        # accidentally: black is this channel's own background median, and the
        # shared range is a max over a single channel, i.e. this channel's own
        # 99.8th percentile. So the single-channel view is the composite's own
        # scale RESTRICTED to one plane, not a second stretch model that could
        # show the same pixels at a different brightness.
        plane = stretch_channels({key: mean})[key]
        arr8 = (np.clip(plane, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
        pil = Image.fromarray(arr8, mode="L")
        if size and pil.width > int(size):
            scale = int(size) / pil.width
            pil = pil.resize((int(size), max(1, int(pil.height * scale))),
                             Image.BILINEAR)
        buf = io.BytesIO()
        pil.save(buf, format="JPEG", quality=quality, optimize=False)

        with self._lock:
            stack = self._stacks.get(key)
            if stack is None:
                # A reset landed while this was rendering. The picture is of a
                # stack that no longer exists, so it must not be cached as the
                # current one -- and it must not be returned either.
                return None
            meta = dict(self.status())
            meta.update({
                "width": pil.width, "height": pil.height,
                "channel": key,
                # THIS channel's counts, replacing the whole-stack totals
                # `status()` carries. The caption under a single-channel view
                # has to say how much went into that channel; reporting the
                # night's total beside one filter's pixels is the caption
                # lying about the picture it sits under.
                "frames": stack.frames,
                "integrated_s": round(stack.integrated_s, 1),
                "rejected": stack.rejected,
            })
            return self._store(key, size, seq_at_start, now,
                               (buf.getvalue(), meta))
