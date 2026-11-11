// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w10_1LaneNodes.test.ts - a flow's SLEW, AUTOFOCUS and GUIDE nodes, which the
// engine now names while it performs them, reach the canvases' one selector
// from a RECORDED stream (#1231, WP 10.1 part b; server
// `SequenceEngine._say_lane`).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/w10_1LaneNodes.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE STREAM is `lane_frames` in server/tests/fixtures/stage_status_stream.json:
// the `sequence` payloads of a clocked simulator night of a TARGET (n2) whose
// lane is SLEW (n3), AUTOFOCUS (n4), GUIDE (n5) and one CAPTURE LOOP (n6),
// beside a REPORT (n12), reduced to state, session and stages and
// de-duplicated. The server test holds the recording to the engine
// (test_w10_1_lane_nodes.py `test_the_recorded_lane_stream_is_what_the_engine_
// publishes`). Each frame goes through the store's own `handleEvent`, the path
// a WS frame takes.
//
// WHAT IS GRADED
//   1. a lane node reads what the engine said, in the canvases' words: BUSY
//      while it runs, OK when done, BAD when it failed;
//   2. the recorded stream, frame by frame: the SLEW node is BUSY then OK
//      before the AUTOFOCUS node is BUSY, and so on down the lane; the REPORT,
//      which the engine never names, draws nothing through the live run; the
//      terminal frame leaves no status behind.
//
// MUTANT "done is not a known word" (the `done: "ok"` row of `STAGE_WORDS`
// removed, store.ts). Observed:
//   x the recorded lane stream, frame by frame: ... (expected "n3:ok", got "n3:none")
//
// disk-reads: server/tests/fixtures/stage_status_stream.json, ui/src/api.ts, ui/src/api/, ui/src/components/
// disk-reads: ui/src/help.ts, ui/src/lib/, ui/src/next/, ui/src/store.ts, ui/src/ws.ts

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "localStorage", "sessionStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "requestAnimationFrame", "cancelAnimationFrame",
  "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}

// ------------------------------------------------------------------- imports
const { readFileSync } = await import("node:fs");
const { useStore, stageStatusOf } = await import("../../../store");
const { FLOWS_INIT } = await import("../flowsSlice");
const { flowsApi } = await import("../../../lib/flowsApi");
type FlowRecordRec = import("../flowsTypes").FlowRecordRec;
type SequenceState = import("../../../types").SequenceState;

// ------------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) {
    throw new Error(`${msg} (expected ${JSON.stringify(want)}, got ${JSON.stringify(got)})`);
  }
}

// A live run's progress re-read answers 404, which the slice leaves silent.
(flowsApi as any).progress = async () => { throw new Error("Not Found"); };

// ------------------------------------------------------------------ fixtures
// A missing or unreadable file FAILS the whole file, never skips it.
const STREAM_FILE = "../../../../../server/tests/fixtures/stage_status_stream.json";
function readFrames(): any[] {
  try {
    const doc = JSON.parse(readFileSync(new URL(STREAM_FILE, import.meta.url), "utf8") as string);
    return doc.lane_frames;
  } catch (e) {
    throw new Error(`cannot read ${STREAM_FILE}, the recorded stream this file replays: ${(e as Error).message}`);
  }
}
const FRAMES: any[] = readFrames();
if (!Array.isArray(FRAMES) || FRAMES.length < 6) {
  throw new Error("the recorded stream has no lane_frames");
}
const SESSION: string = FRAMES[0].session.id;
const SLEW = "n3", FOCUS = "n4", GUIDE = "n5", CAPTURE = "n6", REPORT = "n12";

const REC: FlowRecordRec = {
  id: "f1", name: "Flow f1", folder: "My flows", tagline: "",
  graph: { nodes: [], edges: [] },
  created_ts: 1, updated_ts: 1, last_run: null, last_result: "", readonly: false,
};

const RUN = (stages: Record<string, string>): SequenceState => ({
  state: "running",
  session: { id: SESSION, name: "Session", count_mode: "accepted", accepted: 0 },
  stages,
} as SequenceState);

function open(): void {
  useStore.setState({ sequence: { state: "idle" } } as any);
  useStore.setState({
    flows: {
      ...FLOWS_INIT, ui: { ...FLOWS_INIT.ui }, record: REC, graph: REC.graph,
      sessionIds: [SESSION], statuses: {},
    },
  } as any);
}
function frame(i: number): void {
  useStore.getState().handleEvent({ type: "sequence", data: FRAMES[i], ts: i });
}
const state = () => useStore.getState() as any;
const wordOf = (id: string): string => String(stageStatusOf(state(), id) ?? "none");
const BY_ENGINE: Record<string, string> = {
  running: "busy", done: "ok", skipped: "warn", failed: "bad", deferred: "warn",
};

// ====================================== 1. a lane node reads what is said

test("a lane node reads BUSY while it runs, OK when done and BAD when it failed", () => {
  open();
  useStore.getState().handleEvent({
    type: "sequence", ts: 1,
    data: RUN({ [SLEW]: "done", [FOCUS]: "running", [GUIDE]: "failed" }) as any,
  });
  eq(wordOf(SLEW), "ok", "the SLEW node once the slew is done");
  eq(wordOf(FOCUS), "busy", "the AUTOFOCUS node through a sweep");
  eq(wordOf(GUIDE), "bad", "the GUIDE node whose start failed");
  eq(wordOf(REPORT), "none", "a node the engine never names, through the live run");
});

// ====================================================== 2. the recorded stream

test("the recorded lane stream, frame by frame", () => {
  open();
  const seen: Record<string, string[]> = {};
  for (let i = 0; i < FRAMES.length; i++) {
    frame(i);
    const stages: Record<string, string> = FRAMES[i].stages ?? {};
    const live = FRAMES[i].state === "running";
    for (const id of [SLEW, FOCUS, GUIDE, CAPTURE, "n2"]) {
      const said = stages[id];
      const want = said === undefined ? (live ? "none" : "idle")
        : (live ? BY_ENGINE[said] : "idle");
      eq(wordOf(id), want, `frame ${i} (${FRAMES[i].state}): ${id}`);
      const words = (seen[id] ??= []);
      if (live && said !== undefined && words[words.length - 1] !== BY_ENGINE[said]) {
        words.push(BY_ENGINE[said]);
      }
    }
    eq(wordOf(REPORT), live ? "none" : "idle",
       `frame ${i}: the REPORT, which the engine never names`);
  }
  for (const id of [SLEW, FOCUS, GUIDE]) {
    eq((seen[id] ?? []).join(","), "busy,ok", `${id} is busy, then ok, once`);
  }
  eq(Object.keys(state().flows.statuses).length, 0, "the terminal frame leaves no status behind");
});

// ----------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nw10_1LaneNodes.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
