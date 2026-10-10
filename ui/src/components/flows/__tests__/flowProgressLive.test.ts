// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// flowProgressLive.test.ts - the TARGET chip ("212/315 subs") stays current
// while the open flow's run is shooting (#214; #189 S1 item 9, spec 1.2).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/flowProgressLive.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. The chip was read on open, after a save and when a run started,
// and never again: an operator who left the canvas open through the night saw
// the count the run started with, and the chip has no timestamp, so a frozen
// count looked like a current one. That count decides whether a target gets
// another night.
//
// WHAT IS GUARDED
//
//   1. A frame landing on the OPEN flow's run re-reads progress, at most once
//      per LIVE_PROGRESS_MIN_MS, and the run ending re-reads once more.
//   2. A live re-read does not blank the chip while it is in flight: the count
//      only grows during a run, so the answer in hand is still the right flow's
//      count, one frame behind. Reads on open, save and a new run still clear
//      first (flowProgressChip.test.tsx grades open and save; the run's is here).
//   3. The newest-read ticket and the record check still drop stale answers on
//      the live path.
//   4. No read for another flow's run, and none while the graph is dirty.
//   5. The real store (store.ts) hands the slice what it needs to watch the
//      sequence: the slice-level cases below build their own store, so without
//      case 5 the wiring could be missing and every other case still pass.
//   6. What one open leaves for the next: a newly opened flow is not held to
//      the previous flow's 30 s window, and sign-out forgets which sessions
//      were the open flow's (section 4b).
//
// Every guarded case names the mutant it kills and quotes the failure that
// mutant produced when it was run from a byte-for-byte backup of the file it
// changed (restored and hash-compared after each run).
//
// The clock is controlled (`Date.now`), the codebase's fake-timer idiom
// (captureStall.test.ts): the debounce reads Date.now and nothing else, so
// every advance below lands on a chosen instant and no test sleeps.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
// store.ts touches the document at import time (touch sizing), and lib/base.ts
// reads `window.location.pathname` at module scope.
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(`<!doctype html><html><body></body></html>`,
                      { url: "http://local/", pretendToBeVisual: true });
const win = dom.window as any;
win.matchMedia = () => ({ matches: false, addEventListener() {},
                          removeEventListener() {}, addListener() {},
                          removeListener() {} });
win.WebSocket = class { close() {} addEventListener() {} send() {} };
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "localStorage", "requestAnimationFrame", "cancelAnimationFrame",
  "getComputedStyle", "matchMedia", "WebSocket", "location",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}

let CLOCK = 1_700_000_000_000;
Date.now = () => CLOCK;
const advance = (ms: number) => { CLOCK += ms; };

const { useStore } = await import("../../../store");
const { flowsApi } = await import("../../../lib/flowsApi");
const { progressChip } = await import("../flowProgress");
const { createFlowsActions, FLOWS_INIT, LIVE_PROGRESS_MIN_MS } = await import("../flowsSlice");
const { clearedRigState } = await import("../../../lib/authGate");
const { NODE_DEFS } = await import("../nodeDefs");
type FlowProgress = import("../../../lib/flowsApi").FlowProgress;
type FlowsHost = import("../flowsSlice").FlowsHost;
type FlowsState = import("../flowsSlice").FlowsState;
type SequenceState = import("../../../types").SequenceState;

// ------------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}
/** Lets a read the action did not await settle. */
const flush = () => new Promise<void>((r) => setTimeout(r, 0));

// ------------------------------------------------------------------ fixtures
/** A flow's answer (f1 unless named): TARGET t1 has `banked` of 315 on
 *  session `sid` (null: the flow has never run). */
function answer(sid: string | null, banked: number, flowId = "f1"): FlowProgress {
  return {
    flow_id: flowId,
    session: sid ? { id: sid, status: "active", nights: 1, count_mode: "attempts" } : null,
    blocks: [{
      node_id: "t1", name: "t1", kind: "target", banked, owed: 315 - banked, total: 315,
      panels: [{ target_id: "tid-t1", name: "t1", row: 0, col: 0,
                 banked, owed: 315 - banked, total: 315, steps: [] }],
    }],
    orphaned: { frames: 0, steps: 0 },
  };
}

/** A sequence publish for a live run of session `sid`, `frames` done. */
function live(sid: string, frames: number, state: SequenceState["state"] = "running"): SequenceState {
  return {
    state, plan_name: "Flow f1", target: "t1",
    session: { id: sid, name: "Flow f1", count_mode: "attempts", accepted: frames },
    progress: { frames_done: frames, calibration_frames_done: 0, frames_total: 315, percent: 0, elapsed_s: 0, rejected: 0 },
  };
}
/** The publish that ends a run: the session sub-state is cleared with it. */
function over(state: SequenceState["state"] = "complete"): SequenceState {
  return { state, end_reason: state === "complete" ? "complete" : "aborted" };
}

function rec(id: string) {
  return {
    id, name: `Flow ${id}`, folder: "My flows", tagline: "",
    graph: { nodes: [{ id: "t1", type: "target", x: 0, y: 0,
                       params: { ...NODE_DEFS.target.params } }], edges: [] },
    created_ts: 1, updated_ts: 1, last_run: null, last_result: "", readonly: false,
  };
}

// ------------------------------------------------------------------- stubs
/** Every progress read the slice made, pending until a test settles it. */
interface Read { id: string; resolve: (v: FlowProgress) => void; reject: (e: unknown) => void }
let reads: Read[] = [];
(flowsApi as any).progress = (id: string) =>
  new Promise<FlowProgress>((resolve, reject) => { reads.push({ id, resolve, reject }); });
(flowsApi as any).get = async (id: string) => rec(id);
(flowsApi as any).compileDraft = async () => ({ plan: {}, structural: [], issues: [], unmapped: [] });
(flowsApi as any).save = async (id: string, flow: any) => ({ ...flow, id });
(flowsApi as any).list = async () => [];
(flowsApi as any).folders = async () => [];
/** What the next `POST /run` answers with. */
let runSession: string | null = "s1";
(flowsApi as any).run = async (id: string) => ({
  started: true, flow_id: id, frames: 315, unmapped: [],
  session: runSession === null ? undefined
    : { id: runSession, night: 1, continued: false, kept: 0, new: 1, dropped: 0 },
});

type Host = FlowsHost & { sequence: SequenceState };

/** The slice behind a miniature store that keeps zustand's whole contract:
 *  set, get, and `subscribe` with (state, previous) - the third argument
 *  zustand hands a state creator, which is how the slice watches `sequence`.
 *  Every state it passes through is recorded, so a case can ask whether the
 *  chip ever blanked. */
function slice() {
  let state: Host;
  const listeners = new Set<(s: Host, prev: Host) => void>();
  const seen: FlowsState[] = [];
  const set = (fn: (s: any) => any) => {
    const prev = state;
    state = { ...state, ...fn(state) } as Host;
    seen.push(state.flows);
    for (const l of listeners) l(state, prev);
  };
  const get = () => state;
  const api = {
    subscribe(l: (s: Host, prev: Host) => void) {
      listeners.add(l);
      return () => { listeners.delete(l); };
    },
  };
  const actions = createFlowsActions(set, get, api);
  state = { ...actions, flows: { ...FLOWS_INIT }, sequence: { state: "idle" } } as Host;
  reads = [];
  runSession = "s1";
  return {
    get flows(): FlowsState { return state.flows; },
    a: actions,
    seen,
    /** Publish a sequence state, the way store.ts's `sequence` case writes it. */
    seq(next: SequenceState) { set(() => ({ sequence: next })); },
    /** The sign-out gate's write (lib/authGate.ts `clearedRigState`), cut to
     *  the two fields this store holds. */
    signOut() {
      const c = clearedRigState();
      set(() => ({ flows: c.flows, sequence: c.sequence }));
    },
    chip(): string | null { return progressChip(state.flows.progress, "t1", state.flows.dirty); },
  };
}

/** Open f1 and land `ans` as its first read's answer. */
async function openWith(s: ReturnType<typeof slice>, ans: FlowProgress) {
  await s.a.flowsOpen("f1");
  const r = reads.at(-1);
  if (!r || r.id !== "f1") throw new Error("precondition: opening f1 read its progress");
  r.resolve(ans);
  await flush();
  eq(s.chip(), progressChip(ans, "t1", false), "precondition: the open's answer is on the chip");
}

// ========================================= 1. FRAMES RE-READ, AT MOST ONCE PER 30 S

// MUTANT "no live re-read" (the `void fetchProgress(true);` in the slice's
// sequence handler deleted). Observed, 2/12 (every case that needs a live
// read failed; only the two controls passed):
//   x frame advances re-read at most once per 30 s, and a later advance reads
//     again: while the open flow's run banked frames the chip never re-read
//     them, or re-read them on every frame
//     expected "1 read for 4 frames inside 30 s, 2 after a frame past it; chip 217/315 subs"
//     got      "0 read for 4 frames inside 30 s, 0 after a frame past it; chip 212/315 subs"
// MUTANT "no debounce" (the handler's `if (!ended && t - liveReadAt <
// LIVE_PROGRESS_MIN_MS) return;` deleted, so every advance reads). Observed,
// 10/12 (this case, and the run-end case, which got "3 live reads; chip
// 214/315 subs"):
//   x frame advances re-read at most once per 30 s, and a later advance reads
//     again: while the open flow's run banked frames the chip never re-read
//     them, or re-read them on every frame
//     expected "1 read for 4 frames inside 30 s, 2 after a frame past it; chip 217/315 subs"
//     got      "4 read for 4 frames inside 30 s, 5 after a frame past it; chip 217/315 subs"
await test("frame advances re-read at most once per 30 s, and a later advance reads again", async () => {
  eq(LIVE_PROGRESS_MIN_MS, 30_000, "the issue's cadence: at most one live read every 30 s");
  const s = slice();
  await openWith(s, answer("s1", 212));
  const base = reads.length;
  s.seq(live("s1", 212));                       // the run is live; nothing new yet
  eq(reads.length - base, 0, "precondition: a run going live with no new frame is not a read");
  advance(1_000); s.seq(live("s1", 213));       // the first frame: read
  advance(4_000); s.seq(live("s1", 214));
  advance(10_000); s.seq(live("s1", 215));
  advance(10_000); s.seq(live("s1", 216));      // 24 s after the read
  const inside = reads.length - base;
  reads.at(-1)?.resolve(answer("s1", 213));
  await flush();
  advance(LIVE_PROGRESS_MIN_MS); s.seq(live("s1", 217));
  const after = reads.length - base;
  reads.at(-1)?.resolve(answer("s1", 217));
  await flush();
  eq(`${inside} read for 4 frames inside 30 s, ${after} after a frame past it; chip ${s.chip()}`,
    "1 read for 4 frames inside 30 s, 2 after a frame past it; chip 217/315 subs",
    "while the open flow's run banked frames the chip never re-read them, or re-read them on every frame");
  eq(reads.slice(base).every((r) => r.id === "f1"), true, "a live read asked about another flow");
});

// MUTANT "clear first on a live refresh" (the sequence handler calls
// `fetchProgress()`, the clearing read, instead of `fetchProgress(true)`).
// Observed, 11/12:
//   x a live re-read keeps the chip on screen until its answer lands: the chip
//     blinked out while a live read was in flight; the count in hand was still
//     this flow's, one frame behind
//     expected "212/315 subs; 0 states with no chip"
//     got      "null; 1 states with no chip"
await test("a live re-read keeps the chip on screen until its answer lands", async () => {
  const s = slice();
  await openWith(s, answer("s1", 212));
  s.seq(live("s1", 212));
  const from = s.seen.length;
  advance(1_000); s.seq(live("s1", 213));
  eq(reads.length, 2, "precondition: the frame started a live read");
  const blank = s.seen.slice(from)
    .filter((f) => progressChip(f.progress, "t1", f.dirty) === null).length;
  eq(`${s.chip()}; ${blank} states with no chip`, "212/315 subs; 0 states with no chip",
    "the chip blinked out while a live read was in flight; the count in hand was still "
    + "this flow's, one frame behind");
  reads[1].resolve(answer("s1", 213));
  await flush();
  eq(s.chip(), "213/315 subs", "the live answer did not land");
});

// ============================================== 2. THE RUN ENDING READS ONCE MORE

// MUTANT "no run-end read" (the handler's `const ended = ...` made
// `const ended = false;`). Observed, 10/12 (this case, and the hold case,
// which got "0 reads through the hold and the wind-down, 0 when it stopped"):
//   x the run ending re-reads once more, inside the 30 s window: the last
//     frames of the run never reached the chip; it stays one frame short all
//     night
//     expected "2 live reads; chip 214/315 subs"
//     got      "1 live reads; chip 213/315 subs"
// MUTANT "debounce the run-end read" (the window test applied to it too:
// `!ended &&` dropped, leaving `if (t - liveReadAt < LIVE_PROGRESS_MIN_MS)
// return;`). Observed, 11/12:
//   x the run ending re-reads once more, inside the 30 s window: the last
//     frames of the run never reached the chip; it stays one frame short all
//     night
//     expected "2 live reads; chip 214/315 subs"
//     got      "1 live reads; chip 213/315 subs"
await test("the run ending re-reads once more, inside the 30 s window", async () => {
  const s = slice();
  await openWith(s, answer("s1", 212));
  const base = reads.length;
  s.seq(live("s1", 212));
  advance(1_000); s.seq(live("s1", 213));       // read
  reads.at(-1)?.resolve(answer("s1", 213));
  await flush();
  advance(9_000); s.seq(live("s1", 214));       // inside the window: no read
  advance(5_000); s.seq(over("complete"));      // the run ends: read
  advance(5_000); s.seq({ state: "idle" });     // nothing more to count
  const n = reads.length - base;
  reads.at(-1)?.resolve(answer("s1", 214));
  await flush();
  eq(`${n} live reads; chip ${s.chip()}`, "2 live reads; chip 214/315 subs",
    "the last frames of the run never reached the chip; it stays one frame short all night");
});

// MUTANT "live means running" (liveSessionOf's `runIsLive(seq)` made
// `seq?.state === "running"`). Observed, 11/12:
//   x a cloud hold and a wind-down are still the run: the one end read
//     waits for the stop: a hold or the abort's wind-down was taken for the
//     end of the run
//     expected "0 reads through the hold and the wind-down, 1 when it stopped"
//     got      "2 reads through the hold and the wind-down, 2 when it stopped"
await test("a cloud hold and a wind-down are still the run: the one end read waits for the stop", async () => {
  const s = slice();
  await openWith(s, answer("s1", 212));
  const base = reads.length;
  s.seq(live("s1", 212));
  advance(60_000); s.seq(live("s1", 212, "holding"));
  advance(60_000); s.seq(live("s1", 212));
  advance(60_000); s.seq(live("s1", 212, "aborting"));
  const through = reads.length - base;
  advance(60_000); s.seq(over("aborted"));
  const stopped = reads.length - base;
  eq(`${through} reads through the hold and the wind-down, ${stopped} when it stopped`,
    "0 reads through the hold and the wind-down, 1 when it stopped",
    "a hold or the abort's wind-down was taken for the end of the run");
});

// ====================================== 3. A NEW RUN: CLEARED FIRST, KNOWN AT ONCE

// MUTANT "the run's read keeps the chip" (flowsRun calls
// `fetchProgress(true)`). Observed, 11/12:
//   x a new run clears the chip first, and its frames re-read before any
//     answer names its session: a START OVER run went into a new session and
//     the old session's count stayed on the card
//     expected "null"
//     got      "212/315 subs"
// MUTANT "know the run only by the answer's session" (flowsRun's
// `noteSession(id, res.session?.id);` deleted). Observed, 11/12:
//   x a new run clears the chip first, and its frames re-read before any
//     answer names its session: the run RUN had just started was not
//     recognized as this flow's until a progress answer said so
//     expected 1
//     got      0
await test("a new run clears the chip first, and its frames re-read before any answer names its session", async () => {
  const s = slice();
  await openWith(s, answer("s1", 212));
  runSession = "s2";                             // START OVER: a new session
  eq(await s.a.flowsRun({ fresh: true }), null, "precondition: the run started");
  eq(String(s.chip()), "null",
    "a START OVER run went into a new session and the old session's count stayed on the card");
  eq(reads.length, 2, "precondition: the run read its progress");
  // The run's read fails, so no answer has named s2 yet.
  reads[1].reject(new Error("Not Found"));
  await flush();
  const base = reads.length;
  s.seq(live("s2", 0));
  advance(1_000); s.seq(live("s2", 1));
  eq(reads.length - base, 1,
    "the run RUN had just started was not recognized as this flow's until a progress answer said so");
  reads.at(-1)?.resolve(answer("s2", 1));
  await flush();
  eq(s.chip(), "1/315 subs", "the new session's first frame did not reach the chip");
});

// ===================================== 4. STALE LIVE ANSWERS ARE STILL DROPPED

// MUTANT "no ticket check" (the `ticket !== progressTicket` test in
// fetchProgress deleted). Observed, 11/12:
//   x a live answer overtaken by a save's read is dropped: the live read
//     started before the save landed after the save's read and put the old
//     recipe's count back on the card
//     expected "0/315 subs"
//     got      "213/315 subs"
await test("a live answer overtaken by a save's read is dropped", async () => {
  const s = slice();
  await openWith(s, answer("s1", 212));
  s.seq(live("s1", 212));
  advance(1_000); s.seq(live("s1", 213));
  eq(reads.length, 2, "precondition: the frame started a live read");
  s.a.flowsSetParam("t1", "name", "M 31");
  await s.a.flowsSave();
  eq(reads.length, 3, "precondition: the save read its progress");
  reads[2].resolve(answer("s1", 0));             // the saved recipe: nothing banked
  await flush();
  reads[1].resolve(answer("s1", 213));           // the live read lands LAST
  await flush();
  eq(s.chip(), "0/315 subs",
    "the live read started before the save landed after the save's read and put the old "
    + "recipe's count back on the card");
});

// MUTANT "no record check" (the `get().flows.record?.id !== id` test in
// fetchProgress deleted). Observed, 11/12:
//   x a live answer that lands after the editor closed is dropped: a closed
//     flow's live answer landed in the store; the next flow opened would
//     start out holding it
//     expected null
//     got      "f1"
await test("a live answer that lands after the editor closed is dropped", async () => {
  const s = slice();
  await openWith(s, answer("s1", 212));
  s.seq(live("s1", 212));
  advance(1_000); s.seq(live("s1", 213));
  eq(reads.length, 2, "precondition: the frame started a live read");
  await s.a.flowsCloseEditor();
  eq(s.flows.record, null, "precondition: the editor closed");
  reads[1].resolve(answer("s1", 213));
  await flush();
  eq(s.flows.progress?.flow_id ?? null, null,
    "a closed flow's live answer landed in the store; the next flow opened would start out holding it");
});

// ============================== 4b. WHAT ONE OPEN LEAVES BEHIND FOR THE NEXT

// A FLOW OPENED AFTER ANOTHER'S LIVE READ HAS ITS OWN WINDOW. f1's last read
// says nothing about how recently f2's chip was read. Here f1's run ends, the
// operator opens f2 and presses RUN, and f2's first frame lands 10 s after
// f1's end read: without the reset f2's chip sits on the run's first answer
// until f1's window runs out.
//
// MUTANT "no window reset on open" (flowsOpen's `liveReadAt = -Infinity;`
// deleted). Observed, 11/12:
//   x a flow opened after another flow's live read is not held to that read's
//     window: f1's last live read held back f2's: f2's chip waited out a
//     window it never opened
//     expected "1 reads for f2's first frame; chip 1/315 subs"
//     got      "0 reads for f2's first frame; chip 0/315 subs"
await test("a flow opened after another flow's live read is not held to that read's window", async () => {
  const s = slice();
  await openWith(s, answer("s1", 212));
  s.seq(live("s1", 212));
  advance(1_000); s.seq(live("s1", 213));        // f1's live read
  reads.at(-1)?.resolve(answer("s1", 213));
  await flush();
  advance(2_000); s.seq(over("complete"));       // f1's end read
  reads.at(-1)?.resolve(answer("s1", 213));
  await flush();
  advance(2_000);
  await s.a.flowsOpen("f2");
  eq(reads.at(-1)?.id, "f2", "precondition: opening f2 read its progress");
  reads.at(-1)?.resolve(answer(null, 0, "f2"));  // f2 has never run
  await flush();
  runSession = "s2";
  eq(await s.a.flowsRun(), null, "precondition: f2's run started");
  reads.at(-1)?.resolve(answer("s2", 0, "f2"));
  await flush();
  const base = reads.length;
  s.seq(live("s2", 0));
  advance(8_000); s.seq(live("s2", 1));          // 10 s after f1's end read
  const n = reads.length - base;
  reads.at(-1)?.resolve(answer("s2", 1, "f2"));
  await flush();
  eq(`${n} reads for f2's first frame; chip ${s.chip()}`,
    "1 reads for f2's first frame; chip 1/315 subs",
    "f1's last live read held back f2's: f2's chip waited out a window it never opened");
});

// SIGN-OUT FORGETS WHICH SESSIONS WERE THE OPEN FLOW'S. The slice keeps them
// only while a flow is open, and the gate's reset closes the editor, so after
// the next sign-in they are learned again from an answer, never carried
// across the login screen. Here the reopened flow's first read fails, so
// nothing has named s1 since.
//
// MUTANT "keep the sessions with no flow open" (the watcher's
// `if (flowSessions && !s.flows.record) flowSessions = null;` deleted).
// Observed, 11/12:
//   x sign-out forgets the open flow's sessions: a session learned before
//     sign-out still drove this flow's reads after it: the slice carried a
//     fact about the rig across the login screen
//     expected 0
//     got      1
await test("sign-out forgets the open flow's sessions", async () => {
  const s = slice();
  await openWith(s, answer("s1", 212));
  s.seq(live("s1", 212));
  // The positive half, so a slice with no live read at all cannot pass here:
  // before sign-out, a frame on s1 is f1's and re-reads it.
  advance(1_000); s.seq(live("s1", 213));
  eq(reads.length, 2, "precondition: before sign-out a frame on s1 re-read f1");
  reads[1].resolve(answer("s1", 213));
  await flush();
  s.signOut();
  eq(s.flows.record, null, "precondition: sign-out closed the editor");
  eq(reads.length, 2, "precondition: sign-out read nothing");
  await s.a.flowsOpen("f1");                     // signed in again
  reads.at(-1)?.reject(new Error("Not Found"));  // and no answer names s1
  await flush();
  const base = reads.length;
  s.seq(live("s1", 213));                        // the new socket's snapshot
  advance(LIVE_PROGRESS_MIN_MS); s.seq(live("s1", 214));
  eq(reads.length - base, 0,
    "a session learned before sign-out still drove this flow's reads after it: the slice "
    + "carried a fact about the rig across the login screen");
});

// ======================================================= 5. CONTROLS

// CONTROL: another flow's run. MUTANT "any live run is the open flow's"
// (openFlowOwns returns `sid !== null && id !== undefined`, dropping the
// session test). Observed, 10/12 (this case, and the sign-out case, which got
// 1 read where it wanted 0):
//   x CONTROL: another flow's run never re-reads this flow's chip: another
//     flow's frames re-read this flow's progress; its count cannot change
//     expected "0 reads; chip 212/315 subs"
//     got      "3 reads; chip 212/315 subs"
await test("CONTROL: another flow's run never re-reads this flow's chip", async () => {
  const s = slice();
  await openWith(s, answer("s1", 212));
  const base = reads.length;
  s.seq(live("other", 50));
  advance(1_000); s.seq(live("other", 51));
  advance(40_000); s.seq(live("other", 52));
  advance(1_000); s.seq(over("complete"));
  eq(`${reads.length - base} reads; chip ${s.chip()}`, "0 reads; chip 212/315 subs",
    "another flow's frames re-read this flow's progress; its count cannot change");
});

// CONTROL: a dirty graph. MUTANT "no dirty gate" (the handler's
// `if (!f.record || f.dirty) return;` made `if (!f.record) return;`).
// Observed, 11/12:
//   x CONTROL: no live read while the graph is dirty: the chip is hidden over
//     an unsaved edit and the save re-reads; these reads bought nothing
//     expected 0
//     got      3
await test("CONTROL: no live read while the graph is dirty", async () => {
  const s = slice();
  await openWith(s, answer("s1", 212));
  const base = reads.length;
  s.seq(live("s1", 212));
  s.a.flowsSetParam("t1", "name", "M 31");
  eq(s.flows.dirty, true, "precondition: the graph is dirty");
  advance(1_000); s.seq(live("s1", 213));
  advance(40_000); s.seq(live("s1", 214));
  advance(1_000); s.seq(over("complete"));
  eq(reads.length - base, 0,
    "the chip is hidden over an unsaved edit and the save re-reads; these reads bought nothing");
});

// ============================================== 6. THE APP STORE IS WIRED

/** Feed the real store one `sequence` bus event, the way the socket does. */
function seqEvent(data: SequenceState): void {
  useStore.getState().handleEvent({ type: "sequence", data: data as any, ts: CLOCK });
}

// MUTANT "store.ts does not hand the slice its api" (store.ts's
// `createFlowsActions(set, get, storeApi)` made `createFlowsActions(set,
// get)`). Observed, 11/12:
//   x the app store's sequence events keep the chip current: a frame landed
//     on the open flow's run through store.ts's sequence handler and the chip
//     never re-read it
//     expected "2 reads; chip 213/315 subs"
//     got      "1 reads; chip 212/315 subs"
await test("the app store's sequence events keep the chip current", async () => {
  useStore.setState({ flows: { ...FLOWS_INIT }, sequence: { state: "idle" } } as any);
  reads = [];
  await useStore.getState().flowsOpen("f1");
  eq(reads.length, 1, "precondition: the store's open read progress");
  reads[0].resolve(answer("s1", 212));
  await flush();
  seqEvent(live("s1", 212));
  advance(1_000); seqEvent(live("s1", 213));
  const n = reads.length;
  reads.at(-1)?.resolve(answer("s1", 213));
  await flush();
  const f = useStore.getState().flows;
  eq(`${n} reads; chip ${progressChip(f.progress, "t1", f.dirty)}`, "2 reads; chip 213/315 subs",
    "a frame landed on the open flow's run through store.ts's sequence handler and the chip never re-read it");
});

// ------------------------------------------------------------------- report
const total = passed + failed;
console.log(`flowProgressLive.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
