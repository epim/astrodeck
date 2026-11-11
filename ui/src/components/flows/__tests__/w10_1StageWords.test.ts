// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w10_1StageWords.test.ts - the two words WP 10.1 part a adds to the engine's
// stage status reach the canvases' one selector, from a RECORDED stream
// (#1231; server `SequenceEngine._stage_word`).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/w10_1StageWords.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE STREAM is `deferred_frames` in server/tests/fixtures/stage_status_stream.json:
// the `sequence` payloads of a clocked simulator night of a 2x2 mosaic (TARGET
// n2, FILTER CYCLE n7) whose every panel misses its first centring, reduced to
// state, session and stages and de-duplicated. The server test holds the
// recording to the engine (test_w10_1_stage_words.py
// `test_the_recorded_deferred_stream_is_what_the_engine_publishes`). Each frame
// goes through the store's own `handleEvent`, the path a WS frame takes.
//
// WHAT IS GRADED
//   1. `deferred` is a status of its own on the canvases' scale (warn), and a
//      stage the engine calls deferred is never BUSY;
//   2. `waiting`, and a word a later server may add, is left out: the stage
//      reads as unwritten (no word through a live run), which is what it
//      shows for a stage nobody has named, never a stale BUSY;
//   3. the recorded stream, frame by frame: every stage reads the word the
//      engine said, and the run's terminal frame leaves no status behind.
//
// MUTANT "deferred is not a known word" (the `deferred: "warn"` row of
// `STAGE_WORDS` removed, store.ts). Observed:
//   x a stage the engine calls deferred reads WARN, never BUSY: ... (expected "warn", got "none")
//   x the recorded deferred stream, frame by frame: ... (expected "n2:warn n7:warn", got "n2:none n7:none")
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
const { useStore, stageStatusOf, stageStatusesOf } = await import("../../../store");
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
    return doc.deferred_frames;
  } catch (e) {
    throw new Error(`cannot read ${STREAM_FILE}, the recorded stream this file replays: ${(e as Error).message}`);
  }
}
const FRAMES: any[] = readFrames();
if (!Array.isArray(FRAMES) || FRAMES.length < 4) {
  throw new Error("the recorded stream has no deferred_frames");
}
const SESSION: string = FRAMES[0].session.id;

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
const WORDS = (ids: string[]): string => ids.map((id) => `${id}:${wordOf(id)}`).join(" ");
const BY_ENGINE: Record<string, string> = {
  running: "busy", done: "ok", skipped: "warn", failed: "bad", deferred: "warn",
};

// ==================================================== 1. deferred is a status

test("a stage the engine calls deferred reads WARN, never BUSY", () => {
  const known = [SESSION];
  const out = stageStatusesOf(known, RUN({ n2: "deferred", n7: "running" }));
  eq(out.n2, "warn", "the deferred stage's status");
  eq(out.n7, "busy", "a running stage beside it");
  open();
  useStore.getState().handleEvent({ type: "sequence", data: RUN({ n2: "deferred" }) as any, ts: 1 });
  eq(wordOf("n2"), "warn", "the selector's answer for a deferred stage through a live run");
});

// ================================================= 2. a word nobody knows

test("waiting, and a word a later server adds, is unwritten: no word, never a stale BUSY", () => {
  const known = [SESSION];
  const out = stageStatusesOf(known, RUN({ n2: "running", n7: "waiting", n8: "paused-for-tea" }));
  eq(JSON.stringify(out), JSON.stringify({ n2: "busy" }), "only the words this build knows are kept");
  open();
  useStore.getState().handleEvent({ type: "sequence", data: RUN({ n7: "running" }) as any, ts: 1 });
  eq(wordOf("n7"), "busy", "precondition: n7 was BUSY");
  useStore.getState().handleEvent({ type: "sequence", data: RUN({ n7: "waiting" }) as any, ts: 2 });
  eq(wordOf("n7"), "none", "n7 once the engine says it is waiting: unwritten through the live run");
});

// ====================================================== 3. the recorded stream

test("the recorded deferred stream, frame by frame", () => {
  open();
  let sawDeferred = false;
  for (let i = 0; i < FRAMES.length; i++) {
    frame(i);
    const stages: Record<string, string> = FRAMES[i].stages ?? {};
    const live = FRAMES[i].state === "running";
    const want = Object.entries(stages)
      .map(([n, w]) => `${n}:${live ? BY_ENGINE[w] : "idle"}`).sort().join(" ");
    const ids = Object.keys(stages).sort();
    eq(WORDS(ids).split(" ").sort().join(" "), want, `frame ${i} (${FRAMES[i].state})`);
    if (live && Object.values(stages).includes("deferred")) {
      sawDeferred = true;
      for (const [n, w] of Object.entries(stages)) {
        if (w === "deferred") eq(wordOf(n), "warn", `frame ${i}: ${n} is deferred, so it is not BUSY`);
      }
    }
  }
  eq(sawDeferred, true, "premise: the recording holds a deferred stage");
  eq(Object.keys(state().flows.statuses).length, 0, "the terminal frame leaves no status behind");
});

// ----------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nw10_1StageWords.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
