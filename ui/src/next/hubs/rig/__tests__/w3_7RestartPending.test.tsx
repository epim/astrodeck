// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w3_7RestartPending.test.tsx - the SAFETY sheet says a Windows restart is
// pending, with the arm gate's own reason (WP 3.7, #1076).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx <this file>
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT THIS IS ABOUT. Windows Update restarted the rig PC at 08:43 and nothing
// said a restart was waiting. The server now puts `restart` on the status frame
// (`pending`, `sources`, the active hours, and `arm_refusal`, the arm gate's
// own sentence). The SAFETY sheet, where the rig's other availability holds
// are shown, prints it:
//
//   1. nothing pending: no restart card at all (a card that always shows is
//      a card nobody reads);
//   2. pending and the gate would refuse: the banner carries RESTART PENDING
//      and the server's sentence VERBATIM, not a second wording of it;
//   3. pending but the night is inside the active hours: the banner still says
//      so, and says why it is not a refusal;
//   4. a server too old to send the block: no card, no crash.
//   5. the ACKNOWLEDGE control: shown to a role that can start a night, not to
//      one that cannot, gone once the server says acknowledged, and pressing it
//      POSTs /api/rig/restart/acknowledge (the stored acknowledgement the start
//      routes, the quick flow and auto-resume all honour).
//
// The sheet is mounted whole, so a card the sheet never rendered cannot pass.
//
// NAMED MUTANTS: the card's gate `status?.restart?.pending` read as
// `status?.restart` turns case 1 red (a card on a rig with nothing pending);
// the control's `!status.restart.acknowledged` dropped turns the
// disappearance case red.

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
  { url: "http://local/#/rig/devices/safety", pretendToBeVisual: true },
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
  "MouseEvent", "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "IS_REACT_ACT_ENVIRONMENT",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const SEED_SAFETY = {
  enabled: true, max_alt_deg: 90, horizon: null, nogo_box: null,
  enforce_pier_limits: false, twilight_deg: -12, solar_avoidance: true,
  solar_exclusion_deg: 30, on_unsafe: "pause", unsafe_consecutive: 3,
  resume_when_safe: true, resume_safe_consecutive: 3, max_pause_min: 120,
  close_dome_on_unsafe: false, close_dome_when_done: false,
  reopen_dome_when_safe: false,
};
const CONFIG = {
  version: 1, site: { name: "home", latitude: 34, longitude: -116, elevation_m: 20 },
  optics: { focal_length_mm: 500, aperture_mm: 100 }, active_profile_id: null,
  safety: SEED_SAFETY,
  cooling: { warm_ramp: true, warm_rate_c_per_min: 2, warm_ambient_c: null },
  escalation: {
    require_cooling: true, cooling_action: "abort", require_guiding: false,
    guiding_action: "warn", af_failure_action: "skip", hfr_reject_action: "retake",
    hfr_retake_limit_per_target: 4, hfr_reject_factor: 1.5, no_progress_watchdog_s: 900,
    reconnect_resume: true, reconnect_retries: 3,
  },
  alerts: [], deadman_url: "",
};

const asked: string[] = [];
g.fetch = async (url: string, init?: { method?: string }) => {
  const u = String(url);
  asked.push(`${init?.method ?? "GET"} ${u}`);
  const ok = (data: unknown) => ({ ok: true, status: 200, statusText: "OK", json: async () => data });
  if (u.includes("/api/dome/state")) {
    return ok({ connected: false, shutter: "unknown", requires_park_before_close: true, can_bind: false });
  }
  if (u.includes("/api/alerts/health")) {
    return ok({ undelivered: 0, undelivered_by_sink: {}, deadman: { configured: false } });
  }
  if (u.includes("/api/config")) return ok(CONFIG);
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { SafetySheet, restartSentence } = await import("../sheets/safety");

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const ADMIN = {
  role: "admin", email: "admin@rig",
  caps: ["view.status", "view.preview", "control.mount", "config.safety", "config.alerts"],
};
const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
const q = (sel: string) => container.querySelector(sel) as any;

const REFUSAL = "Windows has a restart pending (windows_update) and tonight runs "
  + "outside Windows Update's active hours (08:00 to 17:00), so it may restart "
  + "this PC in the middle of the run. Restart the PC now, or set the Windows "
  + "Update active hours to cover the night, then arm again. To start anyway, "
  + "press ACKNOWLEDGE on the Windows restart card in Rig > Safety: nights then "
  + "start despite it until Windows has restarted.";

const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };

async function mountWith(restart: unknown, principal: unknown = ADMIN): Promise<void> {
  if (rootRef) act(() => { rootRef!.unmount(); });
  const status: Record<string, unknown> = {
    connected: {}, looping: false, mode: "sim", busy_lanes: [],
    meridian: { status: "counting", hours_to_flip: 1.63, flip_enabled: true, pier_side: "east" },
  };
  if (restart !== undefined) status.restart = restart;
  act(() => {
    useStore.setState({
      wsPhase: "up", equipConnected: true, principal, authGate: "open",
      toasts: [], confirm: null, config: JSON.parse(JSON.stringify(CONFIG)),
      status,
      safety: {
        connected: true,
        reading: {
          is_safe: true, reason: "", source: "Boltwood II", stale: false,
          ts: Math.round(Date.now() / 1000), detail: { wind_kmh: 12, humidity: 64 },
        },
        streak: 0,
      },
    } as never);
  });
  rootRef = createRoot(container);
  act(() => { rootRef!.render(createElement(SafetySheet as any, { params: {}, depth: 0 })); });
  await settle();
  assert(q('[data-testid="rig-safety"]') != null && q('[data-testid="safety-deadman"]') != null,
    "precondition: the sheet did not render");
}

await testAsync("nothing pending shows no restart card", async () => {
  await mountWith({ pending: false, sources: [], active_hours: { start: 8, end: 17 }, acknowledged: false, arm_refusal: null });
  assert(q('[data-testid="safety-restart"]') == null, "a restart card on a rig with nothing pending");
});

await testAsync("a pending restart the gate would refuse shows the server's sentence", async () => {
  await mountWith({
    pending: true, sources: ["windows_update"],
    active_hours: { start: 8, end: 17 }, acknowledged: false, arm_refusal: REFUSAL,
  });
  const banner = q('[data-testid="safety-restart-pending"]');
  assert(banner != null, "no restart banner with a restart pending");
  const said = (banner.textContent || "").trim();
  assert(said.startsWith("RESTART PENDING."), `the banner does not say RESTART PENDING: ${said}`);
  assert(said.includes(REFUSAL), `the banner is not the gate's own sentence: ${said}`);
});

await testAsync("a pending restart inside the active hours says why it is not a refusal", async () => {
  await mountWith({
    pending: true, sources: ["component_servicing"],
    active_hours: { start: 16, end: 10 }, acknowledged: false, arm_refusal: null,
  });
  const said = (q('[data-testid="safety-restart-pending"]')?.textContent || "").trim();
  assert(said.startsWith("RESTART PENDING (component_servicing)."), said);
  assert(/inside Windows Update's active hours/.test(said), said);
});

await testAsync("a server that sends no restart block shows no card and does not crash", async () => {
  await mountWith(undefined);
  assert(q('[data-testid="safety-restart"]') == null, "a restart card with no data");
});

const PENDING = {
  pending: true, sources: ["windows_update"], active_hours: { start: 8, end: 17 },
  acknowledged: false, arm_refusal: REFUSAL,
};
const click = (node: any) => act(() => {
  node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
});

await testAsync("an operator sees ACKNOWLEDGE, a viewer does not", async () => {
  await mountWith(PENDING);
  assert(q('[data-testid="safety-restart-acknowledge"]') != null, "no ACKNOWLEDGE control for an operator");
  await mountWith(PENDING, VIEWER);
  assert(q('[data-testid="safety-restart"]') != null, "precondition: the viewer sees the card");
  assert(q('[data-testid="safety-restart-acknowledge"]') == null, "a viewer was offered ACKNOWLEDGE");
});

await testAsync("pressing ACKNOWLEDGE posts the acknowledgement", async () => {
  await mountWith(PENDING);
  const before = asked.length;
  click(q('[data-testid="safety-restart-acknowledge"]'));
  await settle();
  const posts = asked.slice(before).filter((a) => a.startsWith("POST "));
  assert(posts.length === 1 && posts[0].endsWith("/api/rig/restart/acknowledge"),
    `expected one POST to the acknowledge route, got ${JSON.stringify(posts)}`);
  assert(q('[data-testid="safety-restart-acknowledge"]') == null,
    "the control stayed after a successful acknowledge");
});

await testAsync("once the server says acknowledged the control is gone and the banner says so", async () => {
  await mountWith({ ...PENDING, acknowledged: true, arm_refusal: null });
  assert(q('[data-testid="safety-restart-acknowledge"]') == null, "ACKNOWLEDGE shown after it was acknowledged");
  const said = (q('[data-testid="safety-restart-pending"]')?.textContent || "").trim();
  assert(said.startsWith("RESTART PENDING (windows_update). Acknowledged:"), said);
});

await testAsync("restartSentence is the sentence, not a rewording", async () => {
  const r = { pending: true, sources: ["windows_update"], active_hours: { start: 8, end: 17 }, acknowledged: false, arm_refusal: "X Y Z" };
  assert(restartSentence(r) === "RESTART PENDING. X Y Z", restartSentence(r));
});

act(() => { rootRef?.unmount(); });

const total = passed + failed;
console.log(`w3_7RestartPending.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
