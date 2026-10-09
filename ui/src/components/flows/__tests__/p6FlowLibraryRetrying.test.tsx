// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// p6FlowLibraryRetrying.test.tsx -- the classic flow library says it is asking
// again, and a re-ask never stacks on a load in flight (#859).
//
//   Run directly:  npx tsx src/components/flows/__tests__/p6FlowLibraryRetrying.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Harness copied from flowLibraryDom.test.tsx. The library GET is HELD on a
// promise that never settles, so the mount's own load stays in flight and the
// store fields under test are the ones this file wrote.
//
// Named mutants (each turns the case beside it red):
//   (a) FlowLibrary renders the error block only (the retrying branch deleted).
//   (b) drop `!f.libraryLoading` from libraryFailedIn (each visibilitychange
//       starts another GET while one is out).
//   (c) UX8: delete FlowLibrary's useRetryOnReturn (a load that settled as
//       failed is never re-asked when the tab comes back).

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

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "getComputedStyle", "matchMedia",
  "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
installAutoRaf(g);
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- imports
import { installAutoRaf } from "../../../testing/rafPolyfill";
import React from "react";
import { createRoot } from "react-dom/client";
import { act } from "react";

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }

const { useStore } = await import("../../../store");
const { api, ApiError } = await import("../../../api");
const { setRetrySleepForTests } = await import("../../../lib/retryLoad");
const { FlowLibrary } = await import("../FlowLibrary");

// ------------------------------------------------------------------- harness
const FOLDERS = [{ name: "My flows", count: 0, readonly: false }];
let asked: string[] = [];
/** "hold": every library GET is held, so the mount's load never settles.
 *  "timeout": every GET times out the way api.ts reports an AbortSignal. */
let getMode: "hold" | "timeout" = "hold";
(api as any).get = (path: string) => {
  asked.push(path);
  if (getMode === "timeout") {
    return Promise.reject(new ApiError("request timed out — server not responding", 0, true));
  }
  return new Promise(() => { /* held */ });
};
const settle = async () => {
  for (let i = 0; i < 8; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const flowsGets = () => asked.filter((p) => p === "/api/flows").length;

function seed(fields: Record<string, unknown>) {
  unmount();
  asked = [];
  const st = useStore.getState() as any;
  useStore.setState({
    flows: {
      ...st.flows, cards: [], folders: FOLDERS, libraryLoaded: false,
      libraryError: null, libraryRetry: null, libraryLoading: false,
      ui: { ...st.flows.ui, query: "", folderChip: "all", highlightId: null },
      ...fields,
    },
    principal: { role: "admin", email: "t@t", caps: ["control.capture"] },
  } as any);
}

let root: ReturnType<typeof createRoot> | null = null;
function unmount() {
  if (!root) return;
  const r = root;
  root = null;
  act(() => { r.unmount(); });
}
async function mount() {
  const host = document.getElementById("root")!;
  host.innerHTML = "";
  root = createRoot(host);
  await act(async () => { root!.render(React.createElement(FlowLibrary)); });
  return host;
}
const retryButton = () => Array.from(document.querySelectorAll("button"))
  .find((b) => (b.textContent || "").trim() === "RETRY");

// --------------------------------------------------------------------- tests
await test("(a) while it asks again the retrying line shows and RETRY does not", async () => {
  seed({ libraryRetry: { attempt: 2, of: 4 } });
  await mount();
  const line = document.querySelector("[data-flows-retrying]");
  assert(line, "no retrying line while libraryRetry is set");
  assert(/No answer from the rig yet\. Asking again by itself \(try 2 of 4\)\./.test(line!.textContent || ""),
    `the retrying line reads "${line!.textContent}"`);
  assert(!retryButton(), "a RETRY button while the app is already asking again");
  assert(!/Could not read the flow library/.test(document.body.textContent || ""),
    "the error shows while the app is still asking");

  seed({ libraryError: "request timed out — server not responding" });
  await mount();
  assert(!document.querySelector("[data-flows-retrying]"), "a retrying line after the tries are spent");
  assert(retryButton(), "no RETRY once the tries are spent");
});

await test("(b) a re-ask never stacks on a load in flight", async () => {
  seed({ libraryError: "request timed out — server not responding" });
  await mount();
  assert(flowsGets() === 1, `precondition: the mount asked once (asked ${flowsGets()})`);
  assert(useStore.getState().flows.libraryLoading, "precondition: the mount's load is in flight");
  await act(async () => { document.dispatchEvent(new win.Event("visibilitychange")); });
  await act(async () => { document.dispatchEvent(new win.Event("visibilitychange")); });
  assert(flowsGets() === 1, `two tab-visible events while a load is out asked ${flowsGets()} times`);
});

await test("(c) a load that settled as failed is asked once more when the tab comes back", async () => {
  setRetrySleepForTests(async () => {});
  try {
    getMode = "timeout";
    seed({});
    await mount();
    await settle();
    assert(flowsGets() === 4, `precondition: the mount's load tried four times (asked ${flowsGets()})`);
    const f = useStore.getState().flows;
    assert(!!f.libraryError && !f.libraryLoading && !f.libraryLoaded,
      "precondition: the load settled as failed");
    getMode = "hold";
    asked = [];
    await act(async () => { document.dispatchEvent(new win.Event("visibilitychange")); });
    assert(flowsGets() === 1, `the tab coming back asked ${flowsGets()} times, not once`);
  } finally {
    getMode = "hold";
    setRetrySleepForTests(null);
  }
});

unmount();

// -------------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\np6FlowLibraryRetrying.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
  process.exitCode = 1;
}
