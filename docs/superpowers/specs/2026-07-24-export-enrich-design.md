# PRO-10 — Export-bundle enrichments (materialize / layouts / FWHM / keep_threshold / naming tokens)

Follow-ups deferred from the PRO-10 export-bundles design
(`docs/superpowers/specs/2026-07-23-export-bundles-design.md` §4, decisions 1/3/4/6)
and the PRO-11 naming design (`2026-07-23-file-naming-templates-design.md` §"open
questions" decision 4). All five are **additive** to shipped, green code — no
behavior change to the existing one-click `.zip`.

## 0. Scope

Five independent enrichments, each landable alone:

- **(a)** `POST …/bundle/materialize` — server lays the actual FITS out under
  `CAPTURE_DIR/exports/<id>/` (hardlink, `cp` fallback) for users running
  AstroDeck **on the capture box**.
- **(b)** `layout` variants `"siril"` and `"app"` (grouped is v1; the param
  already exists on `build_bundle`/`build_script`).
- **(c)** a real per-frame **FWHM** column in `weights.csv` — honestly, this is
  `k·HFR` (PRO-7 does not measure FWHM); design decides the honest framing.
- **(d)** an optional **`keep_threshold`** that flags the worst-weighted tail.
- **(e)** filename tokens **`$$GAIN$$` / `$$EXPOSURE$$` / `$$BINNING$$`** threaded
  through the naming engine.

## 1. Current-state seams (all read; file:line)

- `server/astrodeck/sequence/bundle.py` — pure core. `build_bundle` @222 already
  takes `layout: str = "grouped"` and threads it into `Bundle.layout` @310;
  `_group_dir` @206 builds `<TARGET>/<FILTER>/<EXP>s_g<GAIN>_bin<BIN>` and the
  light `dest` is hard-coded to `{gdir}/lights/{name}` @275; master `dest` is
  `masters/master{Kind}.fits` @300. `LightEntry` @62 carries `weight`; there is
  **no** `fwhm` and **no** `keep` field yet. `weights_csv` @391 + `_CSV_COLS`
  @386 are the CSV; `manifest_json` @317; `build_script`/`_build_sh` @474 /
  `_build_ps1` @503 emit `mkdir -p {gdir}/lights` + `cp -- src dest` from
  `l.dest` / `g.masters` — i.e. **layout lives entirely in the `dest` strings the
  builder computes**, the script just replays them.
- `server/astrodeck/api/app.py` — `report_bundle` @2092 (JSON preview) and
  `report_bundle_zip` @2105 (in-memory `.zip`, `zipfile` @2119). Both resolve
  `_get_master_library()` @439 and pass `is_local=hub._is_local_save`. Both are
  `CAP_VIEW_STATUS` (read-only). `_slug(report_id)` @2088 sanitizes the download
  filename.
- `server/astrodeck/hub.py` — `CAPTURE_DIR` @98-99; `_is_local_save` @1779 (the
  `resolve()` + `is_relative_to(CAPTURE_DIR)` + `exists()` guard — the exact
  pattern the materialize route must reuse for its **destination** check);
  `_capture_path` @1899 renders the naming template with a `fields` dict @1908
  that today has **no** GAIN/EXPOSURE/BINNING; `capture()` @1505 already has
  `exposure_s`, `gain`, `offset`, `binning` in scope and calls `_capture_path`
  @1536 — the seam where the three new tokens' values become available.
- `server/astrodeck/naming.py` — `KNOWN_TOKENS` @18 (token→sanitize-mode),
  `_TOKEN_RE` @24, `render_relative_path` @61, `validate_template` @84, `_SAMPLE`
  @78 (dry-render fields). Adding a token = one `KNOWN_TOKENS` entry + one
  `_SAMPLE` entry; the engine and validator are already generic.
- `ui/src/views/ReportView.tsx` — the "Stacking bundle" `Panel` @298; the
  `weightAlt` checkbox @322-334 (the only current advanced control, inline); the
  download `<a href …/bundle.zip{?weight_altitude}>` @345. `report.frames_captured`
  drives `bundleDisabledReason`.
- `ui/src/lib/bundleView.ts` — pure panel helpers (`masterChips`,
  `bundleDisabledReason`); the home for new pure UI logic (query-string builder,
  layout-option list), unit-tested via `bundleView.test.ts`.
- `server/tests/test_bundle.py` — `env` fixture @378 (TestClient + report on
  disk) already exercises `/bundle` + `/bundle.zip`; `FrameRecord` factory rows
  throughout. `server/tests/test_naming.py` — `render_relative_path` +
  `validate_template` cases; `test_hub_*` @88+ pin the `_capture_path` output.

## 2. Architecture — where each change lives

Everything stays behind the **pure-core / thin-route / thin-render** split PRO-10
already established. Layout, FWHM, and keep_threshold are pure `bundle.py`
changes (the routes only pass params through). Materialize is the one new route
doing I/O. Tokens are a `naming.py` + `hub.py` change independent of the bundle.

### 2.1 (b) Layout variants — pure, the cleanest of the five

Layout already flows through `build_bundle(layout=…)`. The **only** thing a
layout controls is the relative `dest` paths; the script replays them verbatim.
So the entire feature is: **compute `dest` per layout**, in one new pure helper.

Introduce `_dest_paths(layout, gdir, light_name, kind) -> str` (or two tiny
helpers, `_light_dest` + `_master_dest`) replacing the inline `{gdir}/lights/…`
@275 and `masters/master{Kind}.fits` @300:

| layout | light dest | master dest (kind ∈ dark/flat/bias) |
|---|---|---|
| `grouped` (v1, unchanged) | `{gdir}/lights/{name}` | `masters/master{Kind}.fits` (shared, top-level) |
| `siril` | `{gdir}/lights/{name}` | `{gdir}/{darks\|flats\|biases}/master{Kind}.fits` (**per-group**, pluralized, alongside `lights/`) |
| `app` | `{gdir}/Light/{name}` | `{gdir}/{Dark\|Flat\|Bias}/master{Kind}.fits` (per-group, APP's capitalized frame-type dirs) |

Rationale for the conventions:
- **Siril**: its Sequence/OSC and mono workflows expect sibling `lights/ darks/
  flats/ biases/` directories per stack; masters go **inside** the group so
  Siril's per-sequence calibration finds them. Plural lower-case is Siril idiom.
- **APP** (AstroPixelProcessor): loads by frame *type* and uses capitalized
  `Light/Dark/Flat` groupings; masters live with the group so APP's
  auto-calibration associates them.

`build_bundle` validates `layout ∈ {"grouped","siril","app"}` and raises
`ValueError` (route → 400) on anything else — one guard at the top, mirroring
`build_script`'s `shell` handling. `readme_text` already prints
`bundle.layout`; extend the one "tree" example block @434 to switch its
illustration by layout (pure string, no new data).

**Key insight that keeps this lean:** because masters may now be *per-group*
(siril/app) rather than shared (grouped), `Group.masters` /
`Group.master_sources` are already per-group dicts — no model change. Only the
`dest` string differs. The dedup concern (same master matched by N groups) only
existed for grouped's shared top-level path; per-group layouts naturally write
one copy per group (correct — each group's calibration is independent). For
grouped we keep today's single shared `masters/` copy.

### 2.2 (c) FWHM column — honest `k·HFR`, clearly labeled

PRO-7 measures **HFR and eccentricity, not FWHM** (grep of `server/astrodeck`
confirms zero `fwhm` producers). So a "true FWHM" column would be a fabrication.
The honest design:

- Add `fwhm_from_hfr(hfr, k=…) -> float | None` in `bundle.py` (pure; `None`→`None`).
  The **column header is `fwhm_est`**, not `fwhm`, and the README + manifest note
  say "estimated FWHM ≈ k·HFR (AstroDeck measures HFR; k defaults to 2.0)". A
  SubframeSelector user keying on true FWHM is thus never misled into thinking
  this is a measured PSF FWHM.
- Add `fwhm_est` to `_CSV_COLS` @386 (after `hfr`) and to the manifest per-light
  row @335. **Do not** add it to the slim `bundle_summary` (preview stays small).
- `k` is a module constant `HFR_TO_FWHM_K = 2.0` (round stars: FWHM ≈ 2·HFR is
  the common first-order relation; exact factor depends on PSF/sampling — that
  caveat is *why* the column is `_est` and documented). Not user-tunable in v1
  (open decision D3).
- **When PRO-7 ships true FWHM** later: add a `fwhm` field to `FrameRecord`,
  populate `LightEntry.fwhm`, and have the CSV emit measured `fwhm` when present,
  falling back to `fwhm_est`. Designed-for but not built now.

### 2.3 (d) keep_threshold — flag the worst tail, never delete

`build_bundle(…, keep_threshold: float | None = None)`. Semantics:
`keep_threshold` is a **weight** cutoff in `[0,1]` (weights are already
group-normalized, best = 1.0). A sub with `weight < keep_threshold` gets
`keep=False`; otherwise `keep=True`. `None` (default) ⇒ every `keep=True`
(today's behavior; `keep == accepted` is unchanged when threshold absent — see
below).

- Add `keep: bool` to `LightEntry` (default `True`).
- Threshold is applied **within each group after normalization** (@270), so it
  culls the relatively-worst subs per stack, not across dissimilar filters/exps.
- Surface `keep` in the manifest per-light row and as a **`keep` column** in
  `weights.csv`. Add `kept_count` to each group in `bundle_summary` and to
  `manifest_json` group headers so the UI/README can say "38 of 42 kept".
- **Safety:** `keep=False` **never removes a sub from the bundle or the build
  script.** It is advisory metadata the user's stacker (or a future filtered
  export) consumes. The build script still lays out every local sub; a comment
  block in the README explains the `keep` column. This preserves PRO-10 decision
  6 ("don't second-guess the run's own quality gate") while giving experts the
  tail flag they asked for.
- Interaction with `accepted`: `accepted` = the run's real-time gate; `keep` =
  the post-hoc weight tail. They are orthogonal columns. A sub can be
  `accepted=True, keep=False` (passed live but is the weakest 10%). We do **not**
  fold `accepted` into `keep`.

`keep_threshold` also flows to the materialize route and the `.zip` route as a
query param, so the exported manifest reflects the user's choice.

### 2.4 (a) Materialize route — the only new I/O

New route (write, so **POST**, not GET — avoids browser prefetch/caching
re-running a filesystem mutation, and it is not a download):

```
POST /api/reports/{report_id}/bundle/materialize
     ?layout=grouped|siril|app  &weight_altitude=…  &keep_threshold=…
     dependencies=[Depends(require(CAP_CONTROL_CAPTURE))]
```

Capability: **`CAP_CONTROL_CAPTURE`**, not `CAP_VIEW_STATUS` — this writes to the
capture box's filesystem, so it needs the same write authority as capturing.
(Open decision D1.)

Behavior:
1. Load report (404 if missing), `build_bundle(...)` exactly as the `.zip` route
   — `build_bundle` already selected only `is_local` subs, so **every `src` is
   provably under `CAPTURE_DIR`** (the security invariant we inherit for free).
2. Destination root: `CAPTURE_DIR / "exports" / _slug(report_id)`. Use the
   sanitized slug (never the raw path param) so the export dir can't traverse.
3. For each light + master, dest = `root / entry.dest`. **Re-validate every
   resolved dest** with the `_is_local_save`-style guard:
   `dest.resolve().is_relative_to((CAPTURE_DIR/"exports").resolve())`. `entry.dest`
   is already `sanitize_component`-built (target/filter can't escape), but we
   defense-in-depth the resolved path anyway. Refuse (skip + record error) any
   dest that escapes.
4. Link strategy per file: `os.link(src, dest)` (hardlink — zero extra bytes,
   instant). On `OSError` (EXDEV cross-device, EMLINK, EPERM, or dest exists),
   fall back to `shutil.copy2(src, dest)`. If both fail, record the file in a
   `failed` list and continue (partial success is reported, not fatal).
   `mkdir(parents=True, exist_ok=True)` per dest dir. **Idempotent:** if dest
   already exists and is the same inode (re-materialize), skip as `linked`.
5. This is blocking disk I/O → run the whole materialize under
   `asyncio.to_thread(...)` (mirrors `SessionReporter.load`).
6. Response JSON: `{ "export_dir": "<abs>", "layout": …, "linked": N,
   "copied": M, "failed": [ {src, reason} … ], "bytes_copied": B,
   "groups": [ {dir, linked, copied} … ] }`. **No file body.**

Masters caveat: `master_sources` come from PRO-1's library — they may sit outside
`CAPTURE_DIR` (a shared calibration store). Hardlink across devices fails → `cp`
fallback handles it. The **source** is trusted (library, not user param); only
the **dest** needs the containment check. We do **not** require masters to be
local.

Why not zip-the-FITS instead? PRO-10 decision 1 already rejected that (tens of GB
through a Pi browser). Materialize is the on-box answer: no bytes move for
same-filesystem hardlinks.

### 2.5 (e) Naming tokens `$$GAIN$$` / `$$EXPOSURE$$` / `$$BINNING$$`

PRO-11 deferred these purely because the values "aren't available at the
`_capture_path` seam without threading new args through `capture()`." But
`capture()` @1505 **already has** `exposure_s`, `gain`, `binning` in scope, and it
is the sole caller that matters. So the thread is short:

- `naming.py`: add to `KNOWN_TOKENS` @18 — `"GAIN": "loose", "EXPOSURE": "loose",
  "BINNING": "loose"`. Add matching `_SAMPLE` @78 entries (`"GAIN": "100",
  "EXPOSURE": "300", "BINNING": "1"`) so `validate_template`'s dry render passes.
  **No engine change** — `render_relative_path` is already token-generic.
- `hub.py`: extend `_capture_path` @1899 signature to accept
  `gain: int | None = None, exposure_s: float | None = None,
  binning: int | None = None`; add three `fields` entries @1908. Format
  decisions (D4): `GAIN` = `str(gain)` (int, e.g. `100`); `BINNING` = `str(binning)`
  (e.g. `1`); `EXPOSURE` = **integer seconds when whole, else `g`-formatted with
  `.` → `p`** so `1.5s` renders `1p5` (a `.` in a filename segment is legal but
  fractional-second exposures are rare; the `p` avoids extension confusion). All
  three pass through `sanitize_component(..., "loose")` already. A `None` value
  renders empty (token drops out — existing engine behavior), so old templates
  and no-gain frames are unaffected.
- `capture()` @1536: pass `gain=gain, exposure_s=exposure_s, binning=binning` to
  `_capture_path`. Backward-compatible defaults keep every other caller working.
- UI naming panel (`2026-07-23-file-naming-templates-design.md` token help list
  + `ui/src/lib/naming.ts` mirror): add the three tokens to the help chips and
  the client-side dry-render `_SAMPLE`. These render **only** when the user opts
  them into their template; the default template is byte-for-byte unchanged.

## 3. UX — progressive disclosure

**Novice path is untouched.** The panel's default remains a single obvious
control: **Download bundle.zip** (grouped layout, no threshold, altitude off).
Zero configuration, plain-language copy. A novice never sees a layout picker, a
threshold slider, or the materialize button unless they open Advanced.

**New: an `<details>`-style "Advanced" disclosure**, collapsed by default, below
the group list, containing (in order):

1. **Layout** — a 3-way segmented control / `<select>`: `Grouped (default)` ·
   `Siril` · `APP`. Label copy: "Folder layout for your stacker." Default
   `Grouped`. Changing it updates both the `bundle.zip` href query and the
   materialize action; the group-list preview dirs update live (pure recompute
   from the same preview data via a `bundleView` helper — no refetch needed, or a
   cheap re-`GET /bundle?layout=` if we want server-truth dirs; recommend
   client-side recompute in `bundleView.ts` to avoid a round-trip).
2. **Weight subs by altitude** — the existing checkbox @322, **moved inside**
   Advanced (it is an expert affordance; today it sits in the novice flow). Its
   long explanatory `title` is preserved.
3. **Flag worst subs below** — an optional weight threshold. Novice-safe design:
   a checkbox "Flag the weakest subs" that, when checked, reveals a
   `0.0–1.0` number/slider (default `0.5`) with helper text "Subs below this
   weight are marked `keep=false` in the manifest — nothing is deleted." Unchecked
   ⇒ `keep_threshold` omitted ⇒ every sub kept (today's behavior). The preview
   shows a live "N of M would be flagged" count computed client-side from the
   per-group weights the preview already carries, or server-side via a `kept_count`
   in the `?keep_threshold=` preview response (recommend server-side — D6).
4. **Materialize on the capture box** — a button, **honest-disabled (§11.8)** when
   the server is not the capture box. Detecting "on the capture box": the simplest
   honest signal is that `preview.groups` is non-empty (build_bundle only emits
   groups whose subs are `is_local`) — i.e. if there are local subs, materialize
   is meaningful. When enabled: button "Lay out FITS under captures/exports/…"; on
   click, `POST …/bundle/materialize` with the current layout/threshold; on
   success show the returned `export_dir` + "linked N, copied M" inline; on partial
   failure list the failed files. When disabled: dimmed + `aria-disabled` + `title=
   "No local subs on this machine — the FITS live on your imaging host. Use
   Download bundle.zip and run build.sh there."` (never native `disabled`).

Copy discipline: the Advanced summary line reads "Advanced (layout, weighting,
materialize)" so an expert knows power lives there; a novice reads past it. Reuse
the app's existing collapsible pattern + `btn`/`text-dim`/`text-warn` classes and
the red-night dark aesthetic — do not introduce a new idiom. Nothing advanced is
persisted (query-at-request-time only); an expert who never opens Advanced is
unaffected, and a novice is never forced to understand a layout, a threshold, or a
hardlink to get their `.zip`.

## 4. Backend plan (task order, each independently shippable)

1. **Layouts (pure)** — `_light_dest`/`_master_dest` helpers + `layout` guard in
   `build_bundle`; `readme_text` per-layout example. `bundle.py` only.
2. **FWHM (pure)** — `HFR_TO_FWHM_K`, `fwhm_from_hfr`, `fwhm_est` in `_CSV_COLS`
   + manifest row + README note. `bundle.py` only.
3. **keep_threshold (pure)** — `LightEntry.keep`, `build_bundle(keep_threshold=)`,
   `keep` CSV column, `kept_count` in summary + manifest, README note. `bundle.py`.
4. **Route params** — `layout` + `keep_threshold` query params on `report_bundle`
   (preview returns `kept_count` when threshold set) and `report_bundle_zip`;
   validate `layout`/`keep_threshold` (400 on bad). `app.py`.
5. **Materialize route (I/O)** — new `POST …/bundle/materialize`,
   `CAP_CONTROL_CAPTURE`, `asyncio.to_thread` link/copy engine with dest
   containment guard; a pure `bundle_materialize_plan(bundle, root) -> [(src,dest)]`
   helper in `bundle.py` so the link/copy loop iterates a unit-testable list and
   the route stays thin. `app.py` + `bundle.py`.
6. **Naming tokens** — `KNOWN_TOKENS` + `_SAMPLE` in `naming.py`; `_capture_path`
   signature + fields + `capture()` call (@1536) in `hub.py`; UI token help +
   `naming.ts` mirror `_SAMPLE`.

## 5. UI plan

- `ui/src/lib/bundleView.ts` — new pure helpers (unit-tested via existing
  `bundleView.test.ts`, no DOM): `bundleQuery({layout,weightAlt,keepThreshold})` →
  query string; `layoutOptions()` → the 3 option tuples; `relayoutDirs(preview,
  layout)` → recompute group dir strings client-side (mirror `_light_dest`);
  `materializeSummary(resp)` → the "linked N, copied M, K failed" line.
- `ui/src/views/ReportView.tsx` — add the Advanced disclosure; move the altitude
  checkbox in; wire the layout `<select>`, threshold control, and materialize
  button. Panel stays a **thin render over `bundleView` pure logic** — render not
  DOM-tested (per test discipline); the logic is.
- `ui/src/api/reports.ts` — `materializeBundle(id, opts)` POST wrapper;
  `getBundlePreview(id, opts)` gains optional `layout`/`keep_threshold`.
- `ui/src/types.ts` — `BundlePreview` group gains `kept_count?`; materialize
  response type.

## 6. LEAN test plan — estimated net-new: ~8 tests

Reuse the `env` fixture (`test_bundle.py:378`) + existing `FrameRecord` factories;
parametrize aggressively.

1. `@parametrize layout in {grouped,siril,app}` — one test builds a 2-group
   bundle, asserts light dest + master dest shape per layout AND that
   `build_script("sh")` contains the matching `mkdir`/`cp` lines. 1 test, 3 params.
2. `build_bundle` rejects an unknown `layout` with `ValueError`; route → 400.
3. `weights_csv`/`manifest` carry `fwhm_est == 2.0·hfr`, blank when hfr None —
   fold into the existing `test_weights_csv_blanks_none_metrics` (extend, ~1 net).
4. `@parametrize (threshold, expected_keep_flags)` — `None`→all keep; `0.5`→tail
   flagged; verify `keep` never removes a sub from `build_script` and `kept_count`
   is correct. 1 test, ~3 params. (Pins the no-deletion safety property.)
5. `bundle_materialize_plan` pure helper — dests all under `exports/<slug>` and
   match the layout.
6. Route integration (`env`): `POST …/bundle/materialize` — hardlink path (real
   `tmp_path` under CAPTURE_DIR, files exist at `exports/<id>/…`) AND cp fallback
   via `monkeypatch os.link` raising `OSError`; assert `linked`/`copied` counts +
   a dest-escape src is refused. 1 test.
7. `render_relative_path` with `$$GAIN$$_$$EXPOSURE$$_$$BINNING$$` → `100_300_1`
   (and fractional `1.5→1p5`); `validate_template` accepts the new tokens.
   `test_naming.py`, 1 parametrized render test.
8. `_capture_path` (hub) with the new tokens produces a path containing
   gain/exp/bin — extend an existing `test_hub_*` parametrization where possible.
9. UI: extend `bundleView.test.ts` (runs via `npx tsx`) with `bundleQuery` +
   `relayoutDirs` cases — 1 net-new tsx block, no DOM.

Net new backend ≈ 6–7, UI ≈ 1 block ⇒ ~8 total. No trivial per-serializer tests;
layout/threshold are single parametrized tests; FWHM folds into an existing CSV
test; materialize is the one genuinely new integration test (link + copy + guard).

## 7. Open decisions (with recommendations)

- **D1 — Materialize verb & capability.** *Recommend POST + `CAP_CONTROL_CAPTURE`.*
  It writes to the capture box filesystem → needs write authority, not
  `CAP_VIEW_STATUS`; POST avoids browser prefetch re-running a mutation, and it is
  not a download.
- **D2 — Detecting "on the capture box".** *Recommend reusing `preview.groups`
  non-empty* (build_bundle already filters to `is_local` subs) — no new probe. A
  dedicated `/api/system/is_capture_host` is cleaner long-term but extra surface.
- **D3 — FWHM `k` user-tunable?** *Recommend fixed `k=2.0` + `fwhm_est` header +
  documented caveat.* A tunable `k` invites false precision on a value we don't
  measure. When PRO-7 ships true FWHM, emit measured `fwhm` and drop `_est`.
- **D4 — `$$EXPOSURE$$` fractional formatting.** *Recommend integer seconds when
  whole (`300`), else `g`-format with `.`→`p` (`1.5`→`1p5`).* Extension-safe,
  readable; fractional exposures are rare.
- **D5 — keep_threshold semantics.** *Recommend a normalized-weight cutoff in
  `[0,1]`* (weights already group-normalized, best=1.0) — filter/exposure-agnostic
  and reuses the number the UI shows. An absolute-HFR cutoff mis-behaves across
  filters.
- **D6 — keep_threshold in the preview.** *Recommend server returns `kept_count`
  when `?keep_threshold=` is set* (small `bundle_summary` add) rather than shipping
  a 2000-row weight vector to the client for a JS recompute — keeps the preview slim.
- **D7 — Layout dir recompute: client vs. refetch.** *Recommend client-side
  `relayoutDirs`* (mirror `_light_dest`) so the picker is instant/offline; a
  `GET /bundle?layout=` refetch is the fallback for server-truth dirs.
- **D8 — Materialize idempotency.** *Recommend idempotent:* same-inode hardlink →
  skip as `linked`; existing different file → `copy2` overwrite. A re-materialize
  after adding subs tops up the export tree.

## 8. Risks

- **R1 — Materialize path traversal / arbitrary write** (highest severity).
  Mitigated: srcs are provably under CAPTURE_DIR (build_bundle's `is_local`
  filter); dests are `sanitize_component`-built AND re-validated with the
  `_is_local_save` `resolve()+is_relative_to(exports)` guard before every write.
  Test #6 exercises the escape-refusal path.
- **R2 — Hardlink footguns.** EXDEV/existing-dest raise `OSError` → `copy2`
  fallback. Hardlinks share inodes — editing an exported sub mutates the original;
  README: "exports are hardlinks; treat read-only." Copy fallback avoids this where
  the FS can't hardlink.
- **R3 — FWHM mislabeling.** A user could key a SubframeSelector expr on
  `fwhm_est` thinking it's measured. Mitigated by the `_est` suffix + explicit
  README/manifest note; `hfr` stays the primary sharpness column.
- **R4 — keep_threshold misread as delete.** Mitigated: `keep=False` is
  advisory-only, the script lays out every sub, and UI + README say "nothing is
  deleted."
- **R5 — Naming token backward-compat.** New tokens opt-in; `None` values drop out
  via the existing engine; default template byte-for-byte unchanged
  (`test_default_template_byte_for_byte` still passes). `_capture_path` new args
  default `None` so no other caller breaks.
- **R6 — Layout convention drift.** Siril/APP folder expectations are inferred from
  their calibration workflows; a slightly-off convention is a cosmetic relayout,
  not data loss. Validate against a real Siril/APP import before calling the layouts
  "supported" in docs.
- **R7 — Test-count creep.** Held to ~8 by parametrizing layouts/threshold into
  single tests, folding FWHM into an existing CSV test, and one integration test
  for materialize.
