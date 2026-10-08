// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w16MosaicRows.test.tsx - the BY TARGET card groups a mosaic's panels (#188).
//
//   Run directly:  npx tsx src/next/hubs/session/report/__tests__/w16MosaicRows.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// A 2x2 mosaic used to be four unrelated targets named "<name> r-c" in this
// card, with no header, no roll-up and no way to see the mosaic's frames as one
// body of work. The server now labels each panel's row with `mosaic` and
// `panel` (report.py `TargetBreakdown`); `groupTargetsByMosaic` turns the rows
// into one header per mosaic with a row per panel, and `ByTargetCard` renders
// it. A target that is no mosaic's panel renders exactly as it did.
//
// MUTANTS (each run from a byte backup of FilterRows.tsx, restored
// byte-identical and grepped gone; the failure is as the run printed it):
//
//   'group by target name': the grouper keys on `t.name` and not `t.mosaic`.
//      4 of 12 pass; first red: "a 2x2 mosaic is ONE group of four panels, in
//      grid order: expected one mosaic group, got 4:
//      ["target","mosaic","mosaic","mosaic","mosaic","target"]"
//   'sum drops rejected': the roll-up adds frames and time but not rejects.
//      "the roll-up is the sum of the panels, rejects included: rejected 0,
//      expected 1+2 = 3"
//   'panels in report order': the panels are not put in grid order.
//      "a 2x2 mosaic is ONE group of four panels, in grid order: panels not in
//      grid order: 2-1,1-1,2-2,1-2"
//   'plural always': the header says "1 panels".
//      "a mosaic with one panel so far is a header with one panel row: the
//      header says: Veil1 panels · 4 frames · 20m 0s..."
//   'ungrouped' (`if (!t.mosaic)` becomes `if (true)`, which is what the card
//      did before this work): 1 of 12 pass; first red: "a 2x2 mosaic is ONE
//      group of four panels, in grid order: expected one mosaic group, got 0:
//      ["target","target","target","target","target","target"]"

/* eslint-disable @typescript-eslint/no-explicit-any */

const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;
// The kit `FilterRows` imports reaches the store, which reads localStorage and
// the media query at import.
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "getComputedStyle", "matchMedia",
  "WebSocket", "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { ByTargetCard, groupTargetsByMosaic } = await import("../FilterRows");
const { fmtDuration } = await import("../../../../../lib/eta");

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const F = (filter: string, frames: number, rejected = 0) => ({
  filter, frames, rejected, integration_s: frames * 300, hfr_median: 2.2,
});
/** One panel's row as the server writes it. */
function panel(mosaic: string, label: string, frames: number, rejected = 0) {
  return {
    name: `${mosaic} ${label}`, frames, rejected, integration_s: frames * 300,
    by_filter: [F("Ha", frames, rejected)], mosaic, panel: label,
  };
}
/** A target that is no mosaic's panel: the server writes null for both. */
function single(name: string, frames: number) {
  return {
    name, frames, rejected: 0, integration_s: frames * 300,
    by_filter: [F("L", frames)], mosaic: null, panel: null,
  };
}

// The order the group driver visits a 2x2 in is NOT grid order, which is the
// point: the report lists targets by first frame.
const REPORT_ORDER = [
  single("M31", 10),
  panel("Veil", "2-1", 5, 1),
  panel("Veil", "1-1", 6),
  panel("Veil", "2-2", 7, 2),
  panel("Veil", "1-2", 8),
  single("NGC 7331", 3),
];

// ------------------------------------------------------------------ the grouper

test("a 2x2 mosaic is ONE group of four panels, in grid order", () => {
  const rows = groupTargetsByMosaic(REPORT_ORDER);
  const mosaics = rows.filter((r) => r.kind === "mosaic");
  assert(mosaics.length === 1,
    `expected one mosaic group, got ${mosaics.length}: ${JSON.stringify(rows.map((r) => r.kind))}`);
  const m: any = mosaics[0];
  assert(m.mosaic === "Veil", `the group is named ${m.mosaic}`);
  assert(m.panels.map((p: any) => p.target.panel).join(",") === "1-1,1-2,2-1,2-2",
    `panels not in grid order: ${m.panels.map((p: any) => p.target.panel).join(",")}`);
});

test("the group sits where its first panel was, and singles keep their order", () => {
  const rows = groupTargetsByMosaic(REPORT_ORDER);
  const names = rows.map((r: any) => (r.kind === "mosaic" ? `[${r.mosaic}]` : r.target.name));
  assert(names.join("|") === "M31|[Veil]|NGC 7331", `row order: ${names.join("|")}`);
});

test("the roll-up is the sum of the panels, rejects included", () => {
  const m: any = groupTargetsByMosaic(REPORT_ORDER).find((r) => r.kind === "mosaic");
  assert(m.frames === 26, `frames ${m.frames}, expected 5+6+7+8 = 26`);
  assert(m.rejected === 3, `rejected ${m.rejected}, expected 1+2 = 3`);
  assert(m.integration_s === 26 * 300, `integration ${m.integration_s}`);
});

test("each panel keeps its index into the report's own targets", () => {
  const m: any = groupTargetsByMosaic(REPORT_ORDER).find((r) => r.kind === "mosaic");
  const byLabel = Object.fromEntries(m.panels.map((p: any) => [p.target.panel, p.index]));
  assert(byLabel["1-1"] === 2 && byLabel["2-1"] === 1 && byLabel["1-2"] === 4,
    `indexes: ${JSON.stringify(byLabel)}`);
});

test("two different mosaics are two groups", () => {
  const rows = groupTargetsByMosaic([
    panel("North", "1-1", 2), panel("South", "1-1", 3), panel("North", "1-2", 4),
  ]);
  assert(rows.length === 2 && rows.every((r) => r.kind === "mosaic"),
    `rows: ${JSON.stringify(rows.map((r) => r.kind))}`);
  assert((rows[0] as any).panels.length === 2, "North has its two panels together");
});

test("targets with no label (a report from before the fields) are all plain", () => {
  const old = [{ name: "Veil 1-1", frames: 4, rejected: 0, integration_s: 1200, by_filter: [F("Ha", 4)] },
    { name: "Veil 1-2", frames: 4, rejected: 0, integration_s: 1200, by_filter: [F("Ha", 4)] }];
  const rows = groupTargetsByMosaic(old);
  assert(rows.length === 2 && rows.every((r) => r.kind === "target"),
    `an unlabelled target was grouped: ${JSON.stringify(rows.map((r) => r.kind))}`);
});

test("a mosaic member with no panel label is listed under its own name", () => {
  const odd = [{ name: "Wide field", frames: 4, rejected: 0, integration_s: 1200,
    by_filter: [F("Ha", 4)], mosaic: "Veil", panel: null }];
  const rows: any[] = groupTargetsByMosaic(odd);
  assert(rows.length === 1 && rows[0].kind === "mosaic" && rows[0].panels.length === 1,
    "it is still a panel of its mosaic");
});

// ----------------------------------------------------------------------- the DOM

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
act(() => { root.render(createElement(ByTargetCard, { targets: REPORT_ORDER })); });
const tid = (id: string) => container.querySelector(`[data-testid="${id}"]`);
const all = (sel: string) => [...container.querySelectorAll(sel)] as any[];

test("one header names the mosaic and carries the sum", () => {
  const heads = all('[data-testid^="report-mosaic-"]').filter((e) => /^report-mosaic-\d+$/.test(e.getAttribute("data-testid")));
  assert(heads.length === 1, `expected one mosaic block, got ${heads.length}`);
  const t = heads[0].textContent as string;
  assert(/Veil/.test(t), `the mosaic's name is not in its header: ${t.slice(0, 120)}`);
  assert(t.includes(`4 panels · 26 frames · ${fmtDuration(26 * 300)}`),
    `the sum is not in the header: ${t.slice(0, 200)}`);
  assert(/3 rejected/.test(t), `the summed rejects are not shown: ${t.slice(0, 200)}`);
});

test("one row per panel, labelled by panel, under that header", () => {
  const head = tid("report-mosaic-1");
  assert(head != null, "the mosaic block is the second row of the card (after M31)");
  const rows = [...head.querySelectorAll('[data-testid^="report-target-"]')]
    .filter((e: any) => /^report-target-\d+$/.test(e.getAttribute("data-testid")));
  assert(rows.length === 4, `expected four panel rows inside the mosaic, got ${rows.length}`);
  const labels = rows.map((r: any) => (/Panel (\d-\d)/.exec(r.textContent) || [])[1]);
  assert(labels.join(",") === "1-1,1-2,2-1,2-2", `panel rows: ${labels.join(",")}`);
  // A panel keeps its per-filter detail.
  assert(tid("report-target-2-filter-Ha") != null, "the 1-1 panel's Ha row is gone");
});

test("a plain target renders exactly as before, outside any mosaic", () => {
  const m31 = tid("report-target-0");
  assert(m31 != null && /M31/.test(m31.textContent), "M31 is not row 0");
  assert(tid("report-mosaic-1").contains(m31) === false, "M31 was pulled into the mosaic");
  assert(!/Panel/.test(m31.textContent), "a plain target was given a panel label");
  const ngc = tid("report-target-5");
  assert(ngc != null && /NGC 7331/.test(ngc.textContent), "NGC 7331 lost its testid");
});

test("the panel names are not repeated as four unrelated targets", () => {
  const text = container.textContent as string;
  assert(!/Veil 1-1/.test(text) && !/Veil 2-2/.test(text),
    `the old '<name> r-c' target names are still on screen: ${text.slice(0, 300)}`);
});

// A mosaic with one panel shot so far is still a mosaic, and says "1 panel".
act(() => {
  root.render(createElement(ByTargetCard, { targets: [panel("Veil", "1-2", 4)] }));
});
test("a mosaic with one panel so far is a header with one panel row", () => {
  const head = tid("report-mosaic-0");
  assert(head != null, "a lone panel was listed as a plain target");
  assert((head.textContent as string).includes("1 panel · 4 frames"),
    `the header says: ${(head.textContent as string).slice(0, 120)}`);
  assert(!/1 panels/.test(head.textContent), "'1 panels'");
  assert(/Panel 1-2/.test(head.textContent), "the panel row is missing");
});

act(() => { root.unmount(); });
const total = passed + failed;
console.log(`w16MosaicRows.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
