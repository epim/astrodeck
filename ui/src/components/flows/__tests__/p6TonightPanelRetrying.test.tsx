// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// p6TonightPanelRetrying.test.tsx -- the classic Tonight panel says it is
// asking again while `flowsFetchTonight` waits to retry its read (#859).
//
//   Run directly:  npx tsx src/components/flows/__tests__/p6TonightPanelRetrying.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The classic panel and the #/next sheet share `flowsFetchTonight`, which now
// asks a timed-out read again after 2 s, 5 s and 15 s. Without this line the
// panel showed "Resolving tonight" for up to 82 s where it used to show the
// error after 15 s. Harness cut down from tonightPanelDom.test.tsx: the store
// is seeded and `flowsFetchTonight` replaced by a no-op, because this file is
// about what renders.
//
// Named mutant: TP  the panel's loading branch ignores `tonightRetry` (the
// plain "Resolving tonight" line shows while the read waits to ask again).

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;
win.matchMedia = () => ({
  matches: true, addEventListener() {}, removeEventListener() {},
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
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }

const { useStore } = await import("../../../store");
const { FLOWS_INIT } = await import("../flowsSlice");
const TonightPanel = (await import("../TonightPanel")).default;

const root = createRoot(win.document.getElementById("root"));

function render(flows: Record<string, unknown>): void {
  act(() => root.render(null));
  act(() => {
    useStore.setState({
      flows: {
        ...FLOWS_INIT,
        record: {
          id: "quick-m31", name: "QUICK M31", folder: "My flows", tagline: "",
          graph: { nodes: [], edges: [] }, created_ts: 0, updated_ts: 0,
          last_run: null, last_result: "", readonly: false,
        },
        ui: { ...FLOWS_INIT.ui, tonightOpen: true, tonightTab: "timeline" },
        ...flows,
      },
      flowsFetchTonight: async () => { /* this file is about what renders */ },
    } as any);
  });
  act(() => root.render(React.createElement(TonightPanel)));
}
/** The overlay renders through a body-level portal. */
const bodyText = (): string => (win.document.body.textContent ?? "") as string;

test("TP while the read waits to ask again the panel says so", () => {
  render({ tonight: null, tonightLoading: true, tonightRetry: { attempt: 2, of: 4 } });
  assert(/No answer from the rig yet\. Asking again by itself \(try 2 of 4\)\./.test(bodyText()),
    "the panel does not say it is asking again");
  assert(!/Resolving tonight/.test(bodyText()), "the plain loading line shows while the read waits to retry");
});

test("TP without a retry the loading line is unchanged", () => {
  render({ tonight: null, tonightLoading: true, tonightRetry: null });
  assert(/Resolving tonight/.test(bodyText()), "the loading line is gone");
  assert(!/Asking again by itself/.test(bodyText()), "a retrying line with no retry");
});

act(() => root.render(null));
act(() => root.unmount());

console.log(`p6TonightPanelRetrying.test: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  process.exitCode = 1;
}
