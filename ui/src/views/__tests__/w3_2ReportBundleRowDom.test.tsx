// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w3_2ReportBundleRowDom.test.tsx - the classic report's stacking-bundle rows
// name the mosaic and panel of a mosaic's group (WP 3.2, #830, #188).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/views/__tests__/w3_2ReportBundleRowDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The server's bundle preview carries `mosaic` and `panel` on each group row
// (`bundle_summary`), and a stack that is one panel of a mosaic is stitched,
// never co-added: its row says so. A group that is no panel's reads as before.
//
// MUTANT "row ignores the mosaic" (ReportView.tsx: the `if (g.mosaic)` push
// in `BundleGroupRow` removed): the mosaic row case reds, quoted below.

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

type Pending = { url: string; ok: (b: any) => void };
const pending: Pending[] = [];
function answer(match: string, body: any): void {
  const i = pending.findIndex((p) => p.url.includes(match));
  if (i < 0) throw new Error(`nothing in flight matching "${match}" (have: ${pending.map((p) => p.url).join(", ")})`);
  const [p] = pending.splice(i, 1);
  p.ok(body);
}
const inFlight = (match: string) => pending.some((p) => p.url.includes(match));

win.fetch = (url: string) =>
  new Promise((resolve) => {
    pending.push({
      url: String(url),
      ok: (body: any) => resolve({ ok: true, status: 200, statusText: "OK", json: () => Promise.resolve(body) }),
    });
  });

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "requestAnimationFrame", "cancelAnimationFrame", "fetch",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const ReportView = (await import("../ReportView")).default;

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
async function flush(): Promise<void> {
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

const ID = "Veil-20260805-203000";
const REPORT = {
  id: ID, plan_name: "Veil", started_at: 1_754_000_000, ended_at: 1_754_020_000,
  end_reason: "complete", frames_captured: 9, frames_rejected: 0,
  integration_s: 9 * 300,
  by_filter: [{ filter: "Ha", frames: 9, rejected: 0, integration_s: 2700, hfr_median: 2.2 }],
  targets: [], safety_events: [], trends: { hfr: [], temp: [], rms: [] },
};
const GROUP = {
  filter: "Ha", exposure_s: 300, gain: 100, binning: 1, light_count: 3,
  accepted_count: 3, kept_count: 3, masters: { dark: true, flat: true, bias: false },
};
const BUNDLE = {
  report_id: ID, plan_name: "Veil", layout: "grouped", weight_altitude: false,
  keep_threshold: null, warnings: [],
  groups: [
    { ...GROUP, dir: "Veil/Veil 1-2/Ha/300s_g100_bin1", target: "Veil 1-2",
      mosaic: "Veil", panel: "1-2", mosaic_id: "group-a", panel_id: "panel-a" },
    { ...GROUP, dir: "M31/Ha/300s_g100_bin1", target: "M31",
      mosaic: null, panel: null, mosaic_id: null, panel_id: null },
  ],
};

act(() => {
  useStore.setState({
    principal: { role: "admin", email: null, caps: ["view.status", "control.capture"] },
  } as never);
});
const container = win.document.getElementById("root") as any;
const root = createRoot(container);
act(() => { root.render(createElement(ReportView)); });

async function scenario(): Promise<void> {
  answer("/api/reports", [{
    id: ID, plan_name: "Veil", started_at: REPORT.started_at, ended_at: REPORT.ended_at,
    end_reason: "complete", frames_captured: 9, frames_rejected: 0,
    integration_s: REPORT.integration_s,
  }]);
  await flush();
  answer(`/api/reports/${encodeURIComponent(ID)}`, REPORT);
  await flush();
  if (inFlight("bundle")) answer("bundle", BUNDLE);
  await flush();

  const text = (container.textContent as string).replace(/\s+/g, " ");

  test("the bundle preview is on screen", () => {
    assert(/Stacking bundle/.test(text), `no Stacking bundle panel: ${text.slice(0, 200)}`);
    assert(/M31 \u00b7 Ha \u00b7 300s/.test(text), "the plain group is not listed");
  });

  test("a mosaic panel's group row names its mosaic and its place", () => {
    assert(/Veil 1-2 \u00b7 Ha \u00b7 300s \u00b7 g100 \u00b7 bin1 \u00b7 mosaic Veil panel 1-2/.test(text),
      `the mosaic row reads: ${text.slice(text.indexOf("Veil 1-2"), text.indexOf("Veil 1-2") + 90)}`);
  });

  test("a group that is no panel's reads as before", () => {
    assert(/M31 \u00b7 Ha \u00b7 300s \u00b7 g100 \u00b7 bin1(?! \u00b7 mosaic)/.test(text), "the plain row gained a mosaic clause");
  });
}

await scenario().catch((e: unknown) => {
  failed++;
  failures.push(`x the scenario could not be driven to the end: ${(e as Error).message}`);
});

act(() => { root.unmount(); });
const total = passed + failed;
console.log(`w3_2ReportBundleRowDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
