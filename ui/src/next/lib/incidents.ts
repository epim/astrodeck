// incidents.ts — derives the Session hub's incident list from engine state
// (ARCHITECTURE.md #10 "Incident model"). Pure: `IncidentInputs` is plain
// fields, decoupled from the real store types, so this is testable with
// literal objects and importable without pulling in `store.ts`/`types.ts`.
//
// Kind -> pill/color/title/engine/next/actions text is lifted from the
// README's "Incidents (Session)" table; the fuller "engine"/"next" sentences
// below paraphrase that table's columns rather than the design prototype's
// demo INC object (scratchpad seams/proto/logic.js `INC`), because the
// prototype's sentences bake in specific fake numbers ("frees 31 GB", "-5C is
// reachable at ~70% power") that this module has no real data to back —
// ARCHITECTURE.md #10: "Where the engine has no such verb, the action is
// omitted, never faked."
//
// "stall" (NO PROGRESS) has NO row in the README table at all (the table has
// 8 rows: cloud/safety/link/solve/af/guide/disk/cooler); its pill, color and
// trigger condition come from the T0.3 brief's own inputs list, and its copy/
// actions are this module's own composition using the two action ids the
// README table's other 8 rows never use (`stop_run`, `resume_run`) — flagged
// as an assumption in the T0.3 report.

import { fmtBytes } from "./format";

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

export interface IncidentInputs {
  sequence: {
    state: string;
    detail?: string;
    hold?: { reason?: string; since?: number } | null;
    end_reason?: string;
  };
  safety: { is_safe: boolean; reason?: string; stale?: boolean; ts?: number } | null;
  wsPhase: string;
  telemetryStale: boolean;
  wsLastEvent: number | null;
  mountOp: { stuck?: boolean; error_arcmin?: number; attempt?: number } | null;
  focus: { state: string; message?: string } | null;
  lastAutofocusResult: { ok?: boolean; failed?: boolean } | null;
  guide: { guiding?: boolean; lost?: boolean; phase?: string } | null;
  diskFreeBytes: number | null;
  cooler: { on?: boolean; at_setpoint?: boolean; gate_open?: boolean; setpoint?: number; temperature?: number } | null;
  lastCaptureAtMs: number | null;
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

/** Hex color for a kind, for the Session tab's pulsing dot — usable without
 *  computing a full incident. */
export function incidentColor(kind: IncidentKind): string {
  return COLOR[kind];
}

// most severe first (ARCHITECTURE.md #10: "most severe first")
const SEVERITY_ORDER: IncidentKind[] = ["safety", "link", "solve", "cloud", "guide", "af", "cooler", "disk", "stall"];

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

const DISK_LOW_BYTES = 2 * 1024 * 1024 * 1024; // README: "clean stop at 2 GB"

// "holding" per SequenceState (ui/src/types.ts), plus every other state the
// engine is still actively driving the rig in (running/aborting/paused).
const LIVE_STATES = new Set(["running", "holding", "aborting", "paused", "nina_native"]);
function isSequenceRunning(state: string): boolean {
  return LIVE_STATES.has(state);
}

function cloudIncident(inp: IncidentInputs): Incident | null {
  const hold = inp.sequence.hold;
  if (!hold) return null;
  const state = inp.sequence.state;
  const isHoldingState = state === "holding" || state === "paused" || state === "hold";
  const reason = (hold.reason ?? "").toLowerCase();
  if (!isHoldingState || !/cloud|weather|sky/.test(reason)) return null;
  return {
    kind: "cloud",
    pill: PILL.cloud,
    color: COLOR.cloud,
    title: "CLOUD HOLD",
    sinceMs: hold.since ?? null,
    resolvesItself: true,
    engine: "Capture paused at the frame boundary, guiding parked, mount tracking, cooler holding.",
    next: "Resumes after 3 clear frames; the plan continues where it left off.",
    actions: [
      { id: "wait", label: "WAIT", primary: true },
      { id: "ignore_weather", label: "IGNORE WEATHER TONIGHT" },
      { id: "switch_target", label: "SWITCH TARGET" },
    ],
  };
}

function safetyIncident(inp: IncidentInputs): Incident | null {
  const safety = inp.safety;
  if (!safety || safety.is_safe !== false) return null;
  const reason = safety.reason ? safety.reason.toUpperCase() : null;
  return {
    kind: "safety",
    pill: PILL.safety,
    color: COLOR.safety,
    title: reason ? `SAFETY TRIP · ${reason}` : "SAFETY TRIP",
    sinceMs: safety.ts ?? null,
    resolvesItself: true,
    engine: "Stop, park, close, warm cooler; the flow is suspended, not killed.",
    next: "Re-arms 30 minutes after the sensor reads dry.",
    actions: [
      { id: "acknowledge", label: "ACKNOWLEDGE", primary: true },
      { id: "open_safety", label: "OPEN SAFETY" },
    ],
  };
}

function linkIncident(inp: IncidentInputs): Incident | null {
  if (inp.wsPhase === "up" && !inp.telemetryStale) return null;
  return {
    kind: "link",
    pill: PILL.link,
    color: COLOR.link,
    title: "LINK LOST",
    sinceMs: inp.wsLastEvent,
    resolvesItself: true,
    engine: "The session continues on the rig; this view is the last state received.",
    next: "Retries direct every 5 seconds, then falls back to the relay after 30 seconds.",
    actions: [
      { id: "retry_link", label: "RETRY NOW", primary: true },
      { id: "switch_relay", label: "SWITCH TO RELAY" },
    ],
  };
}

function solveIncident(inp: IncidentInputs): Incident | null {
  if (!inp.mountOp?.stuck) return null;
  return {
    kind: "solve",
    pill: PILL.solve,
    color: COLOR.solve,
    title: "PLATE SOLVE FAILED",
    sinceMs: null,
    resolvesItself: true,
    engine: "3 fails, then a blind solve, a refocus, and one more try.",
    next: "Retries automatically, then skips or holds if it keeps failing.",
    actions: [
      { id: "retry_solve", label: "RETRY NOW", primary: true },
      { id: "skip_target", label: "SKIP TARGET" },
      { id: "nudge_mount", label: "NUDGE MOUNT" },
    ],
  };
}

function afIncident(inp: IncidentInputs): Incident | null {
  if (!isSequenceRunning(inp.sequence.state)) return null;
  const failed = inp.focus?.state === "failed" || inp.lastAutofocusResult?.failed === true;
  if (!failed) return null;
  return {
    kind: "af",
    pill: PILL.af,
    color: COLOR.af,
    title: "AUTOFOCUS FAILED",
    sinceMs: null,
    resolvesItself: true,
    engine: "Restored the last good focus position; tightened the HFR watchdog.",
    next: "Retries automatically on the next filter change.",
    actions: [
      { id: "retry_af", label: "RETRY AUTOFOCUS", primary: true },
      { id: "keep_last_good", label: "KEEP LAST GOOD" },
      { id: "open_focuser", label: "OPEN FOCUSER" },
    ],
  };
}

function guideIncident(inp: IncidentInputs): Incident | null {
  if (!inp.guide?.lost) return null;
  return {
    kind: "guide",
    pill: PILL.guide,
    color: COLOR.guide,
    title: "GUIDE STAR LOST",
    sinceMs: null,
    resolvesItself: true,
    engine: "Frame discarded; re-acquiring on a new star.",
    next: "Re-acquires automatically; escalates to a cloud hold if it keeps failing.",
    actions: [
      { id: "reacquire", label: "RE-ACQUIRE", primary: true },
      { id: "continue_unguided", label: "CONTINUE UNGUIDED" },
    ],
  };
}

function diskIncident(inp: IncidentInputs): Incident | null {
  const bytes = inp.diskFreeBytes;
  if (bytes == null || bytes >= DISK_LOW_BYTES) return null;
  return {
    kind: "disk",
    pill: PILL.disk,
    color: COLOR.disk,
    title: `DISK LOW · ${fmtBytes(bytes)}`,
    sinceMs: null,
    resolvesItself: false,
    engine: "Capture continues; a clean stop is coming at 2 GB free.",
    next: "No automatic action; the run stops cleanly at 2 GB free.",
    actions: [
      { id: "free_disk", label: "FREE SPACE" },
      { id: "continue", label: "CONTINUE", primary: true },
    ],
  };
}

function coolerIncident(inp: IncidentInputs): Incident | null {
  if (!inp.cooler || inp.cooler.gate_open !== false) return null;
  if (!isSequenceRunning(inp.sequence.state)) return null;
  const title =
    inp.cooler.setpoint != null ? `COOLER CAN'T HOLD ${inp.cooler.setpoint}°C` : "COOLER GATE CLOSED";
  return {
    kind: "cooler",
    pill: PILL.cooler,
    color: COLOR.cooler,
    title,
    sinceMs: null,
    resolvesItself: false,
    engine: "The capture gate is closed until the sensor is stable at an achievable setpoint.",
    next: "No automatic action; the gate stays closed until the sensor stabilizes.",
    actions: [
      { id: "accept_setpoint", label: "ACCEPT SETPOINT" },
      { id: "wait_cooler", label: "WAIT", primary: true },
    ],
  };
}

function stallIncident(inp: IncidentInputs, nowMs: number): Incident | null {
  if (!isSequenceRunning(inp.sequence.state)) return null;
  if (inp.lastCaptureAtMs == null || inp.expectedFrameS == null) return null;
  const thresholdMs = (3 * inp.expectedFrameS + 120) * 1000;
  if (nowMs - inp.lastCaptureAtMs <= thresholdMs) return null;
  return {
    kind: "stall",
    pill: PILL.stall,
    color: COLOR.stall,
    title: "NO PROGRESS",
    sinceMs: inp.lastCaptureAtMs,
    resolvesItself: false,
    engine: "No frame has landed in well over the expected exposure time.",
    next: "No automatic action; a stalled run needs a person.",
    actions: [
      { id: "resume_run", label: "RESUME", primary: true },
      { id: "stop_run", label: "STOP RUN" },
    ],
  };
}

const DERIVE: Record<IncidentKind, (inp: IncidentInputs, nowMs: number) => Incident | null> = {
  safety: safetyIncident,
  link: linkIncident,
  solve: solveIncident,
  cloud: cloudIncident,
  guide: guideIncident,
  af: afIncident,
  cooler: coolerIncident,
  disk: diskIncident,
  stall: stallIncident,
};

/** Every active incident, most severe first (ARCHITECTURE.md #10 severity
 *  order: safety, link, solve, cloud, guide, af, cooler, disk, stall). */
export function deriveIncidents(inp: IncidentInputs, nowMs: number): Incident[] {
  const out: Incident[] = [];
  for (const kind of SEVERITY_ORDER) {
    const incident = DERIVE[kind](inp, nowMs);
    if (incident) out.push(incident);
  }
  return out;
}
