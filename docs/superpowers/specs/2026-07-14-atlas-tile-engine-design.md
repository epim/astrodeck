# Atlas Client-Side HiPS Tile Engine ("slippy sky") — Design Spec

**Date:** 2026-07-14
**Status:** Approved by user (design conversation 2026-07-14; user chose the tile
engine over margin/hybrid cutout approaches, and folding "Use camera FOV" into
the Lock toggle)
**Owner surfaces:** `server/astrodeck/catalog/` (tile route), `ui/src/lib/`
(healpix + mesh math), `ui/src/components/atlas/` (renderer + SkyCanvas +
controls)

## 0. Goal and context

Atlas panning today drags a static, viewport-sized cutout (blank beyond its
edges), waits 300 ms after release, then swaps one re-rendered frame. The user
wants map-grade smooth pan/zoom. Three approved changes:

1. **Client-side HiPS tile renderer** replaces the survey `<img>` in SkyCanvas:
   the browser fetches raw HiPS tiles from a new server tile route and warps
   them through the exact TAN projection on a WebGL canvas. Never blank at any
   drag speed (parent-tile upsampling), smooth zoom through the tile pyramid.
2. **Grab-the-sky drag on both axes** — the horizontal pan sign is inverted
   today (vertical is already correct): dragging right must pull the sky right,
   revealing what lies to the left. Fix in the tile engine's drag handler AND
   in the legacy `panTo` (schematic + fallback paths agree).
3. **Controls cleanup** — the "Use camera FOV" button is removed; the Lock
   toggle absorbs it (on → save current zoom, apply camera FOV ×1.6; off →
   restore the saved zoom). The stretch Linear/Asinh buttons are removed
   (tile JPEGs are pre-stretched; the control has nothing left to affect).

Bonus consequence: on-demand tile caching makes the offline pack **grow with
use** — online deep zooms pull order 5–9 tiles through our server, which
persists them into the pack tree. The hips2fits cutout path is no longer used
by the Atlas (it remains, untouched, as the automatic fallback when WebGL is
unavailable — and its routing/tests keep passing).

### Constraints carried forward (frozen)

* Offline-first: with `config.survey.online_fetch = false`, NO code path may
  make an upstream network request. Pack tiles (orders 0–4) serve offline;
  deeper/missing tiles 404 and the client upsamples parents.
* The existing `/api/survey/cutout.jpg` route, `survey.py` routing, cache keys,
  and all their tests stay byte-for-byte untouched.
* View is North-up; the sky never rotates — the FOV rectangle rotates above it
  (FovOverlay SVG unchanged, still sits on top of the survey layer).
* Pack location `CAPTURE_DIR/_survey_pack/<slug>/`; `_evict_cache` never
  touches it.
* Night dimmer (`imageBrightness`) applies to the canvas exactly as it did to
  the `<img>` (CSS `filter: brightness(...)` on the element).

## 1. Server: tile route + on-demand pack growth

New route in `server/astrodeck/catalog/survey_pack.py`'s API surface (route
hosted in `api/app.py` if RBAC parity demands, else module router — follow the
cutout route's placement convention, which is an APIRouter in the catalog
module with NO auth dependency; tiles match the cutout route's auth posture).

`GET /api/survey/tile/{slug}/{order}/{npix}.jpg`

* **Slug registry** (extends `PACK_SLUGS` with per-slug mirror bases):
  * `dss2color` → survey `CDS/P/DSS2/color`, mirrors: ESA `https://skies.esac.esa.int/DSSColor`,
    CDS `https://alasky.cds.unistra.fr/DSS/DSSColor`, `https://alaskybis.cds.unistra.fr/DSS/DSSColor`.
  * `dss2red` → `CDS/P/DSS2/red`, mirrors: ESA `https://skies.esac.esa.int/DSS2Red`
    (VERIFY path at implementation; ESA slugs differ from CDS), CDS
    `https://alasky.cds.unistra.fr/DSS/DSS2Merged`… — the exact red/2MASS
    mirror paths MUST be verified against each mirror's `properties` file at
    implementation time; the registry structure is the requirement, the two
    non-color path strings are placeholders to verify (color's are verified).
  * `twomass` → `CDS/P/2MASS/color`, mirrors: CDS `https://alasky.cds.unistra.fr/2MASS/Color`
    (+ ESA equivalent, same verify note).
* **Validation:** slug must be in the registry (404 otherwise); `0 ≤ order ≤ 9`;
  `0 ≤ npix < 12·4^order` (422 outside).
* **Flow:** pack file exists → `FileResponse` with
  `Cache-Control: public, max-age=31536000, immutable` (a tile of a fixed
  survey never changes). Missing + `online_fetch=false` → `404
  {"detail": "tile unavailable"}` with `Cache-Control: no-store`. Missing +
  `online_fetch=true` → fetch from the slug's mirror list (first mirror that
  answers; JPEG SOI check; one retry; 6 s timeout — tiles are small), atomic
  `.tmp`→`replace` write INTO the pack tree, serve. Upstream failure → the
  same 404 shape (client keeps parents; retries later).
* **Per-tile single-flight:** concurrent requests for the same missing tile
  coalesce (refcounted per-key asyncio lock — same pattern as
  `survey.py:_single_flight`; implement locally in the tile module, do not
  import survey.py's private helper).
* **Disk guard:** before writing an on-demand tile, if
  `shutil.disk_usage(pack_dir).free < 200 MB`, serve the fetched bytes
  WITHOUT caching (never fill a Pi card from browsing). Constant
  `_TILE_CACHE_MIN_FREE_BYTES = 200 * 1024 * 1024`.
* On-demand tiles live in the same `Norder{k}/Dir…/Npix….jpg` tree as fetched
  packs; `DELETE /api/survey/pack` removes them with the pack (dss2color); the
  red/2MASS trees are also children of `PACK_ROOT` and are removed by slug via
  the same `remove_pack(slug)` (no UI for that in v1 — CLI/manual only).
* **`fetching` note:** the tile route never touches `fetch_state` — bulk pack
  fetch progress and on-demand tile traffic are separate concerns.

## 2. Client: HEALPix math in TypeScript (`ui/src/lib/healpix.ts`)

Pure module, no DOM. Exports (exact signatures):

```ts
export function ang2pixNested(order: number, raDeg: number, decDeg: number): number;
// Sphere point of a FRACTIONAL position inside a nested pixel: the pixel's
// local (u,v) in [0,1]^2 (u,v = 0..1 across the pixel diamond, consistent
// with the tile's JPEG x/y axes per the server-proven conventions
// _X_FROM_EVEN_BITS=False, _JPG_FLIP_Y=False):
export function pixUV2ang(order: number, npix: number, u: number, v: number): { raDeg: number; decDeg: number };
export function parentOf(npix: number): number;           // npix >> 2
export function childUVRect(npix: number, levelsUp: number): { u0: number; v0: number; size: number };
// ^ where this pixel's [0,1]^2 lands inside its ancestor `levelsUp` levels up
//   (quadrant walk from the nested sub-bits) — used for parent-texture crops.
```

**Correctness anchoring (the risk center):** a committed Python generator
`server/scripts/gen_healpix_vectors.py` produces
`ui/src/lib/__tests__/healpix.vectors.json` from **astropy-healpix** (the
proven server truth) covering: ang2pix at orders 0–9 across equatorial band /
polar caps / band boundaries / RA wrap / both poles (≥200 cases), and
pixUV2ang at fractional (u,v) ∈ {0, 0.25, 0.5, 1} grid for pixels in every
base face (≥300 cases, tolerance 1e-9 deg on the generator side, 1e-6 in the
TS assert). The vectors file is committed; the tsx test fails if the TS port
disagrees with any vector. The generator also emits the tile-axis convention
cases proving (u,v) orientation matches the server renderer's within-tile
mapping (column←odd bits, row←even bits, no flip — pinned 2026-07-13 against
real CDS M31 imagery).

## 3. Client: tile view math (`ui/src/lib/tileView.ts`)

Pure module (npx-tsx testable). Exports:

* `tileOrderFor(fovDeg, viewportPx): number` — same rule as everywhere:
  smallest k with tile scale `(58.6324°·3600)/(512·2^k)` ≤ viewport scale
  `fovDeg·3600/viewportPx`, clamped to [0, 9].
* `visibleTiles(centerRaDeg, centerDecDeg, fovDeg, viewportPx, order): number[]`
  — sample a 24×24 grid over the viewport +15% margin, inverse-TAN
  (`deproject` from framing.ts) each sample to sky, `ang2pixNested`, dedupe.
  (At the selected order a tile spans ≥ ~500 px, so a 24×24 grid cannot skip
  one; the margin ring doubles as the prefetch set.)
* `tileMesh(order, npix, centerRaDeg, centerDecDeg, fovDeg, viewportPx):
  {positions: Float32Array, uvs: Float32Array, indices: Uint16Array}` — a 4×4
  subdivided quad (25 vertices, 32 triangles): vertex (u,v) →
  `pixUV2ang` → sky → `project()` (framing.ts) → screen px (North-up,
  East-left: `x = W/2 − xi·pxPerDeg`, `y = H/2 − eta·pxPerDeg`). Includes the
  RA-wrap guard project() already provides.
* `ancestorUV(npix, levelsUp, uvs): Float32Array` — rescale a tile's UVs into
  the ancestor's texture rect via `childUVRect` (for parent upsampling).

## 4. Client: renderer (`ui/src/components/atlas/TileEngine.tsx` + `ui/src/lib/tileGL.ts`)

* **`tileGL.ts`** — thin WebGL wrapper, the ONLY file that touches the GL API:
  context init (returns null on failure → caller falls back), one textured-quad
  shader pair, texture upload from ImageBitmap, GPU texture LRU (128), draw
  call taking the mesh buffers. Everything above it is pure/testable.
* **`TileEngine.tsx`** — the canvas component mounted by SkyCanvas:
  * Props: `{ centerRaDeg, centerDecDeg, fovDeg, slug, onlineFetch, brightness,
    onFirstTile(), onAllFailing() }`.
  * Loader: fetch queue prioritized by distance-from-center; concurrency 6;
    `AbortController` per tile, aborted when a tile leaves the visible+margin
    set; ImageBitmap LRU cache 256 entries; failed tiles negative-cached 45 s
    (404 offline is normal — do not hammer); a tile that arrives marks the
    scene dirty.
  * Render loop: `requestAnimationFrame`, dirty-flag driven (center/fov/prop
    changes and texture arrivals set dirty; idle frames draw nothing).
  * Draw pass per visible tile: bind the tile's own texture if loaded, else
    walk `parentOf` up to 5 levels for the nearest loaded ancestor and draw
    with `ancestorUV` crop (soft but never blank); no texture at any level →
    leave black this frame (arrives later).
  * devicePixelRatio-aware canvas sizing; CSS `filter: brightness(...)` for
    the night dimmer (no GL work).
  * `onFirstTile` fires once when the first texture draws (clears skeleton /
    degraded); `onAllFailing` fires when 8 consecutive fetches fail AND zero
    textures have ever drawn (drives the existing degraded banner semantics).
* **Prefetch:** the +15% margin ring from `visibleTiles` is already the
  prefetch set — no extra machinery.

## 5. SkyCanvas integration + drag

* `mode === "survey"` now mounts `<TileEngine …/>` when `tileGL` context init
  succeeds; on failure it mounts the EXISTING `<img>` cutout pipeline
  unchanged (loader, transform, backoff, snapped headers — all kept intact and
  passing their tests). A module-level capability probe runs once.
* **Drag:** pointer handlers stay in SkyCanvas (they also serve schematic and
  fallback modes). Grab-the-sky fix: in `panTo`, the horizontal line becomes
  `const dXiDeg = dxPx / cssPerDeg;` (sign flipped; vertical line unchanged) —
  comment updated to say "drag right pulls the sky right (map-style, both
  axes)". The tile engine consumes the same `onCenterChange` updates per
  pointermove — no debounce in the view loop (fetching has its own queue; the
  transform/mesh math is per-frame cheap).
* First-load skeleton: shown until `onFirstTile`; degraded banner driven by
  `onAllFailing` through the existing `onSurveyError`/`onSurveyLoad` callbacks
  (AtlasView machinery unchanged).
* The 300 ms settled-fetch debounce, snapped-header transform, and object-URL
  lifecycle remain ONLY in the fallback img path.

## 6. Controls cleanup (`SurveyControls.tsx`, `AtlasView.tsx`)

* Remove the "Use camera FOV" button. The **Lock** toggle behavior: on →
  store the current `fovZoomDeg` (new optional framing-session field
  `prev_zoom_deg`), then `onZoom(clamp(frameFovDeg * 1.6))`; off → restore
  `prev_zoom_deg` if present (clamped), else keep current zoom. While locked,
  the zoom stepper/wheel remain enabled (lock re-applies only on optics
  change, as today — no behavior change there beyond the apply/restore).
* Remove the stretch Linear/Asinh button group and its `stretch`/
  `onStretchChange` props end-to-end in SurveyControls; AtlasView stops
  passing them. The store/session `stretch` field and the server's `stretch`
  query param survive untouched (fallback path still sends whatever the
  session holds; default `linear`).
* Survey picker unchanged (incl. online-only gating). The tile engine maps
  survey id → slug via a UI-side mirror of the registry
  (`{"CDS/P/DSS2/color": "dss2color", "CDS/P/DSS2/red": "dss2red",
  "CDS/P/2MASS/color": "twomass"}`).

## 7. Testing

* **healpix.ts:** golden-vector tsx test (≥500 vectors from astropy-healpix,
  §2) — the load-bearing gate. Plus hand cases for `parentOf`/`childUVRect`.
* **tileView.ts:** tsx tests — `tileOrderFor` table cases; `visibleTiles` for
  a known view asserts the exact npix set (computed from the vectors);
  `tileMesh` center vertex lands at viewport center for the tile containing
  the view center; East-left/North-up orientation asserted via two known
  points; `ancestorUV` quadrant math hand cases.
* **Server tile route (pytest):** pack hit + immutable header; offline miss →
  404 no-store + NO client construction (the `_Boom` pattern); online miss →
  mirror fetch (injectable transport) + atomic write + served bytes + file
  present; single-flight coalescing; disk-guard path serves without writing
  (monkeypatched `disk_usage`); slug/order/npix validation (404/422); the
  full existing survey/cutout test files still pass untouched.
* **Visual parity dev-gate (REQUIRED, like the offline-pack renderer's
  ground-truth step that caught both bit-convention errors):**
  during development, render the M31 view via the tile engine in a headless
  probe OR compare `tileMesh`+`pixUV2ang` output against
  `hips_local.render_cutout`'s sampling for ≥3 known pixels; and the
  implementer views a browser screenshot against the server render. The
  screen-orientation assertions in tileView tests carry the automated burden.
* **GL layer:** `tileGL.ts` is exempt from unit tests (thin wrapper); all
  logic above it is pure and tested. `npm run build` green.

## 8. Out of scope (v1)

* Pinch-zoom gestures; inertial pan; sky-rotation rendering; crossfade
  between surveys; tile-tree eviction policy beyond the 200 MB disk guard
  (Settings delete remains the relief valve); Settings UI for red/2MASS tile
  trees; retiring the cutout route (it is the fallback and other-consumer
  surface).
