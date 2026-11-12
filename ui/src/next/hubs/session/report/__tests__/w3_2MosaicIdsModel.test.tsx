// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w3_2MosaicIdsModel.test.tsx - a mosaic is its id in the report's rows, and a
// bundle group row names its mosaic (WP 3.2, #796, #188, #830).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/session/report/__tests__/w3_2MosaicIdsModel.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// `groupTargetsByMosaic` grouped by the mosaic's NAME, so two groups that share
// a name and name their panels alike were one header with their panels
// interleaved. The server now writes `mosaic_id` on each row and the grouper
// keys on it; a report written before the ids has the label only and groups by
// it as it did.
//
// MUTANT "group by name" (FilterRows.tsx: `const key = t.mosaic_id || t.mosaic`
// becomes `const key = t.mosaic`): the two-mosaics case reds, quoted below.

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
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "getComputedStyle", "matchMedia",
  "WebSocket", "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}

const { groupTargetsByMosaic } = await import("../FilterRows");
const { groupLine } = await import("../reportModel");

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

/** One panel's row as the server writes it, with its ids. */
function panel(mosaicId: string | null, label: string, frames: number) {
  return {
    name: `Veil ${label}`, frames, rejected: 0, integration_s: frames * 300,
    by_filter: [], mosaic: "Veil", panel: label, mosaic_id: mosaicId,
    panel_id: mosaicId ? `${mosaicId}-${label}` : null,
  };
}

test("two mosaics that share a name and panel names stay two groups", () => {
  const rows = groupTargetsByMosaic([
    panel("group-a", "1-1", 3), panel("group-b", "1-1", 5),
    panel("group-a", "1-2", 4), panel("group-b", "1-2", 6),
  ]);
  const mosaics: any[] = rows.filter((r) => r.kind === "mosaic");
  assert(mosaics.length === 2,
    `expected two mosaic groups, got ${mosaics.length}: ${JSON.stringify(rows.map((r) => r.kind))}`);
  assert(mosaics.map((m) => m.frames).join(",") === "7,11",
    `each group sums its own panels: ${mosaics.map((m) => m.frames).join(",")}`);
  assert(mosaics.every((m) => m.panels.map((p: any) => p.target.panel).join(",") === "1-1,1-2"),
    "each group holds one 1-1 and one 1-2");
});

test("a report written before the ids groups by the label as it always did", () => {
  const rows = groupTargetsByMosaic([
    panel(null, "1-1", 3), panel(null, "1-2", 4),
  ]);
  const mosaics: any[] = rows.filter((r) => r.kind === "mosaic");
  assert(mosaics.length === 1 && mosaics[0].panels.length === 2,
    `one group of two: ${JSON.stringify(rows.map((r) => r.kind))}`);
});

const GROUP = {
  dir: "Veil/Veil 1-2/Ha/300s_g100_bin1", target: "Veil 1-2", filter: "Ha",
  exposure_s: 300, gain: 100, binning: 1, light_count: 3, accepted_count: 3,
  masters: {},
};

test("a mosaic panel's bundle row names its mosaic and its place", () => {
  const line = groupLine({ ...GROUP, mosaic: "Veil", panel: "1-2", mosaic_id: "g", panel_id: "p" } as any);
  assert(line === "Veil 1-2 \u00b7 Ha \u00b7 300s \u00b7 g100 \u00b7 bin1 \u00b7 mosaic Veil panel 1-2 · folder Veil", line);
});

test("a row with a mosaic and no panel, and a row with neither", () => {
  const some = groupLine({ ...GROUP, mosaic: "Veil", panel: null } as any);
  assert(some.endsWith("\u00b7 mosaic Veil \u00b7 folder Veil"), some);
  const none = groupLine({ ...GROUP, mosaic: null, panel: null } as any);
  assert(none === "Veil 1-2 \u00b7 Ha \u00b7 300s \u00b7 g100 \u00b7 bin1", none);
  assert(groupLine(GROUP as any) === none, "an older server sends neither key");
});

console.log(`w3_2MosaicIdsModel: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
