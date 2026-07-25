// instructionSim.ts — pure, client-side DRY RUN for the conditional sequencer.
// "Given this state, which rules would fire and why." Preview ONLY: the server
// evaluator (sequence/instructions.py) stays authoritative at run time.
//
// Deliberate scope (design §4.1): a SINGLE-FRAME, "assuming armed" preview. It
// mirrors the server's gate ORDER (enabled -> only_target -> condition) and the
// compound all/any logic exactly, but it does NOT reconstruct runtime
// edge/arm/cooldown history — a rule that would fire is reported as such with an
// explicit note when `once`/`cooldown_s` could still hold it back. Replicating
// timing here would be a second, drift-prone semantics implementation.
//
// Pure: no React, no clock (the caller passes `now`), fully unit-testable.
import type { Condition, Instruction, Predicate, PredicateKind } from "../types";
import { PREDICATE_OF, describeInstruction } from "./instructions";

export interface SimSnapshot {
  hfr: number | null;
  guideRms: number | null;
  frameRejected: boolean;
  targetComplete: boolean;
  now: string;                      // "HH:MM" 24h local
  activeTarget: string | null;
}

export type SimGate =
  | "disabled"          // rule switched off
  | "only_target"       // gated to a different target
  | "not_met"           // condition is decisively false
  | "needs_input"       // a metric this rule needs is unreadable in the snapshot
  | "invalid";          // the rule itself is incomplete (validation would flag it)

export interface SimOutcome {
  id: string;
  wouldFire: boolean;
  summary: string;                  // human one-liner for the rule
  reason: string;                   // why it fires / why it does not
  gate?: SimGate;                   // present only when wouldFire === false
  note?: string;                    // honest caveat (once / cooldown)
}

/** Honest copy for the preview panel — the preview is a single frame, not a run. */
export const SIM_CAVEAT =
  "Preview assumes each rule is ready to fire — a real run also applies " +
  "edge, once and cooldown timing, so the live result can differ.";

const HHMM_RE = /^(\d{2}):(\d{2})$/;

/** "HH:MM" -> minutes since midnight, or null when malformed/absent. */
export function hhmmMinutes(s: string | null | undefined): number | null {
  const m = HHMM_RE.exec(s ?? "");
  if (!m) return null;
  const h = Number(m[1]);
  const mi = Number(m[2]);
  return h < 24 && mi < 60 ? h * 60 + mi : null;
}

/** One leaf predicate as a tri-state level. null === indeterminate (the metric
 *  is unreadable), mirroring the server's `_eval_predicate`. */
export function evalPredicate(p: Predicate, s: SimSnapshot): boolean | null {
  switch (p.kind as PredicateKind) {
    case "hfr_above":
      return s.hfr === null ? null : s.hfr > p.threshold;
    case "guide_rms_above":
      return s.guideRms === null ? null : s.guideRms > p.threshold;
    case "frame_rejected":
      return s.frameRejected;
    case "target_complete":
      return s.targetComplete;
    case "at_time": {
      const t = hhmmMinutes(p.at_time);
      const now = hhmmMinutes(s.now);
      return t === null || now === null ? null : now >= t;
    }
    default:
      return null;
  }
}

/** 1-level all/any over leaves. Short-circuits exactly like the server: `all`
 *  on any decisive false, `any` on any decisive true; otherwise a `null` term
 *  makes the whole expression indeterminate. */
export function evalCondition(c: Condition, s: SimSnapshot): boolean | null {
  const vals = c.terms.map((t) => evalPredicate(t, s));
  if (c.op === "all") {
    if (vals.some((v) => v === false)) return false;
    return vals.some((v) => v === null) ? null : true;
  }
  if (vals.some((v) => v === true)) return true;
  return vals.some((v) => v === null) ? null : false;
}

function conditionOf(i: Instruction): Condition {
  // `when` OVERRIDES the flat trigger (same rule as the server).
  if (i.when) return i.when;
  return {
    op: "all",
    terms: [{ kind: PREDICATE_OF[i.trigger], threshold: i.threshold, at_time: i.at_time }],
  };
}

/** Single-frame preview of every rule against one snapshot. Gate order mirrors
 *  the server: enabled -> only_target -> condition. */
export function simulateInstructions(
  rules: Instruction[], s: SimSnapshot,
): SimOutcome[] {
  return rules.map((i, idx) => {
    const id = i.id ?? `rule-${idx}`;
    const summary = describeInstruction(i);
    if (!i.enabled) {
      return { id, wouldFire: false, summary, gate: "disabled" as const,
               reason: "rule is switched off" };
    }
    if (i.only_target !== null && i.only_target !== s.activeTarget) {
      return { id, wouldFire: false, summary, gate: "only_target" as const,
               reason: `only runs while ${i.only_target} is the active target` };
    }
    if ((i.action === "run_target" || i.action === "skip_target")
        && !(i.target_arg ?? "").trim()) {
      return { id, wouldFire: false, summary, gate: "invalid" as const,
               reason: "no target chosen for the jump" };
    }
    const v = evalCondition(conditionOf(i), s);
    if (v === null) {
      return { id, wouldFire: false, summary, gate: "needs_input" as const,
               reason: "waiting on a reading this rule needs" };
    }
    if (!v) {
      return { id, wouldFire: false, summary, gate: "not_met" as const,
               reason: "condition not met" };
    }
    const throttled = i.once || i.cooldown_s > 0;
    return {
      id, wouldFire: true, summary, reason: "condition met",
      ...(throttled
        ? { note: i.once
              ? "fires once per run — skipped if it already fired"
              : `subject to the ${i.cooldown_s}s cooldown` }
        : {}),
    };
  });
}

/** A sensible starting snapshot so the preview renders instantly with zero
 *  configuration (the novice one-tap path). */
export function defaultSnapshot(
  targetNames: string[], now: string = "22:00",
): SimSnapshot {
  return {
    hfr: 3, guideRms: 0.8, frameRejected: false, targetComplete: false,
    now, activeTarget: targetNames[0] ?? null,
  };
}
