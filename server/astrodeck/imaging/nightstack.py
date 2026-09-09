"""One image per night, and an animation of a transient brightening.

The rig shoots the same field night after night. Every other stacking path in
this package answers "what does the frame that just landed look like" —
``livestack`` for a running preview, ``sessionstack`` for a colour composite of
the run in progress. Neither survives the night, and neither puts two nights
side by side. This module answers the other question: **what changed between
last night and tonight**, which for a supernova is the only question there is.

The reduction is deliberately small. It is not a substitute for a real
processing suite; it is the thing that turns 500 subs across a fortnight into
one PNG per night, an animation, and a number per night you can plot.

Four decisions carry the module, and each exists because of something the sky
or the mount actually does:

**The 180-degree flip is real and it is not in the headers.** A German
equatorial that crosses the meridian re-hangs the camera upside down: every
frame after the flip is the same field rotated by exactly 180 degrees. Nothing
in the FITS this rig writes records it — ``fitsio.save_fits`` has no PIERSIDE
card, and ``ROTATANG`` is the rotator's own reported angle, which does not
change when the MOUNT flips. So :func:`register_translation` tries the frame
both ways round and keeps whichever correlates better. That costs one extra
FFT per frame and removes an entire class of "half the night is garbage".

**The stretch is computed once and reused.** Every frame-by-frame auto-stretch
in this package (``processing.auto_stretch``) derives its own black and white
point, which is right for a preview and catastrophic for an animation: the sky
gets brighter and dimmer frame to frame and the supernova's rise disappears
into the flicker. :func:`stretch_params` is computed on ONE night — the
reference night — and :func:`stretch` applies those same numbers to every
other. A night that was genuinely brighter therefore LOOKS brighter, which is
the entire point.

**Photometry is a ratio, not a magnitude.** Nights differ in transparency, in
sky brightness, in how many subs survived, and this pipeline does not carry a
photometric zero point or a colour term. Dividing the supernova's flux by the
summed flux of a few comparison stars in the same crop cancels all of that:
cloud that halves the supernova halves the comparisons too. What comes out is
a relative light curve — the SHAPE of the rise is meaningful, the absolute
level is not. See :func:`relative_flux`.

**Memory is bounded by the crop, not by the sensor.** A 6252x4176 frame is
104 MB as float32 and a night is a couple of dozen of them. :func:`combine`
takes an optional ``bounds`` window and materialises only that, and the frame
sequence it reads from is indexable rather than a list, so the caller can hand
it something that loads from disk on demand (:class:`FitsFrames`). A night of
17 subs over a 1856x1356 window costs about 170 MB instead of 1.8 GB.

Everything here is pure: no devices, no server, no global state. The CLI that
drives it lives in ``server/tools/sn_animation.py``.
"""
from __future__ import annotations

import math
import re
import shutil
import subprocess
import warnings
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import NamedTuple

import numpy as np

from ..calibration.stacker import sigma_clip_mean
from .stars import detect_stars

__all__ = [
    "FrameRef", "night_of", "parse_frame_name", "scan_frames", "group_by_night",
    "FitsFrames", "load_frame", "build_master", "load_masters", "calibrate",
    "Alignment", "register_translation", "compose", "place", "place_crop",
    "bin_mean", "box_blur", "combine", "fill_nan",
    "StretchParams", "stretch_params", "stretch",
    "crop_bounds", "crop_around", "Mark", "annotate",
    "Photometry", "aperture_sum", "annulus_background", "net_flux",
    "relative_flux", "find_comparison_stars",
    "assemble_gif", "assemble_mp4", "plot_lightcurve",
    "MIN_REGISTRATION_SCORE", "DEFAULT_BIN", "DEFAULT_SATURATION_ADU",
]


# --------------------------------------------------------------------------
# Filenames and nights
# --------------------------------------------------------------------------

#: ``naming.DEFAULT_TEMPLATE`` renders
#: ``$$FRAMETYPE$$_$$TARGET$$_$$FILTER$$_$$DATE$$_$$TIME$$_$$FRAMENR$$``, and the
#: target is LOOSE-sanitised, so it keeps spaces (``NGC 7331``) and may keep
#: underscores. The date/time/index tail is rigid, so the pattern is anchored on
#: the tail and the greedy target soaks up whatever is left in the middle — the
#: only parse that survives a target with an underscore in it.
FRAME_RE = re.compile(
    r"^(?P<frame_type>[^_]+)"
    r"_(?P<target>.+)"
    r"_(?P<filter>[^_]+)"
    r"_(?P<date>\d{4}-\d{2}-\d{2})"
    r"_(?P<time>\d{6})"
    r"_(?P<index>\d+)$"
)

#: Extensions the capture path writes (``naming.CAPTURE_EXT`` plus the short
#: spelling other tools produce).
FITS_SUFFIXES = (".fits", ".fit", ".fts")


@dataclass(frozen=True)
class FrameRef:
    """One light frame located by its filename alone — no FITS read.

    Filename-only on purpose: bucketing a fortnight of frames into nights must
    not cost a header open per file, and the sequence writes the local civil
    time into the name (``$$DATE$$``/``$$TIME$$``), which is exactly the clock
    the noon boundary is defined against.
    """
    path: Path
    frame_type: str
    target: str
    filter_name: str
    when: datetime           # naive LOCAL civil time, from the filename
    index: int

    @property
    def night(self) -> date:
        return night_of(self.when)


def night_of(timestamp_local) -> date:
    """The observing night a local timestamp belongs to, as a date.

    A night runs local noon to local noon, so a frame stamped 01:30 on the 8th
    belongs to the night that began on the 7th. Same rollover as
    ``events.night_key`` and the ``$$NIGHT$$`` filename token, so a night's
    frames, its log file and this tool all agree on what "2026-09-07" means.

    Accepts a naive local ``datetime``/``date`` or a POSIX timestamp.
    """
    if isinstance(timestamp_local, (int, float)):
        timestamp_local = datetime.fromtimestamp(float(timestamp_local))
    if isinstance(timestamp_local, datetime):
        hours = timestamp_local.hour + timestamp_local.minute / 60.0
        d = timestamp_local.date()
        return d if hours >= 12.0 else date.fromordinal(d.toordinal() - 1)
    if isinstance(timestamp_local, date):
        # A bare date carries no clock; treat it as already-a-night key.
        return timestamp_local
    raise TypeError(f"night_of: unsupported timestamp {timestamp_local!r}")


def parse_frame_name(path) -> FrameRef | None:
    """Parse one capture filename, or None when it does not match the template.

    Returning None rather than raising is what lets a caller walk a directory
    that also holds masters, ``.ini`` leftovers and hand-renamed files without
    a try/except around every entry.
    """
    p = Path(path)
    if p.suffix.lower() not in FITS_SUFFIXES:
        return None
    m = FRAME_RE.match(p.stem)
    if not m:
        return None
    try:
        when = datetime.strptime(m.group("date") + m.group("time"),
                                 "%Y-%m-%d%H%M%S")
    except ValueError:
        return None
    return FrameRef(path=p, frame_type=m.group("frame_type"),
                    target=m.group("target"), filter_name=m.group("filter"),
                    when=when, index=int(m.group("index")))


def scan_frames(directory, *, filter_name: str | None = None,
                frame_type: str | None = "Light"
                ) -> tuple[list[FrameRef], list[str]]:
    """Every parseable frame in ``directory``, plus a note per skipped file.

    The notes are returned rather than logged so the caller decides how loud to
    be; a silent skip is how a night quietly loses half its frames.
    """
    root = Path(directory)
    frames: list[FrameRef] = []
    notes: list[str] = []
    if not root.is_dir():
        return frames, [f"not a directory: {root}"]
    for p in sorted(root.iterdir()):
        if not p.is_file():
            continue
        if p.suffix.lower() not in FITS_SUFFIXES:
            continue
        ref = parse_frame_name(p)
        if ref is None:
            notes.append(f"skipped (name does not match the capture template): {p.name}")
            continue
        if frame_type is not None and ref.frame_type.lower() != frame_type.lower():
            notes.append(f"skipped ({ref.frame_type}, not {frame_type}): {p.name}")
            continue
        if filter_name is not None and ref.filter_name.lower() != filter_name.lower():
            continue
        frames.append(ref)
    frames.sort(key=lambda f: (f.when, f.index))
    return frames, notes


def group_by_night(frames: Iterable[FrameRef]) -> dict[date, list[FrameRef]]:
    """Frames bucketed by observing night, each bucket in time order."""
    out: dict[date, list[FrameRef]] = {}
    for f in frames:
        out.setdefault(f.night, []).append(f)
    for v in out.values():
        v.sort(key=lambda f: (f.when, f.index))
    return dict(sorted(out.items()))


# --------------------------------------------------------------------------
# Reading and calibrating
# --------------------------------------------------------------------------

#: Above this ADU a pixel is assumed to be into the non-linear top of the well,
#: so a star containing one is no use as a photometric comparison. The sensor
#: saturates at 65535; the margin is the usual "stay off the shoulder"
#: allowance, and it is wide enough to still hold after :func:`calibrate` has
#: taken the sky pedestal (a few hundred ADU) off.
DEFAULT_SATURATION_ADU = 60000.0


def load_frame(path) -> tuple[np.ndarray, object]:
    """Read one FITS as float32 plus its header.

    ``astropy`` applies BZERO/BSCALE on the way in, so the ``int16 + 32768``
    encoding every ``save_fits`` frame uses comes back as the uint16 values the
    camera actually produced. Nothing here needs to know about that.
    """
    from astropy.io import fits
    with fits.open(Path(path), memmap=False) as hdul:
        hdu = hdul[0]
        data = np.asarray(hdu.data, dtype=np.float32)
        header = hdu.header.copy()
    return data, header


def _frame_scalar(path, *, sample: int = 512) -> float:
    """Median of a central patch — a per-frame level without a full read."""
    from astropy.io import fits
    with fits.open(Path(path), memmap=False) as hdul:
        h, w = hdul[0].shape
        y0 = max(0, h // 2 - sample // 2)
        x0 = max(0, w // 2 - sample // 2)
        patch = np.asarray(hdul[0].section[y0:y0 + sample], dtype=np.float32)
    patch = patch[:, x0:x0 + sample]
    med = float(np.median(patch))
    return med if med > 0 else 1.0


def build_master(paths: Sequence[Path], *, method: str = "sigma_clip",
                 sigma: float = 3.0, max_frames: int = 64,
                 strip_rows: int = 256, normalise: bool = False,
                 bias: np.ndarray | None = None) -> np.ndarray | None:
    """Stack calibration frames into one master, in bounded memory.

    Reads row strips through ``hdul[0].section`` instead of whole frames — the
    same trick ``calibration.library.build_master_streamed`` uses, and for the
    same reason: 50 bias frames at 26 megapixels is 5 GB if you load them, and
    170 MB if you do not. Peak cost here is ``strip_rows x width x n x 4 B``.

    ``normalise=True`` (for flats) divides each frame by its own central median
    first, so a flat shot at a different sky brightness still contributes its
    SHAPE and not its level; the master is then scaled to a median of 1.0 so
    dividing a light by it is a pure correction.

    Returns None when there is nothing to stack — calibration is best effort
    and an absent master is a documented outcome, not an error.
    """
    from astropy.io import fits
    use = [Path(p) for p in paths]
    if not use:
        return None
    if len(use) > max_frames:
        idx = np.linspace(0, len(use) - 1, max_frames).astype(int)
        use = [use[i] for i in idx]
    scalars = [_frame_scalar(p) for p in use] if normalise else [1.0] * len(use)

    hduls = [fits.open(p, memmap=False) for p in use]
    try:
        shapes = {tuple(hd[0].shape) for hd in hduls}
        if len(shapes) != 1:
            raise ValueError(f"ragged calibration set: {sorted(shapes)}")
        h, w = hduls[0][0].shape
        if bias is not None and bias.shape != (h, w):
            bias = None
        out = np.empty((h, w), dtype=np.float32)
        step = max(1, int(strip_rows))
        for y0 in range(0, h, step):
            y1 = min(y0 + step, h)
            strips = []
            for hd, s in zip(hduls, scalars):
                strip = np.asarray(hd[0].section[y0:y1], dtype=np.float32)
                if bias is not None:
                    strip = strip - bias[y0:y1]
                if s != 1.0:
                    strip = strip / s
                strips.append(strip)
            if method == "median" or len(strips) < 3:
                out[y0:y1] = np.median(np.stack(strips), axis=0)
            else:
                out[y0:y1] = sigma_clip_mean(strips, sigma)
    finally:
        for hd in hduls:
            hd.close()
    if normalise:
        med = float(np.median(out))
        if med > 0:
            out /= med
    return out


def _fits_in(directory) -> list[Path]:
    root = Path(directory)
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir()
                  if p.is_file() and p.suffix.lower() in FITS_SUFFIXES)


def load_masters(bias_dir=None, flats_dir=None, filters: Sequence[str] = (),
                 *, notes: list[str] | None = None
                 ) -> tuple[np.ndarray | None, dict[str, np.ndarray]]:
    """Build the master bias and the per-filter master flats we actually need.

    Only the filters named are built: a master flat is another 104 MB on this
    sensor, and a run that reduces the L channel has no use for the other six.
    Every failure is recorded in ``notes`` and swallowed — a missing or
    unreadable calibration directory must degrade the picture, not stop it.
    """
    from astropy.io import fits
    log = notes if notes is not None else []
    bias = None
    if bias_dir:
        paths = _fits_in(bias_dir)
        if not paths:
            log.append(f"no bias frames under {bias_dir}")
        else:
            try:
                bias = build_master(paths, method="median")
                log.append(f"master bias from {len(paths)} frames")
            except Exception as exc:                      # best effort
                log.append(f"master bias failed ({exc!r}); continuing without")

    flats: dict[str, np.ndarray] = {}
    wanted = {f.upper() for f in filters}
    if flats_dir and wanted:
        by_filter: dict[str, list[Path]] = {}
        for p in _fits_in(flats_dir):
            try:
                name = str(fits.getheader(p).get("FILTER", "")).strip().upper()
            except Exception:
                continue
            if name in wanted:
                by_filter.setdefault(name, []).append(p)
        for name in sorted(wanted):
            paths = by_filter.get(name, [])
            if not paths:
                log.append(f"no {name} flats under {flats_dir}")
                continue
            try:
                flats[name] = build_master(paths, normalise=True, bias=bias)
                log.append(f"master flat {name} from {len(paths)} frames")
            except Exception as exc:
                log.append(f"master flat {name} failed ({exc!r}); continuing without")
    return bias, flats


def calibrate(raw: np.ndarray, *, bias: np.ndarray | None = None,
              flat: np.ndarray | None = None, pedestal: bool = True
              ) -> np.ndarray:
    """Best-effort calibration of one light frame, as float32.

    Bias then flat then pedestal, each optional and each skipped silently when
    the master's shape does not match the light (a mismatched master is a
    configuration mistake, and applying it would be worse than not).

    The PEDESTAL always runs by default, bias or no bias: subtracting each
    frame's own median puts every sub on a zero sky. That is what makes a
    moonlit sub and a dark-sky sub stack together without the brighter one
    dragging the sigma clip around, and what makes one shared stretch legible
    across a fortnight of different skies. Aperture photometry re-derives its
    own local background from an annulus, so nothing downstream depends on the
    absolute level this throws away.

    No dark subtraction, on purpose. The rig has no darks matching the 60 s
    gain-125 lights, and hot pixels are handled where they are cheap to handle:
    the dither moves them around the sky between subs, so the sigma clip in
    :func:`combine` rejects them.
    """
    x = np.array(raw, dtype=np.float32, copy=True)
    if bias is not None and bias.shape == x.shape:
        x -= bias
    if flat is not None and flat.shape == x.shape:
        safe = np.asarray(flat, dtype=np.float32)
        x /= np.where(safe > 1e-3, safe, 1.0)
    if pedestal:
        x -= np.float32(np.median(x))
    return x


class FitsFrames(Sequence):
    """An indexable, lazily-loading, calibrated view of a night's frames.

    :func:`combine` reads its input twice (once for the robust centre, once for
    the clipped mean) and indexes rather than iterates, so handing it one of
    these keeps exactly one full frame in memory at a time instead of the whole
    night. The cost is reading each file twice off disk, which in daylight on a
    local SSD is nothing next to 1.8 GB of resident float32.
    """

    def __init__(self, paths: Sequence[Path], *, bias=None, flat=None,
                 pedestal: bool = True,
                 loader: Callable[[Path], np.ndarray] | None = None) -> None:
        self._paths = [Path(p) for p in paths]
        self._bias = bias
        self._flat = flat
        self._pedestal = pedestal
        self._loader = loader

    def __len__(self) -> int:
        return len(self._paths)

    def __getitem__(self, i):
        if isinstance(i, slice):
            return [self[k] for k in range(*i.indices(len(self)))]
        path = self._paths[i]
        raw = self._loader(path) if self._loader else load_frame(path)[0]
        return calibrate(raw, bias=self._bias, flat=self._flat,
                         pedestal=self._pedestal)


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------

#: Binning factor for the coarse correlation. At 0.968 arcsec/px a bin of 4
#: still resolves the star field, and it turns a 6252x4176 FFT into a
#: 1563x1044 one — the difference between a night registering in seconds and
#: in minutes.
DEFAULT_BIN = 4

#: Side of the full-resolution window used to refine the coarse shift. Big
#: enough to hold plenty of stars, small enough that its FFT is free.
DEFAULT_REFINE_SIZE = 1024

#: Correlation score below which a registration is not believed.
#:
#: The score is the AMBIGUITY RATIO: the correlation peak divided by the next
#: highest point on the surface more than a few pixels away (:func:`_peak_score`).
#: One translation that explains the frame gives a peak with nothing near it;
#: a frame that matches nothing gives a surface of near-equal candidates and a
#: ratio just over 1.
#:
#: It is deliberately NOT the more familiar peak-to-sidelobe ratio (peak height
#: in standard deviations of the surface), because that number scales with the
#: number of pixels correlated and so cannot carry one threshold across a
#: 400x300 test frame and a 26-megapixel sub. Measured on synthetic fields at
#: both sizes:
#:
#:     correct match, clear ......... 3.8 - 21
#:     correct match, thin cloud .... 5.8 - 20
#:     unrelated star field ......... 1.26 - 1.31
#:     blank frame .................. 1.04 - 1.06
#:
#: (the same cases scored 32-892 and 6.9-35.5 on peak-to-sidelobe — two
#: populations that overlap, which is why that metric was dropped.) 2.0 sits in
#: the empty gap: nothing accidental is stacked, and a cloud-dimmed frame that
#: still has a findable offset still contributes.
MIN_REGISTRATION_SCORE = 2.0


class Alignment(NamedTuple):
    """How to put one frame onto another's pixel grid.

    ``place(image, alignment)`` rotates by 180 degrees when ``flipped``, then
    translates by ``(dy, dx)``. The pair is therefore the shift to APPLY to the
    image, not the image's offset from the reference — the opposite sign to the
    convention ``imaging.registration`` uses, and spelled out here because
    getting it backwards produces a stack that looks plausible and is wrong.
    """
    dy: int
    dx: int
    score: float
    flipped: bool


def bin_mean(a: np.ndarray, factor: int) -> np.ndarray:
    """Mean of every ``factor`` x ``factor`` block, as float32.

    ``sessionstack.block_mean`` does the same reduction but clips to uint16,
    which destroys a background-subtracted frame's negatives. This one stays in
    floating point.
    """
    f = max(1, int(factor))
    a = np.asarray(a, dtype=np.float32)
    if f == 1:
        return a
    h = (a.shape[0] // f) * f
    w = (a.shape[1] // f) * f
    if h == 0 or w == 0:
        return a
    return a[:h, :w].reshape(h // f, f, w // f, f).mean(axis=(1, 3)).astype(np.float32)


def box_blur(a: np.ndarray, radius: int = 1) -> np.ndarray:
    """Separable box blur, cumulative-sum based. No scipy in this package."""
    r = int(radius)
    if r < 1:
        return np.asarray(a, dtype=np.float32)
    out = np.asarray(a, dtype=np.float64)
    for axis in (0, 1):
        n = out.shape[axis]
        pad = [(0, 0), (0, 0)]
        pad[axis] = (r, r)
        p = np.pad(out, pad, mode="edge")
        head = list(p.shape)
        head[axis] = 1
        cs = np.concatenate([np.zeros(head), np.cumsum(p, axis=axis)], axis=axis)
        k = 2 * r + 1
        hi = np.take(cs, np.arange(k, k + n), axis=axis)
        lo = np.take(cs, np.arange(0, n), axis=axis)
        out = (hi - lo) / k
    return out.astype(np.float32)


def _smooth_size(n: int) -> int:
    """Largest 5-smooth integer <= n (and >= 8).

    Binning a 6252x4176 sensor by 4 gives 1563x1044, and 1563 = 3 x 521 with
    521 prime — numpy falls off its fast path and the FFT crawls. Trimming a
    few rows off the edge to reach 1536x1024 costs a couple of arcminutes of
    field and buys back an order of magnitude.
    """
    n = int(n)
    if n < 8:
        return max(1, n)
    best = 8
    p2 = 1
    while p2 <= n:
        p23 = p2
        while p23 <= n:
            p235 = p23
            while p235 <= n:
                if p235 > best:
                    best = p235
                p235 *= 5
            p23 *= 3
        p2 *= 2
    return best


def _hann2d(shape: tuple[int, int]) -> np.ndarray:
    """Separable Hann window. The FFT treats the frame as periodic, so without
    a taper the discontinuity at the frame edge is itself a strong feature and
    it correlates with nothing but itself."""
    hy = np.hanning(shape[0]).astype(np.float32)
    hx = np.hanning(shape[1]).astype(np.float32)
    return np.outer(hy, hx)


def _prepare(a: np.ndarray) -> np.ndarray:
    """Background-subtract, lightly smooth, clip to positive, window.

    Clipping the negatives is what makes this a STAR correlation rather than a
    noise correlation: below the sky there is nothing to match on, and phase
    correlation normalises every frequency to unit magnitude, so any noise left
    in gets the same weight as a star.
    """
    x = np.asarray(a, dtype=np.float32)
    finite = np.isfinite(x)
    if not finite.all():
        fill = float(np.median(x[finite])) if finite.any() else 0.0
        x = np.where(finite, x, fill).astype(np.float32)
    x = x - np.float32(np.median(x))
    x = box_blur(x, 1)
    np.maximum(x, 0.0, out=x)
    x *= _hann2d(x.shape)
    return x


def _peak_score(c: np.ndarray, py: int, px: int, exclude: int = 5) -> float:
    """Ambiguity ratio: the peak divided by the best rival elsewhere.

    Excluding a small box around the peak keeps its own shoulder from being
    counted as the rival. See :data:`MIN_REGISTRATION_SCORE` for why this
    metric and not peak-to-sidelobe.
    """
    h, w = c.shape
    mask = np.ones((h, w), dtype=bool)
    ys = (np.arange(py - exclude, py + exclude + 1)) % h
    xs = (np.arange(px - exclude, px + exclude + 1)) % w
    mask[np.ix_(ys, xs)] = False
    rival = float(c[mask].max())
    peak = float(c[py, px])
    if rival <= 0:
        return float("inf") if peak > 0 else 0.0
    return peak / rival


def _phase_correlate(reference: np.ndarray, image: np.ndarray
                     ) -> tuple[int, int, float]:
    """Integer shift to apply to ``image`` to land it on ``reference``.

    Standard phase correlation: the cross-power spectrum's phase alone carries
    the translation, so normalising away the magnitudes turns a broad
    galaxy-dominated correlation blob into a near-delta peak. If
    ``image(y, x) = reference(y - d, x - d)`` the peak sits at ``+d``, and the
    shift that undoes it is ``-d`` — which is what is returned.
    """
    h = min(_smooth_size(reference.shape[0]), _smooth_size(image.shape[0]))
    w = min(_smooth_size(reference.shape[1]), _smooth_size(image.shape[1]))
    ref = _prepare(reference[:h, :w])
    img = _prepare(image[:h, :w])
    fr = np.fft.rfft2(ref)
    fi = np.fft.rfft2(img)
    r = fi * np.conj(fr)
    r /= np.abs(r) + 1e-12
    c = np.fft.irfft2(r, s=(h, w))
    idx = int(np.argmax(c))
    py, px = divmod(idx, w)
    score = _peak_score(c, py, px)
    dy = py if py <= h // 2 else py - h
    dx = px if px <= w // 2 else px - w
    return -int(dy), -int(dx), score


def _window_origin(n: int, s: int, d: int) -> int | None:
    """Where to put an ``s``-wide refinement window along an ``n``-long axis so
    it lies inside the reference AND, offset by ``-d``, inside the image.

    As close to centred as the shift allows. Centring blindly and giving up
    when the offset window fell off the edge silently disabled refinement for
    exactly the frames that need it most — a large cross-night offset, or a
    meridian flip, on a small frame.
    """
    lo = max(0, d)
    hi = min(n - s, n - s + d)
    if lo > hi:
        return None
    return int(min(max((n - s) // 2, lo), hi))


def _refine(reference: np.ndarray, image: np.ndarray, dy: int, dx: int,
            size: int, limit: int) -> tuple[int, int]:
    """Second correlation pass at full resolution over a central window.

    The coarse pass runs on a binned copy, so its answer is only good to the
    binning factor. Re-correlating a window that is already coarse-aligned
    costs one small FFT and recovers the last few pixels; a residual larger
    than ``limit`` means the window found something the whole frame did not, so
    it is discarded rather than trusted.
    """
    h, w = reference.shape
    s = int(min(size, h, w))
    if s < 64:
        return dy, dx
    y0 = _window_origin(h, s, dy)
    x0 = _window_origin(w, s, dx)
    if y0 is None or x0 is None:
        # The shift is bigger than the slack around the window: there is no
        # placement that lies inside BOTH frames. Keep the coarse answer.
        return dy, dx
    ddy, ddx, _ = _phase_correlate(reference[y0:y0 + s, x0:x0 + s],
                                   image[y0 - dy:y0 - dy + s, x0 - dx:x0 - dx + s])
    if abs(ddy) > limit or abs(ddx) > limit:
        return dy, dx
    return dy + ddy, dx + ddx


def register_translation(reference: np.ndarray, image: np.ndarray, *,
                         bin_factor: int = DEFAULT_BIN,
                         refine: bool = True,
                         refine_size: int = DEFAULT_REFINE_SIZE,
                         try_flip: bool = True) -> Alignment:
    """Find the shift (and the meridian flip) taking ``image`` onto ``reference``.

    Both orientations are tried and the better-correlating one wins. That is
    not belt-and-braces: a mount that crosses the meridian mid-night re-hangs
    the camera upside down, the rotator does not compensate, and NOTHING in the
    FITS this rig writes records it — ``save_fits`` emits no PIERSIDE, and
    ``ROTATANG`` is the rotator's own angle, which is unchanged by a mount
    flip. Half a night would otherwise stack as noise.

    Runs coarse-then-fine: a binned correlation over the whole frame to find
    the shift to within ``bin_factor`` pixels, then a full-resolution pass over
    a central window to recover the rest.

    Returns an :class:`Alignment`; ``score`` is the coarse peak-to-sidelobe
    ratio, which the caller should test against :data:`MIN_REGISTRATION_SCORE`.
    """
    ref = np.asarray(reference, dtype=np.float32)
    img = np.asarray(image, dtype=np.float32)
    if ref.shape != img.shape:
        raise ValueError(f"shape mismatch: {ref.shape} vs {img.shape}")

    f = max(1, int(bin_factor))
    ref_b = bin_mean(ref, f)
    candidates: list[tuple[float, int, int, bool]] = []
    for flipped in ((False, True) if try_flip else (False,)):
        cand = img[::-1, ::-1] if flipped else img
        dy, dx, score = _phase_correlate(ref_b, bin_mean(cand, f))
        candidates.append((score, dy * f, dx * f, flipped))
    score, dy, dx, flipped = max(candidates, key=lambda c: c[0])

    if refine:
        cand = img[::-1, ::-1] if flipped else img
        dy, dx = _refine(ref, cand, dy, dx, refine_size, limit=2 * f)
    return Alignment(int(dy), int(dx), float(score), bool(flipped))


def compose(outer: Alignment, inner: Alignment) -> Alignment:
    """``inner`` maps a frame onto grid B; ``outer`` maps grid B onto grid C.
    The result maps the frame straight onto grid C.

    The 180-degree rotation is its own inverse and its linear part is ``-I``, so
    an outer flip negates the inner translation and the flips compose by XOR.
    Written out because composing these by hand is where sign errors live.
    """
    dy = (-inner.dy if outer.flipped else inner.dy) + outer.dy
    dx = (-inner.dx if outer.flipped else inner.dx) + outer.dx
    return Alignment(int(dy), int(dx), min(outer.score, inner.score),
                     bool(outer.flipped) ^ bool(inner.flipped))


def place_crop(image: np.ndarray, alignment: Alignment,
               bounds: tuple[int, int, int, int], *,
               fill: float = float("nan")) -> np.ndarray:
    """Put ``image`` on the reference grid and return the ``bounds`` window.

    ``bounds`` is ``(y0, x0, y1, x1)`` in REFERENCE coordinates. Only that
    window is materialised, which is the whole reason a night of 26-megapixel
    frames fits in memory. Pixels the image does not reach are ``fill``.

    The 180-degree rotation is done with a reversed view (``image[::-1, ::-1]``),
    not a copy — numpy negative strides cost nothing until the slice below
    copies the window out.
    """
    y0, x0, y1, x1 = (int(v) for v in bounds)
    src = image[::-1, ::-1] if alignment.flipped else image
    h, w = src.shape
    out = np.full((max(0, y1 - y0), max(0, x1 - x0)), fill, dtype=np.float32)
    sy0, sy1 = y0 - alignment.dy, y1 - alignment.dy
    sx0, sx1 = x0 - alignment.dx, x1 - alignment.dx
    cy0, cy1 = max(0, sy0), min(h, sy1)
    cx0, cx1 = max(0, sx0), min(w, sx1)
    if cy1 > cy0 and cx1 > cx0:
        out[cy0 - sy0:cy1 - sy0, cx0 - sx0:cx1 - sx0] = src[cy0:cy1, cx0:cx1]
    return out


def place(image: np.ndarray, alignment: Alignment, *,
          fill: float = float("nan")) -> np.ndarray:
    """``place_crop`` over the whole reference-sized grid."""
    return place_crop(image, alignment, (0, 0, image.shape[0], image.shape[1]),
                      fill=fill)


# --------------------------------------------------------------------------
# Combining
# --------------------------------------------------------------------------

#: Below this many frames a sigma clip has nothing to clip against, so the
#: combine degrades to a plain median (the same threshold
#: ``calibration.stacker.sigma_clip_mean`` uses).
MEDIAN_BELOW = 3


def combine(frames, shifts: Sequence[Alignment], *,
            bounds: tuple[int, int, int, int] | None = None,
            sigma: float = 3.0) -> np.ndarray:
    """Sigma-clipped mean of aligned frames, on the reference's pixel grid.

    ``frames`` only has to support ``len()`` and ``[i]`` — a list of arrays for
    a test, a :class:`FitsFrames` for a real night. ``bounds`` restricts the
    output (and the memory) to one window in reference coordinates.

    The clip is centred on the per-pixel MEDIAN and scaled by the MAD, not by
    the mean and standard deviation. That matters for exactly the case this
    module needs it for: a hot pixel is one enormous value among a dozen
    ordinary ones, and it drags a mean/sigma clip's own threshold out past
    itself — with five frames a single huge outlier sits at 2 sigma of a
    standard deviation it created, and survives. A median/MAD clip is blind to
    it. The dither is what turns a FIXED hot pixel into a per-pixel outlier in
    the first place: it lands on a different piece of sky in every sub.

    **The un-overlapped border is NaN**, not zero and not the frame median: a
    pixel no frame reached is missing data, and NaN is the only value that
    cannot be mistaken for signal by the stretch, the photometry or a later
    stack. :func:`fill_nan` converts it when a caller needs a number.
    """
    n = len(frames)
    if n == 0:
        raise ValueError("combine needs at least one frame")
    if len(shifts) != n:
        raise ValueError(f"{n} frames but {len(shifts)} shifts")
    first = np.asarray(frames[0], dtype=np.float32)
    if bounds is None:
        bounds = (0, 0, first.shape[0], first.shape[1])

    placed = [place_crop(first, shifts[0], bounds)]
    for i in range(1, n):
        placed.append(place_crop(np.asarray(frames[i], dtype=np.float32),
                                 shifts[i], bounds))
    stack = np.stack(placed)
    del placed

    if n < MEDIAN_BELOW:
        with np.errstate(invalid="ignore"):
            return _nanmedian(stack).astype(np.float32)

    med = _nanmedian(stack)
    mad = _nanmedian(np.abs(stack - med)) * 1.4826
    # A zero MAD means every sample agreed exactly; keep only the agreeing ones
    # (which is what rejects a lone hot pixel in a noiseless synthetic frame).
    lo = med - sigma * mad
    hi = med + sigma * mad
    keep = np.isfinite(stack) & (stack >= lo) & (stack <= hi)
    total = np.where(keep, np.nan_to_num(stack, nan=0.0), 0.0).sum(axis=0)
    count = keep.sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = total / count
    out = np.where(count > 0, mean, med)
    # No frame reached this pixel at all -> genuinely missing, stays NaN.
    seen = np.isfinite(stack).any(axis=0)
    out = np.where(seen, out, np.nan)
    return out.astype(np.float32)


def _nanmedian(stack: np.ndarray) -> np.ndarray:
    """``np.nanmedian`` without the all-NaN RuntimeWarning (an all-NaN column
    is the expected state of the un-overlapped border, not a surprise)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmedian(stack, axis=0)


def fill_nan(image: np.ndarray, value: float | None = None) -> np.ndarray:
    """Replace NaN with ``value``, or with the image's own median when None."""
    x = np.asarray(image, dtype=np.float32)
    bad = ~np.isfinite(x)
    if not bad.any():
        return x
    if value is None:
        good = x[~bad]
        value = float(np.median(good)) if good.size else 0.0
    out = x.copy()
    out[bad] = np.float32(value)
    return out


# --------------------------------------------------------------------------
# Stretch
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class StretchParams:
    """The three numbers that turn linear counts into an 8-bit picture.

    Carried as a value so ONE night's numbers can be applied to every night —
    see :func:`stretch_params`.
    """
    black: float
    white: float
    softening: float = 30.0


def stretch_params(image: np.ndarray, *, white_percentile: float = 99.7,
                   black_sigma: float = 1.0,
                   softening: float = 30.0) -> StretchParams:
    """Derive a stretch from one image — normally the reference night's.

    Black sits just BELOW the background median (by ``black_sigma`` robust
    sigmas) rather than on it, so the sky reads as dark grey with visible grain
    instead of as a clipped black field; white is a high percentile, which on a
    galaxy field is the galaxy's core and the brighter stars.

    Compute this once and pass it to every :func:`stretch` call. An animation
    whose frames each auto-stretch flickers, and a flickering animation hides
    precisely the brightness change it exists to show.
    """
    x = np.asarray(image, dtype=np.float32)
    good = x[np.isfinite(x)]
    if good.size == 0:
        return StretchParams(0.0, 1.0, softening)
    med = float(np.median(good))
    mad = float(np.median(np.abs(good - med))) * 1.4826
    if mad <= 0:
        mad = float(good.std()) or 1.0
    black = med - black_sigma * mad
    white = float(np.percentile(good, white_percentile))
    if not (white > black):
        white = black + max(mad, 1.0)
    return StretchParams(black, white, float(softening))


def stretch(image: np.ndarray, params: StretchParams | None = None, *,
            white_percentile: float = 99.7, black_sigma: float = 1.0,
            softening: float = 30.0) -> np.ndarray:
    """asinh stretch to uint8, using ``params`` when given.

    asinh rather than a gamma or a midtone transfer: it is close to linear at
    the faint end, where the difference between a 15th- and a 16th-magnitude
    point source lives, and logarithmic at the bright end, where the galaxy
    core would otherwise be a white disc.

    Passing ``params=None`` derives them from THIS image, which is right for a
    one-off look and wrong for an animation.
    """
    p = params or stretch_params(image, white_percentile=white_percentile,
                                 black_sigma=black_sigma, softening=softening)
    x = np.asarray(image, dtype=np.float32)
    x = np.where(np.isfinite(x), x, p.black)
    span = p.white - p.black
    if span <= 0:
        span = 1.0
    t = np.clip((x - p.black) / span, 0.0, 1.0)
    a = max(1e-6, float(p.softening))
    y = np.arcsinh(a * t) / math.asinh(a)
    return np.clip(np.rint(y * 255.0), 0, 255).astype(np.uint8)


# --------------------------------------------------------------------------
# Cropping and annotation
# --------------------------------------------------------------------------

def crop_bounds(shape: tuple[int, int], cy: float, cx: float,
                height: int, width: int) -> tuple[int, int, int, int]:
    """``(y0, x0, y1, x1)`` for a ``height`` x ``width`` box centred on
    ``(cy, cx)``, slid (not shrunk) to stay inside ``shape``.

    Sliding rather than clipping keeps every night's crop the SAME SIZE, which
    a GIF requires and a light curve's pixel coordinates assume.
    """
    h, w = int(shape[0]), int(shape[1])
    ch = min(int(height), h)
    cw = min(int(width), w)
    y0 = int(round(cy - ch / 2.0))
    x0 = int(round(cx - cw / 2.0))
    y0 = max(0, min(y0, h - ch))
    x0 = max(0, min(x0, w - cw))
    return y0, x0, y0 + ch, x0 + cw


def crop_around(image: np.ndarray, cy: float, cx: float,
                height: int, width: int) -> np.ndarray:
    y0, x0, y1, x1 = crop_bounds(image.shape, cy, cx, height, width)
    return np.asarray(image)[y0:y1, x0:x1]


class Mark(NamedTuple):
    """A circle to draw on the annotated frame, in image pixels."""
    x: float
    y: float
    radius: float = 16.0
    label: str = ""
    colour: tuple[int, int, int] = (255, 196, 64)


def annotate(image8: np.ndarray, text: str = "", marks: Sequence[Mark] = (),
             *, text_colour: tuple[int, int, int] = (255, 255, 255),
             margin: int = 14, font_size: int | None = None) -> np.ndarray:
    """Burn the night's date and any marks into an 8-bit frame; returns RGB.

    The date has to be IN the pixels: a GIF has no caption, and an animation of
    unlabelled frames cannot be checked against the log. The circle is an
    outline, never a filled dot, so the thing it points at stays visible.
    """
    from PIL import Image, ImageDraw, ImageFont

    a = np.asarray(image8)
    if a.ndim == 2:
        rgb = np.dstack([a, a, a]).astype(np.uint8)
    else:
        rgb = a.astype(np.uint8)
    img = Image.fromarray(rgb, mode="RGB")
    draw = ImageDraw.Draw(img)
    size = font_size or max(16, img.height // 34)
    try:
        font = ImageFont.load_default(size=size)
    except TypeError:                          # very old Pillow: fixed size
        font = ImageFont.load_default()

    for m in marks:
        r = float(m.radius)
        box = (m.x - r, m.y - r, m.x + r, m.y + r)
        draw.ellipse(box, outline=tuple(m.colour), width=2)
        if m.label:
            draw.text((m.x + r + 4, m.y - r), m.label, fill=tuple(m.colour),
                      font=font)

    if text:
        # A dark shadow under the text so it reads on a bright galaxy core as
        # well as on empty sky.
        draw.text((margin + 2, margin + 2), text, fill=(0, 0, 0), font=font)
        draw.text((margin, margin), text, fill=tuple(text_colour), font=font)
    return np.asarray(img, dtype=np.uint8)


# --------------------------------------------------------------------------
# Photometry
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Photometry:
    """One night's relative brightness measurement."""
    ratio: float
    error: float
    target_net: float
    target_error: float
    comparison_net: float
    comparison_error: float
    background: float
    n_comparison: int


def _disc(image: np.ndarray, y: float, x: float, r_out: float):
    """Sub-image plus the radius grid over it — one bounding box, no whole-frame
    distance array (which on a 26-megapixel frame would be 200 MB)."""
    h, w = image.shape
    y0 = max(0, int(math.floor(y - r_out)))
    y1 = min(h, int(math.ceil(y + r_out)) + 1)
    x0 = max(0, int(math.floor(x - r_out)))
    x1 = min(w, int(math.ceil(x + r_out)) + 1)
    if y1 <= y0 or x1 <= x0:
        return None, None
    yy = np.arange(y0, y1, dtype=np.float32)[:, None] - y
    xx = np.arange(x0, x1, dtype=np.float32)[None, :] - x
    return image[y0:y1, x0:x1], np.hypot(yy, xx)


def aperture_sum(image: np.ndarray, y: float, x: float,
                 radius: float) -> tuple[float, int]:
    """Total counts inside a circular aperture, and how many pixels that was."""
    sub, rad = _disc(np.asarray(image, dtype=np.float32), y, x, radius)
    if sub is None:
        return 0.0, 0
    mask = (rad <= radius) & np.isfinite(sub)
    return float(sub[mask].sum()), int(mask.sum())


def annulus_background(image: np.ndarray, y: float, x: float,
                       r_in: float, r_out: float) -> tuple[float, float, int]:
    """Median, robust sigma and pixel count of a sky annulus.

    Median rather than mean because the annulus routinely contains a field star
    that has nothing to do with the target.
    """
    sub, rad = _disc(np.asarray(image, dtype=np.float32), y, x, r_out)
    if sub is None:
        return 0.0, 0.0, 0
    mask = (rad >= r_in) & (rad <= r_out) & np.isfinite(sub)
    vals = sub[mask]
    if vals.size < 8:
        return 0.0, 0.0, int(vals.size)
    med = float(np.median(vals))
    sd = float(np.median(np.abs(vals - med))) * 1.4826
    if sd <= 0:
        sd = float(vals.std())
    return med, sd, int(vals.size)


def net_flux(image: np.ndarray, y: float, x: float, aperture_px: float,
             annulus: tuple[float, float]) -> tuple[float, float, float]:
    """Background-subtracted aperture flux, its uncertainty, and the sky level.

    The uncertainty is the standard aperture-photometry background term:
    ``sigma_sky * sqrt(n_ap * (1 + n_ap / n_ann))`` — the scatter in the sky
    the aperture inherits, plus the scatter in the estimate of that sky. It
    deliberately omits the source's own shot noise, because this pipeline never
    learns the gain (the stack is a mean of an unknown number of subs), so a
    photon-counting term here would be a fabrication. It is therefore a FLOOR
    on the error, and it is labelled rough for that reason.
    """
    total, n_ap = aperture_sum(image, y, x, aperture_px)
    sky, sd, n_ann = annulus_background(image, y, x, annulus[0], annulus[1])
    net = total - sky * n_ap
    if n_ap == 0 or n_ann == 0:
        return float(net), float("inf"), float(sky)
    err = sd * math.sqrt(n_ap * (1.0 + n_ap / n_ann))
    return float(net), float(err), float(sky)


def relative_flux(image: np.ndarray, y: float, x: float,
                  comparison_positions: Sequence[tuple[float, float]],
                  aperture_px: float = 8.0,
                  annulus: tuple[float, float] = (14.0, 24.0)) -> Photometry:
    """The target's flux divided by the summed flux of comparison stars.

    A RATIO, on purpose. Nights differ in transparency, in sky brightness, in
    how many subs survived and therefore in the level of the mean stack, and
    this pipeline carries no photometric zero point, no colour term and no
    airmass correction. None of that survives the division: cloud that halves
    the supernova halves the comparisons in the same crop by the same factor.
    What is left is the shape of the light curve, which is the part that says
    anything about the star.

    ``comparison_positions`` are ``(y, x)`` in this image's pixels. Every night
    is registered onto the same grid before this is called, so one set of
    positions chosen on the reference night is valid for all of them.
    """
    t_net, t_err, sky = net_flux(image, y, x, aperture_px, annulus)
    c_net = 0.0
    c_var = 0.0
    n = 0
    for cy, cx in comparison_positions:
        net, err, _ = net_flux(image, cy, cx, aperture_px, annulus)
        if not math.isfinite(err):
            continue
        c_net += net
        c_var += err * err
        n += 1
    c_err = math.sqrt(c_var)
    if n == 0 or c_net <= 0:
        return Photometry(float("nan"), float("nan"), t_net, t_err,
                          c_net, c_err, sky, n)
    ratio = t_net / c_net
    rel_t = (t_err / t_net) if t_net > 0 else float("inf")
    rel_c = c_err / c_net
    err = abs(ratio) * math.hypot(rel_t, rel_c)
    return Photometry(float(ratio), float(err), t_net, t_err, c_net, c_err,
                      sky, n)


def find_comparison_stars(image: np.ndarray, n: int = 5, *,
                          aperture_px: float = 8.0,
                          annulus: tuple[float, float] = (14.0, 24.0),
                          saturation: float = DEFAULT_SATURATION_ADU,
                          min_separation: float | None = None,
                          exclude: Sequence[tuple[float, float]] = (),
                          exclude_radius: float = 40.0,
                          k_sigma: float = 6.0,
                          max_hfr_ratio: float = 1.8,
                          max_candidates: int = 200
                          ) -> list[tuple[float, float]]:
    """Pick ``n`` bright, unsaturated, isolated stars, as ``(y, x)``.

    Chosen ONCE, on the reference night, and reused for every other night —
    swapping comparison stars between nights would put a step in the light
    curve that has nothing to do with the supernova.

    Isolation is the expensive-to-notice criterion: a neighbour inside the sky
    annulus inflates the background estimate and eats the star's own flux, and
    it does so by a different amount on a night with worse seeing.

    ``max_hfr_ratio`` throws out anything measurably broader than the frame's
    own stars. On a galaxy field the brightest local maximum in the crop is
    routinely the GALAXY CORE, which is not a point source, does not vary with
    the seeing the way a star does, and would anchor the whole light curve to
    the thing the supernova is sitting in front of.
    """
    img = np.asarray(image, dtype=np.float32)
    sep = min_separation if min_separation is not None else 2.0 * annulus[1]
    # Both the aperture and the sky annulus have to fit inside the crop, or the
    # star is measured through a truncated aperture on every single night.
    edge = max(float(aperture_px), float(annulus[1])) + 4.0
    h, w = img.shape
    stars = detect_stars(fill_nan(img), k_sigma=k_sigma,
                         max_stars=max_candidates)
    stars.sort(key=lambda s: s.flux, reverse=True)
    hfrs = [s.hfr for s in stars if s.hfr > 0]
    hfr_cap = (float(np.median(hfrs)) * max_hfr_ratio) if hfrs else float("inf")

    chosen: list[tuple[float, float]] = []
    for s in stars:
        if len(chosen) >= n:
            break
        if s.peak >= saturation:
            continue
        if s.hfr > hfr_cap:
            continue
        if s.y < edge or s.x < edge or s.y > h - edge or s.x > w - edge:
            continue
        if any(math.hypot(s.y - ey, s.x - ex) < exclude_radius
               for ey, ex in exclude):
            continue
        if any(math.hypot(s.y - o.y, s.x - o.x) < sep
               for o in stars if o is not s):
            continue
        if any(math.hypot(s.y - cy, s.x - cx) < sep for cy, cx in chosen):
            continue
        chosen.append((float(s.y), float(s.x)))
    return chosen


# --------------------------------------------------------------------------
# Animation
# --------------------------------------------------------------------------

def _as_pil(frame8: np.ndarray):
    from PIL import Image
    a = np.asarray(frame8, dtype=np.uint8)
    if a.ndim == 2:
        return Image.fromarray(a, mode="L").convert("RGB")
    return Image.fromarray(a, mode="RGB")


def assemble_gif(frames8: Sequence[np.ndarray], path, frame_ms: int = 700, *,
                 loop: int = 0):
    """Write an animated GIF, every frame sharing ONE palette.

    Pillow's default is to quantise each frame independently, which gives the
    sky a slightly different grey in every frame — the exact flicker the shared
    stretch was chosen to avoid, reintroduced at the last step. Quantising the
    first frame and reusing its palette costs nothing and keeps the background
    still.
    """
    from PIL import Image

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not frames8:
        raise ValueError("assemble_gif needs at least one frame")
    images = [_as_pil(f) for f in frames8]
    base = images[0].quantize(colors=256, method=Image.MEDIANCUT)
    paletted = [base] + [im.quantize(palette=base, dither=Image.Dither.NONE)
                         for im in images[1:]]
    paletted[0].save(out, save_all=True, append_images=paletted[1:],
                     duration=int(frame_ms), loop=int(loop), optimize=False)
    return out


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def assemble_mp4(frames8: Sequence[np.ndarray], path, frame_ms: int = 700):
    """Also write an MP4, when ffmpeg is on PATH. Returns None when it is not.

    Frames are piped in as raw RGB rather than written out as PNGs: no temp
    directory, no cleanup, and nothing left behind if it fails.
    """
    exe = ffmpeg_path()
    if not exe or not frames8:
        return None
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    first = _as_pil(frames8[0])
    w, h = first.size
    fps = max(1.0, 1000.0 / max(1, int(frame_ms)))
    args = [exe, "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
            "-r", f"{fps:.4f}", "-i", "-",
            # yuv420p needs even dimensions; pad rather than scale so no pixel
            # is resampled.
            "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)]
    try:
        proc = subprocess.Popen(args, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    except OSError:
        return None
    try:
        # One frame at a time down the pipe: a fortnight of 1600x1100 frames is
        # 80 MB as one bytes object and nothing needs it to be.
        for f in frames8:
            proc.stdin.write(np.asarray(_as_pil(f), dtype=np.uint8).tobytes())
        proc.stdin.close()
        rc = proc.wait(timeout=300)
    except (OSError, ValueError, subprocess.SubprocessError):
        proc.kill()
        proc.wait()
        return None
    if rc != 0 or not out.exists():
        return None
    return out


def plot_lightcurve(points: Sequence[tuple[str, float, float]], path, *,
                    width: int = 900, height: int = 520,
                    title: str = "relative flux") -> Path:
    """A plain scatter-with-error-bars PNG, drawn with Pillow.

    matplotlib is not a dependency of this package and adding one for a
    four-point plot on an appliance would be absurd. This draws axes, ticks and
    error bars and nothing else.
    """
    from PIL import Image, ImageDraw, ImageFont

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    usable = [(lbl, v, e) for lbl, v, e in points
              if v is not None and math.isfinite(v)]
    img = Image.new("RGB", (width, height), (18, 18, 22))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=14)
        big = ImageFont.load_default(size=18)
    except TypeError:
        font = big = ImageFont.load_default()

    left, right, top, bottom = 90, width - 30, 50, height - 70
    draw.text((left, 16), title, fill=(230, 230, 235), font=big)
    draw.rectangle((left, top, right, bottom), outline=(90, 90, 100))
    if not usable:
        draw.text((left + 10, top + 10), "no measurements", fill=(200, 120, 120),
                  font=font)
        img.save(out)
        return out

    vals = [v for _, v, _ in usable]
    errs = [e if math.isfinite(e) else 0.0 for _, _, e in usable]
    lo = min(v - e for v, e in zip(vals, errs))
    hi = max(v + e for v, e in zip(vals, errs))
    if hi <= lo:
        hi, lo = lo + 1.0, lo - 1.0
    pad = (hi - lo) * 0.12
    lo, hi = lo - pad, hi + pad

    def ypix(v: float) -> float:
        return bottom - (v - lo) / (hi - lo) * (bottom - top)

    for i in range(5):
        v = lo + (hi - lo) * i / 4.0
        y = ypix(v)
        draw.line((left, y, right, y), fill=(48, 48, 56))
        draw.text((10, y - 7), f"{v:.3g}", fill=(170, 170, 180), font=font)

    n = len(usable)
    step = (right - left) / max(1, n)
    for i, (lbl, v, e) in enumerate(usable):
        x = left + step * (i + 0.5)
        y = ypix(v)
        if math.isfinite(e) and e > 0:
            draw.line((x, ypix(v - e), x, ypix(v + e)), fill=(120, 170, 230),
                      width=2)
            draw.line((x - 5, ypix(v + e), x + 5, ypix(v + e)),
                      fill=(120, 170, 230), width=2)
            draw.line((x - 5, ypix(v - e), x + 5, ypix(v - e)),
                      fill=(120, 170, 230), width=2)
        draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=(250, 200, 90))
        draw.text((x - 34, bottom + 10), lbl, fill=(190, 190, 200), font=font)
    img.save(out)
    return out
