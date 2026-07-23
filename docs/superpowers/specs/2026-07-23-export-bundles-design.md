# PRO-10 — Stacker-ready interop export bundles (PixInsight / Siril / APP)

**Feature:** One click on a finished session report produces a **stacking bundle** —
light subs grouped by *target + filter + exposure + gain + binning*, each group
paired with its matched master calibration (from PRO-1's library), plus a
**per-sub weighting manifest** (HFR / eccentricity / guide-RMS / altitude → a
normalized weight) and a layout the user can materialize with a generated
one-click build script. The acquisition→processing handoff is where imagers lose
the most time; this closes it.

Date: 2026-07-23 · Branch: `feat/wave0-remainder` · Depends on: **PRO-1** (matched
masters — design doc absent at time of writing; we design against a documented
`MasterLibrary.match(key) -> path | None` Protocol and ship a null stub so PRO-10
is independently shippable and wires the real library in with one line when PRO-1
lands).

---

## 1. Design

### 1.1 Goal

A pro finishing a night has 40–300 light subs scattered under `captures/…`, plus
(after PRO-1) a library of master darks/flats/bias. Getting from there to a stack
means: (a) sorting lights into per-target/filter/exposure/gain groups, (b) finding
the *right* master for each group, (c) culling/weighting subs by quality. All three
are tedious and error-prone by hand. PRO-10 does all three from the data AstroDeck
**already recorded during the run** (the session report) plus PRO-1's matcher, and
hands back a small, honest bundle: a manifest + a weighting CSV + a build script,
delivered as one `.zip` download from the report viewer.

**v1 scope (shippable, fully tested):** ONE clean grouped layout + the weighting
manifest + the download. Explicitly deferred (see §4): materializing/zipping the
FITS themselves, Siril/APP-specific folder conventions, true FWHM, altitude in the
weight formula.

### 1.2 Current-state seams (all read; file:line)

**Saved frames + naming (where the subs live):**

- `server/astrodeck/hub.py:96-97` — `CAPTURE_DIR` (env `ASTRODECK_CAPTURE_DIR` or
  `<repo>/captures`). Every local sub lives under here.
- `server/astrodeck/hub.py:1772-1792` — `_capture_path(target, frame_type,
  filter_name)` → `CAPTURE_DIR / render_relative_path(template, fields)`; the saved
  FITS path.
- `server/astrodeck/hub.py:1450`, `:1461-1467` — capture builds `local_save_path`,
  offloads `save_fits(...)`, and stamps `frame.saved_path = str(local_save_path)`.
- `server/astrodeck/hub.py:1653-1660` — `_is_local_save(path)` — resolves + checks
  `is_relative_to(CAPTURE_DIR.resolve()) and p.exists()`. **This is the exact
  security guard the FITS-download route reuses (`app.py:2553`); the bundle reuses
  it too** so a crafted `saved_path` can never escape the capture tree.
- `server/astrodeck/naming.py:31-38` — `sanitize_component(value, mode)` (`strict` /
  `loose`); `:61-74` `render_relative_path`; `:12` `CAPTURE_EXT=".fits"`. The bundle
  reuses `sanitize_component` for every path component it emits.
- `server/astrodeck/imaging/fitsio.py:12-49` — `save_fits` writes `GAIN`, `OFFSET`,
  `XBINNING`, `IMAGETYP`, `EXPTIME`, `CCD-TEMP`, `OBJECT`, `FILTER` headers. Masters
  are ordinary FITS; the bundle references master **paths**, it never parses FITS.

**Per-sub quality metrics (what we recorded during the run):**

- `server/astrodeck/sequence/report.py:60-71` — `FrameRecord(ts, target, filter,
  frame_type, exposure_s, accepted, hfr, sensor_temp_c, guide_rms_total,
  saved_path)`. **CRITICAL GAP: no `gain`, `offset`, `binning`, `ecc`, or
  `altitude` are persisted today.** HFR ✅, guide-RMS ✅ (`guide_rms_total`),
  sensor temp ✅; the rest must be added (§1.3, Task A/B — all additive).
- `server/astrodeck/sequence/report.py:289-302` — `record_frame`; `:346-359`
  `build`; `:396-406` `load`; `:374-394` `list_reports`. Reports persist at
  `captures/reports/<id>.json` (`_reports_dir` `:46-49`).
- `server/astrodeck/sequence/engine.py:1206-1230` — `_reporter_record` builds the
  `FrameRecord`: `hfr = info.get("hfr")`, `rms` from `guider.stats().rms_total`,
  `temp` from `hub.last_frame.temperature_c`, `saved = info.get("saved_path")`.
  **In scope here but currently dropped:** `step.gain / step.offset / step.binning`
  (the `SequenceStep`, `models.py:14-20`) and `info.get("ecc")`.
- `server/astrodeck/hub.py:1597-1599`, `:1627-1628` — the preview `info` dict
  already carries `info["ecc"]` (from `frame_eccentricity`), `info["hfr"]`,
  `info["stars"]`. `engine.py:2065-2071` already reads `info.get("ecc")` for the
  reject gate, proving it is populated at record time.
- `server/astrodeck/sequence/engine.py:1588-1596` — the grounded altitude pattern:
  `from ..catalog import altaz; lat = self.hub.site["latitude"]; lon =
  self.hub.site["longitude"]; altaz(target.ra_hours, target.dec_deg, lat, lon,
  now)`. `hub.site` is a dict property (`hub.py:906-912`). We reuse this verbatim to
  stamp `altitude_deg` per accepted frame.
- `server/astrodeck/imaging/stars.py:19-28` (`Star.ecc/theta`), `:176-181`
  (`frame_eccentricity`) — PRO-7's eccentricity source; already folded into `info`.

**PRO-1 calibration-library matcher (the dependency):**

- `docs/superpowers/specs/2026-07-23-calibration-library-design.md` — **ABSENT.**
  `2026-07-23-calibration-capture-design.md` (NOV-10) exists but is a *different*
  feature (one-tap capture of calibration frames, not a master library). No
  `master`/`MasterLibrary`/`match_master` symbol exists in `server/astrodeck/`
  (grepped). **Therefore PRO-10 designs against a documented Protocol** (§1.4) and
  ships `NullMasterLibrary` (always `None`), so the bundle is fully functional
  today (groups + weights + layout; masters simply show as "not matched") and gains
  real master pairing the moment PRO-1 provides a matcher — a one-line wire-up.

**Download / route patterns to mirror:**

- `server/astrodeck/api/app.py:2044-2064` — `report_frames_csv`: `SessionReporter.
  load` → build text in memory (`io.StringIO` + `csv.writer`) → `Response(...,
  media_type="text/csv", headers={"Content-Disposition": f'attachment;
  filename="{fname}"'})` where `fname = f"{_slug(report_id)}.frames.csv"`. **The
  bundle routes mirror this exactly**, swapping the body for an in-memory `.zip`.
- `server/astrodeck/api/app.py:2026-2042` — `list_reports` / `get_report` route
  shape, `@declare(CAP_VIEW_STATUS)`, `Depends(require(CAP_VIEW_STATUS))`.
- `server/astrodeck/api/app.py:11` `import io`, `:13` `import shutil`, `:23`
  `Response`, `:43` `from .report import FrameRecord, SessionReporter`, `_slug`
  (used `:2062`), `:68` `from ..hub import CAPTURE_DIR, hub`. **No `zipfile` import
  yet** — Task 4 adds `import zipfile` (stdlib).

**UI seams:**

- `ui/src/views/ReportView.tsx:246-255` — the existing `frames.csv` download link:
  `<a href={`${BASE}/api/reports/${encodeURIComponent(sel ?? "")}/frames.csv`}
  download className="btn …"><Icon name="download"/> Download frames.csv</a>`.
  **The bundle download link sits right beside this**, same idiom.
- `ui/src/api/reports.ts:1-11` — `listReports` / `getReport` typed wrappers; Task 5
  adds `getBundlePreview`.
- `ui/src/types.ts:941-962` — `SessionReportSummary` / `SessionReport` (note:
  `frames` is intentionally **not** in the TS type; the UI never needs per-frame
  rows). `frames_captured` is available for the honest-disabled gate.
- `ui/src/lib/base.ts` — `BASE`. `ui/src/lib/__tests__/eta.test.ts:1-45` — the
  inline-assert test idiom (`npx tsx`), used by Task 5's pure helper test.

### 1.3 Approach — data model

**Additive `FrameRecord` fields** (`report.py`, all `| None = None` → every existing
persisted report loads unchanged; totals/breakdowns/trends untouched):

```python
gain: int | None = None
offset: int | None = None
binning: int | None = None
ecc: float | None = None
altitude_deg: float | None = None
```

**`_reporter_record` populate** (`engine.py`): pass `gain=step.gain`,
`offset=step.offset`, `binning=step.binning`, `ecc=info.get("ecc")`, and
`altitude_deg=_frame_altitude(target, self.hub.site, ts)` (a new pure helper, §1.6
Task B) — all best-effort, the whole build is already wrapped in `try/except`
(`engine.py:1223-1230`).

**Pure bundle core** (`server/astrodeck/sequence/bundle.py`, new — no I/O; the route
does all I/O):

```python
@dataclass(frozen=True)
class CalibKey:                 # what the master library is asked to match
    frame_type: str            # "Dark" | "Flat" | "Bias"
    exposure_s: float
    gain: int | None
    offset: int | None
    binning: int | None
    filter: str | None
    temp_c: float | None

class MasterLibrary(Protocol): # PRO-1's contract (documented; stubbed until it lands)
    def match(self, key: CalibKey) -> str | None: ...

@dataclass(frozen=True)
class LightEntry:
    src: str; dest: str        # abs source path ; bundle-relative dest path
    ts: float; accepted: bool
    hfr: float | None; ecc: float | None; guide_rms: float | None
    sensor_temp_c: float | None; altitude_deg: float | None
    weight: float              # 0..1, group-normalized, higher = better

@dataclass(frozen=True)
class Group:
    dir: str                   # relative "<TARGET>/<FILTER>/<EXP>s_g<GAIN>_bin<BIN>"
    target: str; filter: str | None; exposure_s: float
    gain: int | None; binning: int | None
    lights: tuple[LightEntry, ...]
    masters: dict[str, str]        # kind -> bundle-relative dest ("masters/masterDark.fits")
    master_sources: dict[str, str] # kind -> abs source path
    missing_masters: tuple[str, ...]

@dataclass(frozen=True)
class Bundle:
    report_id: str; plan_name: str; layout: str
    groups: tuple[Group, ...]
    warnings: tuple[str, ...]      # e.g. "no master library configured"
```

### 1.4 Approach — algorithm

`build_bundle(report, library, *, is_local, layout="grouped") -> Bundle`:

1. **Select subs.** Keep frames where `_is_light(frame_type)` (reuse
   `report._is_light`, `report.py:109`) **and** `saved_path` is truthy **and**
   `is_local(saved_path)` is true. (`is_local` is injected — the route passes
   `hub._is_local_save`; tests pass a stub. Keeps the core pure and testable, and
   reuses the one true security guard.) Rejected-but-kept ("warn") subs have a
   `saved_path` and come along with `accepted=False`; discarded rejects have no
   path and drop out naturally.
2. **Group** by `(target, filter, exposure_s, gain, binning)`. `dir` =
   `sanitize_component(target,"loose") / sanitize_component(filter or "NoFilter",
   "strict") / f"{exp:g}s_g{gain}_bin{binning}"` (gain/binning `None` → `"NA"`).
3. **Weight** each sub within its group (`sub_weight`, the tested numeric core):
   for each metric present, a 0..1 sub-score where 1 = best-in-group —
   `s_hfr = min_hfr/hfr`, `s_ecc = 1 - ecc` (ecc already 0=round=best),
   `s_rms = min_rms/rms`. `weight = mean(available sub-scores)`, or `1.0` if none
   present. Then normalize the group so the best sub = `1.0`
   (`weight /= max_weight_in_group`) — the relative form PixInsight WBPP /
   SubframeSelector and Siril expect. Altitude is **exported but not** in the
   formula in v1 (§4, decision 2). Deterministic, O(subs), no numpy.
4. **Match masters.** For each group, for `kind in ("Dark","Flat","Bias")` call
   `library.match(CalibKey(kind, exposure_s, gain, offset=None, binning, filter,
   temp_c=median group sensor_temp_c))`. A path → `masters[kind.lower()] =
   "masters/master{Kind}.fits"`, `master_sources[...] = abspath`; `None` →
   `missing_masters += kind`. (Flats key on filter+bin; darks on exp+gain+bin+temp;
   bias on gain+bin+temp — but the **tolerance/priority logic is PRO-1's**, not
   ours; we pass the full key and trust the returned path.)
5. **Warnings.** `library is NullMasterLibrary` → one warning; any group with a
   non-empty `missing_masters` is surfaced in the summary (not a warning — normal).

**Serializers (pure):** `manifest_json(bundle)` (full, with per-light rows),
`bundle_summary(bundle)` (slim: per-group counts + master match status + warnings,
for the UI preview — no per-light rows so a 2000-frame report stays a small JSON),
`weights_csv(bundle)` (one row per light), `readme_text(bundle)`,
`build_script(bundle, shell)` (`"sh"` → `cp`/`ln` with `shlex.quote`; `"ps1"` →
`Copy-Item -LiteralPath` with single-quote-escaped literals — safe by construction,
Task 3).

### 1.5 Approach — behavior (routes + UI)

- `GET /api/reports/{id}/bundle` → `bundle_summary(...)` JSON (CAP_VIEW_STATUS).
  404 if report missing. The UI preview panel reads this.
- `GET /api/reports/{id}/bundle.zip` → build report → `build_bundle` → in-memory
  `zipfile.ZipFile(io.BytesIO())` containing `manifest.json`, `weights.csv`,
  `README.txt`, `build.sh`, `build.ps1` (**not** the FITS — §4 decision 1) →
  `Response(buf, media_type="application/zip", headers={"Content-Disposition":
  f'attachment; filename="{_slug(id)}.bundle.zip"'})`. Mirrors `report_frames_csv`.
- The master library is resolved by `_get_master_library()` in `app.py`:
  `getattr(hub, "master_library", None) or NullMasterLibrary()` — real lib the
  instant PRO-1 attaches one to the hub, null today.
- `ReportView.tsx`: a new **"Stacking bundle"** `<Panel>` above the CSV link:
  per-group lines (`M42 · Ha · 300s · g100 · 42 lights · Dark ✓ · Flat ✓ · Bias —`)
  + any warnings, then a download `<a>` for `bundle.zip`. **Honest-disabled**
  (§2, §11.8) when `report.frames_captured === 0` or the preview has zero groups:
  render a `<span>` (not `<a>`) with `aria-disabled`, dimmed, lock icon, and a
  `title` explaining why — never a native-disabled control, never a dead `<a>`.

### 1.6 Placement

| Concern | File |
|---|---|
| Additive per-sub fields | `server/astrodeck/sequence/report.py` (Task A) |
| Populate at record time + altitude helper | `server/astrodeck/sequence/engine.py` (Task B) |
| Pure core: models + `build_bundle` + `sub_weight` | `server/astrodeck/sequence/bundle.py` (Task 1) |
| Pure serializers: manifest / summary / csv / readme | `server/astrodeck/sequence/bundle.py` (Task 2) |
| Pure `build_script` (sh + ps1) | `server/astrodeck/sequence/bundle.py` (Task 3) |
| Routes: `/bundle` + `/bundle.zip` | `server/astrodeck/api/app.py` (Task 4) |
| UI: api wrapper + type + ReportView panel | `ui/src/api/reports.ts`, `ui/src/types.ts`, `ui/src/views/ReportView.tsx` (Task 5) |
| Backend tests | `server/tests/test_bundle.py` (new) |
| UI pure test | `ui/src/lib/__tests__/bundleView.test.ts` (new) |

---

## 2. Global Constraints (verbatim)

- **Privacy** — real coords `37.348110` / `121.801704` and label `"My Backyard"`
  NEVER in code/tests/docs; site default `"My Observatory"`/`0.0`.
- Never `git add -A`.
- UI gate: `cd ui && npx tsc -b`.
- NO jsdom — pure logic via `npx tsx` inline-assert (idiom
  `ui/src/lib/__tests__/eta.test.ts`).
- Backend tests: `server/.venv/Scripts/pytest.exe` (repo root, `-n0` single).
- Client toasts via `useStore.getState().enqueueToast`.
- Honest-disabled §11.8 (dim + lock + `aria-disabled` + `title`, never native
  `disabled`).
- Do not disrupt astrotown.

---

## 3. TDD Plan

Interfaces first (exact signatures), then bite-sized RED→GREEN steps. Every backend
test: `server/.venv/Scripts/pytest.exe server/tests/test_bundle.py -n0 -q` from
repo root. Every UI gate: `cd ui && npx tsc -b`.

### Interfaces

```python
# server/astrodeck/sequence/report.py  (Task A — additive fields only)
class FrameRecord(BaseModel):
    ...                               # existing fields unchanged
    gain: int | None = None
    offset: int | None = None
    binning: int | None = None
    ecc: float | None = None
    altitude_deg: float | None = None

# server/astrodeck/sequence/engine.py  (Task B)
def _frame_altitude(target, site: dict, when: float) -> float | None: ...

# server/astrodeck/sequence/bundle.py  (Tasks 1-3)
@dataclass(frozen=True)
class CalibKey: ...                   # §1.3
class MasterLibrary(Protocol):
    def match(self, key: CalibKey) -> str | None: ...
class NullMasterLibrary:
    def match(self, key: CalibKey) -> str | None: return None
@dataclass(frozen=True)
class LightEntry: ...
@dataclass(frozen=True)
class Group: ...
@dataclass(frozen=True)
class Bundle: ...

def sub_weight(hfr: float | None, ecc: float | None, rms: float | None,
               *, min_hfr: float | None, min_rms: float | None) -> float: ...
def build_bundle(report: "SessionReport", library: MasterLibrary, *,
                 is_local: Callable[[str], bool], layout: str = "grouped") -> Bundle: ...
def manifest_json(bundle: Bundle) -> dict: ...
def bundle_summary(bundle: Bundle) -> dict: ...
def weights_csv(bundle: Bundle) -> str: ...
def readme_text(bundle: Bundle) -> str: ...
def build_script(bundle: Bundle, shell: str = "sh") -> str: ...   # shell in {"sh","ps1"}
```

```typescript
// ui/src/api/reports.ts  (Task 5)
export const getBundlePreview = (id: string): Promise<BundlePreview> =>
  api.get<BundlePreview>(`/api/reports/${encodeURIComponent(id)}/bundle`);

// ui/src/types.ts  (Task 5)
export interface BundleGroupSummary {
  dir: string; target: string; filter: string | null;
  exposure_s: number; gain: number | null; binning: number | null;
  light_count: number; accepted_count: number;
  masters: Record<string, boolean>;     // {dark:true, flat:true, bias:false}
}
export interface BundlePreview {
  report_id: string; plan_name: string; layout: string;
  groups: BundleGroupSummary[]; warnings: string[];
}

// ui/src/lib/bundleView.ts  (Task 5 — pure, tsx-tested)
export function masterChips(m: Record<string, boolean>): { kind: string; ok: boolean }[];
export function bundleDisabledReason(framesCaptured: number,
                                     preview: BundlePreview | null): string | null;
```

---

### Task A — additive `FrameRecord` fields · **impl tier: Sonnet** (mechanical schema add)

**Files:** `server/astrodeck/sequence/report.py`, `server/tests/test_bundle.py`.

**Step A1 (RED).** Create `server/tests/test_bundle.py`:

```python
from astrodeck.sequence.report import FrameRecord

def test_frame_record_carries_new_fields_and_defaults_none():
    fr = FrameRecord(ts=1.0, target="M42", filter="Ha", exposure_s=300.0,
                     gain=100, offset=30, binning=1, ecc=0.12, altitude_deg=61.5)
    d = fr.model_dump()
    assert d["gain"] == 100 and d["binning"] == 1
    assert d["ecc"] == 0.12 and d["altitude_deg"] == 61.5
    # backward-compat: an old record without the new keys loads with None
    old = FrameRecord(ts=2.0, target="M31", exposure_s=60.0)
    assert old.gain is None and old.ecc is None and old.altitude_deg is None
```

Run `server/.venv/Scripts/pytest.exe server/tests/test_bundle.py -n0 -q` →
**RED** (`TypeError: unexpected keyword argument 'gain'`).

**Step A2 (GREEN).** Add the five fields to `FrameRecord` after `saved_path`
(`report.py:71`). Re-run → `1 passed`.

---

### Task B — populate at record time + `_frame_altitude` · **impl tier: Sonnet** (small; altitude is a 4-line pure helper reusing the `engine.py:1588-1596` pattern)

**Files:** `server/astrodeck/sequence/engine.py`, `server/tests/test_bundle.py`.

**Step B1 (RED).** Add to `test_bundle.py`:

```python
from astrodeck.sequence.engine import _frame_altitude

def test_frame_altitude_uses_site_and_returns_none_on_bad_input():
    site = {"latitude": 34.0, "longitude": -118.0}   # neutral, NOT the backyard
    class T: ra_hours = 5.5; dec_deg = -5.4
    alt = _frame_altitude(T(), site, 1_700_000_000.0)
    assert alt is None or -90.0 <= alt <= 90.0
    assert _frame_altitude(None, site, 1.0) is None            # defensive
    assert _frame_altitude(T(), {}, 1.0) is None               # missing lat/lon
```

Run → **RED** (`ImportError: cannot import name '_frame_altitude'`).

**Step B2 (GREEN).** Add the module-level helper to `engine.py` (reuses the grounded
`altaz` pattern):

```python
def _frame_altitude(target, site: dict, when: float) -> float | None:
    """Best-effort target altitude (deg) at capture time; None on any gap."""
    try:
        from ..catalog import altaz
        lat = site["latitude"]; lon = site["longitude"]
        alt, _ = altaz(target.ra_hours, target.dec_deg, lat, lon, when)
        return round(float(alt), 2)
    except Exception:
        return None
```

Then extend the `FrameRecord(...)` construction in `_reporter_record`
(`engine.py:1224-1228`) — add `gain=step.gain, offset=step.offset,
binning=step.binning, ecc=info.get("ecc") if isinstance(info, dict) else None,
altitude_deg=_frame_altitude(target, self.hub.site, time.time())`. Run → `2 passed`.
Gate the wider engine: `server/.venv/Scripts/pytest.exe server/tests/test_report.py
server/tests/test_session_engine.py -n0 -q` → still green (additive kwargs only).

---

### Task 1 — pure core: models + `sub_weight` + `build_bundle` · **impl tier: Opus** (justify: the group-normalized weighting numerics + graceful `None` handling + the grouping/selection invariants are the correctness-critical heart; everything downstream renders what this returns)

**Files:** `server/astrodeck/sequence/bundle.py` (new), `server/tests/test_bundle.py`.

**Step 1a (RED) — weighting.** Add:

```python
from astrodeck.sequence.bundle import sub_weight

def test_sub_weight_best_is_one_and_missing_metrics_ignored():
    # single metric present, this sub is group-best -> 1.0
    assert sub_weight(2.0, None, None, min_hfr=2.0, min_rms=None) == 1.0
    # worse HFR than group min -> < 1.0, monotone
    w_good = sub_weight(2.0, 0.10, 1.0, min_hfr=2.0, min_rms=1.0)
    w_bad  = sub_weight(4.0, 0.40, 3.0, min_hfr=2.0, min_rms=1.0)
    assert 0.0 < w_bad < w_good <= 1.0
    # no metrics at all -> neutral 1.0 (never penalize an un-measured sub to 0)
    assert sub_weight(None, None, None, min_hfr=None, min_rms=None) == 1.0
```

Run → **RED** (module missing).

**Step 1b (GREEN) — weighting.** Create `bundle.py` with the dataclasses/Protocol
from §1.3 and:

```python
def sub_weight(hfr, ecc, rms, *, min_hfr, min_rms) -> float:
    scores: list[float] = []
    if hfr is not None and min_hfr is not None and hfr > 0:
        scores.append(min(1.0, min_hfr / hfr))
    if ecc is not None:
        scores.append(max(0.0, min(1.0, 1.0 - ecc)))
    if rms is not None and min_rms is not None and rms > 0:
        scores.append(min(1.0, min_rms / rms))
    return sum(scores) / len(scores) if scores else 1.0
```

Run → `3 passed`.

**Step 1c (RED) — build_bundle grouping + selection + master match.** Add:

```python
from astrodeck.sequence.report import SessionReport, FrameRecord
from astrodeck.sequence.bundle import build_bundle, NullMasterLibrary, CalibKey

def _rep(frames):
    return SessionReport(id="r1", plan_name="P", frames=frames)

def test_build_bundle_groups_selects_locals_matches_masters():
    frames = [
        FrameRecord(ts=1, target="M42", filter="Ha", exposure_s=300, gain=100,
                    binning=1, hfr=2.0, ecc=0.1, guide_rms_total=0.5,
                    saved_path="/cap/a.fits", accepted=True),
        FrameRecord(ts=2, target="M42", filter="Ha", exposure_s=300, gain=100,
                    binning=1, hfr=4.0, ecc=0.4, guide_rms_total=1.0,
                    saved_path="/cap/b.fits", accepted=True),
        # different exposure -> its own group
        FrameRecord(ts=3, target="M42", filter="Ha", exposure_s=120, gain=100,
                    binning=1, hfr=2.5, saved_path="/cap/c.fits", accepted=True),
        # a Dark (not a light) -> excluded from groups
        FrameRecord(ts=4, target="M42", frame_type="Dark", exposure_s=300,
                    saved_path="/cap/d.fits", accepted=True),
        # no saved_path -> excluded
        FrameRecord(ts=5, target="M42", filter="Ha", exposure_s=300, gain=100,
                    binning=1, hfr=3.0, accepted=True),
    ]
    class Lib:
        def match(self, key: CalibKey):
            return "/masters/dark.fits" if key.frame_type == "Dark" else None
    b = build_bundle(_rep(frames), Lib(), is_local=lambda p: p.startswith("/cap/"))
    assert len(b.groups) == 2                       # (Ha,300,g100,bin1) and (Ha,120,…)
    g = next(g for g in b.groups if g.exposure_s == 300)
    assert len(g.lights) == 2                       # d(dark) + e(no path) excluded
    assert g.dir == "M42/Ha/300s_g100_bin1"
    best = max(g.lights, key=lambda l: l.weight)
    assert best.hfr == 2.0 and best.weight == 1.0   # group-normalized best
    assert g.masters == {"dark": "masters/masterDark.fits"}
    assert set(g.missing_masters) == {"Flat", "Bias"}

def test_build_bundle_null_library_warns_and_still_groups():
    frames = [FrameRecord(ts=1, target="M42", filter="Ha", exposure_s=300,
                          gain=100, binning=1, hfr=2.0, saved_path="/cap/a.fits")]
    b = build_bundle(_rep(frames), NullMasterLibrary(),
                     is_local=lambda p: True)
    assert len(b.groups) == 1
    assert any("master" in w.lower() for w in b.warnings)
```

Run → **RED**.

**Step 1d (GREEN).** Implement `NullMasterLibrary` and `build_bundle` per §1.4:
reuse `report._is_light`, `naming.sanitize_component`; group into an ordered dict;
compute `min_hfr`/`min_rms` per group; `sub_weight` each then divide by the group
max weight (guard max==0); build `CalibKey` per kind with `temp_c` = median of the
group's non-None `sensor_temp_c`; append the "no master library configured" warning
when `isinstance(library, NullMasterLibrary)`. Run → `5 passed`.

---

### Task 2 — pure serializers: manifest / summary / csv / readme · **impl tier: Sonnet** (mechanical dict/CSV/string assembly over the Task-1 model)

**Files:** `server/astrodeck/sequence/bundle.py`, `server/tests/test_bundle.py`.

**Step 2a (RED).** Add:

```python
import csv, io
from astrodeck.sequence.bundle import (manifest_json, bundle_summary,
                                        weights_csv, readme_text)

def test_serializers_shapes():
    frames = [FrameRecord(ts=1, target="M42", filter="Ha", exposure_s=300,
                          gain=100, binning=1, hfr=2.0, ecc=0.1,
                          guide_rms_total=0.5, altitude_deg=61.0,
                          saved_path="/cap/a.fits", accepted=True)]
    b = build_bundle(_rep(frames), NullMasterLibrary(), is_local=lambda p: True)

    m = manifest_json(b)
    assert m["report_id"] == "r1" and m["layout"] == "grouped"
    assert m["groups"][0]["lights"][0]["dest"] == "M42/Ha/300s_g100_bin1/lights/a.fits"

    s = bundle_summary(b)
    assert s["groups"][0]["light_count"] == 1
    assert s["groups"][0]["masters"] == {"dark": False, "flat": False, "bias": False}
    assert "lights" not in s["groups"][0]            # slim: no per-light rows

    rows = list(csv.reader(io.StringIO(weights_csv(b))))
    assert rows[0][:3] == ["target", "filter", "exposure_s"]
    assert "weight" in rows[0] and len(rows) == 2     # header + 1 light

    assert "300s_g100_bin1" in readme_text(b)         # references the layout
```

Run → **RED**.

**Step 2b (GREEN).** Implement the four functions. `dest` for a light =
`f"{group.dir}/lights/{Path(src).name}"`. `weights_csv` header:
`["target","filter","frame_type","exposure_s","gain","binning","accepted","hfr",
"ecc","guide_rms","sensor_temp_c","altitude_deg","weight","dest","src"]` via
`csv.writer` (blank for `None`). `bundle_summary` emits per-group counts + a
`{dark,flat,bias: bool}` map (`kind in masters`), no per-light rows. `readme_text`
explains the layout + that lights/masters are placed by `build.sh`/`build.ps1`.
Run → `6 passed`.

---

### Task 3 — pure `build_script` (sh + ps1, injection-safe) · **impl tier: Opus** (justify: filenames flow into shell text; correctness = no shell/PowerShell injection is a genuine safety property, tested against adversarial names)

**Files:** `server/astrodeck/sequence/bundle.py`, `server/tests/test_bundle.py`.

**Step 3a (RED).** Add:

```python
from astrodeck.sequence.bundle import build_script

def test_build_script_quotes_paths_safely():
    frames = [FrameRecord(ts=1, target="M42", filter="Ha", exposure_s=300,
                          gain=100, binning=1, hfr=2.0,
                          saved_path="/cap/a b;rm -rf ~.fits", accepted=True)]
    class Lib:
        def match(self, key): return "/masters/d'q.fits" if key.frame_type=="Dark" else None
    b = build_bundle(_rep(frames), Lib(), is_local=lambda p: True)

    sh = build_script(b, "sh")
    assert sh.startswith("#!/bin/sh")
    assert "'/cap/a b;rm -rf ~.fits'" in sh          # shlex.quote'd, not interpolated raw
    assert "rm -rf ~.fits\n" not in sh               # the bad name never becomes a command

    ps = build_script(b, "ps1")
    assert "Copy-Item -LiteralPath '/masters/d''q.fits'" in ps   # '' escapes the quote
    assert "mkdir" in ps.lower() or "New-Item" in ps
```

Run → **RED**.

**Step 3b (GREEN).** Implement with `shlex.quote` for `sh` (`mkdir -p <q(dir)>`,
`cp <q(src)> <q(dest)>` — or `ln -s` per §4 decision 1) and, for `ps1`,
single-quoted literals with `.replace("'", "''")` and `Copy-Item -LiteralPath`.
Emit one mkdir + copy per light and per matched master; header line `#!/bin/sh`
(sh) / a `# PowerShell` comment (ps1). Run → `7 passed`.

---

### Task 4 — routes: `/bundle` (JSON) + `/bundle.zip` · **impl tier: Sonnet** (mirrors `report_frames_csv`; stdlib `zipfile`; thin)

**Files:** `server/astrodeck/api/app.py`, `server/tests/test_bundle.py`.

**Step 4a (RED) — route test** (uses the app test client fixture like other route
tests; if none is handy, this step may be verified with the existing app-level test
harness). Add a zip-shape assertion:

```python
import io, zipfile
# pseudocode against the project's existing TestClient fixture `client`:
# r = client.get("/api/reports/<seeded id>/bundle.zip")
# assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
# names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
# assert {"manifest.json","weights.csv","README.txt","build.sh","build.ps1"} <= set(names)
```

(If seeding a report through the client is heavy, keep Task 4 verification at the
**pure zip-assembly** level: factor the in-memory zip build into a helper
`_zip_bytes(bundle) -> bytes` in `app.py` or `bundle.py` and unit-test *that* with a
hand-built `Bundle`, leaving the route as a thin typechecked shell. Recommended —
keeps the tested seam pure.)

**Step 4b (GREEN).** Add `import zipfile` (`app.py`). Add
`_get_master_library()` (`getattr(hub, "master_library", None) or
NullMasterLibrary()`) and, beside `report_frames_csv` (`app.py:2064`):

```python
@app.get("/api/reports/{report_id}/bundle", dependencies=[Depends(require(CAP_VIEW_STATUS))])
@declare(CAP_VIEW_STATUS)
async def report_bundle(report_id: str):
    report = await asyncio.to_thread(SessionReporter.load, report_id)
    if report is None:
        raise HTTPException(404, "report not found")
    b = build_bundle(report, _get_master_library(), is_local=hub._is_local_save)
    return bundle_summary(b)

@app.get("/api/reports/{report_id}/bundle.zip", dependencies=[Depends(require(CAP_VIEW_STATUS))])
@declare(CAP_VIEW_STATUS)
async def report_bundle_zip(report_id: str):
    report = await asyncio.to_thread(SessionReporter.load, report_id)
    if report is None:
        raise HTTPException(404, "report not found")
    b = build_bundle(report, _get_master_library(), is_local=hub._is_local_save)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest_json(b), indent=2))
        z.writestr("weights.csv", weights_csv(b))
        z.writestr("README.txt", readme_text(b))
        z.writestr("build.sh", build_script(b, "sh"))
        z.writestr("build.ps1", build_script(b, "ps1"))
    fname = f"{_slug(report_id)}.bundle.zip"
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})
```

Import `build_bundle, manifest_json, bundle_summary, weights_csv, readme_text,
build_script, NullMasterLibrary` from `..sequence.bundle`, add `import json` if not
present. Run the pure helper test → green; full suite spot-check:
`server/.venv/Scripts/pytest.exe server/tests/test_bundle.py -n0 -q` → all pass.

---

### Task 5 — UI: api wrapper + type + ReportView panel · **impl tier: Sonnet** (thin render over a pure `bundleView.ts` helper; typecheck gate)

**Files:** `ui/src/api/reports.ts`, `ui/src/types.ts`, `ui/src/lib/bundleView.ts`
(new), `ui/src/lib/__tests__/bundleView.test.ts` (new), `ui/src/views/ReportView.tsx`.

**Step 5a (RED) — pure helper test.** Create `bundleView.test.ts` mirroring
`eta.test.ts`'s harness:

```typescript
import { masterChips, bundleDisabledReason } from "../bundleView";
// ...harness (test/eq/assert) copied from eta.test.ts ...

test("masterChips fixed order dark/flat/bias with ok flags", () => {
  const c = masterChips({ dark: true, flat: false, bias: true });
  eq(c.map((x) => x.kind).join(","), "dark,flat,bias");
  eq(c[1].ok, false);
});
test("disabled when no frames or no groups; enabled otherwise", () => {
  assert(bundleDisabledReason(0, null) !== null, "0 frames disabled");
  const empty = { report_id: "r", plan_name: "p", layout: "grouped", groups: [], warnings: [] };
  assert(bundleDisabledReason(5, empty) !== null, "no groups disabled");
  const ok = { ...empty, groups: [{ dir: "d", target: "M42", filter: "Ha",
    exposure_s: 300, gain: 100, binning: 1, light_count: 2, accepted_count: 2,
    masters: { dark: true, flat: false, bias: false } }] };
  eq(bundleDisabledReason(5, ok), null);
});
// footer:
if (failed) { console.error(failures.join("\n")); process.exit(1); }
console.log(`bundleView: ${passed} passed`);
```

Run `cd ui && npx tsx src/lib/__tests__/bundleView.test.ts` → **RED** (module
missing).

**Step 5b (GREEN) — pure helper.** Create `ui/src/lib/bundleView.ts`:

```typescript
import type { BundlePreview } from "../types";
export function masterChips(m: Record<string, boolean>) {
  return (["dark", "flat", "bias"] as const).map((kind) => ({ kind, ok: !!m[kind] }));
}
export function bundleDisabledReason(framesCaptured: number,
                                     preview: BundlePreview | null): string | null {
  if (framesCaptured <= 0) return "No frames were captured in this session.";
  if (preview && preview.groups.length === 0)
    return "No local light subs available to bundle.";
  return null;
}
```

Add the `BundleGroupSummary` / `BundlePreview` interfaces to `types.ts` and
`getBundlePreview` to `api/reports.ts` (both from the Interfaces block). Re-run the
tsx test → `bundleView: 2 passed`.

**Step 5c (GREEN) — thin render.** In `ReportView.tsx`, load the preview in Effect B
alongside `getReport` (best-effort; a preview failure just leaves the panel absent),
and render a `<Panel title="Stacking bundle">` above the CSV link (`:246`): map
`preview.groups` to lines with `masterChips`; render warnings in `text-warn`; then
the download control. **Honest-disabled** via `bundleDisabledReason`: when non-null,
render a dimmed `<span aria-disabled="true" title={reason}>` with a lock icon
instead of the `<a>`; when null, the real
`<a href={`${BASE}/api/reports/${encodeURIComponent(sel ?? "")}/bundle.zip`} download
className="btn …">`. No `useState` beyond a `preview` slot; no writes; CAP_VIEW_STATUS
(the whole view is already read-only). Gate: `cd ui && npx tsc -b` → clean.

---

## 4. Open decisions (with recommendations)

1. **Download payload = manifest + scripts, or the FITS themselves?**
   *Recommendation: manifest + scripts (v1).* Subs are 25–120 MB each; a night is
   tens of GB. Zipping/copying that through a browser on a Raspberry Pi is a
   non-starter. The `.zip` ships `manifest.json` + `weights.csv` + `README.txt` +
   `build.sh`/`build.ps1`; the script materializes the tree locally (default
   `ln -s`, `cp` fallback) from the user's existing captures — small, fast, safe.
   *Follow-up:* a server-side `?materialize=hardlink` that lays the tree out under
   `captures/exports/<id>/` for users running AstroDeck on the capture box.

2. **Altitude in the weight formula, or informational-only?**
   *Recommendation: informational-only (v1).* We persist + export `altitude_deg`,
   but the weight = `mean(hfr, ecc, rms sub-scores)`. Folding airmass in correctly
   (transparency vs. extinction vs. seeing) is subtle and stacker-dependent; a wrong
   default silently mis-weights. Ship the honest quality weight; expose altitude so
   the user can add an airmass term in their stacker. *Follow-up:* optional
   `weight_altitude=true` with a documented `sin(alt)` term.

3. **FWHM column?** *Recommendation: omit (v1).* We measure HFR, not FWHM;
   `FWHM ≈ k·HFR` depends on PSF/sampling and would mislead a SubframeSelector
   expression keyed on true FWHM. HFR is the sharpness column. *Follow-up:* a real
   per-frame FWHM in PRO-7 → an additive `fwhm` field + column.

4. **Siril / APP layouts now?** *Recommendation: defer (v1 = one "grouped"
   layout).* `build_bundle`/`build_script` already take a `layout` param; add
   `"siril"` (`lights/ darks/ flats/ biases/` per group) and `"app"` as pure
   follow-up branches once the grouped layout is validated on a real stack.

5. **Master library when PRO-1 is absent.** *Recommendation: ship
   `NullMasterLibrary` + a `warnings` entry + per-group `missing_masters`.* The
   bundle is fully useful today (grouping + weighting + layout); `report_bundle`
   resolves `hub.master_library` the instant PRO-1 attaches one — zero PRO-10
   rework. Confirm PRO-1's real signature is `match(CalibKey) -> str | None` when it
   lands (the one integration point).

6. **`keep` vs `accepted`.** *Recommendation: `keep == accepted` (v1).* Don't
   second-guess the run's own quality gate; export the numeric `weight` and let the
   user cull in their stacker. *Follow-up:* an optional `keep_threshold` that flags
   the worst tail.
