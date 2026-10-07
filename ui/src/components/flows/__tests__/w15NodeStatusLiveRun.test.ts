// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w15NodeStatusLiveRun.test.ts - a stage card stops claiming IDLE through a
// live run of its own flow, when nothing has written the stage's status, on
// BOTH canvases, MOUNTED over the real store (backlog plan WP-111, #464 part
// A; S5's phone rule, FlowStagesPhoneSheet.tsx `showStatus = written ||
// !runLive`, applied to the two other readers).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/w15NodeStatusLiveRun.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT (#464). Nothing writes `flows.statuses`: the engine publishes no
// in-flight stage, so every stage's status reads the default, idle, and through
// a live run of THIS flow that is false. The phone stage list already treated
// the field as absent while its flow runs (S5, found by the real-page probe:
// TARGET, FILTER CYCLE and SESSION REPORT all IDLE while the rig shot panel
// 1-1). The classic card's LED (`<Led state="off" label="idle">`) and the
// #/next card's dot (`title="IDLE"`, `aria-label="stage IDLE"`, a hollow ring)
// still said it. Parts B and C (the engine publishes the stage and the slice
// writes `busy`) are later; #464 stays open, and this file grades only the
// false-claim half.
//
// WHAT IS GRADED. Each case mounts a classic card and a #/next card for each of
// two stages, and reads everything a card says about its status: its text, its
// accessible names and tooltips, its `data-status`, and whether the dot is
// there at all.
//
//   1. a live run of the open flow, nothing written: neither canvas draws a dot
//      or says IDLE (the classic card shows no LED, not an 'off' one);
//   2. no run: both canvases say IDLE as they always did, where it is true;
//   3. a live run of ANOTHER flow, and a live run while no flow is open: the
//      same, IDLE is true of this flow;
//   4. a written status outranks the rule, per stage: n1 reads BUSY through
//      the live run while n2, unwritten, reads nothing;
//   5. the run ending brings IDLE back;
//   6. the re-render discipline: a sequence tick inside a live run re-renders
//      neither card (the two new subscriptions are booleans), counted through
//      `def.sum` as flowNodeDom.test.tsx does.
//
// Every mutant below was run from a byte backup of the file it edits (kept
// in the worktree, deleted after), restored and sha256-compared, with the
// mutant's text grepped out of the file. The failures each produced are
// quoted verbatim (the first line of each failing case, then its expected and
// got lines where they are short).
//
// MUTANT "classic card drops the guard" (FlowNodeCard.tsx `const showStatus =
// written || !runLive;` made `= true`: the card as it was). Observed, 4/6:
//   x a live run of the open flow with nothing written: neither canvas draws a dot or says IDLE: the classic card for n1 draws a status dot through a live run of its own flow
//     expected false
//     got      true
//   x a written status outranks the rule per stage: n1 reads BUSY through the live run, n2 reads nothing: the classic LED of the unwritten stage, beside a written one
// MUTANT "next card drops the guard" (FlowNode.tsx, the same). Observed, 3/6:
//   x a live run of the open flow with nothing written: neither canvas draws a dot or says IDLE: the next card for n1 draws a status dot through a live run of its own flow
//   x a written status outranks the rule per stage: n1 reads BUSY through the live run, n2 reads nothing: the #/next dot of the unwritten stage, beside a written one
//   x the run ending brings IDLE back: precondition: no dot through the live run
// MUTANT "card shows only a written status" (`= written;` in place of
// `written || !runLive`, so IDLE vanishes where it is true). Observed, 3/6
// in each file, cases 2, 3 and 5:
//   classic: x no run: both canvases say IDLE where it is true: the classic card's LED name with no run
//     expected "idle"
//     got      undefined
//   next: x no run: both canvases say IDLE where it is true: the #/next dot's accessible name with no run
//     expected "stage IDLE"
//     got      undefined
// MUTANT "runLive ignores the session" (`useStore((s) => s.sequence?.state ===
// "running")` in place of `flowRunLive(knownSessions(...), ...)`: another
// flow's run claims this one's). Observed, 5/6 in each file:
//   classic: x a live run of ANOTHER flow, or with no flow open, leaves IDLE standing: the classic LED through another flow's run: nothing of THIS flow is running, so idle is true
//     expected "idle"
//     got      undefined
//   next: x a live run of ANOTHER flow, or with no flow open, leaves IDLE standing: the #/next dot through another flow's run
//     expected "stage IDLE"
//     got      undefined
// MUTANT "ignores a written status" (`const written = false;`). Observed,
// 5/6 in each file:
//   classic: x a written status outranks the rule per stage: n1 reads BUSY through the live run, n2 reads nothing: the classic LED of the written stage
//     expected "busy"
//     got      undefined
//   next: x a written status outranks the rule per stage: n1 reads BUSY through the live run, n2 reads nothing: the #/next dot's name for the written stage
//     expected "stage BUSY"
//     got      undefined
// MUTANT "selects the sequence object, not a boolean" (`useStore((s) =>
// (flowRunLive(...) ? s.sequence : null)) !== null`, one line in either
// file). Observed, 5/6 in each file:
//   x a sequence tick inside a live run re-renders neither card: renders caused by two sequence ticks of the same live run: a subscription returned the sequence object, not a boolean
//     expected 0
//     got      4
// MUTANT "next card keeps data-status idle on the root" (FlowNode.tsx
// `data-status={showStatus ? status : undefined}` made `data-status={status}`).
// Observed, 5/6:
//   x a live run of the open flow with nothing written: neither canvas draws a dot or says IDLE: the next card for n1 says IDLE through a live run: CAPTURE LOOPrunall doneframe gradedpass doneL · 120s · g100 · ×24 | idle | Edit CAPTURE LOOP parameters | Edit parameters

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// The #/next card's tree imports `.css` files.
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
const { FLOWS_INIT } = await import("../flowsSlice");
const { flowsApi } = await import("../../../lib/flowsApi");
const { NODE_DEFS } = await import("../nodeDefs");
const ClassicCard = (await import("../FlowNodeCard")).default;
const { FlowNodeCard: NextCard } = await import("../../../next/hubs/session/flows/canvas/FlowNode");
type FlowNodeRec = import("../flowsTypes").FlowNodeRec;
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
    throw new Error(`${msg}\n    expected ${JSON.stringify(want)}\n    got      ${JSON.stringify(got)}`);
  }
}

// A live run's progress re-read (the slice starts one when the run's frames
// advance or the run ends) answers 404, which the slice leaves silent.
(flowsApi as any).progress = async () => { throw new Error("Not Found"); };

// ------------------------------------------------------------------- fixture
// Two stages of a type whose footer calls `def.sum` once per render, which is
// what the render counter below reads. The ids are the same on both canvases.
const N1: FlowNodeRec = { id: "n1", type: "capture", x: 10, y: 20, params: { ...NODE_DEFS.capture.params } };
const N2: FlowNodeRec = { id: "n2", type: "capture", x: 300, y: 20, params: { ...NODE_DEFS.capture.params } };
const REC: FlowRecordRec = {
  id: "f1", name: "Flow f1", folder: "My flows", tagline: "",
  graph: { nodes: [N1, N2], edges: [] },
  created_ts: 1, updated_ts: 1, last_run: null, last_result: "", readonly: false,
};

/** Renders per card, counted where the card renders: the footer's summary.
 *  Both canvases call `def.sum` once per render of a non-TARGET card. */
let renders = 0;
{
  const orig = NODE_DEFS.capture.sum;
  NODE_DEFS.capture.sum = (p) => { renders++; return orig(p); };
}

const container = win.document.getElementById("root");
const root = createRoot(container);

function mount(): void {
  act(() => root.render(createElement(Fragment, null,
    createElement("div", { "data-canvas": "classic" },
      [N1, N2].map((n) => createElement(ClassicCard, { key: n.id, node: n }))),
    createElement("div", { "data-canvas": "next" },
      [N1, N2].map((n) => createElement(NextCard, { key: n.id, node: n }))))));
}

type Canvas = "classic" | "next";
const card = (c: Canvas, id: string): any =>
  container.querySelector(`[data-canvas="${c}"] [data-node-id="${id}"]`);
/** The status dot: the classic `Led`, the #/next `.nx-flow-node-led`. */
const dot = (c: Canvas, id: string): any =>
  card(c, id)?.querySelector(c === "classic" ? ".led" : ".nx-flow-node-led") ?? null;

/** Everything one card says about its status, as words: its text, every
 *  accessible name and tooltip under it, and its own `data-status`. A dot
 *  that is merely emptied of its name would still draw a ring, so the dot's
 *  presence is graded separately; this is what a screen reader or a hover
 *  would be told. */
function says(c: Canvas, id: string): string {
  const el = card(c, id);
  if (!el) return "no card";
  const words: string[] = [el.textContent ?? "", el.getAttribute("data-status") ?? ""];
  for (const d of el.querySelectorAll("[aria-label], [title]")) {
    words.push(d.getAttribute("aria-label") ?? "", d.getAttribute("title") ?? "");
  }
  return words.join(" | ");
}
const claimsIdle = (c: Canvas, id: string): boolean => /idle/i.test(says(c, id));
/** Whether the dot is in the card at all (a boolean: a DOM node is not
 *  something `eq` can print). */
const drawn = (c: Canvas, id: string): boolean => dot(c, id) !== null;

// ------------------------------------------------------------ store scenarios
const RUN = (sid: string, extra: Partial<SequenceState> = {}): SequenceState => ({
  state: "running",
  session: { id: sid, name: "Session", count_mode: "accepted", accepted: 0 },
  ...extra,
} as SequenceState);
const IDLE_RUN: SequenceState = { state: "idle" };

/** The store as the open flow `f1` with its known sessions (or none open),
 *  the given sequence state, and the given written statuses. */
function stage(opts: {
  open?: boolean; sessionIds?: string[]; sequence?: SequenceState;
  statuses?: Record<string, string>;
}): void {
  act(() => {
    useStore.setState({
      sequence: opts.sequence ?? IDLE_RUN,
      flows: {
        ...FLOWS_INIT, ui: { ...FLOWS_INIT.ui },
        record: opts.open === false ? null : REC,
        graph: REC.graph,
        sessionIds: opts.sessionIds ?? [],
        statuses: opts.statuses ?? {},
      },
    } as any);
  });
}
const setSequence = (sequence: SequenceState): void => {
  act(() => { useStore.setState({ sequence } as any); });
};

// =================================================== 1. a live run, unwritten

test("a live run of the open flow with nothing written: neither canvas draws a dot or says IDLE", () => {
  stage({ sessionIds: ["s1"], sequence: RUN("s1") });
  mount();
  for (const c of ["classic", "next"] as const) {
    for (const id of ["n1", "n2"]) {
      eq(card(c, id) !== null, true, `precondition: the ${c} card for ${id} is mounted`);
      eq(drawn(c, id), false, `the ${c} card for ${id} draws a status dot through a live run of its own flow`);
      eq(claimsIdle(c, id), false,
        `the ${c} card for ${id} says IDLE through a live run: ${says(c, id)}`);
    }
  }
});

// ============================================================== 2. no run

test("no run: both canvases say IDLE where it is true", () => {
  stage({ sessionIds: ["s1"], sequence: IDLE_RUN });
  mount();
  const led = dot("classic", "n1");
  eq(led?.getAttribute("aria-label"), "idle", "the classic card's LED name with no run");
  eq(led?.className.includes("led-off"), true, "the classic card's LED is the off one with no run");
  const d = dot("next", "n1");
  eq(d?.getAttribute("aria-label"), "stage IDLE", "the #/next dot's accessible name with no run");
  eq(d?.getAttribute("title"), "IDLE", "the #/next dot's tooltip with no run");
  eq(d?.getAttribute("data-status"), "idle", "the #/next dot's status with no run");
  eq(card("next", "n1")?.getAttribute("data-status"), "idle", "the #/next card's status with no run");
});

// ============================================== 3. a run that is not this flow's

test("a live run of ANOTHER flow, or with no flow open, leaves IDLE standing", () => {
  stage({ sessionIds: ["s1"], sequence: RUN("other-flows-session") });
  mount();
  eq(dot("classic", "n1")?.getAttribute("aria-label"), "idle",
    "the classic LED through another flow's run: nothing of THIS flow is running, so idle is true");
  eq(dot("next", "n1")?.getAttribute("aria-label"), "stage IDLE",
    "the #/next dot through another flow's run");

  // The slice drops `sessionIds` with the record; a list left behind by a
  // write that cleared only the record belongs to no flow (`knownSessions`).
  stage({ open: false, sessionIds: ["s1"], sequence: RUN("s1") });
  mount();
  eq(dot("classic", "n1")?.getAttribute("aria-label"), "idle",
    "the classic LED with no record open, although the sequence names a session in the stale list");
  eq(dot("next", "n1")?.getAttribute("aria-label"), "stage IDLE",
    "the #/next dot with no record open");
});

// ========================================= 4. a written status outranks it

test("a written status outranks the rule per stage: n1 reads BUSY through the live run, n2 reads nothing", () => {
  stage({ sessionIds: ["s1"], sequence: RUN("s1"), statuses: { n1: "busy" } });
  mount();
  const led = dot("classic", "n1");
  eq(led?.getAttribute("aria-label"), "busy", "the classic LED of the written stage");
  eq(led?.className.includes("led-busy"), true, "the classic LED of the written stage is the busy one");
  const d = dot("next", "n1");
  eq(d?.getAttribute("aria-label"), "stage BUSY", "the #/next dot's name for the written stage");
  eq(d?.getAttribute("data-status"), "busy", "the #/next dot's status for the written stage");
  eq(d?.getAttribute("data-pulse"), "true", "the #/next dot pulses for the written stage");
  eq(drawn("classic", "n2"), false, "the classic LED of the unwritten stage, beside a written one");
  eq(drawn("next", "n2"), false, "the #/next dot of the unwritten stage, beside a written one");
});

// ===================================================== 5. the run ending

test("the run ending brings IDLE back", () => {
  stage({ sessionIds: ["s1"], sequence: RUN("s1") });
  mount();
  eq(drawn("next", "n1"), false, "precondition: no dot through the live run");
  setSequence({ ...RUN("s1"), state: "complete" });
  eq(dot("classic", "n1")?.getAttribute("aria-label"), "idle", "the classic LED once the run is over");
  eq(dot("next", "n1")?.getAttribute("aria-label"), "stage IDLE", "the #/next dot once the run is over");
  setSequence(RUN("s1"));
  eq(drawn("next", "n1"), false, "a run that goes live again takes the dot away again");
});

// ====================================================== 6. re-render discipline

test("a sequence tick inside a live run re-renders neither card", () => {
  stage({ sessionIds: ["s1"], sequence: RUN("s1") });
  mount();
  const before = renders;
  setSequence(RUN("s1", { detail: "M31 1-1: L 30s  [4/30]" }));
  setSequence(RUN("s1", { detail: "M31 1-1: L 30s  [5/30]" }));
  eq(renders - before, 0,
    "renders caused by two sequence ticks of the same live run: a subscription returned the sequence object, not a boolean");

  // The counter does see a card render: a status written to n1 re-renders n1
  // on both canvases and neither n2.
  const mid = renders;
  act(() => {
    useStore.setState({ flows: { ...useStore.getState().flows, statuses: { n1: "busy" } } } as any);
  });
  eq(renders - mid, 2, "precondition: writing n1's status re-renders n1 on both canvases and nothing else");
});

// ----------------------------------------------------------------- report
act(() => root.render(null));
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nw15NodeStatusLiveRun.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
