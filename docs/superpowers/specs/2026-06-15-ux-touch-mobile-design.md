# AstroDeck — Surface Spec: "Touch Ergonomics & Mobile Field Use"

**Date:** 2026-06-15
**Surface owner:** lead designer
**Status:** build-ready (revised after 3 adversarial UX critiques)
**Review source:** `docs/reviews/2026-06-15-ux-panel-review.md` §"Touch/field ergonomics" (lines 114-119), plus accessibility (121-125), reliability (127-132), performance (134-139), visual polish (141-147), IA (149-154).

---

## 0. What changed from the draft, and why (critique resolution log)

Three reviewers (ASIAIR-veteran/field-workflow, safety+touch-ergonomics, accessibility+visual-systems) converged on the same verdict: **the tap-sizing, hold-to-confirm, 5+More nav, SVG icons, NINA-disable, and the layered safety chain are right; the time-based acceleration ramp and the translucent screen-lock are wrong and must be rethought.** Backend grounding was also factually wrong in the draft. This spec adopts the critiques. Decisions:

| # | Critique (who) | Resolution |
|---|---|---|
| R1 | Ramp is unpredictable / hostile to centering; "hold longer = faster" causes overshoot-oscillate (C1 A1-A2, C2 #3,#6) | **REMOVED the time-based acceleration ramp as the model.** Primary touch control = an **explicit, persistent discrete-rate selector** (Guide / 8× / 0.5°/s, 48-56px segmented) + a **tap-to-pulse fine-nudge** (short tap = fixed pulse-guide move) for post-GOTO centering. Hold = slew at the *currently selected fixed rate only* (never auto-accelerates above it). The headline interaction is now **predictable fixed-rate slew + tap-to-nudge**, matching ASIAIR. |
| R2 | High rates (2-4°/s) reachable from touch + 1.5s deadman = up to 6° uncommanded travel on network loss (C1 A3, A5) | **Touch manual slew is capped at `TOUCH_MAX_RATE_DEG_S = 0.6`** (worst-case stale-stop < 0.9° at the 1.5s watchdog, and we shorten the watchdog — R5). Gross repositioning is GOTO's job. The selectable touch rates are Guide-pulse / 8× sidereal (~0.033°/s) / 0.5°/s. No 2°/s or 4°/s touch band exists. |
| R3 | `onPointerLeave: stopSlew` contradicts `setPointerCapture` (C1 A4, C2 #5) | **Resolved: with pointer capture set, stop is driven ONLY by `pointerup` / `pointercancel` / `lostpointercapture`. `onPointerLeave` is removed.** A captured pointer keeps the slew bound to the finger; lifting anywhere stops it. |
| R4 | Backend facts wrong: sim `move_axis` does NOT clamp; status loop is **2.0s** not 1.5s; `alpaca.stop()` overrides `base.stop()` and only calls `abortslew` (does NOT zero MoveAxis); sim `_move_loop` ignores `stop()` (C2 #1,#2) | **Verified true against code.** Fixes: (a) STOP and release MUST zero both axes (`alpaca.stop()` rewritten to `abortslew` + `move_axis(0)` on both; sim `stop()` zeroes `_move_rates`); (b) server rate clamp lives in the `/api/mount/move` endpoint (not sim); (c) deadman is a **dedicated asyncio task at 250ms cadence**, NOT the 2.0s status loop. |
| R5 | Deadman is a "parallel/robustness" add but the safe version *requires* it; 1.5s window too long (C1 A3,H2, C2 #1, C3 closing) | **The move-axis deadman is a HARD PREREQUISITE of touch slew, not parallel.** Window = **1200ms** (comfortably > worst-case Alpaca round-trip, < the old 1.5s). Because touch rate is capped at 0.6°/s, worst case ≈ 0.72° travel. Client keepalive at **~1Hz** (separate from rate-change POSTs — R6). |
| R6 | 8Hz re-POST of `/api/mount/move` jerks real serial mounts & saturates links (C2 #4) | **POST only on rate change** (press / rate-selector change / release). A separate **1Hz keepalive POST re-asserting the current rate** feeds the deadman. No 120ms tick storm. |
| R7 | No N/S/E/W labels, no per-axis reverse (C2 #8); no on-sky direction indication (C1 §I) | Pad arrows labelled **N/S/E/W** (not bare ▲▼◀▶); **reverse-RA / reverse-Dec toggles** persisted to `localStorage`; a one-line "direction depends on pier side & image orientation — toggle if it moves the wrong way" hint. |
| R8 | NINA greyed-pad is a dead end — user still must center (C2 #7) | NINA mode keeps the pad **live in tap-to-pulse mode only** (pulse-guide works through NINA-bridge-backing ASCOM? No — NINA bridge `move_axis` raises AND it has no pulse endpoint we rely on, so in NINA mode the pad offers **small relative GOTO nudges** via `/api/mount/goto` with a tiny RA/Dec delta, plus the "use GOTO for big moves" hint). Hold-slew arrows are disabled in NINA; tap-nudge stays. |
| R9 | STOP/Abort/Halt must be 1-tap, NOT hold; hold-on-Abort is a safety regression (C1 B1,C1; C2 #14) | **STOP, Abort, focuser Halt = instant 1-tap, full-width, OUTSIDE the D-pad.** Hold-to-confirm reserved for *non-urgent* destructive: Disconnect, Park. Sequencer/step delete uses **undo** (5s toast), not hold. §0 rule corrected: "red destructive that is *urgent* = 1-tap big bar; *non-urgent & irreversible* = hold; *reversible* = instant + undo." |
| R10 | STOP in D-pad center = fat-finger hazard; trained "red=hold" users will hold it (C1 B2, C3 D2) | **STOP moved out of the grid to a full-width bar below the pad**, with a **categorically distinct emergency treatment** (solid `--bad` fill, octagon ■ motif, larger, NOT an outline) so it never reads as a HoldButton. |
| R11 | TouchGuard translucent scrim over a dim red night screen makes the "still a monitor" claim false & can hide the one error you must see (C1 D1,D3; C3 B,E3) | **Redesigned: TouchGuard does NOT use a uniform scrim.** It is a full-viewport **input blocker** with a **solid `bg-panel` status chip** (full contrast, deterministic) carrying the live readout, NOT floating text over the frame. Critical-alert passthrough is **gated**: lock is *disabled* (with a tooltip) until the reliability surface's sequence-`error` rendering lands; until then the lock simply does not exist, so it can never mask an alert. |
| R12 | Auto-lock during framing is a footgun (C1 D2, C2 #9) | **Auto-lock defaults OFF, and the 1-min option is removed** (min offered = 3 min). Never auto-locks within **30s of any manual control interaction**. Unlock = a single deliberate **slide gesture** (not an 800ms cold-glove hold). |
| R13 | WakeLock coupled to `locked` contradicts "pocket the phone" (C1 D4) | **WakeLock requested only while a sequence is running OR `monitorAwake` is explicitly on** — NOT on `locked`. Locking to pocket the phone lets the screen sleep. |
| R14 | Primary-5 buries Rig+Align (the first setup steps) under More (C1 E1) | **Primary 5 = Rig, Align, Mount, Focus, Capture.** Overflow = Guide, Plan, Power. Setup-critical views are one tap; occasional/monitoring views are in More. |
| R15 | "More tab changes its icon" is a shape-shifting tab (C1 E2) | **More tab keeps a fixed grid icon always.** When an overflow view is active, More shows an accent dot + the overflow view name as a sub-label, but the icon never changes. |
| R16 | NIGHT duplicated in header + sheet (C1 E3) | **NIGHT lives ONLY in the header** (compact 44px icon toggle — it's toggled constantly). The More sheet does NOT duplicate it. More hosts LOG, Lock, Haptics, Reverse-axis toggles. |
| R17 | `pointer: coarse` mis-buckets stylus/2-in-1 field tablets (C1 F1) | Use **`any-pointer: coarse`** for the size bumps, AND a user override `touchSizing: 'auto'|'on'|'off'` (store + `.touch-ui` root class) so the field user can force glove sizing regardless of inferred pointer. |
| R18 | 16px field font reflows the dense sequencer step grid (C1 F2, C2 #15, C3 D math) | **Sequencer step rows get a dedicated mobile CARD layout below `md`** (stacked label+input, 44px inputs) — the fixed `grid-cols-[90px_70px_60px_50px_60px_auto]` is `md:`-and-up only. 16px field font applies; no overflow because the dense grid no longer exists on phone. |
| R19 | No `:focus-visible`; HoldButton un-keyboardable; SlewPad pointer-only locks out keyboard (C3 C) | Global `:focus-visible` outline added. `HoldButton` §0 contract MUST include a keyboard path (Enter arms → Enter confirms). SlewPad arrows get a **keyboard nudge fallback** (focusable, Enter/Space = one fixed pulse at the selected rate). |
| R20 | `--text-dim` fails AA but draft hangs instructional text on it (C3 B) | Add **`--text-dim2`** (AA-passing both modes). All *instructional / state-explanation* text uses `--text` or `--text-dim2`; `--text-dim` is decorative-only. |
| R21 | Color-only band/active-tab/badge state collapses in night-red (C3 A1-A3) | Rate selector encodes by **label text + a growing speed glyph (1-3 `▰`)**, not color, in all modes. Active nav tab gets a **filled `bg-accent/15` chip + border + filled-vs-outline icon swap**, not just color. LOG badge gets an **outline ring + count**, not red-on-red fill. |
| R22 | No `prefers-reduced-motion`; decorative glows hurt dark adaptation (C3 E1,E2) | All new animation wrapped in `@media (prefers-reduced-motion: reduce)` → instant. **Decorative SLEW/edge glows dropped in night mode**; only the safety alert pulse survives. |
| R23 | Sidereal-multiple labels off by ~4×; 0.004°/s mislabeled (C2 #12, C1 A2) | Rate labels computed from the real sidereal constant **15.041 arcsec/s = 0.004178°/s**. Bands: **Guide-pulse** (tap only), **8× sidereal** (0.0334°/s) for fine slew, **0.5°/s** for set. Labels show the multiple AND the °/s. |
| R24 | Server clamp 6.0 below the 4.0 ramp ceiling = theater; not device-aware (C1 A5, C2 #13) | Server clamp = **`TOUCH_MAX_RATE_DEG_S = 0.6`** (matches the client cap, actually engages). Reading real Alpaca `axisrates` max is promoted to a **P1 follow-up** (additive `mount.caps.max_rate_deg_s`), but v1 is safe because the touch cap is well below any mount's max. |
| R25 | `touch-action: none` missing → page scroll hijacks slew (C3/C1 §J) | Pad container gets **`touch-action: none`** so a vertical drag on an arrow never triggers `main` scroll. |
| R26 | Haptics dead on iOS/iPad (the field-dominant device); toggle ships as a no-op; band-buzz reads as alarm (C1 G1-G3, C2 #10) | Haptics is **purely additive**; the **numeric °/s + large label is the PRIMARY feedback**. The Haptics toggle **hides itself when `!navigator.vibrate`** (no dead control). **No buzz on rate-selector change** (no alarm-fatigue). `haptics.error()` is **debounced (≥1.5s)** so a flaky-network night doesn't buzz constantly. |
| R27 | App.tsx broad `useStore()` + adding badge/nav/lock inherits per-tick re-render (C3 G, C1 §J) | New header/nav/lock pieces are **separate child components with narrow selectors**; this surface does NOT widen `App`'s subscription. Stated as a hard boundary so it doesn't collide with the perf surface's `App` split. |
| R28 | Two ✕ glyphs (step vs target) different commitment, indistinguishable (C3 G) | Step remove = **minus/× icon, instant + undo**. Target delete = **trash icon + outline ring, instant + undo** (no hold). Visibly different affordances; neither requires a hidden hold. |
| R29 | `max-w-[280px]` pad math optimistic re panel/main padding (C1 F3, C3 D1) | Pad `max-w-[260px]`, arrows `tap-lg` 56px (not 60) with one canonical **`gap-2` (8px)** token. 3×56+2×8 = 184px, fits inside the ~311px usable @375px with margin. The three competing gap values (8px token / gap-2 / gap-3) collapse to **one `--tap-gap: 8px` referenced everywhere.** |
| R30 | Below-horizon / pier-limit unguarded; this surface makes manual motion easier (C1 §I, review P0 lines 43-45) | This surface adds a **client-side altitude guard on the slew pad**: while holding, if `status.mount.alt` drops below `MIN_SLEW_ALT_DEG = 10`, the pad **auto-stops and flashes "below horizon limit."** Full pier-limit enforcement is the safety surface's job (coordination noted §15); this is the minimal guard appropriate to a control that *adds* manual motion. |

**Rejected / deferred (with reason):**
- **Full `axisrates` device-max clamp (C2 #13 as P0):** deferred to P1. Rejected as a v1 blocker because the 0.6°/s touch cap is already below every mount's max, so no driver will throw; reading caps is an enhancement, not a safety gate. (Tracked as additive `mount.caps`.)
- **Glove capacitive-touch limitation (C1 §I):** acknowledged, not solvable in software. We rename `--tap-glove` → `--tap-lg` so we don't *promise* glove support we can't deliver. Documented as a known limitation, not a feature.
- **Pulse-guide tap-nudge in NINA mode (C2 #7 first option):** rejected — NINA bridge exposes no manual pulse path we can rely on; we use **small relative GOTO** there instead (R8).

---

## 1. Overview

Scope: the P0/P1 tablet/field-UX fixes from the panel review. Targets: slew pad, focuser nudges, filter chips, sequencer deletes, header buttons, the unreadable 8-item mobile nav, plus four new capabilities — **predictable fixed-rate slew + tap-to-pulse fine-nudge** (not a time ramp), **adaptive landscape/portrait at `md` (768px)**, **additive haptics**, and a **monitor-safe screen-lock**.

This surface **consumes** (does not define) the design-system `HoldButton` primitive (Batch 2, §0). It **owns and exports** as shared leaf utilities: `haptics`, the slew controller, the `tap`/`tap-lg` size tokens, `--text-dim2`, and the global `:focus-visible` rule.

Guiding principle after critique: **legible and predictable beats clever.** Fixed rates the user picks deliberately; instant emergency stops; full-contrast monitor readouts; safety guards on any control that adds motion.

---

## 2. Shared contracts (TS types, store slices, REST, backend fields)

> Everything in this section is a cross-surface contract. Implementers of OTHER surfaces depend on these exact shapes. Additive only — no existing field changes.

### 2.1 TS types — `ui/src/types.ts` (additive)

```ts
// Manual-slew rate options (fixed, predictable). Computed from sidereal = 0.004178°/s.
export type SlewRateId = "pulse" | "fine" | "set";
export interface SlewRateOption {
  id: SlewRateId;
  label: string;        // "GUIDE" | "8× SID" | "0.5°/s"
  rateDegS: number;     // 0 for pulse (tap-only); 0.0334; 0.5
  pulseMs?: number;     // for "pulse": fixed pulse-guide duration (e.g. 250ms)
}

// Optional, additive — populated later by the device-caps follow-up (P1).
export interface MountCaps { max_rate_deg_s?: number; }
// RigStatus.mount MAY gain `caps?: MountCaps` — readers must treat as optional.

export interface TouchSettings {
  hapticsEnabled: boolean;
  touchSizing: "auto" | "on" | "off";   // glove-size override
  reverseRa: boolean;
  reverseDec: boolean;
  autoLockMs: number | null;            // null = off; options: null | 180000 | 300000
}
```

### 2.2 Store slice — `ui/src/store.ts` (additive to `AppState`)

```ts
// state
locked: boolean;                 // touch-guard engaged
lockAvailable: boolean;          // false until reliability sequence-error rendering lands (gate, R11)
monitorAwake: boolean;           // explicit wake-lock-as-monitor toggle (decoupled from locked, R13)
touch: TouchSettings;

// actions
setLocked: (v: boolean) => void;       // setting true MUST also stop any active slew (R11/§4.6)
setMonitorAwake: (v: boolean) => void;
setTouch: (patch: Partial<TouchSettings>) => void;  // persists each key to localStorage
```

Init (read from `localStorage`):
```
locked: false,
lockAvailable: false,            // flipped true by reliability surface when error-render ships
monitorAwake: false,
touch: {
  hapticsEnabled: localStorage["astrodeck-haptics"] !== "0",
  touchSizing: (localStorage["astrodeck-touch-size"] as any) || "auto",
  reverseRa: localStorage["astrodeck-rev-ra"] === "1",
  reverseDec: localStorage["astrodeck-rev-dec"] === "1",
  autoLockMs: Number(localStorage["astrodeck-autolock"]) || null,
}
```
`setTouch` mirrors `hapticsEnabled` to `haptics.enabled`. `navMore` open/close and the rate-selector index are **local `useState`** in their components (transient UI), NOT global — to avoid widening subscriptions.

**Undo contract (R9/R28):** reuse the reliability surface's toast-queue if present; until then this surface ships a minimal `pendingUndo: { label: string; undo: () => void } | null` local to the affected view (Sequence). Stated so the reliability surface can later host it. No global store field required for v1.

### 2.3 REST endpoints

**No new endpoints.** Existing `/api/mount/move` (`{axis: "ra"|"dec", rate_deg_s: number}`) and `/api/mount/goto` (`{ra_hours, dec_deg, center}`) and `/api/mount/stop` are reused. Contract additions are **transparent server-side behaviors**:

- `POST /api/mount/move` — now **clamps `rate_deg_s` to ±`TOUCH_MAX_RATE_DEG_S` (0.6)** and records `last_move_ts` for the deadman. Request/response shape unchanged.
- `POST /api/mount/stop` — now guarantees **both axes are zeroed** in addition to `abortslew` (via the device fix in §6.2). Shape unchanged.
- Tap-nudge in NINA mode reuses `POST /api/mount/goto` with a small RA/Dec delta from current position (computed client-side). No new endpoint.

### 2.4 Backend fields / behavior

- `server/astrodeck/api/app.py`: module const `TOUCH_MAX_RATE_DEG_S = 0.6`; clamp in `move_axis`; stamp `hub.last_move_ts`.
- `server/astrodeck/hub.py`: new attr `last_move_ts: float | None`; new method `ensure_move_watchdog()` + `_move_watchdog()` (dedicated 250ms task, **not** the 2.0s status loop). Halts both axes if a non-zero move is older than `MOVE_DEADMAN_MS = 1200`.
- `server/astrodeck/devices/alpaca.py`: `AlpacaTelescope.stop()` rewritten to `abortslew` **then** `move_axis("ra",0)` + `move_axis("dec",0)` (currently only calls `abortslew` — verified, this is the dangerous gap).
- `server/astrodeck/devices/sim.py`: `SimTelescope.stop()` (or `move_axis(_,0)` path) must **clear `_move_rates`** so `_move_loop` exits; today `stop()`→`base.stop()`→`move_axis(0,0)` sets rates to 0 which DOES exit the loop, but confirm the override chain — sim relies on `base.stop()`, which is correct *if* sim doesn't override `stop()`. **Action: ensure `SimTelescope` does NOT override `stop()`, so `base.stop()` zeroes `_move_rates` and the loop exits.** Add a `_move_loop` clamp to `TOUCH_MAX_RATE_DEG_S` defensively (sim currently does NOT clamp — verified).
- No DB/model changes. `SequencePlan` / `Target` / `ExposureStep` untouched.
- `RigStatus.mount.caps?: { max_rate_deg_s }` — optional/additive, populated by the P1 caps follow-up; readers treat as `undefined` in v1.

---

## 3. Tap-target sizing & global a11y CSS — `ui/src/index.css`

Append after `.btn` / token blocks:

```css
:root {
  --text-dim2: #8b9bb5;   /* AA-passing instructional text (day) — R20 */
  --tap-min: 44px;        /* WCAG / HIG floor */
  --tap-lg: 56px;         /* primary touch controls (NOT "glove" — see rejected items) */
  --tap-gap: 8px;         /* the ONE canonical gap token — R29 */
}
:root.night { --text-dim2: #d08a8a; }   /* lighter astronomy-red, AA over night bg */

/* Size bumps gated on ANY coarse pointer (R17), OR the user override .touch-ui */
@media (any-pointer: coarse) {
  .btn { min-height: var(--tap-min); padding: 10px 16px; }
  .field, select.field { min-height: var(--tap-min); font-size: 16px; } /* 16px = no iOS zoom */
}
.touch-ui .btn { min-height: var(--tap-min); padding: 10px 16px; }
.touch-ui .field, .touch-ui select.field { min-height: var(--tap-min); font-size: 16px; }
.no-touch-ui .btn { min-height: revert; }  /* touchSizing:"off" hard opt-out */

.tap    { min-width: var(--tap-min); min-height: var(--tap-min); }
.tap-lg { min-width: var(--tap-lg);  min-height: var(--tap-lg);  }

/* R19 — focus rings (review flagged global outline:none) */
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

/* R22 — reduced motion */
@media (prefers-reduced-motion: reduce) {
  .view-enter, .blink, .more-sheet-in, .ramp-bar, .hold-fill { animation: none !important; transition: none !important; }
}
/* R22 — kill decorative glow in night mode (keep only safety pulse .alert-pulse) */
:root.night .slew-arrow.is-active { box-shadow: none; }
```

`touchSizing` resolution: `"auto"` → rely on `@media (any-pointer: coarse)`; `"on"` → add `.touch-ui` to `<html>`; `"off"` → add `.no-touch-ui`. Set in the same place `night` toggles the root class (`store.setTouch`).

### 3.1 Per-component size table

| Component / file | Now | New |
|---|---|---|
| Slew pad arrows (`SlewPad`) | ~58px `btn` | `tap-lg` (56px), labelled N/S/E/W, `--tap-gap` |
| STOP | center cell, 10px | **full-width bar below pad**, solid `--bad`, ■ motif, 56px, 14px label, 1-tap |
| Rate selector | chips `!text-[10px]` | 48-56px segmented `pulse / 8× / 0.5°/s`, persistent, with speed glyph |
| Focuser ±10/100/1000 | `btn` ~36px | `btn tap`; below `md` a 3×2 56px grid |
| Focuser Halt | 1-tap danger | **stays 1-tap** (urgent), `tap`, distinct ■ treatment |
| Filter chips (`CaptureView`) | `btn !px-3` ~30px | `btn tap` min-w 56px, `gap-2`, wrap |
| Sequencer step ✕ | ~16px glyph | `tap` 44px minus-icon button, **instant + undo** |
| Sequencer target ✕ | `!text-[10px]` | `tap` 44px **trash-icon + ring**, **instant + undo** |
| Sequencer `+ step` | `!text-[10px]` | `btn tap` |
| Capture Single/Loop | `btn` | `tap-lg` on coarse, `haptics.start/stop` |
| Capture Stop | `btn-danger` | **1-tap** `tap-lg` (urgent — NOT hold) |
| Mount Park / Disconnect | `btn` | `<HoldButton>` (non-urgent destructive) |
| Header NIGHT | 28px | `tap` 44px icon toggle, header-only |
| Header LOG | 28px | `tap` 44px, outline-ring error badge |
| Mobile bottom nav | 8 items, 8px | **5 + More**, 56px, 24px SVG icons, 11px labels |

Rule (corrected): every interactive element ≥44px; primary touch controls (arrows, capture, GOTO) 56px; **urgent destructive (STOP/Abort/Stop-capture/Halt) = 1-tap distinct ■ bar**; **non-urgent irreversible (Park/Disconnect) = HoldButton**; **reversible (deletes) = instant + undo**.

---

## 0 (contract). The `HoldButton` primitive — owned by design-system surface

Do **not** implement here. Batch 2 exports from `ui/src/components/HoldButton.tsx`:

```ts
export interface HoldButtonProps {
  onConfirm: () => void;
  holdMs?: number;        // default 600
  className?: string;
  danger?: boolean;
  haptic?: boolean;       // default true; fires haptics.confirm() on completion
  children: ReactNode;
  disabled?: boolean;
  "aria-label"?: string;
}
export function HoldButton(p: HoldButtonProps): JSX.Element;
```

**Required behaviors this surface depends on (pin in the contract):**
- Renders a fill that animates over `holdMs`; early release cancels; completion calls `onConfirm`.
- **Keyboard path (R19, a11y-mandatory):** focusable; **Enter/Space arms (first press) → second Enter/Space within 3s confirms**; Esc cancels. A hold is un-keyboardable, so this two-step fallback is required, not optional.
- Respects `prefers-reduced-motion` (fill jumps to discrete 0/armed/full).
- Used here for **non-urgent destructive only**: Disconnect, Park. **NOT** STOP, Abort, Halt, Stop-capture, or deletes.

If Batch 2 hasn't merged, a local stub `ui/src/components/HoldButton.stub.tsx` (same signature, real radial fill + the keyboard path so timing is testable) unblocks dev; swap is a one-line barrel change. The stub MUST implement the fill + keyboard path (not a no-op) so field timing is real.

---

## 4. Slew pad — predictable fixed-rate slew + tap-to-pulse (the revised headline)

### 4.1 New files (owned by this surface)
- `ui/src/lib/slewController.ts` — framework-agnostic controller (rate selection, keepalive, deadman client-side mirror, axis-reverse, alt-guard). Testable, no React.
- `ui/src/components/SlewPad.tsx` — the pad component.
- `ui/src/lib/haptics.ts` — §7.
- `ui/src/components/navIcons.tsx` — §6.3.

### 4.2 Controller model — `slewController.ts`

```ts
export const SLEW_RATES: SlewRateOption[] = [
  { id: "pulse", label: "GUIDE", rateDegS: 0,      pulseMs: 250 }, // tap-only fine nudge
  { id: "fine",  label: "8× SID", rateDegS: 0.0334 },              // 8 × 0.004178
  { id: "set",   label: "0.5°/s", rateDegS: 0.5 },
];
export const TOUCH_MAX_RATE_DEG_S = 0.6;   // mirror of server clamp (R2/R24)
export const MIN_SLEW_ALT_DEG = 10;        // client alt-guard (R30)

export interface SlewControllerOpts {
  getRate: () => SlewRateOption;          // current selection
  reverseRa: () => boolean;
  reverseDec: () => boolean;
  getAlt: () => number | null;            // from status.mount.alt for the guard
  postMove: (axis: "ra"|"dec", rateDegS: number) => Promise<void>;
  postNudge: (axis: "ra"|"dec", dir: -1|1) => Promise<void>; // pulse OR small relative GOTO
  onStateChange?: (s: SlewState) => void; // for UI label + alt-guard flash
}
export class SlewController {
  startHold(axis: "ra"|"dec", dir: -1|1): void; // posts fixed rate once; starts 1Hz keepalive
  stopHold(): void;                              // posts rate 0 once; clears keepalive
  tapNudge(axis: "ra"|"dec", dir: -1|1): void;   // single fixed pulse (pulse-guide or rel-GOTO)
  forceStop(): void;                             // panic / external (lock, blur) — posts 0, idempotent
  isHolding(): boolean;
}
```

Logic:
1. **Tap** (pointerup within < 200ms and no movement) → `tapNudge`: one fixed move. In `pulse` rate it's a pulse-guide-sized move; in any rate when `status.mode==="nina"` it's a small relative GOTO. Predictable, repeatable.
2. **Hold** (pointer held) → `startHold`: post the **currently selected fixed rate once** (sign × `dir` × reverse flags), clamped to `TOUCH_MAX_RATE_DEG_S`. Start a **1Hz keepalive** re-posting the same rate (feeds the server deadman). **No acceleration. No re-POST except keepalive.** (R1, R6)
3. **Release / cancel / lost-capture** → `stopHold`: post rate 0 once, clear keepalive.
4. **Alt-guard:** keepalive tick also checks `getAlt()`; if `< MIN_SLEW_ALT_DEG`, call `forceStop()` and emit a `belowHorizon` state → UI flashes "below horizon limit." (R30)
5. **`pulse` rate disables hold** (it's tap-only); arrows still tap-nudge. In NINA mode, hold is disabled for all rates; tap-nudge (relative GOTO) stays. (R8)

### 4.3 `SlewPad.tsx` wiring

```ts
const handlers = (axis: "ra"|"dec", dir: -1|1) => ({
  onPointerDown: (e) => {
    e.currentTarget.setPointerCapture(e.pointerId);  // keep events if finger slides off
    haptics.tap();
    ctrl.beginPress(axis, dir);   // controller decides tap vs hold on release/timeout
  },
  onPointerUp:          () => ctrl.endPress(axis, dir),
  onPointerCancel:      () => ctrl.forceStop(),
  onLostPointerCapture: () => ctrl.forceStop(),
  // NO onPointerLeave (R3) — capture keeps it bound; stop only on up/cancel/lost.
  onKeyDown: (e) => { if (e.key === "Enter" || e.key === " ") ctrl.tapNudge(axis, dir); }, // R19
  tabIndex: 0,
  role: "button",
  "aria-label": `slew ${axisLabel(axis, dir)}`,   // "slew north" etc.
});
```

Robustness:
- **`touch-action: none`** on the pad container (R25) so vertical drags don't scroll `main`.
- **Global safety effect:** on `blur` / `visibilitychange(hidden)` / `setLocked(true)` → `ctrl.forceStop()` and POST 0. (R11/R13)
- **`setPointerCapture`** binds the slew to the finger; lifting anywhere stops it (works *with* the no-`onPointerLeave` rule).

### 4.4 Pad layout (N/S/E/W + reverse + STOP bar)

```
        [  N  ]
[ W ]   [pulse 8× 0.5]   [ E ]      ← rate selector sits center (replaces STOP-in-center)
        [  S  ]
   [ ▣  S T O P  (full-width, solid --bad, ■) ]   ← R10: outside the grid, 1-tap
   reverse RA [ ] · reverse Dec [ ]               ← R7
   "moves wrong way? toggle reverse — depends on pier side & framing"  (--text-dim2)
```
Arrows labelled **N/S/E/W** with small ▲▼◀▶ glyphs beneath. The **rate selector occupies the center cell** (was STOP). STOP is a **separate full-width emergency bar** below, solid fill, ■ octagon motif — categorically distinct from the outline HoldButtons elsewhere (R10/C3 D2). Pad `max-w-[260px]` centered, `--tap-gap` gaps (R29).

### 4.5 Live feedback (R21 — shape+text, not color)
Below the rate selector, one mono line: the selected rate's **label + °/s + a speed glyph (`▰`, `▰▰`, `▰▰▰` for pulse/fine/set)**. While holding: the active arrow gets an inset highlight (border, not glow in night mode); the line reads e.g. `HOLD · 0.50°/s ▰▰▰`. On alt-guard trip: `▣ BELOW HORIZON LIMIT — stopped` in `--bad` with the `.alert-pulse` (the one surviving night pulse). Minimum text size **14px on coarse** (R-F floor).

### 4.6 NINA mode (R8)
`status.mode === "nina"`: hold-slew arrows **disabled** (NINA `move_axis` raises — verified). Tap-nudge stays, implemented as a **small relative GOTO** (`/api/mount/goto` with current RA/Dec ± a fixed small delta). Pad shows a one-line note "NINA: arrows do fine nudges; use catalog GOTO for big moves." No dead pad. (Previously the draft greyed the whole pad — a regression for NINA users who still must center.)

### 4.7 Desktop (fine-pointer)
On `(pointer: fine)` only, the same fixed-rate model applies (mouse-hold = slew at selected rate, click = nudge). The NINA guard applies identically. No separate legacy chip path (the rate selector IS the chips, kept for everyone — predictable for all). (Resolves C1's note that the draft left a parallel desktop chip path un-NINA-guarded.)

---

## 5. Mobile bottom nav: 8 → 5 + More

### 5.1 Structure (`App.tsx`, IA reorder — R14)
Order per review line 149 (Rig → Align → Mount → Focus → Capture → Guide → Plan → Power):
```ts
const PRIMARY: ViewName[] = ["connect", "polar", "mount", "focus", "capture"]; // Rig, Align, Mount, Focus, Capture
const OVERFLOW: ViewName[] = ["guide", "sequence", "power"];                    // Guide, Plan, Power
```
Bottom nav = 5 primary tabs + a 6th **More** button (fixed grid icon). Each tab: 56px min-height, **24px SVG icon**, **11px** label `tracking-normal` (was 8px `tracking-widest` — the cause of unreadability). Active tab: **`bg-accent/15` chip + 2px top border + filled-vs-outline icon swap** (R21 — not color alone).

### 5.2 More overflow sheet — `ui/src/components/NavMoreSheet.tsx`
- Bottom sheet, `role="dialog"`, backdrop dismiss, `.more-sheet-in` slide (reduced-motion → instant).
- Rows: the 3 overflow views (56px, icon + label + live `<Led>`).
- Global controls hosted here: **LOG** (with outline-ring error badge), **Lock Screen** (disabled w/ tooltip until `lockAvailable`), **Haptics toggle** (hidden if `!navigator.vibrate` — R26), **Reverse-RA / Reverse-Dec** toggles, **touch-size** override (`auto/on/off`).
- **NIGHT is NOT here** — it stays in the header only (R16).
- More tab: fixed grid icon always; if an overflow view is active, show an accent dot + that view's name as a sub-label — icon never morphs (R15).
- Dismiss on selection / backdrop / Esc. `haptics.tap()` on open.

### 5.3 Icons — `ui/src/components/navIcons.tsx`
Inline single-weight 24px `currentColor` SVGs replacing the inconsistent unicode glyphs (◈◉◎✛⊕❖≡⏻ — verified in `App.tsx` `NAV`). 8 view icons + a More grid icon, each with a **filled and outline variant** for active-state swap (R21). ~15 lines each. (Full Lucide migration remains the visual surface's job; we scope only the nav set.)

---

## 6. Backend changes (safety-critical — touch slew is NOT safe without them)

### 6.1 Server rate clamp + move timestamp — `server/astrodeck/api/app.py`
```python
TOUCH_MAX_RATE_DEG_S = 0.6

@app.post("/api/mount/move")
async def move_axis(body: MoveAxisBody):
    try:
        tel = hub.require("telescope")
        rate = max(-TOUCH_MAX_RATE_DEG_S, min(TOUCH_MAX_RATE_DEG_S, body.rate_deg_s))
        await tel.move_axis(body.axis, rate)
        hub.note_move(body.axis, rate)   # stamps last_move_ts; arms/disarms deadman
        return {"ok": True}
    except DeviceError as e:
        raise _err(e)
```

### 6.2 STOP must zero both axes — `server/astrodeck/devices/alpaca.py`
Current `AlpacaTelescope.stop()` only calls `abortslew` — **verified**, and on real ASCOM drivers `AbortSlew` does NOT reliably stop a `MoveAxis`. Fix:
```python
async def stop(self) -> None:
    await self._put("abortslew")
    await self.move_axis("ra", 0)
    await self.move_axis("dec", 0)
```
Sim: ensure `SimTelescope` does **not** override `stop()`, so `base.stop()` (which calls `move_axis(_,0)` on both) clears `_move_rates` and `_move_loop` exits. Add a defensive clamp in sim `_move_loop`/`move_axis` to `TOUCH_MAX_RATE_DEG_S` (sim currently stores raw rate — verified).

### 6.3 Move-axis deadman — `server/astrodeck/hub.py` (HARD PREREQUISITE, R5)
Dedicated task, **not** the 2.0s status loop (which is too slow — verified line 419):
```python
MOVE_DEADMAN_MS = 1200

def note_move(self, axis, rate):
    self._move_rates_seen[axis] = rate
    self.last_move_ts = time.monotonic()
    self.ensure_move_watchdog()

async def _move_watchdog(self):
    while True:
        await asyncio.sleep(0.25)   # 250ms cadence
        if self.last_move_ts is None: continue
        moving = any(r != 0 for r in self._move_rates_seen.values())
        if moving and (time.monotonic() - self.last_move_ts) * 1000 > MOVE_DEADMAN_MS:
            tel = self.devices.get("telescope")
            if tel: await tel.stop()      # zeroes both axes (§6.2)
            self._move_rates_seen = {"ra": 0, "dec": 0}
            bus.log("warning", "manual slew watchdog: auto-halt (stale keepalive)", "safety")
```
Client keepalive (1Hz, §4.2) re-stamps `last_move_ts`; a dropped network mid-hold auto-halts within ≤1.2s, worst-case ≤0.72° travel at the 0.6°/s cap. **Coordinate with the safety surface (§15) to implement this watchdog ONCE.**

### 6.4 Deferred (P1, additive): `mount.caps.max_rate_deg_s`
Read Alpaca `axisrates` max into `RigStatus.mount.caps`; client clamps the `0.5°/s` option to the real device max if lower. Not a v1 gate (touch cap 0.6 is already below any mount max).

---

## 7. Haptics — `ui/src/lib/haptics.ts` (additive leaf, R26)

```ts
type Pattern = "tap" | "start" | "stop" | "confirm" | "warn" | "error";
const PATTERNS: Record<Pattern, number | number[]> = {
  tap: 10, start: [0, 20], stop: [0, 12, 40, 12],
  confirm: [0, 15, 30, 15], warn: [0, 30], error: [0, 50, 40, 50],
};
let lastError = 0;
export const haptics = {
  enabled: true,
  supported: typeof navigator !== "undefined" && !!navigator.vibrate,
  fire(p: Pattern) {
    if (!this.enabled || !this.supported) return;
    if (p === "error") { const now = Date.now(); if (now - lastError < 1500) return; lastError = now; } // debounce R26
    navigator.vibrate(PATTERNS[p]);
  },
  tap(){this.fire("tap")}, start(){this.fire("start")}, stop(){this.fire("stop")},
  warn(){this.fire("warn")}, error(){this.fire("error")},
};
```
Wiring: slew `tap()` on press, `start()` on hold begin, `stop()` on release. Capture `start/stop`. GOTO `start()`. HoldButton `confirm()` (internal). **No buzz on rate-selector change** (R26 — no alarm fatigue). Error toast → `haptics.error()` (debounced) — one-line add in `store.ts` log handler. **The toggle hides itself when `!haptics.supported`** (no dead control on iPhone/iPad). Numeric °/s + label remain the **primary** feedback; haptics is additive.

---

## 8. Screen-lock / TouchGuard — `ui/src/components/TouchGuard.tsx` (R11/R12/R13)

Pure client-side; no backend, no device state change. Rendered once at App root (z-50) with a **narrow selector** (`useStore(s => s.locked)`).

### 8.1 Availability gate (R11)
`lockAvailable` is **false** until the reliability surface ships sequence-`error` rendering. While false, the Lock control is **disabled with tooltip** "Screen lock enables after error-alerting lands." A lock that could mask the one alert you must see is not shipped. When true, lock is enabled.

### 8.2 Behavior (monitor-safe, NOT a scrim — R11/C3 B)
- Full-viewport **transparent input blocker** (`pointer-events:auto`) — blocks all taps to the app beneath, but is **not** a uniform translucent wash.
- A **solid `bg-panel` status chip** (full contrast, deterministic) centered/anchored: RA/Dec, mount state, RMS, SEQ x/y, sensor temp — same data as the header strip, on an opaque chip so legibility is guaranteed in night mode. (Draft's translucent text-over-frame is dropped.)
- **Slide-to-unlock** affordance (single deliberate horizontal drag, ~60% of a 200px track) — NOT an 800ms cold-glove hold (R12). Accidental taps do nothing.
- **Critical-alert passthrough:** when `sequence.state === "error"` (now rendered, post-gate) the chip surfaces the alert with `.alert-pulse` and the unlock prompt pulses. This is the ONE animation kept in night mode (R22).
- **Auto-lock:** `touch.autoLockMs` default **null (off)**; options **off / 3min / 5min** (1-min removed — R12). Idle timer watches `pointerdown`/`keydown`; **never engages within 30s of a manual control interaction** (R12). A dismissible "locking in 5s" affordance precedes engage (NOT via the error toast slot — its own element, R-C3 G).
- **`setLocked(true)` MUST call `slewController.forceStop()`** and POST rate 0 (R11/C2 #11) — locking mid-hold can't leave the mount slewing under the overlay.

### 8.3 WakeLock — `ui/src/lib/useWakeLock.ts` (R13)
Request `navigator.wakeLock.request("screen")` only while **a sequence is running OR `monitorAwake` is on** — NOT while `locked` (locking to pocket the phone must let the screen sleep). Guarded; silent no-op where unsupported. `monitorAwake` is an explicit toggle in More.

---

## 9. Adaptive landscape/portrait layout (`md` = 768px)

Width-breakpoint strategy (no `orientation` API). Tailwind defaults.

| View | Now | New |
|---|---|---|
| MountView root | `lg:grid-cols-[340px_1fr]` | `md:grid-cols-[minmax(290px,340px)_1fr]`; left = Pointing + SlewPad (`max-w-[260px]`), right = Catalog |
| CaptureView root | `xl:grid-cols-[1fr_340px]` | `md:grid-cols-[1fr_320px] xl:grid-cols-[1fr_360px]` |
| SequenceView root | `xl:grid-cols-[1fr_320px]` | `md:grid-cols-[1fr_300px]` |
| FocusView root | `lg:grid-cols-[1fr_320px]` | `md:grid-cols-[1fr_300px]` |
| Sequencer step rows | fixed `grid-cols-[90px_70px_60px_50px_60px_auto]` | **below `md`: stacked CARD** (label+44px input per field); `md:`+ keeps the grid (R18) |

SlewPad keeps `max-w-[260px]` and centers so portrait doesn't stretch it; spare landscape width goes to the catalog. `max-w-[1500px]` content cap stays. Catalog at 768px: with the pad column at 290-340px the table gets ~430-480px; below that the catalog table itself adopts a 2-line compact row (coordinate w/ visual surface) — acceptable at `md`, full table at `lg`+.

---

## 10. All UI states

**SlewPad**
- *Idle (connected, unparked, alpaca/sim):* arrows enabled, rate selector shows current, feedback line dim with glyph.
- *Holding:* active arrow inset-highlighted, line `HOLD · <rate>°/s ▰…`, 1Hz keepalive running.
- *Tap-nudge:* brief flash on the tapped arrow, one pulse/rel-GOTO.
- *pulse rate selected:* arrows tap-only (hold disabled), line "tap to nudge".
- *NINA mode:* hold disabled, tap = relative GOTO, note line shown.
- *No mount:* greyed, "connect a mount."
- *Parked:* greyed, "unpark to slew" + inline Unpark.
- *Below-horizon trip:* auto-stopped, `▣ BELOW HORIZON LIMIT` pulse.
- *Move POST fails:* toast + debounced `haptics.error()` + `forceStop()` (never leave mount thinking it moves).
- *Network loss mid-hold:* client blur/visibility handler stops; server deadman is backstop (≤1.2s).

**STOP / Abort / Halt / Stop-capture:** single resting ■ bar; 1-tap fires immediately; `haptics.stop()`.

**Mobile nav / More:** *closed* (5 + More); *open* (sheet + backdrop + LEDs); *overflow view active* (More shows dot + name, icon fixed).

**TouchGuard:** *lockAvailable false* — Lock disabled+tooltip; *unlocked* — not rendered; *locked* — input blocker + opaque status chip + slide-to-unlock; *locked + sequence error* — alert on chip + pulsing unlock; *auto-lock pending* — dismissible 5s countdown element.

**HoldButton (Park/Disconnect only):** resting outline; holding radial fill + arm haptic; early release resets; confirmed → action + `haptics.confirm()`; keyboard arm→confirm path.

**Deletes (step/target):** instant removal + 5s undo affordance; undo restores.

**Haptics:** no visible UI except the toggle, which is **hidden when unsupported**.

---

## 11. Night-mode + 375px-phone notes

Viewport 375×667, `:root.night` (`--accent:#ff5d5d`, `--img-filter` red).

- **Header (h-12):** `ASTRODECK` wordmark, compact **NIGHT** icon-toggle (44px, lit red), LINK LED, LOG (outline-ring badge). RA/Dec strip stays `hidden md:flex` (gone at 375px — correct).
- **MountView:** `md` two-pane collapses to stacked — Pointing, then SlewPad (`max-w-[260px]`; 3×56 + 2×8 = **184px**, fits the ~311px usable inside `main p-4` + `panel p-4`), rate selector, STOP bar, reverse toggles, then Catalog (compact rows).
- **Night palette:** arrows `--accent` red on near-black; active = inset border (no glow — R22); STOP bar solid `--bad #ff4040`, ■ motif, 14px label — unmistakable. Rate feedback encodes by **label + °/s + growing `▰` glyph**, NOT color (night collapses reds — R21).
- **Bottom nav:** 5 tabs (Rig/Align/Mount/Focus/Capture) + More, 56px, 24px red-tinted icons, 11px labels. Active = `bg-accent/15` chip + top border + filled icon (R21).
- **More sheet:** red-tinted; rows Guide/Plan/Power + LOG + Lock (gated) + Haptics (hidden if unsupported — likely on iPad) + Reverse-RA/Dec + touch-size. (NIGHT not duplicated here — R16.)
- **Lock:** if `lockAvailable`, tap Lock → `haptics.warn()` → input blocker + **opaque `bg-panel` chip** showing `TRACKING · SEQ 42/120 · RMS 0.6"` in full-contrast red mono (NOT translucent over the frame); slide-to-unlock pill. WakeLock NOT held by lock (R13).
- **Slewing on phone:** pick `0.5°/s`, press E, feel `start()`, watch `HOLD · 0.50°/s ▰▰▰`, release → `stop()`, mount halts (client 0 + deadman backstop). Wrong direction? toggle Reverse-RA. Tap (don't hold) for a fine pulse to center after GOTO.
- **Instructional text** ("moves wrong way?…", "unpark to slew", NINA note) uses **`--text-dim2`** (AA), not the failing `--text-dim` (R20). All new live-state text ≥12px (14px coarse) (R-F). All new animation respects `prefers-reduced-motion` (R22).

---

## 12. File ownership (disjoint — parallel-safe)

**Create (this surface only):**
- `ui/src/lib/slewController.ts`
- `ui/src/lib/haptics.ts`
- `ui/src/lib/useWakeLock.ts`
- `ui/src/components/SlewPad.tsx`
- `ui/src/components/TouchGuard.tsx`
- `ui/src/components/NavMoreSheet.tsx`
- `ui/src/components/navIcons.tsx`
- `ui/src/components/HoldButton.stub.tsx` (temporary; delete on Batch 2 merge)
- `ui/src/components/HeaderControls.tsx` (NIGHT/LOG/badge as narrow-selector child — R27)
- `ui/src/components/BottomNav.tsx` (5+More as narrow-selector child — R27)
- Tests: `ui/src/lib/slewController.test.ts`, `ui/src/lib/haptics.test.ts`, `server/.../test_move_watchdog.py`

**Modify (each line called out to minimize collision):**
- `ui/src/index.css` — tokens (`--text-dim2`, tap tokens, `--tap-gap`), `@media (any-pointer: coarse)` + `.touch-ui`, `.tap`/`.tap-lg`, `:focus-visible`, reduced-motion, night glow-kill, STOP-bar + slide-unlock + ramp/feedback styles.
- `ui/src/App.tsx` — mount `<TouchGuard/>`, `<HeaderControls/>`, `<BottomNav/>` (replace inline header buttons + inline nav). **Does NOT widen `useStore()`** (R27/§15).
- `ui/src/views/MountView.tsx` — replace inline pad (lines ~63-81) with `<SlewPad>`; `md` two-pane grid; GOTO `tap`.
- `ui/src/views/CaptureView.tsx` — filter chips `tap`; Stop 1-tap `tap-lg`; `md` grid; capture `haptics`.
- `ui/src/views/FocusView.tsx` — nudges `tap` + responsive grid; Halt stays 1-tap `tap`.
- `ui/src/views/SequenceView.tsx` — step ✕ → 44px minus-icon + undo; target ✕ → trash-icon + undo; `+step` `tap`; mobile card step layout below `md`; Abort stays 1-tap.
- `ui/src/store.ts` — `locked`/`lockAvailable`/`monitorAwake`/`touch` + setters; debounced `haptics.error()` in log handler.
- `ui/src/types.ts` — `SlewRateId`/`SlewRateOption`/`MountCaps`/`TouchSettings`.
- `ui/src/components/index.ts` (barrel) — export new components.
- `server/astrodeck/api/app.py` — `TOUCH_MAX_RATE_DEG_S` clamp + `hub.note_move` in `move_axis`.
- `server/astrodeck/hub.py` — `last_move_ts`, `note_move`, `_move_watchdog` (shared w/ safety surface).
- `server/astrodeck/devices/alpaca.py` — `stop()` zeroes both axes.
- `server/astrodeck/devices/sim.py` — no `stop()` override (rely on `base.stop()`); defensive rate clamp in `_move_loop`.

---

## 13. Interop / coordination (§15 detail)

- **Design system (Batch 2):** consumes `HoldButton` (§0, incl. mandatory keyboard path); contributes `haptics`, `slewController`, `tap`/`tap-lg`/`--tap-gap`/`--text-dim2`, `:focus-visible` back to shared.
- **Reliability surface:** owns `unseenLogErrors` (LOG badge renders only if present); owns sequence-`error` rendering — **`lockAvailable` flips true only when that lands** (hard gate, R11); owns the toast queue (undo + auto-lock countdown should migrate onto it).
- **Safety surface:** **co-owns the move-axis deadman** (§6.3) — implement once; owns full pier/horizon-limit enforcement (this surface adds only the minimal client alt-guard, R30).
- **Performance surface:** is splitting `App.tsx`'s broad `useStore()`. This surface **renders all new header/nav/lock pieces as separate narrow-selector children** and does NOT touch `App`'s existing subscription (R27) — no collision.
- **IA reorder (Batch 1):** the `NAV` order (R14) matches review line 149; this surface introduces the canonical 8-entry order + 5/More split — **land with the IA reorder in one PR** to avoid two orderings colliding.
- **Settings surface (Batch 1):** `touch.*` fields are Settings-ready; until Settings ships, the More sheet is their home (haptics, reverse-axis, touch-size, auto-lock).
- **NINA bridge:** SlewPad respects `mode==="nina"` (hold disabled, tap=rel-GOTO — R8).

---

## 14. Implementation checklist

**Backend (prerequisites — do FIRST, R5):**
- [ ] `alpaca.py` `stop()` → `abortslew` + `move_axis("ra",0)` + `move_axis("dec",0)`.
- [ ] `sim.py` — confirm no `stop()` override; add defensive `TOUCH_MAX_RATE_DEG_S` clamp in `move_axis`/`_move_loop`.
- [ ] `hub.py` — `last_move_ts`, `note_move`, `_move_watchdog` (250ms, 1200ms deadman). Coordinate w/ safety surface (implement once).
- [ ] `app.py` — clamp `rate_deg_s` to ±0.6 in `move_axis`; call `hub.note_move`.
- [ ] Test: watchdog auto-halts both axes after stale keepalive; clamp caps rate; STOP zeroes MoveAxis (sim + alpaca-mock).

**Shared contracts:**
- [ ] `types.ts` — `SlewRateId`, `SlewRateOption`, `MountCaps`, `TouchSettings`.
- [ ] `store.ts` — `locked`/`lockAvailable`/`monitorAwake`/`touch` + setters; debounced `haptics.error()`.

**CSS / a11y:**
- [ ] `--text-dim2` (both modes), tap tokens, single `--tap-gap`.
- [ ] `@media (any-pointer: coarse)` + `.touch-ui`/`.no-touch-ui`; `touchSizing` root-class wiring.
- [ ] global `:focus-visible`; `prefers-reduced-motion` block; night glow-kill.
- [ ] STOP-bar (solid ■), slide-to-unlock, rate-feedback, more-sheet styles.

**Leaf utilities:**
- [ ] `haptics.ts` (+ `supported`, debounced error, hide-when-unsupported); test.
- [ ] `slewController.ts` (fixed-rate hold, tap-nudge, 1Hz keepalive, reverse, alt-guard, forceStop, NINA rel-GOTO); test (no acceleration; tap vs hold; idempotent stop; clamp; alt-guard trip; NINA path).
- [ ] `useWakeLock.ts` (sequence-running OR monitorAwake; NOT locked).
- [ ] `navIcons.tsx` (8 views + More, filled+outline variants).

**Components:**
- [ ] `HoldButton.stub.tsx` (real fill + keyboard arm→confirm).
- [ ] `SlewPad.tsx` — N/S/E/W, pointer-capture, no `onPointerLeave`, `touch-action:none`, keyboard nudge, rate selector center, STOP bar, reverse toggles, alt-guard flash, NINA mode, global blur/visibility/lock forceStop.
- [ ] `TouchGuard.tsx` — gated on `lockAvailable`; opaque status chip (no scrim); slide-to-unlock; auto-lock (off/3/5min, 30s grace, dismissible countdown); `setLocked(true)`→forceStop.
- [ ] `NavMoreSheet.tsx` — overflow rows + LOG + Lock(gated) + Haptics(hidden if unsupported) + reverse + touch-size; fixed More icon.
- [ ] `HeaderControls.tsx` — NIGHT(44px)/LOG(badge), narrow selectors.
- [ ] `BottomNav.tsx` — 5+More, 56px/24px/11px, chip+border+icon-swap active state, narrow selectors.
- [ ] barrel exports.

**View wiring:**
- [ ] `App.tsx` — mount TouchGuard/HeaderControls/BottomNav; do NOT widen `useStore()`.
- [ ] `MountView.tsx` — `<SlewPad>`, `md` grid, GOTO `tap`.
- [ ] `CaptureView.tsx` — chips `tap`, Stop 1-tap, `md` grid, capture haptics.
- [ ] `FocusView.tsx` — nudges `tap` + responsive grid, Halt 1-tap.
- [ ] `SequenceView.tsx` — step/target deletes (icon + undo), `+step` `tap`, mobile card step layout, Abort 1-tap.

**Verification:**
- [ ] 375px night-mode walkthrough (§11): slew, reverse, tap-nudge, lock(if available), nav.
- [ ] Field-safety: kill network mid-hold → mount halts ≤1.2s ≤0.72°; STOP zeroes both axes; lock mid-hold stops slew.
- [ ] a11y: keyboard-reach every new control; focus rings; reduced-motion; contrast on all new text.
- [ ] Haptics toggle hidden on a no-`navigator.vibrate` device.

---

## 15. Open coordination items (to orchestrator)
1. **`HoldButton` signature + keyboard path** — confirm with design-system surface (§0).
2. **`unseenLogErrors` + sequence-`error` rendering** — reliability surface; `lockAvailable` gate depends on the latter (hard).
3. **Move-axis deadman** — shared with safety surface; implement once.
4. **`NAV` order + 5/More split** — land with Batch-1 IA reorder in one PR.
5. **`App.tsx` subscription split** — performance surface; this surface stays clear by using child components with narrow selectors.
