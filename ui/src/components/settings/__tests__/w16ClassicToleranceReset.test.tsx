// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w16ClassicToleranceReset.test.tsx - the rotator bin on the CLASSIC Matching
// and stacking panel (#176 wave 16 integration; #826, owner ruling D-I,
// consolidation WP 3.9). Mounted: the real CalibrationTolerancesPanel.
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/settings/__tests__/w16ClassicToleranceReset.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// HISTORY. WP-122 added `calibration.rotator_bin_deg` to the stored block and
// the panel carried it through a SAVE unseen, so RESET had to keep it (a RESET
// that wrote the five-field defaults dropped the key and the server put the
// bin back to its default). It now has a control, the sixth number on the
// panel, so RESET puts it back with the other five and the panel shows what it
// posts.
//
// WHAT IS GUARDED
//
//   1. The stored rotator bin is on screen, and an edit to it is what SAVE
//      posts.
//   2. RESET puts all six back, the rotator bin included.
//   3. A block from a server that sends no rotator bin shows the default (2).
//   4. A bin narrower than the matcher's 1 degree warns and disables SAVE; 0
//      (no binning) does neither.
//
// NAMED MUTANTS (from a byte backup of CalibrationTolerancesPanel.tsx,
// restored and sha256-compared):
//   "RESET keeps the stored bin" (the `onClick` made `setDraft((d) => ({
//   ...DEFAULTS, rotator_bin_deg: d.rotator_bin_deg }))`): x RESET puts all six
//   tolerances back, the rotator bin with them: RESET left the rotator bin it
//   shows at the stored number (expected 2, got 7)
//   "rotator rule dropped" (`rotatorBinTooNarrow` made false): x a rotator bin
//   narrower than the matcher's 1 degree warns and disables SAVE: no warning

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
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement", "HTMLButtonElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "requestAnimationFrame", "cancelAnimationFrame",
  "getComputedStyle", "matchMedia", "WebSocket",
]) {
  const v = win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const posts: { url: string; body: any }[] = [];
g.fetch = async (url: any, init: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  if (method === "POST") posts.push({ url: u, body: init?.body ? JSON.parse(init.body) : null });
  return {
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => ({}),
    text: async () => "{}",
  };
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const { default: CalibrationTolerancesPanel } = await import("../CalibrationTolerancesPanel");

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const tick = () => new Promise((r) => setTimeout(r, 0));
const settle = async () => { for (let i = 0; i < 6; i++) await act(async () => { await tick(); }); };
const button = (label: string): any =>
  (Array.from(container.querySelectorAll("button")) as any[])
    .find((b) => (b.textContent ?? "").trim().includes(label));
const press = async (label: string) => {
  const b = button(label);
  if (!b) throw new Error(`no "${label}" button on the panel`);
  if (b.disabled) throw new Error(`the "${label}" button is disabled`);
  await act(async () => {
    b.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
    for (let i = 0; i < 6; i++) await tick();
  });
};

const stateOf = (calibration: Record<string, number>) => act(async () => {
  useStore.setState({
    principal: { role: "admin", email: "admin@example.test", caps: ["config.site_optics"] },
    authGate: "open",
    config: { calibration } as never,
  } as never);
});
const BLOCK = { exposure_tol_pct: 5, temp_tol_c: 2, temp_bin_c: 5, stack_sigma: 3, max_stack_frames: 100 };
const mountPanel = async () => {
  await act(async () => { root.render(createElement(CalibrationTolerancesPanel)); });
  await settle();
};
const rotatorBox = (): any => {
  const label = (Array.from(container.querySelectorAll("label")) as any[])
    .find((l) => (l.textContent ?? "").includes("Rotator angle bin"));
  if (!label) throw new Error("no Rotator angle bin field on the classic panel");
  return label.querySelector("input");
};
const typeInto = async (el: any, text: string) => {
  const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!;
  await act(async () => {
    setter.call(el, text);
    el.dispatchEvent(new win.Event("input", { bubbles: true }));
    for (let i = 0; i < 3; i++) await tick();
  });
};
const savePosted = () => posts.find((p) => p.url.includes("/api/config/calibration"))?.body;

await test("the stored rotator bin is on screen, and an edit to it is what SAVE posts", async () => {
  posts.length = 0;
  await stateOf({ ...BLOCK, rotator_bin_deg: 7 });
  await mountPanel();
  eq(rotatorBox().value, "7", "the stored rotator bin is not the number on screen");
  await typeInto(rotatorBox(), "4");
  await press("Save");
  const body = savePosted();
  if (!body) throw new Error("SAVE posted nothing to /api/config/calibration");
  eq(body.rotator_bin_deg, 4, "SAVE sent a different rotator bin from the one on screen");
  eq(body.temp_tol_c, 2, "SAVE dropped a field the panel did not change");
});

await test("RESET puts all six tolerances back, the rotator bin with them", async () => {
  posts.length = 0;
  await stateOf({ ...BLOCK, temp_tol_c: 4, rotator_bin_deg: 7 });
  await mountPanel();
  await press("Reset to defaults");
  await press("Save");
  const body = savePosted();
  if (!body) throw new Error("SAVE posted nothing to /api/config/calibration");
  eq(body.temp_tol_c, 2, "RESET did not put the tolerance back to its default");
  eq(body.rotator_bin_deg, 2, "RESET left the rotator bin it shows at the stored number");
});

await test("a block with no rotator bin shows the default", async () => {
  posts.length = 0;
  await stateOf({ ...BLOCK, temp_tol_c: 4 });
  await mountPanel();
  eq(rotatorBox().value, "2", "a block with no rotator bin shows no default");
});

await test("a rotator bin narrower than the matcher's 1 degree warns and disables SAVE, and 0 does neither", async () => {
  posts.length = 0;
  await stateOf({ ...BLOCK, rotator_bin_deg: 2 });
  await mountPanel();
  await typeInto(rotatorBox(), "0.5");
  if (!String(container.textContent).includes("narrower than the match angle tolerance")) {
    throw new Error("a rotator bin of 0.5 raised no warning");
  }
  eq(button("Save").disabled, true, "SAVE is live with a rotator bin narrower than the matcher's tolerance");
  await typeInto(rotatorBox(), "0");
  eq(String(container.textContent).includes("narrower than the match angle tolerance"), false,
    "a bin of 0 (no binning) raised the warning");
  eq(button("Save").disabled, false, "SAVE is locked for a bin of 0");
});

await act(async () => { root.unmount(); });
const total = passed + failed;
console.log(`w16ClassicToleranceReset.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export const result = { passed, failed, total };
export default result;
