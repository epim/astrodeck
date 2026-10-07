// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w15RestrictedAssetsDetail.test.tsx - the Credits row says WHERE the Player One
// SDK came from when the server says so (#705; WP-116, finished at the wave 15
// integration).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/__tests__/w15RestrictedAssetsDetail.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. `GET /api/licensing/restricted` returns, on every row, a `detail`
// clause: for the Player One SDK installed by the vendor's own installer it is
// "installed by the vendor's installer (redistribution ruling pending, #632)".
// The SDK IS in use (the camera opens from it, so the LED reads "in use"), but
// whether a system install counts as "fetched" is still the owner's question,
// and the row is the one place that says so. The panel's interface did not
// carry the field and its row did not print it, so the server wrote a sentence
// nobody could read: the row said "in use" and nothing of the open question.
//
// WHAT IS GUARDED
//   1. The sentence is printed beside the row's title and the "in use" LED, for
//      the row that has one, verbatim.
//   2. A row with `detail: null` (every other row, and every row of a server
//      that sends none) prints no empty element: the row looks as it did.
//   3. The sentence goes with the ROW: a second row without one does not borrow it.
//
// MUTANT "detail not rendered" (RestrictedAssetsPanel.tsx: the
// `{a.detail && (<span ... data-restricted-detail ...>` block removed).
// Observed, "1/3 passed", from a byte backup restored byte-identically (sha256
// compared):
//   x the vendor-installed Player One row says where its SDK came from: the row
//     does not say where the SDK came from: "Player One camera SDKfetched, never
//     shippedthe licensor's wordsour readingGet it from
//     https://example.invalid/sdk"
// MUTANT "detail printed on every row" (the `a.detail &&` guard made `true`).
// Observed, "1/3 passed":
//   x a row with no detail prints no empty detail element: the dss2 row prints
//     a detail element though the server sent null

/* eslint-disable @typescript-eslint/no-explicit-any */

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
  "FocusEvent", "PointerEvent", "Image", "localStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "requestAnimationFrame", "cancelAnimationFrame",
  "ResizeObserver",
]) {
  const v = k === "window" ? win : win[k];
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
  {
    id: "astrospheric", title: "Astrospheric", quote: "the licensor's words",
    reading: "our reading", remedy: "acknowledge", source: "https://example.invalid/a",
    without: "the forecast stays off", satisfied: false,
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
const { useStore } = await import("../../store");
const RestrictedAssetsPanel = (await import("../settings/RestrictedAssetsPanel")).default;

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
await act(async () => { root.render(createElement(RestrictedAssetsPanel as any)); });
await settle();

const row = (id: string): any => container.querySelector(`[data-restricted-asset="${id}"]`);

await testAsync("the vendor-installed Player One row says where its SDK came from", async () => {
  const r = row("playerone");
  assert(r != null, "the Player One row did not render: the fixture is wrong");
  assert(String(r.textContent).includes("Player One camera SDK"), "premise: this is the Player One row");
  // The LED's own word is the label, so "in use" is checked on the LED.
  const led = r.querySelector('[role="img"]');
  assert(led != null && led.getAttribute("aria-label") === "in use",
    "premise: the SDK reads in use, so what the row has to add is the open question");
  const detail = r.querySelector("[data-restricted-detail]");
  assert(detail != null,
    `the row does not say where the SDK came from: "${String(r.textContent).slice(0, 160)}"`);
  assert(detail.textContent === INSTALLER_DETAIL,
    `the detail is not the server's sentence: "${detail.textContent}"`);
});

await testAsync("a row with no detail prints no empty detail element", async () => {
  for (const id of ["dss2", "astrospheric"]) {
    const r = row(id);
    assert(r != null, `the ${id} row did not render: the fixture is wrong`);
    assert(r.querySelector("[data-restricted-detail]") == null,
      `the ${id} row prints a detail element though the server sent null`);
  }
});

await testAsync("the sentence belongs to its row and to no other", async () => {
  const all = container.querySelectorAll("[data-restricted-detail]");
  assert(all.length === 1, `${all.length} detail elements for one detail`);
  assert(String(container.textContent).split(INSTALLER_DETAIL).length === 2,
    "the sentence is printed more than once");
});

await act(async () => { root.unmount(); });

const total = passed + failed;
console.log(`w15RestrictedAssetsDetail.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
