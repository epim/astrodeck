// coerceParamFinite.test.ts - a numeric param keeps a number only when it is
// FINITE (#358; spec 2026-09-23 flows mosaic, 2.5 `coerceParam`).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/coerceParamFinite.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. `coerceParam` kept a numeric field's value unless it was NaN.
// `parseFloat` reads "Infinity", "-Infinity" and "1e999" as plus or minus
// Infinity, which is not NaN, so the node stored Infinity. `JSON.stringify`
// writes Infinity as null, so the save and every compile request carried
// `null` for that param: the server read its missing-key default, or refused,
// while the inspector kept showing what was typed until the flow was
// reloaded. The editor showing one value while the run uses another is the
// green-while-wrong class, and the comment describing the rule claimed
// something the code did not do.
//
// WHAT IS GRADED. Both doors into a node's params: `flowsSetParam` (every
// inspector field) and `flowsApplyFraming` (the TARGET modal's DONE), driven
// through a miniature store, plus `coerceParam` itself. For each of the three
// inputs the stored value AND the JSON that the compile request carries are
// read, because the JSON is where the old rule did its damage. The controls
// hold what must not change: every finite value is kept, `parseFloat`'s
// leniency ("12abc" is 12) is kept, and a TEXT default takes "Infinity" as
// the text it is (a TARGET may be named that).
//
// Every mutant below was run in a private scratch copy of ui/, never in the
// shared tree (#254), and the failure it produced is quoted verbatim.

/* eslint-disable @typescript-eslint/no-explicit-any */

// The slice imports lib/flowsApi -> lib/api -> lib/base, and base.ts reads
// `window.location.pathname` AT MODULE SCOPE. No request is made: the
// compile is replaced below.
(globalThis as any).window = { location: { pathname: "/", origin: "http://local" } };
(globalThis as any).localStorage = { getItem: () => null, setItem() {}, removeItem() {} };

const { createFlowsActions, FLOWS_INIT, coerceParam } = await import("../flowsSlice");
const { flowsApi } = await import("../../../lib/flowsApi");
const { NODE_DEFS } = await import("../nodeDefs");
type FlowsHost = import("../flowsSlice").FlowsHost;
type FlowsState = import("../flowsSlice").FlowsState;
type FlowGraphRec = import("../flowsTypes").FlowGraphRec;
type FlowNodeType = import("../flowsTypes").FlowNodeType;

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

/** A value as a failure prints it: JSON would call Infinity and NaN null,
 *  which is exactly the confusion this file is about. */
const show = (v: unknown): string => (typeof v === "number" && !Number.isFinite(v)
  ? String(v) : JSON.stringify(v));

// ---------------------------------------------------------------- compile
/** The JSON of every graph a compile request carried, as it went over the
 *  wire. */
let wire: string[] = [];
(flowsApi as any).compileDraft = async (graph: FlowGraphRec) => {
  wire.push(JSON.stringify(graph));
  return { plan: {}, structural: [], issues: [], unmapped: [] };
};

/** A miniature store holding one node of `type` with `params`. */
function harness(type: FlowNodeType, params: Record<string, string | number>) {
  let state: FlowsHost;
  const set = (fn: (s: FlowsHost) => Partial<FlowsHost>) => {
    state = { ...state, ...fn(state) } as FlowsHost;
  };
  const actions = createFlowsActions(set, () => state);
  const graph: FlowGraphRec = { nodes: [{ id: "n", type, x: 0, y: 0, params }], edges: [] };
  state = { ...actions, flows: { ...FLOWS_INIT, graph } } as FlowsHost;
  wire = [];
  return {
    get flows(): FlowsState { return state.flows; },
    param: (key: string) => state.flows.graph.nodes[0].params[key],
    a: actions,
  };
}

/** The three inputs #358 names. Each is a string `parseFloat` reads as an
 *  infinity, which is not NaN. */
const INFINITE_TEXT = ["Infinity", "-Infinity", "1e999"] as const;

const EXPOSURE = NODE_DEFS.capture.params.exposure as number;
const OVERLAP = NODE_DEFS.target.params.overlap as number;

// ========================================================= flowsSetParam

// MUTANT "Number.isNaN restored" (flowsSlice.ts coerceParam: `return
// Number.isFinite(v) ? v : base` put back to `return Number.isNaN(v) ? base
// : v`). Observed (coerceParamFinite.test: 2/6 passed; no other file goes
// red with it, flowsSlice.test, flowsApplyFraming.test and framingModel.test
// included, because none of their tables holds an infinity):
//   x flowsSetParam: 'Infinity', '-Infinity' and '1e999' revert to the numeric default: capture.exposure "Infinity": stored Infinity, expected 120; capture.exposure "-Infinity": stored -Infinity, expected 120; capture.exposure "1e999": stored Infinity, expected 120; target.overlap "Infinity": stored Infinity, expected 25; target.overlap "-Infinity": stored -Infinity, expected 25; target.overlap "1e999": stored Infinity, expected 25
await test("flowsSetParam: 'Infinity', '-Infinity' and '1e999' revert to the numeric default", () => {
  const bad: string[] = [];
  for (const [type, key, base, start] of [
    ["capture", "exposure", EXPOSURE, 240],
    ["target", "overlap", OVERLAP, 20],
  ] as const) {
    for (const raw of INFINITE_TEXT) {
      // Started off the default, so the revert is a real write and not a
      // value that was never touched.
      const h = harness(type, { ...NODE_DEFS[type].params, [key]: start });
      h.a.flowsSetParam("n", key, raw);
      const v = h.param(key);
      if (v !== base) bad.push(`${type}.${key} ${JSON.stringify(raw)}: stored ${show(v)}, expected ${show(base)}`);
    }
  }
  assert(bad.length === 0, bad.join("; "));
});

// The harm itself: the value the save and the compile send. Under the old
// rule every one of these went over the wire as null.
//
// MUTANT "Number.isNaN restored". Observed (coerceParamFinite.test: 2/6
// passed):
//   x flowsSetParam: what the save and the compile would send is the default, never null: "Infinity": serialised {"overlap":null}, expected {"overlap":25}; "-Infinity": serialised {"overlap":null}, expected {"overlap":25}; "1e999": serialised {"overlap":null}, expected {"overlap":25}
await test("flowsSetParam: what the save and the compile would send is the default, never null", () => {
  const bad: string[] = [];
  for (const raw of INFINITE_TEXT) {
    const h = harness("target", { ...NODE_DEFS.target.params, overlap: 20 });
    h.a.flowsSetParam("n", "overlap", raw);
    const sent = JSON.stringify({ overlap: h.param("overlap") });
    const want = JSON.stringify({ overlap: OVERLAP });
    if (sent !== want) bad.push(`${JSON.stringify(raw)}: serialised ${sent}, expected ${want}`);
  }
  assert(bad.length === 0, bad.join("; "));
});

// ===================================================== flowsApplyFraming

// The modal hands DONE text from its fields or numbers from its model, so
// both spellings of an infinity are graded, and the compile DONE starts is
// read off the wire.
//
// MUTANT "Number.isNaN restored". Observed (coerceParamFinite.test: 2/6
// passed):
//   x flowsApplyFraming: the three, as text and as numbers, revert to the default, and the compile carries it: "Infinity": stored Infinity, expected 25; "Infinity": the compile carried overlap null; "-Infinity": stored -Infinity, expected 25; "-Infinity": the compile carried overlap null; "1e999": stored Infinity, expected 25; "1e999": the compile carried overlap null; Infinity: stored Infinity, expected 25; Infinity: the compile carried overlap null; -Infinity: stored -Infinity, expected 25; -Infinity: the compile carried overlap null
await test("flowsApplyFraming: the three, as text and as numbers, revert to the default, and the compile carries it", async () => {
  const bad: string[] = [];
  for (const raw of [...INFINITE_TEXT, Infinity, -Infinity]) {
    const h = harness("target", { ...NODE_DEFS.target.params, overlap: 20 });
    await h.a.flowsApplyFraming("n", { overlap: raw });
    const label = typeof raw === "string" ? JSON.stringify(raw) : String(raw);
    const v = h.param("overlap");
    if (v !== OVERLAP) bad.push(`${label}: stored ${show(v)}, expected ${show(OVERLAP)}`);
    if (wire.length !== 1) { bad.push(`${label}: ${wire.length} compiles, expected 1`); continue; }
    const carried = JSON.parse(wire[0]).nodes[0].params.overlap;
    if (carried !== OVERLAP) bad.push(`${label}: the compile carried overlap ${JSON.stringify(carried)}`);
  }
  assert(bad.length === 0, bad.join("; "));
});

// ============================================================ coerceParam

// MUTANT "Number.isNaN restored". Observed (coerceParamFinite.test: 2/6
// passed):
//   x coerceParam keeps a number only when it is finite: coerceParam(25, "Infinity") = Infinity, expected 25; coerceParam(25, "-Infinity") = -Infinity, expected 25; coerceParam(25, "1e999") = Infinity, expected 25; coerceParam(25, Infinity) = Infinity, expected 25; coerceParam(25, -Infinity) = -Infinity, expected 25
await test("coerceParam keeps a number only when it is finite", () => {
  const rows: [string | number, number][] = [
    ["Infinity", 25], ["-Infinity", 25], ["1e999", 25], [Infinity, 25], [-Infinity, 25],
  ];
  const bad = rows.filter(([raw, want]) => coerceParam(25, raw) !== want)
    .map(([raw, want]) => `coerceParam(25, ${show(raw)}) = ${show(coerceParam(25, raw))}, expected ${show(want)}`);
  assert(bad.length === 0, bad.join("; "));
});

// ================================================================ controls

// Nothing the finite rule should leave alone moves: the largest finite
// double, exponents, negatives and zero are numbers; `parseFloat`'s leniency
// stays (the modal's model copies it, framingModel.test.ts); garbage and NaN
// still revert.
//
// MUTANT "every number reverts" (coerceParam: `return base` for any numeric
// default, the finite check made unconditional). Observed
// (coerceParamFinite.test: 5/6 passed; the cases above stay green, since
// they expect the default; flowsSlice.test, flowsApplyFraming.test and
// framingModel.test go red with it too):
//   x control: every finite value is kept, and garbage and NaN still revert: coerceParam(25, "240") = 25, expected 240; coerceParam(25, "1e3") = 25, expected 1000; coerceParam(25, "1e308") = 25, expected 1e+308; coerceParam(25, "-1") = 25, expected -1; coerceParam(25, "0") = 25, expected 0; coerceParam(25, "12abc") = 25, expected 12; coerceParam(25, 0.5) = 25, expected 0.5; flowsSetParam exposure "1e3": stored 120, expected 1000
await test("control: every finite value is kept, and garbage and NaN still revert", () => {
  const rows: [string | number, number][] = [
    ["240", 240], ["1e3", 1000], ["1e308", 1e308], ["-1", -1], ["0", 0], ["12abc", 12],
    [0.5, 0.5], ["banana", 25], ["", 25], [Number.NaN, 25],
  ];
  const bad = rows.filter(([raw, want]) => coerceParam(25, raw) !== want)
    .map(([raw, want]) => `coerceParam(25, ${show(raw)}) = ${show(coerceParam(25, raw))}, expected ${show(want)}`);
  const h = harness("capture", { ...NODE_DEFS.capture.params });
  h.a.flowsSetParam("n", "exposure", "1e3");
  if (h.param("exposure") !== 1000) bad.push(`flowsSetParam exposure "1e3": stored ${show(h.param("exposure"))}, expected 1000`);
  assert(bad.length === 0, bad.join("; "));
});

// A text default takes text: "Infinity" is a name a TARGET may carry, and a
// select keeps matching its string options. A key with no default is kept
// as given.
//
// MUTANT "finite check on every default" (coerceParam: the numeric branch's
// `typeof base === "number"` test widened to `base !== undefined`, so a text
// default is parsed as a number). Observed (coerceParamFinite.test: 5/6
// passed; flowsSlice.test's `bin` case and the parity tables in
// flowsApplyFraming.test and framingModel.test go red with it too). The name
// falls to the TARGET's missing-key default, which `flowsSetParam` coerces
// against:
//   x control: a text default keeps "Infinity" as text, and a key with no default keeps what it is given: coerceParam("", "Infinity") = "", expected "Infinity"; coerceParam("1", "1e999") = "1", expected "1e999"; flowsSetParam target.name "Infinity": stored "M31 - Andromeda", expected "Infinity"
await test("control: a text default keeps \"Infinity\" as text, and a key with no default keeps what it is given", () => {
  const rows: [string | number | undefined, string | number, string | number][] = [
    ["", "Infinity", "Infinity"], ["1", "1e999", "1e999"],
    [undefined, "Infinity", "Infinity"],
  ];
  const bad = rows.filter(([base, raw, want]) => coerceParam(base, raw) !== want)
    .map(([base, raw, want]) =>
      `coerceParam(${show(base)}, ${show(raw)}) = ${show(coerceParam(base, raw))}, expected ${show(want)}`);
  const h = harness("target", { ...NODE_DEFS.target.params, name: "" });
  h.a.flowsSetParam("n", "name", "Infinity");
  if (h.param("name") !== "Infinity") bad.push(`flowsSetParam target.name "Infinity": stored ${show(h.param("name"))}, expected "Infinity"`);
  assert(bad.length === 0, bad.join("; "));
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`coerceParamFinite.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export const result = { passed, failed, total };
export default result;
