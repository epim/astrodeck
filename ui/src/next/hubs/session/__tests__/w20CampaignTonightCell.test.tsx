// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w20CampaignTonightCell.test.tsx - the campaign ledger's night strip carries a
// "tonight" cell for EVERY live run state, an abort's wind-down included (#931,
// WP-174, wave 20).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/session/__tests__/w20CampaignTonightCell.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. `useCampaign` pushed the "tonight" cell only for
// `running || holding || paused`. It left out "aborting", the ~210 s wind-down
// (types.ts) in which the run is still live and `crossHub.ts` says so. Tonight's
// hours are folded into the campaign's banked total from the LIVE ledger
// whatever the state, so for that stretch the strip drew a total that counted
// hours none of its cells showed, and the cell came back as a "past" night only
// when the report landed. It now asks `runIsLive`.
//
// WHAT IS WORTH ASSERTING. The hook itself, mounted through a probe, over every
// state of the union: the cell is there with the hours the ledger holds for the
// live states and absent for the rest (the planned cells always follow it, so
// "the strip has cells" would be true of a predicate that is always false). The
// map is a `Record` over the union, so a new state is a compile error here until
// someone decides what the strip does for it. And the figure: the cell's hours
// equal `tonightH`, the part of the total it stands for.
//
// MUTANT "aborting is not live" (`runIsLive(seq)` in the cell's `if` made
// `seq.state === "running" || seq.state === "holding" || seq.state === "paused"`,
// the unfixed text). Run from a byte backup of useCampaign.ts, restored with a
// byte copy (md5sum compared): see the report for the failing line.
//
// Convention: harness shape from w3CampaignNightNullSurfaces.test.tsx.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
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

// ------------------------------------------------------------- the fake rig
// A flow-origin session with SIX accepted 300 s L frames banked tonight (its
// last night is "r3"): 6 x 300 s = 0.5 h, so tonight's cell reads "tonight 0.5h"
// and the total is the archive's 1.2 h plus those 0.5.
const frame = (i: number) => ({
  id: `f${i}`, ts: 1000 + i, night: "r3", target_id: "t1", step_id: "s-L",
  thumb: null, metrics: {}, auto_accepted: true, override: null,
});
const SESSION = {
  id: "sess-1", schema_version: 3, name: "W20 Campaign",
  created_ts: 1, updated_ts: 2, status: "active",
  nights: ["r1", "r2", "r3"], auto_resume: true,
  origin: "flow", origin_id: "flow-1",
  plan: {
    name: "W20 Campaign", guide: true, dither_every: 1, dither_pixels: null,
    autofocus_every: 0, cool_to: -10, cool_timeout_s: 600,
    apply_filter_offsets: null, refocus_on_temp_delta_c: null, meridian_flip: true,
    recover_guiding: null, hfr_reject_factor: 0, park_when_done: true,
    warm_cooler_when_done: true, count_mode: "accepted",
    targets: [{
      id: "t1", name: "W20-T1", ra_hours: 0.71, dec_deg: 41.2,
      center: true, autofocus_first: true, calibration: false,
      steps: [
        { id: "s-L", filter: "L", exposure_s: 300, gain: 100, offset: 30, binning: 1, count: 12, frame_type: "Light" },
      ],
    }],
  },
  frames: [0, 1, 2, 3, 4, 5].map(frame),
};

const CARD = {
  id: "flow-1", name: "W20 Campaign", folder: "My flows", tagline: "a campaign",
  readonly: false, stages: 7, wires: 6, last_run: 1, last_result: "ok", updated_ts: 1,
};

const TONIGHT_OK = {
  ok: true, reason: "",
  night: { dusk_unix: 1, dawn_unix: 2, dark_start_unix: 1, dark_end_unix: 2 },
  budget: [{ filter: "L", goal_h: 6, banked_h: 1.2, tonight_h: 0.5, has_ledger: true }],
};

g.fetch = async (url: any) => {
  const u = String(url);
  const ok = (data: unknown) => ({
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => data, text: async () => "",
  });
  if (u.includes("/api/sessions/sess-1")) return ok(SESSION);
  if (u.includes("/api/sessions")) return ok({ sessions: [] });
  if (u.includes("/api/flows/flow-1/tonight")) return ok(TONIGHT_OK);
  if (u.includes("/api/flows/folders")) return ok([{ name: "My flows", count: 1, readonly: false }]);
  if (u.includes("/api/flows")) return ok([CARD]);
  if (u.includes("/api/reports")) return ok([]);
  return ok({ ok: true });
};

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { useCampaign, resetCampaignForTests } = await import("../now/useCampaign");
const { resetSessionDataForTests } = await import("../now/sessionData");
type SeqState = import("../../../../types").SequenceState["state"];

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void> | void): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const settle = async () => {
  for (let i = 0; i < 8; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

const ADMIN = {
  role: "admin", email: null,
  caps: ["view.status", "view.preview", "view.weather", "view.site_derived",
    "control.capture", "control.mount"],
};

let lastRead: ReturnType<typeof useCampaign> | null = null;
function Probe() {
  lastRead = useCampaign();
  return null;
}
/** Read through a call: TypeScript narrows `lastRead` to `null` after the reset
 *  in `mountAt` and cannot see the probe assign it during the awaits. */
const probed = (): ReturnType<typeof useCampaign> | null => lastRead;

/** A fresh mount of the hook with the engine in `state`. */
async function mountAt(state: SeqState): Promise<NonNullable<ReturnType<typeof useCampaign>["campaign"]>> {
  await act(async () => { root.render(createElement("div")); });
  resetCampaignForTests();
  resetSessionDataForTests();
  lastRead = null;
  act(() => {
    useStore.setState({
      principal: ADMIN,
      authGate: "open",
      equipConnected: true,
      wsPhase: "up",
      status: { connected: { camera: { connected: true } } },
      resumeArm: null,
      sequence: {
        state, target: "W20-T1", plan_name: "W20 Campaign", target_index: 0,
        session: { id: "sess-1", name: "W20 Campaign", count_mode: "accepted", accepted: 6, target: "W20-T1" },
      },
      flows: {
        ...(useStore.getState().flows as any),
        cards: [CARD], libraryLoaded: true, libraryError: null,
      },
    } as never);
  });
  await act(async () => { root.render(createElement(Probe)); });
  await settle();
  const got = probed();
  const c = got?.campaign ?? null;
  assert(c != null, `the campaign never resolved while the run is ${state} `
    + `(refused=${JSON.stringify(got?.refused)}, error=${JSON.stringify(got?.error)}) - the fixture is wrong, not the strip`);
  return c!;
}

/** Does the night strip hold a "tonight" cell in each state of the union? A
 *  `Record`, so a new member of the union does not compile until it is placed. */
const HAS_TONIGHT: Record<SeqState, boolean> = {
  running: true, paused: true, holding: true, aborting: true,
  idle: false, complete: false, aborted: false, error: false, nina_native: false,
};

for (const state of Object.keys(HAS_TONIGHT) as SeqState[]) {
  const has = HAS_TONIGHT[state];
  await test(
    has
      ? `${state}: a run is live, the strip holds tonight's cell with its hours`
      : `${state}: no run is live, the strip holds no tonight cell`,
    async () => {
      const c = await mountAt(state);
      const cells = c.nights.filter((n) => n.kind === "tonight");
      if (!has) {
        assert(cells.length === 0,
          `a tonight cell is drawn while the run is ${state}: ${JSON.stringify(cells)}`);
        return;
      }
      assert(cells.length === 1,
        `${cells.length} tonight cells while the run is ${state} (want 1); the strip is `
        + `${JSON.stringify(c.nights.map((n) => n.label))}`);
      assert(cells[0].label === "tonight 0.5h",
        `the tonight cell does not carry the ledger's hours while the run is ${state}: "${cells[0].label}"`);
      assert(Math.abs(c.tonightH - 0.5) < 1e-9, `tonightH is ${c.tonightH}, want 0.5`);
      assert(Math.abs(c.bankedH - 1.7) < 1e-9,
        `bankedH is ${c.bankedH}: the total no longer counts tonight's 0.5 h beside the archive's 1.2`);
    },
  );
}

await test("the tonight cell sits before the planned nights, in every live state", async () => {
  for (const state of ["running", "paused", "holding", "aborting"] as SeqState[]) {
    const c = await mountAt(state);
    const kinds = c.nights.map((n) => n.kind);
    const at = kinds.indexOf("tonight");
    assert(at >= 0, `no tonight cell while the run is ${state}`);
    assert(kinds.slice(at + 1).every((k) => k === "planned"),
      `a past night follows the tonight cell while the run is ${state}: ${JSON.stringify(kinds)}`);
  }
});

act(() => { root.unmount(); });

const total = passed + failed;
console.log(`w20CampaignTonightCell: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
