# Night of 2026-08-18 — NGC 7129, observations and open bugs

Live-run notes. Everything here was seen on the rig tonight, not inferred.

## Fixed and deployed (0.2.96)

- **The sweep discarded a clean V because its outermost point was a donut.**
  Far outside focus the detector sizes a bloated ring smaller than the disc
  further in, the wing test called it "an arm that turns round", and a 3.88px
  minimum from 2539 stars went in the bin. Cost an hour at HFR 5.61 with 3.05
  available. Peel is gated on star starvation (0.55% of the tip's count), not
  geometry, so a genuine second minimum still fails.
- **One failed refocus retired the rule for the night.** `on_hfr_above` is
  edge-triggered and re-arms only when HFR drops back through the threshold; a
  failed sweep leaves HFR high, so the rule never re-arms. Now re-armed on
  failure, bounded to 3.

## Fixed, NOT deployed (waiting for the run to end)

- **The star marker was drawn on top of the star.** Ring radius was 1.6x HFR,
  inside a stretched star's ~2.6x HFR visible disc, with a dark halo
  under-stroke. This is what "doughnut-y stars" was.
- **"Stars saturated" fired on ONE railed pixel.** 169px of 26,108,352 on a good
  frame. Now needs 0.02% of the frame.

## The flow cannot cool the camera, and talks as if it can

The operator expected their flow to cool the sensor. It cannot: **no node and no
param in the flow vocabulary sets a cooling setpoint.** The only source is
`config.cooling.setpoint_c`, injected into the plan by the run route
(`flows/to_plan.py:854`), and it was null — so the night opened at ambient.

What makes this a broken promise rather than a missing feature is that the
vocabulary is full of cooler-shaped controls:

```python
"hold":      {"cooler": "Re-cool + stabilize before capture"}
"parkclose": {"cooler": "Hold cold (day darks)"}
"abort":     {"warm": "Yes"}
```

The operator's own graph carries `warm: Yes`, which only means anything if
something cooled. `nodes.py` even documents the exact disaster in a comment —
"Resuming into that shoots warm subs against a cold dark library, which is
exactly what happened on 2026-08-12 — 63 frames at ambient" — while offering no
way to cool. Tonight it was 35 frames at ~20 C before anyone noticed.

Worked around live 2026-08-18 23:27: cooled via `POST /api/camera/cooler`
(18.1 -> -10.0 C in 4 min, TEC settling at 27%, so -10 holds with headroom) and
persisted `cooling.setpoint_c = -10.0`, which every future run now inherits.

**Two candidate fixes, needs a decision:**

1. *The cheap, high-value one.* Nothing warned that the setpoint was unset. A
   flow that will capture on a cooling-capable camera with no standing setpoint
   should say so at compile time, in the same advisory list as the other losses:
   "this rig has no cooling setpoint, so the night shoots at ambient". That is
   the mechanism that would have caught this before the first frame.
2. *The real one.* A COOLING node (or a setpoint param on CAPTURE/CYCLE) so the
   graph can express the temperature it wants, instead of silently depending on
   a rig-level field the canvas never mentions.

## Autofocus moves to a position its own confirming frame says is worse

Asked to check whether autofocus was as tight as it could be. It is not, and the
sweeps' own data says so. Every sweep takes a confirming exposure at the fitted
vertex. In all three sweeps tonight that frame came back WORSE than a sample the
sweep already had, and it moved there anyway:

| sweep | best sample | moved to (fit) | confirm frame there | logged as |
|-------|-------------|----------------|---------------------|-----------|
| 22:58 | 11177 -> 3.05 | 11167 (-10) | **3.78** (+24%) | "HFR 2.94" |
| 23:18 | 11169 -> 3.24 | 11188 (+19) | **3.43** (+6%)  | "HFR 2.92" |
| 23:39 | 11188 -> 3.01 | 11179 (-9)  | **3.90** (+30%) | "HFR 2.95" |

Three for three. Seeing noise would land better about half the time.

It reaches the subs. Same six filters, two different bases, from the frame
ledger:

    base 11167 (22:58 sweep):  R 3.31  G 3.54  B 3.58  S 3.17  Ha 3.18  Oiii 3.64
    base 11186 (23:18 sweep):  R 2.88  G 2.91  B 2.94  S 2.64  Ha 2.66  Oiii 3.21

Every filter 10-20% better. ~15 minutes of the run was spent at the worse base.
At this train's slope (0.0774 px/step) 20 steps off focus costs ~13% HFR, which
is exactly the size of the effect.

**The reported number is the model, not a measurement.** `native autofocus
complete: position 11167, HFR 2.94 (hyperbolic)` is the fit's y0. The next
exposure at that position measured 3.78. Same class as everything in
`broken-promises-bug-class`.

**Why the fit wanders.** With OUTER_SIZE_FACTOR 8 and 4 points a side, the
innermost samples sit at +-70 steps where HFR is already 2.1x the minimum.
Nothing is sampled between 1.0x and 2.1x, so the vertex is always extrapolated
from the arms, and the three sweeps' arms disagree by more than the fit's
precision. I could not reproduce the observed 21-step spread with a noise model
(photon noise gives 1 step, symmetric seeing jitter 3 even at absurd amplitude),
so I am NOT recommending "more sample points" — that would be a guess.

**Three fixes, in order of confidence:**

1. **Never move to a fitted position that measures worse than a sample already
   in hand.** The confirming exposure is already taken; it just is not used.
   Would have caught all three tonight. No extra exposures.
2. **Log the measured HFR, not the model's y0** — or both. A number nothing
   observed should not be the line the operator reads.
3. **Apply the starvation-gated peel to the FITTER, not just the acceptance
   judge.** Sweep C carried a turned-back outermost point (15.59 below its
   neighbour's 16.32); on an unweighted fit that one point moves the vertex 22
   steps. The production fit is weighted 1/error^2 so the real shift is
   unmeasured — but it is the same defect class as the one already fixed, in the
   path that runs every time instead of only on salvage.

## The mount's reported position drifts degrees away from where it is pointing

**The most serious thing seen tonight.** At 21:16, straight after three
plate-solve syncs, the mount reported the target exactly: 21h42m59s +66d06'47".
By 00:45 it reported 21h32m47s **+62d21'** — 3.75 degrees of declination away —
and it was still moving at ~21 arcsec/minute, monotonically, while tracking a
guided target at 1.1" RMS and never slewing.

**The telescope had not moved.** Two L frames 1h45m apart show the identical
field, same framing, best correlation at a ~1.2' shift (dithering). No field
rotation either, which also rules out a polar error large enough to explain a
real 3.75 degree drift.

The app noticed and shrugged:

    23:27 [solve] field identification cleared: the mount has moved 3.67 deg
                  since the last plate solve

It took "the mount moved 3.67 deg" at face value and discarded its field
identification, rather than treating "a guided, non-slewing mount appears to
have moved 3.67 degrees" as the impossibility it is.

**What it contaminates:**

- Every FITS header from ~21:30 on: `RA`, `DEC`, `OBJCTRA`, `OBJCTDEC`, and the
  derived `OBJCTALT` / `AIRMASS`. Stacking software that trusts headers for
  alignment hints or airmass is being handed a position degrees off.
- The **meridian logic**. `hours_to_flip` counted down to zero on this false
  position at 00:49 and the engine declined the flip — correctly, for reasons
  decided at run start from the TARGET's dec, not from the mount's. The right
  outcome, reached without the position being right.
- The **safety gates** — altitude floor, horizon, no-go box, solar avoidance all
  read mount coordinates.

Not diagnosed. Two candidate causes (driver position cache diverging vs genuine
drift) and the imaging evidence rules against the second. Watching what the
native LX200 driver actually reads off the wire is a daylight job.

**Cheap detector, whatever the cause:** the run already plate-solves. Comparing
a solve against the mount's claimed position costs nothing and turns this from
invisible into a warning. The code that logged "moved 3.67 deg" is the exact
place to raise it.

## Open — not yet fixed

- **`preview_crop` calls itself "sensor-1:1" but crops the PREVIEW array.**
  `entry.linear` is the downscaled preview, so a request for w=420 at x=2900
  returned 224px — the array is 3124 wide against a 6252px sensor. The docstring
  promises a sensor pixel-peep and the endpoint delivers a 2x-binned one. Either
  the name or the array is wrong.
- **The doctor reads `reject: 0` as "reject everything".** Carried from
  2026-08-17. Turning the dial off is punished.
- **Ten "not honoured by a run" warnings block RUN on every flow**, including
  the shipped Examples, and all ten are level `warn`. The dialog is clear and
  RUN ANYWAY works, but a warning that fires for every flow ever authored is a
  warning nobody reads. Several are also overstated: `nodes.condition` says the
  node's settings "do not reach the run" when its trigger and threshold DO —
  only the frame window and `once` are dropped.
- **No dead-man's-switch URL set**, logged at every run start. Operator action.
- **`could not read site from mount`** roughly every 40 min, log-only.
- **`bg_nebula.png` 404s** on the login page.
- **FILTER SLOT NAMES modal has no `.overlay-scrim`.**

## Measured tonight, for the record

- Focuser defocus slope **0.0774-0.0778 px/step**, stable across four sweeps.
  Sweep span is now self-sizing at ±280 (step 70) and every point is measurable.
- Best focus **11167-11196**, HFR 2.94-3.39 hyperbolic. Filter offsets from the
  wheel: L 0, R -18, G 0, B -18, S -18, Ha -15, Oiii +2.
- Frame quality on a 60s B sub: FWHM **3.34px = 3.24"** by half-max crossing,
  elongation 1.20 with scattered angles (R=0.10 — not trailing, not tilt).
- Saturation on the same sub: **169px of 26,108,352**. Exposure is not too long.
- Sky verdict held **clear (200 bright stars, 35-45x noise)** all evening
  against a forecast of 100% cloud from 22:00.
- Cooling: 18.1 -> -10.0 C in ~4 min, TEC power settling at **27%** at -10 C
  against ~16 C ambient. Deeper setpoints are clearly available.
- Frames tonight by sensor temperature: **35 at ambient ~20 C**, 2 mid-cooldown,
  the rest at -10 C. They need separate darks; do not stack them together.
