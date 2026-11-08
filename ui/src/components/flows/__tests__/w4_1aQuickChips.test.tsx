// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w4_1aQuickChips.test.tsx - the quick sheet's calibration and automation chips
// keep what they say (#1111 C-05, #843, #818, #820, #845; wave 4, WP 4.1a).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/w4_1aQuickChips.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE CLASS: a claim nothing keeps. The sheet offered chips the engine did not
// run, the flats chip created the one method the engine does not run, and the
// Tonight timeline drew the flats with no width. The server half is
// `server/tests/test_w4_quick_chips.py`; this file holds the UI half:
//
//   * THE CAPABILITY MIRROR (`nodeDefs.ts` `DUSK_FLATS_METHODS`,
//     `AUTOMATION_CHIPS`) is `nodes.py`'s table row for row, read as text, so
//     the sheet can never offer what the server's table refuses;
//   * THE CHIP KEYS the sheet offers (`quick.tsx` `AUTOMATION`) are the
//     table's keys, in its order;
//   * THE FLATS CHIP creates the block with the first capable method, and the
//     DARKS AFTER chip writes the Always policy the plan now carries;
//   * THE TONIGHT TIMELINE draws a block the engine runs at the run's start
//     with the server's estimated width, opens the axis at the run's start
//     (#845), keeps a tick when no width is known, and both readers carry the
//     new key.
//
// NAMED MUTANT (run from a byte backup, restored byte-identically with sha256
// compared): the lens cap offered again, `nodeDefs.ts` `DUSK_FLATS_METHODS`
// marking "cap" engineCapable: the mirror case and the creation case go red.

/* eslint-disable @typescript-eslint/no-explicit-any */

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { installAutoRaf } from "../../../testing/rafPolyfill";

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
win.WebSocket = class { close() {} addEventListener() {} send() {} };
{
  const gl = globalThis as any;
  for (const k of [
    "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
    "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
    "matchMedia", "WebSocket", "location", "history",
  ]) {
    const v = k === "window" ? win : win[k];
    Object.defineProperty(gl, k, { value: v, writable: true, configurable: true });
  }
  installAutoRaf(gl);
  gl.IS_REACT_ACT_ENVIRONMENT = true;
}

const nodeDefs = await import("../nodeDefs");
const { timelineGeometry, TL_W } = await import("../TonightTimeline");
const { withDuskFlats, withDarksAfter, withRotation, withTargetPool, flatPanelConnected } = await import(
  "../../../next/hubs/sky/sheets/flowGraphExtras");
const { chipAnswers, quickPayload } = await import(
  "../../../next/hubs/session/flows/create/quickPayload");
const { readTonight } = await import(
  "../../../next/hubs/session/flows/tonight/tonightModel");
type TonightTimelineProps = import("../TonightTimeline").TonightTimelineProps;
type FlowGraphRec = import("../flowsTypes").FlowGraphRec;

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): asserts cond { if (!cond) throw new Error(msg); }
function eq(a: unknown, b: unknown, msg: string): void {
  if (JSON.stringify(a) !== JSON.stringify(b)) {
    throw new Error(`${msg} (expected ${JSON.stringify(b)}, got ${JSON.stringify(a)})`);
  }
}

const HERE = dirname(fileURLToPath(import.meta.url));
const UI_SRC = resolve(HERE, "../../..");
const NODES_PY = resolve(UI_SRC, "../../server/astrodeck/flows/nodes.py");
const QUICK_TSX = resolve(UI_SRC, "next/hubs/sky/sheets/quick.tsx");

/** Each `Capability(key, label, engine_capable ...)` row of one nodes.py table. */
function pyRows(table: string): { key: string; label: string; engineCapable: boolean }[] {
  const src = readFileSync(NODES_PY, "utf-8").replace(/\r\n/g, "\n");
  const at = src.indexOf(`${table}: tuple[Capability, ...] = (`);
  assert(at >= 0, `nodes.py no longer has ${table}`);
  const body = src.slice(at, src.indexOf("\n)\n", at));
  const rows: { key: string; label: string; engineCapable: boolean }[] = [];
  const re = /Capability\("(\w+)",\s*"([^"]+)",\s*(True|False)/g;
  for (let m = re.exec(body); m; m = re.exec(body)) {
    rows.push({ key: m[1], label: m[2], engineCapable: m[3] === "True" });
  }
  return rows;
}

const view = (rows: readonly { key: string; label: string; engineCapable: boolean }[]) =>
  rows.map((r) => ({ key: r.key, label: r.label, engineCapable: r.engineCapable }));

// ============================================================ the mirror

// MUTANT "the lens cap offered again" (nodeDefs.ts DUSK_FLATS_METHODS: "cap"
// `engineCapable: true`). Observed from a byte backup (restored, sha256
// compared): this case and "a new flats block is created with the first
// capable method" stay green on the order but the mirror case reads
// `{"key":"cap","label":"Translucent lens cap","engineCapable":true}` against
// nodes.py's false.
test("the dusk-flats capability mirror is nodes.py's table, row for row", () => {
  eq(view(nodeDefs.DUSK_FLATS_METHODS), pyRows("DUSK_FLATS_METHODS"),
    "DUSK_FLATS_METHODS against nodes.py");
});

test("the automation-chip capability mirror is nodes.py's table, row for row", () => {
  eq(view(nodeDefs.AUTOMATION_CHIPS), pyRows("AUTOMATION_CHIPS"),
    "AUTOMATION_CHIPS against nodes.py");
});

test("the chips the quick sheet offers are the table's, in its order", () => {
  const src = readFileSync(QUICK_TSX, "utf-8").replace(/\r\n/g, "\n");
  const at = src.indexOf("const AUTOMATION:");
  assert(at >= 0, "quick.tsx no longer declares its AUTOMATION chips");
  const block = src.slice(at, src.indexOf("];", at));
  const keys = [...block.matchAll(/key: "(\w+)"/g)].map((m) => m[1]);
  eq(keys, nodeDefs.AUTOMATION_CHIPS.map((c) => c.key), "quick.tsx AUTOMATION keys");
});

test("an incapable chip is not offered, and a key the table lacks is not either", () => {
  assert(nodeDefs.chipEngineCapable("af") && nodeDefs.chipEngineCapable("hfr"), "af and hfr run");
  assert(!nodeDefs.chipEngineCapable("cloud"), "the cloud chip reaches no rule");
  assert(!nodeDefs.chipEngineCapable("stack"), "the live-stack chip reaches nothing");
  assert(!nodeDefs.chipEngineCapable("nonsense"), "an unknown chip is not offered");
});

// ============================================================ the chips

function lane(): FlowGraphRec {
  return {
    nodes: [
      { id: "n1", type: "dusk", x: 30, y: 60, params: {} },
      { id: "n2", type: "target", x: 100, y: 60, params: {} },
      { id: "n7", type: "report", x: 258, y: 60, params: {} },
    ],
    edges: [
      { id: "we0", from: "n1", fromPort: "window", to: "n2", toPort: "arm" },
      { id: "we1", from: "n2", fromPort: "target", to: "n7", toPort: "session" },
    ],
  };
}

test("a new DUSK FLATS block is created with the first method the engine runs, never the lens cap", () => {
  const made = nodeDefs.createParams("duskflats").method;
  eq(made, nodeDefs.capableFlatsMethods()[0], "the created method");
  eq(made, "Twilight sky", "the first capable method");
  const row = nodeDefs.DUSK_FLATS_METHODS.find((m) => m.label === made);
  assert(row?.engineCapable === true, "the created method is not engine-capable");
  eq(nodeDefs.NODE_DEFS.duskflats.params.method, "Translucent lens cap",
    "the missing-key default keeps the meaning a saved block always had");
});

test("the flats chip adds the block with that method, wired between the window and what it fed", () => {
  const g = withDuskFlats(lane());
  const node = g.nodes.find((n) => n.type === "duskflats");
  assert(node, "no DUSK FLATS node was added");
  eq(node.params.method, "Twilight sky", "the chip's method");
  const into = g.edges.find((e) => e.to === node.id && e.toPort === "run");
  eq(into?.from, "n1", "the window feeds the flats");
  const out = g.edges.find((e) => e.from === node.id && e.fromPort === "done");
  eq(out?.to, "n2", "the flats feed what the window fed");
});

test("the flats chip creates the flat panel when one is connected, else the twilight sky", () => {
  const method = (g: FlowGraphRec) => g.nodes.find((n) => n.type === "duskflats")?.params.method;
  eq(method(withDuskFlats(lane(), true)), "Flat panel", "a panel is connected");
  eq(method(withDuskFlats(lane(), false)), "Twilight sky", "no panel");
  eq(method(withDuskFlats(lane())), "Twilight sky", "no panel said");
});

test("a flat panel is the covercalibrator role, read from backend_links then the device map", () => {
  const dev = { connected: true, name: "p" } as any;
  eq(flatPanelConnected(undefined, undefined), false, "nothing known");
  eq(flatPanelConnected(undefined, { covercalibrator: dev }), true, "the legacy map");
  eq(flatPanelConnected(undefined, { camera: dev }), false, "another role is not a panel");
  const link = (connected: boolean) =>
    [{ role: "covercalibrator", ok: true, error: null, attempted: true, connected }];
  eq(flatPanelConnected(link(false), { covercalibrator: dev }), false, "a bridged rig's links win");
  eq(flatPanelConnected(link(true), undefined), true, "a bridged rig's panel");
});

test("the chips are the payload: off is false, the dither chip is N or 0", () => {
  eq(chipAnswers({}, 3), { autofocus: true, watchdog: true, ditherEvery: 3 }, "every chip on");
  eq(chipAnswers({ af: false, hfr: false }, 3),
    { autofocus: false, watchdog: false, ditherEvery: 3 }, "autofocus and the watchdog off");
  eq(chipAnswers({}, 5).ditherEvery, 5, "the sheet's N");
  eq(chipAnswers({ dither: false }, 5).ditherEvery, 0, "dither off is 0");
});

test("the body carries the chips when given, and none when not", () => {
  const base = {
    target: { name: "M31", ra: "00h 42m 44s", dec: "+41 16 09" }, subs: 4, filters: ["L"],
    exposures: { L: 60 }, guided: true, run: false,
  };
  const withChips = quickPayload({ ...base, chips: { autofocus: false, watchdog: true, ditherEvery: 5 } });
  eq([withChips.autofocus, withChips.watchdog, withChips.dither_every], [false, true, 5], "the chips");
  const without = quickPayload(base) as any;
  eq(["autofocus" in without, "watchdog" in without, "dither_every" in without], [false, false, false],
    "a caller with no chips posts none");
});

test("every edit the sheet makes keeps the flow's settings, where the chips are written", () => {
  // The server writes `quickAutofocus` and `quickDitherEvery` into the flow's
  // settings, and the sheet saves the graph back after these edits: an edit that
  // rebuilt the graph as {nodes, edges} would drop them and undo the chips.
  const g: FlowGraphRec = { ...lane(), settings: { quickAutofocus: false, quickDitherEvery: 5 } };
  const want = { quickAutofocus: false, quickDitherEvery: 5 };
  eq(withDuskFlats(g).settings, want, "withDuskFlats");
  eq(withDarksAfter(g).settings, want, "withDarksAfter");
  eq(withRotation(g, 12).settings, want, "withRotation");
  eq(withTargetPool(g, ["M31", "M33"]).settings, want, "withTargetPool");
});

test("the darks chip writes the Always policy the plan now carries", () => {
  const g = withDarksAfter(lane());
  const calib = g.nodes.find((n) => n.type === "calib");
  assert(calib, "no CALIBRATION QUEUE was added");
  eq(calib.params.darks, "Always", "the queue's Darks policy (plan.day_darks_always)");
});

// ============================================================ the timeline

const DUSK = 1_700_000_000;
const H = 3600;
const NIGHT = {
  dusk_unix: DUSK, dawn_unix: DUSK + 8 * H,
  dark_start_unix: DUSK + 40 * 60, dark_end_unix: DUSK + 8 * H - 40 * 60,
};
const PAD = (8 * H) * 0.03;

function props(flats: any): TonightTimelineProps {
  return { night: NIGHT, flats, moon: null, targets: [] };
}
const PANEL = { start_unix: null, end_unix: null, runs_at_start: true };

test("a panel block with an estimated length is a block of that width from the run's start", () => {
  const at = DUSK + 2 * H;
  const g = timelineGeometry(props({ ...PANEL, at_unix: at, duration_s: 1800 }))!;
  const r = g.rects.find((x) => x.key === "flats-start");
  assert(r, "no flats block was drawn for a block with a length");
  const t0 = DUSK - PAD;
  const span = 8 * H + 2 * PAD;
  const want = (1800 / span) * TL_W;
  assert(Math.abs(r.w - want) < 0.01, `the block's width is not its duration's: ${r.w} vs ${want}`);
  assert(!g.dashes.some((d) => d.key === "flats-start"), "a tick was drawn beside the block");
  eq(g.flats, "start", "the geometry's flats mark");
  assert(Math.abs(r.x - ((at - t0) / span) * TL_W) < 0.01, "the block does not start at the run's start");
});

test("with no estimate the mark stays a tick, as it was", () => {
  const at = DUSK + 2 * H;
  for (const none of [undefined, null, 0]) {
    const g = timelineGeometry(props({ ...PANEL, at_unix: at, duration_s: none }))!;
    assert(g.dashes.some((d) => d.key === "flats-start"), `no tick for duration ${none}`);
    assert(!g.rects.some((x) => x.key === "flats-start"), `a block for duration ${none}`);
  }
});

test("the axis opens at a run start before dusk, so the block is no longer clamped to the edge (#845)", () => {
  const at = DUSK - 30 * 60;
  const g = timelineGeometry(props({ ...PANEL, at_unix: at, duration_s: 600 }))!;
  eq(g.t0, at, "the axis opens at the run's start");
  const r = g.rects.find((x) => x.key === "flats-start");
  assert(r, "no block for a run that starts before dusk");
  assert(r.x === 0, `the block starts at the axis's left edge, where the run starts: ${r.x}`);
  assert(r.w > 0, "the block has no width");
});

test("a run start far before dusk does not squash the night", () => {
  const g = timelineGeometry(props({ ...PANEL, at_unix: DUSK - 10 * H, duration_s: 600 }))!;
  eq(g.t0, DUSK - PAD, "the axis keeps its gutter when the start is more than three hours back");
});

test("a window block (twilight sky, lens cap) is unchanged and draws no run-start mark", () => {
  const g = timelineGeometry(props({
    start_unix: DUSK - 25 * 60, end_unix: DUSK - 5 * 60, runs_at_start: false,
    at_unix: null, duration_s: 600,
  }))!;
  assert(g.rects.some((x) => x.key === "flats"), "the window block is gone");
  assert(!g.rects.some((x) => x.key === "flats-start"), "a run-start block beside a window block");
  eq(g.flats, "window", "the geometry's flats mark");
});

test("both Tonight readers carry the estimated length, and read a non-number as none", () => {
  const payload = (flats: Record<string, unknown>) => ({
    ok: true, night: NIGHT, flats, moon: null, targets: [],
  });
  const read = readTonight(payload({ ...PANEL, at_unix: DUSK, duration_s: 90 }));
  eq(read?.flats?.duration_s, 90, "the #/next reader's duration_s");
  const bad = readTonight(payload({ ...PANEL, at_unix: DUSK, duration_s: "soon" }));
  eq(bad?.flats?.duration_s, null, "a non-number reads as none");
});

const total = passed + failed;
console.log(`w4_1aQuickChips.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}

export default { passed, failed, total };
export { passed, failed, total };
