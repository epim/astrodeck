// countsNotice.test.ts - the persistent counts line and the note behind it
// (#189; spec Revision 2, ruling 2; S4 orchestrator ruling 8).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/countsNotice.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS WORTH GUARDING HERE
//
// A flow saved before ruling 2 counts every sub taken, rejected ones
// included, until it is next saved; the server's save switches it. While it
// has not been switched, both editors show one line saying so. Three things
// can break that without anything erroring:
//
//   1. THE WORDS. The ruling fixes them, and they live in two languages: the
//      server's read says them (store.py `COUNTS_NOTE`, and app.py's
//      `COUNTS_DORMANT_ADDENDUM` when the flow has a dormant session), and
//      the editor draws its own copy. So this file PARSES the server's
//      sources and compares, as flowSettingsParity.test.ts does for
//      FLOW_SETTINGS. Do not replace the parser with literals.
//   2. WHEN IT SHOWS. The line follows the GRAPH (any TARGET or POOL that
//      counts attempts, by the server's own `counts_attempts`), not the note
//      the flow was opened with: a save that switches the counts must take it
//      down, or it goes on saying that saving switches a switched flow.
//   3. WHERE THE NOTE GOES. The open-time note is kept in state
//      (`flows.countsNote`) for the line, and no longer put on the flow log;
//      every other migrated note still is.
//
// Every mutant below was run in a private scratch copy of ui/ (#254), and the
// failure it produced is quoted verbatim.

/* eslint-disable @typescript-eslint/no-explicit-any */

// @ts-ignore  no @types/node guaranteed; tsx supplies fs at runtime
import { readFileSync } from "node:fs";

// The slice imports lib/flowsApi -> lib/api -> lib/base, and base.ts reads
// `window.location.pathname` AT MODULE SCOPE. No request is made: every
// flowsApi call the store cases reach is replaced below.
(globalThis as any).window = { location: { pathname: "/", origin: "http://local" } };
(globalThis as any).localStorage = { getItem: () => null, setItem() {}, removeItem() {} };

const {
  ACCEPTED_SUBS, COUNTED_TYPES, COUNTS_DORMANT_ADDENDUM, COUNTS_NOTE,
  acceptCounts, countsAttempts, countsNotice,
} = await import("../countsNotice");
const { COUNT_MODES } = await import("../nodeDefs");
const { createFlowsActions, FLOWS_INIT } = await import("../flowsSlice");
const { flowsApi } = await import("../../../lib/flowsApi");
type FlowsHost = import("../flowsSlice").FlowsHost;
type FlowsState = import("../flowsSlice").FlowsState;
type FlowGraphRec = import("../flowsTypes").FlowGraphRec;
type FlowNodeRec = import("../flowsTypes").FlowNodeRec;

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}

// ================================================= the server's sentences

/** A server source file, read when a case needs it: a missing file fails the
 *  case with a sentence, never skips it. */
function readPy(rel: string): string {
  try {
    return readFileSync(new URL(rel, import.meta.url), "utf8") as string;
  } catch (e) {
    throw new Error(`cannot read ${rel}; the counts parity cannot run: ${(e as Error).message}`);
  }
}

/** The value of a module-level string constant `NAME = "..."` or `NAME = (
 *  "..." "...")`, the implicit concatenation Python allows. Only
 *  double-quoted plain literals are read (the server writes these constants
 *  that way); anything else in the value (an f-string, a name, a `+`)
 *  throws, because guessing at it would compare against a sentence the
 *  server does not say. */
function pyStringConst(src: string, name: string): string {
  const at = new RegExp(`^${name}(?:\\s*:\\s*[\\w\\[\\], ]+)?\\s*=\\s*`, "m").exec(src);
  assert(at !== null, `${name} is not a module-level constant any more; did it move or get renamed?`);
  let i = at!.index + at![0].length;
  const paren = src[i] === "(";
  if (paren) i++;
  let out = "";
  let parts = 0;
  for (;;) {
    while (i < src.length && /\s/.test(src[i])) {
      if (!paren && src[i] === "\n") break;
      i++;
    }
    if (paren && src[i] === ")") break;
    if (!paren && (src[i] === "\n" || i >= src.length)) break;
    if (src[i] === "#") { while (i < src.length && src[i] !== "\n") i++; continue; }
    assert(src[i] === '"', `${name}: something other than a double-quoted literal at ${JSON.stringify(src.slice(i, i + 30))}`);
    i++;
    for (; i < src.length && src[i] !== '"'; i++) {
      if (src[i] === "\\") { i++; out += src[i] === "n" ? "\n" : src[i]; continue; }
      assert(src[i] !== "\n", `${name}: a literal runs past its line`);
      out += src[i];
    }
    i++;
    parts++;
  }
  assert(parts > 0, `${name} holds no string literal`);
  return out;
}

const STORE_PY = "../../../../../server/astrodeck/flows/store.py";
const APP_PY = "../../../../../server/astrodeck/api/app.py";
const SAVE_RULES_PY = "../../../../../server/astrodeck/flows/save_rules.py";

// MUTANT "note drift" (countsNotice.ts COUNTS_NOTE: "saving this flow
// switches it" -> "saving this flow changes it"). Observed (10/11 passed):
//   x COUNTS_NOTE is store.py's sentence, verbatim: the editor's counts line has drifted from the note the server's read says
//   expected "This flow counts every sub taken, rejected ones included. New flows count accepted subs only, and saving this flow switches it."
//   got      "This flow counts every sub taken, rejected ones included. New flows count accepted subs only, and saving this flow changes it."
await test("COUNTS_NOTE is store.py's sentence, verbatim", () => {
  eq(COUNTS_NOTE, pyStringConst(readPy(STORE_PY), "COUNTS_NOTE"),
    "the editor's counts line has drifted from the note the server's read says");
});

// MUTANT "addendum drift" (countsNotice.ts COUNTS_DORMANT_ADDENDUM: "until
// you CONTINUE" -> "until you continue"). Observed (9/11 passed; this case
// and the dormant store case, whose served note is built from the parsed
// sentence):
//   x COUNTS_DORMANT_ADDENDUM is app.py's sentence, joined as the route joins it: the editor's dormant addendum has drifted from the one the server's route adds
//   expected "Its armed session keeps its count until you CONTINUE."
//   got      "Its armed session keeps its count until you continue."
//   x the dormant addendum the route added reaches the line: the flow's dormant session went unmentioned
//   expected "This flow counts every sub taken, rejected ones included. New flows count accepted subs only, and saving this flow switches it. Its armed session keeps its count until you CONTINUE."
//   got      "This flow counts every sub taken, rejected ones included. New flows count accepted subs only, and saving this flow switches it."
await test("COUNTS_DORMANT_ADDENDUM is app.py's sentence, joined as the route joins it", () => {
  const app = readPy(APP_PY);
  eq(COUNTS_DORMANT_ADDENDUM, pyStringConst(app, "COUNTS_DORMANT_ADDENDUM"),
    "the editor's dormant addendum has drifted from the one the server's route adds");
  // The line joins the two with one space because the route does; a route
  // that joined them otherwise would make `countsNotice` build a sentence the
  // server never says.
  assert(app.includes('f"{n.note} {COUNTS_DORMANT_ADDENDUM}"'),
    "app.py no longer joins the counts note and the addendum as `f\"{n.note} {COUNTS_DORMANT_ADDENDUM}\"`");
});

await test("counts_attempts' two constants match save_rules.py", () => {
  const rules = readPy(SAVE_RULES_PY);
  eq(ACCEPTED_SUBS, pyStringConst(rules, "ACCEPTED_SUBS"), "ACCEPTED_SUBS");
  assert((COUNT_MODES as readonly string[]).includes(ACCEPTED_SUBS),
    "ACCEPTED_SUBS is not one of nodeDefs' COUNT_MODES");
  const m = /^COUNTED_TYPES[^=]*=\s*frozenset\(\{([^}]*)\}\)/m.exec(rules);
  assert(m !== null, "save_rules.py COUNTED_TYPES is not a frozenset literal any more");
  const py = Array.from(m![1].matchAll(/"([^"]+)"/g), (x) => x[1]).sort();
  eq([...COUNTED_TYPES].sort().join(","), py.join(","), "COUNTED_TYPES");
});

// ======================================================== the pure line

const node = (id: string, type: FlowNodeRec["type"], params: Record<string, string | number> = {}): FlowNodeRec =>
  ({ id, type, x: 0, y: 0, params });
const graphOf = (...nodes: FlowNodeRec[]): FlowGraphRec => ({ nodes, edges: [] });
const DORMANT_NOTE = `${COUNTS_NOTE} ${COUNTS_DORMANT_ADDENDUM}`;

await test("countsAttempts is the server's counts_attempts: anything but Accepted subs, on a TARGET or POOL", () => {
  const rows: [string, FlowNodeRec, boolean][] = [
    ["a TARGET with no counts key (saved before the key)", node("t", "target"), true],
    ['a TARGET counting "Every sub taken"', node("t", "target", { counts: "Every sub taken" }), true],
    ["a TARGET with a value this build does not offer", node("t", "target", { counts: "Keepers only" }), true],
    ["a TARGET counting accepted subs", node("t", "target", { counts: ACCEPTED_SUBS }), false],
    ["a POOL with no counts key", node("p", "pool"), true],
    ["a POOL counting accepted subs", node("p", "pool", { counts: ACCEPTED_SUBS }), false],
    ["a CAPTURE, which has no counts", node("c", "capture", { counts: "Every sub taken" }), false],
  ];
  const bad = rows.filter(([, n, want]) => countsAttempts(n) !== want)
    .map(([what, n, want]) => `${what}: expected ${want}, got ${countsAttempts(n)}`);
  assert(bad.length === 0, bad.join("; "));
});

// MUTANT "notice read from record.migrated only" (countsNotice.ts
// countsNotice: `if (!nodes.some(countsAttempts)) return null;` replaced by
// `if (!serverNote) return null;`, the line shown whenever the flow was
// opened with a note). Observed (8/11 passed; this case, the acceptCounts
// case on its last check, and the store's switch case below):
//   x the line follows the GRAPH; the note it was opened with adds only the addendum: attempts, opened with no note: expected "This flow counts every sub taken, rejected ones included. New flows count accepted subs only, and saving this flow switches it.", got null; switched, though opened with the dormant note: expected null, got "This flow counts every sub taken, rejected ones included. New flows count accepted subs only, and saving this flow switches it. Its armed session keeps its count until you CONTINUE."; no TARGET or POOL at all: expected null, got "This flow counts every sub taken, rejected ones included. New flows count accepted subs only, and saving this flow switches it. Its armed session keeps its count until you CONTINUE."; no graph: expected null, got "This flow counts every sub taken, rejected ones included. New flows count accepted subs only, and saving this flow switches it."
//   x acceptCounts switches exactly what the server's save switches, and keeps the rest: the switched graph still shows the line
await test("the line follows the GRAPH; the note it was opened with adds only the addendum", () => {
  const attempts = graphOf(node("t", "target"), node("c", "capture"));
  const switched = graphOf(node("t", "target", { counts: ACCEPTED_SUBS }), node("p", "pool", { counts: ACCEPTED_SUBS }));
  const rows: [string, string | null, string | null][] = [
    ["attempts, opened with the plain note", countsNotice(attempts, COUNTS_NOTE), COUNTS_NOTE],
    ["attempts, opened with the dormant note", countsNotice(attempts, DORMANT_NOTE), DORMANT_NOTE],
    // The graph decides, whatever the open said.
    ["attempts, opened with no note", countsNotice(attempts, null), COUNTS_NOTE],
    ["switched, though opened with the dormant note", countsNotice(switched, DORMANT_NOTE), null],
    ["switched, no note", countsNotice(switched, null), null],
    ["no TARGET or POOL at all", countsNotice(graphOf(node("c", "capture")), DORMANT_NOTE), null],
    ["no graph", countsNotice(null, COUNTS_NOTE), null],
  ];
  const bad = rows.filter(([, got, want]) => got !== want)
    .map(([what, got, want]) => `${what}: expected ${JSON.stringify(want)}, got ${JSON.stringify(got)}`);
  assert(bad.length === 0, bad.join("; "));
});

await test("acceptCounts switches exactly what the server's save switches, and keeps the rest", () => {
  // MUTANT "switch every block" (acceptCounts: the countsAttempts filter in
  // the map deleted, so a CAPTURE gains a counts key too). Observed (10/11
  // passed):
  //   x acceptCounts switches exactly what the server's save switches, and keeps the rest: done was rewritten though it needed no switch; c: {"exposure":60,"counts":"Accepted subs"}; c was rewritten though it needed no switch
  const g: FlowGraphRec = {
    nodes: [node("t", "target", { name: "M31" }), node("p", "pool", { counts: "Every sub taken" }),
            node("done", "target", { counts: ACCEPTED_SUBS }), node("c", "capture", { exposure: 60 })],
    edges: [{ id: "k1", from: "t", fromPort: "target", to: "c", toPort: "run" }],
    settings: { whenWaiting: "Wait for the mosaic" },
  };
  const out = acceptCounts(g);
  const bad: string[] = [];
  for (const n of out.nodes) {
    const was = g.nodes.find((x) => x.id === n.id)!;
    const want = countsAttempts(was) ? { ...was.params, counts: ACCEPTED_SUBS } : was.params;
    if (JSON.stringify(n.params) !== JSON.stringify(want)) bad.push(`${n.id}: ${JSON.stringify(n.params)}`);
    if (!countsAttempts(was) && n !== was) bad.push(`${n.id} was rewritten though it needed no switch`);
  }
  if (out.edges !== g.edges) bad.push("the wires were rewritten");
  if (JSON.stringify(out.settings) !== JSON.stringify(g.settings)) bad.push("the flow's settings were dropped");
  if (countsNotice(out, DORMANT_NOTE) !== null) bad.push("the switched graph still shows the line");
  assert(bad.length === 0, bad.join("; "));
  assert(acceptCounts(out) === out, "a graph with nothing to switch must come back as the same object");
});

// =========================================================== the store

function record(id: string, graph: FlowGraphRec, migrated: unknown[] = []) {
  return {
    id, name: `Flow ${id}`, folder: "My flows", tagline: "",
    graph: JSON.parse(JSON.stringify(graph)),
    created_ts: 1, updated_ts: 1, last_run: null, last_result: "", readonly: false,
    migrated,
  };
}

/** What `GET /api/flows/{id}` answers, per id, and the next PUT's notes.
 *  `putHold`, when set, keeps each PUT open until it is released. */
let served: Record<string, unknown> = {};
let putNotes: Record<string, unknown> = {};
let putHold: Promise<void> | null = null;
(flowsApi as any).get = async (id: string) => {
  if (!(id in served)) throw new Error(`no flow ${id}`);
  return JSON.parse(JSON.stringify(served[id]));
};
(flowsApi as any).save = async (_id: string, flow: any) => {
  if (putHold) await putHold;
  return JSON.parse(JSON.stringify({ ...flow, migrated: [], ...putNotes }));
};
(flowsApi as any).compileDraft = async () => ({ plan: {}, structural: [], issues: [], unmapped: [] });
(flowsApi as any).progress = async () => { throw new Error("Not Found"); };
(flowsApi as any).list = async () => [];
(flowsApi as any).folders = async () => [];

function harness() {
  let state: FlowsHost;
  const set = (fn: (s: FlowsHost) => Partial<FlowsHost>) => { state = { ...state, ...fn(state) } as FlowsHost; };
  const actions = createFlowsActions(set, () => state);
  state = { ...actions, flows: { ...FLOWS_INIT } } as FlowsHost;
  return { get flows(): FlowsState { return state.flows; }, a: actions };
}
/** What both editors draw, from the store as it stands. */
const line = (f: FlowsState) => countsNotice(f.graph, f.countsNote);

/** An old flow: a mosaic with no counts key and a stage. */
const OLD = graphOf(node("m31", "target", { name: "M31", rows: 3, cols: 2 }), node("cy", "cycle"));
const ROTATION_NOTE = "angle 23.4 was the old palette default and commanded a connected rotator to PA 23.4";

// MUTANT "counts note still logged" (flowsSlice.ts migrationNotes: the
// `m.key !== COUNTS_MIGRATION_KEY` filter deleted, so flowsOpen logs the
// counts note as it did before ruling 8). Observed (10/11 passed):
//   x the open-time counts note is kept in state and not logged; every other note is still logged: the flow log must carry the rotation note, and not the counts note the line now shows
//   expected "[warn] angle 23.4 was the old palette default and commanded a connected rotator to PA 23.4"
//   got      "[warn] angle 23.4 was the old palette default and commanded a connected rotator to PA 23.4 | [warn] This flow counts every sub taken, rejected ones included. New flows count accepted subs only, and saving this flow switches it."
await test("the open-time counts note is kept in state and not logged; every other note is still logged", async () => {
  served = { old: record("old", OLD, [{ key: "rotation", note: ROTATION_NOTE }, { key: "counts", note: COUNTS_NOTE }]) };
  const h = harness();
  await h.a.flowsOpen("old");
  eq(h.flows.record?.id, "old", "precondition: the flow opened");
  eq(h.flows.countsNote, COUNTS_NOTE, "the open-time counts note was not kept for the editors");
  eq(h.flows.logs.map((l) => `[${l.tone}] ${l.msg}`).join(" | "), `[warn] ${ROTATION_NOTE}`,
    "the flow log must carry the rotation note, and not the counts note the line now shows");
  eq(line(h.flows), COUNTS_NOTE, "the editors' line is not up over a flow that counts attempts");
});

await test("the dormant addendum the route added reaches the line", async () => {
  // The served note is built from app.py's parsed sentence, so this case
  // also fails when the TypeScript copy of the addendum drifts.
  const addendum = pyStringConst(readPy(APP_PY), "COUNTS_DORMANT_ADDENDUM");
  served = { old: record("old", OLD, [{ key: "counts", note: `${COUNTS_NOTE} ${addendum}` }]) };
  const h = harness();
  await h.a.flowsOpen("old");
  eq(line(h.flows), `${COUNTS_NOTE} ${addendum}`, "the flow's dormant session went unmentioned");
});

// MUTANT "notice read from record.migrated only" (as above): the line
// stays up after the save switches the counts. Observed:
//   x a save that switches the counts takes the line down, without a reopen: the line still says saving switches a flow the save has just switched
//   expected null
//   got      "This flow counts every sub taken, rejected ones included. New flows count accepted subs only, and saving this flow switches it. Its armed session keeps its count until you CONTINUE."
//
// MUTANT "no switch on the canvas" (flowsSlice.ts flowsSave: the
// `acceptCounts` write deleted, the graph left as it was sent). Observed
// (10/11 passed):
//   x a save that switches the counts takes the line down, without a reopen: the canvas still holds the counts the server just switched
//   expected "Accepted subs"
//   got      undefined
await test("a save that switches the counts takes the line down, without a reopen", async () => {
  served = { old: record("old", OLD, [{ key: "counts", note: DORMANT_NOTE }]) };
  const h = harness();
  await h.a.flowsOpen("old");
  eq(line(h.flows), DORMANT_NOTE, "precondition: the line is up");
  h.a.flowsSetParam("cy", "cycles", "7");
  putNotes = { migrated: [{ key: "counts", note: "now counts accepted subs only" }] };
  await h.a.flowsSave();
  putNotes = {};
  eq(h.flows.dirty, false, "precondition: the save completed, and the switch is not an edit");
  eq(h.flows.graph.nodes.find((n) => n.id === "m31")?.params.counts, ACCEPTED_SUBS,
    "the canvas still holds the counts the server just switched");
  eq(line(h.flows), null, "the line still says saving switches a flow the save has just switched");
  eq(h.flows.countsNote, DORMANT_NOTE, "the note is the open's, and a save leaves it alone");
});

await test("control: a save that switched nothing leaves the line up", async () => {
  served = { old: record("old", OLD, [{ key: "counts", note: COUNTS_NOTE }]) };
  const h = harness();
  await h.a.flowsOpen("old");
  h.a.flowsSetParam("cy", "cycles", "7");
  const before = h.flows.graph;
  putNotes = { migrated: [] };
  await h.a.flowsSave();
  eq(h.flows.dirty, false, "precondition: the save completed");
  eq(h.flows.graph, before, "a save that switched nothing rewrote the canvas");
  eq(line(h.flows), COUNTS_NOTE, "an older server did not switch the flow, so the line must stay");
});

// THE SWITCH GOES INTO THE GRAPH ON SCREEN, NOT THE ONE THAT WAS SENT (#215's
// class). The operator can keep editing while the PUT is out, and flowsSave
// keeps such an edit unsaved (`dirty` stays true). A switch written into the
// graph the save SENT would put the canvas back to that graph: the edit gone
// from the screen, while `dirty` still says there is something to save.
//
// MUTANT "switch the sent graph" (flowsSlice.ts flowsSave: `acceptCounts(graph)`,
// the graph read before the PUT, in place of `acceptCounts(s.flows.graph)`).
// Every other case here saves a graph nobody touched during the PUT, so it
// stayed green on them. Observed (verifier, 2026-09-26; 12/13 passed):
//   x an edit made while the save is in flight survives the counts switch, and stays unsaved: the edit made during the save's round trip was lost: cycles is 7, not 9
await test("an edit made while the save is in flight survives the counts switch, and stays unsaved", async () => {
  served = { old: record("old", OLD, [{ key: "counts", note: COUNTS_NOTE }]) };
  const h = harness();
  await h.a.flowsOpen("old");
  h.a.flowsSetParam("cy", "cycles", "7");
  let release!: () => void;
  putHold = new Promise<void>((r) => { release = r; });
  putNotes = { migrated: [{ key: "counts", note: "now counts accepted subs only" }] };
  const saving = h.a.flowsSave();
  h.a.flowsSetParam("cy", "cycles", "9");
  release();
  await saving;
  putHold = null;
  putNotes = {};
  const bad: string[] = [];
  const cycles = h.flows.graph.nodes.find((n) => n.id === "cy")?.params.cycles;
  if (cycles !== 9) bad.push(`the edit made during the save's round trip was lost: cycles is ${cycles}, not 9`);
  const counts = h.flows.graph.nodes.find((n) => n.id === "m31")?.params.counts;
  if (counts !== ACCEPTED_SUBS) bad.push(`the switch did not reach the canvas: counts is ${JSON.stringify(counts)}`);
  if (!h.flows.dirty) bad.push("the edit made during the save's round trip was marked saved");
  assert(bad.length === 0, bad.join("; "));
});

// CONTROL. An answer for a flow that is no longer open says nothing about the
// flow that is: switching THAT graph would change a flow the server has not
// switched, silently, and take its counts line down.
//
// MUTANT "switch before the stale check" (flowsSlice.ts flowsSave: the
// `acceptCounts` write moved into a `set` of its own straight after the PUT
// answers, ahead of the stale check). Observed (verifier, 2026-09-26; 11/13
// passed; the switch case above failed with it, because a write of its own
// replaces the graph object and flowsSave then reads the switch as an edit):
//   x a save that switches the counts takes the line down, without a reopen: precondition: the save completed, and the switch is not an edit
//   expected false
//   got      true
//   x control: a counts switch answered after another flow opened leaves that flow alone: the answer for "old" switched the counts of "other", which is open
//   expected undefined
//   got      "Accepted subs"
await test("control: a counts switch answered after another flow opened leaves that flow alone", async () => {
  served = {
    old: record("old", OLD, [{ key: "counts", note: COUNTS_NOTE }]),
    other: record("other", OLD, [{ key: "counts", note: COUNTS_NOTE }]),
  };
  const h = harness();
  await h.a.flowsOpen("old");
  h.a.flowsSetParam("cy", "cycles", "7");
  let release!: () => void;
  putHold = new Promise<void>((r) => { release = r; });
  putNotes = { migrated: [{ key: "counts", note: "now counts accepted subs only" }] };
  const saving = h.a.flowsSave();
  await h.a.flowsOpen("other");
  release();
  await saving;
  putHold = null;
  putNotes = {};
  eq(h.flows.record?.id, "other", "precondition: the other flow is open");
  eq(h.flows.graph.nodes.find((n) => n.id === "m31")?.params.counts, undefined,
    `the answer for "old" switched the counts of "other", which is open`);
  eq(line(h.flows), COUNTS_NOTE, "the other flow's counts line came down");
});

await test("control: close drops the note, and the next open replaces it", async () => {
  // MUTANT "note outlives the flow" (flowsCloseEditor: `countsNote: null`
  // deleted). Observed (10/11 passed):
  //   x control: close drops the note, and the next open replaces it: the closed flow's note is still held
  //   expected null
  //   got      "This flow counts every sub taken, rejected ones included. New flows count accepted subs only, and saving this flow switches it. Its armed session keeps its count until you CONTINUE."
  served = {
    old: record("old", OLD, [{ key: "counts", note: DORMANT_NOTE }]),
    cur: record("cur", graphOf(node("t", "target", { counts: ACCEPTED_SUBS }))),
  };
  const h = harness();
  await h.a.flowsOpen("old");
  await h.a.flowsCloseEditor();
  eq(h.flows.countsNote, null, "the closed flow's note is still held");
  await h.a.flowsOpen("old");
  await h.a.flowsOpen("cur");
  eq(h.flows.countsNote, null, "the previous flow's note survived the open of one that has none");
  eq(line(h.flows), null, "a switched flow shows the line");
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`countsNotice.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export const result = { passed, failed, total };
export default result;
