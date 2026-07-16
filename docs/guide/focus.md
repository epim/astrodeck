# Focus

Sharp stars start here. The **Focus** view gives you manual focuser control, a
one-tap **autofocus** routine, and a **V-curve** that shows exactly how sharp
each focuser position is. Focus controls need **operator or admin** access
(`control.capture`); without it each panel shows a **Read-only** badge.

**Jargon:** **HFR** = *half-flux radius*, the radius of the circle containing
half a star's light. Lower HFR = tighter, sharper stars. Autofocus works by
finding the focuser position that **minimises HFR**.

---

## The Focuser panel

- **position / max / temp** — the focuser's current step position, its maximum,
  and its temperature (°C) if reported.
- **Manual jog** — a grid of relative moves: `−1000 −100 −10 +10 +100 +1000`
  steps.
- **Go to position** — type an absolute step and press **Go**.
- **Halt** — stop the focuser immediately.

---

## Autofocus

The **Autofocus** panel:

- **Exposure (s)** — per-measurement exposure, default `2`.
- **Step size** — focuser steps between samples, default `350`.
- **◎ Run Autofocus** — starts the routine.

As described in the panel: it *"sweeps 4 steps each side of current position,
measures star HFR, fits the V-curve and drives to its minimum."* So a default run
takes 9 measurements (4 below, current, 4 above), approaching from below first to
take out backlash, and needs at least a few measurable stars to work.

The **Refocus** line shows whether the autofocus is armed to re-run during a
sequence — *"every N frames"* and/or on a temperature drift — or *"manual
only"*. Those cadences are set in the [plan](plan-and-sequences.md), not here.

---

## Reading the V-curve

The **V-Curve · HFR vs Position** panel plots measured **HFR (y)** against
**focuser position (x)**. As you move away from focus in either direction, stars
bloat and HFR climbs — so a good sweep looks like a **V** (or a smooth curve)
with a clear minimum at best focus.

- Each point is one measurement; the fitted **minimum** marks best focus, and
  AstroDeck drives the focuser there.
- A good result has a well-bracketed minimum *inside* the swept range. If the
  minimum falls outside the sweep, AstroDeck tells you to **widen the sweep or
  recentre** — increase the step size or re-centre near focus and rerun.
- If it can't find a minimum (flat/inverted fit, or too few stars), it reports
  the failure and returns the focuser to where it started.

Depending on which engine runs autofocus (see below), the curve is either a
parabola fit or, on the native/NINA path, a real hyperbola with trendlines and
an R² goodness-of-fit.

---

## The verdict

Two verdicts help you judge focus:

- **Live focus verdict** (on the Live Preview here): reads **GOOD / FAIR / POOR**
  with the median HFR in pixels and a trend arrow (*sharper* / *softer*) versus
  the previous frame. It warns on **Few stars** (check focus/clouds or lengthen
  the exposure) and on saturated stars (shorten exposure or lower gain).
- **Autofocus result** (the **Result** panel): after a sweep it shows
  **excellent / good / soft / failed**, the achieved HFR (px and arcsec), the
  fit R² and method when available, and the **best position**. A **provider
  badge** shows which engine ran it.

The GOOD/FAIR/POOR thresholds come from your session's HFR settings, so "good"
means good *for your rig*.

---

## Which engine focuses

Autofocus routing follows the **Autofocus** task provider you pick on the
Equipment **Tasks** panel (see [equipment-and-profiles.md](equipment-and-profiles.md)):

- **AstroDeck native** — a Rust engine measures HFR and fits the curve, driving
  your camera + focuser directly.
- **Backend (NINA)** — delegates to NINA's own autofocus and draws its real
  V-curve.
- **Auto** picks the best available for the connected rig.

---

## Routes

`POST /api/focuser/move` (manual + goto), `POST /api/focuser/autofocus`,
`POST /api/focuser/halt` — all require `control.capture`. Autofocus refuses to
start while a capture loop or sequence is running.

---

## Related

- [Capture](capture.md) — the preview, histogram, and star overlay.
- [Plan & sequences](plan-and-sequences.md) — automatic refocus cadence.
