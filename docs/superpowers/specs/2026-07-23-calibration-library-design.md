# PRO-1 — Master calibration-frame library with automatic matching + reuse

Combined design spec + TDD implementation plan. One shippable v1: **index +
match + numpy master-stack + a sequence pre-flight coverage warning + a library
UI panel.** Nice-to-haves (dark-scaling, flat-dark subtraction, auto-apply to
lights, incremental indexing) are explicitly deferred at the end.

---

## 1. Design

### 1.1 Goal

Today AstroDeck *captures* darks/flats/bias (each frame carries `IMAGETYP` +
the exposure/gain/offset/temp/binning/filter headers) but **nothing builds
masters, indexes them, matches them to lights, or warns when a light has no
calibration.** PRO-1 adds a managed store that:

1. **Indexes** acquired calibration frames by
   `(frame_type, exposure_s, gain, offset, temp_bin, binning, filter)`.
2. **Builds masters** by numpy median / sigma-clipped-mean stacking (bounded
   memory, robust to outliers), one master per index bucket.
3. **Matches** a light step's `(exposure, gain, offset, temp, binning, filter)`
   to the best master within a tolerance window, reusing masters across nights.
4. **Surfaces coverage** in a library UI panel (what masters exist, frame
   counts, when built).
5. **Warns at sequence pre-flight** when a light step has no matching dark/flat.

### 1.2 Current-state seams (every line read first-hand)

- **`server/astrodeck/imaging/fitsio.py:12-49`** — `save_fits` writes exactly the
  headers a calibration frame carries **on this branch**: `EXPTIME` (18),
  `GAIN` (19), `OFFSET` (20), `XBINNING`/`YBINNING` (21-22), `IMAGETYP` (23),
  `DATE-OBS` (28), `CCD-TEMP` (29-30, only when `temperature_c is not None`),
  `BAYERPAT` (31-32), `OBJECT` (33-34), `FILTER` (35-36), `INSTRUME` (43-44).
  **These are the match keys.** PRO-2's richer headers (`XPIXSZ`, `READOUTMODE`,
  sensor dims) live on another branch and are **not** available here — design
  against this header set only.
- **`server/astrodeck/hub.py:1419-1479`** — `hub.capture(exposure_s, gain, offset,
  binning, save, target, frame_type)`. When `save=True` and the frame is linear
  (sim/Alpaca), it resolves the active filter (1443-1449), builds
  `local_save_path = self._capture_path(...)` (1450) and writes the FITS via
  `save_fits(..., frame_type=frame_type, ...)` off-thread (1461-1464). Dark/Bias
  are shutter-closed (1430-1431). **This is the source of every raw calibration
  frame the library scans.**
- **`server/astrodeck/hub.py:1772-1792`** — `_capture_path` renders the frame path
  under `CAPTURE_DIR` via the PRO-11 naming template. Calibration frames land in
  ordinary `*.fits` files under `CAPTURE_DIR` (target folder or `untargeted`).
- **`server/astrodeck/hub.py:96-97`** — `CAPTURE_DIR = Path($ASTRODECK_CAPTURE_DIR)`
  or `repo/captures`. Tests monkeypatch `hub.CAPTURE_DIR`; the library must
  resolve it **live** (never bind it at import).
- **`server/astrodeck/hub.py:1755-1770`** — the **side-JSON precedent**:
  `_counter_file()` returns `CAPTURE_DIR / ".frame_counters.json"` (resolved live
  so the monkeypatch is honored) and reads/writes it with `read_json_or` /
  `write_json_atomic`. `.sequence_resume.json` follows the same pattern. The
  masters manifest reuses this exact idiom.
- **`server/astrodeck/persist.py:90-147`** — `write_json_atomic` (Windows-safe
  atomic + `.bak`), `read_json_or`, `ensure_dir`; `safe_id_path` (41-68) refuses
  path-escape ids on any OS → `KeyError` (routes map to 404). The masters store
  reuses these verbatim.
- **`server/astrodeck/plans.py:57-166`** — the **store analog**: `PlanLibrary` is a
  uuid-keyed JSON store (list/get/save/delete/export) under `CONFIG_DIR/plans`,
  a module singleton `plan_library` (166). `CalibrationLibrary` mirrors its
  shape (module singleton, atomic manifest, `MAX_*` soft quota, id-safe paths).
- **`server/astrodeck/imaging/readnoise.py:14-24`** — precedent for a **pure numpy
  frame-list reduction**: `[np.asarray(f, dtype=np.float64) for f in frames]`,
  validate non-empty, reduce. The stacker follows this shape.
- **`server/astrodeck/imaging/stars.py:56-62`** — precedent for robust stats:
  `img.astype(np.float64)`, `np.median`, MAD → sigma via `* 1.4826`. The
  sigma-clip stacker reuses the MAD-sigma convention.
- **FITS read-back** — `astropy.io.fits`. `fits.getheader(path)` for the index
  scan, `fits.open(path, memmap=True)` for the streamed stack (row-strips without
  loading whole frames), `fits.PrimaryHDU(data).writeto(...)` to save a master
  (same call `save_fits` uses at line 16/48). Test precedent:
  `server/tests/test_fitsio.py:15-25` builds a synthetic `CameraFrame` +
  `save_fits` to a `tmp_path`.
- **`server/astrodeck/sequence/models.py:10-23,49-63`** — `ExposureStep` carries
  `filter`, `exposure_s`, `gain`, `offset`, `binning`, `count`, `frame_type`;
  `Target.calibration` (57) flags a darks/bias/flats target; `SequencePlan.cool_to`
  (74) is the sensor temp lights are shot at. A **light need** is derived from a
  non-calibration target's steps + `plan.cool_to`.
- **`server/astrodeck/api/app.py:3334-3379`** — `POST /api/sequence/preflight`
  (`sequence_preflight_plan`) is the **plan-wide non-blocking pre-flight**. It
  already returns `{"ok": not warnings, "warnings": [{"target","kind","message"}]}`
  and appends `never_rises` altitude warnings (3372-3378). **This is exactly
  where the `no_calibration` coverage warning slots** — same shape, appended
  after the altitude loop.
- **`server/astrodeck/api/app.py:2173-2237`** — the plans route block: list/get/
  save/delete/export/import, `to_thread` offload for disk work,
  `Depends(require(CAP_...))` + `@declare(...)`, `KeyError → HTTPException(404)`.
  The calibration routes mirror this.
- **`server/astrodeck/config.py:367-370,446-475`** — `NamingConfig` is a 1-field
  `BaseModel`; `AppConfig` appends feature config blocks with
  `Field(default_factory=...)` ("old configs load fine"). `CalibrationConfig`
  appends the same way.
- **UI `ui/src/components/sequence/PlanLibraryPanel.tsx`** — the **library-panel
  pattern**: `api.get<PlanRow[]>("/api/plans")` into `rows` (58-68), a
  `loadErr`/empty/list tri-state body (312-389), per-row actions, `confirmDialog`
  for delete (151-168), `showToast` for feedback, `useCanControlCapture()`
  gating the write buttons (35, 220). `CalibrationLibraryPanel` mirrors this.
- **UI `ui/src/lib/preflight.ts:41-222`** — `buildPreflight(status, plan, site,
  altById, actions)` builds the `CheckItem[]` the modal/strip render; the
  **filters row** (159-180) is the exact template for a `calibration` row:
  collect what the plan references, compare against what's available, push
  `ok`/`warn`/`skipped`. `preflightVerdict` (225-229). Consumed by
  `PreflightModal.tsx` (50, via `usePreflight`) and `PreflightStrip`.
- **UI `ui/src/lib/calibration.ts:10-12`** — existing `FrameType` union +
  `FRAME_TYPES`; reuse the type. (This file is the *capture-nudge* logic, not a
  library — unrelated to PRO-1 storage.)
- **UI `ui/src/lib/__tests__/eta.test.ts:1-43`** — the **tsx inline-assert
  harness** (`test`/`eq`/`assert`/`near`, printed pass/fail counts). Every new
  pure-TS test copies this harness header.
- **UI `ui/src/components/settings/SettingsView.tsx:42-95,143-199`** — tabbed shell;
  a new tab is a `Tab` union member + a `TABS` entry + a render branch. The
  Calibration panel registers here.
- **`ui/src/store.ts:1199-1200`** — `showToast(level, message)` wraps
  `get().enqueueToast({...})`; client feedback goes through this.

### 1.3 Approach

**Data model (pure).** A `CalKey` is the 7-tuple index key derived from a FITS
header. For grouping-into-a-master, temperature is *quantized* to a bin
(`temp_bin`, default 5 °C); for matching-a-light, the master's *representative*
temperature is compared to the light's target temp within a °C tolerance. Filter
is `""` for Dark/Bias (shutter closed, mono-agnostic). Bias exposure is folded
to `0.0`.

**Matcher (pure).** Given a `LightNeed(exposure_s, gain, offset, temp_c, binning,
filter)` and the master list, a light is *covered* when both:
- a **DARK** master matches: `gain`/`offset`/`binning` exact, `exposure_s` within
  `±exposure_tol_pct`, `temp_c` within `±temp_tol_c` (temp skipped when either
  side is `None`), **and**
- a **FLAT** master matches: `filter`/`binning`/`gain` exact (flat exposure is
  auto-solved to ADU, so exposure/temp are not constraints).

Bias is *optional* in v1 (not required for coverage). `coverage_for(needs,
masters, tol)` returns a `Gap` per uncovered need listing which of `dark`/`flat`
is missing. **Empty library ⇒ no gaps** (mirrors the horizon check's "don't warn
from an untrustworthy/empty state" — a user who never built masters isn't
nagged).

**Stacker (pure numeric, bounded memory).** `sigma_clip_mean(frames, sigma)`
computes a per-pixel robust center (median), rejects values beyond `k·MAD-sigma`,
and returns the mean of survivors (median fallback where all rejected), as
`float32`. `median_stack` is the cheap path (Bias / tiny stacks). The pure core
operates on in-memory arrays (fully tested for numeric correctness). The
**bounded-memory wrapper** `build_master_streamed` memmaps each source FITS,
iterates row-strips (`strip_rows` high), and calls the pure stacker per strip so
peak memory is `strip_rows × width × n_frames × 4 B` — independent of full-frame
size. Frame count is capped (`max_frames`, evenly sampled) so a 500-dark folder
can't OOM the Pi.

**Library (I/O).** `CalibrationLibrary(capture_dir=lambda: hub.CAPTURE_DIR)`:
- `scan_raw()` walks `CAPTURE_DIR` (excluding `_masters/`, `_solve/`), reads each
  `*.fits` header via `fits.getheader`, keeps only `IMAGETYP ∈ {DARK,BIAS,FLAT}`,
  and buckets paths by `key_index_id(key, temp_bin_width)`.
- `build()` stacks each bucket → `_masters/<key_id>.fits` (float32, header carries
  the key + `NFRAMES` + `MASTER=T`), rewrites `_masters/masters.json` (atomic).
- `list_masters()` reads the manifest → `MasterRecord[]`.
- `delete(id)` removes the master FITS + manifest row (id-safe path).
- `coverage(plan, tol, temp_bin_width)` derives light needs from the plan +
  `cool_to` and calls the pure `coverage_for`.

**Behavior / placement.**
- Backend: new package `server/astrodeck/calibration/` (keys, stacker, matcher,
  library). Config: `CalibrationConfig` appended to `AppConfig`. Routes:
  `GET/POST /api/calibration/masters`, `POST /api/calibration/build`,
  `DELETE /api/calibration/masters/{id}` (mirroring the plans block); coverage
  folded into `sequence_preflight_plan`.
- Frontend: pure `ui/src/lib/calibrationLibrary.ts` (row formatting + a
  client-side coverage mirror for the live pre-flight row); thin
  `CalibrationLibraryPanel.tsx` registered as a new Settings **Calibration** tab;
  a `calibration` row wired into `buildPreflight` (fed a fetched masters list).

**Why the coverage rule is mirrored in TS.** The authoritative matcher is the
Python core (used by the library + the server route, fully pytest'd). The
`PreflightModal` re-checks *live and synchronously* as the user edits the plan
(`buildPreflight` is pure client-side), so a thin ~12-line TS coverage predicate
(tsx-tested against the same tolerance cases) mirrors the Python rule rather than
round-tripping the server on every keystroke. This parallel is idiomatic here
(the codebase already parallels altitude/naming logic across Py/TS); Open
Decision D3 records the trade-off.

---

## 2. Global Constraints (verbatim — apply to every task)

- **Privacy.** The real observing coordinates **[SITE-LAT] / [SITE-LON]** and the
  label **"[SITE-LABEL]"** must **NEVER** appear in code, tests, or docs. The site
  default is **"My Observatory"** / **0.0**. (Calibration masters carry no RA/Dec
  by construction — darks/bias/flats have no pointing — so this feature adds no
  coordinate surface, but the rule stands for any fixture/sample.)
- **Never** `git add -A`. Stage explicit paths only.
- **UI gate:** `cd ui && npx tsc -b` must pass.
- **NO jsdom.** Pure logic is tested via `npx tsx` inline-assert (idiom:
  `ui/src/lib/__tests__/eta.test.ts`). No React-DOM rendering in tests.
- **Backend tests:** `server/.venv/Scripts/pytest.exe`, run from the repo root,
  **`-n0`** single-worker (the repo default is `-n 12`; override per task).
- **Client toasts** via `useStore.getState().enqueueToast` (the
  `showToast(level, msg)` wrapper at `store.ts:1199` routes through it).
- **Honest-disabled (§11.8):** a control the user lacks the capability for is
  **dimmed + locked + `aria-disabled` + `title`**, never a native `disabled`
  attribute.
- **Do not disrupt astrotown** (the deployed box): no behavior change to capture,
  no migration of existing files, no writes outside `CAPTURE_DIR/_masters` and
  the config block.

---

## 3. TDD Plan

Package layout:

```
server/astrodeck/calibration/
  __init__.py      # re-exports the public surface
  keys.py          # CalKey, temp_bin, key_from_header, key_index_id
  stacker.py       # sigma_clip_mean, median_stack, stack_frames
  matcher.py       # LightNeed, MasterRecord, MatchTolerance, Gap, matching + coverage
  library.py       # CalibrationLibrary, build_master_streamed, BuildReport
server/tests/
  test_calibration_keys.py
  test_calibration_stacker.py
  test_calibration_matcher.py
  test_calibration_library.py
  test_calibration_api.py
ui/src/lib/calibrationLibrary.ts
ui/src/lib/__tests__/calibrationLibrary.test.ts
ui/src/components/settings/CalibrationLibraryPanel.tsx
```

### Interfaces (exact signatures — the contract every task honors)

```python
# server/astrodeck/calibration/keys.py
from dataclasses import dataclass
from typing import Mapping

CAL_FRAME_TYPES: frozenset[str] = frozenset({"DARK", "BIAS", "FLAT"})

@dataclass(frozen=True)
class CalKey:
    frame_type: str        # "DARK" | "BIAS" | "FLAT" (upper-cased)
    exposure_s: float      # rounded to 3 dp; 0.0 for BIAS
    gain: int
    offset: int
    temp_c: float | None   # sensor temp from CCD-TEMP; None if absent
    binning: int
    filter: str            # "" for DARK/BIAS

def temp_bin(temp_c: float | None, width: float) -> float | None:
    """Quantize temp to the nearest `width`-degree bin CENTER. None passthrough.
    width <= 0 disables binning (returns temp_c unchanged)."""

def key_from_header(header: Mapping) -> CalKey | None:
    """CalKey from a FITS header, or None when IMAGETYP is absent/not a cal type.
    Case/space-insensitive IMAGETYP ('Dark Frame' -> 'DARK'). Missing GAIN/OFFSET
    default 0; missing XBINNING -> 1; BIAS folds exposure to 0.0."""

def key_index_id(key: CalKey, temp_bin_width: float) -> str:
    """Stable filesystem-safe id for a key's BUCKET (temp binned). e.g.
    'dark_e300.000_g100_o30_t-10_b1' / 'flat_g100_o30_tNA_b1_fHa'. Exposure/temp
    omitted where irrelevant (BIAS: no exp; FLAT: no exp/temp)."""
```

```python
# server/astrodeck/calibration/stacker.py
import numpy as np

DEFAULT_SIGMA: float = 3.0

def median_stack(frames: list[np.ndarray]) -> np.ndarray:
    """Per-pixel median over a uniform-shape stack. Returns float32."""

def sigma_clip_mean(frames: list[np.ndarray], sigma: float = DEFAULT_SIGMA) -> np.ndarray:
    """Per-pixel sigma-clipped mean: one rejection pass at |x - median| >
    sigma * (1.4826 * MAD); mean of survivors, median where all rejected.
    Returns float32. len<3 degrades to median_stack (clipping is meaningless)."""

def stack_frames(frames: list[np.ndarray], method: str = "sigma_clip",
                 sigma: float = DEFAULT_SIGMA) -> np.ndarray:
    """Dispatch median|sigma_clip. Raises ValueError on empty or ragged shapes."""
```

```python
# server/astrodeck/calibration/matcher.py
from dataclasses import dataclass

@dataclass(frozen=True)
class MatchTolerance:
    exposure_tol_pct: float = 5.0
    temp_tol_c: float = 2.0

@dataclass(frozen=True)
class LightNeed:
    exposure_s: float
    gain: int
    offset: int
    temp_c: float | None
    binning: int
    filter: str

@dataclass(frozen=True)
class MasterRecord:
    id: str
    frame_type: str        # DARK|BIAS|FLAT
    exposure_s: float
    gain: int
    offset: int
    temp_c: float | None
    binning: int
    filter: str
    frame_count: int
    path: str
    built_ts: float

@dataclass(frozen=True)
class Gap:
    filter: str
    exposure_s: float
    gain: int
    binning: int
    missing: tuple[str, ...]   # subset of ("dark", "flat")

def dark_matches(need: LightNeed, m: MasterRecord, tol: MatchTolerance) -> bool
def flat_matches(need: LightNeed, m: MasterRecord, tol: MatchTolerance) -> bool
def best_master(need: LightNeed, masters: list[MasterRecord], tol: MatchTolerance,
                frame_type: str) -> MasterRecord | None
    """Closest matching master of `frame_type` (DARK ranked by |Δexposure| then
    |Δtemp|, most frames as tiebreak), or None."""
def coverage_for(needs: list[LightNeed], masters: list[MasterRecord],
                 tol: MatchTolerance) -> list[Gap]
    """One Gap per uncovered need. [] when masters is empty (no nag)."""
```

```python
# server/astrodeck/calibration/library.py
from pathlib import Path
from typing import Callable
from .matcher import MasterRecord, MatchTolerance, Gap

@dataclass(frozen=True)
class BuildReport:
    masters_built: int
    frames_indexed: int
    buckets: int

class CalibrationLibrary:
    def __init__(self, capture_dir: Callable[[], Path]): ...
    def masters_dir(self) -> Path                # <capture>/_masters
    def list_masters(self) -> list[MasterRecord] # from manifest
    def scan_raw(self, temp_bin_width: float) -> dict[str, list[Path]]
    def build(self, *, sigma: float = 3.0, temp_bin_width: float = 5.0,
              max_frames: int = 100, strip_rows: int = 64) -> BuildReport
    def delete(self, master_id: str) -> None     # id-safe; KeyError on escape
    def coverage(self, plan, tol: MatchTolerance, temp_bin_width: float) -> list[Gap]

def build_master_streamed(paths: list[Path], out_path: Path, *, method: str,
                          sigma: float, max_frames: int, strip_rows: int,
                          key, frame_count_hint: int | None = None) -> int
    """Memmap sources, stack per row-strip (bounded memory), write float32 master
    with the key baked into the header. Returns frames used."""
```

```typescript
// ui/src/lib/calibrationLibrary.ts
import type { FrameType } from "./calibration";
export interface MasterRow {
  id: string; frame_type: FrameType; exposure_s: number; gain: number;
  offset: number; temp_c: number | null; binning: number; filter: string;
  frame_count: number; built_ts: number;
}
export interface CoverageTol { exposureTolPct: number; tempTolC: number; }
export function masterRowSummary(m: MasterRow): string;
export function groupMasters(rows: MasterRow[]): { type: FrameType; rows: MasterRow[] }[];
export function planCalibrationGaps(plan: SequencePlan, masters: MasterRow[],
                                    tol: CoverageTol): { missing: string[] };
```

---

### Task 1 — `keys.py` + tests  *(impl tier: Sonnet — mechanical extraction/quantization)*

**Files:** `server/astrodeck/calibration/keys.py`,
`server/astrodeck/calibration/__init__.py`,
`server/tests/test_calibration_keys.py`.

**Step 1.1** Write `keys.py`. Core logic:

```python
"""PRO-1 calibration index keys — pure header → key extraction (no I/O)."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Mapping

CAL_FRAME_TYPES = frozenset({"DARK", "BIAS", "FLAT"})

# IMAGETYP normalization: NINA/ASCOM write "Dark", "Dark Frame", "DARK",
# "Bias Frame", "Flat", "FlatField", etc. Map the leading word to our enum.
def _norm_imagetyp(raw: str) -> str | None:
    t = "".join(ch for ch in str(raw).upper() if ch.isalpha() or ch.isspace()).strip()
    if not t:
        return None
    head = t.split()[0]
    if head in ("DARK",): return "DARK"
    if head in ("BIAS", "ZERO"): return "BIAS"
    if head in ("FLAT", "FLATFIELD"): return "FLAT"
    return None

@dataclass(frozen=True)
class CalKey:
    frame_type: str
    exposure_s: float
    gain: int
    offset: int
    temp_c: float | None
    binning: int
    filter: str

def temp_bin(temp_c: float | None, width: float) -> float | None:
    if temp_c is None or width <= 0:
        return temp_c
    return round(round(temp_c / width) * width, 3)

def key_from_header(header: Mapping) -> CalKey | None:
    ftype = _norm_imagetyp(header.get("IMAGETYP", ""))
    if ftype is None:
        return None
    exp = 0.0 if ftype == "BIAS" else round(float(header.get("EXPTIME", 0.0) or 0.0), 3)
    ccd = header.get("CCD-TEMP", None)
    temp = None if ccd is None else round(float(ccd), 3)
    filt = "" if ftype in ("DARK", "BIAS") else str(header.get("FILTER", "") or "")
    return CalKey(
        frame_type=ftype, exposure_s=exp,
        gain=int(header.get("GAIN", 0) or 0), offset=int(header.get("OFFSET", 0) or 0),
        temp_c=temp, binning=int(header.get("XBINNING", 1) or 1), filter=filt)

def key_index_id(key: CalKey, temp_bin_width: float) -> str:
    tb = temp_bin(key.temp_c, temp_bin_width)
    ts = "NA" if tb is None else f"{tb:g}"
    parts = [key.frame_type.lower()]
    if key.frame_type == "DARK":
        parts += [f"e{key.exposure_s:.3f}", f"g{key.gain}", f"o{key.offset}", f"t{ts}", f"b{key.binning}"]
    elif key.frame_type == "BIAS":
        parts += [f"g{key.gain}", f"o{key.offset}", f"t{ts}", f"b{key.binning}"]
    else:  # FLAT — filter + binning + gain; exposure/temp not identity
        parts += [f"g{key.gain}", f"o{key.offset}", f"b{key.binning}", f"f{key.filter or 'none'}"]
    return "_".join(parts)
```

Add `__init__.py` re-exporting `CalKey, key_from_header, temp_bin, key_index_id,
CAL_FRAME_TYPES`.

**Step 1.2** Write `test_calibration_keys.py`:

```python
from astrodeck.calibration.keys import (
    CalKey, key_from_header, temp_bin, key_index_id, CAL_FRAME_TYPES)

def test_light_header_is_not_a_cal_key():
    assert key_from_header({"IMAGETYP": "Light", "EXPTIME": 300}) is None

def test_dark_key_extraction():
    k = key_from_header({"IMAGETYP": "Dark Frame", "EXPTIME": 300.0, "GAIN": 100,
                         "OFFSET": 30, "CCD-TEMP": -10.0, "XBINNING": 1})
    assert k == CalKey("DARK", 300.0, 100, 30, -10.0, 1, "")

def test_bias_folds_exposure_to_zero_and_drops_filter():
    k = key_from_header({"IMAGETYP": "Bias", "EXPTIME": 0.001, "FILTER": "Ha",
                         "GAIN": 100, "OFFSET": 30, "XBINNING": 1})
    assert k.frame_type == "BIAS" and k.exposure_s == 0.0 and k.filter == ""

def test_flat_keeps_filter():
    k = key_from_header({"IMAGETYP": "FlatField", "EXPTIME": 2.5, "FILTER": "Ha",
                         "GAIN": 100, "XBINNING": 2})
    assert k.frame_type == "FLAT" and k.filter == "Ha" and k.binning == 2

def test_missing_ccdtemp_is_none():
    assert key_from_header({"IMAGETYP": "Dark", "EXPTIME": 1}).temp_c is None

def test_temp_bin_quantizes_to_center():
    assert temp_bin(-9.3, 5.0) == -10.0
    assert temp_bin(-7.4, 5.0) == -5.0
    assert temp_bin(None, 5.0) is None
    assert temp_bin(-9.3, 0) == -9.3          # disabled

def test_index_id_buckets_by_binned_temp():
    a = key_from_header({"IMAGETYP": "Dark", "EXPTIME": 300, "GAIN": 100,
                         "OFFSET": 30, "CCD-TEMP": -9.3, "XBINNING": 1})
    b = key_from_header({"IMAGETYP": "Dark", "EXPTIME": 300, "GAIN": 100,
                         "OFFSET": 30, "CCD-TEMP": -10.7, "XBINNING": 1})
    assert key_index_id(a, 5.0) == key_index_id(b, 5.0)   # same 5C bucket

def test_index_id_flat_ignores_exposure():
    a = key_from_header({"IMAGETYP": "Flat", "EXPTIME": 2.0, "FILTER": "Ha", "GAIN": 100})
    b = key_from_header({"IMAGETYP": "Flat", "EXPTIME": 3.5, "FILTER": "Ha", "GAIN": 100})
    assert key_index_id(a, 5.0) == key_index_id(b, 5.0)
```

**Run:** `server/.venv/Scripts/pytest.exe server/tests/test_calibration_keys.py -n0 -q`
→ **`8 passed`**.

---

### Task 2 — `stacker.py` + tests  *(impl tier: **Opus** — numeric correctness of sigma-clip + float32/nan handling is genuinely subtle; a wrong rejection makes silently-bad masters)*

**Files:** `server/astrodeck/calibration/stacker.py`,
`server/tests/test_calibration_stacker.py`; extend `__init__.py`.

**Step 2.1** Write `stacker.py`:

```python
"""PRO-1 master stacking — pure numpy reduction (no I/O). Operates on in-memory
frame lists; the bounded-memory streaming wrapper lives in library.py."""
from __future__ import annotations
import numpy as np

DEFAULT_SIGMA = 3.0

def _as_stack(frames: list[np.ndarray]) -> np.ndarray:
    if not frames:
        raise ValueError("stack needs at least one frame")
    shapes = {f.shape for f in frames}
    if len(shapes) != 1:
        raise ValueError(f"ragged stack: shapes {sorted(shapes)}")
    return np.stack([np.asarray(f, dtype=np.float32) for f in frames], axis=0)

def median_stack(frames: list[np.ndarray]) -> np.ndarray:
    return np.median(_as_stack(frames), axis=0).astype(np.float32)

def sigma_clip_mean(frames: list[np.ndarray], sigma: float = DEFAULT_SIGMA) -> np.ndarray:
    stack = _as_stack(frames)
    if stack.shape[0] < 3:
        return np.median(stack, axis=0).astype(np.float32)
    med = np.median(stack, axis=0)                               # (H,W) robust center
    mad = np.median(np.abs(stack - med), axis=0) * 1.4826        # (H,W) robust sigma
    lo = med - sigma * mad
    hi = med + sigma * mad
    keep = (stack >= lo) & (stack <= hi)                         # (N,H,W)
    ssum = np.where(keep, stack, 0.0).sum(axis=0)
    cnt = keep.sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = ssum / cnt
    # a pixel whose values are all rejected (cnt==0, only when mad==0 edge) -> median
    out = np.where(cnt > 0, mean, med)
    return out.astype(np.float32)

def stack_frames(frames: list[np.ndarray], method: str = "sigma_clip",
                 sigma: float = DEFAULT_SIGMA) -> np.ndarray:
    if method == "median":
        return median_stack(frames)
    if method == "sigma_clip":
        return sigma_clip_mean(frames, sigma)
    raise ValueError(f"unknown stack method: {method!r}")
```

**Step 2.2** Write `test_calibration_stacker.py`:

```python
import numpy as np, pytest
from astrodeck.calibration.stacker import median_stack, sigma_clip_mean, stack_frames

def test_median_of_constant_stack():
    fs = [np.full((4, 4), 100, np.uint16) for _ in range(5)]
    assert np.allclose(median_stack(fs), 100.0)

def test_sigma_clip_rejects_a_cosmic_ray():
    # 8 frames at 100, one pixel spiked to 60000 in one frame -> master ~100.
    fs = [np.full((3, 3), 100.0, np.float32) for _ in range(8)]
    fs[0][1, 1] = 60000.0
    out = sigma_clip_mean(fs, sigma=3.0)
    assert abs(out[1, 1] - 100.0) < 1.0            # spike rejected
    assert out.dtype == np.float32

def test_sigma_clip_keeps_real_signal():
    rng = np.random.default_rng(7)
    fs = [rng.normal(200.0, 5.0, (8, 8)).astype(np.float32) for _ in range(10)]
    out = sigma_clip_mean(fs, sigma=3.0)
    assert abs(float(out.mean()) - 200.0) < 2.0

def test_small_stack_degrades_to_median():
    fs = [np.full((2, 2), 10.0, np.float32), np.full((2, 2), 20.0, np.float32)]
    assert np.allclose(sigma_clip_mean(fs), np.median(np.stack(fs), 0))

def test_all_rejected_pixel_falls_back_to_median():
    # zero-MAD column forces cnt==0 path for a lone deviant -> median, no nan.
    fs = [np.full((1, 1), 5.0, np.float32) for _ in range(4)]
    fs[0][0, 0] = 5.0  # identical -> mad 0; ensure no nan escapes
    out = sigma_clip_mean(fs)
    assert np.isfinite(out).all()

def test_empty_and_ragged_raise():
    with pytest.raises(ValueError): stack_frames([])
    with pytest.raises(ValueError):
        stack_frames([np.zeros((2, 2)), np.zeros((3, 3))])
```

**Run:** `server/.venv/Scripts/pytest.exe server/tests/test_calibration_stacker.py -n0 -q`
→ **`6 passed`**.

---

### Task 3 — `matcher.py` + tests  *(impl tier: Sonnet — predicate + tolerance; well-specified. Opus not warranted)*

**Files:** `server/astrodeck/calibration/matcher.py`,
`server/tests/test_calibration_matcher.py`; extend `__init__.py`.

**Step 3.1** Write `matcher.py` per the Interfaces block. Key predicates:

```python
def _within_pct(a: float, b: float, pct: float) -> bool:
    if b == 0:
        return a == 0
    return abs(a - b) <= abs(b) * (pct / 100.0)

def _temp_ok(need_t, master_t, tol_c: float) -> bool:
    if need_t is None or master_t is None:
        return True                       # temp not a constraint when unknown
    return abs(need_t - master_t) <= tol_c

def dark_matches(need, m, tol):
    return (m.frame_type == "DARK" and m.gain == need.gain and m.offset == need.offset
            and m.binning == need.binning
            and _within_pct(need.exposure_s, m.exposure_s, tol.exposure_tol_pct)
            and _temp_ok(need.temp_c, m.temp_c, tol.temp_tol_c))

def flat_matches(need, m, tol):
    return (m.frame_type == "FLAT" and m.filter == need.filter
            and m.binning == need.binning and m.gain == need.gain)

def best_master(need, masters, tol, frame_type):
    pred = {"DARK": dark_matches, "FLAT": flat_matches}[frame_type]
    cands = [m for m in masters if pred(need, m, tol)]
    if not cands:
        return None
    def rank(m):
        dexp = abs(need.exposure_s - m.exposure_s)
        dtemp = 0.0 if (need.temp_c is None or m.temp_c is None) else abs(need.temp_c - m.temp_c)
        return (dexp, dtemp, -m.frame_count)
    return sorted(cands, key=rank)[0]

def coverage_for(needs, masters, tol):
    if not masters:
        return []                          # empty library never nags
    gaps = []
    for n in needs:
        missing = []
        if best_master(n, masters, tol, "DARK") is None: missing.append("dark")
        if best_master(n, masters, tol, "FLAT") is None: missing.append("flat")
        if missing:
            gaps.append(Gap(filter=n.filter, exposure_s=n.exposure_s, gain=n.gain,
                            binning=n.binning, missing=tuple(missing)))
    return gaps
```

**Step 3.2** Write `test_calibration_matcher.py` (representative cases):

```python
from astrodeck.calibration.matcher import (
    LightNeed, MasterRecord, MatchTolerance, Gap,
    dark_matches, flat_matches, best_master, coverage_for)

TOL = MatchTolerance(exposure_tol_pct=5.0, temp_tol_c=2.0)
def dark(exp, g=100, o=30, t=-10.0, b=1, n=20, id="d"):
    return MasterRecord(id, "DARK", exp, g, o, t, b, "", n, "/m/"+id, 0.0)
def flat(filt, g=100, o=30, b=1, n=20, id="f"):
    return MasterRecord(id, "FLAT", 2.0, g, o, -10.0, b, filt, n, "/m/"+id, 0.0)
def need(exp=300, g=100, o=30, t=-10.0, b=1, filt="Ha"):
    return LightNeed(exp, g, o, t, b, filt)

def test_dark_matches_within_exposure_tolerance():
    assert dark_matches(need(300), dark(307), TOL)      # +2.3% within 5%
    assert not dark_matches(need(300), dark(400), TOL)  # +33% out

def test_dark_temp_out_of_tolerance_fails():
    assert not dark_matches(need(300, t=-10.0), dark(300, t=-15.0), TOL)

def test_dark_temp_unknown_is_not_a_constraint():
    assert dark_matches(need(300, t=None), dark(300, t=-40.0), TOL)

def test_gain_offset_binning_are_exact():
    assert not dark_matches(need(300, g=200), dark(300, g=100), TOL)
    assert not dark_matches(need(300, b=2), dark(300, b=1), TOL)

def test_flat_matches_filter_and_binning():
    assert flat_matches(need(filt="Ha"), flat("Ha"), TOL)
    assert not flat_matches(need(filt="OIII"), flat("Ha"), TOL)

def test_best_master_prefers_closest_exposure_then_most_frames():
    best = best_master(need(300), [dark(310, n=5, id="far"),
                                   dark(302, n=5, id="near"),
                                   dark(302, n=40, id="deep")], TOL, "DARK")
    assert best.id == "deep"                # equal Δexp, more frames wins

def test_coverage_reports_missing_dark_and_flat():
    gaps = coverage_for([need(300, filt="Ha")], [flat("OIII")], TOL)
    assert gaps == [Gap("Ha", 300.0, 100, 1, ("dark", "flat"))]

def test_coverage_empty_when_all_covered():
    assert coverage_for([need(300, filt="Ha")], [dark(300), flat("Ha")], TOL) == []

def test_coverage_empty_library_never_nags():
    assert coverage_for([need(300)], [], TOL) == []
```

**Run:** `server/.venv/Scripts/pytest.exe server/tests/test_calibration_matcher.py -n0 -q`
→ **`9 passed`**.

---

### Task 4 — `library.py` (scan + streamed build + manifest + coverage) + tests  *(impl tier: **Opus** — memmap row-strip streaming, FITS round-trip, live-CAPTURE_DIR resolution, and equivalence-to-pure-stacker are subtle; a strip-boundary bug corrupts masters silently)*

**Files:** `server/astrodeck/calibration/library.py`,
`server/tests/test_calibration_library.py`; extend `__init__.py`.

**Step 4.1** Write `library.py`. Skeleton (grounded in `plans.py` + `persist.py`
+ `hub._counter_file`):

```python
"""PRO-1 master calibration library — scans CAPTURE_DIR, builds masters, matches."""
from __future__ import annotations
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from ..persist import read_json_or, write_json_atomic, safe_id_path
from .keys import key_from_header, key_index_id, CAL_FRAME_TYPES
from .matcher import (LightNeed, MasterRecord, MatchTolerance, Gap, coverage_for)
from .stacker import stack_frames

MASTERS_DIRNAME = "_masters"
MANIFEST_NAME = "masters.json"
EXCLUDE_DIRS = {MASTERS_DIRNAME, "_solve"}
MANIFEST_SCHEMA = 1

@dataclass(frozen=True)
class BuildReport:
    masters_built: int
    frames_indexed: int
    buckets: int

def _write_master_fits(data: np.ndarray, out_path: Path, key, frame_count: int) -> None:
    from astropy.io import fits           # lazy (hub precedent)
    hdu = fits.PrimaryHDU(data.astype(np.float32))
    h = hdu.header
    h["IMAGETYP"] = f"Master {key.frame_type.title()}"
    h["EXPTIME"] = key.exposure_s
    h["GAIN"] = key.gain
    h["OFFSET"] = key.offset
    if key.temp_c is not None: h["CCD-TEMP"] = key.temp_c
    h["XBINNING"] = key.binning
    h["YBINNING"] = key.binning
    if key.filter: h["FILTER"] = key.filter
    h["NFRAMES"] = (frame_count, "source frames stacked")
    h["MASTER"] = (True, "AstroDeck master calibration frame")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    hdu.writeto(out_path, overwrite=True)

def build_master_streamed(paths, out_path, *, method, sigma, max_frames,
                          strip_rows, key, frame_count_hint=None) -> int:
    from astropy.io import fits
    use = paths if len(paths) <= max_frames else \
        [paths[i] for i in np.linspace(0, len(paths) - 1, max_frames).astype(int)]
    hduls = [fits.open(p, memmap=True) for p in use]
    try:
        h, w = hduls[0][0].data.shape
        out = np.empty((h, w), dtype=np.float32)
        for y0 in range(0, h, strip_rows):
            y1 = min(y0 + strip_rows, h)
            strips = [np.asarray(hd[0].data[y0:y1], dtype=np.float32) for hd in hduls]
            out[y0:y1] = stack_frames(strips, method=method, sigma=sigma)
    finally:
        for hd in hduls: hd.close()
    _write_master_fits(out, out_path, key, len(use))
    return len(use)

class CalibrationLibrary:
    def __init__(self, capture_dir: Callable[[], Path]):
        self._capture_dir = capture_dir

    def masters_dir(self) -> Path:
        return self._capture_dir() / MASTERS_DIRNAME
    def _manifest_path(self) -> Path:
        return self.masters_dir() / MANIFEST_NAME

    def scan_raw(self, temp_bin_width: float) -> dict[str, list[Path]]:
        from astropy.io import fits
        root = self._capture_dir()
        buckets: dict[str, tuple] = {}     # id -> (key, [paths])
        for p in root.rglob("*.fits"):
            if any(part in EXCLUDE_DIRS for part in p.relative_to(root).parts):
                continue
            try:
                key = key_from_header(fits.getheader(p))
            except Exception:
                continue
            if key is None or key.frame_type not in CAL_FRAME_TYPES:
                continue
            kid = key_index_id(key, temp_bin_width)
            buckets.setdefault(kid, (key, []))[1].append(p)
        return {kid: paths for kid, (key, paths) in buckets.items()}, \
               {kid: key for kid, (key, _) in buckets.items()}   # see note

    def build(self, *, sigma=3.0, temp_bin_width=5.0, max_frames=100, strip_rows=64):
        paths_by_id, key_by_id = self.scan_raw(temp_bin_width)
        records, indexed = [], 0
        for kid, paths in paths_by_id.items():
            key = key_by_id[kid]
            out = self.masters_dir() / f"{kid}.fits"
            method = "median" if key.frame_type == "BIAS" else "sigma_clip"
            n = build_master_streamed(paths, out, method=method, sigma=sigma,
                                      max_frames=max_frames, strip_rows=strip_rows, key=key)
            indexed += len(paths)
            records.append(MasterRecord(
                id=kid, frame_type=key.frame_type, exposure_s=key.exposure_s,
                gain=key.gain, offset=key.offset, temp_c=key.temp_c,
                binning=key.binning, filter=key.filter, frame_count=n,
                path=str(out), built_ts=time.time()))
        self._save_manifest(records)
        return BuildReport(len(records), indexed, len(paths_by_id))

    def list_masters(self):
        raw = read_json_or(self._manifest_path(), {})
        rows = raw.get("masters", []) if isinstance(raw, dict) else []
        return [MasterRecord(**r) for r in rows if _valid_row(r)]

    def _save_manifest(self, records):
        write_json_atomic(self._manifest_path(), {
            "schema_version": MANIFEST_SCHEMA,
            "masters": [vars(r) for r in records]})

    def delete(self, master_id: str) -> None:
        path = safe_id_path(self.masters_dir(), master_id, ".fits")  # KeyError on escape
        if path.exists(): path.unlink()
        self._save_manifest([m for m in self.list_masters() if m.id != master_id])

    def coverage(self, plan, tol: MatchTolerance, temp_bin_width: float):
        masters = self.list_masters()
        needs = _plan_needs(plan)
        return coverage_for(needs, masters, tol)
```

`_plan_needs(plan)` derives a de-duplicated `LightNeed` per distinct
`(exposure, gain, offset, binning, filter)` across all **non-calibration**
targets, `temp_c = plan.cool_to`. `_valid_row` guards a malformed manifest row.
*(Implementation note: refactor `scan_raw` to return a single dict keyed to a
small `(key, paths)` struct — the two-dict return above is shorthand for the
spec; the task builds one clean structure.)*

**Step 4.2** Write `test_calibration_library.py`. Use `save_fits` + a synthetic
`CameraFrame` (idiom `test_fitsio.py:15-25`) to lay down raw frames, monkeypatch
the capture dir via the injected getter, build, and assert:

```python
import numpy as np
from pathlib import Path
from astropy.io import fits
from astrodeck.devices.base import CameraFrame
from astrodeck.imaging.fitsio import save_fits
from astrodeck.calibration.library import CalibrationLibrary
from astrodeck.calibration.matcher import MatchTolerance, LightNeed
from astrodeck.calibration.stacker import sigma_clip_mean

def _dark(tmp, i, exp=300.0, temp=-10.0):
    f = CameraFrame(data=np.full((32, 24), 100 + i, np.uint16), exposure_s=exp,
                    gain=100, offset=30, binning=1, bayer_pattern=None,
                    temperature_c=temp, timestamp=1_772_000_000.0 + i)
    return save_fits(f, tmp / f"dark_{i}.fits", frame_type="Dark")

def test_build_creates_one_master_per_bucket(tmp_path):
    for i in range(5): _dark(tmp_path, i)
    lib = CalibrationLibrary(lambda: tmp_path)
    rep = lib.build(temp_bin_width=5.0)
    assert rep.masters_built == 1 and rep.frames_indexed == 5
    masters = lib.list_masters()
    assert len(masters) == 1 and masters[0].frame_type == "DARK"
    assert masters[0].frame_count == 5

def test_streamed_build_equals_in_memory_stack(tmp_path):
    arrs = [np.random.default_rng(i).integers(90, 110, (40, 24), np.uint16) for i in range(6)]
    for i, a in enumerate(arrs):
        save_fits(CameraFrame(a, 300.0, 100, 30, 1, None, -10.0, 1.0 + i),
                  tmp_path / f"d{i}.fits", frame_type="Dark")
    lib = CalibrationLibrary(lambda: tmp_path)
    lib.build(temp_bin_width=5.0, strip_rows=7)          # strip does not divide 40
    m = lib.list_masters()[0]
    got = fits.getdata(m.path)
    want = sigma_clip_mean(arrs)                          # pure reference
    assert np.allclose(got, want, atol=1e-3)

def test_masters_dir_and_solve_are_excluded(tmp_path):
    _dark(tmp_path, 0)
    lib = CalibrationLibrary(lambda: tmp_path); lib.build()
    # a second build must not re-index the master it just wrote under _masters/
    rep = lib.build()
    assert rep.frames_indexed == 1

def test_delete_removes_fits_and_row(tmp_path):
    _dark(tmp_path, 0)
    lib = CalibrationLibrary(lambda: tmp_path); lib.build()
    mid = lib.list_masters()[0].id
    lib.delete(mid)
    assert lib.list_masters() == []
    assert not (tmp_path / "_masters" / f"{mid}.fits").exists()

def test_flat_bucket_ignores_exposure(tmp_path):
    for i, exp in enumerate((2.0, 3.5, 2.8)):
        save_fits(CameraFrame(np.full((16, 16), 30000, np.uint16), exp, 100, 30, 1,
                              None, -10.0, 1.0 + i), tmp_path / f"flat_{i}.fits",
                  frame_type="Flat", filter_name="Ha")
    lib = CalibrationLibrary(lambda: tmp_path); rep = lib.build()
    assert rep.masters_built == 1 and lib.list_masters()[0].filter == "Ha"
```

**Run:** `server/.venv/Scripts/pytest.exe server/tests/test_calibration_library.py -n0 -q`
→ **`5 passed`**.

---

### Task 5 — config block + API routes + pre-flight coverage + tests  *(impl tier: Sonnet — mechanical wiring against the plans-route + preflight template)*

**Files:** `server/astrodeck/config.py` (add `CalibrationConfig`, append to
`AppConfig`), `server/astrodeck/api/app.py` (routes + preflight fold-in),
`server/tests/test_calibration_api.py`.

**Step 5.1** In `config.py`, beside `NamingConfig` (367-370), add:

```python
class CalibrationConfig(BaseModel):
    """PRO-1 master-library matching + stacking tolerances (appended — old
    configs load fine)."""
    exposure_tol_pct: float = Field(5.0, ge=0, le=100)
    temp_tol_c: float = Field(2.0, ge=0, le=50)
    temp_bin_c: float = Field(5.0, ge=0, le=50)
    stack_sigma: float = Field(3.0, gt=0, le=10)
    max_stack_frames: int = Field(100, ge=1, le=1000)
```

and in `AppConfig` (after `naming`, ~475):
`calibration: CalibrationConfig = Field(default_factory=CalibrationConfig)`.

**Step 5.2** In `app.py`, near the plans import (71) add
`from ..calibration import CalibrationLibrary` and instantiate a module singleton
in the app factory bound to the **live** capture dir:
`cal_library = CalibrationLibrary(lambda: hub_module.CAPTURE_DIR)` (import
`from .. import hub as hub_module`). Then, mirroring 2173-2204:

```python
@app.get("/api/calibration/masters", dependencies=[Depends(require(CAP_VIEW_STATUS))])
@declare(CAP_VIEW_STATUS)
async def list_masters():
    return [vars(m) for m in await asyncio.to_thread(cal_library.list_masters)]

@app.post("/api/calibration/build", dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
@declare(CAP_CONTROL_CAPTURE)
async def build_masters():
    c = config_store.cfg().calibration
    rep = await asyncio.to_thread(
        cal_library.build, sigma=c.stack_sigma, temp_bin_width=c.temp_bin_c,
        max_frames=c.max_stack_frames)
    return vars(rep)

@app.delete("/api/calibration/masters/{master_id}",
            dependencies=[Depends(require(CAP_CONTROL_CAPTURE))])
@declare(CAP_CONTROL_CAPTURE)
async def delete_master(master_id: str):
    try:
        await asyncio.to_thread(cal_library.delete, master_id)
    except KeyError:
        raise HTTPException(404, "master not found")
    return {"deleted": master_id}
```

**Step 5.3** Fold coverage into `sequence_preflight_plan` (after the altitude
loop, before `return`, at ~3378):

```python
c = cfg.calibration
tol = MatchTolerance(c.exposure_tol_pct, c.temp_tol_c)
gaps = await asyncio.to_thread(cal_library.coverage, plan, tol, c.temp_bin_c)
for g in gaps:
    warnings.append({
        "target": "", "kind": "no_calibration",
        "message": (f"no {' or '.join(g.missing)} master for "
                    f"{g.exposure_s:g}s · gain {g.gain} · bin {g.binning}"
                    + (f" · {g.filter}" if g.filter else ""))})
```

**Step 5.4** Write `test_calibration_api.py` using the existing app test client
fixture (grep `test_app_*` for the `client`/`app` fixture idiom) + a monkeypatched
`hub.CAPTURE_DIR = tmp_path`:

```python
def test_masters_empty_then_build(client, tmp_path, monkeypatch):
    # lay a dark under CAPTURE_DIR (monkeypatched to tmp_path), build, list.
    ...  # save_fits a Dark; POST /api/calibration/build
    r = client.post("/api/calibration/build"); assert r.json()["masters_built"] == 1
    assert len(client.get("/api/calibration/masters").json()) == 1

def test_preflight_warns_missing_calibration(client, tmp_path, monkeypatch):
    # library empty of flats -> a light step warns no_calibration when a dark
    # exists but no flat (empty library suppression covered by matcher test).
    ...
    r = client.post("/api/sequence/preflight", json=plan_with_one_light_step)
    kinds = [w["kind"] for w in r.json()["warnings"]]
    assert "no_calibration" in kinds

def test_delete_bad_id_is_404(client):
    assert client.delete("/api/calibration/masters/..%2Fx").status_code in (404, 400)
```

**Run:** `server/.venv/Scripts/pytest.exe server/tests/test_calibration_api.py -n0 -q`
→ all pass. Then a **regression sweep**:
`server/.venv/Scripts/pytest.exe server/tests -n0 -q` → existing suite still green.

---

### Task 6 — `calibrationLibrary.ts` pure lib + tsx tests  *(impl tier: Sonnet — pure formatting + a mirrored predicate; tsx-tested)*

**Files:** `ui/src/lib/calibrationLibrary.ts`,
`ui/src/lib/__tests__/calibrationLibrary.test.ts`, `ui/src/types.ts` (add
`MasterRow`, `CalibrationBuildReport`).

**Step 6.1** Write `calibrationLibrary.ts`:

```typescript
import type { FrameType } from "./calibration";
import type { SequencePlan } from "../types";

export interface MasterRow {
  id: string; frame_type: FrameType; exposure_s: number; gain: number;
  offset: number; temp_c: number | null; binning: number; filter: string;
  frame_count: number; built_ts: number;
}
export interface CoverageTol { exposureTolPct: number; tempTolC: number; }

export function masterRowSummary(m: MasterRow): string {
  const p: string[] = [];
  if (m.frame_type !== "Bias") p.push(m.exposure_s >= 1 ? `${Math.round(m.exposure_s)}s` : `${m.exposure_s}s`);
  p.push(`gain ${m.gain}`);
  if (m.temp_c != null) p.push(`${m.temp_c}°C`);
  p.push(`bin${m.binning}`);
  if (m.filter) p.push(m.filter);
  p.push(`×${m.frame_count}`);
  return p.join(" · ");
}

export function groupMasters(rows: MasterRow[]): { type: FrameType; rows: MasterRow[] }[] {
  const order: FrameType[] = ["Dark", "Flat", "Bias"];
  return order.map((type) => ({ type, rows: rows.filter((r) => r.frame_type === type) }))
              .filter((g) => g.rows.length > 0);
}

const withinPct = (a: number, b: number, pct: number) =>
  b === 0 ? a === 0 : Math.abs(a - b) <= Math.abs(b) * (pct / 100);

// Mirrors matcher.py dark_matches/flat_matches (Open Decision D3). tsx-tested
// against the same tolerance cases the Python matcher uses.
export function planCalibrationGaps(
  plan: SequencePlan, masters: MasterRow[], tol: CoverageTol,
): { missing: string[] } {
  if (masters.length === 0) return { missing: [] };   // empty library never nags
  const t = plan.cool_to ?? null;
  const needDark = new Set<string>(), needFlat = new Set<string>();
  const haveDark = (n: {exposure_s:number;gain:number;offset:number;binning:number}) =>
    masters.some((m) => m.frame_type === "Dark" && m.gain === n.gain && m.offset === n.offset
      && m.binning === n.binning && withinPct(n.exposure_s, m.exposure_s, tol.exposureTolPct)
      && (t == null || m.temp_c == null || Math.abs(t - m.temp_c) <= tol.tempTolC));
  const haveFlat = (filter: string, gain: number, binning: number) =>
    masters.some((m) => m.frame_type === "Flat" && m.filter === filter
      && m.gain === gain && m.binning === binning);
  for (const tg of plan.targets) {
    if (tg.calibration) continue;
    for (const s of tg.steps) {
      const n = { exposure_s: s.exposure_s, gain: s.gain, offset: s.offset, binning: s.binning };
      if (!haveDark(n)) needDark.add(`${s.exposure_s}/${s.gain}/${s.binning}`);
      if (s.filter && !haveFlat(s.filter, s.gain, s.binning)) needFlat.add(s.filter);
    }
  }
  const missing: string[] = [];
  if (needDark.size) missing.push("darks");
  if (needFlat.size) missing.push(`flats (${[...needFlat].join(", ")})`);
  return { missing };
}
```

**Step 6.2** Write `calibrationLibrary.test.ts` copying the eta.test.ts harness
header; cover `masterRowSummary` (Bias drops exposure), `groupMasters` order/skip,
and `planCalibrationGaps` (covered ⇒ empty; missing flat ⇒ reported; empty
library ⇒ empty; exposure tolerance boundary).

**Run:** `cd ui && npx tsx src/lib/__tests__/calibrationLibrary.test.ts`
→ prints **`N passed, 0 failed`**. Then `cd ui && npx tsc -b` → clean.

---

### Task 7 — `CalibrationLibraryPanel.tsx` + Settings tab + pre-flight row  *(impl tier: Sonnet — thin render verified by tsc; mirrors PlanLibraryPanel + preflight filters row)*

**Files:** `ui/src/components/settings/CalibrationLibraryPanel.tsx`,
`ui/src/components/settings/SettingsView.tsx`, `ui/src/lib/preflight.ts`,
`ui/src/components/PreflightStrip.tsx` (thread masters through `usePreflight`),
`ui/src/store.ts` (a `masters` slice + fetch), `ui/src/api.ts` if a helper is
missing.

**Step 7.1** `CalibrationLibraryPanel.tsx` — model on `PlanLibraryPanel.tsx`:
- `api.get<MasterRow[]>("/api/calibration/masters")` into `rows`, `loadErr`
  tri-state body (PlanLibraryPanel:58-68,312-333).
- Header "Rebuild library" button → `POST /api/calibration/build`; on success
  `showToast("info", \`Built ${rep.masters_built} masters from ${rep.frames_indexed} frames\`)`
  then refresh. Gate with `useCanControlCapture()`; when false, render the button
  **honest-disabled** (§11.8: `aria-disabled`, `title="Building masters needs …"`,
  dim/locked — **not** native `disabled`).
- Body: `groupMasters(rows)` → a section per type; each row shows
  `masterRowSummary(m)` + a delete button (`confirmDialog` → `api.del`).
- Empty state: "No masters yet. Capture darks/flats/bias, then Rebuild."

**Step 7.2** Register in `SettingsView.tsx`: add `"calibration"` to the `Tab`
union (42-50), a `{ value: "calibration", label: "Calibration" }` entry in `TABS`
(67-87), and a render branch `{activeTab === "calibration" && <CalibrationLibraryPanel />}`.

**Step 7.3** Add a `calibration` row to `buildPreflight` (preflight.ts, after the
filters row ~180), signature-extended with an optional `masters: MasterRow[] = []`:

```typescript
// --- calibration (PRO-1) ---
const lightsForCal = lightTargets(plan);
if (masters.length === 0 || lightsForCal.length === 0)
  push("calibration", "Calibration", "skipped");
else {
  const { missing } = planCalibrationGaps(plan, masters, {
    exposureTolPct: 5, tempTolC: 2 });
  if (missing.length === 0) push("calibration", "Calibration", "ok");
  else push("calibration", "Calibration", "warn", {
    detail: { value: `no ${missing.join(" · ")}` } });
}
```

Thread `masters` from the store through `usePreflight` (PreflightStrip). Because
the modal defaults to "Run anyway" past warnings (PreflightModal footer 155-162),
this is non-blocking — a missing master never prevents a run.

**Step 7.4 (gate):** `cd ui && npx tsc -b` → clean. The panel is a thin render;
correctness rides on the Task 6 tsx tests + the typechecker. No jsdom.

---

## 4. Open decisions (each with a recommendation)

- **D1 — Bias as dark-substitute / dark-scaling.** v1 requires an *exposure-matched
  dark* for coverage; it does not synthesize a scaled dark from bias+exposure.
  **Recommend:** ship v1 requiring matched darks (honest, simple); defer
  bias-scaled dark synthesis and flat-dark subtraction to a PRO-1.1 follow-up
  (they need a clean noise model and are easy to get subtly wrong).
- **D2 — Temp binning width (5 °C) and exposure tolerance (5 %).** These control
  how aggressively masters are reused across nights. **Recommend:** defaults
  `temp_bin_c=5`, `temp_tol_c=2`, `exposure_tol_pct=5` (regulated-cooler rigs sit
  within a couple °C night-to-night; 5 % covers 300 s↔315 s rounding). All are
  `CalibrationConfig` fields, so a user can tighten/loosen without a code change.
- **D3 — Matcher duplicated in TS.** The authoritative matcher is Python (library
  + server route, fully pytest'd); the live pre-flight row uses a mirrored
  ~12-line TS predicate (`planCalibrationGaps`, tsx-tested) to stay synchronous
  as the plan is edited. **Recommend:** keep the tested TS mirror rather than a
  per-keystroke server round-trip; the server `POST /api/sequence/preflight`
  remains the authoritative API surface (and a future automation consumer). The
  drift risk is bounded by parallel tolerance-case tests on both sides.
- **D4 — Empty-library suppression.** `coverage_for`/`planCalibrationGaps` return
  no gaps when zero masters exist. **Recommend:** keep it (mirrors the horizon
  check's "no warning from an untrustworthy/empty state" — a user who never built
  masters shouldn't be nagged on every plan). Once ≥1 master exists, specific
  gaps warn.
- **D5 — Panel placement.** A new Settings **Calibration** tab vs. a card on the
  Sequence view. **Recommend:** Settings tab (it is data-ops config, not a
  per-run control), with the coverage feedback surfacing where users act on it —
  the pre-flight modal. Revisit if users want a build button next to the Plan.
- **D6 — Build is a manual action (no incremental/auto-index).** v1 rescans the
  whole `CAPTURE_DIR` on an explicit "Rebuild". **Recommend:** manual for v1
  (predictable, no capture-hot-path coupling, no astrotown behavior change);
  defer incremental indexing (index-on-capture) and a mtime cache to a follow-up
  if full rescans get slow on large libraries.
