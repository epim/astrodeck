# Atlas Interaction Quality (Wave 2) — Design

**Requirements source:** `docs/superpowers/reviews/2026-07-12-atlas-plan-ux-review.md`
(commit 5eaa131), Wave 2 items + design decisions 1-2 (user-approved 2026-07-12).
Builds on Wave 1 (shipped `78c0ec0..d719093`) — SkyCanvas now has the fetch-loader/
transform-pan pipeline; AtlasView has merged optics + narrow selectors.

**Goal:** Make the framing box directly manipulable (a real rotation handle ON the box),
let the user find targets without leaving the Atlas (header catalog search), stop lying
on the recenter button in free-roam, and make the dead `activePanel` mosaic highlight
earn its keep or leave.

**Non-goals:** anything Plan/scheduling (Wave 3); new rotation *semantics* (the
`setFraming({rotation_deg})` funnel, stepper, ±5° buttons, and `[`/`]` keys all stay);
Mount/Plan searches (they keep their own).

---

## 1. Stalk rotation handle on the framing box

Replaces the invisible top-of-canvas knob-zone heuristic (`SkyCanvas` `onPointerDown`
`py < rect.height*0.14 && |px−w/2| < w*0.22`) and the fixed knob drawn at the canvas
top. PowerPoint/Figma-style: the handle is part of the box and rotates with it.

- **Drawing (FovOverlay):** inside the box's rotated `<g>`, a stalk protrudes from the
  midpoint of the box's TOP edge: a line from `(0, −frameH/2)` up to
  `(0, −frameH/2 − STALK_LEN)` and a circle (r ≈ 10 viewBox units) at its tip, using
  the same `.svg-halo` + `var(--accent)` stroke idiom as the box. Drawn only when
  `haveOptics` (no handle on the dashed placeholder frame). STALK_LEN ≈ 34 viewBox
  units (matches the old knob's stalk).
- **Hit-testing (real element, not a zone):** SkyCanvas's SVG layer keeps
  `pointer-events-none`, but the handle group re-enables itself
  (`pointer-events: all`) and carries `data-role="rotate-handle"`. A larger invisible
  hit circle (r = 64 viewBox units, `fill="transparent"`, `stroke="none"`) sits under
  the visible tip so the touch target stays ≳44 CSS px on a 360 px canvas. SkyCanvas's
  parent `onPointerDown` decides mode by
  `(e.target as Element).closest?.('[data-role="rotate-handle"]')` → `"rotate"`, else
  `"pan"` — correct at any rotation angle, any zoom, with zero duplicated geometry math.
- **Rotate math unchanged:** pointer angle about the canvas center (the box center IS
  the canvas center), same delta-angle mapping as today; still funnels into
  `onRotate` → `setFraming({rotation_deg})`.
- **Affordance:** the handle group sets `cursor: grab`; while a rotate-drag is active
  the parent container switches to `cursor: grabbing` (it already does for rotate
  mode). The old fixed-knob SVG block in SkyCanvas is deleted.
- **A11y:** unchanged — `[`/`]` keys remain the keyboard path (already announced in the
  canvas aria-label); the handle is a pointer affordance, `aria-hidden` like the rest
  of the SVG layer.

## 2. Catalog search on the Atlas header

- Search input in the AtlasView header row, styled like Plan's search
  (`SequenceView.tsx` pattern): 250 ms debounce, `GET /api/catalog?q=`, top 6 results
  in a dropdown listing name/type, keyboard navigable (the same markup/roles Plan's
  search uses — transcribe its idiom).
- **Picking a result:**
  - If a framing session is active: `setFraming({ target: entry, center:
    { ra_hours: entry.ra_hours, dec_deg: entry.dec_deg } })` — keeps the user's survey,
    zoom, rotation, and mosaic setup; swaps what is being framed and recenters on it.
  - If no session (Atlas empty state): `openFraming(entry)` — seeds a fresh session
    exactly as the Mount "Frame" button does. The empty state gains the search box so
    the "go to Mount catalog first" dead-end dies.
- The empty-state hint copy is updated to mention searching right here.
- No new server work: `GET /api/catalog` already serves Plan and Mount.

## 3. Context-correct recenter label

- `SurveyControls` gains a `hasTarget: boolean` prop; the recenter `IconButton` label
  becomes `hasTarget ? "Recenter on target" : "Recenter on mount"`. AtlasView passes
  `hasTarget={!!target}`. Behavior (`recenter()`) is unchanged — the label finally
  matches what it does in free-roam.

## 4. `activePanel` — expose (if a panel list exists) or delete

- Wave-1 seams confirmed `FovOverlay.activeIndex` is fully implemented (thicker stroke
  + corner ticks on one panel) but never fed: `AtlasView` renders the SkyCanvas without
  `activePanel`, and per-panel UI in AtlasView renders panel readouts (the `adjustedPa`
  block).
- **Decision rule (seam-confirmed during planning):** if AtlasView's mosaic cluster
  renders a per-panel list/readout, add hover/focus wiring: pointing at a panel row
  sets `activePanel` state (plain `useState<number | null>`), passed through SkyCanvas
  → FovOverlay `activeIndex`; leaving clears it. Touch: tapping a row toggles the
  highlight. If there is NO per-panel row UI to hang this on, delete `activeIndex`
  from FovOverlay and `activePanel` from SkyCanvasProps instead — no dead contract
  survives the wave either way.

## 5. Testing

- Pure logic in this wave is thin (it's interaction wiring); the testable seams:
  - No new pure modules expected. If the handle hit-test or search-pick logic grows a
    pure helper, it gets an `npx tsx` assert file per repo convention.
- Gates per task: `npm run build` from `ui/`; the three Wave-1 tsx test files re-run
  green as regression (surveyView 6/6, tooltipMachine 7/7, missingOptics 4/4).
- Manual smoke items recorded for the user (rotation-by-handle feel; search → frame →
  send-to-plan flow) — pointer-drag interactions have no automated harness here.

## 6. Compatibility

- No API changes, no server changes, no type changes visible outside `ui/src`.
- `SkyCanvasProps` changes: `activePanel` either becomes live or is removed;
  everything else additive.
