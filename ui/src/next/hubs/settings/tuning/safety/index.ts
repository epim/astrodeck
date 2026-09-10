// The Safety tuning area (wave R7, T-R7-9). Two entry points, one for each
// place the rebuilt policy editor is mounted:
//
//   * `SafetyTuningPanel` - the body of Settings > MORE > SAFETY.
//   * `EscalationEditor`  - also mounted straight into `hubs/rig/sheets/
//     safety.tsx`, replacing `components/settings/EscalationPanel` there.
//
// The pure modules are exported too, so a test grades the sentences the screen
// prints rather than ones it typed itself.

export { SafetyTuningPanel, tuningLockSentence } from "./SafetyTuningPanel";
export { LIMITS_LINK_SUB, LIVE_LINK_SUB, TRIP_LINK_SUB, WARM_LINK_SUB } from "./SafetyTuningPanel";
export { EscalationEditor } from "./EscalationEditor";
export {
  CUSTOM_NOT_PICKABLE, PRESET_BLURB, PRESET_LABEL, PRESET_ORDER, PRESET_VALUES, presetLine,
  presetPatch, type PresetName,
} from "./safetyPresets";
export {
  ACTION_BLURB, ACTION_LABEL, ESC_TITLE, escalationLockSentence, HFR_LABEL, RETAKE_OFF_REASON,
  RETRIES_OFF_REASON, COOLING_OFF_REASON, GUIDING_OFF_REASON, watchdogLine,
} from "./escalationModel";
