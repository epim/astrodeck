# AstroDeck Feature Program — Professionals & First-Night Novices

**Status:** Program brief (governing document for a multi-feature build-out)
**Created:** 2026-07-22
**Author:** Fable (supervisor), from a 52-agent grounded gap analysis
**Audience of this doc:** the Opus designers who will write each feature's detailed spec, and the supervisor (me) who reviews those specs before implementation.

---

## 0. How to read this document

This is **not** a task-by-task implementation plan. It is the portfolio-level brief
that sits *above* the per-feature specs. Each feature below is scoped to a **design
brief** — enough for an Opus designer to run `brainstorming → writing-plans` and for
me to review the result, with the real code seams already located so nobody rediscovers
them.

The pipeline for every feature is:

```
This brief  →  Opus writes spec (brainstorming skill)  →  I review the spec
            →  Opus writes plan (writing-plans skill)   →  I review the plan
            →  subagent-driven-development: implementers at the tier named here
            →  task review + adversarial whole-branch review  →  merge
```

**Design is always Opus, reviewed by me.** Implementation tier is named per feature
and is a *floor recommendation*; the task-brief granularity in `writing-plans` sets the
final per-task tier (a task whose plan contains the complete code to write drops to the
cheapest tier that can transcribe + test it; novel-algorithm or correctness-critical
tasks stay on Opus).

Every claim of "current state" below was **verified against the actual code** by an
adversarial agent instructed to refute it. File/line anchors are from those verifications
(HEAD at analysis time; re-confirm line numbers before editing — the tree moves).

### How this list was produced

157 already-shipped features were inventoried across 6 areas; 5 expert personas (3 pro,
2 novice) proposed 40 candidate gaps against that inventory; each candidate was then
handed to a skeptic told to *find the feature in the code and refute the gap*. All 40
survived — nothing here is "already built." Candidates that turned out already-shipped
(threshold-triggered refocus, the per-filter AF **offset editor** at commits `7e40827`/
`8698a89`, the full alert **dispatcher**, the full report **backend**) were dropped or
down-scoped to only their missing slice.

---

## 1. Strategic framing

AstroDeck already has an observatory-grade spine: native drivers (AM5N mount, Player One
/ ZWO cameras, EAF/CAA/rotator), a native Rust autoguider at PHD2 parity, ASTAP
plate-solving, a HiPS sky atlas with offline pack, multi-night sessions with weather veto,
RBAC, remote relay, and signed self-update. The gaps are not in the spine — they are at
the **two ends of the user spectrum**, and they are different in kind:

- **Professionals** are blocked on the **data contract and unattended-automation edges**:
  the acquisition→processing handoff (FITS headers, calibration masters, export bundles),
  observatory hardware safety (roof close, flat panels), and the depth of automation and
  measurement (conditional sequencing, SNR planning, eccentricity/tilt diagnostics,
  editable guide tuning). Much of this is *finish-what's-started* — the backend exists and
  the UI or the last computation is missing.

- **Novices on their first night** are blocked on the **reward loop and confidence**:
  seeing a bright image accumulate immediately (live stacking), being guided from blank
  install to first frame, being told in words whether focus/guiding/timing is OK, and not
  hitting a dead end when something fails. Their failure mode is *giving up*, and every
  item is chosen to remove one give-up trigger.

**Product thesis:** ship the novice *first-night reward loop* to widen the top of the
funnel, and ship the pro *data + automation edges* to make AstroDeck a credible NINA/ASIAIR
replacement for people who already know what they want. The two tracks share more
infrastructure than they appear to (§3), so we build the shared cores once.

---

## 2. Execution model & global constraints

### Model tiering (implementation)

| Tier | Use for | Examples in this program |
|------|---------|--------------------------|
| **Opus** | Architecture, novel algorithms, correctness-critical wiring | live-stack registration, conditional-sequencer engine, calibration matching core, safety-abort ordering, Bahtinov spike detection |
| **Sonnet** | Integration, pattern-following, UI over a defined backend, well-specified math | report viewer, alert-sink UI + channels, guide-tuning plumb, eccentricity second-moments, moon/HA constraints, most novice UI |
| **Haiku** | Pure mechanical transcription with complete plan code | starter-template gallery data, glossary content expansion, trivial field additions |

Reviewers run at a tier scaled to the diff's risk, never below Sonnet.

### Global constraints (inherited by every feature spec — copy verbatim into each plan)

- **Privacy:** real observing-site coordinates **37.348110 N / 121.801704 W** and the label
  **"My Backyard"** must NEVER appear in code, tests, docs, or fixtures. Site default stays
  **"My Observatory"**, coords `0.0`. (Both prior review privacy scans were clean — keep it that way.)
- **Secrets:** admin tokens and the Ed25519 signing seed are never committed literally.
- **Git:** never `git add -A` — stage explicit paths only. Push to origin only when the user asks.
- **Backend tests:** `server/.venv/Scripts/pytest.exe`. Do **not** run `vite build` concurrently with pytest.
- **UI gate:** `cd ui && npx tsc -b` is the CI gate; the repo has **no jsdom/DOM harness** —
  component logic is tested via `npx tsx` inline-assert files (see `SegmentedControl.test.tsx`,
  `healthStrip.test.ts` for the idiom).
- **astrotown:** do not disrupt the deployed box; this program is dev-branch work until a release is cut.
- **Honest-disabled UI (§11.8 idiom):** dim token + lock glyph + `aria-disabled` + `title`
  reason — never native `disabled`. Client toasts via `useStore.getState().enqueueToast(...)`
  (not WS-bound). New device roles follow the `_DEV_TYPE_TO_ROLE` + hub-singleton pattern.

### Definition of done (per feature)

tsc-b clean · backend suite green · new logic covered by a test in the repo's idiom ·
spec-compliance + code-quality task reviews passed · privacy scan clean · tracker updated.

---

## 3. Shared foundations (build once, consume many)

Several features collapse onto a small number of cores. **Build the core as its own
feature/task, then the consumers become thin.** These are the highest-leverage work in
the program.

| Foundation | What it is | Consumed by |
|-----------|-----------|-------------|
| **F-A · Photometry/SNR core** | `sky-background ADU → electrons` (via `egain`) + the already-measured read noise (`imaging/readnoise.py`) → per-sub SNR, sky-limited sub length, frames-to-target-SNR | PRO-6 (SNR estimator), NOV-4 (suggest settings) |
| **F-B · Complete FITS headers** | Full stacker-critical keyword set + WCS write-back in `imaging/fitsio.py` | PRO-1 (calibration matching keys off temp/gain/exp), PRO-10 (export), any re-solve-free downstream |
| **F-C · `frame_type` capture path** | Let `CaptureView`/`hub` capture Dark/Flat/Bias with correct metadata (today manual capture is always Light) | PRO-1 (library needs captured calibration), NOV-10 (take-darks prompt) |
| **F-D · Report-backend surfacing** | `sequence/report.py` (`SessionReporter`, `FrameRecord`, `SessionReport`, `trends()`) is fully built and thrown away with no UI | PRO-8 (report viewer + live trends), NOV-8 (Monitor waiting status via `schedule.gating_status()`) |
| **F-E · Second-moment star metrics** | Compute real `ecc`/`theta` in `imaging/stars.py` (fields exist as `0.0` placeholders) | PRO-7 (eccentricity reject gate), PRO-13 (tilt inspector per-zone elongation) |
| **F-F · CoverCalibrator / device-role extension** | New device roles wired via `_DEV_TYPE_TO_ROLE` + hub singleton + comhost handlers (`handlers_aux.py`) | PRO-5 (flat panel), PRO-4 (roof/dome) |
| **F-G · Coach-mark / spotlight primitive** | A reusable "highlight this element + tip" overlay with `hasSeen` persistence | NOV-2 (first-run wizard), general onboarding |

---

## 4. Wave plan (dependency-ordered)

Three waves. Wave 0 is mostly **finish-what's-started** — small/medium items where the
backend or engine already exists and only a surface or a last computation is missing, so
both audiences get wins fast. Wave 1 delivers the **novice first-night loop + photometry**.
Wave 2 is the heavy **observatory-automation** lift for pros.

### Wave 0 — Foundations & fast wins
Build **F-B, F-C, F-D, F-E** here (they unblock later waves). Ship the low-effort surfaces
that ride on already-built backends.

- **PRO-2** Complete FITS headers *(= F-B; also fixes 2 live bugs)*
- **NOV-10** One-tap calibration capture + "take darks now?" *(rides F-C)*
- **NOV-8** Monitor "why nothing is happening" *(rides F-D / `gating_status`)*
- **PRO-8** Report viewer + live trend dashboard *(rides F-D)*
- **NOV-6** One-tap "focus my scope" + "Sharp!" verdict
- **NOV-5** Starter sequence templates
- **PRO-9** Alert-sink UI + Discord/email/Slack channels
- **PRO-12** Editable guide tuning + Dec-direction + BLC *(expose orphaned Rust params)*
- **NOV-7** Plain-language guiding narration + verdict
- **PRO-7** Real eccentricity + max-eccentricity reject gate *(= F-E)*

### Wave 1 — Novice first-night loop & photometry
- **F-A** Photometry/SNR core → **PRO-6** SNR estimator + **NOV-4** suggest settings/presets
- **NOV-1** Live-stacking / EAA "Live View" *(the retention centerpiece)*
- **NOV-2** Guided first-run + first-light walkthrough *(= builds F-G)*
- **NOV-3** "What can I image tonight?" difficulty picker
- **NOV-9** In-app explainers + troubleshooting + actionable errors
- **NOV-11** One-tap save/share "first light" image
- **PRO-14** Enforced moon-separation / illumination / hour-angle constraints
- **PRO-11** Configurable file-naming / folder templates

### Wave 2 — Observatory automation & pro data-ops
- **PRO-1** Master calibration-frame library *(depends on F-B, F-C)*
- **PRO-5** Automated flat acquisition + CoverCalibrator *(= builds F-F)*
- **PRO-4** Auto roof/dome close on unsafe + end-of-night *(rides F-F pattern)*
- **PRO-3** Conditional / trigger-based sequencing *(largest single lift)*
- **PRO-10** Stacker-ready export bundles *(depends on PRO-1, F-B)*
- **PRO-13** Sensor-tilt / aberration inspector *(depends on F-E)*
- **NOV-12** Bahtinov-mask focus aid

---

## 5. Feature catalog — Professional track

> Priority: P0 blocks the core job / most users hit it · P1 important · P2 nice-to-have.
> State: **partial** = exists but shallow · **absent** = not there. Design tier is Opus + my review throughout.

### PRO-1 · Master calibration-frame library — P0 · absent · effort L · impl **Opus** (core) + Sonnet (UI/CRUD)
**Gap.** Frames can be *tagged* Dark/Flat/Bias and `IMAGETYP` is written, but nothing
builds masters, indexes them, or matches them to lights. Verified across `imaging/fitsio.py`,
`sequence/*`, `hub.py`, `api/app.py`, `devices/*`, `SequenceView`, `CaptureView` — no
library exists.
**Design approach.** A calibration store keyed by `(frame_type, exposure_s, gain, offset,
temp_bin, binning, filter)`. Ingest tagged frames (needs **F-C**), stack sets into masters
(numpy median/sigma-clip), index with a tolerance window (temp ±°C, age-in-days), expose
coverage, and **warn at sequence start** when a light step has no matching calibration.
Depends on **F-B** (headers carry the match keys).
**Seams.** New `imaging/calibration.py` (+ maybe `calibration/` package); `sequence/engine.py`
pre-flight hook; `api/app.py` endpoints; new UI panel. **Open decision:** stack masters
in-app vs. only index + hand off to an external stacker (see §8).

### PRO-2 · Complete FITS headers for stacker interop — P0 · partial · effort M · impl **Sonnet** *(= F-B)*
> **✅ SHIPPED** on branch `feat/fits-header-completeness` (2026-07-22, commits `43a08fe`→`1864d42`, 7 tasks, +21 tests, suite 1577/8). Spec + plan under `docs/superpowers/{specs,plans}/2026-07-22-fits-header-completeness*`. Also delivers foundation **F-B**. Follow-on minors: `format_ra_fits` seconds-rollover; `_sim_wcs` rotation sense; EGAIN auto-learn N/A.

**Gap.** `imaging/fitsio.py:12-49` `save_fits()` writes only EXPTIME, GAIN, OFFSET,
XBINNING/YBINNING, IMAGETYP, DATE-OBS, CCD-TEMP, BAYERPAT, OBJECT, FILTER, RA, DEC, TELESCOP,
INSTRUME, SWCREATE. Missing the keywords WBPP/Siril/APP/DSS key on. Two live bugs: `SWCREATE`
hardcoded to `0.1.0`; `TELESCOP` missing on manual captures.
**Design approach.** Add FOCALLEN, XPIXSZ/YPIXSZ, AIRMASS, SITELAT/SITELONG, OBJCTALT,
SET-TEMP, FOCPOS/FOCTEMP, ROTATANG, EGAIN, EQUINOX, and **WCS write-back** after a successful
solve. `focal_length_mm`/`pixel_size_um` already live in config; airmass/alt from
`catalog/coords.py`. Fix the version to read the real package version; always write TELESCOP.
**Seams.** `imaging/fitsio.py`; value sources in config + `catalog/coords`; solve→WCS hook in
`solve/*`. Highest-leverage pro item: M-effort, unblocks the whole downstream contract.

### PRO-3 · Conditional / trigger-based sequencing — P1 · partial · effort L · impl **Opus** (engine) + Sonnet (UI)
**Gap.** `sequence/engine.py` (~2095 lines) runs a fixed plan shape — targets × steps with
fixed automation toggles. No when-condition-do-action layer, nested loops, or if/then. This
is the headline reason power users pick NINA's Advanced Sequencer.
**Design approach.** A composable instruction model (triggers, conditions, action blocks)
layered over the existing scheduler without breaking the current fixed plans (they become a
built-in template). **Open decision:** how far toward full NINA parity (see §8) — recommend a
bounded trigger/condition vocabulary first, not an arbitrary scripting engine.
**Seams.** `sequence/models.py`, `sequence/engine.py`, `plans.py`; `SequenceView.tsx`,
`planLibrary.ts`/`planGroups.ts`. Largest single lift — design in isolation, review hard.

### PRO-4 · Auto roof/dome close on unsafe + end-of-night — P1 · absent · effort L · impl **Sonnet** (device) + **Opus** (safety ordering)
**Gap.** No Dome/roll-off role anywhere. `comhost/handlers_aux.py` has switch/CoverCalibrator
scaffolding but no enclosure. Safety abort (`_wind_down(park, warm)` / `on_unsafe`) never
closes a roof — parking under an open roof in rain is the load-bearing missing safety action.
**Design approach.** A Dome/roll-off device role (open/close/slave/park-and-close) wired into
the safety-abort path **before** the pause-hold, and into end-of-night finalize. Ordering is
correctness-critical (close must not strand the mount in the roof's path) → Opus owns the
sequencing task. Shares **F-F** device-role pattern.
**Seams.** New device role via `_DEV_TYPE_TO_ROLE`; `sequence/engine.py` abort + finalize;
`comhost/handlers_aux.py`; safety panel UI.

### PRO-5 · Automated flat acquisition + CoverCalibrator — P1 · absent · effort L · impl **Sonnet** (loop) + **Opus** (sequencer integration) *(= F-F)*
**Gap.** No ADU-target auto-exposure (`target_adu`/`solve_exposure`/`full_well` absent
server-side), no flat-panel/CoverCalibrator device class, no dusk/dawn sky-flat routine.
**Design approach.** An ADU-target loop that solves per-filter flat exposure to a chosen mean
ADU; a CoverCalibrator device role to drive panel brightness + cover; a sky-flat routine that
re-solves exposure as twilight changes. Builds **F-F**.
**Seams.** New device role; `imaging/` exposure solver; `sequence/engine.py` flat step;
`handlers_aux.py`.

### PRO-6 · Integration-time / SNR sub-count estimator — P1 · absent · effort L · impl **Sonnet** *(consumes F-A)*
**Gap.** No estimator anywhere. `imaging/readnoise.py` measures read noise but nothing turns
sky background + read noise + accepted-frame count into projected integration / per-sub SNR /
frames-remaining-to-target.
**Design approach.** Build **F-A** photometry core, then surface projected total integration,
per-sub SNR, and "N subs to target SNR" per filter, live in Sequence + Monitor.
**Seams.** `imaging/` (F-A); `sequence/report.py` for accepted counts; Sequence/Monitor UI.

### PRO-7 · Real eccentricity + max-eccentricity reject gate — P1 · partial · effort M · impl **Sonnet** *(= F-E)*
**Gap.** `imaging/stars.py` `Star` dataclass (lines 18-26) declares `ecc`/`theta` with
comments "Pass 2 … 0.0 placeholder in Pass 1"; `detect_stars()` (lines 36-89) never computes
them — always `0.0`. The frame-reject gate + UI type already exist but the threshold is inert.
**Design approach.** Compute true second-moment eccentricity/PA in `detect_stars` (Pass 2 over
unsaturated mid-bright stars), draw elongation-oriented markers, add a plan-level
`max_eccentricity` to the reject gate. Foundational for PRO-13.
**Seams.** `imaging/stars.py`; reject gate in `sequence/`; `StarOverlay.tsx`; plan model + UI.

### PRO-8 · Report viewer + live trend dashboard — P1 · partial · effort M · impl **Sonnet** *(rides F-D)*
**Gap.** Backend is fully built (`sequence/report.py`: `FrameRecord`, `SessionReporter`
record/finalize, `SessionReport` with by_filter/targets/safety_events/frames, `trends()`) and
has **no viewer** — the whole night is computed and discarded.
**Design approach.** A `ReportView` rendering the per-filter/target integration breakdown,
safety timeline, and HFR/RMS/temp trend lines with the `frames.csv` link; plus a **live**
in-acquisition strip charting HFR/star-count/RMS/temp over the last N subs; enrich per-sub log
columns (gain, star count, focus position, altitude).
**Seams.** New `ReportView.tsx` + route; `graphs.tsx`; small `FrameRecord` field additions.

### PRO-9 · Alert-sink UI + Discord / email / Slack — P1 · partial · effort M · impl **Sonnet**
**Gap.** `alerting.py` (535 lines) is a complete, hardened dispatcher (SSRF-guarded, dedupe,
retry queue, dead-man switch, heartbeat, per-sink level/event gating) — but sinks are only
settable by hand-editing config, and only ntfy/webhook/telegram exist.
**Design approach.** A settings panel to add/test sinks and show per-sink verified/queue/
dead-man health, plus native Discord, SMTP email, and Slack channel adapters following the
existing sink interface.
**Seams.** New `AlertsPanel.tsx` + endpoints; 3 channel adapters in `alerting.py`.

### PRO-10 · Stacker-ready export bundles — P2 · absent · effort L · impl **Sonnet**
**Gap.** No XISF/Siril/PixInsight/WBPP export logic (only incidental prose hits).
**Design approach.** One-click export laying out lights grouped by target+filter+gain+exposure,
each group paired with its **matched master** (needs PRO-1) + a per-sub quality manifest
(HFR/FWHM/eccentricity/RMS/altitude) for weighted stacking; optional Siril/APP folder layouts.
Depends on PRO-1 + **F-B**.
**Seams.** New `export/` module; endpoint; small UI action.

### PRO-11 · Configurable file-naming / folder templates — P2 · absent · effort M · impl **Sonnet**
**Gap.** `hub.py:1673-1681` `_capture_path()` is the sole path builder and is fixed:
`[frame_type, safe_target] + [filter?] + [stamp, counter]` under `CAPTURE_DIR/safe_target/`.
**Design approach.** A NINA-style token template (`$$TARGET$$/$$DATE$$/$$FILTER$$/$$FRAMETYPE$$`
…) for filename + folder structure, per-night date/filter/frame-type foldering, persisted
per-target counter.
**Seams.** `hub.py` `_capture_path`; config + settings UI.

### PRO-12 · Editable guide tuning + Dec-direction + BLC — P1→P2 · partial · effort M · impl **Sonnet**
**Gap.** `GuideView.tsx` `GuideSettingsDrawer`/`AlgoParams` (lines 385-510) render only two
algorithm-**kind** selects. The Rust engine already implements RA aggressiveness/hysteresis/
min-move/resist-switch/lowpass slope, Dec guide-direction (Auto/N/S/Off), and a BLC pulse —
all **orphaned** above the API.
**Design approach.** Plumb the existing engine params through the API and expose them for
editing with the clamps that already exist; add the Dec-direction selector and BLC field.
No new algorithm — pure exposure of built capability.
**Seams.** `guide/` API surface; `native.py`; `guideSettings.ts`; `GuideView.tsx`.

### PRO-13 · Sensor-tilt / aberration inspector — P2 · absent · effort M · impl **Sonnet** *(depends F-E)*
**Gap.** No zone/heatmap/corner-vs-center analysis (verified across `imaging/stars.py`,
`focus/*`, `native/crates/astro-star`, `StarOverlay.tsx`).
**Design approach.** Partition each frame into zones, report per-zone median HFR + mean
elongation (needs **F-E**), render a tilt heatmap, classify asymmetric (tilt) vs radial
(coma/spacing) vs uniform (tracking).
**Seams.** `imaging/` zone stats; new UI overlay/panel; consumes PRO-7 metrics.

### PRO-14 · Enforced moon-sep / illumination / hour-angle constraints — P2 · partial · effort M · impl **Sonnet**
**Gap.** `sequence/models.py` `Schedule` (lines 23-39) has altitude + time-window + solar-cone
constraints but **no** hour-angle or moon fields. Moon math exists but is advisory-only.
**Design approach.** Promote moon separation, moon illumination, and hour-angle to first-class
per-target `Schedule` constraints the scheduler enforces and **skips** on, mirroring the
existing altitude/solar-cone enforcement.
**Seams.** `sequence/models.py`, `sequence/schedule.py`; `catalog/` moon calc; SchedulePanel UI.

---

## 6. Feature catalog — Novice first-night track

### NOV-1 · Live-stacking / EAA "Live View" — P0 · absent · effort L · impl **Opus** (engine) + Sonnet (UI)
**Gap.** No live-stacking engine (`imaging/processing.py` is single-frame only: auto-stretch,
histogram, to_png/jpeg; `FrameFilmstrip` just lists recent frames).
**Design approach.** A server-side Live View that aligns incoming subs (star-match/affine —
can reuse `imaging/stars.py detect_stars`) and running-mean/sum-stacks into one continuously
brightening, SNR-growing preview with a "12 frames · 24 min integrated" readout. Registration
is the hard, correctness-driving part → Opus owns it. **Open decision:** full registration vs.
fixed-frame running-average first (see §8).
**Seams.** New `imaging/livestack.py`; capture→stack hook in `hub.py`; new Live View UI over
`PreviewStage`. The single biggest first-night reward + retention driver.

### NOV-2 · Guided first-run + first-light walkthrough — P0 · partial · effort M · impl **Sonnet** *(= builds F-G)*
**Gap.** Building blocks are individually strong but disconnected — no component chains
site → connect rig → save profile → pick target → cool → first frame. No coach/tour/spotlight/
`hasSeen`. Governing doc: `docs/superpowers/specs/2026-06-15-ux-onboarding-safety-ux-design.md`.
**Design approach.** One linear flow wrapping the existing SitePanel/DriversPanel/EquipmentView/
ProfileList/PreflightModal, plus **F-G** first-time coach marks. Offer "Simulator" as the rig so
a user with no hardware still reaches a first frame.
**Seams.** New `Onboarding/` flow; **F-G** primitive; store flag for first-run/`hasSeen`.

### NOV-3 · "What can I image tonight?" difficulty picker — P1 · absent · effort M · impl **Sonnet**
**Gap.** `catalog/objects.py` `DSO` has id/name/type/ra/dec/mag/size — **no difficulty concept**.
`VisibilityPanel` exists but there's no ranked beginner picker.
**Design approach.** Rank the catalog by tonight's best-window visibility (visibility math
already exists) and tag each object with a plain difficulty rating (Easy: M42/M45/M31; Hard:
small faint galaxies). Difficulty is curated data on the catalog.
**Seams.** `catalog/objects.py` (difficulty field + data); ranking endpoint; new picker UI.

### NOV-4 · Beginner presets + "suggest settings" — P1 · absent · effort M · impl **Sonnet** *(consumes F-A)*
**Gap.** No suggest/recommend/preset anywhere; the Capture form silently accepts any valid
number, so a first-nighter guesses exposure/gain and blows the histogram.
**Design approach.** Canned starter presets (Nebula-broadband, Galaxy, Cluster, First-light)
that set exposure/gain/offset/binning in one tap, plus a "Suggest settings" action computing a
sensible sub length + gain from read noise + live sky background (**F-A**).
**Seams.** Presets in `CaptureView`; suggest endpoint over F-A.

### NOV-5 · Starter sequence templates — P1 · absent · effort S · impl **Sonnet/Haiku**
**Gap.** No template/preset/starter/gallery in `SequenceView`/`PlanLibraryPanel`.
**Design approach.** A small gallery of one-tap plan templates ("60 × 120s Luminance", "OSC
broadband 30 × 180s", "Quick 20 × 60s test") that drop a ready-to-run plan onto the selected
target. Reuses the existing plan model + apply-steps machinery.
**Seams.** Template data + a gallery affordance in `SequenceView.tsx`/`PlanLibraryPanel.tsx`.

### NOV-6 · One-tap "focus my scope" + "Sharp!" verdict — P1 · partial · effort S · impl **Sonnet**
**Gap.** Autofocus engine is full (`/api/focuser/autofocus`, `autofocus.ts`, `FocusVerdict.tsx`)
but requires the user to pick step size/exposure and reports a V-curve/R² that intimidates a
beginner.
**Design approach.** One button that runs the existing autofocus with params auto-derived from
camera + current star sizes, then reports a jargon-free result ("Sharp! Stars are tight —
you're focused"). Wraps built capability.
**Seams.** `FocusView.tsx`; param auto-derivation; extend `FocusVerdict.tsx` copy.

### NOV-7 · Plain-language guiding narration + verdict — P1 · partial · effort M · impl **Sonnet**
**Gap.** The engine tracks the exact phases (finding star / calibrating / settling / guiding)
but they aren't wired through the API/UI, and there's no words-verdict for RMS. `GuideView.tsx`,
`rmsCompare.ts`, `guideRms.ts` show numbers only.
**Design approach.** Narrate phases ("Finding a guide star… Calibrating… Guiding well — you can
relax") and add a words verdict ("0.7 px ≈ 1.1″ — good for 3-minute subs"), doing the
pixels↔arcsec honesty for the user (respect the existing `is_arcsec`/guide-FL handling).
**Seams.** Surface engine phase through `guide/` API; `GuideView.tsx` narration + verdict.

### NOV-8 · Monitor "why nothing is happening" — P1 · partial · effort S · impl **Sonnet** *(rides F-D)*
**Gap.** Backend is fully built: `sequence/schedule.py::gating_status()` (lines 326-390) returns
`{state: waiting/window_closed/never_rises/ready, reason, eta_s, start_ts, stop_ts}` and
`engine.py` exposes it — but it's not surfaced as a prominent plain sentence on Monitor.
**Design approach.** A prominent plain sentence on Monitor: "Waiting for M31 to clear your
horizon — rises above 30° at 11:14 pm, about 47 min" / "Meridian flip in 18 min — imaging will
pause briefly." Pure surfacing of existing data.
**Seams.** `MonitorView.tsx`; consume `gating_status` via store.

### NOV-9 · In-app explainers + troubleshooting + actionable errors — P1 · partial · effort M · impl **Sonnet**
**Gap.** `ui/src/help.ts` glossary has 9 keys wired via `hint`/`Info`; `humanize.ts`
(`humanizeLog`/`humanizeSeqError`, used at `store.ts:1068,1305`) maps a *handful* of failures.
No troubleshooting page; most fields/errors have no in-app answer.
**Design approach.** Expand contextual explainers to the fields a beginner meets first (gain,
HFR, calibration); add an in-app troubleshooting page ("my image is black", "stars are
streaks"); map common failures to a plain cause+fix + doc link shown on the error itself.
**Seams.** `help.ts`; extend `humanize.ts` mapping; new troubleshooting page; error surfaces.

### NOV-10 · One-tap calibration capture + "take darks now?" — P1 · absent · effort M · impl **Sonnet** *(builds F-C)*
**Gap.** `CaptureView.tsx` builds its POST body (lines ~102-109) with no `frame_type` — manual
capture is always Light. No calibration quick-action, no end-of-session prompt.
**Design approach.** A calibration quick-action in Capture that sets `frame_type` and pre-fills
exposure/gain/temp from the last lights, coaches cover/uncover, and fires an end-of-session
"you shot 60 × 120s at −10 °C — take matching darks now?" prompt. Builds **F-C** (which PRO-1
also needs).
**Seams.** `CaptureView.tsx` + `hub.py` capture path (`frame_type`); prompt logic + toast/dialog.

### NOV-11 · One-tap save/share "first light" — P2 · absent · effort M · impl **Sonnet**
**Gap.** `PreviewToolbar.tsx` offers only three raw per-frame exports (Stretched PNG / Lossless
PNG / FITS) — no captioned, phone-sized share image.
**Design approach.** A "Save first light" action producing a nicely-stretched, captioned JPEG
(target, exposure/count, date) of the best sub or the live stack (NOV-1), sized for phone share.
**Seams.** `imaging/processing.py` compositing/caption; endpoint; `PreviewToolbar` action.

### NOV-12 · Bahtinov-mask focus aid — P2 · absent · effort L · impl **Opus** (spike detection) + Sonnet (UI)
**Gap.** Only doc mentions of "bahtinov" exist; no spike/diffraction analysis anywhere.
**Design approach.** A live analyzer detecting a Bahtinov mask's three diffraction spikes on a
bright star, showing central-spike offset with a clear go/stop signal ("turn IN a little" →
"PERFECT — locked"). Spike detection is real CV (line/Hough-style) → Opus owns the algorithm.
**Seams.** New `imaging/bahtinov.py`; live analysis hook; focus UI overlay.

---

## 7. Sequencing summary & highest-leverage picks

| Wave | Pro | Novice |
|------|-----|--------|
| **0 — foundations & fast wins** | PRO-2, PRO-8, PRO-9, PRO-12, PRO-7 | NOV-10, NOV-8, NOV-6, NOV-5, NOV-7 |
| **1 — novice loop & photometry** | PRO-6, PRO-14, PRO-11 | NOV-1, NOV-2, NOV-3, NOV-4, NOV-9, NOV-11 |
| **2 — observatory automation** | PRO-1, PRO-5, PRO-4, PRO-3, PRO-10, PRO-13 | NOV-12 |

- **Single highest-leverage pro item:** **PRO-2 (FITS headers)** — M effort, values largely
  already in config, unblocks calibration matching + export + re-solve-free downstream, and
  fixes two live correctness bugs.
- **Biggest pro *hole*:** **PRO-1 (calibration library)** — P0/L, the marquee data-ops gap.
- **Single highest-leverage novice item:** **NOV-1 (Live View / EAA)** — the first-night reward
  loop and strongest retention driver.
- **Cheapest novice wins to ship first (all S, backend-ready):** **NOV-8, NOV-6, NOV-5** — ship
  these while NOV-1 is built.

**Finish-what's-started cluster** (backend/engine exists, only the surface or last computation
missing — cheapest value per unit effort): PRO-8, PRO-9, PRO-12, NOV-8, NOV-7, PRO-7, NOV-6.

---

## 8. Open design decisions (resolve in per-feature Opus specs, flag to me)

These are genuine product forks that the detailed design should settle (with my review), not
guess:

1. **PRO-1 masters:** does AstroDeck **stack** master calibration in-app (numpy median/sigma-clip,
   more compute + storage) or only **index + match + hand off** raw sets to an external stacker?
   Recommendation: index+match first, in-app master-build as a follow-on.
2. **PRO-3 sequencer scope:** bounded trigger/condition vocabulary vs. full NINA-parity arbitrary
   nesting/scripting. Recommendation: bounded first — cover the common recovery/hardware triggers,
   avoid becoming a scripting runtime.
3. **NOV-1 live-stack:** full star-match registration (handles dither/drift, more code) vs.
   fixed-frame running-average (ships faster, breaks on dither). Recommendation: ship
   running-average with drift-reject, add registration as a second pass.
4. **PRO-9 / secrets:** email (SMTP) and Slack/Discord webhooks introduce credentials — confirm
   they live in the same secret-handling path as existing tokens, never in committed config.
5. **NOV-3 difficulty data:** curate difficulty by hand for the ~58-object catalog, or derive it
   (mag + size + surface brightness heuristic)? Recommendation: derive + allow curated override.

---

## 9. What was deliberately excluded

Dropped as already-shipped or out-of-scope, so we don't rebuild them: threshold-triggered
refocus (ships), per-filter AF **offset editor** (shipped `7e40827`/`8698a89` — only the
auto-**learn** loop is missing, folded as a small follow-on to PRO-12), the alert **dispatcher**
(complete — only UI + channels are PRO-9), the report **backend** (complete — only the viewer is
PRO-8), the Guiding Assistant wizard (the project's own docs defer it until a backlash-measurement
tool exists), continuous temperature-compensation focus (threshold refocus already covers the
need), multi-instrument/dual-rig and all-sky-camera (catalogued in the roadmap as future tiers;
`hub.py:166-186` is single-rig by design today).
