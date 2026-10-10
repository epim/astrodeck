// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w20ThrowawayTargetIndex.test.tsx - a calibration set is not a plan target
// (#941), on the screens that read `target_index`.
//
//   Run directly:  npx tsx src/next/hubs/session/__tests__/w20ThrowawayTargetIndex.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The engine now publishes `target_index: null` (the key present) while a
// calibration target the plan does not hold is exposing: the DUSK FLATS sets,
// the day darks, a cloud-hold dark. It used to publish the placeholder it was
// handed, which named plan target 0 (or the held target), so through the flats
// the Pool chips lit the first target as "now", the run header read "1 of N"
// and the vitals band named the second target as the one after "this one".
//
// What is checked is what a person SEES, from the same payloads the engine
// sends: the numbers 0 and 1, the null, and the absent key (a run that has not
// published a target yet), which every screen reads as the first target and
// must keep reading so.

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
win.WebSocket = class { close() {} send() {} addEventListener() {} removeEventListener() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "sessionStorage",
  "getComputedStyle", "matchMedia", "WebSocket", "requestAnimationFrame",
  "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------ the session
const target = (id: string, name: string, ra: number) => ({
  id, name, ra_hours: ra, dec_deg: 41.2, center: true, autofocus_first: false,
  calibration: false,
  steps: [{ id: `s-${id}`, filter: "L", exposure_s: 300, gain: 100, offset: 30,
            binning: 1, count: 10, frame_type: "Light" }],
});

const SESSION = {
  id: "sess-1", schema_version: 3, name: "Three targets",
  created_ts: 1, updated_ts: 2, status: "active", nights: ["night-2"],
  auto_resume: true, origin: "", origin_id: "",
  plan: {
    name: "Three targets", guide: true, dither_every: 1, dither_pixels: null,
    autofocus_every: 0, cool_to: -10, cool_timeout_s: 600,
    apply_filter_offsets: null, refocus_on_temp_delta_c: null, meridian_flip: true,
    recover_guiding: null, hfr_reject_factor: 0, park_when_done: true,
    warm_cooler_when_done: true, count_mode: "attempts",
    targets: [target("ta", "Alpha", 0.7), target("tb", "Bravo", 5.5),
              target("tc", "Charlie", 9.1)],
  },
  frames: [],
};

g.fetch = async (url: any) => {
  const u = String(url);
  const body = u.includes("/api/sessions/sess-1") ? SESSION
    : u.includes("/api/sessions") ? { sessions: [{ id: "sess-1", name: "Three targets", status: "active", created_ts: 1, updated_ts: 2, nights: 1, accepted: 0, total: 30, auto_resume: true }] }
      : u.includes("/api/reports") ? []
        : u.includes("/api/flows") ? []
          : { ok: true };
  return {
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => body,
    text: async () => "",
  };
};

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { activePlanTarget } = await import("../../../../lib/planTarget");
const { PoolChips } = await import("../now/PoolChips");
const { VitalsBand } = await import("../now/VitalsBand");
const { runTargetLine } = await import("../now/RunHeader");
const { nextTargetFace } = await import("../../monitor/live/VitalsBand");
const { resetSessionDataForTests } = await import("../now/sessionData");
const { resetCampaignForTests } = await import("../now/useCampaign");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (!Object.is(got, want)) throw new Error(`${msg} expected ${String(want)}, got ${String(got)}`);
}
const settle = async () => {
  for (let i = 0; i < 4; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

const container = win.document.getElementById("root") as any;
const byId = (id: string) => container.querySelector(`[data-testid="${id}"]`) as any;

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.site_derived", "control.capture", "control.mount"],
};

/** The sequence state the engine publishes while `target` exposes at `index`.
 *  `index` "absent" leaves the key out, as a run that has published no
 *  target yet does. */
function seed(target: string, index: number | null | "absent"): void {
  const seq: any = {
    state: "running", target, plan_name: "Three targets",
    session: { id: "sess-1", name: "Three targets", count_mode: "attempts", accepted: 0, target },
    progress: { frames_done: 0, calibration_frames_done: 0, frames_total: 30, percent: 0, elapsed_s: 10, rejected: 0 },
  };
  if (index !== "absent") seq.target_index = index;
  useStore.setState({
    principal: OPERATOR, equipConnected: true, wsPhase: "up",
    status: { connected: {}, looping: false },
    sequence: seq,
  } as never);
}

async function mounted<T>(node: any, read: () => T): Promise<T> {
  resetSessionDataForTests();
  resetCampaignForTests();
  const root = createRoot(container);
  await act(async () => { root.render(node); });
  await settle();
  const out = read();
  await act(async () => { root.unmount(); });
  return out;
}

/** The chip words the Pool shows, by target: "now", "next" or "done". */
function chipWords(): Record<string, string> {
  const out: Record<string, string> = {};
  for (const id of ["ta", "tb", "tc"]) {
    const chip = byId(`pool-${id}`);
    out[id] = chip ? String(chip.textContent).split(" · ")[0] : "missing";
  }
  return out;
}

// ------------------------------------------------- the three things it says
test("activePlanTarget: a number is that target", () => {
  eq(activePlanTarget({ target_index: 0 }), 0, "target 0:");
  eq(activePlanTarget({ target_index: 2 }), 2, "target 2:");
});
test("activePlanTarget: null is NO plan target", () => {
  eq(activePlanTarget({ target_index: null }), null, "null:");
});
test("activePlanTarget: absent is the first target, as every screen has read it", () => {
  eq(activePlanTarget({}), 0, "absent:");
  eq(activePlanTarget({ target_index: undefined }), 0, "undefined:");
  eq(activePlanTarget({ target_index: Number.NaN }), 0, "NaN:");
});

// ------------------------------------------------------------ the run line
test("the run line reads 'k of N' for a plan target", () => {
  eq(runTargetLine({ target: "Bravo", target_index: 1 }, 3), "Bravo · 2 of 3", "target 1:");
  eq(runTargetLine({ target: "Alpha", target_index: 0 }, 3), "Alpha · 1 of 3", "target 0:");
});
test("the run line names the calibration set and no place in the plan", () => {
  eq(runTargetLine({ target: "dusk flats L", target_index: null }, 3), "dusk flats L",
    "through the flats:");
});
test("the run line reads the first target for a run that has published none", () => {
  eq(runTargetLine({ target: "Alpha" }, 3), "Alpha · 1 of 3", "absent:");
});

// ------------------------------------------------ the monitor's NEXT TARGET
const PLAN: any = { targets: [{ name: "Alpha" }, { name: "Bravo" }, { name: "Charlie" }] };
test("the monitor's next target: through DUSK FLATS the first target is next", () => {
  const face = nextTargetFace({ state: "running", target_index: null } as any, PLAN);
  eq(face.value, "Alpha", "the next target through the flats:");
  eq(face.sub, "1 of 3", "its place:");
});
test("the monitor's next target: a cloud-hold dark does not guess one", () => {
  const face = nextTargetFace(
    { state: "holding", hold: "clouds", target_index: null } as any, PLAN);
  eq(face.value, "--", "a target named while the held one is unpublished:");
  eq(face.sub, "held for cloud", "the reason:");
});
test("the monitor's next target: plan targets and a run with none yet are as they were", () => {
  eq(nextTargetFace({ state: "running", target_index: 0 } as any, PLAN).value, "Bravo", "after 0:");
  eq(nextTargetFace({ state: "running", target_index: 2 } as any, PLAN).value, "none queued", "after the last:");
  eq(nextTargetFace({ state: "running" } as any, PLAN).value, "Alpha", "absent:");
  eq(nextTargetFace({ state: "holding", hold: "clouds", target_index: 1 } as any, PLAN).value,
    "Charlie", "a hold on a plan target:");
});

// ------------------------------------------------------------- Pool chips
await testAsync("Pool: a plan target exposing is the one chip that reads 'now'", async () => {
  seed("Bravo", 1);
  const words = await mounted(createElement(PoolChips), chipWords);
  eq(JSON.stringify(words), JSON.stringify({ ta: "next", tb: "now", tc: "next" }),
    "chips at target 1:");
});

await testAsync("Pool: through the flats NO chip reads 'now' (unfixed: the first)", async () => {
  seed("dusk flats L", null);
  const words = await mounted(createElement(PoolChips), chipWords);
  eq(JSON.stringify(words), JSON.stringify({ ta: "next", tb: "next", tc: "next" }),
    "chips while a calibration set exposes:");
});

await testAsync("Pool: a run that has published no target yet still reads the first as 'now'", async () => {
  seed("", "absent");
  const words = await mounted(createElement(PoolChips), chipWords);
  eq(JSON.stringify(words), JSON.stringify({ ta: "now", tb: "next", tc: "next" }),
    "chips with no target_index:");
});

// ------------------------------------------------- vitals band NEXT TARGET
const nextCell = () => {
  const cell = byId("vital-next");
  return cell ? String(cell.textContent) : null;
};

await testAsync("Vitals: the cell after a plan target names the one after it", async () => {
  seed("Alpha", 0);
  const text = await mounted(createElement(VitalsBand, { cells: 7 }), nextCell);
  assert(text != null && /Bravo/.test(text), `NEXT TARGET after target 0 read "${text}"`);
});

await testAsync("Vitals: through the flats there is no NEXT TARGET cell (unfixed: the second target)", async () => {
  seed("dusk flats L", null);
  const text = await mounted(createElement(VitalsBand, { cells: 7 }), nextCell);
  eq(text, null, "the NEXT TARGET cell while a calibration set exposes:");
});

const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`w20ThrowawayTargetIndex.test: ${passed}/${total} passed`);
// eslint-disable-next-line no-console
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
