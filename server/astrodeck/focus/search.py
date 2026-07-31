"""Deciding where to move next when you cannot see stars.

Pure decision logic, no I/O — so the part that is easy to get wrong in a way
nobody notices until 2am at the scope can be tested at a desk.

The model: a defocused star is an annulus whose radius grows LINEARLY with
distance from focus, ``r = k * |x - x_focus|``. That gives a V in |x|, so:

* two measurements on the SAME side extrapolate straight to the crossing;
* a measurement that got BIGGER means the last move went the wrong way;
* once the blob is small enough, stop — the V-curve autofocus measures HFR far
  better than this ever will, and its job starts where this one ends.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Below this the blob is a star, not a donut: hand over.
from ..imaging.defocus import HANDOVER_R80_PX

#: A probe must be big enough to change the blob by more than measurement
#: noise. Expressed as a fraction of the searchable span.
PROBE_FRACTION = 0.08

#: Never trust an extrapolation further than this multiple of the span — a
#: shallow slope from two noisy points can otherwise fling the focuser to the
#: far end of the travel on one bad frame.
MAX_EXTRAPOLATION_SPAN = 1.5


@dataclass(frozen=True)
class Probe:
    position: int
    r80: float


@dataclass(frozen=True)
class Decision:
    """What to do next. Exactly one of ``move_to`` / ``done`` / ``give_up``."""
    move_to: int | None = None
    done: bool = False
    give_up: str | None = None
    why: str = ""


def _clamp(x: float, lo: int, hi: int) -> int:
    return int(max(lo, min(hi, round(x))))


def decide(history: list[Probe], lo: int, hi: int, *,
           handover: float = HANDOVER_R80_PX,
           max_probes: int = 12) -> Decision:
    """Where to go next, given everything measured so far.

    ``lo``/``hi`` bound the SEARCHABLE range — which is the focuser's real
    travel, not whatever ``max_position`` claims. On this rig max_position reads
    600000 while the usable range is a small fraction of that, and a search that
    trusts the claim spends its night driving to positions the focuser will
    never reach.
    """
    if not history:
        return Decision(move_to=None, why="measure where we are first")

    last = history[-1]
    if last.r80 <= handover:
        return Decision(done=True,
                        why=f"blob is {last.r80:.1f}px — small enough to autofocus")
    if len(history) >= max_probes:
        best = min(history, key=lambda p: p.r80)
        return Decision(give_up=f"no focus found in {len(history)} probes; "
                                f"best was {best.r80:.0f}px at {best.position}",
                        move_to=best.position)

    span = max(1, hi - lo)
    probe = max(1, int(span * PROBE_FRACTION))

    if len(history) == 1:
        # Nothing to compare against yet. Probe toward the MIDDLE of the range:
        # focus is more often inside the travel than beyond the end we happen to
        # be sitting on, and probing outward from an end wastes the move.
        mid = (lo + hi) // 2
        direction = 1 if mid >= last.position else -1
        return Decision(move_to=_clamp(last.position + direction * probe, lo, hi),
                        why="first probe, toward the middle of the travel")

    prev = history[-2]
    if last.position == prev.position:
        # Already tried this exact spot. Nudging by +probe is useless when the
        # position is CLAMPED at a range edge — it proposes the same number
        # again, and the search burns its whole probe budget standing still
        # (observed on the rig: five probes in a row at 40000). Push AWAY from
        # whichever edge we are pinned to, and if we are pinned to both there is
        # nowhere left to look.
        if last.position >= hi:
            nxt = _clamp(last.position - probe, lo, hi)
        elif last.position <= lo:
            nxt = _clamp(last.position + probe, lo, hi)
        else:
            nxt = _clamp(last.position + probe, lo, hi)
        if nxt == last.position:
            return Decision(give_up=("the search is pinned at "
                                     f"{last.position} with nowhere left to "
                                     "move inside the usable travel"),
                            move_to=min(history, key=lambda p: p.r80).position)
        return Decision(move_to=nxt, why="already tried here; move off the edge")

    # Two points on a V. If the blob shrank, the line through them crosses zero
    # at (or near) focus. If it grew, the same line points backwards — which is
    # still the right direction to go, just the other way.
    slope = (last.r80 - prev.r80) / (last.position - prev.position)
    if abs(slope) < 1e-9:
        return Decision(move_to=_clamp(last.position + 2 * probe, lo, hi),
                        why="blob unchanged; probe further")

    target = last.position - last.r80 / slope
    limit = MAX_EXTRAPOLATION_SPAN * span
    if abs(target - last.position) > limit:
        # A shallow slope from noisy points extrapolates absurdly far. Step a
        # bounded amount in the indicated direction instead of leaping.
        step = probe * 3 * (1 if target > last.position else -1)
        return Decision(move_to=_clamp(last.position + step, lo, hi),
                        why="extrapolation implausibly far; bounded step instead")
    nxt = _clamp(target, lo, hi)
    if nxt == last.position:
        # Already where the extrapolation points, but still too big: the model
        # is off. Probe past it rather than sitting still.
        return Decision(move_to=_clamp(last.position + probe, lo, hi),
                        why="at the predicted focus but still defocused; probe on")
    return Decision(move_to=nxt,
                    why=f"extrapolated focus from {prev.r80:.0f}px@{prev.position} "
                        f"and {last.r80:.0f}px@{last.position}")
