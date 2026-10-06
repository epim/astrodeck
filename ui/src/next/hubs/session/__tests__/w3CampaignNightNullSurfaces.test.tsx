// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w3CampaignNightNullSurfaces.test.tsx - #430 remainder (WP-25 new defect),
// backlog wave 3 integration.
//
//   Run directly:  npx tsx src/next/hubs/session/__tests__/w3CampaignNightNullSurfaces.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. `w3NightNumberWithNoRow.test.tsx` pinned that `useCampaign`'s own
// `summary` sentence drops its night number when the active session has no
// matching `GET /api/sessions` list row yet (`nightUnknown`) - but
// `CampaignRead.night`/`totalNights` THEMSELVES stayed plain `number`s, still
// carrying `nightNo`'s fallback to `session.nights.length` (a RUN count, #430's
// root defect, not an observing-night count). Three surfaces read those two
// fields DIRECTLY rather than through `summary`, and so kept guessing a night
// number the route never measured:
//
//   * `RunHeader.tsx`'s target line ("campaign night N of ~M")
//   * `crossHub.ts`'s `CampaignChrome` (the cross-hub strip's "night N of ~M")
//   * the Flows list row's campaign meta line ("campaign · night N of ~M")
//
// THE FIX. `CampaignRead.night`/`totalNights` are now `number | null`, null
// under exactly `nightUnknown`, and all three surfaces gate on the null the
// same way `summary` already did: no night number is shown until the session
// has a list row to count it from.
//
// Convention: harness shape from `crossHub.test.ts` (the Probe pattern) and
// `w3NightNumberWithNoRow.test.tsx` (the "session but no row" fixture).

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
{
  const { registerHooks } = await import("node:module");
  if (typeof registerHooks === "function") {
    registerHooks({
      load(url: string, context: any, nextLoad: any) {
        if (url.endsWith(".css")) {
          return { format: "module", shortCircuit: true, source: "export default {};" };
        }
        return nextLoad(url, context);
      },
    } as any);
  }
}

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
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "localStorage", "sessionStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "requestAnimationFrame", "cancelAnimationFrame",
  "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- the fake rig
// The same "session exists, no list row" shape `w3NightNumberWithNoRow.test.tsx`
// uses: three report ids on the session document (a run count of 3), zero rows
// in the list, so `nightNo`'s old fallback would print "night 3".
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

const CARD = {
  id: "flow-1", name: "W3 Mosaic", folder: "My flows", tagline: "a campaign",
  readonly: false, stages: 7, wires: 6, last_run: 1, last_result: "ok", updated_ts: 1,
};

const TONIGHT_OK = {
  ok: true, reason: "",
  night: { dusk_unix: 1, dawn_unix: 2, dark_start_unix: 1, dark_end_unix: 2 },
  budget: [{ filter: "L", goal_h: 6, banked_h: 1.2, tonight_h: 0.5, has_ledger: true }],
};

const asked: string[] = [];
g.fetch = async (url: any, init: any) => {
  const u = String(url);
  asked.push(`${(init?.method ?? "GET").toUpperCase()} ${u}`);
  const ok = (data: unknown) => ({
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => data, text: async () => "",
  });
  if (u.includes("/api/sessions/sess-1")) return ok(SESSION);
  if (u.includes("/api/sessions")) return ok({ sessions: [] }); // no row, ever
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
const { resetCampaignForTests } = await import("../now/useCampaign");
const { resetSessionDataForTests } = await import("../now/sessionData");
const cross = await import("../crossHub");
const { RunHeader } = await import("../now/RunHeader");
const { FlowsScreen } = await import("../flows/FlowsScreen");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void> | void): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
const container = win.document.getElementById("root") as any;
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

/** `sequence.state: "idle"` on purpose: the point under test is the NIGHT
 *  NUMBER, not the live/parked distinction, and idle keeps `camp.live` and
 *  `camp.parked` both false so every surface takes its plain "campaign ·
 *  banked" form rather than the running or parked ones. */
function seed(): void {
  act(() => {
    const f = useStore.getState().flows as any;
    useStore.setState({
      principal: ADMIN,
      authGate: "open",
      equipConnected: true,
      wsPhase: "up",
      status: { connected: { camera: { connected: true } } },
      resumeArm: null,
      toasts: [],
      sequence: {
        state: "idle", target: "W3-T1", plan_name: "W3 Mosaic", target_index: 0,
        session: { id: "sess-1", name: "W3 Mosaic", count_mode: "accepted", accepted: 1, target: "W3-T1" },
      },
      flows: {
        ...f,
        record: null, dirty: false,
        run: { ...f.run, phase: "idle" },
        ui: { ...f.ui, screen: "library", query: "", folderChip: "all", highlightId: null },
        cards: [CARD], libraryLoaded: true, libraryError: null,
      },
    } as never);
  });
}

function reset(): void {
  resetCampaignForTests();
  resetSessionDataForTests();
  asked.length = 0;
}

// =========================================== 1. crossHub.ts: CampaignChrome
await testAsync("crossHub: no list row, no night number on the chrome or the strip", async () => {
  reset();
  seed();
  let seen: { camp: ReturnType<typeof cross.useSessionCampaign>; strip: ReturnType<typeof cross.useCampaignStrip> } =
    { camp: null, strip: null };
  function Probe(): null {
    seen = { camp: cross.useSessionCampaign(), strip: cross.useCampaignStrip() };
    return null;
  }
  win.location.hash = "#/weather/conditions"; // any hub but Session - Now
  const root = createRoot(container);
  await act(async () => { root.render(createElement(Probe)); });
  await settle();
  try {
    assert(seen.camp != null, "precondition: a campaign must resolve at all");
    assert(seen.camp!.night === null, `CampaignChrome.night must be null with no list row, got ${seen.camp!.night}`);
    assert(seen.camp!.totalNights === null, `CampaignChrome.totalNights must be null, got ${seen.camp!.totalNights}`);
    assert(seen.strip != null, "precondition: the strip must render for a campaign");
    assert(!/night\s+\d+\s+of/.test(seen.strip!.line),
      `the strip guessed a night number with no row to back it: "${seen.strip!.line}"`);
    assert(/1\.2 of 6 h captured/.test(seen.strip!.line), `the banked figures dropped too: "${seen.strip!.line}"`);
  } finally {
    await act(async () => { root.unmount(); });
  }
});

// ================================================== 2. RunHeader.tsx
await testAsync("RunHeader: no list row, no night number on the target line", async () => {
  reset();
  seed();
  const root = createRoot(container);
  await act(async () => { root.render(createElement(RunHeader, {})); });
  await settle();
  try {
    const line = container.querySelector('[data-testid="now-target-line"]')?.textContent ?? "";
    assert(!/campaign night/.test(line),
      `the target line guessed a night number with no row to back it: "${line}"`);
    assert(line.length > 0, "the target line must still say something - a fallback, not a blank");
  } finally {
    await act(async () => { root.unmount(); });
  }
});

// ================================================= 3. the Flows list row
await testAsync("FlowsScreen: no list row, no night number on the campaign row's meta line", async () => {
  reset();
  seed();
  win.location.hash = "#/session/flows";
  const root = createRoot(container);
  await act(async () => { root.render(createElement(FlowsScreen as any)); });
  await settle();
  try {
    const meta = container.querySelector('[data-testid="flow-meta-flow-1"]')?.textContent ?? "";
    assert(meta.length > 0, "precondition: the campaign row's meta line must render");
    assert(!/night\s+\d+\s+of/.test(meta),
      `the row's meta line guessed a night number with no row to back it: "${meta}"`);
    assert(/1\.2 of 6 h/.test(meta), `the banked figures dropped too: "${meta}"`);
  } finally {
    await act(async () => { root.unmount(); });
  }
});

// MUTATION RECORD, 2026-10-01 (W3 integration, #430 remainder / WP-25 new
// defect), from a byte backup of useCampaign.ts restored byte-identically.
// Output verbatim.
//
//   MUTANT "night/totalNights always counted" (useCampaign.ts's return:
//   `night: nightUnknown ? null : nightNo` and `totalNights: nightUnknown ?
//   null : totalNights` both reverted to their pre-fix unconditional values,
//   `night: nightNo` and `totalNights,` - the code as it was before this
//   fix). Observed ("w3CampaignNightNullSurfaces.test: 0/3 passed"):
//     x crossHub: no list row, no night number on the chrome or the strip:
//     CampaignChrome.night must be null with no list row, got 3
//     x RunHeader: no list row, no night number on the target line: the
//     target line guessed a night number with no row to back it: "W3-T1 ·
//     campaign night 3 of ~5"
//     x FlowsScreen: no list row, no night number on the campaign row's meta
//     line: the row's meta line guessed a night number with no row to back
//     it: "campaign · night 3 of ~5 · 1.2 of 6 h banked"

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`w3CampaignNightNullSurfaces.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
export default { passed, failed, total };
export { passed, failed, total };
