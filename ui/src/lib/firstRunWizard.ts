// firstRunWizard.ts — pure step machine for NOV-2 (first-run wizard design
// spec §3, Task 2). Consumes a plain snapshot of live signals (no store, no
// DOM) and returns the view-model: which step is active, what's done, and
// the applicability of the optional "cool" step (only when the connected
// camera can cool — status.camera.can_cool). Everything else (CoachMark,
// FirstRunWizard) is a thin render over this.

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
export interface WizardStepDef {
  id: WizardStepId;
  title: string;
  /** WHERE to go and WHAT to press. Every body names the view AND the place on
   *  it (top / bottom / behind MORE), because the phone feedback that produced
   *  this file was "I need to scroll to the bottom of the page, which is not
   *  easy for a user to know about unless we tell them to scroll." */
  body: string;
  /** The STATED REASON the docked bar's Next is inert, phrased to complete
   *  "Next unlocks once …". Never the native `disabled` attribute: the bar dims
   *  Next, sets aria-disabled, and prints this. One per step so the reason
   *  names the specific thing still missing rather than a generic "not yet". */
  need: string;
  cta: string;
  view: ViewName;
}
export interface WizardStep extends WizardStepDef { applicable: true; done: boolean; index: number; }
export interface WizardView {
  steps: WizardStep[]; activeId: WizardStepId; activeIndex: number;
  doneCount: number; total: number; complete: boolean;
}

// Copy: novice-plain, no jargon, and SPATIAL. Each body says which view and
// where on it — on a phone the bottom bar only carries Equipment / Align /
// Mount / Focus / Capture, so Settings, Atlas and Plan live behind MORE, and
// the Cooler panel is at the very bottom of a long Capture page. A user cannot
// guess either of those, so the copy states them.
//
// Only the "location" step may mention the default (0, 0) site — it is the one
// step whose whole point is that the default is not a real sky. `cta` is the
// bar's deep-link button and shares a cramped 390px row with Back/Skip/Next,
// so it is the destination's NAME; the accessible name says "Open <name>".
export const WIZARD_STEPS: readonly WizardStepDef[] = [
  { id: "location", title: "Set your location",
    body: "Open Settings — on a phone it's behind MORE in the bottom bar. Observing Site is the first panel. The default (0, 0) is not a real sky.",
    need: "your real location is saved",
    cta: "Settings", view: "settings" },
  { id: "connect", title: "Connect a rig",
    body: "Open Equipment — on a phone it's the first tab in the bottom bar. Scroll down to Rig Actions, then Detect hardware rig or Simulator rig.",
    need: "a rig is connected",
    cta: "Equipment", view: "connect" },
  { id: "profile", title: "Save a profile",
    body: "Back on Equipment, keep scrolling past Rig Actions to the Profiles panel. Save this rig and it reconnects with one tap.",
    need: "a profile is saved",
    cta: "Equipment", view: "connect" },
  { id: "target", title: "Pick a target",
    body: "Open Atlas — on a phone it's behind MORE in the bottom bar. Search or tap the sky, then press Add target to Plan.",
    need: "a target is in your plan",
    cta: "Atlas", view: "atlas" },
  { id: "cool", title: "Cool the camera",
    body: "Open Capture and scroll all the way to the BOTTOM — the Cooler panel is down there. Type a Target °C, then press Cool.",
    need: "the cooler is running",
    cta: "Capture", view: "capture" },
  { id: "frame", title: "Take your first frame",
    body: "Back to the TOP of Capture: the Exposure panel. Set the seconds, press Single, and watch it land in the preview.",
    need: "one frame has landed",
    cta: "Capture", view: "capture" },
] as const;

/** Per-step completion. THE single source of truth for "is this step actually
 *  finished" — the docked bar's Next button is wired to the `done` flag this
 *  produces (via `computeWizard`), never to a second guess of its own, so Next
 *  ungreys the instant the real signal flips. */
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
