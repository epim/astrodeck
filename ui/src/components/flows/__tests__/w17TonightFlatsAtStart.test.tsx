// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w17TonightFlatsAtStart.test.tsx - the Tonight timeline places a flat-PANEL
// DUSK FLATS block where the engine runs it, not at the Sun window (#744, #603
// job B).
//
//   Run directly:  npx tsx src/components/flows/__tests__/w17TonightFlatsAtStart.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// A panel is a constant light source, so `SequenceEngine._dusk_flats` does not
// wait for the node's Sun band: it runs once, when the run reaches it, before
// the first light. The timeline still drew a FLATS block over the Sun window
// and a legend entry "twilight / flats", telling the operator to plan the
// evening around an hour the engine does not use. The server now drops the
// window times for a block the engine runs as a panel and says `runs_at_start`
// and `at_unix` (the run's start) instead; the lens-cap and twilight-sky
// methods, which the engine does not run, keep their window block.
//
// WHAT IS WORTH ASSERTING:
//
//   THE RULE THAT PLACES THE MARK (`timelineGeometry`, shared by the classic
//   panel and the #/next card): a panel block is a TICK at the run's start
//   labelled FLATS and no block over a window; a window block is a block; a
//   panel block whose start falls outside the axis, or that a server which
//   predates #744 sent (no `runs_at_start`), draws nothing wrong.
//   THE LEGEND NEVER NAMES A MARK THE PICTURE LACKS, on both surfaces: "twilight
//   / flats" only beside a window block, "twilight" alone otherwise, and "flats,
//   once before the first light" only beside the tick.
//   BOTH READERS CARRY THE NEW KEYS (the classic panel's `readTonight` and the
//   new UI's), and read a non-boolean `runs_at_start` as false.
//
// NAMED MUTANTS (each run from a byte backup in this worktree, restored
// byte-identically with sha256 compared); the observed result is quoted at the
// case that catches it.

/* eslint-disable @typescript-eslint/no-explicit-any */

{
  const { registerHooks } = await import("node:module");
  registerHooks({
    load(url: string, context: any, nextLoad: any) {
      if (url.endsWith(".css")) {
        return { format: "module", shortCircuit: true, source: "export default {};" };
      }
      return nextLoad(url, context);
    },
  } as any);
}

const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;
win.matchMedia = () => ({
  matches: true, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "getComputedStyle", "matchMedia",
  "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
installAutoRaf(g);
g.IS_REACT_ACT_ENVIRONMENT = true;

import { installAutoRaf } from "../../../testing/rafPolyfill";
import React, { act } from "react";
import { createRoot } from "react-dom/client";

const { useStore } = await import("../../../store");
const { FLOWS_INIT } = await import("../flowsSlice");
const TonightPanel = (await import("../TonightPanel")).default;
const { timelineGeometry } = await import("../TonightTimeline");
const { TonightTimelineCard } = await import("../../../next/hubs/session/flows/tonight/TonightTimelineCard");
const { readTonight } = await import("../../../next/hubs/session/flows/tonight/tonightModel");

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): asserts cond { if (!cond) throw new Error(msg); }

const DUSK = 1_700_000_000;
const H = 3600;
const RUN_START = DUSK - 30 * 60;

const NIGHT = {
  dusk_unix: DUSK, dawn_unix: DUSK + 8 * H,
  dark_start_unix: DUSK + 40 * 60, dark_end_unix: DUSK + 8 * H - 40 * 60,
};
const MOON = { illumination: 0.5, rise_unix: null, set_unix: null };

/** What the server sends for a block the engine runs as a flat panel. The run
 *  starts 30 minutes BEFORE dusk (an autorun offset of -30 min), which is before
 *  the axis does. */
const PANEL_FLATS = {
  start_unix: null, end_unix: null, runs_at_start: true, at_unix: RUN_START,
};
/** The same, for a run that starts well inside the axis. */
const PANEL_ON_AXIS = { ...PANEL_FLATS, at_unix: DUSK + 2 * H };
/** And for a lens-cap or twilight-sky block: the Sun window, drawn, not run. */
const CAP_FLATS = {
  start_unix: DUSK - 25 * 60, end_unix: DUSK - 5 * 60, runs_at_start: false, at_unix: null,
};
/** A server that predates #744: the window keys only. */
const OLD_FLATS = { start_unix: DUSK - 25 * 60, end_unix: DUSK - 5 * 60 };

const props = (flats: any) => ({ night: NIGHT, flats, moon: MOON, targets: [] });
const rectKeys = (gm: any): string[] => gm.rects.map((r: any) => r.key);
const dashKeys = (gm: any): string[] => gm.dashes.map((d: any) => d.key);
const labelText = (gm: any, key: string): string | undefined =>
  gm.labels.find((l: any) => l.key === key)?.text;

// ===================================================== the placement rule

test("a flat-panel block is a tick at the run's start, labelled FLATS, and no block over a window", () => {
  const gm = timelineGeometry(props(PANEL_ON_AXIS))!;
  assert(!rectKeys(gm).includes("flats"), "a panel block was drawn over a Sun window");
  const tick = gm.dashes.find((d: any) => d.key === "flats-start");
  assert(tick, "no tick at the run's start");
  assert(tick.x > 0 && tick.x < 1000, `the tick is on an axis edge, not at the run's start: ${tick.x}`);
  eq(labelText(gm, "flats-start-label"), "FLATS", "the tick's label");
  eq(gm.flats, "start", "the geometry does not say what the mark is");
});

test("a run that starts before the axis is already going when it opens: the tick sits at the left edge", () => {
  const gm = timelineGeometry(props(PANEL_FLATS))!;
  const tick = gm.dashes.find((d: any) => d.key === "flats-start");
  assert(tick, "a run that starts before the axis drew no tick at all");
  eq(tick.x, 0, "the tick's place");
  eq(gm.flats, "start", "the geometry's flats mark");
});
function eq(a: unknown, b: unknown, msg: string): void {
  if (a !== b) throw new Error(`${msg} (expected ${String(b)}, got ${String(a)})`);
}

test("a lens-cap or twilight-sky block keeps its window block, with no tick", () => {
  const gm = timelineGeometry(props(CAP_FLATS))!;
  assert(rectKeys(gm).includes("flats"), "the window block is gone");
  assert(!dashKeys(gm).includes("flats-start"), "a tick was drawn for a block the engine does not run");
  eq(gm.flats, "window", "the geometry's flats mark");
});

test("a server that predates #744 draws what it always drew", () => {
  const gm = timelineGeometry(props(OLD_FLATS))!;
  assert(rectKeys(gm).includes("flats") && !dashKeys(gm).includes("flats-start"),
    "the old window answer no longer draws its block");
  eq(gm.flats, "window", "the geometry's flats mark");
});

test("nothing is drawn for a tick the axis does not hold, or for no flats at all", () => {
  const off = timelineGeometry(props({ ...PANEL_FLATS, at_unix: DUSK + 20 * H }))!;
  eq(off.flats, null, "a tick off the axis");
  assert(!dashKeys(off).includes("flats-start"), "a tick was drawn off the axis");
  const none = timelineGeometry(props(null))!;
  eq(none.flats, null, "no flats block");
  const unsure = timelineGeometry(props({ ...PANEL_FLATS, at_unix: null }))!;
  eq(unsure.flats, null, "a panel block the server could not place draws nothing");
});

// MUTANT "TICK_BRANCH_REMOVED" (TonightTimeline.tsx: the `else if (flats &&
// flats.runs_at_start === true ...)` branch made `else if (false as boolean)`).
// Observed from a byte backup (restored, sha256 compared):
// "w17TonightFlatsAtStart: 4/8 passed", four red - "x a flat-panel block is a
// tick at the run's start, labelled FLATS, and no block over a window: no tick
// at the run's start", "x a run that starts before the axis is already going
// when it opens: the tick sits at the left edge: a run that starts before the
// axis drew no tick at all", and the two legend cases below.

// ============================================== the legends, both surfaces

const container = win.document.getElementById("root") as any;
const root = createRoot(container);

function mountCard(flats: any): string {
  act(() => root.render(null));
  act(() => root.render(React.createElement(TonightTimelineCard as any, props(flats))));
  return (container.querySelector(".nx-tn-legend")?.textContent ?? "") as string;
}

// MUTANT "CARD_LEGEND_ALWAYS_TWILIGHT_FLATS" (TonightTimelineCard.tsx: the
// legend's `g.flats === "window" ? "twilight / flats" : "twilight"` made the
// constant "twilight / flats"). Observed (restored, sha256 compared):
// "7/8 passed", this case red - "x the #/next card's legend names the tick only
// beside it: a panel night's legend still names flats over the twilight:
// imaging window + altitude arctwilight / flatsflats, once before the first
// lightmeridian flipmoon up (50%)".
test("the #/next card's legend names the tick only beside it", () => {
  const panel = mountCard(PANEL_FLATS);
  assert(/twilight/.test(panel) && !/twilight \/ flats/.test(panel),
    `a panel night's legend still names flats over the twilight: ${panel}`);
  assert(/flats, once before the first light/.test(panel),
    `the tick is not named: ${panel}`);
  const cap = mountCard(CAP_FLATS);
  assert(/twilight \/ flats/.test(cap), `a window night lost twilight / flats: ${cap}`);
  assert(!/once before the first light/.test(cap), `a window night names a tick: ${cap}`);
  const none = mountCard(null);
  assert(/twilight/.test(none) && !/flats/.test(none),
    `a night with no flats names flats: ${none}`);
});

function panelOf(flats: any): string {
  act(() => root.render(null));
  act(() => {
    useStore.setState({
      flows: {
        ...FLOWS_INIT,
        record: {
          id: "f", name: "F", folder: "", tagline: "", graph: { nodes: [], edges: [] },
          created_ts: 0, updated_ts: 0, last_run: null, last_result: "", readonly: true,
        },
        ui: { ...FLOWS_INIT.ui, tonightOpen: true },
        tonight: {
          ok: true, reason: "", night: { ...NIGHT, window_start_unix: RUN_START, window_stop_unix: DUSK + 8 * H },
          flats, moon: MOON, targets: [], budget: [], story: [], brief: "",
        },
      },
      flowsFetchTonight: async () => {},
    } as any);
  });
  act(() => root.render(React.createElement(TonightPanel)));
  return (win.document.querySelector("[data-flows-tonight='timeline']")?.textContent ?? "") as string;
}

// MUTANT "CLASSIC_LEGEND_ALWAYS_TWILIGHT_FLATS" (TonightTimeline.tsx, the same
// change in the classic legend): "7/8 passed", this case red - "x the classic
// panel reads the new keys and its legend follows the mark: a panel night's
// legend still names flats over the twilight: ... twilight / flats flats, once
// before the first light ...".
// MUTANT "CLASSIC_READER_DROPS_THE_KEYS" (TonightPanel.tsx: `runs_at_start:
// flats.runs_at_start === true` made `runs_at_start: false`): "7/8 passed",
// this case red - "x ... the classic reader dropped runs_at_start / at_unix:
// ...imaging window + altitude arc twilight meridian flip moon up (50%)".
test("the classic panel reads the new keys and its legend follows the mark", () => {
  const panel = panelOf(PANEL_FLATS);
  assert(/flats, once before the first light/.test(panel),
    `the classic reader dropped runs_at_start / at_unix: ${panel}`);
  assert(!/twilight \/ flats/.test(panel), `a panel night's legend still names flats over the twilight: ${panel}`);
  assert(/FLATS/.test(panel), `the tick has no label: ${panel}`);
  const cap = panelOf(CAP_FLATS);
  assert(/twilight \/ flats/.test(cap) && !/once before the first light/.test(cap),
    `a window night's legend is wrong: ${cap}`);
  const odd = panelOf({ ...PANEL_FLATS, runs_at_start: "yes" });
  assert(!/once before the first light/.test(odd),
    `a non-boolean runs_at_start was read as true: ${odd}`);
});

// MUTANT "NEW_READER_READS_ANY_TRUTHY" (tonightModel.ts: `runs_at_start:
// flats.runs_at_start === true` made `!!flats.runs_at_start`): "7/8 passed",
// this case red - "x the new UI's reader carries the keys and reads a
// non-boolean as false: a non-boolean runs_at_start (expected false, got
// true)".
test("the new UI's reader carries the keys and reads a non-boolean as false", () => {
  const base = { ok: true, night: NIGHT, flats: PANEL_FLATS, moon: MOON, targets: [], story: [] };
  const got = readTonight(base as any)!;
  eq((got.flats as any).runs_at_start, true, "runs_at_start");
  eq((got.flats as any).at_unix, RUN_START, "at_unix");
  const odd = readTonight({ ...base, flats: { ...PANEL_FLATS, runs_at_start: 1 } } as any)!;
  eq((odd.flats as any).runs_at_start, false, "a non-boolean runs_at_start");
});

act(() => root.unmount());

console.log(`w17TonightFlatsAtStart: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
