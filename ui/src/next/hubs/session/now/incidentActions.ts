// incidentActions.ts - what an incident's buttons actually DO, and what the
// card is allowed to claim (plan section A.3).
//
// TWO JOBS, AND THE SECOND ONE IS THE POINT.
//
// 1. THE VERB TABLE. Every action id the incident model emits is looked up
//    here. An id with no row is DROPPED before the card renders it. Four of
//    them have no row on purpose, because the server has no such verb:
//
//      SWITCH TARGET / SKIP TARGET - there is no skip route. The only
//        `/api/sequence/*` POSTs are start, pause, resume, abort, preflight,
//        recover and the stack family.
//      KEEP LAST GOOD - there is no restore route, and none is needed: the
//        engine restored the last good position before the incident was even
//        visible. That is stated as a FACT in the NEXT row instead.
//      SWITCH TO RELAY - there is no client-side transport switch. Direct and
//        relay are chosen in Settings.
//      CONTINUE UNGUIDED - writing `guide=false` edits the LOCAL plan, and the
//        server is executing the copy it took at Run, so the press would change
//        nothing about the run in progress. `POST /api/guide/stop` is the
//        honest verb and it is offered under its own name.
//
//    A button that does nothing is worse than an explained absence: it teaches
//    the operator that pressing things at 3am is how you fix the night.
//
// 2. THE COPY CORRECTION, and why it lives here rather than in
//    `next/lib/incidents.ts`. That module is another task's file and this one
//    does not edit it - but as shipped it promises things the engine does not
//    do ("Resumes after 3 clear frames", "Re-arms 30 minutes after the sensor
//    reads dry", "falls back to the relay after 30 seconds", "3 fails, then a
//    blind solve"). None of those numbers exists anywhere in the engine; they
//    are the design prototype's fixtures. Rendering them would put an invented
//    promise on the screen an operator uses to decide whether to drive to the
//    observatory. So `refineIncident` re-states ENGINE and NEXT from the LIVE
//    fields the plan names - `seq.detail`, `sky.text`, `focus.message`, the
//    cooler's own numbers - and the divergence is reported so the lib can be
//    corrected and this override deleted.

import { connectWs } from "../../../../ws";
import { api } from "../../../../api";
import { setIgnoreTonight } from "../../../../api/weather";
import { fmtDuration } from "../../../../lib/eta";
import { telemetryStaleNotice } from "../../../../lib/telemetry";
import { useStore } from "../../../../store";
import { lockReason, type GateStoreSlice } from "../../../lib/gate";
import type {
  Capability, CoolerInfo, DiskInfo, FocusEvent, RigStatus, SafetyState, SequenceState,
} from "../../../../types";
import type { Incident, IncidentAction } from "../../../lib";
import { nav } from "../../../router";

// ---------------------------------------------------------------- the table

export type IncidentActionKind = "endpoint" | "nav" | "client" | "local";

export interface IncidentActionSpec {
  id: string;
  /** The label this table guarantees. The incident model's own label is used
   *  when it has one; this is the fallback and the name in the report. */
  label: string;
  kind: IncidentActionKind;
  /** The POST path, for `kind: "endpoint"`. This is the field the test walks. */
  path?: string;
  /** The route, for `kind: "nav"`. */
  to?: string;
  cap?: Capability;
  /** A second capability that must also be held (`gate.ts`'s `GateInput`
   *  takes ONE `cap` - do not widen it). The caller checks `cap` then `cap2`
   *  and takes the first reason, same pattern as `busyLane2` below;
   *  `capLockReason` is the exported resolver that does it. */
  cap2?: Capability;
  /** Refused while this rig lane is busy (`lib/gate.ts` reads the lane list). */
  busyLane?: string;
  /** A second lane that must also be clear. `gate.ts` takes one, so the caller
   *  checks both and takes the first reason. */
  busyLane2?: string;
  needsRole?: string;
}

function spec(s: IncidentActionSpec): IncidentActionSpec { return s; }

/** Every action this hub can actually fire. Keyed by the id
 *  `next/lib/incidents.ts` emits (plus the two this file adds). */
export const INCIDENT_ACTIONS: Record<string, IncidentActionSpec> = {
  // --- cloud -------------------------------------------------------------
  wait: spec({ id: "wait", label: "WAIT", kind: "local" }),
  // Requires BOTH: control.capture (the principal) and view.weather (a
  // dependency - the response echoes the full weather payload, which carries
  // site_lat/site_lon - server/astrodeck/api/app.py:2476-2481, reasoned at
  // :2462-2471). Declaring control.capture alone let a control.capture
  // holder without view.weather see this action as unlocked.
  ignore_weather: spec({
    id: "ignore_weather", label: "IGNORE WEATHER TONIGHT", kind: "endpoint",
    path: "/api/weather/ignore-tonight", cap: "control.capture", cap2: "view.weather",
  }),
  // --- safety ------------------------------------------------------------
  acknowledge: spec({ id: "acknowledge", label: "ACKNOWLEDGE", kind: "local" }),
  open_safety: spec({ id: "open_safety", label: "OPEN SAFETY", kind: "nav", to: "/rig/devices/safety" }),
  // --- link --------------------------------------------------------------
  retry_link: spec({ id: "retry_link", label: "RETRY NOW", kind: "client" }),
  open_connection: spec({
    id: "open_connection", label: "CONNECTION", kind: "nav",
    to: "/settings/general/connection",
  }),
  // --- solve -------------------------------------------------------------
  retry_solve: spec({
    id: "retry_solve", label: "RETRY NOW", kind: "endpoint",
    path: "/api/mount/solve_sync", cap: "control.mount",
    busyLane: "solve", busyLane2: "goto", needsRole: "telescope",
  }),
  nudge_mount: spec({ id: "nudge_mount", label: "NUDGE MOUNT", kind: "nav", to: "/rig/devices/mount" }),
  // --- autofocus ---------------------------------------------------------
  retry_af: spec({
    id: "retry_af", label: "RETRY AUTOFOCUS", kind: "endpoint",
    path: "/api/focuser/autofocus", cap: "control.capture",
    busyLane: "autofocus", busyLane2: "capture", needsRole: "focuser",
  }),
  open_focuser: spec({ id: "open_focuser", label: "OPEN FOCUSER", kind: "nav", to: "/rig/devices/focuser" }),
  // --- guide -------------------------------------------------------------
  reacquire: spec({
    id: "reacquire", label: "RE-ACQUIRE", kind: "endpoint",
    path: "/api/guide/start", cap: "control.guide", busyLane: "guide", needsRole: "guider",
  }),
  /** The honest half of the omitted CONTINUE UNGUIDED: the engine tolerates a
   *  stopped guider and logs "continuing UNGUIDED", and the plan is untouched. */
  stop_guiding: spec({
    id: "stop_guiding", label: "STOP GUIDING", kind: "endpoint",
    path: "/api/guide/stop", cap: "control.guide", needsRole: "guider",
  }),
  // --- disk --------------------------------------------------------------
  free_disk: spec({ id: "free_disk", label: "FREE SPACE", kind: "nav", to: "archive:trash", cap: "control.capture" }),
  continue: spec({ id: "continue", label: "CONTINUE", kind: "local" }),
  // --- cooler ------------------------------------------------------------
  accept_setpoint: spec({
    id: "accept_setpoint", label: "ACCEPT SETPOINT", kind: "endpoint",
    path: "/api/camera/cooler", cap: "control.capture", needsRole: "camera",
  }),
  wait_cooler: spec({ id: "wait_cooler", label: "WAIT", kind: "local" }),
  // --- stall -------------------------------------------------------------
  view_log: spec({ id: "view_log", label: "VIEW LOG", kind: "nav", to: "/monitor/log" }),
  stop_run: spec({
    id: "stop_run", label: "STOP THE RUN", kind: "endpoint",
    path: "/api/sequence/abort", cap: "control.mount",
  }),
};

/** Ids with NO verb behind them, under both the plan's short names and the
 *  names `next/lib/incidents.ts` actually emits. Nothing here may appear in
 *  `INCIDENT_ACTIONS`, and `refineIncident` drops them from every card. */
export const OMITTED_ACTION_IDS: readonly string[] = [
  "switch", "switch_target",
  "skip", "skip_target",
  "useLast", "keep_last_good",
  "relay", "switch_relay",
  // The stall card's RESUME: `/api/sequence/resume` refuses a run that is not
  // paused, and a no-progress stall is a RUNNING run. A button that can only
  // 409 is not a recovery.
  "resume_run",
];

/** Two-tap arm window for the incident card's STOP (the same 3 s the STOP
 *  control uses). */
export const INCIDENT_ARM_MS = 3000;
export const INCIDENT_ARM_LABEL = "TAP AGAIN TO STOP";
/** Action ids that must be pressed twice. */
export const ARMED_ACTION_IDS: readonly string[] = ["stop_run"];

export function specFor(id: string): IncidentActionSpec | null {
  return Object.prototype.hasOwnProperty.call(INCIDENT_ACTIONS, id)
    ? INCIDENT_ACTIONS[id]
    : null;
}

// -------------------------------------------------------- the two-cap lock
// `gate.ts`'s `GateInput` takes ONE `cap` (ARCHITECTURE.md #8); this is the
// caller-side combinator for a spec that names two (`cap` then `cap2`, first
// non-null reason wins) - the same shape `IncidentStack.tsx`'s own
// `lockedFor` already applies to `busyLane`/`busyLane2`.
//
// It lives HERE rather than in `IncidentStack.tsx` because the spec table is
// here: a spec that names two capabilities and a resolver that reads one is a
// lock that grades half the rule. `IncidentStack.tsx`'s `lockedFor` calls this,
// so `ignore_weather` (control.capture AND view.weather) is refused on whichever
// of the two the principal is missing, by name.
export function capLockReason(
  s: Pick<IncidentActionSpec, "cap" | "cap2">,
  gate: GateStoreSlice,
): string | null {
  if (s.cap) {
    const r = lockReason({ cap: s.cap }, gate);
    if (r) return r;
  }
  if (s.cap2) {
    const r = lockReason({ cap: s.cap2 }, gate);
    if (r) return r;
  }
  return null;
}

// ------------------------------------------------------------------- firing

export interface ActionContext {
  /** `useFrameSettings("focus")` - the sweep runs at the FOCUS scope's numbers,
   *  not the capture scope's. */
  focusFrame: { exposure_s: number; gain: number; binning: number };
  /** What `ACCEPT n°C` will send. Composed by the caller so the LABEL and the
   *  BODY can never carry different numbers. */
  coolerTargetC: number | null;
  /** Local "I have read this" dismissal, by incident kind. */
  onDismiss: (kind: string) => void;
  /** Whether weather is already ignored tonight, so IGNORE can offer UNDO. */
  weatherIgnored: boolean;
}

/** Fire one action. Throws on a server refusal so the caller can toast the
 *  server's own message rather than a generic one. */
export async function runIncidentAction(
  id: string, kind: string, ctx: ActionContext,
): Promise<void> {
  const s = specFor(id);
  if (!s) return;

  switch (s.kind) {
    case "local":
      ctx.onDismiss(kind);
      return;
    case "client":
      // Reconnection is already automatic; this is the impatient path, and it
      // is the same function the shell calls on mount.
      connectWs();
      return;
    case "nav":
      if (s.to === "archive:trash") { nav.sheet("archive", { tab: "trash" }); return; }
      nav.go(s.to!);
      // The legacy drawer's unseen-error badge is zeroed either way, so the
      // count does not keep announcing something the operator has now read.
      if (id === "view_log") useStore.getState().openLog();
      return;
    case "endpoint":
      break;
  }

  switch (id) {
    case "ignore_weather":
      await setIgnoreTonight(!ctx.weatherIgnored);
      return;
    case "retry_solve":
      await api.post(s.path!);
      return;
    case "retry_af":
      // `AutofocusBody.filter` is a WHEEL SLOT INDEX, not a name, and the frame
      // scope carries a name - so the filter is left to the server's own
      // "stay where you are" default rather than sending a guessed slot.
      await api.post(s.path!, {
        exposure_s: ctx.focusFrame.exposure_s,
        gain: ctx.focusFrame.gain,
        binning: ctx.focusFrame.binning,
      });
      return;
    case "reacquire":
    case "stop_guiding":
    case "stop_run":
      await api.post(s.path!);
      return;
    case "accept_setpoint":
      if (ctx.coolerTargetC == null) return;
      await api.post(s.path!, { on: true, target_c: ctx.coolerTargetC });
      return;
    default:
      await api.post(s.path!);
  }
}

// --------------------------------------------------------------- the copy

export interface RefineContext {
  seq: SequenceState;
  status: RigStatus | null;
  safety: SafetyState | null;
  focus: FocusEvent | null;
  lastAutofocusPosition: number | null;
  mountOp: { stuck?: boolean; error_arcmin?: number; attempt?: number } | null;
  telemetryStale: boolean;
  wsPhase: string;
  wsLastEvent: number | null;
  lastCaptureAtMs: number | null;
  nowMs: number;
  weatherIgnored: boolean;
  /** `config.escalation.on_no_progress ?? config.safety.on_unsafe`, or null. */
  escalation: string | null;
  /** Tablet or wider: the Connection sheet exists there. */
  wide: boolean;
  coolerTargetC: number | null;
}

const RECENTRING = [
  "re-centring after guiding loss",
  "re-centring: the guided field walked",
];

function act(id: string, label: string, primary = false): IncidentAction {
  return { id, label, primary, cap: INCIDENT_ACTIONS[id]?.cap };
}

/** Which actions this incident is allowed to show, after the four with no verb
 *  are dropped and the honest replacements are added. */
export function actionsFor(inc: Incident, ctx: RefineContext): IncidentAction[] {
  const keep = inc.actions.filter((a) => specFor(a.id) != null);
  switch (inc.kind) {
    case "cloud": {
      const out = keep.map((a) =>
        a.id === "ignore_weather" && ctx.weatherIgnored
          ? { ...a, label: "UNDO - STOP IGNORING WEATHER", primary: true }
          : a.id === "ignore_weather" ? { ...a, primary: true } : { ...a, primary: false });
      return out;
    }
    case "link":
      return ctx.wide ? [...keep, act("open_connection", "CONNECTION")] : keep;
    case "guide":
      return [...keep, act("stop_guiding", "STOP GUIDING")];
    case "stall":
      // The shipped module offers RESUME, which can only 409 on a running run.
      return [act("view_log", "VIEW LOG", true), act("stop_run", "STOP THE RUN")];
    case "cooler":
      return keep.map((a) => (a.id === "accept_setpoint" && ctx.coolerTargetC != null
        ? { ...a, label: `ACCEPT ${ctx.coolerTargetC}°C`, primary: true }
        : a));
    case "disk":
      return keep.map((a) => (a.id === "free_disk" ? { ...a, primary: true } : { ...a, primary: false }));
    default:
      return keep;
  }
}

function coolerOf(status: RigStatus | null): CoolerInfo | null {
  return status?.camera?.cooler ?? null;
}

function diskOf(status: RigStatus | null): DiskInfo | null {
  return status?.disk ?? null;
}

/** Re-state one incident from the live engine fields, and drop the actions with
 *  no verb. The kind, pill, colour and `sinceMs` are the lib's; the sentences
 *  and the action list are this hub's. */
export function refineIncident(inc: Incident, ctx: RefineContext): Incident {
  const detail = ctx.seq.detail ?? "";
  const out: Incident = { ...inc, actions: actionsFor(inc, ctx) };

  switch (inc.kind) {
    case "cloud": {
      out.engine = detail.startsWith("held for cloud")
        ? detail
        : "Capture paused at the frame boundary. Guiding parked, mount tracking, "
          + "cooler holding at setpoint - nothing to redo when it clears.";
      const sky = ctx.seq.sky?.text ? ` (${ctx.seq.sky.text})` : "";
      out.next = `Watching the star count and the cloud score${sky}; the ledger `
        + "keeps the sub count, so the plan picks up mid-pass.";
      out.resolvesItself = true;
      break;
    }
    case "safety": {
      const r = ctx.safety?.reading ?? null;
      const src = (r?.source || r?.reason || "unknown").toUpperCase();
      out.title = `SAFETY TRIP · ${src}`;
      if (r?.stale) {
        out.engine = "Safety reading stale - treating as unsafe";
      } else if (detail.startsWith("paused (unsafe)") || detail.startsWith("roof closed (unsafe)")) {
        out.engine = detail;
      } else {
        const parked = ctx.seq.state === "paused"
          || (ctx.status?.busy_lanes ?? []).includes("park");
        const reason = r?.reason ? `${r.reason}. ` : "";
        out.engine = parked
          ? `${reason}Capture stopped, mount parked, roof closed, cooler warming. `
            + "The flow is suspended, not killed."
          : `${reason}The run is ${ctx.seq.state}. The flow is suspended, not killed.`;
      }
      out.next = "The run re-arms itself when the monitor reads safe again; if the "
        + "window has closed it writes the report instead.";
      // The design's own column: this one needs a person.
      out.resolvesItself = false;
      break;
    }
    case "link": {
      out.engine = "This phone cannot reach the rig computer. The session keeps "
        + "running there - nothing on the rig depends on the phone.";
      let next: string;
      if (ctx.telemetryStale && ctx.wsPhase === "up") {
        const age = ctx.wsLastEvent != null ? Math.max(0, ctx.nowMs - ctx.wsLastEvent) : 0;
        const notice = telemetryStaleNotice(age);
        next = notice ? `${notice.title}. ${notice.detail}` : "Reconnecting. The view below is the last state received.";
      } else {
        next = "Reconnecting. The view below is the last state received.";
      }
      out.next = `${next} Direct and relay are chosen in Settings > Connection.`;
      break;
    }
    case "solve": {
      const err = ctx.mountOp?.error_arcmin;
      const where = err != null
        ? ` - ${err.toFixed(1)}' off after attempt ${ctx.mountOp?.attempt ?? "?"}`
        : "";
      out.engine = `Centring is not converging${where}.`;
      out.next = "The engine retries the solve on its own; if it cannot centre it "
        + "sets this target aside and the run continues.";
      break;
    }
    case "af": {
      const f = ctx.focus;
      const pos = ctx.lastAutofocusPosition;
      out.engine = f?.message
        || (f as { advice?: string } | null)?.advice
        || `No V-curve minimum. The last good position ${pos ?? "?"} is restored.`;
      out.next = "Capture continues at the last good focus; the next filter change "
        + "or temperature step retries. The engine has already put the last good "
        + "position back - there is no button for it because there is nothing left to do.";
      break;
    }
    case "guide": {
      out.engine = RECENTRING.some((s) => detail.startsWith(s))
        ? detail
        : "The frame was discarded. The guider is re-acquiring on a new star.";
      out.next = "If it cannot re-acquire with a falling star count the engine treats "
        + "it as a cloud hold. Stopping the guider lets the run continue unguided on "
        + "this target; the plan is unchanged.";
      break;
    }
    case "disk": {
      const d = diskOf(ctx.status);
      if (d) {
        const critical = d.critical === true;
        out.title = `DISK ${critical ? "CRITICAL" : "LOW"} · ${d.free_gb.toFixed(1)} GB`;
        if (critical) out.color = "#ff5470";
      }
      out.engine = "Capture continues. The run stops cleanly and writes its report "
        + "when the disk runs out.";
      out.next = "Nothing frees space by itself. Deleting sessions already downloaded "
        + "frees the most.";
      out.resolvesItself = false;
      break;
    }
    case "cooler": {
      const c = coolerOf(ctx.status);
      const temp = ctx.status?.camera?.temperature;
      if (ctx.seq.end_reason === "cooling_skip") {
        out.title = "COOLING SKIPPED";
      } else if (c?.target_c != null) {
        out.title = `COOLER CANNOT HOLD ${c.target_c}°C`;
      }
      // `can_report_power: false` means the camera has no power figure at all;
      // printing one would be a number with nothing behind it.
      const power = c?.can_report_power && c.power != null ? `${c.power}% power, ` : "";
      const at = typeof temp === "number" ? `sensor at ${temp.toFixed(1)}°C. ` : "";
      out.engine = `${power}${at}The capture gate stays closed until the sensor is `
        + "stable at an achievable setpoint.";
      out.next = "Accepting a warmer setpoint re-opens the gate once the sensor "
        + "settles there.";
      out.resolvesItself = false;
      break;
    }
    case "stall": {
      const idleS = ctx.lastCaptureAtMs != null
        ? Math.max(0, (ctx.nowMs - ctx.lastCaptureAtMs) / 1000)
        : null;
      out.engine = idleS != null
        ? `No frame recorded for ${fmtDuration(idleS)}. The rig is still in a running state.`
        : "No frame has been recorded. The rig is still in a running state.";
      const serverTrip = ctx.safety?.reading?.is_safe === false
        && (ctx.safety.reading.reason ?? "").startsWith("no progress in ");
      out.next = serverTrip && ctx.escalation
        ? `The escalation policy is set to "${ctx.escalation}"; the run acts on it at `
          + "the next frame boundary."
        : "Nothing acts on this by itself - the no-progress watchdog is off or has not "
          + "tripped yet.";
      out.resolvesItself = false;
      break;
    }
    default:
      break;
  }
  return out;
}
