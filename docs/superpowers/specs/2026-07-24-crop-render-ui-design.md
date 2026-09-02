# Crop + Render UI — pixel-peep zoom, WYSIWYG export, per-pixel clip mask

Design spec (design only — no implementation) for the UI that CONSUMES the two
already-real preview endpoints that nothing currently calls:

- `GET /api/preview/{id}/crop?x&y&w&h` — sensor-1:1 (no downscale) auto-stretched
  PNG of an ROI cut from the retained **linear** array. `app.py:2695`–`:2716`.
- `GET /api/preview/{id}/render.png?black&mid&white` — full native-resolution
  server-baked PNG of the linear frame at explicit stretch levels.
  `app.py:2718`–`:2734`.

Three deliverables, one theme (true-resolution truth from the linear array):
- **(a) Pixel-peep zoom** — when the user zooms past display-native, fetch a
  1:1 `/crop` of the visible ROI so they inspect real sensor pixels, not an
  upscaled blur.
- **(b) "Download rendered PNG"** — a full-res baked WYSIWYG export via
  `/render.png` at the current stretch levels.
- **(c) Wire ClipMaskLayer's per-pixel clip paint** — the layer's own comment
  (`ClipMaskLayer.tsx:12`) says per-pixel painting "lands with the linear /crop
  path in Pass 2"; that path is now live.

---

## 1. Design

### 1.1 Why these three, and why now

The linear path already renders WYSIWYG on a client `<canvas>` (`useImageRemap`),
but only at the **display encode resolution** (`_encode` caps width at 1400 —
`processing.py:124`). Two truths are therefore missing from the UI today:

1. **Real pixels when you zoom in.** `usePreviewGestures` clamps zoom to
   `fitScale * 8` (`usePreviewGestures.ts:30`,`:52`–`:55`) and the transform just
   CSS-scales the ≤1400px `<canvas>`/`<img>`. Past ~100% the user sees bilinear
   upscaling of a downscaled base — a lie about focus/noise/stars. `/crop`
   returns the **native sensor pixels** of the visible box.
2. **A full-resolution export that matches the screen.** Every current download
   is either the ≤1400px display PNG (`/png`, `/lossless.png`), a phone JPEG
   (`/share.jpg`), or the raw FITS. There is no "give me the full-res picture as
   I've stretched it." `/render.png` bakes the linear array at chosen levels.
3. **The clip mask is a frame, not a map.** `ClipMaskLayer` can only draw a
   static amber hatch border because it has no linear pixels client-side
   (`ClipMaskLayer.tsx:6`–`:12`). `/crop` gives it exactly those pixels for the
   visible region.

All three are **linear-path only**. NINA/pre-stretched frames have no
`entry.linear`, so `/crop` and `/render.png` 404 (`app.py:2705`,`:2727`) and the
clip mask is already honestly disabled there. This is the same capability gate
that already governs the canvas path (`PreviewStage.tsx:229` `linearEnabled`) and
the clip toggle (`LivePreview.tsx:80` `clipAvailable`).

### 1.2 Current-state seams (every line read)

**The shared stage + transform** — `ui/src/components/preview/PreviewStage.tsx`
- The one `.preview-transform` layer (translate+scale, origin `0 0`) that holds
  the image buffer(s) + overlay `<svg>`: `:302`–`:380`. `transform` string built
  at `:254`. `dispW`/`dispH` (display image dims, falsy-fallback to data dims)
  `:95`–`:96`. `displayScale = dispW / data_width` `:233`.
- Linear path branch (canvas via `useImageRemap`): `:229`–`:230`, rendered
  `:341`–`:348`. NINA/`<img>` branch `:306`–`:340`.
- `fitScale` (object-contain) `:112`–`:115`; `geom` handed to gestures
  `:117`–`:127`. `zoomedIn` (`viewport.scale > fitScale + 1e-3`) comes back from
  the hook `:122` and sets `touchAction` `:294`.
- Controls exposed to the toolbar via `onControls` `:166`–`:174`
  (`fit`/`hundred`/`zoomIn`/`zoomOut`).
- The overlay `<svg>` viewBox is `0 0 dispW dispH` `:351`–`:357`; `ClipMaskLayer`
  mounted at `:358` with `active={clipActive}` computed `:235`–`:240`
  (`data_is_linear && full_well != null && overlays.clip && stats.max >=
  full_well`).
- `PreviewInfo.id` → display URL `u(\`/api/preview/${preview.id}\`)` `:181`.

**Gestures / zoom math** — `ui/src/components/preview/usePreviewGestures.ts`
- `viewport` is store-owned; the hook is a thin controller reading a ref
  (`:39`–`:44`). `zoomAt(scale, fx, fy)` keeps a container-space focal point
  stationary `:71`–`:86`. `clampScale` cap `= max(fitScale*8, 4)` `:51`–`:55`,
  `MAX_SCALE_MULT=8` `:30`. `clampPan` `:61`–`:68`. `setHundred` = `zoomAt(1, …)`
  `:93`–`:98` (scale 1 == display px, i.e. the ≤1400 base, NOT sensor 1:1).
- Wheel `:173`–`:180`, pinch `:116`–`:126`,`:147`–`:155`, dbl-tap/dbl-click
  Fit↔100% `:127`–`:138`,`:182`–`:187`, keyboard `+/-/0/1` + arrows
  `:206`–`:251`.

**Linear canvas remap** — `ui/src/components/preview/useImageRemap.ts`
- Loads the auto-stretched display base once per url, caches decoded pixels, and
  re-applies a 256-entry MTF LUT on stretch change, rAF-debounced `:56`–`:65`;
  drags run at ≤900px `:19`,`:79`–`:83`. `onReady(w,h)` callback exists but
  `PreviewStage` passes none. **This is the debounce/cache pattern to mirror for
  crop fetches.**

**Stretch model + the WYSIWYG contract** — `ui/src/components/preview/lut.ts` +
`server/astrodeck/imaging/processing.py`
- **Client stretch is display-domain**: it is a *further* MTF applied on top of
  the server's already-auto-stretched 8-bit base (`lut.ts:1`–`:10`).
  `effectiveLevels(s)` `:25`–`:36`: Auto → `{0, 0.5 - brightness*0.35, 1}`;
  Manual → `{black, mid, white}` (all display-domain 0..1, `types.ts:287`–`:289`).
- **Server stretch is linear-domain**: `stretch_with(data, black, mid, white)`
  (`processing.py:88`–`:100`) treats black/white as clip fractions of the full
  16-bit range and mid as the midtones balance in the clipped sub-range;
  `render.png` calls `to_png(arr, …, black, mid, white)` → `stretch_with`
  (`processing.py:144`–`:145`).
- **The bridge already exists**: `auto_levels(data)` `:51`–`:70` returns the
  **linear-domain** `(black, mid, white)` triple that reproduces `auto_stretch`
  via `stretch_with`; `PreviewInfo.auto_levels` carries it to the client
  (`types.ts:266`). `levels_to_mtf` `:73`–`:85` is the shared normalizer both
  sides agree on. **This is the key to WYSIWYG — see §1.5 and Decision A.**
- `/crop` auto-stretches its ROI (`to_png(crop, True, crop_width)` — no explicit
  levels, `app.py:2715`) — i.e. the crop is auto-stretched, **not** at the user's
  manual levels (Decision B).

**The download menu** — `ui/src/components/preview/PreviewToolbar.tsx`
- Menu container + items `:170`–`:253`; download-anchor idiom `:192`–`:200`
  (`<a role="menuitem" href={u(...)} download={...} onClick={() =>
  setDlOpen(false)}>`). Honest-disabled §11.8 spans `:211`–`:220`,`:242`–`:250`.
  `dlDisabled = id == null || linkDown` `:102`. Menu only renders when
  `dlOpen && id != null` `:190`. Zoom cluster `:107`–`:123`.
- Existing `shareQuery` lib helper pattern — `ui/src/lib/share.ts` (pure,
  URLSearchParams, tested). Mirror it for `/crop` + `/render.png` query building.

**Orchestrator** — `ui/src/components/preview/LivePreview.tsx`
- Owns `viewport`/`stretch`/`overlays` from the store `:48`–`:59`, wires the stage
  `:92`–`:107` and toolbar `:109`–`:122`, computes `clipAvailable` `:80`. `shown`
  precedence `:47`. `scalePct` `:78`.

**Backend routes** — `server/astrodeck/api/app.py`
- `/crop` `:2695`–`:2716`: clamps `x,y` into `[0, n-1]`, `w,h<=0` ⇒ rest of frame,
  ROI clamped inside sensor (no over-read), 1:1 (max_width = crop width). 404 when
  `entry is None or entry.linear is None`.
- `/render.png` `:2718`–`:2734`: full native res, `stretch_with(arr, black, mid,
  white)`, 404 same guard. Cache header `_PREVIEW_CACHE` (`max-age=3600`)
  `:2584`. Both gated `CAP_VIEW_PREVIEW`.

### 1.3 Novice experience (zero config)

- **Zoom just works.** Pinch/scroll/double-tap already zoom (unchanged). The
  *new* invisible behavior: once the user pushes **past display-native**
  (`viewport.scale > displayNativeScale`, §2.1), the visible box silently
  upgrades to real sensor pixels — a crisp `/crop` fades in over the CSS-upscaled
  canvas. No control, no mode, no label. It just stops being blurry. If the crop
  can't be fetched (old frame → 404, offline), the CSS upscale stays — **never a
  broken tile, never a blank** (mirrors Lane B, `PreviewStage.tsx:266`).
- **One obvious download.** The existing **Download ▾** menu gets one new
  top item on the linear path: **"Download full-res PNG"** — one tap, saves the
  picture exactly as stretched on screen, at full sensor resolution. Plain-copy
  title: *"The image as you see it, full resolution."* On NINA frames the item is
  honestly disabled (lock + "Full-res export needs linear data — this frame came
  from NINA") — same idiom as the existing FITS/PNG disabled spans.
- **Clip mask reads as a heat overlay, not homework.** When the user turns on the
  existing **Clip** toggle and the frame clips, they still get the amber frame
  immediately (unchanged, zero-latency honest indicator); *if* they've zoomed in,
  the actually-saturated pixels inside the viewport light up amber on top. No new
  toggle for novices — the existing Clip toggle simply got more truthful.

### 1.4 Advanced experience (progressive disclosure, collapsed by default)

The stage gains **one** new screen-space affordance, and it is **off by default**:

- **1:1 Loupe** — a small toggle button in the toolbar's zoom cluster (icon
  `focus`, label "1:1"), honest-disabled off the linear path. When on, a compact
  loupe panel (bottom-right chrome, like the existing selected-star readout
  `PreviewStage.tsx:407`–`:412`) shows a **sensor-1:1 crop centered on the cursor
  / viewport center**, plus the exact ROI: `x,y w×h @1:1` in mono tabular-nums,
  and the source `preview.id`. This is the expert's "am I actually in focus"
  tool. Collapsed/absent for novices — they never see it unless they open it.
- **ROI coordinates on the loupe** — raw sensor `x/y/w/h`, copy-on-click, so an
  expert can reason about a specific sensor region (e.g. a hot column).
- **Per-pixel saturation mask** already surfaces through the existing Clip
  toggle; the *advanced* nuance is a small "per-pixel" vs "frame" note in the
  clip chip when zoomed (the mask is exact only inside the fetched ROI; outside
  it, the honest frame border remains). No separate toggle — the disclosure is
  the zoom itself.

Everything advanced is additive chrome; the novice path (§1.3) never renders any
of it. No expert cap: the loupe follows the cursor and re-fetches on pan.

### 1.5 The WYSIWYG fidelity problem (the one real hazard)

The client stretch is display-domain (a remap **on top of** auto_stretch); the
server `render.png` is a single linear-domain `stretch_with`. Feeding the
client's `stretch.black/mid/white` straight into `render.png` would **not** match
the screen. The clean resolution (Decision A):

- **Auto mode (the default, ~95% of use):** on-screen linear = `auto_stretch`
  (client LUT is identity when Auto + brightness 0, `useImageRemap.ts:91`). The
  server reproduces `auto_stretch` exactly via `stretch_with(arr,
  *auto_levels)`. So the client sends **`preview.auto_levels`** (already in
  `PreviewInfo`, `types.ts:266`) — **byte-faithful WYSIWYG at full res**, no
  server change.
- **Auto + brightness nudge / Manual:** on-screen is a *composition*
  (`auto_stretch` then a display-domain MTF). A single `stretch_with` can only
  approximate it. Three options — see Decision A; recommendation is v1 ships the
  exact Auto path + a documented "high-quality bake" for Manual, with an exact
  full-res Manual path as a Python-only follow-up (compose mode in `to_png`, no
  wheel).

A shared pure helper `ui/src/lib/renderLevels.ts :: toRenderLevels(stretch,
preview)` centralizes this mapping (tested; §5).

---

## 2. Architecture

### 2.1 Pixel-peep zoom (the crop layer)

**Concept.** Add one screen-space `<canvas>`/`<img>` **crop overlay** inside the
`.preview-transform` layer, sitting *above* the base canvas but *below* the
overlay `<svg>`, covering only the current ROI rect (in display space). It paints
a 1:1 `/crop` PNG of that ROI. Because it lives in the same transform layer, it
pans/zooms in lockstep with everything else — the exact bug-avoidance reason the
single-transform layer exists (`PreviewStage.tsx:8`–`:10`).

**When to fetch.** Define `displayNativeScale = dispW / data_width` — the scale at
which one display pixel equals one *sensor* pixel would be `1 / displayScale`,
i.e. `data_width / dispW`. Concretely: the display base is downscaled by
`displayScale = dispW/data_width` (`PreviewStage.tsx:233`); once `viewport.scale >
1/displayScale` (roughly: the user has zoomed past the point where the display
base runs out of real pixels) the CSS upscale becomes visible and a crop is
worth fetching. Below that threshold the base canvas is already ≥ native — **no
crop, no request** (novice zoom "just works" with zero traffic).

**ROI computation (pure, tested — `ui/src/lib/cropRoi.ts`).** From
`viewport{scale,x,y}`, `stageSize{w,h}`, `displayScale`, and `data_width/height`,
compute the visible rectangle in **sensor** coordinates:
- Visible display-space box = inverse-transform the stage rect through
  `translate(x,y) scale(scale)` → `[dispX0,dispY0 .. dispX1,dispY1]`, clamped to
  `[0,dispW]×[0,dispH]`.
- Sensor ROI = divide by `displayScale` → `{x,y,w,h}` in sensor px, integer-
  rounded and clamped to `[0,data_width]×[0,data_height]` (the server clamps too,
  `app.py:2709`–`:2712`, but clamping client-side keeps the cache key stable and
  avoids requesting a 5000px-wide strip when only 400px is visible).
- **Cap the request area.** Never ask for more than ~the viewport's worth of
  sensor px (e.g. `min(roiW, ceil(stageW / scale / displayScale) )`) so a crop is
  bounded by what's on screen, not the whole sensor. This keeps PNG encode +
  transfer small even at 2× zoom on a 60MP sensor.

**Debounce + cache (mirror `useImageRemap`).** New hook
`ui/src/components/preview/useCropZoom.ts`:
- Debounce fetches to ~120–160ms of gesture-idle (pan/zoom settle) — do **not**
  fetch mid-pinch. Reuse the rAF/settle idea from `useImageRemap.ts:56`–`:65`.
- **Quantize the ROI** to a grid (e.g. round x/y/w/h to a 64 or 128px lattice
  and expand slightly past the viewport) so small pans reuse the same crop and
  the cache actually hits. Cache key = `${id}:${qx}:${qy}:${qw}:${qh}`.
- LRU cache of decoded crop object-URLs (cap ~8 entries; `URL.revokeObjectURL`
  on eviction). New frame `id` ⇒ drop crops for old ids (linear is retained for
  only 1–2 frames server-side, `app.py:2701`, so stale crops are useless).
- On 404 (frame's linear expired) or network error: keep the CSS-upscaled base,
  set no error chrome. Same "degrade to base, never break" as Lane B.
- Abort in-flight fetch on a newer ROI (AbortController) so a fast zoom doesn't
  stack requests.

**Render.** The crop overlay is one absolutely-positioned element at
`(dispX0, dispY0)` size `(roiW*displayScale, roiH*displayScale)` inside the
transform layer, `image-rendering: pixelated` so the honest sensor pixels aren't
re-smoothed, cross-fading in (respect `prefers-reduced-motion`, like the base
buffer swap `PreviewStage.tsx:56`–`:59`). It carries the same `.astro` night
tint class as the base canvas (`PreviewStage.tsx:341`–`:348`) so tint stays one
mechanism (§8 of the live-preview spec).

### 2.2 Full-res render export (the download)

- **Pure query helper** — extend the `share.ts` pattern:
  `ui/src/lib/renderLevels.ts :: renderQuery(stretch, preview)` builds
  `?black=&mid=&white=` from `toRenderLevels(stretch, preview)` (§1.5). In Auto:
  `preview.auto_levels`. In Manual: `effectiveLevels(stretch)` mapped to the
  linear contract (Decision A).
- **Menu item** — one new anchor in `PreviewToolbar`'s download menu, placed
  first (it's the primary "the picture" export), linear-path-gated:
  `<a href={u(\`/api/preview/${id}/render.png${renderQuery(stretch, preview)}\`)}
  download={\`astrodeck_${id}.png\`}>`. NINA ⇒ honest-disabled span (lock +
  reason), exactly like the existing PNG/FITS disabled spans
  (`PreviewToolbar.tsx:211`–`:220`).
- **Toolbar needs `stretch`** — currently the toolbar doesn't receive it. Thread
  `stretch` from `LivePreview` (`useStretch()` already read, `:49`) into
  `PreviewToolbar` props. Small, additive.
- `download` attribute makes it a save; `_PREVIEW_CACHE` lets a repeat export of
  the same levels hit cache. No client-side encode — the server bakes at native
  res, which is the whole point (the client canvas is capped at ≤1400).

### 2.3 Per-pixel clip mask (finish ClipMaskLayer)

The honest per-pixel mask needs the **linear pixels** of the region. Two-tier,
zero-regression:
- **Tier 1 (unchanged, always available):** the static amber hatch frame + tag
  when `stats.max >= full_well` (`ClipMaskLayer.tsx` today). This stays as the
  zoomed-out / no-crop truth — it costs nothing and never lies.
- **Tier 2 (new, when a crop is in hand):** when the crop layer (§2.1) has
  fetched the ROI **and** clip is on, derive a saturation mask from the crop and
  paint saturated pixels amber *inside the ROI only*. Because `/crop` returns an
  **auto-stretched 8-bit PNG**, not raw 16-bit, a pixel at/above full-well maps to
  8-bit 255 (auto_stretch clamps highlights) — so "== 255 in the crop" is a
  faithful saturated-pixel test for a mono/L PNG (Decision C discusses the OSC
  and near-clip nuance). The mask is computed on a worker-free small canvas
  (`getImageData` over the ROI, threshold, paint to an offscreen canvas, draw
  tinted) — the ROI is viewport-bounded so this is a few hundred k px at most.
- Mask canvas is drawn in the same transform layer, clipped to the ROI rect, amber
  at ~0.5 alpha, `image-rendering: pixelated`, static (no flashing — §11.4).
  Outside the ROI, Tier-1's frame still communicates "this frame clips."
- **Contract:** `ClipMaskLayer` gains an optional `cropPixels?: {data,
  x,y,w,h}`; when present and `active`, it paints per-pixel; else it falls back to
  the frame. Keeps the component honest and the caller in control (its existing
  design ethos, `ClipMaskLayer.tsx:6`–`:12`).

### 2.4 Data flow

```
LivePreview (owns viewport, stretch, overlays)
  └─ PreviewStage
       ├─ useCropZoom(preview.id, viewport, stageSize, displayScale, data dims)
       │     → { cropUrl, roi, cropPixels }   (debounced, cached, aborts)
       ├─ transform layer
       │     ├─ base canvas / img            (unchanged)
       │     ├─ CropOverlay  {cropUrl, roi}  (new; only when zoomed past native)
       │     └─ svg overlays
       │           └─ ClipMaskLayer {active, cropPixels}   (Tier 1 + new Tier 2)
       └─ (advanced) LoupePanel {roi, cropUrl}   (screen-space chrome, opt-in)
  └─ PreviewToolbar {..., stretch, loupeOn, setLoupeOn}
       └─ Download ▾ → "Download full-res PNG" → /render.png?renderQuery
```

---

## 3. Backend plan

**No required backend change for v1.** Both endpoints are complete and correct
for the Auto-mode WYSIWYG export, the crop zoom, and the clip mask.

**Recommended Python-only follow-up (Decision A, ships after v1, no wheel):** add
an exact full-res Manual export by teaching `to_png`/`render.png` a "compose"
mode — `auto_stretch(arr)` then apply the display-domain MTF from
`effectiveLevels` — so a Manual-mode export is byte-exact to screen at native
res. This is pure NumPy/Pillow in `processing.py` (mirrors the existing
`stretch_with` branch, `:144`–`:148`); **no Rust, no maturin wheel rebuild.**
Until then Manual export is a documented high-quality approximation (or uses the
canvas fallback in Decision A).

**Optional (Decision B):** a `?black&mid&white` on `/crop` so the loupe/crop can
match Manual levels instead of always auto-stretching. Also Python-only, additive
(same `stretch_with` the render route already uses). Recommend deferring — auto-
stretched crops are the right default for focus/pixel inspection.

---

## 4. UI plan (file-by-file, all additive)

1. **`ui/src/lib/cropRoi.ts`** (new, pure) — `visibleSensorRoi(viewport,
   stageSize, displayScale, dataW, dataH, cap)` → `{x,y,w,h}` clamped + capped;
   `quantizeRoi(roi, grid)` → lattice-snapped ROI + cache key. **Tested (§5).**
2. **`ui/src/lib/renderLevels.ts`** (new, pure) — `toRenderLevels(stretch,
   preview)` → `{black,mid,white}` (Decision A mapping) and `renderQuery(...)` →
   query string (mirrors `share.ts`). **Tested (§5).**
3. **`ui/src/components/preview/useCropZoom.ts`** (new) — debounce, quantize,
   fetch, LRU object-URL cache, AbortController, `getImageData` for `cropPixels`.
   Thin React glue over the two pure libs; **not DOM-tested** (logic lives in the
   libs).
4. **`ui/src/components/preview/CropOverlay.tsx`** (new, thin) — positioned crop
   `<img>`/canvas in the transform layer, pixelated, reduced-motion fade. Dumb
   render shell.
5. **`ui/src/components/preview/LoupePanel.tsx`** (new, thin, advanced) —
   screen-space 1:1 loupe + ROI readout, opt-in. Dumb render shell.
6. **`ClipMaskLayer.tsx`** (edit) — add optional `cropPixels` prop + Tier-2
   per-pixel paint; Tier-1 unchanged. The threshold/mask derivation is a **pure
   helper** `saturationMask(cropImageData, threshold)` in
   `ui/src/lib/clipMask.ts` (**tested**); the component just draws it.
7. **`PreviewStage.tsx`** (edit) — call `useCropZoom`, mount `CropOverlay` +
   (advanced) `LoupePanel`, pass `cropPixels` to `ClipMaskLayer`. Compute
   `displayNativeScale` threshold. Wire a `loupeOn` state (lifted or local).
8. **`PreviewToolbar.tsx`** (edit) — accept `stretch`; add "Download full-res
   PNG" menu item (linear-gated, honest-disabled on NINA); add "1:1" loupe toggle
   in the zoom cluster (honest-disabled off linear).
9. **`LivePreview.tsx`** (edit) — pass `stretch` to the toolbar; own `loupeOn` if
   lifted.
10. **`ui/src/types.ts`** — no new server contract fields needed (`auto_levels`,
    `data_width/height`, `full_well` all present). Add local prop types only.

---

## 5. LEAN test plan — estimated **net-new ≈ 7 tests**

Philosophy: pure logic in tested libs; render shells (`CropOverlay`,
`LoupePanel`, `useCropZoom`) stay thin and DOM-untested; parametrize related
cases into single tests; reuse existing fixtures. Split ~4 UI (Vitest) + ~3
server (pytest).

**UI — `ui/src/lib` (pure, the load-bearing math):**
1. `cropRoi.test.ts` — **one parametrized test** over a table of
   `(viewport, stageSize, displayScale, dataDims)` → expected `{x,y,w,h}`:
   covers (a) not-zoomed ⇒ full/why-no-crop, (b) zoomed+panned centered ROI,
   (c) edge/corner clamps (no over-read past sensor), (d) the viewport-area cap.
   Plus `quantizeRoi` lattice-snap + stable cache key. **1 test.**
2. `renderLevels.test.ts` — **one parametrized test**: Auto ⇒ emits
   `preview.auto_levels`; Auto+brightness and Manual ⇒ the Decision-A mapping;
   `renderQuery` string shape + omitted-when-default. Pins the WYSIWYG contract.
   **1 test.**
3. `clipMask.test.ts` — `saturationMask` thresholding: a synthetic ImageData with
   known 255 / near-255 / mid pixels ⇒ correct mask bitset; boundary at threshold.
   **1 test.**
4. `PreviewToolbar` integration — extend the **existing** toolbar test (reuse its
   fixtures): assert the render-PNG item is present + `href` carries the right
   query on a linear frame, and is the honest-disabled span (lock, not native
   `disabled`) on a NINA frame. **1 test** (added case, not a new file).

**Server — reuse the existing preview-route fixtures in the app test module:**
5. `/render.png` — **one parametrized test**: (a) linear frame + explicit
   `black/mid/white` ⇒ 200 `image/png`, full native width (assert PNG width ==
   `data_width`, i.e. no downscale), and pixels differ from auto (levels applied);
   (b) `auto_levels` params ⇒ output equals the auto-stretch bytes (WYSIWYG
   anchor); (c) NINA/no-linear ⇒ 404. **1 test.**
6. `/crop` — **one parametrized test**: (a) mid-frame ROI ⇒ 200 PNG of exactly
   `w×h` (1:1, no downscale); (b) out-of-range `x/y/w/h` ⇒ clamped, still 200, no
   over-read (size ≤ sensor); (c) `w=h=0` ⇒ rest-of-frame; (d) expired/NINA ⇒
   404. **1 test.**
7. WYSIWYG equivalence — **one focused test** asserting
   `stretch_with(arr, *auto_levels(arr)) ≈ auto_stretch(arr)` within tolerance
   (this is the contract the whole Auto-mode export rests on; pins it against
   drift). Pure `processing.py`, no HTTP. **1 test.**

If Decision A's compose mode ships later, add **1** server test for the compose
branch — counted with that follow-up, not v1.

**Net new: ~7** (4 UI incl. 1 extended existing, 3 server). No DOM/render tests
for the new components — their logic is entirely in tested libs.

---

## 6. Open decisions (each with a recommendation)

**A. Manual-mode WYSIWYG fidelity for `/render.png`.** The client stretch is
display-domain; the server is linear-domain single-pass. Options:
  - **A1 (recommend for v1):** Auto mode sends `preview.auto_levels` ⇒ exact
    full-res WYSIWYG (covers ~95%). Manual mode sends the `effectiveLevels`
    triple through `toRenderLevels` as a **high-quality approximation**, labeled
    honestly in the menu title as "full resolution" (not "pixel-identical").
    Ship UI-only, zero server change.
  - **A2 (recommend as fast follow, Python-only):** add a compose mode to
    `to_png`/`render.png` (`auto_stretch` then display MTF) ⇒ Manual becomes
    byte-exact too. No wheel. Small, isolated.
  - **A3 (fallback for reduced scope):** in Manual mode, export the client
    `<canvas>` via `toBlob()` — pixel-identical but capped at ≤1400px. Reject as
    the primary (defeats "full-res"), keep as an offline/degraded path.
  → **Recommendation: A1 now + A2 next.** Decisive, honest, and never blocks on a
  wheel rebuild.

**B. Should the crop/loupe honor Manual levels, or always auto-stretch?**
`/crop` currently always auto-stretches. → **Recommend always auto-stretch for
v1** — a pixel-peep for focus/noise/stars wants the neutral, consistent
auto-stretch regardless of the user's cosmetic Manual levels; it's also cheaper
(no per-request levels). Revisit only if users ask to peep *their* stretch (then
add `?black&mid&white` to `/crop`, Python-only).

**C. Saturation test on an 8-bit auto-stretched crop.** `/crop` returns L-mode
8-bit; auto_stretch clamps highlights so full-well pixels ⇒ 255. → **Recommend
threshold at 255 (== clipped) for the per-pixel mask**, and keep Tier-1's
`stats.max >= full_well` frame as the authoritative "this frame clips" signal.
Note the OSC caveat: the preview is a mono render of an OSC frame
(`PreviewStage.tsx:402`–`:404`), so the mask is per-*preview*-pixel, not
per-Bayer-channel — acceptable and honest for a saturation *indicator*. A raw
16-bit crop channel would be a future refinement (Decision B territory), not v1.

**D. Loupe placement & follow-cursor cost.** A cursor-following loupe re-fetches
on pan. → **Recommend viewport-center loupe** (re-fetch on pan-settle only, via
the same debounced crop the zoom layer already fetched — the loupe reuses the
cached crop, near-zero extra traffic). Cursor-follow is an expert nicety to add
later if requested.

**E. Zoom cap vs sensor 1:1.** `MAX_SCALE_MULT=8` caps zoom at `fitScale*8`,
which may still be below sensor-1:1 on a small sensor / large panel, or far above
it on a 60MP sensor. The crop layer decouples *inspection resolution* from
*zoom*: even at the 8× cap the pixels are real (crop-fed). → **Recommend leaving
the cap as-is** — the crop makes the cap about framing, not fidelity. Optionally
raise to `*12` later; not needed for correctness.

**F. Where does `loupeOn` live?** → **Recommend local `useState` in
`PreviewStage`** exposed to the toolbar via the existing `onControls` channel
(`PreviewStage.tsx:166`–`:174`) — it's ephemeral view state, not worth a store
slice (unlike `overlays`, which persists).

---

## 7. Risks

- **R1 — WYSIWYG drift in Manual (Decision A).** Mitigated by A1's honest copy +
  the §5.7 equivalence test pinning the Auto anchor; A2 closes it exactly.
- **R2 — Crop request storms on fast pinch.** Mitigated by debounce-to-settle +
  ROI quantization + AbortController + LRU cache. Never fetch mid-gesture.
- **R3 — Linear retention window (1–2 frames).** A crop/render can 404 on an
  older pinned frame. Mitigated: degrade to CSS-upscaled base + keep the Tier-1
  clip frame; **never** show a broken tile (Lane B parity). The download item can
  surface a one-line "this frame's full-res source expired — showing the latest"
  only if we later allow export off a pinned old frame; v1 exports the retained
  frame.
- **R4 — Memory on the Pi/phone.** Decoded crops are viewport-bounded and LRU-
  capped (~8); mask canvases are transient. No full-sensor client decode ever.
- **R5 — OSC mask semantics (Decision C).** Per-preview-pixel, not per-channel;
  documented as an indicator, not photometric truth.
- **R6 — Transform-layer alignment.** The crop overlay MUST live in the single
  `.preview-transform` layer (not screen space) or it will drift from the base on
  pan/zoom — the exact bug the shared layer was built to prevent
  (`PreviewStage.tsx:8`–`:10`). Enforced by construction (mount inside the layer).
- **R7 — Test-count creep.** Held to ~7 by putting all math in pure libs and
  parametrizing; the render shells are deliberately DOM-untested.

---

## 8. Out of scope (v1)

- Compose-mode exact Manual export (Decision A2 — fast follow).
- Manual-levels crops / raw 16-bit per-channel crops (Decision B).
- Cursor-following loupe (Decision D).
- Export of an *expired/pinned old* frame's full-res (retention-window bound).
- Any Rust/native change or wheel rebuild — none of v1 touches it.
