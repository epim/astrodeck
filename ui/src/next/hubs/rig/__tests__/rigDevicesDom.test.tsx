// rigDevicesDom.test.tsx - RIG · DEVICES, mounted (plan hub-rig.md A, T-RIG-1).
//
//   Run directly:  npx tsx src/next/hubs/rig/__tests__/rigDevicesDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// SEVEN THINGS, each of which is a specific way this screen could render
// perfectly and still be wrong:
//
//  1. PRECONDITION. The marker, one row per connected role, and the PROFILE
//     row. Without this every assertion below is trivially true of a blank
//     page - "no control has the wrong attribute" holds for no controls.
//  2. A ROW GOES SOMEWHERE. Tapping CAMERA must put `#/rig/devices/camera` in
//     the hash. A row that only looks pressable is the design's whole navigation
//     model missing.
//  3. AN ALARM WORD CARRIES ITS REASON. `BackendLinkGrid` is not on this screen
//     any more (plan E31); if the row's third line stops rendering, a failed
//     link becomes a red word with no cause anywhere in the app.
//  4. + 5. THE QUICK ACTIONS REACH THE RIG. COOL must POST the cooler route
//     with the setpoint, PARK must POST the park route. A local-state-only
//     control makes every surface agree with every other surface and with
//     nothing on the hardware.
//  6. READ-ONLY IS RENDERED, NOT REMOVED. An operator (no `config.backend`)
//     still SEES ADD A DEVICE and the profile list, dimmed, with the reason -
//     and pressing them fires no request and navigates nowhere. A viewer's COOL
//     is the same shape.
//  7. THE FIRST NIGHT CARD IS THE INTERSTITIAL'S NEW HOME. Its four rows are
//     what `NotConnectedInterstitial` used to say; RUN THE SIMULATOR posts a
//     RigSpec of sim roles to `/api/connect/rig`, NOT the legacy
//     `/api/connect/sim`, whose result the assignment map cannot read and which
//     leaves `backend_links` (and therefore this whole screen) empty.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/rig/devices", pretendToBeVisual: true },
);
const win = dom.window as any;

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "HTMLSelectElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent",
  "KeyboardEvent", "PointerEvent", "localStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- fetch recorder
interface Asked { method: string; url: string; body: any }
const asked: Asked[] = [];
const ROLES = [
  "camera", "telescope", "focuser", "filterwheel", "guider", "rotator",
  "switch", "safety",
];
const DRIVERS = {
  roles: ROLES,
  drivers: [
    {
      id: "sim", type: "sim", label: "Simulator", enabled: true, implicit: true,
      status: { reachable: true, error: null, detail: null, probed_at: 0 },
      offers: { devices: ROLES.map((r) => ({ role: r, name: `Simulated ${r}` })), tasks: [] },
    },
  ],
};
const PROFILES = [
  {
    id: "p1", name: "Backyard", mode: "mixed", devices_count: 7,
    site_name: null, active: true,
  },
];

/** What the SERVER says it can talk to. The hardware one is what SCAN THIS
 *  COMPUTER probes; the two network ones are what SCAN THE NETWORK asks. */
const BACKENDS = [
  { name: "nina", label: "NINA", hardware: false, discoverable: true, driver_type: "nina", transport: "network" },
  { name: "native", label: "Alpaca", hardware: false, discoverable: true, driver_type: "alpaca", transport: "network" },
  { name: "zwo-asi", label: "ZWO ASI camera", hardware: true, discoverable: true, driver_type: "zwo-asi", transport: "local" },
];

const ok = (body: unknown) => ({
  ok: true, status: 200, statusText: "OK", json: async () => body,
});

g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const method = init?.method ?? "GET";
  const u = String(url);
  asked.push({ method, url: u, body: init?.body ? JSON.parse(init.body) : null });
  if (u.includes("/api/drivers")) return ok(DRIVERS);
  if (u.includes("/api/profiles")) return ok(PROFILES);
  if (u.includes("/api/backends")) return ok(BACKENDS);
  if (u.includes("/api/discover/zwo-asi")) {
    return ok([{ role: "camera", name: "ASI533MM", index: 0 }]);
  }
  if (u.includes("/api/discover/")) return ok([]);
  if (u.includes("/api/config/drivers")) {
    return ok({ driver: { id: "nina-a1b2", type: "nina", host: "10.0.0.5", port: 1888, enabled: true, label: "NINA", extra: {} } });
  }
  if (u.includes("/api/connect/rig")) {
    return ok({
      summary: {},
      results: ROLES.map((role) => ({ role, ok: true, error: null, attempted: true })),
      backend_links: [],
    });
  }
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { DevicesScreen } = await import("../devices/DevicesScreen");
const { AddDeviceSheet } = await import("../sheets/addDevice");
const { DriverSheet } = await import("../sheets/driver");
type RigStatus = import("../../../../types").RigStatus;

// ------------------------------------------------------------------- harness
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
const ADMIN = {
  role: "admin",
  email: "admin@rig",
  caps: [
    "view.status", "view.preview", "view.weather", "view.site_derived",
    "control.capture", "control.guide", "control.mount", "control.power",
    "config.safety", "config.backend", "config.site_optics", "config.alerts",
  ],
};
const OPERATOR = {
  role: "operator",
  email: "op@rig",
  caps: [
    "view.status", "view.preview", "view.weather", "view.site_derived",
    "control.capture", "control.guide", "control.mount",
  ],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };

function linked(over: Record<string, unknown> = {}): RigStatus {
  return {
    connected: {
      camera: { name: "ASI2600MM Pro", kind: "camera", connected: true },
      telescope: { name: "AM5N", kind: "telescope", connected: true },
      focuser: { name: "ZWO EAF", kind: "focuser", connected: true },
      filterwheel: { name: "Snowflake", kind: "filterwheel", connected: true },
      rotator: { name: "ZWO CAA", kind: "rotator", connected: true },
      switch: { name: "Pegasus UPB", kind: "switch", connected: true },
      safety: { name: "Safety monitor", kind: "safety", connected: true },
    },
    looping: false,
    busy_lanes: [],
    backend_links: ROLES.map((role) => ({
      role, ok: true, error: null, attempted: true, connected: true,
    })),
    camera: {
      temperature: 8.4, can_cool: true, width: 6248, height: 4176,
      max_gain: 300, max_bin: 4,
      cooler: { on: false, power: 0, target_c: -10, at_target: false, can_report_power: true },
    },
    mount: { tracking: true, parked: false, slewing: false },
    filterwheel: { position: 0, names: ["L", "R", "G", "B", "Ha", "OIII", "SII"], current: "L" },
    rotator: { name: "ZWO CAA", sky_deg: 12, mech_deg: 0, moving: false, synced: true, can_reverse: false, reverse: false },
    guider: { name: "native", guiding: false, rms_ra: 0, rms_dec: 0, rms_total: 0, snr: 0, recent: [] },
    ...over,
  } as unknown as RigStatus;
}

function seed(over: Record<string, unknown> = {}): void {
  act(() => {
    useStore.setState({
      status: linked(),
      equipConnected: true,
      wsPhase: "up",
      principal: ADMIN,
      config: { active_profile_id: "p1", cooling: { warm_ramp: true, warm_rate_c_per_min: 2, warm_ambient_c: null } },
      sequence: { state: "idle" },
      safety: {
        connected: true, streak: 0,
        reading: { is_safe: true, reason: "", source: "Safety monitor", stale: false, ts: 0 },
      },
      toasts: [],
      ...over,
    } as never);
  });
}

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
function mount(): void {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  act(() => { rootRef!.render(createElement(DevicesScreen as any)); });
}
function mountSheet(Comp: any, params: Record<string, string> = {}): void {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  act(() => { rootRef!.render(createElement(Comp, { params, depth: 0 })); });
}
function click(node: any): void {
  act(() => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
const q = (sel: string) => container.querySelector(sel) as any;
const qa = (sel: string) => Array.from(container.querySelectorAll(sel)) as any[];
/** The popover portals to document.body, so it is NOT under `container`. */
const qd = (sel: string) => win.document.querySelector(sel) as any;
const text = () => (container.textContent || "") as string;
const setHash = (h: string) => { win.location.hash = h; };

// ===================================================== 1. the precondition
seed();
mount();
await settle();

test("the screen mounted, with one row per connected role and the PROFILE row", () => {
  assert(q('[data-testid="rig-devices"]') != null,
    "no screen marker - the devices screen did not render at all");
  const rows = qa('[data-testid^="device-row-"]');
  eq(rows.length, 8, `expected eight device rows, found ${rows.length}`);
  eq(
    rows.map((r) => r.getAttribute("data-testid")).join(","),
    "device-row-camera,device-row-telescope,device-row-focuser,device-row-filterwheel,"
    + "device-row-guider,device-row-rotator,device-row-safety,device-row-switch",
    "the rows are not the design's order with ROTATOR added",
  );
  assert(q('[data-testid="profile-row"]') != null, "no PROFILE row");
  assert(/Backyard/.test(q('[data-testid="profile-value"]').textContent),
    "the PROFILE row does not name the active profile");
});

test("the summary line reports live state, not the prototype's fixture", () => {
  const line = q('[data-testid="rig-summary"]').textContent as string;
  assert(/cooler off/.test(line), `the summary dropped the cooler clause (${line})`);
  assert(/tracking/.test(line), `the summary dropped the mount clause (${line})`);
  assert(/7 filters/.test(line), `the summary dropped the wheel clause (${line})`);
  assert(/safe/.test(line), `the summary dropped the safety clause (${line})`);
});

test("ADD A DEVICE names the backends this server actually has (E22)", () => {
  const row = q('[data-testid="add-a-device"]');
  assert(row != null, "no ADD A DEVICE row");
  assert(/USB, Alpaca and ASCOM/.test(row.textContent),
    `the sub-line does not name the real backend set (${row.textContent})`);
  assert(!/INDI/.test(text()), "the screen offers INDI, which this server has no backend for");
});

// =================================================== 2. a row goes somewhere
test("tapping the CAMERA row opens its sheet", () => {
  click(q('[data-testid="device-row-camera"]'));
  eq(win.location.hash, "#/rig/devices/camera", "hash:");
  setHash("#/rig/devices");
});

// ================================================ 3. an alarm carries a reason
await testAsync("a failed link renders FAILED and the backend's own reason", async () => {
  seed({
    status: linked({
      backend_links: ROLES.map((role) => (role === "focuser"
        ? { role, ok: false, error: "COM7 did not answer", attempted: true, connected: false }
        : { role, ok: true, error: null, attempted: true, connected: true })),
    }),
  });
  mount();
  await settle();
  const row = q('[data-testid="device-row-focuser"]');
  assert(/FAILED/.test(row.textContent), `the row does not say it failed (${row.textContent})`);
  const reason = q('[data-testid="device-reason-focuser"]');
  assert(reason != null,
    "a red FAILED with no reason - BackendLinkGrid is not on this screen, so this line is the only home the reason has");
  eq(reason.textContent, "COM7 did not answer", "reason:");
});

// ================================================= 4. COOL reaches the rig
await testAsync("COOL posts the cooler route with the set-point", async () => {
  seed();
  mount();
  await settle();
  asked.length = 0;
  const cool = q('[data-testid="quick-cool"]');
  assert(cool != null, "no COOL button");
  assert(/COOL TO -10/.test(cool.textContent),
    `the label does not carry the set-point it would send (${cool.textContent})`);
  click(cool);
  await settle();
  const post = asked.find((a) => a.method === "POST" && a.url.includes("/api/camera/cooler"));
  assert(post != null, `no cooler POST - the button only moved local state (${JSON.stringify(asked)})`);
  eq(post!.body.on, true, "cooler on:");
  eq(post!.body.target_c, -10, "target:");
});

// ================================================= 5. PARK reaches the rig
await testAsync("PARK posts the park route", async () => {
  asked.length = 0;
  const park = q('[data-testid="quick-park"]');
  assert(park != null, "no PARK button");
  assert(/PARK MOUNT/.test(park.textContent), `label: ${park.textContent}`);
  click(park);
  await settle();
  const post = asked.find((a) => a.method === "POST" && a.url.includes("/api/mount/park"));
  assert(post != null, `no park POST (${JSON.stringify(asked)})`);
});

// =========================================== 6. read-only is rendered, not gone
await testAsync("an operator sees ADD A DEVICE and the profiles, read-only", async () => {
  seed({ principal: OPERATOR });
  mount();
  await settle();

  const add = q('[data-testid="add-a-device"]');
  assert(add != null, "ADD A DEVICE was HIDDEN from an operator instead of dimmed");
  eq(add.getAttribute("aria-disabled"), "true", "aria-disabled:");
  eq(add.getAttribute("title"), "needs admin access",
    `the reason does not name the capability (${add.getAttribute("title")})`);
  assert(add.getAttribute("disabled") == null,
    "the native disabled attribute swallows the press and the explanation with it");

  const before = asked.length;
  const hash = win.location.hash;
  click(add);
  await settle();
  eq(asked.length, before, "a locked ADD A DEVICE still fired a request");
  eq(win.location.hash, hash, "a locked ADD A DEVICE still navigated");

  // The profiles popover, same rule: listed, dimmed, explained.
  click(q('[data-testid="profile-row"]'));
  await settle();
  const pick = qd('[data-testid="profile-pick-p1"]');
  assert(pick != null, "the profile list was hidden from an operator");
  eq(pick.getAttribute("aria-disabled"), "true", "aria-disabled:");
  assert(/admin access/.test(pick.getAttribute("title") ?? ""),
    `the reason does not name the capability (${pick.getAttribute("title")})`);
  const before2 = asked.length;
  click(pick);
  await settle();
  eq(asked.length, before2, "a locked profile row still activated a profile");

  const note = q('[data-testid="devices-readonly"]');
  assert(note != null && /admin access/.test(note.textContent),
    "no read-only note saying who could connect equipment");
});

await testAsync("a viewer's COOL is dimmed, explained, and fires nothing", async () => {
  seed({ principal: VIEWER });
  mount();
  await settle();
  const cool = q('[data-testid="quick-cool"]');
  assert(cool != null, "COOL was hidden from a viewer instead of dimmed");
  eq(cool.getAttribute("aria-disabled"), "true", "aria-disabled:");
  assert(/operator or admin access/.test(cool.getAttribute("title") ?? ""),
    `the reason does not name the capability (${cool.getAttribute("title")})`);
  const before = asked.length;
  click(cool);
  await settle();
  eq(asked.length, before, "a locked COOL still reached the camera");
});

// ============================================ 7. the FIRST NIGHT card and sim
await testAsync("nothing connected renders FIRST NIGHT with the folded interstitial", async () => {
  seed({
    principal: ADMIN,
    equipConnected: false,
    status: { connected: {}, looping: false, busy_lanes: [], backend_links: [] } as unknown as RigStatus,
    safety: null,
  });
  mount();
  await settle();

  const card = q('[data-testid="first-night"]');
  assert(card != null, "no FIRST NIGHT card on a rig with nothing connected");
  assert(/FIRST NIGHT · CONNECT ONCE/.test(card.textContent), "the card lost its label");
  const cta = q('[data-testid="first-night-connect"]');
  assert(cta != null, "no CONNECT CTA");
  eq(cta.textContent.trim(), "CONNECT BACKYARD PROFILE", "CTA label:");
  // The four rows the interstitial folded into this card.
  assert(q('[data-testid="first-night-sim"]') != null, "no RUN THE SIMULATOR row");
  assert(q('[data-testid="first-night-setup"]') != null, "no setup-guide row");
  assert(q('[data-testid="first-night-tonight"]') != null, "no plan-tonight row");
  assert(/Planning works with the rig switched off/.test(text()),
    "the plan-tonight row lost the sentence that makes it worth pressing");
  // Seven greyed rows, not an empty list: somewhere to press.
  eq(qa('[data-testid^="device-row-"]').length, 7, "the not-connected roster is not the seven");
});

await testAsync("RUN THE SIMULATOR connects a sim rig through the RigSpec path", async () => {
  asked.length = 0;
  click(q('[data-testid="first-night-sim"]'));
  await settle();
  const post = asked.find((a) => a.method === "POST" && a.url.includes("/api/connect/rig"));
  assert(post != null, `no RigSpec connect (${JSON.stringify(asked.map((a) => a.url))})`);
  assert(!asked.some((a) => a.url.includes("/api/connect/sim")),
    "the legacy sim shortcut was used - it leaves backend_links empty, so this very screen goes blank");
  eq(post!.body.primary, "none", "primary:");
  eq(post!.body.roles.camera.backend, "sim", "the camera role is not simulated:");
  eq(Object.keys(post!.body.roles).length, ROLES.length, "role count:");
});

// ============================================ 8. the ADD A DEVICE sheet works
await testAsync("the ADD A DEVICE sheet renders an assignment row per server role", async () => {
  seed({ principal: ADMIN });
  mountSheet(AddDeviceSheet);
  await settle();
  assert(q('[data-testid="sheet-add-device"]') != null, "the sheet did not render at all");
  const rows = qa('[data-testid^="role-"]').filter(
    (n) => ROLES.includes((n.getAttribute("data-testid") ?? "").replace("role-", "")),
  );
  eq(rows.length, ROLES.length, `expected one row per server role, found ${rows.length}`);
  assert(q('[data-testid="open-driver-sheet"]') != null,
    "no way to declare a driver a scan cannot find - that is GAP-2's whole point");
  assert(q('[data-testid="connect-rig"]') != null, "no CONNECT RIG");
  assert(q('[data-testid="disconnect-rig"]') != null, "no DISCONNECT");
});

await testAsync("SCAN THIS COMPUTER probes the hardware backends and offers what answered", async () => {
  asked.length = 0;
  click(q('[data-testid="scan-hardware"]'));
  await settle();
  await settle();
  assert(asked.some((a) => a.url.includes("/api/backends")),
    `the scan never asked which backends exist (${JSON.stringify(asked.map((a) => a.url))})`);
  assert(asked.some((a) => a.url.includes("/api/discover/zwo-asi")),
    "the scan never probed the hardware backend the server registered");
  const found = q('[data-testid="hw-found-0"]');
  assert(found != null, "the scan answered and the sheet listed nothing");
  assert(/ASI533MM/.test(found.textContent), `the found row lost the device name (${found.textContent})`);
  assert(q('[data-testid="hw-add-0"]') != null, "a found device with no way to add it");
});

await testAsync("the driver sheet validates before it posts, and posts what it validated", async () => {
  mountSheet(DriverSheet);
  await settle();
  assert(q('[data-testid="sheet-driver"]') != null, "the driver sheet did not render");
  const add = q('[data-testid="driver-add"]');
  // Empty host: the shipped validator's own sentence, and the button carries it
  // as its reason rather than posting a 422 for the user to decode.
  eq(add.getAttribute("title"), "host is required", `reason: ${add.getAttribute("title")}`);
  const before = asked.length;
  click(add);
  await settle();
  eq(asked.length, before, "ADD DRIVER posted an invalid form");

  const host = q('[data-testid="driver-host"]');
  act(() => {
    const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!;
    setter.call(host, "10.0.0.5");
    host.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
  await settle();
  asked.length = 0;
  click(q('[data-testid="driver-add"]'));
  await settle();
  const post = asked.find((a) => a.method === "POST" && a.url.includes("/api/config/drivers"));
  assert(post != null, `no driver POST (${JSON.stringify(asked)})`);
  eq(post!.body.type, "nina", "type:");
  eq(post!.body.host, "10.0.0.5", "host:");
});

act(() => { rootRef?.unmount(); });

const total = passed + failed;
console.log(`rigDevicesDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
