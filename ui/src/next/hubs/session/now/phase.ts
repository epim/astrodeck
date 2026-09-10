// phase.ts - the run's phase pill, as one pure function (plan section A.1).
//
// PURE ON PURPOSE. The pill is the single most-read thing on the screen and it
// is read in the dark, at 3am, to decide whether to go back to bed. Every input
// it needs is a plain field, so the whole ladder is testable without a store, a
// socket or a DOM - and so the Now screen and the desktop SessionColumn cannot
// end up with two answers to "what is the rig doing".
//
// THE README SHOWS SIX PILLS; THE ENGINE PUBLISHES ELEVEN STATES (deviation D1).
// SOLVING, COOLING, WAITING, STOPPING, ERROR and NINA IS DRIVING are additions,
// and they are not decoration: `engine.py` really does sit in a cooling gate
// (`detail` "cooling to -10C"), really does publish `aborting` for the whole
// ~210 s teardown, and really does hand the rig to NINA. A pill that read
// CAPTURING through a cooling gate would be the UI making something up.
//
// ORDER IS THE WHOLE ALGORITHM. An incident outranks everything, because the
// pill is where an incident announces itself; then paused; then the terminal
// states, which can never be "capturing"; and only then the lane ladder, which
// is ordered by which lane owns the camera at that moment.

import type { SequenceState } from "../../../../types";
import type { Tone } from "../../../ui";

/** The design's colour values (README "Design tokens"), not token names: the
 *  pill paints its own dot and border with them and an incident hands its own
 *  colour straight in. */
export const PHASE_COLOR = {
  capture: "#00D2FF",
  step: "#4d7cff",
  warn: "#ffb454",
  bad: "#ff5470",
  good: "#3ddc97",
  dim: "#7683a5",
} as const;

export interface PhaseView {
  text: string;
  color: string;
  /** 1.4 s dot pulse - true for every state the rig is still live in. */
  pulse: boolean;
  /** The token tone, so `:root.night` still resolves a colour when the hex is
   *  flattened by the night layer. Shape and word carry the state; the hue is
   *  the third signal, never the only one. */
  tone: Tone;
}

export interface PhaseInput {
  state: SequenceState["state"];
  detail?: string;
  /** `sequence.schedule.state`, when the engine attached one. */
  scheduleState?: "waiting" | "ready" | "window_closed" | "never_rises" | null;
  /** `status.busy_lanes` - the rig's own list of what is running. */
  busyLanes: readonly string[];
  /** `store.focus?.state === "running"` - the autofocus event stream, which
   *  starts before the `autofocus` lane appears on a 2 s status poll. */
  focusRunning: boolean;
  /** `progress.frame_started_at_ms` - non-null means a shutter is open. */
  frameStartedAtMs: number | null;
  /** The top incident's pill and colour. It wins outright. */
  incident: { pill: string; color: string } | null;
}

function toneOf(color: string): Tone {
  switch (color) {
    case PHASE_COLOR.capture: return "accent";
    case PHASE_COLOR.step: return "info";
    case PHASE_COLOR.warn: return "warn";
    case PHASE_COLOR.bad: return "bad";
    case PHASE_COLOR.good: return "good";
    default: return "dim";
  }
}

/** Terminal = the rig has stopped and nothing more is coming. `aborting` is NOT
 *  terminal (types.ts: "aborting IS STILL A LIVE RUN"), so its pill pulses. */
export function isTerminalState(state: SequenceState["state"]): boolean {
  return state === "complete" || state === "aborted" || state === "error" || state === "idle";
}

/** The lane ladder for a `running`/`holding` publish, in the order the plan's
 *  table gives. Exported so the test can walk the rows one at a time. */
export function laneOf(inp: PhaseInput): { text: string; color: string } {
  const detail = (inp.detail ?? "").toLowerCase();
  const busy = (lane: string) => inp.busyLanes.includes(lane);

  if (busy("goto") || detail.startsWith("slewing to ") || detail.startsWith("re-centring")) {
    return { text: "SLEWING", color: PHASE_COLOR.step };
  }
  if (busy("autofocus") || detail.includes("autofocus") || inp.focusRunning) {
    return { text: "FOCUSING", color: PHASE_COLOR.step };
  }
  if (busy("solve") || detail.includes("solving")) {
    return { text: "SOLVING", color: PHASE_COLOR.step };
  }
  if (detail === "starting guiding" || busy("guide")) {
    return { text: "GUIDING", color: PHASE_COLOR.step };
  }
  if (detail.startsWith("cooling to ")) {
    return { text: "COOLING", color: PHASE_COLOR.step };
  }
  if (inp.scheduleState === "waiting") {
    return { text: "WAITING", color: PHASE_COLOR.dim };
  }
  if (busy("capture") || inp.frameStartedAtMs != null) {
    return { text: "CAPTURING", color: PHASE_COLOR.capture };
  }
  return { text: "RUNNING", color: PHASE_COLOR.capture };
}

export function phaseOf(inp: PhaseInput): PhaseView {
  const done = (text: string, color: string, pulse = true): PhaseView =>
    ({ text, color, pulse, tone: toneOf(color) });

  // 1. An incident owns the pill. It is the only place a hold, a stale link or
  //    a closed cooler gate is visible from the top of the screen.
  if (inp.incident) return done(inp.incident.pill, inp.incident.color);

  // 2. Paused. Not terminal - the run is still the engine's, and the frame in
  //    flight is still exposing (see RunControls' PAUSING notice).
  if (inp.state === "paused") return done("PAUSED", PHASE_COLOR.warn);

  // 3. Terminal, and the two that are not.
  if (inp.state === "complete") return done("DONE", PHASE_COLOR.good, false);
  if (inp.state === "error") return done("ERROR", PHASE_COLOR.bad, false);
  if (inp.state === "aborted") return done("STOPPED", PHASE_COLOR.bad, false);
  if (inp.state === "aborting") return done("STOPPING", PHASE_COLOR.bad);
  if (inp.state === "nina_native") return done("NINA IS DRIVING", PHASE_COLOR.dim);
  if (inp.state === "idle") return done("IDLE", PHASE_COLOR.dim, false);

  // 4. running / holding -> the lane ladder.
  const lane = laneOf(inp);
  return done(lane.text, lane.color);
}

/** Lower-cased phase word for the live stack's "focusing..." caption. */
export function phaseWord(view: PhaseView): string {
  return view.text.toLowerCase();
}
