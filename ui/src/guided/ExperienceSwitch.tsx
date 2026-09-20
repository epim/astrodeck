import { Segmented } from "../components/Segmented";
import { useExperience } from "./experience";
import { useStore } from "../store";
import { ACTIVE_SEQUENCE } from "./model";

export function ExperienceSwitch() {
  const mode = useExperience((s) => s.mode);
  const setMode = useExperience((s) => s.setMode);
  return <div className="experience-switch">
    <Segmented options={[{ value: "guided", label: "Guided" }, { value: "pro", label: "Pro" }]}
      value={mode} onChange={(next) => {
        // Keep the current operation's tools exposed during a presentation
        // switch. This changes neither the selected tool nor the rig state.
        const s = useStore.getState();
        const busy = ACTIVE_SEQUENCE.has(s.sequence.state) || s.focus?.state === "running"
          || ["running", "paused", "pausing"].includes(s.polar.state) || !!s.status?.mount?.slewing || !!s.status?.looping;
        if (next === "guided" && busy) {
          useExperience.getState().leaveWizard();
          useExperience.getState().showTool();
        }
        setMode(next);
      }} ariaLabel="Experience" />
  </div>;
}
