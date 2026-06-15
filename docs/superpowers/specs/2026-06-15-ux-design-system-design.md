# AstroDeck Design System — Icons, Accessibility, Night-Vision, Polish

**Date:** 2026-06-15
**Surface:** Batch 2 cross-cutting foundation (icons, a11y tokens, night-vision LUT, touch/safety primitives, polish)
**Status:** Build-ready. All paths absolute under `C:\Users\bear\astro\`.

This surface is the shared visual/interaction foundation the other Batch-2/3 surfaces
(Live-preview, Monitor, Settings, Onboarding, Touch-ergonomics) depend on. The `Icon`,
`Confirm`, `Tooltip`, `EmptyState`, `Led`, `Stat` primitives are specified to exact
contracts so those surfaces drop in without re-touching these files.

This revision incorporates three adversarial UX critiques. A summary of what changed and
what was rejected is in **§16 Critique resolution log** — read it to understand the
reasoning behind the non-obvious decisions (inverted luminance ladder, single-tap STOP,
overlay-scrim dimmer, directional glyphs).

---

## 1. Overview

### Goals
1. Replace inconsistent unicode-glyph nav/action icons with a recognizable single-weight
   inline-SVG set (no font/library dependency; `currentColor` inheritance gives night/dim/focus for free).
2. Fix WCAG AA contrast failures in both day and night palettes, **re-derived from measured
   contrast** (not estimated hex).
3. Rebuild the night-vision LUT so good/warn/bad are distinguishable **and astronomy-safe**,
   with **alarm = brightest/most-salient** (severity tracks salience), reinforced by non-color
   shape + glyph cues that survive `prefers-reduced-motion` and grayscale.
4. Add a global screen dimmer that is **safe**: never traps the user, never dims critical
   alerts, never breaks fixed-positioned overlays, and never double-dims the live preview to
   the point you cannot judge focus.
5. Add field-safe destructive-action confirmation that is **correctly scoped**: hold-to-confirm
   only for slow/irreversible actions; emergency motion STOP/HALT stay single-tap-immediate.
6. Keyboard focus rings, legible graph fonts, empty-state illustrations, dead-space fill,
   view cross-fade, dimmed night starfield — all respecting reduced-motion.
7. Fold in the toast-reliability fix (queue + auto-dismiss + LOG badge) because this surface
   already changes toast rendering/dimming, and a dimmed single-overwriting toast would be a
   net regression.

### Non-goals (deferred, no parity claim made)
- Auto-night-at-dusk / geolocation-driven dimming (belongs to Settings + safety surfaces).
- Server-side preference sync. Brightness/night/lock are pure client display state in
  `localStorage`. An optional future `GET/PUT /api/preferences` is sketched in §6 but **not**
  built here — no backend dependency.
- Live-preview tooling (zoom/pan/stretch/star overlay) is the Live-preview surface's job; this
  surface only guarantees the preview `<img>` stays focus-legible under night mode + dimmer.

---

## 2. File ownership plan (disjoint, for parallel implementers)

Each file below has exactly one owner so parallel implementers never collide. Within `ui.tsx`
and `index.css` (shared by several primitives), the work is split into **named, non-overlapping
sections** with sentinel comments so two implementers can land different sections in sequence
without merge conflict; build order in §15 makes the dependency chain explicit.

| File | Action | Owner-section | What |
|---|---|---|---|
| `ui/src/components/icons.tsx` | **CREATE** | ICONS | `Icon` component + glyph set (nav, action, directional, status) |
| `ui/src/index.css` | **EDIT** | CSS-TOKENS / CSS-FOCUS / CSS-DIMMER / CSS-LED / CSS-STARS / CSS-MOTION / CSS-FLEX / CSS-EMPTY | tokens (measured), focus rings, dimmer scrim, LED shapes, starfield, motion+reduced-motion, flex helpers, empty-state |
| `ui/src/components/ui.tsx` | **EDIT** | UI-LED / UI-STAT / UI-FIELD / UI-CONFIRM / UI-TOOLTIP / UI-EMPTY | rewrite `Led`,`Stat`; `Field` gains `hint`; add `Confirm`,`Tooltip`,`InfoDot`,`EmptyState` |
| `ui/src/store.ts` | **EDIT** | STORE-DISPLAY / STORE-TOAST | brightness/night/lock slice + persistence; toast queue + log badge slice |
| `ui/src/App.tsx` | **EDIT** | APP-HEADER / APP-NAV / APP-LAYOUT / APP-OVERLAYS | header cluster (icons, dimmer, lock, night), nav icons + IA reorder, flex-stretch + cross-fade, toast/lock overlays outside dimmed subtree |
| `ui/src/components/graphs.tsx` | **EDIT** | GRAPHS | font sizes, HTML label overlay, aspect-locked SVG, decoration vs data token split |
| `ui/src/components/polar.tsx` | **EDIT (light)** | POLAR | aspect-lock reticle; uses new `--bad` ladder automatically (token-only otherwise) |
| `ui/src/views/ConnectView.tsx` | **EDIT (light)** | — | unicode→Icon; `Led state=` sweep; `Confirm` on Disconnect All; EmptyState; NINA-link LED |
| `ui/src/views/MountView.tsx` | **EDIT (light)** | — | directional Icons on slew pad; STOP stays single-tap; `setPointerCapture` jog |
| `ui/src/views/PolarView.tsx` | **EDIT (light)** | — | directional Icons on AZ/ALT; STOP single-tap; remove tone glyph from TOTAL |
| `ui/src/views/FocusView.tsx` | **EDIT (light)** | — | unicode→Icon; EmptyState for V-curve; HALT stays single-tap; InfoDot on HFR/STEP |
| `ui/src/views/CaptureView.tsx` | **EDIT (light)** | — | unicode→Icon; InfoDot on OFFSET/HFR; preview stays focus-legible |
| `ui/src/views/GuideView.tsx` | **EDIT (light)** | — | unicode→Icon; `Led state=`; EmptyState |
| `ui/src/views/SequenceView.tsx` | **EDIT (light)** | — | `Confirm` on Abort + delete-target/step; unicode→Icon; InfoDot on DITHER |
| `ui/src/views/PowerView.tsx` | **EDIT (light)** | — | unicode→Icon; EmptyState; panel fill |
| `ui/index.html` | **EDIT (1 line)** | — | inline `--screen-brightness` + `.night` on `<html>` pre-paint to kill flash |

No backend model changes, no new REST endpoints. (See §6 for the deferred optional endpoint.)

---

## 3. Shared contracts

The only contract changes are **client-side**: TS types and store slices. No REST/backend fields
change. This is what keeps the surface independent and shippable.

### 3.1 TS types — `ui/src/types.ts` (additive only)

```ts
// Display/UI preference state (client-only, persisted to localStorage)
export type LedState = "off" | "on" | "warn" | "bad" | "busy";
export type Tone = "good" | "warn" | "bad";

export interface ToastItem {
  id: number;
  level: "info" | "warning" | "error";
  message: string;
  source?: string;
  /** epoch ms when it should auto-dismiss; null = sticky (error stays until dismissed) */
  expiresAt: number | null;
}
```

`PolarState`, `MountStatus`, etc. are **unchanged**. `RigStatus.mode` already carries
`"nina"`, which is the only data the NINA-link health LED needs (§9.2) — no new field.

### 3.2 Store slice — `ui/src/store.ts`

Two additive slices. Existing `night` field is **moved into** the display slice (its
persistence already exists; we extend it). No existing action signature changes except
`toggleNight` (already public) and `showToast`/log handling (internal).

```ts
interface DisplaySlice {
  night: boolean;
  locked: boolean;            // screen-lock / touch-guard
  brightness: number;         // active dim factor applied to --screen-brightness, clamped 0.08..1
  dayBrightness: number;      // remembered day value
  nightBrightness: number;    // remembered night value (default 0.45)

  toggleNight: () => void;          // swaps active brightness to the other mode's memory
  setBrightness: (v: number) => void;
  resetBrightness: () => void;      // -> 1.0 in current mode (the always-reachable escape)
  setLocked: (v: boolean) => void;
}

interface ToastSlice {
  toasts: ToastItem[];        // queue, max 4 visible; errors sticky, info/warn auto-dismiss
  unseenErrors: number;       // LOG badge count, cleared when log drawer opened
  pushToast: (level: ToastItem["level"], message: string, source?: string) => void;
  dismissToast: (id: number) => void;
  clearUnseen: () => void;
}
```

`showToast(level, message)` is **kept as a thin alias** to `pushToast` so the ~12 call sites in
views (`showToast("error", …)`) do not change. The log-event handler increments `unseenErrors`
on `level==="error"`.

### 3.3 localStorage keys (client source of truth)

| key | type | default |
|---|---|---|
| `astrodeck-night` | `"0"`/`"1"` | `"0"` (existing) |
| `astrodeck-bright-day` | float string | `"1"` |
| `astrodeck-bright-night` | float string | `"0.45"` |

`locked` is **not** persisted (a lock must not survive reload — that would trap the user).

### 3.4 REST endpoints / backend fields

**None.** No `hub.py`, model, or route changes. The deferred optional `GET/PUT /api/preferences`
(§6) is owned by the future Settings surface, not this one.

---

## 4. Icon system — `ui/src/components/icons.tsx` (CREATE)

### Rationale (critique D1 resolved)
We keep **inline SVG** (not a Lucide dependency) for `currentColor` inheritance, zero bundle
cost, and to hand-tune the four astro-specific glyphs. But we adopt the critique's substance:
common action glyphs use **standard, recognizable silhouettes** matching Lucide geometry, and
the nav glyphs are **redrawn as distinct silhouettes** — no more "three circles with ticks."

### Constraints
24×24 viewBox, `stroke="currentColor"`, `fill="none"`, `stroke-width=1.5`, round caps/joins.
Default render 18px; **nav/touch contexts pass `size={20}`**. Decorative by default
(`aria-hidden`); pass `title` for a labelled graphic (`role="img"` + `<title>`).

**Status/warning dots use a real `<circle r="0.8">`, never a zero-length line** (critique P2:
`M…v.01` does not paint reliably with round caps across renderers).

### API

```tsx
export type IconName =
  // nav (each a DISTINCT silhouette — verified at 20px desaturated):
  | "rig"        // stacked equipment / connector plug
  | "capture"    // camera body + shutter (NOT a ringed circle)
  | "focus"      // draw-tube + bracket reticle (corner brackets, not radial ticks)
  | "mount"      // telescope-on-tripod silhouette
  | "align"      // crosshair-in-circle (single distinct reticle)
  | "guide"      // oscilloscope/graph trace in a box (NOT a bare star — avoids "favorite")
  | "plan"       // checklist + timeline
  | "power"      // power glyph
  | "settings"   // gear (future Settings nav)
  | "monitor"    // dashboard + trend (future Monitor nav)
  // actions:
  | "play" | "stop" | "pause" | "refresh" | "bridge" | "link"
  | "sun" | "moon" | "lock" | "unlock"
  // status:
  | "alert" | "check" | "info" | "x"
  // directional (semantic data — polar/slew correction arrows):
  | "arrow-up" | "arrow-down" | "arrow-left" | "arrow-right";

export interface IconProps {
  name: IconName;
  size?: number;        // px, default 18
  className?: string;   // color/opacity utilities
  strokeWidth?: number; // default 1.5
  title?: string;       // set => role="img" + <title>; else aria-hidden
}
export function Icon(props: IconProps): JSX.Element;
```

Implementation: a `Record<IconName, JSX.Element>` of path fragments; the `<svg>` wrapper sets
`role={title ? "img" : undefined}`, `aria-hidden={title ? undefined : true}`,
`focusable="false"`, `className={"shrink-0 " + className}`.

### Distinct-silhouette requirement (critique D2/D3 resolved)
`capture`, `focus`, `align` MUST NOT all read as "circle + marks". Implementer test gate:
render the 10 nav glyphs at 20px, desaturate to grayscale, and confirm each is identifiable.
- `capture` = camera body rectangle + lens hump + shutter dot.
- `focus` = vertical draw-tube with two corner brackets (reticle framing), no radial ticks.
- `align` = single circle with internal crosshair + center dot.
- `mount` = OTA tube on a tripod/wedge (telescope silhouette).
- `guide` = a small trace line inside a framed box (graph), not a star.

### Directional glyphs (critique C resolved — semantic data, not chrome)
`arrow-up/down/left/right` exist specifically for the **polar AZ/ALT correction arrows** and the
**slew pad**. The unicode→Icon sweep MUST map by sign, NOT route through `play`:
- PolarView: `azArrow = az < 0 ? "arrow-left" : "arrow-right"`; `altArrow = alt > 0 ? "arrow-down" : "arrow-up"`.
- MountView slew pad: `arrow-up/down/left/right` per axis/dir.

### Decorative→Icon mapping (chrome only)

| Old unicode | Where | Icon |
|---|---|---|
| `◈` Rig nav | App nav | `rig` |
| `◉ ◎ ✛ ⊕ ❖ ≡ ⏻` nav | App nav | `capture` `focus` `mount` `align` `guide` `plan` `power` |
| `◈ Bridge` button | ConnectView | `bridge` (distinct from `rig`) |
| `▶` Connect/Run/Resume | ConnectView/SequenceView | `play` |
| `⟳` Scan | ConnectView | `refresh` |
| `✛ Solve & Sync` | MountView | `align` |
| `● / ◐` NIGHT/DAY | App header | `moon` / `sun` (single target-state indicator; see §7.4) |

`settings`/`monitor` are defined now so the Settings/Monitor surfaces drop into the nav without
re-touching this file.

---

## 5. Accessibility tokens — `index.css` (CSS-TOKENS)

### 5.1 Measured-contrast mandate (critiques F/P0 resolved)
Every token routed into **text, button labels, or graph data labels** MUST measure **≥4.5:1**
against the surface it sits on (`--bg`, `--bg-raise`, or `--bg-panel`). `--text-faint` is the
**only** sub-4.5:1 role and may be used **only** for non-text decoration (gridline strokes,
ghost-illustration strokes). The implementer MUST verify final hex with a contrast tool before
commit — do not ship estimated values. Day estimates in the original draft were low (day
`--text-dim` actually ≈7:1) and night values were wrong (night `--text-dim #a85252` = 3.92:1,
fails). Final values below are chosen to clear the bar.

### 5.2 Day palette (split dim into two roles)

```css
:root {
  --text-dim:   #97a6c2;   /* ~6.5:1 on --bg — AA for small text */
  --text-faint: #5d6c85;   /* decoration only — gridlines/ghost strokes, never readable text */
  /* day status & accent unchanged (all pass): good #4ade80, warn #fbbf24, bad #f87171,
     accent #7dd3fc. accent-dim is RESTRICTED to borders/large-text only (it is 3.78:1 on
     --bg — FAILS as small text; stop using it as small-button label color, see 5.4). */
}
```

### 5.3 Night palette — re-derived (critiques A/F/P0 resolved)
See §8 for the full night LUT including the corrected luminance ladder. Night text roles:

```css
:root.night {
  --text:       #e08a8a;   /* primary, ~6:1 on night --bg */
  --text-dim:   #cc7676;   /* re-derived to ~5.0:1 (was #a85252 = 3.92:1, FAILED) */
  --text-faint: #6e2a2a;   /* decoration only */
}
```

### 5.4 `--accent-dim` correction (critique P1 resolved)
`--accent-dim` (day `#38709c`, night `#a03535`) measures **3.78:1** on `--bg` — it FAILS AA as
small text. **Audit rule:** `--accent-dim` may be used for **borders and large text (≥18.66px or
≥14px bold) only**. The secondary buttons (`QUERY`, `Connect PHD2`, `GO`) currently use
`color: var(--accent)` (which passes) — keep that. Any place using `--accent-dim` as a small
label color must switch to `--accent`.

### 5.5 Audit rule for implementers
- `.label`, `.panel-title`, `Stat` label/unit, nav labels, graph **numeric** labels → `--text-dim`.
- `--text-faint` → only SVG gridline strokes and ghost-illustration strokes.
- Graph axis **numbers** (`19986`, `+2"`, V-curve endpoints, best-position readout) are **data,
  not decoration** → `--text-dim` (critique F1/P2). Only the dashed gridline *strokes* use `--text-faint`.

---

## 6. Data flow & the deferred endpoint

- Brightness / night / lock / toasts live entirely in the Zustand store. Brightness and night
  persist to `localStorage`; they drive one CSS var (`--screen-brightness`) and one root class
  (`.night`). No WS, no REST, no backend.
- **Deferred optional (NOT built here):** if cross-device sync is later wanted, the Settings
  surface may add `night_mode`/`brightness_day`/`brightness_night` to a settings blob behind a
  new `GET/PUT /api/preferences`. The Settings view would then call `setBrightness`/`toggleNight`
  and also persist server-side. Designed-for, deferred; client `localStorage` is the source of
  truth today. Listed here only so the future surface knows the shape — no work in this surface.
- Icon, Led, Stat, Tooltip, Confirm, EmptyState are **pure presentational** — props +
  `currentColor` + CSS tokens, zero store coupling. That is what makes them safe shared deps and
  trivially `React.memo`-able (compatible with the performance surface).

---

## 7. Global brightness dimmer — SAFE rebuild

### 7.1 Mechanism: overlay scrim, NOT `filter` on `#root` (critiques B/F1 resolved)
The original `#root { filter: brightness() }` is rejected for four reasons the critiques proved:
(a) it makes `#root` the containing block for every `position: fixed` overlay (fragile invariant);
(b) it stacks badly with `.panel { backdrop-filter: blur() }`; (c) it dims the error toast — the
one channel that must punch through; (d) it dims its own slider thumb, trapping the user.

**Replacement:** a fixed black overlay scrim whose opacity is `1 - brightness`, plus a brightness
filter applied **only to a dedicated content wrapper that does NOT contain the overlays**.

```css
/* CSS-DIMMER */
/* dim only the app content wrapper; overlays (toast, lock, dimmer control) are siblings */
.dim-content { filter: brightness(var(--screen-brightness, 1)); }

/* belt-and-suspenders true darkening below what filter can do (OLED black-pixel safe):
   a full-screen multiply scrim, pointer-events:none, sitting ABOVE content but BELOW
   the always-bright overlay layer. opacity tracks the dimmer. */
.dim-scrim {
  position: fixed; inset: 0; z-index: 40; pointer-events: none;
  background: #000;
  opacity: var(--scrim-opacity, 0);   /* = clamp(0, 1 - brightness*1.05, 0.92) */
}
```

DOM structure in `App.tsx` (APP-LAYOUT / APP-OVERLAYS):

```
#root
 ├─ .dim-content   (header + nav + main + log drawer)  ← filter: brightness()
 ├─ .dim-scrim     (fixed, multiply darkening, pointer-events:none)  ← above content
 └─ .overlay-top   (fixed, z-50, NOT dimmed):
       ├─ toast stack          ← always full brightness (critique B3)
       ├─ critical-alert layer
       ├─ lock overlay
       └─ brightness reset / "100%" affordance  (critique B2 escape hatch)
```

Because the dimmer/reset control and toasts live in `.overlay-top` (a sibling of `.dim-content`,
not a child), they are **never** dimmed and **never** re-anchored by a `filter` containing block.
`position: fixed` inside `.dim-content` is avoided entirely — the only fixed elements are in
`.overlay-top`. This removes the fragile invariant (critique B4/F1).

### 7.2 Floor + escape hatch (critiques B1/B2/E2 resolved)
- **Floor lowered to 0.08** (was 0.25). Combined with the scrim, the UI can go near-black for a
  truly dark site. Copy near the control: "Dims the screen image — for full darkness also lower
  your device backlight." (critique B1: be explicit it is not the hardware backlight.)
- **Always-reachable reset:** a `resetBrightness()` affordance lives in `.overlay-top` at FULL
  brightness, plus a global keyboard shortcut (`Shift+B` → reset to 1.0) and a long-press
  (≥1s) anywhere on the lock overlay restores brightness. The user can never be trapped behind a
  dimmed-out thumb (critique B2).

### 7.3 No double-dimming the preview (critiques A4/A3 resolved)
The live preview `<img class="astro">` MUST stay focus-legible. Two protections:
1. The night `--img-filter` is rebuilt to **preserve luminance/contrast** (§8.4) — no `saturate(6)`,
   no `brightness(0.55)`.
2. The Live-preview surface owns an independent per-image stretch/brightness; this surface's
   global dimmer is **not** the only way to see the frame. As a hard rule for this surface: the
   `.astro` image's effective luminance after global dim must never drop below what the user can
   recover via the preview's own stretch. We document this as a contract; the global dimmer scrim
   sits above the preview but the preview surface may render its own controls in `.overlay-top` if
   it needs an always-bright stretch slider. (Coordination note for the Live-preview implementer.)

### 7.4 Header control + single mode indicator (critiques D5/H2/F2/P3 resolved)
One mode indicator only — no adjacent sun-AND-moon. The toggle shows the **target** state:

```tsx
// APP-HEADER (inside .dim-content; the RESET lives in .overlay-top)
<div className="flex items-center gap-2">
  <input type="range" min={0.08} max={1} step={0.02} value={brightness}
    aria-label="Screen brightness"
    aria-valuetext={`${Math.round(brightness * 100)} percent`}
    onChange={(e) => setBrightness(Number(e.target.value))}
    className="dimmer" />
  <span className="mono text-[10px] text-dim w-8 text-right">{Math.round(brightness*100)}%</span>
  {/* −/+ steppers for glove use, each ≥44px hit area (critique P1) */}
  <button className="step-btn" aria-label="Dimmer down" onClick={() => setBrightness(brightness-0.06)}>−</button>
  <button className="step-btn" aria-label="Dimmer up"   onClick={() => setBrightness(brightness+0.06)}>+</button>
</div>
<button className="btn night-toggle" onClick={toggleNight}
  aria-label={night ? "Switch to day mode" : "Switch to night mode"}
  title={night ? "Switch to day mode" : "Switch to red night-vision mode"}>
  <Icon name={night ? "sun" : "moon"} size={14} /> {night ? "DAY" : "NIGHT"}
</button>
<button className="btn lock-toggle" onClick={() => setLocked(true)}
  aria-label="Lock screen" title="Lock screen (touch guard)">
  <Icon name="lock" size={14} />
</button>
```

- The slider shows a **numeric %** and sets `aria-valuetext` (critique F2).
- The toggle has exactly **one** icon = the target mode (critique H2/D5). Accessible name set.
- `.dimmer` track gets a **≥3:1 outline** so its extent is visible at night (critique P3): night
  track uses `--line-bright` with a `1px solid var(--text-dim)` outline.
- Touch (`.dimmer` thumb, steppers) sized to ≥44px hit area (critique P1). On phone the steppers
  are the primary input; the thin slider is secondary.

### 7.5 Store implementation (critiques F4/P3 resolved — clamp on READ)

```ts
const clampB = (v: number) => Math.min(1, Math.max(0.08, v));
const numLS = (k: string, d: number) => {
  const n = Number(localStorage.getItem(k));
  return Number.isFinite(n) ? n : d;     // value may be out of range; clampB below fixes it
};
function applyBrightness(v: number) {
  const b = clampB(v);                                  // clamp on READ too (critique F4)
  document.documentElement.style.setProperty("--screen-brightness", String(b));
  document.documentElement.style.setProperty("--scrim-opacity",
    String(Math.min(0.92, Math.max(0, 1 - b * 1.05)))); // scrim deepens past filter floor
}

const initialNight = localStorage.getItem("astrodeck-night") === "1";
const dayB   = clampB(numLS("astrodeck-bright-day", 1));
const nightB = clampB(numLS("astrodeck-bright-night", 0.45));

// in create():
night: initialNight, locked: false,
dayBrightness: dayB, nightBrightness: nightB,
brightness: initialNight ? nightB : dayB,

setBrightness: (raw) => {
  const v = clampB(raw); applyBrightness(v);
  const night = get().night;
  if (night) { localStorage.setItem("astrodeck-bright-night", String(v)); set({ brightness: v, nightBrightness: v }); }
  else       { localStorage.setItem("astrodeck-bright-day",   String(v)); set({ brightness: v, dayBrightness: v }); }
},
resetBrightness: () => { get().setBrightness(1); },
toggleNight: () => {
  const night = !get().night;
  localStorage.setItem("astrodeck-night", night ? "1" : "0");
  document.documentElement.classList.toggle("night", night);
  const b = night ? get().nightBrightness : get().dayBrightness;
  applyBrightness(b); set({ night, brightness: b });
},
setLocked: (v) => set({ locked: v }),
```

`applyBrightness(initialNight ? nightB : dayB)` runs once at module init (alongside the existing
night `classList.add`). **Anti-flash (critique P3):** `index.html` inlines `--screen-brightness`
and the `.night` class on `<html>` from `localStorage` in a tiny pre-paint script, so first paint
is correct with no full-brightness flash.

### 7.6 Mode-change legibility (critique B5 resolved)
Toggling NIGHT jumps to the remembered night brightness. To make the jump read as intentional,
the `.dim-content` filter and `.dim-scrim` opacity transition `0.25s ease`, and a brief
non-blocking pill in `.overlay-top` announces "Night · 45%" for 1.2s (also `aria-live="polite"`).

---

## 8. Night-vision LUT — corrected luminance ladder

### 8.1 The fix: alarm = brightest (critiques A1/A2/A3/A4/P0 — all three reviewers agree)
The original "good=brightest, bad=darkest" is **inverted** and dangerous on emitted light: the
eye is drawn to the brightest pixel, so the alarm must be brightest. The measured contrast also
proved the original night `--bad #7e1d1d` = **2.04:1** (unreadable as text, and it is routed into
`Stat`/`btn-danger`/polar error). Both problems are fixed by flipping the ladder so **bad is the
brightest, most-saturated rung; good is the calm, dimmest rung** — all three stay deep-red and
all three clear 4.5:1 as text. Non-color **shape + glyph + blink** (§9) remain the primary
differentiator; the ladder is the secondary cue.

### 8.2 Separate "status severity" from "interactive danger" (critique A4 resolved)
`--bad` is overloaded today (status fault AND `btn-danger` chrome). Split them so the destructive
buttons stay legible regardless of ladder:

```css
:root {
  --danger-ink: var(--bad);   /* day: same */
}
:root.night {
  --danger-ink: #ff8f8f;      /* button text/border: bright readable red, decoupled from status */
}
.btn-danger { border-color: color-mix(in srgb, var(--danger-ink) 45%, transparent); color: var(--danger-ink); }
```

### 8.3 Night status tokens (re-derived, measured ≥4.5:1, deep-red band)

```css
:root.night {
  --bg:        #0a0303;
  --bg-raise:  #140606;
  --bg-panel:  #170808dd;
  --line:      #361010;
  --line-bright:#571a1a;
  --accent:    #ff5d5d;   /* interactive red, 5.93:1 on --bg */
  --accent-dim:#a03535;   /* borders/large-text ONLY (3.x:1) */
  --glow:      #ff5d5d44;

  /* SEVERITY LADDER — alarm is BRIGHTEST/most-saturated; good is the calm dim rung.
     All three are deep-red (hue-safe for dark adaptation) and ALL ≥4.5:1 as text. */
  --bad:  #ff7070;   /* brightest + high-sat — grabs the eye; the alarm. ~7:1 on --bg */
  --warn: #e0905c;   /* mid rung; kept as red-leaning amber but verify ≥4.5:1 and minimal
                        orange — differentiate primarily by SHAPE/BLINK, not hue (critique A3) */
  --good: #b86a6a;   /* dim, low-sat red — "fine, ignore me". ~4.6:1 on --bg */
}
```

Implementer gate: measure all three on both `--bg` and `--bg-panel`; if `--warn` cannot reach
4.5:1 without shifting toward orange, **lower its luminance and lean on the halo shape** rather
than shifting hue (critique A3 — orange destroys scotopic adaptation). The shape/glyph system
(§9) is what actually disambiguates; color is reinforcement.

### 8.4 Night image filter — preserve focus legibility (critique A3 — single most important)

```css
:root.night {
  /* OLD (rejected): grayscale(1) sepia(1) saturate(6) hue-rotate(-18deg) brightness(0.55)
     — annihilated star-shape/HFR legibility. */
  --img-filter: sepia(0.55) saturate(1.6) hue-rotate(-12deg) brightness(0.9) contrast(1.05);
}
```

Gentle red tint, **luminance and contrast preserved**, so a defocused vs sharp star still differ.
The `.astro` image is **not** over-saturated into a single hue. This is the correctness keystone:
focus judgment must survive night mode (panel review P0).

### 8.5 Token reference (final, both modes — verify hex before commit)

| Token | Day | Night | Notes |
|---|---|---|---|
| `--text` | `#c8d4e8` | `#e08a8a` | |
| `--text-dim` | `#97a6c2` | `#cc7676` | raised for AA both modes (measure ≥4.5:1) |
| `--text-faint` | `#5d6c85` | `#6e2a2a` | decoration only |
| `--good` | `#4ade80` | `#b86a6a` | night = dim calm rung |
| `--warn` | `#fbbf24` | `#e0905c` | night = mid rung (shape-differentiated) |
| `--bad` | `#f87171` | `#ff7070` | night = **brightest** = alarm |
| `--danger-ink` | `=--bad` | `#ff8f8f` | button danger chrome, decoupled |
| `--accent` | `#7dd3fc` | `#ff5d5d` | |
| `--accent-dim` | `#38709c` | `#a03535` | borders/large-text only |
| `--screen-brightness` | day mem | `0.45` default | floor 0.08; on `:root`, applied to `.dim-content` |
| `--img-filter` | `none` | luminance-preserving (§8.4) | |

---

## 9. Non-color state cues — `Led` + `Stat` (`ui.tsx`)

### 9.1 `Led` — distinct silhouette per state, survives reduced-motion + grayscale (critiques P1/A1)
Size bumped to **11px** for shape discrimination (critique P1: 9px too small).

```tsx
export function Led({ state, label }: { state: LedState; label?: string }): JSX.Element;
```

Shapes (CSS-LED) — each state is a **distinct silhouette**, not just color/animation:

| state | silhouette | color | motion |
|---|---|---|---|
| `off` | flat low-contrast **dash** (`–`), not a ring | `--text-faint` | none |
| `on` | filled **circle** | `--good` | none |
| `warn` | filled circle + **halo ring** (outline-offset) | `--warn` | none |
| `bad` | filled **square** | `--bad` (brightest) | blink (reduced-motion → static square + double ring) |
| `busy` | **half-filled circle** (left half) | `--accent` | slow sweep (reduced-motion → static half-circle) |

```css
/* CSS-LED */
.led        { position: relative; width: 11px; height: 11px; border-radius: 50%; }
.led-off    { width: 11px; height: 2px; border-radius: 1px; background: var(--text-faint); align-self: center; }
.led-on     { background: var(--good); box-shadow: 0 0 6px var(--good); }
.led-warn   { background: var(--warn); box-shadow: 0 0 6px var(--warn);
              outline: 1.5px solid var(--warn); outline-offset: 2px; }
.led-bad    { background: var(--bad); border-radius: 1px;            /* SQUARE */
              box-shadow: 0 0 9px var(--bad); animation: pulse-glow 1.0s ease-in-out infinite; }
.led-busy   { background: var(--accent);
              clip-path: inset(0 50% 0 0);                            /* HALF-FILLED */
              animation: led-sweep 1.4s linear infinite; }
@media (prefers-reduced-motion: reduce) {
  .led-bad  { animation: none; outline: 1.5px solid var(--bad); outline-offset: 2px; } /* static double cue */
  .led-busy { animation: none; }
}
```
- `off` is a **dash**, distinct from the `warn` halo-ring and `busy` half-circle (critique P1:
  hollow-dot vs haloed-dot were confusable).
- `bad`/`busy` keep a **static distinct silhouette under reduced-motion** so motion-off users
  still get the state (critique P1/P3: don't just turn the signal off).

**Migration (critiques G1/F2/P1 — no permanent union shim):** Do a **single mechanical sweep** in
one PR. `Led` is strictly typed `{ state: LedState }`. Codemod the call sites:
- `<Led on={x} />` → `<Led state={x ? "on" : "off"} />`
- `<Led on warn={p} />` (paused-seq LED, App.tsx:114) → `<Led state={p ? "warn" : "busy"} />`
  (note: the running-sequence LED becomes `busy`, paused becomes `warn` — the old boolean API
  could not express `busy`, which is exactly why the union shim silently kept wrong semantics).
- ConnectView ×9, App.tsx ×3, GuideView. If staging is unavoidable, export a separate
  `LedLegacy` (old boolean props) so the new `Led` keeps exhaustiveness on `LedState` — never a
  union prop that lets a typo fall through to `led-off` (critique P1).

### 9.2 NINA-link health LED (critique E4 resolved)
This surface owns `Led`, so it defines the panel-review-requested separate "backend↔NINA" LED:
- Data: `status.mode === "nina"` → link healthy; if mode was `nina` and a `log` error with
  `source` indicating NINA arrives, → `bad`.
- Placement: ConnectView NINA Bridge panel header and (future) Monitor view.
- States: `state="on"` (bridged & healthy), `state="bad"` (bridge error), `state="off"` (not
  bridged). Browser↔backend LINK LED in the header stays separate (`wsConnected`).

### 9.3 `Stat` — tone glyph, warn/bad only, never clips (critiques D/G3/P1 resolved)

```tsx
export function Stat({ label, value, unit, tone, hint, glyph = true }: {
  label: string; value: string | number; unit?: string;
  tone?: Tone; hint?: string; glyph?: boolean;
}): JSX.Element;
```

Rules:
- Glyph renders **only for `warn`/`bad`, never `good`** (critique D2: a check on every nominal
  value is noise ASIAIR doesn't have).
- **Distinct glyph per tone** (critique P1: don't alias warn=bad): `warn` → `alert` triangle;
  `bad` → `x`. Explicit switch, not a nested ternary.
- Glyph is placed in a **non-flowing leading slot that does not consume the value's truncation
  budget** (critique D1): the value `<span>` keeps `truncate`; the glyph sits in a separate
  `shrink-0` span before it, so `RMS 0.42"` never clips its last digit.
- `glyph={false}` opt-out for huge readouts. **PolarView TOTAL passes `glyph={false}`** (critiques
  D2/G3): the `text-4xl` + color is already unmistakable, and during alignment every value is
  legitimately "out of tolerance" — a wall of triangles cries wolf. Tone color stays; glyph off.
- `hint` wraps the value in a `Tooltip` (§10) — where "HFR: lower=sharper, 1.5–3px good" lives.
- Loading/`null` → renders `—` in `--text-faint`, no glyph, never throws.

---

## 10. `Tooltip`, `InfoDot`, `Confirm` primitives (`ui.tsx`)

### 10.1 `Tooltip` (critique P2 — viewport clamp, 44px hit area)

```tsx
export function Tooltip({ content, children, side = "top" }: {
  content: ReactNode; children: ReactNode; side?: "top"|"bottom"|"left"|"right";
}): JSX.Element;
```
- Trigger wrapped in focusable `<span tabIndex={0}>` with `aria-describedby`; opens on
  **hover + focus + tap**; closes on outside-tap / `Escape`.
- Bubble: `.panel`-styled, `role="tooltip"`, `z-50`, **`max-width: min(240px, 90vw)`** with a
  viewport-edge guard so it never clips on a 375px phone (critique P2). On `sm:` (phone) it may
  present as a bottom-anchored sheet rather than a side bubble.

### 10.2 `InfoDot` (critique P1 — ≥44px hit area)
```tsx
export function InfoDot({ label, content }: { label?: string; content: ReactNode }): JSX.Element;
```
Renders a 14px `info` Icon inside a `Tooltip`, but the focusable span has **padding to a ≥44px
hit area** (icon stays 14px visually). Drop next to jargon labels: HFR, OFFSET, STEP SIZE, DITHER.

### 10.3 `Confirm` — correctly scoped (critiques B/C — the biggest safety fix)

**Taxonomy split (critiques B1/C1/C2/A — all three reviewers):**
- **Hold-to-confirm (`Confirm`)** ONLY for slow/irreversible, non-urgent destructive actions:
  **Disconnect All, sequence Abort, Park-while-imaging, delete target/step**.
- **Single-tap immediate** for safety motion stops: **mount STOP, focuser HALT, polar STOP**,
  and the slew-pad release. These are themselves the "undo"; adding a 700ms delay while a mount
  drives into the pier is a hazard. They keep their current single-tap `onClick`.

```tsx
export function Confirm({ onConfirm, label, holdMs = 700, children }: {
  onConfirm: () => void;
  label: string;             // a11y/announce text, e.g. "Abort sequence" (required)
  holdMs?: number;
  children: (bind: HoldBind) => ReactNode;
}): JSX.Element;
type HoldBind = {
  onPointerDown; onPointerUp; onKeyDown; onKeyUp;
  progress: number;   // 0..1 fill
  armed: boolean;
  hintLabel: string;  // persistent "HOLD TO …" text (critique C4/P1 discoverability)
};
```

Behavior (critiques B3/B4/C3/C4 resolved):
- **`setPointerCapture` on pointerdown** so finger drift / glove jitter does NOT cancel the hold
  (critique B3/C2). No `onPointerLeave` cancel. Release before 100% cancels.
- Fill overlay (uses `--danger-ink`) is the **non-color** progress signal.
- **Persistent affordance:** the resting button label already reads the hold requirement
  (`hintLabel` e.g. "HOLD TO ABORT") so a first-timer never taps-and-wonders (critique C4/P1).
- **Haptics:** `navigator.vibrate?.(30)` on confirm-fire (critique B4).
- **Keyboard path = honest two-step:** `Enter`/`Space` arms; label swaps to
  **"PRESS ENTER AGAIN TO ABORT"** (NOT "hold" — keyboard can't hold; critique C3/P1) with
  `aria-live="assertive"`; second press within a **generous 3s** confirms; `Escape` disarms.
  Same visible armed state for pointer and keyboard.

Usage (Abort):
```tsx
<Confirm onConfirm={abort} label="Abort sequence">
  {(b) => (
    <button {...b} className="btn btn-danger relative overflow-hidden">
      <span className="absolute inset-y-0 left-0" style={{ width: `${b.progress*100}%`, background: "color-mix(in srgb, var(--danger-ink) 25%, transparent)" }} />
      <Icon name="stop" size={14}/> {b.armed ? "PRESS AGAIN" : b.hintLabel}
    </button>
  )}
</Confirm>
```

---

## 11. Graphs — `graphs.tsx` (GRAPHS)

Critiques F1/G2/P2/E1 resolved.

1. **Numeric labels are data → `--text-dim`** (not `--text-faint`). Only dashed gridline strokes
   use `--text-faint`. Night `--text-faint #6e2a2a` (~1.7:1) is fine for a gridline stroke, never
   for a number.
2. **All graph text moves to an absolutely-positioned HTML overlay** at `text-[11px] mono`, for
   `VCurve`, `GuideGraph`, and any stretched SVG — a fixed viewBox `fontSize` cannot guarantee a
   px floor across variable container widths (critique P2). Wrap each `<svg>` in a `relative`
   container; render labels as HTML `<div className="absolute …">`. The implementer computes the
   same scale math the SVG uses to pin the `+2"` tick and V-curve endpoints to their data
   positions (critique G2: the overlay must duplicate the scale, not float).
3. **Stop using `preserveAspectRatio="none"` for data graphs** (critique G2). `GuideGraph` and
   `Histogram` switch to a normal viewBox + `vector-effect="non-scaling-stroke"` so stroke width
   stays crisp without distorting the data; with no `none`, the HTML-overlay tick label stays
   aligned to the (now undistorted) gridline.
4. **Aspect-lock for fill-grow (critique P2):** when the V-curve / reticle panel grows (§12), the
   SVG is centered at a locked aspect ratio (`max-width`/`max-height` + `margin:auto`), never
   non-uniformly stretched — a stretched V-curve makes the focus minimum look asymmetric
   (misleading) and a stretched polar reticle breaks the circular AZ/ALT geometry.
5. `GuideGraph` RA/DEC legend stays `--accent` / `--warn`; `Histogram` stays `--accent` (reds-out
   at night automatically).

---

## 12. Layout: dead-space fill without stretching instruments

Critiques E1/P2 resolved. The ~40% void (`ui-focus.png`, `ui-power.png`) is fixed **without**
wrapping a giant empty bordered box around emptiness.

```css
/* CSS-FLEX */
.fill-col  { display:flex; flex-direction:column; min-height:100%; }
.fill-grow { flex:1 1 auto; min-height:0; }
```

Rules:
1. `App.tsx` main inner wrapper: `view-enter max-w-[1500px] mx-auto w-full min-h-full flex flex-col`.
2. The **primary panel grows** (`fill-grow`), but its instrument SVG is **aspect-locked and
   centered** (§11.4), not stretched. The freed space is filled with **useful secondary content
   or a properly-scaled empty state**, never a stretched instrument (critique E1/P2):
   - FocusView: the grown V-curve panel hosts the centered V-curve **and** (per panel-review IA)
     space reserved for a live-preview strip the Live-preview surface fills; until then, a
     scaled `EmptyState`.
   - PolarView: reticle centered at locked aspect; control rail stays intrinsic.
   - PowerView/ConnectView: device-list panel grows with `content-start`; remaining space breathes
     on the dark background rather than a 700px-tall empty bordered box (critique E1).
3. This is additive flex classing + the EmptyState component; no component API changes.

---

## 13. Polish: focus rings, starfield, motion, empty states, brackets

### 13.1 Focus-visible rings (critiques P1/F3/P3 resolved) — CSS-FOCUS
```css
/* higher-than-:where specificity so Tailwind's outline-none utilities don't win (critique P1) */
button:focus-visible, a:focus-visible, input:focus-visible,
select:focus-visible, textarea:focus-visible, [tabindex]:focus-visible {
  outline: 2px solid var(--accent);    /* solid ring is the cue; halo is bonus (critique F3) */
  outline-offset: 2px;
}
button:focus:not(:focus-visible), a:focus:not(:focus-visible),
input:focus:not(:focus-visible), select:focus:not(:focus-visible),
textarea:focus:not(:focus-visible), [tabindex]:focus:not(:focus-visible) { outline: none; }
.field:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; } /* ring only; drop the border swap to avoid double cue (critique P1) */
```
- Remove the unconditional `.field { outline:none }`; replace with the scoped reset above.
- Rely on the **solid 2px outline** as the indicator (do not count the dimmed `--glow` halo as
  the cue; critique F3). The outline must meet WCAG 2.4.11 (≥3:1) — night `--accent` is 5.93:1;
  verify it stays ≥3:1 after the §7 dimmer at floor (the scrim sits above content, so the
  outline dims with content but the ratio is preserved; if it falls below 3:1 at 0.08, the scrim
  is capped so the focused control's ring stays visible).
- **One cue per field** (ring, not ring+border; critique P1).

### 13.2 Dimmed night starfield — inside the dimmed subtree (critiques A/P1/B-double-dim)
The starfield must dim **with** the content, not stay relatively bright. So it moves **inside
`.dim-content`** (e.g. on a `.dim-content::before` or the content background), NOT on `body`
behind `#root`. At brightness 0.08 the stars dim along with the UI — decoration never out-shines
data (critique P1/F1/G4).

```css
/* CSS-STARS — rendered on .dim-content background so it dims with the UI */
:root.night .dim-content {
  background-image:
    radial-gradient(1px 1px at 12% 28%, #ff9b9b10 50%, transparent 50%),
    radial-gradient(1px 1px at 78% 12%, #ff9b9b0c 50%, transparent 50%),
    radial-gradient(1.5px 1.5px at 42% 64%, #ff9b9b0a 50%, transparent 50%),
    radial-gradient(1px 1px at 91% 81%, #ff9b9b0c 50%, transparent 50%),
    radial-gradient(1px 1px at 23% 88%, #ff9b9b08 50%, transparent 50%),
    radial-gradient(ellipse 1200px 800px at 70% -10%, #2a0a0a44, transparent);
}
```

### 13.3 Motion + reduced-motion (critiques P3/A) — CSS-MOTION
```css
@keyframes view-fade { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }
.view-enter { animation: view-fade 0.22s ease both; }   /* NO scale() — avoids text re-raster shimmer (critique P3) */
@media (prefers-reduced-motion: reduce) {
  .view-enter, .blink, .led-bad, .led-busy, .empty-ghost { animation: none !important; }
}
```
Keep the existing `<main key={view}>` keyed remount; the fade reads as a clean cross-fade over the
persistent dark bg with no double render. **Reduced-motion users still get the state** because
`led-bad`/`led-busy` carry a static silhouette (§9.1) and the running-sequence nav badge gets a
static ring instead of blink.

### 13.4 Empty states — static, container-scaled (critiques E2/E3) — UI-EMPTY / CSS-EMPTY
```tsx
export function EmptyState({ icon, title, hint, action, size = "hero" }: {
  icon: IconName; title: string; hint?: string; action?: ReactNode; size?: "hero" | "inline";
}): JSX.Element;
```
- **`size="hero"`** (default): centered 56px ghost icon + title + hint + optional action; fills a
  view panel.
- **`size="inline"`**: one-line muted note (small icon + title) for tight containers like the
  **log drawer** "no events yet" — a 56px hero illustration would dominate a 340px drawer
  (critique E3).
- **Static ghost icon, NO pulse** (critique E2: a pulse implies loading/activity where there is
  none). The `.empty-ghost` class sets low opacity only, no animation.
- Mappings: FocusView V-curve → `hero` (`icon="focus"`, action Run Autofocus). ConnectView no-scan
  → `hero` (`icon="refresh"`). Log drawer → `inline`. Guide/Plan empty lists → `hero`.

### 13.5 Bracket motif (critique P3) — make it 4 corners
The panel review flagged the 2-corner `.panel::before/::after` as asymmetric/incomplete. This
"polish" surface resolves it: extend to **4 corners** (add two more pseudo-elements via a wrapper
or box-shadow technique, since an element has only `::before`/`::after`). Implement with a single
`.panel` background using four `linear-gradient` corner marks (no extra DOM), e.g. layered
`background-image` corner brackets in `--accent-dim`.

---

## 14. UI states matrix + 375px-phone / night notes

### 14.1 Per-primitive states
- **Led:** `off`(dash) / `on`(circle) / `warn`(haloed circle) / `bad`(square, blink→static) /
  `busy`(half-circle, sweep→static). Each distinct in grayscale at 11px.
- **Stat:** value present (mono, glyph for warn/bad only, non-clipping) / `—` placeholder
  (`--text-faint`, no glyph) when null/loading. `glyph={false}` for hero readouts.
- **Confirm:** idle (persistent "HOLD TO …" label) → arming (progress 0..1, `aria-live` polite) →
  confirmed (fires once + vibrate, resets) → cancelled (release/`Escape`, focus retained).
  Disabled passes through.
- **Tooltip/InfoDot:** closed / hover-open / focus-open / tap-open; `Escape`+outside-tap close;
  viewport-clamped; ≥44px hit area.
- **EmptyState:** static; `hero` vs `inline` per container.
- **Dimmer:** 0.08–1.0; numeric % + `aria-valuetext`; reset always reachable in `.overlay-top`;
  steppers ≥44px.
- **Lock overlay:** off / on (full-screen `.overlay-top` scrim with an "unlock" slide/long-press;
  toasts and critical alerts still render above it; long-press also restores brightness).
- **Toast queue:** 0..4 visible; error = sticky until dismissed; info/warn auto-dismiss (5s);
  LOG badge shows `unseenErrors`, cleared on drawer open.

### 14.2 375px phone, red night mode (the hard case)
- **Header** collapses (existing `hidden sm:`): logo + dimmer (steppers primary, thin slider
  secondary) + % + NIGHT toggle (icon+label) + LOCK + LINK Led. Mount/temp readouts stay
  `hidden md:`.
- **Bottom nav** (`sm:hidden`): `Icon size={20}` + `text-[10px]` labels (up from 8px); each
  `<button>` `min-h-[52px]` to clear 44px; labels `--text-dim` (AA). 8 items × ~46px fits 375px;
  icons give a non-text cue if labels truncate. Icon-only states (e.g. the moon/sun toggle if a
  label hides on phone) carry `aria-label`.
- **Night LUT:** bg `#0a0303` with dimmed red starfield inside `.dim-content`; status reds are the
  corrected ladder — alarm (`bad`) is the brightest square + blink, surviving on a cheap phone
  OLED at floor brightness.
- **Confirm** hold uses `setPointerCapture` (glove-jitter safe) + `navigator.vibrate`; the fill is
  the feedback (no hover on phone). **STOP/HALT stay single-tap** (safety).
- **Tooltip/InfoDot** tap-open, viewport-clamped, ≥44px hit; bottom-sheet presentation on phone.
- **Dimmer** floor 0.08 + scrim → near-black for dark sites; reset reachable via overlay control,
  `Shift+B` (if BT keyboard), or long-press on lock overlay.
- Focus rings appear only with a BT keyboard (`:focus-visible`); cross-fade/blink honor
  `prefers-reduced-motion`.

---

## 15. Build order (each step compiles independently)

1. **`icons.tsx`** (no deps) — incl. directional + status glyphs; pass the 20px grayscale
   distinct-silhouette gate.
2. **`index.css`** — CSS-TOKENS (measured), CSS-FOCUS, CSS-DIMMER (scrim, no `#root` filter),
   CSS-LED (11px shapes), CSS-STARS (inside `.dim-content`), CSS-MOTION (no scale, reduced-motion),
   CSS-FLEX, CSS-EMPTY, 4-corner brackets. (no deps)
3. **`index.html`** — pre-paint inline `--screen-brightness` + `.night` (anti-flash).
4. **`store.ts`** — STORE-DISPLAY (brightness/night/lock + clamp-on-read + applyBrightness init),
   STORE-TOAST (queue + auto-dismiss + unseenErrors; `showToast` alias).
5. **`ui.tsx`** — UI-LED (`{state}` only, no union), UI-STAT (warn/bad glyph, non-clip,
   `glyph` opt-out), UI-FIELD (`hint`), UI-CONFIRM (hold + capture + vibrate + honest keyboard),
   UI-TOOLTIP/UI-EMPTY (InfoDot, EmptyState hero/inline).
6. **`App.tsx`** — APP-HEADER (dimmer cluster, single mode icon, lock), APP-NAV (icons + IA
   reorder Rig→Align→Mount→Focus→Capture→Guide→Plan→Power), APP-LAYOUT (`.dim-content` wrapper +
   flex-stretch + view key), APP-OVERLAYS (`.dim-scrim` + `.overlay-top` toasts/lock/reset
   outside dimmed subtree; LOG badge).
7. **`graphs.tsx` + `polar.tsx`** — HTML label overlay, drop `preserveAspectRatio="none"`, data
   numbers `--text-dim`, aspect-lock for fill-grow.
8. **views sweep** — unicode→Icon (directional via sign-mapped arrow glyphs, NOT `play`);
   `Led state=` sweep (single PR); `Confirm` on Disconnect/Abort/delete ONLY; STOP/HALT/polar-STOP
   stay single-tap; `setPointerCapture` on MountView jog; EmptyState; InfoDot on jargon labels;
   PolarView TOTAL `glyph={false}`; NINA-link LED wired.

The IA reorder in step 6 (Align before Mount) matches the panel review's session-flow fix.

---

## 16. Critique resolution log

### Accepted and fixed
- **Inverted luminance ladder (A1/A2/P0, all 3 reviewers):** flipped — `--bad` is now the
  brightest/most-saturated rung (the alarm), `--good` the calm dim rung. Re-derived all night
  status/text colors from **measured** contrast (old `--bad #7e1d1d`=2.04:1, night
  `--text-dim #a85252`=3.92:1 — both failed). §8, §5.
- **Status vs interactive-danger overload (A4):** split `--danger-ink` from `--bad` so
  destructive buttons stay legible regardless of ladder. §8.2.
- **Orange warn destroys dark adaptation (A3):** warn differentiates by **shape/blink**, not hue;
  implementer gate caps orange shift. §8.3.
- **Night image filter kills focus legibility (A3, "single most important"):** rebuilt
  `--img-filter` to preserve luminance/contrast (no saturate(6)/brightness(0.55)). §8.4.
- **`filter:brightness` hazards — dims toast, traps user, breaks fixed/backdrop (B1–B5/F1/F3):**
  replaced with overlay-scrim dimmer; toasts/reset/lock live in an undimmed `.overlay-top`
  sibling; floor lowered to 0.08; always-reachable reset + `Shift+B` + long-press; numeric % +
  `aria-valuetext`; mode-change transition + announce. §7.
- **Hold-to-confirm on STOP/HALT (B1/C1/C2, all 3):** taxonomy split — emergency motion stops are
  single-tap-immediate; `Confirm` only for Disconnect/Abort/Park/delete. §10.3.
- **Confirm field failures (B3/B4/C3/C4):** `setPointerCapture` (not `onPointerLeave`), haptics,
  persistent "HOLD TO …" affordance, honest keyboard two-step ("PRESS AGAIN", generous 3s). §10.3.
- **Directional arrows lost in unicode→Icon sweep (C, 2 reviewers):** added 4 directional glyphs;
  polar AZ/ALT + slew pad map by **sign**, never through `play`. §4.
- **Led shim type-unsound / silently keeps old semantics (G1/F2/P1):** removed the union shim;
  single mechanical sweep, strict `{state}` type, `LedLegacy` if staged. §9.1.
- **Led shapes confusable / collapse under reduced-motion (P1):** 11px; `off`=dash, `busy`=half-
  circle, distinct static silhouettes for `bad`/`busy` when motion off. §9.1.
- **Stat glyph clutter/clip/alias (D1/D2/G3/P1):** glyph on warn/bad only, distinct per tone,
  non-flowing slot (no clip), `glyph={false}` on polar TOTAL. §9.3.
- **Graph numbers are data not decoration (F1/P2):** numbers stay `--text-dim`; HTML overlay for
  all graphs; drop `preserveAspectRatio="none"`. §11.
- **fill-grow stretches/distorts instruments (E1/P2):** panel grows but SVG aspect-locked +
  centered; freed space gets useful content / scaled empty state, not a stretched instrument /
  empty bordered box. §12.
- **Empty-state pulse implies loading; hero too big for drawer (E2/E3):** static ghost; `hero`
  vs `inline`. §13.4.
- **Starfield double-dim reversal (G4/P1/F1):** moved inside `.dim-content` so it dims with the
  UI. §13.2.
- **Touch targets — slider thumb / InfoDot / Tooltip clip (P1/P2):** ≥44px hit areas + steppers;
  Tooltip `min(240px,90vw)` + edge guard. §7.4, §10.1–2.
- **Toast single-overwrite + dimmed (B3/H1):** folded in toast queue + auto-dismiss + LOG badge.
  §3.2, §14.1.
- **Screen lock / touch guard (E1/E-lock):** added to header cluster + overlay. §7.4, §14.
- **NINA-link health LED undefined (E4):** defined its states/placement. §9.2.
- **Misc (P2/P3):** accent-dim restricted to borders/large-text (3.78:1 fails as text);
  focus-ring specificity above Tailwind `outline-none`; one focus cue per field; no `scale()` in
  view-fade; slider track ≥3:1 outline; anti-flash pre-paint; 4-corner brackets; reduced-motion
  keeps state via static silhouettes.

### Rejected (with reason)
- **"Use Lucide dependency instead of hand-rolled icons" (D1):** rejected the dependency, accepted
  the substance. Inline SVG keeps `currentColor` (night/dim/focus for free), zero bundle, and lets
  us tune astro glyphs. We adopt recognizable silhouettes for common actions and a grayscale
  distinct-silhouette test gate for nav, which addresses the real concern (recognizability).
- **Auto-night-at-dusk / geolocation dimming (E3):** out of scope; belongs to Settings/safety
  surfaces. We make **no parity claim** here, so this is a clean deferral, not a gap.
- **Server-side preference sync now (`/api/preferences`):** deferred. Building it here would add a
  backend dependency to a pure-display surface and couple it to the unbuilt Settings surface.
  `localStorage` is the source of truth; the endpoint shape is documented for the future owner. §6.
- **Per-image preview brightness control built in this surface (A3/A4 secondary):** the
  *requirement* is accepted (preview must stay focus-legible; we fixed the filter and forbid
  double-dimming), but the per-image stretch UI is owned by the Live-preview surface. We document
  the contract and the coordination note rather than build a second stretch control here.

---

## 17. Implementation checklist

- [ ] `icons.tsx`: nav/action/directional/status glyphs; status dots use real `<circle>`;
      distinct-silhouette gate passed at 20px grayscale.
- [ ] `index.css` CSS-TOKENS: day + night text/status re-derived; **every text/label/data token
      measured ≥4.5:1** (tool-verified); `--text-faint` decoration-only; `--accent-dim`
      borders/large-text-only; `--danger-ink` split from `--bad`.
- [ ] `index.css` CSS-DIMMER: `.dim-content` filter + `.dim-scrim` (opacity tracks brightness),
      no `filter` on `#root`.
- [ ] `index.css` CSS-LED: 11px; off=dash, on=circle, warn=halo, bad=square(blink→static),
      busy=half(sweep→static); reduced-motion keeps static silhouettes.
- [ ] `index.css` CSS-FOCUS: scoped `:focus-visible` ring above Tailwind specificity; remove
      blanket `.field{outline:none}`; one cue per field; ≥3:1 verified at floor brightness.
- [ ] `index.css` CSS-STARS inside `.dim-content`; CSS-MOTION no-scale + reduced-motion;
      CSS-FLEX; CSS-EMPTY (no pulse); 4-corner brackets.
- [ ] `index.html`: pre-paint inline `--screen-brightness` + `.night`.
- [ ] `store.ts` STORE-DISPLAY: brightness (clamp on read+write, floor 0.08), day/night memory,
      `toggleNight`, `resetBrightness`, `setLocked`; `applyBrightness` init.
- [ ] `store.ts` STORE-TOAST: `toasts` queue (≤4, errors sticky, info/warn 5s auto-dismiss),
      `unseenErrors`, `pushToast`/`dismissToast`/`clearUnseen`; `showToast` alias kept.
- [ ] `types.ts`: `LedState`, `Tone`, `ToastItem` (additive).
- [ ] `ui.tsx` UI-LED: strict `{state}`; single-PR call-site sweep; NINA-link LED states.
- [ ] `ui.tsx` UI-STAT: warn/bad glyph only, distinct per tone, non-clipping, `glyph` opt-out,
      `hint`→Tooltip, `—` placeholder.
- [ ] `ui.tsx` UI-FIELD `hint`; UI-CONFIRM (hold + setPointerCapture + vibrate + persistent
      affordance + honest keyboard two-step); UI-TOOLTIP (viewport clamp, 44px); InfoDot;
      EmptyState hero/inline static.
- [ ] `App.tsx`: header dimmer cluster (% + steppers + single mode icon + lock); nav icons + IA
      reorder; `.dim-content` wrapper + flex-stretch + view key; `.dim-scrim` + `.overlay-top`
      (toasts/lock/reset undimmed); LOG badge; `Shift+B` reset.
- [ ] `graphs.tsx`/`polar.tsx`: HTML label overlay; drop `preserveAspectRatio="none"`; data
      numbers `--text-dim`; aspect-lock for fill-grow.
- [ ] views sweep: unicode→Icon; directional via sign-mapped arrows; `Led state=`; `Confirm` on
      Disconnect/Abort/delete ONLY; STOP/HALT/polar-STOP single-tap; MountView jog
      `setPointerCapture`; EmptyState; InfoDot on HFR/OFFSET/STEP/DITHER; PolarView TOTAL
      `glyph={false}`.
- [ ] Verify on 375px night-mode: nav ≥44px, alarm (bad) is the brightest/most-salient, focus
      legible in preview, dimmer reaches near-black + reset reachable, tooltips don't clip,
      reduced-motion still conveys state.
