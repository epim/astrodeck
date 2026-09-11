// incidents.ts - derives the Session hub's incident list from engine state
// (ARCHITECTURE.md #10 "Incident model"; the exact engine fields per kind are
// fixed by the Session plan's section A.3). Pure: `IncidentInputs` is plain
// fields, decoupled from the real store types, so this is testable with literal
// objects and importable without pulling in `store.ts`/`types.ts`.
//
// WHAT THIS MODULE IS ALLOWED TO SAY. Every ENGINE and NEXT sentence below is
// composed from a field that is actually on the wire - `sequence.detail`,
// `sequence.sky.text`, `focus.message`, the cooler's own numbers, the safety
// reading's own reason. It used to carry four sentences that were the design
// prototype's fixtures rather than the engine's behaviour:
//
//     "Resumes after 3 clear frames"            - the engine counts no such thing
//     "Re-arms 30 minutes after the sensor reads dry"  - there is no 30-minute re-arm
//     "falls back to the relay after 30 seconds"       - there is no client-side
//                                                        transport switch at all
//     "3 fails, then a blind solve"             - the engine publishes no such ladder
//
// An operator reads this card to decide whether to drive to the observatory. A
// promise nothing keeps is the most expensive thing this screen can print, so
// each of those is gone and what replaced it is the live field.
//
// The same rule governs the ACTIONS: five of the design's buttons have no verb
// behind them anywhere on the server (`switch_target`, `skip_target`,
// `keep_last_good`, `continue_unguided`, `switch_relay`), and the stall card's
// RESUME could only ever 409 on a run that is by definition still running
// (`/api/sequence/resume` refuses a run that is not paused). They are omitted,
// never faked - ARCHITECTURE.md #10: "Where the engine has no such verb, the
// action is omitted, never faked."
//
// `sinceMs` prefers the RIG's own clock: the newest `logs` line whose source and
// message match the kind's signature (`events.py` stamps `ts` in unix seconds;
// the caller multiplies). Where the rig timestamps nothing - a link this phone
// lost, a disk threshold the server publishes no transition for - `sinceMs` is
// null and the card renders the tail clause alone rather than inventing an age.

import { fmtDuration, stallLevel } from "../../lib/eta";

export type IncidentKind = "cloud" | "safety" | "link" | "solve" | "af" | "guide" | "disk" | "cooler" | "stall";

export type IncidentPill =
  | "HOLDING"
  | "PARKED"
  | "STALE"
  | "RETRYING"
  | "RECOVERING"
  | "GATE CLOSED"
  | "NO PROGRESS";

export interface IncidentAction {
  id: string;
  label: string;
  primary?: boolean;
  cap?: string;
}

export interface Incident {
  kind: IncidentKind;
  pill: IncidentPill;
  color: string;
  title: string;
  sinceMs: number | null;
  resolvesItself: boolean;
  engine: string;
  next: string;
  actions: IncidentAction[];
}

/** One line of the rig's own log ring, flattened. `tsMs` is `LogLine.ts * 1000`
 *  - the RIG's clock, which is the only clock that can date an event that
 *  happened on the rig. */
export interface IncidentLogLine {
  source: string;
  message: string;
  level: string;
  tsMs: number;
}

export interface IncidentInputs {
  sequence: {
    state: string;
    detail?: string;
    hold?: { reason?: string; since?: number } | null;
    end_reason?: string;
    /** `SequenceState.sky` - the engine's own sky verdict, published beside
     *  `state` on EVERY publish (`sequence/engine.py _sky_state`). `holding` is
     *  the field that survives a routine `running` publish overwriting the
     *  `holding` state, which is exactly the 2026-08-12 defect: the hold was
     *  up and the state said running. Read BOTH. */
    sky?: {
      cloudy?: boolean | null;
      score?: number | null;
      reason?: string;
      text?: string;
      holding?: boolean;
    } | null;
  };
  /** `SafetyState.reading`, flattened; `ts` already in MILLISECONDS. */
  safety: {
    is_safe: boolean;
    reason?: string;
    source?: string;
    stale?: boolean;
    ts?: number;
  } | null;
  wsPhase: string;
  telemetryStale: boolean;
  wsLastEvent: number | null;
  mountOp: { stuck?: boolean; error_arcmin?: number; attempt?: number } | null;
  focus: { state: string; message?: string } | null;
  lastAutofocusResult: { ok?: boolean; failed?: boolean } | null;
  /** `GuideStats`. There is no `lost` flag on the wire: the engine's own words
   *  for a lost star are `phase === "lost"` (NOV-7 narration phase) and the
   *  re-centring `sequence.detail` strings, and those are what this reads. */
  guide: { guiding?: boolean; phase?: string } | null;
  /** `RigStatus.disk` verbatim. The THRESHOLD IS THE SERVER'S: `low` and
   *  `critical` are computed where the disk actually is, so a client-side "under
   *  2 GB" rule would be a second opinion that disagrees on a rig whose capture
   *  directory is on another volume. */
  disk: { free_gb: number; low: boolean; critical: boolean } | null;
  cooler: {
    on?: boolean;
    /** `CoolerInfo.at_target`. */
    at_target?: boolean;
    /** An already-decided capture gate, when a caller has one. Absent - the
     *  normal case, because the engine publishes no such flag - and the gate is
     *  derived here from `at_target` and the engine's own `cooling to ...`
     *  detail (engine.py:4525). */
    gate_open?: boolean;
    target_c?: number | null;
    temperature?: number | null;
    power?: number | null;
    can_report_power?: boolean;
  } | null;
  /** The rig's log ring, oldest first (`store.logs`). Used ONLY for `sinceMs`. */
  logs: IncidentLogLine[];
  lastCaptureAtMs: number | null;
  /** `progress.current_exposure_s` - what `stallLevel` measures against. */
  expectedFrameS: number | null;
}

// ------------------------------------------------------------------ colors
const COLOR: Record<IncidentKind, string> = {
  safety: "#ff5470",
  link: "#7683a5",
  solve: "#ff5470",
  cloud: "#ffb454",
  guide: "#ffb454",
  af: "#ffb454",
  cooler: "#ffb454",
  disk: "#ffb454",
  stall: "#ffb454",
};

const BAD = "#ff5470";

/** Hex color for a kind, for the Session tab's pulsing dot - usable without
 *  computing a full incident. */
export function incidentColor(kind: IncidentKind): string {
  return COLOR[kind];
}

/** Most severe first, and this array IS the order `deriveIncidents` returns
 *  (Session plan A.3: `safety` > `link` > `solve` > `stall` > `cooler` >
 *  `guide` > `af` > `disk` > `cloud`).
 *
 *  STALL SITS ABOVE CLOUD DELIBERATELY. A cloud hold is the engine working: it
 *  parked the guider, it is probing, and it will pick up where it left off. A
 *  stall is the engine NOT working while it still says "running". If both are
 *  true, the one that needs a person is the one that gets the card. */
const SEVERITY_ORDER: IncidentKind[] = [
  "safety", "link", "solve", "stall", "cooler", "guide", "af", "disk", "cloud",
];

const PILL: Record<IncidentKind, IncidentPill> = {
  cloud: "HOLDING",
  safety: "PARKED",
  link: "STALE",
  solve: "RETRYING",
  af: "RECOVERING",
  guide: "RECOVERING",
  disk: "RECOVERING",
  cooler: "GATE CLOSED",
  stall: "NO PROGRESS",
};

/** The no-progress watchdog trips the SAFETY monitor, so its reading arrives on
 *  the same channel as rain and a closed roof. It is the `stall` incident, not a
 *  safety trip: a card that said PARKED over a rig that is neither parked nor in
 *  danger sends someone out to a working observatory (engine.py:4310 publishes
 *  exactly `no progress in {n} min`). */
const NO_PROGRESS_REASON = "no progress in ";

function isNoProgressReading(safety: IncidentInputs["safety"]): boolean {
  return !!safety && safety.is_safe === false
    && (safety.reason ?? "").startsWith(NO_PROGRESS_REASON);
}

// "holding" per SequenceState (ui/src/types.ts), plus every other state the
// engine is still actively driving the rig in (running/aborting/paused).
const LIVE_STATES = new Set(["running", "holding", "aborting", "paused", "nina_native"]);
function isSequenceRunning(state: string): boolean {
  return LIVE_STATES.has(state);
}

/** The newest log line the rig wrote for this kind, in the rig's own clock.
 *  Walks backwards: the ring is oldest-first and the NEWEST match is the one
 *  that dates the incident on screen now. */
function sinceFromLog(
  logs: IncidentLogLine[],
  source: string,
  message: RegExp,
  level?: string,
): number | null {
  for (let i = logs.length - 1; i >= 0; i--) {
    const line = logs[i];
    if (line.source !== source) continue;
    if (level != null && line.level !== level) continue;
    if (!message.test(line.message)) continue;
    return line.tsMs;
  }
  return null;
}

/** The two `sequence.detail` strings the engine publishes while it is putting a
 *  guided field back where it was (engine.py:5222, :5324). */
const RECENTRING = [
  "re-centring after guiding loss",
  "re-centring: the guided field walked",
];

function detailOf(inp: IncidentInputs): string {
  return inp.sequence.detail ?? "";
}

// -------------------------------------------------------------------- cloud

function cloudIncident(inp: IncidentInputs): Incident | null {
  const sky = inp.sequence.sky ?? null;
  const state = inp.sequence.state;
  const holdingState = state === "holding" || state === "paused" || state === "hold";
  const reason = (inp.sequence.hold?.reason ?? "").toLowerCase();
  // A hold the operator asked for is not weather, and a PAUSED run is not a
  // hold at all - so the state alone is never enough: the engine has to have
  // named a weather-shaped reason. `sky.holding` is the second, stronger
  // source: `_holding_for_clear` is published beside `state` on every publish
  // (engine.py:1098), so it says the hold is up even when the last routine
  // `running` publish overwrote the state.
  const reasonFits = reason !== "" && /cloud|weather|sky|clear/.test(reason);
  const active = sky?.holding === true || (holdingState && reasonFits);
  if (!active) return null;

  const detail = detailOf(inp);
  const skyText = sky?.text ? ` (${sky.text})` : "";

  return {
    kind: "cloud",
    pill: PILL.cloud,
    color: COLOR.cloud,
    title: "CLOUD HOLD",
    sinceMs: sinceFromLog(inp.logs, "sequence", /^holding for clear sky: /)
      ?? inp.sequence.hold?.since ?? null,
    resolvesItself: true,
    // The engine composes its own sentence on this path ("held for cloud -
    // <reason>. Probing every N min; parks after M min", engine.py:3707), and it
    // carries the two numbers this module cannot know. Prefer it.
    engine: detail.startsWith("held for cloud")
      ? detail
      : "Capture paused at the frame boundary. Guiding parked, mount tracking, "
        + "cooler holding at setpoint - nothing to redo when it clears.",
    next: `Watching the star count and the cloud score${skyText}; the ledger `
      + "keeps the sub count, so the plan picks up mid-pass.",
    actions: [
      { id: "wait", label: "WAIT" },
      { id: "ignore_weather", label: "IGNORE WEATHER TONIGHT", primary: true },
    ],
  };
}

// ------------------------------------------------------------------- safety

function safetyIncident(inp: IncidentInputs): Incident | null {
  const safety = inp.safety;
  const tripped = !!safety && safety.is_safe === false && !isNoProgressReading(safety);
  const terminal = inp.sequence.end_reason === "unsafe";
  if (!tripped && !terminal) return null;

  const detail = detailOf(inp);
  const label = (safety?.source || safety?.reason || "unknown").toUpperCase();

  let engine: string;
  if (safety?.stale === true) {
    // lib/health.ts's Tier-2 string, verbatim: a reading that timed out is
    // treated as unsafe, and saying so is not the same as saying it read unsafe.
    engine = "Safety reading stale - treating as unsafe.";
  } else if (detail.startsWith("paused (unsafe)") || detail.startsWith("roof closed (unsafe)")) {
    // The engine's own words for what it actually did (engine.py:3417, :3533).
    engine = detail;
  } else {
    const reason = safety?.reason ? `${safety.reason}. ` : "";
    engine = `${reason}Capture is stopped and the flow is suspended, not killed.`;
  }

  return {
    kind: "safety",
    pill: PILL.safety,
    color: COLOR.safety,
    title: `SAFETY TRIP · ${label}`,
    sinceMs: safety?.ts ?? null,
    // The design's own column, and the honest one: nothing here re-arms on a
    // timer. The run waits for the MONITOR to read safe again, which is a fact
    // about the sky, not a countdown.
    resolvesItself: false,
    engine,
    next: "The run re-arms itself when the monitor reads safe again; if the "
      + "window has closed it writes the report instead.",
    actions: [
      { id: "acknowledge", label: "ACKNOWLEDGE" },
      { id: "open_safety", label: "OPEN SAFETY", primary: true },
    ],
  };
}

// --------------------------------------------------------------------- link

/** How long the FIRST connection is allowed to take before a socket that has
 *  never opened counts as a lost link. Long enough for a cold start on a slow
 *  field link or the relay; short enough that a rig which really is unreachable
 *  is named while the user is still looking at the screen. */
export const FIRST_LINK_GRACE_MS = 5000;

function linkIncident(inp: IncidentInputs, nowMs: number): Incident | null {
  if (inp.wsPhase === "up" && !inp.telemetryStale) return null;

  // NOT A LOST LINK: A LINK THAT HAS NOT HAPPENED YET (review #16).
  //
  // `store.ts:1023` initialises `wsPhase: "connecting"` and `wsLastEvent` to
  // the moment the store was created, so the first paint of every cold start
  // used to carry "LINK LOST - This phone cannot reach the rig computer", 0 s
  // old, plus the Session tab's dot, until the socket opened. The first thing
  // the app ever said was that it was broken.
  //
  // "connecting" is the phase that means NEVER UP: `ws.ts:48` picks
  // `everConnected ? "reconnecting" : "connecting"`, and a socket that opened
  // and dropped goes to "down". So the suppression here cannot hide a real
  // drop - it can only cover the opening seconds of a document, and only until
  // the grace lapses, after which a socket that still has not opened IS a link
  // the user needs to be told about.
  const opening = inp.wsPhase === "connecting"
    && inp.wsLastEvent != null
    && nowMs - inp.wsLastEvent < FIRST_LINK_GRACE_MS;
  if (opening) return null;

  return {
    kind: "link",
    pill: PILL.link,
    color: COLOR.link,
    title: "LINK LOST",
    // The rig cannot timestamp a link THIS phone lost, so the last thing it
    // said is the only honest age there is.
    sinceMs: inp.wsLastEvent,
    resolvesItself: true,
    engine: "This phone cannot reach the rig computer. The session keeps running "
      + "there - nothing on the rig depends on the phone.",
    next: "Reconnecting. The view below is the last state received.",
    actions: [
      { id: "retry_link", label: "RETRY NOW", primary: true },
    ],
  };
}

// -------------------------------------------------------------------- solve

const SOLVE_WINDOW_MS = 5 * 60 * 1000;

function solveIncident(inp: IncidentInputs, nowMs: number): Incident | null {
  const stuck = inp.mountOp?.stuck === true;
  const logTs = inp.sequence.state === "running"
    ? sinceFromLog(inp.logs, "sequence", /solve|centr/i, "error")
    : null;
  const recent = logTs != null && nowMs - logTs <= SOLVE_WINDOW_MS;
  if (!stuck && !recent) return null;

  const err = inp.mountOp?.error_arcmin;
  const where = err != null
    ? ` - ${err.toFixed(1)}' off after attempt ${inp.mountOp?.attempt ?? "?"}`
    : "";

  return {
    kind: "solve",
    pill: PILL.solve,
    color: COLOR.solve,
    title: "PLATE SOLVE FAILED",
    sinceMs: recent ? logTs : null,
    resolvesItself: true,
    engine: `Centring is not converging${where}.`,
    next: "The engine retries the solve on its own; if it cannot centre it sets "
      + "this target aside and the run continues.",
    actions: [
      { id: "retry_solve", label: "RETRY NOW", primary: true },
      { id: "nudge_mount", label: "NUDGE MOUNT" },
    ],
  };
}

// ----------------------------------------------------------------------- af

function afIncident(inp: IncidentInputs): Incident | null {
  if (!isSequenceRunning(inp.sequence.state)) return null;
  const failed = inp.focus?.state === "failed" || inp.lastAutofocusResult?.failed === true;
  if (!failed) return null;
  return {
    kind: "af",
    pill: PILL.af,
    color: COLOR.af,
    title: "AUTOFOCUS FAILED",
    sinceMs: sinceFromLog(inp.logs, "sequence", /autofocus failed/i),
    resolvesItself: true,
    // The server has usually already worked out WHY (a two-second sweep on a
    // sparse field, say) and said so. Never print a generic sentence over a
    // server-composed one.
    engine: inp.focus?.message
      || "No V-curve minimum. The last good focus position is restored.",
    next: "Capture continues at the last good focus; the next filter change or "
      + "temperature step retries. The engine has already put the last good "
      + "position back - there is no button for it because there is nothing "
      + "left to do.",
    actions: [
      { id: "retry_af", label: "RETRY AUTOFOCUS", primary: true },
      { id: "open_focuser", label: "OPEN FOCUSER" },
    ],
  };
}

// -------------------------------------------------------------------- guide

function guideIncident(inp: IncidentInputs): Incident | null {
  const detail = detailOf(inp);
  const recentring = RECENTRING.some((s) => detail.startsWith(s));
  // `phase === "lost"` is the engine's own word for a lost star (NOV-7). A bare
  // `guiding === false` is NOT: the guider is legitimately stopped through every
  // slew, every autofocus and every filter change, and a card that fired on it
  // would spend the night announcing the run working normally.
  const lost = inp.guide?.phase === "lost" || recentring;
  if (!lost) return null;

  return {
    kind: "guide",
    pill: PILL.guide,
    color: COLOR.guide,
    title: "GUIDE STAR LOST",
    sinceMs: sinceFromLog(inp.logs, "guide", /lost|re-acquir/i),
    resolvesItself: true,
    engine: recentring
      ? detail
      : "The frame was discarded. The guider is re-acquiring on a new star.",
    next: "If it cannot re-acquire with a falling star count the engine treats "
      + "it as a cloud hold.",
    actions: [
      { id: "reacquire", label: "RE-ACQUIRE", primary: true },
    ],
  };
}

// --------------------------------------------------------------------- disk

function diskIncident(inp: IncidentInputs): Incident | null {
  const disk = inp.disk;
  if (!disk || (disk.low !== true && disk.critical !== true)) return null;
  const critical = disk.critical === true;
  return {
    kind: "disk",
    pill: PILL.disk,
    color: critical ? BAD : COLOR.disk,
    title: `DISK ${critical ? "CRITICAL" : "LOW"} · ${disk.free_gb.toFixed(1)} GB`,
    // The server publishes no transition timestamp for a threshold crossing,
    // and a made-up one would be the only number on the card.
    sinceMs: null,
    resolvesItself: false,
    engine: "Capture continues. The run stops cleanly and writes its report when "
      + "the disk runs out.",
    next: "Nothing frees space by itself. Deleting sessions already downloaded "
      + "frees the most.",
    actions: [
      { id: "free_disk", label: "FREE SPACE", primary: true },
      { id: "continue", label: "CONTINUE" },
    ],
  };
}

// ------------------------------------------------------------------- cooler

/** The capture gate. The engine holds capture while it is cooling to a setpoint
 *  the sensor has not reached - it publishes `cooling to <t>` as the detail
 *  (engine.py:4525) and re-opens on `at_target`. A caller that already knows the
 *  gate state passes `gate_open` and this defers to it. */
function coolerGateClosed(inp: IncidentInputs): boolean {
  const c = inp.cooler;
  if (!c) return false;
  if (c.gate_open === false) return true;
  if (c.gate_open === true) return false;
  return detailOf(inp).startsWith("cooling to ") && c.at_target === false;
}

function coolerIncident(inp: IncidentInputs): Incident | null {
  const skipped = inp.sequence.end_reason === "cooling_skip";
  const closed = coolerGateClosed(inp) && isSequenceRunning(inp.sequence.state);
  if (!skipped && !closed) return null;

  const c = inp.cooler;
  const temp = c?.temperature;
  // `can_report_power: false` means the camera has no power figure at all;
  // printing one would be a number with nothing behind it.
  const power = c?.can_report_power !== false && c?.power != null ? `${c.power}% power, ` : "";
  const at = typeof temp === "number" ? `sensor at ${temp.toFixed(1)}°C. ` : "";

  return {
    kind: "cooler",
    pill: PILL.cooler,
    color: COLOR.cooler,
    title: skipped
      ? "COOLING SKIPPED"
      : (c?.target_c != null ? `COOLER CANNOT HOLD ${c.target_c}°C` : "COOLER GATE CLOSED"),
    sinceMs: sinceFromLog(inp.logs, "sequence", /^cooling camera to /),
    resolvesItself: false,
    engine: `${power}${at}The capture gate stays closed until the sensor is `
      + "stable at an achievable setpoint.",
    next: "Accepting a warmer setpoint re-opens the gate once the sensor settles there.",
    actions: [
      { id: "accept_setpoint", label: "ACCEPT SETPOINT", primary: true },
      { id: "wait_cooler", label: "WAIT" },
    ],
  };
}

// -------------------------------------------------------------------- stall

function stallIncident(inp: IncidentInputs, nowMs: number): Incident | null {
  // 1. The server's own watchdog, which is authoritative: it publishes through
  //    the safety channel and it knows what the engine last wrote to disk.
  const serverTrip = isNoProgressReading(inp.safety);

  // 2. The client's earlier notice. `stallLevel` is the SAME rule the Monitor's
  //    stall chip uses (3 x the exposure + 20 s, `lib/eta.ts`), so the two
  //    surfaces cannot disagree about what "stalled" means - and it gates
  //    strictly on `running`, because a paused or finished run has no next frame
  //    coming and "stalled" would be a lie rather than a warning. `amber` does
  //    NOT raise a card; it raises the vitals band's stall line.
  const idleS = inp.lastCaptureAtMs != null ? (nowMs - inp.lastCaptureAtMs) / 1000 : null;
  const clientRed = stallLevel(
    inp.sequence.state as Parameters<typeof stallLevel>[0],
    idleS,
    inp.expectedFrameS ?? 0,
  ) === "red";

  if (!serverTrip && !clientRed) return null;

  const idle = idleS != null && idleS > 0
    ? `No frame recorded for ${fmtDuration(idleS)}. `
    : "No frame has been recorded. ";

  return {
    kind: "stall",
    pill: PILL.stall,
    color: COLOR.stall,
    title: "NO PROGRESS",
    sinceMs: serverTrip ? (inp.safety?.ts ?? null) : inp.lastCaptureAtMs,
    resolvesItself: false,
    engine: `${idle}The rig is still in a running state.`,
    next: serverTrip
      ? "The no-progress watchdog has tripped; the run acts on the escalation "
        + "policy at the next frame boundary."
      : "Nothing acts on this by itself - the no-progress watchdog is off or has "
        + "not tripped yet.",
    actions: [
      { id: "view_log", label: "VIEW LOG", primary: true },
      // NOT a RESUME: `/api/sequence/resume` refuses a run that is not paused,
      // and a no-progress stall is by definition a RUNNING run. A button that
      // can only 409 is not a recovery.
      { id: "stop_run", label: "STOP THE RUN" },
    ],
  };
}

const DERIVE: Record<IncidentKind, (inp: IncidentInputs, nowMs: number) => Incident | null> = {
  safety: safetyIncident,
  link: linkIncident,
  solve: solveIncident,
  stall: stallIncident,
  cooler: coolerIncident,
  guide: guideIncident,
  af: afIncident,
  disk: diskIncident,
  cloud: cloudIncident,
};

/** Every active incident, most severe first (`SEVERITY_ORDER`). */
export function deriveIncidents(inp: IncidentInputs, nowMs: number): Incident[] {
  const out: Incident[] = [];
  for (const kind of SEVERITY_ORDER) {
    const incident = DERIVE[kind](inp, nowMs);
    if (incident) out.push(incident);
  }
  return out;
}
