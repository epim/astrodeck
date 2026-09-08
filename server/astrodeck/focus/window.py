"""How much of a frame a sweep point needs to measure.

WHY THIS EXISTS, in numbers. One native autofocus sweep on astrotown (v0.3.25,
26 MP Player One, 6 s frames at gain 200, bin 1, 9 points) took 7-9 minutes on
2026-09-07, and roughly 80 percent of every point was MEASUREMENT on 26 million
pixels while the shutter was shut. The shutter was open for 6 s of a 40 s point.

Measured on the rig's own frames (6224 x 4168), whole frame against the central
0.4 of each axis — 16 percent of the pixels:

    pass                        whole frame        0.4 window
    detect_and_measure          60-67 s (rich)     12-14 s
                                27 s (34 stars)
    focus_size                  8.8-9.5 s          1.0-2.2 s

and on a synthetic 26 MP frame with 1200 stars, 8.3 s -> 1.3 s and 0.85-1.7 s ->
0.16-0.47 s. A star field's count scales with AREA, so keeping ``keep`` stars
means keeping sqrt(keep / n_full) of each axis — which is the whole of
``measure_window``.

DOES IT MEASURE THE SAME NUMBER? On the rig's L light, 4.51 px windowed against
4.64 px whole-frame (3 percent). On an Ha light, 3.27 against 3.98 — 18 percent
SMALLER, because corner stars are bigger on this optic. That is not an error:
a windowed sweep measures the CENTRE's focus, which is the focus anyone cares
about. It does mean the window has to be the SAME for every point of a sweep,
or a centre-versus-corner step lands in the middle of the curve — hence a single
fraction chosen once, from the probe, and never revised per point.
"""
from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

#: Stars a windowed measurement aims to keep. The size metric is a median over
#: the brightest few (``imaging.stars._bright_population`` lets the top flux
#: quartile vote, 5..25 of them), so 40 detections is the count at which the
#: voting population reaches its own ceiling — measuring more of the frame buys
#: pixels, not precision.
MEASURE_WINDOW_KEEP = 40

#: The smallest window, as a fraction of EACH axis. 0.4 keeps 16 percent of the
#: pixels, which is where the rig's measured 8.8-9.5 s falls to 1.0-2.2 s. Below
#: this the window starts to be a different optical field rather than a cheaper
#: sample of the same one (off-axis aberration varies across the frame), and the
#: saving is already 6x.
MEASURE_WINDOW_FLOOR = 0.4


def measure_window(n_full: int, keep: int = MEASURE_WINDOW_KEEP,
                   floor: float = MEASURE_WINDOW_FLOOR) -> float:
    """The fraction of EACH AXIS a sweep point should measure, from the probe's
    star count.

    1.0 — the whole frame — when the field holds fewer than ``keep`` stars:
    there is nothing to give away, and a window that came back below the fit's
    star floor would be re-measured whole anyway, having paid for both.

    Otherwise sqrt(keep / n_full), clamped to ``floor``. Star count goes as
    area, so that is the smallest window expected to still hold ``keep`` stars.
    """
    n = int(n_full)
    if n < int(keep) or keep <= 0:
        return 1.0
    return float(min(1.0, max(float(floor), math.sqrt(float(keep) / n))))


def centre_slice(shape: tuple[int, int], frac: float) -> tuple[slice, slice]:
    """A CENTRED window of ``shape``, as slices — ``frac`` 1.0 is the whole frame.

    Centred because the probe measured the whole frame and the window has to be
    a sample of the same field, not a different corner of it. Slices rather than
    an array so the caller keeps a VIEW: a 26 MP frame is 52 MB, and the
    measurement only ever reads.
    """
    h, w = int(shape[0]), int(shape[1])
    f = float(frac)
    if f >= 1.0:
        return slice(0, h), slice(0, w)
    # At least one pixel on each axis: a degenerate window would report "no
    # sources" and be blamed on the sky.
    ch = max(1, min(h, int(round(h * f))))
    cw = max(1, min(w, int(round(w * f))))
    y0 = (h - ch) // 2
    x0 = (w - cw) // 2
    return slice(y0, y0 + ch), slice(x0, x0 + cw)


def window_frame(frame, frac: float):
    """The same frame with its ``data`` narrowed to the centred window.

    A FRAME, not an array, because the measurement seam takes one: substitutes
    read ``frame.focuser_position`` off it to know which sweep point they are
    looking at (the sweep exposes the next point while this one is measured, so
    the live focuser position belongs to a different frame). Everything but the
    pixels is carried across unchanged, and the pixels are a view — no copy.

    ``frac`` 1.0 returns the frame itself, so the whole-frame path allocates
    nothing at all.
    """
    if float(frac) >= 1.0:
        return frame
    data = np.asarray(frame.data)
    return replace(frame, data=data[centre_slice(data.shape, frac)])
