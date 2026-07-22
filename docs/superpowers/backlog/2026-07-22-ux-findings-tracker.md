# UI/UX findings tracker — 2026-07-22

Living backlog of UX findings from the naive-state walkthrough (user, mobile via
relay) + two code investigations. Keep updating: mark status as items move
`open → planned → in-progress → fixed → verified`. New findings append at the end.

Categories: **BUG** (functional/regression) · **DECISION** (needs a product/arch
call) · **FEATURE** (net-new) · **RESPONSIVE** (mobile layout) · **POLISH** (formatting/pro).

Severity: P0 (broken core) · P1 (broken on a primary surface) · P2 (missing/rough) · P3 (polish).

| ID | Title | Cat | Sev | Status |
|----|-------|-----|-----|--------|
| UX-01 | Manual slew feels dead (tap does nothing) | BUG | P1 | open |
| UX-02 | Native guider not selectable in Equipment | DECISION | P2 | open |
| UX-03 | Autofocus + Polar-align should default to native | DECISION | P2 | open |
| UX-04 | Plate-solve default / bundle ASTAP vs build native solver | DECISION | P2 | open |
| UX-05 | Filter-wheel slot-name assignment (gear → modal → FITS) | FEATURE | P2 | open |
| UX-06 | Atlas search: first query shows no suggestions | BUG (regression) | P1 | open |
| UX-07 | Atlas shows no sky survey (black + "LOADING…") | DECISION+POLISH | P1 | open |
| UX-08 | Tracking-rate pill overflows (Solar clipped) on mobile | RESPONSIVE | P1 | open |
| UX-09 | Atlas optics inputs oversized; "FROM CAMERA" overlaps labels | RESPONSIVE | P2 | open |
| UX-10 | Pixel size renders raw float `3.7599999904632` | POLISH | P3 | open |
| UX-11 | Plan (Sequence) view horizontal overflow | RESPONSIVE | P1 | open |
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
**Options:** (a) add a pointer from the Equipment "Guiding" row to the Guide provider panel
when native isn't yet available; (b) **surface the guide-provider override in Equipment's
Tasks panel** alongside autofocus/polar/solve so all 4 live in one place (recommended);
(c) docs/tooltip only.
**Files:** `native_backend.py:199-209`; `providers.py:317-408`; `ui/src/views/GuideView.tsx:129-264`; `ui/src/views/EquipmentView.tsx:605-617`.

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

### UX-04 — Plate-solve default / native solver · DECISION · P2
**Fact-check:** ASTAP is **NOT bundled** — `find_astap()` searches external install
locations (like NINA). There is **no native solver** — `SimSolver` refuses real rigs,
every `native_solver()` returns None, no `astro-solve` crate exists (only a design
dossier). Precedence already puts ASTAP first *when found*; the "if astap is native"
premise is false today.
**Options:** (a) make ASTAP the solve default **if bundled** (needs b); (b) **bundle ASTAP**
— license-gated (ASTAP is closed-source freeware by Han Kleijn; verify redistribution
terms out-of-repo; ship `astap.exe` + star DB per-platform) — this is the near-term unblock
for UX-03's polar default; (c) **build a native Rust plate-solver** (`astro-solve`, quad/
index matching) — substantial, a real differentiator that fully removes the external dep.
**Files:** `solve/astap.py:15-31`; `solve/simsolver.py`; `providers.py:249-273`; `native/crates/` (no solve crate); `docs/native-parity/algorithms/nina-platesolving.md`.

### UX-05 — Filter-wheel slot-name assignment · FEATURE · P2
Gear icon on the filter-wheel box → modal to name each slot (for FITS headers +
filenames, NINA-style). **Current state:** `FilterWheel.filter_names` exists and is
**already wired to the FITS `FILTER` header + the status feed** — but it's only ever
hardware-derived (Snowflake firmware letters), never user-settable, and overwritten on
reconnect. **Build:** (a) a per-profile config store for user slot names (seed
`filter_names` from it at connect, hardware letters as fallback — apply generically in
`hub`, not just Wanderer); (b) `POST /api/filterwheel/names` (cap-gated); (c) UI gear
(reuse the `settings` icon) on the Filter Wheel panel/box → modal, one input per slot;
(d) FITS wiring **already done**. Consider filter-name-in-filename too (NINA parity).
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

### UX-07 — Atlas shows no sky survey · DECISION+POLISH · P1
Black canvas + perpetual "LOADING color…". **Root cause:** first-run config gap —
`SurveyConfig.online_fetch` defaults **off** and no offline pack is pre-seeded, so every
`/api/survey/tile` 404s deterministically → black canvas. A real "no survey source —
download the pack / enable online fetch" banner exists but renders tiny/subordinate below
the canvas; the giant "LOADING…" reads as "still working" (misleading).
**Decisions:** (a) survey-source default — ship `online_fetch` ON, a first-run prompt, or
a pre-seeded minimal pack; (b) fix the empty/loading state to a clear "no survey source" CTA.
**Files:** `config.py:334`; `catalog/tiles.py:95-127`; `ui/src/components/atlas/SkyCanvas.tsx:525,549`; `ui/src/views/AtlasView.tsx:173-178,400`; `catalog/survey_pack.py`.

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

## Open product decisions (need the user)
- **Survey source default (UX-07):** ship online-fetch ON / first-run prompt / pre-seed a pack?
- **Plate solver (UX-04):** bundle ASTAP (license permitting) vs build a native Rust solver vs both (bundle now, native later)?
- **Guider selection model (UX-02):** relocate the guide-provider override into Equipment Tasks, or just add a discoverability pointer?
- **Filter-name-in-filename (UX-05):** match NINA's filter-in-filename convention as well as the FITS header?
