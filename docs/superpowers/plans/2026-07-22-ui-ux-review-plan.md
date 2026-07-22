# AstroDeck UI/UX Review — Plan

> **For agentic workers:** this is a *review methodology*, not an implementation plan. It produces a
> prioritized, deduped findings list that feeds `docs/superpowers/backlog/2026-07-22-ux-findings-tracker.md`
> and, from there, a follow-on fix plan. Run it as the multi-agent workflow described in §7.

**Goal:** Find every UX defect a professional astrophotographer would hit across AstroDeck's whole
surface — on phone, tablet, and desktop, in day and night mode, from a naive first-run state through a
full imaging session — and rank them so we fix what matters first.

**Why this review, why now:** the naive-state walkthrough surfaced a class of defects the *previous*
review missed entirely — mobile horizontal overflow, a manual-slew control that feels dead, a black
Atlas with a stuck "LOADING…", oversized/disordered inputs, and a search regression that review itself
introduced. Those escaped because the last pass was **desktop-only, static-code-only, and single-lens**.
This plan is built to not miss them again: it **drives the live UI at real phone widths**, it
**exercises controls and observes their effect** (not just reads their code), it **benchmarks against a
professional/NINA bar**, and it **runs adversarial multi-lens synthesis** so one blind spot doesn't sink
the whole review.

---

## 1. Scope

**In scope — every user-facing surface:**
- **Views:** Equipment, Align/Polar, Mount, Focus, Capture, Guide, Atlas, Plan (Sequence), Power,
  Monitor, Settings (all sub-tabs: Drivers, Profiles, Site, Updates, Auth/Access, Relay), Login.
- **Cross-cutting shells:** the app frame/nav (`App.tsx`), the shared `Panel`/`ui.tsx` primitives, the
  `SegmentedControl`, toasts/errors, modals, the WS status/reconnect banner.
- **Full journeys:** naive first-run → add a driver → assign roles → connect → slew → focus → solve →
  polar-align → guide → sequence a capture → review a frame. The seams *between* views are where the
  discoverability bugs live (guider selection is the proof).

**Out of scope:** backend algorithm correctness (covered by the 1522-test suite), the Rust engine
internals, and anything requiring photons on the sky (that's the separate first-light shakedown).

**Non-disruptive constraint:** astrotown is in the user's hands (naive, 0.2.16). The live-driving in
§4 runs against a **local sim instance**, never the box. Real-rig checks (§6) touch astrotown only
read-only or when the user hands it back.

---

## 2. Review dimensions (lenses)

Each lens is a distinct pair of eyes. A finding is only as trustworthy as the lens that is *looking
for that class of defect* — the last review had no "responsive" lens and no "does-the-control-do-
anything" lens, so those defects were invisible to it. The nine lenses:

1. **Functional correctness** — does each control *do what it says*? Click it, watch the effect (or the
   API call + state change). Dead/confusing controls (UX-01 slew) live here. This lens must *interact*,
   not read.
2. **Responsive / mobile** — at 360–414px, does anything overflow, clip, or overlap? Is every control
   reachable and tappable (≥44px targets)? UX-08/09/11/12 live here. Phone-first, because that's how the
   user actually operates the rig.
3. **Discovery & no-hardcoding** — are options *discovered* from devices/providers, not hardcoded? Is the
   right option *findable* without cross-panel tribal knowledge? UX-02 (native guider) lives here.
4. **Sensible defaults & flow** — from naive first-run, does the app default to the right thing and guide
   the next step? Native-first task providers (UX-03), survey source (UX-07), slew default rate (UX-01).
5. **Professional polish** — formatting (UX-10 raw float), alignment, spacing, consistent iconography,
   copy tone. Does it read "neat and professional," the user's exact bar?
6. **NINA / ASIAIR parity** — for each pro workflow (filter names→FITS, autofocus, plate-solve, guiding
   assistant, meridian flip, dithering, framing), does AstroDeck match the expected capability? Gaps →
   features (UX-05 filter names). This lens carries a parity checklist.
7. **States** — loading / empty / error / offline / disconnected / permission-denied. Are they *honest*?
   (UX-07's stuck "LOADING…" is a dishonest state.) Every async surface gets each state exercised.
8. **Accessibility** — keyboard reachability, focus order, contrast (esp. red night mode), ARIA on custom
   controls (the `SegmentedControl` is a radiogroup — verify), touch-target size.
9. **Data correctness** — units, precision, rounding, timezone/LST, coordinate epoch (JNOW vs J2000),
   RA/Dec formatting, °/arcmin/arcsec. Wrong numbers erode trust faster than ugly ones.

---

## 3. Viewport & theme matrix

Every view is walked in each cell that applies (phone is the priority row):

| | Phone ~390×844 | Tablet ~820×1180 | Desktop ~1440×900 |
|---|---|---|---|
| **Night mode (red)** | primary | check | check |
| **Day mode** | primary | check | check |

- **Phone night mode is the primary cell** — it's the real operating condition (dark site, phone in
  hand). Overflow, contrast, and tap targets are judged hardest here.
- Also spot-check an extreme narrow (360×640) and a landscape phone (844×390) for the worst overflow.
- Night mode gets its own contrast pass — red-on-black must stay legible without breaking the "no white
  light" rule.

---

## 4. Method A — live driving (the gap-closer)

The previous review never rendered a pixel. This phase does, at phone width, on a **local sim
instance**, driving real interactions with Playwright/Chromium.

**Environment:** boot a local AstroDeck on the sim profile (the sim rig gives a camera/mount/focuser/
filterwheel/guider without hardware). This is resettable and cannot disrupt astrotown. Serve the built
UI (build **before** the run, never concurrent with pytest — vite deletes `ui/dist` mid-suite).

**Per view × per viewport cell, the driver:**
1. Navigates to the view, waits for load, **screenshots** (the artifact every lens reads).
2. **Measures overflow:** compare `document.documentElement.scrollWidth` to the viewport width; flag any
   element whose right edge exceeds the viewport (the deterministic UX-08/11/12 detector).
3. **Exercises the primary controls:** type in inputs, open dropdowns, toggle segmented controls, click
   the primary action — and captures the resulting DOM/API/state change. (This is what catches "the tap
   does nothing" — you *see* whether anything happened.)
4. **Walks the states:** force loading (throttle), empty (naive profile), error (kill the backend mid-
   call), offline (drop the WS) — screenshot each.
5. Records: screenshot path, overflow measurements, interaction results, console errors, failed network
   calls.

**Seed interactions (from the reported issues — verify each is reproduced or fixed):**
- Atlas: type `m31` as the *first* query, assert the suggestion dropdown appears (UX-06).
- Atlas: load with no survey source, assert the empty-state is honest, not a stuck "LOADING…" (UX-07).
- Mount: render the tracking-rate `SegmentedControl` at 390px, assert "Solar" is fully visible (UX-08).
- Mount/Slew: tap a direction, assert a visible/observable effect or an explanatory hint (UX-01).
- Sequence: render at 390px, assert the "+ add target" row doesn't overflow (UX-11).
- Every view at 390px: assert `scrollWidth ≤ viewport` (UX-12 systemic sweep).

**Output:** a `live-capture/` folder of screenshots + a structured JSON of measurements per cell.

---

## 5. Method B — static code review (root-cause depth)

Live driving finds *symptoms*; code review finds *root causes* and *classes*. Run per lens, over the
components each view renders, cross-referenced to the §4 captures. The two investigations already done
prove the value: they turned "slew does nothing" into "default continuous rate + tap = sub-visible
travel + suppressed hint," which points straight at the fix.

For each finding, code review must produce: exact `file:line`, the root-cause mechanism, whether it's an
**instance** or a **systemic class** (UX-12 is a class: shared `Panel` header + no page-level overflow
guard), and the candidate fix direction. Systemic classes are worth more than instances — fixing the
`Panel` header once fixes every `Panel right={…}`.

**Known good patterns to hold others against:** EquipmentView (flex-wrap + shrinkable `max-w`) is the
responsive reference; `guide/native.py`'s per-profile JSON is the config-store reference for UX-05.

---

## 6. Method C — real-rig functional verification

Some findings can only be judged against real hardware behavior, and the sim can hide them (the AM5
accel ramp that makes UX-01's tap sub-visible doesn't exist in sim). When the user hands astrotown back
(or via read-only status), verify:
- UX-01: does a slew tap move the real AM5? confirm `status.mode != "nina"`; measure the travel at the
  default vs the guide rate.
- UX-03: read the live TasksPanel *reason* lines — what does autofocus/polar/solve actually resolve to on
  the connected rig, and why?
- UX-05: after setting filter names, capture a frame and read the FITS `FILTER` header end-to-end.
- Any state/data finding that depends on real device telemetry.

This phase is gated on user availability and stays strictly non-disruptive.

---

## 7. Execution as a multi-agent workflow

Per ultracode, run this as an orchestrated `Workflow`, not a single pass — the lenses are independent
and adversarial verification is what keeps false findings out.

**Phase 1 — Live capture (fan-out by viewport cell).** One agent per (viewport × theme) cell drives all
views via Method A against the local sim instance, emitting screenshots + measurement JSON. Barrier: we
need the full capture set before the lenses read it.

**Phase 2 — Lens review (pipeline, fan-out by lens).** One agent per lens (§2), each reading the Phase-1
captures **and** the code (Method B), emitting structured findings `{id, lens, view, viewport, severity,
file:line, root-cause, instance|class, fix-direction, evidence(screenshot)}`. Pipeline, not barrier —
each lens's findings flow to verification as soon as that lens finishes.

**Phase 3 — Adversarial verify (fan-out per finding).** Each finding gets an independent skeptic prompted
to **refute** it (reproduce from the capture/code; is it real, is the severity right, is it already
fixed?). Default-to-refuted on uncertainty. This is the gate that would have caught the regression the
last review *introduced* — and that keeps "looks wrong in a screenshot" from becoming a false P1.

**Phase 4 — Parity gap (single focused agent).** Walk the NINA/ASIAIR parity checklist (§2 lens 6)
independently of the live captures — parity gaps are about *absent* capability, which screenshots can't
show. Emits capability gaps as feature-findings.

**Phase 5 — Synthesis + completeness critic (single agent).** Dedup across lenses (same root cause seen
by responsive + polish → one finding), merge instances into their systemic class, rank by severity ×
frequency × user-visibility, and run a completeness critic: *what did we not look at?* (a view not
driven, a state not forced, a lens with zero findings that should have some, a claim never verified on
real hardware). Its gaps become the next round's work. Output updates the tracker and proposes the fix
plan's phase order.

**Scale knobs (ultracode = thorough):** ≥3 skeptics per P0/P1 finding; loop Phases 2–3 until a round adds
nothing new (loop-until-dry); every silent cap (views sampled, states skipped) gets `log()`-ed so
"covered everything" is never a lie.

---

## 8. Deliverables

1. **Updated tracker** (`docs/superpowers/backlog/2026-07-22-ux-findings-tracker.md`) — all confirmed
   findings, deduped, each with lens/severity/root-cause/`file:line`/evidence, superseding the seed list.
2. **A prioritized fix plan** (`docs/superpowers/plans/2026-07-23-ux-fixes.md`, follow-on) — phased:
   (P0/P1 responsive + functional first, then defaults/discovery, then features, then polish), each phase
   an independently shippable release, systemic fixes before their instances.
3. **The surfaced product decisions** (below) put to the user before the dependent fixes start.
4. **A parity scorecard** — AstroDeck vs NINA/ASIAIR across the pro workflows, gaps flagged as roadmap.

---

## 9. Product decisions to surface (block dependent fixes)

These are *decisions*, not defects — the review flags them and the user calls them:
- **Survey source default (UX-07):** ship online-fetch ON, a first-run prompt, or a pre-seeded minimal
  offline pack? (Blocks the Atlas empty-state fix.)
- **Plate solver (UX-04):** bundle ASTAP (if its license permits redistribution) vs build a native Rust
  solver vs both (bundle now, native later)? (Blocks the polar-align native default, UX-03.)
- **Guider selection model (UX-02):** relocate the guide-provider override into Equipment's Tasks panel
  (all four providers in one place), or just add a discoverability pointer from the guider row?
- **Filter-name-in-filename (UX-05):** match NINA's filter-in-filename convention in addition to the FITS
  header?

---

## 10. Seed findings (from the walkthrough — the review must reproduce, root-cause, or refute each)

The 12 catalogued items in the tracker (UX-01 … UX-12) are the review's *seeds*, not its *ceiling*. Each
enters Phase 3 as a claim to verify; the review's job is to confirm/refine/refute them **and** to find
the ones the user didn't happen to hit — the completeness critic (Phase 5) exists precisely so the
review's output is broader than the walkthrough's input.

---

## Self-review of this plan

- **Closes the prior review's gaps?** Yes — adds live phone-width driving (missed: overflow), interaction
  observation (missed: dead slew), honest-state checks (missed: stuck LOADING), adversarial verify
  (missed: self-introduced regression), and a parity lens (missed: filter-name feature).
- **Every reported issue mapped to a lens + a method?** Yes (§2 lenses, §4 seed interactions, §10).
- **Non-disruptive to astrotown?** Yes — live driving is local-sim only; real-rig phase is read-only /
  user-gated.
- **Runs as the ultracode workflow shape?** Yes — capture → lens fan-out → adversarial verify → parity →
  synthesis + completeness critic, loop-until-dry.
