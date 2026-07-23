# Live-stacking / EAA "Live View" — a continuously brightening, SNR-growing preview

**Feature:** NOV-1 (novice; the retention centerpiece)
**Date:** 2026-07-23
**Branch:** feat/wave0-remainder

---

## 1. Design

### 1.1 Goal

A beginner's first 2-minute sub of a faint nebula looks like black noise. Without an accumulating image they conclude the rig is broken and quit. **Live View** turns the live loop into an EAA experience: each incoming sub is server-side aligned onto a running accumulator and the **mean of the stack** is what gets stretched and shown, so the image continuously brightens and de-noises (√N SNR growth) while a readout says **"12 frames · 24 min integrated"**.

Scope, per the approved open decision: ship a **fixed-frame running-mean** with a lightweight **brightest-star drift-reject** first (skip a sub whose alignment offset is too large). Full star-match registration is an explicitly-deferred bounded second pass — **do NOT over-build registration in v1**.

The displayed image reuses the *entire* existing preview pipeline for free (auto-stretch, stats, histogram, star overlay, clip mask, PNG/thumb downloads) — Live View only swaps the pixels the pipeline runs on from "this sub" to "the accumulated mean," and rides one additive `livestack` field on the existing `preview` event.

### 1.2 Current-state seams (every one read at file:line)

**Processing is single-frame only — there is no stacking anywhere.**
- `server/astrodeck/imaging/processing.py:33` `auto_stretch(data)`, `:51` `auto_levels(data)`, `:88` `stretch_with(data, black, mid, white)`, `:103` `compute_histogram(data)` (linear), `:112` `display_histogram(stretched01)`, `:135` `to_png(data)`, `:142` `to_jpeg(data, ...)`, `:156` `to_thumb(...)`, `:174` `frame_stats(data)`. Every function takes ONE frame's `np.ndarray`; nothing accumulates across frames. (Module docstring `:1-19` confirms the two single-frame domains, auto vs explicit-levels.)

**Star detection is reusable as the alignment anchor.**
- `server/astrodeck/imaging/stars.py:54` `detect_stars(data, k_sigma=5.0, max_stars=200, box=15) -> list[Star]`. `Star` at `:19-27` carries `x, y, flux, hfr, peak, ecc, theta` — `x`/`y` are flux-weighted centroids in `frame.data` pixel space (`stars.py:97-98,113-115`). Candidates are appended in descending *peak-pixel* order (`:73`), NOT flux order, so the brightest-by-flux star must be selected explicitly (`_median_hfr_from` re-sorts `by_flux` at `:130` — the same care applies here).
- `server/astrodeck/imaging/stars.py:194` `measure_frame` / `:184` `measure_stars(stars, ...)` already derive `(median_hfr, star_count, star_marks)` from an already-detected list, so a caller can `detect_stars` **once** and feed multiple consumers (`:187-191`). Live View exploits this: one detect pass on the incoming sub drives the alignment anchor, the drift decision, AND the per-sub HFR/overlay.

**The single capture → preview publish path (where each sub lands, and where the accumulator lives/resets).**
- `server/astrodeck/hub.py:1382` `capture(...)` — `:1393` `frame = await cam.expose(...)`, `:1396` `self.last_frame = frame`, `:1432` `info = await self._publish_preview(frame)`. Every path (Single, Loop, sequence) funnels through here.
- `server/astrodeck/hub.py:1458` `_publish_preview(frame)` is the SINGLE publish point. Two branches:
  - NINA (`:1498-1516`): pre-rendered JPEG verbatim, `data_is_linear=False`. **Live View must skip this branch** (no linear pixels to stack).
  - raw/linear sim/Alpaca (`:1517-1563`): `:1518` `auto_levels(data)`, `:1519` `to_jpeg`, `:1521` `to_png` (lossless), `:1522` `to_thumb`, `:1524` `stretch_with`, `:1525` `display_histogram`, `:1528` `stars = detect_stars(data)`, `:1529` `measure_stars(...)`, `:1550-1553` `cloud_score(data, stars=stars)` (linear-only), `:1530-1539` `info.update(...)`. Then `:1571-1574` store the ring entry + `bus.publish("preview", **info)`.
  - `frame_stats(data)` is computed at the top for BOTH branches at `:1481`.
- `server/astrodeck/hub.py:168` `Hub.__init__` — `:177` `self.preview_seq = 0`, `:178` `self.previews`, `:180` `self.last_frame`. **This is where a `self.live_stacker` accumulator handle lives.**
- `server/astrodeck/hub.py:1720` `start_loop(exposure_s, gain, offset, binning, frame_type="Light")` — `:1734-1745` `_loop()` calls `self.capture(...)` in a while-loop; `:1748` `stop_loop()`; `:1765` `looping` property.
- `server/astrodeck/hub.py:2261` `poll_status()` — `:2263` `"looping": self.looping` (the 2s wholesale-replace status dict; anything the toggle needs on reload must live here, `:2264-2265`).
- `server/astrodeck/events.py:39` `bus.publish(type, **data)` → `Event(type, data)` fan-out. The client `preview` handler is `ui/src/store.ts:1255-1261` (`set({preview:p, ...}); pushPreview(p)`).
- `server/astrodeck/imaging/__init__.py:1-26` — the imaging package's public exports (where a `LiveStacker`/`brightest_centroid` export is added), and `server/astrodeck/hub.py:38-52` the hub's `from .imaging import (...)` block.

**The API capture routes to mirror.**
- `server/astrodeck/api/app.py:2375` `POST /api/capture`, `:2394` `POST /api/capture/loop` (guards polar `:2397` + `engine.running` `:2399` + `hub.require("camera")` `:2401`, then `await hub.start_loop(...)` `:2408`), `:2412` `POST /api/capture/stop` (`hub.stop_loop()` + abort in-flight `:2416-2424`). `CaptureBody` at `:435-442` (`exposure_s, gain, offset, binning, save, target, frame_type`).

**Client preview type + render + toolbar + view.**
- `ui/src/types.ts:191-219` `PreviewInfo` — `stats` `:193`, `histogram` `:194`, `exposure_s`/`gain`/`binning` `:197-199`, `data_width/height` `:200-201`, `data_is_linear` `:206`, `full_well` `:209`, `auto_levels` `:212`, `hfr`/`stars`/`star_list` `:215-217`, `ts` `:218`. Add an optional `livestack` here.
- `ui/src/types.ts:41-107` `RigStatus` — `looping` `:43`. Add an optional `live_stack_active` here.
- `ui/src/store.ts:1255` `case "preview"`; `:1427` `usePreview`, `:1499` `usePreviews`, `:1512` `useLivePreviewId`; `:1358` `useStatus`. The store already carries the whole `PreviewInfo` per frame — the readout reads `usePreview().livestack` with **no store-shape change**.
- `ui/src/components/preview/LivePreview.tsx:33` orchestrator; `:81` `<Panel title="Live Preview" right={<PreviewMeta .../>}>` — the header slot for the readout; the stacked image is served by the existing `<img src={`/api/preview/${id}`}>` path (`PreviewStage.tsx:179,306`) with zero new image route.
- `ui/src/components/preview/PreviewToolbar.tsx:16-49` — the `Toggle` honest-disabled idiom (dim `!text-dim` + `<Icon name="lock">` + `aria-disabled`, click swallowed, never native `disabled`).
- `ui/src/views/CaptureView.tsx:274` `onSingle`, `:279` `onLoop`, `:287` `onStop`, `:512-538` the capture-button cluster, `:111` `const looping = !!status?.looping`, `:116` `seqOwnsCamera`, `:117` `captureBlocked`, `:329` `<LivePreview/>`. Honest-disabled precedent already in-file at `:440-452,498-506`.
- `ui/src/lib/__tests__/eta.test.ts:1-43` — the no-jsdom inline-assert harness run via `npx tsx`; `server/tests/test_clouds.py:12-47` — the synthetic-Gaussian-star-field pytest helper this plan reuses.

### 1.3 Approach

**Placement of the core: server-side pure Python.** Stacking needs the linear `uint16` pixels, which only exist server-side (the client only ever receives an 8-bit stretched JPEG). So the tested heart is a new pure module `server/astrodeck/imaging/livestack.py` — a `LiveStacker` accumulator + two free functions — with NO device I/O, tested via pytest with synthetic shifted frames. The hub owns exactly one `LiveStacker` instance while Live View is armed; the client is a thin readout + a control.

**Algorithm (fixed-frame running-mean + brightest-star drift-reject).**
1. **Anchor** — on the first accepted sub, record the *brightest-by-flux* detected star's centroid as the reference `(rx, ry)` and seed the accumulator with that sub.
2. **Offset** — for each later sub, `offset = (cx-rx, cy-ry)` from its own brightest-star centroid. This is the whole "registration" in v1: an integer translation. No rotation, no scale, no per-star matching (that is the deferred second pass).
3. **Drift-reject** — if `hypot(offset) > reject_frac * min(H, W)` the sub is skipped (a satellite streak, a big bump, a slew): `rejected += 1`, the stack is unchanged, the last mean keeps showing.
4. **Accumulate** — otherwise shift the sub by the rounded integer offset into a `float32` running-sum, with a per-pixel `uint16` **coverage** counter so the overlap-only edges don't progressively darken; `frames += 1`, `integrated_s += exposure_s`. `mean = round(sum / max(coverage,1))` clipped to `uint16`.
5. **Auto re-anchor** — after `reanchor_after` (default 3) *consecutive* drift-rejects, treat the current sub as a deliberate re-frame: reset the accumulator and re-seed from it. This makes "slew to a new target and keep Live View on" self-heal without a manual reset, and stops an endless reject spiral.
6. **Size self-heal** — a sub whose shape differs from the accumulator (the user changed binning) auto-reseeds, never crashes.

Running-MEAN (not sum) is the displayed image on purpose: the mean stays inside the original 16-bit range, so `auto_stretch` behaves identically frame-to-frame and the *only* thing that changes is that the MAD noise estimate drops as √N — which is exactly what lets `auto_stretch` push the faint nebula up out of the noise floor. `integrated_s` (Σ accepted exposures) is tracked separately as the honest "minutes of signal" number. This is standard EAA behavior and it is honest: we never fabricate signal, we reveal what averaging recovers.

**Hub wiring — one surgical hook in the raw/linear branch of `_publish_preview` (`hub.py:1517`).** Reorder so `stars = detect_stars(sub)` runs first (it is already the branch's only detect), feed the armed stacker `stack.add(sub, frame.exposure_s, stars=stars)`, and if a mean exists swap the pixels the *image* pipeline runs on from `sub` to `mean` (auto_levels / to_jpeg / to_png / to_thumb / stretch_with / display_histogram / `frame_stats`), while leaving **`detect_stars`, `measure_stars`, and `cloud_score` on the raw sub** (the anchor + per-sub HFR + the contrast-based cloud metric all need the single unstretched sub, not the mean). Attach `info["livestack"] = {frames, integrated_s, rejected, accepted}`. The NINA branch and every non-armed capture are byte-for-byte unchanged (the hook is a no-op when `self.live_stacker is None`).

**Server truth for the toggle:** `poll_status()` gains `live_stack_active = self.live_stacker is not None`, so the Live View button reflects reality after a page reload (rides the existing 2s push, no new endpoint).

**API:** three tiny routes mirroring `capture_loop` — `POST /api/capture/livestack/start` (arm the stacker + start the loop), `.../reset` (clear the accumulator, keep arming), `.../stop` (disarm + stop the loop). `LiveStackBody(CaptureBody)` adds `reject_frac`.

**Client:** additive `PreviewInfo.livestack` + `RigStatus.live_stack_active` types; a pure `ui/src/lib/liveStack.ts` formatter (tsx-tested); a `LiveStackReadout` shown in the Live Preview header; a **Live View** toggle + **Reset** in Capture, honest-disabled when the camera is unavailable/blocked. The readout reads `usePreview().livestack`; the toggle reads `useStatus().live_stack_active`. **No store-shape change.**

### 1.4 Data shapes & copy

Server `livestack` sub-dict on the `preview` event / `PreviewInfo`:
```
livestack: { frames: number; integrated_s: number; rejected: number; accepted: boolean }
```
Client readout copy (via `formatLiveStack`): headline **"12 frames · 24 min integrated"**; when subs were skipped, a dim suffix **"· 3 skipped"**; a single frame reads **"1 frame · 2 min integrated"**; under a minute reads seconds (**"48 s integrated"**); ≥ 1 h reads **"1.5 h integrated"**. The Live View button label toggles **"Live View" ⇄ "Live View · on"**; Reset is **"Reset stack"**.

---

## 2. Global Constraints (verbatim)

- **Privacy.** The real observing site — latitude **[SITE-LAT]**, longitude **[SITE-LON]**, and the label **"[SITE-LABEL]"** — must NEVER appear in code, tests, or docs. The site default in any fixture/example is **"My Observatory"** at **0.0 / 0.0**. Live View touches no site data, but synthetic test frames and any example must honor this.
- **Never `git add -A`.** Stage only the files this plan names, explicitly, by path.
- **UI typecheck gate:** `cd ui && npx tsc -b` must pass — it is the render-layer's correctness gate (there is no jsdom).
- **NO jsdom.** Pure logic is tested via `npx tsx` inline-assert modules (the `ui/src/lib/__tests__/eta.test.ts` idiom); thin render is verified only by the typechecker.
- **Backend tests:** `server/.venv/Scripts/pytest.exe` run from the **repo root**, single-process **`-n0`** (never xdist for these targeted runs).
- **Client toasts** go through `useStore.getState().enqueueToast` (surfaced in views as `showToast` — `CaptureView.tsx:59`); never `alert`/`console` for user-facing errors.
- **Honest-disabled (§11.8):** a control that can't act is dimmed + lock glyph + `aria-disabled` + a `title` reason — NEVER the native `disabled` attribute (whose `.btn:disabled` opacity the design system rejects). Follow `PreviewToolbar.tsx:34-48`.
- **Do not disrupt astrotown** (the deployed box): no config/site/deploy changes; this is a code-only feature on the branch.

---

## 3. TDD Plan

Interfaces are frozen here; tasks compile/behave against them. Every task lists exact Files, exact commands, and the expected output.

### Interfaces (exact signatures)

**`server/astrodeck/imaging/livestack.py`**
```python
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from .stars import Star

DEFAULT_REJECT_FRAC = 0.08      # drift-reject beyond 8% of the frame's short edge
DEFAULT_REANCHOR_AFTER = 3      # consecutive drift-rejects -> re-anchor to current sub

def brightest_centroid(stars: list[Star]) -> tuple[float, float] | None: ...
def align_offset(ref: tuple[float, float], cur: tuple[float, float]) -> tuple[float, float]: ...

@dataclass
class StackOutcome:
    accepted: bool
    frames: int
    integrated_s: float
    rejected: int
    dx: float
    dy: float
    reason: str          # "" accepted | "no_stars" | "drift" | "reseed" | "size"

class LiveStacker:
    def __init__(self, reject_frac: float = DEFAULT_REJECT_FRAC,
                 reanchor_after: int = DEFAULT_REANCHOR_AFTER) -> None: ...
    @property
    def frames(self) -> int: ...
    @property
    def integrated_s(self) -> float: ...
    @property
    def rejected(self) -> int: ...
    def reset(self) -> None: ...
    def mean(self) -> np.ndarray | None: ...            # uint16 running mean; None until first accept
    def add(self, data: np.ndarray, exposure_s: float, *,
            stars: list[Star] | None = None) -> StackOutcome: ...
```

**`server/astrodeck/hub.py`** (additive)
```python
self.live_stacker: "LiveStacker | None" = None          # in __init__, by self.previews
def start_live_stack(self, reject_frac: float = DEFAULT_REJECT_FRAC) -> dict: ...   # -> {"active": True}
def reset_live_stack(self) -> dict: ...                                             # -> {"active": bool, ...}
def stop_live_stack(self) -> dict: ...                                              # -> {"active": False}
# poll_status(): out["live_stack_active"] = self.live_stacker is not None
```

**`server/astrodeck/api/app.py`** (additive)
```python
class LiveStackBody(CaptureBody):
    reject_frac: float = 0.08
# POST /api/capture/livestack/start  (LiveStackBody) -> {"active": True}
# POST /api/capture/livestack/reset                  -> {"active": bool, "frames": int, ...}
# POST /api/capture/livestack/stop                   -> {"active": False}
```

**`ui/src/types.ts`** (additive)
```ts
export interface LiveStackInfo { frames: number; integrated_s: number; rejected: number; accepted: boolean; }
// on PreviewInfo:  livestack?: LiveStackInfo;
// on RigStatus:    live_stack_active?: boolean;
```

**`ui/src/lib/liveStack.ts`**
```ts
import type { LiveStackInfo } from "../types";
export function integratedLabel(seconds: number): string;   // "48 s" | "24 min" | "1.5 h"
export function formatLiveStack(ls: LiveStackInfo): {
  frames: string;      // "1 frame" | "12 frames"
  integrated: string;  // integratedLabel(integrated_s)
  rejected: string;    // "" when 0 | "3 skipped"
  headline: string;    // "12 frames · 24 min integrated"
};
```

---

### Task 1 — Pure core: `LiveStacker` + alignment (pytest, synthetic frames) — **Opus**
*Impl tier: **Opus**. Genuinely subtle numeric correctness: the shift/coverage slice arithmetic, the reseed/size self-heal state machine, and the alignment sign convention are the kind of thing that silently registers frames the wrong way. This is the tested heart everything else trusts.*

**Files:** create `server/astrodeck/imaging/livestack.py`; create `server/tests/test_livestack.py`.

**Step 1a — write the failing test first.** `server/tests/test_livestack.py`:
```python
"""Live-stacking core: brightest-star alignment, running-mean accumulation with
per-pixel coverage, drift-reject, and auto re-anchor. Synthetic shifted frames."""
import math
import numpy as np
from astrodeck.imaging import LiveStacker, brightest_centroid, align_offset
from astrodeck.imaging import detect_stars


def star_frame(cx: float, cy: float, *, shape=(200, 240), bg=400.0, noise=5.0,
               flux=300_000.0, sigma=1.6, seed=3) -> np.ndarray:
    """One bright star (the alignment anchor) at (cx, cy) on a faint background."""
    rng = np.random.default_rng(seed)
    h, w = shape
    img = rng.normal(bg, noise, shape)
    xs = np.arange(w) - cx
    ys = (np.arange(h) - cy)[:, None]
    img += flux * np.exp(-(xs**2 + ys**2) / (2 * sigma**2)) / (2 * math.pi * sigma**2)
    return np.clip(img, 0, 65535).astype(np.uint16)


def test_brightest_centroid_picks_highest_flux():
    f = star_frame(120.0, 100.0)
    c = brightest_centroid(detect_stars(f))
    assert c is not None
    assert abs(c[0] - 120.0) < 1.5 and abs(c[1] - 100.0) < 1.5

def test_brightest_centroid_none_when_no_stars():
    assert brightest_centroid([]) is None

def test_align_offset_arithmetic():
    assert align_offset((100.0, 100.0), (103.0, 98.0)) == (3.0, -2.0)

def test_first_sub_seeds_and_mean_matches():
    s = LiveStacker()
    o = s.add(star_frame(120.0, 100.0), 120.0)
    assert o.accepted and o.frames == 1 and o.reason == ""
    assert abs(o.integrated_s - 120.0) < 1e-6
    assert s.mean() is not None and s.mean().shape == (200, 240)

def test_shifted_sub_registers_onto_reference():
    s = LiveStacker()
    a = star_frame(120.0, 100.0)
    s.add(a, 120.0)
    # star moved +3 in x, -2 in y (roll keeps it far from edges)
    b = np.roll(a, shift=(-2, 3), axis=(0, 1))
    o = s.add(b, 120.0, stars=detect_stars(b))
    assert o.accepted and o.frames == 2
    assert round(o.dx) == 3 and round(o.dy) == -2
    # both subs' star sits at the reference (120,100) in the mean -> that pixel is
    # brighter than the un-covered corner, and integration doubled.
    m = s.mean()
    assert m[100, 120] > m[0, 0]
    assert abs(s.integrated_s - 240.0) < 1e-6

def test_drift_beyond_threshold_is_rejected():
    s = LiveStacker(reject_frac=0.08)   # 0.08 * min(200,240)=200 -> 16 px
    s.add(star_frame(120.0, 100.0), 120.0)
    o = s.add(star_frame(160.0, 100.0), 120.0)   # 40 px drift > 16
    assert not o.accepted and o.reason == "drift"
    assert o.frames == 1 and o.rejected == 1

def test_three_consecutive_drifts_reanchor():
    s = LiveStacker(reject_frac=0.08, reanchor_after=3)
    s.add(star_frame(120.0, 100.0), 120.0)
    for _ in range(2):
        o = s.add(star_frame(170.0, 100.0), 120.0)
        assert o.reason == "drift"
    o = s.add(star_frame(170.0, 100.0), 120.0)   # 3rd consecutive -> reseed
    assert o.reason == "reseed" and o.frames == 1

def test_size_change_reseeds():
    s = LiveStacker()
    s.add(star_frame(120.0, 100.0), 120.0)
    o = s.add(star_frame(60.0, 50.0, shape=(100, 120)), 120.0)
    assert o.reason == "size" and o.frames == 1 and s.mean().shape == (100, 120)

def test_no_stars_rejected_without_reanchor_spiral():
    s = LiveStacker()
    s.add(star_frame(120.0, 100.0), 120.0)
    blank = np.full((200, 240), 400, dtype=np.uint16)
    o = s.add(blank, 120.0, stars=[])
    assert not o.accepted and o.reason == "no_stars" and o.frames == 1

def test_reset_clears_accumulator():
    s = LiveStacker()
    s.add(star_frame(120.0, 100.0), 120.0)
    s.reset()
    assert s.frames == 0 and s.integrated_s == 0.0 and s.mean() is None
```
Run (expect ImportError / collection failure — the module doesn't exist yet):
```
server/.venv/Scripts/pytest.exe server/tests/test_livestack.py -n0 -q
```
Expected: `ModuleNotFoundError` on `LiveStacker` (RED).

**Step 1b — implement `server/astrodeck/imaging/livestack.py`.**
```python
"""Live-stacking (EAA "Live View") accumulator core.

A fixed-frame running-MEAN stack with a lightweight brightest-star drift-reject.
This is the pure, tested heart of NOV-1 — no device I/O. The hub owns one
``LiveStacker`` while Live View is armed and feeds it every raw linear sub; the
mean it returns becomes the displayed preview (spec §1.3). Full star-match
registration is a deferred second pass — v1 aligns on a single integer offset.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .stars import Star, detect_stars

DEFAULT_REJECT_FRAC = 0.08
DEFAULT_REANCHOR_AFTER = 3


def brightest_centroid(stars: list[Star]) -> tuple[float, float] | None:
    """(x, y) of the highest-FLUX star — the alignment anchor. None if empty.

    detect_stars appends in descending peak-pixel order, NOT flux order
    (stars.py:73), so select by flux explicitly (mirrors _median_hfr_from)."""
    if not stars:
        return None
    s = max(stars, key=lambda s: s.flux)
    return (float(s.x), float(s.y))


def align_offset(ref: tuple[float, float],
                 cur: tuple[float, float]) -> tuple[float, float]:
    """Pixel drift (dx, dy) of ``cur`` relative to ``ref``: a star at ``ref``
    appears at ``ref + offset`` in the current sub."""
    return (cur[0] - ref[0], cur[1] - ref[1])


def _axis_slices(n: int, d: int) -> tuple[slice, slice]:
    """Destination/source index ranges along one axis for an integer shift ``d``.
    accumulator[dst] += data[src], where src == dst + d, in-bounds both sides."""
    if d >= 0:
        return slice(0, n - d), slice(d, n)
    return slice(-d, n), slice(0, n + d)


@dataclass
class StackOutcome:
    accepted: bool
    frames: int
    integrated_s: float
    rejected: int
    dx: float
    dy: float
    reason: str


class LiveStacker:
    def __init__(self, reject_frac: float = DEFAULT_REJECT_FRAC,
                 reanchor_after: int = DEFAULT_REANCHOR_AFTER) -> None:
        self.reject_frac = float(reject_frac)
        self.reanchor_after = int(reanchor_after)
        self.reset()

    def reset(self) -> None:
        self._sum: np.ndarray | None = None          # float32 running sum
        self._cov: np.ndarray | None = None          # uint16 per-pixel coverage
        self._ref: tuple[float, float] | None = None  # reference brightest centroid
        self._shape: tuple[int, int] | None = None
        self._frames = 0
        self._integrated = 0.0
        self._rejected = 0
        self._consec = 0

    @property
    def frames(self) -> int:
        return self._frames

    @property
    def integrated_s(self) -> float:
        return self._integrated

    @property
    def rejected(self) -> int:
        return self._rejected

    def mean(self) -> np.ndarray | None:
        if self._sum is None or self._cov is None:
            return None
        cov = np.maximum(self._cov, 1)
        return np.clip(np.round(self._sum / cov), 0, 65535).astype(np.uint16)

    def _seed(self, data: np.ndarray, exposure_s: float,
              centroid: tuple[float, float]) -> None:
        self._shape = (int(data.shape[0]), int(data.shape[1]))
        self._sum = data.astype(np.float32)
        self._cov = np.ones(self._shape, dtype=np.uint16)
        self._ref = centroid
        self._frames = 1
        self._integrated = float(exposure_s)
        self._consec = 0

    def _outcome(self, accepted: bool, dx: float, dy: float,
                 reason: str) -> StackOutcome:
        return StackOutcome(accepted, self._frames, self._integrated,
                            self._rejected, dx, dy, reason)

    def add(self, data: np.ndarray, exposure_s: float, *,
            stars: list[Star] | None = None) -> StackOutcome:
        if stars is None:
            stars = detect_stars(data)
        centroid = brightest_centroid(stars)
        if centroid is None:
            self._rejected += 1
            return self._outcome(False, 0.0, 0.0, "no_stars")

        # first sub, or the frame geometry changed (binning) -> (re)seed.
        if self._sum is None or (int(data.shape[0]), int(data.shape[1])) != self._shape:
            reason = "size" if self._sum is not None else ""
            self._seed(data, exposure_s, centroid)
            return self._outcome(True, 0.0, 0.0, reason)

        dx, dy = align_offset(self._ref, centroid)
        h, w = self._shape
        if (dx * dx + dy * dy) ** 0.5 > self.reject_frac * min(h, w):
            self._rejected += 1
            self._consec += 1
            if self._consec >= self.reanchor_after:
                self._seed(data, exposure_s, centroid)   # deliberate re-frame
                return self._outcome(True, dx, dy, "reseed")
            return self._outcome(False, dx, dy, "drift")

        idx = int(round(dx))
        idy = int(round(dy))
        ys_dst, ys_src = _axis_slices(h, idy)
        xs_dst, xs_src = _axis_slices(w, idx)
        self._sum[ys_dst, xs_dst] += data[ys_src, xs_src].astype(np.float32)
        self._cov[ys_dst, xs_dst] += 1
        self._frames += 1
        self._integrated += float(exposure_s)
        self._consec = 0
        return self._outcome(True, dx, dy, "")
```

**Step 1c — export** from `server/astrodeck/imaging/__init__.py`: add `from .livestack import LiveStacker, StackOutcome, brightest_centroid, align_offset, DEFAULT_REJECT_FRAC, DEFAULT_REANCHOR_AFTER` and append those names to `__all__`.

Run:
```
server/.venv/Scripts/pytest.exe server/tests/test_livestack.py -n0 -q
```
Expected: **all pass** (GREEN), e.g. `10 passed`.

---

### Task 2 — Hub: arm/reset/stop + the `_publish_preview` hook + status flag — **Opus**
*Impl tier: **Opus**. The `_publish_preview` surgery is in the capture hot path: it must keep `detect_stars`/`measure_stars`/`cloud_score` on the raw sub, swap only the image-pipeline pixels to the mean, override `stats`, leave the NINA branch untouched, and no-op cleanly when unarmed. Getting the sub-vs-mean split wrong corrupts overlays or cloud detection. Correctness-sensitive.*

**Files:** edit `server/astrodeck/hub.py`; create `server/tests/test_hub_livestack.py`.

**Step 2a — hub state + methods.** In `Hub.__init__` (by `self.previews`, `hub.py:178`) add:
```python
# Live View (NOV-1): the single EAA running-mean accumulator, non-None while
# armed. Fed each raw linear sub in _publish_preview; None = feature off.
self.live_stacker = None
```
Add methods near the loop controls (`hub.py:1720`):
```python
def start_live_stack(self, reject_frac: float = 0.08) -> dict:
    from .imaging import LiveStacker
    self.live_stacker = LiveStacker(reject_frac=reject_frac)
    bus.log("info", "Live View on — stacking subs", "capture")
    return {"active": True}

def reset_live_stack(self) -> dict:
    if self.live_stacker is not None:
        self.live_stacker.reset()
    return {"active": self.live_stacker is not None,
            "frames": getattr(self.live_stacker, "frames", 0)}

def stop_live_stack(self) -> dict:
    self.live_stacker = None
    return {"active": False}
```
Import `LiveStacker` lazily inside `start_live_stack` (shown) to avoid touching the module-top `from .imaging import (...)` block. Also clear it on teardown: in `_teardown` (`hub.py:703`) add `self.live_stacker = None` alongside the other reset state.

**Step 2b — the hook.** In `_publish_preview` raw/linear branch (`hub.py:1517-1563`), restructure the top of the `else:` so detection happens first and the image pixels can be swapped:
```python
else:
    sub = data                                   # the raw linear sub
    stars = await asyncio.to_thread(detect_stars, sub)   # anchor + HFR (one pass)
    ls_info = None
    if self.live_stacker is not None:
        outcome = await asyncio.to_thread(
            self.live_stacker.add, sub, frame.exposure_s, stars=stars)
        mean = self.live_stacker.mean()
        if mean is not None:
            data = mean                          # the image pipeline shows the stack
            info["stats"] = await asyncio.to_thread(frame_stats, data)
        ls_info = {"frames": outcome.frames,
                   "integrated_s": round(outcome.integrated_s, 1),
                   "rejected": outcome.rejected, "accepted": outcome.accepted}
    black, mid, white = await asyncio.to_thread(auto_levels, data)
    jpeg, dw, dh = await asyncio.to_thread(to_jpeg, data, black=black, mid=mid, white=white)
    lossless = await asyncio.to_thread(to_png, data)
    thumb = await asyncio.to_thread(to_thumb, data)
    stretched = await asyncio.to_thread(stretch_with, data, black, mid, white)
    hist_display = await asyncio.to_thread(display_histogram, stretched)
    hfr, count, marks = measure_stars(stars, full_well=info["full_well"])
    # ... existing info.update(...) unchanged ...
    if data_is_linear:
        cloud = await asyncio.to_thread(cloud_score, sub, stars=stars)   # SUB, not mean
        info["cloud"] = cloud.to_dict()
    # ... existing hfr/stars setdefault + PreviewEntry unchanged ...
    if ls_info is not None:
        info["livestack"] = ls_info
```
The two load-bearing edits vs today's code: `detect_stars` moves above the image encodes and now runs on `sub`; `cloud_score`'s first arg becomes `sub` (was `data` at `hub.py:1551`, which now may be the mean). `histogram_linear` (`hub.py:1532`) should also read `sub` so the Advanced linear histogram stays a true single-sub linear view — change `compute_histogram, data` → `compute_histogram, sub`. Everything else in the branch is untouched.

**Step 2c — status flag.** In `poll_status` (`hub.py:2263`), next to `"looping"`:
```python
out["live_stack_active"] = self.live_stacker is not None
```

**Step 2d — hub integration test** `server/tests/test_hub_livestack.py` (drives the real sim rig through the hub):
```python
"""Live View through the hub: arming makes preview events carry `livestack`,
frames accumulate across the loop's subs, stop clears it, and an unarmed hub
publishes NO livestack field (byte-for-byte-unchanged preview path)."""
import asyncio
import numpy as np
import pytest
from astrodeck.hub import Hub


class _Frame:
    rendered_bytes = None
    def __init__(self, data):
        self.data = data
        self.exposure_s = 120.0
        self.gain = 100
        self.binning = 1
        self.full_well = 65535
        self.bayer_pattern = None
        self.saved_path = None
        self.data_is_linear = True


def _sub(cx, cy):
    import math
    rng = np.random.default_rng(1)
    img = rng.normal(400, 5.0, (200, 240))
    xs = np.arange(240) - cx
    ys = (np.arange(200) - cy)[:, None]
    img += 300_000.0 * np.exp(-(xs**2 + ys**2) / (2 * 1.6**2)) / (2 * math.pi * 1.6**2)
    return np.clip(img, 0, 65535).astype(np.uint16)


def test_arming_accumulates_and_stop_clears():
    hub = Hub()
    assert hub.start_live_stack()["active"] is True
    a = asyncio.run(hub._publish_preview(_Frame(_sub(120, 100))))
    assert a["livestack"]["frames"] == 1
    b = asyncio.run(hub._publish_preview(_Frame(_sub(122, 99))))   # 2px drift, accepted
    assert b["livestack"]["frames"] == 2 and b["livestack"]["accepted"] is True
    assert abs(b["livestack"]["integrated_s"] - 240.0) < 1e-6
    hub.stop_live_stack()
    c = asyncio.run(hub._publish_preview(_Frame(_sub(120, 100))))
    assert "livestack" not in c

def test_unarmed_publish_has_no_livestack():
    hub = Hub()
    info = asyncio.run(hub._publish_preview(_Frame(_sub(120, 100))))
    assert "livestack" not in info
```
Run:
```
server/.venv/Scripts/pytest.exe server/tests/test_hub_livestack.py server/tests/test_livestack.py -n0 -q
```
Expected: **all pass**. Then a broader guard that the preview path is otherwise unchanged:
```
server/.venv/Scripts/pytest.exe server/tests/test_imaging.py -n0 -q
```
Expected: still **all pass** (no regression in the single-frame preview contract).

---

### Task 3 — API: livestack start/reset/stop routes — **Sonnet**
*Impl tier: **Sonnet**. Mechanical: mirror `capture_loop`'s guards + body.*

**Files:** edit `server/astrodeck/api/app.py`.

Add the body near `CaptureBody` (`app.py:435`):
```python
class LiveStackBody(CaptureBody):
    reject_frac: float = 0.08
```
Add routes next to `capture_stop` (`app.py:2425`), copying the polar/engine/camera guards from `capture_loop` (`app.py:2397-2404`):
```python
@app.post("/api/capture/livestack/start", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
@declare(CAP_CONTROL_CAPTURE)
async def livestack_start(body: LiveStackBody):
    if hub.polar.running:
        raise HTTPException(409, "polar alignment in progress")
    if engine.running:
        raise HTTPException(409, "a sequence is running")
    try:
        hub.require("camera")
    except DeviceError as e:
        raise _err(e)
    hub.start_live_stack(reject_frac=body.reject_frac)
    await hub.start_loop(body.exposure_s, body.gain, body.offset, body.binning,
                         frame_type="Light")
    return {"active": True}

@app.post("/api/capture/livestack/reset", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
@declare(CAP_CONTROL_CAPTURE)
async def livestack_reset():
    return hub.reset_live_stack()

@app.post("/api/capture/livestack/stop", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
@declare(CAP_CONTROL_CAPTURE)
async def livestack_stop():
    hub.stop_loop()
    return hub.stop_live_stack()
```
Verify import wiring compiles (no test framework change):
```
server/.venv/Scripts/pytest.exe server/tests/test_hub_livestack.py -n0 -q
```
Expected: still **all pass** (this task adds routes; the hub test already covers behavior). If an existing route-smoke test enumerates endpoints, run it too and expect pass.

---

### Task 4 — Client types: additive `livestack` + `live_stack_active` — **Sonnet**
*Impl tier: **Sonnet**. Two additive fields + one interface.*

**Files:** edit `ui/src/types.ts`.

Add the interface near `PreviewInfo` (`types.ts:191`):
```ts
// NOV-1 live-stacking readout — present only on raw/linear subs while Live View
// is armed (server-side accumulator). Old clients ignore it.
export interface LiveStackInfo {
  frames: number;        // subs in the current stack
  integrated_s: number;  // Σ accepted exposures
  rejected: number;      // subs skipped by drift-reject
  accepted: boolean;     // was THIS sub accepted (vs a drift skip)
}
```
On `PreviewInfo` (after `star_list?` at `:217`): `livestack?: LiveStackInfo;`
On `RigStatus` (after `looping` at `:43`): `live_stack_active?: boolean; // NOV-1: Live View armed`

Gate:
```
cd ui && npx tsc -b
```
Expected: exit 0 (additive optional fields — no consumer breaks).

---

### Task 5 — Client pure formatter: `lib/liveStack.ts` (+ tsx test) — **Sonnet**
*Impl tier: **Sonnet**. Pure string formatting with unit/plural edges; tsx inline-assert.*

**Files:** create `ui/src/lib/liveStack.ts`; create `ui/src/lib/__tests__/liveStack.test.ts`.

`ui/src/lib/liveStack.ts`:
```ts
// Pure formatting for the Live View readout (NOV-1). No React, tsx-tested.
import type { LiveStackInfo } from "../types";

export function integratedLabel(seconds: number): string {
  const s = Math.max(0, seconds);
  if (s < 60) return `${Math.round(s)} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  return `${(s / 3600).toFixed(1).replace(/\.0$/, "")} h`;
}

export function formatLiveStack(ls: LiveStackInfo): {
  frames: string; integrated: string; rejected: string; headline: string;
} {
  const n = Math.max(0, Math.floor(ls.frames));
  const frames = `${n} ${n === 1 ? "frame" : "frames"}`;
  const integrated = integratedLabel(ls.integrated_s);
  const r = Math.max(0, Math.floor(ls.rejected));
  const rejected = r > 0 ? `${r} skipped` : "";
  return { frames, integrated, rejected, headline: `${frames} · ${integrated} integrated` };
}
```
Test `ui/src/lib/__tests__/liveStack.test.ts` (eta.test.ts harness):
```ts
import { integratedLabel, formatLiveStack } from "../liveStack";
let passed = 0, failed = 0; const failures: string[] = [];
function test(name: string, fn: () => void) { try { fn(); passed++; } catch (e) { failed++; failures.push(`✗ ${name}: ${(e as Error).message}`); } }
function eq<T>(a: T, b: T, m = "") { if (a !== b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }

test("integratedLabel: seconds under a minute", () => eq(integratedLabel(48), "48 s"));
test("integratedLabel: minutes", () => eq(integratedLabel(1440), "24 min"));
test("integratedLabel: hours drops .0", () => eq(integratedLabel(3600), "1 h"));
test("integratedLabel: fractional hours", () => eq(integratedLabel(5400), "1.5 h"));
test("integratedLabel: negative clamps", () => eq(integratedLabel(-9), "0 s"));
test("formatLiveStack: singular frame", () => eq(formatLiveStack({ frames: 1, integrated_s: 120, rejected: 0, accepted: true }).frames, "1 frame"));
test("formatLiveStack: headline", () => eq(formatLiveStack({ frames: 12, integrated_s: 1440, rejected: 0, accepted: true }).headline, "12 frames · 24 min integrated"));
test("formatLiveStack: rejected suffix", () => eq(formatLiveStack({ frames: 12, integrated_s: 1440, rejected: 3, accepted: true }).rejected, "3 skipped"));
test("formatLiveStack: no rejected -> empty", () => eq(formatLiveStack({ frames: 5, integrated_s: 600, rejected: 0, accepted: true }).rejected, ""));

const total = passed + failed;
console.log(`\nliveStack.test: ${passed}/${total} passed`);
if (failures.length) console.error(failures.join("\n"));
export const result = { passed, failed, total };
```
Run:
```
cd ui && npx tsx src/lib/__tests__/liveStack.test.ts
cd ui && npx tsc -b
```
Expected: `liveStack.test: 9/9 passed`; tsc exit 0.

---

### Task 6 — Client: `LiveStackReadout` in the Live Preview header — **Sonnet**
*Impl tier: **Sonnet**. Thin render off the tested formatter; typechecker-verified.*

**Files:** create `ui/src/components/preview/LiveStackReadout.tsx`; edit `ui/src/components/preview/LivePreview.tsx`.

`LiveStackReadout.tsx`:
```tsx
// NOV-1 Live View readout: "◉ 12 frames · 24 min integrated · 3 skipped".
// Renders nothing unless the shown preview carries a livestack block.
import type { PreviewInfo } from "../../types";
import { formatLiveStack } from "../../lib/liveStack";

export function LiveStackReadout({ preview }: { preview: PreviewInfo | null }) {
  const ls = preview?.livestack;
  if (!ls) return null;
  const f = formatLiveStack(ls);
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] mono text-accent" aria-live="polite">
      <span className="w-2 h-2 rounded-full bg-accent animate-pulse" aria-hidden />
      <span className="tabular-nums">{f.headline}</span>
      {f.rejected && <span className="text-dim">· {f.rejected}</span>}
    </span>
  );
}
```
In `LivePreview.tsx` header (`:81`), place the readout alongside `PreviewMeta`:
```tsx
import { LiveStackReadout } from "./LiveStackReadout";
// ...
<Panel title="Live Preview" right={
  <span className="flex items-center gap-3">
    <LiveStackReadout preview={shown} />
    <PreviewMeta preview={shown} />
  </span>
}>
```
Gate:
```
cd ui && npx tsc -b
```
Expected: exit 0.

---

### Task 7 — Client: Live View toggle + Reset in Capture — **Sonnet**
*Impl tier: **Sonnet**. Wires two buttons to the routes + server-truth status; honest-disabled; typechecker-verified.*

**Files:** edit `ui/src/views/CaptureView.tsx` (and add `useStatus`-derived active flag; the value already exists via `status.live_stack_active` from Task 4).

Read server truth near `looping` (`CaptureView.tsx:111`):
```tsx
const liveStackOn = !!status?.live_stack_active;
```
Add handlers by `onLoop`/`onStop` (`:279-318`):
```tsx
const onLiveView = () => {
  if (captureBlocked || !canCapture || exposureInvalid || gainInvalid) return;
  if (liveStackOn) { act(() => api.post("/api/capture/livestack/stop")); return; }
  beginExposure(exposureS);
  act(() => api.post("/api/capture/livestack/start", { ...body, frame_type: "Light" }));
};
const onResetStack = () => { if (canCapture && liveStackOn) act(() => api.post("/api/capture/livestack/reset")); };
```
Add controls under the Single/Loop/Stop grid (`:538`). The Live View button shows its armed state via `btn-accent`; Reset uses the honest-disabled idiom (dim + lock + `aria-disabled` + `title`) when not armed, mirroring `CaptureView.tsx:498-506`:
```tsx
<div className="grid grid-cols-2 gap-2 mt-2">
  <button
    className={`btn tap min-h-[44px] ${liveStackOn ? "btn-accent border-accent" : ""}`}
    aria-pressed={liveStackOn}
    disabled={!canCapture || captureBlocked || exposureInvalid || gainInvalid}
    title="Stack subs into one continuously brightening image"
    onClick={onLiveView}>
    {liveStackOn ? "Live View · on" : "Live View"}
  </button>
  {liveStackOn ? (
    <button className="btn tap min-h-[44px]" onClick={onResetStack}>Reset stack</button>
  ) : (
    <span className="btn tap min-h-[44px] opacity-40 inline-flex items-center gap-1.5 cursor-not-allowed"
      aria-disabled title="Start Live View to reset the stack">
      <Icon name="lock" size={12} /> Reset stack
    </span>
  )}
</div>
```
(`Icon` is already imported at `CaptureView.tsx:24`.) The Stop button (`:531`) already calls `/api/capture/stop`; that stops the loop but leaves the stacker armed — acceptable (Live View · on stays lit, the next Loop resumes the stack). Toggling Live View off is the explicit disarm.

Gate + smoke the two client tests:
```
cd ui && npx tsc -b
cd ui && npx tsx src/lib/__tests__/liveStack.test.ts
```
Expected: tsc exit 0; `liveStack.test: 9/9 passed`.

---

### Task 8 — Full-suite verification — **Sonnet**
*Impl tier: **Sonnet**. Run the gates; confirm no regression.*

```
server/.venv/Scripts/pytest.exe server/tests/test_livestack.py server/tests/test_hub_livestack.py server/tests/test_imaging.py server/tests/test_clouds.py -n0 -q
cd ui && npx tsc -b
cd ui && npx tsx src/lib/__tests__/liveStack.test.ts
```
Expected: pytest all pass; tsc exit 0; tsx `9/9 passed`. Stage ONLY the files named in this plan (never `git add -A`).

---

## 4. Open decisions (with a recommendation each)

**D1 — Mean vs sum for the displayed image.**
*Recommendation:* **Running MEAN** for the displayed pixels (keeps the 16-bit range stable so `auto_stretch` behaves identically each frame; the brightening comes from the √N noise drop letting the stretch push harder), and report `integrated_s` = Σ accepted exposures separately as the honest "minutes of signal." Sum would overflow/blow past `full_well` and break the clip mask. Adopted in Task 1.

**D2 — Per-pixel coverage array vs scalar count + zero-fill.**
*Recommendation:* **Coverage array** (`uint16`, one per accumulator). It costs one extra HxW array but eliminates the progressive edge-darkening a scalar-count + `np.roll` zero-fill would show as drift accumulates. Memory is bounded (one `float32` sum + one `uint16` coverage while armed; released on stop/teardown) and the feature is opt-in. Adopted in Task 1.

**D3 — Overlay/HFR describe the sub, image describes the mean.**
*Recommendation:* **Accept the split for v1** and document it. The star overlay is default-OFF (`OverlayToggles.stars=false`, `types.ts:239`) and beginners in Live View rarely enable it; the per-sub HFR is the honest focus metric of the incoming sub. Detecting a *second* time on the mean (so marks align to the shown stack) is a clean Pass-2 upgrade but not worth the extra detect-per-frame now. Keeps the hook to a single `detect_stars` pass, exactly matching today's cost.

**D4 — Drift-reject threshold: fraction of frame vs fixed px.**
*Recommendation:* **Fraction** (`reject_frac`, default 0.08 → 8% of the short edge) so it scales across sensor sizes and binning without per-rig tuning. Exposed on `LiveStackBody.reject_frac` for a future Advanced control; not surfaced in the beginner UI in v1.

**D5 — Toggle state source of truth.**
*Recommendation:* **Server truth via `poll_status().live_stack_active`** (Task 2c) rather than a client store slice, so a page reload mid-stack shows the correct armed state and the readout keeps flowing from `preview.livestack`. Costs one status field; no store-shape change. Adopted.

**D6 — Auto re-anchor after N consecutive drift-rejects.**
*Recommendation:* **Keep it** (default 3). It makes "slew to a new target with Live View still on" self-heal into a fresh stack instead of rejecting forever, and it is cheap + fully tested (Task 1 `test_three_consecutive_drifts_reanchor`). A manual **Reset stack** button (Task 7) remains for the deliberate case.

**D7 — Does Stop disarm the stacker?**
*Recommendation:* **No** — `/api/capture/stop` stops the loop but leaves the stacker armed (Live View · on stays lit; resuming Loop continues the stack). Explicit disarm is toggling **Live View** off (`/api/capture/livestack/stop`). This matches the mental model that Live View is a *mode*, not a single run. Documented in Task 7.
