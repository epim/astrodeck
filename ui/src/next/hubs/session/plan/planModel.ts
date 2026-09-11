// planModel.ts - what every piece of the rebuilt plan editor agrees on.
//
// Pure except for the two hooks at the bottom, both of which only wrap a fetch
// the legacy view already made. Nothing here re-derives a number that
// `ui/src/lib/*` or `components/sequence/stepDefaults.ts` already computes -
// this module holds the handful of decisions that lived INSIDE
// `views/SequenceView.tsx`'s render and had nowhere else to go.
//
// THREE THINGS IN HERE ARE CORRECTIONS, NOT TRANSCRIPTIONS.
//
// 1. `holding` IS A LIVE RUN. `SequenceView.tsx:355` reads
//    `running || paused || aborting` and omits it, but `types.ts:600-607` says
//    the engine PROMOTES a routine `running` publish to `holding` for the whole
//    of a cloud hold - so on the one night the sky closes, the legacy editor
//    unlocked the target list, re-enabled the catalog search and dropped the
//    "plan structure is locked" note over a run that is still the engine's.
//    `hubs/session/now/RunControls.tsx` already treats it as live; so does this.
//
// 2. THE RUN-LOCK NOTES ARE PHRASED FOR `LockNote`. That primitive prints
//    `Read-only - <reason>`, so each reason starts lower-case and states the
//    lock, not the sentence around it. The clause set is the legacy note's,
//    with the em-dashes and curly quotes replaced (copy rule: hyphens only).
//
// 3. THE STATE WORD IS A SHAPE AND A WORD, never a colour. `PAUSING` is not an
//    engine state at all: it is the window between the pause landing and the
//    open shutter closing, which the engine cannot report and the client has to
//    work out (see `useSubFrame`). Someone deciding whether to walk out to the
//    scope reads that word.

import { useEffect, useState } from "react";

import { api } from "../../../../api";
import type { SequencePlan, SequenceState, Target } from "../../../../types";
import type { Tone } from "../../../ui/types";

// ------------------------------------------------------------------- states

/** Every state in which the run still belongs to the engine. `holding` is in
 *  here for the reason at the top of the file; `aborting` because the wind-down
 *  is minutes long and the rig has not stopped. */
export const LIVE_SEQUENCE_STATES: readonly SequenceState["state"][] =
  ["running", "holding", "paused", "aborting"];

export function isSequenceLive(state: SequenceState["state"]): boolean {
  return LIVE_SEQUENCE_STATES.includes(state);
}

export function isSequenceFinished(state: SequenceState["state"]): boolean {
  return state === "complete" || state === "error" || state === "aborted";
}

export interface StateView {
  word: string;
  tone: Tone;
  /** The 1.4 s dot pulse: something is still HAPPENING. It is the channel that
   *  survives a red-light screen where hue barely reads. */
  pulse: boolean;
}

/** `SeqStateBadge`'s table (`SequenceView.tsx:52-75`), plus `holding`, which the
 *  legacy badge fell through to its `state.toUpperCase()` default for. */
export function stateView(state: SequenceState["state"], pausing: boolean): StateView {
  if (pausing) return { word: "PAUSING", tone: "warn", pulse: true };
  switch (state) {
    case "running": return { word: "RUNNING", tone: "good", pulse: true };
    case "holding": return { word: "HOLDING", tone: "warn", pulse: true };
    case "paused": return { word: "PAUSED", tone: "warn", pulse: false };
    case "aborting": return { word: "ABORTING", tone: "warn", pulse: true };
    case "complete": return { word: "COMPLETE", tone: "good", pulse: false };
    case "error": return { word: "ERROR", tone: "bad", pulse: false };
    case "aborted": return { word: "ABORTED", tone: "bad", pulse: false };
    case "nina_native": return { word: "NINA IS DRIVING", tone: "info", pulse: true };
    default: return { word: state.toUpperCase(), tone: "accent", pulse: false };
  }
}

// -------------------------------------------------------------------- totals

export function targetFrames(t: Target): number {
  return t.steps.reduce((b, s) => b + s.count, 0);
}

export function targetSeconds(t: Target): number {
  return t.steps.reduce((b, s) => b + s.count * s.exposure_s, 0);
}

export function planFrames(plan: SequencePlan): number {
  return plan.targets.reduce((a, t) => a + targetFrames(t), 0);
}

export function planSeconds(plan: SequencePlan): number {
  return plan.targets.reduce((a, t) => a + targetSeconds(t), 0);
}

/** `Nh Mm` from seconds, the whole-minute rounding the server's
 *  `plans.py::_summarize` and the legacy panel both use. */
export function fmtHoursMinutes(seconds: number): string {
  const min = seconds / 60;
  return `${Math.floor(min / 60)}h ${Math.round(min % 60)}m`;
}

// -------------------------------------------------------------- mosaic blocks

export type PlanBlock =
  | { kind: "single"; ti: number }
  | { kind: "group"; group: string; members: number[] };

/** Partition the ORDERED target list into render blocks: a lone target, or a
 *  run of CONSECUTIVE targets sharing one `mosaic_group`. Adjacency is the
 *  grouping rule (not a map keyed by name) because "Order by tonight" returns a
 *  group-atomic permutation and the blocks have to follow it. */
export function planBlocks(targets: Target[]): PlanBlock[] {
  const blocks: PlanBlock[] = [];
  targets.forEach((t, ti) => {
    const g = t.mosaic_group;
    const prev = blocks[blocks.length - 1];
    if (g && prev && prev.kind === "group" && prev.group === g) prev.members.push(ti);
    else if (g) blocks.push({ kind: "group", group: g, members: [ti] });
    else blocks.push({ kind: "single", ti });
  });
  return blocks;
}

// ------------------------------------------------------------ run-lock notes
//
// Three sentences, one per region, each of them a claim the code must keep. The
// first draft of the legacy version failed that twice - it said targets "can't
// be added" while the search in the same panel added them - so every clause
// below is enforced by the component that renders it.

/** `SequenceView.tsx:1052-1057`, over the target list. */
export function structureLockNote(planName: string): string {
  return `plan structure is locked while "${planName}" runs - the server is executing `
    + `the copy of the plan it took at Run, so nothing here adds or removes a target, a `
    + `step or a template, the catalog search is off, and per-target scheduling is frozen. `
    + `Frame type, filter, exposure, gain, binning and count stay editable and apply to `
    + `your next run, not this one.`;
}

/** `SequenceView.tsx:1496-1499`, over the automation column. */
export function automationLockNote(planName: string): string {
  return `these settings apply to your NEXT run, not "${planName}" - the server is `
    + `executing the copy of the plan it took at Run, so changing anything here, `
    + `including the safety monitor gate, does not reach the run in progress. Stop it `
    + `and start it again to run under new settings.`;
}

/** `SequenceView.tsx:1758-1759`, over the when/then rules. */
export function instructionsLockNote(planName: string): string {
  return `rules apply to your next run, not "${planName}" - the server is executing the `
    + `copy of the plan it took at Run.`;
}

/** The name a lock note calls the run in progress. The engine's own
 *  `plan_name` first: the plan in the editor may already have been renamed. */
export function runningPlanName(seqPlanName: string | undefined, draftName: string): string {
  return seqPlanName ?? draftName ?? "A plan";
}

// ------------------------------------------------------------------- recovery

export interface Recoverable {
  /** The dormant session the offer is about. `PATCH /api/sessions/{id}` with
   *  `status: "abandoned"` is what DISCARDS it - a real server verb, not an
   *  invented one, and the only way to stop an offer the user does not want
   *  from reappearing every time the editor opens. */
  sessionId: string | null;
  name: string;
  frames_done: number;
  frames_total: number;
  /** When the run stopped, epoch seconds, or null on an older server. */
  ts: number | null;
}

/** `GET /api/sequence/recoverable`, re-asked on the running EDGE and nowhere
 *  else: the answer only changes when a run starts or stops. A failure leaves
 *  it null - an older server, or none, simply shows no resume card rather than
 *  an error the user cannot act on. */
export function useRecoverable(running: boolean): {
  rec: Recoverable | null;
  /** Drop the offer locally once it has been resumed or discarded, so the card
   *  goes away without waiting for the next `running` edge. */
  clear: () => void;
} {
  const [rec, setRec] = useState<Recoverable | null>(null);
  useEffect(() => {
    if (running) { setRec(null); return; }
    let alive = true;
    api.get<{
      recoverable: boolean; session_id?: string; name?: string;
      frames_done?: number; frames_total?: number; ts?: number;
    }>("/api/sequence/recoverable").then(
      (r) => {
        if (!alive) return;
        setRec(r.recoverable
          ? {
            sessionId: r.session_id ?? null,
            name: r.name ?? "The last run",
            frames_done: r.frames_done ?? 0,
            frames_total: r.frames_total ?? 0,
            ts: r.ts ?? null,
          }
          : null);
      },
      () => { /* no server, or one too old to answer: no card */ },
    );
    return () => { alive = false; };
  }, [running]);
  return { rec, clear: () => setRec(null) };
}

// ------------------------------------------------------------- reversible edit

export interface UndoState { label: string; prev: SequencePlan }

/** How long a reversible plan edit can be taken back. Instant-apply plus this
 *  window, never a hold: a delete you can undo is not a destructive action, and
 *  making it one costs a second of hold on every routine edit. */
export const UNDO_MS = 5000;
