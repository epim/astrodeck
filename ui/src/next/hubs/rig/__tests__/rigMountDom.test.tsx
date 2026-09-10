// rigMountDom.test.tsx - the MOUNT device sheet, MOUNTED (plan hub-rig.md D.2,
// T-RIG-3 row).
//
//   Run directly:  npx tsx src/next/hubs/rig/__tests__/rigMountDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// FIVE DECISIONS ARE UNDER TEST, and each one is here because getting it wrong
// has a name:
//
//   1. The sheet mounted the real instruments - four tiles, the pad and the
//      three actions. Without this precondition every assertion below could
//      pass on a blank page (astrodeck-ui-probe-traps: a broken probe reads as
//      a passing app).
//   2. Picking a rate on the dial POSTs the rate route AND paints the choice
//      before the 2 s status frame catches up (`usePendingValue`). A control
//      that sits unmoved for two seconds is how a user learns to press twice.
//   3. PARK reaches `/api/mount/park`, and stays reachable for a viewer only as
//      an explanation - never as a request.
//   4. UNMOUNTING THE SHEET WITH NO FINGER ON THE PAD SENDS NO
//      `/api/mount/stop`. That POST is not a local control: server-side it
//      bumps the global motion epoch, and on 2026-08-07 an unconditional
//      version of this reflex aborted a running polar alignment because
//      somebody backgrounded the tab. A sheet is dismissable, so this sheet
//      unmounts the pad constantly - the guard is load-bearing HERE.
//   5. A run owning the mount locks the pad but NOT park. Park is the abort;
//      gating it on the thing it exists to end is the bug the inventory records
//      three separate times.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/rig/devices/mount", pretendToBeVisual: true },
);
const win = dom.window as any;

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
// The pad binds a slew to a finger with pointer capture; jsdom implements none
// of it. Stubbed, not assumed - SlewPad VERIFIES the capture it asked for.
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.hasPointerCapture = function () { return true; };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "Image", "localStorage", "getComputedStyle", "matchMedia",
  "WebSocket", "requestAnimationFrame", "cancelAnimationFrame", "ResizeObserver",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- fetch recorder
const asked: string[] = [];
g.fetch = async (url: string, init?: { method?: string }) => {
  asked.push(`${init?.method ?? "GET"} ${String(url)}`);
  if (String(url).includes("/api/catalog")) {
    return { ok: true, status: 200, statusText: "OK", json: async () => [] };
  }
  return { ok: true, status: 200, statusText: "OK", json: async () => ({}) };
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { MountSheet, FLOW_OWNS_MOUNT, fmtArcmin, fmtFlipIn, polarRowSub } =
  await import("../sheets/mount");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}
const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

// ------------------------------------------------------------------ fixtures
const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.site_derived", "control.capture",
    "control.mount", "control.guide"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };

function mountStatus(over: Record<string, unknown> = {}) {
  return {
    connected: { telescope: { connected: true, name: "ZWO AM5N" } },
    looping: false,
    busy_lanes: [],
    busy: null,
    backend_links: [{ role: "telescope", connected: true, error: null }],
    mount: {
      ra_hours: 20.5, dec_deg: 60.1, ra_str: "20h 30m", dec_str: "+60° 06'",
      alt: 62, az: 41, tracking: true, parked: false, slewing: false,
      tracking_rate: "sidereal", can_set_tracking_rate: true, can_find_home: true,
      pointing: { verified: false, reason: "solve failed: not enough stars", error_arcmin: 2.4 },
      ...over,
    },
    meridian: {
      status: "counting", hours_to_flip: 1.63, flip_enabled: true, pier_side: "east",
    },
  };
}

function seed(over: Record<string, unknown> = {}): void {
  act(() => {
    useStore.setState({
      status: mountStatus(),
      equipConnected: true,
      wsPhase: "up",
      principal: OPERATOR,
      sequence: { state: "idle" },
      polar: {
        state: "idle", az_error: 0, alt_error: 0, total_error: 0,
        progress: 0, message: "", source: null,
      },
      logs: [],
      config: { safety: { solar_avoidance: true, solar_exclusion_deg: 30 } },
      mountOp: null,
      toasts: [],
      locked: false,
      ...over,
    } as never);
  });
}

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
function mount(): void {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  act(() => { rootRef!.render(createElement(MountSheet as any, { params: {}, depth: 0 })); });
}
function click(node: any): void {
  assert(node != null, "click target missing");
  act(() => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
const q = (sel: string) => container.querySelector(sel) as any;
const text = () => (container.textContent || "") as string;
const posted = (needle: string) => asked.some((a) => a === `POST ${needle}`);

// ============================================================ 1. precondition
seed();
mount();

test("the sheet mounted, with the tiles, the pad and the actions on it", () => {
  assert(q('[data-testid="rig-mount"]') != null,
    "no sheet marker - the sheet did not render at all");
  eq(container.querySelectorAll('[data-testid="mount-tiles"] .nx-readout').length, 4,
    "the four readout tiles are not on screen");
  assert(q('[data-testid="tile-tracking"]') != null && q('[data-testid="tile-pier"]') != null
    && q('[data-testid="tile-altaz"]') != null && q('[data-testid="tile-pointing"]') != null,
    "the tiles are present but not the four the plan names");
  assert(q('[data-testid="mount-dial"]') != null, "the one dial is missing");
  // The pad, by something only SlewPad renders: its STOP bar and its N/S/E/W keys.
  assert(q('[aria-label="Stop all mount motion"]') != null,
    "the pad's STOP bar is not on screen - SlewPad did not mount");
  assert(/RATE|GUIDE|SID/.test(text()),
    "the pad's rate selector is missing - the sheet mounted a shell, not the pad");
  assert(q('[data-testid="mount-park"]') != null && q('[data-testid="mount-home"]') != null
    && q('[data-testid="mount-solve"]') != null,
    "the three action buttons are not all present");
  assert(q('[data-testid="mount-polar-row"]') != null, "no POLAR ALIGNMENT row");
});

test("the 64 px pad rule is published, and aimed at the pad this sheet mounts", () => {
  // jsdom lays nothing out and this test loads no Tailwind, so the SIZE cannot
  // be measured here. What CAN be graded are the two ways the rule silently
  // stops working: the style block disappearing, and the wrapper class being
  // renamed out from under the selector.
  const style = [...container.querySelectorAll("style")]
    .map((s: any) => s.textContent || "").join("\n");
  assert(/64px/.test(style), "the pad's 64 px rule is not published by the sheet");
  assert(/\.nx-mount-pad button\.tap-lg/.test(style),
    "the pad rule does not target the pad's own arrow class");
  assert(/:root:not\(\.no-touch-ui\)/.test(style),
    "the pad rule is below index.css's coarse-pointer .tap-lg rule and would never apply");
  const pad = q('[data-testid="mount-pad"]');
  assert((pad.className || "").split(" ").includes("nx-mount-pad"),
    "the wrapper the rule keys on is not on the element that holds the pad");
  assert(pad.querySelector("button.tap-lg") != null,
    "the pad's arrows do not carry .tap-lg - the rule matches nothing");
});

test("the header live line and the tiles carry the rig's own numbers", () => {
  assert(/tracking sidereal · idle/.test(text()),
    `the live line does not describe a tracking, idle mount - got ${text().slice(0, 120)}`);
  assert(/pointing not verified/.test(text()),
    "an unverified pointing is not called out on the header line");
  assert(/east/.test(text()) && /flip in 1h 38m/.test(text()),
    "the PIER tile does not carry the pier side and the flip countdown");
  assert(/62° \/ 41°/.test(text()), "the ALT / AZ tile does not carry the mount's alt/az");
  assert(/solve failed: not enough stars/.test(text()),
    "the POINTING tile drops the reason the pointing is not verified");
  assert(/offset from target · 2\.4′/.test(text()) && /measured at the last solve/.test(text()),
    "the offset readout does not show the last solve's measured error");
});

// ====================================================== 2. the TRACKING write
await testAsync("picking a rate POSTs the rate route and latches the choice", async () => {
  asked.length = 0;
  click(q('[data-testid="tile-tracking"]'));           // point the dial at TRACKING
  const lunar = q('[data-testid="mount-dial"] [data-value="lunar"]');
  assert(lunar != null, "the dial has no `lunar` stop - E7's four stops are not rendered");
  click(lunar);
  await settle();
  assert(posted("/api/mount/tracking_rate?rate=lunar"),
    `the rate was not written - asked: ${JSON.stringify(asked)}`);
  // THE LATCH. `status.mount.tracking_rate` is still "sidereal" - the rig has
  // not published a new frame - and the tile must show the choice anyway.
  eq((useStore.getState().status as any).mount.tracking_rate, "sidereal",
    "precondition: the seeded rig still reports sidereal");
  assert(/lunar/.test(q('[data-testid="tile-tracking"]').textContent || ""),
    "the TRACKING tile did not paint the chosen rate - the control sits unmoved for 2 s");
});

test("a mount that cannot change rate is offered on/off, not four rates", () => {
  seed({ status: { ...mountStatus(), mount: { ...mountStatus().mount, can_set_tracking_rate: false } } });
  mount();
  assert(q('[data-testid="mount-dial"] [data-value="lunar"]') == null,
    "a mount with no rate control is still offered lunar - the dial would 400");
  assert(q('[data-testid="mount-dial"] [data-value="off"]') != null,
    "the collapsed dial lost its `off` stop");
});

// ================================================================== 3. PARK
await testAsync("PARK fires /api/mount/park", async () => {
  seed();
  mount();
  asked.length = 0;
  click(q('[data-testid="mount-park"]'));
  await settle();
  assert(posted("/api/mount/park"), `PARK sent nothing - asked: ${JSON.stringify(asked)}`);
});

// ================================================================ 4. viewer
await testAsync("a viewer sees the same screen, dimmed, and it sends nothing", async () => {
  seed({ principal: VIEWER });
  mount();
  await settle();
  const park = q('[data-testid="mount-park"]');
  assert(park != null, "PARK is HIDDEN from a viewer - nothing may be hidden (ARCHITECTURE 8)");
  eq(park.getAttribute("aria-disabled"), "true", "PARK is not honest-disabled for a viewer");
  assert(/needs/.test(park.getAttribute("title") || ""),
    `PARK carries no reason for a viewer - title was ${park.getAttribute("title")}`);
  const pad = q('[data-testid="mount-pad"]');
  eq(pad.getAttribute("aria-disabled"), "true", "the pad is not honest-disabled for a viewer");
  asked.length = 0;
  click(park);
  click(q('[data-testid="mount-solve"]'));
  await settle();
  eq(asked.length, 0, `a viewer's presses reached the rig: ${JSON.stringify(asked)}`);
});

// =========================================== 5. the ownership-gated panic stop
await testAsync("dismissing the sheet with no finger on the pad stops nothing", async () => {
  seed();
  mount();
  await settle();
  assert(q('[aria-label="Stop all mount motion"]') != null,
    "precondition: the pad is mounted, so its unmount reflex is the thing under test");
  asked.length = 0;
  act(() => { rootRef!.unmount(); });
  rootRef = null;
  await settle();
  assert(!posted("/api/mount/stop"),
    "closing the sheet fired the GLOBAL motion fence - this is the 2026-08-07 regression: "
    + `it aborts whatever else the rig is doing. asked: ${JSON.stringify(asked)}`);
});

// ========================================================== 6. flow ownership
await testAsync("a run owns the pad but never the abort", async () => {
  seed({ sequence: { state: "running", target: "NGC 6946" } });
  mount();
  await settle();
  const pad = q('[data-testid="mount-pad"]');
  eq(pad.getAttribute("title"), FLOW_OWNS_MOUNT,
    "the pad is not locked while a flow owns the mount");
  assert(/owned by the flow · NGC 6946/.test(text()),
    "the header line does not name the run that holds the mount");
  const park = q('[data-testid="mount-park"]');
  assert(park.getAttribute("aria-disabled") == null,
    "PARK is locked while a run holds the mount - park IS the abort and must stay live");
  asked.length = 0;
  click(park);
  await settle();
  assert(posted("/api/mount/park"), "PARK did not reach the rig during a run");

  // The pad's own escape hatch is exempt from the flow lock for the same reason.
  asked.length = 0;
  click(q('[aria-label="Stop all mount motion"]'));
  await settle();
  assert(posted("/api/mount/stop"),
    "the pad's STOP bar was swallowed by the flow lock - the one control that may never be");
});

// ======================================================= pure helpers on show
test("the readout formatters switch units where the design does", () => {
  eq(fmtArcmin(2.4), "2.4′", "arcminutes are not formatted as arcminutes");
  eq(fmtArcmin(90), "1.5°", "the readout does not switch to degrees past 60'");
  eq(fmtFlipIn(1.63), "1h 38m", "the flip countdown is not hours and minutes");
});

test("the polar row says what was measured, or that nothing was", () => {
  eq(polarRowSub(null), "not measured", "an absent polar session claims a measurement");
  eq(polarRowSub({ state: "idle" }), "not measured", "an idle aligner claims a measurement");
  eq(polarRowSub({ state: "running", phase: "measuring", point_index: 1 }), "measuring 2 of 3",
    "a running alignment does not report which solve it is on");
  eq(polarRowSub({ state: "done", total_error: 1.2 }), "error 1.2′ · excellent",
    "a finished alignment does not report its error and verdict");
  eq(polarRowSub({ state: "done", total_error: 25 }), "error 25.0′ · keep going",
    "a session abandoned at 25' reads as a success");
});

if (rootRef) act(() => { rootRef!.unmount(); });

const total = passed + failed;
console.log(`rigMount.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
