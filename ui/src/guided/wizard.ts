import type { ViewName } from "../types";

export const WIZARD_STEPS = [
  { id: "equipment", label: "Equipment", view: "connect", title: "Meet your equipment",
    help: "Connect your camera and mount. A guide camera and motorized focuser are optional." },
  { id: "location", label: "Location", view: null, title: "Where is your telescope?",
    help: "AstroDeck uses this location to work out what's visible. It should be the telescope's location, even when you're controlling it from somewhere else." },
  { id: "horizon", label: "Surroundings", view: null, title: "Find your clear sky",
    help: "Stand beside the telescope and trace the tops of nearby trees and buildings. Also check that the tripod is steady and the telescope can turn without catching a cable or hitting anything." },
  { id: "focus", label: "Focus", view: "focus", title: "Bring the stars into focus",
    help: "Take a short exposure of a star. Adjust the focus until the star looks small and sharp. If you have a motorized focuser, autofocus can refine it." },
  { id: "alignment", label: "Alignment", view: "polar", title: "Align with the sky",
    help: "If you have an equatorial mount, choose a clear area for the alignment measurements. Follow the adjustment instructions, then measure again to check the result. Other mount types need their own setup procedure." },
  { id: "image", label: "First image", view: null, title: "Take your first image",
    help: "Choose a target, point the telescope, then take a short exposure. We'll keep you here while the image arrives." },
] as const;
export type WizardStep = typeof WIZARD_STEPS[number]["id"];
export function wizardStep(id: WizardStep) { return WIZARD_STEPS.find(step => step.id === id)!; }
export function wizardAcceptsView(id: WizardStep, view: ViewName): boolean {
  return id === "image" ? ["tonight", "atlas", "capture"].includes(view) : wizardStep(id).view === view;
}
