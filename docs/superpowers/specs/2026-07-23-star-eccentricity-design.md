# PRO-7 — Real per-star eccentricity + max-eccentricity reject gate

Combined design spec + TDD implementation plan. One file.

Status: ready. Slug `star-eccentricity`.

---

## 1. Design

### 1.1 Goal

Three genuinely-missing slices, in dependency order:

1. **Compute real second-moment eccentricity + position angle** in the Python
   `detect_stars` pipeline. The `Star.ecc` / `Star.theta` fields exist but are
   frozen `0.0` placeholders — the detector never computes them.
2. **Wire a plan-level `max_eccentricity` frame-reject gate** — a per-frame
   quality ceiling sibling to the existing `min_stars` / `max_guide_rms` gates.
   The field does **not** exist yet anywhere (server or UI); the *gate machinery*
   it plugs into (`_check_quality`) does.
3. **Draw elongation-oriented star markers** — orient the overlay glyph by
   `theta`, shape it by `ecc`, so trailing / tilt / coma is visible at a glance.

### 1.2 Current-state seams (real file:line, all read)

**The placeholder fields** — `server/astrodeck/imaging/stars.py:25-26`:

```python
ecc: float = 0.0      # Pass 2 (unsaturated mid-bright only); 0.0 placeholder in Pass 1
theta: float = 0.0    # Pass 2 (radians)
```

**Where a second-moment pass belongs** — `detect_stars`
(`stars.py:36-89`). The per-star cutout `cut` is already background-subtracted
and `.clip(0)`'d (`stars.py:74`), and the flux-weighted centroid `cx,cy` plus
the coordinate grids `yy,xx` are already computed for HFR
(`stars.py:78-82`). Second moments reuse *exactly* those arrays — the ecc/theta
computation is ~5 extra numpy reductions on the same 15×15 cutout, immediately
after the HFR guard at `stars.py:83`:

```python
yy, xx = np.mgrid[0:box, 0:box]
cx = float((xx * cut).sum() / total)
cy = float((yy * cut).sum() / total)
r = np.hypot(xx - cx, yy - cy)
hfr = float((r * cut).sum() / total)
if hfr <= 0.05 or hfr > half:
    continue
stars.append(Star(x=..., y=..., flux=total, hfr=hfr, peak=...))
```

**The exposure gate already exists** — `star_marks` only attaches `ecc`/`theta`
to the trusted mid-bright, unsaturated population, and only when `s.ecc` is
truthy (`stars.py:137-144`):

```python
unsaturated_mid = (s.peak > floor and (sat is None or s.peak < sat))
if s.ecc and unsaturated_mid:
    m["ecc"] = round(float(s.ecc), 3)
    m["theta"] = round(float(s.theta), 3)
```

So once `detect_stars` fills real `ecc`, marks light up automatically for the
right stars — no change to `star_marks` needed. (Saturated flat-tops, whose
moments are meaningless, are already filtered here — confirmed by the existing
`test_star_marks_ecc_only_for_unsaturated_midbright`, `test_imaging.py:169-180`.)

**The frame-reject gate** — `SequenceEngine._check_quality`
(`engine.py:1952-1994`). It ANDs three ceilings, each `0 = off`, each skipping
calibration frames. The `max_guide_rms` branch (`engine.py:1984-1990`) is the
exact template for a `max_eccentricity` sibling:

```python
if accepted and not calibration and plan.max_guide_rms > 0:
    rms = self._guide_rms()
    if rms is not None and rms > plan.max_guide_rms:
        self._rejected += 1
        bus.log("warning", f'guide RMS {rms:.2f}" above ceiling '
                           f'{plan.max_guide_rms:.2f}"', "sequence")
        accepted = False
```

The gate reads scalar metrics off `info` (`info.get("hfr")` at
`engine.py:1966`, `info.get("stars")` at `engine.py:1978`). `info` is the hub
capture/preview dict (returned by `_capture`, `engine.py:1002-1011`). It carries
no eccentricity scalar today — we must add `info["ecc"]`.

**Where `info` gets its scalars** — `hub.py:1526-1549`. One detection pass feeds
HFR, star count, and overlay marks:

```python
stars = await asyncio.to_thread(detect_stars, data)
hfr, count, marks = measure_stars(stars, full_well=info["full_well"])
info.update({..., "star_list": marks})
...
if hfr is not None:
    info.setdefault("hfr", round(float(hfr), 2))
    info.setdefault("stars", int(count))
```

`marks` already carry per-star `ecc` for the trusted population, so the frame's
representative eccentricity is just the median of those — no second pass, no new
detection.

**The plan model** — `SequencePlan` (`models.py:59`), plain pydantic `BaseModel`,
no custom validators. Siblings: `hfr_reject_factor` (`models.py:75`),
`min_stars` (`models.py:81`), `max_guide_rms` (`models.py:82`). `max_eccentricity`
slots in right after `max_guide_rms`.

**The UI types** — `StarMark.ecc?`/`.theta?` already declared
(`ui/src/types.ts:178-179`); `PreviewInfo.star_list?` at `types.ts:208`. The plan
interface carries `min_stars?` / `max_guide_rms?` / `max_consecutive_rejects_night?`
at `types.ts:439-442` — `max_eccentricity?` is missing and slots in at line 442.

**The plan editor** — `SequenceView.tsx:967-976`, the `max guide RMS` control, is
the exact idiom (label + inline "off" hint when 0 + numeric field):

```tsx
<label className="flex items-center justify-between gap-2">
  <span className="text-dim">max guide RMS (arcsec)</span>
  <span className="inline-flex items-center gap-2">
    {(plan.max_guide_rms ?? 0) === 0 && (
      <span className="text-[10px] uppercase tracking-widest text-dim">off</span>
    )}
    <input className="field !w-16 !py-1" value={plan.max_guide_rms ?? 0}
      onChange={(e) => setPlan({ ...plan, max_guide_rms: Math.max(0, num(e.target.value, plan.max_guide_rms ?? 0)) })} />
  </span>
</label>
```

**The overlay** — `StarOverlay.tsx:68-111`. Each star is a `<circle>` sized
`r = max(hfr*displayScale*1.6, 4)` (`StarOverlay.tsx:73`), stroke color by HFR
quality, stroke *style* also by HFR (solid/medium/dashed — never color-alone,
§11.1). `theta`/`ecc` on `StarMark` are unused. The circle becomes an ellipse
(rx=r major, ry=r·√(1−ecc²) minor, rotated by theta) when `ecc` is present.

### 1.3 The math (second moments → ecc, PA)

On the background-subtracted, non-negative cutout weighted by pixel value
`w = cut`, with centroid `(cx, cy)`:

```
Ixx = Σ w·(x−cx)²  / Σw        Iyy = Σ w·(y−cy)² / Σw        Ixy = Σ w·(x−cx)(y−cy) / Σw
```

Eigenvalues of the 2×2 covariance (major λ₁, minor λ₂):

```
mean   = (Ixx + Iyy) / 2
common = hypot((Ixx − Iyy)/2, Ixy)
λ₁ = mean + common        λ₂ = mean − common
```

Then the SExtractor-convention eccentricity and position angle:

```
ecc   = sqrt(1 − λ₂/λ₁)          # 0 = round, →1 = a line
theta = 0.5 · atan2(2·Ixy, Ixx − Iyy)   # radians, major-axis angle from +x, (−π/2, π/2]
```

Guards: `λ₁ ≤ 0` → `(0.0, 0.0)`; clamp `λ₂/λ₁` into `[0, 1]` before the sqrt so
float noise can't produce a NaN or ecc > 1. This matches the native Rust
provider, which already emits a real `"eccentricity"` per star
(`test_native_module.py:48`) — the Python path reaches parity, it does not
invent a new metric.

The core is a pure scalar function `_ecc_theta(ixx, iyy, ixy) -> (ecc, theta)` so
the numeric heart is unit-testable with exact hand-computed vectors, independent
of pixel plumbing.

### 1.4 Frame eccentricity metric (for the gate)

The gate needs one scalar per frame. Use the **median** of the trusted marks'
`ecc` (robust; a single hot pixel or diffraction spike shouldn't trip the gate).
The marks already encode the trusted population, so:

```python
def frame_eccentricity(marks: list[dict]) -> float | None:
    eccs = [m["ecc"] for m in marks if "ecc" in m]
    return float(np.median(eccs)) if eccs else None
```

`None` (no trusted stars measured) → the gate abstains, exactly like
`hfr is None` / `stars is None` abstain today. This deliberately does **not**
change the arity of `measure_stars`/`measure_frame` (which would break their
3-tuple unpackers, e.g. `test_measure_frame_single_pass_matches_median_hfr`,
`test_imaging.py:150-160`) — it derives from the marks the hub already has.

### 1.5 Overlay encoding decision (colorblind-safe)

`ecc` is encoded as **shape** (ellipse aspect + orientation), HFR keeps its
existing **color + stroke-style** encoding. Adding elongation as geometry rather
than a second color channel keeps the overlay legible under §11.1 (never
color-alone) and avoids two severities fighting over one hue. See Open Decision A
for the "color by ecc" alternative from the brief.

### 1.6 Placement summary

| Concern | File | Change |
|---|---|---|
| Numeric core | `imaging/stars.py` | `_ecc_theta()` helper + call in `detect_stars` |
| Frame metric | `imaging/stars.py` | `frame_eccentricity(marks)` helper |
| Wire metric onto `info` | `hub.py:~1537` | `info.setdefault("ecc", …)` |
| Plan field | `sequence/models.py:82` | `max_eccentricity: float = 0.0` |
| Gate | `sequence/engine.py:~1990` | `max_eccentricity` branch in `_check_quality` |
| UI plan type | `ui/src/types.ts:442` | `max_eccentricity?: number` |
| UI control | `ui/src/views/SequenceView.tsx:976` | one `<label>` mirroring max-guide-RMS |
| Overlay geometry | `ui/src/lib/starEllipse.ts` (new) | `ellipseGeom()` pure helper |
| Overlay render | `ui/src/components/preview/StarOverlay.tsx` | `<ellipse>` when ecc present |

---

## 2. Global Constraints (verbatim, binding)

- **Privacy.** The real site coordinates `[SITE-LAT]` / `[SITE-LON]` and the label
  `"[SITE-LABEL]"` must NEVER appear in code, tests, or docs. The site default is
  `"My Observatory"` / `0.0`. (This feature touches none of it — no coordinate,
  no site label anywhere in the diff. Stated to remain binding.)
- **Never `git add -A`.** Stage named paths only.
- **UI typecheck gate:** `cd ui && npx tsc -b`.
- **NO jsdom.** Pure logic is tested via `npx tsx` inline-assert, idiom
  `ui/src/lib/__tests__/eta.test.ts` and
  `ui/src/components/__tests__/healthStrip.test.ts`. Thin render is verified by the
  typechecker only.
- **Backend tests:** `server/.venv/Scripts/pytest.exe` run from the **repo root**,
  single process (`-n0`).
- **Rust:** `native/` has its own cargo tests — `cargo test` in the crate. (No
  Rust work in this feature; the native provider already computes eccentricity.)
- **Client toasts** via `useStore.getState().enqueueToast`.
- **Honest-disabled idiom (§11.8):** dim token + lock glyph + `aria-disabled` +
  `title`, never native `disabled`. (The `max_eccentricity` control uses the
  existing "off" inline-hint idiom of its sibling gates; no disabled control is
  introduced.)
- **Do not disrupt astrotown.**

---

## 3. TDD Plan

### Task 1 — Real per-star ecc/theta in `detect_stars` (numeric core)

**Impl tier: Opus.** Genuinely subtle: eigenvalue/position-angle derivation,
truncation-aware tolerances, and correctly *flipping* an existing test that
asserts the old placeholder behaviour. Getting the atan2 convention and the
λ-clamp guards right is correctness-critical.

**Files**
- Modify: `server/astrodeck/imaging/stars.py`
- Modify (test): `server/tests/test_imaging.py`

**Interfaces**

```python
def _ecc_theta(ixx: float, iyy: float, ixy: float) -> tuple[float, float]:
    """Second-moment eccentricity and major-axis position angle.

    ecc in [0,1] (0 = round); theta in radians, (−π/2, π/2], measured from +x.
    Degenerate/negative covariance → (0.0, 0.0)."""
```

`Star.ecc` / `Star.theta` keep their existing types (`float`), just populated.

**Steps**

1. **Write the exact-vector test first** (`test_imaging.py`), importing
   `_ecc_theta` from `astrodeck.imaging.stars`:

   ```python
   import math
   from astrodeck.imaging.stars import _ecc_theta

   def test_ecc_theta_exact_vectors():
       # round: equal moments, no cross term
       e, t = _ecc_theta(4.0, 4.0, 0.0)
       assert e == 0.0 and t == 0.0
       # horizontal elongation Ixx>Iyy: ecc=sqrt(1-1/9), PA=0
       e, t = _ecc_theta(9.0, 1.0, 0.0)
       assert abs(e - math.sqrt(8/9)) < 1e-9 and abs(t) < 1e-9
       # vertical elongation Iyy>Ixx: same ecc, PA=+pi/2
       e, t = _ecc_theta(1.0, 9.0, 0.0)
       assert abs(e - math.sqrt(8/9)) < 1e-9 and abs(t - math.pi/2) < 1e-9
       # 45 deg: equal diagonal, cross term -> PA=pi/4
       e, t = _ecc_theta(5.0, 5.0, 4.0)
       assert abs(e - math.sqrt(8/9)) < 1e-9 and abs(t - math.pi/4) < 1e-9
       # degenerate guard
       assert _ecc_theta(0.0, 0.0, 0.0) == (0.0, 0.0)
   ```

2. **Implement `_ecc_theta`** in `stars.py` (add `import math` at top):

   ```python
   def _ecc_theta(ixx: float, iyy: float, ixy: float) -> tuple[float, float]:
       mean = (ixx + iyy) / 2.0
       common = math.hypot((ixx - iyy) / 2.0, ixy)
       lam1 = mean + common            # major eigenvalue
       lam2 = mean - common            # minor eigenvalue
       if lam1 <= 0.0:
           return 0.0, 0.0
       ratio = min(1.0, max(0.0, lam2 / lam1))
       ecc = math.sqrt(1.0 - ratio)
       theta = 0.5 * math.atan2(2.0 * ixy, ixx - iyy)
       return float(ecc), float(theta)
   ```

3. **Wire it into `detect_stars`** — right after the HFR guard (`stars.py:83`),
   reusing `xx, yy, cut, cx, cy, total` already in scope:

   ```python
   dx = xx - cx
   dy = yy - cy
   ixx = float((dx * dx * cut).sum() / total)
   iyy = float((dy * dy * cut).sum() / total)
   ixy = float((dx * dy * cut).sum() / total)
   ecc, theta = _ecc_theta(ixx, iyy, ixy)
   stars.append(Star(
       x=x - half + cx, y=y - half + cy, flux=total,
       hfr=hfr, peak=float(img[y, x]), ecc=ecc, theta=theta,
   ))
   ```

4. **Add the synthetic-blob integration test** — proves the pixel→moment→ecc
   chain works end to end on a real elongated PSF:

   ```python
   def _elongated_blob(sx: float, sy: float, angle: float = 0.0,
                       shape=(120, 120), flux=3.0e5) -> np.ndarray:
       cx, cy = shape[1] / 2, shape[0] / 2
       yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
       xr = (xx - cx) * math.cos(angle) + (yy - cy) * math.sin(angle)
       yr = -(xx - cx) * math.sin(angle) + (yy - cy) * math.cos(angle)
       g = flux * np.exp(-(xr**2 / (2*sx**2) + yr**2 / (2*sy**2)))
       img = np.full(shape, 500.0) + g
       return np.clip(img, 0, 65535).astype(np.uint16)

   def test_detect_stars_measures_elongation():
       stars = detect_stars(_elongated_blob(sx=2.2, sy=1.1))   # analytic ecc ~0.866
       assert stars, "expected a detection"
       s = max(stars, key=lambda s: s.flux)
       assert 0.6 < s.ecc < 0.95          # box truncation lowers it below analytic
       assert abs(s.theta) < 0.2          # major axis ~ +x

   def test_detect_stars_round_is_low_ecc():
       stars = detect_stars(_elongated_blob(sx=1.5, sy=1.5))
       s = max(stars, key=lambda s: s.flux)
       assert s.ecc < 0.25

   def test_detect_stars_pa_tracks_rotation():
       stars = detect_stars(_elongated_blob(sx=2.2, sy=1.1, angle=math.pi/4))
       s = max(stars, key=lambda s: s.flux)
       assert abs(abs(s.theta) - math.pi/4) < 0.25
   ```

5. **Flip the now-stale Pass-1 assertion.** `test_star_marks_shape_and_coords`
   (`test_imaging.py:137-147`) currently asserts `"ecc" not in m`. With real ecc
   computed, `star_marks` attaches it for the (unsaturated, mid-bright) synthetic
   stars. Update to the Pass-2 expectation:

   ```python
   def test_star_marks_shape_and_coords():
       img = synthetic_field(n_stars=20)
       stars = detect_stars(img)
       marks = star_marks(stars)
       assert isinstance(marks, list) and len(marks) >= 12
       assert any("ecc" in m for m in marks)        # Pass 2 is now live
       for m in marks:
           assert set(m) >= {"x", "y", "hfr"}
           assert 0 <= m["x"] <= img.shape[1]
           assert 0 <= m["y"] <= img.shape[0]
           assert m["hfr"] > 0
           if "ecc" in m:
               assert 0.0 <= m["ecc"] <= 1.0 and "theta" in m
       # round synthetic stars -> low typical elongation
       eccs = [m["ecc"] for m in marks if "ecc" in m]
       assert eccs and float(np.median(eccs)) < 0.5
   ```

**Commands / expected output**

```
server/.venv/Scripts/pytest.exe server/tests/test_imaging.py -n0 -q
# expected: all pass, incl. test_ecc_theta_exact_vectors,
# test_detect_stars_measures_elongation, _round_is_low_ecc, _pa_tracks_rotation,
# and the updated test_star_marks_shape_and_coords
```

---

### Task 2 — Frame metric + `max_eccentricity` gate

**Impl tier: Sonnet.** Mechanical: one median helper, one `info.setdefault`,
one plan field, one gate branch copied from the `max_guide_rms` template.

**Files**
- Modify: `server/astrodeck/imaging/stars.py` (add `frame_eccentricity`)
- Modify: `server/astrodeck/imaging/__init__.py` (export it, next to `star_marks`)
- Modify: `server/astrodeck/hub.py` (set `info["ecc"]`)
- Modify: `server/astrodeck/sequence/models.py` (add plan field)
- Modify: `server/astrodeck/sequence/engine.py` (`_check_quality` branch)
- Modify (test): `server/tests/test_imaging.py`, `server/tests/test_sequence_engine_fixes.py`

**Interfaces**

```python
# imaging/stars.py
def frame_eccentricity(marks: list[dict]) -> float | None: ...

# sequence/models.py — SequencePlan, after max_guide_rms (line 82)
max_eccentricity: float = 0.0   # per-frame median-ecc ceiling, 0..1 (0 = off)
```

**Steps**

1. **`frame_eccentricity` + its test** (test first, `test_imaging.py`):

   ```python
   from astrodeck.imaging.stars import frame_eccentricity

   def test_frame_eccentricity_median_of_trusted():
       assert frame_eccentricity([]) is None
       assert frame_eccentricity([{"x": 1, "y": 1, "hfr": 2.0}]) is None  # no ecc key
       marks = [{"x": 1, "y": 1, "hfr": 2.0, "ecc": 0.2, "theta": 0.0},
                {"x": 2, "y": 2, "hfr": 2.0, "ecc": 0.4, "theta": 0.0},
                {"x": 3, "y": 3, "hfr": 2.0, "ecc": 0.6, "theta": 0.0}]
       assert abs(frame_eccentricity(marks) - 0.4) < 1e-9
   ```

   Implementation (in `stars.py`):

   ```python
   def frame_eccentricity(marks: list[dict]) -> float | None:
       """Representative frame eccentricity: median of the trusted marks' ``ecc``
       (the mid-bright unsaturated population ``star_marks`` already attached ecc
       to). ``None`` when no star carried an ecc — the gate then abstains."""
       eccs = [m["ecc"] for m in marks if "ecc" in m]
       return float(np.median(eccs)) if eccs else None
   ```

   Add `frame_eccentricity` to the re-export list in
   `imaging/__init__.py` (alongside `star_marks`, `measure_frame`, `measure_stars`).

2. **Set `info["ecc"]` in the hub.** In `hub.py`, right where marks land on `info`
   (after `info.update({..., "star_list": marks})`, `hub.py:1536`):

   ```python
   fecc = frame_eccentricity(marks)
   if fecc is not None:
       info.setdefault("ecc", round(fecc, 3))
   ```

   Add `frame_eccentricity` to the existing `from ...imaging import (... measure_stars ...)`
   at `hub.py:43`. `setdefault` keeps a backend-supplied ecc (native/NINA) winning,
   mirroring the `hfr`/`stars` treatment at `hub.py:1547-1549`.

3. **Add the plan field.** `models.py`, immediately after `max_guide_rms`
   (`models.py:82`):

   ```python
   max_eccentricity: float = 0.0            # per-frame median-ecc ceiling, 0..1 (0 = off)
   ```

4. **Add the gate branch + test.** Gate test first
   (`test_sequence_engine_fixes.py`, mirroring `test_rejected_frames_do_not_feed_hfr_median`
   at line 382):

   ```python
   async def test_max_eccentricity_gate(sim_hub):
       engine = SequenceEngine(sim_hub)
       engine.plan = SequencePlan(name="p", targets=[], max_eccentricity=0.6)
       assert engine._check_quality({"ecc": 0.80}) is False   # elongated -> reject
       assert engine._check_quality({"ecc": 0.50}) is True    # round enough -> keep
       assert engine._check_quality({"ecc": 0.80}, calibration=True) is True  # calib skips
       assert engine._check_quality({}) is True               # no ecc -> abstain
       engine.plan = SequencePlan(name="p", targets=[])       # 0 = off
       assert engine._check_quality({"ecc": 0.99}) is True
   ```

   Then the branch in `_check_quality`, after the `max_guide_rms` block
   (`engine.py:1990`) and before the record-fold (`engine.py:1991`):

   ```python
   if accepted and not calibration and plan.max_eccentricity > 0:
       ecc = info.get("ecc") if isinstance(info, dict) else None
       if ecc is not None and float(ecc) > plan.max_eccentricity:
           self._rejected += 1
           bus.log("warning", f"frame eccentricity {float(ecc):.2f} above ceiling "
                              f"{plan.max_eccentricity:.2f} — trailing/tilt", "sequence")
           accepted = False
   ```

**Commands / expected output**

```
server/.venv/Scripts/pytest.exe server/tests/test_imaging.py server/tests/test_sequence_engine_fixes.py -n0 -q
# expected: all pass, incl. test_frame_eccentricity_median_of_trusted and test_max_eccentricity_gate
```

Also run a quick guard that nothing else asserted an exact `info` key-set:

```
server/.venv/Scripts/pytest.exe server/tests -n0 -q -k "preview or hub or quality"
# expected: green (info["ecc"] is additive via setdefault)
```

---

### Task 3 — UI: plan type + control + elongation-oriented markers

**Impl tier: Sonnet.** The geometry helper is simple trig; the control mirrors an
existing sibling; the `<ellipse>` swap is thin render behind the typechecker.

**Files**
- Modify: `ui/src/types.ts` (plan field)
- Modify: `ui/src/views/SequenceView.tsx` (one control)
- Create: `ui/src/lib/starEllipse.ts` (pure geometry)
- Create (test): `ui/src/lib/__tests__/starEllipse.test.ts`
- Modify: `ui/src/components/preview/StarOverlay.tsx` (render ellipse)

**Interfaces**

```ts
// ui/src/types.ts — SequencePlan interface, after max_consecutive_rejects_night (line 442)
max_eccentricity?: number; // per-frame median-ecc ceiling, 0..1 (0 = off)

// ui/src/lib/starEllipse.ts
export interface EllipseGeom { rx: number; ry: number; rotDeg: number }
export function ellipseGeom(
  rCircle: number,          // the existing circle radius (display px)
  ecc: number | undefined,  // StarMark.ecc
  theta: number | undefined,// StarMark.theta (radians)
): EllipseGeom | null;      // null => draw the plain circle (Pass-1 / no ecc)
```

**Steps**

1. **Add the plan type field** (`types.ts:442`, after `max_consecutive_rejects_night?`):

   ```ts
   max_eccentricity?: number; // per-frame median-ecc ceiling, 0..1 (0 = off)
   ```

2. **Geometry helper + tsx test.** Test first
   (`ui/src/lib/__tests__/starEllipse.test.ts`, using the `eta.test.ts`
   inline-assert harness — `test`, `near`, `assert`, final `console.log` +
   `export const result`):

   ```ts
   import { ellipseGeom } from "../starEllipse";

   test("no ecc -> null (plain circle)", () => {
     assert(ellipseGeom(10, undefined, undefined) === null, "undefined ecc");
   });
   test("round star -> near-circular", () => {
     const g = ellipseGeom(10, 0.0, 0.0)!;
     near(g.rx, 10, 1e-9, "rx"); near(g.ry, 10, 1e-9, "ry"); near(g.rotDeg, 0, 1e-9, "rot");
   });
   test("ecc 0.866 -> minor = major/2", () => {
     const g = ellipseGeom(10, 0.866, 0.0)!;   // sqrt(1-0.866^2)=0.5
     near(g.rx, 10, 1e-6, "major"); near(g.ry, 5, 0.02, "minor");
   });
   test("theta maps to degrees", () => {
     const g = ellipseGeom(10, 0.7, Math.PI / 4)!;
     near(g.rotDeg, 45, 1e-6, "rot deg");
   });
   ```

   Implementation:

   ```ts
   export interface EllipseGeom { rx: number; ry: number; rotDeg: number }

   export function ellipseGeom(
     rCircle: number,
     ecc: number | undefined,
     theta: number | undefined,
   ): EllipseGeom | null {
     if (ecc == null) return null;
     const e = Math.min(1, Math.max(0, ecc));
     const aspect = Math.sqrt(1 - e * e); // minor/major = b/a
     return { rx: rCircle, ry: rCircle * aspect, rotDeg: ((theta ?? 0) * 180) / Math.PI };
   }
   ```

3. **Render the ellipse** in `StarOverlay.tsx`. Inside the `shown.map` body
   (`StarOverlay.tsx:70-108`), compute the geometry once and swap the two visible
   `<circle>` strokes (the halo under-stroke at `:95` and the colored stroke at
   `:96-105`) for `<ellipse>` when non-null, keeping the same `stroke`, `sw`,
   `dash`, `vectorEffect`, and the invisible hit `<circle r={r+8}>`:

   ```tsx
   const geom = ellipseGeom(r, s.ecc, s.theta);
   // ...
   {geom ? (
     <>
       <ellipse cx={cx} cy={cy} rx={geom.rx} ry={geom.ry}
         transform={`rotate(${geom.rotDeg} ${cx} ${cy})`}
         fill="none" stroke="var(--halo)" strokeWidth={sw + 2} vectorEffect="non-scaling-stroke" />
       <ellipse cx={cx} cy={cy} rx={geom.rx} ry={geom.ry}
         transform={`rotate(${geom.rotDeg} ${cx} ${cy})`}
         fill="none" stroke={color} strokeWidth={isSel ? sw + 1 : sw}
         strokeDasharray={dash} vectorEffect="non-scaling-stroke" />
     </>
   ) : (
     /* existing two <circle> strokes unchanged */
   )}
   ```

   Import `ellipseGeom` from `../../lib/starEllipse`. HFR keeps color+style; ecc is
   pure shape (Design §1.5).

4. **Add the plan control** in `SequenceView.tsx`, after the `max guide RMS`
   label block (`SequenceView.tsx:976`), clamped to 0..1:

   ```tsx
   <label className="flex items-center justify-between gap-2">
     <span className="text-dim inline-flex items-center gap-1">
       max eccentricity (0–1)
       <InfoDot label="About the eccentricity gate"
         content="Reject a frame whose stars are too elongated (trailing / tilt / coma): the median star eccentricity across the frame. 0 = off." />
     </span>
     <span className="inline-flex items-center gap-2">
       {(plan.max_eccentricity ?? 0) === 0 && (
         <span className="text-[10px] uppercase tracking-widest text-dim">off</span>
       )}
       <input className="field !w-16 !py-1" value={plan.max_eccentricity ?? 0}
         onChange={(e) => setPlan({ ...plan, max_eccentricity: Math.min(1, Math.max(0, num(e.target.value, plan.max_eccentricity ?? 0))) })} />
     </span>
   </label>
   ```

**Commands / expected output**

```
cd ui && npx tsx src/lib/__tests__/starEllipse.test.ts
# expected: "starEllipse.test: 4/4 passed"
cd ui && npx tsc -b
# expected: exit 0, no diagnostics
```

---

## 4. Open decisions

**A. Overlay: encode ecc as shape (recommended) vs. color (brief's wording).**
The brief says "color by ecc". Recommendation: encode ecc as **shape** (ellipse
aspect + orientation) and keep HFR as the marker's color+style. Reason: HFR
already owns the color channel with a colorblind-safe style fallback (§11.1);
adding a second color severity fights it. Shape reads elongation instantly and is
colorblind-safe. If a color cue is still wanted, add it as a follow-up: a thin
accent tick along the major axis tinted by an ecc ramp — additive, non-breaking.

**B. Frame metric: median (recommended) vs. max eccentricity.** The field is
named `max_eccentricity` (a ceiling), but the frame *statistic* it compares
against should be the **median** trusted-star ecc, not the literal per-star max —
one hot pixel or diffraction spike shouldn't reject an otherwise-good frame.
Recommendation: median (as designed). If a user genuinely wants "reject if *any*
star trails," that's a separate future knob, not this gate.

**C. Compute ecc for all detected stars vs. only the trusted subset.**
Recommendation: compute in `detect_stars` for **every** star (the moments are ~5
cheap reductions on a cutout that already exists), and let the existing
`star_marks` exposure gate (`stars.py:137-144`) decide who publishes ecc.
Simpler and keeps `detect_stars` free of population policy; saturated flat-tops
get a value but it's never surfaced or gated on.

**D. UI plan control placement.** Recommendation: put `max eccentricity` directly
under `max guide RMS` in the existing multi-night quality-gate group
(`SequenceView.tsx:967-976`) — it's a per-frame quality ceiling of the same
family. No new section, no honest-disabled control (uses the sibling "off"
inline-hint idiom).
