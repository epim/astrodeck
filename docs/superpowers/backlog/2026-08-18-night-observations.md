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
