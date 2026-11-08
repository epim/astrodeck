// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// loopChipCurrent.test.ts - the loop wire's chip counts panels only while the
// compile answer in hand is the answer for the graph on screen, on BOTH
// canvases, MOUNTED over the real slice (#356; S7; spec 2026-09-23 flows
// mosaic, 1.4 "its count withheld while stale", Revision 9 row 2). And, at
// the bottom, that every reader of a stage's status goes through the one
// selector (`useStageStatus`, #464, #1125) and that no comment says nothing
// writes `flows.statuses`, as they did until the engine published the stages.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/loopChipCurrent.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. Both wire layers withheld the chip's count by `dirty`, which
// says whether the graph is SAVED, not whether the answer was COMPILED from
// it. S5 made a save compile (#356) and stamped each answer with the graph its
// request sent, which `compiledIsCurrent` (flowsSlice.ts) compares with the
// graph on screen, but the chip kept asking `dirty`, and that is wrong both
// ways:
//
//   - a skip edit, then SAVE, with the grid unchanged: the PUT's answer clears
//     `dirty` a round trip before the save's own compile lands, and the only
//     answer in hand is the one compiled before the skip, so the chip drew
//     its six panels over a block that now shoots five;
//   - the modal's DONE compiles the unsaved draft: that answer is exactly the
//     graph on screen, and the chip withheld its count because the flow was
//     dirty.
//
// WHAT IS GRADED, through the REAL store and its REAL flows slice (every
// request below is stubbed at `flowsApi`, the slice's only door to the
// server), with the classic layer (FlowWireLayer.tsx) and the #/next layer
// (canvas/FlowWires.tsx) mounted side by side, so each case reads what an
// operator reads on either canvas:
//
//   1. skip edit then SAVE: no count from the PUT's answer until the save's
//      compile lands, then the new count;
//   2. DONE's compile of the unsaved draft: its count, although the flow is
//      dirty;
//   3. CONTROL: an inspector edit after that compile withholds the count
//      again, although nothing was saved or compiled.
//
// The fake compile answers FOR THE GRAPH IT WAS SENT, as the server does, so
// a stale answer and a current one differ in the number the chip prints.
//
// Every mutant below was run in the private scratch copy
// S7-UCANVAS-mut of ui/ under the session scratchpad, never in the shared
// tree (#254), and the failure it produced is quoted verbatim.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// The #/next layer's tree imports `.css` files.
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
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- imports
const { createElement, act, Fragment } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const { FLOWS_INIT, compiledIsCurrent } = await import("../flowsSlice");
const { flowsApi } = await import("../../../lib/flowsApi");
const { NODE_DEFS } = await import("../nodeDefs");
const { LOOP_CHIP_WORDS } = await import("../targetSummary");
const ClassicLayer = (await import("../FlowWireLayer")).default;
const { FlowWireLayer: NextLayer } = await import("../../../next/hubs/session/flows/canvas/FlowWires");
type FlowGraphRec = import("../flowsTypes").FlowGraphRec;

// ------------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void> | void): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) {
    throw new Error(`${msg}\n    expected ${JSON.stringify(want)}\n    got      ${JSON.stringify(got)}`);
  }
}
/** Lets every settled stub resume the slice's awaits, inside act so the
 *  layers re-render on what the slice wrote. */
async function flush(): Promise<void> {
  await act(async () => { await new Promise<void>((r) => setTimeout(r, 0)); });
}

// ------------------------------------------------------------ the fake server

/** "2-3, 1-1" as the panels it names. The stand-in for compile.py
 *  `parse_skip`: only that the answer describes the graph sent is graded. */
function skipOf(v: unknown): number[][] {
  return String(v ?? "").split(",").map((s) => s.trim()).filter(Boolean)
    .map((s) => s.split("-").map(Number));
}

/** The compile answer for `graph`: each TARGET's grid and parsed skip, in
 *  compile.py `_target_entry`'s `mosaic` shape. */
function answerFor(graph: FlowGraphRec) {
  const targets = graph.nodes.filter((n) => n.type === "target").map((n) => ({
    node_id: n.id,
    mosaic: { rows: Number(n.params.rows), cols: Number(n.params.cols), skip: skipOf(n.params.skip) },
  }));
  return { plan: { targets }, structural: [], issues: [], unmapped: [] };
}

/** Every compile the slice started. `holdCompiles` keeps each open until a
 *  case answers it; otherwise it answers at once. */
let compiles: { graph: FlowGraphRec; answer: () => void }[] = [];
let holdCompiles = false;
(flowsApi as any).compileDraft = (graph: FlowGraphRec) => new Promise((resolve) => {
  const c = { graph, answer: () => resolve(answerFor(graph)) };
  compiles.push(c);
  if (!holdCompiles) c.answer();
});
/** The server stores what it was sent and answers with it. */
(flowsApi as any).save = async (id: string, flow: any) =>
  JSON.parse(JSON.stringify({ ...flow, id, migrated: [] }));
(flowsApi as any).progress = async () => { throw new Error("Not Found"); };
(flowsApi as any).list = async () => [];
(flowsApi as any).folders = async () => [];

/** M31 at three columns by two rows, nothing skipped, and the dashed loop
 *  wire from the lane's tail (the FILTER CYCLE) back to "next panel". */
const MOSAIC = (): FlowGraphRec => ({
  nodes: [
    { id: "t", type: "target", x: 0, y: 0,
      params: { ...NODE_DEFS.target.params, name: "M31", rows: 2, cols: 3, skip: "" } },
    { id: "cy", type: "cycle", x: 250, y: 0, params: { ...NODE_DEFS.cycle.params } },
  ],
  edges: [
    { id: "lane", from: "t", fromPort: "target", to: "cy", toPort: "run" },
    { id: "loop", from: "cy", fromPort: "pass", to: "t", toPort: "next" },
  ],
});
(flowsApi as any).get = async (id: string) => ({
  id, name: `Flow ${id}`, folder: "My flows", tagline: "", graph: MOSAIC(),
  created_ts: 1, updated_ts: 1, last_run: null, last_result: "", readonly: false,
});

// ------------------------------------------------------------------- mounting
const container = win.document.getElementById("root");
const root = createRoot(container);

/** Both layers mounted over a fresh store with the flow OPENED through the
 *  real `flowsOpen`, whose compile has answered. */
async function opened(): Promise<void> {
  act(() => root.render(null));
  compiles = [];
  holdCompiles = false;
  act(() => { useStore.setState({ flows: { ...FLOWS_INIT, ui: { ...FLOWS_INIT.ui } } } as any); });
  act(() => root.render(createElement(Fragment, null,
    createElement(ClassicLayer, {}),
    createElement(NextLayer, { tier: "tablet" }))));
  await act(async () => { await useStore.getState().flowsOpen("f1"); });
  await flush();
}

/** The chip each canvas draws on the loop wire, "classic | next". */
function chips(): string {
  const classic = container.querySelector("[data-loop-chip] text")?.textContent ?? "no chip";
  const next = container.querySelector('[data-testid="flow-loop-chip"] text')?.textContent ?? "no chip";
  return `${classic} | ${next}`;
}
/** What both canvases should draw, the same words on each. */
const both = (s: string) => `${s} | ${s}`;
const panels = (n: number) => `${LOOP_CHIP_WORDS} · ${n} panels`;
const flows = () => useStore.getState().flows;

// ================================================== 1. a skip edit, then SAVE

// MUTANT "classic chip reads dirty" (FlowWireLayer.tsx: `current` read as
// `!s.flows.dirty`, which is `loopChip(graph, e, plan, dirty)`, as the layer
// was before S7). Observed, loopChipCurrent.test 1/4 (this case, case 2 and
// case 3's precondition, which is case 2's count):
//   x a skip edit then SAVE, the grid unchanged, shows no stale count until the save's compile lands: the chips once the PUT has answered and the save's compile has not: the opened graph's six panels drawn over a block that now shoots five
//     expected "every pass: next panel | every pass: next panel"
//     got      "every pass: next panel · 6 panels | every pass: next panel"
// MUTANT "next chip reads dirty" (FlowWires.tsx, the same). Observed,
// loopChipCurrent.test 1/4, the mirror:
//   x a skip edit then SAVE, ...: the chips once the PUT has answered and the save's compile has not: the opened graph's six panels drawn over a block that now shoots five
//     expected "every pass: next panel | every pass: next panel"
//     got      "every pass: next panel | every pass: next panel · 6 panels"
// loopArcDom.test.tsx stays 19/19 under the classic one: it seeds the store
// by hand, and there an edit and `dirty` arrive together.
//
// MUTANT "chip ignores the answer's graph" (both layers hand `loopChip`
// false, so every answer counts). Observed, loopChipCurrent.test 2/4 (this
// case and the control, case 3):
//   x a skip edit then SAVE, the grid unchanged, shows no stale count until the save's compile lands: the chips while the skip edit is unsaved: an answer for the graph before the skip counted over it
//     expected "every pass: next panel | every pass: next panel"
//     got      "every pass: next panel · 6 panels | every pass: next panel · 6 panels"
// "count while stale" (targetSummary.ts loopChip ignores `stale`) fails the
// same two cases with the same lines.
await test("a skip edit then SAVE, the grid unchanged, shows no stale count until the save's compile lands", async () => {
  await opened();
  eq(chips(), both(panels(6)), "precondition: the opened 3x2 counts six live panels on both canvases");
  act(() => { useStore.getState().flowsSetParam("t", "skip", "2-3"); });
  eq(chips(), both(LOOP_CHIP_WORDS), "the chips while the skip edit is unsaved: an answer for the graph before the skip counted over it");

  holdCompiles = true;
  compiles = [];
  await act(async () => { await useStore.getState().flowsSave(); });
  await flush();
  eq(`dirty ${flows().dirty}, compiles ${compiles.length}, current ${compiledIsCurrent(flows())}`,
    "dirty false, compiles 1, current false",
    "precondition: the PUT has answered, the save's compile is out, the answer in hand is the old graph's");
  const grid = flows().graph.nodes.find((n) => n.id === "t")!.params;
  eq(`${grid.cols}x${grid.rows}`, "3x2", "precondition: the grid is unchanged, so the entry's grid check cannot withhold the count");
  eq(chips(), both(LOOP_CHIP_WORDS),
    "the chips once the PUT has answered and the save's compile has not: the opened graph's six panels drawn over a block that now shoots five");

  await act(async () => { compiles[0].answer(); });
  await flush();
  eq(chips(), both(panels(5)), "the chips once the save's compile has landed");
});

// ============================================= 2. DONE's compile of a draft

// MUTANT "classic chip reads dirty". Observed, loopChipCurrent.test 1/4:
//   x DONE's compile of an unsaved draft shows its count although the flow is dirty: the chips once DONE's compile of the draft has landed
//     expected "every pass: next panel · 5 panels | every pass: next panel · 5 panels"
//     got      "every pass: next panel | every pass: next panel · 5 panels"
// MUTANT "next chip reads dirty". Observed, loopChipCurrent.test 1/4:
//   x DONE's compile of an unsaved draft shows its count although the flow is dirty: the chips once DONE's compile of the draft has landed
//     expected "every pass: next panel · 5 panels | every pass: next panel · 5 panels"
//     got      "every pass: next panel · 5 panels | every pass: next panel"
await test("DONE's compile of an unsaved draft shows its count although the flow is dirty", async () => {
  await opened();
  await act(async () => { await useStore.getState().flowsApplyFraming("t", { skip: "2-3" }); });
  await flush();
  eq(`dirty ${flows().dirty}, current ${compiledIsCurrent(flows())}`, "dirty true, current true",
    "precondition: DONE wrote the draft, unsaved, and compiled it");
  eq(chips(), both(panels(5)), "the chips once DONE's compile of the draft has landed");
});

// =============================================== 3. CONTROL: an edit after it

// Its own assertion holds under "chip reads dirty" (the flow is dirty
// either way; the case fails there only on its precondition, DONE's count).
// Red under "chip ignores the answer's graph". Observed
// (loopChipCurrent.test 2/4):
//   x CONTROL: an inspector edit after DONE's compile withholds the count again: the chips after an edit no compile has seen
//     expected "every pass: next panel | every pass: next panel"
//     got      "every pass: next panel · 5 panels | every pass: next panel · 5 panels"
await test("CONTROL: an inspector edit after DONE's compile withholds the count again", async () => {
  await opened();
  await act(async () => { await useStore.getState().flowsApplyFraming("t", { skip: "2-3" }); });
  await flush();
  eq(chips(), both(panels(5)), "precondition: DONE's count");
  compiles = [];
  act(() => { useStore.getState().flowsSetParam("t", "skip", "1-1, 2-3"); });
  eq(compiles.length, 0, "precondition: an inspector edit compiles nothing");
  eq(chips(), both(LOOP_CHIP_WORDS), "the chips after an edit no compile has seen");
});

// ============ 4. #464, #1125: one selector, and no comment denies the writer

// S7 orchestrator ruling 10 deferred #464 because the engine published no
// stage; the comments said so, in the readers, and WP-111 made the cards draw
// nothing through a live run. WP 4.2 (#1125) built the writer (the engine
// publishes `stages` on the sequence state; store.ts keeps them as
// `flows.statuses`) and collapsed the readers' visibility rules into one
// selector, `stageStatusOf` / `useStageStatus` in store.ts. w4_2StageStatus
// Replay.test.tsx grades the DOM from a recorded stream; this case holds the
// two facts a DOM test cannot see.
//
//   a. Every reader of a stage's status asks the selector: the two cards, the
//      phone stage list's row and both wire layers call `useStageStatus`, and
//      no source file but store.ts indexes `statuses[`, so a second rule for
//      when a stage may say a word cannot be added beside it unseen.
//   b. No comment in those files says nothing writes the field, that no
//      topic carries a stage's status, or that every stage reads idle
//      through a live run: each was true until the writer, and is false now.
//
// MUTANT "a card keeps its own rule" (FlowNodeCard.tsx `const stage =
// useStageStatus(node.id);` put back as `const stage = useStore((s) =>
// s.flows.statuses[node.id] ?? null);`). Observed, loopChipCurrent.test 3/4:
//   x #464, #1125: ...: components/flows/FlowNodeCard.tsx does not call useStageStatus; src/components/flows/FlowNodeCard.tsx indexes statuses[ itself
// MUTANT "a header restored" (FlowNode.tsx's header put back word for word as
// it stood before WP 4.2: "Nothing sends a per-stage status (there is no
// `flow.node` topic, and nothing writes `flows.statuses`, #464, ..."). Observed,
// 3/4:
//   x #464, #1125: ...: next/hubs/session/flows/canvas/FlowNode.tsx says nothing writes the field: "nothing writes"
await test("#464, #1125: every reader asks the one selector, and no comment says nothing writes the status", async () => {
  const fs = await import("node:fs");
  const { fileURLToPath } = await import("node:url");
  const src = fileURLToPath(new URL("../../../", import.meta.url)).replace(/[\\/]+$/, "");
  const readers = [
    "components/flows/FlowNodeCard.tsx",
    "components/flows/FlowWireLayer.tsx",
    "next/hubs/session/flows/canvas/FlowNode.tsx",
    "next/hubs/session/flows/canvas/FlowWires.tsx",
    "next/hubs/session/flows/canvas/FlowStagesPhoneSheet.tsx",
  ];
  const bad: string[] = [];
  for (const file of readers) {
    if (!/useStageStatus\(/.test(fs.readFileSync(`${src}/${file}`, "utf8"))) {
      bad.push(`${file} does not call useStageStatus`);
    }
  }
  // Every non-test source file under ui/src, to find the ones that index
  // `statuses[`.
  const walk = (dir: string, out: string[] = []): string[] => {
    for (const name of fs.readdirSync(dir)) {
      const p = `${dir}/${name}`;
      if (fs.statSync(p).isDirectory()) {
        if (name !== "__tests__" && name !== "node_modules") walk(p, out);
      } else if (/\.(ts|tsx)$/.test(name) && !/\.test\.tsx?$/.test(name)) {
        out.push(p);
      }
    }
    return out;
  };
  for (const p of walk(src)) {
    const rel = p.slice(src.length + 1);
    const code = fs.readFileSync(p, "utf8").split(/\r?\n/)
      .filter((ln) => !/^\s*(\/\/|\/\*|\*)/.test(ln)).join("\n");
    if (rel !== "store.ts" && /\bstatuses\[/.test(code)) {
      bad.push(`${rel} indexes statuses[ itself`);
    }
  }
  const denies = [
    /nothing writes/i, /no (server )?topic carries a stage/i,
    /nothing does \(#464/i, /filled from a WS frame/i,
    /every (stage|LED|node) reads idle/i, /so every wire reads idle/i,
  ];
  for (const file of [...readers, "components/flows/FlowCanvas.tsx",
    "next/hubs/session/flows/canvas/FlowCanvasSurface.tsx",
    "store.ts"]) {
    const t = fs.readFileSync(`${src}/${file}`, "utf8").replace(/\r/g, "")
      .replace(/^\s*(\/\/|\/\*\*|\*\/|\*)[ \t]?/gm, "").replace(/\s+/g, " ");
    for (const re of denies) {
      const m = re.exec(t);
      if (m) bad.push(`${file} says nothing writes the field: ${JSON.stringify(m[0])}`);
    }
  }
  if (bad.length) throw new Error(bad.join("\n    "));
});

// ----------------------------------------------------------------- report
act(() => root.render(null));
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nloopChipCurrent.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
