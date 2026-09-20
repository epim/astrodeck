import type { FocusEvent, PolarState, RigStatus, SequenceState, SiteInfo, ViewName } from "../types";

export type Journey = "first" | "returning";
export type Change = "same" | "moved" | "equipment";
export interface GuidedSnapshot {
  status: RigStatus | null;
  site: Pick<SiteInfo, "is_default" | "horizon_points"> | null;
  fresh: boolean;
  sequence: Pick<SequenceState, "state" | "target">;
  focus: Pick<FocusEvent, "state"> | null;
  polar: { state: PolarState["state"] | "pausing" };
}
export interface Step {
  id: string;
  title: string;
  description: string;
  evidence: string;
  action: string;
  view: ViewName;
}
export const ACTIVE_SEQUENCE = new Set<SequenceState["state"]>([
  "running", "paused", "holding", "aborting", "nina_native",
]);

export function guidedModel(s: GuidedSnapshot, journey: Journey, change: Change) {
  const live = (role: string) => s.fresh && (
    s.status?.backend_links?.find((l) => l.role === role)?.connected ?? !!s.status?.connected?.[role]?.connected
  );
  const camera = live("camera"), mount = live("telescope"), focuser = live("focuser");
  const siteSaved = s.site?.is_default === false;
  // Configuration is useful evidence, but neither a saved skyline nor an old
  // focus/polar result certifies the physical setup tonight. No green "ready"
  // result is synthesized here; the forthcoming readiness service owns that.
  const steps: Step[] = [
    { id: "equipment", title: "Meet your equipment", view: "connect", action: "Review equipment",
      description: "Connect your camera and mount. Save them as a profile so you can use the same setup next time.",
      evidence: !s.fresh ? "Waiting for current equipment status" : camera && mount ? "Camera and mount connected" : "Camera and mount connections need a check" },
    { id: "location", title: "Find your place", view: "settings", action: "Set observing location",
      description: "Tell AstroDeck where the telescope is. Check that the mount is steady and has room to move.",
      evidence: s.site == null ? "Location status unavailable" : siteSaved ? "Location saved · confirm this is tonight's site" : "Observing location needs to be set" },
    { id: "horizon", title: "Know your surroundings", view: "atlas", action: "Open Sky atlas",
      description: "Trace nearby trees and buildings so AstroDeck knows what blocks your view. Check for anything the telescope could hit as it turns.",
      evidence: s.site?.horizon_points?.length ? "Saved horizon · review from the telescope's position" : "Horizon needs review at the telescope" },
    { id: "focus", title: "Bring the stars into focus", view: "focus", action: "Open focus tools",
      description: focuser ? "Take a star image and get it roughly in focus. Then try autofocus to sharpen it." : "You can focus by hand. Take short star exposures and adjust until the stars look small and sharp.",
      evidence: s.fresh && s.focus?.state === "done" ? "Previous focus result available · check it for this setup" : "Needs a usable star image" },
    { id: "alignment", title: "Align with the sky", view: "polar", action: "Open alignment tools",
      description: "For an equatorial mount, choose a clear area for the measurements. Follow the adjustment instructions, then check the result.",
      evidence: "Physical alignment must be checked for this setup" },
    { id: "image", title: "Make your first image", view: "tonight", action: "Explore tonight's targets",
      description: "Find something visible from your location. See how it fits in the frame, then try a short exposure.",
      evidence: "Guided checks the setup before moving to a target" },
  ];
  if (journey === "returning") {
    steps[0].title = "Check your equipment";
    steps[1].title = "Confirm your location";
    steps[3].title = "Check the focus";
    steps[4].title = "Verify alignment";
    steps[5].title = "Choose tonight's target";
    steps[5].description = "Return to a favorite or find something new. Check its visibility and your capture settings for tonight.";
  }
  let recommended = !camera || !mount ? steps[0] : !siteSaved ? steps[1] : steps[2];
  if (journey === "returning" && change === "equipment") recommended = steps[0];
  if (journey === "returning" && change === "moved" && camera && mount) recommended = steps[1];
  const operations: { title: string; view: ViewName }[] = [];
  if (ACTIVE_SEQUENCE.has(s.sequence.state)) operations.push({ title: `Imaging session · ${s.sequence.state.replace("nina_native", "NINA is running")}`, view: "monitor" });
  if (s.focus?.state === "running") operations.push({ title: "Autofocus in progress", view: "focus" });
  if (["running", "paused", "pausing"].includes(s.polar.state)) operations.push({ title: `Polar alignment · ${s.polar.state}`, view: "polar" });
  if (s.status?.mount?.slewing) operations.push({ title: "Mount moving", view: "mount" });
  if (s.status?.looping) operations.push({ title: "Camera taking repeated exposures", view: "capture" });
  return { steps, recommended, operations, camera, mount, focuser, siteSaved };
}
