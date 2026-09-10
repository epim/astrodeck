// rigPowerDom.test.tsx - the POWER device sheet, MOUNTED (plan hub-rig.md B.2,
// task T-RIG-2).
//
//   Run directly:  npx tsx src/next/hubs/rig/__tests__/rigPowerDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// FIVE THINGS WORTH A TEST. Four of them are the behaviours `views/PowerView.tsx`
// records as having been bugs, and every one of them is invisible on screen when
// it regresses - which is precisely why they need an assertion rather than a
// look:
//
//   1. PRECONDITION - one row per port, of all three kinds. Without it, "no row
//      has the wrong attribute" is trivially true of a sheet with no rows.
//   2. A toggle POSTs `/api/switch/set {port_id, value}` and NOTHING ELSE.
//   3. NEVER A SECOND WALK WHILE ONE IS OUT. A UPB toggle is 1+5N sequential
//      serial GETs. A second tap during the walk must not stack another one; a
//      dimmer RELEASE during one must be QUEUED and sent when the walk returns,
//      because dropping it is invisible - the draft clears, the thumb snaps
//      back, and nothing says the adjustment went nowhere.
//   4. A viewer sees every port and its live value, inert, with the reason - and
//      a press reaches nothing.
//   5. THE SESSION LOCK IS A NAME MATCH, AND SAYS SO. `SwitchPort` has no lock
//      flag (E13), so the mount port is matched by name; the footer has to
//      disclose that or a user whose dew port is called "USB DEW" has no way to
//      find out why it locked.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/rig/devices/power", pretendToBeVisual: true },
);
const win = dom.window as any;

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------ fixtures
interface Port {
  id: number; name: string; can_write: boolean; is_boolean: boolean;
  value: number; min: number; max: number; unit: string;
}
// Shaped like the simulator's own box (server devices/sim.py:1346-1353): the
// booleans carry 0/1 with NO unit, and the current lives on its own read-only
// port. That is what makes "port toggles with amps" undeliverable as drawn.
const PORTS: Port[] = [
  { id: 0, name: "Mount 12V", can_write: true, is_boolean: true, value: 1, min: 0, max: 1, unit: "" },
  { id: 1, name: "Camera 12V", can_write: true, is_boolean: true, value: 1, min: 0, max: 1, unit: "" },
  { id: 2, name: "Bench light", can_write: true, is_boolean: true, value: 0, min: 0, max: 1, unit: "" },
  { id: 3, name: "Dew Heater A", can_write: true, is_boolean: false, value: 35, min: 0, max: 100, unit: "%" },
  { id: 4, name: "Input Voltage", can_write: false, is_boolean: false, value: 13.7, min: 0, max: 15, unit: "V" },
  { id: 5, name: "Total Current", can_write: false, is_boolean: false, value: 2.4, min: 0, max: 10, unit: "A" },
];

// ------------------------------------------------------------- fetch recorder
interface Asked { method: string; url: string; body: unknown }
const asked: Asked[] = [];
/** When set, `/api/switch/set` hangs until it is called - the only way to have a
 *  walk genuinely IN FLIGHT while a second gesture arrives. */
let releaseSet: ((ports: Port[]) => void) | null = null;
let holdSet = false;

g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const method = init?.method ?? "GET";
  const body = init?.body ? JSON.parse(init.body) : null;
  asked.push({ method, url: String(url), body });
  const ok = (json: unknown) => ({ ok: true, status: 200, statusText: "OK", json: async () => json });

  if (String(url).includes("/api/switch/ports")) return ok(PORTS);
  if (String(url).includes("/api/switch/set")) {
    const { port_id, value } = body as { port_id: number; value: number };
    const next = PORTS.map((p) => (p.id === port_id ? { ...p, value } : p));
    if (holdSet) {
      return await new Promise<any>((resolve) => {
        releaseSet = (ports: Port[]) => resolve(ok(ports));
      });
    }
    return ok(next);
  }
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { PowerSheet, powerLiveLine, sessionLockReason, SESSION_CRITICAL } =
  await import("../sheets/power");
type RigStatus = import("../../../../types").RigStatus;

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

const ADMIN = {
  role: "admin",
  email: "admin@rig",
  caps: ["view.status", "view.preview", "view.media", "view.site_precise",
    "view.site_derived", "view.weather", "control.capture", "control.mount",
    "control.guide", "control.power", "config.safety", "config.backend"],
};
const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "control.capture", "control.guide", "control.mount"],
};

function seed(over: Record<string, unknown> = {}): void {
  act(() => {
    useStore.setState({
      status: {
        connected: { switch: { name: "Pegasus UPB", kind: "switch", connected: true } },
        looping: false,
        busy_lanes: [],
        backend_links: [{ role: "switch", connected: true, error: null }],
      } as unknown as RigStatus,
      equipConnected: true,
      wsPhase: "up",
      principal: ADMIN,
      sequence: { state: "idle" },
      toasts: [],
      ...over,
    } as never);
  });
}

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
async function mount(): Promise<void> {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  act(() => { rootRef!.render(createElement(PowerSheet as any, { params: {}, depth: 0 })); });
  await settle();
}
function click(node: any): void {
  act(() => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
const q = (sel: string) => container.querySelector(sel) as any;
const qa = (sel: string) => Array.from(container.querySelectorAll(sel)) as any[];
const text = () => (container.textContent || "") as string;

// ====================================================== 1. the precondition
seed();
await mount();

test("the sheet mounted with one row per port, of all three kinds", () => {
  assert(q('[data-testid="rig-power"]') != null,
    "no sheet marker - the sheet did not render at all");
  eq(qa('[data-testid^="port-"]').length, PORTS.length,
    "the sheet lost a port - a row that is not drawn is a port nobody can switch");
  // The three kinds are DIFFERENT rows, not one shape rendered six times.
  eq(qa('[role="switch"]').length, 3, "the writable boolean ports are not switches");
  eq(qa('input[type="range"]').length, 1, "the writable numeric port has no dimmer");
  assert(/Total Current/.test(text()) && /2\.4 A/.test(text()),
    "the read-only telemetry port is not rendered with its value and unit");
});

test("the header names the box and the live line counts the ports", () => {
  eq(q(".nx-sheet-title").textContent, "POWER · Pegasus UPB",
    "the header does not name the connected box");
  const line = q(".nx-sheet-live").textContent as string;
  eq(line, "13.7 V · 2.4 A · 2 of 3 ports on", `the live line is wrong (${line})`);
  // Every clause comes from a read-only port; with none, none is claimed.
  eq(powerLiveLine([], true), "connected · no readings yet",
    "a box with no readings still had numbers put in its mouth");
});

test("a boolean port does not claim a current it cannot report", () => {
  // The design's right column reads "0.9 A" per port. `SwitchPort` has no
  // per-port current; a boolean port's `value` is 0/1 with an empty unit, so
  // rendering it with an "A" after it would invent an ammeter.
  const row = q('[data-testid="port-0"]');
  assert(/ON/.test(row.textContent), "the mount port does not say it is on");
  assert(!/1\.0 A/.test(row.textContent),
    "a boolean port's 0/1 value was printed as a current the box never reported");
});

test("the footer discloses that the session lock is a NAME match (E13)", () => {
  const foot = q('[data-testid="power-footer"]').textContent as string;
  assert(/Mount, camera and USB are locked while a session runs/.test(foot),
    "the design's own sentence about the session lock is missing");
  assert(/matched by name; rename a port on the power box/.test(foot),
    "the sheet locks ports by a name heuristic without telling the user, so a "
    + "wrongly-matched port has no explanation and no fix");
  // E24: the fragment claims dew heaters on auto follow the dew margin from
  // Weather. Nothing in this engine drives a switch port from the dew margin.
  assert(!/dew margin/i.test(text()),
    "the sheet claims dew ports follow the dew margin from Weather");
  assert(/hold their power until you change them/.test(foot),
    "the honest replacement sentence about dew ports is missing");
});

// ================================================= 2 + 3. the write and the walk
await testAsync("a toggle POSTs the port id and its new value, and nothing else", async () => {
  asked.length = 0;
  click(q('[data-testid="port-2"]')); // Bench light, currently off
  await settle();
  const sets = asked.filter((a) => a.url === "/api/switch/set");
  eq(sets.length, 1, `expected exactly one switch write (${JSON.stringify(asked)})`);
  eq(sets[0].method, "POST", "the port was not written with a POST");
  eq(JSON.stringify(sets[0].body), JSON.stringify({ port_id: 2, value: 1 }),
    `the write body is wrong (${JSON.stringify(sets[0].body)})`);
});

await testAsync("a second tap during the walk does NOT stack a second walk", async () => {
  holdSet = true;
  releaseSet = null;
  asked.length = 0;
  const row = () => q('[data-testid="port-2"]');
  const before = row().getAttribute("aria-checked");
  click(row());
  await settle();
  eq(asked.filter((a) => a.url === "/api/switch/set").length, 1, "the first tap did not go out");

  // Mid-walk the row keeps the port's REAL state and narrates the request.
  eq(row().getAttribute("aria-busy"), "true", "the row does not report the walk in flight");
  eq(row().getAttribute("aria-checked"), before,
    "the row flipped to the REQUESTED state - a UPB walk takes 1+5N serial GETs "
    + "and the box has not switched yet");
  assert(new RegExp(`-> ${before === "true" ? "OFF" : "ON"}`).test(row().textContent),
    "the row does not narrate the request it is waiting on");
  eq(row().getAttribute("aria-describedby"), "nx-port-2-pending",
    "the pending narration rides on the accessible NAME instead of a description");
  eq(row().getAttribute("aria-label"), "Bench light",
    "the control's accessible name changed mid-interaction");

  click(row());
  await settle();
  eq(asked.filter((a) => a.url === "/api/switch/set").length, 1,
    "a second tap during the walk sent a second full walk of the box");

  releaseSet!(PORTS.map((p) => (p.id === 2 ? { ...p, value: before === "true" ? 0 : 1 } : p)));
  await settle();
  holdSet = false;
});

await testAsync("a dimmer release during a walk is QUEUED, not dropped", async () => {
  seed();
  await mount();
  holdSet = true;
  releaseSet = null;
  asked.length = 0;

  const slider = () => q('[data-testid="dimmer-3"]');
  const drag = (v: number) => {
    act(() => {
      const el = slider();
      // React overrides `value` on the node with its own setter, which keeps the
      // change TRACKER in step - so a plain `el.value = ...` looks to React like
      // no change at all and onChange never fires. Write through the prototype's
      // native setter, then dispatch the event React actually listens for.
      const native = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value");
      native!.set!.call(el, String(v));
      el.dispatchEvent(new win.Event("input", { bubbles: true }));
    });
  };
  const release = () => {
    act(() => { slider().dispatchEvent(new win.MouseEvent("pointerup", { bubbles: true })); });
  };

  drag(60);
  release();
  await settle();
  const first = asked.filter((a) => a.url === "/api/switch/set");
  eq(first.length, 1, "the first release did not commit");
  eq((first[0].body as { value: number }).value, 60, "the first release sent the wrong level");

  // A second adjustment while the box is still walking. Dropping it is the
  // invisible failure: the draft clears, the thumb snaps back to 35, and the
  // row says nothing about the level that went nowhere.
  drag(80);
  release();
  await settle();
  eq(asked.filter((a) => a.url === "/api/switch/set").length, 1,
    "the second release started a second walk while one was out");
  eq(slider().value, "80", "the draft was dropped - the thumb left the level the user chose");

  releaseSet!(PORTS.map((p) => (p.id === 3 ? { ...p, value: 60 } : p)));
  await settle();
  await settle();
  const sets = asked.filter((a) => a.url === "/api/switch/set");
  eq(sets.length, 2, "the queued level was never sent when the walk returned");
  eq(JSON.stringify(sets[1].body), JSON.stringify({ port_id: 3, value: 80 }),
    `the queued level is wrong (${JSON.stringify(sets[1].body)})`);

  holdSet = false;
  releaseSet!(PORTS.map((p) => (p.id === 3 ? { ...p, value: 80 } : p)));
  await settle();
});

test("a pending dimmer says out loud that it is waiting for the box", () => {
  // aria-valuetext, because aria-valuenow is the level and cannot carry "not
  // there yet". Asserted on the resolved state so the attribute is present at
  // all: it is set from the same `pending` flag the row's colour reads.
  const slider = q('[data-testid="dimmer-3"]');
  assert((slider.getAttribute("aria-valuetext") || "").includes("%"),
    "the dimmer has no aria-valuetext, so a screen reader announces a bare number");
  eq(slider.getAttribute("aria-label"), "Dew Heater A level",
    "the dimmer is unnamed - the port name beside it is a sibling span, not a label");
});

// ============================================== 4. a viewer, honest-disabled
await testAsync("an operator without control.power sees every port, inert, with the reason", async () => {
  seed({ principal: OPERATOR });
  await mount();
  asked.length = 0;

  eq(qa('[data-testid^="port-"]').length, PORTS.length,
    "ports were HIDDEN from a caller who cannot switch them - they must be shown, inert");
  const row = q('[data-testid="port-2"]');
  eq(row.getAttribute("aria-disabled"), "true", "the port is not marked aria-disabled");
  assert(!row.hasAttribute("disabled"),
    "the port uses the native disabled attribute, which takes it and its reason out of the tree");
  eq(row.getAttribute("title"), "needs admin access",
    `the row does not say who may switch it (${row.getAttribute("title")})`);
  eq(q('[data-testid="dimmer-3"]').getAttribute("aria-disabled"), "true",
    "the dimmer is live for a caller who cannot switch it");
  assert(/13\.7 V/.test(text()), "the live telemetry is hidden rather than shown read-only");

  click(row);
  await settle();
  eq(asked.filter((a) => a.url === "/api/switch/set").length, 0,
    "a caller without control.power reached the power box");
  const toasts = (useStore.getState() as any).toasts as { title: string }[];
  assert(toasts.some((t) => /admin access/.test(t.title)),
    "the press was swallowed with no explanation");
});

// ================================================ 5. the session lock by name
await testAsync("while a run is going, the session-critical ports lock and say why", async () => {
  seed({ principal: ADMIN, sequence: { state: "running", target: "NGC 6946" } });
  await mount();
  asked.length = 0;

  const mount0 = q('[data-testid="port-0"]');   // Mount 12V
  const bench = q('[data-testid="port-2"]');    // Bench light
  eq(mount0.getAttribute("aria-disabled"), "true", "the mount port is live during a run");
  eq(mount0.getAttribute("title"), sessionLockReason("Mount 12V"),
    `the mount port's reason is wrong (${mount0.getAttribute("title")})`);
  assert(/Stop the run on Session - Now/.test(mount0.getAttribute("title") || ""),
    "the reason does not say where the run can be stopped");
  assert(/session-critical · matched by name/.test(mount0.textContent),
    "the row does not disclose that it locked on a name match");

  eq(bench.getAttribute("aria-disabled"), null,
    "a port that is not session-critical locked too - the run only owns mount, camera and USB");
  click(mount0);
  await settle();
  eq(asked.filter((a) => a.url === "/api/switch/set").length, 0,
    "a locked port still reached the power box");
  click(bench);
  await settle();
  eq(asked.filter((a) => a.url === "/api/switch/set").length, 1,
    "an unlocked port stopped working while a run was going");
});

test("the name heuristic matches what the design names and nothing else", () => {
  for (const yes of ["Mount 12V", "12 V · CAMERA", "USB HUB", "usb hub"]) {
    assert(SESSION_CRITICAL.test(yes), `"${yes}" should be session-critical`);
  }
  for (const no of ["Dew Heater A", "Bench light", "Input Voltage"]) {
    assert(!SESSION_CRITICAL.test(no), `"${no}" should NOT lock during a run`);
  }
});

// ============================================================ 6. no box at all
await testAsync("with no power box the sheet says so and asks the rig for nothing", async () => {
  seed({
    equipConnected: false,
    status: {
      connected: {}, looping: false, busy_lanes: [],
      backend_links: [{ role: "switch", connected: false, error: null }],
    } as unknown as RigStatus,
  });
  await mount();
  asked.length = 0;
  await settle();

  assert(q('[data-testid="power-empty"]') != null, "no empty state for a missing power box");
  assert(/connect a switch device \(Pegasus UPB, etc\.\) on ADD A DEVICE/.test(text()),
    "the empty state does not say what to connect or where");
  eq(q(".nx-sheet-live").textContent, "NOT CONNECTED", "the live line claims a state");
  eq(asked.filter((a) => a.url.includes("/api/switch")).length, 0,
    "the 5 s poll kept running against a box that is not there");
});

act(() => { rootRef?.unmount(); });

const total = passed + failed;
console.log(`rigPowerDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
