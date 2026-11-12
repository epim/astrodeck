// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w11_6BundleRowsTellMosaicsApart.test.tsx - two mosaics with one name read as
// two different bundle rows in both UIs (WP 11.6, #1205, #796).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/session/report/__tests__/w11_6BundleRowsTellMosaicsApart.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The server tells two same-named mosaics' groups apart by the folder the
// stack is built in: `Veil/...` for the first, `Veil_2/...` for the second
// (`_mosaic_folders`, bundle-relative, from the sanitised label). The row's
// label said only `mosaic Veil panel 1-2` for both. It now ends with that
// folder, which is the one thing the two rows do not share. It is the
// bundle-relative folder, never a filesystem path, so it carries no user name
// and no site label. One function, `groupLine`, words the row for the classic
// report and for #/next; the classic report used to keep a copy of it.
//
// MUTANT "the folder dropped" (reportModel.ts `groupLine`: the line that pushes
// `folder ...` removed): the model case and both DOM cases red, quoted below.

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
const { useStore } = await import("../../../../../store");
const ReportView = (await import("../../../../../views/ReportView")).default;
const { groupLine } = await import("../reportModel");

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


const PANEL = {
  filter: "Ha", exposure_s: 300, gain: 100, binning: 1, light_count: 3,
  accepted_count: 3, kept_count: 3, masters: { dark: true, flat: true, bias: false },
  target: "Veil 1-2", mosaic: "Veil", panel: "1-2",
};
const FIRST = { ...PANEL, dir: "Veil/Veil 1-2/Ha/300s_g100_bin1", mosaic_id: "group-a", panel_id: "panel-a" };
const SECOND = { ...PANEL, dir: "Veil_2/Veil 1-2/Ha/300s_g100_bin1", mosaic_id: "group-b", panel_id: "panel-b" };

test("two same-named mosaics' rows do not read the same", () => {
  const a = groupLine(FIRST as any);
  const b = groupLine(SECOND as any);
  assert(a !== b, `both rows read: ${a}`);
  assert(a.endsWith("· folder Veil"), a);
  assert(b.endsWith("· folder Veil_2"), b);
});

test("a row whose folder carries no mosaic prefix gains nothing", () => {
  const plain = { ...PANEL, mosaic: null, panel: null, dir: "M31/Ha/300s_g100_bin1", target: "M31" };
  assert(groupLine(plain as any) === "M31 · Ha · 300s · g100 · bin1", groupLine(plain as any));
  const unshared = { ...PANEL, dir: "Veil 1-2/Ha/300s_g100_bin1" };
  assert(!/folder/.test(groupLine(unshared as any)), groupLine(unshared as any));
});

test("a path that is not bundle-relative is never printed", () => {
  for (const dir of ["C:\\Users\\someone\\Veil/Veil 1-2/Ha/300s_g100_bin1",
                     "/home/someone/Veil/Veil 1-2/Ha/300s_g100_bin1"]) {
    const line = groupLine({ ...PANEL, dir } as any);
    assert(!/folder|someone|Users/.test(line), line);
  }
});

const ID = "Veil-20260805-203000";
const REPORT = {
  id: ID, plan_name: "Veil", started_at: 1_754_000_000, ended_at: 1_754_020_000,
  end_reason: "complete", frames_captured: 6, frames_rejected: 0,
  integration_s: 6 * 300,
  by_filter: [{ filter: "Ha", frames: 6, rejected: 0, integration_s: 1800, hfr_median: 2.2 }],
  targets: [], safety_events: [], trends: { hfr: [], temp: [], rms: [] },
};
const BUNDLE = {
  report_id: ID, plan_name: "Veil", layout: "grouped", weight_altitude: false,
  keep_threshold: null, warnings: [], groups: [FIRST, SECOND],
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
    end_reason: "complete", frames_captured: 6, frames_rejected: 0,
    integration_s: REPORT.integration_s,
  }]);
  await flush();
  answer(`/api/reports/${encodeURIComponent(ID)}`, REPORT);
  await flush();
  if (inFlight("bundle")) answer("bundle", BUNDLE);
  await flush();

  const text = (container.textContent as string).replace(/\s+/g, " ");
  test("the classic report's two rows name their own folder", () => {
    assert(text.includes(groupLine(FIRST as any)),
      `no first row: ${text.slice(text.indexOf("Veil 1-2"), text.indexOf("Veil 1-2") + 160)}`);
    assert(text.includes(groupLine(SECOND as any)),
      `no second row: ${text.slice(text.indexOf("Veil 1-2"), text.indexOf("Veil 1-2") + 160)}`);
    assert(/folder Veil_2/.test(text) && /folder Veil(?!_)/.test(text),
      `the classic rows read: ${text.slice(text.indexOf("Veil 1-2"), text.indexOf("Veil 1-2") + 200)}`);
  });
}

await scenario().catch((e: unknown) => {
  failed++;
  failures.push(`x the scenario could not be driven to the end: ${(e as Error).message}`);
});

act(() => { root.unmount(); });
const total = passed + failed;
console.log(`w11_6BundleRowsTellMosaicsApart.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
