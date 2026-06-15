# AstroDeck — Sky Atlas + Framing Assistant + Mosaic + Visibility (build-ready spec)

**Date:** 2026-06-15
**Surface owner:** Atlas (new top-level view)
**Status:** final, build-ready. Resolves the three adversarial UX critiques (dispositions in §13).
**Review source:** `docs/reviews/2026-06-15-ux-panel-review.md` (Tier-1 #4 framing/mosaic, Tier-1 #9 visibility, P0 below-horizon, P0 touch ≥44px, P1 accessibility/night-mode).

---

## 1. Overview

A new top-level view, **Atlas** (`view: "atlas"`), reached from (a) the nav rail/overflow, (b) a per-row "Frame" action in Mount and Plan, or (c) the Plan header. It overlays the chosen target on a real survey cutout, draws a draggable + rotatable sensor-FOV rectangle (or mosaic grid) computed from the optics, generates mosaic panels with correct deprojected RA/Dec, and shows tonight's altitude/transit/moon/best-window. Panels and single frames hand off to the existing sequencer as `Target[]`.

Four cooperating regions share one piece of state, a **FramingSession** (center + rotation + mosaic grid + survey + zoom):

1. **Sky view** — a survey image cutout centered on the target (our own SVG coordinate frame on top).
2. **FOV overlay** — sensor rectangle / mosaic grid, draggable + rotatable, in pure SVG we control.
3. **Mosaic planner** — rows × cols × overlap% → server-canonical panel list → "Send to Plan".
4. **Visibility planner** — altitude-over-time tonight, transit, astro-dark window, moon track/phase/separation, recommended order.

### Key architecture decisions (verified against the code)

- **Survey image: backend-proxied static HiPS cutout via CDS `hips2fits`**, rendered to JPEG, displayed as a plain `<img>` under an SVG we fully own. Not Aladin Lite (400 KB CDN JS, couples our overlay to its projection, hard to red-night-theme, breaks offline-at-the-scope). Not raw HiPS tile streaming (reimplements a tile pyramid). The cutout is requested in **TAN (gnomonic) projection** centered on the target, so the deg→px scale is linear and uniform and our SVG FOV rectangle maps to it with exact, dependency-free math. Proxying sidesteps browser CORS, gives us a disk cache, and degrades to an offline schematic.
- **`httpx` and `astropy` are already hard dependencies** (`server/pyproject.toml` lines 7,9 — verified). So: the survey proxy uses `httpx.AsyncClient` (no new dep), and **all ephemeris math (sun/moon/transit/twilight/moon-phase) uses `astropy.coordinates` + `astropy.time`** rather than hand-rolled formulae. This is the single biggest correctness change vs. the draft (which proposed a hand-rolled `catalog/moon.py` to "keep the Pi image light" — unnecessary; astropy is already shipped).
- **The mosaic engine is server-canonical** (`catalog/framing.py`, `POST /api/framing/mosaic`); the client mirrors the same math in `lib/framing.ts` for zero-latency live overlay during drag, but "Send to Plan" always routes through the server so the slew targets handed to the engine are byte-identical to the preview.
- **Plan state is lifted into the Zustand store** as the single source of truth from day one (not the localStorage-race interim the draft proposed). SequenceView reads/writes `store.plan`; `addTargetsToPlan` mutates the same store slice. This kills the data-loss race (critique C1-C1 / C2-#10).
- **Rotation is honest.** There is no rotator role in the device layer, and the engine's `_setup_target` only calls `goto_and_center(ra, dec)` (verified `engine.py:186` — no rotation applied). So Atlas does **not** silently promise framing the rig can't reproduce. Instead it uses the rotation the solver already returns (`SolveResult.rotation_deg`, verified `solve/base.py:13`): the FOV rectangle's rotation becomes a **target camera angle** stamped on the Target, and at send-time the UI surfaces "rotate camera to N° (manual)" guidance. Mosaic overlap defaults to **25%** so panels still tile without a rotator. (Disposition: critique C1-B4 / C2-#1.)

---

## 2. Disjoint-file ownership (parallel implementers don't collide)

Each implementer owns a disjoint set of files. Shared contracts (§4) are frozen first by Owner A so B–E compile against stable types/endpoints.

| Owner | Scope | Files (exclusive) |
|---|---|---|
| **A — contracts & store** | types, store plan-lift + framing slice, optics endpoint, hub.optics, Target model field, design tokens | `ui/src/types.ts`, `ui/src/store.ts`, `ui/src/index.css`, `ui/src/components/ui.tsx`, `server/astrodeck/hub.py`, `server/astrodeck/sequence/models.py`, `server/astrodeck/api/app.py` (only the new routes block + summary), `server/astrodeck/persist.py` (new) |
| **B — sky canvas + math** | survey proxy, projection/FOV math, canvas + overlay + survey controls | `ui/src/lib/framing.ts`, `ui/src/components/atlas/SkyCanvas.tsx`, `ui/src/components/atlas/FovOverlay.tsx`, `ui/src/components/atlas/SurveyControls.tsx`, `server/astrodeck/catalog/survey.py` |
| **C — mosaic** | server mosaic compute, mosaic panel UI, plan hand-off, Plan group display | `ui/src/components/atlas/MosaicPanel.tsx`, `server/astrodeck/catalog/framing.py`, `ui/src/views/SequenceView.tsx` (group rendering only — coordinate the plan-lift with A) |
| **D — visibility** | astropy ephemeris, visibility panel + curve | `ui/src/lib/visibility.ts`, `ui/src/components/atlas/VisibilityPanel.tsx`, `server/astrodeck/catalog/visibility.py` |
| **E — page + nav + entry points** | the page shell, nav wiring, Mount/Plan entry buttons | `ui/src/views/AtlasView.tsx`, `ui/src/App.tsx`, `ui/src/views/MountView.tsx` (Frame button only) |

**Shared-edit coordination notes**

- `app.py` and `SequenceView.tsx` are touched by more than one owner. Resolve by: A adds the optics/summary block and registers C's and D's routers via `app.include_router(...)`; B/C/D each ship a **separate APIRouter module** (`survey.py`/`framing.py`/`visibility.py` expose `router`), so no two owners edit the same function in `app.py`. SequenceView's plan-lift (A) and group rendering (C) are split: A lands the `usePlan` store hook and the `Target` extra fields first; C only adds the group header/collapse block.
- `catalog/__init__.py` is appended-to by B/C/D — each adds one export line; merge is trivial (additive).

---

## 3. Component / file plan

### Frontend — new (`ui/src`)

- `views/AtlasView.tsx` — page shell; owns `FramingSession` via store; layout (desktop split / phone pinned-canvas); inline focal-length field; data fetching orchestration.
- `components/atlas/SkyCanvas.tsx` — survey `<img>` (double-buffered) + SVG overlay + HTML label layer; pan/zoom/drag/rotate; keyboard nudge; schematic fallback mode.
- `components/atlas/FovOverlay.tsx` — pure SVG: sensor rectangle(s), mosaic grid, rotation handle, active-panel emphasis (stroke + corner ticks, **not** translucent fill), black-halo strokes.
- `components/atlas/MosaicPanel.tsx` — rows/cols/overlap (44px steppers) + total-FOV + integration-vs-window reality check + generated panel list + "Send to Plan".
- `components/atlas/VisibilityPanel.tsx` — altitude curve (`<path>` memoized) + transit/moon/window readouts (glyph+text) + "recommended order".
- `components/atlas/SurveyControls.tsx` — survey picker, stretch toggle, FOV zoom + "fit object", recenter, center-nudge buttons, "use camera FOV" lock, image dimmer.
- `lib/framing.ts` — pure math (gnomonic project/deproject with ρ→0 guard, FOV-from-optics, mosaic grid → RA/Dec with `ra % 24` wrap). No React; unit-tested.
- `lib/visibility.ts` — formatting only (server is source of truth): VisibilityNight → SVG path strings, time formatting, status-glyph helpers.

### Frontend — modified

- `App.tsx` — register `atlas` in `VIEWS`; add to nav with a Lucide icon (`telescope`); IA slot **immediately before Plan**: `Rig → Align → Mount → Focus → Capture → Guide → Atlas → Plan → Power`. Mobile bottom-nav stays at its current item budget — Atlas is reached via the **"More ⋯" overflow sheet** there (and contextual Frame buttons), not a 9th cramped slot.
- `store.ts` — add `"atlas"` to `ViewName`; **lift the plan** into the store (`plan`, `setPlan`, `usePlan`); add `settings` (optics+site cache from `/api/summary`); add `framing` slice + `openFraming`/`setFraming`; add `addTargetsToPlan`; add `atlasHandoff` flag.
- `types.ts` — add `Optics`, `FovRect`, `MosaicSpec`, `MosaicPanel`, `MosaicResult`, `FramingSession`, `MoonInfo`, `VisibilitySample`, `VisibilityNight`, `VisibilityTarget`; extend `Target` with `rotation_deg?`, `mosaic_group?`.
- `components/ui.tsx` — `Stat` gains a `glyph?` + `hint?` channel (status by shape/text, not color alone); `Toggle` hit area padded to 44px (visual stays 36px); new `Stepper` and `IconButton` (≥44px) primitives.
- `index.css` — add `--survey-filter` (separate from `--img-filter`), `:focus-visible` global ring, `.btn-touch`/`.stepper` (≥44px), bump `--text-dim` to meet AA, `.svg-halo` utility for stroke halos.
- `views/MountView.tsx` — add a small "Frame" `IconButton` per catalog row (≥44px) calling `openFraming(entry)`.
- `views/SequenceView.tsx` — read `plan` from the store (was `useState`); render a `mosaic_group` collapsible header with per-group rollup + delete-group; show `rotation_deg` chip + a one-shot "N panels added from Atlas" banner keyed off `atlasHandoff`.

### Backend — new (`server/astrodeck`)

- `catalog/survey.py` — `APIRouter`; `hips2fits` proxy + disk cache + 503 fallback.
- `catalog/framing.py` — `APIRouter`; `MosaicSpecIn` model + `compute_mosaic()` (canonical panels, `ra % 24` wrap, ρ→0 guard).
- `catalog/visibility.py` — `APIRouter`; astropy-based night sampler, transit (sample-max within dark window), twilight + darkness fallback, moon track/phase/separation/rise-set, best-window scoring, recommended order.
- `persist.py` — tiny JSON store for `{site, optics}` (off the async loop via `asyncio.to_thread`); shared by site + optics so both survive restart.

### Backend — modified

- `hub.py` — add `self.optics = {...}` seeded from the connected camera (`pixel_size_um`, `sensor_width`, `sensor_height` already exist on `Camera`, verified `devices/base.py:81-83`); `focal_length_mm` user-set. `summary()` already returns `site` (verified `hub.py:155`); **add `"optics": self.optics`** to it. Load `{site,optics}` from `persist.py` on init.
- `sequence/models.py` — `Target` gains `rotation_deg: float | None = None` and `mosaic_group: str | None = None` (both optional/nullable — existing plans deserialize unchanged).
- `api/app.py` — `include_router` for survey/framing/visibility; add `POST /api/optics`; surface `optics` from summary.
- `catalog/__init__.py` — append exports `survey_router`, `framing_router`, `visibility_router`, `compute_mosaic`.

---

## 4. Shared contracts

> Owner A freezes this section first. Everything else compiles against it.

### 4.1 TypeScript types (`ui/src/types.ts`)

```ts
export interface Optics {
  focal_length_mm: number;   // user-set or back-computed from a solve
  pixel_size_um: number;     // from camera
  sensor_width: number;      // px, from camera
  sensor_height: number;     // px, from camera
  name?: string;
}

export interface FovRect {                 // single sensor footprint
  ra_hours: number; dec_deg: number;
  fov_x_deg: number; fov_y_deg: number;
  rotation_deg: number;                    // position angle, N-up E-left
}

export interface MosaicSpec {
  ra_hours: number; dec_deg: number;
  rows: number; cols: number;              // 1..10
  overlap: number;                         // 0..0.5 (default 0.25)
  rotation_deg: number;                    // PA applied to whole mosaic
  fov_x_deg: number; fov_y_deg: number;    // single-frame FOV at bin 1
}

export interface MosaicPanel {
  row: number; col: number;
  ra_hours: number; dec_deg: number;       // server returns ra already %24-wrapped
  rotation_deg: number;
  transit_alt?: number;                    // peak alt tonight (NOT instantaneous "now" alt)
}

export interface MosaicResult {
  panels: MosaicPanel[];
  total_fov_x_deg: number; total_fov_y_deg: number;   // tangent-plane extent
  frame_fov_x_deg: number; frame_fov_y_deg: number;
  pixel_scale_arcsec: number;
}

export interface FramingSession {
  target?: CatalogEntry;                   // origin object (undefined = free-roam)
  center: { ra_hours: number; dec_deg: number };
  rotation_deg: number;
  survey: string;                          // "CDS/P/DSS2/color" | "CDS/P/DSS2/red" | "CDS/P/2MASS/color"
  stretch: "linear" | "asinh";
  fovZoomDeg: number;                      // survey crop angular width
  mosaic: { rows: number; cols: number; overlap: number };
  panels: MosaicPanel[];                   // generated; length 1 when 1x1
}

export interface MoonInfo {
  illumination: number;                    // 0..1
  phase_name: string;                      // "Waning Gibbous"
  alt: number; az: number;                 // at session time
  separation_deg: number;                  // from target
  rise_unix: number | null;                // tonight, null if always up/down
  set_unix: number | null;
}

export interface VisibilitySample { t_unix: number; alt: number; moon_alt: number; sun_alt: number; }

export interface VisibilityNight {
  date: string;
  transit_unix: number; transit_alt: number;          // peak within dark window (see §9)
  transit_in_daylight: boolean;                        // true if geometric transit is in daylight
  dark_start_unix: number | null;                      // astro-dark start (null if none)
  dark_end_unix: number | null;
  darkness_kind: "astronomical" | "nautical" | "none"; // fallback ladder
  samples: VisibilitySample[];
  moon: MoonInfo;
  best_window: { start_unix: number; end_unix: number; mean_alt: number } | null;
  alt_limit_deg: number;                               // horizon limit (default 30)
  never_rises_above_limit: boolean;
}

export interface VisibilityTarget {
  name: string; ra_hours: number; dec_deg: number;
  transit_unix: number; max_alt: number;
  best_window: { start_unix: number; end_unix: number } | null;
  moon_sep_deg: number;
}
```

Extend `Target` (both TS and pydantic):

```ts
export interface Target {
  /* …existing… */
  rotation_deg?: number;    // target camera angle (PA) — guidance only, no rotator in rig
  mosaic_group?: string;    // e.g. "M31" to group panels in the Plan UI
}
```

### 4.2 Store slices (`ui/src/store.ts`)

```ts
view: ViewName;                            // + "atlas"

// plan lifted into the store — SINGLE SOURCE OF TRUTH (kills the localStorage race)
plan: SequencePlan;                        // initialized from loadPlan(); persisted via subscribe
setPlan: (p: SequencePlan) => void;        // writes localStorage["astrodeck-plan"] in the setter

settings: { optics: Optics | null; site: { latitude: number; longitude: number } | null };
setSettings: (s: Partial<AppState["settings"]>) => void;   // populated from /api/summary "hello" + on demand

framing: FramingSession | null;
openFraming: (e?: CatalogEntry) => void;   // view="atlas"; seed center from entry, FOV from settings.optics
setFraming: (patch: Partial<FramingSession>) => void;

atlasHandoff: number;                      // bump to flash "added to plan" banner in SequenceView
addTargetsToPlan: (targets: Target[], group?: string) => void;
```

`addTargetsToPlan` rules (resolves dedupe critique C1-C2):
- Operates on `store.plan` directly (no localStorage round-trip).
- **Replace-by-group, not drop-by-name:** if any incoming target carries `mosaic_group === group`, remove all existing targets with that `mosaic_group` first, then append the new set (so re-framing the same object **replaces** its panels instead of silently no-op'ing). Single (1×1) frames with no group append normally; a same-named single target prompts replace-vs-add.
- After mutate, bump `atlasHandoff`.

`setPlan` persists to `localStorage["astrodeck-plan"]` synchronously inside the setter. SequenceView and Atlas both read `usePlan()`; no component holds its own copy.

### 4.3 REST endpoints

All survey/ephemeris external calls are proxied server-side (no browser CORS).

**`GET /api/survey/cutout.jpg`** (Owner B)
Query: `ra` (**hours**), `dec` (deg), `fov` (deg crop width), `width` (px, default 768, clamp 256–1200), `survey` (default `CDS/P/DSS2/color`), `stretch` (`linear`|`asinh`, default `linear`).
Backend builds (note `ra_deg = ra_hours * 15`):
```
GET https://alasky.cds.unistra.fr/hips-image-services/hips2fits
    ?hips={survey}&ra={ra_deg}&dec={dec}&fov={fov}&width={w}&height={w}
    &projection=TAN&coordsys=icrs&format=jpg&stretch={stretch}
```
- **No `rot` / no PA param.** hips2fits does not rotate the output frame by PA (`coordsys` only selects ICRS/Galactic); the image is always North-up and our SVG rectangle rotates over it. (Resolves critiques C1-A4 / C2-#8.)
- `httpx.AsyncClient(timeout=15)`, one retry, `User-Agent: AstroDeck/0.1`.
- Disk cache `captures/_survey/{sha1(params)}.jpg`, `Cache-Control: max-age=86400`. Cache key quantizes `ra→0.0002h (~3")`, `dec→0.002°`, `fov→0.01°` — **finer than the draft** so a fine-framing nudge doesn't return a stale image while the overlay moves (resolves critique C2-#15). During an interactive drag the client requests un-quantized and debounces 300 ms; quantization is only the disk-cache key.
- On upstream failure → `503 {"detail":"survey unavailable","fallback":"schematic"}`. Client switches to schematic mode; inline banner, no toast.

**`POST /api/framing/mosaic`** (Owner C)
Body `MosaicSpec` → `MosaicResult`. Canonical panel coordinates; `ra` already `% 24`-wrapped; ρ→0 center special-cased. Optional `date?` so each panel's `transit_alt` is filled from the visibility module.

**`GET /api/visibility`** (Owner D)
Query: `ra`, `dec`, `date?` (defaults tonight at `hub.site`), `step_min?` (default 10), `alt_limit?` (default 30).
Returns `VisibilityNight`. Uses astropy (`AltAz`, `get_body("moon")`, `get_sun`) — see §9.

**`POST /api/visibility/order`** (Owner D)
Body `{ targets: {name,ra_hours,dec_deg}[], date? }` → each annotated `VisibilityTarget` + `recommended_order: number[]`. Mosaic groups are kept atomic (see §7/C4).

**`POST /api/optics`** (Owner A)
Body `Optics` → sets `hub.optics`, persists via `persist.py`. If the camera is connected, `pixel_size_um`/`sensor_width`/`sensor_height` default from it; the user supplies only `focal_length_mm`. `GET /api/summary` returns `optics` and `site`.

### 4.4 Backend model fields

- `hub.optics = {"focal_length_mm": 0.0, "pixel_size_um": 0.0, "sensor_width": 0, "sensor_height": 0, "name": ""}`; seeded on camera connect; surfaced in `summary()`.
- `MosaicSpecIn(BaseModel)` mirrors `MosaicSpec`.
- `Target.rotation_deg: float | None = None`, `Target.mosaic_group: str | None = None`.
- **`Target.ra_hours` keeps `Field(ge=0, lt=24)`** — so `compute_mosaic` MUST emit `ra % 24` (panels near M31 at 0.71h otherwise deproject to ≥24h or <0 and 422 the whole plan — verified `models.py:19`). The client mirror wraps identically. (Resolves critique C2-#2.)

---

## 5. Core math (`lib/framing.ts` ⇄ `catalog/framing.py`, mirrored)

### FOV from optics — always at **bin 1** for panel generation

```
fov_x_deg = degrees( 2 * atan( (sensor_width_px  * pixel_size_um/1000) / (2 * focal_length_mm) ) )
fov_y_deg = degrees( 2 * atan( (sensor_height_px * pixel_size_um/1000) / (2 * focal_length_mm) ) )
pixel_scale_arcsec = 206.265 * pixel_size_um / focal_length_mm
```
Binning affects only the **displayed** pixel-scale readout; **panel tiling always uses bin-1 FOV** (resolves critique C2-#14). Guard: `focal_length_mm <= 0 || pixel_size_um <= 0` → FOV unknown → dashed placeholder + inline CTA (§6 states).

**Plausibility hint (resolves critique C1-D1):** SkyCanvas/SurveyControls show the resulting `fov_x' × fov_y'` and `pixel_scale "/px"` prominently with a sanity note — `< 0.7"/px → "very high resolution"`, `> 5"/px → "very wide field"`, typical 1–4"/px — so a focal-length typo (800 vs 80) is visible immediately.

### Gnomonic (TAN) projection — sky ↔ tangent-plane, with ρ→0 guard

Forward (sky → standard coords ξ,η in degrees, relative to center ra0,dec0):
```
h  = sin(dec)·sin(dec0) + cos(dec)·cos(dec0)·cos(ra−ra0)
ξ  =  cos(dec)·sin(ra−ra0) / h
η  = (sin(dec)·cos(dec0) − cos(dec)·sin(dec0)·cos(ra−ra0)) / h
```
Inverse (ξ,η → sky), **center special-cased to avoid divide-by-zero NaN**:
```
ρ = sqrt(ξ² + η²)
if ρ < 1e-12:  return (ra0, dec0)          # <-- the common path: open on-target, hit Send
c  = atan(ρ)
dec = asin( cos(c)·sin(dec0) + η·sin(c)·cos(dec0)/ρ )
ra  = ra0 + atan2( ξ·sin(c), ρ·cos(dec0)·cos(c) − η·sin(dec0)·sin(c) )
ra  = ra % 24                              # normalize the wrap
```
(The ρ→0 guard resolves critique C1-A3 / C2 — without it "open on target, hit Send" ships NaN RA/Dec to the mount.)

Because the survey cutout is a TAN image of known angular width `fovZoomDeg` over `W×W` px, `px_per_deg = W / fovZoomDeg` is uniform. The FOV rectangle is drawn `fov_x·px_per_deg × fov_y·px_per_deg`, centered on the projected target, rotated by `rotation_deg`. This is the payoff of the controlled-projection cutout.

### Mosaic panel offsets

```
step_x = fov_x · (1 − overlap);  step_y = fov_y · (1 − overlap)
for r in 0..rows-1, c in 0..cols-1:
    gx = (c − (cols-1)/2) · step_x
    gy = ((rows-1)/2 − r) · step_y
    ξ  = gx·cosθ − gy·sinθ;  η = gx·sinθ + gy·cosθ      # θ = rotation_deg
    (ra, dec) = deproject(ξ, η, ra0, dec0)              # inverse-TAN above, ra already %24
    panel = { row:r, col:c, ra_hours:ra, dec_deg:dec, rotation_deg:θ }
order panels boustrophedon (snake) by row
total_fov_x = (cols − (cols-1)·overlap)·fov_x          # tangent-plane extent
total_fov_y = (rows − (rows-1)·overlap)·fov_y
```
Total FOV is presented as **tangent-plane extent** (what the deprojected panels actually cover), not raw degrees-of-RA, so high-dec mosaics (M81/M82 +69°, Cave +62°, half the catalog is dec>+57°) aren't mislabeled (resolves critique C2-#4, D3). The realized panel footprints are also drawn on the survey, which is the visual source of truth.

---

## 6. AtlasView layout, components, props, states

### Desktop (≥ lg)

```
grid lg:grid-cols-[1fr_380px]
┌──────── header: object name · [Focal length ___ mm] [calibrate from last solve] ───────┐
│  SkyCanvas (square, max 720px)        │  SurveyControls                                 │
│   survey <img> + SVG + HTML labels    │  MosaicPanel                                    │
│   SurveyControls overlay TL collapsed │  VisibilityPanel                                │
└───────────────────────────────────────┴─────────────────────────────────────────────────┘
```

**Self-contained optics (resolves critique C1-B1):** focal length is an inline field **in the Atlas header**, defaulting from the connected camera's pixel size + sensor px and the persisted `optics.focal_length_mm`. The feature is fully usable on day one without the (separate-P0) Settings view. A "calibrate from last solve" button back-computes effective focal length from the last solve's `pixel_scale_arcsec` + the camera pixel size (`fl_mm = 206.265 · pixel_size_um / pixel_scale`), which is reducer/barlow-proof (resolves critique C2-#9). The solve pixel scale is already returned by `hub.solve_and_sync` (verified `hub.py:284,307`).

### `SkyCanvas` props

```ts
<SkyCanvas
  center rotationDeg survey stretch fovZoomDeg optics
  mosaic={{rows,cols,overlap}} catalogTarget
  onCenterChange(ra,dec) onRotate(deg) onZoom(fovDeg)
  mode="survey"|"schematic" night={boolean}
/>
```

Internals: `<div class="relative aspect-square bg-black">` containing, stacked:
1. `<img class="survey">` (filtered by `--survey-filter`, double-buffered — keep prior frame until new `onLoad`+`rAF`), URL `= /api/survey/cutout.jpg?…` recomputed debounced 300 ms.
2. an always-present night dimmer `<div class="absolute inset-0 bg-black/40">` gated off only after `onLoad`+`rAF` so no single bright JPEG frame ever reaches a dark-adapted eye (resolves critique C3-A7).
3. `<svg viewBox="0 0 1000 1000">` — geometry only (rectangle, grid, target cross, catalog-size ellipse, compass ticks, scale-bar ticks). All strokes use `var(--accent)` **with a 1px `#000` halo underlay** (`paint-order:stroke; stroke:#000; stroke-width:3`) so the rectangle never collapses into a same-hue red survey in night mode (resolves critique C3-A1).
4. **HTML label layer** `<div class="absolute inset-0 pointer-events-none">` — every text label (FOV readout `62.0′×41.3′`, "/px", panel indices, N/E, scale-bar value, "Your camera"/"Object size" legends) is real CSS px (≥12px), positioned from the same projection. **No text inside the scaled SVG** (a `font-size:11` in a 1000-unit viewBox rendered at 343px = ~3.8px real — resolves critique C3-A2).

Novice scaffolding (resolves critique C1-B7): the rectangle is labeled **"Your camera"**, the catalog-size ellipse **"Object size"**, and a one-line verdict — "object fills 38% of frame" / "object is 2.6× your frame — needs a mosaic" — appears beneath the canvas.

Interactions:
- **Drag inside rectangle** → translate center (px delta ÷ `px_per_deg` → ξ,η → deproject).
- **Drag rotation knob** (top-edge, ≥44px hit) → `rotation_deg`; plus explicit **−5°/+5°** buttons.
- **Center-nudge buttons** (↑↓←→, ≥44px) step ±1 frame-width (or ±0.05°) — fine framing on a phone where finger-drag is ~17″/px and hopeless for arcminute precision (resolves critique C1-B8).
- **Wheel / pinch** → `fovZoomDeg` (clamp 0.1°–10°). "Fit object" sets `1.6 × size_arcmin/60`; if `size_arcmin <= 0` (stars/doubles) it falls back to `max(sensorFovDeg, 0.5°)` and suppresses the size ellipse (resolves critique C3-A11).
- **Keyboard:** canvas is `role="application"` focusable; arrow keys nudge center, `[`/`]` rotate; visible `:focus-visible` ring (resolves critique C3-A5).
- Pointer handlers use `setPointerCapture`; all hit targets ≥44px.

### `FovOverlay` (pure SVG, geometry only)

Draws one rectangle (1×1) or `rows×cols` rectangles with overlap gaps; the **active panel** is emphasized by a **thicker stroke + corner ticks** (reusing the panel bracket motif) and a solid index badge — **never** a translucent accent fill (invisible red-on-red in night mode — resolves critique C3-A12). All labels are emitted to the HTML layer (above).

### `SurveyControls`

Survey `<select>` (DSS2 color / DSS2 red / 2MASS / "schematic (offline)"); **stretch toggle** linear/asinh (so faint nebulae aren't a flat gray card — addresses the review's fixed-stretch complaint and critique C2-#8b); FOV zoom slider + "fit object"; **per-image brightness dimmer** (slider, persisted as night-brightness memory — the survey is the most dark-adaptation-destroying element on screen, critique C1-B5/C3-A7); "use camera FOV" lock (44px Toggle); recenter-to-target; center-nudge cluster; rotation numeric (0–360) + ±5°. All controls use `.btn-touch`/`.stepper`/44px Toggle (resolves critique C3-A3).

**Night-mode survey legibility (resolves critique C1-B5 / C3-A1):** in night mode the survey defaults to a monochrome HiPS (`CDS/P/DSS2/red`) and uses a dedicated `--survey-filter` (`grayscale(1) brightness(0.55) sepia(1) hue-rotate(-25deg) saturate(1.3)`) — a true low-luminance red, **not** the `--img-filter` hue-rotate of a color JPEG that muddies into a red smear.

### `MosaicPanel`

Controls (all ≥44px): rows/cols steppers (1–10), overlap slider (0–50%, **default 25%**), read-only total-FOV (tangent-plane °×°) and panel/frame count. Below: generated panel list (index, RA/Dec, **transit-altitude chip** — not now-alt, because every panel of a tight mosaic shares ~the same instantaneous alt and a now-alt chip is decorative noise that looks like per-panel data; resolves critique C1-A2). Status chips pair a glyph with the number (`↑68°`, `↓12° low`), never color-only (resolves critique C3-A4).

**Mosaic reality check (resolves critique C1-B2 / C2-#11) — the highest-value beginner safeguard:** the panel computes mosaic integration time `= Σ panels × Σ step counts × exposure` (+ per-panel slew/solve overhead estimate) and cross-references it against `VisibilityNight.best_window` end and the object's set time. If the mosaic won't finish before the object leaves the window it shows, inline:
> ⚠ 9 panels ≈ 4h12m, but M31 leaves your window at 01:30 (≈2h40m left). You'll finish ~6 of 9 panels tonight. Reduce panels, shorten exposures, or split across nights.

**Multi-panel focus nudge (resolves critique C2/C1-C3):** when `rows*cols > 1`, the panel surfaces a checkbox "refocus on temp drift during mosaic" that, on send, sets the plan's `refocus_on_temp_delta_c` to a sane default (1.5) if it's 0, and sets `autofocus_every` if the user opts in — so a 4h mosaic doesn't run on one autofocus and drift to soft stars. `autofocus_first` is set on panel (0,0) only.

**Meridian-flip warning (resolves critique C2-#3):** if the visibility data shows the mosaic spans the meridian, show "⚠ this mosaic crosses the meridian — panels shot after the flip are rotated 180°; expect a stitch seam unless you re-rotate the camera." (The engine flips per-frame, verified `engine.py:312`, so the flip itself is handled; the rotation consequence is the honest warning.)

**Rotation honesty (resolves critique C1-B4 / C2-#1):** with no rotator in the rig, the rotation control is labeled "camera angle (manual)". On "Send to Plan" the UI shows "set your camera to PA N° before this run" and stamps `rotation_deg` on each Target as guidance. The overlap default of 25% covers PA-0 cameras tiling without a rotator.

**Send-to-Plan:** "Send N panels to Plan" → `POST /api/framing/mosaic` (canonical) → `addTargetsToPlan(panels→Target[], group=target.id)` → banner + optional switch to Plan. 1×1 reads "Add target to Plan". **Disabled while a sequence is running** (the engine snapshots its plan at start; mid-run additions wouldn't be picked up and would mislead — resolves critique C2-#10). Below-horizon override gate (§ states).

Each generated `Target` from a panel:
```ts
{ name: `${id} ${r+1}-${c+1}`, ra_hours, dec_deg, rotation_deg,
  center: true, autofocus_first: (r===0 && c===0), calibration: false,
  mosaic_group: id, steps: [{ ...DEFAULT_STEP }] }   // reuse SequenceView DEFAULT_STEP
```

### `VisibilityPanel`

- **Altitude curve** — single memoized `<path>` for the target (build once with `useMemo`, not per-tick rects). Layers differentiated by **non-color channels** so they survive the all-red night palette (resolves critique C3-A6):
  - target alt: solid 2px path.
  - moon alt: dotted 1px path + ☾ glyph at its peak.
  - astro-dark band: the only **filled** band, low opacity + diagonal hatch pattern.
  - best window: bracketed `[ ]` end-caps + label (not a fill).
  - alt-limit: dashed horizontal + `30°` HTML label.
  - now-line: solid + "NOW" HTML label.
- **Readouts** (`Stat` with glyph+text): Transit `21:14 · 68°` (or "transits in daylight — peak in dark 02:10 · 41°"); Best window `22:40–02:10`; Moon `34% Waning Cres · ☾ sets 23:14 · 71° away`; Object up `5h12m above 30°`. Moon separation pairs a glyph (`⚠ <30°`, `✕ <15°`) with the number, never color alone (resolves critique C3-A4, C2-#12).
- States per §6 below; the panel collapses on error, never blocking framing.
- No "Use best window" action button — Autorun doesn't exist yet, so a time-gating button that doesn't gate is a trust-breaker. The best window is shown as advisory text only, with a clear "(advisory — runs start immediately; scheduling comes with Autorun)" note (resolves critique C2 / C1-D4).

### UI states (all regions)

- **No optics:** survey renders; FOV rectangle → dashed placeholder + inline "Set focal length above to draw your camera's frame" (focal field is in the header, no dead Settings link). MosaicPanel disabled with the same CTA.
- **Implausible optics:** FOV/pixel-scale hint flags it (§5) but does not block.
- **Survey loading:** previous image stays (double-buffer); thin top progress bar + "LOADING DSS2…" chip; first-ever load = centered skeleton + faint grid.
- **Survey error/offline (503):** `mode="schematic"`; banner "Survey offline — schematic framing: sizes approximate, can't preview nebula shape" with retry (honest about the limit — resolves critique C2-D2); overlay/mosaic math unchanged; dimmed starfield behind it (review wants starfield preserved in night mode).
- **Visibility loading:** shimmer skeleton curve + "computing tonight…".
- **Visibility error:** "Couldn't compute visibility" + retry; panel collapses, framing unaffected.
- **Below limit / never rises:** curve flat at/below limit; "Does not rise above 30° tonight" with glyph; **Send-to-Plan requires explicit override** ("M83 peaks at 12° — add anyway?") and stamps the observable window onto the target so a future scheduler can honor it (resolves review below-horizon P0 + critique C2-#11). Does not pretend the engine gates it at run time.
- **Default-site nudge:** if `hub.site` is still default SF (37.77,−122.42), a one-line "Using default location — set yours in Settings" (ties to the hardcoded-SF P0; alt values still compute from `hub.site`).
- **High latitude / no astro-dark (summer):** visibility falls back to nautical, then to darkest interval, labeled accordingly; never a blank window (resolves critique C2-#6).
- **Success:** rectangle/grid crisp, panel list populated, Send enabled.

---

## 7. Sequencer interop

- A mosaic is **N Targets** — no engine change required (engine iterates `plan.targets`, slewing + `goto_and_center` per target; verified `engine.py:157,186`). Each panel carries `center:true` so each is plate-solve-centered.
- **Per-panel solve-failure surfacing (resolves critique C2-#3):** `goto_and_center` returns `centered:False` after retries and the engine continues (verified `engine.py:187`). For a 9-panel mosaic a single cloud/no-star panel would otherwise leave a silent gap. The Plan group header shows a per-panel warning chip when a panel's centering didn't converge (read from the existing `mount`/`solve` log events the engine already publishes). This surfaces the failure; it does not change engine behavior (escalation to abort is the review's separate `require_*` safety item).
- `rotation_deg` / `mosaic_group` are additive nullable metadata — existing plans deserialize unchanged.
- **SequenceView group rendering (Owner C):** consecutive targets sharing `mosaic_group` collapse under a header ("M31 · 6 panels · 4h12m") with per-group frame/time rollup (reuse the existing `totalFrames`/`totalMinutes` reducers, scoped to the group) and a delete-group control. A `rotation_deg` chip ("PA 31°") shows on each.
- **Atomic reorder (resolves critique C2/C1-C4):** "Sort by transit" treats each `mosaic_group` as one unit — it sorts groups (and ungrouped targets) by transit time, but **never interleaves panels within a group** (snake order preserved inside the group). The tie-break is group max-alt. `POST /api/visibility/order` returns the order at group granularity.

---

## 8. Night mode + 375px phone

### Design-system token changes (`index.css`, Owner A)

- **`--survey-filter`** (separate from `--img-filter`): day `grayscale(0)`, night `grayscale(1) brightness(0.55) sepia(1) hue-rotate(-25deg) saturate(1.3)`. Survey `<img>` uses `class="survey"`, **not** `class="astro"`.
- **`:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px }`** global; remove the blanket `outline:none` reliance (resolves critique C3-A5 / review keyboard-focus P1).
- **`.btn-touch`, `.stepper` → `min-height:44px; min-width:44px`**; mandated for every Atlas control (steppers, selects, sliders, the rotation/nudge buttons). `Toggle` hit area padded to 44px (visual stays 36px). (Resolves critique C3-A3 / review touch P0.)
- **Bump `--text-dim`** to meet WCAG AA (day `#7e8da8`, night `#b06a6a`); Atlas never renders information-bearing text (coordinates, FOV, panel RA/Dec, moon sep) in `--text-dim` — those use `--text` (resolves critique C3-A9).
- **`.svg-halo`** utility (`paint-order:stroke; stroke:#000; stroke-width:3`) for overlay strokes.

### 375px phone (resolves critique C1-B6 / C3-A13)

- **Pinned canvas, scrolling planners** — SkyCanvas stays at the top (~55–60% height, square ~343px); the planner (Mosaic + Tonight, stacked or as an in-page collapsible) scrolls beneath it so the overlay updates **live** while you drag a slider. **Never** hide the canvas behind exclusive tabs — framing is a see-while-you-adjust task on the exact device where it matters most.
- SurveyControls collapse into an **inline collapsible row** (tap-to-expand), reusing the existing panel pattern — **not** a new bottom-sheet (no sheet/scrim/focus-trap pattern exists in the app; a half-built one is a one-handed-in-the-dark risk — resolves critique C3-A13).
- Touch: drag = coarse move; explicit center-nudge + ±5° buttons for fine framing; "Send to Plan" full-width 48px; mosaic steppers 44px ± buttons.
- **Nav:** Atlas is reached via the **"More ⋯" overflow sheet** in the bottom nav (decided — no waffling) plus contextual Frame buttons. It is discoverable (resolves critique C1-C6) without adding a 9th 8px-label slot. The overflow sheet is the only new mobile pattern and is shared with Settings/Power/Log.

### Night mode specifics

- Survey legibility via `--survey-filter` + monochrome default + per-image dimmer (above).
- Status everywhere = glyph/shape + text, never `text-good/warn/bad` alone (the night palette collapses good/warn/bad toward coral). `Stat` gains a `glyph` channel (Owner A).
- Visibility curve layers differentiated by stroke/dash/hatch/glyph, not hue (§6).
- Dimmed starfield preserved behind the schematic fallback (existing body gradient).
- **Icon debt:** use the Lucide set the review mandates for Atlas additions (`telescope`, `frame`, `grid-2x2`, `plus`, `compass`) — do **not** add new unicode glyphs, and do **not** reuse `⊕` (already Align) for "Frame" (resolves critique C3-A10).

---

## 9. Backend implementation notes

- **`catalog/visibility.py` uses astropy** (already a hard dep — verified `pyproject.toml:9`): `EarthLocation` from `hub.site`, `AltAz` frame, `get_body("moon")`, `get_sun`, `astropy.time.Time`. No hand-rolled `moon.py`.
  - **Sample** alt every `step_min` from local sunset→sunrise (sun alt via `get_sun`); each sample carries target alt, moon alt, sun alt.
  - **Transit = the sampled max-alt timestamp within the dark window** (not the closed-form `90−|lat−dec|`, which is wrong for circumpolar/below-pole objects and useless when geometric transit is in daylight). If geometric transit is in daylight, set `transit_in_daylight=true` and report the in-darkness peak. (Resolves critique C2-#5.)
  - **Twilight ladder:** astro-dark = sun_alt ≤ −18°. If that never occurs tonight (|lat|>~48° in June), fall back to nautical (≤ −12°), then to the darkest interval (min sun alt); set `darkness_kind` accordingly and never return a blank window. (Resolves critique C2-#6.)
  - **Best window** = longest run with `alt ≥ alt_limit` AND within the dark window, scored `mean_alt × moon_factor` where `moon_factor = 1` when the moon is below the horizon during the window, else `clamp(f(sep, illumination), 0.3, 1)` — because a 95%-illuminated moon that has set is irrelevant; moon **altitude** gates whether separation matters at all. (Resolves critique C2-#5 moon scoring.)
  - **Moon:** `illumination`, signed phase angle → correct waxing/waning `phase_name`, alt/az, separation, and **moonrise/moonset within the night** ("dark sky after 23:14") since that's the actual decision input. (Resolves critique C2-#12.)
  - **`alt_limit` default 30°** (not 20° — too low for most backyards with trees/buildings), overridable via query (ties to the future horizon-limit setting).
- **`catalog/survey.py`:** `httpx.AsyncClient` (existing dep), disk cache, 15 s timeout, retry-once, graceful 503. `ra_deg = ra_hours*15` is explicit and unit-tested (a 15× error lands a random field — critique C2-#8a).
- **J2000 invariant (resolves critique C2-#7):** the catalog is J2000, hips2fits is requested `coordsys=icrs`, and the mount plate-solves+centers on the actual sky — all consistent end-to-end. `lib/framing.ts` documents the invariant: never mix the live JNow mount RA into overlay registration; the overlay is always J2000.
- **`persist.py`:** `{site, optics}` JSON read on init, written via `asyncio.to_thread` (small, off the event loop).

---

## 10. Build order within this surface

1. **(A)** Freeze §4 contracts: `types.ts`, store plan-lift + framing slice, `index.css` tokens + `.btn-touch`/`Stat` glyph, `hub.optics` + `POST /api/optics` + summary, `Target` fields, `persist.py`. Unblocks everyone.
2. **(B)** `lib/framing.ts` math + unit tests (projection ρ→0, FOV, mosaic `%24`); `catalog/survey.py` proxy; `SkyCanvas` static centered cutout + double-buffer + dimmer.
3. **(B)** `FovOverlay` + drag/rotate/zoom/nudge/keyboard; HTML label layer; halo strokes; "use camera FOV" lock; plausibility hint.
4. **(C)** `catalog/framing.py` + `POST /api/framing/mosaic`; `MosaicPanel` + reality check; `addTargetsToPlan`; SequenceView group rendering + atomic delete.
5. **(D)** `catalog/visibility.py` (astropy) + `GET /api/visibility`; `VisibilityPanel` curve + readouts; `POST /api/visibility/order` + Plan "sort by transit" (group-atomic).
6. **(E)** `AtlasView` shell + inline focal field + "calibrate from last solve"; nav + "More" overflow + Mount/Plan Frame buttons; phone pinned-canvas layout; below-horizon override; offline schematic.

---

## 11. Implementation checklist

**Contracts / store (A)**
- [ ] `types.ts`: add the 4.1 interfaces; extend `Target` with `rotation_deg?`, `mosaic_group?`.
- [ ] `store.ts`: add `"atlas"`; lift `plan`/`setPlan` (persist in setter); `settings`+`setSettings` from `/api/summary`; `framing`+`openFraming`+`setFraming`; `atlasHandoff`; `addTargetsToPlan` (replace-by-group).
- [ ] `index.css`: `--survey-filter`; global `:focus-visible`; `.btn-touch`/`.stepper` (44px); bump `--text-dim` (AA); `.svg-halo`.
- [ ] `ui.tsx`: `Stat` `glyph`/`hint` channel; 44px `Toggle` hit area; `Stepper`/`IconButton`.
- [ ] `hub.py`: `self.optics`, seed from camera, add to `summary()`, load from `persist.py`.
- [ ] `sequence/models.py`: `Target.rotation_deg`, `Target.mosaic_group` (both nullable).
- [ ] `persist.py`: `{site,optics}` JSON via `to_thread`.
- [ ] `app.py`: `POST /api/optics`; `include_router` for survey/framing/visibility.

**Sky canvas + math (B)**
- [ ] `lib/framing.ts`: gnomonic project/deproject (ρ→0 guard, `%24`), FOV-from-optics (bin-1), mosaic grid. Unit tests incl. M31 wrap + dec>80° + center-drag.
- [ ] `catalog/survey.py`: hips2fits proxy (`ra_deg=ra*15`, no `rot`, `coordsys=icrs`), disk cache (fine key), 503 fallback; router.
- [ ] `SkyCanvas`: double-buffer + night dimmer gate; SVG geometry + halo strokes; HTML label layer; drag/rotate/zoom/nudge/keyboard ≥44px; schematic mode; "Your camera"/"Object" legends + fit/needs-mosaic verdict.
- [ ] `FovOverlay`: rectangle/grid, active panel = stroke+ticks (no translucent fill).
- [ ] `SurveyControls`: survey/stretch/zoom/fit/lock/nudge/dimmer (44px), plausibility hint.

**Mosaic (C)**
- [ ] `catalog/framing.py`: `compute_mosaic` (canonical, `%24`, ρ→0, tangent-plane total FOV); router; optional `transit_alt`.
- [ ] `MosaicPanel`: 44px steppers/overlap (default 25%); total-FOV + counts; panel list (transit-alt glyph chips); reality check vs window; meridian warning; multi-panel refocus nudge; rotation "camera angle (manual)"; Send (disabled while running; below-horizon override).
- [ ] `SequenceView`: read store `plan`; `mosaic_group` collapsible + rollup + delete-group + PA chip + per-panel center-fail chip; `atlasHandoff` banner.

**Visibility (D)**
- [ ] `catalog/visibility.py` (astropy): night sampler; sample-max transit (+daylight flag); twilight ladder + darkness fallback; best-window (moon-altitude-gated scoring); moon phase/sep/rise-set; `alt_limit` default 30; `/api/visibility` + `/api/visibility/order` (group-atomic) routers.
- [ ] `lib/visibility.ts`: VisibilityNight → SVG paths; time/status-glyph formatters.
- [ ] `VisibilityPanel`: memoized curve, dash/hatch/glyph-differentiated layers; glyph+text readouts; advisory-only best window; loading/error/below-limit/high-lat states.

**Page + nav + entry (E)**
- [ ] `AtlasView`: shell; inline focal field + "calibrate from last solve"; desktop split + phone pinned-canvas; all §6 states wired.
- [ ] `App.tsx`: register `atlas`; nav slot before Plan (Lucide `telescope`); "More ⋯" overflow sheet (Atlas/Settings/Power/Log).
- [ ] `MountView.tsx`: per-row Frame `IconButton` (≥44px, Lucide `frame`, not `⊕`) → `openFraming`.

**Cross-cutting verification**
- [ ] Open M31 → Send 1×1 → no NaN coords (ρ→0). Send 3×1 → no 422 (`%24` wrap).
- [ ] Re-frame M31 → Send again → panels **replaced**, not dropped.
- [ ] Edit an exposure in Plan after a mosaic send → panels survive (store SSOT, no localStorage race).
- [ ] Night mode: FOV rectangle visible over red survey; all status chips legible (glyph+text); SVG labels ≥12px real; every control ≥44px.
- [ ] High-latitude summer date → visibility shows a labeled fallback window, never blank.

---

## 12. Reused primitives (verified in code)

- `Camera.pixel_size_um` / `sensor_width` / `sensor_height` — `devices/base.py:81-83`.
- `SolveResult.rotation_deg` + `pixel_scale_arcsec` — `solve/base.py:13-14`; `hub.solve_and_sync` returns `pixel_scale` — `hub.py:284,307`.
- `coords.altaz` / `lst_hours` / `format_ra` / `format_dec` — `catalog/coords.py` (visibility additionally uses astropy for sun/moon).
- `hub.goto_and_center` / `hub.site` / `hub.summary()` (already returns `site`) — `hub.py:155,309`.
- Engine iterates `plan.targets`, `goto_and_center` per target, per-frame meridian flip — `engine.py:157,186,312`.
- `Target.ra_hours = Field(ge=0, lt=24)` — `models.py:19` (drives the `%24` requirement).
- `SequenceView.DEFAULT_STEP` + `localStorage["astrodeck-plan"]` — `views/SequenceView.tsx:7,35`.
- `img.astro` / `--img-filter` night CSS — `index.css:42,90` (Atlas uses a **new** `--survey-filter` instead).
- `httpx>=0.27`, `astropy>=6.0` already dependencies — `server/pyproject.toml:7,9`.

---

## 13. Disposition of the three critiques

**Accepted and fixed:**
- ρ=0 deprojection NaN (C1-A3 / C2): center special-cased (§5).
- RA wraparound 422s the plan (C2-#2): `%24` in `compute_mosaic` + mirror (§4.4, §5).
- Per-panel `alt` is now-alt noise (C1-A2): use transit altitude (§4.1, §6).
- `rot` cutout param is a no-op (C1-A4 / C2-#8): removed entirely (§4.3).
- Optics gated on non-existent Settings (C1-B1 / E1): inline header field + "calibrate from last solve" (§6).
- Mosaic-cost reality check (C1-B2 / C2-#11): integration vs best-window/set time (§6).
- Rotation is decorative/a lie (C1-B4 / C2-#1): manual camera-angle guidance + 25% default overlap + meridian-flip warning; no false promise (§1, §6).
- localStorage data-loss race (C1-C1 / C2-#10): plan lifted into store, SSOT, send disabled while running (§1, §4.2, §6).
- Dedupe drops re-frames (C1-C2): replace-by-`mosaic_group` (§4.2).
- Multi-panel focus drift (C1-C3): refocus-on-temp nudge on send (§6).
- Reorder vs snake-order conflict (C1-C4 / C2): groups treated atomically (§7).
- Transit/twilight/high-lat/moon bugs (C2-#5,#6,#12): astropy + sample-max transit + darkness fallback + moonrise/set (§9).
- Survey smear + overlay collapse + viewBox text + sub-44px + color-only + no focus ring (C3-A1,A2,A3,A4,A5): `--survey-filter`, halo strokes, HTML labels, 44px primitives, glyph+text, `:focus-visible` (§6, §8).
- Curve red-on-red, brightness flash, translucent-fill state, bottom-sheet, `--text-dim` AA, icon collision (C3-A6,A7,A12,A13,A9,A10): dash/hatch/glyph layering, gated dimmer, stroke+ticks, inline collapsible, AA tokens, Lucide icons (§6, §8).
- Phone tabs hide the canvas (C1-B6 / C3-A13): pinned-canvas + scrolling planners (§8).
- Coarse drag precision (C1-B8): center-nudge buttons (§6).
- Novice scaffolding (C1-B7): "Your camera"/"Object" legends + fits/needs-mosaic verdict (§6).
- Offline mode oversold (C2-D2): honest "sizes approximate, can't preview shape" banner (§6 states).
- Optics typo guard (C1-D1): pixel-scale/FOV plausibility hint (§5).
- Discoverability (C1-C6): Atlas in nav before Plan + mobile "More" overflow (§8).
- "Use best window" gates nothing (C1-D4 / C2): advisory-only text, no false button (§6).
- Survey cache too coarse (C2-#15): finer key + un-quantized interactive request (§4.3).
- Bin in panel generation (C2-#14): panels always bin-1 (§5).
- High-dec total-FOV mislabel (C2-#4 / D3): tangent-plane extent + drawn footprints (§5).
- `ra` hours-vs-deg (C2-#8a): explicit `*15` + unit test (§9).

**Rejected, with reason:**
- **C1-A1 (claim: `summary()` doesn't return `site`)** — rejected on the facts: `hub.py:155` returns `site`. The actionable remainder (add `optics`) is kept (§4.4).
- **C1-A5 / C2-#18 (claim: `httpx` unverified / risks a new dep)** — rejected: `httpx>=0.27` is already a dependency (`pyproject.toml:7`). Relatedly, the draft's hand-rolled `catalog/moon.py` is **dropped** because `astropy>=6.0` is also already a dependency (`pyproject.toml:9`) — using it is strictly more correct and adds no weight.

**Deferred (noted, not blocking this surface):**
- **C2-#13 / C2-#17 (catalog needs major/minor axis + PA for elongated objects / fit-object aspect)** — a catalog **data-model** change (`objects.py` `DSO`), out of this surface's file ownership. Handled defensively here: `size_arcmin <= 0` and single-dimension cases degrade gracefully (suppress the ellipse, sensible "fit object" fallback). Flagged as a follow-up so the size ellipse can become an oriented ellipse later without reworking Atlas.
