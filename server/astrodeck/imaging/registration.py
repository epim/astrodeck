"""Star-pattern registration: the translation between two star fields.

Live stacking v1 anchored on the single brightest star. That fails in three
ways a live stack notices within minutes:

  * the brightest star saturates and its centroid wanders inside the flat top;
  * two stars of similar flux swap rank between frames, so the "anchor" jumps
    to a different star and the whole stack shifts by their separation;
  * the anchor drifts out of frame or is lost to a passing cloud, and the next
    frame re-anchors on something else entirely.

This module matches a CONSTELLATION instead. Every (reference, current) star
pair proposes a candidate translation; the candidate with the most independent
support wins, and the winner is refined to sub-pixel precision by averaging its
supporters' residuals. A wrong pair proposes a translation no other pair agrees
with, so it gets one vote and loses.

Pure and dependency-light on purpose (numpy only, no scipy): it runs inside the
capture path on every sub.
"""
from __future__ import annotations

from dataclasses import dataclass

from .stars import Star

#: How many of the brightest stars take part in the vote. The cost is O(K^2)
#: candidates x O(K) support checks; at 12 that is ~1.7k distance tests, which
#: is nothing next to the detection pass that produced the stars.
DEFAULT_MAX_STARS = 12
#: How close a predicted position must land to count as support (pixels). Wide
#: enough to tolerate field rotation across a short sub and centroid noise on
#: faint stars, tight enough that two different stars rarely both qualify.
DEFAULT_TOLERANCE_PX = 2.5
#: Fewest supporting pairs for a translation to be believed. Two points can be
#: coincidence; three agreeing across the field is a pattern.
DEFAULT_MIN_SUPPORT = 3


@dataclass(frozen=True)
class Registration:
    """The measured translation from the reference field to the current one."""
    dx: float
    dy: float
    #: how many star pairs agreed with this translation
    support: int
    #: RMS residual of the supporting pairs (px) — how tight the agreement was
    rms: float

    @property
    def shift(self) -> tuple[float, float]:
        return (self.dx, self.dy)


def brightest(stars: list[Star], n: int = DEFAULT_MAX_STARS) -> list[Star]:
    """The ``n`` highest-FLUX stars, brightest first.

    ``detect_stars`` appends in descending PEAK-pixel order, not flux order
    (stars.py), and a saturated star's peak is clipped — so peak order puts
    flat-topped blobs ahead of genuinely brighter stars. Select on flux.
    """
    return sorted(stars, key=lambda s: s.flux, reverse=True)[:max(0, int(n))]


def register(ref: list[Star], cur: list[Star], *,
             max_stars: int = DEFAULT_MAX_STARS,
             tolerance_px: float = DEFAULT_TOLERANCE_PX,
             min_support: int = DEFAULT_MIN_SUPPORT,
             max_shift_px: float | None = None) -> Registration | None:
    """Translation taking ``ref`` onto ``cur``, or None when there is no
    agreement worth trusting.

    Returning None is a first-class answer: it means "this sub cannot be placed
    on the stack", which is exactly the right response to a cloud, a cable snag,
    or a frame full of hot pixels. The caller rejects the sub rather than
    stacking it at a guessed offset.

    ``max_shift_px`` optionally rejects a translation larger than the caller is
    willing to believe (a real slew, not drift), before the support test.

    A failed first pass ESCALATES the candidate pool before giving up. Taking
    the K brightest assumes the K brightest detections are stars, and one
    saturated satellite trail breaks that assumption completely: the detector
    reports a long row of bright local maxima, they fill the top K, and the real
    constellation never gets considered. Failure is exactly the signal that the
    pool was not representative, so widening it costs nothing on the happy path.
    """
    for k in (max_stars, max_stars * 2):
        got = _register_at(ref, cur, k, tolerance_px, min_support, max_shift_px)
        if got is not None:
            return got
        if k >= len(ref) and k >= len(cur):
            break        # already considering every star; a wider net is the same net
    return None


def _register_at(ref: list[Star], cur: list[Star], max_stars: int,
                 tolerance_px: float, min_support: int,
                 max_shift_px: float | None) -> Registration | None:
    r = brightest(ref, max_stars)
    c = brightest(cur, max_stars)
    if len(r) < min_support or len(c) < min_support:
        return None

    tol2 = float(tolerance_px) ** 2
    best: Registration | None = None

    for a in r:
        for b in c:
            dx = b.x - a.x
            dy = b.y - a.y
            if max_shift_px is not None and (dx * dx + dy * dy) > max_shift_px ** 2:
                continue
            # How many OTHER reference stars land on a current star under this
            # translation? Each reference star may only be claimed once, and by
            # its nearest candidate, so a dense clump cannot inflate the vote.
            support = 0
            sum_ex = sum_ey = sum_sq = 0.0
            for s in r:
                px, py = s.x + dx, s.y + dy
                nearest = None
                nd2 = tol2
                for t in c:
                    d2 = (t.x - px) ** 2 + (t.y - py) ** 2
                    if d2 <= nd2:
                        nd2 = d2
                        nearest = t
                if nearest is None:
                    continue
                support += 1
                sum_ex += nearest.x - px
                sum_ey += nearest.y - py
                sum_sq += nd2
            if support < min_support:
                continue
            # Refine: the mean residual of the supporters IS the sub-pixel
            # correction to the integer-ish candidate this pair proposed.
            rdx = dx + sum_ex / support
            rdy = dy + sum_ey / support
            rms = (sum_sq / support) ** 0.5
            cand = Registration(rdx, rdy, support, rms)
            # More support wins; ties break on the tighter fit.
            if best is None or (cand.support, -cand.rms) > (best.support, -best.rms):
                best = cand

    return best
