# NOV-12 — Bahtinov-mask focus aid with an explicit "you're focused" callout (novice)

Combined design spec + TDD implementation plan. One file.

Slug: `bahtinov-focus`

---

## 1. Design

### 1.1 Goal

A live analyzer that, while a Bahtinov mask is on the scope and pointed at a
bright star, detects the mask's three diffraction spikes, measures the
central-spike offset from the crossing of the two outer spikes, and shows the
novice a plain go/stop callout:

> **middle spike is 3.2 px off — nudge the focuser** → **PERFECT — locked**

The Bahtinov mask is the most common beginner focusing tool, and its "did I nail
it?" moment is exactly the kind of yes/no the rest of AstroDeck's novice surface
(NOV-6 one-tap focus, FocusVerdict) already answers in words. The
correctness-critical core is a real computer-vision routine: fit the three
spikes, compute the signed central offset. That core is a **pure, pytest'd
function over a synthetic Bahtinov pattern** — no camera, no UI.

**Scope v1 (shippable, tested):** the analyzer (pure core) + arming lifecycle +
an additive `bahtinov` field on the `preview` event + a plain verdict panel in
the Focus view. **Deferred follow-ons** (§5): drawing the fitted spikes/offset
marker as a canvas overlay on the live preview; auto-learning the physical
IN/OUT direction; auto-fitting the mask half-angle.

### 1.2 Current-state seams (every one read at real file:line)

**Star centering — reuse, do not reinvent.**
- `server/astrodeck/imaging/stars.py:54` — `detect_stars(data, k_sigma=5.0, max_stars=200, box=15) -> list[Star]`. The classic background-subtract → k-sigma → local-max → centroid pipeline. Already runs once per live sub.
- `server/astrodeck/imaging/stars.py:19-27` — `Star(x, y, flux, hfr, peak, ecc, theta)`. Coords are in `frame.data` pixel space.
- `server/astrodeck/imaging/livestack.py:21` — `brightest_centroid(stars) -> tuple[float,float] | None` picks the highest-**flux** star (detect appends in peak order, so it selects by flux explicitly). This is the exact anchor the Bahtinov analysis centers on.

**Frame access + the one-detection-pass block.**
- `server/astrodeck/hub.py:1462` — `_publish_preview(frame)` builds the `preview` event. In the raw/linear branch (`server/astrodeck/hub.py:1521-1587`), `sub` (the linear `np.ndarray`) and `stars = detect_stars(sub)` are already in scope. Cloud detection attaches here (`hub.py:1571-1574`, `info["cloud"]`), eccentricity here (`hub.py:1564-1566`, `info["ecc"]`), HFR/stars here (`hub.py:1576-1578`). **A gated Bahtinov analysis attaches in exactly this block** — one more consumer of the already-computed `sub` + `stars`, mirroring `cloud`.
- `server/astrodeck/hub.py:1582-1587` — the **decisive constraint**: `entry.linear = None`. The ~125 MB linear uint16 array is deliberately **not retained** past the frame (P3-1; `/crop` and `/render` are 501 stubs — `app.py:2580-2592`). So a "analyze the current frame on demand" endpoint has no pixels to analyze without either re-enabling that retention or firing a fresh exposure that fights the live loop (camera mutual exclusion — `app.py:2827`). **This is why the design is an armed additive field on the preview event, not a pull endpoint over a stored frame.** (Open decision §4.1.)

**Arming lifecycle — mirror `live_stacker`.**
- `server/astrodeck/hub.py:183` — `self.live_stacker = None` (nullable accumulator, feature-off default).
- `server/astrodeck/hub.py:1531-1541` — `_publish_preview` checks `if self.live_stacker is not None:` and consumes the sub; a no-op when unarmed.
- `server/astrodeck/hub.py:1793-1808` — `start_live_stack` / `reset_live_stack` / `stop_live_stack`. The arm/disarm precedent.
- `server/astrodeck/hub.py:2305` — `out["live_stack_active"] = self.live_stacker is not None` in the status dict. Bahtinov adds `bahtinov_active` the same way, so the UI can reflect + honest-disable the toggle.

**Endpoints — mirror the livestack routes.**
- `server/astrodeck/api/app.py:2436-2461` — `POST /api/capture/livestack/{start,reset,stop}`: guard (`engine.running`/`polar` → 409), `hub.require("camera")`, arm, `hub.start_loop(...)`. The endpoint template.
- `server/astrodeck/api/app.py:2811-2863` — `POST /api/focuser/{move,autofocus,halt}` — the focuser route neighborhood the new routes live in; `2827` shows the `engine.running or hub.looping` → 409 camera-busy guard.
- `server/astrodeck/api/app.py:437-450, 490-497` — `CaptureBody`(exposure_s/gain/offset/binning) → `LiveStackBody`, and `AutofocusBody`. The request-body precedent to mirror for `BahtinovBody`.

**Pure-analyzer module precedent.**
- `server/astrodeck/imaging/clouds.py:1-56` — `cloud_score(sub, stars=...) -> CloudResult` with `CloudResult.to_dict()`. A pure "analyze one linear frame → dataclass → dict for the preview payload" module. `bahtinov.py` is built to the same shape (dataclass + `to_dict()`).
- `server/astrodeck/imaging/__init__.py:14-31` — the imaging package's public exports (`detect_stars`, `cloud_score`, `brightest_centroid`, …). Bahtinov exports are added here.
- No `scipy` anywhere under `astrodeck/` (grep clean) — the Radon transform is hand-rolled in numpy (`np.bincount` weighted histogram per angle). numpy is the only dependency, matching `stars.py`/`clouds.py`.

**Client seams.**
- `ui/src/types.ts:212-241` — `PreviewInfo`. Additive optional `bahtinov?: BahtinovInfo` field (old clients ignore it — same policy as `livestack?` at `types.ts:239` and `cloud`).
- `ui/src/views/FocusView.tsx:47` — `const shown = useLivePreview();` and `ui/src/views/FocusView.tsx:123` — the `FocusVerdict` header slot. The Bahtinov panel is a sibling Panel in this view reading `shown.bahtinov`.
- `ui/src/components/preview/FocusVerdict.tsx:73-144` — the live-frame verdict render (word + tone class + `role="status" aria-live="polite"`, never color alone). `BahtinovAid.tsx` mirrors this structure.
- `ui/src/lib/autofocus.ts:271-300` — `plainFocusVerdict(...)` — a pure `{level,tone,headline,detail}` mapping, no React. The precedent for `lib/bahtinov.ts`'s pure verdict mapping.
- `ui/src/lib/__tests__/eta.test.ts:1-197` — the tsx inline-assert harness idiom (`npx tsx …`, no jsdom). The Bahtinov lib test copies this harness verbatim.
- `ui/src/store.ts:643` (`enqueueToast`), `ui/src/store.ts:680`/`1198` (`showToast` → `enqueueToast`) — client toast helpers. FocusView already imports `showToast` (`FocusView.tsx:42`).

### 1.3 Approach

**Data / behavior.** The user taps **"Bahtinov focus"** in the Focus view. That
arms a hub flag (`hub.bahtinov`) and ensures the live loop is running (mirroring
`livestack_start`). On every raw/linear sub, `_publish_preview` — already holding
`sub` and `stars` — runs `analyze_bahtinov(sub, center=brightest_centroid(stars))`
in a worker thread and attaches `info["bahtinov"]`. The client reads
`shown.bahtinov` and renders a plain go/stop callout that updates each frame.
Tapping again disarms.

**Algorithm (the tested core).** Center on the brightest star. Crop a square ROI
(default half=128 px, clamped to frame). Background-subtract (median), clip
negatives, and zero a small central disk so the star blob doesn't dominate. Then:

1. **Discrete Radon transform** `R(φ, ρ)` over a normal-angle grid φ ∈ [0,180) at
   0.5° steps: for each φ, `ρ = x·cosφ + y·sinφ` per pixel, `R[φ] =
   bincount(round(ρ), weights=intensity)`. A bright straight spike with normal
   angle φ₀ makes a sharp tall peak in the ρ-profile at φ≈φ₀. (No scipy; pure
   numpy — one `bincount` per angle over a small ROI.)
2. **Spike angles.** Line strength `S(φ) = max_ρ R(φ,ρ) − median_ρ R(φ,ρ)` (peak
   prominence, robust to a diffuse pedestal). The three strongest, well-separated
   (≥ ~5° apart) local maxima are the three spike normal-angles. A Bahtinov mask
   makes them symmetric: `{μ, μ−α, μ+α}`.
3. **Label + sub-pixel ρ.** The circular-median angle is the **central** spike;
   the other two are the **outer** pair. For each spike, parabolic-interpolate the
   Radon peak to a sub-pixel `ρ`. Each spike is a line `n·p = ρ`, `n=(cosφ,sinφ)`.
4. **Vertex** `V` = intersection of the two outer lines (2×2 solve).
5. **Signed offset** = signed perpendicular distance of `V` from the central line
   = `n_c·V − ρ_c`, in pixels. In focus → 0; defocus shifts the central spike
   sideways, so |offset| grows and its **sign** flips as you cross best focus.
6. **Validity gate.** Require three spikes above a strength floor **and** rough
   symmetry (`|(μ−φ_left) − (φ_right−μ)|` small); otherwise `valid=False,
   reason="point at a bright star through the mask"` — the UI shows guidance, not
   a bogus number.
7. **Verdict.** `|offset| ≤ tol_px` (default 1.5 px) → **locked**; else **off**,
   with a geometric side ("middle spike sits LEFT/RIGHT of the crossing") that is
   always unambiguously correct from the image, plus an optional per-rig
   `invert` flag translating side→"turn IN/OUT" once the user calibrates
   direction (§4.2 — we do not fabricate the physical mapping).

**Device protocol.** None. This is pure image analysis over frames the existing
capture path already produces; no new driver, no `base.py` device. The focuser
already has `move`/`halt` (`app.py:2811,2851`) — the user turns the focuser
by hand or with those buttons; Bahtinov only *reads* frames.

**Placement.**
- `server/astrodeck/imaging/bahtinov.py` — new pure module (core + wrapper + result + verdict).
- `server/astrodeck/imaging/__init__.py` — export `analyze_bahtinov`, `BahtinovResult`.
- `server/astrodeck/hub.py` — armed state + `arm_bahtinov`/`disarm_bahtinov` + gated call in `_publish_preview` + `bahtinov_active` in status.
- `server/astrodeck/api/app.py` — `BahtinovBody` + `POST /api/focuser/bahtinov/{start,stop}`.
- `ui/src/types.ts` — `BahtinovInfo` + `PreviewInfo.bahtinov?`.
- `ui/src/lib/bahtinov.ts` + `ui/src/lib/__tests__/bahtinov.test.ts` — pure verdict mapping + test.
- `ui/src/components/preview/BahtinovAid.tsx` — render.
- `ui/src/views/FocusView.tsx` — panel + arm/disarm toggle.

---

## 2. Global Constraints (verbatim — apply to every task)

- **Privacy.** The real coordinates `[SITE-LAT]` / `[SITE-LON]` and the label
  `"[SITE-LABEL]"` must NEVER appear in code, tests, or docs. Site defaults are
  `"My Observatory"` / `0.0`. (This feature touches no site/location data — keep
  it that way.)
- **Never `git add -A`.** Stage only the specific files you changed, by path.
- **UI gate.** `cd ui && npx tsc -b` must pass with zero errors before any UI
  task is done.
- **NO jsdom.** UI logic is tested as pure functions via `npx tsx` inline-assert
  (idiom: `ui/src/lib/__tests__/eta.test.ts`). No test renderer.
- **Backend tests** run with `server/.venv/Scripts/pytest.exe` from the repo
  root, `-n0` (single worker, no xdist) for these targeted runs.
- **Device drivers** follow the existing `base.py` `Device`/ABC + the
  `_DEV_TYPE_TO_ROLE` + hub-singleton pattern. (N/A here — no new device.)
- **Client toasts** via `useStore.getState().enqueueToast` (or the `showToast`
  wrapper the view already imports).
- **Honest-disabled (§11.8):** a disabled control is dimmed + locked +
  `aria-disabled` + `title` explaining why — never the native `disabled`
  attribute on the primary action. (Focuser buttons show the pattern already —
  `FocusView.tsx:256-265`.)
- **Do not disrupt astrotown.** No deploys, no config pushes, no touching the
  running box. Code + local tests only.

---

## 3. TDD Plan

Task order is dependency order. Each task: write the test first, watch it fail,
implement, watch it pass, run the typecheck/pytest command shown, confirm the
expected output.

### Interfaces (exact signatures — freeze these)

```python
# server/astrodeck/imaging/bahtinov.py
from __future__ import annotations
from dataclasses import dataclass
import numpy as np

# ---- pure numeric core (Task 1) ----
def _radon(roi: np.ndarray, angles_rad: np.ndarray,
           cx: float, cy: float) -> tuple[np.ndarray, np.ndarray]:
    """Discrete Radon over normal-angles. Returns (R, rhos):
    R.shape == (len(angles_rad), len(rhos)); R[i, j] = Σ intensity of pixels whose
    signed distance round(x·cosφ + y·sinφ) maps to rhos[j], measured from (cx,cy)."""

def _subpixel_peak(profile: np.ndarray) -> tuple[float, float]:
    """(argmax as float via 3-point parabola, peak value). Integer argmax at the ends."""

def _spike_angles(strength: np.ndarray, angles_rad: np.ndarray, *,
                  n: int = 3, min_sep_rad: float = np.deg2rad(5.0)) -> list[int]:
    """Indices of the n strongest, ≥min_sep_rad-separated local maxima of strength."""

def _intersect(line_a: tuple[float, float, float],
               line_b: tuple[float, float, float]) -> tuple[float, float] | None:
    """Intersection of two lines n·p = ρ, each (nx, ny, ρ). None if near-parallel."""

def bahtinov_offset(roi: np.ndarray, cx: float, cy: float, *,
                    n_angles: int = 360, core_mask_px: float = 8.0
                    ) -> tuple[float | None, list[float], bool, str]:
    """Fit 3 spikes in a background-subtracted ROI centered at (cx,cy).
    Returns (signed_offset_px | None, spike_angles_deg[3], valid, reason)."""

# ---- wrapper + result + verdict (Task 2) ----
@dataclass
class BahtinovResult:
    valid: bool
    offset_px: float | None       # signed; None when invalid
    in_focus: bool                # |offset| <= tol_px  (False when invalid)
    side: str | None              # "left" | "right" | None (geometric)
    direction: str | None         # "in" | "out" | None (side flipped by invert)
    angles_deg: list[float]       # the 3 spike angles (empty when invalid)
    center: tuple[float, float] | None
    tol_px: float
    reason: str                   # plain-language status / why-invalid
    def to_dict(self) -> dict: ...

def analyze_bahtinov(data: np.ndarray, center: tuple[float, float] | None = None, *,
                     stars: list | None = None, half: int = 128,
                     tol_px: float = 1.5, invert: bool = False) -> BahtinovResult:
    """Full analyzer: pick brightest star if center is None, crop ROI, run
    bahtinov_offset, build the verdict. Never raises on a starless/blank frame —
    returns BahtinovResult(valid=False, ...)."""
```

```python
# server/astrodeck/api/app.py  (mirror LiveStackBody / AutofocusBody)
class BahtinovBody(CaptureBody):     # exposure_s/gain/offset/binning inherited
    tol_px: float = 1.5
    invert: bool = False
```

```python
# server/astrodeck/hub.py
self.bahtinov: dict | None = None    # None = off; {"tol_px":..., "invert":...} = armed
def arm_bahtinov(self, tol_px: float = 1.5, invert: bool = False) -> dict: ...
def disarm_bahtinov(self) -> dict: ...
```

```ts
// ui/src/types.ts
export interface BahtinovInfo {
  valid: boolean;
  offset_px: number | null;
  in_focus: boolean;
  side: "left" | "right" | null;
  direction: "in" | "out" | null;
  angles_deg: number[];
  tol_px: number;
  reason: string;
}
// PreviewInfo gains:  bahtinov?: BahtinovInfo;

// ui/src/lib/bahtinov.ts
export type BahtTone = "good" | "warn" | "bad" | "neutral";
export interface BahtVerdict { tone: BahtTone; headline: string; detail: string; }
export function bahtinovAid(info: BahtinovInfo | null | undefined): BahtVerdict;
```

---

### Task 1 — Pure Radon + spike-fit + signed-offset core `bahtinov.py`
**Impl tier: Opus.** Genuinely subtle numeric/geometry: Radon convention,
sub-pixel peak, symmetry labelling, signed offset. The synthetic test is the spec.

**Files:** `server/astrodeck/imaging/bahtinov.py` (new, core fns only),
`server/tests/test_bahtinov_core.py` (new).

**Step 1 — write the failing test first.** `server/tests/test_bahtinov_core.py`:

```python
import numpy as np
from astrodeck.imaging.bahtinov import bahtinov_offset, _intersect

def _make_bahtinov(size=257, phi_c_deg=45.0, alpha_deg=20.0, central_shift=0.0,
                   line_hw=1.3, amp=1200.0, star_sigma=3.0, star_amp=6000.0, bg=100.0):
    """Three bright spikes crossing at the ROI center. The two OUTER spikes pass
    through the center (vertex at origin); the CENTRAL spike is shifted
    perpendicular by `central_shift` px (0 == in focus)."""
    c = (size - 1) / 2.0
    yy, xx = np.mgrid[0:size, 0:size].astype(float)
    x, y = xx - c, yy - c
    img = np.full((size, size), bg, float)
    img += star_amp * np.exp(-(x**2 + y**2) / (2 * star_sigma**2))
    def add_line(ridge_deg, shift):
        phi = np.deg2rad(ridge_deg + 90.0)          # normal angle
        d = x * np.cos(phi) + y * np.sin(phi) - shift
        return amp * np.exp(-(d**2) / (2 * line_hw**2))
    img += add_line(phi_c_deg, central_shift)       # central
    img += add_line(phi_c_deg - alpha_deg, 0.0)     # outer L
    img += add_line(phi_c_deg + alpha_deg, 0.0)     # outer R
    return img

def test_intersect_basic():
    # x-axis line (n=(0,1),rho=0) and y-axis line (n=(1,0),rho=0) cross at origin
    p = _intersect((0.0, 1.0, 0.0), (1.0, 0.0, 0.0))
    assert p is not None and abs(p[0]) < 1e-9 and abs(p[1]) < 1e-9

def test_in_focus_offset_near_zero():
    img = _make_bahtinov(central_shift=0.0)
    c = (img.shape[0] - 1) / 2.0
    off, angles, valid, _ = bahtinov_offset(img, c, c)
    assert valid and off is not None
    assert abs(off) < 0.6, off
    assert len(angles) == 3

def test_defocus_offset_magnitude_and_sign():
    c = (257 - 1) / 2.0
    off_p, _, ok_p, _ = bahtinov_offset(_make_bahtinov(central_shift=+6.0), c, c)
    off_n, _, ok_n, _ = bahtinov_offset(_make_bahtinov(central_shift=-6.0), c, c)
    assert ok_p and ok_n
    assert abs(abs(off_p) - 6.0) < 0.7, off_p          # magnitude recovered
    assert (off_p > 0) != (off_n > 0), (off_p, off_n)  # sign flips with defocus dir

def test_recovers_spike_angles():
    c = (257 - 1) / 2.0
    _, angles, valid, _ = bahtinov_offset(_make_bahtinov(phi_c_deg=45, alpha_deg=20), c, c)
    assert valid
    got = sorted(a % 180 for a in angles)
    # normal-angle triple {μ-α, μ, μ+α} with μ = 45+90 = 135
    for want in (115.0, 135.0, 155.0):
        assert min(abs(g - want) for g in got) < 1.6, (got, want)

def test_blank_frame_is_invalid():
    off, angles, valid, reason = bahtinov_offset(np.full((257, 257), 100.0), 128.0, 128.0)
    assert not valid and off is None and reason
```

Run (must FAIL — module/fn missing):
```
server/.venv/Scripts/pytest.exe server/tests/test_bahtinov_core.py -n0 -q
```
Expected first run: `ModuleNotFoundError: No module named 'astrodeck.imaging.bahtinov'` (or collection error).

**Step 2 — implement the core** in `server/astrodeck/imaging/bahtinov.py`:

```python
"""Bahtinov-mask focus analysis (NOV-12).

A Bahtinov mask makes three diffraction spikes on a bright star: two outer spikes
that cross in an X, and a central spike that bisects them. At best focus the
central spike passes through the X's crossing point; defocus shifts it sideways.
The signed perpendicular distance from the crossing to the central spike is the
focus error, in pixels, and flips sign as you pass through focus.

Pure numpy (no scipy): the spike lines are found with a hand-rolled discrete
Radon transform (one weighted np.bincount per angle over a small ROI)."""
from __future__ import annotations

import numpy as np


def _radon(roi, angles_rad, cx, cy):
    h, w = roi.shape
    yy, xx = np.mgrid[0:h, 0:w]
    x = (xx.ravel() - cx)
    y = (yy.ravel() - cy)
    wts = roi.ravel().astype(np.float64)
    R = int(np.ceil(np.hypot(max(cx, w - cx), max(cy, h - cy)))) + 1
    rhos = np.arange(-R, R + 1)
    out = np.empty((len(angles_rad), len(rhos)), dtype=np.float64)
    for i, phi in enumerate(angles_rad):
        idx = np.rint(x * np.cos(phi) + y * np.sin(phi)).astype(np.intp) + R
        out[i] = np.bincount(idx, weights=wts, minlength=len(rhos))[:len(rhos)]
    return out, rhos


def _subpixel_peak(profile):
    j = int(np.argmax(profile))
    v = float(profile[j])
    if 0 < j < len(profile) - 1:
        a, b, c = float(profile[j - 1]), v, float(profile[j + 1])
        denom = (a - 2 * b + c)
        if denom != 0:
            return j + 0.5 * (a - c) / denom, v
    return float(j), v


def _spike_angles(strength, angles_rad, *, n=3, min_sep_rad=np.deg2rad(5.0)):
    order = np.argsort(strength)[::-1]
    picked: list[int] = []
    for k in order:
        phi = angles_rad[k]
        if all(_circ_sep(phi, angles_rad[p]) >= min_sep_rad for p in picked):
            picked.append(int(k))
            if len(picked) == n:
                break
    return picked


def _circ_sep(a, b):
    d = abs(a - b) % np.pi
    return min(d, np.pi - d)


def _intersect(line_a, line_b):
    nax, nay, ra = line_a
    nbx, nby, rb = line_b
    det = nax * nby - nay * nbx
    if abs(det) < 1e-9:
        return None
    x = (ra * nby - rb * nay) / det
    y = (nax * rb - nbx * ra) / det
    return (float(x), float(y))


def bahtinov_offset(roi, cx, cy, *, n_angles=360, core_mask_px=8.0):
    a = np.asarray(roi, dtype=np.float64)
    a = a - float(np.median(a))
    a = np.clip(a, 0.0, None)
    # zero the saturated core so the star blob doesn't dominate the Radon
    h, w = a.shape
    yy, xx = np.mgrid[0:h, 0:w]
    core = (xx - cx) ** 2 + (yy - cy) ** 2 <= core_mask_px ** 2
    a = a.copy()
    a[core] = 0.0

    angles = np.linspace(0.0, np.pi, n_angles, endpoint=False)
    R, rhos = _radon(a, angles, cx, cy)
    strength = R.max(axis=1) - np.median(R, axis=1)
    idx = _spike_angles(strength, angles, n=3)
    if len(idx) < 3:
        return None, [], False, "need three spikes — point at a bright star through the mask"

    lines = []          # (nx, ny, rho, angle_rad)
    for k in idx:
        jf, _ = _subpixel_peak(R[k])
        rho = float(jf) - (len(rhos) - 1) / 2.0
        phi = float(angles[k])
        lines.append((np.cos(phi), np.sin(phi), rho, phi))
    lines.sort(key=lambda L: L[3])

    # central = circular-median angle (middle after sorting the 3, no wrap for a
    # real mask); outer = the flanking pair
    central = lines[1]
    outer = (lines[0], lines[2])
    # symmetry gate: the flankers must sit ~equidistant in angle from the central
    sep_l = _circ_sep(central[3], outer[0][3])
    sep_r = _circ_sep(central[3], outer[1][3])
    if abs(sep_l - sep_r) > np.deg2rad(8.0) or min(sep_l, sep_r) < np.deg2rad(4.0):
        return None, [round(np.rad2deg(L[3]), 2) for L in lines], False, \
            "spikes not symmetric — reseat the mask and recenter the star"

    v = _intersect(outer[0][:3], outer[1][:3])
    if v is None:
        return None, [round(np.rad2deg(L[3]), 2) for L in lines], False, \
            "outer spikes parallel — recenter the star"
    ncx, ncy, rc, _ = central
    offset = ncx * v[0] + ncy * v[1] - rc      # signed distance of vertex from central line
    return float(offset), [round(np.rad2deg(L[3]), 2) for L in lines], True, "ok"
```

Run (must PASS):
```
server/.venv/Scripts/pytest.exe server/tests/test_bahtinov_core.py -n0 -q
```
Expected: `6 passed`.

> Opus note on the sign test: `test_defocus_offset_magnitude_and_sign` only pins
> magnitude + that the sign *flips* between ±shift — it does NOT assert a physical
> IN/OUT meaning (that's rig calibration, §4.2). Keep it that way; do not add an
> assertion tying sign to "in".

---

### Task 2 — `analyze_bahtinov` wrapper + `BahtinovResult` + verdict + exports
**Impl tier: Sonnet** (mechanical once Task 1's core + thresholds are frozen; the
verdict is a threshold/copy table, the wrapper is crop + call + package).

**Files:** `server/astrodeck/imaging/bahtinov.py` (append), `server/astrodeck/imaging/__init__.py` (edit exports), `server/tests/test_bahtinov_analyze.py` (new).

**Step 1 — failing test** `server/tests/test_bahtinov_analyze.py`:

```python
import numpy as np
from astrodeck.imaging.bahtinov import analyze_bahtinov
from astrodeck.tests_util_bahtinov import make_bahtinov  # or inline the generator

def test_locked_when_in_focus():
    img = make_bahtinov(central_shift=0.0)
    r = analyze_bahtinov(img, center=((img.shape[0]-1)/2,)*2, tol_px=1.5)
    assert r.valid and r.in_focus and r.offset_px is not None
    d = r.to_dict()
    assert d["valid"] is True and d["in_focus"] is True and "tol_px" in d

def test_off_when_defocused_has_side():
    c = ((257-1)/2,)*2
    r = analyze_bahtinov(make_bahtinov(central_shift=5.0), center=c, tol_px=1.5)
    assert r.valid and not r.in_focus
    assert r.side in ("left", "right") and abs(r.offset_px) > 1.5

def test_invert_flips_direction_not_side():
    c = ((257-1)/2,)*2
    base = analyze_bahtinov(make_bahtinov(central_shift=5.0), center=c, invert=False)
    inv  = analyze_bahtinov(make_bahtinov(central_shift=5.0), center=c, invert=True)
    assert base.side == inv.side              # geometric side is invariant
    assert base.direction != inv.direction    # IN/OUT label flips

def test_blank_frame_invalid_never_raises():
    r = analyze_bahtinov(np.full((257,257), 100.0, np.float64))
    assert not r.valid and r.offset_px is None and r.reason
    assert r.to_dict()["valid"] is False
```

(Put `make_bahtinov` in a shared `server/tests/_bahtinov_synth.py` importable by
both test modules, or duplicate the generator from Task 1 — reviewer's call.)

Run (FAIL — `analyze_bahtinov` not implemented):
```
server/.venv/Scripts/pytest.exe server/tests/test_bahtinov_analyze.py -n0 -q
```

**Step 2 — implement** (append to `bahtinov.py`):

```python
from dataclasses import dataclass, field
from .stars import detect_stars
from .livestack import brightest_centroid


@dataclass
class BahtinovResult:
    valid: bool
    offset_px: float | None
    in_focus: bool
    side: str | None
    direction: str | None
    angles_deg: list[float]
    center: tuple[float, float] | None
    tol_px: float
    reason: str

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "offset_px": (round(self.offset_px, 2) if self.offset_px is not None else None),
            "in_focus": self.in_focus,
            "side": self.side,
            "direction": self.direction,
            "angles_deg": [round(a, 1) for a in self.angles_deg],
            "tol_px": self.tol_px,
            "reason": self.reason,
        }


def analyze_bahtinov(data, center=None, *, stars=None, half=128,
                     tol_px=1.5, invert=False) -> BahtinovResult:
    def _invalid(reason, angles=()):
        return BahtinovResult(False, None, False, None, None, list(angles),
                              None, tol_px, reason)
    h, w = data.shape
    if center is None:
        if stars is None:
            stars = detect_stars(data)
        center = brightest_centroid(stars)
        if center is None:
            return _invalid("no bright star found — center a star through the mask")
    cx, cy = float(center[0]), float(center[1])
    x0 = int(round(cx)) - half
    y0 = int(round(cy)) - half
    x0 = max(0, min(x0, w - 1)); y0 = max(0, min(y0, h - 1))
    x1 = min(w, x0 + 2 * half); y1 = min(h, y0 + 2 * half)
    roi = data[y0:y1, x0:x1]
    if roi.shape[0] < 32 or roi.shape[1] < 32:
        return _invalid("star too close to the edge — recenter it")
    off, angles, valid, reason = bahtinov_offset(roi, cx - x0, cy - y0)
    if not valid:
        return _invalid(reason, angles)
    in_focus = abs(off) <= tol_px
    side = None if in_focus else ("left" if off < 0 else "right")
    direction = None
    if side is not None:
        pair = ("out", "in") if not invert else ("in", "out")
        direction = pair[0] if side == "left" else pair[1]
    reason = ("locked — you're focused" if in_focus
              else f"middle spike {abs(off):.1f} px {side} of the crossing")
    return BahtinovResult(valid, off, in_focus, side, direction, angles,
                          (cx, cy), tol_px, reason)
```

Add to `server/astrodeck/imaging/__init__.py` (mirror the `cloud_score` export at
`__init__.py:17,29`): import `analyze_bahtinov, BahtinovResult` from `.bahtinov`
and add both to `__all__`.

Run (PASS):
```
server/.venv/Scripts/pytest.exe server/tests/test_bahtinov_analyze.py -n0 -q
```
Expected: `4 passed`.

---

### Task 3 — Hub arming + gated call in `_publish_preview` + status flag
**Impl tier: Sonnet** (a line-for-line mirror of the `live_stacker` wiring).

**Files:** `server/astrodeck/hub.py`, `server/tests/test_bahtinov_hub.py` (new).

**Step 1 — failing test** `server/tests/test_bahtinov_hub.py` (unit-level, no camera):

```python
import numpy as np
from astrodeck.hub import Hub   # match the existing hub construction in other hub tests

def _synthetic_sub():
    from tests._bahtinov_synth import make_bahtinov   # shared generator
    return make_bahtinov(central_shift=0.0).astype(np.uint16)

def test_arm_disarm_toggles_state():
    hub = Hub.__new__(Hub)          # or the test factory other hub tests use
    hub.bahtinov = None
    assert hub.arm_bahtinov(tol_px=2.0)["active"] is True
    assert hub.bahtinov == {"tol_px": 2.0, "invert": False}
    assert hub.disarm_bahtinov()["active"] is False
    assert hub.bahtinov is None
```

> If `Hub` can't be cheaply constructed, test `arm_bahtinov`/`disarm_bahtinov` on a
> bare instance as above (they only touch `self.bahtinov`), and cover the
> `_publish_preview` attach in Task 4's route test (which exercises a real sub).
> Match whatever construction the neighboring `server/tests/test_*hub*.py` use.

Run (FAIL — attrs/methods missing):
```
server/.venv/Scripts/pytest.exe server/tests/test_bahtinov_hub.py -n0 -q
```

**Step 2 — implement** in `hub.py`:

- Init beside `self.live_stacker = None` (`hub.py:183`):
  ```python
  self.bahtinov: dict | None = None    # NOV-12: None=off; {"tol_px","invert"}=armed
  ```
  and release it in teardown beside `hub.py:713`.
- Methods beside the live-stack methods (`hub.py:1793-1808`):
  ```python
  def arm_bahtinov(self, tol_px: float = 1.5, invert: bool = False) -> dict:
      self.bahtinov = {"tol_px": float(tol_px), "invert": bool(invert)}
      bus.log("info", "Bahtinov focus aid on", "focus")
      return {"active": True}

  def disarm_bahtinov(self) -> dict:
      self.bahtinov = None
      return {"active": False}
  ```
- Gated attach in `_publish_preview`, right after the cloud block (`hub.py:1574`),
  in the raw/linear branch so `sub` + `stars` are in scope:
  ```python
  if self.bahtinov is not None and data_is_linear:
      from .imaging import analyze_bahtinov
      res = await asyncio.to_thread(
          lambda: analyze_bahtinov(sub, None, stars=stars,
                                   tol_px=self.bahtinov["tol_px"],
                                   invert=self.bahtinov["invert"]))
      info["bahtinov"] = res.to_dict()
  ```
  Passing `stars=stars` lets `analyze_bahtinov` derive the brightest-star center
  itself (reusing the already-computed list — no second `detect_stars`, and no
  new hub-level import; `hub.py:38` imports from `.imaging` does NOT currently
  include `brightest_centroid`, so keeping the call inside `analyze_bahtinov`
  avoids touching that import block).
- Status flag beside `hub.py:2305`:
  ```python
  out["bahtinov_active"] = self.bahtinov is not None
  ```

Run (PASS):
```
server/.venv/Scripts/pytest.exe server/tests/test_bahtinov_hub.py -n0 -q
```
Expected: `1 passed`. Then a fast regression on the imaging + hub area:
```
server/.venv/Scripts/pytest.exe server/tests/test_bahtinov_core.py server/tests/test_bahtinov_analyze.py server/tests/test_bahtinov_hub.py -n0 -q
```

---

### Task 4 — API endpoints `POST /api/focuser/bahtinov/{start,stop}`
**Impl tier: Sonnet** (a mirror of the livestack routes + a pydantic body).

**Files:** `server/astrodeck/api/app.py`, `server/tests/test_bahtinov_route.py` (new; model it on `server/tests/test_autofocus_route.py`).

**Step 1 — failing test** `server/tests/test_bahtinov_route.py` (mirror the
existing route-test harness — same client fixture / connect-sim setup as
`test_autofocus_route.py`):

```python
def test_start_arms_and_stop_disarms(client_connected_sim):   # reuse the existing fixture
    c = client_connected_sim
    r = c.post("/api/focuser/bahtinov/start",
               json={"exposure_s": 1.0, "gain": 100, "binning": 1, "tol_px": 1.5})
    assert r.status_code == 200 and r.json()["active"] is True
    assert c.get("/api/status").json()["bahtinov_active"] is True
    r = c.post("/api/focuser/bahtinov/stop")
    assert r.status_code == 200 and r.json()["active"] is False
    assert c.get("/api/status").json()["bahtinov_active"] is False

def test_start_conflicts_with_running_sequence(client_sequence_running):
    r = client_sequence_running.post("/api/focuser/bahtinov/start", json={})
    assert r.status_code == 409
```

Run (FAIL — 404 route):
```
server/.venv/Scripts/pytest.exe server/tests/test_bahtinov_route.py -n0 -q
```

**Step 2 — implement** in `app.py`:

- Body model beside `LiveStackBody` (`app.py:447`):
  ```python
  class BahtinovBody(CaptureBody):
      tol_px: float = 1.5
      invert: bool = False
  ```
- Routes beside the focuser routes (`app.py:2811-2863`), mirroring
  `livestack_start`/`livestack_stop` (`app.py:2436-2461`):
  ```python
  @app.post("/api/focuser/bahtinov/start", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
  @declare(CAP_CONTROL_CAPTURE)
  async def bahtinov_start(body: BahtinovBody):
      if engine.running:
          raise HTTPException(409, "a sequence is running")
      try:
          hub.require("camera")
      except DeviceError as e:
          raise _err(e)
      hub.arm_bahtinov(tol_px=body.tol_px, invert=body.invert)
      if not hub.looping:
          await hub.start_loop(body.exposure_s, body.gain, body.offset,
                               body.binning, frame_type="Light")
      return {"active": True}

  @app.post("/api/focuser/bahtinov/stop", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
  @declare(CAP_CONTROL_CAPTURE)
  async def bahtinov_stop():
      return hub.disarm_bahtinov()   # leaves the live loop as the user left it (§4.3)
  ```

Run (PASS):
```
server/.venv/Scripts/pytest.exe server/tests/test_bahtinov_route.py -n0 -q
```
Expected: `2 passed`.

---

### Task 5 — Client types + pure verdict lib + tsx test
**Impl tier: Sonnet** (pure mapping + additive type; tsx-testable).

**Files:** `ui/src/types.ts` (add `BahtinovInfo` + `PreviewInfo.bahtinov?`),
`ui/src/lib/bahtinov.ts` (new), `ui/src/lib/__tests__/bahtinov.test.ts` (new).

**Step 1 — failing test** `ui/src/lib/__tests__/bahtinov.test.ts` (copy the harness
header from `eta.test.ts:21-43`):

```ts
import { bahtinovAid } from "../bahtinov";
// ...paste the test/eq/assert harness from eta.test.ts...

test("locked → good, PERFECT headline", () => {
  const v = bahtinovAid({ valid: true, offset_px: 0.3, in_focus: true, side: null,
    direction: null, angles_deg: [115,135,155], tol_px: 1.5, reason: "locked" });
  eq(v.tone, "good"); assert(/PERFECT|locked/i.test(v.headline), "headline");
});
test("off + direction in → warn, mentions turn IN", () => {
  const v = bahtinovAid({ valid: true, offset_px: 3.2, in_focus: false, side: "right",
    direction: "in", angles_deg: [], tol_px: 1.5, reason: "middle spike 3.2 px right" });
  eq(v.tone, "warn"); assert(/in\b/i.test(v.detail), "detail says IN");
});
test("invalid → neutral guidance, no number", () => {
  const v = bahtinovAid({ valid: false, offset_px: null, in_focus: false, side: null,
    direction: null, angles_deg: [], tol_px: 1.5, reason: "no bright star found" });
  eq(v.tone, "neutral"); assert(v.detail.length > 0, "has guidance");
});
test("null info → neutral awaiting", () => { eq(bahtinovAid(null).tone, "neutral"); });
// ...report block from eta.test.ts:187-196...
```

Run (FAIL — `../bahtinov` missing):
```
cd ui && npx tsx src/lib/__tests__/bahtinov.test.ts
```

**Step 2 — implement** `ui/src/lib/bahtinov.ts` (mirror `plainFocusVerdict`,
`autofocus.ts:271`):

```ts
// lib/bahtinov.ts — pure verdict mapping for the NOV-12 Bahtinov aid.
// No React/DOM: npx-tsx testable (eta.test.ts precedent).
import type { BahtinovInfo } from "../types";

export type BahtTone = "good" | "warn" | "bad" | "neutral";
export interface BahtVerdict { tone: BahtTone; headline: string; detail: string; }

export function bahtinovAid(info: BahtinovInfo | null | undefined): BahtVerdict {
  if (!info) return { tone: "neutral", headline: "Bahtinov focus", detail: "Waiting for a frame…" };
  if (!info.valid)
    return { tone: "neutral", headline: "Line up the star", detail: info.reason };
  if (info.in_focus)
    return { tone: "good", headline: "PERFECT — locked", detail: "The middle spike is centered — you're focused." };
  const off = info.offset_px != null ? `${Math.abs(info.offset_px).toFixed(1)} px` : "";
  const turn = info.direction ? `turn ${info.direction.toUpperCase()} a little` : "nudge the focuser";
  const tone: BahtTone = info.offset_px != null && Math.abs(info.offset_px) > info.tol_px * 4 ? "bad" : "warn";
  return { tone, headline: `Not yet — ${turn}`, detail: `Middle spike ${off} ${info.side ?? ""} of the crossing.` };
}
```

Add to `ui/src/types.ts`: the `BahtinovInfo` interface (§Interfaces) and
`bahtinov?: BahtinovInfo;` on `PreviewInfo` (beside `livestack?` at `types.ts:239`).

Run (PASS) then the gate:
```
cd ui && npx tsx src/lib/__tests__/bahtinov.test.ts
cd ui && npx tsc -b
```
Expected: `bahtinov.test: 4/4 passed`, and `tsc -b` clean (no output).

---

### Task 6 — Client render: `BahtinovAid.tsx` + FocusView toggle
**Impl tier: Sonnet** (thin render verified by the typechecker; logic already tested in Task 5).

**Files:** `ui/src/components/preview/BahtinovAid.tsx` (new), `ui/src/views/FocusView.tsx` (add a Panel + arm/disarm toggle).

**Step 1 — component** `BahtinovAid.tsx` (mirror `FocusVerdict.tsx:73-144` structure — word + tone + `role="status" aria-live="polite"`, never color alone):

```tsx
import type { PreviewInfo } from "../../types";
import { bahtinovAid, type BahtTone } from "../../lib/bahtinov";

const TONE: Record<BahtTone, string> = {
  good: "text-good", warn: "text-warn", bad: "text-bad", neutral: "text-dim",
};

export function BahtinovAid({ preview }: { preview: PreviewInfo | null }) {
  const v = bahtinovAid(preview?.bahtinov);
  return (
    <div role="status" aria-live="polite">
      <div className={`text-base font-semibold ${TONE[v.tone]}`}>{v.headline}</div>
      <div className="text-xs text-dim">{v.detail}</div>
    </div>
  );
}
```

**Step 2 — wire into `FocusView.tsx`.** Add a `Panel title="Bahtinov Focus"` (near
the Live Preview panel, `FocusView.tsx:123`) containing `<BahtinovAid preview={shown} />`
and a toggle button. The toggle reads armed state from
`useStatus()` (`status?.bahtinov_active`, wired in Task 3) and posts start/stop:

```tsx
const bahtOn = status?.bahtinov_active ?? false;
// honest-disabled §11.8 when no operator control (mirror focusButtonState usage):
const disabled = !canFocus;
<button
  className={`btn w-full tap min-h-11 ${disabled ? "opacity-40" : ""} ${bahtOn ? "btn-accent" : ""}`}
  aria-disabled={disabled || undefined}
  aria-pressed={bahtOn}
  title={disabled ? "Read-only — focusing needs operator access" : undefined}
  onClick={disabled ? undefined : () => act(() =>
    api.post(bahtOn ? "/api/focuser/bahtinov/stop" : "/api/focuser/bahtinov/start",
             bahtOn ? {} : { exposure_s: 1, gain: 100, binning: 1 }))}
>{bahtOn ? "Stop Bahtinov aid" : "Bahtinov focus"}</button>
```

(Reuse the existing `act(...)` helper at `FocusView.tsx:99` and `canFocus` at
`FocusView.tsx:43`. Add `bahtinov_active?: boolean` to the `Status` type in
`types.ts` where `live_stack_active` lives so `status?.bahtinov_active`
typechecks.)

**Verify — typecheck only (thin render):**
```
cd ui && npx tsc -b
```
Expected: clean. Then a quick full-suite backend regression before calling done:
```
server/.venv/Scripts/pytest.exe -n0 -q -k "bahtinov or preview or focus"
```

---

## 4. Open decisions (each with a recommendation)

### 4.1 Additive preview field vs a pull endpoint over the current frame
**Recommendation: additive field on the `preview` event, gated by an armed flag
(as designed).** The `preview` event already carries per-frame analysis (`hfr`,
`cloud`, `ecc`), and the raw linear array is intentionally NOT retained
(`hub.py:1582-1587`), so a `GET /bahtinov` over "the last frame" would have no
pixels without re-enabling ~125 MB retention or firing a conflicting exposure
(`app.py:2827`). The armed field reuses the one detection pass and streams a
verdict every frame — which is exactly the live "watch the number drop to zero"
loop a Bahtinov user wants. The `start`/`stop` routes are the only new endpoints.

### 4.2 Physical IN/OUT direction — fabricate it or expose a calibration flag?
**Recommendation: expose a per-rig `invert` flag; default the copy to the
unambiguous geometric side, add the IN/OUT word on top of it.** Which sign of the
offset means "focuser too far in" depends on the optical train and mask
orientation (mirror flips, mask rotation) — it is genuinely rig-specific and we
must not invent it. The pure core returns a signed offset; the verdict always
states the geometric side ("middle spike sits LEFT of the crossing"), which the
follow-on overlay makes self-evident, and layers a `direction` (`in`/`out`) that
the user flips once via `invert` if it's backwards for their scope. Honest and
still actionable. (v1 ships `invert` in the body/result; surfacing a one-tap
"that was backwards" learn button is a follow-on.)

### 4.3 Should `stop` also stop the live loop it may have started?
**Recommendation: `start` starts the loop only if one isn't already running;
`stop` disarms only and leaves the loop as-is.** Least surprising: a user who was
already live-previewing keeps previewing after they close the aid; a user who
started fresh can stop the loop with the existing capture-stop control. (Tracking
loop-ownership to auto-stop is a nicety, not v1. `livestack_stop` does stop its
loop — noted, but Bahtinov's flow differs because focusing overlaps ordinary
live preview.)

### 4.4 ROI half-size and mask half-angle — fixed or adaptive?
**Recommendation: fixed defaults in v1 (`half=128`, no mask-angle prior; detect
all three angles from the Radon peaks).** The symmetry gate already rejects
non-Bahtinov junk. Auto-sizing the ROI from the star's brightness/HFR and
fitting/validating against a known mask half-angle (to reject a two-spike
misread) are follow-ons. `half`/`tol_px` are parameters, so a later Advanced knob
is a wire-through, not a refactor.

### 4.5 Where does the panel live — Focus view only, or Capture too?
**Recommendation: Focus view only for v1.** It sits with the focuser controls and
the one-tap focus hero, which is where a novice goes to focus. The armed field
rides on every `preview` event regardless, so a Capture-surface panel is a pure
add-on later with zero backend change.

---

## 5. Deferred follow-ons (noted, out of v1 scope)
- **Live overlay:** draw the three fitted spike lines + the crossing vertex + the
  central-offset marker on the preview canvas (the brief calls this a "thin
  follow-on"). The result already carries `angles_deg` and the center; add `rho`
  per line + the vertex to `to_dict()` when this lands.
- **IN/OUT auto-learn:** a one-tap "that was backwards" that persists `invert` per
  profile.
- **Mask half-angle validation** to harden against a 2-spike or rotated-mask
  misread.
- **Capture-surface panel** (§4.5).
- **Loop-ownership auto-stop** on disarm (§4.3).
```
