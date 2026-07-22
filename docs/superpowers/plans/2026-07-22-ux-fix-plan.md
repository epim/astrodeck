# AstroDeck UI/UX Review — Results & Prioritized Fix Plan

> Output of the multi-lens adversarial review (9 lenses → refute → synthesize), 57 agents,
> 47 findings raised → **35 confirmed, 12 refuted**, 0 needs-live (all decidable from code).
> Seed issues from the walkthrough are UX-01…UX-12 (in the tracker); new findings are UX-13…UX-37.
> Companion: `docs/superpowers/backlog/2026-07-22-ux-findings-tracker.md` (index) ·
> `docs/superpowers/plans/2026-07-22-ui-ux-review-plan.md` (methodology).

34 verified findings from 9 lenses (functional, responsive, discovery, defaults-flow, polish,
parity, states, a11y, data-correctness). After dedup/merge: **8 confirm existing seeds**,
**25 are new (UX-13…UX-37)**.

## New findings

| ID | View | Sev | Summary | file:line | Class? |
|----|------|-----|---------|-----------|--------|
| **UX-13** | Atlas / Mount | **P1** | Mount status RA/Dec is served straight from `tel.get_position()` **without** `from_mount_frame()`, so a real (JNow) Alpaca mount's coords are consumed as J2000 — survey tiles + FovOverlay + free-roam Send-to-Plan mis-center ~20′. | `store.ts:784`, `AtlasView.tsx:435`, root `hub.py:2210-2212` | class (epoch) |
| **UX-14** | Mount / Catalog / Atlas | P2 | No epoch label anywhere: Mount "Pointing" shows JNow (Alpaca) beside J2000 catalog/Atlas coords, so a perfectly-slewed target reads as a ~20′ error. Shares root with UX-13. | `MountView.tsx:108` | class (epoch) |
| **UX-15** | Guide | P2 | Guide RMS is hard-labeled "arcsec" (title + `″` + sub-arcsec tone thresholds) but is **pixels** whenever guide-scope FL is unset (the default on a real rig; `image_scale=1.0` fallback). | `GuideView.tsx:57` (root `native_backend.py:143`) | class |
| **UX-16** | Mount / Guide / Polar | P2 | No local in-flight/pending state on slow async actions (Solve&Sync, Force Recalibrate, Start Guiding/Alignment) — buttons stay enabled through the multi-second server round-trip, read as dead, double-fire. | `MountView.tsx:155`, `GuideView.tsx:82` | class |
| **UX-17** | Power | P2 | Dew-heater/PWM slider is bound to server-echoed value with no local draft and POSTs `/api/switch/set` on **every** drag tick → thumb snaps back, POST-per-pixel storm over the relay. CaptureView already does this correctly. | `PowerView.tsx:83` | instance |
| **UX-18** | Sequence / Power / Atlas | P2 | Async list fetches swallow errors into empty state — PlanLibraryPanel shows "No saved plans yet" on a failed GET, PowerView renders empty panels, Sequence search shows "no matches". No toast. | `PlanLibraryPanel.tsx:57` | class |
| **UX-19** | Polar | P2 | Idle "Total error" panel unconditionally renders the `!hasReading` else-branch → busy LED (`led-sweep`) + "Waiting for solve…" before Start is ever pressed. | `PolarView.tsx:157` | instance |
| **UX-20** | Mount/Settings/Equipment/More-sheet | P2 | Four hand-rolled `role="radiogroup"` widgets announce the radio pattern but implement only `onClick` — no arrow-key nav, no roving tabindex. Canonical `ui/SegmentedControl` has the full model. | `Segmented.tsx:24` | class (a11y) |
| **UX-21** | Guide/Equipment/Rotator | P2 | Inline & panel-header `.btn` buttons collapsed below the 44px touch min by `!py-0.5`/`!py-1`/`min-h-9` (two below the 24px AA floor). | `GuideView.tsx:321` | class (a11y) |
| **UX-22** | Sequence | P2 | Step editor has no frame-type control; `DEFAULT_STEP` hardcodes `frame_type:'Light'`, so Dark/Bias/Flat are unscriptable and calibration targets write `IMAGETYP=Light`. No Flat path at all. | `SequenceView.tsx:652` | instance |
| **UX-23** | Guide | P3 | Calibration is trigger/clear-only — `GuideStats` carries no RA/Dec rates, angle, orthogonality, or backlash, and nothing renders a report; a bad (flipped) calibration is invisible until the mount runs away. | `GuideView.tsx:90` | instance |
| **UX-24** | Guide / Sequence | P3 | Dither exposes only a pixel count; settle pixels/time/timeout are hardcoded backend-side with no UI. Sane defaults exist → tuning nice-to-have. | `GuideView.tsx:97` | instance |
| **UX-25** | Focus | P3 | Autofocus posts only `{exposure_s, step}` — no filter or binning, so per-filter AF (core NINA workflow, and the way to build offsets for UX-05) is impossible. | `FocusView.tsx:204` | instance |
| **UX-26** | Equipment | P3 | "Detect hardware rig" auto-assign is a hardcoded vendor allowlist; only the camera role has an offers-based fallback, so any non-allowlisted role-offering driver is added but silently unassigned. | `equipment.ts:192` | instance |
| **UX-27** | Capture | P3 | Binning options hardcoded `[1,2,4]` with no camera reference; no `max_bin`/supported-binning field exists to discover from. Diverges from the `gain`/`max_gain` hint next to it. | `CaptureView.tsx:215` | instance |
| **UX-28** | Capture | P3 | Cooler Set/Cool posts `target_c: Number(coolerTarget)` with no `Number.isFinite` guard; `JSON.stringify(NaN)→null`, so a bad entry silently sends `target_c:null, on:true`. | `CaptureView.tsx:381` | instance |
| **UX-29** | Focus | P3 | Relative steps (`±` up to 1000) and Go-to are not clamped to `[0, foc.max]` (max IS displayed); out-of-range positions are commanded, relying on a silent server clamp. | `FocusView.tsx:194` | instance |
| **UX-30** | Capture | P3 | A Single-frame capture that fails server-side during readout leaves the "downloading…" striped bar spinning forever. An error toast does fire; Stop recovers. | `CaptureView.tsx:143` | instance |
| **UX-31** | Equipment | P3 | First-run: the lone visually-primary action ("Connect Rig (0)") is disabled while the two real bootstraps (Detect / Simulator) are de-emphasized plain buttons with no "start here" empty-state. | `EquipmentView.tsx:405` | instance |
| **UX-32** | ProviderBadge | P3 | `.prov-na` renders 11px label text in `--text-faint` — a token the stylesheet's own contract declares decoration-only/sub-4.5:1 (~3.2:1 day, ~4.4:1 night). | `index.css:429` | class (a11y) |
| **UX-33** | App shell | P3 | No skip-to-content link; the 11-button `<nav>` precedes `<main>` in DOM/tab order, so keyboard/switch users traverse the whole rail to reach view content. | `App.tsx:383` | instance |
| **UX-34** | App-wide | P3 | Inline unicode dingbats (⚠ ✓ ✛ ⊕) hand-typed as status/action icons instead of the SVG `Icon` set — size/baseline mismatch, and OS color-emoji can break the pure-red night palette. | `SequenceView.tsx:63` | class (polish) |
| **UX-35** | Guide/TouchGuard/Header | P3 | Guide RMS prints ASCII `"` for arcsec while every other arcsec readout uses `″` (U+2033) — same metric punctuated two ways. Co-located with UX-15. | `GuideView.tsx:57` | class (polish) |
| **UX-36** | Mount / SlewPad | P3 | 7 spots reference a non-existent token via `text-[color:var(--text-dim2,var(--text-dim))]`; `--text-dim2` is never defined, so it silently aliases `text-dim` — latent color trap. | `SlewPad.tsx:314` | class (polish) |
| **UX-37** | Mount | P3 | "tracking"/"rate" labels use ad-hoc lowercase `text-xs text-dim` while peer labels use the uppercased `.label` — one card mixes two label treatments. | `MountView.tsx:121` | instance |

## Confirmed seeds (from the walkthrough)

| ID | Seed | Sev | Verification note |
|----|------|-----|------|
| **UX-01** | Manual slew feels dead | P1→**P2** | Confirmed default `rateIdx=1` continuous → sub-visible tap; hint suppressed. Downgraded: tap DOES flash arrow + "HOLD · 0.03°/s" + haptics, so "nothing visible" was overstated. |
| **UX-04** | Plate-solve / ASTAP | P2 | Confirmed: no ASTAP → `_resolve_polar` returns sim; `session.py:94` streams fabricated az/alt; PolarView never gates Start on provider realness. Bundle decision RESOLVED; **add an interim sim-warning gate**. |
| **UX-05** | Filter offsets / slot-names | P2 | Confirmed: `apply_filter_offsets` defaults True but offsets only written by sim/NINA — no route, no editor, no per-filter AF → on-by-default toggle is a silent no-op on a native rig. |
| **UX-07** | Atlas no survey / perpetual LOADING | P1 | Confirmed (UI half): `SkyCanvas.tsx:549` skeleton has no `surveyDegraded` guard → LOADING persists over the honest CTA. (Data half — pack absent in release — is the separate RESOLVED deploy-gap fix.) |
| **UX-08** | Tracking-rate pill clips (Solar) | P1→**P2** | Confirmed: SegmentedControl no `min-w-0`/`w-full` + `whitespace-nowrap` → SOLAR overflows at ≤360px. |
| **UX-09** | Atlas optics labels overlap | P2 | Confirmed: `.label` nowrap + inline "from camera" chip in `grid-cols-2` → ~180px content vs ~160px tracks spills. |
| **UX-10** | Pixel-size raw float | P3 | Confirmed: `String(liveOptics.pixel_size_um)` unrounded; Alpaca single-precision → `3.7599999904632`; no formatter in `lib/optics.ts`. |
| **UX-11** | Sequence header overflow | P2 | Confirmed: nowrap button + `input.field !w-56` (224px `!important`) in the non-wrapping shared Panel header → clip at 360px. |

**Revisions:** UX-01/08/11 severities revised P1→P2 in verification (mechanism confirmed; impact is partial/edge, not a broken core). **UX-12** (systemic app-wide overflow) is the parent class for UX-08/09/11.
**Refuted as defects (kept for other reasons):** UX-02 guider (the provider panel works and defaults to native — *not* a defect; retained as a user-directed placement change). The native-guider "empty panel/dead-end" framing was refuted.

## NEEDS-LIVE (running-UI capture pass — optional, for before/after evidence)

Every finding is code-confirmed, but these assert a rendered/behavioral outcome a capture pass should pin down before/after the fix:
- **Responsive clipping** — UX-08/09/11 (+UX-12): confirm exact clip at 320/360/390px, no horizontal body scroll after fix.
- **Touch targets** — UX-21: measure the three worst hit areas on a coarse-pointer device.
- **Contrast** — UX-32: confirm measured ratios (computed from tokens, not sampled).
- **Dishonest states** — UX-19/UX-30/UX-07/UX-18: most persuasive as live captures of the misleading frame.
- **Data-correctness** — UX-13/14 (~20′ epoch offset), UX-15 (px-labeled-arcsec): a live solve-vs-catalog diff on the AM5N would quantify the real offset.
- **a11y keyboard** — UX-20/UX-33: confirm with an actual keyboard/screen-reader pass.

## Completeness gaps (what a second round should target)

- **No running-UI adversarial pass** — responsive/contrast/touch magnitudes are computed from source, not sampled. A live capture round finalizes severities on UX-08/09/11/21/32.
- **No on-sky validation** — UX-13/14 (epoch), UX-15 (guide scale), UX-04 (sim polar) are proven in code; real-world magnitude is inferred.
- **Thin/under-reviewed:** Settings view (only cited as radiogroup host), Monitor view (only the correct counter-example), Preview/imaging surfaces (PreviewStage/FrameStats/ScaleBar), Auth/RBAC/session UI, WS-relay reconnection UX. Discovery lens returned only 2 findings — likely more capability-negotiation instances (rotator ranges, cooler limits, filter counts).
- **Second round:** (1) the live capture pass; (2) Settings + Monitor + Preview as first-class subjects; (3) a capability-discovery sweep across all device panels; (4) reconnection/error-recovery states on the relay path.

## Proposed fix phases

Systemic/class fixes precede their instances within each phase.

### Phase A — Correctness & primary-surface regressions (P0/P1 + high-visibility P2)
1. **UX-13 (P1)** — Apply `from_mount_frame()` in the hub status builder (or emit `ra_j2000`/`ra_jnow`). *Root fix; also fixes UX-14's normalize path.*
2. **UX-07 (P1)** — Gate the tile skeleton on `!surveyDegraded` + ship the baseline pack (RESOLVED).
3. **UX-01 (P2, was P1)** — Default `rateIdx` to the tap-only GUIDE rate, or always render the tap/hold hint.
4. **UX-06 (P1 seed)** — Atlas first-query regression (carry from tracker).
5. **UX-12 → UX-08/09/11** — Wrap the shared Panel header (`ui.tsx:24`), add `<main>` `overflow-x` guard (`App.tsx:425`), shrinkable-width discipline; then the three instance clips.
6. **UX-16 (P2 class)** — Per-action pending state (mirror CaptureView's phase machine); gate Solve&Sync on a connected mount.
7. **UX-17 (P2)** — Local-draft slider + commit on pointer release.
8. **UX-18 (P2 class)** — Distinct error flag vs empty (reuse `EquipmentView` `loadErr` idiom) across PlanLibrary/Power/Sequence-search.
9. **UX-19 (P2)** — Distinguish idle from measuring in the Total-error panel.

### Phase B — Defaults, discovery & data honesty
10. **UX-14 (P2)** — Epoch chips on all coordinate readouts (once UX-13 normalizes, label everything J2000).
11. **UX-15 (P2)** — Report `image_scale`/`is_arcsec` on guide status; switch title/unit/thresholds to px when scale is 1.0.
12. **UX-26 (P3)** — Drive auto-assign from discovered offers, not a vendor map.
13. **UX-27 (P3)** — Add camera `max_bin` capability; build options from it.
14. **UX-31 (P3)** — First-run empty-state promoting Detect / Simulator.
15. **UX-04 (P2)** — Bundle ASTAP (RESOLVED) + interim sim-warning gate on PolarView Start.

### Phase C — Parity features
16. **UX-22 (P2)** — Frame-type select + Flat path.
17. **UX-05 (P2)** — Filter-offset editor + `POST /api/filterwheel/names|offsets` (RESOLVED direction).
18. **UX-25 (P3)** — Filter/binning on autofocus (enables UX-05 measurement).
19. **UX-23 (P3)** — Emit + render calibration report with pass/fail + reject.
20. **UX-24 (P3)** — Settle pixels/time/timeout fields.
21. **UX-28/29/30 (P3)** — Cooler validation; focuser clamp; bound the download phase with a timeout/status signal.

### Phase D — a11y & polish (class before instance)
22. **UX-20 (P2 class)** — Lift `SegmentedControl` keyboard model into a shared hook / replace the four bespoke radiogroups.
23. **UX-21 (P2 class)** — Give `.btn` an intrinsic coarse-pointer min-height; audit `!py-*`/`min-h-9` sites.
24. **UX-33 (P3)** — Skip-to-content link + `#main` target.
25. **UX-32 (P3)** — `.prov-na` → `--text-dim`; convey "unavailable" via border/dot.
26. **UX-34 (P3 class)** — Replace inline dingbats with `<Icon>`.
27. **UX-35 (P3 class)** — Shared arcsec/arcmin unit constant (`″`/`′`); bundle with UX-15 (same lines).
28. **UX-36 (P3 class)** — Replace 7 `--text-dim2` fallbacks with `text-dim`.
29. **UX-37 (P3)** — Give tracking/rate spans the `.label` class.
30. **UX-10 (P3)** — Add `fmtMicron` to `lib/optics.ts`; round the placeholder.
