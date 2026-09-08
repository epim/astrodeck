"""Expose the next sweep point while the current one is still being measured.

THE IDLE HALF OF EVERY SWEEP. A point costs `move -> expose -> measure`, and on
a 26 MP frame the measure half is star detection: seconds of CPU against a 6 s
exposure. The shutter is shut for all of it, the focuser is parked for all of
it, and a nine-point sweep therefore spends most of its wall-clock doing
nothing a camera could not have been doing at the same time.

`sweep.rs` was written with this in mind and says so:

    Pipelined move-while-analyzing timing is a host concern; the machine
    preserves the measurement positions and their order exactly.

WHAT MAKES IT AWKWARD is that the engine will not say where to go next until
the current measurement has been added — that is the whole shape of the
`next()` / `add_measurement()` protocol. So overlapping means PREDICTING the
next position, and the value of this module is that the prediction is checked
rather than trusted.

THE PREDICTION IS NO LONGER A RULE OF THUMB. It used to be one line — `pos -
step`, because both of the engine's phases descend — and that line was wrong
about two moves of every run it described: the turn-round (where the engine
extends UPWARD from the highest point it has measured) and the validation move
(which is up, at the fitted focus). The first cost a wasted exposure and, under
the one-strike rule below, all the overlap for the rest of the run; the second
had to be dodged by counting emitted positions.

Now the engine answers for itself. `FocusSweep.peek_next(position, hfr, stdev,
star_count)` runs the hypothetical on a CLONE of the state machine and returns
the step it would emit, with the `kind` of measurement that step expects back.
So this module:

  * forms the measurement the pending frame is EXPECTED to produce — a linear
    extrapolation in position from the last two measured points (one point: its
    own value; none: no guess), with the last point's σ and star count;
  * asks the engine what it would do with it;
  * speculates only on `move_to` with kind `point` — a `validation`, a
    `restore`, a `baseline` or a terminal step is recognised, not counted to.

A MISS IS STILL POSSIBLE, and it is worth saying exactly where. The
extrapolation is linear and the curve it walks is a V, so at the turn — the one
point where the sweep pivots — the extrapolated value can land on the wrong
side of the vertex, and the engine's answer to that hypothetical is a different
position from its answer to the real measurement. That is one frame, at one
point of the run, and the rule below bounds it.

CORRECTNESS DOES NOT DEPEND ON BEING RIGHT. A speculatively exposed frame is
used only when the engine asks for the position it was taken at; otherwise it
is thrown away. What the prediction buys is time, and what a miss costs is one
exposure. One rule bounds that, because a wasted exposure through a narrowband
filter is 30 s and would turn a speedup into a regression:

  1. ONE STRIKE. After the first miss, speculation is off for the rest of the
     run. Worst case per run: one wasted exposure.

The old rules 2 and 3 (stop before the validation move; never guess at or below
the focuser's floor) are gone because the engine now answers both itself: the
validation move arrives labelled, and a clamped or terminal step is not a
`point`. The loop's own leash check is unchanged and still bounds where a
speculative move may go.

Pure, and separate from `focus.native`, for the reason the rest of this
package's decisions are: the loop it steers runs behind a camera, a focuser and
a Rust state machine, so a rule that only exists inside that loop is a rule
only a telescope can test.
"""
from __future__ import annotations

#: Star count used for the hypothetical when the caller passes no counts. The
#: engine reads `star_count` as a fit weight and as its no-star sentinel (0
#: means "this frame had nothing in it"), so the one thing this must not be is
#: zero: a hypothetical of "the next frame is empty" is not what the sweep is
#: about to measure, and it would make the engine's answer describe a failure.
ASSUMED_STARS = 20

#: A linear extrapolation across a V can walk straight through the vertex and
#: come out negative, and the engine is being asked about a STAR SIZE. Clamp it
#: to something small and positive rather than hand it an impossibility.
MIN_EXPECTED_HFR = 0.1


class SweepPredictor:
    """Where the sweep will ask to go next, and whether it is worth guessing.

    The caller reports every position the engine EMITS (`emitted`) and then asks
    for the next guess (`predict`). Nothing here touches a device: `predict`
    borrows the caller's `FocusSweep` only to ask it a hypothetical question,
    which `peek_next` answers on a clone.
    """

    def __init__(self):
        self.predicted: int | None = None
        self.hits = 0
        self.misses = 0
        #: Set by the first miss and never cleared — see rule 1.
        self.stopped = False

    def seed(self, position: int) -> None:
        """Record a speculative exposure this module did not choose.

        The probe's own move is one: the loop asks the engine for its first step
        BEFORE the probe is measured and starts that move and exposure
        underneath the measurement, so by the time the loop reaches its first
        `emitted()` a frame is already in flight for that position. Without this
        the accounting would call the run's most certain guess a miss and turn
        speculation off before the sweep had taken a single point.
        """
        self.predicted = int(position)

    def emitted(self, position: int) -> bool:
        """Record the position the engine asked for. True if we had guessed it.

        A miss stops speculation for the rest of the run, whatever caused it: a
        dropped point the engine re-asks, or an extrapolation that landed on the
        wrong side of the vertex at the turn. Both mean the same thing — the
        expected measurement this run is predicting from no longer describes
        what the sweep is doing.
        """
        hit = self.predicted is not None and self.predicted == int(position)
        if self.predicted is not None:
            if hit:
                self.hits += 1
            else:
                self.misses += 1
                self.stopped = True
        self.predicted = None
        return hit

    def predict(self, sweep, pending_position: int,
                points: list[tuple[int, float, float]],
                counts: list[int] | None = None) -> int | None:
        """The next position to speculatively expose at, or None to sit still.

        ``sweep`` is the live ``FocusSweep``; ``pending_position`` is the point
        whose frame is about to be measured; ``points`` is every accepted
        ``(position, hfr, sigma)`` IN THE ORDER MEASURED, and ``counts`` their
        star counts. Nothing here mutates the sweep — `peek_next` clones it.

        None is always safe: the caller falls back to `move -> expose` in line,
        which is exactly what the loop did before this existed.
        """
        self.predicted = None
        if self.stopped or not points:
            return None
        # An older wheel has no peek_next. Sitting still costs the speedup and
        # nothing else; guessing with the retired descending rule would cost a
        # frame at every turn-round instead.
        if not hasattr(sweep, "peek_next"):
            return None

        pending = int(pending_position)
        expected = self._expected_hfr(pending, points)
        sigma = float(points[-1][2])
        stars = int(counts[-1]) if counts else ASSUMED_STARS
        try:
            step = sweep.peek_next(pending, float(expected), sigma,
                                   max(1, stars))
        except Exception:      # noqa: BLE001 - a guess must not raise
            # The engine rejecting a hypothetical is not the run's failure: the
            # real measurement goes through `add_measurement`, which is
            # unaffected by anything asked here.
            return None
        if step.get("action") != "move_to" or step.get("kind") != "point":
            # `validation`, `restore`, `baseline`, `done`, `failed` — every one
            # of them is a move this module must not speculate on. The
            # validation frame in particular must be taken at the fitted vertex
            # AFTER the fit, not at a position guessed before it.
            return None
        self.predicted = int(step["position"])
        return self.predicted

    @staticmethod
    def _expected_hfr(pending: int,
                      points: list[tuple[int, float, float]]) -> float:
        """What the pending frame is expected to measure.

        Linear in position through the last two measured points, because that
        is what a V-curve's flank is over one step and the engine only needs the
        answer to be on the right side of its own decisions. One point: its own
        value (a flat guess is better than no guess — it is right whenever the
        engine's next move does not depend on the size, which is most of the
        initial pass).
        """
        if len(points) == 1:
            return max(MIN_EXPECTED_HFR, float(points[-1][1]))
        (p0, h0, _s0), (p1, h1, _s1) = points[-2], points[-1]
        if p1 == p0:
            return max(MIN_EXPECTED_HFR, float(h1))
        slope = (float(h1) - float(h0)) / float(p1 - p0)
        return max(MIN_EXPECTED_HFR, float(h1) + slope * float(pending - p1))


class Prefetch:
    """One speculative move-and-expose, in flight.

    Holds the position it is exposing at so the frame can only ever be used for
    that position. AWAITED rather than cancelled on the ordinary paths: this
    task owns the focuser and the camera while it runs, so anything else that
    wants either — a failure return, the leash, the restore-to-start — has to
    let it finish first. Cancelling a download half way is how a camera ends up
    in a state the next exposure inherits.
    """

    def __init__(self, position: int, task):
        self.position = int(position)
        self.task = task

    async def take(self, position: int):
        """The frame if it was exposed at ``position``, else None (discarded).

        Either way the task is finished before this returns, so the caller owns
        the devices again.
        """
        frame = await self.settle()
        return frame if self.position == int(position) else None

    async def settle(self, *, cancel: bool = False):
        """Let the speculative exposure finish (or kill it) and hand back its
        frame. Never raises: a speculative failure is not the run's failure, and
        the in-line path that follows will hit the same device error properly.

        ``cancel`` is for the teardown path only, where the sweep itself is
        already being cancelled and waiting out an exposure would just delay a
        halt the user asked for.
        """
        if cancel:
            self.task.cancel()
        try:
            return await self.task
        except BaseException:      # noqa: BLE001 - a guess must not raise
            return None
