"""Pure ADU-target flat-exposure solver (PRO-5). No numpy, no I/O — a bounded,
iterative linear solver so it is exercisable with a synthetic panel in pytest and
reusable by the sequence engine (and, later, the twilight sky-flat re-solve).

Flat signal above the bias pedestal is ~proportional to exposure, so the next
exposure estimate is a single linear ratio, clamped to [min, max] and iterated:

    next = last_exposure * (target_adu - pedestal) / (measured - pedestal)

The solver is deterministic and side-effect-free: feed it the median ADU of each
trial capture; it returns a ``FlatStep`` telling the caller whether to stop and
what exposure to use next (or to accept).
"""
from __future__ import annotations

from dataclasses import dataclass

_EPS = 1e-9


@dataclass(frozen=True)
class FlatStep:
    exposure_s: float      # exposure to try next, or the accepted one when done
    done: bool             # stop the metering loop
    converged: bool        # measurement landed in the target band
    reason: str            # "" running | "converged" | "too_bright_at_min"
    #                        | "too_dim_at_max" | "max_iterations"
    iterations: int        # updates applied so far


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


class FlatExposureSolver:
    """Bounded, iterative ADU-target exposure solver.

    Construct with the target ADU and an initial exposure guess; call ``first()``
    for the (clamped) initial exposure to shoot, then feed each capture's median
    ADU to ``update()`` until it returns a ``FlatStep`` with ``done=True``."""

    def __init__(self, target_adu: float, *, initial_exposure_s: float,
                 min_exposure_s: float = 0.001, max_exposure_s: float = 30.0,
                 tolerance: float = 0.10, pedestal: float = 0.0,
                 max_iterations: int = 8) -> None:
        self.target = float(target_adu)
        self.min_s = float(min_exposure_s)
        self.max_s = float(max_exposure_s)
        self.tol = float(tolerance)
        self.pedestal = float(pedestal)
        self.max_iterations = int(max_iterations)
        self._last = _clamp(float(initial_exposure_s), self.min_s, self.max_s)
        self._iters = 0
        self._best_err = float("inf")
        self._best_exp = self._last

    def _band(self) -> tuple[float, float]:
        return self.target * (1 - self.tol), self.target * (1 + self.tol)

    def first(self) -> FlatStep:
        return FlatStep(self._last, False, False, "", 0)

    def update(self, measured_adu: float) -> FlatStep:
        self._iters += 1
        m = float(measured_adu)
        lo, hi = self._band()
        err = abs(m - self.target)
        if err < self._best_err:
            self._best_err, self._best_exp = err, self._last
        # converged?
        if lo <= m <= hi:
            return FlatStep(self._last, True, True, "converged", self._iters)
        signal = m - self.pedestal
        # rail checks BEFORE proposing a move
        if m > hi and self._last <= self.min_s + _EPS:
            return FlatStep(self.min_s, True, False, "too_bright_at_min", self._iters)
        if (m < lo) and self._last >= self.max_s - _EPS:
            return FlatStep(self.max_s, True, False, "too_dim_at_max", self._iters)
        if self._iters >= self.max_iterations:
            return FlatStep(self._best_exp, True, False, "max_iterations", self._iters)
        # linear estimate (no-signal / divide-by-zero guard → drive to max)
        if signal <= _EPS:
            nxt = self.max_s
        else:
            nxt = self._last * (self.target - self.pedestal) / signal
        self._last = _clamp(nxt, self.min_s, self.max_s)
        return FlatStep(self._last, False, False, "", self._iters)
