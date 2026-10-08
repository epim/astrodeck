// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w16ReportMosaicDom.test.tsx - the classic session report groups a mosaic's
// panels under one header (#188).
//
//   Run directly:  npx tsx src/views/__tests__/w16ReportMosaicDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The classic report's BY TARGET panel listed a 2x2 mosaic as four unrelated
// targets named "<name> r-c". It now shows one header with the mosaic's name
// and the summed frames, and a row per panel, from the `mosaic` and `panel`
// the server writes on each panel's row. A target that is no mosaic's panel is
// listed as it always was. The next UI's card has its own test
// (next/hubs/session/report/__tests__/w16MosaicRows.test.tsx), which also holds
// the grouping rule both views share.
//
// MUTANT (from a byte backup of ReportView.tsx, restored byte-identical and
// grepped gone): 'classic ignores the grouping' renders `report.targets` as
// plain rows, as the panel did before this work. 1 of 4 pass; first red: "one
// header names the mosaic and carries the summed frames: no mosaic block in
// the By target panel", then "a plain target is listed as before, and the
// panels are not four targets: the old '<name> r-c' target names are still on
// screen". (Making the shared grouper return plain rows reds the same three.)

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
const { fmtDuration } = await import("../../lib/eta");
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
const F = (filter: string, frames: number, rejected = 0) => ({
  filter, frames, rejected, integration_s: frames * 300, hfr_median: 2.2,
});
function panel(label: string, frames: number, rejected = 0) {
  return {
    name: `Veil ${label}`, frames, rejected, integration_s: frames * 300,
    by_filter: [F("Ha", frames, rejected)], mosaic: "Veil", panel: label,
  };
}
const TARGETS = [
  panel("2-1", 5, 1), panel("1-1", 6), panel("2-2", 7, 2), panel("1-2", 8),
  {
    name: "M31", frames: 10, rejected: 0, integration_s: 3000,
    by_filter: [F("L", 10)], mosaic: null, panel: null,
  },
];
const REPORT = {
  id: ID, plan_name: "Veil", started_at: 1_754_000_000, ended_at: 1_754_020_000,
  end_reason: "complete", frames_captured: 36, frames_rejected: 3,
  integration_s: 36 * 300,
  by_filter: [F("Ha", 26, 3), F("L", 10)],
  targets: TARGETS, safety_events: [], trends: { hfr: [], temp: [], rms: [] },
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
    end_reason: "complete", frames_captured: 36, frames_rejected: 3,
    integration_s: REPORT.integration_s,
  }]);
  await flush();
  answer(`/api/reports/${encodeURIComponent(ID)}`, REPORT);
  await flush();
  if (inFlight("bundle")) answer("bundle", { groups: [], warnings: [], keep_threshold: null });
  await flush();

  const tid = (id: string) => container.querySelector(`[data-testid="${id}"]`);

  // PRECONDITION: the report really rendered, so nothing below can pass over an
  // empty page.
  test("the report with a mosaic is on screen", () => {
    assert(/By target/.test(container.textContent), `no By target panel: ${(container.textContent || "").slice(0, 200)}`);
    assert(/M31/.test(container.textContent), "the plain target is not listed");
  });

  test("one header names the mosaic and carries the summed frames", () => {
    const head = tid("report-mosaic-0");
    assert(head != null, "no mosaic block in the By target panel");
    const t = head.textContent as string;
    assert(/Veil/.test(t), `the header does not name the mosaic: ${t.slice(0, 120)}`);
    assert(t.includes(`4 panels · 26 frames · ${fmtDuration(26 * 300)}`),
      `the roll-up is not in the header: ${t.slice(0, 200)}`);
    assert(/3 rejected/.test(t), `the summed rejects are missing: ${t.slice(0, 200)}`);
  });

  test("a row per panel, in grid order, with its filter rows", () => {
    const head = tid("report-mosaic-0");
    const rows = [...head.querySelectorAll('[data-testid^="report-panel-"]')];
    assert(rows.length === 4, `expected four panel rows, got ${rows.length}`);
    const labels = rows.map((r: any) => (/Panel (\d-\d)/.exec(r.textContent) || [])[1]);
    assert(labels.join(",") === "1-1,1-2,2-1,2-2", `panel order: ${labels.join(",")}`);
    assert(/Ha/.test(rows[0].textContent) && /6 frames/.test(rows[0].textContent),
      `a panel row lost its detail: ${rows[0].textContent.slice(0, 160)}`);
  });

  test("a plain target is listed as before, and the panels are not four targets", () => {
    const text = container.textContent as string;
    assert(!/Veil 1-1/.test(text) && !/Veil 2-2/.test(text),
      "the old '<name> r-c' target names are still on screen");
    const m31 = tid("report-target-4");
    assert(m31 != null && /M31/.test(m31.textContent), "M31 is not listed as a plain target");
    assert(!tid("report-mosaic-0").contains(m31), "M31 was pulled into the mosaic");
  });
}

await scenario().catch((e: unknown) => {
  failed++;
  failures.push(`x the scenario could not be driven to the end: ${(e as Error).message}`);
});

act(() => { root.unmount(); });
const total = passed + failed;
console.log(`w16ReportMosaicDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
