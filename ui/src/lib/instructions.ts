// instructions.ts — pure helpers for the conditional sequencer (PRO-3). Mirrors
// the "logic in lib, thin render" split (lib/planLibrary.ts, lib/sequenceTemplates.ts):
// InstructionsPanel is a thin render over these tested, side-effect-free helpers.
// The backend evaluator (sequence/instructions.py) is authoritative at run time;
// these drive the editor's default/describe/validate UX only.
import type { ActionKind, Instruction, TriggerKind } from "../types";
import { uid } from "./ids";

export const TRIGGER_LABELS: Record<TriggerKind, string> = {
  on_hfr_above: "HFR above",
  on_guide_rms_above: "Guide RMS above",
  on_frame_rejected: "Frame rejected",
  on_target_complete: "Target complete",
  at_time: "At time",
};

export const ACTION_LABELS: Record<ActionKind, string> = {
  notify: "Notify",
  pause: "Pause",
  refocus: "Refocus",
  dither: "Dither",
  abort: "Abort",
};

/** HFR / guide-RMS triggers gate on a numeric threshold. */
export function triggerNeedsThreshold(t: TriggerKind): boolean {
  return t === "on_hfr_above" || t === "on_guide_rms_above";
}

/** Only the at_time trigger needs an "HH:MM" clock. */
export function triggerNeedsTime(t: TriggerKind): boolean {
  return t === "at_time";
}

/** A fresh, valid enabled rule for the "Add rule" button. Carries its own id
 *  (client-side, matching server uuid4().hex) so edits are stable across
 *  re-renders. Defaults to a notify-on-reject rule (needs no threshold/time). */
export function defaultInstruction(): Instruction {
  return {
    id: uid(),
    enabled: true,
    trigger: "on_frame_rejected",
    threshold: 0,
    at_time: null,
    action: "notify",
    message: "Frame rejected",
    level: "warning",
    once: false,
    cooldown_s: 0,
    only_target: null,
  };
}

const HHMM_RE = /^(\d{2}):(\d{2})$/;

function validHhmm(s: string | null): boolean {
  const m = HHMM_RE.exec(s ?? "");
  return !!m && Number(m[1]) < 24 && Number(m[2]) < 60;
}

/** Author-facing validation. Returns a list of problems ([] === valid). */
export function validateInstruction(i: Instruction): string[] {
  const problems: string[] = [];
  if (triggerNeedsThreshold(i.trigger) && !(i.threshold > 0)) {
    problems.push("Threshold must be greater than 0.");
  }
  if (triggerNeedsTime(i.trigger) && !validHhmm(i.at_time)) {
    problems.push('Time must be in "HH:MM" (24h) form.');
  }
  if (i.action === "notify" && !i.message.trim()) {
    problems.push("Notify needs a message.");
  }
  if (i.cooldown_s < 0) {
    problems.push("Cooldown cannot be negative.");
  }
  return problems;
}

/** A one-line human summary, e.g. `When HFR > 3.5 → refocus (once) · only M31`. */
export function describeInstruction(i: Instruction, _targetNames?: string[]): string {
  let cond: string;
  switch (i.trigger) {
    case "on_hfr_above":
      cond = `When HFR > ${i.threshold}`;
      break;
    case "on_guide_rms_above":
      cond = `When guide RMS > ${i.threshold}"`;
      break;
    case "on_frame_rejected":
      cond = "When a frame is rejected";
      break;
    case "on_target_complete":
      cond = "When target complete";
      break;
    case "at_time":
      cond = `At ${i.at_time ?? "??:??"}`;
      break;
    default:
      cond = "When";
  }
  let s = `${cond} → ${ACTION_LABELS[i.action].toLowerCase()}`;
  if (i.once) s += " (once)";
  if (i.only_target) s += ` · only ${i.only_target}`;
  return s;
}
