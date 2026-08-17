# Autofocus: a measured sweep span, and a pipelined sweep

2026-08-17. Two independent changes to the same loop, both aimed at the same
thing: a sweep that finishes sooner and lands more often.

## Where this comes from

#219 closed with one item deliberately left open:

> the sweep still spans +/-1400 (`steps_each_side=4` x 350) when +/-350 measured
> beautifully. Narrowing it wants the focuser's critical focus zone, which has
> never been measured.

And a second, separate observation: every point of a sweep is
`move -> expose -> measure`, and the camera sits idle for the whole measure.
On a 26 MP frame `detect_and_measure` plus `focus_size` costs 3-6 s against a
2 s exposure, so roughly half the wall-clock of a sweep is spent with the
shutter shut and one core busy.

## A. The span is a measurement, not a constant

### The physics we already rely on

`imaging/defocus.py::focus_from_two` states it: a defocused star's diameter
grows LINEARLY with distance from focus, "with no knowledge of the aperture,
the f-ratio or the step size". A V-curve is the hyperbola

    size(d) = sqrt(y0^2 + (m*d)^2)

where `y0` is the in-focus size and `m` the asymptotic slope in px per focuser
step. `m` is a property of the optical train and the focuser's step size. It is
constant for a rig, and it is exactly the number that decides how wide a sweep
has to be.

We cannot compute it: `Optics` carries `focal_length_mm` but no aperture, so
there is no f-ratio and therefore no CFZ formula
(`2.44 * f^2 * 0.55` um, per the Hocus Focus dossier). We do not need to. **The
sweep measures it every time it succeeds.**

Last night's run, inverted through the hyperbola against `y0 = 2.96`:

| offset from focus | measured | implied m |
|---|---|---|
| +/-350 | 27.38 / 27.06 | 0.0777 / 0.0768 |
| +1050 | 84.89 | 0.0808 |

Three independent points agreeing to 5% - the model holds on this rig.

### Sizing the sweep from it

Pick what the OUTERMOST point should read, as a multiple of the in-focus size,
and the geometry falls out:

    half_span = y0 * sqrt(F^2 - 1) / m
    step      = half_span / steps_each_side

With `F = 8` and last night's numbers: `half_span = 2.96 * 7.94 / 0.078 = 301`
steps, `step = 75`. Against the shipped 350 that is a **4.6x narrower sweep**,
and it lands the outer point at ~24 px instead of 85 px - well inside what the
detector measures confidently, and still 8x the tip.

`F = 8` is the one judgement call. Constraints on it, from code that already
exists:

* the engine's flat-tip band is 0.1 px, so the INNERMOST point must clear it -
  at `F = 8` the first point off centre reads `sqrt(y0^2 + (m*step)^2)` =
  6.6 px against a 2.96 px tip, clearing it by 35x;
* `curve_verdict` wants both wings >= 1.5x the minimum - cleared by 5x;
* the wings must stay measurable, which is what #219 was about, and 24 px is a
  ring the detector now measures to 2%.

The half-span is **binning-invariant**: `y0` and `m` both scale as `1/bin`, so
their ratio does not. Nothing has to be normalised.

### Storage and failure behaviour

`focus_calibration.json` in the config dir, keyed like `focuser_state.json`
(a separate file deliberately: `save_focuser_position` REPLACES its entry on
every completed move, so anything sharing that entry would be wiped).

* Written only by a sweep that SUCCEEDED and produced enough arm points, on
  both sides. A one-armed slope is what a sweep that never bracketed focus
  produces, and believing it would narrow the next sweep away from focus.
* Read when a caller passes `step=None`, which becomes the default for
  `run_autofocus`, the sequence engine, and the API body. An explicit step
  from the Advanced panel still wins.
* **Discarded** when a sweep fails with `not_enough_spread` or a flat curve.
  That is the one failure a too-narrow span produces, and discarding sends the
  next attempt back to the field-proven 350. Without this a single bad
  calibration could wedge focus for a whole night.
* Clamped to `[25, 1500]` steps and to the focuser's travel, and **never wider
  than the range the measuring sweep actually covered**. That last clamp was
  not in the first draft and the benchmark put it there: the simulator's gentle
  synthetic defocus reads 0.0033 px/step and asked for ±5712 off a ±1400 sweep.
  A shallow slope legitimately wants a wider sweep — but an *under-measured
  wing* produces a shallow slope too, and then the next sweep is wider, its
  wings further out, its measurement worse. Capping at what worked stops the
  ratchet, and is also the honest reading of a slope measured only over the
  range it sampled.

## B. The sweep pipelines

`sweep.rs` says the door is open:

> Pipelined move-while-analyzing timing is a host concern; the machine
> preserves the measurement positions and their order exactly.

The engine will not tell us the next position until the current measurement is
added, so pipelining means PREDICTING it and checking the prediction.

The prediction is one line. `setup_initial_sweep` walks
`start + offset*step` DOWN to `start`, and `decide_extension` then continues
down from `points.first().position - step`. Both phases descend by exactly one
step. So: **the next position is the current one minus `step`** - and every
emitted position verifies it.

    expose(pos) -> start speculative move+expose(pos - step)
                -> measure(pos) on a thread, concurrently
                -> add_measurement, next()
                -> engine says move_to(p):  p == predicted ? use the frame : discard

Correctness does not depend on the prediction being right: a frame is only ever
used for the position the engine actually asked for. What the prediction buys
is time, and what a miss costs is one exposure. Two rules bound that cost:

1. **One strike.** After the first miss, speculation is off for the rest of the
   run. Worst case per run: one wasted exposure.
2. **Stop before validation.** After `2*offset` measurements the engine is
   about to fit and move to the validation point - which is UP, never
   `pos - step`. Stopping there removes the one miss that would otherwise be
   guaranteed on every successful run.

A speculative task owns the focuser and the camera while it runs, so every
other path (failure returns, the leash, the `except` restore) must settle it
first. It is AWAITED, never cancelled: cancelling a download mid-flight is how
a camera ends up in a state the next exposure inherits.

The legacy numpy path pipelines too, and there it is unconditional - its
positions are a plain list computed up front, so there is nothing to predict.

## Measured effect

On the simulator, with the measurement faked at 0.35 s against a 0.2 s
exposure (the ratio is what matters; the sim's frames are far smaller than the
rig's):

| | wall | frames |
|---|---|---|
| shipped ±1400, serial | 12.69 s | 12 |
| shipped ±1400, pipelined | 9.89 s | 12 |
| measured span, pipelined | 9.03 s | 11 |

**Pipelining alone: 22% faster. Both together: 29%.**

The rig should do better on both counts, and this is an expectation rather than
a measurement: its measure/expose ratio is larger (3-6 s of detection against
2 s of shutter, so more to hide), and its span narrows 4.6× rather than 1.3×,
which takes real focuser travel out of every point as well as the two extension
points the sweep spent walking off the measurable end.

Neither change alters what the fit is given: the same positions, in the same
order, measured the same way.

## What the build changed about the plan

* **The measurement had to move onto the frame.** Six focus tests substituted
  the metric and read `rig.focuser_pos` inside it. That worked only because the
  focuser stood still during the measurement, which is exactly what pipelining
  ends. `CameraFrame.focuser_position` (sim-only) and both metric seams taking
  the frame make a double able to say *which point it is looking at* — the
  live-device read was a test double that could pass while the code under test
  was wrong.
* **The widening cap** (above), which the benchmark found and the plan missed.
* **A session-scoped config dir.** `focus_calibration.json` is the first file
  tests write as a *side effect*, so every successful sim sweep left one behind
  for the next test in that xdist worker. An autouse unlink in `conftest.py`
  closes it.
