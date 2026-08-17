"""Expose the next sweep point while the current one is still being measured.

THE IDLE HALF OF EVERY SWEEP. A point costs `move -> expose -> measure`, and on
a 26 MP frame the measure half is `detect_and_measure` plus `focus_size`: 3-6 s
of CPU against a 2 s exposure. The shutter is shut for all of it, the focuser
is parked for all of it, and a nine-point sweep therefore spends roughly half
its wall-clock doing nothing a camera could not have been doing at the same
time.

`sweep.rs` was written with this in mind and says so:

    Pipelined move-while-analyzing timing is a host concern; the machine
    preserves the measurement positions and their order exactly.

WHAT MAKES IT AWKWARD is that the engine will not say where to go next until
the current measurement has been added — that is the whole shape of the
`next()` / `add_measurement()` protocol. So overlapping means PREDICTING the
next position, and the value of this module is that the prediction is checked
rather than trusted.

THE PREDICTION IS ONE LINE. `setup_initial_sweep` walks `start + offset*step`
DOWN to `start`; `decide_extension` then continues down from
`points.first().position - step`. Both phases descend by exactly one step, and
that matches every sweep this rig has logged — 12185, 11835, 11485, 11135,
10785, 10435, 10085, 9735, 9385, 9035, 8685 on 2026-08-17. So the next position
is the current one minus `step`.

CORRECTNESS DOES NOT DEPEND ON BEING RIGHT. A speculatively exposed frame is
used only when the engine asks for the position it was taken at; otherwise it
is thrown away. What the prediction buys is time, and what a miss costs is one
exposure. Two rules bound that cost, because a wasted exposure through a
narrowband filter is 30 s and would turn a speedup into a regression:

  1. ONE STRIKE. After the first miss, speculation is off for the rest of the
     run. Worst case per run: one wasted exposure.
  2. STOP BEFORE VALIDATION. After `2*steps_each_side` emitted positions the
     engine is about to fit and move to its validation point — which is UP, at
     the fitted focus, never `pos - step`. Without this rule that miss would be
     GUARANTEED on every successful run, i.e. we would pay the one-exposure
     penalty every single time.

Pure, and separate from `focus.native`, for the reason the rest of this
package's decisions are: the loop it steers runs behind a camera, a focuser and
a Rust state machine, so a rule that only exists inside that loop is a rule
only a telescope can test.
"""
from __future__ import annotations


class SweepPredictor:
    """Where the sweep will ask to go next, and whether it is worth guessing.

    The caller reports every position the engine EMITS (`emitted`) and then asks
    for the next guess (`predict`). Nothing here touches a device.
    """

    def __init__(self, step: int, steps_each_side: int):
        self.step = int(step)
        #: How many emitted positions are still worth predicting. `2*sides` is
        #: one short of the nominal `2*sides + 1` point sweep on purpose: the
        #: position AFTER the last swept point is the validation move, and
        #: guessing it wrong is the miss rule 2 above exists to avoid.
        self.limit = 2 * max(1, int(steps_each_side))
        self.last: int | None = None
        self.predicted: int | None = None
        self.emitted_count = 0
        self.hits = 0
        self.misses = 0
        #: Set by the first miss and never cleared — see rule 1.
        self.stopped = False

    def emitted(self, position: int) -> bool:
        """Record the position the engine asked for. True if we had guessed it.

        A miss stops speculation for the rest of the run, whatever caused it: a
        dropped point the engine re-asks, an extension that turned round, or the
        run reaching validation. All three mean the same thing — the descending
        model no longer describes what this sweep is doing.
        """
        hit = self.predicted is not None and self.predicted == int(position)
        if self.predicted is not None:
            if hit:
                self.hits += 1
            else:
                self.misses += 1
                self.stopped = True
        self.predicted = None
        self.last = int(position)
        self.emitted_count += 1
        return hit

    def predict(self) -> int | None:
        """The next position to speculatively expose at, or None to sit still.

        None is always safe: the caller falls back to `move -> expose` in line,
        which is exactly what the loop did before this existed.
        """
        if self.stopped or self.last is None:
            return None
        if self.emitted_count > self.limit:
            return None
        nxt = self.last - self.step
        # At or below the focuser's floor the engine clamps to 0 and then breaks
        # out of the extension loop (`hit_focuser_zero`), so the descending model
        # stops applying. Sit still rather than guess at the edge.
        if nxt <= 0:
            return None
        self.predicted = nxt
        return nxt


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
