// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w14TonightPanelReadsCanvas.test.tsx - the classic Tonight overlay describes
// the graph on the canvas, not the one last stored (#688, part 1 of 3; WP-86).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/w14TonightPanelReadsCanvas.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE OWNER'S REPORT, MOUNTED. The only block of a saved flow deleted, two
// filter cycles added, TONIGHT then STORY: the sentences described the old
// flow, and PLAN did too, until the flow was left and opened again.
// `w14FlowsFlushBeforeRead.test.ts` pins the store's half (the PUT precedes the
// read, the compile is waited for); this file mounts the real overlay over the
// real store and a fake rig that REMEMBERS what was PUT, so a panel that asked
// the store for the wrong read, or never asked, shows the old flow's words.
//
// The rig is the network (`fetch`), not a stub of the slice: the overlay, the
// store, the slice and `flowsApi` are the production ones.
//
// Every case names the mutant it kills and quotes what that mutant produced
// when run from a byte-for-byte backup of the file named (restored and
// sha256-compared after each run).

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;
// Overlay's useMediaQuery calls matchMedia during render.
win.matchMedia = () => ({
  matches: true, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "getComputedStyle", "matchMedia",
  "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- imports
import { installAutoRaf } from "../../../testing/rafPolyfill";
installAutoRaf(g);
import React from "react";
import { createRoot } from "react-dom/client";
import { act } from "react";

const { useStore } = await import("../../../store");
const { FLOWS_INIT } = await import("../flowsSlice");
const { NODE_DEFS } = await import("../nodeDefs");
const { TONIGHT_EXAMPLE_EDITS_NOTE, TONIGHT_UNSAVED_NOTE } = await import("../flowsTypes");
const TonightPanel = (await import("../TonightPanel")).default;

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
  finally {
    // A PUT still held when a case ends (it failed before it answered it) is
    // answered here: the slice keeps its save in a closure the whole file
    // shares, and every later flush would wait on it for ever.
    await act(async () => {
      for (const release of heldPuts) release(true);
      for (const release of heldTonight) release();
    });
    await settle();
  }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}
const tick = () => new Promise<void>((r) => setTimeout(r, 0));
/** Lets every chain that can finish without a timer finish; a NEGATIVE is read after it. */
const settle = async (n = 6): Promise<void> => {
  for (let i = 0; i < n; i++) await act(async () => { await tick(); });
};
async function until(cond: () => boolean, what: string, ms = 3000): Promise<void> {
  const end = Date.now() + ms;
  while (!cond()) {
    if (Date.now() > end) throw new Error(`timed out waiting for ${what}`);
    await act(async () => { await tick(); });
  }
}

// ------------------------------------------------------------- the fake rig
const FLOW_ID = "f1";
const STORED_GRAPH = {
  nodes: [{ id: "t1", type: "target", x: 0, y: 0, params: { ...NODE_DEFS.target.params } }],
  edges: [],
};
/** What the operator drew over it: the target gone, two filter cycles in. */
const CANVAS_GRAPH = {
  nodes: [
    { id: "c1", type: "cycle", x: 0, y: 0, params: { ...NODE_DEFS.cycle.params } },
    { id: "c2", type: "cycle", x: 0, y: 200, params: { ...NODE_DEFS.cycle.params } },
  ],
  edges: [],
};
const recordOf = (graph: unknown, readonly: boolean) => ({
  id: FLOW_ID, name: "NGC7331 Preferential Filtering", folder: "My flows", tagline: "",
  graph, created_ts: 1, updated_ts: 1, last_run: null, last_result: "", readonly,
});

/** What the rig holds: the last PUT it answered. */
let stored: any = recordOf(STORED_GRAPH, false);
let putMode: "now" | "hold" | "fail" = "now";
let heldPuts: Array<(ok: boolean) => void> = [];
/** While set, the tonight request is held open until the test releases it. */
let holdTonight = false;
let heldTonight: Array<() => void> = [];
let asked: string[] = [];
const typesOf = (graph: any): string => (graph?.nodes ?? []).map((n: any) => n.type).join(", ");

g.fetch = async (url: any, init?: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  asked.push(`${method} ${u}`);
  const body = init?.body ? JSON.parse(init.body) : undefined;
  const reply = (status: number, data: unknown) => ({
    ok: status >= 200 && status < 300, status, statusText: String(status),
    headers: { get: () => "application/json" },
    json: async () => data,
    text: async () => JSON.stringify(data),
  });
  if (u === `/api/flows/${FLOW_ID}` && method === "PUT") {
    const accept = () => {
      stored = { ...body.flow, id: FLOW_ID, updated_ts: stored.updated_ts + 1 };
      return reply(200, stored);
    };
    if (putMode === "fail") return reply(422, { detail: "a stage has no name" });
    if (putMode === "hold") {
      return new Promise((resolve) => {
        heldPuts.push((ok) => resolve(ok ? accept() : reply(422, { detail: "a stage has no name" })));
      });
    }
    return accept();
  }
  if (u === `/api/flows/${FLOW_ID}` && method === "GET") return reply(200, stored);
  if (u === `/api/flows/${FLOW_ID}/tonight` && method === "GET") {
    if (holdTonight) await new Promise<void>((resolve) => { heldTonight.push(resolve); });
    return reply(200, {
      ok: true, reason: "",
      night: { dusk_unix: 1000, dawn_unix: 2000, dark_start_unix: 1200, dark_end_unix: 1800 },
      flats: null, moon: null, targets: [], budget: [],
      brief: `Blocks: ${typesOf(stored.graph)}.`,
      story: [{ t_unix: null, label: "ANY", msg: "a story row", tone: "text" }],
      campaign: { is_campaign: false, has_pool: false, has_ledger: false, quota: null, note: "", members: [] },
    });
  }
  if (u === "/api/flows/compile") {
    return reply(200, { plan: { blocks: typesOf(body.graph) }, structural: [], issues: [], unmapped: [] });
  }
  if (u.endsWith("/progress")) return reply(404, { detail: "Not Found" });
  return reply(200, {});
};

// ------------------------------------------------------------------- mounts
const root = createRoot(win.document.getElementById("root"));
const q = (sel: string): any => win.document.querySelector(sel);
const bodyText = (): string => String(win.document.body.textContent);

/** The flow open with `graph` on the canvas and the stored one behind it. The
 *  compile in hand is the one `flowsOpen` made of the STORED graph. */
function seed(o: { tab: "story" | "plan"; dirty: boolean; readonly?: boolean; mode?: typeof putMode; holdTonight?: boolean }): void {
  asked = []; heldPuts = []; heldTonight = []; holdTonight = o.holdTonight === true; putMode = o.mode ?? "now";
  stored = recordOf(STORED_GRAPH, o.readonly === true);
  act(() => root.render(null));
  act(() => {
    useStore.setState({
      flows: {
        ...FLOWS_INIT,
        record: stored,
        graph: o.dirty ? CANVAS_GRAPH : STORED_GRAPH,
        dirty: o.dirty,
        compiled: { plan: { blocks: typesOf(STORED_GRAPH) }, structural: [], issues: [], unmapped: [], from: STORED_GRAPH },
        tonight: null,
        ui: { ...FLOWS_INIT.ui, tonightOpen: true, tonightTab: o.tab },
      },
    } as any);
  });
  act(() => root.render(React.createElement(TonightPanel)));
}
const noteText = (): string | null => q("[data-testid='tonight-stored-note']")?.textContent ?? null;
const putIndex = () => asked.indexOf(`PUT /api/flows/${FLOW_ID}`);
const readIndex = () => asked.indexOf(`GET /api/flows/${FLOW_ID}/tonight`);

// ======================================================= 1. THE OWNER'S REPORT

// MUTANT "flowsFetchTonight skips the flush" (flowsSlice.ts: the `await
// saveBeforeRead();` ahead of the first `superseded()` check in
// flowsFetchTonight made `void 0;`). Observed, 3/6 passed:
//   x the owner's report (#688), mounted: STORY after deleting the block and
//     adding two filter cycles reads the canvas, and the PUT came first: STORY
//     describes the flow as last saved, not as drawn: PLAN SUMMARYBlocks: target.
await test("the owner's report (#688), mounted: STORY after deleting the block and adding two filter cycles reads the canvas, and the PUT came first", async () => {
  seed({ tab: "story", dirty: true });
  await until(() => q("[data-tonight-brief]") !== null, "STORY's brief");
  const brief = String(q("[data-tonight-brief]").textContent);
  assert(brief.includes("Blocks: cycle, cycle."),
    `STORY describes the flow as last saved, not as drawn: ${brief}`);
  assert(putIndex() >= 0 && putIndex() < readIndex(),
    `Tonight was read before the edit was saved: ${asked.join(", ")}`);
  eq(noteText(), null, "a saved edit left a stored-flow note over STORY");
});

// MUTANT "PLAN not held while the flush is out" (TonightPanel.tsx: `body = planPending`
// made `body = false && planPending`). Observed, 5/6 passed:
//   x PLAN does not draw the plan of the flow as last compiled while the flush
//     is still saving the canvas, and draws the canvas's once it lands: the old
//     plan was drawn while the save was out: last round's plan under this
//     round's title
//     expected "no plan drawn, compiling note"
//     got      "plan drawn: {\n  \"blocks\": \"target\"\n}"
await test("PLAN does not draw the plan of the flow as last compiled while the flush is still saving the canvas, and draws the canvas's once it lands", async () => {
  seed({ tab: "plan", dirty: true, mode: "hold" });
  await until(() => heldPuts.length === 1, "the flush's PUT");
  await settle();
  const pre = q("[data-flows-tonight='plan'] pre");
  eq(pre === null && bodyText().includes("Compiling this graph")
      ? "no plan drawn, compiling note" : `plan drawn: ${pre?.textContent ?? "-"}`.slice(0, 40),
    "no plan drawn, compiling note",
    "the old plan was drawn while the save was out: last round's plan under this round's title");
  await act(async () => { heldPuts[0](true); });
  await until(() => q("[data-flows-tonight='plan'] pre") !== null, "the canvas's plan");
  assert(String(q("[data-flows-tonight='plan'] pre").textContent).includes("cycle, cycle"),
    `PLAN is not the compile of the canvas: ${q("[data-flows-tonight='plan'] pre").textContent}`);
});

// CONTROL: the hold is for a compile that is not of the canvas. A clean flow's
// PLAN is its current compile, drawn while Tonight is still loading and with no
// PUT; a hold that fired on every load would pass the case above.
// MUTANT "PLAN held on every load" (TonightPanel.tsx: the `&& !compiledIsCurrent(s.flows)`
// term of `planPending` removed). Observed, 5/6 passed:
//   x CONTROL: a clean flow's PLAN is drawn while Tonight is still loading, with
//     no PUT: a current plan was held back while Tonight loaded
await test("CONTROL: a clean flow's PLAN is drawn while Tonight is still loading, with no PUT", async () => {
  seed({ tab: "plan", dirty: false, holdTonight: true });
  // The seed's compile was made of the stored graph, which is on screen.
  act(() => {
    const f = useStore.getState().flows;
    useStore.setState({ flows: { ...f, compiled: { ...f.compiled!, from: f.graph } } } as any);
  });
  await until(() => heldTonight.length === 1, "the held read of the route");
  eq(useStore.getState().flows.tonightLoading, true, "precondition: Tonight is still loading");
  assert(q("[data-flows-tonight='plan'] pre") !== null, "a current plan was held back while Tonight loaded");
  eq(putIndex(), -1, "a clean flow was saved by opening Tonight");
});

// ==================================================== 2. WHAT IT STILL CANNOT SAY

// MUTANT "example note absent" (flowsTypes.ts `tonightStoredNote`: the `readonly
// ? TONIGHT_EXAMPLE_EDITS_NOTE : TONIGHT_UNSAVED_NOTE` arm made
// `TONIGHT_UNSAVED_NOTE`). Observed, 5/6 passed:
//   x an edited example says its edits are not saved, over the tabs that read
//     the stored example: the note on an edited example is not the example's own
//     sentence
//     expected "Edits to an example are not saved, so this describes the stored example, not the graph drawn here."
//     got      "The latest edits did not save, so this describes the last saved version of the flow, not the graph drawn here."
await test("an edited example says its edits are not saved, over the tabs that read the stored example", async () => {
  seed({ tab: "story", dirty: true, readonly: true });
  await until(() => noteText() !== null, "the example note");
  eq(noteText(), TONIGHT_EXAMPLE_EDITS_NOTE, "the note on an edited example is not the example's own sentence");
  eq(putIndex(), -1, "an example was PUT: the server refuses it");
});

// MUTANT "note without dirty" (flowsTypes.ts `tonightStoredNote`: the `if
// (!dirty) return null;` line removed). Observed, 4/6 passed (the owner's-report
// case fails too, on the note a saved edit should not carry):
//   x CONTROL: an unedited example carries no note, and PLAN never does: an
//     unedited example was said to have unsaved edits
//     expected "none"
//     got      "Edits to an example are not saved, so this describes the stored example, not the graph drawn here."
await test("CONTROL: an unedited example carries no note, and PLAN never does", async () => {
  seed({ tab: "story", dirty: false, readonly: true });
  await until(() => q("[data-tonight-brief]") !== null, "STORY's brief");
  eq(noteText() ?? "none", "none", "an unedited example was said to have unsaved edits");
  seed({ tab: "plan", dirty: true, readonly: true });
  await settle();
  eq(noteText() ?? "none", "none", "PLAN, which renders a compile of the canvas, carried the stored-flow note");
});

// MUTANT "note while the PUT is out" (TonightPanel.tsx: `loading` removed from
// the `storedNote` guard). Observed, 5/6 passed:
//   x an edit that did not save is said over STORY once the flush is done, and
//     not while the PUT is still out: the panel said the edit had not saved
//     before the save had answered
//     expected "no note while the PUT is out"
//     got      "note while the PUT is out"
await test("an edit that did not save is said over STORY once the flush is done, and not while the PUT is still out", async () => {
  seed({ tab: "story", dirty: true, mode: "hold" });
  await until(() => heldPuts.length === 1, "the flush's PUT");
  await settle();
  eq(noteText() === null ? "no note while the PUT is out" : "note while the PUT is out",
    "no note while the PUT is out", "the panel said the edit had not saved before the save had answered");
  await act(async () => { heldPuts[0](false); });
  await until(() => noteText() !== null, "the unsaved-edit note");
  eq(noteText(), TONIGHT_UNSAVED_NOTE, "the failed save was not said over STORY");
  assert(String(q("[data-tonight-brief]")?.textContent ?? "").includes("Blocks: target."),
    "after a failed save STORY should describe the last saved flow, the one the note says it describes");
});

// ------------------------------------------------------------------- report
act(() => root.unmount());
const total = passed + failed;
console.log(`w14TonightPanelReadsCanvas.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
