// nowIncidentActions.test.ts - every incident button either fires a route the
// server has, or is not drawn.
//
//   Run directly:  npx tsx src/next/hubs/session/__tests__/nowIncidentActions.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// THE MAIN ASSERTION READS THE SERVER. Every `kind: "endpoint"` path in the
// table is checked against `@app.post(...)` in `server/astrodeck/api/app.py`
// itself, not against a list copied into this file. A list would agree with the
// table forever, including on the day a route is renamed - and the symptom of
// that is a button that 404s at 3am on a rig with a stuck solve.
//
// THE OTHER HALF IS THE ABSENCES. Four actions in the design have no verb
// behind them (SWITCH TARGET, SKIP TARGET, KEEP LAST GOOD, SWITCH TO RELAY) and
// `next/lib/incidents.ts` emits three of them anyway. They must not reach a
// card: a button that does nothing teaches an operator that pressing things at
// 3am is how the night gets fixed.

/* eslint-disable @typescript-eslint/no-explicit-any */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import type { IncidentInputs } from "../../../lib/incidents";
import type { RefineContext } from "../now/incidentActions";
import type { SequenceState } from "../../../../types";

// The table itself is data, but the module that holds it also knows how to FIRE
// each row, so importing it pulls in `api.ts` -> `lib/base.ts`, which reads
// `window.location.pathname` at module scope. A four-line jsdom is cheaper than
// splitting a file the plan names as one.
const { JSDOM } = await import("jsdom");
const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://local/" });
const win = dom.window as any;
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} send() {} addEventListener() {} removeEventListener() {} };
const g = globalThis as any;
for (const k of ["window", "document", "navigator", "localStorage", "sessionStorage",
  "location", "history", "Element", "Node", "Event", "CustomEvent", "WebSocket",
  "matchMedia", "getComputedStyle"]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}

const { deriveIncidents } = await import("../../../lib/incidents");
const {
  actionsFor, INCIDENT_ACTIONS, OMITTED_ACTION_IDS, refineIncident, specFor,
} = await import("../now/incidentActions");
const { HUB_IDS } = await import("../../../router");

let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${String(want)}, got ${String(got)}`);
}

// ------------------------------------------------------- the server's routes

const APP_PY = fileURLToPath(new URL("../../../../../../server/astrodeck/api/app.py", import.meta.url));
let appSource = "";
try { appSource = readFileSync(APP_PY, "utf8"); } catch { appSource = ""; }

const serverPosts = new Set<string>();
for (const m of appSource.matchAll(/@app\.post\(\s*"([^"]+)"/g)) serverPosts.add(m[1]);

test("the server source was actually read - an empty set would pass everything", () => {
  assert(appSource.length > 1000, `could not read ${APP_PY}`);
  assert(serverPosts.size > 20, `only ${serverPosts.size} POST routes found - the scan is broken`);
  assert(serverPosts.has("/api/sequence/abort"), "the abort route is not in the scan");
});

test("every endpoint action posts to a path the server actually declares", () => {
  for (const [id, s] of Object.entries(INCIDENT_ACTIONS)) {
    if (s.kind !== "endpoint") continue;
    assert(!!s.path, `${id} is an endpoint action with no path`);
    assert(serverPosts.has(s.path!),
      `${id} posts to ${s.path}, which app.py does not declare as a POST route`);
  }
});

test("every nav action goes to a hub this router knows", () => {
  for (const [id, s] of Object.entries(INCIDENT_ACTIONS)) {
    if (s.kind !== "nav") continue;
    assert(!!s.to, `${id} is a nav action with no destination`);
    if (s.to!.includes(":")) continue;   // "archive:trash" is a sheet, not a path
    const hub = s.to!.split("/")[1];
    assert((HUB_IDS as readonly string[]).includes(hub),
      `${id} navigates to ${s.to}, whose hub "${hub}" is not in the router`);
  }
});

// --------------------------------------------------------------- the absences

test("the four verbs that do not exist are absent from the table", () => {
  for (const id of OMITTED_ACTION_IDS) {
    eq(specFor(id), null, `${id} has a table entry, but the server has no such verb:`);
  }
  // Both naming families: the plan's short names and the ids the lib emits.
  for (const id of ["switch", "switch_target", "skip", "skip_target",
    "useLast", "keep_last_good", "relay", "switch_relay"]) {
    assert(!(id in INCIDENT_ACTIONS), `${id} is in INCIDENT_ACTIONS and must not be`);
  }
});

// ------------------------------------------------------------ the real fold

const seq: SequenceState = {
  state: "running",
  detail: "",
  progress: {
    frames_done: 4, frames_total: 20, percent: 20, elapsed_s: 600, rejected: 0,
    current_exposure_s: 120,
  },
};

const CTX: RefineContext = {
  seq,
  status: {
    connected: {}, looping: false,
    disk: { free_gb: 1.2, low: true, critical: false },
    camera: {
      temperature: -3.4, can_cool: true, width: 1, height: 1, max_gain: 100,
      cooler: { on: true, power: 99, target_c: -10, at_target: false, can_report_power: true },
    },
  } as never,
  safety: { connected: true, streak: 3, reading: { is_safe: false, reason: "rain", source: "sim", stale: false, ts: 1_700_000_000 } },
  focus: { state: "failed", points: [], best: null, message: "only 3 stars in the field" },
  lastAutofocusPosition: 11218,
  mountOp: { stuck: true, error_arcmin: 4.2, attempt: 3 },
  telemetryStale: false,
  wsPhase: "down",
  wsLastEvent: 1_700_000_000_000,
  lastCaptureAtMs: 1_700_000_000_000,
  nowMs: 1_700_000_900_000,
  weatherIgnored: false,
  escalation: "park",
  wide: false,
  coolerTargetC: -2,
};

const inputs: IncidentInputs = {
  sequence: { state: "holding", detail: "", hold: { reason: "clouds" }, end_reason: undefined },
  safety: { is_safe: false, reason: "rain", stale: false, ts: 1_700_000_000_000 },
  wsPhase: "down",
  telemetryStale: false,
  wsLastEvent: 1_700_000_000_000,
  mountOp: { stuck: true, error_arcmin: 4.2, attempt: 3 },
  focus: { state: "failed" },
  lastAutofocusResult: { failed: true },
  guide: { guiding: false, lost: true },
  diskFreeBytes: 1.2e9,
  cooler: { on: true, at_setpoint: false, gate_open: false, setpoint: -10, temperature: -3.4 },
  lastCaptureAtMs: 1_700_000_000_000,
  expectedFrameS: 120,
};

const raw = deriveIncidents(inputs, CTX.nowMs);

test("the fixture really raises every kind - otherwise the sweeps below are vacuous", () => {
  const kinds = raw.map((i) => i.kind).sort().join(",");
  eq(kinds, "af,cloud,cooler,disk,guide,link,safety,solve,stall", "kinds raised:");
});

test("after refining, EVERY action on EVERY card has a verb behind it", () => {
  for (const inc of raw) {
    const refined = refineIncident({ ...inc, actions: inc.actions }, { ...CTX, seq: { ...seq, state: inc.kind === "cloud" ? "holding" : "running" } });
    assert(refined.actions.length > 0, `${inc.kind} was refined down to no actions at all`);
    for (const a of refined.actions) {
      assert(specFor(a.id) != null,
        `${inc.kind} still offers "${a.id}", which fires nothing`);
    }
  }
});

test("the omitted ids really were offered by the lib, and really were dropped", () => {
  const offered = new Set(raw.flatMap((i) => i.actions.map((a) => a.id)));
  // A positive control: if the lib ever stops emitting these, this test would
  // otherwise pass while asserting nothing.
  for (const id of ["switch_target", "skip_target", "keep_last_good", "continue_unguided", "switch_relay"]) {
    assert(offered.has(id), `the lib no longer emits ${id} - this guard has gone vacuous`);
  }
  for (const inc of raw) {
    const kept = actionsFor(inc, CTX).map((a) => a.id);
    for (const id of ["switch_target", "skip_target", "keep_last_good", "continue_unguided", "switch_relay"]) {
      assert(!kept.includes(id), `${inc.kind} kept ${id}`);
    }
  }
});

test("CONTINUE UNGUIDED is replaced by the honest verb, not just deleted", () => {
  const guide = raw.find((i) => i.kind === "guide")!;
  const ids = actionsFor(guide, CTX).map((a) => a.id);
  assert(ids.includes("stop_guiding"), `guide offers ${ids.join(",")} - STOP GUIDING is missing`);
  eq(INCIDENT_ACTIONS.stop_guiding.path, "/api/guide/stop", "stop_guiding path:");
});

test("the stall card offers a log and a stop, not a RESUME that can only 409", () => {
  const stall = raw.find((i) => i.kind === "stall")!;
  const ids = actionsFor(stall, CTX).map((a) => a.id);
  eq(ids.join(","), "view_log,stop_run", "stall actions:");
});

test("ACCEPT carries the number it will send - not a fixed one from the prototype", () => {
  const cooler = raw.find((i) => i.kind === "cooler")!;
  const accept = actionsFor(cooler, CTX).find((a) => a.id === "accept_setpoint")!;
  eq(accept.label, "ACCEPT -2°C", "the label must carry the posted setpoint:");
  const other = actionsFor(cooler, { ...CTX, coolerTargetC: 5 })
    .find((a) => a.id === "accept_setpoint")!;
  eq(other.label, "ACCEPT 5°C", "the label did not follow the number:");
});

test("the CONNECTION action appears only where the Connection sheet exists", () => {
  const link = raw.find((i) => i.kind === "link")!;
  assert(!actionsFor(link, CTX).some((a) => a.id === "open_connection"),
    "the phone offers a Connection sheet it cannot open");
  assert(actionsFor(link, { ...CTX, wide: true }).some((a) => a.id === "open_connection"),
    "the tablet does not offer the Connection sheet");
});

// ------------------------------------------------------------ the promises

test("no card promises a number the engine does not have", () => {
  const forbidden = [
    /3 clear frames/i,          // the prototype's, not the engine's
    /30 minutes after/i,        // there is no 30-minute safety re-arm
    /relay after 30 seconds/i,  // there is no client-side transport switch
    /blind solve/i,             // the engine publishes no such ladder
  ];
  for (const inc of raw) {
    const r = refineIncident(inc, CTX);
    for (const re of forbidden) {
      assert(!re.test(r.engine), `${inc.kind} ENGINE still says ${re}: "${r.engine}"`);
      assert(!re.test(r.next), `${inc.kind} NEXT still says ${re}: "${r.next}"`);
    }
  }
});

test("the KEEP LAST GOOD fact survives as a sentence once the button is gone", () => {
  const af = raw.find((i) => i.kind === "af")!;
  const r = refineIncident(af, CTX);
  assert(/last good/i.test(r.next), `the restored-focus fact was lost: "${r.next}"`);
  // and the ENGINE row prefers the server's own words over a generic sentence
  eq(r.engine, "only 3 stars in the field", "focus.message must win:");
});

test("a safety trip needs a person - `resolvesItself` is false, whatever the lib said", () => {
  const s = raw.find((i) => i.kind === "safety")!;
  eq(s.resolvesItself, true, "precondition: the lib says a safety trip resolves itself");
  eq(refineIncident(s, CTX).resolvesItself, false, "after refining:");
});

test("DISK CRITICAL is a different card from DISK LOW", () => {
  const d = raw.find((i) => i.kind === "disk")!;
  const low = refineIncident(d, CTX);
  assert(/DISK LOW/.test(low.title), `low title: ${low.title}`);
  const crit = refineIncident(d, {
    ...CTX,
    status: { ...(CTX.status as object), disk: { free_gb: 0.4, low: true, critical: true } } as never,
  });
  assert(/DISK CRITICAL/.test(crit.title), `critical title: ${crit.title}`);
  eq(crit.color, "#ff5470", "a critical disk is not amber:");
});

const total = passed + failed;
console.log(`nowIncidentActions.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
