// ids.ts — client-side stable-id generation + backfill (sessions spec §1).
// Every create path assigns ids at creation time; ensurePlanIds is the safety
// net in loadPlan/setPlan that backfills legacy localStorage plans.
import type { ExposureStep, SequencePlan, Target } from "../types";

/** 32-char lowercase hex (matches server uuid4().hex). crypto.randomUUID when
 *  available; Math.random fallback for old WebViews. */
export function uid(): string {
  const c = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto;
  if (c?.randomUUID) return c.randomUUID().replace(/-/g, "");
  let out = "";
  for (let i = 0; i < 32; i++) out += "0123456789abcdef"[Math.floor(Math.random() * 16)];
  return out;
}

export function ensureStepId(s: ExposureStep): ExposureStep {
  return s.id ? s : { ...s, id: uid() };
}

export function ensureTargetIds(t: Target): Target {
  const steps = t.steps.map(ensureStepId);
  const changed = !t.id || steps.some((s, i) => s !== t.steps[i]);
  return changed ? { ...t, id: t.id ?? uid(), steps } : t;
}

/** Backfill missing target/step ids. Reference-preserving: returns the SAME
 *  object when nothing was missing (no spurious re-renders / storage churn). */
export function ensurePlanIds(p: SequencePlan): SequencePlan {
  const targets = p.targets.map(ensureTargetIds);
  const changed = targets.some((t, i) => t !== p.targets[i]);
  return changed ? { ...p, targets } : p;
}
