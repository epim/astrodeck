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
