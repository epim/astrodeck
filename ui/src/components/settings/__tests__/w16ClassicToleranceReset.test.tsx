// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w16ClassicToleranceReset.test.tsx - "Reset to defaults" on the CLASSIC
// Matching and stacking panel keeps the stored rotator bin (#176; wave 16
// integration, verifier). Mounted: the real CalibrationTolerancesPanel.
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/settings/__tests__/w16ClassicToleranceReset.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE GAP. WP-122 added `calibration.rotator_bin_deg` to the stored block and
// the integration made both editors carry it through a SAVE unseen (neither
// has a control for it). RESET wrote the five-field `DEFAULTS` over the
// draft, which dropped the key, so the SAVE after a RESET sent none and the
// server put the rotator bin back to its own default: a hidden setting
// changed by a button that names five others.
//
// WHAT IS GUARDED
//
//   1. RESET puts the five tolerances back to their defaults.
//   2. The SAVE after it posts the stored `rotator_bin_deg` unchanged.
//
// NAMED MUTANT (from a byte backup of CalibrationTolerancesPanel.tsx,
// restored and sha256-compared): "RESET writes the bare defaults" (the
// `onClick` made `setDraft({ ...DEFAULTS })`). RED, 1/2 passed:
//   x RESET puts the five tolerances back and keeps the stored rotator bin:
//     SAVE after RESET dropped the stored rotator bin, so the server would
//     reset it to its default (expected 7, got undefined)

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

await test("RESET puts the five tolerances back and keeps the stored rotator bin", async () => {
  posts.length = 0;
  await act(async () => {
    useStore.setState({
      principal: { role: "admin", email: "admin@example.test", caps: ["config.site_optics"] },
      authGate: "open",
      config: { calibration: {
        exposure_tol_pct: 5, temp_tol_c: 4, temp_bin_c: 5, rotator_bin_deg: 7,
        stack_sigma: 3, max_stack_frames: 100,
      } } as never,
    } as never);
  });
  await act(async () => { root.render(createElement(CalibrationTolerancesPanel)); });
  await settle();
  await press("Reset to defaults");
  await press("Save");
  const body = posts.find((p) => p.url.includes("/api/config/calibration"))?.body;
  if (!body) throw new Error("SAVE posted nothing to /api/config/calibration");
  eq(body.temp_tol_c, 2, "RESET did not put the tolerance back to its default");
  eq(body.rotator_bin_deg, 7,
    "SAVE after RESET dropped the stored rotator bin, so the server would reset it to its default");
});

await test("RESET on a block that carries no rotator bin posts none either", async () => {
  // CONTROL: an older server sends no `rotator_bin_deg`; the panel invents none.
  posts.length = 0;
  await act(async () => {
    useStore.setState({
      config: { calibration: {
        exposure_tol_pct: 5, temp_tol_c: 4, temp_bin_c: 5, stack_sigma: 3, max_stack_frames: 100,
      } } as never,
    } as never);
  });
  await settle();
  await press("Reset to defaults");
  await press("Save");
  const body = posts.find((p) => p.url.includes("/api/config/calibration"))?.body;
  if (!body) throw new Error("SAVE posted nothing to /api/config/calibration");
  eq(body.temp_tol_c, 2, "RESET did not put the tolerance back to its default");
  eq("rotator_bin_deg" in body, false, "the panel invented a rotator bin the server never sent");
});

await act(async () => { root.unmount(); });
const total = passed + failed;
console.log(`w16ClassicToleranceReset.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export const result = { passed, failed, total };
export default result;
