# UI/UX feedback — user walkthrough 2026-07-15 (post sub-project B smoke)

Source: user review of the live app at :8800. Evidence screenshot for the Focus items:
`C:\Users\bear\Pictures\Screenshots\Screenshot 2026-07-15 222738.png` (V-curve panel).
Owner: future "UI polish wave" (separate from sub-project C/weather). None of these block C.

## Focus screen
- **F1 (feature):** Autofocus needs a binning setting (bin the AF exposures independently of imaging binning).
- **F2 (bug, visual):** "HFR" axis label overlaps the top y-axis tick number ("HFR" renders on top of "2.9" — see screenshot).
- **F3 (bug, correctness):** V-curve trend lines are wrong: the right-side fit line is only partially drawn (disconnected segment at the top right, gap over the mid-right samples), and the intersection/minimum of the two fit lines does not coincide with the reported autofocus point (BEST 19989 marker sits offset from the V apex). Fit rendering and/or fit math needs a root-cause pass — the drawn fit must agree with the solver's chosen minimum.

## Atlas screen
- **A1 (feature):** Optional on-screen annotations explaining what's being displayed (label overlays for objects/FOV/grid — toggleable).
- **A2 (feature):** Solar-system objects missing: planets (and Moon/Sun at minimum) should be findable in the catalog/search.
- **A3 (bug, layout):** Focal length / pixel size / sensor width / sensor height readouts are unstable under window resize — they spread apart or wrap "in a hectic fashion" instead of holding a consistent arrangement.
- **A4 (design):** Those same boxes are oversized for their content; and these are rig properties — they should be assigned on the rig definition (profile), not free-floating editable boxes on Atlas. (Code note: optics already live in `AppConfig.optics` with a per-profile override `Profile.optics` — the Atlas surface duplicates them; likely resolution is rig/profile as the single write surface, Atlas shows a compact read-only chip.)

## Plan screen
- **P1 (feature):** Saved plans need user-chosen names at save time (save/export/import exist; naming is the gap).
- **P2 (layout):** The plan-library module's placement is too cramped to read plan names — needs room or a different placement.
- **P3 (UX):** Export should propose a sensible default location on disk (default-download path UX) rather than the current flow.

## Settings screen
- **S1 (UX, naming):** "Save site" vs "Saved locations" is confusing — one means "apply these values as the current site," the other "store for later reuse." Simplify the mental model (e.g., one primary action "Apply & save to rig", library actions renamed "Store in library / Load from library", or merge into a single flow where saving the site optionally also stores a named library entry). Needs a small design pass, then rename/restructure.

## Notes
- User will provide gemini/claude/gpt agents for recurring UI/UX reviews; the reusable review prompt lives at `docs/superpowers/prompts/ui-ux-review-prompt.md`.

---

# Codex field review triage — 2026-07-16 (reviewed :8802 instance)

Full report: `docs/superpowers/reviews/2026-07-15-codex-ui-field-review.md`. Evidence
screenshots: `review-evidence/` at repo root (kept on disk, git-ignored, referenced by
absolute path from the report). Review quality: high — followed the prompt (personas,
sweeps, per-finding evidence, intuitive leaps, top-10). Verified-truths it recorded:
plan totals correct; site validation messages actionable; resume picks up sessions
correctly; **its V-curve run showed fitted minimum / BEST marker / final position all
agreeing** — so F3's apex≠BEST is likely data-dependent/intermittent, which narrows the
F3 root-cause pass (compare the user's failing run shape vs Codex's passing one).

## Triage verdicts on its top findings

- **REC-01 "Abort ignored" (its #1 Blocker): DOWNGRADED — automation artifact.**
  Abort is a press-and-hold `HoldButton` (MonitorView.tsx:334-338); repeated *clicks*
  are ignored by design. Residual REAL finding (Major): the hold affordance is
  undiscoverable — face just says "Abort"; add a visible "hold" hint (and the same for
  every HoldButton). Verify by hand once to close the loop.
- **CAP-01 negative exposure accepted (Blocker): REAL by inspection likelihood — top
  fix candidate.** `-5s` accepted, captured, and reported in metadata. Validate against
  a positive camera-supported range at the input boundary; keep last valid value.
- **ATL-01/MNT-01 planets absent (Blocker): confirms A2**, elevated — fix at a shared
  catalog/ephemeris provider so Atlas, Mount, and Plan agree (Sun/Moon/planets).
- **REC-02 PAUSED not truthful (Major): REAL.** Progress advanced 1/130→3/130 while
  "PAUSED"; global strip said RUNNING simultaneously. Needs a "Pausing — finishing
  current frame" intermediate state and strip consistency. (Some of this is honest
  physics — an in-flight exposure completes — but the UI must say so.)
- **EQ-01 three conflicting connection truths (Major): REAL** — sim-connected
  toasts/dots vs UNASSIGNED drivers vs "No rig connected yet" + `Connect Rig (0)`.
- **REC-03 live session beside unrelated draft (Major): REAL** — session snapshot
  should own the main context while running; drafts move behind an explicit edit affordance.
- **PLN-02 second-tab last-write-wins on drafts (Major): REAL** — no stale/conflict
  detection on the plan draft (config/site have 409 versioning; drafts have nothing).
- **MON-01 night-mode guide chart is hue-only (Major): REAL and embarrassing** — the
  Monitor guide chart uses two solid color-only lines, violating our own dash/width/
  label night rule (Sparkline complies; GuideGraph predates the rule). Fold with CAP-02
  (night contrast floor; brightness badge said 100% while header sat at 45%).
- **MON-02/REV-01 HFR units inconsistent (Major): REAL** — `HFR 2.10` unitless on
  Monitor vs px/″ split on Capture; timestamps like `223727` unreadable. One canonical
  format everywhere: `HFR 2.10 px (3.07″)`, `2026-07-15 22:37:27`.
- **FOC-01 stale pre-AF preview beside FOCUS—GOOD (Major): REAL** — refresh preview
  with the final AF frame or label it "pre-autofocus frame".
- **FOC-02 unexplained dual fit lines / no legend: fold into F3's root-cause pass.**

## Confirmations of existing items (evidence now attached)
- F1 ⊂ FOC-04 (AF binning; Codex adds filter/gain + tappable curve samples).
- F2 = FOC-03 (HFR label overlap; adds axis units + interior ticks).
- A3/A4 = ATL-03 + ATL-06 + SIT-03 (optics belong on profile; oversized/unstable boxes;
  cap numeric field widths — SIT-03 measured a 1,074 px input for 9 characters).
- P2 = PLN-01 (cramped names + 37 px buttons + clipped actions at 900 px).
- S1 = SIT-01 (verb rename proposal: "Apply active site" / "Save as location preset…" /
  "Load selected preset") + SIT-02 (matching-preset selection resets in new tab) +
  SIT-04 (provenance line: source + timestamp on the active site).
- A1 adjacent ATL-04 (visibility chart unlabeled; summary concatenates `21:4548°` —
  that concatenation is its own small bug).

## Smaller new items (polish-wave fodder)
ATL-02 search feedback (Enter-only, no empty state); ATL-05 suggested-mosaic CTA
(object 6.4× frame → offer grid); EQ-02/CAP-04/MON-03 narrow+wide layout balance;
MON-04 meridian countdown format (`in 6 h 11 m` + clock time); PLN-03 step offset
visibility (exports contain offset 30 the UI never shows); PLN-05 toggle touch targets
+ ON/OFF words; CAP-03 "Center" verb ambiguity; CAP-05 capture destination visibility;
EQ-03 first-light checklist; REV-02 recovery summary (why stopped, what Resume does);
AUT-01 preview-as-viewer role matrix.
