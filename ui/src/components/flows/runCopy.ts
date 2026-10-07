// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// runCopy.ts - what the RUN button says and what the run readouts show, read
// off the two answers the run-mode surfaces hold (#189 S5; spec 5.9 button
// copy, 5.10 published state and ETA, U-07).
//
// ONE COPY, FOUR SURFACES. The classic header, the classic phone MONITOR tab,
// the #/next canvas toolbar and the #/next phone stage sheet each carry a RUN
// button and (three of them) a set of readouts. Each surface used to spell
// "RUN" / "STOP" for itself and read `flows.run`, which nothing on the server
// writes, so every readout sat at IDLE / - / - / 0 through a real run. Both
// answers now come from here, through `useFlowRunControls` and
// `useFlowRunReadouts` (flowRunControls.tsx), so the four cannot word a run
// differently.
//
// THE BUTTON IS READ FROM THE PROGRESS ROUTE ALONE. `GET /api/flows/{id}/
// progress` names the session Run would continue (server `current_for_flow`,
// the method `run_flow` itself calls) and counts it by that session's own
// count mode against the SAVED graph, which is what RUN compiles. So the
// numbers on the button are the numbers CONTINUE will carry on from. The
// graph on screen is the wrong source twice over: it may hold unsaved edits
// RUN will not start, and it knows nothing of what the session banked.
//
// THE READOUTS ARE READ FROM THE SEQUENCE STATE, and only while that run is
// this flow's (`flowRunLive`: the session the run writes is one the slice
// knows as the flow's). A run of another flow, or of a plan, is not this
// flow's run, and its target, frames and clock on this flow's monitor would
// be a claim about the wrong ledger. Whose run it is was read off the
// progress answer until #449, and every save blanked that answer for a round
// trip: the monitor dropped to IDLE in the middle of a run and came back.
//
// NOTHING SITE-DERIVED OF THEIR OWN, AND THE ETA CARRIES #166 ITEM 1. The
// published state carries `live.meridian_eta_s` and a meridian wait's
// `schedule.reason`, and for an operator the panel and pass across that wait:
// each timestamps a transit, which gives away the site's longitude (spec
// 6.9). The readouts read none of them. Across a meridian wait the stage
// names the mosaic and the wait, the same words for an operator and a viewer.
// The ETA they do print is the engine's `progress.eta_s`, and that is not
// clean: `compute_eta` adds a flip's cost while a flip falls inside the run,
// so the number steps when the flip is taken, which is #166 item 1, still
// open. It is the field the Monitor's own ETA already prints, so the four
// surfaces add places that show it and no new channel, and they inherit
// whatever #166's fix does to it, with no change here (#510, B16).
//
// Pure: no store, no React, no clock. Each answers from its arguments alone.
import type { FlowProgress } from "../../lib/flowsApi";
import type { SequenceState } from "../../types";
import { flowRunLive } from "./flowRunState";
import type { FlowGraphRec } from "./flowsTypes";
import { duskAutoResume } from "./nodeDefs";

// ------------------------------------------------------------- the button

/** The three things RUN can do. The verb is the word the button leads with. */
export type RunVerb = "RUN" | "CONTINUE" | "STOP";

/** What the RUN button says, in parts, because two of them behave differently
 *  at 390 px: the flow's name may be cut with an ellipsis and the
 *  parenthetical may not, since it carries the numbers being continued. */
export interface RunCopy {
  verb: RunVerb;
  /** The flow's name in the button's capitals, on CONTINUE only; "" otherwise. */
  name: string;
  /** "(night 3, 412/1890 subs)" on CONTINUE when the route's numbers are
   *  readable; "(412/1890 subs)" when the route names no night (a server
   *  older than H4); "" otherwise. */
  detail: string;
  /** The whole line, for an accessible name and a tooltip. */
  text: string;
  /** The numbers `detail` is made of: `night` null when the route named
   *  none, and all three null when there is no detail. */
  night: number | null;
  banked: number | null;
  total: number | null;
  /** What RUN says about a flow whose DUSK WINDOW has Automatic resume Off:
   *  `RESUME_OFF_LINE`, on RUN and CONTINUE and never on STOP, and "" for a
   *  flow that resumes (and for a caller that did not say, which is the
   *  default). It is a line BESIDE the button, never part of `text`: the
   *  button says what the press does, this says what the NEXT night will not. */
  notice: string;
}

/** The sentence RUN says about a flow whose Automatic resume is Off (#195,
 *  WP-118; spec 2.4, Revision 2 ruling 7): the engine disarms such a session
 *  where its night ends, so a later night does not start it, and the operator
 *  who pressed RUN should not find that out at dusk. It says the two things
 *  Tonight's copy says (`tonight._NO_LATER_RESUME`, "a subsequent night does
 *  not resume by itself; CONTINUE it by hand") and the Target modal's
 *  campaign line says in the same words as this one
 *  (`framingApi.campaignLine`): a later night does not resume the flow, and
 *  CONTINUE is the way. A same-night crash or restart still resumes, which is
 *  the engine's separate promise and not this line's to restate. */
export const RESUME_OFF_LINE =
  "Automatic resume is off for this flow, so a subsequent night does not resume it by itself: CONTINUE it by hand.";

/** Whether the flow on screen resumes on subsequent nights, read off its
 *  first DUSK WINDOW as the compile reads it (nodes.py `dusk_auto_resume`, here
 *  `duskAutoResume`): false only for an explicit "Off" in DUSK WINDOW's
 *  `autoResume`. TRUE for a flow with no DUSK WINDOW and for no graph at all,
 *  because a flow that says nothing resumes, and a notice that appeared for the
 *  absence of an answer would be a claim about a choice nobody made. */
export function graphResumes(graph: FlowGraphRec | null | undefined): boolean {
  const dusk = graph?.nodes?.find((n) => n.type === "dusk");
  return dusk ? duskAutoResume(dusk.params) : true;
}

const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** The RUN button's copy.
 *
 *  STOP while `live` (the engine owns the rig for this flow): the button then
 *  aborts, whatever the progress route says.
 *
 *  CONTINUE when the route names a DORMANT session, the one case in which
 *  `run_flow` continues rather than starts fresh (server `run_flow`: "it is
 *  continued only if it is dormant"). THE NIGHT IS THE RUN ROUTE'S OWN
 *  (#511, H4): the route's `continue_night`, the night a CONTINUE pressed
 *  now would start, which the progress route answers from its own clock
 *  through `Session.night_at`, the rule the run route answers `night` by.
 *  So a second CONTINUE in the same evening reads the night the log is
 *  already writing, and the next evening one more. It reads THE
 *  OBSERVING-NIGHT COUNT (#430, S7 orchestrator ruling 7) underneath: a
 *  restart in the same night is the same night. Until H4 the button printed
 *  the route's `nights` plus one, because the route sent no clock, and on a
 *  night the session had already run it read one more than the run route
 *  answered. A route that names no night (a server older than H4) gets no
 *  night number on the button, never a guessed one: the counts are printed
 *  alone. The counts are the blocks' `banked` over their `total`, summed
 *  over the blocks and nothing else. A block's sums already hold its
 *  panels, so adding the panels again would count every sub twice; skipped
 *  panels and orphaned frames are in no block's sums because they fill no
 *  quota.
 *
 *  RUN otherwise: no answer yet, no session (never run, or the newest was
 *  abandoned: the route answers null for both), a complete session, or an
 *  active one that is not live here. `run_flow` starts all of those fresh.
 *
 *  A dormant session whose numbers do not read as numbers still reads
 *  CONTINUE, since that is what the press does, but with no parenthetical:
 *  numbers the server did not send are not put on the button.
 *
 *  `resumes` is whether the flow's DUSK WINDOW has Automatic resume on
 *  (`graphResumes`, default true: a caller that does not say reads a flow that
 *  resumes, as every flow did before the option). False puts `RESUME_OFF_LINE`
 *  in `copy.notice` on RUN and CONTINUE. The button's own `text` never changes
 *  with it. */
export function runCopy(flowName: string, progress: FlowProgress | null | undefined,
                        live: boolean, resumes: boolean = true): RunCopy {
  // The notice is said for the two verbs that START something, never for
  // STOP, and only for a flow that said Off (`graphResumes`).
  const notice = resumes ? "" : RESUME_OFF_LINE;
  const plain = (verb: RunVerb): RunCopy =>
    ({ verb, name: "", detail: "", text: verb, night: null, banked: null, total: null,
       notice: verb === "STOP" ? "" : notice });
  if (live) return plain("STOP");
  const session = progress?.session;
  if (!session || session.status !== "dormant") return plain("RUN");

  const name = (flowName ?? "").trim().toUpperCase();
  const blocks = Array.isArray(progress?.blocks) ? progress!.blocks : [];
  let banked = 0;
  let total = 0;
  let readable = true;
  for (const b of blocks) {
    if (!finite(b?.banked) || !finite(b?.total)) { readable = false; break; }
    banked += b.banked;
    total += b.total;
  }
  // The route's own number or none: never `nights` plus one (#511).
  const night = readable && finite(session.continue_night) ? session.continue_night : null;
  const counts = `${banked}/${total} subs`;
  const detail = !readable ? ""
    : night === null ? `(${counts})` : `(night ${night}, ${counts})`;
  return {
    verb: "CONTINUE",
    name,
    detail,
    text: ["CONTINUE", name, detail].filter((p) => p !== "").join(" "),
    night,
    banked: readable ? banked : null,
    total: readable ? total : null,
    notice,
  };
}

/** START OVER's confirm title: the decision, not a restatement of the body. */
export const START_OVER_TITLE = "Start this flow over?";

/** START OVER's confirm body, from the copy CONTINUE shows beside it.
 *
 *  What the press really does (spec 5.9): a fresh session, with the dormant
 *  one left on disk, unarmed, and never reopened by CONTINUE, because
 *  CONTINUE reads only the newest session this flow started. Its numbers are
 *  the ones on the CONTINUE button, so the operator sees what they are
 *  walking away from. */
export function startOverBody(copy: RunCopy): string {
  const held = copy.banked !== null && copy.total !== null
    ? ` It holds ${copy.banked} of ${copy.total} subs.` : "";
  // The new session is the Off flow's too: it will not come back on a
  // subsequent night either, and this is the last thing said before it starts.
  const off = copy.notice !== "" ? ` ${copy.notice}` : "";
  return "START OVER begins a new session that counts from 0 and leaves this one on disk,"
    + ` where CONTINUE will not go back to it.${held}${off}`;
}

// ----------------------------------------------------------- the readouts

/** The ETA readout's words while the finish clock prices hops it has not
 *  measured (server `compute_eta`, `hops_costed` false; spec 5.10, U-07). */
export const HOPS_NOT_COSTED = "hops not yet costed";

/** The stage while a mosaic waits on the meridian rule. No countdown and no
 *  panel: the wait's end is the crossing, which is site-derived (6.9). */
export const MERIDIAN_WAIT_STAGE = "waiting for the meridian";

/** The flows vocabulary for the engine's live states: a wind-down from an
 *  abort is STOPPING, the word the flow's own run phase uses, because the rig
 *  is still moving. */
const STATE_WORD: Record<string, string> = {
  running: "RUNNING", holding: "HOLDING", paused: "PAUSED", aborting: "STOPPING",
};

/** What the readouts show when no run of this flow is live: the store's own
 *  `flows.run`, exactly as the surfaces printed it before S5. */
export interface RunIdle {
  phase: string;
  etaS: number | null;
  curStage: string;
  frames: number;
  frameGoal: number | null;
}

export interface RunReadouts {
  /** True when every value below came from the sequence state. */
  fed: boolean;
  /** The STATE word, upper-cased. */
  state: string;
  /** Seconds to finish as the rig computed them, or null. Each surface
   *  formats it with its own dash for "not said". */
  etaS: number | null;
  /** `HOPS_NOT_COSTED` while the clock prices unmeasured hops, else null. */
  etaNote: string | null;
  stage: string;
  frames: number;
  frameGoal: number | null;
}

/** The STAGE readout for a live run.
 *
 *  A mosaic (`state.group`, published only while the target belongs to one)
 *  reads "M31 2-3 · pass 2": the group's name, the panel and the pass, each
 *  left out when the engine did not send it (the panel is null before a
 *  group's first visit). Across a meridian wait it reads "M31 · waiting for
 *  the meridian", and never the panel or pass an operator is still served:
 *  the panel a waiting group names is the one it last shot or the one it
 *  will hop to at the crossing, and printing either would give an operator's
 *  screen a transit time a viewer's is denied.
 *
 *  A single target reads the target the engine names. Null when it names
 *  nothing. */
export function runStage(seq: SequenceState): string | null {
  const g = seq.group;
  if (g && typeof g === "object") {
    const name = typeof g.name === "string" ? g.name.trim() : "";
    if (g.meridian_wait) return [name, MERIDIAN_WAIT_STAGE].filter((p) => p !== "").join(" · ");
    const where = [name, typeof g.panel === "string" ? g.panel.trim() : ""]
      .filter((p) => p !== "").join(" ");
    const pass = finite(g.pass) ? `pass ${g.pass}` : "";
    const line = [where, pass].filter((p) => p !== "").join(" · ");
    return line === "" ? null : line;
  }
  const target = typeof seq.target === "string" ? seq.target.trim() : "";
  return target === "" ? null : target;
}

/** Who `runReadouts` asks about the run: the sessions known to be the open
 *  flow's (flowRunState `knownSessions`), which is what every store reader
 *  passes (flowRunControls `useFlowRunReadouts`).
 *
 *  A LIST, NEVER A PROGRESS ANSWER. Until S7 a progress answer was accepted
 *  here too, standing for the one session it names, only so runCopy.test.ts
 *  compiled across the #449 change; that test now passes the list. No store
 *  reader may ask `flows.progress` whose run it is: a save blanks it for a
 *  round trip, which is the defect #449 names. */
export type RunSessions = readonly string[] | null | undefined;

/** The four readouts (STATE, ETA, STAGE, FRAMES), fed from the sequence state
 *  while `flowRunLive` says the rig's run is this flow's, one of `known`'s
 *  sessions (see `RunSessions`), and the idle values (`flows.run`)
 *  otherwise. */
export function runReadouts(known: RunSessions,
                            sequence: SequenceState | null | undefined,
                            idle: RunIdle): RunReadouts {
  if (!sequence || !flowRunLive(known, sequence)) {
    return {
      fed: false,
      state: String(idle.phase ?? "idle").toUpperCase(),
      etaS: idle.etaS,
      etaNote: null,
      stage: idle.curStage,
      frames: idle.frames,
      frameGoal: idle.frameGoal,
    };
  }
  const p = sequence.progress;
  return {
    fed: true,
    state: STATE_WORD[sequence.state] ?? String(sequence.state).toUpperCase(),
    etaS: finite(p?.eta_s) && p!.eta_s! >= 0 ? p!.eta_s! : null,
    // Only an explicit false: a server older than U-07 sends no flag, and its
    // silence is not a claim that the hops are unpriced.
    etaNote: p?.hops_costed === false ? HOPS_NOT_COSTED : null,
    stage: runStage(sequence) ?? idle.curStage,
    frames: finite(p?.frames_done) ? p!.frames_done : 0,
    frameGoal: finite(p?.frames_total) ? p!.frames_total : null,
  };
}
