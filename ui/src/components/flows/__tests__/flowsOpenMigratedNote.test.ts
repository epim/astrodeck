// flowsOpenMigratedNote.test.ts - the one-time migration note reaches the flow
// log when a flow is opened (#150; spec 2026-09-23 section 3.6).
//
//   Run:  node --import tsx src/components/flows/__tests__/flowsOpenMigratedNote.test.ts   (from ui/)
//
// WHAT IS WORTH GUARDING HERE
//
// FLOW_SCHEMA 3's read rewrites a stored TARGET's `rotation` of 23.4 - the old
// palette default, which commanded a connected rotator to an angle nobody chose
// - to -1, "any angle". That silently changes what a saved flow does, so the
// server says so in `record.migrated: [{key, note}]` (server
// `store._migrate`), and a note that reaches no screen is a change nobody was
// told about. `flowsOpen` puts each note on the flow log at warn tone, which is
// the one strip BOTH editors draw (`components/flows/FlowLogStrip` and the
// #/next `canvas/FlowLogStrip`, plus the phone stage sheet).
//
// "Once per open": the file keeps its 23.4 until it is next written (a save, or
// a run's `touch_run`, which is why the server's `run_flow` logs the note as it
// starts), so every GET carries the note again, and every open says it exactly
// once - not once per compile, and not zero times because the log already had
// it.
//
// Every case names the mutant it kills and quotes the failure that mutant
// produced when it was run from a byte-for-byte backup of flowsSlice.ts.

/* eslint-disable @typescript-eslint/no-explicit-any */

// The slice imports lib/flowsApi -> lib/api -> lib/base, and base.ts reads
// `window.location.pathname` AT MODULE SCOPE. No request is made: the two
// flowsApi calls `flowsOpen` reaches are replaced below.
(globalThis as any).window = { location: { pathname: "/", origin: "http://local" } };
(globalThis as any).localStorage = { getItem: () => null, setItem() {}, removeItem() {} };

const { createFlowsActions, FLOWS_INIT } = await import("../flowsSlice");
const { flowsApi } = await import("../../../lib/flowsApi");
type FlowsHost = import("../flowsSlice").FlowsHost;
type FlowsState = import("../flowsSlice").FlowsState;

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}

/** The server's sentence (server `store.ROTATION_234_NOTE`). Copied, not
 *  parsed: this file tests that the UI SHOWS whatever note arrives, and the
 *  wording is the server's to change. */
const NOTE_234 =
  "angle 23.4 was the old palette default and commanded a connected rotator "
  + 'to PA 23.4; it now reads "any angle". Set it again if you meant it.';

function record(id: string, extra: Record<string, unknown> = {}) {
  return {
    id, name: `Flow ${id}`, folder: "My flows", tagline: "",
    graph: { nodes: [], edges: [] },
    created_ts: 1, updated_ts: 1, last_run: null, last_result: "", readonly: false,
    ...extra,
  };
}

/** What `GET /api/flows/{id}` answers, per id. */
let served: Record<string, unknown> = {};
let compiles = 0;
(flowsApi as any).get = async (id: string) => {
  if (!(id in served)) throw new Error(`no flow ${id}`);
  // A fresh copy per GET, as the wire gives: a note array shared between two
  // opens would let one open's mutation leak into the next.
  return JSON.parse(JSON.stringify(served[id]));
};
// A compile that succeeds, so `flowsCompile` appends nothing of its own and
// every log line in these tests is one `flowsOpen` wrote.
(flowsApi as any).compileDraft = async () => {
  compiles++;
  return { plan: {}, structural: [], issues: [], unmapped: [] };
};

/** The same set/get contract zustand hands the slice. */
function harness() {
  let state: FlowsHost;
  const set = (fn: (s: FlowsHost) => Partial<FlowsHost>) => {
    state = { ...state, ...fn(state) } as FlowsHost;
  };
  const get = () => state;
  const actions = createFlowsActions(set, get);
  state = { ...actions, flows: { ...FLOWS_INIT } } as FlowsHost;
  return { get flows(): FlowsState { return state.flows; }, a: actions };
}

// ============================================================ the note

// MUTANT "ignore migrated" (the loop over `migrationNotes(rec)` in flowsOpen
// deleted). Observed, 3/6 (this case, "once PER OPEN" and the no-sentence case):
//   x a record carrying one migrated note puts exactly that note on the log,
//     once, at warn: the note never reached the flow log
//     expected 1
//     got      0
await test("a record carrying one migrated note puts exactly that note on the log, once, at warn", async () => {
  served = { m: record("m", { migrated: [{ key: "rotation", note: NOTE_234 }] }) };
  const h = harness();
  await h.a.flowsOpen("m");
  eq(h.flows.record?.id, "m", "precondition: the flow opened");
  eq(h.flows.libraryError, null, "precondition: the open did not fail");
  eq(h.flows.logs.length, 1, "the note never reached the flow log");
  eq(h.flows.logs[0].msg, NOTE_234, "the log line is not the server's note, verbatim");
  eq(h.flows.logs[0].tone, "warn",
    "the note changes what a saved flow does; at info tone it reads as chatter");
});

await test("once PER OPEN: a second open of the same unsaved flow says it once more, not twice", async () => {
  served = { m: record("m", { migrated: [{ key: "rotation", note: NOTE_234 }] }) };
  const h = harness();
  await h.a.flowsOpen("m");
  await h.a.flowsOpen("m");
  eq(h.flows.logs.filter((l) => l.msg === NOTE_234).length, 2,
    "the file still holds 23.4 until a save, so each open must say so exactly once");
});

// CONTROL. Every record an older server sends has no `migrated` key at all, and
// every current record the read did not change has `migrated: []`.
//
// MUTANT "no guard" (the `if (!Array.isArray(list)) return [];` line in
// `migrationNotes` deleted) makes the first of these throw inside flowsOpen,
// whose catch turns it into a library error. Observed, 5/6:
//   x control: a record with no migrated key (an older server) appends nothing
//     and opens as before: an older server's record made the open fail
//     expected null
//     got      "Cannot read properties of undefined (reading 'map')"
await test("control: a record with no migrated key (an older server) appends nothing and opens as before", async () => {
  served = { old: record("old") };
  const h = harness();
  const compiledBefore = compiles;
  await h.a.flowsOpen("old");
  eq(h.flows.record?.id, "old", "precondition: the flow opened");
  eq(h.flows.libraryError, null, "an older server's record made the open fail");
  eq(h.flows.logs.length, 0, "a record with nothing migrated put a line on the log");
  eq(h.flows.ui.screen, "editor", "the open no longer lands in the editor");
  eq(compiles - compiledBefore, 1, "the open no longer compiles exactly once");
});

await test("control: an empty migrated list appends nothing", async () => {
  served = { cur: record("cur", { migrated: [] }) };
  const h = harness();
  await h.a.flowsOpen("cur");
  eq(h.flows.record?.id, "cur", "precondition: the flow opened");
  eq(h.flows.logs.length, 0, "an empty migrated list put a line on the log");
});

// MUTANT "no note check" (the `.filter` in `migrationNotes` made to keep every
// entry, whatever its `note`). Observed, 5/6:
//   x an entry with no sentence in it is not printed as one: a note with no
//     text reached the log
//     expected 1
//     got      3
await test("an entry with no sentence in it is not printed as one", async () => {
  // A malformed entry must not become a warn line reading "undefined" or an
  // empty row: both look like the rig saying something it did not.
  served = {
    odd: record("odd", {
      migrated: [{ key: "rotation" }, { key: "rotation", note: "" }, { key: "rotation", note: NOTE_234 }],
    }),
  };
  const h = harness();
  await h.a.flowsOpen("odd");
  eq(h.flows.logs.length, 1, "a note with no text reached the log");
  eq(h.flows.logs[0].msg, NOTE_234, "the one real note is the one printed");
});

await test("control: a failed open appends no note and keeps the previous record", async () => {
  served = { m: record("m", { migrated: [{ key: "rotation", note: NOTE_234 }] }) };
  const h = harness();
  await h.a.flowsOpen("m");
  const before = h.flows.logs.length;
  await h.a.flowsOpen("gone");
  eq(h.flows.logs.length, before, "a failed open wrote to the log");
  eq(h.flows.record?.id, "m", "a failed open replaced the record");
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`flowsOpenMigratedNote.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
