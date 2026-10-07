// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w15FlowsAutosave.test.ts - the flow editor saves itself, a moment after the
// last edit, and never while a run of that flow is live (#688, part 2 of 3;
// WP-99).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/w15FlowsAutosave.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE OWNER'S REPORT. "We need to be dynamically saving flow changes as we
// go." Neither editor had an autosave: the canvas and the stored flow were two
// graphs from the first edit until a SAVE press or a way out, and every reader
// of the stored flow (Tonight's STORY, TIMELINE and CAMPAIGN, a RUN) described
// the old one. Part 1 (WP-86) made those readers save first; this part makes
// the standing state a short window.
//
// THE RULES UNDER TEST (the orchestrator's rulings for this WP)
//
//   1. 2000 ms of QUIET, then ONE PUT per burst: an edit inside the window
//      moves the deadline, it does not add a save.
//   2. NEVER for a read-only record: `flowsSave` declines one itself, so the
//      only way to see the guard is that nothing is armed and nothing is said.
//   3. NEVER while a run of THIS flow is live (a save re-anchors banked
//      counts), read the way the RUN button reads it: the sessions known to be
//      the flow's against the rig's sequence state, and the client's
//      optimistic phase only inside its bridge window (#647). Another flow's
//      run does not hold it. An edit the run held is saved once the run ends.
//   4. A FAILED PUT IS RETRIED ONLY ON THE NEXT EDIT, never on a timer, so a
//      server 422 does not loop; and an autosave that waited on a save which
//      then failed does not send that graph a second time.
//   5. ONE SAVE IN FLIGHT AT A TIME: an edit made while the PUT is out is
//      saved after it, not beside it (two PUTs in flight can answer out of
//      order, which `saveBeforeRead` already knows).
//   6. `flows.saving` is true exactly while a PUT is out: it is the SAVING
//      word both editors' pills draw.
//
// FAKE TIME. The slice reads `setTimeout` and `Date.now` at call time, so this
// file replaces both on `globalThis` with a virtual clock (`advance`): the
// 2000 ms is a value the tests assert, not a delay they sit through, and no
// assertion depends on a real clock's tick. The harness's own waits go through
// the real `setTimeout`, saved first.
//
// Every guarded case names the mutant it kills and quotes what that mutant
// produced when it was run from a byte-for-byte backup of flowsSlice.ts
// (restored and sha256-compared after each run, the mutant's marker grepped
// absent). Against the code before this WP (no autosave at all) the file was
// observed at 7/18, red for the same reason in every timing case ("expected
// 1, got 0" PUTs, and `AUTOSAVE_QUIET_MS` undefined); the seven that passed
// are the cases that assert nothing happens.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------- globals first
(globalThis as any).window = {
  location: { pathname: "/", origin: "http://local" },
};
(globalThis as any).localStorage = {
  getItem: () => null, setItem() {}, removeItem() {},
};

// ---------------------------------------------------------------- fake time
const realSetTimeout = globalThis.setTimeout;
interface Armed { id: number; due: number; fn: () => void }
let armed: Armed[] = [];
let nextTimerId = 1;
let CLOCK = 1_700_000_000_000;
Date.now = () => CLOCK;
(globalThis as any).setTimeout = (fn: () => void, ms = 0): number => {
  const id = nextTimerId++;
  armed.push({ id, due: CLOCK + ms, fn });
  return id;
};
(globalThis as any).clearTimeout = (id?: number): void => {
  armed = armed.filter((a) => a.id !== id);
};
/** One macrotask of the REAL clock: every promise chain that can finish
 *  without a timer has finished when this resolves, so a NEGATIVE ("no second
 *  PUT yet") is read after it. */
const flush = () => new Promise<void>((r) => realSetTimeout(r, 0));
/** Move the virtual clock forward, running every timer that falls due on the
 *  way in order, and letting what each one started run to its next wait. */
async function advance(ms: number): Promise<void> {
  const end = CLOCK + ms;
  for (;;) {
    const next = armed.filter((a) => a.due <= end).sort((a, b) => a.due - b.due)[0];
    if (!next) break;
    armed = armed.filter((a) => a !== next);
    CLOCK = Math.max(CLOCK, next.due);
    next.fn();
    await flush();
  }
  CLOCK = end;
  await flush();
}

const {
  createFlowsActions, FLOWS_INIT, AUTOSAVE_QUIET_MS, AUTOSAVE_RUN_BRIDGE_MS,
} = await import("../flowsSlice");
const { flowsApi } = await import("../../../lib/flowsApi");
const { NODE_DEFS } = await import("../nodeDefs");
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

// ------------------------------------------------------------------- stubs
function rec(id: string, readonly = false) {
  return {
    id, name: `Flow ${id}`, folder: "My flows", tagline: "",
    graph: { nodes: [{ id: "t1", type: "target", x: 0, y: 0,
                       params: { ...NODE_DEFS.target.params } }], edges: [] },
    created_ts: 1, updated_ts: 1, last_run: null, last_result: "", readonly,
  };
}

interface Put { id: string; flow: any; resolve: (v: unknown) => void; reject: (e: unknown) => void }
let puts: Put[] = [];
/** Every PUT the slice sent, in order, answered or not. */
let sent: Array<{ id: string; flow: any }> = [];
let readonlyRecord = false;
(flowsApi as any).save = (id: string, flow: any) => {
  sent.push({ id, flow });
  return new Promise((resolve, reject) => { puts.push({ id, flow, resolve, reject }); });
};
(flowsApi as any).get = async (id: string) => rec(id, readonlyRecord);
(flowsApi as any).compileDraft = async () => ({ plan: {}, structural: [], issues: [], unmapped: [] });
(flowsApi as any).progress = async () => { throw new Error("Not Found"); };
(flowsApi as any).list = async () => [];
(flowsApi as any).folders = async () => [];

/** The server's answer to a PUT: what it was sent, stored, with a new stamp. */
function answerPut(p: Put, ts = 2): void {
  p.resolve({ ...p.flow, id: p.id, updated_ts: ts });
}

type Host = FlowsHost & { sequence: SequenceState };

/** The slice behind a miniature store that keeps zustand's whole contract:
 *  set, get, and `subscribe` with (state, previous), which is how the slice
 *  watches both the sequence and the graph. */
function slice() {
  let state: Host;
  const listeners = new Set<(s: Host, prev: Host) => void>();
  const set = (fn: (s: any) => any) => {
    const prev = state;
    state = { ...state, ...fn(state) } as Host;
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
  puts = []; sent = []; armed = []; readonlyRecord = false;
  CLOCK = 1_700_000_000_000;
  return {
    get flows(): FlowsState { return state.flows; },
    a: actions,
    /** Publish a sequence state, the way store.ts's `sequence` case writes it. */
    seq(next: SequenceState) { set(() => ({ sequence: next })); },
    /** A write to `flows` that is not a graph edit (a hand-seeded run latch,
     *  the known sessions). */
    patchFlows(p: Partial<FlowsState>) { set((s: any) => ({ flows: { ...s.flows, ...p } })); },
    /** The sign-out gate's reset of `flows`, which waits for nothing. */
    resetFlows() { set(() => ({ flows: { ...FLOWS_INIT } })); },
  };
}

/** A publish for a live run of session `sid`. */
function live(sid: string): SequenceState {
  return {
    state: "running", plan_name: "Flow f1", target: "t1",
    session: { id: sid, name: "Flow f1", count_mode: "attempts", accepted: 0 },
    progress: { frames_done: 0, frames_total: 10, percent: 0, elapsed_s: 0, rejected: 0 },
  };
}
const over = (): SequenceState => ({ state: "complete", end_reason: "complete" });

const nameOf = (flow: any): string =>
  String(flow?.graph?.nodes?.find((n: any) => n.id === "t1")?.params?.name ?? "-");
const edit = (s: ReturnType<typeof slice>, v: string) => s.a.flowsSetParam("t1", "name", v);
const logLines = (f: FlowsState) => f.logs.map((l) => `${l.tone}: ${l.msg}`);
const autosaveLines = (f: FlowsState) => logLines(f).filter((l) => /autosave/i.test(l));

/** Open f1 and let its compile settle. */
async function openF1(s: ReturnType<typeof slice>): Promise<void> {
  await s.a.flowsOpen("f1");
  await flush();
  eq(s.flows.record?.id, "f1", "precondition: f1 opened");
}

// ============================================================ 1. THE WINDOW

// The bridge window's drift from the RUN button's own is pinned beside the
// header's pill (w15FlowHeaderSaveState.test.tsx), which mounts the real store
// and so can import `flowRunControls`; this file runs without a DOM.
await test("the quiet window is 2000 ms", async () => {
  eq(AUTOSAVE_QUIET_MS, 2000, "the ruling: 2000 ms of quiet");
});

// MUTANT "autosave never scheduled" (the `scheduleAutosave();` of the slice's
// edit branch made `void 0;`). Observed, 9/18 (every case that needs a PUT):
//   x an edit is saved once, 2000 ms after it, and carries the edit: the flow
//     was not saved 2000 ms after an edit, or was saved before the window ended
//     expected "0 PUTs 1999 ms after the edit, 1 at 2000 ms"
//     got      "0 PUTs 1999 ms after the edit, 0 at 2000 ms"
// MUTANT "no debounce" (the same line made `void get().flowsSave();`).
// Observed, 7/18; here: got "1 PUTs 1999 ms after the edit, 1 at 2000 ms".
// MUTANT "saving never set" (the `set((s) => patch(s, { saving: true }));` of
// `flowsSave` deleted). Observed, 16/18; here:
//   x ...: the PUT is out and `saving` does not say so
//     expected true
//     got      false
// MUTANT "saving never cleared" (the `finally`'s write of `saving` deleted).
// Observed, 15/18; here:
//   x ...: the answered PUT left the flow unsaved or still saving
//     expected "dirty false, saving false"
//     got      "dirty false, saving true"
await test("an edit is saved once, 2000 ms after it, and carries the edit", async () => {
  const s = slice();
  await openF1(s);
  edit(s, "M 31");
  await advance(1999);
  const early = puts.length;
  await advance(1);
  eq(`${early} PUTs 1999 ms after the edit, ${puts.length} at 2000 ms`,
    "0 PUTs 1999 ms after the edit, 1 at 2000 ms",
    "the flow was not saved 2000 ms after an edit, or was saved before the window ended");
  eq(nameOf(puts[0]?.flow), "M 31", "the PUT did not carry the edit");
  eq(s.flows.saving, true, "the PUT is out and `saving` does not say so");
  answerPut(puts[0]);
  await flush();
  eq(`dirty ${s.flows.dirty}, saving ${s.flows.saving}`, "dirty false, saving false",
    "the answered PUT left the flow unsaved or still saving");
  await advance(30_000);
  eq(puts.length, 1, "the save's own answer scheduled another PUT");
});

// MUTANT "no debounce" (the edit branch calls `void get().flowsSave();` in
// place of `scheduleAutosave()`). Observed, 7/18; here:
//   x edits inside the window make ONE PUT, 2000 ms after the LAST one: an
//     edit inside the window did not move the deadline: the burst was saved
//     early or more than once
//     expected "0 PUTs before the last edit's 2000 ms, 1 at it"
//     got      "3 PUTs before the last edit's 2000 ms, 3 at it"
// MUTANT "the timer is not reset" (`cancelAutosave();` deleted from
// `scheduleAutosave`, so each edit leaves its own timer). Observed, 17/18;
// the same line, got "1 PUTs before the last edit's 2000 ms, 1 at it": the
// first edit's timer saved the burst 2000 ms before the quiet it was owed.
await test("edits inside the window make ONE PUT, 2000 ms after the LAST one", async () => {
  const s = slice();
  await openF1(s);
  edit(s, "M 31");
  await advance(1000);
  edit(s, "M 32");
  await advance(1000);
  edit(s, "M 33");
  await advance(1999);
  const beforeQuiet = puts.length;
  await advance(1);
  eq(`${beforeQuiet} PUTs before the last edit's 2000 ms, ${puts.length} at it`,
    "0 PUTs before the last edit's 2000 ms, 1 at it",
    "an edit inside the window did not move the deadline: the burst was saved early or more than once");
  eq(nameOf(puts[0]?.flow), "M 33", "the one PUT did not carry the burst's last edit");
  answerPut(puts[0]);
  await advance(10_000);
  eq(puts.length, 1, "a burst of three edits made more than one PUT");
});

// MUTANT "the name is not an edit" (`|| f.record.name !== p.record?.name`
// deleted from the edit test of `onFlowsWrite`). Observed, 17/18:
//   x a rename is an edit too: a rename was not autosaved
//     expected 1
//     got      0
await test("a rename is an edit too", async () => {
  const s = slice();
  await openF1(s);
  s.a.flowsSetName("Andromeda");
  await advance(2000);
  eq(puts.length, 1, "a rename was not autosaved");
  eq(puts[0]?.flow?.name, "Andromeda", "the PUT did not carry the new name");
  answerPut(puts[0]);
});

// MUTANT "a clean flow is eligible" (`&& f.dirty` deleted from
// `autosaveEligible`). Observed, 17/18:
//   x a flow nobody edited arms nothing, and neither does its own compile:
//     opening a clean flow armed an autosave timer
//     expected 0
//     got      1
// MUTANT "every write while dirty arms it" (`edited &&` deleted from the edit
// branch). Observed, 15/18: the failed save's own `libraryError` write arms a
// retry, and the close and open cases below see the timer left armed:
//   x a failed PUT is not retried until the next edit, and it says so: a
//     failed autosave was retried on a timer: a server 422 would loop all night
//     expected "1 PUTs in a minute"
//     got      "2 PUTs in a minute"
await test("a flow nobody edited arms nothing, and neither does its own compile", async () => {
  const s = slice();
  await openF1(s);
  eq(armed.length, 0, "opening a clean flow armed an autosave timer");
  await advance(30_000);
  eq(puts.length, 0, "a clean flow was PUT");
});

// ====================================================== 2. NEVER READ-ONLY

// MUTANT "readonly guard removed" (`&& !f.record.readonly` deleted from
// `autosaveEligible`). `flowsSave` declines a read-only record itself, so no
// PUT shows it; what shows is the timer and the line. Observed, 17/18:
//   x a read-only Example is never autosaved: nothing armed, nothing sent,
//     nothing said: an edit to an Example armed an autosave, sent one, or
//     announced that it could not save
//     expected "0 timers armed, 0 PUTs, 0 autosave lines"
//     got      "1 timers armed, 0 PUTs, 1 autosave lines"
await test("a read-only Example is never autosaved: nothing armed, nothing sent, nothing said", async () => {
  const s = slice();
  readonlyRecord = true;
  await openF1(s);
  eq(s.flows.record?.readonly, true, "precondition: the record is read-only");
  edit(s, "M 31");
  const armedAfterEdit = armed.length;
  await advance(10_000);
  eq(`${armedAfterEdit} timers armed, ${puts.length} PUTs, ${autosaveLines(s.flows).length} autosave lines`,
    "0 timers armed, 0 PUTs, 0 autosave lines",
    "an edit to an Example armed an autosave, sent one, or announced that it could not save");
  eq(s.flows.dirty, true, "the edit to the Example was marked saved");
});

// ============================================== 3. NEVER DURING A LIVE RUN

// MUTANT "live-run guard removed" (`if (runLive()) { heldByRun = true; return; }`
// in `autosaveFire` made `if (false) { ... }`). Observed, 15/18, the three
// cases that run a flow:
//   x none while the run latch says this flow's run is live: a flow was
//     autosaved while its own run was starting or running
//     expected 0
//     got      1
// (and the same two lines for the session case below and for the held edit's
// precondition, "nothing was saved during the run".)
await test("none while the run latch says this flow's run is live", async () => {
  const s = slice();
  await openF1(s);
  s.patchFlows({ run: { ...s.flows.run, phase: "running", startedAt: CLOCK } });
  edit(s, "M 31");
  await advance(60_000);
  eq(puts.length, 0, "a flow was autosaved while its own run was starting or running");
});

// MUTANT "the guard reads the phase only" (`|| openFlowOwns(liveSessionOf(
// seqSeen))` deleted from `runLive`). Observed, 16/18:
//   x none while the rig's live run is one of this flow's sessions: a flow was
//     autosaved while the rig's run on its session was live
//     expected 0
//     got      1
await test("none while the rig's live run is one of this flow's sessions", async () => {
  const s = slice();
  await openF1(s);
  s.patchFlows({ sessionIds: ["s1"] });
  s.seq(live("s1"));
  edit(s, "M 31");
  await advance(60_000);
  eq(puts.length, 0, "a flow was autosaved while the rig's run on its session was live");
});

// MUTANT "any live run holds it" (`openFlowOwns(liveSessionOf(seqSeen))` made
// `liveSessionOf(seqSeen) !== null` in `runLive`). Observed, 17/18:
//   x another flow's live run does not hold this flow's autosave: another
//     flow's run held this flow's edit unsaved
//     expected 1
//     got      0
await test("another flow's live run does not hold this flow's autosave", async () => {
  const s = slice();
  await openF1(s);
  s.patchFlows({ sessionIds: ["s1"] });
  s.seq(live("a-session-of-another-flow"));
  edit(s, "M 31");
  await advance(2000);
  eq(puts.length, 1, "another flow's run held this flow's edit unsaved");
});

// MUTANT "the latch never expires" (`(f.run.startedAt === null || Date.now()
// - f.run.startedAt < AUTOSAVE_RUN_BRIDGE_MS)` made `true` in `runLive`): a
// `phase` left live by a run that ended while another flow was open (#647's
// shape) holds this flow's autosave for the rest of the page's life.
// Observed, 17/18:
//   x a run latch past its bridge window, with no live run behind it, holds
//     nothing: a stale optimistic phase held the flow's autosave for good
//     expected 1
//     got      0
await test("a run latch past its bridge window, with no live run behind it, holds nothing", async () => {
  const s = slice();
  await openF1(s);
  s.patchFlows({ run: { ...s.flows.run, phase: "running",
                        startedAt: CLOCK - AUTOSAVE_RUN_BRIDGE_MS - 1 } });
  edit(s, "M 31");
  await advance(2000);
  eq(puts.length, 1, "a stale optimistic phase held the flow's autosave for good");
});

// MUTANT "a stamp-less latch is not believed" (`f.run.startedAt === null ||
// Date.now()` made `f.run.startedAt !== null && Date.now()` in `runLive`; this
// case was added by the independent verifier because that arm of the guard was
// pinned by nothing: the mutant passed 18/18). `flowsRun` always stamps the
// latch, so a live `phase` with no stamp is a state nothing real writes, and
// the slice reads it the safe way: as a run. The edit it held is saved once the
// latch is cleared (`f.run !== p.run`). Observed, 18/19:
//   x a live run latch with no stamp is believed, and the edit it held is saved
//     when the latch clears: a latch with no stamp was read as no run: the
//     flow was autosaved over what may be a live run
//     expected 0
//     got      1
await test("a live run latch with no stamp is believed, and the edit it held is saved when the latch clears", async () => {
  const s = slice();
  await openF1(s);
  s.patchFlows({ run: { ...s.flows.run, phase: "running", startedAt: null } });
  edit(s, "M 31");
  await advance(60_000);
  eq(puts.length, 0, "a latch with no stamp was read as no run: the flow was autosaved over what may be a live run");
  s.patchFlows({ run: { ...s.flows.run, phase: "idle", startedAt: null } });
  await advance(1999);
  const early = puts.length;
  await advance(1);
  eq(`${early} PUTs 1999 ms after the latch cleared, ${puts.length} at 2000 ms`,
    "0 PUTs 1999 ms after the latch cleared, 1 at 2000 ms",
    "the edit the latch held was not saved a quiet window after the latch cleared");
  answerPut(puts[0]);
});

// MUTANT "the held edit is forgotten" (`heldByRun = true;` deleted from
// `autosaveFire`): an edit made during the night sat unsaved after the run
// ended, under a pill that said UNSAVED EDITS, with no SAVE button in the
// classic editor to end it. Observed, 17/18:
//   x an edit the run held is saved 2000 ms after the run ends: the edit the
//     run held was never saved after the run ended, or was saved without the
//     quiet window
//     expected "0 PUTs 1999 ms after the run ended, 1 at 2000 ms"
//     got      "0 PUTs 1999 ms after the run ended, 0 at 2000 ms"
await test("an edit the run held is saved 2000 ms after the run ends", async () => {
  const s = slice();
  await openF1(s);
  s.patchFlows({ sessionIds: ["s1"] });
  s.seq(live("s1"));
  edit(s, "M 31");
  await advance(10_000);
  eq(puts.length, 0, "precondition: nothing was saved during the run");
  s.seq(over());
  await advance(1999);
  const early = puts.length;
  await advance(1);
  eq(`${early} PUTs 1999 ms after the run ended, ${puts.length} at 2000 ms`,
    "0 PUTs 1999 ms after the run ended, 1 at 2000 ms",
    "the edit the run held was never saved after the run ended, or was saved without the quiet window");
  eq(nameOf(puts[0]?.flow), "M 31", "the held save did not carry the edit");
  answerPut(puts[0]);
});

// ===================================================== 4. A FAILED PUT

// MUTANT "retry on a timer" (`scheduleAutosave();` added at the end of the
// failure branch of `autosaveFire`). Observed, 17/18:
//   x a failed PUT is not retried until the next edit, and it says so: a
//     failed autosave was retried on a timer: a server 422 would loop all night
//     expected "1 PUTs in a minute"
//     got      "2 PUTs in a minute"
await test("a failed PUT is not retried until the next edit, and it says so", async () => {
  const s = slice();
  await openF1(s);
  edit(s, "M 31");
  await advance(2000);
  eq(puts.length, 1, "precondition: the autosave sent its PUT");
  puts[0].reject(new Error("HTTP 422: that flow is refused"));
  await flush();
  eq(s.flows.saving, false, "a failed PUT left the flow saving");
  eq(s.flows.dirty, true, "a failed PUT marked the flow saved");
  await advance(60_000);
  eq(`${puts.length} PUTs in a minute`, "1 PUTs in a minute",
    "a failed autosave was retried on a timer: a server 422 would loop all night");
  eq(armed.length, 0, "a failed autosave left a timer armed");
  const said = autosaveLines(s.flows);
  eq(said.length, 1, `the failure was not said once on the flow log: ${JSON.stringify(said)}`);
  eq(/HTTP 422/.test(said[0]) && /next edit/.test(said[0]), true,
    `the line must carry the reason and say when it is tried again: ${said[0]}`);
  edit(s, "M 32");
  await advance(2000);
  eq(puts.length, 2, "the next edit after a failure was not saved");
  eq(nameOf(puts[1]?.flow), "M 32", "the retry did not carry the new edit");
  answerPut(puts[1]);
});

// MUTANT "retry what it waited on" (`if (carried) return;` made `if (false)
// return;` in `autosaveFire`). Observed, 17/18: the operator's SAVE failed,
// and the autosave that had been waiting behind it sent the same graph again.
//   x an autosave that waited behind a save which failed does not send that
//     graph again: the autosave re-sent the graph its neighbour's failed PUT
//     carried: a failure retried without an edit
//     expected "1 PUTs"
//     got      "2 PUTs"
await test("an autosave that waited behind a save which failed does not send that graph again", async () => {
  const s = slice();
  await openF1(s);
  edit(s, "M 31");
  const manual = s.a.flowsSave();
  eq(puts.length, 1, "precondition: the manual save is out");
  await advance(2000);
  puts[0].reject(new Error("HTTP 422: that flow is refused"));
  await manual;
  await flush();
  eq(`${puts.length} PUTs`, "1 PUTs",
    "the autosave re-sent the graph its neighbour's failed PUT carried: a failure retried without an edit");
});

// ================================================ 5. ONE SAVE IN FLIGHT

// MUTANT "autosave does not wait" (`if (savingPromise) await savingPromise;`
// deleted from `autosaveFire`). Observed, 17/18:
//   x an edit made while the PUT is out is saved after it, never beside it:
//     the second save went out beside the first
//     expected "1 PUTs while the first was still out"
//     got      "2 PUTs while the first was still out"
await test("an edit made while the PUT is out is saved after it, never beside it", async () => {
  const s = slice();
  await openF1(s);
  edit(s, "M 31");
  await advance(2000);
  eq(puts.length, 1, "precondition: the first PUT is out");
  edit(s, "M 32");
  await advance(2000);
  eq(`${puts.length} PUTs while the first was still out`, "1 PUTs while the first was still out",
    "the second save went out beside the first");
  answerPut(puts[0]);
  await flush();
  eq(puts.length, 2, "the edit made during the PUT was never saved");
  eq(nameOf(puts[1]?.flow), "M 32", "the second PUT did not carry the edit made during the first");
  answerPut(puts[1], 3);
  await flush();
  eq(s.flows.dirty, false, "the flow is still unsaved after both PUTs");
});

// ===================================================== 6. THE SAVING WORD

// MUTANT "saving cleared by any PUT" (the `finally`'s guarded write in
// `flowsSave` made `set((s) => patch(s, { saving: false }));`). Observed,
// 17/18:
//   x `saving` is true exactly while a PUT is out, however many overlap: the
//     first PUT's answer put the SAVING word out under the second
//     expected "saving true while the second PUT was out"
//     got      "saving false while the second PUT was out"
// MUTANT "saving never cleared" (that write deleted). Observed, 15/18; here,
// and in the first case of this file:
//   x ...: a failed PUT left the flow saving
//     expected false
//     got      true
await test("`saving` is true exactly while a PUT is out, however many overlap", async () => {
  const s = slice();
  await openF1(s);
  eq(s.flows.saving, false, "a flow that is only open is saving");
  edit(s, "M 31");
  const first = s.a.flowsSave();
  eq(s.flows.saving, true, "a PUT is out and `saving` is false");
  edit(s, "M 32");
  const second = s.a.flowsSave();
  eq(puts.length, 2, "precondition: two PUTs are out");
  answerPut(puts[0]);
  await first;
  eq(`saving ${s.flows.saving} while the second PUT was out`, "saving true while the second PUT was out",
    "the first PUT's answer put the SAVING word out under the second");
  answerPut(puts[1], 3);
  await second;
  eq(s.flows.saving, false, "both PUTs answered and the flow is still saving");
});

// ======================================== 7. CANCELLED WITH THE EDITOR

// MUTANT "close does not cancel" (the `cancelAutosave();` at the top of
// `flowsCloseEditor` deleted): a timer the close should have taken down is
// left to fire into the save the close is already making. Observed, 17/18:
//   x closing the editor takes the pending autosave down before its own save:
//     the close left the autosave armed beside its own save
//     expected "0 timers armed while the close's own PUT was out"
//     got      "1 timers armed while the close's own PUT was out"
await test("closing the editor takes the pending autosave down before its own save", async () => {
  const s = slice();
  await openF1(s);
  edit(s, "M 31");
  eq(armed.length, 1, "precondition: the edit armed one autosave");
  const closing = s.a.flowsCloseEditor();
  await flush();
  eq(`${armed.length} timers armed while the close's own PUT was out`,
    "0 timers armed while the close's own PUT was out",
    "the close left the autosave armed beside its own save");
  answerPut(puts[0]);
  await closing;
  await advance(10_000);
  eq(puts.length, 1, "the pending autosave sent a second PUT after the close");
});

// MUTANT "open does not cancel" (the `cancelAutosave();` at the top of
// `flowsOpen` deleted). Observed, 17/18:
//   x opening another flow takes the pending autosave down before saving the
//     one it leaves: the open left the leaving flow's autosave armed
//     expected "0 timers armed while the leaving flow's save was out"
//     got      "1 timers armed while the leaving flow's save was out"
await test("opening another flow takes the pending autosave down before saving the one it leaves", async () => {
  const s = slice();
  await openF1(s);
  edit(s, "M 31");
  const opening = s.a.flowsOpen("f2");
  await flush();
  eq(`${armed.length} timers armed while the leaving flow's save was out`,
    "0 timers armed while the leaving flow's save was out",
    "the open left the leaving flow's autosave armed");
  answerPut(puts[0]);
  await opening;
  eq(s.flows.record?.id, "f2", "precondition: f2 opened");
  await advance(10_000);
  eq(puts.length, 1, "the leaving flow's autosave sent a second PUT after the open");
});

// MUTANT "no cancel when the record goes" (the `!f.record` branch of
// `onFlowsWrite` made `return;` alone): the gate resets `flows` and waits for
// nothing. Observed, 17/18:
//   x a reset of flows (sign-out) takes the pending autosave down: an autosave
//     stayed armed behind the login screen
//     expected "0 timers armed after the sign-out reset"
//     got      "1 timers armed after the sign-out reset"
await test("a reset of flows (sign-out) takes the pending autosave down", async () => {
  const s = slice();
  await openF1(s);
  edit(s, "M 31");
  s.resetFlows();
  eq(`${armed.length} timers armed after the sign-out reset`, "0 timers armed after the sign-out reset",
    "an autosave stayed armed behind the login screen");
  await advance(10_000);
  eq(puts.length, 0, "an autosave sent a PUT after the flow was gone");
});

// ------------------------------------------------------------------- report
const total = passed + failed;
console.log(`w15FlowsAutosave.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
