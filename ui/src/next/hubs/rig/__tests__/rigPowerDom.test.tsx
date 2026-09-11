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
//   5. THE SESSION LOCK IS A NAME MATCH ON AN OLD ENGINE, AND SAYS SO. Before
//      S7h the wire had no lock flag (E13), so the mount port was matched by
//      name; the footer had to disclose that or a user whose dew port is called
//      "USB DEW" had no way to find out why it locked. That path is now the
//      FALLBACK and is still tested, because a rig that has not been updated
//      must not silently lose the lock.
//
// AND SIX MORE, added with D-RIG-5 and the port half of D-RIG-3 (T-U7b-7). The
// decision moved into `power_guard.py` and every port row now carries the
// engine's own answer, so the things worth an assertion are the ones where the
// client could still disagree with it:
//
//   6. `protected_now` LOCKS THE ROW WITH THE SERVER'S OWN SENTENCE, before any
//      press - a tap must never have to be refused to learn it would be.
//   7. THE TRI-STATE DOES NOT COLLAPSE. A port whose NAME matches the heuristic
//      but whose stored decision is `false` is NOT locked. That is the whole
//      point of a third state, and it is invisible on screen when it breaks:
//      the row just goes back to being locked and looks correct.
//   8. BY NAME SENDS `null`, NOT AN OMISSION and not `false`. Absent means
//      unchanged on the server and `null` is one of three real values, so a
//      spread over defaults would forge a decision.
//   9. FOLLOW DEW SENDS ONLY `follow_dew`, for the same reason from the other
//      side: a second key in that body un-pins a protection nobody touched.
//  10. A 409 `port_protected` shows the WIRE's sentence, verbatim and whole -
//      matched by `code`, and not through `showToast`, which would truncate it.
//  11. A 403 `local_only` on the settings PUT says where the setting can be
//      changed, and does NOT take the port's on/off control away with it: the
//      relay fence covers the policy route on purpose and misses
//      `POST /api/switch/set` on purpose.

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
  protect_during_run?: boolean | null;
  protected_now?: boolean;
  follow_dew?: boolean;
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

// The SAME box on an engine that carries `power_guard` (S7h + S7L), answering
// `GET /api/switch/ports` through `power_guard.annotate`. A run is live, so
// `protected_now` is the effective answer with the run already ANDed in.
//
// Port 1 is the fixture the tri-state exists for: its name MATCHES the
// heuristic, and somebody has stored `false` against it. The stored decision
// wins, and it has to keep winning - a client that reads the two states as one
// boolean re-locks that port and looks entirely correct doing it.
//
// Port 3 is a Pegasus UPB dew channel: an 8-bit register, 0..255, not a
// percentage. It is what stops the FOLLOW DEW copy from inventing a second unit.
const ANNOTATED: Port[] = [
  { id: 0, name: "Mount 12V", can_write: true, is_boolean: true, value: 1, min: 0, max: 1, unit: "",
    protect_during_run: null, protected_now: true, follow_dew: false },
  { id: 1, name: "Camera 12V", can_write: true, is_boolean: true, value: 1, min: 0, max: 1, unit: "",
    protect_during_run: false, protected_now: false, follow_dew: false },
  { id: 2, name: "Bench light", can_write: true, is_boolean: true, value: 0, min: 0, max: 1, unit: "",
    protect_during_run: null, protected_now: false, follow_dew: false },
  { id: 3, name: "Dew Heater A", can_write: true, is_boolean: false, value: 88, min: 0, max: 255, unit: "",
    protect_during_run: null, protected_now: false, follow_dew: true },
  { id: 4, name: "Input Voltage", can_write: false, is_boolean: false, value: 13.7, min: 0, max: 15, unit: "V",
    protect_during_run: null, protected_now: false, follow_dew: false },
  { id: 5, name: "Total Current", can_write: false, is_boolean: false, value: 2.4, min: 0, max: 10, unit: "A",
    protect_during_run: null, protected_now: false, follow_dew: false },
];

/** `power_guard._REFUSAL` with the port name filled in, spelled out HERE rather
 *  than imported from the module under test - a test that quotes the code it is
 *  checking cannot notice the code changing. Every clause matters: the name, the
 *  consequence, and BOTH ways out. It is 178 characters, which is also what
 *  makes it a truncation detector: `showToast` runs its argument through
 *  `humanizeLog`, which cuts at 137 and would take the second way out with it. */
const refusalFor = (name: string): string =>
  `${name} is protected while a run is live: switching it now would cut power to `
  + "something the sequence is using. Stop the run, or clear the protection for "
  + "this port in Power settings.";
const REFUSAL_MOUNT = refusalFor("Mount 12V");

// ------------------------------------------------------------- fetch recorder
interface Asked { method: string; url: string; body: unknown }
const asked: Asked[] = [];
/** When set, `/api/switch/set` hangs until it is called - the only way to have a
 *  walk genuinely IN FLIGHT while a second gesture arrives. */
let releaseSet: ((ports: Port[]) => void) | null = null;
let holdSet = false;
/** Which engine the box is answering as: `PORTS` (pre-S7h, no annotation at
 *  all) or `ANNOTATED`. Swapped by the section that needs it, never both. */
let served: Port[] = PORTS;
/** When set, the next `POST /api/switch/set` / `PUT /api/switch/ports/{id}`
 *  fails with this status and body instead of writing. */
let setFail: { status: number; body: unknown } | null = null;
let putFail: { status: number; body: unknown } | null = null;

g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const method = init?.method ?? "GET";
  const body = init?.body ? JSON.parse(init.body) : null;
  const path = String(url);
  asked.push({ method, url: path, body });
  const ok = (json: unknown) => ({ ok: true, status: 200, statusText: "OK", json: async () => json });
  const fail = (f: { status: number; body: unknown }) => ({
    ok: false, status: f.status, statusText: "Conflict", json: async () => f.body,
  });

  // BEFORE the GET branch: `/api/switch/ports/3` starts with `/api/switch/ports`.
  if (method === "PUT" && /\/api\/switch\/ports\/\d+$/.test(path)) {
    if (putFail) return fail(putFail);
    const id = Number(path.slice(path.lastIndexOf("/") + 1));
    // The server merges only the keys that were SENT, which is the contract the
    // one-key tests below are about; the stub has to merge the same way or a
    // forged key would be invisible here too.
    served = served.map((p) => (p.id === id ? { ...p, ...(body as object) } : p));
    return ok(served);
  }
  if (path.includes("/api/switch/ports")) return ok(served);
  if (path.includes("/api/switch/set")) {
    if (setFail) return fail(setFail);
    const { port_id, value } = body as { port_id: number; value: number };
    const next = served.map((p) => (p.id === port_id ? { ...p, value } : p));
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
const { PowerSheet, powerLiveLine, legacyLockReason, SESSION_CRITICAL } =
  await import("../sheets/power");
const {
  followDewNote, isAnnotated, mergeSettings, portLockReason, protectLine,
  protectPatch, protectStop, RELAY_SETTINGS_NOTE, settingsSummary,
} = await import("../lib/portSettings");
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
/** The PORT ROWS, and only those. `[data-testid^="port-"]` used to be enough;
 *  the settings group added `port-settings-<id>`, `port-protect-<id>` and
 *  `port-follow-dew-<id>` under the same prefix, which would have quietly turned
 *  "one row per port" into "one row per port plus however many controls exist
 *  today" - an assertion that still passes and no longer means anything. */
const portRows = () => qa('[data-testid^="port-"]')
  .filter((n) => /^port-\d+$/.test(n.getAttribute("data-testid") || ""));

// ====================================================== 1. the precondition
seed();
await mount();

test("the sheet mounted with one row per port, of all three kinds", () => {
  assert(q('[data-testid="rig-power"]') != null,
    "no sheet marker - the sheet did not render at all");
  eq(portRows().length, PORTS.length,
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

  eq(portRows().length, PORTS.length,
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
  eq(mount0.getAttribute("title"), legacyLockReason("Mount 12V"),
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

// ======================== 7-11. the annotated engine (D-RIG-5 + D-RIG-3 ports)
// From here on the box answers as an engine that HAS `power_guard`. Everything
// above still describes the fallback and stays exactly as it was.
const opened = new Set<string>();
/** Open a port's settings group once. The Disclosure mounts its children only
 *  while open (that is its contract), so nothing inside can be asserted on -
 *  or pressed - until this has run. */
function openSettings(id: number): void {
  const key = `${id}`;
  const head = q(`[data-testid="port-settings-${id}"] button[aria-expanded]`);
  assert(head != null, `port ${id} has no settings group`);
  if (head.getAttribute("aria-expanded") === "true") { opened.add(key); return; }
  click(head);
  opened.add(key);
}
const segOption = (id: number, value: string) =>
  q(`[data-testid="port-protect-${id}"] [data-value="${value}"]`);
const puts = () => asked.filter((a) => a.method === "PUT");
const sets = () => asked.filter((a) => a.url === "/api/switch/set");
const toastTitles = () =>
  ((useStore.getState() as any).toasts as { title: string }[]).map((t) => t.title);

served = ANNOTATED.map((p) => ({ ...p }));
seed({ principal: ADMIN, sequence: { state: "running", target: "NGC 6946" } });
await mount();

test("the annotated sheet mounted, with a settings group under every writable port", () => {
  assert(q('[data-testid="rig-power"]') != null, "the sheet did not render at all");
  eq(portRows().length, ANNOTATED.length, "the sheet lost a port");
  // Four writable ports, and the two read-only telemetry rows have no policy to
  // set - offering them one would be a control bound to nothing.
  eq(qa('[data-testid^="port-settings-"]').length, 4,
    "the settings group is not on every writable port (or leaked onto a sensor)");
  assert(q('[data-testid="port-settings-4"]') == null,
    "a read-only telemetry port was given protection and dew settings");
});

await testAsync("a protected port is locked with the ENGINE'S sentence and fires nothing", async () => {
  asked.length = 0;
  act(() => { useStore.setState({ toasts: [] } as never); });
  const mount0 = q('[data-testid="port-0"]');
  eq(mount0.getAttribute("aria-disabled"), "true",
    "a port the engine will refuse is live - the first the user hears of it is a 409");
  eq(mount0.getAttribute("title"), REFUSAL_MOUNT,
    `the row's reason is not the server's own sentence (${mount0.getAttribute("title")})`);
  assert(!mount0.hasAttribute("disabled"),
    "the native disabled attribute took the reason out of the tree with it");
  assert(/protected during the run - matched by name/.test(mount0.textContent),
    "the row does not say WHICH of the two decisions locked it");

  click(mount0);
  await settle();
  eq(sets().length, 0,
    "a port the engine has already said it will refuse still reached the box - the "
    + "round trip exists only to be told what the row was already holding");
  assert(toastTitles().includes(REFUSAL_MOUNT),
    `the press was swallowed with no explanation (${JSON.stringify(toastTitles())})`);
});

await testAsync("the operator's decision beats the name, in both directions", async () => {
  asked.length = 0;
  // "Camera 12V" MATCHES /mount|camera|usb/i and is stored as `false`. If the
  // client folds null and false into one boolean, this port locks and the
  // screen looks perfectly correct - which is why it is asserted rather than
  // looked at.
  const cam = q('[data-testid="port-1"]');
  eq(cam.getAttribute("aria-disabled"), null,
    "a port stored as NOT PROTECTED locked anyway - the tri-state collapsed and "
    + "the operator's decision was overridden by the port's label");
  click(cam);
  await settle();
  eq(sets().length, 1, "a port the engine allows could not be switched");
  openSettings(1);
  eq(segOption(1, "off").getAttribute("aria-checked"), "true",
    "NOT PROTECTED is not the shown stop - `false` was rendered as BY NAME, so "
    + "the control cannot show what it is about to change");
});

await testAsync("selecting BY NAME sends protect_during_run: null, not false and not nothing", async () => {
  asked.length = 0;
  openSettings(1);
  click(segOption(1, "name"));
  await settle();
  eq(puts().length, 1, `expected exactly one settings write (${JSON.stringify(asked)})`);
  eq(puts()[0].url, "/api/switch/ports/1", "the settings write went to the wrong port");
  const body = puts()[0].body as Record<string, unknown>;
  eq(JSON.stringify(Object.keys(body)), JSON.stringify(["protect_during_run"]),
    `the body carries a key the user did not set (${JSON.stringify(body)}) - absent `
    + "means UNCHANGED on the server, so a forged key silently rewrites the other policy");
  assert(Object.is(body.protect_during_run, null),
    `BY NAME did not send null (${JSON.stringify(body.protect_during_run)}) - `
    + "false is a different decision and does not follow a rename");
});

await testAsync("FOLLOW DEW sends only follow_dew, and says what it will do to THIS port", async () => {
  asked.length = 0;
  openSettings(3);
  const note = q('[data-testid="port-settings-3"]').textContent as string;
  assert(note.includes(followDewNote(ANNOTATED[3])),
    "the FOLLOW DEW note does not name this port's own range");
  assert(/between 0 and 255/.test(note),
    "the note does not name the port's 0..255 register - a UPB dew channel is not a percentage");
  assert(!note.includes("%"),
    "the note prints a percentage beside a control that writes a register value");
  assert(/by name - not protected · follows dew/.test(
    q('[data-testid="port-settings-3"]').textContent as string),
  "the collapsed summary does not state both policies");

  click(q('[data-testid="port-follow-dew-3"]'));
  await settle();
  eq(puts().length, 1, `expected exactly one settings write (${JSON.stringify(asked)})`);
  const body = puts()[0].body as Record<string, unknown>;
  eq(JSON.stringify(body), JSON.stringify({ follow_dew: false }),
    `the dew write carries a second key (${JSON.stringify(body)}) - it would un-pin `
    + "a protection nobody touched");
});

await testAsync("a 409 port_protected shows the wire's sentence, whole and verbatim", async () => {
  asked.length = 0;
  act(() => { useStore.setState({ toasts: [] } as never); });
  const sentence = refusalFor("Bench light");
  // The nested FastAPI shape the server actually sends (`s7l-patches.md` From
  // S7h patch 4), so `code` is where the client must read it from.
  setFail = {
    status: 409,
    body: { detail: { detail: sentence, code: "port_protected", port_id: 2, port_name: "Bench light" } },
  };
  click(q('[data-testid="port-2"]'));   // a run started since the last poll
  await settle();
  setFail = null;

  eq(sets().length, 1, "the tap did not reach the box at all");
  const titles = toastTitles();
  assert(titles.includes(sentence),
    `the refusal was not shown verbatim (${JSON.stringify(titles)}) - the second way `
    + "out is the clause that gets lost, and it is the one the user is standing in front of");
  assert(sentence.length > 137,
    "the fixture sentence is short enough to survive humanizeLog, so this test "
    + "no longer proves the toast is not truncated");
  eq(puts().length, 0, "the refusal triggered a settings write");
});

await testAsync("a 403 local_only names the way to change it, and leaves the port switchable", async () => {
  asked.length = 0;
  act(() => { useStore.setState({ toasts: [] } as never); });
  putFail = {
    status: 403,
    body: { detail: "this security-sensitive operation is LAN-only", code: "local_only" },
  };
  openSettings(2);
  click(segOption(2, "on"));
  await settle();
  putFail = null;

  eq(puts().length, 1, "the settings write did not go out");
  assert(toastTitles().includes(RELAY_SETTINGS_NOTE),
    `the relay refusal is the raw server phrase (${JSON.stringify(toastTitles())}), which `
    + "names neither the port nor where the setting can be changed");

  // The fence covers the POLICY route and deliberately misses the port itself:
  // operating a power box over the relay is the product.
  click(q('[data-testid="port-2"]'));
  await settle();
  eq(sets().length, 1, "the relay-only settings refusal took the port's own switch with it");
});

test("the footer describes the controls this engine actually has", () => {
  const foot = q('[data-testid="power-footer"]').textContent as string;
  assert(/Ports marked PROTECTED are refused while a run is live/.test(foot),
    "the footer does not say what PROTECTED does");
  assert(/BY NAME follows the port's label/.test(foot),
    "the footer does not say what the third state does");
  assert(/driven from the dew margin/.test(foot),
    "the footer still denies that anything drives a dew port (E24)");
  assert(!/Ports are matched by name; rename a port on the power box/.test(foot),
    "the footer still describes the client-side name heuristic as the rule");
});

await testAsync("an operator sees both policies, inert, with the reason, and writes nothing", async () => {
  seed({ principal: OPERATOR, sequence: { state: "running" } });
  await mount();
  asked.length = 0;

  openSettings(2);
  const seg = q('[data-testid="port-protect-2"]');
  const dew = q('[data-testid="port-follow-dew-2"]');
  eq(seg.getAttribute("aria-disabled"), "true", "the protection control is live for an operator");
  eq(seg.getAttribute("title"), "needs admin access",
    `the protection control does not say who may change it (${seg.getAttribute("title")})`);
  eq(dew.getAttribute("aria-disabled"), "true", "FOLLOW DEW is live for an operator");
  assert(!seg.hasAttribute("disabled") && !dew.hasAttribute("disabled"),
    "the native disabled attribute was used, taking the reason out of the tree");
  assert(/Read-only - needs admin access/.test(
    q('[data-testid="port-settings-2"]').textContent as string),
  "the settings group does not state, while idle, the same reason its controls give when pressed");

  click(segOption(2, "on"));
  click(dew);
  await settle();
  eq(puts().length, 0, "a caller without config.safety rewrote the engine's refusal policy");
  assert(toastTitles().some((t) => /admin access/.test(t)),
    "the press was swallowed with no explanation");
});

// ============================================ 12. the LAN fence, BEFORE the press
//
// `PUT /api/switch/ports/{id}` is on `app.py`'s relay fence
// (`_REMOTE_LOCAL_ONLY_MUTATION_PREFIXES`, matched by prefix), so over the
// relay it 403s for EVERY role - the ADMIN below included. Test 11 above
// already proves the 403 is translated after the fact; this one proves the
// control never renders armed in the first place, which is the difference
// between a policy you cannot change and a policy you have to press to learn
// you cannot change.
//
// Sabotage: drop `needsLan: true` from the `settingsReason` `useLock` in
// power.tsx and the title goes back to null for an admin - both assertions on
// LOCAL_ONLY_REASON go red, and so does the "no PUT" one.
const { LOCAL_ONLY_REASON } = await import("../../../lib/gate");
const { noteRemoteStatus, resetRelayForTests } = await import("../../../lib/relay");

await testAsync("on the relay the port POLICIES lock and the port SWITCH does not", async () => {
  served = ANNOTATED.map((p) => ({ ...p }));
  seed({ principal: ADMIN, sequence: { state: "idle" } });
  await mount();
  opened.clear();
  openSettings(2);
  // Precondition on the LAN: an admin CAN set the policy here. Without it this
  // whole section would pass against a sheet that locks everything.
  eq(segOption(2, "on").getAttribute("aria-disabled"), null,
    "precondition: an admin on the LAN cannot set the policy, so the relay case proves nothing");

  act(() => { noteRemoteStatus({ via: "relay" }); });
  await settle();
  const seg = q('[data-testid="port-protect-2"]');
  const dew = q('[data-testid="port-follow-dew-2"]');
  eq(seg.getAttribute("title"), LOCAL_ONLY_REASON,
    `PROTECT DURING RUN names the wrong blocker over the relay (${seg.getAttribute("title")})`);
  eq(dew.getAttribute("title"), LOCAL_ONLY_REASON,
    `FOLLOW DEW names the wrong blocker over the relay (${dew.getAttribute("title")})`);
  assert(!seg.hasAttribute("disabled") && !dew.hasAttribute("disabled"),
    "the native disabled attribute was used, taking the reason out of the tree");

  asked.length = 0;
  click(segOption(2, "on"));
  click(dew);
  await settle();
  eq(puts().length, 0,
    `an armed-looking policy control reached the fenced route (${JSON.stringify(asked)})`);

  // AND THE OTHER HALF, which is why the fence is a prefix and not `/api/switch`:
  // operating a power box over the relay is the product.
  click(q('[data-testid="port-2"]'));
  await settle();
  eq(sets().length, 1,
    "the relay lock took the port's own on/off switch with it - that route is not fenced");
  act(() => { noteRemoteStatus({ via: "direct" }); });
  resetRelayForTests();
});

// ------------------------------------------------- the model, without a DOM
test("the tri-state model keeps null, true and false apart", () => {
  const named = { ...ANNOTATED[0] };          // Camera/Mount name, unset
  const pinnedOff = { ...ANNOTATED[1] };      // name matches, stored false
  eq(protectStop(named), "name", "an unset port is not on BY NAME");
  eq(protectStop(pinnedOff), "off", "a stored false was folded into BY NAME");
  eq(protectStop({ ...named, protect_during_run: true } as any), "on",
    "a stored true was folded into BY NAME");

  assert(Object.is(protectPatch("name").protect_during_run, null),
    "BY NAME does not patch null");
  eq(protectPatch("off").protect_during_run, false, "NOT PROTECTED does not patch false");
  eq(protectPatch("on").protect_during_run, true, "PROTECTED does not patch true");
  for (const stop of ["name", "on", "off"] as const) {
    eq(Object.keys(protectPatch(stop)).length, 1,
      `${stop} patches more than the one key it decides`);
  }

  // The three lines have to READ differently or the control is a two-state
  // switch with a decorative third stop.
  eq(protectLine(named), "matched by name - protected", "BY NAME does not state its consequence");
  eq(protectLine(pinnedOff), "not protected whatever this port is called",
    "NOT PROTECTED does not say that it survives a rename");
  eq(settingsSummary(ANNOTATED[3]), "by name - not protected · follows dew",
    "the collapsed summary drops one of the two policies");

  eq(JSON.stringify(mergeSettings({ protect_during_run: null }, { follow_dew: true })),
    JSON.stringify({ protect_during_run: null, follow_dew: true }),
    "a queued patch loses the key the newer press did not touch");
});

test("the engine's answer is used when there is one, and the name only when there is not", () => {
  const oldMount = PORTS[0];                  // no annotation at all
  assert(!isAnnotated(oldMount), "an unannotated row was read as annotated");
  assert(isAnnotated(ANNOTATED[0]), "an annotated row was read as unannotated");
  eq(portLockReason(oldMount as any, true), legacyLockReason("Mount 12V"),
    "an engine with no power_guard lost the lock that shipped");
  eq(portLockReason(oldMount as any, false), null, "an idle rig locked the mount port");
  eq(portLockReason(ANNOTATED[0] as any, false), REFUSAL_MOUNT,
    "the client re-ANDed protected_now with its own idea of whether a run is live - "
    + "the engine counts a PAUSE as live and the client would not");
  eq(portLockReason(ANNOTATED[1] as any, true), null,
    "the name beat the stored decision");
});

act(() => { rootRef?.unmount(); });

const total = passed + failed;
console.log(`rigPowerDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
