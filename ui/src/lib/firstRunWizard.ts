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
