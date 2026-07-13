# Offline-First Survey Pack — Design Spec

**Date:** 2026-07-13
**Status:** Approved by user (design conversation 2026-07-13)
**Owner surfaces:** `server/astrodeck/catalog/` (survey pipeline), Settings UI, Atlas UI

## 0. Goal and context

The Atlas center frame must render sky-survey imagery with **zero network** at the
scope. Today `survey.py` proxies CDS `hips2fits`; a CDS outage (observed 2026-07-13:
TLS handshake failures to all four CDS hosts) leaves the Atlas on the first-load
skeleton with the "Survey unreachable" banner, because the Wave-1 `v2` cache salt
orphaned all older cutouts and every first view needs upstream.

This feature **inverts the pipeline to offline-first** (user decision):

1. A local HiPS tile pack (DSS2 color, orders 0–4, ≈250 MB) becomes the **primary**
   render source.
2. The CDS `hips2fits` upstream becomes an **opt-in enhancement** — a Settings
   toggle, **disabled by default** — used only where it adds resolution (small FOV).
3. The pack is acquired by a **fetch-once downloader** (CLI + Settings button) from
   public HiPS mirrors. The repo and releases ship **no survey imagery** (DSS2 is
   © AAO/STScI; end-user fetch to their own device avoids redistribution).

Verified ground facts (2026-07-13, ESA mirror `https://skies.esac.esa.int/DSSColor`):
`properties` reports `hips_tile_width=512`, `hips_tile_format=jpeg`,
`hips_frame=equatorial`, `hips_order=9`, `hips_order_min=0`. Tile sizes measured:
order 3 ≈ 33 KB, order 4 ≈ 60–100 KB. Orders 0–4 = 12+48+192+768+3072 = **4,092
tiles ≈ 250 MB**.

### Frozen contracts (non-negotiable, unchanged by this feature)

* `GET /api/survey/cutout.jpg` query shape; `ra` in **hours** (`ra_deg = ra*15`).
* TAN projection, square cutout, North-up, **no rotation** — output orientation
  must match what hips2fits JPEGs deliver today (the client's `framing.ts` /
  `surveyView.ts` math was verified against those).
* Snapped-geometry response headers `X-Survey-Ra-Deg` / `X-Survey-Dec-Deg` /
  `X-Survey-Fov-Deg` on every 200 (hit and miss).
* Failure shape: `503 {"detail": "survey unavailable", "fallback": "schematic"}`.
* Snap-to-grid bucketing (`_snap_geometry`, integer bucket-index keys) unchanged.
* **Existing upstream cache keys unchanged** (salt `v2`, no source component) so the
  currently-warm cache keeps serving. Pack renders use a distinct salt (§4).

## 1. Pack format and location

* **Layout:** standard HiPS directory tree, verbatim mirror structure:
  `Norder{k}/Dir{(npix // 10000) * 10000}/Npix{npix}.jpg` for k = 0..4,
  npix = 0 .. 12·4^k − 1, plus the mirror's `properties` file at the pack root.
* **Location:** `CAPTURE_DIR / "_survey_pack" / "dss2color"`. Same writable volume
  as captures and the `_survey` cutout cache. The `_evict_cache` bound in
  `survey.py` operates only on `CAPTURE_DIR/_survey/*.jpg` and **must never touch
  `_survey_pack`** (it already can't — different directory — but a test pins it).
* **Manifest:** on successful fetch completion the downloader writes
  `pack.json` at the pack root:
  `{"survey": "CDS/P/DSS2/color", "slug": "dss2color", "order": 4,
    "tile_width": 512, "tile_count": 4092, "fetched_at": <unix>,
    "bytes": <total>}`.
  "Pack present" (for routing and the status API) ≡ `pack.json` exists and parses.
  The renderer itself tolerates a partial tree (missing tile → black fill, §3);
  the manifest only gates whether routing *tries* the pack.
* **Survey mapping:** v1 packs exactly one survey. Internal map
  `_PACK_SLUGS = {"CDS/P/DSS2/color": "dss2color"}`. `DSS2/red` and `2MASS/color`
  remain upstream-only in v1 (out of scope §9).
* `tile_width` is **read from `properties` / `pack.json`** (default 512 if the key
  is absent) — never hardcoded in the renderer.

## 2. Fetcher (`server/astrodeck/catalog/survey_pack.py`)

New module owning acquisition, manifest, and status. No FastAPI imports — the API
layer (§5) wraps it.

* **Mirror list, tried in order per tile-set** (constants):
  1. `https://skies.esac.esa.int/DSSColor` (verified reachable 2026-07-13)
  2. `https://alasky.cds.unistra.fr/DSS/DSSColor`
  3. `https://alaskybis.cds.unistra.fr/DSS/DSSColor`
  The fetch run picks the first mirror whose `properties` GET succeeds and uses it
  for the whole run (no per-tile failover; a mid-run mirror failure fails the run —
  resume handles it).
* **Disk pre-flight (ENOSPC guard):** before any network I/O, compute
  `remaining = tiles_not_yet_on_disk`; require
  `shutil.disk_usage(pack_dir).free >= remaining * 70_000 + 50 MB` headroom
  (70 KB ≈ measured order-4 tile size). On failure, no fetch starts: the CLI
  prints the shortfall and exits 2; the API returns
  `507 {"detail": "insufficient disk space", "free_bytes": n, "required_bytes": m}`
  (§5). Scaling by *remaining* tiles means a nearly-complete resume isn't
  refused on a nearly-full card.
* **Algorithm:** download `properties` → parse/validate `hips_tile_width` (int,
  default 512) and that `hips_tile_format` contains `jpeg` → enumerate all
  (k, npix) for k ≤ target order → skip tiles whose file already exists with
  size > 0 (resume) → fetch remaining with bounded concurrency
  (`asyncio.Semaphore(8)`, one shared `httpx.AsyncClient`, 30 s per-tile timeout,
  one retry per tile) → each tile validated (starts with JPEG SOI `FF D8`, else
  count as failed, don't write) → atomic write (`.tmp` then `replace`, same idiom
  as `_write_cache`) → after the sweep, if failed == 0 write `pack.json`; if
  failed > 0, report and leave the manifest absent (re-run resumes).
* **Progress state** (in-process): `{"done": int, "total": int, "failed": int}`
  exposed via a module-level accessor for §5. Exactly one fetch task may run at a
  time (module-level `asyncio.Lock`; a second start request while running is a
  no-op that reports "already fetching").
* **CLI:** `python -m astrodeck.catalog.survey_pack fetch [--order 4] [--dest PATH]`.
  `--dest` defaults to the server's pack path. Constraint: the CLI must not
  START the hub or the FastAPI app. Importing `astrodeck.hub` for `CAPTURE_DIR`
  is permitted — seam-verified side-effect-free at import (hub.py's only
  module-level statement is `hub = Hub()`, which assigns plain attributes and
  starts nothing) and it matches `survey.py:38`'s existing import, avoiding a
  hand-copied path formula with an off-by-one risk. Prints progress lines
  (`done/total`) and a final summary; exit code 0 only if the manifest was
  written, 2 on the disk pre-flight failure.
* **Delete:** `remove_pack(slug)` — `shutil.rmtree` of the pack dir; used by §5.

## 3. Local renderer (`server/astrodeck/catalog/hips_local.py`)

Pure function core, no FastAPI imports:

```
render_cutout(pack_dir: Path, ra_deg: float, dec_deg: float, fov_deg: float,
              width: int) -> bytes   # JPEG; raises PackUnavailable if no manifest
```

* **New dependency:** `astropy-healpix>=1.0` (astropy-affiliated; wheels for
  win/linux/arm). Hand-rolling nested-HEALPix bit math is this feature's main
  correctness risk; we don't.
* **Order selection:** output scale `s_out = fov_deg*3600/width` arcsec/px; tile
  scale at order k is `58.63°·3600 / (tile_width·2^k)` arcsec/px (= `412.2/2^k`
  for 512px tiles). Use the smallest k with tile scale ≤ `s_out`, clamped to
  `[0, pack order]`. Uses the pack's actual `tile_width` (synthetic test packs
  use 64). (Wide FOV → low order → few tiles; never renders from hundreds of
  tiles.)
* **Sampling pipeline (vectorized numpy, no per-pixel Python loops):**
  1. Build an astropy TAN WCS: CRVAL = (ra, dec) ICRS, CRPIX = image center
     ((width+1)/2), square pixels, scale = fov/width deg/px, **RA axis oriented so
     the JPEG matches hips2fits output orientation** (North up, East on the same
     side as hips2fits — see validation below).
  2. `pixel_to_world` for the full width×width grid → (lon, lat) arrays.
  3. `HEALPix(nside=2**(k+9), order="nested").lonlat_to_healpix(...)` → nested
     index `h` per output pixel (tile order k + 9 sub-bits for 512px tiles; for a
     generic `tile_width = 2**s`, use `k + s`).
  4. Tile id `npix = h >> (2*s)`; in-tile sub-index `sub = h & (4**s - 1)`;
     de-interleave `sub` into (x, y) (one parity of bits is x, the other y) and
     apply the JPEG vertical-flip convention of HiPS tiles.
  5. Group output pixels by `npix` (numpy unique), load each tile once via PIL →
     RGB ndarray, nearest-neighbor gather. Missing/corrupt tile file → fill those
     pixels black (log once per render at debug level).
  6. Encode JPEG (Pillow, quality 85) and return bytes.
* **Bit-convention constants:** the de-interleave parity and the vertical flip are
  two module constants. Their values are **fixed by the orientation tests** (§7),
  not by prose: the synthetic-pack tests fail unless both are right. During
  development the implementer additionally eyeballs a local render of an
  already-cached CDS bucket against the cached JPEG (the M31 bucket currently in
  `_survey` is ground truth); no licensed image enters the repo.
* **Tile LRU:** `functools.lru_cache(maxsize=48)` on
  `(str(pack_dir), k, npix) -> ndarray | None`. 48 × ~0.75 MB ≈ 36 MB worst case.
* **Performance target:** ≤ 150 ms for a 768² render on the dev PC (measured in a
  test with the synthetic pack; generous bound so CI never flakes). The route
  calls the renderer via `asyncio.to_thread` — never on the event loop.

## 4. Routing and config (`survey.py`, `config.py`)

* **Config:** new block in the server config model, persisted and merged through
  the existing `POST /api/config` machinery (`extra="forbid"` per block):
  `survey: {"online_fetch": bool = False}`. RBAC: same capability tier as the
  other hardware/config blocks in the existing field-level map (planner pins the
  exact constant from `app.py`'s config-caps table). **Migration note:** after
  upgrade, online fetching is OFF until explicitly enabled — intentional
  (offline-first, user decision).
* **Threshold constant:** `_ONLINE_FOV_MAX_DEG = 4.0`. Below 4° the order-4 pack
  (25.8″/px) is soft on a 768px viewport; at/above it's ≥ native resolution.
* **Cache keys:**
  * Upstream renders: **exactly today's key** — `sha1("v2|{survey}|{ra_idx}|{dec_idx}|{fov_idx}|{width}|{stretch}")`.
  * Pack renders: `sha1("v2pk|{survey}|{ra_idx}|{dec_idx}|{fov_idx}|{width}|-")` —
    distinct salt, stretch normalized to `"-"` (stretch is baked into pack JPEGs;
    two stretch values must not double-cache identical pack renders).
* **Request flow** (replaces the current miss path; cache-hit fast path first,
  checking the upstream key then the pack key, upstream preferred as higher
  quality):

```
width clamp, snap, keys = (up_key, pk_key)
1. up_path exists  -> serve it              (X-Survey-Source: upstream)
2. pk_path exists AND NOT (online_fetch AND fov < 4.0)
                   -> serve it              (X-Survey-Source: pack)
   # when upstream is wanted, a pack-cached file does NOT satisfy the request —
   # upstream gets its chance to upgrade quality; on failure step 4 serves pk.
3. single-flight on up_key if upstream wanted, else on pk_key; recheck as above.
4. if online_fetch AND fov < _ONLINE_FOV_MAX_DEG:
       try upstream fetch (existing _fetch_cutout)
           -> cache at up_path, serve       (X-Survey-Source: upstream)
       on RuntimeError: fall through to 5 (never 503 while a pack can render)
5. if pack present for this survey:
       pk_path exists -> serve
       else render via asyncio.to_thread(render_cutout, ...)
           -> cache at pk_path, serve       (X-Survey-Source: pack)
       on PackUnavailable/any render error: fall through to 6
6. if online_fetch AND fov >= _ONLINE_FOV_MAX_DEG:   # pack failed; last resort
       try upstream once as in 4
7. otherwise -> the frozen 503
```

* Every 200 carries the existing snapped `X-Survey-*` headers plus new
  `X-Survey-Source: upstream | pack`.
* With `online_fetch = false`, **no code path may construct an httpx client** for
  cutouts (test-enforced, §7).
* Surveys without a pack (`DSS2/red`, `2MASS/color`): steps 5 is skipped (no
  pack), so they work only when `online_fetch` is on; otherwise 503.

## 5. Pack API (routes hosted in `api/app.py`, logic in `survey_pack.py`)

Seam-pinned decisions (extraction 2026-07-13): dedicated config blocks
(`drivers`, `rotator`, `providers`) are written through their OWN typed
`ConfigStore` setters + routes in `app.py` — NOT the generic `POST /api/config`
field-caps merge. `survey` follows that pattern: `ConfigStore.set_survey` +
`POST /api/config/survey` in `app.py`. The pack routes also live in `app.py`
(the proven `@declare(...)` + `Depends(require(...))` idiom next to the
drivers routes); `survey_pack.py` stays FastAPI-free and exposes plain
functions/exceptions the routes wrap. Capability: **`CAP_CONFIG_SITE_OPTICS`**
for all writes (`POST /api/config/survey`, `POST /api/survey/pack/fetch`,
`DELETE /api/survey/pack`) — Atlas/optics-adjacent, no new capability or
role-map churn; `CAP_VIEW_STATUS` for `GET /api/survey/pack`.

* `GET /api/survey/pack` → `{"present": bool, "slug": "dss2color",
  "survey": "CDS/P/DSS2/color", "order": int|null, "bytes": int|null,
  "tile_count": int|null, "fetched_at": float|null,
  "fetching": {"done": int, "total": int, "failed": int} | null}`.
  Reads manifest + live progress; never scans 4,092 files on the request path
  (manifest carries totals; while fetching, progress comes from §2 state).
  RBAC: view-status capability (same as `GET /api/config`).
* `POST /api/survey/pack/fetch` body `{"order": 4}` (order optional, default 4,
  clamped 1..6) → runs the disk pre-flight synchronously (507 on failure, §2),
  then starts the background fetch task (single-flight per §2); returns
  `202 {"started": true}` or `200 {"started": false, "already": true}`.
  RBAC: same capability as config writes.
* `DELETE /api/survey/pack` → removes the pack directory (`remove_pack`); 409 if
  a fetch is currently running. RBAC: same as config writes.

## 6. UI

* **Settings → new "Sky Atlas" card** (own component file, following the Settings
  panel idiom used by the Backend Drivers panel):
  * Toggle "Online survey fetch (CDS)" bound to `config.survey.online_fetch` via
    the existing config-patch path. Help text: "When on, small fields load
    full-resolution imagery from CDS; the offline pack remains the fallback."
  * Pack status line from `GET /api/survey/pack`: "Offline sky pack: 250 MB,
    order 4, fetched <date>" or "not downloaded".
  * Button "Download offline sky pack (~250 MB)" → `POST /api/survey/pack/fetch`;
    while `fetching` is non-null, poll GET every 2 s (only while the card is
    mounted) and show `done/total` as a progress bar; on `failed > 0` completion
    show a retry hint (re-running resumes); on a 507 response show the shortfall
    plainly ("Not enough space on the capture volume — needs ~X MB free").
  * "Delete pack" button (confirm dialog) → DELETE.
  * Attribution line (static text): "DSS2 imagery © AAO/STScI, served from
    CDS/ESA HiPS mirrors."
* **Survey picker (`SurveyControls.tsx`):** new prop `onlineFetch: boolean`
  (AtlasView passes it from config in the store). When false, the `DSS2 red` and
  `2MASS color` options render `disabled` with title "Enable online survey fetch
  in Settings"; the two stretch buttons are disabled with title "Stretch applies
  to online imagery" (pack tiles are pre-stretched). `DSS2 color` and
  `Schematic (offline)` are always selectable.
* **Atlas empty-state banner:** AtlasView learns pack presence (one
  `GET /api/survey/pack` on mount, refreshed after Settings changes via the
  store's config). When the degraded state fires AND `!online_fetch` AND
  `!packPresent`, the banner copy becomes: "No survey source — download the
  offline sky pack in Settings, or enable online fetch." If that same status
  reports `fetching` non-null, the copy is instead
  "Downloading offline sky pack… {done}/{total}" and AtlasView re-polls
  `GET /api/survey/pack` every 2 s while this banner is visible (poll stops when
  the banner clears or the view unmounts). No completion wiring is needed: once
  the pack lands, SkyCanvas's existing backoff retry succeeds and the degraded
  state clears itself. All other degraded cases keep today's copy. No other
  SkyCanvas behavior changes (backoff, keep last-good, skeleton all unchanged).

## 7. Testing

Server (pytest, existing conventions):

* **`test_hips_local.py`** — all against synthetic packs generated in-test with
  PIL (solid-color or gradient tiles, small `tile_width` like 64 to keep tests
  fast; renderer honors `tile_width` from properties/manifest):
  * order-selection math (fov/width → k) table cases incl. clamping;
  * de-interleave: hand-computed (sub → x,y) cases;
  * tile-targeting: cutout centered inside a known npix is dominated by that
    tile's color; neighboring-field colors appear on the correct sides;
  * **orientation**: a pack where the field north of center is red and south is
    blue ⇒ rendered image top half red, bottom half blue; likewise an east/west
    pair fixes the horizontal convention (both module constants are pinned by
    these tests);
  * missing tile file → black fill, no raise; absent manifest → `PackUnavailable`;
  * performance bound: 768² synthetic render < 150 ms (generous; skip on CI slow
    marker if it ever flakes).
* **`test_survey_pack_fetch.py`** — `httpx.MockTransport`: full small fetch
  (order 0/1) writes tree + manifest; resume skips existing files; non-JPEG body
  counted failed and not written; failed > 0 ⇒ no manifest; progress counters;
  concurrent second start is a no-op; delete refuses while running; disk
  pre-flight (monkeypatched `shutil.disk_usage`) blocks a fresh fetch when free
  space is short but allows a nearly-complete resume, and the API path returns
  the 507 shape.
* **`survey.py` route tests** (extend the existing survey test module):
  * `online_fetch=False` ⇒ zero upstream attempts — monkeypatch
    `survey.httpx.AsyncClient` with a class whose constructor raises
    (capture the real client first for the FastAPI test client — the Wave-1
    monkeypatch trap);
  * `online_fetch=True`, fov < 4 ⇒ upstream tried first, pack serves on failure
    (`X-Survey-Source: pack`), upstream serves on success;
  * fov ≥ 4 with pack ⇒ pack serves without upstream attempt;
  * no pack + online off ⇒ frozen 503 shape;
  * cache-key separation: upstream key format byte-identical to today's
    (regression-pin the sha1 input string), pack key uses `v2pk` + `-` stretch;
  * pack-cached file does not satisfy a request that wants upstream (step 2
    guard), and `_evict_cache` never deletes anything under `_survey_pack`.
* **Existing survey tests:** any that exercise the upstream path must now set
  `online_fetch=True` (or seed a pack) in their fixtures — expected churn, listed
  in the plan.

UI: `npm run build` green; if a pure helper is extracted (e.g., pack-status
formatting), a self-executing `npx tsx` test file per repo convention. Manual
smoke (at the scope): pack download from Settings, Atlas renders offline with
Wi-Fi off, toggle online and verify small-FOV quality upgrade.

## 8. Migration and ops notes

* Default behavior change: fresh and upgraded installs render from the pack (or
  503 with the new banner until it's downloaded). Enabling `survey.online_fetch`
  restores today's upstream behavior for fov < 4°.
* The warm upstream cache keeps working (key format preserved).
* Pack lives outside the bounded `_survey` cache: the 200 MB cutout-cache cap is
  unaffected; the pack adds ~250 MB of planned disk use on the capture volume.
* Docs: README gains a short "Offline sky pack" section (CLI one-liner + Settings
  path).

## 9. Out of scope (v1)

* Packs for DSS2 red / 2MASS (fetcher is parameterized internally, but no UI).
* Bilinear/tile-boundary-blended sampling (nearest-neighbor only; imagery is
  upscaled at the FOVs where the pack serves small fields).
* Deeper-than-order-4 packs in the Settings UI (CLI `--order` accepts up to 6).
* Auto-refresh/re-fetch of stale packs; HiPS `Allsky.jpg` preview usage.
* Mid-run mirror swap on per-tile failure (considered at review: rotate to the
  next mirror when a tile fails its retry). Deferred — a failed run is cheap to
  retry because resume skips everything already fetched.
* Any change to the schematic mode or the client survey loader/backoff.
