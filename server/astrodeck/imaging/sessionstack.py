# Copyright (c) 2026 James Penick
# SPDX-License-Identifier: Apache-2.0
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

**One picture per panel, kept across visits.** A rotating mosaic hands the
stacker a frame of panel A, then B, then C, and back to A. The stack used to
have ONE identity, (target, run), and reseeded whenever it changed, so every hop
threw the previous panel's pixels away and the live stack never built. Each
panel is now its own slot (:class:`_Slot`), keyed by the target's id when the
caller gives one (names repeat in a plan with no group; ids do not) and else by
its name, and only the RUN is shared: a second run is a new picture for every
panel. Reads default to the FOREGROUND panel, the one the latest accepted frame
fed, so a one-target night is a one-slot stacker that answers exactly as the old
single stack did. Memory is the price of keeping panels, so there is a budget
(``max_bytes``): when an add takes the accumulators of every panel past it, the
panel least recently ADDED to (never the one being fed) is released, named in
``evicted``, and starts again if the run returns to it.

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
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from ..events import bus
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
#: The most the accumulators of every panel may hold together, in bytes (decimal
#: MB, the unit the arithmetic in the module docstring uses). One panel costs
#: 19.6 MB per filter at the rig's 1563x1044 grid, so an LRGB panel is 78 MB and
#: this holds seven of them; a 3x3 mosaic would ask for about 705 MB on a box
#: with 4 GB, and the stack gives up its least recently fed panel before it
#: takes the rest of the box.
DEFAULT_MAX_BYTES = 600_000_000

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


@dataclass
class _Slot:
    """One panel's picture: its accumulators and everything that describes them.

    Everything that used to be a field of the stacker and described THE picture
    lives here, so a panel is dropped, evicted or re-rendered by dealing with
    one object and nothing about it can outlive it. That includes the render
    cache: a cache held by the stacker had to be purged by hand wherever a
    picture died, and the day one purge was missed a dead panel's pixels would
    have kept serving under a live panel's name.
    """
    #: The panel key: the target's id when the caller gave one, else its name.
    key: str
    #: The name to show. Several panels can share one (ids are unique where
    #: names can repeat in a plan with no group).
    target: str
    stacks: dict[str, LiveStacker] = field(default_factory=dict)
    #: Frames already folded in, by :func:`frame_key`. See
    #: :meth:`SessionStacker._holds` for why the check is across ALL slots.
    seen: set[str] = field(default_factory=set)
    factor: int = 0
    #: The stacker's change counter as it stood at this panel's last accepted
    #: add. Monotonic across panels, so it doubles as the "least recently
    #: added" clock the budget evicts by.
    seq: int = 0
    #: Rendered JPEGs by CHANNEL, with ``None`` for the composite. One entry per
    #: view the UI can ask for, because a client watching the Ha channel and a
    #: client watching the composite are polling the same panel and a
    #: single-slot cache would make each of them re-render the other's picture
    #: on every poll.
    cache: dict[str | None, tuple[bytes, dict]] = field(default_factory=dict)
    #: ``(seq, size, rendered_at)`` per cache entry -- the age and the
    #: identity have to be per entry too, or a fresh composite render would
    #: make a stale channel render look current.
    cache_stamp: dict[str | None, tuple[int, int, float]] = field(
        default_factory=dict)
    rendered_at: float = 0.0

    def nbytes(self) -> int:
        """What this panel's accumulators hold, measured and not estimated: the
        three float32 planes of every channel's ``LiveStacker``. Read each time,
        because a stacker that re-seeds (the frame size changed) swaps its
        arrays."""
        total = 0
        for stack in self.stacks.values():
            for name in ("_sum", "_sumsq", "_cov"):
                plane = getattr(stack, name, None)
                if plane is not None:
                    total += int(plane.nbytes)
        return total


class SessionStacker:
    """One ``LiveStacker`` per filter, per PANEL, plus the composite over them.

    A panel is whatever the caller names with ``target`` / ``target_id`` -- a
    mosaic tile, or simply the one target of an ordinary night. Each panel is a
    :class:`_Slot` and they are kept across visits: a rotating mosaic comes
    back to panel A with A's pixels still in it. The one identity that is NOT
    per panel is the RUN (``session``): a second run is a new picture for every
    panel, because the mount was re-centred and the rotator may have moved.

    The FOREGROUND panel is the one the most recent accepted frame fed. Every
    read that is not told a ``panel`` describes it, which is what makes a
    one-target night answer exactly as it did when there was one picture.

    ``enabled`` is the user's switch and survives a run ending; ``reset``
    throws the pixels away. ``add`` is the only write path; it is called from
    the sequence engine's frame loop once per ACCEPTED light, and from a
    backfill worker for the subs the run accepted BEFORE the switch was
    flipped. Both hold ``self._lock`` for the whole accumulation -- see the
    module docstring.
    """

    def __init__(self, *, max_side: int = MAX_STACK_SIDE,
                 clip_sigma: float = 4.0,
                 min_render_interval_s: float = MIN_RENDER_INTERVAL_S,
                 max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self.max_side = int(max_side)
        self.clip_sigma = float(clip_sigma)
        self.min_render_interval_s = float(min_render_interval_s)
        #: The most the accumulators of ALL panels may hold together. Checked
        #: after an add, from the measured arrays; see :meth:`_enforce_budget`.
        self.max_bytes = int(max_bytes)
        self.enabled = False
        #: Guards every read and write of the slots, the identity and the
        #: evicted list. RLock because ``add`` can call ``_drop_all`` and
        #: ``rgb_preview`` calls ``status``.
        self._lock = threading.RLock()
        #: Panels by key, in the order they were first seen (which is the order
        #: a chip row should show them in: a row that reshuffled at every hop
        #: would be a row nobody could tap).
        self._slots: dict[str, _Slot] = {}
        #: The key of the foreground panel, or None before anything is stacked
        #: and after every panel has been dropped.
        self._fg: str | None = None
        #: Names of panels whose pixels the budget released, in the order it
        #: did so, each once. A panel that has since come back and restarted is
        #: still listed: the point is that its count is not the night's.
        self._evicted: list[str] = []
        #: Bumped by ``reset``/``stop``. A backfill worker carries the value it
        #: started with and gives up when it changes, so switching the stack off
        #: or pressing Reset stops the disk reads instead of filling a stack the
        #: operator just threw away.
        self._generation = 0
        self._backfill = BackfillProgress()
        #: The foreground panel's display name, kept after a Reset so the empty
        #: picture's caption still says what was cleared.
        self._target = ""
        #: Which RUN these pixels belong to (the engine's report id). A second
        #: run on the same target is a new picture -- the mount was re-centred,
        #: the rotator may have moved, and last night's stack is not this
        #: night's -- so a run change drops EVERY panel.
        self._session = ""
        #: bumped on every accepted add, so a client can tell "the picture
        #: changed" from "I polled again" without decoding the JPEG. One
        #: counter for the whole stacker: a per-panel counter could repeat a
        #: value when the foreground flips from one panel to another and a
        #: client would read two different pictures as unchanged.
        self._seq = 0

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
        """Throw EVERY panel's pixels away. The OPERATOR's reset (and ``stop``),
        so it bumps the generation and any backfill in flight gives up -- carrying
        on filling a stack somebody just cleared would undo the button press.

        ``session`` is the run to keep (the hub passes the current one, so the
        next frame of the same run is not a second reset), and ``target`` is the
        caption the emptied picture keeps."""
        with self._lock:
            self._generation += 1
            if not self._backfill.running:
                self._backfill = BackfillProgress()
            # A pass IN FLIGHT keeps its counters: the generation bump is what
            # stops it, and it stamps its own "stopped" on the way out. Handing
            # it a fresh zeroed block instead would leave the operator with
            # "1 of 0" -- the worker's remaining steps landing on a counter that
            # never knew about them.
            self._drop_all(session)
            self._target = target
            return self.status()

    def _drop_all(self, session: str) -> None:
        """Release every panel and adopt ``session`` as the run.

        Replaces the old ``_reseed``, which also adopted a TARGET: the target is
        no longer part of the identity, only the run is. Deliberately does NOT
        bump the generation: ``add`` calls this the first time a frame names a
        new run, which on a freshly started stack is EVERY first frame --
        including the backfill's own. Aborting a backfill on the drop its own
        first frame caused would make the feature a one-frame no-op. The worker
        watches the run instead (see ``run_backfill``).
        """
        self._slots.clear()
        self._evicted.clear()
        self._fg = None
        self._target = ""
        self._session = session
        self._seq += 1

    # ------------------------------------------------------------- accumulate
    @staticmethod
    def _panel_key(name: str, target_id: str | None) -> str:
        """The target's id when it has one, else its name. Ids are unique where
        names can repeat, so two targets both called "M42" in a plan with no
        group are two panels rather than one picture of both."""
        return str(target_id) if target_id else name

    def add(self, data: np.ndarray, filter_name: str | None,
            exposure_s: float, *, target: str | None = None,
            session: str | None = None,
            bayer_pattern: str | None = None,
            key: str | None = None,
            target_id: str | None = None) -> str | None:
        """Fold one accepted light into its panel's, and its filter's, stack.

        Returns the channels it landed on joined by ``+`` (``"L"``, ``"Ha"``,
        or ``"R+G+B"`` for a debayered OSC sub), or None when nothing was
        stacked -- disabled, no pixels, no stars to register on, or a frame this
        stacker has already consumed. The panel is ``target_id`` when given,
        else ``target``; with neither it is the foreground panel. A RUN
        different from the one in hand drops every panel first: a stack that
        spans two nights is not a picture of either. A different target is just
        a different panel and costs the others nothing.

        ``bayer_pattern`` is the sub's own mosaic (``CameraFrame.bayer_pattern``
        live, ``BAYERPAT`` off disk); pass it through :func:`effective_bayer`
        with the frame's binning first. ``key`` is the frame's identity on disk
        and is what makes a second sighting a no-op -- see :meth:`_holds`.

        A panel exists only once a frame has LANDED in it. A refused first frame
        (no stars, cloud) leaves no empty panel behind and does not take the
        foreground: an empty picture is not something to show.

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
        released: list[str] = []
        with self._lock:
            if ident and self._holds(ident):
                return None
            # Which panel, decided BEFORE a run change can clear the foreground
            # it would otherwise have defaulted to.
            if target is None and not target_id:
                held = self._slots.get(self._fg) if self._fg is not None else None
                name = held.target if held is not None else self._target
                pkey = self._fg if self._fg is not None else name
            else:
                name = target or ""
                pkey = self._panel_key(name, target_id)
            new_session = self._session if session is None else (session or "")
            reseeded = new_session != self._session
            if reseeded:
                self._drop_all(new_session)

            slot = self._slots.get(pkey)
            fresh = slot is None
            if fresh:
                slot = _Slot(key=pkey, target=name)
            if not slot.factor:
                slot.factor = downsample_factor(arr.shape,
                                                max_side=self.max_side)
            planes, shared = self._planes(arr, filter_name, bayer_pattern,
                                          slot.factor)
            landed: list[str] = []
            for ch, small in planes.items():
                stack = slot.stacks.get(ch)
                if stack is None:
                    stack = LiveStacker(clip_sigma=self.clip_sigma)
                    slot.stacks[ch] = stack
                outcome = stack.add(small, float(exposure_s or 0.0),
                                    stars=shared)
                if outcome.accepted:
                    landed.append(ch)
            if not landed:
                # A fresh slot built for this frame is simply never inserted.
                return None
            # Recorded only on a frame that actually contributed, so a sub the
            # stacker refused can be retried by a later backfill rather than
            # being permanently written off.
            if ident:
                slot.seen.add(ident)
            if fresh and not reseeded:
                # A new panel is a new picture on screen, which the old single
                # stack announced by reseeding. Keeps `seq` moving exactly as
                # it did for the first frame of a night.
                self._seq += 1
            self._seq += 1
            slot.seq = self._seq
            if fresh:
                self._slots[pkey] = slot
            self._fg = pkey
            self._target = slot.target
            result = "+".join(landed)
            released = self._enforce_budget(pkey)
        # Said outside the lock: the bus is not ours and a subscriber must never
        # be able to hold up the frame loop while the accumulators are locked.
        for name in released:
            bus.log("warning",
                    f"Session stack: released the {name} panel to stay under "
                    f"the {self.max_bytes // 1_000_000} MB memory budget; it "
                    "restarts when the run returns to it", "capture")
        return result

    def _holds(self, ident: str) -> bool:
        """Whether ANY panel already holds this frame. Call under the lock.

        Across all slots, not the panel being fed. A frame is a file on disk and
        belongs to one panel; if the backfill and the live path ever named its
        panel differently (an id on one side, a name on the other) a per-slot
        check would fold one photograph into two pictures.
        """
        return any(ident in s.seen for s in self._slots.values())

    def _held(self) -> int:
        """Bytes held by every panel's accumulators. Call under the lock."""
        return sum(s.nbytes() for s in self._slots.values())

    def held_bytes(self) -> int:
        with self._lock:
            return self._held()

    def _enforce_budget(self, fed: str) -> list[str]:
        """Release panels, least recently ADDED first, until the accumulators fit
        :attr:`max_bytes`. Returns the names newly recorded as evicted, for the
        caller to log once the lock is released. Call under the lock.

        Never the panel just fed: it is the picture being built, and a budget
        smaller than one panel would otherwise release the only thing there is.
        Such a panel simply exceeds the budget, which is the honest outcome.

        The budget is read AFTER the add, from the arrays themselves, so the
        figure is measured and cannot drift from what the frame really cost. The
        price is that the peak is the budget plus one panel's worth for the
        length of one add, which is a transient and not a leak.

        The evicted panel reseeds on its next visit: today's behaviour, for
        that panel only. A mosaic with more panels than the budget holds will
        reseed at every visit under a round-robin, because the panel least
        recently added is always the one the rotation is about to come back to.
        """
        newly: list[str] = []
        while self._held() > self.max_bytes:
            victims = [s for k, s in self._slots.items() if k != fed]
            if not victims:
                break
            victim = min(victims, key=lambda s: s.seq)
            del self._slots[victim.key]
            if victim.target not in self._evicted:
                self._evicted.append(victim.target)
                newly.append(victim.target)
        return newly

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
        """Whether this sub is already in the stack, in any panel. A cheap
        pre-check so a backfill can skip the disk read; ``add`` re-checks under
        the lock, which is the check that actually decides."""
        ident = frame_key(key)
        if not ident:
            return False
        with self._lock:
            return self._holds(ident)

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
        """The foreground panel's name."""
        return self._target

    @property
    def session(self) -> str:
        return self._session

    def _resolve(self, panel: str | None) -> _Slot | None:
        """The slot a ``panel`` argument means, or None. Call under the lock.

        No panel (None or empty) is the foreground. Otherwise the panel KEY
        wins, so an id always means the panel it names; failing that a target
        NAME, which is what a hand-typed URL will carry. Where several panels
        share the name the most recently added answers, because that is the one
        the run is shooting.
        """
        if panel is None or panel == "":
            return self._slots.get(self._fg) if self._fg is not None else None
        want = str(panel)
        hit = self._slots.get(want)
        if hit is not None:
            return hit
        named = [s for s in self._slots.values() if s.target == want]
        return max(named, key=lambda s: s.seq) if named else None

    def has_panel(self, panel: str | None) -> bool:
        """Whether ``panel`` names something stacked (None = the foreground)."""
        with self._lock:
            return self._resolve(panel) is not None

    def _channels_of(self, slot: _Slot | None) -> list[ChannelStatus]:
        if slot is None:
            return []
        out = [ChannelStatus(ch, s.frames, s.integrated_s, s.rejected)
               for ch, s in slot.stacks.items()]
        out.sort(key=lambda c: CHANNEL_ORDER.index(c.channel)
                 if c.channel in CHANNEL_ORDER else 99)
        return out

    def channels(self, panel: str | None = None) -> list[ChannelStatus]:
        with self._lock:
            return self._channels_of(self._resolve(panel))

    @staticmethod
    def _mode_of(chans: list[ChannelStatus]) -> str | None:
        have = {c.channel for c in chans}
        if not have:
            return None
        if have & {"R", "G", "B"}:
            return "rgb"
        if have & {"Ha", "Oiii", "Sii"}:
            return "narrowband"
        return "mono"

    def mode(self, panel: str | None = None) -> str | None:
        """What kind of composite the channels in hand make: broadband colour,
        narrowband colour, or a single-channel grey. None when empty."""
        with self._lock:
            return self._mode_of(self._channels_of(self._resolve(panel)))

    def _panels(self) -> list[dict]:
        out = []
        for s in self._slots.values():
            chans = self._channels_of(s)
            out.append({"key": s.key, "target": s.target,
                        "frames": sum(c.frames for c in chans),
                        "integrated_s": round(sum(c.integrated_s
                                                  for c in chans), 1),
                        "seq": s.seq})
        return out

    def _status_locked(self, slot: _Slot | None,
                       asked: str | None = None) -> dict:
        chans = self._channels_of(slot)
        rendered_at = slot.rendered_at if slot is not None else 0.0
        age = round(time.time() - rendered_at, 1) if rendered_at else None
        return {
            "enabled": self.enabled,
            "target": (slot.target if slot is not None
                       else (asked or self._target)),
            "session": self._session,
            "seq": slot.seq if slot is not None else self._seq,
            "channels": [c.to_dict() for c in chans],
            "frames": sum(c.frames for c in chans),
            "integrated_s": round(sum(c.integrated_s for c in chans), 1),
            "rejected": sum(c.rejected for c in chans),
            "mode": self._mode_of(chans),
            "downsample": slot.factor if slot is not None else 0,
            "has_image": bool(chans),
            "render_age_s": age,
            "backfill": self._backfill.to_dict(),
            "panels": self._panels(),
            "evicted": list(self._evicted),
        }

    def status(self, panel: str | None = None) -> dict:
        """The stack as one panel's picture (default: the foreground), plus the
        list of every panel.

        Every top-level key is the one it always was and describes the panel
        asked for, so a client that has never heard of panels reads the
        foreground exactly as it read the single picture. ``panels`` and
        ``evicted`` are the two additions, and they describe the whole stack
        whichever panel was asked about. A panel that is not stacked reads as an
        empty picture rather than raising: this is what a poll gets for a panel
        the budget released between two polls.
        """
        with self._lock:
            return self._status_locked(self._resolve(panel),
                                       asked=panel or None)

    # -------------------------------------------------------------- composite
    def _aligned_planes(self, slot: _Slot
                        ) -> tuple[dict[str, np.ndarray], int, int] | None:
        """Every channel of one panel's running mean, on one grid, shifted onto
        the busiest channel's reference frame."""
        means: dict[str, np.ndarray] = {}
        for ch, s in slot.stacks.items():
            m = s.mean()
            if m is not None:
                means[ch] = m
        if not means:
            return None
        # The busiest channel is the reference: it has the most frames behind
        # its constellation and the least to gain from being moved.
        primary = max(means, key=lambda c: (slot.stacks[c].frames,
                                            -CHANNEL_ORDER.index(c)
                                            if c in CHANNEL_ORDER else -99))
        h, w = means[primary].shape
        ref_stars = getattr(slot.stacks[primary], "_ref_stars", [])

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
            cur_stars = getattr(slot.stacks[ch], "_ref_stars", [])
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

    def compose(self, panel: str | None = None) -> np.ndarray | None:
        """One panel's composite as float RGB in [0,1], shape (h, w, 3). None
        when the panel is empty or not stacked (default: the foreground).

        L is folded in last and as a LUMINANCE SUBSTITUTION: each channel gets
        ``L - y`` added, where ``y`` is the composite's own luminance. The
        differences between channels (the colour) survive untouched while the
        brightness becomes L's, which is what LRGB means. It also degenerates
        correctly: a session with nothing but L has zero colour, so the same
        line produces grey rather than needing a second code path.
        """
        with self._lock:
            slot = self._resolve(panel)
        if slot is None:
            return None
        return self._compose_slot(slot)

    def _compose_slot(self, slot: _Slot) -> np.ndarray | None:
        with self._lock:
            if self._slots.get(slot.key) is not slot:
                return None               # dropped or released since it was named
            got = self._aligned_planes(slot)
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
    def _cached(self, slot: _Slot, key: str | None, size: int,
                now: float) -> tuple[bytes, dict] | None:
        """The stored render of one panel's view, or None. Call under the lock.

        The rule is the one the composite has always used, applied per entry: a
        render is reused until a frame is added to THIS PANEL AND
        ``min_render_interval_s`` has passed since THAT entry was built, so a
        phone polling every second costs one dict lookup rather than a
        percentile over a few megapixels, and a frame landing on panel B is not
        a reason to re-render panel A.
        """
        got = slot.cache.get(key)
        if got is None:
            return None
        seq, cached_size, at = slot.cache_stamp.get(key, (-1, 0, 0.0))
        if cached_size != int(size):
            return None
        if seq == slot.seq or now - at < self.min_render_interval_s:
            return got
        return None

    def _store(self, slot: _Slot, key: str | None, size: int, seq: int,
               now: float, payload: tuple[bytes, dict]) -> tuple[bytes, dict]:
        """Keep one render. Call under the lock.

        ``seq`` is the value read BEFORE the render started, not the one that
        stands now: stamping the entry with the current seq would label this
        picture with a frame it does not contain, and the next poll would be
        served the stale one as current.
        """
        slot.cache[key] = payload
        slot.cache_stamp[key] = (int(seq), int(size), now)
        slot.rendered_at = now
        # Bound the cache at one composite plus one per channel. It cannot grow
        # past that by construction -- the keys are None and the output of
        # `channel_for`, which is always one of CHANNEL_ORDER -- so this loop is
        # unreachable today. It is here so that adding an alias that folds onto
        # an eighth channel cannot quietly turn the cache into a leak, and it
        # drops the entry from BOTH dicts rather than only flagging it.
        while len(slot.cache) > len(CHANNEL_ORDER) + 1:
            oldest = min(slot.cache,
                         key=lambda k: slot.cache_stamp.get(k, (0, 0, 0.0))[2])
            slot.cache.pop(oldest, None)
            slot.cache_stamp.pop(oldest, None)
        return payload

    def rgb_preview(self, size: int = DEFAULT_PREVIEW_SIZE,
                    panel: str | None = None, *, quality: int = 85
                    ) -> tuple[bytes, dict] | None:
        """(JPEG bytes, meta) for one panel's composite, or None when nothing is
        stacked there (default panel: the foreground).

        Cached under the key ``None`` in that panel's own cache -- see
        :meth:`_cached`. ``meta["panel"]`` is the key of the panel rendered.
        """
        now = time.time()
        with self._lock:
            slot = self._resolve(panel)
            if slot is None:
                return None
            hit = self._cached(slot, None, size, now)
            if hit is not None:
                return hit
            # The seq the render is ABOUT to be built from, read before the lock
            # is dropped.
            seq_at_start = slot.seq

        rgb = self._compose_slot(slot)
        if rgb is None:
            with self._lock:
                slot.cache.pop(None, None)
                slot.cache_stamp.pop(None, None)
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
            if self._slots.get(slot.key) is not slot:
                # A reset, a new run or the budget took the panel while this
                # was rendering. The picture is of a stack that no longer
                # exists, so it must not be cached as the current one, and it
                # must not be returned either.
                return None
            meta = self._status_locked(slot)
            meta.update({"width": pil.width, "height": pil.height,
                         "panel": slot.key})
            return self._store(slot, None, size, seq_at_start, now,
                               (buf.getvalue(), meta))

    def channel_preview(self, channel: str, size: int = DEFAULT_PREVIEW_SIZE,
                        panel: str | None = None, *, quality: int = 85
                        ) -> tuple[bytes, dict] | None:
        """(JPEG bytes, meta) for ONE channel's running mean in one panel, or
        None (default panel: the foreground).

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

        None means REFUSE, and the route turns it into a 404. Three ways to get
        there and all matter:

          * an empty name is not "the L channel", it is "no channel asked for".
            ``channel_for("")`` is ``L``, so folding it would serve one filter
            to a caller who asked for the picture;
          * a channel with no accumulator (``Sii`` on a night that shot none)
            has no pixels. Rendering it anyway would produce a black frame,
            which on a monitor page at 3am is indistinguishable from a dead
            sensor or a closed shutter;
          * a panel that is not stacked has no channels at all.
        """
        name = (channel or "").strip()
        if not name:
            return None
        key = channel_for(name)
        now = time.time()
        with self._lock:
            slot = self._resolve(panel)
            if slot is None or key not in slot.stacks:
                return None
            hit = self._cached(slot, key, size, now)
            if hit is not None:
                return hit
            mean = slot.stacks[key].mean()
            if mean is None:
                return None
            seq_at_start = slot.seq

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
            stack = slot.stacks.get(key)
            if stack is None or self._slots.get(slot.key) is not slot:
                # A reset (or the budget) landed while this was rendering. The
                # picture is of a stack that no longer exists, so it must not
                # be cached as the current one -- and it must not be returned
                # either.
                return None
            meta = self._status_locked(slot)
            meta.update({
                "width": pil.width, "height": pil.height,
                "channel": key, "panel": slot.key,
                # THIS channel's counts, replacing the whole-panel totals
                # `status()` carries. The caption under a single-channel view
                # has to say how much went into that channel; reporting the
                # night's total beside one filter's pixels is the caption
                # lying about the picture it sits under.
                "frames": stack.frames,
                "integrated_s": round(stack.integrated_s, 1),
                "rejected": stack.rejected,
            })
            return self._store(slot, key, size, seq_at_start, now,
                               (buf.getvalue(), meta))
