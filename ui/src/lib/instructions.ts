// instructions.ts — pure helpers for the conditional sequencer (PRO-3). Mirrors
// the "logic in lib, thin render" split (lib/planLibrary.ts, lib/sequenceTemplates.ts):
// InstructionsPanel is a thin render over these tested, side-effect-free helpers.
// The backend evaluator (sequence/instructions.py) is authoritative at run time;
// these drive the editor's default/describe/validate UX only.
import type {
  ActionKind, Condition, Instruction, Predicate, PredicateKind, TriggerKind,
} from "../types";
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
  run_target: "Jump to target",
  skip_target: "Skip target",
};

/** Leaf predicates of a compound condition — the SAME closed vocabulary as the
 *  flat triggers, so "combine conditions" is a lossless upgrade, not a new
 *  language. */
export const PREDICATE_LABELS: Record<PredicateKind, string> = {
  hfr_above: "HFR above",
  guide_rms_above: "Guide RMS above",
  frame_rejected: "Frame rejected",
  target_complete: "Target complete",
  at_time: "At time",
};

/** Flat trigger <-> leaf predicate (1:1; the server keeps the same mapping). */
export const PREDICATE_OF: Record<TriggerKind, PredicateKind> = {
  on_hfr_above: "hfr_above",
  on_guide_rms_above: "guide_rms_above",
  on_frame_rejected: "frame_rejected",
  on_target_complete: "target_complete",
  at_time: "at_time",
};

export const MIN_TERMS = 2;
export const MAX_TERMS = 8;

/** run_target / skip_target need a destination target name (`target_arg`). */
export function actionNeedsTarget(a: ActionKind): boolean {
  return a === "run_target" || a === "skip_target";
}

export function predicateNeedsThreshold(k: PredicateKind): boolean {
  return k === "hfr_above" || k === "guide_rms_above";
}

export function predicateNeedsTime(k: PredicateKind): boolean {
  return k === "at_time";
}

export function defaultPredicate(kind: PredicateKind = "frame_rejected"): Predicate {
  return { kind, threshold: predicateNeedsThreshold(kind) ? 1 : 0, at_time: null };
}

/** Lossless upgrade: the rule's CURRENT flat trigger becomes term 1, so the
 *  author never loses the condition they already wrote. Defaults to AND. */
export function toCompound(i: Instruction): Condition {
  const first: Predicate = {
    kind: PREDICATE_OF[i.trigger],
    threshold: i.threshold,
    at_time: i.at_time,
  };
  const second = defaultPredicate(
    first.kind === "hfr_above" ? "guide_rms_above" : "hfr_above");
  return { op: "all", terms: [first, second] };
}

/** Back to a single condition: keep term 1, drop the compound. */
export function toFlat(c: Condition): Pick<Instruction, "trigger" | "threshold" | "at_time"> {
  const t = c.terms[0] ?? defaultPredicate();
  const entry = (Object.keys(PREDICATE_OF) as TriggerKind[])
    .find((k) => PREDICATE_OF[k] === t.kind) ?? "on_frame_rejected";
  return { trigger: entry, threshold: t.threshold, at_time: t.at_time };
}

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
    target_arg: null,
    when: null,
  };
}

/** Patch for an action change. Switching TO a jump action seeds a destination
 *  and turns `once` ON (author-level throttle — the server's MAX_JUMPS budget is
 *  the real loop backstop); switching away clears the destination. */
export function actionChangePatch(
  i: Instruction, action: ActionKind, targetNames: string[],
): Partial<Instruction> {
  if (!actionNeedsTarget(action)) {
    return actionNeedsTarget(i.action)
      ? { action, target_arg: null }
      : { action };
  }
  const other = targetNames.find((n) => n !== i.only_target) ?? targetNames[0] ?? null;
  return { action, target_arg: i.target_arg || other, once: true };
}

const HHMM_RE = /^(\d{2}):(\d{2})$/;

function validHhmm(s: string | null): boolean {
  const m = HHMM_RE.exec(s ?? "");
  return !!m && Number(m[1]) < 24 && Number(m[2]) < 60;
}

/** Author-facing validation. Returns a list of problems ([] === valid). */
export function validateInstruction(i: Instruction): string[] {
  const problems: string[] = [];
  const when = i.when ?? null;
  if (when) {
    // compound OVERRIDES the flat trigger, so only the compound is validated.
    if (when.terms.length < MIN_TERMS || when.terms.length > MAX_TERMS) {
      problems.push(`Combined conditions need ${MIN_TERMS}–${MAX_TERMS} conditions.`);
    }
    when.terms.forEach((t, n) => {
      if (predicateNeedsThreshold(t.kind) && !(t.threshold > 0)) {
        problems.push(`Condition ${n + 1}: threshold must be greater than 0.`);
      }
      if (predicateNeedsTime(t.kind) && !validHhmm(t.at_time)) {
        problems.push(`Condition ${n + 1}: time must be in "HH:MM" (24h) form.`);
      }
    });
  } else {
    if (triggerNeedsThreshold(i.trigger) && !(i.threshold > 0)) {
      problems.push("Threshold must be greater than 0.");
    }
    if (triggerNeedsTime(i.trigger) && !validHhmm(i.at_time)) {
      problems.push('Time must be in "HH:MM" (24h) form.');
    }
  }
  if (actionNeedsTarget(i.action) && !(i.target_arg ?? "").trim()) {
    problems.push(`${ACTION_LABELS[i.action]} needs a target to act on.`);
  }
  if (i.action === "notify" && !i.message.trim()) {
    problems.push("Notify needs a message.");
  }
  if (i.cooldown_s < 0) {
    problems.push("Cooldown cannot be negative.");
  }
  return problems;
}

/** One leaf predicate as plain language (no leading "When"). */
export function describePredicate(p: Predicate): string {
  switch (p.kind) {
    case "hfr_above": return `HFR > ${p.threshold}`;
    case "guide_rms_above": return `guide RMS > ${p.threshold}"`;
    case "frame_rejected": return "a frame is rejected";
    case "target_complete": return "target complete";
    case "at_time": return `after ${p.at_time ?? "??:??"}`;
    default: return "?";
  }
}

/** A one-line human summary, e.g. `When HFR > 3.5 → refocus (once) · only M31`. */
export function describeInstruction(i: Instruction, _targetNames?: string[]): string {
  let cond: string;
  const when = i.when ?? null;
  if (when) {
    const joiner = when.op === "all" ? " AND " : " OR ";
    cond = `When ${when.terms.map(describePredicate).join(joiner)}`;
    return finishDescribe(i, cond);
  }
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
  return finishDescribe(i, cond);
}

function finishDescribe(i: Instruction, cond: string): string {
  let s = `${cond} → ${ACTION_LABELS[i.action].toLowerCase()}`;
  if (actionNeedsTarget(i.action)) s += ` ${i.target_arg ?? "?"}`;
  if (i.once) s += " (once)";
  if (i.only_target) s += ` · only ${i.only_target}`;
  return s;
}
