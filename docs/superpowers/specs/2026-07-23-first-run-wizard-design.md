# NOV-2 — Guided first-run + first-light walkthrough (novice) + F-G coach-mark foundation

**Date:** 2026-07-23
**Feature:** NOV-2 (linear first-run wizard) building foundation **F-G** (reusable coach-mark / spotlight primitive + `hasSeen` persistence)
**Status:** build-ready
**Governing spec:** `docs/superpowers/specs/2026-06-15-ux-onboarding-safety-ux-design.md` (the onboarding/pre-flight P0 cluster — **already shipped**; see Surprises)

---

## 1. Design

### 1.1 Goal

One linear, resumable flow that walks a first-timer from a blank slate to first light:

> **set location → connect a rig (or Simulator) → save a profile → pick a target → cool the camera → take your first frame**

with first-time coach marks. The building blocks all exist and are strong, but nothing chains them and there is no coach/tour/`hasSeen` primitive. A first-timer facing the Equipment view's per-role driver dropdowns, a separate Settings→Connect→Observing-Site panel, a separate Profiles panel, an Atlas, and a Capture view — with no thread between them — is exactly who quits before connecting a camera.

The deliverable is two things:

- **F-G (foundation):** a reusable **`CoachMark`** spotlight component + a **`hasSeen`** store slice (persisted). Reusable by any future one-time hint.
- **NOV-2 (the wizard):** a **pure step-state machine** (`computeWizard`) + a thin, non-modal **`FirstRunWizard`** overlay that deep-links to the existing panels and **auto-advances as each step's precondition is satisfied by live store state** — it never re-implements connect/site/capture, it points at them and watches.

The pure, tested core is `computeWizard(snapshot, manualId)` — which step is active, what's done, applicability of the optional "cool" step, manual back/next override. Everything else is a thin render verified by the typechecker.

### 1.2 Current-state seams (real file:line, all read)

**The onboarding P0 cluster is already built** — so NOV-2 does *not* rebuild pre-flight/interstitials/confirm dialogs; it reuses them.

- **Governing spec** `docs/superpowers/specs/2026-06-15-ux-onboarding-safety-ux-design.md` — §1 primitives, §2 pre-flight, §7 interstitial + nav gating. Shipped: `PreflightStrip.tsx`, `PreflightModal.tsx`, `Checklist.tsx`, `ConfirmDialog.tsx`, `NotConnectedInterstitial.tsx`, `lib/horizon.ts`, `lib/preflight.ts`, `HoldButton` (in `ui.tsx`), store `site`/`equipConnected`/`confirm` slices.
- **No coach/tour/hasSeen/spotlight/first-run-wizard exists.** Grep over `ui/src` for `coach|tour|onboard|hasSeen|spotlight|walkthrough|wizard|firstRun`: the only hits are (a) **auth** `first_run` (`views/Login.tsx:42`, `lib/caps.ts:165`, `lib/connection.ts:70`) — the create-admin gate, unrelated; (b) `FRAME_COACH` (`lib/calibration.ts:16`) — static copy strings under the Capture frame-type selector, not a coach-mark; (c) `PolarView.tsx:16,118` "phase wizard" — a polar-alignment phase machine, unrelated. **F-G is genuinely missing.**
- **Site editor** `components/settings/SitePanel.tsx` — the ONE place the site is set. Default-site warning banner keys on `config?.site?.is_default` (`SitePanel.tsx:408-416`); `buildSite()` defaults name `"My Observatory"` / `is_default:true` (`SitePanel.tsx:176-184`). It lives on **Settings → Connect** tab (`SettingsView.tsx:166`), which is `SettingsView`'s default tab (`SettingsView.tsx:53`) — so `setView("settings")` shows it with no tab plumbing.
- **Equipment / role assignment + connect flows** `views/EquipmentView.tsx` — `connectAssignments()` (the one connect impl, `:128`), `doConnect` (`:171`), `doSimRig` (builds an all-sim map → connect, `:191`), `doDetectHardware` (`:204`), Profiles panel `doSaveProfile` (`:235`), and the first-run "No equipment yet — Detect hardware rig / Simulator rig" copy (`:406-411`). The wizard **routes to this view (`setView("connect")`) and watches `equipConnected`**; it does not call connect itself.
- **Profiles** `components/settings/ProfileList.tsx` — capture (`onCapture :274`), "No profiles yet" empty state (`:339`). `api/backends.listProfiles()` gives the count signal.
- **Camera cool + first frame** `views/CaptureView.tsx` — cool posts `/api/camera/cooler {on,target_c}` (`:686`), gated on `cam?.can_cool` (`:646`); first frame posts `/api/capture` (`:277`).
- **App shell / routing** `App.tsx` — `VIEWS` map (`:56`), `NAV` (`:41`), `view`/`setView` selectors (`:105-106`), `ConfirmHost` mounted once near root (`:472`), `NotConnectedInterstitial` render (`:420`), the `overlay-top` fixed layer for never-dimmed chrome (`:442`). This is where `<FirstRunWizard/>` mounts.
- **Not-connected interstitial** `components/NotConnectedInterstitial.tsx:47-55` — the "Go to Rig" CTA a lost first-timer hits; the natural re-entry anchor for the wizard.
- **Store** `store.ts` — `view` defaults `"connect"` (`:674`); `equipConnected` declared `:505`, set from `status.mode` in `handleEvent` (`:1152`); `site` declared `:504`, `setSite` `:838`, hydrated from `hello`/`status` (`:1170`,`:1185`); `confirm` slice `:518`/`:1013`. **localStorage boolean-persist template = `autoMonitor`**: key const `:314`, init read `:740`, action `:1057`. Selectors already exported: `useEquipConnected :1480`, `useSite :1479`, `useConfig :1443`, `usePlan :1445`, `usePreviews :1499`.
- **UI primitives** `components/ui.tsx` — `Panel :18`, `Toggle :132`, `HoldButton :257`, `EmptyState :492`, `IconButton :165`.
- **Modal shell to model overlay focus after** `components/ConfirmDialog.tsx:58-201` — portal-less `fixed inset-0 z-[60]` scrim `bg-black/70`, focus trap + Escape + focus-return, `role="alertdialog"`. (The wizard is **non-modal**, so it borrows the pattern but must NOT trap focus or block the underlying panels — see §1.4.)
- **Pure placement math (reuse)** `lib/tooltipPlace.ts:39` — `placeTooltip({trigger,bubble,viewport,side})` → viewport-clamped `{left,top,side}`. `CoachMark`'s callout reuses this verbatim.
- **Test idiom** `lib/__tests__/eta.test.ts` — inline-assert harness, run via `npx tsx`; `__tests__/store.test.ts:11-64` — `MemStorage` + `document`/`window` stubs for store-touching tests. `package.json` build is `tsc -b && vite build`; **no vitest/jsdom**.

### 1.3 Approach — data shapes, copy, behavior, algorithm

**F-G-1 · `hasSeen` (pure helpers `lib/coach.ts` + store slice).**
One persisted localStorage key `astrodeck-coach-seen` holds a JSON object `{ [key]: true }`. Pure helpers keep the (de)serialize/merge testable without a DOM; the store slice mirrors the `autoMonitor` persist pattern (`store.ts:314/740/1057`). The wizard's own dismissal uses key `"first-run-wizard"`; individual coach marks use their own keys (e.g. `"coach-detect-rig"`).

```ts
// lib/coach.ts
export type SeenMap = Record<string, true>;
export const COACH_SEEN_KEY = "astrodeck-coach-seen";
export const WIZARD_SEEN_KEY = "first-run-wizard";
export function parseSeen(raw: string | null): SeenMap;        // resilient: garbage → {}
export function serializeSeen(m: SeenMap): string;
export function withSeen(m: SeenMap, key: string): SeenMap;    // idempotent add
export interface Box { left: number; top: number; width: number; height: number }
export function spotlightRect(target: Box, pad?: number): Box; // pad-expanded cutout, pad default 8
```

**F-G-2 · `CoachMark` spotlight (`components/CoachMark.tsx`).**
Given a CSS selector (`data-coach="…"` anchor) + a `seenKey` + copy, it portals a dim layer with a transparent hole around the target (box-shadow spill trick: a div at `spotlightRect(rect)` with `box-shadow: 0 0 0 9999px rgba(0,0,0,.6)`) and a callout anchored via `placeTooltip`. Dismiss → `markSeen(seenKey)`. If `useHasSeen(seenKey)` is true it renders `null`. If the target selector matches nothing, it degrades to a centered callout (never a broken hole). Re-measures on resize/scroll. Thin render; the geometry (`spotlightRect`) + anchoring (`placeTooltip`) are the pure/tested parts.

**NOV-2-1 · `computeWizard` (pure machine `lib/firstRunWizard.ts`) — the tested core.**
Consumes a plain snapshot of live signals (no store, no DOM) and returns the view-model. The `"cool"` step is **applicable only when the connected camera can cool** (`status.camera.can_cool`), which changes `total` and the last step — the main subtlety. Active step = manual override if it names an applicable step, else the first applicable-and-not-done step, else the last (all done).

```ts
export type WizardStepId = "location"|"connect"|"profile"|"target"|"cool"|"frame";
export interface WizardSnapshot {
  siteIsDefault: boolean;   // true ⇒ location NOT set (default 0,0 "My Observatory")
  equipConnected: boolean;
  profileCount: number;
  targetCount: number;
  hasCooler: boolean;       // status.camera.can_cool
  coolerActive: boolean;    // status.camera.cooler.on
  frameCount: number;       // previews.length
}
export function computeWizard(snap: WizardSnapshot, manualId?: WizardStepId | null): WizardView;
```

Completion signals (all already on the wire — **no backend change**):

| step | done when | applicable | deep-link |
|---|---|---|---|
| `location` | `!siteIsDefault` | always | `settings` (SitePanel is on the default Connect tab) |
| `connect` | `equipConnected` | always | `connect` (Equipment) |
| `profile` | `profileCount > 0` | always | `connect` (Equipment Profiles panel) |
| `target` | `targetCount > 0` | always | `atlas` |
| `cool` | `coolerActive` | **`hasCooler` only** | `capture` |
| `frame` | `frameCount > 0` | always | `capture` |

**NOV-2-2 · store wizard slice.** `wizardOpen` (session bool), `wizardStepId` (manual pointer, session), actions `openWizard/closeWizard/setWizardStep`, plus the F-G-1 `coachSeen/markSeen/resetCoach`. `closeWizard()` marks `first-run-wizard` seen so it never re-nags. `resetCoach()` is a dev/QA escape hatch.

**NOV-2-3 · `FirstRunWizard` overlay.** A **non-modal** docked card (bottom sheet < 640px, bottom-right card desktop), mounted at App root, visible only while `wizardOpen`. It builds the snapshot from narrow selectors, calls `computeWizard(snap, wizardStepId)`, and renders: progress `doneCount/total`, the active step title + body + a single primary CTA (`setView(step.view)`), a done/checklist rail, Back/Skip, and an X/"Later" that `closeWizard()`s. An effect watches `doneCount`; when it rises it calls `setWizardStep(null)` so the machine auto-advances to the next incomplete step. On `complete` it shows a "You're all set — clear skies!" finish state; Finish → `closeWizard()` + `enqueueToast({level:"success", title:"Setup complete — clear skies!"})`. The profile-count signal (no store slice) is fetched with a one-shot `listProfiles()` on mount + when `equipConnected` flips.

**NOV-2-4 · App integration.** Mount `<FirstRunWizard/>` after `<ConfirmHost/>` (`App.tsx:472`). Auto-open decision lives inside the component (self-contained): once auth has resolved and login isn't showing, if `!hasSeen("first-run-wizard") && siteIsDefault && !equipConnected` (genuine blank slate) → `openWizard()`. Re-entry: a "Setup guide" button added to `NotConnectedInterstitial` (the place a lost first-timer lands) → `openWizard()`.

### 1.4 Placement & interaction

- The wizard is **not** a focus-trapping modal — the user must be able to actually operate SitePanel, the Equipment dropdowns, the cooler input while the card is up. It sits in a fixed docked slot with its own controls focusable, and never `pointer-events` over the whole screen. (Contrast: `ConfirmDialog` traps; the wizard borrows its scrim/token look for the card only.)
- `CoachMark` **is** a temporary spotlight (dims the page) but is dismiss-first and never blocks the underlying control it points at (the hole is click-through to the target).
- Lives above content but must not fight `overlay-top` toasts / lock overlay: card `z-[55]` (below `ConfirmDialog` `z-[60]` and toasts, above nav). Never dimmed by the brightness scrim (render inside `overlay-top` or a sibling with the same exemption).
- 375px: card is a full-width bottom sheet, primary CTA ≥48px, step list scrolls inside the card.

---

## 2. Global Constraints (verbatim)

Privacy — real coords **<REDACTED-LAT> / <REDACTED-LON>** and label **"<REDACTED-SITE-LABEL>"** NEVER in code/tests/docs, site default **"My Observatory"/0.0**; never `git add -A`; UI gate `cd ui && npx tsc -b`; NO jsdom — pure logic via `npx tsx` inline-assert (idiom `ui/src/lib/__tests__/eta.test.ts`); backend tests `server/.venv/Scripts/pytest.exe` (repo root, `-n0` single); client toasts via `useStore.getState().enqueueToast`; honest-disabled §11.8 (dim+lock+aria-disabled+title, never native `disabled`); do not disrupt astrotown.

**Application to this feature:** NOV-2 is **UI-only — no backend tasks** (every completion signal already rides `site.is_default`, `status.mode`/`equipConnected`, `plan.targets`, `status.camera.can_cool`/`cooler`, and `previews`), so the pytest note stands but is unused here. Any wizard control that is *not yet available* uses honest-disabled (dim + lock glyph + `aria-disabled` + `title`), never native `disabled` — but note the wizard's primary CTAs are always "go do X" and stay enabled; auto-advance replaces a gated "Next". Location copy references only the default `(0, 0)` / "My Observatory" — never the real backyard coords/label.

---

## 3. TDD Plan

Five tasks. The one Opus task is the pure step machine (subtle applicability/active-selection/override correctness); the rest are mechanical (store slice off a known template; thin React verified by `tsc`; geometry reusing a tested helper).

### Interfaces (exact signatures)

```ts
// ui/src/lib/coach.ts
export type SeenMap = Record<string, true>;
export const COACH_SEEN_KEY = "astrodeck-coach-seen";
export const WIZARD_SEEN_KEY = "first-run-wizard";
export function parseSeen(raw: string | null): SeenMap;
export function serializeSeen(m: SeenMap): string;
export function withSeen(m: SeenMap, key: string): SeenMap;
export interface Box { left: number; top: number; width: number; height: number }
export function spotlightRect(target: Box, pad?: number): Box;

// ui/src/lib/firstRunWizard.ts
import type { ViewName } from "../types";
export type WizardStepId = "location" | "connect" | "profile" | "target" | "cool" | "frame";
export interface WizardSnapshot {
  siteIsDefault: boolean; equipConnected: boolean; profileCount: number;
  targetCount: number; hasCooler: boolean; coolerActive: boolean; frameCount: number;
}
export interface WizardStepDef { id: WizardStepId; title: string; body: string; cta: string; view: ViewName; }
export interface WizardStep extends WizardStepDef { applicable: true; done: boolean; index: number; }
export interface WizardView {
  steps: WizardStep[]; activeId: WizardStepId; activeIndex: number;
  doneCount: number; total: number; complete: boolean;
}
export const WIZARD_STEPS: readonly WizardStepDef[];
export function computeWizard(snap: WizardSnapshot, manualId?: WizardStepId | null): WizardView;

// ui/src/store.ts (additive slice + selectors)
coachSeen: SeenMap;                                  // persisted astrodeck-coach-seen
wizardOpen: boolean;                                 // session
wizardStepId: WizardStepId | null;                   // session (manual override)
markSeen: (key: string) => void;
resetCoach: () => void;
openWizard: () => void;
closeWizard: () => void;
setWizardStep: (id: WizardStepId | null) => void;
export const useHasSeen: (key: string) => boolean;
export const useWizardOpen: () => boolean;
export const useWizardStepId: () => WizardStepId | null;

// ui/src/components/CoachMark.tsx
export function CoachMark(props: {
  targetSel: string; seenKey: string; title: string; body: React.ReactNode;
  side?: "top" | "bottom" | "left" | "right"; onDismiss?: () => void;
}): JSX.Element | null;

// ui/src/components/FirstRunWizard.tsx
export default function FirstRunWizard(): JSX.Element | null;
```

---

### Task 1 — `hasSeen` foundation: `lib/coach.ts` pure helpers + store slice
**Impl tier: Sonnet** (mechanical — pure JSON (de)serialize + a store slice cloned from the `autoMonitor` template at `store.ts:314/740/1057`).

**Files:** `ui/src/lib/coach.ts` (new), `ui/src/lib/__tests__/coach.test.ts` (new), `ui/src/store.ts` (additive: state + init + actions + selectors), `ui/src/store.test`/`__tests__/store.test.ts` (extend — optional).

**Step 1a — write `lib/coach.ts`:**
```ts
export type SeenMap = Record<string, true>;
export const COACH_SEEN_KEY = "astrodeck-coach-seen";
export const WIZARD_SEEN_KEY = "first-run-wizard";

export function parseSeen(raw: string | null): SeenMap {
  if (!raw) return {};
  try {
    const o = JSON.parse(raw) as unknown;
    if (!o || typeof o !== "object" || Array.isArray(o)) return {};
    const out: SeenMap = {};
    for (const [k, v] of Object.entries(o as Record<string, unknown>)) {
      if (v === true) out[k] = true;
    }
    return out;
  } catch {
    return {};
  }
}
export function serializeSeen(m: SeenMap): string { return JSON.stringify(m); }
export function withSeen(m: SeenMap, key: string): SeenMap {
  return m[key] ? m : { ...m, [key]: true };
}

export interface Box { left: number; top: number; width: number; height: number }
export function spotlightRect(target: Box, pad = 8): Box {
  return {
    left: target.left - pad, top: target.top - pad,
    width: target.width + pad * 2, height: target.height + pad * 2,
  };
}
```

**Step 1b — write `lib/__tests__/coach.test.ts`** (eta.test.ts inline-assert idiom):
```ts
import { parseSeen, serializeSeen, withSeen, spotlightRect } from "../coach";
let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, f: () => void){ try { f(); passed++; } catch(e){ failed++; failures.push(`✗ ${n}: ${(e as Error).message}`);} }
function eq<T>(a: T, b: T, m=""){ if(a!==b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }
function assert(c: boolean, m: string){ if(!c) throw new Error(m); }

test("parseSeen: null/garbage → {}", () => {
  eq(Object.keys(parseSeen(null)).length, 0, "null");
  eq(Object.keys(parseSeen("not json")).length, 0, "garbage");
  eq(Object.keys(parseSeen("[1,2]")).length, 0, "array");
});
test("parseSeen: keeps only true values", () => {
  const m = parseSeen(JSON.stringify({ a: true, b: false, c: 1 }));
  eq(m.a, true, "a"); assert(!("b" in m), "b dropped"); assert(!("c" in m), "c dropped");
});
test("withSeen: idempotent add", () => {
  const a = withSeen({}, "x"); eq(a.x, true, "added");
  const b = withSeen(a, "x"); eq(b, a, "same ref when already present");
});
test("serializeSeen round-trips through parseSeen", () => {
  const m = withSeen(withSeen({}, "first-run-wizard"), "coach-detect-rig");
  const r = parseSeen(serializeSeen(m));
  eq(r["first-run-wizard"], true, "k1"); eq(r["coach-detect-rig"], true, "k2");
});
test("spotlightRect: pads symmetrically", () => {
  const r = spotlightRect({ left: 100, top: 50, width: 40, height: 20 }, 8);
  eq(r.left, 92, "left"); eq(r.top, 42, "top"); eq(r.width, 56, "w"); eq(r.height, 36, "h");
});

const total = passed + failed;
console.log(`\ncoach.test: ${passed}/${total} passed`);
if (failures.length) console.error(failures.join("\n"));
export const result = { passed, failed, total };
```

**Step 1c — add the store slice** (`store.ts`). Import at top: `import { parseSeen, serializeSeen, withSeen, COACH_SEEN_KEY, WIZARD_SEEN_KEY, type SeenMap } from "./lib/coach";` and `import type { WizardStepId } from "./lib/firstRunWizard";`. Add to `AppState` (near `site`/`equipConnected` decl, `store.ts:504-505`):
```ts
  coachSeen: SeenMap;
  wizardOpen: boolean;
  wizardStepId: WizardStepId | null;
  markSeen: (key: string) => void;
  resetCoach: () => void;
  openWizard: () => void;
  closeWizard: () => void;
  setWizardStep: (id: WizardStepId | null) => void;
```
Init (near `site: null` `store.ts:704`):
```ts
  coachSeen: parseSeen(localStorage.getItem(COACH_SEEN_KEY)),
  wizardOpen: false,
  wizardStepId: null,
```
Actions (near `setSite` `store.ts:838`):
```ts
  markSeen: (key) => {
    const next = withSeen(get().coachSeen, key);
    if (next === get().coachSeen) return;
    try { localStorage.setItem(COACH_SEEN_KEY, serializeSeen(next)); } catch { /* quota */ }
    set({ coachSeen: next });
  },
  resetCoach: () => {
    try { localStorage.removeItem(COACH_SEEN_KEY); } catch { /* ignore */ }
    set({ coachSeen: {}, wizardOpen: false, wizardStepId: null });
  },
  openWizard: () => set({ wizardOpen: true, wizardStepId: null }),
  closeWizard: () => {
    const next = withSeen(get().coachSeen, WIZARD_SEEN_KEY);
    try { localStorage.setItem(COACH_SEEN_KEY, serializeSeen(next)); } catch { /* quota */ }
    set({ coachSeen: next, wizardOpen: false, wizardStepId: null });
  },
  setWizardStep: (id) => set({ wizardStepId: id }),
```
Selectors (near `useEquipConnected` `store.ts:1480`):
```ts
export const useHasSeen = (key: string) => useStore((s) => !!s.coachSeen[key]);
export const useWizardOpen = () => useStore((s) => s.wizardOpen);
export const useWizardStepId = () => useStore((s) => s.wizardStepId);
```
> Note: `store.ts` importing a type from `firstRunWizard.ts` (Task 2) — land Task 2's file first, or stub the `WizardStepId` type union inline until it exists. Recommend implementing Task 2 before Task 1c.

**Verify:**
- `cd ui && npx tsx src/lib/__tests__/coach.test.ts` → `coach.test: 5/5 passed`
- `cd ui && npx tsc -b` → exits 0, no diagnostics.

---

### Task 2 — pure step machine `lib/firstRunWizard.ts` (the tested core)
**Impl tier: Opus.** *Justification:* the correctness that matters is subtle and easy to get wrong: the optional `cool` step must drop out of `steps`/`total` and shift the last-step identity when `!hasCooler`; active-step selection must honor a manual override only when it names an *applicable* step and otherwise fall to the first-incomplete (with all-done falling to the last); step order-tolerance (a user who connects before setting location still sees `location` active). This is the pure heart everything else renders.

**Files:** `ui/src/lib/firstRunWizard.ts` (new), `ui/src/lib/__tests__/firstRunWizard.test.ts` (new).

**Step 2a — `lib/firstRunWizard.ts`:**
```ts
import type { ViewName } from "../types";

export type WizardStepId = "location" | "connect" | "profile" | "target" | "cool" | "frame";

export interface WizardSnapshot {
  siteIsDefault: boolean;
  equipConnected: boolean;
  profileCount: number;
  targetCount: number;
  hasCooler: boolean;
  coolerActive: boolean;
  frameCount: number;
}
export interface WizardStepDef { id: WizardStepId; title: string; body: string; cta: string; view: ViewName; }
export interface WizardStep extends WizardStepDef { applicable: true; done: boolean; index: number; }
export interface WizardView {
  steps: WizardStep[]; activeId: WizardStepId; activeIndex: number;
  doneCount: number; total: number; complete: boolean;
}

// Copy: novice-plain, no jargon. Location references ONLY the default (0,0)/"My Observatory".
export const WIZARD_STEPS: readonly WizardStepDef[] = [
  { id: "location", title: "Set your location",
    body: "AstroDeck needs your observing site to know what's up tonight. The default is (0, 0) “My Observatory” — not a real sky. Set your real location in Settings.",
    cta: "Set location", view: "settings" },
  { id: "connect", title: "Connect a rig",
    body: "Detect the gear plugged into this machine — or start the Simulator rig to explore with no hardware.",
    cta: "Go to Equipment", view: "connect" },
  { id: "profile", title: "Save a profile",
    body: "Save this rig as a profile so it reconnects with one tap next time.",
    cta: "Save a profile", view: "connect" },
  { id: "target", title: "Pick a target",
    body: "Find something in the Atlas and send it to your plan.",
    cta: "Open Atlas", view: "atlas" },
  { id: "cool", title: "Cool the camera",
    body: "Cool the sensor to your setpoint before lights — colder means less thermal noise.",
    cta: "Open Capture", view: "capture" },
  { id: "frame", title: "Take your first frame",
    body: "Shoot one frame and watch it land in the live preview. That's first light.",
    cta: "Open Capture", view: "capture" },
] as const;

function isDone(id: WizardStepId, s: WizardSnapshot): boolean {
  switch (id) {
    case "location": return !s.siteIsDefault;
    case "connect":  return s.equipConnected;
    case "profile":  return s.profileCount > 0;
    case "target":   return s.targetCount > 0;
    case "cool":     return s.coolerActive;
    case "frame":    return s.frameCount > 0;
  }
}
function isApplicable(id: WizardStepId, s: WizardSnapshot): boolean {
  if (id === "cool") return s.hasCooler; // only meaningful when the camera can cool
  return true;
}

export function computeWizard(snap: WizardSnapshot, manualId?: WizardStepId | null): WizardView {
  const steps: WizardStep[] = WIZARD_STEPS
    .filter((d) => isApplicable(d.id, snap))
    .map((d, i) => ({ ...d, applicable: true as const, done: isDone(d.id, snap), index: i }));

  const doneCount = steps.filter((st) => st.done).length;
  const total = steps.length;
  const complete = doneCount === total;

  const firstIncomplete = steps.find((st) => !st.done);
  const autoId: WizardStepId = firstIncomplete ? firstIncomplete.id : steps[steps.length - 1].id;
  const honored: WizardStepId =
    manualId && steps.some((st) => st.id === manualId) ? manualId : autoId;
  const activeIndex = steps.findIndex((st) => st.id === honored);

  return { steps, activeId: honored, activeIndex, doneCount, total, complete };
}
```

**Step 2b — `lib/__tests__/firstRunWizard.test.ts`:**
```ts
import { computeWizard, WIZARD_STEPS, type WizardSnapshot } from "../firstRunWizard";
let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, f: () => void){ try { f(); passed++; } catch(e){ failed++; failures.push(`✗ ${n}: ${(e as Error).message}`);} }
function eq<T>(a: T, b: T, m=""){ if(a!==b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }
function assert(c: boolean, m: string){ if(!c) throw new Error(m); }

const BLANK: WizardSnapshot = {
  siteIsDefault: true, equipConnected: false, profileCount: 0,
  targetCount: 0, hasCooler: true, coolerActive: false, frameCount: 0,
};

test("blank slate → location active, nothing done, 6 steps (cooler present)", () => {
  const v = computeWizard(BLANK);
  eq(v.activeId, "location", "active"); eq(v.doneCount, 0, "done"); eq(v.total, 6, "total"); eq(v.complete, false, "complete");
});
test("no cooler drops the cool step: total 5, frame is last", () => {
  const v = computeWizard({ ...BLANK, hasCooler: false });
  eq(v.total, 5, "total"); assert(!v.steps.some(s => s.id === "cool"), "cool absent");
  eq(v.steps[v.steps.length - 1].id, "frame", "frame last");
});
test("set location advances active to connect", () => {
  const v = computeWizard({ ...BLANK, siteIsDefault: false });
  eq(v.activeId, "connect", "active"); eq(v.doneCount, 1, "done");
});
test("order tolerance: connected but no location → location still active", () => {
  const v = computeWizard({ ...BLANK, equipConnected: true });
  eq(v.activeId, "location", "location first-incomplete"); eq(v.doneCount, 1, "connect counted done");
});
test("manual override honored only for applicable steps", () => {
  const v = computeWizard(BLANK, "target");
  eq(v.activeId, "target", "honored");
  const noCool = computeWizard({ ...BLANK, hasCooler: false }, "cool"); // cool not applicable
  eq(noCool.activeId, "location", "falls back to auto when override N/A");
});
test("all applicable done → complete, active is last step", () => {
  const done: WizardSnapshot = { siteIsDefault: false, equipConnected: true, profileCount: 1,
    targetCount: 2, hasCooler: true, coolerActive: true, frameCount: 1 };
  const v = computeWizard(done);
  eq(v.complete, true, "complete"); eq(v.doneCount, 6, "all done"); eq(v.activeId, "frame", "last");
});
test("no-cooler rig can complete without cooling", () => {
  const v = computeWizard({ siteIsDefault: false, equipConnected: true, profileCount: 1,
    targetCount: 1, hasCooler: false, coolerActive: false, frameCount: 1 });
  eq(v.complete, true, "complete on 5 steps"); eq(v.total, 5, "total");
});
test("WIZARD_STEPS never leaks the real backyard label", () => {
  const blob = JSON.stringify(WIZARD_STEPS);
  assert(!blob.includes("Backyard") && !blob.includes("[SITE-LAT]"), "no private site data in copy");
});

const total = passed + failed;
console.log(`\nfirstRunWizard.test: ${passed}/${total} passed`);
if (failures.length) console.error(failures.join("\n"));
export const result = { passed, failed, total };
```

**Verify:**
- `cd ui && npx tsx src/lib/__tests__/firstRunWizard.test.ts` → `firstRunWizard.test: 8/8 passed`
- `cd ui && npx tsc -b` → 0.

---

### Task 3 — `CoachMark` spotlight primitive (F-G-2)
**Impl tier: Sonnet** (thin React; geometry is the already-tested `spotlightRect` + `placeTooltip`; no novel math).

**Files:** `ui/src/components/CoachMark.tsx` (new). Reuse `createPortal` (see `ui.tsx:6`), `placeTooltip` (`lib/tooltipPlace.ts:39`), `spotlightRect` + `useHasSeen`/`markSeen`.

**Behavior:** `if (useHasSeen(seenKey)) return null;`. `useLayoutEffect` resolves `document.querySelector(targetSel)`; if found, measure `getBoundingClientRect()` → keep `rect` state, recompute on `resize`/`scroll` (capture, passive). Portal to `document.body`:
- dim/cutout: a `fixed` div positioned at `spotlightRect(rect)` with `boxShadow: "0 0 0 9999px rgba(0,0,0,0.55)"`, `pointerEvents: "none"`, `border: 1px solid var(--accent)`. (Hole is click-through so the user can still hit the real control.)
- callout: a `.panel` card placed via `placeTooltip({ trigger: rect, bubble: {measured}, viewport, side })`, containing `title`, `body`, and a "Got it" button → `markSeen(seenKey); onDismiss?.()`. Card `z-[56]`, `pointerEvents:"auto"`.
- If target not found: render the callout centered (`fixed inset-0 grid place-items-center`), no cutout.
- Escape / outside-click on the dim → dismiss. Reduced motion: no fade.

**Verify:** `cd ui && npx tsc -b` → 0. (Render surface exercised via Task 4/5 wiring; there is no jsdom, so the check is the typechecker + the pure `spotlightRect`/`placeTooltip` tests already green.)

---

### Task 4 — `FirstRunWizard` overlay component
**Impl tier: Sonnet** (thin render; all logic delegated to `computeWizard`; store selectors already exist).

**Files:** `ui/src/components/FirstRunWizard.tsx` (new). Uses `useWizardOpen`, `useWizardStepId`, `useSite`, `useEquipConnected`, `useConfig`, `usePlan`, `usePreviews`, `useStore` (`setView`, `setWizardStep`, `closeWizard`, `enqueueToast`), `computeWizard`, `WIZARD_STEPS`, `listProfiles` (`api/backends`).

**Step 4a — build the snapshot + view:**
```tsx
const open = useWizardOpen();
const manualId = useWizardStepId();
const site = useSite();
const config = useConfig();
const equipConnected = useEquipConnected();
const targetCount = usePlan().targets.length;
const previewCount = usePreviews().length;
const hasCooler = useStore((s) => !!s.status?.camera?.can_cool);
const coolerActive = useStore((s) => !!s.status?.camera?.cooler?.on);
const setView = useStore((s) => s.setView);
const setWizardStep = useStore((s) => s.setWizardStep);
const closeWizard = useStore((s) => s.closeWizard);

const [profileCount, setProfileCount] = useState(0);
useEffect(() => { if (!open) return;
  void listProfiles().then((r) => setProfileCount(r.length)).catch(() => {});
}, [open, equipConnected]);

const siteIsDefault = site?.is_default ?? config?.site?.is_default ?? true;
const view = computeWizard(
  { siteIsDefault, equipConnected, profileCount, targetCount, hasCooler, coolerActive, frameCount: previewCount },
  manualId,
);
```

**Step 4b — auto-advance (clear manual override when a step completes):**
```tsx
const prevDone = useRef(view.doneCount);
useEffect(() => {
  if (view.doneCount > prevDone.current && manualId) setWizardStep(null);
  prevDone.current = view.doneCount;
}, [view.doneCount, manualId, setWizardStep]);
```

**Step 4c — render** (non-modal docked card, `if (!open) return null;`):
- container: `fixed bottom-0 inset-x-0 sm:inset-x-auto sm:right-4 sm:bottom-4 z-[55] ... sm:max-w-[380px]` `.panel sheet-enter`, `role="dialog" aria-label="First-run setup guide"` (NOT `aria-modal`).
- header: "Get set up" + progress `{view.doneCount}/{view.total}` + X ("Later") → `closeWizard()`.
- a compact step rail: each `view.steps[i]` shows a `✓` (done, `text-good`) or `◦` glyph + `title`; the active one highlighted; a done step is a clickable `setWizardStep(step.id)` to revisit (always enabled — no honest-disabled needed since navigation is free).
- active step body + primary CTA `btn btn-accent min-h-11` → `setView(active.view)` (the card stays up; the user acts in the target view and the step auto-completes).
- Back / Skip: `setWizardStep(prev/next applicable step id)`.
- when `view.complete`: swap body for "You're all set — clear skies!" and a Finish `btn btn-accent` → `closeWizard(); useStore.getState().enqueueToast({ level: "success", title: "Setup complete — clear skies!" })`.

**Verify:** `cd ui && npx tsc -b` → 0.

---

### Task 5 — App integration: mount, auto-open, re-entry
**Impl tier: Sonnet** (small wiring edits).

**Files:** `ui/src/App.tsx` (mount + auto-open effect), `ui/src/components/NotConnectedInterstitial.tsx` (re-entry button), optionally `ui/src/views/EquipmentView.tsx` (a `data-coach="detect-rig"` attribute on the Detect/Simulator buttons `:422-427` for an illustrative CoachMark).

**Step 5a — mount** in `App.tsx` right after `<ConfirmHost />` (`App.tsx:472`): `import FirstRunWizard from "./components/FirstRunWizard";` then `<FirstRunWizard />`. It self-hides when `!wizardOpen`.

**Step 5b — auto-open** (inside `FirstRunWizard`, self-contained; add to Task 4's component so App stays minimal):
```tsx
const openWizard = useStore((s) => s.openWizard);
const seenWizard = useHasSeen(WIZARD_SEEN_KEY);
useEffect(() => {
  // genuine blank slate only; never re-nag once dismissed (seenWizard guards it).
  if (!seenWizard && !open && siteIsDefault && !equipConnected) openWizard();
}, [seenWizard, open, siteIsDefault, equipConnected, openWizard]);
```
(Guarded by `siteIsDefault && !equipConnected` so a returning user who already has a rig/site never gets it; `seenWizard` guards a dismissed user.)

**Step 5c — re-entry** in `NotConnectedInterstitial.tsx` (`:45-55`, the `canConnect` branch): add a secondary `btn` "New here? Open the setup guide" → `useStore.getState().openWizard()`, so a lost first-timer who dismissed it can reopen.

**Verify:**
- `cd ui && npx tsc -b` → 0.
- Manual smoke (dev server, sim rig): clear `localStorage['astrodeck-coach-seen']`; reload with default site + no rig → wizard auto-opens on `location`; set a location in Settings → advances to `connect`; start the Simulator rig → `connect` checks off; save a profile / send an Atlas target / cool / capture one frame → each step checks off live; `complete` shows the finish + success toast; reload → does not reappear; the interstitial's "Open the setup guide" reopens it.

---

## 4. Open decisions

1. **Where does the "profile" step's count come from — a one-shot `listProfiles()` in the component, or a new store slice?**
   *Recommendation:* the one-shot fetch in `FirstRunWizard` (on open + on `equipConnected` change). A store slice for profiles is unjustified for one wizard step and would add a polling surface; the fetch is read-only and cheap. (If a future feature needs live profile counts, promote then.)

2. **Auto-open trigger breadth — blank-slate only, or any un-seen user?**
   *Recommendation:* blank-slate only (`!seen && siteIsDefault && !equipConnected`). A user who already connected once (or on `astrotown`) should never be interrupted; the re-entry button covers the "I dismissed it and want it back" case. Avoids nagging existing installs on first deploy.

3. **Is `CoachMark` (F-G-2) required for NOV-2 MVP, or can the wizard ship on deep-links + auto-advance alone?**
   *Recommendation:* build `CoachMark` (it's the named F-G foundation and is small/self-contained) but wire only **one** illustrative spotlight (the Equipment Detect/Simulator buttons on the `connect` step) so the primitive is proven without heavy per-control choreography. The wizard is fully functional without any CoachMark, so this de-risks the schedule.

4. **`siteIsDefault` source — `store.site` (poll/hello) or `config.site` (persisted)?**
   *Recommendation:* prefer `store.site?.is_default`, fall back to `config?.site?.is_default`, default `true`. `store.site` hydrates from the `hello` summary at WS connect (`store.ts:1185`) even with no equipment, so it's live at boot; `config` is the durable backstop.

5. **Card z-index vs. the brightness scrim / toasts.**
   *Recommendation:* `z-[55]` (below `ConfirmDialog` `z-[60]` and `overlay-top` toasts, above nav), and render it exempt from the brightness scrim (sibling of `overlay-top`, or inside it) so night-mode dimming never buries the guide. Confirm against `App.tsx:434-467` layering during Task 5.

6. **Dismiss semantics — does "Later" (X) permanently mark seen, or just close for the session?**
   *Recommendation:* "Later"/X and Finish both `closeWizard()` (permanent `first-run-wizard` seen) — one dismissal model, no half-states — and the interstitial re-entry is the deliberate way back. A session-only close would re-nag on every reload and train users to reflexively dismiss.
