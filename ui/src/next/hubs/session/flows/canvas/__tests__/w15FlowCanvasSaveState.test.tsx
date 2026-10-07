// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w15FlowCanvasSaveState.test.tsx - the #/next toolbar says SAVING while a PUT
// is out, does not send a second beside it, and its draft-checks sentence no
// longer sends the operator to a SAVE the flow makes for itself (#688, part 2
// of 3; WP-99).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/session/flows/canvas/__tests__/w15FlowCanvasSaveState.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE TWO DEFECTS THE AUTOSAVE WOULD OTHERWISE HAVE MADE.
//
//   1. A PUT is now out two seconds after the last edit, and over the relay it
//      takes a second or more. SAVE pressed in that gap sent the same graph
//      BESIDE the one in flight, and two PUTs can answer out of order. The
//      pill said UNSAVED EDITS under a PUT that was already carrying them. So
//      the pill says SAVING (`flows.saving`), and SAVE says why it is not
//      pressable meanwhile.
//   2. The draft-checks popover said "RUN starts the SAVED flow, so save
//      first": false since RUN saves first (#688 part 1) and the flow saves
//      itself. For an Example, whose edits are never saved, the true sentence
//      is the other one.
//
// The PUT is held open with a deferred fetch, so "SAVING" is a state the test
// stands in, not a moment it races. Every case names the mutant it kills and
// quotes what that mutant produced when run from a byte-for-byte backup of the
// file named (restored and sha256-compared after each run, the mutant's marker
// grepped absent).

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
win.matchMedia = (q: string) => {
  const m = /min-width:\s*(\d+)px/.exec(q);
  return {
    matches: m ? 820 >= Number(m[1]) : false,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {},
  };
};
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "ResizeObserver", "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- the fake rig
// `PUT /api/flows/{id}` is held until a test answers it.
interface HeldPut { body: any; answer: () => void }
let heldPuts: HeldPut[] = [];
const asked: { url: string; method: string }[] = [];
g.fetch = (url: string, init?: { method?: string; body?: string }) => {
  const method = init?.method ?? "GET";
  const body = init?.body ? JSON.parse(init.body) : undefined;
  asked.push({ url, method });
  const reply = (data: unknown) => ({
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => data,
    text: async () => JSON.stringify(data),
  });
  if (method === "PUT" && /^\/api\/flows\/[^/]+$/.test(url)) {
    return new Promise((resolve) => {
      heldPuts.push({
        body,
        answer: () => resolve(reply({ ...RECORD, ...(body?.flow ?? {}), id: "flow-m16" })),
      });
    });
  }
  return Promise.resolve(reply({ ok: true }));
};

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../../store");
const { FlowCanvasToolbar } = await import("../FlowCanvasToolbar");
const {
  CHECKS_DRAFT_EXAMPLE_WHY, CHECKS_DRAFT_WHY, SAVE_CLEAN_REASON, SAVE_SAVING_REASON,
  SAVE_STATE_CLEAN, SAVE_STATE_DIRTY, SAVE_STATE_SAVING, checksDraftWhy, saveLockReason,
  saveStateTone,
} = await import("../canvasModel");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
  finally {
    // A PUT left held would make every later flush of the shared slice wait.
    await act(async () => { for (const p of heldPuts) p.answer(); heldPuts = []; });
  }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg}\n  expected ${String(want)}\n  got      ${String(got)}`);
}

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const settle = async (): Promise<void> => {
  for (let i = 0; i < 4; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const tid = (t: string): any => container.querySelector(`[data-testid="${t}"]`);
const click = (el: any): void => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};

// ------------------------------------------------------------------ fixture
const GRAPH = {
  nodes: [{ id: "n2", type: "target", x: 300, y: 0, params: {} }],
  edges: [],
};
const RECORD = {
  id: "flow-m16", name: "M16 - full-service night", folder: "My flows",
  tagline: "", graph: GRAPH, created_ts: 1, updated_ts: 2,
  last_run: null, last_result: "", readonly: false,
};
const ADMIN_CAPS = [
  "view.status", "view.preview", "control.mount", "control.capture", "view.site_derived",
];

function seed(flows: Record<string, unknown> = {}): void {
  heldPuts = [];
  act(() => {
    const s = useStore.getState();
    useStore.setState({
      principal: { role: "admin", email: null, caps: ADMIN_CAPS } as never,
      authGate: "open",
      status: { connected: { camera: { connected: true } } } as never,
      equipConnected: true,
      wsPhase: "up",
      toasts: [],
      sequence: { state: "idle" } as never,
      flows: {
        ...s.flows,
        record: RECORD as never,
        graph: JSON.parse(JSON.stringify(GRAPH)),
        dirty: false, saving: false,
        sel: null, editNode: null, wire: null, tapWire: null,
        statuses: {}, logs: [], compiled: null,
        progress: null, sessionIds: [],
        run: { ...s.flows.run, phase: "idle", etaS: null, startedAt: null },
        ui: { ...s.flows.ui, notesOpen: false, logOpen: false },
        ...flows,
      } as never,
    } as never);
  });
}
async function mount(): Promise<void> {
  await act(async () => { root.render(createElement("div")); });
  await act(async () => { root.render(createElement(FlowCanvasToolbar as any)); });
  await settle();
}
const puts = (): number => asked.filter((a) => a.method === "PUT").length;

// ============================================ 1. SAVING, AND ONE PUT AT A TIME

// MUTANT "the toolbar ignores saving" (FlowCanvasToolbar.tsx: `saveStateWord(
// dirty, readonly, saving)` made `saveStateWord(dirty, readonly)`). Observed,
// 2/3:
//   x a PUT out says SAVING, SAVE sends no second one beside it, and the
//     answer says SAVED: a PUT is out and the pill still says the edits are
//     unsaved
//     expected SAVING
//     got      UNSAVED EDITS
// MUTANT "SAVE pressable while saving" (canvasModel.ts `saveLockReason`: the
// `if (saving) return SAVE_SAVING_REASON;` deleted). Observed, 1/3:
//   x ...: SAVE is pressable while a PUT is out
//     expected true
//     got      null
// (and the pure-words case: "a PUT out is a reason", expected "This flow is
// saving now.", got null.)
// MUTANT "the lock does not stop the press" (FlowCanvasToolbar.tsx:
// `saveLockReason(dirty, readonly, saving)` made `saveLockReason(dirty,
// readonly)`). Observed, 2/3: the same line, expected true, got null.
await testAsync("a PUT out says SAVING, SAVE sends no second one beside it, and the answer says SAVED", async () => {
  seed({ dirty: true });
  await mount();
  eq(tid("flow-save-state").textContent, SAVE_STATE_DIRTY, "precondition: the edit is unsaved");
  eq(tid("flow-save").getAttribute("aria-disabled"), null, "precondition: SAVE is live over an edit");

  const before = puts();
  click(tid("flow-save"));
  await settle();
  eq(puts() - before, 1, "precondition: the press sent its PUT");
  eq(tid("flow-save-state").textContent, SAVE_STATE_SAVING,
    "a PUT is out and the pill still says the edits are unsaved");
  eq(tid("flow-save").getAttribute("aria-disabled"), "true", "SAVE is pressable while a PUT is out");
  eq(tid("flow-save").getAttribute("title"), SAVE_SAVING_REASON, "and it does not say why");
  click(tid("flow-save"));
  await settle();
  eq(puts() - before, 1, "a second press sent the same graph beside the one in flight");

  await act(async () => { heldPuts[0].answer(); });
  await settle();
  eq(tid("flow-save-state").textContent, SAVE_STATE_CLEAN, "the PUT was answered and the pill is not SAVED");
  eq(tid("flow-save").getAttribute("title"), SAVE_CLEAN_REASON, "SAVE has nothing left to say but that");
});

await testAsync("the pure words: SAVING is dim and its lock is a sentence", async () => {
  eq(saveStateTone(true, false, true), "dim", "a PUT on its way is drawn as neglected");
  eq(saveStateTone(true, false, false), "warn", "unsent edits are not drawn as pending");
  eq(saveStateTone(true, true, false), "dim", "an Example's edits are drawn as pending");
  eq(saveLockReason(true, false, true), SAVE_SAVING_REASON, "a PUT out is a reason");
  eq(saveLockReason(true, false, false), null, "an edit with nothing out is pressable");
  eq(saveLockReason(true, true, true) === SAVE_SAVING_REASON, false,
    "an Example's reason is that it cannot be saved, not that it is saving");
});

// ================================================== 2. THE DRAFT SENTENCE

// MUTANT "the old sentence" (canvasModel.ts `CHECKS_DRAFT_WHY` restored to
// "These checks are on the graph as drawn. RUN starts the SAVED flow, so save
// first and they describe what the rig will do."). Observed, 2/3:
//   x the draft-checks sentence no longer tells the operator to save first,
//     and an Example gets its own: the draft-checks sentence sends the
//     operator to a SAVE: RUN saves first now, and the flow saves itself
//     expected false
//     got      true
// MUTANT "the Example gets the ordinary sentence" (canvasModel.ts
// `checksDraftWhy`: `readonly ? CHECKS_DRAFT_EXAMPLE_WHY : CHECKS_DRAFT_WHY`
// made `CHECKS_DRAFT_WHY`). Observed, 2/3:
//   x ...: an Example's sentence
//     expected These checks are on the graph as drawn. This example flow
//       cannot be saved, so RUN would start the stored version, not the one
//       drawn here.
//     got      These checks are on the graph as drawn, which the rig does not
//       hold yet. The flow saves itself a moment after your last edit, and RUN
//       and TONIGHT save it first.
await testAsync("the draft-checks sentence no longer tells the operator to save first, and an Example gets its own", async () => {
  eq(/save first/i.test(CHECKS_DRAFT_WHY), false,
    "the draft-checks sentence sends the operator to a SAVE: RUN saves first now, and the flow saves itself");
  eq(/saves itself/.test(CHECKS_DRAFT_WHY) && /RUN and TONIGHT save it first/.test(CHECKS_DRAFT_WHY), true,
    `the sentence must say what happens instead: ${CHECKS_DRAFT_WHY}`);
  eq(checksDraftWhy(false), CHECKS_DRAFT_WHY, "an ordinary flow's sentence");
  eq(checksDraftWhy(true), CHECKS_DRAFT_EXAMPLE_WHY, "an Example's sentence");
  eq(/cannot be saved/.test(CHECKS_DRAFT_EXAMPLE_WHY), true,
    `the Example's sentence must say it cannot be saved: ${CHECKS_DRAFT_EXAMPLE_WHY}`);

  // And it is what the mounted popover draws, for each kind of flow.
  const compiled = { plan: {}, structural: [], issues: [], unmapped: [] };
  seed({ dirty: true, compiled });
  await mount();
  click(tid("flow-checks"));
  await settle();
  const ordinary = String(win.document.body.textContent);
  assert(ordinary.includes(CHECKS_DRAFT_WHY), "the popover over an edited flow does not carry the draft sentence");

  seed({ dirty: true, compiled, record: { ...RECORD, readonly: true } as never });
  await mount();
  click(tid("flow-checks"));
  await settle();
  const example = String(win.document.body.textContent);
  assert(example.includes(CHECKS_DRAFT_EXAMPLE_WHY),
    "the popover over an edited Example does not carry the Example's sentence");
  assert(!example.includes(CHECKS_DRAFT_WHY), "an Example was told its flow saves itself");
});

// ------------------------------------------------------------------- report
const total = passed + failed;
console.log(`w15FlowCanvasSaveState.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
