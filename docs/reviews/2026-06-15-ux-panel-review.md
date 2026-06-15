# AstroDeck — UX/UI Expert Panel Review

**Date:** 2026-06-15
**Method:** 12 independent expert reviewers (sonnet subagents), each a distinct
lens, reviewing the actual implementation (UI source + 12 screenshots + backend).
Lenses: ASIAIR veteran, tablet/field ergonomics, accessibility, visual design,
information architecture, novice onboarding, reliability/error-states, imaging-
workflow completeness, image review/analysis, competitive parity, frontend
performance, observatory-automation/safety.

## Verdict (panel consensus)

AstroDeck's **automation backbone is genuinely strong** — multiple experts said
the sequence engine (cooling, meridian flip, filter offsets, HFR gating, guiding
recovery, crash-resume) **matches or beats ASIAIR's sequencer and approaches
NINA**, and the polar-alignment reticle and multi-vendor NINA bridge are real
differentiators.

But the **daily imaging loop and several signature ASIAIR conveniences are
missing**, and it is **not yet "unbox-and-image" or "fire-and-forget."** Two
themes dominated, each raised independently by 4+ reviewers:

1. **The live preview is the weakest link** — a dim, fixed-stretch static PNG
   with no zoom, no adjustable stretch, no star overlay, no history. You cannot
   judge focus or framing from it, which is the single most-used interaction.
2. **There is no unified "monitor" view** — during a run you must tab between
   Capture, Guide, and Plan. ASIAIR's all-in-one live screen is its strongest
   UX card.

---

## P0 — Consensus blockers

- **Live preview unusable for real work** (ASIAIR-vet, image-review, competitive,
  perf, mobile, tablet): no zoom/pan/1:1 pixel-peep; stretch is fixed server-side
  (`processing.py target_bg=0.18`) and NINA-passthrough frames can't be adjusted
  at all; histogram is read-only and doesn't reflect the displayed image; the
  crosshair is decorative and misaligned with the frame; PNG re-fetch per frame
  flashes/blanks with no double-buffer.
- **Site lat/lon hardcoded to San Francisco** (`hub.py self.site = 37.77,-122.42`)
  — breaks altitude, transit, meridian-flip timing, and polar compass directions
  for every other user. No Settings view.
- **No safety monitor / weather / cloud abort** and **no horizon/altitude limit
  enforcement** — clouds keep it shooting; a bad slew can drive the mount into the
  pier/tripod. Hard blocker for genuinely unattended runs.
- **No first-run guidance / pre-flight checklist** — all 8 tabs are live with no
  equipment connected; a novice can do everything out of order; "Run Sequence"
  fires with no readiness check.
- **Error surfacing is fragile**: a single toast slot (overwrites, no auto-dismiss
  timer), the log drawer is desktop-only and undiscoverable, the sequence `error`
  state vanishes from the UI, and a WS drop shows stale data with controls still
  live.

---

## Missing features vs ASIAIR (the core ask) — prioritized

### Tier 1 — Table stakes to be a credible ASIAIR replacement
1. **Live preview tooling**: zoom/pan + 1:1, adjustable stretch (black/mid/white
   sliders coupled to an interactive histogram), per-star HFR/eccentricity overlay,
   saturation/clip mask.
2. **Unified Monitor/dashboard view**: last-frame thumbnail + guide graph + sequence
   progress + temp on one screen (promote when a sequence is running).
3. **Live stacking / EAA mode** — repeatedly named the #1 ASIAIR use case; absent
   entirely.
4. **Sky atlas + framing assistant + mosaic planner**: target overlaid on a DSS/HiPS
   survey with a draggable, rotatable sensor-FOV rectangle; mosaic panel generation.
5. **File manager + image download**: browse/preview/download captured FITS to the
   client (today you need SSH; `saved_path` is never surfaced).
6. **Plate-solve result + annotation**: solved RA/Dec, error, and object/grid
   annotation overlaid on the preview (today solve output is only a log line).
7. **Flats wizard**: ADU-target auto-exposure flats per filter (+ panel brightness
   control); today only darks/bias exist.
8. **Autorun / scheduling**: start-at-dusk, min-altitude gating, switch-at-transit,
   dawn cutoff; today targets run in manual order with no time/altitude gating.
9. **Tonight's-sky / visibility planner**: altitude-over-time curve, transit time,
   moon separation, "best window" + recommended order.
10. **Deployment story**: ASIAIR is self-contained hardware. AstroDeck needs a
    Raspberry Pi image / Docker / one-command installer or the "replacement" pitch
    is aspirational. (Specs are silent on the host.)

### Tier 2 — Important workflow gaps
- Frame-type selector (Light/Dark/Flat/Bias) in Capture & sequence steps (model
  supports it; UI doesn't).
- Plate-solve config: focal length + pixel size → FOV hint (today hardcoded 1.0°).
- Equipment profiles + server-side plan library (today plan is single localStorage
  key; reconnect re-enters everything).
- PHD2 guide assistant: calibration status/trigger, settle confirmation, multi-star
  / guide-star + SNR thresholds.
- Filter-offset editor UI (engine applies offsets but there's no way to set them).
- Dark/flat calibration library with auto-match by temp/exposure/gain/bin.
- Meridian-flip countdown + cooling ETA shown during a run.
- End-of-night session report (frames, integration, rejects, RMS/temp trends).

### Tier 3 — Differentiators (where AstroDeck could beat ASIAIR)
Multi-camera/dual-rig, NINA full-sequencer passthrough, all-sky plate-solve PA,
planetary/video mode, weather/safety + roof automation, cloud backup, push/ntfy
alerting.

---

## Cross-cutting quality issues

**Unattended safety** (P0–P1): no safety/weather abort; no horizon/pier-limit
enforcement; cooler-timeout, autofocus-failure, and guiding-start-failure all
silently downgrade (should offer require_* → abort); no push/ntfy alerting (must
keep a tab open); no dawn cutoff; no device-reconnect-and-resume; no watchdog.

**Onboarding** (P0–P1): no wizard/checklist; tabs not gated on readiness; "DAY"/
"NIGHT" button label is inverted-verb confusing; no tooltips for offset, HFR, step
size, dither (define HFR! "lower=sharper, 1.5–3px good"); no below-horizon warning
before GOTO/Run.

**Touch/field ergonomics** (P0–P1): slew-pad cells ~58px and STOP text 10px;
focuser ±, filter chips, and sequencer ✕-delete (~16px) below the 44px minimum;
header NIGHT/LOG ~28px; mobile bottom-nav 8 items with 8px labels; destructive
Abort/Stop/Disconnect fire on single tap (need hold-to-confirm); no press-and-hold
slew ramp; no haptics; no landscape/portrait adaptation at ~768px tablet width;
no screen-lock/touch-guard.

**Accessibility** (P1–P2): `--text-dim #5d6c85` fails WCAG AA; night-mode status
colors are all coral-red so good/warn/bad collapse and it's not true dim-red
astronomy-safe; state is color-only (LEDs/tones need shape/icon cues); no keyboard
focus rings (`outline:none`); SVG label fonts 8–9px scale to ~6px; no global
brightness dimmer; no separate night-brightness memory.

**Reliability/error UX** (P0–P1): queue/persist toasts + add auto-dismiss + a LOG
badge with unseen-error count; make the log drawer available on all viewports;
render the sequence `error` state (don't hide it); add a "reconnecting"/stale
indicator and `AbortSignal.timeout` on fetch; add a NINA-link health LED (backend
↔ NINA, separate from browser↔backend); proxy the manual Alpaca scan server-side
(browser CORS).

**Performance** (P1–P2): `App.tsx` uses a broad `useStore()` → the whole tree
re-renders every guide tick (split selectors); `Histogram` (128 `<rect>`) and the
guide SVGs rebuild each tick unmemoized (collapse to one `<path>`, `React.memo` +
`useMemo`); raw frames encode as PNG not JPEG (5–8× bandwidth); status poll bundles
serial Alpaca mount+camera calls (gather + split slices); `_persist()` is sync in
the async loop (`to_thread`); double-buffer the preview `<img>`.

**Visual polish** (P1–P3): replace the inconsistent unicode-glyph nav icons with a
real single-weight SVG set (Lucide etc.); panels leave ~35–45% dead vertical space
(stretch with flex); the 2-corner bracket motif reads asymmetric/incomplete;
empty states are plain uppercase text (ghost illustrations); add view cross-fade
transitions; preserve a dimmed starfield in night mode; richer device rows on the
Rig page (icons + live sub-readouts + per-device reconnect); a "LIVE" badge +
pulsing border on the looping preview.

**IA fixes** (P0–P2): reorder the rail to match session flow — **Rig → Align →
Mount → Focus → Capture → Guide → Plan → Power** (Align currently sits *after*
Mount, which is backwards); surface Solve & Sync on Capture (the frame lives
there) with an inline result; the camera dew heater is orphaned in Capture's
Cooler panel while box dew heaters live in Power; add active-filter to the header;
add a live preview strip to the Focus view so you're not blind while focusing.

---

## Recommended build order

1. **Live preview overhaul** (zoom/pan, adjustable stretch + interactive histogram,
   star overlay, JPEG + double-buffer) — biggest daily-use win, unblocks focus &
   framing judgment.
2. **Settings/site + plate-solve config** (lat/lon/elevation, focal length, pixel
   size) — a true correctness P0; everything geo-dependent is wrong off-SF.
3. **Unified Monitor view** + reliability surfacing (error state, toast queue, log
   badge, reconnect/stale, NINA-link health).
4. **Unattended safety** (safety-monitor hook + horizon limits + require_* escalation
   + ntfy/webhook alerting).
5. **Onboarding** (pre-flight checklist, readiness gating, tooltips/HFR help,
   below-horizon warning) + **touch ergonomics** (tap sizes, hold-to-confirm).
6. **File manager + download**, **frame-type selector**, **plan/profile persistence**.
7. Bigger features: **sky atlas + framing/mosaic**, **live stacking**, **flats
   wizard**, **autorun scheduling**.
8. **Deployment**: Docker / Raspberry Pi image / one-command installer.

## Minor corrections to panel notes
- FocusView **does** have the V-curve graph (one reviewer was unsure) — confirmed
  present in `components/graphs.tsx`/`FocusView.tsx`.
- Catalog with an empty query **does** return all targets sorted by magnitude
  (`search_catalog("")`), so the Mount table is populated on load.
