# AstroDeck Surface Spec — "Onboarding, Guidance & Pre-flight"

**Date:** 2026-06-15
**Surface owner:** lead designer
**Status:** build-ready
**Panel review:** `docs/reviews/2026-06-15-ux-panel-review.md` (P0 lines 46–48, 109–112, 114–119, 121–126)
**Supersedes draft** "Onboarding, Guidance & Pre-flight" after three adversarial UX critiques.

---

## 0. Overview

This surface delivers the **onboarding / readiness / pre-flight P0 cluster**: a readiness checklist, a not-connected interstitial, contextual help for cryptic fields, a below-horizon guard on GOTO/Run, the inverted DAY/NIGHT label fix, visible keyboard focus, and a reusable hold-to-confirm wrapper for **non-urgent** destructive actions. Everything is grounded in the existing conventions (`Panel`/`Field`/`Stat`/`Led`/`Toggle`, the `status` WS event, `api`, theme tokens, `hub.poll_status()`/`altaz()`/`_spawn()`).

### What changed from the draft (resolutions to the critiques)

Three critiques converged on the same load-bearing facts. **All accepted unless noted.**

| # | Valid critique (accepted) | Resolution in this spec |
|---|---|---|
| A1 / general | Horizon math uses the **hardcoded SF site** (`hub.py:38`). Confident green/red verdicts off the wrong lat/lon are actively misleading. | **Hard dependency on Settings/site (build-order #2).** The horizon row is **disabled** ("Set your location to check altitude", Fix → Settings) whenever `site.is_default === true`. No altitude verdict is rendered from default coordinates. `/api/site` already exists; we add a `default` flag. (§4a, §6) |
| A2 / collision | `alt < 0` floor was sold as **pier-strike protection**; that conflates horizon with pier/tripod collision. | Copy is corrected to "below the **visible horizon** (atmosphere/obstructions)". **No collision claim.** Real pier/horizon-limit enforcement is explicitly deferred to the safety-monitor P0 (review line 104). (§5, §7-copy) |
| B3 / data shape | Draft read `status.mount.tracking` and `status.connected.mount`; the real shape is `connected[role].connected` for presence and **top-level** `status.mount`/`camera`/`guider` for live values, and `status.mount` is **omitted** when the telescope is connected but `get_position()` fails. | `buildPreflight` rewritten against the verified shape (§3). New explicit `not-reporting` state distinct from `parked`. |
| B2 / guiding-start | Guiding starts **after** slew inside the engine (`engine.py:200`). Blocking pre-flight on `guiding === true` blocks every guided run. | Guiding check: guider connected → `ok` "will start after slew"; `plan.guide` and **no guider** → `warn` (not blocked) "frames will be unguided". **No RMS threshold at pre-flight** (no star locked yet). (§3 table) |
| B3 / cooling-block, engine | Engine cools **at start** (`engine.py:146`) with a **±1.0 °C** acceptance band (`engine.py:279`). Blocking start on temperature blocks the process that does the cooling, and the ±3 band was invented. | Cooling is **never `blocked`** while a cooler exists. `plan.cool_to` set + camera can't cool → `blocked`. Otherwise `info`/`warn` with live Δ + ETA. Tolerance text uses the engine's **±1.0 °C** constant. Row offers in-place **"Start cooling now"**. (§3 table) |
| B/critique2 #9 | Parked is **not** a blocker — engine unparks inside `goto_and_center` (`hub.py:315`). | Tracking/parked check: telescope connected → `ok` ("parked — will unpark on start" is `ok`, not blocked). Blocked **only** if no telescope and a non-calibration target needs slewing. (§3 table) |
| B4 / modal-gate-all-clear | A mandatory modal (and an 800ms hold) on **every** Run trains users to bash through. | **Inline readiness strip** (the `dense` Checklist) on the Sequence view colours the Run button. The modal **only opens when there is a `blocked` item**. All-clear and warn-only runs proceed with a **single tap** (warnings listed inline above the button). (§2) |
| B5 / hold-past-warning | Hold-to-run-past-a-warning is a trap; proceeding past a soft warning is constructive, not destructive. | **Removed.** Warnings are shown inline; Run is a normal tap. Hold is reserved strictly for destructive/irreversible actions. (§1c, §2) |
| F19 / critique2 #7 | **STOP / emergency-stop must be instant.** Hold-to-stop makes the pier-strike case worse. | `ConfirmHold` is **never** applied to STOP (slew-pad or in-slew) or the slew abort. STOP stays a single instant tap and is enlarged to ≥44px. Hold applies only to **Disconnect All, Park, Abort-sequence**. (§1c, §7) |
| B/critique2 #4,#6 | Horizon check used stale `targets[0]`; first target may be calibration; a snapshot teaches nothing about "runnable tonight". | Check the **first non-calibration target** plus flag **any** light target currently below horizon. Surface **transit / "sets in Xh"** using `altaz(..., unix_time=…)` (available in `coords.py`). GOTO **re-queries live altitude at the tap**, never trusts the stale catalog row. (§3, §4a, §5) |
| critique2 #5 | Don't add the `alt<0` guard inside `goto_and_center` unconditionally — it's shared by `meridian_flip` (`hub.py:345`); a low southern transit would abort a flip mid-run. | Guard lives **only** in user-initiated GOTO and sequence-start paths. `meridian_flip`'s internal call passes `force=True`. `alt<0` floor is gated on `not site.is_default`. (§4b) |
| critique2 #10 / D14,D15 | Tooltip copy asserts rig-specific numbers (offset 30–50, HFR 1.5–3px, EAF 250–500) and an OSC-wrong binning tip; "offset doesn't change brightness" is literally wrong. | Copy rewritten: keep the **concept**, drop/caveat absolute numbers, fix the offset claim, fix the OSC binning caveat. (§1a HELP registry) |
| D12 / critique3 #4 | A `?` dot on ~11 dense Automation rows creates colliding 44px hit areas. | **One "What do these mean?" affordance per panel** that opens a single reference sheet; plus inline one-line helper text under only the genuinely cryptic fields (offset, HFR, dither). No per-row dot in dense panels. The HelpDot trigger is the **whole `.label`**, not an inline glyph, where used. (§1a, §1b) |
| D13 / critique3 (touch) | Tap-toggle tooltips that "dismiss on scroll" vanish the instant a user drags to read them. | On touch, help is a **dismissable sheet** (tap-outside / X / Escape), **never auto-dismiss on scroll**. (§1a) |
| E16 / critique (nav) | "Tappable but locked" is a third anti-pattern; the lock dot collides with the existing connected-`Led` in the same corner; both are dim-red and indistinguishable at night. | **One model:** gated tabs are **fully disabled** (`aria-disabled`, no navigation) with a small **lock icon** that replaces (not overlaps) the connected-`Led`. (§3b) |
| E17 / critique2 (WS drop) | Gating on `status.mode === "none"` strands users on a WS drop mid-run. | Gate on a **separate equipment-connected flag** persisted across WS reconnects (`everConnected` + last-known mode), distinct from `wsConnected`. A WS drop shows a "reconnecting" banner (separate P0), **never** the onboarding interstitial. (§3b, §8) |
| E18 / critique2 #14 | "Connect Simulator Rig" on every interstitial is a field footgun. | Sim rig stays **only on the Rig page**, clearly labelled "Demo / Simulator". Interstitials route to Rig, not to sim. (§3a) |
| F20 / critique3 #3 | Hold cancels on jittery `pointerleave` under gloves/cold; red-on-red fill is invisible in night mode; reduced-motion "jumps" kill feedback. | Hold is **tolerant of micro-movements** (cancel only on `pointercancel` or leaving a padded bounds), progress shown via **luminance + geometry + `aria-live` text countdown** (not hue), and uses a smooth `transition: width` under reduced-motion. (§1c) |
| F21 / critique3 (keyboard) | Pointer-hold and keyboard two-step are two mental models for one button. | **Single confirm model for all inputs:** two-step **arm → confirm** (3s arm window) is the canonical interaction; pointer-hold is an *equivalent* progressive arm of the same state, not a separate idiom. Keyboard and touch both arm-then-confirm. (§1c) |
| C10/C11 / critique3 #8 | `NIGHT ON/OFF` re-encodes state into a verb-shaped label; glyph-only diff fails low-vision; inconsistent with `Toggle`. | Use the existing **`Toggle` component** with a fixed caption "Night vision" — switch position **is** the state, no verb. ≥44px. Same for LOG (kept as button, bumped to ≥44px). (§6) |
| critique3 #1 / #13, critique2 #13 | The three night "status inks" are **not lightness-separable** and `--warn-ink` is **orange** (hue cue + hurts dark adaptation). | **Drop the 3-red-ink scheme.** Shape glyph **+ a redundant text token** ("READY/WARN/BLOCK"/"NOT NEEDED"/"CHECKING") is the **sufficient** cue. Tints stay in-hue red and are **tertiary**. Build test asserts the text token is always present. (§9, §10, §11) |
| critique3 #2 | New controls inherit `outline:none`; ARIA draws nothing; modals need focus trap/Escape/restore. | This surface ships a global `:focus-visible` ring (day + night) and the modal/sheet ships **focus trap + initial focus + Escape + focus return**. (§1e, §9-css, §11) |
| critique3 #5,#6,#9 | Help body 11px + `--text-dim` "?" fail legibility/AA; checklist `detail` truncates the very numbers it's proud of. | Help body **≥12px**; trigger uses `--ink`/`--accent`, never `--text-dim`. `detail` reuses **`Stat` semantics** (value in `--ink`, unit/target in `--dim`), **≥12px, no truncation of the primary value** (wraps to line 2 on narrow). (§1a, §1b) |
| critique3 #7 | GOTO alt cell is colour-only; carry the shape cue through surfaces this diff touches. | MountView alt cell gets a **severity glyph** (`↓` low / `⚠ below`) next to the number. (§5) |
| critique3 #11 | `.blink` is overloaded (nav LED, running label, pending, firing) and is suppressed under reduced-motion → pending has no cue. | `pending`/`checking` uses a **static** `◌` + the text token "Checking…", never relying on `.blink`. (§1b, §9) |
| critique3 #12 | A hard `alt<0` block dialog with a hold-to-"I understand" teaches hold==acknowledge. | Hard block uses a **plain single OK** dialog (no hold). Hold is only for dialogs that **fire** an action. (§1e, §5) |
| critique2 #11 | Missing the checks that actually stop runs: **filter-name validation** and **disk space**. | Added: `filters` check (every `step.filter ∈ filterwheel.names`) — cheap, deterministic, **blocked** on mismatch. `disk` check — one new backend field, **warn/blocked**. (§3, §4c) |
| critique2 #12, H24 | Resume must show **frames remaining**, not re-derive; routing Resume through a blocking gate at 3am is hostile. | Resume runs the **same inline readiness strip** (uses `frames_remaining` from `/api/sequence/recoverable`) and **warns, never blocks**; horizon "set since crash" is a warn. (§2c) |
| H26 | Pre-flight without the error-surfacing P0 is a trust trap (green check, then invisible failure). | Explicit **dependency note**: this surface ships **after** the reliability/error-surfacing P0 (build-order #3). Cross-referenced, not re-specified here. (§12) |
| H25 | `HORIZON_MIN_DEG=15` invented and hardcoded twice. | Horizon minimum comes from **`site.horizon_min_deg`** (server-owned, set in Settings, default 15) — single source, one place. (§4a, Shared contracts) |

**Rejected / deferred (with reason):**

- **critique2 #11 "plate-solve readiness" and "dew/weather" checks** — *deferred, not rejected.* Solver-availability needs a backend capability probe that belongs with the plate-solve-config P0 (build-order #2); weather belongs with the safety-monitor P0 (#4). The `Checklist` component is built to accept these as additional `CheckItem`s with zero structural change, so they slot in later. Out of scope **here** to avoid blocking on those surfaces.
- **F8 "shorten Abort to 500ms"** — *partially accepted.* Abort-sequence is destructive-but-not-instant-critical; we use **600ms** (down from 800) with an unmistakable arm state. We do **not** make it instant (it stops an unattended multi-hour run; an accidental tap is costly). STOP/motion-stop is the instant one.

---

## 1. New reusable primitives

> **Ownership note for parallel implementers:** every primitive below is a **new file** with no shared mutable surface except `store.ts` (one additive slice, §9) and `index.css` (one additive block, §9-css). Implementers must touch **only the files listed under their item** plus the two shared files via the **named, disjoint anchors** in §9 / §13.

### 1a. `Tooltip` + `HelpSheet` + `HelpDot` — `ui/src/components/Tooltip.tsx` (new) · `ui/src/help.ts` (new)

Two affordances backed by one content registry:

```tsx
// trigger that opens a popover (desktop) / bottom sheet (touch)
export function HelpDot(props: { k?: HelpKey; content?: ReactNode; label?: string }): JSX.Element;
// a single "What do these mean?" link that opens a multi-row reference sheet
export function HelpSheet(props: { title: string; keys: HelpKey[] }): JSX.Element;
```

Behavior / states (resolves D12, D13, C-help, critique3 #4,#5):
- **Trigger sizing:** enlarged hit area gated on `(any-pointer: coarse)` (covers touch laptops), not `pointer: coarse`. In dense panels (`Automation`, step grid) we use **`HelpSheet`** (one trigger per panel), never per-row `HelpDot`s, so 44px hit areas never collide.
- **"?" glyph** renders in `--accent` (reads as interactive) with a `border-line2` ring — **never `--text-dim`**.
- **Body** is `.panel`, `text-[12px] leading-relaxed text-ink`, `max-w-[280px]`, `role="tooltip"`/`role="dialog"` (sheet), `z-40`.
- **Open:** hover (pointer:fine, 150ms) OR tap (coarse). **Dismiss:** outside pointerdown, Escape, explicit X (sheet), route change. **Never on scroll.**
- **Touch:** opens as a centered/bottom sheet, `max-w-[calc(100vw-24px)]`, stays until X/outside/Escape.
- Positioning: `useLayoutEffect` measure + flip-on-overflow + viewport clamp. No external dep.
- Reduced motion: skip the fade.
- Focus: trigger is a real `<button>`, gets the global `:focus-visible` ring (§9-css); sheet traps focus and returns it on close.

**Content registry — `ui/src/help.ts`** (copy corrected per critique2 #10 / D14 / D15):

```ts
export const HELP = {
  offset:
    "Camera offset is a fixed pedestal added to every pixel so read noise never clips at zero (loses shadow detail). Use your camera's recommended value — too low clips the darkest pixels, too high wastes dynamic range. It shifts the whole image up by a constant; it does not add real signal to your subject.",
  hfr:
    "Half-Flux Radius — the radius containing half a star's light. Lower = sharper focus. What counts as 'good' depends on your pixel scale (focal length + pixel size + seeing), so watch the MINIMUM HFR your own rig reaches and refocus when it climbs ~20% above that, rather than chasing a fixed number.",
  stepSize:
    "Focuser step size: motor steps moved between autofocus samples. Larger = wider, coarser search; too large overshoots the V-curve, too small wastes time. The right value depends on your focuser and scope — start from your EAF/scope's recommended step and adjust.",
  dither:
    "Dither nudges the mount a few pixels between frames so hot pixels and noise land in different places and average out when stacking. 'Every N frames' sets how often; pixels sets the nudge size (a few pixels is typical).",
  hfrReject:
    "Flag soft frames: if a frame's HFR exceeds this multiple of the running median, it's flagged (e.g. 1.5x = 50% softer than typical). 0 = off. Catches wind gusts, passing cloud, and focus drift.",
  filterOffset:
    "Per-filter focus offset: filters can focus at slightly different points. When set, the focuser shifts automatically on filter change so you don't refocus every time.",
  coolTo:
    "Cool the sensor to this temperature before lights and hold it. Colder means less thermal noise and lets you reuse a dark library. Blank = no cooling (uncooled camera). The sequence cools and waits at the start — you don't need to pre-cool before pressing Run.",
  meridianFlip:
    "German equatorial mounts reach the pier as a target crosses the meridian (due south). A flip swaps the scope to the other side and re-centers by plate-solving. Turn off for fork or alt-az mounts (which don't flip).",
  binning:
    "Binning combines NxN pixels into one: brighter, lower-resolution, less data. On mono cameras, 1x for final lights and 2x for fast framing/focus. On one-shot-colour (OSC) cameras, hardware binning destroys the Bayer pattern, so '2x' is usually done in software and changes colour handling — prefer 1x for OSC lights.",
} as const;
export type HelpKey = keyof typeof HELP;
```

### 1b. `Checklist` + `ChecklistItem` — `ui/src/components/Checklist.tsx` (new)

Pure presentational; takes already-computed items (reusable by the inline strip, the modal, and later the safety monitor).

```ts
export type CheckStatus = "ok" | "warn" | "blocked" | "checking" | "skipped" | "disabled";

export interface CheckItem {
  id: string;                 // "camera" | "mount" | "guiding" | "cooling" | "horizon" | "focuser" | "filters" | "disk"
  label: string;
  status: CheckStatus;
  word: string;               // REDUNDANT TEXT TOKEN, always set: "READY"|"WARN"|"FIX"|"CHECKING"|"NOT NEEDED"|"SET LOCATION"
  detail?: { value: string; unit?: string };  // reuses Stat semantics (value in ink, unit in dim)
  help?: HelpKey;
  fix?: { label: string; view?: ViewName; onClick?: () => void | Promise<void>; inPlace?: boolean };
}
```

```tsx
export function Checklist(props: { items: CheckItem[]; dense?: boolean }): JSX.Element;
```

Row anatomy (min-height 44px; `dense` → 40px strip variant) — resolves critique3 #1,#6,#7,#11,#27:
- **Status glyph**, shape + weight differentiated, 18px, **never colour-only**:
  - `ok` → filled `✓`
  - `warn` → outlined `△`
  - `blocked` → filled `✕` on a hollow ring
  - `checking` → **static** `◌` (no `.blink`)
  - `skipped` → `–`
  - `disabled` → `⊘`
- **Word token** (`mono text-[11px]`, always present) immediately after the glyph — the **sufficient** non-colour cue.
- **Label** (`text-sm text-ink`).
- **Detail** — uses `Stat` rendering: value `≥12px text-ink`, unit/target `text-dim`. **Primary value never truncated**; wraps to line 2 < 360px.
- **Fix** — only on `warn`/`blocked`/`disabled` with `fix` present. `inPlace: true` → a button that calls `fix.onClick()` **without leaving** ("Unpark", "Start cooling now", "Start guiding"). Else a deep-link that does **not** close the modal where the modal stays open (it stays open; gear is single-rig — resolves draft-#7). Min 44px, `text-[12px]` (resolves P2 #9).
- Empty: 3 `checking` skeleton rows.

### 1c. `ConfirmHold` — `ui/src/components/ConfirmHold.tsx` (new)

Arm-to-confirm wrapper for **non-urgent destructive** actions only (Disconnect All, Park, Abort-sequence). **Never STOP/motion-stop.** Resolves F19, F20, F21, critique3 #3.

```tsx
export function ConfirmHold(props: {
  onConfirm: () => void | Promise<void>;
  children: ReactNode;                 // label, e.g. "Disconnect All"
  armMs?: number;                      // default 600
  className?: string; disabled?: boolean;
  confirmLabel?: ReactNode;            // shown while armed, e.g. "Release to abort"
  haptic?: boolean;                    // navigator.vibrate on fire (default true)
}): JSX.Element;
```

**One confirm model for all inputs (F21):** the button has states `idle → arming → armed → firing`.
- **Pointer:** `pointerdown` starts arming (progress sweeps 0→100% over `armMs`); reaching 100% → `armed` and **fires on the same hold** (lift before 100% cancels). Cancels only on `pointercancel` or leaving a **padded** bounds (tolerant of glove/condensation jitter — F20).
- **Keyboard / assistive:** first `Enter`/`Space` → `armed` for 3s (label "Press again to confirm"); second press fires. Same `armed` state, same `aria` semantics.
- **Feedback (critique3 #3):** progress is **luminance + geometry**, not hue — a bright (`--ink` at reduced alpha) sweeping fill **and** a 3-segment tick row that fills; plus an `aria-live="assertive"` text countdown ("Hold… release to confirm"). Legible red-on-red in night mode because the fill is light-on-dark, not red-on-red.
- **Reduced motion:** smooth `transition: width` (a width transition is not vestibular motion), not discrete jumps — usable for short arms.
- **In-flight:** while `onConfirm` resolves, disabled + static "…" (no `.blink`). Re-entrancy guard.
- `touch-action: none` is scoped to the **button box only** (critique3 #10), so the surrounding sheet still scrolls.
- Visual base is `.btn .btn-danger`; gets the global `:focus-visible` ring.

### 1d. Horizon helper — `ui/src/lib/horizon.ts` (new)

```ts
export type HorizonVerdict = "ok" | "low" | "below" | "unknown";
// unknown when site is default OR altitude could not be fetched
export function horizonVerdict(alt: number | null | undefined, horizonMin: number, siteIsDefault: boolean): HorizonVerdict;
```

Pure; no `HORIZON_MIN_DEG` constant in TS (resolves H25) — `horizonMin` comes from `site.horizon_min_deg`.

### 1e. `ConfirmDialog` — `ui/src/components/ConfirmDialog.tsx` (new)

Promise-based modal for one-off confirms, mounted once via `<ConfirmHost/>` in `App.tsx`. Resolves critique3 #2 (focus), #12 (no hold on non-firing dialogs).

```ts
export function confirmDialog(opts: {
  title: string; body: ReactNode;
  confirmLabel?: string; cancelLabel?: string;
  tone?: "warn" | "danger";
  mode?: "ok" | "confirm" | "hold";   // "ok" = single dismiss (no proceed); "confirm" = OK/Cancel; "hold" = ConfirmHold proceed
}): Promise<boolean>;
```

- **Focus trap + initial focus on the safest button + Escape cancels + focus return** to the opener. Scrim `bg-black/70`.
- `mode: "hold"` only when the dialog **fires** an action (low slew). A hard block (`below`) uses `mode: "ok"` — single dismiss, returns `false`.

---

## 2. The Pre-flight surface (inline-first; modal only when blocked)

Resolves B4 (don't modal-gate the all-clear) and B5 (no hold past warning).

### 2a. `PreflightStrip` — `ui/src/components/PreflightStrip.tsx` (new)

A `dense` Checklist rendered **inline** in `SequenceView`, directly above the Run button. Always visible when a plan has frames.

- Derives items from `buildPreflight(...)` (§3), re-derived on each `status` tick via a **memoized narrow selector** (no broad `useStore()`).
- Verdict drives the Run button:
  - **all `ok`/`skipped`** → Run is `btn btn-accent`, single tap.
  - **any `warn`/`disabled`** (no blockers) → Run is `btn btn-accent` (single tap); warnings listed inline above it ("Will run unguided", "Altitude unverified — set your location"). Plain language, no jargon (resolves B8).
  - **any `blocked`** → Run is `btn-danger`-outlined and **disabled**; clicking it (or a "Review" link) opens `PreflightModal`. Copy: "2 things need fixing before you can run" (resolves B8).

### 2b. `PreflightModal` — `ui/src/components/PreflightModal.tsx` (new)

Opens **only** when there is a `blocked` item (or from the strip's "Review"). It is the explainer + fix surface, not a mandatory gate.

- Layout: `.panel`, `max-w-[560px]`, centered; **full-height bottom sheet < 640px** (`fixed inset-x-0 bottom-0 max-h-[88vh] rounded-t`).
- **Blocked rows are pinned to the top** and visually separated (resolves H28, P2 #28); the sticky footer also names the first blocker ("Mount: no telescope ▴").
- Header: "Pre-flight — {plan.name}" + verdict pill carrying the **word** ("BLOCKED" / "2 TO FIX"), not colour-only.
- Body: `<Checklist items={preflightItems} />` with **in-place Fix** buttons (Unpark / Start cooling now / etc.) — modal **stays open**, rows flip live as the user fixes things.
- Footer: primary "▸ Run Sequence" enabled only when no blockers remain (then it's a single tap — proceeding past remaining warnings is **not** a hold); secondary "Cancel".
- **Live re-check is debounced 750ms and layout is frozen** (Fix-link space reserved; verdict pill not flipped more than necessary) so the list doesn't churn while read (resolves critique #22).
- Focus trap / Escape / restore via the modal shell (shared with ConfirmHost pattern).
- `onProceed` → `api.post("/api/sequence/start", { ...plan, force })` where `force` is set only if the user accepted a `low` (0–15°) horizon warning earlier.

### 2c. Resume

`SequenceView`'s existing "Resume" routes through the **same `PreflightStrip`** (not a blocking modal). It uses `frames_remaining`/`frames_done` from `/api/sequence/recoverable` for counts (resolves critique2 #12) and **warns, never blocks** — a target that set since the crash is a `warn` with a Fix deep-link, not a wall (resolves H24).

---

## 3. Readiness derivation — `ui/src/lib/preflight.ts` (new)

Single source of truth; pure + unit-testable.

```ts
export function buildPreflight(
  status: RigStatus | null,
  plan: SequencePlan,
  site: SiteInfo,                              // { latitude, longitude, is_default, horizon_min_deg }
  altById: Record<string, PreflightAlt | undefined>,  // from /api/sequence/preflight, keyed by target name
  actions: PreflightActions,                   // in-place fix callbacks (startCooling, unpark, …)
): CheckItem[];
```

Checks (reconciled to the **verified status shape** — `connected[role].connected` for presence; top-level `status.mount`/`camera`/`guider` for values; `status.mount` absent ⇒ not-reporting):

| id | Source signal (verified path) | ok | warn | blocked | skipped / disabled |
|---|---|---|---|---|---|
| `camera` | `status.connected.camera?.connected` | connected | — | **blocked** if false ("No camera — connect first", Fix → Rig) | — |
| `mount` | `status.connected.telescope?.connected`, then top-level `status.mount` | connected & reporting; **parked is `ok`** ("will unpark on start") | telescope connected but `status.mount === undefined` → `warn` "not reporting yet" | **blocked** if no telescope AND any non-calibration target (Fix → Rig) | **skipped** if every target `calibration` |
| `guiding` | `status.guider` presence (only emitted when guider connected) | guider connected → "will start after slew" | `plan.guide && !status.guider` → **warn** "no guider — frames will be unguided" | — (never blocked) | **skipped** if `!plan.guide` |
| `cooling` | top-level `status.camera.temperature` vs `plan.cool_to`; `status.camera.can_cool` | `cool_to == null` → skipped; else Δ within ±1.0 °C → ok | cooler reachable but Δ > 1.0 °C → **warn** "cooling: −4.2 °C → −10 °C, ~3m" + in-place "Start cooling now" | **blocked** only if `plan.cool_to != null && !status.camera.can_cool` ("plan wants cooling; camera can't cool") | **skipped** if `cool_to == null` |
| `horizon` | `/api/sequence/preflight` for first **non-calibration** target + scan all light targets | every light target ≥ `horizon_min_deg` | any light target 0–`min`° → **warn** "M81 low (12°)"; OR transit info ("sets in 1h10m") | **blocked** only if a light target `alt < 0` AND `!site.is_default` ("below the visible horizon now") | **disabled** ("Set your location to check altitude", Fix → Settings) if `site.is_default`; **skipped** if no light targets |
| `focuser` | `status.connected.focuser?.connected` | connected | any target `autofocus_first` but no focuser → **warn** "AF requested, no focuser — will skip" | — | **skipped** if no focuser & no AF requested |
| `filters` | every `step.filter` ∈ `status.filterwheel?.names` | all present (or no filters used) | wheel not connected but steps name filters → **warn** "no filter wheel — filter steps ignored" | **blocked** if wheel connected but a `step.filter` is missing ("plan uses 'Ha'; wheel has L,R,G,B", Fix → highlight step) | **skipped** if no step names a filter |
| `disk` | `status.disk.free_gb` / `status.disk.low` (§4c) | `free_gb` above warn threshold | `low` (warn threshold) → **warn** "Low disk: 8 GB free" | **blocked** if `free_gb` below hard floor (e.g. < 1 GB) | **skipped** if backend omits `disk` |

- **Verdict:** `blocked` if any `blocked`; else `warn` if any `warn`/`disabled`; else `ok`.
- Every item sets `word` (the redundant text token). `detail` uses `Stat` value/unit split.
- Live updates from the 2s `status` tick + the one-shot `/api/sequence/preflight` (refetched every 30s while a strip/modal is mounted, and on demand). Horizon `unknown`/fetch-fail → `warn` "altitude unverified" (server guard §4b is the real net), **except** on the Run path a `below` from a *successful* fetch blocks.

---

## 4. Backend additions

### 4a. `GET /api/sequence/preflight?ra_hours=&dec_deg=` — `server/astrodeck/api/app.py`

Authoritative **live** alt/az + horizon verdict for a coordinate. Keeps the math server-side (single source) and uses the **real site** once Settings lands.

```python
@app.get("/api/sequence/preflight")
async def sequence_preflight(ra_hours: float, dec_deg: float):
    from ..catalog import altaz
    s = hub.site
    if s.get("is_default"):
        return {"alt": None, "az": None, "verdict": "unknown",
                "horizon_min_deg": s["horizon_min_deg"], "site_is_default": True}
    now = time.time()
    alt, az = altaz(ra_hours, dec_deg, s["latitude"], s["longitude"], now)
    hmin = s["horizon_min_deg"]
    # transit / sets-in: sample altaz over the next hours (coords.altaz takes unix_time)
    sets_in_min = _hours_until_below(ra_hours, dec_deg, s, hmin, now)
    return {"alt": round(alt, 1), "az": round(az, 1),
            "horizon_min_deg": hmin, "site_is_default": False,
            "verdict": "below" if alt < 0 else "low" if alt < hmin else "ok",
            "sets_in_min": sets_in_min}
```

`hub.site` gains `is_default: bool` and `horizon_min_deg: float` (default 15). `/api/site` (already present, `app.py:192`) clears `is_default` and accepts `horizon_min_deg`. **`HORIZON_MIN_DEG` is not a TS or duplicated Python constant** — it lives in `hub.site` (resolves H25, A1).

### 4b. Server-side horizon guard (defense in depth) — `hub.py`

```python
def _check_horizon(self, ra_hours, dec_deg, *, force=False):
    if self.site.get("is_default"):
        return                      # never block on default coordinates
    alt, _ = altaz(ra_hours, dec_deg, self.site["latitude"], self.site["longitude"])
    if alt < 0 and not force:
        raise DeviceError(f"target is below the visible horizon (alt {alt:.0f} deg)")
```

- Called in **user-initiated** paths only: `goto` / `plain_goto` (app.py) and `sequence_start`.
- **Not** added unconditionally inside `goto_and_center` (shared by `meridian_flip`). `meridian_flip` → `goto_and_center` is unaffected; if a future guard is added there it passes `force=True` (resolves critique2 #5).
- `GotoBody` gains `force: bool = False`; the start path accepts `force` (threaded so a user who accepted a `low` slew isn't re-blocked). Copy says "visible horizon", **no collision claim** (resolves A2).

### 4c. Disk space field — `hub.py poll_status()`

Add a top-level `disk` block to `poll_status()` output (one `shutil.disk_usage(CAPTURE_DIR)` call):

```python
import shutil
try:
    du = shutil.disk_usage(CAPTURE_DIR)
    free_gb = du.free / 1e9
    out["disk"] = {"free_gb": round(free_gb, 1),
                   "low": free_gb < 10, "critical": free_gb < 1}
except OSError:
    pass
```

No new persisted model fields on `SequencePlan`. New: `/api/sequence/preflight` (read-only), `force` flag on goto/start, `site.is_default`/`site.horizon_min_deg`, and the `disk` status block.

---

## 5. Below-horizon warning on GOTO — `MountView.tsx`

GOTO **re-queries live altitude at the tap** (never trusts the stale catalog row — resolves critique2 #6) and adds a **severity glyph** to the alt cell (resolves critique3 #7).

```tsx
const doGoto = async (r: CatalogEntry) => {
  const pf = await api.get<PreflightAlt>(
    `/api/sequence/preflight?ra_hours=${r.ra_hours}&dec_deg=${r.dec_deg}`);
  if (pf.verdict === "unknown") {            // default site or fetch issue
    const ok = await confirmDialog({ title: "Location not set",
      body: "Altitude can't be checked until you set your location in Settings. Slew anyway?",
      tone: "warn", mode: "confirm", confirmLabel: "Slew anyway" });
    if (!ok) return;
  } else if (pf.verdict === "below") {       // alt < 0, real site
    await confirmDialog({ title: "Below the visible horizon",
      body: `${r.id} is at ${pf.alt}° — below the horizon, so it isn't visible now.`,
      tone: "danger", mode: "ok" });          // single dismiss, no slew, NO hold
    return;
  } else if (pf.verdict === "low") {         // 0–min°
    const ok = await confirmDialog({ title: "Low on the horizon",
      body: `${r.id} is only ${pf.alt}° up — expect heavy atmosphere and possible obstructions. Slew anyway?`,
      tone: "warn", mode: "confirm", confirmLabel: "Slew anyway" });
    if (!ok) return;
  }
  await act(() => api.post("/api/mount/goto",
    { ra_hours: r.ra_hours, dec_deg: r.dec_deg, center, force: pf.verdict === "low" }));
};
```

Alt cell adds a glyph next to the number: `r.alt < 0` → `⚠` (with `text-bad`), `0 ≤ alt < 20` → `↓` (`text-warn`), so the cue is **shape, not colour-only**. The header strip / Pointing panel keep live `alt` with `tone="warn"` < 20° (already present). The same `verdict` rule is reused by `buildPreflight`'s `horizon` check, so GOTO and Run share one rule.

---

## 6. DAY/NIGHT label fix — `App.tsx`

Replace the verb-shaped text button (`App.tsx:82–88`) with the existing **`Toggle`** component + a fixed caption — switch position **is** the state, no verb (resolves C10, C11, critique3 #8):

```tsx
<label className="flex items-center gap-2 h-11 px-2">
  <Toggle checked={night} onChange={toggleNight} />
  <span className="label">Night vision</span>
</label>
```

- `Toggle` already exposes its state visually; we add `aria-label="Night vision"` and `role="switch"`/`aria-checked` inside `Toggle` (a small additive change to `ui.tsx` — see ownership note). Height ≥44px.
- The **LOG** button beside it is bumped to ≥44px (`h-11`) too (resolves critique3 #8 second half; review line 116).

---

## 7. Not-connected interstitial + nav gating

### 7a. `NotConnectedInterstitial` — `ui/src/components/NotConnectedInterstitial.tsx` (new)

Rendered by equipment-dependent views when **not connected** (see §3b for the gating signal, which is **not** `wsConnected`).

- Ghost glyph (view's nav icon, 40px, `opacity-30`) — also addresses "empty states are plain uppercase text" (review line 145).
- Headline "Connect equipment first" + per-view one-liner.
- Primary `btn btn-accent` "◈ Go to Rig" → `setView("connect")`. **No "Connect Simulator Rig" here** (resolves E18) — sim lives only on the Rig page, labelled "Demo / Simulator".
- Exceptions: **Rig** never gated; **Plan** partially gated — the **builder stays usable offline** (legitimate daytime flow), only **Run** is disabled with an inline "connect equipment to run" note.

### 7b. Gating in `App.tsx`

Single render-path change, **fully disabled** gated tabs (one model — resolves E16):

```tsx
const equipConnected = useStore(s => s.equipConnected);   // §9 slice, persists across WS reconnects
const GATED: Record<ViewName, boolean> = {
  connect: false, sequence: false /* partial */, capture: true, focus: true,
  mount: true, polar: true, guide: true, power: true,
};
// in main:
{!equipConnected && GATED[view] ? <NotConnectedInterstitial view={view} /> : <Active />}
```

Nav (left rail + bottom): gated tabs while `!equipConnected` render a **lock icon that replaces** the connected-`Led` slot (not overlapping it — resolves E16/collision), `aria-disabled`, and `onClick` is a no-op (truly disabled). When connected, the existing connected-`Led` returns.

---

## 8. Data flow

```
WS "status" (2s) ──► store.status ──► narrow selector (memoized)
                                         │
SiteInfo (store.site, from /api/summary + /api/site) ─┤
plan (SequenceView local + localStorage) ────────────┤
/api/sequence/preflight (per light target, 30s) ─────┤
                                         ▼
                               buildPreflight() ─► CheckItem[]
                                         ▼
                 PreflightStrip (inline, always) ── verdict ──► Run button state
                                         │ (blocked only)
                                         ▼
                                  PreflightModal (explain + in-place Fix)
                                         ▼
                  onProceed → POST /api/sequence/start { ...plan, force }

equipConnected (store): set true on first non-"none" status mode; stays true across
wsConnected=false (WS drop) until an explicit /api/disconnect or a status with mode==="none".
A WS drop sets wsConnected=false only → "reconnecting" banner (separate P0), NOT the interstitial.
```

---

## 9. Shared contracts (types · store · REST · backend) — read this for parallel work

### `ui/src/types.ts` (additive only)

```ts
export interface SiteInfo { latitude: number; longitude: number; is_default: boolean; horizon_min_deg: number; }
export interface PreflightAlt {
  alt: number | null; az: number | null;
  verdict: "ok" | "low" | "below" | "unknown";
  horizon_min_deg: number; site_is_default: boolean; sets_in_min?: number | null;
}
export type CheckStatus = "ok" | "warn" | "blocked" | "checking" | "skipped" | "disabled";
export interface CheckItem { /* as §1b */ }
// RigStatus gains an optional disk block:
export interface DiskInfo { free_gb: number; low: boolean; critical: boolean; }
// add `disk?: DiskInfo;` to interface RigStatus
// GotoBody analog (client): the /api/mount/goto and /api/sequence/start POST bodies accept optional `force?: boolean`
```

### `ui/src/store.ts` (one additive slice + narrow selectors)

```ts
// new state:
site: SiteInfo | null;                 // hydrated from /ws "hello".summary.site and /api/site
equipConnected: boolean;               // sticky equipment-connected flag (NOT wsConnected)
confirm: ConfirmRequest | null;        // drives ConfirmHost / confirmDialog()
// new actions:
setSite(s: SiteInfo): void;
pushConfirm(req: ConfirmRequest): Promise<boolean>;
// equipConnected maintenance lives in handleEvent("status"/"hello"):
//   mode !== "none" → equipConnected = true; mode === "none" → equipConnected = false
// narrow selector helpers (discourage broad useStore()):
export const useEquipConnected = () => useStore(s => s.equipConnected);
export const useStatus = () => useStore(s => s.status);
export const useSite = () => useStore(s => s.site);
```

> The `confirm` slice + `<ConfirmHost/>` are the **only** new shared store surface. Implementers of ConfirmDialog own `confirm`; implementers of gating own `equipConnected`; implementers of preflight own `site`. These are disjoint keys — no collisions.

### REST endpoints

- **NEW** `GET /api/sequence/preflight?ra_hours=&dec_deg=` → `PreflightAlt` (§4a).
- **CHANGED** `POST /api/mount/goto` — `GotoBody` gains `force: bool = False`; handler calls `hub._check_horizon(...)` (§4b).
- **CHANGED** `POST /api/sequence/start` — accepts `force` (query or body field) and calls `hub._check_horizon(...)` for the first light target (§4b).
- **CHANGED** `POST /api/site` (`SiteBody`) gains optional `horizon_min_deg: float = 15` and the handler sets `is_default = False`.
- **CHANGED** `GET /api/summary` / `poll_status` — `site` now includes `is_default` + `horizon_min_deg`; status gains optional `disk` (§4c).

### Backend fields

- `hub.site` → `{"latitude", "longitude", "is_default": True, "horizon_min_deg": 15.0}` (default flag flips to `False` on `/api/site`).
- `poll_status()` output → optional top-level `disk: {free_gb, low, critical}`.
- **No new `SequencePlan` fields.**

### `ui/src/index.css` (one additive block, named anchor `/* === onboarding-safety === */`)

```css
/* === onboarding-safety === */
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
:root.night :focus-visible { outline-color: var(--accent); }   /* red ring, still ≥3:1 on dark */
.confirmhold-fill { background: color-mix(in srgb, var(--text) 70%, transparent); }  /* light-on-dark, hue-independent */
.confirmhold-btn { touch-action: none; }                       /* scoped to the button box only */
.check-glyph { font-size: 18px; line-height: 1; }
/* NO new --ok-ink/--warn-ink/--bad-ink tokens (rejected: not lightness-separable; orange hurts dark adaptation).
   Status is conveyed by glyph + word token; existing --good/--warn/--bad are tertiary tint only. */
```

> **Ownership of shared files:** `store.ts`, `types.ts`, `index.css`, and the tiny `ui.tsx` `Toggle` ARIA addition are edited by **one** implementer ("foundation" task) in a first pass that lands the contracts above; all other primitives/views are then built against them in parallel. The first-pass diff is limited to the named anchors here.

---

## 10. All UI states

**PreflightStrip / PreflightModal**
- *checking* — `status` null or alt not yet fetched: `checking` rows (static `◌` + "CHECKING"); strip shows neutral, Run enabled per non-horizon checks.
- *location-default* — `site.is_default`: horizon row `disabled` ("SET LOCATION", Fix → Settings); Run allowed (warn), server guard inert on default site.
- *all-ok* — verdict "READY", single-tap Run.
- *warn-only* — warnings listed inline above Run; single-tap Run; modal not forced.
- *blocked* — Run disabled + `btn-danger` outline; "N to fix"; opening modal pins blocked rows top, offers in-place Fix; live re-check (debounced 750ms) flips rows.
- *error (preflight fetch)* — horizon `warn` "altitude unverified"; other rows derive from `status`.
- *success* — proceed → toast "Sequence starting…"; existing progress panel takes over.

**NotConnectedInterstitial** — single state; clears when `equipConnected` flips true (view fades in via `view-enter`). WS drop does **not** trigger it.

**ConfirmHold** — idle / arming (sweep + tick fill + aria countdown) / armed / firing (static "…") / done. Keyboard: arm (3s) → confirm. Same states across input modalities.

**ConfirmDialog** — open (focus trapped) / dismissed. `mode:"ok"` has only Dismiss.

**HelpDot / HelpSheet** — closed / open (popover desktop, sheet touch) / dismissed (X / outside / Escape, never scroll).

---

## 11. Night mode + 375px phone

**Night mode**
- **No new red status inks** (rejected). State = **glyph shape/weight + word token**; `--good/--warn/--bad` are tint-only and tertiary. The word token guarantees readability where all reds collapse (resolves critique3 #1, critique2 #13).
- **Focus ring** ships for night too (`:root.night :focus-visible`), ≥3:1 on `--bg-panel`.
- **ConfirmHold fill** is **light-on-dark** (`--text` at 70% alpha), so progress is legible over a red danger button (resolves critique3 #3).
- New chrome (modal, sheets, dots) uses theme tokens → inherits red palette; no white flashes. Preview keeps existing `--img-filter`.

**375px phone**
- **PreflightModal** = full-height bottom sheet, sticky full-width primary ≥48px; rows stack glyph+word+label on line 1, detail+Fix on line 2 < 360px; **blocked rows pinned top** (resolves H28).
- **HelpSheet/HelpDot** open as a centered/bottom sheet, `max-w-[calc(100vw-24px)]`, dismiss on X/outside/Escape (never scroll).
- **Detail values never truncated** (wrap to line 2) so tolerances/numbers stay readable (resolves critique3 #6).
- All new tap targets ≥44px (≥48px modal primary); `ConfirmHold` `touch-action:none` scoped to its box so the sheet still scrolls (resolves critique3 #10).
- Nav lock icon replaces the connected-`Led` (no overlap), large enough to read at phone scale.

---

## 12. Dependencies & interop

- **Hard dependency (build-order #2):** Settings/site must land first — horizon checks render `disabled` until `!site.is_default` (resolves A1). This surface ships its `/api/site` `is_default`/`horizon_min_deg` extension so Settings can flip it.
- **Hard dependency (build-order #3):** the reliability/error-surfacing P0 (sequence `error` state, toast queue, reconnect/stale banner) ships before/with this so a green pre-flight isn't followed by an invisible failure (resolves H26). The "reconnecting" banner is **that** surface's, not this one's.
- **Deferred-but-pluggable:** safety-monitor weather/abort and pier/horizon-limit enforcement (#4) and plate-solve readiness (#2) slot into `Checklist` as extra `CheckItem`s with no structural change.
- Reuses `Panel`/`Field`/`Stat`/`Led`/`Toggle` and `.btn`/`.panel`/`.field`/`.label`/`.progress-*`. Event-driven off `status`; adds only the one-shot `/api/sequence/preflight`. `_spawn`/start/goto contracts unchanged except the additive `force`.

---

## 13. File plan (disjoint ownership for parallel implementers)

**Pass 0 — Foundation (one implementer; lands shared contracts at named anchors):**
- `ui/src/types.ts` — add `SiteInfo`, `PreflightAlt`, `CheckStatus`, `CheckItem`, `DiskInfo`; `disk?` on `RigStatus`; `force?` on goto/start bodies.
- `ui/src/store.ts` — `site`, `equipConnected`, `confirm` slice + actions + narrow selectors; `equipConnected` maintenance in `handleEvent`.
- `ui/src/index.css` — `/* === onboarding-safety === */` block (focus ring, confirmhold fill, check-glyph; **no new ink tokens**).
- `ui/src/components/ui.tsx` — add `role="switch"`/`aria-checked`/`aria-label` to `Toggle` (additive, backward-compatible).
- `server/astrodeck/hub.py` — `site` default flag + `horizon_min_deg`; `_check_horizon`; `disk` in `poll_status`.
- `server/astrodeck/api/app.py` — `GET /api/sequence/preflight`; `force` on `GotoBody` + goto handler + `sequence_start`; `horizon_min_deg`/`is_default` on `/api/site`.

**Pass 1 — Primitives (parallel; each new file, no cross-deps beyond Pass 0):**
- `ui/src/components/Tooltip.tsx` + `ui/src/help.ts` — Tooltip/HelpSheet/HelpDot + HELP.
- `ui/src/components/Checklist.tsx` — Checklist/ChecklistItem.
- `ui/src/components/ConfirmHold.tsx` — ConfirmHold.
- `ui/src/components/ConfirmDialog.tsx` — confirmDialog + ConfirmHost.
- `ui/src/lib/horizon.ts` — horizonVerdict.
- `ui/src/lib/preflight.ts` — buildPreflight.
- `ui/src/components/PreflightStrip.tsx` + `ui/src/components/PreflightModal.tsx`.
- `ui/src/components/NotConnectedInterstitial.tsx`.

**Pass 2 — View wiring (parallel; each owns one view file):**
- `ui/src/App.tsx` — gating render path, mount `<ConfirmHost/>`, Night-vision `Toggle`, LOG ≥44px, nav lock icons, narrow selectors.
- `ui/src/views/SequenceView.tsx` — `PreflightStrip` above Run; modal on blocked; Abort → `ConfirmHold` (600ms); HelpSheet on Automation; route Resume through strip.
- `ui/src/views/MountView.tsx` — live-altitude GOTO guard + alt-cell severity glyph; Park → `ConfirmHold`; STOP stays instant + enlarged ≥44px.
- `ui/src/views/CaptureView.tsx` — HelpSheet/inline helper for offset/HFR/binning; Stop stays instant.
- `ui/src/views/ConnectView.tsx` — Disconnect All → `ConfirmHold`; sim button labelled "Demo / Simulator".
- `ui/src/views/FocusView.tsx` — HelpDot on step size.

---

## 14. Implementation checklist

**Backend (Pass 0)**
- [ ] `hub.site` defaults include `is_default: True`, `horizon_min_deg: 15.0`.
- [ ] `/api/site` sets `is_default=False`, accepts `horizon_min_deg`.
- [ ] `GET /api/sequence/preflight` returns `PreflightAlt`; `unknown` when `is_default`; computes `sets_in_min` via `altaz(..., unix_time)`.
- [ ] `hub._check_horizon` (gated on `not is_default`, `alt<0` only) wired into goto/plain_goto/sequence_start; **not** into `goto_and_center`; `force` threaded; `meridian_flip` path unaffected.
- [ ] `GotoBody.force` + start `force`; copy says "visible horizon", no collision claim.
- [ ] `poll_status` emits `disk` block.
- [ ] Tests: preflight verdicts (ok/low/below/unknown), default-site short-circuit, horizon guard blocks `alt<0` only with real site, meridian-flip not blocked.

**Shared FE (Pass 0)**
- [ ] types added; `disk?`/`force?` wired.
- [ ] store: `site`, `equipConnected` (sticky across WS drop), `confirm` slice, narrow selectors.
- [ ] `index.css` anchor block; **no new ink tokens**; `:focus-visible` day+night.
- [ ] `Toggle` ARIA (`role=switch`).

**Primitives (Pass 1)**
- [ ] HelpSheet/HelpDot: `any-pointer:coarse` hit area, `--accent` glyph, ≥12px body, dismiss-not-on-scroll, focus return.
- [ ] HELP copy matches §1a exactly (offset claim fixed, HFR/step caveated, OSC binning caveat).
- [ ] Checklist: glyph + **word token** always; `Stat`-style detail no-truncate; in-place Fix; static `◌` for checking (no `.blink`).
- [ ] ConfirmHold: arm→confirm one model (pointer+keyboard), jitter-tolerant cancel, luminance/geometry + `aria-live` feedback, smooth reduced-motion, 600ms default, `touch-action` scoped.
- [ ] ConfirmDialog: focus trap/Escape/restore; `mode:"ok"` single-dismiss (no hold).
- [ ] buildPreflight: verified status paths; parked/guiding/cooling are **not** false blockers; filters + disk checks; first non-cal target + all light targets for horizon; debounced live derive.
- [ ] PreflightStrip inline; PreflightModal only on blocked, pinned blockers, debounced re-check, layout frozen.

**Views (Pass 2)**
- [ ] App: fully-disabled gating on `equipConnected` (not `wsConnected`); lock icon replaces Led; Night-vision `Toggle`; LOG ≥44px; `<ConfirmHost/>` mounted.
- [ ] Sequence: strip + modal; Abort=ConfirmHold; Automation HelpSheet; Resume via strip (warn-not-block; `frames_remaining` counts).
- [ ] Mount: GOTO re-queries live alt; alt-cell severity glyph; Park=ConfirmHold; **STOP instant + ≥44px**.
- [ ] Capture/Focus/Connect: HelpSheet/HelpDot; Disconnect All=ConfirmHold; **Capture Stop instant**; sim labelled Demo.

**Acceptance (a11y/visual gates)**
- [ ] Build test: every `CheckItem.word` is non-empty (state never colour-only).
- [ ] Keyboard: Tab reaches every new control with a visible ring (day + night); modal/sheet trap + Escape + restore verified.
- [ ] Night: status rows readable with colour removed (glyph+word); ConfirmHold progress visible over red.
- [ ] Touch 375px: no Help/Checklist hit-area < 44px; modal blockers visible above the fold; detail values not truncated.
