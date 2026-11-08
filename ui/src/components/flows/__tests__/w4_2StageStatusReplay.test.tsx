// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w4_2StageStatusReplay.test.tsx - the engine's stage statuses reach every
// pill, LED and wire on BOTH canvases, MOUNTED over the real store, from a
// RECORDED sequence stream (backlog consolidation WP 4.2, #1125; members
// #464 and #742).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/w4_2StageStatusReplay.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE STREAM is server/tests/fixtures/stage_status_stream.json: the `sequence`
// payloads of a clocked simulator night of a TARGET (n2) feeding two CAPTURE
// LOOPs (n3 then n4), beside an AUTOFOCUS (n5) and a REPORT (n12) the engine
// does not run as stages, reduced to state, session and stages and
// de-duplicated. The server test holds the recording to the engine
// (test_w4_2_stage_status.py `test_the_recorded_stream_is_what_the_engine_
// publishes`), so it cannot drift from what the rig publishes. Each frame goes
// through the store's own `handleEvent`, the path a WS frame takes.
//
// WHAT IS GRADED, per frame, on the classic card's LED, the #/next card's dot
// and pill, each wire (its march is the source stage's word) and, in the last
// case, the phone stage list's pills:
//   1. the engine said nothing yet: no stage draws a status through the live
//      run, and no wire marches;
//   2. n2 and n3 running: both read BUSY and the wires leaving them march,
//      while n4, n5 and n12 draw nothing;
//   3. n3 done, n2 still running: n3 reads DONE, n4 draws nothing yet;
//   4. n4 running, then everything the engine runs done; the AUTOFOCUS and
//      the REPORT still draw nothing, a live run included;
//   5. the run's terminal frame: every stage reads IDLE again, where it is
//      true, and the store holds no statuses;
//   6. THE SAME STREAM over a flow that is not the run's (another flow may
//      reuse a node id): nothing is painted, every stage reads IDLE;
//   7. the phone stage list's rows, the third reader of the one selector, show
//      the engine's words through the same stream and none for a stage it has
//      not named.
//
// Every mutant below was run from a byte backup of store.ts, restored and
// sha256-compared; the first failing lines are quoted.
//
// MUTANT "the store keeps no statuses" (the `useStore.setState` of the
// subscription that writes `flows.statuses` from the sequence state made
// `void next;`, so the stream reaches `sequence` and goes no further).
// Observed, 3/8 passing:
//   x n2 and n3 running: ...: the classic dot of n2 (expected "busy", got "none")
//   x n3 done while n2 still runs: ...: premise: n3 was busy (expected "busy", got "none")
//   x n4 running, then everything the engine runs done; ...: the classic dot of n4 while its frames are shot (expected "busy", got "none")
//   and the phone case, the same.
// MUTANT "the selector ignores the written status" (`stageStatusOf` without its
// `written !== undefined` line): the same cases, red.
// MUTANT "a stage is painted by another flow's run" (`stageStatusesOf` without
// its `flowRunLive` guard). Observed, 6/8 passing:
//   x the run's terminal frame: ...: the classic cards once the run is over (expected "n2:idle n3:idle n4:idle n5:idle n12:idle", got "n2:ok n3:ok n4:ok n5:idle n12:idle")
//   x the same stream over a flow that is not the run's paints nothing: ...: (expected "n2:idle ...", got "n2:busy n3:ok n4:busy ...")
// MUTANT "the subscription trusts the flows state" (`sameStatuses(s.flows.
// statuses ?? NO_STATUSES, next)` made `sameStatuses(s.flows.statuses, next)`).
// Observed, 7/8 passing:
//   x a write of a flows state that holds no statuses throws nothing and invents none: Cannot convert undefined or null to object

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
{
  const { registerHooks } = await import("node:module");
  registerHooks({
    load(url: string, context: any, nextLoad: any) {
      if (url.endsWith(".css")) {
        return { format: "module", shortCircuit: true, source: "export default {};" };
      }
      return nextLoad(url, context);
    },
  } as any);
}

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;
// A phone, 390 px wide: the stage list asks the breakpoint (case 7).
win.matchMedia = (q: string) => {
  const m = /min-width:\s*(\d+)px/.exec(q);
  return {
    matches: m ? 390 >= Number(m[1]) : false,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {},
  };
};
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
// Every request the open flow's sheet makes is answered, with nothing in it.
(globalThis as any).fetch = async () => ({
  ok: true, status: 200, statusText: "OK",
  headers: { get: () => "application/json" },
  json: async () => ({ ok: true }), text: async () => "{\"ok\":true}",
});
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "FocusEvent", "localStorage", "sessionStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "ResizeObserver", "requestAnimationFrame",
  "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- imports
const { readFileSync } = await import("node:fs");
const { createElement, act, Fragment } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const { FLOWS_INIT } = await import("../flowsSlice");
const { flowsApi } = await import("../../../lib/flowsApi");
const { NODE_DEFS } = await import("../nodeDefs");
const ClassicCard = (await import("../FlowNodeCard")).default;
const ClassicWires = (await import("../FlowWireLayer")).default;
const { FlowStagesPhoneSheet } = await import("../../../next/hubs/session/flows/canvas/FlowStagesPhoneSheet");
const { FlowNodeCard: NextCard } = await import("../../../next/hubs/session/flows/canvas/FlowNode");
const { FlowWireLayer: NextWires } = await import("../../../next/hubs/session/flows/canvas/FlowWires");
type FlowNodeRec = import("../flowsTypes").FlowNodeRec;
type FlowEdgeRec = import("../flowsTypes").FlowEdgeRec;
type FlowRecordRec = import("../flowsTypes").FlowRecordRec;

// ------------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void> | void): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
const settle = async (): Promise<void> => {
  for (let i = 0; i < 4; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) {
    throw new Error(`${msg} (expected ${JSON.stringify(want)}, got ${JSON.stringify(got)})`);
  }
}

// A live run's progress re-read (the slice starts one when the run's frames
// advance or the run ends) answers 404, which the slice leaves silent.
(flowsApi as any).progress = async () => { throw new Error("Not Found"); };

// ------------------------------------------------------------------ fixtures
// A missing or unreadable file FAILS the whole file, never skips it.
const STREAM_FILE = "../../../../../server/tests/fixtures/stage_status_stream.json";
function readStream(): { frames: any[] } {
  try {
    return JSON.parse(readFileSync(new URL(STREAM_FILE, import.meta.url), "utf8") as string);
  } catch (e) {
    throw new Error(`cannot read ${STREAM_FILE}, the recorded stream this file replays: ${(e as Error).message}`);
  }
}
const FRAMES: any[] = readStream().frames;
const SESSION: string = FRAMES[0].session.id;

const node = (id: string, type: string, x: number): FlowNodeRec =>
  ({ id, type, x, y: 20, params: { ...(NODE_DEFS as any)[type].params } }) as FlowNodeRec;
const NODES = [node("n2", "target", 10), node("n3", "capture", 300), node("n4", "capture", 590),
               node("n5", "autofocus", 880), node("n12", "report", 1170)];
const EDGES: FlowEdgeRec[] = [
  { id: "e1", from: "n2", fromPort: "target", to: "n3", toPort: "run" },
  { id: "e2", from: "n3", fromPort: "complete", to: "n4", toPort: "run" },
  { id: "e3", from: "n4", fromPort: "complete", to: "n12", toPort: "session" },
];
const GRAPH = { nodes: NODES, edges: EDGES };
const REC: FlowRecordRec = {
  id: "f1", name: "Flow f1", folder: "My flows", tagline: "", graph: GRAPH,
  created_ts: 1, updated_ts: 1, last_run: null, last_result: "", readonly: false,
};
const IDS = NODES.map((n) => n.id);

const container = win.document.getElementById("root");
const root = createRoot(container);

function mount(): void {
  act(() => root.render(createElement(Fragment, null,
    createElement("div", { "data-canvas": "classic" },
      createElement(ClassicWires, { tier: "desktop" }),
      NODES.map((n) => createElement(ClassicCard, { key: n.id, node: n }))),
    createElement("div", { "data-canvas": "next" },
      createElement(NextWires, { tier: "tablet" }),
      NODES.map((n) => createElement(NextCard, { key: n.id, node: n }))))));
}

type Canvas = "classic" | "next";
const card = (c: Canvas, id: string): any =>
  container.querySelector(`[data-canvas="${c}"] [data-node-id="${id}"]`);
const dot = (c: Canvas, id: string): any =>
  card(c, id)?.querySelector(c === "classic" ? ".led" : ".nx-flow-node-led") ?? null;
/** What a card's status dot says: its accessible name, or "none" when it is
 *  not drawn. The classic LED's name is the word ("busy"); the #/next dot's
 *  is "stage BUSY". Both are lowered to the word. */
function word(c: Canvas, id: string): string {
  const d = dot(c, id);
  if (!d) return "none";
  return String(d.getAttribute("aria-label") ?? "").replace(/^stage /i, "").toLowerCase();
}
/** Whether the #/next card's own pill prints a word (it prints none for idle). */
const pill = (id: string): string =>
  String(card("next", id)?.querySelector(".nx-status-pill")?.textContent ?? "none").trim();
/** Whether a wire marches (the source stage's word busy or done, while the
 *  client's own run latch is up), per canvas. */
function marching(c: Canvas, edgeId: string): boolean {
  const hit = container.querySelector(`[data-canvas="${c}"] path[data-wire][data-edge-id="${edgeId}"]`);
  const vis = hit?.parentElement?.querySelectorAll("path")[1];
  return /march/.test(String(vis?.getAttribute("class") ?? ""));
}

// ---------------------------------------------------------------- the replay
/** The store as the open flow `f1`, the flow's client latch up (the run was
 *  started from here), with the given known sessions. */
function open(sessionIds: string[]): void {
  // The sequence goes idle FIRST: a live run ending lets go of the client's
  // run latch, and the latch is what the wires read, so it is set after.
  act(() => { useStore.setState({ sequence: { state: "idle" } } as any); });
  act(() => {
    useStore.setState({
      flows: {
        ...FLOWS_INIT, ui: { ...FLOWS_INIT.ui }, record: REC, graph: GRAPH,
        sessionIds, statuses: {},
        run: { ...FLOWS_INIT.run, phase: "running", startedAt: 1 },
      },
    } as any);
  });
}
/** One recorded frame, through the path a WS frame takes. */
function frame(i: number): void {
  act(() => {
    useStore.getState().handleEvent({ type: "sequence", data: FRAMES[i], ts: i });
  });
}
const WORDS = (c: Canvas, ids: string[] = IDS): string =>
  ids.map((id) => `${id}:${word(c, id)}`).join(" ");
const NONE_YET = "n2:none n3:none n4:none n5:none n12:none";

// =================================================== 1. nothing said yet

await test("the engine has said nothing yet: no stage draws a status and no wire marches", () => {
  open([SESSION]);
  mount();
  frame(0);
  for (const c of ["classic", "next"] as const) {
    eq(WORDS(c), NONE_YET, `the ${c} cards through a live run, before the first stage word`);
    eq(marching(c, "e1") || marching(c, "e2") || marching(c, "e3"), false, `a wire marches on ${c}`);
  }
});

// ============================================== 2. n2 and n3 running

await test("n2 and n3 running: n2 reads BUSY on both canvases, n4 reads nothing, the wires leaving them march", () => {
  open([SESSION]);
  mount();
  frame(0);
  frame(1);
  for (const c of ["classic", "next"] as const) {
    eq(word(c, "n2"), "busy", `the ${c} dot of n2`);
    eq(word(c, "n3"), "busy", `the ${c} dot of n3`);
    eq(word(c, "n4"), "none", `the ${c} dot of n4, which the engine has not entered`);
    eq(marching(c, "e1"), true, `the wire leaving n2 on ${c}`);
    eq(marching(c, "e2"), true, `the wire leaving n3 on ${c}`);
    eq(marching(c, "e3"), false, `the wire leaving n4 on ${c}, which says nothing`);
  }
  eq(pill("n2"), "BUSY", "the #/next pill of n2");
  eq(pill("n3"), "BUSY", "the #/next pill of n3");
  eq(pill("n4"), "none", "the #/next pill of n4");
  eq(card("next", "n2").getAttribute("data-status"), "busy", "the #/next card's own status of n2");
});

// ================================================== 3. n3 done, n2 running

await test("n3 done while n2 still runs: the pill of n3 changes from BUSY to DONE, n4 draws nothing yet", () => {
  open([SESSION]);
  mount();
  for (let i = 0; i <= 1; i++) frame(i);
  eq(word("classic", "n3"), "busy", "premise: n3 was busy");
  frame(2);
  for (const c of ["classic", "next"] as const) {
    eq(word(c, "n3"), c === "classic" ? "ok" : "done",
      `the ${c} dot of n3 once its frames are in`);
    eq(word(c, "n2"), "busy", `the ${c} dot of n2, which runs through both stages`);
    eq(word(c, "n4"), "none", `the ${c} dot of n4`);
  }
  eq(pill("n3"), "DONE", "the #/next pill of n3");
});

// ============================================== 4. n4 running, all done

await test("n4 running, then everything the engine runs done; the AUTOFOCUS and the REPORT draw nothing", () => {
  open([SESSION]);
  mount();
  for (let i = 0; i <= 3; i++) frame(i);
  for (const c of ["classic", "next"] as const) {
    eq(word(c, "n4"), "busy", `the ${c} dot of n4 while its frames are shot`);
  }
  eq(pill("n4"), "BUSY", "the #/next pill of n4");
  frame(4);
  for (const c of ["classic", "next"] as const) {
    eq(WORDS(c, ["n2", "n3", "n4"]).replace(/ok|done/g, "D"), "n2:D n3:D n4:D",
      `the ${c} stages the engine runs, all done`);
    eq(WORDS(c, ["n5", "n12"]), "n5:none n12:none",
      `the ${c} AUTOFOCUS and REPORT, which the engine does not run as stages`);
  }
  eq(JSON.stringify(useStore.getState().flows.statuses),
    JSON.stringify({ n3: "ok", n2: "ok", n4: "ok" }), "the store's statuses, in the canvases' words");
});

// ======================================================= 5. the run ends

await test("the run's terminal frame: every stage reads IDLE again and the store holds no statuses", () => {
  open([SESSION]);
  mount();
  for (let i = 0; i < FRAMES.length; i++) frame(i);
  eq(FRAMES[FRAMES.length - 1].state, "complete", "premise: the stream ends in the terminal frame");
  for (const c of ["classic", "next"] as const) {
    eq(WORDS(c), "n2:idle n3:idle n4:idle n5:idle n12:idle", `the ${c} cards once the run is over`);
  }
  eq(Object.keys(useStore.getState().flows.statuses).length, 0, "the statuses the run left in the store");
});

// ============================================ 6. a run that is not this flow's

await test("the same stream over a flow that is not the run's paints nothing: every stage reads IDLE", () => {
  open(["another-flows-session"]);
  mount();
  for (let i = 0; i <= 3; i++) frame(i);
  for (const c of ["classic", "next"] as const) {
    eq(WORDS(c), "n2:idle n3:idle n4:idle n5:idle n12:idle",
      `the ${c} cards of a flow whose run this is not, though it may reuse the node ids`);
    eq(marching(c, "e1") || marching(c, "e2"), false, `a wire marches on ${c} for another flow's run`);
  }
  eq(Object.keys(useStore.getState().flows.statuses).length, 0, "the other flow's stages in this store");
});


// ============================================== 7. the phone stage list

await test("the phone stage list shows the engine's words through the same stream, none for an unnamed stage", async () => {
  open([SESSION]);
  await act(async () => {
    root.render(createElement(FlowStagesPhoneSheet as any, { params: { open: REC.id }, depth: 0 }));
  });
  await settle();
  // The rows in the list's own order: the TARGET, then the lane, the AUTOFOCUS
  // before the two CAPTURE LOOPs (n3, n4) and the REPORT after.
  const rows = (): string => [...container.querySelectorAll('[data-testid="flow-stage-row"]')]
    .map((r: any) => {
      const label = /^(TARGET|AUTOFOCUS|CAPTURE LOOP|SESSION REPORT)/.exec(String(r.textContent))?.[1] ?? "?";
      return `${label}=${r.querySelector(".nx-status-pill")?.textContent ?? "none"}`;
    }).join(", ");
  const replay = (from: number, to: number): void => {
    for (let i = from; i <= to; i++) frame(i);
  };
  eq(rows(), "TARGET=IDLE, AUTOFOCUS=IDLE, CAPTURE LOOP=IDLE, CAPTURE LOOP=IDLE, SESSION REPORT=IDLE",
    "the stage list before the run: idle is true");
  replay(0, 0);
  eq(rows(), "TARGET=none, AUTOFOCUS=none, CAPTURE LOOP=none, CAPTURE LOOP=none, SESSION REPORT=none",
    "the stage list through the live run before the engine has named a stage");
  replay(1, 1);
  eq(rows(), "TARGET=BUSY, AUTOFOCUS=none, CAPTURE LOOP=BUSY, CAPTURE LOOP=none, SESSION REPORT=none",
    "the stage list with n2 and n3 running");
  replay(2, 3);
  eq(rows(), "TARGET=BUSY, AUTOFOCUS=none, CAPTURE LOOP=DONE, CAPTURE LOOP=BUSY, SESSION REPORT=none",
    "the stage list with n3 done and n4 running");
  replay(4, 4);
  eq(rows(), "TARGET=DONE, AUTOFOCUS=none, CAPTURE LOOP=DONE, CAPTURE LOOP=DONE, SESSION REPORT=none",
    "the stage list with everything the engine runs done");
});

// ===================================== 8. a flows state with no statuses yet

await test("a write of a flows state that holds no statuses throws nothing and invents none", () => {
  // The settings hub's own test seeds `flows` as a partial object, with no
  // `sessionIds` either. The store's subscription runs on every write of the
  // whole store and sees the known sessions change, so it meets one.
  act(() => root.render(null));
  open([SESSION]);
  act(() => { useStore.setState({ flows: { libraryLoaded: true, cards: [], folders: [] } } as any); });
  eq(useStore.getState().flows.statuses, undefined, "a statuses field the subscription invented");
});

// ----------------------------------------------------------------- report
act(() => root.render(null));
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nw4_2StageStatusReplay.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
