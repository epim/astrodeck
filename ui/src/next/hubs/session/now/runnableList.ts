// runnableList.ts - one ordered list of everything a phone can START tonight:
// saved flows AND saved plans, in the order the operator is likeliest to want.
//
// WHY PLANS ARE ON IT (D-FU-3, wave plan section 0 Q3). The plan editor is
// honest-disabled at phone width (`PLAN_EDITOR_PHONE_REASON`), so before this
// list a phone had no way to run a saved plan at all - the gap the decision
// exists to close. A flow and a plan are different documents on different
// routes (`POST /api/flows/{id}/run` vs `POST /api/sequence/start`) but they are
// the same QUESTION on this screen, so they share one list and one ordering.
//
// NOTHING IS DE-DUPLICATED. A flow and a plan may carry the same name - people
// build the plan first and wire the flow after - and dropping either one would
// hide a document the operator saved. `kind` disambiguates them on screen; the
// two rows start two different things and both stay.
//
// THE SORT KEY IS A STRING BECAUSE THE ORDER IS TWO ORDERS. Flows come first,
// most-recently-run first, ties broken on `updated_ts`; plans follow,
// alphabetically. Neither half is expressible as one number, so `sortKey`
// encodes the whole thing lexicographically and `buildRunnables` sorts on it
// alone - which is what lets a test assert the order from the rows themselves
// rather than re-deriving it from the inputs.

import type { FlowCard } from "../../../../lib/flowsApi";
import type { PlanRow } from "../../../../api/plans";
import { fmtDuration } from "../../../lib/format";

export type RunnableKind = "flow" | "plan";

/** LIVE navigates to the running session; RESUME picks an armed session back
 *  up; RUN starts. There is no STOP here - a list row never stops a run
 *  (`FlowsScreen.tsx:123-127`). */
export type RunnableVerb = "RUN" | "RESUME" | "LIVE";

export interface Runnable {
  kind: RunnableKind;
  id: string;
  name: string;
  /** The second line: what this document is, in numbers the name does not
   *  carry. Never a caption narrating the row. */
  meta: string;
  verb: RunnableVerb;
  /** Lexicographic; see the header. Compare with `<`, never `localeCompare`
   *  (a locale-dependent order would make the list machine-dependent). */
  sortKey: string;
}

/** The armed half of `/api/sequence/resume-arm`, narrowed to what the verb
 *  needs. Taking the fields rather than the whole state keeps this module pure
 *  and testable with a two-key literal. */
export interface ArmedOrigin {
  origin: string;
  origin_id: string;
}

export interface RunnableInputs {
  cards: FlowCard[];
  plans: PlanRow[];
  /** `sequence.state`. Anything but "idle" means the engine owns the rig. */
  seqState: string;
  /** The flow the running campaign came from, or null. */
  campaignFlowId: string | null;
  armed: ArmedOrigin | null;
}

/** Big enough that no real `last_run`/`updated_ts` (unix seconds) reaches it,
 *  and small enough that `String()` never switches to exponential notation. */
const RECENCY_BASE = 1e15;

/** A unix-seconds instant as a fixed-width string that sorts NEWEST FIRST. */
function recencyKey(n: number | null | undefined): string {
  const v = typeof n === "number" && Number.isFinite(n) ? Math.round(n) : 0;
  const clamped = Math.max(0, Math.min(RECENCY_BASE, v));
  return String(RECENCY_BASE - clamped).padStart(16, "0");
}

/** "3 targets · 240 frames · 8h 0m", or as much of it as the row carries.
 *
 *  `integration_min` is the server's own field (`plans.py:55`, whole-minute
 *  rounded to one decimal); a row that arrived without it prints the frames and
 *  stops rather than showing "0h 0m", which would read as a plan with no
 *  exposures in it. */
export function planMeta(row: PlanRow): string {
  const parts: string[] = [];
  const targets = Number.isFinite(row.targets) ? row.targets : null;
  const frames = Number.isFinite(row.frames) ? row.frames : null;
  const mins = Number.isFinite(row.integration_min) ? row.integration_min : null;
  if (targets !== null) parts.push(`${targets} ${targets === 1 ? "target" : "targets"}`);
  if (frames !== null) parts.push(`${frames} ${frames === 1 ? "frame" : "frames"}`);
  if (mins !== null && mins > 0) parts.push(fmtDuration(mins * 60));
  return parts.length > 0 ? parts.join(" · ") : "saved plan";
}

/** The flow's own one-liner, or the shape of its graph when it has none.
 *  `FlowsScreen` shows the same two fallbacks. */
export function flowMeta(card: FlowCard): string {
  const tagline = typeof card.tagline === "string" ? card.tagline.trim() : "";
  if (tagline) return tagline;
  const stages = Number.isFinite(card.stages) ? card.stages : 0;
  return `${stages} ${stages === 1 ? "stage" : "stages"}`;
}

/**
 * Build the ordered list.
 *
 * `seqState !== "idle"` plus a matching `campaignFlowId` is the only thing that
 * makes a row LIVE. It is deliberately narrower than "a run exists": this list
 * only renders when nothing is running, and the row that claims to be the live
 * one has to be the one the engine actually came from.
 *
 * PLANS NEVER READ "RESUME". `/api/sequence/resume-arm` stamps `origin: "plan"`
 * with an EMPTY `origin_id` (the engine's plan path has no library id to record
 * - `app.py:6627` starts the plan it was handed), so a plan row cannot know
 * whether the armed session is its own. Offering RESUME on a guess would resume
 * somebody else's session; the armed session is picked up from the empty card
 * above the list, which names it.
 */
export function buildRunnables(inp: RunnableInputs): Runnable[] {
  const cards = Array.isArray(inp.cards) ? inp.cards : [];
  const plans = Array.isArray(inp.plans) ? inp.plans : [];
  const engineBusy = inp.seqState !== "idle";
  const armed = inp.armed;

  const out: Runnable[] = [];

  for (const card of cards) {
    if (!card || typeof card.id !== "string" || card.id === "") continue;
    const live = engineBusy && inp.campaignFlowId === card.id;
    const resumable = !live
      && armed != null && armed.origin === "flow" && armed.origin_id === card.id;
    out.push({
      kind: "flow",
      id: card.id,
      name: card.name,
      meta: flowMeta(card),
      verb: live ? "LIVE" : resumable ? "RESUME" : "RUN",
      sortKey: `0:${recencyKey(card.last_run)}:${recencyKey(card.updated_ts)}`,
    });
  }

  for (const row of plans) {
    if (!row || typeof row.id !== "string" || row.id === "") continue;
    const name = typeof row.name === "string" ? row.name : "";
    out.push({
      kind: "plan",
      id: row.id,
      name,
      meta: planMeta(row),
      verb: "RUN",
      // The id rides along so two plans with the same name keep a stable order
      // instead of swapping places between renders.
      sortKey: `1:${name.toLowerCase()}:${row.id}`,
    });
  }

  out.sort((a, b) => (a.sortKey < b.sortKey ? -1 : a.sortKey > b.sortKey ? 1 : 0));
  return out;
}
