# The autofocus confirm frame measures worse than the sweep, and I do not know why

**Status:** EXPLAINED, on the full night's data. It is the winner's curse, and
the guard is therefore backwards. See the section at the bottom, which
supersedes the "not obviously the winner's curse" paragraph above it — that
conclusion was drawn from nine runs and a bad estimator.

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

---

## RESOLVED the next morning, on 23 runs instead of 9

The overnight log carried 23 autofocus runs, not the 9 I had at 00:30. On the
full set:

- **21 of 23 confirm frames were worse, 2 were better.** Median +18.7%, mean
  +18.3%. Not 100% one-directional, which is what a purely mechanical cause
  (backlash, settling, a longer return move) would have to produce. Both
  exceptions fell in the last hour, when the sky was steadiest.

- **The 21:37 run measures the noise floor directly.** Its best sweep point and
  its confirm frame are at *the same focuser position*, 11206, and read
  **2.81 px and 3.67 px**. Identical position, no move between them beyond the
  approach, 30.6% apart. Nothing about focus can explain that; it is what one
  measurement of this quantity costs, and it puts the per-point noise near
  0.86 px on a ~3.2 px reading.

That settles it. The sweep's best point is the **minimum of ten noisy draws** and
is therefore biased low by roughly 1.5 sigma, while the confirm frame is a single
unbiased draw at the fitted optimum. At the sigma the 21:37 pair implies, that
bias is 0.6-1.3 px — and the observed mean penalty is +18.3% of ~3.4 px, about
0.6 px. The numbers line up without needing any mechanism at all.

My earlier attempt to test this predicted a bias of only -0.06 px and I recorded
it as "not established". The estimator was wrong, not the hypothesis: I fitted
sigma across the whole sweep, where the far-defocus wings run 18-28 px and
dominate the residual, instead of across the near-focus points that actually
determine the minimum.

**So `confirmed_best` is backwards.** It compares a selection-biased number
against an unbiased one and, unsurprisingly, prefers the biased one — on 21 of
23 runs it discarded the all-points fit in favour of whichever grid point got
lucky. The fit uses ten points and interpolates between a 79-step grid; the
lucky point is constrained to the grid and chosen *because* it read low. The
guard is not protecting focus, it is degrading it slightly and spending a frame
and a move per autofocus to do so.

**The fix is to make the comparison fair, not to widen the threshold.** Widening
it just moves the arbitrary line. Two honest options:

1. Re-measure the best sweep point at confirm time and compare two fresh draws.
   Costs one extra frame; removes the bias entirely.
2. Keep one confirm frame but compare it against the *fitted curve's value* at
   the best sweep point rather than against the measured value there. The curve
   is the de-biased estimate and it is already computed.

Option 2 is free. Either way the guard should then only fire on a genuine fit
failure, which is what it was for — and those exist: the 23:51 run fitted a
position on a mount that had stopped tracking, and no threshold based on noise
should be expected to catch that. Defect 1 in
`2026-08-21-tracking-stopped-and-nothing-noticed.md` is the right guard for it.
