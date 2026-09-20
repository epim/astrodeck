import { create } from "zustand";
import { useStore } from "../store";
import { WIZARD_STEPS, type WizardStep } from "./wizard";

type Fact = "location" | "horizon" | "focus" | "alignment";
export type SetupFact = Fact;
let reportCheck: ((action: "complete" | "invalidate", fact: Fact, origin?: "manual") => Promise<boolean> | void) | null = null;
export function observeSetupChecks(listener: typeof reportCheck) { reportCheck = listener; }
export interface SkyPosition { ra_hours: number; dec_deg: number; }
export const useGuidedSetup = create<{
  location: boolean; horizon: boolean; focus: boolean; alignment: boolean;
  field: SkyPosition | null;
  siteId: string | null;
  locationKey: string; horizonKey: string;
  recoveryMessage: string | null;
  recovering: boolean;
  savingChecks: boolean;
  setField: (field: SkyPosition | null) => void;
  complete: (step: Fact, origin?: "manual") => Promise<boolean>; invalidate: (step: Fact) => void;
}>((set) => ({
  location: false, horizon: false, focus: false, alignment: false,
  field: null,
  siteId: null,
  locationKey: "", horizonKey: "",
  recoveryMessage: null, recovering: false, savingChecks: false,
  setField: (field) => {set({field,focus:false,alignment:false});reportCheck?.("invalidate", "focus");},
  complete: (step, origin) => {
    set({ [step]: true, ...(step === "location" ? {locationKey: JSON.stringify(useStore.getState().config?.site)} : {}), ...(step === "horizon" ? {horizonKey: JSON.stringify(useStore.getState().config?.safety?.horizon)} : {}) });
    return Promise.resolve(reportCheck?.("complete", step, origin) ?? true);
  },
  invalidate: (step) => {
    const facts: Fact[] = ["location", "horizon", "focus", "alignment"];
    set(Object.fromEntries(facts.slice(facts.indexOf(step)).map(key => [key, false])));
    if (step === "location" || step === "horizon") set({field:null});
    reportCheck?.("invalidate", step);
  },
}));

export function atPosition(position: SkyPosition | undefined, target: SkyPosition): boolean {
  if (!position || !Number.isFinite(position.ra_hours) || !Number.isFinite(position.dec_deg)) return false;
  const rad = Math.PI / 180;
  const cosine = Math.sin(position.dec_deg*rad)*Math.sin(target.dec_deg*rad) + Math.cos(position.dec_deg*rad)*Math.cos(target.dec_deg*rad)*Math.cos((position.ra_hours-target.ra_hours)*15*rad);
  return Math.acos(Math.max(-1,Math.min(1,cosine))) / rad < 1;
}

export function equipmentBlocker(s = useStore.getState()): string | null {
  if (useGuidedSetup.getState().recovering) return "Checking the current setup with the telescope…";
  if (s.wsPhase !== "up" || s.telemetryStale || !s.status) return "Reconnect to the telescope so we can check the equipment.";
  const connected = (role: string) => s.status?.backend_links?.find(l => l.role === role)?.connected ?? !!s.status?.connected?.[role]?.connected;
  if (!connected("telescope")) return "Connect a mount before continuing.";
  if (!connected("camera")) return "Connect an imaging camera before continuing.";
  return null;
}

export function stepBlocker(step: WizardStep): string | null {
  const index = WIZARD_STEPS.findIndex(item => item.id === step);
  if (index === 0) return null;
  const equipment = equipmentBlocker();
  if (equipment) return equipment;
  const facts = useGuidedSetup.getState();
  if (facts.savingChecks) return "Saving the setup check to the controller…";
  if (index >= 2 && !facts.location) return "Save your observing location first.";
  if (index >= 3 && !facts.horizon) return "Review and save the horizon first.";
  const status = useStore.getState().status;
  if (index >= 4 && (useStore.getState().focus?.state === "running" || useStore.getState().filterOffsetsLearn?.state === "running")) return "Wait for the focus measurements to finish.";
  if (index >= 4 && status?.busy_lanes?.some(lane=>["autofocus","filter_offsets","focuser"].includes(lane))) return "Wait for the focus measurements to finish.";
  if (index >= 5 && status?.busy_lanes?.includes("polar")) return "Finish the alignment operation first.";
  if (index >= 4 && !facts.focus) return "Point at the alignment field and finish focusing first.";
  if (index >= 5 && !facts.alignment) return "Finish and verify polar alignment first.";
  return null;
}

export function useStepBlocker(step: WizardStep) {
  useStore(s => s.status); useStore(s => s.wsPhase); useStore(s => s.telemetryStale);
  useStore(s => s.focus); useStore(s => s.filterOffsetsLearn);
  useGuidedSetup();
  return stepBlocker(step);
}
