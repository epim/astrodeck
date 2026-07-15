// sessions.ts — pure helpers for the Sessions cards (sessions spec §7).
// No store/server access: plain data in, plain data out (tsx-testable).
import type { SequencePlan, Session, SessionFrame } from "../types";

export interface TargetProgress {
  target_id: string;
  name: string;
  accepted: number;   // effective-accepted, capped per step at its count
  total: number;
}

/** Per-target accepted/total bars off the frozen session plan + ledger. */
export function targetProgress(plan: SequencePlan, frames: SessionFrame[]): TargetProgress[] {
  const byStep = new Map<string, number>();
  for (const f of frames) {
    const ok = f.override != null ? f.override === "accept" : f.auto_accepted;
    if (ok) byStep.set(f.step_id, (byStep.get(f.step_id) ?? 0) + 1);
  }
  return plan.targets.map((t) => ({
    target_id: t.id ?? t.name,
    name: t.name,
    accepted: t.steps.reduce(
      (a, s) => a + Math.min(s.count, byStep.get(s.id ?? "") ?? 0), 0),
    total: t.steps.reduce((a, s) => a + s.count, 0),
  }));
}

export interface MergePreview {
  kept: number;     // step ids present in BOTH plans (progress survives)
  added: number;    // new step ids (start at zero)
  dropped: number;  // old frame-BEARING step ids no longer in the plan
}

/** kept/added/dropped step-id diff shown BEFORE "Update from Plan" (spec §4/§7). */
export function mergePreview(session: Session, next: SequencePlan): MergePreview {
  const oldIds = new Set(session.plan.targets.flatMap((t) => t.steps.map((s) => s.id ?? "")));
  const newIds = new Set(next.targets.flatMap((t) => t.steps.map((s) => s.id ?? "")));
  const withFrames = new Set(session.frames.map((f) => f.step_id));
  let kept = 0;
  let added = 0;
  let dropped = 0;
  for (const id of newIds) {
    if (oldIds.has(id)) kept++;
    else added++;
  }
  for (const id of oldIds) {
    if (!newIds.has(id) && withFrames.has(id)) dropped++;
  }
  return { kept, added, dropped };
}
