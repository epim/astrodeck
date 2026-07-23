# PRO-13 — Sensor-tilt / corner-vs-center optical-aberration inspector

Combined design spec + TDD implementation plan. One file.

Status: ready. Slug `tilt-inspector`. Builds on PRO-7 (real per-star ecc/theta,
already shipped this session).

---

## 1. Design

### 1.1 Goal

Aggregate the per-star metrics PRO-7 already computes into a **zone map** of the
frame, classify the spatial pattern, and surface it visually:

1. **Zone aggregation** — bin the frame into a 3×3 grid by star pixel-position,
   and per zone report **median HFR**, **mean eccentricity**, a **dominant
   elongation axis**, and a star count.
2. **Pattern classification** — from the zone map, decide whether the frame is
   `uniform` (round & flat — good optics + good tracking), `tilt` (asymmetric HFR
   gradient — one side/corner bloated), `coma` (radial/symmetric — corners
   degrade vs a sharp center, elongation points radially — spacing/coma), or
   `tracking` (uniform *directional* elongation everywhere — guiding/RA drift).
3. **Heatmap panel** — a per-zone HFR heatmap overlaid on the preview inside the
   existing shared transform, with per-zone elongation ticks, and a
   classification chip.

**Scope v1:** the pure zone-aggregation + classification core (pytest'd), an
additive `info["tilt"]` block on the preview event (mirrors `info["ecc"]`), the
heatmap overlay + chip, and a toolbar toggle. **Explicitly deferred** (§4): a
frame-reject *gate* on tilt (PRO-7's ecc gate already covers "too elongated"),
configurable grid size, and a per-target tilt trend in the sequence report.

### 1.2 Current-state seams (real file:line, all read)

**PRO-7 already emits per-star ecc/theta + hfr.** `detect_stars`
(`server/astrodeck/imaging/stars.py:54-117`) fills every `Star` with real
`hfr`, `ecc`, `theta` via the second-moment block at `stars.py:104-116`
(`_ecc_theta`, `stars.py:37-51`). The `Star` dataclass carries
`x,y,flux,hfr,peak,ecc,theta` (`stars.py:19-27`).

**The per-star overlay payload already carries what a zone map needs.**
`star_marks` (`stars.py:140-173`) emits `[{x, y, hfr[, ecc, theta]}]` — `x,y` in
`frame.data` pixel space, `hfr` on **every** mark, and `ecc`/`theta` attached
**only** for the trusted mid-bright unsaturated population (`stars.py:168-171`):

```python
unsaturated_mid = (s.peak > floor and (sat is None or s.peak < sat))
if s.ecc and unsaturated_mid:
    m["ecc"] = round(float(s.ecc), 3)
    m["theta"] = round(float(s.theta), 3)
```

That is exactly the population we want to trust for elongation — so a tilt
aggregator that reads `marks` inherits PRO-7's saturation/faint filtering for
free, with no new detection pass.

**`frame_eccentricity` is the exact aggregation template.**
`stars.py:176-181` reduces the trusted marks to one scalar with `np.median`:

```python
def frame_eccentricity(marks: list[dict]) -> float | None:
    eccs = [m["ecc"] for m in marks if "ecc" in m]
    return float(np.median(eccs)) if eccs else None
```

`frame_tilt` mirrors this shape — a pure function over `marks` returning a
JSON-able block or `None` — and slots in beside it.

**Where the hub builds `info` and already has both `marks` and frame dims.**
`hub.py:1483-1500` builds the base `info` dict, including the frame dimensions
`info["data_width"] = int(data.shape[1])` / `info["data_height"] =
int(data.shape[0])` (`hub.py:1489-1490`). The linear-path branch runs one
detection pass and lands the marks + ecc scalar (`hub.py:1550-1566`):

```python
hfr, count, marks = measure_stars(stars, full_well=info["full_well"])
info.update({..., "star_list": marks})
...
fecc = frame_eccentricity(marks)
if fecc is not None:
    info.setdefault("ecc", round(fecc, 3))
```

`frame_tilt(marks, info["data_width"], info["data_height"])` slots in
immediately after, guarded the same way. `frame_eccentricity` is already
imported from `.imaging` (`hub.py:44`, inside the `from .imaging import (...)`
block) — `frame_tilt` joins that import.

**The imaging package re-exports.** `imaging/__init__.py:13-16` re-exports
`frame_eccentricity` from `.stars`; `__all__` lists it (`__init__.py:28`).
`frame_tilt` is added to both.

**The client contract.** `ui/src/types.ts` declares `StarMark`
(`types.ts:195-201`, with optional `ecc?`/`theta?`), `PreviewInfo`
(`types.ts:212-241`, `star_list?` at `:238`, `hfr?`/`stars?` at `:236-237`), and
`OverlayToggles` (`types.ts:260-265`: `stars`, `clip`, `reticle`, `centerMark`).
`TiltInfo` is a new additive interface; `PreviewInfo.tilt?` and
`OverlayToggles.tilt` are new optional/boolean fields.

**Overlay toggle defaults + persistence.** `store.ts:262-269` `defaultOverlays()`
returns the four flags; `loadPreviewPersisted` merges any persisted subset via
`Object.assign(overlays, p.overlays)` (`store.ts:284`) and `persistPreview`
serializes the whole `overlays` object (`store.ts:299-305`). Adding
`tilt: false` to the default is picked up by persistence automatically.

**The render host.** `PreviewStage.tsx` holds one shared `.preview-transform`
layer with the image buffer(s) + an overlay `<svg>` (`PreviewStage.tsx:298-374`).
Overlays live in that svg in data-display space: `ClipMaskLayer`
(`:355`), `StarOverlay` (`:356-371`, gated by `overlays.stars && starsAvailable`,
`starsAvailable` computed at `:240`), `Reticle` (`:372`). `displayScale =
dispW / data_width` (`:231`) maps data px → display px. Screen-space chrome
(not transformed) hangs below: the selected-star readout chip
(`:400-406`) and the decimation chip (`:408-413`) are the exact idiom for a
classification chip. `overlays` is already a prop (`:37`).

**The overlay toggle UI.** `PreviewToolbar.tsx:128-157` renders one `<Toggle>`
per overlay. The Stars toggle (`:128-135`) is the exact idiom — `on` +
`disabled` (honest-disabled handled by the shared `Toggle`, same as Clip) +
`title` that changes when unavailable:

```tsx
<Toggle on={overlays.stars} disabled={!starsAvailable} icon="align" label="Stars"
  title={starsAvailable ? "Toggle star HFR overlay" : "No per-star data for this frame"}
  onClick={() => setOverlays({ stars: !overlays.stars })} />
```

**The tsx pure-logic test idiom.** `ui/src/lib/__tests__/eta.test.ts:21-43` is
the inline-assert harness (`test`, `eq`, `assert`, `near`, final `console.log` +
`export const result`), run with `npx tsx`. No jsdom.

### 1.3 The aggregation (marks → zones)

Bin each trusted-or-not mark into a `cols×rows` grid (v1: 3×3) by its `data`-space
`x,y`:

```
c = min(cols-1, floor(x / width  * cols))
r = min(rows-1, floor(y / height * rows))
zone = r*cols + c        # row-major
```

Per zone (with `n` = marks binned there):

- `hfr`  = `np.median` of every mark's `hfr` in the zone (HFR is on all marks).
- `ecc`  = `np.mean` of the marks that carry `ecc` (trusted subset only).
- `theta` = circular mean of the marks' `theta`, **as an axis** (see §1.4).
- Zones with `n < 3` report `hfr/ecc/theta = None` (honest — too few stars to
  trust), but keep `n` so the client can mute them.

### 1.4 The math (axis statistics + classification)

**Position angle is an axis (mod π), not a direction (mod 2π).** `theta` from
`_ecc_theta` lives in `(−π/2, π/2]` and `+80°` ≡ `−80°`'s neighbour is the same
near-vertical axis. Averaging or spreading raw θ is wrong at the ±π/2 seam. The
standard fix is the **doubled-angle** transform:

```
axis_mean(θ)   = 0.5 · atan2( mean(sin 2θ), mean(cos 2θ) )
axis_spread(θ) = 1 − hypot( mean(cos 2θ), mean(sin 2θ) )      # 0 = perfectly aligned, 1 = scattered
axis_diff(a,b) = let d = |a−b| mod π in min(d, π−d)           # acute angle between two axes, 0..π/2
```

`axis_spread` of `{+80°, −80°}` → both double to ≈ ±160°→ wrap to a tight cluster
near 180°, resultant ≈ 1, spread ≈ 0 (correctly "same axis"). This is the one
genuinely subtle numeric piece and is unit-tested with exact vectors.

**Classifier.** From the populated zones compute (`med` = median zone HFR):

```
rel_spread   = (max_zone_hfr − min_zone_hfr) / med          # nonuniformity → severity
mean_ecc     = mean of populated zones' ecc
axis_spread  = axis_spread over zones' theta                # low = one common direction
tilt_mag     = hypot(gx, gy) / med   where gx = (last col mean − first col mean),
                                            gy = (last row mean − first row mean)   # planar HFR gradient
radial_excess= (mean corner-zone hfr − center-zone hfr) / center-zone hfr           # symmetric corner degrade
radial_frac  = fraction of (non-center, theta-bearing) zones whose theta axis
               aligns (axis_diff < 0.5 rad) with the radial direction from frame center
```

Decision (ordered; constants in §3 Task 1):

```
if rel_spread < FLAT_TOL and mean_ecc < ROUND_TOL:                      pattern = "uniform"   # good
elif mean_ecc >= ELONG_TOL and axis_spread < AXIS_ALIGN_TOL
                            and radial_frac < RADIAL_FRAC_TOL:          pattern = "tracking"  # uniform drift
elif radial_excess >= RADIAL_EXCESS_TOL
     and (radial_frac >= RADIAL_FRAC_TOL or tilt_mag < TILT_TOL):       pattern = "coma"      # radial/symmetric
elif tilt_mag >= TILT_TOL:                                             pattern = "tilt"      # asymmetric
else:                                                                  pattern = "uniform"
```

Order matters: a uniform-direction elongation is `tracking` even with some HFR
spread; a symmetric corner degrade is `coma` before the residual gradient reads
as `tilt`. `severity = rel_spread` (0..1-ish), `worst_zone` = index of the
highest-HFR populated zone. `None` overall when < `MIN_POPULATED` zones have
enough stars — the client then shows nothing (abstain, exactly like
`frame_eccentricity` → `None`).

### 1.5 Heatmap encoding (colorblind-safe)

The heatmap fills each zone with a low-opacity color ramped by that zone's HFR
**and** prints the zone's HFR number **and** draws an elongation tick (major-axis
line, length ∝ ecc, angle = theta). Three redundant channels → never
color-alone (§11.1). Empty zones render muted with no tick (honest — no data).
The classification chip states the pattern in words with a tone color.

### 1.6 Placement summary

| Concern | File | Change |
|---|---|---|
| Aggregation + classifier core | `imaging/stars.py` | `frame_tilt()` + `_bin_zones` / `_classify` / axis helpers |
| Re-export | `imaging/__init__.py:14,28` | add `frame_tilt` |
| Wire onto `info` | `hub.py:~1566` | `info["tilt"] = frame_tilt(marks, dw, dh)` (guarded) |
| Import | `hub.py:44` | add `frame_tilt` to the `.imaging` import |
| Client contract | `ui/src/types.ts` | `TiltZone` + `TiltInfo`; `PreviewInfo.tilt?`; `OverlayToggles.tilt` |
| Toggle default | `ui/src/store.ts:262-269` | `tilt: false` |
| Pure client helpers | `ui/src/lib/tilt.ts` (new) | `tiltSummary`, `zoneHfrRange`, `heatFrac` |
| Heatmap render | `ui/src/components/preview/TiltOverlay.tsx` (new) | grid rects + ticks + labels |
| Stage wiring + chip | `ui/src/components/preview/PreviewStage.tsx` | render `TiltOverlay` + classification chip |
| Toolbar toggle | `ui/src/components/preview/PreviewToolbar.tsx` | one `<Toggle>` mirroring Stars |

---

## 2. Global Constraints (verbatim, binding)

- **Privacy.** The real site coordinates `37.348110` / `121.801704` and the label
  `"My Backyard"` must NEVER appear in code, tests, or docs. The site default is
  `"My Observatory"` / `0.0`. (This feature touches no coordinate and no site
  label anywhere in the diff. Stated to remain binding.)
- **Never `git add -A`.** Stage named paths only.
- **UI typecheck gate:** `cd ui && npx tsc -b`.
- **NO jsdom.** Pure logic is tested via `npx tsx` inline-assert, idiom
  `ui/src/lib/__tests__/eta.test.ts`. Thin render is verified by the typechecker
  only.
- **Backend tests:** `server/.venv/Scripts/pytest.exe` run from the **repo root**,
  single process (`-n0`).
- **Device drivers** follow the existing `base.py` `Device`/ABC + the
  `_DEV_TYPE_TO_ROLE` + hub-singleton pattern. (No device/driver work in this
  feature — it is pure image analysis over marks the hub already has. Stated to
  remain binding.)
- **Client toasts** via `useStore.getState().enqueueToast`. (Not needed here —
  the inspector is passive; no toast.)
- **Honest-disabled idiom (§11.8):** dim token + lock glyph + `aria-disabled` +
  `title`, never native `disabled`. The tilt `<Toggle>` reuses the **exact**
  Stars/Clip idiom (`disabled` + availability-dependent `title`); honest-disabled
  is the shared `Toggle` component's concern and already governs Stars/Clip.
  Muted (dataless) heatmap zones are dimmed with an `aria`-free decorative note,
  never a native-disabled control.
- **Do not disrupt astrotown.**

---

## 3. TDD Plan

### Task 1 — `frame_tilt` aggregation + classification core (numeric)

**Impl tier: Opus.** Genuinely subtle: doubled-angle axis statistics (mean +
spread across the ±π/2 seam), the planar-gradient-vs-radial-excess discrimination
that separates `tilt` from `coma`, and a decision order whose branches must not
steal each other's cases. Getting the axis math and threshold ordering right is
correctness-critical; the rest of the feature is plumbing behind it.

**Files**
- Modify: `server/astrodeck/imaging/stars.py`
- Create (test): `server/tests/test_tilt.py`

**Interfaces**

```python
# server/astrodeck/imaging/stars.py — module constants (tunable; Open Decision B)
_TILT_MIN_ZONE_STARS = 3       # a zone needs >= this to report hfr/ecc/theta
_TILT_MIN_POPULATED  = 4       # need >= this many populated zones to classify at all
_TILT_FLAT_TOL       = 0.15    # rel HFR spread below this (+ round) => uniform
_TILT_ROUND_TOL      = 0.20    # mean ecc below this => round
_TILT_ELONG_TOL      = 0.35    # mean ecc at/above this => elongated enough for tracking
_TILT_AXIS_ALIGN_TOL = 0.25    # axis_spread below this => one common elongation direction
_TILT_RADIAL_FRAC_TOL= 0.55    # fraction of zones radially aligned => radial (coma)
_TILT_RADIAL_ANGLE   = 0.5     # radians (~29deg): theta-vs-radial alignment tolerance
_TILT_RADIAL_EXCESS  = 0.25    # (corner_mean - center)/center at/above => corners degraded
_TILT_GRAD_TOL       = 0.20    # normalized planar HFR gradient at/above => asymmetric tilt

def _axis_mean(thetas: list[float]) -> float: ...     # doubled-angle circular mean, radians
def _axis_spread(thetas: list[float]) -> float: ...   # 0 (aligned) .. 1 (scattered)
def _axis_diff(a: float, b: float) -> float: ...      # acute angle between two axes, 0..pi/2

def _bin_zones(marks: list[dict], width: float, height: float,
               cols: int, rows: int) -> list[dict]:
    """Row-major zones: [{'hfr': float|None, 'ecc': float|None,
    'theta': float|None, 'n': int}, ...] (len == rows*cols)."""

def _classify(zones: list[dict], cols: int, rows: int) -> tuple[str, float, int | None]:
    """Return (pattern, severity, worst_zone).
    pattern in {'uniform','tilt','coma','tracking'}."""

def frame_tilt(marks: list[dict], width: float, height: float,
               *, grid: int = 3) -> dict | None:
    """Zone map + pattern for the tilt/aberration inspector, over the same
    ``marks`` ``frame_eccentricity`` reads. ``None`` when too few zones have
    enough stars (the client then shows nothing). Block shape:
      {'cols','rows','zones':[{hfr,ecc,theta,n}...],'pattern','severity','worst_zone'}"""
```

**Steps**

1. **Write the axis-math tests first** (`test_tilt.py`), importing the helpers
   from `astrodeck.imaging.stars`:

   ```python
   import math
   from astrodeck.imaging.stars import _axis_mean, _axis_spread, _axis_diff

   def test_axis_mean_and_spread_handle_the_pi_seam():
       # aligned horizontal axes -> mean ~0, spread ~0
       assert abs(_axis_mean([0.0, 0.05, -0.05])) < 1e-6
       assert _axis_spread([0.0, 0.0, 0.0]) < 1e-9
       # +80deg and -80deg are the SAME axis (near vertical): tight, not scattered
       a = math.radians(80); b = math.radians(-80)
       assert _axis_spread([a, b]) < 0.15
       # genuinely perpendicular axes -> maximally scattered
       assert _axis_spread([0.0, math.pi / 2]) > 0.9

   def test_axis_diff_is_acute_and_wraps():
       assert abs(_axis_diff(0.0, 0.0)) < 1e-9
       assert abs(_axis_diff(0.0, math.pi / 2) - math.pi / 2) < 1e-9
       # 10deg vs 170deg are 20deg apart as axes (not 160)
       assert abs(_axis_diff(math.radians(10), math.radians(170)) - math.radians(20)) < 1e-6
   ```

2. **Implement the axis helpers** (`stars.py`; `math` and `numpy as np` are
   already imported):

   ```python
   def _axis_mean(thetas: list[float]) -> float:
       c = float(np.mean(np.cos(2.0 * np.asarray(thetas))))
       s = float(np.mean(np.sin(2.0 * np.asarray(thetas))))
       return 0.5 * math.atan2(s, c)

   def _axis_spread(thetas: list[float]) -> float:
       c = float(np.mean(np.cos(2.0 * np.asarray(thetas))))
       s = float(np.mean(np.sin(2.0 * np.asarray(thetas))))
       return float(1.0 - math.hypot(c, s))

   def _axis_diff(a: float, b: float) -> float:
       d = abs(a - b) % math.pi
       return min(d, math.pi - d)
   ```

3. **Write the binning test**, then implement `_bin_zones`:

   ```python
   from astrodeck.imaging.stars import _bin_zones

   def _mark(x, y, hfr, ecc=None, theta=None):
       m = {"x": float(x), "y": float(y), "hfr": float(hfr)}
       if ecc is not None:
           m["ecc"] = float(ecc); m["theta"] = float(theta or 0.0)
       return m

   def test_bin_zones_medians_and_null_thin_zones():
       W = H = 900  # 3x3 -> each zone 300px
       marks = []
       # zone 0 (top-left): 4 stars hfr 2.0, ecc 0.1
       marks += [_mark(30 + i, 30 + i, 2.0, 0.1, 0.0) for i in range(4)]
       # zone 8 (bottom-right): 4 stars hfr 4.0, ecc 0.5
       marks += [_mark(630 + i, 630 + i, 4.0, 0.5, 0.0) for i in range(4)]
       # zone 4 (center): only 2 stars -> too thin -> None hfr
       marks += [_mark(450, 450, 3.0, 0.2, 0.0), _mark(451, 451, 3.0, 0.2, 0.0)]
       zones = _bin_zones(marks, W, H, 3, 3)
       assert len(zones) == 9
       assert abs(zones[0]["hfr"] - 2.0) < 1e-9 and zones[0]["n"] == 4
       assert abs(zones[8]["hfr"] - 4.0) < 1e-9
       assert zones[4]["hfr"] is None and zones[4]["n"] == 2  # honest: too few
   ```

   Implementation:

   ```python
   def _bin_zones(marks, width, height, cols, rows):
       buckets: list[list[dict]] = [[] for _ in range(cols * rows)]
       for m in marks:
           x, y = m["x"], m["y"]
           if not (0.0 <= x < width and 0.0 <= y < height):
               continue
           c = min(cols - 1, int(x / width * cols))
           r = min(rows - 1, int(y / height * rows))
           buckets[r * cols + c].append(m)
       zones = []
       for b in buckets:
           n = len(b)
           if n >= _TILT_MIN_ZONE_STARS:
               hfr = float(np.median([m["hfr"] for m in b]))
               eccs = [m["ecc"] for m in b if "ecc" in m]
               thetas = [m["theta"] for m in b if "theta" in m]
               ecc = float(np.mean(eccs)) if eccs else None
               theta = _axis_mean(thetas) if thetas else None
           else:
               hfr = ecc = theta = None
           zones.append({
               "hfr": round(hfr, 2) if hfr is not None else None,
               "ecc": round(ecc, 3) if ecc is not None else None,
               "theta": round(theta, 3) if theta is not None else None,
               "n": n,
           })
       return zones
   ```

4. **Write the classifier tests with four synthetic zone patterns**, then
   implement `_classify` + `frame_tilt`. The builder drops `k` marks per zone at
   that zone's pixel center with a chosen `hfr/ecc/theta`:

   ```python
   from astrodeck.imaging.stars import frame_tilt
   W = H = 900

   def _zone_field(specs):
       """specs: {zone_index: (hfr, ecc, theta_or_None)} -> marks (4 per named zone)."""
       out = []
       for idx, (hfr, ecc, theta) in specs.items():
           r, c = divmod(idx, 3)
           cx, cy = (c + 0.5) * 300, (r + 0.5) * 300
           for i in range(4):
               out.append(_mark(cx + i, cy + i, hfr, ecc, theta))
       return out

   def _all(hfr, ecc, theta):
       return {i: (hfr, ecc, theta) for i in range(9)}

   def test_uniform_round_and_flat():
       t = frame_tilt(_zone_field(_all(2.0, 0.10, 0.0)), W, H)
       assert t is not None and t["pattern"] == "uniform"
       assert len(t["zones"]) == 9 and t["cols"] == 3 and t["rows"] == 3

   def test_tilt_is_asymmetric_hfr_gradient():
       # HFR ramps left(1.8) -> right(3.8) across columns; stars stay round
       specs = {}
       for i in range(9):
           col = i % 3
           specs[i] = (1.8 + col * 1.0, 0.12, 0.0)
       t = frame_tilt(_zone_field(specs), W, H)
       assert t["pattern"] == "tilt"
       assert t["worst_zone"] in (2, 5, 8)  # rightmost column is worst

   def test_coma_is_radial_corner_degrade():
       # center sharp+round, corners bloated with radial elongation
       specs = {4: (1.8, 0.05, 0.0)}
       for i in (0, 1, 2, 3, 5, 6, 7, 8):
           r, c = divmod(i, 3)
           cx, cy = (c + 0.5) * 300, (r + 0.5) * 300
           radial = math.atan2(cy - H / 2, cx - W / 2)
           specs[i] = (3.4, 0.55, radial)
       t = frame_tilt(_zone_field(specs), W, H)
       assert t["pattern"] == "coma"

   def test_tracking_is_uniform_directional_elongation():
       # every zone elongated the SAME direction (RA drift), HFR flat
       t = frame_tilt(_zone_field(_all(2.4, 0.55, 0.0)), W, H)
       assert t["pattern"] == "tracking"

   def test_frame_tilt_abstains_when_too_sparse():
       assert frame_tilt([_mark(10, 10, 2.0)], W, H) is None
   ```

   Implementation (`_classify` uses geometric corners/center of the grid):

   ```python
   def _zone_center_frac(i, cols, rows):
       r, c = divmod(i, cols)
       return ((c + 0.5) / cols, (r + 0.5) / rows)

   def _classify(zones, cols, rows):
       pop = [(i, z) for i, z in enumerate(zones) if z["hfr"] is not None]
       hfrs = [z["hfr"] for _, z in pop]
       med = float(np.median(hfrs))
       rel_spread = (max(hfrs) - min(hfrs)) / med if med > 0 else 0.0
       worst = max(pop, key=lambda iz: iz[1]["hfr"])[0]

       eccs = [z["ecc"] for _, z in pop if z["ecc"] is not None]
       mean_ecc = float(np.mean(eccs)) if eccs else 0.0
       thetas = [z["theta"] for _, z in pop if z["theta"] is not None]
       axis_spread = _axis_spread(thetas) if len(thetas) >= 2 else 1.0

       # planar HFR gradient (asymmetry): first/last populated column & row means
       col_means = [[] for _ in range(cols)]
       row_means = [[] for _ in range(rows)]
       for i, z in pop:
           r, c = divmod(i, cols)
           col_means[c].append(z["hfr"]); row_means[r].append(z["hfr"])
       def _span(groups):
           ms = [float(np.mean(g)) for g in groups if g]
           return (ms[-1] - ms[0]) if len(ms) >= 2 else 0.0
       tilt_mag = math.hypot(_span(col_means), _span(row_means)) / med if med > 0 else 0.0

       # radial excess: geometric corners vs center cell
       center_i = (rows // 2) * cols + (cols // 2)
       corner_ix = [0, cols - 1, (rows - 1) * cols, rows * cols - 1]
       center = zones[center_i]["hfr"]
       ch = [zones[i]["hfr"] for i in corner_ix if zones[i]["hfr"] is not None]
       radial_excess = ((float(np.mean(ch)) - center) / center
                        if (center and center > 0 and ch) else 0.0)

       # radial alignment of elongation axes
       aligned = total = 0
       for i, z in enumerate(zones):
           if i == center_i or z["theta"] is None:
               continue
           zx, zy = _zone_center_frac(i, cols, rows)
           radial = math.atan2(zy - 0.5, zx - 0.5)
           total += 1
           if _axis_diff(z["theta"], radial) < _TILT_RADIAL_ANGLE:
               aligned += 1
       radial_frac = aligned / total if total else 0.0

       if rel_spread < _TILT_FLAT_TOL and mean_ecc < _TILT_ROUND_TOL:
           pattern = "uniform"
       elif (mean_ecc >= _TILT_ELONG_TOL and axis_spread < _TILT_AXIS_ALIGN_TOL
             and radial_frac < _TILT_RADIAL_FRAC_TOL):
           pattern = "tracking"
       elif (radial_excess >= _TILT_RADIAL_EXCESS
             and (radial_frac >= _TILT_RADIAL_FRAC_TOL or tilt_mag < _TILT_GRAD_TOL)):
           pattern = "coma"
       elif tilt_mag >= _TILT_GRAD_TOL:
           pattern = "tilt"
       else:
           pattern = "uniform"
       return pattern, rel_spread, worst

   def frame_tilt(marks, width, height, *, grid: int = 3):
       if not marks or width <= 0 or height <= 0:
           return None
       cols = rows = grid
       zones = _bin_zones(marks, width, height, cols, rows)
       if sum(1 for z in zones if z["hfr"] is not None) < _TILT_MIN_POPULATED:
           return None
       pattern, severity, worst = _classify(zones, cols, rows)
       return {"cols": cols, "rows": rows, "zones": zones,
               "pattern": pattern, "severity": round(severity, 3),
               "worst_zone": worst}
   ```

**Commands / expected output**

```
server/.venv/Scripts/pytest.exe server/tests/test_tilt.py -n0 -q
# expected: all pass — axis-math, binning, and the four pattern cases
# (uniform / tilt / coma / tracking) + the sparse-abstain case
```

---

### Task 2 — Export + wire `info["tilt"]` into the hub

**Impl tier: Sonnet.** Mechanical: one re-export, one import, one guarded
assignment mirroring the `frame_eccentricity` block two lines above; one
integration assertion.

**Files**
- Modify: `server/astrodeck/imaging/__init__.py`
- Modify: `server/astrodeck/hub.py`
- Create (test): `server/tests/test_tilt_hub.py` (or extend an existing preview test)

**Steps**

1. **Re-export** `frame_tilt` in `imaging/__init__.py`: add it to the
   `from .stars import (...)` list (`__init__.py:13-16`, next to
   `frame_eccentricity`) and to `__all__` (`__init__.py:28`).

2. **Import into the hub**: add `frame_tilt` to the `from .imaging import (...)`
   block at `hub.py:44` (alongside `frame_eccentricity`).

3. **Wire onto `info`** in `hub.py`, immediately after the `frame_eccentricity`
   block (`hub.py:1564-1566`), reusing the frame dims already on `info`:

   ```python
   tilt = frame_tilt(marks, info["data_width"], info["data_height"])
   if tilt is not None:
       info["tilt"] = tilt
   ```

   Additive (a new key, no `setdefault` needed — nothing else emits `tilt`), and
   only on the linear/native detection path where `marks` exist, exactly like
   `star_list` / `ecc`.

4. **Integration test** — a real synthetic sub through the pipeline proves the
   block appears and is well-formed. Reuse `synthetic_field` from
   `test_imaging.py:29-40` (or import it) to build a star field, run
   `detect_stars` → `star_marks` → `frame_tilt`:

   ```python
   import numpy as np
   from astrodeck.imaging import detect_stars, star_marks, frame_tilt

   def test_frame_tilt_block_shape_from_real_detection():
       rng = np.random.default_rng(3)
       img = rng.normal(500, 8, (900, 900))
       ys, xs = np.mgrid[0:900, 0:900]
       for _ in range(120):
           px, py = rng.uniform(40, 860), rng.uniform(40, 860)
           img += 2.5e5 * np.exp(-((xs - px) ** 2 + (ys - py) ** 2) / (2 * 1.6 ** 2))
       marks = star_marks(detect_stars(img))
       t = frame_tilt(marks, 900, 900)
       assert t is not None
       assert t["cols"] == 3 and t["rows"] == 3 and len(t["zones"]) == 9
       assert t["pattern"] in {"uniform", "tilt", "coma", "tracking"}
       assert isinstance(t["severity"], float)
       for z in t["zones"]:
           assert set(z) == {"hfr", "ecc", "theta", "n"}
   ```

**Commands / expected output**

```
server/.venv/Scripts/pytest.exe server/tests/test_tilt.py server/tests/test_tilt_hub.py -n0 -q
# expected: all pass
server/.venv/Scripts/pytest.exe server/tests -n0 -q -k "preview or hub"
# expected: green — info["tilt"] is purely additive
```

---

### Task 3 — Client contract + pure heatmap helpers

**Impl tier: Sonnet.** Additive TS interfaces, one default flag, and three small
pure functions with a tsx test. No render here.

**Files**
- Modify: `ui/src/types.ts`
- Modify: `ui/src/store.ts`
- Create: `ui/src/lib/tilt.ts`
- Create (test): `ui/src/lib/__tests__/tilt.test.ts`

**Interfaces**

```ts
// ui/src/types.ts — after StarMark (types.ts:201), before PreviewInfo
export interface TiltZone {
  hfr: number | null;   // median HFR of zone stars (px); null when too few stars
  ecc: number | null;   // mean elongation of trusted stars; null when none
  theta: number | null; // mean major-axis angle (radians); null when none
  n: number;            // stars binned into this zone
}
export interface TiltInfo {
  cols: number;
  rows: number;
  zones: TiltZone[];                                   // row-major, len === rows*cols
  pattern: "uniform" | "tilt" | "coma" | "tracking";
  severity: number;                                    // 0..1-ish relative HFR spread
  worst_zone: number | null;                           // index into zones, or null
}
// PreviewInfo (types.ts:212-241): add beside star_list? (:238)
tilt?: TiltInfo;
// OverlayToggles (types.ts:260-265): add
tilt: boolean; // default false — tilt/aberration heatmap

// ui/src/lib/tilt.ts
export interface TiltSummary { label: string; tone: "good" | "warn" | "bad"; advice: string }
export function tiltSummary(t: TiltInfo): TiltSummary;
export function zoneHfrRange(t: TiltInfo): { min: number; max: number } | null;
export function heatFrac(hfr: number, min: number, max: number): number; // 0..1, clamped
```

**Steps**

1. **Add the interfaces + `PreviewInfo.tilt?` + `OverlayToggles.tilt`** in
   `types.ts` at the anchors above.

2. **Add the default flag** in `store.ts` `defaultOverlays()`
   (`store.ts:262-269`): `tilt: false,`. Persistence needs no change —
   `Object.assign(overlays, p.overlays)` (`store.ts:284`) and the whole-object
   serialize (`store.ts:299-305`) pick it up.

3. **Write the tsx test first** (`ui/src/lib/__tests__/tilt.test.ts`, using the
   `eta.test.ts` harness — copy the `test/eq/assert/near` block and the final
   `console.log` + `export const result`):

   ```ts
   import { tiltSummary, zoneHfrRange, heatFrac } from "../tilt";
   import type { TiltInfo } from "../types";

   const mk = (over: Partial<TiltInfo>): TiltInfo => ({
     cols: 3, rows: 3, zones: [], pattern: "uniform", severity: 0.1, worst_zone: null, ...over,
   });

   test("heatFrac clamps and handles degenerate range", () => {
     near(heatFrac(3, 2, 4), 0.5, 1e-9, "mid");
     eq(heatFrac(1, 2, 4), 0, "below min clamps");
     eq(heatFrac(9, 2, 4), 1, "above max clamps");
     eq(heatFrac(3, 4, 4), 0, "min==max -> 0"); // no divide-by-zero
   });

   test("zoneHfrRange ignores null zones, needs >=2 populated", () => {
     const z = (hfr: number | null) => ({ hfr, ecc: null, theta: null, n: hfr ? 4 : 0 });
     const r = zoneHfrRange(mk({ zones: [z(2), z(null), z(4), z(3)] }))!;
     eq(r.min, 2, "min"); eq(r.max, 4, "max");
     assert(zoneHfrRange(mk({ zones: [z(2), z(null)] })) === null, "one populated -> null");
   });

   test("tiltSummary maps each pattern to label + tone", () => {
     eq(tiltSummary(mk({ pattern: "uniform" })).tone, "good", "uniform good");
     eq(tiltSummary(mk({ pattern: "tilt" })).tone, "bad", "tilt bad");
     eq(tiltSummary(mk({ pattern: "coma" })).tone, "warn", "coma warn");
     eq(tiltSummary(mk({ pattern: "tracking" })).tone, "warn", "tracking warn");
     assert(tiltSummary(mk({ pattern: "tilt" })).label.length > 0, "has label");
   });
   ```

4. **Implement `ui/src/lib/tilt.ts`:**

   ```ts
   import type { TiltInfo } from "../types";

   export interface TiltSummary { label: string; tone: "good" | "warn" | "bad"; advice: string }

   const TABLE: Record<TiltInfo["pattern"], TiltSummary> = {
     uniform:  { label: "Uniform", tone: "good",
                 advice: "Stars are round and even across the frame." },
     tilt:     { label: "Sensor tilt", tone: "bad",
                 advice: "HFR is worse on one side — check camera/sensor squareness and spacer tilt." },
     coma:     { label: "Coma / spacing", tone: "warn",
                 advice: "Corners degrade radially vs a sharp center — adjust back-focus / corrector spacing." },
     tracking: { label: "Tracking drift", tone: "warn",
                 advice: "Elongation is uniform in one direction — check guiding / polar alignment." },
   };

   export function tiltSummary(t: TiltInfo): TiltSummary {
     return TABLE[t.pattern] ?? TABLE.uniform;
   }

   export function zoneHfrRange(t: TiltInfo): { min: number; max: number } | null {
     const hs = t.zones.map((z) => z.hfr).filter((h): h is number => h != null);
     if (hs.length < 2) return null;
     return { min: Math.min(...hs), max: Math.max(...hs) };
   }

   export function heatFrac(hfr: number, min: number, max: number): number {
     if (max <= min) return 0;
     return Math.min(1, Math.max(0, (hfr - min) / (max - min)));
   }
   ```

**Commands / expected output**

```
cd ui && npx tsx src/lib/__tests__/tilt.test.ts
# expected: "tilt.test: N/N passed"
cd ui && npx tsc -b
# expected: exit 0, no diagnostics
```

---

### Task 4 — Heatmap overlay render + chip + toolbar toggle

**Impl tier: Sonnet.** Thin SVG render inside the existing transform + one chip +
one toolbar toggle mirroring Stars; all behind the typechecker (no jsdom).

**Files**
- Create: `ui/src/components/preview/TiltOverlay.tsx`
- Modify: `ui/src/components/preview/PreviewStage.tsx`
- Modify: `ui/src/components/preview/PreviewToolbar.tsx`

**Interfaces**

```tsx
// ui/src/components/preview/TiltOverlay.tsx
interface Props {
  tilt: TiltInfo;
  dispW: number;   // display px (== PreviewStage dispW)
  dispH: number;
}
export function TiltOverlay(props: Props): JSX.Element;
```

**Steps**

1. **`TiltOverlay.tsx`** — a `<g>` of `cols×rows` cells sized `dispW/cols` ×
   `dispH/rows`. Per zone: a `<rect>` filled by a heat color at
   `heatFrac(hfr,min,max)` opacity (compute `min/max` once via `zoneHfrRange`;
   dataless zones → muted fill, no tick, no label), a centered HFR `<text>`, and
   an elongation `<line>` through the cell center rotated by `theta`, length
   `∝ ecc`. Color ramp uses the existing `--good/--warn/--bad` tokens
   (mirroring `StarOverlay.tsx:76`) chosen by `heatFrac` bands so the map is not
   color-alone (number + tick are redundant channels, §11.1 / Design §1.5).
   Pointer-events off (it is a passive readout under the star layer). Example
   skeleton:

   ```tsx
   import type { TiltInfo } from "../../types";
   import { zoneHfrRange, heatFrac } from "../../lib/tilt";

   export function TiltOverlay({ tilt, dispW, dispH }: Props) {
     const range = zoneHfrRange(tilt);
     const cw = dispW / tilt.cols, ch = dispH / tilt.rows;
     return (
       <g style={{ pointerEvents: "none" }}>
         {tilt.zones.map((z, i) => {
           const r = Math.floor(i / tilt.cols), c = i % tilt.cols;
           const x = c * cw, y = r * ch;
           const f = z.hfr != null && range ? heatFrac(z.hfr, range.min, range.max) : null;
           const fill = f == null ? "var(--halo)"
             : f < 0.34 ? "var(--good)" : f < 0.67 ? "var(--warn)" : "var(--bad)";
           const op = f == null ? 0.06 : 0.16 + f * 0.22;
           const tickLen = z.ecc != null ? (Math.min(cw, ch) / 2) * 0.7 * z.ecc : 0;
           return (
             <g key={i}>
               <rect x={x} y={y} width={cw} height={ch} fill={fill} fillOpacity={op}
                     stroke="var(--halo)" strokeOpacity={0.5} vectorEffect="non-scaling-stroke" />
               {z.hfr != null && (
                 <text x={x + cw / 2} y={y + ch / 2} textAnchor="middle" dominantBaseline="central"
                       fontSize={Math.max(10, Math.min(cw, ch) * 0.14)} fill="var(--ink)"
                       style={{ paintOrder: "stroke", stroke: "var(--halo)", strokeWidth: 3 }}>
                   {z.hfr.toFixed(2)}
                 </text>
               )}
               {tickLen > 0 && z.theta != null && (
                 <line
                   x1={x + cw / 2 - Math.cos(z.theta) * tickLen}
                   y1={y + ch / 2 - Math.sin(z.theta) * tickLen}
                   x2={x + cw / 2 + Math.cos(z.theta) * tickLen}
                   y2={y + ch / 2 + Math.sin(z.theta) * tickLen}
                   stroke="var(--ink)" strokeWidth={2} vectorEffect="non-scaling-stroke" />
               )}
             </g>
           );
         })}
       </g>
     );
   }
   ```

2. **Wire into `PreviewStage.tsx`.** Import `TiltOverlay` and `tiltSummary`; add a
   `tiltAvailable` derivation next to `starsAvailable` (`PreviewStage.tsx:240`):

   ```tsx
   const tiltAvailable = !!preview?.tilt;
   ```

   Render the heatmap in the overlay svg **before** `StarOverlay`
   (`:355-356`) so stars draw on top:

   ```tsx
   {overlays.tilt && preview.tilt && (
     <TiltOverlay tilt={preview.tilt} dispW={dispW} dispH={dispH} />
   )}
   ```

   Add the classification chip in screen-space chrome, mirroring the selected-star
   readout (`:400-406`), tinted by tone:

   ```tsx
   {overlays.tilt && preview.tilt && (() => {
     const s = tiltSummary(preview.tilt);
     const tint = s.tone === "good" ? "!text-good" : s.tone === "warn" ? "!text-warn" : "!text-bad";
     return (
       <div className={`absolute bottom-2 left-2 preview-chip flex items-center gap-1 ${tint}`}
            aria-live="polite" title={s.advice}>
         Field: {s.label}
       </div>
     );
   })()}
   ```

   (The selected-star chip already owns `bottom-2 left-2`; stack the tilt chip
   with a small `mt` offset or move it to `bottom-2 left-1/2` — a one-line
   position choice; both chips are rarely on together. See Open Decision E.)

3. **Add the toolbar toggle** in `PreviewToolbar.tsx`, after the Stars toggle
   (`PreviewToolbar.tsx:135`), reusing the exact Stars idiom (honest-disabled via
   the shared `Toggle`). It needs `preview` in scope to compute availability —
   the toolbar already receives `preview` (`PreviewToolbar.tsx:67`-region props):

   ```tsx
   <Toggle
     on={overlays.tilt}
     disabled={!preview?.tilt}
     icon="focus"
     label="Tilt"
     title={preview?.tilt ? "Toggle tilt / aberration heatmap" : "No tilt data for this frame"}
     onClick={() => setOverlays({ tilt: !overlays.tilt })}
   />
   ```

   (Confirm the `icon` name against the icon set during impl; `"focus"` /
   `"align"` are used by sibling toggles.)

**Commands / expected output**

```
cd ui && npx tsc -b
# expected: exit 0, no diagnostics
```

Manual smoke (optional, not a gate): run a sim capture, toggle **Tilt** in the
preview toolbar → a 3×3 heatmap with per-zone HFR + ticks appears, and the
`Field: …` chip names the pattern.

---

## 4. Open decisions

**A. Aggregate over `marks` (recommended) vs. the raw `Star` list / a second
pass.** Recommendation: aggregate over `marks`, exactly as `frame_eccentricity`
does (`stars.py:176-181`). The hub already has `marks` (`hub.py:1550`); marks
carry `hfr` on every star and `ecc/theta` only on the trusted mid-bright
population — so the zone map inherits PRO-7's saturation/faint policy with zero
new plumbing and no extra detection cost. Reading raw `Star` objects would
re-implement that policy and thread a new value through the hub.

**B. Classifier thresholds as module constants (recommended) vs. config/plan
knobs.** Recommendation: ship the thresholds (§3 Task 1) as documented module
constants tuned against the synthetic patterns, and treat them as the v1
contract. They are the classifier's whole behavior, so they must be test-pinned;
exposing them as user config invites miscalibration. If field data later shows a
systematic misclassification, promote the one or two that matter — additive, no
contract change to the `info["tilt"]` block.

**C. Heatmap encoding — color + number + tick (recommended) vs. color-only.**
Recommendation: three redundant channels (§1.5) so the map is legible under §11.1
and readable at a glance without hovering. Color-only would fail colorblind
review and hide the actual HFR values that make the map actionable.

**D. Grid fixed at 3×3 v1 (recommended) vs. configurable N×N.** Recommendation:
fix 3×3. The corner-vs-center classifier is cleanest with an odd grid that has a
true center cell, and 3×3 is the standard tilt-inspector layout. `frame_tilt`
takes a `grid=` kwarg so 5×5 is a one-line follow-up once the 3×3 classifier is
validated on-sky; the client already renders `cols×rows` generically.

**E. Classification chip placement (minor).** Recommendation: render the
`Field: …` chip bottom-left (mirroring the selected-star readout,
`PreviewStage.tsx:400-406`) with a small vertical offset so it can coexist with
the selected-star chip; both are niche and rarely on together. A dedicated corner
is not worth the layout churn in v1.

**F. No frame-reject gate in v1 (recommended).** Recommendation: keep the tilt
inspector passive. PRO-7's `max_eccentricity` gate already rejects
"too-elongated" frames; a spatial-pattern gate ("reject if tilted") is a
different, higher-risk policy (a tilted rig tilts every frame — rejecting them
all just stalls the sequence). Defer until the classifier is trusted on real
data. When added, it plugs into `_check_quality` reading `info["tilt"]["pattern"]`
/ `severity`, exactly like the ecc gate.

**G. Sequence-report tilt trend (deferred follow-up).** A per-target "tilt over
the night" summary (does tilt worsen as the OTA cools / the meridian flips?) is a
natural extension but out of v1 scope — it needs report-model plumbing, not just
the per-frame block. Noted for a future pass.
