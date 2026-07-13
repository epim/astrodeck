# Atlas Reliability & Performance (Wave 1) — Design

**Requirements source:** `docs/superpowers/reviews/2026-07-12-atlas-plan-ux-review.md`
(commit 5eaa131) — grounded findings, file:line for every claim. User approved all three
waves 2026-07-12; Wave 1 has no open design questions. This spec turns Wave 1's nine
approved items into implementable design.

**Goal:** Make the Sky Atlas fast and trustworthy: panning tracks the pointer, the survey
image never vanishes, "Survey offline" heals itself, the optics warning tells the truth
and can be fixed in place, tooltips stop flickering app-wide, and the server survey proxy
stops being a 30-second cache-missing single point of stall.

**Non-goals (explicitly out of Wave 1):** rotation handle, Atlas search, recenter label,
`activePanel` (Wave 2); everything Plan/scheduling (Wave 3); HiPS tile rendering; multiple
upstream survey mirrors; `/api/framing/mosaic` (pure math, fast, untouched).

---

## 1. Client survey pipeline (SkyCanvas.tsx + AtlasView.tsx)

### 1.1 Loader: fetch + abort + generation guard (replaces `Image()` preload)

Replace the in-memory `Image()` double-buffer with a `fetch`-based loader:

- On settle (existing 300 ms debounce retained), `fetch(targetUrl, {signal})` with an
  `AbortController`; starting a new load aborts the previous in-flight one.
- Monotonic generation counter; `onload`/`onerror` handlers check
  `gen === latestGen` before touching state — stale responses can never overwrite
  `shownUrl` (fixes review Symptom 2 cause 3).
- Response body → `URL.createObjectURL(blob)` → set as `<img src>`; swap `shownUrl` only
  after the blob is ready (`img.decode()` or `onload` on the object URL). Revoke the
  previous object URL after swap.
- Read snapped-geometry response headers (§5.2): `X-Survey-Ra-Deg`, `X-Survey-Dec-Deg`,
  `X-Survey-Fov-Deg`. Record `shownMeta = {raDeg, decDeg, fovDeg}` alongside `shownUrl`
  — fall back to the requested values when headers are absent (older server).

### 1.2 Transform-pan during gestures (kills the per-pixel dimmer)

- `targetUrl` changing NO LONGER calls `setImgReady(false)`. `imgReady`/dimmer state is
  deleted outright (§1.4 replaces it with a corner spinner).
- During a gesture (and between settle and swap), the current `<img>` gets a CSS
  transform computed from `shownMeta` vs the live view center/fov:
  - `pxPerDeg = imgRenderedWidthPx / shownMeta.fovDeg`
  - `dx = wrapDeltaRaDeg(shownMeta.raDeg, view.raDeg) * cos(view.decDeg°) * pxPerDeg`
  - `dy` from dec delta; `scale = shownMeta.fovDeg / view.fovDeg`
  - `transform: translate(dx, dy) scale(s)`, `transform-origin: center`, no transition.
  - RA wrap handled at ±180° (`wrapDeltaRaDeg` returns the signed shortest delta).
  Small-angle linear approximation is acceptable; the transform is temporary until the
  settled fetch swaps in a correctly-centred image (which resets the residual transform,
  computed the same way — after §5.2 the residual is ≤ fov/40, a few px).
- The math lives in a pure module `ui/src/lib/surveyView.ts`
  (`surveyTransform(shownMeta, view, renderedWidthPx) -> {dx, dy, scale}` +
  `wrapDeltaRaDeg`) so it is testable via the repo's self-executing `npx tsx` convention.

### 1.3 Keep last good image (render gated on having an image, not on mode)

- Render rule becomes: show `<img>` whenever `shownUrl` exists AND the user has not
  explicitly chosen Schematic. The schematic gradient renders only when there is no image
  yet, or on explicit user choice. A transient fetch failure no longer unmounts a valid
  image (review Symptom 2 cause 1).
- Survey-source change (dropdown) clears `shownUrl`/`shownMeta` (a DSS2-color frame must
  not persist under a 2MASS selection).

### 1.4 Availability state machine (replaces the one-way `surveyDown` latch)

- `surveyDown: boolean` latch is replaced by `surveyDegraded: boolean` + retry
  bookkeeping in AtlasView (or a small hook owned by SkyCanvas — implementer's choice,
  but state must survive SkyCanvas remounts if it lives there today; follow the current
  ownership shape).
- On fetch failure: `surveyDegraded = true`, keep last image (§1.3), schedule an
  auto-retry of the same URL with exponential backoff 5 s → 10 s → 20 s → 40 s → cap
  60 s. On success anywhere: `surveyDegraded = false`, reset backoff.
- Any new settled view (center/fov/survey/stretch change) fetches immediately as today —
  user action is always a fresh chance; it does not wait for the backoff timer.
- `mode` no longer flips to `schematic` on failure — the fetch effect's
  schematic-early-return (`SkyCanvas.tsx:139-143`) therefore no longer strangles retries.
  Explicit "Schematic" in the survey dropdown remains a pure user choice.
- Banner: while degraded, one unobtrusive strip ("Survey unreachable — showing last
  image; retrying…" / "…showing schematic; retrying…" when no image yet). The sr-only
  live region keeps announcing state changes. Banner disappears on recovery.

### 1.5 Loading indicator

- The 55%-opacity full dimmer (`SkyCanvas.tsx:343-348`) is deleted. Replacement: a small
  corner spinner/badge shown only when a settled fetch has been in flight > 300 ms.
  Panning never dims the image.

## 2. Visibility fetch debounce (VisibilityPanel.tsx)

- 300 ms debounce (matching the survey debounce) on the `/api/visibility` fetch effect.
- `AbortController` per request; abort the in-flight request when a new one is scheduled;
  stale-guard responses by generation.
- Skip refetch when the rounded key is unchanged (round ra_hours to 3 dp, dec_deg to
  2 dp) so sub-pixel drift doesn't refire after settle.

## 3. Optics: truthful gating, in-place editing, one banner

### 3.1 Gate on camera-merged optics

- AtlasView computes optics ONCE from the merged source and passes results down. Merge
  order: `status.optics` (live, camera-merged — same source FocusView.tsx:76 already
  uses) → `config.optics_computed` (from `loadConfig()`) → raw `config.optics`.
- `SkyCanvas` receives `fov`/`haveOptics` (or the merged optics object) as props; its
  duplicate computation (`SkyCanvas.tsx:106-110`) is deleted. `framing.ts` helpers stay
  pure and unchanged in signature except as below.

### 3.2 Truthful warning + missing-field naming

- Add pure helper `missingOpticsFields(o): string[]` (in `ui/src/lib/framing.ts`,
  `npx tsx`-tested) returning the human names of whichever of the four fields are not
  yet positive: focal length, pixel size, sensor width, sensor height.
- The warning copy names exactly the missing fields (e.g. "Framing needs your optics:
  missing pixel size, sensor size") instead of always blaming focal length.

### 3.3 Edit pixel size + sensor dims in place

- Extend the existing Atlas inline optics editor (focal-length field →
  `PUT /api/optics` + `loadConfig()`, `AtlasView.tsx:184-214`) with three more numeric
  fields: pixel size (µm), sensor width (px), sensor height (px).
- Server semantics preserved: 0/empty = "use camera" (`config.py:66-71`,
  `hub.effective_optics()`). When the config value is 0 and the camera reports a value,
  the field shows the camera value as placeholder with a "from camera" chip; a manually
  entered value overrides; clearing back to empty returns to camera-sourced.
- If `PUT /api/optics` does not already accept these fields, extend it (server change,
  same validation style as focal length; 422 on negatives/NaN per existing convention).

### 3.4 One banner

- Keep the AtlasView header banner (`AtlasView.tsx:469-477`) as the single optics
  warning; delete the duplicate in-canvas banner (`SkyCanvas.tsx:435-439`).

## 4. Tooltip fix (ui.tsx — all ~24 sites inherit)

Rewrite `Tooltip`'s interaction model; `InfoDot` and all call sites unchanged:

- Switch trigger handlers to pointer events with `pointerType` discrimination:
  - **Mouse hover:** `pointerenter` (mouse) schedules open after ~100 ms (hover intent);
    `pointerleave` schedules close after ~250 ms; re-enter within grace cancels the
    close. Flicker from drifting off the 15 px halo dies here.
  - **Touch/pen tap:** toggles open/closed. Mouse-derived open logic never runs for
    synthesized events (`pointerType !== 'mouse'`), killing the tap open-then-toggle
    flash. While open via touch, a `pointerdown` outside (document listener) closes it.
  - **Keyboard:** `focus` opens, `blur` closes, `Escape` closes (retained).
- The plain `onClick` toggle for mouse is removed (hover already owns mouse); bubble
  stays `pointer-events-none`.
- Open/close decision logic extracted as a pure reducer
  (`ui/src/lib/tooltipMachine.ts`: `tooltipNext(state, event) -> state` where events are
  `enter-mouse | leave-mouse | tap | focus | blur | escape | open-timer | close-timer |
  outside`), `npx tsx`-tested; the component maps DOM events + timers onto the reducer.

## 5. Server survey proxy (survey.py)

### 5.1 Timeout budget

- `_TIMEOUT_S`: 15.0 → **6.0**. Attempts stay 2 with the 0.4 s inter-attempt sleep
  (transient TLS failures to CDS were observed live; one retry earns its keep). Worst
  case falls ~30.4 s → ~12.8 s, and single-flight (§5.3) stops pileup.
- 503 response shape unchanged (`{"detail":"survey unavailable","fallback":"schematic"}`).

### 5.2 FOV-scaled snapping with integer-bucket keys (fixes both cache-miss-every-pan
and the boundary float flip)

- Server snaps the REQUEST geometry to a grid before fetching upstream and before
  keying, so the cache key and the returned image always agree:
  - Center grid step = `fov / 20` (degrees, both axes; RA converted hours→deg first).
  - FOV snapped to a 2 % logarithmic grid: `fovIdx = round(ln(fov)/ln(1.02))`,
    `snappedFov = 1.02**fovIdx`.
  - Bucket indices are INTEGERS (`raIdx = round(raDeg / step)` etc.); the cache key is
    built from the integer indices + fovIdx + survey + stretch + width — never from
    reconstructed floats. This removes the double-bucketing float flip at boundaries
    (review Symptom 1 cause 3) because identical indices always produce identical keys.
  - Add a `v2` salt to the key so pre-change cache files are simply orphaned; existing
    TTL/size eviction cleans them. Cache directory unchanged.
- Response carries the snapped geometry: headers `X-Survey-Ra-Deg`, `X-Survey-Dec-Deg`,
  `X-Survey-Fov-Deg` (also on cache hits). The client compensates for the ≤ fov/40
  residual via §1.2's transform, so framing accuracy is exact.
- Result: revisiting anywhere within a bucket is a disk-cache hit (5-9 ms measured), and
  a typical pan session touches a handful of buckets instead of one key per 3 arcsec.

### 5.3 Single-flight per cache key

- Concurrent requests for the same key: exactly one goes upstream; the others wait and
  serve the cached file. Per-key lock registry guarded by a global lock, entries dropped
  after release (no unbounded growth). Match the file's existing sync/async style —
  grounded during planning, not guessed.

## 6. Perf hygiene (cheap wins only)

- `React.memo` on `FovOverlay`, `SurveyControls`, `VisibilityPanel` (and the SkyCanvas
  label layer if it is a component).
- Atlas-tree components stop calling the whole-object `useStatus()` selector
  (`store.ts:1174`); they select the narrow fields they render so WS status ticks stop
  re-rendering the whole Atlas tree. Scope: atlas components only — no store refactor.

## 7. Testing

- **Server (pytest, from `server/`):** snapping/bucket math (boundary determinism:
  adjacent floats inside one bucket → identical key; step scales with fov), snapped
  headers present on hit AND miss, `v2` key salt, single-flight (two concurrent
  requests, monkeypatched upstream counts one call), timeout constant, 503 shape,
  eviction untouched.
- **UI (`npx tsx` self-executing asserts — NO vitest):** `surveyView.ts` transform math
  (RA wrap ±180°, cos-dec scaling, zoom scale), `tooltipMachine.ts` reducer (tap toggles
  once; mouse enter→leave→re-enter within grace stays open; escape closes),
  `missingOpticsFields` naming.
- **Build:** `npm run build` green from `ui/`.
- Full server suite green before each commit lands (repo convention).

## 8. Compatibility

- Headers are additive; the client falls back to requested-geometry when absent, so
  client and server halves can land in either order within the wave.
- No API shape changes; `PUT /api/optics` gains optional fields only if it lacks them.
- Old survey cache entries orphaned by the `v2` salt age out via existing eviction.
