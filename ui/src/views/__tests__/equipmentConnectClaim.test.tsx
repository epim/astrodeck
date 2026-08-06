// equipmentConnectClaim.test.tsx — Equipment's Connect Rig button, MOUNTED,
// against a rig this browser did not start.
//
// THE DEFECT (audit #15). The button decides whether pressing it would change
// anything by diffing the dropdown picks against `connectedMap` — and
// `connectedMap` was adopted by copying those same picks the moment ANY role
// came up. So a rig brought up by a boot profile, the one-tap simulator, or the
// field tablet was declared to be "connected with these exact picks" on the
// strength of this browser's localStorage, which had never seen it. The button
// went dim, aria-disabled, titled "the rig is connected with these exact picks"
// — and the only way to apply the picks you were actually looking at was to
// deliberately spoil a row and put it back.
//
// THE DEFECT THE FIRST FIX SHIPPED (review round 2). Adoption then corroborated
// a pick against the server by comparing DISPLAY NAMES — and a display name is
// not an identity. `SimSession.get_device` documents its ConnSpec as "accepted
// for interface parity but unused", so the offer this page stores ("Simulated
// camera") is described back as "Sim Camera 533MM": a simulator rig connected
// from this very screen reported every role as a pending edit, on every reload
// and every return to the tab, and invited a reconnect that tears the rig down.
// The two halves are tested together below, because a fix for either one alone
// is a fix that breaks the other.
//
//   Run directly:  npx tsx src/views/__tests__/equipmentConnectClaim.test.tsx
//   Also run by `npm test` (run-tests.mjs).
//
// The claim is a rendered attribute over a state transition (picks in storage,
// then a status frame from a rig nobody here connected), so it is asserted on a
// real mounted button, not on a helper.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
// Before the store or the view are imported: the api client reads
// window.location at module scope. run-tests.mjs gives this file its own
// process, so the globals planted here cannot leak into another suite.
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
win.WebSocket = class { close() {} addEventListener() {} send() {} };

// The picks this browser remembers. Seeded BEFORE the view is imported:
// `useState(() => loadAssignments())` reads storage exactly once, per mount.
const STORAGE_KEY = "astrodeck.equipment.assignments.v1";
const PICKED_CAMERA = "ZWO ASI2600MM Pro";
const PICKED_MOUNT = "ZWO AM5N";
// Exactly what an Alpaca pick persists: the offer's dev_type + dev_num are the
// ConnSpec addressing, and drivers.py guarantees every Alpaca offer carries
// them.
const ALPACA_PICKS = {
  camera: { driverId: "drv-asi", devType: "camera", devNum: 0, name: PICKED_CAMERA },
  mount: { driverId: "drv-am5", devType: "telescope", devNum: 0, name: PICKED_MOUNT },
};
// Exactly what `▶ Simulator rig` persists (lib/equipment simAssignments over
// the sim driver's offers): a name, and NO addressing — the sim backend has
// none. JSON.stringify drops the undefined devType/devNum, so this is the
// literal shape that comes back out of localStorage.
const SIM_PICKS = {
  camera: { driverId: "sim", name: "Simulated camera" },
  mount: { driverId: "sim", name: "Simulated mount" },
  filterwheel: { driverId: "sim", name: "Simulated filterwheel" },
};
win.localStorage.setItem(STORAGE_KEY, JSON.stringify(ALPACA_PICKS));

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "requestAnimationFrame",
  "cancelAnimationFrame", "getComputedStyle", "matchMedia", "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- server stubs
// Only three routes matter here; anything else the tree pokes at answers {} so a
// stray fetch can never be the reason a test fails.
const drv = (id: string, label: string, role: string, name: string) => ({
  id, type: "alpaca", label, enabled: true, implicit: false,
  host: "127.0.0.1", port: 11111,
  status: { reachable: true, error: null, detail: null, probed_at: 0 },
  offers: { devices: [{ role, name, dev_type: role === "mount" ? "telescope" : "camera", dev_num: 0 }], tasks: [] },
});
const ROLES = ["camera", "mount", "filterwheel"];
const DRIVERS = {
  // filterwheel is here for the sim rig and for the last test in this file (the
  // entrance to the focus-offsets editor); the two Alpaca rows never offer it.
  roles: ROLES,
  drivers: [
    drv("drv-asi", "ASI camera", "camera", PICKED_CAMERA),
    drv("drv-am5", "AM5 mount", "mount", PICKED_MOUNT),
    // The implicit simulator, offers shaped exactly as drivers.py mints them:
    // `f"Simulated {role}"`, with no dev_type/dev_num anywhere.
    {
      id: "sim", type: "sim", label: "Simulator", enabled: true, implicit: true,
      status: { reachable: true, error: null, detail: null, probed_at: 0 },
      offers: {
        devices: ROLES.map((r) => ({ role: r, name: `Simulated ${r}` })),
        tasks: [],
      },
    },
  ],
};
/** POST /api/connect/rig answers with this when set (per-role ok flags). */
let connectResults: { role: string; ok: boolean; error: string | null; attempted: boolean }[] | null = null;
const posted: string[] = [];
g.fetch = async (url: string, init?: any) => {
  const path = String(url);
  if ((init?.method ?? "GET") !== "GET") posted.push(path);
  const body = path.includes("/api/connect/rig") && connectResults
    ? { summary: {}, results: connectResults, backend_links: [] }
    : path.includes("/api/drivers") ? DRIVERS
      : path.includes("/api/profiles") ? []
        : {};
  return {
    ok: true, status: 200, statusText: "OK",
    json: async () => body,
  } as any;
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const EquipmentView = (await import("../EquipmentView")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

// ------------------------------------------------------- device fixtures
// `status.connected` is `{role: Device.describe()}` verbatim (hub.summary()
// ["devices"] straight into poll_status), so these are the REAL shapes, not a
// convenient subset — the whole point of the round-2 fix is that the button
// reads identity the server round-trips, and a fixture that omits it would
// prove nothing.

/** An Alpaca device (backend "native"/"ascom-local"): dev_type + dev_num come
 *  back exactly as the ConnSpec sent them (NativeSession.get_device). */
const alpacaDev = (role: string, kind: string, name: string, devType: string, devNum: number) => ({
  name, kind, connected: true, host: "127.0.0.1", port: 11111,
  dev_type: devType, dev_num: devNum, role, backend: "alpaca",
});
/** A simulator device: the KEYS are present and empty, which is real
 *  information ("this backend has no Alpaca addressing"), and the name is the
 *  sim's own (devices/sim.py) — never the offer name we asked for. */
const simDev = (role: string, kind: string, name: string) => ({
  name, kind, connected: true, host: "", port: 0,
  dev_type: "", dev_num: 0, role, backend: "",
});
/** A server too old to publish addressing at all: the keys are ABSENT. */
const legacyDev = (kind: string, name: string) => ({ name, kind, connected: true });

/** A status frame reporting a live rig made of exactly these devices. */
function seedRig(devices: Record<string, any>): void {
  useStore.setState({
    status: {
      connected: devices, looping: false, mode: "sim",
      filterwheel: { names: ["L", "R", "G"], offsets: [0, 0, 0], opaque: [false, false, false], position: 0 },
      backend_links: Object.keys(devices).map((role) => ({
        role, ok: true, error: null, attempted: true, connected: true,
      })),
    },
    principal: { role: "admin", email: null, caps: ["view.status", "config.backend"] },
  } as never);
}

let container = win.document.getElementById("root") as any;
let root = createRoot(container);

/** Mount (or re-render) and let the two mount fetches settle. */
async function render(): Promise<void> {
  await act(async () => { root.render(createElement(EquipmentView)); });
  for (let i = 0; i < 5; i++) await act(async () => { await Promise.resolve(); });
}

/** Unmount, swap the remembered picks, mount a FRESH root — the only way to
 *  change what `useState(() => loadAssignments())` reads (an unmounted root
 *  cannot be re-rendered into). */
async function remountWith(picks: unknown): Promise<void> {
  await act(async () => { root.unmount(); });
  win.localStorage.setItem(STORAGE_KEY, JSON.stringify(picks));
  const next = win.document.createElement("div");
  win.document.body.appendChild(next);
  container = next;
  root = createRoot(next);
  await render();
}

/** The Connect Rig button — found by its icon-plus-label text, since the label
 *  is the thing under test and cannot double as the selector. */
function connectButton(): any {
  const buttons = [...container.querySelectorAll("button")];
  return buttons.find((b: any) => /connect rig|rig connected|reconnect rig/i.test(b.textContent || ""));
}
function label(): string { return (connectButton()?.textContent || "").trim(); }
/** The per-role driver dropdowns only — the Tasks panel below renders its own
 *  <select>s, and counting those in would make a row assertion meaningless. */
function driverSelects(): any[] {
  return [...container.querySelectorAll('select[aria-label$=" driver"]')];
}

/** Adoption is a one-shot taken on the edge where the rig comes up, so every
 *  scenario drops the rig first. */
function reseed(devices: Record<string, any>): void {
  act(() => { seedRig({}); });
  act(() => { seedRig(devices); });
}

// ---------------------------------------------------------- the rig is a SIM
// The nightmare this is really about: the rig came up simulated (a boot profile
// with no devices resolves every role to the simulator), and the picks on
// screen name real hardware. Nothing about the running rig matches them.
seedRig({
  camera: simDev("camera", "camera", "Sim Camera 533MM"),
  mount: simDev("mount", "mount", "Sim Mount EQ6-R"),
});
await render();

test("the view actually mounted, with the picks it was seeded with", () => {
  // Anti-blank-page guard: every assertion below is about ONE button's label,
  // and "no button" would satisfy several of them by accident.
  assert(/equipment/i.test(container.textContent || ""), "the Equipment view did not render");
  const selects = driverSelects();
  assert(selects.length === ROLES.length,
    `expected a driver <select> per role, found ${selects.length}`);
  assert(
    selects[0].value === "drv-asi",
    `the camera row did not restore the seeded pick (value=${selects[0].value}) — ` +
    "without it the button has nothing to compare and this file proves nothing",
  );
});

test("a rig connected elsewhere is not reported as 'these exact picks'", () => {
  const b = connectButton();
  assert(b != null, "no Connect Rig button rendered");
  assert(
    !/rig connected/i.test(label()),
    `the button says "${label()}" while the rig is running Simulated devices and the picks ` +
    "name a ZWO ASI2600MM Pro and an AM5N — that claim comes from localStorage, not from the rig",
  );
  assert(
    /reconnect rig \(2 changed\)/i.test(label()),
    `expected "Reconnect Rig (2 changed)" — both picks are contradicted by the running ` +
    `rig — got "${label()}"`,
  );
  assert(
    b.getAttribute("aria-disabled") == null && !b.disabled,
    "the button is disabled, so the only way to apply the picks is still to spoil a row",
  );
});

// ------------------------------------------------ the rig IS what was picked
test("a rig that really is running these picks still reports nothing to do", () => {
  // The other half: the fix must not simply always-enable the button, or the
  // dim state it was built for (pressing would re-cycle a working rig and drop
  // the mount mid-run) is gone.
  reseed({
    camera: alpacaDev("camera", "camera", PICKED_CAMERA, "camera", 0),
    mount: alpacaDev("mount", "mount", PICKED_MOUNT, "telescope", 0),
  });
  assert(
    /rig connected/i.test(label()),
    `the rig is running exactly the two devices picked here, so pressing Connect would ` +
    `change nothing — expected "Rig connected", got "${label()}"`,
  );
  assert(connectButton().getAttribute("aria-disabled") === "true",
    "a rig that already matches should be dim, not an invitation to re-cycle it");
});

// ------------------------------------------------------------- one row apart
test("a SECOND UNIT of the same model counts as changed, matching name and all", () => {
  // Two identical cameras on one Alpaca server differ only by dev_num — which
  // is precisely the identity `describe()` round-trips and the display name
  // cannot express. The mount below is the same model, same name, dev_num 1.
  reseed({
    camera: alpacaDev("camera", "camera", PICKED_CAMERA, "camera", 0),
    mount: alpacaDev("mount", "mount", PICKED_MOUNT, "telescope", 1),
  });
  assert(
    /reconnect rig \(1 changed\)/i.test(label()),
    `the camera matches and the mount is a different unit (dev_num 1, same name), so exactly ` +
    `one role is pending — got "${label()}"`,
  );
  assert(
    /will reconnect: mount/i.test(connectButton().getAttribute("title") || ""),
    `the title should name the role that differs — got "${connectButton().getAttribute("title")}"`,
  );
});

// ------------------------------------------------- a server that says nothing
test("a server that publishes no addressing does not fabricate edits", () => {
  // Degrade path: an older build whose status frame carries no dev_type key at
  // all. "I cannot tell" must not render as "(2 changed)" — that is the same
  // fabricated number, arrived at from the other direction.
  reseed({
    camera: legacyDev("camera", PICKED_CAMERA),
    mount: legacyDev("mount", PICKED_MOUNT),
  });
  assert(
    /rig connected/i.test(label()),
    `nothing contradicts the picks — the names agree and the server published no addressing ` +
    `to contradict them — got "${label()}"`,
  );
});

// ------------------------------------------------------- a PARTIAL reconnect
// #15's literal complaint, on the path the first fix did not touch: after a
// partial connect the button recorded the WHOLE map as running, read "Rig
// connected", and the only way to retry the refused role was to spoil a row and
// put it back. Start from a rig that contradicts both picks so the button is
// pressable, then answer the POST with one role up and one refused. The rig
// stays live throughout (a RECONNECT, not a cold start), so `connectedMap` is
// the receipt under test and not a re-adoption from the next status frame.
reseed({
  camera: simDev("camera", "camera", "Sim Camera 533MM"),
  mount: simDev("mount", "mount", "Sim Mount EQ6-R"),
});
const beforeConnect = label();
connectResults = [
  { role: "camera", ok: true, error: null, attempted: true },
  { role: "mount", ok: false, error: "mount: connection refused", attempted: true },
];
posted.length = 0;
await act(async () => {
  connectButton().dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
});
for (let i = 0; i < 8; i++) await act(async () => { await Promise.resolve(); });
connectResults = null;

test("the partial connect really was pressed, from a pressable button", () => {
  // PRECONDITION for the two assertions below: without a live button and a POST
  // on the wire, "not Rig connected" would be true for the wrong reason.
  assert(/reconnect rig \(2 changed\)/i.test(beforeConnect),
    `expected a pressable "Reconnect Rig (2 changed)" to press — got "${beforeConnect}"`);
  assert(posted.some((p) => p.includes("/api/connect/rig")),
    `the connect never went out (posted: ${JSON.stringify(posted)})`);
});

test("a role that failed to connect stays pending, so it can be retried", () => {
  assert(
    !/rig connected/i.test(label()),
    `the mount was REFUSED and the button says "${label()}" — the retry is only reachable by ` +
    "spoiling a row and putting it back, which is the defect",
  );
  assert(
    /reconnect rig \(1 changed\)/i.test(label()),
    `the camera came up and the mount did not, so exactly one role is still pending — ` +
    `got "${label()}"`,
  );
  assert(
    /will reconnect: mount/i.test(connectButton().getAttribute("title") || ""),
    `the title should name the role that did not come up — got ` +
    `"${connectButton().getAttribute("title")}"`,
  );
});

// ------------------------------------ #84: the filter-slots entrance's a11y
// It announced itself `aria-disabled` to assistive tech in a read-only session
// while opening an editor that is fully usable — and carried no dimming, so
// nothing in a screenshot could contradict it. The session that used to set
// that flag is the one to test under.
test("the focus-offsets entrance does not announce itself disabled while it opens", () => {
  act(() => {
    useStore.setState({
      principal: { role: "viewer", email: null, caps: ["view.status"] },
    } as never);
  });
  const opener = [...container.querySelectorAll("button")]
    .find((b: any) => /filter slots/i.test(b.textContent || ""));
  assert(opener != null, "no filter-slots entrance rendered — the filterwheel row is missing");
  // PRECONDITION: this is the read-only session, the one state in which the
  // flag was ever set. Without it the assertion below is about nothing.
  const rows = [...container.querySelectorAll("select")];
  assert(rows.length > 0 && (rows[0] as any).disabled === true,
    "the session still has config.backend, so nothing here would have been aria-disabled anyway");
  assert(opener.getAttribute("aria-disabled") == null,
    "the button reports itself unavailable to a screen reader and then opens a working editor");
  act(() => { opener.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
  assert(container.querySelector('[role="dialog"][aria-label="Filter slot names"]') != null,
    "the editor did not open — if it really were unavailable, aria-disabled would have been true");
});

// ============================================================ THE SIMULATOR RIG
// The regression the round-1 fix shipped, and the reason a display name cannot
// be an identity. `▶ Simulator rig` stores the OFFER names ("Simulated
// camera"); `sim_backend.SimSession.get_device` ignores the ConnSpec entirely,
// so the devices describe themselves with the sim's own names ("Sim Camera
// 533MM"). Comparing those two strings reported an eleven-role simulator rig as
// eleven pending edits — on every reload and every return to the tab — over a
// rig that IS those picks, and invited a reconnect that tears it all down.
await act(async () => { useStore.setState({ principal: { role: "admin", email: null, caps: ["view.status", "config.backend"] } } as never); });
await remountWith(SIM_PICKS);
act(() => { seedRig({}); });
act(() => {
  seedRig({
    camera: simDev("camera", "camera", "Sim Camera 533MM"),
    mount: simDev("mount", "mount", "Sim Mount EQ6-R"),
    filterwheel: simDev("filterwheel", "filterwheel", "Sim Filter Wheel 8x36"),
  });
});

test("the simulator picks were restored and really do disagree by name", () => {
  // PRECONDITION, both halves. Without the first, the button has nothing to
  // compare; without the second, a name-matching implementation would pass the
  // assertion below for the wrong reason and this test would prove nothing.
  const selects = driverSelects();
  assert(selects.length === ROLES.length && selects.every((s: any) => s.value === "sim"),
    `expected every row on the simulator, got [${selects.map((s: any) => s.value).join(", ")}]`);
  const live = (useStore.getState().status as any).connected;
  assert(live.camera.name === "Sim Camera 533MM" && live.camera.name !== SIM_PICKS.camera.name,
    "the live sim device and the stored sim pick carry the SAME name in this fixture, so " +
    "name-matching would look correct here");
});

test("a simulator rig that IS the picks does not report every role as changed", () => {
  assert(
    !/changed/i.test(label()),
    `the rig is running exactly these three simulated devices and the button says "${label()}" — ` +
    "that count is manufactured out of a display name the sim backend never round-trips, and " +
    "pressing it tears the whole rig down",
  );
  assert(
    /rig connected/i.test(label()),
    `expected "Rig connected" over a rig that is precisely these picks — got "${label()}"`,
  );
  assert(connectButton().getAttribute("aria-disabled") === "true",
    "a simulator rig that already matches should be dim, not an invitation to re-cycle it");
});

test("a simulator pick is still contradicted by a role that is DARK", () => {
  // The converse, so the sim case is not simply "always adopt": liveness is
  // still checked, and a pick whose role nothing is filling stays pending.
  reseed({
    camera: simDev("camera", "camera", "Sim Camera 533MM"),
    mount: simDev("mount", "mount", "Sim Mount EQ6-R"),
  });
  assert(
    /reconnect rig \(1 changed\)/i.test(label()),
    `the filterwheel is picked and nothing is filling that role, so exactly one is pending — ` +
    `got "${label()}"`,
  );
});

test("a simulator pick is contradicted by a REAL device in that role", () => {
  // And the case the identity check exists for: an Alpaca-addressed device
  // cannot be the simulator this browser picked, whatever either is called.
  reseed({
    camera: alpacaDev("camera", "camera", PICKED_CAMERA, "camera", 0),
    mount: simDev("mount", "mount", "Sim Mount EQ6-R"),
    filterwheel: simDev("filterwheel", "filterwheel", "Sim Filter Wheel 8x36"),
  });
  assert(
    /reconnect rig \(1 changed\)/i.test(label()),
    `the camera role is filled by a real Alpaca camera while this browser picked the ` +
    `simulator — got "${label()}"`,
  );
  assert(
    /will reconnect: camera/i.test(connectButton().getAttribute("title") || ""),
    `the title should name the camera — got "${connectButton().getAttribute("title")}"`,
  );
});

act(() => { root.unmount(); });

console.log(`equipmentConnectClaim: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
