// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w3NightNumberWithNoRow.test.tsx - WP-25 (c), #430's remaining item 1.
//
//   Run directly:  npx tsx src/next/hubs/session/now/__tests__/w3NightNumberWithNoRow.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// `useCampaign`'s `nightNo` used to read `row?.nights ?? (session?.nights?.length
// ?? 1)` (useCampaign.ts:395). `row` is `GET /api/sessions`' list entry, whose
// `nights` is the observing-night count (#430, H4). `session.nights` is the
// SESSION document's own list, one report id per `engine.start` - a run count,
// not a night count (#430's root defect). When the list row has not arrived yet
// (its own fetch, alongside the session's, can simply be slower) or carries no
// match for this session, the old fallback read that run count as the night
// number, so a same-night restart showed the campaign card one night ahead of
// the log.
//
// The fix (#430's last comment, item 1; WP-25 plan "with no list row, show no
// night number rather than the run count") landed here first as the campaign
// card's OWN sentence (`campaign.summary`, drawn only by `CampaignLedger.tsx`,
// pinned below): with no row, it says the banked/goal/nights-left facts the
// route itself measured and leaves the night number out, rather than guess at
// one it cannot stand behind.
//
// `campaign.night`/`campaign.totalNights` THEMSELVES were widened to
// `number | null` later (#430 remainder, WP-25 new defect, W3 integration):
// `crossHub.ts`'s `CampaignChrome.night`, `RunHeader.tsx`'s target line and
// the Flows list row's campaign meta line all read those fields directly and
// fell back to the same run-count guess `summary` had already stopped
// making. See `w3CampaignNightNullSurfaces.test.tsx` for that half.
//
// NAMED MUTANT, run from a byte copy of useCampaign.ts and restored
// byte-identical afterwards (sha256 checked). The observed failure is quoted at
// the test it turns red.
//   M1 "summary always counts the night" (`nightUnknown` forced to `false`,
//      so a missing row again falls back to `session.nights.length`)

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

// ------------------------------------------------------------- fetch recorder
// Three report-ids-as-runs on the session document, against zero rows in the
// list: if a fallback to `session.nights.length` were still live, the card
// would read "night 3", a number nothing on the engine's own night-keyed
// ledger agrees with. The made-up site carries no real coordinates (project
// rule): this fixture is numbers and strings invented for the test alone.
const SESSION = {
  id: "sess-1", schema_version: 3, name: "W3 Mosaic",
  created_ts: 1, updated_ts: 2, status: "active",
  nights: ["r1", "r2", "r3"], auto_resume: true,
  origin: "flow", origin_id: "flow-1",
  plan: {
    name: "W3 Mosaic", guide: true, dither_every: 1, dither_pixels: null,
    autofocus_every: 0, cool_to: -10, cool_timeout_s: 600,
    apply_filter_offsets: null, refocus_on_temp_delta_c: null, meridian_flip: true,
    recover_guiding: null, hfr_reject_factor: 0, park_when_done: true,
    warm_cooler_when_done: true, count_mode: "accepted",
    targets: [{
      id: "t1", name: "W3-T1", ra_hours: 0.71, dec_deg: 41.2,
      center: true, autofocus_first: true, calibration: false,
      steps: [
        { id: "s-L", filter: "L", exposure_s: 300, gain: 100, offset: 30, binning: 1, count: 12, frame_type: "Light" },
      ],
    }],
  },
  frames: [],
};

/** Toggled per mount: true answers `GET /api/sessions` with this session's row,
 *  false answers it with an empty list - the row has not arrived, or never
 *  matched. Either way `useActiveSession`'s `row` comes back null. */
let rowPresent = false;

const asks: string[] = [];
const tonightBudget: unknown[] = [
  { filter: "L", goal_h: 6, banked_h: 1.2, tonight_h: 0.5, has_ledger: true },
];

g.fetch = async (url: any, init: any) => {
  asks.push(`${(init?.method ?? "GET").toUpperCase()} ${String(url)}`);
  const u = String(url);
  const body = u.includes("/api/sessions/sess-1") ? SESSION
    : u.includes("/api/sessions") ? {
      sessions: rowPresent
        ? [{ id: "sess-1", name: "W3 Mosaic", status: "active", created_ts: 1, updated_ts: 2, nights: 1, accepted: 1, total: 12, auto_resume: true }]
        : [],
    }
      : u.includes("/tonight") ? { ok: true, reason: "", budget: tonightBudget, night: { dusk_unix: 1, dawn_unix: 2, dark_start_unix: 1, dark_end_unix: 2 } }
        : u.includes("/api/reports") ? []
          : u.includes("/api/flows") ? [{ id: "flow-1", name: "W3 Mosaic", folder: "", tagline: "", readonly: false, stages: 7, wires: 6, last_run: 1, last_result: "ok", updated_ts: 1 }]
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
const { useStore } = await import("../../../../../store");
const { CampaignLedger } = await import("../CampaignLedger");
const { resetCampaignForTests } = await import("../useCampaign");
const { resetSessionDataForTests } = await import("../sessionData");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
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

function seed(): void {
  useStore.setState({
    principal: OPERATOR,
    equipConnected: true,
    wsPhase: "up",
    status: { connected: {}, looping: false },
    sequence: {
      state: "running", target: "W3-T1", plan_name: "W3 Mosaic", target_index: 0,
      session: { id: "sess-1", name: "W3 Mosaic", count_mode: "accepted", accepted: 1, target: "W3-T1" },
      progress: { frames_done: 1, frames_total: 12, percent: 8, elapsed_s: 300, rejected: 0 },
    },
    flows: {
      ...(useStore.getState() as any).flows,
      cards: [{ id: "flow-1", name: "W3 Mosaic", folder: "", tagline: "", readonly: false, stages: 7, wires: 6, last_run: 1, last_result: "ok", updated_ts: 1 }],
      libraryLoaded: true, libraryError: null,
    },
  } as never);
}

async function mount(): Promise<{ root: ReturnType<typeof createRoot> }> {
  const root = createRoot(container);
  await act(async () => { root.render(createElement(CampaignLedger)); });
  await settle();
  return { root };
}

// ---------------------------------------------- 1. no row: no night number
rowPresent = false;
resetCampaignForTests();
resetSessionDataForTests();
seed();
let mounted = await mount();

await testAsync("precondition: the ledger still draws with no list row - a session read failure, not this rule", async () => {
  assert(byId("now-campaign-ledger") != null,
    "no campaign ledger at all: the flow-id resolution or the tonight fetch broke, not the night-number rule");
});

await testAsync("with no list row, the summary names no night and makes no run-count guess", async () => {
  // M1 "summary always counts the night", observed:
  //   x with no list row, the summary names no night and makes no run-count
  //   guess: the summary guessed a night number with no row to back it:
  //   "night 3 of ~5 · 1.2 of 6 h · ~2 clear nights left"
  const card = byId("now-campaign-ledger").textContent as string;
  assert(!/night\s+\d+\s+of/.test(card),
    `the summary guessed a night number with no row to back it: "${card}"`);
  // What IS measured stays on the card: the bank, the goal and the clear-night
  // projection are the route's own numbers, not derived from the night count.
  assert(/1\.2 of 6 h/.test(card), `the banked/goal figures dropped too: "${card}"`);
  assert(/clear night/.test(card), `the clear-nights-left projection dropped too: "${card}"`);
});

await act(async () => { mounted.root.unmount(); });

// ------------------------------------------- 2. control: a row IS present
rowPresent = true;
resetCampaignForTests();
resetSessionDataForTests();
seed();
mounted = await mount();

await testAsync("control: with the list row present, the summary states its night number", async () => {
  const card = byId("now-campaign-ledger").textContent as string;
  // The row says `nights: 1`; the session's own `nights` (length 3, a run
  // count) must not be what is shown instead.
  assert(/night 1 of ~/.test(card), `the row's own night count is not shown: "${card}"`);
});

await act(async () => { mounted.root.unmount(); });

const total = passed + failed;
console.log(`w3NightNumberWithNoRow.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
