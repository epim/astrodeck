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
"""
from __future__ import annotations

import io
import math
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


def channel_for(filter_name: str | None) -> str:
    """The composite channel a filter name feeds. Never raises, never empty."""
    key = (filter_name or "").strip().upper()
    return _ALIASES.get(key, "L")


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
    the pixels away. ``add`` is the only write path and is called from the
    sequence engine's frame loop, once per ACCEPTED light.
    """

    def __init__(self, *, max_side: int = MAX_STACK_SIDE,
                 clip_sigma: float = 4.0,
                 min_render_interval_s: float = MIN_RENDER_INTERVAL_S) -> None:
        self.max_side = int(max_side)
        self.clip_sigma = float(clip_sigma)
        self.min_render_interval_s = float(min_render_interval_s)
        self.enabled = False
        self._stacks: dict[str, LiveStacker] = {}
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
        self._cache: tuple[bytes, dict] | None = None
        self._cache_seq = -1
        self._cache_size = 0
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
        self._stacks.clear()
        self._target = target
        self._session = session
        self._factor = 0
        self._seq += 1
        self._cache = None
        self._cache_seq = -1
        self._rendered_at = 0.0
        return self.status()

    # ------------------------------------------------------------- accumulate
    def add(self, data: np.ndarray, filter_name: str | None,
            exposure_s: float, *, target: str | None = None,
            session: str | None = None) -> str | None:
        """Fold one accepted light into its filter's stack.

        Returns the channel it landed on, or None when nothing was stacked
        (disabled, no pixels, no stars to register on). A target or run
        different from the one in hand resets first: a stack that spans two
        objects is not a picture of either.
        """
        if not self.enabled:
            return None
        arr = np.asarray(data)
        if arr.ndim != 2 or arr.size == 0:
            return None
        new_target = self._target if target is None else (target or "")
        new_session = self._session if session is None else (session or "")
        if (new_target, new_session) != (self._target, self._session):
            self.reset(new_target, new_session)
        if not self._factor:
            self._factor = downsample_factor(arr.shape, max_side=self.max_side)
        small = block_mean(arr, self._factor)

        ch = channel_for(filter_name)
        stack = self._stacks.get(ch)
        if stack is None:
            stack = LiveStacker(clip_sigma=self.clip_sigma)
            self._stacks[ch] = stack
        outcome = stack.add(small, float(exposure_s or 0.0))
        if not outcome.accepted:
            return None
        self._seq += 1
        return ch

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
        out = [ChannelStatus(ch, s.frames, s.integrated_s, s.rejected)
               for ch, s in self._stacks.items()]
        out.sort(key=lambda c: CHANNEL_ORDER.index(c.channel)
                 if c.channel in CHANNEL_ORDER else 99)
        return out

    def status(self) -> dict:
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
        got = self._aligned_planes()
        if got is None:
            return None
        planes, h, w = got
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

    def rgb_preview(self, size: int = DEFAULT_PREVIEW_SIZE, *,
                    quality: int = 85) -> tuple[bytes, dict] | None:
        """(JPEG bytes, meta) for the composite, or None when nothing is stacked.

        Cached: a render is reused until a frame is added AND
        ``min_render_interval_s`` has passed, so a phone polling every second
        costs one dict lookup rather than a percentile over seven channels.
        """
        now = time.time()
        if (self._cache is not None and self._cache_size == int(size)
                and (self._cache_seq == self._seq
                     or now - self._rendered_at < self.min_render_interval_s)):
            return self._cache

        rgb = self.compose()
        if rgb is None:
            self._cache = None
            return None
        arr8 = (rgb * 255.0 + 0.5).astype(np.uint8)
        pil = Image.fromarray(arr8, mode="RGB")
        if size and pil.width > int(size):
            scale = int(size) / pil.width
            pil = pil.resize((int(size), max(1, int(pil.height * scale))),
                             Image.BILINEAR)
        buf = io.BytesIO()
        pil.save(buf, format="JPEG", quality=quality, optimize=False)

        self._rendered_at = now
        self._cache_seq = self._seq
        self._cache_size = int(size)
        meta = dict(self.status())
        meta.update({"width": pil.width, "height": pil.height})
        self._cache = (buf.getvalue(), meta)
        return self._cache
