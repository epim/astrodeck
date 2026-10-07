// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w15FlowHeaderSaveState.test.tsx - the classic editor's header says whether
// the rig holds the graph on screen: SAVING / SAVED / UNSAVED EDITS, in the
// #/next toolbar's own words, and the real store autosaves the flow it
// shows (#688, part 2 of 3; WP-99).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/w15FlowHeaderSaveState.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHY THE PILL. The classic editor has no SAVE press: its edits were stored by
// closing the editor, by TONIGHT and by RUN, and nothing on screen said
// whether the rig had them. With the autosave (w15FlowsAutosave.test.ts pins
// the slice) the one place the operator can see the state is this word. It is
// the #/next toolbar's pill word (`saveStateWord`, one function in the pure
// module both editors import), so the two editors cannot word one fact two
// ways, and over a live run it says why an edit is waiting.
//
// WHAT IS MOUNTED. The production header against the real store, with the
// slice's `flowsApi` methods replaced and nothing else: the case in section 3
// edits through the store's own action and watches the mounted pill go from
// UNSAVED EDITS through SAVING to SAVED, so the wiring that hands the slice
// the store's subscription (`createFlowsActions(set, get, storeApi)`) is
// exercised too, which the slice-level file cannot see.
//
// Every case names the mutant it kills and quotes what that mutant produced
// when run from a byte-for-byte backup of the file named (restored and
// sha256-compared after each run, the mutant's marker grepped absent).

/* eslint-disable @typescript-eslint/no-explicit-any */

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
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "FocusEvent", "localStorage", "sessionStorage",
  "getComputedStyle", "matchMedia", "WebSocket", "ResizeObserver",
  "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ---------------------------------------------------------------- fake time
// Only the autosave's own timer is faked, and only inside the one case that
// needs it (`withFakeTime`): React and the harness keep the real clock.
const realSetTimeout = globalThis.setTimeout;
const realClearTimeout = globalThis.clearTimeout;
interface Armed { id: number; due: number; fn: () => void }
let armed: Armed[] = [];
let nextTimerId = 1;
let CLOCK = Date.now();
function installFakeTime(): void {
  armed = [];
  (globalThis as any).setTimeout = (fn: () => void, ms = 0): number => {
    const id = nextTimerId++;
    armed.push({ id, due: CLOCK + ms, fn });
    return id;
  };
  (globalThis as any).clearTimeout = (id?: number): void => {
    armed = armed.filter((a) => a.id !== id);
  };
}
function restoreTime(): void {
  (globalThis as any).setTimeout = realSetTimeout;
  (globalThis as any).clearTimeout = realClearTimeout;
}
/** Run every fake timer due within `ms`, then let the work they started run. */
async function advance(ms: number): Promise<void> {
  const end = CLOCK + ms;
  for (;;) {
    const next = armed.filter((a) => a.due <= end).sort((a, b) => a.due - b.due)[0];
    if (!next) break;
    armed = armed.filter((a) => a !== next);
    CLOCK = Math.max(CLOCK, next.due);
    next.fn();
    await tickReal();
  }
  CLOCK = end;
  await tickReal();
}
const tickReal = () => new Promise<void>((r) => realSetTimeout(r, 0));

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const { flowsApi } = await import("../../../lib/flowsApi");
const { default: FlowHeader, saveStateClass } = await import("../FlowHeader");
const { RUN_PHASE_BRIDGE_MS } = await import("../flowRunControls");
const { AUTOSAVE_RUN_BRIDGE_MS, AUTOSAVE_QUIET_MS } = await import("../flowsSlice");
const {
  AUTOSAVE_PAUSED_NOTE, SAVE_READONLY_REASON, SAVE_STATE_CLEAN, SAVE_STATE_DIRTY,
  SAVE_STATE_READONLY, SAVE_STATE_SAVING, saveStateWord,
} = await import("../flowsTypes");
const canvasModel = await import("../../../next/hubs/session/flows/canvas/canvasModel");
const { NODE_DEFS } = await import("../nodeDefs");

// ------------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
  finally { restoreTime(); await drain(); }
}
/** A PUT still held when a case ends (it failed before it answered it) is
 *  answered here: the slice keeps its save in a closure the whole file shares,
 *  and every later case would wait on it for ever. */
async function drain(): Promise<void> {
  await act(async () => {
    for (const p of puts) p.resolve({ ...p.flow, updated_ts: 9 });
    puts = [];
  });
  await tickReal();
}
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}

// ------------------------------------------------------------------ stubs
interface Put { flow: any; resolve: (v: unknown) => void; reject: (e: unknown) => void }
let puts: Put[] = [];
(flowsApi as any).save = (_id: string, flow: any) =>
  new Promise((resolve, reject) => { puts.push({ flow, resolve, reject }); });
(flowsApi as any).compileDraft = async () => ({ plan: {}, structural: [], issues: [], unmapped: [] });
(flowsApi as any).progress = async () => { throw new Error("Not Found"); };

// ------------------------------------------------------------------ fixture
const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "control.capture", "control.mount", "view.site_derived"],
};
const GRAPH = {
  nodes: [{ id: "t1", type: "target", x: 0, y: 0, params: { ...NODE_DEFS.target.params } }],
  edges: [],
};
function record(readonly: boolean) {
  return {
    id: "f1", name: "NGC7331 Preferential Filtering", folder: "My flows", tagline: "",
    graph: GRAPH, created_ts: 0, updated_ts: 0,
    last_run: null, last_result: "" as const, readonly,
  };
}

function seed(o: {
  dirty?: boolean; saving?: boolean; readonly?: boolean; liveSession?: boolean;
}): void {
  const flows = useStore.getState().flows;
  puts = [];
  act(() => { useStore.setState({
    principal: OPERATOR,
    equipConnected: true,
    wsPhase: "up",
    confirm: null,
    toasts: [],
    status: { connected: { camera: { connected: true, name: "sim" } } },
    sequence: o.liveSession
      ? { state: "running", session: { id: "s1" } }
      : { state: "idle" },
    flows: {
      ...flows,
      record: record(o.readonly === true),
      graph: JSON.parse(JSON.stringify(GRAPH)),
      dirty: o.dirty === true,
      saving: o.saving === true,
      sessionIds: o.liveSession ? ["s1"] : [],
      logs: [],
      libraryError: null,
      compiled: null,
      progress: null,
      run: { ...flows.run, phase: "idle", startedAt: null },
      ui: { ...flows.ui, screen: "editor" },
    },
  } as never); });
}

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const settle = async (): Promise<void> => {
  for (let i = 0; i < 3; i++) await act(async () => { await tickReal(); });
};
async function mount(tier: "tablet" | "phone" = "tablet"): Promise<void> {
  await act(async () => { root.render(createElement("div")); });
  await act(async () => { root.render(createElement(FlowHeader as any, { tier })); });
  await settle();
}
const pill = (): any => container.querySelector('[data-testid="flow-header-save-state"]');
const pillText = (): string => String(pill()?.textContent ?? "(no pill)");

// ============================================ 1. THE WORDS, SHARED, AND THE PILL

// MUTANT "a second set of words" (flowsTypes.ts `SAVE_STATE_CLEAN = "SAVED"`
// made "SAVED."). The two editors read one constant, so the mutant moves both
// and the identity checks below hold; the literal pins the words themselves.
// Observed, 3/6:
//   x the classic header and the #/next toolbar say the same four words: the
//     ruling's four words
//     expected "SAVED|UNSAVED EDITS|READ ONLY|SAVING"
//     got      "SAVED.|UNSAVED EDITS|READ ONLY|SAVING"
await testAsync("the classic header and the #/next toolbar say the same four words", async () => {
  eq(canvasModel.SAVE_STATE_CLEAN, SAVE_STATE_CLEAN, "the classic header and the #/next toolbar word SAVED differently");
  eq(canvasModel.SAVE_STATE_DIRTY, SAVE_STATE_DIRTY, "the two editors word UNSAVED EDITS differently");
  eq(canvasModel.SAVE_STATE_READONLY, SAVE_STATE_READONLY, "the two editors word READ ONLY differently");
  eq(canvasModel.SAVE_STATE_SAVING, SAVE_STATE_SAVING, "the two editors word SAVING differently");
  eq(canvasModel.saveStateWord, saveStateWord, "the toolbar has its own word function");
  eq([SAVE_STATE_CLEAN, SAVE_STATE_DIRTY, SAVE_STATE_READONLY, SAVE_STATE_SAVING].join("|"),
    "SAVED|UNSAVED EDITS|READ ONLY|SAVING", "the ruling's four words");
  eq(canvasModel.SAVE_READONLY_REASON, SAVE_READONLY_REASON, "the READ ONLY sentence has two homes");
  // The word function: READ ONLY first, then SAVING over UNSAVED EDITS.
  eq(saveStateWord(true, true, true), SAVE_STATE_READONLY, "an Example is read-only even mid-PUT");
  eq(saveStateWord(true, false, true), SAVE_STATE_SAVING, "a PUT out outranks the edit it carries");
  eq(saveStateWord(true, false, false), SAVE_STATE_DIRTY, "an edit nobody is sending is unsaved");
  eq(saveStateWord(false, false, false), SAVE_STATE_CLEAN, "a clean flow is saved");
  eq(AUTOSAVE_RUN_BRIDGE_MS, RUN_PHASE_BRIDGE_MS,
    "the autosave's reading of the optimistic run latch drifted from the RUN button's (#647)");
});

// MUTANT "no pill" (FlowHeader.tsx: the `{editor && !phone && (` of section
// 8b made `{false && (`). Observed, 3/6:
//   x the pill says SAVED, UNSAVED EDITS, SAVING and READ ONLY for the flow
//     open: a clean flow's pill
//     expected "SAVED"
//     got      "(no pill)"
// MUTANT "the pill ignores saving" (FlowHeader.tsx: `saveStateWord(dirty,
// readonly, saving)` made `saveStateWord(dirty, readonly)`). Observed, 4/6:
//   x the pill says SAVED, UNSAVED EDITS, SAVING and READ ONLY for the flow
//     open: a flow with a PUT out says so
//     expected "SAVING"
//     got      "UNSAVED EDITS"
await testAsync("the pill says SAVED, UNSAVED EDITS, SAVING and READ ONLY for the flow open", async () => {
  seed({});
  await mount();
  eq(pillText(), "SAVED", "a clean flow's pill");
  seed({ dirty: true });
  await settle();
  eq(pillText(), "UNSAVED EDITS", "an edited flow's pill");
  eq(pill().className.includes("text-warn"), true, "edits nothing is sending are drawn in warn");
  seed({ dirty: true, saving: true });
  await settle();
  eq(pillText(), "SAVING", "a flow with a PUT out says so");
  eq(pill().className.includes("text-warn"), false, "a PUT on its way is not drawn as neglected");
  seed({ dirty: true, readonly: true });
  await settle();
  eq(pillText(), "READ ONLY", "an Example's pill");
  eq(pill().getAttribute("title"), SAVE_READONLY_REASON, "READ ONLY does not say why");
  eq(saveStateClass(true, true, false), "text-dim", "an Example's edits are not drawn as pending");
});

// MUTANT "the pill on the library screen" (FlowHeader.tsx: `{editor && !phone
// && (` made `{!phone && (`). Observed, 5/6:
//   x no pill over the library, where no flow is open: the library header
//     shows a save state for no flow
//     expected "no pill"
//     got      "a pill reading UNSAVED EDITS"
await testAsync("no pill over the library, where no flow is open", async () => {
  seed({ dirty: true });
  act(() => { useStore.setState({
    flows: { ...useStore.getState().flows, ui: { ...useStore.getState().flows.ui, screen: "library" } },
  } as never); });
  await mount();
  eq(pill() === null ? "no pill" : `a pill reading ${pillText()}`, "no pill",
    "the library header shows a save state for no flow");
});

// MUTANT "the phone has no pill" (FlowHeader.tsx: the phone span's testid
// renamed, so the pill under the title is not found). Observed, 5/6:
//   x the phone header carries the pill once, under the flow's name: the phone
//     header must carry exactly one pill
//     expected 1
//     got      0
// MUTANT "two pills" (the tablet pill's `!phone &&` removed). Observed, 5/6;
// the same case, expected 1, got 2.
await testAsync("the phone header carries the pill once, under the flow's name", async () => {
  seed({ dirty: true });
  await mount("phone");
  eq(container.querySelectorAll('[data-testid="flow-header-save-state"]').length, 1,
    "the phone header must carry exactly one pill");
  eq(pillText(), "UNSAVED EDITS", "the phone's pill");
  eq(pill().parentElement?.firstElementChild?.textContent, "NGC7331 Preferential Filtering",
    "the pill is not under the flow's name");
  eq(pill().className.includes("truncate"), true,
    "the phone's pill can push the row wider than the screen: it must truncate");
});

// ============================================== 2. WHAT A LIVE RUN SAYS

// MUTANT "no pause note" (FlowHeader.tsx: `running && dirty && !saving ?
// AUTOSAVE_PAUSED_NOTE` made `false ? AUTOSAVE_PAUSED_NOTE`). Observed, 5/6:
//   x over a live run an unsaved edit says why it is waiting, and only then:
//     UNSAVED EDITS over a live run says nothing about why
//     expected "Autosave is paused while this flow's run is live, because a
//       save re-anchors the counts of blocks that have banked subs. The edit
//       is saved a moment after the run ends."
//     got      null
await testAsync("over a live run an unsaved edit says why it is waiting, and only then", async () => {
  seed({ dirty: true, liveSession: true });
  await mount();
  eq(pillText(), "UNSAVED EDITS", "precondition: the edit is unsaved");
  eq(pill().getAttribute("title"), AUTOSAVE_PAUSED_NOTE,
    "UNSAVED EDITS over a live run says nothing about why");
  seed({ dirty: true });
  await settle();
  eq(pill().getAttribute("title"), null,
    "with no run live the pill claims the autosave is paused");
  seed({ liveSession: true });
  await settle();
  eq(pill().getAttribute("title"), null, "a clean flow over a live run claims the autosave is paused");
});

// ===================================================== 3. THE REAL STORE SAVES

// MUTANT "the store hands the slice no subscription" (store.ts is not this
// WP's to edit, so run against the slice instead: flowsSlice.ts's
// `seqSeen = s.sequence;` in the subscription made `seqSeen = s.sequence;
// return;`, which is what a store that gave the slice no `api` produces).
// Observed, 5/6:
//   x an edit through the real store is saved 2000 ms later, and the mounted
//     pill follows it: the real store did not hand the slice its
//     subscription, so the edit was never saved
//     expected "1 PUTs 2000 ms after an edit through the real store"
//     got      "0 PUTs 2000 ms after an edit through the real store"
// MUTANT "the bridge window drifts" (flowsSlice.ts `AUTOSAVE_RUN_BRIDGE_MS =
// 20_000` made 15_000). Observed, 5/6, in the first case:
//     expected 20000
//     got      15000
await testAsync("an edit through the real store is saved 2000 ms later, and the mounted pill follows it", async () => {
  seed({});
  await mount();
  eq(pillText(), "SAVED", "precondition: a clean flow");
  installFakeTime();
  act(() => { useStore.getState().flowsSetParam("t1", "name", "M 31"); });
  await settle();
  eq(pillText(), "UNSAVED EDITS", "the edit did not show");
  await act(async () => { await advance(AUTOSAVE_QUIET_MS - 1); });
  eq(puts.length, 0, "the flow was saved before its quiet window ended");
  await act(async () => { await advance(1); });
  eq(`${puts.length} PUTs ${AUTOSAVE_QUIET_MS} ms after an edit through the real store`,
    `1 PUTs ${AUTOSAVE_QUIET_MS} ms after an edit through the real store`,
    "the real store did not hand the slice its subscription, so the edit was never saved");
  await settle();
  eq(pillText(), "SAVING", "a PUT is out and the header does not say so");
  await act(async () => { puts[0].resolve({ ...puts[0].flow, updated_ts: 2 }); });
  await settle();
  eq(pillText(), "SAVED", "the PUT was answered and the header still says it is not saved");
  eq(String(puts[0].flow.graph.nodes[0].params.name), "M 31", "the PUT did not carry the edit");
});

// ------------------------------------------------------------------- report
const total = passed + failed;
console.log(`w15FlowHeaderSaveState.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
