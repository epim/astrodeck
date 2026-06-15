# AstroDeck — Live Preview Overhaul — Build-Ready Spec

**Date:** 2026-06-15
**Status:** Final (revised after 3 adversarial UX critiques)
**Owner:** Lead designer
**Scope this spec:** the Live Preview surface on Capture (and a shared `PreviewStage`
embeddable on Focus). It does NOT cover Settings/site, the Monitor view, or
unattended safety — those are separate specs in the same build batch.

---

## 0. How the critiques changed this design (decisions log)

This spec is the draft design revised against three adversarial critiques
(novice-at-night, ASIAIR field-workflow, accessibility/visual-systems). Every
valid finding is resolved below; rejected ones are recorded with rationale.
Findings were verified against the actual code before accepting.

### Correctness bugs the critiques caught (all confirmed in code — ACCEPTED)

1. **The histogram is NOT linear for NINA.** `devices/base.py:40` docstring and
   `devices/nina.py:91-99` `_decode_gray16` prove NINA's `frame.data` is the
   *rendered* JPEG decoded to L and bit-shifted (`* 257`) — it is the **stretched
   8-bit image promoted to 16-bit**, not linear sensor data. The draft repeatedly
   claimed "histogram is always over real linear data — good." That is false for
   NINA. **Resolution:** the event now carries `data_is_linear: bool`. Histogram
   and any threshold-based feature is labeled and behaves per that flag. We never
   call a stretched histogram "linear."

2. **Star-coordinate scaling was self-contradictory and backwards.** Detector runs
   on `frame.data`; `to_png`/`to_jpeg` downsample *after* (`processing.py:49`). So
   star coords live in `frame.data` pixel space, which equals the reported
   `width/height` (`hub.py:218-219` sets `width = frame.data.shape[1]`), NOT
   "full-res sensor space." **Resolution:** define one coordinate space:
   `data_width/data_height = frame.data.shape`. Star coords are in that space. The
   client maps to the encoded image by `displayScale = display_width / data_width`.
   No "full-res sensor" multiply. Per-frame, never memoized across ids (binning
   changes `data.shape` mid-session).

3. **`full_well = 65535` makes the clip mask lie.** Alpaca exposes `MaxADU`
   (and we already read `gainmax`/`cameraxsize`); many CMOS deliver 12/14-bit in a
   16-bit container, so true saturation is far below 65535. **Resolution:**
   `full_well` is derived from the driver (`MaxADU` for Alpaca; bit-depth/`* 257`
   inverse for NINA) at connect, stored on the camera, and copied per-frame.
   Defaults to 65535 only if unknown, and the clip mask is **disabled** (not shown
   as "approximate") whenever `full_well` is unknown or the data isn't linear.

4. **"1:1 true pixel-peep" is a false claim on a ≤1400px JPEG.** **Resolution:**
   the toolbar control is renamed **"100%"** (100% of the *preview*), and a real
   sensor-native crop is served on demand by a new `/crop` endpoint (raw/linear
   path only). The loupe/focus mode uses `/crop` so focus judgement is on true
   pixels, not resampled JPEG.

5. **Eccentricity from second moments is wrong on saturated/flat-top stars.**
   `detect_stars` keeps stars where the center is ≥95% of the cutout max, i.e. it
   keeps saturated stars (`stars.py:57`). **Resolution:** eccentricity is computed
   only on the **unsaturated, mid-bright** subset (`peak < full_well*0.9` and above
   a faint floor), the same population `median_hfr` already trusts. Eccentricity is
   **deferred to Pass 2** (see §13 phasing); Pass 1 ships HFR circles + count only.

### Deceptive-affordance findings (ACCEPTED — these are the highest priority)

6. **Fake-but-draggable NINA stretch handles.** All three critiques flagged this.
   **Resolution:** when the frame is pre-stretched (`is_stretched`), the
   black/mid/white handles are **truly disabled** (not "tinted", not a hover lock
   icon). The panel relabels to **"Display levels (8-bit)"** and offers only an
   honest single **Brightness** + **Contrast** pair that visibly operates on the
   rendered image. Persistent inline text states the constraint. No CSS-filter
   masquerading as a real stretch.

7. **8-bit ≥254 "approximate" clip mask cries wolf.** **Resolution:** the clip mask
   is shown **only** when `data_is_linear && full_well` is known. On the NINA path
   it is hidden with an inline explanation, never an "approximate" overlay. An
   honest absence beats a confident wrong answer.

### Novice-first findings (ACCEPTED)

8. **No plain-language verdict — only numbers and circles.** **Resolution:** a
   **Focus verdict line** sits above the stage: e.g. "Focus: GOOD — median HFR
   2.3 px (3.1″) · lower is sharper ↓" with a trend arrow vs the previous frame.
   Thresholds are session-relative (derived from best-seen HFR) with sane absolute
   fallbacks, and user-overridable in Settings (out of scope here; this spec reads
   `hfrGood/hfrWarn` from store with defaults). HFR is always defined inline.

9. **"black/mid/white" is jargon; Auto must be the default and sticky.**
   **Resolution:** the primary control is **Auto stretch (on by default)** plus one
   **Brightness** slider. Black/mid/white + the MTF transfer-curve overlay live
   behind an **"Advanced"** disclosure, off by default. "Auto" is a sticky toggle:
   grabbing any handle flips to **Manual** (clearly labeled); pressing **Auto**
   re-derives from the frame's `auto_levels` (never resets to 0/0.5/1). There is no
   separate "Reset" that means something different.

10. **Per-frame auto-stretch, not one-time seed; don't persist absolute stretch.**
    **Resolution:** while Auto is on, display recomputes from each frame's
    `auto_levels` (so dawn/filter changes track). Manual B/M/W are session+target
    scoped and are **not** persisted to localStorage. Only the *toggles* (auto
    on/off, advanced open, overlay prefs) persist. `linkAll` is removed; its only
    honest meaning ("apply same auto policy") is what Auto already does.

11. **Filmstrip "expired" tiles leak server internals and frighten novices.**
    **Resolution:** server keeps **thumbnails for many frames** (160px JPEG ≈5 KB,
    keep 50) and full display images for 8. The client filmstrip only shows tiles
    it can render (thumbnail-backed). Copy is reassuring ("older frames — preview
    not kept; saved FITS are safe") and never says "cache". Empty filmstrip shows a
    labeled placeholder so the affordance is discoverable.

12. **Pinned-during-live is a silent trap; keep a centering aid by default.**
    **Resolution:** when pinned during an active loop, the stage dims and shows a
    prominent persistent banner with a live new-frame counter and a large **Return
    to Live** button. A subtle **center mark** is **on by default** (now that it is
    correctly aligned inside the transform); the full reticle remains a toggle.

13. **Warnings without actions create anxiety.** **Resolution:** every warning pairs
    with a one-line action ("Stars saturated — shorten exposure or lower gain";
    "Few stars — check focus/clouds").

### Field-workflow findings (ACCEPTED)

14. **Pixel scale / arcsec readout.** Thread `pixel_scale_arcsec` (already computed
    in `solve_and_sync`, derivable from `pixel_size_um` + focal length) into the
    event; HFR shows in px **and** arcsec; a scale bar overlays the stage.
15. **Loupe / focus mode is the real ASIAIR focus loop.** Tap a star (or use
    "auto-pick brightest") → `/crop` a ~200px ROI at 1:1 from linear data → show it
    large with live HFR every loop. This is what Focus needs. Pass 2.
16. **Decimate by *worst* stars, not spatial sparsity.** When zoomed out, keep the
    highest-HFR / most-eccentric star per screen-cell (that's where tilt/coma show),
    and disclose "showing 180/420 stars."
17. **Snap, don't blend, on fast loops.** Cross-fade only when idle/paused or when
    exposure ≥ ~3 s; otherwise snap-swap (still double-buffered, no blank). Cancel
    an in-flight fade if a newer frame decodes.
18. **Memory budget on the Pi.** Do **not** retain linear `frame.data` for all 8
    ring slots (a 6200 frame is ~125 MB). Keep linear data for the **latest 1–2**
    frames only (enough for live LUT, `/crop`, `/render`); older slots keep only
    display + thumb bytes. `/crop` and `/render` 422 if the linear frame is no
    longer held.
19. **Drop `/render.png` from Pass 1.** Client LUT covers live; bake stretch into
    downloads only on demand. `/render.png` returns in Pass 2 as the export/low-end
    fallback.
20. **OSC/color frames.** Detect `bayer_pattern`; if present and not debayered,
    label the preview "mono preview of OSC frame" and disable per-channel histogram
    claims. Full debayer-for-preview is a later pass; this spec only prevents the
    silent gray-mosaic bug.

### Accessibility / visual-systems findings (ACCEPTED)

21. **No-color-alone, as a system rule** (see §11 Perceptual Rules). Every new
    color-coded surface (star circles, filmstrip HFR badge, clip tag, toolbar
    toggles, histogram clip glow) gets a redundant shape/glyph/text/line-style
    channel in **both** day and night modes — not just stars at night.
22. **Overlay contrast via dark halo.** Every overlay stroke is drawn twice (a
    `--halo` dark stroke under the colored stroke) so it reads on bright nebula and
    dark sky alike. Specified, not asserted.
23. **One night-tint mechanism.** Both the canvas (raw) and `<img>` (NINA) paths use
    the **same** mechanism: keep the existing CSS `filter: var(--img-filter)` applied
    via a shared `.astro-surface` class on whichever element is the active buffer.
    We do **not** bake tint into the LUT (that diverged the two paths). One code
    path, identical hue/brightness.
24. **No flashing; gate motion.** No "flashing magenta". Clip mask is a **static**
    desaturated-amber hatch outline. All pulses ≤ 0.6 Hz, gated behind
    `prefers-reduced-motion: reduce` (→ static), and LIVE uses a static dot + text
    at night.
25. **Touch targets.** 44px primary controls; histogram handles use a 44×32px hit
    slop **plus** a "scrub-nearest" interaction (drag anywhere moves the nearest
    handle, which enlarges while active) so close handles are reachable with cold
    thumbs.
26. **Keyboard + ARIA + focus rings.** Stage, handles, filmstrip, and toolbar are
    keyboard-operable with `:focus-visible` rings and proper roles. Status changes
    (expired/stale/error/source) go to an `aria-live="polite"` region. Persistent
    inline text replaces tooltips for all constraints (touch has no hover).
27. **Honest disabled state.** Disabled controls desaturate to a defined dim token
    (≥3:1 on panel) + a lock glyph + `aria-disabled`, not `opacity:0.35`.

### REJECTED or deferred (with rationale)

- **C1 #24 "client LUT janks on a phone": partially rejected.** We keep client LUT
  as primary but add a defined fallback: the working canvas downsamples to ≤900px
  while a handle is actively dragged and re-renders full-res on release; if a frame
  arrives mid-drag the live image still advances via the display bytes. No vague
  "device threshold."
- **C2 #17 path-traversal on FileResponse: accepted as a guard, not a redesign.**
  All file-serving endpoints resolve the path and assert `is_relative_to(CAPTURE_DIR)`
  before serving. Cheap, included.
- **C1 #28 / C2 / C3 "too large for one P0": accepted as phasing, not removal.**
  The work is split into Pass 1 (ship-blocking) and Pass 2 (§13). Pass 1 is
  independently shippable and is what the checklist (§14) covers as P0. File
  ownership (§3) lets Pass-1 sub-tasks run in parallel without collision.
- **C3 double-tap as the only zoom: rejected as stated, mitigated.** Double-tap
  stays as an accelerator only; the +/−/Fit/100% buttons are the always-visible
  primary path and are never collapsed into the overflow sheet.

---

## 1. Overview

The live preview is the single most-used interaction and today is unusable for
real work: a fixed-stretch static image that blanks on every new frame, with a
decorative misaligned crosshair, a read-only histogram decoupled from the image,
no zoom, no star overlay, and no history.

This overhaul delivers, **Pass 1 (P0, this spec's checklist):**

- **No-flash double-buffer** with snap-on-fast-loop / fade-when-idle.
- **Zoom / pan** (pointer + touch + keyboard) inside a single shared transform so
  overlays stay pixel-aligned — killing the misaligned-crosshair bug by
  construction. **"100%"** and **Fit** buttons; honest labeling.
- **Adjustable stretch** that is honest per source: full client-side LUT remap for
  raw/linear frames (sim/Alpaca), and an explicitly-labeled display-only
  Brightness/Contrast for pre-stretched NINA frames (real B/M/W disabled).
- **Interactive histogram** in the **stretched/display domain** so the handles have
  usable travel (a linear histogram of a light frame is a left-edge spike), with a
  plain Brightness primary and Advanced black/mid/white.
- **Per-star HFR overlay** (color + shape + halo), worst-star decimation,
  per-frame coordinate scaling, and a **plain-language focus verdict** with arcsec.
- **Clip/saturation mask** — raw/linear path only, driver-derived `full_well`,
  static hatch, never on NINA.
- **Frame filmstrip** backed by cheap server thumbnails (no "expired" tiles),
  pinned-vs-live state, shared with Focus.
- **JPEG for the live loop, lossless for the paused/zoomed frame and `/crop`**, so
  bandwidth is low but focus judgement is never on lossy mush.

**Pass 2 (fast-follow):** eccentricity overlay, loupe/focus-star mode, `/crop`
sensor-1:1 everywhere, `/render.png` server stretch + export, overview minimap,
OSC debayer-for-preview.

---

## 2. Two render strategies (the NINA-vs-raw split — corrected)

Driven by two **per-frame** booleans from the backend: `is_stretched` and
`data_is_linear` (for current backends these are inverses, but we carry both so a
future backend that sends linear data + a render can opt into full controls).

| | Raw / linear path (sim, Alpaca) | NINA passthrough |
|---|---|---|
| `is_stretched` | `false` | `true` |
| `data_is_linear` | `true` | `false` |
| Server display bytes | `to_jpeg(auto_stretch(data))`, ≤1400px, q85 | NINA's `rendered_bytes` (JPEG) verbatim |
| Lossless base for paused/zoom | `to_png(stretch=auto)` cached (latest 1–2) | none (NINA gives no linear data) |
| Client display element | `<canvas>` (LUT remap) OR `<img>` of display bytes | `<img>` |
| **Stretch sliders** | **full client B/M/W LUT, instant** | **DISABLED**; honest Brightness/Contrast only, labeled "Display levels (8-bit)" |
| Histogram domain | display/stretched-domain bins (usable handles); a true-linear histogram is available behind Advanced labeled "linear" | display-domain bins, labeled "of displayed image" |
| Clip mask | exact (linear threshold at `full_well`) | **hidden** (no linear data) |
| 100% / loupe | preview-100%, true sensor-1:1 via `/crop` (Pass 2) | preview-100% only |
| Star overlay | from `star_list` | from `star_list` if NINA returned per-star data; else hidden with hint |

Both paths share: double-buffer, zoom/pan, center mark/reticle, filmstrip,
download, verdict line, scale bar.

**Stretch fidelity decision:** client-side LUT is primary for the linear path
(zero latency). The server `/render.png` (Pass 2) is the export/low-end fallback.
There is **no** server round-trip on a slider drag in Pass 1.

---

## 3. Component & file plan (disjoint ownership for parallel work)

All new UI components live under `ui/src/components/preview/` (new folder). Each
file below lists its **single owner area** so Pass-1 sub-agents don't collide. A
file is owned by exactly one work-stream; shared contracts (§4) are landed
**first** by the Contracts owner, then streams build against them.

### Work-stream C — Shared contracts (land first, blocks others)
**Modify (small, additive):**
- `ui/src/types.ts` — add `StarMark`, `Viewport`, `StretchParams`, `OverlayToggles`,
  `PreviewSource`; extend `PreviewInfo`.
- `ui/src/store.ts` — add preview slice (state + actions), rewrite the `"preview"`
  event case to `pushPreview`, add narrow selector hooks.
- `server/astrodeck/devices/base.py` — add `star_list`, `full_well`,
  `pixel_scale_arcsec`, `data_is_linear` fields to `CameraFrame`; add
  `full_well`/`pixel_size_um` capability defaults already present on `Camera`.
- `server/astrodeck/hub.py` — `_publish_preview` event shape + ring entry struct +
  `full_well`/`pixel_scale` plumbing (see §6). Ring entry is the contract the API
  reads.
- `server/astrodeck/api/app.py` — new routes (signatures only landed first as
  stubs returning 501, then filled by stream S).

### Work-stream V — View wiring & orchestrator
**Create:**
- `ui/src/components/preview/LivePreview.tsx` — orchestrator (Panel + sub-components).
- `ui/src/components/preview/PreviewMeta.tsx` — header readout.
- `ui/src/components/preview/FocusVerdict.tsx` — plain-language verdict line.
**Modify:**
- `ui/src/views/CaptureView.tsx` — replace inline preview Panel + crosshair +
  read-only histogram with `<LivePreview/>`; split the broad `useStore()` into
  narrow selectors.
- `ui/src/App.tsx` — narrow the broad `useStore()` destructure (perf).

### Work-stream S — Stage, gestures, image remap
**Create:**
- `ui/src/components/preview/PreviewStage.tsx`
- `ui/src/components/preview/usePreviewGestures.ts`
- `ui/src/components/preview/useImageRemap.ts`
- `ui/src/components/preview/Reticle.tsx`
- `ui/src/components/preview/ScaleBar.tsx`

### Work-stream O — Overlays & histogram
**Create:**
- `ui/src/components/preview/StretchHistogram.tsx`
- `ui/src/components/preview/StarOverlay.tsx`
- `ui/src/components/preview/ClipMaskLayer.tsx`
- `ui/src/components/preview/FrameStats.tsx` (min/median/mean/max/σ + HFR/stars,
  extracted from CaptureView so it can be reused on Focus).

### Work-stream T — Toolbar & filmstrip
**Create:**
- `ui/src/components/preview/PreviewToolbar.tsx`
- `ui/src/components/preview/FrameFilmstrip.tsx`

### Work-stream B — Backend imaging
**Modify:**
- `server/astrodeck/imaging/processing.py` — add `to_jpeg`, `to_thumb`,
  `stretch_with`, `auto_levels`, `levels_to_mtf`, `display_histogram`; keep
  `to_png`, `compute_histogram`, `auto_stretch`.
- `server/astrodeck/imaging/stars.py` — add `star_marks(...)`; add a
  `detect_stars(..., return_marks=True)` path; (ecc/theta fields land but stay
  unused/optional in Pass 1).
- `server/astrodeck/imaging/__init__.py` — export new helpers.
- `server/astrodeck/devices/alpaca.py` — read `MaxADU` → `full_well` on connect.
- `server/astrodeck/devices/nina.py` — set `data_is_linear=False`,
  `is_stretched`(implicit), `full_well` from bit-depth if available.

### Work-stream X — CSS / design tokens
**Modify:**
- `ui/src/index.css` — remove `.crosshair`; add `.astro-surface`, `.preview-stage`
  (touch-action rules), `.preview-transform`, filmstrip/handle/halo styles, a
  global `:focus-visible` token, `--halo` token, `prefers-reduced-motion` guards,
  disabled-state token.
- `ui/src/components/graphs.tsx` — collapse the old `Histogram` to one `<path>`
  (perf); keep its export and call sites untouched.

> **Collision rule:** `store.ts`, `types.ts`, `hub.py`, `app.py`, `base.py` are
> touched only by stream **C** (and stream **S/T** consume the resulting types).
> `processing.py`/`stars.py`/`alpaca.py`/`nina.py` only by stream **B**.
> `index.css`/`graphs.tsx` only by stream **X**. Each new file under
> `components/preview/` has exactly one owning stream above. No two streams edit
> the same file.

---

## 4. Shared contracts

### 4.1 TypeScript types (`ui/src/types.ts`)

```ts
export type PreviewSource = "sim" | "alpaca" | "nina";

export interface StarMark {
  x: number;          // image-data pixel coords == frame.data space (NOT sensor space)
  y: number;
  hfr: number;        // px
  ecc?: number;       // 0..1, Pass 2 (optional; absent in Pass 1)
  theta?: number;     // radians, Pass 2
}

export interface PreviewInfo {
  id: number;
  stats: { min: number; max: number; mean: number; median: number; std: number };
  histogram: number[];            // DISPLAY-domain bins (see histogram_domain)
  histogram_linear?: number[];    // linear bins, present only when data_is_linear
  histogram_domain: "display" | "linear";
  exposure_s: number;
  gain: number;
  binning: number;
  data_width: number;             // == frame.data.shape[1]  (was `width`)
  data_height: number;            // == frame.data.shape[0]  (was `height`)
  display_width: number;          // encoded preview px after ≤1400 downscale
  display_height: number;
  mime: string;                   // "image/jpeg" | "image/png"
  source: PreviewSource;
  is_stretched: boolean;          // true => no client re-stretch (NINA)
  data_is_linear: boolean;        // false for NINA (decoded-from-render)
  has_lossless: boolean;          // a lossless base is cached (paused/zoom)
  full_well: number | null;       // driver-derived; null => clip mask disabled
  pixel_scale_arcsec?: number;    // for arcsec HFR + scale bar, when known
  bayer_pattern?: string | null;  // non-null => OSC frame (mono preview note)
  auto_levels: { black: number; mid: number; white: number }; // 0..1 in display domain
  saved_path?: string;
  saved_local?: boolean;          // true only if saved_path is under CAPTURE_DIR (FITS dl ok)
  hfr?: number;
  stars?: number;
  star_list?: StarMark[];
  ts: number;                     // server epoch seconds (filmstrip age)
}

export interface Viewport { scale: number; x: number; y: number; fit: boolean; }

export interface StretchParams {
  auto: boolean;                  // sticky; default true
  black: number; mid: number; white: number; // display-domain 0..1, used in Manual
  brightness: number;             // simple primary slider, -1..1 (maps to mid)
  contrast: number;               // display-only path (NINA), -1..1
  advancedOpen: boolean;          // disclosure for B/M/W + curve, default false
}

export interface OverlayToggles {
  stars: boolean;                 // default false
  clip: boolean;                  // default false (only effective when linear+full_well)
  reticle: boolean;               // default false (full reticle)
  centerMark: boolean;            // default TRUE (subtle framing aid)
}
```

`width`/`height` are **renamed** to `data_width`/`data_height` to end the
"sensor vs data vs display" ambiguity. The single existing consumer
(`CaptureView` header span) is updated by stream V.

### 4.2 Store slice (`ui/src/store.ts`)

Add to `AppState`:

```ts
// frame history: server keeps thumbnails for many; client holds metadata for cap 24
previews: PreviewInfo[];          // newest last, cap 24
selectedPreviewId: number | null; // null => follow live
livePreviewId: number | null;

viewport: Viewport;               // persists across tab switch (in-memory, not localStorage)
stretch: StretchParams;           // auto/advancedOpen persist; B/M/W do NOT persist
overlays: OverlayToggles;         // persists to localStorage
hfrGood: number;                  // verdict threshold, default 2.5 (px) or session-best*1.15
hfrWarn: number;                  // default 4.0

pushPreview: (p: PreviewInfo) => void;
selectPreview: (id: number | null) => void;   // null => snap back to live
setViewport: (v: Partial<Viewport>) => void;
setStretch: (s: Partial<StretchParams>) => void;
setOverlays: (o: Partial<OverlayToggles>) => void;
```

`pushPreview`:
- append, trim to 24, set `livePreviewId = p.id`;
- if `stretch.auto`, the display recomputes from `p.auto_levels` (handled in
  `useImageRemap`, store just keeps `auto` true);
- if the user had pinned (`selectedPreviewId !== null`), do **not** move the stage;
  the filmstrip badges new live arrivals and the pinned banner counts them.

`"preview"` event case becomes `get().pushPreview(ev.data as unknown as PreviewInfo)`.

**Narrow selector hooks** (exported, to fix the broad-`useStore` re-render):
`usePreviews()`, `useLivePreview()` (metadata for `selectedPreviewId ?? livePreviewId`),
`useViewport()`, `useStretch()`, `useOverlays()`, `useNight()`. `App.tsx` and
`CaptureView` stop destructuring the whole store.

**Persistence (`astrodeck-preview` localStorage key):** persist
`overlays`, `stretch.auto`, `stretch.advancedOpen` only. **Never** persist
`stretch.black/mid/white/brightness/contrast`, `viewport`, or `selectedPreviewId`.
Manual stretch resets per session/target. (Resolves C1#21, C2#14.)

### 4.3 Backend `CameraFrame` fields (`devices/base.py`)

```python
star_list: list[dict] | None = None      # [{x,y,hfr[,ecc,theta]}] in frame.data coords
full_well: int | None = None             # driver-derived saturation ADU; None if unknown
pixel_scale_arcsec: float | None = None  # when focal length + pixel size known
data_is_linear: bool = True              # False for NINA (decoded-from-render)
```

`Camera` capability already has `pixel_size_um`; add `full_well: int | None = None`
populated on connect (Alpaca: `MaxADU`; NINA: from bit-depth if exposed).

### 4.4 REST endpoints (`api/app.py`)

Pass 1:

| Method/Path | Returns | Notes |
|---|---|---|
| `GET /api/preview/{id}` | display bytes, correct `mime` | replaces hardcoded PNG; new canonical URL |
| `GET /api/preview/{id}.png` | **only real PNG** (lossless base if `has_lossless`, else 404→use `/`) | back-compat; NOT a JPEG-under-.png |
| `GET /api/preview/{id}/lossless.png` | lossless stretched PNG (latest 1–2 only) | for paused/zoom; 422 if not held |
| `GET /api/preview/{id}/thumb.jpg` | ~160px JPEG q70 | filmstrip; cached for many frames |
| `GET /api/preview/{id}/fits` | `FileResponse(saved_path)` | only if `saved_local` (under CAPTURE_DIR); else 404 with reason |
| `GET /api/preview/{id}/png` | full-res stretched PNG, attachment | download |

Pass 2 (declared now, stubbed 501): `GET /api/preview/{id}/crop?x&y&w&h`,
`GET /api/preview/{id}/render.png?black&mid&white`.

**Security:** `/fits` resolves `saved_path` and serves only if
`Path(saved_path).resolve().is_relative_to(CAPTURE_DIR.resolve())`. All routes keep
`Cache-Control: max-age=3600` like the existing route.

**Client URL building:** the client builds the live image URL as `/api/preview/{id}`
and reads `mime` from the event. It requests `/lossless.png` only when paused/zoomed
and `has_lossless`. No `.png`-suffix-returns-JPEG content negotiation.

### 4.5 `preview` event payload (published by `hub._publish_preview`)

Superset of §4.1 `PreviewInfo` (the event *is* the `PreviewInfo`). New/changed keys:
`data_width`, `data_height`, `display_width`, `display_height`, `mime`, `source`,
`is_stretched`, `data_is_linear`, `has_lossless`, `full_well`, `pixel_scale_arcsec`,
`bayer_pattern`, `histogram` (display-domain), `histogram_linear` (linear path only),
`histogram_domain`, `auto_levels`, `ts`, `star_list` (linear path), `saved_local`.

### 4.6 Backend imaging signatures (`imaging/processing.py`, `imaging/stars.py`)

```python
# processing.py
def to_jpeg(data, *, black=None, mid=None, white=None,
            max_width=1400, quality=85) -> tuple[bytes, int, int]:
    """Stretched 8-bit JPEG (mode 'L'). Returns (bytes, w, h)."""

def to_thumb(data_or_img, *, max_width=160, quality=70) -> bytes: ...

def stretch_with(data, black, mid, white) -> np.ndarray:
    """Explicit-levels MTF stretch → float [0,1]."""

def auto_levels(data, target_bg=0.18) -> tuple[float, float, float]:
    """The (black, mid, white) the auto-stretch implies, in display 0..1 — seeds
    the UI handles so Manual starts where the image actually looks right."""

def levels_to_mtf(black, mid, white) -> tuple[float, float, float]: ...

def display_histogram(stretched01, bins=128) -> list[int]:
    """Histogram of the DISPLAY-domain image so handles have usable travel."""

# stars.py
@dataclass
class Star:
    x: float; y: float; flux: float; hfr: float; peak: float
    ecc: float = 0.0      # Pass 2; 0.0 placeholder in Pass 1
    theta: float = 0.0

def star_marks(stars, *, full_well=None) -> list[dict]:
    """[{x,y,hfr[,ecc,theta]}] rounded; ecc only for unsaturated mid-bright stars."""
```

`detect_stars` is reused (one call feeds both `median_hfr` and `star_marks` — no
double detection in the capture hot path).

---

## 5. UI states (every state, all components)

### Stage
- **Empty** (no previews): subtle ghost reticle + "No frame yet — take an exposure"
  (sentence case). Zoom/stretch/filmstrip controls disabled (defined dim token +
  lock glyph + `aria-disabled`). Histogram: "Awaiting first frame."
- **Loading first image:** skeleton shimmer (gated by `prefers-reduced-motion`);
  LIVE hidden until first decode.
- **Loading subsequent (looping):** no blank — old buffer holds; new buffer
  **snaps** in on fast loops (<3 s) or **fades** (120 ms) when idle/paused. Static
  "●" indicator (not a fast spinner) during decode.
- **Success / live:** image + enabled overlays; LIVE badge (static dot + "LIVE"
  text; pulses ≤0.6 Hz only in day mode and only if motion allowed).
- **Pinned (history):** stage holds the pinned frame, dimmed; persistent banner
  "Viewing frame #n · N new frames · [Return to Live]" with `aria-live`.
- **Older frame, image not kept:** thumbnail-backed tiles only exist if renderable,
  so this state is reached only by direct id; copy "Older frame — full preview not
  kept. Saved FITS are safe." Stats/histogram from retained metadata still render.
- **Fetch error (endpoint 5xx):** inline "Preview unavailable" + Retry; `aria-live`;
  existing `showToast`.
- **Pre-stretched (NINA):** persistent inline badge on stage "Source: NINA
  (display-only adjust)". Real B/M/W disabled (§ Histogram). Clip toggle hidden
  with inline note. Star overlay shown only if `star_list` present.
- **OSC frame:** inline "Mono preview of OSC frame" note; clip/per-channel claims
  suppressed.
- **No / few stars:** Stars toggle disabled with inline hint "No per-star data for
  this frame" (NINA without marks, or <3 stars). Verdict line: "Few stars — check
  focus/clouds."
- **WS dropped (`!wsConnected`):** stage dims to ~60% + persistent "Stale — link
  down" ribbon (`aria-live`); capture-affecting toolbar actions (download of live,
  etc.) disabled, not merely dimmed.

### Focus verdict line (above stage)
- GOOD / FAIR / POOR derived from `hfr` vs `hfrGood`/`hfrWarn`, with trend arrow vs
  previous frame and arcsec when `pixel_scale_arcsec` known. Always defines HFR
  ("lower is sharper"). Color **and** word **and** arrow (never color alone).
- Saturation action line when `stats.max >= full_well` (linear) or clip mask active.

### Histogram (`StretchHistogram`)
- **Primary:** Auto toggle (sticky, default on) + one Brightness slider.
- **Advanced disclosure (default closed):** black/mid/white draggable handles +
  optional MTF transfer-curve overlay (dashed, different luminance — never hue-only).
- **Domain:** display/stretched bins so handles have travel; a "linear" view is
  available behind Advanced **only** when `data_is_linear`, labeled "linear".
- **NINA / pre-stretched:** B/M/W handles **disabled** (lock glyph, desaturated),
  panel titled "Display levels (8-bit)"; only Brightness/Contrast active; persistent
  inline "Frame pre-stretched by NINA — display-only adjust."
- **Clipping:** when `stats.max >= full_well`, white handle shows a "▲ CLIPPED" text
  tag + dashed outline (shape+text, not just a red glow).
- Each handle: `role="slider"`, `aria-valuemin/max/now`, keyboard arrows, visible
  focus ring, live numeric readout (% and ADU). 44×32 hit slop + scrub-nearest.
- Empty: "Awaiting first frame."

### Star overlay
- `<circle>` per star at `(x,y) * displayScale` inside the transform;
  `vector-effect="non-scaling-stroke"`; **dark halo** under colored stroke.
- Quality encoded by **color + stroke style** in both modes: good = thin solid,
  warn = medium solid, bad = dashed. Tap-to-pin readout ("HFR 2.3px · 3.1″"),
  `aria-describedby` live region (not a CSS `title`).
- Decimation when zoomed out keeps the **worst** star per ~24px screen cell;
  discloses "Showing 180/420 stars."

### Clip mask
- Only when `data_is_linear && full_well != null && overlays.clip`. Static
  desaturated-amber **hatch outline** over pixels ≥ `full_well*0.99`. Never flashing,
  never magenta, hidden entirely on NINA/unknown-well with inline reason.

### Filmstrip
- Thumbnail-backed tiles (server keeps thumbs for many frames). Newest right;
  auto-scroll to end only when following live **and** no user interaction in last
  ~1 s. Each tile: HFR number + good/warn/bad glyph (●/▲/■) + relative age; LIVE
  tile gets a static accent border + "LIVE". Empty: labeled placeholder "Frame
  history will appear here." Tap → `selectPreview(id)`.

### Toolbar
- Always-visible primary (≥44px): −, zoom %, +, **Fit**, **100%**. Toggles
  (Stars / Clip / Reticle / Center) with filled-background active state + checkmark
  (not color-only). Download ▾ (FITS only if `saved_local`, else disabled + lock
  glyph + "saved on NINA host" note; PNG; raw PNG). LIVE badge. Overflow "⋯" sheet
  holds only non-essential items; never Fit/100%.

---

## 6. Backend data flow (`hub._publish_preview`, corrected)

1. `capture()` → `CameraFrame`.
   - **Linear path:** one `detect_stars(frame.data)` call → `median_hfr` + count +
     `star_marks`. Compute `auto_levels`; render display **JPEG** via
     `to_jpeg(auto)`; build `display_histogram(stretched)`; also compute
     `compute_histogram(frame.data)` as `histogram_linear`. Cache ring entry with
     display bytes + thumb + (for latest 1–2 only) lossless PNG + linear `data`
     reference. `full_well` from camera; `pixel_scale_arcsec` from
     `pixel_size_um`+focal length if known.
   - **NINA path:** use `rendered_bytes` verbatim as display; `to_thumb` from it;
     `histogram` = `compute_histogram(frame.data)` but labeled
     `histogram_domain="display"` and `data_is_linear=False`; no lossless, no linear
     retention, no clip; `star_list` only if NINA returned per-star data (it
     usually does not → `None`).
2. Ring entry struct (replaces `tuple[bytes, str]`):
   ```python
   @dataclass
   class PreviewEntry:
       display: bytes
       mime: str
       thumb: bytes
       lossless: bytes | None      # only latest 1..2
       linear: np.ndarray | None   # only latest 1..2 (for /crop, /render Pass 2)
       meta: dict                  # the published info dict
   ```
   Ring keeps display+thumb for 8 (thumbs effectively for ~50 via a separate
   thumb-only dict keyed by id), `lossless`/`linear` for the latest 1–2 only.
3. Publish the `preview` event = the `meta` dict (§4.5).
4. WS → `pushPreview`.

**Pi memory:** at most ~2 linear arrays + 8 display JPEGs + ~50 tiny thumbs held.
No 1 GB ring.

---

## 7. Gestures, remap, transform (stream S internals)

- `usePreviewGestures(ref, viewport, onChange)` — Pointer Events.
  - **`touch-action`** is `none` **only when zoomed in** (`scale > fitScale`); at
    fit, single-finger scrolls the page (no trap). (Resolves C1#13, C2#15.)
  - Single-pointer drag → pan only when zoomed; two-pointer → pinch+pan around
    centroid; `wheel` → zoom at cursor; `dblclick`/double-tap → Fit↔100%
    (accelerator only). Clamp scale `[fitScale, maxScale]`, integer-rounded pan,
    `transform-origin: 0 0`.
- `useImageRemap(canvasRef, displayUrl, losslessUrl, stretch, source)` — linear path
  only. Loads the base once; on B/M/W or Brightness change re-applies a 256-entry
  LUT via `putImageData`, rAF-debounced. While a handle is actively dragged the
  working canvas runs at ≤900px; full-res on release. **Night tint is NOT in the
  LUT** — it comes from the shared `.astro-surface` CSS filter (one mechanism).
- Transform layer: one `.preview-transform` div with `translate/scale`; image
  buffer(s) + overlay SVG/canvas are children → overlays pan/zoom in lockstep and
  stay aligned (kills the misaligned-crosshair class of bug). All overlay strokes
  use `non-scaling-stroke` + dark halo (reticle and histogram included).

---

## 8. Night-mode notes

- **One tint mechanism:** the active image buffer (canvas or `<img>`) carries
  `.astro-surface` → `filter: var(--img-filter)` (existing
  `sepia(1) saturate(3) hue-rotate(-35deg) brightness(0.75)`). No LUT tint, so the
  raw and NINA paths look identical and match the rest of the app.
- **No color-alone:** night tokens collapse to coral-red
  (`--good #ff7a7a / --warn #ff9b5d / --bad #ff4040`), so every status uses shape +
  text too (§11). Star quality = stroke style; filmstrip = number + glyph; clip =
  hatch + "▲ CLIPPED"; toolbar active = filled + check.
- **Halo for legibility:** overlay strokes get a `--halo` (dark) under-stroke so a
  dim-red circle still reads on a dim-red star. Overlays may use a *slightly*
  brighter red-channel-only stroke than the image; tested for ≥3:1, not asserted.
- **No flashing ever at night;** LIVE is a static dot + text; decode "●" is static;
  all pulses ≤0.6 Hz and gated behind `prefers-reduced-motion`.
- **Reticle/center mark:** neutral high-value stroke + dark halo, not `--accent`
  red-on-red.
- Add `--halo` token: light theme `#00000099`, night `#1a0303cc`.
- Preserve the dimmed starfield background already present in `:root.night body`.

## 9. 375px-phone notes

- `CaptureView` grid `xl:grid-cols-[1fr_340px]` collapses to one column below `xl`.
  Phone order: **Verdict → Stage (`aspect-[3/2]`, ~250px) → Toolbar (compact) →
  Histogram → Filmstrip → Exposure controls.** Image gets the top of the viewport.
- Fixed aspect-ratio stage box + double-buffer = **no layout shift** on new frame.
- Primary zoom = on-screen 44px +/−/Fit/100% buttons (always visible). Pan only
  when zoomed; page scrolls otherwise.
- Histogram handles: 44×32 hit slop + scrub-nearest; live numeric readout (can't
  judge position by color on a small red histogram).
- Toolbar overflow → bottom sheet ("⋯"), never holding Fit/100%.
- Filmstrip thumbnails ≥44px tap targets; horizontal scroll; auto-scroll suppressed
  ~1 s after any touch.
- Persistent inline status (not tooltips) everywhere — touch has no hover.

---

## 10. Interop with the rest of the app

- **Focus view:** embed `<PreviewStage compact/>` + `<FrameStats/>` reading the same
  store `previews[]` so Focus is not blind while focusing (review IA ask). Loupe
  mode (Pass 2) is the real focus loop. V-curve (`graphs.tsx VCurve`) untouched.
- **Monitor view (next spec):** `<FrameFilmstrip/>` + a `livePreviewId` thumbnail
  drop in with no new plumbing.
- **Solve & Sync on Capture:** solved frames already flow through
  `_publish_preview`; the solved RA/Dec annotation becomes another overlay layer in
  the same transform space later (extension point, not in this spec).
- **Old `Histogram` (`graphs.tsx`):** kept for Capture's existing call site; stream
  X collapses it to one `<path>` (perf) without changing its props.
- **`.crosshair` CSS:** removed; replaced by `<Reticle/>` + center mark inside the
  transform.

---

## 11. Perceptual rules (apply to EVERY new preview component)

This block is normative; each of the new files must conform.

1. **No meaning by color alone, ever** — always pair color with shape, glyph, text,
   or line-style. Applies to star circles, filmstrip HFR badge, clip tag, histogram
   clip glow, and toolbar toggle active state, in **both** day and night modes.
2. **Every overlay stroke gets a dark `--halo` under-stroke** for guaranteed
   contrast on bright nebula and dark sky (draw twice: 2px halo, then 1px color).
3. **One night-tint transfer** shared by canvas + `<img>` via `.astro-surface`
   (CSS `--img-filter`). No second mechanism.
4. **All pulsing/flashing ≤0.6 Hz, never luminance-pulsing at night, and gated
   behind `prefers-reduced-motion: reduce`** (→ static). No "flashing" anything.
5. **44px primary touch targets; 24px absolute minimum only with a scrub-nearest
   fallback** for closely-spaced histogram handles.
6. **Keyboard + `:focus-visible` + `role`/`aria` on stage, handles, filmstrip,
   toolbar.** Arrow-keys pan/nudge, `+/-` zoom, `0` fit, `1` 100%, Enter selects.
7. **Persistent inline status (not tooltips), mirrored to `aria-live="polite"`** for
   NINA-degraded, no-star, older-frame, stale-link, and error states.
8. **Disabled = defined dim token (≥3:1 on panel) + lock glyph + `aria-disabled`,**
   not `opacity:0.35`.

---

## 12. Honesty rules (the deceptions the critiques flagged — normative)

1. Never present a real-looking levels control that secretly does something else.
   NINA B/M/W are **disabled**, not faked.
2. Never call a stretched histogram "linear." Label by `histogram_domain`.
3. Never label downscaled-JPEG zoom "1:1 / pixel-peep." It is "100%" of the preview;
   true sensor 1:1 only via `/crop` (Pass 2), labeled "Sensor 1:1".
4. Never show a clip mask without linear data + known `full_well`. Hide it honestly.
5. Never offer a FITS download that will 404. Gate on `saved_local`.
6. Every warning carries a one-line action.

---

## 13. Phasing

**Pass 1 (P0 — this spec's checklist, ship-blocking):** double-buffer
snap/fade; zoom/pan (pointer+touch+keyboard); Fit/100%; Auto + Brightness stretch,
Advanced B/M/W on the linear path, disabled+honest on NINA; display-domain
histogram; HFR star overlay (color+shape+halo, worst-star decimation, per-frame
scaling); focus verdict line with arcsec; clip mask (linear+full_well only);
filmstrip (thumbnail-backed, pinned/live); JPEG-live + lossless-paused; JPEG
encoding for the loop; perceptual + honesty rules; night + 375px; perf selectors;
`full_well`/`pixel_scale` plumbing; `/`, `/lossless.png`, `/thumb.jpg`, `/fits`,
`/png` endpoints; path-traversal guard.

**Pass 2 (fast-follow):** eccentricity overlay (unsaturated subset); loupe/focus-star
mode via `/crop`; sensor-1:1 everywhere; `/render.png` server stretch + export;
overview minimap; exposure countdown ring on the stage; OSC debayer-for-preview;
per-channel histogram.

---

## 14. Implementation checklist (Pass 1)

**Stream C — contracts (do first):**
- [ ] `types.ts`: add `StarMark`, `Viewport`, `StretchParams`, `OverlayToggles`,
      `PreviewSource`; rewrite `PreviewInfo` (rename `width/height` →
      `data_width/data_height`, add all new fields).
- [ ] `store.ts`: add preview slice + actions; `"preview"` → `pushPreview`; narrow
      selector hooks; persistence rules (no absolute stretch persisted).
- [ ] `base.py`: add `star_list`, `full_well`, `pixel_scale_arcsec`,
      `data_is_linear` to `CameraFrame`; `full_well` capability on `Camera`.
- [ ] `hub.py`: `PreviewEntry` ring struct; corrected `_publish_preview` (coord
      space, display histogram, linear retention cap 1–2, thumbs, `full_well`,
      `pixel_scale`, `source`, `saved_local`).
- [ ] `app.py`: new routes (`/`, `.png` lossless-or-404, `/lossless.png`,
      `/thumb.jpg`, `/fits` guarded, `/png`); Pass-2 routes stubbed 501.

**Stream B — backend imaging:**
- [ ] `processing.py`: `to_jpeg`, `to_thumb`, `stretch_with`, `auto_levels`,
      `levels_to_mtf`, `display_histogram`.
- [ ] `stars.py`: `star_marks`; single-detection reuse with `median_hfr`.
- [ ] `alpaca.py`: read `MaxADU` → `full_well` on connect.
- [ ] `nina.py`: set `data_is_linear=False`, `full_well` if known.
- [ ] `imaging/__init__.py`: export new helpers.

**Stream V — view + orchestrator:**
- [ ] `LivePreview.tsx`, `PreviewMeta.tsx`, `FocusVerdict.tsx`.
- [ ] `CaptureView.tsx`: swap inline preview for `<LivePreview/>`; narrow selectors.
- [ ] `App.tsx`: narrow `useStore()`.

**Stream S — stage:**
- [ ] `PreviewStage.tsx` (double-buffer snap/fade, transform, source split).
- [ ] `usePreviewGestures.ts` (touch-action gating, keyboard).
- [ ] `useImageRemap.ts` (LUT, drag-downsample, no night-tint in LUT).
- [ ] `Reticle.tsx` (+ center mark), `ScaleBar.tsx`.

**Stream O — overlays + histogram:**
- [ ] `StretchHistogram.tsx` (display-domain, Auto+Brightness primary, Advanced
      B/M/W, NINA-disabled, ARIA sliders, scrub-nearest, clip tag).
- [ ] `StarOverlay.tsx` (color+shape+halo, worst-star decimation, per-frame scale).
- [ ] `ClipMaskLayer.tsx` (linear+full_well only, static hatch).
- [ ] `FrameStats.tsx` (extracted, reusable).

**Stream T — toolbar + filmstrip:**
- [ ] `PreviewToolbar.tsx` (44px primaries, honest 100%/Download gating, toggles
      with non-color active state).
- [ ] `FrameFilmstrip.tsx` (thumbnail-backed, pinned/live, auto-scroll suppression).

**Stream X — CSS/tokens:**
- [ ] `index.css`: remove `.crosshair`; add `.astro-surface`, `.preview-stage`,
      `.preview-transform`, filmstrip/handle/halo styles, `--halo` token,
      `:focus-visible` ring, disabled token, `prefers-reduced-motion` guards.
- [ ] `graphs.tsx`: collapse old `Histogram` to one `<path>`.

**Cross-cutting verification:**
- [ ] Overlay alignment tested on **bin1, bin2, and a NINA frame** (coord scaling).
- [ ] NINA: B/M/W disabled, no clip mask, histogram labeled "of displayed image".
- [ ] No-flash confirmed during a 2 s loop (snap) and a 30 s sub (fade).
- [ ] Night mode: good/warn/bad distinguishable by shape with color removed.
- [ ] Keyboard-only operation of stage + handles + filmstrip.
- [ ] Pi memory: linear retention capped at ≤2 frames.
- [ ] `/fits` refuses paths outside `CAPTURE_DIR`.
