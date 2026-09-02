# Polish Grab-Bag — Design (2026-07-24)

Slug: `polish-grabbag`

A batch of four small, additive, high-quality UX improvements, each self-contained
and each deferred as a "minor" from a prior spec:

- **(a) Bahtinov LIVE overlay** — NOV-12 shipped the analysis + the text verdict;
  add the spike-line + vertex overlay on the preview canvas.
- **(b) Absolute per-star SNR** — `StarMark` carries no flux; add one additive
  server field `star_flux_median` so the client shows real per-sub SNR.
- **(c) Tonight surface** — a dedicated "Tonight" nav entry hosting the existing
  `TonightPicker`, plus re-surfacing the active target's difficulty while a
  session is running.
- **(d) True time-axis trend charts** — `reportChart.trendGeom` plots x by index;
  the report trends carry `[ts, v]` — plot by real time.

Design only. No implementation, no commit. Everything below is **purely additive**:
old clients ignore new JSON keys, old reports render unchanged, no Rust/native
change, no maturin wheel rebuild.

---

## 0. Shared principles

- **Progressive disclosure everywhere.** Each feature ships a zero-config novice
  default and tucks raw numbers / manual knobs behind a collapsed "Advanced"
  disclosure or a `title`/tooltip. Honest-disabled idiom (§11.8): dim +
  `aria-disabled` + `title`, never native `disabled`.
- **Pure logic in tested lib helpers**, thin untested render shell. Server logic in
  `imaging/*.py`; client logic in `ui/src/lib/*.ts` (tsx-testable, `npx tsx`
  harness — same as `reportChart.test.ts` / `photometry.test.ts`).
- **Never color-alone (§11.1).** New overlays carry a redundant channel (shape /
  number / glyph) alongside any tint, matching `StarOverlay` / `TiltOverlay`.
- **No test explosion.** Parametrize into existing describe/test blocks, reuse the
  `_bahtinov_synth.make_bahtinov` fixture and the existing `test_imaging.py` /
  `reportChart.test.ts` files. Estimated **net new tests: ~7** (see §6).

---

## 1. Feature (a) — Bahtinov live spike + vertex overlay

### 1.1 Current state (the seam)

- `imaging/bahtinov.py:102 bahtinov_offset()` fits 3 spikes, intersects the two
  outer lines at a vertex `v`, and computes the signed central offset. It already
  computes **everything the overlay needs** internally (`lines` list of
  `(nx, ny, rho, phi)`, the vertex `v`, which line is central) but throws the
  geometry away — it returns only `(offset, angles_deg, valid, reason)`.
- `bahtinov.py:183 BahtinovResult.to_dict()` emits `valid/offset_px/in_focus/
  side/direction/angles_deg/tol_px/reason` — **no coordinates**.
- `hub.py:1742-1748` runs `analyze_bahtinov` and attaches `info["bahtinov"] =
  res.to_dict()` on linear subs while armed. `center=(cx, cy)` is already carried
  on `BahtinovResult` (data-space) but dropped by `to_dict`.
- Client: `types.ts:234 BahtinovInfo` + `lib/bahtinov.ts` (verdict text) +
  `components/preview/BahtinovAid.tsx` (the text panel in `FocusView.tsx:157`).
  `PreviewStage.tsx:351-379` is where overlays live inside the shared transform
  (`ClipMaskLayer`, `TiltOverlay`, `StarOverlay`, `Reticle`), each in
  `data`→display space via `displayScale = dispW / data_width` (`PreviewStage.tsx:233`).

### 1.2 Backend change (additive geometry in the payload)

Extend `bahtinov_offset` to also return the geometry it already has, and
`BahtinovResult`/`to_dict` to emit it in **data-space** pixels (the same space
`star_list` uses, so the client reuses `displayScale`).

Precise coordinate derivation (verified against `_radon` at `bahtinov.py:21-37`):

- `_radon` centers on `(cx, cy)` (ROI coords): `rho = (px-cx)·cosφ + (py-cy)·sinφ`.
- So each spike line, **relative to the ROI center**, is `n·(P - center) = rho`
  with `n = (cosφ, sinφ)`. The vertex `v` from `_intersect` (`bahtinov.py:155`) is
  **also center-relative**.
- `analyze_bahtinov` passes ROI-local center `(cx-x0, cy-y0)` but keeps the
  data-space `center=(cx, cy)` on the result. Therefore data-space maps trivially:
  `vertex_data = (cx + v[0], cy + v[1])`; a point on each spike line (foot of
  perpendicular from center) `= (cx + rho·cosφ, cy + rho·sinφ)`.

New optional dataclass field + dict block (both `None`/absent when invalid):

```python
# bahtinov.py — BahtinovResult gains:
geom: dict | None = None   # data-space overlay geometry; None when invalid

# to_dict() adds, only when valid:
"geom": {
    "center": [cx, cy],
    "vertex": [vx, vy],                    # crossing of the two outer spikes
    "spikes": [
        # one per line, data-space point-on-line + DRAW angle (tangent, deg) + role
        {"x": px, "y": py, "angle_deg": tangent_deg, "central": bool},
        ...
    ],
}
```

`angle_deg` is the **drawing** direction (tangent `= φ + 90°`), so the client draws
a full-length line through `(px, py)` at `angle_deg` without re-deriving normals.
`central` flags the middle spike (drawn brighter/thicker). Rounding: coords to 1 dp,
angle to 1 dp — matches `star_marks` compactness.

`bahtinov_offset` returns one extra tuple element `geom` (internal, data-agnostic:
center-relative), and `analyze_bahtinov` lifts it to data space when building the
result. Invalid frames keep `geom=None` (the overlay renders nothing — abstain,
exactly like `TiltOverlay` on a `null` tilt).

**No change to `hub.py`** beyond it already calling `to_dict()` — the new key rides
along for free. Old clients ignore `geom`.

### 1.3 UI change (the overlay + a pure geom lib)

- New pure lib `ui/src/lib/bahtinovOverlay.ts`: `bahtinovSpikeSegments(geom,
  dispW, dispH, displayScale)` → `{ spikes: {x1,y1,x2,y2,central}[], vertex:
  {x,y}, offsetLabel? }`. It scales the data-space points by `displayScale`, and
  for each spike extends the point ±(long enough to cross the box) along
  `angle_deg`, then **clips the infinite line to the display rect** (Liang–Barsky
  or a simple param-clamp — pure math, tsx-tested). Returns display-space segment
  endpoints. This is the only logic; it is fully unit-tested.
- New render shell `components/preview/BahtinovOverlay.tsx` (thin, untested-by-DOM):
  inside the `PreviewStage` svg (a new sibling after `StarOverlay`), draws:
  - the two outer spikes as thin solid `--sky` lines,
  - the central spike as a thicker `--accent` line (brighter = the one you're
    steering),
  - a vertex marker (small crosshair/ring) at the crossing,
  - `in_focus` → the central line + vertex glow `--good` (drop-shadow), the
    unambiguous "locked" moment.
  Every line gets a `--halo` under-stroke (§11.2), `vectorEffect="non-scaling-
  stroke"`, `pointerEvents: none` (passive, like `TiltOverlay`).

### 1.4 UX — progressive disclosure

- **Novice default:** when the Bahtinov aid is armed (`status.bahtinov_active`),
  the overlay draws **automatically** on the preview — no toggle to find. The
  existing `BahtinovAid` text panel ("PERFECT — locked" / "turn IN a little")
  stays as the plain-language headline. The visual is the reward: the two crossing
  spikes + the middle line snapping to the vertex is self-evident.
- **Advanced disclosure:** an optional overlay toggle `overlays.bahtinov` (default
  **on when armed**) so an expert who wants a clean canvas can hide the lines; live
  behind the existing overlay controls, not the novice path. Raw spike angles
  (already in `angles_deg`) surface only in a collapsed "Advanced" line in the
  Bahtinov panel — not on the canvas.
- No new capability gate: the aid arm/disarm already carries `CAP_CONTROL_CAPTURE`;
  the overlay is a pure read of the same event, visible to everyone.

Because the overlay is only present when `bahtinov?.geom` exists (armed + valid +
linear), it never touches the normal capture/focus canvas.

---

## 2. Feature (b) — absolute per-star SNR

### 2.1 Current state (the seam)

- `imaging/stars.py:20 Star.flux` is background-subtracted total ADU (`cut.sum()`
  after border-median subtraction, `stars.py:92-93`). `star_marks` (`stars.py:140`)
  **drops flux** to keep the overlay small; `measure_stars` (`stars.py:365`)
  returns `(hfr, count, marks)`.
- Client already has a persisted **`PhotometryProfile`** (`store.ts:377-379`):
  `egain` (e-/ADU), `readNoiseE`, `biasAdu` — entered once for NOV-4 Suggest / PRO-6.
- `lib/photometry.ts` already has the tested SNR core: `subNoise`,
  `skyElectronsPerSub`, `subSnr`, `stackedSnr`. The frame background median is on
  `preview.stats.median` (`types.ts:247`).
- The selected-star readout (`PreviewStage.tsx:407-412`) shows only HFR (± arcsec).

### 2.2 Backend change (one additive field)

Add a background-subtracted **median flux over the trusted (mid-bright) stars** to
the preview payload. Reuse the exact population `star_marks` trusts, so the value
never comes from noise-floor faint detections.

- `stars.py`: a tiny helper `star_flux_median(stars, *, full_well=None) -> float |
  None` returning `median(flux)` over the same `unsaturated_mid` filter
  `star_marks` uses (`stars.py:168`), or `None` when the population is empty.
  `measure_stars` returns it as a 4th element **or** (leaner, chosen) computes it
  inline and `hub` calls the helper — see Open Decision A.
- `hub.py:1707-1717`: attach `info["star_flux_median"] = <val>` (ADU, 1 dp) next to
  `star_list`. Absent when no trusted star (client shows nothing — honest abstain).
- `types.ts PreviewInfo`: `star_flux_median?: number; // background-subtracted
  median ADU over trusted mid-bright stars`.

Flux is per-**sub** (this frame), not the stack — honest per-sub SNR, matching the
photometry model's "per-sub" framing.

### 2.3 UI change (pure lib + readout)

- New pure fn in `lib/photometry.ts` (co-located with the tested core):
  `perSubSnrFromFlux({ fluxAdu, egain, biasAdu, medianAdu, readNoiseE }) ->
  { snr: number; ok: boolean; reason?: string }`. It composes the existing tested
  primitives: `signalE = (fluxAdu) * egain` (flux already background-subtracted, so
  no bias term on signal); `skyE = skyElectronsPerSub(medianAdu, biasAdu, egain)`;
  `subSnr(signalE, subNoise(skyE, readNoiseE))`. `ok:false` with a plain reason when
  `egain<=0 || readNoiseE<=0` (profile not set) — identical honesty to
  `suggestSubLength` (`photometry.ts:128`).
- Surface: extend the **selected-star readout** and a small always-on "typical
  star" chip.

### 2.4 UX — progressive disclosure

- **Novice default:** a single-line, plain chip on the preview when a photometry
  profile exists: e.g. "Typical star SNR ~38 this sub" (median flux → one number).
  If no profile is set, the chip is **absent** (not an error) — the novice never
  sees SNR machinery they didn't ask for.
- **One obvious tap to enable:** when the profile is unset, the existing Suggest /
  photometry-profile entry point already prompts for egain/read-noise (NOV-4). We
  add a soft hint in the *Advanced* disclosure only: "Add camera gain + read noise
  to see SNR." No new required config.
- **Advanced disclosure:** tapping a star still shows HFR (unchanged); the SNR line
  is appended, and a collapsed "Advanced" reveals the decomposition (signal e-,
  sky e-, read e-, read-noise fraction — all already computable from
  `subNoise().readFraction`). Expert reward: the same numbers SharpCap's Smart
  Histogram shows, on a real detected star.
- Never color-alone: SNR is a number; any tone (good/warn) is secondary.

---

## 3. Feature (c) — Tonight surface + active-session difficulty

### 3.1 Current state (the seam)

- `TonightPicker` (`components/atlas/TonightPicker.tsx`) already exists — a
  self-contained "What can I image tonight?" panel with a Beginner/All segmented
  filter (beginner default ON), difficulty badges (glyph-primary), peak-alt chips.
  It's mounted **only inside** `AtlasView.tsx:118`, one tab deep.
- `lib/difficulty.ts` is the tested pure display core (label/glyph/tone/hint,
  `BEGINNER_TIERS`, `isBeginnerFriendly`) — reuse verbatim.
- `App.tsx:43 NAV` is the canonical ordered nav; Risk-10 rule: "land the order once,
  **append** entries thereafter" (Monitor was appended). `BottomNav` shows 5 primary
  + More; `NavMoreSheet` hosts the overflow (report/help live there).
- Active target while running: `sequence.progress` (`App.tsx:284`) and the run
  banner carry the plan/percent but **not** the target's difficulty. Catalog
  entries carry `difficulty` (`types.ts:430`).

### 3.2 Design — two small additions

**(c1) A dedicated "Tonight" entry.** Append `{ id: "tonight", label: "Tonight",
icon: "atlas"|<new glyph> }` to `NAV` (Risk-10 append precedent — no reorder, no
eviction) and a `VIEWS.tonight` shell. The view is a thin wrapper that **reuses
`TonightPicker`** (same `onPick → openFraming` wiring `AtlasView` uses) plus a short
novice header ("Point-and-shoot targets that are up right now"). Not equipment-gated
(informational shell, like Atlas/Monitor — `App.tsx:87 GATED`). On mobile it lands
in `NavMoreSheet` overflow if the 5-slot primary bar is full (Open Decision C).

**(c2) Re-surface difficulty while a session is active.** When
`sequence.state === running|paused` and the active target has a known tier, show a
small difficulty chip (glyph + label + `title=difficultyHint`) next to the target
name — on the **Monitor** target line and/or the run banner. Pure reuse of
`difficulty.ts`; a tiny selector maps the active target → its `DifficultyTier`
(from the plan/catalog entry already in the sequence state). Abstains (renders
nothing) when the tier is unknown — no fabrication.

### 3.3 UX — progressive disclosure

- **Novice default:** "Tonight" is a first-class, obvious destination — a beginner
  opens the app, taps Tonight, sees Easy/Moderate picks ranked by altitude, taps one,
  and it frames. Beginner filter defaults ON (already). Zero config.
- **Advanced disclosure:** the "All" segment (already there) unlocks Hard targets;
  surface-brightness numbers live in the badge `title` (already). The active-session
  chip's `title` gives the plain-language hint; experts who know the object ignore it.
- Honest-disabled: none needed — nothing is capability-gated here.

Keeping this lean: **no new backend** (`/api/catalog/tonight` already serves it),
no new component (reuse `TonightPicker`), minimal App wiring.

---

## 4. Feature (d) — true time-axis trend charts

### 4.1 Current state (the seam)

- `lib/reportChart.ts:18 trendGeom(values: number[], w, h, padY)` plots **x by
  index** (`toX = i/(n-1)*w`). The comment concedes "a true time axis is later
  polish".
- Server `report.py:438 trends()` returns `{hfr:[[ts,v]], temp, rms}` — real
  timestamps, independently downsampled per series (`_downsample` at
  `report.py:464`). So HFR (light frames only) and temp (every frame) have
  **different, non-uniform** time spacing — index plotting distorts them.
- `components/graphs.tsx:256 TrendLine({ values })` maps `p[1]` and calls
  `trendGeom`. `ReportView.tsx:291-293` passes `report.trends.hfr.map(p=>p[1])`.
- The live in-acquisition strip also uses `trendGeom` via `pickSeries` (index-based,
  `reportChart.ts:44`) — that path is genuinely index-native and should stay.

### 4.2 Change — additive time-aware geometry

Add a sibling to `trendGeom` (do **not** break the index path the live strip relies
on):

```ts
export interface TrendPoint { t: number; v: number; }
/** Polyline with x scaled by REAL time (t-t0)/(t1-t0). Degenerate single-t
 *  (all same timestamp) falls back to even index spacing. */
export function trendGeomTimed(points: TrendPoint[], w, h, padY=0.1): TrendGeom | null
```

Implementation mirrors `trendGeom` for the y-axis (auto-scale to v min/max with
`padY`), but `toX(t) = ((t - t0)/(t1 - t0)) * w` with a guard: when `t1==t0`
(one point, or all identical ts) fall back to index spacing so a 1-point series or a
clock glitch never divides by zero or collapses to x=0.

`TrendLine` gains an **optional** `times?: number[]` prop (parallel to `values`).
When present it builds `TrendPoint[]` and uses `trendGeomTimed`; when absent it
keeps `trendGeom(values)` — the live strip is untouched. `ReportView` passes both
arrays: `values={t.map(p=>p[1])} times={t.map(p=>p[0])}`.

### 4.3 UX — progressive disclosure

- **Novice default:** the report trends simply become *correct* — a gap in guiding
  (paused an hour) now shows as a flat stretch, not a compressed blip. No new
  control, no new copy. The chart is already labelled with min/max (`TrendLine`).
- **Advanced disclosure (optional, Open Decision D):** faint x-axis end labels
  showing elapsed time / clock at the start and end (reusing `fmtDuration` /
  `toLocaleTimeString`, both already imported in `ReportView`). Kept minimal — a
  small `text-dim` caption under the line, off the novice's critical path. Recommend
  shipping the labels since the data is now truthfully positioned.

Non-uniform gaps between HFR (lights) and temp (all frames) are exactly why this
matters — with a real axis the three panels line up in wall-clock time.

---

## 5. Files touched (summary)

**Backend (Python, additive only):**
- `server/astrodeck/imaging/bahtinov.py` — `bahtinov_offset` returns geom;
  `BahtinovResult.geom` + `to_dict()["geom"]`; data-space lift in `analyze_bahtinov`.
- `server/astrodeck/imaging/stars.py` — `star_flux_median()` helper (trusted-star
  median flux).
- `server/astrodeck/hub.py:1707-1717` — attach `star_flux_median` to preview info
  (bahtinov geom rides `to_dict()` free at `:1748`).

**UI (additive):**
- `ui/src/lib/bahtinovOverlay.ts` (new, pure) + `components/preview/BahtinovOverlay.tsx`
  (new shell) + wire into `PreviewStage.tsx` svg (~line 377) + `OverlayToggles.bahtinov`.
- `ui/src/lib/photometry.ts` — `perSubSnrFromFlux()` (new pure fn); readout in
  `PreviewStage.tsx` selected-star chip + a typical-SNR chip.
- `ui/src/lib/reportChart.ts` — `trendGeomTimed()` (new); `graphs.tsx TrendLine`
  gains optional `times`; `ReportView.tsx` passes timestamps.
- `ui/src/App.tsx` NAV + VIEWS `tonight` (reuses `TonightPicker`); active-session
  difficulty chip in `MonitorView`/run banner via `difficulty.ts`.
- `ui/src/types.ts` — `BahtinovInfo.geom?`, `PreviewInfo.star_flux_median?`,
  `OverlayToggles.bahtinov`.

---

## 6. LEAN test plan (estimated net-new: ~7)

Reuse fixtures and existing files; parametrize; pure logic only.

1. **`test_bahtinov.py`** (reuse `_bahtinov_synth.make_bahtinov`): extend an
   existing valid-frame test to also assert `to_dict()["geom"]` — vertex near ROI
   center in **data space**, 3 spikes with exactly one `central: true`, and the
   central point offset sign matches `offset_px`. **+1 test** (parametrized over
   `central_shift ∈ {0, +2}` reusing the existing param values).

2. **`test_imaging.py`** (reuse existing star fixtures): assert `star_flux_median`
   equals `median` of trusted-star flux and is `None` when only saturated/faint
   stars exist. **+1 test** (parametrized: populated vs empty-trusted).

3. **`reportChart.test.ts`**: `trendGeomTimed` — proportional x for non-uniform
   timestamps, single-point/identical-ts fallback, empty→null. **+1 test**
   (parametrized cases in one `it`).

4. **`photometry.test.ts`**: `perSubSnrFromFlux` — correct SNR from known
   flux/egain/sky/read, and `ok:false` when profile unset. **+1 test**
   (parametrized: valid / no-egain / no-readnoise).

5. **New `bahtinovOverlay.test.ts`**: `bahtinovSpikeSegments` — a spike at a known
   angle clips to the box with correct endpoints; vertex scales by `displayScale`;
   `geom=null`→empty. **+1–2 tests** (parametrized angles).

6. **Difficulty active-session selector** — the plain mapping (active target →
   tier) is a one-liner reusing already-tested `difficulty.ts`; cover with **+1**
   small parametrized test only if the selector holds real branching (known /
   unknown / running-vs-idle). Otherwise fold into an existing store test → **+0–1**.

**Explicitly NOT adding:** DOM/render tests for the new shells (`BahtinovOverlay`,
the Tonight view, the SNR chip) — they are thin passthroughs over tested lib fns,
per the test-discipline rule. `TrendLine`'s `times` branch is covered via
`trendGeomTimed`. Total **≈7 net-new**, within the ~1901-suite budget.

---

## 7. Open decisions (each with a recommendation)

- **A. `star_flux_median` plumbing — 4th return value of `measure_stars` vs a
  standalone helper `hub` calls.** *Recommend the standalone `star_flux_median()`
  helper* called from `hub` alongside `measure_stars`. Keeps `measure_stars`'
  3-tuple contract (used by cloud detection + tests) byte-stable; the helper is
  independently testable and adds no churn to existing call sites.

- **B. Bahtinov overlay toggle default.** *Recommend `overlays.bahtinov` defaults
  ON while armed* (novice sees the reward automatically) with an expert opt-out in
  overlay controls. Alternative (always-on, no toggle) is simpler but caps the
  expert who wants a clean canvas — rejected.

- **C. "Tonight" on mobile — primary bar vs More sheet.** *Recommend appending to
  the `NavMoreSheet` overflow on mobile* (the 5-slot primary bar is full and
  reordering violates Risk-10) while showing it in the desktop left rail. Keeps the
  IA promise ("append, don't reorder"). If product wants it primary, that's a
  separate IA decision, not this batch.

- **D. Time-axis end labels.** *Recommend shipping the faint start/end
  time captions* now that x is truthfully positioned — small `text-dim`, reusing
  `ReportView`'s already-imported `fmtDuration`/`toLocaleTimeString`. Cheap, and
  without it the corrected spacing is subtle. Kept off the novice's critical path.

- **E. SNR chip placement — always-on "typical star" chip vs selected-star only.**
  *Recommend both, gated on a set photometry profile*: a passive typical-SNR chip
  (median flux) for at-a-glance, plus the per-star SNR on tap. Both vanish when no
  profile — zero novice clutter. If one must be cut, keep the per-star (on-tap) one.

---

## 8. Risks

- **Bahtinov coordinate-space bug.** The rho/vertex are center-relative; a wrong
  sign or a missed `+center` puts the lines off the star. *Mitigation:* the pure
  `to_dict()["geom"]` test (§6.1) pins vertex-near-center + central-offset-sign in
  data space against the synthetic fixture; the overlay lib is separately tested.
- **Overlay clutter on a dense field.** The 3 spikes over `StarOverlay` could be
  busy. *Mitigation:* overlay only present when armed (a deliberate focus mode,
  typically pointed at one bright star), passive `pointer-events:none`, expert
  opt-out (Decision B).
- **SNR misread as stacked.** Users may think the number is the final-image SNR.
  *Mitigation:* copy says "this sub"; the model is explicitly per-sub. Advanced
  disclosure shows the decomposition so the number is auditable.
- **Flux without a bias/linearity caveat.** `Star.flux` is background-subtracted
  ADU on a possibly-nonlinear/OSC frame; SNR is an estimate, not photometry.
  *Mitigation:* the module docstring already says "not a photometry tool"; keep the
  copy as "~" estimate, never a precise claim.
- **Time-axis divide-by-zero / single point.** *Mitigation:* `t1==t0` index
  fallback, tested. Live-strip index path left entirely untouched.
- **Nav append regressions.** Adding a NAV entry touches gating maps + mobile
  overflow. *Mitigation:* not equipment-gated (informational shell precedent);
  reuse existing `TonightPicker` so the view body is proven.

---

## 9. Rust / wheel

**None needed.** All four features are pure Python (`imaging/*.py` numpy, additive
JSON keys) + TSX (`lib/*.ts` pure fns + thin shells). No `astrodeck-native` Rust
change, no maturin rebuild, no wheel bump. A Python-only v1 **is** the whole thing —
there is no native surface here.
