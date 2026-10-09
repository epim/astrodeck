// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// retryOnReturn.test.tsx -- a failed one-shot load is asked once more when the
// websocket comes back up or the tab becomes visible (#859).
//
//   Run directly:  npx tsx src/lib/__tests__/retryOnReturn.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The REAL hook in a tiny component; `isFailed` reads a test-owned variable,
// so the gate can change without a render (H4).
//
// Named mutants (each turns the case beside it red):
//   H1   drop `connected &&` from the websocket effect (the first render with
//        connected=false calls too).
//   H1b  drop `!before &&` (fires on the mount run).
//   H2   drop `failedRef.current() &&` in both handlers.
//   H3   remove the visibilitychange listener.
//   H4   the hook caches the boolean at render and the handlers read it.

/* eslint-disable @typescript-eslint/no-explicit-any */
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(`<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true });
const win = dom.window as any;
const g = globalThis as any;
for (const k of ["window", "document", "navigator", "HTMLElement", "Element", "Node", "Event"]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const React = (await import("react")).default;
const { act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useRetryOnReturn } = await import("../retryLoad");

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq(a: unknown, b: unknown, msg: string): void {
  if (a !== b) throw new Error(`${msg}\n  expected ${String(b)}\n  got      ${String(a)}`);
}

let isFailedNow = false;
let calls = 0;
function Probe({ connected }: { connected: boolean }) {
  useRetryOnReturn(() => isFailedNow, () => { calls++; }, connected);
  return null;
}

let root: ReturnType<typeof createRoot> | null = null;
async function render(connected: boolean): Promise<void> {
  if (!root) root = createRoot(document.getElementById("root")!);
  await act(async () => { root!.render(React.createElement(Probe, { connected })); });
}
async function unmount(): Promise<void> {
  if (!root) return;
  const r = root;
  root = null;
  await act(async () => { r.unmount(); });
}
async function becomeVisible(): Promise<void> {
  await act(async () => { document.dispatchEvent(new win.Event("visibilitychange")); });
}

await test("H1 the websocket coming up re-asks a failed load once", async () => {
  await unmount();
  isFailedNow = true;
  calls = 0;
  await render(false);
  await render(true);
  eq(calls, 1, "one re-ask when the socket came up");
  await render(true);
  eq(calls, 1, "a re-render with the socket still up is not another reconnect");
});

await test("H1b mounting with the socket already up is not a reconnect", async () => {
  await unmount();
  isFailedNow = true;
  calls = 0;
  await render(true);
  eq(calls, 0, "the mount run must not re-ask");
});

await test("H2 nothing is re-asked while the load has not failed", async () => {
  await unmount();
  isFailedNow = false;
  calls = 0;
  await render(false);
  await render(true);
  await becomeVisible();
  eq(calls, 0, "not failed: no re-ask on either event");
});

await test("H3 the tab becoming visible re-asks", async () => {
  await unmount();
  isFailedNow = true;
  calls = 0;
  await render(true);
  eq(document.visibilityState, "visible", "precondition: jsdom reports visible");
  await becomeVisible();
  eq(calls, 1, "one re-ask on visibilitychange");
});

await test("H4 the gate is read at event time, not at render", async () => {
  await unmount();
  isFailedNow = false;
  calls = 0;
  await render(true);
  isFailedNow = true; // no re-render
  await becomeVisible();
  eq(calls, 1, "the gate must be read when the event fires");
});

await unmount();

console.log(`retryOnReturn.test: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  process.exitCode = 1;
}
