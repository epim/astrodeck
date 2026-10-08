// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w16RunLatchClosedFlow.test.ts - the run latch is cleared when a run ends with
// the flow closed, or another flow open, through the REAL slice (#717; WP-145,
// wave 16; wired at the wave 16 integration, when WP-117's flowsSlice.ts took
// the one-line change from WP-145's `blocked_on`).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/w16RunLatchClosedFlow.test.ts   (from ui/)
//
// The pure decision is `flowRunState.runLatchEnds` (graded in
// `w16RunLatchEnds.test.ts`); this file is the wiring: that `onSequence` asks it,
// on the sequence writes the store really makes, with no record open.
//
// NAMED MUTANT (from a byte backup in the worktree, restored and
// sha256-compared), "latch cleared for the open flow's run only":
// flowsSlice.ts `if (runLatchEnds(get().flows.run.phase, ended, prev, next)) {`
// made `if (ended && isRunPhaseLive(get().flows.run.phase)) {`, the line as it
// was before. RED, 3/5 passed:
//   x #717: a run that ends with NO FLOW OPEN clears the latch: start RUN,
//     close the editor, let the run end: run.phase is still 'running'
//     expected "idle"  got "running"
//   x #717: a run that ends while ANOTHER flow is open clears the latch:
//     another flow's run ended and the latch stayed up

/* eslint-disable @typescript-eslint/no-explicit-any */

(globalThis as any).window = {
  location: { pathname: "/", origin: "http://local" },
};
(globalThis as any).localStorage = {
  getItem: () => null, setItem() {}, removeItem() {},
};

const { createFlowsActions, FLOWS_INIT } = await import("../flowsSlice");
type FlowsHost = import("../flowsSlice").FlowsHost;
type FlowsState = import("../flowsSlice").FlowsState;
type SequenceState = import("../../../types").SequenceState;

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}

type Host = FlowsHost & { sequence: SequenceState };

/** The slice behind a miniature store that keeps zustand's whole contract (set,
 *  get, `subscribe` with state and previous), as w15FlowsAutosave.test.ts does. */
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
  return {
    get flows(): FlowsState { return state.flows; },
    seq(next: SequenceState) { set(() => ({ sequence: next })); },
    patchFlows(p: Partial<FlowsState>) { set((s: any) => ({ flows: { ...s.flows, ...p } })); },
  };
}

const live = (sid: string, frames = 0): SequenceState => ({
  state: "running", plan_name: "Flow f1", target: "t1",
  session: { id: sid, name: "Flow f1", count_mode: "attempts", accepted: 0 },
  progress: { frames_done: frames, frames_total: 10, percent: 0, elapsed_s: 0, rejected: 0 },
} as SequenceState);
const over = (): SequenceState => ({ state: "complete", end_reason: "complete" } as SequenceState);

/** What `flowsRun` leaves behind when its POST returns. */
const latched = { ...FLOWS_INIT.run, phase: "running" as const, startedAt: 1_700_000_000_000 };

test("regression: the OPEN flow's own run ending clears the latch (what the slice always did)", () => {
  const s = slice();
  s.patchFlows({
    record: { id: "f1", name: "Flow f1" } as any, sessionIds: ["S1"], run: latched,
  });
  s.seq(live("S1"));
  eq(s.flows.run.phase, "running", "premise: the latch is up while the run is live");
  s.seq(over());
  eq(s.flows.run.phase, "idle", "the open flow's own run ended and the latch stayed up");
});

test("#717: a run that ends with NO FLOW OPEN clears the latch", () => {
  const s = slice();
  // RUN pressed, editor closed: the record and the known sessions are gone, the
  // latch is not (`flowsCloseEditor` never touches `run`).
  s.patchFlows({ record: null, sessionIds: [], run: latched });
  s.seq(live("S1"));
  s.seq(over());
  eq(s.flows.run.phase, "idle",
    "start RUN, close the editor, let the run end: run.phase is still 'running'");
  eq(s.flows.run.startedAt, null, "the stamp outlived the latch");
});

test("#717: a run that ends while ANOTHER flow is open clears the latch", () => {
  const s = slice();
  s.patchFlows({
    record: { id: "f2", name: "Flow f2" } as any, sessionIds: ["S2"], run: latched,
  });
  s.seq(live("S1"));     // flow f1's run, not the open flow's
  s.seq(over());
  eq(s.flows.run.phase, "idle", "another flow's run ended and the latch stayed up");
});

test("a write inside a live run leaves the latch up", () => {
  const s = slice();
  s.patchFlows({ record: null, sessionIds: [], run: latched });
  s.seq(live("S1", 0));
  s.seq(live("S1", 1));
  s.seq({ ...live("S1", 1), state: "paused" } as SequenceState);
  eq(s.flows.run.phase, "running", "a frame or a pause ended the latch");
});

test("an idle latch is not rewritten when a run ends", () => {
  const s = slice();
  s.patchFlows({ record: null, sessionIds: [] });
  s.seq(live("S1"));
  const before = s.flows.run;
  s.seq(over());
  eq(s.flows.run, before, "flows.run was rewritten under an idle latch");
});

const total = passed + failed;
console.log(`w16RunLatchClosedFlow.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
