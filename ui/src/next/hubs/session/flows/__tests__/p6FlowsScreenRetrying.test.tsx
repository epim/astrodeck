// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// p6FlowsScreenRetrying.test.tsx -- the #/next Flows list says it is asking
// again instead of showing the error and RETRY (#859).
//
//   Run directly:  npx tsx src/next/hubs/session/flows/__tests__/p6FlowsScreenRetrying.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Harness cut down from flowsDom.test.tsx: the same css hook, jsdom globals
// and fetch stub, with the library GETs HELD so the mount's own load stays in
// flight and the store fields under test are the ones this file wrote.
//
// Named mutants:
//   FS   FlowsScreen renders the error block only (the retrying branch
//        deleted) -> `flows-retrying` is absent while libraryRetry is set.
//   NL   delete useFlowLibrary's useRetryOnReturn (now/sessionData.ts) -> a
//        load that settled as failed is not asked again when the tab comes
//        back. FlowsScreen's re-ask IS that hook's: useSessionCampaign reaches
//        useFlowLibrary through useCampaign. (Fix round 1: a second
//        useRetryOnReturn in FlowsScreen itself was redundant, which is why
//        the reviewers' UX2 mutant, deleting it, survived; it was removed.)
//   FSG  drop the `!libraryLoading` guard in FlowsScreen's mount effect -> the
//        mount starts a second load beside its child's useFlowLibrary one, and
//        the superseded first load's request makes five GETs, not four.
//   (#877) U1  the load line reads `libraryError` instead of `libraryLoadError`
//        (the screen pointed back at the shared field): the second and third
//        #877 cases.
//   (#877) U2  the open's line is the `else` of the retrying line, so it hides
//        behind it: the first #877 case.
//   (#919) X1  the DISMISS button is deleted from the open's line: the first
//        #919 case.
//   (#919) X2  the button's onPress does nothing (the store action is never
//        called): the first #919 case.

/* eslint-disable @typescript-eslint/no-explicit-any */
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
  "PointerEvent", "FocusEvent",
  "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

/** "hold": the library GETs never settle; "timeout": they time out the way a
 *  browser's AbortSignal does (api.ts turns that into "request timed out"). */
let libraryMode: "hold" | "timeout" = "hold";
let flowsGets = 0;
g.fetch = async (url: string) => {
  if (url === "/api/flows" || url === "/api/flows/folders") {
    if (url === "/api/flows") flowsGets++;
    if (libraryMode === "timeout") throw new DOMException("The operation timed out.", "TimeoutError");
    return new Promise(() => { /* held: the load stays in flight */ });
  }
  return {
    ok: false, status: 404, statusText: "Not Found",
    headers: { get: () => "application/json" },
    json: async () => ({ detail: "no" }),
  };
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { resetRouterCacheForTests } = await import("../../../../router");
const { FlowsScreen } = await import("../FlowsScreen");
const { setRetrySleepForTests } = await import("../../../../../lib/retryLoad");

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }

const container = win.document.getElementById("root") as any;
let root = createRoot(container);
const settle = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const tid = (t: string): any => container.querySelector(`[data-testid="${t}"]`);

function seed(fields: Record<string, unknown>): void {
  act(() => {
    const f = useStore.getState().flows;
    useStore.setState({
      principal: { role: "admin", email: null, caps: ["view.status", "control.capture"] } as never,
      authGate: "open",
      sequence: { state: "idle" } as never,
      status: {} as never,
      wsPhase: "up",
      resumeArm: null as never,
      flows: {
        ...f, record: null, cards: [], folders: [], libraryLoaded: false,
        libraryError: null, libraryLoadError: null, libraryRetry: null, libraryLoading: false,
        run: { ...f.run, phase: "idle" },
        ui: { ...f.ui, screen: "library", query: "", folderChip: "all", highlightId: null },
        ...fields,
      } as never,
    } as never);
  });
}
async function mountAt(hash: string): Promise<void> {
  win.location.hash = hash;
  resetRouterCacheForTests();
  await act(async () => { root.unmount(); });
  root = createRoot(container);
  await act(async () => { root.render(createElement(FlowsScreen as any)); });
  await settle();
}

await test("while the slice asks again the list says so, with no error and no RETRY", async () => {
  seed({ libraryRetry: { attempt: 3, of: 4 } });
  await mountAt("#/session/flows");
  const line = tid("flows-retrying");
  assert(line != null, "no flows-retrying line while libraryRetry is set");
  assert(/No answer from the rig yet\. Asking again by itself \(try 3 of 4\)\./.test(line.textContent),
    `the retrying line reads "${line.textContent}"`);
  assert(tid("flows-retry") == null, "a RETRY button while the app is already asking again");
  assert(tid("flows-error") == null, "the error shows while the app is still asking");
});

await test("once the tries are spent the error and RETRY show", async () => {
  seed({ libraryLoadError: "request timed out — server not responding" });
  await mountAt("#/session/flows");
  assert(tid("flows-retrying") == null, "a retrying line after the tries are spent");
  assert(tid("flows-retry") != null, "no RETRY once the tries are spent");
});

await test("FSG + NL the mount asks once per try, and a failed load is asked once more when the tab comes back", async () => {
  setRetrySleepForTests(async () => {});
  try {
    libraryMode = "timeout";
    flowsGets = 0;
    seed({});
    await mountAt("#/session/flows");
    assert(flowsGets === 4, `the mount asked the library ${flowsGets} times through four tries, not 4`);
    assert(tid("flows-retry") != null, "precondition: the load settled as failed, with RETRY");
    libraryMode = "hold";
    flowsGets = 0;
    await act(async () => { document.dispatchEvent(new win.Event("visibilitychange")); });
    await settle();
    assert(flowsGets === 1, `the tab coming back asked ${flowsGets} times, not once`);
  } finally {
    libraryMode = "hold";
    setRetrySleepForTests(null);
  }
});

// #877: an open's or a save's failure (`libraryError`) is its own line, beside
// the load's (`libraryLoadError`, or the retrying line) and never behind it.
const OPEN_REASON = "no flow named gone";
const LOAD_REASON = "request timed out — server not responding";

await test("#877 an open's failure shows beside the retrying line, not behind it", async () => {
  seed({ libraryRetry: { attempt: 2, of: 4 }, libraryError: OPEN_REASON });
  await mountAt("#/session/flows");
  assert(tid("flows-retrying") != null, "no flows-retrying line while libraryRetry is set");
  const line = tid("flows-action-error");
  assert(line != null, "the open's failure is hidden while the load retries");
  assert(line.textContent.includes(OPEN_REASON), `the line reads "${line.textContent}"`);
  assert(tid("flows-error") == null, "the open's failure was labelled as the library's");
});

await test("#877 a failed load and a failed open are two lines, each with its own reason", async () => {
  seed({ libraryLoadError: LOAD_REASON, libraryError: OPEN_REASON });
  await mountAt("#/session/flows");
  const load = tid("flows-error");
  assert(load != null, "no load-failure line");
  assert(load.textContent.includes(LOAD_REASON), `the load line reads "${load.textContent}"`);
  assert(!load.textContent.includes(OPEN_REASON), "the open's reason shows as the library's");
  assert(tid("flows-retry") != null, "no RETRY for the failed load");
  const open = tid("flows-action-error");
  assert(open != null, "no open-failure line");
  assert(open.textContent.includes(OPEN_REASON), `the open line reads "${open.textContent}"`);
  assert(!open.textContent.includes(LOAD_REASON), "the load's reason shows as the open's");
});

await test("#877 an open's failure alone is not reported as an unreadable library", async () => {
  seed({ libraryError: OPEN_REASON });
  await mountAt("#/session/flows");
  assert(tid("flows-action-error") != null, "no open-failure line");
  assert(tid("flows-error") == null, "an open's failure was reported as 'could not read the flow library'");
  assert(tid("flows-retry") == null, "a RETRY for a library that did not fail");
});

// #919: the open's or save's failure line can be taken down. Nothing else does
// it between opens, so a stale failure sat under a healthy list.
const press = (el: any): void => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};

await test("#919 DISMISS takes the open's failure line down and leaves the load's line and RETRY", async () => {
  seed({ libraryLoadError: LOAD_REASON, libraryError: OPEN_REASON });
  await mountAt("#/session/flows");
  const dismiss = tid("flows-action-error-dismiss");
  assert(dismiss != null, "the open-failure line has no DISMISS");
  press(dismiss);
  await settle();
  assert(useStore.getState().flows.libraryError === null, "DISMISS left the open's reason in the store");
  assert(tid("flows-action-error") == null, "the open-failure line is still up after DISMISS");
  assert(tid("flows-action-error-dismiss") == null, "DISMISS is still up after it was pressed");
  assert(tid("flows-error") != null && tid("flows-retry") != null,
    "DISMISS took the load's failure line, or its RETRY, down with it");
  assert(useStore.getState().flows.libraryLoadError === LOAD_REASON, "DISMISS cleared the load's failure");
});

await test("#919 there is no DISMISS when there is no open or save failure to dismiss", async () => {
  seed({ libraryLoadError: LOAD_REASON });
  await mountAt("#/session/flows");
  assert(tid("flows-action-error") == null, "an open-failure line with no open failure");
  assert(tid("flows-action-error-dismiss") == null, "a DISMISS with nothing to dismiss");
});

await act(async () => { root.unmount(); });

console.log(`p6FlowsScreenRetrying.test: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  process.exitCode = 1;
}
