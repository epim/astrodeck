# UI/UX findings tracker — 2026-07-22

Living backlog of UX findings from the naive-state walkthrough (user, mobile via
relay) + two code investigations. Keep updating: mark status as items move
`open → planned → in-progress → fixed → verified`. New findings append at the end.

Categories: **BUG** (functional/regression) · **DECISION** (needs a product/arch
call) · **FEATURE** (net-new) · **RESPONSIVE** (mobile layout) · **POLISH** (formatting/pro).

Severity: P0 (broken core) · P1 (broken on a primary surface) · P2 (missing/rough) · P3 (polish).

| ID | Title | Cat | Sev | Status |
|----|-------|-----|-----|--------|
| UX-01 | Manual slew feels dead (tap does nothing) | BUG | P2 | confirmed |
| UX-02 | Native guider not selectable in Equipment | DECISION | P2 | open |
| UX-03 | Autofocus + Polar-align should default to native | DECISION | P2 | open |
| UX-04 | Plate-solve default / bundle ASTAP vs build native solver | DECISION | P2 | open |
| UX-05 | Filter-wheel slot-name assignment (gear → modal → FITS) | FEATURE | P2 | open |
| UX-06 | Atlas search: first query shows no suggestions | BUG (regression) | P1 | open |
| UX-07 | Atlas shows no sky survey (black + "LOADING…") | DECISION+POLISH | P1 | open |
| UX-08 | Tracking-rate pill overflows (Solar clipped) on mobile | RESPONSIVE | P2 | confirmed |
| UX-09 | Atlas optics inputs oversized; "FROM CAMERA" overlaps labels | RESPONSIVE | P2 | open |
| UX-10 | Pixel size renders raw float `3.7599999904632` | POLISH | P3 | open |
| UX-11 | Plan (Sequence) view horizontal overflow | RESPONSIVE | P2 | confirmed |
| UX-12 | App-wide mobile horizontal overflow (systemic) | RESPONSIVE | P1 | open |

---

### UX-01 — Manual slew feels dead · BUG · P1
Tapping SlewPad N/S/E/W on the connected AM5N does nothing visible.
**Root cause (grounded):** the backend path WORKS — `zwo_am5.move_axis` issues real
`:R<n>#`+`:M<dir>#` LX200 rate commands (not the inert pulse-guide path). The problem
is the touch model: SlewPad defaults to `rateIdx=1` = "8× SID" (a *continuous* rate),
and per the "one model" design a quick TAP at a continuous rate is just a brief
start-slew + stop-slew (`slewController.ts:141-170`) — at 0.0334°/s + tap timing + the
AM5's accel ramp, physical travel is below the threshold of visible motion. The only
guidance ("tap to nudge / hold disabled") is suppressed at continuous rates
(`holdDisabledForRate = rate<=0`, false at 8×SID). So a user taps → invisible motion + no hint.
**Verify:** `status.mode` is not misdetected as `"nina"` (that routes taps to a goto — different symptom).
**Fix direction:** default to the tap-only "GUIDE" pulse rate, OR make a tap at a continuous rate a
real nudge, OR always show the tap-vs-hold guidance.
**Files:** `ui/src/components/SlewPad.tsx:81,205,406`; `ui/src/lib/slewController.ts:141-170,176-213`.

### UX-02 — Native guider not selectable in Equipment · DECISION · P2
"we're not hard-coding these are we?" — **confirmed NOT hardcoded.** The Equipment
"Guiding" role is a *device slot*, dynamically populated by `eligibleDrivers("guider")`
from backend probes; only PHD2/NINA offer a `guider` *device*. The **native guider is a
task-provider**, not a device — selected on the **Guide view's "Guide Provider" panel**,
and it only appears once a `guide_camera` role is assigned + connected (Auto already
prefers native when no NINA). So the mechanism exists + is discovered; the failure is
**cross-panel discoverability** + overlapping terminology.
**"isn't it already?"** — Equipment *does* render a "Guiding" row (`RoleSlot`,
`EquipmentView.tsx:381,519,605`) with a driver `<select>` fed by `eligibleDrivers("guider")`
— but that select only lists driver-*devices* (PHD2/NINA); the native guider is not a device,
so it never appears there today. So no, our native guider is not currently selectable in
Equipment.
**Decision → make the native guider selectable in the Equipment panel.** Surface the
guide-provider choice (incl. "AstroDeck native") in Equipment — either as an option in the
Guiding row or in Equipment's Tasks panel alongside autofocus/polar/solve so all four
providers live in one place. Requires a `guide_camera` role assigned+connected for native to
be eligible; show that as the gating hint when it isn't.
**Files:** `native_backend.py:199-209`; `providers.py:317-408`; `ui/src/views/GuideView.tsx:129-264`; `ui/src/views/EquipmentView.tsx:381,519,605-617`; `ui/src/components/equipment/TasksPanel.tsx`.

### UX-03 — Autofocus + Polar-align default to native · DECISION · P2
**Precedence is already native-first.** Autofocus: `_resolve_autofocus` prefers native
when a camera+focuser are connected and the Rust wheel is importable (`backend_af` is
NINA-only) — likely already native; verify via the live TasksPanel *reason* line +
`NATIVE_AVAILABLE`. Polar-align: `native_polar` requires a *real solver*
(`_has_real_solver` → `resolve("solve")`), which **raises without ASTAP on a real mount**,
so Auto falls to sim/NINA — and even an explicit "astrodeck" override re-checks the same
gate. **So polar-align's native default is gated entirely on UX-04.**
**Fix:** resolve ASTAP (UX-04) → polar auto-resolves native, no precedence change needed.
**Files:** `providers.py:171-246`; `devices/nina.py:448`; `ui/src/components/equipment/TasksPanel.tsx:133`.

### UX-04 — Plate-solve default: BUNDLE ASTAP · DECISION **RESOLVED** · P2
**Fact-check (corrected):** ASTAP is **NOT** closed freeware — both the GUI `astap` and the
command-line `astap_cli` are one codebase under **MPL-2.0** (Mozilla Public License 2.0),
source-available. MPL is *weak, file-scoped* copyleft: we **may redistribute the binary**
inside our release (even under AstroDeck's own license) as long as we ship the MPL-2.0 text
+ a source link (`github.com/han-k59/astap`) and keep notices — it does **not** infect
AstroDeck's code (the GPLv3 worry was wrong). Star DBs are Gaia-derived → redistributable
with an **"ESA/Gaia/DPAC"** credit. (Skip HyperLEDA — non-commercial, and unused for solving.)
**Availability:** official `astap_cli` builds exist for **every** supported OS — Windows x64,
Linux x64, macOS Intel + Apple Silicon (all <1.5 MB), plus ARM variants. **No OS gap.**
Currently ASTAP is **not bundled** (`find_astap()` only searches external install paths) and
**no native solver exists** (SimSolver refuses real rigs; `native_solver()`=None; no crate).
**Decision → bundle ASTAP + a star DB in the release** (unblocks UX-03 polar native default).
**Footprint:** per-OS binary 0.3–1.4 MB + one shared star DB — **D05 ~102 MB** (covers 0.6°–6°
FOV, the typical astro case) as the default, or D50 ~850 MB for max FOV robustness.
**Two code touch-ups the bundle needs:** (1) `find_astap()` looks for `astap`/`astap.exe` and
has **no macOS path** → rename the bundled `astap_cli`→`astap` or set `ASTAP_PATH` at launch;
(2) `astap.py` passes no `-d <db_dir>` → colocate the DB with the binary or add `-d`.
CLI flags astap.py already uses (`-f -z -r -fov -ra -spd`, `.ini`/`PLTSOLVD`+WCS parse) **match**
the `astap_cli` contract exactly. Native Rust solver = optional future work, **not** a blocker.
**Supported-OS basis:** CI matrix = ubuntu-latest + windows-latest (Win x64 + Linux x64);
`requires-python>=3.11`; macOS = soft install target; ARM = out of scope.
**Files:** `solve/astap.py` (`find_astap` + `AstapSolver.solve`); `providers.py:249-273`; `scripts/build_release.py` (bundle wiring); `native/crates/` (no solve crate).

### UX-05 — Filter-wheel slot-name assignment · FEATURE · P2
Gear icon on the filter-wheel box → modal to name each slot (for FITS headers +
filenames, NINA-style). **Current state:** `FilterWheel.filter_names` exists and is
**already wired to the FITS `FILTER` header + the status feed** — but it's only ever
hardware-derived (Snowflake firmware letters), never user-settable, and overwritten on
reconnect. **Build:** (a) a per-profile config store for user slot names (seed
`filter_names` from it at connect, hardware letters as fallback — apply generically in
`hub`, not just Wanderer); (b) `POST /api/filterwheel/names` (cap-gated); (c) UI gear
(reuse the `settings` icon) on the Filter Wheel panel/box → modal, one input per slot;
(d) FITS wiring **already done**. **Decision: also match NINA's filter-name-in-filename**
convention (filter token in the saved-image filename), in addition to the FITS `FILTER` header.
**Files:** `base.py:339`; `wanderer_snowflake.py:168`; `hub.py:1405,2243`; `imaging/fitsio.py:35`; `api/app.py:2805`; `ui/src/views/CaptureView.tsx:326`; `ui/src/components/icons.tsx:40`; `guide/native.py:769` (per-profile config-store precedent).

### UX-06 — Atlas search: first query shows nothing · BUG (regression) · P1
Typing "m31" yields no dropdown the first time; backspace+retype fixes it.
**Root cause:** a **regression from the recent CatalogSearch dismissal fix** — the
`dismissed` latch is set by `onBlur` with `relatedTarget === null` (Android keyboard
hide; `Node.contains(null) === false`) and only clears on the *next* search edit, so the
first query's results arrive but `showDropdown` stays false.
**Fix:** reset `dismissed` when new `results` arrive, and/or ignore blur when
`relatedTarget === null` (keyboard-close, not a real tab-away).
**Files:** `ui/src/components/atlas/CatalogSearch.tsx:31,34,61,82`.

### UX-07 — Atlas shows no sky survey · DECISION **RESOLVED** + POLISH · P1
Black canvas + perpetual "LOADING color…". **Root cause (corrected — it's a deploy DATA
gap, not a code regression):** the offline-survey *code* still ships, but the *data* does
not. The ~212 MB order-4 DSS2-color HiPS pack lives under **git-ignored**
`captures/_survey_pack/dss2color/` (`.gitignore`), and `scripts/build_release.py` bundles
**only** `server/` + `ui/dist` (manifest hardcodes `["server","ui/dist"]`) — no pack, no
first-boot seed step. So a naive/self-updated box boots with the code, an **empty pack dir**,
and `SurveyConfig.online_fetch=False` (`config.py:334`) → `tiles.py:110-113` returns a
deterministic 404 for **every** tile → TileEngine draws nothing → black. `pack_present()`
gates on `pack.json`, which is written only after a *clean* fetch (`survey_pack.py:247-256`),
so a partial pack still reads absent. The dev box shows imagery only because its pack was
fetched locally once, outside the release path. The giant "LOADING…" is a dishonest state.
**Decision → bundle a baseline pack + seed on first boot, keep offline-first.** Ship
**DSS2-color order-3 (~45 MB)** as the always-present floor inside the release tarball
(incl. its `pack.json`); on first boot, seed it into the **persistent**
`CAPTURE_DIR/_survey_pack/dss2color` **only if absent** (so self-update won't wipe it and a
user who fetched a deeper order keeps it). Keep `online_fetch=False` (offline-first is
correct for field rigs) and keep the in-app "Download full pack" upgrade
(`POST /api/survey/pack/fetch`, order-4/5). Plus: fix the empty/loading state → honest
"no survey source" CTA. **Footprint:** order-3 color ~45 MB (~51″/px) · order-4 color
~212 MB (~26″/px, current dev default) · order-5 ~0.7–0.9 GB; grayscale ≈ half of color.
**Files:** `scripts/build_release.py:26-60` (bundle + seed); `catalog/survey_pack.py:23-26,145-159,247-256`; `catalog/tiles.py:95-127`; `config.py:330-334`; `ui/src/components/atlas/TileEngine.tsx:116-148`; `ui/src/views/AtlasView.tsx`.

### UX-08 — Tracking-rate pill overflows on mobile · RESPONSIVE · P1
Solar clipped off the right edge. **Root:** `SegmentedControl` has no `min-w-0`/shrink and
its `whitespace-nowrap` buttons set an un-shrinkable ~300-330px floor; the MountView row's
flex `min-width:auto` refuses to shrink below content.
**Fix:** `min-w-0` + allow shrink/wrap + smaller/adaptive text at narrow widths.
**Files:** `ui/src/components/ui/SegmentedControl.tsx:95,129`; `ui/src/views/MountView.tsx:132`.

### UX-09 — Atlas optics inputs oversized + label overlap · RESPONSIVE · P2
`grid-cols-2` forces ~157px tracks; the label rows (no `flex-wrap`) + the "from camera"
chip overflow into the adjacent column; fixed-width inputs (`w-24`/`w-20`) + unit boxes
cramp. **Fix:** responsive optics layout (wrap labels, shrinkable inputs, stack at narrow).
**Files:** `ui/src/views/AtlasView.tsx:584-769`.

### UX-10 — Pixel size raw float · POLISH · P3
`AtlasView.tsx:646` placeholder = `String(liveOptics.pixel_size_um)` (raw camera float
`3.7599999904632`). **Fix:** round/format (e.g. `toFixed(2)`) — add a shared formatter to
`lib/optics.ts` (which has none today); also lines 677, 708.
**Files:** `ui/src/views/AtlasView.tsx:646,677,708`; `ui/src/lib/optics.ts`.

### UX-11 — Plan (Sequence) horizontal overflow · RESPONSIVE · P1
"+ add target" input + "EMPTY PLAN" text clipped. **Root:** non-wrapping `Panel` header
(`ui.tsx:24`) + the add-target input forced `!w-56` (224px) at every width.
**Fix:** wrap the header row / make the input shrinkable (`min-w-0`, `max-w-full`).
**Files:** `ui/src/views/SequenceView.tsx:519-551`; `ui/src/components/ui.tsx:24`.

### UX-12 — App-wide mobile overflow (systemic) · RESPONSIVE · P1
**Recurring root patterns:** fixed-px input widths in non-wrapping flex rows;
`whitespace-nowrap` in un-capped grid/flex controls; the shared **non-wrapping `Panel`
header** (affects every `Panel right={…}`); and **no page-level `overflow-x` guard** on
`<main>`. EquipmentView is the good pattern (flex-wrap + shrinkable `max-w`).
**Systemic fix:** (a) page-level `overflow-x` guard on `<main>`; (b) wrap the `Panel`
header; (c) a shrinkable-width discipline (`min-w-0`/`max-w`/`flex-wrap`); (d) per-view
cleanups. **Audit order (evidence-ranked):** Sequence > Mount > Atlas > Guide (`grid-cols-4`
stats) > Capture/Focus > Power > Settings.
**Files:** `ui/src/App.tsx:425`; `ui/src/components/ui.tsx:24`; per-view.

---

## Fix progress
- **Phase A — SHIPPED** (`fecfec3`, `a07cca2`; tsc -b clean, backend 1523 passed, SegmentedControl 10/10):
  UX-01, UX-06, UX-07, UX-08, UX-09, UX-11, UX-12, UX-13, UX-16, UX-17, UX-18, UX-19. ✅
- **Phase B — SHIPPED** (`d339730`, `7d8348e`, `95433b1`): UX-14, UX-26, UX-31, UX-04, UX-15
  (guide RMS is_arcsec/image_scale + px-vs-arcsec unit label), UX-27 (camera max_bin capability). ✅
- **Phase C — SHIPPED** (`f897a18`, `c5cf96b`, `7e40827`, `8698a89`, `2b73578`, `914c995`):
  UX-28/29/30 (cooler/focuser guards + download watchdog), UX-22 (frame-type select + Bias shutter),
  UX-05 (filter slot names/offsets: per-profile store + POST route + FITS filename token + modal),
  UX-25 (per-filter/binning autofocus), UX-23 (calibration report + panel), UX-24 (dither settle). ✅
- **Phase D — SHIPPED** (`4d1de9f`, `72ce93a`): UX-21 (.btn coarse min-h), UX-32 (.prov-na contrast),
  UX-33 (skip-link), UX-35 (″ glyph), UX-36 (--text-dim2 refs), UX-37 (MountView .label), UX-10
  (fmtMicron), UX-34 (status dingbats → Icon), UX-20 (shared radiogroup keyboard model, 4 groups). ✅
- **ALL 30 UX findings shipped** across A→D — every commit tsc -b clean + test-gated.
- **Merged to main + PUSHED 2026-07-22.** `origin/main` @ `df87ef4`. See **Post-review completion**
  below for the whole-branch review fixes, release-eng bundling (UX-04/UX-07), and the UX-34 remainder.

## Post-review completion — SHIPPED + PUSHED 2026-07-22 (`origin/main` @ `df87ef4`)

After A→D, an independent adversarial whole-branch review plus the two remaining release-eng
items plus the deferred dingbat sweep all landed on `main`. Verification across all three:
full backend suite **1556 passed / 8 skipped**, `tsc -b` clean, SegmentedControl 10/10.

### Whole-branch review — `b0adee9` (7 dimensions, 12 agents, refute-by-default verify)
5 confirmed findings, all fixed — all on the **NINA bridge path**, which the
native/sim/Alpaca/PHD2 fixes had already covered (the bridge drew less scrutiny):
- `NinaGuider.stats()` omitted `is_arcsec` → a NINA rig with a known PixelScale showed genuine
  arcsec RMS labeled "px" (the inverse of UX-15). Now sets `is_arcsec`/`image_scale`, gated on a
  reported PixelScale (mirrors native's `image_scale_known`).
- NINA `max_bin` fell back to the CURRENT bin field `BinX` (default 1), collapsing the UI bin
  ceiling to 1 when `MaxBinX` was absent. Now probes `MaxBinX`/`MaxBin` only (matches alpaca).
- `NavMoreSheet` focus trap collected roving `tabIndex=-1` options → forward-Tab escaped the
  aria-modal dialog in the default state. New `tabbablesIn()` filters non-tabbable elements.
- +6 tests (`test_nina_optics_bin.py`, filter-seed via `_apply_connect_result`, autofocus
  move-before-sweep ordering). Privacy/secrets scan of the whole branch: **CLEAN**.

### Release-eng UX-04 + UX-07 — `0b07005`
- **ASTAP (UX-04):** `solve/astap.py` now passes `-d <db_dir>`. `_VENDOR_ASTAP =
  astrodeck/vendor/astap` (module const, monkeypatchable); `_bundled_db_dir()` returns it only
  when it holds `*.290`/`*.1476` DB files; `ASTAP_DATA` env overrides. Arg assembly extracted to
  `_solve_args()` (unit-testable). Binary discovery already covered `astap`/`astap_cli` per OS
  (closes the macOS gap once a mac binary is bundled).
- **Survey pack (UX-07):** `catalog/survey_pack.py` `seed_bundled_pack()` — first boot copies the
  bundled baseline from `BUNDLED_PACK_ROOT = astrodeck/catalog/_bundled_pack/<slug>` into
  persistent `CAPTURE_DIR/_survey_pack/<slug>` **only if absent** (self-update never wipes it; a
  user's deeper fetched order is kept). `pack.json` copied LAST (`pack_present` gates on it, so a
  crash mid-copy re-seeds next boot). Called best-effort in `api/app.py` boot lifespan. The honest
  "no survey source" Atlas CTA was already shipped (SkyCanvas, `!tileDrew && surveyDegraded`).
- **build_release.py:** `--astap-dir` → `astrodeck/vendor/astap/` (+ MPL-2.0/ESA-Gaia `NOTICE.txt`);
  `--survey-pack` (must contain `pack.json`) → `astrodeck/catalog/_bundled_pack/<slug>/`. Both
  optional — warn-and-omit like `ui/dist`. Manifest `contents` records what shipped.
- **Assets are external — NOT in the repo** (per-OS `astap_cli`, ~102 MB D05 DB, ~45 MB order-3
  pack). Produce them for a real release, then bundle:
  1. download `astap_cli` + the D05 DB into one dir (github.com/han-k59/astap, MPL-2.0);
  2. `python -m astrodeck.catalog.survey_pack fetch --order 3 --dest <packdir>`;
  3. `python scripts/build_release.py --version X --astap-dir <astapdir> --survey-pack <packdir>`.
- +12 tests (`test_astap_bundle.py`, `test_survey_pack_seed.py`, `test_build_release.py`).

### UX-34 remainder — `df87ef4`
7 action-button dingbats → `<Icon>`: ❖→`guide` (GuideView), ⊕→`align` (PolarView), ◎→`focus`
(FocusView), ✛→`align` (MountView), ▸→`play` (PreflightModal), ⟳→`refresh` (DriversPanel ×2).
Idiom: `<Icon name=… size=… className="inline -mt-0.5 mr-1" />`. **Left alone (deliberate):**
semantic status shapes chosen "not colour alone" (`●▲■`, `✓△✕◌⊘`), disclosure carets (`▾▸`),
direction arrows, and typography (`−°″×·`, inline `⚠` in text banners).

## Review results — 2026-07-21 (multi-lens workflow: 57 agents, 47 raised → 35 confirmed / 12 refuted)

The thorough review ran (9 lenses → adversarial refute → synthesis). Full results + the phased fix
order: **`docs/superpowers/plans/2026-07-22-ux-fix-plan.md`**. It confirmed 8 of the 12 seeds
(revising UX-01/08/11 P1→P2 on magnitude), **refuted UX-02 as a defect** (the guide-provider panel
works and defaults to native — retained only as your requested Equipment placement), sharpened UX-04
(no-ASTAP silently runs the *simulator* with no warning), and surfaced **25 new findings**:

| ID | View | Sev | Summary | file:line |
|----|------|-----|---------|-----------|
| UX-13 | Atlas/Mount | **P1** | JNow mount coords consumed as J2000 (no `from_mount_frame()`) → survey/overlay/send-to-plan mis-center ~20′ | `hub.py:2210`, `store.ts:784` |
| UX-14 | Mount/Atlas | P2 | No epoch label — JNow "Pointing" beside J2000 catalog reads as ~20′ error | `MountView.tsx:108` |
| UX-15 | Guide | P2 | Guide RMS labeled "arcsec" but is **pixels** when guide-scope FL unset (real-rig default) | `GuideView.tsx:57` |
| UX-16 | Mount/Guide/Polar | P2 | No pending state on slow async actions (Solve&Sync, Recalibrate, Start) → read dead, double-fire | `MountView.tsx:155` |
| UX-17 | Power | P2 | Dew-heater slider POSTs per drag-tick → snap-back + POST storm (no local draft) | `PowerView.tsx:83` |
| UX-18 | Sequence/Power/Atlas | P2 | Async list-fetch errors swallowed into empty state, no toast | `PlanLibraryPanel.tsx:57` |
| UX-19 | Polar | P2 | Idle "Total error" panel shows busy LED + "Waiting for solve…" before Start | `PolarView.tsx:157` |
| UX-20 | Mount/Settings/Equip | P2 | 4 hand-rolled radiogroups: no arrow-key nav / roving tabindex (a11y) | `Segmented.tsx:24` |
| UX-21 | Guide/Equip/Rotator | P2 | `.btn` buttons collapsed below 44px touch min (two below 24px AA) | `GuideView.tsx:321` |
| UX-22 | Sequence | P2 | No frame-type control → Dark/Bias/Flat unscriptable; calibration writes `IMAGETYP=Light` | `SequenceView.tsx:652` |
| UX-23 | Guide | P3 | Calibration report never surfaced — bad/flipped calibration invisible until runaway | `GuideView.tsx:90` |
| UX-24 | Guide/Sequence | P3 | Dither settle pixels/time/timeout hardcoded, no UI | `GuideView.tsx:97` |
| UX-25 | Focus | P3 | Autofocus has no filter/binning → per-filter AF impossible (needed for UX-05 offsets) | `FocusView.tsx:204` |
| UX-26 | Equipment | P3 | "Detect rig" auto-assign is a hardcoded vendor allowlist; non-listed drivers left unassigned | `equipment.ts:192` |
| UX-27 | Capture | P3 | Binning hardcoded `[1,2,4]`; no camera `max_bin` capability to discover from | `CaptureView.tsx:215` |
| UX-28 | Capture | P3 | Cooler target has no `isFinite` guard → bad entry sends `target_c:null, on:true` | `CaptureView.tsx:381` |
| UX-29 | Focus | P3 | Relative steps / Go-to not clamped to `[0, max]` (relies on silent server clamp) | `FocusView.tsx:194` |
| UX-30 | Capture | P3 | Single-frame readout failure leaves "downloading…" bar spinning forever | `CaptureView.tsx:143` |
| UX-31 | Equipment | P3 | First-run: primary "Connect Rig (0)" disabled; real bootstraps de-emphasized, no empty-state | `EquipmentView.tsx:405` |
| UX-32 | ProviderBadge | P3 | `.prov-na` 11px text in `--text-faint` (sub-4.5:1 contrast) | `index.css:429` |
| UX-33 | App shell | P3 | No skip-to-content; 11-button nav precedes `<main>` in tab order | `App.tsx:383` |
| UX-34 | App-wide | P3 | Inline unicode dingbats as icons (size/baseline mismatch, night-palette risk) — **SHIPPED** status `72ce93a`, action buttons `df87ef4` | `SequenceView.tsx:63` |
| UX-35 | Guide/Header | P3 | Guide RMS prints ASCII `"` for arcsec vs `″` elsewhere | `GuideView.tsx:57` |
| UX-36 | Mount/SlewPad | P3 | 7 refs to undefined `--text-dim2` token (silently aliases `text-dim`) | `SlewPad.tsx:314` |
| UX-37 | Mount | P3 | "tracking"/"rate" use ad-hoc labels vs the `.label` class | `MountView.tsx:121` |

**Second-round gaps** (not yet reviewed in depth): Settings + Monitor + Preview views as first-class
subjects; a capability-discovery sweep across all device panels (rotator/cooler/FW ranges); relay
reconnection/error-recovery states; and an optional running-UI capture pass to pin exact
responsive/contrast/touch magnitudes before/after.

## Product decisions — RESOLVED 2026-07-21
- **Plate solver (UX-04):** **Bundle ASTAP** (`astap_cli`, MPL-2.0 → redistributable) + the
  **D05 star DB (~102 MB)** in the release; available on all supported OSs. Two `astap.py`
  touch-ups (name/`ASTAP_PATH` + `-d` DB path). Native Rust solver = future work, not a blocker.
  — **IMPLEMENTED `0b07005`** (astap `-d` wiring + `build_release.py --astap-dir`); assets external.
- **Survey source (UX-07):** **Bundle a DSS2-color order-3 pack (~45 MB)** as the baseline,
  **seed on first boot** into persistent captures if absent; **keep `online_fetch=False`**
  (offline-first); keep the in-app full-pack upgrade fetcher.
  — **IMPLEMENTED `0b07005`** (`seed_bundled_pack` + `build_release.py --survey-pack`); pack external.
- **Guider selection (UX-02):** **Make the native guider selectable in the Equipment panel**
  (surface the guide-provider choice there; gate native on a connected `guide_camera`).
- **Filter-name-in-filename (UX-05):** **Yes — match NINA** (filter token in the filename, plus
  the existing FITS `FILTER` header).
