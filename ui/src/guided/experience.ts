import { create } from "zustand";
import type { Journey } from "./model";
import { WIZARD_STEPS, type WizardStep } from "./wizard";

export type ExperienceMode = "guided" | "pro";
const KEY = "astrodeck.experience.v1";
const JOURNEY_KEY = "astrodeck.journey.v1";
const WIZARD_KEY = "astrodeck.walkthrough.v1";
export function initialWalkthrough(storage?: Pick<Storage,"getItem">, hash = ""): {wizard:WizardStep|null;home:boolean} {
  const fallback = {wizard:null,home:!new URLSearchParams(hash.split("?")[1]).has("panel")};
  try {
    const saved = JSON.parse(storage?.getItem(WIZARD_KEY) ?? "null");
    // An explicit link to another tool takes precedence over a saved page.
    if (saved?.path !== hash.split("?")[0] || !WIZARD_STEPS.some(s=>s.id===saved?.wizard)) return fallback;
    return {wizard:saved.wizard,home:saved.home===true};
  } catch { return fallback; }
}
function rememberWalkthrough(wizard:WizardStep|null,home:boolean) {
  try {window.localStorage.setItem(WIZARD_KEY,JSON.stringify({wizard,home,path:window.location.hash.split("?")[0]}));} catch { /* optional presentation preference */ }
}
const resumed = (()=>{try{return initialWalkthrough(window.localStorage,window.location.hash);}catch{return {wizard:null,home:true};}})();
function initialJourney(): Journey {
  try { return window.localStorage.getItem(JOURNEY_KEY) === "returning" ? "returning" : "first"; }
  catch { return "first"; }
}

export function initialExperience(storage?: Pick<Storage, "getItem">, hash = ""): ExperienceMode {
  const requested = new URLSearchParams(hash.split("?")[1]).get("experience");
  if (requested === "guided" || requested === "pro") return requested;
  try { return storage?.getItem(KEY) === "guided" ? "guided" : "pro"; }
  catch { return "pro"; }
}

function initialMode(): ExperienceMode {
  if (typeof window === "undefined") return "pro";
  try {
    const mode = initialExperience(window.localStorage, window.location.hash);
    window.localStorage.setItem(KEY, mode);
    return mode;
  }
  catch { return initialExperience(undefined, window.location.hash); }
}

// Presentation preferences only. This store deliberately has no dependency on
// the rig store or command APIs. Changing mode cannot rewrite a plan or run.
export const useExperience = create<{
  mode: ExperienceMode;
  home: boolean;
  journey: Journey;
  wizard: WizardStep | null;
  openWizard: (step: WizardStep) => void;
  leaveWizard: () => void;
  setJourney: (journey: Journey) => void;
  setMode: (mode: ExperienceMode) => void;
  showHome: () => void;
  showTool: () => void;
}>((set) => ({
  mode: initialMode(),
  home: resumed.home,
  journey: initialJourney(),
  wizard: resumed.wizard,
  openWizard: (wizard) => {rememberWalkthrough(wizard,false);set({ wizard, home: false });},
  leaveWizard: () => {rememberWalkthrough(null,false);set({ wizard: null });},
  setJourney: (journey) => {
    try { window.localStorage.setItem(JOURNEY_KEY, journey); } catch { /* optional preference */ }
    set({ journey });
  },
  setMode: (mode) => {
    try { window.localStorage.setItem(KEY, mode); } catch { /* in-memory preference still works */ }
    // A shared Guided preview link must not force Guided back on after the
    // operator explicitly chooses Pro and refreshes. Preserve other flags.
    if (typeof window !== "undefined") {
      const [path, query] = window.location.hash.split("?");
      const params = new URLSearchParams(query);
      params.set("experience", mode);
      window.history.replaceState(window.history.state, "", `${path || "#/classic"}?${params}`);
    }
    set({ mode });
  },
  showHome: () => {rememberWalkthrough(useExperience.getState().wizard,true);set({ home: true });},
  showTool: () => {rememberWalkthrough(useExperience.getState().wizard,false);set({ home: false });},
}));
