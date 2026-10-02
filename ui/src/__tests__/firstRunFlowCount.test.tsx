// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// firstRunFlowCount.test.tsx - the classic first-run guide's "Pick a target"
// step ticks for a flow the wizard saved (#458; the S5/S6 integration).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/__tests__/firstRunFlowCount.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT THIS GUARDS. S6 (#196) made the step say "press SEND TO FLOW WIZARD",
// and the wizard's GENERATE saves a flow and adds nothing to the Plan. The
// step's completion (`lib/firstRunWizard.ts` `isDone("target")`) learned to
// count saved flows, but the docked bar (`components/FirstRunWizard.tsx`)
// passed no flow count, so a first-run user who did exactly what the step
// said was left on a locked Next. The bar now passes the store library's
// count and reads the library once when it opens; the wizard's GENERATE
// refreshes that library (graded in sendToWizardSheet.test.tsx). The pure
// predicate is graded in deletedDoorStrings.test.ts; this file mounts the bar
// on the real store and reads its Next button, the control the user presses,
// and its progress chip.
//
// Every mutant below ran in a private scratch copy of ui/
// (scratchpad/S5-FINAL-INTEG-ui-mut), from a byte backup restored with its
// sha256 checked, never in the shared tree (#254). Output verbatim.
//
//   MUTANT "the bar passes no flow count" (FirstRunWizard.tsx: `flowCount`
//   left out of the snapshot it hands `computeWizard`). Observed
//   ("firstRunFlowCount.test: 1/3 passed"):
//     x a flow the server has saved finishes 'Pick a target', the library read once on opening: a saved flow did not finish the step that sends the user to the wizard:
//       expected ["1 of 5 done",null]
//       got      ["0 of 5 done","target"]
//     x a library the store already holds ticks the step without reading it again: the store's saved flow did not unlock Next:
//       expected "Next step | aria-disabled=null"
//       got      "Next — unlocks once the wizard has saved a flow, or a target is in your plan | aria-disabled=true"
//
//   MUTANT "the bar never reads the library" (the effect that loads it on
//   opening removed). Observed ("firstRunFlowCount.test: 1/3 passed"):
//     x a flow the server has saved finishes 'Pick a target', the library read once on opening: opening the guide did not read the flows library:
//       expected 1
//       got      0
//     x control: with no saved flow and no Plan target, Next stays locked and says why: opening the guide did not read the flows library:
//       expected 1
//       got      0
//
// Convention: inline test()/eq() helpers, printed tally plus the
// { passed, failed, total } export (shell-and-tests.md section 4).

/* eslint-disable @typescript-eslint/no-explicit-any */

const { registerHooks } = await import("node:module");
registerHooks({
  load(url: string, context: any, nextLoad: any) {
    if (url.endsWith(".css")) return { format: "module", shortCircuit: true, source: "export default {};" };
    return nextLoad(url, context);
  },
} as any);

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
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.HTMLCanvasElement.prototype.getContext = () => null;
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.scrollTo = () => {};
win.HTMLElement.prototype.scrollIntoView = function () {};

// ------------------------------------------------------------ the network
interface Call { method: string; url: string }
let calls: Call[] = [];
/** What the library route holds: the flows the server has saved. */
let saved: unknown[] = [];
win.fetch = async (url: string, init?: { method?: string }) => {
  const c: Call = { method: init?.method ?? "GET", url: String(url) };
  calls.push(c);
  const body = c.method === "GET" && c.url.endsWith("/api/flows") ? saved
    : c.method === "GET" && c.url.endsWith("/api/flows/folders") ? []
      : null;
  const status = body === null ? 404 : 200;
  return {
    ok: status === 200, status, statusText: String(status),
    headers: { get: () => "application/json" },
    json: async () => body ?? { detail: "not in this fixture" },
  };
};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement", "HTMLSelectElement",
  "HTMLTextAreaElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "FocusEvent", "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "ResizeObserver",
  "requestAnimationFrame", "cancelAnimationFrame", "Image", "URL", "fetch", "Blob", "WebSocket",
  "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../store");
const FirstRunWizard = (await import("../components/FirstRunWizard")).default;

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
  finally { act(() => { root.render(null); }); }
}
function eq<T>(got: T, want: T, msg: string): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}

const root = createRoot(win.document.getElementById("root"));
const FLOWS0 = useStore.getState().flows;
const CARD = {
  id: "f1", name: "M31", folder: "My flows", tagline: "", created_ts: 0, updated_ts: 0,
  last_run: null, last_result: "", readonly: false,
};

/** The bar opened on "Pick a target", with an empty Plan and the flows
 *  library as the store holds it at boot (not read yet). */
async function openOnTargetStep(): Promise<void> {
  calls = [];
  useStore.setState({
    flows: { ...FLOWS0, cards: [], libraryLoaded: false },
    plan: { ...useStore.getState().plan, targets: [] },
    wizardOpen: true,
    wizardStepId: "target",
  } as any);
  act(() => { root.render(createElement(FirstRunWizard)); });
  for (let i = 0; i < 3; i++) await act(async () => { await new Promise((r) => setTimeout(r, 5)); });
}
/** The Next button's accessible name: "Next step" when it is live, "Next -
 *  unlocks once ..." when it is not. */
function nextLabel(): string {
  const b = Array.from(win.document.querySelectorAll("button"))
    .find((x: any) => (x.textContent ?? "").trim().endsWith("Next")) as any;
  return b ? `${b.getAttribute("aria-label")} | aria-disabled=${b.getAttribute("aria-disabled")}` : "no Next button";
}
/** The progress chip's accessible name, "... - N of M done": the count of
 *  finished steps, whichever step the bar is showing. */
function chip(): string {
  const b = Array.from(win.document.querySelectorAll("button"))
    .find((x: any) => (x.getAttribute("aria-label") ?? "").includes("full setup list")) as any;
  const m = /(\d+ of \d+ done)/.exec(b?.getAttribute("aria-label") ?? "");
  return m ? m[1] : "no progress chip";
}
const libraryReads = () => calls.filter((c) => c.method === "GET" && c.url.endsWith("/api/flows")).length;

// ==================================================================== cases

await test("a flow the server has saved finishes 'Pick a target', the library read once on opening", async () => {
  saved = [CARD];
  await openOnTargetStep();
  eq(libraryReads(), 1, "opening the guide did not read the flows library:");
  // The step finished while the bar showed it, so the bar did what it does
  // for any step that finishes: it counted it and moved on to the first
  // unfinished one (the manual step cleared, FirstRunWizard's auto-advance).
  eq([chip(), useStore.getState().wizardStepId], ["1 of 5 done", null],
    "a saved flow did not finish the step that sends the user to the wizard:");
});

await test("control: with no saved flow and no Plan target, Next stays locked and says why", async () => {
  saved = [];
  await openOnTargetStep();
  eq(libraryReads(), 1, "opening the guide did not read the flows library:");
  eq([chip(), useStore.getState().wizardStepId], ["0 of 5 done", "target"],
    "the step finished with nothing saved:");
  eq(nextLabel(),
    "Next — unlocks once the wizard has saved a flow, or a target is in your plan | aria-disabled=true",
    "the step unlocked with nothing saved:");
});

await test("a library the store already holds ticks the step without reading it again", async () => {
  saved = [];
  calls = [];
  useStore.setState({
    flows: { ...FLOWS0, cards: [CARD], libraryLoaded: true },
    plan: { ...useStore.getState().plan, targets: [] },
    wizardOpen: true,
    wizardStepId: "target",
  } as any);
  act(() => { root.render(createElement(FirstRunWizard)); });
  for (let i = 0; i < 3; i++) await act(async () => { await new Promise((r) => setTimeout(r, 5)); });
  eq(libraryReads(), 0, "a loaded library was read again:");
  // Done at mount, so nothing rose and the bar stays on the step, Next live.
  eq(nextLabel(), "Next step | aria-disabled=null", "the store's saved flow did not unlock Next:");
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`firstRunFlowCount.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
