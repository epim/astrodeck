# Atlas Reliability & Performance (Wave 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Sky Atlas fast and trustworthy: pans track the pointer with zero fetch storms, the survey image never vanishes, "Survey offline" heals itself with backoff, the optics warning names the real missing fields and is fixable in place, tooltips stop flickering app-wide, and the server survey proxy gains snap-to-grid caching + single-flight + a 6 s timeout.

**Architecture:** Server snaps request geometry onto a FOV-scaled grid (integer bucket indices → cache keys; snapped values → upstream fetch AND `X-Survey-*` response headers). The client's new fetch-based loader reads those headers and compensates the ≤ fov/40 residual with a CSS transform — the same transform that slides the last-good frame under the pointer during a drag. Availability becomes a self-healing `surveyDegraded` flag (AtlasView) + backoff retry (SkyCanvas), never a mode flip.

**Tech Stack:** FastAPI + httpx (async) server; React 18 + zustand 5 + Tailwind UI; pytest (server, from `server/`); self-executing `npx tsx` assert files (UI — NO vitest).

**Spec:** `docs/superpowers/specs/2026-07-12-atlas-reliability-design.md` (amended 2026-07-13). Grounded findings: `docs/superpowers/reviews/2026-07-12-atlas-plan-ux-review.md`. Verbatim seams: `.superpowers/sdd/seams/wave1-{client-survey,ui-lib,server}.md`.

## Global Constraints

- Server tests run from `server/`: `/c/Users/bear/astro/server/.venv/Scripts/python.exe -m pytest tests/test_survey.py -q` (task-scoped) and the full suite before the final commit of the wave. KNOWN HARNESS ISSUE: if a long command gets auto-backgrounded, re-run it immediately and stay foreground; no monitor notifications exist.
- UI tests are self-executing assert files: `npx tsx src/lib/__tests__/<name>.test.ts` run from `ui/`. They print `<name>.test: N/N passed` and are NOT executed by CI (CI only builds) — you MUST run them yourself and paste the output.
- UI build gate: `npm run build` from `ui/` (`tsc -b && vite build`) must pass for every UI task.
- 503 response shape is FROZEN: `{"detail":{"detail":"survey unavailable","fallback":"schematic"}}` (HTTPException detail).
- RA is in HOURS on the client API (`ra_deg = ra * 15` server-side). J2000 everywhere; never mix JNow.
- Response headers (exact names): `X-Survey-Ra-Deg`, `X-Survey-Dec-Deg`, `X-Survey-Fov-Deg` — snapped values, degrees, formatted `%.6f`.
- Snap grid: fov snapped to a 2 % log grid (`round(ln(fov)/ln(1.02))`), center step = `snapped_fov / 20` degrees both axes, cache keys built from INTEGER bucket indices + `"v2"` salt.
- Tooltip bubble keeps `pointer-events-none`. Timings: open delay 100 ms, close grace 250 ms, backoff 5 s→10 s→20 s→40 s→cap 60 s, debounces 300 ms.
- Commit per task, message style `fix(atlas): …` / `feat(atlas): …` / `perf(ui): …`, each commit ends with the two standard trailers (Co-Authored-By: Claude Fable 5 <noreply@anthropic.com> + Claude-Session line).
- Do not touch `/api/framing/mosaic`, eviction logic beyond what's shown, or any Wave 2/3 surface (rotation handle, search, Plan/schedule).

---

### Task 1: Server — FOV-scaled snap-to-grid cache + snapped headers + 6 s timeout

**Files:**
- Modify: `server/astrodeck/catalog/survey.py`
- Test: `server/tests/test_survey.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `_snap_geometry(ra_hours: float, dec_deg: float, fov_deg: float) -> tuple[float, float, float, tuple[int, int, int]]` (snapped ra_deg, dec_deg, fov_deg, (ra_idx, dec_idx, fov_idx)); `_cache_key(idx: tuple[int,int,int], width: int, survey: str, stretch: str) -> str`; module constants `_FOV_LOG_STEP = 1.02`, `_CENTER_STEPS_PER_FOV = 20`, `_KEY_SALT = "v2"`, `_TIMEOUT_S = 6.0`; response headers `X-Survey-Ra-Deg/-Dec-Deg/-Fov-Deg` on hit AND miss. Task 5's client reads these headers.

- [ ] **Step 1: Update the tests first (rewrite the quantization test, add snap/header/timeout tests)**

In `server/tests/test_survey.py`, REPLACE `test_ra_hours_to_degrees_and_no_rot` and `test_cache_key_quantization_is_stable` with the versions below, and APPEND the three new tests. Everything else in the file stays byte-identical.

```python
def test_ra_hours_to_degrees_and_no_rot(client):
    # ra=1.0h must become ~15.0 deg (then grid-snapped); TAN + icrs; NO rot param.
    r = client.get("/api/survey/cutout.jpg", params={"ra": 1.0, "dec": 41.0, "fov": 1.5})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    p = _FakeClient.last_params
    assert p is not None
    snapped_fov = float(p["fov"])
    step = snapped_fov / survey_mod._CENTER_STEPS_PER_FOV
    # hours -> degrees, then snapped to the fov-scaled grid (within half a step)
    assert abs(float(p["ra"]) - 15.0) <= step / 2 + 1e-9
    assert p["projection"] == "TAN"
    assert p["coordsys"] == "icrs"
    assert p["format"] == "jpg"
    assert "rot" not in p  # spec: no PA param
```

```python
def test_cache_key_quantization_is_stable():
    # Coords inside one fov/20 bucket collapse onto ONE key; a >1-step nudge
    # does not. Keys are pure functions of INTEGER indices — no float flip.
    keys = set()
    for i in range(100):
        *_geom, idx = survey_mod._snap_geometry(0.712300 + i * 1e-7, 41.270, 1.5)
        keys.add(survey_mod._cache_key(idx, 768, "CDS/P/DSS2/color", "linear"))
    assert len(keys) == 1

    _ra, _dec, fov, idx1 = survey_mod._snap_geometry(1.0, 40.0, 1.5)
    step = fov / survey_mod._CENTER_STEPS_PER_FOV
    *_g2, idx2 = survey_mod._snap_geometry(1.0 + (step * 0.4) / 15.0, 40.0, 1.5)
    *_g3, idx3 = survey_mod._snap_geometry(1.0 + (step * 1.5) / 15.0, 40.0, 1.5)
    assert idx1 == idx2       # within one step -> same bucket
    assert idx1 != idx3       # 1.5 steps away -> different bucket
    k1 = survey_mod._cache_key(idx1, 768, "CDS/P/DSS2/color", "linear")
    k3 = survey_mod._cache_key(idx3, 768, "CDS/P/DSS2/color", "linear")
    assert k1 != k3
```

```python
def test_snapped_geometry_headers_on_miss_and_hit(client):
    params = {"ra": 1.0, "dec": 41.0, "fov": 1.5}
    r1 = client.get("/api/survey/cutout.jpg", params=params)
    assert r1.status_code == 200
    ra1 = float(r1.headers["x-survey-ra-deg"])
    dec1 = float(r1.headers["x-survey-dec-deg"])
    fov1 = float(r1.headers["x-survey-fov-deg"])
    step = fov1 / survey_mod._CENTER_STEPS_PER_FOV
    assert abs(ra1 - 15.0) <= step / 2 + 1e-9
    assert abs(dec1 - 41.0) <= step / 2 + 1e-9
    assert abs(fov1 - 1.5) / 1.5 <= 0.011          # 2% log grid -> within ~1%
    # upstream was asked for EXACTLY the snapped geometry the header reports
    p = _FakeClient.last_params
    assert float(p["ra"]) == pytest.approx(ra1)
    assert float(p["dec"]) == pytest.approx(dec1)
    assert float(p["fov"]) == pytest.approx(fov1)
    # cache hit returns identical headers (and no network — force it to fail)
    _FakeClient.fail = True
    r2 = client.get("/api/survey/cutout.jpg", params=params)
    assert r2.status_code == 200
    assert r2.headers["x-survey-ra-deg"] == r1.headers["x-survey-ra-deg"]
    assert r2.headers["x-survey-dec-deg"] == r1.headers["x-survey-dec-deg"]
    assert r2.headers["x-survey-fov-deg"] == r1.headers["x-survey-fov-deg"]


def test_snap_clamps_dec_and_wraps_ra():
    ra_deg, dec_deg, _fov, _idx = survey_mod._snap_geometry(23.9999, 89.999, 2.0)
    assert 0.0 <= ra_deg < 360.0
    assert -90.0 <= dec_deg <= 90.0


def test_timeout_and_salt_constants():
    # 6 s x 2 attempts + 0.4 s sleep ~= 12.8 s worst case (was ~30.4 s).
    assert survey_mod._TIMEOUT_S == 6.0
    assert survey_mod._KEY_SALT == "v2"   # orphans pre-snap cache entries
```

- [ ] **Step 2: Run the tests to verify the new/changed ones fail**

Run from `server/`: `/c/Users/bear/astro/server/.venv/Scripts/python.exe -m pytest tests/test_survey.py -q`
Expected: FAIL — `AttributeError: ... has no attribute '_CENTER_STEPS_PER_FOV'` (and friends). Pre-existing tests still pass.

- [ ] **Step 3: Implement the snap in survey.py**

3a. Add `import math` after `import hashlib` (line 41 area).

3b. Change line 57 `_TIMEOUT_S = 15.0` to:

```python
_TIMEOUT_S = 6.0  # was 15.0 — 2 attempts ~= 12.8 s worst case; single-flight stops pileup
```

3c. REPLACE the quantization block (lines 80-111: the `_Q_RA_H/_Q_DEC_DEG/_Q_FOV_DEG` constants, `_quantize`, and the old `_cache_key`) with:

```python
# Snap-to-grid caching (Wave-1 spec §5.2): request geometry is SNAPPED onto a
# FOV-scaled grid BEFORE keying and BEFORE the upstream fetch, so the cached
# image always matches its key exactly. The snapped geometry is returned in
# X-Survey-* response headers; the client compensates the <= fov/40 residual
# with a CSS transform (ui/src/lib/surveyView.ts) — zero framing-accuracy loss.
#
# Keys are built from INTEGER bucket indices — never reconstructed floats — so
# two requests in the same bucket can never disagree at a float boundary (the
# pre-snap double-bucket bug, review 2026-07-12 Symptom 1 cause 3).
_FOV_LOG_STEP = 1.02          # fov snapped to a 2% logarithmic grid
_CENTER_STEPS_PER_FOV = 20    # center grid step = snapped_fov / 20 (deg, both axes)
_KEY_SALT = "v2"              # orphan pre-snap cache files (TTL eviction cleans them)


def _snap_geometry(
    ra_hours: float, dec_deg: float, fov_deg: float
) -> tuple[float, float, float, tuple[int, int, int]]:
    """Snap (ra HOURS, dec, fov) onto the fov-scaled grid.

    Returns (ra_deg, dec_deg, fov_deg) SNAPPED — ra converted hours->DEGREES
    (the unit-critical *15, spec §4.3) and wrapped to [0,360), dec clamped to
    [-90,90] — plus the integer bucket indices (ra_idx, dec_idx, fov_idx).
    The RA step is a fixed sky-plane step (no cos-dec scaling): buckets get
    finer in true angle near the poles, which costs cache hits, never accuracy.
    """
    fov_idx = round(math.log(fov_deg) / math.log(_FOV_LOG_STEP))
    snapped_fov = _FOV_LOG_STEP ** fov_idx
    step = snapped_fov / _CENTER_STEPS_PER_FOV
    ra_deg = (ra_hours * 15.0) % 360.0  # <-- HOURS -> DEGREES (unit-critical)
    ra_idx = round(ra_deg / step)
    dec_idx = round(dec_deg / step)
    snapped_ra = (ra_idx * step) % 360.0
    snapped_dec = max(-90.0, min(90.0, dec_idx * step))
    return snapped_ra, snapped_dec, snapped_fov, (ra_idx, dec_idx, fov_idx)


def _cache_key(idx: tuple[int, int, int], width: int, survey: str, stretch: str) -> str:
    """SHA-1 over the INTEGER bucket indices + discrete params — the disk key."""
    ra_idx, dec_idx, fov_idx = idx
    raw = f"{_KEY_SALT}|{survey}|{ra_idx}|{dec_idx}|{fov_idx}|{width}|{stretch}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()
```

3d. In `survey_cutout` (route body, lines 247-271), REPLACE from `width = max(...)` down to the `params = _hips2fits_params(...)` line with:

```python
    width = max(_WIDTH_MIN, min(_WIDTH_MAX, width))
    snapped_ra, snapped_dec, snapped_fov, idx = _snap_geometry(ra, dec, fov)

    key = _cache_key(idx, width, survey, stretch)
    cache_path = _SURVEY_CACHE_DIR / f"{key}.jpg"
    cache_headers = {
        "Cache-Control": f"max-age={_CACHE_MAX_AGE}",
        # Snapped geometry (spec Wave-1 §5.2) — the client reads these to place
        # the frame exactly (residual compensated by a CSS transform).
        "X-Survey-Ra-Deg": f"{snapped_ra:.6f}",
        "X-Survey-Dec-Deg": f"{snapped_dec:.6f}",
        "X-Survey-Fov-Deg": f"{snapped_fov:.6f}",
    }

    if cache_path.exists():
        return FileResponse(cache_path, media_type="image/jpeg", headers=cache_headers)

    params = _hips2fits_params(snapped_ra, snapped_dec, snapped_fov, width, survey, stretch)
```

(The old standalone `ra_deg = ra * 15.0` line is deleted — the conversion now lives in `_snap_geometry`. The `try/except RuntimeError -> 503`, `asyncio.to_thread(_write_cache, ...)`, and final `Response(...)` lines are unchanged and keep using `cache_headers`.)

- [ ] **Step 4: Run the survey tests**

`/c/Users/bear/astro/server/.venv/Scripts/python.exe -m pytest tests/test_survey.py -q`
Expected: ALL PASS (including the untouched eviction/503/width tests).

- [ ] **Step 5: Commit**

```bash
git add server/astrodeck/catalog/survey.py server/tests/test_survey.py
git commit -m "fix(survey): FOV-scaled snap-to-grid cache, integer-index keys, snapped X-Survey-* headers, 6s timeout (wave-1 §5.1-5.2)"
```

---

### Task 2: Server — single-flight per cache key

**Files:**
- Modify: `server/astrodeck/catalog/survey.py`
- Test: `server/tests/test_survey.py`

**Interfaces:**
- Consumes: Task 1's `_cache_key`/`cache_headers` route shape.
- Produces: `_single_flight(key: str)` async context manager; module dict `_inflight: dict[str, list]` (key -> `[asyncio.Lock, refcount]`), empty when idle.

- [ ] **Step 1: Write the failing tests (append to test_survey.py)**

```python
# ---------------------------------------------------------- single-flight

class _GatedClient(_FakeClient):
    """Counts upstream GETs and holds them until `gate` is set."""
    calls = 0
    gate: "asyncio.Event | None" = None

    async def get(self, url, params=None):
        _GatedClient.calls += 1
        _FakeClient.last_params = params
        if _GatedClient.gate is not None:
            await _GatedClient.gate.wait()
        return _FakeResponse()


def _asgi_client(monkeypatch, tmp_path):
    monkeypatch.setattr(survey_mod, "_SURVEY_CACHE_DIR", tmp_path / "_survey")
    monkeypatch.setattr(survey_mod.httpx, "AsyncClient", _GatedClient)
    _GatedClient.calls = 0
    _GatedClient.gate = None
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(survey_mod.router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_single_flight_coalesces_identical_requests(tmp_path, monkeypatch):
    import asyncio
    _GatedClient.gate = asyncio.Event()
    async with _asgi_client(monkeypatch, tmp_path) as c:
        p = {"ra": 1.0, "dec": 41.0, "fov": 1.5}
        t1 = asyncio.create_task(c.get("/api/survey/cutout.jpg", params=p))
        t2 = asyncio.create_task(c.get("/api/survey/cutout.jpg", params=p))
        await asyncio.sleep(0.05)       # both in flight; one holds the key lock
        _GatedClient.gate.set()
        r1, r2 = await asyncio.gather(t1, t2)
    assert r1.status_code == 200 and r2.status_code == 200
    assert _GatedClient.calls == 1      # exactly one upstream fetch
    assert not survey_mod._inflight     # lock registry drained


async def test_single_flight_distinct_keys_fetch_independently(tmp_path, monkeypatch):
    import asyncio
    async with _asgi_client(monkeypatch, tmp_path) as c:
        r1, r2 = await asyncio.gather(
            c.get("/api/survey/cutout.jpg", params={"ra": 1.0, "dec": 41.0, "fov": 1.5}),
            c.get("/api/survey/cutout.jpg", params={"ra": 1.0, "dec": 41.0, "fov": 3.0}),
        )
    assert r1.status_code == 200 and r2.status_code == 200
    assert _GatedClient.calls == 2
    assert not survey_mod._inflight
```

(`asyncio_mode = "auto"` in `server/pyproject.toml` runs these async tests directly. `import asyncio` at the top of the file already exists? It does NOT — add `import asyncio` to the file's imports instead of the local imports if you prefer; either works.)

- [ ] **Step 2: Run to verify failure**

`/c/Users/bear/astro/server/.venv/Scripts/python.exe -m pytest tests/test_survey.py -q -k single_flight`
Expected: FAIL — `AttributeError: ... no attribute '_inflight'` and/or `calls == 2`.

- [ ] **Step 3: Implement single-flight in survey.py**

3a. Add to imports: `from contextlib import asynccontextmanager`.

3b. After the snap-grid block (Task 1's code), add:

```python
# ------------------------------------------------------------- single-flight
# Concurrent requests for the SAME cache key coalesce: one goes upstream, the
# rest wait on the per-key lock and then serve the file it cached. Entries are
# refcounted and dropped when the last waiter leaves (no unbounded growth).
# Event-loop-only state: the dict is only mutated between awaits, so no extra
# guard lock is needed. NOTE: if the leader's fetch fails (503), each waiter
# retries upstream itself in turn — a failure is never cached.
_inflight: dict[str, list] = {}  # key -> [asyncio.Lock, refcount]


@asynccontextmanager
async def _single_flight(key: str):
    entry = _inflight.get(key)
    if entry is None:
        entry = [asyncio.Lock(), 0]
        _inflight[key] = entry
    entry[1] += 1
    try:
        async with entry[0]:
            yield
    finally:
        entry[1] -= 1
        if entry[1] <= 0:
            _inflight.pop(key, None)
```

3c. In `survey_cutout`, wrap everything AFTER the first `if cache_path.exists(): return FileResponse(...)` in the context manager, with a second cache check inside (a coalesced waiter finds the file the leader just wrote):

```python
    async with _single_flight(key):
        if cache_path.exists():
            return FileResponse(cache_path, media_type="image/jpeg", headers=cache_headers)

        params = _hips2fits_params(snapped_ra, snapped_dec, snapped_fov, width, survey, stretch)
        try:
            body = await _fetch_cutout(params)
        except RuntimeError:
            # Upstream unreachable/slow/blank — honest 503 so the client keeps its
            # last good frame and retries with backoff (no longer a mode flip).
            raise HTTPException(
                status_code=503,
                detail={"detail": "survey unavailable", "fallback": "schematic"},
            )

        # Persist off the event loop (small image, but disk I/O shouldn't block).
        await asyncio.to_thread(_write_cache, cache_path, body)
        return Response(body, media_type="image/jpeg", headers=cache_headers)
```

- [ ] **Step 4: Run the full survey test file**

`/c/Users/bear/astro/server/.venv/Scripts/python.exe -m pytest tests/test_survey.py -q`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add server/astrodeck/catalog/survey.py server/tests/test_survey.py
git commit -m "fix(survey): single-flight per cache key — concurrent identical requests coalesce to one upstream fetch (wave-1 §5.3)"
```

---

### Task 3: UI lib — `surveyView.ts` transform math + tests

**Files:**
- Create: `ui/src/lib/surveyView.ts`
- Test: `ui/src/lib/__tests__/surveyView.test.ts`

**Interfaces:**
- Consumes: `project()` from `ui/src/lib/framing.ts` (exact TAN forward projection, xi=East+/eta=North+ in degrees; RA args in HOURS).
- Produces: `interface SurveyGeom { raDeg: number; decDeg: number; fovDeg: number }`; `surveyTransform(shown: SurveyGeom, view: SurveyGeom, renderedWidthPx: number): { dx: number; dy: number; scale: number }`. Task 5 renders `translate(${dx}px, ${dy}px) scale(${scale})` with `transform-origin: center`.

- [ ] **Step 1: Create `ui/src/lib/surveyView.ts`**

```ts
// surveyView — pure math mapping the last-fetched survey frame onto the live
// Atlas view (Wave-1 spec §1.2). NO React, no DOM: importable by the npx-tsx
// assert tests (rotatorDial.ts precedent).
//
// The <img> shows a TAN cutout centered at `shown` (geometry from the server's
// X-Survey-* headers — snapped, so it differs from the request by <= fov/40).
// While the user pans/zooms, the live view center `view` drifts away from
// `shown`; this transform slides/scales the existing frame so the sky tracks
// the pointer with ZERO fetches until the settled fetch swaps a new frame in.
//
// Screen convention (matches SkyCanvas panTo): North up, East LEFT — a sky
// point at (xi, eta) degrees from center renders at
//   (centerPx - xi*pxPerDeg, centerPx - eta*pxPerDeg).
// To bring `view`'s center (offset (xi,eta) from `shown`'s center) to the
// canvas center, the frame translates by (+xi, +eta) * pxPerDeg-of-the-VIEW —
// the scale factor folds in because CSS applies scale() about center first.

import { project } from "./framing";

export interface SurveyGeom {
  raDeg: number;   // frame center RA, DEGREES (header is degrees; /15 for project)
  decDeg: number;
  fovDeg: number;  // frame angular width
}

export function surveyTransform(
  shown: SurveyGeom,
  view: SurveyGeom,
  renderedWidthPx: number,
): { dx: number; dy: number; scale: number } {
  // Exact TAN offsets of the view center relative to the shown frame's tangent
  // point — project() handles RA wrap and cos-dec inherently.
  const { xi, eta } = project(
    view.raDeg / 15, view.decDeg,
    shown.raDeg / 15, shown.decDeg,
  );
  const pxPerDeg = renderedWidthPx / view.fovDeg;
  return {
    dx: xi * pxPerDeg,
    dy: eta * pxPerDeg,
    scale: shown.fovDeg / view.fovDeg,
  };
}
```

- [ ] **Step 2: Create `ui/src/lib/__tests__/surveyView.test.ts`**

Use the repo's standard self-executing harness (mirrors `telemetry.test.ts`):

```ts
// surveyView.test.ts — transform math for the pan/zoom-tracking survey frame.
// Run with:  npx tsx src/lib/__tests__/surveyView.test.ts   (from ui/)

import { surveyTransform, type SurveyGeom } from "../surveyView";

let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) {
    failed++; failures.push(`x ${name}: ${(e as Error).message}`);
  }
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}
function near(a: number, b: number, tol: number, msg: string): void {
  assert(Math.abs(a - b) <= tol, `${msg} (got ${a}, want ${b} ±${tol})`);
}

const W = 768;
const g = (raDeg: number, decDeg: number, fovDeg: number): SurveyGeom =>
  ({ raDeg, decDeg, fovDeg });

test("identity: same geometry -> no transform", () => {
  const t = surveyTransform(g(150, 20, 2), g(150, 20, 2), W);
  near(t.dx, 0, 1e-9, "dx"); near(t.dy, 0, 1e-9, "dy"); near(t.scale, 1, 1e-12, "scale");
});

test("pure zoom: shown fov 2°, view fov 1° -> scale 2, no translate", () => {
  const t = surveyTransform(g(150, 20, 2), g(150, 20, 1), W);
  near(t.dx, 0, 1e-9, "dx"); near(t.dy, 0, 1e-9, "dy"); near(t.scale, 2, 1e-12, "scale");
});

test("view north of shown -> frame slides DOWN (dy positive)", () => {
  // view center 0.5° north; eta ~= +0.5 -> dy ~= 0.5 * 768/2 = 192
  const t = surveyTransform(g(150, 20, 2), g(150, 20.5, 2), W);
  near(t.dy, 192, 2, "dy"); near(t.dx, 0, 2, "dx");
});

test("view east of shown at dec 60 -> cos-dec-scaled dx, positive (E is screen-left)", () => {
  // +1° of RA at dec 60 is ~cos(60°)=0.5° on the sky -> dx ~= 0.5 * 768/2 = 192
  const t = surveyTransform(g(150, 60, 2), g(151, 60, 2), W);
  near(t.dx, 192, 4, "dx");  // TAN-exact vs small-angle: allow ~2%
});

test("RA wrap: shown 359.9°, view 0.1° -> small positive dx, not a full-circle jump", () => {
  const t = surveyTransform(g(359.9, 0, 2), g(0.1, 0, 2), W);
  near(t.dx, 0.2 * (W / 2), 1, "dx");   // 0.2° east -> ~76.8 px
  assert(Math.abs(t.dx) < 200, "wrap must not explode");
});

test("residual scale from fov snapping stays tiny", () => {
  // server snaps fov on a 2% grid -> worst residual ~1%
  const t = surveyTransform(g(150, 20, 1.4859), g(150, 20, 1.5), W);
  assert(Math.abs(t.scale - 1) < 0.011, `scale residual ${t.scale}`);
});

const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nsurveyView.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}
export const result = { passed, failed, total };
```

- [ ] **Step 3: Run the test**

From `ui/`: `npx tsx src/lib/__tests__/surveyView.test.ts`
Expected: `surveyView.test: 6/6 passed`.

- [ ] **Step 4: Build gate**

From `ui/`: `npm run build` — expected: success.

- [ ] **Step 5: Commit**

```bash
git add ui/src/lib/surveyView.ts ui/src/lib/__tests__/surveyView.test.ts
git commit -m "feat(atlas): surveyView.ts — pure TAN transform mapping the shown survey frame onto the live view (wave-1 §1.2)"
```

---

### Task 4: UI — tooltip state machine + Tooltip rewrite (all ~24 sites inherit)

**Files:**
- Create: `ui/src/lib/tooltipMachine.ts`
- Modify: `ui/src/components/ui.tsx` (Tooltip only, lines 342-396; InfoDot and all call sites unchanged)
- Test: `ui/src/lib/__tests__/tooltipMachine.test.ts`

**Interfaces:**
- Produces: `tooltipNext(s: TooltipState, ev: TooltipEvent): TooltipState`, `TOOLTIP_IDLE`, `TOOLTIP_OPEN_DELAY_MS = 100`, `TOOLTIP_CLOSE_GRACE_MS = 250`.
- The bubble keeps `pointer-events-none`; `InfoDot`'s `-m-[15px] p-[15px]` halo unchanged.

- [ ] **Step 1: Create `ui/src/lib/tooltipMachine.ts`**

```ts
// tooltipMachine — pure open/close state machine for components/ui.tsx Tooltip.
// Split out (rotatorDial.ts precedent) so the npx-tsx assert tests can import
// it under plain Node — no React, no DOM.
//
// Why a machine (Wave-1 spec §4): the old Tooltip opened on mouseenter AND
// toggled on click, so a touch tap (which synthesizes mouseenter+click)
// flashed open-then-shut; and a mouse drifting off the 15px halo closed
// instantly under an unhoverable bubble. Mouse hover now runs through grace
// timers; touch taps toggle exactly once.

export type TooltipEvent =
  | "enter-mouse" | "leave-mouse"   // hover (pointerType === "mouse" only)
  | "tap"                            // touch/pen pointerup
  | "focus" | "blur"                 // keyboard focus (pointer-induced focus suppressed)
  | "escape" | "outside"             // dismissal
  | "open-timer" | "close-timer";    // grace-timer expiry

export interface TooltipState {
  open: boolean;
  pendingOpen: boolean;   // mouse entered; waiting out the open delay
  pendingClose: boolean;  // mouse left while open; waiting out the close grace
}

export const TOOLTIP_IDLE: TooltipState = { open: false, pendingOpen: false, pendingClose: false };
export const TOOLTIP_OPEN_DELAY_MS = 100;
export const TOOLTIP_CLOSE_GRACE_MS = 250;

export function tooltipNext(s: TooltipState, ev: TooltipEvent): TooltipState {
  switch (ev) {
    case "enter-mouse":
      // Re-enter during the close grace keeps it open (kills the drift flicker).
      if (s.open) return { open: true, pendingOpen: false, pendingClose: false };
      return { open: false, pendingOpen: true, pendingClose: false };
    case "open-timer":
      return s.pendingOpen ? { open: true, pendingOpen: false, pendingClose: false } : s;
    case "leave-mouse":
      if (s.pendingOpen) return TOOLTIP_IDLE;
      if (s.open) return { open: true, pendingOpen: false, pendingClose: true };
      return s;
    case "close-timer":
      return s.pendingClose ? TOOLTIP_IDLE : s;
    case "tap":
      // One tap = one toggle. (The old mouseenter+click pair fired both.)
      return { open: !s.open, pendingOpen: false, pendingClose: false };
    case "focus":
      return { open: true, pendingOpen: false, pendingClose: false };
    case "blur":
    case "escape":
    case "outside":
      return TOOLTIP_IDLE;
    default:
      return s;
  }
}
```

- [ ] **Step 2: Create `ui/src/lib/__tests__/tooltipMachine.test.ts`** (same harness shape as Task 3's test file — `test`/`assert` helpers + final `console.log` report; transcribe them again):

```ts
// tooltipMachine.test.ts — Run with: npx tsx src/lib/__tests__/tooltipMachine.test.ts
import { tooltipNext, TOOLTIP_IDLE, type TooltipState } from "../tooltipMachine";

let passed = 0; let failed = 0; const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

function run(events: string[], from: TooltipState = TOOLTIP_IDLE): TooltipState {
  return events.reduce((s, ev) => tooltipNext(s, ev as never), from);
}

test("tap toggles open exactly once (no flash)", () => {
  const s1 = run(["tap"]);
  assert(s1.open, "first tap opens");
  const s2 = run(["tap"], s1);
  assert(!s2.open, "second tap closes");
});

test("mouse enter waits out the open delay", () => {
  const s = run(["enter-mouse"]);
  assert(!s.open && s.pendingOpen, "not open until the timer fires");
  assert(run(["open-timer"], s).open, "open after the delay");
});

test("graze: enter then leave before the delay never opens", () => {
  const s = run(["enter-mouse", "leave-mouse"]);
  assert(!s.open && !s.pendingOpen && !s.pendingClose, "back to idle");
  assert(!tooltipNext(s, "open-timer").open, "stale open-timer is a no-op");
});

test("drift: leave then re-enter within the grace stays open (the flicker fix)", () => {
  const open = run(["enter-mouse", "open-timer"]);
  const grace = run(["leave-mouse"], open);
  assert(grace.open && grace.pendingClose, "still open during grace");
  const back = run(["enter-mouse"], grace);
  assert(back.open && !back.pendingClose, "re-enter cancels the close");
  assert(tooltipNext(back, "close-timer").open, "stale close-timer is a no-op");
});

test("leave without re-enter closes after the grace", () => {
  const s = run(["enter-mouse", "open-timer", "leave-mouse", "close-timer"]);
  assert(!s.open, "closed after grace expiry");
});

test("escape / outside / blur close from any open state", () => {
  for (const ev of ["escape", "outside", "blur"] as const) {
    assert(!run(["tap", ev]).open, `${ev} closes`);
  }
});

test("focus opens immediately (keyboard)", () => {
  assert(run(["focus"]).open, "focus opens");
});

const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\ntooltipMachine.test: ${passed}/${total} passed`);
if (failures.length) { console.error(failures.join("\n")); }
export const result = { passed, failed, total };
```

- [ ] **Step 3: Run the test**

From `ui/`: `npx tsx src/lib/__tests__/tooltipMachine.test.ts` — expected `tooltipMachine.test: 7/7 passed`.

- [ ] **Step 4: Rewrite `Tooltip` in ui.tsx**

4a. Extend the react import (ui.tsx:12-15) with `useReducer`:

```tsx
import {
  useEffect, useId, useReducer, useRef, useState, type ReactNode, type PointerEvent as RPointerEvent,
  type KeyboardEvent as RKeyboardEvent,
} from "react";
```

4b. Add below the existing imports:

```tsx
import {
  tooltipNext, TOOLTIP_IDLE, TOOLTIP_OPEN_DELAY_MS, TOOLTIP_CLOSE_GRACE_MS,
} from "../lib/tooltipMachine";
```

4c. REPLACE the whole `Tooltip` function (lines 342-396, from the `/* ===== UI-TOOLTIP` comment through the closing brace before the InfoDot comment) with:

```tsx
/* ============================================================ UI-TOOLTIP
   Mouse hover runs through hover-intent grace timers (lib/tooltipMachine.ts);
   touch/pen taps toggle exactly once; keyboard focus opens; Escape/outside-tap
   closes. Viewport-clamped bubble, role=tooltip, pointer-events-none bubble,
   >=44px focusable hit area on the trigger. */
export function Tooltip({ content, children, side = "top" }: {
  content: ReactNode; children: ReactNode; side?: "top" | "bottom" | "left" | "right";
}) {
  const [st, dispatch] = useReducer(tooltipNext, TOOLTIP_IDLE);
  const id = useId();
  const ref = useRef<HTMLSpanElement>(null);
  // Suppress the focus-opens path when focus was pointer-induced (a touch tap
  // focuses the span THEN fires pointerup — without this, tap = open+toggle).
  const pointerDownAtRef = useRef(0);

  // Grace timers: the machine sets pending flags; these effects fire the expiry
  // events. State changes re-run the effect, cancelling stale timers.
  useEffect(() => {
    if (!st.pendingOpen) return;
    const t = window.setTimeout(() => dispatch("open-timer"), TOOLTIP_OPEN_DELAY_MS);
    return () => window.clearTimeout(t);
  }, [st.pendingOpen]);
  useEffect(() => {
    if (!st.pendingClose) return;
    const t = window.setTimeout(() => dispatch("close-timer"), TOOLTIP_CLOSE_GRACE_MS);
    return () => window.clearTimeout(t);
  }, [st.pendingClose]);

  useEffect(() => {
    if (!st.open) return;
    const onDoc = (e: Event) => { if (ref.current && !ref.current.contains(e.target as Node)) dispatch("outside"); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") dispatch("escape"); };
    document.addEventListener("pointerdown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("pointerdown", onDoc); document.removeEventListener("keydown", onKey); };
  }, [st.open]);

  const pos: Record<string, string> = {
    top: "bottom-full left-1/2 -translate-x-1/2 mb-1.5",
    bottom: "top-full left-1/2 -translate-x-1/2 mt-1.5",
    left: "right-full top-1/2 -translate-y-1/2 mr-1.5",
    right: "left-full top-1/2 -translate-y-1/2 ml-1.5",
  };

  return (
    <span ref={ref} className="relative inline-flex">
      <span
        tabIndex={0}
        role="button"
        aria-describedby={st.open ? id : undefined}
        aria-expanded={st.open}
        className="inline-flex items-center cursor-help"
        onPointerEnter={(e: RPointerEvent<HTMLSpanElement>) => { if (e.pointerType === "mouse") dispatch("enter-mouse"); }}
        onPointerLeave={(e: RPointerEvent<HTMLSpanElement>) => { if (e.pointerType === "mouse") dispatch("leave-mouse"); }}
        onPointerDown={() => { pointerDownAtRef.current = Date.now(); }}
        onPointerUp={(e: RPointerEvent<HTMLSpanElement>) => { if (e.pointerType !== "mouse") dispatch("tap"); }}
        onFocus={() => { if (Date.now() - pointerDownAtRef.current > 400) dispatch("focus"); }}
        onBlur={() => dispatch("blur")}
        onClick={(e) => e.stopPropagation()}
      >
        {children}
      </span>
      {st.open && (
        <span
          id={id}
          role="tooltip"
          className={`panel absolute z-50 ${pos[side]} px-2.5 py-2 text-[11px] leading-snug text-ink
            w-max max-w-[min(240px,90vw)] pointer-events-none`}
        >
          {content}
        </span>
      )}
    </span>
  );
}
```

(`InfoDot` below it is untouched. If `useState` becomes unused in ui.tsx after this change, tsc will say so — only then remove it from the import.)

- [ ] **Step 5: Build gate**

From `ui/`: `npm run build` — expected: success.

- [ ] **Step 6: Commit**

```bash
git add ui/src/lib/tooltipMachine.ts ui/src/lib/__tests__/tooltipMachine.test.ts ui/src/components/ui.tsx
git commit -m "fix(ui): tooltip hover-intent state machine — tap toggles once, mouse drift gets a close grace; all InfoDot/hint sites inherit (wave-1 §4)"
```

---

### Task 5: Client survey pipeline — SkyCanvas fetch loader + transform-pan + keep-last-good; AtlasView degraded state

**Files:**
- Modify: `ui/src/components/atlas/SkyCanvas.tsx` (survey-loading internals + render; pointer/keyboard/zoom handlers untouched)
- Modify: `ui/src/views/AtlasView.tsx` (surveyDown → surveyDegraded; mode never flips on failure; stable callbacks)
- Test: build gate (`npm run build`); pure math already tested in Task 3

**Interfaces:**
- Consumes: Task 3's `surveyTransform`/`SurveyGeom`; Task 1's `X-Survey-*` headers (graceful fallback to requested geometry when absent).
- Produces: new optional `SkyCanvasProps.surveyDegraded?: boolean`; `onSurveyError`/`onSurveyLoad` semantics change to "settled fetch failed / succeeded" (AtlasView maps them to the degraded flag, NOT to mode).

- [ ] **Step 1: SkyCanvas — imports and state**

1a. Add import: `import { surveyTransform, type SurveyGeom } from "../../lib/surveyView";`

1b. In `SkyCanvasProps`, replace the `mode` doc comment and add the new prop after `imageBrightness`:

```tsx
  /** survey | schematic — schematic is now ONLY the user's explicit choice. */
  mode: "survey" | "schematic";
  /** Per-image brightness dimmer 0.08..1 (SurveyControls slider). */
  imageBrightness?: number;
  /** Last settled fetch failed; last good frame stays up while retries run. */
  surveyDegraded?: boolean;
```

and update the two callback doc comments:

```tsx
  /** Fired when a settled survey fetch fails (AtlasView sets surveyDegraded). */
  onSurveyError?: () => void;
  /** Fired when a survey frame loads OK (AtlasView clears surveyDegraded). */
  onSurveyLoad?: () => void;
```

Destructure `surveyDegraded = false` alongside the other props.

1c. REPLACE the state block (lines 107-113: `imgReady`, `loading`, `everLoaded`, `shownUrl`, `debounceRef`) with:

```tsx
  const [slowLoad, setSlowLoad] = useState(false);   // settled fetch in flight > 300 ms
  const [everLoaded, setEverLoaded] = useState(false);

  // Last good frame: object URL + the geometry it was fetched at (from the
  // X-Survey-* headers). The frame stays mounted through failures/gestures;
  // surveyTransform() maps it onto the live view until the next swap.
  const [shownUrl, setShownUrl] = useState<string | null>(null);
  const [shownGeom, setShownGeom] = useState<SurveyGeom | null>(null);
  const shownUrlRef = useRef<string | null>(null);   // for unmount revocation
  const debounceRef = useRef<number | null>(null);
  const genRef = useRef(0);                          // stale-response guard
  const abortRef = useRef<AbortController | null>(null);
  const retryRef = useRef<{ timer: number | null; attempt: number }>({ timer: null, attempt: 0 });
```

- [ ] **Step 2: SkyCanvas — the loader (insert after the `targetUrl` memo, replacing the old fetch effect at lines 149-181 AND the `setImgReady(false)` effect)**

```tsx
  // Fetch-based loader (Wave-1 spec §1.1): abortable, generation-guarded, swaps
  // only after decode. Reads the server's snapped-geometry headers so the
  // residual (<= fov/40 after server §5.2) is compensated by the transform below.
  const loadSurvey = useCallback((url: string, fallback: SurveyGeom) => {
    const gen = ++genRef.current;
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    const slowTimer = window.setTimeout(() => {
      if (gen === genRef.current) setSlowLoad(true);
    }, 300);
    void (async () => {
      try {
        const res = await fetch(url, { signal: ac.signal });
        if (!res.ok) throw new Error(`survey ${res.status}`);
        const geom: SurveyGeom = {
          raDeg: Number(res.headers.get("x-survey-ra-deg") ?? fallback.raDeg),
          decDeg: Number(res.headers.get("x-survey-dec-deg") ?? fallback.decDeg),
          fovDeg: Number(res.headers.get("x-survey-fov-deg") ?? fallback.fovDeg),
        };
        const blob = await res.blob();
        const blobUrl = URL.createObjectURL(blob);
        // Decode before swap — double-buffer semantics, no flash of a half-
        // decoded frame. decode() rejection is benign (frame still usable).
        const img = new Image();
        img.src = blobUrl;
        try { await img.decode(); } catch { /* ok */ }
        if (gen !== genRef.current) {
          URL.revokeObjectURL(blobUrl);
          return;
        }
        retryRef.current.attempt = 0;
        setShownUrl((prev) => {
          if (prev) URL.revokeObjectURL(prev);
          return blobUrl;
        });
        shownUrlRef.current = blobUrl;
        setShownGeom(geom);
        setSlowLoad(false);
        setEverLoaded(true);
        onSurveyLoad?.();
      } catch {
        if (gen !== genRef.current) return; // aborted by a newer load — not a failure
        setSlowLoad(false);
        // Self-healing retry with backoff (spec §1.4): 5s -> 10s -> ... cap 60s.
        // Any new settled view cancels this and fetches immediately instead.
        const attempt = retryRef.current.attempt + 1;
        retryRef.current.attempt = attempt;
        const delay = Math.min(60_000, 5_000 * 2 ** (attempt - 1));
        retryRef.current.timer = window.setTimeout(() => {
          if (gen === genRef.current) loadSurvey(url, fallback);
        }, delay);
        onSurveyError?.();
      } finally {
        window.clearTimeout(slowTimer);
      }
    })();
  }, [onSurveyError, onSurveyLoad]);

  // Settled-fetch scheduler: 300 ms debounce (unchanged cadence). A new view is
  // always a fresh chance — pending backoff retries are cancelled first.
  useEffect(() => {
    if (mode === "schematic") {
      setSlowLoad(false);
      return;
    }
    if (retryRef.current.timer != null) {
      window.clearTimeout(retryRef.current.timer);
      retryRef.current.timer = null;
    }
    retryRef.current.attempt = 0;
    if (debounceRef.current != null) window.clearTimeout(debounceRef.current);
    const fallback: SurveyGeom = {
      raDeg: center.ra_hours * 15,
      decDeg: center.dec_deg,
      fovDeg: fovZoomDeg,
    };
    debounceRef.current = window.setTimeout(() => loadSurvey(targetUrl, fallback), 300);
    return () => {
      if (debounceRef.current != null) window.clearTimeout(debounceRef.current);
    };
  }, [targetUrl, mode, loadSurvey, center.ra_hours, center.dec_deg, fovZoomDeg]);

  // A survey-source change must not keep showing the previous survey's frame.
  useEffect(() => {
    genRef.current++;
    abortRef.current?.abort();
    if (retryRef.current.timer != null) window.clearTimeout(retryRef.current.timer);
    retryRef.current.attempt = 0;
    setShownUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
    shownUrlRef.current = null;
    setShownGeom(null);
    setEverLoaded(false);
  }, [survey]);

  // Unmount: kill in-flight work + timers, release the object URL.
  useEffect(() => () => {
    genRef.current++;
    abortRef.current?.abort();
    if (retryRef.current.timer != null) window.clearTimeout(retryRef.current.timer);
    if (shownUrlRef.current) URL.revokeObjectURL(shownUrlRef.current);
  }, []);

  // CSS transform mapping the last-fetched frame onto the live view (spec §1.2):
  // pans track the pointer with zero fetches; the settled fetch swaps in a
  // re-centered frame and the transform collapses back toward identity.
  const imgTransform = useMemo(() => {
    if (!shownGeom) return undefined;
    const t = surveyTransform(
      shownGeom,
      { raDeg: center.ra_hours * 15, decDeg: center.dec_deg, fovDeg: fovZoomDeg },
      boxPx,
    );
    if (Math.abs(t.dx) < 0.01 && Math.abs(t.dy) < 0.01 && Math.abs(t.scale - 1) < 1e-4) {
      return undefined;
    }
    return `translate(${t.dx.toFixed(2)}px, ${t.dy.toFixed(2)}px) scale(${t.scale.toFixed(4)})`;
  }, [shownGeom, center.ra_hours, center.dec_deg, fovZoomDeg, boxPx]);
```

- [ ] **Step 3: SkyCanvas — render changes**

3a. Delete the `dimmerOn` line (`const dimmerOn = mode === "survey" && (!imgReady || loading);`).

3b. REPLACE the survey `<img>` + schematic blocks (lines 323-341) with:

```tsx
        {/* 1. survey image — the LAST GOOD frame stays through failures/gestures
              (keep-last-good, spec §1.3); the transform tracks the live view. */}
        {mode === "survey" && shownUrl && (
          <img
            src={shownUrl}
            alt=""
            aria-hidden
            draggable={false}
            className="survey absolute inset-0 w-full h-full object-cover"
            // Brightness rides the CSS var (see .survey rule); the transform is
            // the pan/zoom tracker — never animate it (it must follow 1:1).
            style={{
              ["--survey-bright" as string]: imageBrightness,
              transform: imgTransform,
              transformOrigin: "center",
            } as CSSProperties}
          />
        )}

        {/* schematic backdrop: explicit user choice OR no frame fetched yet */}
        {(mode === "schematic" || !shownUrl) && (
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_50%_40%,#10131b,#04060a)]" aria-hidden />
        )}
```

3c. REPLACE the sr-only status span content (lines 345-351) with:

```tsx
        <span className="sr-only" role="status" aria-live="polite">
          {mode === "schematic"
            ? "Schematic framing"
            : surveyDegraded
              ? `Survey unreachable, retrying, showing ${shownUrl ? "last image" : "schematic"}`
              : slowLoad
                ? "Loading survey"
                : ""}
        </span>
```

3d. REPLACE the always-present night dimmer (lines 353-358) with (fixed levels — loading no longer blacks out the frame, spec §1.5; the night red filter still applies at first paint via the `.survey` CSS rule, so no bright flash reaches a dark-adapted eye):

```tsx
        <div
          className="absolute inset-0 bg-black pointer-events-none transition-opacity duration-200"
          style={{ opacity: night ? 0.18 : 0 }}
          aria-hidden
        />
```

3e. First-load skeleton condition (line 361) becomes `{mode === "survey" && !everLoaded && !shownUrl && (`; the subsequent-loads chip condition (line 368) becomes `{mode === "survey" && everLoaded && slowLoad && (` (chip text stays `LOADING…`).

3f. REPLACE the below-canvas offline banner (lines 453-457) with:

```tsx
      {mode === "survey" && surveyDegraded && (
        <div className="text-[12px] text-warn border border-line2 bg-black/30 px-2 py-1">
          ⚠ Survey unreachable — {shownUrl ? "showing the last image" : "schematic framing"}; retrying automatically.
        </div>
      )}
      {mode === "schematic" && (
        <div className="text-[12px] text-dim border border-line2 bg-black/30 px-2 py-1">
          Schematic framing: sizes approximate, can't preview nebula shape.
        </div>
      )}
```

3g. Header comment (file top, lines 11-26): update bullets 1-2 to describe the fetch-based loader + keep-last-good + fixed night dimmer (no more "ALWAYS-PRESENT night dimmer gated off after onLoad+rAF"). Keep the layering list shape.

- [ ] **Step 4: AtlasView — degraded semantics**

4a. Line 130 block: rename state (comment updated):

```tsx
  // Survey fetch failure -> degraded (last good frame stays up; SkyCanvas
  // retries with backoff). NEVER flips the view to schematic (wave-1 §1.4).
  const [surveyDegraded, setSurveyDegraded] = useState(false);
```

4b. REPLACE the reset effect (lines 160-164) with:

```tsx
  // A survey-source change is a fresh chance — clear the degraded flag.
  useEffect(() => {
    setSurveyDegraded(false);
  }, [framing?.survey]);
```

4c. REPLACE the mode derivation (line 243-244) with:

```tsx
  const mode: "survey" | "schematic" = survey === "schematic" ? "schematic" : "survey";
```

4d. Add stable callbacks (near the other handlers; CRITICAL — inline arrows here would re-arm SkyCanvas's debounce effect on every AtlasView render and strangle backoff retries):

```tsx
  // Stable identities: SkyCanvas's fetch effect depends on these via loadSurvey.
  const onSurveyError = useCallback(() => setSurveyDegraded(true), []);
  const onSurveyLoad = useCallback(() => setSurveyDegraded(false), []);
```

4e. In the `<SkyCanvas>` render (lines 483-501), replace the two inline arrows and add the new prop:

```tsx
            surveyDegraded={surveyDegraded}
            onSurveyError={onSurveyError}
            onSurveyLoad={onSurveyLoad}
```

- [ ] **Step 5: Build gate + tsx regression**

From `ui/`: `npm run build` — expected success. Also re-run `npx tsx src/lib/__tests__/surveyView.test.ts` (still 6/6).

- [ ] **Step 6: Commit**

```bash
git add ui/src/components/atlas/SkyCanvas.tsx ui/src/views/AtlasView.tsx
git commit -m "fix(atlas): fetch loader w/ abort+generation guard, transform-pan, keep-last-good frame, self-healing degraded state w/ backoff (wave-1 §1)"
```

---

### Task 6: VisibilityPanel — debounced, keep-chart-while-refetching fetch

**Files:**
- Modify: `ui/src/components/atlas/VisibilityPanel.tsx` (the fetch effect only, lines 62-90)
- Test: build gate

**Interfaces:**
- Consumes: nothing new. Props unchanged.
- Produces: nothing new downstream. Fetch fires ≥300 ms after the last change of the ROUNDED key (ra 3 dp ≈ 54″, dec 2 dp = 36″).

- [ ] **Step 1: Replace the fetch effect (lines 62-90) with:**

```tsx
  // Rounded fetch key (wave-1 §2): sub-arcminute drift must not refire the
  // server-side astropy ephemeris. 0.001 h ≈ 54″ RA; 0.01° = 36″ dec — both far
  // below anything visible on a tonight-scale chart.
  const keyRa = Math.round(ra_hours * 1000) / 1000;
  const keyDec = Math.round(dec_deg * 100) / 100;

  useEffect(() => {
    let alive = true;
    // 300 ms debounce (matches the survey debounce): a drag fires ONE request
    // per settle, not one per pointer-move tick. The previous chart stays up
    // while refetching — the loading skeleton only shows before the first data.
    const timer = window.setTimeout(() => {
      setState((prev) => (prev.kind === "ok" ? prev : { kind: "loading" }));
      nowRef.current = Date.now() / 1000;
      const url =
        `/api/visibility?ra=${encodeURIComponent(keyRa)}` +
        `&dec=${encodeURIComponent(keyDec)}` +
        `&alt_limit=${encodeURIComponent(altLimit)}`;
      api
        .get<VisibilityNight>(url)
        .then((night) => {
          if (!alive) return;
          setState({ kind: "ok", night });
          onNight?.(night);
        })
        .catch((e) => {
          if (!alive) return;
          const message =
            e instanceof ApiError ? e.message : "couldn't compute visibility";
          setState({ kind: "error", message });
          onNight?.(null);
        });
    }, 300);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
    // onNight intentionally omitted — it's a stable lift callback; re-fetch only
    // on the (rounded) target/limit or an explicit retry.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keyRa, keyDec, altLimit, reloadKey]);
```

- [ ] **Step 2: Build gate**

From `ui/`: `npm run build` — expected success.

- [ ] **Step 3: Commit**

```bash
git add ui/src/components/atlas/VisibilityPanel.tsx
git commit -m "fix(atlas): debounce visibility fetch (300ms, rounded key) and keep the chart while refetching (wave-1 §2)"
```

---

### Task 7: Optics truth — merged gating, truthful warning, in-place editor, one banner

**Files:**
- Modify: `ui/src/lib/framing.ts` (add `OpticsLike` + `missingOpticsFields`)
- Modify: `ui/src/views/AtlasView.tsx` (merged optics; warning copy; editor fields; drop `useStatus()`)
- Modify: `ui/src/components/atlas/SkyCanvas.tsx` (widen `optics` prop type to `OpticsLike`; delete the in-canvas "Set focal length above…" banner at lines 445-449 — the header banner is the single warning)
- Test: `ui/src/lib/__tests__/missingOptics.test.ts` + build gate

**Interfaces:**
- Consumes: `OpticsComputed` on `status.optics` (server `effective_optics()` — camera-merged; `optics_computed` on config as fallback); `PUT /api/optics` already accepts all four fields via the pydantic `Optics` model (0 = "use camera") — NO server change needed.
- Produces: `export interface OpticsLike { focal_length_mm: number; pixel_size_um: number; sensor_width_px: number; sensor_height_px: number }` and `export function missingOpticsFields(o: OpticsLike | null | undefined): string[]` in framing.ts. `SkyCanvasProps.optics` becomes `OpticsLike | null`.

**This task is judgment-bearing (JSX placement in AtlasView's header cluster): read the current header/editor region of AtlasView.tsx (roughly lines 400-480) before editing.**

- [ ] **Step 1: framing.ts — add below `FovFromOptics`:**

```ts
/** The 4 fields the FOV math needs — config Optics and camera-merged
 *  OpticsComputed both satisfy it structurally. */
export interface OpticsLike {
  focal_length_mm: number;
  pixel_size_um: number;
  sensor_width_px: number;
  sensor_height_px: number;
}

/** Human names of whichever optics fields are still missing (<= 0), for the
 *  warning banner (wave-1 §3.2). Empty array == optics usable. */
export function missingOpticsFields(o: OpticsLike | null | undefined): string[] {
  if (!o) return ["focal length", "pixel size", "sensor size"];
  const missing: string[] = [];
  if (!(o.focal_length_mm > 0)) missing.push("focal length");
  if (!(o.pixel_size_um > 0)) missing.push("pixel size");
  if (!(o.sensor_width_px > 0) || !(o.sensor_height_px > 0)) missing.push("sensor size");
  return missing;
}
```

Also change `fovFromOptics`' inline parameter type to `OpticsLike | null | undefined` (same shape — pure rename, no behavior change).

- [ ] **Step 2: Test file `ui/src/lib/__tests__/missingOptics.test.ts`** (standard harness — transcribe the `test`/`assert` helpers from Task 3's file):

```ts
// missingOptics.test.ts — Run with: npx tsx src/lib/__tests__/missingOptics.test.ts
import { missingOpticsFields } from "../framing";
// ... test/assert harness (as in surveyView.test.ts) ...

test("null optics -> all three named", () => {
  assert(missingOpticsFields(null).join(",") === "focal length,pixel size,sensor size", "all missing");
});
test("complete optics -> empty", () => {
  assert(missingOpticsFields({ focal_length_mm: 530, pixel_size_um: 3.76, sensor_width_px: 6248, sensor_height_px: 4176 }).length === 0, "none missing");
});
test("camera-pending optics -> pixel + sensor named, focal not", () => {
  const m = missingOpticsFields({ focal_length_mm: 530, pixel_size_um: 0, sensor_width_px: 0, sensor_height_px: 0 });
  assert(m.join(",") === "pixel size,sensor size", `got ${m.join(",")}`);
});
test("half a sensor is still a missing sensor", () => {
  const m = missingOpticsFields({ focal_length_mm: 530, pixel_size_um: 3.76, sensor_width_px: 6248, sensor_height_px: 0 });
  assert(m.join(",") === "sensor size", `got ${m.join(",")}`);
});
```

Run: `npx tsx src/lib/__tests__/missingOptics.test.ts` — expected 4/4.

- [ ] **Step 3: AtlasView — merged optics gate (replaces the raw-config gate)**

3a. Imports: add `missingOpticsFields, type OpticsLike` to the framing.ts import; add `import { useShallow } from "zustand/react/shallow";`; REMOVE `useStatus` from the store import.

3b. Replace `const status = useStatus();` with:

```tsx
  // Camera-merged optics (server effective_optics; same source FocusView uses).
  // useShallow: the dict is rebuilt every WS tick but its fields are primitives.
  const statusOptics = useStore(useShallow((s) => s.status?.optics));
```

3c. After `const computed = config?.optics_computed ?? null;` (line 148) add:

```tsx
  // Merge for the FOV gate (wave-1 §3.1): config override wins (nonzero), else
  // the camera-reported value from the live merged readout. Focal stays the
  // config/draft value (focalOverride still applies via fovFromOptics).
  const liveOptics = statusOptics ?? computed;
  const mergedOptics: OpticsLike | null = useMemo(() => {
    if (!optics) return null;
    return {
      focal_length_mm: optics.focal_length_mm,
      pixel_size_um: optics.pixel_size_um || liveOptics?.pixel_size_um || 0,
      sensor_width_px: optics.sensor_width_px || liveOptics?.sensor_width_px || 0,
      sensor_height_px: optics.sensor_height_px || liveOptics?.sensor_height_px || 0,
    };
  }, [optics, liveOptics]);
```

3d. The `fov` memo (line 172-176) now uses `mergedOptics`:

```tsx
  const fov = useMemo(
    () => fovFromOptics(mergedOptics, focalOverride),
    [mergedOptics, focalOverride],
  );
```

3e. `recenter` (lines 259-266): replace `const m = status?.mount;` with `const m = useStore.getState().status?.mount;` (event-handler read — no subscription).

3f. The `<SkyCanvas optics={optics}` prop becomes `optics={mergedOptics}`.

- [ ] **Step 4: AtlasView — truthful warning banner (replace lines 468-477):**

```tsx
      {/* no-optics CTA banner — names the ACTUAL missing fields (wave-1 §3.2) */}
      {!haveOptics && (
        <div className="flex items-start gap-1.5 text-[12px] text-warn border border-line2 bg-black/20 px-2 py-1">
          <Icon name="alert" size={14} className="shrink-0 mt-0.5" />
          <span>
            Framing needs your optics — missing{" "}
            {missingOpticsFields(mergedOptics).join(", ")}. Set them here, or
            connect your camera to fill pixel/sensor automatically.
          </span>
        </div>
      )}
```

- [ ] **Step 5: AtlasView — pixel-size + sensor editor beside the focal field**

Add a generic commit helper next to `commitFocalValue` (same toast/version/reload pattern):

```tsx
  // Commit any optics field(s): PUT /api/optics then re-GET config. Empty/0
  // means "use camera" (server merge, config.py). Same optimistic-concurrency
  // version token as the focal committer.
  const commitOpticsPatch = useCallback(
    async (patch: Partial<Optics>) => {
      if (!optics) return;
      try {
        const next: Optics = { ...optics, ...patch };
        await api.put("/api/optics", { optics: next, version: config?.version ?? null });
        await loadConfig();
      } catch (e) {
        enqueueToast({
          level: "error",
          title: "Couldn't save optics",
          detail: (e as Error).message,
        });
      }
    },
    [optics, config?.version, loadConfig, enqueueToast],
  );
```

Then add three numeric inputs in the header cluster next to the existing focal-length field (match its markup/classes exactly — read the surrounding JSX first):
- "Pixel size (µm)" → commits `{ pixel_size_um: n }` on blur/Enter (`n >= 0`; empty commits `0`).
- "Sensor W (px)" / "Sensor H (px)" → commit `{ sensor_width_px: n }` / `{ sensor_height_px: n }` (integers ≥ 0; empty commits `0`).
- Each field: when the config value is `0` AND the camera reports one (`liveOptics.<field> > 0` with `liveOptics.source === "camera"` or `"mixed"`), show the camera value as the input placeholder and a small `from camera` chip (`<span className="text-[10px] text-dim border border-line2 px-1">from camera</span>`) beside the label.
- Draft-state pattern: one `useState<string>` per field seeded from config (same shape as `focalDraft`, including the reseed-on-config-change effect).
- Keep every control ≥44 px touch target (`.field`/`.btn-touch` conventions used by the focal field).

- [ ] **Step 6: SkyCanvas — prop type + banner removal**

6a. `SkyCanvasProps.optics` becomes:

```tsx
  optics: OpticsLike | null; // camera-MERGED 4-field optics (AtlasView builds it)
```

with `import type { OpticsLike } from "../../lib/framing";` added (and drop the now-unused `Optics` import from types if tsc flags it).

6b. DELETE the in-canvas warning block (lines 444-449, `{!haveOptics && (<div ... Set focal length above ...`) — the AtlasView header banner is now the single optics warning (wave-1 §3.4).

- [ ] **Step 7: Run tests + build**

From `ui/`: `npx tsx src/lib/__tests__/missingOptics.test.ts` (4/4) and `npm run build` (success).

- [ ] **Step 8: Commit**

```bash
git add ui/src/lib/framing.ts ui/src/lib/__tests__/missingOptics.test.ts ui/src/views/AtlasView.tsx ui/src/components/atlas/SkyCanvas.tsx
git commit -m "fix(atlas): gate optics on camera-merged values, name the actual missing fields, add pixel/sensor in-place editor, single banner (wave-1 §3)"
```

---

### Task 8: Perf hygiene — memo FovOverlay/VisibilityPanel, rounded panel props

**Files:**
- Modify: `ui/src/components/atlas/FovOverlay.tsx` (memo wrap)
- Modify: `ui/src/components/atlas/VisibilityPanel.tsx` (memo wrap)
- Modify: `ui/src/views/AtlasView.tsx` (pass VisibilityPanel rounded coords)
- Test: build gate

**Interfaces:** consumes Task 6's rounded-key convention (same rounding at the call site). No new interfaces.

- [ ] **Step 1: FovOverlay memo**

```tsx
import { memo, type JSX } from "react";
```

and change the export to:

```tsx
export const FovOverlay = memo(function FovOverlay(props: FovOverlayProps): JSX.Element {
  ...unchanged body...
});
```

(All props are primitives/nulls — memo is fully effective: the SVG grid no longer re-renders while SkyCanvas state churns during loads.)

- [ ] **Step 2: VisibilityPanel memo**

Add `memo` to the react import; change the export to `export const VisibilityPanel = memo(function VisibilityPanel({ ... }: VisibilityPanelProps) { ... });` keeping `export default VisibilityPanel;` at the bottom. (`onNight` is a `useState` setter in AtlasView — identity-stable, so memo holds.)

- [ ] **Step 3: AtlasView — rounded props at the mount site (lines 647-652):**

```tsx
          {/* rounded to the panel's own fetch key so pans don't re-render it */}
          <VisibilityPanel
            ra_hours={Math.round(center.ra_hours * 1000) / 1000}
            dec_deg={Math.round(center.dec_deg * 100) / 100}
            altLimit={site?.horizon_min_deg ?? 30}
            onNight={setVisNight}
          />
```

- [ ] **Step 4: Build + commit**

`npm run build` (success), then:

```bash
git add ui/src/components/atlas/FovOverlay.tsx ui/src/components/atlas/VisibilityPanel.tsx ui/src/views/AtlasView.tsx
git commit -m "perf(atlas): memo FovOverlay/VisibilityPanel + rounded panel props — pans and WS ticks stop re-rendering static geometry (wave-1 §6)"
```

---

## Final verification (after Task 8, before the whole-branch review)

- [ ] Full server suite from `server/`: `/c/Users/bear/astro/server/.venv/Scripts/python.exe -m pytest -q` — expected: all pass (~930+). KNOWN HARNESS ISSUE: if auto-backgrounded, re-run immediately and stay foreground.
- [ ] All UI tsx tests from `ui/`: `npx tsx src/lib/__tests__/surveyView.test.ts && npx tsx src/lib/__tests__/tooltipMachine.test.ts && npx tsx src/lib/__tests__/missingOptics.test.ts` — all green.
- [ ] `npm run build` from `ui/` — success.
