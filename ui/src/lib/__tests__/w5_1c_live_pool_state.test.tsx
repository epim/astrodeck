// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w5_1c_live_pool_state.test.tsx - WP 5.1c fix round 1 (#183).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/lib/__tests__/w5_1c_live_pool_state.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The engine publishes `state.group` for a POOL as it does for a mosaic, now
// with `kind: "pool"`, no `pass` (a pool has a pick at each boundary, no pass
// over its members), and `panel`, `panels_done`, `panels_total` counting
// CANDIDATES. Every reader of the live group words that as candidates:
//
//   * `runStage` (the run's STAGE readout) names the candidate running and how
//     many are complete, with no "pass";
//   * `runTargetLine` and `runKind` (the Now run header) say "candidates" and
//     "target pool", not "panels" and "mosaic";
//   * `groupForBlock` (the framing sheet's read of the run) never hands a
//     pool's state to a mosaic block's panel rows.
//
// A mosaic's state, with and without `kind`, reads as it did.
import type { SequenceGroupState, SequenceState } from "../../types";

/* eslint-disable @typescript-eslint/no-explicit-any */

// RunHeader imports the store, which reads `window` as it loads.
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
const { runStage } = await import("../../components/flows/runCopy");
const { groupForBlock } = await import("../../components/flows/flowRunState");
const { runTargetLine, runKind } = await import("../../next/hubs/session/now/RunHeader");

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const pool = {
  id: "pool-1", name: "M42, M31", kind: "pool", mode: "sequential",
  panel: "M31", visit_elapsed_s: 0, panels_done: 1, panels_total: 3,
  set_aside: [], meridian_wait: false,
} as unknown as SequenceGroupState;

const mosaic = {
  id: "mos-1", name: "M16", kind: "mosaic", mode: "rotate", pass: 2,
  panel: "2-1", visit_elapsed_s: 0, panels_done: 1, panels_total: 4,
  set_aside: [], meridian_wait: false,
} as unknown as SequenceGroupState;

const seqOf = (group: SequenceGroupState, target: string): SequenceState =>
  ({ state: "running", target, plan_name: "night", group } as unknown as SequenceState);

test("the stage names the running candidate and the count done, with no pass", () => {
  const stage = runStage(seqOf(pool, "M31"));
  assert(stage === "M42, M31 M31 \u00b7 1 of 3 candidates done", `stage: "${stage}"`);
});

test("the stage of a mosaic still reads panel and pass", () => {
  assert(runStage(seqOf(mosaic, "M16 2-1")) === "M16 2-1 \u00b7 pass 2", "mosaic stage unchanged");
  const bare: any = { ...mosaic };
  delete bare.kind;
  assert(runStage(seqOf(bare, "M16 2-1")) === "M16 2-1 \u00b7 pass 2", "a state with no kind is a mosaic's");
});

test("the target line says candidates", () => {
  assert(runTargetLine(seqOf(pool, "M31"), 3) === "M31 \u00b7 one of 3 candidates",
    `line: "${runTargetLine(seqOf(pool, "M31"), 3)}"`);
  assert(runTargetLine(seqOf(mosaic, "M16 2-1"), 4) === "M16 2-1 \u00b7 one of 4 panels", "mosaic line unchanged");
});

test("the run's kind is a target pool", () => {
  assert(runKind(pool, 3) === "target pool", `kind: "${runKind(pool, 3)}"`);
  assert(runKind(mosaic, 4) === "mosaic", "a mosaic is still a mosaic");
});

test("a pool's live state is never a mosaic block's group", () => {
  assert(groupForBlock(seqOf(pool, "M31"), "pool-1") === null, "pool state not handed out");
  assert(groupForBlock(seqOf(mosaic, "M16 2-1"), "mos-1") === mosaic, "the mosaic's is");
});

const total = passed + failed;
console.log(`w5_1c_live_pool_state.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
