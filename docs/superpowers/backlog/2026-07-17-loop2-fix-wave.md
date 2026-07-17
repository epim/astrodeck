# Loop-2 fix wave — triage of round-3 reviews + user evening items

Sources: `docs/superpowers/reviews/2026-07-17-codex-review3.md` (authoritative — 13 FIXED,
evidence-backed), `2026-07-17-gemini-review3.md` (visual corroboration only; its ROLE-OP
"live test" and "docs match re-gated UI" rows are unreliable — see progress ledger),
user-reported items queued in `2026-07-16-loop1-fix-wave.md` §QUEUED FOR LOOP-2, and the
stale role-docs bug found 2026-07-17 (progress ledger).

Both reviewers are DONE — dist rebuilds and docs edits are safe again.

## Wave tasks (code first, docs last)

### G1 — Stall-warning truth + radar positive health (UI)
- Stall warning (`CAPTURE STALLED?`) currently fires independent of sequence state: a
  COMPLETE session showed `COMPLETE`, `10/10`, and `CAPTURE STALLED? last frame Nm ago`
  simultaneously for minutes (codex R3-MON-01, Major, evidence
  review-evidence-3-codex/R3-MON-complete-still-stalled.png); round 2 saw the same on
  PAUSED. Gate stall detection to states that can still produce frames (running only —
  a paused run's frame gap is explained by the pause); clear the timer/warning
  immediately on COMPLETE/ABORTED/ERROR/PAUSED. Add a pure-logic test for the gating
  (terminal + paused states → no stall). [R3-MON-01, R2-PLN-02 residue]
- Radar tile-health badge: after successful paint the badge disappears entirely, so a
  later silent failure is indistinguishable from healthy. Keep a persistent positive
  state (`updated Nm ago`) when healthy, `loading…` while pending, `tiles unavailable`
  on failure — never nothing. [R3-MON-03]
- Exposure validation upper bound: also reject absurd/scientific-notation values with
  explicit supported bounds (pick sane camera-agnostic bounds, e.g. 0 < s ≤ 3600, and
  say so in the inline error). [R3-CAP-02 note]

### G2 — Plan surface merge (UI; USER-DECIDED + codex convergence)
- COMBINE the Plan Library and Plan boxes into ONE harmonious panel (user instruction,
  screenshot .superpowers/sdd/loop2/user-plan-library-truncation.png) — and codex
  independently filed the same confusion (R3-PLAN-02: current plan / Sessions / Plan
  library read as one long undifferentiated surface; R3-PLAN-03: no saved/unsaved
  ownership cue near the plan name).
- Design intent (judgment delegated, propose in report): one plan panel with a sticky
  header carrying plan NAME + saved/unsaved state + Save/Save-as/Import/Export actions;
  the library rows live in/under the same panel; Sessions stays its own clearly bounded
  region.
- Plan-name width priority: names are ellipsized to ~3 chars ("Fli…") while metadata
  chips + load/export/delete take the row — names are the PRIMARY identifier; wrap or
  two-line rows. [user screenshot]

### G3 — Atlas handle + site load affordance (UI)
- Sky Atlas: at some screen widths the drag handle atop the camera/FOV box sits BEHIND a
  label — invisible and ungrabbable. Find the stacking/layout collision; handle must
  always be visible and grabbable at every width. [user]
- Site panel: after `Load selected preset`, show a prominent `Loaded into form — not
  active yet` state with the next action (`Set site`) highlighted — the two-step flow is
  correct but easy to miss cold. [R3-SITE-02]

### G4 — Loopback-trust test mode (server + small UI note)
- Review instances treat ALL loopback clients as trusted local admin, making a real
  operator/viewer role pass impossible (codex R3-ROLE-01 BLOCKED: created disposable
  users but stayed admin; /login on loopback fell into a disconnected viewer shell).
- Add a config flag (e.g. `auth.trust_loopback: bool = True`; False → loopback clients
  must authenticate like any remote client). Server tests: flag off → unauthenticated
  loopback gets viewer-level/401 treatment per existing auth model; flag on (default) →
  current behavior unchanged. PEP-563 watch-out: any new body model at module scope.
- This unblocks the role verification in loop 3 / user smoke. Keep default True —
  no behavior change for normal users.

### G5 — Docs: role truth rewrite (after G1-G4; THE stale-docs fix)
Four guides still document pre-fix behavior ("controls visible but 403") that the final
fix round re-gated — enumerated by codex and verified by controller grep:
- remote-access-and-roles.md:117-144 — rewrite matrix + caveat to re-gated truth
  (operator/viewer see DISABLED controls with lock notes, never 403s from enabled
  controls); add loopback-trust prerequisite to the disposable-account procedure using
  G4's new flag; keep the procedure honest about what loopback trust means.
- monitor.md:19-34 — rewrite control section from the capability contract; remove 403
  caveat.
- plan-and-sequences.md:11-15 — state exactly which role can see and use each control.
- sessions-multi-night.md:80-84 — regrade controls now disabled with lock note.
Three role CONTRADICTIONS (verify each against server caps in code, then write truth):
- equipment-and-profiles.md:5-6 vs :153 — who can actually connect (config.backend
  holder = admin per capabilities.py; fix the "operator or admin" line).
- weather.md:11-15 vs :88-93 — weather VIEW is admin-only (view.site_precise) but
  `ignore weather tonight` is CAP_CONTROL_CAPTURE (operator CAN use it from the Sessions
  card without seeing the weather panel). State this exactly, and note it as a known
  product-decision-pending quirk.
- site-and-locations.md:8-10 vs :106-112 — verify site-write caps in code; write truth.
Also: troubleshooting.md stall checklist — post-G1, note that stall warnings no longer
appear on paused/terminal runs (update the "if COMPLETE still shows stalled" guidance).

## NOT this wave (recorded)
- First-image next-step cue after connect [R3-EQ-02/R2-EQ-02]; capture destination line
  [R3-CAP-03]; AF point inspection + run history [R3-FOC-03]; Atlas optics
  read-only-from-profile [R3-ATL-03]; "Show in downloads" [R3-PLAN-01]. All
  Opportunity-class, deferred ledger.
- Standing non-goals: planet ephemerides, PAUSE_REQUESTED, role-preview mode, OIDC UI,
  AF-survives-reload.
- PENDING USER DECISIONS unchanged: abandoned-status affordance, custom roles, OIDC UI
  form, first-run-admin-from-local, operator weather visibility (now sharpened by the
  weather.md contradiction above).
