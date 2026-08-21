# The autofocus confirm frame measures worse than the sweep, and I do not know why

**Status:** OBSERVED AND UNEXPLAINED. Three hypotheses tested and refuted. Not
fixed, and deliberately not "fixed" on a guess.

## The observation

Nine autofocus runs on 2026-08-20/21 (0.3.4, NGC 7129). In every one, the frame
taken at the fitted position measured worse than the best point already measured
during the sweep. Never better, once equal:

| run | best sweep point | frame at fitted position | penalty |
|---|---|---|---|
| 21:37 | 11206 -> 2.81 | 2.81 | 0% |
| 21:43 | 11224 -> 3.77 | 4.01 | +6.4% |
| 22:02 | 11194 -> 3.05 | 11205 -> 3.78 | +24% |
| 22:23 | 11194 -> 3.13 | 11195 -> 4.13 | +32% |
| 22:33 | 11174 -> 3.33 | 11178 -> 4.49 | +35% |
| 23:04 | 11194 -> 3.13 | 11179 -> 4.40 | +41% |
| 23:14 | 11174 -> 3.28 | 11168 -> 4.43 | +35% |
| 23:29 | 11194 -> 3.53 | 11176 -> 4.19 | +19% |
| 23:51 | 11122 -> 4.18 | 11154 -> 4.46 | +6.7% |

**Beware the selection bias that nearly fooled the author of this note.** The
`confirmed_best` warning only fires above 15%, so reading the warnings alone
shows 6 runs and suggests the other 3 were clean. They were not; two of them
were 6% worse. Read the completion lines too.

The 22:23 run is the sharpest case: the fitted position was **one step** from
the best sweep point and measured 32% worse. At this focuser's own measured
0.0776 px/step that step is worth 0.078 px, not 1.00 px. Whatever this is, it
is 13x larger than the position difference can explain.

Star counts fall with it -- 870 -> 623 at 22:23, 1803 -> 1428 at 22:33 -- so the
frame is genuinely worse, not merely measured worse.

## What it is NOT

**Not backlash.** `autofocus.py:868` and `:1090` both use `pos - step`, and
`step` is NEGATIVE because `resolve_sweep` returns a descending sweep. So both
the pre-sweep approach and the final approach come from ABOVE, which is the same
direction the sweep travels. The comments say "from below" and are wrong about
the direction, but the code is right. Fix the comments, not the moves.

**Not a metric mismatch.** The sweep measures through `sweep_metric` ->
`star_size` + `_size_point`; the confirm calls `imaging.stars.focus_size`.
Different call sites, but both return `size.radius` off the same `star_size`,
so on a star-rich frame they are the same number. The only difference is the
`min_stars` default (3 vs MIN_STARS_PER_POINT), which gates None, not the value.

Worth noting anyway: `sweep_metric`'s docstring claims it exists so the metric
"cannot silently differ between the curve and the final verify", and the final
verify does not call it. The claim is currently true by coincidence rather than
by construction. That is the shape that becomes a bug the first time either side
changes.

**Not the guider or the mount.** No guide, mount or camera events appear in the
40-90 s around any confirm frame.

**Not obviously the winner's curse.** The best of N noisy measurements is
biased low, so a fresh measurement at the true optimum should look worse. This
is the most attractive explanation and it predicts the right sign. It was tested
by fitting each sweep and comparing the fitted curve at the best point against
the measured value there: mean predicted bias -0.06 px against a mean observed
penalty an order of magnitude larger, correlation +0.15 over 9 runs. The test is
weak -- sigma is dominated by the far-defocus wings where HFR runs 18-23 px --
so this is "not established", not "excluded". A weighted fit over the inner
points only would settle it.

## What would settle it

Do not fix this by guessing. Add to the confirm frame's log line the three
numbers that discriminate the surviving explanations, then read one night:

- **star count and SNR** -- if SNR falls with HFR it is the sky. Transparency
  swung by 4x in SNR within minutes on this night (`11x noise` at 00:11,
  `40x noise` at 00:02), so this is a live candidate.
- **elapsed seconds since the last sweep point** -- the confirm frame lands
  33-44 s after the sweep ends against 20-27 s between sweep points, because of
  the longer return move. If the penalty scales with the gap it is the sky; if
  it does not, it is mechanical.
- **the focuser position read back from the device** after the move, not the
  commanded one.

## Why it matters

The guard keeps the better position, so focus is not currently wrong. Two real
costs:

1. Every autofocus spends a frame and a move proving something it then discards.
2. When the penalty falls under the 15% threshold the rig **accepts** the worse
   position. That happened at 23:51: 4.46 px kept over a 4.18 px sweep point.

And if the winner's curse turns out to be the cause, the guard is backwards --
it prefers the luckiest grid point over an all-points fit, which is worse focus
on average. That would make `confirmed_best` a fix that needs fixing.
