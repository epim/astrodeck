# Atlas + Plan UX/Functionality Review — Grounded Findings (2026-07-12)

Ground truth from three read-only code explorations (client Atlas, server survey pipeline
with live probes against :8801, Plan tab + tooltip). Feeds the three approved fix waves.
User approved all three waves 2026-07-12. Every claim below carries file:line evidence;
hypotheses are marked.

---

## Symptom 1 — poor panning performance

Rendering model: single `<img class="survey">` double-buffered via in-memory `Image()`
preloader under an SVG geometry layer + HTML label layer (`ui/src/components/atlas/SkyCanvas.tsx:314-325`).
No canvas, no tiling, no CSS-transform intermediate pan.

Compounding causes, worst first:

1. **Visibility request storm (dominant).** `VisibilityPanel` fetch effect has NO debounce
   and is keyed on `[ra_hours, dec_deg, altLimit, reloadKey]` (`VisibilityPanel.tsx:62-90`),
   fed raw `center.ra_hours/dec_deg` from `AtlasView.tsx:648-649`. Every pointer-move tick
   during a drag fires `GET /api/visibility` (server astropy ephemeris) — one HTTP request
   per drag tick for the whole gesture.
2. **Dimmer re-arms per drag pixel.** `targetUrl` is a `useMemo` over
   `[center, fovZoomDeg, survey, stretch]` (`SkyCanvas.tsx:134-137`; `surveyUrl` formats
   ra/dec to 6dp at :71-79). `onPointerMove` calls `onCenterChange` per native tick
   (:220-237 → `AtlasView.tsx:247-248` `setFraming`). Every `targetUrl` change runs
   `setImgReady(false)` (:169-171); `dimmerOn = mode==="survey" && (!imgReady || loading)`
   (:294) → 55%-opacity black overlay (:343-348) covers the image for the entire gesture.
3. **Every settled pan is a cache miss.** Server cache quantization is ~3" RA / ~7.2" dec /
   0.01° fov (`server/astrodeck/catalog/survey.py:80-97` `_cache_key`, sha1 over quantized
   params) — far finer than any drag. Measured: cache hit 5-9 ms; miss+success ~2.8 s;
   miss+upstream-failure **10.6-10.8 s**. Bonus bug (live-verified): float imprecision in
   `round(value/step)` at bucket boundaries double-buckets visually identical coords
   (raw `-5.3940` vs `-5.3960` for Δ0.00001°).
4. **No abort / stale-response guard.** The 300 ms debounce (:139-166) only cancels the
   pending timer; an in-flight `Image()` load can't be cancelled and `onload` closures don't
   check "is this still the latest request" — a late slow response can overwrite `shownUrl`
   with a stale frame. No `AbortController` anywhere in the file.
5. **No memoization** anywhere in `ui/src/components/atlas/*` (grep-confirmed), and
   `useStatus()` selects the whole `RigStatus` object (`store.ts:1174`) which is replaced
   wholesale per WS status event (:981-989) → full Atlas tree re-render per telemetry tick.
6. No single-flight on the server: concurrent identical requests to a new key both hit
   upstream independently (`survey.py:241` plain `Path.exists()`; live-verified).

## Symptom 2 — image disappears

1. **A failed fetch discards a good image.** `img.onerror` fires `onSurveyError` →
   `AtlasView.tsx:499` `setSurveyDown(true)` → `mode="schematic"` (:243-244). Render is
   gated on `mode`, not on having a valid image: `{mode==="survey" && shownUrl && <img/>}` /
   `{mode==="schematic" && <gradient div/>}` (`SkyCanvas.tsx:313-330`) — the mounted, valid
   image is unmounted on any transient error.
2. Dimmer blackout during gestures (Symptom 1 cause 2) reads as disappearance.
3. Stale late-landing fetch can revert `shownUrl` to an older frame (hypothesis, code-grounded).

## Symptom 3 — perpetual loading + sticky "Survey offline"

1. `loading=true` set at debounce top (`SkyCanvas.tsx:144`), cleared only in `img.onload`
   (:152) / `img.onerror` (:158) / statically when schematic (:141).
2. **Server worst case ~30.4 s** per request: `_TIMEOUT_S=15.0`, 2 attempts, 0.4 s fixed
   sleep, catch-all `Exception` (`survey.py:118-139`). All failures collapse to
   503 `{"detail":"survey unavailable","fallback":"schematic"}` (:245-253).
3. **`surveyDown` is a one-way latch.** Only reset path: `framing.survey` *value change*
   (`AtlasView.tsx:160-164`). While schematic, the fetch effect early-returns
   (`SkyCanvas.tsx:139-143`) so **no retry is ever attempted** — `onSurveyLoad` can never
   fire again. Sole escape: touch the Survey dropdown (`SurveyControls.tsx:85-93`).
4. Single hard-coded upstream `https://alasky.cds.unistra.fr/hips-image-services/hips2fits`
   (`survey.py`), no health endpoint anywhere in the package (grep-confirmed), no alternate
   source. Live probe: TLS handshake to that host fails intermittently from this box
   (~5 s/attempt → the measured 10.7 s failures; one probe succeeded 2.8 s/71 KB).
5. Banner render sites: sr-only `SkyCanvas.tsx:335-341`, visible banner :443-447.

## Symptom 4 — focal-length warning persists

1. Warning gates on `haveOptics` = all FOUR of `focal_length_mm, pixel_size_um,
   sensor_width_px, sensor_height_px` > 0 (`ui/src/lib/framing.ts:71-96` `fovFromOptics`).
   Computed independently (duplicated) in `AtlasView.tsx:172-176` and `SkyCanvas.tsx:106-110`.
2. Server defaults: pixel/sensor = 0 = "use camera" (`server/astrodeck/config.py:66-71`).
   The camera merge exists server-side: `hub.effective_optics()` (`hub.py:828-863`) →
   `config.optics_computed` (`app.py:997,1280`) and `status.optics` (used correctly by
   `FocusView.tsx:76`). **AtlasView gates on raw `config.optics`** and uses
   `optics_computed` only for calibrate-from-solve pixel size (`AtlasView.tsx:221`).
3. **No UI anywhere edits pixel_size_um / sensor_width_px / sensor_height_px**
   (repo-wide grep: zero editable inputs). One-time persisting auto-populate only fires
   during profile-connect, gated on `optics.auto_from_camera` and the camera driver
   reporting those attrs (`hub.py:1112-1123`).
4. Net: sim/fresh/non-reporting rigs can never clear the warning by setting focal length,
   and the copy ("Set a focal length above…") names the wrong remedy. Duplicate banners:
   `AtlasView.tsx:469-477` (header) + `SkyCanvas.tsx:435-439` (in-canvas).
5. Focal-length set path itself is correct: inline field → `PUT /api/optics` + `loadConfig()`
   (`AtlasView.tsx:147-148,184-214`).

## Symptom 5 — "Recenter on target" / no search on Atlas

- `SurveyControls.tsx:152` → `AtlasView.tsx:259-266`: snaps to seeded catalog target
  (set only by MountView's Frame button, `MountView.tsx:174-180` → `openFraming`) or live
  mount position in free-roam. Functional; label wrong in free-roam.
- **No target search on Atlas** (full read confirmed; empty-state hint at `AtlasView.tsx:96-98`
  redirects to Mount catalog). Catalog search exists on Mount AND a second, different search
  on Plan (see below).

## Symptom 6 — framing-box rotation handle

- Box drawn as SVG panels in rotated `<g>` (`FovOverlay.tsx:65-156`); a drag-rotate knob IS
  drawn top-center (`SkyCanvas.tsx:391-395`, cy=VIEW*0.07) but its hit zone is a heuristic
  rectangle in the parent `onPointerDown`: `py < rect.height*0.14 && |px-w/2| < w*0.22`
  (:197-218) — not a hit-test on the drawn handle. **The box itself is never grabbable for
  rotation** — grabbing it pans. Rotate drag maps pointer angle about screen center (:228-236).
- Other rotation inputs: stepper + ±5° buttons (`SurveyControls.tsx:166-179`), keys `[`/`]`
  (`SkyCanvas.tsx:265-266`). All converge on `AtlasView.tsx:249` `setFraming({rotation_deg})`.

## Symptom 7 — tooltip flicker (app-wide)

Component: `Tooltip`/`InfoDot` in `ui/src/components/ui.tsx:342-412`.
- `pointer-events-none` IS correctly set on the bubble (:389) — the classic occlusion-steal
  loop is NOT the mechanism.
- Handlers live on the trigger span only (:376-380): `onMouseEnter/Leave`, `onFocus/Blur`,
  AND `onClick={…setOpen(v=>!v)}`.
- `InfoDot` inflates hit-box with `-m-[15px] p-[15px]` (:406); bubble sits ~6 px away
  (`mb-1.5`, :362-365) with `max-w-[240px]` prose (help.ts strings run 2-4 sentences).
- Mechanisms (hypotheses, code-grounded): (1) **touch tap** = synthesized `mouseenter`
  (open) then `click` (toggle → close): one tap flashes open/shut — strongest candidate on
  this touch-first UI; (2) **mouse drift**: only a 15 px halo is hoverable while the bubble
  is 40-70 px tall and unhoverable → drift exits → close → drift back → reopen loop.
- Usage: ~24 instantiation points across 12+ files (9 direct `InfoDot`, internal ones in
  `Field`/`Stat` at ui.tsx:52,98,109, 15 `hint=` sites). One-component fix repairs all.

---

## Server survey pipeline facts (for Wave 1 server items)

- `GET /api/survey/cutout.jpg` (`survey.py:222-257`): ra in HOURS (×15 server-side),
  fov ≤ 20°, width 256-1200 (default 768, height forced = width), surveys enum
  `CDS/P/DSS2/color|CDS/P/DSS2/red|CDS/P/2MASS/color`, stretch linear|asinh.
  `Cache-Control: max-age=86400`. Client always requests width=768 (`SkyCanvas.tsx:79,135`).
- Disk cache at `CAPTURE_DIR/"_survey"` (`survey.py:52`, `hub.py:92`); eviction TTL 7 d /
  200 MB / 2000 files (:142-219). No memory index, no single-flight.
- `POST /api/framing/mosaic` (`framing.py:167-205`) is pure math (3.6 ms live), zero survey
  dependency — do not touch in Wave 1.
- `GET /api/visibility` + `POST /api/visibility/order` (`visibility.py:510-538`) local astropy.

## Plan tab map (label "Plan", id `sequence`, file `SequenceView.tsx` — naming split)

- Nav: `App.tsx:39-52` (`:48` `{id:"sequence", label:"Plan"}`), `VIEWS` :67-85,
  mobile `BottomNav.tsx:23-29` + `NavMoreSheet` overflow.
- **Plan's own catalog search** (250 ms debounce, `GET /api/catalog?q=`, 6 results,
  `SequenceView.tsx:69-70,95-102`, UI :346-363). `addTarget()` (:168-177) appends a bare
  target: `center:true, autofocus_first:true, calibration:false`, one default step —
  **no rotation_deg, no mosaic_group, no below-horizon confirm** (Atlas's confirm is at
  `AtlasView.tsx:361-374` gated on `visNight.never_rises_above_limit`).
- Target cards :372-463 (PA badge :379-388 — "auto" iff rotator present), step rows :420-457,
  mosaic-group blocks :465-502 (grouping :149-166), delete+5s-undo :82-93.
- Automation panel :521-591 (InfoDot ×4 at 530,553,560,577); Plan panel :510-519.
- Run gating: `usePreflight` (`PreflightStrip.tsx:141`, 30 s poll :27; checks in
  `lib/preflight.ts:59-144`), Run button :596-616, `PreflightModal` :638-644 →
  `POST /api/sequence/start` :111-114; pause/resume/abort :281-315; recover :223-227,331-336.
- **Atlas→Plan handoff (works well)**: `AtlasView.tsx:359-394` `sendToPlan` → server mosaic
  (client-mirror fallback :340-357/:325-335) → `addTargetsToPlan` (`store.ts:733-745`,
  replace-by-`mosaic_group` then append; plan persisted under PLAN_KEY :680-687) → toast →
  `setView("sequence")`; one-shot banner survives remount via store (:389-393;
  `SequenceView.tsx:44-52,196-214`). `rotation_deg` set at send (`AtlasView.tsx:317`).
- **No Plan→Atlas path**: `openFraming` called only from `MountView.tsx:180` and
  `AtlasView.tsx:236`.
- **Dead scheduling contract**: `Schedule` model (`types.ts:630-640` — start_mode
  now|dusk|dawn|time, stop_mode, min_altitude_deg, max_run_min, on_missed) +
  `defaultSchedule()` (`store.ts:160-172`, comment says it backfills for a "schedule
  sub-panel") + `SequencePlan.safety_check`/`meridian_flip_warn_min` (`types.ts:393-394`)
  — **zero UI anywhere** (exhaustive grep). Confirmed finding, not hypothesis.
- Plan has no altitude/visibility chart (grep-confirmed); `VisibilityPanel` mounts only in
  Atlas (:647-652), scoped to the currently-framed target.

## Dead/vestigial inventory

- `FovOverlay` `activeIndex` panel-highlight (:34-35,122-142) fully implemented, never fed —
  `AtlasView.tsx:483-501` never passes `activePanel`.
- Schedule model + safety_check + meridian_flip_warn_min (above).
- Duplicate optics warning banners; duplicate pixel-scale warnings (SurveyControls + canvas).
- Inline IIFE `adjustedPa` recompute per render (`AtlasView.tsx:586-596`) — style only.

---

## Approved wave plan (user approved all three, 2026-07-12)

### Wave 1 — reliability & performance (no design questions; straight to spec)
1. Debounce VisibilityPanel fetch (match survey's 300 ms).
2. Stop resetting `imgReady` per drag tick; CSS-transform the current `<img>` during the
   gesture and fetch on settle (dimmer only for settled fetches, if at all).
3. Keep last good image on fetch failure (gate render on having an image, not on mode).
4. Make `surveyDown` self-healing: retry with backoff on next settle; clear on successful
   load; keep manual "Schematic" selection as an explicit user choice.
5. Stale-response guard + effective abort for in-flight image loads.
6. Gate `haveOptics` on camera-merged optics (`optics_computed`/`status.optics`), make the
   warning name the actual missing fields, add UI to edit pixel size + sensor dims (or a
   "from camera" indicator), de-duplicate the two banners.
7. Tooltip fix in ui.tsx: suppress tap/click double-toggle, add open/close grace timers
   (hover-intent); all ~24 sites inherit.
8. Server: timeout 15 s → ~6 s; single-flight coalescing per cache key; FOV-scaled cache
   quantization (bucket ∝ fov, e.g. fov/20) so revisits hit; fix boundary float flip.
9. Perf hygiene where cheap: memoize atlas children / narrow status selector.

### Wave 2 — interaction quality
- Rotation handles on the framing box itself (see design decisions below); hit-test real
  elements; cursor/affordance feedback; keep stepper/keys.
- Target search on the Atlas screen (reuses `GET /api/catalog`).
- Context-correct recenter label ("Recenter on target" vs "Recenter on mount").
- Expose or delete the dead `activePanel` mosaic highlight.

### Wave 3 — make Plan actually plan
- Unify target entry guarantees across Plan search vs Atlas (see design decisions below).
- "Frame in Atlas" affordance on plan target cards (Plan→Atlas path via `openFraming`).
- Per-target visibility in Plan (altitude sparkline / tonight ordering; server
  `POST /api/visibility/order` already exists).
- Build or delete the dormant Schedule contract (see design decisions below).
- Naming split (Plan/sequence/SequenceView) — rename only if cheap; not user-visible.

### Design decisions (user answers 2026-07-12)
1. **Rotation handle: stalk handle on the box** — a rotation grip protruding from the
   framing box's top edge (PowerPoint/Figma style), rotating about the box center; box
   body/sky still pans; `[`/`]` keys and stepper kept. Replace the invisible top-of-canvas
   knob-zone heuristic with a real hit-test on the drawn handle element.
2. **Atlas search: search box on the Atlas header** — same `GET /api/catalog` as Plan's
   search; picking a result frames it (sets framing target + recenters). Mount and Plan
   keep their searches. Recenter button gets context-correct labeling.
3. **Scheduling: BUILD THE FULL SCHEDULE SUB-PANEL** (user chose the big option over the
   recommended visibility-first). Per-target start/stop gating UI (start now|dusk|dawn|time,
   stop conditions, min_altitude_deg, max_run_min, on_missed policy) wired so the SEQUENCE
   ENGINE actually honors the fields (server-side engine work required — engine currently
   ignores Schedule entirely), plus per-target altitude sparklines / tonight ordering in
   Plan (`POST /api/visibility/order` exists). The dormant `Schedule`/`defaultSchedule`
   contract becomes load-bearing; also decide `safety_check`/`meridian_flip_warn_min`
   surfacing as part of this spec.
4. **Target entry: parity in place** — keep Plan's quick-add but give it the same
   guarantees as Atlas-sent targets (below-horizon confirm, sensible default rotation
   handling) plus a "Frame in Atlas" button on every plan target card (Plan→Atlas via
   `openFraming`).

### Execution notes
- Wave 1 = pure fixes; spec straight from this doc's grounded findings.
- Wave 2 = interaction upgrades per decisions 1-2.
- Wave 3 = decisions 3-4; the full scheduler makes this the largest wave (sequence-engine
  changes + UI). Each wave: spec → plan (seam-grounded) → sdd execution, subagent-first.
