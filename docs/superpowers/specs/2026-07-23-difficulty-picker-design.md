# NOV-3 — "What can I image tonight?" ranked target picker with difficulty ratings

Combined design spec + TDD implementation plan. One file.

Slug: `difficulty-picker`

---

## 1. DESIGN

### 1.1 Goal

A first-timer opens the Atlas and, instead of a blank "frame a target" prompt,
sees a **ranked list of what's best to image tonight** — each object tagged with
a plain **Easy / Moderate / Hard** difficulty rating so they pick a slam-dunk
(M42, M45, M31) not an impossible smudge (a small faint galaxy). Picking a row
opens a framing session exactly as the existing catalog search does.

Two new pure, tested pieces of logic:
1. **Difficulty derivation** (server, Python): a `mag + size → surface-brightness`
   heuristic mapped to a 3-tier rating, with a small **curated override table**
   for famous exceptions the heuristic misreads.
2. **Tonight ranking** (server, Python): a pure visibility score that reuses the
   *existing* `compute_night` ephemeris and orders the whole catalog best-first.

The difficulty tier is stamped onto every `/api/catalog` row (additive), and a
new `GET /api/catalog/tonight` endpoint returns the ranked, difficulty-tagged
picks. A new `TonightPicker` component renders them in the Atlas empty state.

### 1.2 Current-state seams (every one read, cited file:line)

**Catalog data — difficulty does NOT exist:**
- `server/astrodeck/catalog/objects.py:12-20` — the frozen `DSO` dataclass:
  `id, name, type, ra_hours, dec_deg, mag, size_arcmin`. **No `difficulty`, no
  surface brightness.** Confirms the brief.
- `objects.py:23-89` — `_RAW`, ~65 curated crowd-pleaser rows (mag + size' per
  object; the raw material the heuristic consumes).
- `objects.py:91-95` — `_TYPE_NAMES` (short code → human type).
- `objects.py:97` — `CATALOG: list[DSO]`.
- `objects.py:100-112` — `search_catalog(query, limit=25)` builds the result
  **dict** per object (`id/name/type/ra_hours/dec_deg/mag/size_arcmin`) and
  sorts by `mag`. This is the single projection `DSO → dict` — the natural place
  to stamp difficulty so every consumer gets it for free.

**The `/api/catalog` route (stamps live alt/az):**
- `server/astrodeck/api/app.py:3265-3275` — `GET /api/catalog?q=` calls
  `search_catalog(q)` then, per row, stamps `alt`/`az` via
  `altaz(ra, dec, hub.site["latitude"], hub.site["longitude"])`. Plain `list[dict]`
  return, **no pydantic response_model** → adding keys is safe/additive.
- `app.py:47` — `from ..catalog import search_catalog` already imported.
- `app.py:9` — `import asyncio` already at module top (the new endpoint's fan-out
  needs it; no new import required).
- `server/astrodeck/catalog/coords.py:63-76` — `altaz(...)` (hand-rolled, cheap).

**The tonight/altitude/best-window math already exists — REUSE it:**
- `server/astrodeck/catalog/visibility.py:387-495` — `compute_night(ra_hours,
  dec_deg, *, date, step_min, alt_limit, site) -> dict`. Pure (astropy in-process
  only). Returns `transit_alt`, `best_window {start_unix,end_unix,mean_alt}|null`,
  `moon {separation_deg,...}`, `never_rises_above_limit`, `date`, etc.
- `visibility.py:331-382` — `_best_window(...)` returns `mean_alt` (the raw mean
  altitude of the best contiguous run) — the visibility quality signal to rank on.
- `visibility.py:536-573` — `post_order(...)`: the **fan-out pattern to mirror** —
  `asyncio.Semaphore(8)` + `asyncio.gather` of `asyncio.to_thread(compute_night,
  …, step_min=20)` over ≤200 targets. The tonight endpoint is the same shape over
  `CATALOG` (~65 objects).
- `visibility.py:498-507` — `transit_alt_for(...)` (coarse step_min=20 precedent).

**TS contracts:**
- `ui/src/types.ts:353-363` — `CatalogEntry` (flat: `id,name,type,ra_hours,
  dec_deg,mag,size_arcmin,alt,az`). Extend additively with `difficulty?`.
- `ui/src/types.ts:951-974` — `VisibilityNight` / `VisibilityTarget` (the shapes
  the new pick reuses fields from: `best_window`, `moon_sep_deg`).

**Existing catalog/visibility UI to mirror:**
- `ui/src/components/atlas/CatalogSearch.tsx:100-119` — debounced `GET /api/catalog`
  dropdown; each row shows an **alt chip** colored `alt>40 → text-good`, `<20 →
  text-warn`, else `text-dim` (`CatalogSearch.tsx:109`). The TonightPicker mirrors
  this alt-chip idiom and the `onPick(entry)` contract (`CatalogSearch.tsx:55-59`).
- `ui/src/components/atlas/VisibilityPanel.tsx:82-116` — fetch-with-debounce +
  loading/error/ok state machine; the pattern the picker's fetch effect follows.
- `ui/src/views/AtlasView.tsx:96-122` — `AtlasEmpty` (the empty-state card holding
  `CatalogSearch` + "Free-roam"); `AtlasView.tsx:404` — rendered when `!framing`.
  This is the **placement**.
- `ui/src/views/AtlasView.tsx:144` — `enqueueToast` already pulled from store here.
- `ui/src/store.ts:799-831` — `openFraming(e?: CatalogEntry)` seeds a session from
  the entry and sets `view:"atlas"`. A pick calls this — no store change needed.

**Glyph-not-hue formatting precedent (night palette §8):**
- `ui/src/lib/visibility.ts:177-207` — `moonSepGlyph`/`moonSepTone`/`darknessLabel`
  are pure string helpers (glyph is the primary channel, tone secondary). The new
  `lib/difficulty.ts` mirrors this exactly.

**UI primitives (exact signatures for the thin render):**
- `ui/src/components/ui.tsx:18-32` — `Panel({title,right,children,className})`.
- `ui/src/components/ui.tsx:81-84` — `Stat({label,value,unit,tone,hint,glyph})` —
  `glyph` accepts a custom ReactNode (used for the difficulty glyph).
- `ui/src/components/ui.tsx:492-511` — `EmptyState({icon,title,hint,action,size})`.

**Test idioms:**
- `ui/src/lib/__tests__/eta.test.ts:21-43` — the inline-assert harness
  (`test/eq/assert/near`, `console.log(\`name: N/total passed\`)`), run via
  `npx tsx`. No jsdom, pure logic only.
- `server/tests/test_catalog.py:1-35` — pytest idiom: `from astrodeck.catalog
  import …` then plain asserts.
- `server/tests/test_visibility.py:36-53,58-139` — pure-function pytest + a minimal
  `FastAPI()` with only the router mounted, `ConfigStore` seeded with a
  **non-default synthetic site** (40/-74), monkeypatched into `config_mod`/`hub_mod`.
  The endpoint test mirrors this.

### 1.3 Approach — data shapes, copy, behavior

**Difficulty tier.** `"easy" | "moderate" | "hard"`. Derived from a weighted blend
of integrated magnitude and mean surface brightness, with curated overrides:

```
surface_brightness_mag(mag, size') = mag + 2.5·log10(π·(size'/2)²)   # mag/arcmin²
score = 0.65·clamp01((mag − 6.5)/3.0) + 0.35·clamp01((sb − 13.0)/2.5)
tier  = easy  if score < 0.34 ; moderate if < 0.67 ; else hard
```

The **worse of "faint" and "spread thin" gates the rating**, but integrated mag
dominates (0.65) so the big bright heroes stay Easy. Worked examples (these are
the pytest vectors — see §3):

| Object | mag | size' | sb (≈) | score (≈) | tier |
|---|---|---|---|---|---|
| M42 Orion | 4.0 | 85 | 13.4 | 0.05 | easy |
| M31 Andromeda | 3.4 | 190 | 14.5 | 0.21 | easy |
| M45 Pleiades | 1.6 | 110 | 11.5 | 0.00 | easy |
| M13 Hercules GC | 5.8 | 20 | 12.0 | 0.00 | easy |
| M51 Whirlpool | 8.4 | 11 | 13.3 | 0.46 | moderate |
| M74 Phantom | 9.4 | 10.5 | 14.2 | 0.80 | hard |
| NGC 4565 Needle | 10.4 | 15.8 | 16.1 | 1.00 | hard |

**Curated override table** (`id → tier`) — the escape hatch for famous cases the
mean-SB model misreads for a *beginner on an OSC/DSLR*:

```
"IC 434":  "hard"      # Horsehead — dark nebula, needs Hα (heuristic says moderate)
"NGC 1499":"hard"      # California — very low surface brightness (heuristic moderate)
"M33":     "moderate"  # Triangulum — big but low SB, a classic beginner letdown (heuristic easy)
"M101":    "moderate"  # Pinwheel — low-SB face-on spiral (heuristic easy)
```

`difficulty_for(id, mag, size') → {tier, surface_brightness, source, score}` where
`source ∈ {"heuristic","curated"}` and `score` is `None` for curated rows.

**Catalog row (additive).** `search_catalog` stamps three flat keys per row:
`difficulty` (tier string), `surface_brightness` (rounded mag/arcmin²),
`difficulty_source`. Existing consumers ignore them; `CatalogSearch` can later
show a badge (out of scope here — the picker is the surface).

**Tonight ranking.** Pure `tonight_score(night) → float` (higher = better):
```
never_rises_above_limit  → −100 + transit_alt   # always below risers, still orderable
best_window present       → best_window.mean_alt
else                      → transit_alt
```
Difficulty is a **tag/filter, not a demotion** (brief: "rank by tonight's
best-window visibility and tag each object with a difficulty rating"). Ranking is
purely visibility; the beginner *filters* to Easy/Moderate. `rank_picks(picks)`
sorts by `(−score, mag)` (best window first; tie-break brighter first).

**Endpoint.** `GET /api/catalog/tonight?date=&alt_limit=` — fans `compute_night`
(step_min=20) over `CATALOG` with `Semaphore(8)` + `gather` (mirrors `post_order`),
attaches difficulty, returns:
```jsonc
{ "date": "2026-07-23",
  "site_is_default": false,
  "picks": [ { id,name,type,ra_hours,dec_deg,mag,size_arcmin,
               difficulty,surface_brightness,difficulty_source,
               max_alt,transit_unix,best_window|null,moon_sep_deg,
               never_rises_above_limit,score } ] }
```

**Picker UI behavior.** `TonightPicker`:
- On mount fetches `/api/catalog/tonight?alt_limit={site.horizon_min_deg ?? 30}`.
- Loading skeleton → error+retry → ranked list (mirrors VisibilityPanel's state
  machine).
- A **beginner filter** segmented control: `Beginner (Easy + Moderate)` default ON
  / `All`. Filtering to an empty set renders an `EmptyState` (never dead air).
- Each row: `#id  Name` · type · a **difficulty badge** (glyph + label, tone
  secondary) · an **alt chip** (`max_alt`, colored with the CatalogSearch idiom) ·
  a below-limit "low" warn glyph when `never_rises_above_limit`. Rows stay fully
  **pickable** (framing a low target is allowed; the Atlas/Send flow warns later —
  `AtlasView.tsx:475-476,929-938`), so nothing here is disabled.
- `site_is_default` → the same warn nudge AtlasView shows (`AtlasView.tsx:772-780`):
  "Using a default location — set yours in Settings for accurate visibility."
- Row click → `onPick(entry)` where the parent passes `openFraming` (identical to
  `CatalogSearch`), seeding a framing session from the pick's ra/dec/name/type.

Copy: panel title **"What can I image tonight?"**; difficulty labels **Easy /
Moderate / Hard**; glyphs `● / ◐ / ○` (filled → open = harder), tone
`good/warn/bad` as the secondary channel only.

### 1.4 Placement

The `TonightPicker` renders **inside `AtlasEmpty`** (`AtlasView.tsx:96-122`),
below the existing `CatalogSearch`/free-roam actions — the first surface a novice
sees on the Atlas with no active session. Picking a row opens a framing session
(the empty state is then replaced by the framer, exactly as a search pick is).
No new nav view, no `ViewName`/store changes — minimal, shippable slice.
(Promotion to its own "Tonight" nav tab and re-surfacing it while a session is
active are noted in Open Decisions.)

---

## 2. GLOBAL CONSTRAINTS (verbatim)

- **Privacy.** The real coordinates `[SITE-LAT]` / `[SITE-LON]` and the label
  "[SITE-LABEL]" must **NEVER** appear in code, tests, or docs. The site default is
  "My Observatory" / `0.0`. All tests use synthetic non-default sites (e.g.
  `40.0 / -74.0`), matching `test_visibility.py`.
- **Never `git add -A`.** Stage files explicitly by path.
- **UI typecheck gate:** `cd ui && npx tsc -b`.
- **NO jsdom.** Pure logic is tested via `npx tsx` with the inline-assert idiom
  (`ui/src/lib/__tests__/eta.test.ts`). No React render tests.
- **Backend tests:** `server/.venv/Scripts/pytest.exe` from the repo root, `-n0`
  (single worker).
- **Client toasts** via `useStore.getState().enqueueToast` (or the pulled
  `enqueueToast` already in AtlasView).
- **Honest-disabled (§11.8):** any gated control is dim + lock + `aria-disabled` +
  `title`, **never** native `disabled`. (The picker gates nothing by default — all
  rows are pickable; documented so a reviewer knows it was considered. If a future
  direct "Send to Plan" affordance is gated while a sequence runs, it must use this
  idiom, not `disabled`.)
- **Do not disrupt astrotown** (the deployed box). No deploys; code + tests only.

---

## 3. TDD IMPLEMENTATION PLAN

Right-sized: 7 tasks. Pure numeric/ranking logic behind tested functions
(pytest + tsx); thin renders verified by the typechecker.

### Interfaces block (exact signatures)

**Python — `server/astrodeck/catalog/difficulty.py` (new):**
```python
DifficultyTier = str  # "easy" | "moderate" | "hard"

CURATED: dict[str, str]  # object id -> tier override

def surface_brightness_mag(mag: float, size_arcmin: float) -> float: ...
def difficulty_score(mag: float, size_arcmin: float) -> float: ...
def tier_from_score(score: float) -> str: ...
def difficulty_for(obj_id: str, mag: float, size_arcmin: float) -> dict: ...
    # -> {"tier": str, "surface_brightness": float,
    #     "source": "heuristic"|"curated", "score": float|None}
```

**Python — `server/astrodeck/catalog/tonight.py` (new):**
```python
def tonight_score(night: dict) -> float: ...
def rank_picks(picks: list[dict]) -> list[dict]: ...   # sorted (-score, mag)
```

**Python — `server/astrodeck/api/app.py` (new route in `create_app`):**
```python
@app.get("/api/catalog/tonight", dependencies=[Depends(require(CAP_VIEW_STATUS))])
@declare(CAP_VIEW_STATUS)
async def catalog_tonight(date: str | None = None, alt_limit: float = 30.0): ...
```

**TS — `ui/src/lib/difficulty.ts` (new):**
```ts
export type DifficultyTier = "easy" | "moderate" | "hard";
export const BEGINNER_TIERS: DifficultyTier[];          // ["easy","moderate"]
export function difficultyLabel(t: DifficultyTier): string;
export function difficultyGlyph(t: DifficultyTier): string;
export function difficultyTone(t: DifficultyTier): "good" | "warn" | "bad";
export function difficultyHint(t: DifficultyTier): string;
export function isBeginnerFriendly(t: DifficultyTier): boolean;
```

**TS — `ui/src/types.ts` (additive):**
```ts
// on CatalogEntry:
difficulty?: DifficultyTier;
surface_brightness?: number;
difficulty_source?: "heuristic" | "curated";

export interface TonightPick { /* see Task 5 */ }
export interface TonightResponse { date: string; site_is_default: boolean; picks: TonightPick[]; }
```

**TS — `ui/src/components/atlas/TonightPicker.tsx` (new):**
```ts
export function TonightPicker({ onPick }: { onPick: (e: CatalogEntry) => void }): JSX.Element;
```

---

### Task 1 — `difficulty.py`: the tested heuristic  ·  **Sonnet (mechanical)**
The numeric design is fully pre-specified in §1.3 (formula + constants + vectors);
implementation is verbatim. No genuine numeric judgement remains at build time.

**Files:** `server/astrodeck/catalog/difficulty.py` (new),
`server/tests/test_difficulty.py` (new).

**Step 1a — write the test first** (`server/tests/test_difficulty.py`):
```python
"""Difficulty heuristic (NOV-3): mag + size -> surface brightness -> tier."""
from astrodeck.catalog.difficulty import (
    surface_brightness_mag, difficulty_score, tier_from_score, difficulty_for,
    CURATED,
)


def test_surface_brightness_disk_model():
    # sb = mag + 2.5*log10(pi*(size/2)^2). M42: 4.0 mag, 85' -> ~13.4 mag/arcmin^2.
    assert abs(surface_brightness_mag(4.0, 85.0) - 13.4) < 0.1
    assert abs(surface_brightness_mag(3.4, 190.0) - 14.5) < 0.1


def test_surface_brightness_zero_size_is_integrated_mag():
    # Degenerate size can't take a log — fall back to the integrated mag.
    assert surface_brightness_mag(8.0, 0.0) == 8.0


def test_bright_large_heroes_are_easy():
    for mag, size in [(4.0, 85.0), (3.4, 190.0), (1.6, 110.0), (5.8, 20.0)]:
        assert tier_from_score(difficulty_score(mag, size)) == "easy"


def test_small_faint_galaxies_are_hard():
    assert tier_from_score(difficulty_score(9.4, 10.5)) == "hard"    # M74
    assert tier_from_score(difficulty_score(10.4, 15.8)) == "hard"   # NGC 4565


def test_difficulty_monotonic_in_magnitude():
    # Fainter at equal size never gets EASIER.
    assert difficulty_score(10.0, 10.0) > difficulty_score(5.0, 10.0)


def test_score_clamped_unit_interval():
    assert 0.0 <= difficulty_score(1.0, 200.0) <= 1.0
    assert 0.0 <= difficulty_score(14.0, 1.0) <= 1.0


def test_curated_override_wins_and_marks_source():
    d = difficulty_for("IC 434", 7.3, 60.0)   # Horsehead pinned hard
    assert d["tier"] == "hard"
    assert d["source"] == "curated"
    assert d["score"] is None
    assert set(CURATED) >= {"IC 434", "NGC 1499", "M33", "M101"}


def test_heuristic_row_reports_source_and_sb():
    d = difficulty_for("M42", 4.0, 85.0)
    assert d["tier"] == "easy"
    assert d["source"] == "heuristic"
    assert abs(d["surface_brightness"] - 13.4) < 0.1
    assert 0.0 <= d["score"] <= 1.0
```

**Step 1b — implement** (`server/astrodeck/catalog/difficulty.py`):
```python
"""Beginner difficulty rating for catalog targets (NOV-3).

Derives an Easy / Moderate / Hard tag from a target's integrated magnitude and
apparent size via a mean surface-brightness model, with a small curated override
table for famous objects the disk model misreads for a first-timer on an OSC.

Pure — no I/O, no astropy. Imported by objects.search_catalog and the
/api/catalog/tonight route.
"""
from __future__ import annotations

import math

DifficultyTier = str  # "easy" | "moderate" | "hard"

# --- heuristic constants (design spec §1.3; worked vectors in test_difficulty) ---
_EASY_MAG, _HARD_MAG = 6.5, 9.5      # integrated-mag ramp
_EASY_SB, _HARD_SB = 13.0, 15.5      # mean surface-brightness ramp (mag/arcmin^2)
_W_MAG, _W_SB = 0.65, 0.35           # integrated mag dominates; SB is the penalty
_EASY_MAX, _MODERATE_MAX = 0.34, 0.67

# Famous exceptions the mean-SB model misreads for a beginner (id -> tier).
CURATED: dict[str, str] = {
    "IC 434": "hard",      # Horsehead — dark nebula, needs Halpha
    "NGC 1499": "hard",    # California — very low surface brightness on an OSC
    "M33": "moderate",     # Triangulum — large but low SB, a classic letdown
    "M101": "moderate",    # Pinwheel — low-SB face-on spiral
}


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def surface_brightness_mag(mag: float, size_arcmin: float) -> float:
    """Mean surface brightness (mag/arcmin^2), size as a uniform-disk diameter.

    Larger and/or fainter -> higher (dimmer) number. A non-positive size can't
    take a log, so we fall back to the integrated magnitude (clusters/degenerate).
    """
    if size_arcmin <= 0.0:
        return mag
    area = math.pi * (size_arcmin / 2.0) ** 2  # arcmin^2
    return mag + 2.5 * math.log10(area)


def difficulty_score(mag: float, size_arcmin: float) -> float:
    """0 (dead easy) .. 1 (very hard). Weighted blend of integrated mag and SB."""
    mag_term = _clamp01((mag - _EASY_MAG) / (_HARD_MAG - _EASY_MAG))
    sb = surface_brightness_mag(mag, size_arcmin)
    sb_term = _clamp01((sb - _EASY_SB) / (_HARD_SB - _EASY_SB))
    return _W_MAG * mag_term + _W_SB * sb_term


def tier_from_score(score: float) -> str:
    if score < _EASY_MAX:
        return "easy"
    if score < _MODERATE_MAX:
        return "moderate"
    return "hard"


def difficulty_for(obj_id: str, mag: float, size_arcmin: float) -> dict:
    """Full difficulty block for a target. Curated overrides win over the
    heuristic and carry ``source="curated"`` with a ``None`` score."""
    sb = round(surface_brightness_mag(mag, size_arcmin), 1)
    if obj_id in CURATED:
        return {"tier": CURATED[obj_id], "surface_brightness": sb,
                "source": "curated", "score": None}
    score = difficulty_score(mag, size_arcmin)
    return {"tier": tier_from_score(score), "surface_brightness": sb,
            "source": "heuristic", "score": round(score, 3)}
```

**Verify:**
```
server/.venv/Scripts/pytest.exe server/tests/test_difficulty.py -n0 -q
```
Expected: `8 passed`.

---

### Task 2 — stamp difficulty onto every catalog row  ·  **Sonnet (mechanical)**

**Files:** `server/astrodeck/catalog/objects.py` (edit `search_catalog`),
`server/tests/test_catalog.py` (add one assert).

**Step 2a — extend the test** (append to `test_catalog.py`):
```python
def test_search_stamps_difficulty():
    row = search_catalog("M42")[0]
    assert row["id"] == "M42"
    assert row["difficulty"] == "easy"
    assert row["difficulty_source"] == "heuristic"
    assert isinstance(row["surface_brightness"], float)
    # a curated-override target carries its source through search too.
    horse = next(r for r in search_catalog("horsehead") if r["id"] == "IC 434")
    assert horse["difficulty"] == "hard" and horse["difficulty_source"] == "curated"
```

**Step 2b — implement** — in `objects.py`, import and stamp inside the result dict
(`objects.py:100-112`):
```python
from .difficulty import difficulty_for      # top of file, after the DSO dataclass import block
```
Then in `search_catalog`, replace the appended dict with:
```python
        d = difficulty_for(o.id, o.mag, o.size_arcmin)
        results.append({
            "id": o.id, "name": o.name,
            "type": _TYPE_NAMES[o.type],
            "ra_hours": o.ra_hours, "dec_deg": o.dec_deg,
            "mag": o.mag, "size_arcmin": o.size_arcmin,
            "difficulty": d["tier"],
            "surface_brightness": d["surface_brightness"],
            "difficulty_source": d["source"],
        })
```
(The `/api/catalog` route at `app.py:3265-3275` then also stamps `alt`/`az` on top,
unchanged — the picker/badges get difficulty for free.)

**Verify:**
```
server/.venv/Scripts/pytest.exe server/tests/test_catalog.py -n0 -q
```
Expected: `5 passed` (4 existing + 1 new).

---

### Task 3 — `tonight.py`: the tested ranking  ·  **Sonnet (mechanical)**

**Files:** `server/astrodeck/catalog/tonight.py` (new),
`server/tests/test_tonight.py` (new). Pure — synthetic night dicts, no astropy.

**Step 3a — test first** (`server/tests/test_tonight.py`):
```python
"""Tonight ranking (NOV-3) — pure, no astropy."""
from astrodeck.catalog.tonight import tonight_score, rank_picks


def _night(**kw):
    base = {"never_rises_above_limit": False, "transit_alt": 50.0, "best_window": None}
    base.update(kw)
    return base


def test_best_window_mean_alt_is_the_score():
    assert tonight_score(_night(best_window={"mean_alt": 65.0})) == 65.0


def test_no_window_uses_transit_alt():
    assert tonight_score(_night(best_window=None, transit_alt=42.0)) == 42.0


def test_never_rises_sinks_below_all_risers_but_stays_orderable():
    low = tonight_score(_night(never_rises_above_limit=True, transit_alt=12.0))
    lower = tonight_score(_night(never_rises_above_limit=True, transit_alt=5.0))
    assert low < 0 and lower < low            # higher transit ranks above lower
    assert low < tonight_score(_night(transit_alt=1.0))   # always below any riser


def test_rank_picks_orders_best_first_then_brighter():
    picks = [
        {"id": "A", "score": 40.0, "mag": 6.0},
        {"id": "B", "score": 70.0, "mag": 9.0},
        {"id": "C", "score": 70.0, "mag": 5.0},   # ties B on score, brighter -> first
    ]
    order = [p["id"] for p in rank_picks(picks)]
    assert order == ["C", "B", "A"]
```

**Step 3b — implement** (`server/astrodeck/catalog/tonight.py`):
```python
"""Rank the catalog by tonight's visibility for the beginner picker (NOV-3).

Pure ranking only — the astropy ephemeris is computed upstream by
visibility.compute_night and passed in as night dicts. Difficulty is a TAG, not a
ranking input (design spec §1.3): we rank purely by tonight's best-window altitude.
"""
from __future__ import annotations


def tonight_score(night: dict) -> float:
    """Higher = better tonight. Targets that never clear the alt limit sink below
    every riser but stay orderable by how high they get."""
    if night.get("never_rises_above_limit"):
        return -100.0 + float(night.get("transit_alt", 0.0))
    bw = night.get("best_window")
    if bw:
        return float(bw["mean_alt"])
    return float(night.get("transit_alt", 0.0))


def rank_picks(picks: list[dict]) -> list[dict]:
    """Best window first; tie-break brighter (lower mag) first."""
    return sorted(picks, key=lambda p: (-p["score"], p["mag"]))
```

**Verify:**
```
server/.venv/Scripts/pytest.exe server/tests/test_tonight.py -n0 -q
```
Expected: `4 passed`.

---

### Task 4 — `GET /api/catalog/tonight` endpoint  ·  **Sonnet (mechanical)**

**Files:** `server/astrodeck/api/app.py` (add route right after the `/api/catalog`
route, `app.py:3275`), `server/tests/test_tonight_route.py` (new).

**Step 4a — implement the route** (insert after `app.py:3275`):
```python
    @app.get("/api/catalog/tonight",
             dependencies=[Depends(require(CAP_VIEW_STATUS))])
    @declare(CAP_VIEW_STATUS)
    async def catalog_tonight(date: str | None = None, alt_limit: float = 30.0):
        """Rank the whole catalog by tonight's best-window visibility and tag each
        object with a beginner difficulty rating (NOV-3). Mirrors post_order's
        throttled fan-out of compute_night (visibility.py)."""
        from ..catalog.objects import CATALOG, _TYPE_NAMES
        from ..catalog.visibility import compute_night
        from ..catalog.tonight import tonight_score, rank_picks
        from ..catalog.difficulty import difficulty_for

        sem = asyncio.Semaphore(8)

        async def _night(o):
            async with sem:
                return await asyncio.to_thread(
                    compute_night, o.ra_hours, o.dec_deg,
                    date=date, step_min=20, alt_limit=alt_limit)

        nights = await asyncio.gather(*[_night(o) for o in CATALOG])
        picks: list[dict] = []
        for o, night in zip(CATALOG, nights):
            d = difficulty_for(o.id, o.mag, o.size_arcmin)
            bw = night["best_window"]
            picks.append({
                "id": o.id, "name": o.name, "type": _TYPE_NAMES[o.type],
                "ra_hours": o.ra_hours, "dec_deg": o.dec_deg,
                "mag": o.mag, "size_arcmin": o.size_arcmin,
                "difficulty": d["tier"],
                "surface_brightness": d["surface_brightness"],
                "difficulty_source": d["source"],
                "max_alt": night["transit_alt"],
                "transit_unix": night["transit_unix"],
                "best_window": ({"start_unix": bw["start_unix"],
                                 "end_unix": bw["end_unix"]} if bw else None),
                "moon_sep_deg": night["moon"]["separation_deg"],
                "never_rises_above_limit": night["never_rises_above_limit"],
                "score": tonight_score(night),
            })
        out_date = nights[0]["date"] if nights else (date or "")
        return {"date": out_date, "picks": rank_picks(picks),
                "site_is_default": bool(hub.site.get("is_default", False))}
```
(`asyncio`, `Depends`, `require`, `CAP_VIEW_STATUS`, `declare`, `hub` are all
already imported in app.py — verified `app.py:9` for asyncio; the catalog route
above uses the same auth stack.)

**Step 4b — endpoint test** (`server/tests/test_tonight_route.py`) — mirror
`test_visibility.py`'s minimal-app + isolated-store fixture, but mount the full
`create_app` (the route lives there). Reuse the config-isolation pattern:
```python
"""/api/catalog/tonight route (NOV-3)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from astrodeck.api.app import create_app
from astrodeck.config import ConfigStore, Site


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = ConfigStore(path=tmp_path / "astrodeck.json")
    store.set_site(Site(name="Mid", latitude=40.0, longitude=-74.0),
                   expected_version=None)
    import astrodeck.config as config_mod
    import astrodeck.hub as hub_mod
    monkeypatch.setattr(config_mod, "config_store", store)
    monkeypatch.setattr(hub_mod, "config_store", store)
    with TestClient(create_app()) as c:
        yield c


def test_tonight_ranks_and_tags(client):
    r = client.get("/api/catalog/tonight", params={"date": "2026-01-15"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["date"] and isinstance(data["picks"], list) and data["picks"]
    # ranked best-first: scores are non-increasing.
    scores = [p["score"] for p in data["picks"]]
    assert scores == sorted(scores, reverse=True)
    # every pick carries a difficulty tag + the tonight visibility fields.
    p = data["picks"][0]
    assert p["difficulty"] in {"easy", "moderate", "hard"}
    assert {"max_alt", "transit_unix", "moon_sep_deg",
            "never_rises_above_limit", "surface_brightness"} <= set(p)
    # a curated override survives to the wire.
    horse = next(x for x in data["picks"] if x["id"] == "IC 434")
    assert horse["difficulty"] == "hard" and horse["difficulty_source"] == "curated"
```
> Note for the implementer: if `create_app()` requires args/side-effects that make
> it heavy in a test (check its signature at `app.py`), fall back to the
> `test_visibility.py` approach — a bare `FastAPI()` with just the tonight route
> factored into a small `APIRouter`. The route body is identical either way; only
> the mounting differs. Confirm which by reading `create_app`'s signature first.

**Verify:**
```
server/.venv/Scripts/pytest.exe server/tests/test_tonight_route.py -n0 -q
```
Expected: `1 passed` (astropy over ~65 objects — a few seconds).

---

### Task 5 — TS types  ·  **Sonnet (mechanical)**

**Files:** `ui/src/types.ts`.

Add to `CatalogEntry` (`types.ts:353-363`), additive/optional so existing
payloads/mocks type-check:
```ts
export interface CatalogEntry {
  id: string;
  name: string;
  type: string;
  ra_hours: number;
  dec_deg: number;
  mag: number;
  size_arcmin: number;
  alt: number;
  az: number;
  // NOV-3 (additive): server-derived beginner difficulty. Optional so payloads
  // that predate it (older /api/catalog, test doubles) still type-check.
  difficulty?: "easy" | "moderate" | "hard";
  surface_brightness?: number;              // mag/arcmin^2
  difficulty_source?: "heuristic" | "curated";
}
```
And a new block (near the Atlas contracts, after `VisibilityTarget` at
`types.ts:974`):
```ts
// ------------------------------------------------ tonight picker (NOV-3)
// GET /api/catalog/tonight — the ranked "what can I image tonight?" list. Each
// pick is a CatalogEntry-shaped object plus tonight's visibility summary and a
// difficulty tag; ordered best-window-first by the server (score desc).
export interface TonightPick {
  id: string;
  name: string;
  type: string;
  ra_hours: number;
  dec_deg: number;
  mag: number;
  size_arcmin: number;
  difficulty: "easy" | "moderate" | "hard";
  surface_brightness: number;
  difficulty_source: "heuristic" | "curated";
  max_alt: number;
  transit_unix: number;
  best_window: { start_unix: number; end_unix: number } | null;
  moon_sep_deg: number;
  never_rises_above_limit: boolean;
  score: number;                            // server ranking key (visibility)
}

export interface TonightResponse {
  date: string;
  site_is_default: boolean;
  picks: TonightPick[];
}
```

**Verify:** `cd ui && npx tsc -b` → exit 0, no output.

---

### Task 6 — `lib/difficulty.ts` + tsx test  ·  **Sonnet (mechanical)**

**Files:** `ui/src/lib/difficulty.ts` (new),
`ui/src/lib/__tests__/difficulty.test.ts` (new). Mirrors `lib/visibility.ts`'s
glyph-not-hue helpers and the `eta.test.ts` harness.

**Step 6a — implement** (`ui/src/lib/difficulty.ts`):
```ts
// difficulty.ts — pure display helpers for the beginner difficulty tag (NOV-3).
// The tier is DERIVED SERVER-SIDE (catalog/difficulty.py); this module only turns
// it into a label / glyph / tone / hint. Glyph is the primary channel, tone the
// secondary one (the night palette collapses good/warn/bad toward coral — design
// spec §8), mirroring lib/visibility.ts's moonSepGlyph/moonSepTone.

import type { DifficultyTier } from "../types";
export type { DifficultyTier };

/** Tiers a first-timer should reach for by default (the picker's beginner filter). */
export const BEGINNER_TIERS: DifficultyTier[] = ["easy", "moderate"];

export function difficultyLabel(t: DifficultyTier): string {
  switch (t) {
    case "easy": return "Easy";
    case "moderate": return "Moderate";
    case "hard": return "Hard";
  }
}

/** Filled -> open circle as difficulty rises; the shape carries the meaning in
 *  night mode where tone color does not survive. */
export function difficultyGlyph(t: DifficultyTier): string {
  switch (t) {
    case "easy": return "●";       // ● filled
    case "moderate": return "◐";   // ◐ half
    case "hard": return "○";       // ○ open
  }
}

export function difficultyTone(t: DifficultyTier): "good" | "warn" | "bad" {
  switch (t) {
    case "easy": return "good";
    case "moderate": return "warn";
    case "hard": return "bad";
  }
}

export function difficultyHint(t: DifficultyTier): string {
  switch (t) {
    case "easy": return "Bright and well-sized — a great first target.";
    case "moderate": return "Doable, but dimmer or smaller — expect more subs.";
    case "hard": return "Faint or low surface brightness — for experienced rigs.";
  }
}

export function isBeginnerFriendly(t: DifficultyTier): boolean {
  return BEGINNER_TIERS.includes(t);
}
```

**Step 6b — test** (`ui/src/lib/__tests__/difficulty.test.ts`) — copy the
`test/eq/assert` harness header from `eta.test.ts:21-43`, then:
```ts
import {
  difficultyLabel, difficultyGlyph, difficultyTone, difficultyHint,
  isBeginnerFriendly, BEGINNER_TIERS, type DifficultyTier,
} from "../difficulty";

const TIERS: DifficultyTier[] = ["easy", "moderate", "hard"];

test("label maps every tier to a capitalized word", () => {
  eq(difficultyLabel("easy"), "Easy");
  eq(difficultyLabel("moderate"), "Moderate");
  eq(difficultyLabel("hard"), "Hard");
});

test("glyph/tone/hint are total over the tier union (no empty output)", () => {
  for (const t of TIERS) {
    assert(difficultyGlyph(t).length > 0, `glyph ${t}`);
    assert(["good", "warn", "bad"].includes(difficultyTone(t)), `tone ${t}`);
    assert(difficultyHint(t).length > 0, `hint ${t}`);
  }
});

test("glyphs are distinct per tier (shape is the night-mode channel)", () => {
  const g = TIERS.map(difficultyGlyph);
  eq(new Set(g).size, 3, "three distinct glyphs");
});

test("tone ramps good -> warn -> bad", () => {
  eq(difficultyTone("easy"), "good");
  eq(difficultyTone("moderate"), "warn");
  eq(difficultyTone("hard"), "bad");
});

test("beginner filter admits easy+moderate, excludes hard", () => {
  eq(isBeginnerFriendly("easy"), true);
  eq(isBeginnerFriendly("moderate"), true);
  eq(isBeginnerFriendly("hard"), false);
  eq(BEGINNER_TIERS.length, 2);
});
```
(Keep the `passed/failed/failures` footer from `eta.test.ts:187-197`.)

**Verify** (from `ui/`):
```
npx tsx src/lib/__tests__/difficulty.test.ts
```
Expected: `difficulty.test: 5/5 passed`.

---

### Task 7 — `TonightPicker` + wire into Atlas empty state  ·  **Sonnet (mechanical)**

**Files:** `ui/src/components/atlas/TonightPicker.tsx` (new),
`ui/src/views/AtlasView.tsx` (render it in `AtlasEmpty`). Thin render — no logic
tests; correctness is the typechecker + the pure helpers above.

**Step 7a — component** (`ui/src/components/atlas/TonightPicker.tsx`). Mirrors
`VisibilityPanel`'s load state machine (`VisibilityPanel.tsx:45-116`) and
`CatalogSearch`'s alt-chip + `onPick` idiom (`CatalogSearch.tsx:55-119`):
```tsx
// TonightPicker — "What can I image tonight?" (NOV-3). Fetches the server-ranked,
// difficulty-tagged catalog (GET /api/catalog/tonight) and lets a first-timer pick
// a slam-dunk. A pick calls onPick(entry) -> AtlasView passes openFraming, exactly
// like CatalogSearch. Beginner filter (Easy+Moderate) defaults ON.

import { useEffect, useMemo, useState, type JSX } from "react";
import { api, ApiError } from "../../api";
import type { CatalogEntry, TonightPick, TonightResponse } from "../../types";
import { useStore } from "../../store";
import { Icon } from "../icons";
import { EmptyState } from "../ui";
import {
  difficultyLabel, difficultyGlyph, difficultyTone, isBeginnerFriendly,
} from "../../lib/difficulty";

type Load =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; res: TonightResponse };

// A TonightPick is CatalogEntry-shaped except alt/az (the framer seeds altaz
// itself from ra/dec via openFraming). Build the entry the parent expects.
function toEntry(p: TonightPick): CatalogEntry {
  return {
    id: p.id, name: p.name, type: p.type,
    ra_hours: p.ra_hours, dec_deg: p.dec_deg,
    mag: p.mag, size_arcmin: p.size_arcmin,
    alt: p.max_alt, az: 0,
    difficulty: p.difficulty,
    surface_brightness: p.surface_brightness,
    difficulty_source: p.difficulty_source,
  };
}

export function TonightPicker({
  onPick,
}: {
  onPick: (e: CatalogEntry) => void;
}): JSX.Element {
  const altLimit = useStore((s) => s.site?.horizon_min_deg ?? 30);
  const [state, setState] = useState<Load>({ kind: "loading" });
  const [beginnerOnly, setBeginnerOnly] = useState(true);

  useEffect(() => {
    let alive = true;
    setState({ kind: "loading" });
    api
      .get<TonightResponse>(`/api/catalog/tonight?alt_limit=${encodeURIComponent(altLimit)}`)
      .then((res) => { if (alive) setState({ kind: "ok", res }); })
      .catch((e) => {
        if (!alive) return;
        setState({ kind: "error",
          message: e instanceof ApiError ? e.message : "couldn't rank tonight" });
      });
    return () => { alive = false; };
  }, [altLimit]);

  const picks = state.kind === "ok" ? state.res.picks : [];
  const shown = useMemo(
    () => (beginnerOnly ? picks.filter((p) => isBeginnerFriendly(p.difficulty)) : picks),
    [picks, beginnerOnly],
  );

  return (
    <div className="flex flex-col gap-2 w-full text-left">
      <div className="flex items-center justify-between gap-2">
        <span className="panel-title">What can I image tonight?</span>
        {/* segmented beginner filter (not a disable — a scope toggle) */}
        <div className="inline-flex text-xs border border-line2">
          {([["Beginner", true], ["All", false]] as const).map(([label, v]) => (
            <button key={label} type="button"
              aria-pressed={beginnerOnly === v}
              onClick={() => setBeginnerOnly(v)}
              className={`px-2 py-1 ${beginnerOnly === v ? "bg-raise text-ink" : "text-dim"}`}>
              {label}
            </button>
          ))}
        </div>
      </div>

      {state.kind === "ok" && state.res.site_is_default && (
        <div className="flex items-start gap-1.5 text-[12px] text-warn border border-line2 bg-black/20 px-2 py-1">
          <Icon name="alert" size={14} className="shrink-0 mt-0.5" />
          <span>Using a default location — set yours in Settings for accurate visibility.</span>
        </div>
      )}

      {state.kind === "loading" && (
        <p className="text-xs text-dim px-1 py-2">Ranking tonight…</p>
      )}
      {state.kind === "error" && (
        <p className="text-xs text-warn px-1 py-2">
          Couldn&apos;t rank tonight — {state.message}
        </p>
      )}
      {state.kind === "ok" && shown.length === 0 && (
        <EmptyState size="inline" icon="atlas" title="No beginner targets up tonight — try All." />
      )}

      {state.kind === "ok" && shown.length > 0 && (
        <ul className="flex flex-col max-h-72 overflow-y-auto border border-line2 divide-y divide-line2">
          {shown.slice(0, 20).map((p) => (
            <li key={p.id}>
              <button type="button" onClick={() => onPick(toEntry(p))}
                className="w-full text-left px-3 py-2 text-xs hover:bg-raise transition-colors flex items-center justify-between gap-2 cursor-pointer">
                <span className="min-w-0 truncate">
                  <span className="mono text-accent">{p.id}</span> {p.name}
                  <span className="text-dim"> · {p.type}</span>
                </span>
                <span className="flex items-center gap-2 shrink-0">
                  {/* difficulty badge — glyph is primary, tone secondary (§8) */}
                  <span className={`inline-flex items-center gap-1 ${
                    difficultyTone(p.difficulty) === "good" ? "text-good"
                    : difficultyTone(p.difficulty) === "warn" ? "text-warn" : "text-bad"}`}
                    title={`${difficultyLabel(p.difficulty)} · surface brightness ${p.surface_brightness} mag/arcmin²${p.difficulty_source === "curated" ? " (curated)" : ""}`}>
                    <span aria-hidden>{difficultyGlyph(p.difficulty)}</span>
                    {difficultyLabel(p.difficulty)}
                  </span>
                  {/* peak-alt chip — CatalogSearch idiom (CatalogSearch.tsx:109) */}
                  <span className={`mono ${
                    p.never_rises_above_limit ? "text-warn"
                    : p.max_alt > 40 ? "text-good" : p.max_alt < 20 ? "text-warn" : "text-dim"}`}
                    title={p.never_rises_above_limit
                      ? `Never rises above ${Math.round(altLimit)}° tonight`
                      : "Peak altitude tonight"}>
                    {p.never_rises_above_limit ? "low " : "↑"}{p.max_alt.toFixed(0)}°
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default TonightPicker;
```
> Implementer: confirm `text-bad` exists as a utility (grep `text-bad` in
> `ui/src`); `text-good`/`text-warn`/`text-dim` are used throughout
> (`CatalogSearch.tsx:109`). If `text-bad` is absent, use `text-warn` for the hard
> tier's tone (glyph still distinguishes it). Confirm `Icon name="alert"` and
> `EmptyState size="inline"` (`ui.tsx:495-501`) — both used already in AtlasView.

**Step 7b — wire into `AtlasEmpty`** (`AtlasView.tsx:96-122`). Add the import and
render the picker in the action block:
```tsx
import { TonightPicker } from "../components/atlas/TonightPicker";
```
Inside `AtlasEmpty`'s `action` (after the existing `<CatalogSearch …/>` and
free-roam button, `AtlasView.tsx:110-117`), add:
```tsx
              <div className="w-full mt-2 pt-3 border-t border-line2">
                <TonightPicker onPick={onPick} />
              </div>
```
`AtlasEmpty` already receives `onPick` (`AtlasView.tsx:100`) wired to `openFraming`
at the call site (`AtlasView.tsx:404`), so picks open a framing session unchanged.
(Widen the empty card so the list fits: change `max-w-md` at `AtlasView.tsx:105`
to `max-w-xl`.)

**Verify:**
```
cd ui && npx tsc -b
```
Expected: exit 0, no output. Then re-run the tsx test from Task 6 to confirm no
regression, and the Task 1/3/4 pytest files.

**Full-suite gate (after all tasks):**
```
server/.venv/Scripts/pytest.exe -n0 -q     # or default -n auto; expect all green
cd ui && npx tsc -b
```

---

## 4. OPEN DECISIONS

1. **Difficulty as a tag/filter vs. a ranking input.**
   *Recommendation:* keep ranking **purely by visibility** and use difficulty as a
   tag + beginner filter (as specified). The brief says "rank by tonight's
   best-window visibility and tag each object" — mixing difficulty into the score
   would bury a well-placed Moderate under a poorly-placed Easy. The filter already
   gives the novice the slam-dunk.

2. **Placement: Atlas empty state only, vs. its own "Tonight" nav view.**
   *Recommendation:* ship the empty-state placement now (zero nav/store churn,
   minimal slice). Defer a dedicated nav tab; if user testing shows people want it
   after a session is open, add a header "Tonight's picks" button that clears
   `framing` (a new `store.closeFraming`) to return to the empty state — a small
   follow-up, not this slice.

3. **Curated override list size.**
   *Recommendation:* ship the 4 documented corrections (Horsehead, California, M33,
   M101) and grow it only from real "this rating is wrong" feedback. The heuristic
   already nails the clear cases (M42/M31/M45/M13 easy; M74/NGC 4565 hard); a large
   override table would defeat the point of a heuristic and rot silently.

4. **Endpoint cost (astropy over ~65 objects per call).**
   *Recommendation:* accept it for v1 — `Semaphore(8)` + `to_thread` keeps the Pi's
   event loop free and it's a once-per-visit call, not a poll (the picker fetches
   on mount only, keyed on `altLimit`). If it proves heavy on the box, add a short
   TTL cache keyed on `(date, alt_limit, site)` in a follow-up; not needed now.

5. **`TonightPick` vs. reusing `CatalogEntry` + `VisibilityTarget`.**
   *Recommendation:* keep the dedicated `TonightPick` (a flat superset). Composing
   two interfaces would force `alt/az` onto picks (the server doesn't compute
   instantaneous az here) and split the difficulty fields awkwardly. One flat shape
   matches the endpoint's flat JSON and the picker's needs.

6. **Endpoint test mounting: `create_app()` vs. a factored router.**
   *Recommendation:* try `create_app()` first (Task 4b); if its construction is
   heavy/side-effectful, factor the route into a tiny `APIRouter` and mount it bare
   like `test_visibility.py`. Decide by reading `create_app`'s signature — do not
   guess.
