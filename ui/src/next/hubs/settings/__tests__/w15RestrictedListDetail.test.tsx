// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w15RestrictedListDetail.test.tsx - the #/next Credits list says WHERE the
// Player One SDK came from when the server says so (#705; WP-116, finished at
// the wave 15 integration), as the classic panel does
// (components/__tests__/w15RestrictedAssetsDetail.test.tsx).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/settings/__tests__/w15RestrictedListDetail.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// `GET /api/licensing/restricted` returns a `detail` clause on every row. For a
// Player One SDK installed by the vendor's own installer it is "installed by the
// vendor's installer (redistribution ruling pending, #632)": the SDK is in use
// but whether a system install counts as "fetched" is the owner's open
// question, and the row is the one place that says so. `RestrictedAsset` did
// not carry the field and `RestrictedList` did not print it.
//
// MUTANT "detail not rendered" (RestrictedList.tsx: the `{a.detail && (<p ...
// data-testid="restricted-detail">` block removed). Observed, "1/2 passed", from
// a byte backup restored byte-identically (sha256 compared):
//   x the vendor-installed Player One row says where its SDK came from: the row
//     does not say where the SDK came from: "IN USEPlayer One camera SDKfetched,
//     never shippedthe licensor's wordsour readingGet it from
//     https://example.invalid/sdk"
// MUTANT "detail printed on every row" (the `a.detail &&` guard made `true`).
// Observed, "1/2 passed":
//   x a row with no detail prints no empty detail element: the DSS2 row prints a
//     detail element though the server sent null

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
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "Image", "localStorage", "getComputedStyle", "matchMedia",
  "WebSocket", "requestAnimationFrame", "cancelAnimationFrame", "ResizeObserver",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const INSTALLER_DETAIL =
  "installed by the vendor's installer (redistribution ruling pending, #632)";

const ROWS: any[] = [
  {
    id: "playerone", title: "Player One camera SDK", quote: "the licensor's words",
    reading: "our reading", remedy: "fetch", source: "https://example.invalid/sdk",
    without: "Player One cameras do not open", satisfied: true,
    detail: INSTALLER_DETAIL, consent: null,
  },
  {
    id: "dss2", title: "DSS2 sky survey imagery", quote: "the licensor's words",
    reading: "our reading", remedy: "fetch", source: "https://example.invalid/dss2",
    without: "the survey is blank", satisfied: true,
    detail: null, consent: null,
  },
];

const ok = (json: any) => ({ ok: true, status: 200, statusText: "OK", json: async () => json });
g.fetch = async (url: string) => {
  if (String(url).includes("/api/licensing/restricted")) return ok({ assets: ROWS });
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const RestrictedList = (await import("../tuning/system/RestrictedList")).default;

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
const settle = async () => {
  for (let i = 0; i < 4; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
act(() => {
  useStore.setState({
    principal: { role: "admin", email: "a@rig", caps: ["view.status", "config.backend"] },
    wsPhase: "up", toasts: [],
  } as never);
});
await act(async () => { root.render(createElement(RestrictedList as any)); });
await settle();

const rowOf = (id: string): any => container.querySelector(`[data-restricted-asset="${id}"]`);

await testAsync("the vendor-installed Player One row says where its SDK came from", async () => {
  const r = rowOf("playerone");
  assert(r != null, "the Player One row did not render: the fixture is wrong");
  assert(String(r.textContent).includes("IN USE"), "premise: the SDK reads in use");
  const detail = r.querySelector('[data-testid="restricted-detail"]');
  assert(detail != null,
    `the row does not say where the SDK came from: "${String(r.textContent).slice(0, 160)}"`);
  assert(detail.textContent === INSTALLER_DETAIL,
    `the detail is not the server's sentence: "${detail.textContent}"`);
});

await testAsync("a row with no detail prints no empty detail element", async () => {
  const r = rowOf("dss2");
  assert(r != null, "the DSS2 row did not render: the fixture is wrong");
  assert(r.querySelector('[data-testid="restricted-detail"]') == null,
    "the DSS2 row prints a detail element though the server sent null");
});

await act(async () => { root.unmount(); });

const total = passed + failed;
console.log(`w15RestrictedListDetail.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
