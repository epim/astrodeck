# Focus pod + polar zoom ladder — design

Two UI features requested 2026-08-03. Grounded against the real components on
that date; every file:line below was read, not inferred.

- **#125** — a speed dial over the Focus preview giving capture + focuser reach
- **#128** — an animated 30′→10′ zoom on the polar reticle

They ship together because both are "the control and the thing it changes must
be on screen at the same time".

---

## #125 — the Focus pod

### The problem, stated precisely

Everything this feature needs **already exists** on the Focus screen: exposure
presets `[1,2,3,5,10]` (`lib/focusCapture.ts`), Single/Loop/Stop, the
1/10/100/1000 `StepDial`, live HFR with a previous-frame trend caret, and an
exposure-progress narrator. The user's complaint was never "these controls are
missing" — it is that on a phone they sit **below the image they change**, so
you cannot see a frame and adjust focus in the same glance.

So this is a **reach** problem, not a capability gap.

### Shape

A single 56px disc over the lower-right of the preview. Collapsed it is not a
bare button — it carries the two numbers you stare at while focusing:

```
  ┌──────────────┐
  │              │        collapsed:  ◜◝  ring = exposure progress
  │   preview    │                   ( 4.2▼ )  HFR + trend vs last frame
  │              │                    ◟◞
  └──────────────┘

  expanded (quarter arc, staggered 30ms along the radius):

              (AF)
         (∞)         IN ◀──┐
   (SHOOT 3s)              ●
                    OUT ▶──┘
```

- **Five arc chips**: SHOOT, LOOP, IN, OUT, AF. 48px minimum.
- **Two cycling badges, not menus.** Exposure rides on SHOOT (`3s` → tap →
  `5s`); step size is shared by IN/OUT. One target, current value always
  visible, nothing to open one-handed in the dark.
- Radial scrim behind the arc only — chips must read over a star field without
  dimming the frame being judged.
- Night mode: red channel only, and the collapsed ring's state must be legible
  by **weight and dash**, not hue (house rule).

### Decisions (these were open questions — do not re-litigate)

**It is a SHORTCUT, not a second implementation.** The pod calls the exact same
handlers the panels call and renders the same state. The codebase removed a
duplicate control once before (`FocusView.tsx:951-954`) and that lesson stands:
what was removed then was a *second control with its own behaviour*. A shortcut
that dispatches the identical action is not that. The sidebar panels stay —
they hold what the pod deliberately omits (absolute position entry, the AF
curve, filter, binning).

**It must route through the existing blocker chain, not the banners.** The
`#122` "capture in progress" warnings at `FocusView.tsx:480-503` are advisory
`role="status"` panels with no proceed path. The actual refusal is
`focusCaptureBlocker → captureReason → singleReason` (`FocusView.tsx:384-390`),
which returns a **sentence**. A blocked pod chip renders as `LockedChip` with
that sentence — dim + lock glyph + tap-to-open tooltip — never native
`disabled`. This is the house honest-disabled pattern.

**The pod is a SIBLING of the preview stage, never a descendant.** Gestures are
attached imperatively to the stage root, and the `[data-no-pan]` escape hatch is
checked **only** in `onPointerDown` (`usePreviewGestures.ts:112-113`). `onWheel`
calls `preventDefault()` unconditionally and `onDblClick` has no check, so a
control placed inside the stage would have a scroll wheel zoom the image and a
double-tap toggle Fit/100%. `PreviewStage` takes no `children` prop anyway. The
route is a new `relative` wrapper in `FocusView` holding stage and pod as
siblings.

**The pod must set `touchAction: "none"` itself.** The stage sets it; an overlay
sibling defaults to `auto`. Since the step control is a vertical press-and-slide,
the page would scroll under the thumb. Every other vertical-drag control in this
repo sets it inline (`SlewPad.tsx:314`, `TouchGuard.tsx:166`,
`StretchHistogram.tsx:232`, and four more) — `StepDial`'s own root does **not**,
which is a pre-existing gap that gets worse over the preview. Fix it there too.

### Accessibility

Real `<button>` per chip. The arc is a `role="menu"`; Enter opens, arrows
traverse, Escape closes. `prefers-reduced-motion` drops the bloom to a fade —
the file already has one reduced-motion block (`index.css:753`) to follow.

---

## #128 — the polar zoom ladder

### What already exists

`polar.tsx` is a hand-rolled SVG, fully token-driven, no canvas or library. It
**already has an adaptive scale**: `_CEIL_LADDER = [10,15,20,30,50,75,100,150,
200,300]` and `boundaryArcmin(total)` returns the first rung ≥ `total * 1.08`
(`polar.tsx:66-71`). So this task is not "add a zoom" — it is fixing three
specific defects in a zoom that is already there.

### The three defects

1. **Zoom-out only.** The smallest rung is 10, so the reticle can never zoom in
   past the 10′ ring. The 3′ and 1′ half of the request does not exist.
2. **No hysteresis.** `want = total * 1.08` picks rung 10 only while
   `total ≤ 9.259′`. A live reading oscillating across 9.26′ flips 10↔15 on
   every update and `k` jumps 16.5→11.0 px/arcmin, moving every ring and the
   dot. Worse, `total` here is the **eased point's** magnitude, not the server's
   `total_error`, so a rung can step mid-tween.
3. **The rescale is instantaneous.** `useEasedPoint` tweens the point over 600ms,
   but `k` and every ring radius are plain render values with no transition, so
   a ladder step snaps.

Defect 3 is the one that matters most, and it is worth stating why: **when the
scale changes silently, a shrinking error can look like a growing one.** Zoom in
and the marker's pixel distance from centre *increases* even though you just
improved the alignment. A snap therefore reads as backwards progress. The
animation is not decoration; it is what keeps the display honest.

### Decisions (these were open questions)

**Rings are derived from the current rung, not fixed.** The existing comments
(`polar.tsx:63-65, 146-147`) argue for pinning the 2′/10′ rings so "2′" is not a
moving target. That decision is being **deliberately changed** — the user asked
for the zoom, and below 3′ the 10′ ring leaves the viewBox entirely. What made
the old decision good is preserved differently: **every ring carries its arcmin
label**, so the scale is never ambiguous even as it moves. Re-derive the whole
rings/labels block (`:158-162, :202-208, :215-222`) rather than extending it —
note `{r: 2, boundary: smax === 2}` is currently a dead branch that starts
mattering the moment sub-10 rungs exist.

**Instruction copy gets its own thresholds; `polarTier` is untouched.**
`polarTier`'s 2/10 boundary answers *"how good is my alignment"* and feeds
verdict strings that are tested. The new copy answers a different question —
*"what do I turn next"* — and needs its own boundaries:

| total error | instruction |
|---|---|
| > 30′ | Rotate the tripod, or reposition the pier. |
| 10′–30′ | Use the azimuth and altitude bolts. |
| 1′–10′ | Fine bolt adjustment. |
| < 1′ | Good enough for most imaging. |

Two independent scales is correct here, not an inconsistency to reconcile.

**Announce the rung.** A brief scale label pulse on change, so the user reads
the rescale as a rescale. Silent is the failure mode.

### Out of scope

The pole-guard refusal (~450 chars) currently renders **twice** — once in the
alert banner and again under the reticle (`PolarView.tsx:122`). Real defect,
separate item; do not fold it in.

---

## Testing

`ui/run-tests.mjs` (`npm test`, not vitest — 108 files / 1184 assertions).
jsdom has no layout, so assert behaviour and contracts, not geometry:

- `boundaryArcmin` is module-private and untested today — export and test it,
  including the hysteresis band from both directions.
- The pod's blocked state must render the blocker **sentence**, not a boolean.
- Per-tier instruction copy: one test per boundary, asserting the exact string.
- Anything geometric needs a live measurement, not a unit test.
