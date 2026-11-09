// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w5_1c_pool_is_not_a_mosaic.test.tsx - WP 5.1c (#183).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/lib/__tests__/w5_1c_pool_is_not_a_mosaic.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// A plan's `groups` used to hold mosaics only, and two readers took every
// entry for one. A POOL (`kind: "pool"`) is a set of candidates the run picks
// one of at a time, not a grid:
//
//   * the review drawer's mosaic list (`mosaicGroupsOf`) offered the pool as a
//     mosaic with its candidates as panels, and a "mosaic" filter choice;
//   * the Now integration bar (`plannedShutter`) counted a pool's candidates
//     as panels, in full, and so promised three times the integration the
//     rig can deliver from a three-candidate pool, where it cuts to one copy
//     of each distinct step.
//
// Pure functions over a plan, no clock.
import type { SequencePlan, Target, TargetGroup } from "../../types";

/* eslint-disable @typescript-eslint/no-explicit-any */

// IntegrationBar imports the store, which reads `window` as it loads: the same
// jsdom the component tests of this directory install first.
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(`<!doctype html><html><body></body></html>`,
  { url: "http://local/", pretendToBeVisual: true });
const win = dom.window as any;
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class {
  static OPEN = 1;
  readyState = 0;
  close() {} send() {} addEventListener() {} removeEventListener() {}
};
for (const k of ["window", "document", "navigator", "localStorage", "sessionStorage",
  "matchMedia", "WebSocket", "location", "history"]) {
  const v = k === "window" ? win : win[k];
  if (v !== undefined) Object.defineProperty(globalThis, k, { value: v, writable: true, configurable: true });
}
const { mosaicGroupsOf } = await import("../sessionReview");
const { plannedShutter } = await import("../../next/hubs/session/now/IntegrationBar");

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const step = (id: string) => ({
  id, filter: "L", exposure_s: 60, count: 10, gain: 100, offset: 10, binning: 1,
  frame_type: "Light",
});

const target = (id: string, group: string | undefined, cell?: [number, number]): Target => ({
  id, name: id, ra_hours: 1, dec_deg: 20, center: true,
  ...(group ? { mosaic_group: group } : {}),
  ...(cell ? { panel_row: cell[0], panel_col: cell[1] } : {}),
  steps: [step(`${id}-L`)],
} as unknown as Target);

const group = (id: string, kind: "mosaic" | "pool"): TargetGroup =>
  ({ id, name: id, kind, mode: "sequential" } as unknown as TargetGroup);

// A pool of three candidates beside a two-panel mosaic.
const plan = {
  targets: [
    target("cand-a", "pool"), target("cand-b", "pool"), target("cand-c", "pool"),
    target("pan-1", "mos", [0, 0]), target("pan-2", "mos", [0, 1]),
  ],
  groups: [group("pool", "pool"), group("mos", "mosaic")],
} as unknown as SequencePlan;

test("the review drawer lists the mosaic and not the pool", () => {
  const got = mosaicGroupsOf(plan);
  assert(got.length === 1 && got[0].id === "mos", `groups: ${got.map((g) => g.id).join(",")}`);
  assert(got[0].panels.length === 2, "the mosaic keeps its two panels");
});

test("a pool's candidates are no legacy mosaic either", () => {
  const lone = { targets: plan.targets.slice(0, 3), groups: [group("pool", "pool")] } as SequencePlan;
  assert(mosaicGroupsOf(lone).length === 0, "a pool alone is no mosaic");
});

test("the integration bar cuts a pool to one copy of each step and counts panels in full", () => {
  const rows = plannedShutter(plan);
  assert(rows.length === 1 && rows[0].filter === "L", `rows: ${JSON.stringify(rows)}`);
  // One copy of the pool's step (10) plus both panels in full (20), not 50.
  assert(rows[0].count === 30, `planned ${rows[0].count} subs, expected 30`);
});

test("a plan with a mosaic and no pool reads as before", () => {
  const only = { targets: plan.targets.slice(3), groups: [group("mos", "mosaic")] } as SequencePlan;
  assert(mosaicGroupsOf(only).length === 1, "the mosaic is listed");
  assert(plannedShutter(only)[0].count === 20, "both panels are in full");
});

const total = passed + failed;
console.log(`w5_1c_pool_is_not_a_mosaic.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
