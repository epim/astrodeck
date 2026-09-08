# Autofocus efficiency: a sweep that costs the camera, not the CPU

Status: in progress, 2026-09-08. Local commits only.

## The measured problem

One native autofocus sweep on astrotown (v0.3.25, 26 MP Player One, 6 s focus
frames at gain 200, bin 1, 9 points) takes 7 to 9 minutes. The night log of
2026-09-07 21:39 to 21:48 gives the shape of the cost:

| stage | wall | what it was doing |
|---|---|---|
| probe frame | 35 s | 6 s exposure + download + a full-frame Rust `detect_and_measure` (about 27 s) |
| sweep point, near focus (HFR 6-10) | 32-48 s | pipelined: the next exposure runs under this point's measurement, so the wall is the measurement: 27 s Rust pass + 5-20 s `focus_size` |
| sweep point, far out (HFR 25-40) | 43-74 s | `focus_size` grows with the star size |
| validation point | 84 s | never speculated (by rule), so move + expose + a measurement in series |

Roughly 80 percent of every point is measurement on 26 million pixels; the
shutter is open for 6 s of a 40 s point. Two smaller wastes ride along: the
predictor stops speculating at the first turn-round (one wasted exposure, then
nothing overlapped for the rest of the run), and the validation move and the
final settle approach the vertex moving OUT while every swept point was
approached moving IN, on a focuser measured at tens of steps of backlash.

## The plan, in three lanes

1. **Measure the centre, once.** Per point, drop the Rust `detect_and_measure`
   (it supplied a star count and a MAD that `focus_size` can supply itself)
   and run `focus_size` on a centred window of the frame sized from the probe's
   star count: the smallest window expected to keep about 40 stars, never
   smaller than 0.4 of each axis, and the whole frame when the field is too
   sparse for any window. A window that measures fewer than the per-point
   minimum falls back to the whole frame for that point. The probe keeps its
   full-frame Rust pass: that count is what sizes the window.
2. **Predict with the engine, not a rule of thumb.** `FocusSweep.peek_next`
   (Rust, additive) answers "what would you ask for next if this point measured
   X", so the host speculates through the turn-round instead of stopping at it,
   and every step carries its `kind` (point, validation, restore, baseline) so
   the validation move is recognised rather than counted. The probe's
   measurement overlaps the first sweep move and exposure, because the engine
   can be asked for the first position before the probe is measured.
3. **Approach from one side.** Every OUT move overshoots by a configurable
   `approach_overshoot_steps` (default 200) and returns IN, so the swept points,
   the validation frame and the final position are all reached moving IN. This
   is the astro-focus `Backlash` Overshoot model with only OUT backlash set,
   applied by the host.

## Measuring it

`server/tests/_focus_clock.py` runs the real host loop and the real Rust state
machine under a virtual clock with a rig-calibrated cost model (exposure,
download, focuser rate, measurement cost per pixel and per pixel of star
size), a focuser with mechanical backlash, and stubbed measurements that answer
from a V-curve model. It reports wall time, exposures, wasted frames,
measurement passes with their pixel fraction, moves and reversals, camera idle
time, and the final position error. The cost model is calibrated so the
2026-09-07 21:39 sweep replays within 15 percent of its logged 9 min 11 s.

The status quo is measured on the tree before any lane lands; each lane is
measured after it merges. The table lives in this file's changelog below.

## Changelog

### 2026-09-08 status quo (git 93692d3), from `python tests/_focus_clock.py`

```
metric                 rich_at_focus  rich_turnround       sparse_83         rich_83
--------------------  --------------  --------------  --------------  --------------
wall_s                         689.8           700.1           503.2           678.1
exposures                         11              12              11              11
wasted_frames                      0               1               0               0
rust_passes (frac)        11 (1.00)       11 (1.00)       11 (1.00)       11 (1.00)
size_passes (frac)        10 (1.00)       10 (1.00)       10 (1.00)       10 (1.00)
moves / reversals           11 / 2          12 / 3          11 / 3          11 / 3
camera_idle_s                  543.2           544.9           378.4           531.8
physical_error_steps             -39               5             -39             -39
approach_of_final                out              in              in              in
```

The sparse replay lands 8.5 percent under the rig's logged 550 s with no
constant fitted to it (every cost constant is anchored on a rig measurement:
the 2026-09-07 log and a benchmark of the two measurements on real frames on
the rig itself, scratch note af_cost_box.out). What the replay also showed:
the first sweep point and the validation frame are the run's only outward
moves, so on a focuser with 40 steps of backlash both are measured 40 steps
low. The distorted validation frame reads 36 percent worse than a swept
neighbour, which trips the confirm-the-fit override; the override then moves
one step in, shorter than the slack, so it turns the motor and not the tube.
That is the 2026-08-19 "the confirming frame read worse than a sample already
in hand" observation with a cause attached.
